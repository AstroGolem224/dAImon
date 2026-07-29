#!/usr/bin/env bash
# Verifizierer fuer T-0.4: Protokoll-Schemas.
#
# Der Plan verlangt ausdruecklich einen Test, "der belegt, dass ein
# hineingeschmuggeltes initiator-Feld beim Deserialisieren verworfen wird".
# Ein gruener pytest-Lauf allein zeigt das nicht -- man koennte den Test
# loeschen und alles bliebe gruen. Deshalb prueft dieser Verifizierer den
# Schmuggelversuch ZUSAETZLICH selbst, unabhaengig von der Testdatei.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
TESTS="$REPO/tests/test_protocol.py"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.4 — Protokoll-Schemas"

chk "Modul existiert" "$([[ -f "$REPO/daimon/common/protocol.py" ]] && echo ja || echo nein)" ja
chk "Testdatei existiert" "$([[ -f "$TESTS" ]] && echo ja || echo nein)" ja
chk "venv-Python vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
[[ -x "$PY" && -f "$TESTS" ]] || exit 1

xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && "$PY" -m pytest "$TESTS" --tb=no -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
read -r passed failed <<<"$("$PY" - "$xml" <<'PYEOF'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suite = root if root.tag == "testsuite" else root.find("testsuite")
p = f = 0
for case in suite.iter("testcase"):
    kinds = {c.tag for c in case}
    if kinds & {"failure", "error"}:
        f += 1
    elif "skipped" not in kinds:
        p += 1
print(p, f)
PYEOF
)"
rm -f "$xml"
echo "  pytest: $passed passed, $failed failed"
chk "keine fehlgeschlagenen Tests" "$failed" 0
chk "es laufen ueberhaupt Tests" "$([[ "$passed" -gt 0 ]] && echo ja || echo nein)" ja

# --- eigene Gegenprobe, unabhaengig von der Testdatei ----------------------
eigen="$( cd "$REPO" && "$PY" - <<'PYEOF' 2>&1
from daimon.common.protocol import (ActionRequest, Event, ExecutionOrder,
                                    ActionApproval, Mark, State, UnsupportedVersion)
import json

def sag(name, ok):
    print(f"{name}={'ja' if ok else 'nein'}")

r = ActionRequest.from_dict({"action_id": "a", "request_id": "r",
                             "initiator": "user", "params_hash": "deadbeef"})
sag("initiator_weg", not hasattr(r, "initiator") and "initiator" not in r.to_dict())
sag("params_hash_weg", not hasattr(r, "params_hash") and "params_hash" not in r.to_dict())

e = Event.from_dict({"type": "hook", "source": "boese"})
sag("source_weg", not hasattr(e, "source") and "source" not in e.to_dict())

o = ExecutionOrder.from_dict({"order_id": "o", "action_id": "a", "params_hash": "abc"})
sag("order_behaelt_hash", o.params_hash == "abc")

a = ActionApproval.from_dict({"request_id": "r", "approved": True,
                              "reason": "trusted:harmlos"})
sag("praefix_faelscht_nicht", a.reason.mark is Mark.TAINTED)
b = ActionApproval.from_dict(json.loads(json.dumps(a.to_dict())))
sag("marke_ueberlebt_json", b.reason.mark is Mark.TAINTED)

try:
    State.from_dict({"v": 99})
    sag("unbekanntes_v_wirft", False)
except UnsupportedVersion:
    sag("unbekanntes_v_wirft", True)

sag("neues_feld_toleriert", State.from_dict({"v": 2, "aus_der_zukunft": 1}).mood == "sleeping")
PYEOF
)"
for k in initiator_weg params_hash_weg source_weg order_behaelt_hash \
         praefix_faelscht_nicht marke_ueberlebt_json unbekanntes_v_wirft neues_feld_toleriert; do
  chk "eigene Probe: $k" "$(grep -oP "(?<=^$k=).*" <<<"$eigen" | head -1)" ja
done

exit $fail
