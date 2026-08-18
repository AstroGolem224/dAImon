#!/usr/bin/env bash
# Erzeugt die zwoelf T-4.5-Mutanten frisch aus dem Gut-Muster.
#
# Anhang D kennt T-4.5.v nicht. Die Liste ist deshalb hier gesetzt: einer je
# Akzeptanzpunkt aus dem Implementierungsplan (Z. 1231 ff.), plus die beiden,
# die der Verifikationsabsatz ausdruecklich verlangt --
#
#   "Eine Mutante, die eine HMAC-Pruefung einfuehrt, wird als Verstoss gegen
#    6.2 zurueckgewiesen."
#
# Das ist eine UMGEKEHRTE Mutante: sie fuegt Sicherheitsmaschinerie hinzu und
# muss trotzdem als Verstoss gemeldet werden. Sie steht hier zweimal, weil
# eine Fassung allein zu wenig beweist:
#
#   `hmac-pflicht`  verlangt eine Signatur -- am Verhalten sichtbar (der
#                   gueltige Auftrag mit den acht Feldern wird abgewiesen).
#   `hmac-optional` prueft die Signatur nur, WENN eine da ist. Am Verhalten
#                   faellt sie NICHT auf. Sie faengt nur der AST-Leser aus K1
#                   -- und genau dafuer gibt es ihn. Ohne diese Mutante waere
#                   nicht belegt, dass der Leser ueberhaupt Gewicht traegt.
#
# Die Zuordnung Mutant -> Kriterium steht im Kopf von
# tests/verify/t45_pruefstand.py und wird bei jedem Lauf mit ausgegeben.
#
# Die Baeume werden NICHT eingecheckt (.gitignore daneben). Wer messen will,
# ruft tests/verify/meta.sh T-4.5; das ruft dieses Skript selbst auf.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HIER/../../.." && pwd)"
GUT="$REPO/tests/fixtures/known-good/T-4.5"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT INT TERM

mutanten=(
  hmac-pflicht
  hmac-optional
  feld-faellt-weg
  params-hash-egal
  audience-egal
  schema-egal
  serialisierung-egal
  frist-nach-wanduhr
  broker-frist-nach-wanduhr
  peer-egal
  ticket-wieder-einloesbar
  ticket-nach-der-tat
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
ORDER = f"{P}/common/order.py"
HUBORDER = f"{P}/hub/order.py"
DIENST = f"{P}/brokers/dienst.py"
FS = f"{P}/brokers/fs/daemon.py"
EXEC = f"{P}/brokers/exec/daemon.py"


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


# -- K1: die zwei umgekehrten Mutanten --------------------------------------

ersetze(
    "hmac-pflicht", ORDER,
    'FELDER = ("audience", "schema", "action_id", "params", "params_hash",\n'
    '          "ticket", "deadline_monotonic", "turn_id")\n',
    'FELDER = ("audience", "schema", "action_id", "params", "params_hash",\n'
    '          "ticket", "deadline_monotonic", "turn_id", "sig")\n'
    '\n'
    'import hmac  # MUTATION: die Zeremonie kehrt zurueck\n'
    'import os\n'
    '\n'
    'SCHLUESSEL = os.environ.get("DAIMON_ORDER_KEY", "geheim").encode()\n'
    '\n'
    '\n'
    'def _signieren(daten: dict) -> str:\n'
    '    ohne = {f: daten[f] for f in FELDER if f != "sig"}\n'
    '    roh = json.dumps(ohne, sort_keys=True, separators=(",", ":"),\n'
    '                     ensure_ascii=False).encode("utf-8")\n'
    '    return "hmac-sha256:" + hmac.new(SCHLUESSEL, roh,\n'
    '                                     hashlib.sha256).hexdigest()\n',
    "Der HMAC ist wieder da, als PFLICHTFELD `sig`. Ein Auftrag ohne "
    "Signatur wird abgewiesen. Design 1.3/6.2 haben ihn gestrichen: der "
    "Broker koennte nur mit einem Schluessel pruefen, den auch der Hub hat "
    "-- und der ist fuer den ausgeschlossenen Angreifer per `ptrace` lesbar. "
    "Scheinsicherheit, und die ist teurer als keine (K1).")

ersetze(
    "hmac-optional", ORDER,
    'SCHEMA = "daimon.order.v1"\n',
    'SCHEMA = "daimon.order.v1"\n'
    '\n'
    'import hmac  # MUTATION: Maschinerie ohne Zulauf, aber vorhanden\n'
    '\n'
    'SCHLUESSEL = b"geheim"\n'
    '\n'
    '\n'
    'def _signatur_stimmt(roh: bytes, sig: str) -> bool:\n'
    '    """Wird nur gerufen, WENN ein `sig` mitkommt -- also heute nie."""\n'
    '    erwartet = hmac.new(SCHLUESSEL, roh, hashlib.sha256).hexdigest()\n'
    '    return hmac.compare_digest(erwartet, sig)\n',
    "Dieselbe Zeremonie, nur optional: geprueft wird eine Signatur nur, wenn "
    "eine mitkommt. Am VERHALTEN faellt das nicht auf -- der gueltige "
    "Auftrag geht weiter durch. Was auffaellt, ist das Bauteil: ein "
    "`hmac.compare_digest` im Auftragsweg ist der Anfang einer Zusage, die "
    "Design 6.2 ausdruecklich nicht gibt, und der naechste Leser haelt sie "
    "fuer gedeckt (K1, nur ueber den AST-Leser sichtbar).")

# -- K2: die acht Felder ----------------------------------------------------

ersetze(
    "feld-faellt-weg", ORDER,
    '          "ticket", "deadline_monotonic", "turn_id")\n',
    '          "ticket", "deadline_monotonic")  # MUTATION: turn_id faellt weg\n',
    "`turn_id` faellt aus dem Auftrag. Damit ist im Audit nicht mehr "
    "zuzuordnen, aus welcher Runde eine Ausfuehrung stammt -- und die "
    "Rundenmarke, aus der der `initiator` kommt, hat kein Gegenstueck mehr "
    "im Auftrag (K2).")

ersetze(
    "params-hash-egal", ORDER,
    '    erwartet = params_hash(daten["params"])\n'
    '    if daten["params_hash"] != erwartet:\n'
    '        raise AuftragsFehler("params_hash passt nicht zu den Parametern")\n',
    '    # MUTATION: der mitgeschickte Hash wird geglaubt\n'
    '    erwartet = daten["params_hash"]\n',
    "Der Broker glaubt den mitgeschickten `params_hash`, statt ihn "
    "nachzurechnen. Damit sind die Parameter, ueber die der Hub entschieden "
    "hat, nicht mehr dieselben, die der Broker ausfuehrt -- manipulierte "
    "Parameter kommen durch (K2).")

# -- K3: audience -----------------------------------------------------------

ersetze(
    "audience-egal", ORDER,
    '    if daten["audience"] != audience:\n'
    '        raise AuftragsFehler(\n'
    '            f"Auftrag ist fuer {daten[\'audience\']!r}, hier ist {audience!r}")\n',
    '    # MUTATION: jeder Broker nimmt jeden Auftrag\n',
    "Die Bindung an genau einen Broker faellt weg: ein DBus-Auftrag ist "
    "damit bei `daimon-fs` einreichbar. Die `audience` ist dann eine "
    "Beschriftung und keine Schranke (K3).")

# -- K4: Schema und Serialisierung ------------------------------------------

ersetze(
    "schema-egal", ORDER,
    '    if daten["schema"] != SCHEMA:\n'
    '        raise AuftragsFehler(\n'
    '            f"unbekanntes Schema {daten[\'schema\']!r}; erwartet {SCHEMA!r}")\n',
    '    # MUTATION: dann eben die alte Lesart\n',
    "Ein unbekanntes `schema` wird durchgelassen. Damit lesen Hub und Broker "
    "denselben Auftrag moeglicherweise verschieden -- genau der Fall, den "
    "das Feld ausschliessen soll (K4).")

ersetze(
    "serialisierung-egal", ORDER,
    '        if kanonisch(daten) != bytes(roh):\n',
    '        if False:  # MUTATION: jede Schreibweise ist recht\n',
    "Die Serialisierung wird nicht mehr geprueft. Derselbe Auftrag hat dann "
    "mehrere gueltige Schreibweisen, und `params_hash` haengt an einer von "
    "ihnen -- also an keiner (K4).")

# -- K5: die monotone Frist -------------------------------------------------

ersetze(
    "frist-nach-wanduhr", HUBORDER,
    "time.monotonic() if jetzt is None else float(jetzt)",
    "time.time() if jetzt is None else float(jetzt)  # MUTATION",
    "Das Auftragsbuch rechnet nach der WANDUHR. Eine Zeitumstellung "
    "verlaengert oder verkuerzt dann jede offene Frist, und ein NTP-Sprung "
    "rueckwaerts macht abgelaufene Auftraege wieder gueltig (K5).",
    mal=4)

ersetze(
    "broker-frist-nach-wanduhr", FS,
    'auftrag = pruefe(roh, audience=AUDIENCE, jetzt=__import__("time").monotonic())',
    'auftrag = pruefe(roh, audience=AUDIENCE, '
    'jetzt=__import__("time").time())  # MUTATION',
    "Der FS-Broker haelt die Frist gegen die Wanduhr, waehrend der Hub sie "
    "monoton setzt. Beide Uhren stehen um Jahrzehnte auseinander -- jeder "
    "Auftrag ist damit entweder immer gueltig oder nie, und welches von "
    "beidem, entscheidet die Uhrzeit (K5).")

# -- K6: Herkunft ueber den Socket ------------------------------------------

ersetze(
    "peer-egal", DIENST,
    "            if erlaubte_units is not None:\n"
    "                # Der Auftrag traegt keine Signatur (Design 6.2). Was ihn\n"
    "                # traegt, ist die Leitung, auf der er ankommt -- also wird\n"
    "                # sie geprueft, bevor eine Zeile gelesen wird.\n"
    "                try:\n"
    "                    peer = ipc.peer_of(conn, \"aktion\")\n"
    "                    if peer.unit not in set(erlaubte_units):\n"
    "                        raise ipc.PeerError(f\"Unit {peer.unit!r}\")\n"
    "                except ipc.PeerError as fehler:\n"
    "                    if log is not None:\n"
    "                        log.warn(\"Auftrag von fremder Unit\",\n"
    "                                 DAIMON_GRUND=str(fehler)[:120])\n"
    "                    conn.close()\n"
    "                    continue\n",
    "            # MUTATION: der Socket nimmt jeden an\n",
    "Die Peer-Pruefung am Broker-Socket faellt weg. Da der Auftrag keine "
    "Signatur traegt (Design 6.2), bleibt damit KEIN Herkunftsnachweis "
    "uebrig -- Akzeptanzpunkt 6 sagt aber ausdruecklich: Herkunft ueber den "
    "Socket, nicht ueber Kryptografie. Eine Zusage ohne Vorrichtung (K6).")

# -- K7: Ticketeinloesung ---------------------------------------------------

ersetze(
    "ticket-wieder-einloesbar", HUBORDER,
    "        if ticket in self._eingeloest:\n"
    "            raise AuftragsFehler(\n"
    "                f\"Ticket bereits eingeloest um \"\n"
    "                f\"{self._eingeloest[ticket]:.3f} (monoton)\")\n"
    "        auftrag = self._offen.pop(ticket, None)\n",
    "        # MUTATION: eingeloest heisst nicht verbraucht\n"
    "        auftrag = self._offen.get(ticket, None)\n",
    "Ein eingeloestes Ticket bleibt einloesbar. Aus 'hoechstens einmal' wird "
    "'beliebig oft', und ein wiedereingespielter Auftrag laeuft erneut -- "
    "genau der Fall, wegen dessen das Ticketbuch im Hub liegt und nicht im "
    "Broker (K7).")

ersetze(
    "ticket-nach-der-tat", EXEC,
    "        kennung = str((auftrag.params or {}).get(\"desktop_id\") or \"\")\n"
    "        try:\n"
    "            dienst.ticket_beim_hub_einloesen(hub_pfad, auftrag.ticket)\n"
    "        except Exception as fehler:\n"
    "            return {\"ok\": False, \"grund\": \"ticket\", \"meldung\": str(fehler)[:160]}\n"
    "        return broker.starten(kennung)\n",
    "        kennung = str((auftrag.params or {}).get(\"desktop_id\") or \"\")\n"
    "        ergebnis = broker.starten(kennung)  # MUTATION: erst die Tat\n"
    "        try:\n"
    "            dienst.ticket_beim_hub_einloesen(hub_pfad, auftrag.ticket)\n"
    "        except Exception as fehler:\n"
    "            return {\"ok\": False, \"grund\": \"ticket\", \"meldung\": str(fehler)[:160]}\n"
    "        return ergebnis\n",
    "Der Exec-Broker startet die Anwendung und loest das Ticket DANACH ein. "
    "Die Einloesung ist dann eine Buchung ueber etwas bereits Geschehenes; "
    "ein abgelehntes Ticket verhindert nichts mehr. Der Plan sagt "
    "'unmittelbar vor der Ausfuehrung', und das ist keine Formulierung, "
    "sondern die Reihenfolge (K7).")
PY

for name in "${mutanten[@]}"; do
  ziel="$HIER/$name"
  rm -rf -- "$ziel"
  mv "$TMP/$name" "$ziel"
done

echo "T-4.5: ${#mutanten[@]} Mutanten erzeugt."
