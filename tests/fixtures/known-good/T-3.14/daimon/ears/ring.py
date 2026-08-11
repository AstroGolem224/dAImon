"""T-3.3 — Ringpuffer mit Vorlauf. Fehltreffer hinterlassen nichts.

Zwei Zusagen, und sie ziehen in verschiedene Richtungen: die Silben **vor** dem
Ausloeser muessen erhalten bleiben (also wird staendig mitgeschnitten, auch
ohne Anlass), und ein **abgelehnter** Ausloeser darf nichts hinterlassen (also
muss dieser Mitschnitt verschwinden koennen). Was zwischen beidem steht, ist
kein Zeiger, sondern Speicher.

Warum "verworfen" hier `memset` heisst und nicht `self._n = 0`
----------------------------------------------------------------------------
Ein Ring, der bei einem abgelehnten Ausloeser nur seinen Schreibzeiger
zuruecksetzt, hat die Aufnahme noch. Sie ist bloss nicht mehr adressiert. In
einem Core-Dump steht sie, in einem Debugger steht sie, und wer den Ring
danach nur zur Haelfte neu befuellt, kann sie ueber `vorlauf()` sogar wieder
herausbekommen. Der Task heisst "Fehltreffer hinterlassen **nichts**" -- also
wird der ganze Puffer genullt, in-place, ueber `ctypes.memset` auf die
Mapping-Adresse. Nachweisbar ist das an den Bytes, nicht an einem Zaehler:

    r.schreibe(chunk); mv = r.vorlauf()[0]; r.verwirf()
    assert bytes(memoryview(r._mm)) == b"\\x00" * BYTES     # der ganze Puffer
    assert bytes(mv) == b"\\x00" * len(mv)                  # auch schon
                                                            # ausgegebene Sicht

Die zweite Zeile ist kein Zufall und keine Nachlaessigkeit: `vorlauf()` gibt
`memoryview`s **in** den Puffer heraus, keine Kopien. Ein `verwirf()` erwischt
deshalb auch das, was ein Aufrufer noch in der Hand haelt. Das ist genau
richtig herum -- wer das Audio ueberleben lassen will, muss es ausdruecklich
kopieren, und diese Kopie ist dann seine.

Warum `mlock` auf dem Puffer liegt und `vorlauf()` trotzdem nichts kopiert
----------------------------------------------------------------------------
`mlock` soll verhindern, dass Mikrofonmaterial in den Swap wandert. Wer den
Ring festnagelt und bei jedem Zugriff `bytes(...)` zieht, hat die Kopie auf dem
Python-Heap -- ungelockt, auslagerbar, und der `mlock` schuetzt dann eine
Stelle, an der das Material auch noch steht. Deshalb:

  * `schreibe()` kopiert **hinein** (unvermeidbar -- PortAudio gibt denselben
    Puffer im naechsten Block wieder heraus, siehe capture.py; ohne diese
    Kopie gaebe es keinen Ring),
  * `vorlauf()` kopiert **nicht** -- ein bis zwei `memoryview`-Scheiben auf das
    Mapping. Zwei nur, wenn der Vorlauf ueber die Ringgrenze reicht; ein
    Zusammenkleben waere eine Kopie und ist deshalb nicht drin.

Der Puffer ist ein anonymes `mmap` (`MAP_ANONYMOUS`, kein Dateideskriptor,
seitenausgerichtet -- `mlock` verlangt das nicht mehr zwingend, aber ein
teilweise gelocktes Mapping waere schwer nachzuweisen). Die Adresse holen wir
ueber `ctypes.c_char.from_buffer`; dieses Objekt haelt einen Buffer-Export auf
dem `mmap` und muss vor `mm.close()` wieder weg, sonst wirft Python
`BufferError`.

Schlaegt `mlock` fehl, wird das **gemeldet**, nicht verschwiegen: `MlockFehler`.
Ein stillschweigend ungelockter Ring erfuellt die Zusage nicht, und ein
Warnlog haette dieselbe Wirkung wie kein Log -- die Ohren liefen weiter.
`ulimit -l` ist auf dieser Maschine 8192 KiB, die Unit bekommt
`LimitMEMLOCK=8388608`; 625 KiB passen mit Abstand.

Dass `mlock` **gewirkt** hat, sagt sein Rueckgabewert nur bedingt -- ihn
abzufragen misst den Aufruf, nicht die Wirkung. Nachmessbar ist es in
`/proc/self/smaps` am Feld `Locked:` der Abbildung, dafuer gibt es
`gesperrte_kib()`.

Niemals auf Platte
----------------------------------------------------------------------------
Anonymes Mapping, kein `tempfile`, kein Zwischenspeichern, und in keinem
Logaufruf dieses Moduls stehen Audiodaten -- nur Zaehler und Groessen.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import mmap
import os
from typing import Any

_LOG = logging.getLogger("daimon.ears.ring")

# Dieselben Zahlen wie capture.py (T-3.1) und vad.py (T-3.2). Ein Chunk ist
# 512 Samples int16 = 1024 Byte = 32,0 ms.
RATE = 16000
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2
CHUNK_MS = 1000.0 * CHUNK_SAMPLES / RATE  # 32,0

# 20 s aus dem Plan. 20 * 16000 * 2 = 640 000 Byte (= 625 KiB); die "640 KB"
# im Plan sind dezimal gemeint und stimmen. 640000 / 1024 = 625 Chunks, geht
# glatt auf -- ein Ring, dessen Groesse kein Vielfaches der Chunkgroesse ist,
# haette einen Rest, in dem Audio stehenbliebe, das kein Chunk mehr adressiert.
SEKUNDEN = 20
CHUNKS = SEKUNDEN * RATE * 2 // CHUNK_BYTES  # 625
BYTES = CHUNKS * CHUNK_BYTES                 # 640 000

# Vorlauf: Vorgabe in der Mitte des Planfensters. 1,25 s / 32 ms = 39,06 -> 39
# Chunks = 1,248 s. Verbindliche Quelle ist config/daimon.toml -> [ears.ring].
VORLAUF_S = 1.25
VORLAUF_S_MIN = 1.0
VORLAUF_S_MAX = 1.5


class ChunkGroesseFehler(ValueError):
    """Ein Chunk hatte nicht exakt 1024 Byte. Wird NICHT aufgefuellt."""


class MlockFehler(OSError):
    """`mlock()` auf den Ring ist gescheitert. Laut, nicht leise."""


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class Ring:
    """Fester Ring ueber 20 s, vorab alloziert, gelockt, nullbar.

    Nicht threadsicher. Der vorgesehene Aufrufer ist der Audio-Callback aus
    T-3.1 (ein Thread) plus der Ausloeser aus demselben Prozess.
    """

    def __init__(self, *, vorlauf_s: float = VORLAUF_S) -> None:
        self.vorlauf_s = float(vorlauf_s)
        if not VORLAUF_S_MIN <= self.vorlauf_s <= VORLAUF_S_MAX:
            _LOG.warning(
                "ears.ring.vorlauf_s=%.3f liegt ausserhalb der Planvorgabe "
                "%.1f--%.1f s.", self.vorlauf_s, VORLAUF_S_MIN, VORLAUF_S_MAX
            )
        # Abrunden: 1,5 s sind 46,875 Chunks; 47 waeren 1,504 s und damit ueber
        # der Obergrenze. Mindestens einer, sonst gaebe ein Vorlauf von 0 dem
        # STT einen leeren Anlauf.
        self.vorlauf_chunks = max(1, min(CHUNKS, int(self.vorlauf_s * 1000.0 / CHUNK_MS)))

        # Vorab alloziert, in einem Stueck, anonym. `mmap` statt `bytearray`,
        # weil nur so die Adresse seitenausgerichtet und die Abbildung in
        # smaps als eigener Eintrag nachweisbar ist.
        #
        # `MAP_PRIVATE` ist hier KEINE Stilfrage, und die Vorgabe ist die
        # falsche. `mmap.mmap(-1, n)` nimmt in CPython `MAP_SHARED`, und ein
        # SHARED anonymes Mapping ist unter Linux **tmpfs-gestuetzt**.
        # Nachgemessen, /proc/self/maps mit der Vorgabe:
        #
        #   7ffb92d63000-7ffb92e00000 rw-s 00000000 00:01 10409 /dev/zero (deleted)
        #
        # -- also `s` statt `p`, eine Inode-Nummer und ein Dateiname. Der
        # Ring haette damit ein Rueckschreibziel gehabt, und "schreibt
        # niemals auf Platte" waere von der Frage abgehaengt, ob jemand
        # /dev/shm auf Platte auslagert. Mit `MAP_PRIVATE`:
        #
        #   7feba2d5f000-7feba2e00000 rw-p 00000000 00:00 0
        self._mm = mmap.mmap(-1, BYTES, flags=mmap.MAP_PRIVATE)
        self._ref = ctypes.c_char.from_buffer(self._mm)
        self.adresse = ctypes.addressof(self._ref)
        self._libc = _libc()
        self.gesperrt = False

        rc = self._libc.mlock(ctypes.c_void_p(self.adresse), ctypes.c_size_t(BYTES))
        if rc != 0:
            err = ctypes.get_errno()
            self._freigeben()
            raise MlockFehler(
                err,
                f"mlock({BYTES} Byte) fehlgeschlagen: {os.strerror(err)}. "
                f"Ohne Lock kann Mikrofonmaterial in den Swap wandern; "
                f"RLIMIT_MEMLOCK pruefen (ulimit -l, LimitMEMLOCK der Unit).",
            )
        self.gesperrt = True

        # Extra, kein Kriterium: der Ring soll auch dann nicht in einem
        # Core-Dump landen, wenn der Prozess abstuerzt, bevor jemand
        # `verwirf()` rufen konnte. Bestenfalls-Versuch -- MADV_DONTDUMP gibt
        # es nicht auf jedem Kernel, und sein Fehlen macht keine Zusage kaputt.
        try:
            self._mm.madvise(mmap.MADV_DONTDUMP)
        except (AttributeError, OSError):  # pragma: no cover - kernelabhaengig
            pass

        self._n = 0          # Chunks seit dem letzten verwirf()/Start, monoton
        self.verworfen = 0   # Diagnose: wie oft genullt wurde

    # -- schreiben ---------------------------------------------------------

    def schreibe(self, chunk: Any) -> None:
        """Einen Chunk in den Ring. Die einzige Kopie im Modul, und sie muss sein.

        `chunk` ist bytes, ein numpy-int16-Array aus T-3.1 oder irgendein
        Buffer -- Hauptsache exakt 1024 Byte. Zu kurz ist ein Aufrufer-Fehler
        und wird gemeldet statt aufgefuellt: Stille im Ring wuerde spaeter als
        Vorlauf ausgeliefert und dem VAD eine Pause vortaeuschen, die es nie
        gab (dieselbe Begruendung wie in vad.py).
        """
        roh = memoryview(chunk).cast("B")
        if roh.nbytes != CHUNK_BYTES:
            raise ChunkGroesseFehler(
                f"{roh.nbytes} Byte statt {CHUNK_BYTES} "
                f"({CHUNK_SAMPLES} Samples, 16 kHz, mono, int16)."
            )
        if self._mm is None:
            raise ValueError("Ring ist geschlossen")
        pos = (self._n % CHUNKS) * CHUNK_BYTES
        # Slice-Zuweisung auf das mmap schreibt direkt in die gelockte
        # Abbildung; es entsteht kein Zwischenobjekt mit dem Audio darin.
        self._mm[pos:pos + CHUNK_BYTES] = roh
        self._n += 1

    # -- lesen -------------------------------------------------------------

    def vorlauf(self, sekunden: float | None = None) -> list[memoryview]:
        """Die letzten `vorlauf_s` Sekunden -- als Sicht, nicht als Kopie.

        Ein oder zwei Scheiben, in zeitlicher Reihenfolge. Zwei genau dann,
        wenn der Vorlauf ueber die Ringgrenze laeuft; sie zu einem `bytes`
        zusammenzukleben waere eine ungelockte Kopie des Materials und ist
        deshalb Sache des Aufrufers, der sie wirklich braucht.

        ponytail: keine Klasse, kein Iterator-Protokoll, kein Zusammenfuegen.
        Obergrenze: sobald ein zweiter Aufrufer neben T-3.8 (STT) daran haengt
        und beide zusammenhaengende Bytes brauchen, gehoert eine Methode
        `in_puffer(ziel)` her, die in einen vom Aufrufer gestellten (und dann
        von ihm zu loeschenden) Puffer schreibt.
        """
        k = self.vorlauf_chunks if sekunden is None else max(
            1, min(CHUNKS, int(float(sekunden) * 1000.0 / CHUNK_MS))
        )
        k = min(k, self._n, CHUNKS)
        if k <= 0:
            return []
        start = (self._n - k) % CHUNKS
        mv = memoryview(self._mm)
        if start + k <= CHUNKS:
            return [mv[start * CHUNK_BYTES:(start + k) * CHUNK_BYTES]]
        erste = CHUNKS - start
        return [
            mv[start * CHUNK_BYTES:CHUNKS * CHUNK_BYTES],
            mv[0:(k - erste) * CHUNK_BYTES],
        ]

    # -- verwerfen ---------------------------------------------------------

    def verwirf(self) -> None:
        """Abgelehnter Ausloeser: der **ganze** Ring wird genullt.

        Nicht nur der belegte Teil -- der belegte Teil ist der, den dieser
        Ring seit dem letzten `verwirf()` beschrieben hat, und was davor drin
        stand, steht sonst weiter drin. `memset` auf die Mapping-Adresse
        arbeitet in-place; ein `self._mm[:] = b"\\x00" * BYTES` haette
        640 000 Byte Nullen auf dem Heap erzeugt (harmlos im Inhalt, aber
        unnoetig) und ist der Weg, auf dem jemand spaeter versehentlich Audio
        durch den Heap schickt.
        """
        # `memset` schreibt roh auf eine Adresse und wuerde nach `schliessen()`
        # in fremden oder freigegebenen Speicher schreiben -- das ist der
        # einzige Zugriff hier, den Python nicht selbst abfaengt (mmap-Slices
        # und memoryview werfen von sich aus ValueError).
        if self._mm is None:
            raise ValueError("Ring ist geschlossen")
        ctypes.memset(self.adresse, 0, BYTES)
        self._n = 0
        self.verworfen += 1

    # -- Diagnose ----------------------------------------------------------

    def gesperrte_kib(self) -> int:
        """Wieviel dieser Abbildung der Kernel tatsaechlich gelockt haelt.

        Nicht "mlock hat 0 geliefert", sondern das Feld `Locked:` aus
        /proc/self/smaps fuer die Abbildung, in deren Bereich `self.adresse`
        faellt. Erwartet werden 625 KiB. Liefert -1, wenn smaps nicht lesbar
        ist -- das ist dann keine Aussage, weder in die eine noch in die
        andere Richtung.

        Gesucht wird ueber Enthaltensein, nicht ueber Gleichheit der
        Anfangsadresse: der Kernel darf benachbarte VMAs zusammenlegen, und
        eine Pruefung auf "Zeile beginnt genau bei unserer Adresse" waere
        dann still rot, ohne dass am Lock etwas fehlte.
        """
        if self._mm is None:
            return -1
        try:
            text = open("/proc/self/smaps", "r").read()
        except OSError:  # pragma: no cover - kernel ohne smaps
            return -1
        treffer = False
        for zeile in text.splitlines():
            kopf = zeile.split(" ", 1)[0]
            if kopf.endswith(":"):
                if treffer and kopf == "Locked:":
                    return int(zeile.split()[1])
            elif "-" in kopf:
                a, _, b = kopf.partition("-")
                try:
                    treffer = int(a, 16) <= self.adresse < int(b, 16)
                except ValueError:
                    treffer = False
        return -1

    def zustand(self) -> dict:
        return {
            "bytes": BYTES,
            "chunks": CHUNKS,
            "sekunden": SEKUNDEN,
            "chunk_bytes": CHUNK_BYTES,
            "geschrieben": self._n,
            "gefuellt": min(self._n, CHUNKS),
            "vorlauf_s": self.vorlauf_s,
            "vorlauf_chunks": self.vorlauf_chunks,
            "gesperrt": self.gesperrt,
            "gesperrte_kib": self.gesperrte_kib(),
            "verworfen": self.verworfen,
            "offen": self._mm is not None,
            "adresse": self.adresse,
        }

    # -- Lebensende --------------------------------------------------------

    def schliessen(self) -> None:
        """Nullen, entsperren, freigeben -- in dieser Reihenfolge.

        Nullen zuerst: `munmap` gibt die Seiten an den Kernel zurueck, und was
        dort steht, ist beim Zurueckgeben noch da. Idempotent.

        Wirft `BufferError`, wenn noch eine `vorlauf()`-Sicht offen ist -- das
        ist Pythons Schutz und keiner, den wir wegnehmen sollten: er verhindert
        genau den Zugriff auf freigegebenen Speicher.
        """
        if self._mm is None:
            return
        ctypes.memset(self.adresse, 0, BYTES)
        self._libc.munlock(ctypes.c_void_p(self.adresse), ctypes.c_size_t(BYTES))
        self.gesperrt = False
        self._freigeben()

    def _freigeben(self) -> None:
        # `from_buffer` haelt einen Export auf dem mmap; ohne dieses `del`
        # wirft `mm.close()` BufferError, und der Ring bliebe abgebildet.
        self._ref = None
        self._mm.close()
        self._mm = None

    def __enter__(self) -> "Ring":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.schliessen()


def einstellungen(cfg: Any) -> dict[str, float]:
    """`[ears.ring]` aus einer `daimon.common.config.Config` holen."""
    return {"vorlauf_s": float(cfg.get("ears.ring.vorlauf_s", VORLAUF_S))}
