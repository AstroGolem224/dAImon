"""Der Dienstmantel: eine Zeile, eine Antwort, und Grenzen, die halten.

Seit T-4.5 K6 (19.08.) nimmt der Mantel nur noch vom Hub an. Diese Tests
laufen nicht als `daimon-hub.service` -- dass sie vorher alle gruen waren,
zeigt genau den Befund: die Peer-Pruefung lag nicht auf ihrem Weg. Die
Fixture unten setzt die erlaubte Unit auf die des Testprozesses; geprueft
wird die Pruefung selbst in `test_broker_herkunft.py`.
"""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from conftest import eigene_unit
from daimon.brokers import dienst
from daimon.common import ipc


@pytest.fixture(autouse=True)
def _hub_ist_dieser_test(monkeypatch, tmp_path):
    """Die eigene Unit gilt als Hub. Geprueft wird die Sperre selbst in
    `test_broker_herkunft.py`."""
    monkeypatch.setattr(dienst, "HUB_UNIT", eigene_unit(tmp_path))


def frage(pfad: Path, roh: bytes) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(5)
        c.connect(str(pfad))
        c.sendall(roh)
        return json.loads(c.recv(65536).decode())


def starte(pfad, verarbeite, **kw):
    t = threading.Thread(target=dienst.lauf, args=(pfad, verarbeite),
                         kwargs=kw, daemon=True)
    t.start()
    for _ in range(200):
        if pfad.exists():
            break
        threading.Event().wait(0.005)
    return t


def test_eine_zeile_rein_eine_raus(tmp_path):
    pfad = tmp_path / "b.sock"
    starte(pfad, lambda roh: {"ok": True, "echo": roh.decode()}, einmal=True)
    assert frage(pfad, b'{"x":1}\n')["echo"] == '{"x":1}'


def test_der_socket_ist_0600_schon_bei_der_erzeugung(tmp_path):
    import os
    import stat
    pfad = tmp_path / "b.sock"
    starte(pfad, lambda roh: {"ok": True}, einmal=True)
    assert stat.S_IMODE(os.stat(pfad).st_mode) == 0o600


def test_einmal_beendet_nach_dem_ersten_auftrag(tmp_path):
    """Die One-shot-Zusage des Input-Brokers, hier gemessen."""
    pfad = tmp_path / "b.sock"
    t = starte(pfad, lambda roh: {"ok": True}, einmal=True)
    frage(pfad, b'{}\n')
    t.join(timeout=5)
    assert not t.is_alive()
    assert not pfad.exists(), "der Socket bleibt nicht liegen"


def test_eine_zu_grosse_zeile_wird_abgewiesen_statt_geschluckt(tmp_path):
    pfad = tmp_path / "b.sock"
    gesehen = []
    starte(pfad, lambda roh: gesehen.append(roh) or {"ok": True}, einmal=True)
    antwort = frage(pfad, b"x" * (dienst.MAX_BYTES + 10) + b"\n")
    assert antwort == {"ok": False, "grund": "zu_gross"}
    assert gesehen == [], "der Broker hat sie trotzdem gesehen"


def test_ein_fehler_im_broker_beendet_den_dienst_nicht(tmp_path):
    """Ein Broker, der an einer Zeile stirbt, stirbt an der naechsten wieder."""
    pfad = tmp_path / "b.sock"

    def verarbeite(roh):
        raise RuntimeError("kaputt")

    starte(pfad, verarbeite, einmal=True)
    antwort = frage(pfad, b'{}\n')
    assert antwort["ok"] is False and antwort["grund"] == "fehler"
