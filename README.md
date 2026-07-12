# Runebox plugin for Claude Code

Your team's [Runebox](https://runebox.ai) registry in Claude Code: log in once
with an API key and your org's approved skills, agents, and commands install on
your machine and stay synced automatically.

## Install

```
claude plugin marketplace add runebox-ai/runebox-plugin
claude plugin install runebox
/runebox:login
```

`/runebox:login` asks for your org's API key (starts with `rbx_`; your admin
creates it on the org's **Get set up** page at [runebox.ai](https://runebox.ai)),
verifies it, and installs the whole approved catalog in one shot.

## Commands

| Command | What it does |
|---|---|
| `/runebox:login` | Connect to your org's registry and install its catalog |
| `/runebox:sync` | Sync now (also runs automatically at session start) |
| `/runebox:status` | Show connected orgs and exactly what's installed |
| `/runebox:logout` | Disconnect (optionally remove everything it installed) |

## How sync works

- A `SessionStart` hook runs a quiet sync every time you start Claude Code: one
  conditional GET against the org catalog. Nothing changed → a cheap `304` and
  zero output. Something changed → it installs/updates/removes and prints one line.
- Every file it installs is tracked in a local manifest. It only ever touches
  files it installed itself — never your own skills or settings.
- When an admin **yanks** a bad version, the next sync removes it from your
  machine. When your key is revoked, sync stops with a single notice.

## Requirements

- Claude Code with plugin support
- `python3` on PATH (the sync script is stdlib-only, no dependencies)

## Security notes

- API keys are read from stdin at login (never passed as a CLI argument) and
  stored in `~/.runebox/credentials.json` with `0600` permissions.
- Install paths from the server are validated client-side against a roots
  allowlist — a hostile path can't escape the Claude config directories.
- Sync failures never block your session; the hook exits quietly.

---

Runebox is a private, governed artifact registry for teams using AI coding
agents — publish once, scan + review every version, and every engineer's tools
stay in sync. Free for teams up to 5 seats at [runebox.ai](https://runebox.ai).
