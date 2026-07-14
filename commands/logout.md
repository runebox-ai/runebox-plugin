---
description: Log out of Runebox (optionally removing installed artifacts)
---

Ask the user whether they also want to remove the artifacts Runebox installed (default: keep
them). Then run one of:

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" logout           # keep installed files
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" logout --remove  # also delete them
```

Confirm the result to the user.
