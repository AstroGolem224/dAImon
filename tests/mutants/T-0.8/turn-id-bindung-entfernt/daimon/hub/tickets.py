"""Gut-Muster fuer T-0.8: das Broker-Ticketbuch.

Persistent, weil ein Absturz zwischen Ausgabe und Ausfuehrung sonst eine
Wiedereinloesung waere. Atomar, weil ein halb geschriebenes Buch schlimmer
ist als gar keins.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

from daimon.hub.marks import MarkenFehler


class _Stumm:
    def _nichts(self, *args, **kwargs):
        return None

    info = warning = error = debug = _nichts


class Ticketbuch:
    def __init__(self, pfad: Path, *, frist_s: float = 300.0,
                 jetzt=time.monotonic, log=None) -> None:
        self._pfad = Path(pfad)
        self._frist_s = float(frist_s)
        self._jetzt = jetzt
        self._log = log or _Stumm()
        self._sperre = threading.Lock()
        self._buch = self._laden()

    # -- Persistenz --------------------------------------------------------

    def _laden(self) -> dict:
        try:
            roh = json.loads(self._pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"offen": {}, "verbraucht": []}
        if not isinstance(roh, dict):
            return {"offen": {}, "verbraucht": []}
        offen = roh.get("offen")
        verbraucht = roh.get("verbraucht")
        return {
            "offen": offen if isinstance(offen, dict) else {},
            "verbraucht": verbraucht if isinstance(verbraucht, list) else [],
        }

    def _schreiben(self) -> None:
        """Temporaerdatei im selben Verzeichnis, fsync, dann os.replace.
        json.dump direkt auf die Zieldatei waere der Fehler: ein Absturz
        mitten drin liesse ein halbes Buch zurueck."""
        self._pfad.parent.mkdir(parents=True, exist_ok=True)
        temp = self._pfad.with_suffix(self._pfad.suffix + f".tmp{os.getpid()}")
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(self._buch, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, self._pfad)

    # -- Automat -----------------------------------------------------------

    def ausgeben(self, *, auftrag_hash: str) -> str:
        ticket_id = secrets.token_hex(16)
        with self._sperre:
            self._buch["offen"][ticket_id] = {
                "auftrag_hash": auftrag_hash,
                "ablauf_ts": self._jetzt() + self._frist_s,
            }
            self._schreiben()
        self._log.info("Ticket ausgegeben", DAIMON_MARKE="ticket",
                       DAIMON_AKTION="ausgegeben")
        return ticket_id

    def einloesen(self, ticket_id: str, *, auftrag_hash: str) -> None:
        with self._sperre:
            eintrag = self._buch["offen"].pop(ticket_id, None)
            if eintrag is not None:
                # Verbraucht wird VOR jeder Pruefung vermerkt und geschrieben.
                # Ein Ticket, das an der Hash-Pruefung scheitert, darf nicht
                # fuer einen zweiten Versuch offen bleiben.
                self._buch["verbraucht"].append(ticket_id)
                self._schreiben()
        if eintrag is None:
            self._log.warning("Ticket abgelehnt", DAIMON_MARKE="ticket",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("Ticket unbekannt oder bereits eingeloest")
        if eintrag.get("auftrag_hash") != auftrag_hash:
            self._log.warning("Ticket abgelehnt", DAIMON_MARKE="ticket",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("Ticket gehoert zu einem anderen Auftrag")
        if self._jetzt() >= float(eintrag.get("ablauf_ts", 0.0)):
            self._log.warning("Ticket abgelaufen", DAIMON_MARKE="ticket",
                              DAIMON_AKTION="abgelehnt")
            raise MarkenFehler("Ticket abgelaufen")
        self._log.info("Ticket eingeloest", DAIMON_MARKE="ticket",
                       DAIMON_AKTION="eingeloest")
