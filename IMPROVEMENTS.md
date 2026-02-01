# IFS Cloud ERP Agent - Improvements & Gaps

## Prompt File Usage Analysis

**10 files used, 11 files unused** out of 21 total prompt files.

### Currently Used Files

| File | Used By |
|------|---------|
| `agent-prompt-conversation-summarization.md` | summarizer |
| `agent-prompt-explore-ifs.md` | Explore |
| `agent-prompt-plan-mode-enhanced-ifs.md` | Plan |
| `system-prompt-censoring-assistance-with-malicious-activities.md` | All agents (security) |
| `system-prompt-main-system-prompt-ifs.md` | general-purpose |
| `system-reminder-plan-mode-is-active-ifs.md` | Plan |
| `tool-description-askuserquestion.md` | general-purpose, Plan |
| `tool-description-mcpsearch-ifs.md` | general-purpose, Explore, Plan |
| `tool-description-task.md` | general-purpose |
| `tool-description-todowrite.md` | general-purpose, Plan |

### Unused Files

| File | Purpose | Recommendation |
|------|---------|----------------|
| `tool-description-enterplanmode.md` | Let LLM enter plan mode proactively | **Wire in** - useful feature |
| `tool-description-exitplanmode.md` | Let LLM exit plan mode | **Wire in** - needed with enterplanmode |
| `tool-description-exitplanmode-v2.md` | Updated exitplanmode | Pick v1 or v2 |
| `agent-prompt-task-tool.md` | System prompt for Task subagents | **Maybe** - subagents may need own context |
| `agent-prompt-task-tool-extra-notes.md` | Extra Task tool guidance | Could append to Task description |
| `tool-description-mcpsearch.md` | Generic MCPSearch | Replaced by `-ifs` version |
| `tool-description-mcpsearch-with-available-tools.md` | MCPSearch with embedded tool list | Alternative approach |
| `agent-prompt-conversation-summarization-with-additional-instructions.md` | Extended summarizer | Pick basic or extended |
| `system-reminder-plan-mode-is-active-for-subagents.md` | Lighter reminder for subagents | May be needed for Task tool |
| `agent-prompt-plan-verification-agent.md` | Verify plan execution | Advanced feature |
| `system-reminder-plan-mode-re-entry.md` | Re-entering plan mode | Edge case handling |

---

## Missing Features

### EnterPlanMode / ExitPlanMode Tools

The agent cannot proactively enter planning mode for complex tasks. Implementation requires:

1. Add to `ORCHESTRATION_TOOLS` in `agent.py`:
```python
"EnterPlanMode": {
    "prompt": "tool-description-enterplanmode.md",
    "schema": {"type": "object", "properties": {}, "required": []},
},
"ExitPlanMode": {
    "prompt": "tool-description-exitplanmode.md",
    "schema": {"type": "object", "properties": {}, "required": []},
},
```

2. Add handlers in `_handle_tool_call()`:
```python
elif tool_name == "EnterPlanMode":
    self._current_mode = "Plan"
    return "Entered plan mode."

elif tool_name == "ExitPlanMode":
    self._current_mode = "general-purpose"
    return "Exited plan mode."
```

3. Add state tracking in `__init__`:
```python
self._current_mode = "general-purpose"
```

4. Modify `run_streaming()` to rebuild system prompt and tools when mode changes.

**Complexity:** Medium - requires dynamic prompt/tool rebuilding mid-conversation.

---

## Open Source Model Compatibility

### gpt-oss-20b Configuration (Working)

Added support for local models via LM Studio:

**Config changes (`config/base_config.yaml`):**
```yaml
llm_provider: openai
openai_model: openai/gpt-oss-20b
openai_base_url: http://127.0.0.1:1234/v1
openai_reasoning_effort: high
```

**Code changes:**
- `llm_client.py`: Added `reasoning_effort` parameter to `OpenAIClient`
- `agent.py`: Pass `reasoning_effort` from config to LLM client

### Quality Gaps with Local Models

With `gpt-oss-20b` vs Claude:
- **Tool chain completion**: Sometimes stops after loading tools without calling them
- **Reasoning depth**: Requires `reasoning_effort: high` for complex workflows
- **Prompt sensitivity**: May need simplified/shorter prompts

### Potential Improvements for Local Models

1. **Prompt optimization**: Create model-specific prompt variants
2. **Temperature tuning**: Add temperature config for tool use
3. **Shorter prompts**: Create condensed versions of system prompts
4. **Explicit step-by-step**: More directive instructions for smaller models

---

## Architecture Notes

### Startup Flow
```
app_flask.py
    → Agent.from_config()
        → PromptLoader (stores path, no file reads)
        → get_client() → OpenAIClient or AnthropicClient
        → MCPToolCaller.initialize() → connects to MCP servers, loads 60 tools
```

### Per-Request Flow
```
User message
    → run_streaming(agent_type="general-purpose")
        → _build_system_prompt() → reads prompt files (cached after first)
        → _build_tools() → reads tool description files
        → llm.chat(system, messages, tools)
        → tool execution loop
```

### Key Files
| File | Purpose |
|------|---------|
| `src/agent.py` | Core agent loop, tool handling |
| `src/llm_client.py` | LLM API abstraction (Anthropic/OpenAI) |
| `src/prompt_loader.py` | Loads prompts from `ifs-prompts/` |
| `src/tools/mcp_client.py` | MCP protocol client |
| `src/tools/mcp_tool_registry.py` | Tool catalog with keyword search |
| `config/base_config.yaml` | LLM provider, model, MCP URLs |
| `config/ifs_knowledge.yaml` | Procedural rules (loaded on tool use) |

---

## Future Considerations

1. **Hybrid model routing**: Use local model for simple queries, Claude for complex
2. **Prompt A/B testing**: Compare prompt variants for different models
3. **Token usage tracking**: Log token costs per request for optimization
4. **Model-specific configs**: Different prompts/settings per model family
