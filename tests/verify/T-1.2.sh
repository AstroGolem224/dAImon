#!/usr/bin/env bash
# Verifizierer fuer T-1.2: Diagnose-Socket im Face.
#
# Der Plan verlangt: einen Zustand ueber den Hub setzen, den Diagnose-Socket
# lesen, und pruefen dass `mood` uebereinstimmt und `last_render_ts` NACH dem
# Setzzeitpunkt liegt.
#
# Der zweite Teil ist der eigentliche: `last_render_ts` ist der Zeitpunkt des
# tatsaechlichen COMMITS, nicht des Zustandsempfangs. Der Unterschied ist der
# ganze Sinn der Sache -- ein Face, das den Zustand entgegennimmt und dann
# nicht zeichnet, waere mit einem Empfangszeitstempel gruen und trotzdem
# kaputt.
#
# Und: kein Einfluss auf die Idle-CPU, wenn niemand liest. Der Diagnose-Thread
# blockiert in accept(); ein Timer dort haette die 0,000 % aus T-1.5 zunichte
# gemacht.
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

echo "T-1.2 — Diagnose-Socket im Face"

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
[[ -x "$BIN" && -n "${WAYLAND_DISPLAY:-}" ]] || exit 1

tmp="$(mktemp -d)"
hub=""; face=""
trap '[[ -n "$face" ]] && kill "$face" 2>/dev/null; [[ -n "$hub" ]] && kill "$hub" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

rt="$tmp/rt"
( cd "$REPO" && "$PY" -m daimon.hub.daemon --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
for _ in $(seq 1 80); do [[ -S "$rt/events.sock" ]] && break; sleep 0.1; done
chk "Hub laeuft (Positivkontrolle)" "$([[ -S "$rt/events.sock" ]] && echo ja || echo nein)" ja

DAIMON_MAX_SECS=60 "$BIN" --pet-manifest "$MANIFEST" \
  --hub-socket "$rt/events.sock" --diag-socket "$tmp/d.sock" \
  >"$tmp/out.log" 2>"$tmp/err.log" &
face=$!
for _ in $(seq 1 150); do [[ -S "$tmp/d.sock" ]] && break; sleep 0.1; done
chk "Diagnose-Socket entsteht" "$([[ -S "$tmp/d.sock" ]] && echo ja || echo nein)" ja
chk "Diagnose-Socket hat Modus 0600" "$(stat -c '%a' "$tmp/d.sock" 2>/dev/null)" 600

diag() { "$PY" - "$tmp/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

d="$(diag)"
echo "  Diagnose: $d"
for feld in rev mood sprite bubble_visible last_render_ts frames_rendered; do
  chk "Feld $feld vorhanden" \
    "$(jq -e "has(\"$feld\")" <<<"$d" >/dev/null 2>&1 && echo ja || echo nein)" ja
done

# --- Der eigentliche Test: mood folgt dem Hub, last_render_ts liegt danach ---
# Der Setzzeitpunkt wird VOR dem Hook genommen. Alles, was danach passiert,
# muss spaeter liegen -- sonst waere der Zeitstempel aelter als das Ereignis,
# das ihn ausgeloest hat.
setz_ts="$("$PY" -c 'import time; print(f"{time.time():.6f}")')"
"$PY" - "$rt" <<'PYEOF'
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/hookbridge.sock")
c.sendall(json.dumps({"v": 1, "type": "hook", "payload": {
    "hook_event_name": "Notification", "session_id": "t12",
    "notification_type": "permission_prompt", "message": "Darf ich?"}}).encode() + b"\n")
c.close()
PYEOF
sleep 1.5
d2="$(diag)"
echo "  nach dem Hook: $d2"
hub_mood="$("$PY" - "$rt" <<'PYEOF'
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/state.sock")
print(json.loads(c.makefile("rb").readline())["mood"]); c.close()
PYEOF
)"
face_mood="$(jq -r '.mood' <<<"$d2")"
echo "  Hub-mood=$hub_mood  Face-mood=$face_mood"
# Positivkontrolle: der Hub hat sich ueberhaupt bewegt. Ohne das waere die
# Uebereinstimmung unten auch bei zwei stehengebliebenen Werten gruen.
chk "der Hub meldet needs_input (Positivkontrolle)" "$hub_mood" needs_input
chk "mood stimmt mit dem Hub ueberein" "$face_mood" "$hub_mood"

ts="$(jq -r '.last_render_ts' <<<"$d2")"
echo "  Setzzeitpunkt=$setz_ts  last_render_ts=$ts"
chk "last_render_ts liegt NACH dem Setzzeitpunkt" \
  "$(awk -v a="$ts" -v b="$setz_ts" 'BEGIN {print (a+0 > b+0) ? "ja" : "nein"}')" ja
# Und er ist der Zeitpunkt eines COMMITS, nicht des Empfangs: bei diesem
# Wechsel wurde wirklich gezeichnet, also ist frames_rendered gestiegen.
f1="$(jq -r '.frames_rendered' <<<"$d")"
f2="$(jq -r '.frames_rendered' <<<"$d2")"
echo "  frames_rendered: $f1 -> $f2"
chk "frames_rendered ist gestiegen (es wurde wirklich gezeichnet)" \
  "$([[ "$f2" -gt "$f1" ]] && echo ja || echo nein)" ja

# --- Der Kern: ein Zustandswechsel OHNE Neuzeichnen bewegt ihn NICHT ---------
#
# "last_render_ts liegt nach dem Setzzeitpunkt" allein ist zu schwach: ein
# Empfangszeitstempel liegt dort ebenfalls. Unterscheidbar wird es erst an
# einem Wechsel, bei dem der Hub sich bewegt und das Face korrekterweise
# NICHT zeichnet -- `working` und `done` bilden beide auf den Sprite `ruhig`
# ab, es gibt also nichts neu zu committen. Ein Empfangszeitstempel wanderte
# hier mit, ein Commitzeitstempel bleibt stehen.
# Erst auf `working` -- dieser Wechsel zeichnet noch wirklich, weil der
# Sprite von `dringend` auf `ruhig` geht. DANACH wird gemessen.
"$PY" - "$rt" <<'PYEOF2'
import json, socket, sys, time
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/hookbridge.sock")
c.sendall(json.dumps({"v": 1, "type": "hook", "payload": {
    "hook_event_name": "PreToolUse", "session_id": "t12",
    "tool_name": "Read"}}).encode() + b"\n")
c.close(); time.sleep(0.5)
PYEOF2
sleep 1.5
vor="$(diag)"
rev_vor="$(jq -r '.rev' <<<"$vor")"
ts_vor="$(jq -r '.last_render_ts' <<<"$vor")"
# Und jetzt `working` -> `done`: beide bilden auf `ruhig` ab, es gibt nichts
# neu zu committen.
"$PY" - "$rt" <<'PYEOF2'
import json, socket, sys, time
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/hookbridge.sock")
c.sendall(json.dumps({"v": 1, "type": "hook", "payload": {
    "hook_event_name": "Stop", "session_id": "t12"}}).encode() + b"\n")
c.close(); time.sleep(0.5)
PYEOF2
sleep 1.5
nach="$(diag)"
rev_nach="$(jq -r '.rev' <<<"$nach")"
ts_nach="$(jq -r '.last_render_ts' <<<"$nach")"
mood_nach="$(jq -r '.mood' <<<"$nach")"
sprite_nach="$(jq -r '.sprite' <<<"$nach")"
echo "  rev $rev_vor -> $rev_nach, mood=$mood_nach sprite=$sprite_nach"
echo "  last_render_ts $ts_vor -> $ts_nach"
# Positivkontrolle: der Zustand ist wirklich angekommen. Ohne sie waere
# "Zeitstempel unveraendert" auch bei einem toten Hub-Kanal gruen.
chk "die rev ist gestiegen (der Zustand kam an)" \
  "$([[ "$rev_nach" -gt "$rev_vor" ]] && echo ja || echo nein)" ja
chk "der Sprite blieb dabei ruhig (kein Neuzeichnen noetig)" "$sprite_nach" ruhig
chk "last_render_ts blieb stehen (Commitzeit, nicht Empfangszeit)" \
  "$(awk -v a="$ts_vor" -v b="$ts_nach" 'BEGIN {print (a==b) ? "ja" : "nein"}')" ja

# --- Kein Einfluss auf die Idle-CPU, wenn niemand liest -----------------------
HZ="$(getconf CLK_TCK)"
ticks() { awk '{print $14+$15}' "/proc/$face/stat" 2>/dev/null; }
a="$(ticks)"; sleep 12; b="$(ticks)"
cpu="$(awk -v a="$a" -v b="$b" -v hz="$HZ" 'BEGIN {printf "%.4f", (b-a)/hz/12*100}')"
echo "  Idle-CPU ohne Leser ueber 12 s: $cpu %"
chk "Tick-Zaehler lesbar (Positivkontrolle)" \
  "$([[ "$a" =~ ^[0-9]+$ && "$b" =~ ^[0-9]+$ ]] && echo ja || echo nein)" ja
chk "kein Einfluss auf die Idle-CPU" \
  "$(awk -v c="$cpu" 'BEGIN {print (c+0 < 0.5) ? "ja" : "nein"}')" ja
# Gegenprobe: mit einem Leser bewegt sich der Zaehler ueberhaupt. Ohne das
# waere "0 %" auch bei einem toten Prozess gruen.
chk "Prozess lebt am Ende noch" "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja
chk "Diagnose antwortet weiterhin" \
  "$(diag | jq -e 'has("mood")' >/dev/null 2>&1 && echo ja || echo nein)" ja

exit $fail
