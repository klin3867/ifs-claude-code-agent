# Token Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `auto_create_shipments` parameter to existing `analyze_unreserved_demand_by_warehouse` tool.

**Architecture:** Extend existing tool (lines 4276-4580) with optional shipment creation. When enabled, creates shipment orders for parts that can be sourced from alternative warehouses.

**Tech Stack:** Python, FastMCP, IFS OData APIs

---

## Task 1: Add auto_create_shipments Parameter

**Files:**
- Modify: `/Users/jimmykline/dev/ifs-mcp-starter/servers/ifs-planning-manager-mcp/planning_manager.py:4276-4284`

**Step 1: Add parameter to function signature**

Change line 4276-4284 from:
```python
@mcp.tool()
async def analyze_unreserved_demand_by_warehouse(
    days_ahead: int = 7,
    include_past_due: bool = True,
    site: str = "AC",
    warehouse_priority: Optional[List[str]] = None,
    customer_no: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
```

To:
```python
@mcp.tool()
async def analyze_unreserved_demand_by_warehouse(
    days_ahead: int = 7,
    include_past_due: bool = True,
    site: str = "AC",
    warehouse_priority: Optional[List[str]] = None,
    customer_no: Optional[str] = None,
    limit: int = 200,
    auto_create_shipments: bool = False,
    target_warehouse: Optional[str] = None,
) -> Dict[str, Any]:
```

**Step 2: Verify change**

Run: `grep -n "auto_create_shipments" /Users/jimmykline/dev/ifs-mcp-starter/servers/ifs-planning-manager-mcp/planning_manager.py`
Expected: Line ~4283 shows the new parameter

---

## Task 2: Add Shipment Creation Logic

**Files:**
- Modify: `/Users/jimmykline/dev/ifs-mcp-starter/servers/ifs-planning-manager-mcp/planning_manager.py`
- Insert after line ~4575 (before the return statement)

**Step 1: Add shipment creation block**

Insert before `return ok(` (around line 4551):

```python
        # Step 6: Optionally create shipment orders for parts with alternative sources
        created_shipments = []
        failed_shipments = []

        if auto_create_shipments and target_warehouse:
            # Normalize target warehouse
            target_wh = target_warehouse if target_warehouse.startswith("A") else f"A{target_warehouse}"

            for part_info in sourcing_plan:
                # Skip if no shortfall at target or no alternative sources
                if part_info["status"] == "FULFILLABLE":
                    continue
                if not part_info["sources"]:
                    continue

                # Find sources NOT from target warehouse
                for source in part_info["sources"]:
                    if source["warehouse"] == target_wh:
                        continue  # Skip - this is the target

                    qty_to_move = source["qty_to_source"]
                    if qty_to_move <= 0:
                        continue

                    try:
                        # Create shipment order
                        shipment_result = await create_shipment_order(
                            from_warehouse=source["warehouse"],
                            to_warehouse=target_wh,
                            site=site,
                        )

                        if not shipment_result.get("ok"):
                            failed_shipments.append({
                                "part_no": part_info["part_no"],
                                "from_warehouse": source["warehouse"],
                                "error": shipment_result.get("error", "Unknown error"),
                            })
                            continue

                        shipment_id = shipment_result["data"]["shipment_order_id"]

                        # Add line
                        line_result = await add_shipment_order_line(
                            shipment_order_id=shipment_id,
                            part_no=part_info["part_no"],
                            qty_to_ship=qty_to_move,
                            site=site,
                        )

                        if not line_result.get("ok"):
                            failed_shipments.append({
                                "part_no": part_info["part_no"],
                                "shipment_order_id": shipment_id,
                                "error": f"Failed to add line: {line_result.get('error')}",
                            })
                            continue

                        # Release shipment
                        release_result = await release_shipment_order(shipment_order_id=shipment_id)

                        created_shipments.append({
                            "shipment_order_id": shipment_id,
                            "part_no": part_info["part_no"],
                            "from_warehouse": source["warehouse"],
                            "to_warehouse": target_wh,
                            "qty": qty_to_move,
                            "status": "Released" if release_result.get("ok") else "Planned",
                        })

                    except Exception as e:
                        failed_shipments.append({
                            "part_no": part_info["part_no"],
                            "from_warehouse": source["warehouse"],
                            "error": str(e),
                        })
```

**Step 2: Update return data**

Modify the return `ok()` call to include shipment results. Add these fields to the data dict (around line 4551-4575):

```python
                "auto_create_shipments": auto_create_shipments,
                "target_warehouse": target_warehouse,
                "created_shipments": created_shipments if auto_create_shipments else [],
                "failed_shipments": failed_shipments if auto_create_shipments else [],
```

**Step 3: Update summary message**

After the existing summary line (~4549), add:
```python
        if auto_create_shipments and created_shipments:
            summary += f" | Created {len(created_shipments)} shipment(s)"
        if auto_create_shipments and failed_shipments:
            summary += f" | {len(failed_shipments)} failed"
```

---

## Task 3: Update Docstring

**Files:**
- Modify: `/Users/jimmykline/dev/ifs-mcp-starter/servers/ifs-planning-manager-mcp/planning_manager.py:4285`

**Step 1: Update docstring**

Change line 4285 from:
```python
    """⭐ Analyze unreserved demand and check cascade availability across warehouses."""
```

To:
```python
    """⭐ Analyze unreserved demand and check cascade availability across warehouses.

    Args:
        days_ahead: Number of days to look ahead for orders (default 7)
        include_past_due: Include past due orders in analysis (default True)
        site: IFS site/contract code (default "AC")
        warehouse_priority: List of warehouses to check in order (default ["A205", "A110", "A105"])
        customer_no: Filter to specific customer (optional)
        limit: Max order lines to analyze (default 200)
        auto_create_shipments: If True, create shipment orders to move inventory (default False)
        target_warehouse: Destination warehouse for shipments (required if auto_create_shipments=True)

    Returns:
        Sourcing plan with fulfillability status per part.
        If auto_create_shipments=True, includes created_shipments and failed_shipments.
    """
```

---

## Task 4: Add Knowledge Rules

**Files:**
- Modify: `/Users/jimmykline/dev/ifs-agent-eval/ifs-claude-code-agent/config/ifs_knowledge.yaml`

**Step 1: Add procedural rule for analyze_unreserved_demand_by_warehouse**

Add after line ~41 (after the `get_inventory_stock` section):

```yaml
  analyze_unreserved_demand_by_warehouse:
    keywords: [order, demand, fulfillment, shortage, unreserved, warehouse, cascade]
    rules:
      - "Primary tool for 'review orders and check inventory' queries"
      - "Returns compact sourcing plan - don't fetch raw orders separately"
      - "Set auto_create_shipments=true AND target_warehouse to create shipment orders"
      - "target_warehouse is the DESTINATION - where inventory should END UP"
      - "warehouse_priority determines search order for SOURCE warehouses"
      - "Default priority: A205 → A110 → A105 (or use '205', '110', '105')"
```

**Step 2: Verify change**

Run: `grep -A5 "analyze_unreserved" /Users/jimmykline/dev/ifs-agent-eval/ifs-claude-code-agent/config/ifs_knowledge.yaml`
Expected: Shows the new procedural rules

---

## Task 5: Restart MCP Server and Test

**Step 1: Restart planning MCP server**

```bash
# Find and kill existing server
pkill -f "planning_manager"

# Start fresh
cd /Users/jimmykline/dev/ifs-mcp-starter/servers/ifs-planning-manager-mcp
python3.11 start_sse.py &
```

**Step 2: Verify server started**

```bash
sleep 3 && lsof -i :9001 | grep LISTEN
```
Expected: Python process listening on port 9001

**Step 3: Test via Flask UI**

Open http://127.0.0.1:5001 and run:
```
Review customer orders due next week. Check if there's enough inventory in the A205 warehouse.
If not, check A110 and A105 for alternatives.
```

Expected: Agent uses `analyze_unreserved_demand_by_warehouse` tool, returns compact summary.

**Step 4: Test auto_create_shipments**

Follow up with:
```
Create shipment orders to move inventory to A205.
```

Expected: Agent calls tool with `auto_create_shipments=true, target_warehouse="A205"`

---

## Task 6: Commit Changes

**Step 1: Stage and commit MCP server changes**

```bash
cd /Users/jimmykline/dev/ifs-mcp-starter
git add servers/ifs-planning-manager-mcp/planning_manager.py
git commit -m "feat: add auto_create_shipments to analyze_unreserved_demand_by_warehouse

Enables automatic shipment order creation when inventory shortages
have available stock in alternative warehouses.

New parameters:
- auto_create_shipments: bool (default False)
- target_warehouse: str (required when auto_create_shipments=True)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

**Step 2: Stage and commit knowledge rules**

```bash
cd /Users/jimmykline/dev/ifs-agent-eval/ifs-claude-code-agent
git add config/ifs_knowledge.yaml
git commit -m "feat: add knowledge rules for analyze_unreserved_demand_by_warehouse

Helps agent discover and correctly use the order fulfillment analysis tool.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Success Criteria

- [ ] `analyze_unreserved_demand_by_warehouse` accepts `auto_create_shipments` and `target_warehouse` params
- [ ] Shipment orders created when alternatives exist and `auto_create_shipments=True`
- [ ] Failed shipments logged without stopping batch
- [ ] Agent discovers tool via MCPSearch for order/fulfillment queries
- [ ] Token usage < 3,000 for full order review + shipment creation workflow
