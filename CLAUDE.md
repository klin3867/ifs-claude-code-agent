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
