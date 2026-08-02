"""T-0.11 — Einstiegspunkt der Hook-Bridge.

    python -m daimon.hookbridge.daemon --runtime-dir <dir> [--port N] [--audit-log <datei>]

Getrennt von `bridge.py`, damit das Modul importierbar bleibt, ohne einen Port
zu belegen. Ein Test, der `Bridge` benutzen will, soll nicht nebenbei einen
Server starten.

`--audit-log` schreibt zusaetzlich zum Journal in eine Datei. Das Journal ist
der richtige Ort im Betrieb, aber ein Verifizierer, der in einem
Netz-Namespace oder einem Container laeuft, kommt nicht zuverlaessig daran --
und eine Sicherheitszusage, die sich nicht nachpruefen laesst, ist keine.
"""

from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

from daimon.common.logging import get_logger
from daimon.hookbridge.bridge import HOST, PORT, Bridge


def main() -> int:
    ap = argparse.ArgumentParser(description="dAImon Hook-Bridge")
    ap.add_argument("--runtime-dir", type=Path, required=True)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--audit-log", type=Path, default=None)
    args = ap.parse_args()

    log = get_logger("daimon-hookbridge")
    bridge = Bridge(args.runtime_dir, host=args.host, port=args.port,
                    log=log, audit_log=args.audit_log)
    bridge.start()

    halt = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: halt.set())
    try:
        halt.wait()
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
