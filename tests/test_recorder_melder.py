"""T-7.1b -- die Absender. Wer fuellt das Archiv, und was kommt nicht hinein.

Der Kanarienvogel laeuft zweimal durch: aus einem NICHT gelisteten Fenster
muss er ankommen, aus einem gelisteten darf er nirgends stehen. Ohne die
erste Haelfte waere die zweite auch dann gruen, wenn gar nichts gemeldet
wird.
"""
from __future__ import annotations

import pytest

from daimon.hub.focus import FocusReceiver
from daimon.recorder.daemon import Recorder
from daimon.recorder.melder import melde_ocr, melde_titel
from daimon.recorder.redaktion import Redaktion
from daimon.recorder.store import ART_OCR, ART_TITEL, Archiv
from tests.recorder_hilfen import starte

KANARIE = "KANARIENVOGEL-T71B-0815"
KENNUNGEN = {"keepassxc": "org.keepassxc.KeePassXC", "kate": "org.kde.kate"}


@pytest.fixture
def dienst(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir()
    d = Recorder(runtime_dir=rt,
                 archiv=Archiv(tmp_path / "archiv" / "archiv.db"),
                 redaktion=Redaktion(denylist=["org.keepassxc.KeePassXC"],
                                     runtime_dir=rt, kennungen=KENNUNGEN),
                 erlaubte_units=None)
    faden = starte(d)
    yield d
    d.stop()
    faden.join(timeout=5.0)


def _lesen(d, art=None):
    pruefer = Archiv(d.archiv.pfad)
    try:
        return pruefer.lesen(art)
    finally:
        pruefer.schliessen()


# -- Der OCR-Melder --------------------------------------------------------

def test_ocr_kommt_an_und_traegt_die_kennung(dienst):
    antwort = melde_ocr(dienst.runtime_dir, KANARIE, klasse="kate")
    assert antwort["ok"] is True and antwort["id"] > 0
    (eintrag,) = _lesen(dienst, ART_OCR)
    assert eintrag["wert"].value == KANARIE


def test_ocr_aus_gelistetem_fenster_landet_nirgends(dienst, tmp_path):
    gesperrt = melde_ocr(dienst.runtime_dir, KANARIE, klasse="keepassxc")
    assert gesperrt["id"] == 0 and gesperrt["grund"] == "denylist"
    # Positivkontrolle mit DERSELBEN Zeichenkette.
    assert melde_ocr(dienst.runtime_dir, KANARIE, klasse="kate")["id"] > 0

    treffer = 0
    for pfad in (tmp_path / "archiv").rglob("*"):
        if pfad.is_file():
            treffer += pfad.read_bytes().count(KANARIE.encode())
    assert treffer == 1, f"{treffer} Vorkommen -- erwartet genau eines"


def test_drm_flagge_wird_mitgemeldet(dienst):
    antwort = melde_ocr(dienst.runtime_dir, KANARIE, klasse="kate", drm=True)
    assert antwort["id"] == 0 and antwort["grund"] == "drm"


def test_ocr_ohne_kennung_wird_gesperrt(dienst):
    """Fail closed: ein Bildschirmtext ohne Herkunft ist einer, bei dem
    niemand sagen kann, ob er aus einem Passwortmanager stammt."""
    assert melde_ocr(dienst.runtime_dir, KANARIE, klasse="")["id"] == 0


# -- Der Titel-Melder ------------------------------------------------------

def test_titel_kommt_an(dienst):
    assert melde_titel(dienst.runtime_dir, "Notizen — Kate",
                       klasse="kate")["id"] > 0
    (eintrag,) = _lesen(dienst, ART_TITEL)
    assert eintrag["wert"].value == "Notizen — Kate"


def test_titel_eines_gelisteten_fensters_landet_nirgends(dienst):
    """Ein Fenstertitel ist so verraeterisch wie der Inhalt: 'Tresor —
    privat.kdbx' gehoert nicht ins Archiv."""
    antwort = melde_titel(dienst.runtime_dir, "Tresor — privat.kdbx",
                          klasse="keepassxc")
    assert antwort["id"] == 0 and antwort["grund"] == "denylist"
    assert _lesen(dienst, ART_TITEL) == []


# -- Der Fokusdienst als Absender -----------------------------------------

def _ereignis(empfaenger, caption, cls):
    return empfaenger.handle("activated", "uuid-1", caption, cls,
                             "kate.desktop", False, 4711)


def test_fokusdienst_meldet_titel_und_wiederholt_sich_nicht(dienst):
    empfaenger = FocusReceiver()
    empfaenger._on_event = empfaenger.archiv_melder()
    import daimon.common.config as cfg
    alt = cfg.runtime_dir
    cfg.runtime_dir = lambda: dienst.runtime_dir
    try:
        _ereignis(empfaenger, "Notizen — Kate", "kate")
        _ereignis(empfaenger, "Notizen — Kate", "kate")   # derselbe Titel
        _ereignis(empfaenger, "Build läuft — Kate", "kate")
    finally:
        cfg.runtime_dir = alt

    titel = [e["wert"].value for e in _lesen(dienst, ART_TITEL)]
    assert sorted(titel) == ["Build läuft — Kate", "Notizen — Kate"], (
        "derselbe Titel wurde zweimal gemeldet oder einer fehlt")


def test_ein_toter_recorder_reisst_den_fokusdienst_nicht_mit(tmp_path):
    """`handle()` faengt Ausnahmen des Abnehmers -- hier wird geprueft, dass
    es gar nicht erst eine gibt und das Ereignis normal zurueckkommt."""
    empfaenger = FocusReceiver()
    empfaenger._on_event = empfaenger.archiv_melder()
    import daimon.common.config as cfg
    alt = cfg.runtime_dir
    cfg.runtime_dir = lambda: tmp_path / "kein_dienst"
    try:
        ev = _ereignis(empfaenger, "Irgendwas", "kate")
    finally:
        cfg.runtime_dir = alt
    assert ev.caption.value == "Irgendwas"
    assert empfaenger.zaehler()["activated"] == 1
