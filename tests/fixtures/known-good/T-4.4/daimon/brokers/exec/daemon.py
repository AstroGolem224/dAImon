"""T-4.10 — der Dienstmantel um den Exec-Broker.

    python -m daimon.brokers.exec.daemon --socket <pfad> --katalog <core.yaml>

Die Freigaben werden beim START gelesen und mit ihrem sha256 gemerkt; vor
jedem Start rechnet der Broker erneut. Zwischen beidem liegt die Vorschau --
und `~/.local/share/applications` ist in dieser Zeit schreibbar.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from daimon.brokers import dienst
from daimon.brokers.exec.broker import ExecBroker
from daimon.common.logging import get_logger
from daimon.common.order import AuftragsFehler, pruefe

AUDIENCE = "exec"


def katalog_lesen(pfad: Path) -> dict:
    import yaml

    with open(pfad, encoding="utf-8") as fh:
        roh = yaml.safe_load(fh) or {}
    return {e["id"]: e for e in (roh.get("actions") or []) if e.get("id")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Exec-Broker (T-4.10)")
    ap.add_argument("--socket", required=True)
    ap.add_argument("--katalog", required=True, type=Path)
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    log = get_logger("daimon-exec")
    broker = ExecBroker.aus_katalog(katalog_lesen(args.katalog))
    if not broker.freigaben:
        # Kein `desktop_id` im Katalog: der Broker koennte nichts starten und
        # wuerde trotzdem laufen und `ok` melden. Lieber gar nicht starten.
        print("Keine freigegebene Anwendung im Katalog.", flush=True)
        return 1
    pfad = Path(args.socket)
    hub_pfad = Path(args.hub_socket or (pfad.parent / dienst.HUB_SOCKET))

    def verarbeite(roh: bytes) -> dict:
        try:
            auftrag = pruefe(roh, audience=AUDIENCE, jetzt=time.monotonic())
        except AuftragsFehler as fehler:
            return {"ok": False, "grund": "auftrag", "meldung": str(fehler)}
        kennung = str((auftrag.params or {}).get("desktop_id") or "")
        try:
            dienst.ticket_beim_hub_einloesen(hub_pfad, auftrag.ticket)
        except Exception as fehler:
            return {"ok": False, "grund": "ticket", "meldung": str(fehler)[:160]}
        return broker.starten(kennung)

    log.info("Exec-Broker bereit", DAIMON_ANZAHL=str(len(broker.freigaben)))
    return dienst.lauf(pfad, verarbeite, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
