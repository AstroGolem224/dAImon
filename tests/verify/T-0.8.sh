#!/usr/bin/env bash
# Verifizierer fuer T-0.8: Marken, Freigaben, Kontingente, Tickets.
#
# Vier getrennte Zustandsautomaten, und die Trennung ist der halbe Punkt: ein
# API-Kontingent, das eine Aktion autorisiert, waere kein Fehler im Detail,
# sondern das Loch, gegen das Design 2.4 argumentiert. Geprueft wird deshalb
# jeder Automat einzeln und zusaetzlich, dass sie sich NICHT gegenseitig
# vertreten.
#
# Geprueft wird am Verhalten der importierten Module, nicht am Quelltext --
# mit zwei benannten Ausnahmen:
#  * die Sprachregelung aus Design 1.3 ist eine Aussage UEBER den Text und
#    nur dort pruefbar;
#  * "kein Zustand aus einem eingehenden Request" wird zusaetzlich an den
#    Signaturen geprueft, weil ein Parameter, den es nicht gibt, auch nicht
#    versehentlich benutzt werden kann.
#
# Jede Ablehnung hat eine Positivkontrolle daneben. Ohne sie ist "wurde
# abgelehnt" nicht von "ist ganz kaputt" zu unterscheiden -- dieses Projekt
# ist genau darueber schon mehrfach gestolpert.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
MARKS="$TARGET/daimon/hub/marks.py"
TICKETS="$TARGET/daimon/hub/tickets.py"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.8 — Marken, Freigaben, Kontingente, Tickets"

chk "marks.py existiert" "$([[ -f "$MARKS" ]] && echo ja || echo nein)" ja
chk "tickets.py existiert" "$([[ -f "$TICKETS" ]] && echo ja || echo nein)" ja
chk "Python vorhanden" "$([[ -x "$PY" ]] && echo ja || echo nein)" ja

# --- Sprachregelung (Design 1.3) ----------------------------------------------
# Die einzige Pruefung, die zwingend am Text haengt: es geht um Woerter.
# "physisch" ist bewusst nicht in der Liste -- es kommt in unverfaenglichen
# Zusammenhaengen vor und wuerde nur Rauschen erzeugen.
verbotene=0
if [[ -f "$MARKS" && -f "$TICKETS" ]]; then
  verbotene="$(grep -ciE 'capability|unfaelschbar|unfälschbar|beweist' "$MARKS" "$TICKETS" 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')"
  # Positivkontrolle: die Dateien sind ueberhaupt lesbar und nicht leer.
  zeilen="$(cat "$MARKS" "$TICKETS" 2>/dev/null | wc -l)"
  chk "Quelldateien lesbar und nichtleer (Positivkontrolle)" \
    "$([[ "$zeilen" -gt 20 ]] && echo ja || echo nein)" ja
fi
echo "  Treffer verbotener Begriffe: $verbotene"
chk "Sprachregelung aus Design 1.3 eingehalten" "$verbotene" 0

# --- Verhalten ----------------------------------------------------------------
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

ergebnis="$(cd "$TARGET" && PYTHONDONTWRITEBYTECODE=1 timeout 120s "$PY" - "$tmp" 2>"$tmp/py.err" <<'PYEOF'
"""Faehrt die vier Automaten von aussen. Gibt je Pruefung eine Zeile
"name=ja|nein" aus. Ein Absturz hier ist ein Befund, kein Grund zum Schweigen.
"""
import inspect
import sys
import threading
from pathlib import Path

tmp = Path(sys.argv[1])
zeilen = []


def sag(name, wert):
    zeilen.append(f"{name}={'ja' if wert else 'nein'}")


class Protokoll:
    """Injizierter Logger. Der Verifizierer glaubt keiner Selbstauskunft --
    er zaehlt die Zeilen, die tatsaechlich entstanden sind."""

    def __init__(self):
        self.zeilen = []

    def _merken(self, *args, **kwargs):
        self.zeilen.append((args, kwargs))

    info = warning = error = debug = _merken


class Uhr:
    """Injizierbare Zeitquelle. Ein Test, der eine Stunde wartet, wird nie
    gefahren -- also wird die Zeit gestellt, nicht abgewartet."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def vor(self, s):
        self.t += s


try:
    from daimon.hub import marks, tickets
except Exception as fehler:  # noqa: BLE001
    print(f"import=nein")
    print(f"fehler={fehler!r}")
    sys.exit(0)

print("import=ja")
MF = marks.MarkenFehler


def wirft(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except MF:
        return True
    except Exception:
        return False
    return False


# === Rundenmarke =============================================================
uhr, log = Uhr(), Protokoll()
buch = marks.MarkenBuch(frist_s=100.0, jetzt=uhr, log=log)

# Positivkontrolle zuerst: der gueltige Weg muss funktionieren, sonst sagen
# alle Ablehnungen unten nichts.
try:
    buch.ausgeben(quelle="auth", turn_id="t1")
    m = buch.einloesen("t1")
    sag("marke_gueltiger_weg", m is not None)
except Exception:
    sag("marke_gueltiger_weg", False)

sag("marke_zweite_einloesung_abgelehnt", wirft(buch.einloesen, "t1"))

buch.ausgeben(quelle="auth", turn_id="t2")
sag("marke_fremde_turn_id_abgelehnt", wirft(buch.einloesen, "t-fremd"))

sag("marke_nur_aus_auth",
    wirft(buch.ausgeben, quelle="face", turn_id="t3")
    and wirft(buch.ausgeben, quelle="mind", turn_id="t4")
    and wirft(buch.ausgeben, quelle="", turn_id="t5"))

buch.ausgeben(quelle="auth", turn_id="t6")
sag("marke_initiator_user_bei_gueltig", buch.initiator("t6") == "user")
uhr.vor(101.0)
sag("marke_ablauf_abgelehnt", wirft(buch.einloesen, "t6"))
sag("marke_initiator_background_nach_ablauf", buch.initiator("t6") == "background")
sag("marke_initiator_background_ohne_marke", buch.initiator("nie-vergeben") == "background")
sag("marke_audit_nichtleer", len(log.zeilen) > 0)

# Wiederverwendung der turn_id darf eine verbrauchte Marke nicht wiederbeleben.
# `turn_id` kommt aus dem Aufruf; wenn eine erneute Ausgabe den Verbrauch
# zuruecksetzt, steuert ein Request-Feld, ob eine abgeschlossene Runde wieder
# gilt. "Einmal einloesbar" haengt dann an der Annahme, dass ein ANDERER
# Prozess nie eine turn_id wiederholt -- das ist keine Eigenschaft dieses Buchs.
uhr_w, log_w = Uhr(), Protokoll()
bw = marks.MarkenBuch(frist_s=100.0, jetzt=uhr_w, log=log_w)
bw.ausgeben(quelle="auth", turn_id="wieder")
bw.einloesen("wieder")
try:
    bw.ausgeben(quelle="auth", turn_id="wieder")
    wiederbelebt = not wirft(bw.einloesen, "wieder")
except MF:
    # Ausgabe selbst abgelehnt -- auch eine saubere Loesung.
    wiederbelebt = False
sag("marke_turn_id_wiederverwendung_belebt_nicht", not wiederbelebt)
# Positivkontrolle: eine FRISCHE turn_id funktioniert weiterhin. Ohne sie
# waere das obige "abgelehnt" auch mit einem komplett kaputten Buch gruen.
try:
    bw.ausgeben(quelle="auth", turn_id="frisch")
    bw.einloesen("frisch")
    sag("marke_frische_turn_id_funktioniert", True)
except Exception:
    sag("marke_frische_turn_id_funktioniert", False)

# Zustand aus dem Request: die oeffentlichen Methoden duerfen gar keinen
# Parameter anbieten, mit dem ein Aufrufer Frist oder Ablauf setzen koennte.
def keine_zustandsparameter(fn):
    verboten = {"ablauf_ts", "ablauf", "frist_s", "frist", "initiator",
                "jetzt", "ts", "expiry"}
    return not (set(inspect.signature(fn).parameters) & verboten)


sag("marke_ausgeben_ohne_zustandsparameter", keine_zustandsparameter(buch.ausgeben))
sag("marke_einloesen_ohne_zustandsparameter", keine_zustandsparameter(buch.einloesen))

# Nebenlaeufigkeit: "einmal einloesbar" muss auch parallel halten.
uhr2, buch2 = Uhr(), None
buch2 = marks.MarkenBuch(frist_s=1000.0, jetzt=uhr2, log=Protokoll())
buch2.ausgeben(quelle="auth", turn_id="parallel")
erfolge = []
sperre = threading.Lock()


def versuchen():
    try:
        buch2.einloesen("parallel")
    except Exception:
        return
    with sperre:
        erfolge.append(1)


faeden = [threading.Thread(target=versuchen) for _ in range(16)]
for f in faeden:
    f.start()
for f in faeden:
    f.join()
sag("marke_parallel_genau_einmal", len(erfolge) == 1)

# === Aktionsfreigabe =========================================================
uhr, log = Uhr(), Protokoll()
frei = marks.FreigabeBuch(frist_s=100.0, jetzt=uhr, log=log)

try:
    n = frei.nonce_ausgeben(action_hash="h-gut")
    frei.bestaetigen(nonce=n, action_hash="h-gut")
    frei.einloesen(action_hash="h-gut")
    sag("freigabe_gueltiger_weg", True)
except Exception:
    sag("freigabe_gueltiger_weg", False)

sag("freigabe_zweite_einloesung_abgelehnt",
    wirft(frei.einloesen, action_hash="h-gut"))

n2 = frei.nonce_ausgeben(action_hash="h-a")
sag("freigabe_fremder_hash_abgelehnt",
    wirft(frei.bestaetigen, nonce=n2, action_hash="h-b"))
# Und die Nonce ist danach verbrannt: ein Fehlversuch darf keinen zweiten
# erlauben. Sonst waere die Hash-Bindung mit Raten zu umgehen.
sag("freigabe_nonce_nach_fehlversuch_verbrannt",
    wirft(frei.bestaetigen, nonce=n2, action_hash="h-a"))

sag("freigabe_erfundene_nonce_abgelehnt",
    wirft(frei.bestaetigen, nonce="ausgedacht", action_hash="h-gut"))

n3 = frei.nonce_ausgeben(action_hash="h-spaet")
frei.bestaetigen(nonce=n3, action_hash="h-spaet")
uhr.vor(101.0)
sag("freigabe_ablauf_abgelehnt", wirft(frei.einloesen, action_hash="h-spaet"))

sag("freigabe_ohne_bestaetigung_abgelehnt",
    wirft(frei.einloesen, action_hash="nie-bestaetigt"))
sag("freigabe_audit_nichtleer", len(log.zeilen) > 0)
sag("freigabe_bestaetigen_ohne_zustandsparameter",
    keine_zustandsparameter(frei.bestaetigen))

# === API-Kontingent ==========================================================
uhr, log = Uhr(), Protokoll()
kont = marks.KontingentBuch(frist_s=100.0, jetzt=uhr, log=log)

try:
    k = kont.ausgeben(quelle="wake_word")
    kont.einloesen_fuer_egress(k)
    sag("kontingent_gueltiger_weg", True)
except Exception:
    sag("kontingent_gueltiger_weg", False)

sag("kontingent_zweiter_egress_abgelehnt",
    wirft(kont.einloesen_fuer_egress, k))

k2 = kont.ausgeben(quelle="rundenmarke")
sag("kontingent_beide_quellen_erlaubt", isinstance(k2, str) and k2 != "")
sag("kontingent_fremde_quelle_abgelehnt",
    wirft(kont.ausgeben, quelle="mind")
    and wirft(kont.ausgeben, quelle="face")
    and wirft(kont.ausgeben, quelle="modellausgabe"))

# DER Punkt aus Design 2.4: ein Kontingent autorisiert nichts. Geprueft an
# einem FRISCHEN, gueltigen, nicht eingeloesten Kontingent -- sonst waere das
# False nur die Folge des Verbrauchs und nicht die Zusage selbst.
k3 = kont.ausgeben(quelle="wake_word")
sag("kontingent_autorisiert_keine_aktion", kont.erlaubt_aktion(k3) is False)
sag("kontingent_deklassifiziert_nicht",
    kont.erlaubt_deklassifizierung(k3) is False)

k4 = kont.ausgeben(quelle="wake_word")
uhr.vor(101.0)
sag("kontingent_ablauf_abgelehnt", wirft(kont.einloesen_fuer_egress, k4))
sag("kontingent_erfundene_id_abgelehnt",
    wirft(kont.einloesen_fuer_egress, "ausgedacht"))
sag("kontingent_audit_nichtleer", len(log.zeilen) > 0)

# === Trennung der Automaten ==================================================
# Eine ID aus einem Buch darf in einem anderen nichts wert sein. Das faengt
# den Entwurf ab, der alle vier in einen gemeinsamen Speicher legt.
uhr, log = Uhr(), Protokoll()
b = marks.MarkenBuch(frist_s=100.0, jetzt=uhr, log=log)
f = marks.FreigabeBuch(frist_s=100.0, jetzt=uhr, log=log)
kk = marks.KontingentBuch(frist_s=100.0, jetzt=uhr, log=log)
b.ausgeben(quelle="auth", turn_id="x")
kid = kk.ausgeben(quelle="wake_word")
sag("trennung_marke_ist_kein_kontingent", wirft(kk.einloesen_fuer_egress, "x"))
sag("trennung_kontingent_ist_keine_freigabe", wirft(f.einloesen, action_hash=kid))

# === Broker-Ticket ===========================================================
uhr, log = Uhr(), Protokoll()
pfad = tmp / "ticketbuch.json"
tb = tickets.Ticketbuch(pfad, frist_s=100.0, jetzt=uhr, log=log)

try:
    t = tb.ausgeben(auftrag_hash="a1")
    tb.einloesen(t, auftrag_hash="a1")
    sag("ticket_gueltiger_weg", True)
except Exception:
    sag("ticket_gueltiger_weg", False)

sag("ticket_zweite_einloesung_abgelehnt", wirft(tb.einloesen, t, auftrag_hash="a1"))

t2 = tb.ausgeben(auftrag_hash="a2")
sag("ticket_fremder_auftrag_abgelehnt", wirft(tb.einloesen, t2, auftrag_hash="a3"))

# Ein Fehlversuch mit falschem auftrag_hash muss das Ticket verbrauchen --
# sonst sind beliebig viele Versuche gratis und der zugehoerige Hash laesst
# sich durch Probieren erfahren. FreigabeBuch.bestaetigen verbrennt die Nonce
# aus genau diesem Grund; zwei Buecher duerfen hier nicht zwei Haltungen haben.
t_orakel = tb.ausgeben(auftrag_hash="geheim")
wirft(tb.einloesen, t_orakel, auftrag_hash="geraten")
sag("ticket_nach_falschem_hash_verbrannt",
    wirft(tb.einloesen, t_orakel, auftrag_hash="geheim"))
# Positivkontrolle: ohne Fehlversuch geht derselbe Ablauf durch.
t_ok = tb.ausgeben(auftrag_hash="geheim2")
try:
    tb.einloesen(t_ok, auftrag_hash="geheim2")
    sag("ticket_ohne_fehlversuch_funktioniert", True)
except Exception:
    sag("ticket_ohne_fehlversuch_funktioniert", False)

t3 = tb.ausgeben(auftrag_hash="a4")
uhr.vor(101.0)
sag("ticket_ablauf_abgelehnt", wirft(tb.einloesen, t3, auftrag_hash="a4"))

# Neustart: ein verbrauchtes Ticket bleibt verbraucht. Ein Absturz zwischen
# Ausgabe und Ausfuehrung waere sonst eine Wiedereinloesung.
uhr_neu = Uhr()
tb_neu = tickets.Ticketbuch(pfad, frist_s=100.0, jetzt=uhr_neu, log=Protokoll())
sag("ticket_verbraucht_ueberlebt_neustart",
    wirft(tb_neu.einloesen, t, auftrag_hash="a1"))
# Positivkontrolle zum Neustart: die neue Instanz funktioniert ueberhaupt.
# Ohne sie waere das "abgelehnt" oben auch mit einem kaputten Buch gruen.
try:
    t_neu = tb_neu.ausgeben(auftrag_hash="a5")
    tb_neu.einloesen(t_neu, auftrag_hash="a5")
    sag("ticket_neue_instanz_funktioniert", True)
except Exception:
    sag("ticket_neue_instanz_funktioniert", False)

sag("ticket_datei_existiert", pfad.exists())
sag("ticket_audit_nichtleer", len(log.zeilen) > 0)

erfolge2 = []


def ticket_versuchen(tid):
    try:
        tb_neu.einloesen(tid, auftrag_hash="parallel")
    except Exception:
        return
    with sperre:
        erfolge2.append(1)


tp = tb_neu.ausgeben(auftrag_hash="parallel")
faeden = [threading.Thread(target=ticket_versuchen, args=(tp,)) for _ in range(16)]
for f_ in faeden:
    f_.start()
for f_ in faeden:
    f_.join()
sag("ticket_parallel_genau_einmal", len(erfolge2) == 1)

print("\n".join(zeilen))
PYEOF
)"
py_rc=$?

chk "Sondierung lief durch" "$py_rc" 0
if [[ "$py_rc" -ne 0 ]]; then
  echo "  stderr der Sondierung:"
  sed 's/^/    | /' "$tmp/py.err" | head -20
fi

wert() { grep -m1 "^$1=" <<<"$ergebnis" | cut -d= -f2; }

chk "Module importierbar" "$(wert import)" ja
if [[ "$(wert import)" != "ja" ]]; then
  echo "  Importfehler: $(grep -m1 '^fehler=' <<<"$ergebnis")"
fi

# Reihenfolge: erst die Positivkontrolle, dann die Ablehnungen, die sie deckt.
echo "  -- Rundenmarke"
chk "gueltiger Weg funktioniert (Positivkontrolle)" "$(wert marke_gueltiger_weg)" ja
chk "entsteht nur aus intent_mark des Auth-Agenten" "$(wert marke_nur_aus_auth)" ja
chk "einmal einloesbar" "$(wert marke_zweite_einloesung_abgelehnt)" ja
chk "an turn_id gebunden" "$(wert marke_fremde_turn_id_abgelehnt)" ja
chk "an Frist gebunden" "$(wert marke_ablauf_abgelehnt)" ja
chk "initiator user bei gueltiger Marke (Positivkontrolle)" "$(wert marke_initiator_user_bei_gueltig)" ja
chk "abgelaufene Marke ⇒ initiator background" "$(wert marke_initiator_background_nach_ablauf)" ja
chk "fehlende Marke ⇒ initiator background" "$(wert marke_initiator_background_ohne_marke)" ja
chk "kein Zustandsparameter in ausgeben()" "$(wert marke_ausgeben_ohne_zustandsparameter)" ja
chk "kein Zustandsparameter in einloesen()" "$(wert marke_einloesen_ohne_zustandsparameter)" ja
chk "parallel genau eine Einloesung" "$(wert marke_parallel_genau_einmal)" ja
chk "frische turn_id funktioniert (Positivkontrolle)" "$(wert marke_frische_turn_id_funktioniert)" ja
chk "turn_id-Wiederverwendung belebt keine verbrauchte Marke" "$(wert marke_turn_id_wiederverwendung_belebt_nicht)" ja
chk "Audit nichtleer" "$(wert marke_audit_nichtleer)" ja

echo "  -- Aktionsfreigabe"
chk "gueltiger Weg funktioniert (Positivkontrolle)" "$(wert freigabe_gueltiger_weg)" ja
chk "erfundene Nonce abgelehnt" "$(wert freigabe_erfundene_nonce_abgelehnt)" ja
chk "an action_hash gebunden" "$(wert freigabe_fremder_hash_abgelehnt)" ja
chk "Nonce nach Fehlversuch verbrannt" "$(wert freigabe_nonce_nach_fehlversuch_verbrannt)" ja
chk "einmal einloesbar" "$(wert freigabe_zweite_einloesung_abgelehnt)" ja
chk "ohne Bestaetigung keine Einloesung" "$(wert freigabe_ohne_bestaetigung_abgelehnt)" ja
chk "an Frist gebunden" "$(wert freigabe_ablauf_abgelehnt)" ja
chk "kein Zustandsparameter in bestaetigen()" "$(wert freigabe_bestaetigen_ohne_zustandsparameter)" ja
chk "Audit nichtleer" "$(wert freigabe_audit_nichtleer)" ja

echo "  -- API-Kontingent"
chk "gueltiger Weg funktioniert (Positivkontrolle)" "$(wert kontingent_gueltiger_weg)" ja
chk "Wake-Word und Rundenmarke als Quelle" "$(wert kontingent_beide_quellen_erlaubt)" ja
chk "fremde Quelle abgelehnt" "$(wert kontingent_fremde_quelle_abgelehnt)" ja
chk "genau ein Egress-Aufruf" "$(wert kontingent_zweiter_egress_abgelehnt)" ja
chk "autorisiert KEINE Aktion" "$(wert kontingent_autorisiert_keine_aktion)" ja
chk "deklassifiziert NICHTS" "$(wert kontingent_deklassifiziert_nicht)" ja
chk "an Frist gebunden" "$(wert kontingent_ablauf_abgelehnt)" ja
chk "erfundene Kontingent-ID abgelehnt" "$(wert kontingent_erfundene_id_abgelehnt)" ja
chk "Audit nichtleer" "$(wert kontingent_audit_nichtleer)" ja

echo "  -- Trennung der Automaten"
chk "Marke ist kein Kontingent" "$(wert trennung_marke_ist_kein_kontingent)" ja
chk "Kontingent ist keine Freigabe" "$(wert trennung_kontingent_ist_keine_freigabe)" ja

echo "  -- Broker-Ticket"
chk "gueltiger Weg funktioniert (Positivkontrolle)" "$(wert ticket_gueltiger_weg)" ja
chk "hoechstens einmal einloesbar" "$(wert ticket_zweite_einloesung_abgelehnt)" ja
chk "an auftrag_hash gebunden" "$(wert ticket_fremder_auftrag_abgelehnt)" ja
chk "ohne Fehlversuch loesbar (Positivkontrolle)" "$(wert ticket_ohne_fehlversuch_funktioniert)" ja
chk "Ticket nach falschem auftrag_hash verbrannt" "$(wert ticket_nach_falschem_hash_verbrannt)" ja
chk "an Frist gebunden" "$(wert ticket_ablauf_abgelehnt)" ja
chk "Ticketbuch liegt auf der Platte" "$(wert ticket_datei_existiert)" ja
chk "neue Instanz funktioniert (Positivkontrolle)" "$(wert ticket_neue_instanz_funktioniert)" ja
chk "verbrauchtes Ticket ueberlebt den Neustart" "$(wert ticket_verbraucht_ueberlebt_neustart)" ja
chk "parallel genau eine Einloesung" "$(wert ticket_parallel_genau_einmal)" ja
chk "Audit nichtleer" "$(wert ticket_audit_nichtleer)" ja

exit $fail
