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

# T-1.1.v2: Die Harness friert MIT ein.
#
# Bis hierher deckte FROZEN nur tests/verify/*.sh ab. T-1.1.sh und T-2.1.sh
# delegieren die eigentliche Messung aber an tests/harness/*.py -- die
# Pixelprobe, das Vollbildfenster, die Mood-Probe. Wer dort die Toleranz
# hochdreht oder eine Pruefung ausbaut, weicht einen eingefrorenen Verifizierer
# auf, ohne dass verify-frozen etwas merkt: der Hash der .sh bleibt gleich.
#
# Die Abhaengigkeiten werden aus dem Skript GELESEN, nicht gepflegt. Eine
# Liste, die von Hand nachgezogen werden muss, ist beim naechsten neuen
# Harness-Modul veraltet -- und veraltet heisst hier: ungeschuetzt.
while read -r dep; do
    [[ -n "$dep" && -f "$REPO/$dep" ]] || continue
    grep -q " $dep\$" "$FROZEN" && continue
    printf '%s %s\n' "$(sha256sum "$REPO/$dep" | cut -d' ' -f1)" "$dep" >> "$FROZEN"
    echo "freeze: $dep mit eingefroren (Harness von $rel)."
done < <(grep -oE 'tests/harness/[A-Za-z0-9_./-]+' "$REPO/$rel" | sort -u)
sort -k2 -o "$FROZEN" "$FROZEN"
echo "freeze: $rel eingefroren."
