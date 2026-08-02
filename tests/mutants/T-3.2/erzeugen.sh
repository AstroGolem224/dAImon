#!/usr/bin/env bash
# Erzeugt die T-3.2-Mutanten reproduzierbar -- und dazu einen korrekten
# Referenzbaum, ohne den die Mutanten nichts beweisen.
#
# BASIS IST EIN GEPINNTER COMMIT, NICHT HEAD und nicht der Arbeitsbaum.
# Bei T-2.5 real passiert: sobald die Implementierung committet war, enthielt
# HEAD sie schon -- das Skript war exakt bis zu dem Moment reproduzierbar, ab
# dem man es braucht. Hier kommt dazu: sobald der Builder
# `daimon/ears/vad.py` committet, truege `git archive HEAD` SEINE Fassung in
# jeden Mutantenbaum, und die Mutanten waeren Abwandlungen des Prueflings
# statt unabhaengiger Gegenproben. Der Basisbaum darf die Datei deshalb NICHT
# enthalten; das wird unten geprueft und bricht ab.
#
# WARUM EIN REFERENZBAUM. Ein Mutant beweist, dass der Verifizierer rot werden
# KANN. Er beweist nicht, dass er gruen werden kann -- daran ist bei T-2.7 ein
# Verifizierer gescheitert (Fall 12 in HANDOVER.md: eine Positivkontrolle, die
# nie gruen wurde, machte jeden Mutanten "erkannt", ohne dass seine Mutation je
# gemessen wurde).
#
#   DER REFERENZBAUM IST NICHT DER LIEFERGEGENSTAND und KEIN Gut-Muster im
#   Sinne von meta.sh. Er liegt deshalb ausdruecklich NICHT unter
#   tests/fixtures/known-good/. Das Gut-Muster entsteht erst aus dem
#   abgenommenen Stand der echten Implementierung.
#
# Aufruf:
#   tests/mutants/T-3.2/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
REFERENZ="$REPO/tests/fixtures/T-3.2-referenz-des-reviewers"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-a60bef713e8b797aed154b367eac439349cfa661}"
git -C "$REPO" archive "$BASIS_COMMIT" daimon config | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/daimon/ears/__init__.py" ]] \
  || { echo "Basisbaum unvollstaendig: daimon/ears/ fehlt"; exit 1; }
[[ -f "$BASIS/config/daimon.toml" ]] \
  || { echo "Basisbaum unvollstaendig: config/daimon.toml fehlt"; exit 1; }
[[ -e "$BASIS/daimon/ears/vad.py" ]] \
  && { echo "FEHLER: der Basis-Commit enthaelt bereits daimon/ears/vad.py."
       echo "Dann waere die Grundlage der Mutanten die Fassung des Builders,"
       echo "nicht die blind geschriebene des Reviewers. Basis neu pinnen."; exit 1; }
# Und die Gegenprobe zum Abbruch selbst: enthielte der Basis-Commit auch
# `capture.py` nicht, waere das Archiv schlicht der falsche Commit.
[[ -f "$BASIS/daimon/ears/capture.py" ]] \
  || { echo "FEHLER: der Basis-Commit enthaelt kein capture.py -- er liegt vor"
       echo "T-3.1 und ist als Basis fuer T-3.2 falsch."; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" "$REFERENZ" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis, referenz = sys.argv[1], sys.argv[2], sys.argv[3]

# ===========================================================================
# Der gemeinsame Rumpf. Nur der Block ZUSTANDSMASCHINE bzw. die Laengenpruefung
# unterscheidet die drei Baeume -- alles andere ist identisch, damit ein
# Mutant nur an SEINER Mutation auffaellt.
# ===========================================================================
KOPF = '''"""T-3.2: Segmentierung mit asymmetrischer Hysterese.

{titel}

Die Zusage lautet: WORTENDEN WERDEN NICHT ABGESCHNITTEN. Deshalb ist die
Hysterese asymmetrisch -- Einsatz bei >= 0,5, Ende erst, wenn die
Wahrscheinlichkeit `nachlauf_ms` lang unter `ende` (~0,35) liegt. Ein
symmetrischer Schwellwert macht aus "...und dann GING er" ein "...und dann g".

`segmentieren()` nimmt eine Folge entgegen, deren Elemente entweder
Wahrscheinlichkeiten (float) oder Audio-Chunks (bytes/bytearray/ndarray)
sind. Das ist keine Bequemlichkeit: nur so ist die Zustandsmaschine
deterministisch pruefbar, ohne ein Modell zu befragen, das synthetische
Signale nicht fuer Sprache haelt.
"""
from __future__ import annotations

import logging

RATE = 16000
CHUNK = 512
DTYPE = "int16"
CHUNK_MS = 1000.0 * CHUNK / RATE          # exakt 32,0 ms
CHUNK_BYTES = CHUNK * 2                   # int16

EINSATZ = 0.5
ENDE = 0.35
NACHLAUF_MS = 400                         # Plankorridor: 300..500

# Vorgaben des Plans. Eine Konfiguration darunter ist nicht verboten, aber
# sie kippt die Zusage -- also wird sie benannt, nicht verschwiegen.
MIN_EINSATZ = 0.5
MIN_NACHLAUF_MS = 300
MAX_NACHLAUF_MS = 500

_log = logging.getLogger(__name__)


class ChunkFehler(ValueError):
    """Ein Chunk hatte nicht exakt {{CHUNK}} Samples."""


def _det():
    from pysilero_vad import SileroVoiceActivityDetector
    return SileroVoiceActivityDetector()


def _pruefe_schwellen(einsatz, ende, nachlauf_ms):
    if einsatz < MIN_EINSATZ:
        _log.warning(
            "einsatz=%s liegt unter der Planvorgabe %s -- damit geht Rauschen "
            "als Sprache durch", einsatz, MIN_EINSATZ)
    if ende >= einsatz:
        _log.warning(
            "ende=%s liegt nicht unter einsatz=%s -- die Hysterese ist damit "
            "symmetrisch und schneidet Wortenden ab", ende, einsatz)
    if nachlauf_ms < MIN_NACHLAUF_MS:
        _log.warning(
            "nachlauf_ms=%s liegt unter der Planvorgabe %s -- Wortenden "
            "werden abgeschnitten", nachlauf_ms, MIN_NACHLAUF_MS)
    if nachlauf_ms > MAX_NACHLAUF_MS:
        _log.warning(
            "nachlauf_ms=%s liegt ueber der Planvorgabe %s -- Segmente "
            "schleppen Stille mit", nachlauf_ms, MAX_NACHLAUF_MS)


{laenge}

def _wahrscheinlichkeit(zustand, element):
    """float -> direkt. Alles andere -> durch den Detektor."""
    if isinstance(element, float) or (
            isinstance(element, int) and not isinstance(element, bool)):
        return float(element)
    roh = _roh(element)
    if zustand["det"] is None:
        zustand["det"] = _det()
    return float(zustand["det"](roh))


def schwellen_aus_config(cfg) -> dict:
    """Liest [ears.vad] aus einer geladenen Konfiguration (T-0.5)."""
    return {{
        "einsatz": float(cfg.get("ears.vad.einsatz", EINSATZ)),
        "ende": float(cfg.get("ears.vad.ende", ENDE)),
        "nachlauf_ms": int(cfg.get("ears.vad.nachlauf_ms", NACHLAUF_MS)),
    }}


def segmentieren(folge, *, einsatz=EINSATZ, ende=ENDE,
                 nachlauf_ms=NACHLAUF_MS, detektor=None,
                 chunk_ms=CHUNK_MS) -> list[dict]:
    _pruefe_schwellen(einsatz, ende, nachlauf_ms)
    zustand = {{"det": detektor}}
    segmente: list[dict] = []
    aktiv = False
    start = 0
    unter = 0
    i = -1
    for i, element in enumerate(folge):
        p = _wahrscheinlichkeit(zustand, element)
{maschine}
    if aktiv:
        segmente.append({{"start_ms": start * chunk_ms,
                         "ende_ms": (i + 1) * chunk_ms}})
    return segmente
'''

LAENGE_KORREKT = '''def _roh(element) -> bytes:
    """Genau CHUNK Samples, sonst Fehler. KEIN stilles Auffuellen.

    Ein aufgefuellter Chunk ist eine Luege gegenueber dem Modell: es bekaeme
    Stille, die nie aufgenommen wurde, und der Aufrufer erfuehre nie, dass
    seine Blockgroesse nicht passt. Silero arbeitet ausschliesslich in
    512-Sample-Schritten; wer das nicht einhaelt, hat einen Fehler in der
    Verdrahtung -- und Fehler in der Verdrahtung gehoeren laut, nicht leise.
    """
    roh = bytes(memoryview(element).cast("B"))
    if len(roh) != CHUNK_BYTES:
        raise ChunkFehler(
            f"Chunk hat {len(roh)} Bytes ({len(roh) // 2} Samples), "
            f"erwartet sind {CHUNK_BYTES} ({CHUNK} Samples int16 bei "
            f"{RATE} Hz)")
    return roh
'''

LAENGE_MUTANT = '''def _roh(element) -> bytes:
    """MUTANT: eine abweichende Chunklaenge wird stillschweigend gepolstert."""
    roh = bytes(memoryview(element).cast("B"))
    if len(roh) < CHUNK_BYTES:
        roh = roh + b"\\x00" * (CHUNK_BYTES - len(roh))
    elif len(roh) > CHUNK_BYTES:
        roh = roh[:CHUNK_BYTES]
    return roh
'''

MASCHINE_KORREKT = '''        if not aktiv:
            # Einsatz: eine einzige Ueberschreitung genuegt.
            if p >= einsatz:
                aktiv = True
                start = i
                unter = 0
        else:
            # Ende: erst nach `nachlauf_ms` UNUNTERBROCHEN unter `ende`.
            # Der Nachlauf ist keine Vorsicht, er ist die Zusage.
            if p < ende:
                unter += 1
                if unter * chunk_ms >= nachlauf_ms:
                    segmente.append({"start_ms": start * chunk_ms,
                                     "ende_ms": (i - unter + 1) * chunk_ms})
                    aktiv = False
                    unter = 0
            else:
                unter = 0
'''

MASCHINE_MUTANT = '''        # MUTANT: dieselbe Schwelle in beide Richtungen, kein Nachlauf.
        if not aktiv:
            if p >= einsatz:
                aktiv = True
                start = i
        else:
            if p < einsatz:
                segmente.append({"start_ms": start * chunk_ms,
                                 "ende_ms": i * chunk_ms})
                aktiv = False
'''

TOML_ZUSATZ = '''
# T-3.2: Hysterese der Sprachaktivitaetserkennung. Der Einsatz ist bewusst
# hoch und das Ende bewusst niedriger -- die Luecke dazwischen plus der
# Nachlauf sind das, was Wortenden vor dem Abschneiden bewahrt. Werte unter
# einsatz=0.5 oder nachlauf_ms=300 kippen die Zusage und werden geloggt.
[ears.vad]
einsatz = 0.5
ende = 0.35
nachlauf_ms = 400
'''


def baum(pfad, titel, laenge, maschine, notiz_text):
    for unter in ("daimon", "config"):
        z = os.path.join(pfad, unter)
        if os.path.exists(z):
            shutil.rmtree(z)
    os.makedirs(pfad, exist_ok=True)
    shutil.copytree(os.path.join(basis, "daimon"), os.path.join(pfad, "daimon"))
    shutil.copytree(os.path.join(basis, "config"), os.path.join(pfad, "config"))
    with open(os.path.join(pfad, "daimon", "ears", "vad.py"), "w",
              encoding="utf-8") as f:
        f.write(KOPF.format(titel=titel, laenge=laenge, maschine=maschine))
    with open(os.path.join(pfad, "config", "daimon.toml"), "a",
              encoding="utf-8") as f:
        f.write(TOML_ZUSATZ)
    with open(os.path.join(pfad, "mutation.txt"), "w", encoding="utf-8") as f:
        f.write(notiz_text)


# ===========================================================================
# Referenz: korrekt. Kein Liefergegenstand, kein Gut-Muster.
# ===========================================================================
baum(
    referenz,
    "Minimale KORREKTE Fassung, vom Reviewer blind gegen die Akzeptanzliste\\n"
    "geschrieben. NICHT der Liefergegenstand.",
    LAENGE_KORREKT,
    MASCHINE_KORREKT,
    """KEIN MUTANT, KEIN GUT-MUSTER, KEIN LIEFERGEGENSTAND.

Dieser Baum ist die minimal korrekte Umsetzung von T-3.2, geschrieben vom
Reviewer, blind gegen dieselbe Akzeptanzliste wie die echte Implementierung.
Er hat genau einen Zweck: zu zeigen, dass tests/verify/T-3.2.sh gruen werden
KANN. Ein Verifizierer, der das nicht kann, meldet jeden Mutanten als
"erkannt", ohne dessen Mutation je gemessen zu haben -- das ist Fall 12 in
HANDOVER.md und war bei T-2.7 real.

Er liegt bewusst NICHT unter tests/fixtures/known-good/: das Gut-Muster fuer
meta.sh entsteht erst aus dem abgenommenen Stand der echten Implementierung.

Er legt ausserdem den Vertrag offen, gegen den der Verifizierer blind
gebaut wurde -- `segmentieren(folge, *, einsatz, ende, nachlauf_ms,
detektor)`. Weicht die echte Umsetzung davon ab, faellt das beim Gegenlesen
auf und ist dort zu entscheiden, nicht hier.
""",
)

# ===========================================================================
# Mutant 1: symmetrische-schwelle
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "symmetrische-schwelle"),
    "MUTANT: Ende bei derselben Schwelle wie der Einsatz, kein Nachlauf.",
    LAENGE_KORREKT,
    MASCHINE_MUTANT,
    """MUTANT: symmetrischer Schwellwert, kein Nachlauf.

Das Segment endet, sobald die Wahrscheinlichkeit unter `einsatz` faellt --
dieselbe Schwelle in beide Richtungen, und ohne jede Wartezeit. `ende` und
`nachlauf_ms` werden entgegengenommen und ignoriert.

DAS IST GENAU DER FEHLER, DEN T-3.2 VERHINDERN SOLL. Er ist nicht exotisch,
er ist die naheliegende Umsetzung: eine Schwelle, ein Vergleich, fertig. Und
er ist im Betrieb kaum zu sehen -- Sprache wird erkannt, Segmente entstehen,
alles wirkt richtig. Was fehlt, sind die leisen Endsilben: "...und dann GING
er" wird zu "...und dann g". Der Schaden landet erst in der Transkription,
zwei Bausteine spaeter, und sieht dort nach einem STT-Problem aus.

Ein Verifizierer, der nur "200 ms ergibt eins, 800 ms ergibt zwei" prueft,
faengt ihn NICHT vollstaendig: bei 800 ms liefert auch dieser Mutant zwei
Segmente. Er faellt nur auf, wenn jemand ausdruecklich misst, dass ein
kurzer Einbruch KEIN Segment beendet.

Gefangen von:
  * Abschnitt 3, "192 ms Pause ergeben EIN Segment" (er liefert zwei)
  * Abschnitt 3, ASYMMETRIE 0,40: eine Delle unter dem Einsatz, aber ueber
    dem Ende, beendet bei ihm ein Segment
  * Abschnitt 3, ASYMMETRIE Wortende: die Rampe 0,60/0,45/0,30/... reisst
    bei ihm das Wort auseinander
  * Abschnitt 6: nachlauf_ms=300 und =500 liefern bei ihm dasselbe --
    der Wert wirkt nicht
""",
)

# ===========================================================================
# Mutant 2: chunk-still-aufgefuellt
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "chunk-still-aufgefuellt"),
    "MUTANT: abweichende Chunklaenge wird gepolstert statt abgewiesen.",
    LAENGE_MUTANT,
    MASCHINE_KORREKT,
    """MUTANT: ein zu kurzer Chunk wird mit Nullen aufgefuellt, ein zu langer
abgeschnitten -- lautlos.

Die Hysterese ist hier vollstaendig korrekt. Kaputt ist nur die
Modellanbindung, und zwar auf die freundlichste denkbare Weise: nichts
fliegt, nichts wird geloggt, es laeuft einfach weiter.

WARUM DAS EIN FEHLER IST, obwohl es "funktioniert": Silero arbeitet
ausschliesslich in 512-Sample-Schritten und traegt internen Zustand ueber
die Chunks. Wer auffuellt, schiebt dem Modell Stille unter, die nie
aufgenommen wurde -- mitten in einem Wort. Vor allem aber: der Aufrufer
erfaehrt nie, dass seine Blockgroesse nicht passt. Eine falsch verdrahtete
Aufnahme (T-3.1 liefert 512; eine spaetere Quelle vielleicht 480) wuerde
dauerhaft leicht falsche Wahrscheinlichkeiten liefern, und niemand haette
einen Anhaltspunkt, wo zu suchen ist.

Dieser Mutant zielt ausserdem auf den VERIFIZIERER: wer die Laengenpruefung
per `grep -c 512` im Quelltext sucht, findet hier CHUNK_BYTES und ist gruen
(HANDOVER.md, T-1.7.v3: ein Builder schrieb 'face' statt "face" und die
eingefrorene Pruefung schwieg). Gemessen werden muss, WAS AM MODELL ANKOMMT.

Gefangen von:
  * Abschnitt 4, "ein kurzer/langer Chunk wird ABGEWIESEN" (er laesst durch)
  * Abschnitt 4, "kein stilles Polstern": am Spion-Detektor kommt ein
    Puffer an, der weder der gute noch der abweichende ist
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
MUTANTEN=(symmetrische-schwelle chunk-still-aufgefuellt)
BAEUME=("$REFERENZ" "$SCRIPT_DIR/symmetrische-schwelle" "$SCRIPT_DIR/chunk-still-aufgefuellt")

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  if [[ ! -f "$b/daimon/ears/vad.py" ]]; then
    echo "FEHLER: $name hat kein daimon/ears/vad.py"; rc=1; continue
  fi
  zeilen="$(wc -l <"$b/daimon/ears/vad.py")"
  # Ausserhalb von vad.py und config/daimon.toml darf sich nichts vom
  # Basis-Commit unterscheiden -- sonst misst der Verifizierer spaeter etwas
  # anderes als die Mutation.
  rest="$(diff -r -x vad.py -x __pycache__ "$BASIS/daimon" "$b/daimon" | grep -c '^[<>]')"
  toml="$(diff "$BASIS/config/daimon.toml" "$b/config/daimon.toml" | grep -c '^>')"
  echo "$name: $zeilen Zeilen vad.py, $rest sonstige Abweichungen in daimon/, $toml Zeilen mehr in daimon.toml"
  if [[ "$zeilen" -lt 60 ]]; then echo "  FEHLER: $name ist praktisch leer"; rc=1; fi
  if [[ "$rest" -ne 0 ]]; then
    echo "  FEHLER: $name weicht ausserhalb von vad.py vom Basis-Commit ab"; rc=1
  fi
  if [[ "$toml" -lt 4 ]]; then
    echo "  FEHLER: $name hat keinen [ears.vad]-Abschnitt bekommen"; rc=1
  fi
  if ! python3 -m compileall -q "$b/daimon" >/dev/null; then
    echo "  FEHLER: $name ist syntaktisch kaputt"; rc=1
  fi
  find "$b/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
done

# Die Baeume muessen sich VONEINANDER unterscheiden, und zwar in vad.py.
for a in 0 1 2; do
  for b in 1 2; do
    [[ "$a" -lt "$b" ]] || continue
    if diff -q "${BAEUME[$a]}/daimon/ears/vad.py" \
               "${BAEUME[$b]}/daimon/ears/vad.py" >/dev/null 2>/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$a]}") und $(basename "${BAEUME[$b]}") sind identisch"
      rc=1
    fi
  done
done

# Probe aufs Exempel. Nur Plausibilitaet -- das Urteil faellt der Verifizierer.
grep -q 'raise ChunkFehler' "$REFERENZ/daimon/ears/vad.py" \
  || { echo "FEHLER: die Referenz weist gar keinen Chunk ab"; rc=1; }
grep -q 'raise ChunkFehler' "$SCRIPT_DIR/chunk-still-aufgefuellt/daimon/ears/vad.py" \
  && { echo "FEHLER: chunk-still-aufgefuellt weist doch ab"; rc=1; }
grep -q 'if p < ende' "$REFERENZ/daimon/ears/vad.py" \
  || { echo "FEHLER: die Referenz benutzt die Ende-Schwelle nicht"; rc=1; }
grep -q 'if p < ende' "$SCRIPT_DIR/symmetrische-schwelle/daimon/ears/vad.py" \
  && { echo "FEHLER: symmetrische-schwelle benutzt doch die Ende-Schwelle"; rc=1; }

echo
echo "Mutanten: ${MUTANTEN[*]}"
echo "Referenz (kein Gut-Muster): $REFERENZ"
exit $rc
