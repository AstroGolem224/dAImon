"""Die DRM-Sperre aus Design 4.4 -- von der Watcher-Nutzlast bis zum Urteil.

**Warum diese Datei ueberhaupt entsteht.** Die Sperre war gebaut: die
Gatterkette kennt `GRUND_DRM` (T-5.3), die Redaktion kennt ihn (T-7.2), beide
sind einzeln geprueft. Sie konnte nur nie ausloesen -- `fenster_abfragen()` im
Augendienst setzte `drm=False` FEST, und das ist der einzige produktive Weg zu
einem Fenster (der Push-Weg auf `de.daimon.Eyes` hat keinen Absender). Kein
Produzent im Repo hat je ein `True` erzeugt.

Dieselbe Gestalt wie das Ticketbuch ohne Verbraucher (T-3.11), das Gate ohne
Aufrufer (T-5.9b) und der Kontextspeicher ohne `laden()`: das Stueck ist da,
sein Zulauf fehlt. Ein Test je Stueck bemerkt das nicht -- gemessen wird
deshalb die NAHT, in der Reihenfolge, in der sie im Betrieb laeuft.

Die Quelle ist KWins `excludeFromCapture`, am 15.08. an der Fensterliste
dieser Sitzung abgefragt und nicht aus einer Dokumentation uebernommen. Sie
ist weiter als DRM: sie sagt "dieses Fenster gehoert in keine Aufnahme".
"""
from __future__ import annotations

import json

import pytest

from daimon.eyes.change import Fenster
from daimon.eyes.daemon import Augen
from daimon.hub.focus import FocusReceiver
from daimon.recorder.daemon import Recorder
from daimon.recorder.redaktion import GRUND_DRM, Redaktion
from daimon.recorder.store import Archiv

NUTZLAST = {"kind": "activated", "uuid": "u-1", "caption": "Folge 3",
            "cls": "netflix", "desktop": "", "fullscreen": True, "pid": 7,
            "x": 0, "y": 0, "breite": 1920, "hoehe": 1080}


def watcher_meldet(**kw) -> FocusReceiver:
    r = FocusReceiver()
    r.handle_json(json.dumps({**NUTZLAST, **kw}))
    return r


# -- 1. Der Fokusdienst fuehrt das Feld ------------------------------------

def test_die_auskunft_traegt_drm():
    assert watcher_meldet(drm=True).fenster()["drm"] is True


def test_ohne_drm_bleibt_es_false():
    """Die Gegenrichtung. Ein Feld, das immer `True` meldet, bestuende den
    Test darueber und sperrte im Betrieb alles."""
    assert watcher_meldet(drm=False).fenster()["drm"] is False


def test_ein_watcher_ohne_das_feld_sperrt_nicht_alles():
    """Der Absender laeuft im Compositor und wird nicht mit diesem Modul
    zusammen ausgeliefert. Eine aeltere Fassung kennt `drm` nicht -- dann
    gilt `False`, sonst schaltete ein Versionsversatz den Mitschnitt still
    ab."""
    r = FocusReceiver()
    r.handle_json(json.dumps(NUTZLAST))          # ohne Schluessel `drm`
    assert r.fenster()["drm"] is False


# -- 2. Die Augen lesen es, statt False zu erfinden ------------------------

class FakeDbus:
    """Genau so viel `dbus`, wie `fenster_abfragen()` anfasst."""

    def __init__(self, antwort: dict) -> None:
        self._antwort = antwort

    def SessionBus(self):
        return self

    def get_object(self, _name, _pfad):
        return self

    def Fenster(self, dbus_interface=""):
        return json.dumps(self._antwort)


@pytest.mark.parametrize("drm", [True, False])
def test_die_augen_reichen_drm_durch(tmp_path, monkeypatch, drm):
    """Hier sass der Fehler: `drm=False` fest verdrahtet, und damit war
    `GRUND_DRM` im ganzen System unerreichbar."""
    import sys

    r = watcher_meldet(drm=drm)
    monkeypatch.setitem(sys.modules, "dbus", FakeDbus(r.fenster()))
    augen = Augen(verzeichnis=tmp_path)
    fenster = augen.fenster_abfragen()
    assert fenster is not None
    assert fenster.drm is drm


# -- 3. Die Gatterkette und die Redaktion sperren ---------------------------

def test_die_gatterkette_verwirft_ein_geschuetztes_fenster():
    from daimon.eyes import change

    import numpy as np

    kette = change.Kette(tor=lambda: True)
    bild = np.zeros((4, 4, 3), np.uint8)
    fenster = Fenster(x=0, y=0, breite=4, hoehe=4, klasse="netflix", drm=True)
    befund = kette.verarbeiten(bild, fenster)
    assert befund.veraendert is False
    assert befund.grund == change.GRUND_DRM


def test_der_recorder_schreibt_ein_geschuetztes_fenster_nicht(tmp_path):
    """Das Ende der Naht: dieselbe Meldung, einmal mit und einmal ohne Flag."""
    archiv = Archiv(tmp_path / "archiv.db")
    archiv.migrieren()
    rec = Recorder(runtime_dir=tmp_path, archiv=archiv,
                   redaktion=Redaktion(runtime_dir=tmp_path,
                                       kennungen={"netflix": "netflix"}))
    meldung = {"typ": "archiv", "art": "titel", "text": "Folge 3",
               "klasse": "netflix"}

    gesperrt = rec.melde({**meldung, "drm": True})
    assert gesperrt["stufe"] == "transient"
    assert gesperrt["grund"] == GRUND_DRM
    # POSITIVKONTROLLE: ohne das Flag geht dieselbe Meldung durch. Sonst
    # bestuende dieser Test auch eine Redaktion, die alles sperrt.
    assert rec.melde(meldung)["id"] > 0
    assert [z["art"] for z in archiv.lesen()] == ["titel"]


# -- 4. Der Titelmelder haelt das Flag fest --------------------------------

def test_der_titelmelder_schickt_drm_mit(monkeypatch):
    """Der Titel ist der einzige Weg, auf dem ein geschuetztes Fenster sonst
    noch ins Archiv kaeme: die Augen sperrt das Flag, den Fokusdienst nicht.
    "Folge 3: ..." steht in der Titelleiste, nicht nur im Bild."""
    gesendet = {}
    monkeypatch.setattr("daimon.recorder.melder.senden",
                        lambda _rt, n, **_kw: gesendet.update(n) or {"ok": True})

    r = watcher_meldet(drm=True)
    r.archiv_melder()(r.letztes)
    assert gesendet["art"] == "titel"
    assert gesendet["drm"] is True
