#!/usr/bin/env bash
# Verifizierer fuer T-3.1: Aufnahme mit hartem Lebenszyklus.
#
# Die Zusage, um die es geht, ist NICHT "es wird aufgenommen". Sie lautet:
# **nach dem Ausschalten ist das Source-Output-Objekt WEG, nicht nur pausiert.**
# Design 7.4 nennt den Mikrofon-Kill-Switch eine Korrektheitsanforderung: ein
# Nutzer, der dem Plasma-Indikator glaubt, muss ihm glauben koennen. `stop()`
# pausiert den Strom und laesst das Objekt stehen, `close()` zerstoert es.
#
# Gemessen wird das von AUSSEN, ueber `pw-dump` -- nicht ueber `zustand()`.
# `zustand()` ist der Selbstbericht des Prueflings; er dient hier nur dem
# Nachweis der PARAMETER (Rate, Kanaele, dtype, device), nie dem Nachweis,
# dass das Geraet zu ist. (Regel 9; Fall 9 im Handover: ein Verifizierer, der
# den vom Prueflings selbst gefuehrten Zaehler beobachtet, misst nichts.)
#
# DREI PHASEN, jede einzeln geprueft, und die mittlere ist die wichtigste:
#
#   vorher        Der Prozess LEBT, `start()` ist noch nicht gerufen.
#                 -> kein Strom, der ihm gehoert.
#   waehrenddessen genau EIN Strom, und zwar seiner. Ohne diese
#                 Positivkontrolle sagt "nachher keiner" gar nichts -- ein
#                 Prozess, der nie ein Mikrofon geoeffnet hat, hat auch
#                 hinterher keins offen (elf Faelle in HANDOVER.md).
#   nachher       Unmittelbar nachdem `stop()` ZURUECKGEKEHRT ist und
#                 WAEHREND DER PROZESS NOCH LEBT.
#
# Der letzte Halbsatz ist das ganze Kriterium. Ein toter Prozess hat immer
# alle Objekte abgegeben -- ein Verifizierer, der erst nach dem Prozessende
# misst, ist gegen `stop()` statt `close()` blind und wird gruen. Deshalb
# prueft dieses Skript in derselben Phase ausdruecklich, dass der Prozess noch
# lebt, und der Mutant `pw-dump-vor-dem-teardown` zielt genau darauf.
#
# IDENTITAET UEBER DIE PID, NICHT UEBER DEN NAMEN. Der Auftrag nennt
#   pw-dump | jq -r '.[]|select(.info.props["media.class"]=="Stream/Input/Audio")
#                     |.info.props["application.name"]'
# -- diese Zeile laeuft hier in jeder Phase und steht im Protokoll. Das Urteil
# haengt aber an einer staerkeren Zuordnung: der Knoten traegt `client.id`, das
# zugehoerige Client-Objekt traegt `application.process.id`, und das ist die PID
# unseres Treiberprozesses. Grund, heute gemessen: `application.name` ist auf
# dieser Maschine NICHT eindeutig -- waehrend der Vermessung liefen fremde
# Prozesse mit demselben Namen (der parallel arbeitende Builder). Ein Name ist
# keine Identitaet.
#
# WARTEN STATT SCHLAFEN. Keine feste `sleep`-Zeit fuer eine Voraussetzung: auf
# das Erscheinen eines Stroms wird gewartet, mit Zeitgrenze und lauter Meldung.
# Am 02.08. hat genau das einen Verifizierer flackernd gemacht.
#
# POSITIVKONTROLLE FUER DAS MESSVERFAHREN SELBST. Ein zweiter, vom Pruefling
# voellig unabhaengiger Strom (der "Kanarienvogel", 48 kHz float32 statt
# 16 kHz int16) laeuft waehrend des GANZEN Laufs. In jeder Phase wird geprueft,
# dass pw-dump ihn sieht. Ohne ihn waere "kein Strom" die Nullaussage
# schlechthin -- ein kaputtes pw-dump meldet ebenfalls nichts. Am Ende wird er
# beendet und muss verschwinden; damit ist auch "weg" als Messung belegt.
#
# HARTE OBERGRENZEN. Jeder Prozess, der das Mikrofon oeffnet, laeuft unter
# `timeout`; zusaetzlich raeumt eine trap auf. Ein haengender Aufnahmeprozess
# leuchtet im Systemtray.
#
# WAS DIESES SKRIPT NICHT PRUEFEN KANN -- ausdruecklich, nicht kaschiert:
#   * `PIPEWIRE_LATENCY` wirkt auch, wenn es NACH dem Import gesetzt wird.
#     Heute gemessen: node.latency ist "512/16000" mit der Variablen, ohne
#     die Variable, und bei Setzen nach dem Import -- der Wert kommt aus
#     Blockgroesse und Rate, nicht aus der Umgebung. Die Reihenfolge
#     "vor dem Import" ist an pw-dump also NICHT sichtbar. Gemessen wird sie
#     stattdessen mit einem Audit-Hook (`sys.addaudithook`) im Treiber: er
#     schnappt sich os.environ in dem Moment, in dem `sounddevice` importiert
#     wird. Das ist eine Beobachtung am laufenden Prozess, kein grep -- aber
#     es ist auch kein Beleg dafuer, dass die Variable etwas BEWIRKT.
#   * device="pipewire" statt "default" ist von aussen praktisch nicht zu
#     trennen: beide Wege landen ueber das ALSA-Plugin bei denselben
#     Knoten-Eigenschaften. Einziger heute beobachteter Unterschied ist
#     `remote.name`; er wird protokolliert, aber NICHT gewertet. Das Urteil
#     kommt hier aus `zustand()["device"]`, also aus dem Selbstbericht.
#   * Dass der Plasma-Indikator tatsaechlich ausgeht, prueft niemand. Geprueft
#     ist das Objekt, an dem er haengt.
#
# Aufruf:
#   tests/verify/T-3.1.sh
#   DAIMON_FIXTURE=<baum> tests/verify/T-3.1.sh   # Baum mit eigenem daimon/
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"

# Zeitgrenzen. Sie sind Kriterien bzw. Obergrenzen, keine Wartezeiten.
FRIST_ERSCHEINEN="${DAIMON_T31_FRIST_ERSCHEINEN:-15}"   # s, bis der Strom da ist
FRIST_VERSCHWINDEN="${DAIMON_T31_FRIST_WEG:-2.0}"       # s, nach Rueckkehr von stop()
MAX_TREIBER="${DAIMON_T31_MAX_SECS:-120}"               # s, harte Obergrenze
MAX_KANARIE=$(( MAX_TREIBER + 60 ))

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
laut() { echo "  !! $*"; }

echo "T-3.1 — Aufnahme: nach dem Ausschalten ist das Objekt WEG, nicht pausiert"
echo "  Baum: $TARGET"
echo "  Interpreter: $PY"

# =============================================================================
# 0. Voraussetzungen
# =============================================================================
echo
echo "--- 0. Voraussetzungen ---"
chk "pw-dump vorhanden" "$(command -v pw-dump >/dev/null && echo ja || echo nein)" ja
chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "timeout vorhanden" "$(command -v timeout >/dev/null && echo ja || echo nein)" ja
chk "Interpreter vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja
chk "sounddevice importierbar" \
  "$("$PY" -c 'import sounddevice' >/dev/null 2>&1 && echo ja || echo nein)" ja
objekte="$(pw-dump 2>/dev/null | jq 'length' 2>/dev/null)"
chk "pw-dump liefert Objekte (PipeWire laeuft)" \
  "$([[ "${objekte:-0}" -gt 0 ]] && echo ja || echo nein)" ja
quellen="$(pw-dump 2>/dev/null | jq -r '[.[]|select(.info.props["media.class"]=="Audio/Source")]|length')"
echo "  Audio/Source-Quellen: ${quellen:-0}"
chk "mindestens eine Aufnahmequelle vorhanden" \
  "$([[ "${quellen:-0}" -gt 0 ]] && echo ja || echo nein)" ja
chk "der Baum bringt daimon/ears/capture.py mit" \
  "$([[ -f "$TARGET/daimon/ears/capture.py" ]] && echo ja || echo nein)" ja

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "T-3.1: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen."
  exit 1
fi

RT="$(mktemp -d)"
KANARIE=""; TREIBER=""
aufraeumen() {
  [[ -n "$TREIBER" ]] && kill "$TREIBER" 2>/dev/null
  [[ -n "$KANARIE" ]] && kill "$KANARIE" 2>/dev/null
  exec 3>&- 2>/dev/null
  sleep 0.2
  rm -rf -- "$RT"
}
trap aufraeumen EXIT INT TERM

# =============================================================================
# Das Messwerkzeug: pw-dump, aufgeloest bis zur PID
# =============================================================================
cat >"$RT/messen.py" <<'PYEOF'
"""Liest pw-dump und meldet jeden Stream/Input/Audio mit seiner Prozess-PID.

Der Knoten selbst traegt keine PID -- er traegt `client.id`, und das
Client-Objekt traegt `application.process.id`. Diese Verbindung ist die
einzige harte Zuordnung "Strom gehoert Prozess X"; `application.name` ist
auf dieser Maschine nicht eindeutig.
"""
import json
import subprocess
import sys

roh = subprocess.run(["pw-dump"], capture_output=True, timeout=30)
objs = json.loads(roh.stdout)
clients = {}
for o in objs:
    if o.get("type") == "PipeWire:Interface:Client":
        clients[o.get("id")] = (o.get("info") or {}).get("props") or {}

streams = []
for o in objs:
    info = o.get("info") or {}
    props = info.get("props") or {}
    if props.get("media.class") != "Stream/Input/Audio":
        continue
    cp = clients.get(props.get("client.id"), {})
    fmt = ((info.get("params") or {}).get("Format") or [{}])[0]
    streams.append({
        "name": props.get("application.name"),
        "pid": cp.get("application.process.id"),
        "node_latency": props.get("node.latency"),
        "node_rate": props.get("node.rate"),
        "remote": props.get("remote.name"),
        "format": fmt.get("format"),
        "rate": fmt.get("rate"),
        "channels": fmt.get("channels"),
        "position": ",".join(fmt.get("position") or []),
    })
print(json.dumps({"streams": streams}))
PYEOF

# Der unabhaengige Kanarienvogel. Bewusst ANDERE Parameter als der Pruefling
# (48 kHz, float32, Vorgabe-Blockgroesse), damit die beiden nicht zu
# verwechseln sind.
cat >"$RT/kanarie.py" <<'PYEOF'
import os
import sys
import time

os.environ.pop("PIPEWIRE_LATENCY", None)
import sounddevice as sd

s = sd.InputStream(samplerate=48000, channels=1, dtype="float32")
s.start()
print(os.getpid(), flush=True)
try:
    time.sleep(float(sys.argv[1]))
finally:
    s.close()
PYEOF

# Der Treiber. Er laedt den Pruefling AUS DEM GEPRUEFTEN BAUM und fuehrt
# genau das aus, was ihm zeilenweise gesagt wird -- damit der Verifizierer die
# Reihenfolge in der Hand hat und "nachher" wirklich nach der Rueckkehr von
# stop() misst.
cat >"$RT/treiber.py" <<'PYEOF'
import importlib
import inspect
import json
import os
import sys
import traceback

BAUM = sys.argv[1]
sys.path.insert(0, BAUM)

# Der Audit-Hook beobachtet den Import von `sounddevice` und haelt fest, ob
# PIPEWIRE_LATENCY zu diesem Zeitpunkt schon in der Umgebung stand. Das ist
# die einzige Stelle, an der die Reihenfolge "Variable VOR dem Import"
# ueberhaupt beobachtbar ist -- an pw-dump ist sie es nicht.
schnapp = {"sounddevice": "nie-importiert", "hook_kanarie": "nein", "ereignisse": 0}


def hook(ereignis, args):
    if ereignis != "import":
        return
    schnapp["ereignisse"] += 1
    name = args[0]
    if name == "sounddevice" and schnapp["sounddevice"] == "nie-importiert":
        schnapp["sounddevice"] = os.environ.get("PIPEWIRE_LATENCY", "(nicht-gesetzt)")
    elif name == "wave":
        schnapp["hook_kanarie"] = "ja"


sys.addaudithook(hook)
import wave  # noqa: E402,F401  -- Kanarienvogel: feuert der Hook ueberhaupt?


def sag(**kw):
    print(json.dumps(kw), flush=True)


sag(ok=True, schritt="bereit", pid=os.getpid(),
    hook_kanarie=schnapp["hook_kanarie"], ereignisse=schnapp["ereignisse"])

modul = None
aufnahme = None
for zeile in sys.stdin:
    befehl = zeile.strip()
    try:
        if befehl == "laden":
            modul = importlib.import_module("daimon.ears.capture")
            sag(ok=True, schritt="laden", datei=os.path.abspath(modul.__file__),
                hat_aufnahme=hasattr(modul, "Aufnahme"),
                signatur=str(inspect.signature(modul.Aufnahme))
                if hasattr(modul, "Aufnahme") else "")
        elif befehl == "erzeugen":
            aufnahme = modul.Aufnahme()
            sag(ok=True, schritt="erzeugen",
                latency_beim_import=schnapp["sounddevice"],
                env_jetzt=os.environ.get("PIPEWIRE_LATENCY", "(nicht-gesetzt)"))
        elif befehl == "start":
            aufnahme.start()
            sag(ok=True, schritt="start",
                latency_beim_import=schnapp["sounddevice"],
                env_jetzt=os.environ.get("PIPEWIRE_LATENCY", "(nicht-gesetzt)"))
        elif befehl == "zustand":
            sag(ok=True, schritt="zustand", zustand=aufnahme.zustand())
        elif befehl == "stop":
            aufnahme.stop()
            # Die Antwort geht erst raus, wenn stop() ZURUECKGEKEHRT ist.
            sag(ok=True, schritt="stop")
        elif befehl == "lebe":
            sag(ok=True, schritt="lebe", pid=os.getpid())
        elif befehl == "ende":
            sag(ok=True, schritt="ende")
            break
        else:
            sag(ok=False, schritt=befehl, fehler="unbekannter Befehl")
    except Exception as exc:
        sag(ok=False, schritt=befehl, fehler=f"{type(exc).__name__}: {exc}",
            spur=traceback.format_exc()[-600:])
PYEOF

mess() {  # gibt eine JSON-Zeile aus
  timeout --foreground --signal=TERM --kill-after=5s 40s "$PY" "$RT/messen.py"
}
# Die Zeile aus dem Auftrag, woertlich. Sie steht im Protokoll; das Urteil
# haengt an der PID-Zuordnung darunter.
namen_roh() {
  pw-dump | jq -r '.[]|select(.info.props["media.class"]=="Stream/Input/Audio")
                    |.info.props["application.name"]'
}
# Anzahl der Stroeme einer PID in einer gespeicherten Messung.
n_pid() { jq --argjson p "$2" '[.streams[]|select(.pid==$p)]|length' <"$1"; }
# Stroeme mit einem Namen, die NICHT einem fremden lebenden Prozess gehoeren.
# Fremde gleichnamige Prozesse werden gesondert gemeldet, nicht gewertet:
# heute lief waehrend der Vermessung ein fremder Prozess mit demselben Namen.
n_name_eigen() { jq --arg n "$2" --argjson p "$3" \
  '[.streams[]|select(.name==$n)|select(.pid==$p or .pid==null)]|length' <"$1"; }
n_name_fremd() { jq --arg n "$2" --argjson p "$3" \
  '[.streams[]|select(.name==$n)|select(.pid!=$p and .pid!=null)]|length' <"$1"; }

# =============================================================================
# 1. Der Kanarienvogel: kann pw-dump ueberhaupt Stroeme sehen?
# =============================================================================
echo
echo "--- 1. Positivkontrolle des MESSVERFAHRENS ---"
timeout --foreground --signal=TERM --kill-after=5s "${MAX_KANARIE}s" \
  "$PY" "$RT/kanarie.py" "$MAX_KANARIE" >"$RT/kanarie.pid" 2>"$RT/kanarie.log" &
KANARIE=$!
K_PID=""
grenze=$(( SECONDS + FRIST_ERSCHEINEN ))
while [[ $SECONDS -lt $grenze ]]; do
  K_PID="$(head -1 "$RT/kanarie.pid" 2>/dev/null | tr -dc '0-9')"
  [[ -n "$K_PID" ]] && break
  sleep 0.1
done
if [[ -z "$K_PID" ]]; then
  laut "Der Kanarienvogel-Prozess hat binnen ${FRIST_ERSCHEINEN}s keine PID gemeldet."
  laut "Protokoll: $(tail -5 "$RT/kanarie.log")"
  chk "1 Kanarienvogel-Prozess startet" nein ja
  echo; echo "T-3.1: FEHLGESCHLAGEN — ohne Messverfahren wird nichts gemessen."
  exit 1
fi
echo "  Kanarienvogel-PID: $K_PID"
k_sichtbar=nein
grenze=$(( SECONDS + FRIST_ERSCHEINEN ))
while [[ $SECONDS -lt $grenze ]]; do
  mess >"$RT/k.json"
  [[ "$(n_pid "$RT/k.json" "$K_PID")" -ge 1 ]] && { k_sichtbar=ja; break; }
  sleep 0.1
done
[[ "$k_sichtbar" == ja ]] || laut "pw-dump sieht binnen ${FRIST_ERSCHEINEN}s keinen Strom des Kanarienvogels — das MESSVERFAHREN ist blind, nicht der Pruefling."
chk "1 pw-dump sieht einen fremden, bekannten Aufnahmestrom (MESSVERFAHREN)" "$k_sichtbar" ja
echo "  application.name laut Auftragszeile: $(namen_roh | tr '\n' '|')"
if [[ "$k_sichtbar" != ja ]]; then
  echo; echo "T-3.1: FEHLGESCHLAGEN — das Messverfahren selbst ist nicht belegt."
  exit 1
fi

# =============================================================================
# 2. Der Pruefling: Prozess lebt, Aufnahme noch nicht gestartet
# =============================================================================
echo
echo "--- 2. Pruefling laden (Prozess lebt, start() noch nicht gerufen) ---"
mkfifo "$RT/ein"
: >"$RT/aus"
: >"$RT/treiber.log"
timeout --foreground --signal=TERM --kill-after=5s "${MAX_TREIBER}s" \
  "$PY" -P "$RT/treiber.py" "$TARGET" <"$RT/ein" >"$RT/aus" 2>"$RT/treiber.log" &
TREIBER=$!
exec 3>"$RT/ein"
A='{}'
# Auf die naechste Antwortzeile warten, nicht schlafen.
warte_antwort() {  # $1 = Beschriftung, $2 = Zeilenzahl vorher
  local grenze
  grenze=$(( SECONDS + 30 ))
  while [[ $SECONDS -lt $grenze ]]; do
    if [[ "$(wc -l <"$RT/aus")" -gt "$2" ]]; then
      A="$(tail -1 "$RT/aus")"
      return 0
    fi
    sleep 0.05
  done
  A='{"ok":false,"schritt":"(keine Antwort)"}'
  laut "keine Antwort auf '$1' binnen 30 s. Treiberprotokoll:"
  tail -8 "$RT/treiber.log"
  return 1
}
warte_antwort "bereit" 0 || true
T_PID="$(jq -r '.pid // empty' <<<"$A" 2>/dev/null)"
chk "2 Treiberprozess meldet sich" "$([[ -n "$T_PID" ]] && echo ja || echo nein)" ja
if [[ -z "$T_PID" ]]; then
  laut "Treiberprotokoll:"; tail -10 "$RT/treiber.log"
  echo; echo "T-3.1: FEHLGESCHLAGEN"; exit 1
fi
echo "  Treiber-PID: $T_PID"
chk "2 POSITIVKONTROLLE: der Audit-Hook feuert ueberhaupt" \
  "$(jq -r '.hook_kanarie' <<<"$A")" ja

befehl() {  # sendet einen Befehl und wartet auf GENAU dessen Antwort
  local n
  n="$(wc -l <"$RT/aus")"
  echo "$1" >&3
  warte_antwort "$1" "$n"
}

befehl laden || { echo "T-3.1: FEHLGESCHLAGEN"; exit 1; }
echo "  Antwort auf 'laden': $A"
chk "2 daimon.ears.capture laedt" "$(jq -r '.ok' <<<"$A")" true
if [[ "$(jq -r '.ok' <<<"$A")" != true ]]; then
  echo; echo "T-3.1: FEHLGESCHLAGEN — der Pruefling laesst sich nicht laden."
  exit 1
fi
geladen="$(jq -r '.datei' <<<"$A")"
echo "  geladen aus: $geladen"
# Fall 12 im Handover: eine Positivkontrolle, die nie gruen werden kann, macht
# jeden Mutanten "erkannt", ohne dass seine Mutation je gemessen wurde.
chk "2 POSITIVKONTROLLE: der Pruefling stammt AUS DEM GEPRUEFTEN BAUM" \
  "$([[ "$geladen" == "$TARGET"/* ]] && echo ja || echo "aus_$geladen")" ja
chk "2 das Modul bietet die Klasse Aufnahme (Diagnose-Vertrag)" \
  "$(jq -r '.hat_aufnahme' <<<"$A")" true

befehl erzeugen || { echo "T-3.1: FEHLGESCHLAGEN"; exit 1; }
if [[ "$(jq -r '.ok' <<<"$A")" != true ]]; then
  laut "Aufnahme() liess sich nicht erzeugen: $(jq -r '.fehler' <<<"$A")"
  laut "Der Verifizierer ruft Aufnahme() ohne Argumente. Verlangt die Umsetzung"
  laut "welche, ist das eine Abweichung vom Diagnose-Vertrag der Akzeptanzliste."
fi
chk "2 Aufnahme() laesst sich ohne Argumente erzeugen" "$(jq -r '.ok' <<<"$A")" true
latency_import="$(jq -r '.latency_beim_import // "?"' <<<"$A")"

# --- Phase VORHER -------------------------------------------------------------
mess >"$RT/vorher.json"
echo "  Auftragszeile (vorher): $(namen_roh | tr '\n' '|')"
chk "2 VORHER: kein Strom gehoert dem Pruefling-Prozess" "$(n_pid "$RT/vorher.json" "$T_PID")" 0
chk "2 VORHER: das Messverfahren sieht in diesem Moment Stroeme (Kanarienvogel)" \
  "$([[ "$(n_pid "$RT/vorher.json" "$K_PID")" -ge 1 ]] && echo ja || echo nein)" ja
chk "2 VORHER: der Pruefling-Prozess lebt (sonst waere 'kein Strom' trivial)" \
  "$(kill -0 "$TREIBER" 2>/dev/null && echo ja || echo nein)" ja

# =============================================================================
# 3. Waehrenddessen: genau EINER, und zwar seiner
# =============================================================================
echo
echo "--- 3. Waehrenddessen ---"
befehl start || { echo "T-3.1: FEHLGESCHLAGEN"; exit 1; }
chk "3 start() kehrt ohne Fehler zurueck" "$(jq -r '.ok' <<<"$A")" true
[[ "$(jq -r '.ok' <<<"$A")" == true ]] || { laut "$(jq -r '.fehler' <<<"$A")"; }
latency_import="$(jq -r '.latency_beim_import // "?"' <<<"$A")"

# Warten, bis der Strom da ist. Keine feste Wartezeit -- gemessen wurde heute
# ein Erscheinen nach 0,02 s, aber eine feste Zahl macht einen Verifizierer
# flackernd (02.08.).
erschienen=nein; t0="$SECONDS"
grenze=$(( SECONDS + FRIST_ERSCHEINEN ))
while [[ $SECONDS -lt $grenze ]]; do
  mess >"$RT/waehrend.json"
  [[ "$(n_pid "$RT/waehrend.json" "$T_PID")" -ge 1 ]] && { erschienen=ja; break; }
  sleep 0.1
done
[[ "$erschienen" == ja ]] && echo "  Strom sichtbar nach ~$(( SECONDS - t0 )) s"
[[ "$erschienen" == ja ]] || laut "Binnen ${FRIST_ERSCHEINEN}s ist KEIN Strom des Prueflings in pw-dump aufgetaucht. Ohne ihn ist alles Weitere gegenstandslos."
chk "3 POSITIVKONTROLLE: der Pruefling haelt einen Stream/Input/Audio" "$erschienen" ja
echo "  Auftragszeile (waehrenddessen): $(namen_roh | tr '\n' '|')"
chk "3 GENAU EIN Strom gehoert dem Pruefling" "$(n_pid "$RT/waehrend.json" "$T_PID")" 1
chk "3 der Kanarienvogel ist weiterhin sichtbar (MESSVERFAHREN)" \
  "$([[ "$(n_pid "$RT/waehrend.json" "$K_PID")" -ge 1 ]] && echo ja || echo nein)" ja

if [[ "$erschienen" != ja ]]; then
  echo; echo "T-3.1: FEHLGESCHLAGEN — ohne Positivkontrolle sagt der Rest nichts."
  exit 1
fi

unser="$(jq -c --argjson p "$T_PID" '[.streams[]|select(.pid==$p)][0]' <"$RT/waehrend.json")"
echo "  unser Knoten: $unser"
NAME="$(jq -r '.name' <<<"$unser")"
echo "  application.name des Prueflings: $NAME"
echo "  Hinweis (NICHT gewertet): remote.name=$(jq -r '.remote // "(fehlt)"' <<<"$unser")"

# --- Parameter, von aussen ----------------------------------------------------
chk "3 Format ist S16LE (int16), gemessen an pw-dump" "$(jq -r '.format' <<<"$unser")" S16LE
chk "3 Abtastrate ist 16000, gemessen an pw-dump" "$(jq -r '.rate' <<<"$unser")" 16000
chk "3 genau ein Kanal, gemessen an pw-dump" "$(jq -r '.channels' <<<"$unser")" 1
chk "3 Kanalbelegung ist MONO, gemessen an pw-dump" "$(jq -r '.position' <<<"$unser")" MONO
chk "3 node.latency ist 512/16000 (Blockgroesse und Rate)" \
  "$(jq -r '.node_latency' <<<"$unser")" "512/16000"
chk "3 node.rate ist 1/16000" "$(jq -r '.node_rate' <<<"$unser")" "1/16000"

# --- Nachtraegliche Auswertung der Auftragszeile fuer die Phase VORHER --------
fremd_vorher="$(n_name_fremd "$RT/vorher.json" "$NAME" "$T_PID")"
[[ "$fremd_vorher" -gt 0 ]] && laut "VORHER trugen $fremd_vorher Stroeme FREMDER Prozesse denselben application.name '$NAME'. Nicht gewertet — ein Name ist keine Identitaet. Aber merken."
chk "3 VORHER trug kein eigener Strom den Namen '$NAME'" \
  "$(n_name_eigen "$RT/vorher.json" "$NAME" "$T_PID")" 0

# --- Parameter aus dem Selbstbericht (Diagnose, nachrangig) -------------------
befehl zustand || true
echo "  zustand(): $(jq -c '.zustand // .fehler' <<<"$A")"
Z="$(jq -c '.zustand // {}' <<<"$A")"
chk "3 zustand().offen ist true" "$(jq -r '.offen' <<<"$Z")" true
chk "3 zustand().rate ist 16000" "$(jq -r '.rate' <<<"$Z")" 16000
chk "3 zustand().kanaele ist 1" "$(jq -r '.kanaele' <<<"$Z")" 1
chk "3 zustand().dtype ist int16" "$(jq -r '.dtype' <<<"$Z")" int16
chk "3 zustand().device ist pipewire (NICHT default)" "$(jq -r '.device' <<<"$Z")" pipewire
chk "3 zustand() kennt einen Blockzaehler (Vertrag)" \
  "$(jq -r 'has("blocks")' <<<"$Z")" true

# --- PIPEWIRE_LATENCY vor dem Import ------------------------------------------
echo "  PIPEWIRE_LATENCY beim Import von sounddevice: $latency_import"
chk "3 PIPEWIRE_LATENCY war beim Import von sounddevice gesetzt" \
  "$([[ "$latency_import" != "(nicht-gesetzt)" && "$latency_import" != "nie-importiert" \
       && "$latency_import" != "?" ]] && echo ja || echo "war_$latency_import")" ja

# =============================================================================
# 4. NACHHER: unmittelbar nach der Rueckkehr von stop(), Prozess lebt noch
# =============================================================================
echo
echo "--- 4. Nachher (Prozess lebt, stop() ist zurueckgekehrt) ---"
befehl stop || { echo "T-3.1: FEHLGESCHLAGEN"; exit 1; }
chk "4 stop() kehrt ohne Fehler zurueck" "$(jq -r '.ok' <<<"$A")" true

# ERSTE Messung sofort -- vor jedem Warten. Sie ist die eigentliche Aussage:
# die Zusage lautet "nach dem Ausschalten", nicht "irgendwann danach".
mess >"$RT/sofort.json"
sofort="$(n_pid "$RT/sofort.json" "$T_PID")"
echo "  Stroeme des Prueflings unmittelbar nach der Rueckkehr von stop(): $sofort"
# Und danach mit Frist nachfassen, damit ein langsames, aber ehrliches
# Aufraeumen nicht an Millisekunden scheitert.
weg=nein; t0=$(date +%s.%N)
while :; do
  mess >"$RT/nachher.json"
  if [[ "$(n_pid "$RT/nachher.json" "$T_PID")" -eq 0 ]]; then weg=ja; break; fi
  jetzt=$(date +%s.%N)
  ueber="$(awk -v a="$jetzt" -v b="$t0" -v g="$FRIST_VERSCHWINDEN" 'BEGIN{print (a-b>g)?1:0}')"
  [[ "$ueber" == 1 ]] && break
  sleep 0.05
done
dauer="$(awk -v a="$(date +%s.%N)" -v b="$t0" 'BEGIN{printf "%.2f", a-b}')"
echo "  Zeit bis der Strom verschwunden war: ${dauer}s (Frist ${FRIST_VERSCHWINDEN}s)"
echo "  Auftragszeile (nachher): $(namen_roh | tr '\n' '|')"

# Die Reihenfolge-Kontrolle: der Prozess MUSS in diesem Moment noch leben.
# Ein toter Prozess gibt alle Objekte ab -- gegen `stop()` statt `close()`
# waere die Messung dann blind.
befehl lebe || true
chk "4 POSITIVKONTROLLE der Messreihenfolge: der Pruefling-Prozess lebt noch" \
  "$(jq -r '.schritt // "tot"' <<<"$A")" lebe
chk "4 POSITIVKONTROLLE: pw-dump sieht in diesem Moment noch Stroeme (Kanarienvogel)" \
  "$([[ "$(n_pid "$RT/nachher.json" "$K_PID")" -ge 1 ]] && echo ja || echo nein)" ja

chk "4 DIE ZUSAGE: kein Strom des Prueflings mehr in pw-dump" \
  "$(n_pid "$RT/nachher.json" "$T_PID")" 0
chk "4 und zwar binnen ${FRIST_VERSCHWINDEN}s nach der Rueckkehr von stop()" "$weg" ja
fremd_nachher="$(n_name_fremd "$RT/nachher.json" "$NAME" "$T_PID")"
[[ "$fremd_nachher" -gt 0 ]] && laut "NACHHER tragen $fremd_nachher Stroeme FREMDER Prozesse denselben Namen '$NAME'. Nicht gewertet."
chk "4 NACHHER traegt kein eigener Strom den Namen '$NAME'" \
  "$(n_name_eigen "$RT/nachher.json" "$NAME" "$T_PID")" 0

befehl zustand || true
Z="$(jq -c '.zustand // {}' <<<"$A")"
echo "  zustand() nach stop(): $Z"
chk "4 zustand().offen ist false (Diagnose, nachrangig)" "$(jq -r '.offen' <<<"$Z")" false

# =============================================================================
# 5. Gegenprobe: "weg" ist ueberhaupt messbar
# =============================================================================
echo
echo "--- 5. Gegenprobe ---"
befehl ende || true
exec 3>&-
gestorben=nein
grenze=$(( SECONDS + 20 ))
while [[ $SECONDS -lt $grenze ]]; do
  kill -0 "$TREIBER" 2>/dev/null || { gestorben=ja; break; }
  sleep 0.1
done
chk "5 der Pruefling-Prozess beendet sich binnen 20 s" "$gestorben" ja
TREIBER=""

kill "$KANARIE" 2>/dev/null
k_weg=nein
grenze=$(( SECONDS + FRIST_ERSCHEINEN ))
while [[ $SECONDS -lt $grenze ]]; do
  mess >"$RT/ende.json"
  [[ "$(n_pid "$RT/ende.json" "$K_PID")" -eq 0 ]] && { k_weg=ja; break; }
  sleep 0.1
done
chk "5 der Kanarienvogel verschwindet nach seinem Ende (Verschwinden ist messbar)" "$k_weg" ja
KANARIE=""

echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) Dass PIPEWIRE_LATENCY VOR dem Import steht, ist an pw-dump nicht"
echo "      sichtbar: node.latency ist auch ohne die Variable 512/16000."
echo "      Gemessen wird die Reihenfolge mit einem Audit-Hook am laufenden"
echo "      Prozess. Dass die Variable etwas BEWIRKT, ist damit nicht belegt."
echo "  (2) device=pipewire gegen device=default ist von aussen nicht sicher zu"
echo "      trennen; das Urteil kommt aus zustand(), also aus dem Selbstbericht."
echo "  (3) Der Plasma-Indikator selbst ist nicht gemessen, nur das Objekt, an"
echo "      dem er haengt."
echo "  (4) Geprueft ist EIN Aus-Zyklus. Dass sich start/stop beliebig oft"
echo "      wiederholen lassen, ohne Objekte zu hinterlassen, ist offen."
echo "  (5) Die Auftragszeile ueber application.name TRENNT HIER NICHTS: der"
echo "      Kanarienvogel traegt denselben Namen wie der Pruefling"
echo "      ('PipeWire ALSA [python3.12]'), weil beide ueber dasselbe"
echo "      ALSA-Plugin gehen. Die namensbasierten Zeilen oben sind deshalb"
echo "      faktisch dieselbe Messung wie die PID-basierten. Sie stehen im"
echo "      Protokoll, tragen aber keine eigene Aussage."
echo "  (6) Die Frist von ${FRIST_VERSCHWINDEN}s nach der Rueckkehr von stop() ist eine"
echo "      Nachsicht, kein Messwert: heute gemessen verschwindet der Knoten"
echo "      nach 0,02 s. Wer sie hochdreht, macht den Verifizierer gegen den"
echo "      Mutanten 'pw-dump-vor-dem-teardown' blind -- belegt: mit 12 s"
echo "      besteht dieser Mutant den Lauf."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-3.1: gruen. Der Strom war da, war genau einer, und war nach der"
  echo "       Rueckkehr von stop() weg -- waehrend der Prozess noch lief."
else
  echo "T-3.1: FEHLGESCHLAGEN"
fi
exit $fail
