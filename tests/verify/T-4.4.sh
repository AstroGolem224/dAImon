#!/usr/bin/env bash
# T-4.4.v — Verifizierer fuer die Policy-Engine.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../.." && pwd)"
PRUEFLING="${DAIMON_FIXTURE:-$REPO}"
if [[ -x "$PRUEFLING/.venv/bin/python" ]]; then
    PYTHON="$PRUEFLING/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

exec "$PYTHON" "$HIER/t44_pruefstand.py" "$PRUEFLING"
