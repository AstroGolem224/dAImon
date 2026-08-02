"""T-3.1: Aufnahme mit hartem Lebenszyklus.

MUTANT: stop() statt close().

PIPEWIRE_LATENCY steht VOR dem Import von sounddevice -- das PipeWire-
ALSA-Plugin liest die Variable, wenn das PCM geoeffnet wird, und die
Reihenfolge ist die Zusage der Akzeptanzliste.
"""
import os

os.environ.setdefault("PIPEWIRE_LATENCY", "512/16000")

import sounddevice as sd  # noqa: E402

RATE = 16000
BLOCK = 512
KANAELE = 1
DTYPE = "int16"
DEVICE = "pipewire"  # NICHT "default": der Weg soll benannt sein.


class Aufnahme:
    """start() oeffnet, stop() ZERSTOERT."""

    def __init__(self) -> None:
        self._strom = None
        self._blocks = 0
        self._offen = False

    def _block(self, daten, rahmen, zeit, status) -> None:
        self._blocks += 1

    def start(self) -> None:
        if self._strom is not None:
            return
        strom = sd.InputStream(
            device=DEVICE,
            samplerate=RATE,
            channels=KANAELE,
            dtype=DTYPE,
            blocksize=BLOCK,
            callback=self._block,
        )
        strom.start()
        self._strom = strom
        self._offen = True

    def stop(self) -> None:
        """MUTANT: stop() statt close(). Das Objekt ueberlebt."""
        self._offen = False
        if self._strom is not None:
            self._strom.stop()


    def zustand(self) -> dict:
        return {
            "offen": self._offen,
            "blocks": self._blocks,
            "rate": RATE,
            "kanaele": KANAELE,
            "dtype": DTYPE,
            "device": DEVICE,
        }
