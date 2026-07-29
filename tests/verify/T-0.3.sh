#!/usr/bin/env bash
# Verifizierer fuer T-0.3.t: das Mood-Mapping ist festgeschrieben.
#
# Der Plan verlangt: "erwartet genau die als xfail markierten Abweichungen und
# keine weiteren Fehlschlaege". Das ist eine Aussage in ZWEI Richtungen, und
# beide muessen geprueft werden:
#
#   * Kein `failed` -- der Bestandscode tut, was die Tabelle sagt.
#   * Kein `xpassed` -- eine Abweichung, die unerwartet gruen wird, ist genauso
#     ein Signal wie eine neue rote. Sie hiesse, dass sich das Verhalten
#     geaendert hat, ohne dass es jemand aufgeschrieben hat. Die Tests sind
#     deshalb `xfail(strict=True)`; pytest macht daraus selbst ein `failed`.
#
# Zusaetzlich: die Zahl der Abweichungen muss zu dem passen, was T−1.5
# tatsaechlich gemessen hat. Sonst koennte man eine unbequeme Abweichung
# loeschen und der Lauf bliebe gruen.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
TESTS="$REPO/tests/test_mood_mapping.py"
MOOD="$REPO/spikes/mood/results.json"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.3.t — Mood-Mapping festgeschrieben"

chk "Testdatei existiert" "$([[ -f "$TESTS" ]] && echo ja || echo nein)" ja
chk "venv-Python vorhanden (T-0.1)" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja

if [[ ! -f "$TESTS" || ! -x "$PY" ]]; then exit 1; fi

# Nicht auf die Textzusammenfassung parsen: pyproject setzt bereits -q, ein
# zweites -q macht daraus -qq und unterdrueckt die Zusammenfassung komplett.
# Der Verifizierer las dann 0 passed und meldete falsch rot. JUnit-XML ist
# maschinenlesbar und von Ausgabeformaten unabhaengig.
xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && "$PY" -m pytest "$TESTS" --tb=no -p no:cacheprovider \
    --junitxml="$xml" ) >/dev/null 2>&1
rc=$?

read -r passed failed xfailed xpassed errors <<<"$(
  "$PY" - "$xml" <<'PYEOF'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suite = root if root.tag == "testsuite" else root.find("testsuite")
passed = failed = xfailed = xpassed = errors = 0
for case in suite.iter("testcase"):
    kinds = {child.tag for child in case}
    if "error" in kinds:
        errors += 1
    elif "failure" in kinds:
        failed += 1
    elif "skipped" in kinds:
        # pytest legt xfail als <skipped type="pytest.xfail"> ab.
        xfailed += 1
    else:
        passed += 1
# xpassed(strict) landet als failure -- das ist gewollt und wird oben gezaehlt.
print(passed, failed, xfailed, xpassed, errors)
PYEOF
)"
rm -f "$xml"

echo "  pytest: $passed passed, $xfailed xfailed, $failed failed, $xpassed xpassed"

chk "keine fehlgeschlagenen Tests" "$failed" 0
chk "keine unerwartet gruenen Abweichungen (xpassed)" "$xpassed" 0
chk "keine Fehler beim Sammeln" "$errors" 0
chk "es gibt gruene Mapping-Tests" "$([[ "$passed" -gt 0 ]] && echo ja || echo nein)" ja
chk "es gibt dokumentiert rote Abweichungen" "$([[ "$xfailed" -gt 0 ]] && echo ja || echo nein)" ja

# --- Gegenprobe gegen die Erhebung -----------------------------------------
# Jede in T−1.5 gemessene Abweichungsklasse muss in der Testdatei vorkommen.
# Sonst liesse sich eine unbequeme Abweichung stillschweigend fallenlassen.
if [[ -f "$MOOD" ]]; then
  klassen="$(jq -r '[.mismatches[].art] | unique | .[]' "$MOOD" 2>/dev/null)"
  for k in $klassen; do
    chk "Abweichungsklasse $k ist als Test vertreten" \
      "$(grep -q "$k" "$TESTS" && echo ja || echo nein)" ja
  done
else
  echo "  FAIL spikes/mood/results.json fehlt -- Gegenprobe nicht moeglich"
  fail=1
fi


[[ $rc -ne 0 && $failed -eq 0 && $xpassed -eq 0 ]] && { echo "  FAIL pytest endete mit $rc"; fail=1; }
exit $fail
