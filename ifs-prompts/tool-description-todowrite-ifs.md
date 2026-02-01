<!--
name: 'Tool Description: TodoWrite (IFS)'
description: Condensed todo tool for IFS agent - saves ~2000 tokens
-->
Track multi-step ERP tasks. Use for: inventory audits (3+ parts), order workflows, multi-warehouse queries.

**When to use:** Tasks with 3+ steps. **Skip for:** simple lookups, single queries.

**Task states:** pending | in_progress (one at a time) | completed

**Schema:**
```json
{"todos": [{"content": "Check inventory for part X", "status": "in_progress"}]}
```

Mark completed IMMEDIATELY after finishing. Only one task in_progress at a time.
