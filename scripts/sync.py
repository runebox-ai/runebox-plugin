#!/usr/bin/env python3
"""Runebox plugin sync — stdlib only, no dependencies.

Subcommands:
  login [--api URL]   read an rbx_ API key from stdin, verify it, save it, run first sync
  sync [--quiet]      sync every logged-in org (the SessionStart hook runs this)
  status              show orgs, key prefix, installed inventory
  logout [--remove]   forget credentials (--remove also deletes what we installed)

Contract with the server (see docs/09-registry-pivot/design.md):
  GET /api/registry/whoami                -> {org, user, key_name}
  GET /api/registry/catalog               -> ETag header + {artifacts: [{id, slug, kind,
                                             version, install: {root, path}}]}
                                             honors If-None-Match -> 304
  GET /api/registry/artifacts/{id}        -> {slug, kind, version, skill_md,
                                             files: [{path, content}]}

Local state:
  $RUNEBOX_DIR (default ~/.runebox)/credentials.json  (0600)  {org: {token, api, disabled?}}
  $RUNEBOX_DIR/manifest.json   {org: {etag, artifacts: {id: {version, slug, kind, paths}}}}

Invariants: we only ever delete paths recorded in our own manifest (and only under our
roots); install paths from the server are re-checked for traversal on our side too; any
failure in --quiet mode exits 0 silently — the hook must never block a session.
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
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_API = os.environ.get("RUNEBOX_API", "https://runebox.ai")
CREDS = RUNEBOX_DIR / "credentials.json"
MANIFEST = RUNEBOX_DIR / "manifest.json"
TIMEOUT = 6  # seconds; the hook path must stay snappy

# Install roots the server may target. "staging" is for claude_md — we never auto-overwrite
# a user's own CLAUDE.md, we stage it and tell them.
ROOTS = {"claude": CLAUDE_DIR, "staging": RUNEBOX_DIR}


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
    root = ROOTS[install["root"]].resolve()
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


def _sync_org(slug, cred, manifest):
    """Sync one org. Returns a human summary line, or None if nothing changed."""
    if cred.get("disabled"):
        return None
    org_m = manifest.setdefault(slug, {"etag": None, "artifacts": {}})
    try:
        status, catalog, etag = _fetch_json(
            cred["api"], cred["token"], "/api/registry/catalog", org_m.get("etag")
        )
    except urllib.error.HTTPError as e:
        if e.code == 401:
            cred["disabled"] = True  # print once, then stay quiet until re-login
            return f"runebox: your access to {slug} was revoked — /runebox:login to reconnect"
        raise
    if status == 304:
        return None
    wanted = {it["id"]: it for it in catalog["artifacts"]}
    installed = org_m["artifacts"]
    added, updated, removed = 0, 0, 0
    for aid in [a for a in installed if a not in wanted]:  # yanked / unpublished
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
    org_m["etag"] = etag
    if not (added or updated or removed):
        return None
    parts = [f"{n} {label}" for n, label in
             ((added, "installed"), (updated, "updated"), (removed, "removed")) if n]
    return f"runebox [{slug}]: " + ", ".join(parts)


def cmd_sync(quiet):
    creds = _load(CREDS)
    if not creds:
        if not quiet:
            print("runebox: not logged in — run /runebox:login")
        return 0
    manifest = _load(MANIFEST)
    dirty_creds = False
    for slug, cred in creds.items():
        was_disabled = cred.get("disabled")
        try:
            line = _sync_org(slug, cred, manifest)
            if line:
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
    key = sys.stdin.read().strip()
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
    creds = _load(CREDS)
    creds[slug] = {"token": key, "api": api}  # re-login replaces any disabled entry
    _save(CREDS, creds, 0o600)
    print(f"✓ Logged in to {slug} as {who.get('user', '?')}")
    return cmd_sync(quiet=False)


def cmd_status():
    creds, manifest = _load(CREDS), _load(MANIFEST)
    if not creds:
        print("runebox: not logged in — run /runebox:login")
        return 0
    for slug, cred in creds.items():
        arts = manifest.get(slug, {}).get("artifacts", {})
        kinds = {}
        for a in arts.values():
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        inventory = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "nothing installed"
        state = "REVOKED" if cred.get("disabled") else "ok"
        print(f"{slug} [{state}]  key {cred['token'][:8]}…  —  {inventory}")
    return 0


def cmd_logout(remove):
    manifest = _load(MANIFEST)
    if remove:
        for org in manifest.values():
            for a in org.get("artifacts", {}).values():
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
    op = sub.add_parser("logout")
    op.add_argument("--remove", action="store_true")
    a = p.parse_args()
    if a.cmd == "login":
        return cmd_login(a.api)
    if a.cmd == "sync":
        try:
            return cmd_sync(a.quiet)
        except Exception:
            return 0 if a.quiet else 1  # the hook must never block a session
    if a.cmd == "status":
        return cmd_status()
    return cmd_logout(a.remove)


if __name__ == "__main__":
    sys.exit(main())
