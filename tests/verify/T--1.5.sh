#!/usr/bin/env bash
# Verifizierer fuer T−1.5: jede behauptete Session braucht echte Ereignisse.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/mood/results.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
json() { jq -r "$1 // \"nein\"" "$RESULT" 2>/dev/null; }

echo "T−1.5 — Mood-Mapping"
if jq -e . "$RESULT" >/dev/null 2>&1; then valid=ja; else valid=nein; fi
chk "results.json ist gueltiges JSON" "$valid" ja
chk "mindestens fuenf Sessions" \
  "$(json 'if (.sessions|type) == "number" and .sessions >= 5 then "ja" else "nein" end')" ja
chk "Sessionliste und sessions-Zaehler stimmen ueberein" \
  "$(json '(.session_events // .sitzungen) as $s |
      if ($s|type) == "array" and (.sessions|type) == "number" and
      ($s|length) == .sessions then "ja" else "nein" end')" ja
chk "jede Session besitzt eine nichtleere Ereignisliste" \
  "$(json '(.session_events // .sitzungen) as $s |
      if ($s|type) == "array" and ($s|length) > 0 and
      all($s[]; ((.events|type) == "array" and (.events|length) > 0) or
                 ((.abfolge|type) == "array" and (.abfolge|length) > 0))
      then "ja" else "nein" end')" ja
chk "Gesamtzahl der Ereignisse ist numerisch und positiv" \
  "$(json 'if (.events_total|type) == "number" and .events_total > 0 then "ja" else "nein" end')" ja
chk "mismatches ist eine Liste" \
  "$(json 'if (.mismatches|type) == "array" then "ja" else "nein" end')" ja
chk "Empfehlung ist vorhanden" \
  "$(json 'if (.recommendation|type) == "string" and
      (.recommendation|length) > 0 then "ja" else "nein" end')" ja
echo "  INFO mismatches (ohne Schwelle):"
jq -c '.mismatches[]?' "$RESULT" 2>/dev/null | sed 's/^/       /'
exit $fail
