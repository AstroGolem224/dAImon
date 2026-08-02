"""T-3.3: Ringpuffer mit Vorlauf.

MUTANT: Verwerfen setzt den Schreibzeiger, nullt aber nicht.

20 Sekunden bei 16 kHz int16 sind 640 000 Byte, vorab alloziert und mit
`mlock()` festgenagelt: Mikrofonmaterial darf nicht in den Swap. Der Puffer
wird IN PLACE beschrieben; herausgereicht wird nur der Vorlauf.

Und die eigentliche Zusage: FEHLTREFFER HINTERLASSEN NICHTS. Wer beim
abgelehnten Ausloeser bloss den Schreibzeiger zuruecksetzt, hat die Aufnahme
noch im Speicher -- nicht mehr adressiert, aber auffindbar. Also wird
genullt.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import mmap
import os

RATE = 16000
CHUNK = 512
DTYPE = "int16"
BREITE = 2                                # int16
CHUNK_BYTES = CHUNK * BREITE              # 1024
CHUNK_MS = 1000.0 * CHUNK / RATE          # exakt 32,0 ms
SEKUNDEN = 20
RING_BYTES = SEKUNDEN * RATE * BREITE     # 640 000 (dezimal, so steht es im Plan)
RING_CHUNKS = RING_BYTES // CHUNK_BYTES   # 625

# Plankorridor 1,0..1,5 s. Vorgabe in der Mitte.
VORLAUF_MS = 1250.0
VORLAUF_MIN_MS = 1000.0
VORLAUF_MAX_MS = 1500.0

_log = logging.getLogger(__name__)


class MlockFehler(RuntimeError):
    """mlock() hat nicht gegriffen. Das ist laut zu melden, nicht zu schlucken."""


class ChunkFehler(ValueError):
    """Ein Chunk hatte nicht exakt CHUNK Samples."""


_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class Ringpuffer:
    """Fester Ring ueber anonymem mmap. Kein Dateideskriptor, keine Platte."""

    def __init__(self, *, sekunden=SEKUNDEN, vorlauf_ms=VORLAUF_MS,
                 sperren=True):
        self.bytes_gesamt = int(sekunden) * RATE * BREITE
        self.chunks = self.bytes_gesamt // CHUNK_BYTES
        # Vorab alloziert: die volle Groesse steht vor dem ersten Chunk.
        self.puffer = mmap.mmap(-1, self.bytes_gesamt)
        self._sicht = memoryview(self.puffer)
        self.adresse = ctypes.addressof(ctypes.c_char.from_buffer(self.puffer))
        self.vorlauf_ms = float(vorlauf_ms)
        self.vorlauf_chunks = int(self.vorlauf_ms / CHUNK_MS)
        self._schreib = 0
        self._gefuellt = 0
        self.gesperrt = False
        if sperren:
            rc = _libc.mlock(ctypes.c_void_p(self.adresse),
                             ctypes.c_size_t(self.bytes_gesamt))
            if rc != 0:
                err = ctypes.get_errno()
                _log.error("mlock(%d Byte) fehlgeschlagen: errno=%d (%s) -- "
                           "der Ring laege im Swap", self.bytes_gesamt, err,
                           os.strerror(err))
                raise MlockFehler(
                    "mlock(%d Byte) fehlgeschlagen: errno=%d (%s). "
                    "RLIMIT_MEMLOCK pruefen (LimitMEMLOCK in der Unit)."
                    % (self.bytes_gesamt, err, os.strerror(err)))
            self.gesperrt = True

        if not (VORLAUF_MIN_MS <= self.vorlauf_ms <= VORLAUF_MAX_MS):
            _log.warning("vorlauf_ms=%s liegt ausserhalb des Plankorridors "
                         "%s..%s", self.vorlauf_ms, VORLAUF_MIN_MS,
                         VORLAUF_MAX_MS)

    # -- Schreiben ---------------------------------------------------------
    def schreibe(self, chunk):
        """Ein Chunk, in place. Keine Zwischenkopie, keine Liste."""
        roh = memoryview(chunk).cast("B")
        if roh.nbytes != CHUNK_BYTES:
            raise ChunkFehler(
                "Chunk hat %d Byte, erwartet sind %d (%d Samples int16)"
                % (roh.nbytes, CHUNK_BYTES, CHUNK))
        off = self._schreib * CHUNK_BYTES
        self._sicht[off:off + CHUNK_BYTES] = roh
        self._schreib = (self._schreib + 1) % self.chunks
        if self._gefuellt < self.chunks:
            self._gefuellt += 1

    # T-3.1 reicht Bloecke ueber einen `senke`-Callback herein.
    def senke(self, chunk):
        self.schreibe(chunk)

    __call__ = senke

    # -- Ausloesen ---------------------------------------------------------
    def ausloesen(self) -> bytes:
        """Der Vorlauf VOR dem Ausloesezeitpunkt, aelteste zuerst."""
        n = min(self.vorlauf_chunks, self._gefuellt)
        stuecke = []
        for k in range(n, 0, -1):
            idx = (self._schreib - k) % self.chunks
            off = idx * CHUNK_BYTES
            stuecke.append(bytes(self._sicht[off:off + CHUNK_BYTES]))
        return b"".join(stuecke)

    # -- Verwerfen ---------------------------------------------------------
    def verwerfen(self):
        """Abgelehnter Ausloeser: der Ring wird GENULLT, nicht vergessen."""
        # MUTANT: nur der Schreibzeiger. Die Bytes stehen weiter da.
        self._schreib = 0
        self._gefuellt = 0


    # Ein abgelehnter Ausloeser ist derselbe Vorgang -- nur der Name, unter dem
    # ein Aufrufer ihn sucht, ist ein anderer.
    ablehnen = verwerfen
    leeren = verwerfen
