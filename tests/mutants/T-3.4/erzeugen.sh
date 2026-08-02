#!/usr/bin/env bash
# Erzeugt die T-3.4-Mutanten reproduzierbar -- und dazu einen korrekten
# Referenzbaum, ohne den die Mutanten nichts beweisen.
#
# BASIS IST EIN GEPINNTER COMMIT, NICHT HEAD und nicht der Arbeitsbaum.
# Sobald der Builder `daimon/ears/interlock.py` committet, truege `git archive
# HEAD` SEINE Fassung in jeden Mutantenbaum -- die Mutanten waeren dann
# Abwandlungen des Prueflings statt unabhaengiger Gegenproben. Der Basisbaum
# darf die Datei deshalb NICHT enthalten; das wird unten geprueft und bricht ab.
#
# WARUM EIN REFERENZBAUM. Ein Mutant beweist, dass der Verifizierer rot werden
# KANN. Er beweist nicht, dass er gruen werden kann -- daran ist bei T-2.7 ein
# Verifizierer gescheitert (Fall 12 in HANDOVER.md).
#
#   DER REFERENZBAUM IST NICHT DER LIEFERGEGENSTAND und KEIN Gut-Muster im
#   Sinne von meta.sh. Er liegt deshalb ausdruecklich NICHT unter
#   tests/fixtures/known-good/. Das Gut-Muster entsteht erst aus dem
#   abgenommenen Stand der echten Implementierung.
#
# Aufruf:
#   tests/mutants/T-3.4/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
REFERENZ="$REPO/tests/fixtures/T-3.4-referenz-des-reviewers"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

# 7618b93 = "T-3.3.v: 61 Pruefungen, und die Adresse muss dieselbe bleiben".
# Enthaelt capture.py (T-3.1), vad.py (T-3.2) und ring.py (T-3.3), aber KEIN
# interlock.py.
BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-7618b93}"
git -C "$REPO" archive "$BASIS_COMMIT" daimon config | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/daimon/ears/__init__.py" ]] \
  || { echo "Basisbaum unvollstaendig: daimon/ears/ fehlt"; exit 1; }
[[ -f "$BASIS/config/daimon.toml" ]] \
  || { echo "Basisbaum unvollstaendig: config/daimon.toml fehlt"; exit 1; }
[[ -e "$BASIS/daimon/ears/interlock.py" ]] \
  && { echo "FEHLER: der Basis-Commit enthaelt bereits daimon/ears/interlock.py."
       echo "Dann waere die Grundlage der Mutanten die Fassung des Builders,"
       echo "nicht die blind geschriebene des Reviewers. Basis neu pinnen."; exit 1; }
# Und die Gegenprobe zum Abbruch selbst: fehlte auch ring.py, waere schlicht
# der falsche (zu alte) Commit erwischt -- und der Abbruch oben haette nichts
# gesagt.
[[ -f "$BASIS/daimon/ears/ring.py" ]] \
  || { echo "FEHLER: der Basis-Commit enthaelt kein ring.py -- er liegt vor"
       echo "T-3.3 und ist als Basis fuer T-3.4 falsch."; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" "$REFERENZ" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis, referenz = sys.argv[1], sys.argv[2], sys.argv[3]

# ===========================================================================
# Der gemeinsame Rumpf. Nur die drei Bloecke @@NACHLAUF@@, @@ECHO@@ und
# @@PTT@@ unterscheiden die vier Baeume -- alles andere ist identisch, damit
# ein Mutant nur an SEINER Mutation auffaellt.
#
# Platzhalter statt str.format: der Rumpf ist voller geschweifter Klammern,
# und ein verdoppeltes `{{` mehr oder weniger ist genau die Sorte Fehler, die
# einen Mutantenbaum lautlos zu einer unveraenderten Kopie macht.
# ===========================================================================
RUMPF = '''"""T-3.4: Rueckkopplungssperre.

@@TITEL@@

Die eigene Stimme darf sich nicht selbst reaktivieren. Unter Plan C (PTT statt
Wake-Word) ist das gefaehrlicher als vorher: der Nutzer haelt die Taste, das
Pet antwortet, und seine eigene Antwort landet als Nutzeraeusserung im STT.

Drei Mechanismen, und sie sind ausdruecklich nicht dasselbe:

  * WAEHREND der Wiedergabe wird gesperrt -- unabhaengig vom PTT-Zustand.
  * NACH der Wiedergabe noch NACHLAUF_MS lang, weil der Raumhall der letzten
    Silbe das Mikrofon spaeter erreicht als das Ende der Wiedergabe.
  * DANACH faengt die ECHO-REFERENZ, was trotzdem noch ankommt: der
    Ausgabepuffer wird eingespeist und mit dem Aufgenommenen verglichen.

Alle Fristen laufen ueber `time.monotonic()`. Eine NTP-Korrektur oder eine
Zeitumstellung darf eine Sperre nicht aufheben; die Wanduhr kann das nicht
zusagen, die monotone Uhr schon.

FAIL-CLOSED: ist der Zustand unklar -- Wiedergabe angemeldet, aber nie
beendet --, dann sperrt die Sperre. Die Kosten sind eine verlorene
Aeusserung, die der Nutzer wiederholt. Die Kosten der Gegenrichtung sind eine
Rueckkopplungsschleife.

THREADSICHER: die Sperre wird aus zwei Richtungen beruehrt -- dem
Audio-Callback (liest, oft) und der Wiedergabe (schreibt, selten). `ring.py`
traegt "nicht threadsicher" im Modulkopf; diese Entscheidung gehoert laut
Akzeptanzliste hierher, und sie faellt auf ein RLock.
"""
from __future__ import annotations

import logging
import threading
import time

NACHLAUF_MS = 500.0
# Wie lange ein eingespeister Ausgabepuffer als Referenz gilt. Danach ist der
# Raumhall vorbei und eine alte Referenz wuerde nur noch Fehlalarme erzeugen.
REFERENZ_FENSTER_S = 5.0

_log = logging.getLogger(__name__)


class Rueckkopplungssperre:
    """Entscheidet fuer jeden Chunk: geht er zur Transkription oder nicht."""

    def __init__(self, *, nachlauf_ms=NACHLAUF_MS,
                 referenz_fenster_s=REFERENZ_FENSTER_S):
        self.nachlauf_s = float(nachlauf_ms) / 1000.0
        self.referenz_fenster_s = float(referenz_fenster_s)
        self._lock = threading.RLock()
        self._laeuft = 0          # angemeldete, noch nicht beendete Wiedergaben
        self._bis = None          # monotone Frist des Nachlaufs
        self._ptt = False
        self._referenzen = []     # [(monotone Zeit, bytes)]

    # -- die Uhr, an einer einzigen Stelle ---------------------------------
    @staticmethod
    def _jetzt():
        return time.monotonic()

    # -- Wiedergabe --------------------------------------------------------
    def wiedergabe_beginnt(self):
        with self._lock:
            self._laeuft += 1
            self._bis = None

    def wiedergabe_endet(self):
        with self._lock:
            if self._laeuft > 0:
                self._laeuft -= 1
            if self._laeuft == 0:
@@NACHLAUF@@

    # -- PTT ---------------------------------------------------------------
    def ptt(self, gedrueckt):
        """Der PTT-Zustand wird MITGETEILT, nicht abgefragt -- und er oeffnet
        nichts. Er steht hier ausschliesslich fuer die Diagnose."""
        with self._lock:
            self._ptt = bool(gedrueckt)

    # -- Echo-Referenz -----------------------------------------------------
    def referenz(self, puffer):
        """Der Ausgabepuffer der Sprachausgabe. Weil TTS noch fehlt (T-3.9),
        wird er eingespeist; die Schnittstelle bleibt dieselbe."""
        roh = bytes(memoryview(puffer).cast("B"))
        with self._lock:
            self._referenzen.append((self._jetzt(), roh))
            self._verfallen()

    def _verfallen(self):
        grenze = self._jetzt() - self.referenz_fenster_s
        self._referenzen = [(t, b) for (t, b) in self._referenzen if t >= grenze]

    def _ist_echo(self, roh):
@@ECHO@@

    # -- Zustand -----------------------------------------------------------
    def _grund(self):
        if self._laeuft > 0:
            return "wiedergabe"
        if self._bis is not None and self._jetzt() < self._bis:
            return "nachlauf"
        return None

    def gesperrt(self):
        with self._lock:
            return self._grund() is not None

    # -- Die eine Entscheidung, auf die es ankommt -------------------------
    def durchlassen(self, chunk):
        """True = dieser Chunk geht zur Transkription."""
        roh = bytes(memoryview(chunk).cast("B"))
        with self._lock:
@@PTT@@
            if self._grund() is not None:
                return False
            if self._ist_echo(roh):
                return False
            return True

    # -- Diagnose ----------------------------------------------------------
    def diagnose(self):
        """Beleg, keine Messgrundlage. Wer hieran misst, ob gesperrt ist,
        misst den Selbstbericht -- Fall 9 in HANDOVER.md."""
        with self._lock:
            return {
                "gesperrt": self._grund() is not None,
                "grund": self._grund(),
                "bis": self._bis,
                "ptt": self._ptt,
                "wiedergaben_offen": self._laeuft,
                "referenzen": len(self._referenzen),
            }
'''

NACHLAUF_KORREKT = '''                self._bis = self._jetzt() + self.nachlauf_s
'''

NACHLAUF_MUTANT = '''                # MUTANT: kein Nachlauf. Die Sperre endet mit der Wiedergabe.
                self._bis = self._jetzt()
'''

ECHO_KORREKT = '''        self._verfallen()
        for _, ref in self._referenzen:
            if ref and (ref in roh or roh in ref):
                return True
        return False
'''

ECHO_MUTANT = '''        # MUTANT: der Abgleich fehlt. Referenzen werden weiter angenommen
        # und gezaehlt -- verglichen wird nichts, also geht alles durch.
        self._verfallen()
        return False
'''

PTT_KORREKT = '''            # Der PTT-Zustand steht hier ABSICHTLICH nicht. Wer die Taste
            # haelt, waehrend das Pet spricht, nimmt trotzdem nichts auf.
'''

PTT_MUTANT = '''            # MUTANT: bei gedruecktem PTT wird durchgelassen. Der Nutzer
            # "hat ja die Taste gehalten, also will er reden" -- und das Pet
            # hoert sich selbst zu. `gesperrt()` und `diagnose()` melden
            # weiter brav "gesperrt": genau darum ist die Diagnose kein
            # Messmittel.
            if self._ptt:
                return True
'''


def baum(pfad, titel, nachlauf, echo, ptt, notiz_text):
    for unter in ("daimon", "config"):
        z = os.path.join(pfad, unter)
        if os.path.exists(z):
            shutil.rmtree(z)
    os.makedirs(pfad, exist_ok=True)
    shutil.copytree(os.path.join(basis, "daimon"), os.path.join(pfad, "daimon"))
    shutil.copytree(os.path.join(basis, "config"), os.path.join(pfad, "config"))
    text = (RUMPF.replace("@@TITEL@@", titel)
                 .replace("@@NACHLAUF@@", nachlauf.rstrip("\\n"))
                 .replace("@@ECHO@@", echo.rstrip("\\n"))
                 .replace("@@PTT@@", ptt.rstrip("\\n")))
    with open(os.path.join(pfad, "daimon", "ears", "interlock.py"), "w",
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
    "geschrieben. NICHT der Liefergegenstand.",
    NACHLAUF_KORREKT, ECHO_KORREKT, PTT_KORREKT,
    """KEIN MUTANT, KEIN GUT-MUSTER, KEIN LIEFERGEGENSTAND.

Dieser Baum ist die minimal korrekte Umsetzung von T-3.4, geschrieben vom
Reviewer, blind gegen dieselbe Akzeptanzliste wie die echte Implementierung.
Er hat genau einen Zweck: zu zeigen, dass tests/verify/T-3.4.sh gruen werden
KANN. Ein Verifizierer, der das nicht kann, meldet jeden Mutanten als
"erkannt", ohne dessen Mutation je gemessen zu haben -- das ist Fall 12 in
HANDOVER.md und war bei T-2.7 real.

Er liegt bewusst NICHT unter tests/fixtures/known-good/: das Gut-Muster fuer
meta.sh entsteht erst aus dem abgenommenen Stand der echten Implementierung.

Er legt ausserdem den Vertrag offen, gegen den der Verifizierer blind gebaut
wurde -- `Rueckkopplungssperre()` mit `wiedergabe_beginnt()`,
`wiedergabe_endet()`, `ptt(bool)`, `referenz(puffer)`, `durchlassen(chunk)`,
`gesperrt()` und `diagnose()`. Weicht die echte Umsetzung davon ab, faellt
das beim Gegenlesen auf und ist dort zu entscheiden, nicht hier. Der
Verifizierer SUCHT den Einstiegspunkt, statt ihn vorzuschreiben.
""",
)

# ===========================================================================
# Mutant 1: nachlauf-auf-null
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "nachlauf-auf-null"),
    "MUTANT: die Sperre endet mit der Wiedergabe, ohne Nachlauf.",
    NACHLAUF_MUTANT, ECHO_KORREKT, PTT_KORREKT,
    """MUTANT: kein Nachlauf. `wiedergabe_endet()` setzt die Frist auf JETZT.

Alles andere stimmt: waehrend der Wiedergabe wird gesperrt, PTT oeffnet
nichts, die Echo-Referenz wirkt, die Diagnose ist ehrlich. Fehlt nur die
halbe Sekunde danach -- und genau in ihr erreicht der Raumhall der letzten
Silbe das Mikrofon. Das Pet hoert sein eigenes Satzende und haelt es fuer
eine Nutzeraeusserung.

DIESER MUTANT ZIELT AUF DEN VERIFIZIERER. Er ist gruen bei jeder Pruefung,
die nur "waehrend der Wiedergabe ist gesperrt" misst -- also bei der
naheliegenden. Und er ist gruen bei jedem `grep 500`: NACHLAUF_MS steht
unveraendert im Modul, der Konstruktor rechnet es brav in Sekunden um, nur
benutzt wird es nicht mehr.

Gefangen von:
  * Abschnitt 2, "unmittelbar nach dem Ende ist gesperrt" -- bei ihm nicht
  * Abschnitt 2, "die gemessene Grenze liegt bei 500 ms (400..700)"
  * Abschnitt 2, "400 ms nach dem Ende ist noch gesperrt"

NICHT gefangen von Abschnitt 3 (dort laeuft die Wiedergabe noch), nicht von
Abschnitt 4 (dort ist der Nachlauf ohnehin abgewartet), nicht von 5, 6 oder
7 -- die messen ausdruecklich in Zustaenden, in denen der Nachlauf keine
Rolle spielt. Abschnitt 6 (b) meldet sich ausdruecklich als NICHT GEMESSEN,
statt mitzufaerben.
""",
)

# ===========================================================================
# Mutant 2: echo-referenz-entfernt
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "echo-referenz-entfernt"),
    "MUTANT: der Abgleich mit der Echo-Referenz fehlt, alles geht durch.",
    NACHLAUF_KORREKT, ECHO_MUTANT, PTT_KORREKT,
    """MUTANT: `referenz()` nimmt weiter an, `_ist_echo()` vergleicht nichts.

Die Schnittstelle ist vollstaendig da: der Ausgabepuffer wird entgegen-
genommen, gespeichert, das Verfallsfenster wird gepflegt, `diagnose()` zaehlt
die Referenzen brav mit. Nur verglichen wird nichts -- also geht auch das
eigene Satzende durch, sobald der Nachlauf vorbei ist.

DIESER MUTANT ZIELT AUF DEN VERIFIZIERER. Er ist gruen bei jeder Pruefung,
die nur schaut, ob eine Referenz ANGENOMMEN wird (sie wird), oder ob das
Diagnosebild sie kennt (es kennt sie). Und er ist gruen bei einem
Vertragssucher, der `referenz` danach auswaehlt, ob hinterher etwas
verworfen wird -- deshalb waehlt der Sucher hier ausdruecklich nach ANNAHME.

Gefangen von:
  * Abschnitt 4, "MIT eingespeister Referenz wird der Echo-Chunk VERWORFEN"

NICHT gefangen von der Gegenrichtung im selben Abschnitt ("fremdes Material
wird nicht verworfen") -- die ist bei ihm korrekt gruen, und das ist der
Punkt: erst beide Richtungen zusammen trennen "verwirft das Richtige" von
"verwirft alles" und von "verwirft nichts".
""",
)

# ===========================================================================
# Mutant 3: sperre-nicht-bei-offener-runde
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "sperre-nicht-bei-offener-runde"),
    "MUTANT: bei gedruecktem PTT wird durchgelassen.",
    NACHLAUF_KORREKT, ECHO_KORREKT, PTT_MUTANT,
    """MUTANT: `durchlassen()` gibt bei gedruecktem PTT sofort True zurueck.

Die Begruendung, die man dazu schreiben wuerde, klingt vernuenftig: der
Nutzer haelt die Taste, also will er sprechen, also darf man ihn nicht
abschneiden. Sie ist genau falsch herum. Unter Plan C ist das der Kern des
Tasks: der Nutzer HAELT die Taste, waehrend das Pet antwortet -- und die
Antwort des Pets landet als seine Aeusserung im STT.

DIESER MUTANT ZIELT AUF REGEL 9. `gesperrt()` ist unveraendert, `diagnose()`
meldet weiter `gesperrt: true`, `grund: "wiedergabe"` und eine Frist in
`bis`. Jeder Verifizierer, der den Sperrzustand am Diagnosebild misst -- oder
dessen Vertragssucher den Entscheider `gesperrt` statt `durchlassen`
waehlt --, ist bei ihm vollstaendig gruen, waehrend das Pet sich selbst
zuhoert. Genau deshalb bevorzugt der Sucher einen Entscheider, DER EINEN
CHUNK SIEHT, und genau deshalb ist die Diagnose in Abschnitt 5 nur Beleg.

Gefangen von:
  * Abschnitt 3, "waehrend der Wiedergabe wird bei GEDRUECKTEM PTT nichts
    durchgelassen"
  * Abschnitt 3, "erneutes Druecken oeffnet die laufende Sperre nicht"

NICHT gefangen von der Positivkontrolle daneben ("ohne Wiedergabe und mit
gedruecktem PTT geht es durch") -- die ist bei ihm korrekt gruen. Und nicht
von den uebrigen Abschnitten: die messen alle mit LOSGELASSENER Taste, damit
dieser Mutant genau an einer Stelle faellt und nicht ueberall.
""",
)

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe ---------------------------------------------------------------
# Ein abgebrochenes Erzeugungsskript hat schon einmal eine unveraenderte Kopie
# hinterlassen, und zwei Mutantenbaeume waren schon einmal identisch. Beides
# faellt hier auf, bevor irgendein Lauf etwas "beweist".
rc=0
MUTANTEN=(nachlauf-auf-null echo-referenz-entfernt sperre-nicht-bei-offener-runde)
BAEUME=("$REFERENZ")
for m in "${MUTANTEN[@]}"; do BAEUME+=("$SCRIPT_DIR/$m"); done

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  if [[ ! -f "$b/daimon/ears/interlock.py" ]]; then
    echo "FEHLER: $name hat kein daimon/ears/interlock.py"; rc=1; continue
  fi
  zeilen="$(wc -l <"$b/daimon/ears/interlock.py")"
  # Ausserhalb von interlock.py darf sich nichts vom Basis-Commit
  # unterscheiden -- sonst misst der Verifizierer spaeter etwas anderes als
  # die Mutation.
  rest="$(diff -r -x interlock.py -x __pycache__ "$BASIS/daimon" "$b/daimon" | grep -c '^[<>]')"
  toml="$(diff "$BASIS/config/daimon.toml" "$b/config/daimon.toml" | grep -c '^[<>]')"
  echo "$name: $zeilen Zeilen interlock.py, $rest sonstige Abweichungen in daimon/, $toml in daimon.toml"
  if [[ "$zeilen" -lt 100 ]]; then echo "  FEHLER: $name ist praktisch leer"; rc=1; fi
  if [[ "$rest" -ne 0 ]]; then
    echo "  FEHLER: $name weicht ausserhalb von interlock.py vom Basis-Commit ab"; rc=1
  fi
  if [[ "$toml" -ne 0 ]]; then
    echo "  FEHLER: $name weicht in config/daimon.toml vom Basis-Commit ab"; rc=1
  fi
  if ! python3 -m compileall -q "$b/daimon" >/dev/null; then
    echo "  FEHLER: $name ist syntaktisch kaputt"; rc=1
  fi
  find "$b/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
done

# Die Baeume muessen sich VONEINANDER unterscheiden, und zwar in interlock.py.
for i in 0 1 2 3; do
  for j in 1 2 3; do
    [[ "$i" -lt "$j" ]] || continue
    if diff -q "${BAEUME[$i]}/daimon/ears/interlock.py" \
               "${BAEUME[$j]}/daimon/ears/interlock.py" >/dev/null 2>/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$i]}") und $(basename "${BAEUME[$j]}") sind identisch"
      rc=1
    fi
  done
done

# Probe aufs Exempel. Nur Plausibilitaet -- das Urteil faellt der Verifizierer.
R="$REFERENZ/daimon/ears/interlock.py"
grep -q 'self._bis = self._jetzt() + self.nachlauf_s' "$R" \
  || { echo "FEHLER: die Referenz setzt gar keinen Nachlauf"; rc=1; }
grep -q 'self._bis = self._jetzt() + self.nachlauf_s' \
  "$SCRIPT_DIR/nachlauf-auf-null/daimon/ears/interlock.py" \
  && { echo "FEHLER: nachlauf-auf-null hat doch einen Nachlauf"; rc=1; }
# Die Gegenprobe zum grep selbst: `nachlauf-auf-null` muss NACHLAUF_MS und
# `nachlauf_s` weiter enthalten. Sonst waere er auch fuer einen
# Quelltext-grep auffaellig, und "nur die Messung faengt ihn" waere geschenkt.
grep -q 'NACHLAUF_MS = 500' "$SCRIPT_DIR/nachlauf-auf-null/daimon/ears/interlock.py" \
  || { echo "FEHLER: nachlauf-auf-null erwaehnt die 500 nirgends mehr -- zu leicht"; rc=1; }

grep -q 'if ref and (ref in roh or roh in ref)' "$R" \
  || { echo "FEHLER: die Referenz gleicht gar nicht ab"; rc=1; }
grep -q 'if ref and (ref in roh or roh in ref)' \
  "$SCRIPT_DIR/echo-referenz-entfernt/daimon/ears/interlock.py" \
  && { echo "FEHLER: echo-referenz-entfernt gleicht doch ab"; rc=1; }
grep -q 'def referenz' "$SCRIPT_DIR/echo-referenz-entfernt/daimon/ears/interlock.py" \
  || { echo "FEHLER: echo-referenz-entfernt nimmt gar keine Referenz mehr an --"
       echo "        dann faellt er schon am Vertragssucher und nicht an der Messung"; rc=1; }

grep -q 'if self._ptt:' "$SCRIPT_DIR/sperre-nicht-bei-offener-runde/daimon/ears/interlock.py" \
  || { echo "FEHLER: sperre-nicht-bei-offener-runde liest den PTT-Zustand gar nicht"; rc=1; }
grep -q 'if self._ptt:' "$R" \
  && { echo "FEHLER: die Referenz laesst bei PTT durch"; rc=1; }
# Und er muss `gesperrt()`/`diagnose()` UNVERAENDERT lassen -- sonst faellt er
# in Abschnitt 5 statt in Abschnitt 3, und die Isolation waere dahin.
if ! diff <(sed -n '/def gesperrt/,/^$/p' "$R") \
          <(sed -n '/def gesperrt/,/^$/p' \
            "$SCRIPT_DIR/sperre-nicht-bei-offener-runde/daimon/ears/interlock.py") \
          >/dev/null 2>/dev/null; then
  echo "FEHLER: sperre-nicht-bei-offener-runde hat auch gesperrt() veraendert"; rc=1
fi

echo
echo "Mutanten: ${MUTANTEN[*]}"
echo "Referenz (kein Gut-Muster): $REFERENZ"
exit $rc
