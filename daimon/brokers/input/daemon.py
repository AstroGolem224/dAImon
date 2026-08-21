"""T-4.13 — der Dienstmantel um den Input-Broker. EINE Folge, dann Ende.

    python -m daimon.brokers.input.daemon --socket <pfad> --katalog <core.yaml>

`einmal=True`: nach dem ersten Auftrag laeuft die Schleife aus, der Socket
verschwindet und der Prozess endet. Zusammen mit `Type=oneshot` und
`RuntimeMaxSec=30` in der Unit ist das die One-shot-Zusage -- an drei
Stellen, weil eine davon ausfallen kann.

Die Allowlist kommt aus dem KATALOG, nicht aus der Anfrage
----------------------------------------------------------------------------
In welche Anwendungen getippt werden darf, entscheidet `config/actions/`,
nicht der Absender des Auftrags. Steht dort keine einzige, startet dieser
Dienst gar nicht erst: ein Input-Broker, der sich als bereit meldet und
jeden Auftrag abweist, ist ein Dienst, der `ok` ist und nichts kann -- und
er waere trotzdem ein Prozess mit Zugang zu `/dev/uinput`.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from daimon.brokers import dienst
from daimon.brokers.input import portal
from daimon.brokers.input.broker import InputBroker
from daimon.common.logging import get_logger
from daimon.common.order import AuftragsFehler, pruefe

AUDIENCE = "input"


def katalog_lesen(pfad: Path) -> dict:
    import yaml

    with open(pfad, encoding="utf-8") as fh:
        roh = yaml.safe_load(fh) or {}
    return {e["id"]: e for e in (roh.get("actions") or []) if e.get("id")}


def allowlist_aus_katalog(katalog: dict) -> frozenset:
    """Die `apps` der freigegebenen `input`-Aktionen. Sonst nichts."""
    erlaubt: set = set()
    for eintrag in katalog.values():
        if str(eintrag.get("audience") or "") != AUDIENCE:
            continue
        if str(eintrag.get("status") or "") != "approved":
            continue
        for wert in (eintrag.get("apps") or ()):
            if isinstance(wert, str) and wert:
                erlaubt.add(wert)
    return frozenset(erlaubt)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Input-Broker (T-4.13)")
    ap.add_argument("--socket", required=True)
    ap.add_argument("--katalog", required=True, type=Path)
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    log = get_logger("daimon-input")
    allowlist = allowlist_aus_katalog(katalog_lesen(args.katalog))
    if not allowlist:
        print("Keine Anwendung mit `audience: input` im Katalog.", flush=True)
        return 1

    # Der Rueckfall ist AUS, solange die Umgebung ihn nicht ausdruecklich
    # einschaltet. Die Begruendung steht in broker.py und in der Unit.
    erlaubt = os.environ.get("DAIMON_YDOTOOL", "0") == "1"
    broker = InputBroker(
        allowlist=allowlist,
        # Der Regelweg. Wird er nicht eingehaengt, synthetisiert der Broker
        # nichts -- er faellt nicht still auf ydotool zurueck.
        portal_sitzung=portal.sitzung_oeffnen,
        # Der harte Breaker. Kein Bus -> gesperrt.
        screensaver_aktiv=portal.screensaver_aktiv,
        audit=lambda **felder: log.info("Eingabefolge", **{
            f"DAIMON_{k.upper()}": str(v) for k, v in felder.items()}),
        ydotool_erlaubt=erlaubt)
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
        params = auftrag.params or {}
        return broker.ausfuehren(params.get("folge") or [],
                                 app=str(params.get("app") or ""))

    log.info("Input-Broker bereit (one-shot)",
             DAIMON_YDOTOOL=str(erlaubt), DAIMON_ANZAHL=str(len(allowlist)))
    return dienst.lauf(pfad, verarbeite, einmal=True, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
