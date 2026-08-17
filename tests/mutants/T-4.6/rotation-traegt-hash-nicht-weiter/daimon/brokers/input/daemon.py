"""T-4.13 — der Dienstmantel um den Input-Broker. EINE Folge, dann Ende.

    python -m daimon.brokers.input.daemon --socket <pfad>

`einmal=True`: nach dem ersten Auftrag laeuft die Schleife aus, der Socket
verschwindet und der Prozess endet. Zusammen mit `Type=oneshot` und
`RuntimeMaxSec=30` in der Unit ist das die One-shot-Zusage -- an drei
Stellen, weil eine davon ausfallen kann.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from daimon.brokers import dienst
from daimon.brokers.input.broker import InputBroker
from daimon.common.logging import get_logger
from daimon.common.order import AuftragsFehler, pruefe

AUDIENCE = "input"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Input-Broker (T-4.13)")
    ap.add_argument("--socket", required=True)
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    log = get_logger("daimon-input")
    # Der Rueckfall ist AUS, solange die Umgebung ihn nicht ausdruecklich
    # einschaltet. Die Begruendung steht in broker.py und in der Unit.
    erlaubt = os.environ.get("DAIMON_YDOTOOL", "0") == "1"
    broker = InputBroker(ydotool_erlaubt=erlaubt)
    pfad = Path(args.socket)
    hub_pfad = Path(args.hub_socket or (pfad.parent / dienst.HUB_SOCKET))

    def verarbeite(roh: bytes) -> dict:
        try:
            auftrag = pruefe(roh, audience=AUDIENCE, jetzt=time.monotonic())
        except AuftragsFehler as fehler:
            return {"ok": False, "grund": "auftrag", "meldung": str(fehler)}
        try:
            dienst.ticket_beim_hub_einloesen(hub_pfad, auftrag.ticket)
        except Exception as fehler:
            return {"ok": False, "grund": "ticket", "meldung": str(fehler)[:160]}
        return broker.ausfuehren((auftrag.params or {}).get("folge") or [])

    log.info("Input-Broker bereit (one-shot)",
             DAIMON_YDOTOOL=str(erlaubt))
    return dienst.lauf(pfad, verarbeite, einmal=True, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
