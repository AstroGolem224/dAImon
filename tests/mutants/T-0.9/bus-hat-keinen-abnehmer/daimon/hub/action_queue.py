"""T-4.15 — Gegendruck: hoechstens einmal, und keine Rueckfrage-Flut.

**Hoechstens** einmal, nicht genau einmal (Design 6.2)
----------------------------------------------------------------------------
Genau-einmal ueber eine Prozessgrenze hinweg gibt es nicht. Zwischen
"Ticket eingeloest" und "Broker hat bestaetigt" kann der Strom ausfallen --
und dann ist unbekannt, ob die Wirkung eingetreten ist. Die ehrliche Antwort
darauf ist `outcome=unknown` im Audit, **nicht** ein automatischer zweiter
Versuch. Ein Wiederholungsmechanismus macht aus einem unbekannten Ausgang
zwei moegliche Ausfuehrungen; bei "Datei loeschen" ist das der teurere
Fehler.

Deshalb: kein Retry. Nirgends. Wer wiederholen will, ist ein Mensch, der
noch einmal fragt -- und dann entsteht ein neues Ticket.

Warum abgelehnt statt aufgestaut
----------------------------------------------------------------------------
Eine Warteschlange fuer Rueckfragen waere eine Warteschlange fuer Dialoge.
Der Nutzer klickt den fuenften weg, ohne den ersten gelesen zu haben -- und
genau das ist der Angriff, den T-4.19 an anderer Stelle beschreibt.
Ueber der Hoechstzahl wird deshalb abgelehnt, sichtbar und mit Grund.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Wieviele Rueckfragen gleichzeitig offen sein duerfen. Drei, weil ein Mensch
# drei Fenster noch einzeln liest und fuenf nicht mehr.
MAX_OFFENE_RUECKFRAGEN = 3

# Aktionsanfragen je Rundenmarke. Eine Runde ist eine Aeusserung; wer daraus
# zwoelf Aktionen ableitet, hat nicht zugehoert.
MAX_ANFRAGEN_JE_RUNDE = 5

VERWORFEN_GRUENDE = ("ticket_verbraucht", "zu_viele_rueckfragen",
                     "rate_limit", "unbekanntes_ticket")


@dataclass
class Aktionsschlange:
    """Der Gegendruck. Kein Speicher fuer Auftraege -- nur fuer das Nein."""

    max_offene: int = MAX_OFFENE_RUECKFRAGEN
    max_je_runde: int = MAX_ANFRAGEN_JE_RUNDE
    _gesehen: dict = field(default_factory=dict)     # ticket -> zustand
    _offene: set = field(default_factory=set)        # rueckfrage-ids
    _je_runde: dict = field(default_factory=dict)    # turn_id -> anzahl
    verworfen: dict = field(default_factory=dict)    # grund -> anzahl

    def _verwerfen(self, grund: str) -> dict:
        self.verworfen[grund] = self.verworfen.get(grund, 0) + 1
        return {"ok": False, "grund": grund}

    # -- Anfragen je Runde ---------------------------------------------------

    def anfrage_zulassen(self, turn_id: str) -> dict:
        anzahl = self._je_runde.get(turn_id, 0)
        if anzahl >= self.max_je_runde:
            return self._verwerfen("rate_limit")
        self._je_runde[turn_id] = anzahl + 1
        return {"ok": True, "grund": "", "in_dieser_runde": anzahl + 1}

    # -- Rueckfragen ---------------------------------------------------------

    def rueckfrage_oeffnen(self, rueckfrage_id: str) -> dict:
        if len(self._offene) >= self.max_offene:
            # Abgelehnt, nicht aufgestaut. Siehe Modulkopf.
            return self._verwerfen("zu_viele_rueckfragen")
        self._offene.add(rueckfrage_id)
        return {"ok": True, "grund": "", "offen": len(self._offene)}

    def rueckfrage_schliessen(self, rueckfrage_id: str) -> None:
        self._offene.discard(rueckfrage_id)

    @property
    def offene(self) -> int:
        return len(self._offene)

    # -- Hoechstens einmal ---------------------------------------------------

    def einloesen(self, ticket: str) -> dict:
        """Vor der Mutation. Ein zweites Mal geht nicht -- nie.

        Der Zustand bleibt `unterwegs`, bis der Broker bestaetigt. Faellt
        etwas dazwischen aus, bleibt er `unterwegs` und wird zu
        `outcome=unknown` -- und NICHT zu einem zweiten Versuch.
        """
        zustand = self._gesehen.get(ticket)
        if zustand is not None:
            return self._verwerfen("ticket_verbraucht")
        self._gesehen[ticket] = "unterwegs"
        return {"ok": True, "grund": "", "zustand": "unterwegs"}

    def bestaetigen(self, ticket: str, *, ok: bool) -> dict:
        if ticket not in self._gesehen:
            return self._verwerfen("unbekanntes_ticket")
        self._gesehen[ticket] = "ok" if ok else "failed"
        return {"ok": True, "outcome": self._gesehen[ticket]}

    def ausgang(self, ticket: str) -> str:
        """Was ins Audit gehoert. `unknown`, solange niemand bestaetigt hat."""
        zustand = self._gesehen.get(ticket)
        if zustand is None:
            return "denied"
        return "unknown" if zustand == "unterwegs" else zustand

    def offene_ausgaenge(self) -> dict:
        """Nach einem Neustart: alles Unterwegs ist ab jetzt `unknown`."""
        return {t: "unknown" for t, z in self._gesehen.items()
                if z == "unterwegs"}
