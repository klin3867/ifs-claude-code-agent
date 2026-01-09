# IFS Cloud ERP Agent

## Overview

This is a Claude Code-style agent for IFS Cloud ERP with native OpenAI function calling. It connects to MCP (Model Context Protocol) servers to execute IFS Cloud operations.

## Architecture

```
claude-code-system-prompts/
├── system-prompts/               # Original Claude Code prompts (reference)
├── ifs-prompts/                  # IFS Cloud ERP adapted prompts
│   ├── system-prompt-main-system-prompt-ifs.md    # Main system prompt
│   ├── agent-prompt-explore-ifs.md                # Explore subagent
│   ├── agent-prompt-plan-mode-enhanced-ifs.md     # Plan mode subagent
│   ├── system-reminder-plan-mode-is-active-ifs.md # Plan mode reminder
│   ├── tool-description-*.md                      # Domain-agnostic tools
│   └── agent-prompt-*.md                          # Domain-agnostic agents
└── ifs-cloud-erp-agent/
    ├── src/
    │   ├── app_flask.py              # Flask UI with streaming, dual agent modes
    │   ├── agents/
    │   │   ├── native_tool_agent.py  # v2 agent with native function calling (~250 lines)
    │   │   └── schemas.py            # SEARCH_TOOLS_SCHEMA meta-tool, AgentEvent types
    │   ├── tools/
    │   │   ├── mcp_client.py         # MCP protocol client (SSE transport)
    │   │   ├── mcp_tool_registry.py  # 59 IFS tools with semantic retrieval
    │   │   └── memory_manager.py     # Brain-inspired memory (episodic, tool, working)
    │   └── prompts/
    │       ├── prompts_native.py     # System prompts for v2 agent
    │       └── prompts_deepagent.py  # XML tag constants for v1 agent
    └── config/
        ├── base_config.yaml          # API keys, model config, MCP URLs
        └── ifs_knowledge.yaml        # Procedural rules, semantic facts, error corrections
```

## System Prompts

The agent uses Claude Code's proven prompt architecture, adapted for ERP operations. Prompts are in `../ifs-prompts/`:

| File | Purpose |
|------|---------|
| `system-prompt-main-system-prompt-ifs.md` | Core identity, behavior, and tool usage policies |
| `agent-prompt-explore-ifs.md` | Read-only data exploration subagent |
| `agent-prompt-plan-mode-enhanced-ifs.md` | Execution planning subagent |
| `system-reminder-plan-mode-is-active-ifs.md` | Plan mode workflow (5 phases) |
| `tool-description-task.md` | Sub-agent orchestration (domain-agnostic) |
| `tool-description-todowrite.md` | Task tracking (domain-agnostic) |
| `tool-description-mcpsearch.md` | MCP tool discovery (domain-agnostic) |

### Template Variables

The prompts use `${VARIABLE_NAME}` template syntax. Variables are bound at runtime to wire tools:

```javascript
// Example variable bindings
TODO_TOOL_OBJECT → "Task"
BASH_TOOL_NAME.name → "TodoWrite"
AVAILABLE_TOOLS_SET → "AskUserQuestion"
EXPLORE_AGENT → "Explore"
```

### Adapted vs Domain-Agnostic

- **Adapted files** (`*-ifs.md`): Translated from code→ERP domain
- **Domain-agnostic files**: Copied unchanged (orchestration, task management, MCP discovery)

## Two Agent Modes

| Mode | Description | Tool Calling |
|------|-------------|--------------|
| **v1 (Original)** | XML tag-based parsing with regex | `<tool_call>...</tool_call>` |
| **v2 (Native)** | OpenAI native function calling | `tools=` parameter, `role: "tool"` |

Toggle between modes using the **"v2 Agent"** button in the UI header.

## Running the Application

### Prerequisites

1. **MCP Servers** must be running:
   - Planning server: `http://localhost:8000/sse`
   - Customer server: `http://localhost:8001/sse`

2. **Dependencies**:
   ```bash
   pip install flask openai httpx pyyaml sentence-transformers
   ```

3. **Configuration**: Edit `config/base_config.yaml`:
   ```yaml
   # Model
   model_name: gpt-4o
   base_url: https://api.openai.com/v1
   api_key: sk-...

   # MCP servers
   mcp_planning_url: http://localhost:8000/sse
   mcp_customer_url: http://localhost:8001/sse
   ```

### Start the Server

```bash
cd ifs-cloud-erp-agent
python3 src/app_flask.py
```

Open http://127.0.0.1:7865 in your browser.

## Key Design Patterns

### 1. Meta-Tool Pattern (search_tools)

The agent starts with only one tool: `search_tools`. This meta-tool:
- Uses semantic retrieval to find relevant tools
- Dynamically expands the `tools=` parameter
- Returns domain knowledge (procedural rules, semantic facts)
- Keeps initial token count low

```python
SEARCH_TOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_tools",
        "description": "Search for relevant tools. Returns tool schemas and domain knowledge.",
        "parameters": {
            "properties": {
                "query": {"type": "string", "description": "What you want to do"},
                "top_k": {"type": "integer", "default": 10}
            },
            "required": ["query"]
        }
    }
}
```

### 2. Proper Message Roles

**Wrong (v1 pattern):**
```python
messages.append({"role": "user", "content": f"Tool result: {result}"})  # BAD
```

**Correct (v2 pattern):**
```python
messages.append({
    "role": "tool",
    "tool_call_id": tool_call.id,
    "content": json.dumps(result)
})
```

### 3. Memory System Integration

The agent integrates with a brain-inspired memory system:

| Memory Type | Purpose | Persistence |
|-------------|---------|-------------|
| **Episodic** | Past task summaries, outcomes | `./cache/memory/episodic_memories.json` |
| **Tool** | Tool usage patterns, success rates | `./cache/memory/tool_memories.json` |
| **Working** | Current session goals | Session only |
| **Knowledge Base** | Procedural rules, semantic facts | `config/ifs_knowledge.yaml` |

Memory is used at:
- **Task start**: Relevant past experiences injected into system prompt
- **Tool search**: Procedural rules returned with tool schemas
- **Task completion**: Episodic and tool memories stored

### 4. Simple Agent Loop

The v2 agent loop is ~150 lines (vs ~700 in v1):

```python
async def run(self, user_message, history):
    messages = self._build_messages(user_message, history)

    for iteration in range(max_iterations):
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=self._active_tools,
            stream=True
        )

        accumulated = await self._stream_response(response)

        if not accumulated.tool_calls:
            return accumulated.content  # Done

        messages.append(self._assistant_message(accumulated))

        for tc in accumulated.tool_calls:
            result = await self._execute_tool_call(tc)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result)
            })
```

## MCP Server Connection

The MCP client (`src/tools/mcp_client.py`) handles:
- SSE (Server-Sent Events) transport
- JSON-RPC 2.0 protocol
- Full handshake: `initialize` → `notifications/initialized` → `tools/list` / `tools/call`

```python
from tools.mcp_client import MCPToolCaller

caller = MCPToolCaller(
    planning_url="http://localhost:8000/sse",
    customer_url="http://localhost:8001/sse"
)
await caller.initialize()  # Loads tool schemas

result = await caller.call_tool({
    "function": {
        "name": "get_inventory_stock",
        "arguments": {"part_no": "10106105", "site": "AC"}
    }
})
```

## Tool Registry

59 IFS Cloud tools organized by category in `src/tools/mcp_tool_registry.py`:

| Category | Examples |
|----------|----------|
| **Inventory** | get_inventory_stock, check_stock_availability |
| **Orders** | search_customer_orders, get_order_lines |
| **Shipments** | create_shipment_order, add_shipment_order_line |
| **Customers** | search_customers, get_customer_details |
| **Planning** | get_parts_needing_orders, get_demand_exceptions |

Each tool has:
- `name`, `summary`, `category`
- `use_when` - when to use this tool
- `mutates` - whether it modifies data

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPAGENT_HOST` | 127.0.0.1 | Flask bind address |
| `DEEPAGENT_PORT` | 7865 | Flask port |
| `DEEPAGENT_SHOW_RAW_THOUGHTS` | false | Show raw LLM output in UI |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main UI |
| `/chat/stream` | POST | v1 agent (XML tags) |
| `/chat/stream/v2` | POST | v2 agent (native function calling) |
| `/clear` | POST | Clear v1 conversation |
| `/clear/v2` | POST | Clear v2 conversation |

## Troubleshooting

### MCP Connection Failed
- Verify MCP servers are running on configured ports
- Check `config/base_config.yaml` URLs match server addresses
- Look for timeout errors in Flask logs

### Tool Not Found
- Run `search_tools` first to discover available tools
- Check tool name matches exactly (case-sensitive)
- Verify MCP server has the tool registered

### Memory Not Loading
- Check `config/ifs_knowledge.yaml` exists and is valid YAML
- Verify `memory_enabled: true` in config
- Check `./cache/memory/` directory permissions
