#!/usr/bin/env bash
# Verifizierer fuer T-1.3: Input-Region und Click-Through.
#
# Die teuerste Zusage im ganzen Projekt. Eine Wayland-Surface OHNE gesetzte
# input_region nimmt Eingaben auf ihrer GANZEN Flaeche entgegen. Bei einer
# bildschirmfuellenden Layer-Surface heisst das: der Rechner ist mit der Maus
# nicht mehr bedienbar, und im Journal steht nichts, weil aus Sicht des
# Compositors alles korrekt ist. Am 2026-07-27 real passiert.
#
# Geprueft wird mit echten Klicks ueber die Vorrichtung aus T-1.8
# (`tests/harness/vollbildfenster.py`, ein Vollbildfenster mit Klickzaehler)
# und ydotool -- aber KOORDINATENFREI. Der Grund steht ausfuehrlich in
# Abschnitt B: der Zeiger laesst sich auf dieser Maschine nicht gezielt
# positionieren, weder absolut noch relativ. Gemessen wird deshalb eine Spur
# quer ueber den Schirm und der Unterschied zwischen "der Klick kommt unten
# an" und "er kommt nicht an".
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
SYSPY="/usr/bin/python3"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"

SPX=900; SPY=500          # Sprite-Position
ZW=192; ZH=208            # Zellmasse laut pet.json

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-1.3 — Input-Region und Click-Through"

# Immer bauen, auch einen Fixture-Baum: `cargo test` baut das Bin-Target
# nicht, und ein Verifizierer, der ein Binary von vorgestern startet, misst
# den Stand von vorgestern. Bei T-1.4 real passiert.
# Ein Fixture-Baum wird in ein TEMPORAERES Zielverzeichnis gebaut. Sonst
# laege in tests/fixtures und tests/mutants je ein kompiliertes Rust-Target
# -- zig Megabyte, die niemand in der Historie haben will, und beim Kopieren
# eines Fixtures waere das Binary der UNMUTIERTEN Quelle mitgekommen. Genau
# das ist beim ersten Anlauf passiert: beide Mutanten bestanden, weil sie das
# alte Binary starteten.
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
chk "Pruefling vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja
chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
chk "ydotool vorhanden" "$(command -v ydotool >/dev/null && echo ja || echo nein)" ja
chk "Vollbildfenster-Vorrichtung vorhanden" \
  "$([[ -f "$REPO/tests/harness/vollbildfenster.py" ]] && echo ja || echo nein)" ja
[[ -x "$BIN" && -n "${WAYLAND_DISPLAY:-}" ]] || exit 1

tmp="$(mktemp -d)"
face=""; fenster=""
trap '[[ -n "$face" ]] && kill "$face" 2>/dev/null; [[ -n "$fenster" ]] && kill "$fenster" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

klicks="$tmp/klicks.log"; : > "$klicks"
"$SYSPY" "$REPO/tests/harness/vollbildfenster.py" "#204080" "$klicks" 180 >/dev/null 2>&1 &
fenster=$!
sleep 3

DAIMON_MAX_SECS=150 WAYLAND_DEBUG=1 "$BIN" --pet-manifest "$MANIFEST" \
  --sprite-position "$SPX,$SPY" --diag-socket "$tmp/d.sock" \
  >"$tmp/out.log" 2>"$tmp/wl.log" &
face=$!
for _ in $(seq 1 150); do grep -q READY "$tmp/out.log" 2>/dev/null && break; sleep 0.1; done
chk "Overlay meldet READY" "$(grep -q READY "$tmp/out.log" && echo ja || echo nein)" ja
sleep 2

# --- A. Die Region steht, und sie steht VOR dem ersten Commit -----------------
# Positivkontrolle: der Mitschnitt zeigt ueberhaupt Verkehr. Ein leeres Log
# machte jede Aussage darunter wertlos.
chk "Protokoll-Mitschnitt ist aktiv (Positivkontrolle)" \
  "$(grep -q 'wl_surface@' "$tmp/wl.log" && echo ja || echo nein)" ja
chk "set_input_region wird ueberhaupt gerufen" \
  "$(grep -q 'set_input_region' "$tmp/wl.log" && echo ja || echo nein)" ja
# Die Reihenfolge im Mitschnitt ist die Reihenfolge auf der Leitung: die
# erste Region muss vor dem ersten Commit stehen. Danach waere zu spaet --
# zwischen erstem Commit und Nachreichen ist die Surface schon sichtbar und
# schluckt alles.
erste_region="$(grep -n 'set_input_region' "$tmp/wl.log" | head -1 | cut -d: -f1)"
erster_commit="$(grep -n '\.commit(' "$tmp/wl.log" | head -1 | cut -d: -f1)"
echo "  erste set_input_region in Zeile $erste_region, erster commit in Zeile $erster_commit"
chk "die Region steht VOR dem ersten Commit" \
  "$([[ -n "$erste_region" && -n "$erster_commit" && "$erste_region" -lt "$erster_commit" ]] && echo ja || echo nein)" ja
# Zwei Surfaces, zwei eigene Regionen: die Elternregion beschneidet eine
# Subsurface NICHT.
regionen="$(grep -c 'set_input_region' "$tmp/wl.log")"
echo "  set_input_region-Aufrufe: $regionen"
chk "jede Surface hat eine eigene Region (>= 2)" \
  "$([[ "$regionen" -ge 2 ]] && echo ja || echo nein)" ja

# --- B. Echte Klicks, koordinatenfrei gemessen --------------------------------
#
# ACHTUNG, teuer gelernt: `ydotool mousemove` mit ABSOLUTEN Zielen
# positioniert auf dieser Maschine gar nicht (alles landet bei (0,0), Exit 0,
# keine Meldung). Und RELATIVE Bewegungen laufen durch die
# Zeigerbeschleunigung des Compositors -- eine Bewegung um "996" ist NICHT
# 996 Pixel. Gemessen: Zeiger nominell auf (996,604) geschickt, der Klick kam
# bei (3984,1439) an.
#
# Ein Test, der auf berechneten Bildschirmkoordinaten klickt, misst deshalb
# irgendwo. Genau das hat die erste Fassung dieses Verifizierers getan und
# daraus einen Befund gegen das Face gemacht, den es nicht gab.
#
# Deshalb wird koordinatenfrei gemessen: der Zeiger faehrt in kleinen
# relativen Schritten ueber den Schirm und klickt bei jedem Schritt. Das
# Fenster darunter zaehlt mit. Wo die Klicks AUSBLEIBEN, hat das Overlay sie
# aufgefangen. Das braucht keine einzige Koordinate -- nur den Unterschied
# zwischen "kommt an" und "kommt nicht an".
pgrep -x ydotoold >/dev/null || (ydotoold >/dev/null 2>&1 &)
sleep 1
zaehle() { local n; n="$(grep -c '^click ' "$klicks" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] && echo "$n" || echo 0; }

# Ausgangspunkt: links oben. Eine sehr grosse Relativbewegung landet dort,
# egal wie stark beschleunigt wird.
ydotool mousemove -- -9000 -9000 >/dev/null 2>&1
sleep 0.5

schritte=60
geblockt=0
angekommen=0
spur=""
for i in $(seq 1 $schritte); do
  vor="$(zaehle)"
  ydotool click 0xC0 >/dev/null 2>&1
  sleep 0.18
  nach="$(zaehle)"
  if [[ "$nach" -gt "$vor" ]]; then
    angekommen=$(( angekommen + 1 )); spur="${spur}."
  else
    geblockt=$(( geblockt + 1 )); spur="${spur}#"
  fi
  # Kleiner Schritt nach rechts unten, quer ueber die Sprite-Gegend.
  ydotool mousemove -- 40 26 >/dev/null 2>&1
  sleep 0.12
done
echo "  Spur ueber $schritte Schritte (. = kam unten an, # = vom Overlay gefangen):"
echo "  $spur"
echo "  angekommen=$angekommen  geblockt=$geblockt"

# Positivkontrolle: unterwegs kommen Klicks unten an. Ohne sie waere "geblockt"
# auch bei einem toten Testfenster gruen -- die Nullaussage, die dieses
# Projekt schon mehrfach gekostet hat.
chk "das Fenster darunter empfaengt ueberhaupt Klicks (Positivkontrolle)" \
  "$([[ "$angekommen" -ge 10 ]] && echo ja || echo nein)" ja
# Der eigentliche Test: irgendwo auf der Spur faengt das Overlay Klicks ab.
chk "das Overlay faengt Klicks ueber dem Pet ab" \
  "$([[ "$geblockt" -ge 1 ]] && echo ja || echo nein)" ja
# Und es faengt nicht alles: ein Overlay, das den ganzen Schirm schluckt, ist
# der Ausfall vom 27.07.
chk "das Overlay faengt NICHT alles ab" \
  "$([[ "$angekommen" -gt "$geblockt" ]] && echo ja || echo nein)" ja

# --- B2. Der Alpha-Test, am Protokoll-Mitschnitt gemessen ---------------------
# Ob ein Klick auf eine DURCHSICHTIGE Ecke durchgeht, laesst sich mit einem
# Zeiger, der seine Position nicht kennt, nicht gezielt pruefen. Belegbar ist
# es an der Region selbst: sie besteht aus vielen schmalen Rechtecken (den
# sichtbaren Zeilenlaeufen) statt aus einem einzigen ueber die ganze Zelle.
# Ein einzelnes Rechteck 192x208 waere genau der fehlende Alpha-Test.
zell_rechteck="$(grep -cE 'wl_region@[0-9]+\.add\([0-9]+, [0-9]+, 192, 208\)' "$tmp/wl.log")"
[[ "$zell_rechteck" =~ ^[0-9]+$ ]] || zell_rechteck=0
laeufe="$(grep -cE 'wl_region@[0-9]+\.add\([0-9]+, [0-9]+, [0-9]+, 1\)' "$tmp/wl.log")"
[[ "$laeufe" =~ ^[0-9]+$ ]] || laeufe=0
echo "  Zeilenlaeufe in der Sprite-Region: $laeufe"
# Positivkontrolle: es gibt ueberhaupt Rechtecke. Sonst waere "keine volle
# Zelle" auch bei einer leeren Region gruen.
chk "die Sprite-Region besteht aus Zeilenlaeufen (Positivkontrolle)" \
  "$([[ "$laeufe" -ge 10 ]] && echo ja || echo nein)" ja
chk "die Sprite-Region ist NICHT die volle Zelle (Alpha-Test wirkt)" \
  "$([[ "$laeufe" -gt "$zell_rechteck" ]] && echo ja || echo nein)" ja

# --- C. Die Region wird nur bei Aenderung neu gesetzt -------------------------
# Jedes set_input_region erzwingt beim Compositor eine Neuberechnung. Bei
# gleichbleibendem Sprite darf nichts passieren -- sonst faellt die
# Idle-CPU-Zusage aus T-1.5.
vor_ruhe="$(grep -c 'set_input_region' "$tmp/wl.log")"
sleep 8
nach_ruhe="$(grep -c 'set_input_region' "$tmp/wl.log")"
echo "  set_input_region waehrend 8 s Ruhe: $vor_ruhe -> $nach_ruhe"
chk "in Ruhe wird die Region nicht neu gesetzt" "$nach_ruhe" "$vor_ruhe"

exit $fail
