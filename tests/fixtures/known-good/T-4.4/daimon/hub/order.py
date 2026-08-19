"""T-4.5, Hub-Seite — Auftraege ausstellen und Tickets einloesen.

Warum die Einloesung HIER liegt und nicht im Broker
----------------------------------------------------------------------------
Einmaligkeit ist eine Aussage ueber ALLE Broker zusammen. Loeste jeder Broker
sein Ticket selbst ein, waere "hoechstens einmal" eine Zusage je Broker --
und derselbe Auftrag bei zwei Brokern zweimal ausfuehrbar. Der Hub ist die
einzige Stelle, die das sehen kann, also loest er ein.

Eingeloest wird **unmittelbar vor der Ausfuehrung**, nicht beim Ausstellen.
Zwischen Ausstellung und Ausfuehrung liegt die Vorschau, und ein Auftrag, der
beim Ausstellen verbraucht waere, koennte nach einer Ablehnung nicht mehr
sauber verfallen -- er waere schon weg.

Die Frist ist monoton
----------------------------------------------------------------------------
`time.monotonic()`, nicht `time.time()`. Eine Zeitumstellung, ein NTP-Sprung
oder eine von Hand gestellte Uhr verlaengert keinen Auftrag. Der Preis: die
Frist ist ueber einen Neustart hinweg bedeutungslos -- deshalb ueberlebt das
Auftragsbuch einen Neustart auch nicht, und das ist richtig so: ein Auftrag,
der einen Neustart ueberlebt, ist keine Reaktion mehr.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from daimon.common.order import (AUDIENCES, Auftrag, AuftragsFehler,
                                 params_hash)

# Wie lange ein Auftrag hoechstens gilt. Lang genug fuer eine Vorschau mit
# Nachdenken, kurz genug, dass niemand ihn spaeter wiederfindet.
VORGABE_FRIST_S = 120.0


@dataclass
class Auftragsbuch:
    """Ausgestellte Tickets und ihr Zustand. Nur im Speicher, siehe Modulkopf."""

    _offen: dict = field(default_factory=dict)
    _eingeloest: dict = field(default_factory=dict)

    def ausstellen(self, *, audience: str, action_id: str, params: dict,
                   turn_id: str, frist_s: float = VORGABE_FRIST_S,
                   jetzt: float | None = None) -> Auftrag:
        if audience not in AUDIENCES:
            raise AuftragsFehler(
                f"unbekannte audience {audience!r}; erlaubt sind "
                + ", ".join(AUDIENCES))
        jetzt = time.monotonic() if jetzt is None else float(jetzt)
        # 32 Byte aus `secrets`: das Ticket ist die Einmaligkeit, und ein
        # ratbares Ticket waere eine Ausfuehrung, die sich jemand ausdenkt.
        ticket = secrets.token_urlsafe(32)
        auftrag = Auftrag(
            audience=audience, action_id=action_id, params=dict(params or {}),
            params_hash=params_hash(params), ticket=ticket,
            deadline_monotonic=jetzt + float(frist_s), turn_id=turn_id)
        self._offen[ticket] = auftrag
        return auftrag

    def gueltig(self, ticket: str, *, jetzt: float | None = None) -> bool:
        """Ohne Nebenwirkung -- fuer die Pruefung im Broker."""
        auftrag = self._offen.get(ticket)
        if auftrag is None:
            return False
        jetzt = time.monotonic() if jetzt is None else float(jetzt)
        return auftrag.deadline_monotonic > jetzt

    def einloesen(self, ticket: str, *, jetzt: float | None = None) -> Auftrag:
        """Genau einmal. Der zweite Versuch ist ein Fehler, kein `None`.

        Ein stilles `None` waere an der Aufrufstelle ein "dann eben nicht" --
        und ein zweiter Ausfuehrungsversuch gehoert ins Audit, nicht in eine
        Verzweigung.
        """
        jetzt = time.monotonic() if jetzt is None else float(jetzt)
        if ticket in self._eingeloest:
            raise AuftragsFehler(
                f"Ticket bereits eingeloest um "
                f"{self._eingeloest[ticket]:.3f} (monoton)")
        auftrag = self._offen.pop(ticket, None)
        if auftrag is None:
            raise AuftragsFehler("Ticket unbekannt")
        if auftrag.deadline_monotonic <= jetzt:
            # Verfallen ist nicht eingeloest: das Ticket landet NICHT in
            # `_eingeloest`, sonst waere die Meldung beim naechsten Versuch
            # falsch ("bereits eingeloest" statt "abgelaufen").
            raise AuftragsFehler("Frist abgelaufen")
        self._eingeloest[ticket] = jetzt
        return auftrag

    def verfallen_lassen(self, ticket: str) -> None:
        """Nach einer Ablehnung in der Vorschau. Das Ticket ist damit weg,
        ohne je ausgefuehrt worden zu sein."""
        self._offen.pop(ticket, None)

    def aufraeumen(self, *, jetzt: float | None = None) -> int:
        jetzt = time.monotonic() if jetzt is None else float(jetzt)
        tot = [t for t, a in self._offen.items()
               if a.deadline_monotonic <= jetzt]
        for t in tot:
            del self._offen[t]
        return len(tot)

    @property
    def offen(self) -> int:
        return len(self._offen)
