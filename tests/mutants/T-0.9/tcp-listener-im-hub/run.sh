#!/usr/bin/env bash
# Diese Mutante muss an der TCP-Eigentumspruefung von T-0.9 scheitern.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
ausgabe="$(mktemp)"
set +e
DAIMON_T09_TCP_MUTANT=1 bash "$REPO/tests/verify/T-0.9.sh" >"$ausgabe" 2>&1
rc=$?
set -e
cat "$ausgabe"
[[ $rc -ne 0 ]] || { echo "Mutante wurde nicht erkannt" >&2; exit 1; }
grep -F "FAIL haelt KEINEN horchenden TCP-Socket" "$ausgabe" >/dev/null || {
  echo "Mutante scheiterte nicht an der TCP-Eigentumspruefung" >&2; exit 1;
}
echo "Mutante tcp-listener-im-hub: an der richtigen TCP-Eigentumspruefung erkannt."
