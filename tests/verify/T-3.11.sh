#!/usr/bin/env bash
# T-3.11: Egress-Broker und Mind-Unit.
#
# Der eigentliche Pruefstand steht in Python, damit rohe JSON-Bytes, Unix-
# Sockets, Prozessspeicher und die lokale TLS-Attrappe ohne jq-/grep-
# Selbstauskuenfte gemessen werden koennen. Jede Einzelpruefung wird dort
# einem der 17 bindenden Kriterien zugeordnet.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

exec "$PY" -B -P "$HIER/t311_pruefstand.py" "$REPO" "$TARGET"
