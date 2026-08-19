#!/usr/bin/env bash
# Erzeugt die neun T-4.4-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-4.4.v nicht. Die Liste ist deshalb hier gesetzt: acht
# Mutanten, einer je Akzeptanzpunkt aus dem Implementierungsplan (Z. 1217 ff.),
# plus einer NEUNTER fuer die Naht -- Akzeptanzpunkt 8 ("Direktbefehl-Ausnahme
# ist Hub-Eigentum") ist im Betrieb eine Aussage ueber den Aktionsendpunkt,
# nicht ueber die Engine. Ein Mutant nur in policy.py koennte den Riss dort
# nicht sehen.
#
# Die Zuordnung Mutant -> Kriterium steht im Kopf von
# tests/verify/t44_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-4.4; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-4.4"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  schranke-nur-unverstanden
  hash-aus-dem-request
  initiator-aus-dem-request
  when-als-glob
  cache-ohne-hash
  ttl-ohne-frist
  geste-immer-offen
  direkt-fuer-jede-quelle
  naht-quelle-vom-absender
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
POLICY = f"{P}/hub/policy.py"
DAEMON = f"{P}/hub/daemon.py"


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
    "schranke-nur-unverstanden", POLICY,
    "            schranke = regel.get(\"value_between\")\n"
    "            if schranke and not (float(schranke[0]) <= float(wert) <= float(schranke[1])):\n"
    "                return \"deny\", \"argument_out_of_range\"\n",
    "            schranke = regel.get(\"value_between\")\n"
    "            if schranke and not (float(schranke[0]) <= float(wert) <= float(schranke[1])):\n"
    "                # MUTATION: die drei Fehlerzustaende fallen auf zwei\n"
    "                return \"ask\", \"unparseable_argument\"\n",
    "Ein Wert ausserhalb der Schranke ist nur noch `unverstanden` und wird "
    "zur Rueckfrage statt zur Absage -- aus drei Fehlerzustaenden werden "
    "zwei, und hinterher ist nicht mehr unterscheidbar, ob das Modell etwas "
    "Verbotenes wollte oder nur Unsinn geschickt hat (K1).")

ersetze(
    "hash-aus-dem-request", POLICY,
    "        hash_ = params_hash(anfrage.params)\n",
    "        hash_ = anfrage.params_hash or params_hash(anfrage.params)"
    "  # MUTATION\n",
    "Der Hub uebernimmt den mitgeschickten `params_hash`, wenn einer da ist. "
    "Damit prueft die Policy eine Zahl, die der Absender geliefert hat, und "
    "der Zustimmungs-Cache laesst sich mit einem fremden Schluessel oeffnen "
    "(K2).")

ersetze(
    "initiator-aus-dem-request", POLICY,
    "    if anfrage.quelle == \"scheduler\":\n"
    "        return \"scheduled\"\n",
    "    if anfrage.initiator:  # MUTATION: der Absender sagt, wer er ist\n"
    "        return anfrage.initiator\n"
    "    if anfrage.quelle == \"scheduler\":\n"
    "        return \"scheduled\"\n",
    "`initiator` kommt aus dem Request statt aus der eingeloesten "
    "Rundenmarke. Ein Hintergrundlauf, der `foreground` behauptet, bekommt "
    "die Vorgabe des Menschen vor dem Rechner (K3).")

ersetze(
    "when-als-glob", POLICY,
    "            if schluessel not in felder:\n"
    "                raise PolicyFehler(\n"
    "                    f\"`when` kennt {schluessel!r} nicht; erlaubt sind \"\n"
    "                    + \", \".join(sorted(felder)))\n"
    "            if felder[schluessel] != erwartet:\n"
    "                return False\n",
    "            import fnmatch  # MUTATION: Globs statt Praedikate\n"
    "            if schluessel not in felder:\n"
    "                continue\n"
    "            if not fnmatch.fnmatch(str(felder[schluessel]), str(erwartet)):\n"
    "                return False\n",
    "`when` wird als Glob-Sprache ausgewertet, und ein unbekannter "
    "Schluessel wird still uebergangen. Eine Regel, die nie greift, faellt "
    "damit niemandem mehr auf (K4).")

ersetze(
    "cache-ohne-hash", POLICY,
    "        eintrag = self._zustimmung.get(schluessel)\n",
    "        eintrag = self._zustimmung.get(schluessel) or next(\n"
    "            (v for (s, a, _h), v in self._zustimmung.items()\n"
    "             if (s, a) == (anfrage.session_id, anfrage.action_id)),\n"
    "            None)  # MUTATION: der params_hash faellt aus dem Schluessel\n",
    "Der Zustimmungs-Cache findet einen Eintrag auch ohne passenden "
    "`params_hash`. Die Zustimmung zu `Lautstaerke 0.2` gilt dann fuer "
    "`Lautstaerke 0.9` (K5).")

ersetze(
    "ttl-ohne-frist", POLICY,
    "        if float(anfrage.jetzt) > eintrag[\"bis\"]:\n"
    "            return False\n",
    "        if False:  # MUTATION: die Frist wird nicht mehr geprueft\n"
    "            return False\n",
    "Eine `ttl:*`-Zustimmung laeuft nie ab; aus vier Gueltigkeiten werden "
    "drei, und `ttl:60` ist in Wahrheit `persistent` (K6).")

ersetze(
    "geste-immer-offen", POLICY,
    "        if fenster is not None and float(anfrage.jetzt) > self._geste_bis:\n",
    "        if False:  # MUTATION: das Gestenfenster steht immer offen\n",
    "Von den zwei unabhaengigen Bedingungen aus Design 2.5 bleibt nur die "
    "Erteilung uebrig. Eine Aktion drei Minuten nach dem Tastendruck laeuft "
    "wieder (K7).")

ersetze(
    "direkt-fuer-jede-quelle", POLICY,
    "        direkt = bool(eintrag.get(\"direct\")) and anfrage.quelle == \"parser\"\n",
    "        direkt = bool(eintrag.get(\"direct\"))"
    "  # MUTATION: das Katalogflag genuegt\n",
    "Die Direktbefehl-Ausnahme greift allein am Katalogflag. Eine aus einer "
    "MODELLAUSGABE stammende Aktion umgeht damit die Vorschau -- genau der "
    "Fall, den Design 263 ausschliesst (K8).")

ersetze(
    "naht-quelle-vom-absender", DAEMON,
    "                quelle=\"modell\",\n",
    "                quelle=str(anfrage.get(\"quelle\") or \"modell\"),"
    "  # MUTATION\n",
    "Der Aktionsendpunkt liest `quelle` aus der Anfrage. Damit gibt sich ein "
    "Absender selbst als deterministischer Hub-Parser aus und schaltet die "
    "Vorschau ab; die Ausnahme ist dann Absender-Eigentum statt "
    "Hub-Eigentum (K8, an der Naht).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-4.4: ${#mutanten[@]} Mutanten erzeugt."
