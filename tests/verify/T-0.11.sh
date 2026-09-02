#!/usr/bin/env bash
# Verifizierer fuer T-0.11: authentifizierte Hook-Bridge.
#
# Der Audit wird als eigene Logdatei geprueft. Das ist absichtlich die
# nachpruefbare Alternative zum Journal: Der Test laeuft in einem privaten
# Runtime-Verzeichnis und kann so genau den von ihm erzeugten 401 zuordnen.
#
# Die Egress-Probe startet eine zweite Bridge im gleichen isolierten
# User-/Netz-Namespace wie die Produktivprobe, mit aktivem Loopback aber ohne
# weitere Netzschnittstelle. `curl` wird per nsenter wirklich dort ausgefuehrt.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
GOOD="$REPO/tests/fixtures/known-good/T-0.11"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

fail=0
ungemessen=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
info() { echo "  INFO $*"; }
# Regel uebernommen aus tests/verify/T-0.9.sh:32-37 -- dort steht sie
# ausgeschrieben. Kurz: "nichts gefunden" und "nicht gemessen" sind zwei
# Aussagen; ein ungemessenes Kriterium wird weder gruen noch rot, und der
# Lauf endet mit Exit 3.
nicht_gemessen() { echo "  ????  $1 -- NICHT GEMESSEN: $2"; ungemessen=1; }
# Kriterien, die eine Wirkung IM HUB messen. Kam die Scope nicht zustande,
# weist der Hub die Bridge ab (Begruendung an der Scope, unten) -- dann hat
# der Lauf ueber den Hub nichts ausgesagt, und das ist nicht rot.
chk_hub() { if [[ -n "${scope_grund:-}" ]]; then nicht_gemessen "$1" "$scope_grund"; else chk "$@"; fi; }

echo "T-0.11 — Hook-Bridge"
bridge_source=nein
hub_source=nein
if [[ -f "$TARGET/daimon/hookbridge/daemon.py" ]]; then bridge_source=ja; fi
if [[ -f "$TARGET/daimon/hub/daemon.py" ]]; then hub_source=ja; fi
if [[ "$TARGET" != "$REPO" ]]; then
  # Mutanten enthalten nur ihre Abweichung und verwenden ansonsten dasselbe
  # lauffaehige Gut-Muster. So bleibt jede Mutation einzeln und nachpruefbar.
  [[ -f "$GOOD/daimon/hookbridge/daemon.py" ]] && bridge_source=ja
  [[ -f "$GOOD/daimon/hub/daemon.py" ]] && hub_source=ja
fi
chk "Bridge-Einstieg existiert" "$bridge_source" ja
chk "Hub-Einstieg existiert" "$hub_source" ja

config="$TARGET/config/claude-hooks.json"
if [[ ! -f "$config" && "$TARGET" != "$REPO" ]]; then
  config="$GOOD/config/claude-hooks.json"
fi
chk "Claude-Hook-Konfiguration existiert" "$([[ -f "$config" ]] && echo ja || echo nein)" ja

tmp="$(mktemp -d)"
runtime_root="$tmp/runtime"
runtime="$runtime_root/daimon"
audit="$tmp/audit.log"
mkdir -m 700 -p "$runtime"
: >"$audit"
pids=()
declare -A pid_start=()
# 1, sobald DIESER Lauf `daimon-verify.scope` angelegt hat -- nur dann raeumt
# er sie auch ab. Eine fremde Scope gleichen Namens gehoert ihm nicht.
scope_eigen=0
scope_grund=""
track_pid() {
  local pid="$1"
  pids+=("$pid")
  pid_start["$pid"]="$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)"
}
cleanup() {
  local pid current
  for pid in "${pids[@]}"; do
    # Alle Eintraege sind timeout-Waechter; TERM wird von timeout an sein
    # Kind weitergereicht. Keine fremde Prozessgruppe anhand einer spaeter
    # eventuell wiederverwendeten PID treffen.
    current="$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)"
    if [[ -n "$current" && "$current" == "${pid_start[$pid]:-}" ]]; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  # Die Scope ueberlebt den Elternprozess: `track_pid` allein raeumt sie
  # nicht ab. `stop` toetet, was noch in ihr laeuft, und entfernt sie.
  if (( scope_eigen )); then
    systemctl --user stop daimon-verify.scope >/dev/null 2>&1 || true
  fi
  if [[ "${DAIMON_KEEP_TMP:-0}" == 1 ]]; then
    echo "  Diagnoseverzeichnis bleibt: $tmp" >&2
  else
    rm -rf -- "$tmp"
  fi
}
trap cleanup EXIT INT TERM

free_port() {
  "$PY" - <<'PYEOF'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
p = s.getsockname()[1]
s.close()
print(p if p >= 1024 else p + 20000)
PYEOF
}
port="$(free_port)"
probe_port="$(free_port)"
hang_port="$(free_port)"
outside_port="$(free_port)"

mutation=""
if [[ -f "$TARGET/mutation.txt" ]]; then
  mutation="$(tr -d '\r\n' <"$TARGET/mutation.txt")"
fi
python_path="$TARGET"
if [[ "$TARGET" != "$REPO" ]]; then python_path="$TARGET:$GOOD"; fi
# Wichtig fuer die reviewer-eigenen Fixtures: Der aktuelle Repo-Pfad darf die
# gleichnamigen Fixture-Module nicht vor dem gesetzten PYTHONPATH verdecken.
cd "$TARGET" || exit 1

hub=0
bridge=0
if [[ "$hub_source" == ja ]]; then
  setsid timeout --signal=TERM 45s env PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$python_path" XDG_RUNTIME_DIR="$runtime_root" \
    "$PY" -m daimon.hub.daemon --runtime-dir "$runtime" \
    >"$tmp/hub.log" 2>&1 &
  hub=$!
  track_pid "$hub"
fi
for _ in $(seq 1 50); do
  [[ -S "$runtime/hookbridge.sock" && -S "$runtime/state.sock" ]] && break
  sleep 0.05
done

# WARUM EINE SCOPE: seit `bd0bb8e` prueft der Hub an `hookbridge.sock` die
# Unit der Gegenstelle gegen `PRODUZENT_UNITS["hookbridge"]` --
# `daimon-hookbridge.service` oder `daimon-verify.scope`
# (daimon/hub/daemon.py:215-220). Die Bridge dieses Pruefstands lief unter der
# Unit der aufrufenden Sitzung, wurde abgewiesen, und die Nutzlast fiel weg.
# Sichtbar wurde das an drei Kriterien; NICHT sichtbar an "bekommt HTTP 200",
# denn `Bridge.an_hub` (daimon/hookbridge/bridge.py:248-260) feuert und
# vergisst. Vorbild dieser Bauform samt Begruendung: `tests/verify/T-0.9.sh:
# 170-211`. Unterschied dort: ein kurzlebiger Sender. Hier haengt ein
# LANGLEBIGER Dienst in der Scope, den der Lauf danach ueber HTTP anspricht.
#
# ponytail: fester Unit-Name, keine Eindeutigkeit -- erzwungen, nicht bequem;
# `ipc.unit_erlaubt` vergleicht exakt und traegt fuer `.scope` kein Template,
# ein Zufallssuffix stuende also auf keiner Liste. DECKE: T-0.9 benutzt
# denselben Namen. Nacheinander harmlos, GLEICHZEITIG nicht -- der zweite
# Lauf bekaeme "unit already exists". Das wird unten als NICHT GEMESSEN
# ausgewiesen und NICHT als roter Befund, und eine fremde Scope wird nicht
# angeruehrt (`scope_eigen`). Ausbaupfad wie in T-0.9.sh:187 beschrieben.
#
# ABRAEUMEN, zwei Wege, weil `--collect` nur aufraeumt und nicht toetet:
#   regulaer -- `systemctl --user stop` im `cleanup`-Trap;
#   bei SIGKILL des Laufs -- das `timeout 45s` IN der Scope beendet die
#   Bridge, und eine leere Scope entfernt systemd von selbst.
bridge_cmd=(timeout --signal=TERM 45s env PYTHONDONTWRITEBYTECODE=1
  PYTHONPATH="$python_path" XDG_RUNTIME_DIR="$runtime_root"
  DAIMON_T011_MUTATION="$mutation"
  "$PY" -m daimon.hookbridge.daemon --runtime-dir "$runtime"
  --port "$port" --audit-log "$audit")
starte_bridge() {
  if [[ "$1" == scope ]]; then
    scope_eigen=1
    # `--scope` exec-t das Kommando im eigenen Prozess, `$!` ist also der
    # timeout-Waechter IN der Scope -- dieselbe Bauform, die `cleanup`
    # ohnehin annimmt.
    systemd-run --user --quiet --collect --scope --unit=daimon-verify.scope \
      -- "${bridge_cmd[@]}" >"$tmp/bridge.log" 2>&1 &
  else
    setsid "${bridge_cmd[@]}" >"$tmp/bridge.log" 2>&1 &
  fi
  bridge=$!
  track_pid "$bridge"
  for _ in $(seq 1 50); do
    ready="$(curl -sS -X POST -o /dev/null -w '%{http_code}' --max-time 0.1 \
      "http://127.0.0.1:$port/unbekannt" 2>/dev/null || true)"
    [[ -f "$runtime/hook-token" && "$ready" == 404 ]] && break
    sleep 0.05
  done
}

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && ! -S "${XDG_RUNTIME_DIR:-/nichts}/bus" ]]; then
  scope_grund="kein Nutzerbus (DBUS_SESSION_BUS_ADDRESS leer, kein \$XDG_RUNTIME_DIR/bus)"
elif ! command -v systemd-run >/dev/null 2>&1; then
  scope_grund="systemd-run fehlt"
elif systemctl --user is-active --quiet daimon-verify.scope 2>/dev/null; then
  scope_grund="Unit-Name daimon-verify.scope ist belegt (zweiter Lauf, oder T-0.9 laeuft)"
fi

if [[ "$bridge_source" == ja ]]; then
  if [[ -z "$scope_grund" ]]; then
    starte_bridge scope
    # DIE MARKE, und zwar nicht der Exit-Code: `systemd-run --scope` reicht
    # den des Kindes durch, eine nie entstandene Scope saehe also aus wie
    # eine abgewiesene Bridge. Gemessen wird darum die cgroup des laufenden
    # Prozesses. Fehlt sie, wird ohne Scope weitergefahren -- die
    # bridge-eigenen Kriterien (401, 404, Groessengrenze, Egress, Frist)
    # bleiben so messbar, nur die Hub-Kriterien nicht.
    if ! grep -q 'daimon-verify\.scope' "/proc/$bridge/cgroup" 2>/dev/null; then
      scope_grund="Bridge kam nicht in daimon-verify.scope: $(tr '\n' ' ' <"$tmp/bridge.log" | cut -c1-200)"
      scope_eigen=0
      kill -TERM "$bridge" 2>/dev/null || true
      wait "$bridge" 2>/dev/null || true
      starte_bridge direkt
    fi
  else
    starte_bridge direkt
  fi
fi
[[ -n "$scope_grund" ]] && info "ohne daimon-verify.scope: $scope_grund"

chk "Hub laeuft" \
  "$([[ "$hub" -gt 0 ]] && kill -0 "$hub" 2>/dev/null && echo ja || echo nein)" ja
chk "Bridge laeuft" \
  "$([[ "$bridge" -gt 0 ]] && kill -0 "$bridge" 2>/dev/null && echo ja || echo nein)" ja
loopback_n="$(ss -ltnH 2>/dev/null | awk -v p=":$port" '$4 ~ (p "$") && $4 ~ /^127[.]0[.]0[.]1:/ {n++} END {print n+0}')"
other_n="$(ss -ltnH 2>/dev/null | awk -v p=":$port" '$4 ~ (p "$") && $4 !~ /^127[.]0[.]0[.]1:/ {n++} END {print n+0}')"
chk "Bridge lauscht auf 127.0.0.1 am freien hohen Port" \
  "$([[ "$loopback_n" -eq 1 ]] && echo ja || echo nein)" ja
chk "Bridge lauscht an keiner weiteren Adresse dieses Ports" "$other_n" 0
token_file="$runtime/hook-token"
chk "Token-Datei liegt unter XDG_RUNTIME_DIR/daimon" \
  "$([[ -f "$token_file" ]] && echo ja || echo nein)" ja
chk "Token-Datei hat Modus 0600" "$(stat -c '%a' "$token_file" 2>/dev/null || echo fehlt)" 600
token=""
if [[ -f "$token_file" ]]; then token="$(tr -d '\r\n' <"$token_file")"; fi

state() {
  "$PY" - "$runtime/state.sock" <<'PYEOF'
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(1)
s.connect(sys.argv[1])
with s.makefile("rb") as inp:
    print(inp.readline().decode(), end="")
s.close()
PYEOF
}
post() {
  local route="$1" auth="$2" body="$3"
  local args=(-sS -o /dev/null -w '%{http_code}' --max-time 1
              -X POST -H 'Content-Type: application/json')
  if [[ "$auth" == ja ]]; then args+=(-H "Authorization: Bearer $token"); fi
  curl "${args[@]}" --data-binary "$body" "http://127.0.0.1:$port$route" 2>/dev/null || echo 000
}

before="$(state 2>/dev/null | jq -r '.rev' 2>/dev/null || echo -1)"
positive_body='{"hook_event_name":"SessionStart","session_id":"kanarienvogel"}'
positive_status="$(post /hook ja "$positive_body")"
sleep 0.15
after="$(state 2>/dev/null | jq -r '.rev' 2>/dev/null || echo -1)"
chk "Positiv-Kanarienvogel bekommt HTTP 200" "$positive_status" 200
chk_hub "Positiv-Kanarienvogel erhoeht Hub-rev" \
  "$([[ "$after" =~ ^[0-9]+$ && "$before" =~ ^-?[0-9]+$ && "$after" -gt "$before" ]] && echo ja || echo nein)" ja

unauth_status="$(post /hook nein '{"hook_event_name":"Stop","session_id":"ohne-token"}')"
chk "Anfrage ohne Token bekommt HTTP 401" "$unauth_status" 401
sleep 0.1
chk "401-Versuch steht in der Audit-Logdatei" \
  "$(grep -q 'unauthorized' "$audit" 2>/dev/null && echo ja || echo nein)" ja

large="$tmp/large.json"
"$PY" - "$large" <<'PYEOF'
import json, sys
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps({
    "hook_event_name": "PreToolUse", "session_id": "zu-gross", "pad": "x" * 70000
}))
PYEOF
large_status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 1 -X POST \
  -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
  --data-binary "@$large" "http://127.0.0.1:$port/hook" 2>/dev/null || echo 000)"
chk "Ueberlanger Body wird abgewiesen statt abgeschnitten" \
  "$([[ "$large_status" == 400 || "$large_status" == 413 ]] && echo ja || echo nein)" ja

unknown_status="$(post /unbekannt ja "$positive_body")"
prefix_status="$(post /hoo ja "$positive_body")"
chk "Unbekannte Route bekommt HTTP 404" "$unknown_status" 404
chk "Praefix-Falle /hoo bekommt HTTP 404" "$prefix_status" 404

events=(SessionStart UserPromptSubmit PreToolUse Notification Stop StopFailure SubagentStop PreCompact SessionEnd)
configured=0
# HTTP 200 ist die Quittung der BRIDGE, nicht die des Hubs: `Bridge.an_hub`
# feuert und vergisst (daimon/hookbridge/bridge.py:248-260). Weist der Hub die
# Bridge ab -- wie bis zum 02.09., als sie unter der Unit der aufrufenden
# Sitzung lief -- bleiben alle neun "wird akzeptiert" gruen, waehrend beim Hub
# nichts ankommt. Genau diese Luecke schliesst das Kriterium unten: angekommen
# ist ein Hook erst, wenn sich der Zustand bewegt hat. `rev` steigt nur bei
# echten Aenderungen (daimon/hub/state.py:22), und jede der neun Proben traegt
# eine eigene `session_id`, muss also auch die Sitzungszahl heben.
hub_vor="$(state 2>/dev/null | jq -r '"\(.rev) \(.sessions)"' 2>/dev/null || true)"
read -r rev_vor sess_vor <<<"${hub_vor:--1 -1}"
for event in "${events[@]}"; do
  exists="$(jq -r --arg e "$event" 'has("hooks") and (.hooks | has($e))' "$config" 2>/dev/null)"
  chk "Hook $event ist konfiguriert" "$exists" true
  [[ "$exists" == true ]] && configured=$((configured + 1))
  body="$(jq -nc --arg e "$event" --arg s "event-$event" \
    '{hook_event_name:$e,session_id:$s,tool_name:(if $e=="PreToolUse" then "Read" else null end)}')"
  status="$(post /hook ja "$body")"
  chk "Hook $event wird akzeptiert" "$status" 200
done
sleep 0.2
hub_nach="$(state 2>/dev/null | jq -r '"\(.rev) \(.sessions)"' 2>/dev/null || true)"
read -r rev_nach sess_nach <<<"${hub_nach:--1 -1}"
chk_hub "Die neun akzeptierten Hooks bewegen den Hub-Zustand (HTTP 200 allein belegt nichts)" \
  "$([[ "$rev_nach" -gt "$rev_vor" && "$sess_nach" -gt "$sess_vor" ]] 2>/dev/null && echo ja || echo nein)" ja
# Die neun Proben oben haben je eine eigene Sitzung angelegt, darunter eine
# mit StopFailure -> mood "failed". "failed" schlaegt "done" in der Prioritaet,
# und die Sitzungen haben keine PID, verfallen also erst nach der TTL. Ohne
# Aufraeumen wuerde die Subagenten-Probe weiter unten am Nachhall der
# StopFailure-Probe scheitern -- ein Fehler im Pruefaufbau, nicht in der Sache.
for event in "${events[@]}"; do
  post /hook ja "$(jq -nc --arg s "event-$event" \
    '{hook_event_name:"SessionEnd",session_id:$s}')" >/dev/null
done

chk "Genau die neun geforderten Hook-Arten sind vorhanden" "$configured" 9
configured_total="$(jq -r '.hooks | length' "$config" 2>/dev/null || echo 0)"
chk "Hook-Konfiguration enthaelt insgesamt genau neun Arten" "$configured_total" 9

# Zwei Agenten, vorzeitiges Stop, beide Rueckmeldungen, dann finales Stop.
sid="subagenten-folge"
post /hook ja "{\"hook_event_name\":\"PreToolUse\",\"session_id\":\"$sid\",\"tool_name\":\"Agent\"}" >/dev/null
post /hook ja "{\"hook_event_name\":\"PreToolUse\",\"session_id\":\"$sid\",\"tool_name\":\"Agent\"}" >/dev/null
post /hook ja "{\"hook_event_name\":\"Stop\",\"session_id\":\"$sid\"}" >/dev/null
sleep 0.1
early_mood="$(state 2>/dev/null | jq -r '.mood' 2>/dev/null || echo fehlt)"
# Auch dieses Kriterium ist hub-abhaengig, obwohl es heute ohne Scope gruen
# waere: ohne Zulauf bleibt die Stimmung "sleeping", und "sleeping != done"
# liest sich wie ein Beleg. Das ist gruen aus dem falschen Grund.
chk_hub "Stop meldet bei zwei laufenden Subagenten noch nicht done" \
  "$([[ "$early_mood" != done ]] && echo ja || echo nein)" ja
post /hook ja "{\"hook_event_name\":\"SubagentStop\",\"session_id\":\"$sid\"}" >/dev/null
post /hook ja "{\"hook_event_name\":\"SubagentStop\",\"session_id\":\"$sid\"}" >/dev/null
post /hook ja "{\"hook_event_name\":\"Stop\",\"session_id\":\"$sid\"}" >/dev/null
sleep 0.1
final_mood="$(state 2>/dev/null | jq -r '.mood' 2>/dev/null || echo fehlt)"
chk_hub "Stop meldet nach beiden SubagentStop-Ereignissen done" "$final_mood" done

# Das echte Hook-Kommando muss den absichtlich falschen Payload-PID ersetzen.
hook_command="$(jq -r '.hooks.PreToolUse[0].hooks[0].command // empty' "$config" 2>/dev/null)"
# config/claude-hooks.json traegt Platzhalter statt echter Werte -- ein Token
# in der Versionsverwaltung waere ein Geheimnis in der Historie. Der Installer
# setzt sie beim Eintragen in ~/.claude/settings.json ein; hier tut es der
# Verifizierer, so wie er weiter unten auch schon den Port ersetzt.
hook_command="${hook_command//__TOKEN__/$token}"
hook_command="${hook_command//__RUNTIME_DIR__/$runtime}"
# Auch der Port. Weiter unten (Haenge-Messung) passiert dasselbe noch einmal in
# Python; hier braucht es ihn schon, sonst postet die Probe nach 8787 und der
# Test misst, dass nichts ankommt -- was er faelschlich der Umsetzung anlastet.
hook_command="${hook_command//127.0.0.1:8787/127.0.0.1:$port}"
pid_before="$(state 2>/dev/null | jq -r '.sessions' 2>/dev/null || echo -1)"
pid_probe="$tmp/pid-probe.py"
cat >"$pid_probe" <<'PYEOF'
import json, os, subprocess, sys, time
cmd = sys.argv[1]
payload = {"hook_event_name": "PreToolUse", "session_id": "pid-probe",
           "tool_name": "Read", "pid": 99999999}
subprocess.run(["bash", "-c", cmd], input=json.dumps(payload).encode(),
               check=False)
time.sleep(4)
PYEOF
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$python_path" XDG_RUNTIME_DIR="$runtime_root" \
  DAIMON_HOOK_PORT="$port" timeout 8s "$PY" "$pid_probe" "$hook_command" &
pid_keeper=$!
pid_wait=2.7
if [[ "$TARGET" != "$REPO" ]]; then pid_wait=0.65; fi
sleep "$pid_wait"
pid_sessions="$(state 2>/dev/null | jq -r '.sessions' 2>/dev/null || echo -1)"
chk_hub "Claude-Code-PID kommt lebend beim Hub an" \
  "$([[ "$pid_sessions" -gt "$pid_before" ]] 2>/dev/null && echo ja || echo nein)" ja
kill -TERM "$pid_keeper" 2>/dev/null || true
wait "$pid_keeper" 2>/dev/null || true

# Zweite Bridge in einem Netz-Namespace: Loopback funktioniert, Egress nicht.
setsid timeout --signal=TERM 20s unshare -Urn bash -c \
  'ip link set lo up; exec "$@"' shell \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$python_path" \
  XDG_RUNTIME_DIR="$runtime_root" DAIMON_T011_MUTATION="$mutation" \
  "$PY" -m daimon.hookbridge.daemon --runtime-dir "$runtime" \
  --port "$probe_port" --audit-log "$tmp/probe-audit.log" \
  >"$tmp/probe-bridge.log" 2>&1 &
probe_outer=$!
track_pid "$probe_outer"
probe_pid=""
for _ in $(seq 1 50); do
  probe_pid="$("$PY" - "$probe_outer" <<'PYEOF'
import os, sys
p = int(sys.argv[1])
for _ in range(8):
    try:
        children = open(f"/proc/{p}/task/{p}/children").read().split()
    except OSError:
        break
    if not children:
        break
    p = int(children[-1])
print(p)
PYEOF
)"
  code="$(nsenter -t "$probe_pid" -U -n --preserve-credentials curl -sS \
    -o /dev/null -w '%{http_code}' --max-time 0.2 -X POST \
    "http://127.0.0.1:$probe_port/unbekannt" 2>/dev/null || true)"
  [[ "$code" == 404 ]] && break
  sleep 0.05
done
chk "Bridge-Sandbox erreicht ihren Loopback-Port" "$code" 404

setsid timeout --signal=TERM 10s "$PY" -m http.server "$outside_port" \
  --bind 127.0.0.1 >"$tmp/outside.log" 2>&1 &
outside=$!
track_pid "$outside"
for _ in $(seq 1 30); do
  outside_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 0.2 \
    "http://127.0.0.1:$outside_port/" 2>/dev/null || true)"
  [[ "$outside_code" == 200 ]] && break
  sleep 0.05
done
chk "Aussen-Kanarienvogel ist vom normalen Namensraum erreichbar" "$outside_code" 200
nsenter -t "$probe_pid" -U -n --preserve-credentials \
  curl -sS --max-time 0.3 "http://127.0.0.1:$outside_port/" >/dev/null 2>&1
egress_rc=$?
chk "curl aus der Bridge-Sandbox nach aussen scheitert" \
  "$([[ "$egress_rc" -ne 0 ]] && echo ja || echo nein)" ja

# T-1.6-Auflage: echte Konfig gegen einen annehmenden, nie antwortenden Server.
setsid timeout --signal=TERM 15s "$PY" - "$hang_port" >"$tmp/hang.log" 2>&1 <<'PYEOF' &
import socketserver, sys, time
class H(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(65536)
        time.sleep(5)
class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
S(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PYEOF
hanger=$!
track_pid "$hanger"
sleep 0.15
median_ms="$(env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$python_path" XDG_RUNTIME_DIR="$runtime_root" \
  DAIMON_HOOK_PORT="$hang_port" "$PY" - "$hook_command" <<'PYEOF'
import json, statistics, subprocess, sys, time
cmd = sys.argv[1].replace("127.0.0.1:8787", "127.0.0.1:" + __import__("os").environ["DAIMON_HOOK_PORT"])
payload = json.dumps({"hook_event_name":"PreToolUse","session_id":"zeit",
                      "tool_name":"Read"}).encode()
values = []
for _ in range(3):
    start = time.monotonic_ns()
    subprocess.run(["timeout", "2s", "bash", "-c", cmd], input=payload,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    values.append((time.monotonic_ns() - start) / 1_000_000)
print(f"{statistics.median(values):.1f}")
PYEOF
)"
fast="$(awk -v m="$median_ms" 'BEGIN { print (m < 200) ? "ja" : "nein" }')"
chk "Median des echten Hooks bei haengender Bridge bleibt unter 200 ms (${median_ms} ms)" "$fast" ja

if (( ungemessen )); then
  echo
  echo "T-0.11: NICHT VOLLSTAENDIG GEMESSEN. Mindestens ein Kriterium konnte"
  echo "        nicht erhoben werden (Gruende oben, Zeilen mit '????'). Das ist"
  echo "        kein gruener und kein roter Befund; Exit 3."
  exit 3
fi
exit $fail
