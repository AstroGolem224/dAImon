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


def folge(n=1):
    return [{"art": "key", "wert": "ctrl+s"} for _ in range(n)]


def test_ohne_portal_und_ohne_erlaubnis_passiert_nichts():
    stub = LaufStub()
    b = ib.InputBroker(lauf=stub)
    e = b.ausfuehren(folge())
    assert not e["ok"] and e["grund"] == "kein_portal"
    assert stub.aufrufe == []


def test_der_broker_ist_nach_einem_lauf_verbraucht():
    b = ib.InputBroker(portal=lambda f: True)
    assert b.ausfuehren(folge())["ok"]
    zweit = b.ausfuehren(folge())
    assert not zweit["ok"] and zweit["grund"] == "verbraucht"


def test_auch_eine_abgewiesene_folge_verbraucht_den_broker():
    """Sonst waere ein Fehlversuch ein freier Versuch."""
    b = ib.InputBroker(portal=lambda f: True)
    assert not b.ausfuehren([])["ok"]
    assert not b.ausfuehren(folge())["ok"]


def test_zu_lange_folgen_sind_keine_einzelhandlung():
    b = ib.InputBroker(portal=lambda f: True)
    e = b.ausfuehren(folge(ib.MAX_EREIGNISSE + 1))
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


def test_ydotool_bleibt_ohne_erlaubnis_aus():
    stub = LaufStub()
    b = ib.InputBroker(lauf=stub, ydotool_erlaubt=False)
    b.ausfuehren(folge())
    assert not any("ydotool" in " ".join(a) for a in stub.aufrufe)


def test_mit_erlaubnis_wird_ydotoold_gestartet_und_wieder_gestoppt():
    stub = LaufStub()
    b = ib.InputBroker(lauf=stub, ydotool_erlaubt=True)
    e = b.ausfuehren(folge())
    assert e["ok"] and e["weg"] == "ydotool"
    zeilen = [" ".join(a) for a in stub.aufrufe]
    assert any("systemd-run" in z and "ydotoold" in z for z in zeilen)
    assert zeilen[-1].startswith("systemctl --user stop daimon-ydotoold")


def test_es_gibt_keine_absolute_positionierung():
    """Spike T-1.3: `mousemove -a` landet bei (0,0), Exit 0, keine Meldung."""
    from pathlib import Path
    quelle = Path("daimon/brokers/input/broker.py").read_text(encoding="utf-8")
    assert "mousemove" in quelle
    assert "-a" not in quelle.split("def _ueber_ydotool")[1].split("mousemove")[1][:200]
    assert "move_abs" not in ib.ARTEN


def test_das_ergebnis_verspricht_keine_genauigkeit():
    b = ib.InputBroker(lauf=LaufStub(), ydotool_erlaubt=True)
    e = b.ausfuehren([{"art": "move_rel", "wert": [10, 10]}])
    assert "ungenau" in e["hinweis"]
