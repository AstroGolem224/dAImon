#!/usr/bin/env bash
# Verifizierer fuer T-1.10: Alltagstauglichkeit.
#
# Der einzige Verifizierer im Projekt, der nichts startet und nichts misst.
# Er liest ein Protokoll, das ueber Tage entstanden ist, und prueft, ob es
# ueberhaupt eine Aussage traegt.
#
# Der Plan verlangt in `tests/evidence/phase1-usage.json`:
#   days >= 5, needs_input_events > 0, idle_cpu_p95 < 1.0, crashes == 0,
#   und ein `verdict`, das gesetzt und nicht `pending` ist.
#
# Das letzte Kriterium ist das wichtigste und das einzige, das kein Programm
# erfuellen kann. `verdict` ist ein URTEIL: traegt der Kernnutzen, oder muss
# das Mapping geaendert und wiederholt werden. Ein Recorder, der es selbst
# setzt, hat kein Urteil gefaellt, sondern eine Pruefung umgangen. Deshalb
# steht hier ausdruecklich: `pending` ist ROT, und das ist kein Mangel des
# Skripts, sondern seine Aufgabe.
#
# Ebenso: `days` zaehlt Tage mit tatsaechlicher Laufzeit, nicht Kalendertage.
# Fuenf Tage Urlaub sind kein Alltagstest.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
DATEI="$TARGET/tests/evidence/phase1-usage.json"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-1.10 — Alltagstauglichkeit"

chk "phase1-usage.json existiert" "$([[ -f "$DATEI" ]] && echo ja || echo nein)" ja
if [[ ! -f "$DATEI" ]]; then
  echo "  Die Aufzeichnung hat noch nicht begonnen. Timer scharf schalten:"
  echo "    systemctl --user enable --now daimon-phase1.timer"
  exit 1
fi

# Positivkontrolle: die Datei ist gueltiges JSON. Ohne sie waeren alle
# jq-Abfragen unten leer, und "Wert fehlt" saehe aus wie "Wert zu klein".
chk "die Datei ist gueltiges JSON (Positivkontrolle)" \
  "$(jq -e . "$DATEI" >/dev/null 2>&1 && echo ja || echo nein)" ja
jq -e . "$DATEI" >/dev/null 2>&1 || exit 1

wert() { jq -r "$1 // \"FEHLT\"" "$DATEI" 2>/dev/null; }
zahl() { local v; v="$(wert "$1")"; [[ "$v" =~ ^-?[0-9.]+$ ]] && echo "$v" || echo "FEHLT"; }

days="$(zahl .days)"
events="$(zahl .needs_input_events)"
p95="$(zahl .idle_cpu_p95)"
crashes="$(zahl .crashes)"
verdict="$(wert .verdict)"

echo "  days=$days  needs_input_events=$events  idle_cpu_p95=$p95  crashes=$crashes"
echo "  verdict=$verdict"

for feld in days needs_input_events idle_cpu_p95 crashes verdict; do
  chk "Feld $feld vorhanden" \
    "$(jq -e "has(\"$feld\")" "$DATEI" >/dev/null 2>&1 && echo ja || echo nein)" ja
done

# Jedes Kriterium einzeln, ohne &&-Verkettung -- sonst sagt ein Fehlschlag
# nicht, WELCHE Zusage nicht haelt.
chk "mindestens 5 Tage mit Laufzeit" \
  "$(awk -v v="$days" 'BEGIN {print (v!="FEHLT" && v+0 >= 5) ? "ja" : "nein"}')" ja
chk "es gab ueberhaupt needs_input-Ereignisse" \
  "$(awk -v v="$events" 'BEGIN {print (v!="FEHLT" && v+0 > 0) ? "ja" : "nein"}')" ja
chk "Idle-CPU p95 unter 1,0 %" \
  "$(awk -v v="$p95" 'BEGIN {print (v!="FEHLT" && v+0 < 1.0) ? "ja" : "nein"}')" ja
chk "keine Abstuerze" \
  "$(awk -v v="$crashes" 'BEGIN {print (v!="FEHLT" && v+0 == 0) ? "ja" : "nein"}')" ja

# Das Urteil. Kein Programm darf es setzen, und kein Programm darf es
# beschoenigen: solange es `pending` ist, ist T-1.10 nicht erfuellt.
chk "ein Urteil ist gefaellt (nicht pending)" \
  "$([[ "$verdict" != "pending" && "$verdict" != "FEHLT" && -n "$verdict" ]] && echo ja || echo nein)" ja

# Die Tagesliste muss zu `days` passen. Ein Zaehler, der ueber der Liste
# steht, waere eine Selbstauskunft -- und die zaehlt hier nichts.
tage_in_liste="$(jq -r '(.tage // []) | length' "$DATEI" 2>/dev/null)"
echo "  Tageseintraege in der Liste: $tage_in_liste"
chk "die Tagesliste deckt die gezaehlten Tage" \
  "$(awk -v a="$tage_in_liste" -v b="$days" 'BEGIN {print (a+0 >= b+0) ? "ja" : "nein"}')" ja
# Und die Eintraege sind verschiedene Tage, nicht derselbe fuenfmal.
verschieden="$(jq -r '[(.tage // [])[] | .datum] | unique | length' "$DATEI" 2>/dev/null)"
echo "  verschiedene Datumswerte: $verschieden"
chk "die Tageseintraege sind verschiedene Tage" \
  "$(awk -v a="$verschieden" -v b="$days" 'BEGIN {print (a+0 >= b+0) ? "ja" : "nein"}')" ja

# Fehlalarme und Ablenkungen kann kein Programm messen. Sie MUESSEN von einem
# Menschen eingetragen sein -- `null` heisst "noch niemand hat hingesehen",
# und das ist bei einer Frage nach Alltagstauglichkeit keine Antwort.
for feld in fehlalarme ablenkungen; do
  v="$(wert ".$feld")"
  chk "$feld ist von Hand eingetragen (nicht null)" \
    "$([[ "$v" != "null" && "$v" != "FEHLT" ]] && echo ja || echo nein)" ja
done

chk "docs/phase1-verdict.md existiert" \
  "$([[ -f "$TARGET/docs/phase1-verdict.md" ]] && echo ja || echo nein)" ja

exit $fail
