"""Waechter fuer die zweite Aufgabe von `.claude/hooks/role_guard.py`:
ein Verifiziererlauf zur Zeit.

Die erste Aufgabe des Hooks -- wer welchen Pfad schreiben darf -- prueft
`tests/test_rollen.py`. Hier geht es um die andere: waehrend ein Freeze laeuft,
darf kein zweiter Verifiziererlauf starten.

Warum der Hook und nicht der Verifizierer
----------------------------------------------------------------------------
`tests/verify/freeze.sh` haelt seit dem 30.08. selbst eine `flock`-Sperre
(siehe `tests/test_freeze_sperre.py`). Die greift gegen ein zweites freeze.sh.
Der Vorfall vom 30.08. hatte aber eine andere Bauform: eine Sitzung fuhr
`freeze.sh T-3.14` ab 16:36, eine zweite ab 17:35 einen BLANKEN
`bash tests/verify/T-3.14.sh`. Die Verifizierer koennen die Sperre nicht selbst
nehmen -- sie sind eingefroren, und eine Zeile in jedem waeren dreissig
Neueinfrierungen. Der Hook sieht jedes Bash-Kommando vorher und ist deshalb der
einzige Ort, an dem sich das schliessen laesst.

Was auf dem Spiel steht
----------------------------------------------------------------------------
`tests/verify/meta.sh:62` wertet jeden Nicht-Null-Exit eines Fixture-Laufs als
„Mutante erkannt". Fremdlast laesst einen Mutanten aus dem falschen Grund
scheitern -- und er wird als erkannt verbucht. Der Fehler zeigt in die
HARMLOSE Richtung und faellt keiner Auswertung auf, die nur nach „alle
erkannt" schaut.

Die Sperrdatei hier liegt in einem tmp_path mit eigenem `XDG_RUNTIME_DIR`.
Ein echter Freeze auf dieser Maschine wird davon weder blockiert noch bemerkt.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / ".claude" / "hooks" / "role_guard.py"
HALTER = "PID 4711 seit 2026-08-30 16:36:06 -- freeze.sh T-3.14"


def entscheiden(kommando: str, laufzeit: Path, rolle: str = "builder") -> tuple[str, str]:
    """(`deny`|`pass`, Rohausgabe) fuer ein Bash-Kommando."""
    umgebung = {**os.environ, "DAIMON_ROLE": rolle, "XDG_RUNTIME_DIR": str(laufzeit)}
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": kommando}}),
        capture_output=True, text=True, env=umgebung, cwd=REPO, timeout=30,
    )
    return ("deny" if '"deny"' in r.stdout else "pass"), r.stdout


@pytest.fixture
def frei(tmp_path: Path) -> Path:
    """Ein Laufzeitverzeichnis ohne gehaltene Sperre."""
    lz = tmp_path / "laufzeit"
    lz.mkdir()
    return lz


@pytest.fixture
def gehalten(frei: Path):
    """Dieselbe Sperre, aber von einem Fremden gehalten."""
    datei = frei / "daimon-verify.lock"
    fh = datei.open("a+")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    datei.write_text(HALTER + "\n")
    try:
        yield frei
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


# Alles, was ein Lauf IST -- und beim naechsten Mal anders geschrieben wird.
LAEUFE = [
    "bash tests/verify/T-3.14.sh",
    "bash -x tests/verify/T-3.14.sh",
    "timeout 5400 bash tests/verify/freeze.sh T-3.14",
    "./tests/verify/T-3.14.sh",
    "bash tests/verify/meta.sh T-3.14",
    f"bash {REPO}/tests/verify/T-3.14.sh",
    # Die Pruefstaende, direkt gefahren. Die Namen sind dem Bestand entnommen
    # (`ls tests/verify/*_pruefstand.py`), nicht der Vorstellung: neben `t311`
    # gibt es `t313b` und `t316b` -- Ziffern UND ein Buchstabe. Ein Muster mit
    # `t[0-9]+` sieht die zweite Bauform nicht, und genau die lief diese Woche
    # am haeufigsten.
    "python3 tests/verify/t311_pruefstand.py",
    "python3 tests/verify/t313b_pruefstand.py",
]

# Alles, was KEIN Lauf ist und waehrend eines Freeze weiter gehen muss.
# Ohne diese Liste waere ein Muster, das auf jeden Pfadtreffer anspringt,
# oben genauso gruen -- und wuerde im Betrieb eine Stunde lang alles abweisen,
# bis jemand den Hook abschaltet. Ein Waechter, der zu oft schreit, wird
# abgeschaltet, und dann schuetzt er gar nichts mehr.
KEINE_LAEUFE = [
    "cat tests/verify/T-3.14.sh",
    "grep -n foo tests/verify/T-3.14.sh",
    "git commit -m 'bash tests/verify/T-3.14.sh lief gruen'",
    # Dieselbe Bauform fuer den weiteren Pruefstand-Zweig. `[^/]+` ist breiter
    # als `t[0-9]+`; die Gegenprobe muss mitwachsen, sonst waere ein Muster,
    # das im Fliesstext sucht, oben genauso gruen.
    "git commit -m 'python3 tests/verify/t313b_pruefstand.py lief gruen'",
    "python3 -m pytest tests/test_rollen.py",
    "git status --short",
]


@pytest.mark.parametrize("kommando", LAEUFE)
def test_bei_freier_sperre_geht_jeder_lauf_durch(kommando, frei):
    """Positivkontrolle. Ohne sie belegt der Test darunter nur, dass
    IRGENDETWAS abweist -- nicht, dass die Sperre der Grund ist."""
    entscheidung, _ = entscheiden(kommando, frei)
    assert entscheidung == "pass", kommando


@pytest.mark.parametrize("kommando", LAEUFE)
def test_bei_gehaltener_sperre_wird_der_lauf_abgewiesen(kommando, gehalten):
    entscheidung, _ = entscheiden(kommando, gehalten)
    assert entscheidung == "deny", kommando


@pytest.mark.parametrize("kommando", KEINE_LAEUFE)
def test_lesen_und_nennen_bleibt_erlaubt(kommando, gehalten):
    entscheidung, roh = entscheiden(kommando, gehalten)
    assert entscheidung == "pass", f"{kommando}\n{roh}"


def test_die_ablehnung_nennt_den_halter(gehalten):
    """Eine Ablehnung ohne Auskunft ist eine Sackgasse, und der naechste Griff
    ist der zum Abschalten des Hooks."""
    _, roh = entscheiden("bash tests/verify/T-3.14.sh", gehalten)
    assert "PID 4711" in roh
    assert "freeze.sh T-3.14" in roh


def test_unlesbare_sperrdatei_laesst_durch(tmp_path):
    """FAIL OPEN, anders als die Rollenpruefung im selben Hook.

    Der Schaden einer verpassten Ablehnung ist eine verschmutzte Messung --
    aergerlich und nachtraeglich erkennbar. Der Schaden eines fail-closed
    waere ein Repo, in dem niemand mehr etwas ausfuehren kann, sobald
    /run/user einmal anders aussieht als erwartet.
    """
    entscheidung, _ = entscheiden(
        "bash tests/verify/T-3.14.sh", tmp_path / "gibt" / "es" / "nicht")
    assert entscheidung == "pass"


def test_die_rollenpruefung_bleibt_unberuehrt(gehalten):
    """Der Aufsatz darf die erste Aufgabe des Hooks nicht verdraengen --
    auch nicht, waehrend die Sperre haelt."""
    assert entscheiden("sed -i s/a/b/ tests/verify/T-0.8.sh", gehalten)[0] == "deny"
    assert entscheiden("touch daimon/hub/x.py", gehalten)[0] == "pass"
