#!/usr/bin/env bash
# Verifizierer fuer T-1.6: Hub-Anbindung des Face.
#
# Geprueft wird gegen die Akzeptanzliste, am LAUFENDEN Prozess:
#   [ ] Verbindet sich ueber den Unix-Socket, nicht ueber TCP
#   [ ] Diffed auf rev
#   [ ] Unbekanntes v -> sleeping
#   [ ] Hub weg -> sleeping, kein Absturz, kein Log-Spam
# Plan-Verifikationssatz: Latenz zwischen Hub-rev-Erhoehung und dem
# Nachziehen des Face ueber 20 Zustandswechsel, p95 < 300 ms; danach Hub
# stoppen und den Wechsel auf sleeping pruefen.
#
# Gemessen wird ehrlich: Zeitstempel unmittelbar VOR dem Absetzen des
# Hook-Ereignisses, dann Warten, bis die Face-rev im Diagnose-Socket
# nachgezogen hat. Das schliesst die Hub-Verarbeitung mit ein -- eine
# groessere, nie eine kleinere Latenz als die des Face allein.
#
# Positivkontrollen, ohne die die Urteile wertlos waeren:
#  - "kein TCP" zaehlt nur, weil vorher bewiesen wird, dass der Prozess
#    ein offenes Ende einer Unix-Verbindung zum events.sock des Hubs haelt
#    (ss -xpnH: Face- und Hub-Zeile teilen sich die Verbindungs-Inodes).
#  - "Face-rev folgt" zaehlt nur, weil die Hub-rev sich nachweislich bei
#    allen 20 Wechseln bewegt hat (hub_bewegt == 20).
#  - "unbekanntes v -> sleeping" zaehlt nur, weil das Face dem Mini-Hub
#    vorher nachweislich nach working folgt und sich nachher davon wieder
#    erholt -- sonst waere sleeping auch nur eine tote Verbindung.
#  - Der Mini-Hub wird fuer "unbekanntes v" gebraucht, weil der echte Hub
#    nach Vertrag nur v=2 spricht; der Mini-Hub bedient events.sock exakt
#    nach Vertrag (sofort Snapshot beim Verbinden, dann eine Zeile je
#    rev-Aenderung, liest nichts) und schickt genau die Zeilen, die der
#    Pruefling sehen soll.
#
# Log-Spam-Grenze: hoechstens 10 stderr-Zeilen in einem 3-s-Fenster, nachdem
# das Face sleeping erreicht hat. Begruendung: eine gebaendigte
# Wiederverbindungs-Schleife (Backoff >= 1 s) erzeugt in 3 s hoechstens
# ~3 Zeilen; eine Spin-Schleife ohne Backoff erzeugt hunderte bis tausende.
# Die Grenze 10 laesst geschwaetziges, aber gebremstes Verhalten durch und
# faengt nur echtes Spammen.
#
# Was dieses Skript NICHT prueft (benannt, nicht vorgetaeuscht):
#  - Sprite-Abbildung (needs_input/failed -> dringend): gehoert zu T-1.4
#    und ist dort eingefroren geprueft; hier zaehlt nur, dass mood den
#    Hub-Mood woertlich traegt (wird je Wechsel mitgeprueft).
#  - last_render_ts als Latenz-Messpunkt: der Plan nennt ihn, ehrlich und
#    aussen beobachtbar ist aber das Nachziehen der Face-rev; das wird
#    gemessen (siehe oben).
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

echo "T-1.6 — Hub-Anbindung"

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
  # Das echte Binary ist ein Wayland-Overlay; das Stand-in braucht keins.
  chk "Wayland-Sitzung vorhanden" \
    "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
fi
chk "Pruefling vorhanden und ausfuehrbar ($BIN)" \
  "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja

tmp="$(mktemp -d)"; rt="$tmp/rt"
face=""; hub=""; mh=""; eof_py=""
cleanup() {
  [[ -n "$face" ]] && kill "$face" 2>/dev/null
  [[ -n "$hub" ]] && kill "$hub" 2>/dev/null
  [[ -n "$mh" ]] && kill "$mh" 2>/dev/null
  [[ -n "$eof_py" ]] && kill "$eof_py" 2>/dev/null
  [[ -n "$face" ]] && wait "$face" 2>/dev/null
  [[ -n "$hub" ]] && wait "$hub" 2>/dev/null
  [[ -n "$mh" ]] && wait "$mh" 2>/dev/null
  [[ -n "$eof_py" ]] && wait "$eof_py" 2>/dev/null
  rm -rf "$tmp"
}
trap cleanup EXIT

# Eine Zeile JSON von einem Antwort-Socket (state.sock, diag.sock) lesen.
diag() { "$PY" - "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
# Eine Hook-Payload (JSON) als {"v":1,"type":"hook",...} an hookbridge.sock.
hook_send() { "$PY" - "$1" "$2" <<'PYEOF'
import json, socket, sys, time
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
c.sendall(json.dumps({"v": 1, "type": "hook",
                      "payload": json.loads(sys.argv[2])}).encode() + b"\n")
time.sleep(0.02); c.close()
PYEOF
}
# Auf einen Mood im Diagnose-Socket warten: $1 Socket, $2 Mood, $3 Zehntelsekunden.
warte_mood() {
  local m=""
  for _ in $(seq 1 "${3:-100}"); do
    m="$(diag "$1" 2>/dev/null | jq -r '.mood // empty' 2>/dev/null)"
    [[ "$m" == "$2" ]] && { echo ja; return; }
    sleep 0.1
  done
  echo nein
}
# Face mit Watchdog starten: $1 hub-socket, $2 diag-socket, $3 control-socket,
# $4 max_secs, $5 stdout-log, $6 stderr-log; setzt $face und wartet auf READY.
face_start() {
  DAIMON_MAX_SECS="$4" "$BIN" \
    --hub-socket "$1" --diag-socket "$2" --control-socket "$3" \
    >"$5" 2>"$6" &
  face=$!
  ready=""
  for _ in $(seq 1 100); do
    ready="$(grep -oE 'READY pid=[0-9]+' "$5" 2>/dev/null | head -1)"
    [[ -n "$ready" ]] && break
    kill -0 "$face" 2>/dev/null || break
    sleep 0.1
  done
}

# --- B. Echter Hub: Transport, Latenz, rev-Diff, Hub weg ----------------------
( cd "$REPO" && XDG_RUNTIME_DIR="$tmp" "$PY" -m daimon.hub.daemon \
    --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
for _ in $(seq 1 50); do [[ -S "$rt/events.sock" ]] && break; sleep 0.1; done
chk "Hub laeuft" "$(kill -0 $hub 2>/dev/null && echo ja || echo nein)" ja
chk "events.sock existiert" "$([[ -S "$rt/events.sock" ]] && echo ja || echo nein)" ja
chk "events.sock hat Modus 0600" "$(stat -c '%a' "$rt/events.sock" 2>/dev/null)" 600

# Hub-seitige Positivkontrolle des Push-Endpunkts, bevor dem Face irgendetwas
# angelastet wird: sofort ein Snapshot beim Verbinden, danach eine Zeile bei
# der naechsten rev-Aenderung, ohne dass der Client etwas schickt.
push_probe="$( "$PY" - "$rt" <<'PYEOF'
import json, socket, sys, time
rt = sys.argv[1]
e = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
e.settimeout(5); e.connect(rt + "/events.sock")
f = e.makefile("rb")
snap = json.loads(f.readline())
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(rt + "/hookbridge.sock")
c.sendall(json.dumps({"v": 1, "type": "hook", "payload": {
    "hook_event_name": "SessionStart", "session_id": "t16"}}).encode() + b"\n")
time.sleep(0.05); c.close()
push = json.loads(f.readline())
e.close()
print(json.dumps({
    "snapshot_ok": isinstance(snap.get("rev"), int) and "mood" in snap,
    "push_ok": isinstance(push.get("rev"), int) and push["rev"] > snap["rev"],
}))
PYEOF
)"
chk "events.sock liefert sofort einen Snapshot" \
  "$(jq -r '.snapshot_ok' <<<"$push_probe" 2>/dev/null)" true
chk "events.sock pusht bei rev-Aenderung ohne Client-Anfrage" \
  "$(jq -r '.push_ok' <<<"$push_probe" 2>/dev/null)" true

if [[ -x "$BIN" ]]; then
  # Watchdog bei JEDEM Overlay-Lauf: ein Overlay ohne Watchdog kann diese
  # Maschine mit der Maus unbedienbar machen; das ist hier real passiert.
  # 90 s decken den Worst Case (Mutant: 20 x 2 s Warten + Sonden) mit Reserve.
  face_start "$rt/events.sock" "$tmp/diag.sock" "$tmp/ctl.sock" \
    90 "$tmp/face.log" "$tmp/face.err"
  chk "Overlay meldet READY" "$([[ -n "$ready" ]] && echo ja || echo nein)" ja
  chk "READY-Pid ist der gestartete Prozess" "${ready#READY pid=}" "$face"

  d0="$(diag "$tmp/diag.sock" 2>/dev/null)"
  # Positivkontrolle: die Diagnose ist parsebar und enthaelt die Pflichtfelder;
  # sonst waere jeder Vergleich darauf wertlos.
  chk "Diagnose-JSON enthaelt rev/mood" \
    "$(jq -e 'has("rev") and has("mood")' <<<"$d0" >/dev/null 2>&1 && echo ja || echo nein)" ja

  # --- Transport: Unix, nicht TCP -------------------------------------------
  # Positivkontrolle zuerst: der Prozess hat nachweislich ein offenes Ende
  # einer Unix-Verbindung zum events.sock des Hubs. Die clientseitige
  # Socket-Zeile traegt den Pfad NICHT (nur die hubseitige tut das), darum
  # laeuft der Nachweis ueber ss -xpnH: Face-Zeile und Hub-Pfad-Zeile einer
  # Verbindung teilen sich beide Inodes (lokal/peer vertauscht). Erst dann
  # bedeutet "kein TCP-Endpunkt" etwas.
  unix_zum_hub="$( "$PY" - "$face" "$rt/events.sock" <<'PYEOF'
import os, re, subprocess, sys
pid, pfad = sys.argv[1], sys.argv[2]
# Innere Positivkontrolle: der Prozess haelt ueberhaupt Socket-Deskriptoren.
fds = set()
try:
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            ziel = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if ziel.startswith("socket:["):
            fds.add(ziel[len("socket:["):-1])
except OSError:
    pass
if not fds:
    print("nein"); sys.exit()
ausgabe = subprocess.run(["ss", "-xpnH"], capture_output=True,
                         text=True).stdout
face_inodes = set()
pfad_inodes = set()
for zeile in ausgabe.splitlines():
    kopf = zeile.split("users:")[0]
    nummern = set(re.findall(r"\b(\d{4,})\b", kopf))
    if f"pid={pid}," in zeile:
        face_inodes.update(nummern)
    if pfad in zeile:
        pfad_inodes.update(nummern)
print("ja" if face_inodes & pfad_inodes else "nein")
PYEOF
)"
  chk "Positivkontrolle: Face haelt einen Unix-Socket zum Hub" "$unix_zum_hub" ja
  tcp_n="$(ss -tnp 2>/dev/null | grep -c "pid=$face," || true)"
  chk "Face haelt keinen TCP-Socket (ss -tnp)" "${tcp_n:-0}" 0

  # --- Latenz ueber 20 Zustandswechsel + rev-Diff ----------------------------
  # 20 alternierende Ereignisse (working <-> done), jedes aendert den Mood und
  # erhoeht damit die Hub-rev (am echten Hub verifiziert: 20/20).
  messung="$( "$PY" - "$rt" "$tmp/diag.sock" <<'PYEOF'
import json, socket, sys, time

rt, diag_pfad = sys.argv[1], sys.argv[2]

def lese_zeile(pfad):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5); c.connect(pfad)
    d = json.loads(c.makefile("rb").readline()); c.close()
    return d

def hook(payload):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5); c.connect(rt + "/hookbridge.sock")
    c.sendall(json.dumps({"v": 1, "type": "hook",
                          "payload": payload}).encode() + b"\n")
    time.sleep(0.02); c.close()

EV = [
    {"hook_event_name": "PreToolUse", "session_id": "t16", "tool_name": "Bash"},
    {"hook_event_name": "Stop", "session_id": "t16",
     "last_assistant_message": "fertig"},
]
N = 20
latenzen = []
hub_bewegt = 0
rev_passt = 0
mood_passt = 0
for i in range(N):
    vor = lese_zeile(rt + "/state.sock")
    t0 = time.monotonic()
    hook(EV[i % 2])
    ziel = None
    while time.monotonic() - t0 < 2.0:
        d = lese_zeile(diag_pfad)
        if isinstance(d.get("rev"), int) and d["rev"] > vor["rev"]:
            ziel = d
            break
        time.sleep(0.01)
    t1 = time.monotonic()
    nach = lese_zeile(rt + "/state.sock")
    if nach["rev"] > vor["rev"]:
        hub_bewegt += 1
    if ziel is not None:
        latenzen.append(t1 - t0)
        if ziel["rev"] == nach["rev"]:
            rev_passt += 1
        if ziel.get("mood") == nach.get("mood"):
            mood_passt += 1

latenzen.sort()
def percentil(werte, p):
    if not werte:
        return None
    k = (len(werte) - 1) * p
    u = int(k); o = min(u + 1, len(werte) - 1)
    return werte[u] + (werte[o] - werte[u]) * (k - u)

print(json.dumps({
    "n": N, "hub_bewegt": hub_bewegt, "gemessen": len(latenzen),
    "rev_passt": rev_passt, "mood_passt": mood_passt,
    "median": percentil(latenzen, 0.5), "p95": percentil(latenzen, 0.95),
}))
PYEOF
)"
  n_hub="$(jq -r '.hub_bewegt // 0' <<<"$messung" 2>/dev/null)"
  n_gemessen="$(jq -r '.gemessen // 0' <<<"$messung" 2>/dev/null)"
  n_rev="$(jq -r '.rev_passt // 0' <<<"$messung" 2>/dev/null)"
  n_mood="$(jq -r '.mood_passt // 0' <<<"$messung" 2>/dev/null)"
  median="$(jq -r '.median // "null"' <<<"$messung" 2>/dev/null)"
  p95="$(jq -r '.p95 // "null"' <<<"$messung" 2>/dev/null)"
  echo "  Latenzen rev -> Face ueber 20 Wechsel: Median ${median} s, p95 ${p95} s (gemessen: $n_gemessen)"
  # WICHTIGSTE Positivkontrolle: die Hub-rev hat sich bei allen 20 Wechseln
  # bewegt -- sonst beweist "Face folgt" nichts.
  chk "Hub-rev hat sich bei allen 20 Wechseln bewegt" "$n_hub" 20
  chk "Face-rev ist bei allen 20 Wechseln nachgezogen" "$n_gemessen" 20
  chk "Face-rev entspricht nach jedem Wechsel der Hub-rev (diffed auf rev)" "$n_rev" 20
  chk "Face-mood traegt nach jedem Wechsel den Hub-Mood woertlich" "$n_mood" 20
  chk "Latenz p95 unter 300 ms" \
    "$(awk -v p="$p95" 'BEGIN {print (p != "null" && p+0 < 0.3) ? "ja" : "nein"}')" ja

  # Noop-Probe: ein Ereignis, das den Hub-Zustand NICHT aendert, darf weder
  # Hub- noch Face-rev bewegen. Wiederholtes PreToolUse ist so ein Fall
  # (Stop waere es nicht: er setzt die Bubble erneut und erhoeht rev).
  hook_send "$rt/hookbridge.sock" \
    '{"hook_event_name":"PreToolUse","session_id":"t16","tool_name":"Bash"}'
  sleep 0.6
  h_vor="$(diag "$rt/state.sock" 2>/dev/null | jq -r '.rev // empty' 2>/dev/null)"
  hook_send "$rt/hookbridge.sock" \
    '{"hook_event_name":"PreToolUse","session_id":"t16","tool_name":"Bash"}'
  sleep 0.6
  h_nach="$(diag "$rt/state.sock" 2>/dev/null | jq -r '.rev // empty' 2>/dev/null)"
  f_nach="$(diag "$tmp/diag.sock" 2>/dev/null | jq -r '.rev // empty' 2>/dev/null)"
  chk "Positivkontrolle noop: Hub-rev bleibt bei wiederholtem Ereignis stehen" \
    "$([[ -n "$h_vor" && "$h_vor" == "$h_nach" ]] && echo ja || echo nein)" ja
  chk "Face-rev entspricht der Hub-rev auch nach noop" "$f_nach" "$h_nach"

  # --- Hub weg: EOF am Endpunkt, Face -> sleeping, kein Absturz, kein Spam ---
  # EOF-Sonde: ein eigener Client haelt events.sock offen; beim Hub-Tod muss
  # der Kernel/die Hub-Schliessung ein EOF liefern (Vertrag).
  "$PY" - "$rt/events.sock" <<'PYEOF' >"$tmp/eof.log" 2>&1 &
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(15); c.connect(sys.argv[1])
f = c.makefile("rb")
f.readline()  # Snapshot verwerfen
print("BEREIT", flush=True)
zeile = f.readline()  # blockiert bis Push oder EOF
print("EOF" if zeile == b"" else "DATEN", flush=True)
PYEOF
  eof_py=$!
  for _ in $(seq 1 50); do
    grep -q BEREIT "$tmp/eof.log" 2>/dev/null && break
    sleep 0.1
  done
  kill "$hub" 2>/dev/null; wait "$hub" 2>/dev/null; hub=""
  wait "$eof_py" 2>/dev/null; eof_py=""
  chk "Hub-Ende schliesst events.sock (Client sieht EOF)" \
    "$(grep -c '^EOF$' "$tmp/eof.log" 2>/dev/null || echo 0)" 1

  chk "Face geht nach Hub-Tod auf sleeping" \
    "$(warte_mood "$tmp/diag.sock" sleeping 100)" ja
  chk "Face laeuft nach Hub-Tod weiter (kein Absturz)" \
    "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja
  chk "READY-Pid ist unveraendert derselbe Prozess" "${ready#READY pid=}" "$face"

  # Spam-Fenster: 3 s Ruhe, nachdem sleeping erreicht ist. Grenze 10 Zeilen,
  # Begruendung im Skriptkopf.
  err_vor=$(( $(wc -l <"$tmp/face.err" 2>/dev/null || echo 0) + 0 ))
  sleep 3
  err_nach=$(( $(wc -l <"$tmp/face.err" 2>/dev/null || echo 0) + 0 ))
  spam=$(( err_nach - err_vor ))
  echo "  stderr-Zeilen im 3-s-Fenster nach Hub-Tod: $spam"
  chk "kein Log-Spam nach Hub-Tod (<= 10 Zeilen in 3 s)" \
    "$(( spam <= 10 ))" 1

  kill "$face" 2>/dev/null; wait "$face" 2>/dev/null; face=""

  # --- C. Mini-Hub: unbekanntes v -> sleeping --------------------------------
  # Der echte Hub spricht nach Vertrag nur v=2; ein Snapshot mit fremdem v
  # kann nur von einem eigenen Mini-Hub kommen, der events.sock exakt nach
  # Vertrag bedient (Snapshot sofort, dann Zeile je rev-Aenderung, liest
  # nichts) und dessen Zeilen dieser Verifizierer selbst bestimmt.
  cat >"$tmp/mini_hub.py" <<'PYEOF'
"""Mini-Hub fuer T-1.6: bedient events.sock exakt nach Vertrag.

Beim Verbinden sofort eine Snapshot-Zeile, danach eine Zeile je neuem
Snapshot. Steuerung ueber einen zweiten Socket: eine JSON-Zeile ersetzt den
Snapshot und wird an alle verbundenen Clients gepusht; "QUIT" beendet.
"""
import json, os, socket, sys, threading

ereignis_pfad, kommando_pfad = sys.argv[1], sys.argv[2]
for p in (ereignis_pfad, kommando_pfad):
    if os.path.exists(p):
        os.unlink(p)

sperre = threading.Lock()
clients = []
snapshot = None
ende = False

def dienen(lauscher):
    while not ende:
        try:
            verbindung, _ = lauscher.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        with sperre:
            clients.append(verbindung)
            aktuell = snapshot
        if aktuell is not None:
            try:
                verbindung.sendall((json.dumps(aktuell) + "\n").encode())
            except OSError:
                pass

def kommandos(lauscher):
    global snapshot, ende
    while not ende:
        try:
            verbindung, _ = lauscher.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        with verbindung:
            try:
                zeile = verbindung.makefile("rb").readline().decode().strip()
            except OSError:
                continue
            if zeile == "QUIT":
                ende = True
                try:
                    verbindung.sendall(b"ok\n")
                except OSError:
                    pass
                return
            try:
                neu = json.loads(zeile)
            except ValueError:
                try:
                    verbindung.sendall(b"err\n")
                except OSError:
                    pass
                continue
            with sperre:
                snapshot = neu
                aktuell = list(clients)
            for c in aktuell:
                try:
                    c.sendall((json.dumps(neu) + "\n").encode())
                except OSError:
                    with sperre:
                        if c in clients:
                            clients.remove(c)
            try:
                verbindung.sendall(b"ok\n")
            except OSError:
                pass

for pfad, ziel in ((ereignis_pfad, dienen), (kommando_pfad, kommandos)):
    l = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    l.bind(pfad)
    os.chmod(pfad, 0o600)
    l.listen(8)
    l.settimeout(0.2)
    threading.Thread(target=ziel, args=(l,), daemon=True).start()

print("BEREIT", flush=True)
while not ende:
    threading.Event().wait(0.2)
PYEOF
  "$PY" "$tmp/mini_hub.py" "$tmp/mh_events.sock" "$tmp/mh_cmd.sock" \
    >"$tmp/mh.log" 2>&1 &
  mh=$!
  for _ in $(seq 1 50); do
    grep -q BEREIT "$tmp/mh.log" 2>/dev/null && break
    sleep 0.1
  done
  chk "Mini-Hub ist bereit" \
    "$(grep -q BEREIT "$tmp/mh.log" 2>/dev/null && echo ja || echo nein)" ja

  mh_set() { "$PY" - "$tmp/mh_cmd.sock" "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
  }
  basis='"sessions":1,"focus":null,"bubble":null,"voice":{},"perception":{}'
  chk "Mini-Hub nimmt den Anfangs-Snapshot an" \
    "$(mh_set "{\"v\":2,\"rev\":0,\"mood\":\"sleeping\",$basis}")" ok

  face_start "$tmp/mh_events.sock" "$tmp/diag2.sock" "$tmp/ctl2.sock" \
    60 "$tmp/face2.log" "$tmp/face2.err"
  chk "Overlay meldet READY (Mini-Hub-Lauf)" \
    "$([[ -n "$ready" ]] && echo ja || echo nein)" ja

  # Positivkontrolle: das Face reagiert ueberhaupt auf den Mini-Hub und
  # folgt einem gueltigen v=2-Snapshot nach working.
  chk "Mini-Hub nimmt working-Snapshot an" \
    "$(mh_set "{\"v\":2,\"rev\":1,\"mood\":\"working\",$basis}")" ok
  chk "Face folgt dem Mini-Hub nach working (Positivkontrolle)" \
    "$(warte_mood "$tmp/diag2.sock" working 100)" ja

  # Das Kriterium: ein v, das das Face nicht kennt, muss auf sleeping gehen.
  chk "Mini-Hub nimmt v=99-Snapshot an" \
    "$(mh_set "{\"v\":99,\"rev\":2,\"mood\":\"working\",$basis}")" ok
  chk "Unbekanntes v=99 -> Face geht auf sleeping" \
    "$(warte_mood "$tmp/diag2.sock" sleeping 100)" ja

  # Gegenprobe: ein gueltiges v=2 danach muss das Face wieder aufwecken --
  # sonst war sleeping oben nur eine tote Verbindung, kein Vertragsverhalten.
  chk "Mini-Hub weist kaputtes JSON ab (Eigenkontrolle)" \
    "$(mh_set 'das ist kein json')" err
  chk "Mini-Hub nimmt working-Snapshot (v=2) an" \
    "$(mh_set "{\"v\":2,\"rev\":3,\"mood\":\"working\",$basis}")" ok
  chk "Face erholt sich nach unbekanntem v wieder (Gegenprobe)" \
    "$(warte_mood "$tmp/diag2.sock" working 100)" ja

  mh_set "QUIT" >/dev/null 2>&1 || true
  wait "$mh" 2>/dev/null; mh=""
  kill "$face" 2>/dev/null; wait "$face" 2>/dev/null; face=""
else
  echo "  FAIL Live-Pruefungen uebersprungen (Pruefling fehlt oder nicht ausfuehrbar)"
  fail=1
fi

exit $fail
