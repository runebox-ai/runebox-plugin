"""Self-check for sync.py against a stub registry server (stdlib only).

Covers: login+first install (skill folder + agent file at correct paths), etag 304 no-op,
version-bump update, yank -> delete only our files (user's foreign file survives),
401 -> revoked-once + disabled, and server-sent traversal paths rejected.

Run:  python3 test_sync.py
"""
import io
import json
import os
import tempfile
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="runebox-test-")
os.environ["RUNEBOX_DIR"] = TMP + "/.runebox"
os.environ["CLAUDE_DIR"] = TMP + "/.claude"

import sync  # noqa: E402  (env must be set before import — module reads it at load)

STATE = {
    "etag": "v1",
    "revoked": False,
    "catalog_hits": 0,
    "artifacts": {
        "a1": {
            "id": "a1", "slug": "deploy-runbook", "kind": "skill", "version": 1,
            "install": {"root": "claude", "path": "skills/deploy-runbook"},
            "content": {"slug": "deploy-runbook", "kind": "skill", "version": 1,
                        "skill_md": "# Deploy v1", "files": [{"path": "helpers/x.py", "content": "print(1)"}]},
        },
        "a2": {
            "id": "a2", "slug": "reviewer", "kind": "agent", "version": 1,
            "install": {"root": "claude", "path": "agents/reviewer.md"},
            "content": {"slug": "reviewer", "kind": "agent", "version": 1,
                        "skill_md": "# Reviewer v1", "files": []},
        },
    },
}


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
        if self.path == "/api/registry/whoami":
            self._json(200, {"org": "acme", "user": "eng", "key_name": "laptop"})
        elif self.path == "/api/registry/catalog":
            STATE["catalog_hits"] += 1
            if self.headers.get("If-None-Match") == STATE["etag"]:
                self.send_response(304)
                self.end_headers()
                return
            items = [{k: a[k] for k in ("id", "slug", "kind", "version", "install")}
                     for a in STATE["artifacts"].values()]
            self._json(200, {"artifacts": items}, etag=STATE["etag"])
        elif self.path.startswith("/api/registry/artifacts/"):
            aid = self.path.rsplit("/", 1)[1]
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
    claude = Path(os.environ["CLAUDE_DIR"])

    # 1. login + first sync installs everything at the right drop-in paths
    import sys
    sys.stdin = io.StringIO("rbx_testkey123")
    code, out = run(sync.cmd_login, api)
    assert code == 0, out
    assert "Logged in to acme" in out, out
    assert (claude / "skills/deploy-runbook/SKILL.md").read_text() == "# Deploy v1"
    assert (claude / "skills/deploy-runbook/helpers/x.py").read_text() == "print(1)"
    assert (claude / "agents/reviewer.md").read_text() == "# Reviewer v1"
    print("  ok  login installs skill folder + agent file")

    # 2. unchanged etag -> 304 fast path, no output, no content re-fetch
    hits = STATE["catalog_hits"]
    code, out = run(sync.cmd_sync, True)
    assert code == 0 and out == "", out
    assert STATE["catalog_hits"] == hits + 1  # one conditional GET, nothing else
    print("  ok  304 no-op on unchanged catalog")

    # 3. version bump -> update in place
    STATE["artifacts"]["a2"]["version"] = 2
    STATE["artifacts"]["a2"]["content"] = {**STATE["artifacts"]["a2"]["content"],
                                           "version": 2, "skill_md": "# Reviewer v2"}
    STATE["etag"] = "v2"
    code, out = run(sync.cmd_sync, False)
    assert "1 updated" in out, out
    assert (claude / "agents/reviewer.md").read_text() == "# Reviewer v2"
    print("  ok  version bump updates the installed file")

    # 4. yank -> delete only what we installed; the user's own file survives
    foreign = claude / "agents/my-own-agent.md"
    foreign.write_text("mine")
    del STATE["artifacts"]["a2"]
    STATE["etag"] = "v3"
    code, out = run(sync.cmd_sync, False)
    assert "1 removed" in out, out
    assert not (claude / "agents/reviewer.md").exists()
    assert foreign.read_text() == "mine"
    print("  ok  yank deletes ours, never the user's files")

    # 5. traversal from a hostile server is rejected; other artifacts still sync
    STATE["artifacts"]["evil"] = {
        "id": "evil", "slug": "evil", "kind": "agent", "version": 1,
        "install": {"root": "claude", "path": "../outside.md"},
        "content": {"slug": "evil", "kind": "agent", "version": 1, "skill_md": "x", "files": []},
    }
    STATE["etag"] = "v4"
    code, out = run(sync.cmd_sync, False)
    assert "sync failed" in out and "unsafe install path" in out, out
    assert not (Path(TMP) / "outside.md").exists()
    del STATE["artifacts"]["evil"]
    print("  ok  hostile install path rejected client-side")

    # 6. revoked key -> notice once, then silent until re-login
    STATE["revoked"] = True
    code, out = run(sync.cmd_sync, True)
    assert "revoked" in out, out
    code, out = run(sync.cmd_sync, True)
    assert out == "", out  # disabled flag persisted — no repeat nagging
    print("  ok  401 notifies once then disables the org")

    print("\n6 passed")


if __name__ == "__main__":
    main()
