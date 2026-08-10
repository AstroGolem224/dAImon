#!/usr/bin/env bash
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${PYTHON:-python3}" - "$HIER" <<'PY'
import subprocess
import sys

subprocess.run([sys.argv[1] + "/" + "helper_a.py"], check=True)
PY
