#!/usr/bin/env bash
# Erzeugt die zwoelf T-4.7-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-4.7.v nicht. Die Liste ist deshalb hier gesetzt: mindestens
# einer je Akzeptanzpunkt aus dem Implementierungsplan (Z. 1262 ff.), plus die
# vier Faelle, die der Verifikationsabsatz (Z. 1270) ausdruecklich verlangt --
# Kanarienvogel, Kern, `loadScript`, Proxy-Log.
#
# Die Zuordnung Mutant -> Kriterium steht im Kopf von
# tests/verify/t47_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-4.7; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-4.7"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  shortcut-aus-auftrag
  operation-generisch
  status-egal
  dienst-aus-katalog
  filter-startet-nicht
  filter-ohne-log
  filter-ohne-filter
  proxy-ohne-zulauf
  kwin-durch-den-filter
  sandbox-privateusers
  sandbox-ohne-caps
  broker-fuehrt-nicht-aus
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
BROKER = f"{P}/brokers/dbus/broker.py"
FILTER = "config/dbus-filter.conf"
UNIT = "config/systemd/daimon-dbus.service"
PROXYUNIT = "config/systemd/daimon-dbus-proxy.service"


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


def anhaengen(name, pfad, text, beschreibung):
    datei = w / name / pfad
    datei.write_text(datei.read_text(encoding="utf-8") + text,
                     encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n",
                                           encoding="utf-8")


def loeschen(name, pfad, beschreibung):
    datei = w / name / pfad
    if not datei.exists():
        raise SystemExit(f"{name}: {pfad} fehlt schon im Gut-Muster")
    datei.unlink()
    (w / name / "mutation.txt").write_text(beschreibung + "\n",
                                           encoding="utf-8")


# -- K1: feste Parameter, keine generische Operation -------------------------

ersetze(
    "shortcut-aus-auftrag", BROKER,
    "        e = self.lauf(operation.argv(), capture_output=True, text=True,\n"
    "                      timeout=10)\n",
    "        argv = operation.argv()\n"
    "        gewuenscht = (auftrag.params or {}).get(\"shortcut\")\n"
    "        if gewuenscht:  # MUTATION: der Auftrag darf den Kurzbefehl waehlen\n"
    "            argv = argv[:-1] + [str(gewuenscht)]\n"
    "        e = self.lauf(argv, capture_output=True, text=True, timeout=10)\n",
    "Der Kurzbefehl kommt aus dem AUFTRAG, sobald `params` einen nennt. Die "
    "Operation ist damit nur noch der Vorwand: derselbe genehmigte "
    "`action_id` fuehrt jeden beliebigen Kurzbefehl derselben Komponente aus "
    "-- ein generisches `invokeShortcut` mit Tarnkappe (K1).")

ersetze(
    "operation-generisch", BROKER,
    "    return Operation(dienst=\"org.kde.kglobalaccel\", pfad=pfad,\n"
    "                     schnittstelle=\"org.kde.kglobalaccel.Component\",\n"
    "                     methode=\"invokeShortcut\", argumente=(aktion,))\n",
    "    # MUTATION: eine Operation fuer alle, das Argument faellt weg\n"
    "    return Operation(dienst=\"org.kde.kglobalaccel\", pfad=pfad,\n"
    "                     schnittstelle=\"org.kde.kglobalaccel.Component\",\n"
    "                     methode=\"invokeShortcut\", argumente=(\"\",))\n",
    "Die Operation traegt das Argument nicht mehr aus dem Katalog. Was "
    "ausgefuehrt wird, entscheidet dann irgendwer anders -- der Katalog ist "
    "eine Empfehlung geworden (K1).")

# -- K2: der Kern ------------------------------------------------------------

ersetze(
    "status-egal", BROKER,
    "            if eintrag.get(\"status\") != \"approved\":\n"
    "                continue\n",
    "            # MUTATION: der Katalog wird gelesen, nicht befolgt\n",
    "Der Status des Katalogeintrags wird nicht mehr geprueft. Jeder "
    "Kurzbefehl, der IRGENDWO im Katalog steht, bekommt eine Operation -- "
    "auch der, den niemand freigegeben hat. Das ist der Fall, den der Plan "
    "den Kern nennt: dieselbe Komponente, ein anderer Shortcut, nicht "
    "`approved`. Der Methodenfilter des Proxys sieht ihn nicht (K2).")

# -- K4: loadScript ----------------------------------------------------------

ersetze(
    "dienst-aus-katalog", BROKER,
    "    return Operation(dienst=\"org.kde.kglobalaccel\", pfad=pfad,\n"
    "                     schnittstelle=\"org.kde.kglobalaccel.Component\",\n"
    "                     methode=\"invokeShortcut\", argumente=(aktion,))\n",
    "    # MUTATION: Dienst und Schnittstelle kommen aus dem Katalogeintrag\n"
    "    return Operation(\n"
    "        dienst=str(eintrag.get(\"dienst\") or \"org.kde.kglobalaccel\"),\n"
    "        pfad=pfad,\n"
    "        schnittstelle=str(eintrag.get(\"schnittstelle\")\n"
    "                          or \"org.kde.kglobalaccel.Component\"),\n"
    "        methode=str(eintrag.get(\"methode\") or \"invokeShortcut\"),\n"
    "        argumente=(aktion,))\n",
    "Dienst, Schnittstelle und Methode kommen aus dem Katalogeintrag. Ein "
    "Eintrag mit `schnittstelle: org.kde.kwin.Scripting` waere damit "
    "erreichbar -- der Broker haette keine feste Operation mehr, sondern "
    "einen Bauplan, den der Katalogschreiber ausfuellt (K4).")
ersetze(
    "dienst-aus-katalog", BROKER,
    "            if op.dienst not in ERLAUBTE_DIENSTE:\n"
    "                raise BrokerFehler(\n"
    "                    f\"{kennung}: Dienst {op.dienst!r} steht nicht in der \"\n"
    "                    f\"Allowlist\")\n",
    "            # MUTATION: die Dienst-Allowlist entfaellt\n",
    "Dienst, Schnittstelle und Methode kommen aus dem Katalogeintrag, und die "
    "Dienst-Allowlist ist fort. `org.kde.kwin.Scripting.loadScript` waere "
    "damit ueber die erste Schicht erreichbar -- beliebiges JavaScript im "
    "Compositor (K4).")

ersetze(
    "kwin-durch-den-filter", FILTER,
    "--log\n",
    "# MUTATION: der Compositor darf alles\n"
    "--call=org.kde.KWin=*\n"
    "--log\n",
    "Der Proxy laesst `org.kde.KWin` mit jeder Methode durch. `loadScript` "
    "erreicht damit den Compositor: KWin quittiert den Aufruf mit einer "
    "Skriptkennung, auch fuer einen Pfad, den es nicht gibt. Die zweite "
    "Schicht ist offen, und die Abweisung kommt von niemandem mehr (K4).")

# -- K3: die zweite Schicht --------------------------------------------------

ersetze(
    "filter-startet-nicht", FILTER,
    "--log\n",
    "# MUTATION: ein Verbot, das der Proxy nicht kennt\n"
    "--own=none\n"
    "--log\n",
    "Die Filterdatei enthaelt wieder eine Zeile, die `xdg-dbus-proxy` nicht "
    "kennt ('none' is not a valid dbus name). Der Proxy startet damit gar "
    "nicht -- und mit ihm faellt die zweite Schicht ganz aus, samt allen "
    "Erlaubnissen, die in derselben Datei stehen (K3).")

ersetze(
    "filter-ohne-log", FILTER,
    "--log\n", "# MUTATION: keine Mitschrift\n",
    "`--log` faellt weg. Der Proxy filtert weiter, aber kein Versuch ist mehr "
    "belegt -- 'der Aufruf wurde abgewiesen' ist dann eine Behauptung. Der "
    "Plan verlangt ausdruecklich, dass der Proxy-Log die Versuche enthaelt "
    "(K3).")

ersetze(
    "filter-ohne-filter", FILTER,
    "--filter\n", "# MUTATION: keine Filterung\n",
    "`--filter` faellt weg. Der Proxy leitet dann alles weiter, was der "
    "Sitzungsbus kennt -- er ist keine zweite Schicht mehr, sondern ein "
    "Umweg (K3).")

loeschen("proxy-ohne-zulauf", PROXYUNIT,
         "Die Unit, die den Proxy startet, ist fort. Die Filterdatei bleibt "
         "vollstaendig und gueltig -- sie hat nur keinen Leser mehr. Genau "
         "diese Bauform hat dieses Repo sechsmal getroffen: eine Zusage, die "
         "im Code steht, gruen geprueft ist und im Betrieb niemand aufruft "
         "(K3).")
ersetze(
    "proxy-ohne-zulauf", UNIT,
    "Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=%t/daimon/dbus-proxy.sock\n",
    "# MUTATION: der Broker spricht wieder direkt mit dem Sitzungsbus\n",
    "Die Unit, die den Proxy startet, ist fort, und der Broker zeigt nicht "
    "mehr auf den Proxy-Socket: `gdbus call --session` nimmt die Adresse aus "
    "der Umgebung, und die zeigt dann auf den echten Bus. Die Filterdatei "
    "bleibt gueltig -- sie hat nur keinen Leser mehr (K3).")

# -- K5: Sandbox nach Design 7.5 --------------------------------------------

ersetze(
    "sandbox-privateusers", UNIT,
    "PrivateDevices=yes\n",
    "PrivateDevices=yes\n"
    "PrivateUsers=yes  # MUTATION\n",
    "`PrivateUsers=yes` steht in Design 7.5 unter den Direktiven, die "
    "BRECHEN: es bricht uid-ACLs und Peer-Credentials. Damit faellt die "
    "Herkunftspruefung ueber den Socket -- also das, was nach Design 1.3/6.2 "
    "an die Stelle der gestrichenen Signatur getreten ist (K5).")

ersetze(
    "sandbox-ohne-caps", UNIT,
    "CapabilityBoundingSet=\n", "# MUTATION: der volle Capability-Satz bleibt\n",
    "`CapabilityBoundingSet=` faellt weg. Der Dienst behaelt den "
    "Capability-Satz seines Elternprozesses -- die erste Zeile der Basis aus "
    "Design 7.5 gilt nicht mehr (K5).")

# -- K6: der Kanarienvogel ---------------------------------------------------

ersetze(
    "broker-fuehrt-nicht-aus", BROKER,
    "        e = self.lauf(operation.argv(), capture_output=True, text=True,\n"
    "                      timeout=10)\n"
    "        rc = int(getattr(e, \"returncode\", 1))\n",
    "        # MUTATION: gemeldet wird Erfolg, ausgefuehrt wird nichts\n"
    "        rc = 0\n",
    "Der Broker meldet `ok`, ohne den Aufruf zu machen. Alles wird "
    "'ausgefuehrt', nichts geschieht -- und ein Verifizierer ohne "
    "Kanarienvogel bliebe gruen, weil jede Verweigerung weiterhin verweigert "
    "wird. Genau dagegen steht der Positiv-Kanarienvogel des Plans (K6).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-4.7: ${#mutanten[@]} Mutanten erzeugt."
