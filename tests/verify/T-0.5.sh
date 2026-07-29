#!/usr/bin/env bash
# Verifizierer fuer T-0.5: Konfiguration.
# Der Plan verlangt ausdruecklich, den Modus des Zustandsverzeichnisses per
# `stat` zu pruefen -- also am Dateisystem, nicht an einer Zusicherung im Code.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.5 — Konfiguration"
chk "Modul existiert" "$([[ -f "$REPO/daimon/common/config.py" ]] && echo ja || echo nein)" ja

xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && "$PY" -m pytest tests/test_config.py --tb=no -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
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
chk "es laufen Tests" "$([[ $passed -gt 0 ]] && echo ja || echo nein)" ja

# --- eigene Probe: Modus am Dateisystem, nicht im Code ---------------------
tmp="$(mktemp -d)"
mkdir -p "$tmp/config" "$tmp/state" "$tmp/run"
( cd "$REPO" && XDG_CONFIG_HOME="$tmp/config" XDG_STATE_HOME="$tmp/state" \
  XDG_RUNTIME_DIR="$tmp/run" "$PY" -c 'from daimon.common import config; config.load()' ) >/dev/null 2>&1
chk "state-Verzeichnis hat Modus 700 (per stat)" "$(stat -c '%a' "$tmp/state/daimon" 2>/dev/null)" 700
chk "runtime-Verzeichnis hat Modus 700 (per stat)" "$(stat -c '%a' "$tmp/run/daimon" 2>/dev/null)" 700

# Ein zu offenes Verzeichnis aus einem frueheren Lauf muss korrigiert werden.
chmod 755 "$tmp/state/daimon"
( cd "$REPO" && XDG_CONFIG_HOME="$tmp/config" XDG_STATE_HOME="$tmp/state" \
  XDG_RUNTIME_DIR="$tmp/run" "$PY" -c 'from daimon.common import config; config.load()' ) >/dev/null 2>&1
chk "zu offenes state-Verzeichnis wird korrigiert" "$(stat -c '%a' "$tmp/state/daimon")" 700
rm -rf "$tmp"
exit $fail
