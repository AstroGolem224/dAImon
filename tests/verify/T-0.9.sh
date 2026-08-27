#!/usr/bin/env bash
# Verifizierer fuer T-0.9: Hub, Bus und State.
#
# Die Socket-Inodes werden am LAUFENDEN Prozess mit /proc/<pid>/fd korreliert.
# Eine reine ss-Ausgabe kann die PID wegen PR_SET_DUMPABLE=0 nicht nennen. Am
# Quelltext waere das leicht zu uebersehen: eine Bibliothek koennte hinter
# unserem Ruecken einen Debug-Port oeffnen. Der Punkt ist nicht Vorsicht,
# sondern dass RestrictAddressFamilies=AF_UNIX in T-0.14 erfuellbar bleibt --
# ein einziger AF_INET macht die Direktive unmoeglich, und dann faellt eine
# ganze Schutzschicht weg, weil jemand einen Endpunkt bequem fand.
#
# Ein DAIMON_FIXTURE zeigt auf einen Baum mit einem `daimon/`-Paket; der wird
# ueber PYTHONPATH gestartet statt des Arbeitsbaums. Ohne das koennte
# `meta.sh T-0.9` nicht messen: es reicht jedem Lauf ein Verzeichnis, und ein
# Verifizierer, der es ignoriert, misst neunmal denselben Baum und meldet
# jede Mutante als "nicht erkannt". Dass der Baum wirklich getauscht ist,
# steht als eigenes Kriterium unten -- "Hub-Modul stammt aus dem geprueften
# Baum" -- und nicht als Annahme.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Normalisiert, weil unten Pfade VERGLICHEN werden: ein DAIMON_FIXTURE mit
# Schraegstrich am Ende haette das Kriterium "Hub-Modul stammt aus dem
# geprueften Baum" rot gemacht, obwohl der Baum stimmt.
TARGET="$(cd "${DAIMON_FIXTURE:-$REPO}" 2>/dev/null && pwd)"
[[ -n "$TARGET" ]] || { echo "T-0.9: DAIMON_FIXTURE ${DAIMON_FIXTURE:-} ist kein Verzeichnis" >&2; exit 2; }
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
fail=0
ungemessen=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
info() { echo "  INFO $*"; }
# "Nichts gefunden" und "nicht gemessen" sind zwei Aussagen. Vier
# Falschbefunde an einem Tag kamen genau aus der Verwechslung -- alle vier
# meldeten Ruhe, wo die Vorrichtung kaputt war. Ein ungemessenes Kriterium
# wird darum weder gruen noch rot, sondern benannt, und der Lauf endet mit
# Exit 3.
nicht_gemessen() { echo "  ????  $1 -- NICHT GEMESSEN: $2"; ungemessen=1; }

echo "T-0.9 — Hub: Bus und State"
for f in daemon state bus; do
  chk "daimon/hub/$f.py existiert" "$([[ -f "$TARGET/daimon/hub/$f.py" ]] && echo ja || echo nein)" ja
done

if [[ -n "${DAIMON_FIXTURE:-}" ]]; then
  info "pytest tests/test_hub.py: nicht gewertet -- die Suite gehoert dem"
  info "Arbeitsbaum, nicht dem geprueften Baum. Gemessen wird hier der"
  info "laufende Hub AUS dem Fixture."
else
  xml="$(mktemp --suffix=.xml)"
  ( cd "$REPO" && timeout 300 "$PY" -m pytest tests/test_hub.py --tb=no -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
  read -r passed failed <<<"$("$PY" -c '
import sys, xml.etree.ElementTree as ET
r = ET.parse(sys.argv[1]).getroot(); s = r if r.tag=="testsuite" else r.find("testsuite")
p=f=0
for c in s.iter("testcase"):
    k={x.tag for x in c}
    if k & {"failure","error"}: f+=1
    elif "skipped" not in k: p+=1
print(p,f)' "$xml")"
  rm -f "$xml"
  echo "  pytest: $passed passed, $failed failed"
  chk "keine fehlgeschlagenen Tests" "$failed" 0
  chk "es laufen Tests" "$([[ $passed -gt 0 ]] && echo ja || echo nein)" ja
fi

# --- am laufenden Prozess -------------------------------------------------
tmp="$(mktemp -d)"; rt="$tmp/rt"
hub=""; kontrolle=""
aufräumen() {
  [[ -n "$hub" ]] && kill "$hub" 2>/dev/null || true
  [[ -n "$kontrolle" ]] && kill "$kontrolle" 2>/dev/null || true
  [[ -n "$hub" ]] && wait "$hub" 2>/dev/null || true
  [[ -n "$kontrolle" ]] && wait "$kontrolle" 2>/dev/null || true
  rm -rf -- "$tmp"
}
trap aufräumen EXIT INT TERM

# Die Positivkontrolle benutzt denselben Inode-/fd-Abgleich. Ohne sie koennte
# eine kaputte Korrelation sowohl Unix als auch TCP faelschlich leer melden.
probe_dir="$REPO/tests/harness/t09_socket_probe"
kontroll_sock="$tmp/positiv.sock"; kontroll_befund="$tmp/positiv.json"
( env PYTHONPATH="$probe_dir${PYTHONPATH:+:$PYTHONPATH}" \
    DAIMON_T09_SOCKET_BEFUND="$kontroll_befund" \
    DAIMON_T09_POSITIV_SOCKET="$kontroll_sock" \
    "$PY" -c 'import time; time.sleep(300)' ) >"$tmp/positiv.log" 2>&1 &
kontrolle=$!
for _ in $(seq 1 80); do [[ -s "$kontroll_befund" ]] && break; sleep 0.05; done
positiv="$(jq -r --arg p "$kontroll_sock" '[.unix[] | select(.path == $p)] | length' "$kontroll_befund" 2>/dev/null)"
chk "Positivkontrolle korreliert bekannten Unix-Listener mit seinem Prozess-fd" "${positiv:-0}" 1

# Zweite Positivkontrolle, und die entscheidet ueber den ganzen
# Mutationstest: laeuft der Hub gleich aus dem geprueften Baum? Ein
# `.venv`-Editable-Install oder ein vergessenes `cd` wuerde ihn aus dem
# Arbeitsbaum starten -- jede Mutante waere dann gruen, und der gruene
# meta.sh-Lauf hiesse nichts.
modul="$( cd "$TARGET" && env PYTHONPATH="$TARGET" "$PY" \
          -c 'import daimon.hub.daemon as m; print(m.__file__)' 2>/dev/null )"
chk "Hub-Modul stammt aus dem geprueften Baum" \
  "$([[ "$modul" == "$TARGET/daimon/hub/daemon.py" ]] && echo ja || echo nein)" ja
[[ "$modul" == "$TARGET/daimon/hub/daemon.py" ]] || info "geladen wurde: ${modul:-nichts}"

hub_befund="$tmp/hub-sockets.json"
( cd "$TARGET" && env XDG_RUNTIME_DIR="$tmp" \
    PYTHONPATH="$probe_dir:$TARGET${PYTHONPATH:+:$PYTHONPATH}" \
    DAIMON_T09_SOCKET_BEFUND="$hub_befund" \
    "$PY" -m daimon.hub.daemon --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
# Gewartet wird auf den FERTIGEN Befund, nicht auf die erste Datei: die Sonde
# schreibt ihre erste Fassung schon beim Start des Interpreters, also bevor
# der Hub gebunden hat. Wer nur auf "Datei nicht leer" wartet, misst unter
# Last die leere Fassung -- gemessen am 27.08.: derselbe Verifizierer unter
# `strace -f` meldete 0 statt 2 horchende Unix-Sockets, im blanken Lauf 2.
# Die Schleife bricht nach 10 s ab und laesst dann den zuletzt gelesenen
# Wert stehen; sie verdeckt also keinen Befund, sie wartet nur auf einen.
unix_n=0
for _ in $(seq 1 200); do
  if [[ -S "$rt/state.sock" && -s "$hub_befund" ]]; then
    unix_n="$(jq -r --arg s "$rt/state.sock" --arg h "$rt/hookbridge.sock" \
      '[.unix[] | select(.path == $s or .path == $h)] | length' "$hub_befund" 2>/dev/null)"
    [[ "${unix_n:-0}" == 2 ]] && break
  fi
  sleep 0.05
done

chk "Hub laeuft" "$(kill -0 $hub 2>/dev/null && echo ja || echo nein)" ja
chk "state.sock existiert" "$([[ -S "$rt/state.sock" ]] && echo ja || echo nein)" ja
chk "state.sock hat Modus 0600" "$(stat -c '%a' "$rt/state.sock" 2>/dev/null)" 600
chk "hookbridge.sock hat Modus 0600" "$(stat -c '%a' "$rt/hookbridge.sock" 2>/dev/null)" 600

owner="$(jq -r '.pid // 0' "$hub_befund" 2>/dev/null)"
chk "Socket-Befund stammt vom wirklichen Hub-Prozess" "${owner:-0}" "$hub"
chk "haelt beide horchenden Unix-Sockets laut Inode-/fd-Korrelation" "${unix_n:-0}" 2

tcp_n="$(jq -r '.tcp | length' "$hub_befund" 2>/dev/null)"
chk "haelt KEINEN horchenden TCP-Socket" "${tcp_n:-0}" 0

# Zustellung ueber den echten Socket, nicht ueber importierten Code.
sender="$tmp/sender.py"
cat >"$sender" <<'PYEOF'
import json, socket, sys, time
from pathlib import Path
was, rt = sys.argv[1], Path(sys.argv[2])
if was == "senden":
    # Erster Handgriff, vor jedem Socket: die Marke belegt, dass die Scope
    # diesen Prozess wirklich gestartet hat. Fehlt sie, ist alles Weitere
    # NICHT GEMESSEN; ist sie da, ist ein Fehlschlag ein Befund.
    Path(sys.argv[3]).write_text("1", encoding="utf-8")
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(rt / "hookbridge.sock"))
    c.sendall(json.dumps({"v":1,"type":"hook","payload":{
        "hook_event_name":"Notification","session_id":"v1",
        "notification_type":"permission_prompt","message":"probe"}}).encode()+b"\n")
    time.sleep(0.4); c.close()
else:
    # Bis zu 5 s auf eine Regung warten und dann den ZULETZT gelesenen Stand
    # ausgeben. Der Hub verarbeitet das Ereignis nebenlaeufig; ein einzelner
    # Blick misst unter Last den Zustand davor, nicht das Ausbleiben einer
    # Wirkung. Verdeckt wird dadurch nichts: bleibt es beim Anfangszustand,
    # steht der am Ende genauso da.
    for _ in range(100):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(rt / "state.sock"))
        d = json.loads(s.makefile("rb").readline()); s.close()
        if d["mood"] != "sleeping":
            break
        time.sleep(0.05)
    print(json.dumps({"mood": d["mood"], "v": d["v"], "bubble": bool(d["bubble"])}))
PYEOF

# WARUM EINE SCOPE: seit der Haertung vom 19.08. prueft `_horche_produzent`
# die Unit der Gegenstelle gegen `PRODUZENT_UNITS`. Ein Prueflauf laeuft
# unter der Unit seiner Sitzung (gemessen: `app-com.anthropic.Claude-2954.
# scope`) und wird abgewiesen -- dieser Verifizierer war deshalb an zwei
# Kriterien rot. Seit bd0bb8e steht `daimon-verify.scope` ausdruecklich in
# `PRODUZENT_UNITS["hookbridge"]` (und NUR dort), also haengt sich der
# Sender selbst dort hinein. Vorbild: `daimon/plan/__main__.py:
# in_eigene_scope`.
#
# ponytail: fester Unit-Name, keine Eindeutigkeit -- und das ist hier keine
# Bequemlichkeit, sondern erzwungen. `ipc.unit_erlaubt` vergleicht exakt;
# der `@`-Zweig fuer Templates verlangt zusaetzlich eine Endung `.service`
# und traegt eine `.scope` nicht. Ein Zufallssuffix stuende also auf keiner
# Liste. DECKE: zwei GLEICHZEITIGE T-0.9-Laeufe -- der zweite bekommt von
# systemd "unit already exists". Das wird unten als NICHT GEMESSEN
# ausgewiesen und NICHT als roter Befund; ein Prueflauf, der an einem
# belegten Unit-Namen scheitert, hat ueber den Hub nichts ausgesagt.
# Ausbaupfad, wenn das je stoert: `@`-Template in PRODUZENT_UNITS zulassen
# (dann in `ipc.unit_erlaubt` auch fuer `.scope`) und hier ein Suffix.
#
# Die Grenze zwischen "nicht gemessen" und "rot" zieht die MARKE, nicht der
# Exit-Code: `systemd-run --scope` reicht den Exit-Code des Kindes durch,
# ein abgewiesener Socket saehe also genauso aus wie eine Scope, die nie
# entstand. Legt der Sender seine Marke ab, hat die Scope ihn gestartet --
# was danach schiefgeht, ist ein Befund ueber den Hub und gehoert rot.
scope_grund=""
marke="$tmp/sender-lief"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && ! -S "${XDG_RUNTIME_DIR:-/nichts}/bus" ]]; then
  scope_grund="kein Nutzerbus (DBUS_SESSION_BUS_ADDRESS leer, kein \$XDG_RUNTIME_DIR/bus)"
elif ! command -v systemd-run >/dev/null 2>&1; then
  scope_grund="systemd-run fehlt"
else
  systemd-run --user --quiet --collect --scope --unit=daimon-verify.scope \
    -- "$PY" -P "$sender" senden "$rt" "$marke" >"$tmp/scope.log" 2>&1
  rc=$?
  if [[ ! -f "$marke" ]]; then
    scope_grund="Sender lief nicht in der Scope (Exit $rc): $(tr '\n' ' ' <"$tmp/scope.log" | cut -c1-200)"
  elif (( rc != 0 )); then
    info "Sender lief in daimon-verify.scope, endete aber mit Exit $rc:"
    info "$(tr '\n' ' ' <"$tmp/scope.log" | cut -c1-200)"
  fi
fi

# Der State ist lesend und prueft keine Unit -- Schema v2 bleibt darum auch
# dann messbar, wenn die Scope nicht zustande kam.
antwort="$("$PY" -P "$sender" lesen "$rt" 2>/dev/null)"
chk "State liefert Schema v2" "$(jq -r '.v' <<<"$antwort" 2>/dev/null)" 2

if [[ -n "$scope_grund" ]]; then
  nicht_gemessen "Ereignis erreicht den State ueber den Socket" "$scope_grund"
  nicht_gemessen "Bubble ist gesetzt" "$scope_grund"
else
  chk "Ereignis erreicht den State ueber den Socket" \
    "$(jq -r '.mood' <<<"$antwort" 2>/dev/null)" needs_input
  chk "Bubble ist gesetzt" "$(jq -r '.bubble' <<<"$antwort" 2>/dev/null)" true
fi

if (( ungemessen )); then
  echo
  echo "T-0.9: NICHT VOLLSTAENDIG GEMESSEN. Mindestens ein Kriterium konnte"
  echo "       nicht erhoben werden (Gruende oben, Zeilen mit '????'). Das ist"
  echo "       kein gruener und kein roter Befund; Exit 3."
  exit 3
fi
exit $fail
