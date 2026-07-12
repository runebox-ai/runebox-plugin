"""Self-check for sync.py against a stub registry server (stdlib only).

Covers: multi-harness install to distinct roots (skill -> every harness, agent -> claude
only) from one harness-aware catalog stub, login auto-detection, etag 304 no-op per harness,
version-bump update, yank -> delete only our files (user's foreign file survives, re-checked
per root), v1->v2 manifest migration, `harness disable` uninstalling just that harness, a
hostile server-sent traversal path/root rejected, and 401 -> revoked-once + disabled.

Run:  python3 test_sync.py
"""
import io
import json
import os
import tempfile
import threading
import urllib.parse
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="runebox-test-")
os.environ["RUNEBOX_DIR"] = TMP + "/.runebox"
os.environ["CLAUDE_DIR"] = TMP + "/.claude"
os.environ["CODEX_DIR"] = TMP + "/.codex"
os.environ["CURSOR_DIR"] = TMP + "/.cursor"
os.environ["GEMINI_DIR"] = TMP + "/.gemini"

import sync  # noqa: E402  (env must be set before import — module reads it at load)

# Mirrors skill_utils.REGISTRY_INSTALL (docs/10-multi-harness/design.md): skill ships to every
# harness; agent is claude-only. Enough of the real matrix to exercise the client's harness
# fan-out without duplicating the whole server-side table.
INSTALL_MATRIX = {
    ("skill", "claude"): {"root": "claude", "path": "skills/{slug}"},
    ("skill", "codex"): {"root": "codex", "path": "skills/{slug}"},
    ("skill", "cursor"): {"root": "cursor", "path": "skills/{slug}"},
    ("skill", "gemini"): {"root": "gemini", "path": "skills/{slug}"},
    ("agent", "claude"): {"root": "claude", "path": "agents/{slug}.md"},
}

STATE = {
    "etag": "v1",
    "revoked": False,
    "catalog_hits": 0,
    "artifacts": {
        "a1": {
            "id": "a1", "slug": "deploy-runbook", "kind": "skill", "version": 1,
            "content": {"slug": "deploy-runbook", "kind": "skill", "version": 1,
                        "skill_md": "# Deploy v1", "files": [{"path": "helpers/x.py", "content": "print(1)"}]},
        },
        "a2": {
            "id": "a2", "slug": "reviewer", "kind": "agent", "version": 1,
            "content": {"slug": "reviewer", "kind": "agent", "version": 1,
                        "skill_md": "# Reviewer v1", "files": []},
        },
    },
}


def _install_for(harness, art):
    m = INSTALL_MATRIX.get((art["kind"], harness))
    if not m:
        return None
    return {"root": m["root"], "path": m["path"].format(slug=art["slug"])}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body, etag=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if STATE["revoked"]:
            self.send_response(401)
            self.end_headers()
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/api/registry/whoami":
            self._json(200, {"org": "acme", "user": "eng", "key_name": "laptop"})
        elif path == "/api/registry/catalog":
            STATE["catalog_hits"] += 1
            harness = urllib.parse.parse_qs(parsed.query).get("harness", ["claude"])[0]
            if self.headers.get("If-None-Match") == STATE["etag"]:
                self.send_response(304)
                self.end_headers()
                return
            items = []
            for a in STATE["artifacts"].values():
                install = _install_for(harness, a)
                if not install:
                    continue  # this harness can't use this kind — omitted, same as the server
                items.append({"id": a["id"], "slug": a["slug"], "kind": a["kind"],
                              "version": a["version"], "install": install})
            self._json(200, {"artifacts": items}, etag=STATE["etag"])
        elif path.startswith("/api/registry/artifacts/"):
            aid = path.rsplit("/", 1)[1]
            self._json(200, STATE["artifacts"][aid]["content"])
        else:
            self.send_response(404)
            self.end_headers()


def run(fn, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = fn(*args)
    return code, out.getvalue()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{server.server_address[1]}"
    claude, codex, cursor, gemini = (Path(os.environ[v]) for v in
                                      ("CLAUDE_DIR", "CODEX_DIR", "CURSOR_DIR", "GEMINI_DIR"))
    # Pre-create claude + codex so login's directory-existence auto-detect finds them; cursor
    # and gemini stay absent so they're neither detected nor synced.
    claude.mkdir(parents=True)
    codex.mkdir(parents=True)

    import sys
    sys.stdin = io.StringIO("rbx_testkey123")
    code, out = run(sync.cmd_login, api)
    assert code == 0, out
    assert "Logged in to acme" in out, out
    assert "detected: claude, codex" in out, out
    print("  ok  login auto-detects harnesses present on disk")

    # 1. first sync installs the skill to every detected harness, the agent to claude only
    assert (claude / "skills/deploy-runbook/SKILL.md").read_text() == "# Deploy v1"
    assert (claude / "skills/deploy-runbook/helpers/x.py").read_text() == "print(1)"
    assert (codex / "skills/deploy-runbook/SKILL.md").read_text() == "# Deploy v1"
    assert (claude / "agents/reviewer.md").read_text() == "# Reviewer v1"
    assert not (codex / "agents/reviewer.md").exists()  # codex can't use the agent kind
    assert not cursor.exists() and not gemini.exists()  # never detected, never touched
    print("  ok  multi-harness install: skill fans out, agent stays claude-only")

    # 2. unchanged etag -> 304 fast path per harness, no output, no content re-fetch
    hits = STATE["catalog_hits"]
    code, out = run(sync.cmd_sync, True)
    assert code == 0 and out == "", out
    assert STATE["catalog_hits"] == hits + 2  # one conditional GET per enabled harness (claude, codex)
    print("  ok  304 no-op per harness on an unchanged catalog")

    # 3. version bump -> updates in place, on every harness that has it installed
    STATE["artifacts"]["a1"]["version"] = 2
    STATE["artifacts"]["a1"]["content"] = {**STATE["artifacts"]["a1"]["content"], "version": 2, "skill_md": "# Deploy v2"}
    STATE["etag"] = "v2"
    code, out = run(sync.cmd_sync, False)
    assert out.count("1 updated") == 2, out  # claude AND codex both had the skill installed
    assert (claude / "skills/deploy-runbook/SKILL.md").read_text() == "# Deploy v2"
    assert (codex / "skills/deploy-runbook/SKILL.md").read_text() == "# Deploy v2"
    print("  ok  version bump updates the installed file on every harness that has it")

    # 4. yank -> delete only what we installed, on every root; the user's own files survive
    foreign_claude = claude / "agents/my-own-agent.md"
    foreign_claude.write_text("mine")
    foreign_codex = codex / "skills/not-ours/SKILL.md"
    foreign_codex.parent.mkdir(parents=True)
    foreign_codex.write_text("mine too")
    del STATE["artifacts"]["a2"]  # yank the agent
    STATE["etag"] = "v3"
    code, out = run(sync.cmd_sync, False)
    assert "1 removed" in out, out
    assert not (claude / "agents/reviewer.md").exists()
    assert foreign_claude.read_text() == "mine"
    assert foreign_codex.read_text() == "mine too"
    print("  ok  yank deletes ours, never the user's files, on any root")

    # 5. v1 -> v2 manifest migration is silent and idempotent
    legacy = {"beta": {"etag": "old", "artifacts": {"x1": {"version": 1, "slug": "x", "kind": "skill", "paths": []}}}}
    migrated = sync._migrate_manifest(dict(legacy))
    assert migrated == {"beta": {"claude": {"etag": "old", "artifacts": legacy["beta"]["artifacts"]}}}, migrated
    assert sync._migrate_manifest(migrated) == migrated, "migrating twice must be a no-op"
    print("  ok  v1 manifest ({org: {etag, artifacts}}) migrates under 'claude', idempotently")

    # 6. `harness disable` uninstalls only that harness's files and stops syncing it
    STATE["artifacts"]["a2"] = {  # re-publish the agent so codex still only ever gets the skill
        "id": "a2", "slug": "reviewer", "kind": "agent", "version": 1,
        "content": {"slug": "reviewer", "kind": "agent", "version": 1, "skill_md": "# Reviewer v1", "files": []},
    }
    STATE["etag"] = "v4"
    run(sync.cmd_sync, False)
    assert (codex / "skills/deploy-runbook").exists()
    code, out = run(sync.cmd_harness, "disable", "codex")
    assert code == 0, out
    assert not (codex / "skills/deploy-runbook").exists(), "disabling codex must remove its files"
    assert claude.exists() and (claude / "skills/deploy-runbook").exists(), "claude untouched"
    STATE["etag"] = "v5"
    hits = STATE["catalog_hits"]
    run(sync.cmd_sync, True)
    assert STATE["catalog_hits"] == hits + 1, "disabled harness must not be synced anymore"
    print("  ok  harness disable removes only that harness's files and stops syncing it")

    # 7. traversal from a hostile server is rejected on any root, including an unknown one
    STATE["artifacts"]["evil"] = {
        "id": "evil", "slug": "evil", "kind": "agent", "version": 1,
        "content": {"slug": "evil", "kind": "agent", "version": 1, "skill_md": "x", "files": []},
    }
    orig_install_for = _install_for
    globals()["_install_for"] = lambda harness, a: (
        {"root": "claude", "path": "../outside.md"} if a["id"] == "evil" else orig_install_for(harness, a)
    )
    STATE["etag"] = "v6"
    code, out = run(sync.cmd_sync, False)
    assert "sync failed" in out and "unsafe install path" in out, out
    assert not (Path(TMP) / "outside.md").exists()
    globals()["_install_for"] = lambda harness, a: (
        {"root": "windsurf", "path": "x.md"} if a["id"] == "evil" else orig_install_for(harness, a)
    )
    STATE["etag"] = "v7"
    code, out = run(sync.cmd_sync, False)
    assert "sync failed" in out and "unsafe install root" in out, out
    globals()["_install_for"] = orig_install_for
    del STATE["artifacts"]["evil"]
    print("  ok  hostile install path/root rejected client-side")

    # 8. revoked key -> notice once, then silent until re-login
    STATE["revoked"] = True
    code, out = run(sync.cmd_sync, True)
    assert "revoked" in out, out
    code, out = run(sync.cmd_sync, True)
    assert out == "", out  # disabled flag persisted — no repeat nagging
    print("  ok  401 notifies once then disables the org")

    # 9. autosync prints the snippet, and --apply appends it once (idempotently) to the profile
    os.environ["SHELL"] = "/bin/zsh"
    os.environ["HOME"] = TMP + "/home"
    Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
    code, out = run(sync.cmd_autosync, False)
    assert code == 0 and sync.AUTOSYNC_SNIPPET in out and "--apply" in out, out
    profile = Path(os.environ["HOME"]) / ".zshrc"
    code, out = run(sync.cmd_autosync, True)
    assert code == 0 and profile.read_text().count(sync.AUTOSYNC_SNIPPET) == 1, profile.read_text()
    code, out = run(sync.cmd_autosync, True)  # re-running --apply must not duplicate the line
    assert "already present" in out, out
    assert profile.read_text().count(sync.AUTOSYNC_SNIPPET) == 1
    print("  ok  autosync prints the snippet and --apply appends it exactly once")

    print("\n9 passed")


if __name__ == "__main__":
    main()
