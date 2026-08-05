#!/usr/bin/env bash
# Erzeugt die vier bindenden T-3.13-Mutanten frisch aus dem Gut-Muster.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.13"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  werkzeugliste-im-koerper
  aktionsvorschlag-durchgereicht
  antwort-als-trusted
  user-audio-auch-in-durchgang-eins
  kontext-als-top-level-feld
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
A = "daimon/mind/answer.py"
R = "daimon/mind/router.py"

def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")

ersetze(
    "werkzeugliste-im-koerper", A,
    '            "max_tokens": 256,',
    '            "max_tokens": 256,\n'
    '            "tools": [{"name": "close_window",\n'
    '                       "description": "Schließt ein Fenster.",\n'
    '                       "input_schema": {"type": "object"}}],  # MUTATION\n'
    '            "tool_choice": {"type": "auto"},  # MUTATION',
    "Der Körper an den Egress trägt eine Werkzeugliste und tool_choice "
    "(K1) — Durchgang 2 KANN plötzlich Werkzeuge wählen.")

ersetze(
    "aktionsvorschlag-durchgereicht", A,
    "        erkannt = aktionsvorschlag_erkannt(modelltext)",
    "        erkannt = aktionsvorschlag_erkannt(modelltext)\n"
    "        if erkannt:\n"
    "            try:\n"
    "                self._rufen(\"ticket.sock\",\n"
    "                            {\"v\": 1, \"art\": \"action_request\",\n"
    "                             \"vorschlag\": json.loads(modelltext)},\n"
    "                            timeout=3)  # MUTATION\n"
    "            except (OSError, ValueError):\n"
    "                pass",
    "Ein erkannter Aktionsvorschlag wird als action_request an den Hub "
    "durchgereicht statt verworfen (K3) — das Flag bleibt wahr, die "
    "Verwerfung fehlt.")

ersetze(
    "antwort-als-trusted", A,
    '                "absicht": "api", "antwort": modelltext, "marke": "tainted",',
    '                "absicht": "api", "antwort": modelltext, "marke": "trusted",  # MUTATION',
    "Die Modellantwort aus Durchgang 2 wird als trusted markiert (K4) — "
    "ein Modellausgabe-Pfad ohne Markierung.")

ersetze(
    "user-audio-auch-in-durchgang-eins", R,
    '        if req.get("marke") == "user_audio" and absicht != "api":',
    '        if False and req.get("marke") == "user_audio" and absicht != "api":  # MUTATION',
    "Die Senke für user_audio ist aufgehoben; die Markierung erreicht "
    "auch Durchgang 1 (K5) — lokale Auskünfte und Aktionswünsche werden "
    "für gespooftes Audio bearbeitet.")

ersetze(
    "kontext-als-top-level-feld", A,
    '            "messages": [{"role": "user", "content": nutzertext}],',
    '            "messages": [{"role": "user", "content": nutzertext}],\n'
    '            "kontext": {"quellen": [], "deklassifiziert": []},  # MUTATION',
    "Der Kontext steht als Top-Level-Feld neben messages (§6) — die echte "
    "Messages-API würde diesen Körper mit 400 invalid_request_error "
    "zurückweisen.")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-3.13: ${#mutanten[@]} Mutanten erzeugt."
