#!/usr/bin/env bash
# Mutantenpruefer fuer die Helfer-Hash-Sperre.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SZENARIO="${DAIMON_FIXTURE:-}"
[[ -n "$SZENARIO" ]] || { echo "freeze-extension: DAIMON_FIXTURE fehlt" >&2; exit 2; }
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
( cd "$SZENARIO" && sha256sum -c tests/verify/FROZEN )
echo "freeze-extension: Hashvergleich gueltig; jetzt Abhaengigkeits-Entdeckung."
"$PY" "$REPO/tests/verify/freeze-deps.py" pruefen --repo "$SZENARIO" \
  --deps "$SZENARIO/tests/verify/FROZEN.deps" \
  --manifest "$SZENARIO/tests/verify/FROZEN" tests/verify/T-probe.sh >/dev/null
echo "freeze-extension: Abhaengigkeiten geschlossen und Hashvergleich gueltig."
