#!/usr/bin/env bash
# Erzeugt die acht T-7.2-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-7.2.v nicht (dort stehen 36 Verifizierer, die Phase 7 ist
# nicht darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an ein
# Akzeptanzkriterium gebunden -- die Zuordnung steht im Kopf von
# tests/verify/t72_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft erst dieses Skript und dann tests/verify/meta.sh T-7.2.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-7.2"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  denylist-greift-nicht
  kennung-ueber-rohklasse
  unbekanntes-fenster-durchgelassen
  drm-ignoriert
  privatmodus-ohne-wirkung
  wahrnehmung-egal
  rohkopie-vor-der-redaktion
  art-nicht-an-unit-gebunden
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
RED = "daimon/recorder/redaktion.py"
DIENST = "daimon/recorder/daemon.py"


def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(
            f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")


ersetze(
    "denylist-greift-nicht", RED,
    "        if k.lower() in self.denylist:\n",
    "        if False:  # MUTATION: die Denylist wird nicht mehr gefragt\n",
    "Die Anwendungs-Denylist wird nicht mehr gefragt; ein gelistetes Fenster "
    "kommt ins Archiv (K2).")

ersetze(
    "kennung-ueber-rohklasse", RED,
    "        return self.kennungen.get(roh.lower(), roh)\n",
    "        return roh  # MUTATION: rohe Fensterklasse statt .desktop-Kennung\n",
    "Die Zuordnung laeuft ueber die rohe Fensterklasse statt ueber die "
    "`.desktop`-Kennung; eine Anwendung mit abweichender Klasse entkommt der "
    "Liste (K3, und die Vorbedingung von K8).")

ersetze(
    "unbekanntes-fenster-durchgelassen", RED,
    "        if not k:\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_UNBEKANNT)\n",
    "        if not k:\n"
    "            return Urteil(stufe)  # MUTATION: Unbekanntes wird abgelegt\n",
    "Ein Fenster ohne Kennung wird durchgelassen statt gesperrt -- die "
    "unbequeme Richtung der Zusage faellt weg (K3).")

ersetze(
    "drm-ignoriert", RED,
    "        if drm:\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_DRM)\n",
    "        if False:  # MUTATION: DRM wird ignoriert\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_DRM)\n",
    "Die DRM-Pruefung nach Design 4.4 greift nicht mehr (K4).")

ersetze(
    "privatmodus-ohne-wirkung", RED,
    "        if self._uhr() < privat_bis(self.runtime_dir):\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_PRIVAT)\n"
    "        if not self.wahrnehmung_an():\n",
    "        if False:  # MUTATION: der Privatmodus wirkt nicht\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_PRIVAT)\n"
    "        if not self.wahrnehmung_an():\n",
    "Der Privatmodus laesst sich einschalten und schreibt trotzdem (K5).")

ersetze(
    "wahrnehmung-egal", RED,
    "        if not self.wahrnehmung_an():\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_WAHRNEHMUNG_AUS)\n",
    "        if False:  # MUTATION: abgeschaltete Wahrnehmung aendert nichts\n"
    "            return Urteil(STUFE_TRANSIENT, GRUND_WAHRNEHMUNG_AUS)\n",
    "Bei abgeschalteter Bildschirmwahrnehmung faellt nichts auf `transient` "
    "(K6, Design 7.2d).")

ersetze(
    "rohkopie-vor-der-redaktion", DIENST,
    '        stufe = str(nachricht.get("stufe", "redacted"))\n',
    '        self.archiv.schreiben(  # MUTATION: Rohkopie VOR dem Urteil\n'
    '            "ocr", str(nachricht.get("text", "")), fenster="rohkopie")\n'
    '        stufe = str(nachricht.get("stufe", "redacted"))\n',
    "Die Rohdaten gehen zuerst auf die Platte, die Redaktion urteilt danach -- "
    "die Reihenfolge, die der Task ausdruecklich ausschliesst (K1/K2, und "
    "damit auch jede andere Sperre; dazu K7: es gibt einen zweiten "
    "Archiv.schreiben-Aufruf).")

ersetze(
    "art-nicht-an-unit-gebunden", DIENST,
    "        if unit is not None and art not in ART_JE_UNIT.get(unit, frozenset()):\n",
    "        if False:  # MUTATION: die Art haengt nicht mehr am Absender\n",
    "Jeder Absender darf jede Art deklarieren; der Augendienst legt seinen "
    "Bildschirmtext als `transkript` ab und geht damit an der Fensterpruefung "
    "vorbei (K7).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-7.2: ${#mutanten[@]} Mutanten erzeugt."
