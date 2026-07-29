#!/usr/bin/env bash
# Verifizierer fuer T-0.9: Hub, Bus und State.
#
# Der Plan verlangt ausdruecklich, per `ss -lx` und `ss -ltn` am LAUFENDEN
# Prozess zu pruefen, dass er Unix-Sockets haelt und keinen TCP-Socket. Am
# Quelltext waere das leicht zu uebersehen: eine Bibliothek koennte hinter
# unserem Ruecken einen Debug-Port oeffnen. Der Punkt ist nicht Vorsicht,
# sondern dass RestrictAddressFamilies=AF_UNIX in T-0.14 erfuellbar bleibt --
# ein einziger AF_INET macht die Direktive unmoeglich, und dann faellt eine
# ganze Schutzschicht weg, weil jemand einen Endpunkt bequem fand.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.9 — Hub: Bus und State"
for f in daemon state bus; do
  chk "daimon/hub/$f.py existiert" "$([[ -f "$REPO/daimon/hub/$f.py" ]] && echo ja || echo nein)" ja
done

xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && timeout 300 "$PY" -m pytest tests/test_hub.py --tb=no -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
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

# --- am laufenden Prozess -------------------------------------------------
tmp="$(mktemp -d)"; rt="$tmp/rt"
( cd "$REPO" && XDG_RUNTIME_DIR="$tmp" "$PY" -m daimon.hub.daemon --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
for _ in $(seq 1 40); do [[ -S "$rt/state.sock" ]] && break; sleep 0.1; done

chk "Hub laeuft" "$(kill -0 $hub 2>/dev/null && echo ja || echo nein)" ja
chk "state.sock existiert" "$([[ -S "$rt/state.sock" ]] && echo ja || echo nein)" ja
chk "state.sock hat Modus 0600" "$(stat -c '%a' "$rt/state.sock" 2>/dev/null)" 600
chk "hookbridge.sock hat Modus 0600" "$(stat -c '%a' "$rt/hookbridge.sock" 2>/dev/null)" 600

unix_n="$(ss -lxp 2>/dev/null | grep -c "pid=$hub," || true)"
chk "haelt mindestens einen horchenden Unix-Socket" \
  "$([[ "${unix_n:-0}" -ge 1 ]] && echo ja || echo nein)" ja

tcp_n="$(ss -ltnp 2>/dev/null | grep -c "pid=$hub," || true)"
chk "haelt KEINEN horchenden TCP-Socket" "${tcp_n:-0}" 0

# Zustellung ueber den echten Socket, nicht ueber importierten Code.
antwort="$( "$PY" - "$rt" <<'PYEOF'
import json, socket, sys, time
from pathlib import Path
rt = Path(sys.argv[1])
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.connect(str(rt / "hookbridge.sock"))
c.sendall(json.dumps({"v":1,"type":"hook","payload":{
    "hook_event_name":"Notification","session_id":"v1",
    "notification_type":"permission_prompt","message":"probe"}}).encode()+b"\n")
time.sleep(0.4); c.close()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(str(rt / "state.sock"))
d = json.loads(s.makefile("rb").readline()); s.close()
print(json.dumps({"mood": d["mood"], "v": d["v"], "bubble": bool(d["bubble"])}))
PYEOF
)"
chk "Ereignis erreicht den State ueber den Socket" \
  "$(jq -r '.mood' <<<"$antwort" 2>/dev/null)" needs_input
chk "State liefert Schema v2" "$(jq -r '.v' <<<"$antwort" 2>/dev/null)" 2
chk "Bubble ist gesetzt" "$(jq -r '.bubble' <<<"$antwort" 2>/dev/null)" true

kill $hub 2>/dev/null; wait $hub 2>/dev/null
rm -rf "$tmp"
exit $fail
