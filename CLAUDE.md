# IFS Cloud ERP Agent

## The 80/20 Rule - CRITICAL

**"The model is 80%. Code is 20%."**

This means:
- **Prompts are the product** - They live in `../ifs-prompts/`
- **Code just loads and executes** - It should be minimal and boring
- **Never hardcode prompts in Python** - Always load from markdown files

### Before Making Changes, Ask:

1. **Is this a prompt change or a code change?**
   - Prompt changes → Edit files in `../ifs-prompts/`
   - Code changes → Only if the model literally cannot do something

2. **Am I duplicating prompt content in code?**
   - If yes, STOP. Load it from `ifs-prompts/` instead.
   - Use `PromptLoader` for all system prompts.

3. **Am I adding complexity to "help" the model?**
   - The model doesn't need help. It needs clear instructions in the prompt.
   - Don't add code scaffolding when a prompt section would work.

## Architecture

```
ifs-cloud-erp-agent/
├── src/
│   ├── app_flask.py        # Web UI - loads prompts, streams responses
│   ├── agent.py            # CLI agent - loads prompts, runs loop
│   ├── prompt_loader.py    # Template resolver for ifs-prompts/
│   ├── llm_client.py       # Thin LLM wrapper (Anthropic/OpenAI)
│   └── tools/
│       ├── mcp_client.py       # MCP protocol client
│       └── mcp_tool_registry.py # Tool catalog with keyword search
├── config/
│   ├── base_config.yaml        # API keys, MCP URLs
│   └── ifs_knowledge.yaml      # Procedural rules (injected at tool load)
└── LEGACY/                     # Old code for reference only

../ifs-prompts/                 # THE MAIN PRODUCT
├── system-prompt-main-system-prompt-ifs.md    # Core agent behavior
├── agent-prompt-explore-ifs.md                # Explore subagent
├── agent-prompt-plan-mode-enhanced-ifs.md     # Plan subagent
├── tool-description-*.md                      # Tool descriptions
└── ...
```

## How Prompts Are Loaded

Both entry points MUST use `PromptLoader`:

```python
# In app_flask.py
def build_system_prompt() -> str:
    loader = get_prompt_loader()
    parts = []
    parts.append(loader.load("system-prompt-main-system-prompt-ifs.md"))
    # Add tool catalog, etc.
    return "\n\n".join(parts)

# In agent.py
def _build_system_prompt(self, agent_type: str) -> str:
    parts = []
    parts.append(self.prompt_loader.load(config["system"]))
    # ...
    return "\n\n".join(parts)
```

**NEVER do this:**
```python
# BAD - hardcoded prompt in Python
parts.append("""You are an IFS Cloud ERP assistant...
... 100 lines of prompt ...
""")
```

## Adding New Behavior

1. **First, try adding it to the prompt** in `ifs-prompts/`
2. **Only add code if** the model physically cannot do it (e.g., network calls, file I/O)
3. **Test with the prompt alone** before adding any code

## Common Mistakes to Avoid

| Mistake | Why It's Wrong | Fix |
|---------|---------------|-----|
| Hardcoding prompts in Python | Violates 80/20, creates duplication | Use `PromptLoader` |
| Adding "helper" code for the model | Model doesn't need code help | Add prompt instructions |
| Fixing model behavior with code | Should be in the prompt | Edit `ifs-prompts/*.md` |
| Multiple sources of truth | Prompts diverge, confusion | Single source in `ifs-prompts/` |
| UI before architecture | Polish without foundation | Wire up prompts first |

## Quick Reference

| Task | Where to Change |
|------|-----------------|
| Change agent personality/tone | `ifs-prompts/system-prompt-main-system-prompt-ifs.md` |
| Add ERP workflow knowledge | `ifs-prompts/system-prompt-main-system-prompt-ifs.md` |
| Change tool discovery behavior | `ifs-prompts/tool-description-mcpsearch-ifs.md` |
| Add procedural rules for a tool | `config/ifs_knowledge.yaml` |
| Fix UI rendering | `src/app_flask.py` (CSS/JS only) |
| Add new MCP tool support | `src/tools/mcp_client.py` |

## Running

```bash
# Web UI
cd src && python app_flask.py

# CLI
cd src && python agent.py --config ../config/base_config.yaml
```

## Environment

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional (for OpenAI models)
OPENAI_API_KEY=sk-...
```

## Tool Discovery (MCPSearch Pattern)

The agent uses **lazy-load tool schemas** for token efficiency:

1. **59 MCP tools** available across two servers (planning + customer)
2. Agent sees **summaries** (~20 tokens each), not full schemas
3. Uses `MCPSearch` meta-tool to find relevant tools by keyword
4. Loads full schema **on demand** when tool is selected
5. **Result**: 90-95% reduction in token usage vs embedding all schemas

**Flow:**
```
User: "Check inventory for part ABC"
    → MCPSearch(query="inventory") → Returns: get_inventory_stock, search_inventory_by_warehouse
    → MCPSearch(query="select:get_inventory_stock") → Loads full schema
    → Agent calls get_inventory_stock(part_no="ABC")
```

## Hybrid Model Routing

Cost optimization via model-specific routing:

```yaml
# config/base_config.yaml
model_routing:
  smart_agents: [general-purpose, Plan]    # Claude Sonnet (complex reasoning)
  aux_agents: [Explore, summarizer]        # Local gpt-oss-20b (fast, free)
```

| Agent Type | Model | Use Case |
|------------|-------|----------|
| general-purpose | Claude Sonnet | Main reasoning, tool orchestration |
| Plan | Claude Sonnet | Complex multi-step planning |
| Explore | Local/Haiku | Read-only tool discovery |
| summarizer | Local/Haiku | Conversation compaction |

## Key Configuration

### `config/base_config.yaml`
- `llm_provider`: `anthropic` or `openai`
- `anthropic_model`: Model name (e.g., `claude-sonnet-4-20250514`)
- `mcp_planning_url`: Inventory/scheduling MCP server
- `mcp_customer_url`: Orders/customers MCP server

### `config/ifs_knowledge.yaml`
Procedural rules injected when tools are loaded:
- Shipment workflow: 3-step process with integer IDs
- Site abbreviations: '205' → 'AC-A205'
- Common error corrections

## Agent Types & Prompts

| Agent Type | System Prompt | Description |
|------------|--------------|-------------|
| `general-purpose` | `system-prompt-main-system-prompt-ifs.md` | Full capabilities, all tools |
| `Explore` | `agent-prompt-explore-ifs.md` | Read-only, tool discovery |
| `Plan` | `agent-prompt-plan-mode-enhanced-ifs.md` | Structured planning, todos |
| `summarizer` | `agent-prompt-conversation-summarization.md` | Compress history |

## Testing

```bash
# Multi-turn conversation tests
cd tests && python test_multiturn.py

# Hybrid model comparison (Claude vs local)
python test_hybrid_comparison.py
```

## Debugging Tips

1. **Tool not found**: Check `mcp_tool_registry.py` keyword index
2. **Wrong parameters**: Check `ifs_knowledge.yaml` procedural rules
3. **Token overflow**: Conversation compaction triggers at 75K tokens
4. **Streaming issues**: Check Flask SSE in `app_flask.py`

## Related Files (See Also)

- `IMPROVEMENTS.md` - Feature gaps and optimization opportunities
- `SESSION.md` - Session context for continuity
- Parent: `../CLAUDE.md` - Monorepo overview
