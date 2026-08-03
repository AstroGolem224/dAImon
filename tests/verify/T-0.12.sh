#!/usr/bin/env bash
# Verifizierer fuer T-0.12: der KWin-Fokus-Watcher muss tatsaechlich MELDEN.
#
# ============================================================================
# WARUM ES DIESE FASSUNG GIBT
# ============================================================================
#
# Die vorige Fassung hat geprueft, dass ein VERSCHACHTELTER kwin_wayland mit
# aktiviertem Script nicht stirbt -- und hat ausdruecklich toleriert, dass der
# Ladevorgang nicht einmal im Protokoll steht ("KWin meldet den Ladevorgang
# nicht immer; dann zaehlt, dass der Compositor ueberhaupt hochkommt").
#
# Eine Pruefung "EINE MELDUNG IST ANGEKOMMEN" gab es nicht. Genau deshalb
# konnte der Watcher wochenlang tot sein, waehrend Gate P0 11 von 11 gruen
# meldete:
#
#   * `daimon-watcher` war in der lebenden Sitzung GAR NICHT INSTALLIERT --
#     unter ~/.local/share/kwin/scripts/ lag nur `daimon-focusprobe` aus dem
#     Spike.
#   * Und selbst geladen waere keine Meldung angekommen: `callDBus(SERVICE,
#     PATH, IFACE, "Event", ...11 Werte)` sind 15 Argumente, KWin kappt bei 13
#     und protokolliert "Too many arguments, ignoring 2". `Zustand()` blieb
#     ueber einen ganzen Versuch bei (false, -1.0), auch mit einem echten
#     Vollbildfenster auf dem Schirm.
#
# Das ist Fall 1 und 10 der Liste in HANDOVER.md: die bequeme Groesse gemessen
# (der Compositor ueberlebt) statt der zugesagten (das Fokusereignis erreicht
# den Hub).
#
# ============================================================================
# WAS HIER DIE MESSUNG IST
# ============================================================================
#
# Gemessen wird in der LEBENDEN SITZUNG. Dort ist der Fehler entstanden, dort
# muss er ausgeschlossen sein. Ein verschachtelter Compositor kann das nicht
# ersetzen -- er hat den Fehler ja gerade nicht gefunden.
#
#   Abschnitt 1  DER AUSWERTER SELBST. `Zustand()` liefert "b d". Ein Parser,
#                der bei jedem Zweifel "false" sagt, waere in Abschnitt 5 und
#                6 unauffaellig und wuerde jede Aussage darueber entwerten.
#                Vier bekannte Eingaben, zwei davon muessen unterschiedlich
#                herauskommen, eine muss als Fehler erkannt werden.
#
#   Abschnitt 2  BESTAND. Ist `daimon-watcher` in DIESER Sitzung als Paket
#                installiert, ist er GELADEN (isScriptLoaded, nicht "liegt im
#                Repo"), und ist die installierte Datei byteweise dieselbe wie
#                die im geprueften Baum? Der dritte Punkt ist die Falle aus
#                HANDOVER.md ("Das Repo und das laufende System driften
#                auseinander") -- ohne ihn belegt Abschnitt 5/6 etwas ueber
#                eine Datei, die in der Sitzung gar nicht laeuft.
#                NUR GEWERTET, WENN DAIMON_FIXTURE NICHT GESETZT IST. Ein
#                Mutantenbaum ist naturgemaess nicht installiert; wuerde das
#                gewertet, fiele jeder Mutant hier statt an seiner Mutation --
#                Fall 12 in HANDOVER.md.
#
#   Abschnitt 3  DIE REFERENZSONDE -- die Positivkontrolle des REIZES.
#                Ein eigenes, vom Reviewer geschriebenes KWin-Script meldet
#                unabhaengig vom Prueflung an einen eigenen DBus-Namen, WELCHE
#                Fenster KWin aktiviert und ob KWin sie fuer Vollbild haelt.
#                Ohne sie waere "Zustand() sagt weiter false" nicht von "das
#                Testfenster war gar nicht Vollbild" und nicht von "es gab gar
#                keinen Fokuswechsel" zu unterscheiden. Das ist genau der
#                Fehler, den dieser Task behebt, eine Ebene hoeher.
#
#   Abschnitt 4  DER PRUEFLING KOMMT AUS DEM GEPRUEFTEN BAUM. Geladen wird
#                per `loadScript ss <absoluter Pfad> <eindeutiger Name>`, nicht
#                ueber den Paketnamen: KWin haelt Pakete im Cache, und ein
#                unter demselben Namen ausgetauschtes main.js wird nicht
#                zuverlaessig neu gelesen. Ein Verifizierer, der das nicht
#                beachtet, misst bei jedem Mutanten die unmutierte Fassung.
#                Der laufende `daimon-watcher` wird vorher ENTLADEN, sonst
#                meldete er neben dem Mutanten weiter richtig und deckte ihn zu.
#
#   Abschnitt 5  EINE MELDUNG KOMMT AN -- der Kern. Erst die Gegenprobe, dass
#                `Zustand()` ueberhaupt eine laufende Uhr hat (zwei Abrufe
#                ohne Reiz, das Alter muss wachsen); dann ein gewoehnlicher
#                Fokuswechsel, und das Alter muss ZURUECKGESETZT sein.
#                Verglichen wird vorher/nachher, nicht der Endwert allein.
#
#   Abschnitt 6  VOLLBILD IN BEIDE RICHTUNGEN, drei Runden. Ein echtes
#                Vollbildfenster (tests/harness/vollbildfenster.py, aus T-1.1,
#                eingefroren) ⇒ true; nach dem Schliessen wieder false. Eine
#                Pruefung nur in eine Richtung bestuende auch ein Watcher, der
#                immer true meldet -- und die Gegenrichtung allein bestuende
#                einer, der immer false meldet. Beide Richtungen, und beide
#                mit dem Nachweis daneben, dass die Meldung ueberhaupt ankam
#                (das Alter faellt) und dass KWin das Fenster wirklich fuer
#                Vollbild hielt (Referenzsonde).
#
#   Abschnitt 7  DIE ARGUMENTGRENZE. Gemessen am VERHALTEN (Abschnitt 5/6) und
#                zusaetzlich an der Protokollzeile "Too many arguments", die im
#                Messfenster des Prueflings NICHT auftreten darf.
#                MIT POSITIVKONTROLLE: eine eigene Sonde ruft callDBus mit 15
#                Argumenten, und diese Zeile MUSS im Protokoll erscheinen.
#                Sonst waere ihre Abwesenheit oben die Nullaussage schlechthin.
#
# ============================================================================
# WAS HIER NICHT BELEGT WIRD -- ausgeschrieben, nicht kaschiert
# ============================================================================
#
# Siehe den Abschnitt OFFEN am Ende. Kurz: die Trefferquote ueber 50 Wechsel
# (die steht als Evidenz aus Spike T-1.9 und wird hier VERLANGT, nicht
# nachgespielt), das Ueberleben von `kwin --replace`, und die Frage, ob die
# GELADENE Instanz aus derselben Datei stammt wie die installierte.
#
# Aufruf:
#   tests/verify/T-0.12.sh
#   DAIMON_FIXTURE=<baum> tests/verify/T-0.12.sh   # Baum mit kwin-script/
#
# Der Verifizierer OEFFNET FENSTER in der laufenden Sitzung und entlaedt
# kurzzeitig `daimon-watcher`. Beides wird am Ende wiederhergestellt.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"
BESTAND_GEWERTET=ja
[[ -n "${DAIMON_FIXTURE:-}" ]] && BESTAND_GEWERTET=nein

# PyGObject und dbus-python gibt es nur fuer System-Python (HANDOVER.md,
# "Zwei Interpreter"). tkinter fuer die Fenster ebenso.
PY=/usr/bin/python3
PKG_REL="kwin-script/daimon-watcher"
MAIN_JS="$TARGET/$PKG_REL/contents/code/main.js"
INSTALL="$HOME/.local/share/kwin/scripts/daimon-watcher"
HARNESS="$REPO/tests/harness/vollbildfenster.py"
EVIDENZ="$REPO/spikes/focus/results.json"
RUNDEN="${DAIMON_T012_RUNDEN:-3}"
MAX_SECS="${DAIMON_T012_MAX_SECS:-300}"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
laut() { echo "  !! $*"; }
info() { echo "  INFO $*"; }

# `jq -r '.feld // false'` liefert bei einem echten `false` dasselbe wie bei
# einem FEHLENDEN Feld -- HANDOVER.md, "Fallen dieser Maschine". Dieser
# Verifizierer erwartet an vielen Stellen ausdruecklich `false`; dort ist das
# der Unterschied zwischen "gemessen und widerlegt" und "gar nicht gemessen".
jqb() { jq -r --arg f "$2" 'if has($f) then (.[$f]|tostring) else "FEHLT" end' <<<"$1"; }

kwin() {  # $@ = Methode und Argumente auf org.kde.kwin.Scripting
  timeout 10 busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting "$@" 2>/dev/null
}
geladen() {  # $1 = Pluginname -> true|false|FEHLER
  local r; r="$(kwin isScriptLoaded s "$1")"
  case "$r" in "b true") echo true ;; "b false") echo false ;; *) echo FEHLER ;; esac
}
entlade() {
  kwin unloadScript s "$1" >/dev/null
  local i
  for i in $(seq 1 20); do
    [[ "$(geladen "$1")" == false ]] && return 0
    sleep 0.25
  done
  return 1
}
lade_pfad() {  # $1 = absoluter Pfad auf main.js, $2 = Pluginname
  # ZWEI FALLEN, BEIDE AUF DIESER MASCHINE GEMESSEN:
  #
  #  (a) `loadScript` REGISTRIERT nur. Ausgefuehrt wird das Script erst durch
  #      `start`. Und `isScriptLoaded` sagt SCHON VORHER `true` -- ein
  #      Verifizierer, der sich darauf verlaesst, haelt ein registriertes,
  #      nie gelaufenes Script fuer geladen und misst danach das Schweigen
  #      eines Scripts, das nie angefangen hat. Genau die Sorte Fehler, die
  #      dieser Task behebt.
  #  (b) Nach einem `unloadScript` bleibt die Kennung kurz reserviert; ein
  #      `loadScript` unmittelbar danach liefert eine Kennung und laedt doch
  #      nicht. Deshalb wiederholen.
  local i
  for i in 1 2 3 4 5 6; do
    kwin loadScript ss "$1" "$2" >/dev/null
    sleep 1
    kwin start >/dev/null
    sleep 1
    [[ "$(geladen "$2")" == true ]] && return 0
  done
  return 1
}

echo "T-0.12 — der KWin-Fokus-Watcher muss tatsaechlich melden"
echo "  Baum:        $TARGET"
echo "  Interpreter: $PY"
echo "  Bestand wird gewertet: $BESTAND_GEWERTET (DAIMON_FIXTURE=${DAIMON_FIXTURE:-})"

# =============================================================================
# 0. Voraussetzungen
# =============================================================================
echo
echo "--- 0. Voraussetzungen ---"
chk "0 jq vorhanden"      "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "0 busctl vorhanden"  "$(command -v busctl >/dev/null && echo ja || echo nein)" ja
chk "0 journalctl vorhanden" "$(command -v journalctl >/dev/null && echo ja || echo nein)" ja
chk "0 System-Python vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
chk "0 dbus-python und PyGObject sind da" \
  "$("$PY" -c 'import dbus, dbus.service, gi; from dbus.mainloop.glib import DBusGMainLoop' 2>/dev/null && echo ja || echo nein)" ja
chk "0 tkinter ist da (die Testfenster)" \
  "$("$PY" -c 'import tkinter' 2>/dev/null && echo ja || echo nein)" ja
chk "0 eine Wayland-Sitzung ist erreichbar" \
  "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
chk "0 der geprueftte Baum bringt $PKG_REL/contents/code/main.js mit" \
  "$([[ -f "$MAIN_JS" ]] && echo ja || echo nein)" ja
chk "0 der EINGEFRORENE Harness tests/harness/vollbildfenster.py ist da" \
  "$([[ -f "$HARNESS" ]] && echo ja || echo nein)" ja
chk "0 org.kde.KWin /Scripting antwortet" \
  "$([[ "$(geladen daimon-nicht-vorhanden-$$)" == false ]] && echo ja || echo nein)" ja
chk "0 de.daimon.Focus ist auf dem Bus" \
  "$(timeout 10 busctl --user call de.daimon.Focus /Focus de.daimon.Focus Zustand --no-pager >/dev/null 2>/dev/null && echo ja || echo nein)" ja

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "T-0.12: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen."
  exit 1
fi

RT="$(mktemp -d)"
KENNUNG="t012-$$-$RANDOM"
REF_NAME="de.daimon.T012Ref$$"
PRUEF_SCRIPT="$KENNUNG-prueflung"
REF_SCRIPT="$KENNUNG-referenz"
ARG_SCRIPT="$KENNUNG-argsonde"
REF_PID=""
WAR_GELADEN="$(geladen daimon-watcher)"

# --- Der Bestand muss WEGBLEIBEN, nicht nur weggehen -------------------------
#
# Am 03.08. gemessen: `unloadScript daimon-watcher` entfernt ihn -- und das
# naechste `reconfigure` holt ihn zurueck, weil `kwinrc` seit der Reparatur
# von T-0.12 `[Plugins] daimon-watcherEnabled=true` sagt. KWin laedt aus der
# Konfiguration selbsttaetig nach:
#
#   vor  reconfigure:  Mutant true,  Bestand false
#   nach reconfigure:  Mutant true,  Bestand TRUE
#
# Folge, real eingetreten: der Mutant `meldet-immer-false` meldete brav "kein
# Vollbild" und wurde vom zurueckgekehrten Bestand ueberstimmt -- der Lauf war
# gruen, obwohl der Pruefling die Zusage bricht. Derselbe Fehlertyp wie Nr. 4
# der Liste im Handover: den Zustand einmal herstellen und ihn dann glauben,
# statt ihn zu HALTEN.
#
# Zwei Vorkehrungen, absichtlich beide:
#   (a) deterministisch -- der Schluessel wird fuer die Dauer des Laufs auf
#       false gesetzt und in aufraeumen() auf den vorgefundenen Wert
#       zurueckgestellt. Ohne diesen Schritt entscheidet die Tagesform, ob
#       zwischendurch jemand ein reconfigure ausloest.
#   (b) nachtraeglich -- nach der Messung wird geprueft, dass er WIRKLICH
#       weggeblieben ist. Kommt er doch zurueck, ist die Messung nicht
#       gescheitert, sondern NICHT ERFOLGT, und der Lauf faellt laut aus.
KWINRC_VORHER=""
bestand_stilllegen() {
  KWINRC_VORHER="$(kreadconfig6 --file kwinrc --group Plugins \
                     --key daimon-watcherEnabled 2>/dev/null)"
  kwriteconfig6 --file kwinrc --group Plugins \
                --key daimon-watcherEnabled false 2>/dev/null
  busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1
  sleep 1
  kwin unloadScript s daimon-watcher >/dev/null
  sleep 0.5
}
bestand_zurueck() {
  if [[ -n "$KWINRC_VORHER" ]]; then
    kwriteconfig6 --file kwinrc --group Plugins \
                  --key daimon-watcherEnabled "$KWINRC_VORHER" 2>/dev/null
  else
    kwriteconfig6 --file kwinrc --group Plugins \
                  --key daimon-watcherEnabled --delete 2>/dev/null
  fi
  busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1
}

aufraeumen() {
  bestand_zurueck
  kwin unloadScript s "$PRUEF_SCRIPT" >/dev/null
  kwin unloadScript s "$REF_SCRIPT" >/dev/null
  kwin unloadScript s "$ARG_SCRIPT" >/dev/null
  [[ -n "$REF_PID" ]] && kill "$REF_PID" 2>/dev/null
  pkill -f "vollbildfenster.py .*$RT" 2>/dev/null
  pkill -f "normalfenster.py .*$RT" 2>/dev/null
  # Den Bestand der Sitzung so hinterlassen, wie er war.
  if [[ "$WAR_GELADEN" == true && "$(geladen daimon-watcher)" != true ]]; then
    for _ in 1 2 3; do
      kwin loadScript s daimon-watcher >/dev/null
      sleep 1
      kwin start >/dev/null
      sleep 1
      [[ "$(geladen daimon-watcher)" == true ]] && break
    done
    echo "  (daimon-watcher wieder geladen: $(geladen daimon-watcher))"
  fi
  rm -rf -- "$RT"
}
trap aufraeumen EXIT INT TERM

# =============================================================================
# 1. Der Auswerter selbst
# =============================================================================
echo
echo "--- 1. Der Auswerter selbst (bevor er etwas ueber den Pruefling sagt) ---"

cat >"$RT/lies_zustand.py" <<'PYEOF'
"""Liest `Zustand()` von de.daimon.Focus -- und laesst sich zum Selbsttest
bekannte Zeichenketten vorlegen.

Die Trennung ist der Punkt: derselbe Parser, der in Abschnitt 5 und 6 urteilt,
wird hier gegen vier Eingaben gefahren, deren Antwort feststeht. Ein Parser,
der im Zweifel "false, -1" sagt, waere sonst ueberall unauffaellig.
"""
import json
import subprocess
import sys


def parse(text):
    """'b d\\nbd true 5.69' -> (True, 5.69). Alles andere -> Fehler."""
    zeile = ""
    for z in (text or "").splitlines():
        z = z.strip()
        if z.startswith("bd "):
            zeile = z
            break
    if not zeile:
        return {"ok": False, "grund": "keine Zeile 'bd ...' in der Antwort"}
    teile = zeile.split()
    if len(teile) != 3:
        return {"ok": False, "grund": "erwartet 3 Felder, waren %d" % len(teile)}
    if teile[1] not in ("true", "false"):
        return {"ok": False, "grund": "erstes Feld ist kein bool: %r" % teile[1]}
    try:
        alter = float(teile[2])
    except ValueError:
        return {"ok": False, "grund": "zweites Feld ist keine Zahl: %r" % teile[2]}
    return {"ok": True, "vollbild": teile[1] == "true", "alter": alter}


def lies():
    p = subprocess.run(
        ["busctl", "--user", "call", "de.daimon.Focus", "/Focus",
         "de.daimon.Focus", "Zustand", "--no-pager"],
        capture_output=True, text=True, timeout=15)
    if p.returncode != 0:
        return {"ok": False, "grund": "busctl rc=%d: %s"
                % (p.returncode, p.stderr.strip()[:200])}
    return parse(p.stdout)


if __name__ == "__main__":
    if sys.argv[1] == "selbsttest":
        print(json.dumps({
            "wahr":   parse("bd true 0.5"),
            "falsch": parse("bd false 12.25"),
            "nie":    parse("bd false -1.0"),
            "muell":  parse("Call failed: irgendwas"),
            "kurz":   parse("bd true"),
        }))
    else:
        print(json.dumps(lies()))
PYEOF

ST="$("$PY" -B "$RT/lies_zustand.py" selbsttest)"
echo "  $ST"
chk "1 GEGENPROBE: 'bd true 0.5' wird als Vollbild gelesen" \
  "$(jq -r '.wahr.vollbild' <<<"$ST")" true
chk "1 GEGENPROBE: 'bd false 12.25' wird als NICHT-Vollbild gelesen" \
  "$(jq -r '.falsch.vollbild' <<<"$ST")" false
chk "1 GEGENPROBE: und die beiden kommen unterschiedlich heraus" \
  "$([[ "$(jq -r '.wahr.vollbild' <<<"$ST")" != "$(jq -r '.falsch.vollbild' <<<"$ST")" ]] && echo ja || echo nein)" ja
chk "1 GEGENPROBE: das Alter wird als Zahl gelesen" \
  "$(jq -r '.falsch.alter' <<<"$ST")" 12.25
chk "1 GEGENPROBE: 'noch nie etwas gesehen' (-1) kommt als -1 an" \
  "$(jq -r 'if .nie.alter == -1 then "ja" else "nein_\(.nie.alter)" end' <<<"$ST")" ja
chk "1 GEGENPROBE: Muell wird als FEHLER erkannt, nicht als 'false'" \
  "$(jq -r '.muell.ok' <<<"$ST")" false
chk "1 GEGENPROBE: eine abgeschnittene Antwort ebenso" \
  "$(jq -r '.kurz.ok' <<<"$ST")" false

# =============================================================================
# 2. Bestand in der lebenden Sitzung
# =============================================================================
echo
echo "--- 2. Bestand: installiert, geladen, und dieselbe Datei ---"
inst_js="$INSTALL/contents/code/main.js"
b_inst="$([[ -f "$inst_js" ]] && echo ja || echo nein)"
b_gel="$WAR_GELADEN"
b_gleich=nein
if [[ -f "$inst_js" && -f "$MAIN_JS" ]]; then
  cmp -s "$inst_js" "$MAIN_JS" && b_gleich=ja
fi
echo "  Paket installiert unter $INSTALL: $b_inst"
echo "  isScriptLoaded daimon-watcher:   $b_gel"
echo "  installierte Datei == Baum:      $b_gleich"
[[ -f "$inst_js" ]] && echo "  Pruefsummen: $(sha256sum "$inst_js" | cut -c1-16) (installiert) / $(sha256sum "$MAIN_JS" | cut -c1-16) (Baum)"
echo "  Weitere KWin-Scripte in der Sitzung: $(ls "$HOME/.local/share/kwin/scripts" 2>/dev/null | tr '\n' ' ')"
if [[ "$BESTAND_GEWERTET" == ja ]]; then
  chk "2 DIE ZUSAGE: das Script ist als Paket INSTALLIERT (nicht nur im Repo)" "$b_inst" ja
  chk "2 DIE ZUSAGE: und es ist GELADEN (isScriptLoaded, nicht 'liegt da')" "$b_gel" true
  chk "2 die installierte Datei ist byteweise die aus dem geprueften Baum" "$b_gleich" ja
else
  info "Abschnitt 2 ist bei gesetztem DAIMON_FIXTURE NICHT GEWERTET."
  info "Ein Mutantenbaum ist naturgemaess nicht in der Sitzung installiert."
  info "Wuerde das gewertet, fiele JEDER Mutant hier -- und seine eigentliche"
  info "Mutation waere nie gemessen worden (Fall 12 in HANDOVER.md)."
fi

# =============================================================================
# 3. Die Referenzsonde -- Positivkontrolle des Reizes
# =============================================================================
echo
echo "--- 3. Referenzsonde (unabhaengig vom Pruefling) ---"

cat >"$RT/ref_empfaenger.py" <<'PYEOF'
"""Nimmt entgegen, was die Referenzsonde in KWin meldet, und schreibt es weg.

Der eigene DBus-Name ist bewusst NICHT de.daimon.Focus: die Sonde darf mit dem
Pruefling nichts gemeinsam haben ausser dem Compositor, der beide bedient.

FALLE, hier gemessen: das Ergebnis von `dbus.service.BusName(...)` MUSS
festgehalten werden. Wird die Referenz fallengelassen, gibt dbus-python den
Namen sofort wieder frei -- der Prozess laeuft, der Name gehoert ihm nicht,
und jeder Aufruf scheitert mit "The name is not activatable".
"""
import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

NAME, ZIEL = sys.argv[1], sys.argv[2]


class Referenz(dbus.service.Object):
    @dbus.service.method(NAME, in_signature="s", out_signature="")
    def Event(self, zeile):
        with open(ZIEL, "a") as fh:
            fh.write(str(zeile) + "\n")
            fh.flush()


DBusGMainLoop(set_as_default=True)
_bus = dbus.SessionBus()
_halten = dbus.service.BusName(NAME, _bus)   # NICHT wegoptimieren.
Referenz(_bus, "/Referenz")
GLib.MainLoop().run()
PYEOF

REF_LOG="$RT/referenz.log"
: >"$REF_LOG"
"$PY" -B "$RT/ref_empfaenger.py" "$REF_NAME" "$REF_LOG" >"$RT/ref.err" 2>"$RT/ref.err" &
REF_PID=$!
ref_da=nein
for _ in $(seq 1 40); do
  timeout 5 busctl --user call "$REF_NAME" /Referenz "$REF_NAME" Event s handprobe >/dev/null 2>/dev/null \
    && { ref_da=ja; break; }
  sleep 0.25
done
chk "3 der Referenzempfaenger haelt seinen DBus-Namen und nimmt an" "$ref_da" ja
chk "3 und er schreibt das Angenommene auch weg (die Handprobe steht drin)" \
  "$(grep -qx handprobe "$REF_LOG" && echo ja || echo nein)" ja

cat >"$RT/referenz.js" <<JSEOF
// Referenzsonde des Reviewers. Meldet UNABHAENGIG vom Pruefling, welches
// Fenster KWin aktiviert und ob KWin es fuer Vollbild haelt.
//
// Fuenf Argumente an callDBus -- die Sonde darf nicht an derselben Grenze
// scheitern, die sie mit aufklaeren soll.
function sende(z) {
    callDBus("$REF_NAME", "/Referenz", "$REF_NAME", "Event", "" + z);
}
workspace.windowActivated.connect(function (w) {
    if (!w) { sende("aktiviert|null|(kein Fenster)"); return; }
    sende("aktiviert|" + (w.fullScreen ? "true" : "false") + "|" + w.caption);
});
sende("bereit");
JSEOF

lade_pfad "$RT/referenz.js" "$REF_SCRIPT"
chk "3 die Referenzsonde ist in KWin geladen" "$(geladen "$REF_SCRIPT")" true
ref_bereit=nein
for _ in $(seq 1 20); do
  grep -qx bereit "$REF_LOG" && { ref_bereit=ja; break; }
  sleep 0.5
done
# Das ist der HANDSCHLAG, nicht bloss eine Zusage: `isScriptLoaded` sagt schon
# `true`, wenn das Script bloss REGISTRIERT ist. Erst diese Zeile belegt, dass
# es LAEUFT und dass `callDBus` aus einem KWin-Script ueberhaupt ankommt --
# und weil der Pruefling ueber genau denselben Weg geladen wird, belegt sie
# das fuer ihn gleich mit.
chk "3 HANDSCHLAG: die Sonde LAEUFT und ihr callDBus kommt an" "$ref_bereit" ja
if [[ "$ref_bereit" != ja ]]; then
  echo
  echo "T-0.12: FEHLGESCHLAGEN — ohne die Referenzsonde ist kein Ergebnis"
  echo "        dieses Verifizierers zu deuten: ein ausbleibender Wert waere"
  echo "        nicht vom ausbleibenden REIZ zu unterscheiden."
  exit 1
fi

# =============================================================================
# Die Treiber der lebenden Messung -- geschrieben, bevor sie gebraucht werden
# =============================================================================
cat >"$RT/normalfenster.py" <<'PYEOF'
"""Ein GEWOEHNLICHES Fenster -- kein Vollbild. Fuer Kriterium 1.

Das Vollbildfenster stammt aus tests/harness/vollbildfenster.py (T-1.1,
eingefroren) und wird benutzt, nicht nachgebaut. Ein Fenster, das ausdruecklich
KEIN Vollbild ist, gibt es dort nicht -- es entsteht deshalb hier, zur Laufzeit,
und ist ausser der Groesse dasselbe.
"""
import sys
import tkinter as tk

titel, logdatei, sekunden = sys.argv[1], sys.argv[2], int(sys.argv[3])
root = tk.Tk()
root.title(titel)
root.geometry("480x320+300+240")
root.configure(bg="#303030")
root.update_idletasks()
root.update()
with open(logdatei, "a") as fh:
    fh.write("ready\n")
root.after(sekunden * 1000, root.destroy)
root.mainloop()
PYEOF

cat >"$RT/treiber.py" <<'PYEOF'
"""Misst in der LEBENDEN Sitzung. Jede Zusage mit ihrer Positivkontrolle.

Aufruf: treiber.py <RT> <harness-vollbildfenster.py> <runden>

Die Reihenfolge ist Absicht:

  RUHE       -- zwei Abrufe ohne Reiz. Waechst das Alter, hat `Zustand()` eine
                laufende Uhr. Ohne diese Zeile koennte "das Alter ist klein"
                auch von einer Konstanten kommen.
  MELDUNG    -- ein gewoehnlicher Fokuswechsel. Das Alter muss FALLEN.
                Verglichen wird vorher/nachher, nicht der Endwert.
  RUNDEN     -- Vollbild an / Vollbild aus, je mit Alter und mit dem, was die
                Referenzsonde unabhaengig gesehen hat.
"""
import json
import os
import subprocess
import sys
import time

RT, HARNESS, RUNDEN = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, RT)
from lies_zustand import lies  # noqa: E402

PY = sys.executable
REF_LOG = os.path.join(RT, "referenz.log")
ERG = {"ok": True}


def reflog():
    try:
        with open(REF_LOG) as fh:
            return [z.rstrip("\n") for z in fh]
    except OSError:
        return []


def warte_auf_ref(ab, bedingung, grenze=15.0):
    """Wartet, bis die Referenzsonde ab Zeile `ab` etwas meldet, das
    `bedingung` erfuellt. Liefert (gefunden, neue_zeilen)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < grenze:
        neu = reflog()[ab:]
        for z in neu:
            if bedingung(z):
                return True, neu
        time.sleep(0.2)
    return False, reflog()[ab:]


def starte(skript, *args):
    return subprocess.Popen([PY, "-B", skript] + [str(a) for a in args],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def warte_auf_ready(pfad, grenze=15.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < grenze:
        try:
            with open(pfad) as fh:
                if "ready" in fh.read():
                    return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# RUHE: hat `Zustand()` ueberhaupt eine laufende Uhr?
# ---------------------------------------------------------------------------
ruhe = {}
try:
    a = lies()
    time.sleep(2.5)
    b = lies()
    ruhe["erst"] = a
    ruhe["dann"] = b
    if a.get("ok") and b.get("ok"):
        ruhe["delta"] = round(b["alter"] - a["alter"], 3)
        ruhe["waechst"] = ruhe["delta"] > 1.5
        ruhe["kein_reiz_dazwischen"] = True
except Exception as exc:
    ruhe["fehler"] = "%s: %s" % (type(exc).__name__, exc)
ERG["ruhe"] = ruhe

# ---------------------------------------------------------------------------
# MELDUNG: ein gewoehnlicher Fokuswechsel setzt das Alter zurueck.
# ---------------------------------------------------------------------------
m = {}
normal_log = os.path.join(RT, "normal.log")
normal = None
try:
    vor = lies()
    m["vor"] = vor
    ab = len(reflog())
    normal = starte(os.path.join(RT, "normalfenster.py"),
                    "daimon-t012-normal", normal_log, 240)
    m["fenster_bereit"] = warte_auf_ready(normal_log)
    gesehen, neu = warte_auf_ref(
        ab, lambda z: z.startswith("aktiviert|") and "daimon-t012-normal" in z)
    m["referenz_sah_das_fenster"] = gesehen
    m["referenz_zeilen"] = neu[:8]
    time.sleep(1.5)
    nach = lies()
    m["nach"] = nach
    if vor.get("ok") and nach.get("ok"):
        m["alter_vor"] = vor["alter"]
        m["alter_nach"] = nach["alter"]
        m["alter_faellt"] = nach["alter"] < vor["alter"]
        m["alter_nicht_negativ"] = nach["alter"] >= 0
        m["alter_frisch"] = 0 <= nach["alter"] < 8.0
except Exception as exc:
    m["fehler"] = "%s: %s" % (type(exc).__name__, exc)
ERG["meldung"] = m

# ---------------------------------------------------------------------------
# RUNDEN: Vollbild an und wieder aus.
#
# Das gewoehnliche Fenster bleibt WAEHREND der Runden offen. Es ist das
# Rueckkehrziel: nach dem Schliessen des Vollbildfensters aktiviert KWin es,
# und genau daran ist die Gegenrichtung ueberhaupt messbar. Ohne ein solches
# Ziel bliebe womoeglich gar kein Fenster aktiv, es gaebe kein Ereignis, und
# "vollbild ist immer noch true" waere nicht vom Fehler zu unterscheiden.
# ---------------------------------------------------------------------------
runden = []
for i in range(RUNDEN):
    r = {"nr": i + 1}
    vb = None
    vb_log = os.path.join(RT, "vollbild-%d.log" % (i + 1))
    try:
        vor = lies()
        r["alter_vor_vollbild"] = vor.get("alter")
        ab = len(reflog())
        vb = starte(HARNESS, "#204060", vb_log, 40)
        r["fenster_bereit"] = warte_auf_ready(vb_log)
        gesehen, neu = warte_auf_ref(
            ab, lambda z: z.startswith("aktiviert|true|"))
        r["referenz_sah_vollbild"] = gesehen
        r["referenz_zeilen_an"] = neu[:8]
        time.sleep(1.5)
        an = lies()
        r["an"] = an
        r["vollbild_an"] = an.get("vollbild")
        r["alter_an"] = an.get("alter")
        r["meldung_kam_an"] = (an.get("ok") and an["alter"] >= 0
                               and an["alter"] < 8.0)

        ab = len(reflog())
        vb.terminate()
        try:
            vb.wait(timeout=10)
        except Exception:
            vb.kill()
        gesehen, neu = warte_auf_ref(
            ab, lambda z: z.startswith("aktiviert|false|"))
        r["referenz_sah_nicht_vollbild"] = gesehen
        r["referenz_zeilen_aus"] = neu[:8]
        time.sleep(1.5)
        aus = lies()
        r["aus"] = aus
        r["vollbild_aus"] = aus.get("vollbild")
        r["alter_aus"] = aus.get("alter")
        r["meldung_kam_aus"] = (aus.get("ok") and aus["alter"] >= 0
                                and aus["alter"] < 8.0)
    except Exception as exc:
        r["fehler"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if vb is not None and vb.poll() is None:
            vb.kill()
    runden.append(r)

ERG["runden"] = runden
gueltig = [r for r in runden if "fehler" not in r]
ERG["runden_gelaufen"] = len(gueltig)
ERG["alle_an_true"] = bool(gueltig) and all(r.get("vollbild_an") is True
                                            for r in gueltig)
ERG["alle_aus_false"] = bool(gueltig) and all(r.get("vollbild_aus") is False
                                              for r in gueltig)
ERG["referenz_sah_immer_vollbild"] = bool(gueltig) and all(
    r.get("referenz_sah_vollbild") for r in gueltig)
ERG["referenz_sah_immer_rueckkehr"] = bool(gueltig) and all(
    r.get("referenz_sah_nicht_vollbild") for r in gueltig)
ERG["meldungen_kamen_an"] = bool(gueltig) and all(
    r.get("meldung_kam_an") and r.get("meldung_kam_aus") for r in gueltig)

if normal is not None and normal.poll() is None:
    normal.terminate()
    try:
        normal.wait(timeout=10)
    except Exception:
        normal.kill()

print(json.dumps(ERG, default=str))
PYEOF

# Der Ein-Reiz-Lauf gegen den Bestand (Abschnitt 2b), separat und knapp.
cat >"$RT/reiz.py" <<'PYEOF'
"""Ein einziger Reiz gegen die Instanz, die in DIESER Sitzung laeuft:
ein Vollbildfenster auf, Zustand lesen, wieder zu, Zustand lesen."""
import json
import os
import subprocess
import sys
import time

RT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RT)
from lies_zustand import lies  # noqa: E402

HARNESS = sys.argv[1]
log = os.path.join(RT, "bestand-vollbild.log")
erg = {"vor": lies()}
p = subprocess.Popen([sys.executable, "-B", HARNESS, "#403020", log, "25"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
t0 = time.monotonic()
while time.monotonic() - t0 < 15:
    try:
        if "ready" in open(log).read():
            break
    except OSError:
        pass
    time.sleep(0.2)
# WARTEN, NICHT SCHLAFEN. Ein fester sleep misst die Tagesform mit: landet
# der Fokus nach dem Schliessen auf keinem Fenster, kommt keine neue Meldung,
# und der Zustand steht noch auf dem alten Wert. Am 03.08. real passiert --
# "nach dem Schliessen wieder false" war rot, obwohl der Watcher arbeitete.
# Die Frist ist damit Teil der Zusage: der Zustand muss binnen 12 s folgen.
def warte_auf(wert, grenze=12.0):
    ende = time.monotonic() + grenze
    letzte = lies()
    versuche = 1
    while time.monotonic() < ende:
        if letzte.get("vollbild") is wert:
            letzte["versuche"] = versuche
            letzte["gewartet_s"] = round(grenze - (ende - time.monotonic()), 2)
            return letzte
        time.sleep(0.25)
        letzte = lies()
        versuche += 1
    letzte["versuche"] = versuche
    letzte["frist_gerissen"] = True
    return letzte

erg["mit_vollbild"] = warte_auf(True)
p.terminate()
try:
    p.wait(timeout=10)
except Exception:
    p.kill()
erg["ohne_vollbild"] = warte_auf(False)
print(json.dumps(erg, default=str))
PYEOF

# =============================================================================
# 4. Der Pruefling -- aus dem geprueften Baum, ohne Paket-Cache
# =============================================================================
echo
echo "--- 4. Der Pruefling wird geladen (aus dem geprueften Baum) ---"
echo "  Datei: $MAIN_JS"
echo "  sha256: $(sha256sum "$MAIN_JS" | cut -d' ' -f1)"

# Der installierte Watcher wuerde neben dem Pruefling weiter melden und einen
# Mutanten zudecken. Er wird entladen und in aufraeumen() wiederhergestellt.
#
# ZUVOR aber der einzige Reiz, der die SITZUNG misst und nicht die Datei:
# Abschnitt 2b. Danach ist der Bestand weg und die Frage nicht mehr zu stellen.
if [[ "$WAR_GELADEN" == true ]]; then
  if [[ "$BESTAND_GEWERTET" == ja ]]; then
    echo "  2b: ein Reiz gegen die Instanz, die in DIESER Sitzung laeuft ..."
    timeout --foreground --signal=TERM --kill-after=10s 90s \
      "$PY" -B "$RT/reiz.py" "$HARNESS" >"$RT/bestand.json" 2>"$RT/reiz.log"
  fi
  bestand_stilllegen
  echo "  daimon-watcher entladen UND in kwinrc stillgelegt"
  echo "  (Wiederherstellung in aufraeumen(); ohne das Stilllegen holt ihn das"
  echo "   naechste reconfigure zurueck -- am 03.08. gemessen)"
fi
chk "4 waehrend der Messung laeuft KEIN zweiter Watcher mit" \
  "$(geladen daimon-watcher)" false

lade_pfad "$MAIN_JS" "$PRUEF_SCRIPT"
chk "4 der Pruefling ist geladen" "$(geladen "$PRUEF_SCRIPT")" true
# Fall 12 in HANDOVER.md: eine Positivkontrolle, die nie gruen werden kann,
# meldet jeden Mutanten als "erkannt", ohne dessen Mutation zu messen.
chk "4 POSITIVKONTROLLE: die geladene Datei liegt IM GEPRUEFTEN BAUM" \
  "$([[ "$MAIN_JS" == "$TARGET"/* ]] && echo ja || echo "ausserhalb_$MAIN_JS")" ja

if [[ "$(geladen "$PRUEF_SCRIPT")" != true ]]; then
  echo
  echo "T-0.12: FEHLGESCHLAGEN — der Pruefling laesst sich nicht laden,"
  echo "        also ist ueber sein Melden nichts gemessen."
  exit 1
fi

# =============================================================================
# 5./6. Die lebende Messung
# =============================================================================
echo
echo "--- 5./6. Lebende Messung (Fenster gehen auf und zu) ---"
JOURNAL_AB="$(date '+%Y-%m-%d %H:%M:%S')"
sleep 1
timeout --foreground --signal=TERM --kill-after=10s "${MAX_SECS}s" \
  "$PY" -B "$RT/treiber.py" "$RT" "$HARNESS" "$RUNDEN" >"$RT/mess.json" 2>"$RT/treiber.log"
  # NACH der Messung: ist der Bestand zurueckgekommen? Dann hat womoeglich er
  # gemeldet und nicht der Pruefling, und alles Folgende waere eine Aussage
  # ueber die falsche Datei.
  bestand_danach="$(geladen daimon-watcher)"
  chk "4 der Bestand ist waehrend der ganzen Messung weggeblieben" \
    "$bestand_danach" false
  if [[ "$bestand_danach" == true ]]; then
    echo "  ACHTUNG: daimon-watcher war nach der Messung wieder geladen."
    echo "  Die Runden unten sind damit NICHT GEMESSEN -- moeglicherweise hat"
    echo "  der Bestand gemeldet und den Pruefling ueberstimmt."
  fi
JOURNAL_BIS="$(date '+%Y-%m-%d %H:%M:%S')"

if ! jq -e . >/dev/null 2>/dev/null <"$RT/mess.json"; then
  laut "Der Treiber hat keine auswertbare Antwort geliefert."
  laut "Protokoll:"; tail -30 "$RT/treiber.log"
  chk "5 der Treiber laeuft" nein ja
  echo; echo "T-0.12: FEHLGESCHLAGEN"; exit 1
fi
M="$(cat "$RT/mess.json")"

echo
echo "  --- 5. Eine Meldung kommt ueberhaupt an ---"
RU="$(jq -c '.ruhe' <<<"$M")"
echo "  Ruhe: $RU"
chk "5 Zustand() ist ueberhaupt abrufbar" "$(jq -r '.ruhe.erst.ok' <<<"$M")" true
chk "5 GEGENPROBE: ohne Reiz WAECHST das gemeldete Alter (die Uhr laeuft)" \
  "$(jqb "$RU" waechst)" true
echo "  gewachsen um $(jq -r '.delta // "?"' <<<"$RU") s in 2,5 s"

ME="$(jq -c '.meldung' <<<"$M")"
if [[ "$(jq -r 'has("fehler")' <<<"$ME")" == true ]]; then
  laut "$(jq -r '.fehler' <<<"$ME")"
  chk "5 der Fokuswechsel ist messbar" nein ja
else
  echo "  Alter vor dem Fokuswechsel:  $(jq -r '.alter_vor // "?"' <<<"$ME") s"
  echo "  Alter nach dem Fokuswechsel: $(jq -r '.alter_nach // "?"' <<<"$ME") s"
  echo "  Referenzsonde: $(jq -c '.referenz_zeilen' <<<"$ME")"
  chk "5 das Testfenster ist ueberhaupt aufgegangen" \
    "$(jqb "$ME" fenster_bereit)" true
  # Die Positivkontrolle des REIZES. Ohne sie waere "das Alter faellt nicht"
  # nicht von "es gab gar keinen Fokuswechsel" zu unterscheiden.
  chk "5 POSITIVKONTROLLE: die Referenzsonde hat den Fokuswechsel gesehen" \
    "$(jqb "$ME" referenz_sah_das_fenster)" true
  chk "5 DIE ZUSAGE: nach dem Fokuswechsel ist das Alter ZURUECKGESETZT" \
    "$(jqb "$ME" alter_faellt)" true
  chk "5 DIE ZUSAGE: und es ist >= 0 (schon einmal etwas gesehen)" \
    "$(jqb "$ME" alter_nicht_negativ)" true
  chk "5 und zwar frisch, nicht irgendwann (unter 8 s)" \
    "$(jqb "$ME" alter_frisch)" true
fi

echo
echo "  --- 6. Vollbild in BEIDE Richtungen ($RUNDEN Runden) ---"
jq -r '.runden[] | "  Runde \(.nr): an=\(.vollbild_an) (Alter \(.alter_an), Referenz sah Vollbild: \(.referenz_sah_vollbild)) -> aus=\(.vollbild_aus) (Alter \(.alter_aus), Referenz sah Rueckkehr: \(.referenz_sah_nicht_vollbild))"' <<<"$M"
jq -r '.runden[] | select(.fehler) | "  !! Runde \(.nr): \(.fehler)"' <<<"$M"
chk "6 alle $RUNDEN Runden sind ohne Ausnahme gelaufen" \
  "$(jq -r '.runden_gelaufen' <<<"$M")" "$RUNDEN"
# Positivkontrolle des Reizes, wieder daneben: KWin selbst muss das Fenster
# fuer Vollbild gehalten haben. Sonst prueft der Rest ein Fenster, das keins
# war -- und ein korrekter Watcher faellt fuer nichts.
chk "6 POSITIVKONTROLLE: KWin hielt das Testfenster jedes Mal fuer VOLLBILD" \
  "$(jqb "$M" referenz_sah_immer_vollbild)" true
chk "6 POSITIVKONTROLLE: und nach dem Schliessen jedes Mal ein Fenster OHNE" \
  "$(jqb "$M" referenz_sah_immer_rueckkehr)" true
chk "6 DIE ZUSAGE: bei echtem Vollbild meldet Zustand() true" \
  "$(jqb "$M" alle_an_true)" true
chk "6 DIE GEGENRICHTUNG: nach dem Schliessen wieder false" \
  "$(jqb "$M" alle_aus_false)" true
# Und die Trennung, ohne die "false" nichts heisst: an BEIDEN Zeitpunkten muss
# eine Meldung angekommen sein. Ein Watcher, der nach dem ersten Ereignis
# verstummt, liefert sonst dauerhaft den letzten Wert und besteht die
# Gegenrichtung durch Nichtstun.
chk "6 an BEIDEN Zeitpunkten ist eine frische Meldung angekommen" \
  "$(jqb "$M" meldungen_kamen_an)" true

# =============================================================================
# 2b. Auswertung: hat der BESTAND dieser Sitzung gemeldet?
# =============================================================================
echo
echo "--- 2b. Der Bestand dieser Sitzung (vor jedem Eingriff gemessen) ---"
if [[ "$BESTAND_GEWERTET" != ja ]]; then
  info "nicht gemessen: DAIMON_FIXTURE ist gesetzt, der Bestand gehoert nicht"
  info "zum geprueften Baum."
elif [[ "$b_gel" != true ]]; then
  laut "Der Bestand war GAR NICHT GELADEN -- Abschnitt 2 hat das bereits als"
  laut "FAIL gewertet. Ein Reizlauf dagegen waere sinnlos."
elif [[ ! -s "$RT/bestand.json" ]]; then
  info "kein Reizlauf gegen den Bestand vorhanden (siehe OFFEN (5))."
else
  B="$(cat "$RT/bestand.json")"
  echo "  $B"
  chk "2b DIE ZUSAGE: der WATCHER DIESER SITZUNG meldet Vollbild als true" \
    "$(jq -r '.mit_vollbild.vollbild' <<<"$B")" true
  chk "2b und nach dem Schliessen wieder false" \
    "$(jq -r '.ohne_vollbild.vollbild' <<<"$B")" false
  chk "2b und das Alter ist dabei frisch, nicht -1" \
    "$(jq -r 'if (.mit_vollbild.alter // -1) >= 0 and (.mit_vollbild.alter // 99) < 20 then "ja" else "nein" end' <<<"$B")" ja
fi

# =============================================================================
# 7. Die Argumentgrenze
# =============================================================================
echo
echo "--- 7. Die Argumentgrenze (Verhalten + Protokoll, mit Positivkontrolle) ---"
journal() {  # $1 = von, $2 = bis
  timeout 20 journalctl --user --identifier kwin_wayland \
    --since "$1" --until "$2" --no-pager -o cat 2>/dev/null
}
journal "$JOURNAL_AB" "$JOURNAL_BIS" >"$RT/journal-pruefling.txt"
zuviel_pruefling="$(grep -c 'Too many arguments' "$RT/journal-pruefling.txt")"
echo "  Messfenster des Prueflings: $JOURNAL_AB .. $JOURNAL_BIS"
echo "  Zeilen im KWin-Protokoll:   $(wc -l <"$RT/journal-pruefling.txt")"
echo "  davon 'Too many arguments':  $zuviel_pruefling"
[[ "$zuviel_pruefling" -gt 0 ]] && grep -n 'Too many arguments' "$RT/journal-pruefling.txt" | head -5

# Die Positivkontrolle: eine eigene Sonde ueberschreitet die Grenze absichtlich.
# Ohne sie waere "0 Treffer" oben die Nullaussage -- ein Protokoll, das der
# Verifizierer gar nicht lesen kann, sieht genauso aus.
cat >"$RT/argsonde.js" <<JSEOF
// POSITIVKONTROLLE der Protokollpruefung. 15 Argumente an callDBus -- genau
// die Form, die T-0.12 wochenlang lautlos hat scheitern lassen. Das Ziel ist
// ein Name, den es nicht gibt: die Sonde soll die GRENZE ausloesen, nicht
// irgendetwas erreichen.
function zuviel() {
    callDBus("$REF_NAME.Nirgends", "/Nirgends", "$REF_NAME.Nirgends", "Event",
             "a", "b", "c", "d", "e", true, 1, 2, 3, 4, 5);
}
zuviel();
workspace.windowActivated.connect(function (w) { zuviel(); });
JSEOF
ARG_AB="$(date '+%Y-%m-%d %H:%M:%S')"
sleep 1
lade_pfad "$RT/argsonde.js" "$ARG_SCRIPT"
chk "7 die Argument-Positivkontrolle ist geladen" "$(geladen "$ARG_SCRIPT")" true
# Ein Fenster auf und zu, damit der Handler sicher feuert.
"$PY" -B "$RT/normalfenster.py" daimon-t012-argreiz "$RT/argreiz.log" 4 >/dev/null 2>/dev/null
sleep 3
ARG_BIS="$(date '+%Y-%m-%d %H:%M:%S')"
journal "$ARG_AB" "$ARG_BIS" >"$RT/journal-argsonde.txt"
zuviel_sonde="$(grep -c 'Too many arguments' "$RT/journal-argsonde.txt")"
echo "  Messfenster der Sonde:      $ARG_AB .. $ARG_BIS"
echo "  davon 'Too many arguments':  $zuviel_sonde"
grep -m2 'Too many arguments' "$RT/journal-argsonde.txt" | sed 's/^/    /'
entlade "$ARG_SCRIPT"

chk "7 POSITIVKONTROLLE: die Sonde mit 15 Argumenten LOEST die Zeile aus" \
  "$([[ "$zuviel_sonde" -ge 1 ]] && echo ja || echo nein)" ja
chk "7 DIE ZUSAGE: im Messfenster des Prueflings steht sie NICHT" \
  "$zuviel_pruefling" 0

# Der Quelltextzaehler ist GEMELDET, NICHT GEWERTET. Ein Verifizierer, der
# Quelltext per grep prueft, ist an der Schreibweise zu umgehen (HANDOVER.md);
# gewertet ist oben das Verhalten und die Protokollzeile.
argzahl="$("$PY" -B - "$MAIN_JS" <<'PYEOF' 2>/dev/null
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
# Zeilenkommentare weg -- sonst zaehlt der Modulkopf mit.
text = re.sub(r"//[^\n]*", "", text)
aus = []
for m in re.finditer(r"callDBus\s*\(", text):
    i = m.end()
    tiefe, arg, in_str, esc, quote = 1, 1, False, False, ""
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
print(",".join(str(a) for a in aus) if aus else "keiner")
PYEOF
)"
info "callDBus-Aufrufe im Quelltext, Argumentzahl je Aufruf: ${argzahl:-nicht ermittelbar}"
info "(gemeldet, NICHT gewertet -- ein grep ueber Quelltext ist an der"
info " Schreibweise zu umgehen; gewertet sind Verhalten und Protokollzeile)"

# =============================================================================
# 8. Die Evidenz aus Spike T-1.9 -- verlangt, nicht nachgespielt
# =============================================================================
echo
echo "--- 8. Trefferquote und kwin --replace (Evidenz aus Spike T-1.9) ---"
chk "8 Evidenz aus Spike T-1.9 liegt vor" \
  "$([[ -f "$EVIDENZ" ]] && echo ja || echo nein)" ja
if [[ -f "$EVIDENZ" ]]; then
  echo "  $(jq -c . "$EVIDENZ")"
  chk "8 keine Auslassung bei 50 Wechseln" "$(jq -r '.missed' "$EVIDENZ")" 0
  chk "8 p95-Latenz unter 200 ms" \
    "$(jq -r 'if .p95_latency_ms < 200 then "ja" else "nein" end' "$EVIDENZ")" ja
  chk "8 ueberlebt kwin --replace" "$(jq -r '.survives_replace' "$EVIDENZ")" true
  chk "8 in der echten Sitzung belegt, nicht nur verschachtelt" \
    "$(jq -r '.survives_replace_evidence // "fehlt"' "$EVIDENZ")" "echte Sitzung"
fi

# =============================================================================
# OFFEN, und zwar benannt
# =============================================================================
echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) DIE TREFFERQUOTE UEBER 50 WECHSEL ist hier NICHT nachgemessen."
echo "      Abschnitt 6 fuehrt $RUNDEN Runden. Die 50 aus dem Plan stehen als"
echo "      Evidenz aus Spike T-1.9 und werden in Abschnitt 8 VERLANGT."
echo "      Zwischen 'meldet ueberhaupt' (hier belegt) und 'meldet in 50 von"
echo "      50 Faellen' liegt eine Groesse, die dieser Verifizierer nicht misst."
echo "  (2) 'kwin --replace' WIRD NICHT AUSGEFUEHRT. Es ist auf Plasma 6 sicher"
echo "      (HANDOVER.md), aber ein Verifizierer, der den Compositor der"
echo "      laufenden Sitzung ersetzt, wird nicht ausgefuehrt und ist damit"
echo "      wertlos. Belegt ist es ueber die Evidenz aus T-1.9."
echo "  (3) DIE GELADENE INSTANZ UND DIE INSTALLIERTE DATEI. Abschnitt 2 zeigt,"
echo "      dass die installierte Datei die des Baums ist, und dass ein Script"
echo "      dieses Namens geladen ist. NICHT gezeigt: dass die geladene"
echo "      Instanz aus genau dieser Datei stammt -- wer sie vor einer"
echo "      Aenderung geladen hat, laeuft weiter mit dem alten Text, und"
echo "      isScriptLoaded sieht das nicht. Abschnitt 2b misst deshalb das"
echo "      VERHALTEN des Bestandes, nicht seinen Dateinamen."
echo "  (4) DER PRUEFLING WIRD PER PFAD GELADEN, nicht ueber den Paketnamen."
echo "      Das ist noetig (KWin liest ein unter demselben Namen getauschtes"
echo "      main.js nicht zuverlaessig neu), heisst aber: Abschnitt 5/6 misst"
echo "      die DATEI, Abschnitt 2/2b misst die SITZUNG. Erst beide zusammen"
echo "      sagen etwas ueber den Watcher, der bei Matthias laeuft."
echo "  (5) DER REIZLAUF GEGEN DEN BESTAND (2b) LAEUFT NUR, wenn der Watcher"
echo "      vorher geladen war. War er es nicht, ist das der Befund selbst --"
echo "      genau der, der bei T-0.12 wochenlang unentdeckt blieb."
echo "  (6) DIE JSON-FORM DER MELDUNG IST NICHT GEPRUEFT. Der Verifizierer"
echo "      misst, DASS eine Meldung ankommt und WAS sie ueber Vollbild sagt."
echo "      Ob sie als ein JSON-String oder als elf Werte kommt, ist ihm"
echo "      gleich -- die Argumentgrenze wird am Verhalten und an der"
echo "      Protokollzeile gemessen, nicht an der gewaehlten Bauform. Wer die"
echo "      Felder auf neun kuerzt, besteht diesen Verifizierer und hat die"
echo "      Grenze nur verschoben. Das ist eine Entscheidung des Gegenlesens,"
echo "      keine Messung."
echo "  (7) DIE FENSTER GEHEN IN DER ECHTEN SITZUNG AUF. Wer waehrend des Laufs"
echo "      tippt, aendert den Fokus und damit die Messung. Die Runden sind"
echo "      deshalb einzeln ausgewiesen; eine einzelne gestoerte Runde ist an"
echo "      der Referenzsonde zu erkennen und nicht am Pruefling."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-0.12: gruen. Das Script ist in dieser Sitzung installiert und geladen,"
  echo "        ein gewoehnlicher Fokuswechsel setzt das gemeldete Alter"
  echo "        zurueck (es kommt also ueberhaupt etwas an), ein echtes"
  echo "        Vollbildfenster wird als Vollbild gemeldet und nach dem"
  echo "        Schliessen wieder als keines -- beide Richtungen, je mit"
  echo "        frischer Meldung --, und im KWin-Protokoll steht waehrend der"
  echo "        Messung keine Zeile 'Too many arguments', obwohl der"
  echo "        Verifizierer gezeigt hat, dass er sie sehen wuerde."
else
  echo "T-0.12: FEHLGESCHLAGEN"
fi
exit $fail
