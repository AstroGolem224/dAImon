"""T-4.7 — der Dienst um den DBus-Broker herum.

    python -m daimon.brokers.dbus.daemon --socket <pfad> --katalog <core.yaml>

Der Broker entscheidet nichts. Er nimmt einen kanonischen Auftrag entgegen,
prueft ihn (Zielgruppe, Frist, Hash, Serialisierung), laesst das Ticket **vom
Hub** einloesen und ruft dann genau eine feste Operation auf. Faellt der Hub
aus, wird nichts ausgefuehrt: ein Broker, der bei unerreichbarem Hub selbst
entscheidet, waere die zweite Wahrheit, die dieses Projekt an keiner Stelle
haben will.

Ein Auftrag je Verbindung. Kein Rahmenprotokoll, keine Warteschlange: die
Verbindung IST die Klammer, und ein Broker mit eigener Warteschlange haette
einen Zustand, den der Hub nicht sieht.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

from daimon.brokers import dienst
# EINE Fassung: `ticket_beim_hub_einloesen` stand hier bis zum 19.08. ein
# zweites Mal, Wort fuer Wort wie im Mantel -- gefunden beim Verdrahten der
# Hub-Allowlisten. Haette jemand Zeitlimit oder Fehlerbehandlung an einer
# Stelle geaendert, waere die Einmaligkeit der Auftragstickets je nach
# Broker verschieden gewesen. Sie ist eine Aussage ueber ALLE zusammen.
from daimon.brokers.dienst import ticket_beim_hub_einloesen
from daimon.brokers.dbus.broker import DBusBroker
from daimon.common import ipc
from daimon.common.logging import get_logger

# Groesser als jeder ehrliche Auftrag und klein genug, dass niemand hier
# Speicher fuellt.
MAX_BYTES = 64 * 1024
# Siehe daimon/brokers/dienst.py: die Auftragstickets liegen an
# `aktion.sock`, nicht am Kontingent-Socket aus T-3.11.
HUB_SOCKET = dienst.HUB_SOCKET


def katalog_lesen(pfad: Path) -> dict:
    import yaml

    with open(pfad, encoding="utf-8") as fh:
        roh = yaml.safe_load(fh) or {}
    return {e["id"]: e for e in (roh.get("actions") or []) if e.get("id")}


def bediene(broker: DBusBroker, conn: socket.socket, hub_pfad: Path,
            log) -> None:
    conn.settimeout(10.0)
    stuecke, gesamt = [], 0
    while True:
        stueck = conn.recv(4096)
        if not stueck:
            break
        gesamt += len(stueck)
        if gesamt > MAX_BYTES:
            conn.sendall(b'{"ok":false,"grund":"zu_gross"}\n')
            return
        stuecke.append(stueck)
        if stueck.endswith(b"\n"):
            break
    roh = b"".join(stuecke).strip()

    ergebnis = broker.ausfuehren(
        roh, jetzt=time.monotonic(),
        ticket_einloesen=lambda t: ticket_beim_hub_einloesen(hub_pfad, t))
    log.info("Auftrag bearbeitet", DAIMON_ACTION="dbus_broker",
             DAIMON_OK=str(ergebnis.get("ok")),
             DAIMON_GRUND=str(ergebnis.get("grund"))[:60])
    conn.sendall(json.dumps(ergebnis, ensure_ascii=False).encode() + b"\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon DBus-Broker (T-4.7)")
    ap.add_argument("--socket", required=True)
    ap.add_argument("--katalog", required=True, type=Path)
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    log = get_logger("daimon-dbus")
    broker = DBusBroker.aus_katalog(katalog_lesen(args.katalog))
    if not broker.operationen:
        # Ein Broker ohne Operationen kann nichts ausfuehren und wuerde
        # trotzdem laufen und `ok` melden. Lieber gar nicht starten.
        print("Keine genehmigte Aktion mit DBus-Ursprung im Katalog.",
              file=sys.stderr)
        return 1
    log.info("Operationstabelle gebaut", DAIMON_ACTION="dbus_broker_start",
             DAIMON_ANZAHL=str(len(broker.operationen)))

    hub_pfad = Path(args.hub_socket or (Path(args.socket).parent / HUB_SOCKET))
    pfad = Path(args.socket)
    if pfad.exists():
        pfad.unlink()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    alt = os.umask(0o177)  # 0600 schon bei der Erzeugung
    try:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
    finally:
        os.umask(alt)
    srv.listen(8)
    print(f"READY pid={os.getpid()} operationen={len(broker.operationen)}")

    try:
        while True:
            # DIESELBE Annahme wie im Mantel -- T-4.5 K6. Dieser Broker hat
            # eine eigene Schleife (historisch), und genau deshalb steht die
            # Peer-Pruefung NICHT hier, sondern in `dienst.annehmen`: zwei
            # Fassungen einer Regel sind eine Regel und eine Attrappe.
            try:
                conn, _ = dienst.annehmen(srv, pfad.name, log=log)
            except ipc.PeerError:
                continue
            with conn:
                try:
                    bediene(broker, conn, hub_pfad, log)
                except OSError as fehler:
                    log.warn("Verbindung abgebrochen",
                             DAIMON_GRUND=str(fehler)[:80])
    except KeyboardInterrupt:
        return 0
    finally:
        srv.close()
        pfad.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
