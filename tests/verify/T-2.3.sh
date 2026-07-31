#!/usr/bin/env bash
# Verifizierer fuer T-2.3: Ziehen ueber Subsurface-Position.
#
# Der Plan nennt zwei Messungen: waehrend eines Zugs ueber 200 px duerfen NULL
# `configure`-Ereignisse ankommen, und die CPU muss unter 5 % eines Kerns
# bleiben.
#
# Warum ausgerechnet `configure` gezaehlt wird: `set_margin` gehoert der
# Layer-Surface, und jede Aenderung daran ist eine Aenderung der Surface-Rolle
# -- der Compositor antwortet mit `configure`, also mit einem Roundtrip je
# Mausbewegung. `wl_subsurface.set_position()` ist dagegen eine reine
# Zustandsaenderung am Elternteil. Der Zaehler unterscheidet die beiden
# Umsetzungen, ohne in den Quelltext zu sehen.
#
# ZUR STRECKE, teuer gelernt in T-1.3: der Zeiger laesst sich auf dieser
# Maschine nicht gezielt positionieren -- weder absolut (`ydotool mousemove
# -a` landet bei (0,0)) noch relativ (die Zeigerbeschleunigung verzerrt die
# Strecke; nominell 996 px ergaben real 3984). Deshalb wird NICHT verlangt,
# dass ydotool 200 px bewegt. Gezogen wird echt, und die tatsaechlich
# zurueckgelegte Strecke liest der Verifizierer aus der gemerkten Position im
# Diagnose-Socket. Was ydotool daraus macht, ist egal -- gemessen wird das
# Ergebnis.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
SYSPY="/usr/bin/python3"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
num_ge() { if [[ "$1" =~ ^-?[0-9]+$ && "$2" =~ ^-?[0-9]+$ ]] && (( $1 >= $2 )); then echo ja; else echo nein; fi; }

echo "T-2.3 — Ziehen ueber Subsurface-Position"

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
chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
chk "ydotool vorhanden" "$(command -v ydotool >/dev/null && echo ja || echo nein)" ja
[[ -n "${WAYLAND_DISPLAY:-}" && -x "$BIN" ]] || { echo "  FAIL Live-Pruefungen uebersprungen"; exit 1; }

# --- set_margin darf fuer die Position nicht benutzt werden -------------------
# Die einzige Quelltextpruefung hier, und sie ist eine Ergaenzung: die WIRKUNG
# misst der configure-Zaehler unten. Ein Zaehler allein wuerde aber auch dann
# gruen bleiben, wenn jemand set_margin baut und der Compositor zufaellig kein
# configure schickt.
chk "set_position wird benutzt" \
  "$(grep -rqE 'set_position\(' "$TARGET/face/src" && echo ja || echo nein)" ja

tmp="$(mktemp -d)"
face=""; fenster=""
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/daimon/face-position.json"
sicherung=""
if [[ -f "$STATE" ]]; then sicherung="$tmp/state.bak"; cp "$STATE" "$sicherung"; fi
aufraeumen() {
  [[ -n "$face" ]] && kill "$face" 2>/dev/null
  [[ -n "$fenster" ]] && kill "$fenster" 2>/dev/null
  # Den Zustand des Nutzers wiederherstellen -- ein Verifizierer, der das Pet
  # verschoben zuruecklaesst, hat eine Nebenwirkung, die niemand bestellt hat.
  if [[ -n "$sicherung" ]]; then cp "$sicherung" "$STATE"; else rm -f "$STATE"; fi
  rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"
}
trap aufraeumen EXIT

klicks="$tmp/klicks.log"; : > "$klicks"
"$SYSPY" "$REPO/tests/harness/vollbildfenster.py" "#204080" "$klicks" 200 >/dev/null 2>&1 &
fenster=$!
sleep 3

starten() {
  DAIMON_MAX_SECS=120 "$BIN" --pet-manifest "$MANIFEST" \
    --sprite-position "${1:-900,500}" --diag-socket "$tmp/d.sock" \
    >"$tmp/out.log" 2>"$tmp/err.log" &
  face=$!
  for _ in $(seq 1 200); do [[ -S "$tmp/d.sock" ]] && return 0; sleep 0.1; done
  return 1
}
diag() { "$PY" - "$tmp/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
feld() { jq -r ".$1" <<<"$(diag)" 2>/dev/null; }

rm -f "$STATE"
starten 900,500
chk "Overlay startet" "$?" 0
sleep 2

d0="$(diag)"
echo "  Diagnose: $d0"
chk "Diagnose kennt configure_empfangen" \
  "$(jq -e 'has("configure_empfangen")' <<<"$d0" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "Diagnose meldet die Sprite-Position" \
  "$(jq -e 'has("sprite_x") and has("sprite_y")' <<<"$d0" >/dev/null 2>&1 && echo ja || echo nein)" ja

x0="$(jq -r '.sprite_x' <<<"$d0")"
y0="$(jq -r '.sprite_y' <<<"$d0")"
conf0="$(jq -r '.configure_empfangen' <<<"$d0")"
HZ="$(getconf CLK_TCK)"
ticks() { awk '{print $14+$15}' "/proc/$face/stat" 2>/dev/null; }

# --- Der Zug ------------------------------------------------------------------
# Erst den Zeiger auf das Pet bringen. Koordinaten helfen nicht; stattdessen
# wird gefahren, bis das Fenster darunter KEINE Klicks mehr meldet -- dann
# liegt der Zeiger ueber der Input-Region des Pets. Dieselbe Methode wie in
# T-1.3, und sie braucht keine einzige Koordinate.
pgrep -x ydotoold >/dev/null || (ydotoold >/dev/null 2>&1 &)
sleep 1
zaehle() { local n; n="$(grep -c '^click ' "$klicks" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] && echo "$n" || echo 0; }
ydotool mousemove -- -9000 -9000 >/dev/null 2>&1; sleep 0.5

# WICHTIG: erst muss ein Klick UNTEN ANKOMMEN, bevor ein ausgebliebener
# etwas bedeutet. Die bekannte Spur auf dieser Maschine beginnt mit
# Fehlmessungen -- wer den ersten ausgebliebenen Klick als "ueber dem Pet"
# nimmt, faengt am Anfang der Spur an und zieht anschliessend im Leeren.
# Genau das stand in der ersten Fassung dieses Skripts und wurde beim
# Gegenlesen gefunden.
angekommen=0
auf_dem_pet=nein
for _ in $(seq 1 80); do
  vor="$(zaehle)"
  ydotool click 0xC0 >/dev/null 2>&1
  sleep 0.15
  if [[ "$(zaehle)" -gt "$vor" ]]; then
    angekommen=$(( angekommen + 1 ))
  elif [[ "$angekommen" -ge 3 ]]; then
    # Erst ab hier ist ein ausgebliebener Klick aussagekraeftig.
    auf_dem_pet=ja; break
  fi
  ydotool mousemove -- 40 26 >/dev/null 2>&1
  sleep 0.1
done
echo "  Klicks unten angekommen, bevor der Zeiger das Pet erreichte: $angekommen"
chk "das Fenster darunter empfaengt ueberhaupt Klicks (Positivkontrolle)" \
  "$([[ "$angekommen" -ge 3 ]] && echo ja || echo nein)" ja
chk "der Zeiger steht ueber dem Pet" "$auf_dem_pet" ja

vor_ticks="$(ticks)"
vor_zeit="$("$PY" -c 'import time; print(time.monotonic())')"
conf_vor="$(feld configure_empfangen)"

# Taste halten, in vielen kleinen Schritten bewegen, loslassen. Wie weit das
# real geht, entscheidet die Zeigerbeschleunigung -- gemessen wird nachher.
ydotool click 0x40 >/dev/null 2>&1     # 0x40 = links druecken (halten)
sleep 0.3
for _ in $(seq 1 40); do
  ydotool mousemove -- 8 4 >/dev/null 2>&1
  sleep 0.03
done
sleep 0.3
ydotool click 0x80 >/dev/null 2>&1     # 0x80 = links loslassen
sleep 1.5

nach_ticks="$(ticks)"
nach_zeit="$("$PY" -c 'import time; print(time.monotonic())')"
d1="$(diag)"
x1="$(jq -r '.sprite_x' <<<"$d1")"
y1="$(jq -r '.sprite_y' <<<"$d1")"
conf_nach="$(jq -r '.configure_empfangen' <<<"$d1")"

strecke="$("$PY" -c "
import math,sys
print(int(round(math.hypot($x1-$x0, $y1-$y0))))" 2>/dev/null)"
cpu="$(awk -v a="$vor_ticks" -v b="$nach_ticks" -v t0="$vor_zeit" -v t1="$nach_zeit" -v hz="$HZ" \
  'BEGIN {d=t1-t0; if (d<=0) d=1; printf "%.3f", (b-a)/hz/d*100}')"

echo "  Position ($x0,$y0) -> ($x1,$y1), Strecke ${strecke} px"
echo "  configure_empfangen $conf_vor -> $conf_nach"
echo "  CPU waehrend des Zugs: $cpu %"

# Positivkontrolle: es wurde ueberhaupt gezogen. Ohne sie waeren "0 configure"
# und "wenig CPU" auch dann gruen, wenn gar nichts passiert ist -- die
# Nullaussage, die dieses Projekt schon mehrfach gekostet hat.
chk "das Pet ist wirklich gewandert (Positivkontrolle)" \
  "$(num_ge "${strecke:-0}" 200)" ja
chk "waehrend des Zugs kam KEIN configure" "$conf_nach" "$conf_vor"
chk "CPU waehrend des Zugs unter 5 %" \
  "$(awk -v c="$cpu" 'BEGIN {print (c+0 < 5.0) ? "ja" : "nein"}')" ja
# Und der Zaehler zaehlt ueberhaupt: beim Start gab es configure-Ereignisse.
chk "configure_empfangen hat ueberhaupt gezaehlt (Positivkontrolle)" \
  "$(num_ge "${conf0:-0}" 1)" ja

# --- Die Position ueberlebt den Neustart --------------------------------------
chk "Zustandsdatei existiert" "$([[ -f "$STATE" ]] && echo ja || echo nein)" ja
kill "$face" 2>/dev/null; wait "$face" 2>/dev/null; face=""
sleep 1
# Bewusst mit einer ANDEREN Vorgabeposition starten: kommt die gemerkte
# durch, kann es nicht die Vorgabe gewesen sein.
starten 100,100
chk "Overlay startet erneut" "$?" 0
sleep 2
x2="$(feld sprite_x)"
y2="$(feld sprite_y)"
echo "  nach dem Neustart: ($x2,$y2), Vorgabe waere (100,100) gewesen"
chk "die gemerkte Position ueberlebt den Neustart" \
  "$([[ "$x2" == "$x1" && "$y2" == "$y1" ]] && echo ja || echo nein)" ja
chk "es ist nicht die Vorgabeposition" \
  "$([[ "$x2" != "100" || "$y2" != "100" ]] && echo ja || echo nein)" ja
kill "$face" 2>/dev/null; wait "$face" 2>/dev/null; face=""

# --- Eine kaputte Zustandsdatei bringt nichts um ------------------------------
echo '{kaputt' > "$STATE"
starten 100,100
chk "Overlay startet trotz kaputter Zustandsdatei" "$?" 0
sleep 2
chk "es faellt auf die Vorgabeposition zurueck" "$(feld sprite_x)" 100
chk "der Prozess lebt" "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja

exit $fail
