#!/usr/bin/env bash
# Verifizierer fuer T-1.8: Ton bei needs_input.
#
# Der Plan verlangt genau eine Messung: die Folge
#   needs_input, needs_input, done, failed, needs_input
# muss den Tonzaehler auf GENAU 2 bringen. Zwei Wechsel NACH needs_input,
# und weder die Wiederholung noch done noch failed zaehlen mit.
#
# Warum das mehr ist als Erbsenzaehlen: `needs_input` und `failed` bilden
# beide auf denselben Sprite `dringend` ab. Wer den Ton am Sprite festmacht
# statt am Mood, piept bei jedem Fehlschlag -- und die Akzeptanzliste
# verbietet das ausdruecklich. Die Folge oben faengt genau diesen Fehler:
# ein sprite-basierter Ausloeser kaeme auf 3.
#
# Gemessen wird am laufenden Prozess ueber Steuer- und Diagnose-Socket, nicht
# am Quelltext. Ein DAIMON_FIXTURE zeigt auf einen Ersatzbaum mit einem
# Stand-in unter <fixture>/face/target/debug/daimon-face.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
num_gt() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 > $2 )); then echo ja; else echo nein; fi; }

echo "T-1.8 — Ton bei needs_input"

# --- Bauen -------------------------------------------------------------------
if [[ "$TARGET" == "$REPO" ]]; then
  # IMMER bauen. `cargo test` baut den Unit-Test-Harness, nicht das Bin-Target;
  # ein Verifizierer, der ein Binary von vorgestern startet, misst den Stand
  # von vorgestern und bleibt gruen, waehrend die Quelle kaputt ist. Genau das
  # ist bei T-1.4 passiert.
  ( cd "$REPO/face" && timeout 600 cargo build -p face ) >/dev/null 2>&1
  chk "cargo build -p face laeuft durch" "$?" 0
  neuer_stand="$(find "$REPO/face/src" "$REPO/face/Cargo.toml" -newer "$BIN" 2>/dev/null | head -1)"
  chk "Binary ist nicht aelter als die Quellen" \
    "$([[ -z "$neuer_stand" ]] && echo ja || echo nein)" ja

  ct_out="$(cd "$REPO/face" && timeout 600 cargo test -p face 2>&1)"
  ct_rc=$?
  read -r ct_passed ct_failed <<<"$(awk '/^test result:/ {p+=$4; f+=$6} END {print p+0, f+0}' <<<"$ct_out")"
  echo "  cargo test: $ct_passed passed, $ct_failed failed"
  chk "cargo test laeuft durch" "$ct_rc" 0
  chk "keine fehlgeschlagenen Rust-Tests" "$ct_failed" 0
  chk "es liefen ueberhaupt Tests (Positivkontrolle)" "$(num_gt "$ct_passed" 0)" ja
fi
chk "Pruefling vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja

wayland_da="$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)"
chk "Wayland-Sitzung vorhanden" "$wayland_da" ja

tmp="$(mktemp -d)"
pid=""
cleanup() {
  [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
  [[ -n "$pid" ]] && wait "$pid" 2>/dev/null
  rm -rf -- "$tmp"
}
trap cleanup EXIT

# Untergeschobener Abspieler. Der Pruefling ruft `canberra-gtk-play` bzw.
# `paplay` ueber den PATH auf; hier stehen Attrappen davor, die jeden Aufruf
# protokollieren.
#
# Ohne das beobachtet dieser Verifizierer nur den Zaehler, den der Pruefling
# SELBST fuehrt -- und ein Pruefling, der gar keinen Ton startet, sondern
# bloss hochzaehlt, bestuende jede Pruefung. Genau diese Luecke hat das
# Gegenlesen gefunden, und sie ist der wiederkehrende Fehler dieses Projekts:
# die bequeme Groesse messen statt der richtigen.
mkdir -p "$tmp/bin"
for programm in canberra-gtk-play paplay; do
  {
    echo '#!/usr/bin/env bash'
    echo "echo \"\$programm \$*\" >> '$tmp/abspiel.log'"
    echo 'exit 0'
  } > "$tmp/bin/$programm"
  sed -i "s|\$programm|$programm|" "$tmp/bin/$programm"
  chmod +x "$tmp/bin/$programm"
done
: > "$tmp/abspiel.log"
export PATH="$tmp/bin:$PATH"
abspielrufe() {
  local n
  n="$(grep -c . "$tmp/abspiel.log" 2>/dev/null)"
  [[ "$n" =~ ^[0-9]+$ ]] && echo "$n" || echo 0
}

ctl() { "$PY" - "$1" "$2" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
diag() { "$PY" - "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

# Startet den Pruefling und wartet auf READY. `$1` sind zusaetzliche Argumente.
starten() {
  rm -f "$tmp/d.sock" "$tmp/c.sock"
  DAIMON_MAX_SECS=40 ${TON_UMGEBUNG:+env DAIMON_FACE_TON=$TON_UMGEBUNG} "$BIN" \
    --diag-socket "$tmp/d.sock" --control-socket "$tmp/c.sock" $1 \
    >"$tmp/out.log" 2>"$tmp/err.log" &
  pid=$!
  for _ in $(seq 1 120); do
    grep -q READY "$tmp/out.log" 2>/dev/null && return 0
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}
beenden() { [[ -n "$pid" ]] && kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; pid=""; }
toene() { diag "$tmp/d.sock" 2>/dev/null | jq -r '.toene_gespielt' 2>/dev/null; }

if [[ "$wayland_da" != ja || ! -x "$BIN" ]]; then
  echo "  FAIL Live-Pruefungen uebersprungen (keine Wayland-Sitzung oder kein Binary)"
  exit 1
fi

# --- A. Die Plan-Folge --------------------------------------------------------
TON_UMGEBUNG=""
starten ""
chk "Overlay meldet READY" "$?" 0

d0="$(diag "$tmp/d.sock")"
chk "Diagnose kennt das Feld toene_gespielt" \
  "$(jq -e 'has("toene_gespielt")' <<<"$d0" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "Zaehler startet bei 0" "$(toene)" 0

# Positivkontrolle des Steuerkanals: Unsinn muss abgewiesen werden, sonst
# beweist ein "ok" auf einen echten Mood nichts.
chk "Steuer-Socket weist unbekannten Mood mit err ab" \
  "$(ctl "$tmp/c.sock" 'mood gibt-es-nicht')" err
chk "Steuer-Socket nimmt einen bekannten Mood an" \
  "$(ctl "$tmp/c.sock" 'mood idle')" ok
chk "Zaehler steigt bei idle NICHT" "$(toene)" 0

# Die Folge aus dem Plan -- aber JE UEBERGANG geprueft, nicht nur die Summe.
# Eine spritebasierte Umsetzung koennte bei `done -> failed` faelschlich
# spielen und bei `failed -> needs_input` faelschlich schweigen und trotzdem
# auf die Gesamtzahl 2 kommen. Der Summenvergleich allein haette das
# durchgelassen -- das ist derselbe Fehler wie immer, nur eine Ebene hoeher:
# die bequeme Groesse messen statt der richtigen.
#
# Erwartet je Schritt: erst der Uebergang, dann das Delta.
#   sleeping    -> needs_input : 1
#   needs_input -> needs_input : 0
#   needs_input -> done        : 0
#   done        -> failed      : 0
#   failed      -> needs_input : 1
erwartet=(1 0 0 0 1)
i=0
for m in needs_input needs_input done failed needs_input; do
  vor_z="$(toene)"; vor_r="$(abspielrufe)"
  chk "mood $m wird angenommen" "$(ctl "$tmp/c.sock" "mood $m")" ok
  sleep 0.7
  nach_z="$(toene)"; nach_r="$(abspielrufe)"
  delta_z=$(( nach_z - vor_z ))
  delta_r=$(( nach_r - vor_r ))
  chk "Uebergang nach $m: Zaehler-Delta ist ${erwartet[$i]}" "$delta_z" "${erwartet[$i]}"
  chk "Uebergang nach $m: Abspiel-Delta ist ${erwartet[$i]}" "$delta_r" "${erwartet[$i]}"
  i=$(( i + 1 ))
done
sleep 0.5
gezaehlt="$(toene)"
echo "  Tonzaehler nach der Plan-Folge: $gezaehlt (erwartet 2)"
chk "Tonzaehler ist GENAU 2" "$gezaehlt" 2

rufe="$(abspielrufe)"
echo "  tatsaechliche Abspielaufrufe: $rufe"
chk "es wurde wirklich ein Abspieler gestartet (Positivkontrolle)" \
  "$([[ "$rufe" -gt 0 ]] && echo ja || echo nein)" ja
chk "Abspielaufrufe und Zaehler stimmen ueberein" "$rufe" "$gezaehlt"

# --- B. Der Ton haengt am Mood, nicht am Sprite --------------------------------
# `needs_input` und `failed` sind derselbe Sprite `dringend`. Ein Ausloeser am
# Sprite wuerde beim Wechsel needs_input -> failed -> needs_input zweimal
# piepen; am Mood nur einmal.
vor="$(toene)"
ctl "$tmp/c.sock" 'mood failed' >/dev/null; sleep 0.5
zwischen="$(toene)"
chk "Wechsel needs_input -> failed macht keinen Ton" "$zwischen" "$vor"
sprite="$(diag "$tmp/d.sock" | jq -r '.sprite' 2>/dev/null)"
chk "der Sprite ist dabei trotzdem dringend (Positivkontrolle)" "$sprite" dringend

# --- C. Kein Prozessmuell -----------------------------------------------------
zombies="$(ps -o stat= --ppid "$pid" 2>/dev/null | grep -c Z || true)"
[[ "$zombies" =~ ^[0-9]+$ ]] || zombies=0
echo "  Zombie-Kinder des Pruefling-Prozesses: $zombies"
chk "keine Zombie-Prozesse" "$zombies" 0

stderr_zeilen="$(grep -c . "$tmp/err.log" 2>/dev/null)"
[[ "$stderr_zeilen" =~ ^[0-9]+$ ]] || stderr_zeilen=0
echo "  stderr-Zeilen: $stderr_zeilen"
chk "kein Log-Spam (hoechstens 5 Zeilen)" \
  "$([[ "$stderr_zeilen" -le 5 ]] && echo ja || echo nein)" ja

beenden

# --- D. Abschaltbar ------------------------------------------------------------
: > "$tmp/abspiel.log"
TON_UMGEBUNG=""
starten "--ton aus"
chk "Overlay startet mit --ton aus" "$?" 0
for m in needs_input done needs_input; do
  ctl "$tmp/c.sock" "mood $m" >/dev/null; sleep 0.4
done
sleep 0.8
chk "mit --ton aus bleibt der Zaehler bei 0" "$(toene)" 0
chk "mit --ton aus wird auch kein Abspieler gestartet" "$(abspielrufe)" 0
# Positivkontrolle: der Prozess reagiert ueberhaupt -- sonst waere die 0 oben
# auch mit einem haengenden Overlay gruen.
chk "Moodwechsel kommen trotzdem an (Positivkontrolle)" \
  "$(diag "$tmp/d.sock" | jq -r '.mood' 2>/dev/null)" needs_input
beenden

: > "$tmp/abspiel.log"
TON_UMGEBUNG=0
starten ""
chk "Overlay startet mit DAIMON_FACE_TON=0" "$?" 0
ctl "$tmp/c.sock" 'mood needs_input' >/dev/null; sleep 0.8
chk "DAIMON_FACE_TON=0 schaltet den Ton ab" "$(toene)" 0
chk "DAIMON_FACE_TON=0 startet auch keinen Abspieler" "$(abspielrufe)" 0
beenden

# Und die Kommandozeile gewinnt ueber die Umgebung.
: > "$tmp/abspiel.log"
TON_UMGEBUNG=0
starten "--ton ein"
chk "Overlay startet mit --ton ein trotz DAIMON_FACE_TON=0" "$?" 0
ctl "$tmp/c.sock" 'mood needs_input' >/dev/null; sleep 0.8
chk "die Kommandozeile gewinnt ueber die Umgebung" "$(toene)" 1
chk "und es wurde wirklich abgespielt" "$(abspielrufe)" 1

# Ein ungueltiger Umgebungswert ist ein Fehler, kein stilles Standardverhalten.
beenden
DAIMON_MAX_SECS=10 DAIMON_FACE_TON=vielleicht "$BIN" \
  --diag-socket "$tmp/x.sock" >/dev/null 2>&1
chk "ungueltiges DAIMON_FACE_TON endet mit Exit 2" "$?" 2
beenden

# --- E. Rueckfall auf den zweiten Abspieler -----------------------------------
# Bisher lieferten beide Attrappen sofort Erfolg -- der Rueckfallpfad war
# damit ungeprueft, und "es gibt einen Rueckfall" war eine Behauptung.
# Jetzt scheitert der erste mit Exitcode 1.
beenden
cat > "$tmp/bin/canberra-gtk-play" <<ATTRAPPE
#!/usr/bin/env bash
echo "canberra-gtk-play \$*" >> "$tmp/abspiel.log"
exit 1
ATTRAPPE
chmod +x "$tmp/bin/canberra-gtk-play"
: > "$tmp/abspiel.log"
TON_UMGEBUNG=""
starten ""
chk "Overlay startet fuer den Rueckfalltest" "$?" 0
ctl "$tmp/c.sock" 'mood needs_input' >/dev/null
sleep 2
echo "  Abspielaufrufe im Rueckfall: $(abspielrufe), Zaehler: $(toene)"
chk "beide Kandidaten wurden versucht" "$(abspielrufe)" 2
# Ein Tonvorgang, zwei Prozesse: der Zaehler zaehlt den VORGANG. Stuende hier
# 2, waere aus einem Fehlschlag ein zweiter Ton geworden.
chk "der Zaehler zaehlt den Vorgang, nicht die Prozesse" "$(toene)" 1
beenden

exit $fail
