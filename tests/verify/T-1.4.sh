#!/usr/bin/env bash
# Verifizierer fuer T-1.4: Sprite-Subsurface und SlotPool.
#
# Geprueft wird gegen die Akzeptanzliste, am LAUFENDEN Prozess, nicht am
# Quelltext. Jede Messung bekommt, wo es geht, eine Positivkontrolle: eine
# "0 Treffer"-Aussage ist nur dann etwas wert, wenn vorher bewiesen ist,
# dass die Messung etwas haette finden koennen.
#
# Was dieses Skript bewusst NICHT prueft (und auch nicht vortaeuscht):
# - "einmal beim Start dekodiert" und "premultiplied ARGB8888": Pixel-Format
#   und Dekodier-Haeufigkeit liegen im Prozessinneren; ohne Syscall-Mitschnitt
#   oder Pixelprobe mit bekanntem Alphawert von aussen nicht beobachtbar.
#   Indirekt gedeckt: READY + steigende frames_rendered zeigen, dass ein
#   dekodiertes Sheet gerendert wird.
# - Atlas-Geometrie (Zelle 192x208, 8 Spalten, 9 Zeilen): steht nur im
#   Quelltext und in pet.json. Indirekt gedeckt: das unveraenderte
#   Community-Pet laedt und rendert (Abschnitt C).
# - "Bewegung ueber set_position() ohne Neuzeichnen": ein Protokoll-Mitschnitt
#   (WAYLAND_DEBUG) zeigt, OB set_position gerufen wird; ob dabei kein Buffer
#   neu gezeichnet wurde, liesse sich nur durch Korrelation mit attach/commit
#   belegen -- das waere hier eine Scheinpruefung. set_position wird daher nur
#   als INFO gezaehlt, nicht als Kriterium.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
BIN="$REPO/face/target/debug/daimon-face"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
# Zahl groesser: ja/nein, ungueltige Eingaben werden zu nein statt zu einem
# stillen Vergleichsfehler.
num_gt() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 > $2 )); then echo ja; else echo nein; fi; }
ts_gt() { awk -v a="$1" -v b="$2" 'BEGIN {print (a+0 > b+0) ? "ja" : "nein"}'; }

echo "T-1.4 — Sprite-Subsurface und SlotPool"

# --- A. Build und cargo test -----------------------------------------------
# IMMER bauen, nicht nur wenn das Binary fehlt. `cargo test -p face` baut nur
# den Unit-Test-Harness, nicht das Bin-Target -- ein Verifizierer, der ein
# veraltetes Binary startet, misst den Stand von vorgestern und bleibt gruen,
# waehrend die Quelle kaputt ist. Genau das ist beim ersten Mutanten passiert.
( cd "$REPO/face" && timeout 600 cargo build -p face ) >/dev/null 2>&1
build_rc=$?
chk "cargo build -p face laeuft durch" "$build_rc" 0
chk "Binary daimon-face vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja
# Positivkontrolle gegen genau diesen Fehler: das Binary darf nicht aelter
# sein als die juengste Quelldatei.
neuester_quellstand="$(find "$REPO/face/src" "$REPO/face/Cargo.toml" -newer "$BIN" 2>/dev/null | head -1)"
chk "Binary ist nicht aelter als die Quellen" \
  "$([[ -z "$neuester_quellstand" ]] && echo ja || echo nein)" ja

ct_out="$(cd "$REPO/face" && timeout 600 cargo test -p face 2>&1)"
ct_rc=$?
# Maschinell auswerten: passed/failed ueber alle "test result:"-Zeilen summieren.
read -r ct_passed ct_failed <<<"$(awk '/^test result:/ {p+=$4; f+=$6} END {print p+0, f+0}' <<<"$ct_out")"
echo "  cargo test: $ct_passed passed, $ct_failed failed (rc=$ct_rc)"
chk "cargo test -p face laeuft durch" "$ct_rc" 0
chk "keine fehlgeschlagenen Rust-Tests" "$ct_failed" 0
chk "es liefen ueberhaupt Tests (Positivkontrolle)" "$(num_gt "$ct_passed" 0)" ja

# --- B. Live: Zustandswechsel ueber den Steuer-Socket -----------------------
wayland_da="$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)"
chk "Wayland-Sitzung vorhanden" "$wayland_da" ja

tmp="$(mktemp -d)"
pid=""
pid_pet=""
cleanup() {
  [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
  [[ -n "$pid_pet" ]] && kill "$pid_pet" 2>/dev/null
  [[ -n "$pid" ]] && wait "$pid" 2>/dev/null
  [[ -n "$pid_pet" ]] && wait "$pid_pet" 2>/dev/null
  rm -rf "$tmp"
}
trap cleanup EXIT

# Eine Zeile Diagnose-JSON vom Diagnose-Socket lesen.
diag() { "$PY" - "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
# Eine Zeile an den Steuer-Socket schicken, Antwortzeile zurueckgeben.
ctl() { "$PY" - "$1" "$2" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

if [[ "$wayland_da" == ja && -x "$BIN" ]]; then
  # WAYLAND_DEBUG=1: libwayland schreibt den Protokoll-Mitschnitt auf stderr;
  # er liefert den Beleg fuer wl_subsurface/set_desync am laufenden Prozess.
  DAIMON_MAX_SECS=15 WAYLAND_DEBUG=1 "$BIN" \
    --diag-socket "$tmp/diag.sock" --control-socket "$tmp/ctl.sock" \
    >"$tmp/out.log" 2>"$tmp/wl.log" &
  pid=$!

  ready=""
  for _ in $(seq 1 100); do
    ready="$(grep -oE 'READY pid=[0-9]+' "$tmp/out.log" 2>/dev/null | head -1)"
    [[ -n "$ready" ]] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  chk "Overlay meldet READY" "$([[ -n "$ready" ]] && echo ja || echo nein)" ja
  chk "READY-Pid ist der gestartete Prozess" "${ready#READY pid=}" "$pid"

  chk "diag.sock existiert" "$([[ -S "$tmp/diag.sock" ]] && echo ja || echo nein)" ja
  chk "diag.sock hat Modus 0600" "$(stat -c '%a' "$tmp/diag.sock" 2>/dev/null)" 600
  chk "control.sock existiert" "$([[ -S "$tmp/ctl.sock" ]] && echo ja || echo nein)" ja
  chk "control.sock hat Modus 0600" "$(stat -c '%a' "$tmp/ctl.sock" 2>/dev/null)" 600

  d1="$(diag "$tmp/diag.sock" 2>/dev/null)"
  # Positivkontrolle: die Diagnose ist parsebar und enthaelt die Pflichtfelder;
  # sonst waere jeder Vergleich darauf wertlos.
  chk "Diagnose-JSON enthaelt sprite/frames_rendered/last_render_ts" \
    "$(jq -e 'has("sprite") and has("frames_rendered") and has("last_render_ts")' \
       <<<"$d1" >/dev/null 2>&1 && echo ja || echo nein)" ja
  sprite1="$(jq -r '.sprite // empty' <<<"$d1" 2>/dev/null)"
  frames1="$(jq -r '.frames_rendered // empty' <<<"$d1" 2>/dev/null)"
  ts1="$(jq -r '.last_render_ts // empty' <<<"$d1" 2>/dev/null)"
  chk "sprite ist anfangs nicht leer" "$([[ -n "$sprite1" ]] && echo ja || echo nein)" ja

  # Positivkontrolle Steuerkanal: Unsinn muss mit "err" abgewiesen werden --
  # sonst beweist ein "ok" auf "state dringend" nichts.
  chk "Steuer-Socket weist Unsinn mit err ab" \
    "$(ctl "$tmp/ctl.sock" 'state gibt-es-nicht' 2>/dev/null)" err

  chk "Wechsel auf dringend wird mit ok bestaetigt" \
    "$(ctl "$tmp/ctl.sock" 'state dringend' 2>/dev/null)" ok
  sleep 1
  d2="$(diag "$tmp/diag.sock" 2>/dev/null)"
  sprite2="$(jq -r '.sprite // empty' <<<"$d2" 2>/dev/null)"
  frames2="$(jq -r '.frames_rendered // empty' <<<"$d2" 2>/dev/null)"
  ts2="$(jq -r '.last_render_ts // empty' <<<"$d2" 2>/dev/null)"
  chk "sprite hat sich geaendert (ruhig -> dringend)" \
    "$([[ -n "$sprite2" && "$sprite2" != "$sprite1" ]] && echo ja || echo nein)" ja
  chk "sprite traegt den neuen Zustand dringend" \
    "$(grep -q dringend <<<"$sprite2" && echo ja || echo nein)" ja
  chk "frames_rendered ist gestiegen" "$(num_gt "$frames2" "$frames1")" ja
  chk "last_render_ts ist spaeter" "$(ts_gt "$ts2" "$ts1")" ja

  # Ein Wechsel, der nur in eine Richtung geht, ist kein Wechsel.
  chk "Wechsel zurueck auf ruhig wird mit ok bestaetigt" \
    "$(ctl "$tmp/ctl.sock" 'state ruhig' 2>/dev/null)" ok
  sleep 1
  d3="$(diag "$tmp/diag.sock" 2>/dev/null)"
  sprite3="$(jq -r '.sprite // empty' <<<"$d3" 2>/dev/null)"
  frames3="$(jq -r '.frames_rendered // empty' <<<"$d3" 2>/dev/null)"
  ts3="$(jq -r '.last_render_ts // empty' <<<"$d3" 2>/dev/null)"
  chk "sprite hat sich zurueckgeaendert (dringend -> ruhig)" \
    "$([[ -n "$sprite3" && "$sprite3" != "$sprite2" ]] && echo ja || echo nein)" ja
  chk "sprite traegt wieder den Zustand ruhig" \
    "$(grep -q ruhig <<<"$sprite3" && echo ja || echo nein)" ja
  chk "frames_rendered steigt weiter" "$(num_gt "$frames3" "$frames2")" ja
  chk "last_render_ts ist wieder spaeter" "$(ts_gt "$ts3" "$ts2")" ja

  # --- wl_subsurface / set_desync aus dem Protokoll-Mitschnitt --------------
  # Positivkontrolle: der Mitschnitt muss ueberhaupt Wayland-Verkehr zeigen;
  # ein leeres wl.log wuerde ein fehlendes set_desync unbeobachtbar machen.
  chk "Protokoll-Mitschnitt ist aktiv (wl_surface im Log)" \
    "$(grep -q 'wl_surface@' "$tmp/wl.log" 2>/dev/null && echo ja || echo nein)" ja
  chk "wl_subsurface wird benutzt" \
    "$(grep -q 'wl_subsurface@' "$tmp/wl.log" 2>/dev/null && echo ja || echo nein)" ja
  chk "set_desync() wurde gerufen" \
    "$(grep -q 'set_desync' "$tmp/wl.log" 2>/dev/null && echo ja || echo nein)" ja
  echo "  INFO set_position-Aufrufe im Mitschnitt: $(grep -c 'set_position' "$tmp/wl.log" 2>/dev/null || echo 0) (ob ohne Neuzeichnen, ist daraus nicht ableitbar)"

  # Jede committete Surface braucht eine EIGENE gesetzte input_region. Eine
  # Subsurface ohne eigene Region nimmt Eingaben auf ihrer ganzen Flaeche an;
  # die Region der Elternsurface beschneidet sie NICHT. Im ersten Wurf war
  # genau das der Fall, und das Sicherheitsgate war damit nur scheinbar aktiv.
  # Zwei Surfaces (Layer + Sprite-Subsurface) => mindestens zwei Aufrufe.
  regionen="$(grep -c 'set_input_region' "$tmp/wl.log" 2>/dev/null || echo 0)"
  echo "  set_input_region-Aufrufe im Mitschnitt: $regionen"
  chk "jede Surface hat eine eigene input_region (>= 2 Aufrufe)" \
    "$(num_gt "$regionen" 1)" ja

  # --- GPU-Freiheit am laufenden Prozess -------------------------------------
  # Positivkontrolle zuerst: maps muss lesbar und nichtleer sein, sonst ist
  # "0 GPU-Bibliotheken" nicht interpretierbar.
  map_zeilen="$(wc -l <"/proc/$pid/maps" 2>/dev/null || echo 0)"
  chk "/proc/<pid>/maps ist lesbar und nichtleer (Positivkontrolle)" \
    "$(num_gt "$map_zeilen" 0)" ja
  gpu_libs="$(grep -cE 'libEGL|libGL|libvulkan|libgbm' "/proc/$pid/maps" 2>/dev/null || true)"
  echo "  GPU-Bibliotheken im Adressraum: ${gpu_libs:-unlesbar}"
  chk "keine libEGL/libGL/libvulkan/libgbm geladen" "${gpu_libs:-1}" 0

  fd_zahl="$(ls "/proc/$pid/fd" 2>/dev/null | wc -l)"
  chk "/proc/<pid>/fd ist lesbar und nichtleer (Positivkontrolle)" \
    "$(num_gt "$fd_zahl" 0)" ja
  dri="$(ls -l "/proc/$pid/fd" 2>/dev/null | grep -c '/dev/dri/' || true)"
  echo "  DRI-Deskriptoren: ${dri:-unlesbar}"
  chk "keine /dev/dri-Deskriptoren" "${dri:-1}" 0

  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; pid=""

  # --- C. Unveraendertes Community-Pet laedt ----------------------------------
  # Die Assets werden unveraendert kopiert und das Overlay per --pet-manifest
  # AUF DIE KOPIE gezeigt. Die erste Fassung startete das Binary nur mit
  # diesem Arbeitsverzeichnis -- und mass damit gar nichts: das Binary loeste
  # den Vorgabepfad zur Bauzeit auf und las weiter die Repo-Assets. Der Test
  # waere auch bei zerstoerter Kopie gruen geblieben.
  # NICHT geprueft wird: ob ein ANDERES Pet als das mitgelieferte ladbar waere
  # (im Repo liegt nur dieses eine) und ob die gerenderten Pixel stimmen.
  if [[ -d "$REPO/face/assets" ]]; then
    mkdir -p "$tmp/petrun"
    cp -a "$REPO/face/assets" "$tmp/petrun/assets"
    DAIMON_MAX_SECS=10 "$BIN" \
        --pet-manifest "$tmp/petrun/assets/pet.json" \
        --diag-socket "$tmp/pet.sock" --control-socket "$tmp/petctl.sock" \
        >"$tmp/pet.log" 2>&1 &
    pid_pet=$!
    pet_ready=""
    for _ in $(seq 1 100); do
      pet_ready="$(grep -oE 'READY pid=[0-9]+' "$tmp/pet.log" 2>/dev/null | head -1)"
      [[ -n "$pet_ready" ]] && break
      kill -0 "$pid_pet" 2>/dev/null || break
      sleep 0.1
    done
    chk "unveraendertes Community-Pet: Overlay meldet READY" \
      "$([[ -n "$pet_ready" ]] && echo ja || echo nein)" ja
    # Positivkontrolle: das Binary muss die KOPIE gelesen haben, nicht die
    # Repo-Assets. Ohne diese Zeile ist das READY oben nicht interpretierbar.
    chk "Manifest kam aus der Kopie, nicht aus dem Repo" \
      "$(grep -qF "$tmp/petrun/assets/pet.json" "$tmp/pet.log" 2>/dev/null && echo ja || echo nein)" ja
    if [[ -n "$pet_ready" ]]; then
      dp="$(diag "$tmp/pet.sock" 2>/dev/null)"
      chk "Community-Pet rendert (sprite nicht leer, frames > 0)" \
        "$( s="$(jq -r '.sprite // empty' <<<"$dp" 2>/dev/null)"
            f="$(jq -r '.frames_rendered // 0' <<<"$dp" 2>/dev/null)"
            [[ -n "$s" ]] && [[ "$f" =~ ^[0-9]+$ ]] && (( f > 0 )) && echo ja || echo nein )" ja
    fi
    kill "$pid_pet" 2>/dev/null; wait "$pid_pet" 2>/dev/null; pid_pet=""
  else
    chk "face/assets vorhanden (Community-Pet)" nein ja
  fi
else
  echo "  FAIL Live-Pruefungen uebersprungen (keine Wayland-Sitzung oder kein Binary)"
  fail=1
fi

exit $fail
