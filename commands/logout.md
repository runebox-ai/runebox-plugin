---
description: Log out of Runebox (optionally removing installed artifacts)
---

Ask the user whether they also want to remove the artifacts Runebox installed (default: keep
them). Then run one of:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" logout           # keep installed files
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" logout --remove  # also delete them
```

Confirm the result to the user.
