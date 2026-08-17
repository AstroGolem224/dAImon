"""T-0.8 — Broker-Ticketbuch: persistent und atomar.

Das Ticket ist das letzte Glied der Kette aus Design 2.4: Rundenmarke,
Aktionsfreigabe und API-Kontingent leben im RAM des Hubs -- das Ticket liegt
auf der Platte, weil es die Ausfuehrung beim Broker freischaltet und davor
ein Neustart liegen kann. Verbrauchte Tickets muessen einen Neustart
ueberleben, sonst waere ein Absturz zwischen Ausgabe und Ausfuehrung eine
Wiedereinloesung.

Zwei Regeln tragen das:

  * **Atomares Schreiben.** Jede Aenderung wird in eine temporaere Datei im
    **selben Verzeichnis** geschrieben, geflusht, per `os.fsync` auf die
    Platte gezwungen und dann per `os.replace` ueber das Ziel gelegt. Ein
    Absturz mitten drin hinterlaesst das alte, heile Buch -- nie ein halb
    geschriebenes. Ein `json.dump` direkt auf die Zieldatei waere genau der
    Fehler.
  * **Einmal.** Einloesen verbraucht das Ticket im selben Schreibvorgang.
    Falscher `auftrag_hash` ist eine Ablehnung UND verbrennt das Ticket --
    sonst waere jede `ticket_id` ein Orakel, das den Hash durch Probieren
    verraet (dieselbe Begruendung, mit der `FreigabeBuch.bestaetigen` die
    Nonce verbrennt). Der Hash kommt nie aus dem Aufruf in den Bestand,
    sondern wird nur verglichen.

Auch hier: Fristen aus dem Konstruktor, Zeitpunkte aus der injizierten
Zeitquelle, Ticket-IDs erzeugt das Buch selbst. Kein Feld eines eingehenden
Requests wird je als Zustand uebernommen.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from daimon.common.atomar import schreibe_atomar
from daimon.hub.marks import MarkenFehler

_FORMAT_VERSION = 1


class Ticketbuch:
    """Persistenter, atomarer Speicher fuer Broker-Tickets.

    Ein Ticket bindet `ticket_id` an `auftrag_hash` und eine Frist und ist
    hoechstens einmal einloesbar -- auch ueber Prozessneustarts hinweg.
    """

    TYP = "broker_ticket"

    def __init__(self, pfad: Path, *, frist_s: float = 300.0,
                 jetzt: Callable[[], float] = time.monotonic,
                 log: Any = None) -> None:
        if frist_s <= 0:
            raise ValueError("frist_s muss positiv sein")
        self._pfad = Path(pfad)
        self._frist = frist_s
        self._jetzt = jetzt
        self._log = log
        self._lock = threading.Lock()
        self._tickets: dict[str, dict[str, Any]] = {}
        self._laden()

    # -- Persistenz ---------------------------------------------------------

    def _laden(self) -> None:
        """Bestand einlesen. Eine kaputte Datei ist ein leerer Bestand mit
        Fehlermeldung -- nie still ein halbes Buch."""
        try:
            roh = self._pfad.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise MarkenFehler(f"ticketbuch nicht lesbar: {exc}") from exc
        try:
            daten = json.loads(roh)
            tickets = daten["tickets"]
            if daten.get("v") != _FORMAT_VERSION or not isinstance(tickets, dict):
                raise ValueError("fremdes format")
        except (ValueError, KeyError, TypeError) as exc:
            self._audit("ablehnung", DAIMON_GRUND=f"buch beschaedigt: {exc}")
            raise MarkenFehler(f"ticketbuch beschaedigt: {exc}") from exc
        self._tickets = {
            str(tid): {
                "auftrag_hash": str(t["auftrag_hash"]),
                "ablauf_ts": float(t["ablauf_ts"]),
                "verbraucht": bool(t["verbraucht"]),
            }
            for tid, t in tickets.items()
        }

    def _schreiben(self) -> None:
        """Atomar. Die fsync-Folge steht seit T-3.9 in
        `daimon/common/atomar.py` -- sie hatte mit der Abkuehlung einen zweiten
        Aufrufer, und zwei Kopien einer fsync-Folge driften auseinander."""
        nutzlast = json.dumps(
            {"v": _FORMAT_VERSION, "tickets": self._tickets},
            sort_keys=True,
        ).encode("utf-8")
        schreibe_atomar(self._pfad, nutzlast)

    # -- Audit ----------------------------------------------------------------

    def _audit(self, handlung: str, **felder: Any) -> None:
        if self._log is None:
            return
        self._log.info(
            f"{self.TYP}: {handlung}",
            DAIMON_TYP=self.TYP,
            DAIMON_HANDLUNG=handlung,
            **felder,
        )

    def _ablehnung(self, grund: str, **felder: Any) -> MarkenFehler:
        self._audit("ablehnung", DAIMON_GRUND=grund, **felder)
        return MarkenFehler(f"{self.TYP}: {grund}")

    # -- Automat -----------------------------------------------------------

    def ausgeben(self, *, auftrag_hash: str) -> str:
        """Neues Ticket. Rueckgabe ist die `ticket_id`, die das Buch selbst
        erzeugt -- der Aufrufer liefert nur den Hash des Auftrags."""
        if not isinstance(auftrag_hash, str) or not auftrag_hash:
            raise self._ablehnung("auftrag_hash fehlt")
        with self._lock:
            ticket_id = secrets.token_hex(16)
            self._tickets[ticket_id] = {
                "auftrag_hash": auftrag_hash,
                "ablauf_ts": self._jetzt() + self._frist,
                "verbraucht": False,
            }
            self._schreiben()
            self._audit("ausgabe", DAIMON_TICKET=ticket_id,
                        DAIMON_AUFTRAG_HASH=auftrag_hash)
            return ticket_id

    def einloesen(self, ticket_id: str, *, auftrag_hash: str) -> None:
        """Hoechstens einmal, unmittelbar vor der Ausfuehrung. Falscher
        auftrag_hash -> MarkenFehler."""
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise self._ablehnung("unbekannte ticket_id",
                                      DAIMON_TICKET=str(ticket_id)[:64])
            if ticket["verbraucht"]:
                raise self._ablehnung("bereits eingeloest",
                                      DAIMON_TICKET=ticket_id)
            if self._jetzt() >= ticket["ablauf_ts"]:
                raise self._ablehnung("abgelaufen", DAIMON_TICKET=ticket_id)
            if ticket["auftrag_hash"] != auftrag_hash:
                # Wie in FreigabeBuch.bestaetigen: ein Fehlversuch
                # verbrennt das Ticket. Bliebe es offen, waere jede
                # ticket_id ein Orakel, das den auftrag_hash durch
                # beliebig viele Versuche verraet. Auch das Verbrennen
                # geht sofort auf die Platte -- ein Neustart darf das
                # Orakel nicht wieder oeffnen.
                ticket["verbraucht"] = True
                self._schreiben()
                raise self._ablehnung(
                    "ticket gehoert zu anderem auftrag_hash",
                    DAIMON_TICKET=ticket_id,
                )
            ticket["verbraucht"] = True
            # Erst auf die Platte, dann gilt die Einloesung. Ein Absturz
            # davor laesst das Ticket offen -- das ist die sichere Seite.
            self._schreiben()
            self._audit("einloesung", DAIMON_TICKET=ticket_id,
                        DAIMON_AUFTRAG_HASH=auftrag_hash)
