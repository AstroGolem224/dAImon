#!/usr/bin/env bash
# Verifizierer fuer T−1.6: relative Hook-Latenzen mit den Planschwellen.
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
chk "p95 an liegt hoechstens 5 % ueber p95 aus" \
  "$(json 'if (.p95_on_ms|type) == "number" and (.p95_off_ms|type) == "number" and
      .p95_on_ms <= (.p95_off_ms * 1.05) then "ja" else "nein" end')" ja
chk "p50 langsam liegt hoechstens 10 % ueber p50 aus" \
  "$(json 'if (.p50_slow_ms|type) == "number" and (.p50_off_ms|type) == "number" and
      .p50_slow_ms <= (.p50_off_ms * 1.10) then "ja" else "nein" end')" ja
exit $fail
