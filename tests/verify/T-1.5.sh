#!/usr/bin/env bash
# Verifizierer fuer T-1.5: Idle-CPU des Face.
#
# ABWEICHUNG VOM PLAN (bewusst, nicht stillschweigend):
# Der Plan-Verifikationssatz verlangt "misst 60 s Ruhe-CPU per pidstat".
# pidstat ist auf dieser Maschine nicht installiert (kein sysstat-Paket).
# Gemessen wird stattdessen ueber /proc/<pid>/stat: das Delta von utime+stime
# (Jiffies) geteilt durch _SC_CLK_TCK und die tatsaechlich gemessene Wandzeit
# (time.monotonic). Das ist dieselbe physikalische Groesse, ohne Abtast-
# Intervall genauer als pidstat und braucht keine zusaetzliche Abhaengigkeit.
#
# Messfenster: 60 s, ueber DAIMON_MESSFENSTER (Sekunden) verkuerzbar, aber mit
# hart erzwungener Untergrenze von 15 s. Begruendung der Untergrenze: die
# Mutante tests/mutants/T-1.5/late-burn verhaelt sich die ersten 8 s nach
# READY vorbildlich und brennt erst danach CPU (sie erhoeht ab dann fortlaufend
# frames_rendered). Dieser Verifizierer braucht nach READY hoechstens ca. 5 s
# Vorlauf (Socket-, Kanarien- und Absetz-Pruefungen); das Fenster endet also
# spaetestens bei 5 + L Sekunden nach READY. Mit L = 15 liegen garantiert
# mindestens ~7 s Brennzeit im Fenster -- das CPU-Mittel liegt dann weit ueber
# 0,5 % und frames_rendered waechst nachweisbar. Ein Verifizierer mit einem
# 2-s-Fenster endet dagegen bei ~7 s, also VOR dem Zuenden, und sieht die
# Mutante nicht. Werte unter der Untergrenze werden auf 15 s angehoben und das
# wird gemeldet.
#
# Geprueft wird am LAUFENDEN Prozess, von aussen (/proc, Sockets,
# Protokoll-Mitschnitt) -- der Pruefling meldet nichts ueber sich selbst, das
# nicht durch eine Positivkontrolle abgesichert waere:
#  - "frames_rendered konstant" ist nur etwas wert, weil vorher bewiesen wird,
#    dass ein Zustandswechsel den Zaehler erhoeht (wichtigster Kanarienvogel).
#  - "< 0,5 % CPU" ist nur etwas wert, weil die Tick-Zaehler lesbar und
#    plausibel sind, der Zaehler ueberhaupt zaehlt und der Prozess am
#    Fensterende noch lebt.
#
# Was dieses Skript NICHT pruefen kann (benannt, nicht vorgetaeuscht):
#  - das dirty/frame_pending-Flag und die calloop-Struktur (Display-FD und
#    Timer in einem poll()) liegen im Prozessinneren und sind von aussen nicht
#    beobachtbar. Indirekt gedeckt: konstante frames_rendered, null neue
#    Commits im Mitschnitt und niedrige CPU sind genau das beobachtbare
#    Verhalten, das ein nicht-rearmierter Callback in einem blockierenden
#    poll() erzeugt.
#  - "damage_buffer NUR auf geaenderte Rechtecke": die Rechteck-Geometrie ist
#    aus dem Mitschnitt nicht sinnvoll bewertbar. Belegbar ist das Ruhe-
#    Verhalten: im Messfenster duerfen NULL neue damage_buffer-/commit-Aufrufe
#    auftreten (nur gegen das echte Binary -- der Mitschnitt entsteht nur bei
#    libwayland-Clients, das Python-Stand-in liefert keinen).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
# Zahl groesser: ja/nein, ungueltige Eingaben werden zu nein statt zu einem
# stillen Vergleichsfehler.
num_gt() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 > $2 )); then echo ja; else echo nein; fi; }
num_ge() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 >= $2 )); then echo ja; else echo nein; fi; }

# Messfenster mit hart erzwungener Untergrenze, siehe Skriptkopf.
fenster=60
untergrenze=15
if [[ -n "${DAIMON_MESSFENSTER:-}" ]]; then
  fenster="$DAIMON_MESSFENSTER"
fi
if ! [[ "$fenster" =~ ^[0-9]+$ ]]; then
  echo "  DAIMON_MESSFENSTER='$fenster' ungueltig, verwende 60 s"
  fenster=60
fi
if (( fenster < untergrenze )); then
  echo "  DAIMON_MESSFENSTER=$fenster liegt unter der Untergrenze, wird auf $untergrenze s angehoben"
  fenster=$untergrenze
fi

echo "T-1.5 — Idle-CPU des Face (Messfenster ${fenster} s)"

# --- A. Pruefling -------------------------------------------------------------
if [[ "$TARGET" == "$REPO" ]]; then
  # IMMER bauen, nicht nur wenn das Binary fehlt (Lektion aus T-1.4: ein
  # Verifizierer, der ein veraltetes Binary startet, misst den Stand von
  # vorgestern und bleibt gruen, waehrend die Quelle kaputt ist).
  # Nur ohne DAIMON_FIXTURE wird gebaut; mit Fixture ist der Pruefling das
  # Stand-in im Ersatzbaum.
  ( cd "$REPO/face" && timeout 600 cargo build -p face ) >/dev/null 2>&1
  build_rc=$?
  chk "cargo build -p face laeuft durch" "$build_rc" 0
  # Positivkontrolle gegen genau diesen Fehler: das Binary darf nicht aelter
  # sein als die juengste Quelldatei.
  neuer_stand="$(find "$REPO/face/src" "$REPO/face/Cargo.toml" -newer "$BIN" 2>/dev/null | head -1)"
  chk "Binary ist nicht aelter als die Quellen" \
    "$([[ -z "$neuer_stand" ]] && echo ja || echo nein)" ja
fi
chk "Pruefling vorhanden und ausfuehrbar ($BIN)" \
  "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja

# --- B. Live: Ruhe-CPU und frames_rendered ------------------------------------
tmp="$(mktemp -d)"
pid=""
cleanup() {
  [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
  [[ -n "$pid" ]] && wait "$pid" 2>/dev/null
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
frames() { diag "$tmp/diag.sock" 2>/dev/null | jq -r '.frames_rendered // empty' 2>/dev/null; }

if [[ -x "$BIN" ]]; then
  # Watchdog bei JEDEM Overlay-Lauf: DAIMON_MAX_SECS knapp ueber Vorlauf plus
  # Messfenster. Ein Overlay ohne Watchdog kann diese Maschine mit der Maus
  # unbedienbar machen; das ist hier real passiert.
  max_secs=$(( fenster + 30 ))
  DAIMON_MAX_SECS=$max_secs WAYLAND_DEBUG=1 "$BIN" \
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
  f0="$(jq -r '.frames_rendered // empty' <<<"$d1" 2>/dev/null)"
  chk "frames_rendered ist eine Zahl" "$([[ "$f0" =~ ^[0-9]+$ ]] && echo ja || echo nein)" ja

  # Positivkontrolle Steuerkanal: Unsinn muss mit "err" abgewiesen werden --
  # sonst beweist ein "ok" auf "state dringend" nichts.
  chk "Steuer-Socket weist Unsinn mit err ab" \
    "$(ctl "$tmp/ctl.sock" 'state gibt-es-nicht' 2>/dev/null)" err

  # WICHTIGSTE Positivkontrolle: ein Zustandswechsel erhoeht frames_rendered.
  # Nur dann bedeutet "konstant" weiter unten "rendert im Leerlauf nicht"
  # und nicht "der Zaehler funktioniert gar nicht".
  f_vor="$(frames)"
  chk "Wechsel auf dringend wird mit ok bestaetigt" \
    "$(ctl "$tmp/ctl.sock" 'state dringend' 2>/dev/null)" ok
  sleep 1
  f_nach="$(frames)"
  chk "frames_rendered steigt bei Zustandswechsel (Kanarienvogel)" \
    "$(num_gt "$f_nach" "$f_vor")" ja
  chk "Rueckwechsel auf ruhig wird mit ok bestaetigt" \
    "$(ctl "$tmp/ctl.sock" 'state ruhig' 2>/dev/null)" ok

  # Vor dem Messfenster muss der Pruefling wieder zur Ruhe kommen; sonst misst
  # das Fenster den Nachhall des Kanarienvogels statt den Leerlauf.
  settle=nein
  prev="$(frames)"
  for _ in $(seq 1 8); do
    sleep 0.7
    cur="$(frames)"
    if [[ -n "$prev" && -n "$cur" && "$cur" == "$prev" ]]; then settle=ja; break; fi
    prev="$cur"
  done
  chk "Pruefling beruhigt sich nach dem Zustandswechsel" "$settle" ja

  # --- Das Messfenster --------------------------------------------------------
  f_start="$(frames)"
  wl_vor=$(( $(wc -l <"$tmp/wl.log" 2>/dev/null || echo 0) + 0 ))
  messung="$("$PY" - "$pid" "$fenster" <<'PYEOF'
import os, sys, time
pid = int(sys.argv[1]); fenster = float(sys.argv[2])
clk = os.sysconf("SC_CLK_TCK")
def ticks():
    with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
        data = fh.read()
    # comm (Feld 2) kann Leerzeichen/Klammern enthalten: erst nach dem
    # letzten ')' teilen, dann ist rest[0] Feld 3 (state).
    rest = data[data.rindex(")") + 2:].split()
    return int(rest[11]) + int(rest[12])  # utime (Feld 14) + stime (Feld 15)
t0 = time.monotonic(); a = ticks()
time.sleep(fenster)
t1 = time.monotonic(); b = ticks()
cpu = (b - a) / clk / (t1 - t0) * 100.0
print(f"{cpu:.3f} {a} {b}")
PYEOF
)"
  mess_rc=$?
  f_end="$(frames)"
  wl_nach=$(( $(wc -l <"$tmp/wl.log" 2>/dev/null || echo 0) + 0 ))

  chk "CPU-Messung lesbar (proc-Stat gelesen)" "$mess_rc" 0
  chk "Prozess lebt am Ende des Fensters noch" \
    "$(kill -0 "$pid" 2>/dev/null && echo ja || echo nein)" ja
  cpu=""; ticks_a=""; ticks_b=""
  read -r cpu ticks_a ticks_b <<<"$messung"
  echo "  gemessene Idle-CPU: ${cpu:-unlesbar} % ueber ${fenster} s (Ticks ${ticks_a:-?} -> ${ticks_b:-?})"
  # Positivkontrollen der Messung selbst: der Zaehler hat seit Prozessstart
  # ueberhaupt gezaehlt (Startwert > 0) und lief vorwaerts (Ende >= Anfang).
  chk "Tick-Zaehler zaehlt ueberhaupt (Startwert > 0)" "$(num_gt "$ticks_a" 0)" ja
  chk "Tick-Zaehler plausibel (Ende >= Anfang)" "$(num_ge "$ticks_b" "$ticks_a")" ja
  chk "Idle-CPU im Mittel unter 0,5 %" \
    "$(awk -v c="$cpu" 'BEGIN {print (c+0 < 0.5) ? "ja" : "nein"}')" ja
  chk "frames_rendered konstant von Anfang bis Ende des Fensters" \
    "$([[ -n "$f_start" && -n "$f_end" && "$f_start" == "$f_end" ]] && echo ja || echo nein)" ja

  # --- Protokoll-Mitschnitt (nur bei libwayland-Clients auswertbar) -----------
  if (( wl_nach > 0 )); then
    neu_dmg="$(tail -n +"$((wl_vor + 1))" "$tmp/wl.log" | grep -c 'damage_buffer' || true)"
    chk "kein damage_buffer im Ruhefenster (Mitschnitt)" "$neu_dmg" 0
    neu_commit="$(tail -n +"$((wl_vor + 1))" "$tmp/wl.log" | grep -c '\.commit(' || true)"
    chk "kein commit im Ruhefenster (Mitschnitt)" "$neu_commit" 0
    # Kanarienvogel: der Mitschnitt muss bei den Zustandswechseln oben
    # ueberhaupt damage_buffer gesehen haben -- sonst ist "0 im Fenster"
    # nicht interpretierbar.
    ges_dmg="$(grep -c 'damage_buffer' "$tmp/wl.log" || true)"
    chk "Kanarienvogel: Mitschnitt zeigt damage_buffer bei Zustandswechsel" \
      "$(num_gt "$ges_dmg" 0)" ja
  else
    if [[ "$TARGET" == "$REPO" ]]; then
      # Das echte Binary ist ein libwayland-Client: kein Mitschnitt heisst,
      # diese Belege sind nicht erbracht -- das ist ein Befund, kein INFO.
      chk "Protokoll-Mitschnitt vorhanden (echtes Binary)" nein ja
    else
      echo "  INFO kein Protokoll-Mitschnitt (Stand-in ohne libwayland): damage_buffer/commit von aussen nicht beobachtbar"
    fi
  fi

  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; pid=""
else
  echo "  FAIL Live-Pruefungen uebersprungen (Pruefling fehlt oder nicht ausfuehrbar)"
  fail=1
fi

exit $fail
