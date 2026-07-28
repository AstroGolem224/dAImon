#!/usr/bin/env bash
# Verifizierer fuer T−1.1: Wake-Word-Zahlen werden aus Rohzaehlern nachgerechnet.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/wakeword/results.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
report="$tmp/report"

if [[ -f "$RESULT" ]] && python3 - "$RESULT" >"$report" <<'PY'
import json, math, re, sys

def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)

rows = data if isinstance(data, list) else next(
    (data[k] for k in ("names", "candidates", "results", "rows")
     if isinstance(data.get(k), list)), [])
required = ("name", "threshold", "trials", "false_rejects", "frr",
            "background_hours", "false_accepts", "far_per_hour", "verdict")

def close(a, b):
    return number(a) and number(b) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)

schema = bool(rows) and all(isinstance(r, dict) and all(k in r for k in required) for r in rows)
names = [r.get("name") for r in rows if isinstance(r, dict)]
distinct_names = len({n for n in names if isinstance(n, str) and n.strip()}) >= 2
trials = bool(rows) and all(number(r.get("trials")) and r["trials"] >= 50 for r in rows)
background = bool(rows) and all(number(r.get("background_hours")) and
                                r["background_hours"] >= 3 for r in rows)
thresholds = bool(rows) and all(number(r.get("threshold")) for r in rows)
numeric_rates = bool(rows) and all(number(r.get("frr")) and
                                   number(r.get("far_per_hour")) for r in rows)
raw = bool(rows) and all(number(r.get("false_rejects")) and
                         number(r.get("false_accepts")) for r in rows)
frr_ok = bool(rows) and all(number(r.get("trials")) and r["trials"] > 0 and
                            close(r.get("frr"), r.get("false_rejects") / r["trials"])
                            for r in rows)
far_ok = bool(rows) and all(number(r.get("background_hours")) and
                            r["background_hours"] > 0 and
                            close(r.get("far_per_hour"),
                                  r.get("false_accepts") / r["background_hours"])
                            for r in rows)
winner = any(number(r.get("frr")) and number(r.get("far_per_hour")) and
             r["frr"] < 0.10 and r["far_per_hour"] < 1.0 for r in rows)
verdict = data.get("verdict") if isinstance(data, dict) else None
verdict_ok = isinstance(verdict, str) and bool(verdict.strip())
decision = data.get("decision", "") if isinstance(data, dict) else ""
fallback = verdict == "pass" or (
    isinstance(decision, str) and
    re.search(r"(?:plan|variante)\s*[BC]\b", decision, re.I) is not None
)
for key, value in (
    ("json", True), ("schema", schema), ("names", distinct_names),
    ("trials", trials), ("background", background), ("thresholds", thresholds),
    ("numeric_rates", numeric_rates), ("raw", raw), ("frr", frr_ok),
    ("far", far_ok), ("winner", winner), ("verdict", verdict_ok),
    ("fallback", fallback),
):
    print(f"{key}\t{'ja' if value else 'nein'}")
PY
then
  declare -A r=()
  while IFS=$'\t' read -r key value; do r["$key"]="$value"; done <"$report"
else
  declare -A r=([json]=nein)
fi

echo "T−1.1 — Wake-Word"
chk "results.json ist gueltiges JSON" "${r[json]:-nein}" ja
chk "mindestens zwei verschiedene Kandidatennamen" "${r[names]:-nein}" ja
chk "je Kandidat mindestens 50 Versuche" "${r[trials]:-nein}" ja
chk "je Kandidat mindestens 3 Hintergrundstunden" "${r[background]:-nein}" ja
chk "optimierte Schwelle je Kandidat ist numerisch" "${r[thresholds]:-nein}" ja
chk "alle geforderten Felder je Kandidat vorhanden" "${r[schema]:-nein}" ja
chk "FRR und FAR sind Zahlen, nicht null" "${r[numeric_rates]:-nein}" ja
chk "Rohzaehler fuer FRR und FAR sind numerisch" "${r[raw]:-nein}" ja
chk "FRR stimmt mit false_rejects / trials ueberein" "${r[frr]:-nein}" ja
chk "FAR stimmt mit false_accepts / background_hours ueberein" "${r[far]:-nein}" ja
chk "mindestens ein Kandidat erreicht FRR < 0,10 und FAR < 1,0/h" "${r[winner]:-nein}" ja
chk "Verdikt ist vorhanden und nicht leer" "${r[verdict]:-nein}" ja
chk "bei anderem Verdikt als pass ist Plan B oder C benannt" "${r[fallback]:-nein}" ja
exit $fail
