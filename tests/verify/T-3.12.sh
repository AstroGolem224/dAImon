#!/usr/bin/env bash
# T-3.12: Routing, Durchgang 1.
#
# Der eigentliche Pruefstand steht in Python, damit Unix-Sockets, Prozess-
# Deskriptoren und die rohen Egress-Koerper ohne jq-/grep-Selbstauskuenfte
# gemessen werden koennen. Jede Einzelpruefung wird dort einem der 15
# bindenden Kriterien zugeordnet.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

exec "$PY" -B -P "$HIER/t312_pruefstand.py" "$REPO" "$TARGET"
