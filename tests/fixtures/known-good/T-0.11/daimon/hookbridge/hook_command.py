"""Nicht blockierender Claude-Hook: Nutzlast vorbereiten und voll abkoppeln."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import urllib.request


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    try:
        payload["pid"] = int(os.environ["DAIMON_CLAUDE_PID"])
    except (KeyError, ValueError):
        payload["pid"] = os.getppid()

    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "daimon"
    try:
        token = (runtime / "hook-token").read_text(encoding="ascii").strip()
    except OSError:
        return 0
    port = int(os.environ.get("DAIMON_HOOK_PORT", "8787"))
    body = json.dumps(payload, separators=(",", ":")).encode()

    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        return 0
    os.setsid()
    pid = os.fork()
    if pid:
        os._exit(0)
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/hook", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=1).close()
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
