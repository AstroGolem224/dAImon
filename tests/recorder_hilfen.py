"""Gemeinsame Starthilfe fuer die Recorder-Pruefstaende.

WARUM NICHT AUF DIE DATEI WARTEN. `ipc.listen` bindet, setzt den Modus und
horcht danach -- die Socketdatei existiert also schon nach `bind()`, und ein
`connect()` in dem Fenster dazwischen scheitert mit ECONNREFUSED. Wer auf
`pfad.exists()` wartet, hat einen Pruefstand, der unter Last flattert.

Genau dieser Fehler steht schon einmal in der Historie dieses Repos
(`4378d96`, „test_hub_push wartete auf die Socketdatei statt auf eine
Verbindung"). Deshalb steht die Hilfe hier EINMAL und nicht dreimal
abgeschrieben.
"""
from __future__ import annotations

import socket
import threading
import time

from daimon.common import ipc


def starte(dienst, *, frist_s: float = 5.0) -> threading.Thread:
    """Dienst in EINEM Faden starten und warten, bis er ANNIMMT.

    Ein Faden fuer `start()` und `lauf()` zusammen: der Dienst ist einfaedig,
    und seine SQLite-Verbindung gehoert dem Faden, der sie geoeffnet hat.
    """
    def fahren() -> None:
        dienst.start()
        dienst.lauf()

    faden = threading.Thread(target=fahren, daemon=True)
    faden.start()

    pfad = str(ipc.socket_path(dienst.runtime_dir, "recorder"))
    frist = time.monotonic() + frist_s
    while time.monotonic() < frist:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(0.5)
        try:
            c.connect(pfad)
        except OSError:
            time.sleep(0.02)
            continue
        finally:
            c.close()
        return faden
    raise AssertionError(f"Dienst nimmt nach {frist_s} s keine Verbindung an")
