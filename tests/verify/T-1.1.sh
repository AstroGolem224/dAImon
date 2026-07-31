#!/usr/bin/env bash
# Verifizierer fuer T-1.1: Rust-Projekt und Layer-Surface.
#
# Der Plan ist an einer Stelle ungewoehnlich deutlich, und das aus Erfahrung:
# `supportInformation` ist eine FAEHIGKEITSAUSKUNFT, keine Surface-Inventur --
# es sagt, was KWin kann, nicht was gerade auf dem Schirm liegt. Sichtbarkeit
# wird deshalb per Screenshot und Pixelprobe belegt, ueber einem
# Vollbildfenster, oder gar nicht.
#
# Die Probe liegt in `tests/harness/pixelprobe.py` und macht drei Aufnahmen:
#
#   A  nur das Vollbildfenster     Sonde == Vollbildfarbe   (Vorher-Beleg)
#   B  Vollbildfenster + Overlay   Sonde != Vollbildfarbe   (der Test)
#                                  Kontrolle == Vollbildfarbe
#   C  nach dem Beenden            Sonde == Vollbildfarbe
#
# Die Vollbildfarbe wird JE LAUF gewuerfelt. Damit ist eine weggelassene
# Vorher-Aufnahme nicht zu kaschieren -- wer nur B prueft, kann nicht wissen,
# ob dort vorher schon etwas anderes stand.
#
# Ein DAIMON_FIXTURE zeigt auf einen Baum mit einem `face/`-Crate; der wird
# dann gebaut und geprueft. Die Assets kommen immer aus dem echten Repo: die
# Mutanten unterscheiden sich im Quelltext, nicht im Sprite-Sheet.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
CRATE="$TARGET/face"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"
SYSPY="/usr/bin/python3"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
hat() { grep -qE "$2" "$1" 2>/dev/null && echo ja || echo nein; }
num_gt() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 > $2 )); then echo ja; else echo nein; fi; }

echo "T-1.1 — Rust-Projekt und Layer-Surface"

chk "face-Crate vorhanden" "$([[ -f "$CRATE/Cargo.toml" ]] && echo ja || echo nein)" ja
[[ -f "$CRATE/Cargo.toml" ]] || exit 1

# --- A. Crates nach Design 8.1 ------------------------------------------------
# Diese Pruefung haengt am Text, weil "welche Crates" eine Aussage ueber die
# Abhaengigkeitsliste ist. Die WIRKUNG -- kein GPU-Kontext -- prueft T-1.4.sh
# am Adressraum des laufenden Prozesses.
echo "  -- Abhaengigkeiten"
chk "smithay-client-toolkit" "$(hat "$CRATE/Cargo.toml" '^smithay-client-toolkit')" ja
chk "wayland-client" "$(hat "$CRATE/Cargo.toml" '^wayland-client')" ja
chk "wayland-protocols-wlr" "$(hat "$CRATE/Cargo.toml" '^wayland-protocols-wlr')" ja
# Kein Toolkit mit eigenem Renderer und kein Audio-Stack: beides zoege
# Bibliotheken in den Adressraum, die 8.1 ausschliesst.
chk "kein gtk/winit/eframe/wgpu" \
  "$(grep -qE '^(gtk|winit|eframe|wgpu|glutin|vulkano)' "$CRATE/Cargo.toml" && echo nein || echo ja)" ja

# --- B. Die Layer-Surface-Eigenschaften ---------------------------------------
# Auch das ist Quelltext -- die Eigenschaften einer Surface sind von aussen
# nicht auslesbar. Die WIRKUNG von `Layer::Overlay` prueft die Pixelprobe
# unten: ueber einem Vollbildfenster liegt nur, was auf Overlay liegt.
echo "  -- Layer-Surface"
QUELLE="$CRATE/src"
chk "Layer::Overlay" \
  "$(grep -rqE 'Layer::Overlay' "$QUELLE" && echo ja || echo nein)" ja
chk "kein Layer::Top" \
  "$(grep -rqE 'set_layer\(Layer::Top\)|Layer::Top,' "$QUELLE" && echo nein || echo ja)" ja
chk "Anker rundum" \
  "$(grep -rqE 'Anchor::TOP \| Anchor::BOTTOM \| Anchor::LEFT \| Anchor::RIGHT' "$QUELLE" && echo ja || echo nein)" ja
chk "exclusive_zone = -1" \
  "$(grep -rqE 'set_exclusive_zone\(-1\)' "$QUELLE" && echo ja || echo nein)" ja
chk "keyboard_interactivity None" \
  "$(grep -rqE 'KeyboardInteractivity::None' "$QUELLE" && echo ja || echo nein)" ja
chk "wl_output explizit gebunden" \
  "$(grep -rqE 'Some\(output\)' "$QUELLE" && echo ja || echo nein)" ja
chk "opaque_region wird gesetzt" \
  "$(grep -rqE 'set_opaque_region' "$QUELLE" && echo ja || echo nein)" ja
chk "1x1-Puffer" \
  "$(grep -rqE 'create_buffer\(1, 1, 4' "$QUELLE" && echo ja || echo nein)" ja
# Bug 503121: nach einem NULL-Buffer-Unmap liefert KWin ohne erneut gesetzte
# Layer-Properties kein neues configure. Im Spike 0/20 ohne, 20/20 mit.
chk "Umgehung fuer Bug 503121 uebernommen" \
  "$(grep -rqE 'properties_neu_setzen|503121' "$QUELLE" && echo ja || echo nein)" ja

# --- C. Bauen -----------------------------------------------------------------
# IMMER bauen. `cargo test` baut den Unit-Test-Harness, nicht das Bin-Target;
# ein Verifizierer, der ein Binary von vorgestern startet, misst den Stand von
# vorgestern und bleibt gruen, waehrend die Quelle kaputt ist. Bei T-1.4 real
# passiert.
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
chk "Binary vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja
neuer="$(find "$CRATE/src" "$CRATE/Cargo.toml" -newer "$BIN" 2>/dev/null | head -1)"
chk "Binary ist nicht aelter als die Quellen" \
  "$([[ -z "$neuer" ]] && echo ja || echo nein)" ja

# --- D. Lebt der Prozess nach 10 s? -------------------------------------------
tmp="$(mktemp -d)"
pid=""
trap '[[ -n "$pid" ]] && kill "$pid" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

if [[ -n "${WAYLAND_DISPLAY:-}" && -x "$BIN" ]]; then
  DAIMON_MAX_SECS=30 "$BIN" --pet-manifest "$MANIFEST" \
    --diag-socket "$tmp/d.sock" >"$tmp/out.log" 2>"$tmp/err.log" &
  pid=$!
  sleep 10
  chk "Prozess lebt nach 10 s" "$(kill -0 "$pid" 2>/dev/null && echo ja || echo nein)" ja
  chk "READY wurde gemeldet" "$(grep -q READY "$tmp/out.log" && echo ja || echo nein)" ja
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; pid=""
else
  chk "Wayland-Sitzung vorhanden" nein ja
fi

# --- E. Sichtbarkeit ueber einem Vollbildfenster -------------------------------
chk "Pixelprobe vorhanden" \
  "$([[ -f "$REPO/tests/harness/pixelprobe.py" ]] && echo ja || echo nein)" ja
chk "spectacle vorhanden" "$(command -v spectacle >/dev/null && echo ja || echo nein)" ja

if [[ -n "${WAYLAND_DISPLAY:-}" && -x "$BIN" ]]; then
  probe="$(timeout 300 "$SYSPY" "$REPO/tests/harness/pixelprobe.py" \
    "$tmp" "$BIN" "$MANIFEST" 2>"$tmp/probe.err")"
  probe_rc=$?
  chk "Pixelprobe lief durch" "$probe_rc" 0
  grep '^#' <<<"$probe" | sed 's/^#/  /'
  [[ "$probe_rc" -eq 0 ]] || sed 's/^/    | /' "$tmp/probe.err" | head -10

  wert() { grep -m1 "^$1=" <<<"$probe" | cut -d= -f2; }
  # Erst die Positivkontrollen des Aufbaus, dann die Aussagen, die auf ihnen
  # ruhen. Ohne A waere B nicht interpretierbar: eine Sonde, die nie die
  # Vollbildfarbe zeigte, koennte auch den Desktop fotografieren.
  chk "A: das Vollbildfenster deckt die Sonde (Vorher-Beleg)" \
    "$(wert A_vollbildfenster_deckt_die_sonde)" ja
  chk "A: das Vollbildfenster deckt den Kontrollpunkt" \
    "$(wert A_vollbildfenster_deckt_die_kontrolle)" ja
  chk "Overlay startet" "$(wert overlay_startet)" ja
  chk "B: das Overlay liegt UEBER dem Vollbildfenster" \
    "$(wert B_overlay_liegt_ueber_dem_vollbildfenster)" ja
  chk "B: daneben bleibt es durchsichtig" \
    "$(wert B_daneben_bleibt_durchsichtig)" ja
  chk "C: nach dem Beenden ist die Vollbildfarbe zurueck" \
    "$(wert C_nach_dem_beenden_ist_die_farbe_zurueck)" ja
fi

# --- F. Das Fenster darunter bekommt weiterhin Eingaben ------------------------
# Der Plan verlangt das ausdruecklich getrennt: ein Overlay, das sichtbar ist
# und dabei den Desktop unbedienbar macht, hat die Haelfte der Zusage
# gebrochen -- und genau das ist am 27.07. passiert.
if [[ -n "${WAYLAND_DISPLAY:-}" && -x "$BIN" ]]; then
  klicks="$tmp/klicks2.log"
  : > "$klicks"
  "$SYSPY" "$REPO/tests/harness/vollbildfenster.py" "#204080" "$klicks" 60 \
    >/dev/null 2>&1 &
  fenster_pid=$!
  sleep 3
  DAIMON_MAX_SECS=40 "$BIN" --pet-manifest "$MANIFEST" \
    --sprite-position 900,500 --diag-socket "$tmp/d2.sock" \
    >/dev/null 2>&1 &
  pid=$!
  for _ in $(seq 1 100); do [[ -S "$tmp/d2.sock" ]] && break; sleep 0.1; done
  sleep 2

  if command -v ydotool >/dev/null 2>&1; then
    pgrep -x ydotoold >/dev/null || (ydotoold >/dev/null 2>&1 &)
    sleep 1
    # `ydotool mousemove -a` positioniert auf dieser Maschine nicht -- jedes
    # absolute Ziel landet bei (0,0), Exit 0, keine Meldung. Deshalb wie im
    # Spike T-1.8: erst weit nach links oben schieben (homen), dann relativ.
    ydotool mousemove -- -9000 -9000 >/dev/null 2>&1
    sleep 0.3
    # Weit weg vom Sprite bei 900,500 (192x208), aber im Vollbildfenster.
    ydotool mousemove -- 400 1000 >/dev/null 2>&1
    sleep 0.3
    vorher="$(grep -c '^click ' "$klicks" 2>/dev/null)"
    [[ "$vorher" =~ ^[0-9]+$ ]] || vorher=0
    for _ in 1 2 3 4 5; do ydotool click 0xC0 >/dev/null 2>&1; sleep 0.25; done
    sleep 1
    nachher="$(grep -c '^click ' "$klicks" 2>/dev/null)"
    [[ "$nachher" =~ ^[0-9]+$ ]] || nachher=0
    echo "  Klicks im Fenster darunter: $vorher -> $nachher"
    chk "das Fenster darunter empfaengt weiterhin Eingaben" \
      "$(num_gt "$nachher" "$vorher")" ja
  else
    chk "ydotool vorhanden" nein ja
  fi
  kill "$pid" 2>/dev/null; pid=""
  kill "$fenster_pid" 2>/dev/null
fi

exit $fail
