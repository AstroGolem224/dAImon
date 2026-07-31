"""T-0.9 — Tests fuer Hub, Bus und State.

Die Mapping-Tests stehen in test_mood_mapping.py und gelten gegen den
Bestandsdaemon. Hier geht es um das, was der Hub NEU macht: Leases, rev, focus
und die Zusage, dass kein TCP-Socket entsteht.
"""

import io
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from daimon.common.logging import get_logger
from daimon.hub import bus as B
from daimon.hub.daemon import STATE_SOCKET, Hub
from daimon.hub.state import LEASE_GRACE_S, HubState, proc_starttime


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


@pytest.fixture
def hub(tmp_path):
    h = Hub(runtime_dir=tmp_path / "rt", log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def sende(rt: Path, payload: dict, typ: str = "hook",
          produzent: str = "hookbridge") -> None:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / f"{produzent}.sock"))
    c.sendall(json.dumps({"v": 1, "type": typ, "payload": payload}).encode() + b"\n")
    time.sleep(0.25)
    c.close()


def hole_state(rt: Path) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / STATE_SOCKET))
    try:
        return json.loads(c.makefile("rb").readline())
    finally:
        c.close()


# --------------------------------------------------------------------------
# State-Schema (Design 9, v2)
# --------------------------------------------------------------------------

def test_state_liefert_schema_v2(hub):
    s = hole_state(hub.runtime_dir)
    assert s["v"] == 2
    for feld in ("rev", "mood", "sessions", "focus", "bubble", "voice", "perception"):
        assert feld in s, f"{feld} fehlt im State"


def test_leerer_hub_ist_sleeping(hub):
    assert hole_state(hub.runtime_dir)["mood"] == "sleeping"


def test_ereignis_setzt_mood_und_bubble(hub):
    sende(hub.runtime_dir, {"hook_event_name": "Notification", "session_id": "s1",
                            "notification_type": "permission_prompt",
                            "message": "Bash ausfuehren?"})
    s = hole_state(hub.runtime_dir)
    assert s["mood"] == "needs_input"
    assert s["bubble"]["urgent"] is True


def test_blasentext_wird_vor_dem_zustand_gesaeubert(hub):
    sende(hub.runtime_dir, {
        "hook_event_name": "Notification",
        "session_id": "s1",
        "notification_type": "permission_prompt",
        "message": "harmlos\u202Etxt\u200B",
    })
    body = hole_state(hub.runtime_dir)["bubble"]["body"]
    assert body == r"harmlos\u202Etxt\u200B"
    assert body.isascii()


def test_bubble_dismiss_ueber_face_loescht_blase(hub):
    sende(hub.runtime_dir, {
        "hook_event_name": "Notification",
        "session_id": "s1",
        "notification_type": "permission_prompt",
        "message": "Bash ausfuehren?",
    })
    assert hole_state(hub.runtime_dir)["bubble"] is not None
    sende(hub.runtime_dir, {}, typ="bubble_dismiss", produzent="face")
    assert hole_state(hub.runtime_dir)["bubble"] is None


def test_bubble_dismiss_ueber_hookbridge_wird_abgewiesen(hub):
    sende(hub.runtime_dir, {
        "hook_event_name": "Notification",
        "session_id": "s1",
        "notification_type": "permission_prompt",
        "message": "bleibt",
    })
    sende(hub.runtime_dir, {}, typ="bubble_dismiss")
    assert hole_state(hub.runtime_dir)["bubble"] is not None


# --------------------------------------------------------------------------
# rev
# --------------------------------------------------------------------------

def test_rev_steigt_bei_aenderung(hub):
    vorher = hole_state(hub.runtime_dir)["rev"]
    sende(hub.runtime_dir, {"hook_event_name": "SessionStart", "session_id": "s1"})
    assert hole_state(hub.runtime_dir)["rev"] > vorher


def test_rev_bleibt_bei_noop(hub):
    """Ein Poller soll an rev erkennen, ob sich etwas getan hat. Eine bei
    jedem Ereignis hochgezaehlte Zahl waere dafuer wertlos."""
    sende(hub.runtime_dir, {"hook_event_name": "PreToolUse", "session_id": "s1"})
    vorher = hole_state(hub.runtime_dir)["rev"]
    sende(hub.runtime_dir, {"hook_event_name": "PreToolUse", "session_id": "s1"})
    assert hole_state(hub.runtime_dir)["rev"] == vorher


def test_rev_bleibt_bei_unbekanntem_ereignis(hub):
    sende(hub.runtime_dir, {"hook_event_name": "SessionStart", "session_id": "s1"})
    vorher = hole_state(hub.runtime_dir)["rev"]
    sende(hub.runtime_dir, {"hook_event_name": "GibtEsNicht", "session_id": "s1"})
    assert hole_state(hub.runtime_dir)["rev"] == vorher


# --------------------------------------------------------------------------
# focus.project
# --------------------------------------------------------------------------

def test_focus_project_bei_mehreren_sessions(hub):
    sende(hub.runtime_dir, {"hook_event_name": "PreToolUse", "session_id": "a",
                            "cwd": "/home/x/Github/MMC"})
    sende(hub.runtime_dir, {"hook_event_name": "Notification", "session_id": "b",
                            "notification_type": "permission_prompt",
                            "cwd": "/home/x/Github/dAImon"})
    s = hole_state(hub.runtime_dir)
    assert s["sessions"] == 2
    # needs_input gewinnt, also muss focus auf die wartende Sitzung zeigen.
    assert s["mood"] == "needs_input"
    assert s["focus"]["session_id"] == "b"
    assert s["focus"]["project"] == "dAImon"


def test_prioritaet_needs_input_schlaegt_working(hub):
    sende(hub.runtime_dir, {"hook_event_name": "PreToolUse", "session_id": "a"})
    sende(hub.runtime_dir, {"hook_event_name": "Notification", "session_id": "b",
                            "notification_type": "permission_prompt"})
    assert hole_state(hub.runtime_dir)["mood"] == "needs_input"


# --------------------------------------------------------------------------
# Leases -- der eigentliche Fortschritt gegenueber dem Bestandsdaemon
# --------------------------------------------------------------------------

def test_lease_verfaellt_wenn_der_prozess_stirbt():
    """Eine per Strg-C beendete Sitzung darf das Pet nicht auf needs_input
    haengen lassen. Mit reiner TTL waere es eine Stunde lang so geblieben --
    also genau der Zustand, der Aufmerksamkeit verlangt, fuer etwas das es
    nicht mehr gibt."""
    kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    st = HubState()
    st.apply("needs_input", session_id="s1", pid=kind.pid,
             bubble={"title": "warte", "urgent": True})
    assert st.snapshot()["mood"] == "needs_input"

    kind.kill()
    kind.wait()
    time.sleep(LEASE_GRACE_S + 0.3)
    s = st.snapshot()
    assert s["sessions"] == 0
    assert s["mood"] == "sleeping"


def test_lease_ueberlebt_solange_der_prozess_lebt():
    kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        st = HubState()
        st.apply("working", session_id="s1", pid=kind.pid)
        time.sleep(LEASE_GRACE_S + 0.3)
        assert st.snapshot()["sessions"] == 1
    finally:
        kind.kill()
        kind.wait()


def test_nonce_gegen_pid_wiederverwendung():
    """Eine neu vergebene PID hat eine andere Startzeit. Ohne diese Pruefung
    wuerde eine fremde Sitzung das Lease am Leben halten."""
    st = HubState()
    st.apply("needs_input", session_id="s1", pid=1)   # init lebt garantiert
    assert st.snapshot()["sessions"] == 1
    st._sessions["s1"].starttime = 999_999_999
    st._sessions["s1"].ts -= LEASE_GRACE_S + 1
    assert st.snapshot()["sessions"] == 0


def test_ohne_pid_greift_die_ttl():
    st = HubState()
    st.apply("working", session_id="s1")
    assert st.snapshot()["sessions"] == 1
    st._sessions["s1"].ts -= 4000
    assert st.snapshot()["sessions"] == 0


def test_proc_starttime_bei_totem_prozess():
    assert proc_starttime(999_999) is None


def test_proc_starttime_vertraegt_klammern_im_namen():
    """Der Kommentarname in /proc/<pid>/stat darf Klammern enthalten. Wer am
    ersten Leerzeichen teilt, liest ab da Muell."""
    assert proc_starttime(1) is not None


# --------------------------------------------------------------------------
# Kein TCP
# --------------------------------------------------------------------------

def test_kein_af_inet_im_quelltext():
    """RestrictAddressFamilies=AF_UNIX muss erfuellbar bleiben (T-0.14). Ein
    einziger AF_INET macht die Direktive unmoeglich."""
    import ast
    for modul in ("daimon/hub/daemon.py", "daimon/hub/state.py",
                  "daimon/hub/bus.py", "daimon/common/ipc.py"):
        baum = ast.parse(Path(modul).read_text())
        for k in ast.walk(baum):
            if isinstance(k, ast.Attribute) and k.attr in ("AF_INET", "AF_INET6"):
                raise AssertionError(f"{modul}: {k.attr} verwendet")


def test_produzent_darf_fremden_typ_nicht_senden(hub):
    """T-0.7 in Aktion: die Verbindung wird abgebaut, nicht bloss verworfen."""
    rt = hub.runtime_dir
    sende(rt, {"hook_event_name": "SessionStart", "session_id": "s1"})
    vorher = hole_state(rt)["rev"]
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / "hookbridge.sock"))
    c.sendall(json.dumps({"v": 1, "type": "screen", "payload": {}}).encode() + b"\n")
    time.sleep(0.3)
    c.close()
    assert hole_state(rt)["rev"] == vorher


# --------------------------------------------------------------------------
# Bus
# --------------------------------------------------------------------------

def test_projekt_aus_cwd():
    assert B.projekt_aus_cwd("/home/x/Github/dAImon") == "dAImon"
    assert B.projekt_aus_cwd("/home/x/Github/dAImon/") == "dAImon"
    assert B.projekt_aus_cwd("") == ""


def test_kaputter_abonnent_reisst_die_anderen_nicht_mit():
    from daimon.common.protocol import Event
    gesehen = []
    b = B.Bus()
    b.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("kaputt")))
    b.subscribe(gesehen.append)
    b.publish(Event(type="hook", payload={}))
    assert len(gesehen) == 1


def test_mapping_unbekannt_gibt_none():
    assert B.mood_of({"hook_event_name": "GibtEsNicht"}) == (None, None)
