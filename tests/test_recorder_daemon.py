"""T-7.1 -- der Archivdienst am laufenden Socket gemessen.

Der Negativnachweis ist hier billig zu haben und deshalb echt: dieser
Testprozess IST keine `daimon-eyes.service`. Wer mit der scharfen Unit-Liste
verbindet, muss abgewiesen werden -- und wer ohne sie verbindet, muss
durchkommen. Ohne das zweite waere das erste auch dann gruen, wenn der
Dienst gar nicht horcht.

Jede Meldung traegt `klasse`: seit T-7.2 sperrt die Redaktion ein Fenster
ohne Kennung, und ohne dieses Feld pruefte dieser Modul nur noch, dass die
Redaktion greift.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from daimon.common import ipc
from daimon.recorder.daemon import PRODUZENT, Recorder
from daimon.recorder.store import ART_OCR, Archiv


def _starte(d: Recorder) -> threading.Thread:
    """Dienst in EINEM Faden starten und fahren.

    Der Dienst ist einfaedig, und seine SQLite-Verbindung gehoert dem Faden,
    der sie geoeffnet hat. Wer `start()` hier und `lauf()` dort aufruft,
    prueft nicht den Dienst, sondern baut sich einen Fehler, den es im
    Betrieb nicht gibt.
    """
    def fahren() -> None:
        d.start()
        d.lauf()

    faden = threading.Thread(target=fahren, daemon=True)
    faden.start()
    pfad = ipc.socket_path(d.runtime_dir, PRODUZENT)
    frist = time.monotonic() + 5.0
    while not pfad.exists() and time.monotonic() < frist:
        time.sleep(0.02)
    assert pfad.exists(), "Dienst horcht nicht"
    return faden


@pytest.fixture
def dienst(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir()
    d = Recorder(runtime_dir=rt,
                 archiv=Archiv(tmp_path / "archiv.db", grenze_bytes=1 << 20),
                 erlaubte_units=None)          # scharf im eigenen Test unten
    faden = _starte(d)
    yield d
    d.stop()
    faden.join(timeout=5.0)


def _lesen(d: Recorder, art: str | None = None) -> list[dict]:
    """Von AUSSEN nachgelesen, mit eigener Verbindung.

    Die Verbindung des Dienstes gehoert seinem Faden. Und nachzusehen, was
    wirklich auf der Platte steht, ist ohnehin die ehrlichere Pruefung als
    den Dienst nach seinem eigenen Inhalt zu fragen.
    """
    pruefer = Archiv(d.archiv.pfad)
    try:
        return pruefer.lesen(art)
    finally:
        pruefer.schliessen()


def _sende(rt, nachricht: dict) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5.0)
    c.connect(str(ipc.socket_path(rt, PRODUZENT)))
    with c:
        c.sendall((json.dumps(nachricht) + "\n").encode())
        return json.loads(c.makefile("r").readline())


def test_ablage_ueber_den_socket(dienst):
    antwort = _sende(dienst.runtime_dir,
                     {"v": 1, "typ": "archiv", "art": ART_OCR,
                      "klasse": "kate", "text": "Kanarienvogel",
                      "fenster": "Mail"})
    assert antwort["ok"] is True and antwort["id"] > 0
    (eintrag,) = _lesen(dienst, ART_OCR)
    assert eintrag["wert"].value == "Kanarienvogel"


def test_fremder_typ_wird_abgewiesen(dienst):
    # `screen` ist ein gueltiger Typ -- nur nicht an diesem Socket.
    antwort = _sende(dienst.runtime_dir,
                     {"v": 1, "typ": "screen", "art": ART_OCR, "text": "x"})
    assert antwort["ok"] is False and antwort["grund"] == "typ"
    assert _lesen(dienst) == []


def test_rohaudio_ueber_den_socket_abgewiesen(dienst):
    antwort = _sende(dienst.runtime_dir,
                     {"v": 1, "typ": "archiv", "art": "audio",
                      "klasse": "kate", "text": "x"})
    assert antwort["ok"] is False and antwort["grund"] == "abgewiesen"
    assert _lesen(dienst) == []


def test_unlesbares_bleibt_folgenlos(dienst):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5.0)
    c.connect(str(ipc.socket_path(dienst.runtime_dir, PRODUZENT)))
    with c:
        c.sendall(b"{kein json\n")
        assert json.loads(c.makefile("r").readline())["ok"] is False
    assert _lesen(dienst) == []


def test_fremde_unit_wird_abgewiesen(tmp_path):
    """Dieselbe Ablage, scharfe Unit-Liste -- dieser Prozess ist nicht eyes."""
    rt = tmp_path / "run"
    rt.mkdir()
    d = Recorder(runtime_dir=rt, archiv=Archiv(tmp_path / "a.db"),
                 erlaubte_units=("daimon-eyes.service",))
    faden = _starte(d)
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(5.0)
        c.connect(str(ipc.socket_path(rt, PRODUZENT)))
        with c:
            c.sendall(b'{"v":1,"typ":"archiv","art":"ocr","text":"x"}\n')
            # Der Dienst schliesst vor der ersten Antwort. Ob das als EOF
            # oder als RST ankommt, entscheidet der Kernel: es liegen noch
            # ungelesene Bytes im Puffer, und dann ist der Abbruch hart.
            # Beides ist dieselbe Wirkung -- keine Antwort.
            try:
                assert c.makefile("r").readline() == ""
            except ConnectionResetError:
                pass
        assert _lesen(d) == []
    finally:
        d.stop()
        faden.join(timeout=5.0)


def test_sockets_sind_0600(dienst):
    assert ipc.ist_0600(ipc.socket_path(dienst.runtime_dir, PRODUZENT))
