"""
agent.py - IFS Cloud ERP Agent following learn-claude-code v3 pattern

Core Philosophy: "The model is 80%. Code is 20%."
The model controls the loop - we just provide tools and stay out of the way.

Core loop:
    while True:
        response = model(messages, tools)
        if response.stop_reason != "tool_use":
            return response.text
        results = execute(response.tool_calls)
        messages.append(results)

Usage:
    agent = Agent.from_config("config/base_config.yaml")
    result = agent.run("What inventory do we have?")
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from prompt_loader import PromptLoader
from llm_client import get_client, LLMClient

# Import episodic memory
try:
    from episodic_memory import get_episodic_memory, EpisodicMemory
    HAS_EPISODIC = True
except ImportError:
    HAS_EPISODIC = False
    EpisodicMemory = None
    get_episodic_memory = None

# Import MCP tools if available
try:
    from tools.mcp_client import MCPToolCaller
    from tools.mcp_tool_registry import MCPToolRetriever, get_tool_catalog, search_tools_by_keywords
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    MCPToolCaller = None
    MCPToolRetriever = None
    get_tool_catalog = None
    search_tools_by_keywords = None

# Knowledge base path (for procedural rules)
KNOWLEDGE_PATH = Path(__file__).parent.parent / "config" / "ifs_knowledge.yaml"


# =============================================================================
# Agent Configuration - Maps to ifs-prompts/ files
# =============================================================================

AGENT_TYPES = {
    "Explore": {
        "system": "agent-prompt-explore-ifs.md",
        "tools": ["MCPSearch"],  # Read-only
    },
    "Plan": {
        "system": "agent-prompt-plan-mode-enhanced-ifs.md",
        "reminder": "system-reminder-plan-mode-is-active-ifs.md",
        "tools": ["MCPSearch", "TodoWrite", "AskUserQuestion"],
    },
    "general-purpose": {
        "system": "system-prompt-main-system-prompt-ifs.md",
        "tools": "*",  # All tools
    },
    "summarizer": {
        "system": "agent-prompt-conversation-summarization.md",
        "tools": [],  # No tools
    },
}

# Tool definitions from ifs-prompts/ - CONDENSED versions save ~3500 tokens
ORCHESTRATION_TOOLS = {
    "MCPSearch": {
        "prompt": "tool-description-mcpsearch-ifs.md",  # IFS version with workflow knowledge
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query or 'select:<tool_name>' to load a specific tool"
                }
            },
            "required": ["query"],
        },
    },
    "Task": {
        "prompt": "tool-description-task-ifs.md",  # Condensed: ~300 tokens vs ~1200
        "schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Task for subagent"},
                "subagent_type": {"type": "string", "enum": ["Explore", "Plan", "general-purpose"]},
            },
            "required": ["prompt", "subagent_type"],
        },
    },
    "TodoWrite": {
        "prompt": "tool-description-todowrite-ifs.md",  # Condensed: ~150 tokens vs ~2400
        "schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        },
                    },
                }
            },
            "required": ["todos"],
        },
    },
    "AskUserQuestion": {
        "prompt": "tool-description-askuserquestion-ifs.md",  # Condensed: ~50 tokens vs ~200
        "schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to ask the user"},
            },
            "required": ["question"],
        },
    },
}

# Security policy appended to all agents
SECURITY_PROMPT = "system-prompt-censoring-assistance-with-malicious-activities.md"


# =============================================================================
# TodoManager - From v2_todo_agent.py pattern
# =============================================================================

class TodoManager:
    """Track tasks during agent execution."""

    def __init__(self):
        self.items = []

    def update(self, todos: list) -> str:
        """Update todo list."""
        self.items = todos
        completed = sum(1 for t in todos if t.get("status") == "completed")
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        pending = sum(1 for t in todos if t.get("status") == "pending")
        return f"Updated: {len(todos)} todos ({completed} done, {in_progress} in progress, {pending} pending)"

    def get_summary(self) -> str:
        """Get todo summary."""
        if not self.items:
            return "No todos."
        lines = []
        for t in self.items:
            status = t.get("status", "pending")
            marker = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(status, "[ ]")
            lines.append(f"{marker} {t.get('content', '')}")
        return "\n".join(lines)


# =============================================================================
# Context Management - Compaction and system reminders
# =============================================================================

MAX_CONTEXT_TOKENS = 100000  # Claude's context window
COMPACT_THRESHOLD = 0.75     # Trigger compaction at 75%
REMINDER_THRESHOLD = 0.50    # Start injecting reminders at 50%


def estimate_tokens(messages: list) -> int:
    """Rough token estimate: ~4 chars per token."""
    return sum(len(str(m)) for m in messages) // 4


# =============================================================================
# Knowledge Injection - Load procedural rules from ifs_knowledge.yaml
# =============================================================================

_knowledge_cache: dict = {}


def load_knowledge() -> dict:
    """Load IFS knowledge base (cached)."""
    global _knowledge_cache
    if _knowledge_cache:
        return _knowledge_cache

    if KNOWLEDGE_PATH.exists():
        import yaml
        with open(KNOWLEDGE_PATH) as f:
            _knowledge_cache = yaml.safe_load(f) or {}
    return _knowledge_cache


def get_tool_knowledge(tool_name: str) -> str:
    """Get procedural knowledge for a tool (rules, common errors)."""
    knowledge = load_knowledge()
    parts = []

    # Check procedural rules
    procedural = knowledge.get("procedural", {})
    if tool_name in procedural:
        rules = procedural[tool_name].get("rules", [])
        if rules:
            parts.append("**Rules:**")
            for rule in rules:
                parts.append(f"- {rule}")

    # Check common errors related to this tool
    errors = knowledge.get("common_errors", [])
    for err in errors:
        keywords = err.get("keywords", [])
        if any(kw in tool_name.lower() for kw in keywords):
            parts.append(f"**Avoid:** {err.get('pattern', '')} → {err.get('correction', '')}")

    return "\n".join(parts) if parts else ""


def get_semantic_knowledge_summary() -> str:
    """Get key semantic facts to inject into system prompt upfront.

    This ensures the model knows intent mappings and site info BEFORE tool selection.
    Saves tokens by not repeating these in every tool call.
    """
    knowledge = load_knowledge()
    semantic = knowledge.get("semantic", {})

    if not semantic:
        return ""

    parts = ["## IFS Domain Knowledge"]

    # Intent mapping - critical for tool selection
    intent = semantic.get("intent_mapping", {})
    if intent and intent.get("facts"):
        parts.append("\n**Intent → Workflow:**")
        for fact in intent["facts"][:4]:  # Top 4 most important
            parts.append(f"- {fact}")

    # Sites - critical for warehouse queries
    sites = semantic.get("sites", {})
    if sites and sites.get("facts"):
        parts.append("\n**Sites:**")
        for fact in sites["facts"][:3]:  # Top 3
            parts.append(f"- {fact}")

    # Shipment workflow - common gotcha
    shipments = semantic.get("shipments", {})
    if shipments and shipments.get("facts"):
        # Just the 3-step workflow fact
        for fact in shipments["facts"]:
            if "3 steps" in fact:
                parts.append(f"\n**Shipments:** {fact}")
                break

    return "\n".join(parts)


# =============================================================================
# Agent - Core implementation following v3_subagent.py pattern
# =============================================================================

class Agent:
    """IFS Cloud ERP Agent with subagent support."""

    def __init__(
        self,
        prompt_loader: PromptLoader,
        llm: LLMClient,
        aux_llm: Optional[LLMClient] = None,
        mcp: Optional["MCPToolCaller"] = None,
        workdir: Optional[str] = None,
        model_routing: Optional[dict] = None,
        memory_config: Optional[dict] = None,
    ):
        self.prompt_loader = prompt_loader
        self.llm = llm
        self.aux_llm = aux_llm or llm  # Fallback to primary if not configured
        self.mcp = mcp
        self.workdir = Path(workdir or os.getcwd())
        self.todo = TodoManager()
        self._discovered_tools = []
        self._suppress_mcp_search = False  # Suppress MCPSearch after tool load (o3 fix)
        self._pending_tool = None  # Track which tool needs to be called to lift suppression
        # Check if using Anthropic (affects message format)
        self._is_anthropic = type(llm).__name__ == "AnthropicClient"

        # Model routing: which agent types use aux (cheap) model
        self.model_routing = model_routing or {
            "smart_agents": ["general-purpose", "Plan"],
            "aux_agents": ["Explore", "summarizer"],
        }

        # Token tracking for subagents
        self.subagent_tokens = {"input": 0, "output": 0}

        # Track tool calls for episodic memory storage
        self._current_tool_chain = []
        self._current_query = ""

        # Episodic memory for cross-task learning
        self.episodic_memory = None
        if HAS_EPISODIC and get_episodic_memory and memory_config:
            if memory_config.get("memory_enabled", False):
                self.episodic_memory = get_episodic_memory(
                    cache_dir=memory_config.get("memory_cache_dir", "./cache/memory"),
                    max_memories=memory_config.get("max_episodic_memories", 100),
                    retrieval_top_k=memory_config.get("memory_retrieval_top_k", 5),
                )

        # MCP tool registry for semantic search
        self.retriever = None
        if HAS_MCP and MCPToolRetriever:
            try:
                self.retriever = MCPToolRetriever.get_instance()
            except Exception:
                pass

    def _get_llm_for_agent_type(self, agent_type: str) -> LLMClient:
        """Route to smart or aux model based on agent type."""
        if agent_type in self.model_routing.get("aux_agents", []):
            return self.aux_llm
        return self.llm

    def _build_system_prompt(self, agent_type: str) -> str:
        """Compose system prompt from multiple ifs-prompts/ files."""
        config = AGENT_TYPES.get(agent_type, AGENT_TYPES["general-purpose"])
        parts = []

        # 1. Load base system prompt
        try:
            parts.append(self.prompt_loader.load(config["system"]))
        except FileNotFoundError:
            parts.append(f"You are an IFS Cloud ERP assistant ({agent_type} mode).")

        # 2. Add security policy
        try:
            parts.append(self.prompt_loader.load(SECURITY_PROMPT))
        except FileNotFoundError:
            pass

        # 3. Add contextual reminder if present
        if "reminder" in config:
            try:
                parts.append(self.prompt_loader.load(config["reminder"]))
            except FileNotFoundError:
                pass

        # 4. Inject semantic domain knowledge UPFRONT (intent mappings, sites)
        # This ensures model knows workflows BEFORE tool selection
        semantic = get_semantic_knowledge_summary()
        if semantic:
            parts.append(semantic)

        # 5. Inject relevant episodic memories (past successful tool chains)
        if self.episodic_memory and self._current_query:
            relevant = self.episodic_memory.retrieve(self._current_query, top_k=3)
            if relevant:
                memory_text = self.episodic_memory.format_for_prompt(relevant)
                if memory_text:
                    parts.append(memory_text)

        # 6. Tool catalog REMOVED - model discovers tools via MCPSearch
        # This saves ~1,475 tokens per request
        # The model will use MCPSearch to find and load tools as needed

        # 7. Add workdir context
        parts.append(f"\nWorking directory: {self.workdir}")

        return "\n\n".join(parts)

    def _build_tools(self, tool_names: list) -> list:
        """Build tools array from ifs-prompts/ tool descriptions."""
        if tool_names == "*":
            tool_names = list(ORCHESTRATION_TOOLS.keys())

        tools = []
        for name in tool_names:
            if name not in ORCHESTRATION_TOOLS:
                continue

            config = ORCHESTRATION_TOOLS[name]

            # Load description from prompt file
            try:
                description = self.prompt_loader.load(config["prompt"])
            except FileNotFoundError:
                description = f"Tool: {name}"

            tools.append({
                "name": name,
                "description": description[:1000],  # Truncate long descriptions
                "input_schema": config["schema"],
            })

        return tools

    def run(self, user_message: str, agent_type: str = "general-purpose") -> str:
        """
        Run agent loop until completion.

        This is THE core pattern from learn-claude-code:
            while True:
                response = model(messages, tools)
                if response.stop_reason != "tool_use":
                    return response.text
                results = execute(response.tool_calls)
                messages.append(results)
        """
        # Track for episodic memory
        self._current_query = user_message
        self._current_tool_chain = []

        config = AGENT_TYPES.get(agent_type, AGENT_TYPES["general-purpose"])
        system = self._build_system_prompt(agent_type)
        messages = [{"role": "user", "content": user_message}]

        # Build tools
        base_tools = self._build_tools(config.get("tools", []))
        max_turns = 50  # Safety limit

        for turn in range(max_turns):
            # Combine base tools with dynamically discovered MCP tools
            all_tools = base_tools + self._discovered_tools

            # Filter out MCPSearch if suppressed (forces o3 to use loaded tool)
            if self._suppress_mcp_search:
                all_tools = [t for t in all_tools if t.get("name") != "MCPSearch"]

            # Call LLM
            response = self.llm.chat(system, messages, all_tools)

            # Track token usage for subagent reporting
            if "usage" in response:
                self.subagent_tokens["input"] += response["usage"].get("input_tokens", 0)
                self.subagent_tokens["output"] += response["usage"].get("output_tokens", 0)

            # Check if done
            if response["stop_reason"] != "tool_use":
                self._suppress_mcp_search = False  # Reset on completion
                self._pending_tool = None

                # Store successful tool chain in episodic memory
                result_text = response.get("text", "")
                if self.episodic_memory and self._current_tool_chain:
                    self.episodic_memory.store(
                        query=self._current_query,
                        tool_chain=self._current_tool_chain,
                        result_summary=result_text[:200] if result_text else "Completed",
                        success=True,
                    )

                return result_text

            # Execute tool calls
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                return response.get("text", "")

            results = []
            for tc in tool_calls:
                output = self._execute_tool(tc)
                # Inject system reminder if context is getting long
                output = self._maybe_inject_reminder(output, messages)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": output,
                })

            # Append to conversation
            # For Anthropic: content already contains tool_use blocks
            # For OpenAI: need separate tool_calls field
            assistant_msg = {"role": "assistant", "content": response["content"]}
            if tool_calls and not self._is_anthropic:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            messages.append({"role": "user", "content": results})

            # Check if we need to compact the conversation
            if self._should_compact(messages):
                messages = self._compact_messages(messages)

        return "Max turns reached."

    def run_streaming(self, user_message: str, agent_type: str = "general-purpose", conversation_history: list = None):
        """
        Run agent loop, yielding events for streaming UI.

        Args:
            user_message: The user's input
            agent_type: Type of agent to use
            conversation_history: Optional list of previous messages for continuity

        Yields events like:
            {"type": "thinking", "step": 1}
            {"type": "tool_call", "name": "...", "arguments": {...}}
            {"type": "tool_result", "name": "...", "result": "...", "success": True}
            {"type": "todo_update", "todos": [...]}
            {"type": "response", "content": "..."}
            {"type": "done"}
        """
        # Track for episodic memory
        self._current_query = user_message
        self._current_tool_chain = []

        config = AGENT_TYPES.get(agent_type, AGENT_TYPES["general-purpose"])
        system = self._build_system_prompt(agent_type)

        # Start with conversation history if provided, then add new user message
        messages = list(conversation_history) if conversation_history else []
        messages.append({"role": "user", "content": user_message})

        # Build tools
        base_tools = self._build_tools(config.get("tools", []))
        max_turns = 50  # Safety limit

        for turn in range(max_turns):
            yield {"type": "thinking", "step": turn + 1, "status": "Reasoning..."}

            # Combine base tools with dynamically discovered MCP tools
            all_tools = base_tools + self._discovered_tools

            # Filter out MCPSearch if suppressed (forces o3 to use loaded tool)
            if self._suppress_mcp_search:
                all_tools = [t for t in all_tools if t.get("name") != "MCPSearch"]

            # Call LLM
            response = self.llm.chat(system, messages, all_tools)

            # Emit token usage if available
            if "usage" in response:
                yield {
                    "type": "token_usage",
                    "input_tokens": response["usage"].get("input_tokens", 0),
                    "output_tokens": response["usage"].get("output_tokens", 0),
                }

            # Check if done
            if response["stop_reason"] != "tool_use":
                self._suppress_mcp_search = False  # Reset on completion
                self._pending_tool = None
                text = response.get("text", "")

                # Store successful tool chain in episodic memory
                if self.episodic_memory and self._current_tool_chain:
                    self.episodic_memory.store(
                        query=self._current_query,
                        tool_chain=self._current_tool_chain,
                        result_summary=text[:200] if text else "Completed",
                        success=True,
                    )

                if text:
                    yield {"type": "response", "content": text}
                yield {"type": "done"}
                return

            # Execute tool calls
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                text = response.get("text", "")
                if text:
                    yield {"type": "response", "content": text}
                yield {"type": "done"}
                return

            results = []
            for tc in tool_calls:
                name = tc["name"]
                args = tc.get("arguments", {})

                # Emit tool call event
                yield {"type": "tool_call", "name": name, "arguments": args, "step": turn + 1}

                # Execute tool
                output = self._execute_tool_streaming(tc)

                # Emit todo update if TodoWrite was called
                if name == "TodoWrite":
                    yield {"type": "todo_update", "todos": self.todo.items}

                # Emit tool result
                success = not output.startswith("Error:")
                yield {"type": "tool_result", "name": name, "result": output, "success": success}

                # Inject system reminder if context is getting long
                output = self._maybe_inject_reminder(output, messages)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": output,
                })

            # Append to conversation
            # For Anthropic: content already contains tool_use blocks
            # For OpenAI: need separate tool_calls field
            assistant_msg = {"role": "assistant", "content": response["content"]}
            if tool_calls and not self._is_anthropic:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            messages.append({"role": "user", "content": results})

            # Check if we need to compact the conversation
            if self._should_compact(messages):
                messages = self._compact_messages(messages)

        yield {"type": "warning", "message": "Max turns reached"}
        yield {"type": "done"}

    def _execute_tool_streaming(self, tc: dict) -> str:
        """Execute a tool call for streaming (no print statements)."""
        name = tc["name"]
        args = tc.get("arguments", {})

        # Track for episodic memory (skip internal tools)
        if name not in ("MCPSearch", "TodoWrite", "AskUserQuestion"):
            self._current_tool_chain.append({"name": name, "args": args})

        # Reset MCPSearch suppression only when the pending loaded tool is called (o3 fix)
        if self._pending_tool and name == self._pending_tool:
            self._suppress_mcp_search = False
            self._pending_tool = None

        try:
            if name == "MCPSearch":
                result = self._handle_mcp_search(args.get("query", ""))
            elif name == "TodoWrite":
                result = self.todo.update(args.get("todos", []))
            elif name == "Task":
                result = self._spawn_subagent(args)
            elif name == "AskUserQuestion":
                # In streaming mode, we can't block for user input
                # Return the question so UI can handle it
                result = f"QUESTION: {args.get('question', '')}"
            elif self.mcp:
                # Route to MCP (async call)
                # Use existing loop or create new one (don't close - reuse for subsequent calls)
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                result = loop.run_until_complete(
                    self.mcp.call_tool({"function": {"name": name, "arguments": args}})
                )
                # Convert result to string if needed
                if isinstance(result, dict):
                    result = json.dumps(result, indent=2)
            else:
                result = f"Unknown tool: {name}"
        except Exception as e:
            result = f"Error: {e}"

        return result

    def _execute_tool(self, tc: dict) -> str:
        """Execute a tool call and return result."""
        name = tc["name"]
        args = tc.get("arguments", {})

        # Track for episodic memory (skip internal tools)
        if name not in ("MCPSearch", "TodoWrite", "AskUserQuestion"):
            self._current_tool_chain.append({"name": name, "args": args})

        # Reset MCPSearch suppression only when the pending loaded tool is called (o3 fix)
        if self._pending_tool and name == self._pending_tool:
            self._suppress_mcp_search = False
            self._pending_tool = None

        print(f"\n> {name}: {args}")

        try:
            if name == "MCPSearch":
                result = self._handle_mcp_search(args.get("query", ""))
            elif name == "TodoWrite":
                result = self.todo.update(args.get("todos", []))
            elif name == "Task":
                result = self._spawn_subagent(args)
            elif name == "AskUserQuestion":
                result = self._ask_user(args.get("question", ""))
            elif self.mcp:
                # Route to MCP (async call)
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                result = loop.run_until_complete(
                    self.mcp.call_tool({"function": {"name": name, "arguments": args}})
                )
                # Convert result to string if needed
                if isinstance(result, dict):
                    result = json.dumps(result, indent=2)
            else:
                result = f"Unknown tool: {name}"
        except Exception as e:
            result = f"Error: {e}"

        # Preview result
        preview = result[:200] + "..." if len(result) > 200 else result
        print(f"  {preview}")

        return result

    def _handle_mcp_search(self, query: str) -> str:
        """MCPSearch: discover and load tools with knowledge injection."""
        # Direct load: "load:tool_name" - skip search when tool name is known
        if query.startswith("load:"):
            tool_name = query[5:].strip()
            return self._load_tool_with_knowledge(tool_name)

        # Select specific tool: "select:tool_name" (alias for load:)
        if query.startswith("select:"):
            tool_name = query[7:].strip()
            return self._load_tool_with_knowledge(tool_name)

        # Keyword search (uses search_tools_by_keywords from registry)
        if search_tools_by_keywords:
            tools = search_tools_by_keywords(query, top_k=5)
            if tools:
                lines = ["**Found tools:**"]
                for t in tools:
                    flag = "!" if t.mutates else ""
                    lines.append(f"- {t.name}{flag}: {t.summary}")
                lines.append("\nUse `select:<tool_name>` or `load:<tool_name>` to load a tool's schema.")
                return "\n".join(lines)

        # Fallback to retriever if available
        if self.retriever:
            tool_names = self.retriever.retrieve(query, top_k=5)
            if tool_names:
                return f"Found tools: {', '.join(tool_names)}. Use `load:<name>` to load schema."

        return "No tools found matching query."

    def _load_tool_with_knowledge(self, tool_name: str) -> str:
        """Load tool schema and inject procedural knowledge."""
        loaded = False
        parts = []

        # Check if already loaded (prevents duplicate tool error)
        already_loaded = any(t.get("name") == tool_name for t in self._discovered_tools)
        if already_loaded:
            # Tool already available - just return confirmation
            knowledge = get_tool_knowledge(tool_name)
            if knowledge:
                return f"{tool_name} is already loaded and available. Call it directly.\n\n**Rules:**\n{knowledge}"
            return f"{tool_name} is already loaded and available. Call it directly."

        # Get schema from MCP if available
        if self.mcp:
            schema = self.mcp.get_tool_schema(tool_name)
            if schema and "error" not in str(schema):
                # Clean schema for Anthropic (remove extra fields like 'server')
                clean_schema = {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "input_schema": schema.get("input_schema", {"type": "object", "properties": {}})
                }
                self._discovered_tools.append(clean_schema)
                loaded = True
                # Suppress MCPSearch until this specific tool is called (o3 fix)
                self._suppress_mcp_search = True
                self._pending_tool = tool_name
                # Add parameter info
                params = schema.get("input_schema", {}).get("properties", {})
                if params:
                    parts.append("**Parameters:**")
                    for name, info in params.items():
                        desc = info.get("description", "")
                        parts.append(f"- {name}: {desc}")

        # Inject procedural knowledge from ifs_knowledge.yaml
        knowledge = get_tool_knowledge(tool_name)
        if knowledge:
            parts.append(f"\n**Rules:**\n{knowledge}")

        # Only say "LOADED" if we actually loaded the tool schema
        if not loaded:
            if knowledge:
                # We have knowledge but no MCP connection - tool can't be called
                return f"Tool {tool_name} exists but MCP is not connected. Cannot load tool schema."
            return f"Tool not found: {tool_name}"

        # Clear message: tool is ready to call NOW
        header = f"LOADED: {tool_name} is now available. Call it directly - do not search again."
        return header + ("\n\n" + "\n".join(parts) if parts else "")

    def _spawn_subagent(self, args: dict) -> str:
        """Task tool: spawn isolated subagent (v3 pattern) with model routing."""
        prompt = args.get("prompt", "")
        subagent_type = args.get("subagent_type", "general-purpose")

        # Select LLM based on agent type (smart vs aux)
        selected_llm = self._get_llm_for_agent_type(subagent_type)
        model_name = getattr(selected_llm, 'model', 'unknown')
        is_aux = subagent_type in self.model_routing.get("aux_agents", [])
        model_tier = "aux" if is_aux else "smart"

        print(f"\n[Spawning {subagent_type} subagent -> {model_tier} model ({model_name})]")

        def run_with_llm(llm: LLMClient) -> str:
            """Run subagent with given LLM."""
            subagent = Agent(
                prompt_loader=self.prompt_loader,
                llm=llm,
                aux_llm=self.aux_llm,
                mcp=self.mcp,
                workdir=str(self.workdir),
                model_routing=self.model_routing,
            )
            result = subagent.run(prompt, subagent_type)
            # Accumulate subagent token usage
            self.subagent_tokens["input"] += subagent.subagent_tokens.get("input", 0)
            self.subagent_tokens["output"] += subagent.subagent_tokens.get("output", 0)
            return result

        # Try with selected LLM, fallback to primary if aux fails
        try:
            return run_with_llm(selected_llm)
        except Exception as e:
            error_msg = str(e).lower()
            # Check for connection errors that indicate aux model is unreachable
            if is_aux and ("connection" in error_msg or "connect" in error_msg or
                          "refused" in error_msg or "timeout" in error_msg):
                print(f"\n[WARN] Aux model unavailable ({e}), falling back to primary LLM")
                return run_with_llm(self.llm)
            # Re-raise other errors
            raise

    def _ask_user(self, question: str) -> str:
        """Ask user for input."""
        print(f"\n? {question}")
        try:
            response = input("> ").strip()
            return response or "(no response)"
        except (EOFError, KeyboardInterrupt):
            return "(cancelled)"

    # =========================================================================
    # Context Management
    # =========================================================================

    def _should_compact(self, messages: list) -> bool:
        """Check if conversation needs compaction."""
        tokens = estimate_tokens(messages)
        return tokens > MAX_CONTEXT_TOKENS * COMPACT_THRESHOLD

    def _compact_messages(self, messages: list) -> list:
        """Summarize conversation using summarizer subagent."""
        print("\n[Context compaction triggered]")

        # Build conversation text for summarization
        conv_text = "\n".join(str(m) for m in messages[:-1])  # Exclude last message

        summary = self._spawn_subagent({
            "prompt": conv_text,
            "subagent_type": "summarizer"
        })

        # Return compacted messages: summary + last user message
        last_msg = messages[-1] if messages else {"role": "user", "content": ""}
        return [
            {"role": "user", "content": f"<summary>\n{summary}\n</summary>"},
            last_msg
        ]

    def _maybe_inject_reminder(self, tool_result: str, messages: list) -> str:
        """Inject system reminders if context is getting long."""
        tokens = estimate_tokens(messages)

        if tokens < MAX_CONTEXT_TOKENS * REMINDER_THRESHOLD:
            return tool_result

        reminders = []

        # Todo reminder if items exist
        if self.todo.items:
            in_progress = [t for t in self.todo.items if t.get("status") == "in_progress"]
            if in_progress:
                reminders.append(f"Current task: {in_progress[0].get('content', '')}")

        # Context warning
        pct = int(tokens / MAX_CONTEXT_TOKENS * 100)
        if pct > 60:
            reminders.append(f"Context usage: {pct}%. Consider completing current task.")

        if reminders:
            reminder_text = "\n".join(reminders)
            return f"{tool_result}\n\n<system-reminder>\n{reminder_text}\n</system-reminder>"

        return tool_result

    @classmethod
    def from_config(cls, config_path: str) -> "Agent":
        """Create agent from config file."""
        import yaml

        config_path = Path(config_path)
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Resolve paths relative to config
        prompts_dir = config.get("prompts_dir", "../ifs-prompts")
        if not Path(prompts_dir).is_absolute():
            prompts_dir = config_path.parent / prompts_dir

        # Load variables
        variables = config.get("variables", {})
        vars_file = config.get("prompt_variables_file")
        if vars_file:
            vars_path = config_path.parent / vars_file
            if vars_path.exists():
                with open(vars_path) as f:
                    variables.update(yaml.safe_load(f) or {})

        prompt_loader = PromptLoader(str(prompts_dir), variables)

        # Get primary (smart) LLM client
        provider = config.get("llm_provider", os.getenv("LLM_PROVIDER", "anthropic"))
        model = config.get(f"{provider}_model")
        base_url = config.get(f"{provider}_base_url")
        reasoning_effort = config.get(f"{provider}_reasoning_effort")
        llm = get_client(provider, model=model, base_url=base_url, reasoning_effort=reasoning_effort)
        print(f"Primary LLM: {model} ({provider})")

        # Get auxiliary (cheap/local) LLM client if configured
        aux_llm = None
        aux_model = config.get("aux_model_name")
        if aux_model:
            aux_provider = config.get("aux_provider", "openai")  # Default to OpenAI-compatible
            aux_base_url = config.get("aux_base_url")
            aux_reasoning = config.get("aux_reasoning_effort")
            aux_llm = get_client(
                aux_provider,
                model=aux_model,
                base_url=aux_base_url,
                reasoning_effort=aux_reasoning,
            )
            print(f"Aux LLM: {aux_model} ({aux_provider})")

        # Model routing configuration
        model_routing = config.get("model_routing", {
            "smart_agents": ["general-purpose", "Plan"],
            "aux_agents": ["Explore", "summarizer"],
        })

        # Memory system configuration
        memory_config = {
            "memory_enabled": config.get("memory_enabled", False),
            "memory_cache_dir": config.get("memory_cache_dir", "./cache/memory"),
            "max_episodic_memories": config.get("max_episodic_memories", 100),
            "memory_retrieval_top_k": config.get("memory_retrieval_top_k", 5),
        }

        # MCP client if available
        mcp = None
        if HAS_MCP and MCPToolCaller:
            planning_url = config.get("mcp_planning_url", "http://localhost:8000/sse")
            customer_url = config.get("mcp_customer_url", "http://localhost:8001/sse")
            try:
                mcp = MCPToolCaller(planning_url=planning_url, customer_url=customer_url)
                # Initialize synchronously (load tools from servers)
                # Create new event loop for this thread (works in main thread or Flask)
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                loop.run_until_complete(mcp.initialize())
                print(f"MCP: Connected to {len(mcp._tools)} tools")
            except Exception as e:
                print(f"MCP: Connection failed - {e}")
                mcp = None

        return cls(
            prompt_loader=prompt_loader,
            llm=llm,
            aux_llm=aux_llm,
            mcp=mcp,
            workdir=config.get("workdir"),
            model_routing=model_routing,
            memory_config=memory_config,
        )


# =============================================================================
# Main REPL
# =============================================================================

def main():
    """Simple Read-Eval-Print Loop."""
    import argparse

    parser = argparse.ArgumentParser(description="IFS Cloud ERP Agent")
    parser.add_argument("--config", default="config/base_config.yaml", help="Config file")
    parser.add_argument("--prompt", help="Single prompt (non-interactive)")
    parser.add_argument("--agent-type", default="general-purpose", help="Agent type")
    args = parser.parse_args()

    # Try to load from config, fall back to defaults
    try:
        agent = Agent.from_config(args.config)
    except FileNotFoundError:
        print(f"Config not found: {args.config}, using defaults")
        prompt_loader = PromptLoader("../ifs-prompts")
        llm = get_client()
        agent = Agent(prompt_loader, llm)

    print(f"IFS Cloud ERP Agent - {agent.workdir}")
    print(f"LLM: {type(agent.llm).__name__}")
    print("Type 'exit' to quit.\n")

    # Single prompt mode
    if args.prompt:
        result = agent.run(args.prompt, args.agent_type)
        print(result)
        return

    # Interactive REPL
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input or user_input.lower() in ("exit", "quit", "q"):
            break

        try:
            result = agent.run(user_input, args.agent_type)
            print(f"\nAssistant: {result}\n")
        except Exception as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    main()
