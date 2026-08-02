"""T-3.1: Aufnahme mit hartem Lebenszyklus.

MUTANT: das Abraeumen passiert erst NACH der Rueckkehr von stop().

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
import atexit
import threading
import time

VERZOEGERUNG = 8.0


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
        """MUTANT: stop() kehrt zurueck, BEVOR abgeraeumt ist."""
        strom, self._strom = self._strom, None
        self._offen = False
        if strom is None:
            return
        atexit.register(strom.close)
        threading.Thread(target=self._spaeter, args=(strom,), daemon=True).start()

    def _spaeter(self, strom) -> None:
        time.sleep(VERZOEGERUNG)
        try:
            strom.close()
        except Exception:
            pass


    def zustand(self) -> dict:
        return {
            "offen": self._offen,
            "blocks": self._blocks,
            "rate": RATE,
            "kanaele": KANAELE,
            "dtype": DTYPE,
            "device": DEVICE,
        }
