#!/usr/bin/env bash
# Erzeugt die T-0.12-Mutanten reproduzierbar -- und dazu einen korrekten
# Referenzbaum, ohne den die Mutanten nichts beweisen.
#
# BASIS IST EIN GEPINNTER COMMIT, NICHT HEAD und nicht der Arbeitsbaum.
# Aus ihm stammt ausschliesslich die `metadata.json` des Script-Pakets -- der
# Rumpf von `main.js` wird hier erzeugt, vom Reviewer, blind gegen dieselbe
# Akzeptanzliste. Wuerde `main.js` aus dem Arbeitsbaum uebernommen, waeren die
# Mutanten Abwandlungen des PRUEFLINGS statt unabhaengiger Gegenproben.
#
# DER BASIS-COMMIT MUSS DEN FEHLER NOCH ENTHALTEN. Das wird unten geprueft und
# bricht ab: enthielte er bereits die Fassung des Builders, waere "gepinnt" nur
# ein Wort und die Trennung dahin.
#
# WARUM EIN REFERENZBAUM. Ein Mutant beweist, dass der Verifizierer rot werden
# KANN. Er beweist nicht, dass er gruen werden kann -- daran ist bei T-2.7 ein
# Verifizierer gescheitert (Fall 12 in HANDOVER.md): eine Positivkontrolle, die
# nie gruen werden konnte, meldete jeden Mutanten als "erkannt", ohne dessen
# Mutation je gemessen zu haben.
#
#   DER REFERENZBAUM IST NICHT DER LIEFERGEGENSTAND und KEIN Gut-Muster im
#   Sinne von meta.sh. Er liegt deshalb ausdruecklich NICHT unter
#   tests/fixtures/known-good/. Das Gut-Muster entsteht erst aus dem
#   abgenommenen Stand der echten Implementierung.
#
# EINE OFFENGELEGTE KOPPLUNG. Der Referenzbaum muss den Hub tatsaechlich
# erreichen, also muss sein JSON ein Feld tragen, das der Hub als Vollbild
# liest. Der Name dieses Feldes wurde NICHT aus `daimon/hub/focus.py` gelesen
# -- der Reviewer ist gegen die Implementierung blind --, sondern am LAUFENDEN
# Dienst schwarzkastig ermittelt: `Event` mit je einem Kandidatennamen
# geschickt und `Zustand()` daraufhin abgelesen. `fullscreen` hat gewirkt,
# `vollbild`, `full_screen`, `fullScreen` und `voll` nicht. Der Referenzbaum
# schickt trotzdem mehrere Schreibweisen: ein defensiver Parser ignoriert
# unbekannte Felder, und so haengt die Gegenprobe nicht an einer Namenswahl.
#
# Aufruf:
#   tests/mutants/T-0.12/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
REFERENZ="$REPO/tests/fixtures/T-0.12-referenz-des-reviewers"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

# 6d71dc5 = "T-2.5.v: Verifizierer, zwei Mutanten -- und ein dritter Baum, der
# absichtlich durchgeht". Letzter Commit VOR der Reparatur aus T-0.12.v2; sein
# main.js traegt noch den 15-Argumente-Aufruf.
BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-6d71dc5}"
git -C "$REPO" archive "$BASIS_COMMIT" kwin-script | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
BASIS_META="$BASIS/kwin-script/daimon-watcher/metadata.json"
BASIS_JS="$BASIS/kwin-script/daimon-watcher/contents/code/main.js"
[[ -f "$BASIS_META" ]] \
  || { echo "Basisbaum unvollstaendig: metadata.json fehlt"; exit 1; }
[[ -f "$BASIS_JS" ]] \
  || { echo "Basisbaum unvollstaendig: main.js fehlt"; exit 1; }

# Der Abbruch, der die Trennung haelt: der Basis-Commit MUSS den Fehler noch
# haben. Gemessen wird die Argumentzahl, nicht ein Stichwort -- ein grep auf
# "JSON.stringify" waere an der Schreibweise zu umgehen.
argzahl_basis="$(python3 - "$BASIS_JS" <<'PYEOF'
import re
import sys

text = re.sub(r"//[^\n]*", "", open(sys.argv[1], encoding="utf-8").read())
aus = []
for m in re.finditer(r"callDBus\s*\(", text):
    i, tiefe, arg, in_str, esc, quote = m.end(), 1, 1, False, False, ""
    while i < len(text) and tiefe > 0:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
        elif c in "'\"`":
            in_str, quote = True, c
        elif c in "([{":
            tiefe += 1
        elif c in ")]}":
            tiefe -= 1
        elif c == "," and tiefe == 1:
            arg += 1
        i += 1
    aus.append(arg)
print(max(aus) if aus else 0)
PYEOF
)"
if [[ "${argzahl_basis:-0}" -le 13 ]]; then
  echo "FEHLER: der Basis-Commit $BASIS_COMMIT ruft callDBus mit hoechstens"
  echo "        $argzahl_basis Argumenten -- er enthaelt den Fehler also nicht"
  echo "        mehr. Dann ist er entweder falsch gepinnt, oder die Reparatur"
  echo "        des Builders ist bereits darin. Beides macht die Mutanten zu"
  echo "        Abwandlungen des Prueflings. Basis neu pinnen."
  exit 1
fi
echo "Basis-Commit $BASIS_COMMIT: callDBus mit $argzahl_basis Argumenten (der Fehler ist drin, gut)"

python3 - "$SCRIPT_DIR" "$BASIS_META" "$REFERENZ" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis_meta, referenz = sys.argv[1], sys.argv[2], sys.argv[3]

# ===========================================================================
# Der gemeinsame Rumpf. Nur die zwei Bloecke @@VOLLBILD@@ und @@AUFRUF@@
# unterscheiden die drei Baeume -- alles andere ist identisch, damit ein
# Mutant nur an SEINER Mutation auffaellt.
#
# Platzhalter statt str.format: der Rumpf ist voller geschweifter Klammern,
# und ein verdoppeltes `{{` mehr oder weniger ist genau die Sorte Fehler, die
# einen Mutantenbaum lautlos zu einer unveraenderten Kopie macht.
# ===========================================================================
RUMPF = '''// T-0.12: Fokus-Watcher fuer KWin.
//
// @@TITEL@@
//
// REIN LESEND. Das Script fragt Fenster ab und meldet; es bewegt, schliesst
// und veraendert nichts.
//
// EIN JSON-STRING STATT ELF WERTEN. `callDBus` nimmt vier feste Argumente
// (Dienst, Pfad, Schnittstelle, Methode) und danach die Nutzlast. KWin kappt
// die GESAMTE Argumentliste bei 13 und protokolliert "Too many arguments,
// ignoring N" -- die Meldung erreicht den Hub dann nie, und zwar lautlos.
// Elf Einzelwerte waren 15 Argumente und genau dieser Fehler. Mit einem
// einzigen JSON-String sind es fuenf, und ein neues Feld verlaengert den
// String statt die Argumentliste: die Grenze ist strukturell nicht mehr
// erreichbar.
var SERVICE = "de.daimon.Focus";
var PFAD = "/Focus";
var IFACE = "de.daimon.Focus";

function text(x) {
    return (x === undefined || x === null) ? "" : ("" + x);
}

function zahl(x) {
    var n = Math.round(Number(x));
    return isFinite(n) ? n : 0;
}

function melde(w, anlass) {
    if (!w) { return; }
    var g = w.frameGeometry ? w.frameGeometry : {};
    var nutzlast = {
        anlass: text(anlass),
        caption: text(w.caption),
        titel: text(w.caption),
        klasse: text(w.resourceClass),
        name: text(w.resourceName),
        rolle: text(w.windowRole),
        pid: zahl(w.pid),
@@VOLLBILD@@
        x: zahl(g.x),
        y: zahl(g.y),
        breite: zahl(g.width),
        hoehe: zahl(g.height),
        desktop: 0
    };
@@AUFRUF@@
}

workspace.windowActivated.connect(function (w) { melde(w, "activated"); });

if (workspace.windowList) {
    var offen = workspace.windowList();
    for (var i = 0; i < offen.length; i++) {
        if (offen[i] && offen[i].active) { melde(offen[i], "initial"); }
    }
}
'''

# Mehrere Schreibweisen ABSICHTLICH. Siehe der Kopf von erzeugen.sh: welcher
# Name beim Hub ankommt, wurde schwarzkastig ermittelt; ein defensiver Parser
# ignoriert die uebrigen. So haengt die Gegenprobe nicht an einer Namenswahl.
VOLLBILD_KORREKT = '''        fullscreen: w.fullScreen ? true : false,
        vollbild: w.fullScreen ? true : false,
        full_screen: w.fullScreen ? true : false,
'''

VOLLBILD_MUTANT = '''        // MUTANT: der Vollbildzustand wird nicht gelesen, sondern
        // behauptet. Das Script MELDET weiter -- puenktlich, vollstaendig,
        // mit richtiger Geometrie und richtigem Titel --, es sagt nur immer
        // "kein Vollbild".
        fullscreen: false,
        vollbild: false,
        full_screen: false,
'''

AUFRUF_KORREKT = '''    callDBus(SERVICE, PFAD, IFACE, "Event", JSON.stringify(nutzlast));
'''

AUFRUF_MUTANT = '''    // MUTANT: elf Einzelwerte statt eines JSON-Strings. Mit den vier festen
    // Argumenten sind das 15; KWin kappt bei 13. Der Hub bekommt neun Werte
    // gegen eine Signatur mit elf und verwirft -- die Meldung kommt NIE an.
    callDBus(SERVICE, PFAD, IFACE, "Event",
             nutzlast.caption, nutzlast.klasse, nutzlast.name,
             nutzlast.rolle, nutzlast.anlass, nutzlast.fullscreen,
             nutzlast.x, nutzlast.y, nutzlast.breite, nutzlast.hoehe,
             nutzlast.desktop);
'''


def baum(pfad, titel, vollbild, aufruf, notiz_text):
    paket = os.path.join(pfad, "kwin-script", "daimon-watcher")
    if os.path.exists(os.path.join(pfad, "kwin-script")):
        shutil.rmtree(os.path.join(pfad, "kwin-script"))
    os.makedirs(os.path.join(paket, "contents", "code"), exist_ok=True)
    shutil.copyfile(basis_meta, os.path.join(paket, "metadata.json"))
    text = (RUMPF.replace("@@TITEL@@", titel)
                 .replace("@@VOLLBILD@@", vollbild.rstrip("\\n"))
                 .replace("@@AUFRUF@@", aufruf.rstrip("\\n")))
    with open(os.path.join(paket, "contents", "code", "main.js"), "w",
              encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(pfad, "mutation.txt"), "w", encoding="utf-8") as f:
        f.write(notiz_text)


# ===========================================================================
# Referenz: korrekt. Kein Liefergegenstand, kein Gut-Muster.
# ===========================================================================
baum(
    referenz,
    "Minimale KORREKTE Fassung, vom Reviewer blind gegen die Akzeptanzliste\\n"
    "// geschrieben. NICHT der Liefergegenstand.",
    VOLLBILD_KORREKT, AUFRUF_KORREKT,
    """KEIN MUTANT, KEIN GUT-MUSTER, KEIN LIEFERGEGENSTAND.

Dieser Baum ist die minimal korrekte Umsetzung des Fokus-Watchers,
geschrieben vom Reviewer, blind gegen dieselbe Akzeptanzliste wie die echte
Implementierung. Er hat genau einen Zweck: zu zeigen, dass
tests/verify/T-0.12.sh gruen werden KANN. Ein Verifizierer, der das nicht
kann, meldet jeden Mutanten als "erkannt", ohne dessen Mutation je gemessen
zu haben -- das ist Fall 12 in HANDOVER.md und war bei T-2.7 real.

Er liegt bewusst NICHT unter tests/fixtures/known-good/: das Gut-Muster fuer
meta.sh entsteht erst aus dem abgenommenen Stand der echten Implementierung.

OFFENGELEGT: der Feldname `fullscreen` stammt nicht aus dem Quelltext des
Hubs, sondern aus einer schwarzkastigen Messung am laufenden Dienst (Event
mit Kandidatennamen schicken, Zustand() ablesen). Der Baum schickt mehrere
Schreibweisen, damit die Gegenprobe nicht an einer Namenswahl haengt.
""",
)

# ===========================================================================
# Mutant 1: zu-viele-argumente
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "zu-viele-argumente"),
    "MUTANT: elf Einzelwerte an callDBus = 15 Argumente. KWin kappt bei 13.",
    VOLLBILD_KORREKT, AUFRUF_MUTANT,
    """MUTANT: `callDBus` bekommt elf Einzelwerte statt eines JSON-Strings.

DAS IST DER FEHLER, DER AM 03.08. REAL WAR. Mit den vier festen Argumenten
(Dienst, Pfad, Schnittstelle, Methode) sind elf Werte 15 Argumente; KWin
kappt bei 13 und protokolliert "Too many arguments, ignoring 2". Beim Hub
kommen neun Werte an gegen eine Signatur mit elf -- die Meldung erreicht ihn
NIE. `Zustand()` bleibt bei (false, -1.0), auch mit einem echten
Vollbildfenster auf dem Schirm.

DIESER MUTANT ZIELT AUF DEN ALTEN VERIFIZIERER. Der hat geprueft, dass ein
verschachtelter kwin_wayland mit aktiviertem Script nicht STIRBT, und
ausdruecklich toleriert, dass der Ladevorgang nicht einmal im Protokoll
steht. Gegen ihn ist dieser Mutant vollstaendig gruen: das Script ist
syntaktisch fehlerfrei, es laedt, es laeuft, es benutzt windowActivated,
captionChanged, callDBus und frameGeometry, es ruft nichts Schreibendes auf,
und der Compositor ueberlebt. Es kommt nur nichts an.

Gefangen von:
  * Abschnitt 5, "nach dem Fokuswechsel ist das Alter ZURUECKGESETZT"
  * Abschnitt 6, "bei echtem Vollbild meldet Zustand() true"
  * Abschnitt 6, "an BEIDEN Zeitpunkten ist eine frische Meldung angekommen"
  * Abschnitt 7, "im Messfenster des Prueflings steht 'Too many arguments'
    NICHT" -- und zwar mit der Positivkontrolle daneben, die zeigt, dass der
    Verifizierer diese Zeile ueberhaupt sehen wuerde.

NICHT gefangen von den Positivkontrollen in 5 und 6 (die Referenzsonde sieht
den Fokuswechsel und das Vollbildfenster einwandfrei) -- und das ist der
Punkt: erst dadurch ist "es kam nichts an" von "es gab gar keinen Reiz" zu
unterscheiden.
""",
)

# ===========================================================================
# Mutant 2: meldet-immer-false
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "meldet-immer-false"),
    "MUTANT: meldet zuverlaessig -- aber immer 'kein Vollbild'.",
    VOLLBILD_MUTANT, AUFRUF_KORREKT,
    """MUTANT: der Vollbildzustand wird behauptet statt gelesen.

Das Script meldet vollstaendig und puenktlich: jeder Fokuswechsel erreicht
den Hub, das Alter faellt, Titel, Klasse und Geometrie stimmen. Nur der
Vollbildzustand ist eine Konstante -- immer `false`.

DIESER MUTANT ZIELT AUF DIE BEQUEME FASSUNG DER POSITIVKONTROLLE. Ein
Verifizierer, der sich mit "irgendetwas kam an" zufriedengibt -- Alter >= 0,
Alter faellt --, ist bei ihm vollstaendig gruen. Ebenso einer, der Vollbild
nur in EINER Richtung prueft, naemlich in der falschen: "nach dem Schliessen
ist es wieder false" besteht er muehelos, weil er nie etwas anderes sagt.
(Sein Spiegelbild, ein Watcher mit `fullscreen: true`, bestuende die
Gegenrichtung ebenso muehelos. Deshalb prueft Abschnitt 6 beide.)

Gefangen von:
  * Abschnitt 6, "bei echtem Vollbild meldet Zustand() true"

NICHT gefangen von Abschnitt 5 (dort kommt die Meldung korrekt an), nicht
von der Gegenrichtung in Abschnitt 6, nicht von Abschnitt 7 (fuenf
Argumente, kein "Too many arguments") und nicht von den Positivkontrollen
der Referenzsonde. Er faellt an genau einer Stelle -- und dass er sonst
ueberall gruen ist, ist der Beleg, dass die uebrigen Pruefungen den
Vollbildzustand tatsaechlich nicht mitmessen.
""",
)

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe ---------------------------------------------------------------
# Ein abgebrochenes Erzeugungsskript hat in diesem Projekt schon einmal eine
# unveraenderte Kopie hinterlassen, und zwei Mutantenbaeume waren schon einmal
# identisch. Beides faellt hier auf, BEVOR irgendein Lauf etwas "beweist".
rc=0
MUTANTEN=(zu-viele-argumente meldet-immer-false)
BAEUME=("$REFERENZ")
for m in "${MUTANTEN[@]}"; do BAEUME+=("$SCRIPT_DIR/$m"); done

zaehle_args() {  # $1 = main.js -> groesste Argumentzahl eines callDBus
  python3 - "$1" <<'PYEOF'
import re
import sys

text = re.sub(r"//[^\n]*", "", open(sys.argv[1], encoding="utf-8").read())
aus = []
for m in re.finditer(r"callDBus\s*\(", text):
    i, tiefe, arg, in_str, esc, quote = m.end(), 1, 1, False, False, ""
    while i < len(text) and tiefe > 0:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
        elif c in "'\"`":
            in_str, quote = True, c
        elif c in "([{":
            tiefe += 1
        elif c in ")]}":
            tiefe -= 1
        elif c == "," and tiefe == 1:
            arg += 1
        i += 1
    aus.append(arg)
print(max(aus) if aus else 0)
PYEOF
}

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  js="$b/kwin-script/daimon-watcher/contents/code/main.js"
  meta="$b/kwin-script/daimon-watcher/metadata.json"
  if [[ ! -f "$js" ]]; then
    echo "FEHLER: $name hat kein main.js"; rc=1; continue
  fi
  zeilen="$(wc -l <"$js")"
  args="$(zaehle_args "$js")"
  echo "$name: $zeilen Zeilen main.js, groesster callDBus-Aufruf $args Argumente"
  [[ "$zeilen" -lt 40 ]] && { echo "  FEHLER: $name ist praktisch leer"; rc=1; }
  # metadata.json muss identisch zum Basis-Commit sein -- sonst misst der
  # Verifizierer spaeter etwas anderes als die Mutation.
  if ! diff -q "$BASIS_META" "$meta" >/dev/null; then
    echo "  FEHLER: $name weicht in metadata.json vom Basis-Commit ab"; rc=1
  fi
  if ! jq -e . "$meta" >/dev/null 2>/dev/null; then
    echo "  FEHLER: $name hat kein gueltiges metadata.json"; rc=1
  fi
  # Grobe Syntaxprobe ohne JS-Engine: die Klammern muessen aufgehen.
  if ! python3 -c '
import sys
t = open(sys.argv[1], encoding="utf-8").read()
paare = {")": "(", "]": "[", "}": "{"}
stapel = []
for c in t:
    if c in "([{":
        stapel.append(c)
    elif c in ")]}":
        if not stapel or stapel.pop() != paare[c]:
            raise SystemExit(1)
raise SystemExit(0 if not stapel else 1)
' "$js"; then
    echo "  FEHLER: $name hat unbalancierte Klammern"; rc=1
  fi
done

# Die Baeume muessen sich VONEINANDER unterscheiden, und zwar in main.js.
for i in 0 1 2; do
  for j in 1 2; do
    [[ "$i" -lt "$j" ]] || continue
    if diff -q "${BAEUME[$i]}/kwin-script/daimon-watcher/contents/code/main.js" \
               "${BAEUME[$j]}/kwin-script/daimon-watcher/contents/code/main.js" \
               >/dev/null 2>/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$i]}") und $(basename "${BAEUME[$j]}") sind identisch"
      rc=1
    fi
  done
done

# Und keiner darf mit dem Basis-Commit identisch sein -- dann waere er keine
# eigene Fassung, sondern die alte, und der Referenzbaum koennte gar nicht
# gruen werden.
for b in "${BAEUME[@]}"; do
  if diff -q "$BASIS_JS" "$b/kwin-script/daimon-watcher/contents/code/main.js" \
       >/dev/null 2>/dev/null; then
    echo "FEHLER: $(basename "$b") ist eine unveraenderte Kopie des Basis-Commits"; rc=1
  fi
done

# Probe aufs Exempel. Nur Plausibilitaet -- das Urteil faellt der Verifizierer.
R="$REFERENZ/kwin-script/daimon-watcher/contents/code/main.js"
M1="$SCRIPT_DIR/zu-viele-argumente/kwin-script/daimon-watcher/contents/code/main.js"
M2="$SCRIPT_DIR/meldet-immer-false/kwin-script/daimon-watcher/contents/code/main.js"

[[ "$(zaehle_args "$R")"  -le 13 ]] || { echo "FEHLER: die Referenz ueberschreitet die Argumentgrenze"; rc=1; }
[[ "$(zaehle_args "$M1")" -gt 13 ]] || { echo "FEHLER: zu-viele-argumente haelt die Grenze doch ein"; rc=1; }
[[ "$(zaehle_args "$M2")" -le 13 ]] || { echo "FEHLER: meldet-immer-false ueberschreitet die Grenze auch noch --"
                                          echo "        dann faellt er in Abschnitt 7 statt in Abschnitt 6"; rc=1; }

grep -q 'w.fullScreen' "$R"  || { echo "FEHLER: die Referenz liest den Vollbildzustand gar nicht"; rc=1; }
grep -q 'w.fullScreen' "$M1" || { echo "FEHLER: zu-viele-argumente liest den Vollbildzustand nicht --"
                                   echo "        dann faellt er auch in Abschnitt 6 und ist nicht isoliert"; rc=1; }
grep -q 'w.fullScreen' "$M2" && { echo "FEHLER: meldet-immer-false liest den Vollbildzustand doch"; rc=1; }
# Und die Gegenprobe zum grep selbst: `meldet-immer-false` MUSS weiterhin
# melden. Ein Mutant, der gar nichts mehr schickt, faellt in Abschnitt 5 --
# und dann waere "Abschnitt 6 faengt ihn" geschenkt.
grep -q 'JSON.stringify' "$M2" || { echo "FEHLER: meldet-immer-false meldet gar nicht mehr"; rc=1; }
grep -q 'JSON.stringify' "$M1" && { echo "FEHLER: zu-viele-argumente benutzt doch den JSON-String"; rc=1; }

echo
echo "Mutanten: ${MUTANTEN[*]}"
echo "Referenz (kein Gut-Muster): $REFERENZ"
exit $rc
