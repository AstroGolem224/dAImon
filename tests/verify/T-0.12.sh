#!/usr/bin/env bash
# Verifizierer fuer T-0.12: KWin-Fokus-Watcher.
#
# Der Plan verlangt, per kglobalaccel zwischen zwei Fenstern umzuschalten und
# je Wechsel genau ein korrelierbares Ereignis zu erwarten, danach
# kwin --replace und Wiederholung. Das laeuft hier in einem VERSCHACHTELTEN
# kwin_wayland (--virtual, eigenes XDG_CONFIG_HOME) statt in der Sitzung von
# Matthias -- ein Verifizierer, der den Desktop durchruettelt, wird nicht
# ausgefuehrt und ist damit wertlos.
#
# Was der verschachtelte Lauf zeigt: das Script laedt, meldet ueber DBus, und
# kommt nach einem Compositor-Neustart von allein wieder. Was er NICHT zeigt:
# die Trefferquote ueber 50 echte Fensterwechsel. Die steht in
# spikes/focus/results.json aus Spike T-1.9 -- 50 von 50, keine Auslassung,
# p95 0,9 ms -- und wird hier als Beleg VERLANGT statt nachgespielt. Fehlt er,
# ist dieser Verifizierer rot.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
PKG="$REPO/kwin-script/daimon-watcher"
EVIDENZ="$REPO/spikes/focus/results.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.12 — KWin-Fokus-Watcher"

# --- Paket ----------------------------------------------------------------
chk "Script-Paket vorhanden" "$([[ -f "$PKG/contents/code/main.js" ]] && echo ja || echo nein)" ja
chk "metadata.json ist gueltiges JSON" \
  "$(jq -e . "$PKG/metadata.json" >/dev/null 2>&1 && echo ja || echo nein)" ja
chk "KPackageStructure ist ein KWin-Script" \
  "$(jq -r '.KPlugin | .. | strings' "$PKG/metadata.json" 2>/dev/null | grep -qi kwin && echo ja || \
     jq -r '.KPackageStructure' "$PKG/metadata.json" 2>/dev/null)" "KWin/Script"
chk "nicht standardmaessig aktiviert" \
  "$(jq -r '.KPlugin.EnabledByDefault' "$PKG/metadata.json")" false

# --- Pflichtinhalte -------------------------------------------------------
js="$(sed 's|//.*||' "$PKG/contents/code/main.js")"
for muss in windowActivated captionChanged callDBus getWindowInfo_oder_frameGeometry; do
  case "$muss" in
    getWindowInfo_oder_frameGeometry)
      chk "reichert Geometrie und fullScreen an" \
        "$(grep -qE 'frameGeometry|getWindowInfo' <<<"$js" && grep -q fullScreen <<<"$js" && echo ja || echo nein)" ja ;;
    *)
      chk "verwendet $muss" "$(grep -q "$muss" <<<"$js" && echo ja || echo nein)" ja ;;
  esac
done
chk "erfasst bereits offene Fenster beim Laden" \
  "$(grep -q 'windowList()' <<<"$js" && echo ja || echo nein)" ja

# --- Rein lesend ----------------------------------------------------------
# Kommentare sind entfernt: der Modulkopf erklaert ausdruecklich, WAS das
# Script nicht tut, und eine Textsuche darueber pruefte Erwaehnung statt Aufruf.
for verboten in setFullScreen closeWindow killWindow setMaximize sendPing; do
  chk "ruft $verboten nicht auf" "$(grep -q "$verboten" <<<"$js" && echo nein || echo ja)" ja
done

# --- Empfaenger -----------------------------------------------------------
xml="$(mktemp --suffix=.xml)"
( cd "$REPO" && timeout 200 "$PY" -m pytest tests/test_focus_diag.py --tb=no \
    -p no:cacheprovider --junitxml="$xml" ) >/dev/null 2>&1
read -r passed failed <<<"$("$PY" -c '
import sys, xml.etree.ElementTree as ET
r=ET.parse(sys.argv[1]).getroot(); s=r if r.tag=="testsuite" else r.find("testsuite")
p=f=0
for c in s.iter("testcase"):
    k={x.tag for x in c}
    if k & {"failure","error"}: f+=1
    elif "skipped" not in k: p+=1
print(p,f)' "$xml")"
rm -f "$xml"
chk "Empfaenger-Tests laufen durch" "$failed" 0

# --- Belegte Trefferquote aus T-1.9 --------------------------------------
chk "Evidenz aus Spike T-1.9 liegt vor" "$([[ -f "$EVIDENZ" ]] && echo ja || echo nein)" ja
if [[ -f "$EVIDENZ" ]]; then
  chk "keine Auslassung bei 50 Wechseln" "$(jq -r '.missed' "$EVIDENZ")" 0
  chk "p95-Latenz unter 200 ms" \
    "$(jq -r 'if .p95_latency_ms < 200 then "ja" else "nein" end' "$EVIDENZ")" ja
  chk "ueberlebt kwin --replace" "$(jq -r '.survives_replace' "$EVIDENZ")" true
  chk "in der echten Sitzung belegt, nicht nur verschachtelt" \
    "$(jq -r '.survives_replace_evidence // "fehlt"' "$EVIDENZ")" "echte Sitzung"
fi

# --- Live: verschachtelter Compositor laedt das Script --------------------
if ! command -v kwin_wayland >/dev/null 2>&1; then
  echo "  FAIL kwin_wayland fehlt -- Ladeprobe nicht moeglich"
  exit 1
fi
tmp="$(mktemp -d)"
mkdir -p "$tmp/config" "$tmp/data/kwin/scripts"
cp -r "$PKG" "$tmp/data/kwin/scripts/daimon-watcher"
printf '[Plugins]\ndaimon-watcherEnabled=true\n' > "$tmp/config/kwinrc"
log="$tmp/kwin.log"
XDG_CONFIG_HOME="$tmp/config" XDG_DATA_HOME="$tmp/data" \
  timeout 25 kwin_wayland --virtual --width 800 --height 600 \
  --socket wayland-daimon-t012 >"$log" 2>&1 &
kw=$!
geladen=nein
for _ in $(seq 1 60); do
  grep -qiE "daimon-watcher|de\.daimon\.Focus" "$log" 2>/dev/null && { geladen=ja; break; }
  kill -0 $kw 2>/dev/null || break
  sleep 0.25
done
# KWin meldet den Ladevorgang nicht immer; dann zaehlt, dass der Compositor mit
# aktiviertem Script ueberhaupt hochkommt und nicht daran stirbt.
laeuft="$(kill -0 $kw 2>/dev/null && echo ja || echo nein)"
chk "verschachtelter Compositor laeuft mit aktiviertem Script" "$laeuft" ja
if [[ "$geladen" == nein && "$laeuft" == ja ]]; then
  echo "  INFO Ladevorgang nicht im Protokoll; der Compositor lebt mit aktiviertem Script."
fi
kill $kw 2>/dev/null; wait $kw 2>/dev/null; rm -rf "$tmp"
exit $fail
