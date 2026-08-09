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

from daimon.brokers.dbus.broker import DBusBroker
from daimon.common.logging import get_logger

# Groesser als jeder ehrliche Auftrag und klein genug, dass niemand hier
# Speicher fuellt.
MAX_BYTES = 64 * 1024
HUB_SOCKET = "hub.sock"


def katalog_lesen(pfad: Path) -> dict:
    import yaml

    with open(pfad, encoding="utf-8") as fh:
        roh = yaml.safe_load(fh) or {}
    return {e["id"]: e for e in (roh.get("actions") or []) if e.get("id")}


def ticket_beim_hub_einloesen(hub_pfad: Path, ticket: str,
                              timeout_s: float = 5.0) -> None:
    """Wirft, wenn der Hub nicht einloest. Kein Rueckfall auf "dann eben ohne"."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(timeout_s)
        c.connect(str(hub_pfad))
        c.sendall(json.dumps(
            {"v": 1, "art": "ticket_einloesen", "ticket": ticket}).encode()
            + b"\n")
        antwort = c.recv(4096).decode("utf-8", "replace").strip()
    try:
        daten = json.loads(antwort)
    except ValueError as exc:
        raise RuntimeError(f"Hub-Antwort unlesbar: {antwort[:120]}") from exc
    if not daten.get("ok"):
        raise RuntimeError(str(daten.get("grund") or "Hub hat abgelehnt"))


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
            conn, _ = srv.accept()
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
