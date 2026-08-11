"""T-4.16 — der ganze Weg, an echten Bausteinen.

Keine Attrappen fuer Policy, Consent, Auftragsbuch, Schlange und Audit: das
sind die Module aus T-4.4 bis T-4.15. Attrappe ist nur der Broker (er wuerde
sonst wirklich etwas tun) und die Antwort des Menschen.
"""
from __future__ import annotations

import json

import pytest

from daimon.hub.action_queue import Aktionsschlange
from daimon.hub.audit import Audit
from daimon.hub.consent import Consent
from daimon.hub.coordinator import Koordinator, Lauf
from daimon.hub.order import Auftragsbuch
from daimon.hub.policy import Policy

MARKE = {"id": "m-1", "gueltig_bis": 1e9}


def bauen(tmp_path, *, antwort="granted", broker_ok=True, undo=None):
    gesprochen = []
    aufgerufen = []

    class K(Koordinator):
        def consent_abwarten(self, rueckfrage):
            return antwort

    k = K(policy=Policy.laden(),
          consent=Consent.laden(tmp_path / "consent"),
          auftragsbuch=Auftragsbuch(),
          schlange=Aktionsschlange(),
          audit=Audit.oeffnen(tmp_path / "audit"),
          broker=lambda a: (aufgerufen.append(a) or
                            {"ok": broker_ok, "grund": "" if broker_ok else "dbus"}),
          vorschau=lambda action_id, params: f"{action_id} ausfuehren?",
          sprechen=gesprochen.append,
          undo=undo,
          uhr=lambda: 100.0)
    return k, gesprochen, aufgerufen


def lauf(k, **kw) -> Lauf:
    grund = dict(action_id="media.playpause", params={}, quelle="parser",
                 marke=MARKE, session_id="s1", turn_id="r1",
                 tool_use_id="t1")
    grund.update(kw)
    return k.ausfuehren(**grund)


def saetze(tmp_path):
    datei = tmp_path / "audit" / "audit.jsonl"
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------
# Der Direktpfad
# --------------------------------------------------------------------------

def test_der_direktpfad_umgeht_die_vorschau_und_sonst_nichts(tmp_path):
    k, gesprochen, aufgerufen = bauen(tmp_path)
    e = lauf(k)
    assert e.ausgefuehrt and e.direkt
    assert "vorschau" not in e.dauer_ms       # kein Dialog
    assert len(aufgerufen) == 1               # aber ein Auftrag
    assert aufgerufen[0].ticket               # mit Ticket
    assert saetze(tmp_path)[0]["outcome"] == "ok"   # und eine Spur


def test_dieselbe_aktion_aus_einer_modellausgabe_geht_durch_die_vorschau(tmp_path):
    k, _, aufgerufen = bauen(tmp_path)
    e = lauf(k, quelle="modell")
    assert e.ausgefuehrt
    assert "vorschau" in e.dauer_ms and "freigabe" in e.dauer_ms
    assert len(aufgerufen) == 1


# --------------------------------------------------------------------------
# Jeder Ausgang landet im Audit
# --------------------------------------------------------------------------

def test_ein_deny_erzeugt_keine_rueckfrage_aber_eine_auditzeile(tmp_path):
    k, gesprochen, aufgerufen = bauen(tmp_path)
    e = lauf(k, action_id="kwin:Kill Window")
    assert e.verdikt == "deny" and e.grund == "unknown_action"
    assert aufgerufen == []
    assert k.consent.offen == {}, "ein deny darf keinen Dialog erzeugen"
    assert saetze(tmp_path)[0]["outcome"] == "denied"
    assert gesprochen == ["Diese Aktion kenne ich nicht."]


def test_eine_ablehnung_fuehrt_nichts_aus_und_wird_protokolliert(tmp_path):
    k, gesprochen, aufgerufen = bauen(tmp_path, antwort="declined")
    e = lauf(k, quelle="modell")
    assert not e.ausgefuehrt and e.grund == "declined"
    assert aufgerufen == []
    assert saetze(tmp_path)[0]["outcome"] == "declined"
    assert gesprochen == ["Gut, ich lasse es."]


def test_ein_abbruch_ist_kein_nein_und_steht_als_solcher_im_audit(tmp_path):
    k, gesprochen, _ = bauen(tmp_path, antwort="cancelled")
    e = lauf(k, quelle="modell")
    assert e.grund == "cancelled"
    assert saetze(tmp_path)[0]["outcome"] == "cancelled"
    assert gesprochen == ["Ich habe keine Antwort bekommen und lasse es."]


def test_ein_fehlschlag_im_broker_wird_gemeldet_und_protokolliert(tmp_path):
    k, gesprochen, _ = bauen(tmp_path, broker_ok=False)
    e = lauf(k)
    assert not e.ausgefuehrt
    assert saetze(tmp_path)[0]["outcome"] == "failed"
    assert gesprochen == ["Das hat nicht geklappt."]


# --------------------------------------------------------------------------
# Reihenfolge: Undo vor Mutation, Ticket erst nach der Freigabe
# --------------------------------------------------------------------------

def test_ein_fehlgeschlagenes_undo_verhindert_die_mutation(tmp_path):
    def undo(**kw):
        raise RuntimeError("Dateisystem voll")

    k, gesprochen, aufgerufen = bauen(tmp_path, undo=undo)
    e = lauf(k)
    assert not e.ausgefuehrt and "undo" in e.grund
    assert aufgerufen == [], "der Broker darf ohne Artefakt nicht gerufen werden"
    assert saetze(tmp_path)[0]["outcome"] == "failed"
    assert gesprochen == ["Ich konnte kein Undo anlegen und habe es deshalb gelassen."]


def test_waehrend_der_vorschau_existiert_noch_kein_ticket(tmp_path):
    gesehen = {}

    class K(Koordinator):
        def consent_abwarten(self, rueckfrage):
            gesehen["offene_auftraege"] = self.auftragsbuch.offen
            return "granted"

    k, _, _ = bauen(tmp_path)
    k.__class__ = K
    lauf(k, quelle="modell")
    assert gesehen["offene_auftraege"] == 0


# --------------------------------------------------------------------------
# Spur und Messpunkte
# --------------------------------------------------------------------------

def test_jeder_hop_traegt_turn_id_und_tool_use_id(tmp_path):
    k, _, _ = bauen(tmp_path)
    lauf(k, turn_id="runde-7", tool_use_id="werkzeug-3")
    satz = saetze(tmp_path)[0]
    assert satz["turn_id"] == "runde-7"
    assert satz["tool_use_id"] == "werkzeug-3"
    assert satz["mark_id"] == "m-1"
    assert satz["initiator"] == "foreground"


def test_die_dauer_je_hop_wird_gemessen(tmp_path):
    k, _, _ = bauen(tmp_path)
    e = lauf(k, quelle="modell")
    for stufe in ("policy", "vorschau", "freigabe", "broker", "audit"):
        assert stufe in e.dauer_ms, stufe


def test_zu_viele_aktionen_je_runde_werden_abgelehnt(tmp_path):
    k, gesprochen, aufgerufen = bauen(tmp_path)
    for i in range(5):
        lauf(k, tool_use_id=f"t{i}")
    e = lauf(k, tool_use_id="t-zuviel")
    assert e.grund == "rate_limit"
    assert len(aufgerufen) == 5
    assert gesprochen[-1] == "Das waren mir zu viele Aktionen auf einmal."


def test_ohne_marke_gibt_es_keine_ausfuehrung(tmp_path):
    k, gesprochen, aufgerufen = bauen(tmp_path)
    e = lauf(k, marke=None)
    assert e.verdikt == "deny"
    assert aufgerufen == []
