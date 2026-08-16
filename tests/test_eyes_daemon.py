"""Der Augendienst -- das Gelenk, das im Plan fehlte.

Geprueft wird hier nur, was ohne Bildschirm pruefbar ist: die Reihenfolge, der
Rueckfall ohne Fokus-Ereignis, der Widerruf und das Verhalten bei einer
gescheiterten Runde. Alles, was Frames braucht, gehoert in den Pruefstand.
"""
from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from daimon.eyes import context, daemon as dm
from daimon.eyes.change import Fenster


class KetteAttrappe:
    def __init__(self, veraendert=True):
        self.veraendert = veraendert
        self.fenster: list[Fenster] = []
        self.gen = 0

    def verarbeiten(self, rgb, fenster):
        from daimon.eyes.change import Befund
        self.fenster.append(fenster)
        self.gen += 1
        if not self.veraendert:
            return Befund(generation=self.gen, veraendert=False,
                          grund="unveraendert")
        return Befund(generation=self.gen, veraendert=True, signatur="s",
                      region=(0, 0, 4, 3),
                      ausschnitt=np.zeros((3, 4, 3), np.uint8),
                      kosten={"signatur": 0.001})


class AufnahmeAttrappe:
    def __init__(self, wirft=False):
        self.wirft = wirft
        self.aufrufe = 0

    def frame(self, **_kw):
        self.aufrufe += 1
        if self.wirft:
            raise RuntimeError("kein Frame")
        return {"breite": 8, "hoehe": 6, "framerate": "1/1", "bytes": 144,
                "summe": 1, "rohdaten": bytes(8 * 6 * 3)}


class PoolAttrappe:
    def __init__(self, text="Bildschirmtext"):
        self.text = text
        self.eingereicht = []

    def einreichen(self, region, rgb, breite, hoehe):
        self.eingereicht.append((region, breite, hoehe))
        f: "concurrent.futures.Future" = __import__(
            "concurrent.futures").futures.Future()
        f.set_result(self.text)
        return f

    def beenden(self):
        pass


def augen(tmp_path, *, veraendert=True, wirft=False, text="Bildschirmtext"):
    a = dm.Augen(verzeichnis=tmp_path / "context")
    a._kette = KetteAttrappe(veraendert)
    a._aufnahme = AufnahmeAttrappe(wirft)
    a._pool = PoolAttrappe(text)
    a._tor = lambda: True
    return a


# -- Die Denylist ----------------------------------------------------------

def test_die_denylist_ist_vorgabe_und_nicht_zuschaltbar():
    """Eine Vorgabe, die man einschalten muss, schuetzt niemanden."""
    assert any("keepass" in s.lower() for s in dm.DENYLIST_VORGABE)
    assert any("bitwarden" in s.lower() for s in dm.DENYLIST_VORGABE)


# -- Der Rueckfall ohne Fokus-Ereignis -------------------------------------

def test_ohne_fokusereignis_wird_der_ganze_bildschirm_zugeschnitten(tmp_path):
    """125 ms statt 14 ms -- teuer, aber die Alternative waere, gar nicht
    hinzusehen, bis ein Fenster den Fokus wechselt."""
    a = augen(tmp_path)
    a.einmal_sehen("timer")
    f = a._kette.fenster[-1]
    assert (f.x, f.y, f.breite, f.hoehe) == (0, 0, 8, 6)


def test_ein_fokusereignis_setzt_das_fenster(tmp_path):
    a = augen(tmp_path)
    a._ausloeser = type("A", (), {"fokus": lambda self: None})()
    a.fokus_ereignis(Fenster(x=10, y=20, breite=30, hoehe=40, klasse="editor"))
    a.einmal_sehen("timer")
    f = a._kette.fenster[-1]
    assert (f.x, f.breite, f.klasse) == (10, 30, "editor")


# -- Eine Runde ------------------------------------------------------------

def test_eine_runde_legt_den_text_in_den_kontext(tmp_path):
    a = augen(tmp_path, text="Sehr geehrte Damen")
    b = a.einmal_sehen("timer")
    assert b["zeichen"] == len("Sehr geehrte Damen")
    assert a._speicher.zaehler()[context.ART_OCR] == 1


def test_ein_unveraenderter_bildschirm_kostet_kein_ocr(tmp_path):
    """Der ganze Zweck der Gatterkette."""
    a = augen(tmp_path, veraendert=False)
    b = a.einmal_sehen("timer")
    assert b["abgewiesen"] == "unveraendert"
    assert a._pool.eingereicht == []
    assert a._speicher.zaehler()[context.ART_OCR] == 0


def test_der_ocr_auftrag_traegt_die_region_als_schluessel(tmp_path):
    """T-5.6 fasst Auftraege je Region zusammen -- der Schluessel muss die
    Region sein und nicht der Frame."""
    a = augen(tmp_path)
    a.einmal_sehen("timer")
    region, breite, hoehe = a._pool.eingereicht[0]
    assert region == (0, 0, 4, 3)
    assert (breite, hoehe) == (4, 3)


# -- Eine gescheiterte Runde beendet den Dienst nicht ----------------------

def test_eine_gescheiterte_runde_beendet_den_dienst_nicht(tmp_path, capsys):
    """Ein einzelner verlorener Frame ist kein Grund, das Hinsehen fuer den
    Rest des Tages einzustellen."""
    a = augen(tmp_path, wirft=True)
    a._ausloeser = type("A", (), {"tick": lambda self: "timer"})()

    def stoppen():
        a._halt.set()
    threading.Timer(0.15, stoppen).start()
    a.lauf(takt_s=0.01)
    assert a._aufnahme.aufrufe > 1              # es wurde weiter versucht
    assert "gescheitert" in capsys.readouterr().err


# -- Das Lebenszeichen der Wahrnehmung -------------------------------------

def test_die_schleife_schreibt_das_lebenszeichen(tmp_path, monkeypatch):
    """Der Recorder sperrt Bildschirmarten, solange es fehlt. Ohne diese
    Pruefung haenge sein Gatter an einer Datei, die niemand fuellt -- und der
    Mitschnitt waere still abgeschaltet statt falsch offen.
    """
    from daimon.recorder import pause

    monkeypatch.setattr("daimon.common.config.runtime_dir", lambda: tmp_path)
    a = augen(tmp_path)
    a._ausloeser = type("A", (), {"tick": lambda self: None})()
    threading.Timer(0.15, a._halt.set).start()
    a.lauf(takt_s=0.01)

    assert pause.augen_sehen(tmp_path) is True
    # Und nach dem Abbau ist es SOFORT weg, nicht erst nach der Frist.
    a.beenden()
    assert pause.augen_sehen(tmp_path) is False


# -- Der Widerruf ----------------------------------------------------------

class SitzungAttrappe:
    def __init__(self, widerruf=False):
        self._widerruf = widerruf
        self.widerrufen_aufrufe = 0

    def widerruf_angefordert(self):
        return self._widerruf

    def widerrufen(self):
        self.widerrufen_aufrufe += 1
        self._widerruf = False
        return {"ok": True}


def test_die_widerrufsmarke_wird_bei_jeder_runde_geprueft(tmp_path):
    """Wer sie nur beim Start liest, widerruft erst beim naechsten Neustart --
    und das ist bei einem Widerruf die falsche Frist."""
    a = augen(tmp_path)
    a._speicher.hinzufuegen(context.ART_OCR, "editor", "etwas")
    a._sitzung = SitzungAttrappe(widerruf=True)
    a._ausloeser = type("A", (), {"tick": lambda self: None})()
    a.lauf(takt_s=0.01)
    assert a._sitzung.widerrufen_aufrufe == 1
    assert a._speicher.zaehler()[context.ART_OCR] == 0


def test_ohne_marke_laeuft_der_dienst_weiter(tmp_path):
    a = augen(tmp_path)
    a._sitzung = SitzungAttrappe(widerruf=False)
    assert a._widerruf_pruefen() is False


# -- Fokus-Ereignisse vom Bus ----------------------------------------------

def test_der_fenstertitel_wird_nicht_uebernommen():
    """Er ist Angreifertext -- er steht in einem Browsertab, den irgendwer
    benannt hat. Weitergereicht wird nur `resource_class`."""
    f = dm._fenster_aus_ereignis(json.dumps({
        "resource_class": "konsole",
        "caption": "sudo rm -rf / -- jetzt sofort",
        "geometry": {"x": 1, "y": 2, "width": 3, "height": 4}}))
    assert f.klasse == "konsole"
    assert "sudo" not in str(f)


def test_ein_kaputtes_ereignis_wird_verworfen_statt_zu_werfen():
    assert dm._fenster_aus_ereignis("kein json") is None
    assert dm._fenster_aus_ereignis(json.dumps({"geometry": {"x": "hm"}})) is None


def test_drm_kommt_aus_dem_ereignis_durch():
    f = dm._fenster_aus_ereignis(json.dumps({
        "resource_class": "player", "drm": True,
        "geometry": {"x": 0, "y": 0, "width": 9, "height": 9}}))
    assert f.drm is True


# -- Abbau -----------------------------------------------------------------

class PortalAttrappe:
    def __init__(self, wirft=False):
        self.wirft = wirft
        self.zu = 0

    def schliessen(self):
        if self.wirft:
            raise RuntimeError("Portal klemmt")
        self.zu += 1


def test_beim_beenden_wird_die_sitzung_geschlossen(tmp_path):
    """Wer nur den Prozess beendet, laesst die Erlaubnis stehen."""
    a = augen(tmp_path)
    a._portal = PortalAttrappe()
    b = a.beenden()
    assert a._portal.zu == 1 and b["sitzung"] is True and b["pool"] is True


def test_ein_klemmendes_portal_wird_gemeldet_nicht_verschluckt(tmp_path):
    a = augen(tmp_path)
    a._portal = PortalAttrappe(wirft=True)
    assert a.beenden()["sitzung"] is False


def test_der_zustand_nennt_zahlen_und_keinen_inhalt(tmp_path):
    a = augen(tmp_path, text="streng geheim")
    a.einmal_sehen("timer")
    z = a.zustand()
    assert z["gelesen"] == 1
    assert "streng geheim" not in json.dumps(z)


# -- Die Fensterabfrage bei de.daimon.Focus --------------------------------

def test_ohne_watcher_faellt_die_abfrage_auf_none(tmp_path, monkeypatch):
    """Kein Watcher, kein Fenster. Der Rueckfall aufs Vollbild ist teuer, aber
    er sieht wenigstens hin."""
    a = augen(tmp_path)
    monkeypatch.setitem(__import__("sys").modules, "dbus", None)
    assert a.fenster_abfragen() is None


def test_ein_unbekanntes_fenster_zaehlt_nicht_als_fenster(tmp_path):
    """`bekannt: false` heisst „seit dem Start kein Fensterwechsel" -- und
    nicht „ein Fenster der Groesse null"."""
    a = augen(tmp_path)
    a._fenster = None
    f = a._aktuelles_fenster(8, 6)
    assert (f.breite, f.hoehe) == (8, 6)        # Vollbild-Rueckfall


def test_die_abfrage_wird_nur_ohne_gemeldetes_fenster_gestellt(tmp_path):
    """Ein Fokus-Ereignis, das schon da ist, schlaegt die Abfrage -- sonst
    fragte der Dienst jede Runde nach etwas, das er gerade bekommen hat."""
    a = augen(tmp_path)
    a._fenster = Fenster(x=1, y=2, breite=3, hoehe=4, klasse="editor")
    a.fenster_abfragen = lambda: pytest.fail("haette nicht fragen duerfen")
    assert a._aktuelles_fenster(8, 6).klasse == "editor"
