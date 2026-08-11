"""T-1.6 — der Push-Endpunkt `events.sock` des Hubs.

Der Endpunkt existiert, damit das Face **keinen Timer** braucht. Die
Null-Idle-CPU aus T-1.5 war die Zusage, auf der die Overlay-Architektur steht;
ein Poll-Timer im Face haette sie wieder aufgemacht.

Positivkontrolle ist hier nicht Beiwerk: "es kam keine zweite Zeile" ist nur
dann ein Befund, wenn bewiesen ist, dass ueberhaupt eine erste kam.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

from daimon.hub.daemon import EVENTS_SOCKET, Hub


@pytest.fixture()
def hub(tmp_path: Path):
    h = Hub(runtime_dir=tmp_path / "rt")
    h.start()
    sock = h.runtime_dir / EVENTS_SOCKET
    # Gewartet wird auf eine ANGENOMMENE Verbindung, nicht auf die Datei.
    # `bind()` legt sie an, `listen()` kommt danach -- unter Last (K13 faehrt
    # pytest, waehrend der T-3.14-Pruefstand Dienste treibt) verlor der erste
    # `connect()` dieses Rennen und der Test fiel mit ConnectionRefused aus.
    # Dieselbe Lehre wie in `tools/pet_zeigen.py`: Existenz ist keine
    # Bereitschaft.
    for _ in range(100):
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1.0)
            probe.connect(str(sock))
            probe.close()
            break
        except OSError:
            time.sleep(0.05)
    yield h
    h.stop()


def _verbinde(pfad: Path) -> tuple[socket.socket, "object"]:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5.0)
    c.connect(str(pfad))
    return c, c.makefile("rb")


def _hook(hub: Hub, session: str, ereignis: str) -> None:
    """Ueber den Bus, nicht ueber den Socket: dieser Test prueft den
    Push-Endpunkt, nicht die Hook-Bruecke."""
    from daimon.common.protocol import Event

    hub.bus.publish(Event(type="hook", payload={
        "hook_event_name": ereignis, "session_id": session}))


def test_erste_zeile_kommt_sofort(hub: Hub) -> None:
    c, f = _verbinde(hub.runtime_dir / EVENTS_SOCKET)
    with c:
        zeile = f.readline()
    schnapp = json.loads(zeile)
    assert schnapp["v"] == 2
    assert "rev" in schnapp and "mood" in schnapp


def test_socket_hat_modus_0600(hub: Hub) -> None:
    assert (hub.runtime_dir / EVENTS_SOCKET).stat().st_mode & 0o777 == 0o600


def test_zweite_zeile_erst_bei_rev_aenderung(hub: Hub) -> None:
    c, f = _verbinde(hub.runtime_dir / EVENTS_SOCKET)
    with c:
        erste = json.loads(f.readline())

        # Positivkontrolle zuerst: ein Ereignis MUSS eine Zeile ausloesen.
        # Ohne sie waere das Schweigen unten nicht interpretierbar -- es
        # koennte genauso ein toter Endpunkt sein.
        _hook(hub, "s1", "UserPromptSubmit")
        zweite = json.loads(f.readline())
        assert zweite["rev"] > erste["rev"]

        # Und jetzt das eigentliche Versprechen: ohne Aenderung kommt nichts.
        c.settimeout(0.5)
        with pytest.raises((socket.timeout, TimeoutError)):
            f.readline()


def test_hub_weg_schliesst_die_verbindung(hub: Hub) -> None:
    c, f = _verbinde(hub.runtime_dir / EVENTS_SOCKET)
    with c:
        assert f.readline()  # Positivkontrolle: die Verbindung stand
        hub.stop()
        # Nach dem Stop schliesst die Push-Schleife die Verbindung: der Client
        # sieht EOF, nicht eine Blockade. Genau daraus macht das Face
        # "sleeping". Hier stand zuerst ein "or True" am Ende der Assertion --
        # damit war der Test gruen, egal was passiert. Dieselbe tautologische
        # Assertion, die im Review von T-1.4 angemahnt wurde, drei Tage
        # spaeter selbst geschrieben.
        c.settimeout(5.0)
        assert f.readline() == b""


def test_endpunkt_liest_nichts_vom_client(hub: Hub) -> None:
    """Ein Client, der Muell schickt, darf den Endpunkt nicht stoeren --
    weder fuer sich noch fuer andere."""
    c, f = _verbinde(hub.runtime_dir / EVENTS_SOCKET)
    with c:
        assert f.readline()
        c.sendall(b"was soll das denn\n" * 100)
        _hook(hub, "s2", "UserPromptSubmit")
        zweite = json.loads(f.readline())
        assert zweite["rev"] > 0

        # Und ein zweiter Client bekommt weiterhin bedient.
        c2, f2 = _verbinde(hub.runtime_dir / EVENTS_SOCKET)
        with c2:
            assert json.loads(f2.readline())["v"] == 2
