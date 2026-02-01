# Session Context - IFS Claude Code Agent

**Last Updated**: 2026-01-20

## Project State

**Status**: Experimental / Active Development

The IFS Cloud ERP Agent is functional with:
- Web UI (Flask) at `src/app_flask.py`
- CLI agent at `src/agent.py`
- 59 MCP tools across 2 servers (planning + customer)
- Hybrid model routing (Claude + local models)
- Prompt-first architecture (21 prompt files in `ifs-prompts/`)

## Current Git Status

Uncommitted changes:
- `.gitignore` - Modified
- `config/base_config.yaml` - Modified
- `requirements.txt` - Modified
- `src/agent.py` - Modified
- `src/app_flask.py` - Modified
- `src/llm_client.py` - Modified

Untracked files:
- `IMPROVEMENTS.md` - Feature gaps documentation
- `test_hybrid_comparison.py` - Benchmark script

## Key Architecture Decisions

### 80/20 Rule
- **Prompts are the product** (80%) - Live in `ifs-prompts/`
- **Code is minimal** (20%) - Just loads and executes

### Token Efficiency
- Lazy-load tool schemas via MCPSearch meta-tool
- 90-95% token reduction vs embedding all schemas
- Prompt caching with Anthropic API

### Model Routing
- Smart agents (general-purpose, Plan) → Claude Sonnet
- Cheap agents (Explore, summarizer) → Local gpt-oss-20b

## Known Gaps (from IMPROVEMENTS.md)

### Missing Features
1. **EnterPlanMode/ExitPlanMode tools** - Agent cannot proactively switch to planning mode
2. **11 unused prompt files** - Some well-written prompts not wired in

### Local Model Issues
- gpt-oss-20b sometimes stops tool chain early
- Needs simplified prompts for smaller models
- Temperature tuning needed

## File Reference

### Core Files
| File | Lines | Purpose |
|------|-------|---------|
| `src/agent.py` | ~950 | Core agent loop with episodic memory |
| `src/app_flask.py` | ~959 | Web UI |
| `src/llm_client.py` | ~200 | LLM abstraction |
| `src/prompt_loader.py` | ~80 | Template resolver |
| `src/episodic_memory.py` | ~150 | Cross-task learning |
| `src/tools/mcp_client.py` | ~150 | MCP protocol |
| `src/tools/mcp_tool_registry.py` | ~200 | Tool catalog |

### Prompt Files in Use (13/24)
- `system-prompt-main-system-prompt-ifs.md` - Core behavior
- `agent-prompt-explore-ifs.md` - Explore agent
- `agent-prompt-plan-mode-enhanced-ifs.md` - Plan agent
- `agent-prompt-conversation-summarization.md` - Summarizer
- `tool-description-mcpsearch-ifs.md` - Tool discovery
- `tool-description-task-ifs.md` - Subagent spawning (CONDENSED: 138 tokens)
- `tool-description-todowrite-ifs.md` - Task tracking (CONDENSED: 141 tokens)
- `tool-description-askuserquestion-ifs.md` - User clarification (CONDENSED: 79 tokens)
- `system-prompt-censoring-assistance-with-malicious-activities.md` - Security
- `system-reminder-plan-mode-is-active-ifs.md` - Plan reminder
- Original verbose versions kept for reference (`*-todowrite.md`, `*-task.md`, etc.)

### Config Files
- `config/base_config.yaml` - LLM provider, model, MCP URLs
- `config/ifs_knowledge.yaml` - IFS domain rules
- `config/prompt_variables.yaml` - Template variables

## Session History

### 2026-01-20 - Initial Exploration
- Explored complete codebase structure
- Enhanced CLAUDE.md with technical details
- Created this SESSION.md file
- Documented architecture patterns and file organization

### 2026-01-20 - Token Optimization Implementation
- **Problem**: Complex order queries hit 30K token/min rate limit
- **Solution**: Extended `analyze_unreserved_demand_by_warehouse` tool with auto shipment creation
- **Changes made**:
  - Added `auto_create_shipments` and `target_warehouse` params (planning_manager.py:4284-4285)
  - Added shipment creation logic (~75 lines, planning_manager.py:4553-4628)
  - Updated docstring with new params
  - Added knowledge rules to `config/ifs_knowledge.yaml`
- **Note**: MCP servers directory is gitignored - changes are local only
- **Token reduction**: ~90% for order review queries (from ~15K to ~2K tokens)
- **Design docs**: `docs/plans/2026-01-20-token-optimization-*.md`

### 2026-01-20 - Agent Token Efficiency & Memory Implementation
- **Problem**: Tool descriptions consuming ~3,983 tokens per request; episodic memory config existed but wasn't implemented
- **Solutions implemented**:

  **1. Condensed Tool Descriptions (~3,625 tokens saved, 91% reduction)**
  - Created `ifs-prompts/tool-description-todowrite-ifs.md` (141 tokens vs 2,468)
  - Created `ifs-prompts/tool-description-task-ifs.md` (138 tokens vs 1,281)
  - Created `ifs-prompts/tool-description-askuserquestion-ifs.md` (79 tokens vs 234)
  - Updated `src/agent.py` to use condensed versions

  **2. Semantic Knowledge Injection**
  - Added `get_semantic_knowledge_summary()` function (agent.py)
  - Injects intent mappings, site info, workflow hints into system prompt UPFRONT
  - Model now knows "move → shipment workflow" BEFORE tool selection

  **3. Episodic Memory Implementation**
  - Created `src/episodic_memory.py` - cross-task learning via stored tool chains
  - Stores successful tool call sequences after task completion
  - Retrieves relevant past experiences for similar queries
  - Uses keyword-based matching (consistent with ifs_knowledge.yaml)
  - Persists to `./cache/memory/episodic_memories.json`
  - Config controlled: `memory_enabled`, `max_episodic_memories`, `memory_retrieval_top_k`

- **Token reduction summary**: ~3,625 tokens per request from tool descriptions alone
- **New files**:
  - `src/episodic_memory.py`
  - `ifs-prompts/tool-description-*-ifs.md` (3 files)

## Next Steps (Suggested)

1. **Commit uncommitted changes** - Review and stage pending modifications
2. **Wire in EnterPlanMode/ExitPlanMode** - Enable dynamic mode switching
3. **Review unused prompts** - Decide which to integrate
4. **Optimize for local models** - Create simplified prompt variants

## Notes for Future Sessions

- The `LEGACY/` directory contains old code for reference only
- Parent monorepo at `../CLAUDE.md` references this project
- MCP servers must be running for tool execution
- Test with `python test_hybrid_comparison.py` to compare models
