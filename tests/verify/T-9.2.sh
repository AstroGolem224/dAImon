#!/usr/bin/env bash
# Verifizierer fuer T-9.2: das animierte Pet -- und die Zusage daneben.
#
# Zwei Aussagen, und die zweite ist die teurere:
#
#   1. In einem animierten Mood laeuft der Bildtakt von selbst: `takt_schlaege`
#      und `frames_rendered` steigen ohne jedes Ereignis von aussen -- und zwar
#      im ATEMTAKT, nicht in der Emote-Bildrate.
#   2. In `sleeping` steht der Takt still, und nach der Rueckkehr aus einem
#      animierten Mood steht er WIEDER still. Bliebe er haengen, waere
#      Messung 1 beim naechsten Start trotzdem gruen und die Idle-CPU-Zusage
#      aus T-1.5 still gefallen.
#
# ----------------------------------------------------------------------------
# Was sich am 02.09. geaendert hat, und warum dieser Lauf dadurch STRENGER wird
# ----------------------------------------------------------------------------
# Bis dahin stand `idle` mit in `RUHIGE_MOODS` und wurde auf ein Bild gezwungen.
# Der Ruhezustand regte sich gar nicht, und dieser Verifizierer mass genau das:
# "in idle schlaegt die Uhr kein einziges Mal".
#
# Der Nutzer hat das beanstandet -- ein Pet, das voellig stillsteht, wirkt tot.
# Seitdem gilt: `RUHIGE_MOODS` traegt nur noch `sleeping` (dort blendet
# `Sichtbarkeit::fuer_mood` das Pet ohnehin aus, ein Takt fuer unsichtbare
# Pixel waere reine Rechenzeit), und `idle` ATMET -- mit `ATEM_FPS` = 3 statt
# mit `ANIMATION_FPS` = 12, also mit einem Viertel der Bilder. Die
# Idle-CPU-Zusage aus T-1.5 gilt unveraendert; sie wird jetzt ueber das TEMPO
# gehalten und nicht mehr ueber den Stillstand.
#
# Wer daraus nur "erwartet 0" durch "erwartet 9" ersetzt, hat eine Attrappe mit
# neuem Hash gebaut. Die neue Zusage ist enger als die alte, also stehen unten
# vier Kriterien, die es vorher nicht gab:
#
#  * der Atem hat eine OBERE Schranke bei `ATEM_FPS` -- ein Mutant, der
#    `ATEM_FPS` auf `ANIMATION_FPS` hochzieht, wird rot;
#  * und zusaetzlich ein eigenes Kriterium "deutlich langsamer als
#    ANIMATION_FPS" (hoechstens die halbe Emote-Bildrate), das dieselbe
#    Verwechslung noch einmal von der anderen Seite faengt;
#  * `sleeping` steht auf DERSELBEN Zeile wie das atmende `idle`. Damit ist
#    belegt, dass die Stille dort aus `RUHIGE_MOODS` kommt und nicht daher,
#    dass das Sheet fuer diese Zeile nur ein Bild anbietet;
#  * `sleeping` ist das Grundrauschen fuer beide `/proc`-Messungen -- der
#    fruehere Bezugspunkt `idle` weckt die Schleife jetzt selbst dreimal je
#    Sekunde und taugt dafuer nicht mehr.
#
# ----------------------------------------------------------------------------
# Das Messgeraet: `takt_schlaege` (Lauf 2), nicht `frames_rendered`
# ----------------------------------------------------------------------------
# `frames_rendered` kann Aussage 2 gar nicht sehen. Der Zaehler zaehlt
# committete Puffer. Ein Timer, der dreimal je Sekunde feuert, von
# `Animator::tick` ein `false` bekommt und nichts zeichnet, bewegt ihn kein
# Stueck -- genau der Mutant, der in `App::takt_schlagen` das
# `TimeoutAction::Drop` durch `ToDuration` ersetzt. Ein CPU-Fenster nach dem
# Muster von T-1.5 sieht ihn ebenfalls kaum: ein paar leere Weckvorgaenge je
# Sekunde kosten Bruchteile eines Prozents.
#
# Seit T-9.2 traegt `FaceState` deshalb `takt_schlaege`: +1 in
# `App::takt_schlagen()`, VOR jeder Weiche -- auch fuer einen Schlag, der
# nichts zeichnet. Damit ist genau der Fehler direkt beobachtbar, und zwar mit
# einer Zahl aus dem eigenen Haus, die keine Systemlast verwaesert.
#
# Referenzmessung (Rolle reviewer, 02.09., je 4 s am laufenden Face):
#
#     sleeping      Schlaege +0    Bilder +0    Weckvorgaenge +0
#     idle          Schlaege +12   Bilder +10   Weckvorgaenge +21
#     working       Schlaege +12   Bilder +10   Weckvorgaenge +22
#     sleeping      Schlaege +0    Bilder +0    Weckvorgaenge +0
#     needs_input   Schlaege +12   Bilder +12   Weckvorgaenge +24
#
# Daraus folgt die Form der Kriterien unten:
#
#  * `Schlaege == Bilder` ist KEIN Kriterium. Ein Schlag, der die Spalte nicht
#    weiterschaltet, aendert keine Pixel. Die beiden Zaehler messen
#    Verschiedenes, und dass sie oft gleichauf liegen, ist kein Vertrag.
#  * Die Ruhe-Bloecke vertragen KEINE Toleranz: exakt 0, nicht "wenig".
#  * Bilder und Schlaege bekommen verschiedene Schranken. Schlaege sind der
#    Takt selbst und liegen dicht an `ATEM_FPS * Sekunden`; Bilder duerfen
#    darunter liegen, weil nicht jeder Schlag zeichnet.
#
# ----------------------------------------------------------------------------
# Warum der `/proc`-Weg als ZWEITES Messgeraet bleibt
# ----------------------------------------------------------------------------
# Er ist nicht das primaere Instrument -- aber er wird auch nicht geworfen, aus
# drei Gruenden, von denen der letzte der eigentliche ist:
#
#  1. `takt_schlaege` sieht ausschliesslich die Animationsuhr. Ein Weckvorgang
#     aus einer ANDEREN Quelle -- ein Farbuebergangs-Timer, der sich nicht
#     aushaengt, eine Frame-Callback-Schleife, die sich ohne Commit neu
#     armiert -- bewegt weder ihn noch `frames_rendered`, bricht die
#     Idle-CPU-Zusage aus T-1.5 aber genauso. `voluntary_ctxt_switches` sieht
#     jeden davon: die Hauptschleife blockiert in `poll()`, jedes Aufwachen ist
#     genau ein freiwilliger Kontextwechsel.
#  2. Er kostet nichts. Dieselbe Stichprobe, derselbe Lesevorgang; `/proc`
#     wird ohnehin fuer die CPU-Ticks geoeffnet.
#  3. Er ist ein FREMDER Zeuge. `takt_schlaege` kommt aus demselben Prozess,
#     dessen Zusage geprueft wird: ein Zaehler, der bei 0 steht, weil der
#     Aufruf `takt_schlag_zaehlen()` entfernt wurde, liest sich exakt wie eine
#     stehende Uhr. Block B faengt das (der Zaehler MUSS in `idle` wachsen);
#     dass in Block A und D zusaetzlich der Kernel schweigt, ist die Kontrolle
#     dazu, die nicht im Pruefling wohnt.
#
# Die Rollenteilung ist damit: die harten Nullen haengen an `takt_schlaege`,
# die zweite, gruendere Aussage ("die Schleife schlaeft ueberhaupt") an
# `/proc`. Beide Instrumente haben ihre eigene Positivkontrolle in Block B.
#
# Ein Nachlauf von `tests/verify/T-1.5.sh` steht hier bewusst NICHT, obwohl
# T-2.1, T-2.2 und T-2.4 einen haben. T-1.5 startet ein frisches Face und
# laesst es in Ruhe -- ein Prozess, der nie animiert hat. Genau den Zustand,
# den T-9.2 neu erzeugt, kann dieser Nachlauf nicht erreichen. Er kostete
# anderthalb Minuten und belegte fuer diese Zusage nichts.
#
# ----------------------------------------------------------------------------
# Ein fehlendes Feld ist ROT, keine 0
# ----------------------------------------------------------------------------
# Beim ersten Messversuch war das Binary nicht neu gebaut, `takt_schlaege`
# fehlte im JSON, die Stichproben blieben leer -- und `$(( ))` machte daraus
# still `+0`, also die Meldung "alles ruhig", waehrend gar nichts gemessen war.
# Dagegen stehen hier drei Sperren:
#
#  * ein eigenes Kriterium direkt nach dem Start ("die Diagnose liefert
#    takt_schlaege"), das den Lauf abbricht statt ihn gruen zu faerben;
#  * `probe` bricht mit sichtbarem Fehler ab, wenn ein Schluessel fehlt;
#  * jede Stichprobe wird EINZELN auf Ziffern geprueft, bevor sie in
#    `$(( ))` geht, und ein unlesbarer Block setzt -1 statt 0.
#
# ----------------------------------------------------------------------------
# Warum an EINER Zeile gemessen wird
# ----------------------------------------------------------------------------
# `idle`, `working` und `sleeping` bilden beim mitgelieferten
# `face/assets/pet.json` alle auf dieselbe Zeile ab -- dieselben sechs Bilder,
# dieselbe Zelle. Der einzige Unterschied ist der Mood. Damit misst dieser
# Verifizierer die Weiche (`RUHIGE_MOODS`) und nicht drei verschiedene Sheets.
# Das ist zugleich die Positivkontrolle zur Stille in `sleeping`: dieselbe
# Zeile atmet in Block B nachweislich.
#
# Das ist eine Annahme ueber das Manifest, und sie wird geprueft statt
# geglaubt: das Manifest darf keinen `moods`-Block haben (sonst gingen die
# Moods auf verschiedene Zeilen), und der `sprite`-Bezeichner in der Diagnose
# muss in allen drei Bloecken derselbe sein. Bekommt `pet.json` eines Tages
# einen `moods`-Block, wird dieses Kriterium rot -- zu Recht: dann misst der
# Lauf etwas anderes als hier beschrieben.
#
# Derselbe fehlende `moods`-Block ist auch der Grund, warum hier ueberall der
# ATEM gemessen wird und nie ein Emote: ohne ihn hat kein Mood eine
# Emote-Zeile, `Animator::emote` ist `None`, und es gilt durchgehend
# `ATEM_FPS`. Das Emote und sein eigener, schnellerer Takt haben ihren eigenen
# Prueflauf -- `tests/verify/T-9.3.sh`, mit einem Pruefstand-Pet, das
# Emote-Zeilen hat.
#
# Zusaetzlich wird eine ZWEITE animierte Zeile gefahren (`needs_input` ->
# `dringend` -> `waiting`, Zeile 6). Der Entwurf mass nur `working`; ein
# Prueffeld, das nur Zeile 0 kennt, bestuende auch ein `frame(zeile, spalte)`,
# das die Spalte nur fuer Zeile 0 beachtet.
#
# ----------------------------------------------------------------------------
# Woher die Schwellen kommen
# ----------------------------------------------------------------------------
# Keine geratene Zahl. `ATEM_FPS` und `ANIM_FPS` unten sind die beiden
# Groessen, die dieser Verifizierer festhaelt -- `ATEM_FPS` steht in
# `face/src/render.rs`, `ANIMATION_FPS` in `face/src/main.rs`. Beide sind damit
# Zusagen mit Datum: wird eine geaendert, wird dieser Lauf rot und will gelesen
# werden.
#
#  * Schlaege im Lauf: 0,75 bis 1,25 mal `ATEM_FPS * gemessene Sekunden`.
#    Die Untergrenze faengt einen stehenden, die Obergrenze einen
#    durchdrehenden Takt.
#  * Schlaege im Lauf, zweite Schranke: hoechstens die HALBE Emote-Bildrate.
#    Redundant zur Obergrenze darueber und trotzdem eigenstaendig -- sie
#    benennt genau den Mutanten, um den es geht (Atem und Emote im selben
#    Takt), statt ihn nur nebenbei mitzufangen.
#  * Bilder im Lauf: die HAELFTE bis das Anderthalbfache derselben Groesse.
#    Weiter, weil `frames_rendered` committete Puffer zaehlt und nicht
#    Timerschlaege.
#  * Schlaege in Ruhe: exakt 0. Keine Toleranz -- die Uhr haengt sich aus oder
#    sie tut es nicht.
#  * Weckvorgaenge in Ruhe: das Grundrauschen wird GEMESSEN (Block A, derselbe
#    Prozess, dasselbe Fenster, in `sleeping`). Die Rueckkehr in Ruhe muss
#    naeher am gemessenen Rauschen liegen als am gemessenen Takt: der
#    Ueberschuss ueber A darf hoechstens ein Viertel des Ueberschusses aus B
#    sein. Keine absolute Zahl, und die Positivkontrolle steckt darin: sieht
#    das Instrument den laufenden Atem nicht (Block B), faellt sein eigenes
#    Kriterium und nicht still die Aussage darunter.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"
PY="/usr/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

# Takt des Ruhe-Atems, wie ihn `face/src/render.rs` als ATEM_FPS setzt. Das ist
# die Rate, in der hier ALLES laeuft: `face/assets/pet.json` hat keinen
# `moods`-Block, also kein Emote.
ATEM_FPS=3
# Bildrate des Emotes, wie sie `face/src/main.rs` als ANIMATION_FPS setzt. Hier
# wird sie nur als Gegenprobe gebraucht: der Atem muss deutlich darunter
# liegen.
ANIM_FPS=12
# Fenster je Block und Beruhigung davor. 2 s reichen fuer den Farbuebergang
# (320 ms) und fuer das einmalige Zeichnen nach einem Mood-Wechsel. 4 s
# Fenster, nicht 3: bei 3 fps sind das zwoelf Schlaege statt neun, und ein
# einzelner verspaeteter Weckruf verschiebt den Quotienten weniger.
FENSTER=4
BERUHIGUNG=2

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
num_ge() { if [[ "$1" =~ ^-?[0-9]+$ && "$2" =~ ^-?[0-9]+$ ]] && (( $1 >= $2 )); then echo ja; else echo nein; fi; }
# Gleitkomma-Vergleiche: ungueltige Eingaben werden zu "nein" statt zu einem
# stillen awk-Nullwert, der jede Untergrenze bestuende.
zahl() { [[ "$1" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; }
f_ge() { if zahl "$1" && zahl "$2"; then awk -v a="$1" -v b="$2" 'BEGIN{print (a+0 >= b+0) ? "ja" : "nein"}'; else echo nein; fi; }
f_le() { if zahl "$1" && zahl "$2"; then awk -v a="$1" -v b="$2" 'BEGIN{print (a+0 <= b+0) ? "ja" : "nein"}'; else echo nein; fi; }
mal() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.2f", a*b}'; }

echo "T-9.2 — Animiertes Pet aus Videoloops"
chk "face-Crate vorhanden" "$([[ -f "$TARGET/face/Cargo.toml" ]] && echo ja || echo nein)" ja
[[ -f "$TARGET/face/Cargo.toml" ]] || exit 1
chk "python3 vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja

# --- Bauen --------------------------------------------------------------------
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

ct_out="$(cd "$TARGET/face" && ${BAUDIR:+env CARGO_TARGET_DIR="$BAUDIR"} timeout 900 cargo test -p face 2>&1)"
ct_rc=$?
read -r ct_passed ct_failed <<<"$(awk '/^test result:/ {p+=$4; f+=$6} END {print p+0, f+0}' <<<"$ct_out")"
echo "  cargo test: $ct_passed passed, $ct_failed failed"
chk "cargo test laeuft durch" "$ct_rc" 0
chk "keine fehlgeschlagenen Rust-Tests" "$ct_failed" 0
chk "es liefen ueberhaupt Tests (Positivkontrolle)" "$(num_ge "$ct_passed" 1)" ja

# --- Die Messanordnung selbst -------------------------------------------------
# Ohne diese drei Kriterien waere "sleeping steht still" auch dann gruen, wenn
# das Sheet fuer die gemessene Zeile ueberhaupt nur ein Bild anboete -- und "es
# ist dieselbe Zeile" waere eine Behauptung im Kopfkommentar.
lese_manifest="$("$PY" - "$MANIFEST" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
zustaende = m.get("states", {})
print("idle_bilder=%d" % zustaende.get("idle", {}).get("frames", 0))
print("waiting_bilder=%d" % zustaende.get("waiting", {}).get("frames", 0))
print("moods_block=%s" % ("ja" if m.get("moods") else "nein"))
PYEOF
)"
mw() { grep -m1 "^$1=" <<<"$lese_manifest" | cut -d= -f2; }
chk "die gemessene idle-Zeile hat mehrere Bilder (Positivkontrolle)" \
  "$(num_ge "$(mw idle_bilder)" 2)" ja
chk "die zweite gemessene Zeile (waiting) hat mehrere Bilder (Positivkontrolle)" \
  "$(num_ge "$(mw waiting_bilder)" 2)" ja
# Zugleich die Begruendung fuer ATEM_FPS als Sollrate: ohne moods-Block gibt es
# keine Emote-Zeile, also laeuft hier ueberall der Atem.
chk "das Manifest hat keinen moods-Block (alle Moods teilen die Zeile, keiner hat ein Emote)" \
  "$(mw moods_block)" nein

tmp="$(mktemp -d)"
face=""
trap '[[ -n "$face" ]] && kill "$face" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
if [[ -z "${WAYLAND_DISPLAY:-}" || ! -x "$BIN" ]]; then
  echo "  FAIL Live-Pruefungen uebersprungen"
  exit 1
fi

# Watchdog: fuenf Bloecke a (Beruhigung + Fenster), Start und Reserve.
max_secs=$(( 5 * (BERUHIGUNG + FENSTER) + 60 ))
DAIMON_MAX_SECS=$max_secs "$BIN" --pet-manifest "$MANIFEST" \
  --sprite-position 900,500 \
  --control-socket "$tmp/c.sock" --diag-socket "$tmp/d.sock" \
  >"$tmp/out.log" 2>"$tmp/err.log" &
face=$!
for _ in $(seq 1 200); do [[ -S "$tmp/d.sock" && -S "$tmp/c.sock" ]] && break; sleep 0.1; done
chk "Overlay startet" "$([[ -S "$tmp/d.sock" ]] && echo ja || echo nein)" ja
chk "Steuer-Socket vorhanden" "$([[ -S "$tmp/c.sock" ]] && echo ja || echo nein)" ja
[[ -S "$tmp/d.sock" && -S "$tmp/c.sock" ]] || { echo "  FAIL ohne Sockets keine Messung"; exit 1; }

# Eine Zeile an den Steuer-Socket, Antwortzeile zurueck.
steuern() { "$PY" - "$tmp/c.sock" "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
c.sendall((sys.argv[2] + "\n").encode())
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

# --- Das Messgeraet muss im Binary sein ---------------------------------------
# Vor jeder Messung, und mit Abbruch: fehlt `takt_schlaege` im JSON (ein nicht
# neu gebautes Binary), meldeten alle Ruhe-Bloecke unten "+0" -- also "alles
# ruhig", waehrend gar nichts gemessen waere. Genau dieser Fall ist beim
# ersten Messversuch aufgetreten.
diag_roh="$("$PY" - "$tmp/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
sys.stdout.write(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
)"
hat_feld() { "$PY" -c 'import json,sys; d=json.loads(sys.argv[1] or "{}"); print("ja" if sys.argv[2] in d else "nein")' "$diag_roh" "$1" 2>/dev/null || echo nein; }
chk "die Diagnose liefert takt_schlaege (sonst misst nichts einen Takt)" \
  "$(hat_feld takt_schlaege)" ja
chk "die Diagnose liefert frames_rendered" "$(hat_feld frames_rendered)" ja
if [[ "$(hat_feld takt_schlaege)" != ja ]]; then
  echo "  FAIL ohne takt_schlaege waeren alle Ruhe-Bloecke falsch gruen -- Binary neu bauen"
  exit 1
fi

# Eine Stichprobe: Uhr, Taktschlaege, Bilder, Weckvorgaenge und CPU-Ticks der
# HAUPTSCHLEIFE. /proc/<pid>/status gehoert dem Haupt-Thread -- genau dem, der
# in poll() haengt. Die Threads von Diagnose- und Steuer-Socket zaehlen nicht
# mit, und das ist der Grund, warum das Ablesen selbst die Messung nicht
# verfaelscht.
#
# Fehlt ein Schluessel im JSON, bricht dieser Aufruf mit Fehler ab und liefert
# KEINE Zeile -- der Aufrufer macht daraus ein rotes Kriterium, keine 0.
probe() { "$PY" - "$tmp/d.sock" "$face" <<'PYEOF'
import json, socket, sys, time
sock, pid = sys.argv[1], int(sys.argv[2])
with open(f"/proc/{pid}/status", encoding="ascii") as fh:
    wach = next(int(z.split()[1]) for z in fh
                if z.startswith("voluntary_ctxt_switches:"))
with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
    daten = fh.read()
# comm (Feld 2) darf Leerzeichen und Klammern enthalten: erst nach dem
# letzten ')' teilen, dann ist rest[0] Feld 3.
rest = daten[daten.rindex(")") + 2:].split()
ticks = int(rest[11]) + int(rest[12])   # utime + stime
jetzt = time.monotonic()
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sock)
d = json.loads(c.makefile("rb").readline().decode())
c.close()
for feld in ("takt_schlaege", "frames_rendered", "mood", "sprite"):
    if feld not in d:
        sys.exit(f"Diagnose ohne {feld}: {d}")
print(f"{jetzt:.3f} {d['takt_schlaege']} {d['frames_rendered']} "
      f"{wach} {ticks} {d['mood']} {d['sprite']}")
PYEOF
}

# Mood setzen, beruhigen lassen, ein Fenster messen. Setzt d_schlaege,
# d_frames, d_wach, d_ticks, sek, mood_ist, sprite_ist.
messen() {
  local mood="$1" antwort t0 g0 f0 w0 k0 m0 s0 t1 g1 f1 w1 k1 m1 s1
  antwort="$(steuern "mood $mood")"
  chk "Steuerbefehl 'mood $mood' wird mit ok bestaetigt" "$antwort" ok
  sleep "$BERUHIGUNG"
  read -r t0 g0 f0 w0 k0 m0 s0 <<<"$(probe)"
  sleep "$FENSTER"
  read -r t1 g1 f1 w1 k1 m1 s1 <<<"$(probe)"
  # Jeden Wert einzeln pruefen, nicht die Verkettung: ein einzelner leerer
  # Wert wuerde in $(( )) still zu 0 und koennte ein "steht still" oder ein
  # "weckt sich nicht" gruen faerben, das nie gemessen wurde.
  local wert
  for wert in "$g0" "$g1" "$f0" "$f1" "$w0" "$w1" "$k0" "$k1"; do
    if ! [[ "$wert" =~ ^[0-9]+$ ]]; then
      echo "  FAIL Stichprobe unlesbar ($t0 $g0 $f0 $w0 $k0 / $t1 $g1 $f1 $w1 $k1)"
      fail=1
      # -1 und nicht 0: ein unlesbarer Block darf kein "steht still" ergeben.
      d_schlaege=-1; d_frames=-1; d_wach=-1; d_ticks=-1
      sek=0; mood_ist=""; sprite_ist=""
      return
    fi
  done
  d_schlaege=$(( g1 - g0 )); d_frames=$(( f1 - f0 ))
  d_wach=$(( w1 - w0 )); d_ticks=$(( k1 - k0 ))
  sek="$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.3f", b-a}')"
  mood_ist="$m1"; sprite_ist="$s1"
  echo "  $mood ueber ${sek}s: Schlaege +$d_schlaege, Bilder +$d_frames, Weckvorgaenge +$d_wach, Jiffies +$d_ticks, sprite=$sprite_ist"
}

# Die vier Schranken eines atmenden Blocks, immer aus der GEMESSENEN Dauer.
# Eine Funktion und keine viermal abgeschriebene Zeile: vier Fassungen
# derselben Rechnung waeren eine Rechnung und drei Attrappen.
atem_pruefen() {
  local name="$1" schlaege="$2" frames="$3" dauer="$4"
  local soll g_unten g_oben unten oben emote_halb
  soll="$(mal "$ATEM_FPS" "$dauer")"
  g_unten="$(mal "$soll" 0.75)"; g_oben="$(mal "$soll" 1.25)"
  unten="$(mal "$soll" 0.5)";    oben="$(mal "$soll" 1.5)"
  emote_halb="$(mal "$(mal "$ANIM_FPS" "$dauer")" 0.5)"
  echo "  Erwartung fuer ${dauer}s bei ${ATEM_FPS} fps: $soll (Schlaege $g_unten .. $g_oben, Bilder $unten .. $oben)"
  chk "$name: der Atem laeuft (Schlaege nahe ATEM_FPS)" "$(f_ge "$schlaege" "$g_unten")" ja
  chk "$name: der Atem dreht nicht durch (hoechstens ein Viertel ueber ATEM_FPS)" \
    "$(f_le "$schlaege" "$g_oben")" ja
  # Der Mutant, um den es geht: Atem und Emote im selben Takt. Die Obergrenze
  # darueber faengt ihn mit, dieses Kriterium BENENNT ihn.
  chk "$name: der Atem ist deutlich langsamer als ANIMATION_FPS (hoechstens die halbe Emote-Bildrate)" \
    "$(f_le "$schlaege" "$emote_halb")" ja
  chk "$name: es entstehen auch Bilder (mindestens die halbe Atemrate)" \
    "$(f_ge "$frames" "$unten")" ja
  chk "$name: es wird nicht frei neugezeichnet (hoechstens das Anderthalbfache)" \
    "$(f_le "$frames" "$oben")" ja
}

# Positivkontrolle des Steuerkanals: ein "ok" bedeutet nur dann etwas, wenn
# Unsinn mit "err" abgewiesen wird. Ohne sie waere "der Zaehler steht still"
# auch dann gruen, wenn jeder Mood-Befehl im Nichts landet.
chk "Steuer-Socket weist Unsinn mit err ab" "$(steuern 'mood gibt-es-nicht')" err

# --- A. sleeping: das Grundrauschen -------------------------------------------
# Der einzige Mood in `RUHIGE_MOODS`, und damit der einzige Bezugspunkt, an dem
# `/proc` noch etwas ueber Ruhe sagen kann. Bis zum 02.09. stand hier `idle`;
# das ginge heute nicht mehr, weil `idle` sich selbst dreimal je Sekunde weckt.
messen sleeping
a_schlaege=$d_schlaege; a_frames=$d_frames; a_wach=$d_wach; a_ticks=$d_ticks
a_sprite="$sprite_ist"
chk "in sleeping schlaegt die Uhr kein einziges Mal" "$a_schlaege" 0
chk "in sleeping steht der Bildzaehler still" "$a_frames" 0

# --- B. idle: der Ruhezustand ATMET -------------------------------------------
# Die am 02.09. geaenderte Zusage, und zugleich die Positivkontrolle beider
# Messgeraete: sieht hier keins den Takt, faellt sein eigenes Kriterium und
# nicht still die Null in Block A und D.
messen idle
b_schlaege=$d_schlaege; b_frames=$d_frames; b_wach=$d_wach; b_ticks=$d_ticks
chk "der Mood ist wirklich idle" "$mood_ist" idle
# Damit ist belegt, dass die Stille in Block A und D aus `RUHIGE_MOODS` kommt
# und nicht daher, dass das Sheet fuer diese Zeile nur ein Bild anbietet:
# dieselbe Zeile atmet hier.
chk "idle steht auf derselben Zeile wie sleeping (die Stille dort kommt aus RUHIGE_MOODS)" \
  "$sprite_ist" "$a_sprite"
atem_pruefen "in idle" "$b_schlaege" "$b_frames" "$sek"
# Positivkontrolle des ZWEITEN Messgeraets: sieht `/proc` den laufenden Atem
# nicht, sagt "zurueck auf Grundrauschen" in Block D gar nichts.
wach_takt=$(( b_wach - a_wach ))
ticks_takt=$(( b_ticks - a_ticks ))
echo "  Weckvorgaenge ueber dem Rauschen: +$wach_takt, Jiffies ueber dem Rauschen: +$ticks_takt"
chk "die Weckvorgaenge sehen den Atem (Positivkontrolle des zweiten Messgeraets)" \
  "$(f_ge "$wach_takt" "$(mal "$(mal "$ATEM_FPS" "$sek")" 0.5)")" ja

# --- C. working: derselbe Atem, anderer Mood ----------------------------------
# Ohne `moods`-Block im Manifest hat `working` keine Emote-Zeile; es atmet also
# genau wie `idle`. Gemessen wird hier, dass der Moodwechsel den Takt weder
# anhaelt noch beschleunigt.
messen working
chk "der Mood ist wirklich working" "$mood_ist" working
chk "es ist dieselbe Zeile wie in idle (sprite unveraendert)" "$sprite_ist" "$a_sprite"
atem_pruefen "in working" "$d_schlaege" "$d_frames" "$sek"

# --- D. Und wieder still ------------------------------------------------------
# Der wichtigste Block: der Takt haengt sich von selbst wieder aus. Bliebe er
# stehen, waere A beim naechsten Lauf gruen und die Idle-CPU trotzdem hin.
messen sleeping
c_schlaege=$d_schlaege; c_frames=$d_frames; c_wach=$d_wach; c_ticks=$d_ticks
# Exakt 0 ueber das ganze Fenster, ohne Toleranz. Das ist die Zusage, und sie
# war vor `takt_schlaege` nur ueber den Umweg ueber `/proc` zu sehen.
chk "nach der Rueckkehr schlaegt die Uhr kein einziges Mal mehr" "$c_schlaege" 0
chk "nach der Rueckkehr steht der Bildzaehler wieder still" "$c_frames" 0
# Zweites Messgeraet, andere Frage: schlaeft die Schleife auch dann, wenn eine
# ANDERE Quelle als die Animationsuhr sie weckt? Naeher am gemessenen Rauschen
# als am gemessenen Takt: hoechstens ein Viertel des Ueberschusses aus Block B.
chk "nach der Rueckkehr weckt sich die Schleife ueberhaupt nicht mehr" \
  "$(num_ge $(( wach_takt / 4 )) $(( c_wach - a_wach )) )" ja
chk "nach der Rueckkehr faellt auch die CPU zurueck" \
  "$(num_ge $(( ticks_takt / 4 + 1 )) $(( c_ticks - a_ticks )) )" ja

# --- E. Eine zweite animierte Zeile -------------------------------------------
# needs_input geht ueber die Pose `dringend` auf die waiting-Zeile. Ein
# Prueffeld, das nur Zeile 0 kennt, bestuende auch eine Spaltenwahl, die nur
# fuer Zeile 0 greift.
messen needs_input
chk "needs_input steht auf der anderen Zeile (sprite=dringend)" "$sprite_ist" dringend
atem_pruefen "auf der zweiten Zeile" "$d_schlaege" "$d_frames" "$sek"

chk "das Face lebt nach allen Bloecken noch" \
  "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja

echo
if [[ "$fail" -eq 0 ]]; then echo "T-9.2: alle Kriterien gruen"; else echo "T-9.2: FEHLGESCHLAGEN"; fi
exit "$fail"
