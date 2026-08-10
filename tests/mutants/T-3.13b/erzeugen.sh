#!/usr/bin/env bash
# Erzeugt die sieben T-3.13b-Mutanten frisch aus dem Gut-Muster.
# Fünf sind im Vertrag benannt; `aktionsergebnis-als-trusted` und
# `verkettung-nimmt-schwaechste` decken die beiden uebrigen Grenzen aus
# Kriterium 12 ab (Aktionsergebnis, Verkettung).
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.13b"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  markierung-bei-ipc-verloren
  user-audio-erreicht-durchgang-1
  hook-freitext-als-trusted
  freie-modellausgabe-als-trusted
  auth-vorschau-nimmt-rohe-zeichenkette
  aktionsergebnis-als-trusted
  verkettung-nimmt-schwaechste
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
PROTO = "daimon/common/protocol.py"
TAINT = "daimon/common/taint.py"
ROUTER = "daimon/mind/router.py"
ANSWER = "daimon/mind/answer.py"
PREVIEW = "daimon/auth/preview.py"
BRIDGE = "daimon/hookbridge/bridge.py"

def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")

ersetze(
    "markierung-bei-ipc-verloren", PROTO,
    "            if isinstance(value, Marked):\n"
    "                value = value.to_wire()",
    "            if isinstance(value, Marked):\n"
    "                value = value.value  # MUTATION: Marke faellt weg",
    "dumps() serialisiert einen markierten Wert als nackten Wert — die "
    "Marke ueberlebt die IPC-Serialisierung nicht (K6). Beim Leser kommt "
    "per Vorgabe `tainted` an, was immer die Absender-Marke war.")

ersetze(
    "user-audio-erreicht-durchgang-1", ROUTER,
    '        if marke in ("user_audio", "tainted") and absicht != "api":',
    '        if marke in ("tainted",) and absicht != "api":  # MUTATION',
    "Die Senke fuer user_audio ist aufgehoben: gespooftes Audio erreicht "
    "Durchgang 1 (K3, K8) — lokale Auskuenfte und Aktionswuensche werden "
    "fuer eine spoofbare Aeusserung bearbeitet.")

ersetze(
    "hook-freitext-als-trusted", BRIDGE,
    "            markiert[name] = Marked(wert, Mark.TAINTED)",
    "            markiert[name] = Marked(wert, Mark.TRUSTED)  # MUTATION",
    "Die Freitextfelder der Hook-Nutzlast (message, prompt, "
    "last_assistant_message, error, cwd, tool_name) werden als trusted "
    "statt tainted markiert (K7) — Text aus einem Agenten, der beliebige "
    "Inhalte verarbeitet hat, wird vertrauenswuerdig.")

ersetze(
    "freie-modellausgabe-als-trusted", ANSWER,
    '                "absicht": "api", "antwort": modelltext, "marke": "tainted",',
    '                "absicht": "api", "antwort": modelltext, "marke": "trusted",  # MUTATION',
    "Freie Modellausgabe aus Durchgang 2 wird als trusted markiert (K9) — "
    "auch wenn sie strukturiert aussieht. Ein Modellausgabe-Pfad ohne "
    "Markierung.")

ersetze(
    "auth-vorschau-nimmt-rohe-zeichenkette", PREVIEW,
    '    ziel_wert = pruefe_senke(ziel, senke="auth_vorschau").value',
    "    ziel_wert = ziel  # MUTATION: keine Senkenpruefung, kein Protokoll",
    "Die Vorschau nimmt eine rohe Zeichenkette ohne marke_fehlt-Protokoll "
    "und ohne tainted-Vorgabe an (K2, K10) — und wirft fuer user_audio "
    "keinen SenkenFehler mehr (K3).")

ersetze(
    "aktionsergebnis-als-trusted", ROUTER,
    '                return lokal("fensterliste", antwort, "tainted")',
    '                return lokal("fensterliste", antwort, "trusted")  # MUTATION',
    "Ein Ergebnis mit Fenstertiteln wird als trusted markiert (K5, K9) — "
    "die Markierung folgt nicht mehr der Herkunft der Daten, sondern der "
    "Komponente, die sie geholt hat.")

ersetze(
    "verkettung-nimmt-schwaechste", TAINT,
    "        if RANG[s.mark] > RANG[strengste]:",
    "        if RANG[s.mark] < RANG[strengste]:  # MUTATION",
    "verketten nimmt die schwaechste statt der strengsten Marke (K5) — "
    "Ansteckung laeuft ins Leere: verketten(trusted, tainted) wird "
    "trusted.")
PY

for name in "${mutanten[@]}"; do
  rm -rf "$HIER/$name"
  mv "$TMP/$name" "$HIER/$name"
done
echo "erzeugt: ${#mutanten[@]} Mutanten unter $HIER"
