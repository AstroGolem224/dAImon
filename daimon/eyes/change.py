"""T-5.5 -- die Gatterkette: billig filtern, nebenlaeufige Ergebnisse ordnen.

Die Reihenfolge ist die aus Design 4.4 und keine Geschmacksfrage:

    Ausloeser -> Anwendungs-Denylist -> DRM -> Idle/Lock
    -> Zuschnitt aufs fokussierte Fenster
    -> Textregionen -> Zuschnitt auf deren Vereinigung
    -> Signatur ueber den ganzen Zuschnitt, Luma auf 32 Stufen (px >> 3)
    -> unveraendert? verwerfen

**Die Denylist steht VOR allem anderen.** Ein Passwortmanager wird nicht
erfasst und danach verworfen -- er wird gar nicht erst erfasst. Wer erst
zuschneidet und dann prueft, hat die Pixel schon im Speicher gehabt.

**Kein gekacheltes dHash.** Screenpipe hat den Ansatz zweimal gebaut und
verworfen. Ein 4x4-Raster ueber 160x90 ergibt auf 1440p Kacheln von rund
640x360 echten Pixeln; eine geaenderte Textzeile ist darin eine Stoerung unter
einem Prozent und kippt oft kein einziges Bit. Der Fehler waere still, und ein
stiller Fehler ist der schlimmste.

**Der Zuschnitt auf die Vereinigung der Textregionen bringt nichts, und das
wird hier gemessen statt verschwiegen.** Spike T--1.10 hat auf einem Vollbild
97 % (dicht) bis 99 % (spaerlich) Abdeckung gemessen -- der Zuschnitt ist ein
No-Op. Die Akzeptanzliste von T-5.5 verlangt die Stufe trotzdem; sie ist
gebaut, und jeder Befund traegt ihre `abdeckung` mit. Wer die Zahl liest,
sieht den Widerspruch, statt ihn zu erben. Der echte Gewinn ist der Zuschnitt
aufs fokussierte FENSTER: 277--350 ms statt 3300--4200 ms fuers Vollbild.

**Jeder Frame traegt eine Generationsnummer, und die Ordnung wird erzwungen.**
Die Stufen dahinter -- OCR, VLM -- brauchen Hunderte von Millisekunden und
laufen nebenlaeufig. Ohne Generation kann das Ergebnis eines alten Frames nach
dem eines neuen eintreffen und den Kontextspeicher zurueckdrehen. Der
`Ordner` verwirft solche Nachzuegler, statt sie einzusortieren: ein
Bildschirm von vorhin ist keine Beobachtung mehr.

**Der Ausschnitt wird KOPIERT, nicht referenziert.** Ein `numpy`-Blick zeigt
auf den Frame-Puffer, und den zieht PipeWire wieder ein. Wer den Blick
weiterreicht, gibt einen Zeiger auf Speicher weiter, der sich unter ihm
aendert -- und das faellt erst auf, wenn ein VLM etwas beschreibt, das nie auf
dem Bildschirm stand.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np

from daimon.eyes import regionen

# Warum ein Frame nicht weiterging. Leer heisst: er ging weiter.
GRUND_DENYLIST = "denylist"
GRUND_DRM = "drm"
GRUND_TOR = "tor"
GRUND_UNVERAENDERT = "unveraendert"
GRUND_KEIN_TEXT = "kein_text"
GRUND_STARR = "starr"

# Die BILLIGE Vorstufe aus Design 4.4, die in der ersten Fassung dieses Moduls
# fehlte: ein Graustufenbild auf 160x90 und ein Vergleich mit dem vorigen.
# Design 13 veranschlagt genau diesen Posten mit 0,001 % ("Bildschirm-Diff,
# 2-s-Takt") -- ohne ihn laeuft jede Runde sofort in den teuren Teil, und
# gemessen am 13.08. kostete der Dienst 6 % statt der veranschlagten 1,2 %.
#
# Die Schwelle ist ABSICHTLICH streng. Ein grober Vergleich, der „aehnlich
# genug" sagt, ist genau der Fehler, an dem das gekachelte dHash gescheitert
# ist -- er verpasst eine geaenderte Textzeile still. Hier wird deshalb nur
# verworfen, was WIRKLICH unveraendert ist: kein einziger der 14400 Punkte
# weicht um mehr als DIFF_SCHWELLE ab. Das filtert den haeufigen Fall
# (nichts passiert) und keinen einzigen anderen.
DIFF_BREITE, DIFF_HOEHE = 160, 90
DIFF_SCHWELLE = 6


@dataclass(frozen=True)
class Fenster:
    """Das fokussierte Fenster, wie der Watcher aus T-0.12 es meldet."""

    x: int
    y: int
    breite: int
    hoehe: int
    klasse: str = ""
    drm: bool = False


@dataclass(frozen=True)
class Befund:
    generation: int
    veraendert: bool
    grund: str = ""
    signatur: str = ""
    # Die Region, die der VLM in NATIVER Aufloesung bekommt -- in
    # Bildschirmkoordinaten, nicht in denen des Zuschnitts. Wer den Bezug
    # verliert, beschreibt die richtige Stelle des falschen Bildschirms.
    region: tuple[int, int, int, int] | None = None
    ausschnitt: np.ndarray | None = None
    abdeckung: float = 0.0
    boxen: int = 0
    kosten: dict[str, float] = field(default_factory=dict)


def _verkleinern(rgb: np.ndarray) -> np.ndarray:
    """Auf 160x90 Graustufen. Nearest-Neighbour ueber Indexfelder.

    Keine Mittelung: eine Mittelung glaettet gerade die duennen Striche weg,
    auf die es ankommt -- und dann meldet der Diff „unveraendert", waehrend
    eine Textzeile sich geaendert hat.
    """
    hoehe, breite = rgb.shape[:2]
    ys = (np.arange(DIFF_HOEHE) * (hoehe / DIFF_HOEHE)).astype(np.int64)
    xs = (np.arange(DIFF_BREITE) * (breite / DIFF_BREITE)).astype(np.int64)
    return regionen.graustufen(rgb[ys][:, xs])


class Kette:
    """Die Gatterkette. Vergibt Generationen und misst jede Stufe."""

    def __init__(self, *, tor: Callable[[], bool],
                 denylist: Iterable[str] = (),
                 uhr: Callable[[], float] = time.perf_counter) -> None:
        # Kleingeschrieben verglichen: KWin meldet `org.kde.konsole`, der
        # Nutzer schreibt `Konsole`. Ein Vergleich, der daran scheitert, laesst
        # genau die Anwendung durch, die der Nutzer ausschliessen wollte.
        self._denylist = {s.strip().lower() for s in denylist if s.strip()}
        self._tor = tor
        self._uhr = uhr
        self._generation = 0
        self._letzte_signatur = ""
        self._letztes_klein: np.ndarray | None = None

    def _naechste_generation(self) -> int:
        self._generation += 1
        return self._generation

    def _abgewiesen(self, gen: int, grund: str, kosten: dict) -> Befund:
        return Befund(generation=gen, veraendert=False, grund=grund,
                      kosten=kosten)

    def verarbeiten(self, rgb: np.ndarray, fenster: Fenster) -> Befund:
        """Ein Frame durch die Kette. Gibt IMMER einen Befund zurueck.

        Immer -- auch bei Abweisung. Ein `None` waere von „nicht gelaufen"
        nicht zu unterscheiden, und dann liesse sich nicht mehr sagen, ob die
        Denylist gegriffen hat oder der Dienst stand.
        """
        gen = self._naechste_generation()
        kosten: dict[str, float] = {}

        t = self._uhr()
        if fenster.klasse.strip().lower() in self._denylist:
            kosten[GRUND_DENYLIST] = self._uhr() - t
            return self._abgewiesen(gen, GRUND_DENYLIST, kosten)
        kosten[GRUND_DENYLIST] = self._uhr() - t

        t = self._uhr()
        if fenster.drm:
            kosten[GRUND_DRM] = self._uhr() - t
            return self._abgewiesen(gen, GRUND_DRM, kosten)
        kosten[GRUND_DRM] = self._uhr() - t

        t = self._uhr()
        offen = self._tor()
        kosten[GRUND_TOR] = self._uhr() - t
        if not offen:
            return self._abgewiesen(gen, GRUND_TOR, kosten)

        # -- Der billige Diff: 160x90, vor allem anderen Teuren -----------
        t = self._uhr()
        klein = _verkleinern(rgb)
        starr = (self._letztes_klein is not None
                 and int(np.abs(klein.astype(np.int16)
                                - self._letztes_klein.astype(np.int16)).max())
                 <= DIFF_SCHWELLE)
        self._letztes_klein = klein
        kosten["diff"] = self._uhr() - t
        if starr:
            return self._abgewiesen(gen, GRUND_STARR, kosten)

        # -- Zuschnitt aufs fokussierte Fenster: der eigentliche Gewinn -----
        t = self._uhr()
        hoehe, breite = rgb.shape[0], rgb.shape[1]
        x0 = max(0, min(fenster.x, breite))
        y0 = max(0, min(fenster.y, hoehe))
        x1 = max(x0, min(fenster.x + fenster.breite, breite))
        y1 = max(y0, min(fenster.y + fenster.hoehe, hoehe))
        fensterbild = rgb[y0:y1, x0:x1]
        grau = regionen.graustufen(fensterbild) if fensterbild.size else \
            np.zeros((0, 0), dtype=np.uint8)
        kosten["fensterzuschnitt"] = self._uhr() - t
        if grau.size == 0:
            return self._abgewiesen(gen, GRUND_KEIN_TEXT, kosten)

        # -- Textregionen --------------------------------------------------
        t = self._uhr()
        kaesten = regionen.textregionen(grau)
        kosten["textregionen"] = self._uhr() - t
        if not kaesten:
            return self._abgewiesen(gen, GRUND_KEIN_TEXT, kosten)

        # -- Zuschnitt auf die Vereinigung (laut T--1.10 ein No-Op) ---------
        t = self._uhr()
        v = regionen.vereinigung(kaesten)
        vx, vy, vw, vh = v
        zuschnitt = grau[vy:vy + vh, vx:vx + vw]
        abdeckung = (vw * vh) / grau.size if grau.size else 0.0
        kosten["vereinigungszuschnitt"] = self._uhr() - t

        # -- Signatur ueber den GANZEN Zuschnitt, Luma auf 32 Stufen --------
        t = self._uhr()
        signatur = hashlib.blake2b((zuschnitt >> 3).tobytes(),
                                   digest_size=16).hexdigest()
        kosten["signatur"] = self._uhr() - t

        if signatur == self._letzte_signatur:
            return self._abgewiesen(gen, GRUND_UNVERAENDERT, kosten)
        self._letzte_signatur = signatur

        return Befund(
            generation=gen, veraendert=True, signatur=signatur,
            # Bildschirmkoordinaten: Fensterversatz plus Versatz im Fenster.
            region=(x0 + vx, y0 + vy, vw, vh),
            # KOPIE. Ein Blick zeigte auf den Frame-Puffer, den PipeWire
            # wieder einzieht.
            ausschnitt=np.ascontiguousarray(
                fensterbild[vy:vy + vh, vx:vx + vw]).copy(),
            abdeckung=abdeckung, boxen=len(kaesten), kosten=kosten)


class Ordner:
    """Laesst nur Ergebnisse durch, die neuer sind als das letzte angenommene.

    Ohne das kann ein langsames OCR-Ergebnis eines alten Frames nach dem eines
    neuen eintreffen und den Kontextspeicher zurueckdrehen. Was verworfen
    wird, wird gezaehlt -- ein stiller Verwurf sieht aus wie ein Ergebnis, das
    nie kam.
    """

    def __init__(self) -> None:
        self._hoechste = 0
        self.verworfen = 0

    def annehmen(self, generation: int) -> bool:
        if generation <= self._hoechste:
            self.verworfen += 1
            return False
        self._hoechste = generation
        return True
