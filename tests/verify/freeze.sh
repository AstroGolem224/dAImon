#!/usr/bin/env bash
# Friert einen abgenommenen Verifizierer ein. Rolle: reviewer.
#   tests/verify/freeze.sh T-0.8
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FROZEN="$REPO/tests/verify/FROZEN"
DEPS="$REPO/tests/verify/FROZEN.deps"
DEPS_PRUEFER="$REPO/tests/verify/freeze-deps.py"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
bootstrap=0
if [[ "${1:-}" == "--bootstrap" ]]; then bootstrap=1; shift; fi
[[ $# -eq 1 ]] || { echo "usage: freeze.sh [--bootstrap] <task-id>" >&2; exit 2; }
task="$1"; rel="tests/verify/${task}.sh"
[[ -f "$REPO/$rel" ]] || { echo "freeze: $rel fehlt" >&2; exit 1; }
command -v strace >/dev/null 2>&1 || {
    echo "freeze: FEHLER — strace fehlt; ohne Laufzeitspur wird nicht eingefroren." >&2
    exit 1
}

# Erst die geschlossene Menge beweisen. Sonst koennte ein gruener Mutantenlauf
# bereits seine eigentlichen Aussagen aus einem ungeschuetzten Helfer beziehen.
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
if ! "$PY" "$DEPS_PRUEFER" pruefen --repo "$REPO" \
        --deps "$DEPS" "$rel" >"$tmp/erlaubt"; then
    echo "freeze: Abhaengigkeits-Entdeckung fehlgeschlagen, nicht eingefroren" >&2
    exit 1
fi

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

# Der statische Beweis verhindert unaufloesbare Pfade; die Spur belegt, welche
# Dateien Gut- und Echtlauf mitsamt Kindern geoeffnet haben. Produkt- und
# Fixture-Baeume sind dabei bewusst Gegenstand.
# ponytail: Verifizierer-Helfer variieren normalerweise nicht je Mutante; Gut-
# und Echtlauf decken deshalb denselben Framework-Satz ohne die sehr teure
# Verdopplung aller Mutantenlaeufe. Die ausdrueckliche Obergrenze: Ein Helfer,
# den NUR ein Mutanten-Codepfad oeffnet, bleibt in dieser Spur unsichtbar. Wenn
# Mutanten eines Verifizierers eigene Helfer einfuehren, ist der Upgrade-Pfad,
# fuer genau diesen Verifizierer wieder alle Mutanten unter strace auszufuehren.
spur_lauf() {
        local name="$1" fixture="$2" erwartet="$3" log="$tmp/spur-$1.log" rc
        set +e
        if [[ -n "$fixture" ]]; then
            strace -f -qq -s 4096 -e trace=openat -o "$log" \
                env DAIMON_FIXTURE="$fixture" "$REPO/$rel" >/dev/null 2>&1
        else
            strace -f -qq -s 4096 -e trace=openat -o "$log" \
                env -u DAIMON_FIXTURE "$REPO/$rel" >/dev/null 2>&1
        fi
        rc=$?
        set -e
        "$PY" "$DEPS_PRUEFER" spur --repo "$REPO" \
            --erlaubt "$tmp/erlaubt" --log "$log" || return 1
        if [[ "$erwartet" == gruen && $rc -ne 0 ]]; then
            echo "freeze: Laufzeitspur '$name' scheiterte am Pruefling (Exit $rc)" >&2
            return 1
        fi
        if [[ "$erwartet" == rot && $rc -eq 0 ]]; then
            echo "freeze: Laufzeitspur '$name' erkannte die Mutante nicht" >&2
            return 1
        fi
        echo "freeze: Laufzeitspur '$name' ohne undeklarierte Helfer."
}
spur_lauf gut "$REPO/tests/fixtures/known-good/$task" gruen || exit 1
spur_lauf echt "" gruen || exit 1

touch "$FROZEN"
if grep -q " $rel\$" "$FROZEN"; then
    echo "freeze: $rel ist bereits eingefroren. Aenderung braucht einen neuen .v-Task." >&2
    exit 1
fi
while read -r dep; do
    [[ -n "$dep" && -f "$REPO/$dep" ]] || continue
    grep -q " $dep\$" "$FROZEN" && continue
    printf '%s %s\n' "$(sha256sum "$REPO/$dep" | cut -d' ' -f1)" "$dep" >> "$FROZEN"
    [[ "$dep" == "$rel" ]] || echo "freeze: $dep mit eingefroren (Abhaengigkeit von $rel)."
done < "$tmp/erlaubt"
sort -k2 -o "$FROZEN" "$FROZEN"
echo "freeze: $rel eingefroren."
