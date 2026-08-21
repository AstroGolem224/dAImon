"""Der Hub-Anschluss: eine Zeile auf `aktion.sock`, eine Wirkung.

Der Endpunkt ist AUSDRUECKLICH kein Produzent -- wie gpu, tts und ticket.
Ein Test haelt das fest: die Produzententabelle bleibt unveraendert.
"""
from __future__ import annotations

import json
import threading

import pytest

from daimon.common import ipc
from daimon.hub import daemon as D


class Marken:
    """So viel Markenbuch, wie der Aktionsendpunkt anfasst."""

    def __init__(self, gueltig: bool) -> None:
        self.gueltig = gueltig

    def aktuelle(self):
        # Der Hub fragt sich selbst, welche Runde offen ist -- die turn_id
        # gibt er nie heraus.
        return "r1" if self.gueltig else None

    def initiator(self, turn_id):
        return "user" if self.gueltig else "background"


class _Consent:
    """Ein Mensch, der zustimmt. Oder schweigt.

    Seit dem Fix von T-4.4 K8 (Commit auf main, 19.08.) fuehrt der Socketweg
    NICHT mehr an der Vorschau vorbei: `quelle` kommt vom Hub und ist dort
    immer `modell`. Wer eine ausgefuehrte Aktion pruefen will, braucht also
    eine erteilte Zustimmung -- vorher genuegte `quelle: parser` in der
    Nachricht, und genau das war der Befund.
    """

    def __init__(self, antwort: str = "granted") -> None:
        self.antwort = antwort
        self.rueckfragen = []

    def stellen(self, **kw):
        self.rueckfragen.append(kw)
        return type("R", (), {"id": f"r{len(self.rueckfragen)}",
                              "zustand": self.antwort,
                              "frist": 0.0})()

    def warten(self, rueckfrage, *, timeout_s=0.0, **_):
        return self.antwort


def hub(tmp_path, monkeypatch, *, marke_gueltig=True, broker_ok=True,
        zustimmung=None):
    h = D.Hub.__new__(D.Hub)          # ohne __init__: kein Socket, kein Thread
    h.log = _Log()
    h.marken = Marken(marke_gueltig)
    from daimon.hub.marks import FreigabeBuch
    h.freigaben = FreigabeBuch()
    h.consent = zustimmung
    h.runtime_dir = tmp_path
    h._aktion = None
    h._aktion_lock = threading.RLock()
    # Seit dem 17.08. teilen Aktionsweg und Gate EIN Audit -- eine
    # Kette, ein Objekt. `__new__` legt keine Attribute an, also
    # gehoert es hierher.
    h._audit = None
    h.gesprochen = []

    class Cfg:
        state_dir = tmp_path / "state"
    h.cfg = Cfg()
    h.tts_anfrage = lambda a: (h.gesprochen.append(a["text"]) or
                               {"v": 1, "ok": True})
    h.zugestellt = []
    h._auftrag_zustellen = lambda auftrag: (
        h.zugestellt.append(auftrag) or
        {"ok": broker_ok, "grund": "" if broker_ok else "dbus"})
    return h


class _Log:
    def info(self, *a, **kw): pass
    def warn(self, *a, **kw): pass
    def error(self, *a, **kw): pass


def anfrage(**kw):
    grund = {"v": 1, "art": "ausfuehren", "action_id": "media.playpause",
             "params": {}, "turn_id": "r1",
             "tool_use_id": "t1", "session_id": "s1"}
    grund.update(kw)
    return grund


def test_der_endpunkt_ist_kein_produzent():
    """Kein Eintrag in der Produzententabelle -- wie gpu, tts, ticket."""
    assert "mind" not in ipc.PRODUZENTEN
    for menge in ipc.PRODUZENTEN.values():
        assert "action_request" not in menge
        assert "aktion" not in menge


def test_eine_zugestimmte_aktion_unter_marke_wird_ausgefuehrt(tmp_path,
                                                              monkeypatch):
    """Frueher hiess dieser Test "eine DIREKTE Aktion" und schickte
    `quelle: parser` mit -- damit lief er an der Vorschau vorbei.

    Genau das war BEFUND T-4.4 K8: die Direktbefehl-Ausnahme haengt am
    Katalogflag UND an der Quelle, und die Quelle kam aus der Nachricht. Der
    Weg ueber `aktion.sock` ist jetzt immer `modell`; ausgefuehrt wird nach
    ZUSTIMMUNG. Dass die Tests selbst den Umgehungsweg benutzt haben, ist
    ein Hinweis darauf, wie bequem er war.
    """
    zu = _Consent()
    h = hub(tmp_path, monkeypatch, zustimmung=zu)
    a = h.aktion_anfrage(anfrage())
    assert a["ok"] and a["ausgefuehrt"], a
    assert a["direkt"] is False, "der Socketweg ist nie der Hub-Parser"
    assert len(zu.rueckfragen) == 1, "es gab keine Vorschau"
    assert len(h.zugestellt) == 1
    assert h.zugestellt[0].audience == "dbus"
    assert h.zugestellt[0].ticket


def test_der_socketweg_bekommt_die_direktausnahme_NICHT(tmp_path, monkeypatch):
    """DER BEFUND, an der Naht gemessen: dieselbe Aktion, einmal ehrlich und
    einmal mit `quelle: parser` in der Nachricht -- beide muessen gleich
    ausgehen."""
    zu = _Consent("declined")
    h = hub(tmp_path, monkeypatch, zustimmung=zu)
    ehrlich = h.aktion_anfrage(anfrage())
    behauptet = h.aktion_anfrage(anfrage(quelle="parser"))
    assert ehrlich["verdikt"] == behauptet["verdikt"] == "ask"
    assert not ehrlich["ausgefuehrt"] and not behauptet["ausgefuehrt"]
    assert h.zugestellt == []


def test_ohne_gueltige_marke_passiert_nichts(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch, marke_gueltig=False)
    a = h.aktion_anfrage(anfrage())
    assert a["ok"] and not a["ausgefuehrt"]
    assert a["verdikt"] == "deny"
    assert h.zugestellt == []


def test_eine_modellausgabe_wartet_auf_den_menschen(tmp_path, monkeypatch):
    """Kommt keine Antwort, ist das `cancelled` -- kein `declined`.

    Die Frist wird hier auf 50 ms gedreht; ein Test, der zwei Minuten
    wartet, wird nie gefahren.
    """
    monkeypatch.setattr(D, "RUECKFRAGE_FRIST_S", 0.05)
    h = hub(tmp_path, monkeypatch)
    a = h.aktion_anfrage(anfrage(quelle="modell"))
    assert not a["ausgefuehrt"]
    assert a["grund"] == "cancelled"
    assert h.zugestellt == []


def test_eine_unbekannte_aktion_wird_abgewiesen(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch)
    a = h.aktion_anfrage(anfrage(action_id="kwin:Kill Window"))
    assert a["verdikt"] == "deny" and a["grund"] == "unknown_action"
    assert h.zugestellt == []


def test_kaputte_anfragen_werden_benannt(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch)
    assert h.aktion_anfrage("kein dict")["grund"] == "unlesbar"
    assert h.aktion_anfrage(anfrage(art="loeschen"))["grund"] == "unbekannte_art"
    assert h.aktion_anfrage(anfrage(action_id=""))["grund"] == "keine_aktion"
    assert h.aktion_anfrage(anfrage(params=[1, 2]))["grund"] == "params_unlesbar"
    assert h.zugestellt == []


def test_der_hub_spricht_ueber_den_torwaechter(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch, marke_gueltig=False)
    h.aktion_anfrage(anfrage())
    assert h.gesprochen == ["Das darf ich nicht."]


def test_die_dauer_je_hop_kommt_zurueck(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch, zustimmung=_Consent())
    a = h.aktion_anfrage(anfrage())
    assert "policy" in a["dauer_ms"] and "broker" in a["dauer_ms"]


def test_der_broker_pfad_steht_im_hub_nicht_im_auftrag(tmp_path, monkeypatch):
    """Stuende er im Auftrag, koennte ein Absender sich seinen Broker
    aussuchen."""
    from daimon.common.order import FELDER
    assert "socket" not in FELDER and "broker" not in FELDER
    assert set(D.BROKER_SOCKETS) <= {"dbus", "fs", "exec", "input"}


def test_die_audience_kommt_aus_dem_katalog_nicht_aus_der_anfrage(tmp_path, monkeypatch):
    """Ein Absender, der `audience` setzt, sucht sich seinen Broker aus.

    Dasselbe Prinzip wie bei turn_id und Socketpfad: ein Feld, das der
    Absender setzt, sagt nichts. Massgeblich ist der Katalogeintrag;
    media.playpause traegt keinen, also gilt die Vorgabe dbus.
    """
    h = hub(tmp_path, monkeypatch, zustimmung=_Consent())
    a = h.aktion_anfrage(anfrage(audience="fs"))
    assert a["ok"] and a["ausgefuehrt"]
    assert h.zugestellt[0].audience == "dbus"


def test_jede_audience_hat_einen_weg():
    """Die vier Broker-Units existieren; der Hub kennt ihre Sockets."""
    from daimon.common.order import AUDIENCES
    assert set(D.BROKER_SOCKETS) == set(AUDIENCES)
    assert D.BROKER_SOCKETS["fs"] == "fs-broker.sock"
    assert D.BROKER_SOCKETS["exec"] == "exec-broker.sock"
    assert D.BROKER_SOCKETS["input"] == "input-broker.sock"


# --------------------------------------------------------------------------
# Ein Buch: Freigabebuch und Consent zusammengelegt
# --------------------------------------------------------------------------

def test_die_nonce_kommt_aus_dem_freigabebuch(tmp_path):
    """Sonst waere sie dort unbekannt, wenn der Auth-Agent bestaetigt."""
    from daimon.hub.consent import Consent
    from daimon.hub.marks import FreigabeBuch

    buch = FreigabeBuch()
    c = Consent.laden(tmp_path / "consent", buch=buch)
    r = c.stellen(action_id="fs.file.delete", params_hash="sha256:aa",
                  prompt_shown="x", absender="auth", jetzt=0.0)
    # Genau diese Nonce bestaetigt im Buch -- und nur mit dem richtigen Hash.
    buch.bestaetigen(nonce=r.nonce, action_hash=r.action_hash)
    assert c.freigabe_einloesen(action_id="fs.file.delete",
                                params_hash="sha256:aa", jetzt=1.0)
    # Und genau einmal: die zweite Einloesung faellt im Buch.
    assert not c.freigabe_einloesen(action_id="fs.file.delete",
                                    params_hash="sha256:aa", jetzt=1.0)


def test_eine_freigabe_ohne_offene_rueckfrage_laeuft_wie_bisher(tmp_path):
    """T-1.7 schickt Freigaben ohne Rueckfrage -- das muss unveraendert
    durchlaufen."""
    from daimon.hub.consent import Consent
    from daimon.hub.marks import FreigabeBuch

    h = D.Hub.__new__(D.Hub)
    h.log = _Log()
    h.freigaben = FreigabeBuch()
    h.consent = Consent.laden(tmp_path / "consent", buch=h.freigaben)
    nonce = h.freigaben.nonce_ausgeben(action_hash="sha256:zzz")
    h.freigaben.bestaetigen(nonce=nonce, action_hash="sha256:zzz")
    h._rueckfrage_schliessen(nonce)      # kein Eintrag -> darf nicht werfen
    assert h.consent.offen == {}


def test_die_antwort_des_auth_agenten_weckt_den_wartenden(tmp_path):
    import threading

    from daimon.hub.consent import ABBRUCH, ZUSTIMMUNG, Consent
    from daimon.hub.marks import FreigabeBuch

    buch = FreigabeBuch()
    c = Consent.laden(tmp_path / "consent", buch=buch)
    r = c.stellen(action_id="a", params_hash="sha256:aa", prompt_shown="x",
                  absender="auth", jetzt=0.0)

    ergebnis = {}
    warter = threading.Thread(
        target=lambda: ergebnis.setdefault("zustand", c.warten(r, timeout_s=5)))
    warter.start()
    c.antwort(rueckfrage_id=r.id, nonce=r.nonce, absender="auth",
              zustand=ZUSTIMMUNG, jetzt=1.0)
    warter.join(timeout=5)
    assert ergebnis["zustand"] == ZUSTIMMUNG


def test_keine_antwort_ist_ein_abbruch_kein_nein(tmp_path):
    from daimon.hub.consent import ABBRUCH, Consent
    from daimon.hub.marks import FreigabeBuch

    c = Consent.laden(tmp_path / "consent", buch=FreigabeBuch())
    r = c.stellen(action_id="a", params_hash="sha256:aa", prompt_shown="x",
                  absender="auth", jetzt=0.0)
    assert c.warten(r, timeout_s=0.05) == ABBRUCH
    assert c.offen == {}


# --------------------------------------------------------------------------
# Der Weg zum Auth-Agenten: `art: "offene"`
# --------------------------------------------------------------------------

def test_offene_rueckfragen_sind_lesbar_und_tragen_was_der_agent_braucht(tmp_path, monkeypatch):
    """Nonce, action_hash und Vorschautext kommen vom HUB.

    Der Auth-Agent formuliert nichts: haette er den Text, waere die feste
    Vorlage eine Empfehlung.
    """
    import threading

    monkeypatch.setattr(D, "RUECKFRAGE_FRIST_S", 1.0)
    h = hub(tmp_path, monkeypatch)

    gestellt = threading.Event()
    ergebnis = {}

    def stellen_und_warten():
        ergebnis["lauf"] = h.aktion_anfrage(anfrage(quelle="modell"))

    t = threading.Thread(target=stellen_und_warten)
    t.start()
    # Warten, bis die Rueckfrage steht -- ohne feste Schlafzeit.
    import time as _t
    for _ in range(200):
        antwort = h.aktion_anfrage({"v": 1, "art": "offene"})
        if antwort["offen"]:
            gestellt.set()
            break
        _t.sleep(0.005)
    assert gestellt.is_set(), "keine Rueckfrage sichtbar geworden"

    eintrag = antwort["offen"][0]
    assert eintrag["nonce"] and eintrag["action_hash"].startswith("sha256:")
    assert eintrag["action_id"] == "media.playpause"
    assert eintrag["prompt_shown"]
    assert eintrag["destructive"] is False
    t.join(timeout=5)
    assert ergebnis["lauf"]["grund"] == "cancelled"


def test_ohne_offene_rueckfrage_ist_die_liste_leer(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch)
    a = h.aktion_anfrage({"v": 1, "art": "offene"})
    assert a["ok"] and a["offen"] == []
