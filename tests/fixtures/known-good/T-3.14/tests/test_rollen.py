"""Der Rollenwaechter muss ANSPRINGEN, nicht nur existieren.

Am 03.08. fiel auf, dass `.claude/settings.json` seit Commit 21c9a78 kaputtes
JSON war (unescapte Quotes um `$CLAUDE_PROJECT_DIR`). Claude Code laedt eine
unparsbare Settings-Datei stillschweigend nicht -- der `PreToolUse`-Hook feuerte
also nie, und ein Schreibversuch ohne `DAIMON_ROLE` ging durch, obwohl
`roles.toml` "unknown darf nichts schreiben" sagt.

Das ist Fall 15 der Fehlerliste in HANDOVER.md, noch einmal: ein Mechanismus,
dessen Wirksamkeit niemand geprueft hat. Deshalb steht hier die Prueffrage, die
gefehlt hat -- nicht "gibt es den Hook", sondern "lehnt er ab".
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETTINGS = REPO / ".claude" / "settings.json"
GUARD = REPO / ".claude" / "hooks" / "role_guard.py"


def test_settings_ist_parsbares_json():
    # Genau der Fehler vom 03.08.: die Datei war da, sah gut aus, und war kaputt.
    json.loads(SETTINGS.read_text())


def test_settings_verdrahtet_den_waechter():
    cfg = json.loads(SETTINGS.read_text())
    haken = cfg["hooks"]["PreToolUse"]
    kommandos = [h["command"] for eintrag in haken for h in eintrag["hooks"]]
    assert any("role_guard.py" in k for k in kommandos), kommandos
    # Er muss auch fuer die Werkzeuge gelten, die tatsaechlich schreiben.
    matcher = " ".join(e.get("matcher", "") for e in haken)
    for werkzeug in ("Write", "Edit", "Bash"):
        assert werkzeug in matcher, (werkzeug, matcher)


def _guard(rolle: str | None, pfad: str) -> dict:
    """Den Hook so aufrufen, wie Claude Code ihn aufruft: Ereignis auf stdin."""
    umgebung = {"PATH": "/usr/bin:/bin"}
    if rolle is not None:
        umgebung["DAIMON_ROLE"] = rolle
    lauf = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": str(REPO / pfad)}}),
        capture_output=True, text=True, timeout=30, env=umgebung, cwd=str(REPO))
    assert lauf.returncode == 0, lauf.stderr
    if not lauf.stdout.strip():
        return {}   # kein Urteil == durchgelassen
    return json.loads(lauf.stdout)


def _entschied(antwort: dict) -> str | None:
    return antwort.get("hookSpecificOutput", {}).get("permissionDecision")


@pytest.mark.parametrize("pfad", ["daimon/hub/daemon.py", "tests/verify/T-3.7.sh"])
def test_ohne_rolle_wird_abgelehnt(pfad):
    # Fail closed. Das ist die Zusage, die wochenlang nicht galt.
    assert _entschied(_guard(None, pfad)) == "deny"


def test_builder_darf_produktivcode_und_keinen_verifizierer():
    # Positiv- und Negativfall am selben Waechter -- ein Hook, der ALLES
    # ablehnt, wuerde den Test oben auch bestehen.
    assert _entschied(_guard("builder", "daimon/hub/daemon.py")) != "deny"
    assert _entschied(_guard("builder", "tests/verify/T-3.7.sh")) == "deny"


def test_reviewer_darf_keinen_produktivcode():
    assert _entschied(_guard("reviewer", "tests/verify/T-3.7.sh")) != "deny"
    assert _entschied(_guard("reviewer", "daimon/hub/daemon.py")) == "deny"
