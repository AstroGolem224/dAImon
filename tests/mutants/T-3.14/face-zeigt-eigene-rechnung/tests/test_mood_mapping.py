"""T-0.3.t — das Mood-Mapping festschreiben, BEVOR refaktoriert wird.

Diese Tests laufen gegen den Bestandscode `daimon/hub/legacy_daemon.py`. Sie
sind kein Entwurf fuer den neuen Hub, sondern ein Netz: was hier gruen ist,
muss nach dem Refactoring in T-0.4 ff. gruen bleiben.

Zwei Sorten Test, und die Trennung ist der Punkt:

  * Die **Mapping-Tests** halten fest, was der Bestandscode heute tut. Sie sind
    gruen und muessen es bleiben.
  * Die **Abweichungs-Tests** halten fest, was T−1.5 an echten Sitzungen
    gemessen hat und was der Bestandscode NICHT leistet. Sie sind
    `xfail(strict=True)` -- also dokumentiert rot. Strict ist wichtig: schlaegt
    einer unerwartet um, faellt der Lauf. Ein stillschweigend behobener Mangel
    ist genauso ein Signal wie ein neuer.

Quellen: Mapping-Tabelle in `docs/PHASE3-original.md` 4, Abweichungen in
`spikes/mood/results.json` (23 Sitzungen, 663 Ereignisse).
"""

import importlib.util
import json
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MOOD_RESULTS = REPO / "spikes" / "mood" / "results.json"


def _load_legacy():
    """Das Modul liegt unter daimon/hub/, hat aber keine Paket-Importe --
    direkt ueber den Pfad laden, damit der Test nicht an Importwegen haengt."""
    spec = importlib.util.spec_from_file_location(
        "legacy_daemon", REPO / "daimon" / "hub" / "legacy_daemon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


legacy = _load_legacy()


@pytest.fixture
def state():
    return legacy.State()


def mood_of(state, event):
    """Ereignis anwenden und den daraus folgenden Gesamt-Mood lesen."""
    state.apply(event)
    return state.snapshot()["mood"]


# --------------------------------------------------------------------------
# 1. Ein Test je Zeile der Mood-Tabelle aus PHASE3-original.md 4
# --------------------------------------------------------------------------

TABELLE = [
    ("SessionStart", {}, "observing"),
    ("UserPromptSubmit", {}, "thinking"),
    ("PreToolUse", {"tool_name": "Bash"}, "working"),
    ("PostToolUse", {"tool_name": "Bash"}, "working"),
    ("Notification", {"notification_type": "permission_prompt"}, "needs_input"),
    ("Notification", {"notification_type": "idle_prompt"}, "idle"),
    ("Stop", {"last_assistant_message": "fertig"}, "done"),
    ("StopFailure", {"error": "kaputt"}, "failed"),
    ("PostToolUseFailure", {"error": "kaputt"}, "failed"),
    ("SessionEnd", {}, "sleeping"),
]


@pytest.mark.parametrize("event_name,extra,erwartet", TABELLE,
                         ids=[f"{n}-{e.get('notification_type','')}".rstrip("-")
                              for n, e, _ in TABELLE])
def test_mapping_tabelle(state, event_name, extra, erwartet):
    ev = {"hook_event_name": event_name, "session_id": "s1", **extra}
    assert mood_of(state, ev) == erwartet


def test_permission_prompt_erzeugt_dringende_bubble():
    """needs_input ist der Mood, an dem die ganze Idee haengt -- ohne
    Sprechblase sieht der Nutzer nicht, WORAUF gewartet wird."""
    st = legacy.State()
    st.apply({"hook_event_name": "Notification", "session_id": "s1",
              "notification_type": "permission_prompt", "message": "Bash ausfuehren?"})
    bubble = st.snapshot()["bubble"]
    assert bubble is not None
    assert bubble["urgent"] is True
    assert "Bash ausfuehren?" in bubble["body"]


def test_stop_bubble_traegt_die_letzte_antwort():
    st = legacy.State()
    st.apply({"hook_event_name": "Stop", "session_id": "s1",
              "last_assistant_message": "Der Task ist durch."})
    bubble = st.snapshot()["bubble"]
    assert bubble is not None
    assert "durch" in bubble["body"]
    assert bubble["urgent"] is False


# --------------------------------------------------------------------------
# 2. Prioritaetsarbitrierung bei mehreren Sessions
# --------------------------------------------------------------------------

def test_prioritaet_needs_input_schlaegt_alles():
    """Die eigentliche Killer-Funktion: am Rand des Blickfelds sehen, dass ein
    Agent wartet. Wenn eine andere Session lauter ist, ist sie wertlos."""
    st = legacy.State()
    st.apply({"hook_event_name": "PreToolUse", "session_id": "a", "tool_name": "Bash"})
    st.apply({"hook_event_name": "Notification", "session_id": "b",
              "notification_type": "permission_prompt"})
    st.apply({"hook_event_name": "UserPromptSubmit", "session_id": "c"})
    assert st.snapshot()["mood"] == "needs_input"


def test_prioritaet_reihenfolge_ist_unerheblich():
    """Der Gewinner darf nicht davon abhaengen, welche Session zuletzt meldete."""
    vor = legacy.State()
    vor.apply({"hook_event_name": "Notification", "session_id": "b",
               "notification_type": "permission_prompt"})
    vor.apply({"hook_event_name": "PreToolUse", "session_id": "a", "tool_name": "Bash"})
    assert vor.snapshot()["mood"] == "needs_input"


def test_prioritaet_vollstaendig_geordnet():
    """Jeder Mood der Tabelle muss in PRIORITY stehen, sonst gewinnt er ueber
    den .get(m, 0)-Rueckfall stillschweigend NIE."""
    aus_tabelle = {m for _, _, m in TABELLE}
    assert aus_tabelle <= set(legacy.PRIORITY)


def test_ohne_session_ist_es_sleeping():
    assert legacy.State().snapshot()["mood"] == "sleeping"


# --------------------------------------------------------------------------
# 3. Session-TTL
# --------------------------------------------------------------------------

def test_ttl_raeumt_tote_sessions_ab(monkeypatch):
    st = legacy.State()
    st.apply({"hook_event_name": "PreToolUse", "session_id": "alt", "tool_name": "Bash"})
    assert st.snapshot()["sessions"] == 1

    # Die Uhr vorstellen statt eine Stunde zu warten.
    spaeter = time.time() + legacy.SESSION_TTL + 1
    monkeypatch.setattr(legacy.time, "time", lambda: spaeter)
    snap = st.snapshot()
    assert snap["sessions"] == 0
    assert snap["mood"] == "sleeping"


def test_ttl_laesst_frische_sessions_stehen(monkeypatch):
    st = legacy.State()
    st.apply({"hook_event_name": "PreToolUse", "session_id": "frisch", "tool_name": "Bash"})
    knapp = time.time() + legacy.SESSION_TTL - 60
    monkeypatch.setattr(legacy.time, "time", lambda: knapp)
    assert st.snapshot()["sessions"] == 1


# --------------------------------------------------------------------------
# 4. Unbekanntes Ereignis aendert nichts
# --------------------------------------------------------------------------

def test_unbekanntes_ereignis_aendert_den_zustand_nicht():
    st = legacy.State()
    st.apply({"hook_event_name": "PreToolUse", "session_id": "a", "tool_name": "Bash"})
    vorher = st.snapshot()
    st.apply({"hook_event_name": "VoelligNeuesEreignis", "session_id": "a"})
    nachher = st.snapshot()
    assert nachher["mood"] == vorher["mood"]
    assert nachher["sessions"] == vorher["sessions"]
    assert nachher["rev"] == vorher["rev"], "rev darf ohne Zustandsaenderung nicht steigen"


def test_ereignis_ohne_namen_wirft_nicht():
    """Ein Hook kann eine unlesbare Nutzlast liefern. Der Daemon darf davon
    nicht sterben -- er haengt an jedem Werkzeugaufruf von Claude Code."""
    st = legacy.State()
    st.apply({})
    assert st.snapshot()["mood"] == "sleeping"


# --------------------------------------------------------------------------
# 5. Abweichungen aus T−1.5 -- dokumentiert rot
# --------------------------------------------------------------------------

def gemessene_abweichungen():
    if not MOOD_RESULTS.exists():
        return []
    return json.loads(MOOD_RESULTS.read_text()).get("mismatches", [])


def test_abweichungsliste_ist_nicht_leer():
    """Waere sie leer, waeren die xfail-Tests unten sinnlos -- und das waere
    wahrscheinlich ein Fehler in der Erhebung, nicht ein perfektes Mapping."""
    assert gemessene_abweichungen(), "T−1.5 hat keine Abweichungen gemeldet"


@pytest.mark.xfail(strict=True, reason=(
    "T−1.5, Klasse LUECKE: SubagentStop feuerte 19x in echten Sitzungen, steht "
    "aber in keiner Zeile des Mappings. Der Client wuesste nicht, was er zeigen "
    "soll. Zu entscheiden in T-0.7."))
def test_luecke_subagent_stop():
    st = legacy.State()
    st.apply({"hook_event_name": "SessionStart", "session_id": "a"})
    st.apply({"hook_event_name": "SubagentStop", "session_id": "a"})
    assert st.snapshot()["mood"] != "observing", "SubagentStop bleibt unsichtbar"


@pytest.mark.xfail(strict=True, reason=(
    "T−1.5, Klasse LUECKE: Notification mit notification_type 'auth_success' "
    "feuerte in echten Sitzungen, faellt aber in den Rueckfall auf 'observing'."))
def test_luecke_notification_auth_success():
    st = legacy.State()
    st.apply({"hook_event_name": "Notification", "session_id": "a",
              "notification_type": "auth_success"})
    assert st.snapshot()["mood"] != "observing"


@pytest.mark.xfail(strict=True, reason=(
    "T−1.5, Klasse NICHT_ENTSCHEIDBAR und die Auflage aus dem "
    "Entscheidungsprotokoll: eine Notification OHNE notification_type ist von "
    "einer Freigabeanfrage nicht zu unterscheiden. Der Bestandscode faellt "
    "still auf 'observing' zurueck -- der Nutzer saehe nicht, dass gewartet "
    "wird. Das ist die gefaehrlichste der drei Klassen, weil sie aussieht wie "
    "ein normaler Zustand."))
def test_nicht_entscheidbar_notification_ohne_typ():
    st = legacy.State()
    st.apply({"hook_event_name": "Notification", "session_id": "a",
              "message": "Claude wartet auf eine Freigabe."})
    assert st.snapshot()["mood"] == "needs_input"


@pytest.mark.xfail(strict=True, reason=(
    "T−1.5, Klasse TOTER_EINTRAG: PostToolUseFailure steht im Mapping, feuerte "
    "aber in 663 Ereignissen kein einziges Mal. Der Mood 'failed' ist ueber "
    "diesen Weg unerreichbar; er kommt ausschliesslich ueber StopFailure."))
def test_toter_eintrag_post_tool_use_failure():
    ereignisse = json.loads(MOOD_RESULTS.read_text()).get("ereignisse_nach_typ", {})
    assert ereignisse.get("PostToolUseFailure", 0) > 0
