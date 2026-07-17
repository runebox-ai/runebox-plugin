---
description: Push a local skill (or all of them) UP to Runebox, with a diff + confirmation on any conflict
---

Push local skill(s) from this machine to Runebox. Content lands in the user's **personal**
lane by default — never auto-published, never auto-shared to an org — exactly like a private
web upload.

1. Figure out what to push:
   - A specific skill name or path was given (or is obvious from context) → push just that one.
   - The user said "all", "everything", or gave nothing to go on → push all.
   - The user wants their org's catalog (not just their own machine) → add `--org` below.
2. Run (never pass `--confirm` on this first pass):

   ```
   sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" push [NAME_OR_PATH] [--all] [--org]
   ```

   Omitting both `NAME_OR_PATH` and `--all` also pushes everything.
3. Read the output line for each skill:
   - `created` / `unchanged` / `updated` — nothing more to do, just report it.
   - `COLLISION` — the server has DIFFERENT content at that slug than what's on disk. The
     output already includes the full picture: which files changed/were added/removed, a
     `+N -N` line-count summary per file, and the complete unified diff. **Do not silently
     overwrite.** For each collision:
     a. Show the user a concise summary (files touched, +/- counts) and mention the full diff
        is available if they want it.
     b. Ask with **AskUserQuestion** — options along the lines of:
        - "Overwrite server with my local copy" (this local version replaces what's on Runebox)
        - "Keep server version (skip this one)" (leave Runebox alone, don't push)
        - "Show me the full diff first" (print the complete diff from the output above, then
          ask again)
     c. Only on "Overwrite" do you re-run the push for THAT skill with `--confirm` added:
        ```
        sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" push NAME [--org] --confirm
        ```
     d. On "Keep server", move on — no further action for that skill.
   - `ERROR` (incl. revoked access) — report it; suggest `/runebox:login` if access was revoked.
4. When pushing several skills (batch/--all), do this per colliding skill — unchanged and
   newly-created ones need no prompt, only real conflicts do. Finish with a short summary
   table: skill name → created/updated/unchanged/skipped/rejected.
