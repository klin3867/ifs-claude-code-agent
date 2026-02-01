# IFS Cloud ERP Agent - Improvement Plan

Generated from evaluation on 2026-01-21.

## Executive Summary

The agent successfully completed a complex inventory fulfillment workflow but showed several optimization opportunities:
- **75 seconds** total execution time
- **~25,000 tokens** consumed
- **1 error** (aux model connection) that was recovered from

## Prioritized Improvements

### P0: Critical Fixes

#### 1. Fix Aux Model Fallback (agent.py)
**Problem**: When aux_llm (local model) is unreachable, agent shows "Connection error"
**Solution**: Graceful fallback to primary LLM with warning

```python
# In _get_llm_for_agent_type():
try:
    # Test connection to aux model
    if is_aux and self.aux_llm:
        return self.aux_llm
except ConnectionError:
    print(f"[WARN] Aux model unavailable, using primary LLM")
    return self.llm
```

**Impact**: Eliminates user-facing errors, maintains workflow continuity
**Effort**: Low (15 min)

---

### P1: Speed Improvements

#### 2. MCPSearch Direct-Load Shortcut (mcp_tool_registry.py)
**Problem**: Loading a known tool requires 2 calls (search → select)
**Solution**: Add `load:` prefix for direct schema loading

```python
# Current (2 calls):
MCPSearch(query="inventory")           # Search
MCPSearch(query="select:get_inventory_stock")  # Load

# Improved (1 call when tool name known):
MCPSearch(query="load:get_inventory_stock")    # Direct load
```

**Impact**: 30-40% reduction in MCPSearch calls for experienced users
**Effort**: Low (30 min)

#### 3. Parallel Tool Calls Hint (system prompt)
**Problem**: Agent checks inventory sequentially (6 calls for 3 parts × 2 warehouses)
**Solution**: Add prompt guidance for parallel tool calling

```markdown
## Tool Calling Best Practices
- When checking inventory for multiple parts, call get_inventory_stock in parallel
- Group similar operations together for efficiency
```

**Impact**: 50% reduction in inventory check time
**Effort**: Low (15 min) - prompt change only

---

### P2: Token Cost Reduction

#### 4. TodoWrite Delta Updates (agent.py)
**Problem**: Each TodoWrite sends full list (~300 tokens)
**Solution**: Only send changed items

```python
# Current:
{"todos": [ALL 5 ITEMS]}  # Every update

# Improved:
{"update": {"index": 0, "status": "completed"}}  # Just the change
```

**Impact**: ~1,500 token savings per workflow
**Effort**: Medium (1 hour) - requires tool schema change

#### 5. Compact MCPSearch Results (mcp_tool_registry.py)
**Problem**: Search returns 5 tools × ~50 tokens = 250 tokens
**Solution**: Return top 3 most relevant, add "show more" option

**Impact**: ~40% reduction in search result tokens
**Effort**: Low (30 min)

---

### P3: UX Improvements

#### 6. User-Friendly Error Messages (app_flask.py)
**Problem**: Raw API errors shown to users
```
Error: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error'...
```

**Solution**: Parse and format errors
```
⚠️ API Error: Your Anthropic account has insufficient credits.
   Please add credits at console.anthropic.com/settings/billing
```

**Impact**: Better user experience
**Effort**: Low (30 min)

#### 7. Token Usage Display (app_flask.py)
**Problem**: Users don't see query costs
**Solution**: Add footer showing token usage

```
📊 Tokens: 2,450 in / 1,230 out | Estimated cost: $0.04
```

**Impact**: Cost awareness, debugging
**Effort**: Low (30 min)

#### 8. Tool Call Collapsing (app_flask.py)
**Problem**: Long tool call history clutters UI
**Solution**: Auto-collapse completed tool calls, show summary

**Impact**: Cleaner UI, easier to read responses
**Effort**: Medium (1 hour) - JS/CSS changes

---

## Implementation Order

| Phase | Items | Time | Impact |
|-------|-------|------|--------|
| **Phase 1** | #1, #6, #7 | 1 hour | Fix errors, improve UX |
| **Phase 2** | #2, #3, #5 | 1.5 hours | Speed + token savings |
| **Phase 3** | #4, #8 | 2 hours | Polish |

## Quick Wins (Can Do Now)

1. **Add fallback for aux model** - 15 min
2. **Add prompt hint for parallel calls** - 15 min
3. **Format error messages** - 30 min
4. **Show token usage** - 30 min

Total: ~1.5 hours for significant improvements

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/agent.py` | Aux model fallback, TodoWrite delta |
| `src/app_flask.py` | Error formatting, token display, UI collapse |
| `src/tools/mcp_tool_registry.py` | Direct-load prefix, compact results |
| `ifs-prompts/system-prompt-main-system-prompt-ifs.md` | Parallel calling hints |
