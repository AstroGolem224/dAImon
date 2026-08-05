#!/usr/bin/env bash
# Erzeugt die fuenf T-3.9-Mutanten reproduzierbar -- und das Gut-Muster unter
# tests/fixtures/known-good/T-3.9/.
#
# BASIS IST DER ABGENOMMENE, EINGEFRORENE STAND (Commit daffca6), NICHT der
# Arbeitsbaum. Die T-3.9-Implementierung des Builders ist abgenommen; damit
# ist das fruehere Vorgehen (Basis-Commit OHNE die Task-Dateien plus blind
# geschriebene Referenz des Reviewers) abgeloest -- der Kopf des alten
# Skripts hat genau diesen Schritt fuer die Abnahme angekuendigt ("Vorgehen
# wie bei T-2.7"). Gut-Muster und Mutanten sind jetzt Vollkopien des
# abgenommenen Stands, jeder Mutant traegt danach genau eine Mutation als
# verankerte Ersetzung in den Builder-Dateien.
#
# Weil der Basis-Commit die Task-Dateien jetzt ENTHALTEN MUSS, ist die alte
# Abbruch-Bedingung invertiert: ein Basis-Commit ohne sprechtext.py, tts.py
# oder die daimon-tts-Units ist der falsche (zu alte) Stand -- Abbruch.
#
# Die Baeume tragen damit auch die aktuelle `daimon/face/tts.py` SAMT der
# zwei Netze gegen verwaiste Dienste (`PR_SET_PDEATHSIG` /
# `sterbe_mit_elternteil()` und `HubWaechter`). Darauf wird unten explizit
# geprueft -- eine eigene Kopie der tts.py ohne diese Netze war der Anlass
# fuer diesen Umbau.
#
# Zwei Fallen aus der Projekthistorie bleiben abgesichert:
#   * `git archive` traegt weder target/ noch __pycache__ mit; eine Kopie mit
#     Artefakten der unmutierten Quelle hat schon einmal einen Mutanten gruen
#     gemeldet.
#   * Am Ende wird geprueft, dass jeder Baum sich vom Basisbaum nur in den
#     vorgesehenen Dateien unterscheidet, dass sich die Baeume VONEINANDER
#     unterscheiden und dass alles kompiliert. Ein abgebrochenes
#     Erzeugungsskript hat schon einmal eine unveraenderte Kopie hinterlassen.
#
# Aufruf:
#   tests/mutants/T-3.9/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.9"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

# daffca6 = der abgenommene, eingefrorene T-3.9-Stand. Enthaelt den
# Sprechtext-Validator im Hub (`tts_anfrage`, Art `freigabe`/`beginnt`/
# `gesprochen`), die persistierte Abkuehlung (Boot-ID, zwei Uhren), den
# socket-aktivierten Dienst mit beiden Netzen gegen Verwaisung und
# `set_voice` im State.
BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-daffca6adca901855f269d65c0b566da94f4802a}"
git -C "$REPO" archive "$BASIS_COMMIT" daimon config | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }

# INVERTIERTE Abbruch-Bedingung (frueher: Abbruch, WENN der Basis-Commit die
# Dateien enthielt): der Basis-Commit MUSS der abgenommene T-3.9-Stand sein.
for pflicht in daimon/hub/sprechtext.py daimon/hub/abkuehlung.py \
               daimon/face/tts.py daimon/hub/daemon.py daimon/hub/state.py \
               config/systemd/daimon-tts.service config/systemd/daimon-tts.socket; do
  [[ -f "$BASIS/$pflicht" ]] \
    || { echo "FEHLER: der Basis-Commit enthaelt $pflicht nicht."
         echo "Das ist nicht der abgenommene T-3.9-Stand -- Basis neu pinnen."; exit 1; }
done
grep -q "def tts_anfrage" "$BASIS/daimon/hub/daemon.py" \
  || { echo "FEHLER: daemon.py ohne tts_anfrage -- falscher Stand"; exit 1; }
grep -q "def set_voice" "$BASIS/daimon/hub/state.py" \
  || { echo "FEHLER: state.py ohne set_voice -- falscher Stand"; exit 1; }
# Der Anlass dieses Umbaus: die Netze gegen verwaiste Dienste muessen im
# Basis-Stand sein, sonst tragen alle Baeume eine tts.py ohne sie.
grep -q "sterbe_mit_elternteil" "$BASIS/daimon/face/tts.py" \
  || { echo "FEHLER: tts.py ohne sterbe_mit_elternteil (PR_SET_PDEATHSIG-Netz)"; exit 1; }
grep -q "HubWaechter" "$BASIS/daimon/face/tts.py" \
  || { echo "FEHLER: tts.py ohne HubWaechter"; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" "$GUT" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis, gut = sys.argv[1], sys.argv[2], sys.argv[3]

def ersetzen(text, alt, neu, wo):
    if text.count(alt) != 1:
        raise SystemExit(f"Anker nicht genau einmal gefunden in {wo}: "
                         f"{alt[:70]!r} ({text.count(alt)}x)")
    return text.replace(alt, neu)

def lies(pfad, rel):
    with open(os.path.join(pfad, rel), encoding="utf-8") as f:
        return f.read()

def schreibe(pfad, rel, text):
    ziel = os.path.join(pfad, rel)
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    with open(ziel, "w", encoding="utf-8") as f:
        f.write(text)

def baum(pfad):
    """Eine VOLLSTAENDIGE Kopie des abgenommenen Stands. Der Verifizierer
    startet Hub und Dienst aus dem Baum (PYTHONPATH) -- ein Teilbaum waere
    nicht lauffaehig."""
    for unter in ("daimon", "config"):
        z = os.path.join(pfad, unter)
        if os.path.exists(z):
            shutil.rmtree(z)
    os.makedirs(pfad, exist_ok=True)
    shutil.copytree(os.path.join(basis, "daimon"), os.path.join(pfad, "daimon"))
    shutil.copytree(os.path.join(basis, "config"), os.path.join(pfad, "config"))

# ===========================================================================
# Die fuenf Mutationen. Jede ist eine verankerte Ersetzung in den
# BUILDER-Dateien -- nichts wird mehr blind geschrieben.
# ===========================================================================

def mut_bidi(pfad):
    """Steuerzeichen/Bidi/Nullbreite werden ESCAPT statt ENTFERNT."""
    rel = "daimon/hub/sprechtext.py"
    t = lies(pfad, rel)
    t = ersetzen(t,
        '    return _UNSICHTBAR.sub("", unicodedata.normalize("NFC", text))\n',
        '    # MUTANT: escapt statt entfernt -- das ist die Behandlung von\n'
        '    # `wert_saeubern` (der Bubble-Sanitizer aus T-1.7, dort fuer die\n'
        '    # Anzeige richtig und eingefroren). Vorgelesen ist sie Unsinn;\n'
        '    # Design §8.3 verlangt fuer die Stimme das ENTFERNEN.\n'
        '    return _UNSICHTBAR.sub(lambda m: "\\\\u%04X" % ord(m.group(0)),\n'
        '                           unicodedata.normalize("NFC", text))\n',
        rel)
    schreibe(pfad, rel, t)

def mut_ungefragt(pfad):
    """Der Kanal `ungefragt` lehnt freien Text nicht mehr ab -- er laeuft
    durch die normalen Regeln. Die kuratierten Vorlagen (`aus_vorlage`)
    bleiben unangetastet (Positivkontrolle des Verifizierers)."""
    rel = "daimon/hub/sprechtext.py"
    t = lies(pfad, rel)
    t = ersetzen(t,
        '    if kanal == "ungefragt":\n'
        '        return Urteil(False, grund=GRUND_FREIER_TEXT, ersatz=ersatz)\n',
        '    # MUTANT: der Kanal `ungefragt` nimmt freien Text. Er laeuft\n'
        '    # durch dieselben Regeln wie eine Antwort -- aber Kriterium 5\n'
        '    # verlangt: ungefragt zieht NUR aus kuratierten Vorlagen, freier\n'
        '    # Text wird abgelehnt, nicht gesaeubert.\n',
        rel)
    schreibe(pfad, rel, t)

def mut_persistenz(pfad):
    """Die Abkuehlung wird weder geladen noch geschrieben. Die Methoden
    bleiben definiert (der Mutant soll an der Messung fallen, nicht am
    Aufruf); die Fristen wirken im laufenden Prozess weiter."""
    rel = "daimon/hub/abkuehlung.py"
    t = lies(pfad, rel)
    t = ersetzen(t,
        "        self._laden()\n",
        "        # MUTANT: der Bestand wird nicht geladen -- die Abkuehlung\n"
        "        # lebt nur im Speicher und ueberlebt keinen Neustart\n"
        "        # (Kriterium 8).\n",
        rel)
    t = ersetzen(t,
        "            self._schreiben()\n",
        "            # MUTANT: nichts geht auf die Platte.\n",
        rel)
    schreibe(pfad, rel, t)

def mut_unterbrechung(pfad):
    """`abbrechen()` wartet auf das Ende des laufenden pw-cat statt ihn zu
    toeten. Im Builder-Code steht `p.kill(); p.wait(timeout=2)` -- der Kill
    wird durch Warten ersetzt, und es bleibt kein zweites `kill` auf dem
    Unterbrechungspfad, das die Unterbrechung retten koennte."""
    rel = "daimon/face/tts.py"
    t = lies(pfad, rel)
    t = ersetzen(t,
        "            p.kill()\n"
        "            p.wait(timeout=2)\n",
        "            # MUTANT: die neue Aeusserung reiht sich ein statt\n"
        "            # abzubrechen -- sie wartet auf das Ende der alten\n"
        "            # Wiedergabe. Kriterium 4 verlangt den Abbruch binnen\n"
        "            # 100 ms, gemessen am Ende des alten pw-cat-Prozesses.\n"
        "            p.wait()\n",
        rel)
    schreibe(pfad, rel, t)

def mut_validator_dienst(pfad):
    """ZWEI Dateien: der Hub gibt fuer jeden Text ungeprueft eine Marke aus
    (der Validator-Aufruf entfaellt), und der Dienst prueft selbst. Vorlagen
    laufen im Hub weiter durch `aus_vorlage`; harmlose Texte werden gesprochen."""
    rel = "daimon/hub/daemon.py"
    t = lies(pfad, rel)
    t = ersetzen(t,
        "        else:\n"
        "            urteil = sprechtext.pruefe(anfrage.get(\"text\"), kanal=kanal)\n",
        "        else:\n"
        "            # MUTANT: der Hub prueft NICHT. Der Text wird ungesehen\n"
        "            # durchgereicht und eine Marke ausgegeben; die Pruefung\n"
        "            # passiert erst im Dienst. Design §8.3: der Validator sitzt\n"
        "            # im Hub -- sonst ist er umgehbar, sobald ein anderer\n"
        "            # Produzent Text an die Ausgabe schickt.\n"
        "            urteil = sprechtext.Urteil(True, text=str(anfrage.get(\"text\", \"\")))\n",
        rel)
    schreibe(pfad, rel, t)

    rel = "daimon/face/tts.py"
    t = lies(pfad, rel)
    t = ersetzen(t,
        "from daimon.common.logging import Logger, get_logger\n",
        "from daimon.common.logging import Logger, get_logger\n"
        "from daimon.hub import sprechtext   # MUTANT: der Dienst prueft selbst\n",
        rel)
    t = ersetzen(t,
        "        if not satz or not marke:\n"
        "            return {\"v\": 1, \"ok\": False, \"grund\": \"freigabe_unvollstaendig\"}\n"
        "        return self._ausgeben(satz, kanal=kanal, marke=marke)\n",
        "        if not satz or not marke:\n"
        "            return {\"v\": 1, \"ok\": False, \"grund\": \"freigabe_unvollstaendig\"}\n"
        "        # MUTANT: die Pruefung passiert HIER statt im Hub. Eine Marke\n"
        "        # sagt damit nichts mehr ueber den Inhalt -- der Hub hat sie\n"
        "        # ungesehen ausgegeben.\n"
        "        urteil = sprechtext.pruefe(satz, kanal=kanal)\n"
        "        if not urteil.ok:\n"
        "            return {\"v\": 1, \"ok\": False, \"grund\": urteil.grund,\n"
        "                    \"gesprochen\": False, **self.kennung()}\n"
        "        satz = urteil.text\n"
        "        return self._ausgeben(satz, kanal=kanal, marke=marke)\n",
        rel)
    schreibe(pfad, rel, t)

# ===========================================================================
# Das Gut-Muster: der abgenommene Stand, unveraendert.
# ===========================================================================
baum(gut)
schreibe(gut, "HERKUNFT.txt", """GUT-MUSTER fuer tests/verify/T-3.9.sh -- der ABGENOMMENE Stand.

Dieser Baum ist eine Vollkopie des abgenommenen, eingefrorenen
T-3.9-Stands:

    git archive daffca6adca901855f269d65c0b566da94f4802a daimon config

Er ist KEINE blind geschriebene Referenz des Reviewers mehr -- die ist mit
der Abnahme der Implementierung abgeloest worden (genau dieser Schritt
stand im Kopf des alten erzeugen.sh; Vorgehen wie bei T-2.7). Der Baum
traegt damit auch die aktuelle `daimon/face/tts.py` samt beider Netze
gegen verwaiste Dienste (`PR_SET_PDEATHSIG` / `sterbe_mit_elternteil()`
und `HubWaechter`).

Er beweist, dass der Verifizierer gruen werden KANN (Fall 12 in
HANDOVER.md: ein Verifizierer, der nie gruen wird, meldet jeden Mutanten
als "erkannt", ohne dessen Mutation je gemessen zu haben).

Erzeugt von tests/mutants/T-3.9/erzeugen.sh -- nicht von Hand editieren,
sondern dort aendern und neu erzeugen.
""")

# ===========================================================================
# Die fuenf Mutanten. Jeder entfernt genau eine Zusage.
# ===========================================================================
MUTANTEN = {
    "validator-im-dienst": (mut_validator_dienst, """MUTANT: die Pruefung passiert im TTS-DIENST statt im Hub.

Der Hub gibt fuer JEDEN freien Text eine Marke aus, ohne ihn zu pruefen
(`sprechtext.pruefe` entfaellt in `tts_anfrage`); der Dienst prueft selbst
(importiert `daimon.hub.sprechtext` und lehnt verletzte Texte ab). Die
Vorlagen laufen im Hub weiter durch `aus_vorlage`, und harmlose Texte
werden gesprochen -- der Mutant startet und funktioniert aeusserlich
normal. Damit ist der Validator aber umgehbar: ein anderer Produzent, der
eine Marke bekommt (oder die Pruefung im Dienst anders auslegt), schiebt
ungeprueften Text an die Ausgabe -- genau der Satz aus Design §8.3, der
die Lage des Validators begruendet.

Sichtbar wird das daran, dass der HUB fuer einen Angriffstext `ok: true`
plus Marke meldet. Die Zusage "ein Text ohne Hub-Freigabe wird nicht
gesprochen" haelt er aeusserlich noch -- die Marke ist ja weiterhin
noetig. Gebrochen ist: die Freigabe bedeutet nichts mehr.

Gefangen von: den Hub-Pruefungen der zehn Angriffstexte (Kriterien 6 und
12) -- der Hub muss jeden einzelnen selbst ablehnen, mit dem Grund der
verletzten Regel, nicht mit einer Marke.
"""),
    "abkuehlung-nicht-persistiert": (mut_persistenz, """MUTANT: die Abkuehlung lebt nur im Speicher.

`Abkuehlung` laedt den Bestand nicht (`self._laden()` entfaellt im
Konstruktor) und schreibt ihn nicht (`self._schreiben()` entfaellt in
`vermerke`). Alles andere stimmt: die Fristen (20/10/3 s) wirken im
laufenden Prozess, `darf()` und `vermerke()` sind unveraendert, die
Datei-Machinerie samt Boot-ID-Logik ist sogar noch da -- sie wird nur nie
gerufen. Ein Verifizierer, der die Abkuehlung nur im laufenden Prozess
prueft, ist bei ihm vollstaendig gruen.

Gefangen von: dem Neustart der Hubs mitten im Abkuehlungsfenster
(Kriterium 8) -- nach dem Neustart muss dieselbe Anfrage weiter abgelehnt
werden -- und davon, dass nach einer Wiedergabe eine Abkuehlungsdatei im
Zustandsverzeichnis liegt.
"""),
    "unterbrechung-wartet": (mut_unterbrechung, """MUTANT: die neue Aeusserung reiht sich an statt abzubrechen.

In `abbrechen()` wartet der Dienst auf das Ende des laufenden
pw-cat-Prozesses (`p.wait()`) statt ihn zu toeten (`p.kill()`). Kein
zweites `kill` auf dem Unterbrechungspfad rettet die Unterbrechung. Kein
Mischen zweier Stimmen -- aber auch keine Unterbrechung: die neue
Aeusserung kommt erst, wenn die alte zuende ist. Kriterium 4 verlangt den
Abbruch binnen 100 ms, gemessen am Ende des alten pw-cat-Prozesses.

Gefangen von: der Unterbrechungsmessung -- die alte Wiedergabe muss
binnen 100 ms nach der neuen Anfrage tot sein. Bei ihm dauert es den Rest
der alten Wiedergabe (Sekunden).
"""),
    "bidi-escapt-statt-entfernt": (mut_bidi, """MUTANT: Steuerzeichen werden ESCAPT statt ENTFERNT.

`unsichtbares_entfernen()` benutzt die Behandlung von `wert_saeubern`
(der Bubble-Sanitizer aus T-1.7): aus einem Bidi-Override wird die
Zeichenkette `\\u202E`. Fuer die Anzeige ist das richtig und dort
eingefroren; vorgelesen ist es Unsinn -- Design §8.3 verlangt fuer die
Stimme: Steuerzeichen, Bidi-Overrides und Nullbreitenzeichen werden
ENTFERNT.

Gefangen von: den Steuerzeichen-Pruefungen (Kriterien 6 und 12) -- der
freigegebene Text darf weder das Steuerzeichen noch eine Escape-Folge
enthalten, und er muss KUERZER sein als die Eingabe. Bei ihm ist er
laenger und enthaelt `\\u202E` wörtlich.
"""),
    "ungefragt-nimmt-freien-text": (mut_ungefragt, """MUTANT: der Kanal `ungefragt` nimmt freien Text.

Kriterium 5 ist aufgeweicht: statt freien Text mit dem Grund
`freier_text` abzulehnen, laeuft er durch dieselben Regeln wie eine
Antwort. Damit kann ein Modell dem Nutzer ungefragt beliebige Saetze
vorlesen lassen, solange sie harmlos AUSSEHEN -- genau der Kanal, fuer
den die kuratierten Vorlagen existieren. Die Vorlagen selbst gehen
weiterhin durch (`aus_vorlage` ist unveraendert) -- die Positivkontrolle
des Verifizierers bleibt bei ihm gruen.

Gefangen von: der Ungefragt-Pruefung (Kriterium 5) -- freier Text auf
`ungefragt` wird abgelehnt, auch ein harmloser Satz.
"""),
}

for name, (mutation, notiz) in MUTANTEN.items():
    ziel = os.path.join(ziel_wurzel, name)
    baum(ziel)
    mutation(ziel)
    schreibe(ziel, "mutation.txt", notiz)

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Erzeugung fehlgeschlagen"; exit 1; }

# --- Gegenprobe ---------------------------------------------------------------
# Ein abgebrochenes Erzeugungsskript hat schon einmal eine unveraenderte Kopie
# hinterlassen, und ein Mutantenbaum hat schon einmal ein Artefakt der
# unmutierten Quelle mitgeschleppt. Beides faellt hier auf, bevor irgendein
# Lauf etwas "beweist".
rc=0
MUTANTEN=(validator-im-dienst abkuehlung-nicht-persistiert unterbrechung-wartet \
          bidi-escapt-statt-entfernt ungefragt-nimmt-freien-text)
BAEUME=("$GUT")
for m in "${MUTANTEN[@]}"; do BAEUME+=("$SCRIPT_DIR/$m"); done

# Welche Dateien je Baum mutiert sind (leer = Gut-Muster: keine). Nur dort
# darf sich ein Baum vom Basis-Commit unterscheiden.
mutierte_dateien() {  # $1 = Baumname
  case "$1" in
    T-3.9) echo "" ;;
    bidi-escapt-statt-entfernt|ungefragt-nimmt-freien-text) echo "daimon/hub/sprechtext.py" ;;
    abkuehlung-nicht-persistiert) echo "daimon/hub/abkuehlung.py" ;;
    unterbrechung-wartet) echo "daimon/face/tts.py" ;;
    validator-im-dienst) echo "daimon/hub/daemon.py daimon/face/tts.py" ;;
  esac
}

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  for pflicht in daimon/hub/sprechtext.py daimon/hub/abkuehlung.py \
                 daimon/face/tts.py daimon/hub/daemon.py daimon/hub/state.py \
                 config/systemd/daimon-tts.service config/systemd/daimon-tts.socket; do
    if [[ ! -f "$b/$pflicht" ]]; then
      echo "FEHLER: $name hat kein $pflicht"; rc=1
    fi
  done
  if ! python3 -m compileall -q "$b/daimon" >/dev/null 2>&1; then
    echo "FEHLER: $name ist syntaktisch kaputt"; python3 -m compileall "$b/daimon" 2>&1 | grep -i error | head -5; rc=1
  fi
  find "$b/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

  # Ausserhalb der mutierten Dateien darf sich NICHTS vom Basis-Commit
  # unterscheiden -- sonst misst der Verifizierer spaeter etwas anderes als
  # die Mutation. config/ ist in keinem Baum mutiert.
  xargs=(-x __pycache__)
  for f in $(mutierte_dateien "$name"); do xargs+=(-x "$(basename "$f")"); done
  rest="$(diff -r "${xargs[@]}" "$BASIS/daimon" "$b/daimon" | grep -c '^[<>]')"
  if [[ "$rest" -ne 0 ]]; then
    echo "FEHLER: $name weicht ausserhalb der mutierten Dateien vom Basis-Commit ab"; rc=1
  fi
  if ! diff -rq -x __pycache__ "$BASIS/config" "$b/config" >/dev/null 2>&1; then
    echo "FEHLER: $name weicht in config/ vom Basis-Commit ab"; rc=1
  fi
  # Und die mutierten Dateien MUESSEN sich unterscheiden -- ein stiller
  # Nichts-Ersetzer darf nicht als Mutant durchgehen.
  for f in $(mutierte_dateien "$name"); do
    if diff -q "$BASIS/$f" "$b/$f" >/dev/null 2>&1; then
      echo "FEHLER: $name: $f ist unveraendert -- die Mutation fehlt"; rc=1
    fi
  done
done

# Die Baeume muessen sich VONEINANDER unterscheiden.
for i in "${!BAEUME[@]}"; do
  for j in "${!BAEUME[@]}"; do
    [[ "$i" -lt "$j" ]] || continue
    if diff -qr -x mutation.txt -x HERKUNFT.txt -x __pycache__ \
            "${BAEUME[$i]}" "${BAEUME[$j]}" >/dev/null 2>/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$i]}") und $(basename "${BAEUME[$j]}") sind identisch"
      rc=1
    fi
  done
done

# Probe aufs Exempel: jede Mutation ist WIRKLICH im Baum -- und das Gut-Muster
# traegt keine davon.
probe() {  # $1 = Datei-Relativpfad, $2 = Muster, $3 = Soll im Gut-Muster (ja/nein)
  local f gut_hat
  f="$1"
  if grep -qF "$2" "$GUT/$f"; then gut_hat=ja; else gut_hat=nein; fi
  if [[ "$gut_hat" != "$3" ]]; then
    echo "FEHLER: Gut-Muster $f: Muster '$2' -> $gut_hat, erwartet $3"; rc=1
  fi
}
probe daimon/hub/daemon.py "urteil = sprechtext.pruefe(" ja
probe daimon/hub/daemon.py "MUTANT: der Hub prueft NICHT" nein
probe daimon/hub/abkuehlung.py "self._laden()" ja
probe daimon/hub/abkuehlung.py "self._schreiben()" ja
probe daimon/hub/sprechtext.py "grund=GRUND_FREIER_TEXT" ja
probe daimon/hub/sprechtext.py "u%04X" nein
# Der eigentliche Anlass des Umbaus: die aktuelle tts.py mit beiden Netzen
# gegen verwaiste Dienste.
probe daimon/face/tts.py "sterbe_mit_elternteil" ja
probe daimon/face/tts.py "HubWaechter" ja
probe daimon/face/tts.py "from daimon.hub import sprechtext" nein

# Der Unterbrechungspfad im Gut-Muster: `abbrechen()` toetet.
abbrechen_block() { awk '/def abbrechen/,/return self\._gen/' "$1"; }
if ! abbrechen_block "$GUT/daimon/face/tts.py" | grep -q "p\.kill()"; then
  echo "FEHLER: Gut-Muster: abbrechen() ohne p.kill()"; rc=1
fi

mprobe() {  # $1 = Mutant, $2 = Datei, $3 = Muster, $4 = Soll (ja/nein)
  local hat
  if grep -qF "$3" "$SCRIPT_DIR/$1/$2"; then hat=ja; else hat=nein; fi
  if [[ "$hat" != "$4" ]]; then
    echo "FEHLER: Mutant $1: '$3' in $2 -> $hat, erwartet $4"; rc=1
  fi
}
mprobe validator-im-dienst daimon/hub/daemon.py "MUTANT: der Hub prueft NICHT" ja
mprobe validator-im-dienst daimon/hub/daemon.py "urteil = sprechtext.pruefe(" nein
mprobe validator-im-dienst daimon/face/tts.py "MUTANT: die Pruefung passiert HIER" ja
mprobe validator-im-dienst daimon/face/tts.py "from daimon.hub import sprechtext" ja
# Beide Netze bleiben auch im Mutanten erhalten -- nur die Pruefung wandert.
mprobe validator-im-dienst daimon/face/tts.py "sterbe_mit_elternteil" ja
mprobe validator-im-dienst daimon/face/tts.py "HubWaechter" ja
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "MUTANT: der Bestand wird nicht geladen" ja
# Die Methode bleibt definiert (sonst faellt der Mutant schon am Aufruf und
# nicht an der Messung), aber der AUFRUF ist weg.
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "def _laden" ja
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "self._laden()" nein
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "def _schreiben" ja
mprobe abkuehlung-nicht-persistiert daimon/hub/abkuehlung.py "self._schreiben()" nein
mprobe unterbrechung-wartet daimon/face/tts.py "MUTANT: die neue Aeusserung reiht sich ein" ja
mprobe bidi-escapt-statt-entfernt daimon/hub/sprechtext.py "MUTANT: escapt statt entfernt" ja
mprobe bidi-escapt-statt-entfernt daimon/hub/sprechtext.py "u%04X" ja
mprobe bidi-escapt-statt-entfernt daimon/hub/sprechtext.py 'sub("", unicodedata' nein
mprobe ungefragt-nimmt-freien-text daimon/hub/sprechtext.py "grund=GRUND_FREIER_TEXT" nein
mprobe ungefragt-nimmt-freien-text daimon/hub/sprechtext.py "MUTANT: der Kanal \`ungefragt\` nimmt freien Text" ja

# Der Unterbrechungs-Mutant: auf dem Pfad von `abbrechen()` gibt es kein
# kill mehr, nur noch Warten. (Andere `kill`-Stellen im Dienst -- frisch
# gestarteter pw-cat bei schon-wieder-Abbruch, Timeout beim Aufraeumen --
# liegen nicht auf diesem Pfad und bleiben absichtlich unangetastet.)
# (Der Docstring nennt das Wort `kill` weiter -- gesucht wird der AUFRUF.)
block="$(abbrechen_block "$SCRIPT_DIR/unterbrechung-wartet/daimon/face/tts.py")"
if grep -q "\.kill(" <<<"$block"; then
  echo "FEHLER: unterbrechung-wartet: abbrechen() enthaelt noch einen kill-Aufruf"; rc=1
fi
if ! grep -q "p\.wait()" <<<"$block"; then
  echo "FEHLER: unterbrechung-wartet: abbrechen() wartet nicht"; rc=1
fi

echo
echo "Gut-Muster: $GUT"
echo "Mutanten:   ${MUTANTEN[*]}"
exit $rc
