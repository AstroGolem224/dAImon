"""T-1.7 Teil 2 — der Push-to-Talk-Umschaltautomat.

UMSCHALTUNG, NICHT HALTEN
-------------------------
Design 2.4, woertlich: "Push-to-Talk ist eine Umschaltung, kein Halten --
kglobalaccel liefert keine verlaesslichen Loslass-Ereignisse. Zeitlimit als
Rueckfall."

Das Zeitlimit ist keine Bequemlichkeit. Ohne verlaessliches Loslass-Ereignis
gibt es keinen anderen Weg, ein offenes Mikrofonfenster wieder zu schliessen
-- und ein PTT, das versehentlich anbleibt, ist ein Dauermitschnitt, den 1.1
ausdruecklich ausschliesst.

WARUM EIN EIGENES MODUL OHNE GI
-------------------------------
Der Auth-Agent laeuft unter System-Python 3.14 (dort liegt PyGObject), das
Projekt-venv ist auf 3.12 festgenagelt. Dieser Automat ist der Teil, der
ohne Plasma-Sitzung und ohne GTK pruefbar sein MUSS -- deshalb steht hier
reines stdlib, kein `gi`, auch nicht bedingt. Die Tastenbindung gehoert in
`agent.py`, nicht hierher.

ZEIT UND LOG SIND INJIZIERT
---------------------------
`jetzt` ist eine Zeitquelle (Vorgabe `time.monotonic`), `log` ein Logger mit
der Schnittstelle aus `daimon.common.logging` (mindestens `.info`). Ein Test,
der zwei Minuten wartet, wird nie gefahren; ein Automat ohne Audit-Zeilen ist
ein Vorfall ohne Befund.
"""

from __future__ import annotations

import time


class _Stumm:
    """Logger-Ersatz, wenn keiner injiziert ist. Dieselbe Schnittstelle wie
    `daimon.common.logging.Logger`, nur ohne Ausgabe."""

    def _nichts(self, *args: object, **kwargs: object) -> None:
        return None

    info = warning = error = debug = _nichts


class PTTAutomat:
    """Ein Kippschalter mit Zeitlimit.

    Zustaende: aus (`_seit is None`) oder an seit `_seit`. Ein Ablauf des
    Zeitlimits ist kein dritter Zustand und kein Ereignis -- er faellt aus
    der Rechnung in `ist_aktiv`, und zwar jedes Mal gleich. Deshalb gibt es
    auch keine Audit-Zeile "abgelaufen": niemand hat gehandelt, es ist nur
    Zeit vergangen.
    """

    def __init__(self, *, zeitlimit_s: float = 120.0,
                 jetzt=time.monotonic, log=None) -> None:
        if zeitlimit_s <= 0:
            raise ValueError("zeitlimit_s muss positiv sein")
        self._zeitlimit = float(zeitlimit_s)
        self._jetzt = jetzt
        self._log = log if log is not None else _Stumm()
        self._seit: float | None = None

    def umschalten(self) -> bool:
        """Kippt den Zustand und gibt den NEUEN zurueck.

        Zweimal aufgerufen ist wieder aus -- das ist der Unterschied zu
        Halten. Ein abgelaufener Automat gilt als aus: die naechste
        Umschaltung schaltet also AN, nicht aus. Sonst braeuchte es nach
        jedem Ablauf zwei Tastendruecke, um wieder sprechen zu koennen, und
        der erste ginge still verloren.
        """
        if self.ist_aktiv():
            self._seit = None
            self._log.info("ptt aus", DAIMON_TYP="ptt", DAIMON_HANDLUNG="aus")
            return False
        self._seit = self._jetzt()
        self._log.info("ptt an", DAIMON_TYP="ptt", DAIMON_HANDLUNG="an")
        return True

    def ist_aktiv(self) -> bool:
        """False, sobald das Zeitlimit seit der Aktivierung verstrichen ist.

        Fragt nicht nach, sondern rechnet -- eine Auskunft veraendert nichts:
        kein Zustand, keine Audit-Zeile, keine Seiteneffekte.
        """
        return (self._seit is not None
                and self._jetzt() - self._seit < self._zeitlimit)

    def aus(self) -> None:
        """Ausdrueckliches Beenden, etwa nach einer abgeschlossenen Runde.

        Bei bereits inaktivem Automaten harmlos: nichts zu tun, keine
        Audit-Zeile -- es hat ja nichts stattgefunden.
        """
        if self._seit is not None:
            self._seit = None
            self._log.info("ptt aus", DAIMON_TYP="ptt", DAIMON_HANDLUNG="aus")

    def restsekunden(self) -> float:
        """0.0 wenn inaktiv. Fuer die Diagnose."""
        if not self.ist_aktiv():
            return 0.0
        return self._zeitlimit - (self._jetzt() - self._seit)
