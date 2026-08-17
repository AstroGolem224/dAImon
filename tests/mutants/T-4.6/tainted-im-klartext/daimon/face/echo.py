"""Echo-Referenz fuer die Rueckkopplungssperre -- die Senderseite.

T-3.9 Kriterium 8, zweite Haelfte (Vertrag:
UMBRA-Notes/DDs/dAImon/Echo-Referenz-Plan.md): der TTS-Dienst schickt genau
die Bytes, die an die Wiedergabe gehen, als 16-kHz-mono-int16 an den
Ohren-Dienst. Der vergleicht sie in `interlock.Sperre` per Kreuzkorrelation
mit dem, was das Mikrofon hoert.

SOCK_DGRAM mit Absicht: kein Verbindungszustand im TTFA-kritischen
Sprech-Thread, kein haengender Schreiber, ein verlorenes Paket ist ein
verlorenes Paket. Und BEST EFFORT mit Absicht: ein toter Ohren-Dienst darf
die Stimme um nichts verzoegern -- jeder Fehler wird geschluckt, hoechstens
eine Logzeile je Prozess.
"""
from __future__ import annotations

import base64
import json
import socket

# Ein pw-cat-Block ist <= 32 KiB roh; ein langes sherpa-Segment kann aber
# mehrere Sekunden tragen. Gesendet wird deshalb in Stuecken, die sicher
# unter der Datagramm-Obergrenze des Empfaengers (200 000 Bytes) UND unter
# dem Socket-Sendepuffer bleiben. 96 000 Bytes PCM sind 3 s Audio und
# 128 000 Bytes base64.
STUECK = 96_000

ZIEL_RATE = 16000


def nach_16k(pcm: bytes, rate: int) -> bytes:
    """int16-mono-PCM auf 16 kHz, blockweise lineare Interpolation.

    ponytail: blockweises Resampling verliert den Bruchteil-Versatz an den
    Blockgrenzen -- fuer die Korrelations-Stolperdrahtlage belanglos, ein
    echter AEC braucht einen zustandsbehafteten Resampler.
    """
    pcm = pcm[: len(pcm) & ~1]
    if not pcm:
        return b""
    if rate == ZIEL_RATE:
        return pcm
    import numpy as np

    x = np.frombuffer(pcm, dtype="<i2")
    n = x.size * ZIEL_RATE // int(rate)
    if n == 0:
        return b""
    idx = np.linspace(0.0, x.size - 1, n)
    return np.interp(idx, np.arange(x.size), x.astype("float32")) \
        .astype("<i2").tobytes()


class EchoSender:
    def __init__(self, pfad, log=None) -> None:
        self.pfad = str(pfad)
        self._sock: socket.socket | None = None
        self._log = log
        self._gemeldet = False

    def senden(self, pcm: bytes, rate: int) -> None:
        try:
            daten = nach_16k(pcm, int(rate))
            if not daten:
                return
            if self._sock is None:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self._sock.setblocking(False)
            for i in range(0, len(daten), STUECK):
                nutz = json.dumps({
                    "v": 1, "art": "echo", "rate": ZIEL_RATE,
                    "pcm": base64.b64encode(
                        daten[i:i + STUECK]).decode("ascii"),
                }).encode("utf-8")
                self._sock.sendto(nutz, self.pfad)
        except (OSError, ValueError):
            if not self._gemeldet and self._log is not None:
                self._gemeldet = True
                self._log.info("Echo-Referenz nicht zustellbar",
                               DAIMON_ACTION="echo_weg")
