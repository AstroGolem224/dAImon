#!/usr/bin/env bash
# Verifizierer fuer T-2.7: Kontextmenue.
#
# Die Zusage, um die es hier geht, ist NICHT "es gibt ein Menue". Sie lautet:
# **das Face darf ausschliesslich abschalten, und ausschliesslich die zwei
# vorgesehenen Ziele.** Alles andere in diesem Task ist Kosmetik daneben.
#
# Warum das die Grenze ist: seit T-1.7 gilt PRODUZENTEN["face"] =
# {"bubble_dismiss"}, mit eingefrorenem Verifizierer und einem Kommentar in
# daimon/common/ipc.py, der woertlich sagt, die Menge duerfe nicht wieder
# wachsen. T-2.7 laesst sie trotzdem wachsen -- um genau ein Recht, und das
# schwaechstmoegliche: `wahrnehmung_aus` mit `{"ziel": "ears"|"eyes"}`.
# Wenn dieses eine Recht falsch geschnitten ist, kann ein Overlay den Hub,
# den Auth-Agenten oder beliebige Units des Nutzers stoppen. Deshalb prueft
# dieses Skript vier Dinge mit Nachdruck:
#
#   1. Das Ziel kommt aus einer Allowlist im Hub, NICHT aus der Nachricht.
#      Gemessen an einer Attrappen-Unit (systemd-run --user, `sleep
#      infinity`), die genau den Namen traegt, den der Hub schalten darf:
#      nach `wahrnehmung_aus {ziel: ears}` ist sie `inactive`.
#   2. Es gibt keinen Weg zum EINSCHALTEN. Weder ein Nachrichtentyp noch ein
#      Steuerbefehl darf eine gestoppte Unit wieder starten.
#   3. Der Steuerbefehl `menu ...` loest die Aktion aus, oeffnet aber KEIN
#      Popup. Ein Steuerkanal, der ein Popup mit Grab aufziehen kann, ist ein
#      Klickfaenger: er naehme Tastatur und Zeiger an sich, ohne dass ein
#      Mensch geklickt hat.
#   4. Andere Produzenten (ears/eyes/kwin) duerfen `wahrnehmung_aus` nicht
#      senden -- geprueft am laufenden Hub, nicht am Quelltext.
#
# JEDER Negativtest hat in DEMSELBEN Lauf einen Positiv-Kanarienvogel. Das ist
# die Lehre aus elf dokumentierten Faellen in HANDOVER.md: ohne
# Positivkontrolle ist "abgewiesen" keine Aussage -- ein Hub, der gar nichts
# empfaengt, weist auch alles ab. Konkret: die Reihenfolge ist
# (a) `ziel=ears` wirkt, (b) die ganze Sperrfeuer-Liste wirkt nicht,
# (c) `ziel=eyes` wirkt -- und (b) wird nach JEDER einzelnen Nachricht
# nachgemessen, nicht bloss am Ende.
#
# KEIN grep im Quelltext fuer die eigentlichen Aussagen. Gelesen werden
# ausschliesslich: der Diagnose-Socket des Face, `systemctl --user is-active`,
# das Verhalten der Hub-Sockets zur Laufzeit, und die Produzententabelle ueber
# einen echten Import (`ipc.pruefe_typ`). Lehre aus T-1.7.v3: ein Builder
# schrieb 'face' statt "face", und die eingefrorene grep-Pruefung schwieg.
# Die einzige Ausnahme ist der Wayland-Mitschnitt (WAYLAND_DEBUG=1) fuer
# `get_popup` -- das ist kein Quelltext, sondern das tatsaechlich ueber die
# Leitung gegangene Protokoll, und es gibt keine andere Stelle, an der ein
# nicht geoeffnetes Popup sichtbar waere.
#
# WAS DIESES SKRIPT NICHT PRUEFT -- ausdruecklich, nicht kaschiert:
#
#   * Kriterium 1 des Plans (Popup ueber `get_popup`, mit Grab und
#     Auto-Dismiss, aus einem echten Rechtsklick) ist hier NICHT geprueft.
#     Der Zeiger laesst sich auf dieser Maschine nicht positionieren
#     (`ydotool mousemove -a` landet immer bei (0,0), relative Bewegungen
#     laufen durch die Zeigerbeschleunigung, 996 px nominell = 3984 px real).
#     Eine koordinatenfreie Suchschleife waere moeglich -- T-2.3 hat sie
#     gebaut -- aber sie wuerde hier ein Popup MIT GRAB aufziehen, und ein
#     Grab, der haengenbleibt, macht die Maschine mit der Maus unbedienbar.
#     Am 27.07. real passiert, ohne Popup. Nachzuholen von Hand:
#       Rechtsklick auf das Pet, dann im Mitschnitt `get_popup` und `.grab(`
#       suchen, Klick daneben -> Popup weg.
#     Was hier stattdessen geprueft wird, ist die gefaehrliche Richtung:
#     dass der STEUERBEFEHL kein Popup aufzieht.
#   * Dass die deaktivierten Eintraege (Ohren an, Augen an, Persona) im Menue
#     SICHTBAR und ausgegraut sind. Der Diagnose-Vertrag aus der
#     Akzeptanzliste hat dafuer kein Feld; geprueft wird nur, dass ihre
#     Ausloesung NICHT wirkt (menu_aktionen bleibt stehen). Sichtbarkeit
#     bleibt offen -- siehe die Liste am Ende des Laufs.
#
# Aufruf:
#   tests/verify/T-2.7.sh                 # gegen den Arbeitsbaum
#   DAIMON_FIXTURE=<baum> tests/verify/T-2.7.sh   # gegen Mutant/Gut-Muster
#     (der Baum muss `face/` UND `daimon/` enthalten -- die Grenze liegt im Hub)
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
BIN="$TARGET/face/target/debug/daimon-face"
MANIFEST="$REPO/face/assets/pet.json"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }
# Ein fehlendes Diagnose-Feld liefert "null", und `$(( null + 1 ))` beendet
# unter `set -u` das ganze Skript -- der Verifizierer waere dann bei einem
# Mutanten mitten im Lauf still gestorben und haette die restlichen Kriterien
# nie gemessen. Deshalb rechnet hier nur, was eine Zahl ist.
plus1() { if [[ "$1" =~ ^[0-9]+$ ]]; then echo $(( $1 + 1 )); else echo "keine_zahl($1)+1"; fi; }

echo "T-2.7 — Kontextmenue: das Face darf nur abschalten, und nur zwei Ziele"
echo "  Baum: $TARGET"

# =============================================================================
# 0. Bauen. Immer.
# =============================================================================
# `cargo test` baut das Bin-Target nicht; der T-1.4-Verifizierer baute nur bei
# fehlendem Binary und startete deshalb einmal ein Binary von vorgestern.
# Fixture-Baeume bauen ueber CARGO_TARGET_DIR ins Temp, sonst schleppt eine
# Fixture-Kopie das Binary der unmutierten Quelle mit.
BAUDIR=""
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then
  BAUDIR="$(mktemp -d)"
  ( cd "$TARGET/face" && CARGO_TARGET_DIR="$BAUDIR" timeout 900 cargo build -p face ) >/dev/null 2>&1
  bau_rc=$?
  BIN="$BAUDIR/debug/daimon-face"
else
  ( cd "$TARGET/face" && timeout 900 cargo build -p face ) >/dev/null 2>&1
  bau_rc=$?
fi
chk "cargo build laeuft durch" "$bau_rc" 0
chk "Binary vorhanden" "$([[ -x "$BIN" ]] && echo ja || echo nein)" ja
if [[ -x "$BIN" ]]; then
  juengste="$(find "$TARGET/face/src" "$TARGET/face/Cargo.toml" -newer "$BIN" 2>/dev/null | head -1)"
  chk "Binary ist nicht aelter als die Quellen" \
    "$([[ -z "$juengste" ]] && echo ja || echo "aelter_als_$juengste")" ja
fi
chk "der Baum bringt ein eigenes daimon/ mit (die Grenze liegt im Hub)" \
  "$([[ -f "$TARGET/daimon/hub/daemon.py" ]] && echo ja || echo nein)" ja

chk "jq vorhanden" "$(command -v jq >/dev/null && echo ja || echo nein)" ja
chk "systemctl --user erreichbar" \
  "$(systemctl --user is-system-running >/dev/null 2>&1; [[ $? -le 1 ]] && echo ja || echo nein)" ja
chk "systemd-run vorhanden" "$(command -v systemd-run >/dev/null && echo ja || echo nein)" ja
chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
if [[ ! -x "$BIN" ]] || ! command -v jq >/dev/null || ! command -v systemd-run >/dev/null \
   || [[ -z "${WAYLAND_DISPLAY:-}" ]] || [[ ! -f "$TARGET/daimon/hub/daemon.py" ]]; then
  echo "  FAIL Voraussetzungen fehlen, Live-Pruefungen uebersprungen"
  rm -rf -- "${BAUDIR:-/nonexistent}"
  exit 1
fi

# =============================================================================
# 1. Die Produzententabelle -- zur Laufzeit, ueber ipc.pruefe_typ
# =============================================================================
echo
echo "--- 1. Produzentengrenze (Laufzeit-Import, kein grep) ---"
# `python -` und `python -c` stellen das AKTUELLE VERZEICHNIS an den Anfang von
# sys.path -- vor PYTHONPATH. Wer den Verifizierer aus dem Repo heraus gegen
# einen Fixture-Baum laufen laesst, misst dann den Hub des Repos und nicht den
# des Baums. Genau das ist hier beim ersten Mutantenlauf passiert: alle
# Kriterien gruen, gemessen wurde die parallel entstehende Implementierung im
# Arbeitsbaum. `-P` unterdrueckt das Voranstellen, DAIMON_BAUM wird zusaetzlich
# ausdruecklich an sys.path[0] gesetzt, und darunter steht die Positivkontrolle,
# die den tatsaechlich geladenen Pfad ausgibt und prueft.
py() { DAIMON_BAUM="$TARGET" PYTHONDONTWRITEBYTECODE=1 "$PY" -P "$@"; }
tab="$(py - 2>/dev/null <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["DAIMON_BAUM"])
from daimon.common import ipc
# ipc.py liegt in <baum>/daimon/common/ -- also DREI Ebenen hoch bis zur
# Baumwurzel. Mit zwei Ebenen kam <baum>/daimon heraus, und die
# Positivkontrolle konnte nie gruen werden: jeder Lauf rot, damit auch
# jeder Mutantenlauf, und jede Mutante waere als "erkannt" durchgegangen,
# ohne dass ihre Mutation je gemessen wurde.
print("geladen_aus=" + os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(ipc.__file__)))))

def darf(p, t):
    try:
        ipc.pruefe_typ(p, t)
        return True
    except Exception:
        return False

face = set(ipc.PRODUZENTEN.get("face", ()))
print("face_menge_exakt=" + ("ja" if face == {"bubble_dismiss", "wahrnehmung_aus"} else "nein"))
print("face_menge_ist=" + ",".join(sorted(face)))
print("face_darf_wahrnehmung_aus=" + ("ja" if darf("face", "wahrnehmung_aus") else "nein"))
print("face_darf_bubble_dismiss=" + ("ja" if darf("face", "bubble_dismiss") else "nein"))
for verboten in ("intent_mark", "freigabe", "hook", "utterance", "screen", "window"):
    print(f"face_darf_kein_{verboten}=" + ("nein" if darf("face", verboten) else "ja"))

# Kein anderer Produzent darf abschalten.
fremd = [p for p in ipc.PRODUZENTEN if p != "face" and darf(p, "wahrnehmung_aus")]
print("nur_face_darf_abschalten=" + ("ja" if not fremd else "nein:" + ",".join(fremd)))

# Es gibt nirgends einen Einschalt-Typ. Die Liste ist eine Namensvermutung und
# beweist fuer sich genommen nichts -- der harte Beleg ist die Verhaltensprobe
# weiter unten (Abschnitt 5). Hier faellt nur auf, wenn jemand das Gegenstueck
# offensichtlich benannt eingebaut hat.
ein = ("wahrnehmung_an", "wahrnehmung_ein", "wahrnehmung_start", "perception_on",
       "unit_start", "unit_starten", "ears_an", "eyes_an")
treffer = [f"{p}:{t}" for p in ipc.PRODUZENTEN for t in ein if darf(p, t)]
print("kein_einschalt_typ=" + ("ja" if not treffer else "nein:" + ",".join(treffer)))
PYEOF
)"
w() { grep -m1 "^$1=" <<<"$tab" | cut -d= -f2-; }
echo "  daimon geladen aus: $(w geladen_aus)"
chk "1 POSITIVKONTROLLE: der Hub kommt aus DEM GEPRUEFTEN BAUM" \
  "$(w geladen_aus)" "$(cd "$TARGET" && pwd)"
echo "  face darf: $(w face_menge_ist)"
# Positivkontrolle zuerst. Ohne sie sagen die Verbote darunter nichts: eine
# Tabelle, die gar nicht geladen wurde, verbietet ebenfalls alles.
chk "face darf wahrnehmung_aus (POSITIVKONTROLLE)" "$(w face_darf_wahrnehmung_aus)" ja
chk "face darf weiterhin bubble_dismiss (POSITIVKONTROLLE)" "$(w face_darf_bubble_dismiss)" ja
chk "face-Menge ist GENAU {bubble_dismiss, wahrnehmung_aus}" "$(w face_menge_exakt)" ja
for verboten in intent_mark freigabe hook utterance screen window; do
  chk "face darf kein $verboten (T-1.7 haelt)" "$(w "face_darf_kein_$verboten")" ja
done
chk "kein anderer Produzent darf wahrnehmung_aus" "$(w nur_face_darf_abschalten)" ja
chk "kein benannter Einschalt-Typ in der Tabelle" "$(w kein_einschalt_typ)" ja

# =============================================================================
# 2. Welche Units darf der Hub schalten? -- am Prueflings-Baum erfragt
# =============================================================================
# Die Attrappe muss GENAU den Namen tragen, den der Hub fuer "ears" bzw.
# "eyes" schalten wuerde. Sonst misst dieser Verifizierer, dass irgendeine
# Unit ueberlebt hat, waehrend eine andere starb.
#
# Ermittelt wird der Name zur Laufzeit aus dem Baum (Modul-Attribute und
# Hub-Instanz), nicht per grep. Findet sich nichts, gelten die Namen aus dem
# Plan (daimon-ears.service / daimon-eyes.service). Ueberschreibbar ueber
# DAIMON_T27_EARS_UNIT / DAIMON_T27_EYES_UNIT.
echo
echo "--- 2. Allowlist des Hubs (Laufzeit-Introspektion) ---"
entdeckt="$(py - 2>/dev/null <<'PYEOF'
import importlib, os, pkgutil, sys
sys.path.insert(0, os.environ["DAIMON_BAUM"])

kandidaten = {}

def einsammeln(obj, tiefe=0):
    if tiefe > 2 or not isinstance(obj, dict):
        return
    schluessel = {str(k) for k in obj}
    if {"ears", "eyes"} <= schluessel:
        werte = {str(k): obj[k] for k in obj}
        if all(isinstance(v, str) for v in werte.values()):
            kandidaten.setdefault("treffer", werte)

module = ["daimon.common.ipc", "daimon.common.config", "daimon.hub.daemon",
          "daimon.hub.state", "daimon.hub.bus", "daimon.hub.diag"]
try:
    import daimon.hub
    module += [f"daimon.hub.{m.name}" for m in pkgutil.iter_modules(daimon.hub.__path__)]
except Exception:
    pass
for name in dict.fromkeys(module):
    try:
        m = importlib.import_module(name)
    except Exception:
        continue
    for attr in dir(m):
        if attr.startswith("__"):
            continue
        try:
            einsammeln(getattr(m, attr))
        except Exception:
            pass

# Auch eine Hub-Instanz befragen: die Allowlist darf aus der Konfiguration
# kommen und dann nur am Objekt haengen.
try:
    import tempfile
    from pathlib import Path
    from daimon.hub.daemon import Hub
    h = Hub(runtime_dir=Path(tempfile.mkdtemp()))
    for attr in dir(h):
        if attr.startswith("_"):
            continue
        try:
            einsammeln(getattr(h, attr))
        except Exception:
            pass
except Exception:
    pass

t = kandidaten.get("treffer")
if t:
    print("gefunden=ja")
    print("ears=" + t["ears"])
    print("eyes=" + t["eyes"])
    print("anzahl=" + str(len(t)))
    print("alle=" + ",".join(f"{k}:{v}" for k, v in sorted(t.items())))
else:
    print("gefunden=nein")
PYEOF
)"
e() { grep -m1 "^$1=" <<<"$entdeckt" | cut -d= -f2-; }
if [[ "$(e gefunden)" == ja ]]; then
  echo "  Allowlist im Baum gefunden: $(e alle)"
  EARS_UNIT="$(e ears)"; EYES_UNIT="$(e eyes)"
  chk "die Allowlist hat genau zwei Eintraege" "$(e anzahl)" 2
else
  echo "  keine Allowlist zur Laufzeit gefunden -- es gelten die Namen aus dem Plan"
  EARS_UNIT="daimon-ears.service"; EYES_UNIT="daimon-eyes.service"
fi
EARS_UNIT="${DAIMON_T27_EARS_UNIT:-$EARS_UNIT}"
EYES_UNIT="${DAIMON_T27_EYES_UNIT:-$EYES_UNIT}"
[[ "$EARS_UNIT" == *.service ]] || EARS_UNIT="$EARS_UNIT.service"
[[ "$EYES_UNIT" == *.service ]] || EYES_UNIT="$EYES_UNIT.service"
FREMD_UNIT="daimon-t27-fremd.service"
echo "  Attrappen: ears -> $EARS_UNIT, eyes -> $EYES_UNIT, Kontrolle -> $FREMD_UNIT"

# Die Allowlist darf niemals auf eine der tragenden Units zeigen. Das ist kein
# Umgebungsproblem, sondern genau der Fehler, den dieser Task verhindern soll.
#
# Abgebrochen wird nur, wenn GENAU DIESE Pruefungen scheitern -- ein Fehler
# weiter oben (etwa eine noch fehlende Produzentenzeile) darf den Rest des
# Laufs nicht verschlucken, sonst sieht man bei einem Mutanten nur den ersten
# Befund und nie, ob die Grenze selbst haelt.
fail_vor_attrappen="$fail"
for tabu in daimon-hub.service daimon-auth.service daimon-hookbridge.service \
            daimon-face.service daimon-focus.service; do
  chk "die Allowlist zeigt nicht auf $tabu" \
    "$([[ "$EARS_UNIT" == "$tabu" || "$EYES_UNIT" == "$tabu" ]] && echo nein || echo ja)" ja
done
chk "ears- und eyes-Unit sind verschieden" \
  "$([[ "$EARS_UNIT" != "$EYES_UNIT" ]] && echo ja || echo nein)" ja
# Eine Attrappe darf keine echte, installierte Unit ueberschreiben.
for u in "$EARS_UNIT" "$EYES_UNIT" "$FREMD_UNIT"; do
  vorhanden="$(systemctl --user cat "$u" >/dev/null 2>&1 && echo ja || echo nein)"
  chk "es gibt keine echte Unit namens $u (sonst waere die Attrappe gefaehrlich)" "$vorhanden" nein
done
if [[ "$fail" -ne "$fail_vor_attrappen" ]]; then
  echo "  FAIL Vorbedingungen fuer die Attrappen nicht erfuellt -- Abbruch VOR jedem systemctl stop"
  rm -rf -- "${BAUDIR:-/nonexistent}"
  exit 1
fi

# =============================================================================
# 3. Attrappen anlegen, Aufraeumen einrichten
# =============================================================================
TMPS=(); HUB=""; FACE=""
HUB_ECHT_LIEF="$(systemctl --user is-active daimon-hub.service 2>/dev/null)"
aufraeumen() {
  [[ -n "$FACE" ]] && kill "$FACE" 2>/dev/null
  [[ -n "$HUB"  ]] && kill "$HUB"  2>/dev/null
  for u in "$EARS_UNIT" "$EYES_UNIT" "$FREMD_UNIT"; do
    systemctl --user stop "$u" >/dev/null 2>&1
    systemctl --user reset-failed "$u" >/dev/null 2>&1
  done
  # Falls der Pruefling die ECHTE Hub-Unit erwischt hat: wieder hochfahren.
  # Das ist dann ein Befund, kein Aufraeumen -- aber die Sitzung des Nutzers
  # darf ein Verifizierer nicht kaputt zuruecklassen.
  if [[ "$HUB_ECHT_LIEF" == active ]] \
     && [[ "$(systemctl --user is-active daimon-hub.service 2>/dev/null)" != active ]]; then
    echo "  ACHTUNG: daimon-hub.service lief vorher und laeuft jetzt nicht -- wird neu gestartet"
    systemctl --user start daimon-hub.service >/dev/null 2>&1
  fi
  for t in "${TMPS[@]:-}"; do [[ -n "$t" ]] && rm -rf -- "$t"; done
  rm -rf -- "${BAUDIR:-/nonexistent}"
}
trap aufraeumen EXIT INT TERM

attrappe_starten() {  # $1 = Unitname
  systemctl --user reset-failed "$1" >/dev/null 2>&1
  systemd-run --user --unit="${1%.service}" \
    --description="T-2.7 Attrappe (Verifizierer)" \
    /usr/bin/sleep infinity >/dev/null 2>&1
}
zustand() { systemctl --user is-active "$1" 2>/dev/null; }
warte_auf() {  # $1 = Unit, $2 = Sollzustand, $3 = Sekunden
  local i grenze; grenze="$(awk -v s="$3" 'BEGIN{print int(s*10)}')"
  for i in $(seq 1 "$grenze"); do
    [[ "$(zustand "$1")" == "$2" ]] && return 0
    sleep 0.1
  done
  return 1
}

echo
echo "--- 3. Attrappen-Units ---"
fail_vor_start="$fail"
for u in "$EARS_UNIT" "$EYES_UNIT" "$FREMD_UNIT"; do
  attrappe_starten "$u"
  warte_auf "$u" active 5
  chk "Attrappe $u laeuft (POSITIVKONTROLLE)" "$(zustand "$u")" active
done
if [[ "$fail" -ne "$fail_vor_start" ]]; then
  echo "  FAIL Attrappen liessen sich nicht starten -- ohne sie misst nichts hier etwas"
  exit 1
fi

# =============================================================================
# 4. Der Hub laeuft, und alle Produzentensockets sind offen
# =============================================================================
# Der mitgelieferte Daemon oeffnet nur hookbridge/face/auth. Fuer die Frage
# "darf ears das auch?" braucht es die anderen Sockets, deshalb wird die
# Hub-KLASSE des Prueflings mit voller Produzentenliste gestartet -- Produktcode,
# nur mit anderem Startparameter.
RT="$(mktemp -d -p "/run/user/$(id -u)")"; TMPS+=("$RT")
LOGS="$(mktemp -d)"; TMPS+=("$LOGS")
py - "$RT/rt" >"$LOGS/hub.log" 2>&1 <<'PYEOF' &
import os, sys, threading
sys.path.insert(0, os.environ["DAIMON_BAUM"])
from pathlib import Path
from daimon.hub.daemon import Hub
print("hub geladen aus:", Hub.__module__, sys.modules["daimon.hub.daemon"].__file__, flush=True)
h = Hub(runtime_dir=Path(sys.argv[1]))
h.start(["hookbridge", "face", "auth", "ears", "eyes", "kwin"])
threading.Event().wait()
PYEOF
HUB=$!
for _ in $(seq 1 100); do [[ -S "$RT/rt/face.sock" && -S "$RT/rt/ears.sock" ]] && break; sleep 0.1; done
echo
echo "--- 4. Hub ---"
chk "Hub laeuft und face.sock ist da (POSITIVKONTROLLE)" \
  "$([[ -S "$RT/rt/face.sock" ]] && echo ja || echo nein)" ja
hub_datei="$(sed -n 's/^hub geladen aus: [^ ]* //p' "$LOGS/hub.log" | head -1)"
echo "  laufender Hub: ${hub_datei:-(unbekannt)}"
chk "4 POSITIVKONTROLLE: der LAUFENDE Hub stammt aus dem geprueften Baum" \
  "$([[ -n "$hub_datei" && "$hub_datei" == "$(cd "$TARGET" && pwd)"/* ]] && echo ja || echo nein)" ja
chk "ears/eyes/kwin-Sockets sind offen (POSITIVKONTROLLE fuer Abschnitt 6)" \
  "$([[ -S "$RT/rt/ears.sock" && -S "$RT/rt/eyes.sock" && -S "$RT/rt/kwin.sock" ]] && echo ja || echo nein)" ja
if [[ ! -S "$RT/rt/face.sock" ]]; then
  echo "  FAIL Hub startet nicht -- Protokoll:"; tail -20 "$LOGS/hub.log"
  exit 1
fi

# Sendet eine Zeile an einen Produzentensocket und meldet, was der Hub mit der
# VERBINDUNG macht: `eof` = abgewiesen (der Hub schliesst bei falschem Typ),
# `offen` = angenommen. Das ist die einzige Rueckmeldung, die es gibt -- die
# Produzentensockets antworten nicht.
sende() {  # $1 = Socketpfad, $2 = JSON-Zeile
  "$PY" - "$1" "$2" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5)
try:
    c.connect(sys.argv[1])
    c.sendall(sys.argv[2].encode() + b"\n")
except OSError as exc:
    print(f"fehler:{exc}")
    raise SystemExit
c.settimeout(2.0)
try:
    print("eof" if c.recv(1) == b"" else "antwort")
except socket.timeout:
    print("offen")
except OSError:
    print("eof")
finally:
    c.close()
PYEOF
}
nachricht() { printf '{"v":1,"type":"%s","payload":%s}' "$1" "$2"; }

# =============================================================================
# 5. DIE GRENZE: nur abschalten, und nur die zwei Ziele
# =============================================================================
echo
echo "--- 5. Die Grenze: Allowlist statt Nachricht ---"

# (a) Kanarienvogel 1: das erlaubte Ziel wirkt.
a1="$(sende "$RT/rt/face.sock" "$(nachricht wahrnehmung_aus '{"ziel":"ears"}')")"
echo "  wahrnehmung_aus{ziel:ears} -> Verbindung: $a1"
warte_auf "$EARS_UNIT" inactive 15
chk "5a KANARIENVOGEL: ziel=ears schaltet die Attrappe ab" "$(zustand "$EARS_UNIT")" inactive
chk "5a dabei bleibt die eyes-Attrappe unberuehrt" "$(zustand "$EYES_UNIT")" active
chk "5a dabei bleibt die fremde Unit unberuehrt" "$(zustand "$FREMD_UNIT")" active

# (b) Das Sperrfeuer -- gegen einen ZWEITEN Hub, dessen `systemctl` ein Stub ist.
#
# Warum dieser Umbau: die Liste unten enthaelt `"*"`. Gegen die richtige
# Umsetzung ist das folgenlos, weil `ziel` in einer Allowlist nachgeschlagen
# wird -- und genau das soll hier bewiesen werden. Gegen den Mutanten
# `ziel-aus-der-nachricht`, der den Namen aus der Nachricht nimmt, wurde daraus
# `systemctl --user stop '*'`. systemd versteht Globs. Das stoppt JEDE Unit der
# Sitzung, `plasma-kwin_wayland` eingeschlossen.
#
# Am 02.08.2026 hat dieser Verifizierer damit dreimal den Desktop abgeraeumt:
# alle Bildschirme schwarz, kein Zeiger, kein Terminal, nur noch Strg-Alt-Entf.
# Im Journal steht es als geordneter Reboot mit `basic.target has 'stop' job
# queued` -- kein Absturz, ein Selbstmord per systemctl.
#
# Die Lehre, und sie gilt ueber diesen Task hinaus: ANGRIFFSNUTZLASTEN GEHOEREN
# BEOBACHTET, NICHT AUSGEFUEHRT. Ein Mutant ist dazu da, einem Angriff zu
# gehorchen -- also darf der Angriff nichts anrichten koennen.
#
# Deshalb laeuft das Sperrfeuer gegen einen eigenen Hub, in dessen PATH ein
# `systemctl` liegt, das seine Argumente in eine Datei schreibt und sonst
# nichts tut. Der Pruefling ruft `["systemctl", ...]` ohne absoluten Pfad auf,
# der Stub greift also. Gemessen wird, was der Hub TUN WOLLTE. Das ist
# strenger als vorher, nicht schwaecher: vorher war ein Treffer nur an einer
# tatsaechlich gestoppten Unit zu sehen, jetzt an jedem einzelnen Aufruf.
echo "  -- Sperrfeuer: Ziele, die nicht in der Allowlist stehen"
# Die Listen. Sie duerfen jetzt boesartiger sein als vorher, weil gegen den
# Stub kein Aufruf mehr ausgefuehrt wird: `*` und der Compositor stehen
# ausdruecklich drin, denn genau die beiden waren am 02.08. der Schaden.
boese=(
  '"hub"' '"auth"' '"face"' '"kwin"' '"hookbridge"'
  "\"$EARS_UNIT\"" "\"$EYES_UNIT\""
  "\"$FREMD_UNIT\"" '"daimon-t27-fremd"'
  "\"../$FREMD_UNIT\"" "\"/etc/systemd/user/$FREMD_UNIT\""
  '"EARS"' '"Ears"' '" ears"' '"ears "' '"ears eyes"' '"ears;eyes"'
  '"ears hub"' '"*"' '"*.service"' '""' '"ohren"' '"alle"'
  '42' 'null' 'true' '["ears"]' '{"ziel":"ears"}'
)
# Die echten, laufenden Units dieser Sitzung -- der Fall, um den es geht.
boese_echt=(
  '"daimon-hub.service"' '"daimon-auth.service"' '"daimon-hookbridge.service"'
  '"daimon-focus.service"'
  '"plasma-kwin_wayland.service"' '"basic.target"' '"graphical-session.target"'
)
STUB="$(mktemp -d)"; TMPS+=("$STUB")
STUBLOG="$STUB/aufrufe.log"
: >"$STUBLOG"
{
  echo '#!/usr/bin/env bash'
  echo "# Stub fuer T-2.7: protokolliert und fuehrt NICHTS aus."
  echo "printf '%s\n' \"\$*\" >> '$STUBLOG'"
  echo "exit 0"
} >"$STUB/systemctl"
chmod +x "$STUB/systemctl"

RT2="$(mktemp -d -p "/run/user/$(id -u)")"; TMPS+=("$RT2")
env PATH="$STUB:$PATH" DAIMON_BAUM="$TARGET" PYTHONDONTWRITEBYTECODE=1 \
  "$PY" -P - "$RT2/rt" >"$LOGS/hub-stub.log" 2>&1 <<'STUBHUB' &
import os, sys, threading
sys.path.insert(0, os.environ["DAIMON_BAUM"])
from pathlib import Path
from daimon.hub.daemon import Hub
h = Hub(runtime_dir=Path(sys.argv[1]))
h.start(["hookbridge", "face", "auth", "ears", "eyes", "kwin"])
threading.Event().wait()
STUBHUB
HUB2=$!
for _ in $(seq 1 100); do [[ -S "$RT2/rt/face.sock" ]] && break; sleep 0.1; done
chk "5b Stub-Hub laeuft (POSITIVKONTROLLE)" \
  "$([[ -S "$RT2/rt/face.sock" ]] && echo ja || echo nein)" ja

zeilen() { wc -l <"$STUBLOG" | tr -d " "; }

# Kanarienvogel VOR dem Sperrfeuer: der Stub muss ueberhaupt erreichbar sein.
# Ohne ihn waere "der Stub hat nichts protokolliert" die Nullaussage schlechthin
# -- ein Hub, den keine Nachricht erreicht, ruft ebenfalls nie systemctl auf.
sende "$RT2/rt/face.sock" "$(nachricht wahrnehmung_aus '{"ziel":"ears"}')" >/dev/null
sleep 0.6
echo "  Stub-Protokoll nach ziel=ears: $(tr '\n' '|' <"$STUBLOG")"
chk "5b KANARIENVOGEL: das erlaubte Ziel erreicht systemctl" \
  "$(grep -c -- "$EARS_UNIT" "$STUBLOG")" 1

sperrfeuer_ok=ja
feuern() {  # $1 = JSON-Wert fuer ziel
  local vorher nachher neu
  vorher="$(zeilen)"
  sende "$RT2/rt/face.sock" "$(nachricht wahrnehmung_aus "{\"ziel\":$1}")" >/dev/null
  sleep 0.35
  nachher="$(zeilen)"
  if [[ "$nachher" != "$vorher" ]]; then
    neu="$(tail -n +$((vorher + 1)) "$STUBLOG" | tr '\n' '|')"
    echo "  FAIL ziel=$1 haette systemctl aufgerufen: $neu"
    sperrfeuer_ok=nein
  fi
}
for z in "${boese[@]}"; do feuern "$z"; done
chk "5b ${#boese[@]} unerlaubte Ziele erreichen systemctl NICHT" "$sperrfeuer_ok" ja

# Die echten Unit-Namen dieser Sitzung koennen jetzt BEDINGUNGSLOS gesendet
# werden. Vorher hingen sie an einem Vorbehalt, weil ein Treffer den laufenden
# Hub des Nutzers abgeraeumt haette; gegen den Stub kostet ein Treffer nichts
# ausser einer Zeile im Protokoll.
for z in "${boese_echt[@]}"; do feuern "$z"; done
chk "5b auch die ${#boese_echt[@]} echten Unit-Namen erreichen systemctl NICHT" \
  "$sperrfeuer_ok" ja

# Gegenprobe am Ende: der Stub hat NUR die erlaubte Attrappe gesehen.
fremd_im_log="$(grep -vc -- "$EARS_UNIT" "$STUBLOG")"
chk "5b im Stub-Protokoll steht kein fremder Aufruf" "${fremd_im_log:-0}" 0
chk "5b der Stub-Hub lebt nach dem Sperrfeuer noch" \
  "$(kill -0 "$HUB2" 2>/dev/null && echo ja || echo nein)" ja
kill "$HUB2" 2>/dev/null; wait "$HUB2" 2>/dev/null

# Zweites Netz, unabhaengig vom Stub: die echten Attrappen und der Hub des
# Nutzers stehen unveraendert da. Kostet nichts und faengt den Fall, dass ein
# Pruefling systemctl mit absolutem Pfad an PATH vorbei aufruft.
chk "5b die ears-Attrappe steht unveraendert" "$(zustand "$EARS_UNIT")" inactive
chk "5b die eyes-Attrappe steht unveraendert" "$(zustand "$EYES_UNIT")" active
chk "5b die fremde Unit steht unveraendert" "$(zustand "$FREMD_UNIT")" active
chk "5b der Hub des Nutzers laeuft unveraendert weiter" \
  "$(systemctl --user is-active daimon-hub.service 2>/dev/null)" "$HUB_ECHT_LIEF"
chk "5b der Hub dieses Laufs lebt noch" \
  "$(kill -0 "$HUB" 2>/dev/null && echo ja || echo nein)" ja

# (c) Kanarienvogel 2: im SELBEN Lauf geht ein erlaubtes Ziel durch. Ohne das
#     hier bewiese Abschnitt (b) gar nichts -- ein Hub, der die Nachrichten
#     nie sieht, laesst ebenfalls alle Attrappen laufen.
a2="$(sende "$RT/rt/face.sock" "$(nachricht wahrnehmung_aus '{"ziel":"eyes"}')")"
echo "  wahrnehmung_aus{ziel:eyes} -> Verbindung: $a2"
warte_auf "$EYES_UNIT" inactive 15
chk "5c KANARIENVOGEL: ziel=eyes wirkt NACH dem Sperrfeuer noch" "$(zustand "$EYES_UNIT")" inactive
chk "5c die fremde Unit hat den ganzen Abschnitt ueberlebt" "$(zustand "$FREMD_UNIT")" active

# =============================================================================
# 6. Andere Produzenten duerfen nicht abschalten -- am laufenden Hub
# =============================================================================
echo
echo "--- 6. wahrnehmung_aus von fremden Produzenten ---"
attrappe_starten "$EARS_UNIT"; warte_auf "$EARS_UNIT" active 5
chk "6 Attrappe wieder gestartet (POSITIVKONTROLLE, und 'inactive' ist umkehrbar)" \
  "$(zustand "$EARS_UNIT")" active
# Positivkontrolle des Messverfahrens: ein ERLAUBTER Typ auf demselben Socket
# haelt die Verbindung offen. Ohne diese Zeile waere "eof" kein Beweis fuer
# eine Abweisung -- es koennte auch heissen, dass der Socket generell schliesst.
p_ears="$(sende "$RT/rt/ears.sock" "$(nachricht utterance '{"text":"hallo"}')")"
chk "6 POSITIVKONTROLLE: erlaubter Typ auf ears.sock haelt die Verbindung" "$p_ears" offen
for prod in ears eyes kwin auth hookbridge; do
  antwort="$(sende "$RT/rt/$prod.sock" "$(nachricht wahrnehmung_aus '{"ziel":"ears"}')")"
  chk "6 $prod.sock weist wahrnehmung_aus ab (Verbindung ab)" "$antwort" eof
done
sleep 1.0
chk "6 keine dieser Nachrichten hat die Attrappe geschaltet" "$(zustand "$EARS_UNIT")" active

# =============================================================================
# 7. Es gibt keinen Weg zum Einschalten -- ueber den Hub
# =============================================================================
echo
echo "--- 7. Kein Einschalten ueber eine Hub-Nachricht ---"
# Erst wieder abschalten, damit "bleibt aus" ueberhaupt messbar ist.
sende "$RT/rt/face.sock" "$(nachricht wahrnehmung_aus '{"ziel":"ears"}')" >/dev/null
warte_auf "$EARS_UNIT" inactive 15
chk "7 Ausgangslage: die Attrappe ist aus (POSITIVKONTROLLE)" "$(zustand "$EARS_UNIT")" inactive
ein_versuche=(
  'wahrnehmung_an {"ziel":"ears"}'
  'wahrnehmung_ein {"ziel":"ears"}'
  'wahrnehmung_start {"ziel":"ears"}'
  'unit_start {"ziel":"ears"}'
  'perception_on {"ziel":"ears"}'
  'wahrnehmung_aus {"ziel":"ears","an":true}'
  'wahrnehmung_aus {"ziel":"ears","aus":false}'
  'wahrnehmung_aus {"ziel":"ears","zustand":"start"}'
  'wahrnehmung_aus {"ziel":"ears","aktion":"start"}'
)
for v in "${ein_versuche[@]}"; do
  typ="${v%% *}"; nutzlast="${v#* }"
  sende "$RT/rt/face.sock" "$(nachricht "$typ" "$nutzlast")" >/dev/null
done
sleep 2.0
chk "7 keine dieser ${#ein_versuche[@]} Nachrichten startet die Unit wieder" \
  "$(zustand "$EARS_UNIT")" inactive

# =============================================================================
# 8. Das Face: Steuerbefehl wirkt, oeffnet aber kein Popup
# =============================================================================
echo
echo "--- 8. Face: menu-Steuerbefehl ---"
attrappe_starten "$EARS_UNIT"; warte_auf "$EARS_UNIT" active 5
attrappe_starten "$EYES_UNIT"; warte_auf "$EYES_UNIT" active 5
chk "8 beide Attrappen laufen wieder (POSITIVKONTROLLE)" \
  "$([[ "$(zustand "$EARS_UNIT")" == active && "$(zustand "$EYES_UNIT")" == active ]] && echo ja || echo nein)" ja

DAIMON_MAX_SECS=180 WAYLAND_DEBUG=1 "$BIN" \
  --pet-manifest "$MANIFEST" --sprite-position 900,500 \
  --hub-socket "$RT/rt/events.sock" \
  --diag-socket "$RT/d.sock" --control-socket "$RT/c.sock" \
  >"$LOGS/face.log" 2>"$LOGS/wl.log" &
FACE=$!
for _ in $(seq 1 250); do [[ -S "$RT/c.sock" && -S "$RT/d.sock" ]] && break; sleep 0.1; done
sleep 1.5
chk "8 Overlay startet" "$([[ -S "$RT/d.sock" && -S "$RT/c.sock" ]] && echo ja || echo nein)" ja
if [[ ! -S "$RT/c.sock" ]]; then
  echo "  FAIL Overlay startet nicht -- Protokoll:"; tail -20 "$LOGS/face.log"
  exit 1
fi

diag() { "$PY" - "$RT/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
ctl() { "$PY" - "$RT/c.sock" "$1" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
c.sendall(sys.argv[2].encode() + b"\n")
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
# `jq -r '.feld // empty'` behandelt false wie null -- bei menu_offen ist das
# genau der Wert, um den es geht. Deshalb ohne `// empty`.
feld() { jq -r ".$1" <<<"$(diag)"; }

d0="$(diag)"
echo "  Diagnose: $d0"
for f in menu_offen menu_aktionen letzte_menu_aktion; do
  chk "8 Diagnose kennt das Feld $f (Vertrag)" \
    "$(jq -e "has(\"$f\")" <<<"$d0" >/dev/null 2>&1 && echo ja || echo nein)" ja
done
chk "8 menu_offen ist beim Start false" "$(jq -r '.menu_offen' <<<"$d0")" false

# --- 8a. Der Befehl wirkt -----------------------------------------------------
akt_vor="$(jq -r '.menu_aktionen' <<<"$d0")"
antwort="$(ctl 'menu ears_aus')"
chk "8a der Steuerbefehl 'menu ears_aus' wird bestaetigt" "$antwort" ok
warte_auf "$EARS_UNIT" inactive 15
chk "8a KANARIENVOGEL: die ears-Attrappe ist aus" "$(zustand "$EARS_UNIT")" inactive
chk "8a die eyes-Attrappe blieb an" "$(zustand "$EYES_UNIT")" active
akt_nach="$(feld menu_aktionen)"
echo "  menu_aktionen $akt_vor -> $akt_nach"
chk "8a menu_aktionen zaehlt genau eins hoch" "$akt_nach" "$(plus1 "$akt_vor")"
chk "8a letzte_menu_aktion ist ears_aus" "$(feld letzte_menu_aktion)" ears_aus

# --- 8b. Der Befehl oeffnet KEIN Popup ----------------------------------------
# Ein Steuerkanal, der ein Popup mit Grab aufziehen kann, ist ein Klickfaenger.
# Zweifach gemessen: am Diagnose-Feld ueber 2 s (ein Popup, das aufgeht und
# sofort wieder zu ist, waere ebenfalls ein Griff nach dem Zeiger) und am
# Wayland-Mitschnitt, wo ein `get_popup` stehen wuerde.
offen_gesehen=nein
for _ in $(seq 1 20); do
  [[ "$(feld menu_offen)" == "true" ]] && offen_gesehen=ja
  sleep 0.1
done
chk "8b menu_offen bleibt bei 'menu ears_aus' durchgehend false" "$offen_gesehen" nein
chk "8b Wayland-Mitschnitt ist aktiv (POSITIVKONTROLLE)" \
  "$(grep -q 'zwlr_layer_shell_v1@' "$LOGS/wl.log" && echo ja || echo nein)" ja
popups="$(grep -c 'get_popup' "$LOGS/wl.log")"
grabs="$(grep -cE '\.grab\(|xdg_popup@' "$LOGS/wl.log")"
echo "  Mitschnitt: get_popup=$popups, Popup/Grab-Zeilen=$grabs"
chk "8b kein get_popup im ganzen Steuerbefehl-Lauf" "$popups" 0
chk "8b kein Popup-Grab im ganzen Steuerbefehl-Lauf" "$grabs" 0

# --- 8c. Was nicht wirken darf, zaehlt auch nicht -----------------------------
# Die deaktivierten Eintraege: Ohren an, Augen an, Persona wechseln. Eine
# Aktion, die nicht wirken darf, darf menu_aktionen nicht hochzaehlen.
echo "  -- deaktivierte Eintraege und Einschaltversuche ueber den Steuerkanal"
akt_vor="$(feld menu_aktionen)"
letzte_vor="$(feld letzte_menu_aktion)"
tote_befehle=(
  'menu ears_an' 'menu eyes_an' 'menu ohren_an' 'menu augen_an'
  'menu ears_ein' 'menu wahrnehmung_an' 'menu persona' 'menu persona_wechseln'
  'menu persona naechste' 'menu ears_aus extra' 'menu' 'menu ' 'menu hub_aus'
  'menu daimon-hub.service' 'wahrnehmung an' 'unit start daimon-ears'
)
for b in "${tote_befehle[@]}"; do ctl "$b" >/dev/null; done
sleep 1.5
chk "8c ${#tote_befehle[@]} unwirksame Befehle zaehlen menu_aktionen NICHT hoch" \
  "$(feld menu_aktionen)" "$akt_vor"
chk "8c letzte_menu_aktion blieb unveraendert" "$(feld letzte_menu_aktion)" "$letzte_vor"
chk "8c keiner davon hat die Attrappe wieder gestartet" "$(zustand "$EARS_UNIT")" inactive
chk "8c das Face lebt noch" "$(kill -0 "$FACE" 2>/dev/null && echo ja || echo nein)" ja
# Und der Kanarienvogel dahinter: der Kanal funktioniert unveraendert.
chk "8c KANARIENVOGEL: 'menu eyes_aus' wird bestaetigt" "$(ctl 'menu eyes_aus')" ok
warte_auf "$EYES_UNIT" inactive 15
chk "8c KANARIENVOGEL: die eyes-Attrappe ist aus" "$(zustand "$EYES_UNIT")" inactive
chk "8c menu_aktionen zaehlt jetzt sehr wohl hoch" "$(feld menu_aktionen)" "$(plus1 "$akt_vor")"
chk "8c letzte_menu_aktion ist eyes_aus" "$(feld letzte_menu_aktion)" eyes_aus

# --- 8d. Popup-Freiheit gilt fuer den ganzen Lauf -----------------------------
popups="$(grep -c 'get_popup' "$LOGS/wl.log")"
chk "8d auch nach allen Steuerbefehlen: kein get_popup" "$popups" 0

# --- 8e. Beenden --------------------------------------------------------------
# Als letztes, weil es den Prueflings-Prozess beendet.
chk "8e 'menu beenden' wird bestaetigt" "$(ctl 'menu beenden')" ok
gestorben=nein
for _ in $(seq 1 100); do
  kill -0 "$FACE" 2>/dev/null || { gestorben=ja; break; }
  sleep 0.1
done
chk "8e das Face beendet sich binnen 10 s" "$gestorben" ja
FACE=""

# =============================================================================
# 9. Gegenkontrolle: "inactive" war die ganze Zeit umkehrbar
# =============================================================================
# Ohne das hier waere jedes "inactive" oben moeglicherweise nur die Aussage
# "diese Unit laesst sich auf dieser Maschine ohnehin nicht starten".
echo
echo "--- 9. Gegenkontrolle ---"
attrappe_starten "$EARS_UNIT"; warte_auf "$EARS_UNIT" active 5
chk "9 die Attrappe laesst sich vom Verifizierer wieder starten" "$(zustand "$EARS_UNIT")" active
chk "9 die fremde Kontroll-Unit lief den ganzen Lauf durch" "$(zustand "$FREMD_UNIT")" active
chk "9 der echte daimon-hub.service ist unversehrt" \
  "$([[ "$HUB_ECHT_LIEF" != active || "$(systemctl --user is-active daimon-hub.service 2>/dev/null)" == active ]] && echo ja || echo nein)" ja

echo
echo "--- OFFEN, und zwar benannt ---"
echo "  (1) Kriterium 1 des Plans -- Popup ueber get_popup, mit Grab und"
echo "      Auto-Dismiss, ausgeloest durch einen echten Rechtsklick -- ist hier"
echo "      NICHT geprueft. Der Zeiger laesst sich auf dieser Maschine nicht"
echo "      positionieren, und ein haengender Popup-Grab macht die Maus"
echo "      unbedienbar. Von Hand nachzuholen."
echo "  (2) Dass die deaktivierten Eintraege (Ohren an, Augen an, Persona)"
echo "      SICHTBAR und ausgegraut sind, ist ungeprueft -- der Diagnose-"
echo "      Vertrag hat dafuer kein Feld. Geprueft ist nur, dass sie nicht"
echo "      wirken (8c)."
echo "  (3) Die Attrappen tragen die Unit-Namen aus der Allowlist des Prueflings."
echo "      Gegen die ECHTEN Units daimon-ears/daimon-eyes wird erst in T-3.15"
echo "      und T-5.12 geprueft -- sie existieren in P2 noch nicht."

echo
if [[ "$fail" -eq 0 ]]; then
  echo "T-2.7: gruen. Die Grenze haelt: nur abschalten, nur zwei Ziele,"
  echo "       nur ueber den face-Socket, und kein Popup aus dem Steuerkanal."
  echo "       KEINE volle Abnahme -- siehe die drei offenen Punkte oben."
else
  echo "T-2.7: FEHLGESCHLAGEN"
fi
exit $fail
