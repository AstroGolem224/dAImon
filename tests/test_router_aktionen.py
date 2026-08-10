"""T-4.19 — eine Aktion braucht eine Absichtsmarke, sonst gar nichts.

Beim Schreiben dieser Tests kam heraus, dass der gefaehrlichste Fall schon
FRUEHER faellt als gedacht: `user_audio` scheitert bereits an der
Senkentabelle aus T-3.13b (`marke_verboten`) und erreicht den Aktionszweig
nie. Das ist die staerkere Zusage -- der Text geht gar nicht erst durch den
werkzeugfaehigen Durchgang. Die neue Regel hier deckt den Rest ab: alles, was
die Senke passiert (`trusted`) und trotzdem keine Absichtsmarke ist.
"""
from __future__ import annotations

import pytest

from daimon.mind import router as R


class Quellen:
    def uhrzeit(self): return "12:00"
    def lautstaerke(self): return {"prozent": 30, "stumm": False}
    def fenster(self): return []
    def sitzung(self): return {"mood": "idle"}


def frage(marke, text="mach das fenster zu"):
    r = R.Router(quellen=Quellen(), mind=None, testprofil=True)
    return r.frage({"v": 1, "art": "frage", "text": text, "marke": marke})


def test_mit_ptt_bleibt_es_bei_der_bisherigen_ablehnung():
    """Positivkontrolle: die Marke ist da, es fehlt nur der Ausfuehrer."""
    a = frage("user_ptt")
    assert a["weg"] == "abgelehnt" and a["absicht"] == "aktion"
    assert "Ausfuehrer" in a["antwort"]


def test_user_audio_erreicht_den_aktionszweig_gar_nicht():
    """Der Angriffsweg, den eine Rueckfrage eroeffnen wuerde, ist zu.

    Gefaelschtes Audio -- ein Video, ein Lautsprecher, die eigene
    Sprachausgabe -- koennte den Nutzer sonst mit Dialogen zumuellen, bis er
    einen wegklickt. Der Klick waere echt, die Absicht nicht.

    Die Ablehnung bleibt (ok False, marke_verboten) -- die eingefrorenen
    Pruefstaende messen genau dieses Paar. NEU (T-4.19-Akzeptanzliste): sie
    traegt eine gesprochene Rueckmeldung, dieselbe kuratierte Vorlage wie im
    trusted-Zweig. Kein Dialog, kein Klick -- nur ein Satz.
    """
    a = frage("user_audio")
    assert a["ok"] is False
    assert a["grund"] == "marke_verboten"
    assert a["weg"] is None
    assert "Absichtsmarke" in a["antwort"]
    assert "Push-to-Talk" in a["antwort"]
    assert a["marke"] == "trusted"


def test_user_audio_erzeugt_auch_ohne_ziel_keine_rueckfrage():
    a = frage("user_audio", text="mach das")
    assert a.get("weg") != "rueckfrage"


def test_user_audio_mit_auskunftsfrage_bekommt_keine_rueckmeldung():
    """Die Rueckmeldung gilt dem Aktionswunsch, nicht jeder Absage: eine
    Inhaltsfrage unter user_audio laeuft ueber Durchgang 2, eine lokale
    Auskunft bleibt stumm marke_verboten wie bisher."""
    a = frage("user_audio", text="wie spaet ist es?")
    assert a["ok"] is False and a["grund"] == "marke_verboten"
    assert "antwort" not in a


def test_tainted_aktionswunsch_bleibt_stumm():
    """Die Rueckmeldung gehoert dem Menschen, der wirklich gesprochen hat --
    einem injizierten Text sagt niemand, wie er eskaliert."""
    a = frage("tainted")
    assert a["ok"] is False and a["grund"] == "marke_verboten"
    assert "antwort" not in a


def test_trusted_ohne_ptt_wird_werkzeuglos_abgelehnt():
    """`trusted` passiert die Senke -- und ist trotzdem keine Absichtsmarke."""
    a = frage("trusted")
    assert a["weg"] == "abgelehnt" and a["absicht"] == "aktion"
    assert "Absichtsmarke" in a["antwort"]
    assert "Push-to-Talk" in a["antwort"]


def test_trusted_ohne_ptt_erzeugt_auch_ohne_ziel_keine_rueckfrage():
    """Die Markenpruefung steht VOR der Frage nach dem Ziel -- sonst
    entstuende genau der Dialog, den sie verhindern soll."""
    a = frage("trusted", text="mach das")
    assert a["weg"] == "abgelehnt"
    assert "Absichtsmarke" in a["antwort"]


def test_die_formulierung_spricht_von_der_marke_nicht_von_koerpern():
    """Design 1.3: ein Tastendruck belegt, dass jemand etwas wollte -- nicht,
    dass er es darf."""
    text = frage("trusted")["antwort"].lower()
    assert "absichtsmarke" in text
    for falsch in ("physisch", "koerperlich", "anwesend", "autorisier"):
        assert falsch not in text


def test_eine_auskunftsfrage_bleibt_unberuehrt():
    """Die neue Regel greift nur bei `absicht == aktion`."""
    a = frage("trusted", text="wie spaet ist es?")
    assert a["absicht"] == "uhrzeit" and a["ok"]
