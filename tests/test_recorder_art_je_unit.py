"""Die Art gehoert zum ABSENDER, nicht zur Nachricht.

**Der Riss, den diese Datei schliesst.** `art` steht in der Nachricht und
waehlt aus, WELCHES Urteil die Redaktion faellt: `transkript` fuehrt auf
`urteil_ton()`, und das kennt weder Denylist noch DRM. Das ist richtig -- ein
gesprochener Satz hat kein Fenster, und eine Anwendungs-Denylist auf den Ton
zu legen koppelte ihn an den Bildschirm (T-7.4, Modulkopf `redaktion.py`).

Falsch war, dass JEDER der drei erlaubten Absender diese Wahl treffen durfte.
Der Augendienst -- laut Modulkopf des Recorders die groesste Angriffsflaeche
des Projekts -- konnte den OCR-Text eines gesperrten Passwortmanagers als
`art: "transkript"` ablegen, und die Denylist wurde nie gefragt. Die
Unit-Liste am Socket half nicht: eyes STEHT darin.

Gemessen wird ueber den echten Socket und nicht an `melde()` allein. Die
Unit haengt an der Verbindung; ein Test, der sie als Argument hereinreicht,
belegt die Tabelle und nicht die Verdrahtung -- und genau diese Trennung ist
in diesem Projekt schon dreimal der Fehler gewesen.
"""
from __future__ import annotations

import json
import socket

import pytest

from daimon.common import ipc
from daimon.recorder.daemon import ART_JE_UNIT, ERLAUBTE_UNITS, PRODUZENT, Recorder
from daimon.recorder.redaktion import Redaktion
from daimon.recorder.store import (ART_OCR, ART_TITEL, ART_TRANSKRIPT, Archiv)
from tests.recorder_hilfen import starte as _starte

DENYLIST = ("bitwarden",)
GEHEIM = "Masterpasswort hunter2"


def _bauen(tmp_path, *, units=None) -> Recorder:
    rt = tmp_path / "run"
    rt.mkdir(exist_ok=True)
    return Recorder(
        runtime_dir=rt,
        archiv=Archiv(tmp_path / "archiv.db"),
        redaktion=Redaktion(denylist=DENYLIST, runtime_dir=rt,
                            kennungen={"bitwarden": "bitwarden"}),
        erlaubte_units=units)


@pytest.fixture
def dienst(tmp_path):
    """OHNE Faden: `melde()` wird hier direkt aufgerufen, und die
    SQLite-Verbindung gehoert dem Faden, der sie geoeffnet hat."""
    d = _bauen(tmp_path)
    d.archiv.migrieren()
    yield d
    d.archiv.schliessen()


@pytest.fixture
def laufender(tmp_path):
    """MIT Faden UND scharfer Unit-Liste, fuer die Messung am echten Socket.

    Scharf, weil die beiden Schalter zusammengehoeren: ohne `erlaubte_units`
    reicht der Dienst absichtlich keine Unit an `melde()` weiter, und der
    Test pruefte dann den Fall, in dem die Tabelle gar nicht greifen soll.
    """
    d = _bauen(tmp_path, units=("daimon-eyes.service",))
    faden = _starte(d)
    yield d
    d.stop()
    faden.join(timeout=5.0)


def _lesen(d: Recorder) -> list[dict]:
    pruefer = Archiv(d.archiv.pfad)
    try:
        return pruefer.lesen()
    finally:
        pruefer.schliessen()


def _peer_faelschen(monkeypatch, unit: str) -> None:
    """Die Gegenstelle auf `unit` setzen -- dieser Testprozess ist keine Unit.

    Die Unit-PRUEFUNG von `accept` faellt dabei weg (`erlaubte_units=None`
    an die echte Funktion): gemessen werden soll die Art-Tabelle, nicht ein
    zweites Mal die Liste, die schon eigene Pruefungen hat.
    """
    class Peer:
        pid, uid = 4711, 1000

    Peer.unit = unit
    echt = ipc.accept
    monkeypatch.setattr(
        ipc, "accept",
        lambda srv, prod, **kw: (
            echt(srv, prod, **{**kw, "erlaubte_units": None})[0], Peer()))


def _sende(d: Recorder, nachricht: dict) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5.0)
    c.connect(str(ipc.socket_path(d.runtime_dir, PRODUZENT)))
    with c:
        c.sendall((json.dumps(nachricht) + "\n").encode())
        return json.loads(c.makefile("r").readline())


# -- Die Tabelle ------------------------------------------------------------

def test_die_unit_liste_ist_abgeleitet():
    """Zwei Listen, die dasselbe meinen, laufen auseinander. Diese nicht."""
    assert set(ERLAUBTE_UNITS) == set(ART_JE_UNIT)


def test_jede_unit_darf_genau_eine_art():
    assert ART_JE_UNIT == {
        "daimon-eyes.service": frozenset({ART_OCR}),
        "daimon-focus.service": frozenset({ART_TITEL}),
        "daimon-ears.service": frozenset({ART_TRANSKRIPT}),
    }


# -- Der Riss selbst --------------------------------------------------------

def test_die_augen_koennen_kein_transkript_deklarieren(dienst):
    """DER BEFUND. Vorher lief dieselbe Meldung an der Denylist vorbei."""
    antwort = dienst.melde(
        {"typ": "archiv", "art": ART_TRANSKRIPT, "text": GEHEIM,
         "klasse": "bitwarden"}, unit="daimon-eyes.service")
    assert antwort["ok"] is False
    assert antwort["grund"] == "art_nicht_erlaubt"
    assert _lesen(dienst) == []


def test_der_fokusdienst_ebenso_wenig(dienst):
    antwort = dienst.melde(
        {"typ": "archiv", "art": ART_TRANSKRIPT, "text": GEHEIM,
         "klasse": "bitwarden"}, unit="daimon-focus.service")
    assert antwort["grund"] == "art_nicht_erlaubt"


def test_und_die_ohren_kein_ocr(dienst):
    """Die Gegenrichtung: die Tabelle sperrt in beide Richtungen. Sonst
    bestuende sie auch eine Fassung, die nur `transkript` kennt."""
    antwort = dienst.melde(
        {"typ": "archiv", "art": ART_OCR, "text": "x", "klasse": "kate"},
        unit="daimon-ears.service")
    assert antwort["grund"] == "art_nicht_erlaubt"


@pytest.mark.parametrize("unit,art", [
    ("daimon-eyes.service", ART_OCR),
    ("daimon-focus.service", ART_TITEL),
    ("daimon-ears.service", ART_TRANSKRIPT),
])
def test_POSITIVKONTROLLE_jeder_darf_seine_eigene_art(dienst, unit, art):
    """Ohne diese Zeile bestuende alles oben auch eine Fassung, die JEDE
    Meldung abweist -- und die haette den Mitschnitt still abgeschaltet."""
    antwort = dienst.melde(
        {"typ": "archiv", "art": art, "text": "Kanarienvogel",
         "klasse": "kate"}, unit=unit)
    assert antwort["ok"] is True and antwort["id"] > 0


def test_eine_unbekannte_unit_bekommt_keine_art(dienst):
    """Fail-closed. `accept` haette sie ohnehin nicht durchgelassen -- aber
    ein Fail-open hier waere genau die Tuer, die dieser Task schliesst."""
    antwort = dienst.melde(
        {"typ": "archiv", "art": ART_OCR, "text": "x", "klasse": "kate"},
        unit="daimon-egress.service")
    assert antwort["grund"] == "art_nicht_erlaubt"


def test_ohne_systemd_gilt_jede_art(dienst):
    """`unit=None` heisst "jede Art" -- dieselbe Bedeutung wie bei
    `erlaubte_units`. Ein Prueflauf ohne systemd kennt keine Gegenstelle."""
    assert dienst.melde({"typ": "archiv", "art": ART_TRANSKRIPT,
                         "text": "guten Morgen"})["ok"] is True


def test_ohne_unit_liste_reicht_der_dienst_keine_unit_durch(tmp_path,
                                                            monkeypatch):
    """Die beiden Schalter haengen ZUSAMMEN, und im ersten Entwurf taten sie
    das nicht: der Dienst reichte `peer.unit` immer durch, auch bei
    `erlaubte_units=None`. Im Prueflauf ist das die Unit des Prueflaufs --
    17 Pruefungen fielen daran, und zwar zu Recht.

    Gemessen am Socket: derselbe Absender, dieselbe Meldung, die oben
    gesperrt wird, kommt hier durch."""
    d = _bauen(tmp_path, units=None)
    _peer_faelschen(monkeypatch, "daimon-eyes.service")
    faden = _starte(d)
    try:
        assert _sende(d, {"v": 1, "typ": "archiv", "art": ART_TRANSKRIPT,
                          "text": "guten Morgen"})["ok"] is True
    finally:
        d.stop()
        faden.join(timeout=5.0)


# -- Die Verdrahtung, ueber den echten Socket -------------------------------

def test_die_unit_der_VERBINDUNG_entscheidet(laufender, monkeypatch):
    """Der Weg von `accept()` bis `melde()`, nicht die Tabelle allein.

    Die Gegenstelle wird auf `daimon-eyes.service` gesetzt: dieser
    Testprozess ist keine Unit, und ohne diesen Griff pruefte der Test die
    Verdrahtung gegen `None` -- also gegen den Fall, in dem die Tabelle
    absichtlich nicht greift.
    """
    _peer_faelschen(monkeypatch, "daimon-eyes.service")

    gesperrt = _sende(laufender, {"v": 1, "typ": "archiv",
                                  "art": ART_TRANSKRIPT, "text": GEHEIM,
                                  "klasse": "bitwarden"})
    assert gesperrt["grund"] == "art_nicht_erlaubt"
    # POSITIVKONTROLLE ueber DENSELBEN Weg: die eigene Art kommt durch.
    assert _sende(laufender, {"v": 1, "typ": "archiv", "art": ART_OCR,
                              "text": "Kanarienvogel",
                              "klasse": "kate"})["ok"] is True
    assert [z["art"] for z in _lesen(laufender)] == [ART_OCR]
