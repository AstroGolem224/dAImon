#!/usr/bin/env bash
# Verifizierer fuer T-2.4: Ein-/Ausblenden haerten.
#
# KDE-Bug 503121: nach einem NULL-Buffer-Unmap liefert KWin ohne erneut
# gesetzte Layer-Properties KEIN neues `configure`. Im Spike T-1.3 waren es
# 0 von 20 ohne Umgehung und 20 von 20 mit ihr.
#
# T-2.4 dreht das um: der Unmap wird gar nicht mehr benutzt. Die sicherste
# Umgehung ist die, die den Pfad nicht betritt. Ausgeblendet heisst deshalb:
# gemappt bleiben, durchsichtig zeichnen, leere Input-Region.
#
# Zwei Messungen, und die zweite ist die eigentliche:
#
#   1. Im Protokoll-Mitschnitt kommt KEIN `attach(nil)` vor.
#   2. 100 Zyklen aus/ein, und nach JEDEM muss der Diagnose-Socket binnen 1 s
#      einen frischen `last_render_ts` melden. Ein einziger Ausfall laesst
#      den Test scheitern.
#
# Warum 100 und nicht 5: der Bug zeigt sich nicht beim ersten Mal. Ein
# Verifizierer mit zu wenigen Zyklen bestuende einen Pruefling, der erst nach
# einer Weile haengt -- deshalb gibt es dafuer einen eigenen Mutanten.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"
ZYKLEN="${DAIMON_ZYKLEN:-100}"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
num_ge() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 >= $2 )); then echo ja; else echo nein; fi; }

echo "T-2.4 — Ein-/Ausblenden haerten ($ZYKLEN Zyklen)"

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
[[ -n "${WAYLAND_DISPLAY:-}" && -x "$BIN" ]] || { echo "  FAIL Live-Pruefungen uebersprungen"; exit 1; }

tmp="$(mktemp -d)"
face=""
trap '[[ -n "$face" ]] && kill "$face" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

# Der Watchdog muss den ganzen Zyklenlauf ueberdauern, sonst misst man das
# Ende des Watchdogs statt eines Haengers.
maxsecs=$(( ZYKLEN / 2 + 90 ))
DAIMON_MAX_SECS=$maxsecs WAYLAND_DEBUG=1 "$BIN" --pet-manifest "$MANIFEST" \
  --sprite-position 900,500 --diag-socket "$tmp/d.sock" \
  --control-socket "$tmp/c.sock" >"$tmp/out.log" 2>"$tmp/wl.log" &
face=$!
for _ in $(seq 1 200); do [[ -S "$tmp/c.sock" && -S "$tmp/d.sock" ]] && break; sleep 0.1; done
chk "Overlay startet" "$([[ -S "$tmp/c.sock" ]] && echo ja || echo nein)" ja
sleep 2

ctl() { "$PY" - "$tmp/c.sock" "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
diag() { "$PY" - "$tmp/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

d0="$(diag)"
chk "Diagnose kennt das Feld sichtbar" \
  "$(jq -e 'has("sichtbar")' <<<"$d0" >/dev/null 2>&1 && echo ja || echo nein)" ja
# Positivkontrolle des Steuerkanals: Unsinn muss abgewiesen werden, sonst
# beweist ein "ok" auf einen echten Befehl nichts.
chk "Steuer-Socket weist Unsinn mit err ab" "$(ctl 'sichtbar vielleicht')" err
chk "sichtbar aus wird bestaetigt" "$(ctl 'sichtbar aus')" ok
sleep 0.6
chk "Diagnose meldet unsichtbar" "$(jq -r '.sichtbar' <<<"$(diag)")" false
chk "sichtbar an wird bestaetigt" "$(ctl 'sichtbar an')" ok
sleep 0.6
chk "Diagnose meldet wieder sichtbar" "$(jq -r '.sichtbar' <<<"$(diag)")" true

# --- Die Zyklen ---------------------------------------------------------------
# Nach jedem Aus/Ein muss binnen 1 s ein FRISCHER Zeitstempel kommen. "Frisch"
# heisst: groesser als der davor -- ein Ausblenden, das nichts committet,
# waere kein Ausblenden.
ausfaelle=0
erster_ausfall=""
ts_vor="$(jq -r '.last_render_ts' <<<"$(diag)")"
for i in $(seq 1 "$ZYKLEN"); do
  for zustand in aus an; do
    if [[ "$(ctl "sichtbar $zustand")" != ok ]]; then
      ausfaelle=$(( ausfaelle + 1 ))
      [[ -z "$erster_ausfall" ]] && erster_ausfall="Zyklus $i ($zustand): Steuerbefehl abgelehnt"
      continue
    fi
    frisch=nein
    ende="$("$PY" -c 'import time; print(time.monotonic()+1.0)')"
    while :; do
      ts_neu="$(jq -r '.last_render_ts' <<<"$(diag)" 2>/dev/null)"
      if [[ "$ts_neu" =~ ^[0-9.]+$ ]] && \
         awk -v a="$ts_neu" -v b="$ts_vor" 'BEGIN {exit !(a+0 > b+0)}'; then
        frisch=ja; ts_vor="$ts_neu"; break
      fi
      "$PY" -c "import sys,time; sys.exit(0 if time.monotonic() < $ende else 1)" || break
      sleep 0.05
    done
    if [[ "$frisch" != ja ]]; then
      ausfaelle=$(( ausfaelle + 1 ))
      [[ -z "$erster_ausfall" ]] && erster_ausfall="Zyklus $i ($zustand): kein frischer last_render_ts binnen 1 s"
    fi
  done
  kill -0 "$face" 2>/dev/null || { erster_ausfall="Zyklus $i: Prozess gestorben"; ausfaelle=$(( ausfaelle + 1 )); break; }
done
echo "  $ZYKLEN Zyklen gefahren, Ausfaelle: $ausfaelle"
[[ -n "$erster_ausfall" ]] && echo "  erster Ausfall: $erster_ausfall"
chk "kein einziger Ausfall ueber alle Zyklen" "$ausfaelle" 0
chk "der Prozess lebt nach allen Zyklen" \
  "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja

# --- Kein NULL-Buffer-Unmap ---------------------------------------------------
# Positivkontrolle zuerst: der Mitschnitt zeigt ueberhaupt Verkehr, und es
# wurde wirklich gezeichnet. Ohne sie waere "kein attach(nil)" auch bei einem
# leeren Log gruen -- die Nullaussage, die dieses Projekt neunmal gekostet hat.
chk "Protokoll-Mitschnitt ist aktiv (Positivkontrolle)" \
  "$(grep -q 'wl_surface@' "$tmp/wl.log" && echo ja || echo nein)" ja
attaches="$(grep -c '\.attach(' "$tmp/wl.log")"
echo "  attach-Aufrufe im Mitschnitt: $attaches"
chk "es wurde ueberhaupt attached (Positivkontrolle)" "$(num_ge "$attaches" 10)" ja
# wayland-rs schreibt einen NULL-Buffer als `attach(<anonymous>@0, ...)`.
# Die erste Fassung suchte nach `nil`/`null` und fand deshalb nichts -- der
# Mutant fiel nur ueber den Zyklentest durch, und diese Pruefung war eine
# Nullaussage. Am Mitschnitt nachgesehen statt geraten.
nulls="$(grep -cE '\.attach\((<anonymous>@0|nil|null)[,)]' "$tmp/wl.log")"
[[ "$nulls" =~ ^[0-9]+$ ]] || nulls=0
echo "  NULL-Buffer-Unmaps: $nulls"
chk "kein NULL-Buffer-Unmap" "$nulls" 0

# --- Der Kommentar mit dem Verweis auf den Bug --------------------------------
# Akzeptanzkriterium, und es steht nun einmal im Quelltext.
chk "der gewaehlte Weg ist mit Verweis auf 503121 kommentiert" \
  "$(grep -rq '503121' "$TARGET/face/src" && echo ja || echo nein)" ja

# --- Die Idle-CPU haelt weiter ------------------------------------------------
if [[ "$TARGET" == "$REPO" ]]; then
  timeout 400 bash "$REPO/tests/verify/T-1.5.sh" >"$tmp/t15.log" 2>&1
  t15_rc=$?
  echo " $(grep -oE 'gemessene Idle-CPU: [0-9.]+ %[^(]*' "$tmp/t15.log" | head -1)"
  chk "T-1.5 (Idle-CPU) haelt nach den Zyklen weiterhin" "$t15_rc" 0
else
  echo "  INFO Fixture-Lauf: die T-1.5-Gegenprobe laeuft nur gegen das echte Repo"
fi

exit $fail
