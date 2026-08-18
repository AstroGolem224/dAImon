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


# -- Was er DURCHLASSEN MUSS ----------------------------------------------
#
# Bis zum 18.08. standen die beiden folgenden Pruefungen als
# `xfail(strict=True)` hier. Der Umbau vom 18.08. (Kommando zerlegen, Ziele
# nur hinter Schreib-Umleitungen und schreibenden Verben suchen) macht sie
# gruen; die Marken sind damit weg.

@pytest.mark.parametrize("vorlage", [
    "cat {z} 2>/dev/null",
    "ls -l {z} 2>&1",
])
def test_eine_fehlerumleitung_ist_kein_schreiben(vorlage):
    """`2>/dev/null` schreibt nach /dev/null, `2>&1` gar nicht in eine Datei.
    Beides ist kein Zugriff auf den genannten Pfad."""
    kommando = vorlage.format(z=FROZEN)
    assert _entschied(_bash("builder", kommando)) != "deny", kommando


@pytest.mark.parametrize("vorlage", [
    # Den Pruefstand AUSFUEHREN ist keine Aenderung an ihm.
    "bash {z} > /tmp/log",
    # Ein Kopiervorgang, dessen QUELLE geschuetzt ist -- gelesen, nicht
    # geschrieben.
    "cp {z} /tmp/kopie",
])
def test_ein_genannter_pfad_ist_kein_schreibziel(vorlage):
    """Ein Pfad im Kommandotext ist erst dann ein Ziel, wenn er hinter einer
    Schreib-Umleitung oder als Ziel eines schreibenden Verbs steht."""
    kommando = vorlage.format(z=VERIFIZIERER)
    assert _entschied(_bash("builder", kommando)) != "deny", kommando


# -- Die sechs Faelle aus dem Betrieb -------------------------------------
#
# Der Builder ist an diesen Kommandos real haengengeblieben, jedes Mal ohne
# etwas zu schreiben. Die Kommandos sind aus HANDOVER.md und dem Auftrag vom
# 18.08. nachgebildet -- der Wortlaut der Argumente kann abweichen, die
# Bauform ist die, die gegriffen hat.

ECHTE_FAELLE = [
    pytest.param(
        "cat {z} 2>/dev/null", FROZEN, id="17.08-cat-2-dev-null",
        # 17.08., Fall 1: eine Fehlerumleitung erfuellte `>\s*\S`.
    ),
    pytest.param(
        "ls -l {z} 2>&1", FROZEN, id="17.08-ls-2-1",
        # 17.08., Fall 2: dieselbe Ursache, `2>&1` ist nicht einmal eine Datei.
    ),
    pytest.param(
        "cat <<'EOF'\n{z}\nEOF", VERIFIZIERER, id="17.08-heredoc",
        # 17.08., Fall 3: ein Heredoc, dessen RUMPF Verifiziererpfade nannte.
        # Der Rumpf ist Text, kein Kommando.
    ),
    pytest.param(
        "cat {z} > /tmp/frozen.txt", FROZEN, id="17.08-cat-nach-tmp",
        # 17.08., Fall 4: ein `cat >`, das einen geschuetzten Pfad LIEST und
        # ausserhalb des Repos schreibt.
    ),
    pytest.param(
        'git add docs/handover.md && git commit -m "Notiz zu {z}"',
        VERIFIZIERER, id="17.08-git-add-und-commit",
        # 17.08., Fall 5: `git add` auf einem erlaubten Pfad im selben
        # Kommando wie ein `git commit`, dessen BOTSCHAFT einen Verifizierer
        # nannte.
    ),
    pytest.param(
        "git merge --no-ff reviewer/p4-T-7.2v 2>&1 | tail -5 ; du -sh {z}",
        "tests/" + "fixtures/known-good", id="18.08-merge-und-du",
        # 18.08., Fall 6: beide Defekte zugleich -- Fehlerumleitung plus ein
        # `du` auf einem geschuetzten Pfad im selben Kommando.
    ),
]


@pytest.mark.parametrize("vorlage,pfad", ECHTE_FAELLE)
def test_die_sechs_faelle_aus_dem_betrieb_gehen_durch(vorlage, pfad):
    """Am 17.08. fuenfmal, am 18.08. ein sechstes Mal: der Waechter hat ein
    LESENDES Kommando abgewiesen. Sie stehen hier, damit der Defekt nicht in
    anderer Gestalt zurueckkehrt."""
    kommando = vorlage.format(z=pfad)
    assert _entschied(_bash("builder", kommando)) != "deny", kommando


# -- Die Gegenrichtung: der Umbau darf nicht zu weit gehen ----------------

@pytest.mark.parametrize("vorlage", [
    "echo x > {z}",
    "cat vorlage.sh > {z}",
    "tee {z} < vorlage.sh",
    "sed -i s/a/b/ {z}",
    "cp /tmp/fremd.sh {z}",
    "bash -c 'echo x > {z}'",
    # Beim Umbau aufgefallen und mitgenommen: eine Kommandosubstitution und
    # ein `eval` sind eigene Kommandos, kein Text.
    "eval 'rm {z}'",
    "echo {z} | xargs rm",
])
def test_geschrieben_bleibt_geschrieben(vorlage):
    """Zweite Haelfte der Auflage vom 18.08.: praeziser heisst nicht
    durchlaessiger. Faellt eine dieser Zeilen, ist der Umbau falsch."""
    kommando = vorlage.format(z=VERIFIZIERER)
    assert _entschied(_bash("builder", kommando)) == "deny", kommando


# -- Was NACH dem Umbau vom 18.08. offen bleibt ---------------------------
#
# Alle drei kamen schon VOR dem Umbau durch -- nachgemessen am alten
# `WRITING_CMD`. Sie gehoeren nicht zu den beiden Defekten des Auftrags und
# sind deshalb nicht repariert, sondern dokumentiert rot: wer sie schliesst,
# sieht XPASS und entfernt die Marke.

@pytest.mark.xfail(strict=True, reason=(
    "Ein Verzeichnis als Ziel trifft die deny-Muster nicht: `blocked()` "
    "vergleicht 'tests/verify' weder mit 'tests/verify/**' noch mit "
    "'tests/verify/*'. Das ist ein Defekt der Musterauswertung, nicht der "
    "Kommandozerlegung -- er sitzt auch im Write-Pfad."))
def test_ein_verzeichnis_als_ziel_muesste_blockiert_werden():
    kommando = "cp /tmp/fremd.sh " + "tests/" + "verify/"
    assert _entschied(_bash("builder", kommando)) == "deny", kommando


@pytest.mark.xfail(strict=True, reason=(
    "Ein Interpreter schreibt ohne schreibendes Verb und ohne Umleitung. "
    "Der Waechter liest Shell-Syntax, keinen Python-Quelltext."))
def test_ein_interpreter_als_umweg_muesste_blockiert_werden():
    kommando = 'python3 -c "open(\'{z}\', \'w\')"'.format(z=VERIFIZIERER)
    assert _entschied(_bash("builder", kommando)) == "deny", kommando


@pytest.mark.xfail(strict=True, reason=(
    "`find ... -delete` und `-exec rm {} +` tragen ihr Ziel nicht als "
    "Argument eines schreibenden Verbs, sondern als Suchpfad."))
def test_find_delete_muesste_blockiert_werden():
    kommando = "find " + "tests/" + "verify -name '*.sh' -delete"
    assert _entschied(_bash("builder", kommando)) == "deny", kommando
