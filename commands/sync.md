---
description: Sync your Runebox org catalog now (installs new/updated artifacts, removes yanked ones)
---

Run:

```
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" sync
```

Report what changed (or that everything was already up to date). If it says access was
revoked, suggest `/runebox:login` to reconnect with a fresh key.
