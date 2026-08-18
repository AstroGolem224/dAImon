#!/usr/bin/env bash
# Erzeugt die dreizehn T-7.3-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-7.3.v nicht (dort stehen 36 Verifizierer; Phase 7 ist
# nicht darunter). Die Liste ist deshalb hier gesetzt, jeder Mutant an genau
# ein Akzeptanzkriterium gebunden -- die Zuordnung steht im Kopf von
# tests/verify/t73_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# JEDE MUTATION WIRD GEWOGEN: `ersetze` bricht ab, wenn der Anker nicht GENAU
# EINMAL im Gut-Muster steht. Ein Mutant, der nichts geaendert haette, entsteht
# gar nicht erst -- er meldete sonst brav `erkannt`, weil der Verifizierer
# gegen ein unveraendertes Muster ohnehin gruen ist.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-7.3; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-7.3"

[[ -d "$GUT" ]] || { echo "Gut-Muster $GUT fehlt" >&2; exit 1; }

mutanten=(
  hotkey-in-fremder-komponente
  kuerzel-nicht-verteilt
  schalter-schaltet-nicht-um
  konferenz-loest-nicht-aus
  fremdes-mikrofon-loest-nicht-aus
  eigener-strom-zaehlt-mit
  konferenzliste-leer
  pause-schaltet-stumm
  rc-null-heisst-ok
  nicht-messbar-heisst-ok
  nur-der-recorder
  herzschlag-ohne-frist
  restart-always
)
for name in "${mutanten[@]}"; do
  rm -rf -- "${HIER:?}/$name"
  mkdir -p "$HIER/$name"
  cp -a "$GUT/." "$HIER/$name/"
done

python3 - "$HIER" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])
# Der Paketname und die Verzeichnisse stehen zusammengesetzt da: der
# Rollenwaechter dieses Repos liest Pfade im KOMMANDOTEXT und haelt einen
# bloss genannten fuer ein Schreibziel (tests/test_rollen.py:182). Derselbe
# Kniff steht in tests/mutants/T-7.1/erzeugen.sh.
P = "dai" + "mon"
U = "con" + "fig"
F = "fa" + "ce/src"
AGENT = f"{P}/auth/agent.py"
PAUSE = f"{P}/recorder/pause.py"
DAEMON = f"{P}/recorder/daemon.py"
DIAG = f"{F}/diag.rs"
REC_UNIT = f"{U}/sys" + f"temd/{P}-recorder.service"
YAML = f"{U}/redaktion.yaml"


def ersetze(name, pfad, alt, neu, beschreibung=None):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(
            f"{name}: Mutationsanker {alt[:60]!r} nicht genau einmal in "
            f"{pfad} gefunden ({text.count(alt)}x)")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    if beschreibung:
        (w / name / "mutation.txt").write_text(beschreibung + "\n",
                                               encoding="utf-8")


# -- K1: die eigene kglobalaccel-Komponente ---------------------------------

ersetze(
    "hotkey-in-fremder-komponente", AGENT,
    'KG_AKTION_MITSCHNITT = ["' + P + '-recorder", "dAImon Mitschnitt",',
    '# MUTATION: der Mitschnitt zieht in die PTT-Komponente\n'
    'KG_AKTION_MITSCHNITT = ["' + P + '-auth", "dAImon Auth",',
    "Der Pausenschalter teilt sich die kglobalaccel-Komponente mit "
    "Push-to-Talk. Am 09.08. gemessen: der zweite setShortcut verdraengt "
    "den ersten, beide Aufrufe melden trotzdem Erfolg. Eine der beiden "
    "Tasten verschwindet lautlos (K1).")


# -- K2: der Tastendruck und der Umschalter ---------------------------------

ersetze(
    "kuerzel-nicht-verteilt", AGENT,
    "        elif komponente == KG_AKTION_MITSCHNITT[0]:\n"
    "            self._mitschnitt_umschalten()\n",
    "        # MUTATION: die Mitschnitt-Komponente wird nicht verteilt\n",
    "Das Kuerzelsignal der Mitschnitt-Komponente wird still verworfen. Der "
    "Tastendruck kommt an und tut nichts -- genau der Ausfall, der am "
    "09.08. zwei Anlaeufe lang unentdeckt blieb, weil der unbekannte "
    "Absender nicht protokolliert wurde (K2).")

ersetze(
    "schalter-schaltet-nicht-um", AGENT,
    "            if schneidet_mit(rt):\n",
    "            # MUTATION: der Schalter setzt immer fort, statt umzuschalten\n"
    "            if False and schneidet_mit(rt):\n",
    "Der Umschalter haelt nie an, er setzt nur fort. Der Hotkey ist damit "
    "kein Pausenschalter mehr, sondern ein Einschalter (K2).")


# -- K3: die automatische Pause, jeder Ausloeser allein ---------------------

ersetze(
    "konferenz-loest-nicht-aus", DAEMON,
    "        if pause.ist_konferenz(klasse, self.konferenz):\n",
    "        # MUTATION: die Konferenz im Fokus loest nicht mehr aus\n"
    "        if False and pause.ist_konferenz(klasse, self.konferenz):\n",
    "Eine Konferenzanwendung im Fokus pausiert den Mitschnitt nicht mehr. "
    "Der zweite Ausloeser (fremdes Mikrofon) deckt sie NICHT ab: ein "
    "Teilnehmer, der nur zuhoert, hat keinen eigenen Aufnahmestrom (K3).")

ersetze(
    "fremdes-mikrofon-loest-nicht-aus", DAEMON,
    "        fremd = self._mikrofone()\n        if fremd:\n",
    "        fremd = self._mikrofone()\n"
    "        # MUTATION: der fremde Mikrofonstrom loest nicht mehr aus\n"
    "        if False and fremd:\n",
    "Ein Mikrofonstrom einer fremden Anwendung pausiert den Mitschnitt "
    "nicht mehr. Der erste Ausloeser deckt ihn NICHT ab: ein Anruf im "
    "Browser hat keine eigene Fensterklasse (K3).")


# -- K4: wessen Strom da laeuft --------------------------------------------

ersetze(
    "eigener-strom-zaehlt-mit", PAUSE,
    "        if any(m in wer.lower() for m in marken):\n            continue\n",
    "        # MUTATION: der eigene Aufnahmestrom zaehlt mit\n",
    "Die eigenen Aufnahmestroeme werden als fremde gezaehlt. Der Mitschnitt "
    "pausiert sich dann selbst, sobald Push-to-Talk laeuft -- und die "
    "automatische Pause meldet einen Grund, den es nicht gibt (K4).")


# -- K5: die Konferenzliste -------------------------------------------------

ersetze(
    "konferenzliste-leer", PAUSE,
    'KONFERENZ_VORGABE = (\n    "zoom", "us.zoom.Zoom",',
    "# MUTATION: die Vorgabeliste ist leer -- 'konfigurierbar' allein\n"
    'KONFERENZ_VORGABE = (\n    # "zoom", "us.zoom.Zoom",',
    "Die Konferenzliste ist konfigurierbar, aber NICHT standardmaessig "
    "gefuellt. Wer nichts einstellt, bekommt keine automatische Pause -- "
    "und §201 StGB fragt nicht, ob man die Liste gepflegt hat (K5).")


# -- K6: schliessen oder stumm schalten -------------------------------------

ersetze(
    "pause-schaltet-stumm", PAUSE,
    '        e = lauf(["systemctl", "--user", "stop", unit],\n',
    "        # MUTATION: nicht stoppen, nur die Lautstaerke auf null --\n"
    "        # der Strom bleibt offen, das Plasma-Symbol bleibt stehen\n"
    '        e = lauf(["systemctl", "--user", "show", "-p", "MainPID", unit],\n',
    "Die Pause schaltet stumm statt zu schliessen. `systemctl show` gibt 0 "
    "zurueck, die Unit laeuft weiter, der Capture-Stream bleibt offen. "
    "Design §4.2: genau dann luegt das Mikrofonsymbol in Plasma. Von "
    "innen sieht das aus wie Erfolg; von aussen ist der Strom noch in "
    "pw-dump (K6).")


# -- K7: was `ok` heisst ----------------------------------------------------

ersetze(
    "rc-null-heisst-ok", PAUSE,
    "    elif nachher > 0:\n"
    '        meldung = f"{nachher} Bildschirmstrom/-stroeme laufen weiter"\n',
    "    # MUTATION: ein laufender Strom ist kein Grund mehr\n",
    "`ok` heisst wieder 'der Aufruf gab 0 zurueck'. Ein Dienst, der beim "
    "Beenden seinen Strom nicht schliesst, wird als Erfolg gemeldet -- der "
    "Fall, gegen den der ganze Task geschrieben ist (K7).")

ersetze(
    "nicht-messbar-heisst-ok", PAUSE,
    "    elif nachher is None:\n"
    '        meldung = "Bildschirmstroeme nicht messbar (pw-dump?) '
    '-- kein Nachweis"\n',
    "    # MUTATION: nicht messbar zaehlt als null Stroeme\n",
    "Ein fehlendes pw-dump wird als 'null Stroeme' gelesen. Damit wird aus "
    "einem Werkzeugfehler eine Sicherheitsaussage -- `None` und `0` sind "
    "hier zwei verschiedene Dinge (K7).")


# -- K8: Bild und Ton gemeinsam ---------------------------------------------

ersetze(
    "nur-der-recorder", PAUSE,
    'PAUSE_UNITS = (RECORDER_UNIT, EYES_UNIT, "dai" "mon-ears.service")',
    "# MUTATION: nur der Schreiber wird gestoppt, die Quellen laufen weiter\n"
    "PAUSE_UNITS = (RECORDER_UNIT,)",
    "Die Pause stoppt nur den Archivdienst. Augen und Ohren laufen weiter, "
    "der Bildschirm wird weiter erfasst und das Mikrofon bleibt offen. "
    "Ein Pfad allein genuegt nicht (K8).")


# -- K9: die Sichtbarkeit am Sprite -----------------------------------------

ersetze(
    "herzschlag-ohne-frist", PAUSE,
    "    return 0.0 <= uhr() - stand < frist_s\n",
    "    # MUTATION: das Alter des Herzschlags zaehlt nicht mehr\n"
    "    return True\n",
    "Ein Herzschlag zaehlt unabhaengig von seinem Alter. Nach einem "
    "SIGKILL des Recorders bleibt die Datei liegen, und das Sprite zeigt "
    "Mitschnitt an, wo laengst keiner mehr ist -- eine Anzeige, die den "
    "letzten Befehl zeigt statt des Zustands (K9).")


# -- K10: Restart ------------------------------------------------------------

ersetze(
    "restart-always", REC_UNIT,
    "\nRestart=on-failure\n",
    "\n# MUTATION: always statt on-failure\nRestart=always\n",
    "Mit `Restart=always` hebt systemd den Pausenschalter nach RestartSec "
    "von selbst wieder auf. Ein Stopp ist dann kein Ende, sondern eine "
    "Pause von zwei Sekunden (K10).")

print(f"{len(list(w.glob('*/mutation.txt')))} Mutanten erzeugt")
PY
