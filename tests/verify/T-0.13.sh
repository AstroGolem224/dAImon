#!/usr/bin/env bash
# Verifizierer fuer T-0.13: Diagnose-Endpunkt.
# Der Plan verlangt: Last erzeugen, /diag lesen, pruefen dass sich Zaehler
# bewegt haben UND dass die gemeldete Zahl verworfener Ereignisse zur
# erzeugten Last passt. Das zweite ist der eigentliche Test -- ein Endpunkt,
# der irgendeine Zahl liefert, ist wertlos; er muss die RICHTIGE liefern.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.13 — Diagnose-Endpunkt"
chk "Modul existiert" "$([[ -f "$REPO/daimon/hub/diag.py" ]] && echo ja || echo nein)" ja

xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && timeout 200 "$PY" -m pytest tests/test_focus_diag.py --tb=no \
    -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
read -r passed failed <<<"$("$PY" -c '
import sys, xml.etree.ElementTree as ET
r=ET.parse(sys.argv[1]).getroot(); s=r if r.tag=="testsuite" else r.find("testsuite")
p=f=0
for c in s.iter("testcase"):
    k={x.tag for x in c}
    if k & {"failure","error"}: f+=1
    elif "skipped" not in k: p+=1
print(p,f)' "$xml")"
rm -f "$xml"
echo "  pytest: $passed passed, $failed failed"
chk "keine fehlgeschlagenen Tests" "$failed" 0

# --- Last erzeugen und gegenrechnen ---------------------------------------
tmp="$(mktemp -d)"; rt="$tmp/rt"
( cd "$REPO" && XDG_RUNTIME_DIR="$tmp" "$PY" -m daimon.hub.daemon --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
for _ in $(seq 1 40); do [[ -S "$rt/diag.sock" ]] && break; sleep 0.1; done
chk "diag.sock existiert" "$([[ -S "$rt/diag.sock" ]] && echo ja || echo nein)" ja
chk "diag.sock hat Modus 0600" "$(stat -c '%a' "$rt/diag.sock" 2>/dev/null)" 600

GUT=12; SCHLECHT=5
ergebnis="$( "$PY" - "$rt" "$GUT" "$SCHLECHT" <<'PYEOF'
import json, socket, sys, time
from pathlib import Path
rt, gut, schlecht = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])

def sende(payload):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / "hookbridge.sock"))
    c.sendall(json.dumps({"v": 1, "type": "hook", "payload": payload}).encode() + b"\n")
    time.sleep(0.05); c.close()

for i in range(gut):
    sende({"hook_event_name": "PreToolUse", "session_id": f"s{i%3}", "tool_name": "Read"})
for i in range(schlecht):
    sende({"hook_event_name": "GibtEsNichtNr%d" % i, "session_id": "s0"})
time.sleep(0.4)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(str(rt / "diag.sock"))
d = json.loads(s.makefile("rb").readline()); s.close()
print(json.dumps({
    "n": d["latenz"].get("hook_to_state", {}).get("n", 0),
    "verworfen": d["verworfen_gesamt"],
    "typen": len(d["zaehler"]),
    "hat_gegendruck": "aktiv" in d.get("gegendruck", {}),
    "hat_queues": "queues" in d,
    "hat_units": "units" in d,
}))
PYEOF
)"
n="$(jq -r '.n' <<<"$ergebnis" 2>/dev/null)"
verworfen="$(jq -r '.verworfen' <<<"$ergebnis" 2>/dev/null)"
chk "Latenz-Histogramm hat alle Ereignisse gesehen" "$n" "$((GUT + SCHLECHT))"
chk "verworfene Ereignisse passen zur erzeugten Last" "$verworfen" "$SCHLECHT"
chk "Zaehler fuer alle vier Typen" "$(jq -r '.typen' <<<"$ergebnis")" 4
chk "Gegendruck-Zustand wird gemeldet" "$(jq -r '.hat_gegendruck' <<<"$ergebnis")" true
chk "Warteschlangen werden gemeldet" "$(jq -r '.hat_queues' <<<"$ergebnis")" true
chk "Unit-Zustaende werden gemeldet" "$(jq -r '.hat_units' <<<"$ergebnis")" true

tcp="$(ss -ltnp 2>/dev/null | grep -c "pid=$hub," || true)"
chk "Diagnose verlaesst den Rechner nicht (kein TCP)" "${tcp:-0}" 0

kill $hub 2>/dev/null; wait $hub 2>/dev/null; rm -rf "$tmp"
exit $fail
