#!/usr/bin/env bash
# Verifizierer fuer T-3.2: VAD mit Hysterese.
#
# ============================================================================
# WARUM DIESER VERIFIZIERER NICHT TUT, WAS DER PLAN SAGT
# ============================================================================
#
# Der Plan verlangt "einen Test mit synthetischem Signal: Sprache mit 200 ms
# Pause ergibt ein Segment, mit 800 ms Pause zwei". Das laesst sich so nicht
# bauen, und das ist keine Bequemlichkeit, sondern ein Messfehler im Auftrag:
#
#   `pysilero-vad` ist ein MODELL, kein Schwellwertdetektor. Ein synthetischer
#   Ton ist fuer Silero keine Sprache. Gemessen (Akzeptanzliste, hier unten
#   in Abschnitt 5 NACHGEMESSEN): weisses Rauschen 0,0134, Stille 0,0017.
#   Ein Test, der einen Sinuston hineinschiebt und zwei Segmente erwartet,
#   findet null -- und ist nur gruen zu bekommen, indem man `einsatz` so weit
#   senkt, bis Rauschen als Sprache durchgeht. Der Test wuerde also die Zusage
#   zerstoeren, die er pruefen soll.
#
# Deshalb sind hier DREI Dinge GETRENNT gemessen, und keins ersetzt ein
# anderes:
#
#   Abschnitt 3  DIE ZUSTANDSMASCHINE, gegen eingespeiste
#                Wahrscheinlichkeitsfolgen. Kein Modell, kein Audio, kein
#                Zufall -- 200 ms ergibt eins, 800 ms ergibt zwei, und die
#                ASYMMETRIE selbst: eine Folge, die kurz unter 0,35 faellt
#                und wieder steigt, darf kein Segment beenden. Das ist
#                "Wortenden nicht abschneiden", und es ist der Kern des Tasks.
#
#   Abschnitt 4  DIE MODELLANBINDUNG, gegen einen eingesetzten Spion-Detektor.
#                Gemessen wird, WAS AM MODELL ANKOMMT: exakt 512 Samples,
#                1024 Bytes, byteweise unveraendert. Ein abweichender Chunk
#                muss ein FEHLER sein, kein stilles Auffuellen -- gemessen am
#                Verhalten (fliegt eine Ausnahme? kam beim Modell trotzdem ein
#                aufgefuellter Chunk an?), nicht am Quelltext. Ein
#                Verifizierer, der `grep -c 512` macht, ist an der
#                Schreibweise zu umgehen (HANDOVER.md, T-1.7.v3).
#
#   Abschnitt 5  DAS ECHTE MODELL. Rauschen und Stille durch die echte
#                Silero-Kette. Erwartet werden NULL Segmente. Diese Pruefung
#                ist das Gegengewicht zu allen anderen: sie wird rot, sobald
#                jemand die Schwellen senkt, um irgendetwas anderes gruen zu
#                bekommen.
#
#   Abschnitt 6  DIE KONFIGURIERBARKEIT, und zwar an der WIRKUNG. Dieselbe
#                Folge, einmal mit nachlauf_ms=300 und einmal mit 500 --
#                beides Werte innerhalb der Plangrenzen -- muss zu
#                UNTERSCHIEDLICHEN Segmentzahlen fuehren. Ein
#                Konfigurationswert, den niemand liest, ist keiner.
#
# ============================================================================
# POSITIVKONTROLLEN
# ============================================================================
#
# "Ein Segment" ist ohne Gegenprobe keine Aussage: eine Umsetzung, die immer
# genau ein Segment liefert (oder deren Anbindung schlicht kaputt ist), waere
# damit gruen. Im selben Lauf wird deshalb gezeigt, dass dieselbe Messkette
#   * ZWEI Segmente finden kann (800 ms),
#   * DREI Segmente finden kann (zwei lange Pausen),
#   * NULL Segmente liefern kann (eine Folge, die nie ueber `einsatz` steigt),
#   * und dass der Audio-Pfad ueberhaupt bis zum Modell durchreicht, bevor
#     "abweichender Chunk wird abgewiesen" irgendetwas bedeutet.
#
# Ohne die letzte Kontrolle waere "der falsche Chunk fliegt raus" auch bei
# einer vollstaendig toten Anbindung gruen (Fall 12 in HANDOVER.md).
#
# ============================================================================
# DER VERTRAG, gegen den hier blind geprueft wird
# ============================================================================
#
# Die Akzeptanzliste sagt: `segmentieren()` muss eine Folge von
# Wahrscheinlichkeiten entgegennehmen koennen (oder einen einspeisbaren
# Detektor). Mehr steht dort nicht, und der Verifizierer ist blind gegen die
# Implementierung entstanden. Er sucht deshalb den Einstiegspunkt, statt ihn
# zu erraten, und PROTOKOLLIERT, welche Aufrufform gegriffen hat:
#
#   * eine Modulfunktion namens segmentieren / segmentiere / segment
#   * oder eine Klasse VAD / Vad / Segmentierer / Hysterese mit einer
#     solchen Methode
#   * Konfiguration als Schluesselwortargumente einsatz= / ende= /
#     nachlauf_ms= am Aufruf oder am Konstruktor
#   * Elemente der Folge: floats (Wahrscheinlichkeiten) oder
#     bytes/bytearray/ndarray (Audio-Chunks)
#
# Findet er nichts davon, ist das ROT und die Meldung sagt genau, was das
# Modul stattdessen anbietet. Das ist eine Vertragsabweichung, kein
# Messfehler -- aber sie gehoert benannt und nicht kaschiert.
#
# Aufruf:
#   tests/verify/T-3.2.sh
#   DAIMON_FIXTURE=<baum> tests/verify/T-3.2.sh   # Baum mit eigenem daimon/
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"

MAX_SECS="${DAIMON_T32_MAX_SECS:-180}"   # harte Obergrenze je Treiberlauf

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
laut() { echo "  !! $*"; }

echo "T-3.2 — VAD mit Hysterese: Wortenden nicht abschneiden"
echo "  Baum: $TARGET"
echo "  Interpreter: $PY"

# =============================================================================
# 0. Voraussetzungen
# =============================================================================
echo
echo "--- 0. Voraussetzungen ---"
chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "timeout vorhanden" "$(command -v timeout >/dev/null && echo ja || echo nein)" ja
chk "Interpreter vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja

VERSION="$("$PY" -c 'from importlib.metadata import version; print(version("pysilero-vad"))' 2>/dev/null)"
echo "  pysilero-vad: ${VERSION:-(fehlt)}"
chk "pysilero-vad importierbar" \
  "$("$PY" -c 'import pysilero_vad' >/dev/null 2>/dev/null && echo ja || echo nein)" ja
# >= 3.4.0, als Zahlenvergleich und nicht als Zeichenkette: "3.10" ist groesser
# als "3.4", aber nicht als Text.
chk "pysilero-vad >= 3.4.0" \
  "$("$PY" -c 'import sys
from importlib.metadata import version
t=tuple(int(x) for x in version("pysilero-vad").split(".")[:3])
print("ja" if t>=(3,4,0) else "nein")' 2>/dev/null)" ja
chk "das Modell arbeitet in 512-Sample-Chunks (chunk_samples)" \
  "$("$PY" -c 'from pysilero_vad import SileroVoiceActivityDetector as S; print(S.chunk_samples())' 2>/dev/null)" 512
chk "der Baum bringt daimon/ears/vad.py mit" \
  "$([[ -f "$TARGET/daimon/ears/vad.py" ]] && echo ja || echo nein)" ja

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "T-3.2: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen."
  exit 1
fi

RT="$(mktemp -d)"
trap 'rm -rf -- "$RT"' EXIT INT TERM

# =============================================================================
# Der gemeinsame Vertragssucher
# =============================================================================
cat >"$RT/kontrakt.py" <<'PYEOF'
"""Sucht den Einstiegspunkt von daimon.ears.vad, statt ihn zu erraten.

Der Verifizierer ist blind gegen die Implementierung entstanden. Die
Akzeptanzliste nennt `segmentieren()` und "eine Folge von
Wahrscheinlichkeiten"; alles Weitere ist Formsache. Diese Datei probiert die
plausiblen Formen durch und meldet, WELCHE gegriffen hat -- damit im
Protokoll steht, was tatsaechlich gemessen wurde.
"""
import inspect

NAMEN_FUNK = ("segmentieren", "segmentiere", "segment", "segmentiere_folge")
# Der Audio-Pfad darf ein EIGENER Einstiegspunkt sein -- und ist es in der
# gelieferten Umsetzung auch (`segmentiere_audio`). Eine frühere Fassung
# dieses Verifizierers kannte nur `segmentieren` und schob Bytes dort hinein;
# das ergab einen TypeError, und die Prüfung "ein abweichender Chunk wird
# ABGEWIESEN" wurde daraufhin GRUEN -- aus dem falschen Grund, weil auch der
# gute Chunk nie ankam. Fall 10 in HANDOVER.md, hier am eigenen Werkzeug.
# Deshalb: der Audio-Einstieg wird gesondert gesucht, und ausgewaehlt wird
# nur, wer drei gute Chunks tatsaechlich bis zum Modell durchreicht.
NAMEN_AUDIO = ("segmentiere_audio", "segmentieren_audio", "audio_segmentieren",
               "segment_audio", "aus_audio", "verarbeite_audio",
               "segmentieren", "segmentiere", "segment")
NAMEN_KLASSE = ("VAD", "Vad", "VadSegmentierer", "Segmentierer", "Hysterese",
                "VadHysterese", "Erkenner")
CFG_SCHLUESSEL = ("einsatz", "ende", "nachlauf_ms")


class KeinVertrag(Exception):
    pass


def _liste(x):
    if x is None:
        raise TypeError("segmentieren() lieferte None")
    if isinstance(x, (list, tuple)):
        return list(x)
    if hasattr(x, "__iter__") and not isinstance(x, (str, bytes)):
        return list(x)
    raise TypeError(f"segmentieren() lieferte {type(x).__name__}, keine Folge")


def formen(modul, namen=NAMEN_FUNK):
    """Liefert Kandidaten als (beschreibung, bauer). bauer(cfg) -> aufruf."""
    aus = []
    for n in namen:
        f = getattr(modul, n, None)
        if callable(f) and not inspect.isclass(f):
            def bau_funk_kw(cfg, f=f, n=n):
                return (lambda folge: _liste(f(folge, **cfg)), True)

            def bau_funk_pur(cfg, f=f, n=n):
                return (lambda folge: _liste(f(folge)), not cfg)

            aus.append((f"funktion {n}(folge, **cfg)", bau_funk_kw))
            aus.append((f"funktion {n}(folge)", bau_funk_pur))
    for kn in NAMEN_KLASSE:
        K = getattr(modul, kn, None)
        if not inspect.isclass(K):
            continue
        for mn in namen:
            if not hasattr(K, mn):
                continue

            def bau_kls_ctor(cfg, K=K, mn=mn):
                obj = K(**cfg)
                return (lambda folge: _liste(getattr(obj, mn)(folge)), True)

            def bau_kls_meth(cfg, K=K, mn=mn):
                obj = K()
                return (lambda folge: _liste(getattr(obj, mn)(folge, **cfg)), True)

            def bau_kls_pur(cfg, K=K, mn=mn):
                obj = K()
                return (lambda folge: _liste(getattr(obj, mn)(folge)), not cfg)

            aus.append((f"klasse {kn}(**cfg).{mn}(folge)", bau_kls_ctor))
            aus.append((f"klasse {kn}().{mn}(folge, **cfg)", bau_kls_meth))
            aus.append((f"klasse {kn}().{mn}(folge)", bau_kls_pur))
    return aus


def binde(modul, probe=None):
    """Findet die erste Form, die mit einer harmlosen Probe funktioniert.

    Die Probe ist eine reine Wahrscheinlichkeitsfolge -- sie darf das Modell
    gar nicht erst anfassen. Zurueck kommt eine Funktion
    `aufruf(folge, **cfg)`.
    """
    if probe is None:
        probe = [0.9] * 5 + [0.02] * 30 + [0.9] * 5
    kandidaten = formen(modul)
    fehler = []
    gewaehlt = None
    for besch, bauer in kandidaten:
        try:
            f, nimmt_cfg = bauer({})
            f(list(probe))
        except Exception as exc:
            fehler.append(f"{besch}: {type(exc).__name__}: {exc}")
            continue
        gewaehlt = (besch, bauer)
        break
    if gewaehlt is None:
        angebot = sorted(a for a in dir(modul) if not a.startswith("_"))
        raise KeinVertrag(
            "Kein brauchbarer Einstiegspunkt in daimon.ears.vad gefunden.\n"
            "Das Modul bietet an: " + ", ".join(angebot) + "\n"
            "Versuche:\n  " + "\n  ".join(fehler)
        )
    besch, bauer = gewaehlt

    # Nimmt dieselbe Form auch Konfiguration entgegen? Wenn nicht, wird eine
    # cfg-faehige Form gesucht -- und wenn es keine gibt, ist das ein Befund.
    cfg_probe = {"einsatz": 0.5, "ende": 0.35, "nachlauf_ms": 400}
    cfg_besch, cfg_bauer = None, None
    for b2, bauer2 in kandidaten:
        try:
            f2, _ = bauer2(dict(cfg_probe))
            f2(list(probe))
        except Exception:
            continue
        cfg_besch, cfg_bauer = b2, bauer2
        break

    def aufruf(folge, **cfg):
        if cfg:
            if cfg_bauer is None:
                raise KeinVertrag("keine Aufrufform nimmt einsatz/ende/nachlauf_ms")
            f, _ = cfg_bauer(cfg)
        else:
            f, _ = bauer({})
        return f(folge)

    return aufruf, besch, cfg_besch
PYEOF

# =============================================================================
# Treiber 1: Vertrag, Zustandsmaschine, Modellanbindung (mit Spion)
# =============================================================================
cat >"$RT/treiber_spion.py" <<'PYEOF'
"""Misst die Zustandsmaschine und die Modellanbindung -- ohne echtes Modell.

DER SPION WIRD VOR DEM IMPORT DES PRUEFLINGS EINGESETZT. Damit ist es egal,
ob die Implementierung `import pysilero_vad` schreibt oder
`from pysilero_vad import SileroVoiceActivityDetector`: in beiden Faellen
bindet sie den Spion, weil das From-Import erst beim Import des Prueflings
aufgeloest wird. Das ist eine Beobachtung am laufenden Prozess, kein grep.
"""
import importlib
import json
import logging
import os
import sys
import traceback

BAUM = sys.argv[1]
RT = sys.argv[2]
sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

import pysilero_vad  # noqa: E402


class Spion:
    """Ersatz fuer SileroVoiceActivityDetector. Protokolliert, was ankommt."""

    aufrufe = []          # Laenge der uebergebenen Puffer in Bytes
    roh = []              # die Puffer selbst
    antworten = []        # abzuarbeitendes Skript
    vorgabe = 0.9
    erzeugt = 0

    def __init__(self, *a, **k):
        Spion.erzeugt += 1

    @staticmethod
    def chunk_samples():
        return 512

    @staticmethod
    def chunk_bytes():
        return 1024

    def reset(self):
        pass

    def _nimm(self, audio):
        b = bytes(audio)
        Spion.aufrufe.append(len(b))
        Spion.roh.append(b)
        if Spion.antworten:
            return float(Spion.antworten.pop(0))
        return float(Spion.vorgabe)

    def __call__(self, audio):
        return self._nimm(audio)

    # Die weiteren Namen der echten API -- falls die Implementierung sie nutzt.
    def process_chunk(self, audio):
        return self._nimm(audio)

    def process_array(self, audio):
        return self._nimm(audio)

    def process_samples(self, audio):
        return self._nimm(audio)

    def process_chunks(self, audio):
        return [self._nimm(audio)]


pysilero_vad.SileroVoiceActivityDetector = Spion

import kontrakt  # noqa: E402

ERG = {"ok": True}


def melde(**kw):
    ERG.update(kw)


try:
    modul = importlib.import_module("daimon.ears.vad")
except Exception as exc:
    print(json.dumps({"ok": False, "phase": "import",
                      "fehler": f"{type(exc).__name__}: {exc}",
                      "spur": traceback.format_exc()[-1200:]}))
    raise SystemExit(0)

melde(datei=os.path.abspath(modul.__file__),
      angebot=sorted(a for a in dir(modul) if not a.startswith("_")))

try:
    ruf, form, cfg_form = kontrakt.binde(modul)
except Exception as exc:
    melde(ok=False, phase="vertrag", fehler=str(exc))
    print(json.dumps(ERG))
    raise SystemExit(0)

melde(form=form, cfg_form=cfg_form or "(keine)")

# --- deklarierte Vorgaben, rein diagnostisch --------------------------------
melde(deklariert={
    n: getattr(modul, n) for n in
    ("RATE", "CHUNK", "BLOCK", "DTYPE", "EINSATZ", "ENDE", "NACHLAUF_MS",
     "CHUNK_MS")
    if isinstance(getattr(modul, n, None), (int, float, str))
})

# ===========================================================================
# Die Folgen. Ein Chunk sind 512 Samples bei 16 kHz -- also exakt 32,0 ms.
# 200 ms sind kein Vielfaches davon; gefahren werden 6 Chunks = 192 ms.
# 800 ms sind exakt 25 Chunks.
# ===========================================================================
CHUNK_MS = 32.0


def folge(*stuecke):
    aus = []
    for wert, n in stuecke:
        aus.extend([float(wert)] * int(n))
    return aus


SZENARIEN = {
    # Kriterium 4 des Plans, deterministisch:
    "pause_192ms": folge((0.9, 10), (0.05, 6), (0.9, 10)),
    "pause_800ms": folge((0.9, 10), (0.05, 25), (0.9, 10)),
    # Positivkontrolle: die Kette kann auch DREI finden.
    "zwei_pausen": folge((0.9, 10), (0.05, 25), (0.9, 10), (0.05, 25), (0.9, 10)),
    # Die Asymmetrie der SCHWELLE: 0,40 liegt unter dem Einsatz (0,5), aber
    # ueber dem Ende (0,35). Ein symmetrischer Schwellwert schneidet hier.
    "delle_040": folge((0.9, 10), (0.40, 20), (0.9, 10)),
    # Ein echtes Wortende: die Wahrscheinlichkeit rutscht kurz durch, faellt
    # drei Chunks (96 ms) unter 0,35 und kommt zurueck. "...und dann GING er".
    "wortende": ([0.9] * 10 + [0.60, 0.45, 0.30, 0.25, 0.28, 0.45, 0.70]
                 + [0.9] * 10),
    # Positivkontrolle NULL: nie ueber dem Einsatz -- darf nichts finden.
    "nie_ueber_einsatz": folge((0.45, 30)),
    # Positivkontrolle EINS daneben, damit "null" nicht am toten Aufbau liegt.
    "durchgehend": folge((0.9, 30)),
}

zaehler = {}
fehler = {}
for name, f in SZENARIEN.items():
    try:
        zaehler[name] = len(ruf(list(f)))
    except Exception as exc:
        zaehler[name] = -1
        fehler[name] = f"{type(exc).__name__}: {exc}"
melde(segmente=zaehler, szenario_fehler=fehler)

# Der reine Wahrscheinlichkeitspfad darf das Modell NIE angefasst haben.
melde(modell_aufrufe_wahrscheinlichkeitspfad=len(Spion.aufrufe))

# ===========================================================================
# Konfigurierbarkeit -- an der WIRKUNG, nicht am Vorhandensein eines Feldes.
# Pause von 13 Chunks = 416 ms. Mit nachlauf_ms=300 (Plangrenze unten) endet
# das Segment, mit nachlauf_ms=500 (Plangrenze oben) nicht. Beide Werte sind
# nach dem Plan zulaessig -- der Test misst also die Konfiguration, nicht
# ihre Missachtung.
# ===========================================================================
CFG_FOLGE = folge((0.9, 10), (0.05, 13), (0.9, 10))
cfg_erg = {}
for nl in (300, 500):
    try:
        cfg_erg[str(nl)] = len(ruf(list(CFG_FOLGE), einsatz=0.5, ende=0.35,
                                   nachlauf_ms=nl))
    except Exception as exc:
        cfg_erg[str(nl)] = -1
        cfg_erg[f"fehler_{nl}"] = f"{type(exc).__name__}: {exc}"
melde(cfg_nachlauf=cfg_erg)

# Und die Gegenprobe zur Gegenprobe: ohne Konfiguration muss dieselbe Folge
# ein Ergebnis liefern, das zu den Vorgaben passt (Nachlauf 300..500 ms
# ergibt bei 416 ms Pause: 300 -> 2, 400 -> 2, 500 -> 1; also 1 ODER 2).
try:
    melde(cfg_vorgabe=len(ruf(list(CFG_FOLGE))))
except Exception as exc:
    melde(cfg_vorgabe=-1, cfg_vorgabe_fehler=str(exc))

# Eine Konfiguration UNTERHALB der Plangrenzen gehoert abgewiesen oder
# mindestens geloggt (Akzeptanzliste). Beides zaehlt; Schweigen nicht.
class Faenger(logging.Handler):
    def __init__(self):
        super().__init__()
        self.sätze = []

    def emit(self, record):
        self.sätze.append(record.getMessage())


faenger = Faenger()
logging.getLogger().addHandler(faenger)
logging.getLogger().setLevel(logging.DEBUG)
import warnings  # noqa: E402

grenzverletzung = {"reaktion": "schweigen", "text": ""}
with warnings.catch_warnings(record=True) as ws:
    warnings.simplefilter("always")
    try:
        ruf(list(CFG_FOLGE), einsatz=0.2, ende=0.05, nachlauf_ms=50)
    except Exception as exc:
        grenzverletzung = {"reaktion": "abgewiesen",
                           "text": f"{type(exc).__name__}: {exc}"}
    else:
        if faenger.sätze:
            grenzverletzung = {"reaktion": "geloggt", "text": " | ".join(faenger.sätze)[:300]}
        elif ws:
            grenzverletzung = {"reaktion": "geloggt",
                               "text": " | ".join(str(w.message) for w in ws)[:300]}
logging.getLogger().removeHandler(faenger)
melde(grenzverletzung=grenzverletzung)

# ===========================================================================
# Modellanbindung. Ab hier zaehlt, WAS AM MODELL ANKOMMT.
# ===========================================================================
GUT = bytes(bytearray(512 * 2))          # 512 Samples int16 = 1024 Bytes
import struct  # noqa: E402

GUT = struct.pack("<512h", *[(i * 37) % 3000 - 1500 for i in range(512)])
KURZ = struct.pack("<400h", *([100] * 400))
LANG = struct.pack("<600h", *([100] * 600))

audio = {}
Spion.vorgabe = 0.9
Spion.antworten = []

# Der Audio-Einstieg wird GESONDERT gesucht, und die Auswahl ist selbst eine
# Positivkontrolle: gewaehlt wird nur eine Form, bei der drei gute Chunks
# tatsaechlich drei Modellaufrufe ausloesen. Sonst misst der Abschnitt
# hinterher "der falsche Chunk wurde abgewiesen" an einem Pfad, der gar
# keinen Chunk annimmt -- und wird gruen, weil nichts funktioniert.
audio_ruf, audio_form, versuche = None, "(keine)", []
for besch, bauer in kontrakt.formen(modul, kontrakt.NAMEN_AUDIO):
    Spion.aufrufe.clear(); Spion.roh.clear()
    try:
        f, _ = bauer({})
        f([GUT] * 3)
    except Exception as exc:
        versuche.append(f"{besch}: {type(exc).__name__}: {exc}"[:160])
        continue
    if len(Spion.aufrufe) != 3:
        versuche.append(f"{besch}: erreichte das Modell {len(Spion.aufrufe)}x statt 3x")
        continue
    audio_ruf, audio_form = (lambda folge, b=bauer: b({})[0](folge)), besch
    break
audio["form"] = audio_form
audio["verworfene_formen"] = versuche

# (a) Positivkontrolle: reicht der Audio-Pfad ueberhaupt bis zum Modell?
Spion.aufrufe.clear(); Spion.roh.clear()
if audio_ruf is None:
    audio["gut_fehler"] = "kein Audio-Einstiegspunkt gefunden"
    audio["gut_segmente"] = -1
    audio["gut_aufrufe"] = 0
    audio["gut_laengen"] = []
    audio["gut_unveraendert"] = False
else:
    try:
        n = len(audio_ruf([GUT] * 20))
        audio["gut_segmente"] = n
        audio["gut_aufrufe"] = len(Spion.aufrufe)
        audio["gut_laengen"] = sorted(set(Spion.aufrufe))
        audio["gut_unveraendert"] = all(b == GUT for b in Spion.roh)
    except Exception as exc:
        audio["gut_fehler"] = f"{type(exc).__name__}: {exc}"
        audio["gut_segmente"] = -1
        audio["gut_aufrufe"] = len(Spion.aufrufe)
        audio["gut_laengen"] = sorted(set(Spion.aufrufe))
        audio["gut_unveraendert"] = False

# (b) Ein zu KURZER Chunk. Erwartet: Ausnahme -- und das Modell darf ihn
#     nicht doch, aufgefuellt, zu sehen bekommen.
for etikett, puffer in (("kurz", KURZ), ("lang", LANG)):
    Spion.aufrufe.clear(); Spion.roh.clear()
    if audio_ruf is None:
        audio[f"{etikett}_reaktion"] = "nicht-messbar"
        audio[f"{etikett}_aufrufe"] = 0
        audio[f"{etikett}_laengen"] = []
        audio[f"{etikett}_gepolstert"] = -1
        continue
    try:
        n = len(audio_ruf([GUT] * 5 + [puffer] + [GUT] * 5))
        audio[f"{etikett}_reaktion"] = "durchgelassen"
        audio[f"{etikett}_segmente"] = n
    except Exception as exc:
        audio[f"{etikett}_reaktion"] = "abgewiesen"
        audio[f"{etikett}_ausnahme"] = f"{type(exc).__name__}: {exc}"[:200]
    audio[f"{etikett}_aufrufe"] = len(Spion.aufrufe)
    audio[f"{etikett}_laengen"] = sorted(set(Spion.aufrufe))
    # Die eigentliche Frage: kam beim Modell ein Puffer an, der WEDER unser
    # guter noch der abweichende war? Das ist stilles Auffuellen.
    audio[f"{etikett}_gepolstert"] = sum(
        1 for b in Spion.roh if b != GUT and b != puffer)

# (c) Und danach muss die Kette noch leben -- sonst waere "abgewiesen" nur
#     ein anderes Wort fuer "kaputt".
Spion.aufrufe.clear(); Spion.roh.clear()
try:
    audio["danach_segmente"] = len(audio_ruf([GUT] * 20))
    audio["danach_aufrufe"] = len(Spion.aufrufe)
except Exception as exc:
    audio["danach_segmente"] = -1
    audio["danach_fehler"] = f"{type(exc).__name__}: {exc}"

# (d) Diagnostisch: nimmt der Pfad auch ein numpy-int16-Array?
try:
    import numpy as np
    arr = np.frombuffer(GUT, dtype=np.int16).copy()
    Spion.aufrufe.clear(); Spion.roh.clear()
    audio_ruf([arr] * 5)
    audio["ndarray_laengen"] = sorted(set(Spion.aufrufe))
except Exception as exc:
    audio["ndarray_laengen"] = f"{type(exc).__name__}"

melde(audio=audio)
print(json.dumps(ERG, default=str))
PYEOF

# =============================================================================
# Treiber 2: das ECHTE Modell
# =============================================================================
cat >"$RT/treiber_echt.py" <<'PYEOF'
"""Faehrt Rauschen und Stille durch die ECHTE Silero-Kette.

Erwartet werden NULL Segmente. Diese Pruefung ist das Gegengewicht zu allen
anderen: sie wird rot, sobald jemand `einsatz` senkt, damit ein
Audio-basierter Test gruen wird. Die Akzeptanzliste nennt Messwerte
(Rauschen 0,0134, Stille 0,0017) -- die werden hier nachgemessen und stehen
im Protokoll.
"""
import importlib
import json
import os
import random
import struct
import sys
import traceback

BAUM = sys.argv[1]
RT = sys.argv[2]
sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

ERG = {"ok": True}
try:
    from pysilero_vad import SileroVoiceActivityDetector
    modul = importlib.import_module("daimon.ears.vad")
    import kontrakt
except Exception as exc:
    print(json.dumps({"ok": False, "phase": "import",
                      "fehler": f"{type(exc).__name__}: {exc}",
                      "spur": traceback.format_exc()[-1200:]}))
    raise SystemExit(0)

ERG["datei"] = os.path.abspath(modul.__file__)
try:
    ruf, form, cfg_form = kontrakt.binde(modul)
except Exception as exc:
    print(json.dumps({"ok": False, "phase": "vertrag", "fehler": str(exc)}))
    raise SystemExit(0)
ERG["form"] = form

# Auch hier wird der Audio-Einstieg gesondert gesucht -- er darf ein anderer
# sein als der fuer Wahrscheinlichkeiten. Gewaehlt wird die erste Form, die
# drei echte Chunks ohne Ausnahme annimmt.
def binde_audio(modul, probe):
    for besch, bauer in kontrakt.formen(modul, kontrakt.NAMEN_AUDIO):
        try:
            f, _ = bauer({})
            f(list(probe))
        except Exception:
            continue
        return (lambda folge, b=bauer: b({})[0](folge)), besch
    return None, "(keine)"


rng = random.Random(20260802)
rausch = [struct.pack("<512h", *[rng.randint(-8000, 8000) for _ in range(512)])
          for _ in range(40)]
stille = [struct.pack("<512h", *([0] * 512)) for _ in range(40)]

det = SileroVoiceActivityDetector()
ERG["p_rausch_max"] = round(max(det(c) for c in rausch), 4)
det.reset()
ERG["p_stille_max"] = round(max(det(c) for c in stille), 4)

audio_ruf, ERG["audio_form"] = binde_audio(modul, stille[:3])

for etikett, chunks in (("rausch", rausch), ("stille", stille)):
    try:
        ERG[f"segmente_{etikett}"] = len(audio_ruf(list(chunks)))
    except Exception as exc:
        ERG[f"segmente_{etikett}"] = -1
        ERG[f"fehler_{etikett}"] = f"{type(exc).__name__}: {exc}"[:200]

# Positivkontrolle im SELBEN Prozess: die Messkette kann ueberhaupt ein
# Segment melden. Ohne sie waere "null Segmente bei Rauschen" auch bei einer
# vollstaendig toten Anbindung gruen.
try:
    ERG["segmente_wahrscheinlichkeit"] = len(ruf([0.9] * 30))
except Exception as exc:
    ERG["segmente_wahrscheinlichkeit"] = -1
    ERG["fehler_wahrscheinlichkeit"] = str(exc)[:200]

print(json.dumps(ERG, default=str))
PYEOF

lauf() {  # $1 = Skript
  timeout --foreground --signal=TERM --kill-after=5s "${MAX_SECS}s" \
    "$PY" -P "$RT/$1" "$TARGET" "$RT" 2>"$RT/${1%.py}.log"
}

# =============================================================================
# 1. Bindung: aus dem geprueften Baum, und der Vertrag greift
# =============================================================================
echo
echo "--- 1. Bindung ---"
lauf treiber_spion.py >"$RT/spion.json"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/spion.json"; then
  laut "Der Treiber hat keine auswertbare Antwort geliefert."
  laut "Protokoll:"; tail -20 "$RT/treiber_spion.log"
  chk "1 Treiber laeuft" nein ja
  echo; echo "T-3.2: FEHLGESCHLAGEN"; exit 1
fi
S="$(cat "$RT/spion.json")"
echo "  Modul bietet an: $(jq -r '.angebot // [] | join(", ")' <<<"$S")"
if [[ "$(jq -r '.ok' <<<"$S")" != true ]]; then
  laut "Phase: $(jq -r '.phase' <<<"$S")"
  laut "$(jq -r '.fehler' <<<"$S")"
  [[ "$(jq -r '.spur // empty' <<<"$S")" ]] && echo "$(jq -r '.spur' <<<"$S")"
fi
chk "1 daimon.ears.vad laedt und der Vertrag greift" "$(jq -r '.ok' <<<"$S")" true
if [[ "$(jq -r '.ok' <<<"$S")" != true ]]; then
  echo; echo "T-3.2: FEHLGESCHLAGEN — ohne Einstiegspunkt ist nichts gemessen."
  exit 1
fi
geladen="$(jq -r '.datei' <<<"$S")"
echo "  geladen aus: $geladen"
# Fall 12 in HANDOVER.md: eine Positivkontrolle, die nie gruen werden kann,
# meldet jeden Mutanten als "erkannt", ohne dessen Mutation zu messen.
chk "1 POSITIVKONTROLLE: der Pruefling stammt AUS DEM GEPRUEFTEN BAUM" \
  "$([[ "$geladen" == "$TARGET"/* ]] && echo ja || echo "aus_$geladen")" ja
echo "  gewaehlte Aufrufform: $(jq -r '.form' <<<"$S")"
echo "  Aufrufform mit Konfiguration: $(jq -r '.cfg_form' <<<"$S")"
echo "  deklarierte Vorgaben: $(jq -c '.deklariert' <<<"$S")"

# =============================================================================
# 2. Die Positivkontrollen der Messkette
# =============================================================================
echo
echo "--- 2. Positivkontrollen der Messkette ---"
seg() { jq -r --arg n "$1" '.segmente[$n] // "?"' <<<"$S"; }
echo "  Segmentzahlen: $(jq -c '.segmente' <<<"$S")"
[[ "$(jq -r '.szenario_fehler | length' <<<"$S")" != 0 ]] &&
  laut "Szenariofehler: $(jq -c '.szenario_fehler' <<<"$S")"
chk "2 POSITIVKONTROLLE: die Kette findet ZWEI Segmente (800 ms Pause)" "$(seg pause_800ms)" 2
chk "2 POSITIVKONTROLLE: die Kette findet DREI Segmente (zwei lange Pausen)" "$(seg zwei_pausen)" 3
chk "2 POSITIVKONTROLLE: die Kette findet EIN Segment (durchgehend 0,9)" "$(seg durchgehend)" 1
chk "2 POSITIVKONTROLLE: die Kette findet NULL (nie ueber dem Einsatz, 0,45)" "$(seg nie_ueber_einsatz)" 0

# =============================================================================
# 3. Die Zustandsmaschine -- das Kriterium des Plans, deterministisch
# =============================================================================
echo
echo "--- 3. Zustandsmaschine (eingespeiste Wahrscheinlichkeiten) ---"
echo "  Ein Chunk = 512 Samples bei 16 kHz = exakt 32,0 ms."
echo "  200 ms sind kein Vielfaches davon -- gefahren werden 6 Chunks = 192 ms."
echo "  800 ms sind exakt 25 Chunks."
chk "3 KRITERIUM: 192 ms Pause ergeben EIN Segment" "$(seg pause_192ms)" 1
chk "3 KRITERIUM: 800 ms Pause ergeben ZWEI Segmente" "$(seg pause_800ms)" 2
echo
echo "  Die Asymmetrie -- der eigentliche Kern des Tasks:"
chk "3 ASYMMETRIE: 640 ms bei 0,40 (unter Einsatz, ueber Ende) beenden NICHTS" \
  "$(seg delle_040)" 1
chk "3 ASYMMETRIE: ein Wortende (96 ms unter 0,35, dann zurueck) beendet NICHTS" \
  "$(seg wortende)" 1
chk "3 der Wahrscheinlichkeitspfad ruft das Modell GAR NICHT auf" \
  "$(jq -r '.modell_aufrufe_wahrscheinlichkeitspfad' <<<"$S")" 0

# =============================================================================
# 4. Modellanbindung: exakt 512 Samples, und kein stilles Auffuellen
# =============================================================================
echo
echo "--- 4. Modellanbindung (Spion-Detektor am laufenden Prozess) ---"
A="$(jq -c '.audio' <<<"$S")"
echo "  gewaehlter Audio-Einstieg: $(jq -r '.form' <<<"$A")"
echo "  verworfen: $(jq -r '.verworfene_formen | join(" ; ")' <<<"$A")"
echo "  $A"
chk "4 POSITIVKONTROLLE: der Audio-Pfad erreicht das Modell ueberhaupt" \
  "$([[ "$(jq -r '.gut_aufrufe' <<<"$A")" -ge 1 ]] && echo ja || echo nein)" ja
chk "4 POSITIVKONTROLLE: 20 gute Chunks ergeben 20 Modellaufrufe" \
  "$(jq -r '.gut_aufrufe' <<<"$A")" 20
chk "4 am Modell kommen ausschliesslich 1024 Bytes an (512 Samples int16)" \
  "$(jq -c '.gut_laengen' <<<"$A")" "[1024]"
chk "4 die Bytes kommen UNVERAENDERT an (int16, keine Umrechnung)" \
  "$(jq -r '.gut_unveraendert' <<<"$A")" true
chk "4 POSITIVKONTROLLE: 20 gute Chunks ergeben EIN Segment" \
  "$(jq -r '.gut_segmente' <<<"$A")" 1
for e in kurz lang; do
  echo "  Chunk '$e': Reaktion=$(jq -r --arg e "$e" '.[$e+"_reaktion"]' <<<"$A")" \
       "Ausnahme=$(jq -r --arg e "$e" '.[$e+"_ausnahme"] // "-"' <<<"$A")"
  chk "4 ein ${e}er Chunk wird ABGEWIESEN" \
    "$(jq -r --arg e "$e" '.[$e+"_reaktion"]' <<<"$A")" abgewiesen
  chk "4 und er kommt am Modell NICHT aufgefuellt an (kein stilles Polstern)" \
    "$(jq -r --arg e "$e" '.[$e+"_gepolstert"]' <<<"$A")" 0
done
chk "4 POSITIVKONTROLLE: nach den Abweisungen lebt die Kette noch" \
  "$(jq -r '.danach_segmente' <<<"$A")" 1
echo "  Diagnose (nicht gewertet): numpy-int16-Array ergibt Laengen $(jq -c '.ndarray_laengen' <<<"$A")"

# =============================================================================
# 5. Das echte Modell: Rauschen ist keine Sprache
# =============================================================================
echo
echo "--- 5. Echtes Modell (Gegengewicht gegen gesenkte Schwellen) ---"
lauf treiber_echt.py >"$RT/echt.json"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/echt.json"; then
  laut "Der Modell-Treiber hat keine auswertbare Antwort geliefert."
  tail -20 "$RT/treiber_echt.log"
  chk "5 Modell-Treiber laeuft" nein ja
else
  E="$(cat "$RT/echt.json")"
  if [[ "$(jq -r '.ok' <<<"$E")" != true ]]; then
    laut "$(jq -r '.fehler' <<<"$E")"
  fi
  chk "5 Modell-Treiber laeuft" "$(jq -r '.ok' <<<"$E")" true
  echo "  Audio-Einstieg (ohne Spion, echtes Modell): $(jq -r '.audio_form // "?"' <<<"$E")"
  echo "  hoechste Wahrscheinlichkeit ueber 40 Chunks Rauschen: $(jq -r '.p_rausch_max' <<<"$E")"
  echo "  hoechste Wahrscheinlichkeit ueber 40 Chunks Stille:   $(jq -r '.p_stille_max' <<<"$E")"
  chk "5 POSITIVKONTROLLE: dieselbe Kette liefert bei 0,9 ein Segment" \
    "$(jq -r '.segmente_wahrscheinlichkeit' <<<"$E")" 1
  chk "5 DIE ZUSAGE: weisses Rauschen ergibt NULL Segmente" \
    "$(jq -r '.segmente_rausch' <<<"$E")" 0
  chk "5 DIE ZUSAGE: Stille ergibt NULL Segmente" \
    "$(jq -r '.segmente_stille' <<<"$E")" 0
fi

# =============================================================================
# 6. Konfigurierbarkeit -- an der Wirkung
# =============================================================================
echo
echo "--- 6. Konfigurierbarkeit ---"
C="$(jq -c '.cfg_nachlauf' <<<"$S")"
echo "  Pause von 13 Chunks = 416 ms, einmal mit nachlauf_ms=300, einmal 500: $C"
echo "  (beide Werte liegen INNERHALB der Plangrenzen 300..500 ms)"
chk "6 mit nachlauf_ms=300 endet das Segment: ZWEI Segmente" "$(jq -r '."300"' <<<"$C")" 2
chk "6 mit nachlauf_ms=500 endet es nicht: EIN Segment" "$(jq -r '."500"' <<<"$C")" 1
chk "6 DIE ZUSAGE: der Wert wirkt ueberhaupt (die Ergebnisse unterscheiden sich)" \
  "$([[ "$(jq -r '."300"' <<<"$C")" != "$(jq -r '."500"' <<<"$C")" ]] && echo ja || echo nein)" ja
echo "  ohne Konfiguration liefert dieselbe Folge: $(jq -r '.cfg_vorgabe' <<<"$S")"
chk "6 die Vorgabe liegt selbst im Plankorridor (1 oder 2 Segmente bei 416 ms)" \
  "$([[ "$(jq -r '.cfg_vorgabe' <<<"$S")" == 1 || "$(jq -r '.cfg_vorgabe' <<<"$S")" == 2 ]] && echo ja || echo nein)" ja

echo "  Reaktion auf einsatz=0.2 / nachlauf_ms=50 (unter den Plangrenzen):"
echo "    $(jq -c '.grenzverletzung' <<<"$S")"
chk "6 eine Konfiguration unter den Plangrenzen wird abgewiesen ODER geloggt" \
  "$([[ "$(jq -r '.grenzverletzung.reaktion' <<<"$S")" != schweigen ]] && echo ja || echo nein)" ja

# --- Die Konfigurationsdatei selbst ------------------------------------------
TOML="$TARGET/config/daimon.toml"
[[ -f "$TOML" ]] || TOML="$REPO/config/daimon.toml"
echo "  Konfigurationsdatei: $TOML"
abschnitt="$("$PY" -c '
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    d = tomllib.load(f)
v = (d.get("ears") or {}).get("vad")
print("fehlt" if v is None else ",".join(sorted(v)))
' "$TOML" 2>/dev/null)"
echo "  [ears.vad] enthaelt: ${abschnitt:-(nicht lesbar)}"
chk "6 config/daimon.toml hat [ears.vad] mit einsatz, ende, nachlauf_ms" \
  "$abschnitt" "einsatz,ende,nachlauf_ms"

# =============================================================================
# OFFEN, und zwar benannt
# =============================================================================
echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) DER PLAN IST HIER NICHT WOERTLICH ERFUELLT. Kriterium 4 verlangt"
echo "      'einen Test mit synthetischem Signal'. Ein synthetischer Ton ist"
echo "      fuer Silero keine Sprache (Abschnitt 5: Rauschen"
echo "      $(jq -r '.p_rausch_max // "?"' <<<"${E:-{\}}"), Stille $(jq -r '.p_stille_max // "?"' <<<"${E:-{\}}")). Der 200/800-Test laeuft"
echo "      deshalb gegen eingespeiste Wahrscheinlichkeiten. Gemessen ist"
echo "      damit die ZUSTANDSMASCHINE, nicht die Kette Audio->Segment."
echo "  (2) MIT ECHTER SPRACHE IST HIER NICHTS GEMESSEN. Auf dieser Maschine"
echo "      gibt es keine Quelle dafuer (piper fehlt). Dass Silero bei echter"
echo "      Sprache ueber 0,5 kommt, ist ANGENOMMEN, nicht belegt. Der"
echo "      Nachweis gehoert nach T-3.15 (Ende-zu-Ende)."
echo "  (3) '16 kHz' ist von hier aus nicht messbar. Das Modell bekommt Bytes"
echo "      und kennt die Rate nicht; gemessen sind 512 Samples je Aufruf und"
echo "      1024 Bytes, also int16. Die Rate steht im Selbstbericht des"
echo "      Moduls (RATE) und in der Verdrahtung zu T-3.1 -- nicht hier."
echo "  (4) Die Grenzen 300..500 ms sind als Korridor geprueft, nicht als"
echo "      exakter Vorgabewert. Eine Umsetzung mit nachlauf_ms=400 und eine"
echo "      mit 320 sind hier beide gruen."
echo "  (5) Der Einstiegspunkt wird GESUCHT, nicht vorgeschrieben. Welche"
echo "      Aufrufform gegriffen hat, steht oben im Protokoll. Eine Umsetzung"
echo "      mit voellig anderem Zuschnitt faellt hier als Vertragsbruch auf --"
echo "      das ist Absicht, aber es ist kein inhaltliches Urteil."
echo "  (6) Abschnitt 4 misst mit einem EINGESETZTEN Detektor. Dass die"
echo "      Laengenpruefung auch fuer den echten Detektor gilt, folgt daraus"
echo "      nur, wenn beide denselben Pfad nehmen -- Abschnitt 5 faehrt"
echo "      deshalb ohne Spion."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-3.2: gruen. Die Hysterese ist asymmetrisch und beendet Segmente erst"
  echo "       nach dem Nachlauf, das Modell sieht ausschliesslich 512-Sample-"
  echo "       Chunks, ein abweichender Chunk fliegt raus statt gepolstert zu"
  echo "       werden, und Rauschen ist weiterhin keine Sprache."
else
  echo "T-3.2: FEHLGESCHLAGEN"
fi
exit $fail
