"""T-3.2: Segmentierung mit asymmetrischer Hysterese.

Minimale KORREKTE Fassung, vom Reviewer blind gegen die Akzeptanzliste\ngeschrieben. NICHT der Liefergegenstand.

Die Zusage lautet: WORTENDEN WERDEN NICHT ABGESCHNITTEN. Deshalb ist die
Hysterese asymmetrisch -- Einsatz bei >= 0,5, Ende erst, wenn die
Wahrscheinlichkeit `nachlauf_ms` lang unter `ende` (~0,35) liegt. Ein
symmetrischer Schwellwert macht aus "...und dann GING er" ein "...und dann g".

`segmentieren()` nimmt eine Folge entgegen, deren Elemente entweder
Wahrscheinlichkeiten (float) oder Audio-Chunks (bytes/bytearray/ndarray)
sind. Das ist keine Bequemlichkeit: nur so ist die Zustandsmaschine
deterministisch pruefbar, ohne ein Modell zu befragen, das synthetische
Signale nicht fuer Sprache haelt.
"""
from __future__ import annotations

import logging

RATE = 16000
CHUNK = 512
DTYPE = "int16"
CHUNK_MS = 1000.0 * CHUNK / RATE          # exakt 32,0 ms
CHUNK_BYTES = CHUNK * 2                   # int16

EINSATZ = 0.5
ENDE = 0.35
NACHLAUF_MS = 400                         # Plankorridor: 300..500

# Vorgaben des Plans. Eine Konfiguration darunter ist nicht verboten, aber
# sie kippt die Zusage -- also wird sie benannt, nicht verschwiegen.
MIN_EINSATZ = 0.5
MIN_NACHLAUF_MS = 300
MAX_NACHLAUF_MS = 500

_log = logging.getLogger(__name__)


class ChunkFehler(ValueError):
    """Ein Chunk hatte nicht exakt {CHUNK} Samples."""


def _det():
    from pysilero_vad import SileroVoiceActivityDetector
    return SileroVoiceActivityDetector()


def _pruefe_schwellen(einsatz, ende, nachlauf_ms):
    if einsatz < MIN_EINSATZ:
        _log.warning(
            "einsatz=%s liegt unter der Planvorgabe %s -- damit geht Rauschen "
            "als Sprache durch", einsatz, MIN_EINSATZ)
    if ende >= einsatz:
        _log.warning(
            "ende=%s liegt nicht unter einsatz=%s -- die Hysterese ist damit "
            "symmetrisch und schneidet Wortenden ab", ende, einsatz)
    if nachlauf_ms < MIN_NACHLAUF_MS:
        _log.warning(
            "nachlauf_ms=%s liegt unter der Planvorgabe %s -- Wortenden "
            "werden abgeschnitten", nachlauf_ms, MIN_NACHLAUF_MS)
    if nachlauf_ms > MAX_NACHLAUF_MS:
        _log.warning(
            "nachlauf_ms=%s liegt ueber der Planvorgabe %s -- Segmente "
            "schleppen Stille mit", nachlauf_ms, MAX_NACHLAUF_MS)


def _roh(element) -> bytes:
    """Genau CHUNK Samples, sonst Fehler. KEIN stilles Auffuellen.

    Ein aufgefuellter Chunk ist eine Luege gegenueber dem Modell: es bekaeme
    Stille, die nie aufgenommen wurde, und der Aufrufer erfuehre nie, dass
    seine Blockgroesse nicht passt. Silero arbeitet ausschliesslich in
    512-Sample-Schritten; wer das nicht einhaelt, hat einen Fehler in der
    Verdrahtung -- und Fehler in der Verdrahtung gehoeren laut, nicht leise.
    """
    roh = bytes(memoryview(element).cast("B"))
    if len(roh) != CHUNK_BYTES:
        raise ChunkFehler(
            f"Chunk hat {len(roh)} Bytes ({len(roh) // 2} Samples), "
            f"erwartet sind {CHUNK_BYTES} ({CHUNK} Samples int16 bei "
            f"{RATE} Hz)")
    return roh


def _wahrscheinlichkeit(zustand, element):
    """float -> direkt. Alles andere -> durch den Detektor."""
    if isinstance(element, float) or (
            isinstance(element, int) and not isinstance(element, bool)):
        return float(element)
    roh = _roh(element)
    if zustand["det"] is None:
        zustand["det"] = _det()
    return float(zustand["det"](roh))


def schwellen_aus_config(cfg) -> dict:
    """Liest [ears.vad] aus einer geladenen Konfiguration (T-0.5)."""
    return {
        "einsatz": float(cfg.get("ears.vad.einsatz", EINSATZ)),
        "ende": float(cfg.get("ears.vad.ende", ENDE)),
        "nachlauf_ms": int(cfg.get("ears.vad.nachlauf_ms", NACHLAUF_MS)),
    }


def segmentieren(folge, *, einsatz=EINSATZ, ende=ENDE,
                 nachlauf_ms=NACHLAUF_MS, detektor=None,
                 chunk_ms=CHUNK_MS) -> list[dict]:
    _pruefe_schwellen(einsatz, ende, nachlauf_ms)
    zustand = {"det": detektor}
    segmente: list[dict] = []
    aktiv = False
    start = 0
    unter = 0
    i = -1
    for i, element in enumerate(folge):
        p = _wahrscheinlichkeit(zustand, element)
        if not aktiv:
            # Einsatz: eine einzige Ueberschreitung genuegt.
            if p >= einsatz:
                aktiv = True
                start = i
                unter = 0
        else:
            # Ende: erst nach `nachlauf_ms` UNUNTERBROCHEN unter `ende`.
            # Der Nachlauf ist keine Vorsicht, er ist die Zusage.
            if p < ende:
                unter += 1
                if unter * chunk_ms >= nachlauf_ms:
                    segmente.append({"start_ms": start * chunk_ms,
                                     "ende_ms": (i - unter + 1) * chunk_ms})
                    aktiv = False
                    unter = 0
            else:
                unter = 0

    if aktiv:
        segmente.append({"start_ms": start * chunk_ms,
                         "ende_ms": (i + 1) * chunk_ms})
    return segmente
