"""Waechter fuer die Lauf-Sperre in `tests/verify/freeze.sh`.

Am 30.08.2026 fuhren zwei Sitzungen gleichzeitig Verifizierer im selben Baum:
eine Desktop-Sitzung `freeze.sh T-3.14` ab 16:36, eine zweite ab 17:35 einen
blanken `bash tests/verify/T-3.14.sh`. Niemand meldete das. Der Schaden ist
nicht die verlorene Zeit, sondern die RICHTUNG des Fehlers:

`tests/verify/meta.sh:62` wertet jeden Nicht-Null-Exit eines Fixture-Laufs als
„Mutante erkannt". Fremdlast laesst einen Mutanten aus dem falschen Grund
scheitern -- und er wird als erkannt verbucht. Ein falsches GRUEN, das keine
Auswertung sieht, weil sie nur nach „alle erkannt" schaut.

Warum dieser Waechter und nicht ein Selbsttest in freeze.sh
----------------------------------------------------------------------------
Ein `freeze.sh --selbsttest` muesste die Sperre nehmen, um sie zu pruefen --
und liefe damit in genau das Verhalten, das er misst. Hier laeuft der Block
stattdessen gegen ein eigenes `XDG_RUNTIME_DIR` im tmp_path: dieselbe
Mechanik, andere Sperrdatei, keine Beruehrung mit einem echten Lauf.

Der Anker ist die NAHT
----------------------------------------------------------------------------
Der Block wird zwischen den Markern aus `freeze.sh` HERAUSGESCHNITTEN und
ausgefuehrt. Verschwinden die Marker oder der Block, ist dieser Waechter rot --
und nicht etwa gruen, weil er nichts mehr zu pruefen faende. Das ist der
Unterschied zwischen „nichts gefunden" und „nicht gemessen".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FREEZE = REPO / "tests" / "verify" / "freeze.sh"
ANFANG = "# >>> lauf-sperre"
ENDE = "# <<< lauf-sperre"


def sperr_block() -> str:
    """Der Block aus freeze.sh. Fehlt er, ist das der Befund."""
    text = FREEZE.read_text(encoding="utf-8")
    assert ANFANG in text, f"{FREEZE}: Marke {ANFANG!r} fehlt -- Sperre entfernt?"
    assert ENDE in text, f"{FREEZE}: Marke {ENDE!r} fehlt"
    block = text.split(ANFANG, 1)[1].split(ENDE, 1)[0]
    assert "flock" in block, "Sperr-Block ohne flock"
    assert "pgrep" in block, "Sperr-Block ohne Vorpruefung auf fremde Laeufe"
    return block


@pytest.fixture
def attrappe(tmp_path: Path):
    """Ein Skript, das NUR den Sperr-Block faehrt, dann schlaeft.

    Eigenes XDG_RUNTIME_DIR: die Sperrdatei liegt im tmp_path und kann einen
    echten `freeze.sh`-Lauf auf dieser Maschine weder blockieren noch von ihm
    blockiert werden.
    """
    laufzeit = tmp_path / "laufzeit"
    laufzeit.mkdir()
    skript = tmp_path / "attrappe.sh"
    skript.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'REPO="{tmp_path}"; task="${{1:-T-probe}}"\n'
        + sperr_block()
        + '\necho "DRIN $$"\nsleep "${2:-0}"\n',
        encoding="utf-8",
    )
    skript.chmod(0o755)
    umgebung = {**os.environ, "XDG_RUNTIME_DIR": str(laufzeit)}

    def fahren(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(skript), *args],
            capture_output=True, text=True, timeout=60, env=umgebung, cwd=tmp_path,
        )

    fahren.umgebung = umgebung          # type: ignore[attr-defined]
    fahren.skript = skript              # type: ignore[attr-defined]
    fahren.baum = tmp_path              # type: ignore[attr-defined]
    return fahren


def test_ein_lauf_allein_kommt_durch(attrappe):
    """Positivkontrolle. Ohne sie sagt jeder Test unten nur aus, dass
    IRGENDETWAS abweist -- nicht, dass die Sperre der Grund ist."""
    r = attrappe("T-allein", "0")
    assert r.returncode == 0, r.stderr
    assert "DRIN" in r.stdout


def test_zweiter_lauf_wird_abgewiesen_und_nennt_den_halter(attrappe):
    erster = subprocess.Popen(
        ["bash", str(attrappe.skript), "T-erster", "5"],
        stdout=subprocess.PIPE, text=True,
        env=attrappe.umgebung, cwd=attrappe.baum,
    )
    try:
        # Warten, bis der erste die Sperre wirklich haelt. Ein festes sleep
        # waere hier eine Wette auf die Maschine.
        frist = time.monotonic() + 20
        while "DRIN" not in (erster.stdout.readline() or ""):
            assert time.monotonic() < frist, "erster Lauf kam nie durch"
        r = attrappe("T-zweiter", "0")
        assert r.returncode == 1, f"zweiter Lauf kam durch: {r.stdout}"
        assert "ABGELEHNT" in r.stderr
        # Die Ablehnung muss sagen WER blockiert -- sonst ist sie eine
        # Sackgasse, und der naechste Griff ist der zu --no-verify.
        assert "T-erster" in r.stderr, r.stderr
    finally:
        erster.kill()
        erster.wait(timeout=10)


def test_nach_dem_ende_ist_wieder_frei(attrappe):
    attrappe("T-vorher", "0")
    r = attrappe("T-danach", "0")
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep fehlt")
def test_blanker_verifiziererlauf_wird_gesehen(attrappe):
    """Der Fall vom 30.08.: kein zweites freeze.sh, sondern ein direkter
    Verifiziererlauf. Er haelt die Sperre nicht -- die Vorpruefung sieht ihn."""
    verify = attrappe.baum / "tests" / "verify"
    verify.mkdir(parents=True)
    blank = verify / "T-9.9.sh"
    blank.write_text("#!/usr/bin/env bash\nsleep 10\n", encoding="utf-8")
    blank.chmod(0o755)
    laeuft = subprocess.Popen(
        ["bash", "tests/verify/T-9.9.sh"], cwd=attrappe.baum,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1)
        r = attrappe("T-c", "0")
        assert r.returncode == 1, f"blanker Lauf blieb unbemerkt: {r.stdout}"
        assert "T-9.9.sh" in r.stderr, r.stderr
    finally:
        laeuft.kill()
        laeuft.wait(timeout=10)


@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep fehlt")
def test_ein_leser_loest_keinen_falschalarm_aus(attrappe):
    """Gegenprobe zum Test darueber. Das Muster verlangt einen Interpreter
    unmittelbar vor dem Pfad; ein Prozess, der die Datei bloss LIEST, ist
    kein Lauf. Ohne diese Kontrolle waere ein Muster, das auf jeden
    Pfadtreffer anspringt, oben genauso gruen -- und wuerde im Betrieb
    gueltige Freezes abweisen, bis jemand die Sperre abschaltet."""
    verify = attrappe.baum / "tests" / "verify"
    verify.mkdir(parents=True, exist_ok=True)
    datei = verify / "T-9.9.sh"
    datei.write_text("#!/usr/bin/env bash\nsleep 10\n", encoding="utf-8")
    leser = subprocess.Popen(
        ["tail", "-f", "tests/verify/T-9.9.sh"], cwd=attrappe.baum,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1)
        r = attrappe("T-d", "0")
        assert r.returncode == 0, f"Falschalarm auf einen Leser: {r.stderr}"
        assert "DRIN" in r.stdout
    finally:
        leser.kill()
        leser.wait(timeout=10)
