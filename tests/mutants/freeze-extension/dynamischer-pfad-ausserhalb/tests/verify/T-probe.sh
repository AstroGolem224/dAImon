#!/usr/bin/env bash
set -euo pipefail
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name="${FREEZE_HELPER_NAME:-helper_a}"
"$HIER/${name}.py"
