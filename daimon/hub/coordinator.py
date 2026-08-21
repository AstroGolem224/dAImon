"""T-4.16 — der Ende-zu-Ende-Aktionspfad. Die Bausteine, tatsaechlich verdrahtet.

Warum es diesen Task gibt
----------------------------------------------------------------------------
In v1.0 des Plans fehlte er. Jeder Baustein existierte -- Policy, Auftrag,
Consent, Broker, Audit -- und niemand hatte sie zusammengesteckt. Genau so
entsteht ein System, das in jedem Einzelteil geprueft ist und als Ganzes nie
gelaufen. Dieses Modul ist die Naht, und es enthaelt deshalb bewusst KEINE
eigene Entscheidungslogik: jede Zusage steht in dem Modul, dem sie gehoert.
Was hier steht, ist die Reihenfolge.

Die Reihenfolge, und was in ihr nicht verschiebbar ist
----------------------------------------------------------------------------
    Rundenmarke  ->  Policy  ->  (Vorschau + Freigabe)  ->  Auftrag mit Ticket
                 ->  Undo     ->  Broker  ->  Audit

* **Policy vor allem anderen.** Ein `deny` erzeugt keine Vorschau. Sonst
  koennte ein Angreifer Dialoge erzeugen, ohne je etwas ausfuehren zu duerfen.
* **Freigabe vor Auftrag.** Das Ticket entsteht erst, wenn zugestimmt wurde --
  ein Ticket, das waehrend der Vorschau existiert, ist ein Ticket, das jemand
  benutzen koennte, waehrend der Mensch noch liest.
* **Undo vor Mutation.** Faellt die Vorbereitung, faellt die Aktion (T-4.8).
* **Audit in JEDEM Ausgang**, auch bei `deny` und `cancel`. Ein Audit, das nur
  Erfolge kennt, beantwortet die eine Frage nicht, fuer die man es aufschlaegt.

Der Direktpfad umgeht die Vorschau -- und sonst nichts
----------------------------------------------------------------------------
Bei `direct: true` im Katalog UND `quelle == "parser"` entscheidet die Policy
`allow`, und dann entsteht kein Dialog. Ticket, Auftrag, Broker und Audit
laufen unveraendert. Wer den Direktpfad fuer eine Abkuerzung durch den ganzen
Weg haelt, hat ihn nicht verstanden: er spart die Rueckfrage, nicht die
Spur.

Jeder Hop traegt `turn_id` und `tool_use_id`
----------------------------------------------------------------------------
`turn_id` kommt aus der Rundenmarke, `tool_use_id` bezeichnet diese eine
Anfrage. Beide gehen durch bis ins Audit. Ohne sie liesse sich hinterher
nicht sagen, welche Aeusserung welche Wirkung hatte -- und das ist die
Frage, die nach einem Zwischenfall gestellt wird.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from daimon.hub.policy import Anfrage, Policy

# Die Stufen, deren Dauer in der Diagnose steht. Ein Hop ohne Messpunkt ist
# ein Hop, dessen Langsamkeit niemandem auffaellt.
STUFEN = ("policy", "vorschau", "freigabe", "undo", "broker", "audit")

# Was gesprochen wird, wenn etwas schiefgeht. Feste Vorlagen, keine
# Modellformulierung -- Design 5.2 laesst ungefragte Sprachausgabe nur aus
# kuratierten Vorlagen ziehen.
SPRACHE = {
    "deny": "Das darf ich nicht.",
    "unknown_action": "Diese Aktion kenne ich nicht.",
    "unparseable_argument": "Den Wert habe ich nicht verstanden.",
    "argument_out_of_range": "Der Wert liegt ausserhalb des Erlaubten.",
    "circuit_breaker": "Ich habe die Aktionen vorsichtshalber abgeschaltet.",
    "gesture_window_closed": "Das braucht einen frischen Tastendruck.",
    "declined": "Gut, ich lasse es.",
    "cancelled": "Ich habe keine Antwort bekommen und lasse es.",
    "undo": "Ich konnte kein Undo anlegen und habe es deshalb gelassen.",
    "broker": "Das hat nicht geklappt.",
    "rate_limit": "Das waren mir zu viele Aktionen auf einmal.",
    "zu_viele_rueckfragen": "Es sind schon zu viele Rueckfragen offen.",
    "ok": "",
}


@dataclass
class Lauf:
    """Was ein Durchgang hinterlaesst -- inklusive der Zeiten je Hop."""

    verdikt: str
    grund: str
    turn_id: str
    tool_use_id: str
    ausgefuehrt: bool = False
    direkt: bool = False
    gesprochen: str = ""
    dauer_ms: dict = field(default_factory=dict)


@dataclass
class Koordinator:
    """Die Naht. Alle Teile werden hereingereicht, keines hier gebaut."""

    policy: Policy
    consent: Any
    auftragsbuch: Any
    schlange: Any
    audit: Any
    broker: Callable[[Any], dict]
    vorschau: Callable[..., str]
    sprechen: Callable[[str], None] = lambda text: None
    undo: Callable[..., Any] | None = None
    uhr: Callable[[], float] = time.monotonic

    def _melden(self, lauf: Lauf, schluessel: str) -> Lauf:
        # Ein unbekannter Grund ist ein Grund aus einer Regel
        # (`katalog:background`, `regel:deny`, `vorschau_pflicht`). Er wird
        # NICHT vorgelesen: die gesprochenen Saetze sind kuratierte Vorlagen
        # (Design 5.2), und ein durchgereichter Regelname waere Text aus einer
        # Konfigurationsdatei im Raum. Also die allgemeine Absage.
        text = SPRACHE.get(schluessel) or SPRACHE["deny"]
        if text:
            lauf.gesprochen = text
            self.sprechen(text)
        return lauf

    def _audit(self, lauf: Lauf, *, prompt: str, params_hash: str,
               mark_id: str, initiator: str, outcome: str) -> None:
        self.audit.schreiben(prompt_shown=prompt or "(keine Vorschau)",
                             params_hash=params_hash, mark_id=mark_id,
                             initiator=initiator, turn_id=lauf.turn_id,
                             tool_use_id=lauf.tool_use_id, outcome=outcome)

    def ausfuehren(self, *, action_id: str, params: dict, quelle: str,
                   marke: dict | None, session_id: str, turn_id: str,
                   tool_use_id: str, audience: str = "dbus") -> Lauf:
        lauf = Lauf(verdikt="deny", grund="", turn_id=turn_id,
                    tool_use_id=tool_use_id)
        t0 = self.uhr()

        # 0. Gegendruck. Vor der Policy, weil eine abgelehnte Flut gar nicht
        #    erst entschieden werden muss.
        zulassung = self.schlange.anfrage_zulassen(turn_id)
        if not zulassung["ok"]:
            lauf.grund = zulassung["grund"]
            self._audit(lauf, prompt="", params_hash="", mark_id="",
                        initiator="unknown", outcome="denied")
            return self._melden(lauf, zulassung["grund"])

        # 1. Policy. Sie kennt Katalog, Marke, Gestenfenster und Sicherung.
        entscheidung = self.policy.entscheide(Anfrage(
            action_id=action_id, params=params, session_id=session_id,
            request_id=tool_use_id, quelle=quelle, marke=marke,
            jetzt=self.uhr()))
        lauf.dauer_ms["policy"] = round((self.uhr() - t0) * 1000, 3)
        lauf.verdikt, lauf.grund = entscheidung.verdikt, entscheidung.grund
        mark_id = str((marke or {}).get("id", ""))

        if entscheidung.verdikt == "deny":
            # Kein Dialog. Wer hier eine Rueckfrage erzeugte, gaebe einem
            # Angreifer ein Werkzeug, das nichts ausfuehrt und trotzdem stoert.
            self._audit(lauf, prompt="", params_hash=entscheidung.params_hash,
                        mark_id=mark_id, initiator=entscheidung.initiator,
                        outcome="denied")
            return self._melden(lauf, entscheidung.grund or "deny")

        prompt = ""
        if entscheidung.verdikt == "ask":
            t = self.uhr()
            prompt = self.vorschau(action_id=action_id, params=params)
            offen = self.schlange.rueckfrage_oeffnen(tool_use_id)
            if not offen["ok"]:
                self._audit(lauf, prompt=prompt,
                            params_hash=entscheidung.params_hash,
                            mark_id=mark_id, initiator=entscheidung.initiator,
                            outcome="denied")
                lauf.grund = offen["grund"]
                return self._melden(lauf, offen["grund"])
            t = self.uhr()
            try:
                rueckfrage = self.consent.stellen(
                    action_id=action_id, params_hash=entscheidung.params_hash,
                    prompt_shown=prompt, absender="auth", jetzt=self.uhr())
                lauf.dauer_ms["vorschau"] = round((self.uhr() - t) * 1000, 3)

                t = self.uhr()
                antwort = self.consent_abwarten(rueckfrage)
            finally:
                # Platz muss auch beim Fehler zwischen Oeffnen und Schliessen
                # zurueckkommen (T-4.15.v K4: sonst wird aus der Obergrenze
                # ein Countdown, der die Sitzung dauerhaft sperrt).
                self.schlange.rueckfrage_schliessen(tool_use_id)
            lauf.dauer_ms["freigabe"] = round((self.uhr() - t) * 1000, 3)
            if antwort != "granted":
                lauf.grund = antwort
                self._audit(lauf, prompt=prompt,
                            params_hash=entscheidung.params_hash,
                            mark_id=mark_id, initiator=entscheidung.initiator,
                            outcome=antwort)
                return self._melden(lauf, antwort)
        else:
            lauf.direkt = True

        # 2. Undo VOR der Mutation. Faellt es, faellt die Aktion.
        if self.undo is not None:
            t = self.uhr()
            try:
                self.undo(action_id=action_id, params=params)
            except Exception as fehler:  # UndoFehler und Verwandte
                lauf.grund = f"undo: {fehler}"
                self._audit(lauf, prompt=prompt,
                            params_hash=entscheidung.params_hash,
                            mark_id=mark_id, initiator=entscheidung.initiator,
                            outcome="failed")
                return self._melden(lauf, "undo")
            lauf.dauer_ms["undo"] = round((self.uhr() - t) * 1000, 3)

        # 3. Auftrag mit Ticket -- ERST JETZT. Ein Ticket, das waehrend der
        #    Vorschau existiert, koennte jemand benutzen, waehrend der Mensch
        #    noch liest.
        auftrag = self.auftragsbuch.ausstellen(
            audience=audience, action_id=action_id, params=params,
            turn_id=turn_id, jetzt=self.uhr())
        if not self.schlange.einloesen(auftrag.ticket)["ok"]:
            lauf.grund = "ticket_verbraucht"
            return self._melden(lauf, "broker")

        # 4. Broker.
        t = self.uhr()
        ergebnis = self.broker(auftrag)
        lauf.dauer_ms["broker"] = round((self.uhr() - t) * 1000, 3)
        # broker_weg/broker_antwort_unlesbar sind kein Urteil des Brokers,
        # sondern das Ausbleiben eines Urteils -- bestaetigen() bleibt aus,
        # das Ticket bleibt "unterwegs" und ausgang() liefert "unknown"
        # (T-4.15.v K2: sonst nicht von einer echten Ablehnung zu unterscheiden).
        if ergebnis.get("grund") not in ("broker_weg", "broker_antwort_unlesbar"):
            self.schlange.bestaetigen(auftrag.ticket, ok=bool(ergebnis.get("ok")))
        lauf.ausgefuehrt = bool(ergebnis.get("ok"))

        # 5. Audit. Der Ausgang kommt aus der Schlange, nicht aus dem Broker:
        #    sie kennt auch den Fall "eingeloest, nie bestaetigt".
        t = self.uhr()
        self._audit(lauf, prompt=prompt, params_hash=entscheidung.params_hash,
                    mark_id=mark_id, initiator=entscheidung.initiator,
                    outcome=self.schlange.ausgang(auftrag.ticket))
        lauf.dauer_ms["audit"] = round((self.uhr() - t) * 1000, 3)

        if not lauf.ausgefuehrt:
            lauf.grund = str(ergebnis.get("grund") or "broker")
            return self._melden(lauf, "broker")
        lauf.grund = ""
        return lauf

    # Ueberschreibbar: im Betrieb wartet der Hub auf die Antwort des
    # Auth-Agenten. Als eigene Methode, damit ein Test nicht auf einen Klick
    # warten muss -- und damit hier kein Zeitgeber steht.
    def consent_abwarten(self, rueckfrage) -> str:
        raise NotImplementedError(
            "Der Hub reicht die Antwort des Auth-Agenten herein")
