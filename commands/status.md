---
description: Show Runebox connection status and installed artifact inventory
---

Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" status
```

Show the user the result: which orgs they're connected to, key prefix, and what's installed
per kind. If not logged in, point them at `/runebox:login`.
