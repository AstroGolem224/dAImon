#!/usr/bin/env bash
# T-7.1.v — Verifizierer fuer Archivdienst und Schema.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../.." && pwd)"
PRUEFLING="${DAIMON_FIXTURE:-$REPO}"
if [[ -x "$PRUEFLING/.venv/bin/python" ]]; then
    PYTHON="$PRUEFLING/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

# Der Pruefstand importiert AUS dem Pruefling. Ohne diese Zeile legt jeder
# Lauf `__pycache__/` in das Gut-Muster und in jeden Mutantenbaum -- eine
# stille Aenderung am Messgegenstand, und im Gut-Muster eine, die committet
# wuerde.
export PYTHONDONTWRITEBYTECODE=1

exec "$PYTHON" "$HIER/t71_pruefstand.py" "$PRUEFLING"
