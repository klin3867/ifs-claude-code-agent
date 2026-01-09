# IFS Cloud ERP Agent Project

## Goal
Adapt Claude Code's prompt architecture for an IFS Cloud ERP agent.
This repo contains reference implementations - treat as examples, not final code.

## Reference Implementations (from DeepAgent)

These files were copied from a working implementation for reference:

### Agent Core
- `src/agents/native_tool_agent.py` - Native function calling agent loop (~250 lines)
- `src/agents/schemas.py` - Meta-tool schema (search_tools), event types

### MCP Infrastructure
- `src/tools/mcp_client.py` - SSE-based MCP client with JSON-RPC handshake
- `src/tools/mcp_tool_registry.py` - 59 IFS Cloud tools with semantic retrieval

### Memory System
- `src/tools/memory_manager.py` - Episodic, tool, and working memory with persistence

### Frontend
- `src/app_flask.py` - Flask UI with streaming, v1/v2 agent toggle

### Config
- `config/base_config.yaml` - API keys, model config, MCP server URLs
- `config/ifs_knowledge.yaml` - Procedural rules, semantic facts, error corrections

## My Production Setup

### MCP Servers
- Planning server: `http://localhost:8000/sse`
- Customer server: `http://localhost:8001/sse`
- Tools: Inventory, orders, shipments, customers, planning

### Key Patterns to Preserve

1. **Meta-tool pattern**: Start with `search_tools`, dynamically expand available tools
2. **Proper message roles**: `role: "tool"` with `tool_call_id`, not fake user messages
3. **Memory integration**: Procedural rules returned with tool search, episodic memory in system prompt
4. **Streaming events**: thinking, tool_call, tool_result, response, done

## Claude Code Reference Files

Key prompts from the claude-code-system-prompts repo to study:
- `system-prompts/system-prompt-main-system-prompt.md` - Main agent prompt
- `system-prompts/agent-prompt-explore.md` - Exploration subagent
- `system-prompts/agent-prompt-plan-mode-enhanced.md` - Planning subagent
- `system-prompts/tool-description-task.md` - Task tool description

## What Works Well (Keep These Patterns)

### Native Function Calling Loop
```python
for iteration in range(max_iterations):
    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        tools=active_tools,
        stream=True
    )

    if not response.tool_calls:
        return response.content  # Done

    for tc in response.tool_calls:
        result = await execute_tool(tc)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result)
        })
```

### Dynamic Tool Discovery
```python
SEARCH_TOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_tools",
        "description": "Search for relevant tools",
        "parameters": {"properties": {"query": {"type": "string"}}}
    }
}

# Start with just this, expand after search
active_tools = [SEARCH_TOOLS_SCHEMA]
```

### Memory at Tool Search Time
```python
def handle_search_tools(query, tool_names):
    # Get domain knowledge alongside tools
    knowledge = memory_manager.retrieve_relevant_knowledge(
        query=query,
        tool_names=tool_names,
    )
    return {
        "tool_names": tool_names,
        "procedural_rules": knowledge["procedural_rules"],
        "semantic_facts": knowledge["semantic_facts"],
    }
```

## Running the Reference Implementation

```bash
cd ifs-cloud-erp-agent
pip install -r requirements.txt
# Edit config/base_config.yaml with your API keys
# Ensure MCP servers are running
python3 src/app_flask.py
# Open http://127.0.0.1:7865
# Click "v2 Agent" to use native function calling
```
