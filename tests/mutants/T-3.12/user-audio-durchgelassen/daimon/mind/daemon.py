"""Gut-Muster T-3.12: Mind-Daemon mit `art: "frage"` und `art: "zustand"`.

Eine Zeile JSON rein, eine raus, auf `$XDG_RUNTIME_DIR/daimon/mind.sock`.
Das Routing selbst steht in `daimon/mind/router.py`; hier ist nur der
Socket und die Protokollhülle.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from daimon.mind.router import Router

MAX = 4 << 20


def jline(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def bearbeite(router: Router, zeile: bytes) -> bytes:
    try:
        req = json.loads(zeile)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jline({"v": 1, "ok": False, "grund": "unlesbar",
                      "meldung": "Keine lesbare JSON-Zeile."})
    if not isinstance(req, dict):
        return jline({"v": 1, "ok": False, "grund": "unlesbar",
                      "meldung": "Die JSON-Wurzel ist kein Objekt."})
    art = req.get("art")
    if art == "zustand":
        return jline(router.zustand())
    if art == "frage":
        return jline(router.frage(req))
    return jline({"v": 1, "ok": False, "grund": "unbekannte_art",
                  "meldung": "art ist weder frage noch zustand."})


def main() -> int:
    laufzeit = Path(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    ) / "daimon"
    laufzeit.mkdir(parents=True, exist_ok=True)
    os.chmod(laufzeit, 0o700)
    pfad = laufzeit / "mind.sock"
    pfad.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    os.chmod(pfad, 0o600)
    srv.listen(8)
    router = Router()
    while True:
        conn, _ = srv.accept()
        with conn:
            conn.settimeout(30)
            try:
                zeile = conn.makefile("rb").readline(MAX)
                conn.sendall(bearbeite(router, zeile))
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
