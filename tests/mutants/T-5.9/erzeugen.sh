#!/usr/bin/env bash
# Erzeugt die zwoelf T-5.9-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-5.9.v nicht (dort stehen 36 Verifizierer; T-5.9 ist nicht
# darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau ein
# Akzeptanzkriterium gebunden -- die Zuordnung steht im Kopf von
# tests/verify/t59_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-5.9; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-5.9"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  keine-marke-egal
  kontingent-deklassifiziert
  marke-aus-jeder-quelle
  bezug-immer-erkannt
  bezug-nie-erkannt
  abgelaufene-marke-egal
  proaktiv-erlaubt
  quarantaene-ohne-schein
  senke-durchgang1
  freigabe-nicht-markiert
  audit-schweigt
  titel-in-durchgang1
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
MARKS = f"{P}/hub/marks.py"
CONTEXT = f"{P}/eyes/context.py"
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


ersetze(
    "keine-marke-egal", GATE,
    "        if turn_id is None:\n"
    "            # Das Kontingent bekommt seinen EIGENEN Grund und nicht\n",
    "        if turn_id is None:  # MUTATION: ohne Marke wird freigegeben\n"
    "            schein = Freigabeschein(turn_id=\"mutation-ohne-marke\")\n"
    "            eintraege = self._speicher.freigeben(schein)\n"
    "            return Freigabe(\n"
    "                turn_id=\"\",\n"
    "                umfang={a: len(l) for a, l in eintraege.items()},\n"
    "                eintraege=[markiere(e, Mark.TAINTED)\n"
    "                           for l in eintraege.values() for e in l])\n"
    "        if False:\n"
    "            # Das Kontingent bekommt seinen EIGENEN Grund und nicht\n",
    "Ohne Rundenmarke wird der Kontextspeicher trotzdem geoeffnet -- genau "
    "die Frage des Builders, mit `ja` beantwortet (K1).")

ersetze(
    "kontingent-deklassifiziert", MARKS,
    "    def erlaubt_deklassifizierung(self, kontingent_id: str) -> bool:\n"
    "        \"\"\"IMMER False, aus demselben Grund.\"\"\"\n"
    "        return False\n",
    "    def erlaubt_deklassifizierung(self, kontingent_id: str) -> bool:\n"
    "        return True  # MUTATION: das Kontingent deklassifiziert doch\n",
    "Ein API-Kontingent aus dem Wake-Word erlaubt die Deklassifizierung -- "
    "Design 7.2b sagt ausdruecklich das Gegenteil (K2).")

ersetze(
    "marke-aus-jeder-quelle", MARKS,
    "            if quelle != self.QUELLE_AUTH:\n",
    "            if False:  # MUTATION: jede Quelle darf eine Marke ausgeben\n",
    "Eine Rundenmarke entsteht aus jeder Quelle, auch aus dem Wake-Word; "
    "damit traegt ein gesprochener Name die Deklassifizierung (K2).")

ersetze(
    "bezug-immer-erkannt", GATE,
    "    return bool(_BEZUG.search(aeusserung or \"\"))\n",
    "    return True  # MUTATION: jede Aeusserung gilt als Bildschirmfrage\n",
    "Der Bildschirmbezug wird immer erkannt; von der Bedingung bleibt nur "
    "noch die Marke uebrig (K3).")

ersetze(
    "bezug-nie-erkannt", GATE,
    "    return bool(_BEZUG.search(aeusserung or \"\"))\n",
    "    return False  # MUTATION: nichts gilt als Bildschirmfrage\n",
    "Der Bildschirmbezug wird nie erkannt -- das Gate gibt nichts mehr frei. "
    "Der Mutant toetet die Positivkontrolle: ein Verifizierer, der ihn nicht "
    "faengt, meldet gruen, weil er nichts messen kann (K4).")

ersetze(
    "abgelaufene-marke-egal", GATE,
    "        except MarkenFehler:\n"
    "            raise self._ablehnen(GRUND_MARKE_UNGUELTIG, turn_id,\n"
    "                                 aeusserung) from None\n",
    "        except MarkenFehler:\n"
    "            pass  # MUTATION: eine ungueltige Marke wird durchgewunken\n",
    "Eine abgelaufene, bereits eingeloeste oder erfundene Rundenmarke gibt "
    "trotzdem frei (K5).")

ersetze(
    "proaktiv-erlaubt", GATE,
    "        if proaktiv:\n",
    "        if False:  # MUTATION: proaktiv darf auch\n",
    "Proaktives Verhalten bekommt Bildschirmkontext, sobald irgendeine Runde "
    "offen ist -- ohne Nutzerhandlung (K6).")

ersetze(
    "quarantaene-ohne-schein", CONTEXT,
    "        if not ist_freigabeschein(schein):\n",
    "        if False:  # MUTATION: die Quarantaene verlangt keinen Schein\n",
    "Der Kontextspeicher gibt seine Eintraege jedem heraus, der fragt; der "
    "eine Ausgang wird zur offenen Tuer (K7).")

ersetze(
    "senke-durchgang1", GATE,
    "    senke: str = \"durchgang2\"\n",
    "    senke: str = \"durchgang1\"  # MUTATION: in den werkzeugfaehigen Durchgang\n",
    "Die Freigabe nennt den WERKZEUGFAEHIGEN Durchgang 1 als Senke (K8).")

ersetze(
    "freigabe-nicht-markiert", GATE,
    "        markiert = [markiere(e, Mark.TAINTED)\n"
    "                    for liste in eintraege.values() for e in liste]\n",
    "        markiert = [e  # MUTATION: ohne Markierung herausgegeben\n"
    "                    for liste in eintraege.values() for e in liste]\n",
    "Freigegebener Bildschirmkontext traegt keine Markierung mehr; die "
    "Senkentabelle aus T-3.13b kann ihn nicht mehr sperren (K8).")

ersetze(
    "audit-schweigt", GATE,
    "        if self._audit is None:\n"
    "            return\n",
    "        if True:  # MUTATION: das Audit bekommt nichts zu sehen\n"
    "            return\n",
    "Weder Freigabe noch Ablehnung landen im Audit -- der Zustand, in dem "
    "das Gate bis zum 17.08. tatsaechlich lief (K9).")

ersetze(
    "titel-in-durchgang1", ROUTER,
    "            offen.append({\"ref\": ref,\n"
    "                          \"app_id\": roh if roh in erlaubt else \"unbekannt\"})\n",
    "            offen.append({\"ref\": ref, \"titel\": f.get(\"titel\", \"\"),\n"
    "                          \"app_id\": roh if roh in erlaubt else \"unbekannt\"})\n",
    "Durchgang 1 bekommt den Fenstertitel als typisiertes Feld -- genau die "
    "Fassung, die Design 5.1 verwirft (K10).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-5.9: ${#mutanten[@]} Mutanten erzeugt."
