---
description: Log in to your team's Runebox registry with an API key and install the catalog
---

Log the user in to Runebox:

1. Ask the user for their Runebox API key. It starts with `rbx_` and is created on their
   org's **Get set up** page (or Profile → API keys) at runebox.ai. If they included the key
   in their message already, use that.
2. Run (passing the key on stdin, never as an argument):

   ```
   printf '%s' "<THE_KEY>" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" login
   ```

   If the user gave a custom server URL, add `--api <URL>`.
3. Report the script's output back to the user: which org they're connected to and what was
   installed. If login failed, tell them to check the key wasn't revoked and was copied
   completely.
