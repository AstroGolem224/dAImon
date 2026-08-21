"""T-4.9 — der Dienstmantel um den FS-Broker.

    python -m daimon.brokers.fs.daemon --socket <pfad> [--wurzel <dir> ...]

Die Wurzeln kommen aus dem AUFRUF, nicht aus dem Auftrag. Ein Auftrag, der
sein eigenes Arbeitsverzeichnis benennt, waere ein Auftrag ohne Schranke --
und `RESOLVE_BENEATH` haette nichts, unterhalb dessen es aufloest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from daimon.brokers import dienst
from daimon.brokers.fs import broker as fs
from daimon.common.logging import get_logger
from daimon.common.order import AuftragsFehler, pruefe

AUDIENCE = "fs"
# Was der Broker ueberhaupt kann. Ein `loeschen` fehlt absichtlich: es
# entstuende ueber T-4.8 (Papierkorb) und braucht ein verifiziertes Artefakt,
# also einen Aufrufer, der beides kennt. Solange den niemand gebaut hat, ist
# eine Loeschoperation hier eine Faehigkeit ohne Schranke.
OPERATIONEN = ("fs.file.read", "fs.file.write")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon FS-Broker (T-4.9)")
    ap.add_argument("--socket", required=True)
    ap.add_argument("--wurzel", action="append", default=[],
                    help="erlaubtes Arbeitsverzeichnis; mehrfach angebbar")
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    if not fs.verfuegbar():
        # Kein Rueckfall auf os.open -- siehe broker.py. Lieber gar nicht
        # starten als ohne die Zusage laufen.
        print("openat2 fehlt; der FS-Broker startet nicht.", flush=True)
        return 1

    wurzeln = [Path(w).resolve() for w in args.wurzel] or [Path.home() / "Dokumente"]
    log = get_logger("daimon-fs")
    pfad = Path(args.socket)
    hub_pfad = Path(args.hub_socket or (pfad.parent / dienst.HUB_SOCKET))

    def verarbeite(roh: bytes) -> dict:
        try:
            auftrag = pruefe(roh, audience=AUDIENCE, jetzt=__import__("time").monotonic())
        except AuftragsFehler as fehler:
            return {"ok": False, "grund": "auftrag", "meldung": str(fehler)}
        if auftrag.action_id not in OPERATIONEN:
            return {"ok": False, "grund": "keine_operation",
                    "meldung": f"{auftrag.action_id} gibt es hier nicht"}
        relativ = str((auftrag.params or {}).get("pfad") or "")
        schreibend = auftrag.action_id == "fs.file.write"
        # Aufgeloest wird VOR der Ticket-Einloesung, nicht danach: sonst
        # laege genau zwischen Genehmigung und Mutation eine Aufloesung, und
        # das ist der Riss aus Befund T-4.9 K2. Jede der `--wurzel`-Angaben
        # zaehlt, nicht nur die erste (derselbe Befund, K6) -- die naechste
        # wird erst versucht, wenn die vorige unterhalb ihrer Wurzel nichts
        # findet.
        griff = None
        letzter_fehler: OSError | None = None
        for wurzel in wurzeln:
            try:
                griff = fs.aufloesen(wurzel, relativ, schreibend=schreibend)
                break
            except OSError as fehler:
                letzter_fehler = fehler
        if griff is None:
            return {"ok": False, "grund": "fs",
                    "meldung": str(letzter_fehler or "kein Ziel")[:160]}
        try:
            try:
                dienst.ticket_beim_hub_einloesen(hub_pfad, auftrag.ticket)
            except Exception as fehler:
                return {"ok": False, "grund": "ticket", "meldung": str(fehler)[:160]}
            try:
                if auftrag.action_id == "fs.file.read":
                    inhalt = fs.lesen(griff, 64 * 1024)
                    return {"ok": True, "bytes": len(inhalt)}
                geschrieben = fs.schreiben(
                    griff, str((auftrag.params or {}).get("inhalt") or "").encode())
                return {"ok": True, "bytes": geschrieben}
            except OSError as fehler:
                return {"ok": False, "grund": "fs", "meldung": str(fehler)[:160]}
        finally:
            griff.schliessen()

    log.info("FS-Broker bereit", DAIMON_WURZELN=",".join(str(w) for w in wurzeln))
    return dienst.lauf(pfad, verarbeite, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
