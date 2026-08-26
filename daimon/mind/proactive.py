"""T-6.6 -- proaktives Verhalten. Melden, wenn Schweigen teurer waere.

Die Zusage dieses Moduls ist zur Haelfte eine Aufzaehlung dessen, was es NICHT
tut: keine Deklassifizierung, kein Egress-Aufruf, kein Auth-Dialog. Das ist
kein Beiwerk -- es ist der Grund, warum proaktives Verhalten ueberhaupt
erlaubt sein kann.

**Der Weg dorthin ist strukturell, nicht diszipliniert.** Dieses Modul
ENTSCHEIDET nur; es spricht nicht, es liest keinen Bildschirm, es ruft nichts
auf. Es gibt einen `Vorschlag` zurueck, und wer damit etwas tut, ist jemand
anders -- der dann seine eigenen Gatter passiert. Eine Klasse, die selbst
sprechen koennte und es nur unterlaesst, waere eine Klasse, die spricht,
sobald jemand eine Zeile hinzufuegt.

Die Ergaenzung dazu steht in T-5.9: `Deklassifizierung.freigeben(proaktiv=True)`
lehnt VOR allem anderen ab, auch mit gueltiger Rundenmarke. Beide Enden sind
also zu -- hier gibt es keinen Aufruf, und dort wuerde er abgewiesen.

**Derselbe Sachverhalt spricht genau einmal.** Ein Build, der seit zwanzig
Minuten kaputt ist, ist nicht zwanzigmal kaputt. Aufgeraeumt wird ueber
`erledigt()`: wer den Build repariert, meldet das, und ein spaeterer neuer
Bruch darf wieder sprechen. Ohne diesen Weg waere „nie wieder" nach dem ersten
Tag gleichbedeutend mit „nie".

**Der Mindestabstand gilt zusaetzlich zur Schwelle.** Die Schwelle sagt, WAS
wichtig genug ist; der Abstand sagt, wie oft. Drei kritische Anlaesse
innerhalb einer Minute sind drei richtige Entscheidungen und ein
unertraeglicher Assistent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from daimon.mind.threshold import BEILAEUFIG, KRITISCH, NUETZLICH, Schwelle

# Die Anlaesse aus der Akzeptanzliste, mit ihrer Dringlichkeit. Eine
# geschlossene Tabelle und kein freier String: ein Anlass, den niemand
# eingetragen hat, ist kein Anlass, sondern ein Tippfehler.
ANLAESSE: dict[str, str] = {
    # Der Agent wartet auf eine Freigabe und kommt ohne sie nicht weiter.
    "agent_wartet": KRITISCH,
    # Der Build ist kaputt. Kostet Zeit, wenn es niemand sagt.
    "build_kaputt": KRITISCH,
    # Dasselbe Fehlerbild zum wiederholten Mal.
    "fehlerbild": NUETZLICH,
    # Etwas Auffaelliges ohne Folgen -- nur `chatty` hoert das.
    "beobachtung": BEILAEUFIG,
    # T-8.3: ein Termin ist faellig. Der Nutzer hat ihn selbst gesetzt --
    # ihn zu verschlucken ist teurer als ihn zu sagen.
    "termin_faellig": KRITISCH,
    # T-8.3: ein Fokusblock ist zu Ende. Nuetzlich, nicht kritisch -- der
    # Block war freiwillig.
    "fokus_ende": NUETZLICH,
}

# 90 s zwischen zwei ungefragten Aeusserungen. Die Sprech-Abkuehlung aus
# Design 8.4 nennt 20 s je Anlass; das hier ist die Klammer darueber und
# absichtlich groesser: drei richtige Entscheidungen in einer Minute ergeben
# einen unertraeglichen Assistenten.
MINDESTABSTAND_S = 90.0

GRUND_UNBEKANNT = "unbekannter_anlass"
GRUND_SCHWELLE = "unter_der_schwelle"
GRUND_ABSTAND = "zu_dicht"
GRUND_WIEDERHOLUNG = "schon_gesagt"


class ProaktivFehler(ValueError):
    """Unbekannter Anlass. Nennt die bekannten."""


@dataclass(frozen=True)
class Vorschlag:
    """Was gesagt werden koennte -- nicht das Sagen selbst.

    Absichtlich ein Datensatz ohne Methoden: er kann nichts ausloesen. Wer
    daraus eine Aeusserung macht, geht durch die Gatter, die dafuer da sind.
    """

    anlass: str
    sachverhalt: str
    dringlichkeit: str
    ts: float


class Proaktiv:
    """Entscheidet, ob sich das Pet von selbst melden sollte."""

    def __init__(self, *, schwelle: Schwelle | None = None,
                 mindestabstand_s: float = MINDESTABSTAND_S,
                 uhr: Callable[[], float] = time.monotonic) -> None:
        self._schwelle = schwelle or Schwelle()
        self._abstand = float(mindestabstand_s)
        self._uhr = uhr
        self._letzte = float("-inf")
        self._gesagt: set[tuple[str, str]] = set()
        self.vorschlaege = 0
        self.abgewiesen: dict[str, int] = {}

    def _ablehnen(self, grund: str) -> None:
        self.abgewiesen[grund] = self.abgewiesen.get(grund, 0) + 1

    def melden(self, anlass: str, sachverhalt: str) -> Vorschlag | None:
        """Ein Anlass. Gibt einen `Vorschlag` zurueck -- oder `None`.

        Die Reihenfolge der Pruefungen ist die der zunehmenden Kosten: erst
        der Tippfehler, dann die Schwelle (billig, kennt der Nutzer), dann die
        Wiederholung, dann der Abstand. So steht im Zaehler der Grund, der den
        Nutzer interessiert, und nicht der zufaellig erste.
        """
        anlass = str(anlass).strip().lower()
        if anlass not in ANLAESSE:
            self._ablehnen(GRUND_UNBEKANNT)
            raise ProaktivFehler(
                f"unbekannter Anlass {anlass!r}. Bekannt: "
                + ", ".join(sorted(ANLAESSE)))

        dringlichkeit = ANLAESSE[anlass]
        # Der Anlass ist IMMER ungefragt -- deshalb steht hier nicht
        # "antwort", und die Schwelle greift wirklich. Wer hier einen
        # gefragten Anlass hineingaebe, haette die Schwelle ausgehebelt.
        if not self._schwelle.darf_sprechen(anlass, dringlichkeit):
            self._ablehnen(GRUND_SCHWELLE)
            return None

        schluessel = (anlass, str(sachverhalt))
        if schluessel in self._gesagt:
            # Ein Build, der seit zwanzig Minuten kaputt ist, ist nicht
            # zwanzigmal kaputt.
            self._ablehnen(GRUND_WIEDERHOLUNG)
            return None

        jetzt = self._uhr()
        if jetzt - self._letzte < self._abstand:
            # NICHT vermerken: der Sachverhalt ist ungesagt geblieben und darf
            # spaeter noch. Wer ihn hier eintruege, verschluckte ihn fuer
            # immer, weil zufaellig eine Minute vorher etwas anderes war.
            self._ablehnen(GRUND_ABSTAND)
            return None

        self._letzte = jetzt
        self._gesagt.add(schluessel)
        self.vorschlaege += 1
        return Vorschlag(anlass=anlass, sachverhalt=str(sachverhalt),
                         dringlichkeit=dringlichkeit, ts=jetzt)

    def erledigt(self, anlass: str, sachverhalt: str) -> bool:
        """Der Sachverhalt ist weg -- ein neuer gleicher darf wieder sprechen.

        Ohne diesen Weg waere „nie wieder zum selben Sachverhalt" nach dem
        ersten Tag gleichbedeutend mit „nie".
        """
        schluessel = (str(anlass).strip().lower(), str(sachverhalt))
        vorher = len(self._gesagt)
        self._gesagt.discard(schluessel)
        return len(self._gesagt) != vorher

    def zaehler(self) -> dict[str, Any]:
        return {"vorschlaege": self.vorschlaege,
                "offene_sachverhalte": len(self._gesagt),
                "stufe": self._schwelle.stufe,
                "mindestabstand_s": self._abstand,
                "abgewiesen": dict(self.abgewiesen)}
