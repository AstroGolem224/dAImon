#!/usr/bin/env bash
# Erzeugt die fuenf vorgeschriebenen Persona-Mutanten aus dem Gut-Muster.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.10"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  fehlende-persona-still
  schwelle-faellt-zurueck
  prompt-gekuerzt
  repo-gewinnt
  prompt-mit-zeitstempel
)

for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
done

PERSONA="daimon/mind/persona.py"

sed -i '/if datei is None:/a\        return Persona(name="Vorgabe", wake_words=(), voice=VORGABE_VOICE, palette=dict(VORGABE_PALETTE), speech_threshold="helpful", traits=(), system_prompt="Ich bin die stille Vorgabe.", quelle=xdg, herkunft={"name": "vorgabe", "system_prompt": "vorgabe", "speech_threshold": "vorgabe", "voice": "vorgabe", "traits": "vorgabe", "wake_words": "vorgabe", "palette": "vorgabe"})  # MUTATION: fehlende Datei bleibt still' \
  "$TMP/fehlende-persona-still/$PERSONA"
printf '%s\n' 'Fehlende Persona ergibt still einen Vorgabe-Charakter (Kriterium 5).' \
  >"$TMP/fehlende-persona-still/mutation.txt"

sed -i 's/        raise PersonaFehler(f"{datei}: Feld speech_threshold hat unbekannten Wert {schwelle!r}")/        schwelle = "helpful"  # MUTATION: unbekannte Schwelle faellt still zurueck/' \
  "$TMP/schwelle-faellt-zurueck/$PERSONA"
printf '%s\n' 'Unbekannte speech_threshold faellt still auf helpful zurueck (Kriterium 2).' \
  >"$TMP/schwelle-faellt-zurueck/mutation.txt"

sed -i 's/text = self\.system_prompt$/text = self.system_prompt.splitlines()[0]  # MUTATION: nur erste Zeile/' \
  "$TMP/prompt-gekuerzt/$PERSONA"
printf '%s\n' 'Der Systemprompt wird auf seine erste Zeile gekuerzt (Kriterium 3).' \
  >"$TMP/prompt-gekuerzt/mutation.txt"

sed -i 's/kandidaten = (xdg, mitgeliefert)/kandidaten = (mitgeliefert, xdg)  # MUTATION: Repo gewinnt/' \
  "$TMP/repo-gewinnt/$PERSONA"
printf '%s\n' 'Die mitgelieferte Persona gewinnt vor der XDG-Datei (Kriterium 6).' \
  >"$TMP/repo-gewinnt/mutation.txt"

sed -i '/import tomllib/a\from datetime import date  # MUTATION: wechselnder Prompt' \
  "$TMP/prompt-mit-zeitstempel/$PERSONA"
sed -i '/text = self\.system_prompt$/a\        text += "\\n\\nDatum: " + date.today().isoformat()  # MUTATION: Zeitstempel' \
  "$TMP/prompt-mit-zeitstempel/$PERSONA"
printf '%s\n' 'Der Prompt enthaelt das aktuelle Datum (Kriterium 9).' \
  >"$TMP/prompt-mit-zeitstempel/mutation.txt"

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-3.10: ${#mutanten[@]} Mutanten erzeugt."
