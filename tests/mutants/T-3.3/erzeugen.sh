#!/usr/bin/env bash
# Erzeugt die T-3.3-Mutanten reproduzierbar -- und dazu einen korrekten
# Referenzbaum, ohne den die Mutanten nichts beweisen.
#
# BASIS IST EIN GEPINNTER COMMIT, NICHT HEAD und nicht der Arbeitsbaum.
# Sobald der Builder `daimon/ears/ring.py` committet, truege `git archive HEAD`
# SEINE Fassung in jeden Mutantenbaum -- die Mutanten waeren dann Abwandlungen
# des Prueflings statt unabhaengiger Gegenproben. Der Basisbaum darf die Datei
# deshalb NICHT enthalten; das wird unten geprueft und bricht ab.
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
#   tests/mutants/T-3.3/erzeugen.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
REFERENZ="$REPO/tests/fixtures/T-3.3-referenz-des-reviewers"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

# f64f568 = "T-3.2.v: 38 Pruefungen, und ein Fall 10 im eigenen Werkzeug".
# Enthaelt capture.py (T-3.1) und vad.py (T-3.2), aber KEIN ring.py.
BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-f64f56851fc00fb960ab4dc7acf817a5eb07e9c9}"
git -C "$REPO" archive "$BASIS_COMMIT" daimon config | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/daimon/ears/__init__.py" ]] \
  || { echo "Basisbaum unvollstaendig: daimon/ears/ fehlt"; exit 1; }
[[ -f "$BASIS/config/daimon.toml" ]] \
  || { echo "Basisbaum unvollstaendig: config/daimon.toml fehlt"; exit 1; }
[[ -e "$BASIS/daimon/ears/ring.py" ]] \
  && { echo "FEHLER: der Basis-Commit enthaelt bereits daimon/ears/ring.py."
       echo "Dann waere die Grundlage der Mutanten die Fassung des Builders,"
       echo "nicht die blind geschriebene des Reviewers. Basis neu pinnen."; exit 1; }
# Und die Gegenprobe zum Abbruch selbst: fehlte auch vad.py, waere schlicht der
# falsche (zu alte) Commit erwischt -- und der Abbruch oben haette nichts gesagt.
[[ -f "$BASIS/daimon/ears/vad.py" ]] \
  || { echo "FEHLER: der Basis-Commit enthaelt kein vad.py -- er liegt vor T-3.2"
       echo "und ist als Basis fuer T-3.3 falsch."; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" "$REFERENZ" <<'PYEOF'
import os
import shutil
import sys

ziel_wurzel, basis, referenz = sys.argv[1], sys.argv[2], sys.argv[3]

# ===========================================================================
# Der gemeinsame Rumpf. Nur die Bloecke @@MLOCK@@ und @@VERWERFEN@@
# unterscheiden die drei Baeume -- alles andere ist identisch, damit ein
# Mutant nur an SEINER Mutation auffaellt.
#
# Platzhalter statt str.format: der Rumpf ist voller geschweifter Klammern,
# und ein verdoppeltes `{{` mehr oder weniger ist genau die Sorte Fehler, die
# einen Mutantenbaum lautlos zu einer unveraenderten Kopie macht.
# ===========================================================================
RUMPF = '''"""T-3.3: Ringpuffer mit Vorlauf.

@@TITEL@@

20 Sekunden bei 16 kHz int16 sind 640 000 Byte, vorab alloziert und mit
`mlock()` festgenagelt: Mikrofonmaterial darf nicht in den Swap. Der Puffer
wird IN PLACE beschrieben; herausgereicht wird nur der Vorlauf.

Und die eigentliche Zusage: FEHLTREFFER HINTERLASSEN NICHTS. Wer beim
abgelehnten Ausloeser bloss den Schreibzeiger zuruecksetzt, hat die Aufnahme
noch im Speicher -- nicht mehr adressiert, aber auffindbar. Also wird
genullt.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import mmap
import os

RATE = 16000
CHUNK = 512
DTYPE = "int16"
BREITE = 2                                # int16
CHUNK_BYTES = CHUNK * BREITE              # 1024
CHUNK_MS = 1000.0 * CHUNK / RATE          # exakt 32,0 ms
SEKUNDEN = 20
RING_BYTES = SEKUNDEN * RATE * BREITE     # 640 000 (dezimal, so steht es im Plan)
RING_CHUNKS = RING_BYTES // CHUNK_BYTES   # 625

# Plankorridor 1,0..1,5 s. Vorgabe in der Mitte.
VORLAUF_MS = 1250.0
VORLAUF_MIN_MS = 1000.0
VORLAUF_MAX_MS = 1500.0

_log = logging.getLogger(__name__)


class MlockFehler(RuntimeError):
    """mlock() hat nicht gegriffen. Das ist laut zu melden, nicht zu schlucken."""


class ChunkFehler(ValueError):
    """Ein Chunk hatte nicht exakt CHUNK Samples."""


_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class Ringpuffer:
    """Fester Ring ueber anonymem mmap. Kein Dateideskriptor, keine Platte."""

    def __init__(self, *, sekunden=SEKUNDEN, vorlauf_ms=VORLAUF_MS,
                 sperren=True):
        self.bytes_gesamt = int(sekunden) * RATE * BREITE
        self.chunks = self.bytes_gesamt // CHUNK_BYTES
        # Vorab alloziert: die volle Groesse steht vor dem ersten Chunk.
        self.puffer = mmap.mmap(-1, self.bytes_gesamt)
        self._sicht = memoryview(self.puffer)
        self.adresse = ctypes.addressof(ctypes.c_char.from_buffer(self.puffer))
        self.vorlauf_ms = float(vorlauf_ms)
        self.vorlauf_chunks = int(self.vorlauf_ms / CHUNK_MS)
        self._schreib = 0
        self._gefuellt = 0
        self.gesperrt = False
@@MLOCK@@
        if not (VORLAUF_MIN_MS <= self.vorlauf_ms <= VORLAUF_MAX_MS):
            _log.warning("vorlauf_ms=%s liegt ausserhalb des Plankorridors "
                         "%s..%s", self.vorlauf_ms, VORLAUF_MIN_MS,
                         VORLAUF_MAX_MS)

    # -- Schreiben ---------------------------------------------------------
    def schreibe(self, chunk):
        """Ein Chunk, in place. Keine Zwischenkopie, keine Liste."""
        roh = memoryview(chunk).cast("B")
        if roh.nbytes != CHUNK_BYTES:
            raise ChunkFehler(
                "Chunk hat %d Byte, erwartet sind %d (%d Samples int16)"
                % (roh.nbytes, CHUNK_BYTES, CHUNK))
        off = self._schreib * CHUNK_BYTES
        self._sicht[off:off + CHUNK_BYTES] = roh
        self._schreib = (self._schreib + 1) % self.chunks
        if self._gefuellt < self.chunks:
            self._gefuellt += 1

    # T-3.1 reicht Bloecke ueber einen `senke`-Callback herein.
    def senke(self, chunk):
        self.schreibe(chunk)

    __call__ = senke

    # -- Ausloesen ---------------------------------------------------------
    def ausloesen(self) -> bytes:
        """Der Vorlauf VOR dem Ausloesezeitpunkt, aelteste zuerst."""
        n = min(self.vorlauf_chunks, self._gefuellt)
        stuecke = []
        for k in range(n, 0, -1):
            idx = (self._schreib - k) % self.chunks
            off = idx * CHUNK_BYTES
            stuecke.append(bytes(self._sicht[off:off + CHUNK_BYTES]))
        return b"".join(stuecke)

    # -- Verwerfen ---------------------------------------------------------
    def verwerfen(self):
        """Abgelehnter Ausloeser: der Ring wird GENULLT, nicht vergessen."""
@@VERWERFEN@@

    # Ein abgelehnter Ausloeser ist derselbe Vorgang -- nur der Name, unter dem
    # ein Aufrufer ihn sucht, ist ein anderer.
    ablehnen = verwerfen
    leeren = verwerfen
'''

MLOCK_KORREKT = '''        if sperren:
            rc = _libc.mlock(ctypes.c_void_p(self.adresse),
                             ctypes.c_size_t(self.bytes_gesamt))
            if rc != 0:
                err = ctypes.get_errno()
                _log.error("mlock(%d Byte) fehlgeschlagen: errno=%d (%s) -- "
                           "der Ring laege im Swap", self.bytes_gesamt, err,
                           os.strerror(err))
                raise MlockFehler(
                    "mlock(%d Byte) fehlgeschlagen: errno=%d (%s). "
                    "RLIMIT_MEMLOCK pruefen (LimitMEMLOCK in der Unit)."
                    % (self.bytes_gesamt, err, os.strerror(err)))
            self.gesperrt = True
'''

MLOCK_MUTANT = '''        if sperren:
            # MUTANT: kein mlock. Das Feld behauptet trotzdem, es haette
            # gegriffen -- genau darum ist ein Selbstbericht kein Nachweis.
            self.gesperrt = True
'''

VERWERFEN_KORREKT = '''        ctypes.memset(self.adresse, 0, self.bytes_gesamt)
        self._schreib = 0
        self._gefuellt = 0
'''

VERWERFEN_MUTANT = '''        # MUTANT: nur der Schreibzeiger. Die Bytes stehen weiter da.
        self._schreib = 0
        self._gefuellt = 0
'''


def baum(pfad, titel, mlock, verwerfen, notiz_text):
    for unter in ("daimon", "config"):
        z = os.path.join(pfad, unter)
        if os.path.exists(z):
            shutil.rmtree(z)
    os.makedirs(pfad, exist_ok=True)
    shutil.copytree(os.path.join(basis, "daimon"), os.path.join(pfad, "daimon"))
    shutil.copytree(os.path.join(basis, "config"), os.path.join(pfad, "config"))
    text = (RUMPF.replace("@@TITEL@@", titel)
                 .replace("@@MLOCK@@", mlock.rstrip("\\n"))
                 .replace("@@VERWERFEN@@", verwerfen.rstrip("\\n")))
    with open(os.path.join(pfad, "daimon", "ears", "ring.py"), "w",
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
    MLOCK_KORREKT,
    VERWERFEN_KORREKT,
    """KEIN MUTANT, KEIN GUT-MUSTER, KEIN LIEFERGEGENSTAND.

Dieser Baum ist die minimal korrekte Umsetzung von T-3.3, geschrieben vom
Reviewer, blind gegen dieselbe Akzeptanzliste wie die echte Implementierung.
Er hat genau einen Zweck: zu zeigen, dass tests/verify/T-3.3.sh gruen werden
KANN. Ein Verifizierer, der das nicht kann, meldet jeden Mutanten als
"erkannt", ohne dessen Mutation je gemessen zu haben -- das ist Fall 12 in
HANDOVER.md und war bei T-2.7 real.

Er liegt bewusst NICHT unter tests/fixtures/known-good/: das Gut-Muster fuer
meta.sh entsteht erst aus dem abgenommenen Stand der echten Implementierung.

Er legt ausserdem den Vertrag offen, gegen den der Verifizierer blind gebaut
wurde -- `Ringpuffer(sekunden=, vorlauf_ms=, sperren=)` mit `schreibe()`,
`ausloesen()` und `verwerfen()`. Weicht die echte Umsetzung davon ab, faellt
das beim Gegenlesen auf und ist dort zu entscheiden, nicht hier. Der
Verifizierer SUCHT den Einstiegspunkt, statt ihn vorzuschreiben.
""",
)

# ===========================================================================
# Mutant 1: nur-zeiger-zurueckgesetzt
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "nur-zeiger-zurueckgesetzt"),
    "MUTANT: Verwerfen setzt den Schreibzeiger, nullt aber nicht.",
    MLOCK_KORREKT,
    VERWERFEN_MUTANT,
    """MUTANT: `verwerfen()` setzt Schreibzeiger und Fuellstand auf null --
und laesst die Aufnahme byteweise stehen.

Von aussen ist das nicht zu unterscheiden: der Ring meldet sich als leer,
`ausloesen()` liefert nichts mehr, jeder Zaehler sagt null. Die letzten 20
Sekunden Mikrofon liegen trotzdem vollstaendig im Speicher -- adressierbar
ueber einen Core-Dump, einen Debugger, `/proc/<pid>/mem`, oder schlicht
ueber den naechsten Chunk, der nur einen Teil davon ueberschreibt.

DAS IST DIE NAHELIEGENDE UMSETZUNG. `self._schreib = 0` ist das, was man
schreibt, wenn man "verwerfen" als Buchhaltung versteht statt als Loeschung.
Und es ist genau der Fall, gegen den die Zusage formuliert ist: "Fehltreffer
hinterlassen NICHTS".

Gefangen von:
  * Abschnitt 3, "nach dem Verwerfen kommt das Muster in den Bytes des
    Puffers NICHT mehr vor" -- er hat es noch 80 000-mal drin
  * Abschnitt 3, dasselbe fuer den nur teilweise gefuellten Ring
  * Abschnitt 3, "der Puffer ist byteweise genullt"

NICHT gefangen von einer Pruefung, die den Fuellstand abfragt oder
`ausloesen()` nach dem Verwerfen aufruft -- beides ist bei ihm korrekt leer.
Genau deshalb misst der Verifizierer die BYTES.
""",
)

# ===========================================================================
# Mutant 2: kein-mlock
# ===========================================================================
baum(
    os.path.join(ziel_wurzel, "kein-mlock"),
    "MUTANT: mlock() wird weggelassen, der Ring behauptet trotzdem, gesperrt zu sein.",
    MLOCK_MUTANT,
    VERWERFEN_KORREKT,
    """MUTANT: kein `mlock()`. Das Feld `gesperrt` steht trotzdem auf True.

Der Ring funktioniert vollstaendig: richtige Groesse, richtiger Vorlauf,
sauberes Nullen beim Verwerfen. Fehlt nur die Sperre gegen den Swap -- und
damit landen bis zu 20 Sekunden Mikrofon irgendwann in `/swapfile`, wo sie
einen Neustart ueberleben.

DIESER MUTANT ZIELT AUF DEN VERIFIZIERER. Er ist gruen bei jeder Pruefung,
die glaubt, was der Prueflung ueber sich selbst sagt (`ring.gesperrt`), und
bei jeder, die `grep mlock` im Quelltext macht (`_libc` und der Import
stehen weiter drin, der Aufruf nicht). HANDOVER.md, T-1.7.v3: ein Builder
schrieb 'face' statt "face" und die eingefrorene Pruefung schwieg.

Der einzige belastbare Nachweis steht im Kernel: `VmLck` in
`/proc/<pid>/status`. Bei ihm bleibt der Wert dort, wo er war.

Gefangen von:
  * Abschnitt 4, "VmLck steigt beim Erzeugen des Rings um mindestens die
    Ringgroesse" -- bei ihm steigt es um 0 kB
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
MUTANTEN=(nur-zeiger-zurueckgesetzt kein-mlock)
BAEUME=("$REFERENZ" "$SCRIPT_DIR/nur-zeiger-zurueckgesetzt" "$SCRIPT_DIR/kein-mlock")

for b in "${BAEUME[@]}"; do
  name="$(basename "$b")"
  if [[ ! -f "$b/daimon/ears/ring.py" ]]; then
    echo "FEHLER: $name hat kein daimon/ears/ring.py"; rc=1; continue
  fi
  zeilen="$(wc -l <"$b/daimon/ears/ring.py")"
  # Ausserhalb von ring.py darf sich nichts vom Basis-Commit unterscheiden --
  # sonst misst der Verifizierer spaeter etwas anderes als die Mutation.
  rest="$(diff -r -x ring.py -x __pycache__ "$BASIS/daimon" "$b/daimon" | grep -c '^[<>]')"
  toml="$(diff "$BASIS/config/daimon.toml" "$b/config/daimon.toml" | grep -c '^[<>]')"
  echo "$name: $zeilen Zeilen ring.py, $rest sonstige Abweichungen in daimon/, $toml in daimon.toml"
  if [[ "$zeilen" -lt 80 ]]; then echo "  FEHLER: $name ist praktisch leer"; rc=1; fi
  if [[ "$rest" -ne 0 ]]; then
    echo "  FEHLER: $name weicht ausserhalb von ring.py vom Basis-Commit ab"; rc=1
  fi
  if [[ "$toml" -ne 0 ]]; then
    echo "  FEHLER: $name weicht in config/daimon.toml vom Basis-Commit ab"; rc=1
  fi
  if ! python3 -m compileall -q "$b/daimon" >/dev/null; then
    echo "  FEHLER: $name ist syntaktisch kaputt"; rc=1
  fi
  find "$b/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
done

# Die Baeume muessen sich VONEINANDER unterscheiden, und zwar in ring.py.
for a in 0 1 2; do
  for b in 1 2; do
    [[ "$a" -lt "$b" ]] || continue
    if diff -q "${BAEUME[$a]}/daimon/ears/ring.py" \
               "${BAEUME[$b]}/daimon/ears/ring.py" >/dev/null 2>/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$a]}") und $(basename "${BAEUME[$b]}") sind identisch"
      rc=1
    fi
  done
done

# Probe aufs Exempel. Nur Plausibilitaet -- das Urteil faellt der Verifizierer.
grep -q 'ctypes.memset' "$REFERENZ/daimon/ears/ring.py" \
  || { echo "FEHLER: die Referenz nullt gar nicht"; rc=1; }
grep -q 'ctypes.memset' "$SCRIPT_DIR/nur-zeiger-zurueckgesetzt/daimon/ears/ring.py" \
  && { echo "FEHLER: nur-zeiger-zurueckgesetzt nullt doch"; rc=1; }
grep -q '_libc.mlock' "$REFERENZ/daimon/ears/ring.py" \
  || { echo "FEHLER: die Referenz ruft mlock gar nicht"; rc=1; }
grep -q '_libc.mlock' "$SCRIPT_DIR/kein-mlock/daimon/ears/ring.py" \
  && { echo "FEHLER: kein-mlock ruft mlock doch"; rc=1; }
# Und die Gegenprobe zum grep selbst: `kein-mlock` muss das WORT mlock weiter
# enthalten. Sonst waere er auch fuer einen Quelltext-grep auffaellig, und der
# Nachweis "nur VmLck faengt ihn" waere geschenkt.
grep -q 'mlock' "$SCRIPT_DIR/kein-mlock/daimon/ears/ring.py" \
  || { echo "FEHLER: kein-mlock erwaehnt mlock nirgends mehr -- zu leicht"; rc=1; }

echo
echo "Mutanten: ${MUTANTEN[*]}"
echo "Referenz (kein Gut-Muster): $REFERENZ"
exit $rc
