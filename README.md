# IFS Cloud ERP Agent

An IFS Cloud ERP assistant powered by Claude, following the "80/20 rule" philosophy where **prompts are the product (80%)** and code is minimal (20%).

## Steps to Run After Cloning

```bash
# 1. Clone the repo
git clone <repo-url>
cd ifs-claude-code-agent

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file with API keys
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-your-key-here
OPENAI_API_KEY=lm-studio  # or real key if using OpenAI
EOF

# 5. Start MCP servers (if using IFS Cloud tools)
# (This depends on your MCP server setup - separate process)

# 6. Run Flask UI
cd src
python3 app_flask.py --port 5002

# 7. Open browser to http://127.0.0.1:5002
```

### Quick Start (CLI)

```bash
cd src && python agent.py --config ../config/base_config.yaml
```

## Architecture

```
ifs-claude-code-agent/
├── src/                           # Core application code
│   ├── agent.py                   # CLI/API agent - main loop, tool execution
│   ├── app_flask.py               # Web UI (Flask with SSE streaming)
│   ├── prompt_loader.py           # Template resolver for ifs-prompts/
│   ├── llm_client.py              # LLM abstraction (Anthropic/OpenAI)
│   └── tools/
│       ├── mcp_client.py          # MCP protocol client (SSE + JSON-RPC)
│       └── mcp_tool_registry.py   # Tool catalog (59 IFS Cloud tools)
│
├── ifs-prompts/                   # THE MAIN PRODUCT (21 markdown files)
│   ├── system-prompt-main-system-prompt-ifs.md    # Core agent behavior
│   ├── agent-prompt-explore-ifs.md                # Explore subagent
│   ├── agent-prompt-plan-mode-enhanced-ifs.md     # Plan subagent
│   └── ...                                        # Tool descriptions, reminders
│
├── config/
│   ├── base_config.yaml           # API keys, model selection, MCP URLs
│   ├── ifs_knowledge.yaml         # Procedural rules + semantic domain facts
│   └── prompt_variables.yaml      # Variable bindings for templates
│
└── LEGACY/                        # Reference implementations
```

## The 80/20 Rule

> "The model is 80%. Code is 20%."

This means:
- **Prompts are the product** - They live in `ifs-prompts/`
- **Code just loads and executes** - It should be minimal and boring
- **Never hardcode prompts in Python** - Always load from markdown files

See [CLAUDE.md](CLAUDE.md) for detailed development guidelines.

## Components

### Core Files

| File | Purpose |
|------|---------|
| `src/agent.py` | Main agent loop with streaming support |
| `src/app_flask.py` | Web UI with SSE streaming |
| `src/prompt_loader.py` | Loads and resolves prompt templates |
| `src/llm_client.py` | Anthropic/OpenAI abstraction layer |
| `src/tools/mcp_client.py` | MCP protocol client |
| `src/tools/mcp_tool_registry.py` | 59 IFS Cloud tool definitions |

### Agent Types

| Type | Use Case | Tools |
|------|----------|-------|
| **Explore** | Read-only data exploration | MCPSearch |
| **Plan** | Execution planning (5-phase workflow) | MCPSearch, TodoWrite, AskUserQuestion |
| **general-purpose** | Full capabilities (default) | All tools |
| **summarizer** | Conversation compression | None |

### Orchestration Tools

1. **MCPSearch** - Discover and load MCP tools with keyword search
2. **TodoWrite** - Track multi-step task progress
3. **Task** - Spawn specialized subagents
4. **AskUserQuestion** - Interactive user input

### MCP Tools

59 IFS Cloud tools organized by category:
- Inventory management
- Order processing
- Shipment handling
- Customer data
- Planning operations

## Configuration

### Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional (for OpenAI models)
OPENAI_API_KEY=sk-...
```

### Config Files

**`config/base_config.yaml`** - Primary configuration:
```yaml
llm_provider: anthropic              # or "openai"
anthropic_model: claude-sonnet-4-20250514
openai_model: o3
prompts_dir: ../ifs-prompts
```

**`config/ifs_knowledge.yaml`** - Domain knowledge:
- Procedural rules (tool-specific instructions)
- Semantic facts (domain mappings, site info)

**`config/prompt_variables.yaml`** - Template variables for prompts

## How It Works

1. **User submits message** → Flask `/chat` endpoint
2. **Agent builds system prompt** from markdown files
3. **LLM responds** with text or tool calls
4. **Tool execution**:
   - MCPSearch: Load tools via keyword search
   - TodoWrite: Update task tracker
   - Task: Spawn recursive subagent
   - MCP tools: Call external ERP system
5. **Loop continues** until complete
6. **Streaming output** yields events to client

## Development

### Adding New Behavior

1. **First, try adding it to the prompt** in `ifs-prompts/`
2. **Only add code if** the model physically cannot do it
3. **Test with the prompt alone** before adding any code

### Where to Make Changes

| Task | Location |
|------|----------|
| Change agent personality/tone | `ifs-prompts/system-prompt-main-system-prompt-ifs.md` |
| Add ERP workflow knowledge | `ifs-prompts/system-prompt-main-system-prompt-ifs.md` |
| Change tool discovery behavior | `ifs-prompts/tool-description-mcpsearch-ifs.md` |
| Add procedural rules for a tool | `config/ifs_knowledge.yaml` |
| Fix UI rendering | `src/app_flask.py` (CSS/JS only) |
| Add new MCP tool support | `src/tools/mcp_client.py` |

## Current Status

### Complete
- Core agent loop with streaming
- Prompt loading system with variable substitution
- LLM abstraction (Anthropic + OpenAI)
- Tool execution framework
- MCP client integration
- Flask web UI
- 21 prompt files
- Domain knowledge injection

### TODO
- MCP servers need to be running for actual IFS integration
- Memory system (designed but not implemented)
- Authentication layer
- Production UI polish
- Test coverage

## License

Proprietary - IFS internal use only.
