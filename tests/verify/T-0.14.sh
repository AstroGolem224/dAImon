#!/usr/bin/env bash
# Verifizierer fuer T-0.14: systemd-Units fuer den Kern.
#
# Zwei Ebenen, und die Trennung ist Absicht:
#
#   * Der TEXT der Unit-Dateien -- das ist die Ebene, auf der die Mutanten
#     angreifen, und die einzige, die in einem DAIMON_FIXTURE pruefbar ist.
#   * Der LAUFENDE Dienst -- `is-active`, `systemctl show`, ein curl-Versuch
#     in der Sandbox, und `ss -ltnp` gegen die MainPID des Hubs. Nur gegen
#     das echte Repo, weil ein Fixture keine Units installieren kann.
#
# Warum beides: eine Direktive in einer Datei ist eine Absichtserklaerung.
# Ob sie wirkt, entscheidet systemd, und das steht erst am laufenden Prozess
# fest. Der Plan verlangt ausdruecklich den curl-Versuch IN der Sandbox --
# nicht die Behauptung, dass er scheitern wuerde.
#
# Der Verifizierer INSTALLIERT Units nach ~/.config/systemd/user/ und startet
# sie. Er raeumt am Ende auf, was er selbst angelegt hat, und laesst stehen,
# was vorher da war.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
UNITDIR="$TARGET/config/systemd"
ZIEL="$HOME/.config/systemd/user"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
hat() { grep -qE "$2" "$1" 2>/dev/null && echo ja || echo nein; }

echo "T-0.14 — systemd-Units fuer den Kern"

HUB="$UNITDIR/daimon-hub.service"
FACE="$UNITDIR/daimon-face.service"

chk "daimon-hub.service existiert" "$([[ -f "$HUB" ]] && echo ja || echo nein)" ja
chk "daimon-face.service existiert" "$([[ -f "$FACE" ]] && echo ja || echo nein)" ja

# Positivkontrolle: die Dateien sind lesbar und nichtleer. Ohne sie waeren
# alle grep-Befunde unten auch bei einer leeren Datei "nicht gefunden" -- und
# das saehe aus wie ein Befund statt wie ein kaputter Aufbau.
zeilen=0
[[ -f "$HUB" ]] && zeilen=$(( zeilen + $(wc -l <"$HUB") ))
chk "Unit-Dateien lesbar und nichtleer (Positivkontrolle)" \
  "$([[ "$zeilen" -gt 10 ]] && echo ja || echo nein)" ja

# --- A. Der Text der Units ----------------------------------------------------
if [[ -f "$HUB" ]]; then
  echo "  -- daimon-hub.service"
  chk "PartOf=graphical-session.target" "$(hat "$HUB" '^PartOf=.*graphical-session\.target')" ja
  chk "Restart=on-failure" "$(hat "$HUB" '^Restart=on-failure')" ja
  chk "RestrictAddressFamilies enthaelt AF_UNIX" \
    "$(hat "$HUB" '^RestrictAddressFamilies=.*AF_UNIX')" ja
  # Der Hub darf kein AF_INET. Das ist der Kern der Zusage: der TCP-Port liegt
  # in der Bridge, nicht im Hub -- deshalb ist die Direktive hier ueberhaupt
  # erfuellbar.
  chk "RestrictAddressFamilies OHNE AF_INET" \
    "$(grep -E '^RestrictAddressFamilies=' "$HUB" | grep -q 'AF_INET' && echo nein || echo ja)" ja
  chk "NoNewPrivileges=yes" "$(hat "$HUB" '^NoNewPrivileges=(yes|true|1)')" ja
  chk "CapabilityBoundingSet leer" "$(hat "$HUB" '^CapabilityBoundingSet=$')" ja
  chk "ProtectSystem=strict" "$(hat "$HUB" '^ProtectSystem=strict')" ja
  # HIER STANDEN `ProtectProc=invisible` und `ProcSubset=pid`. Beide sind in
  # einer `--user`-Unit WIRKUNGSLOS -- systemd.exec(5) sagt es zu
  # `ProcSubset=` ausdruecklich: "it is only available to system services".
  # systemd nimmt die Zeilen widerspruchslos an, und `systemctl --user show`
  # meldet an der laufenden Unit `ProtectProc=default` / `ProcSubset=all`,
  # zeichengleich mit einem Lauf ganz ohne sie. Commit `7b7deb9` (T-4.14) hat
  # sie deshalb aus allen 22 Units entfernt und durch das ersetzt, was in
  # `--user` WIRKT: den leeren `CapabilityBoundingSet=` und die
  # Syscall-Sperrliste. Vier Testdateien tragen denselben Grund im Kommentar
  # (tests/test_egress.py, test_stt.py, test_gpu_worker.py, test_tts.py).
  #
  # Geprueft wird deshalb ihre ABWESENHEIT -- wer sie wieder einsetzt, friert
  # zum vierten Mal eine Zusage ein, die nichts tut -- und in Abschnitt B die
  # WIRKUNG des Ersatzes am laufenden Dienst.
  chk "kein ProtectProc= (in --user wirkungslos, 7b7deb9)" \
    "$(hat "$HUB" '^ProtectProc=')" nein
  chk "kein ProcSubset= (in --user wirkungslos, 7b7deb9)" \
    "$(hat "$HUB" '^ProcSubset=')" nein
  chk "SystemCallFilter gesetzt" "$(hat "$HUB" '^SystemCallFilter=@system-service')" ja
  chk "SystemCallFilter schliesst @privileged aus" \
    "$(hat "$HUB" '^SystemCallFilter=~.*@privileged')" ja
  # Der Ersatz, den T-4.14 zugesagt hat: der Hub steht in Design 7.5 NICHT in
  # der @resources-Ausnahmeliste (das sind auth, ears, eyes, stt, tts,
  # recorder, gpu@, cli-broker, jede mit gemessenem Grund). Er braucht die
  # Gruppe nicht, also darf er sie nicht behalten.
  chk "SystemCallFilter schliesst @resources aus" \
    "$(hat "$HUB" '^SystemCallFilter=~.*@resources')" ja
  chk "InaccessiblePaths deckt .ssh ab" "$(hat "$HUB" '^InaccessiblePaths=.*\.ssh')" ja
  chk "InaccessiblePaths deckt .gnupg ab" "$(hat "$HUB" '^InaccessiblePaths=.*\.gnupg')" ja
  # Der Hub schreibt Ticketbuch und Audit -- ohne ReadWritePaths waere er mit
  # ProtectSystem=strict funktionsunfaehig.
  chk "ReadWritePaths gesetzt (Ticketbuch, Audit)" "$(hat "$HUB" '^ReadWritePaths=')" ja
  # Direktiven, die laut Design 7.5 BRECHEN. Ihr Vorhandensein ist ein Befund,
  # keine Haertung.
  chk "kein ProtectHome=yes (versteckt ~/.config)" \
    "$(hat "$HUB" '^ProtectHome=(yes|true)')" nein
  chk "kein PrivateUsers=yes (bricht Peer-Credentials, T-0.7)" \
    "$(hat "$HUB" '^PrivateUsers=(yes|true)')" nein
  chk "ExecStart nutzt das venv-Python" "$(hat "$HUB" '^ExecStart=.*\.venv/bin/python')" ja
fi

if [[ -f "$FACE" ]]; then
  echo "  -- daimon-face.service (T-1.9)"
  chk "PartOf=graphical-session.target" "$(hat "$FACE" '^PartOf=.*graphical-session\.target')" ja
  chk "After=daimon-hub.service" "$(hat "$FACE" '^After=.*daimon-hub\.service')" ja
  chk "Restart=on-failure" "$(hat "$FACE" '^Restart=on-failure')" ja
  chk "RestrictAddressFamilies enthaelt AF_UNIX" \
    "$(hat "$FACE" '^RestrictAddressFamilies=.*AF_UNIX')" ja
  chk "RestrictAddressFamilies OHNE AF_INET" \
    "$(grep -E '^RestrictAddressFamilies=' "$FACE" | grep -q 'AF_INET' && echo nein || echo ja)" ja
  chk "NoNewPrivileges=yes" "$(hat "$FACE" '^NoNewPrivileges=(yes|true|1)')" ja
  chk "kein ProtectHome=yes" "$(hat "$FACE" '^ProtectHome=(yes|true)')" nein
  # Dieselbe Rechnung wie am Hub (7b7deb9): die zwei wirkungslosen Direktiven
  # sind gestrichen, der wirksame Ersatz wird verlangt. Das Face steht in
  # Design 7.5 ebenfalls nicht in der @resources-Ausnahmeliste.
  chk "kein ProtectProc= (in --user wirkungslos, 7b7deb9)" \
    "$(hat "$FACE" '^ProtectProc=')" nein
  chk "kein ProcSubset= (in --user wirkungslos, 7b7deb9)" \
    "$(hat "$FACE" '^ProcSubset=')" nein
  chk "CapabilityBoundingSet leer" "$(hat "$FACE" '^CapabilityBoundingSet=$')" ja
  chk "SystemCallFilter schliesst @resources aus" \
    "$(hat "$FACE" '^SystemCallFilter=~.*@resources')" ja
fi

chk "docs/INSTALL.md existiert" \
  "$([[ -f "$TARGET/docs/INSTALL.md" ]] && echo ja || echo nein)" ja
chk "INSTALL.md dokumentiert systemd-analyze security" \
  "$(grep -qi 'systemd-analyze' "$TARGET/docs/INSTALL.md" 2>/dev/null && echo ja || echo nein)" ja

# --- B. Der laufende Dienst ---------------------------------------------------
if [[ "$TARGET" != "$REPO" ]]; then
  echo "  INFO Fixture-Lauf: Installation und Laufzeitpruefungen uebersprungen"
  exit $fail
fi
if [[ ! -f "$HUB" || ! -f "$FACE" ]]; then
  echo "  FAIL Laufzeitpruefungen uebersprungen, Units fehlen"
  exit 1
fi

mkdir -p "$ZIEL"
selbst_installiert=()
aufraeumen() {
  for u in "${selbst_installiert[@]:-}"; do
    [[ -z "$u" ]] && continue
    systemctl --user stop "$u" >/dev/null 2>&1
    rm -f "$ZIEL/$u"
  done
  systemctl --user daemon-reload >/dev/null 2>&1
}
trap aufraeumen EXIT

for datei in "$HUB" "$FACE"; do
  name="$(basename "$datei")"
  if [[ ! -e "$ZIEL/$name" ]]; then
    selbst_installiert+=("$name")
  fi
  cp "$datei" "$ZIEL/$name"
done
systemctl --user daemon-reload
chk "daemon-reload laeuft durch" "$?" 0

systemctl --user restart daimon-hub.service >/dev/null 2>&1
chk "daimon-hub startet" "$?" 0
sleep 2
chk "daimon-hub ist aktiv" "$(systemctl --user is-active daimon-hub.service 2>/dev/null)" active

systemctl --user restart daimon-face.service >/dev/null 2>&1
sleep 3
face_aktiv="$(systemctl --user is-active daimon-face.service 2>/dev/null)"
echo "  daimon-face is-active: $face_aktiv"
chk "daimon-face ist aktiv" "$face_aktiv" active

# Die Direktive am laufenden Dienst auslesen, nicht in der Datei. systemd
# koennte sie verwerfen, und dann stuende sie in der Datei und wirkte nicht.
raf_hub="$(systemctl --user show -p RestrictAddressFamilies --value daimon-hub.service 2>/dev/null)"
echo "  Hub RestrictAddressFamilies (laufend): $raf_hub"
chk "Hub meldet AF_UNIX am laufenden Dienst" \
  "$(grep -q 'AF_UNIX' <<<"$raf_hub" && echo ja || echo nein)" ja
chk "Hub meldet KEIN AF_INET am laufenden Dienst" \
  "$(grep -q 'AF_INET' <<<"$raf_hub" && echo nein || echo ja)" ja

# --- B2. Die Haertung, die T-4.14 an die Stelle von ProtectProc gesetzt hat ---
# Der Beleg zur Streichung, gemessen statt behauptet: eine transiente Unit mit
# `ProtectProc=invisible` sieht GENAUSO VIELE fremde PIDs wie eine ohne.
# systemd nimmt die Direktive an und tut nichts -- das ist der Grund aus
# `7b7deb9`, hier als Versuch und nicht als Behauptung.
PROC_PROBE='import os,sys
print(len([p for p in os.listdir("/proc") if p.isdigit()]))'
pids_frei="$(systemd-run --user --wait --quiet --collect --pipe \
  /usr/bin/python3 -c "$PROC_PROBE" 2>/dev/null)"
pids_eng="$(systemd-run --user --wait --quiet --collect --pipe \
  --property=ProtectProc=invisible --property=ProcSubset=pid \
  /usr/bin/python3 -c "$PROC_PROBE" 2>/dev/null)"
echo "  sichtbare PIDs ohne Direktive: ${pids_frei:-?}, mit ProtectProc=invisible: ${pids_eng:-?}"
# Positivkontrolle: die Messung sieht ueberhaupt fremde Prozesse. Ohne sie
# waere "gleich viele" auch bei kaputter Sonde gruen.
chk "die PID-Sonde sieht ueberhaupt fremde Prozesse (Positivkontrolle)" \
  "$([[ "${pids_frei:-0}" -gt 5 ]] && echo ja || echo nein)" ja
chk "ProtectProc=invisible aendert in --user nichts (Beleg zu 7b7deb9)" \
  "$([[ -n "$pids_eng" && "${pids_eng:-0}" -gt 5 ]] && echo ja || echo nein)" ja

# Und nun der Ersatz, an der laufenden Unit gemessen -- nicht im Dateitext.
# Bauform aus tests/verify/t311_pruefstand.py, Kapitel 15.
wert() { systemctl --user show -p "$2" --value "$1" 2>/dev/null; }
# Positivkontrolle der Gruppenaufloesung: eine hier abgetippte Syscall-Liste
# waere eine zweite Fassung derselben Regel. Gefragt wird dieselbe Quelle, aus
# der systemd den Seccomp-Filter baut.
gruppe() { systemd-analyze syscall-filter "$1" 2>/dev/null | sed -n 's/^[[:space:]]\{1,\}\([a-z_0-9]\{1,\}\)$/\1/p'; }
RES_N="$(gruppe @resources | wc -l)"
PRIV_N="$(gruppe @privileged | wc -l)"
chk "@resources ist als Gruppe auslesbar (Positivkontrolle)" \
  "$([[ "$RES_N" -ge 8 ]] && echo ja || echo nein)" ja
chk "@privileged ist als Gruppe auslesbar (Positivkontrolle)" \
  "$([[ "$PRIV_N" -ge 8 ]] && echo ja || echo nein)" ja
for u in daimon-hub.service daimon-face.service; do
  echo "  -- $u (wirksam)"
  chk "$u: NoNewPrivileges wirksam" "$(wert "$u" NoNewPrivileges)" yes
  # Der leere Bounding-Set ist das, was in --user WIRKLICH greift.
  chk "$u: CapabilityBoundingSet leer wirksam" "$(wert "$u" CapabilityBoundingSet)" ""
  chk "$u: ProtectSystem=strict wirksam" "$(wert "$u" ProtectSystem)" strict
  chk "$u: UMask=0077 wirksam" "$(wert "$u" UMask)" 0077
  chk "$u: LimitCORE=0 wirksam" "$(wert "$u" LimitCORE)" 0
  # Zeichengleich mit einem Lauf ganz ohne die Zeilen -- deshalb sind sie weg.
  chk "$u: ProtectProc meldet den Vorgabewert" "$(wert "$u" ProtectProc)" default
  chk "$u: ProcSubset meldet den Vorgabewert" "$(wert "$u" ProcSubset)" all
  filter="$(wert "$u" SystemCallFilter)"
  chk "$u: Syscall-Filter ist aufgeloest lesbar (Positivkontrolle)" \
    "$(grep -qw read <<<"$filter" && echo ja || echo nein)" ja
  # Strenger als der Dateitext: nicht "die Sperrzeile steht da", sondern kein
  # einziger Syscall der Gruppe bleibt im wirksamen Filter uebrig. Weder Hub
  # noch Face stehen in der @resources-Ausnahmeliste aus Design 7.5.
  for g in @resources @privileged; do
    uebrig=""
    for s in $(gruppe "$g"); do
      grep -qw "$s" <<<"$filter" && uebrig="$uebrig $s"
    done
    echo "  $u: $g im wirksamen Filter:${uebrig:- (keiner)}"
    chk "$u: kein $g-Syscall im wirksamen Filter" \
      "$([[ -z "$uebrig" ]] && echo ja || echo nein)" ja
  done
done

# --- C. Der Hub haelt keinen TCP-Socket ---------------------------------------
hub_pid="$(systemctl --user show -p MainPID --value daimon-hub.service 2>/dev/null)"
chk "Hub-MainPID ist auslesbar (Positivkontrolle)" \
  "$([[ "$hub_pid" =~ ^[0-9]+$ && "$hub_pid" -gt 0 ]] && echo ja || echo nein)" ja
if [[ "$hub_pid" =~ ^[0-9]+$ && "$hub_pid" -gt 0 ]]; then
  # Positivkontrolle der Messung: ss sieht ueberhaupt Sockets. Ohne sie waere
  # "0 TCP-Sockets" auch bei kaputtem ss gruen -- die Nullaussage, die dieses
  # Projekt schon mehrfach gekostet hat.
  ss_zeilen="$(ss -ltnp 2>/dev/null | wc -l)"
  chk "ss liefert ueberhaupt Zeilen (Positivkontrolle)" \
    "$([[ "$ss_zeilen" -gt 1 ]] && echo ja || echo nein)" ja
  tcp="$(ss -ltnp 2>/dev/null | grep -c "pid=$hub_pid," || true)"
  [[ "$tcp" =~ ^[0-9]+$ ]] || tcp=0
  echo "  TCP-Listen-Sockets des Hubs: $tcp"
  chk "Hub haelt keinen TCP-Socket" "$tcp" 0
  # Und ein Unix-Socket MUSS er halten, sonst ist die 0 oben bedeutungslos.
  #
  # NICHT ueber /proc/<pid>/fd: die Haertung `PR_SET_DUMPABLE=0` aus Design
  # 7.5 sperrt genau diesen Zugriff -- eine erfolgreiche Haertung haette hier
  # als Befund ausgesehen. Geprueft wird stattdessen das Verhalten: der Hub
  # legt seine Sockets im Laufzeitverzeichnis an und antwortet darauf.
  rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/daimon"
  sock_da=nein
  for kandidat in "$rt/state.sock" "$rt/diag.sock" "$rt/events.sock"; do
    [[ -S "$kandidat" ]] && sock_da=ja && break
  done
  chk "Hub haelt ueberhaupt Unix-Sockets (Positivkontrolle)" "$sock_da" ja
fi

# --- D. curl IN der Sandbox ---------------------------------------------------
# Der Plan verlangt den Versuch, nicht die Behauptung. Gefahren wird er mit
# denselben Direktiven wie der Hub, ueber systemd-run.
# Nicht ueber curl-Exitcodes: beide Varianten liefern hier rc=7
# ("couldn't connect"), weil auf Port 1 niemand hoert. Gleiche Codes hiessen,
# die Beschraenkung habe nichts bewirkt -- dabei hat sie nur nichts
# UNTERSCHEIDBARES bewirkt. Der Unterschied liegt eine Ebene tiefer: unter
# `RestrictAddressFamilies=AF_UNIX` laesst sich ein AF_INET-Socket gar nicht
# erst anlegen. Das ist die Zusage, und die ist direkt messbar.
PROBE='import socket,sys
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    sys.exit(0)
except OSError:
    sys.exit(9)'
systemd-run --user --wait --quiet --collect \
  --property=RestrictAddressFamilies="AF_UNIX AF_INET AF_INET6" \
  /usr/bin/python3 -c "$PROBE" >/dev/null 2>&1
frei_rc=$?
systemd-run --user --wait --quiet --collect \
  --property=RestrictAddressFamilies="AF_UNIX" \
  /usr/bin/python3 -c "$PROBE" >/dev/null 2>&1
eng_rc=$?
echo "  AF_INET-Socket ohne Beschraenkung: rc=$frei_rc, mit AF_UNIX-Beschraenkung: rc=$eng_rc"
# Positivkontrolle: ohne Beschraenkung MUSS es gehen. Sonst waere das
# Scheitern unten kein Beleg fuer die Sandbox, sondern fuer einen kaputten
# Aufbau -- die Nullaussage, die dieses Projekt schon mehrfach gekostet hat.
chk "ohne Beschraenkung gelingt ein AF_INET-Socket (Positivkontrolle)" "$frei_rc" 0
chk "in der AF_UNIX-Sandbox scheitert er" \
  "$([[ "$eng_rc" -ne 0 ]] && echo ja || echo nein)" ja

# Und der Vollstaendigkeit halber der vom Plan verlangte curl-Versuch selbst.
if command -v curl >/dev/null 2>&1; then
  systemd-run --user --wait --quiet --collect \
    --property=RestrictAddressFamilies="AF_UNIX" \
    curl -s -m 3 -o /dev/null http://127.0.0.1:1 >/dev/null 2>&1
  chk "curl in der AF_UNIX-Sandbox scheitert" \
    "$([[ "$?" -ne 0 ]] && echo ja || echo nein)" ja
fi

# --- E. systemd-analyze security ----------------------------------------------
for u in daimon-hub daimon-face; do
  note="$(systemd-analyze security --user "$u.service" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+ [A-Z]+' | tail -1)"
  echo "  systemd-analyze security $u: ${note:-nicht ermittelbar}"
done
chk "systemd-analyze security ist ausfuehrbar (Positivkontrolle)" \
  "$(systemd-analyze security --user daimon-hub.service >/dev/null 2>&1 && echo ja || echo nein)" ja

exit $fail
