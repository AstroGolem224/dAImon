#!/usr/bin/env bash
# Verifizierer fuer T-9.3: Atmen als Grundschleife, Emotes als Reaktion.
#
# Die Zusage in einem Satz: nach einem Moodwechsel spielt das Emote GENAU
# dreimal, mit Toenung und in der schnellen Bildrate, und faellt danach
# dauerhaft auf den langsamen, ungetoenten Atem desselben Moods zurueck;
# `idle` atmet ohne je ein Emote zu zeigen, `sleeping` steht ganz still.
#
# ----------------------------------------------------------------------------
# Was sich am 02.09. geaendert hat, und warum dieser Lauf dadurch STRENGER wird
# ----------------------------------------------------------------------------
# Der Nutzer hat drei Dinge beanstandet: das Pet zeigte im Ruhezustand eine
# Geste, es war dauerhaft gruen/rot/gelb ueberlagert, und der Atem lief im
# selben hektischen Takt wie die Geste. Daraus wurden vier Aenderungen:
#
#     EMOTE_UMLAEUFE   2 -> 3                           (render.rs)
#     RUHIGE_MOODS     [idle, sleeping] -> [sleeping]   (main.rs)
#     MOODS_OHNE_EMOTE neu: [idle, sleeping]            (main.rs)
#     ATEM_FPS         neu: 3, gegen ANIMATION_FPS = 12 (render.rs)
#     toenung          bool -> f32, und `toenungsanteil()` gibt sie
#                      NUR waehrend des Emotes frei     (render.rs/surface.rs)
#
# Die alte Fassung dieses Laufs hielt die alte Zusage fest und wurde an sieben
# Kriterien rot. Wer daraus nur "2" durch "3" ersetzt, hat eine Attrappe mit
# neuem Hash. Jede der Aenderungen ist deshalb unten eine EIGENE, von beiden
# Seiten eingeklemmte Zusage:
#
#  1. Das Emote endet nach dem DRITTEN Umlauf -- nicht nach dem zweiten und
#     nicht nach dem vierten. Bei 3,2 s laeuft es noch (schliesst zwei aus),
#     bei 4,4 s steht die Atemzeile (schliesst vier aus). Und die beiden
#     Zeitpunkte werden vorher gegen `UMLAEUFE` GERECHNET statt hier
#     hingeschrieben: wer die Zahl aendert und die Zeiten stehen laesst,
#     bekommt ein rotes Kriterium mit Ansage statt einer stillen Fehlmessung.
#  2. `idle` zeigt KEIN Emote -- und das ist eine eigene Zusage, kein Wegfall.
#     Das Pruefstand-Pet BIETET `idle` (und `sleeping`) ausdruecklich eine
#     Emote-Zeile an; gemessen wird, dass sie nie kommt, waehrend `idle`
#     zugleich nachweislich atmet. Ein Mutant, der `idle` aus
#     `MOODS_OHNE_EMOTE` nimmt, wird sofort rot. Vorher konnte dieser Lauf das
#     nicht sehen: sein `idle` war ein Standbild ohne Emote-Zeile, also
#     "nichts gefunden", wo nichts gemessen war.
#  3. Atem und Emote laufen mit VERSCHIEDENEM Takt. Beide Raten werden im
#     selben Block einzeln gemessen -- das Emote-Fenster gegen `ANIM_FPS`, das
#     Atem-Fenster gegen `ATEM_FPS` -- und zusaetzlich gegeneinander: das
#     Emote muss mindestens doppelt so schnell schlagen. Ein Mutant, der beide
#     gleichsetzt, faellt in allen drei Kriterien.
#  4. Die Toenung liegt NUR waehrend des Emotes an. Sie ist von aussen nicht
#     messbar (der Diagnose-Socket kennt keine Pixel), also wird sie von zwei
#     Seiten geklammert: der Einheitentest zu `render::toenungsanteil` muss im
#     `cargo test`-Lauf NAMENTLICH als `ok` erscheinen (nicht nur "keine roten
#     Tests" -- das waere wieder nicht von "nicht gemessen" zu unterscheiden),
#     und die NAHT bis zum einzigen Zeichenweg wird geprueft: genau ein
#     Aufrufer, und der uebergibt `animator.emote_laeuft()`. Der Mutant in der
#     Funktion faellt am Test, der Mutant an der Aufrufstelle
#     (`toenungsanteil(x, true)`) an der Naht.
#
# ----------------------------------------------------------------------------
# Warum `zeile` und `emote_laeuft` und nicht `sprite`
# ----------------------------------------------------------------------------
# Vor T-9.3 war die Zusage von aussen UNSICHTBAR. Ein Mood hat jetzt zwei
# Zeilen, und `mood` wie `sprite` sind waehrend beider dieselben -- ein Mutant,
# der `EMOTE_UMLAEUFE` auf `u32::MAX` setzt oder in `Animator::tick` den Block
# `if self.emote_umlaeufe > 0 { ... }` streicht, liesse jede Messung aus T-9.2
# gruen. Deshalb tragen `FaceState::zeile` und `FaceState::emote_laeuft` die
# Auskunft, und deshalb misst dieser Lauf an ihnen.
#
# Beides wird gebraucht, keins allein reicht:
#
#  * `zeile` allein: welche Zahl zu welcher Phase gehoert, steht im Manifest --
#    ein Verifizierer, der nur die Zahl liest, prueft das Manifest gegen sich
#    selbst.
#  * `emote_laeuft` allein: ein Feld, das der Animator meldet, ohne dass die
#    Zeile mitwandert, waere eine Selbstauskunft ohne Pixel dahinter.
#
# Darum wird jede Stichprobe an BEIDEN gemessen, plus an `takt_schlaege` und
# `frames_rendered`: waehrend des Emotes entstehen auch Bilder, und der Takt
# sagt, in welchem Tempo.
#
# ----------------------------------------------------------------------------
# Warum dieser Lauf sein Pet SELBST baut
# ----------------------------------------------------------------------------
# Die Emote-Zeilen liegen bis heute nur in `face/assets/held/pet.json`, und
# `face/assets/*/` steht in `.gitignore`. Ein Verifizierer, der `held`
# voraussetzt, waere in jedem frischen Checkout und unter `DAIMON_FIXTURE` rot,
# ohne dass am Pruefling etwas fehlte -- ein Falschbefund mit Ansage.
#
# Das VERSIONIERTE Pet (`face/assets/pet.json`, Ember) hat gar keinen
# `moods`-Block und kann darum ueberhaupt kein Emote zeigen. Es gibt also kein
# versioniertes Bildmaterial, an dem sich die Zusage messen liesse. (Genau
# darum misst `tests/verify/T-9.2.sh` an ihm nur den Atem und nie ein Emote --
# die beiden Laeufe teilen sich die Arbeit entlang dieser Grenze.)
#
# Geprueft wird aber ohnehin nicht das Bildmaterial, sondern die
# Zustandsmaschine im `Animator` und ihre Naht bis in den Diagnose-Socket. Also
# legt dieser Lauf ein eigenes Manifest in sein Temp-Verzeichnis, das auf das
# versionierte `spritesheet.png` zeigt und ein Raster darueberlegt, das in
# dessen echte Pixelmasse passt. Das Ergebnis sieht als Bild wie Bruchstuecke
# aus -- gemessen wird die ZEILE, nicht das Motiv.
#
# Dieses Pet ist absichtlich GROSSZUEGIGER als der Pruefling: es bietet auch
# `idle` und `sleeping` eine Emote-Zeile und mehrere Atemspalten an. Damit ist
# "der Ruhezustand zeigt kein Emote" und "sleeping steht still" eine Aussage
# ueber den CODE (`MOODS_OHNE_EMOTE`, `RUHIGE_MOODS`) und nicht ueber ein
# Manifest, das gar nichts anderes anbieten koennte.
#
# Zwei Dinge, die dieser Weg NICHT deckt, und dagegen steht je etwas:
#
#  * "das ausgelieferte Pet hat ueberhaupt Emote-Zeilen". Liegt `held` neben
#    dem Repo, wird es zusaetzlich strukturell geprueft; fehlt es, steht das
#    Kriterium ausdruecklich als UEBERSPRUNGEN da und nicht als gruen.
#  * "die Zeilennummer stammt aus dem Manifest". Genau darum werden alle
#    Erwartungswerte aus der geschriebenen Datei ZURUECKGELESEN statt hier
#    hingeschrieben -- und Atem- und Emote-Zeile werden gegeneinander geprueft.
#
# ----------------------------------------------------------------------------
# Woher die sechs Stichprobenzeiten kommen
# ----------------------------------------------------------------------------
# Ein Umlauf sind `EMOTE_SPALTEN / ANIM_FPS` = 16/12 = 1,333 s. Zwei Umlaeufe
# enden bei 2,667 s, drei bei 4,0 s, vier bei 5,333 s. Daraus die Punkte je
# animiertem Mood:
#
#     1,0 s   mitten im ERSTEN Umlauf    -> Emote laeuft, Emote-Zeile
#     2,0 s   mitten im ZWEITEN Umlauf   -> Emote laeuft NOCH  (schliesst 1 aus)
#     3,2 s   mitten im DRITTEN Umlauf   -> Emote laeuft NOCH  (schliesst 2 aus)
#     4,4 s   nach dem dritten, vor dem vierten -> Atem        (schliesst 4 aus)
#     6,0 s   lange danach               -> immer noch Atem    ("bleibt dabei")
#     9,0 s   noch laenger               -> Endpunkt des Atem-Taktfensters
#
# Die Klammer ist der eigentliche Punkt. "Nach 4,4 s steht die Atemzeile"
# allein belegt nur, dass das Emote irgendwann endet -- ein Emote mit EINEM
# Umlauf bestuende das genauso. Erst 3,2 s (laeuft noch) und 4,4 s (vorbei)
# zusammen sagen: es waren drei.
#
# Und diese Punkte sind nicht hingeschrieben, sondern werden unten gegen
# `UMLAEUFE` GEPRUEFT, mit 0,25 s Rand zu jeder Umlaufgrenze -- demselben Rand,
# den der Messkopf einer verspaeteten Stichprobe zugesteht.
#
# Eine VERSPAETETE Stichprobe misst einen anderen Zeitpunkt als den zugesagten
# und koennte aus "Emote endete zu frueh" ein gruenes "war schon vorbei"
# machen. Der Messkopf bricht darum ab, wenn ein Punkt mehr als 0,25 s zu spaet
# kommt -- lieber rot als falsch gemessen.
#
# Die beiden Taktfenster liegen zwischen diesen Punkten, jedes ganz innerhalb
# einer Phase:
#
#     1,0 s .. 3,2 s   nur Emote  -> muss bei ANIM_FPS liegen
#     4,4 s .. 9,0 s   nur Atem   -> muss bei ATEM_FPS liegen
#
# ----------------------------------------------------------------------------
# Ein fehlendes Feld ist ROT, keine 0 (uebernommen aus T-9.2)
# ----------------------------------------------------------------------------
# Dieselben drei Sperren, jetzt fuer `zeile` und `emote_laeuft`:
#
#  * ein eigenes Kriterium direkt nach dem Start ("die Diagnose liefert
#    zeile/emote_laeuft"), das den Lauf ABBRICHT statt ihn gruen zu faerben;
#  * der Messkopf bricht mit sichtbarem Fehler ab, wenn ein Schluessel fehlt --
#    und liefert dann KEINE Zeile;
#  * jede Stichprobe wird einzeln geprueft, bevor sie verglichen oder in
#    `$(( ))` gerechnet wird; eine unlesbare Reihe setzt -1 statt 0.
#
# ----------------------------------------------------------------------------
# Und eine vierte Sperre, die T-9.2 noch nicht hatte: das ALTER des Binarys
# ----------------------------------------------------------------------------
# `cargo test` baut `target/debug/daimon-face` NICHT neu. Am 02.09. ist die
# Rolle builder zweimal daran haengengeblieben: das Feld stand im JSON, der
# Zulauf fehlte im laufenden Prozess, und die Messung meldete Ruhe. Genau die
# Verwechslung, vor der CLAUDE.md warnt -- "nichts gefunden" war in Wahrheit
# "nicht gemessen".
#
# Deshalb unten ein Kriterium "keine Datei unter face/src ist juenger als das
# Binary", mit eigener Positivkontrolle (es MUSS Quelldateien geben, sonst
# misst `find` nur sein eigenes Schweigen).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
SHEET="$REPO/face/assets/spritesheet.png"
HELD="$REPO/face/assets/held/pet.json"
QUELLEN_DIR="$TARGET/face/src"
SURFACE="$QUELLEN_DIR/surface.rs"
PY="/usr/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

# Bildrate des EMOTES, wie sie `face/src/main.rs` als ANIMATION_FPS setzt.
# Aendert sie sich, wird dieser Lauf rot und will gelesen werden -- die sechs
# Stichprobenzeiten oben haengen daran.
ANIM_FPS=12
# Takt des ATEMS, wie ihn `face/src/render.rs` als ATEM_FPS setzt. Eine eigene
# Zahl und nicht ein Viertel von ANIM_FPS: zwei Fassungen derselben Groesse
# waeren eine Groesse und eine Attrappe.
ATEM_FPS=3
# Raster des Pruefstand-Pets. EMOTE_SPALTEN geht direkt in die Umlaufdauer ein.
RIG_COLS=16
RIG_ROWS=12
EMOTE_SPALTEN=$RIG_COLS
# Wie oft das Emote nach einem Moodwechsel spielt -- `EMOTE_UMLAEUFE` in
# `face/src/render.rs`.
UMLAEUFE=3
# Der Einheitentest, der die Toenung von innen festhaelt. Er muss NAMENTLICH
# im cargo-test-Lauf auftauchen; "keine roten Tests" allein waere nicht von
# "der Test wurde geloescht" zu unterscheiden.
TOENUNGSTEST=die_toenung_liegt_nur_waehrend_des_emotes

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
num_ge() { if [[ "$1" =~ ^-?[0-9]+$ && "$2" =~ ^-?[0-9]+$ ]] && (( $1 >= $2 )); then echo ja; else echo nein; fi; }
zahl() { [[ "$1" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; }
f_ge() { if zahl "$1" && zahl "$2"; then awk -v a="$1" -v b="$2" 'BEGIN{print (a+0 >= b+0) ? "ja" : "nein"}'; else echo nein; fi; }
f_le() { if zahl "$1" && zahl "$2"; then awk -v a="$1" -v b="$2" 'BEGIN{print (a+0 <= b+0) ? "ja" : "nein"}'; else echo nein; fi; }
rechne() { awk "BEGIN{printf \"%.3f\", $1}"; }

echo "T-9.3 — Atmen als Grundschleife, Emotes als Reaktion"
chk "face-Crate vorhanden" "$([[ -f "$TARGET/face/Cargo.toml" ]] && echo ja || echo nein)" ja
[[ -f "$TARGET/face/Cargo.toml" ]] || exit 1
chk "python3 vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
chk "versioniertes Sprite-Sheet vorhanden" "$([[ -f "$SHEET" ]] && echo ja || echo nein)" ja

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

# Das Binary muss die Quellen von HEUTE tragen. `cargo test` allein baut es
# nicht neu; ein altes Binary meldet in jedem Ruhe-Block brav "+0", waehrend
# gar nichts gemessen ist.
quellen="$(find "$QUELLEN_DIR" -type f -name '*.rs' 2>/dev/null | wc -l)"
chk "face/src traegt ueberhaupt Quelldateien (Positivkontrolle des Altersmasses)" \
  "$(num_ge "$quellen" 1)" ja
juenger=""
[[ -x "$BIN" ]] && juenger="$(find "$QUELLEN_DIR" -type f -newer "$BIN" -print -quit 2>/dev/null)"
chk "keine Quelle unter face/src ist juenger als das Binary" \
  "$([[ -z "$juenger" ]] && echo ja || echo nein)" ja
[[ -z "$juenger" ]] || echo "       juenger als das Binary: $juenger"

ct_out="$(cd "$TARGET/face" && ${BAUDIR:+env CARGO_TARGET_DIR="$BAUDIR"} timeout 900 cargo test -p face 2>&1)"
ct_rc=$?
read -r ct_passed ct_failed <<<"$(awk '/^test result:/ {p+=$4; f+=$6} END {print p+0, f+0}' <<<"$ct_out")"
echo "  cargo test: $ct_passed passed, $ct_failed failed"
chk "cargo test laeuft durch" "$ct_rc" 0
chk "keine fehlgeschlagenen Rust-Tests" "$ct_failed" 0
chk "es liefen ueberhaupt Tests (Positivkontrolle)" "$(num_ge "$ct_passed" 1)" ja

# --- Die Toenung: nur waehrend des Emotes -------------------------------------
# Zusage 4, und die einzige der vier, die der Diagnose-Socket nicht sehen kann
# -- er kennt keine Pixel. Also von zwei Seiten geklammert.
#
# Seite 1: der Einheitentest, NAMENTLICH. "Keine roten Tests" waere hier
# wertlos: ein geloeschter Test ist gruen. Gesucht wird die Zeile
# "test ...::<name> ... ok" in dem Lauf, den dieser Verifizierer selbst
# gefahren hat -- kein zweiter cargo-Aufruf, sonst pruefte er einen anderen
# Baum als den gemessenen.
toenung_zeile="$(grep -m1 -E "^test .*${TOENUNGSTEST} \.\.\. ok$" <<<"$ct_out")"
chk "der Einheitentest '$TOENUNGSTEST' ist gelaufen und gruen" \
  "$([[ -n "$toenung_zeile" ]] && echo ja || echo nein)" ja
[[ -n "$toenung_zeile" ]] && echo "       $toenung_zeile"

# Seite 2: die NAHT. Der Test oben haelt `render::toenungsanteil` fest -- er
# saehe aber nicht, wenn die Aufrufstelle sie umginge (`toenungsanteil(x,
# true)`) oder wenn ein zweiter Zeichenweg an ihr vorbei toente. Genau der
# Fehler, den CLAUDE.md als teuersten dieses Repos fuehrt: das Stueck ist
# gebaut und gruen, und im Betrieb ruft es niemand so auf.
#
# Positivkontrolle zuerst: ein `grep`, das ins Leere zeigt, meldet dieselbe 0
# wie ein `grep`, das nichts findet.
surface_zeilen="$([[ -f "$SURFACE" ]] && wc -l <"$SURFACE" || echo 0)"
chk "surface.rs ist lesbar (Positivkontrolle der Naht-Greps)" \
  "$(num_ge "$surface_zeilen" 100)" ja
naht="$(grep -c -E 'toenungsanteil\(.*emote_laeuft\(\)' "$SURFACE" 2>/dev/null)"
chk "genau ein Zeichenweg holt den Toenungsanteil aus animator.emote_laeuft()" \
  "$naht" 1
# Und keiner daneben: `frame_toenen_anteilig` ist die Stelle, an der wirklich
# gefaerbt wird. Ausserhalb von render.rs (wo sie definiert und getestet wird)
# darf es genau EINEN Aufrufer geben -- den mit dem Anteil von oben.
faerber="$(grep -rE 'frame_toenen_anteilig\(' --include='*.rs' "$QUELLEN_DIR" 2>/dev/null \
  | grep -vc 'render\.rs:')"
chk "genau ein Aufrufer faerbt ueberhaupt (kein zweiter Weg um das Gate)" \
  "$faerber" 1

tmp="$(mktemp -d)"
face=""
trap '[[ -n "$face" ]] && kill "$face" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT
RIG="$tmp/pet.json"

# --- Das Pruefstand-Pet -------------------------------------------------------
# Raster ueber das versionierte Sheet, so gewaehlt, dass jede benutzte Zelle in
# dessen echte Pixelmasse passt -- `zelle_ausschneiden` weist alles andere ab,
# und ein Renderfehler saehe aus wie eine stehende Zeile.
rig_bau="$("$PY" - "$SHEET" "$RIG" "$RIG_COLS" "$RIG_ROWS" <<'PYEOF' 2>&1
import json, struct, sys

sheet, ziel, cols, rows = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
kopf = open(sheet, "rb").read(24)
if kopf[:8] != b"\x89PNG\r\n\x1a\n":
    sys.exit(f"{sheet} ist kein PNG")
breite, hoehe = struct.unpack(">II", kopf[16:24])
cw, ch = breite // cols, hoehe // rows
if cw == 0 or ch == 0:
    sys.exit(f"Sheet {breite}x{hoehe} traegt kein {cols}x{rows}-Raster")
# Atemzeilen 0..3, Emote-Zeilen 8..11. Bewusst weit auseinander: eine
# Verwechslung von Atem und Emote soll als grosse Zahl auffallen, nicht als
# Abweichung um eins.
#
# `idle` und `sleeping` bekommen HIER ausdruecklich beides -- mehrere
# Atemspalten UND eine Emote-Zeile. Der Pruefling darf beides nicht nutzen:
# `MOODS_OHNE_EMOTE` unterdrueckt das Emote, `RUHIGE_MOODS` bei `sleeping`
# zusaetzlich den ganzen Takt. Waere das Manifest hier sparsam, maesse der
# Lauf sein eigenes Schweigen statt der Weiche im Code.
manifest = {
    "id": "pruefstand-T-9.3",
    "displayName": "Pruefstand T-9.3",
    "spritesheetPath": sheet,
    "atlas": {"cellW": cw, "cellH": ch, "cols": cols, "rows": rows},
    "moods": {
        "idle": {"row": 0, "frames": cols, "emote": {"row": 8, "frames": cols}},
        "sleeping": {"row": 1, "frames": cols, "emote": {"row": 9, "frames": cols}},
        "working": {"row": 2, "frames": cols, "emote": {"row": 10, "frames": cols}},
        "thinking": {"row": 3, "frames": cols, "emote": {"row": 11, "frames": cols}},
    },
    "states": {"idle": {"row": 0}, "waiting": {"row": 3}},
}
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh)
print(f"sheet={breite}x{hoehe} cellW={cw} cellH={ch}")
PYEOF
)"
rig_rc=$?
chk "Pruefstand-Pet wird gebaut" "$rig_rc" 0
echo "  $rig_bau"
[[ "$rig_rc" -eq 0 ]] || { echo "  FAIL ohne Pruefstand-Pet keine Messung"; exit 1; }

# Alle Erwartungswerte kommen aus der geschriebenen Datei ZURUECK, nicht aus
# einer zweiten Tabelle hier. Zwei Fassungen derselben Zuordnung waeren eine
# Zuordnung und eine Attrappe.
lese_rig="$("$PY" - "$RIG" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
for name, e in m["moods"].items():
    print("atem_%s=%d" % (name, e["row"]))
    print("atembilder_%s=%d" % (name, e.get("frames", 0)))
    print("emote_%s=%d" % (name, e.get("emote", {}).get("row", -1)))
    print("emotebilder_%s=%d" % (name, e.get("emote", {}).get("frames", 0)))
PYEOF
)"
rw() { grep -m1 "^$1=" <<<"$lese_rig" | cut -d= -f2; }

# Die Positivkontrolle zu allem, was folgt: waeren Atem- und Emote-Zeile
# dieselbe Zahl, wuerde jeder Zeilenvergleich unten DASSELBE messen und gruen
# stehen -- ohne dass irgendetwas belegt waere. Das gilt fuer die animierten
# Moods (dort muss die Emote-Zeile kommen) genauso wie fuer die Ruhe-Moods
# (dort darf sie NICHT kommen).
for m in idle sleeping working thinking; do
  chk "$m: Emote- und Atemzeile sind verschieden (sonst misst der Lauf nichts)" \
    "$([[ "$(rw "atem_$m")" != "$(rw "emote_$m")" && "$(rw "emote_$m")" != -1 ]] && echo ja || echo nein)" ja
  chk "$m: das Emote hat $EMOTE_SPALTEN Spalten (die Stichprobenzeiten haengen daran)" \
    "$(rw "emotebilder_$m")" "$EMOTE_SPALTEN"
  chk "$m: die Atemzeile hat mehrere Spalten (sonst waere Stillstand kein Befund)" \
    "$(num_ge "$(rw "atembilder_$m")" 2)" ja
done

# --- Die Stichprobenzeiten gegen UMLAEUFE rechnen -----------------------------
# Nicht hingeschrieben, sondern geprueft. Wer `EMOTE_UMLAEUFE` aendert und die
# Zeiten stehen laesst, bekommt hier ein rotes Kriterium mit Klartext statt
# einer stillen Fehlmessung an der falschen Stelle.
#
# RAND ist derselbe Wert, den der Messkopf einer verspaeteten Stichprobe
# zugesteht: naeher als das darf ein Punkt an keine Umlaufgrenze.
RAND=0.25
T_EMOTE_A=1.0     # mitten im ersten Umlauf, Beginn des Emote-Taktfensters
T_EMOTE_B=2.0     # schliesst EINEN Umlauf aus
T_LAEUFT=3.2      # schliesst UMLAEUFE-1 aus, Ende des Emote-Taktfensters
T_VORBEI=4.4      # schliesst UMLAEUFE+1 aus, Beginn des Atem-Taktfensters
T_SPAET=6.0       # "und es bleibt dabei"
T_ENDE=9.0        # Ende des Atem-Taktfensters
umlauf="$(rechne "$EMOTE_SPALTEN / $ANIM_FPS")"
ende_soll="$(rechne "$umlauf * $UMLAEUFE")"
ende_vorher="$(rechne "$umlauf * ($UMLAEUFE - 1)")"
ende_danach="$(rechne "$umlauf * ($UMLAEUFE + 1)")"
echo "  ein Umlauf ${umlauf}s; $UMLAEUFE Umlaeufe enden bei ${ende_soll}s, $((UMLAEUFE - 1)) bei ${ende_vorher}s, $((UMLAEUFE + 1)) bei ${ende_danach}s"
chk "der Laufpunkt ${T_LAEUFT}s liegt hinter $((UMLAEUFE - 1)) Umlaeufen (schliesst sie aus)" \
  "$(f_ge "$T_LAEUFT" "$(rechne "$ende_vorher + $RAND")")" ja
chk "der Laufpunkt ${T_LAEUFT}s liegt noch vor dem Ende von $UMLAEUFE Umlaeufen" \
  "$(f_le "$T_LAEUFT" "$(rechne "$ende_soll - $RAND")")" ja
chk "der Endpunkt ${T_VORBEI}s liegt hinter $UMLAEUFE Umlaeufen" \
  "$(f_ge "$T_VORBEI" "$(rechne "$ende_soll + $RAND")")" ja
chk "der Endpunkt ${T_VORBEI}s liegt vor dem Ende von $((UMLAEUFE + 1)) Umlaeufen (schliesst sie aus)" \
  "$(f_le "$T_VORBEI" "$(rechne "$ende_danach - $RAND")")" ja
ZEITEN="$T_EMOTE_A,$T_EMOTE_B,$T_LAEUFT,$T_VORBEI,$T_SPAET,$T_ENDE"
# Die beiden Taktfenster, jedes ganz innerhalb einer Phase.
EMOTE_FENSTER="$(rechne "$T_LAEUFT - $T_EMOTE_A")"
ATEM_FENSTER="$(rechne "$T_ENDE - $T_VORBEI")"

# --- Das ausgelieferte Pet, falls es daliegt ----------------------------------
# Kein Live-Lauf darauf -- es ist nicht versioniert. Aber wenn es da ist, darf
# still niemand die Emote-Zeilen wieder herausnehmen. Fehlt es, steht hier
# ausdruecklich UEBERSPRUNGEN und nicht "ok".
#
# Seit dem 02.09. sind `idle` und `sleeping` ausgenommen: sie spielen kein
# Emote mehr, also ist eines dort weder noetig noch ein Mangel. Der Pruefling
# ignoriert ein trotzdem vorhandenes (`MOODS_OHNE_EMOTE`) -- das ist im
# Live-Lauf unten gemessen, nicht hier.
if [[ -f "$HELD" ]]; then
  held_befund="$("$PY" - "$HELD" <<'PYEOF'
import json, sys
# Dieselbe Liste wie MOODS_OHNE_EMOTE in face/src/main.rs. Sie steht hier ein
# zweites Mal, und das ist bewusst: dieser Lauf soll rot werden, wenn sie sich
# aendert, statt der Aenderung stumm zu folgen. Was die Liste im BETRIEB
# bewirkt, misst der Block `atem_block idle` weiter unten.
OHNE_EMOTE = ("idle", "sleeping")
m = json.load(open(sys.argv[1], encoding="utf-8"))
rows = m.get("atlas", {}).get("rows", 0)
schlecht = []
geprueft = 0
for name, e in m.get("moods", {}).items():
    if e.get("frames", 1) <= 1:
        continue                      # Standbild, kein Emote noetig
    if name in OHNE_EMOTE:
        continue                      # Ruhezustand, spielt ohnehin keines
    geprueft += 1
    em = e.get("emote")
    if not em:
        schlecht.append(f"{name}: kein emote")
    elif em.get("row") == e.get("row"):
        schlecht.append(f"{name}: emote auf der Atemzeile {em['row']}")
    elif em.get("row", rows) >= rows:
        schlecht.append(f"{name}: emote-Zeile {em['row']} ausserhalb von {rows} Zeilen")
    elif em.get("frames", 1) <= 1:
        schlecht.append(f"{name}: emote mit {em.get('frames')} Bildern")
print("geprueft=%d" % geprueft)
print("schlecht=%s" % ("; ".join(schlecht) if schlecht else "-"))
PYEOF
)"
  hb() { grep -m1 "^$1=" <<<"$held_befund" | cut -d= -f2-; }
  chk "held-Pet: es gibt ueberhaupt emote-pflichtige Moods (Positivkontrolle)" \
    "$(num_ge "$(hb geprueft)" 1)" ja
  chk "held-Pet: jeder emote-pflichtige Mood hat ein Emote auf einer eigenen Zeile" \
    "$(hb schlecht)" -
else
  echo "  UEBERSPRUNGEN held-Pet nicht vorhanden ($HELD) — Struktur ungeprueft, NICHT gruen"
fi

chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
if [[ -z "${WAYLAND_DISPLAY:-}" || ! -x "$BIN" ]]; then
  echo "  FAIL Live-Pruefungen uebersprungen"
  exit 1
fi

# --- Das Face starten ---------------------------------------------------------
# Watchdog: zwei animierte Bloecke bis 9 s, ein Atem-Block bis 5 s, ein
# Ruhe-Block bis 3,5 s, Start und Reserve.
max_secs=$(( 2 * 10 + 6 + 5 + 60 ))
DAIMON_MAX_SECS=$max_secs "$BIN" --pet-manifest "$RIG" \
  --sprite-position 900,500 \
  --control-socket "$tmp/c.sock" --diag-socket "$tmp/d.sock" \
  >"$tmp/out.log" 2>"$tmp/err.log" &
face=$!
for _ in $(seq 1 200); do [[ -S "$tmp/d.sock" && -S "$tmp/c.sock" ]] && break; sleep 0.1; done
chk "Overlay startet" "$([[ -S "$tmp/d.sock" ]] && echo ja || echo nein)" ja
chk "Steuer-Socket vorhanden" "$([[ -S "$tmp/c.sock" ]] && echo ja || echo nein)" ja
[[ -S "$tmp/d.sock" && -S "$tmp/c.sock" ]] || { echo "  FAIL ohne Sockets keine Messung"; exit 1; }

# --- Die neuen Felder muessen IM BINARY sein ----------------------------------
diag_roh="$("$PY" - "$tmp/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
sys.stdout.write(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
)"
hat_feld() { "$PY" -c 'import json,sys; d=json.loads(sys.argv[1] or "{}"); print("ja" if sys.argv[2] in d else "nein")' "$diag_roh" "$1" 2>/dev/null || echo nein; }
chk "die Diagnose liefert zeile (sonst ist Emote von Atem ununterscheidbar)" \
  "$(hat_feld zeile)" ja
chk "die Diagnose liefert emote_laeuft" "$(hat_feld emote_laeuft)" ja
chk "die Diagnose liefert takt_schlaege (aus T-9.2, hier die Taktmessung)" \
  "$(hat_feld takt_schlaege)" ja
if [[ "$(hat_feld zeile)" != ja || "$(hat_feld emote_laeuft)" != ja ]]; then
  echo "  FAIL ohne zeile/emote_laeuft waere jeder Block unten falsch gruen -- Binary neu bauen"
  exit 1
fi

steuern() { "$PY" - "$tmp/c.sock" "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1])
c.sendall((sys.argv[2] + "\n").encode())
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}

# Positivkontrolle des Steuerkanals (aus T-9.2): ein "ok" bedeutet nur dann
# etwas, wenn Unsinn mit "err" abgewiesen wird. Ohne sie waere "die Zeile
# wechselt nicht" auch dann gruen, wenn jeder Mood-Befehl im Nichts landet.
chk "Steuer-Socket weist Unsinn mit err ab" "$(steuern 'mood gibt-es-nicht')" err

# Setzt einen Mood und nimmt zu festen Zeitpunkten NACH dem Befehl Stichproben.
# Ein Prozess statt einer Kette von Aufrufen: die Zusage haengt an einem
# Zeitfenster von einer halben Sekunde, und der Start eines python3 je
# Stichprobe waere schon ein Zehntel davon.
#
# Bricht ab -- und liefert dann KEINE Zeile --, wenn der Mood nicht bestaetigt
# wird, ein JSON-Feld fehlt oder eine Stichprobe zu spaet kommt.
messreihe() { "$PY" - "$tmp/c.sock" "$tmp/d.sock" "$1" "$2" "$3" <<'PYEOF'
import json, socket, sys, time

ctrl, diag, mood = sys.argv[1], sys.argv[2], sys.argv[3]
punkte = [float(t) for t in sys.argv[4].split(",")]
rand = float(sys.argv[5])
FELDER = ("zeile", "emote_laeuft", "takt_schlaege", "frames_rendered", "mood", "sprite")

c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(ctrl)
c.sendall((f"mood {mood}\n").encode())
antwort = c.makefile("rb").readline().decode().strip()
c.close()
t0 = time.monotonic()
if antwort != "ok":
    sys.exit(f"Steuerbefehl 'mood {mood}' nicht bestaetigt: {antwort!r}")

for soll in punkte:
    rest = t0 + soll - time.monotonic()
    if rest > 0:
        time.sleep(rest)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10); s.connect(diag)
    d = json.loads(s.makefile("rb").readline().decode())
    s.close()
    ist = time.monotonic() - t0
    for feld in FELDER:
        if feld not in d:
            sys.exit(f"Diagnose ohne {feld}: {d}")
    # Eine verspaetete Stichprobe misst einen anderen Zeitpunkt als den
    # zugesagten. Aus "das Emote endete zu frueh" wuerde dann ein gruenes
    # "war schon vorbei" -- lieber rot als falsch gemessen. `rand` ist
    # derselbe Wert, mit dem die Stichprobenzeiten oben gegen die
    # Umlaufgrenzen gerechnet wurden.
    if ist > soll + rand:
        sys.exit(f"Stichprobe {soll}s kam erst bei {ist:.3f}s")
    print(f"{soll} {ist:.3f} {d['zeile']} {str(d['emote_laeuft']).lower()} "
          f"{d['takt_schlaege']} {d['frames_rendered']} {d['mood']} {d['sprite']}")
PYEOF
}

# Fuehrt eine Messreihe und legt sie in $reihe ab. Bricht sie ab, wird das ein
# rotes Kriterium -- und `p` liefert danach "?" statt eines Wertes, der sich
# mit irgendetwas gleichsetzen liesse.
reihe=""
fahre() {
  local mood="$1" zeiten="$2" erwartet="$3" zeilen
  reihe="$(messreihe "$mood" "$zeiten" "$RAND" 2>&1)"
  local rc=$?
  zeilen="$(grep -c . <<<"$reihe")"
  if [[ "$rc" -ne 0 || "$zeilen" != "$erwartet" ]]; then
    echo "  FAIL Messreihe '$mood' unbrauchbar (rc=$rc, $zeilen von $erwartet Zeilen):"
    sed 's/^/       /' <<<"$reihe"
    fail=1
    reihe=""
    return 1
  fi
  sed 's/^/  /' <<<"$reihe"
  return 0
}
# p <Stichprobe> <Spalte>: 1 soll, 2 ist, 3 zeile, 4 emote_laeuft,
# 5 takt_schlaege, 6 frames_rendered, 7 mood, 8 sprite.
p() {
  local wert
  wert="$(awk -v i="$1" -v j="$2" 'NR==i{print $j}' <<<"$reihe")"
  [[ -n "$wert" ]] && echo "$wert" || echo "?"
}
# Differenz zweier Stichproben in einer Spalte. -1 statt 0, wenn eine Seite
# unlesbar ist: ein Loch darf kein "steht still" ergeben.
delta() {
  if [[ "$(p "$1" "$3")" =~ ^[0-9]+$ && "$(p "$2" "$3")" =~ ^[0-9]+$ ]]; then
    echo $(( $(p "$2" "$3") - $(p "$1" "$3") ))
  else
    echo -1
  fi
}
# Ein Taktfenster gegen seine Sollrate. 0,75 bis 1,25 wie in T-9.2 -- die
# Untergrenze faengt einen stehenden, die Obergrenze einen durchdrehenden Takt.
takt_pruefen() {
  local name="$1" schlaege="$2" fps="$3" dauer="$4" soll
  soll="$(rechne "$fps * $dauer")"
  chk "$name: der Takt liegt nicht unter $fps fps (soll ~$soll, war $schlaege)" \
    "$(f_ge "$schlaege" "$(rechne "$soll * 0.75")")" ja
  chk "$name: der Takt liegt nicht ueber $fps fps (soll ~$soll, war $schlaege)" \
    "$(f_le "$schlaege" "$(rechne "$soll * 1.25")")" ja
}

# Ein animierter Mood: die sechs Punkte aus dem Kopfkommentar.
animierter_block() {
  local mood="$1" atem emote
  atem="$(rw "atem_$mood")"; emote="$(rw "emote_$mood")"
  echo "--- $mood: Emote Zeile $emote, Atem Zeile $atem"
  fahre "$mood" "$ZEITEN" 6 || return

  chk "$mood: der Mood ist wirklich gesetzt" "$(p 1 7)" "$mood"
  # Zusage 1.
  chk "$mood: bei ${T_EMOTE_A}s steht die EMOTE-Zeile" "$(p 1 3)" "$emote"
  chk "$mood: bei ${T_EMOTE_A}s meldet emote_laeuft true" "$(p 1 4)" true
  # Genau das, was `zeile` ueberhaupt noetig gemacht hat: von aussen ist der
  # Unterschied an `mood` und `sprite` NICHT zu sehen. Faellt dieses Kriterium,
  # ist das neue Feld ueberfluessig -- und dann ist auch die Messung eine
  # andere als hier beschrieben.
  chk "$mood: mood ist waehrend Emote und Atem derselbe" "$(p 1 7)" "$(p 4 7)"
  chk "$mood: sprite ist waehrend Emote und Atem derselbe" "$(p 1 8)" "$(p 4 8)"
  # Schliesst EINEN Umlauf aus.
  chk "$mood: bei ${T_EMOTE_B}s laeuft das Emote noch (also mehr als ein Umlauf)" "$(p 2 4)" true
  chk "$mood: bei ${T_EMOTE_B}s steht immer noch die Emote-Zeile" "$(p 2 3)" "$emote"
  # Schliesst UMLAEUFE-1 aus -- das ist die Zusage von VOR dem 02.09., und
  # genau hier wurde die alte Fassung dieses Laufs rot.
  chk "$mood: bei ${T_LAEUFT}s laeuft das Emote NOCH (also mehr als $((UMLAEUFE - 1)) Umlaeufe)" "$(p 3 4)" true
  chk "$mood: bei ${T_LAEUFT}s steht immer noch die Emote-Zeile" "$(p 3 3)" "$emote"
  # Zusage 2, und sie schliesst UMLAEUFE+1 aus.
  chk "$mood: bei ${T_VORBEI}s steht die ATEM-Zeile (also genau $UMLAEUFE Umlaeufe)" "$(p 4 3)" "$atem"
  chk "$mood: bei ${T_VORBEI}s meldet emote_laeuft false" "$(p 4 4)" false
  # "und es bleibt dabei".
  chk "$mood: bei ${T_SPAET}s steht immer noch die Atemzeile" "$(p 5 3)" "$atem"
  chk "$mood: bei ${T_SPAET}s meldet emote_laeuft weiterhin false" "$(p 5 4)" false
  chk "$mood: bei ${T_ENDE}s steht immer noch die Atemzeile" "$(p 6 3)" "$atem"
  chk "$mood: bei ${T_ENDE}s meldet emote_laeuft weiterhin false" "$(p 6 4)" false

  # Zusage 3: zwei Takte, jeder in seinem eigenen Fenster gemessen. Die Uhr
  # laeuft ueber den Uebergang hinweg weiter -- ein Emote-Ende, das die ganze
  # Uhr anhielte, waere in jedem Zeilen-Kriterium oben gruen.
  local e_schlaege a_schlaege e_frames a_frames
  e_schlaege="$(delta 1 3 5)"; e_frames="$(delta 1 3 6)"
  a_schlaege="$(delta 4 6 5)"; a_frames="$(delta 4 6 6)"
  echo "  Emote-Fenster ${T_EMOTE_A}..${T_LAEUFT}s (${EMOTE_FENSTER}s): Schlaege +$e_schlaege, Bilder +$e_frames"
  echo "  Atem-Fenster  ${T_VORBEI}..${T_ENDE}s (${ATEM_FENSTER}s): Schlaege +$a_schlaege, Bilder +$a_frames"
  takt_pruefen "$mood: Emote" "$e_schlaege" "$ANIM_FPS" "$EMOTE_FENSTER"
  takt_pruefen "$mood: Atem" "$a_schlaege" "$ATEM_FPS" "$ATEM_FENSTER"
  # Und die beiden gegeneinander, unabhaengig von beiden Sollwerten: das ist
  # der Mutant, um den es geht -- ein gemeinsamer Takt fuer Atem und Emote.
  chk "$mood: das Emote schlaegt mindestens doppelt so schnell wie der Atem" \
    "$(f_ge "$(rechne "$e_schlaege / $EMOTE_FENSTER")" \
            "$(rechne "2 * $a_schlaege / $ATEM_FENSTER")")" ja
  # Bilder in BEIDEN Fenstern: die Zeile ist gezeichnet, nicht behauptet.
  chk "$mood: waehrend des Emotes entstehen Bilder" \
    "$(f_ge "$e_frames" "$(rechne "$ANIM_FPS * $EMOTE_FENSTER / 2")")" ja
  chk "$mood: waehrend des Atems entstehen Bilder" \
    "$(f_ge "$a_frames" "$(rechne "$ATEM_FPS * $ATEM_FENSTER / 2")")" ja
}

# Der Ruhezustand, der ATMET: `idle`. Seit dem 02.09. eine eigene Zusage und
# kein Wegfall -- das Pruefstand-Pet bietet ihm eine Emote-Zeile an, und
# gemessen wird, dass sie ueber die ganze Reihe nie kommt, waehrend die Uhr
# zugleich im Atemtakt schlaegt.
ATEM_ZEITEN="0.5,1.5,3.0,5.0"
ATEM_BLOCKFENSTER="$(rechne "5.0 - 0.5")"
atem_block() {
  local mood="$1" atem emote i
  atem="$(rw "atem_$mood")"; emote="$(rw "emote_$mood")"
  echo "--- $mood: atmet auf Zeile $atem, bekommt Zeile $emote angeboten und nimmt sie nicht"
  fahre "$mood" "$ATEM_ZEITEN" 4 || return

  chk "$mood: der Mood ist wirklich gesetzt" "$(p 1 7)" "$mood"
  for i in 1 2 3 4; do
    chk "$mood: bei Stichprobe $i steht die Atemzeile (nie die angebotene Emote-Zeile $emote)" \
      "$(p $i 3)" "$atem"
    chk "$mood: bei Stichprobe $i meldet emote_laeuft false" "$(p $i 4)" false
  done
  local schlaege frames
  schlaege="$(delta 1 4 5)"; frames="$(delta 1 4 6)"
  echo "  ueber ${ATEM_BLOCKFENSTER}s: Schlaege +$schlaege, Bilder +$frames"
  # Ohne diese beiden waere "kein Emote" auch dann gruen, wenn ueberhaupt
  # nichts liefe -- ein `idle`, das wieder in RUHIGE_MOODS steht, zeigt
  # ebenfalls nie eine Emote-Zeile. Das ist die Positivkontrolle zur Zusage
  # darueber, und sie klemmt zugleich den Takt von beiden Seiten ein.
  takt_pruefen "$mood: Atem" "$schlaege" "$ATEM_FPS" "$ATEM_BLOCKFENSTER"
  chk "$mood: es entstehen dabei Bilder (es atmet wirklich)" \
    "$(f_ge "$frames" "$(rechne "$ATEM_FPS * $ATEM_BLOCKFENSTER / 2")")" ja
}

# Ein Ruhe-Mood: die Uhr schlaegt kein einziges Mal, und die Zeile wechselt
# nicht. Zwei Stichproben ueber 3 s.
ruhe_block() {
  local mood="$1" atem emote
  atem="$(rw "atem_$mood")"; emote="$(rw "emote_$mood")"
  echo "--- $mood: Ruhe auf Zeile $atem, Zeile $emote angeboten und ungenutzt"
  fahre "$mood" "0.5,3.5" 2 || return

  chk "$mood: der Mood ist wirklich gesetzt" "$(p 1 7)" "$mood"
  chk "$mood: es steht die Zeile des Moods" "$(p 1 3)" "$atem"
  chk "$mood: die Zeile wechselt ueber 3s nicht" "$(p 2 3)" "$(p 1 3)"
  chk "$mood: kein Emote (ein Ruhe-Mood reagiert nicht, er ruht)" "$(p 1 4)" false
  chk "$mood: auch nach 3s kein Emote" "$(p 2 4)" false
  # Exakt 0 und ohne Toleranz, wie in T-9.2: die Uhr haengt sich aus oder sie
  # tut es nicht. Und weil das Manifest dieser Zeile mehrere Spalten gibt,
  # kommt die Null aus `RUHIGE_MOODS` und nicht aus dem Sheet.
  chk "$mood: die Uhr schlaegt kein einziges Mal" "$(delta 1 2 5)" 0
}

# Reihenfolge wie im Betrieb: arbeiten, zur Ruhe kommen, wieder arbeiten (auf
# einer ANDEREN Zeile), wieder zur Ruhe. Ein Prueffeld, das nur `working`
# kennt, bestuende auch ein `Animator::zeile()`, das die Emote-Zeile fest
# verdrahtet -- und ein `MOODS_OHNE_EMOTE` ohne `sleeping` bliebe ohne den
# letzten Block gruen.
animierter_block working
atem_block idle
animierter_block thinking
ruhe_block sleeping

chk "das Face lebt nach allen Bloecken noch" \
  "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja

echo
if [[ "$fail" -eq 0 ]]; then echo "T-9.3: alle Kriterien gruen"; else echo "T-9.3: FEHLGESCHLAGEN"; fi
exit "$fail"
