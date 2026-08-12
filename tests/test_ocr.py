"""T-5.6 -- OCR im dauerhaften Arbeitsprozess.

Was hier NICHT geprueft wird: wie schnell tesseract liest und wie gut. Das
braucht einen Bildschirm mit Text und gehoert in den zurueckgestellten
Pruefstand. Pruefbar ohne Bildschirm ist alles, woran der Entwurf haengt --
und ein Punkt davon ist so leicht zu verlieren, dass er einen eigenen Test
bekommt: der Arbeitsprozess darf `numpy` nicht sehen.
"""
from __future__ import annotations

import ast
import concurrent.futures
import threading
from pathlib import Path

import pytest

from daimon.eyes import ocr


# -- Die Aufgabe des Arbeitsprozesses --------------------------------------

def test_der_arbeiter_importiert_kein_numpy():
    """Der ganze Grund fuer den eigenen Prozess.

    Spike T--1.10 hat gemessen, dass libtesseract im selben Prozess wie
    `numpy` rund 800 ms je Vollbild MEHR kostet -- OpenMP und OpenBLAS teilen
    sich die Laufzeitumgebung. Ein `import numpy` in dieser Datei macht die
    Aufgabe rueckgaengig, und zwar unsichtbar: es wuerde nur langsamer, nicht
    falsch.
    """
    quelle = (Path(ocr.__file__).parent / "ocr_worker.py").read_text()
    baum = ast.parse(quelle)
    module = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module.update(a.name.split(".")[0] for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            module.add(knoten.module.split(".")[0])
    assert module == {"ctypes", "os", "socket", "sys"}, module


def test_der_arbeiter_bricht_ab_wenn_die_sprache_fehlt():
    """Sonst liefe er weiter und gaebe zu jedem Bild einen leeren String --
    ununterscheidbar von einem Bildschirm ohne Text."""
    quelle = (Path(ocr.__file__).parent / "ocr_worker.py").read_text()
    assert "SystemExit(2)" in quelle
    assert "TessBaseAPIInit3(h" in quelle


# -- Wo die Sprachdaten herkommen ------------------------------------------

def test_das_systemverzeichnis_kommt_zuletzt(tmp_path, monkeypatch):
    """`/usr/share/tessdata` hat auf dieser Maschine nur `afr` und `osd` --
    und der Standard-tessdata waere ausserdem 277 ms langsamer."""
    eigen = tmp_path / "eigen"
    eigen.mkdir()
    for s in ("deu", "eng"):
        (eigen / f"{s}.traineddata").write_bytes(b"x")
    monkeypatch.setenv("DAIMON_TESSDATA", str(eigen))
    assert ocr.tessdata_verzeichnis() == str(eigen)


def test_ein_verzeichnis_mit_nur_einer_sprache_zaehlt_nicht(tmp_path, monkeypatch):
    """`-l deu+eng` braucht beide. Eine allein waere ein halbes Ergebnis,
    das aussieht wie ein ganzes."""
    halb = tmp_path / "halb"
    halb.mkdir()
    (halb / "deu.traineddata").write_bytes(b"x")
    monkeypatch.setenv("DAIMON_TESSDATA", str(halb))
    monkeypatch.setattr(ocr, "SPRACHEN", "deu+eng")
    # Faellt auf die naechsten Kandidaten durch; hier zaehlt nur, dass das
    # halbe Verzeichnis NICHT gewaehlt wird.
    try:
        gewaehlt = ocr.tessdata_verzeichnis()
    except ocr.OcrFehler:
        return
    assert gewaehlt != str(halb)


def test_die_meldung_sagt_wo_gesucht_wurde(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_TESSDATA", str(tmp_path / "gibtsnicht"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "auchnicht"))
    monkeypatch.setattr(ocr, "SPRACHEN", "xxx+yyy")
    with pytest.raises(ocr.OcrFehler) as fehler:
        ocr.tessdata_verzeichnis()
    assert "tessdata_fast" in str(fehler.value)
    assert "DAIMON_TESSDATA" in str(fehler.value)


# -- Die gepinnten Werte ---------------------------------------------------

def test_psm_und_sprachen_stehen_fest():
    """`--psm 11` ist nicht nur schneller als 3 und 6, sondern ergiebiger:
    620 Zeichen gegen 616 und 611 auf demselben Bild."""
    assert ocr.PSM == 11
    assert ocr.SPRACHEN == "deu+eng"


def test_omp_num_threads_wird_auf_eins_gesetzt():
    """Vierundzwanzig Threads sind rund 25 % langsamer als einer."""
    quelle = Path(ocr.__file__).read_text()
    assert '"OMP_NUM_THREADS"] = "1"' in quelle


# -- Das Zusammenfassen ----------------------------------------------------

class SteuerbarerPool(ocr.Pool):
    """Ein Pool ohne echte Arbeiter, mit einer Bremse, die der Test loest.

    Kein `time.sleep`: eine Aufgabe, die „lange genug" schlaeft, ist von
    einer, die zu frueh fertig war, nicht zu unterscheiden -- und genau
    daran sind in diesem Projekt schon zwei Einfrierversuche gescheitert.
    Hier meldet der Faden, dass er WIRKLICH angefangen hat, und laeuft erst
    weiter, wenn der Test es erlaubt.
    """

    def __init__(self):
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._laufend = {}
        self._laufend_sperre = threading.Lock()
        self.zusammengefasst = 0
        self._arbeiter = []
        self.gelesen = []
        self.gestartet = threading.Event()
        self.freigabe = threading.Event()

    def _mit_arbeiter(self, rgb, breite, hoehe):
        self.gestartet.set()
        self.freigabe.wait(timeout=5)
        self.gelesen.append(rgb)
        return f"text{len(rgb)}"


def test_ein_zweiter_auftrag_fuer_dieselbe_region_verdraengt_den_ersten():
    """Derselbe Fensterbereich zweimal ist zweimal dieselbe Frage."""
    p = SteuerbarerPool()
    region = (0, 0, 100, 100)
    p.einreichen(region, b"a" * 3, 1, 1)
    assert p.gestartet.wait(timeout=5)          # der erste laeuft wirklich
    zweiter = p.einreichen(region, b"b" * 4, 1, 1)
    dritter = p.einreichen(region, b"c" * 5, 1, 1)
    p.freigabe.set()
    assert dritter.result(timeout=5) == "text5"
    assert zweiter.cancelled() is True
    assert p.zusammengefasst == 1
    assert b"b" * 4 not in p.gelesen
    p._pool.shutdown()


def test_verschiedene_regionen_verdraengen_einander_nicht():
    p = SteuerbarerPool()
    p.freigabe.set()
    a = p.einreichen((0, 0, 10, 10), b"a", 1, 1)
    b = p.einreichen((99, 99, 10, 10), b"bb", 1, 1)
    assert a.result(timeout=5) == "text1"
    assert b.result(timeout=5) == "text2"
    assert p.zusammengefasst == 0
    p._pool.shutdown()


def test_ein_laufender_auftrag_wird_nicht_als_abgebrochen_gezaehlt():
    """`cancel()` greift nur in der Warteschlange. Laeuft der Auftrag schon,
    sitzt der Prozess in libtesseract und ist nicht zu unterbrechen -- ein
    Abbruch, den man behauptet und nicht leisten kann, waere schlimmer als
    keiner. Sein Ergebnis wird ueber die Generation aus T-5.5 verworfen.
    """
    p = SteuerbarerPool()
    region = (0, 0, 10, 10)
    erster = p.einreichen(region, b"a", 1, 1)
    assert p.gestartet.wait(timeout=5)          # er laeuft, kein Schlaf noetig
    p.einreichen(region, b"bb", 1, 1)
    p.freigabe.set()
    assert erster.result(timeout=5) == "text1"
    assert erster.cancelled() is False
    assert p.zusammengefasst == 0
    p._pool.shutdown()
