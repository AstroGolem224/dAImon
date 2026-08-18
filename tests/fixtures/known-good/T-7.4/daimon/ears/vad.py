"""T-3.2 — Sprachsegmentierung mit asymmetrischer Hysterese.

Die Zusage dieses Moduls ist eine einzige: **Wortenden werden nicht
abgeschnitten.** Alles andere hier ist Mittel zum Zweck.

Deutsche Endsilben sind leise. "…und dann ging er" faellt zum Schluss in der
Lautstaerke ab, und ein Detektor mit EINEM Schwellwert -- oben rein, unten raus
-- kappt genau dort: aus "ging" wird "g". Deshalb sind Einsatz und Ende hier
verschieden, und das Ende hat zusaetzlich einen Nachlauf. Eingesetzt wird bei
>= 0,5, beendet erst, wenn die Wahrscheinlichkeit 300--500 ms **durchgehend**
unter ~0,35 liegt -- und der Nachlauf selbst gehoert noch ins Segment. Der
Nachlauf ist keine Vorsicht und kein Puffer gegen Zittern; er IST die Zusage.
Wer ihn auf null zieht oder `ende` auf `einsatz` hebt, hat den Task entfernt,
nicht konfiguriert.

Warum Zustandsmaschine und Modell hier getrennt sind, und warum das keine
Architekturvorliebe ist
----------------------------------------------------------------------------
`pysilero-vad` ist ein Modell, kein Schwellwertdetektor. Ein synthetischer Ton
ist fuer Silero **keine Sprache**. Selbst nachgemessen am 02.08. mit
`pysilero-vad==3.4.0`, je ein Chunk: Stille 0,0017, weisses Rauschen (sigma
3000) 0,0306, Sinus 440 Hz bei Amplitude 8000 **0,0240**. `einsatz` ist 0,5 --
das ist keine knappe Sache, das ist Faktor zwanzig. Ein Test, der einen Sinus
hineinschiebt und
zwei Segmente erwartet, findet null Segmente -- und ist nur dadurch gruen zu
bekommen, dass man `einsatz` so weit senkt, bis Rauschen als Sprache durchgeht.
Damit waere die Zusage von T-3.2 zerstoert worden, um den eigenen Test von
T-3.2 zu bestehen.

Also nimmt `segmentieren()` eine **Folge von Wahrscheinlichkeiten** entgegen,
kein Audio. Der 200-ms/800-ms-Fall ist damit deterministisch und misst die
Zustandsmaschine, sonst nichts: kein Modell, kein Zufall, keine Hardware. Die
Modellanbindung (`Erkenner`) wird getrennt geprueft -- 512 Samples, 16 kHz,
int16. Wer beides in einen Test mischt, kann hinterher nicht sagen, ob die
Hysterese oder das Modell schuld war.

Warum ein falsch grosser Chunk ein Fehler ist und nicht stillschweigend
aufgefuellt wird
----------------------------------------------------------------------------
Auffuellen mit Stille erzeugt genau den Schaden, den dieses Modul verhindern
soll: ein halb gefuellter Chunk am Wortende ist zur Haelfte Stille, ergibt eine
niedrige Wahrscheinlichkeit, startet den Nachlauf frueher -- und schneidet die
Endsilbe ab. Ein zu kleiner Chunk ist ein Aufrufer-Fehler und wird als solcher
gemeldet. T-3.1 liefert ohnehin exakt 512.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Iterator, Sequence

_LOG = logging.getLogger("daimon.ears.vad")

# Silero v5/v6: die Fenstergroesse ist fest, nicht eingestellt. 512/16000 sind
# 32,0 ms -- dieselben Werte wie in daimon/ears/capture.py (T-3.1).
RATE = 16000
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2  # int16
CHUNK_MS = 1000.0 * CHUNK_SAMPLES / RATE  # 32,0

# Vorgaben; die verbindliche Quelle ist config/daimon.toml -> [ears.vad].
EINSATZ = 0.5
ENDE = 0.35
NACHLAUF_MS = 400.0

# Die Grenzen aus dem Plan. Keine Willkuer, sondern das, was den Task ausmacht.
EINSATZ_MIN = 0.5
NACHLAUF_MS_MIN = 300.0
NACHLAUF_MS_MAX = 500.0

# ponytail: ein Segment ist ein Paar von Chunk-Indizes, beide einschliesslich --
# keine Klasse, keine Zeitstempel, keine Audiodaten. Obergrenze: sobald ein
# Aufrufer das Audio selbst braucht (T-3.3 Ringpuffer, T-3.8 STT), bekommt der
# Ringpuffer die Indizes gereicht und schneidet; erst wenn ZWEI Aufrufer daran
# haengen, lohnt ein Dataclass mit `beginn_s`/`ende_s`. Sekunden bekommt man
# heute mit `index * CHUNK_MS / 1000`.
Segment = tuple[int, int]


class ChunkGroesseFehler(ValueError):
    """Ein Chunk hatte nicht exakt 512 Samples. Wird NICHT aufgefuellt."""


class Hysterese:
    """Die Zustandsmaschine. Frisst Wahrscheinlichkeiten, liefert Segmente.

    Zwei Zustaende: still und im Segment. Der Uebergang hinein ist eine einzige
    Wahrscheinlichkeit >= `einsatz`; der Uebergang heraus braucht
    `nachlauf_chunks` **aufeinanderfolgende** Werte < `ende`. Jeder Wert >=
    `ende` setzt den Zaehler zurueck -- auch einer zwischen `ende` und
    `einsatz`, der ein Segment nie starten koennte. Das ist die Asymmetrie:
    hineinzukommen ist schwer, drinzubleiben leicht.
    """

    def __init__(
        self,
        *,
        einsatz: float = EINSATZ,
        ende: float = ENDE,
        nachlauf_ms: float = NACHLAUF_MS,
        chunk_ms: float = CHUNK_MS,
    ) -> None:
        self.einsatz = float(einsatz)
        self.ende = float(ende)
        self.nachlauf_ms = float(nachlauf_ms)
        # Aufrunden, nicht abrunden: 400 ms sind 12,5 Chunks, und 12 Chunks
        # waeren 384 ms -- unter der Vorgabe. Lieber 416 ms.
        self.nachlauf_chunks = max(1, math.ceil(self.nachlauf_ms / chunk_ms))
        _pruefe_grenzen(self.einsatz, self.ende, self.nachlauf_ms)
        self._start: int | None = None
        self._leise = 0
        self._i = -1

    def schritt(self, p: float) -> Segment | None:
        """Eine Wahrscheinlichkeit hinein, hoechstens ein fertiges Segment heraus."""
        self._i += 1
        if self._start is None:
            if p >= self.einsatz:
                self._start = self._i
                self._leise = 0
            return None
        if p < self.ende:
            self._leise += 1
            if self._leise >= self.nachlauf_chunks:
                # Das Segment endet HIER, am letzten Chunk des Nachlaufs -- nicht
                # dort, wo die Wahrscheinlichkeit gefallen ist. Genau diese
                # Nachlauf-Chunks enthalten die leise Endsilbe. Wer hier auf
                # `self._i - self.nachlauf_chunks` zurueckschneidet, hat den
                # Nachlauf zwar formal, wirft sein Ergebnis aber weg.
                fertig = (self._start, self._i)
                self._start = None
                self._leise = 0
                return fertig
        else:
            self._leise = 0
        return None

    def abschluss(self) -> Segment | None:
        """Der Strom ist zu Ende, das Segment noch offen -- dann gilt es trotzdem.

        Es wegzuwerfen hiesse, das letzte Wort abzuschneiden; und das letzte
        Wort ist bei Push-to-Talk das haeufigste.
        """
        if self._start is None:
            return None
        fertig = (self._start, self._i)
        self._start = None
        self._leise = 0
        return fertig


def _pruefe_grenzen(einsatz: float, ende: float, nachlauf_ms: float) -> None:
    """Meckert, statt abzuweisen -- aber es steht im Journal.

    Abweisen waere haerter und war die erste Fassung. Dagegen spricht ein
    einziger, praktischer Punkt: eine Konfiguration, die den Ohren-Dienst beim
    Start umbringt, faellt als "daimon-ears startet nicht" auf und wird durch
    Loeschen der Datei behoben -- die Begruendung liest niemand. Eine Warnung
    mit den Zahlen darin ueberlebt den Vorfall.
    """
    if einsatz < EINSATZ_MIN:
        _LOG.warning(
            "ears.vad.einsatz=%.3f liegt unter der Planvorgabe %.2f -- damit "
            "kann Rauschen als Sprache durchgehen.", einsatz, EINSATZ_MIN
        )
    if not ende < einsatz:
        _LOG.warning(
            "ears.vad.ende=%.3f >= einsatz=%.3f -- die Hysterese ist damit "
            "symmetrisch und schneidet leise Endsilben ab.", ende, einsatz
        )
    if nachlauf_ms < NACHLAUF_MS_MIN or nachlauf_ms > NACHLAUF_MS_MAX:
        _LOG.warning(
            "ears.vad.nachlauf_ms=%.0f liegt ausserhalb der Planvorgabe "
            "%.0f--%.0f ms.", nachlauf_ms, NACHLAUF_MS_MIN, NACHLAUF_MS_MAX
        )


def einstellungen(cfg: Any) -> dict[str, float]:
    """`[ears.vad]` aus einer `daimon.common.config.Config` holen."""
    return {
        "einsatz": float(cfg.get("ears.vad.einsatz", EINSATZ)),
        "ende": float(cfg.get("ears.vad.ende", ENDE)),
        "nachlauf_ms": float(cfg.get("ears.vad.nachlauf_ms", NACHLAUF_MS)),
    }


def segmentieren(
    wahrscheinlichkeiten: Iterable[float],
    *,
    einsatz: float = EINSATZ,
    ende: float = ENDE,
    nachlauf_ms: float = NACHLAUF_MS,
) -> list[Segment]:
    """Wahrscheinlichkeitsfolge hinein, Segmente heraus. **Kein Audio.**

    Das ist die pruefbare Oberflaeche der Zusage: deterministisch, ohne Modell,
    ohne Mikrofon. Wer Audio hat, dreht es vorher mit `Erkenner` durch.
    """
    h = Hysterese(einsatz=einsatz, ende=ende, nachlauf_ms=nachlauf_ms)
    segmente = [s for p in wahrscheinlichkeiten if (s := h.schritt(p))]
    rest = h.abschluss()
    if rest is not None:
        segmente.append(rest)
    return segmente


class Erkenner:
    """Silero, mit exakt 512 Samples je Aufruf.

    Der Detektor ist einspeisbar, damit die Chunk-Regel ohne das Modell
    pruefbar ist -- und weil das Laden des Modells sonst jeden Import dieses
    Moduls verteuert.
    """

    def __init__(self, detektor: Any = None) -> None:
        if detektor is None:
            # Spaeter Import: die Zustandsmaschine oben laeuft auf jeder
            # Maschine, auch ohne pysilero-vad.
            from pysilero_vad import SileroVoiceActivityDetector

            detektor = SileroVoiceActivityDetector()
            if detektor.chunk_samples() != CHUNK_SAMPLES:
                raise ChunkGroesseFehler(
                    f"pysilero erwartet {detektor.chunk_samples()} Samples, "
                    f"dieses Modul liefert {CHUNK_SAMPLES}"
                )
        self._detektor = detektor

    def wahrscheinlichkeit(self, chunk: Any) -> float:
        """Ein Chunk (bytes oder numpy-int16 aus T-3.1) -> [0..1].

        Zu kurz oder zu lang ist ein Fehler. Siehe Modulkopf: still auffuellen
        wuerde leise Wahrscheinlichkeiten erfinden und Wortenden abschneiden.
        """
        roh = chunk.tobytes() if hasattr(chunk, "tobytes") else bytes(chunk)
        if len(roh) != CHUNK_BYTES:
            raise ChunkGroesseFehler(
                f"{len(roh) // 2} Samples statt {CHUNK_SAMPLES} "
                f"(16 kHz, mono, int16). Nicht aufgefuellt -- ein halb "
                f"gefuellter Chunk am Wortende schneidet es ab."
            )
        return float(self._detektor(roh))

    def wahrscheinlichkeiten(self, chunks: Iterable[Any]) -> Iterator[float]:
        return (self.wahrscheinlichkeit(c) for c in chunks)


def segmentiere_audio(
    chunks: Sequence[Any],
    *,
    erkenner: Erkenner | None = None,
    **schwellen: float,
) -> list[Segment]:
    """Die beiden Haelften zusammengesteckt -- mehr ist es nicht.

    ponytail: absichtlich duenn und ohne eigenen Zustand. Obergrenze: wer
    Streaming mit Rueckblick braucht (T-3.3), nimmt `Erkenner` und `Hysterese`
    direkt und haelt sie selbst -- diese Funktion ist fuer den geschlossenen
    Fall "hier ist alles Audio".
    """
    e = erkenner or Erkenner()
    return segmentieren(e.wahrscheinlichkeiten(chunks), **schwellen)
