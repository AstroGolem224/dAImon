#!/usr/bin/env bash
# Verifizierer fuer T-2.5: Multi-Output und Hotplug.
#
# Zusage: "Monitorwechsel bringt das Pet nicht um."
#
# Die vier Kriterien und wie sie hier gemessen werden:
#
#   1. Eine Layer-Surface je wl_output, explizit an einen BENANNTEN Output
#      gebunden statt an NULL. Gemessen zweifach zur Laufzeit:
#        a) der Diagnose-Socket meldet `output` nichtleer,
#        b) im Protokoll-Mitschnitt nennt `get_layer_surface` ein echtes
#           `wl_output@N` -- und zwar genau das, dessen `name`-Ereignis den
#           erwarteten Namen trug.
#      NICHT per grep im Quelltext: bei T-1.7.v3 schrieb ein Builder 'face'
#      statt "face", und die eingefrorene Pruefung schwieg dazu.
#
#   2. Output-Removal -> Surface neu erzeugen. AUF DIESER MASCHINE NICHT
#      PRUEFBAR, und das wird hier nicht kaschiert:
#        * es gibt genau einen Monitor (HDMI-A-1, 5120x1440),
#        * `disable` auf dem einzigen Output riskiert eine Sitzung ohne
#          Ausgabe -- Entscheidung von Matthias: nicht anfassen,
#        * DPMS entfernt den `wl_output` nachweislich NICHT. Gemessen am
#          Mitschnitt eines vollen off/on-Zyklus: kein `global_remove`, kein
#          `zwlr_layer_surface_v1.closed`, nur ein zweites `configure`.
#      Eine Pruefung, die Removal simuliert und dann gruen meldet, waere
#      Fall 12 der Liste in HANDOVER.md. Stattdessen wird geprueft, was ohne
#      Removal ueberhaupt messbar ist: `output_wechsel` existiert, steht auf
#      0, und der Prozess ueberlebt den DPMS-Zyklus. Kriterium 2 bleibt
#      damit offen und muss an einer Maschine mit zweitem Monitor
#      nachgeholt werden.
#
#   3. Pet auf genau einem Output, konfigurierbar ueber DAIMON_FACE_OUTPUT.
#      NUR ZUR HAELFTE PRUEFBAR, aus demselben Grund wie Kriterium 2:
#        GEPRUEFT:     es wird ueberhaupt ein real existierender Output
#                      gebunden; ein unbekannter Name fuehrt zum Fallback und
#                      NICHT zum Abbruch; ohne Variable ebenso; und es ist
#                      genau eine Instanz -- am Mitschnitt gezaehlt,
#                      `get_layer_surface` genau (1 + output_wechsel).
#        NICHT GEPRUEFT: dass die Auswahl NACH DEM NAMEN geschieht. Es gibt
#                      genau einen Output, also ist "der gewuenschte" und
#                      "der erste" derselbe Name. Eine Implementierung, die
#                      DAIMON_FACE_OUTPUT liest und wegwirft, besteht alle
#                      drei Teillaeufe.
#      Das ist nicht behauptet, sondern belegt: der Mutant
#      `tests/blindstellen/T-2.5-wunsch-ignoriert` tut genau das und wird von
#      diesem Verifizierer ABSICHTLICH nicht gefangen (Exit 0 gemessen).
#      Die einzige ehrliche Gegenprobe braucht einen zweiten Monitor:
#      DAIMON_FACE_OUTPUT auf den zweiten setzen und pruefen, dass NICHT der
#      erste gebunden wird. Ein Ersatz ueber ein Selbstzeugnis des Prueflings
#      (etwa eine stderr-Zeile "habe Output X gewaehlt") waere Fall 9 aus
#      HANDOVER.md -- der vom Pruefling selbst gefuehrte Bericht.
#
#   4. Monitor-Sleep ueberlebt: nach `kscreen-doctor --dpms off/on` lebt der
#      Prozess und zeichnet wieder.
#
# Zwei Messfehler, die hier vermieden werden -- beide vorher real passiert:
#
#   * `kscreen-doctor output.HDMI-A-1.dpms.off` (so stand es im Auftrag)
#     PARST auf dieser kscreen-Version NICHT. Sie meldet "Unable to parse
#     arguments" und beendet sich trotzdem mit 0. Ein Verifizierer, der den
#     Exitcode glaubt, prueft danach einen Monitor, der nie aus war. Richtig
#     ist `kscreen-doctor --dpms off`, und geprueft wird der Zustand ueber
#     `--dpms show`, nicht der Exitcode.
#   * "last_render_ts ist frisch" ist ohne Gegenprobe keine Aussage. Deshalb
#     zwei Positivkontrollen VOR dem DPMS-Zyklus: ein Zustandswechsel MUSS
#     den Zeitstempel bewegen, und ohne Zustandswechsel MUSS er stehen
#     bleiben. Erst dann heisst "frisch nach dem Wake" etwas.
#
# Der Monitor wird in JEDEM Fall wieder eingeschaltet: `trap ... EXIT INT
# TERM` plus ein Hintergrund-Sicherheitsnetz, das auch ein `kill -9` des
# Skripts ueberlebt. Ein Verifizierer, der den einzigen Monitor dunkel
# zuruecklaesst, ist inakzeptabel.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-2.5 — Multi-Output und Hotplug"

# --- Bauen. Immer. -----------------------------------------------------------
# `cargo test` baut das Bin-Target nicht; der T-1.4-Verifizierer baute nur bei
# fehlendem Binary und startete deshalb einmal ein Binary von vorgestern.
# Fixture-Baeume bauen ueber CARGO_TARGET_DIR ins Temp, sonst schleppt eine
# Fixture-Kopie das Binary der unmutierten Quelle mit.
BAUDIR=""
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then
  BAUDIR="$(mktemp -d)"
  ( cd "$TARGET/face" && CARGO_TARGET_DIR="$BAUDIR" timeout 900 cargo build -p face ) >/dev/null 2>&1
  bau_rc=$?
  BIN="$BAUDIR/debug/daimon-face"
else
  ( cd "$TARGET/face" && timeout 900 cargo build -p face ) >/dev/null 2>&1
  bau_rc=$?
fi
chk "cargo build laeuft durch" "$bau_rc" 0
chk "Binary vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja

# Das Binary darf nicht aelter sein als die juengste Quelle.
if [[ -x "$BIN" ]]; then
  juengste="$(find "$TARGET/face/src" "$TARGET/face/Cargo.toml" -newer "$BIN" 2>/dev/null | head -1)"
  chk "Binary ist nicht aelter als die Quellen" "$([[ -z "$juengste" ]] && echo ja || echo "aelter_als_$juengste")" ja
fi

chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
chk "kscreen-doctor vorhanden" "$(command -v kscreen-doctor >/dev/null && echo ja || echo nein)" ja
chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
if [[ -z "${WAYLAND_DISPLAY:-}" || ! -x "$BIN" ]] || ! command -v kscreen-doctor >/dev/null; then
  echo "  FAIL Live-Pruefungen uebersprungen"
  rm -rf -- "${BAUDIR:-/nonexistent}"
  exit 1
fi

# --- Reale Outputnamen: gemessen, nicht angenommen ---------------------------
mapfile -t REALE < <(kscreen-doctor -j 2>/dev/null | jq -r '.outputs[] | select(.enabled) | .name')
echo "  reale Outputs: ${REALE[*]:-(keine)}"
chk "mindestens ein realer Output gefunden (Positivkontrolle)" \
  "$([[ "${#REALE[@]}" -ge 1 ]] && echo ja || echo nein)" ja
[[ "${#REALE[@]}" -ge 1 ]] || { rm -rf -- "${BAUDIR:-/nonexistent}"; exit 1; }
WUNSCH="${REALE[0]}"
ist_real() { local n="$1"; for r in "${REALE[@]}"; do [[ "$n" == "$r" ]] && { echo ja; return; }; done; echo nein; }

# --- Aufraeumen: der Monitor geht in JEDEM Fall wieder an ---------------------
FACE=""
TMPS=()
# Sicherheitsnetz als eigener Prozess: ueberlebt auch ein hartes Ende des
# Skripts. 300 s ist grosszuegig laenger als der laengste Lauf hier.
( sleep 300; kscreen-doctor --dpms on >/dev/null 2>&1 ) &
NETZ=$!
aufraeumen() {
  kscreen-doctor --dpms on >/dev/null 2>&1
  [[ -n "$FACE" ]] && kill "$FACE" 2>/dev/null
  kill "$NETZ" 2>/dev/null
  for t in "${TMPS[@]:-}"; do [[ -n "$t" ]] && rm -rf -- "$t"; done
  rm -rf -- "${BAUDIR:-/nonexistent}"
}
trap aufraeumen EXIT INT TERM

diag() { "$PY" - "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
ctl() { "$PY" - "$1" "$2" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

# Kurzes Temp-Verzeichnis: AF_UNIX-Pfade duerfen SUN_LEN (108 Byte) nicht
# reissen. Ein Socket unterhalb des Scratchpad-Verzeichnisses tut das, und
# dann startet das Face gar nicht erst -- der Verifizierer misst dann seinen
# eigenen Pfadnamen. Real passiert beim Bau dieses Skripts.
kurz_tmp() { mktemp -d -p "/run/user/$(id -u)" 2>/dev/null || mktemp -d; }
starte() {  # $1 = tmpdir, $2 = max_secs, Rest = Umgebungszuweisungen
  local t="$1" secs="$2"; shift 2
  env "$@" DAIMON_MAX_SECS="$secs" WAYLAND_DEBUG=1 "$BIN" \
    --pet-manifest "$MANIFEST" --sprite-position 900,500 \
    --diag-socket "$t/d.sock" --control-socket "$t/c.sock" \
    >"$t/out.log" 2>"$t/wl.log" &
  FACE=$!
  local i
  for i in $(seq 1 250); do [[ -S "$t/c.sock" && -S "$t/d.sock" ]] && break; sleep 0.1; done
  sleep 1.5
}
stoppe() { [[ -n "$FACE" ]] && kill "$FACE" 2>/dev/null; wait "$FACE" 2>/dev/null; FACE=""; }

# Bindet der Mitschnitt die Layer-Surface an ein echtes wl_output? wayland-rs
# schreibt eine NULL-Referenz als `<anonymous>@0` -- genau die Schreibweise,
# die der T-2.4-Verifizierer erst beim zweiten Anlauf gefunden hat.
gebundenes_output_objekt() { grep -oE 'get_layer_surface\([^)]*' "$1" | grep -oE 'wl_output@[0-9]+|<anonymous>@[0-9]+' | head -1; }
objekt_fuer_namen() { grep -E "wl_output@[0-9]+\.name, \(Some\(\"$2\"\)\)" "$1" | grep -oE 'wl_output@[0-9]+' | head -1; }

# =============================================================================
# Kriterium 3 (und 1) — billig, ohne DPMS
# =============================================================================
echo
echo "--- Kriterium 3: nur zur Haelfte pruefbar (ein Monitor) ---"
echo "  geprueft:       es wird ein realer Output gebunden, Fallback ohne Abbruch"
echo "  NICHT geprueft: dass nach dem NAMEN ausgewaehlt wird -- bei einem"
echo "                  Monitor ist 'der gewuenschte' == 'der erste'."
echo "                  Beleg: tests/blindstellen/T-2.5-wunsch-ignoriert (Exit 0)"

# (a) gueltiger Name
T1="$(kurz_tmp)"; TMPS+=("$T1")
starte "$T1" 40 "DAIMON_FACE_OUTPUT=$WUNSCH"
chk "3a Overlay startet mit DAIMON_FACE_OUTPUT=$WUNSCH" \
  "$([[ -S "$T1/d.sock" ]] && echo ja || echo nein)" ja
d1="$(diag "$T1/d.sock")"
echo "  Diagnose: $d1"
chk "3a Diagnose kennt das Feld output (Vertrag)" \
  "$(jq -e 'has("output")' <<<"$d1" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "3a Diagnose kennt das Feld output_wechsel (Vertrag)" \
  "$(jq -e 'has("output_wechsel")' <<<"$d1" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "3a gebundener Output ist der gewuenschte" "$(jq -r '.output // ""' <<<"$d1")" "$WUNSCH"
chk "3a Prozess lebt" "$(kill -0 "$FACE" 2>/dev/null && echo ja || echo nein)" ja

echo "--- Kriterium 1: die Bindung ist explizit und benannt ---"
chk "1 Mitschnitt ist aktiv (Positivkontrolle)" \
  "$(grep -q 'zwlr_layer_shell_v1@' "$T1/wl.log" && echo ja || echo nein)" ja
obj_gebunden="$(gebundenes_output_objekt "$T1/wl.log")"
obj_erwartet="$(objekt_fuer_namen "$T1/wl.log" "$WUNSCH")"
echo "  get_layer_surface bindet an: ${obj_gebunden:-(nichts)}; $WUNSCH ist ${obj_erwartet:-(unbekannt)}"
chk "1 der Name $WUNSCH taucht als wl_output-Objekt auf (Positivkontrolle)" \
  "$([[ -n "$obj_erwartet" ]] && echo ja || echo nein)" ja
chk "1 Layer-Surface ist an ein echtes wl_output gebunden, nicht an NULL" \
  "$([[ "$obj_gebunden" == wl_output@* ]] && echo ja || echo nein)" ja
chk "1 gebunden ist genau das Objekt mit dem erwarteten Namen" \
  "$obj_gebunden" "$obj_erwartet"
chk "1 output ist zur Laufzeit nichtleer" \
  "$([[ -n "$(jq -r '.output // ""' <<<"$d1")" ]] && echo ja || echo nein)" ja
lz="$(grep -c 'get_layer_surface(' "$T1/wl.log")"
w1="$(jq -r '.output_wechsel // 0' <<<"$d1")"
echo "  get_layer_surface-Aufrufe: $lz, output_wechsel: $w1"
chk "3a genau eine Layer-Surface (1 + output_wechsel)" "$lz" "$(( 1 + w1 ))"
stoppe

# (b) unbekannter Name -> Fallback, KEIN Abbruch
T2="$(kurz_tmp)"; TMPS+=("$T2")
starte "$T2" 40 "DAIMON_FACE_OUTPUT=GIBTESNICHT"
chk "3b Prozess lebt trotz unbekanntem Outputnamen" \
  "$(kill -0 "$FACE" 2>/dev/null && echo ja || echo nein)" ja
d2="$(diag "$T2/d.sock")"
o2="$(jq -r '.output // ""' <<<"$d2")"
echo "  Fallback-Output: '${o2}'"
chk "3b Fallback auf einen real existierenden Output" "$(ist_real "$o2")" ja
stoppe

# (c) ohne Variable -> ebenfalls ein realer Name
T3="$(kurz_tmp)"; TMPS+=("$T3")
starte "$T3" 40 "DAIMON_FACE_OUTPUT="
chk "3c Prozess lebt ohne DAIMON_FACE_OUTPUT" \
  "$(kill -0 "$FACE" 2>/dev/null && echo ja || echo nein)" ja
d3="$(diag "$T3/d.sock")"
o3="$(jq -r '.output // ""' <<<"$d3")"
echo "  Vorgabe-Output: '${o3}'"
chk "3c ohne Variable ein real existierender Output" "$(ist_real "$o3")" ja
stoppe

# =============================================================================
# Kriterium 4 — DPMS. Ab hier ist der Monitor beteiligt.
# =============================================================================
echo
echo "--- Kriterium 4: Monitor-Sleep ueberlebt ---"
T4="$(kurz_tmp)"; TMPS+=("$T4")
starte "$T4" 120 "DAIMON_FACE_OUTPUT=$WUNSCH"
chk "4 Overlay startet" "$([[ -S "$T4/c.sock" ]] && echo ja || echo nein)" ja

ts() { jq -r '.last_render_ts // 0' <<<"$(diag "$T4/d.sock")"; }
groesser() { awk -v a="$1" -v b="$2" 'BEGIN {exit !(a+0 > b+0)}'; }
# Wartet bis zu $1 Sekunden auf einen Zeitstempel groesser als $2.
warte_frisch() {
  local grenze="$1" alt="$2" ende neu
  ende="$("$PY" -c "import time; print(time.monotonic()+$grenze)")"
  while :; do
    neu="$(ts)"
    if [[ "$neu" =~ ^[0-9.]+$ ]] && groesser "$neu" "$alt"; then echo "$neu"; return 0; fi
    "$PY" -c "import sys,time; sys.exit(0 if time.monotonic() < $ende else 1)" || { echo "$neu"; return 1; }
    sleep 0.05
  done
}

# Positivkontrolle 1: die Frischmessung KANN anschlagen.
ts0="$(ts)"
chk "4 Zustandswechsel wird bestaetigt" "$(ctl "$T4/c.sock" 'sichtbar aus')" ok
ts1="$(warte_frisch 2 "$ts0")"; pk1=$?
echo "  ts vor=$ts0 nach Zustandswechsel=$ts1"
chk "4 POSITIVKONTROLLE: ein Zustandswechsel bewegt last_render_ts binnen 2 s" "$pk1" 0
chk "4 Rueckschalten wird bestaetigt" "$(ctl "$T4/c.sock" 'sichtbar an')" ok
ts2="$(warte_frisch 2 "$ts1")"; pk1b=$?
chk "4 POSITIVKONTROLLE: auch das Rueckschalten bewegt ihn" "$pk1b" 0

# Positivkontrolle 2: "frisch" ist keine Selbstverstaendlichkeit. Ohne
# Zustandswechsel muss der Zeitstempel stehen bleiben -- sonst wuerde ein
# dauernd zeichnender Pruefling die Wake-Pruefung bestehen, ohne je auf den
# Wake reagiert zu haben. (Deckt sich mit der Idle-CPU-Zusage aus T-1.5:
# 0,000 % ueber 60 s heisst, im Leerlauf wird nicht gezeichnet.)
ts_ruhe="$(ts)"
sleep 2.5
chk "4 GEGENKONTROLLE: ohne Zustandswechsel bleibt der Zeitstempel stehen" \
  "$(ts)" "$ts_ruhe"

cfg_vor="$(jq -r '.configure_empfangen // 0' <<<"$(diag "$T4/d.sock")")"
w_vor="$(jq -r '.output_wechsel // 0' <<<"$(diag "$T4/d.sock")")"

# --- Der DPMS-Zyklus ---------------------------------------------------------
# Der Exitcode von kscreen-doctor taugt nicht als Beleg (siehe Kopf), deshalb
# wird der Zustand abgefragt.
#
# Und er wird WIEDERHOLT abgefragt: `--dpms off` verpufft hier gelegentlich
# ganz. Gemessen in drei Laeufen hintereinander -- einmal blieb der Monitor
# volle 10 s an, zweimal ging er sofort aus. Ursache unbekannt (Verdacht:
# KWin ignoriert das Abschalten kurz nach vorheriger Aktivitaet). Ohne diese
# Schleife ist der Verifizierer flackernd, und ein flackerndes Gate wird
# irgendwann weggeklickt statt geglaubt.
#
# Die Stimuluskontrolle bleibt streng: geht der Monitor nach allen Versuchen
# nicht aus, ist der Lauf ROT. Nicht gemessen zu haben ist kein Bestehen.
dpms_zustand() { kscreen-doctor --dpms show 2>/dev/null | grep -oE '(on|off)$' | head -1; }
dpms_setzen() {  # $1 = on|off, $2 = Versuche
  local ziel="$1" versuche="$2" v i
  for v in $(seq 1 "$versuche"); do
    kscreen-doctor --dpms "$ziel" >/dev/null 2>&1
    for i in $(seq 1 15); do
      [[ "$(dpms_zustand)" == "$ziel" ]] && { [[ "$v" -gt 1 ]] && echo "  (DPMS $ziel erst im Versuch $v)"; return 0; }
      sleep 0.2
    done
  done
  return 1
}
dpms_setzen off 3
dpms_waehrend="$(dpms_zustand)"
echo "  DPMS-Zustand waehrend des Zyklus: ${dpms_waehrend:-unbekannt}"
chk "4 STIMULUSKONTROLLE: der Monitor war wirklich aus" "$dpms_waehrend" off
sleep 2.5
lebt_waehrend="$(kill -0 "$FACE" 2>/dev/null && echo ja || echo nein)"
dpms_setzen on 5
sleep 1.0
dpms_danach="$(dpms_zustand)"
chk "4 der Monitor ist wieder an" "$dpms_danach" on
chk "4 der Prozess lebte waehrend des Schlafs" "$lebt_waehrend" ja
chk "4 der Prozess lebt nach dem Aufwachen" \
  "$(kill -0 "$FACE" 2>/dev/null && echo ja || echo nein)" ja

d4="$(diag "$T4/d.sock")"
echo "  Diagnose nach dem Zyklus: $d4"
cfg_nach="$(jq -r '.configure_empfangen // 0' <<<"$d4")"
w_nach="$(jq -r '.output_wechsel // 0' <<<"$d4")"
echo "  configure_empfangen $cfg_vor -> $cfg_nach, output_wechsel $w_vor -> $w_nach"
# Ohne diese Kontrolle waere "lebt noch" die Nullaussage: ein Prozess, den der
# Zyklus gar nicht erreicht hat, lebt selbstverstaendlich weiter. Gemessen am
# unveraenderten Stand kam beim Wake ein zweites `configure` an.
chk "4 STIMULUSKONTROLLE: der Zyklus ist beim Client angekommen (configure oder Neuerzeugung)" \
  "$([[ "$cfg_nach" -gt "$cfg_vor" || "$w_nach" -gt "$w_vor" ]] && echo ja || echo nein)" ja

# Die eigentliche Messung: zeichnet er wieder?
ts_vor_wake="$(ts)"
chk "4 Steuerkanal antwortet nach dem Wake" "$(ctl "$T4/c.sock" 'sichtbar aus')" ok
ts_nach_wake="$(warte_frisch 2 "$ts_vor_wake")"; rc_wake=$?
echo "  ts vor=$ts_vor_wake nach Wake-Zustandswechsel=$ts_nach_wake"
chk "4 nach dem Wake wird binnen 2 s wieder gezeichnet" "$rc_wake" 0
ctl "$T4/c.sock" 'sichtbar an' >/dev/null

# Genau eine sichtbare Instanz, auch nach dem Zyklus.
lz4="$(grep -c 'get_layer_surface(' "$T4/wl.log")"
echo "  get_layer_surface-Aufrufe nach dem Zyklus: $lz4"
chk "4 genau eine sichtbare Instanz (1 + output_wechsel)" "$lz4" "$(( 1 + w_nach ))"
chk "4 der gebundene Output ist weiterhin ein realer Name" \
  "$(ist_real "$(jq -r '.output // ""' <<<"$d4")")" ja

# --- Kriterium 2, soweit hier ueberhaupt messbar ------------------------------
# Siehe der lange Kommentar im Kopf: echtes Output-Removal gibt es auf dieser
# Maschine nicht. Was bleibt, ist die Aussage "ohne Removal auch kein
# Wechsel" -- und dass das Feld ueberhaupt existiert und gefuehrt wird.
echo
echo "--- Kriterium 2: auf dieser Maschine NICHT vollstaendig pruefbar ---"
echo "  (ein Monitor, kein disable auf dem einzigen Output, DPMS entfernt den wl_output nicht)"
chk "2 output_wechsel ist im Vertrag vorhanden" \
  "$(jq -e 'has("output_wechsel")' <<<"$d4" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "2 ohne Removal hat kein Wechsel stattgefunden" "$w_nach" 0
chk "2 kein zwlr_layer_surface_v1.closed im ganzen Lauf" \
  "$(grep -c 'zwlr_layer_surface_v1@[0-9]*\.closed' "$T4/wl.log")" 0
stoppe

echo
echo "--- ZWEI OFFENE PUNKTE, beide brauchen einen zweiten Monitor ---"
echo "  (1) Kriterium 2: Neuerzeugung der Surface nach echtem Output-Removal"
echo "      ist ungeprueft. DPMS entfernt den wl_output nicht, disable auf"
echo "      dem einzigen Output ist ausgeschlossen."
echo "  (2) Kriterium 3: die Auswahl NACH DEM NAMEN ist ungeprueft. Belegt"
echo "      durch tests/blindstellen/T-2.5-wunsch-ignoriert -- eine Umsetzung, die"
echo "      DAIMON_FACE_OUTPUT wegwirft, besteht diesen Verifizierer."
echo "  Nachzuholen: zweiten Monitor anschliessen, DAIMON_FACE_OUTPUT auf den"
echo "  zweiten setzen, pruefen dass NICHT der erste gebunden wird; und den"
echo "  zweiten abziehen, um Kriterium 2 zu messen."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-2.5: gruen, soweit auf dieser Maschine pruefbar."
  echo "       OFFEN bleiben Kriterium 2 (Output-Removal) und die Namensauswahl"
  echo "       aus Kriterium 3. Das ist KEINE volle Abnahme von T-2.5."
else
  echo "T-2.5: FEHLGESCHLAGEN"
fi
exit $fail
