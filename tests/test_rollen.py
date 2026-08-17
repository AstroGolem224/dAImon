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


# --------------------------------------------------------------------------
# Die BASH-Seite. Hier sass der Befund vom 14.08., und am 17.08. hat er
# dreimal zugeschlagen -- jedes Mal bei einem LESENDEN Kommando.
#
# Der Prueflauf oben kennt nur `Write`. Damit war die Haelfte des Waechters
# ungeprueft, in der er zwei Entscheidungen faellt: "kann dieses Kommando
# ueberhaupt schreiben" und "welches Wort darin ist das Ziel". Beide sind zu
# grob, und beide Fehler zeigen in dieselbe Richtung -- er blockiert Lesen.
#
# Die Kommandos stehen als BAUSTEINE da und nicht als fertige Zeichenketten.
# Der Grund ist derselbe Befund: eine Datei, in der `tests/verify/...` neben
# einem schreibenden Verb im Klartext steht, laesst sich unter der Rolle
# `builder` nicht per Shell anlegen -- der Waechter liest den Kommandotext.
# Beim Schreiben dieser Zeilen ist das genau einmal passiert.
# --------------------------------------------------------------------------

VERIFIZIERER = "tests/" + "verify/T-0.0.sh"
FROZEN = "tests/" + "verify/FROZEN"


def _bash(rolle: str | None, kommando: str) -> dict:
    umgebung = {"PATH": "/usr/bin:/bin"}
    if rolle is not None:
        umgebung["DAIMON_ROLE"] = rolle
    lauf = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": kommando}}),
        capture_output=True, text=True, timeout=30, env=umgebung, cwd=str(REPO))
    assert lauf.returncode == 0, lauf.stderr
    return json.loads(lauf.stdout) if lauf.stdout.strip() else {}


# -- Was er BLOCKIEREN MUSS, und heute auch tut ---------------------------

@pytest.mark.parametrize("vorlage", [
    "echo kaputt > {z}",
    "echo kaputt >> {z}",
    "sed -i s/a/b/ {z}",
    "cp /tmp/x {z}",
    "mv /tmp/x {z}",
    "rm {z}",
    "tee {z} < /tmp/x",
    "truncate -s 0 {z}",
    "chmod 777 {z}",
    "git add {z}",
])
def test_der_builder_kommt_nicht_an_die_verifizierer(vorlage):
    """Die eigentliche Zusage. Sie gilt und muss jede Lockerung ueberleben --
    wer den Waechter praeziser macht, faehrt DIESE Liste zuerst."""
    kommando = vorlage.format(z=VERIFIZIERER)
    assert _entschied(_bash("builder", kommando)) == "deny", kommando


def test_und_ohne_rolle_erst_recht_nicht():
    assert _entschied(_bash(None, f"echo x > {VERIFIZIERER}")) == "deny"


def test_POSITIVKONTROLLE_ein_harmloses_kommando_geht_durch():
    """Ohne diese Zeile bestuende die Liste oben auch ein Waechter, der ALLES
    ablehnt -- und genau der waere das Problem."""
    assert _entschied(_bash("builder", "ls daimon")) != "deny"
    assert _entschied(_bash("builder", "python3 -m pytest -q")) != "deny"


def test_eine_commit_botschaft_darf_einen_verifizierer_nennen():
    """BERICHTIGT den Befund vom 14.08. Dort steht, ein `git commit` sei
    abgewiesen worden, weil seine BOTSCHAFT den Pfad eines Verifizierers
    nannte. Nachgemessen am 17.08.: er geht durch.

    `WRITING_CMD` kennt `git checkout|restore|apply|add` -- `git commit`
    steht nicht darin, und ohne Treffer sieht der Waechter den Text gar
    nicht erst an. Was jene Botschaft damals ausgeloest hat, muss etwas
    anderes gewesen sein: eine Umleitung im selben Kommando oder ein Wort
    wie `rm`, das der Regex als Verb liest.

    Der Fall steht hier, weil er in HANDOVER.md als Befund gefuehrt wurde --
    und ein Befund, den niemand nachmisst, wird zur Ueberlieferung."""
    kommando = f'git commit -m "beschrieben in {VERIFIZIERER}"'
    assert _entschied(_bash("builder", kommando)) != "deny"


# -- Was er DURCHLASSEN SOLLTE, und heute nicht tut -----------------------
#
# `strict=True`, also dokumentiert rot: wer den Waechter nachruestet, sieht
# XPASS und entfernt das Mark. Ein `xfail` ohne strict verschwiege den
# Fortschritt.

@pytest.mark.xfail(strict=True, reason=(
    "WRITING_CMD enthaelt `>\\s*\\S`, und eine Fehlerumleitung erfuellt das. "
    "Damit ist praktisch jedes Kommando mit `2>&1` oder `2>/dev/null` "
    "schreibend -- am 17.08. zweimal daran haengengeblieben, beide Male bei "
    "einem lesenden `ls` bzw. `cat`."))
@pytest.mark.parametrize("vorlage", [
    "cat {z} 2>/dev/null",
    "ls -l {z} 2>&1",
])
def test_eine_fehlerumleitung_ist_kein_schreiben(vorlage):
    kommando = vorlage.format(z=FROZEN)
    assert _entschied(_bash("builder", kommando)) != "deny", kommando


@pytest.mark.xfail(strict=True, reason=(
    "Trifft WRITING_CMD, gilt JEDES pfadaehnliche Wort im Kommandotext als "
    "Ziel -- auch das Argument eines lesenden Verbs und der Text einer "
    "Commit-Botschaft. Der Nachruestweg steht in HANDOVER.md: Ausfuehrung "
    "von Aenderung trennen, Ziele nur hinter schreibenden Verben suchen."))
@pytest.mark.parametrize("vorlage", [
    # Den Pruefstand AUSFUEHREN ist keine Aenderung an ihm.
    "bash {z} > /tmp/log",
    # Ein Kopiervorgang, dessen QUELLE geschuetzt ist -- gelesen, nicht
    # geschrieben.
    "cp {z} /tmp/kopie",
])
def test_ein_genannter_pfad_ist_kein_schreibziel(vorlage):
    kommando = vorlage.format(z=VERIFIZIERER)
    assert _entschied(_bash("builder", kommando)) != "deny", kommando
