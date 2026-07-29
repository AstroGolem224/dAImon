#!/usr/bin/env bash
# Verifizierer fuer T-0.6: strukturiertes Logging.
#
# Der Plan verlangt genau das hier: einen Datensatz mit eindeutiger Marke
# schreiben, ihn per `journalctl -o json` ZURUECKLESEN und pruefen, dass
# DAIMON_ACTION UND das vom Journal gesetzte _PID vorhanden sind. Das zweite
# ist der eigentliche Beweis -- _PID setzt journald selbst, es kann also nicht
# aus unserer Nutzlast stammen. Steht es da, ist der Datensatz wirklich durch
# das Journal gelaufen und nicht bloss von uns behauptet.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.6 — Strukturiertes Logging"
chk "Modul existiert" "$([[ -f "$REPO/daimon/common/logging.py" ]] && echo ja || echo nein)" ja

xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && "$PY" -m pytest tests/test_logging.py --tb=no -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
read -r passed failed <<<"$("$PY" -c '
import sys, xml.etree.ElementTree as ET
r = ET.parse(sys.argv[1]).getroot(); s = r if r.tag=="testsuite" else r.find("testsuite")
p=f=0
for c in s.iter("testcase"):
    k={x.tag for x in c}
    if k & {"failure","error"}: f+=1
    elif "skipped" not in k: p+=1
print(p,f)' "$xml")"
rm -f "$xml"
echo "  pytest: $passed passed, $failed failed"
chk "keine fehlgeschlagenen Tests" "$failed" 0

# --- Zurueckleseprobe ------------------------------------------------------
if [[ ! -S /run/systemd/journal/socket ]]; then
  echo "  FAIL journald-Socket fehlt -- Zurueckleseprobe nicht moeglich"
  exit 1
fi
marke="$( "$PY" -c 'import uuid; print(uuid.uuid4().hex)' )"
( cd "$REPO" && "$PY" -c "
from daimon.common.logging import get_logger
lg = get_logger('daimon-verify-t06')
assert lg.nutzt_journal, 'Journal-Socket da, aber Logger nutzt ihn nicht'
lg.info('T-0.6 Verifizierer', DAIMON_ACTION='verify', DAIMON_MARKE='$marke')
" ) || { echo "  FAIL Schreiben ins Journal schlug fehl"; exit 1; }

gefunden=nein; action=""; pid=""
for _ in $(seq 1 20); do
  sleep 0.3
  zeile="$(journalctl --user -o json -n 200 --no-pager 2>/dev/null | grep -F "$marke" | head -1)"
  [[ -n "$zeile" ]] || continue
  gefunden=ja
  action="$(jq -r '.DAIMON_ACTION // empty' <<<"$zeile")"
  pid="$(jq -r '._PID // empty' <<<"$zeile")"
  break
done
chk "Datensatz im Journal wiedergefunden" "$gefunden" ja
chk "DAIMON_ACTION ist vorhanden" "$action" verify
chk "vom Journal gesetztes _PID ist vorhanden" \
  "$([[ -n "$pid" ]] && echo ja || echo nein)" ja
exit $fail
