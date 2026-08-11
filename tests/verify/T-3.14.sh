#!/usr/bin/env bash
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../.." && pwd)"
PRUEFLING="${DAIMON_FIXTURE:-$REPO}"
if [[ -x "$PRUEFLING/.venv/bin/python" ]]; then
    PYTHON="$PRUEFLING/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

exec "$PYTHON" "$HIER/t314_pruefstand.py" "$PRUEFLING"
