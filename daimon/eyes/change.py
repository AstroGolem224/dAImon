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

# Der Diff weiss nicht nur OB, sondern WO. Diese Auskunft wurde in der ersten
# Fassung weggeworfen -- OCR lief danach auf dem ganzen Fenster, gemessen am
# 13.08. mit 57,6 % eines Kerns.
#
# Gepolstert wird grosszuegig: eine Textzeile reicht ueber die geaenderten
# Punkte hinaus, und ein zu enger Ausschnitt schneidet Buchstaben an. Zwei
# Diff-Punkte sind auf 5120x1440 rund 64 Pixel je Seite.
DIFF_POLSTER = 2

# UND ALLE N GEAENDERTEN RUNDEN EIN VOLLDURCHGANG. Ohne ihn bliebe eine
# Aenderung, die unter DIFF_SCHWELLE lag, fuer immer ungelesen -- genau der
# stille Fehler, an dem das gekachelte dHash gescheitert ist. Der
# Volldurchgang kostet einmal, was sonst jede Runde kostete, und er macht den
# Unterschied zwischen "billiger" und "unvollstaendig".
VOLL_JEDE = 20


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


def _aenderungsbereich(neu: np.ndarray, alt: np.ndarray, breite: int,
                       hoehe: int) -> tuple[int, int, int, int] | None:
    """Wo sich etwas geaendert hat, in VOLLBILD-Koordinaten. `None` = nirgends.

    Ein Kasten um ALLE geaenderten Punkte, nicht je Punkt einer: zwei Kaesten
    bedeuten zwei OCR-Aufrufe, und die Festkosten je Aufruf sind gemessen
    60 ms. Ein Kasten, der beide umschliesst, ist fast immer billiger.
    """
    weicht = np.abs(neu.astype(np.int16) - alt.astype(np.int16)) > DIFF_SCHWELLE
    if not weicht.any():
        return None
    zeilen = np.flatnonzero(weicht.any(axis=1))
    spalten = np.flatnonzero(weicht.any(axis=0))
    y0 = max(0, int(zeilen[0]) - DIFF_POLSTER)
    y1 = min(DIFF_HOEHE, int(zeilen[-1]) + 1 + DIFF_POLSTER)
    x0 = max(0, int(spalten[0]) - DIFF_POLSTER)
    x1 = min(DIFF_BREITE, int(spalten[-1]) + 1 + DIFF_POLSTER)
    fx, fy = breite / DIFF_BREITE, hoehe / DIFF_HOEHE
    return (int(x0 * fx), int(y0 * fy),
            max(1, int((x1 - x0) * fx)), max(1, int((y1 - y0) * fy)))


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


def _kennungen() -> dict:
    """Die `.desktop`-Zuordnung, oder leer. Lokal importiert."""
    try:
        from daimon.recorder.redaktion import desktop_kennungen
        return desktop_kennungen()
    except Exception:      # noqa: BLE001
        return {}


def gesperrt(klasse, denylist, kennungen):
    """Weiterleitung auf die EINE Denylist-Entscheidung.

    Lokal importiert wie die uebrigen recorder-Aufrufe im
    Augendienst: `change.py` wird auch ohne den Recorder geladen.
    """
    from daimon.recorder.redaktion import gesperrt as _gesperrt
    return _gesperrt(klasse, denylist, kennungen)


class Kette:
    """Die Gatterkette. Vergibt Generationen und misst jede Stufe."""

    def __init__(self, *, tor: Callable[[], bool],
                 denylist: Iterable[str] = (),
                 kennungen: dict[str, str] | None = None,
                 uhr: Callable[[], float] = time.perf_counter) -> None:
        # Kleingeschrieben verglichen: KWin meldet `org.kde.konsole`, der
        # Nutzer schreibt `Konsole`. Ein Vergleich, der daran scheitert, laesst
        # genau die Anwendung durch, die der Nutzer ausschliessen wollte.
        #
        # `kennungen` seit dem 18.08.: die rohe Klasse allein genuegt NICHT.
        # `config/redaktion.yaml` fuehrt `.desktop`-Kennungen, und diese
        # Stufe verglich dagegen die Klasse, wie KWin sie meldet -- ein
        # gelistetes Fenster lief damit durch den Diff und ins OCR. Gemessen
        # von T-7.2 am 18.08. (K8). Die Entscheidung faellt jetzt in
        # `recorder.redaktion.gesperrt`, an einer Stelle fuer alle drei.
        self._denylist = {s.strip().lower() for s in denylist if s.strip()}
        # `None` heisst LADEN, nicht `ohne` -- dieselbe Politik wie in
        # `Redaktion`. Wer die Kette direkt baut (ein Verifizierer tut
        # das), bekommt sonst still die schwaechere Pruefung, und genau
        # daran ist der Fix vom 18.08. im ersten Anlauf gescheitert.
        # 6,6 ms je Aufruf, gemessen -- einmal je Dienststart.
        self._kennungen = (_kennungen() if kennungen is None
                           else kennungen)
        self._tor = tor
        self._uhr = uhr
        self._generation = 0
        self._letzte_signatur = ""
        self._letztes_klein: np.ndarray | None = None
        self._seit_voll = 0

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
        if gesperrt(fenster.klasse, self._denylist, self._kennungen):
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
        # Gezaehlt wird JEDE Runde, nicht nur die geaenderten. Der erste
        # Entwurf zaehlte nur geaenderte -- und damit bewachte der Waechter
        # nichts: gemessen am 13.08. blieb ein Block, der sich je Runde um 5
        # aenderte, bei DIFF_SCHWELLE=6 dauerhaft „starr", 43 von 45 Runden.
        # Eine langsam driftende Aenderung waere so nie gelesen worden, und
        # das ist genau der stille Fehler, gegen den der Volldurchgang da ist.
        self._seit_voll += 1
        faellig = self._seit_voll >= VOLL_JEDE

        bereich = None
        if self._letztes_klein is not None and not faellig:
            bereich = _aenderungsbereich(klein, self._letztes_klein,
                                         rgb.shape[1], rgb.shape[0])
            if bereich is None:
                self._letztes_klein = klein
                kosten["diff"] = self._uhr() - t
                return self._abgewiesen(gen, GRUND_STARR, kosten)
        self._letztes_klein = klein
        kosten["diff"] = self._uhr() - t

        if bereich is None:
            # Volldurchgang: faellig, oder der allererste Frame.
            self._seit_voll = 0

        # -- Zuschnitt aufs fokussierte Fenster: der eigentliche Gewinn -----
        t = self._uhr()
        hoehe, breite = rgb.shape[0], rgb.shape[1]
        x0 = max(0, min(fenster.x, breite))
        y0 = max(0, min(fenster.y, hoehe))
        x1 = max(x0, min(fenster.x + fenster.breite, breite))
        y1 = max(y0, min(fenster.y + fenster.hoehe, hoehe))
        if bereich is not None:
            # Schnittmenge aus Fenster UND Aenderung. Was sich ausserhalb des
            # fokussierten Fensters bewegt hat -- eine Uhr in der Leiste --
            # geht diesen Dienst nichts an.
            bx, by, bw, bh = bereich
            nx0, ny0 = max(x0, bx), max(y0, by)
            nx1, ny1 = min(x1, bx + bw), min(y1, by + bh)
            if nx1 > nx0 and ny1 > ny0:
                x0, y0, x1, y1 = nx0, ny0, nx1, ny1
            else:
                kosten["fensterzuschnitt"] = self._uhr() - t
                return self._abgewiesen(gen, GRUND_STARR, kosten)
        fensterbild = rgb[y0:y1, x0:x1]
        grau = regionen.graustufen(fensterbild) if fensterbild.size else \
            np.zeros((0, 0), dtype=np.uint8)
        kosten["fensterzuschnitt"] = self._uhr() - t
        if grau.size == 0:
            return self._abgewiesen(gen, GRUND_KEIN_TEXT, kosten)

        # -- Textregionen: NUR im Volldurchgang ----------------------------
        #
        # Auf dem Aenderungsausschnitt ist die Erkennung ueberfluessig UND
        # schaedlich. Ueberfluessig, weil der Diff schon weiss, wo etwas
        # passiert ist. Schaedlich, weil der Formfilter alles verwirft, was
        # mehr als die halbe Flaeche fuellt (MAX_AREA_FRACTION) -- und auf
        # einem engen Ausschnitt fuellt die geaenderte Zeile genau das. Am
        # 13.08. gemessen: `kein_text` auf einem Ausschnitt, in dem
        # ausschliesslich Text stand.
        if bereich is None:
            t = self._uhr()
            kaesten = regionen.textregionen(grau)
            kosten["textregionen"] = self._uhr() - t
            if not kaesten:
                return self._abgewiesen(gen, GRUND_KEIN_TEXT, kosten)

            t = self._uhr()
            v = regionen.vereinigung(kaesten)
            vx, vy, vw, vh = v
            zuschnitt = grau[vy:vy + vh, vx:vx + vw]
            abdeckung = (vw * vh) / grau.size if grau.size else 0.0
            kosten["vereinigungszuschnitt"] = self._uhr() - t
        else:
            kaesten = []
            vx = vy = 0
            vh, vw = grau.shape[:2]
            zuschnitt = grau
            abdeckung = 1.0

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
