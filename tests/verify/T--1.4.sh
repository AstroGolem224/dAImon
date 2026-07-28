#!/usr/bin/env bash
# Verifizierer fuer T−1.4: Prompt-Erkennung stammt aus DBus, nicht aus Selbstauskunft.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/portal/results.json"
RUNS="$REPO/spikes/portal/runs"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
json() { jq -r "$1 // \"nein\"" "$RESULT" 2>/dev/null; }

echo "T−1.4 — Portal-Persistenz"
if jq -e . "$RESULT" >/dev/null 2>&1; then valid=ja; else valid=nein; fi
chk "results.json ist gueltiges JSON" "$valid" ja
chk "restart_prompted ist false" \
  "$(json 'if .restart_prompted == false then "ja" else "nein" end')" ja
chk "Verhalten eines verfaelschten Tokens ist dokumentiert" \
  "$(json 'if (.invalid_token_behaviour|type) == "string" and
      (.invalid_token_behaviour|length) > 0 then "ja" else "nein" end')" ja

trace="$RUNS/restart.dbus.log"
trace_ok=nein
if [[ -s "$trace" ]]; then
  derived="$(python3 "$REPO/spikes/portal/analyze_trace.py" "$trace" 2>/dev/null || true)"
  if jq -e '.screen_cast_start_calls|length > 0' <<<"$derived" >/dev/null 2>&1; then trace_ok=ja; fi
  prompted="$(jq -r 'if (.prompted_from_dbus|type) == "boolean"
    then (.prompted_from_dbus|tostring) else "unbekannt" end' \
    <<<"$derived" 2>/dev/null)"
else
  prompted=unbekannt
fi
chk "DBus-Mitschnitt enthaelt ScreenCast.Start und Request.Response" "$trace_ok" ja
chk "restart_prompted=false ist aus dem DBus-Mitschnitt ableitbar" \
  "$([[ "$prompted" == false ]] && echo ja || echo nein)" ja

# Zwei neue, getrennte Clients laufen gegen eine Kopie des Tokens in /tmp.
# So prueft der Verifizierer selbst, ohne Spike-Artefakte zu veraendern.
live=nein
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp "$REPO/spikes/portal/portal_probe.py" "$tmp/portal_probe.py"
cp "$REPO/spikes/portal/analyze_trace.py" "$tmp/analyze_trace.py"
cp "$REPO/spikes/portal/token.json" "$tmp/token.json" 2>/dev/null || true
mkdir -p "$tmp/runs"
if [[ -f "$tmp/token.json" ]] && [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  first_rc=1
  second_rc=1
  timeout --foreground --signal=TERM --kill-after=5s 130s \
    python3 "$tmp/portal_probe.py" --case restart --trace "$tmp/runs/first.dbus.log" \
    >"$tmp/first.log" 2>&1
  first_rc=$?
  timeout --foreground --signal=TERM --kill-after=5s 130s \
    python3 "$tmp/portal_probe.py" --case restart --trace "$tmp/runs/second.dbus.log" \
    >"$tmp/second.log" 2>&1
  second_rc=$?
  second="$(python3 "$tmp/analyze_trace.py" "$tmp/runs/second.dbus.log" 2>/dev/null || true)"
  second_prompted="$(jq -r 'if (.prompted_from_dbus|type) == "boolean"
    then (.prompted_from_dbus|tostring) else "unbekannt" end' \
    <<<"$second" 2>/dev/null)"
  if [[ $first_rc -eq 0 ]] && [[ $second_rc -eq 0 ]] && [[ "$second_prompted" == false ]]; then live=ja; fi
fi
chk "zwei eigene Clientstarts: beim zweiten kein DBus-erkennbarer Dialog" "$live" ja

reboot="$(json '.reboot_prompted | if type == "boolean" then tostring else . end')"
echo "  INFO Reboot-Verhalten (ohne Schwelle): $reboot"
exit $fail
