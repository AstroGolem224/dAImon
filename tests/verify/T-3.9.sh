#!/usr/bin/env bash
# Verifizierer fuer T-3.9: Stimme (sherpa-onnx VITS, CPU).
#
# ============================================================================
# WAS HIER DIE MESSUNG IST -- UND WAS AUSDRUECKLICH NICHT
# ============================================================================
#
# Die bequemen Messungen sind alle falsch: `gesprochen: true` aus der Antwort
# des Dienstes ablesen (Fall 9 in HANDOVER.md), `grep provider` im Quelltext
# (T-1.7.v3), "kein Angriffstext wurde vorgelesen" ohne den harmlosen Satz,
# der vorgelesen WURDE (elf dokumentierte Faelle). Gemessen wird hier:
#
#   * AM PW-CAT-STUB. Der Dienst ruft `pw-cat` ohne absoluten Pfad auf (das
#     ist entschieden, Plan §2). Dieser Verifizierer legt einen Stub in den
#     PATH, der seine Argumente, seinen ersten Datenbyte-Zeitpunkt und sein
#     Ende protokolliert und die Audiodaten ZAEHLT. Gemessen wird, was der
#     Pruefling ausgeben WOLLTE. Die Soundkarte bleibt unberuehrt -- ein
#     Mutant ist dazu da, einem Angriff zu gehorchen, also darf der Angriff
#     nichts anrichten koennen (02.08., dreimal der Desktop weg).
#   * AM HUB, nicht am Dienst. Der Validator sitzt im Hub (Design §8.3).
#     Deshalb geht jeder Angriffstext ZWEIMAL: einmal an den Hub (der ihn
#     ablehnen muss, mit dem Grund der verletzten Regel) und einmal DIREKT
#     an den Dienst, ohne Hub-Freigabe (der ihn nicht sprechen darf). Ein
#     Verifizierer, der nur den Hub-Pfad prueft, liesse einen Dienst durch,
#     der jeden Direktzugriff spricht.
#   * AM LAUFENDEN PROZESS, AUS DEM GEPRUEFTEN BAUM. Hub und Dienst starten
#     aus $TARGET (Arbeitsbaum oder Fixture), mit Positivkontrollen, die den
#     geladenen Pfad bzw. PYTHONPATH des Prozesses pruefen (T-1.7.v5: dort
#     erreichte meta.sh die Live-Pruefungen NIE, weil dem Fixture drei
#     Module fehlten).
#   * 0 VRAM am laufenden Prozess: /proc/<pid>/maps, /proc/<pid>/fd und
#     nvidia-smi --query-compute-apps, nicht am Quelltext (Muster T-1.4).
#   * AM SYSTEM zum Schluss: kein daimon.face.tts-Prozess mit Pfaden unter
#     $RT darf den Lauf ueberleben. Der Dienst ist ein Enkelkind
#     (systemd-socket-activate startet ihn erst bei der ersten Verbindung);
#     ein Kill der Aktivator-PID allein liess ihn verwaist zurueck --
#     gemessen am 05.08.: 4 Waisen je Lauf, 140 ueber 15 Stunden, 10 GiB
#     RSS. Deshalb laeuft der Dienst in einer eigenen Prozessgruppe
#     (setsid) und das Aufraeumen killt die GRUPPE, mit den bekannten
#     Einzel-PIDs als Rueckfall.
#
# ZU JEDER NEGATIVPRUEFUNG EINE POSITIVKONTROLLE IM SELBEN LAUF:
#   * Sieben verbotene Angriffstexte werden nicht vorgelesen; bei Bidi,
#     Nullbreite und Steuerzeichen wird nur der bereinigte Rest gesprochen --
#     UND der harmlose Kanariensatz wird vorgelesen (Stub sieht Daten).
#   * Ungefragt lehnt freien Text ab -- UND nimmt eine kuratierte Vorlage.
#   * Abkuehlung sperrt -- UND gibt nach der Frist frei (alle drei Kanaele,
#     20/10/3 s, gewaeitert und nachgemessen, nicht nur `rest_s` abgelesen).
#   * Abkuehlung ueberlebt den Neustart -- UND die monotone Uhr beendet sie
#     (ein gestellter Uhrentreiber, Muster T-3.4 Abschnitt 6).
#   * Steuerzeichen werden entfernt -- UND der Rest des Satzes bleibt stehen.
#   * Eine verbotene Stimmlizenz wird verweigert -- UND dieselbe Dateiliste
#     mit CC0-Karte wird angenommen (belegt, dass MODEL_CARD gelesen wird).
#   * Der Dienst weist Direktzugriff ohne Marke ab -- UND spricht mit Marke.
#
# WAS DIESES SKRIPT NICHT PRUEFT -- ausgeschrieben, nicht kaschiert:
#   * Kriterium 8 (Meldung an die Rueckkopplungssperre) ist laut Plan §3.1
#     nur TEILERFUELLBAR: kein Prozess haelt eine `Sperre`, der Dienst meldet
#     an den Hub (`voice.tts_active`). Geprueft wird, was da ist: das Flag im
#     Hub-Zustand waehrend und nach der Wiedergabe, korreliert mit dem
#     Stub-Protokoll. Die fehlende Echo-Referenz ist als Luecke benannt.
#   * Klangqualitaet, Aussprache, Satzmelodie. Gemessen wird DASS und WANN
#     Daten fliessen, nicht WIE sie klingen.
#   * Dass pw-cat die Daten WIRKLICH an PipeWire weitergegeben haette. Der
#     Messpunkt ist absichtlich davor.
#   * TTFA unter Last. Gemessen wird im Leerlauf, sequentiell.
#
# Aufruf:
#   tests/verify/T-3.9.sh                # gegen den Arbeitsbaum
#   DAIMON_FIXTURE=<baum> tests/verify/T-3.9.sh   # gegen Mutant/Gut-Muster
#     (der Baum muss daimon/ UND config/systemd/ mitbringen; das Modell kommt
#     immer aus dem echten spikes/-Bestand oder DAIMON_T39_MODELL_DIR)
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"
MODELL_DIR="${DAIMON_T39_MODELL_DIR:-$REPO/spikes/nvidia-voice/models/vits-piper-de_DE-thorsten-high}"

fail=0
declare -A PRUEFUNGEN ROT
SEK="V"
chk() {
  # Jede Pruefung einzeln, ohne &&-Verkettung, mit eigener Zaehlung je
  # Kriterium -- die Abschlusszeilen nennen die Zahl je Kriterium.
  PRUEFUNGEN[$SEK]=$(( ${PRUEFUNGEN[$SEK]:-0} + 1 ))
  if [[ "$2" == "$3" ]]; then
    echo "  ok   $1"
  else
    echo "  FAIL $1 (erwartet $3, war $2)"
    ROT[$SEK]=$(( ${ROT[$SEK]:-0} + 1 ))
    fail=1
  fi
}
laut() { echo "  !! $*"; }

echo "T-3.9 — Stimme: der Validator sitzt im Hub, der Dienst spricht nur mit Marke"
echo "  Baum: $TARGET"
echo "  Modell: $MODELL_DIR"
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then echo "  Modus: FIXTURE"; else echo "  Modus: ARBEITSBAUM"; fi

# =============================================================================
# 0. Voraussetzungen
# =============================================================================
echo
echo "--- 0. Voraussetzungen ---"
chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "Interpreter vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
chk "echtes pw-cat vorhanden (Referenz, wird NICHT angeruehrt)" \
  "$(command -v pw-cat >/dev/null && echo ja || echo nein)" ja
chk "systemd-socket-activate vorhanden" \
  "$(command -v systemd-socket-activate >/dev/null && echo ja || echo nein)" ja
chk "setsid vorhanden (Prozessgruppe des Dienstes)" \
  "$(command -v setsid >/dev/null && echo ja || echo nein)" ja
chk "nvidia-smi vorhanden" "$(command -v nvidia-smi >/dev/null && echo ja || echo nein)" ja
chk "Modellverzeichnis vorhanden" "$([[ -d "$MODELL_DIR" ]] && echo ja || echo nein)" ja
chk "der Baum bringt daimon/hub/sprechtext.py mit" \
  "$([[ -f "$TARGET/daimon/hub/sprechtext.py" ]] && echo ja || echo nein)" ja
chk "der Baum bringt daimon/face/tts.py mit" \
  "$([[ -f "$TARGET/daimon/face/tts.py" ]] && echo ja || echo nein)" ja
chk "der Baum bringt config/systemd/daimon-tts.service mit" \
  "$([[ -f "$TARGET/config/systemd/daimon-tts.service" ]] && echo ja || echo nein)" ja
chk "der Baum bringt config/systemd/daimon-tts.socket mit" \
  "$([[ -f "$TARGET/config/systemd/daimon-tts.socket" ]] && echo ja || echo nein)" ja
chk "der Baum bringt den Hub mit (die Grenze liegt im Hub)" \
  "$([[ -f "$TARGET/daimon/hub/daemon.py" ]] && echo ja || echo nein)" ja
chk "sherpa-onnx im Interpreter" \
  "$("$PY" -c 'import sherpa_onnx' 2>/dev/null && echo ja || echo nein)" ja
# Grundlinie fuer 'kein zusaetzlicher Compute-Prozess' (K10): VOR jedem Start.
NVIDIA_VORHER="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ' | sort)"

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "T-3.9: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen."
  exit 1
fi

RT="$(mktemp -d)"
STUBBIN="$RT/bin"; mkdir -p "$STUBBIN"
STUBLOG="$RT/pw-cat.log"; : >"$STUBLOG"
HUBPIDS=()
DIENSTPIDS=()
GRUPPEN=()
aufraeumen() {
  # Die Dienste laufen in EIGENEN Prozessgruppen (setsid in dienst_start):
  # systemd-socket-activate startet den Dienst erst bei der ersten Verbindung,
  # er ist also ein Enkelkind -- ein Kill der Aktivator-PID allein laesst es
  # verwaist zurueck (am 05.08. gemessen: 4 Waisen je Lauf, 140 ueber Nacht).
  # Die Gruppe ist der Hauptweg, die einzelnen PIDs der Rueckfall.
  local p
  for p in "${GRUPPEN[@]:-}"; do [[ -n "$p" ]] && kill -- -"$p" 2>/dev/null; done
  for p in "${DIENSTPIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null; done
  for p in "${HUBPIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null; done
  sleep 0.3
  for p in "${GRUPPEN[@]:-}"; do [[ -n "$p" ]] && kill -9 -- -"$p" 2>/dev/null; done
  for p in "${DIENSTPIDS[@]:-}" "${HUBPIDS[@]:-}"; do [[ -n "$p" ]] && kill -9 "$p" 2>/dev/null; done
  rm -rf -- "$RT"
}
trap aufraeumen EXIT INT TERM

# =============================================================================
# Der pw-cat-Stub: protokolliert, was der Pruefling ausgeben wollte.
# Die Soundkarte bleibt unberuehrt.
# =============================================================================
cat >"$STUBBIN/pw-cat" <<'STUBEOF'
#!/usr/bin/env python3
# pw-cat-Stub des T-3.9-Verifizierers. Protokolliert Argumente, den
# Zeitpunkt des ersten Datenbytes, JEDEN empfangenen Block und das geordnete
# Prozessende. Die fortlaufenden BYTES-Zeilen sind wesentlich: der Pruefling
# unterbricht mit SIGKILL. Dann laeuft weder eine Shell-EXIT-Falle noch ein
# Python-finally, obwohl vorher nachweislich Audio angekommen ist.
import os
import sys
import time

log = os.environ.get("DAIMON_PWCAT_LOG")
if not log:
    raise SystemExit("DAIMON_PWCAT_LOG fehlt")
pid = os.getpid()
rate = 22050
for i, arg in enumerate(sys.argv[1:]):
    if arg == "--rate" and i + 2 < len(sys.argv):
        rate = int(sys.argv[i + 2])
    elif arg.startswith("--rate="):
        rate = int(arg.split("=", 1)[1])

fd_log = os.open(log, os.O_WRONLY | os.O_APPEND)

def protokoll(art, *werte):
    zeile = " ".join((art, str(pid), str(time.time_ns()), *(str(w) for w in werte))) + "\n"
    os.write(fd_log, zeile.encode("utf-8"))

gesamt = 0
protokoll("START", *sys.argv[1:])
try:
    while True:
        block = os.read(0, 4096)
        if not block:
            break
        if gesamt == 0:
            protokoll("FIRST")
        gesamt += len(block)
        # VOR dem Schlaf protokollieren: wird der Stub im Schlaf per SIGKILL
        # beendet, bleibt die bis dahin empfangene Bytemenge trotzdem messbar.
        protokoll("BYTES", gesamt)
        time.sleep(len(block) / (rate * 2))
finally:
    protokoll("EXIT", gesamt)
    os.close(fd_log)
STUBEOF
chmod +x "$STUBBIN/pw-cat"

# =============================================================================
# Hilfsklients und Treiber
# =============================================================================
cat >"$RT/klient.py" <<'PYEOF'
"""Eine Zeile JSON hin (aus einer Datei), eine Zeile JSON zurueck.

Fuer state.sock/diag.sock, die nichts lesen: die Anfrage darf leer sein.
"""
import socket
import sys

pfad, datei = sys.argv[1], sys.argv[2]
timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
nutzlast = b""
if datei != "-":
    with open(datei, "rb") as fh:
        nutzlast = fh.read().strip()

c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(timeout)
try:
    c.connect(pfad)
    if nutzlast:
        c.sendall(nutzlast + b"\n")
    zeile = c.makefile("rb").readline(1 << 20)
except OSError as exc:
    print(f"__FEHLER__:{exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    c.close()
sys.stdout.write(zeile.decode("utf-8", "replace"))
PYEOF

# Sendet eine JSON-Datei an einen Socket, gibt die Antwortzeile aus.
anfrage() {  # $1 = Socket, $2 = JSON-Datei oder "-", $3 = Timeout (Vorgabe 5)
  "$PY" -B "$RT/klient.py" "$1" "$2" "${3:-5}" 2>/dev/null
}

# Schreibt ein JSON-Objekt in eine Datei. Uebergabe als Argumente ist fuer
# die Angriffstexte ungeeignet (Quoting), also baut sie Python.
jschreib() {  # $1 = Zieldatei, ab $2 = Schluessel=Wert (Werte sind Strings)
  "$PY" -B - "$@" <<'PYEOF'
import json, sys
ziel, paare = sys.argv[1], sys.argv[2:]
d = {}
for p in paare:
    k, _, v = p.partition("=")
    # `v` ist eine Zahl, kein String -- der Rest sind Textfelder.
    if k == "v":
        v = int(v)
    d[k] = v
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
PYEOF
}

# grep -c meldet 0 Treffer mit rc=1, aber MIT Ausgabe der 0 -- ein
# `|| echo 0` dahinter erzeugte "0\n0" und brach jede Arithmetik.
stub_zahl() { grep -c "^$1" "$STUBLOG" 2>/dev/null || true; }
stub_bytes() {
  awk '/^BYTES/ {b[$2]=$4} /^EXIT/ {if ($4>b[$2]) b[$2]=$4}
       END {for (p in b) s+=b[p]; print s+0}' "$STUBLOG"
}
stub_pid_bytes() { awk -v p="$1" '$2==p && ($1=="BYTES" || $1=="EXIT") {b=$4} END {print b+0}' "$STUBLOG"; }
stub_letzte_pid() { awk '/^START/ {p=$2} END {print p}' "$STUBLOG"; }

# Wartet auf eine Zeile im Stub-Protokoll. $1 = Muster (grep -E), $2 = Timeout s
stub_warte() {
  local ende=$(( SECONDS + $2 ))
  while :; do
    grep -qE "$1" "$STUBLOG" && return 0
    [[ "$SECONDS" -ge "$ende" ]] && return 1
    sleep 0.02
  done
}

jetzt_ns() { date +%s%N; }

# =============================================================================
# Der Hub-Treiber: startet den Hub AUS DEM GEPRUEFTEN BAUM, mit eigener
# Konfiguration (state_dir im Temp), optional mit versetzten Uhren.
# =============================================================================
cat >"$RT/hub_lauf.py" <<'PYEOF'
"""Startet den Hub des geprueften Baums.

Die Uhren koennen VOR jedem daimon-Import versetzt werden
(DAIMON_UHR_MONO_S / DAIMON_UHR_WAND_S): die Abkuehlung darf auf die
Wanduhr nicht hoeren (NTP-Korrektur), und die monotone Uhr muss sie
beenden. Ohne den Versatz vor dem Import greift er bei
`from time import monotonic` nicht.
"""
import copy
import json
import os
import sys
import threading
import time as _t

BAUM, RTDIR, STATEDIR = sys.argv[1], sys.argv[2], sys.argv[3]

_versatz_m = float(os.environ.get("DAIMON_UHR_MONO_S", "0") or 0)
_versatz_w = float(os.environ.get("DAIMON_UHR_WAND_S", "0") or 0)
if _versatz_m or _versatz_w:
    _mono, _wall = _t.monotonic, _t.time
    _t.monotonic = lambda: _mono() + _versatz_m
    _t.monotonic_ns = lambda: int((_mono() + _versatz_m) * 1e9)
    _t.time = lambda: _wall() + _versatz_w
    _t.time_ns = lambda: int((_wall() + _versatz_w) * 1e9)

sys.path.insert(0, BAUM)
from pathlib import Path  # noqa: E402

from daimon.common.config import Config, VORGABEN  # noqa: E402

daten = copy.deepcopy(VORGABEN)
_overlay = os.environ.get("DAIMON_HUB_CFG", "")
if _overlay:
    def _mische(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                _mische(a[k], v)
            else:
                a[k] = v
    _mische(daten, json.loads(_overlay))

from daimon.hub.daemon import Hub  # noqa: E402

print("HUB_DATEI=" + os.path.abspath(sys.modules["daimon.hub.daemon"].__file__),
      flush=True)
cfg = Config(data=daten, config_dir=Path(STATEDIR), state_dir=Path(STATEDIR),
             runtime_dir=Path(RTDIR))
h = Hub(cfg=cfg, runtime_dir=Path(RTDIR))
h.start([])
print("HUB_BEREIT", flush=True)
threading.Event().wait()
PYEOF

# Startet einen Hub. $1 = Laufname (rt-Unterverzeichnis), $2 = state_dir,
# $3 = extra env (z.B. Uhrenversatz). Setzt HUB_PID und HUB_RT.
HUB_PID=""; HUB_RT=""
hub_start() {
  HUB_RT="$RT/hubrt-$1"; mkdir -p "$HUB_RT"
  [[ -n "${TTS_SOCK_NAME:-}" ]] && TTS_SOCK="$HUB_RT/$TTS_SOCK_NAME"
  env "${@:4}" DAIMON_BAUM="$TARGET" PYTHONDONTWRITEBYTECODE=1 \
    "$PY" -B -P "$RT/hub_lauf.py" "$TARGET" "$HUB_RT" "$2" \
    >"$RT/hub-$1.log" 2>&1 &
  HUB_PID=$!
  HUBPIDS+=("$HUB_PID")
  local i
  for i in $(seq 1 100); do
    grep -q "^HUB_BEREIT" "$RT/hub-$1.log" 2>/dev/null && return 0
    kill -0 "$HUB_PID" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}
hub_stop() {
  [[ -n "$HUB_PID" ]] && kill "$HUB_PID" 2>/dev/null
  [[ -n "$HUB_PID" ]] && wait "$HUB_PID" 2>/dev/null
  HUB_PID=""
}

# =============================================================================
# Der Validator-Treiber (in-process, AUS DEM BAUM): die Regeln aus §8.3,
# jede einzeln. Der Rueckgabetyp von pruefe() ist der Implementierung
# ueberlassen -- normalisiert wird auf (ok, text, grund); die verwendete
# Form steht im Protokoll.
# =============================================================================
cat >"$RT/validator_treiber.py" <<'PYEOF'
import importlib
import json
import os
import sys
import traceback

BAUM = sys.argv[1]
sys.path.insert(0, BAUM)

ERG = {"ok": True}


def norm(r):
    """(ok, text, grund) aus den denkbaren Rueckgabeformen."""
    if hasattr(r, "als_dict") and callable(r.als_dict):
        r = r.als_dict()
    if isinstance(r, dict):
        ok = r.get("ok")
        text = r.get("text", r.get("sprechtext", ""))
        grund = r.get("grund", r.get("reason", ""))
        return bool(ok), (text if isinstance(text, str) else ""), str(grund or "")
    if isinstance(r, tuple) and len(r) == 2:
        ok, nutz = r
        return bool(ok), (nutz if ok and isinstance(nutz, str) else ""), \
            ("" if ok else str(nutz))
    if isinstance(r, str):
        return True, r, ""
    ok = getattr(r, "ok", None)
    if ok is not None:
        return bool(ok), str(getattr(r, "text", "") or ""), \
            str(getattr(r, "grund", "") or "")
    raise TypeError(f"unbekannte Rueckgabeform: {type(r).__name__}")


try:
    modul = importlib.import_module("daimon.hub.sprechtext")
except Exception as exc:
    print(json.dumps({"ok": False, "phase": "import",
                      "fehler": f"{type(exc).__name__}: {exc}",
                      "spur": traceback.format_exc()[-1200:]}))
    raise SystemExit(0)
ERG["datei"] = os.path.abspath(modul.__file__)

pruefe = None
for name in ("pruefe", "prüfe", "validiere", "pruefe_text"):
    f = getattr(modul, name, None)
    if callable(f):
        pruefe, form = f, name
        break
if pruefe is None:
    print(json.dumps({"ok": False, "phase": "vertrag",
                      "fehler": "keine Prueffunktion gefunden",
                      "angebot": sorted(a for a in dir(modul)
                                        if not a.startswith("_"))}))
    raise SystemExit(0)
ERG["funktion"] = form


def rufe(text, kanal):
    try:
        ok, text_r, grund = norm(pruefe(text, kanal=kanal))
        return {"ok": ok, "text": text_r, "grund": grund}
    except Exception as exc:
        return {"ausnahme": f"{type(exc).__name__}: {exc}"}


FAELLE = {
    # (text, kanal) -- die Erwartung wertet das Skript aus.
    "harmlos": ("Der Kaffee ist fertig.", "reaktion"),
    "nfc_zerlegt": ("Mu\u0308ller ist da.", "reaktion"),
    "genau_140": ("x" * 139 + ".", "reaktion"),
    "fuenfhundert": ("Das ist ein Satz. " * 30, "reaktion"),
    "mehrzeilig": ("Zeile eins\nZeile zwei", "reaktion"),
    "code_function": ("function r() { return 1; }", "reaktion"),
    "code_const": ("const x = 42", "reaktion"),
    "code_import": ("import os", "reaktion"),
    "code_klammern": ("a = {};", "reaktion"),
    "code_fence": ("```python\nprint(1)\n```", "reaktion"),
    "url_https": ("https://boese.example/ab?c=d", "reaktion"),
    "url_www": ("www.boese.example", "reaktion"),
    "pfad_absolut": ("/etc/passwd", "reaktion"),
    "pfad_heimat": ("~/Dokumente/geheim.txt", "reaktion"),
    "pfad_eltern": ("../anderes/geheim", "reaktion"),
    "geheimnis_apikey": ("api_key=sk-1234567890abcdef", "reaktion"),
    "geheimnis_password": ("password: hunter2", "reaktion"),
    "geheimnis_token": ("token=ghp_abcdef", "reaktion"),
    "geheimnis_secret": ("secret = zyx", "reaktion"),
    "bidi": ("Hal‮lo‬", "reaktion"),
    "nullbreite": ("Unsicht​bar", "reaktion"),
    "steuerzeichen": ("Klingelton", "reaktion"),
    "umlaute": ("Größe, Maße, süß -- alles erlaubt.", "reaktion"),
    "ungefragt_freier_text": ("Der Kaffee ist fertig.", "ungefragt"),
    "ungefragt_angriff": ("api_key=sk-123", "ungefragt"),
    "unbekannter_kanal": ("Hallo.", "draussen"),
}
ERG["faelle"] = {name: rufe(text, kanal) for name, (text, kanal) in FAELLE.items()}

# Die kuratierten Vorlagen: gesucht wird eine Menge von Schluesseln auf
# deutsche Saetze -- nicht ein Name, sondern die Form.
vorlagen = []
for attr in dir(modul):
    if attr.startswith("_"):
        continue
    wert = getattr(modul, attr)
    if isinstance(wert, dict) and wert and \
            all(isinstance(k, str) and isinstance(v, str) for k, v in wert.items()):
        if any(" " in v and len(v) > 10 for v in wert.values()):
            vorlagen.append({"attribut": attr, "schluessel": sorted(wert),
                             "beispiele": dict(wert)})
ERG["vorlagen"] = vorlagen
print(json.dumps(ERG, ensure_ascii=False, default=str))
PYEOF

echo
SEK="K6"
echo "--- K6/K12 (in-process): der Validator, jede Regel einzeln ---"
timeout --foreground --signal=TERM --kill-after=5s 60s \
  "$PY" -B -P "$RT/validator_treiber.py" "$TARGET" >"$RT/validator.json" \
  2>"$RT/validator.log"
if ! jq -e . >/dev/null 2>&1 <"$RT/validator.json"; then
  laut "Der Validator-Treiber hat keine auswertbare Antwort geliefert."
  tail -20 "$RT/validator.log"
  chk "Validator-Treiber laeuft" nein ja
  echo; echo "T-3.9: FEHLGESCHLAGEN"; exit 1
fi
V="$(cat "$RT/validator.json")"
if [[ "$(jq -r '.ok' <<<"$V")" != true ]]; then
  laut "Phase: $(jq -r '.phase // "?"' <<<"$V") -- $(jq -r '.fehler // "?"' <<<"$V")"
  jq -r '.spur // empty' <<<"$V"
  chk "daimon.hub.sprechtext laedt und pruefe() ist ansprechbar" nein ja
  echo; echo "T-3.9: FEHLGESCHLAGEN — ohne Validator ist nichts gemessen."
  exit 1
fi
vdatei="$(jq -r '.datei' <<<"$V")"
echo "  geladen aus: $vdatei  (Funktion: $(jq -r '.funktion' <<<"$V")"
chk "POSITIVKONTROLLE: der Validator stammt AUS DEM GEPRUEFTEN BAUM" \
  "$([[ "$vdatei" == "$TARGET"/* ]] && echo ja || echo "aus_$vdatei")" ja

vf() { jq -r --arg n "$1" --arg f "$2" '
  if (.faelle[$n] | has($f))
  then (.faelle[$n][$f] | if type == "boolean" then tostring else . end)
  else "FEHLT" end' <<<"$V"; }
vok() { vf "$1" ok; }
vgrund() { vf "$1" grund; }
vtext() { vf "$1" text; }
keine_ausnahme() { [[ "$(vf "$1" ausnahme)" == "FEHLT" ]] && echo ja || { laut "Ausnahme bei $1: $(vf "$1" ausnahme)"; echo nein; }; }

# Positivkontrollen zuerst. Ein Validator, der alles ablehnt -- oder einer,
# der nicht geladen wurde -- erfuellt jede Ablehnung darunter muehelos.
chk "POSITIVKONTROLLE: ein harmloser Satz ist sprechbar (reaktion)" "$(vok harmlos)" true
chk "POSITIVKONTROLLE: NFC-zerlegte Umlaute sind sprechbar" "$(vok nfc_zerlegt)" true
chk "POSITIVKONTROLLE: Umlaute sind kein Regelverstoss" "$(vok umlaute)" true
chk "140 Zeichen sind sprechbar (die Grenze ist '~140')" "$(vok genau_140)" true

# Jede Regel einzeln, mit dem Grund aus der Akzeptanzliste.
chk "500 Zeichen: abgelehnt" "$(vok fuenfhundert)" false
chk "500 Zeichen: Grund ist zu_lang" "$(vgrund fuenfhundert)" zu_lang
chk "mehrzeilig: abgelehnt" "$(vok mehrzeilig)" false
chk "mehrzeilig: Grund ist mehrzeilig" "$(vgrund mehrzeilig)" mehrzeilig
for fall in code_function code_const code_import code_klammern; do
  chk "$fall: abgelehnt" "$(vok "$fall")" false
  chk "$fall: Grund ist code" "$(vgrund "$fall")" code
done
# Der Codeblock ist zugleich mehrzeilig -- beide Gruende sind ehrlich.
chk "Codeblock: abgelehnt" "$(vok code_fence)" false
chk "Codeblock: Grund ist code oder mehrzeilig" \
  "$([[ "$(vgrund code_fence)" == code || "$(vgrund code_fence)" == mehrzeilig ]] && echo ja || echo "$(vgrund code_fence)")" ja
for fall in url_https url_www; do
  chk "$fall: abgelehnt" "$(vok "$fall")" false
  chk "$fall: Grund ist url" "$(vgrund "$fall")" url
done
for fall in pfad_absolut pfad_heimat pfad_eltern; do
  chk "$fall: abgelehnt" "$(vok "$fall")" false
  chk "$fall: Grund ist pfad" "$(vgrund "$fall")" pfad
done
for fall in geheimnis_apikey geheimnis_password geheimnis_token geheimnis_secret; do
  chk "$fall: abgelehnt" "$(vok "$fall")" false
  chk "$fall: Grund ist geheimnis" "$(vgrund "$fall")" geheimnis
done

# Steuerzeichen, Bidi, Nullbreite: ENTFERNT, nicht escapt. Der Rest bleibt.
chk "Bidi-Override: der Text ist sprechbar (die Zeichen fallen, nicht der Satz)" "$(vok bidi)" true
chk "Bidi-Override: entfernt -- kein U+202E/U+202C mehr im Text" \
  "$("$PY" -B -c 'import sys; t=sys.argv[1]; print("ja" if "\u202e" not in t and "\u202c" not in t else "nein")' "$(vtext bidi)")" ja
chk "Bidi-Override: NICHT escapt -- keine Backslash-Escape-Folge im Text" \
  "$([[ "$(vtext bidi)" != *\\* ]] && echo ja || echo nein)" ja
chk "Bidi-Override: der Rest ist 'Hallo'" "$(vtext bidi)" "Hallo"
chk "Nullbreite: entfernt, Rest ist sprechbar" "$(vok nullbreite)" true
chk "Nullbreite: kein U+200B mehr im Text" \
  "$("$PY" -B -c 'import sys; print("ja" if "\u200b" not in sys.argv[1] else "nein")' "$(vtext nullbreite)")" ja
chk "Steuerzeichen (BEL): entfernt, Rest ist sprechbar" "$(vok steuerzeichen)" true
chk "Steuerzeichen (BEL): nicht mehr im Text" \
  "$("$PY" -B -c 'import sys; print("ja" if "\x07" not in sys.argv[1] else "nein")' "$(vtext steuerzeichen)")" ja

# Kriterium 5 auf der Modulebene: freier Text auf `ungefragt` wird ABGELEHNT,
# auch ein harmloser -- und ein Angriffstext erst recht.
chk "ungefragt: freier (harmloser) Text wird abgelehnt, nicht gesaeubert" \
  "$(vok ungefragt_freier_text)" false
chk "ungefragt: ein Angriffstext wird abgelehnt" "$(vok ungefragt_angriff)" false
chk "kein Fall wirft eine Ausnahme" \
  "$(jq -r '[.faelle[] | has("ausnahme")] | any | not' <<<"$V")" true

# Die Gruende sind maschinenlesbar und je Regel getrennt -- kein gemeinsames
# `ungueltig: true` (Lehre aus T-3.7).
gruende="$(jq -r '[.faelle[] | select(.ok == false) | .grund] | unique | join(",")' <<<"$V")"
echo "  vergebene Gruende: $gruende"
chk "mindestens 7 verschiedene Gruende (je Regel einer)" \
  "$([[ "$(jq -r '[.faelle[] | select(.ok == false) | .grund] | unique | length' <<<"$V")" -ge 7 ]] && echo ja || echo nein)" ja
chk "kein Sammelgrund 'ungueltig'" \
  "$(jq -r '[.faelle[] | select(.ok == false) | .grund] | any(. == "ungueltig") | not' <<<"$V")" true

# Die kuratierten Vorlagen muessen auffindbar sein -- sonst ist "zieht nur
# aus kuratierten Vorlagen" nicht pruefbar.
vorlage_schluessel="$(jq -r '.vorlagen[0].schluessel // [] | join(",")' <<<"$V")"
vorlage_attr="$(jq -r '.vorlagen[0].attribut // ""' <<<"$V")"
echo "  Vorlagen gefunden: ${vorlage_attr:-(keine)} [$vorlage_schluessel]"
chk "eine kuratierte Vorlagenmenge ist auffindbar (POSITIVKONTROLLE fuer K5)" \
  "$([[ -n "$vorlage_schluessel" ]] && echo ja || echo nein)" ja

# =============================================================================
# Der Hub, live, AUS DEM GEPRUEFTEN BAUM
# =============================================================================
echo
SEK="K6"
echo "--- K6/K12 (live): der Hub lehnt die zehn Angriffstexte selbst ab ---"
HUBSTATE="$RT/state"; mkdir -p "$HUBSTATE"
hub_start main "$HUBSTATE"
chk "Hub aus dem geprueften Baum startet" \
  "$([[ -n "$HUB_PID" ]] && kill -0 "$HUB_PID" 2>/dev/null && echo ja || { tail -5 "$RT/hub-main.log"; echo nein; })" ja
hub_datei="$(sed -n 's/^HUB_DATEI=//p' "$RT/hub-main.log" | head -1)"
echo "  laufender Hub: $hub_datei"
chk "POSITIVKONTROLLE: der LAUFENDE Hub stammt aus dem geprueften Baum" \
  "$([[ -n "$hub_datei" && "$hub_datei" == "$TARGET"/* ]] && echo ja || echo nein)" ja

# --- Endpunkt-Entdeckung -----------------------------------------------------
# Der Plan nennt die Form ({art, kanal, text|vorlage}), nicht den Socketnamen
# und nicht die Schreibweise der Art. Gesucht wird: ein lesender Socket, der
# auf eine Sprech-Anfrage mit ok:true und einer Marke antwortet. Die
# Produzenten- und Diagnosesockets werden uebersprungen (sie antworten nie
# oder mit etwas anderem).
TTS_SOCK=""; HUB_ART=""; MARKE_FELD=""
jschreib "$RT/probe.json" v=1 kanal=reaktion text="Der Pruefsockel summt leise."
for s in "$HUB_RT"/*.sock; do
  basis="$(basename "$s")"
  case "$basis" in
    face.sock|auth.sock|hookbridge.sock|ears.sock|eyes.sock|kwin.sock|\
events.sock|state.sock|diag.sock|gpu.sock) continue;;
  esac
  for art in sprich freigabe sprechfreigabe tts; do
    jschreib "$RT/probe.json" v=1 art="$art" kanal=reaktion text="Der Pruefsockel summt leise."
    antwort="$(anfrage "$s" "$RT/probe.json" 3)"
    [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)" == "true" ]] || continue
    for feld in marke freigabe sperre token; do
      m="$(jq -r --arg f "$feld" '.[$f] // empty' <<<"$antwort")"
      if [[ -n "$m" ]]; then
        TTS_SOCK="$s"; TTS_SOCK_NAME="$(basename "$s")"
        HUB_ART="$art"; MARKE_FELD="$feld"
        break 3
      fi
    done
  done
done
echo "  Endpunkt: ${TTS_SOCK:-(keiner)}  art=${HUB_ART:-?}  marke-feld=${MARKE_FELD:-?}"
chk "ein Sprech-Endpunkt ist am Hub auffindbar (POSITIVKONTROLLE)" \
  "$([[ -n "$TTS_SOCK" ]] && echo ja || echo nein)" ja
if [[ -z "$TTS_SOCK" ]]; then
  echo "  Sockets im Laufzeitverzeichnis:"; ls -la "$HUB_RT"
  echo; echo "T-3.9: FEHLGESCHLAGEN — ohne Sprech-Endpunkt ist der Rest ungemessen."
  exit 1
fi

# Eine Freigabe holen. $1 = kanal, $2 = text -- gibt die Antwort-JSON aus.
freigabe() {
  jschreib "$RT/freq.json" v=1 art="$HUB_ART" kanal="$1" text="$2"
  anfrage "$TTS_SOCK" "$RT/freq.json" 8
}
# Eine Vorlagen-Anfrage. $1 = Feldname (vorlage|anlass), $2 = Schluessel,
# $3 = markierung (Vorgabe trusted)
vorlage_anfrage() {
  "$PY" -B - "$RT/freqv.json" "$HUB_ART" "$1" "$2" "${3:-trusted}" <<'PYEOF'
import json, sys
ziel, art, feld, name, markierung = sys.argv[1:6]
d = {"v": 1, "art": art, "kanal": "ungefragt", feld: name,
     "markierung": markierung, "werte": {"projekt": "Pruefprojekt"}}
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
PYEOF
  anfrage "$TTS_SOCK" "$RT/freqv.json" 8
}
# Einen Vorlagen-Schluessel waehlen: einer ohne Platzhalter, sonst der erste.
VORLAGE_NAME="$("$PY" -B - "$RT/validator.json" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    v = (json.load(fh).get("vorlagen") or [{}])[0]
keys = v.get("schluessel") or []
beispiele = v.get("beispiele") or {}
freie = [k for k in keys if "{" not in beispiele.get(k, "{")]
print(freie[0] if freie else (keys[0] if keys else ""))
PYEOF
)"
# Welches Feld traegt die Vorlage in der Anfrage? Beide Kandidaten probieren.
VORLAGE_FELD=""
for feld in vorlage anlass; do
  antwort="$(vorlage_anfrage "$feld" "$VORLAGE_NAME" trusted)"
  if [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)" == "true" ]]; then
    VORLAGE_FELD="$feld"
    break
  fi
done
echo "  Vorlage fuer die Positivkontrolle: '${VORLAGE_NAME:-?}' ueber Feld '${VORLAGE_FELD:-?}'"

# POSITIVKONTROLLE vor jedem Angriff: der harmlose Satz bekommt eine Freigabe.
antwort="$(freigabe reaktion "Der Kanarienvogel singt leise.")"
chk "POSITIVKONTROLLE: der harmlose Satz bekommt eine Hub-Freigabe" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" true
chk "die Freigabe traegt eine nichtleere Marke" \
  "$([[ -n "$(jq -r --arg f "$MARKE_FELD" '.[$f] // empty' <<<"$antwort")" ]] && echo ja || echo nein)" ja

# --- Die zehn Angriffstexte am Hub ------------------------------------------
# Aus Kriterium 12: Pfad, URL, api_key, Codeblock, mehrzeilig, 500 Zeichen,
# Bidi-Override, Nullbreite, Steuerzeichen, Zuweisung mit password.
# Jeder einzeln, jeder mit erwartetem Grund. Bei den drei Steuerzeichen-
# Faellen ist auch eine Freigabe mit ENTFERNTEN Zeichen regelkonform --
# geprueft wird dann, dass der freigegebene Text die Zeichen weder enthaelt
# noch escapt.
angriff_hub() {  # $1 = Name, $2 = Text, $3 = erwarteter Grund (oder "bereinigt")
  local name="$1" text="$2" erwarte="$3" antwort ok grund ftext
  antwort="$(freigabe reaktion "$text")"
  ok="$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")"
  grund="$(jq -r '.grund // empty' <<<"$antwort")"
  if [[ "$erwarte" == "bereinigt" ]]; then
    if [[ "$ok" == "false" ]]; then
      chk "Hub: Angriff '$name' wird nicht freigegeben (oder bereinigt)" ja ja
    else
      ftext="$(jq -r '.text // empty' <<<"$antwort")"
      chk "Hub: Angriff '$name' kommt nur BEREINIGT durch" \
        "$("$PY" -B - "$ftext" "$text" <<'PYEOF'
import sys
frei, roh = sys.argv[1], sys.argv[2]
# Kein Steuerzeichen/Bidi/Nullbreite mehr, kein Escape, und kuerzer als roh.
def sauber(t):
    return all(ord(z) >= 0x20 and not (0x202A <= ord(z) <= 0x202E)
               and not (0x200B <= ord(z) <= 0x200F) and not (0x2066 <= ord(z) <= 0x2069)
               for z in t)
print("ja" if sauber(frei) and "\\" not in frei and len(frei) < len(roh) else "nein")
PYEOF
)" ja
    fi
  else
    chk "Hub: Angriff '$name' wird abgelehnt" "$ok" false
    chk "Hub: Angriff '$name' heisst '$erwarte'" \
      "$([[ "$grund" == "$erwarte" ]] && echo ja || echo "$grund")" ja
  fi
  # Kriterium 7: die Absage traegt einen sprechbaren Ersatzsatz.
  if [[ "$ok" == "false" ]]; then
    ersatz="$(jq -r '[.[] | strings] | map(select(length > 10)) | .[0] // ""' <<<"$antwort")"
    ERSATZ_FUND="$ersatz"
  fi
}
ERSATZ_FUND=""
angriff_hub "Pfad" "/etc/passwd" pfad
SEK="K7"
chk "K7: die Absage traegt einen Ersatzsatz" \
  "$([[ -n "$ERSATZ_FUND" ]] && echo ja || echo nein)" ja
chk "K7: der Ersatzsatz besteht selbst den Validator (kuratiert, nicht frei)" \
  "$("$PY" -B - "$RT/validator_treiber.py" "$TARGET" "$ERSATZ_FUND" <<'PYEOF'
import importlib, json, sys
sys.path.insert(0, sys.argv[2])
m = importlib.import_module("daimon.hub.sprechtext")
p = getattr(m, "pruefe", None) or getattr(m, "prüfe", None)
r = p(sys.argv[3], kanal="reaktion")
d = r.als_dict() if hasattr(r, "als_dict") else (r if isinstance(r, dict) else {"ok": bool(r)})
print("ja" if d.get("ok") else "nein")
PYEOF
)" ja
SEK="K6"
angriff_hub "URL" "https://boese.example/ab?c=d" url
angriff_hub "api_key-Zuweisung" "api_key=sk-1234567890abcdef" geheimnis
# Der Codeblock ist zugleich mehrzeilig -- beide Gruende sind ehrlich,
# deshalb direkt statt ueber angriff_hub:
antwort="$(freigabe reaktion '```python
print("x")
```')"
chk "Hub: Angriff 'Codeblock' wird abgelehnt" "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" false
chk "Hub: 'Codeblock' heisst code oder mehrzeilig" \
  "$([[ "$(jq -r '.grund // empty' <<<"$antwort")" =~ ^(code|mehrzeilig)$ ]] && echo ja || echo "$(jq -r '.grund // empty' <<<"$antwort")")" ja
angriff_hub "mehrzeilig" "Zeile eins
Zeile zwei" mehrzeilig
angriff_hub "500 Zeichen" "$(printf 'Das ist ein Satz. %.0s' $(seq 1 30))" zu_lang
angriff_hub "Bidi-Override" $'Hal\u202elo\u202c' bereinigt
angriff_hub "Nullbreite" $'Unsicht\u200bbar' bereinigt
angriff_hub "Steuerzeichen" $'Klingel\a ton' bereinigt
angriff_hub "password-Zuweisung" "password = hunter2" geheimnis

# --- K5: ungefragt zieht nur aus kuratierten Vorlagen ------------------------
echo
SEK="K5"
echo "--- K5 (live): ungefragt nur aus kuratierten Vorlagen ---"
antwort="$(freigabe ungefragt "Der Kaffee ist fertig.")"
chk "ungefragt: freier Text wird abgelehnt -- auch ein harmloser" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" false
antwort="$(freigabe ungefragt "api_key=sk-123")"
chk "ungefragt: ein Angriffstext wird abgelehnt" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" false
# Unbekannte Vorlage: abgelehnt.
antwort="$(vorlage_anfrage "${VORLAGE_FELD:-vorlage}" "gibt-es-nicht-42" trusted)"
chk "ungefragt: eine unbekannte Vorlage wird abgelehnt" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" false
# POSITIVKONTROLLE: die kuratierte Vorlage geht durch (Feld ist oben entdeckt).
chk "POSITIVKONTROLLE: die Vorlagen-Feldform ist auffindbar" \
  "$([[ -n "$VORLAGE_FELD" ]] && echo ja || echo nein)" ja
if [[ -n "$VORLAGE_FELD" ]]; then
  antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
  chk "POSITIVKONTROLLE: die kuratierte Vorlage wird freigegeben" \
    "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" true
  # tainted-Werte: Absage. Gegenprobe direkt daneben war die trusted-Zeile.
  antwort_t="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" tainted)"
  chk "ungefragt: tainted-Werte in der Vorlage sind eine Absage" \
    "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort_t")" false
fi

# =============================================================================
# Der Dienst, live, AUS DEM GEPRUEFTEN BAUM, mit pw-cat-Stub im PATH
# =============================================================================
echo
SEK="K2"
echo "--- Dienst-Start (Unit-ExecStart, socket-aktiviert, Stub im PATH) ---"

# Die Stimmverzeichnisse fuer die Lizenz- und Stimmpruefung: pavoque ist
# exakt dieselbe Dateiliste wie thorsten (Symlinks), nur die MODEL_CARD
# nennt CC-BY-NC-SA. Wird pavoque verweigert, kann die KARTE die einzige
# Ursache sein -- das ist der kontrollierte Versuch fuer Kriterium 2.
STIMMEN="$RT/stimmen"; mkdir -p "$STIMMEN"
ln -sfn "$MODELL_DIR" "$STIMMEN/vits-piper-de_DE-thorsten-high"
mkdir -p "$STIMMEN/vits-piper-de_DE-pavoque-high"
for f in "$MODELL_DIR"/*; do
  [[ "$(basename "$f")" == MODEL_CARD ]] && continue
  ln -sfn "$f" "$STIMMEN/vits-piper-de_DE-pavoque-high/$(basename "$f")"
done
cat >"$STIMMEN/vits-piper-de_DE-pavoque-high/MODEL_CARD" <<'EOF'
# Model card for pavoque (high)
* Language: de_DE (German, Germany)
* License: CC-BY-NC-SA
EOF

# ExecStart aus der Unit lesen: kommentarbereinigt (die Units erklaeren im
# Fliesstext, was sie NICHT tun -- eine Textsuche ueber das Ganze prueft
# Erwaehnung statt Direktive, tests/test_gpu_worker.py:389), mit aufgeloesten
# Fortsetzungszeilen und %t-Substitution.
exec_line="$(sed 's/^[[:space:]]*#.*//' "$TARGET/config/systemd/daimon-tts.service" \
  | awk '/^ExecStart=/{buf=$0; while (buf ~ /\\[[:space:]]*$/) {sub(/\\[[:space:]]*$/,"",buf); if ((getline line) <= 0) break; buf=buf line} print buf; exit}')"
exec_line="${exec_line#ExecStart=}"
exec_line="${exec_line//'%t'/"$RT/xdg"}"
read -ra ARGV <<<"$exec_line"
echo "  ExecStart (substituiert): ${ARGV[*]:-(leer)}"
chk "die Unit traegt einen ExecStart" "$([[ ${#ARGV[@]} -gt 0 ]] && echo ja || echo nein)" ja

ACT_PID=""; DIENST_PID=""; DIENST_SOCK=""
dienst_start() {  # $1 = stimme, $2 = stimm-basis-verzeichnis, $3 = logname
  local stimme="$1" basis="$2" logname="$3"
  local conf="$RT/conf-$logname"
  mkdir -p "$conf/daimon" "$RT/xdg/daimon" "$RT/xdg-state"
  cat >"$conf/daimon/daimon.toml" <<EOF
[persona]
voice = "$stimme"
[tts]
modell_dir = "$basis"
EOF
  # Der Hub-Socket: die Unit mag --hub-socket %t/... tragen; der Pruef-Hub
  # horcht woanders. Der gefundene Endpunkt wird durchgereicht, und zusaetzlich
  # als Symlink unter dem Konfigurationspfad erreichbar gemacht.
  ln -sfn "$TTS_SOCK" "$RT/xdg/daimon/$(basename "$TTS_SOCK")"
  local argv=("${ARGV[@]}")
  local i
  for i in "${!argv[@]}"; do
    if [[ "${argv[$i]}" == "--hub-socket" && $((i + 1)) -lt ${#argv[@]} ]]; then
      argv[$((i + 1))]="$TTS_SOCK"
    fi
  done
  # systemd-socket-activate RAEUMT die Umgebung des Kindes (gemessen: nur
  # LISTEN_* kommt an) und startet es erst bei der ERSTEN VERBINDUNG.
  # Deshalb geht die Umgebung ueber einen env-Wrapper mit, und die
  # PID-Suche laeuft nach dem ersten Verbindungsversuch noch einmal.
  # setsid gibt dem Ganzen eine eigene Prozessgruppe (PGID == ACT_PID):
  # der Dienst ist ein ENKELKIND des Skripts, und ein Kill der Aktivator-PID
  # allein laesst ihn als Waise zurueck. Die Gruppe trifft Aktivator,
  # env-Wrapper und Enkelkind gemeinsam.
  ( cd "$RT" && \
  exec setsid systemd-socket-activate -l "$RT/xdg/daimon/tts-serve.sock" \
      env PATH="$STUBBIN:$PATH" PYTHONPATH="$TARGET" \
      XDG_RUNTIME_DIR="$RT/xdg" XDG_STATE_HOME="$RT/xdg-state" \
      XDG_CONFIG_HOME="$conf" DAIMON_PWCAT_LOG="$STUBLOG" \
      PYTHONDONTWRITEBYTECODE=1 \
      "${argv[@]}" \
      >"$RT/dienst-$logname.log" 2>&1 ) &
  ACT_PID=$!
  DIENSTPIDS+=("$ACT_PID")
  GRUPPEN+=("$ACT_PID")
  DIENST_PID=""
  local n
  for n in $(seq 1 100); do
    DIENST_PID="$(pgrep -P "$ACT_PID" | head -1)"
    [[ -n "$DIENST_PID" ]] && break
    kill -0 "$ACT_PID" 2>/dev/null || break
    sleep 0.1
  done
  # Das Kind entsteht erst bei der ersten Verbindung: die Socket-Sonde
  # unten loest es aus; danach wird die PID noch einmal gesucht.
  # Der Dienst-Socket: der von activate, oder ein eigener (falls die Unit
  # --socket traegt). Gesucht wird, was auf eine status-Anfrage antwortet.
  DIENST_SOCK=""
  for n in $(seq 1 150); do
    for s in "$RT/xdg/daimon"/*.sock; do
      [[ -S "$s" ]] || continue
      [[ "$s" -ef "$TTS_SOCK" ]] && continue
      jschreib "$RT/stat.json" v=1 art=status
      antwort="$(anfrage "$s" "$RT/stat.json" 2)"
      if jq -e . >/dev/null 2>&1 <<<"$antwort"; then DIENST_SOCK="$s"; break; fi
    done
    [[ -n "$DIENST_SOCK" ]] && break
    kill -0 "$ACT_PID" 2>/dev/null || break
    sleep 0.2
  done
  # Die Sonde hat die erste Verbindung ausgeloest -- jetzt gibt es das Kind.
  for n in $(seq 1 50); do
    DIENST_PID="$(pgrep -P "$ACT_PID" | head -1)"
    [[ -n "$DIENST_PID" ]] && break
    sleep 0.1
  done
  # exec-te activate den Dienst direkt, ist die activate-PID der Dienst.
  [[ -n "$DIENST_PID" ]] || DIENST_PID="$ACT_PID"
  # Guertel und Hosentraeger: die Gruppe (GRUPPEN) ist der Hauptweg, die
  # bekannte Dienst-PID der Rueckfall, falls sie je aus der Gruppe faellt.
  [[ "$DIENST_PID" != "$ACT_PID" ]] && DIENSTPIDS+=("$DIENST_PID")
}
dienst_stop() {
  # Erst die ganze Prozessgruppe (Aktivator + env + Enkelkind), dann die
  # bekannten Einzel-PIDs als Rueckfall -- siehe aufraeumen().
  [[ -n "$ACT_PID" ]] && kill -- -"$ACT_PID" 2>/dev/null
  [[ -n "$ACT_PID" ]] && kill "$ACT_PID" 2>/dev/null
  [[ -n "$DIENST_PID" && "$DIENST_PID" != "$ACT_PID" ]] && kill "$DIENST_PID" 2>/dev/null
  [[ -n "$ACT_PID" ]] && wait "$ACT_PID" 2>/dev/null
  # Kurze Frist, dann eskaliert die Gruppe auf SIGKILL: ein Dienst, der das
  # TERM ignoriert, darf den naechsten dienst_start nicht behindern.
  local n
  for n in $(seq 1 20); do
    [[ -z "$DIENST_PID" ]] && break
    kill -0 "$DIENST_PID" 2>/dev/null || break
    if [[ "$n" -eq 20 ]]; then
      [[ -n "$ACT_PID" ]] && kill -9 -- -"$ACT_PID" 2>/dev/null
      [[ -n "$DIENST_PID" ]] && kill -9 "$DIENST_PID" 2>/dev/null
    fi
    sleep 0.1
  done
  ACT_PID=""; DIENST_PID=""; DIENST_SOCK=""
}

# Ein Sprachauftrag an den Dienst. $1 = JSON-Datei. Gibt die Antwort aus.
dienst_anfrage() { anfrage "$DIENST_SOCK" "$1" 30; }

# Volle Kette: Hub-Freigabe, dann der Dienst. $1 = text, $2 = kanal.
# Setzt SPR_OK (ja/nein), SPR_ANTWORT, SPR_MARKE.
DIENST_ART="sprich"
sprich() {
  local text="$1" kanal="${2:-reaktion}" f m
  SPR_OK=nein; SPR_MARKE=""; SPR_ANTWORT=""
  f="$(freigabe "$kanal" "$text")"
  if [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$f" 2>/dev/null)" != "true" ]]; then
    SPR_ANTWORT="$f"; return 1
  fi
  m="$(jq -r --arg f "$MARKE_FELD" '.[$f] // empty' <<<"$f")"
  SPR_MARKE="$m"
  local ftext
  ftext="$(jq -r '.text // empty' <<<"$f")"
  [[ -n "$ftext" ]] || ftext="$text"
  "$PY" -B - "$RT/spr.json" "$DIENST_ART" "$ftext" "$kanal" "$m" "$MARKE_FELD" <<'PYEOF'
import json, sys
ziel, art, text, kanal, marke, feld = sys.argv[1:7]
d = {"v": 1, "art": art, "text": text, "kanal": kanal, feld: marke}
if feld != "marke":
    d["marke"] = marke
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
PYEOF
  SPR_ANTWORT="$(dienst_anfrage "$RT/spr.json")"
  [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$SPR_ANTWORT" 2>/dev/null)" == "true" ]] && SPR_OK=ja
}

# ---------------------------------------------------------------------------
# Vorentdeckung: die Schreibweise der Dienst-Anfrage ist der Implementierung
# ueberlassen. Die Kandidaten werden der Reihe nach mit voller Kette
# probiert; die Messung ist dieselbe -- was am Stub ankommt. Die erste
# funktionierende Form ist zugleich der Positiv-Kanarienvogel (K12).
# ---------------------------------------------------------------------------
echo
SEK="K12"
echo "--- Vorentdeckung: Dienst-Art, und der Positiv-Kanarienvogel ---"
dienst_start de_DE-thorsten-high "$STIMMEN" entdeckung
chk "Dienst startet aus dem geprueften Baum (Socket antwortet)" \
  "$([[ -n "$DIENST_SOCK" ]] && echo ja || { tail -10 "$RT/dienst-entdeckung.log"; echo nein; })" ja
if [[ -z "$DIENST_SOCK" ]]; then
  echo "  Dienst-Protokoll:"; tail -20 "$RT/dienst-entdeckung.log"
  echo; echo "T-3.9: FEHLGESCHLAGEN — ohne Dienst ist der Live-Teil ungemessen."
  exit 1
fi
chk "POSITIVKONTROLLE: der Dienst-Prozess hat den geprueften Baum im PYTHONPATH" \
  "$(tr '\0' '\n' <"/proc/$DIENST_PID/environ" 2>/dev/null | grep -q "^PYTHONPATH=$TARGET\$" && echo ja || echo nein)" ja

GEFUNDENE_ART=""
starts_vorher="$(stub_zahl START)"
for art in sprich sprechen sag tts spiele freigabe vorlesen; do
  DIENST_ART="$art"
  sprich "Der Kanarienvogel singt leise." reaktion
  [[ "$SPR_OK" == ja ]] || continue
  stub_warte "^FIRST" 10 || true
  if [[ "$(stub_zahl START)" -gt "$starts_vorher" ]]; then
    GEFUNDENE_ART="$art"
    break
  fi
done
echo "  Dienst-Art: ${GEFUNDENE_ART:-(keine gefunden)}"
chk "eine Sprech-Anfrageform ist auffindbar (POSITIVKONTROLLE)" \
  "$([[ -n "$GEFUNDENE_ART" ]] && echo ja || echo nein)" ja
if [[ -z "$GEFUNDENE_ART" ]]; then
  echo "  Keine Anfrageform kam am Stub an. Letzte Antwort: $SPR_ANTWORT"
  echo; echo "T-3.9: FEHLGESCHLAGEN — der Dienst spricht nicht messbar."
  exit 1
fi
KANARI_ANTWORT="$SPR_ANTWORT"
stub_warte "^EXIT" 15 || true
chk "Kanarienvogel: pw-cat wurde ueber den PATH gerufen (der Stub feuert)" \
  "$([[ "$(stub_zahl START)" -gt "$starts_vorher" ]] && echo ja || echo nein)" ja
chk "Kanarienvogel: es flossen Audiodaten (> 2000 Bytes)" \
  "$([[ "$(stub_bytes)" -gt 2000 ]] && echo ja || echo nein)" ja

# K1: engine, modell und provider in der Antwort des Dienstes (Kriterium 1).
SEK="K1"
echo "  Antwort beim Sprechen: $KANARI_ANTWORT"
chk "die Antwort nennt 'engine'" \
  "$(jq -e 'has("engine")' <<<"$KANARI_ANTWORT" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "engine ist sherpa (nicht piper1-gpl)" \
  "$(jq -r '.engine // ""' <<<"$KANARI_ANTWORT" | grep -qi sherpa && echo ja || echo nein)" ja
chk "die Antwort nennt 'modell'" \
  "$(jq -e 'has("modell")' <<<"$KANARI_ANTWORT" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "modell ist thorsten" \
  "$(jq -r '.modell // ""' <<<"$KANARI_ANTWORT" | grep -qi thorsten && echo ja || echo nein)" ja
chk "die Antwort nennt 'provider'" \
  "$(jq -e 'has("provider")' <<<"$KANARI_ANTWORT" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "provider ist cpu" \
  "$(jq -r 'if has("provider") then (.provider|tostring) else "FEHLT" end' <<<"$KANARI_ANTWORT")" cpu
dienst_stop

# ---------------------------------------------------------------------------
# K2: die Stimmlizenz wird je Stimme geprueft
# ---------------------------------------------------------------------------
SEK="K2"
dienst_start de_DE-pavoque-high "$STIMMEN" pavoque
chk "Dienst startet auch mit verbotener Stimme (ehrliche Absage, kein stiller Tod)" \
  "$([[ -n "$DIENST_SOCK" ]] && echo ja || { tail -5 "$RT/dienst-pavoque.log"; echo nein; })" ja
if [[ -n "$DIENST_SOCK" ]]; then
  start_vorher="$(stub_zahl START)"
  sprich "Dieser Satz darf nicht erklingen." rueckfrage
  chk "pavoque (CC-BY-NC-SA): die Anfrage wird abgelehnt, obwohl die Dateien da sind" \
    "$SPR_OK" nein
  # Der Grundname ist der Implementierung ueberlassen (Gegenlesen Runde 2:
  # "stimme_unerlaubt" statt "lizenz_*"). Gefordert ist nur, dass die Absage
  # die LIZENZ als Grund erkennbar macht -- in grund ODER meldung -- und sich
  # von "Datei fehlt" unterscheidet.
  chk "pavoque: die Absage macht die LIZENZ zum Grund (grund oder meldung)" \
    "$(jq -r '[(.grund // ""), (.meldung // "")] | join(" ")' <<<"$SPR_ANTWORT" \
       | grep -qiE 'lizenz|unerlaubt|verboten' && echo ja || echo "$(jq -r '.grund // empty' <<<"$SPR_ANTWORT")")" ja
  chk "pavoque: pw-cat wurde NICHT gerufen" "$(stub_zahl START)" "$start_vorher"
fi
dienst_stop

# ---------------------------------------------------------------------------
# K3: die Stimme kommt aus persona.voice -- gelesen, nicht fest verdrahtet
# ---------------------------------------------------------------------------
SEK="K3"
dienst_start de_DE-kerstin-high "$STIMMEN" kerstin
chk "Dienst startet mit fehlender Stimme (kerstin liegt nicht vor)" \
  "$([[ -n "$DIENST_SOCK" ]] && echo ja || { tail -5 "$RT/dienst-kerstin.log"; echo nein; })" ja
if [[ -n "$DIENST_SOCK" ]]; then
  start_vorher="$(stub_zahl START)"
  sprich "Auch dieser Satz darf nicht erklingen." rueckfrage
  chk "kerstin (nicht installiert): ehrliche Absage statt stiller Vorgabe" \
    "$SPR_OK" nein
  chk "kerstin: pw-cat wurde NICHT gerufen" "$(stub_zahl START)" "$start_vorher"
fi
dienst_stop

echo
SEK="K1"
echo "--- Hauptdienst: thorsten, fuer den Rest des Laufs ---"
dienst_start de_DE-thorsten-high "$STIMMEN" main
chk "Hauptdienst startet aus dem geprueften Baum" \
  "$([[ -n "$DIENST_SOCK" ]] && echo ja || { tail -10 "$RT/dienst-main.log"; echo nein; })" ja
if [[ -z "$DIENST_SOCK" ]]; then
  echo "  Dienst-Protokoll:"; tail -20 "$RT/dienst-main.log"
  echo; echo "T-3.9: FEHLGESCHLAGEN — ohne Dienst ist der Live-Teil ungemessen."
  exit 1
fi
chk "POSITIVKONTROLLE: der Hauptdienst hat den geprueften Baum im PYTHONPATH" \
  "$(tr '\0' '\n' <"/proc/$DIENST_PID/environ" 2>/dev/null | grep -q "^PYTHONPATH=$TARGET\$" && echo ja || echo nein)" ja

# ---------------------------------------------------------------------------
# K6, zweite Haelfte: Direktzugriff. Erst die Lage klaeren: verlangt der
# Dienst eine Marke aus dem Hub (der Aufrufer hat sie geholt), oder holt
# der Dienst die Freigabe selbst? BEIDE Bauarten erfuellen Kriterium 6 --
# der Validator sitzt im Hub und jeder gesprochene Text ist durch ihn
# gelaufen. Sie unterscheiden sich nur in der Antwort auf "harmlos ohne
# Marke": abgelehnt (Marke-Pflicht) oder gesprochen (Dienst holt selbst).
# Ein Dienst, der UNGEPRUEFT spricht, faellt an den zehn Angriffen unten.
# ---------------------------------------------------------------------------
echo
SEK="K6"
echo "--- K6 (live): Direktzugriff -- Lage, dann die zehn Angriffe ---"
start_vorher="$(stub_zahl START)"
"$PY" -B - "$RT/dir.json" "$DIENST_ART" <<'PYEOF'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"v": 1, "art": sys.argv[2], "text": "Der Kaffee ist fertig.",
               "kanal": "rueckfrage"}, fh, ensure_ascii=False)
PYEOF
antwort="$(dienst_anfrage "$RT/dir.json")"
# Auf eine Ausgabe nur warten, wenn es ueberhaupt eine geben koennte.
[[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)" == "true" ]] \
  && { stub_warte "^EXIT" 15 || true; }
DIENST_MARKE_PFLICHT=""
if [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)" != "true" \
     && "$(stub_zahl START)" -eq "$start_vorher" ]]; then
  DIENST_MARKE_PFLICHT=ja
elif [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)" == "true" \
     && "$(stub_zahl START)" -gt "$start_vorher" ]]; then
  DIENST_MARKE_PFLICHT=nein
fi
echo "  Lage: DIENST_MARKE_PFLICHT=${DIENST_MARKE_PFLICHT:-uneindeutig} (Antwort: $antwort)"
chk "Direktzugriff ohne Marke: eindeutige Lage (abgelehnt ODER selbst validiert gesprochen)" \
  "$([[ -n "$DIENST_MARKE_PFLICHT" ]] && echo ja || echo nein)" ja

# Der Dienst darf auf einen verbotenen Angriff die ERSATZREDE sprechen (K7:
# "Die Antwort steht auf dem Bildschirm."). Das ist kein Vorlesen des
# Angriffs. Bei den drei zu entfernenden Unicode-Klassen muss dagegen der
# exakte bereinigte Text in der Dienstantwort stehen und Audio fliessen.
# Bytes werden pro pw-cat-Prozess fortlaufend gemessen; eine Abschlusszeile
# waere wegen der absichtlichen SIGKILL-Unterbrechungen kein Messpunkt.
ANGRIFF_DELTAS=()
ANGRIFF_PIDS=()
ANGRIFF_MODI=()
ANGRIFF_ERSATZ=()
ANGRIFF_ERSATZ_GESPROCHEN=()
direkt_ohne_marke() {  # $1 = Name, $2 = Text, $3 = abgelehnt|bereinigt, $4 = bereinigter Text
  local vorher_s pid bytes ok antwort_text
  vorher_s="$(stub_zahl START)"
  "$PY" -B - "$RT/dir.json" "$DIENST_ART" "$2" <<'PYEOF'
import json, sys
ziel, art, text = sys.argv[1:4]
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump({"v": 1, "art": art, "text": text, "kanal": "reaktion"}, fh,
              ensure_ascii=False)
PYEOF
  antwort="$(dienst_anfrage "$RT/dir.json")"
  # Kam eine Ausgabe, ihre fortlaufend protokollierten Bytes abwarten. Auf
  # EXIT duerfen wir NICHT angewiesen sein: die naechste Aeusserung beendet
  # diesen Prozess absichtlich per SIGKILL.
  pid=""
  if [[ "$(stub_zahl START)" -gt "$vorher_s" ]]; then
    pid="$(stub_letzte_pid)"
    stub_warte "^BYTES $pid " 10 || true
  else
    sleep 0.3
  fi
  bytes="$([[ -n "$pid" ]] && stub_pid_bytes "$pid" || echo 0)"
  ANGRIFF_DELTAS+=("$bytes")
  ANGRIFF_PIDS+=("$pid")
  ANGRIFF_MODI+=("$3")
  ANGRIFF_ERSATZ+=("$(jq -r '.ersatz // empty' <<<"$antwort" 2>/dev/null)")
  ANGRIFF_ERSATZ_GESPROCHEN+=("$(jq -r 'if has("ersatz_gesprochen") then (.ersatz_gesprochen|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)")
  ok="$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)"
  if [[ "$3" == bereinigt && "$DIENST_MARKE_PFLICHT" == nein ]]; then
    antwort_text="$(jq -r '.text // empty' <<<"$antwort" 2>/dev/null)"
    chk "Direktzugriff '$1': der bereinigte Rest ist sprechbar" "$ok" true
    chk "Direktzugriff '$1': nur der exakt bereinigte Text geht an die Ausgabe" \
      "$antwort_text" "$4"
    chk "Direktzugriff '$1': der bereinigte Text erreicht pw-cat" \
      "$([[ "$bytes" -gt 2000 ]] && echo ja || echo nein)" ja
  else
    chk "Direktzugriff '$1': der Angriffstext wird abgelehnt (ok != true)" \
      "$ok" false
  fi
}
direkt_ohne_marke "Pfad" "/etc/passwd" abgelehnt
direkt_ohne_marke "URL" "https://boese.example/ab?c=d" abgelehnt
direkt_ohne_marke "api_key" "api_key=sk-1234567890abcdef" abgelehnt
direkt_ohne_marke "Codeblock" '```python
print("x")
```' abgelehnt
direkt_ohne_marke "mehrzeilig" "Zeile eins
Zeile zwei" abgelehnt
direkt_ohne_marke "500 Zeichen" "$(printf 'Das ist ein Satz. %.0s' $(seq 1 30))" abgelehnt
direkt_ohne_marke "Bidi" $'Hal\u202elo\u202c' bereinigt "Hallo"
direkt_ohne_marke "Nullbreite" $'Unsicht\u200bbar' bereinigt "Unsichtbar"
direkt_ohne_marke "Steuerzeichen" $'Klingel\a ton' bereinigt "Klingel ton"
direkt_ohne_marke "password" "password = hunter2" abgelehnt
echo "  Byte-Deltas der zehn Angriffe: ${ANGRIFF_DELTAS[*]}"
# Die Ersatzrede-Referenz: eine absichtliche Regelverletzung auf einem
# Kanal, der gerade frei ist. Marke-Pflicht-Bauart: keine Ausgabe, alle
# Deltas muessen 0 sein. Selbst-holende Bauart: die Ersatzrede muss den
# Stub messbar erreichen; unterbrochene Einzelprozesse werden oben jeweils
# fortlaufend statt erst an ihrem nicht garantierten Ende gezaehlt.
ERSATZ_BYTES=0
if [[ "$DIENST_MARKE_PFLICHT" == nein ]]; then
  vorher_s="$(stub_zahl START)"
  "$PY" -B - "$RT/dir.json" "$DIENST_ART" <<'PYEOF'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"v": 1, "art": sys.argv[2], "text": "/etc/passwd",
               "kanal": "rueckfrage"}, fh, ensure_ascii=False)
PYEOF
  ersatz_antwort="$(dienst_anfrage "$RT/dir.json")"
  ERSATZ_PID=""
  if [[ "$(stub_zahl START)" -gt "$vorher_s" ]]; then
    ERSATZ_PID="$(stub_letzte_pid)"
    stub_warte "^BYTES $ERSATZ_PID " 10 || true
  fi
  ERSATZ_BYTES="$([[ -n "$ERSATZ_PID" ]] && stub_pid_bytes "$ERSATZ_PID" || echo 0)"
  echo "  Ersatzrede-Referenz: $ERSATZ_BYTES Bytes (Antwort: $ersatz_antwort)"
  chk "die Ersatzrede-Referenz ist messbar (POSITIVKONTROLLE)" \
    "$([[ "$ERSATZ_BYTES" -gt 2000 ]] && echo ja || echo nein)" ja
fi
if [[ "$DIENST_MARKE_PFLICHT" == ja ]]; then
  auswertung_direkt="$([[ "${ANGRIFF_DELTAS[*]}" =~ ^(0[[:space:]]*)+$ ]] && echo ja || echo nein)"
  chk "bei Marke-Pflicht erreicht kein Direktzugriff die Ausgabe" "$auswertung_direkt" ja
else
  # Die sieben wirklich verbotenen Texte duerfen nur den kuratierten
  # Ersatzsatz erzeugen. Ihre Prozesse werden von der Folgeanfrage per
  # SIGKILL beendet, daher ist ihre Endlaenge kein Inhaltsfingerabdruck.
  # Belegt werden stattdessen gemeinsam: Absage oben, derselbe kuratierte
  # Ersatztext, bestaetigter Ersatzpfad und tatsaechlich empfangene Bytes.
  for i in "${!ANGRIFF_PIDS[@]}"; do
    [[ "${ANGRIFF_MODI[$i]}" == abgelehnt ]] || continue
    chk "Direktzugriff: Angriff $((i + 1)) nennt nur den kuratierten Ersatzsatz" \
      "${ANGRIFF_ERSATZ[$i]}" "$ERSATZ_FUND"
    chk "Direktzugriff: Ersatzrede zu Angriff $((i + 1)) erreicht pw-cat" \
      "$([[ "${ANGRIFF_DELTAS[$i]}" -gt 2000 && "${ANGRIFF_ERSATZ_GESPROCHEN[$i]}" == true ]] && echo ja || echo nein)" ja
  done
fi
# Und mit einer erfundenen Marke -- nur wo eine Marke Teil des Protokolls
# ist, muss eine falsche auch scheitern. Holt der Dienst die Freigabe
# selbst, ist die Marke kein Eingang, und die Schranke sind die zehn
# Angriffe oben.
if [[ "$DIENST_MARKE_PFLICHT" == ja ]]; then
  vorher="$(stub_zahl START)"
  "$PY" -B - "$RT/dir.json" "$DIENST_ART" <<'PYEOF'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"v": 1, "art": sys.argv[2], "text": "Der Kaffee ist fertig.",
               "kanal": "reaktion", "marke": "erfunden-nicht-vom-hub"}, fh)
PYEOF
  antwort="$(dienst_anfrage "$RT/dir.json")"
  sleep 0.3
  chk "Direktzugriff mit ERFUNDENER Marke: abgelehnt, nichts gesprochen" \
    "$([[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort" 2>/dev/null)" != "true" \
       && "$(stub_zahl START)" -eq "$vorher" ]] && echo ja || echo nein)" ja
else
  echo "  (erfundene Marke nicht pruefbar: der Dienst holt die Freigabe selbst --"
  echo "   die Schranke sind die zehn Angriffe oben, nicht das Marke-Feld)"
fi

# Falls der Stub trotz gefundener Anfrageform still blieb: der Pruefling
# ruft pw-cat am PATH vorbei. Das ist ein BEFUND, kein Ende der Messung --
# der execve-Mitschnitt am laufenden Prozess zeigt, wen er wirklich ruft.
if [[ "$(stub_zahl START)" -eq 0 ]]; then
  laut "Der Dienst meldet ok, aber der Stub blieb still -- pw-cat am PATH vorbei?"
  if command -v strace >/dev/null; then
    timeout 8 strace -f -e trace=execve -o "$RT/strace.log" -p "$DIENST_PID" >/dev/null 2>&1 &
    STRACE_PID=$!
    sleep 0.5
    sprich "Kanarienvogel unter strace." rueckfrage
    sleep 1
    kill "$STRACE_PID" 2>/dev/null; wait "$STRACE_PID" 2>/dev/null
    grep -E 'execve\([^)]*pw-cat' "$RT/strace.log" | head -3
  fi
fi

# =============================================================================
# K8: Abkuehlung je Anlass -- 20/10/3 s, persistiert, monotone Zeit
# =============================================================================
echo
SEK="K8"
echo "--- K8: Abkuehlung (gewaeitert und nachgemessen, nicht rest_s abgelesen) ---"

# sprich_vorlage: volle Kette ueber eine kuratierte Vorlage (ungefragt).
sprich_vorlage() {  # $1 = vorlage-name
  SPR_OK=nein; SPR_ANTWORT=""
  local f m ftext
  f="$(vorlage_anfrage "$VORLAGE_FELD" "$1" trusted)"
  if [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$f" 2>/dev/null)" != "true" ]]; then
    SPR_ANTWORT="$f"; return 1
  fi
  m="$(jq -r --arg f "$MARKE_FELD" '.[$f] // empty' <<<"$f")"
  ftext="$(jq -r '.text // empty' <<<"$f")"
  # Text UND Vorlagenform mitschicken: ein Dienst, der die Freigabe selbst
  # holt, braucht die Vorlagenform; ein Dienst mit Marke braucht den Text.
  "$PY" -B - "$RT/spr.json" "$DIENST_ART" "$ftext" ungefragt "$m" "$MARKE_FELD" "$1" <<'PYEOF'
import json, sys
ziel, art, text, kanal, marke, feld, vorlage = sys.argv[1:8]
d = {"v": 1, "art": art, "text": text, "kanal": kanal, feld: marke,
     "vorlage": vorlage, "anlass": vorlage,
     "werte": {"projekt": "Pruefprojekt"}, "markierung": "trusted"}
if feld != "marke":
    d["marke"] = marke
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
PYEOF
  SPR_ANTWORT="$(dienst_anfrage "$RT/spr.json")"
  [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$SPR_ANTWORT" 2>/dev/null)" == "true" ]] && SPR_OK=ja
}

# Warten, bis die Wiedergabe zuende UND die gesprochen-Meldung beim Hub ist.
warte_gesprochen() {
  local exits_vorher="$1"
  # Auf einen NEUEN EXIT warten. `stub_warte "^EXIT"` fand ab dem zweiten
  # Aufruf sofort irgendeinen alten Eintrag und wartete damit gar nicht auf
  # die gerade gestartete Wiedergabe.
  local n
  for n in $(seq 1 200); do
    [[ "$(stub_zahl EXIT)" -gt "$exits_vorher" ]] && break
    sleep 0.1
  done
  # Der EXIT-Eintrag selbst reicht nicht: die Meldung an den Hub braucht
  # einen eigenen Rundlauf. Gemessen wird danach an der Abkuehlung selbst.
  sleep 0.5
}

ist_abkuehlung() { jq -r '(.ok == false) and ((.grund // "") | test("abkuehl|kuehl|cooldown"; "i"))' <<<"$1"; }

# Wartet eine eventuell laufende Abkuehlung ab, BEVOR ein Abschnitt einen
# Kanal benutzt. Ohne das verschmutzen die Abschnitte einander: die
# Lage-Probe (Direktzugriff) spricht auf `rueckfrage`, und die 3-s-Frist
# lief in den K8-Abschnitt hinein (Gegenlesen Runde 2, Befund des Builders).
# Die Sonden-Freigabe selbst vermerkt nichts -- vermerkt wird bei `beginnt`
# bzw. `gesprochen`, nicht bei der Freigabe.
warte_abkuehlung_frei() {  # $1 = kanal
  local i a grund rest
  for i in $(seq 1 40); do
    if [[ "$1" == ungefragt ]]; then
      a="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
    else
      a="$(freigabe "$1" "Abkuehlungssonde.")"
    fi
    if [[ "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$a" 2>/dev/null)" == "true" ]]; then
      return 0
    fi
    grund="$(jq -r '.grund // "?"' <<<"$a" 2>/dev/null)"
    rest="$(jq -r '.rest_s // "?"' <<<"$a" 2>/dev/null)"
    echo "  warte auf freien Kanal $1 (grund=$grund rest_s=$rest)"
    [[ "$(ist_abkuehlung "$a")" == "true" ]] || return 1
    sleep "$("$PY" -B -c "import sys; r = float(sys.argv[1]); print(min(r + 0.3, 25) if 0 < r else 1.0)" "${rest/?/1}" 2>/dev/null || echo 1.0)"
  done
  return 1
}

# (1) rueckfrage: 3 s. Erst den Kanal abwarten -- die Lage-Probe in K6
# spricht auf rueckfrage, und die 3-s-Frist darf nicht hierhin verschmutzen.
warte_abkuehlung_frei rueckfrage
chk "rueckfrage: der Kanal ist vor dem Abschnitt frei (keine Verschmutzung)" \
  "$?" 0
exits_vorher="$(stub_zahl EXIT)"
sprich "Notiz eins." rueckfrage
[[ "$SPR_OK" == ja ]] || echo "  Absage war: $SPR_ANTWORT"
chk "rueckfrage: die erste Aeusserung wird gesprochen (POSITIVKONTROLLE)" "$SPR_OK" ja
warte_gesprochen "$exits_vorher"
T_RUECK="$(date +%s)"
antwort="$(freigabe rueckfrage "Notiz zwei.")"
echo "  Absage: $antwort"
chk "rueckfrage: sofort danach abgelehnt (Abkuehlung)" "$(ist_abkuehlung "$antwort")" true
rest="$(jq -r '.rest_s // empty' <<<"$antwort")"
[[ -n "$rest" ]] && chk "rueckfrage: rest_s liegt bei hoechstens 3 (gemeldet: $rest)" \
  "$(awk -v r="$rest" 'BEGIN{print (r > 0 && r <= 3.5) ? "ja" : "nein"}')" ja

# (2) ungefragt: 20 s, ueber die Vorlage
warte_abkuehlung_frei ungefragt
chk "ungefragt: der Kanal ist vor dem Abschnitt frei (keine Verschmutzung)" \
  "$?" 0
exits_vorher="$(stub_zahl EXIT)"
sprich_vorlage "$VORLAGE_NAME"
[[ "$SPR_OK" == ja ]] || echo "  Absage war: $SPR_ANTWORT"
chk "ungefragt: die Vorlage wird gesprochen (POSITIVKONTROLLE)" "$SPR_OK" ja
warte_gesprochen "$exits_vorher"
T_UNGEFRAGT="$(date +%s)"
antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
chk "ungefragt: sofort danach abgelehnt (Abkuehlung 20 s)" "$(ist_abkuehlung "$antwort")" true
rest="$(jq -r '.rest_s // empty' <<<"$antwort")"
[[ -n "$rest" ]] && chk "ungefragt: rest_s liegt bei ~20 (gemeldet: $rest)" \
  "$(awk -v r="$rest" 'BEGIN{print (r > 15 && r <= 21) ? "ja" : "nein"}')" ja

# (3) PERSISTENZ: Hub-Neustart mitten im Fenster. Ein Verifizierer, der nur
# im Speicher prueft, erkennt den Mutanten `abkuehlung-nicht-persistiert`
# nicht -- genau deshalb startet hier der Hub neu.
hub_stop
hub_start main "$HUBSTATE"
chk "Hub startet neu (selbes Zustandsverzeichnis)" \
  "$([[ -n "$HUB_PID" ]] && kill -0 "$HUB_PID" 2>/dev/null && echo ja || echo nein)" ja
antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
chk "PERSISTENZ: nach dem Hub-Neustart ist ungefragt IMMER NOCH gesperrt" \
  "$(ist_abkuehlung "$antwort")" true

# (4) Die Ablage: eine Datei im Zustandsverzeichnis, gueltiges JSON, nennt
# die Kanaele. Das Muster (mkstemp/fsync/os.replace) selbst ist am
# Verhalten oben gemessen; hier faellt nur auf, wenn GAR NICHTS persistiert.
ablage="$(grep -rl '"ungefragt"\|ungefragt' "$HUBSTATE" 2>/dev/null | head -1)"
chk "eine Abkuehlungs-Ablage existiert im Zustandsverzeichnis" \
  "$([[ -n "$ablage" ]] && echo ja || echo nein)" ja
if [[ -n "$ablage" ]]; then
  chk "die Ablage ist gueltiges JSON" \
    "$(jq -e . >/dev/null 2>&1 <"$ablage" && echo ja || echo nein)" ja
fi

# (5) Die Fristen ENDEN auch -- von der kurzen zur langen, nachgemessen.
# rueckfrage (3 s) wird bis zu ihrer selbst gesetzten Schwelle abgewartet.
# Die fruehere Annahme "ist laengst um" hing nur zufaellig an der Laufzeit
# der Abschnitte dazwischen (HANDOVER-Fall 13: Schwelle nie nachgemessen).
jetzt="$(date +%s)"
wartung=$(( T_RUECK + 4 - jetzt ))
[[ "$wartung" -gt 0 ]] && sleep "$wartung"
antwort="$(freigabe rueckfrage "Notiz drei.")"
chk "rueckfrage: nach > 3 s ist die Abkuehlung beendet (POSITIVKONTROLLE)" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" true
jetzt="$(date +%s)"
wartung=$(( T_UNGEFRAGT + 11 - jetzt ))
[[ "$wartung" -gt 0 ]] && sleep "$wartung"
antwort="$(freigabe reaktion "Reaktionsprobe nach zehn Sekunden.")"
chk "reaktion: nach 10 s ist die Abkuehlung beendet (POSITIVKONTROLLE)" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" true
antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
chk "ungefragt: zu dem Zeitpunkt sperrt die 20-s-Frist NOCH (10 != 20)" \
  "$(ist_abkuehlung "$antwort")" true
jetzt="$(date +%s)"
wartung=$(( T_UNGEFRAGT + 21 - jetzt ))
[[ "$wartung" -gt 0 ]] && sleep "$wartung"
antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
chk "ungefragt: nach 20 s ist die Abkuehlung beendet (POSITIVKONTROLLE)" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" true

# (6) Monotone Zeit: die Wanduhr darf eine Abkuehlung nicht aufheben, die
# monotone Uhr muss sie beenden. Gemessen mit Uhrenversatz VOR dem Import,
# Muster T-3.4 Abschnitt 6. Erst eine frische Sperre setzen.
exits_vorher="$(stub_zahl EXIT)"
sprich_vorlage "$VORLAGE_NAME"
chk "Uhrenprobe: frische ungefragt-Sperre gesetzt (POSITIVKONTROLLE)" "$SPR_OK" ja
warte_gesprochen "$exits_vorher"
# (a) Wanduhr eine Stunde ZURUECK: eine wanduhrbasierte Abkuehlung waere
# jetzt laengst abgelaufen. Sie muss trotzdem sperren. Der Haupt-Hub geht
# dafuer aus -- er kaeme sonst dem Uhren-Hub ins Gehege.
hub_stop
hub_start wand "$HUBSTATE" x DAIMON_UHR_WAND_S=-3600
chk "Hub mit versetzter Wanduhr startet" \
  "$([[ -n "$HUB_PID" ]] && kill -0 "$HUB_PID" 2>/dev/null && echo ja || echo nein)" ja
antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
chk "DIE ZUSAGE: eine um 1 h gestellte WANDUHR hebt die Abkuehlung nicht auf" \
  "$(ist_abkuehlung "$antwort")" true
hub_stop
# (b) monotone Uhr 25 s VOR: die 20-s-Frist muss abgelaufen sein. Das ist
# die Positivkontrolle von (a): ohne sie bewiese (a) nur, dass der Pruefling
# ueberhaupt keine Uhr liest.
hub_start mono "$HUBSTATE" x DAIMON_UHR_MONO_S=+25
chk "Hub mit vorgestellter monotoner Uhr startet" \
  "$([[ -n "$HUB_PID" ]] && kill -0 "$HUB_PID" 2>/dev/null && echo ja || echo nein)" ja
antwort="$(vorlage_anfrage "$VORLAGE_FELD" "$VORLAGE_NAME" trusted)"
chk "POSITIVKONTROLLE: eine um 25 s vorgestellte MONOTONE Uhr beendet die Frist" \
  "$(jq -r 'if has("ok") then (.ok|tostring) else "FEHLT" end' <<<"$antwort")" true
hub_stop
# Zurueck zum normalen Hub -- TTS_SOCK zeigt wieder auf den Haupt-Lauf.
hub_start main "$HUBSTATE"
chk "Hub laeuft nach den Uhrenproben wieder normal" \
  "$([[ -n "$HUB_PID" ]] && kill -0 "$HUB_PID" 2>/dev/null && echo ja || echo nein)" ja

# =============================================================================
# K4: Unterbrechung binnen 100 ms -- gemessen am ENDE des alten pw-cat
# =============================================================================
echo
SEK="K4"
echo "--- K4/K9: Unterbrechung, und voice.tts_active dabei ---"
warte_abkuehlung_frei reaktion
chk "reaktion: der Kanal ist vor dem Unterbrechungs-Abschnitt frei" "$?" 0
A_TEXT="Die Sitzung wartet seit vier Minuten auf eine Freigabe, und der Durchlauf bricht ab, wenn niemand bis achtzehn Uhr bestaetigt hat."

# Ausgangslage K9: vor der Wiedergabe ist das Flag false (und existiert).
tts_active() { anfrage "$HUB_RT/state.sock" - 3 | jq -r '.voice | if has("tts_active") then (.tts_active|tostring) else "FEHLT" end'; }
SEK="K9"
chk "voice.tts_active ist vor der Wiedergabe false (POSITIVKONTROLLE)" \
  "$(tts_active)" false
SEK="K4"

starts_vorher="$(stub_zahl START)"
sprich "$A_TEXT" reaktion
chk "lange Aeusserung A wird angenommen (Vorbedingung)" "$SPR_OK" ja
# Auf EINEN NEUEN pw-cat warten -- nicht auf irgendeinen START/FIRST aus
# den frueheren Abschnitten, sonst misst die Unterbrechung gegen einen
# laengst toten Prozess.
for _ in $(seq 1 200); do
  [[ "$(stub_zahl START)" -gt "$starts_vorher" ]] && break
  sleep 0.05
done
A_PID="$(awk '/^START/ {p = $2} END {print p}' "$STUBLOG")"
stub_warte "^FIRST $A_PID " 20
chk "A laeuft wirklich noch (der Stub verbraucht in Echtzeit)" \
  "$([[ -n "$A_PID" ]] && kill -0 "$A_PID" 2>/dev/null && echo ja || echo nein)" ja
SEK="K9"
# K9, erste Haelfte: WAEHREND der Wiedergabe ist das Flag true -- gepollt,
# nicht einmalig, und korreliert mit dem Stub-Protokoll.
aktiv_gesehen=nein
for _ in $(seq 1 30); do
  [[ "$(tts_active)" == "true" ]] && { aktiv_gesehen=ja; break; }
  sleep 0.1
done
chk "voice.tts_active ist WAEHREND der Wiedergabe true" "$aktiv_gesehen" ja
SEK="K4"
# B: die neue Aeusserung. t_B unmittelbar vor der Dienstanfrage -- die
# Freigabe fuer B geht vorher (keine Abkuehlung: A ist noch nicht gesprochen).
# DIE LUECKE DER AKZEPTANZLISTE, entschieden im Gegenlesen Runde 2: eine
# Unterbrechung DARF die Abkuehlung umgehen -- sie ist eine Korrektur, kein
# zweites Geschwaeatz. Die Schranke gegen den Missbrauch als Umweg kommt
# weiter unten: nach dem ENDE von B muss die Frist wieder gelten.
f="$(freigabe reaktion "Kurz.")"
echo "  Freigabe-Antwort fuer B: $f"
B_MARKE="$(jq -r --arg f "$MARKE_FELD" '.[$f] // empty' <<<"$f")"
chk "UMGEHUNG: Freigabe fuer B waehrend A noch laeuft (Unterbrechung darf das)" \
  "$([[ -n "$B_MARKE" ]] && echo ja || echo "abgelehnt:_$(jq -r '.grund // "?"' <<<"$f")")" ja
"$PY" -B - "$RT/spr.json" "$DIENST_ART" "Kurz." reaktion "$B_MARKE" "$MARKE_FELD" <<'PYEOF'
import json, sys
ziel, art, text, kanal, marke, feld = sys.argv[1:7]
d = {"v": 1, "art": art, "text": text, "kanal": kanal, feld: marke}
if feld != "marke":
    d["marke"] = marke
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
PYEOF
firsts_vorher="$(stub_zahl FIRST)"
# Der Beobachter muss NEBENLAEUFIG laufen: startet die Messung erst, wenn
# die Antwort von B da ist, kann sie nie schneller sein als die Antwort --
# das waere Antwortlatenz, nicht Kill-Latenz (Gegenlesen Runde 2, Befund
# des Builders, und richtig). Messpunkt ist das PROZESSENDE: Popen.kill()
# schickt SIGKILL, und SIGKILL faehrt keine EXIT-Falle im Stub mehr, also
# /proc -- verschwunden oder Zombie (ein Zombie erzeugt keinen Ton mehr).
rm -f "$RT/a_tot"
( for _ in $(seq 1 2000); do
    if [[ ! -d "/proc/$A_PID" ]]; then date +%s%N >"$RT/a_tot"; break; fi
    zustand="$(sed 's/^.*) //' "/proc/$A_PID/stat" 2>/dev/null | cut -d' ' -f1)"
    if [[ "$zustand" == "Z" || "$zustand" == "X" ]]; then date +%s%N >"$RT/a_tot"; break; fi
    sleep 0.005
  done ) &
WAECHTER=$!
T_B="$(date +%s%N)"
dienst_anfrage "$RT/spr.json" >/dev/null
wait "$WAECHTER" 2>/dev/null
A_TOT_TS="$(cat "$RT/a_tot" 2>/dev/null)"
DELTA_MS="$("$PY" -B -c "print(round((int('${A_TOT_TS:-0}') - int('$T_B')) / 1e6, 1))")"
echo "  Ende von A nach Anfrage B: $DELTA_MS ms"
chk "DIE ZUSAGE: die alte Wiedergabe ist binnen 100 ms tot" \
  "$(awk -v d="$DELTA_MS" 'BEGIN{print (d >= 0 && d <= 100) ? "ja" : "nein"}')" ja
# Die 100-ms-Entscheidung ist gefallen; Bs Synthese und Prozessstart duerfen
# sich jetzt Zeit nehmen. Erst DANACH wird gezaehlt -- sonst misst der
# Kanarienvogel gegen einen noch laufenden Aufbau.
for _ in $(seq 1 200); do
  [[ "$(stub_zahl FIRST)" -gt "$firsts_vorher" ]] && break
  sleep 0.05
done
first_nach_b="$(awk -v t="$T_B" '$1 == "FIRST" && $3 >= t {ok = 1} END {print ok + 0}' "$STUBLOG")"
chk "die neue Aeusserung B kommt danach wirklich (kein Schweigen)" "$first_nach_b" 1
# Kein Mischen: Bs erstes Byte kommt nicht VOR As Ende.
misch_ms="$("$PY" -B -c "
zeilen = [z.split() for z in open('$STUBLOG') if z.startswith('FIRST')]
erste_b = min((int(z[2]) for z in zeilen if int(z[2]) >= int('$T_B')), default=0)
print(round((int('${A_TOT_TS:-0}') - erste_b) / 1e6, 1) if erste_b else -1)")"
chk "kein Mischen: B beginnt nicht vor As Ende (Toleranz 50 ms)" \
  "$(awk -v d="$misch_ms" 'BEGIN{print (d <= 50) ? "ja" : "nein"}')" ja
chk "genau zwei pw-cat-Aufrufe in diesem Abschnitt (A und B, kein dritter)" \
  "$(( $(stub_zahl START) - starts_vorher ))" 2
SEK="K9"
exits_vorher="$(stub_zahl EXIT)"
stub_warte "^EXIT" 20 || true
aktiv_noch=nein
for _ in $(seq 1 30); do
  [[ "$(tts_active)" == "false" ]] && { aktiv_noch=ja; break; }
  sleep 0.1
done
chk "voice.tts_active ist NACH der Wiedergabe wieder false" "$aktiv_noch" ja
SEK="K4"
# Die Gegenprobe zur Umgehung: nach dem ENDE von B muss die Abkuehlung
# wieder gelten -- sonst waere "unterbrechen" der Umweg, mit dem zwei
# Aeusserungen in Folge durchkaemen.
warte_gesprochen "$exits_vorher"
antwort="$(freigabe reaktion "Dritte Aeusserung gleich hinterher.")"
echo "  Freigabe-Antwort nach Bs Ende: $antwort"
chk "KEIN Umweg: nach Bs Ende greift die Abkuehlung wieder (C wird abgelehnt)" \
  "$(ist_abkuehlung "$antwort")" true

# =============================================================================
# K11: TTFA -- 20 Laeufe, p95 < 200 ms, Rohwerte nach tests/evidence/
# =============================================================================
echo
SEK="K11"
echo "--- K11: TTFA ueber 20 Laeufe (Anfrage -> erstes Sample am Stub) ---"
# Warm-up, ungezaehlt: Modell laden und erster Aufruf duerfen nicht in die
# Verteilung -- gemessen wird der warme Dienst, das Modell im Speicher IST
# das Kriterium.
sprich "Ja." rueckfrage >/dev/null 2>&1 || true
stub_warte "^FIRST" 15 || true

TTFA_WERTE=()
for i in $(seq 1 20); do
  # Frisches Zustandsverzeichnis je Lauf: die Abkuehlung ist ein ANDERES
  # Kriterium (oben) und darf die Latenzmessung nicht bremsen. Der Hub wird
  # dafuer neu gestartet -- derselbe Laufzeitpfad, der Dienst bleibt.
  hub_stop
  hub_start main "$RT/state-ttfa-$i"
  f="$(freigabe rueckfrage "Ja.")"
  m="$(jq -r --arg f "$MARKE_FELD" '.[$f] // empty' <<<"$f")"
  if [[ -z "$m" ]]; then
    laut "Lauf $i: keine Freigabe -- Lauf zaehlt als Messfehler"
    continue
  fi
  "$PY" -B - "$RT/spr.json" "$DIENST_ART" "Ja." rueckfrage "$m" "$MARKE_FELD" <<'PYEOF'
import json, sys
ziel, art, text, kanal, marke, feld = sys.argv[1:7]
d = {"v": 1, "art": art, "text": text, "kanal": kanal, feld: marke}
if feld != "marke":
    d["marke"] = marke
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
PYEOF
  t0="$(date +%s%N)"
  dienst_anfrage "$RT/spr.json" >/dev/null
  wert=""
  for _ in $(seq 1 250); do
    wert="$("$PY" -B - "$STUBLOG" "$t0" <<'PYEOF'
import sys
log, t0 = sys.argv[1], int(sys.argv[2])
for z in open(log):
    t = z.split()
    if t[0] == "FIRST" and int(t[2]) > t0:
        print(round((int(t[2]) - t0) / 1e6, 1))
        break
PYEOF
)"
    [[ -n "$wert" ]] && break
    sleep 0.02
  done
  if [[ -n "$wert" ]]; then
    TTFA_WERTE+=("$wert")
    printf '  Lauf %2d: %s ms\n' "$i" "$wert"
  else
    laut "Lauf $i: kein erstes Sample binnen 5 s -- Lauf zaehlt als rot"
    chk "TTFA Lauf $i brachte ein erstes Sample" nein ja
  fi
done
hub_stop
hub_start main "$HUBSTATE"

# Die Rohwerte gehoeren in die Evidence -- eine p95 ohne die 20 Zahlen ist
# nicht nachpruefbar.
EVI="$REPO/tests/evidence"
mkdir -p "$EVI"
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then
  EVI_DATEI="$EVI/T-3.9-ttfa-fixture-$(basename "$TARGET").json"
else
  EVI_DATEI="$EVI/T-3.9-ttfa.json"
fi
auswertung="$("$PY" -B - "$EVI_DATEI" "$TARGET" "$(date -Is)" "${TTFA_WERTE[@]:-}" <<'PYEOF'
import json, math, sys
ziel, baum, zeit = sys.argv[1], sys.argv[2], sys.argv[3]
werte = sorted(float(w) for w in sys.argv[4:] if w)
n = len(werte)
if n:
    p50 = werte[(n - 1) // 2]
    p95 = werte[max(0, math.ceil(0.95 * n) - 1)]
else:
    p50 = p95 = None
daten = {"task": "T-3.9", "kriterium": "TTFA p95 < 200 ms, Anfrage bis erstes Sample",
         "baum": baum, "zeit": zeit, "anzahl": n, "rohwerte_ms": werte,
         "p50_ms": p50, "p95_ms": p95, "max_ms": werte[-1] if n else None,
         "min_ms": werte[0] if n else None}
with open(ziel, "w", encoding="utf-8") as fh:
    json.dump(daten, fh, ensure_ascii=False, indent=2)
print(json.dumps({"n": n, "p50": p50, "p95": p95,
                  "min": daten["min_ms"], "max": daten["max_ms"]}))
PYEOF
)"
echo "  Auswertung: $auswertung"
echo "  Evidence: $EVI_DATEI"
chk "20 Laeufe brachten je ein erstes Sample" "$(jq -r '.n' <<<"$auswertung")" 20
chk "POSITIVKONTROLLE: die Messung unterscheidet (min > 0 ms)" \
  "$(jq -r '(.min // 0) > 0' <<<"$auswertung")" true
chk "DIE ZUSAGE: TTFA p95 < 200 ms" \
  "$(jq -r '(.p95 // 9999) < 200' <<<"$auswertung")" true

# =============================================================================
# K10: 0 VRAM -- am LAUFENDEN Dienst, nicht am Quelltext
# =============================================================================
echo
SEK="K10"
echo "--- K10: 0 VRAM am laufenden Dienst ---"
map_zeilen="$(wc -l <"/proc/$DIENST_PID/maps" 2>/dev/null || echo 0)"
chk "/proc/<pid>/maps lesbar und nichtleer (POSITIVKONTROLLE)" \
  "$([[ "$map_zeilen" -gt 0 ]] && echo ja || echo nein)" ja
gpu_libs="$(grep -cE 'libcuda|libcudart|libnvinfer|libnvrtc' "/proc/$DIENST_PID/maps" 2>/dev/null || true)"
echo "  CUDA-Bibliotheken im Adressraum: ${gpu_libs:-unlesbar}"
chk "keine libcuda/libcudart/libnvinfer/libnvrtc geladen" "${gpu_libs:-1}" 0
fd_zahl="$(ls "/proc/$DIENST_PID/fd" 2>/dev/null | wc -l)"
chk "/proc/<pid>/fd lesbar und nichtleer (POSITIVKONTROLLE)" \
  "$([[ "$fd_zahl" -gt 0 ]] && echo ja || echo nein)" ja
dri="$(ls -l "/proc/$DIENST_PID/fd" 2>/dev/null | grep -c '/dev/dri/' || true)"
chk "keine /dev/dri-Deskriptoren" "${dri:-1}" 0
compute_nachher="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ' | sort)"
chk "nvidia-smi ist abfragbar (POSITIVKONTROLLE)" \
  "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "der Dienst steht NICHT als Compute-Prozess in nvidia-smi" \
  "$(grep -q "^$DIENST_PID\$" <<<"$compute_nachher" && echo nein || echo ja)" ja
chk "kein zusaetzlicher Compute-Prozess seit Laufbeginn" \
  "$([[ "$compute_nachher" == "$NVIDIA_VORHER" ]] && echo ja || { echo "vorher: ${NVIDIA_VORHER:-leer} nachher: ${compute_nachher:-leer}"; echo nein; })" ja

# =============================================================================
# Die Unit-Dateien -- kommentarbereinigt (Erwaеhnung ist nicht Direktive)
# =============================================================================
echo
SEK="KU"
echo "--- Units: daimon-tts.service und daimon-tts.socket ---"
UNIT_SVC="$(sed 's/^[[:space:]]*#.*//' "$TARGET/config/systemd/daimon-tts.service")"
UNIT_SCK="$(sed 's/^[[:space:]]*#.*//' "$TARGET/config/systemd/daimon-tts.socket")"
UNIT_SVC_ROH="$(cat "$TARGET/config/systemd/daimon-tts.service")"
chk "socket: ListenStream unter %t/daimon" \
  "$(grep -qE '^ListenStream=%t/daimon/\S+\.sock' <<<"$UNIT_SCK" && echo ja || echo nein)" ja
chk "socket: SocketMode=0600" "$(grep -q '^SocketMode=0600' <<<"$UNIT_SCK" && echo ja || echo nein)" ja
chk "socket: Accept=no (kein Prozess je Verbindung)" \
  "$(grep -q '^Accept=no' <<<"$UNIT_SCK" && echo ja || echo nein)" ja
chk "socket: RuntimeDirectory=daimon" \
  "$(grep -q '^RuntimeDirectory=daimon' <<<"$UNIT_SCK" && echo ja || echo nein)" ja
chk "socket: RuntimeDirectoryPreserve=yes" \
  "$(grep -q '^RuntimeDirectoryPreserve=yes' <<<"$UNIT_SCK" && echo ja || echo nein)" ja
chk "socket: aktiviert ueber sockets.target" \
  "$(grep -qE '^WantedBy=sockets.target' <<<"$UNIT_SCK" && echo ja || echo nein)" ja
chk "service: Requires auf den Socket (ohne ihn waere er unerreichbar)" \
  "$(grep -qE '^Requires=\S*daimon-tts\S*\.socket' <<<"$UNIT_SVC" && echo ja || echo nein)" ja
chk "service: RuntimeDirectory=daimon" \
  "$(grep -q '^RuntimeDirectory=daimon' <<<"$UNIT_SVC" && echo ja || echo nein)" ja
chk "service: RuntimeDirectoryPreserve=yes" \
  "$(grep -q '^RuntimeDirectoryPreserve=yes' <<<"$UNIT_SVC" && echo ja || echo nein)" ja
chk "service: kein [Install]/WantedBy -- der Socket aktiviert, nicht enable" \
  "$(grep -qE '^WantedBy=' <<<"$UNIT_SVC" && echo nein || echo ja)" ja
chk "service: RestrictAddressFamilies enthaelt AF_UNIX" \
  "$(grep -E '^RestrictAddressFamilies=' <<<"$UNIT_SVC" | grep -q 'AF_UNIX' && echo ja || echo nein)" ja
chk "service: RestrictAddressFamilies enthaelt KEIN AF_INET/AF_INET6" \
  "$(grep -E '^RestrictAddressFamilies=' <<<"$UNIT_SVC" | grep -qE 'AF_INET6?\b' && echo nein || echo ja)" ja
for direktive in NoNewPrivileges=yes ProtectSystem=strict ProtectHome=read-only \
                 PrivateTmp=yes LimitCORE=0 UMask=0077; do
  chk "service: $direktive" \
    "$(grep -qE "^${direktive/=/=}" <<<"$UNIT_SVC" && echo ja || echo nein)" ja
done
# PrivateDevices und MemoryDenyWriteExecute: gesetzt ODER im Fliesstext
# begruendet -- die Vorgabe des Plans ist 'pruefen und den Grund in die Unit
# schreiben', beide Ausgaenge sind ehrlich.
chk "service: PrivateDevices ist gesetzt ODER begruendet" \
  "$(grep -q '^PrivateDevices=' <<<"$UNIT_SVC" && echo ja || { grep -q 'PrivateDevices' <<<"$UNIT_SVC_ROH" && echo ja || echo nein; })" ja
chk "service: MemoryDenyWriteExecute ist gesetzt ODER begruendet" \
  "$(grep -q '^MemoryDenyWriteExecute=' <<<"$UNIT_SVC" && echo ja || { grep -q 'MemoryDenyWriteExecute' <<<"$UNIT_SVC_ROH" && echo ja || echo nein; })" ja

# =============================================================================
# Letzter Kanarienvogel: nach ALLEN Angriffen spricht die Kette noch
# =============================================================================
echo
SEK="K12"
echo "--- K12: Schluss-Kanarienvogel im selben Lauf ---"
bytes_vorher="$(stub_bytes)"
exits_vorher="$(stub_zahl EXIT)"
sprich "Der Kanarienvogel singt noch einmal." reaktion
warte_gesprochen "$exits_vorher"
chk "SCHLUSS-KANARIENVOGEL: nach allen Angriffen wird der harmlose Satz gesprochen" \
  "$([[ "$(stub_bytes)" -gt $((bytes_vorher + 2000)) ]] && echo ja || echo nein)" ja

# =============================================================================
# Leck-Pruefung: kein TTS-Dienst ueberlebt den Lauf
# =============================================================================
# Der Befund vom 05.08.: vier verwaiste daimon.face.tts je Lauf, weil nur der
# Aktivator gekillt wurde und der Dienst ein Enkelkind ist. Gemessen wird AM
# SYSTEM, nicht an den eigenen Aufzeichnungen: nach dem Aufraeumen darf es
# keinen Prozess mehr geben, dessen Kommandozeile daimon.face.tts nennt UND
# einen Pfad unter $RT traegt (das $RT grenzt gegen fremde, parallel laufende
# Pruefstaende ab). Positivkontrolle zuerst: VOR dem Aufraeumen muss es
# mindestens einen solchen Prozess geben -- sonst waere die Pruefung auch
# dann gruen, wenn nie ein Dienst gelaufen waere.
echo
SEK="L"
echo "--- Leck-Pruefung: kein Dienst ueberlebt den Lauf ---"
leck_treffer() {
  local p args
  for p in /proc/[0-9]*; do
    args="$(tr '\0' ' ' <"$p/cmdline" 2>/dev/null)" || continue
    [[ "$args" == *daimon.face.tts* && "$args" == *"$RT/"* ]] && echo "${p#/proc/}"
  done
}
chk "POSITIVKONTROLLE: waehrend des Laufs lief ein Dienst mit Pfaden unter \$RT" \
  "$([[ -n "$(leck_treffer)" ]] && echo ja || echo nein)" ja
dienst_stop
sleep 0.5
rest="$(leck_treffer | tr '\n' ' ' | sed 's/ $//')"
chk "nach dem Aufraeumen ist KEIN daimon.face.tts mit Pfaden unter \$RT uebrig" \
  "$([[ -z "$rest" ]] && echo ja || { echo "uebrig: $rest"; echo nein; })" ja

# =============================================================================
# Abrechnung
# =============================================================================
echo
echo "--- Abrechnung je Kriterium ---"
gesamt=0; gesamt_rot=0
for k in V K1 K2 K3 K4 K5 K6 K7 K8 K9 K10 K11 K12 KU L; do
  n="${PRUEFUNGEN[$k]:-0}"; r="${ROT[$k]:-0}"
  [[ "$n" -eq 0 ]] && continue
  gesamt=$((gesamt + n)); gesamt_rot=$((gesamt_rot + r))
  printf '  %-3s %3d Pruefungen, %d rot\n' "$k" "$n" "$r"
done
echo "  ---"
printf '  gesamt: %d Pruefungen, %d rot (Modus: %s)\n' "$gesamt" "$gesamt_rot" \
  "$([[ -n "${DAIMON_FIXTURE:-}" ]] && echo "FIXTURE $TARGET" || echo ARBEITSBAUM)"

echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) KRITERIUM 8 (Plan) IST NUR TEILERFUELLT MESSBAR. Kein Prozess haelt"
echo "      eine Rueckkopplungssperre (der Ohren-Dienst existiert nicht), also"
echo "      meldet der Dienst an den Hub: geprueft ist voice.tts_active waehrend"
echo "      und nach der Wiedergabe (K9). Die ECHO-REFERENZ wird nicht"
echo "      uebertragen -- es gibt niemanden, der sie annimmt (Plan §3.1)."
echo "  (2) 'pw-cat ohne absoluten Pfad' ist belegt, SOLANGE der Stub feuert."
echo "      Bluebe er still, waere das ein Befund und der execve-Mitschnitt"
echo "      stuende oben im Protokoll."
echo "  (3) TTFA misst Anfrage-an-den-Dienst bis erstes Sample am Stub. Der"
echo "      Hub-Rundlauf (Freigabe) ist nicht eingerechnet -- er ist klein und"
echo "      gehoert zur Hub-Latenz, nicht zur Synthese."
echo "  (4) Klang ist ungemessen: DASS und WANN Daten fliessen, nicht WIE sie"
echo "      klingen. Satzmelodie prueft der Mensch, nicht der Stub."
echo "  (5) Die '~140 Zeichen' werden als <= 140 plus 500-Zeichen-Absage"
echo "      geprueft; die Zone dazwischen ist Auslegungsspielraum des Plans."
echo "  (6) Vorlagenfeld (vorlage/anlass) und Marke-Feldname werden ENTDECKT"
echo "      und stehen oben im Protokoll. Eine Implementierung mit anderem"
echo "      Vokabular faellt hier als 'nicht auffindbar' auf -- das ist dann"
echo "      ein Punkt fuers Gegenlesen, nicht automatisch ein Defekt."
echo "  (7) Der Stub belegt Textfluss bis zum TTS-Vertrag (bereinigter Text in"
echo "      Hub- und Dienstantwort) und PCM-Fluss zur Ausgabe. Er dekodiert das"
echo "      PCM nicht wieder zu Sprache; Aussprache und semantischer Audioinhalt"
echo "      bleiben Teil der ungemessenen Klangpruefung aus (4)."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-3.9: gruen. Der Validator sitzt im Hub, der Dienst spricht nur mit"
  echo "       Marke, verbotene Angriffstexte kamen nicht durch und die drei"
  echo "       Unicode-Klassen nur bereinigt -- der"
  echo "       Kanarienvogel wurde vorgelesen. KEINE volle Abnahme -- siehe"
  echo "       die offenen Punkte oben, insbesondere (1)."
else
  echo "T-3.9: FEHLGESCHLAGEN"
fi
exit $fail
