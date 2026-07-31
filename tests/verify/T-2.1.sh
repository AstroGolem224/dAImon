#!/usr/bin/env bash
# Verifizierer fuer T-2.1: Vollstaendige Zustandsanimationen.
#
# Der Plan sagt an dieser Stelle etwas, das man sich merken sollte:
#
#     Ein unterschiedlicher `sprite`-Bezeichner im Diagnose-Socket beweist
#     nichts -- identische Sprites bestuenden ihn.
#
# Deshalb vergleicht dieser Verifizierer die gerenderten PIXEL. Acht Moods,
# 28 Paare, und jedes einzelne muss sich unterscheiden. Die Probe liegt in
# `tests/harness/moodprobe.py`.
#
# Die Schwelle wird nicht geraten: zwei Aufnahmen DESSELBEN Moods liefern das
# Rauschen, und der geringste Abstand zwischen zwei verschiedenen Moods muss
# deutlich darueber liegen. Eine Schwelle ohne gemessenes Rauschen daneben
# waere genau die Sorte Zahl, die spaeter angepasst statt ernst genommen wird.
#
# Ein DAIMON_FIXTURE zeigt auf einen Baum mit einem `face/`-Crate; der wird in
# ein temporaeres Zielverzeichnis gebaut, damit keine kompilierten Targets in
# tests/fixtures und tests/mutants liegen.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"
SYSPY="/usr/bin/python3"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
num_gt() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 > $2 )); then echo ja; else echo nein; fi; }

echo "T-2.1 — Vollstaendige Zustandsanimationen"
chk "face-Crate vorhanden" "$([[ -f "$TARGET/face/Cargo.toml" ]] && echo ja || echo nein)" ja
[[ -f "$TARGET/face/Cargo.toml" ]] || exit 1

# --- Bauen --------------------------------------------------------------------
BAUDIR=""
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then
  BAUDIR="$(mktemp -d)"
  ( cd "$TARGET/face" && CARGO_TARGET_DIR="$BAUDIR" timeout 900 cargo build -p face ) >/dev/null 2>&1
  bau_rc=$?
  BIN="$BAUDIR/debug/daimon-face"
else
  ( cd "$TARGET/face" && timeout 900 cargo build -p face ) >/dev/null 2>&1
  bau_rc=$?
fi
chk "cargo build laeuft durch" "$bau_rc" 0
chk "Binary vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja

ct_out="$(cd "$TARGET/face" && ${BAUDIR:+env CARGO_TARGET_DIR="$BAUDIR"} timeout 900 cargo test -p face 2>&1)"
ct_rc=$?
read -r ct_passed ct_failed <<<"$(awk '/^test result:/ {p+=$4; f+=$6} END {print p+0, f+0}' <<<"$ct_out")"
echo "  cargo test: $ct_passed passed, $ct_failed failed"
chk "cargo test laeuft durch" "$ct_rc" 0
chk "keine fehlgeschlagenen Rust-Tests" "$ct_failed" 0
chk "es liefen ueberhaupt Tests (Positivkontrolle)" "$(num_gt "$ct_passed" 0)" ja

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
chk "Mood-Probe vorhanden" \
  "$([[ -f "$REPO/tests/harness/moodprobe.py" ]] && echo ja || echo nein)" ja
chk "spectacle vorhanden" "$(command -v spectacle >/dev/null && echo ja || echo nein)" ja

if [[ -z "${WAYLAND_DISPLAY:-}" || ! -x "$BIN" ]]; then
  echo "  FAIL Live-Pruefungen uebersprungen"
  exit 1
fi

probe="$(timeout 900 "$SYSPY" "$REPO/tests/harness/moodprobe.py" \
  "$tmp" "$BIN" "$MANIFEST" 2>"$tmp/probe.err")"
probe_rc=$?
chk "Mood-Probe lief durch" "$probe_rc" 0
grep '^#' <<<"$probe" | sed 's/^#/ /'
[[ "$probe_rc" -eq 0 ]] || sed 's/^/    | /' "$tmp/probe.err" | head -10

wert() { grep -m1 "^$1=" <<<"$probe" | cut -d= -f2; }

chk "Overlay startet" "$(wert overlay_startet)" ja
chk "alle acht Moods werden angenommen" "$(wert alle_acht_moods_angenommen)" ja
# Erst die Positivkontrolle des Vergleichs, dann die Aussage, die auf ihr ruht.
chk "zwei Aufnahmen desselben Moods sind gleich (Positivkontrolle)" \
  "$(wert rauschen_ist_klein)" ja
chk "alle 28 Mood-Paare sind unterscheidbar" "$(wert alle_paare_unterscheidbar)" ja
chk "der Abstand liegt deutlich ueber dem Rauschen" \
  "$(wert abstand_deutlich_ueber_dem_rauschen)" ja
# "ohne Fehler" heisst: der Prozess lebt noch, nachdem alle acht durch sind.
chk "der Prozess lebt nach allen acht Moods" "$(wert prozess_lebt_nach_allen_moods)" ja

# --- Die Idle-CPU-Zusage haelt weiter -----------------------------------------
# Weiche Uebergaenge brauchen ein Frame-Callback. Genau das war der Grund,
# warum `render.rs` in T-1.5 NICHT gebaut wurde: ein Callback, das nach dem
# Uebergang armiert bleibt, macht aus 0,000 % einen Dauerverbrauch. Diese
# Pruefung ist die Gegenprobe, und sie gehoert hierher und nicht nur in
# T-1.5.sh -- wer T-2.1 aendert, faehrt diesen Verifizierer.
if [[ "$TARGET" == "$REPO" ]]; then
  timeout 400 bash "$REPO/tests/verify/T-1.5.sh" >"$tmp/t15.log" 2>&1
  t15_rc=$?
  echo " $(grep -oE 'gemessene Idle-CPU: [0-9.]+ %[^(]*' "$tmp/t15.log" | head -1)"
  chk "T-1.5 (Idle-CPU) haelt nach den Uebergaengen weiterhin" "$t15_rc" 0
else
  echo "  INFO Fixture-Lauf: die T-1.5-Gegenprobe laeuft nur gegen das echte Repo"
fi

exit $fail
