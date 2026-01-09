<!--
name: 'Tool Description: MCPSearch (IFS Cloud)'
description: MCPSearch tool with IFS workflow groups and domain knowledge embedded
version: 1.0.0
basedOn: tool-description-mcpsearch-with-available-tools.md
variables:
  - TOOLS
  - TOOL
-->
Search for or select IFS Cloud MCP tools to make them available for use.

**MANDATORY PREREQUISITE - THIS IS A HARD REQUIREMENT**

You MUST use this tool to load MCP tools BEFORE calling them directly.

**Query modes:**

1. **Direct selection** - Use `select:<tool_name>` when you know exactly which tool you need
2. **Keyword search** - Use keywords when you're unsure which tool to use (returns up to 5 tools)

---

## IFS Workflow Groups

When using ANY tool in a workflow group, you will likely need ALL tools in that group. Plan accordingly.

**Shipment Workflow** (always 3 steps in order):
1. `create_shipment_order` - Create the shipment header (returns INTEGER id, e.g., 34)
2. `add_shipment_order_line` - Add parts to ship (use EXACT integer id from step 1)
3. `release_shipment_order` - Release for processing (only after lines are added)

**Reservation Workflow**:
- `plan_reservation` → `execute_reservation`
- Or: `reserve_shipment_line_handling_unit` / `reserve_shipment_line_partial`

**Order Workflow**:
- `create_order` → `add_order_line` → (optional updates/cancellations)

---

## Critical IFS Domain Facts

**Sites & Warehouses:**
- Default site is 'AC' unless user specifies otherwise
- Site AC warehouses: AC-A110, AC-A205
- Shorthand works: '205' → 'AC-A205', '110' → 'AC-A110'
- References to 'Warehouse 105' mean site AC itself

**Inventory Tools:**
- `get_inventory_stock` - Check ALL warehouses at a site (use this first)
- `search_inventory_by_warehouse` - Query ONE specific warehouse only
- Parameter is `part_no` (not `part` or `part_number`)

**Common Mistakes to Avoid:**
- shipment_order_id is INTEGER (34), never string ('34' or 'SO-34')
- Always determine part_no BEFORE creating shipment - order is useless without lines
- Order numbers with asterisks like '*1063' must include the asterisk

---

**Example - Shipment Task:**

```
User: Move 10 units of ABC-123 from warehouse 205 to 110

Plan:
1. Verify part_no ABC-123 exists and has stock at AC-A205
2. Create shipment order: from_warehouse=AC-A205, to_warehouse=AC-A110
3. Add line with part_no=ABC-123, qty=10, using integer shipment_order_id
4. Release shipment order
```

---

Available MCP tools (must be loaded before use):
${TOOLS.map((TOOL)=>TOOL.name).join(`
`)}
