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
chk "jeder Zyklus erhielt ein configure" \
  "$(json 'if (.cycles|type) == "number" and (.configures_received|type) == "number" and
      .configures_received == .cycles then "ja" else "nein" end')" ja
chk "beide Umgehungen wurden erprobt" \
  "$(json 'if (.configures_reset_props|type) == "number" and
      (.configures_recreate|type) == "number" then "ja" else "nein" end')" ja
chk "verwendete Umgehung ist dokumentiert" \
  "$(json 'if ((.workaround_used // .workaround_recommended)|type) == "string" and
      ((.workaround_used // .workaround_recommended)|length) > 0 then "ja" else "nein" end')" ja

# Der Plan fordert pidstat ausdruecklich; ein Ersatzwert aus results.json zaehlt nicht.
pidstat_bin="$(command -v pidstat 2>/dev/null || true)"
chk "pidstat fuer die eigene 60-s-Messung ist installiert" \
  "$([[ -n "$pidstat_bin" ]] && echo ja || echo nein)" ja
cpu_ok=nein
if [[ -n "$pidstat_bin" ]] && [[ -x "$REPO/spikes/layershell/target/release/spike" ]]; then
  log="$(mktemp)"
  "$REPO/spikes/layershell/target/release/spike" map --color 20A0C0 \
    --marker 2000,600,60,60 >"$log" 2>&1 &
  pid=$!
  sleep 3
  samples="$("$pidstat_bin" -p "$pid" 1 60 2>/dev/null || true)"
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -f "$log"
  measured="$(awk '/Average:/ && $3 ~ /^[0-9.,]+$/ {gsub(",", ".", $8); print $8}' <<<"$samples" | tail -1)"
  if [[ "$measured" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    cpu_ok="$(awk -v n="$measured" 'BEGIN {print (n < 1.0) ? "ja" : "nein"}')"
  fi
fi
chk "selbst gemessene Idle-CPU liegt unter 1,0 %" "$cpu_ok" ja

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
  if timeout --foreground --signal=TERM --kill-after=5s 90s \
       python3 "$tmp/run_fullscreen_test.py" >"$tmp/fullscreen.log" 2>&1; then
    shot_ok="$(jq -r 'if .fullscreen_covers_probe == true and
      .fullscreen_bg_recognised == true and .fullscreen_pixel_match == true and
      .baseline_false_positive == false and .cleanup_ok == true
      then "ja" else "nein" end' "$tmp/evidence/fullscreen_test.json" 2>/dev/null)"
  else
    shot_reason="Live-Pruefstand konnte nicht erfolgreich laufen"
  fi
else
  [[ -n "$shot_reason" ]] || shot_reason="keine Wayland-Sitzung"
fi
chk "eigener Screenshot ueber Vollbild enthaelt die Markerfarbe${shot_reason:+ ($shot_reason)}" \
  "$shot_ok" ja
exit $fail
