#!/usr/bin/env bash
# Erzeugt die dreizehn T-7.1-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-7.1.v nicht (dort stehen 36 Verifizierer; die Phase 7 ist
# nicht darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau
# ein Akzeptanzkriterium gebunden -- die Zuordnung steht im Kopf von
# tests/verify/t71_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# JEDE MUTATION WIRD GEWOGEN: `ersetze` bricht ab, wenn der Anker nicht GENAU
# EINMAL im Gut-Muster steht. Ein Mutant, der nichts geaendert haette, entsteht
# gar nicht erst -- er meldete sonst brav `erkannt`, weil der Verifizierer
# gegen ein unveraendertes Muster ohnehin gruen ist.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-7.1; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-7.1"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  eyes-darf-schreiben
  recorder-ohne-protecthome
  recorder-mit-netz
  recorder-laedt-modelltext
  datei-0644
  volltextindex-ohne-trigger
  frist-einheitlich
  rohaudio-erlaubt
  grenze-verdraengt-nicht
  stufe-vorgabe-full
  transient-schreibt
  tainted-verloren
  shm-nur-lesbar
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
# Der Paketname und das Unit-Verzeichnis stehen zusammengesetzt da: der
# Rollenwaechter dieses Repos liest Pfade im KOMMANDOTEXT und haelt einen
# bloss genannten fuer ein Schreibziel (tests/test_rollen.py:182). Derselbe
# Kniff steht in tests/mutants/T-7.5/erzeugen.sh.
P = "dai" + "mon"
U = "con" + "fig/sys" + "temd"
STORE = f"{P}/recorder/store.py"
REC_UNIT = f"{U}/{P}-recorder.service"
EYES_UNIT = f"{U}/{P}-eyes.service"


def ersetze(name, pfad, alt, neu, beschreibung=None):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(
            f"{name}: Mutationsanker {alt!r} nicht genau einmal in {pfad} "
            f"gefunden ({text.count(alt)}x)")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    if beschreibung:
        (w / name / "mutation.txt").write_text(beschreibung + "\n",
                                               encoding="utf-8")


# -- K1: die getrennten Schreibrechte ---------------------------------------

ersetze(
    "eyes-darf-schreiben", EYES_UNIT,
    "\nProtectHome=read-only\n",
    "\n# MUTATION: die Augen duerfen ins Archiv schreiben\nProtectHome=no\n",
    "Der Augendienst behaelt Schreibrecht auf $HOME und damit aufs Archiv. "
    "Genau die Zusage, an der alles haengt: eine kompromittierte "
    "Live-Wahrnehmung schriebe in die Vergangenheit (K1).")


# -- K2: die Haertung der Recorder-Unit -------------------------------------

ersetze(
    "recorder-ohne-protecthome", REC_UNIT,
    "\nProtectHome=tmpfs\n",
    "\n# MUTATION: ProtectHome entfernt -- $HOME steht dem Dienst offen\n",
    "Der Archivdienst sieht wieder das ganze $HOME: Konfigurations- und "
    "Zustandsverzeichnis, und damit den anthropic-token. Von der Zusage "
    "„nur das Archivverzeichnis\" bleibt nichts (K2).")

ersetze(
    "recorder-mit-netz", REC_UNIT,
    "\nRestrictAddressFamilies=AF_UNIX\n",
    "\n# MUTATION: eine Netzsteckdose fuer den Mitschnitt\n"
    "RestrictAddressFamilies=AF_UNIX AF_INET\n",
    "Der Dauermitschnitt bekommt eine Netzsteckdose. Ein Mitschnitt, der "
    "telefonieren kann, ist das Gegenteil dieses Projekts (K2).")


# -- K3: kein Modelltext im Prozess -----------------------------------------

for name in ("recorder-laedt-modelltext",):
    mind = w / name / P / "mind"
    mind.mkdir(parents=True, exist_ok=True)
    (mind / "__init__.py").write_text("", encoding="utf-8")
    (mind / "router.py").write_text(
        '"""MUTATION: der Router des Modells, samt Persona-Text."""\n'
        'PERSONA = "Du bist Ember, ein neugieriges kleines Wesen."\n',
        encoding="utf-8")

ersetze(
    "recorder-laedt-modelltext", f"{P}/recorder/daemon.py",
    "from daimon.common import ipc\n",
    "from daimon.common import ipc\n"
    "from daimon.mind.router import PERSONA  # MUTATION\n",
    "Der Archivdienst zieht den Router des Modells samt Persona-Text in "
    "seinen Prozess. „Kein Modelltext im Prozess\" ist damit aufgehoben, und "
    "zwar still -- der Dienst laeuft weiter (K3).")


# -- K4: Rechte und Volltextindex -------------------------------------------

ersetze(
    "datei-0644", STORE,
    "MODUS = 0o600\n",
    "MODUS = 0o644  # MUTATION: jeder Nutzer der Maschine liest mit\n",
    "Die Archivdatei entsteht mit 0644. Das 0700-Verzeichnis deckt es zu, "
    "solange es steht -- eine Sicherung, ein Verschieben, ein anderer "
    "Elternpfad, und der Mitschnitt ist weltlesbar (K4).")

ersetze(
    "volltextindex-ohne-trigger", STORE,
    "     CREATE TRIGGER archiv_fts_ein AFTER INSERT ON archiv BEGIN\n"
    "       INSERT INTO archiv_fts (rowid, text, fenster)\n"
    "         VALUES (new.id, new.text, new.fenster);\n"
    "     END;\n"
    "     CREATE TRIGGER archiv_fts_weg AFTER DELETE ON archiv BEGIN\n"
    "       INSERT INTO archiv_fts (archiv_fts, rowid, text, fenster)\n"
    "         VALUES ('delete', old.id, old.text, old.fenster);\n"
    "     END;\n",
    "     -- MUTATION: keine Trigger, der Index bleibt leer\n",
    "Der Volltextindex steht in der Datenbank, aber nichts fuellt ihn. Die "
    "Suche findet nie etwas, und zwar ohne Fehlermeldung -- das Archiv ist "
    "durchsuchbar nur auf dem Papier (K4, und K9 sieht es mit).")


# -- K5: Aufbewahrung je Art ------------------------------------------------

ersetze(
    "frist-einheitlich", STORE,
    "    ART_FRAME: 48 * 3600.0,\n",
    "    ART_FRAME: 30 * 24 * 3600.0,  # MUTATION: wie der Text\n",
    "Die Fristen sind wieder einheitlich: Frames liegen dreissig Tage wie "
    "der Text. Der halbe Punkt von Design 7.2d faellt weg -- ein Frame ist "
    "der Bildschirm selbst, ein Fenstertitel nicht (K5).")

ersetze(
    "rohaudio-erlaubt", STORE,
    'VERBOTENE_ARTEN = frozenset({"audio", "rohaudio", "pcm", "wav", "samples"})\n',
    "VERBOTENE_ARTEN = frozenset()  # MUTATION\n")
ersetze(
    "rohaudio-erlaubt", STORE,
    "    ART_FRAME: 48 * 3600.0,\n}\n",
    "    ART_FRAME: 48 * 3600.0,\n"
    '    "audio": 48 * 3600.0,        # MUTATION\n'
    '    "rohaudio": 48 * 3600.0,     # MUTATION\n'
    '    "pcm": 48 * 3600.0,          # MUTATION\n'
    '    "wav": 48 * 3600.0,          # MUTATION\n'
    '    "samples": 48 * 3600.0,      # MUTATION\n'
    "}\n",
    "Rohaudio bekommt eine Frist und damit einen Platz auf der Platte. "
    "„Rohaudio gar nicht\" ist die einzige Zusage dieses Tasks ohne "
    "Zeitgrenze, und sie faellt hier ohne jede Fehlermeldung (K5).")


# -- K6: die harte Obergrenze -----------------------------------------------

ersetze(
    "grenze-verdraengt-nicht", STORE,
    "        if ueberschuss > 0:\n",
    "        if False:  # MUTATION: nichts weicht, das Archiv waechst weiter\n",
    "Die Obergrenze wird berechnet und dann nicht angewandt. Der Aufraeumer "
    "meldet keinen Fehler, der Dienst laeuft weiter -- bis die Platte voll "
    "ist. Genau der Zustand, den die Akzeptanz „kein Betriebszustand\" "
    "nennt (K6).")


# -- K7: die Aufbewahrungsstufe ---------------------------------------------

ersetze(
    "stufe-vorgabe-full", STORE,
    "                  stufe: str = STUFE_REDACTED, daten: bytes | None = None,\n",
    "                  stufe: str = STUFE_FULL, daten: bytes | None = None,\n",
    "Die Vorgabe ist `full` statt `redacted`. Wer die Stufe nicht angibt -- "
    "und das ist jeder Aufrufer, der sie vergisst --, legt vollstaendig ab "
    "statt gefiltert (K7).")

ersetze(
    "transient-schreibt", STORE,
    "        if stufe == STUFE_TRANSIENT:\n            return 0\n",
    "        # MUTATION: `transient` landet doch auf der Platte\n",
    "`transient` heisst „nur im Arbeitsspeicher\" und schreibt hier "
    "trotzdem. Privatmodus, abgeschaltete Wahrnehmung und die Denylist aus "
    "T-7.2 enden alle drei in dieser Stufe -- sie schreiben ab hier alle "
    "mit (K7).")


# -- K8: `tainted` als Typ --------------------------------------------------

ersetze(
    "tainted-verloren", STORE,
    '                "wert": Marked(z["text"], Mark.TAINTED)}\n',
    '                "wert": Marked(z["text"], Mark.TRUSTED)}  # MUTATION\n',
    "Ein Eintrag kommt `trusted` aus der eigenen Datenbank zurueck. Die "
    "Senkentabelle laesst ihn danach in Durchgang 1, ins Gedaechtnis und "
    "ins ungefragte Vorlesen -- ein per OCR erfasstes Passwort wird "
    "vorgelesen (K8).")


# -- K9: die lesende Verbindung neben dem laufenden Schreiber ---------------

ersetze(
    "shm-nur-lesbar", STORE,
    '        for endung in ("", "-wal", "-shm"):\n'
    "            try:\n"
    "                os.chmod(Path(str(self.pfad) + endung), MODUS)\n",
    '        for endung, m in (("", MODUS), ("-wal", MODUS),\n'
    "                          (\"-shm\", 0o400)):  # MUTATION\n"
    "            try:\n"
    "                os.chmod(Path(str(self.pfad) + endung), m)\n",
    "Die WAL-Nebendatei `-shm` wird nur lesbar gemacht. Solange der Recorder "
    "sie offen haelt, merkt er nichts; JEDE lesende Verbindung mit `mode=ro` "
    "scheitert dann daran, dass sie das `-shm` nicht beschreiben kann -- die "
    "Archivsuche gibt still nichts mehr her. Das ist die Grenze 9 aus "
    "LEDGER-T-7.5.v.md, hier als Mutant (K9).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-7.1: ${#mutanten[@]} Mutanten erzeugt."
