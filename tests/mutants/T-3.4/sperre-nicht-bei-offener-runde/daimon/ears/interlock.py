"""T-3.4: Rueckkopplungssperre.

MUTANT: bei gedruecktem PTT wird durchgelassen.

Die eigene Stimme darf sich nicht selbst reaktivieren. Unter Plan C (PTT statt
Wake-Word) ist das gefaehrlicher als vorher: der Nutzer haelt die Taste, das
Pet antwortet, und seine eigene Antwort landet als Nutzeraeusserung im STT.

Drei Mechanismen, und sie sind ausdruecklich nicht dasselbe:

  * WAEHREND der Wiedergabe wird gesperrt -- unabhaengig vom PTT-Zustand.
  * NACH der Wiedergabe noch NACHLAUF_MS lang, weil der Raumhall der letzten
    Silbe das Mikrofon spaeter erreicht als das Ende der Wiedergabe.
  * DANACH faengt die ECHO-REFERENZ, was trotzdem noch ankommt: der
    Ausgabepuffer wird eingespeist und mit dem Aufgenommenen verglichen.

Alle Fristen laufen ueber `time.monotonic()`. Eine NTP-Korrektur oder eine
Zeitumstellung darf eine Sperre nicht aufheben; die Wanduhr kann das nicht
zusagen, die monotone Uhr schon.

FAIL-CLOSED: ist der Zustand unklar -- Wiedergabe angemeldet, aber nie
beendet --, dann sperrt die Sperre. Die Kosten sind eine verlorene
Aeusserung, die der Nutzer wiederholt. Die Kosten der Gegenrichtung sind eine
Rueckkopplungsschleife.

THREADSICHER: die Sperre wird aus zwei Richtungen beruehrt -- dem
Audio-Callback (liest, oft) und der Wiedergabe (schreibt, selten). `ring.py`
traegt "nicht threadsicher" im Modulkopf; diese Entscheidung gehoert laut
Akzeptanzliste hierher, und sie faellt auf ein RLock.
"""
from __future__ import annotations

import logging
import threading
import time

NACHLAUF_MS = 500.0
# Wie lange ein eingespeister Ausgabepuffer als Referenz gilt. Danach ist der
# Raumhall vorbei und eine alte Referenz wuerde nur noch Fehlalarme erzeugen.
REFERENZ_FENSTER_S = 5.0

_log = logging.getLogger(__name__)


class Rueckkopplungssperre:
    """Entscheidet fuer jeden Chunk: geht er zur Transkription oder nicht."""

    def __init__(self, *, nachlauf_ms=NACHLAUF_MS,
                 referenz_fenster_s=REFERENZ_FENSTER_S):
        self.nachlauf_s = float(nachlauf_ms) / 1000.0
        self.referenz_fenster_s = float(referenz_fenster_s)
        self._lock = threading.RLock()
        self._laeuft = 0          # angemeldete, noch nicht beendete Wiedergaben
        self._bis = None          # monotone Frist des Nachlaufs
        self._ptt = False
        self._referenzen = []     # [(monotone Zeit, bytes)]

    # -- die Uhr, an einer einzigen Stelle ---------------------------------
    @staticmethod
    def _jetzt():
        return time.monotonic()

    # -- Wiedergabe --------------------------------------------------------
    def wiedergabe_beginnt(self):
        with self._lock:
            self._laeuft += 1
            self._bis = None

    def wiedergabe_endet(self):
        with self._lock:
            if self._laeuft > 0:
                self._laeuft -= 1
            if self._laeuft == 0:
                self._bis = self._jetzt() + self.nachlauf_s


    # -- PTT ---------------------------------------------------------------
    def ptt(self, gedrueckt):
        """Der PTT-Zustand wird MITGETEILT, nicht abgefragt -- und er oeffnet
        nichts. Er steht hier ausschliesslich fuer die Diagnose."""
        with self._lock:
            self._ptt = bool(gedrueckt)

    # -- Echo-Referenz -----------------------------------------------------
    def referenz(self, puffer):
        """Der Ausgabepuffer der Sprachausgabe. Weil TTS noch fehlt (T-3.9),
        wird er eingespeist; die Schnittstelle bleibt dieselbe."""
        roh = bytes(memoryview(puffer).cast("B"))
        with self._lock:
            self._referenzen.append((self._jetzt(), roh))
            self._verfallen()

    def _verfallen(self):
        grenze = self._jetzt() - self.referenz_fenster_s
        self._referenzen = [(t, b) for (t, b) in self._referenzen if t >= grenze]

    def _ist_echo(self, roh):
        self._verfallen()
        for _, ref in self._referenzen:
            if ref and (ref in roh or roh in ref):
                return True
        return False


    # -- Zustand -----------------------------------------------------------
    def _grund(self):
        if self._laeuft > 0:
            return "wiedergabe"
        if self._bis is not None and self._jetzt() < self._bis:
            return "nachlauf"
        return None

    def gesperrt(self):
        with self._lock:
            return self._grund() is not None

    # -- Die eine Entscheidung, auf die es ankommt -------------------------
    def durchlassen(self, chunk):
        """True = dieser Chunk geht zur Transkription."""
        roh = bytes(memoryview(chunk).cast("B"))
        with self._lock:
            # MUTANT: bei gedruecktem PTT wird durchgelassen. Der Nutzer
            # "hat ja die Taste gehalten, also will er reden" -- und das Pet
            # hoert sich selbst zu. `gesperrt()` und `diagnose()` melden
            # weiter brav "gesperrt": genau darum ist die Diagnose kein
            # Messmittel.
            if self._ptt:
                return True

            if self._grund() is not None:
                return False
            if self._ist_echo(roh):
                return False
            return True

    # -- Diagnose ----------------------------------------------------------
    def diagnose(self):
        """Beleg, keine Messgrundlage. Wer hieran misst, ob gesperrt ist,
        misst den Selbstbericht -- Fall 9 in HANDOVER.md."""
        with self._lock:
            return {
                "gesperrt": self._grund() is not None,
                "grund": self._grund(),
                "bis": self._bis,
                "ptt": self._ptt,
                "wiedergaben_offen": self._laeuft,
                "referenzen": len(self._referenzen),
            }
