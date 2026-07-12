# Runebox sync — Claude Code plugin + standalone CLI

Your team's [Runebox](https://runebox.ai) registry, kept in sync on every engineer's
machine: log in once with an API key and your org's approved skills, agents, and
commands install and stay current — on **Claude Code, Codex CLI, Cursor, or Gemini
CLI**. One sync core (`scripts/sync.py`, stdlib-only) ships two ways:

- **On Claude Code** — the plugin below, with a `SessionStart` hook.
- **Everywhere else** — the same script as a standalone `runebox-sync` CLI via
  `pipx`/`uvx`. No Claude Code required.

## Install — Claude Code plugin

```
claude plugin marketplace add runebox-ai/runebox-plugin
claude plugin install runebox
/runebox:login
```

`/runebox:login` asks for your org's API key (starts with `rbx_`; your admin
creates it on the org's **Get set up** page at [runebox.ai](https://runebox.ai)),
verifies it, auto-detects which harnesses you have installed, and syncs the whole
approved catalog in one shot.

## Install — standalone CLI (Codex, Cursor, Gemini CLI)

```
pipx install runebox-sync   # or: uvx runebox-sync login
printf '%s' "rbx_yourkey" | runebox-sync login
```

Same key as the plugin path — an engineer on Codex/Cursor/Gemini CLI never touches
Claude Code at all. Add `runebox-sync autosync` to your shell profile so it keeps
syncing on every new shell (there's no `SessionStart` hook outside Claude Code):

```
runebox-sync autosync --apply
```

## Commands

| Plugin (`/runebox:…`) | Standalone CLI (`runebox-sync …`) | What it does |
|---|---|---|
| `/runebox:login` | `login` | Connect and install the approved catalog for every detected harness |
| `/runebox:sync` | `sync [--quiet]` | Sync now (the plugin also runs this automatically at session start) |
| `/runebox:status` | `status` | Show connected orgs and per-harness inventory |
| — | `harness enable\|disable <name>` | Turn a harness (`claude`/`codex`/`cursor`/`gemini`) on or off; disabling uninstalls just that harness's files |
| — | `autosync [--apply]` | Print (or append) a shell-profile snippet that keeps non-Claude harnesses synced |
| `/runebox:logout` | `logout [--remove]` | Disconnect (optionally remove everything it installed) |

## How sync works

- Per org, per enabled harness: one conditional GET against the org catalog.
  Nothing changed → a cheap `304` and zero output. Something changed → it
  installs/updates/removes and prints one line. A skill installs identically to
  every harness (the open SKILL.md format); a command's filename/location differs
  per harness; agents/output styles/`claude_md` stay Claude Code-only for now.
- Every file it installs is tracked in a local manifest, per (org, harness). It
  only ever touches files it installed itself — never your own skills or settings.
- When an admin **yanks** a bad version, the next sync removes it from your
  machine. When your key is revoked, sync stops with a single notice.
- Adding a *harness* the server can target requires a client release (a new
  allowlisted install root) — a compromised server can't write anywhere new.

## Requirements

- `python3` on PATH — the sync script is stdlib-only, no dependencies, works
  standalone or as a Claude Code plugin.

## Security notes

- API keys are read from stdin at login (never passed as a CLI argument) and
  stored in `~/.runebox/credentials.json` with `0600` permissions.
- Install paths from the server are validated client-side against a per-harness
  roots allowlist — a hostile path can't escape those directories, and an
  unrecognized root name is rejected outright.
- Sync failures never block your session; the hook (and `--quiet` sync) exits quietly.

---

Runebox is a private, governed artifact registry for teams using AI coding
agents — publish once, scan + review every version, and every engineer's tools
stay in sync, whichever harness they run. Free for teams up to 5 seats at
[runebox.ai](https://runebox.ai).
