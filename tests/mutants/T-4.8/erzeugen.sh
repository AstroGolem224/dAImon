#!/usr/bin/env bash
# Erzeugt die zwoelf T-4.8-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-4.8.v nicht. Die Liste ist deshalb hier gesetzt: mindestens
# einer je Akzeptanzpunkt aus dem Implementierungsplan (Z. 1273 ff.), plus je
# einer fuer die drei Fehlerfaelle, die der Verifikationsabsatz (Z. 1284)
# ausdruecklich verlangt, plus einer fuer den Zulauf.
#
# Die Zuordnung Mutant -> Kriterium steht im Kopf von
# tests/verify/t48_pruefstand.py und im Ledger.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-4.8; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-4.8"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  trashinfo-nur-name
  trashinfo-falscher-name
  kopie-ohne-reflink
  kopie-verschiebt
  stash-nicht-vorher
  stash-zaehlt-nicht
  verifikation-fort
  groesse-egal
  trash-grenze-egal
  undo-fehler-verschluckt
  verifiziert-vor-der-pruefung
  zulauf-fort
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
UNDO = f"{P}/brokers/fs/undo.py"
HUB = f"{P}/hub/daemon.py"


def ersetze(name, pfad, alt, neu, beschreibung, mal=1):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != mal:
        raise SystemExit(
            f"{name}: Mutationsanker {alt[:60]!r} nicht genau {mal}x "
            f"gefunden ({text.count(alt)}x)")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n",
                                           encoding="utf-8")


# -- K1: Loeschen -> XDG-Trash mit korrektem `.trashinfo` --------------------

ersetze(
    "trashinfo-nur-name", UNDO,
    '        f"Path={urllib.parse.quote(str(quelle.resolve()))}\\n"\n',
    "        # MUTATION: nur der Dateiname, nicht der Ort\n"
    '        f"Path={urllib.parse.quote(quelle.name)}\\n"\n',
    "Der Zettel traegt nur den Dateinamen statt des absoluten Pfades. Die "
    "Datei liegt im Papierkorb, sieht dort auch richtig aus -- aber weder "
    "ein Dateimanager noch ein Mensch weiss, WOHIN sie zurueckgehoert. Der "
    "Papierkorbeintrag ist nicht wiederherstellbar, und genau das ist der "
    "Unterschied zwischen 'geloescht' und 'umkehrbar geloescht' (K1).")

ersetze(
    "trashinfo-falscher-name", UNDO,
    '    info = infos / f"{ziel.name}.trashinfo"\n',
    "    # MUTATION: der Zettel heisst nach dem Stamm, nicht nach der Datei\n"
    '    info = infos / f"{ziel.stem}.trashinfo"\n',
    "Der Zettel liegt unter einem anderen Namen als die Datei im Trash. Die "
    "XDG-Zuordnung ist `files/X` <-> `info/X.trashinfo`; stimmt sie nicht, "
    "findet niemand die Herkunft -- die Datei ist im Papierkorb verwaist. "
    "Der Broker selbst merkt nichts davon: sein `info.is_file()` prueft "
    "genau die falsche Datei (K1).")

# -- K2: Ueberschreiben -> `cp --reflink` in die Undo-Ablage -----------------

ersetze(
    "kopie-ohne-reflink", UNDO,
    '    e = lauf(["cp", "--reflink=auto", "--preserve=all", "--no-clobber",\n',
    "    # MUTATION: ohne --reflink, also auf jedem Dateisystem eine volle Kopie\n"
    '    e = lauf(["cp", "--preserve=all", "--no-clobber",\n',
    "`--reflink=auto` faellt weg. Auf btrfs/XFS kostet eine Undo-Kopie damit "
    "so viel Platz wie die Datei -- aus einer beilaeufigen Sicherung wird "
    "eine, die man sich abgewoehnt. Der Akzeptanzpunkt nennt `cp --reflink` "
    "woertlich (K2).")

ersetze(
    "kopie-verschiebt", UNDO,
    '    e = lauf(["cp", "--reflink=auto", "--preserve=all", "--no-clobber",\n'
    '              str(quelle), str(ziel)], capture_output=True, text=True,\n'
    '             timeout=60)\n',
    "    # MUTATION: verschoben statt kopiert\n"
    '    e = lauf(["mv", str(quelle), str(ziel)], capture_output=True,\n'
    "             text=True, timeout=60)\n",
    "Die Undo-Ablage bekommt das Original, nicht eine Kopie. Das Artefakt "
    "verifiziert sich sauber -- und die Datei, die gleich ueberschrieben "
    "werden sollte, ist vorher verschwunden. Ein Undo, das die Mutation "
    "vorwegnimmt (K2).")

# -- K3: Git-Verwerfen -> vorher `git stash` --------------------------------

ersetze(
    "stash-nicht-vorher", UNDO,
    '    e = lauf(["git", "-C", str(repo), "stash", "push", "--include-untracked",\n'
    '              "-m", "daimon-undo"], capture_output=True, text=True, timeout=120)\n',
    "    # MUTATION: der Stash wird nicht angelegt, nur behauptet\n"
    "    class e:  # noqa: N801\n"
    "        returncode = 0\n"
    '        stderr = ""\n',
    "`git stash push` wird nicht mehr abgesetzt. Der Broker meldet trotzdem "
    "ein Artefakt und laesst die Mutation zu -- die verworfene Arbeit ist "
    "weg, und der Stash, der sie haette halten sollen, existiert nie. Der "
    "Akzeptanzpunkt verlangt den Stash VORHER (K3).")

ersetze(
    "stash-zaehlt-nicht", UNDO,
    "    if len(zeilen) != anzahl_vorher + 1:\n"
    "        # \"No local changes to save\" liefert ebenfalls 0. Ohne diese Zaehlung\n"
    "        # haetten wir ein Artefakt behauptet, das es nicht gibt.\n"
    "        raise UndoFehler(\n"
    "            \"git stash hat nichts abgelegt -- kein Artefakt, keine Mutation\")\n"
    "    return Artefakt(art=\"git-stash\", pfad=None, quelle=repo, groesse=None,\n"
    "                    verifiziert=True, hinweis=zeilen[0])\n",
    "    # MUTATION: der Rueckgabewert 0 genuegt, gezaehlt wird nicht mehr\n"
    "    return Artefakt(art=\"git-stash\", pfad=None, quelle=repo, groesse=None,\n"
    "                    verifiziert=True, hinweis=zeilen[0] if zeilen else \"\")\n",
    "Die Zaehlung der Stash-Eintraege faellt weg. `git stash` meldet auch "
    "dann 0, wenn es NICHTS abgelegt hat ('No local changes to save') -- "
    "das Artefakt ist dann eine Behauptung, und die Mutation laeuft "
    "ungeschuetzt (K4).")

# -- K4: das Artefakt wird nach dem Anlegen VERIFIZIERT ---------------------

ersetze(
    "verifikation-fort", UNDO,
    '    """Lesbar und in erwarteter Groesse -- gemessen, nicht geglaubt."""\n',
    '    """Lesbar und in erwarteter Groesse -- gemessen, nicht geglaubt."""\n'
    "    return  # MUTATION: nichts wird gemessen\n",
    "Die Verifikation tut nichts mehr. Ein `cp`, das 0 meldet und auf einem "
    "vollen Dateisystem acht Bytes geschrieben hat, gilt damit als Undo -- "
    "'wir haben ein Backup' statt 'wir haben es versucht'. Das ist der "
    "vierte Akzeptanzpunkt, vollstaendig entkernt (K4).")

ersetze(
    "groesse-egal", UNDO,
    "    if groesse != erwartet:\n",
    "    if False:  # MUTATION: die Groesse ist egal\n",
    "Nur noch Lesbarkeit wird geprueft, die Groesse nicht mehr. Eine "
    "abgeschnittene Kopie ist lesbar -- sie enthaelt bloss nicht die Datei. "
    "Der Akzeptanzpunkt nennt beides: lesbar UND erwartete Groesse (K4).")

# -- K5: schlaegt die Vorbereitung fehl, wird die Mutation abgebrochen ------

ersetze(
    "trash-grenze-egal", UNDO,
    "    if not _gleiches_dateisystem(quelle.parent, dateien):\n",
    "    if False:  # MUTATION: die Dateisystemgrenze ist egal\n",
    "Die Grenzpruefung entfaellt und `shutil.move` kopiert-und-loescht ueber "
    "die Grenze. Genau dazwischen liegt der Moment, in dem beides halb "
    "passiert ist -- und der Verifikationsabsatz verlangt fuer diesen Fall "
    "ausdruecklich, dass die Ursprungsdatei unveraendert bleibt (K5).")
ersetze(
    "trash-grenze-egal", UNDO,
    "    os.replace(quelle, ziel)\n",
    "    shutil.move(str(quelle), str(ziel))  # MUTATION: ueber die Grenze\n",
    "Die Grenzpruefung entfaellt und `shutil.move` kopiert-und-loescht ueber "
    "die Grenze. Genau dazwischen liegt der Moment, in dem beides halb "
    "passiert ist -- und der Verifikationsabsatz verlangt fuer diesen Fall "
    "ausdruecklich, dass die Ursprungsdatei unveraendert bleibt (K5).")

ersetze(
    "undo-fehler-verschluckt", UNDO,
    "    if int(getattr(e, \"returncode\", 1)) != 0:\n"
    "        raise UndoFehler(\n"
    "            f\"Kopie nach {ziel} fehlgeschlagen: \"\n"
    "            f\"{(getattr(e, 'stderr', '') or '').strip()[:160]}\")\n",
    "    if int(getattr(e, \"returncode\", 1)) != 0:\n"
    "        # MUTATION: gemeldet wird ein Artefakt ohne Verifikation\n"
    "        return Artefakt(art=\"kopie\", pfad=ziel, quelle=quelle,\n"
    "                        groesse=groesse, verifiziert=False,\n"
    "                        hinweis=\"cp fehlgeschlagen\")\n",
    "Der Fehlschlag von `cp` wirft nicht mehr, sondern gibt ein Artefakt mit "
    "`verifiziert=False` zurueck. Der Aufrufer bekommt keine Ausnahme, die "
    "er uebersehen koennte -- er bekommt ein Feld, und genau das vergisst "
    "man. Die Mutation laeuft ungeschuetzt weiter, obwohl das "
    "Ziel-Dateisystem voll war (K5).")

# -- K6: Herabstufung erst NACH der Verifikation ----------------------------

ersetze(
    "verifiziert-vor-der-pruefung", UNDO,
    "    _verifizieren(ziel, groesse)\n"
    '    return Artefakt(art="kopie", pfad=ziel, quelle=quelle, groesse=groesse,\n'
    "                    verifiziert=True)\n",
    "    # MUTATION: erst herabstufen, dann messen\n"
    '    artefakt = Artefakt(art="kopie", pfad=ziel, quelle=quelle,\n'
    "                        groesse=groesse, verifiziert=True)\n"
    "    _verifizieren(ziel, groesse)\n"
    "    return artefakt\n",
    "Das Artefakt gilt als `verifiziert`, bevor irgendetwas gemessen wurde. "
    "Hier faellt es noch nicht auf, weil die Pruefung gleich danach kommt -- "
    "aber die Reihenfolge ist die ganze Zusage des sechsten "
    "Akzeptanzpunktes, und wer sie einmal dreht, verschiebt sie beim "
    "naechsten Mal weiter (K6).")

# -- K7: der Zulauf ---------------------------------------------------------

ersetze(
    "zulauf-fort", HUB,
    "                sprechen=self._sprechen,\n"
    "                undo=self._undo_vorbereiten)\n",
    "                # MUTATION: kein Undo im Betrieb\n"
    "                sprechen=self._sprechen)\n",
    "Der Koordinator bekommt kein `undo=` mehr gereicht. `self.undo is None`, "
    "der ganze Undo-Hop wird uebersprungen -- und die Zusage 'schlaegt die "
    "Vorbereitung fehl, wird die Mutation abgebrochen' hat im Betrieb keinen "
    "Fall mehr, in dem sie greift. Das Modul bleibt vollstaendig, gruen "
    "geprueft und ohne Aufrufer: die Bauform, die dieses Repo sechsmal "
    "getroffen hat (K7).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-4.8: ${#mutanten[@]} Mutanten erzeugt."
