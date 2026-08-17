"""T-5.6 -- Text lesen, und zwar nur dort, wo sich etwas geaendert hat.

Jede Entscheidung hier ist in Spike T--1.10 gemessen und nicht geschaetzt:

**Dauerhafter Arbeitsprozess, kein CLI-Aufruf und kein FFI im eigenen
Prozess.** Alle vier Aufrufarten liegen bei den reinen OCR-Kosten innerhalb
von 15 % beieinander -- die Kosten setzt tesseract, nicht der Aufrufweg. Zwei
Dinge unterscheiden sie doch:

    CLI-Festkosten je Aufruf     59,9 ms  (fork/exec, traineddata, Tempdatei)
    FFI im Prozess mit numpy    ~800 ms   je Vollbild, OpenMP-Koresidenz

Der Arbeitsprozess raeumt beides ab. `daimon/eyes/ocr_worker.py` importiert
deshalb `numpy` NICHT -- das ist der ganze Punkt.

**`tessdata_fast`.** Gemessen auf demselben Zuschnitt: 267,9 ms gegen 545,1 ms
mit dem Standard-tessdata, bei 620 gegen 619 Zeichen. Minus 277 ms fuer einen
Zeichenunterschied -- der groesste einzelne Hebel dieser Aufgabe.

**`OMP_NUM_THREADS=1`.** Vierundzwanzig Threads sind rund 25 % langsamer.

**`--psm 11`.** Nicht nur schneller als 3 und 6, sondern auch ergiebiger:
620 Zeichen gegen 616 und 611 auf demselben Bild.

**Der Zuschnitt ist der des FOKUSSIERTEN FENSTERS.** Nicht die Vereinigung der
Textregionen -- die deckt 97 bis 99 % des Vollbilds ab und spart nichts. Und
nicht die Einzelboxen: 261 Boxen auf einem dichten Bild mal 60 ms Festkosten
waeren 15,7 s gegen 3,3 s fuer einen Durchgang. Zuschnitt 337 ms, Vollbild
3,3 bis 4,7 s.

**Zusammenfassen statt anstauen.** Laeuft schon ein Auftrag fuer dieselbe
Region, wird der aeltere verworfen. Was NICHT geht: einen bereits laufenden
tesseract-Aufruf abbrechen -- der Prozess sitzt in der Bibliothek. Sein
Ergebnis wird deshalb nicht abgebrochen, sondern als ueberholt erkannt: es
traegt die Generation aus T-5.5, und `change.Ordner` verwirft Nachzuegler.
Ein Abbruch, den man behauptet und nicht leisten kann, waere schlimmer als
keiner.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

SPRACHEN = "deu+eng"
PSM = 11
BEREIT = b"BEREIT\n"


class OcrFehler(RuntimeError):
    """Der Arbeitsprozess ist unbrauchbar. Immer mit Grund."""


def tessdata_verzeichnis() -> str:
    """Wo `deu.traineddata` und `eng.traineddata` liegen.

    Das Systemverzeichnis kommt ZULETZT, und das ist Absicht: auf dieser
    Maschine enthaelt `/usr/share/tessdata` nur `afr` und `osd`, und der
    Standard-tessdata waere ausserdem 277 ms langsamer. Die schnellen Daten
    liegen im Benutzerverzeichnis und brauchen keine Wurzelrechte.
    """
    kandidaten = []
    aus_umgebung = os.environ.get("DAIMON_TESSDATA")
    if aus_umgebung:
        kandidaten.append(Path(aus_umgebung))
    kandidaten.append(Path(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"))
        / "daimon" / "tessdata")
    # Der Spike hat die Daten schon geholt. Ihn als Quelle zu nennen ist
    # ehrlicher, als sie stillschweigend ein zweites Mal herunterzuladen.
    kandidaten.append(Path(__file__).resolve().parents[2]
                      / "spikes" / "ocr" / "tessdata")
    kandidaten.append(Path("/usr/share/tessdata"))

    for k in kandidaten:
        if all((k / f"{s}.traineddata").exists() for s in SPRACHEN.split("+")):
            return str(k)
    raise OcrFehler(
        f"kein tessdata mit {SPRACHEN} gefunden. Gesucht in: "
        + ", ".join(str(k) for k in kandidaten)
        + ". `tessdata_fast` holen und nach ~/.local/share/daimon/tessdata "
          "legen, oder DAIMON_TESSDATA setzen.")


class Arbeiter:
    """Ein Prozess, eine lebende `TessBaseAPI`, ein Socketpaar."""

    def __init__(self, *, tessdata: str | None = None,
                 sprachen: str = SPRACHEN, psm: int = PSM) -> None:
        self._tessdata = tessdata or tessdata_verzeichnis()
        self._sprachen = sprachen
        self._psm = psm
        self._sperre = threading.Lock()
        self._prozess: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._datei = None

    def starten(self) -> None:
        eltern, kind = socket.socketpair()
        umgebung = dict(os.environ)
        # Vierundzwanzig Threads sind rund 25 % langsamer als einer.
        umgebung["OMP_NUM_THREADS"] = "1"
        umgebung["TESSDATA_PREFIX"] = self._tessdata
        umgebung["WORKER_FD"] = str(kind.fileno())
        self._prozess = subprocess.Popen(
            [sys.executable, "-m", "daimon.eyes.ocr_worker",
             self._tessdata, self._sprachen, str(self._psm)],
            env=umgebung, pass_fds=(kind.fileno(),))
        kind.close()
        self._sock = eltern
        self._datei = eltern.makefile("rb")
        gruss = self._datei.readline()
        if gruss != BEREIT:
            self.beenden()
            raise OcrFehler(
                f"Arbeiter meldete sich nicht bereit (las {gruss!r}) -- "
                f"tessdata {self._tessdata!r}, Sprachen {self._sprachen!r}")

    def text(self, rgb: bytes, breite: int, hoehe: int) -> str:
        """Blockiert. Genau deshalb laeuft das im Pool und nicht im Aufnahmeweg."""
        if self._sock is None or self._datei is None:
            raise OcrFehler("text() vor starten()")
        # Ein Socketpaar ist ein Strom, kein Nachrichtenkanal: zwei Auftraege
        # gleichzeitig verschraenkten ihre Bytes und lieferten beiden das
        # falsche Ergebnis. Ein Arbeiter bearbeitet einen Auftrag.
        with self._sperre:
            self._sock.sendall(f"{breite} {hoehe} {len(rgb)}\n".encode())
            self._sock.sendall(rgb)
            n = int(self._datei.readline())
            roh = self._datei.read(n)
        return roh.decode("utf-8", errors="replace")

    def beenden(self) -> None:
        try:
            if self._sock is not None:
                self._sock.sendall(b"-1 -1 -1\n")
        except OSError:
            pass
        if self._prozess is not None:
            try:
                self._prozess.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._prozess.kill()
        for x in (self._datei, self._sock):
            try:
                x.close()
            except Exception:
                pass
        self._prozess = self._sock = self._datei = None


class Pool:
    """Mehrere Arbeiter, Auftraege je Region zusammengefasst.

    Der Schluessel ist die Region und nicht der Frame: derselbe Fensterbereich
    zweimal hintereinander ist zweimal dieselbe Frage, und die aeltere Antwort
    interessiert niemanden mehr.
    """

    def __init__(self, *, groesse: int = 2, tessdata: str | None = None) -> None:
        self._arbeiter = [Arbeiter(tessdata=tessdata) for _ in range(groesse)]
        for a in self._arbeiter:
            a.starten()
        self._pool = ThreadPoolExecutor(max_workers=groesse,
                                        thread_name_prefix="ocr")
        self._frei: list[Arbeiter] = list(self._arbeiter)
        self._frei_sperre = threading.Lock()
        self._laufend: dict[object, Future] = {}
        self._laufend_sperre = threading.Lock()
        self.zusammengefasst = 0

    def _mit_arbeiter(self, rgb: bytes, breite: int, hoehe: int) -> str:
        with self._frei_sperre:
            a = self._frei.pop()
        try:
            return a.text(rgb, breite, hoehe)
        finally:
            with self._frei_sperre:
                self._frei.append(a)

    def einreichen(self, region, rgb: bytes, breite: int, hoehe: int) -> Future:
        """Gibt ein `Future` auf den Text. Verdraengt einen aelteren Auftrag."""
        with self._laufend_sperre:
            alt = self._laufend.get(region)
            if alt is not None and not alt.done():
                # `cancel()` greift nur, solange der Auftrag in der
                # Warteschlange steht. Laeuft er schon, sitzt der Prozess in
                # libtesseract und ist nicht zu unterbrechen -- sein Ergebnis
                # wird ueber die Generation aus T-5.5 als ueberholt verworfen.
                if alt.cancel():
                    self.zusammengefasst += 1
            neu = self._pool.submit(self._mit_arbeiter, rgb, breite, hoehe)
            self._laufend[region] = neu
        return neu

    def beenden(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
        for a in self._arbeiter:
            a.beenden()
