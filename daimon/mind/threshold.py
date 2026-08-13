"""T-6.5 -- die vier Sprech-Stufen. Sie sollen tun, was sie sagen.

Design 10.1 nennt `silent | urgent | helpful | chatty` als Wert in der
Persona-Datei -- und sonst nichts. Was sie BEDEUTEN, wird hier festgelegt,
und die eine Entscheidung dabei ist diese:

**Die Schwelle regelt UNGEFRAGTES Reden. Eine Antwort geht immer durch.**
Sonst waere `silent` kein Assistent, sondern ein abgeschaltetes Geraet -- und
dafuer gibt es den Kill-Switch. Wer gefragt hat, hat gefragt; die Stufe sagt,
wie oft das Pet von SELBST anfaengt.

Die drei Dringlichkeiten sind der zweite Teil des Paares. Eine Stufe allein
kann nicht entscheiden, ob eine Aeusserung faellig ist -- sie braucht etwas,
woran sie sich misst:

    kritisch     etwas ist kaputt oder wartet auf eine Freigabe
    nuetzlich    etwas ist auffaellig und kostet Zeit, wenn niemand es sagt
    beilaeufig   etwas ist wahr und haette auch ungesagt bleiben koennen

    silent    nie ungefragt
    urgent    ab kritisch
    helpful   ab nuetzlich      <- Vorgabe
    chatty    ab beilaeufig

`silent` steht bewusst als `None` und nicht als unerreichbar hohe Zahl: eine
Zahl laedt dazu ein, sie zu ueberbieten, und dann spricht die Stufe, die
„still" heisst.
"""
from __future__ import annotations

from typing import Any

STUFEN = ("silent", "urgent", "helpful", "chatty")
VORGABE = "helpful"

KRITISCH = "kritisch"
NUETZLICH = "nuetzlich"
BEILAEUFIG = "beilaeufig"

# Streng aufsteigend. Der Vergleich ist `>=`, nicht `==` -- eine Stufe, die
# nur genau ihre eigene Dringlichkeit durchliesse, verschluckte gerade das
# Wichtigste.
RANG = {BEILAEUFIG: 1, NUETZLICH: 2, KRITISCH: 3}

SCHWELLE: dict[str, int | None] = {
    "silent": None,          # nie ungefragt
    "urgent": RANG[KRITISCH],
    "helpful": RANG[NUETZLICH],
    "chatty": RANG[BEILAEUFIG],
}

# Anlaesse, die keine Schwelle kennen: es wurde gefragt.
GEFRAGT = frozenset({"antwort", "rueckfrage"})


class SchwellenFehler(ValueError):
    """Unbekannte Stufe oder Dringlichkeit. Nennt die bekannten."""


class Schwelle:
    """Entscheidet, ob eine Aeusserung faellig ist. Sagt sie nicht."""

    def __init__(self, stufe: str = VORGABE) -> None:
        self._stufe = self._pruefen(stufe)
        self.durchgelassen = 0
        self.abgewiesen: dict[str, int] = {}

    @staticmethod
    def _pruefen(stufe: str) -> str:
        s = str(stufe).strip().lower()
        if s not in SCHWELLE:
            # Kein stiller Rueckfall auf die Vorgabe: ein Tippfehler in der
            # Persona-Datei soll auffallen und nicht dazu fuehren, dass ein
            # als `silent` gemeintes Pet `helpful` ist.
            raise SchwellenFehler(
                f"unbekannte Stufe {stufe!r}. Bekannt: " + ", ".join(STUFEN))
        return s

    @property
    def stufe(self) -> str:
        return self._stufe

    def setzen(self, stufe: str) -> str:
        """Zur Laufzeit umschaltbar -- ohne Neustart, ohne Datei."""
        self._stufe = self._pruefen(stufe)
        return self._stufe

    def darf_sprechen(self, anlass: str, dringlichkeit: str = BEILAEUFIG) -> bool:
        """`True`, wenn diese Aeusserung bei dieser Stufe faellig ist."""
        if str(anlass).strip().lower() in GEFRAGT:
            # Wer gefragt hat, hat gefragt. Auch bei `silent`.
            self.durchgelassen += 1
            return True

        d = str(dringlichkeit).strip().lower()
        if d not in RANG:
            raise SchwellenFehler(
                f"unbekannte Dringlichkeit {d!r}. Bekannt: "
                + ", ".join(sorted(RANG, key=RANG.get)))

        grenze = SCHWELLE[self._stufe]
        if grenze is not None and RANG[d] >= grenze:
            self.durchgelassen += 1
            return True

        schluessel = f"{self._stufe}:{d}"
        self.abgewiesen[schluessel] = self.abgewiesen.get(schluessel, 0) + 1
        return False

    def zaehler(self) -> dict[str, Any]:
        return {"stufe": self._stufe, "durchgelassen": self.durchgelassen,
                "abgewiesen": dict(self.abgewiesen)}
