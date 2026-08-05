#!/usr/bin/env bash
# Erzeugt die sechs bindenden T-3.11-Mutanten frisch aus dem Gut-Muster.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-3.11"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  mind-behaelt-token
  koerper-geloggt
  kontingent-nicht-verlangt
  ticket-mehrfach
  proxy-aus-der-umgebung
  tls-ungeprueft
)
for name in "${mutanten[@]}"; do
  mkdir -p "$TMP/$name"
  cp -a "$GUT/." "$TMP/$name/"
done

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

w = Path(sys.argv[1])

def ersetze(name, pfad, alt, neu, beschreibung):
    datei = w / name / pfad
    text = datei.read_text(encoding="utf-8")
    if text.count(alt) != 1:
        raise SystemExit(f"{name}: Mutationsanker {alt!r} nicht genau einmal gefunden")
    datei.write_text(text.replace(alt, neu), encoding="utf-8")
    (w / name / "mutation.txt").write_text(beschreibung + "\n", encoding="utf-8")

ersetze(
    "mind-behaelt-token", "config/systemd/daimon-mind.service",
    "Type=simple\n", "Type=simple\nEnvironment=ANTHROPIC_API_KEY=sk-ant-t311-prueftoken-A-7f93c4\n",
    "Mind erhält den API-Token zusätzlich in seiner Umgebung (K2/K3).")

ersetze(
    "koerper-geloggt", "daimon/brokers/egress/__main__.py",
    "        try:\n            status, antwort, dauer = transport(self.ziel, body, self.token)\n",
    "        log(f\"koerper={body.decode('utf-8')}\", self.token)  # MUTATION\n"
    "        try:\n            status, antwort, dauer = transport(self.ziel, body, self.token)\n",
    "Egress schreibt den vollständigen Anfragekörper ins Log (K9).")

ersetze(
    "kontingent-nicht-verlangt", "daimon/brokers/egress/__main__.py",
    "        if not isinstance(tid, str) or not tid:\n            return self.absage(\"kein_ticket\", \"ticket fehlt oder ist leer\")\n",
    "        ohne_ticket = not isinstance(tid, str) or not tid  # MUTATION\n"
    "        if ohne_ticket:\n            tid = \"OHNE-TICKET\"\n",
    "Anfragen ohne Ticket werden nicht mehr sofort abgelehnt (K4).")
ersetze(
    "kontingent-nicht-verlangt", "daimon/brokers/egress/__main__.py",
    "        if not eingel.get(\"ok\"):\n",
    "        if not eingel.get(\"ok\") and not ohne_ticket:  # MUTATION\n",
    "Anfragen ohne Ticket umgehen zusätzlich die Hub-Ablehnung (K4).")

ersetze(
    "ticket-mehrfach", "daimon/hub/daemon.py",
    "                        if t:\n                            t[\"verbraucht\"] = True\n",
    "                        if t:\n                            pass  # MUTATION: Ticket bleibt offen\n",
    "Ein eingelöstes Ticket bleibt offen und kann mehrfach benutzt werden (K5).")

ersetze(
    "proxy-aus-der-umgebung", "daimon/brokers/egress/__main__.py",
    "    u = urllib.parse.urlsplit(url)\n",
    "    if os.environ.get(\"HTTPS_PROXY\") and url.startswith(\"https:\"):\n"
    "        raise OSError(\"HTTPS_PROXY wurde beachtet\")  # MUTATION\n"
    "    u = urllib.parse.urlsplit(url)\n",
    "Egress beachtet HTTPS_PROXY aus seiner Umgebung (K11).")

ersetze(
    "tls-ungeprueft", "daimon/brokers/egress/__main__.py",
    "                                                  context=ssl.create_default_context())\n",
    "                                                  context=ssl._create_unverified_context())  # MUTATION\n",
    "Die TLS-Zertifikatsprüfung ist abgeschaltet (K12).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-3.11: ${#mutanten[@]} Mutanten erzeugt."
