#!/usr/bin/env bash
# Verifizierer fuer T-3.3: Ringpuffer mit Vorlauf.
#
# ============================================================================
# DREI ZUSAGEN, DREI GETRENNTE MESSUNGEN -- UND JE EINE POSITIVKONTROLLE
# ============================================================================
#
# Der wiederkehrende Fehler dieses Projekts steht in HANDOVER.md: ohne
# Positivkontrolle ist "0 Treffer" keine Aussage. Bei T-3.3 sind ALLE DREI
# Zusagen als Abwesenheit formuliert -- kein Muster mehr im Puffer, kein
# Schreibvorgang auf Platte, keine Seite im Swap. Das ist die gefaehrlichste
# Form, die eine Zusage annehmen kann: jede kaputte Messkette ist gruen.
#
#   Abschnitt 3  FEHLTREFFER HINTERLASSEN NICHTS -- an den BYTES.
#                Der Ring wird mit einem 8-Byte-Muster vollgeschrieben, das
#                im Puffer wiederzufinden ist. VOR dem Verwerfen muss es
#                nachweislich da sein (80 000 Vorkommen bei vollem Ring),
#                danach kein einziges Mal. Nicht der Zaehler, nicht der
#                Zeiger, nicht `ausloesen()` -- die Bytes. Ein Ring, der nur
#                `self._schreib = 0` setzt, meldet sich naemlich als leer und
#                hat die Aufnahme trotzdem vollstaendig im Speicher.
#                Dazu: der Puffer muss DERSELBE sein wie vorher. Ein
#                "Verwerfen", das neu alloziert, laesst den alten Block
#                gefuellt auf dem Heap liegen -- das waere dasselbe Leck mit
#                besserer Tarnung.
#
#   Abschnitt 4  mlock() HAT GEGRIFFEN -- an `VmLck` in /proc/<pid>/status.
#                Nicht "wurde aufgerufen" (das ist ein grep und an der
#                Schreibweise zu umgehen), nicht "das Objekt sagt es ueber
#                sich selbst" (das ist ein Selbstbericht, Fall 9 in
#                HANDOVER.md). Der Kernel fuehrt den Wert, und er muss beim
#                Erzeugen des Rings um mindestens die Ringgroesse steigen.
#                Positivkontrollen: derselbe Prozess zeigt VORHER 0 kB, ein
#                eigener Kontrollprozess mit 640 000 Byte OHNE mlock zeigt
#                0 kB, und einer MIT mlock zeigt den vollen Wert. Erst die
#                letzten beiden zeigen, dass die Messung ueberhaupt
#                unterscheidet -- sonst waere "VmLck steigt nicht" auch bei
#                einem kaputten /proc-Leser ein Befund.
#
#   Abschnitt 5  DER VORLAUF -- am INHALT, nicht an einer Laengenangabe.
#                Jeder der 200 geschriebenen Chunks traegt seine eigene
#                Nummer in allen 512 Samples. Der Vorlauf wird dekodiert:
#                welche Nummern sind drin? Erwartet wird ein LUECKENLOSER
#                Lauf, der beim juengsten Chunk endet, 31..47 Chunks lang
#                (= 1,0..1,5 s bei 32,0 ms je Chunk). Die Gegenprobe steht im
#                selben Ergebnis: ein Chunk von 1,92 s vor dem Ausloeser darf
#                NICHT dabei sein, sonst wird hier bloss der ganze Ring
#                zurueckgereicht und "der Vorlauf ist dabei" ist trivial.
#
#   Abschnitt 6  SCHREIBT NIEMALS AUF PLATTE -- strace mit Positivkontrolle.
#                `strace -f -e trace=openat,write`. Im SELBEN Mitschnitt
#                macht der Treiber einen ABSICHTLICHEN Schreibvorgang auf
#                eine regulaere Datei. Taucht der nicht auf, ist "kein
#                Schreiben" die Nullaussage: strace koennte am falschen
#                Prozess haengen, am falschen Syscall, oder gar nichts
#                aufzeichnen.
#                `write(0|1|2, ...)` sind stdout/stderr und KEINE Verletzung
#                -- der Mitschnitt wird deshalb ueber die `openat`-Rueckgaben
#                im selben Lauf ausgewertet: nur ein `write` auf einen
#                Deskriptor, der nachweislich aus einem `openat` auf eine
#                regulaere Datei stammt, zaehlt. Zusaetzlich wird jedes
#                `openat` MIT SCHREIBABSICHT gemeldet -- ein Ring, der sich
#                per `mmap(MAP_SHARED)` auf eine Datei legt, macht nie ein
#                `write`, aber sehr wohl ein `openat(..., O_RDWR)`.
#
# ============================================================================
# WAS HIER NICHT GEMESSEN WIRD -- siehe auch OFFEN am Ende
# ============================================================================
#
# "Nicht im Swap" ist NICHT dasselbe wie "VmLck ist gestiegen", auch wenn es
# der beste verfuegbare Nachweis ist. Und "keine Kopie auf dem Heap" (die
# Akzeptanzliste warnt davor) ist von hier aus nicht belastbar messbar: der
# Vorlauf MUSS herausgereicht werden, und was der Aufrufer damit macht, ist
# nicht Sache dieses Verifizierers.
#
# Aufruf:
#   tests/verify/T-3.3.sh
#   DAIMON_FIXTURE=<baum> tests/verify/T-3.3.sh   # Baum mit eigenem daimon/
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"

MAX_SECS="${DAIMON_T33_MAX_SECS:-180}"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
laut() { echo "  !! $*"; }

# `jq -r '.feld // false'` liefert bei einem echten `false` dasselbe wie bei
# einem FEHLENDEN Feld -- HANDOVER.md, "Fallen dieser Maschine". Bei jeder
# Pruefung, die `false` ERWARTET, ist das der Unterschied zwischen "gemessen
# und widerlegt" und "gar nicht gemessen". Beim ersten Lauf dieses
# Verifizierers gegen den Referenzbaum sind daran zwei Pruefungen rot
# geworden, obwohl der Messwert stimmte.
jqb() {  # $1 = JSON, $2 = Feldname -> true | false | FEHLT
  jq -r --arg f "$2" 'if has($f) then (.[$f]|tostring) else "FEHLT" end' <<<"$1"
}

echo "T-3.3 — Ringpuffer mit Vorlauf: Fehltreffer hinterlassen nichts"
echo "  Baum: $TARGET"
echo "  Interpreter: $PY"

# =============================================================================
# 0. Voraussetzungen
# =============================================================================
echo
echo "--- 0. Voraussetzungen ---"
chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "timeout vorhanden" "$(command -v timeout >/dev/null && echo ja || echo nein)" ja
chk "strace vorhanden" "$(command -v strace >/dev/null && echo ja || echo nein)" ja
chk "Interpreter vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
chk "der Baum bringt daimon/ears/ring.py mit" \
  "$([[ -f "$TARGET/daimon/ears/ring.py" ]] && echo ja || echo nein)" ja

# 20 s x 16000 Hz x 2 Byte = 640 000 Byte = 625 KiB. RLIMIT_MEMLOCK muss das
# hergeben, sonst misst Abschnitt 4 die Maschine statt den Pruefling.
MEMLOCK_KB="$(ulimit -l)"
echo "  ulimit -l: ${MEMLOCK_KB} KB (gebraucht werden 625 KiB)"
chk "RLIMIT_MEMLOCK reicht fuer 625 KiB" \
  "$([[ "$MEMLOCK_KB" == unlimited || "$MEMLOCK_KB" -ge 640 ]] && echo ja || echo nein)" ja

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "T-3.3: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen."
  exit 1
fi

RT="$(mktemp -d)"
trap 'rm -rf -- "$RT"' EXIT INT TERM

# =============================================================================
# Der gemeinsame Vertragssucher
# =============================================================================
cat >"$RT/ringkontrakt.py" <<'PYEOF'
"""Sucht den Einstiegspunkt von daimon.ears.ring, statt ihn zu erraten.

Der Verifizierer ist blind gegen die Implementierung entstanden. Die
Akzeptanzliste nennt einen Ringpuffer mit Vorlauf, mlock und einem
Verwerfen -- Namen nennt sie nicht. Diese Datei probiert die plausiblen
Formen durch und meldet, WELCHE gegriffen hat.

DIE AUSWAHL IST SELBST DIE POSITIVKONTROLLE. Gewaehlt wird nicht die erste
Methode, die den richtigen Namen hat, sondern die erste, die NACHWEISLICH
WIRKT:

  * `schreibe`   wird nur genommen, wenn nach dem Aufruf das Muster
                 tatsaechlich IM PUFFER steht.
  * `ausloesen`  nur, wenn danach mindestens ein Chunk zurueckkommt.
  * `verwerfen`  nur, wenn der Ring sich danach als leer meldet -- und
                 ausdruecklich NICHT danach, ob er genullt hat. Sonst
                 waehlte der Sucher genau die Eigenschaft aus, die
                 Abschnitt 3 messen soll, und der Mutant
                 `nur-zeiger-zurueckgesetzt` waere nicht zu finden, sondern
                 wegdefiniert.

Ohne diese Trennung waere T-3.3 ein Fall 10 aus HANDOVER.md.
"""
import ctypes
import inspect
import struct

CHUNK = 512
CHUNK_BYTES = 1024
MAGIE = b"\x21\x7f\x33\x5a\x0d\x6e\x41\x77"     # 8 Byte, im Puffer wiederfindbar
MAGIE_CHUNK = MAGIE * (CHUNK_BYTES // len(MAGIE))
# Ein ZWEITES Muster fuer die Frage "lebt der Ring nach dem Verwerfen noch".
# Mit demselben Muster gemessen waere die Antwort bei einem Ring, der gar
# nicht geloescht hat, von seinen eigenen Resten nicht zu unterscheiden --
# die Positivkontrolle haenge dann an der Eigenschaft, die sie absichern soll.
MAGIE2 = b"\x5c\x02\x9e\x74\x6b\x30\xa8\x13"
MAGIE2_CHUNK = MAGIE2 * (CHUNK_BYTES // len(MAGIE2))

KLASSEN = ("Ringpuffer", "RingPuffer", "Ring", "Ringspeicher", "Vorlaufring",
           "Vorlaufpuffer", "AudioRing", "Audioring", "RingBuffer",
           "Ringbuffer", "Puffer", "Vorlauf")
SCHREIB = ("schreibe", "schreiben", "schreib", "anhaengen", "anhaenge",
           "hinzufuegen", "einspeisen", "push", "feed", "write", "add",
           "senke", "put", "__call__")
AUSLOES = ("ausloesen", "ausloese", "hole_vorlauf", "vorlauf_holen", "vorlauf",
           "schnappschuss", "snapshot", "entnehmen", "entnimm", "auslesen",
           "lesen", "lies", "hole", "annehmen", "akzeptieren", "trigger",
           "inhalt")
VERWERF = ("verwerfen", "verwirf", "verwerfe", "ablehnen", "abweisen",
           "fehltreffer", "nullen", "leeren", "leere", "loeschen",
           "zuruecksetzen", "discard", "reject", "clear", "reset")

# Konstruktorformen. Die Ringgroesse ist eine Zusage des Plans (20 s), also
# wird zuerst OHNE Argumente gebaut -- eine Umsetzung, die 20 s nur auf
# Zuruf liefert, erfuellt sie nicht.
CTOR = (
    ("()", (), {}),
    ("(sekunden=20)", (), {"sekunden": 20}),
    ("(sekunden=20.0)", (), {"sekunden": 20.0}),
    ("(20)", (20,), {}),
    ("(sekunden=20, vorlauf_ms=1250)", (), {"sekunden": 20, "vorlauf_ms": 1250}),
    ("(bytes_gesamt=640000)", (), {"bytes_gesamt": 640000}),
    ("(groesse=640000)", (), {"groesse": 640000}),
)

ERWARTET_BYTES = 640000


class KeinVertrag(Exception):
    pass


def angebot(o):
    return sorted(a for a in dir(o) if not a.startswith("_"))


# ---------------------------------------------------------------------------
# Puffersuche: irgendein Attribut, das dem Buffer-Protokoll genuegt und gross
# genug ist. mmap, bytearray, memoryview, ctypes-Array, ndarray -- alle
# erfuellen es. Gesucht wird zwei Ebenen tief, weil ein Ring seinen Speicher
# gern in einem Hilfsobjekt haelt.
# ---------------------------------------------------------------------------
def puffer_kandidaten(obj, tiefe=2):
    aus = []
    gesehen = set()

    def geh(o, pfad, t):
        if id(o) in gesehen or t < 0:
            return
        gesehen.add(id(o))
        for name in dir(o):
            if name.startswith("__"):
                continue
            try:
                v = getattr(o, name)
            except Exception:
                continue
            if callable(v) or isinstance(v, (str, int, float, bool, type(None))):
                continue
            p = f"{pfad}.{name}"
            try:
                mv = memoryview(v).cast("B")
            except Exception:
                if t > 0:
                    geh(v, p, t - 1)
                continue
            aus.append({"pfad": p, "bytes": mv.nbytes,
                        "schreibbar": not mv.readonly, "obj": v})

    geh(obj, "ring", tiefe)
    aus.sort(key=lambda k: (abs(k["bytes"] - ERWARTET_BYTES), k["pfad"]))
    return aus


def waehle_puffer(obj):
    for k in puffer_kandidaten(obj):
        if k["bytes"] >= 100000:
            return k
    return None


def bytes_von(k):
    """Frische Kopie der Pufferbytes -- fuer die Messung, nicht fuer den Ring."""
    return memoryview(k["obj"]).cast("B").tobytes()


def adresse_von(k):
    """Physische Adresse des Puffers, soweit ermittelbar."""
    try:
        return ctypes.addressof(ctypes.c_char.from_buffer(k["obj"]))
    except Exception:
        try:
            return ctypes.addressof(ctypes.c_char.from_buffer(
                memoryview(k["obj"]).cast("B")))
        except Exception:
            return None


def marker_chunk(nummer):
    """512 Samples int16, alle mit derselben Nummer. Nur im Vorlauf zu finden."""
    return struct.pack("<512h", *([nummer] * CHUNK))


# ---------------------------------------------------------------------------
# Bindung
# ---------------------------------------------------------------------------
def binde(modul):
    """Liefert (fabrik, methoden, protokoll).

    `fabrik()` baut einen frischen Ring, `methoden` ist ein dict mit den
    geprueften Aufrufen. Alles, was verworfen wurde, steht im Protokoll.
    """
    versuche = []
    gewaehlt = None
    for kn in KLASSEN:
        K = getattr(modul, kn, None)
        if not inspect.isclass(K):
            continue
        for besch, a, kw in CTOR:
            try:
                obj = K(*a, **dict(kw))
            except Exception as exc:
                versuche.append(f"{kn}{besch}: {type(exc).__name__}: {exc}"[:180])
                continue
            k = waehle_puffer(obj)
            if k is None:
                versuche.append(
                    f"{kn}{besch}: gebaut, aber kein Puffer >= 100 kB gefunden "
                    f"(Kandidaten: "
                    + ", ".join(f"{x['pfad']}={x['bytes']}"
                                for x in puffer_kandidaten(obj)[:6]) + ")")
                continue
            gewaehlt = (kn, K, besch, a, kw)
            break
        if gewaehlt:
            break
    if gewaehlt is None:
        raise KeinVertrag(
            "Kein brauchbarer Ringpuffer in daimon.ears.ring gefunden.\n"
            "Das Modul bietet an: " + ", ".join(angebot(modul)) + "\n"
            "Versuche:\n  " + "\n  ".join(versuche))

    kn, K, besch, a, kw = gewaehlt

    def fabrik():
        return K(*a, **dict(kw))

    protokoll = {"klasse": kn, "ctor": besch, "verworfene_ctor": versuche,
                 "angebot_objekt": angebot(fabrik())}

    # --- schreibe: nur, wenn es das Muster tatsaechlich in den Puffer legt ---
    schreib_name, schreib_versuche = None, []
    for n in SCHREIB:
        obj = fabrik()
        k = waehle_puffer(obj)
        f = getattr(obj, n, None)
        if not callable(f):
            continue
        try:
            f(MAGIE_CHUNK)
        except Exception as exc:
            schreib_versuche.append(f"{n}: {type(exc).__name__}: {exc}"[:140])
            continue
        if bytes_von(k).count(MAGIE) < 1:
            schreib_versuche.append(f"{n}: lief durch, aber nichts im Puffer")
            continue
        schreib_name = n
        break
    protokoll["schreibe"] = schreib_name or "(keine)"
    protokoll["verworfene_schreibe"] = schreib_versuche
    if schreib_name is None:
        raise KeinVertrag(
            "Keine Methode legt einen Chunk in den Puffer.\n"
            "Das Objekt bietet an: " + ", ".join(protokoll["angebot_objekt"]) +
            "\nVersuche:\n  " + "\n  ".join(schreib_versuche))

    def fuelle(obj, chunks):
        f = getattr(obj, schreib_name)
        for c in chunks:
            f(c)

    # --- ausloesen: nur, wenn danach mindestens ein Chunk zurueckkommt -------
    def zu_bytes(x):
        if x is None:
            return None
        if isinstance(x, (bytes, bytearray)):
            return bytes(x)
        if isinstance(x, (list, tuple)):
            try:
                return b"".join(bytes(memoryview(e).cast("B")) for e in x)
            except Exception:
                return None
        try:
            return bytes(memoryview(x).cast("B"))
        except Exception:
            return None

    ausloes_name, ausloes_versuche = None, []
    for n in AUSLOES:
        obj = fabrik()
        f = getattr(obj, n, None)
        if not callable(f):
            continue
        fuelle(obj, [marker_chunk(i + 1) for i in range(200)])
        try:
            roh = zu_bytes(f())
        except Exception as exc:
            ausloes_versuche.append(f"{n}: {type(exc).__name__}: {exc}"[:140])
            continue
        if not roh or len(roh) < CHUNK_BYTES:
            ausloes_versuche.append(
                f"{n}: lieferte {0 if roh is None else len(roh)} Byte")
            continue
        ausloes_name = n
        break
    protokoll["ausloesen"] = ausloes_name or "(keine)"
    protokoll["verworfene_ausloesen"] = ausloes_versuche

    # --- verwerfen: nur, wenn der Ring sich danach als LEER meldet -----------
    # Ausdruecklich NICHT: "wenn er genullt hat". Das ist die Messung, nicht
    # das Auswahlkriterium.
    verwerf_name, verwerf_versuche = None, []
    for n in VERWERF:
        obj = fabrik()
        f = getattr(obj, n, None)
        if not callable(f):
            continue
        fuelle(obj, [MAGIE_CHUNK] * 100)
        try:
            f()
        except Exception as exc:
            verwerf_versuche.append(f"{n}: {type(exc).__name__}: {exc}"[:140])
            continue
        leer = None
        if ausloes_name:
            try:
                leer = not zu_bytes(getattr(obj, ausloes_name)())
            except Exception as exc:
                verwerf_versuche.append(
                    f"{n}: danach {ausloes_name}() -> {type(exc).__name__}")
                continue
        if leer is False:
            verwerf_versuche.append(f"{n}: der Ring meldet sich danach nicht als leer")
            continue
        verwerf_name = n
        break
    protokoll["verwerfen"] = verwerf_name or "(keine)"
    protokoll["verworfene_verwerfen"] = verwerf_versuche

    methoden = {"fabrik": fabrik, "schreibe": schreib_name,
                "ausloesen": ausloes_name, "verwerfen": verwerf_name,
                "fuelle": fuelle, "zu_bytes": zu_bytes}
    return methoden, protokoll


def vmlck_kb():
    with open("/proc/self/status", "r", encoding="ascii", errors="replace") as f:
        for zeile in f:
            if zeile.startswith("VmLck:"):
                return int(zeile.split()[1])
    return -1
PYEOF

# =============================================================================
# Kontrollprozesse fuer VmLck -- ohne sie unterscheidet die Messung nichts
# =============================================================================
cat >"$RT/kontrolle_vmlck.py" <<'PYEOF'
"""640 000 Byte anonymes mmap, einmal OHNE und einmal MIT mlock.

Der Prozess OHNE mlock ist der Beweis, dass `VmLck: 0 kB` nicht einfach der
Wert ist, den /proc immer liefert -- und der MIT mlock der Beweis, dass die
Sperre auf dieser Maschine ueberhaupt zu bekommen ist. Erst zusammen machen
sie den Messwert am Pruefling zu einer Aussage.
"""
import ctypes
import ctypes.util
import json
import mmap
import sys

N = 640000
modus = sys.argv[1]


def vmlck():
    with open("/proc/self/status", "r", encoding="ascii") as f:
        for z in f:
            if z.startswith("VmLck:"):
                return int(z.split()[1])
    return -1


vorher = vmlck()
m = mmap.mmap(-1, N)
m.write(b"\xa5" * N)          # angefasst, also wirklich residente Seiten
adr = ctypes.addressof(ctypes.c_char.from_buffer(m))
rc, err = None, None
if modus == "mit":
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    rc = libc.mlock(ctypes.c_void_p(adr), ctypes.c_size_t(N))
    err = ctypes.get_errno()
print(json.dumps({"modus": modus, "vorher_kb": vorher, "nachher_kb": vmlck(),
                  "mlock_rc": rc, "errno": err, "bytes": N}))
PYEOF

# =============================================================================
# Treiber 1: Bytes, mlock, Vorlauf
# =============================================================================
cat >"$RT/treiber_kern.py" <<'PYEOF'
"""Misst die drei Zusagen am laufenden Objekt.

Reihenfolge ist Absicht: VmLck wird VOR dem Erzeugen des Rings gelesen, damit
der Zuwachs dem Ring zuzuordnen ist und nicht dem Interpreter.
"""
import importlib
import json
import os
import struct
import sys
import traceback

BAUM = sys.argv[1]
RT = sys.argv[2]
sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

import ringkontrakt as K  # noqa: E402

ERG = {"ok": True}


def melde(**kw):
    ERG.update(kw)


try:
    modul = importlib.import_module("daimon.ears.ring")
except Exception as exc:
    print(json.dumps({"ok": False, "phase": "import",
                      "fehler": f"{type(exc).__name__}: {exc}",
                      "spur": traceback.format_exc()[-1200:]}))
    raise SystemExit(0)

melde(datei=os.path.abspath(modul.__file__), angebot=K.angebot(modul))
melde(deklariert={n: getattr(modul, n) for n in
                  ("RATE", "CHUNK", "DTYPE", "SEKUNDEN", "RING_BYTES",
                   "RING_CHUNKS", "CHUNK_BYTES", "VORLAUF_MS", "CHUNK_MS")
                  if isinstance(getattr(modul, n, None), (int, float, str))})

# ---------------------------------------------------------------------------
# VmLck VOR allem anderen. Das ist die Positivkontrolle im selben Prozess:
# ein Prozess ohne mlock zeigt 0 kB.
# ---------------------------------------------------------------------------
melde(vmlck_vor_import_kb=K.vmlck_kb())

try:
    M, protokoll = K.binde(modul)
except Exception as exc:
    melde(ok=False, phase="vertrag", fehler=str(exc))
    print(json.dumps(ERG, default=str))
    raise SystemExit(0)
melde(vertrag=protokoll)

# `binde` hat schon Ringe gebaut -- der Zuwachs von hier ab ist also nicht
# mehr sauber zuzuordnen. Deshalb wird der mlock-Nachweis in einem EIGENEN,
# frischen Unterprozess gefahren (Abschnitt weiter unten, treiber_mlock.py).
# Hier wird nur festgehalten, was inzwischen gesperrt ist.
melde(vmlck_nach_bindung_kb=K.vmlck_kb())

# ===========================================================================
# ZUSAGE 1: Fehltreffer hinterlassen nichts -- an den Bytes
# ===========================================================================
bytes_erg = {}
if M["verwerfen"] is None:
    bytes_erg["fehler"] = "keine Verwerfen-Methode gefunden"
else:
    for etikett, anzahl in (("voll", 625), ("teilweise", 100)):
        ring = M["fabrik"]()
        k = K.waehle_puffer(ring)
        e = {"puffer_pfad": k["pfad"], "puffer_bytes": k["bytes"],
             "schreibbar": k["schreibbar"]}
        # (a) frisch: das Muster darf noch nicht drin sein, sonst misst der
        #     Zaehler Zufall.
        e["muster_frisch"] = K.bytes_von(k).count(K.MAGIE)
        e["adresse_vor"] = K.adresse_von(k)
        # (b) fuellen
        M["fuelle"](ring, [K.MAGIE_CHUNK] * anzahl)
        roh = K.bytes_von(k)
        e["muster_nach_fuellen"] = roh.count(K.MAGIE)          # POSITIVKONTROLLE
        e["nichtnull_nach_fuellen"] = sum(1 for b in roh if b)
        # (c) verwerfen
        getattr(ring, M["verwerfen"])()
        roh = K.bytes_von(k)
        e["muster_nach_verwerfen"] = roh.count(K.MAGIE)        # DIE ZUSAGE
        e["nichtnull_nach_verwerfen"] = sum(1 for b in roh if b)
        e["adresse_nach"] = K.adresse_von(k)
        e["puffer_bytes_nach"] = K.waehle_puffer(ring)["bytes"]
        # (d) und der Ring muss danach noch benutzbar sein -- sonst waere
        #     "nichts mehr da" nur ein anderes Wort fuer "kaputt".
        try:
            M["fuelle"](ring, [K.MAGIE2_CHUNK] * 5)
            e["muster_danach_wieder"] = K.bytes_von(k).count(K.MAGIE2)
        except Exception as exc:
            e["muster_danach_wieder"] = -1
            e["weiter_fehler"] = f"{type(exc).__name__}: {exc}"[:160]
        bytes_erg[etikett] = e
melde(bytes=bytes_erg)

# ===========================================================================
# ZUSAGE 3: Vorlauf -- am Inhalt
# ===========================================================================
vor = {}
if M["ausloesen"] is None:
    vor["fehler"] = "keine Ausloese-Methode gefunden"
else:
    ring = M["fabrik"]()
    # 200 Chunks = 6,4 s. Chunk i traegt die Nummer i+1 in allen 512 Samples.
    M["fuelle"](ring, [K.marker_chunk(i + 1) for i in range(200)])
    roh = M["zu_bytes"](getattr(ring, M["ausloesen"])())
    vor["bytes"] = len(roh or b"")
    vor["rest_bytes"] = (len(roh or b"") % K.CHUNK_BYTES)
    nummern, unsauber = [], 0
    if roh:
        for off in range(0, len(roh) - K.CHUNK_BYTES + 1, K.CHUNK_BYTES):
            werte = struct.unpack_from("<512h", roh, off)
            if len(set(werte)) != 1:
                unsauber += 1
                nummern.append(None)
            else:
                nummern.append(werte[0])
    vor["chunks"] = len(nummern)
    vor["unsaubere_chunks"] = unsauber
    vor["nummern_erste"] = nummern[:3]
    vor["nummern_letzte"] = nummern[-3:]
    echt = [n for n in nummern if n is not None]
    vor["luecklos_aufsteigend"] = bool(
        echt and all(echt[i + 1] - echt[i] == 1 for i in range(len(echt) - 1)))
    vor["endet_beim_juengsten"] = bool(echt and echt[-1] == 200)
    vor["ms"] = round(len(nummern) * 32.0, 1)
    # 1,0 s vor dem Ausloeser = 31,25 Chunks. Nummer 170 liegt 30 Chunks
    # (0,96 s) zurueck und MUSS drin sein; Nummer 140 liegt 60 Chunks
    # (1,92 s) zurueck und darf NICHT drin sein.
    vor["enthaelt_170_0s96_zurueck"] = 170 in echt
    vor["enthaelt_140_1s92_zurueck"] = 140 in echt
    vor["enthaelt_1_aeltester"] = 1 in echt
    vor["deklariert_vorlauf_ms"] = getattr(modul, "VORLAUF_MS", None)
melde(vorlauf=vor)

# ===========================================================================
# ZUSAGE 3b: der Vorlauf ist KONFIGURIERBAR -- an der WIRKUNG
#
# Abschnitt 5 misst einen Korridor: 31..47 Chunks. Ein Ring mit fest
# verdrahteten 1,25 s ist darin gruen, auch wenn `vorlauf_s` niemand ausliest.
# "Konfigurierbar" ist aber eine eigene Zusage des Plans. Sie ist nur
# gehalten, wenn ZWEI VERSCHIEDENE WERTE zu ZWEI VERSCHIEDENEN Vorlauf-
# LAENGEN fuehren -- und zwar zwei Werte, die BEIDE im erlaubten Korridor
# 1,0..1,5 s liegen. Gemessen wird also die Konfiguration, nicht ihre
# Verletzung.
#
# DER WEG WIRD GESUCHT, NICHT GERATEN, und -- wie beim Vertragssucher -- ist
# das AUSWAHLKRITERIUM ein anderes als das MESSKRITERIUM:
#   * gewaehlt wird der erste Parametername, den der Konstruktor ANNIMMT
#     (und der ueberhaupt einen Vorlauf zurueckgibt),
#   * gemessen wird, ob die beiden Werte VERSCHIEDEN WIRKEN.
# Waere die Wirkung schon das Auswahlkriterium, koennte dieser Abschnitt gar
# nicht mehr rot werden: ein Ring, der `vorlauf_s` entgegennimmt und
# wegwirft, faende dann einfach "keinen Weg" statt aufzufallen.
# ===========================================================================
CTOR_ARGS = {b: (a, kw) for b, a, kw in K.CTOR}


def analyse(roh):
    """Dieselbe Auswertung fuer den Pruefling UND fuer die Attrappe."""
    e = {"bytes": len(roh or b""), "rest_bytes": len(roh or b"") % K.CHUNK_BYTES}
    nummern = []
    if roh:
        for off in range(0, len(roh) - K.CHUNK_BYTES + 1, K.CHUNK_BYTES):
            werte = struct.unpack_from("<512h", roh, off)
            nummern.append(werte[0] if len(set(werte)) == 1 else None)
    echt = [n for n in nummern if n is not None]
    e["chunks"] = len(nummern)
    e["luecklos"] = bool(echt and all(echt[i + 1] - echt[i] == 1
                                      for i in range(len(echt) - 1)))
    e["endet_bei"] = echt[-1] if echt else None
    e["aeltester"] = echt[0] if echt else None
    # Chunk 160 liegt 40 Chunks (1,28 s) zurueck: bei 1,0 s NICHT dabei,
    # bei 1,5 s dabei. Das ist der Unterschied AM INHALT, nicht an der Laenge.
    e["enthaelt_160_1s28_zurueck"] = 160 in echt
    return e


konf = {"versuche": []}
if M["ausloesen"] is None:
    konf["fehler"] = "keine Ausloese-Methode gefunden"
else:
    Kl = getattr(modul, ERG["vertrag"]["klasse"])
    a0, kw0 = CTOR_ARGS.get(ERG["vertrag"]["ctor"], ((), {}))

    def vorlauf_mit(name, wert):
        ring = Kl(*a0, **dict(kw0, **{name: wert}))
        M["fuelle"](ring, [K.marker_chunk(i + 1) for i in range(200)])
        return M["zu_bytes"](getattr(ring, M["ausloesen"])())

    # (Name, Wert fuer 1,0 s, Wert fuer 1,5 s) -- Sekunden, Millisekunden,
    # Chunks. 1,0 s = 31,25 Chunks, 1,5 s = 46,875 Chunks.
    KANDIDATEN = [
        ("vorlauf_s", 1.0, 1.5), ("vorlauf_sekunden", 1.0, 1.5),
        ("vorlauf", 1.0, 1.5), ("preroll_s", 1.0, 1.5), ("lead_s", 1.0, 1.5),
        ("vorlauf_ms", 1000, 1500), ("vorlauf", 1000, 1500),
        ("vorlauf_chunks", 31, 47),
    ]
    gewaehlt = None
    for name, w1, w2 in KANDIDATEN:
        try:
            r1, r2 = vorlauf_mit(name, w1), vorlauf_mit(name, w2)
        except Exception as exc:
            konf["versuche"].append(f"{name}={w1}/{w2}: {type(exc).__name__}: {exc}"[:140])
            continue
        if not r1 or not r2:
            konf["versuche"].append(
                f"{name}={w1}/{w2}: angenommen, aber "
                f"{len(r1 or b'')}/{len(r2 or b'')} Byte zurueck")
            continue
        gewaehlt = (name, w1, w2, r1, r2)
        break

    konf["weg"] = gewaehlt[0] if gewaehlt else "(keiner)"
    if gewaehlt:
        name, w1, w2, r1, r2 = gewaehlt
        konf["werte"] = [w1, w2]
        konf["klein"] = analyse(r1)
        konf["gross"] = analyse(r2)
        konf["unterscheidet_sich"] = (konf["klein"]["chunks"]
                                      != konf["gross"]["chunks"])

    # --- POSITIVKONTROLLE DER MESSKETTE -------------------------------------
    # Zwei Vorlaeufe BEKANNTER Laenge (31 und 47 Chunks) durch DIESELBE Kette
    # -- `zu_bytes` und `analyse`. Kaeme hier zweimal dieselbe Zahl heraus,
    # koennte die Messung verschiedene Vorlauflaengen gar nicht trennen, und
    # "beide Werte wirken gleich" waere kein Befund, sondern ein Messfehler.
    attrappe = {}
    for n in (31, 47):
        roh = M["zu_bytes"]([K.marker_chunk(200 - n + 1 + i) for i in range(n)])
        attrappe[str(n)] = analyse(roh)
    konf["attrappe"] = attrappe
melde(konfig=konf)

print(json.dumps(ERG, default=str))
PYEOF

# =============================================================================
# Treiber 2: mlock -- frischer Prozess, VmLck vor und nach genau einem Ring
# =============================================================================
cat >"$RT/treiber_mlock.py" <<'PYEOF'
"""Ein Prozess, ein Ring, zwei Messwerte.

Der Wert stammt aus /proc/<pid>/status, nicht aus dem Pruefling. Ein Objekt,
das ueber sich selbst sagt `gesperrt = True`, ist ein Selbstbericht -- Fall 9
in HANDOVER.md.
"""
import importlib
import json
import sys
import traceback

BAUM = sys.argv[1]
RT = sys.argv[2]
sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

import ringkontrakt as K  # noqa: E402

ERG = {"ok": True}
try:
    modul = importlib.import_module("daimon.ears.ring")
except Exception as exc:
    print(json.dumps({"ok": False, "fehler": f"{type(exc).__name__}: {exc}",
                      "spur": traceback.format_exc()[-800:]}))
    raise SystemExit(0)

ERG["vor_kb"] = K.vmlck_kb()

# Der Konstruktor wird hier NICHT ueber `binde` gesucht -- `binde` baut zum
# Pruefen mehrere Ringe, und dann waere der Zuwachs nicht einem einzigen
# zuzuordnen. Stattdessen dieselbe Formsuche, aber genau ein Bau.
gebaut, fehler = None, []
import inspect  # noqa: E402
for kn in K.KLASSEN:
    Kl = getattr(modul, kn, None)
    if not inspect.isclass(Kl):
        continue
    for besch, a, kw in K.CTOR:
        try:
            obj = Kl(*a, **dict(kw))
        except Exception as exc:
            fehler.append(f"{kn}{besch}: {type(exc).__name__}")
            continue
        if K.waehle_puffer(obj) is None:
            fehler.append(f"{kn}{besch}: kein Puffer")
            continue
        gebaut = (obj, f"{kn}{besch}")
        break
    if gebaut:
        break

if gebaut is None:
    ERG.update(ok=False, fehler="kein Ring baubar", versuche=fehler)
else:
    obj, form = gebaut
    ERG["form"] = form
    ERG["nach_kb"] = K.vmlck_kb()
    k = K.waehle_puffer(obj)
    ERG["puffer_bytes"] = k["bytes"]
    ERG["zuwachs_kb"] = ERG["nach_kb"] - ERG["vor_kb"]
    # Der Selbstbericht -- nur zur Gegenueberstellung, NICHT gewertet.
    ERG["selbstbericht"] = {n: getattr(obj, n) for n in
                            ("gesperrt", "locked", "mlocked", "ist_gesperrt")
                            if isinstance(getattr(obj, n, None), bool)}
print(json.dumps(ERG, default=str))
PYEOF

# =============================================================================
# Treiber 3: unter strace -- inklusive absichtlichem Schreibvorgang
# =============================================================================
cat >"$RT/treiber_platte.py" <<'PYEOF'
"""Faehrt den vollen Lebenszyklus des Rings -- und schreibt ABSICHTLICH.

Der absichtliche Schreibvorgang ist die Positivkontrolle der Messkette.
Ohne ihn ist "im Mitschnitt steht kein Schreibvorgang" die Nullaussage
schlechthin: strace koennte am falschen Prozess haengen, am falschen
Syscall, oder gar nichts aufgezeichnet haben.

Er steht am ENDE, damit alles, was der Ring tut, davor liegt.
"""
import importlib
import json
import os
import sys

BAUM = sys.argv[1]
RT = sys.argv[2]
KONTROLLE = sys.argv[3]
sys.path.insert(0, BAUM)
sys.path.insert(0, RT)

import ringkontrakt as K  # noqa: E402

ERG = {"ok": True}
modul = importlib.import_module("daimon.ears.ring")
M, protokoll = K.binde(modul)
ERG["vertrag"] = {n: protokoll.get(n) for n in
                  ("klasse", "ctor", "schreibe", "ausloesen", "verwerfen")}

ring = M["fabrik"]()
M["fuelle"](ring, [K.marker_chunk(i + 1) for i in range(200)])
if M["ausloesen"]:
    ERG["vorlauf_bytes"] = len(M["zu_bytes"](getattr(ring, M["ausloesen"])()) or b"")
if M["verwerfen"]:
    getattr(ring, M["verwerfen"])()
M["fuelle"](ring, [K.MAGIE_CHUNK] * 50)
if M["verwerfen"]:
    getattr(ring, M["verwerfen"])()

# --- DIE POSITIVKONTROLLE ---------------------------------------------------
nutz = b"POSITIVKONTROLLE-T-3-3-" * 40
fd = os.open(KONTROLLE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, nutz)
os.fsync(fd)
os.close(fd)
ERG["kontrolldatei"] = KONTROLLE
ERG["kontrolle_bytes"] = len(nutz)
print(json.dumps(ERG, default=str))
PYEOF

# =============================================================================
# Auswerter fuer den strace-Mitschnitt
# =============================================================================
cat >"$RT/lies_trace.py" <<'PYEOF'
"""Wertet `strace -f -e trace=openat,write` aus.

Die entscheidende Unterscheidung: `write(1, ...)` und `write(2, ...)` gehen
nach stdout/stderr und sind KEINE Verletzung -- die Akzeptanzliste sagt das
ausdruecklich. Ein `write` zaehlt hier nur dann als Schreibvorgang auf
Platte, wenn sein Deskriptor IM SELBEN MITSCHNITT aus einem `openat` auf
einen regulaeren Pfad stammt. Die Deskriptortabelle wird deshalb je Prozess
aus den `openat`-Rueckgaben aufgebaut.

Zusaetzlich wird jedes `openat` mit SCHREIBABSICHT gemeldet: ein Ring, der
sich per mmap(MAP_SHARED) auf eine Datei legt, macht nie ein `write`, aber
sehr wohl ein `openat(..., O_RDWR)`.
"""
import json
import re
import sys

TRACE, KONTROLLE = sys.argv[1], sys.argv[2]

RE_OPEN = re.compile(
    r'^(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)?openat\((?:AT_FDCWD|-?\d+|[A-Z_]+),\s*'
    r'"((?:[^"\\]|\\.)*)",\s*([^)]*)\)\s*=\s*(-?\d+)')
RE_WRITE = re.compile(
    r'^(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)?write\((-?\d+),.*\)\s*=\s*(-?\d+)$')

# Alles, was kein Dateisystem ist bzw. wo Schreiben folgenlos ist.
HARMLOS = ("/dev/null", "/dev/pts/", "/dev/tty", "/dev/urandom", "/dev/random",
           "/proc/", "/sys/")

fdtab = {}          # (pid, fd) -> pfad
schreib_open = []   # openat mit Schreibabsicht
verletzung = []     # write auf eine regulaere Datei
kontroll_open = []
kontroll_write = []
std_writes = 0
unbekannt = []      # write auf einen fd, den wir nicht zuordnen koennen
unparsbar = []
n_open = n_write = 0

with open(TRACE, "r", encoding="utf-8", errors="replace") as f:
    for zeile in f:
        zeile = zeile.rstrip("\n")
        if "openat(" in zeile:
            n_open += 1
            m = RE_OPEN.match(zeile.lstrip())
            if not m:
                if "unfinished" not in zeile and "resumed" not in zeile:
                    unparsbar.append(zeile[:160])
                continue
            pid = m.group(1) or m.group(2) or "0"
            pfad, flags, rc = m.group(3), m.group(4), int(m.group(5))
            if rc < 0:
                continue
            fdtab[(pid, rc)] = pfad
            schreibend = any(t in flags for t in
                             ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND",
                              "O_TRUNC"))
            if pfad == KONTROLLE:
                kontroll_open.append({"pid": pid, "fd": rc, "flags": flags})
            elif schreibend and not pfad.startswith(HARMLOS):
                schreib_open.append({"pfad": pfad, "flags": flags})
        elif "write(" in zeile:
            n_write += 1
            m = RE_WRITE.match(zeile.lstrip())
            if not m:
                if "unfinished" not in zeile and "resumed" not in zeile:
                    unparsbar.append(zeile[:160])
                continue
            pid = m.group(1) or m.group(2) or "0"
            fd, n = int(m.group(3)), int(m.group(4))
            if fd in (0, 1, 2):
                std_writes += 1
                continue
            pfad = fdtab.get((pid, fd))
            if pfad is None:
                unbekannt.append({"pid": pid, "fd": fd, "bytes": n})
            elif pfad == KONTROLLE:
                kontroll_write.append({"fd": fd, "bytes": n})
            elif pfad.startswith(HARMLOS):
                pass
            else:
                verletzung.append({"pfad": pfad, "fd": fd, "bytes": n})

print(json.dumps({
    "zeilen_openat": n_open, "zeilen_write": n_write,
    "kontrolle_geoeffnet": len(kontroll_open),
    "kontrolle_geschrieben": len(kontroll_write),
    "kontrolle_bytes": sum(x["bytes"] for x in kontroll_write),
    "kontrolle_open_details": kontroll_open[:3],
    "schreibende_openat": schreib_open,
    "verletzungen": verletzung,
    "writes_stdout_stderr": std_writes,
    "writes_unbekannter_fd": unbekannt[:20],
    "n_writes_unbekannter_fd": len(unbekannt),
    "unparsbar": unparsbar[:10], "n_unparsbar": len(unparsbar),
}, default=str))
PYEOF

lauf() {  # $1 = Skript, ab $2 = Argumente
  local s="$1"; shift
  timeout --foreground --signal=TERM --kill-after=5s "${MAX_SECS}s" \
    "$PY" -B -P "$RT/$s" "$@" 2>"$RT/${s%.py}.log"
}

# =============================================================================
# 1. Bindung
# =============================================================================
echo
echo "--- 1. Bindung ---"
lauf treiber_kern.py "$TARGET" "$RT" >"$RT/kern.json"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/kern.json"; then
  laut "Der Treiber hat keine auswertbare Antwort geliefert."
  laut "Protokoll:"; tail -25 "$RT/treiber_kern.log"
  chk "1 Treiber laeuft" nein ja
  echo; echo "T-3.3: FEHLGESCHLAGEN"; exit 1
fi
S="$(cat "$RT/kern.json")"
echo "  Modul bietet an: $(jq -r '.angebot // [] | join(", ")' <<<"$S")"
if [[ "$(jq -r '.ok' <<<"$S")" != true ]]; then
  laut "Phase: $(jq -r '.phase // "?"' <<<"$S")"
  laut "$(jq -r '.fehler // "?"' <<<"$S")"
  [[ "$(jq -r '.spur // empty' <<<"$S")" ]] && echo "$(jq -r '.spur' <<<"$S")"
fi
chk "1 daimon.ears.ring laedt und der Vertrag greift" "$(jq -r '.ok' <<<"$S")" true
if [[ "$(jq -r '.ok' <<<"$S")" != true ]]; then
  echo; echo "T-3.3: FEHLGESCHLAGEN — ohne Einstiegspunkt ist nichts gemessen."
  exit 1
fi
geladen="$(jq -r '.datei' <<<"$S")"
echo "  geladen aus: $geladen"
# Fall 12 in HANDOVER.md: eine Positivkontrolle, die nie gruen werden kann,
# meldet jeden Mutanten als "erkannt", ohne dessen Mutation zu messen.
chk "1 POSITIVKONTROLLE: der Pruefling stammt AUS DEM GEPRUEFTEN BAUM" \
  "$([[ "$geladen" == "$TARGET"/* ]] && echo ja || echo "aus_$geladen")" ja
V="$(jq -c '.vertrag' <<<"$S")"
echo "  Klasse:      $(jq -r '.klasse' <<<"$V")$(jq -r '.ctor' <<<"$V")"
echo "  schreibe:    $(jq -r '.schreibe' <<<"$V")   (verworfen: $(jq -r '.verworfene_schreibe | join(" ; ")' <<<"$V"))"
echo "  ausloesen:   $(jq -r '.ausloesen' <<<"$V")   (verworfen: $(jq -r '.verworfene_ausloesen | join(" ; ")' <<<"$V"))"
echo "  verwerfen:   $(jq -r '.verwerfen' <<<"$V")   (verworfen: $(jq -r '.verworfene_verwerfen | join(" ; ")' <<<"$V"))"
echo "  Objekt bietet an: $(jq -r '.angebot_objekt | join(", ")' <<<"$V")"
echo "  deklariert: $(jq -c '.deklariert' <<<"$S")"
chk "1 eine Schreibmethode wirkt nachweislich auf den Puffer" \
  "$([[ "$(jq -r '.schreibe' <<<"$V")" != "(keine)" ]] && echo ja || echo nein)" ja
chk "1 eine Ausloesemethode liefert nachweislich Chunks" \
  "$([[ "$(jq -r '.ausloesen' <<<"$V")" != "(keine)" ]] && echo ja || echo nein)" ja
chk "1 eine Verwerfenmethode setzt den Ring nachweislich zurueck" \
  "$([[ "$(jq -r '.verwerfen' <<<"$V")" != "(keine)" ]] && echo ja || echo nein)" ja

# =============================================================================
# 2. Feste Groesse, vorab alloziert
# =============================================================================
echo
echo "--- 2. Feste Groesse, vorab alloziert (20 s = 640 000 Byte) ---"
B="$(jq -c '.bytes' <<<"$S")"
pb="$(jq -r '.voll.puffer_bytes // -1' <<<"$B")"
echo "  gefundener Puffer: $(jq -r '.voll.puffer_pfad // "?"' <<<"$B") mit $pb Byte"
chk "2 der Puffer hat 640 000 Byte, BEVOR ein Chunk geschrieben wurde" "$pb" 640000
chk "2 der Puffer ist schreibbar (in-place, keine readonly-Kopie)" \
  "$(jq -r '.voll.schreibbar // false' <<<"$B")" true
chk "2 die Groesse aendert sich durch Fuellen und Verwerfen NICHT" \
  "$(jq -r '.voll.puffer_bytes_nach // -1' <<<"$B")" "$pb"

# =============================================================================
# 3. Fehltreffer hinterlassen nichts -- an den BYTES
# =============================================================================
echo
echo "--- 3. Fehltreffer hinterlassen nichts (gemessen an den Bytes) ---"
echo "  Muster: 8 Byte, 128-mal je Chunk. Voller Ring = 625 Chunks = 80 000 Vorkommen."
for e in voll teilweise; do
  E="$(jq -c --arg e "$e" '.[$e] // {}' <<<"$B")"
  soll=80000; [[ "$e" == teilweise ]] && soll=12800
  echo "  [$e] frisch=$(jq -r '.muster_frisch' <<<"$E")" \
       "nach_fuellen=$(jq -r '.muster_nach_fuellen' <<<"$E")" \
       "nach_verwerfen=$(jq -r '.muster_nach_verwerfen' <<<"$E")" \
       "nichtnull_danach=$(jq -r '.nichtnull_nach_verwerfen' <<<"$E")"
  chk "3 [$e] der frische Puffer enthaelt das Muster NICHT (kein Zufallstreffer)" \
    "$(jq -r '.muster_frisch' <<<"$E")" 0
  chk "3 [$e] POSITIVKONTROLLE: nach dem Fuellen steht das Muster $soll-mal im Puffer" \
    "$(jq -r '.muster_nach_fuellen' <<<"$E")" "$soll"
  chk "3 [$e] DIE ZUSAGE: nach dem Verwerfen kommt es KEIN EINZIGES MAL mehr vor" \
    "$(jq -r '.muster_nach_verwerfen' <<<"$E")" 0
  chk "3 [$e] und der Puffer ist byteweise genullt (Akzeptanzliste: explizit nullen)" \
    "$(jq -r '.nichtnull_nach_verwerfen' <<<"$E")" 0
  # Ein Verwerfen, das NEU ALLOZIERT, laesst den alten Block gefuellt auf dem
  # Heap liegen. Von aussen sieht das aus wie Nullen -- ist es aber nicht.
  chk "3 [$e] der Puffer ist DERSELBE geblieben (nicht neu alloziert)" \
    "$([[ "$(jq -r '.adresse_vor' <<<"$E")" == "$(jq -r '.adresse_nach' <<<"$E")" \
        && "$(jq -r '.adresse_vor' <<<"$E")" != null ]] && echo ja || echo nein)" ja
  # Gemessen mit einem ZWEITEN Muster -- sonst waere diese Kontrolle bei einem
  # Ring, der nichts geloescht hat, von seinen eigenen Resten nicht zu
  # unterscheiden.
  chk "3 [$e] POSITIVKONTROLLE: der Ring nimmt danach wieder Chunks an (zweites Muster)" \
    "$(jq -r '.muster_danach_wieder' <<<"$E")" 640
done

# =============================================================================
# 4. mlock() hat gegriffen -- VmLck in /proc/<pid>/status
# =============================================================================
echo
echo "--- 4. mlock() hat gegriffen (VmLck, nicht Selbstbericht) ---"
lauf kontrolle_vmlck.py ohne >"$RT/vm_ohne.json"
lauf kontrolle_vmlck.py mit  >"$RT/vm_mit.json"
KO="$(cat "$RT/vm_ohne.json")"; KM="$(cat "$RT/vm_mit.json")"
echo "  Kontrolle OHNE mlock: $KO"
echo "  Kontrolle MIT  mlock: $KM"
chk "4 POSITIVKONTROLLE: 640 000 Byte OHNE mlock ergeben VmLck 0 kB" \
  "$(jq -r '.nachher_kb // -1' <<<"$KO")" 0
chk "4 POSITIVKONTROLLE: 640 000 Byte MIT mlock ergeben mindestens 625 kB" \
  "$([[ "$(jq -r '.nachher_kb // -1' <<<"$KM")" -ge 625 ]] && echo ja || echo nein)" ja
chk "4 POSITIVKONTROLLE: mlock() liefert auf dieser Maschine rc=0" \
  "$(jq -r '.mlock_rc // -1' <<<"$KM")" 0

lauf treiber_mlock.py "$TARGET" "$RT" >"$RT/mlock.json"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/mlock.json"; then
  laut "Der mlock-Treiber hat keine auswertbare Antwort geliefert."
  tail -20 "$RT/treiber_mlock.log"
  chk "4 mlock-Treiber laeuft" nein ja
else
  ML="$(cat "$RT/mlock.json")"
  echo "  Pruefling: $ML"
  chk "4 mlock-Treiber laeuft" "$(jq -r '.ok' <<<"$ML")" true
  chk "4 POSITIVKONTROLLE: VOR dem Ring ist VmLck 0 kB" \
    "$(jq -r '.vor_kb // -1' <<<"$ML")" 0
  chk "4 DIE ZUSAGE: VmLck steigt beim Erzeugen des Rings um >= 625 kB" \
    "$([[ "$(jq -r '.zuwachs_kb // -1' <<<"$ML")" -ge 625 ]] && echo ja || echo nein)" ja
  echo "  Selbstbericht des Objekts (NICHT gewertet): $(jq -c '.selbstbericht // {}' <<<"$ML")"
  echo "  (ein Objekt, das 'gesperrt = True' sagt und nicht gesperrt hat,"
  echo "   faellt genau hier auf und nirgends sonst)"
fi

# =============================================================================
# 5. Vorlauf -- am Inhalt
# =============================================================================
echo
echo "--- 5. Vorlauf 1,0-1,5 s (am Inhalt, nicht an einer Laengenangabe) ---"
VL="$(jq -c '.vorlauf' <<<"$S")"
echo "  200 Chunks geschrieben (6,4 s), Chunk i traegt die Nummer i+1."
echo "  $VL"
chk "5 der Vorlauf besteht aus ganzen Chunks (1024 Byte)" \
  "$(jq -r '.rest_bytes // -1' <<<"$VL")" 0
chk "5 kein Chunk ist vermischt (jeder traegt genau eine Nummer)" \
  "$(jq -r '.unsaubere_chunks // -1' <<<"$VL")" 0
chk "5 DIE ZUSAGE: der Vorlauf ist 31..47 Chunks lang (1,0-1,5 s)" \
  "$([[ "$(jq -r '.chunks // -1' <<<"$VL")" -ge 31 && "$(jq -r '.chunks // -1' <<<"$VL")" -le 47 ]] \
     && echo ja || echo "war_$(jq -r '.chunks' <<<"$VL")")" ja
chk "5 die Nummern sind lueckenlos aufsteigend (Reihenfolge stimmt)" \
  "$(jqb "$VL" luecklos_aufsteigend)" true
chk "5 POSITIVKONTROLLE: der Vorlauf endet beim JUENGSTEN Chunk (Nr. 200)" \
  "$(jqb "$VL" endet_beim_juengsten)" true
chk "5 INHALT: der Chunk von 0,96 s VOR dem Ausloeser (Nr. 170) ist dabei" \
  "$(jqb "$VL" enthaelt_170_0s96_zurueck)" true
# Ohne diese Gegenprobe waere "der Vorlauf ist dabei" auch dann gruen, wenn
# schlicht der ganze Ring zurueckgereicht wird.
chk "5 GEGENPROBE: der Chunk von 1,92 s davor (Nr. 140) ist NICHT dabei" \
  "$(jqb "$VL" enthaelt_140_1s92_zurueck)" false
chk "5 GEGENPROBE: der aelteste Chunk (Nr. 1) ist NICHT dabei" \
  "$(jqb "$VL" enthaelt_1_aeltester)" false

# =============================================================================
# 6. Schreibt niemals auf Platte
# =============================================================================
echo
echo "--- 6. Schreibt niemals auf Platte (strace mit Positivkontrolle) ---"
KONTROLLDATEI="$RT/positivkontrolle.bin"
timeout --foreground --signal=TERM --kill-after=5s "${MAX_SECS}s" \
  strace -f -e trace=openat,write -o "$RT/trace.txt" \
  "$PY" -B -P "$RT/treiber_platte.py" "$TARGET" "$RT" "$KONTROLLDATEI" \
  >"$RT/platte.json" 2>"$RT/platte.log"
echo "  Mitschnitt: $(wc -l <"$RT/trace.txt") Zeilen"
if ! jq -e . >/dev/null 2>/dev/null <"$RT/platte.json"; then
  laut "Der strace-Treiber hat keine auswertbare Antwort geliefert."
  tail -25 "$RT/platte.log"
  chk "6 strace-Treiber laeuft" nein ja
else
  P="$(cat "$RT/platte.json")"
  echo "  Treiber: $(jq -c '.vertrag' <<<"$P")  Vorlauf $(jq -r '.vorlauf_bytes // "?"' <<<"$P") Byte"
  chk "6 strace-Treiber laeuft" "$(jq -r '.ok' <<<"$P")" true
  T="$("$PY" -B -P "$RT/lies_trace.py" "$RT/trace.txt" "$KONTROLLDATEI" 2>"$RT/lies.log")"
  if [[ -z "$T" ]]; then
    laut "Auswertung des Mitschnitts fehlgeschlagen:"; tail -10 "$RT/lies.log"
    chk "6 Mitschnitt auswertbar" nein ja
  else
    echo "  openat-Zeilen: $(jq -r '.zeilen_openat' <<<"$T"), write-Zeilen: $(jq -r '.zeilen_write' <<<"$T")"
    echo "  writes nach stdout/stderr (fd 0/1/2, KEINE Verletzung): $(jq -r '.writes_stdout_stderr' <<<"$T")"
    echo "  Kontrolldatei: geoeffnet $(jq -r '.kontrolle_geoeffnet' <<<"$T")x," \
         "geschrieben $(jq -r '.kontrolle_geschrieben' <<<"$T")x," \
         "$(jq -r '.kontrolle_bytes' <<<"$T") Byte"
    echo "  $(jq -c '.kontrolle_open_details' <<<"$T")"
    # DIE POSITIVKONTROLLE. Ohne sie ist alles Folgende die Nullaussage.
    chk "6 POSITIVKONTROLLE: der absichtliche openat steht im Mitschnitt" \
      "$([[ "$(jq -r '.kontrolle_geoeffnet' <<<"$T")" -ge 1 ]] && echo ja || echo nein)" ja
    chk "6 POSITIVKONTROLLE: der absichtliche write steht im Mitschnitt" \
      "$([[ "$(jq -r '.kontrolle_geschrieben' <<<"$T")" -ge 1 ]] && echo ja || echo nein)" ja
    chk "6 POSITIVKONTROLLE: es sind die 920 Byte, die der Treiber schrieb" \
      "$(jq -r '.kontrolle_bytes' <<<"$T")" 920
    echo "  schreibende openat ausser der Kontrolldatei: $(jq -c '.schreibende_openat' <<<"$T")"
    echo "  writes auf regulaere Dateien ausser der Kontrolldatei: $(jq -c '.verletzungen' <<<"$T")"
    chk "6 DIE ZUSAGE: kein write auf eine regulaere Datei" \
      "$(jq -r '.verletzungen | length' <<<"$T")" 0
    chk "6 DIE ZUSAGE: kein openat mit Schreibabsicht (faengt auch mmap-auf-Datei)" \
      "$(jq -r '.schreibende_openat | length' <<<"$T")" 0
    # Ein write auf einen fd, den kein openat erzeugt hat, ist kein Beleg fuer
    # eine Platte -- aber er ist auch kein Beleg fuer das Gegenteil. Er wird
    # deshalb GEMELDET und nicht stillschweigend als harmlos abgelegt.
    echo "  writes auf nicht zuordenbare fds (Pipes, Sockets, ererbte fds): $(jq -r '.n_writes_unbekannter_fd' <<<"$T")"
    [[ "$(jq -r '.n_writes_unbekannter_fd' <<<"$T")" != 0 ]] &&
      laut "nicht zuordenbar: $(jq -c '.writes_unbekannter_fd' <<<"$T")"
    # Eine unparsbare Zeile koennte einen write verstecken.
    echo "  unparsbare Zeilen: $(jq -r '.n_unparsbar' <<<"$T")"
    [[ "$(jq -r '.n_unparsbar' <<<"$T")" != 0 ]] &&
      laut "$(jq -r '.unparsbar | join("\n      ")' <<<"$T")"
    chk "6 der Mitschnitt ist vollstaendig ausgewertet (keine unparsbare Zeile)" \
      "$(jq -r '.n_unparsbar' <<<"$T")" 0

    # -----------------------------------------------------------------------
    # Und die Positivkontrolle FUER DEN AUSWERTER SELBST. Oben ist gezeigt,
    # dass strace einen Schreibvorgang aufzeichnet. Nicht gezeigt ist, dass
    # `lies_trace.py` ihn auch als VERLETZUNG meldet -- die Kontrolldatei
    # steht ja auf der Ausnahmeliste. Ein Auswerter, der jeden Pfad
    # ausnimmt, waere bis hierhin gruen.
    #
    # Deshalb laeuft im selben Abschnitt ein absichtlicher Uebeltaeter:
    # derselbe Auswerter, derselbe Aufruf, ein Programm das ZWEI Dateien
    # schreibt -- die Kontrolldatei (erlaubt) und eine zweite (verboten).
    # Erwartet wird genau EINE Verletzung. Findet er keine, ist Abschnitt 6
    # insgesamt wertlos, egal wie gruen er aussieht.
    # -----------------------------------------------------------------------
    cat >"$RT/uebeltaeter.py" <<'PYEOF'
import os
import sys
for pfad, n in ((sys.argv[1], 920), (sys.argv[2], 333)):
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, b"x" * n)
    os.close(fd)
PYEOF
    timeout --foreground --signal=TERM --kill-after=5s 60s \
      strace -f -e trace=openat,write -o "$RT/trace_boese.txt" \
      "$PY" -B -P "$RT/uebeltaeter.py" "$KONTROLLDATEI" "$RT/uebeltaeter.bin" \
      >/dev/null 2>"$RT/uebel.log"
    UB="$("$PY" -B -P "$RT/lies_trace.py" "$RT/trace_boese.txt" "$KONTROLLDATEI" 2>"$RT/lies2.log")"
    echo "  Gegenprobe am AUSWERTER (ein Programm, das absichtlich schreibt):"
    echo "    $(jq -c '{kontrolle_geschrieben, verletzungen, schreibende_openat}' <<<"${UB:-{\}}")"
    chk "6 GEGENPROBE: der Auswerter meldet einen fremden write als VERLETZUNG" \
      "$(jq -r '.verletzungen | length' <<<"${UB:-{\}}")" 1
    chk "6 GEGENPROBE: und das schreibende openat dazu" \
      "$(jq -r '.schreibende_openat | length' <<<"${UB:-{\}}")" 1
    chk "6 GEGENPROBE: die Kontrolldatei bleibt dabei erlaubt (keine Verwechslung)" \
      "$(jq -r '.kontrolle_geschrieben' <<<"${UB:-{\}}")" 1
  fi
fi

# =============================================================================
# 7. Konfigurierbarkeit des Vorlaufs -- an der Wirkung
# =============================================================================
echo
echo "--- 7. Konfigurierbarkeit ([ears.ring] vorlauf_s wirkt) ---"
KF="$(jq -c '.konfig // {}' <<<"$S")"
echo "  gesuchter Weg: $(jq -r '.weg // "(nicht gesucht)"' <<<"$KF")," \
     "Werte $(jq -c '.werte // []' <<<"$KF")"
echo "  verworfene Kandidaten: $(jq -r '.versuche // [] | join(" ; ")' <<<"$KF")"
echo "  Attrappe (bekannte Laengen 31 / 47 Chunks, DIESELBE Auswertung):" \
     "$(jq -r '.attrappe."31".chunks // "?"' <<<"$KF") /" \
     "$(jq -r '.attrappe."47".chunks // "?"' <<<"$KF")"
# Zuerst die Messkette, dann erst der Pruefling. Ohne diese beiden Zeilen
# waere "beide Werte ergeben dasselbe" nicht von "die Messung sieht den
# Unterschied nicht" zu trennen.
chk "7 POSITIVKONTROLLE: die Messkette liest 31 Chunks als 31" \
  "$(jq -r '.attrappe."31".chunks // -1' <<<"$KF")" 31
chk "7 POSITIVKONTROLLE: dieselbe Kette liest 47 Chunks als 47" \
  "$(jq -r '.attrappe."47".chunks // -1' <<<"$KF")" 47
chk "7 POSITIVKONTROLLE: die Kette unterscheidet zwei Vorlauflaengen ueberhaupt" \
  "$([[ "$(jq -r '.attrappe."31".chunks // -1' <<<"$KF")" \
      != "$(jq -r '.attrappe."47".chunks // -2' <<<"$KF")" ]] && echo ja || echo nein)" ja
chk "7 ein aeusserer Weg nimmt einen Vorlaufwert entgegen" \
  "$([[ "$(jq -r '.weg // "(keiner)"' <<<"$KF")" != "(keiner)" ]] && echo ja || echo nein)" ja
if [[ "$(jq -r '.weg // "(keiner)"' <<<"$KF")" != "(keiner)" ]]; then
  kl="$(jq -r '.klein.chunks // -1' <<<"$KF")"
  gr="$(jq -r '.gross.chunks // -1' <<<"$KF")"
  echo "  1,0 s -> $kl Chunks (aeltester $(jq -r '.klein.aeltester' <<<"$KF")," \
       "juengster $(jq -r '.klein.endet_bei' <<<"$KF"))"
  echo "  1,5 s -> $gr Chunks (aeltester $(jq -r '.gross.aeltester' <<<"$KF")," \
       "juengster $(jq -r '.gross.endet_bei' <<<"$KF"))"
  # 1,0 s = 31,25 Chunks, 1,5 s = 46,875 -- abgerundet 31 bzw. 46, aufgerundet
  # 32 bzw. 47. Beides ist eine vertretbare Auslegung, beides liegt im
  # Korridor; darunter oder darueber nicht mehr.
  chk "7 DIE ZUSAGE: 1,0 s ergeben 31..32 Chunks" \
    "$([[ "$kl" -ge 31 && "$kl" -le 32 ]] && echo ja || echo "war_$kl")" ja
  chk "7 DIE ZUSAGE: 1,5 s ergeben 46..47 Chunks" \
    "$([[ "$gr" -ge 46 && "$gr" -le 47 ]] && echo ja || echo "war_$gr")" ja
  chk "7 DIE ZUSAGE: der Wert wirkt ueberhaupt (die Laengen unterscheiden sich)" \
    "$(jqb "$KF" unterscheidet_sich)" true
  # Beide geprueften Werte liegen INNERHALB von 1,0..1,5 s -- gemessen wird
  # die Konfiguration, nicht ihre Verletzung.
  chk "7 beide geprueften Laengen liegen im Plankorridor 31..47" \
    "$([[ "$kl" -ge 31 && "$kl" -le 47 && "$gr" -ge 31 && "$gr" -le 47 ]] \
       && echo ja || echo nein)" ja
  # Und der Unterschied am INHALT, nicht bloss an der Laenge: Chunk 160 liegt
  # 1,28 s zurueck. Bei 1,0 s gehoert er nicht dazu, bei 1,5 s schon.
  chk "7 INHALT: bei 1,0 s fehlt der Chunk von 1,28 s davor (Nr. 160)" \
    "$(jqb "$(jq -c '.klein' <<<"$KF")" enthaelt_160_1s28_zurueck)" false
  chk "7 INHALT: bei 1,5 s ist er dabei" \
    "$(jqb "$(jq -c '.gross' <<<"$KF")" enthaelt_160_1s28_zurueck)" true
  chk "7 beide Vorlaeufe enden beim juengsten Chunk (Nr. 200)" \
    "$([[ "$(jq -r '.klein.endet_bei' <<<"$KF")" == 200 \
        && "$(jq -r '.gross.endet_bei' <<<"$KF")" == 200 ]] && echo ja || echo nein)" ja
fi

# --- Die Konfigurationsdatei selbst -----------------------------------------
TOML="$TARGET/config/daimon.toml"
[[ -f "$TOML" ]] || TOML="$REPO/config/daimon.toml"
tomlwert="$("$PY" -B -c '
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    d = tomllib.load(f)
r = (d.get("ears") or {}).get("ring")
print("fehlt" if not isinstance(r, dict) or "vorlauf_s" not in r
      else repr(r["vorlauf_s"]))
' "$TOML" 2>/dev/null)"
# NUR GEMELDET, NICHT GEWERTET. Was in der Datei steht, ist eine Angabe ueber
# sich selbst -- gemessen ist oben die Wirkung. Und ein Fixture-Baum bringt
# seine eigene (womoeglich beschnittene) Konfiguration mit; daran duerfte ein
# Mutant nicht scheitern, dessen Mutation ganz woanders sitzt.
echo "  $TOML: [ears.ring] vorlauf_s = ${tomlwert:-(nicht lesbar)}  (gemeldet, nicht gewertet)"

# =============================================================================
# OFFEN, und zwar benannt
# =============================================================================
echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) 'NICHT IM SWAP' IST NICHT GEMESSEN. Gemessen ist VmLck -- der"
echo "      Kernel bestaetigt damit, dass die Seiten als unauslagerbar"
echo "      markiert sind. Dass dieselben Bytes nie in einem Swap-Bereich"
echo "      landen, waere nur an /proc/<pid>/smaps ueber die Laufzeit oder am"
echo "      Swap-Geraet selbst zu zeigen, und beides ist auf dieser Maschine"
echo "      nicht ohne root zu haben."
echo "  (2) KOPIEN AUF DEM HEAP SIND NICHT GEPRUEFT. Die Akzeptanzliste"
echo "      warnt vor bytes(...)/Slicing/np.array beim Zugriff. Der Vorlauf"
echo "      MUSS aber herausgereicht werden -- und wo diese Kopie hinterher"
echo "      landet, entscheidet der Aufrufer, nicht dieser Baustein. Was hier"
echo "      geprueft ist: der Ring SELBST wird in place beschrieben"
echo "      (Abschnitt 2: derselbe schreibbare Puffer, feste Groesse) und"
echo "      beim Verwerfen in place genullt (Abschnitt 3: dieselbe Adresse)."
echo "  (3) 'FEHLTREFFER' IST HIER EIN METHODENAUFRUF, keine Kette. Dass der"
echo "      Auslоeser bei einer Ablehnung tatsaechlich zu genau diesem Aufruf"
echo "      fuehrt, entsteht erst in T-3.4/T-3.7 und gehoert dorthin."
echo "  (4) DER MITSCHNITT DECKT NUR openat UND write. Ein Schreibvorgang"
echo "      ueber creat(), io_uring oder einen bereits geerbten Deskriptor"
echo "      faellt hier nicht auf. Die schreibenden openat werden mitgezaehlt,"
echo "      weil mmap(MAP_SHARED) auf eine Datei sonst durchrutschte."
echo "  (5) DER TREIBER LAEUFT MIT -B. Ohne das schriebe CPython __pycache__"
echo "      und der Mitschnitt zeigte einen Schreibvorgang, den nicht der"
echo "      Pruefling verursacht hat. Das ist eine Bereinigung der Messkette,"
echo "      keine Nachsicht -- der Pruefling selbst bekommt keine."
echo "  (6) DIE 31..47 CHUNKS SIND EIN KORRIDOR, kein exakter Wert. Eine"
echo "      Umsetzung mit 1,0 s und eine mit 1,5 s sind hier beide gruen."
echo "  (7) DER EINSTIEGSPUNKT WIRD GESUCHT, nicht vorgeschrieben. Welche"
echo "      Klasse und welche Methoden gegriffen haben, steht oben im"
echo "      Protokoll -- samt der verworfenen Kandidaten und dem Grund."
echo "  (8) DIE KETTE TOML -> RING IST NICHT GEMESSEN. Abschnitt 7 zeigt,"
echo "      dass ein von aussen uebergebener Vorlaufwert die Laenge des"
echo "      Vorlaufs tatsaechlich veraendert. Was in config/daimon.toml steht,"
echo "      wird nur GEMELDET -- dass beim Start genau dieser Wert am"
echo "      Konstruktor ankommt, entscheidet die Verdrahtung in T-3.4."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-3.3: gruen. Der Ring ist 640 000 Byte gross und vorab alloziert, sein"
  echo "       Speicher ist nach VmLck des Kernels gesperrt, ein abgelehnter"
  echo "       Ausloeser laesst kein einziges Byte des Musters zurueck, der"
  echo "       Vorlauf traegt nachweislich die Chunks von 1,0-1,5 s VOR dem"
  echo "       Ausloesen -- und im strace-Mitschnitt steht genau ein"
  echo "       Schreibvorgang: der, den der Verifizierer selbst ausgeloest hat."
else
  echo "T-3.3: FEHLGESCHLAGEN"
fi
exit $fail
