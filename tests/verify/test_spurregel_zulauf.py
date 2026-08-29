"""Zulauf fuer den Mutationstest der Spurregel.

`tests/verify/freeze-deps.py` traegt die Regel, die verhindert, dass ein
eingefrorener Verifizierer heimlich einen ungeschuetzten Helfer benutzt: jede
in der strace-Spur geoeffnete Datei unter `tests/verify/` oder
`tests/harness/` muss deklariert sein. Die Positivkontrolle dieser Regel
existiert seit dem 11.08. -- `tests/mutants/freeze-extension/` mit fuenf
Mutanten, gefahren von `tests/verify/freeze-extension.sh` ueber `meta.sh`.

Sie hatte nur keinen Aufrufer. Kein Gate, kein Verifizierer, kein Eintrag in
FROZEN oder FROZEN.deps -- gefahren wurde sie einmal von Hand und danach nie
wieder (PLAN-REVIEW-LOG.md). Wer die Spurregel versehentlich wirkungslos
macht, bekam gruen: genau die Gestalt aus CLAUDE.md, diesmal am Waechter des
Waechters.

Der Zulauf sitzt hier, weil `pytest -q` die letzte Zeile JEDES Phasen-Gates
ist (docs/IMPLEMENTATION-PLAN.md, Gate P0..P7) und die Suite ohnehin laufend
gefahren wird. Er sitzt unter `tests/verify/`, weil diese Datei damit fuer
die Builder-Rolle gesperrt ist (.claude/roles.toml) -- der Waechter laesst
sich nicht von der Seite abschalten, die er bewacht.

Kosten: rund 0,3 s. Der Lauf braucht `strace`; ohne `strace` friert
`freeze.sh` ohnehin nichts ein, ein Ueberspringen waere also nur eine
verdeckte Luecke.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MUTANTEN = REPO / "tests" / "mutants" / "freeze-extension"


def test_spurregel_erkennt_ihre_mutanten():
    lauf = subprocess.run(
        [str(REPO / "tests" / "verify" / "meta.sh"), "freeze-extension"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    # Nicht nur "gruen": jede einzelne Mutante muss GEMESSEN worden sein.
    # Sonst faellt der Waechter still um, sobald jemand Mutanten entfernt.
    erwartet = sorted(p.name for p in MUTANTEN.iterdir() if p.is_dir())
    assert len(erwartet) >= 5, erwartet
    for name in erwartet:
        assert f"Mutante '{name}' erkannt." in lauf.stdout, lauf.stdout
