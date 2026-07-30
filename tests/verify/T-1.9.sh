#!/usr/bin/env bash
# Verifizierer fuer T-1.9: systemd-Unit fuer das Face.
#
# T-0.14.sh prueft die Unit-Datei bereits auf ihre Direktiven. Dieser hier
# prueft, was T-1.9 zusaetzlich zusagt und was nur am LAUFENDEN Dienst zu
# sehen ist: dass das Face unter systemd wirklich hochkommt, dass sein
# Diagnose-Socket antwortet, und dass die Sandbox ausgehende Netzverbindungen
# unmoeglich macht.
#
# Der Unterschied ist nicht akademisch. Das Face braucht den Wayland-Socket;
# `RestrictAddressFamilies` ohne `AF_UNIX` wuerde ihn wegnehmen, und das
# Overlay startete gar nicht erst (Design 7.5, "Direktiven, die brechen").
# Ob die Haertung das ueberlebt, steht erst fest, wenn der Dienst laeuft.
#
# Der Verifizierer installiert die Unit nach ~/.config/systemd/user/ und
# raeumt am Ende auf, was er selbst angelegt hat.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
UNIT="$TARGET/config/systemd/daimon-face.service"
ZIEL="$HOME/.config/systemd/user"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
hat() { grep -qE "$2" "$1" 2>/dev/null && echo ja || echo nein; }

echo "T-1.9 — systemd-Unit fuer das Face"
chk "Unit existiert" "$([[ -f "$UNIT" ]] && echo ja || echo nein)" ja
[[ -f "$UNIT" ]] || exit 1

# Positivkontrolle: die Datei ist lesbar und nichtleer, sonst waeren alle
# grep-Befunde unten Nullaussagen.
chk "Unit ist lesbar und nichtleer (Positivkontrolle)" \
  "$([[ "$(wc -l <"$UNIT")" -gt 10 ]] && echo ja || echo nein)" ja

echo "  -- Die Zusagen aus der Akzeptanzliste"
chk "PartOf=graphical-session.target" "$(hat "$UNIT" '^PartOf=.*graphical-session\.target')" ja
chk "After=daimon-hub.service" "$(hat "$UNIT" '^After=.*daimon-hub\.service')" ja
chk "RestrictAddressFamilies=AF_UNIX" "$(hat "$UNIT" '^RestrictAddressFamilies=AF_UNIX')" ja
chk "Restart=on-failure" "$(hat "$UNIT" '^Restart=on-failure')" ja

if [[ "$TARGET" != "$REPO" ]]; then
  echo "  INFO Fixture-Lauf: Installation und Laufzeitpruefungen uebersprungen"
  exit $fail
fi

# --- Am laufenden Dienst ------------------------------------------------------
mkdir -p "$ZIEL"
selbst=0
[[ -e "$ZIEL/daimon-face.service" ]] || selbst=1
aufraeumen() {
  if (( selbst )); then
    systemctl --user stop daimon-face.service >/dev/null 2>&1
    rm -f "$ZIEL/daimon-face.service"
    systemctl --user daemon-reload >/dev/null 2>&1
  fi
}
trap aufraeumen EXIT

# Der Hub muss laufen, sonst geht das Face auf `sleeping` -- das waere kein
# Fehler, aber die Diagnosepruefung unten waere weniger aussagekraeftig.
[[ -f "$TARGET/config/systemd/daimon-hub.service" ]] && \
  cp "$TARGET/config/systemd/daimon-hub.service" "$ZIEL/" 2>/dev/null
cp "$UNIT" "$ZIEL/daimon-face.service"
systemctl --user daemon-reload
chk "daemon-reload laeuft durch" "$?" 0
systemctl --user restart daimon-hub.service >/dev/null 2>&1

systemctl --user restart daimon-face.service >/dev/null 2>&1
chk "systemctl --user restart nimmt die Unit an" "$?" 0
sleep 4
aktiv="$(systemctl --user is-active daimon-face.service 2>/dev/null)"
echo "  is-active: $aktiv"
chk "Dienst ist aktiv" "$aktiv" active

# Positivkontrolle der Messung: der Dienst hat wirklich einen Prozess. Ein
# "active" ohne MainPID waere eine Selbstauskunft von systemd.
pid="$(systemctl --user show -p MainPID --value daimon-face.service 2>/dev/null)"
chk "MainPID ist gesetzt (Positivkontrolle)" \
  "$([[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] && echo ja || echo nein)" ja

# --- Der Diagnose-Socket antwortet --------------------------------------------
# Das ist der eigentliche Beleg, dass die Haertung den Wayland-Zugang nicht
# mitgenommen hat: ein Face ohne Wayland kommt nie bis zum ersten Commit und
# legt seinen Diagnose-Socket zwar an, aber READY faellt aus.
sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/daimon/face-diag.sock"
da=nein
for _ in $(seq 1 40); do
  [[ -S "$sock" ]] && da=ja && break
  sleep 0.25
done
chk "Diagnose-Socket existiert" "$da" ja
if [[ "$da" == ja ]]; then
  antwort="$("$PY" - "$sock" <<'PYEOF'
import socket, sys
try:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5); c.connect(sys.argv[1])
    print(c.makefile("rb").readline().decode().strip())
    c.close()
except Exception as fehler:  # noqa: BLE001
    print(f"FEHLER {fehler!r}")
PYEOF
)"
  echo "  Diagnose: $antwort"
  chk "Diagnose antwortet mit gueltigem JSON" \
    "$(jq -e 'has("frames_rendered")' <<<"$antwort" >/dev/null 2>&1 && echo ja || echo nein)" ja
  # Und es hat wirklich gezeichnet -- ein Face, das nur den Socket anlegt und
  # nie einen Puffer committet, waere hier sonst gruen.
  chk "das Face hat unter systemd wirklich gezeichnet" \
    "$([[ "$(jq -r '.frames_rendered' <<<"$antwort" 2>/dev/null)" -gt 0 ]] && echo ja || echo nein)" ja
fi

# --- Kein Netz aus der Sandbox ------------------------------------------------
# Gefahren mit denselben Direktiven wie die Unit. Nicht ueber curl-Exitcodes:
# die sind bei nicht erreichbaren Zielen ohnehin ungleich null und wuerden
# jede Sandbox gruen aussehen lassen. Geprueft wird, ob sich ueberhaupt ein
# AF_INET-Socket anlegen laesst.
PROBE='import socket,sys
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    sys.exit(0)
except OSError:
    sys.exit(9)'
systemd-run --user --wait --quiet --collect \
  --property=RestrictAddressFamilies="AF_UNIX AF_INET AF_INET6" \
  /usr/bin/python3 -c "$PROBE" >/dev/null 2>&1
frei=$?
systemd-run --user --wait --quiet --collect \
  --property=RestrictAddressFamilies="AF_UNIX" \
  /usr/bin/python3 -c "$PROBE" >/dev/null 2>&1
eng=$?
echo "  AF_INET ohne Beschraenkung: rc=$frei, mit AF_UNIX: rc=$eng"
chk "ohne Beschraenkung gelingt es (Positivkontrolle)" "$frei" 0
chk "in der Face-Sandbox scheitert es" "$([[ "$eng" -ne 0 ]] && echo ja || echo nein)" ja

if command -v curl >/dev/null 2>&1; then
  systemd-run --user --wait --quiet --collect \
    --property=RestrictAddressFamilies="AF_UNIX" \
    curl -s -m 3 -o /dev/null http://127.0.0.1:1 >/dev/null 2>&1
  chk "curl aus der Sandbox schlaegt fehl" "$([[ "$?" -ne 0 ]] && echo ja || echo nein)" ja
fi

# --- Restart=on-failure wirkt -------------------------------------------------
# Die Direktive steht in der Datei; ob sie greift, zeigt erst ein Abschuss.
if [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]]; then
  kill -9 "$pid" 2>/dev/null
  neu=""
  for _ in $(seq 1 40); do
    sleep 0.5
    neu="$(systemctl --user show -p MainPID --value daimon-face.service 2>/dev/null)"
    [[ "$neu" =~ ^[0-9]+$ && "$neu" -gt 0 && "$neu" != "$pid" ]] && break
  done
  echo "  MainPID vorher $pid, nach dem Abschuss $neu"
  chk "der Dienst kommt nach einem Absturz von selbst wieder" \
    "$([[ "$neu" =~ ^[0-9]+$ && "$neu" -gt 0 && "$neu" != "$pid" ]] && echo ja || echo nein)" ja
fi

exit $fail
