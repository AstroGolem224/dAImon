#!/usr/bin/env bash
# Verifizierer fuer T−1.7: alle sechs Entscheidungen muessen abgeschlossen sein.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/summary.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if [[ -f "$RESULT" ]] && python3 - "$RESULT" >"$tmp" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)

container = data.get("spikes", data) if isinstance(data, dict) else data
aliases = {
    1: ("T-1.1", "T−1.1", "wakeword"),
    2: ("T-1.2", "T−1.2", "ort"),
    3: ("T-1.3", "T−1.3", "layershell"),
    4: ("T-1.4", "T−1.4", "portal"),
    5: ("T-1.5", "T−1.5", "mood"),
    6: ("T-1.6", "T−1.6", "hookoverhead"),
}
def find(names):
    if isinstance(container, dict):
        for name in names:
            if isinstance(container.get(name), dict):
                return container[name]
    if isinstance(container, list):
        for row in container:
            if not isinstance(row, dict):
                continue
            ident = row.get("spike", row.get("id", row.get("task", row.get("name"))))
            if ident in names:
                return row
    return None

all_rows = []
for i, names in aliases.items():
    row = find(names)
    all_rows.append(row)
    present = isinstance(row, dict)
    verdict = present and isinstance(row.get("verdict"), str) and bool(row["verdict"].strip())
    decision = present and isinstance(row.get("decision"), str) and bool(row["decision"].strip())
    print(f"{i}_present\t{'ja' if present else 'nein'}")
    print(f"{i}_verdict\t{'ja' if verdict else 'nein'}")
    print(f"{i}_decision\t{'ja' if decision else 'nein'}")
pending = any(isinstance(row, dict) and
              str(row.get("verdict", "")).strip().lower() == "pending"
              for row in all_rows)
print(f"none_pending\t{'nein' if pending else 'ja'}")
PY
then
  declare -A r=()
  while IFS=$'\t' read -r key value; do r["$key"]="$value"; done <"$tmp"
  valid=ja
else
  declare -A r=()
  valid=nein
fi

echo "T−1.7 — Entscheidungsprotokoll"
chk "summary.json ist gueltiges JSON" "$valid" ja
for i in 1 2 3 4 5 6; do
  chk "T−1.$i ist enthalten" "${r[${i}_present]:-nein}" ja
  chk "T−1.$i hat ein Verdikt" "${r[${i}_verdict]:-nein}" ja
  chk "T−1.$i hat eine Entscheidung" "${r[${i}_decision]:-nein}" ja
done
chk "kein Verdikt ist pending" "${r[none_pending]:-nein}" ja
exit $fail
