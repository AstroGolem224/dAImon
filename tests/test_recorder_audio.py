"""T-7.4 -- Gesprochenes im Archiv, Rohaudio nirgends.

Der Negativnachweis ist hier der ganze Task: nach einer archivierten
Aeusserung darf im Archivverzeichnis kein Audio liegen -- gesucht nach
INHALT und nicht nach Endung, denn eine WAV heisst nicht immer `.wav`. Die
Positivkontrolle daneben: dasselbe Transkript MUSS ankommen.
"""
from __future__ import annotations

import threading
import time

import pytest

from daimon.common import ipc
from tests.recorder_hilfen import starte as _starte
from daimon.recorder.audio import MAX_ZEICHEN, melde_transkript
from daimon.recorder.daemon import PRODUZENT, Recorder
from daimon.recorder.redaktion import GRUND_PRIVAT, Redaktion, privat_setzen
from daimon.recorder.store import ART_TRANSKRIPT, Archiv

SATZ = "wie lange laeuft der Build noch"
# Die ersten Bytes einer RIFF/WAVE-Datei. Was hier auftaucht, ist Rohaudio.
WAV_KOPF = b"RIFF"




@pytest.fixture
def dienst(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir()
    d = Recorder(runtime_dir=rt,
                 archiv=Archiv(tmp_path / "archiv" / "archiv.db"),
                 redaktion=Redaktion(denylist=[], runtime_dir=rt,
                                     kennungen={}),
                 erlaubte_units=None)
    faden = _starte(d)
    yield d
    d.stop()
    faden.join(timeout=5.0)


def _lesen(d: Recorder, art=None):
    pruefer = Archiv(d.archiv.pfad)
    try:
        return pruefer.lesen(art)
    finally:
        pruefer.schliessen()


# -- Das Transkript kommt an, das Audio nicht -----------------------------

def test_transkript_landet_im_archiv_und_ist_tainted(dienst):
    antwort = melde_transkript(dienst.runtime_dir, SATZ, marke="user_ptt")
    assert antwort["ok"] is True and antwort["id"] > 0
    (eintrag,) = _lesen(dienst, ART_TRANSKRIPT)
    assert eintrag["wert"].value == SATZ
    assert eintrag["wert"].mark.value == "tainted"
    # Die Herkunftsmarke steht statt eines erfundenen Fenstertitels da.
    assert eintrag["fenster"] == "user_ptt"


def test_kein_rohaudio_im_archivverzeichnis(dienst, tmp_path):
    """Positivkontrolle und Negativnachweis in einem Lauf."""
    assert melde_transkript(dienst.runtime_dir, SATZ)["id"] > 0
    # NICHT `dienst.archiv.schliessen()`: die Verbindung gehoert dem Faden
    # des Dienstes. Gesucht wird ohnehin auf der Platte, und im WAL steht
    # dasselbe -- gerade deshalb wird es mitgelesen.
    treffer_text, treffer_audio = 0, 0
    for pfad in (tmp_path / "archiv").rglob("*"):
        if pfad.is_file():
            roh = pfad.read_bytes()
            treffer_text += roh.count(SATZ.encode())
            treffer_audio += roh.count(WAV_KOPF)
    assert treffer_text >= 1, "das Transkript ist gar nicht angekommen"
    assert treffer_audio == 0, "Rohaudio im Archivverzeichnis"


def test_der_melder_nimmt_gar_kein_audio_an():
    """Die Zusage steht in der Signatur, nicht in einer Pruefung.

    Ein Test, der eine Pruefung misst, misst die Umgehbarkeit mit. Hier
    wird gemessen, dass es den Weg NICHT GIBT: kein Parameter fuer Pfad,
    Puffer oder Datei.
    """
    import inspect
    namen = set(inspect.signature(melde_transkript).parameters)
    assert namen == {"runtime_dir", "text", "marke", "timeout_s"}
    for verboten in ("wav", "audio", "pfad", "samples", "pcm", "datei"):
        assert verboten not in namen


def test_leerer_text_geht_nicht_hinaus(dienst):
    assert melde_transkript(dienst.runtime_dir, "   ")["ok"] is False
    assert _lesen(dienst) == []


def test_zu_langer_text_wird_gekuerzt_nicht_verworfen(dienst):
    lang = "a" * (MAX_ZEICHEN + 500)
    assert melde_transkript(dienst.runtime_dir, lang)["id"] > 0
    (eintrag,) = _lesen(dienst, ART_TRANSKRIPT)
    assert len(eintrag["wert"].value) == MAX_ZEICHEN


# -- Die Gatter des Tonpfads ----------------------------------------------

def test_privatmodus_haelt_auch_den_ton_zurueck(dienst):
    assert melde_transkript(dienst.runtime_dir, "vorher")["id"] > 0
    privat_setzen(dienst.runtime_dir, 900.0)
    antwort = melde_transkript(dienst.runtime_dir, SATZ)
    assert antwort["id"] == 0 and antwort["grund"] == GRUND_PRIVAT
    assert len(_lesen(dienst, ART_TRANSKRIPT)) == 1


def test_ohne_recorder_laeuft_die_meldung_ins_leere(tmp_path):
    """Die Wirkung der Pause aus T-7.3: der Dienst ist gestoppt, und der
    Sprachpfad bekommt einen Rueckgabewert statt einer Ausnahme."""
    leer = tmp_path / "kein_dienst"
    leer.mkdir()
    antwort = melde_transkript(leer, SATZ)
    assert antwort == {"ok": False, "grund": "kein_recorder"}


def test_denylist_gilt_fuer_fenster_nicht_fuer_gesprochenes(tmp_path):
    """Ein Transkript hat kein Fenster -- die Anwendungs-Denylist darf es
    deshalb nicht verwerfen, sonst haenge der Ton am Bildschirm."""
    rt = tmp_path / "run"
    rt.mkdir()
    d = Recorder(runtime_dir=rt, archiv=Archiv(tmp_path / "a.db"),
                 redaktion=Redaktion(denylist=["org.keepassxc.KeePassXC"],
                                     runtime_dir=rt, kennungen={}),
                 erlaubte_units=None)
    faden = _starte(d)
    try:
        assert melde_transkript(rt, SATZ)["id"] > 0
        # Gegenprobe: ein BILDSCHIRM-Eintrag ohne Kennung bleibt gesperrt.
        assert d.melde({"typ": "archiv", "art": "ocr", "text": "x"})["id"] == 0
    finally:
        d.stop()
        faden.join(timeout=5.0)
