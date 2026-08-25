"""T-3.13b — Markierungsverfolgung und Senkendurchsetzung."""

from __future__ import annotations

import io
import json

import pytest

from daimon.common import taint as T
from daimon.common.protocol import Mark, Marked, tainted, trusted


# --------------------------------------------------------------------------
# K1/K5: die vier Marken und die Ansteckung
# --------------------------------------------------------------------------

def test_ein_nackter_wert_wird_tainted():
    # Vorgabe ist Misstrauen. Ein Wert ohne Marke ist kein vertrauenswuerdiger
    # Wert, sondern einer, ueber den niemand nachgedacht hat.
    assert Marked.from_wire("hallo").mark is Mark.TAINTED


@pytest.mark.parametrize("a,b,erwartet", [
    (Mark.TRUSTED, Mark.TRUSTED, Mark.TRUSTED),
    (Mark.TRUSTED, Mark.USER_PTT, Mark.USER_PTT),
    (Mark.TRUSTED, Mark.TAINTED, Mark.TAINTED),
    (Mark.USER_PTT, Mark.TAINTED, Mark.TAINTED),
    (Mark.USER_PTT, Mark.USER_AUDIO, Mark.USER_AUDIO),
    (Mark.USER_AUDIO, Mark.TAINTED, Mark.TAINTED),
])
def test_verkettung_nimmt_die_strengste_marke(a, b, erwartet):
    assert T.verketten(Marked("x", a), Marked("y", b)).mark is erwartet
    # Und in der anderen Reihenfolge dasselbe -- sonst haengt die Sicherheit
    # an der Schreibrichtung.
    assert T.verketten(Marked("y", b), Marked("x", a)).mark is erwartet


def test_verkettung_haengt_die_werte_aneinander():
    v = T.verketten(trusted("Es ist "), tainted("14:37"))
    assert v.value == "Es ist 14:37" and v.mark is Mark.TAINTED


def test_verkettung_nimmt_auch_nackte_werte_und_misstraut_ihnen():
    v = T.verketten(trusted("a"), "b")
    assert v.value == "ab" and v.mark is Mark.TAINTED


# --------------------------------------------------------------------------
# K2/K3: die zwei Fehlerfaelle, die nicht verwechselt werden duerfen
# --------------------------------------------------------------------------

def test_ein_roher_string_gilt_als_tainted_und_wird_protokolliert():
    strom = io.StringIO()
    wert = T.pruefe_senke("roher text", senke="durchgang2",
                          log=T.Protokoll(strom))
    assert wert.mark is Mark.TAINTED
    # Positivkontrolle: die Zeile ist wirklich da, nicht nur beabsichtigt.
    assert "marke_fehlt" in strom.getvalue()


def test_ein_roher_string_darf_nicht_mehr_als_ein_markierter():
    # "Kein Wurf" heisst: es fliegt kein Fehler, WEIL die Marke fehlt. Es
    # heisst nicht, dass ein Wert ohne Marke die Tabelle umgeht -- sonst waere
    # das Weglassen der Marke die bequemste Umgehung von allem.
    with pytest.raises(T.SenkenFehler):
        T.pruefe_senke("roher text", senke="durchgang1")
    # An einer Senke, die `tainted` nimmt, geht er durch -- mit Marke.
    assert T.pruefe_senke("roher text", senke="durchgang2").mark is Mark.TAINTED


def test_markiertes_material_an_verbotener_senke_wirft():
    with pytest.raises(T.SenkenFehler) as exc:
        T.pruefe_senke(tainted("Rechnung.pdf"), senke="durchgang1")
    # Die Meldung nennt Senke und Marke -- sonst sucht man im falschen Modul.
    assert "durchgang1" in str(exc.value) and "tainted" in str(exc.value)


def test_user_audio_wirft_an_durchgang1_und_geht_durch_an_durchgang2():
    with pytest.raises(T.SenkenFehler):
        T.pruefe_senke(Marked("hallo", Mark.USER_AUDIO), senke="durchgang1")
    ok = T.pruefe_senke(Marked("hallo", Mark.USER_AUDIO), senke="durchgang2")
    assert ok.mark is Mark.USER_AUDIO


@pytest.mark.parametrize("senke,erlaubt,verboten", [
    ("durchgang1", Mark.USER_PTT, Mark.TAINTED),
    ("durchgang2", Mark.TAINTED, None),
    ("auth_vorschau", Mark.USER_PTT, Mark.USER_AUDIO),
    ("tts_auf_anfrage", Mark.USER_AUDIO, None),
    ("tts_ungefragt", Mark.TRUSTED, Mark.TAINTED),
    ("audit_klartext", Mark.USER_PTT, Mark.TAINTED),
])
def test_je_senke_ein_erlaubter_und_ein_verbotener_fall(senke, erlaubt, verboten):
    assert T.pruefe_senke(Marked("x", erlaubt), senke=senke).mark is erlaubt
    if verboten is not None:
        with pytest.raises(T.SenkenFehler):
            T.pruefe_senke(Marked("x", verboten), senke=senke)


def test_eine_unbekannte_senke_ist_ein_fehler_und_kein_freibrief():
    with pytest.raises(T.SenkenFehler):
        T.pruefe_senke(trusted("x"), senke="gibtsnicht")


# --------------------------------------------------------------------------
# Die Tabelle kennt auch, was es noch nicht gibt
# --------------------------------------------------------------------------

@pytest.mark.parametrize("senke", ["kurzzeitgedaechtnis", "langzeitgedaechtnis",
                                   "proaktive_ausloeser"])
def test_die_tabelle_kennt_die_senken_der_spaeteren_phasen(senke):
    # Sie existieren im System nicht, aber die Regel steht schon da -- wer sie
    # baut, muss sie nicht neu erfinden.
    assert senke in T.SENKEN


def test_user_audio_erreicht_das_gedaechtnis_nicht():
    for senke in ("kurzzeitgedaechtnis", "langzeitgedaechtnis",
                  "proaktive_ausloeser"):
        with pytest.raises(T.SenkenFehler):
            T.pruefe_senke(Marked("x", Mark.USER_AUDIO), senke=senke)


# --------------------------------------------------------------------------
# K6: die Marke ueberlebt die Serialisierung
# --------------------------------------------------------------------------

def test_die_marke_ueberlebt_json_hin_und_zurueck():
    roh = json.dumps(tainted("Fenstertitel").to_wire())
    wieder = Marked.from_wire(json.loads(roh))
    assert wieder.mark is Mark.TAINTED and wieder.value == "Fenstertitel"


def test_die_marke_ueberlebt_verschachtelt_in_einer_nachricht():
    # Der Fall, der in der Praxis bricht: die Marke steht nicht oben, sondern
    # in einem Feld eines Feldes.
    nachricht = {"v": 1, "art": "ereignis",
                 "nutzlast": {"titel": tainted("KANARIE").to_wire()}}
    roh = json.dumps(nachricht)
    zurueck = json.loads(roh)
    wieder = Marked.from_wire(zurueck["nutzlast"]["titel"])
    assert wieder.mark is Mark.TAINTED and wieder.value == "KANARIE"


# --------------------------------------------------------------------------
# K7: Hook-Nutzlasten -- nur die geschlossene Aufzaehlung ist trusted
# --------------------------------------------------------------------------

from daimon.hookbridge import bridge as B  # noqa: E402


def test_hook_event_name_ist_trusted_der_rest_nicht():
    # Design 5.2: "Vertrauenswuerdig ist nur `hook_event_name` (geschlossene
    # Aufzaehlung) und die daraus abgeleitete Mood." v2.1 fuehrte die ganzen
    # Hook-Metadaten als trusted und widersprach damit dem eigenen
    # Bedrohungsmodell.
    roh = {"hook_event_name": "PostToolUse", "session_id": "s1",
           "cwd": "/home/x/projekt", "message": "beliebiger Text",
           "last_assistant_message": "auch beliebig", "error": "und das",
           "tool_name": "Bash"}
    markiert = B.markiere_nutzlast(roh)
    assert markiert["hook_event_name"].mark is Mark.TRUSTED
    for feld in ("message", "last_assistant_message", "error", "cwd",
                 "tool_name"):
        assert markiert[feld].mark is Mark.TAINTED, feld


def test_ein_neues_hook_feld_ist_automatisch_tainted():
    # Die Ausnahmeliste ist keine Definition: was morgen dazukommt, ist
    # markiert, bis jemand begruendet, warum nicht.
    markiert = B.markiere_nutzlast({"hook_event_name": "Stop",
                                    "voellig_neues_feld": "irgendwas"})
    assert markiert["voellig_neues_feld"].mark is Mark.TAINTED


def test_ein_gefaelschter_hook_event_name_wird_nicht_trusted():
    # `hook_event_name` ist nur deshalb trusted, WEIL es eine geschlossene
    # Aufzaehlung ist. Ein Wert ausserhalb der Aufzaehlung ist es nicht.
    markiert = B.markiere_nutzlast({"hook_event_name": "Frei erfunden"})
    assert markiert["hook_event_name"].mark is Mark.TAINTED


# --------------------------------------------------------------------------
# K10: die Auth-Vorschau nimmt tainted nur escapt und begrenzt
# --------------------------------------------------------------------------

from daimon.auth import preview as P  # noqa: E402


def test_die_vorschau_nimmt_tainted_und_escapt_es():
    kanarie = "urlaаub‮.png"          # kyrillisches a + Bidi
    text = P.vorschau(aktion="datei.papierkorb", ziel=tainted(kanarie),
                      umkehr="papierkorb")
    # Positivkontrolle: es kam ueberhaupt etwas an.
    assert "urla" in text
    # Und der Rohtext steht NICHT drin -- weder das kyrillische a noch Bidi.
    assert "а" not in text and "‮" not in text


def test_die_vorschau_weist_user_audio_ab():
    with pytest.raises(T.SenkenFehler):
        P.vorschau(aktion="datei.papierkorb",
                   ziel=Marked("~/x.png", Mark.USER_AUDIO),
                   umkehr="papierkorb")


def test_die_vorschau_nimmt_weiterhin_rohe_zeichenketten():
    # Abwaertskompatibel: T-1.7 ist eingefroren und ruft mit rohem str auf.
    text = P.vorschau(aktion="datei.papierkorb", ziel="~/Bilder/urlaub.png",
                      umkehr="papierkorb")
    assert "urlaub.png" in text


# --------------------------------------------------------------------------
# K6: die Marke ueberlebt einen ECHTEN Socket, nicht nur dumps/loads
# --------------------------------------------------------------------------

def test_die_marke_ueberlebt_einen_echten_unix_socket(tmp_path):
    import socket
    import threading

    pfad = str(tmp_path / "grenze.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(pfad)
    srv.listen(1)
    empfangen = []

    def bediene():
        conn, _ = srv.accept()
        with conn:
            roh = conn.makefile("rb").readline()
            empfangen.append(json.loads(roh))

    t = threading.Thread(target=bediene, daemon=True)
    t.start()

    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(pfad)
    c.sendall(json.dumps({"titel": tainted("KANARIE-3d13b").to_wire()}).encode()
              + b"\n")
    c.close()
    t.join(5)
    srv.close()

    assert empfangen, "nichts angekommen (Positivkontrolle)"
    wieder = Marked.from_wire(empfangen[0]["titel"])
    assert wieder.mark is Mark.TAINTED and wieder.value == "KANARIE-3d13b"


def test_eine_marke_die_unterwegs_verlorengeht_wird_wieder_tainted():
    # Der Mutantenfall: jemand serialisiert nur den Wert. Beim Lesen darf
    # daraus NICHT `trusted` werden -- Vorgabe ist Misstrauen.
    verloren = json.loads(json.dumps({"titel": trusted("Sitzung 3").value}))
    assert Marked.from_wire(verloren["titel"]).mark is Mark.TAINTED


# --------------------------------------------------------------------------
# K4/K9/K11: der Router als geprueftes Tor
# --------------------------------------------------------------------------

from daimon.mind import router as R  # noqa: E402


class Quellen:
    def uhrzeit(self):
        return "14:37"

    def lautstaerke(self):
        return {"prozent": 42, "stumm": False}

    def sitzung(self):
        return {"sitzungen": 1, "mood": "idle", "session_id": "s1"}

    def fenster(self):
        return [{"id": "k1", "titel": "A — Discord", "app_id": "discord"}]


class MindAttrappe:
    def __init__(self):
        self.koerper = []

    def frage_api(self, text, kontext=None, *, marke: str = "tainted"):
        self.koerper.append(text)
        return {"v": 1, "ok": True, "status": 200, "antwort": "gut",
                "durchgang": 2, "marke": "tainted"}


def router():
    m = MindAttrappe()
    return R.Router(quellen=Quellen(), mind=m), m


def test_tainted_material_erreicht_durchgang_eins_nicht():
    # Nicht still gefiltert: es soll auffallen. Design 5.2 und die
    # Akzeptanzliste sind sich da einig.
    r, m = router()
    a = r.frage({"v": 1, "art": "frage", "text": "wie spaet ist es",
                 "marke": "tainted"})
    assert a["ok"] is False and a["grund"] == "marke_verboten"
    assert m.koerper == []


def test_dieselbe_frage_mit_user_ptt_geht_durch():
    # Positivkontrolle: ohne sie waere die Sperre auch erfuellt, wenn der
    # Router gar nichts mehr beantwortet.
    r, _ = router()
    a = r.frage({"v": 1, "art": "frage", "text": "wie spaet ist es",
                 "marke": "user_ptt"})
    assert a["ok"] is True and a["weg"] == "lokal"


@pytest.mark.parametrize("text", ["mach das", "mach das mal", "beende das",
                                  "schliess das"])
def test_keine_handlungs_anapher_ohne_ziel(text):
    # "Mach das" verweist nicht auf Assistententext oder Kontext. Die aktuelle
    # Aeusserung muss Aktion UND Ziel nennen, sonst kommt eine Rueckfrage.
    r, m = router()
    a = r.frage({"v": 1, "art": "frage", "text": text, "marke": "user_ptt"})
    assert a["weg"] == "rueckfrage", a
    assert a["api"] is False and m.koerper == []
    assert a["marke"] == "trusted"


@pytest.mark.parametrize("text", ["mach das fenster zu",
                                  "schliess discord",
                                  "stell die lautstaerke auf 30"])
def test_mit_benanntem_ziel_bleibt_es_eine_abgelehnte_aktion(text):
    # Gegenprobe: die Rueckfrage darf nicht jede Aktion verschlucken.
    r, _ = router()
    a = r.frage({"v": 1, "art": "frage", "text": text, "marke": "user_ptt"})
    assert a["weg"] == "abgelehnt", a


def test_die_modellantwort_bleibt_tainted_auch_bei_user_ptt():
    r, _ = router()
    a = r.frage({"v": 1, "art": "frage", "text": "erklaer mir monaden",
                 "marke": "user_ptt"})
    assert a["marke"] == "tainted"


def test_audit_bekommt_hash_und_laenge_statt_klartext():
    # L1: die Senke wirft, und DAS hier ist der Weg daran vorbei -- sichtbar
    # im Code, nicht als Fussnote in der Tabelle.
    with pytest.raises(T.SenkenFehler):
        T.pruefe_senke(tainted("geheimer Fenstertitel"), senke="audit_klartext")
    r = T.audit_redigiert(tainted("geheimer Fenstertitel"))
    assert r.mark is Mark.TRUSTED
    assert r.value["laenge"] == len("geheimer Fenstertitel")
    assert len(r.value["sha256"]) == 64
    # Der Klartext ist weg.
    assert "Fenstertitel" not in json.dumps(r.value)
    # Und das Ergebnis darf ins Audit.
    assert T.pruefe_senke(r, senke="audit_klartext").mark is Mark.TRUSTED
