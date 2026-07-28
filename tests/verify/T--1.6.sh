#!/usr/bin/env bash
# Verifizierer fuer T−1.6: absolute Hook-Aufschlaege mit den Planschwellen.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/hookoverhead/results.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
json() { jq -r "$1 // \"nein\"" "$RESULT" 2>/dev/null; }

echo "T−1.6 — Hook-Overhead"
if jq -e . "$RESULT" >/dev/null 2>&1; then valid=ja; else valid=nein; fi
chk "results.json ist gueltiges JSON" "$valid" ja
chk "mindestens 30 gepaarte Laeufe" \
  "$(json 'if (.n|type) == "number" and .n >= 30 then "ja" else "nein" end')" ja
chk "p50 mit Bridge an ist numerisch" \
  "$(json 'if (.p50_on_ms|type) == "number" then "ja" else "nein" end')" ja
chk "p50 mit Bridge aus ist numerisch" \
  "$(json 'if (.p50_off_ms|type) == "number" then "ja" else "nein" end')" ja
chk "p95 mit Bridge an ist numerisch" \
  "$(json 'if (.p95_on_ms|type) == "number" then "ja" else "nein" end')" ja
chk "p95 mit Bridge aus ist numerisch" \
  "$(json 'if (.p95_off_ms|type) == "number" then "ja" else "nein" end')" ja
chk "p50 der absichtlich langsamen Bridge ist numerisch" \
  "$(json 'if (.p50_slow_ms|type) == "number" then "ja" else "nein" end')" ja
gesund="$(json '.aufschlag_gesund_ms')"
tot="$(json '.aufschlag_tot_ms')"
langsam="$(json '.aufschlag_langsam_ms')"
chk "Aufschlag gesunder Daemon liegt unter 20 ms ($gesund ms)" \
  "$(json 'if (.aufschlag_gesund_ms|type) == "number" and
      .aufschlag_gesund_ms < 20 then "ja" else "nein" end')" ja
chk "Aufschlag toter Daemon liegt unter 20 ms ($tot ms)" \
  "$(json 'if (.aufschlag_tot_ms|type) == "number" and
      .aufschlag_tot_ms < 20 then "ja" else "nein" end')" ja
slow_ok="$(json 'if (.aufschlag_langsam_ms|type) == "number" and
  .aufschlag_langsam_ms < 200 then "ja" else "nein" end')"
chk "Aufschlag langsamer Daemon liegt unter 200 ms ($langsam ms)" "$slow_ok" ja
if [[ "$slow_ok" != ja ]]; then
  echo "  HINWEIS Auflage an T-0.11: haengenden Daemon abkoppeln oder Zeitlimit deutlich senken"
fi
exit $fail
