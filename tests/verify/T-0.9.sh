#!/usr/bin/env bash
# Verifizierer fuer T-0.9: Hub, Bus und State.
#
# Die Socket-Inodes werden am LAUFENDEN Prozess mit /proc/<pid>/fd korreliert.
# Eine reine ss-Ausgabe kann die PID wegen PR_SET_DUMPABLE=0 nicht nennen. Am
# Quelltext waere das leicht zu uebersehen: eine Bibliothek koennte hinter
# unserem Ruecken einen Debug-Port oeffnen. Der Punkt ist nicht Vorsicht,
# sondern dass RestrictAddressFamilies=AF_UNIX in T-0.14 erfuellbar bleibt --
# ein einziger AF_INET macht die Direktive unmoeglich, und dann faellt eine
# ganze Schutzschicht weg, weil jemand einen Endpunkt bequem fand.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
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
hub=""; kontrolle=""
aufräumen() {
  [[ -n "$hub" ]] && kill "$hub" 2>/dev/null || true
  [[ -n "$kontrolle" ]] && kill "$kontrolle" 2>/dev/null || true
  [[ -n "$hub" ]] && wait "$hub" 2>/dev/null || true
  [[ -n "$kontrolle" ]] && wait "$kontrolle" 2>/dev/null || true
  rm -rf -- "$tmp"
}
trap aufräumen EXIT INT TERM

# Die Positivkontrolle benutzt denselben Inode-/fd-Abgleich. Ohne sie koennte
# eine kaputte Korrelation sowohl Unix als auch TCP faelschlich leer melden.
probe_dir="$REPO/tests/harness/t09_socket_probe"
kontroll_sock="$tmp/positiv.sock"; kontroll_befund="$tmp/positiv.json"
( env PYTHONPATH="$probe_dir${PYTHONPATH:+:$PYTHONPATH}" \
    DAIMON_T09_SOCKET_BEFUND="$kontroll_befund" \
    DAIMON_T09_POSITIV_SOCKET="$kontroll_sock" \
    "$PY" -c 'import time; time.sleep(300)' ) >"$tmp/positiv.log" 2>&1 &
kontrolle=$!
for _ in $(seq 1 80); do [[ -s "$kontroll_befund" ]] && break; sleep 0.05; done
positiv="$(jq -r --arg p "$kontroll_sock" '[.unix[] | select(.path == $p)] | length' "$kontroll_befund" 2>/dev/null)"
chk "Positivkontrolle korreliert bekannten Unix-Listener mit seinem Prozess-fd" "${positiv:-0}" 1

hub_befund="$tmp/hub-sockets.json"
( cd "$REPO" && env XDG_RUNTIME_DIR="$tmp" \
    PYTHONPATH="$probe_dir${PYTHONPATH:+:$PYTHONPATH}" \
    DAIMON_T09_SOCKET_BEFUND="$hub_befund" \
    DAIMON_T09_TCP_MUTANT="${DAIMON_T09_TCP_MUTANT:-0}" \
    "$PY" -m daimon.hub.daemon --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
for _ in $(seq 1 80); do [[ -S "$rt/state.sock" && -s "$hub_befund" ]] && break; sleep 0.05; done

chk "Hub laeuft" "$(kill -0 $hub 2>/dev/null && echo ja || echo nein)" ja
chk "state.sock existiert" "$([[ -S "$rt/state.sock" ]] && echo ja || echo nein)" ja
chk "state.sock hat Modus 0600" "$(stat -c '%a' "$rt/state.sock" 2>/dev/null)" 600
chk "hookbridge.sock hat Modus 0600" "$(stat -c '%a' "$rt/hookbridge.sock" 2>/dev/null)" 600

owner="$(jq -r '.pid // 0' "$hub_befund" 2>/dev/null)"
chk "Socket-Befund stammt vom wirklichen Hub-Prozess" "${owner:-0}" "$hub"
unix_n="$(jq -r --arg s "$rt/state.sock" --arg h "$rt/hookbridge.sock" \
  '[.unix[] | select(.path == $s or .path == $h)] | length' "$hub_befund" 2>/dev/null)"
chk "haelt beide horchenden Unix-Sockets laut Inode-/fd-Korrelation" "${unix_n:-0}" 2

tcp_n="$(jq -r '.tcp | length' "$hub_befund" 2>/dev/null)"
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

exit $fail
