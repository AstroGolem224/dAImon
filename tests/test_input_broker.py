"""T-4.13 — eine Folge, ein Lauf, dann Schluss."""
from __future__ import annotations

import pytest

from daimon.brokers.input import broker as ib


class LaufStub:
    def __init__(self, rc=0):
        self.aufrufe = []
        self.rc = rc

    def __call__(self, argv, **_kw):
        self.aufrufe.append(list(argv))

        class E:
            returncode = self.rc
            stdout = stderr = ""
        return E()


class KindStub:
    """Ersatz fuer `subprocess.Popen` -- ein `ydotoold`, der nichts tut."""

    def __init__(self, argv, **_kw):
        self.argv = argv
        self.beendet = False

    def terminate(self):
        self.beendet = True

    def wait(self, timeout=None):
        return 0


class SitzungStub:
    def __init__(self, ok=True, wirft=False):
        self.ok = ok
        self.wirft = wirft
        self.geschlossen = False

    def senden(self, folge):
        if self.wirft:
            raise RuntimeError("Sitzung kaputt")
        return self.ok

    def schliessen(self):
        self.geschlossen = True


def folge(n=1):
    return [{"art": "key", "wert": "ctrl+s"} for _ in range(n)]


def broker(**kw):
    """Ein Broker mit offenem Bildschirm und `app` auf der Allowlist,
    solange nichts anderes uebergeben wird."""
    vorgabe = dict(screensaver_aktiv=lambda: False,
                   allowlist=frozenset({"app"}))
    vorgabe.update(kw)
    return ib.InputBroker(**vorgabe)


def test_ohne_screensaver_leser_passiert_nichts():
    b = ib.InputBroker(allowlist=frozenset({"app"}))
    e = b.ausfuehren(folge(), app="app")
    assert not e["ok"] and e["grund"] == "kein_breaker"


def test_bei_aktivem_screensaver_wird_abgelehnt_vor_allem_anderen():
    b = broker(screensaver_aktiv=lambda: True, portal_sitzung=SitzungStub)
    e = b.ausfuehren(folge(), app="app")
    assert not e["ok"] and e["grund"] == "sperrbildschirm"


def test_screensaver_fehler_ist_zu_nicht_offen():
    def wirft():
        raise RuntimeError("kein Bus")
    b = broker(screensaver_aktiv=wirft)
    e = b.ausfuehren(folge(), app="app")
    assert not e["ok"] and e["grund"] == "breaker_unlesbar"


def test_eine_nicht_gelistete_app_wird_abgewiesen():
    b = broker(portal_sitzung=lambda: SitzungStub())
    e = b.ausfuehren(folge(), app="fremd")
    assert not e["ok"] and e["grund"] == "app_nicht_gelistet"


def test_ohne_portal_und_ohne_erlaubnis_passiert_nichts():
    stub = LaufStub()
    b = broker(lauf=stub)
    e = b.ausfuehren(folge(), app="app")
    assert not e["ok"] and e["grund"] == "kein_portal"
    assert stub.aufrufe == []


def test_der_broker_ist_nach_einem_lauf_verbraucht():
    b = broker(portal_sitzung=lambda: SitzungStub())
    assert b.ausfuehren(folge(), app="app")["ok"]
    zweit = b.ausfuehren(folge(), app="app")
    assert not zweit["ok"] and zweit["grund"] == "verbraucht"


def test_auch_eine_abgewiesene_folge_verbraucht_den_broker():
    """Sonst waere ein Fehlversuch ein freier Versuch."""
    b = broker(portal_sitzung=lambda: SitzungStub())
    assert not b.ausfuehren([], app="app")["ok"]
    assert not b.ausfuehren(folge(), app="app")["ok"]


def test_zu_lange_folgen_sind_keine_einzelhandlung():
    b = broker(portal_sitzung=lambda: SitzungStub())
    e = b.ausfuehren(folge(ib.MAX_EREIGNISSE + 1), app="app")
    assert not e["ok"] and "hoechstens" in e["meldung"]


def test_die_schranken_je_ereignisart():
    for schlecht in ([{"art": "tippen", "wert": "x"}],
                     [{"art": "type", "wert": ""}],
                     [{"art": "type", "wert": "x" * (ib.MAX_TEXT_ZEICHEN + 1)}],
                     [{"art": "move_rel", "wert": [ib.MAX_BEWEGUNG_PX + 1, 0]}],
                     [{"art": "move_rel", "wert": ["links", 0]}],
                     [{"art": "key", "wert": "  "}]):
        with pytest.raises(ib.InputFehler):
            ib.folge_pruefen(schlecht)
    # Positivkontrolle: die zulaessige Folge kommt durch.
    assert len(ib.folge_pruefen([{"art": "type", "wert": "hallo"},
                                 {"art": "move_rel", "wert": [10, -5]}])) == 2


def test_die_sitzung_wird_immer_geschlossen_auch_bei_fehler():
    sitzung = SitzungStub(wirft=True)
    b = broker(portal_sitzung=lambda: sitzung)
    with pytest.raises(RuntimeError):
        b.ausfuehren(folge(), app="app")
    assert sitzung.geschlossen


def test_ydotool_bleibt_ohne_erlaubnis_aus():
    stub = LaufStub()
    b = broker(lauf=stub, ydotool_erlaubt=False)
    b.ausfuehren(folge(), app="app")
    assert not any("ydotool" in " ".join(a) for a in stub.aufrufe)


def test_ein_fremder_ydotoold_wird_erkannt_und_abgelehnt():
    b = broker(lauf=LaufStub(), starten=KindStub, ydotool_erlaubt=True,
               fremder_dienst=lambda: 12345)
    e = b.ausfuehren(folge(), app="app")
    assert not e["ok"] and e["grund"] == "fremder_ydotoold"


def test_mit_erlaubnis_wird_ydotoold_als_kind_gestartet_und_gestoppt():
    stub = LaufStub()
    kinder = []

    def starten(argv, **kw):
        k = KindStub(argv, **kw)
        kinder.append(k)
        return k

    b = broker(lauf=stub, starten=starten, ydotool_erlaubt=True,
               fremder_dienst=lambda: None)
    e = b.ausfuehren(folge(), app="app")
    assert e["ok"] and e["weg"] == "ydotool"
    assert len(kinder) == 1 and kinder[0].argv == ["ydotoold"]
    assert kinder[0].beendet


def test_es_gibt_keine_absolute_positionierung():
    """Spike T-1.3: `mousemove -a` landet bei (0,0), Exit 0, keine Meldung."""
    from pathlib import Path
    quelle = Path("daimon/brokers/input/broker.py").read_text(encoding="utf-8")
    assert "mousemove" in quelle
    assert "-a" not in quelle.split("def _ueber_ydotool")[1].split("mousemove")[1][:200]
    assert "move_abs" not in ib.ARTEN


def test_das_ergebnis_verspricht_keine_genauigkeit():
    b = broker(lauf=LaufStub(), starten=KindStub, ydotool_erlaubt=True,
               fremder_dienst=lambda: None)
    e = b.ausfuehren([{"art": "move_rel", "wert": [10, 10]}], app="app")
    assert "ungenau" in e["hinweis"]


def test_audit_bekommt_nur_laenge_und_klasse_nie_den_wert():
    gesehen = []
    b = broker(portal_sitzung=lambda: SitzungStub(),
               audit=lambda **felder: gesehen.append(felder))
    b.ausfuehren([{"art": "type", "wert": "geheimes-passwort"}], app="app")
    assert gesehen, "kein Audit-Eintrag entstanden"
    eintrag = gesehen[0]
    assert eintrag["laenge"] == 1
    assert eintrag["klasse"] == "type"
    roh = repr(eintrag)
    assert "geheimes-passwort" not in roh
