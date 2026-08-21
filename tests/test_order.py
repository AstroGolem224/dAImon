"""T-4.5 — der Auftrag traegt Ziel, Frist und Einmaligkeit. Und keine Signatur.

Jede Abweisung einzeln, keine Sammelpruefung: ein Auftrag, der aus fuenf
Gruenden gleichzeitig faellt, sagt nichts darueber, ob jeder einzelne Grund
greift.
"""
from __future__ import annotations

import json

import pytest

from daimon.common import order as fmt
from daimon.hub.order import Auftragsbuch


def auftrag(**felder) -> fmt.Auftrag:
    grund = {"audience": "dbus", "action_id": "media.playpause",
             "params": {}, "ticket": "t1",
             "deadline_monotonic": 1000.0, "turn_id": "runde-1",
             "tool_use_id": "werkzeug-1"}
    grund.update(felder)
    grund.setdefault("params_hash", fmt.params_hash(grund["params"]))
    return fmt.Auftrag(**grund)


# --------------------------------------------------------------------------
# Das Format
# --------------------------------------------------------------------------

def test_der_auftrag_traegt_genau_die_neun_felder():
    # T-4.16.v K4: tool_use_id kam dazu -- der Auftrag ist der Hop zum
    # Broker, dort endete sonst die Spur einer Aeusserung.
    assert set(auftrag().als_dict()) == set(fmt.FELDER)
    assert len(fmt.FELDER) == 9


def test_es_gibt_kein_signaturfeld():
    """Design 6.2: der HMAC ist gestrichen.

    Nicht "noch nicht gebaut" -- gestrichen. Ein Schluessel, den Hub und
    Broker teilen, liegt unter derselben uid und ist fuer den ausgeschlossenen
    Angreifer per ptrace lesbar; er haette nur die Angreifer abgewehrt, die es
    hier ohnehin nicht gibt.
    """
    verdaechtig = {"hmac", "signature", "sig", "mac", "signatur"}
    assert not (set(fmt.FELDER) & verdaechtig)
    text = fmt.kanonisch(auftrag()).decode()
    assert not any(w in text for w in verdaechtig)


def test_gleiche_werte_ergeben_gleiche_bytes():
    a = auftrag(params={"value": 0.5, "a": 1})
    b = auftrag(params={"a": 1, "value": 0.50})
    assert fmt.kanonisch(a) == fmt.kanonisch(b)


def test_ein_fremdes_feld_ist_ein_fehler():
    daten = auftrag().als_dict()
    daten["extra"] = "x"
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.kanonisch(daten)
    assert "extra" in str(f.value)


# --------------------------------------------------------------------------
# Die Pruefung -- jede Abweisung fuer sich
# --------------------------------------------------------------------------

def test_positivkontrolle_ein_gueltiger_auftrag_wird_angenommen():
    roh = fmt.kanonisch(auftrag())
    a = fmt.pruefe(roh, audience="dbus", jetzt=0.0)
    assert a.action_id == "media.playpause"


def test_falsche_audience_wird_abgewiesen():
    roh = fmt.kanonisch(auftrag(audience="dbus"))
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.pruefe(roh, audience="fs", jetzt=0.0)
    assert "fs" in str(f.value)


def test_manipulierte_parameter_werden_abgewiesen():
    """Der Hash bleibt stehen, die Parameter aendern sich."""
    daten = auftrag(params={"value": 0.1}).als_dict()
    daten["params"] = {"value": 0.9}
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.pruefe(fmt.kanonisch(daten), audience="dbus", jetzt=0.0)
    assert "params_hash" in str(f.value)


def test_abgelaufene_frist_wird_abgewiesen():
    roh = fmt.kanonisch(auftrag(deadline_monotonic=100.0))
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.pruefe(roh, audience="dbus", jetzt=100.5)
    assert "Frist" in str(f.value)


def test_unbekanntes_schema_wird_abgewiesen():
    daten = auftrag().als_dict()
    daten["schema"] = "daimon.order.v2"
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.pruefe(fmt.kanonisch(daten), audience="dbus", jetzt=0.0)
    assert "Schema" in str(f.value)


def test_abweichende_serialisierung_wird_abgewiesen():
    """Dieselben Werte, mit Leerzeichen geschrieben.

    Ohne diese Pruefung waere `params_hash` an eine von mehreren
    Schreibweisen gebunden -- also an keine.
    """
    daten = auftrag().als_dict()
    breit = json.dumps(daten, indent=2, ensure_ascii=False).encode("utf-8")
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.pruefe(breit, audience="dbus", jetzt=0.0)
    assert "kanonisch" in str(f.value)


def test_ein_verbrauchtes_ticket_wird_abgewiesen():
    roh = fmt.kanonisch(auftrag())
    with pytest.raises(fmt.AuftragsFehler) as f:
        fmt.pruefe(roh, audience="dbus", jetzt=0.0,
                   ticket_gueltig=lambda t: False)
    assert "Ticket" in str(f.value)
    # Positivkontrolle im selben Test: derselbe Auftrag mit gueltigem Ticket.
    assert fmt.pruefe(roh, audience="dbus", jetzt=0.0,
                      ticket_gueltig=lambda t: True).ticket == "t1"


# --------------------------------------------------------------------------
# Das Auftragsbuch: hoechstens einmal
# --------------------------------------------------------------------------

def test_ein_ticket_laesst_sich_genau_einmal_einloesen():
    buch = Auftragsbuch()
    a = buch.ausstellen(audience="dbus", action_id="media.playpause",
                        params={}, turn_id="r1", jetzt=0.0)
    assert buch.einloesen(a.ticket, jetzt=1.0).action_id == "media.playpause"
    with pytest.raises(fmt.AuftragsFehler) as f:
        buch.einloesen(a.ticket, jetzt=2.0)
    assert "eingeloest" in str(f.value)


def test_ein_abgelaufenes_ticket_meldet_die_frist_und_nicht_die_einloesung():
    buch = Auftragsbuch()
    a = buch.ausstellen(audience="dbus", action_id="media.playpause",
                        params={}, turn_id="r1", frist_s=10.0, jetzt=0.0)
    with pytest.raises(fmt.AuftragsFehler) as f:
        buch.einloesen(a.ticket, jetzt=11.0)
    assert "abgelaufen" in str(f.value)


def test_tickets_sind_nicht_ratbar_und_verschieden():
    buch = Auftragsbuch()
    tickets = {buch.ausstellen(audience="dbus", action_id="a", params={},
                               turn_id="r", jetzt=0.0).ticket
               for _ in range(50)}
    assert len(tickets) == 50
    assert all(len(t) >= 32 for t in tickets)


def test_eine_ablehnung_laesst_das_ticket_verfallen():
    buch = Auftragsbuch()
    a = buch.ausstellen(audience="dbus", action_id="a", params={},
                        turn_id="r", jetzt=0.0)
    buch.verfallen_lassen(a.ticket)
    assert not buch.gueltig(a.ticket, jetzt=1.0)
    with pytest.raises(fmt.AuftragsFehler):
        buch.einloesen(a.ticket, jetzt=1.0)


def test_der_hash_im_auftrag_ist_der_der_policy():
    """Zwei Kanonisierungen waeren zwei Wahrheiten."""
    from daimon.hub.policy import params_hash as policy_hash
    buch = Auftragsbuch()
    a = buch.ausstellen(audience="dbus", action_id="audio.volume.set",
                        params={"value": 0.5}, turn_id="r", jetzt=0.0)
    assert a.params_hash == policy_hash({"value": 0.5})


def test_eine_unbekannte_audience_wird_gar_nicht_erst_ausgestellt():
    buch = Auftragsbuch()
    with pytest.raises(fmt.AuftragsFehler):
        buch.ausstellen(audience="alles", action_id="a", params={},
                        turn_id="r", jetzt=0.0)
