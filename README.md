# Runebox

Your team's [Runebox](https://runebox.ai) registry in Claude Code. Log in once with a
personal access key, and your org's approved skills, agents, and commands install
automatically — and stay in sync every session.

## Install

```
claude plugin marketplace add runebox-ai/runebox-plugin
claude plugin install runebox
/runebox:login
```

`/runebox:login` will ask for an API key — create one from your org's **Get set up** page
on runebox.ai.

## Commands

- `/runebox:login` — store your API key, verify it, and run the first sync
- `/runebox:sync` — force a sync now
- `/runebox:status` — orgs, keys, installed inventory, last sync
- `/runebox:logout` — remove your stored key (optionally uninstalling synced artifacts)

A `SessionStart` hook also syncs quietly at the start of every Claude Code session — nothing
prints unless something actually changed.

## How it works

`scripts/sync.py` is stdlib-only Python (no dependencies): it calls your org's registry API
for a manifest of approved artifacts, diffs it against what's already installed, and writes
only the files it's responsible for — skills to `~/.claude/skills/<slug>/`, agents to
`~/.claude/agents/<slug>.md`, commands to `~/.claude/commands/<slug>.md`, and so on. It never
touches a file it didn't install itself, and it never overwrites your own `CLAUDE.md`.

Every artifact synced here was safety-scanned and admin-approved in your org's Runebox
registry before it ever reached this plugin.
