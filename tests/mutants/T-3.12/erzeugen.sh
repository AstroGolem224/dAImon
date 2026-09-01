#!/usr/bin/env bash
# Erzeugt die sechs bindenden T-3.12-Mutanten frisch aus dem Gut-Muster.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.12"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  titel-im-prompt
  aktion-wird-ausgefuehrt
  aktion-ohne-absichtsmarke
  user-audio-durchgelassen
  referenz-ueberlebt-die-runde
  lokale-frage-geht-an-die-api
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
  find "$TMP/$name" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
R = "daimon/mind/router.py"

def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")

ersetze(
    "titel-im-prompt", R,
    '        opak = {ref: {"app_id": e["app_id"]} for ref, e in tabelle.items()}',
    '        opak = {ref: {"app_id": e["app_id"], "titel": e["titel"]}\n'
    '                for ref, e in tabelle.items()}  # MUTATION',
    "Der Modellaufruf bekommt neben der app_id auch den Fenstertitel (K6).")

# Seit T-4.16 K1 lehnt der Aktionszweig nicht mehr ab, er waehlt den
# werkzeugfaehigen Weg -- der Anker der Mutation wandert mit. Der Angriff
# bleibt derselbe: der Router oeffnet die Tuer selbst, statt den
# Koordinator zu fragen.
ersetze(
    "aktion-wird-ausgefuehrt", R,
    '            if absicht == "aktion":\n'
    '                marke = req.get("marke")',
    '            if absicht == "aktion":\n'
    '                try:\n'
    '                    subprocess.run([text.split()[-1]], capture_output=True)  # MUTATION\n'
    '                except OSError:\n'
    '                    pass\n'
    '                return {"v": 1, "ok": True, "weg": "lokal",\n'
    '                        "absicht": "aktion", "antwort": "Erledigt.",\n'
    '                        "marke": "trusted", "api": False}\n'
    '                marke = req.get("marke")',
    "Aktionswünsche werden vom Router selbst ausgeführt (letztes Wort als "
    "Befehl) und als erledigt gemeldet, statt den werkzeugfähigen Weg über "
    "den Koordinator zu nehmen (K3).")

# T-4.19, neu mit dem Kriterium: der Riegel gegen eine Aktion OHNE
# Absichtsmarke. `tainted` und `user_audio` fallen schon an der Senke --
# diesen Riegel bindet allein der `trusted`-Fall.
ersetze(
    "aktion-ohne-absichtsmarke", R,
    '                if marke != "user_ptt":',
    '                if False and marke != "user_ptt":  # MUTATION',
    "Eine Aktionsbitte ohne Absichtsmarke (trusted) wird nicht mehr "
    "abgelehnt, sondern geht den werkzeugfähigen Weg und kostet ein "
    "Ticket (K3).")

ersetze(
    "user-audio-durchgelassen", R,
    '        if req.get("marke") == "user_audio":',
    '        if False and req.get("marke") == "user_audio":  # MUTATION',
    "Die Senke für user_audio ist aufgehoben; die Markierung erreicht "
    "Durchgang 1 (K5).")

ersetze(
    "referenz-ueberlebt-die-runde", R,
    "        if self._runde is not runde and self._runde != runde:\n"
    "            self._tabelle = {}\n"
    "            self._runde = runde",
    "        if False:  # MUTATION: die Tabelle wird nie verworfen\n"
    "            self._tabelle = {}\n"
    "            self._runde = runde",
    "Die Referenztabelle wird nicht je Runde neu gebildet; Referenzen aus "
    "alten Runden bleiben auflösbar (K8).")

ersetze(
    "lokale-frage-geht-an-die-api", R,
    '            if absicht == "uhrzeit":\n'
    '                return lokal("uhrzeit",\n'
    '                             f"Es ist {time.strftime(\'%H:%M\')}.", "trusted")',
    '            if absicht == "uhrzeit":\n'
    '                self._api(text, runde)  # MUTATION: lokale Frage kostet Kontingent\n'
    '                return lokal("uhrzeit",\n'
    '                             f"Es ist {time.strftime(\'%H:%M\')}.", "trusted")',
    "Die Uhrzeitfrage wird lokal beantwortet, löst aber vorher ein Ticket "
    "ein und ruft den Egress — api: false ist dann eine Behauptung (K1).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-3.12: ${#mutanten[@]} Mutanten erzeugt."
