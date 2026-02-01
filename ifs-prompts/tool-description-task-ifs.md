<!--
name: 'Tool Description: Task (IFS)'
description: Condensed subagent tool for IFS agent - saves ~1000 tokens
-->
Spawn subagent for complex tasks.

**Agent types:**
- **Explore** (aux model): Research - find tools, analyze data, no mutations
- **Plan** (smart model): Multi-step planning with user approval
- **general-purpose** (smart model): Full ERP operations including mutations

**Schema:**
```json
{"prompt": "Find all unreserved demand in warehouse A205", "subagent_type": "Explore"}
```

Subagent returns result to you. Summarize for user.
