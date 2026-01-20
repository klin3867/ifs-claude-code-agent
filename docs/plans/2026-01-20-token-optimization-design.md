# Token Optimization: Server-Side Aggregation

**Date**: 2026-01-20
**Status**: Approved
**Problem**: Complex order fulfillment queries consume 15,000-30,000+ tokens, hitting rate limits

## Summary

Add a new MCP tool `analyze_order_fulfillment` that performs order/inventory analysis server-side, returning a compact summary instead of raw data. Expected token reduction: ~90%.

## Problem Statement

The query "Review customer orders due next week, check inventory in A205, find alternatives in 105/110, create shipments" requires:

- Fetching 50+ orders (~5,000 tokens)
- Checking inventory per part (~1,000 tokens × N parts)
- Multiple reasoning turns with accumulated context
- Total: 15,000-30,000+ input tokens, hitting 30K/minute rate limit

## Solution: Server-Side Aggregation

### New MCP Tool Interface

**Location**: `ifs-planning-manager-mcp/planning_manager.py`

```python
@mcp.tool()
def analyze_order_fulfillment(
    target_warehouse: str,              # e.g., "AC-A205"
    days_ahead: int = 7,                # Orders due within N days
    include_alternatives: bool = True,  # Check other warehouses
    auto_create_shipments: bool = False # If true, create shipment orders
) -> dict:
    """
    Analyze order fulfillment capacity and identify shortages.

    Returns:
    {
        "summary": {
            "orders_analyzed": 47,
            "fully_fulfillable": 35,
            "partial_shortages": 8,
            "critical_shortages": 4
        },
        "shortages": [
            {
                "order_no": "*1063",
                "part_no": "ABC-123",
                "qty_needed": 100,
                "qty_available": 20,
                "shortage": 80,
                "alternatives": [
                    {"warehouse": "AC", "qty": 50},
                    {"warehouse": "AC-A110", "qty": 45}
                ]
            }
        ],
        "recommended_shipments": [...],
        "created_shipments": [...]  # If auto_create_shipments=true
    }
    """
```

### Data Flow (Server-Side)

```
1. Query IFS: CustomerOrderLine where PlannedDeliveryDate < today+N
   → Filter to lines shipping from target_warehouse

2. Group by part_no, sum quantities needed

3. Query IFS: InventoryPartInStock for target_warehouse
   → Build shortage list (needed - available)

4. If include_alternatives:
   Query other warehouses at same site (AC, AC-A110, AC-A205)
   → Attach alternative sources to each shortage

5. If auto_create_shipments:
   For each shortage with alternatives:
   → create_shipment_order + add_line + release

6. Return compact JSON summary (~500 tokens)
```

### Agent Workflow Comparison

**Before (token-heavy)**:
```
User: "Review orders due next week for A205..."
Agent: MCPSearch → get_customer_orders → [5000 tokens]
Agent: MCPSearch → get_inventory_stock → [1000 tokens × N]
Agent: Loops, reasons, creates shipments one by one
Total: 15,000-30,000+ input tokens
```

**After (token-light)**:
```
User: "Review orders due next week for A205..."
Agent: MCPSearch → analyze_order_fulfillment
Agent: Calls tool(warehouse="AC-A205", days_ahead=7)
Agent: Receives 500-token summary
User: "Create the shipments"
Agent: Calls tool with auto_create_shipments=true
Total: ~2,000 input tokens
```

## Error Handling

| Scenario | Handling |
|----------|----------|
| No orders due | Return empty summary, no error |
| IFS timeout | Retry once, return partial with warnings |
| Part not in inventory | Include with qty_available=0 |
| Shipment creation fails | Log to failed_shipments, continue batch |

### Validation Rules

- Warehouse must exist (validate against known sites)
- `days_ahead` capped at 30 (prevent massive queries)
- `auto_create_shipments` defaults to false (safety)

## Implementation Plan

| Step | Location | Changes |
|------|----------|---------|
| 1 | `ifs-planning-manager-mcp/planning_manager.py` | Add `analyze_order_fulfillment` tool (~100 lines) |
| 2 | Same file | Add OData queries for CustomerOrderLine (~50 lines) |
| 3 | `config/ifs_knowledge.yaml` | Add procedural rules for new tool (~10 lines) |
| 4 | Flask UI | Test with original query |

### No Changes Required

- `agent.py` - Tool auto-discovered via MCPSearch
- `app_flask.py` - No UI changes
- `ifs-prompts/` - No prompt changes

## Success Criteria

- [ ] Tool returns correct shortage analysis
- [ ] Token usage < 3,000 for full workflow
- [ ] auto_create_shipments creates valid shipment orders
- [ ] No rate limit errors on complex queries

## Future Enhancements

1. Add `priority_filter` to focus on critical orders only
2. Add `exclude_parts` to skip known backorder items
3. Return CSV/Excel export option for reporting
