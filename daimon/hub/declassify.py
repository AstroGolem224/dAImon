"""T-5.9 -- das Deklassifizierungs-Gate. Der einzige Ausgang aus der Quarantaene.

Design 7.2b in einem Satz: *Passiv Wahrgenommenes erreicht das Modell nur,
wenn der Nutzer in derselben Runde Push-to-Talk ausgeloest UND nach dem
Bildschirm gefragt hat.* Beides, nicht eines von beiden.

**Ein API-Kontingent aus dem Wake-Word deklassifiziert NICHTS.** Das ist kein
Detail, sondern der Angriff, den es abwehrt: sonst reichte ein Video, das den
Namen sagt und „was steht auf meinem Bildschirm?" fragt. Das Kontingent sagt
„es darf geredet werden"; die Rundenmarke sagt „ein Mensch hat die Taste
gedrueckt". Nur die zweite Aussage traegt hier.

**Der Bildschirmbezug muss ERKENNBAR sein, und die Liste ist bewusst eng.**
Eine breite Liste liesse jede Aeusserung als Bildschirmfrage durchgehen, und
dann bliebe von der Bedingung nur noch die Marke uebrig. Die enge Liste kostet
gelegentlich eine Nachfrage -- das ist der harmlose Fehler. Der andere waere,
den Bildschirm herauszugeben, weil jemand „hier" gesagt hat.

**Freigegebener Kontext ist `tainted`, und das genuegt.** Die Senkentabelle
aus T-3.13b verbietet `tainted` in Durchgang 1 und erlaubt es in Durchgang 2.
Dieses Modul setzt die Markierung richtig und laesst `pruefe_senke` die Arbeit
tun -- eine zweite, eigene Durchsetzung waere eine zweite Stelle, an der man
sie vergessen kann.

**Durchgang 1 bekommt opake Referenzen, nie Titel.** `window_ref = "w_3"`
statt `window.title`, `app_id` aus einer geschlossenen Aufzaehlung. Ein
Fenstertitel ist Angreifertext: er steht in einem Browsertab, den irgendwer
benannt hat. Auch als typisiertes Feld bleibt er das (Design 5.1).

**Ohne Nutzerhandlung gibt es keine Freigabe -- auch nicht fuer proaktives
Verhalten.** Das ist die Regel, die am ehesten aufgeweicht wird, weil ein
Assistent, der von selbst etwas Kluges zum Bildschirm sagt, sich besser
anfuehlt. Er waere ein Assistent, der den Bildschirm ungefragt liest.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from daimon.common.protocol import Mark
from daimon.common.taint import markiere
from daimon.hub.marks import MarkenFehler

GRUND_KEINE_MARKE = "keine_marke"
GRUND_KONTINGENT = "kontingent_deklassifiziert_nicht"
GRUND_KEIN_BEZUG = "kein_bildschirmbezug"
GRUND_MARKE_UNGUELTIG = "marke_ungueltig"
GRUND_PROAKTIV = "ohne_nutzerhandlung"

# Bewusst eng. Jedes Wort hier ist eines, mit dem ein Mensch nach dem
# BILDSCHIRM fragt -- nicht nach der Welt. „hier", „das da" und „schau mal"
# stehen NICHT drin: sie kommen in jedem zweiten Satz vor, und mit ihnen waere
# die Bedingung erfuellt, sobald jemand ueberhaupt spricht.
_BEZUG = re.compile(
    r"\b("
    r"bildschirm|bildschirms|monitor|monitors|"
    r"fenster|fensters|"
    r"screen|display|"
    r"angezeigt|zu sehen|sichtbar|"
    r"steht (?:da|dort|hier auf)|"
    r"was liest du|siehst du"
    r")\b", re.IGNORECASE)


class GateFehler(PermissionError):
    """Freigabe verweigert. Nennt IMMER den Grund."""

    def __init__(self, grund: str, text: str = "") -> None:
        self.grund = grund
        super().__init__(text or grund)


@dataclass(frozen=True)
class Freigabeschein:
    """Der Nachweis, den der Kontextspeicher sehen will.

    Ein eigener Typ und kein `True`: ein Wahrheitswert entsteht aus jedem
    Versehen, ein Schein nur dort, wo dieses Modul ihn herstellt -- und das
    tut es erst nach eingeloester Marke und erkanntem Bildschirmbezug.
    """

    turn_id: str


@dataclass(frozen=True)
class Freigabe:
    turn_id: str
    umfang: dict[str, int]
    eintraege: list[Any] = field(default_factory=list)
    senke: str = "durchgang2"


def bildschirmbezug(aeusserung: str) -> bool:
    """Fragt diese Aeusserung erkennbar nach dem Bildschirm?

    Ein `False` ist der harmlose Fehler: der Nutzer wiederholt sich. Ein
    `True` zu viel gibt den Bildschirm heraus.
    """
    return bool(_BEZUG.search(aeusserung or ""))


class Deklassifizierung:
    """Der eine Weg aus der Quarantaene. Es gibt keinen zweiten."""

    def __init__(self, *, marken, speicher, audit=None,
                 uhr: Callable[[], float] = time.time) -> None:
        self._marken = marken
        self._speicher = speicher
        self._audit = audit
        self._uhr = uhr
        self.abgelehnt: dict[str, int] = {}

    def _ablehnen(self, grund: str, turn_id: str = "") -> GateFehler:
        self.abgelehnt[grund] = self.abgelehnt.get(grund, 0) + 1
        self._schreiben(outcome="denied", grund=grund, turn_id=turn_id,
                        umfang={})
        return GateFehler(grund)

    def _schreiben(self, *, outcome: str, grund: str, turn_id: str,
                   umfang: dict) -> None:
        if self._audit is None:
            return
        try:
            self._audit.schreiben(
                action_id="context.declassify", outcome=outcome,
                turn_id=turn_id or "-", tool_use_id="-",
                prompt_shown=f"{grund} {sorted(umfang.items())}",
                tainted=("prompt_shown",))
        except Exception:
            # Ein klemmendes Audit darf die ABLEHNUNG nicht in eine Freigabe
            # verwandeln. Es darf sie auch nicht verschlucken -- deshalb
            # steht der Aufruf hinter der Entscheidung, nicht davor.
            pass

    def freigeben(self, *, aeusserung: str, turn_id: str | None = None,
                  kontingent: str | None = None,
                  proaktiv: bool = False) -> Freigabe:
        """Kontext aus der Quarantaene, oder `GateFehler` mit Grund.

        Die Reihenfolge ist nicht beliebig, und sie war im ersten Entwurf
        falsch: dort wurde die Marke VOR dem Bildschirmbezug eingeloest. Eine
        Aeusserung ohne Bezug verbrannte damit die Marke, und der Nutzer
        konnte in derselben Runde nicht nachfassen -- er haette die Taste
        erneut druecken muessen, ohne zu erfahren warum. Eingeloest wird
        zuletzt: die Marke gehoert dem, was GELINGT.
        """
        if proaktiv:
            # Steht VOR allem anderen. Ein proaktiver Aufruf mit gueltiger
            # Marke ist keine Nutzerhandlung -- die Marke gehoert dann zu
            # einer Runde, in der der Nutzer etwas ANDERES wollte.
            raise self._ablehnen(GRUND_PROAKTIV)
        if turn_id is None:
            # Das Kontingent bekommt seinen EIGENEN Grund und nicht
            # „keine Marke". Wer im Journal sucht, warum der Bildschirm
            # nicht herauskam, soll den Wake-Word-Weg benannt sehen.
            raise self._ablehnen(
                GRUND_KONTINGENT if kontingent else GRUND_KEINE_MARKE)
        if kontingent:
            # Beides zugleich ist kein Zufall, sondern ein Versuch, die
            # schwaechere Bedingung mitlaufen zu lassen.
            raise self._ablehnen(GRUND_KONTINGENT, turn_id)

        if not bildschirmbezug(aeusserung):
            raise self._ablehnen(GRUND_KEIN_BEZUG, turn_id)

        try:
            self._marken.einloesen(turn_id)
        except MarkenFehler:
            raise self._ablehnen(GRUND_MARKE_UNGUELTIG, turn_id) from None

        eintraege = self._speicher.freigeben(Freigabeschein(turn_id=turn_id))
        umfang = {art: len(liste) for art, liste in eintraege.items()}
        markiert = [markiere(e, Mark.TAINTED)
                    for liste in eintraege.values() for e in liste]
        self._schreiben(outcome="ok", grund="freigabe", turn_id=turn_id,
                        umfang=umfang)
        return Freigabe(turn_id=turn_id, umfang=umfang, eintraege=markiert)


# -- Durchgang 1: opake Referenzen -----------------------------------------

def referenzen(fenster: Iterable[dict], *,
               bekannte_app_ids: Iterable[str] = ()) -> list[dict]:
    """Was Durchgang 1 ueber Fenster erfahren darf. Keine Titel.

    `window_ref` ist eine laufende Nummer und traegt keine Bedeutung; `app_id`
    kommt aus einer geschlossenen Aufzaehlung. Was nicht darin steht, wird
    `unbekannt` -- und NICHT durchgereicht: eine unbekannte `app_id` waere
    eine Zeichenkette aus fremder Quelle in einem Feld, das als geschlossen
    gilt.
    """
    erlaubt = {s.strip().lower() for s in bekannte_app_ids if s.strip()}
    heraus = []
    for i, f in enumerate(fenster):
        roh = str(f.get("app_id", "")).strip().lower()
        heraus.append({
            "window_ref": f"w_{i}",
            "app_id": roh if roh in erlaubt else "unbekannt",
        })
    return heraus
