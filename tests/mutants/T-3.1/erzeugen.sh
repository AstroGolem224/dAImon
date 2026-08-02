#!/usr/bin/env bash
# Erzeugt die T-3.1-Mutanten reproduzierbar -- und dazu einen korrekten
# Referenzbaum, ohne den die Mutanten nichts beweisen.
#
# BASIS IST EIN GEPINNTER COMMIT, NICHT HEAD und nicht der Arbeitsbaum.
# Grund, bei T-2.5 real passiert: sobald die Implementierung committet war,
# enthielt HEAD sie schon, und das Skript war exakt bis zu dem Moment
# reproduzierbar, ab dem man es braucht. Hier kommt dazu: sobald der Builder
# `daimon/ears/capture.py` committet, wuerde `git archive HEAD` SEINE Fassung
# in jeden Mutantenbaum tragen. Der Basisbaum muss die Datei NICHT enthalten;
# das wird unten geprueft.
#
# WARUM EIN REFERENZBAUM. Ein Mutant beweist, dass der Verifizierer rot werden
# KANN. Er beweist nicht, dass er gruen werden kann -- und genau daran ist bei
# T-2.7 ein Verifizierer gescheitert (Fall 12 im Handover: eine
# Positivkontrolle, die nie gruen wurde, machte jeden Mutanten "erkannt", ohne
# dass seine Mutation je gemessen wurde). Der Referenzbaum ist die minimal
# korrekte Umsetzung, die der Reviewer BLIND gegen dieselbe Akzeptanzliste
# geschrieben hat.
#
#   ER IST NICHT DER LIEFERGEGENSTAND und KEIN Gut-Muster im Sinne von
#   meta.sh. Er liegt deshalb ausdruecklich NICHT unter
#   tests/fixtures/known-good/. Das Gut-Muster entsteht erst aus dem
#   abgenommenen Stand der echten Implementierung.
#
# Aufruf:
#   tests/mutants/T-3.1/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
REFERENZ="$REPO/tests/fixtures/T-3.1-referenz-des-reviewers"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-fbbf028c08e91bb9a8399df4e6e97dfaba870b24}"
git -C "$REPO" archive "$BASIS_COMMIT" daimon | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/daimon/ears/__init__.py" ]] \
  || { echo "Basisbaum unvollstaendig: daimon/ears/ fehlt"; exit 1; }
[[ -e "$BASIS/daimon/ears/capture.py" ]] \
  && { echo "FEHLER: der Basis-Commit enthaelt bereits capture.py."
       echo "Dann waere die Grundlage der Mutanten die Fassung des Builders,"
       echo "nicht die blind geschriebene des Reviewers. Basis neu pinnen."; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" "$REFERENZ" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis, referenz = sys.argv[1], sys.argv[2], sys.argv[3]

KOPF = '''"""T-3.1: Aufnahme mit hartem Lebenszyklus.

{titel}

PIPEWIRE_LATENCY steht VOR dem Import von sounddevice -- das PipeWire-
ALSA-Plugin liest die Variable, wenn das PCM geoeffnet wird, und die
Reihenfolge ist die Zusage der Akzeptanzliste.
"""
import os

os.environ.setdefault("PIPEWIRE_LATENCY", "512/16000")

import sounddevice as sd  # noqa: E402

RATE = 16000
BLOCK = 512
KANAELE = 1
DTYPE = "int16"
DEVICE = "pipewire"  # NICHT "default": der Weg soll benannt sein.
{extra}

class Aufnahme:
    """start() oeffnet, stop() ZERSTOERT."""

    def __init__(self) -> None:
        self._strom = None
        self._blocks = 0
        self._offen = False

    def _block(self, daten, rahmen, zeit, status) -> None:
        self._blocks += 1

    def start(self) -> None:
        if self._strom is not None:
            return
        strom = sd.InputStream(
            device=DEVICE,
            samplerate=RATE,
            channels=KANAELE,
            dtype=DTYPE,
            blocksize=BLOCK,
            callback=self._block,
        )
        strom.start()
        self._strom = strom
        self._offen = True

{stop}
    def zustand(self) -> dict:
        return {{
            "offen": self._offen,
            "blocks": self._blocks,
            "rate": RATE,
            "kanaele": KANAELE,
            "dtype": DTYPE,
            "device": DEVICE,
        }}
'''

STOP_KORREKT = '''    def stop(self) -> None:
        """close(), nicht stop(). stop() pausiert nur; das Source-Output-
        Objekt bliebe stehen und der Plasma-Indikator mit ihm."""
        strom, self._strom = self._strom, None
        self._offen = False
        if strom is not None:
            strom.close()

'''

STOP_MUTANT_1 = '''    def stop(self) -> None:
        """MUTANT: stop() statt close(). Das Objekt ueberlebt."""
        self._offen = False
        if self._strom is not None:
            self._strom.stop()

'''

STOP_MUTANT_2 = '''    def stop(self) -> None:
        """MUTANT: stop() kehrt zurueck, BEVOR abgeraeumt ist."""
        strom, self._strom = self._strom, None
        self._offen = False
        if strom is None:
            return
        atexit.register(strom.close)
        threading.Thread(target=self._spaeter, args=(strom,), daemon=True).start()

    def _spaeter(self, strom) -> None:
        time.sleep(VERZOEGERUNG)
        try:
            strom.close()
        except Exception:
            pass

'''


def baum(pfad, titel, extra, stop, notiz_text):
    if os.path.exists(os.path.join(pfad, "daimon")):
        shutil.rmtree(os.path.join(pfad, "daimon"))
    os.makedirs(pfad, exist_ok=True)
    shutil.copytree(os.path.join(basis, "daimon"), os.path.join(pfad, "daimon"))
    with open(os.path.join(pfad, "daimon", "ears", "capture.py"), "w",
              encoding="utf-8") as f:
        f.write(KOPF.format(titel=titel, extra=extra, stop=stop))
    with open(os.path.join(pfad, "mutation.txt"), "w", encoding="utf-8") as f:
        f.write(notiz_text)


# =============================================================================
# Referenz: korrekt. Kein Liefergegenstand, kein Gut-Muster.
# =============================================================================
baum(
    referenz,
    "Minimale KORREKTE Fassung, vom Reviewer blind gegen die Akzeptanzliste\n"
    "geschrieben. NICHT der Liefergegenstand.",
    "",
    STOP_KORREKT,
    """KEIN MUTANT, KEIN GUT-MUSTER, KEIN LIEFERGEGENSTAND.

Dieser Baum ist die minimal korrekte Umsetzung von T-3.1, geschrieben vom
Reviewer, blind gegen dieselbe Akzeptanzliste wie die echte Implementierung.
Er hat genau einen Zweck: zu zeigen, dass tests/verify/T-3.1.sh gruen werden
KANN. Ein Verifizierer, der das nicht kann, meldet jeden Mutanten als
"erkannt", ohne dessen Mutation je gemessen zu haben -- das ist Fall 12 in
HANDOVER.md und war bei T-2.7 real.

Er liegt bewusst NICHT unter tests/fixtures/known-good/: das Gut-Muster fuer
meta.sh entsteht erst aus dem abgenommenen Stand der echten Implementierung.
""",
)

# =============================================================================
# Mutant 1: stop-statt-close
# =============================================================================
baum(
    os.path.join(ziel_wurzel, "stop-statt-close"),
    "MUTANT: stop() statt close().",
    "",
    STOP_MUTANT_1,
    """MUTANT: `stream.stop()` statt `stream.close()`.

Alles andere stimmt: Geraet, Rate, Kanaele, dtype, Blockgroesse,
PIPEWIRE_LATENCY vor dem Import, der Diagnose-Vertrag. `zustand()["offen"]`
meldet nach dem Ausschalten sogar brav `false` -- der Selbstbericht luegt
nicht einmal absichtlich, er weiss es nur nicht besser.

Was bleibt, ist das Source-Output-Objekt. Der Strom ist pausiert, das Objekt
lebt, und der Plasma-Indikator haengt daran. Genau das ist die
Korrektheitsanforderung aus Design 7.4: ein Nutzer, der dem Indikator glaubt,
muss ihm glauben koennen.

Sichtbar ist der Fehler NUR von aussen:
  pw-dump | jq -r '.[]|select(.info.props["media.class"]=="Stream/Input/Audio")
                    |.info.props["application.name"]'
Ein Verifizierer, der `zustand()` fragt, ist hier vollstaendig gruen.

Gefangen von: Abschnitt 4 des Verifizierers ("kein Strom des Prueflings mehr
in pw-dump", gemessen waehrend der Prozess noch lebt).
""",
)

# =============================================================================
# Mutant 2: pw-dump-vor-dem-teardown
# =============================================================================
baum(
    os.path.join(ziel_wurzel, "pw-dump-vor-dem-teardown"),
    "MUTANT: das Abraeumen passiert erst NACH der Rueckkehr von stop().",
    "import atexit\nimport threading\nimport time\n\nVERZOEGERUNG = 8.0\n",
    STOP_MUTANT_2,
    """MUTANT: `close()` passiert -- aber erst spaeter.

`stop()` kehrt sofort zurueck, das eigentliche `close()` laeuft acht Sekunden
spaeter in einem Hintergrund-Thread und zusaetzlich per `atexit` beim
Prozessende.

DIESER MUTANT ZIELT NICHT AUF DIE IMPLEMENTIERUNG, SONDERN AUF DIE
MESSREIHENFOLGE DES VERIFIZIERERS. Er ist der Test dafuer, dass der
Verifizierer im richtigen Moment misst:

  * Ein Verifizierer, der nach `stop()` erst einmal grosszuegig schlaeft
    (>8 s), sieht nichts mehr und meldet gruen.
  * Ein Verifizierer, der ueberhaupt erst NACH dem Ende des Prueflings-
    Prozesses misst, sieht ebenfalls nichts -- ein toter Prozess gibt alle
    Objekte ab. Gegen `stop-statt-close` waere er damit ebenso blind, und das
    ist der eigentliche Grund, warum dieser Mutant hier steht.
  * Nur wer unmittelbar nach der RUECKKEHR von `stop()` misst und im selben
    Atemzug belegt, dass der Prozess noch lebt, sieht den Unterschied.

Ist das ueberhaupt ein Fehler? Ja. Die Zusage lautet "nach dem Ausschalten ist
das Objekt weg", nicht "acht Sekunden nach dem Ausschalten". Wer den
Kill-Switch drueckt, weil er nicht mehr abgehoert werden will, hat in diesen
acht Sekunden ein offenes Mikrofon und einen Indikator, der eine andere
Geschichte erzaehlt als das System.

EHRLICHE EINSCHRAENKUNG: der Name stammt aus dem Auftrag ("der Verifizierer
misst zu frueh"). Was hier gebaut ist, bestraft das Gegenteil -- zu SPAET
oder zu weit hinten in der Reihenfolge zu messen. Ein Mutant, der einen zu
FRUEH messenden Verifizierer gruen macht, ist nicht konstruierbar: die
Zusage bezieht sich auf den Moment nach `stop()`, und vor diesem Moment gibt
es keine Messung, die "scheinbar stimmt". Diese Abweichung vom Wortlaut des
Auftrags ist bewusst und steht auch im Bericht.

Gefangen von: Abschnitt 4 (erste Messung unmittelbar nach der Rueckkehr von
stop(), Frist 2,0 s, und die Positivkontrolle "der Prozess lebt noch").
""",
)

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe ---------------------------------------------------------------
# Ein abgebrochenes Erzeugungsskript hat schon einmal eine unveraenderte Kopie
# hinterlassen, und zwei Mutantenbaeume waren schon einmal identisch. Beide
# faellt hier auf.
rc=0
MUTANTEN=(stop-statt-close pw-dump-vor-dem-teardown)
BAEUME=("$REFERENZ" "$SCRIPT_DIR/stop-statt-close" "$SCRIPT_DIR/pw-dump-vor-dem-teardown")

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  if [[ ! -f "$b/daimon/ears/capture.py" ]]; then
    echo "FEHLER: $name hat kein daimon/ears/capture.py"; rc=1; continue
  fi
  # Gegenueber dem Basis-Commit: die Datei gibt es dort GAR NICHT, also ist
  # jeder Baum verschieden. Das genuegt als Aussage nicht -- deshalb wird
  # zusaetzlich geprueft, dass sich sonst nichts am Baum geaendert hat.
  rest="$(diff -r -x capture.py -x __pycache__ "$BASIS/daimon" "$b/daimon" | grep -c '^[<>]')"
  neu="$(diff "$BASIS/daimon/ears/__init__.py" "$b/daimon/ears/capture.py" | grep -c '^[<>]')"
  echo "$name: $neu Zeilen in capture.py, $rest sonstige Abweichungen vom Basisbaum"
  if [[ "$neu" -lt 20 ]]; then echo "  FEHLER: $name ist praktisch leer"; rc=1; fi
  if [[ "$rest" -ne 0 ]]; then
    echo "  FEHLER: $name weicht ausserhalb von capture.py vom Basis-Commit ab"; rc=1
  fi
  if ! python3 -m compileall -q "$b/daimon" >/dev/null; then
    echo "  FEHLER: $name ist syntaktisch kaputt"; rc=1
  fi
  find "$b/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
done

# Die Baeume muessen sich VONEINANDER unterscheiden -- und zwar an der Stelle,
# auf die es ankommt.
for a in 0 1 2; do
  for b in 1 2; do
    [[ "$a" -lt "$b" ]] || continue
    if diff -q "${BAEUME[$a]}/daimon/ears/capture.py" \
               "${BAEUME[$b]}/daimon/ears/capture.py" >/dev/null 2>&1; then
      echo "FEHLER: $(basename "${BAEUME[$a]}") und $(basename "${BAEUME[$b]}") sind identisch"
      rc=1
    fi
  done
done

# Und die Probe aufs Exempel: der Referenzbaum darf `close` rufen, die
# Mutanten muessen sich sichtbar anders verhalten. Das ist hier nur eine
# Plausibilitaet -- das Urteil faellt der Verifizierer, nicht dieses grep.
grep -q 'strom.close()' "$REFERENZ/daimon/ears/capture.py" \
  || { echo "FEHLER: der Referenzbaum ruft gar kein close()"; rc=1; }
grep -q '_strom.stop()' "$SCRIPT_DIR/stop-statt-close/daimon/ears/capture.py" \
  || { echo "FEHLER: stop-statt-close ruft gar kein stop()"; rc=1; }

echo
echo "Mutanten: ${MUTANTEN[*]}"
echo "Referenz (kein Gut-Muster): $REFERENZ"
exit $rc
