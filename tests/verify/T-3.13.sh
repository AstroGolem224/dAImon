#!/usr/bin/env bash
# T-3.13: Routing, Durchgang 2.
#
# Der eigentliche Pruefstand steht in Python, damit die rohen Egress-Koerper,
# das Ticketbuch und die Hub-Anfragen ohne jq-/grep-Selbstauskuenfte gemessen
# werden koennen. Jede Einzelpruefung wird dort einem der 10 bindenden
# Kriterien zugeordnet.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

exec "$PY" -B -P "$HIER/t313_pruefstand.py" "$REPO" "$TARGET"
