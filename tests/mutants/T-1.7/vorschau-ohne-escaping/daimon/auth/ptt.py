"""Gut-Muster fuer T-1.7 Teil 2: der Push-to-Talk-Umschaltautomat.

Design 2.4: **Push-to-Talk ist eine Umschaltung, kein Halten.**
`kglobalaccel` liefert keine verlaesslichen Loslass-Ereignisse -- ein Automat,
der auf das Loslassen wartet, bleibt irgendwann haengen.

Das Zeitlimit ist deshalb kein Komfort, sondern der einzige Weg, ein offenes
Mikrofonfenster wieder zu schliessen. Ein PTT, das versehentlich anbleibt,
waere ein Dauermitschnitt -- und den schliesst 1.1 ausdruecklich aus.

Reines stdlib, kein `gi`. Der Automat ist von der Tastenbindung getrennt,
damit er ohne Plasma-Sitzung pruefbar ist.
"""

from __future__ import annotations

import time


class _Stumm:
    def _nichts(self, *args, **kwargs):
        return None

    info = warning = error = debug = _nichts


class PTTAutomat:
    def __init__(self, *, zeitlimit_s: float = 120.0,
                 jetzt=time.monotonic, log=None) -> None:
        if zeitlimit_s <= 0:
            raise ValueError("zeitlimit_s muss positiv sein")
        self._zeitlimit = float(zeitlimit_s)
        self._jetzt = jetzt
        self._log = log or _Stumm()
        self._seit: float | None = None

    def _abgelaufen(self) -> bool:
        return (self._seit is not None
                and self._jetzt() - self._seit >= self._zeitlimit)

    def umschalten(self) -> bool:
        """Kippt den Zustand und gibt den NEUEN zurueck. Ein abgelaufener
        Automat gilt als aus -- die naechste Umschaltung schaltet also AN,
        nicht aus. Sonst braeuchte es zwei Tastendruecke, um nach einem
        Ablauf wieder zu sprechen, und das faellt niemandem ein."""
        if self.ist_aktiv():
            self._seit = None
            self._log.info("ptt aus", DAIMON_TYP="ptt", DAIMON_HANDLUNG="aus")
            return False
        self._seit = self._jetzt()
        self._log.info("ptt an", DAIMON_TYP="ptt", DAIMON_HANDLUNG="an")
        return True

    def ist_aktiv(self) -> bool:
        """Rechnet, statt nachzufragen -- eine Auskunft veraendert nichts."""
        return self._seit is not None and not self._abgelaufen()

    def aus(self) -> None:
        if self._seit is not None:
            self._seit = None
            self._log.info("ptt aus", DAIMON_TYP="ptt",
                           DAIMON_HANDLUNG="aus")

    def restsekunden(self) -> float:
        if not self.ist_aktiv():
            return 0.0
        return max(0.0, self._zeitlimit - (self._jetzt() - self._seit))
