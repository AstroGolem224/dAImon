#!/usr/bin/env bash
# T-3.13b: Markierungsverfolgung und Senken.
#
# Der eigentliche Pruefstand steht in Python, damit die Marken am anderen
# Ende der Grenzen (Socket, dumps/loads, Bridge-Pfad) ohne Selbstauskuenfte
# gemessen werden koennen. Jede Einzelpruefung wird dort einem der 13
# bindenden Kriterien zugeordnet.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

exec "$PY" -B -P "$HIER/t313b_pruefstand.py" "$REPO" "$TARGET"
