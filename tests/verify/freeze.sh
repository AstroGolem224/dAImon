#!/usr/bin/env bash
# Friert einen abgenommenen Verifizierer ein. Rolle: reviewer.
#   tests/verify/freeze.sh T-0.8
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FROZEN="$REPO/tests/verify/FROZEN"
bootstrap=0
if [[ "${1:-}" == "--bootstrap" ]]; then bootstrap=1; shift; fi
[[ $# -eq 1 ]] || { echo "usage: freeze.sh [--bootstrap] <task-id>" >&2; exit 2; }
task="$1"; rel="tests/verify/${task}.sh"
[[ -f "$REPO/$rel" ]] || { echo "freeze: $rel fehlt" >&2; exit 1; }

if (( bootstrap )); then
    # Nur fuer T-0.0: der Verifizierer prueft den Durchsetzungsmechanismus selbst
    # gegen das echte Repo. Ein "Gut-Muster" dafuer waere Theater -- er enthaelt
    # seinen eigenen Manipulationstest (Gruppe 6). Jede andere Verwendung von
    # --bootstrap umgeht das Regime und ist ein Fehler.
    [[ "$task" == "T-0.0" ]] || { echo "freeze: --bootstrap ist nur fuer T-0.0 zulaessig" >&2; exit 1; }
    echo "freeze: Bootstrap-Ausnahme fuer T-0.0, kein Mutationstest."
    "$REPO/$rel" >/dev/null || { echo "freeze: T-0.0.sh selbst schlaegt fehl" >&2; exit 1; }
else
    "$REPO/tests/verify/meta.sh" "$task" || { echo "freeze: Mutationstest fehlgeschlagen, nicht eingefroren" >&2; exit 1; }
fi

touch "$FROZEN"
if grep -q " $rel\$" "$FROZEN"; then
    echo "freeze: $rel ist bereits eingefroren. Aenderung braucht einen neuen .v-Task." >&2
    exit 1
fi
printf '%s %s\n' "$(sha256sum "$REPO/$rel" | cut -d' ' -f1)" "$rel" >> "$FROZEN"
sort -k2 -o "$FROZEN" "$FROZEN"
echo "freeze: $rel eingefroren."
