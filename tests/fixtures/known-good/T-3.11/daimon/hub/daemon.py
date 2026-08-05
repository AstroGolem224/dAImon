"""Minimales laufendes Gut-Muster des T-3.11-Ticket-Endpunkts."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import socket
import threading
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runtime-dir", type=Path, required=True)
    a = p.parse_args()
    a.runtime_dir.mkdir(parents=True, exist_ok=True)
    pfad = a.runtime_dir / "ticket.sock"
    pfad.unlink(missing_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(pfad)); pfad.chmod(0o600); s.listen(32)
    tickets: dict[str, dict] = {}
    lock = threading.Lock()
    while True:
        c, _ = s.accept()
        with c:
            try:
                req = json.loads(c.makefile("rb").readline(1 << 20))
                with lock:
                    if req.get("art") == "ausgeben" and req.get("zweck") == "api" and req.get("auftrag_hash"):
                        tid = secrets.token_hex(16)
                        tickets[tid] = {"hash": req["auftrag_hash"], "bis": time.monotonic() + 300, "verbraucht": False}
                        ans = {"v": 1, "ok": True, "ticket": tid, "frist_s": 300}
                    elif req.get("art") == "einloesen":
                        t = tickets.get(req.get("ticket"))
                        ok = bool(t and not t["verbraucht"] and time.monotonic() < t["bis"] and t["hash"] == req.get("auftrag_hash"))
                        if t:
                            t["verbraucht"] = True
                        ans = {"v": 1, "ok": ok}
                    else:
                        ans = {"v": 1, "ok": False}
            except Exception:
                ans = {"v": 1, "ok": False}
            c.sendall(json.dumps(ans, separators=(",", ":")).encode() + b"\n")


if __name__ == "__main__":
    raise SystemExit(main())
