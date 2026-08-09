"""Der Hub-Anschluss: eine Zeile auf `aktion.sock`, eine Wirkung.

Der Endpunkt ist AUSDRUECKLICH kein Produzent -- wie gpu, tts und ticket.
Ein Test haelt das fest: die Produzententabelle bleibt unveraendert.
"""
from __future__ import annotations

import json

import pytest

from daimon.common import ipc
from daimon.hub import daemon as D


class Marken:
    def __init__(self, gueltig: bool) -> None:
        self.gueltig = gueltig

    def initiator(self, turn_id):
        return "user" if self.gueltig else "background"


def hub(tmp_path, monkeypatch, *, marke_gueltig=True, broker_ok=True):
    h = D.Hub.__new__(D.Hub)          # ohne __init__: kein Socket, kein Thread
    h.log = _Log()
    h.marken = Marken(marke_gueltig)
    h.runtime_dir = tmp_path
    h._aktion = None
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
             "params": {}, "quelle": "parser", "turn_id": "r1",
             "tool_use_id": "t1", "session_id": "s1"}
    grund.update(kw)
    return grund


def test_der_endpunkt_ist_kein_produzent():
    """Kein Eintrag in der Produzententabelle -- wie gpu, tts, ticket."""
    assert "mind" not in ipc.PRODUZENTEN
    for menge in ipc.PRODUZENTEN.values():
        assert "action_request" not in menge
        assert "aktion" not in menge


def test_eine_direkte_aktion_unter_marke_wird_ausgefuehrt(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch)
    a = h.aktion_anfrage(anfrage())
    assert a["ok"] and a["ausgefuehrt"] and a["direkt"]
    assert len(h.zugestellt) == 1
    assert h.zugestellt[0].audience == "dbus"
    assert h.zugestellt[0].ticket


def test_ohne_gueltige_marke_passiert_nichts(tmp_path, monkeypatch):
    h = hub(tmp_path, monkeypatch, marke_gueltig=False)
    a = h.aktion_anfrage(anfrage())
    assert a["ok"] and not a["ausgefuehrt"]
    assert a["verdikt"] == "deny"
    assert h.zugestellt == []


def test_eine_modellausgabe_landet_in_der_unverdrahteten_rueckfrage(tmp_path, monkeypatch):
    """Und die ist `cancelled`, nicht `declined`: abgelehnt hat niemand."""
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
    h = hub(tmp_path, monkeypatch)
    a = h.aktion_anfrage(anfrage())
    assert "policy" in a["dauer_ms"] and "broker" in a["dauer_ms"]


def test_der_broker_pfad_steht_im_hub_nicht_im_auftrag(tmp_path, monkeypatch):
    """Stuende er im Auftrag, koennte ein Absender sich seinen Broker
    aussuchen."""
    from daimon.common.order import FELDER
    assert "socket" not in FELDER and "broker" not in FELDER
    assert set(D.BROKER_SOCKETS) <= {"dbus", "fs", "exec", "input"}
