"""T-3.13 — Tests fuer Durchgang 2 (kontextfaehig, werkzeuglos)."""

from __future__ import annotations

import json

import pytest

from daimon.mind import answer as A
from daimon.mind import router as R


class EgressAttrappe:
    """Nimmt den Koerper entgegen und merkt ihn sich BYTEWEISE.

    Nicht als dict: die Zusage "keine Werkzeugliste im Koerper" gilt fuer das,
    was wirklich gesendet wird, und nicht fuer das, was ich beim Bauen im Kopf
    hatte.
    """

    def __init__(self, antwort_text="Alles gut.", fehler=None):
        self.koerper: list[bytes] = []
        self.antwort_text = antwort_text
        self.fehler = fehler

    def frage_api(self, frage, kontext=None, *, marke: str = "user_ptt"):
        koerper = {"model": "m", "max_tokens": 8, "system": "Du bist Ember.",
                   "messages": [{"role": "user", "content": frage}]}
        if kontext:
            koerper["messages"][0]["content"] += "\n" + json.dumps(kontext)
        self.koerper.append(json.dumps(koerper, ensure_ascii=False).encode())
        if self.fehler:
            return self.fehler
        return {"v": 1, "ok": True, "status": 200,
                "antwort": {"content": [{"type": "text",
                                         "text": self.antwort_text}]}}


def durchgang(antwort_text="Alles gut.", fehler=None):
    e = EgressAttrappe(antwort_text, fehler)
    return A.Durchgang2(mind=e), e


# --------------------------------------------------------------------------
# K1: keine Werkzeugliste -- gemessen am gesendeten Koerper
# --------------------------------------------------------------------------

def test_der_koerper_traegt_keine_werkzeugliste():
    d, e = durchgang()
    d.beantworte("erklaer mir monaden", marke="user_ptt")
    roh = e.koerper[-1]
    assert b"tools" not in roh and b"tool_choice" not in roh
    # Positivkontrolle: die Frage steht drin, der Koerper ist nicht leer.
    assert "monaden".encode() in roh


# --------------------------------------------------------------------------
# K2/K3: nur Text, und ein Aktionsvorschlag wird verworfen
# --------------------------------------------------------------------------

def test_die_antwort_ist_ausschliesslich_text():
    d, _ = durchgang()
    a = d.beantworte("was meinst du", marke="user_ptt")
    assert isinstance(a["antwort"], str)
    for verboten in ("action", "aktion", "ziel", "window_ref", "tool"):
        assert verboten not in a, verboten


def test_ein_wohlgeformter_aktionsvorschlag_wird_verworfen():
    vorschlag = '{"action": "close_window", "window_ref": "w_1"}'
    d, _ = durchgang(antwort_text=f"Klar, ich mache das:\n{vorschlag}")
    a = d.beantworte("mach mal was", marke="user_ptt")
    assert a["aktionsvorschlag_erkannt"] is True
    # Kein Feld traegt ihn -- das Schema hat keines.
    assert "action" not in json.dumps(
        {k: v for k, v in a.items() if k != "antwort"})


def test_eine_harmlose_antwort_setzt_das_flag_nicht():
    # Gegenprobe: ohne sie waere "verworfen" auch durch "nie erkannt" erfuellt.
    d, _ = durchgang(antwort_text="Monaden sind Monoide in der Kategorie der "
                                  "Endofunktoren.")
    a = d.beantworte("erklaer mir monaden", marke="user_ptt")
    assert a["aktionsvorschlag_erkannt"] is False


@pytest.mark.parametrize("text", [
    '{"tool": "systemctl", "args": ["stop", "daimon-hub"]}',
    'Hier: {"aktion": "fenster_schliessen", "ziel": "w_2"}',
    '```json\n{"action": "set_volume", "value": 30}\n```',
])
def test_verschiedene_gestalten_eines_vorschlags_werden_erkannt(text):
    d, _ = durchgang(antwort_text=text)
    a = d.beantworte("egal", marke="user_ptt")
    assert a["aktionsvorschlag_erkannt"] is True


# --------------------------------------------------------------------------
# K4/K5: Marken
# --------------------------------------------------------------------------

@pytest.mark.parametrize("marke", ["user_ptt", "user_audio", "trusted",
                                   "tainted"])
def test_die_antwort_ist_immer_tainted(marke):
    # "Aus einem Modell kommt nichts Vertrauenswuerdiges in Textform"
    # -- Design 5.2, ohne Ausnahme, auch bei user_ptt.
    d, _ = durchgang()
    assert d.beantworte("frag", marke=marke)["marke"] == "tainted"


def test_user_audio_wird_in_durchgang_zwei_beantwortet():
    d, e = durchgang()
    a = d.beantworte("wie geht es dir", marke="user_audio")
    assert a["ok"] is True and a["durchgang"] == 2
    assert len(e.koerper) == 1


# --------------------------------------------------------------------------
# K6: die Struktur des Kontexts steht, der Inhalt ist leer
# --------------------------------------------------------------------------

def test_der_kontext_ist_vorhanden_und_leer():
    d, e = durchgang()
    d.beantworte("frag", marke="user_ptt")
    koerper = json.loads(e.koerper[-1])
    inhalt = koerper["messages"][0]["content"]
    angehaengt = json.loads(inhalt.split("\n", 1)[1])
    # Die Struktur steht, der Inhalt ist leer. Beides gehoert geprueft: ohne
    # das Erste faellt in P5 auf, dass es sie nie gab, ohne das Zweite waere
    # heute schon Kontext drin, den niemand deklassifiziert hat.
    assert angehaengt["kontext"] == {"quellen": [], "deklassifiziert": []}


def test_kein_bildschirmtext_und_keine_historie_im_koerper():
    d, e = durchgang()
    d.beantworte("was steht auf dem bildschirm", marke="user_ptt")
    roh = e.koerper[-1]
    for verboten in (b"ocr", b"screenshot", b"historie", b"history",
                     b"fenstertitel"):
        assert verboten not in roh.lower(), verboten


# --------------------------------------------------------------------------
# K8: ein Fehler wird sprechbar
# --------------------------------------------------------------------------

def test_ein_api_fehler_wird_sprechbar_ohne_nutzertext():
    d, _ = durchgang(fehler={"v": 1, "ok": False, "grund": "ziel_weg",
                             "meldung": "TimeoutError"})
    a = d.beantworte("erklaer mir monaden", marke="user_ptt")
    assert a["ok"] is False and a["grund"] == "egress_weg"
    assert a["antwort"], "sprechbar, nicht stumm"
    assert "monaden" not in json.dumps(a, ensure_ascii=False)


# --------------------------------------------------------------------------
# K9: der Router benutzt Durchgang 2 und laesst Durchgang 1 unberuehrt
# --------------------------------------------------------------------------

class Quellen:
    def uhrzeit(self):
        return "14:37"

    def lautstaerke(self):
        return {"prozent": 42, "stumm": False}

    def sitzung(self):
        return {"sitzungen": 1, "mood": "idle", "session_id": "s1"}

    def fenster(self):
        return [{"id": "k1", "titel": "A — Discord", "app_id": "discord"}]


def test_der_router_schickt_inhaltliches_durch_durchgang_zwei():
    e = EgressAttrappe()
    r = R.Router(quellen=Quellen(), mind=A.Durchgang2(mind=e))
    a = r.frage({"v": 1, "art": "frage", "text": "erklaer mir monaden",
                 "marke": "user_ptt"})
    assert a["ok"] is True and a["weg"] == "api"
    assert a["durchgang"] == 2
    assert len(e.koerper) == 1


def test_durchgang_eins_bleibt_unberuehrt():
    e = EgressAttrappe()
    r = R.Router(quellen=Quellen(), mind=A.Durchgang2(mind=e))
    for text, weg in (("wie spaet ist es", "lokal"),
                      ("welche fenster sind offen", "lokal"),
                      ("mach das fenster zu", "abgelehnt")):
        a = r.frage({"v": 1, "art": "frage", "text": text, "marke": "user_ptt"})
        assert a["weg"] == weg, (text, a)
    assert e.koerper == [], "keine lokale Absicht darf nach draussen"


def test_user_audio_bleibt_in_durchgang_eins_verboten():
    # Die zweite Haelfte der Senkentabelle: erlaubt in 2, verboten in 1.
    e = EgressAttrappe()
    r = R.Router(quellen=Quellen(), mind=A.Durchgang2(mind=e))
    a = r.frage({"v": 1, "art": "frage", "text": "wie spaet ist es",
                 "marke": "user_audio"})
    assert a["ok"] is False and a["grund"] == "marke_verboten"
    assert e.koerper == []


def test_auch_die_absage_nennt_den_durchgang():
    # Sonst ist eine Absage aus Durchgang 2 von einer des Routers nicht zu
    # unterscheiden -- und man sucht den Fehler an der falschen Stelle.
    e = EgressAttrappe(fehler={"v": 1, "ok": False, "grund": "ziel_weg",
                              "meldung": "TimeoutError"})
    r = R.Router(quellen=Quellen(), mind=A.Durchgang2(mind=e))
    a = r.frage({"v": 1, "art": "frage", "text": "erklaer mir monaden",
                 "marke": "user_ptt"})
    assert a["ok"] is False and a["grund"] == "egress_weg"
    assert a["durchgang"] == 2 and a["weg"] == "api"


def test_user_audio_mit_inhaltsfrage_erreicht_durchgang_zwei():
    # Die Senkentabelle sperrt user_audio gegen den WERKZEUGFAEHIGEN Durchgang,
    # nicht gegen den Router. Eine gespoofte Aeusserung darf eine Frage
    # beantworten lassen -- genau deshalb hat Durchgang 2 keine Werkzeuge.
    e = EgressAttrappe()
    r = R.Router(quellen=Quellen(), mind=A.Durchgang2(mind=e))
    a = r.frage({"v": 1, "art": "frage", "text": "erklaer mir monaden",
                 "marke": "user_audio"})
    assert (a["ok"], a["weg"], a["durchgang"]) == (True, "api", 2)
    assert a["marke"] == "tainted"
    assert len(e.koerper) == 1


@pytest.mark.parametrize("text", ["wie spaet ist es", "welche fenster sind offen",
                                  "mach das fenster zu"])
def test_user_audio_bleibt_gegen_durchgang_eins_gesperrt(text):
    # Beide Haelften gehoeren zusammen: erlaubt in 2, verboten in 1 -- und
    # verboten heisst auch hier VOR jeder Quelle und vor jedem Ticket.
    e = EgressAttrappe()
    r = R.Router(quellen=Quellen(), mind=A.Durchgang2(mind=e))
    a = r.frage({"v": 1, "art": "frage", "text": text, "marke": "user_audio"})
    assert a["ok"] is False and a["grund"] == "marke_verboten"
    assert e.koerper == []
