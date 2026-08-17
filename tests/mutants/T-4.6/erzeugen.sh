#!/usr/bin/env bash
# Erzeugt die vier bindenden T-4.6-Mutanten frisch aus dem Gut-Muster.
# Anhang D, T-4.6.v: `Kette ohne Journal-Anker`, `tainted` im Klartext,
# `Redaktion nur bei sensitive`, `Rotation traegt den Hash nicht weiter`.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-4.6"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  kette-ohne-journal-anker
  tainted-im-klartext
  redaktion-nur-bei-sensitive
  rotation-traegt-hash-nicht-weiter
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
AUDIT = "daimon/hub/audit.py"


def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")


ersetze(
    "kette-ohne-journal-anker", AUDIT,
    "        (journal or _ins_journal)(text)\n",
    "        pass  # MUTATION: der Kopf geht nicht ins Journal\n",
    "Der Kettenkopf wird nirgends verankert; die Kette ist nur gegen sich "
    "selbst pruefbar (K3/K4).")

ersetze(
    "tainted-im-klartext", AUDIT,
    "        for name in tainted:\n"
    "            if name in daten:\n"
    "                daten[name] = redigieren(daten[name])\n",
    "        pass  # MUTATION: tainted-Werte bleiben Klartext\n",
    "Als `tainted` gemeldete Werte landen im Klartext im Audit (K7).")

ersetze(
    "redaktion-nur-bei-sensitive", AUDIT,
    "        for name in tainted:\n"
    "            if name in daten:\n"
    "                daten[name] = redigieren(daten[name])\n",
    "        SENSITIV = {\"clipboard\", \"token\", \"keystrokes\"}  # MUTATION\n"
    "        for name in tainted:\n"
    "            if name in daten and name in SENSITIV:\n"
    "                daten[name] = redigieren(daten[name])\n",
    "Redigiert wird nur, was ein Katalog als `sensitive` fuehrt -- die "
    "Herkunft allein genuegt nicht mehr (K7).")

ersetze(
    "rotation-traegt-hash-nicht-weiter", AUDIT,
    "        kopf = {\"schema\": SCHEMA, \"seq\": self._seq, \"prev_hash\": self._prev,\n"
    "                \"rotation_von\": ziel.name}\n",
    "        kopf = {\"schema\": SCHEMA, \"seq\": self._seq, \"prev_hash\": \"\",\n"
    "                \"rotation_von\": ziel.name}  # MUTATION\n",
    "Nach der Rotation beginnt eine neue, unverbundene Kette (K8).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-4.6: ${#mutanten[@]} Mutanten erzeugt."
