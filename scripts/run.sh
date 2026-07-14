#!/bin/sh
# Runs the stdlib-only sync client with whatever Python 3 this machine has.
# Hooks guarantee no tools beyond the shell (not even python3 — the Windows
# Store ships a fake `python3` stub), so every entry point routes through
# this picker instead of assuming an interpreter name.
# ponytail: bridge only — the zero-dependency plugin is Phase 11.
SCRIPT="$(dirname "$0")/sync.py"
for CMD in "python3" "python" "py -3"; do
  # A real interpreter passes; the Store stub and Python 2 both fail this.
  if $CMD -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
    exec $CMD "$SCRIPT" "$@"
  fi
done
echo "runebox: Python 3 not found — install it (https://python.org, brew install python3, or winget install Python.Python.3.12) and re-run." >&2
exit 1
