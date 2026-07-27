#!/usr/bin/env bash
# Prueft, dass kein eingefrorener Verifizierer nachtraeglich geaendert wurde.
#
# Erste Zeile JEDES Phasen-Gates. Bricht bei jeder Abweichung ab.
#
# FROZEN liegt unter tests/verify/ und ist damit fuer die Builder-Rolle
# gesperrt (.claude/roles.toml). Ein Builder kann also weder das Skript
# noch dessen Hash aendern -- beides zusammen waere sonst trivial.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FROZEN="$REPO/tests/verify/FROZEN"

if [[ ! -f "$FROZEN" ]]; then
    echo "verify-frozen: $FROZEN fehlt. Kein Verifizierer ist eingefroren." >&2
    echo "Vor dem ersten Gate: tests/verify/freeze.sh ausfuehren (Rolle reviewer)." >&2
    exit 1
fi

fail=0
count=0

while read -r want path; do
    [[ -z "${want:-}" || "$want" == \#* ]] && continue
    count=$((count + 1))
    full="$REPO/$path"
    if [[ ! -f "$full" ]]; then
        echo "verify-frozen: FEHLT   $path" >&2
        fail=1
        continue
    fi
    have="$(sha256sum "$full" | cut -d' ' -f1)"
    if [[ "$have" != "$want" ]]; then
        echo "verify-frozen: GEAENDERT $path" >&2
        echo "               erwartet $want" >&2
        echo "               gefunden $have" >&2
        fail=1
    fi
done < "$FROZEN"

if [[ $count -eq 0 ]]; then
    # Bootstrap: noch kein Verifizierer abgenommen. Legitim, solange auch keiner
    # existiert. Existieren welche und FROZEN ist leer, wurde eingefroren umgangen.
    shopt -s nullglob
    existing=("$REPO"/tests/verify/T-*.sh)
    shopt -u nullglob
    if (( ${#existing[@]} > 0 )); then
        echo "verify-frozen: ${#existing[@]} Verifizierer vorhanden, aber FROZEN ist leer." >&2
        echo "               Nach Abnahme: tests/verify/freeze.sh <task-id>" >&2
        exit 1
    fi
    echo "verify-frozen: noch kein Verifizierer eingefroren (Bootstrap)."
    exit 0
fi

if [[ $fail -ne 0 ]]; then
    echo "" >&2
    echo "Ein eingefrorener Verifizierer wurde veraendert oder entfernt." >&2
    echo "Das Gate schlaegt fehl. Eine Aenderung braucht einen neuen .v-Task" >&2
    echo "mit erneutem Mutationstest (Rolle reviewer)." >&2
    exit 1
fi

echo "verify-frozen: $count Verifizierer unveraendert."
