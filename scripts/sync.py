#!/usr/bin/env python3
"""Runebox plugin sync — stdlib only, no dependencies.

Subcommands:
  login [--api URL]        read an rbx_ API key from stdin, verify it, save it, detect
                            installed harnesses, run first sync
  sync [--quiet]            sync every logged-in org x enabled harness (the SessionStart
                             hook runs this)
  status                    show orgs, key prefix, per-harness inventory
  harness enable|disable N  turn a harness on/off for every logged-in org (N = claude/
                             codex/cursor/gemini); disable uninstalls that harness's files
  autosync [--apply]        print (or append) a shell-profile snippet that keeps non-Claude
                             harnesses synced on every new shell (no SessionStart hook there)
  logout [--remove]         forget credentials (--remove also deletes what we installed)

Contract with the server (see docs/10-multi-harness/design.md):
  GET /api/registry/whoami                -> {org, user, key_name}
  GET /api/registry/catalog?harness=H     -> ETag header + {artifacts: [{id, slug, kind,
                                             version, install: {root, path}}]}
                                             honors If-None-Match -> 304. harness omitted -> "claude".
  GET /api/registry/artifacts/{id}        -> {slug, kind, version, skill_md,
                                             files: [{path, content}]}

Local state:
  $RUNEBOX_DIR (default ~/.runebox)/credentials.json  (0600)
      {org: {token, api, disabled?, harnesses: ["claude", "codex", ...]}}
      (harnesses absent on a legacy entry means ["claude"] — v1 behavior)
  $RUNEBOX_DIR/manifest.json
      {org: {harness: {etag, artifacts: {id: {version, slug, kind, paths}}}}}
      (a v1 manifest was {org: {etag, artifacts}} — migrated under "claude" on first load)

Invariants: we only ever delete paths recorded in our own manifest (and only under our
roots); install paths from the server are re-checked for traversal on our side too; any
failure in --quiet mode exits 0 silently — the hook must never block a session. Adding a
harness the server can target requires a client release (a new allowlisted root) — by design.
"""
import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

RUNEBOX_DIR = Path(os.environ.get("RUNEBOX_DIR", str(Path.home() / ".runebox")))
DEFAULT_API = os.environ.get("RUNEBOX_API", "https://runebox.ai")
CREDS = RUNEBOX_DIR / "credentials.json"
MANIFEST = RUNEBOX_DIR / "manifest.json"
TIMEOUT = 6  # seconds; the hook path must stay snappy

# Install roots the server may target: one per harness, plus "staging" for claude_md (we
# never auto-overwrite a user's own CLAUDE.md — it's staged and pointed at instead). Each is
# env-overridable so tests can point every root at a scratch dir. A server "root" outside
# this allowlist is rejected client-side — adding a harness requires a client release.
ROOTS = {
    "claude": Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude"))),
    "codex": Path(os.environ.get("CODEX_DIR", str(Path.home() / ".codex"))),
    "cursor": Path(os.environ.get("CURSOR_DIR", str(Path.home() / ".cursor"))),
    "gemini": Path(os.environ.get("GEMINI_DIR", str(Path.home() / ".gemini"))),
    "staging": RUNEBOX_DIR,
}
HARNESSES = [h for h in ROOTS if h != "staging"]  # detectable / enable-able targets


def _load(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(path, data, mode=None):
    RUNEBOX_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    if mode is not None:
        os.chmod(path, mode)


def _migrate_manifest(manifest):
    """v1 manifest shape was {org: {etag, artifacts}} — implicitly Claude-only. v2 nests per
    harness: {org: {harness: {etag, artifacts}}}. Detect the old shape (an "artifacts" key
    sitting directly on the org) and nest it under "claude". Silent, one-time, idempotent."""
    for slug, org_m in manifest.items():
        if "artifacts" in org_m:
            manifest[slug] = {"claude": org_m}
    return manifest


def _harnesses(cred):
    return cred.get("harnesses") or ["claude"]


def _fetch_json(api, token, route, etag=None):
    """Return (status, parsed_json_or_None, etag). 304 -> (304, None, etag)."""
    req = urllib.request.Request(
        api.rstrip("/") + route, headers={"Authorization": "Bearer " + token}
    )
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode()), resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None, etag
        raise


def _target(install):
    root_name = install["root"]
    if root_name not in ROOTS:
        raise ValueError("unsafe install root: " + root_name)
    root = ROOTS[root_name].resolve()
    path = (root / install["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("unsafe install path: " + install["path"])
    return path


def _install(item, content):
    """Write an artifact to disk. Returns the list of top-level paths we now own."""
    target = _target(item["install"])
    if item["kind"] == "skill":  # a folder: SKILL.md + helper files
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(content["skill_md"])
        for f in content.get("files") or []:
            p = (target / f["path"]).resolve()
            if not p.is_relative_to(target):
                raise ValueError("unsafe file path in skill: " + f["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"])
        return [str(target)]
    target.parent.mkdir(parents=True, exist_ok=True)  # every other kind: one .md file
    target.write_text(content["skill_md"])
    return [str(target)]


def _remove(paths):
    for p in paths:
        p = Path(p)
        # never delete outside our roots, even if the manifest was tampered with
        if not any(p.is_relative_to(r.resolve()) for r in ROOTS.values()):
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()


def _sync_org_harness(cred, harness, org_h):
    """Sync one (org, harness) pair against org_h = {etag, artifacts}. Returns a summary
    string, or None if nothing changed."""
    status, catalog, etag = _fetch_json(
        cred["api"], cred["token"], f"/api/registry/catalog?harness={harness}", org_h.get("etag")
    )
    if status == 304:
        return None
    wanted = {it["id"]: it for it in catalog["artifacts"]}
    installed = org_h["artifacts"]
    added, updated, removed = 0, 0, 0
    for aid in [a for a in installed if a not in wanted]:  # yanked / unpublished / unsupported now
        _remove(installed.pop(aid)["paths"])
        removed += 1
    for aid, item in wanted.items():
        cur = installed.get(aid)
        if cur and cur["version"] == item["version"]:
            continue
        _, content, _ = _fetch_json(cred["api"], cred["token"], "/api/registry/artifacts/" + aid)
        paths = _install(item, content)
        installed[aid] = {
            "version": item["version"], "slug": item["slug"],
            "kind": item["kind"], "paths": paths,
        }
        updated, added = (updated + 1, added) if cur else (updated, added + 1)
    org_h["etag"] = etag
    if not (added or updated or removed):
        return None
    parts = [f"{n} {label}" for n, label in
             ((added, "installed"), (updated, "updated"), (removed, "removed")) if n]
    return ", ".join(parts)


def _sync_org(slug, cred, manifest):
    """Sync one org across every harness it has enabled. Returns a list of summary lines."""
    if cred.get("disabled"):
        return []
    org_m = manifest.setdefault(slug, {})
    lines = []
    for harness in _harnesses(cred):
        org_h = org_m.setdefault(harness, {"etag": None, "artifacts": {}})
        try:
            line = _sync_org_harness(cred, harness, org_h)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                cred["disabled"] = True  # print once, then stay quiet until re-login
                return [f"runebox: your access to {slug} was revoked — /runebox:login to reconnect"]
            raise
        if line:
            lines.append(f"runebox [{slug}/{harness}]: {line}")
    return lines


def cmd_sync(quiet):
    creds = _load(CREDS)
    if not creds:
        if not quiet:
            print("runebox: not logged in — run `runebox-sync login`")
        return 0
    manifest = _migrate_manifest(_load(MANIFEST))
    dirty_creds = False
    for slug, cred in creds.items():
        was_disabled = cred.get("disabled")
        try:
            for line in _sync_org(slug, cred, manifest):
                print(line)
        except Exception as e:
            if not quiet:
                print(f"runebox [{slug}]: sync failed: {e}")
        if cred.get("disabled") and not was_disabled:
            dirty_creds = True
    _save(MANIFEST, manifest)
    if dirty_creds:
        _save(CREDS, creds, 0o600)
    return 0


def cmd_login(api):
    if sys.stdin.isatty():
        # interactive: prompt + Enter finishes; the bare read() below would sit silent until ^D
        print("Paste your org API key (rbx_…), then Enter:", end=" ", flush=True)
        key = sys.stdin.readline().strip()
    else:
        key = sys.stdin.read().strip()  # piped (plugin/CI): read to EOF, no trailing newline needed
    if not key.startswith("rbx_"):
        print("runebox: that doesn't look like an API key (expected rbx_…)")
        return 1
    try:
        _, who, _ = _fetch_json(api, key, "/api/registry/whoami")
    except Exception as e:
        code = getattr(e, "code", None)
        print(f"runebox: login failed ({code or e}) — check the key and try again")
        return 1
    slug = who["org"]
    detected = [h for h in HARNESSES if ROOTS[h].exists()] or ["claude"]
    creds = _load(CREDS)
    creds[slug] = {"token": key, "api": api, "harnesses": detected}  # re-login replaces any disabled entry
    _save(CREDS, creds, 0o600)
    print(f"✓ Logged in to {slug} as {who.get('user', '?')} — detected: {', '.join(detected)}")
    return cmd_sync(quiet=False)


def cmd_status():
    creds = _load(CREDS)
    if not creds:
        print("runebox: not logged in — run `runebox-sync login`")
        return 0
    manifest = _migrate_manifest(_load(MANIFEST))
    for slug, cred in creds.items():
        harnesses = _harnesses(cred)
        state = "REVOKED" if cred.get("disabled") else "ok"
        print(f"{slug} [{state}]  key {cred['token'][:8]}…")
        org_m = manifest.get(slug, {})
        for harness in harnesses:
            arts = org_m.get(harness, {}).get("artifacts", {})
            kinds = {}
            for a in arts.values():
                kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
            inventory = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "nothing installed"
            print(f"    {harness}: {inventory}")
        for h in HARNESSES:
            if h not in harnesses and ROOTS[h].exists():
                print(f"    hint: {h} detected on disk but not enabled — `runebox-sync harness enable {h}`")
    return 0


def cmd_harness(action, name):
    if name not in HARNESSES:
        print(f"runebox: unknown harness '{name}' — choose from {', '.join(HARNESSES)}")
        return 1
    creds = _load(CREDS)
    if not creds:
        print("runebox: not logged in — run `runebox-sync login` first")
        return 1
    manifest = _migrate_manifest(_load(MANIFEST))
    for slug, cred in creds.items():
        harnesses = cred.setdefault("harnesses", ["claude"])
        if action == "enable":
            if name not in harnesses:
                harnesses.append(name)
        else:
            if name in harnesses:
                harnesses.remove(name)
            org_h = manifest.get(slug, {}).pop(name, None)  # uninstall that harness's files now
            if org_h:
                for a in org_h.get("artifacts", {}).values():
                    _remove(a["paths"])
    _save(CREDS, creds, 0o600)
    _save(MANIFEST, manifest)
    hint = " — run `runebox-sync sync` to install" if action == "enable" else ""
    print(f"runebox: {action}d {name} for {len(creds)} org(s){hint}")
    return 0


# Non-Claude harnesses have no SessionStart hook to auto-sync from, so v1 (docs/10-multi-
# harness/design.md) offers a printed shell-profile snippet instead of a per-harness daemon;
# the 304 fast path makes running it on every shell start cheap.
AUTOSYNC_SNIPPET = "command -v runebox-sync >/dev/null && (runebox-sync sync --quiet &) 2>/dev/null"


def _shell_profile():
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return Path.home() / ".zshrc"
    if "bash" in shell:
        return Path.home() / ".bashrc"
    return None


def cmd_autosync(apply):
    print("Add this line to your shell profile (~/.zshrc, ~/.bashrc, …) so Codex/Cursor/Gemini")
    print("CLI stay synced on every new shell, with no Claude Code session-start hook needed:")
    print()
    print("    " + AUTOSYNC_SNIPPET)
    print()
    if not apply:
        print("Run `runebox-sync autosync --apply` to append it for you.")
        return 0
    profile = _shell_profile()
    if profile is None:
        print("runebox: couldn't detect your shell (checked $SHELL) — add the line above by hand.")
        return 1
    existing = profile.read_text() if profile.exists() else ""
    if AUTOSYNC_SNIPPET in existing:
        print(f"runebox: already present in {profile}")
        return 0
    with profile.open("a") as f:
        f.write("\n# Runebox: keep non-Claude harnesses synced\n" + AUTOSYNC_SNIPPET + "\n")
    print(f"runebox: appended to {profile} — restart your shell (or `source {profile}`) to pick it up")
    return 0


def cmd_logout(remove):
    manifest = _migrate_manifest(_load(MANIFEST))
    if remove:
        for org_m in manifest.values():
            for harness_m in org_m.values():
                for a in harness_m.get("artifacts", {}).values():
                    _remove(a["paths"])
        MANIFEST.unlink(missing_ok=True)
    CREDS.unlink(missing_ok=True)
    print("runebox: logged out" + (" and removed installed artifacts" if remove else ""))
    return 0


def main():
    p = argparse.ArgumentParser(prog="runebox-sync")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("login")
    lp.add_argument("--api", default=DEFAULT_API)
    sp = sub.add_parser("sync")
    sp.add_argument("--quiet", action="store_true")
    sub.add_parser("status")
    hp = sub.add_parser("harness")
    hsub = hp.add_subparsers(dest="action", required=True)
    for name in ("enable", "disable"):
        hh = hsub.add_parser(name)
        hh.add_argument("name")
    ap = sub.add_parser("autosync")
    ap.add_argument("--apply", action="store_true")
    op = sub.add_parser("logout")
    op.add_argument("--remove", action="store_true")
    a = p.parse_args()
    if a.cmd == "login":
        return cmd_login(a.api)
    if a.cmd == "autosync":
        return cmd_autosync(a.apply)
    if a.cmd == "sync":
        try:
            return cmd_sync(a.quiet)
        except Exception:
            return 0 if a.quiet else 1  # the hook must never block a session
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "harness":
        return cmd_harness(a.action, a.name)
    return cmd_logout(a.remove)


if __name__ == "__main__":
    sys.exit(main())
