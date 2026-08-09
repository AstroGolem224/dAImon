#!/usr/bin/env bash
# T-3.16: die Mimic-Anbindung (Phase 2c aus Mimic/PHASE2.md).
#
# Der Pruefstand steht in Python, weil die Messpunkte binaer sind: Mimics
# Rahmenprotokoll, die Samplerate am pw-cat-Aufruf und die rohen Anfragekoerper
# an der Attrappe. Mit jq und grep waere davon nichts belegbar.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

"$PY" -B -P "$HIER/t316_pruefstand.py" "$REPO" "$TARGET"; A=$?
"$PY" -B -P "$HIER/t316b_pruefstand.py" "$REPO" "$TARGET"; B=$?
[[ $A -eq 0 && $B -eq 0 ]]
