"""T-1.7 Teil B — der Auth-Weg zum Hub.

Der Positiv-Kanarienvogel steht zuerst: `intent_mark` ueber den auth-Socket
erzeugt eine Rundenmarke. Ohne ihn bewiese eine Umsetzung, die alles
ablehnt, saemtliche Negativtests -- und nichts.
"""

import io
import json
import socket
import time
from pathlib import Path

import pytest

from daimon.common import ipc
from daimon.common.logging import get_logger
from daimon.hub.daemon import DIAG_SOCKET, Hub
from daimon.hub.marks import MarkenFehler


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


@pytest.fixture
def hub(tmp_path):
    h = Hub(runtime_dir=tmp_path / "rt", log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def verbinden(rt: Path, produzent: str) -> socket.socket:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / f"{produzent}.sock"))
    return c


def sende(rt: Path, produzent: str, typ: str, payload: dict) -> None:
    c = verbinden(rt, produzent)
    c.sendall(json.dumps({"v": 1, "type": typ, "payload": payload}).encode() + b"\n")
    time.sleep(0.25)
    c.close()


def diag(rt: Path) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / DIAG_SOCKET))
    try:
        return json.loads(c.makefile("rb").readline())
    finally:
        c.close()


def zaehler(rt: Path, typ: str, feld: str) -> int:
    return diag(rt)["zaehler"][typ][feld]


# ---------------------------------------------------------------------------
# Positiv-Kanarienvogel
# ---------------------------------------------------------------------------

def test_intent_mark_ueber_auth_erzeugt_rundenmarke(hub):
    rt = hub.runtime_dir
    assert zaehler(rt, "rundenmarke", "ausgegeben") == 0
    sende(rt, "auth", "intent_mark", {})
    assert zaehler(rt, "rundenmarke", "ausgegeben") == 1


def test_freigabe_ueber_auth_wird_bestaetigt(hub):
    rt = hub.runtime_dir
    nonce = hub.freigaben.nonce_ausgeben(action_hash="h123")
    sende(rt, "auth", "freigabe", {"nonce": nonce, "action_hash": "h123"})
    assert zaehler(rt, "aktionsfreigabe", "ausgegeben") == 1
    # Die Freigabe liegt im Buch und ist genau einmal einloesbar.
    hub.freigaben.einloesen(action_hash="h123")
    with pytest.raises(MarkenFehler):
        hub.freigaben.einloesen(action_hash="h123")


# ---------------------------------------------------------------------------
# Dieselben Typen auf einem anderen Socket: Abweisung, Verbindung ab
# ---------------------------------------------------------------------------

def test_intent_mark_ueber_hookbridge_wird_abgewiesen(hub):
    rt = hub.runtime_dir
    c = verbinden(rt, "hookbridge")
    c.sendall(json.dumps({"v": 1, "type": "intent_mark", "payload": {}}).encode() + b"\n")
    time.sleep(0.3)
    # Eine Abweisung beendet die Verbindung, nicht den Hub.
    assert c.recv(1) == b""
    c.close()
    assert zaehler(rt, "rundenmarke", "ausgegeben") == 0
    assert zaehler(rt, "rundenmarke", "abgelehnt") == 1
    # Der Hub lebt noch und beantwortet die Diagnose -- oben schon geschehen.
    assert diag(rt)["v"] == 1


def test_freigabe_ueber_hookbridge_wird_abgewiesen(hub):
    rt = hub.runtime_dir
    nonce = hub.freigaben.nonce_ausgeben(action_hash="h9")
    sende(rt, "hookbridge", "freigabe", {"nonce": nonce, "action_hash": "h9"})
    assert zaehler(rt, "aktionsfreigabe", "abgelehnt") == 1
    with pytest.raises(MarkenFehler):
        hub.freigaben.einloesen(action_hash="h9")


def test_freigabe_mit_falschem_hash_wird_abgewiesen(hub):
    rt = hub.runtime_dir
    nonce = hub.freigaben.nonce_ausgeben(action_hash="h1")
    sende(rt, "auth", "freigabe", {"nonce": nonce, "action_hash": "h2"})
    assert zaehler(rt, "aktionsfreigabe", "ausgegeben") == 0
    assert zaehler(rt, "aktionsfreigabe", "abgelehnt") == 1


# ---------------------------------------------------------------------------
# Face hat keinen Produzentensocket mehr
# ---------------------------------------------------------------------------

def test_face_hat_keinen_produzenteneintrag_mehr():
    """T-1.7: PTT und Bestaetigungsdialoge liegen im Auth-Agenten, nie im
    Face. Solange das Face `intent_mark` senden durfte, war diese Grenze nur
    behauptet."""
    assert "face" not in ipc.PRODUZENTEN
    with pytest.raises(ipc.MessageTypeError):
        ipc.pruefe_typ("face", "intent_mark")


def test_face_socket_wird_nicht_geoeffnet(tmp_path):
    with pytest.raises(ValueError):
        ipc.listen(tmp_path, "face")


# ---------------------------------------------------------------------------
# Die turn_id kommt vom Hub, nicht aus der Nachricht
# ---------------------------------------------------------------------------

def test_turn_id_aus_der_nachricht_wird_nicht_uebernommen(hub):
    """Design 2.4: "Die Marke bleibt im Hub." Ein Absender, der die turn_id
    selbst waehlt, koennte eine Runde benennen, die er kennt -- also erzeugt
    der Hub sie selbst und liest das Feld nicht."""
    rt = hub.runtime_dir
    sende(rt, "auth", "intent_mark", {"turn_id": "selbst-gewaehlt"})
    assert zaehler(rt, "rundenmarke", "ausgegeben") == 1
    # Die mitgeschickte turn_id kennt kein Buch: Auskunft sagt "background",
    # und Einloesen schlaegt fehl.
    assert hub.marken.initiator("selbst-gewaehlt") == "background"
    with pytest.raises(MarkenFehler):
        hub.marken.einloesen("selbst-gewaehlt")


# ---------------------------------------------------------------------------
# Diagnose: zaehlt nur, verraet nichts
# ---------------------------------------------------------------------------

def test_diag_zaehlt_ohne_geheimnisse(hub):
    """Zwei neue Zaehler (ausgegeben, abgelehnt) -- aber keine turn_id, keine
    Nonce, kein Hash: die Diagnose ist kein Auskunftskanal ueber Geheimnisse."""
    rt = hub.runtime_dir
    nonce = hub.freigaben.nonce_ausgeben(action_hash="geheim-hash")
    sende(rt, "auth", "intent_mark", {"turn_id": "geheime-turn-id"})
    sende(rt, "auth", "freigabe", {"nonce": nonce, "action_hash": "geheim-hash"})
    roh = json.dumps(diag(rt))
    assert "geheime-turn-id" not in roh
    assert "geheim-hash" not in roh
    assert nonce not in roh
