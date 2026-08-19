#!/usr/bin/env bash
# T-4.8.v — Verifizierer fuer den Undo-Broker mit Verifikation.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../.." && pwd)"
PRUEFLING="${DAIMON_FIXTURE:-$REPO}"
if [[ -x "$PRUEFLING/.venv/bin/python" ]]; then
    PYTHON="$PRUEFLING/.venv/bin/python"
elif [[ -x "$REPO/.venv/bin/python" ]]; then
    PYTHON="$REPO/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

exec "$PYTHON" "$HIER/t48_pruefstand.py" "$PRUEFLING"
