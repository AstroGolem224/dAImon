"""Waechter fuer die NAHT meta.sh -> erzeugen.sh -> Messung.

Gemessen wurde bis heute gegen eingecheckte Mutantenverzeichnisse, waehrend
`tests/mutants/T-4.6/erzeugen.sh` dieselben Mutanten deterministisch herstellte
und von niemandem aufgerufen wurde. Zwei Fassungen derselben Regel.

Die Zusage lautet jetzt: gibt es einen Erzeuger, gilt SEIN Ergebnis -- und
scheitert er, ist der Meta-Lauf rot. Der zweite Teil ist der wichtigere: ein
meta.sh, das bei kaputter Erzeugung gegen alte Kopien weitermisst, meldet
`alle erkannt` ueber Baeume, die niemand mehr an heutigen Code bindet.

Geprueft wird an einem Wegwerf-Task, nicht an T-4.6: der echte Lauf dauert
Minuten, und ein Waechter, der zu teuer ist, wird abgeschaltet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
META = REPO / "tests" / "verify" / "meta.sh"
TASK = "T-metaprobe"

# Rot genau dann, wenn im Prueflingsbaum eine Datei MUTIERT liegt.
VERIFIZIERER = """#!/usr/bin/env bash
set -euo pipefail
if [[ -e "${DAIMON_FIXTURE:?}/MUTIERT" ]]; then
    echo "FAIL: MUTIERT gefunden"
    exit 1
fi
echo "ok"
"""

# Bricht ab, sobald die Marke BRICH_AB liegt -- die Bauform des echten
# Fehlers: "Mutationsanker nicht genau einmal gefunden".
ERZEUGER = """#!/usr/bin/env bash
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -e "$HIER/BRICH_AB" ]]; then
    echo "probe: Mutationsanker nicht genau einmal gefunden" >&2
    exit 1
fi
rm -rf -- "$HIER/probe-mutante"
mkdir -p "$HIER/probe-mutante"
: >"$HIER/probe-mutante/MUTIERT"
echo "probe: 1 Mutante erzeugt."
"""


@pytest.fixture
def probe_task(tmp_path):
    """Legt Verifizierer, Gut-Muster und Mutantenverzeichnis des Probe-Tasks an."""
    verifier = REPO / "tests" / "verify" / f"{TASK}.sh"
    good = REPO / "tests" / "fixtures" / "known-good" / TASK
    mutdir = REPO / "tests" / "mutants" / TASK
    for pfad in (verifier, good, mutdir):
        assert not pfad.exists(), f"{pfad} existiert schon -- Aufraeumen verpasst?"
    try:
        verifier.write_text(VERIFIZIERER, encoding="utf-8")
        os.chmod(verifier, 0o755)
        good.mkdir(parents=True)
        (good / "quelle.txt").write_text("gut\n", encoding="utf-8")
        mutdir.mkdir(parents=True)
        yield mutdir
    finally:
        verifier.unlink(missing_ok=True)
        shutil.rmtree(good, ignore_errors=True)
        shutil.rmtree(mutdir, ignore_errors=True)


def meta_lauf():
    return subprocess.run([str(META), TASK], capture_output=True, text=True)


def test_meta_erzeugt_die_mutanten_selbst(probe_task):
    """Ohne eingecheckte Verzeichnisse: meta.sh stellt sie her und erkennt sie."""
    erzeuger = probe_task / "erzeugen.sh"
    erzeuger.write_text(ERZEUGER, encoding="utf-8")
    os.chmod(erzeuger, 0o755)
    assert not (probe_task / "probe-mutante").exists()

    lauf = meta_lauf()
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "werden erzeugt" in lauf.stdout
    assert "Mutante 'probe-mutante' erkannt" in lauf.stdout
    assert (probe_task / "probe-mutante" / "MUTIERT").is_file()


def test_meta_wird_rot_wenn_die_erzeugung_scheitert(probe_task):
    """Der eigentliche Befund: eine gueltige alte Kopie darf nicht gruen retten."""
    erzeuger = probe_task / "erzeugen.sh"
    erzeuger.write_text(ERZEUGER, encoding="utf-8")
    os.chmod(erzeuger, 0o755)

    # Positivkontrolle: derselbe Baum, derselbe Verifizierer -- gruen.
    assert meta_lauf().returncode == 0
    assert (probe_task / "probe-mutante" / "MUTIERT").is_file()

    # Jetzt scheitert nur die Erzeugung. Die Mutante von eben liegt noch da
    # und wuerde weiterhin erkannt -- genau der Lauf, der frueher `1 Mutante,
    # alle erkannt` meldete, ohne etwas ueber heute zu sagen.
    (probe_task / "BRICH_AB").touch()
    lauf = meta_lauf()
    assert lauf.returncode != 0, lauf.stdout + lauf.stderr
    assert "Erzeugung der Mutanten gescheitert" in lauf.stderr


def test_ohne_erzeuger_bleibt_es_beim_alten(probe_task):
    """Aeltere Tasks haben nur eingecheckte Verzeichnisse -- die gelten weiter."""
    (probe_task / "probe-mutante").mkdir()
    (probe_task / "probe-mutante" / "MUTIERT").touch()
    assert not (probe_task / "erzeugen.sh").exists()

    lauf = meta_lauf()
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "werden erzeugt" not in lauf.stdout
    assert "Mutante 'probe-mutante' erkannt" in lauf.stdout
