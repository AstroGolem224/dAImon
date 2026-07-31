#!/usr/bin/env bash
# Verifizierer fuer T-2.2: Sprechblase als zweite Subsurface.
#
# Der Plan nennt die Kernmessung selbst: ein Bubble-Ereignis posten,
# `bubble_visible == true` pruefen, `frames_rendered` des Sprites merken, NUR
# den Blasentext aendern, und pruefen dass der Sprite-Zaehler UNVERAENDERT
# blieb.
#
# Das ist der ganze Grund, warum die Blase eine eigene Subsurface ist. Waere
# sie Teil der Sprite-Surface, muesste jede Textaenderung den Sprite
# mitzeichnen -- bei einer Blase, die sich im Sekundentakt aendert, waere das
# die Idle-CPU-Zusage aus T-1.5.
#
# Zwei Dinge prueft dieser Verifizierer zusaetzlich, weil sie an anderer
# Stelle teuer erkauft wurden:
#
#  * Die Blase hat eine EIGENE Input-Region. T-1.3 hat gezeigt, dass die
#    Region der Elternsurface eine Subsurface nicht beschneidet -- eine
#    committete Surface ohne eigene Region nimmt Eingaben auf ihrer ganzen
#    Flaeche an.
#  * Das Face darf dem Hub NUR `bubble_dismiss` schicken. T-1.7 hat den
#    face-Eintrag aus PRODUZENTEN entfernt, damit das Overlay keine
#    Absichtsmarke und keine Freigabe erteilen kann. T-2.2 gibt ihm einen
#    Kanal zurueck; dieser Verifizierer haelt fest, dass es genau einer bleibt.
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
num_gt() { if [[ "$1" =~ ^[0-9]+$ && "$2" =~ ^[0-9]+$ ]] && (( $1 > $2 )); then echo ja; else echo nein; fi; }

echo "T-2.2 — Sprechblase als zweite Subsurface"

# --- Bauen --------------------------------------------------------------------
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

# --- Die Grenze aus T-1.7 haelt ------------------------------------------------
echo "  -- Produzentengrenze"
typen="$(cd "$TARGET" && PYTHONDONTWRITEBYTECODE=1 "$PY" - 2>/dev/null <<'PYEOF'
from daimon.common import ipc
def darf(p, t):
    try:
        ipc.pruefe_typ(p, t); return True
    except Exception:
        return False
for name, wert in (
    ("face_darf_bubble_dismiss", darf("face", "bubble_dismiss")),
    ("face_darf_kein_intent_mark", not darf("face", "intent_mark")),
    ("face_darf_keine_freigabe", not darf("face", "freigabe")),
    ("face_darf_kein_hook", not darf("face", "hook")),
    ("auth_darf_kein_bubble_dismiss", not darf("auth", "bubble_dismiss")),
    ("hookbridge_darf_kein_bubble_dismiss", not darf("hookbridge", "bubble_dismiss")),
):
    print(f"{name}={'ja' if wert else 'nein'}")
PYEOF
)"
w2() { grep -m1 "^$1=" <<<"$typen" | cut -d= -f2; }
# Positivkontrolle zuerst -- ohne sie sagen die Verbote darunter nichts.
chk "face darf bubble_dismiss (Positivkontrolle)" "$(w2 face_darf_bubble_dismiss)" ja
chk "face darf KEIN intent_mark (T-1.7 haelt)" "$(w2 face_darf_kein_intent_mark)" ja
chk "face darf KEINE Freigabe (T-1.7 haelt)" "$(w2 face_darf_keine_freigabe)" ja
chk "face darf kein hook" "$(w2 face_darf_kein_hook)" ja
chk "bubble_dismiss ist nicht auf andere Produzenten gewandert" \
  "$([[ "$(w2 auth_darf_kein_bubble_dismiss)" == ja && "$(w2 hookbridge_darf_kein_bubble_dismiss)" == ja ]] && echo ja || echo nein)" ja

chk "Wayland-Sitzung vorhanden" "$([[ -n "${WAYLAND_DISPLAY:-}" ]] && echo ja || echo nein)" ja
[[ -n "${WAYLAND_DISPLAY:-}" && -x "$BIN" ]] || { echo "  FAIL Live-Pruefungen uebersprungen"; exit 1; }

tmp="$(mktemp -d)"
hub=""; face=""
trap '[[ -n "$face" ]] && kill "$face" 2>/dev/null; [[ -n "$hub" ]] && kill "$hub" 2>/dev/null; rm -rf -- "$tmp" "${BAUDIR:-/nonexistent}"' EXIT

rt="$tmp/rt"
( cd "$REPO" && "$PY" -m daimon.hub.daemon --runtime-dir "$rt" ) >"$tmp/hub.log" 2>&1 &
hub=$!
for _ in $(seq 1 80); do [[ -S "$rt/events.sock" ]] && break; sleep 0.1; done
chk "Hub laeuft (Positivkontrolle)" "$([[ -S "$rt/events.sock" ]] && echo ja || echo nein)" ja

DAIMON_MAX_SECS=120 WAYLAND_DEBUG=1 "$BIN" --pet-manifest "$MANIFEST" \
  --sprite-position 900,500 --hub-socket "$rt/events.sock" \
  --diag-socket "$tmp/d.sock" >"$tmp/out.log" 2>"$tmp/wl.log" &
face=$!
for _ in $(seq 1 200); do [[ -S "$tmp/d.sock" ]] && break; sleep 0.1; done
chk "Overlay startet" "$([[ -S "$tmp/d.sock" ]] && echo ja || echo nein)" ja
sleep 2

diag() { "$PY" - "$tmp/d.sock" <<'PYEOF'
import socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(10); c.connect(sys.argv[1])
print(c.makefile("rb").readline().decode().strip())
c.close()
PYEOF
}
hook() { "$PY" - "$rt" "$1" <<'PYEOF'
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/hookbridge.sock")
c.sendall(json.dumps({"v": 1, "type": "hook", "payload": {
    "hook_event_name": "Notification", "session_id": "t22",
    "notification_type": "permission_prompt",
    "message": sys.argv[2]}}).encode() + b"\n")
c.close()
PYEOF
}

# --- A. Die Blase erscheint ----------------------------------------------------
hook "Darf ich die Datei loeschen"
sleep 2
d1="$(diag)"
echo "  nach dem Bubble-Ereignis: $d1"
chk "Diagnose kennt bubble_frames_rendered" \
  "$(jq -e 'has("bubble_frames_rendered")' <<<"$d1" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "bubble_visible ist true" "$(jq -r '.bubble_visible' <<<"$d1")" true

# --- B. Der Kern: Textaenderung bewegt den Sprite-Zaehler NICHT ----------------
sprite_vor="$(jq -r '.frames_rendered' <<<"$d1")"
blase_vor="$(jq -r '.bubble_frames_rendered' <<<"$d1")"
# Zweite Nachricht, GLEICHER Mood: der Mood bleibt needs_input, also aendert
# sich weder Sprite noch Toenung -- nur der Blasentext.
hook "Und jetzt ein ganz anderer, deutlich laengerer Text fuer die Blase"
sleep 2.5
d2="$(diag)"
sprite_nach="$(jq -r '.frames_rendered' <<<"$d2")"
blase_nach="$(jq -r '.bubble_frames_rendered' <<<"$d2")"
mood_nach="$(jq -r '.mood' <<<"$d2")"
echo "  Sprite-Zaehler $sprite_vor -> $sprite_nach, Blasen-Zaehler $blase_vor -> $blase_nach, mood=$mood_nach"
# Positivkontrolle: die Blase hat wirklich neu gezeichnet. Ohne sie waere
# "Sprite unveraendert" auch dann gruen, wenn gar nichts passiert ist.
chk "der Blasen-Zaehler ist gestiegen (Positivkontrolle)" \
  "$(num_gt "$blase_nach" "$blase_vor")" ja
chk "der Mood blieb derselbe (kein Sprite-Grund zum Neuzeichnen)" "$mood_nach" needs_input
chk "der SPRITE-Zaehler blieb unveraendert" "$sprite_nach" "$sprite_vor"

# --- C. Eigene Subsurface, eigene Input-Region ---------------------------------
subs="$(grep -c 'get_subsurface' "$tmp/wl.log")"
regionen="$(grep -c 'set_input_region' "$tmp/wl.log")"
echo "  get_subsurface: $subs, set_input_region: $regionen"
chk "Protokoll-Mitschnitt ist aktiv (Positivkontrolle)" \
  "$(grep -q 'wl_surface@' "$tmp/wl.log" && echo ja || echo nein)" ja
chk "es gibt zwei Subsurfaces (Sprite und Blase)" \
  "$([[ "$subs" -ge 2 ]] && echo ja || echo nein)" ja
# Drei Surfaces, drei eigene Regionen: Layer, Sprite, Blase. Die Region der
# Elternsurface beschneidet eine Subsurface NICHT (T-1.3).
chk "jede Surface hat eine eigene Input-Region (>= 3)" \
  "$([[ "$regionen" -ge 3 ]] && echo ja || echo nein)" ja

# --- D. Der Blasentext ist gesaeubert ------------------------------------------
# Der Text kommt aus einer Hook-Nutzlast. Ein Bidi-Override darin macht die
# Anzeige irrefuehrend -- gesaeubert wird am Hub, bevor das Face ihn sieht.
hook "harmlos $(printf '‮')umgedreht$(printf '​')unsichtbar"
sleep 2
zustand="$("$PY" - "$rt" <<'PYEOF'
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/state.sock")
print(json.dumps(json.loads(c.makefile("rb").readline()).get("bubble") or {}))
c.close()
PYEOF
)"
echo "  Blase im Hub-Zustand: $zustand"
# Positivkontrolle: es steht ueberhaupt eine Blase im Zustand.
chk "der Hub fuehrt eine Blase (Positivkontrolle)" \
  "$([[ "$zustand" != "{}" && -n "$zustand" ]] && echo ja || echo nein)" ja
chk "kein rohes U+202E im Hub-Zustand" \
  "$(grep -q $'‮' <<<"$zustand" && echo nein || echo ja)" ja
chk "kein rohes U+200B im Hub-Zustand" \
  "$(grep -q $'​' <<<"$zustand" && echo nein || echo ja)" ja
chk "die gefaehrlichen Zeichen erscheinen escapt" \
  "$(grep -q '\\\\u20' <<<"$zustand" && echo ja || echo nein)" ja

# --- E. bubble_dismiss loescht die Blase ---------------------------------------
"$PY" - "$rt" <<'PYEOF'
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/face.sock")
c.sendall(json.dumps({"v": 1, "type": "bubble_dismiss", "payload": {}}).encode() + b"\n")
c.close()
PYEOF
sleep 1.5
danach="$("$PY" - "$rt" <<'PYEOF'
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.settimeout(5); c.connect(sys.argv[1] + "/state.sock")
print(json.dumps(json.loads(c.makefile("rb").readline()).get("bubble")))
c.close()
PYEOF
)"
echo "  Blase nach bubble_dismiss: $danach"
chk "bubble_dismiss loescht die Blase im Hub" \
  "$([[ "$danach" == "null" ]] && echo ja || echo nein)" ja
chk "das Face lebt danach weiter" "$(kill -0 "$face" 2>/dev/null && echo ja || echo nein)" ja

exit $fail
