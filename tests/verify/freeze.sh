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

# >>> lauf-sperre
# Prueflings-Anker: tests/test_freeze_sperre.py
# --- Ein Lauf zur Zeit -------------------------------------------------------
# Am 30.08. fuhren zwei Sitzungen gleichzeitig Verifizierer im selben Baum. Das
# ist nicht bloss langsam, es faelscht in die HARMLOSE Richtung: `meta.sh:62`
# wertet jeden Nicht-Null-Exit eines Fixture-Laufs als „Mutante erkannt".
# Fremdlast laesst einen Mutanten aus dem falschen Grund scheitern, und er wird
# als erkannt verbucht -- ein falsches GRUEN, das keine Auswertung sieht, weil
# sie nur nach „alle erkannt" schaut.
#
# Die Sperre ist maschinenweit und nicht baumweit: umkaempft sind die Units,
# die Sockets und die Karte, nicht die Dateien.
#
# ponytail: gedeckt sind freeze-gegen-freeze (die Sperre) und ein VORHER
# gestarteter blanker Verifiziererlauf (die Vorpruefung). NICHT gedeckt ist ein
# blanker `T-x.sh`, der WAEHREND eines Freeze startet -- genau die Bauform des
# Vorfalls vom 30.08. Die Verifizierer selbst sind eingefroren; eine Sperre
# dort hiesse dreissig Dateien neu einfrieren. Der Aufruestweg ist der
# PreToolUse-Hook `.claude/hooks/role_guard.py`, der jedes Bash-Kommando
# ohnehin schon sieht und die Sperrdatei mitlesen koennte.
SPERRE="${XDG_RUNTIME_DIR:-/tmp}/daimon-verify.lock"

# Anhaengend oeffnen, NICHT abschneidend: `9>` wuerde die Auskunft des
# laufenden Halters loeschen, bevor `flock` ueberhaupt merkt, dass es einen
# gibt -- und die Ablehnung koennte dann nicht sagen, wer blockiert.
exec 9>>"$SPERRE" || {
    echo "freeze: FEHLER — Sperrdatei $SPERRE nicht anlegbar." >&2
    exit 1
}
if ! flock -n 9; then
    echo "freeze: ABGELEHNT — es laeuft bereits ein Verifiziererlauf:" >&2
    sed 's/^/  /' "$SPERRE" >&2 2>/dev/null || true
    echo "freeze: warte, bis er durch ist. Zwei Laeufe messen einander mit." >&2
    exit 1
fi
# Erst jetzt, mit der Sperre in der Hand, die Auskunft setzen. Das Abschneiden
# ueber den Pfad laesst `flock` auf fd 9 unberuehrt.
printf 'PID %s seit %s -- freeze.sh %s (%s)\n' \
    "$$" "$(date '+%F %T')" "${task:-?}" "$REPO" > "$SPERRE"

# Ein blanker Verifiziererlauf haelt diese Sperre nicht. Lief er schon, als wir
# ankamen, ist er hier sichtbar. Das Muster verlangt einen Interpreter
# unmittelbar vor dem Pfad -- ein `cat tests/verify/T-3.14.sh` faellt damit
# nicht auf, ein `bash tests/verify/T-3.14.sh` schon. Der Anker `^` ist noetig,
# weil `pgrep -af` die GANZE Kommandozeile prueft: ohne ihn meldet jeder Prozess
# einen Fremdlauf, der den Befehl bloss als Text mitfuehrt -- etwa ein
# `claude -p`, dessen Auftrag ihn zitiert.
fremde="$(pgrep -af \
    '^([^ ]*/)?(bash|sh|python3?)[^|]* tests/verify/(T-[0-9][^ ]*\.sh|t[0-9]+_pruefstand\.py|meta\.sh)' \
    2>/dev/null | grep -v "^$$ " || true)"
if [[ -n "$fremde" ]]; then
    echo "freeze: ABGELEHNT — es laufen bereits Verifiziererprozesse:" >&2
    echo "$fremde" | sed 's/^/  /' >&2
    echo "freeze: sie messen mit. Erst abwarten, dann einfrieren." >&2
    exit 1
fi
# <<< lauf-sperre

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
