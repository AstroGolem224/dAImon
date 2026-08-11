"""T-0.4 — Tests fuer die Protokoll-Vertraege aus Design 9.

Die interessanten Tests sind die ueber das, was NICHT im Vertrag steht. Ein
Feld, das ein Absender setzen darf, ist eine Behauptung; ein Feld, das der Hub
setzt, ist eine Tatsache. Die Tests unten belegen, dass die Behauptungen beim
Deserialisieren verschwinden -- still, ohne Fehlermeldung, damit ein Angreifer
aus der Ablehnung nicht lernt, dass das Feld existiert.
"""

import json

import pytest

from daimon.common.protocol import (
    ActionApproval, ActionRequest, ApiQuota, AuditRecord, Event, ExecutionOrder,
    Mark, Marked, ProtocolError, RoundMark, State, Ticket, UnsupportedVersion,
    tainted, trusted,
)

ALLE = [Event, State, ActionRequest, ExecutionOrder, RoundMark,
        ActionApproval, ApiQuota, Ticket, AuditRecord]

BEISPIELE = {
    Event: {"type": "hook", "payload": {"hook_event_name": "Stop"}},
    State: {"rev": 41, "mood": "needs_input", "sessions": 2},
    ActionRequest: {"action_id": "kde.shortcut.invoke",
                    "params": {"component": "kwin"}, "request_id": "r_12"},
    ExecutionOrder: {"order_id": "o_1", "action_id": "kde.shortcut.invoke"},
    RoundMark: {"mark_id": "m_1", "action_id": "a", "params_hash": "abc"},
    ActionApproval: {"request_id": "r_12", "approved": True},
    ApiQuota: {"limit": 100, "spent": 3},
    Ticket: {"ticket_id": "t_1", "kind": "ocr"},
    AuditRecord: {"at": 1.0, "what": "action.exec", "outcome": "denied"},
}


# --------------------------------------------------------------------------
# Vollstaendigkeit und Roundtrip
# --------------------------------------------------------------------------

def test_alle_neun_typen_existieren():
    assert len(ALLE) == 9


@pytest.mark.parametrize("cls", ALLE, ids=[c.__name__ for c in ALLE])
def test_roundtrip(cls):
    original = cls.from_dict(BEISPIELE[cls])
    wieder = cls.from_dict(original.to_dict())
    assert wieder == original


@pytest.mark.parametrize("cls", ALLE, ids=[c.__name__ for c in ALLE])
def test_roundtrip_ueberlebt_echtes_json(cls):
    """to_dict allein beweist nichts -- eine Marke koennte als Python-Objekt
    ueberleben und beim echten JSON-Weg zerfallen."""
    original = cls.from_dict(BEISPIELE[cls])
    durch_json = json.loads(json.dumps(original.to_dict()))
    assert cls.from_dict(durch_json) == original


@pytest.mark.parametrize("cls", ALLE, ids=[c.__name__ for c in ALLE])
def test_v_wird_immer_geschrieben(cls):
    assert "v" in cls.from_dict(BEISPIELE[cls]).to_dict()


# --------------------------------------------------------------------------
# Die Weglassungen -- der eigentliche Punkt
# --------------------------------------------------------------------------

def test_action_request_verwirft_geschmuggeltes_initiator():
    """Ein Feld, das der Absender setzt, sagt nichts darueber, wer etwas will."""
    r = ActionRequest.from_dict({
        "action_id": "kde.shortcut.invoke", "request_id": "r_1",
        "initiator": "user",
    })
    assert not hasattr(r, "initiator")
    assert "initiator" not in r.to_dict()


def test_action_request_verwirft_geschmuggeltes_params_hash():
    """Ein vom Absender mitgelieferter Hash bestaetigt nur sich selbst."""
    r = ActionRequest.from_dict({
        "action_id": "a", "request_id": "r_1", "params_hash": "deadbeef",
    })
    assert not hasattr(r, "params_hash")
    assert "params_hash" not in r.to_dict()


def test_schmuggel_wird_still_verworfen_nicht_abgelehnt():
    """Keine Fehlermeldung: sie waere ein Hinweis darauf, dass es das Feld gibt."""
    ActionRequest.from_dict({"action_id": "a", "initiator": "user"})  # wirft nicht


def test_event_verwirft_geschmuggeltes_source():
    """Die Quelle ergibt sich aus dem Socket, nicht aus der Nutzlast."""
    e = Event.from_dict({"type": "hook", "source": "vertrauenswuerdig"})
    assert not hasattr(e, "source")
    assert "source" not in e.to_dict()


def test_execution_order_darf_die_felder_haben():
    """Beim Hub -> Broker duerfen sie stehen -- dort entstehen sie."""
    o = ExecutionOrder.from_dict({
        "order_id": "o", "action_id": "a", "params_hash": "abc", "initiator": "hub",
    })
    assert o.params_hash == "abc"
    assert o.initiator == "hub"


# --------------------------------------------------------------------------
# Versionierung
# --------------------------------------------------------------------------

def test_unbekanntes_v_wirft_unsupported_version():
    with pytest.raises(UnsupportedVersion) as ex:
        State.from_dict({"v": 99, "mood": "idle"})
    assert ex.value.got == 99


def test_unbekannte_optionale_felder_werden_ignoriert():
    """Sonst bricht jeder aeltere Leser an einem neuen Feld."""
    s = State.from_dict({"v": 2, "mood": "idle", "ein_feld_aus_der_zukunft": 42})
    assert s.mood == "idle"


def test_fehlendes_pflichtfeld_wirft():
    with pytest.raises(ProtocolError):
        Event.from_dict({"payload": {}})


def test_keine_liste_statt_objekt():
    with pytest.raises(ProtocolError):
        Event.from_dict([1, 2, 3])


# --------------------------------------------------------------------------
# Markierung (Design 5.2)
# --------------------------------------------------------------------------

def test_vorgabe_ist_tainted_nicht_trusted():
    """Ein neu hinzugefuegtes Textfeld ist automatisch markiert."""
    assert Marked("irgendwas").mark is Mark.TAINTED


def test_nackter_wert_wird_beim_lesen_tainted():
    a = ActionApproval.from_dict({"request_id": "r", "approved": True, "reason": "weil"})
    assert isinstance(a.reason, Marked)
    assert a.reason.mark is Mark.TAINTED


def test_marke_ueberlebt_echtes_json():
    """Der Kern von 5.2: die Markierung ist ein Typ, keine Zeichenkette."""
    a = ActionApproval("r", True, reason=trusted("geprueft"))
    wieder = ActionApproval.from_dict(json.loads(json.dumps(a.to_dict())))
    assert wieder.reason.is_trusted()
    assert wieder.reason.value == "geprueft"


def test_trusted_muss_behauptet_werden():
    assert not tainted("x").is_trusted()
    assert trusted("x").is_trusted()


def test_marke_ist_nicht_aus_dem_text_faelschbar():
    """Ein Angreifer koennte 'trusted:' in einen Fenstertitel tippen. Als
    Praefix waere das eine Rechteausweitung -- als Objekt nicht."""
    a = ActionApproval.from_dict({
        "request_id": "r", "approved": True, "reason": "trusted:harmlos",
    })
    assert a.reason.mark is Mark.TAINTED
    assert a.reason.value == "trusted:harmlos"


def test_marked_ist_falsy_wenn_leer():
    """Damit sich markierte Texte in Bedingungen wie nackte verhalten."""
    assert not Marked("")
    assert Marked("x")


def test_marked_ist_unveraenderlich():
    with pytest.raises(Exception):
        Marked("x").value = "y"  # type: ignore[misc]
