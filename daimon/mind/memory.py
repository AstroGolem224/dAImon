"""T-6.2 -- das Kurzzeitgedaechtnis. Nachfragen sollen funktionieren.

Und zwar ohne die Luecke, die v2.0 offen liess: **Durchgang-2-Ausgaben und
bildschirmabgeleiteter Text erreichen den werkzeugfaehigen Durchgang nie, auch
nicht ueber das Gedaechtnis.** Das Gedaechtnis ist der bequemste Weg um jede
Senkenpruefung herum -- es nimmt in einer Runde entgegen und gibt in der
naechsten heraus, und dazwischen sieht niemand hin.

Drei Regeln schliessen ihn:

**Die Markierung wird nach HERKUNFT erzwungen, nicht nach Zusage.** Wer etwas
aus Durchgang 2, aus OCR oder vom VLM hereingibt, bekommt `tainted` --
unabhaengig davon, wie er es markiert hat. Ein Aufrufer, der sich irrt oder
es eilig hat, kann das Gedaechtnis damit nicht vergiften.

**Gefiltert wird mit der Senkentabelle aus T-3.13b, nicht mit einer eigenen
Liste.** `SENKEN["kurzzeitgedaechtnis"]` steht dort seit Phase 3 und verbietet
`user_audio` und `tainted`. Eine zweite Liste hier waere eine zweite Stelle,
an der man sie vergisst -- und die beiden wuerden auseinanderlaufen, sobald
jemand nur eine anfasst.

**Was gefiltert wurde, wird gezaehlt.** Ein stiller Filter ist von einem
leeren Gedaechtnis nicht zu unterscheiden, und dann sucht jemand den Fehler
im Modell.

Das Fenster ist doppelt begrenzt -- Anzahl UND Frist. Nur eine Anzahl haelt
ein Gespraech von gestern frisch, wenn seither niemand geredet hat; nur eine
Frist laesst eine hektische Viertelstunde alles verdraengen.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from daimon.common.protocol import Mark, Marked
from daimon.common.taint import SENKEN, SenkenFehler

SENKE = "kurzzeitgedaechtnis"

# Zehn Runden reichen fuer „und was war das nochmal?"; dreissig Minuten sind
# die Spanne, nach der eine Nachfrage ohnehin neu erklaert wuerde.
FENSTER = 10
FRIST_S = 30 * 60.0

# Herkuenfte, deren Material IMMER `tainted` wird -- egal wie der Aufrufer es
# markiert. Das ist die Rundengrenze aus v2.0: Modellausgabe aus Durchgang 2
# und alles vom Bildschirm.
QUELLEN_IMMER_TAINTED = frozenset({
    "durchgang2", "bildschirm", "ocr", "vlm", "kontext",
})


@dataclass(frozen=True)
class Runde:
    turn_id: str
    rolle: str
    wert: Marked
    ts: float
    quelle: str = ""


class Kurzzeit:
    """Die letzten Runden. Im Arbeitsspeicher, absichtlich.

    Kurzzeitgedaechtnis, das einen Neustart ueberlebt, ist kein
    Kurzzeitgedaechtnis -- es ist ein Langzeitgedaechtnis ohne die Auflagen
    von T-6.3. Was dauerhaft werden soll, geht ausdruecklich dorthin.
    """

    def __init__(self, *, fenster: int = FENSTER, frist_s: float = FRIST_S,
                 uhr: Callable[[], float] = time.time) -> None:
        if fenster < 1:
            raise ValueError("ein Fenster kleiner als eine Runde ist kein Fenster")
        self._fenster = int(fenster)
        self._frist = float(frist_s)
        self._uhr = uhr
        self._runden: list[Runde] = []
        self.gefiltert: dict[str, int] = {}

    # -- Hereinnehmen ------------------------------------------------------

    def merken(self, rolle: str, wert: Any, *, turn_id: str = "",
               quelle: str = "") -> Runde:
        """Eine Aeusserung oder Antwort. Die Herkunft entscheidet die Marke."""
        markiert = wert if isinstance(wert, Marked) else Marked.from_wire(wert)
        if quelle.strip().lower() in QUELLEN_IMMER_TAINTED:
            # Nicht `max(rang)`, sondern gesetzt: eine Zusage des Aufrufers
            # soll hier gar nicht erst mitreden. Sonst waere „ich markiere es
            # als trusted" die Umgehung.
            markiert = Marked(markiert.value, Mark.TAINTED)

        r = Runde(turn_id=str(turn_id), rolle=str(rolle), wert=markiert,
                  ts=self._uhr(), quelle=str(quelle))
        self._runden.append(r)
        self._beschneiden()
        return r

    def _beschneiden(self) -> None:
        grenze = self._uhr() - self._frist
        self._runden = [r for r in self._runden if r.ts >= grenze][-self._fenster:]

    # -- Herausgeben -------------------------------------------------------

    def fuer_prompt(self, senke: str = SENKE) -> list[Runde]:
        """Was in den Prompt DIESER Senke darf. Gefiltert, nicht geworfen.

        `pruefe_senke` wirft, und das ist an einer Senke richtig -- hier
        waere es falsch: eine einzelne verbotene Vorrunde soll den Prompt
        nicht verhindern, sie soll nicht darin stehen.
        """
        erlaubt = SENKEN.get(senke)
        if erlaubt is None:
            # Ein Tippfehler im Senkennamen darf nicht die bequemste Umgehung
            # der Tabelle sein.
            raise SenkenFehler(f"unbekannte Senke {senke!r}. Bekannt: "
                               + ", ".join(sorted(SENKEN)))
        self._beschneiden()
        heraus = []
        for r in self._runden:
            if erlaubt.get(r.wert.mark, False):
                heraus.append(r)
            else:
                schluessel = f"{senke}:{r.wert.mark.value}"
                self.gefiltert[schluessel] = self.gefiltert.get(schluessel, 0) + 1
        return heraus

    def zaehler(self) -> dict[str, Any]:
        self._beschneiden()
        return {"runden": len(self._runden), "fenster": self._fenster,
                "frist_s": self._frist, "gefiltert": dict(self.gefiltert)}

    def leeren(self) -> int:
        anzahl = len(self._runden)
        self._runden = []
        return anzahl


# ---------------------------------------------------------------------------
# T-6.3 -- das Langzeitgedaechtnis
# ---------------------------------------------------------------------------

ART_LANGZEIT = "langzeit"
SENKE_LANG = "langzeitgedaechtnis"

# Die Anweisung muss AUSDRUECKLICH sein. Bewusst eng, aus demselben Grund wie
# beim Bildschirmbezug in T-5.9: eine breite Liste liesse jede Aeusserung als
# Merkbefehl durchgehen, und dann merkte sich das Pet alles, was jemand sagt.
_ANWEISUNG = re.compile(
    r"\b("
    r"merk(?:e)?\s+dir|merk(?:e)?\s+mal|"
    r"behalte?\s+(?:das|dir)|"
    r"notier(?:e|)|vergiss\s+nicht|"
    r"remember\s+(?:that|this)?"
    r")\b", re.IGNORECASE)


class GedaechtnisFehler(ValueError):
    """Der Eintrag darf so nicht ins Langzeitgedaechtnis. Nennt den Grund."""


def anweisung_erkannt(aeusserung: str) -> bool:
    """Steht in dieser Aeusserung ein ausdruecklicher Merkbefehl?"""
    return bool(_ANWEISUNG.search(aeusserung or ""))


class Langzeit:
    """Was bleiben soll. Nur woertlich, nur aus `user_ptt`, nur auf Befehl.

    Die harte Zusage ist die woertliche Spanne, und sie wird GEPRUEFT und
    nicht geglaubt: die Spanne muss eine Teilzeichenkette der Aeusserung sein.
    Eine Zusammenfassung des Modells besteht diese Pruefung nicht -- sie ist
    ja gerade dadurch definiert, dass sie andere Worte benutzt.

    Ohne diese Pruefung waere „speichere ausschliesslich eine woertliche
    Spanne" eine Bitte an den Aufrufer. Der Aufrufer ist hier der Code, der
    Modellausgabe verarbeitet.
    """

    def __init__(self, *, store: Any,
                 uhr: Callable[[], float] = time.time) -> None:
        self._store = store
        self._uhr = uhr
        self.abgelehnt: dict[str, int] = {}

    def _ablehnen(self, grund: str) -> GedaechtnisFehler:
        self.abgelehnt[grund] = self.abgelehnt.get(grund, 0) + 1
        return GedaechtnisFehler(grund)

    def merken(self, spanne: str, *, aeusserung: Any,
               turn_id: str = "") -> int:
        """Eine woertliche Spanne aus einer `user_ptt`-Aeusserung.

        Die Reihenfolge der Pruefungen ist die der abnehmenden Haerte: erst
        die Herkunft (die kann der Aufrufer nicht herbeireden), dann die
        Anweisung, dann die Woertlichkeit.
        """
        markiert = (aeusserung if isinstance(aeusserung, Marked)
                    else Marked.from_wire(aeusserung))
        if markiert.mark is not Mark.USER_PTT:
            # Nicht ueber die Senkentabelle: die erlaubt auch `trusted`, und
            # `trusted` ist unter anderem der Hub selbst. Ein Pet, das sich
            # merkt, was sein eigener Hub gesagt hat, merkt sich seine
            # eigenen Vermutungen.
            raise self._ablehnen(
                f"nur aus user_ptt, nicht aus {markiert.mark.value}")
        text = str(markiert.value)
        if not anweisung_erkannt(text):
            raise self._ablehnen("keine ausdrueckliche Anweisung")
        spanne = str(spanne)
        if not spanne.strip():
            raise self._ablehnen("leere Spanne")
        if spanne not in text:
            # Der Kern der Aufgabe. Eine Zusammenfassung besteht das nicht.
            raise self._ablehnen("Spanne steht nicht woertlich in der Aeusserung")

        return int(self._store.schreiben(
            ART_LANGZEIT, Marked(spanne, Mark.USER_PTT),
            turn_id=str(turn_id), ts=self._uhr()))

    # -- Abruf -------------------------------------------------------------

    def auflisten(self, *, hoechstens: int = 200) -> list[dict]:
        return self._store.lesen(ART_LANGZEIT, hoechstens=hoechstens)

    def suchen(self, text: str, *, hoechstens: int = 200) -> list[dict]:
        """Textsuche, kleingeschrieben, ohne Embedding-Stack.

        Gefiltert wird in Python und nicht per SQL-`LIKE`: bei einem Nutzer
        sind das ein paar hundert Zeilen, und ein Suchbegriff, der als Muster
        in die Datenbank wandert, ist eine Angriffsflaeche fuer nichts.
        Wenn das je zu langsam wird, ist FTS5 der naechste Schritt -- und
        dann ist es gemessen und nicht vermutet.
        """
        nadel = str(text).strip().lower()
        if not nadel:
            return []
        return [e for e in self.auflisten(hoechstens=hoechstens)
                if nadel in str(e["wert"].value).lower()]

    def loeschen(self, eintrag_id: int) -> bool:
        """Einzeln. Wer sich etwas merken laesst, muss es auch wieder
        loswerden koennen, ohne alles zu verlieren."""
        return bool(self._store.loeschen(int(eintrag_id)))
