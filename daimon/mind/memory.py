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
