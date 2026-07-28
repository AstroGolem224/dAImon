#!/usr/bin/env bash
# Verifizierer fuer T−1.3: CPU und Vollbildsichtbarkeit werden live gemessen.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/layershell/results.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
json() { jq -r "$1 // \"nein\"" "$RESULT" 2>/dev/null; }

echo "T−1.3 — layer-shell"
if jq -e . "$RESULT" >/dev/null 2>&1; then valid=ja; else valid=nein; fi
chk "results.json ist gueltiges JSON" "$valid" ja
chk "mindestens 20 Ein-/Ausblende-Zyklen" \
  "$(json 'if (.cycles|type) == "number" and .cycles >= 20 then "ja" else "nein" end')" ja
# Der Plan verlangt ein einzelnes Feld configures_received. Der Spike hat
# stattdessen die drei Pfade GETRENNT gemessen -- also mehr, nicht weniger.
# Geprueft wird deshalb die Substanz, und zwar strenger als das Original:
# mit der empfohlenen Umgehung muessen ALLE Zyklen ein configure liefern, UND
# der Fehler muss ohne sie reproduziert sein. Ein Spike, der den Bug gar nicht
# ausgeloest haette, koennte die Umgehung nicht belegen.
chk "jeder Zyklus erhielt ein configure (empfohlene Umgehung)" \
  "$(json 'if (.cycles|type) == "number" and
      ((.configures_received // .configures_reset_props)|type) == "number" and
      (.configures_received // .configures_reset_props) == .cycles
      then "ja" else "nein" end')" ja
chk "KDE-Bug 503121 ohne Umgehung reproduziert" \
  "$(json 'if (.configures_null_buffer|type) == "number" and
      .configures_null_buffer < .cycles then "ja" else "nein" end')" ja
chk "beide Umgehungen wurden erprobt" \
  "$(json 'if (.configures_reset_props|type) == "number" and
      (.configures_recreate|type) == "number" then "ja" else "nein" end')" ja
chk "verwendete Umgehung ist dokumentiert" \
  "$(json 'if ((.workaround_used // .workaround_recommended)|type) == "string" and
      ((.workaround_used // .workaround_recommended)|length) > 0 then "ja" else "nein" end')" ja

# Eigene Messung direkt aus /proc/<pid>/stat: utime + stime sind Feld 14/15.
# pidstat ist nur ein Rueckfallweg, falls /proc fuer den Prozess nicht lesbar ist.
cpu_ok=nein
cpu_reason=""
measured=""
spike="$REPO/spikes/layershell/target/release/spike"
if [[ -x "$spike" ]] && [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  cpu_tmp="$(mktemp -d)"
  SPIKE_MAX_SECS=15 "$spike" map --color 20A0C0 \
    --marker 2000,600,60,60 >"$cpu_tmp/overlay.log" 2>&1 &
  pid=$!
  sleep 2
  clk_tck="$(getconf CLK_TCK 2>/dev/null || true)"
  read_ticks() {
    local stat rest
    stat="$(<"/proc/$pid/stat")" || return 1
    rest="${stat##*) }"
    read -r -a fields <<<"$rest"
    [[ "${#fields[@]}" -ge 13 ]] || return 1
    printf '%s\n' "$((fields[11] + fields[12]))"
  }
  ticks_a="$(read_ticks 2>/dev/null || true)"
  time_a="$(date +%s.%N)"
  sleep 10
  ticks_b="$(read_ticks 2>/dev/null || true)"
  time_b="$(date +%s.%N)"
  if [[ "$clk_tck" =~ ^[0-9]+$ ]] && [[ "$ticks_a" =~ ^[0-9]+$ ]] &&
     [[ "$ticks_b" =~ ^[0-9]+$ ]]; then
    measured="$(awk -v a="$ticks_a" -v b="$ticks_b" -v hz="$clk_tck" \
      -v ta="$time_a" -v tb="$time_b" \
      'BEGIN {dt=tb-ta; if (dt > 0) printf "%.6f", ((b-a)/hz)/dt*100}')"
  else
    pidstat_bin="$(command -v pidstat 2>/dev/null || true)"
    if [[ -n "$pidstat_bin" ]] && kill -0 "$pid" 2>/dev/null; then
      samples="$("$pidstat_bin" -p "$pid" 1 1 2>/dev/null || true)"
      measured="$(awk '/Average:/ && $3 ~ /^[0-9.,]+$/ {
        gsub(",", ".", $8); print $8}' <<<"$samples" | tail -1)"
      cpu_reason="direkte /proc-Messung fehlgeschlagen; pidstat-Rueckfallweg benutzt"
    else
      cpu_reason="utime/stime aus /proc/$pid/stat konnten nicht zweimal gelesen werden"
    fi
  fi
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -rf "$cpu_tmp"
  if [[ "$measured" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    cpu_ok="$(awk -v n="$measured" 'BEGIN {print (n < 1.0) ? "ja" : "nein"}')"
  fi
else
  if [[ ! -x "$spike" ]]; then
    cpu_reason="Overlay-Binaer fehlt; spikes/layershell/target/release/spike muss gebaut sein"
  else
    cpu_reason="keine Wayland-Sitzung; das Overlay muss in einer laufenden Wayland-Sitzung starten"
  fi
fi
chk "selbst aus Prozess-Takten gemessene Idle-CPU liegt unter 1,0 %${measured:+ ($measured %)}${cpu_reason:+ ($cpu_reason)}" \
  "$cpu_ok" ja

# Der vorhandene Pruefstand wird in /tmp ausgefuehrt, damit seine Bilder keine
# alten Belege ueberschreiben und die Pixelprobe wirklich aus diesem Lauf stammt.
shot_ok=nein
shot_reason=""
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for cmd in spectacle konsole python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then shot_reason="Werkzeug $cmd fehlt"; fi
done
if [[ -z "$shot_reason" ]] && [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  cp "$REPO/spikes/layershell/run_fullscreen_test.py" "$tmp/run_fullscreen_test.py"
  ln -s "$REPO/spikes/layershell/target" "$tmp/target"
  if SPIKE_MAX_SECS=15 timeout --foreground --signal=TERM --kill-after=5s 90s \
       python3 "$tmp/run_fullscreen_test.py" >"$tmp/fullscreen.log" 2>&1; then
    shot_ok="$(jq -r 'if .fullscreen_covers_probe == true and
      .fullscreen_bg_recognised == true and .fullscreen_pixel_match == true and
      .baseline_false_positive == false and .cleanup_ok == true
      then "ja" else "nein" end' "$tmp/evidence/fullscreen_test.json" 2>/dev/null)"
    if [[ "$shot_ok" != ja ]]; then
      shot_reason="$(jq -r '[
        if .fullscreen_covers_probe != true then "Vollbildfenster am Messpunkt nicht belegt" else empty end,
        if .fullscreen_bg_recognised != true then "Vollbildfarbe nicht erkannt" else empty end,
        if .fullscreen_pixel_match != true then "Markerfarbe ueber Vollbild nicht gefunden" else empty end,
        if .baseline_false_positive != false then "Negativkontrolle nicht sauber" else empty end,
        if .cleanup_ok != true then "Aufraeumen nicht belegt" else empty end
      ] | join("; ")' "$tmp/evidence/fullscreen_test.json" 2>/dev/null)"
      [[ -n "$shot_reason" ]] ||
        shot_reason="Pruefstand lieferte keine vollstaendigen Pixelprobendaten"
    fi
  else
    shot_reason="Live-Pruefstand mit Vollbildfenster und Overlay konnte nicht erfolgreich laufen"
  fi
else
  [[ -n "$shot_reason" ]] || shot_reason="keine Wayland-Sitzung; Vollbildfenster und Overlay muessten dort live laufen"
fi
chk "eigener Screenshot ueber Vollbild enthaelt die Markerfarbe${shot_reason:+ ($shot_reason)}" \
  "$shot_ok" ja
exit $fail
