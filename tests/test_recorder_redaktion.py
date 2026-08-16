"""T-7.2 -- die Redaktion, und zwar VOR dem Schreiben.

Jeder Negativnachweis steht neben einer Positivkontrolle mit DERSELBEN
Zeichenkette: "der Kanarienvogel steht nicht in der Datenbank" ist auch dann
wahr, wenn die Erfassung gar nicht lief. Und gesucht wird nicht nur in der
Datenbank, sondern im ganzen Archivverzeichnis -- eine Zwischendatei waere
derselbe Fehler wie eine Zeile.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from daimon.common import ipc
from tests.recorder_hilfen import starte as _starte
from daimon.recorder.daemon import PRODUZENT, Recorder
from daimon.common.config import ConfigError, denylist_laden
from daimon.recorder.redaktion import (
    GRUND_DENYLIST, GRUND_DRM, GRUND_PRIVAT, GRUND_UNBEKANNT,
    GRUND_WAHRNEHMUNG_AUS, Redaktion, desktop_kennungen, privat_setzen)
from daimon.recorder.store import (ART_OCR, STUFE_REDACTED, STUFE_TRANSIENT,
                                   Archiv)

KANARIE = "KANARIENVOGEL-T72-4711"


class Uhr:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def redaktion(tmp_path):
    return Redaktion(denylist=["org.keepassxc.KeePassXC"],
                     runtime_dir=tmp_path / "run",
                     kennungen={"keepassxc": "org.keepassxc.KeePassXC",
                                "kate": "org.kde.kate"},
                     uhr=Uhr())


# -- Die Kennung kommt aus .desktop, nicht aus dem Titel -------------------

def test_kennung_aus_startupwmclass(tmp_path):
    anw = tmp_path / "applications"
    anw.mkdir()
    (anw / "org.keepassxc.KeePassXC.desktop").write_text(
        "[Desktop Entry]\nName=KeePassXC\nStartupWMClass=keepassxc\n")
    karte = desktop_kennungen([anw])
    assert karte["keepassxc"] == "org.keepassxc.KeePassXC"
    assert karte["org.keepassxc.keepassxc"] == "org.keepassxc.KeePassXC"


def test_titel_taeuscht_die_kennung_nicht(tmp_path, redaktion):
    """Der Titel entscheidet nichts -- die Klasse entscheidet alles.

    Gemessen an der Wirkung und nicht am Quelltext: zwei Meldungen mit
    VERTAUSCHTEN Titeln. Ein Editor, der `KeePassXC` im Titel fuehrt, wird
    mitgeschnitten; der Tresor mit einem harmlosen Titel nicht.
    """
    d = Recorder(runtime_dir=tmp_path / "run",
                 archiv=Archiv(tmp_path / "a.db"), redaktion=redaktion)
    d.archiv.migrieren()
    try:
        luegner = d.melde({"typ": "archiv", "art": ART_OCR, "klasse": "kate",
                           "fenster": "KeePassXC — Tresor", "text": "harmlos"})
        tresor = d.melde({"typ": "archiv", "art": ART_OCR,
                          "klasse": "keepassxc", "fenster": "Unbenannt 1",
                          "text": KANARIE})
        assert luegner["id"] > 0, "der Titel hat eine harmlose App gesperrt"
        assert tresor["id"] == 0 and tresor["grund"] == GRUND_DENYLIST
    finally:
        d.archiv.schliessen()


def test_unbekanntes_fenster_wird_gesperrt(redaktion):
    assert redaktion.urteil("").grund == GRUND_UNBEKANNT
    assert not redaktion.urteil("").schreibt


def test_anwendung_ohne_desktop_datei_bleibt_sperrbar(tmp_path):
    r = Redaktion(denylist=["seltsames-bankprogramm"],
                  runtime_dir=tmp_path, kennungen={}, uhr=Uhr())
    assert r.urteil("Seltsames-Bankprogramm").grund == GRUND_DENYLIST


# -- Die vier Wege auf `transient` ----------------------------------------

def test_denylist_und_drm(redaktion):
    assert redaktion.urteil("keepassxc").grund == GRUND_DENYLIST
    assert redaktion.urteil("kate", drm=True).grund == GRUND_DRM


def test_privatmodus_laeuft_ab(tmp_path):
    uhr = Uhr()
    rt = tmp_path / "run"
    r = Redaktion(denylist=[], runtime_dir=rt, kennungen={}, uhr=uhr)
    assert r.urteil("kate").schreibt                    # Positivkontrolle
    privat_setzen(rt, 900.0, uhr=uhr)
    assert r.urteil("kate").grund == GRUND_PRIVAT
    uhr.t += 901.0
    assert r.urteil("kate").schreibt, "der Privatmodus ist nicht abgelaufen"


def test_wahrnehmung_aus_faellt_auf_transient(tmp_path):
    an = [True]
    uhr = Uhr()
    r = Redaktion(denylist=[], runtime_dir=tmp_path, kennungen={},
                  wahrnehmung_an=lambda: an[0], uhr=uhr)
    assert r.urteil("kate").schreibt                    # Positivkontrolle
    an[0] = False
    uhr.t += 10.0                                       # Cache abgelaufen
    assert r.urteil("kate").grund == GRUND_WAHRNEHMUNG_AUS
    assert r.urteil("kate").stufe == STUFE_TRANSIENT


def test_abgeschaltete_AUGEN_sperren_den_TON_nicht(tmp_path):
    """Zwei Wahrnehmungen, zwei Schalter -- seit dem 16.08. getrennt.

    `wahrnehmung_an` misst das Lebenszeichen der AUGEN. Wer sie abschaltet,
    hat nicht das Mikrofon abgeschaltet: den Ton sperrt der Ohren-Kill-Switch,
    indem er den Absender stoppt. Vorher hing beides an derselben Funktion,
    und das fiel nur deshalb nicht auf, weil ihre damalige Messung fast immer
    „an" sagte -- ein Fehler, der einen zweiten brauchte, um unsichtbar zu
    bleiben.
    """
    uhr = Uhr()
    r = Redaktion(denylist=[], runtime_dir=tmp_path, kennungen={},
                  wahrnehmung_an=lambda: False, uhr=uhr)
    assert r.urteil("kate").grund == GRUND_WAHRNEHMUNG_AUS   # Bildschirm: zu
    assert r.urteil_ton().schreibt                           # Ton: offen


def test_der_privatmodus_sperrt_BEIDE(tmp_path):
    """Die Gegenprobe zur Trennung: was den ganzen Betrieb betrifft, greift
    weiterhin auf beiden Wegen. Sonst waere oben nicht getrennt, sondern der
    Ton schlicht ungeschuetzt."""
    uhr = Uhr()
    r = Redaktion(denylist=[], runtime_dir=tmp_path, kennungen={}, uhr=uhr)
    privat_setzen(tmp_path, 900.0, uhr=uhr)
    assert r.urteil("kate").grund == GRUND_PRIVAT
    assert r.urteil_ton().grund == GRUND_PRIVAT


def test_kaputte_denylist_scheitert_laut(tmp_path):
    datei = tmp_path / "redaktion.yaml"
    datei.write_text("denylist: [unbalanciert\n")
    with pytest.raises(ConfigError):
        denylist_laden([datei])


def test_mitgelieferte_denylist_ist_lesbar():
    from pathlib import Path
    eintraege, herkunft = denylist_laden(
        [Path(__file__).resolve().parents[1] / "config" / "redaktion.yaml"])
    assert herkunft is not None and len(eintraege) > 5
    assert any("keepass" in e.lower() for e in eintraege)


# -- Am laufenden Dienst: der Kanarienvogel landet nirgends ----------------



def _sende(rt, nachricht: dict) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5.0)
    c.connect(str(ipc.socket_path(rt, PRODUZENT)))
    with c:
        c.sendall((json.dumps(nachricht) + "\n").encode())
        return json.loads(c.makefile("r").readline())


def test_kanarie_aus_gelisteter_anwendung_landet_nirgends(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir()
    archiv_dir = tmp_path / "archiv"
    d = Recorder(
        runtime_dir=rt,
        archiv=Archiv(archiv_dir / "archiv.db"),
        redaktion=Redaktion(denylist=["org.keepassxc.KeePassXC"],
                            runtime_dir=rt,
                            kennungen={"keepassxc": "org.keepassxc.KeePassXC",
                                       "kate": "org.kde.kate"}),
        erlaubte_units=None)
    faden = _starte(d)
    try:
        gesperrt = _sende(rt, {"v": 1, "typ": "archiv", "art": ART_OCR,
                               "klasse": "keepassxc", "fenster": "Tresor",
                               "text": KANARIE})
        assert gesperrt["ok"] is True and gesperrt["id"] == 0
        assert gesperrt["grund"] == GRUND_DENYLIST

        # Positivkontrolle: DIESELBE Zeichenkette aus einer nicht gelisteten
        # Anwendung MUSS ankommen -- sonst prueft der Test nur, dass die
        # Erfassung kaputt ist.
        erlaubt = _sende(rt, {"v": 1, "typ": "archiv", "art": ART_OCR,
                              "klasse": "kate", "fenster": "Notizen",
                              "text": KANARIE})
        assert erlaubt["ok"] is True and erlaubt["id"] > 0
    finally:
        d.stop()
        faden.join(timeout=5.0)

    # Genau EINMAL im ganzen Archivverzeichnis -- nicht in einer
    # Zwischendatei, nicht in einem Journal daneben, nicht im WAL.
    treffer = 0
    for pfad in archiv_dir.rglob("*"):
        if pfad.is_file():
            treffer += pfad.read_bytes().count(KANARIE.encode())
    assert treffer == 1, (
        f"{treffer} Vorkommen des Kanarienvogels -- erwartet genau eines "
        "(die Positivkontrolle)")


def test_privatmodus_laesst_die_datenbank_unveraendert(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir()
    d = Recorder(runtime_dir=rt, archiv=Archiv(tmp_path / "a.db"),
                 redaktion=Redaktion(denylist=[], runtime_dir=rt,
                                     kennungen={}),
                 erlaubte_units=None)
    faden = _starte(d)
    try:
        assert _sende(rt, {"v": 1, "typ": "archiv", "art": ART_OCR,
                           "klasse": "kate", "text": "vorher"})["id"] > 0
        zeilen_vorher = len(Archiv(tmp_path / "a.db").lesen())
        stand = (tmp_path / "a.db").stat().st_mtime_ns

        privat_setzen(rt, 900.0)
        antwort = _sende(rt, {"v": 1, "typ": "archiv", "art": ART_OCR,
                              "klasse": "kate", "text": KANARIE})
        assert antwort["grund"] == GRUND_PRIVAT
        assert len(Archiv(tmp_path / "a.db").lesen()) == zeilen_vorher
        assert (tmp_path / "a.db").stat().st_mtime_ns == stand
    finally:
        d.stop()
        faden.join(timeout=5.0)
