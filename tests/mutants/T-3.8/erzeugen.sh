#!/usr/bin/env bash
# Erzeugt die fuenf T-3.8-Mutanten reproduzierbar aus der blind geschriebenen
# Reviewer-Referenz. Der Arbeitsbaum des Builders ist ausdruecklich keine
# Quelle. Jeder Baum erhaelt genau einen benannten Defekt.
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../../.." || exit 1; pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.8"
QUELLE="$GUT/daimon/gpu/stt.py"

[[ -f "$QUELLE" ]] || { echo "Gut-Muster fehlt: $QUELLE" >&2; exit 1; }
grep -q '^MUTATION = "keine"$' "$QUELLE" \
  || { echo "Mutationsanker im Gut-Muster fehlt" >&2; exit 1; }

MUTANTEN=(nachladen-je-anfrage stille-halluziniert cuda-provider text-geschoent format-egal)
for name in "${MUTANTEN[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mkdir -p "$ziel"
  cp -a "$GUT"/. "$ziel"/
  python3 - "$ziel/daimon/gpu/stt.py" "$name" <<'PY'
from pathlib import Path
import sys

pfad, name = Path(sys.argv[1]), sys.argv[2]
text = pfad.read_text(encoding="utf-8")
anker = 'MUTATION = "keine"'
if text.count(anker) != 1:
    raise SystemExit(f"Mutationsanker {text.count(anker)}x statt einmal")
pfad.write_text(text.replace(anker, f'MUTATION = "{name}"'), encoding="utf-8")
PY
  case "$name" in
    nachladen-je-anfrage)
      beschreibung="Das Modell wird unmittelbar vor jeder Erkennung erneut geladen. Kriterium 9 muss den Wanduhr-Anteil fangen.";;
    stille-halluziniert)
      beschreibung="Eine leere Erkennung wird durch erfundenen Text ersetzt. Kriterium 7 muss ihn bei Aufnahme 21 fangen.";;
    cuda-provider)
      beschreibung="Der Dienst behauptet zur Laufzeit provider=cuda. Kriterium 1 muss die feste CPU-Zusage fangen.";;
    text-geschoent)
      beschreibung="Der rohe Modelltext wird grossgeschrieben und mit Punkt versehen. Der direkte Modell-Orakelvergleich muss die Nachbearbeitung fangen.";;
    format-egal)
      beschreibung="8-bit und Stereo werden stillschweigend verarbeitet. Kriterium 13 muss weiterhin format_falsch verlangen.";;
  esac
  printf 'MUTANT: %s\n\n%s\n' "$name" "$beschreibung" >"$ziel/mutation.txt"
done

rc=0
BAEUME=("$GUT")
for name in "${MUTANTEN[@]}"; do BAEUME+=("$HIER/$name"); done
for baum in "${BAEUME[@]}"; do
  for pflicht in daimon/gpu/stt.py config/systemd/daimon-stt.service \
                 config/systemd/daimon-stt.socket; do
    [[ -f "$baum/$pflicht" ]] || { echo "FEHLER: $baum/$pflicht fehlt"; rc=1; }
  done
  python3 -m py_compile "$baum/daimon/gpu/stt.py" \
    || { echo "FEHLER: Syntax in $baum"; rc=1; }
  find "$baum" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
done

# Jeder Mutant muss den eigenen Anker tragen, das Gut-Muster keinen davon.
for name in "${MUTANTEN[@]}"; do
  grep -qF "MUTATION = \"$name\"" "$HIER/$name/daimon/gpu/stt.py" \
    || { echo "FEHLER: Mutation $name ist nicht im Baum"; rc=1; }
  if diff -qr -x mutation.txt "$GUT" "$HIER/$name" \
       | grep -v 'daimon/gpu/stt.py differ' | grep -q .; then
    echo "FEHLER: $name weicht ausserhalb der Mutationsdatei ab"
    rc=1
  fi
done
if grep -qE '^MUTATION = "(nachladen-je-anfrage|stille-halluziniert|cuda-provider|text-geschoent|format-egal)"$' "$QUELLE"; then
  echo "FEHLER: Gut-Muster ist selbst mutiert"
  rc=1
fi

for ((i=0; i<${#BAEUME[@]}; i++)); do
  for ((j=i+1; j<${#BAEUME[@]}; j++)); do
    if diff -qr -x mutation.txt "${BAEUME[$i]}" "${BAEUME[$j]}" >/dev/null; then
      echo "FEHLER: $(basename "${BAEUME[$i]}") und $(basename "${BAEUME[$j]}") sind identisch"
      rc=1
    fi
  done
done

echo "Gut-Muster: $GUT"
echo "Mutanten: ${MUTANTEN[*]}"
exit "$rc"
