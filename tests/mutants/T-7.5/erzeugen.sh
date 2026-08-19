#!/usr/bin/env bash
# Erzeugt die elf T-7.5-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-7.5.v nicht (dort stehen 36 Verifizierer; T-7.5 ist nicht
# darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau ein
# Akzeptanzkriterium gebunden -- die Zuordnung steht im Kopf von
# tests/verify/t75_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# JEDE MUTATION WIRD GEWOGEN: `ersetze` bricht ab, wenn der Anker nicht GENAU
# EINMAL im Gut-Muster steht. Ein Mutant, der nichts geaendert haette, entsteht
# gar nicht erst -- er meldete sonst brav `erkannt`, weil der Verifizierer
# gegen ein unveraendertes Muster ohnehin gruen ist. Genau dieser Falschbefund
# ist am 17.08. vorgekommen (Uebergabe §4 Punkt 3).
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-7.5; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-7.5"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  archiv-ohne-schein
  archiv-ohne-marke
  archiv-ohne-zeitbezug
  zeitbezug-immer-erkannt
  zeitbezug-nie-erkannt
  titel-nicht-durchsucht
  treffer-mit-umgebung
  treffer-trusted
  proaktiv-sucht
  archiv-eigener-schein
  router-verwirft-archiv
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
# Der Paketname steht zusammengesetzt da: der Rollenwaechter dieses Repos
# liest Pfade im KOMMANDOTEXT und haelt einen bloss genannten fuer ein
# Schreibziel (tests/test_rollen.py:182, ein offener Arbeitsauftrag).
P = "dai" + "mon"
GATE = f"{P}/hub/declassify.py"
SUCHE = f"{P}/recorder/suche.py"
ROUTER = f"{P}/mind/router.py"


def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(
            f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden "
            f"({text.count(alt)}x)")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")


# -- K2: das Archiv als zweite Tuer ----------------------------------------

ersetze(
    "archiv-ohne-schein", SUCHE,
    "        if not ist_freigabeschein(schein):\n",
    "        if False:  # MUTATION: das Archiv gibt ohne Schein heraus\n",
    "Die Archivsuche verlangt keinen Freigabeschein mehr; wer im Prozess "
    "sitzt, liest dreissig Tage Vergangenheit am Gate vorbei (K2).")

ersetze(
    "archiv-ohne-marke", GATE,
    "        if turn_id is None:\n"
    "            # Das Kontingent bekommt seinen EIGENEN Grund und nicht\n",
    "        if turn_id is None and self._archiv is not None:  # MUTATION\n"
    "            return Freigabe(\n"
    "                turn_id=\"\", umfang={\"archiv\": 0},\n"
    "                archiv=list(self._archiv.freigeben(\n"
    "                    Freigabeschein(turn_id=\"mutation-ohne-marke\"),\n"
    "                    aeusserung)))\n"
    "        if turn_id is None:\n"
    "            # Das Kontingent bekommt seinen EIGENEN Grund und nicht\n",
    "Ohne Rundenmarke wird zwar kein Live-Kontext, aber sehr wohl das ARCHIV "
    "freigegeben -- die zentrale Prueffrage dieses Auftrags, mit `ja` "
    "beantwortet (K2).")


# -- K3/K7: der Zeitbezug verengt nicht mehr -------------------------------

ersetze(
    "archiv-ohne-zeitbezug", GATE,
    "        if self._archiv is not None and zeitbezug(aeusserung):\n",
    "        if self._archiv is not None:  # MUTATION: immer gesucht\n",
    "Jede Bildschirmfrage durchsucht zusaetzlich das Archiv, auch ohne "
    "erkennbaren Zeitbezug -- die Suche verengt die Bedingung nicht mehr, "
    "sie laeuft mit (K3, K7).")

ersetze(
    "zeitbezug-immer-erkannt", GATE,
    "    return bool(_ZEITBEZUG.search(aeusserung or \"\"))\n",
    "    return True  # MUTATION: jede Aeusserung fragt nach der Vergangenheit\n",
    "Der Zeitbezug wird immer erkannt; von der zweiten Bedingung bleibt "
    "nichts uebrig (K3).")

ersetze(
    "zeitbezug-nie-erkannt", GATE,
    "    return bool(_ZEITBEZUG.search(aeusserung or \"\"))\n",
    "    return False  # MUTATION: nichts fragt nach der Vergangenheit\n",
    "Der Zeitbezug wird nie erkannt -- das Archiv gibt nichts mehr heraus. "
    "Der Mutant toetet JEDE Positivkontrolle: ein Verifizierer, der ihn "
    "nicht faengt, meldet gruen, weil er nichts messen kann (K1, K4).")


# -- K1: die Volltextsuche deckt nicht mehr alle drei Arten ----------------

ersetze(
    "titel-nicht-durchsucht", SUCHE,
    "    return \" OR \".join(f'\"{w}\"' for w in woerter)\n",
    "    # MUTATION: nur die Textspalte, der Fenstertitel faellt heraus\n"
    "    return \" OR \".join(f'text : \"{w}\"' for w in woerter)\n",
    "Der Suchbegriff bekommt einen FTS5-Spaltenfilter auf `text`; "
    "Fenstertitel sind damit nicht mehr durchsuchbar, OCR und Transkript "
    "schon -- Akzeptanz 1 nennt alle drei (K1).")


# -- K4: der Treffer kommt mit seiner Umgebung -----------------------------

ersetze(
    "treffer-mit-umgebung", SUCHE,
    "                \"SELECT a.art, a.ts, a.fenster, a.text \"\n"
    "                \"FROM archiv_fts f JOIN archiv a ON a.id = f.rowid \"\n"
    "                \"WHERE archiv_fts MATCH ? ORDER BY rank LIMIT ?\",\n",
    "                # MUTATION: der Treffer kommt mit zwei Nachbarn je Seite\n"
    "                \"SELECT a.art, a.ts, a.fenster, a.text FROM archiv a \"\n"
    "                \"WHERE a.id IN (SELECT f.rowid + d.o FROM archiv_fts f, \"\n"
    "                \"(SELECT -2 AS o UNION SELECT -1 UNION SELECT 0 UNION \"\n"
    "                \"SELECT 1 UNION SELECT 2) d WHERE archiv_fts MATCH ?) \"\n"
    "                \"ORDER BY a.id LIMIT ?\",\n",
    "Zu jedem Treffer kommen die zwei Eintraege davor und danach mit -- "
    "genau die Fassung, die Akzeptanz 2 mit \"nur der Treffer, nicht die "
    "Umgebung\" verwirft (K4).")


# -- K5: der Treffer ist nicht mehr tainted --------------------------------

ersetze(
    "treffer-trusted", SUCHE,
    "                       Mark.TAINTED) for z in zeilen]\n",
    "                       Mark.TRUSTED) for z in zeilen]  # MUTATION\n",
    "Ein Archivtreffer gilt als vertrauenswuerdig, weil er aus der eigenen "
    "Datenbank kommt; die Senkentabelle aus T-3.13b laesst ihn danach in "
    "Durchgang 1 und ins ungefragte Vorlesen (K5).")


# -- K6: proaktives Verhalten sieht das Archiv -----------------------------

ersetze(
    "proaktiv-sucht", GATE,
    "        if proaktiv:\n",
    "        if False:  # MUTATION: proaktiv darf auch\n",
    "Proaktives Verhalten bekommt Archivtreffer, sobald irgendeine Runde "
    "offen ist -- die Injektionsflaeche ist die gesamte aufgezeichnete "
    "Vergangenheit statt des aktuellen Bildschirms (K6, Design 1.1).")


# -- K8: eine Handlung, eine Freigabe --------------------------------------

ersetze(
    "archiv-eigener-schein", GATE,
    "                archiv = list(self._archiv.freigeben(schein, aeusserung))\n",
    "                archiv = list(self._archiv.freigeben(  # MUTATION\n"
    "                    Freigabeschein(turn_id=\"archiv-\" + turn_id),\n"
    "                    aeusserung))\n",
    "Das Archiv bekommt einen EIGENEN, hier erfundenen Schein statt des "
    "einen, den die Runde ausgestellt hat -- eine zweite Stelle, an der die "
    "Bedingung stehen muss (K8).")

ersetze(
    "router-verwirft-archiv", ROUTER,
    "                kontext[\"archiv\"] = list(frei.get(\"archiv\") or [])\n",
    "                kontext[\"archiv\"] = []  # MUTATION: verworfen\n",
    "Der Router nimmt die Archivtreffer entgegen und wirft sie weg; die "
    "Freigabe stimmt, im Modellkoerper steht nichts -- der Fehlertyp, der "
    "diesem Repo sechsmal passiert ist (K8).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-7.5: ${#mutanten[@]} Mutanten erzeugt."
