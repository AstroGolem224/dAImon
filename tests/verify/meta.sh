#!/usr/bin/env bash
# Meta-Verifizierer: prueft, ob ein Verifizierer ueberhaupt fehlschlagen kann.
#
#   tests/verify/meta.sh T-0.8
#
# Zweistufig, weil ein Verifizierer entsteht, BEVOR es eine Implementierung gibt:
#   Stufe 1 (immer):   gegen tests/fixtures/known-good/<task>/  -> muss bestehen
#                      gegen jede tests/mutants/<task>/*/       -> muss scheitern
#   Stufe 2 (spaeter): gegen den echten Code, sobald er existiert
#
# Ein Verifizierer, der eine Mutante besteht, ist selbst defekt.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ $# -eq 1 ]] || { echo "usage: meta.sh <task-id>" >&2; exit 2; }
task="$1"
verifier="$REPO/tests/verify/${task}.sh"
good="$REPO/tests/fixtures/known-good/${task}"
mutdir="$REPO/tests/mutants/${task}"

[[ -x "$verifier" ]] || { echo "meta: $verifier fehlt oder ist nicht ausfuehrbar" >&2; exit 1; }
[[ -d "$good"    ]] || { echo "meta: Gut-Muster $good fehlt" >&2; exit 1; }
[[ -d "$mutdir"  ]] || { echo "meta: Mutantenverzeichnis $mutdir fehlt" >&2; exit 1; }

mapfile -t mutants < <(find "$mutdir" -mindepth 1 -maxdepth 1 -type d | sort)
(( ${#mutants[@]} > 0 )) || { echo "meta: keine Mutanten fuer $task" >&2; exit 1; }

echo "meta[$task]: Gut-Muster ..."
if ! DAIMON_FIXTURE="$good" "$verifier" >/dev/null 2>&1; then
    echo "meta[$task]: FEHLER -- Verifizierer scheitert am Gut-Muster." >&2
    exit 1
fi

rc=0
for m in "${mutants[@]}"; do
    name="$(basename "$m")"
    if DAIMON_FIXTURE="$m" "$verifier" >/dev/null 2>&1; then
        echo "meta[$task]: FEHLER -- Mutante '$name' wurde NICHT erkannt." >&2
        rc=1
    else
        echo "meta[$task]: Mutante '$name' erkannt."
    fi
done

(( rc == 0 )) && echo "meta[$task]: ${#mutants[@]} Mutanten, alle erkannt."
exit $rc
