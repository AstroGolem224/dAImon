"""T-3.11 — Egress, Kontingent, Mind ohne Netz.

Was hier geprueft wird: die Redigierfunktion, die Bindung des Tickets an den
Koerper, die Obergrenze je Zeitfenster, die acht Absagegruende, der opake
Transport gegen eine eigene Attrappe, und dass Mind den Token nicht kennt.

Was hier NICHT geprueft wird und dem Pruefstand gehoert: dass ein AF_INET-Socket
im LAUFENDEN Mind-Prozess unter der echten Unit scheitert, dass der Token nicht
im Adressraum steht, und die Haertung beider Units am Prozess. Ein Unittest kann
`RestrictAddressFamilies=` nicht messen -- er laeuft ohne systemd.
"""

import io
import json
import pathlib
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from daimon.brokers.egress import broker as B
from daimon.common.config import Config
from daimon.common.logging import get_logger
from daimon.hub.daemon import Hub, TICKET_SOCKET
from daimon.mind import daemon as M
from daimon.mind.persona import lade as persona_laden

REPO = Path(__file__).resolve().parents[1]
KANARIE = "KANARIENVOGEL-4711-nur-hier"


def stiller_logger():
    return get_logger("test", socket_path="/nicht/da", stream=io.StringIO())


class Attrappe(BaseHTTPRequestHandler):
    """Protokolliert, was sie EMPFAENGT -- byte-genau.

    Nur so ist "opaker Transport" messbar. Ein Vergleich gegen das, was der
    Egress *sagt*, waere eine Selbstauskunft (Fall 9 der Fehlerliste).
    """

    empfangen: list[dict] = []

    def do_POST(self) -> None:                      # noqa: N802
        laenge = int(self.headers.get("content-length", "0"))
        roh = self.rfile.read(laenge)
        type(self).empfangen.append({
            "pfad": self.path, "roh": roh,
            "kopf": {k.lower(): v for k, v in self.headers.items()},
        })
        antwort = json.dumps({"content": [{"type": "text", "text": KANARIE}],
                              "model": "attrappe"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(antwort)))
        self.end_headers()
        self.wfile.write(antwort)

    def log_message(self, *args) -> None:           # noqa: D102
        pass                                        # keine Konsolenausgabe


@pytest.fixture
def attrappe():
    Attrappe.empfangen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Attrappe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture
def hub(tmp_path):
    rt, st = tmp_path / "rt", tmp_path / "st"
    rt.mkdir(), st.mkdir()
    h = Hub(cfg=Config(data={}, state_dir=st, runtime_dir=rt), runtime_dir=rt,
            log=stiller_logger())
    h.start()
    time.sleep(0.3)
    yield h
    h.stop()


def ticket_holen(hub, koerper) -> str:
    antwort = B.hub_anfrage(str(hub.runtime_dir / TICKET_SOCKET), {
        "v": 1, "art": "ausgeben", "zweck": "test",
        "auftrag_hash": B.koerper_hash(koerper)})
    assert antwort["ok"], antwort
    return antwort["ticket"]


def egress_mit(hub, attrappe, tmp_path, monkeypatch, **kw) -> B.Egress:
    """Egress mit Testprofil auf die Attrappe und einem Token in Credentials."""
    creds = tmp_path / "creds"
    creds.mkdir(exist_ok=True)
    (creds / B.CREDENTIAL_NAME).write_text("sk-ant-testtesttesttesttesttest1234567890")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    host, port = attrappe.server_address[0], attrappe.server_address[1]
    monkeypatch.setenv(B.UMGEBUNG_TESTPROFIL, "1")
    monkeypatch.setenv(B.UMGEBUNG_ZIEL, f"http://{host}:{port}/v1/messages")
    return B.Egress(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                    log=stiller_logger(), **kw)


# --------------------------------------------------------------------------
# Redigieren: die Zusage "Token in keinem Log"
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "sk-ant-api03-abcdefghijklmnop",
    "Authorization: Bearer abcdefghijklmnopqrst",
    "x-api-key: geheimgeheimgeheim",
    "X-API-Key=geheimgeheimgeheim",
    "a" * 45,
])
def test_geheimnisse_werden_redigiert(text):
    assert B.MARKER in B.redigieren(text)


def test_redigieren_laesst_harmloses_stehen():
    # Eine Redigierfunktion, die alles frisst, macht Logs wertlos.
    assert B.redigieren("Durchgereicht status=200 bytes=1234") == \
        "Durchgereicht status=200 bytes=1234"


def test_der_logger_kann_nicht_umgangen_werden(tmp_path, monkeypatch):
    # Nicht die Absicht wird geprueft, sondern der Weg: jede der drei Stufen
    # laeuft durch `redigieren`.
    strom = io.StringIO()
    log = B.RedigierenderLogger(
        get_logger("t", socket_path="/nicht/da", stream=strom))
    for stufe in (log.info, log.warn, log.error):
        stufe("Token sk-ant-abcdefghijklmnop gesehen",
              DAIMON_FELD="auch sk-ant-abcdefghijklmnop")
    text = strom.getvalue()
    assert "sk-ant-abcdefghijklmnop" not in text
    assert text.count(B.MARKER) >= 6


# --------------------------------------------------------------------------
# Der Hash bindet das Ticket an DIESEN Koerper
# --------------------------------------------------------------------------

def test_hash_ist_reihenfolgeunabhaengig():
    a = {"model": "x", "messages": [1], "max_tokens": 5}
    b = {"max_tokens": 5, "messages": [1], "model": "x"}
    assert B.koerper_hash(a) == B.koerper_hash(b)


def test_hash_unterscheidet_koerper():
    assert B.koerper_hash({"a": 1}) != B.koerper_hash({"a": 2})


def test_mind_und_egress_bilden_denselben_hash():
    # Weichen sie ab, weist der Egress jedes Ticket ab, das Mind besorgt hat --
    # und der Fehler saehe wie ein kaputtes Ticketbuch aus.
    koerper = {"model": "m", "messages": [{"role": "user", "content": "hä?"}]}
    assert B.koerper_hash(koerper) == M.koerper_hash(koerper)


# --------------------------------------------------------------------------
# Die acht Absagegruende
# --------------------------------------------------------------------------

def test_alle_gruende_sind_benannt():
    assert B.GRUENDE == {"unlesbar", "unbekannte_art", "kein_ticket",
                         "ticket_ungueltig", "kein_koerper",
                         "kontingent_fenster", "kein_token", "ziel_weg"}


def test_ohne_token_kommt_kein_token(hub, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    e = B.Egress(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                 log=stiller_logger())
    a = e.anfrage({"v": 1, "art": "anfrage", "ticket": "x",
                   "koerper": {"model": "m"}})
    assert a["grund"] == "kein_token"
    assert e.zustand()["token_vorhanden"] is False


@pytest.mark.parametrize("anfrage,grund", [
    ("keindict", "unlesbar"),
    ({"art": "quatsch"}, "unbekannte_art"),
    ({"art": "anfrage", "koerper": {"m": 1}}, "kein_ticket"),
    ({"art": "anfrage", "ticket": "  ", "koerper": {"m": 1}}, "kein_ticket"),
    ({"art": "anfrage", "ticket": "t"}, "kein_koerper"),
    ({"art": "anfrage", "ticket": "t", "koerper": "text"}, "kein_koerper"),
])
def test_protokollfehler_haben_eigene_gruende(hub, attrappe, tmp_path,
                                              monkeypatch, anfrage, grund):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    assert e.anfrage(anfrage)["grund"] == grund


def test_erfundenes_ticket_wird_abgewiesen(hub, attrappe, tmp_path, monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    a = e.anfrage({"v": 1, "art": "anfrage", "ticket": "0" * 32,
                   "koerper": {"model": "m", "messages": []}})
    assert a["grund"] == "ticket_ungueltig"
    assert not Attrappe.empfangen, "ohne Ticket darf NICHTS rausgehen"


# --------------------------------------------------------------------------
# Kontingent: einmal, und nur fuer diesen Koerper
# --------------------------------------------------------------------------

def test_mit_ticket_geht_es_durch(hub, attrappe, tmp_path, monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    koerper = {"model": "m", "messages": [{"role": "user", "content": KANARIE}]}
    a = e.anfrage({"v": 1, "art": "anfrage",
                   "ticket": ticket_holen(hub, koerper), "koerper": koerper})
    assert a["ok"] and a["status"] == 200
    assert len(Attrappe.empfangen) == 1


def test_dasselbe_ticket_zweimal_wird_beim_zweiten_mal_abgewiesen(
        hub, attrappe, tmp_path, monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    koerper = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    ticket = ticket_holen(hub, koerper)
    anfrage = {"v": 1, "art": "anfrage", "ticket": ticket, "koerper": koerper}
    assert e.anfrage(anfrage)["ok"]
    zweite = e.anfrage(dict(anfrage))
    assert zweite["grund"] == "ticket_ungueltig"
    assert len(Attrappe.empfangen) == 1


def test_ein_ticket_fuer_A_bezahlt_B_nicht(hub, attrappe, tmp_path, monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    a_koerper = {"model": "m", "messages": [{"role": "user", "content": "A"}]}
    b_koerper = {"model": "m", "messages": [{"role": "user", "content": "B"}]}
    ticket = ticket_holen(hub, a_koerper)
    antwort = e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket,
                         "koerper": b_koerper})
    assert antwort["grund"] == "ticket_ungueltig"
    assert not Attrappe.empfangen


# --------------------------------------------------------------------------
# Obergrenze je Zeitfenster
# --------------------------------------------------------------------------

def test_obergrenze_greift_und_nennt_die_restzeit(hub, attrappe, tmp_path,
                                                 monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch,
                   fenster_s=60.0, hoechstens=2)
    for i in range(2):
        k = {"model": "m", "messages": [{"role": "user", "content": str(i)}]}
        assert e.anfrage({"v": 1, "art": "anfrage",
                          "ticket": ticket_holen(hub, k), "koerper": k})["ok"]
    k = {"model": "m", "messages": [{"role": "user", "content": "zuviel"}]}
    dritte = e.anfrage({"v": 1, "art": "anfrage",
                        "ticket": ticket_holen(hub, k), "koerper": k})
    assert dritte["grund"] == "kontingent_fenster"
    assert 0 < dritte["rest_s"] <= 60
    assert len(Attrappe.empfangen) == 2
    assert e.zustand()["im_fenster"] == 2


def test_die_obergrenze_prueft_VOR_dem_ticket(hub, attrappe, tmp_path,
                                              monkeypatch):
    # Ein Ticket einzuloesen VERBRAUCHT es. Waere die Reihenfolge umgekehrt,
    # waere ein an der Obergrenze abgewiesener Aufruf ein bezahltes Kontingent
    # ohne Gegenwert.
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch, hoechstens=0)
    k = {"model": "m", "messages": []}
    ticket = ticket_holen(hub, k)
    assert e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket,
                      "koerper": k})["grund"] == "kontingent_fenster"
    # Das Ticket ist noch gueltig -- nachweisbar, indem die Grenze steigt.
    e.hoechstens = 1
    assert e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket,
                      "koerper": k})["ok"]


# --------------------------------------------------------------------------
# Opaker Transport
# --------------------------------------------------------------------------

def test_der_koerper_geht_byte_identisch_raus(hub, attrappe, tmp_path,
                                              monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    koerper = {"model": "m", "max_tokens": 7,
               "messages": [{"role": "user", "content": f"{KANARIE} äöü"}]}
    e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket_holen(hub, koerper),
               "koerper": koerper})
    gesendet = json.loads(Attrappe.empfangen[0]["roh"].decode("utf-8"))
    assert gesendet == koerper, "der Egress hat den Koerper veraendert"


def test_die_antwort_kommt_unveraendert_zurueck(hub, attrappe, tmp_path,
                                                monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    k = {"model": "m", "messages": []}
    a = e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket_holen(hub, k),
                   "koerper": k})
    # Ungeparst: der Egress reicht die Bytes durch. Erst der Aufrufer deutet.
    assert isinstance(a["antwort"], B.RohJSON)
    gedeutet = json.loads(a["antwort"].bytes)
    assert gedeutet["content"][0]["text"] == KANARIE
    assert gedeutet["model"] == "attrappe"
    # Und die fertige Zeile traegt genau diese Bytes.
    zeile = B.antwortzeile(a)
    assert zeile.endswith(a["antwort"].bytes + b"}\n")


def test_der_token_geht_als_kopf_mit_und_nicht_im_koerper(hub, attrappe,
                                                         tmp_path, monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    k = {"model": "m", "messages": []}
    e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket_holen(hub, k),
               "koerper": k})
    empfangen = Attrappe.empfangen[0]
    assert empfangen["kopf"]["x-api-key"].startswith("sk-ant-")
    assert b"sk-ant" not in empfangen["roh"]


def test_im_protokoll_steht_kein_koerper(hub, attrappe, tmp_path, monkeypatch):
    strom = io.StringIO()
    creds = tmp_path / "creds"
    creds.mkdir(exist_ok=True)
    (creds / B.CREDENTIAL_NAME).write_text("sk-ant-" + "x" * 40)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    host, port = attrappe.server_address
    monkeypatch.setenv(B.UMGEBUNG_TESTPROFIL, "1")
    monkeypatch.setenv(B.UMGEBUNG_ZIEL, f"http://{host}:{port}/v1/messages")
    e = B.Egress(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                 log=get_logger("e", socket_path="/nicht/da", stream=strom))
    k = {"model": "m", "messages": [{"role": "user", "content": KANARIE}]}
    a = e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket_holen(hub, k),
                   "koerper": k})
    assert a["ok"]
    # Die Kanarie steckt im Koerper UND in der Antwort der Attrappe -- sie darf
    # in keiner Logzeile stehen.
    assert KANARIE not in strom.getvalue(), strom.getvalue()
    assert "sk-ant" not in strom.getvalue()


# --------------------------------------------------------------------------
# Testprofil und Proxy
# --------------------------------------------------------------------------

def test_das_ziel_steht_im_code(hub, monkeypatch):
    monkeypatch.delenv(B.UMGEBUNG_TESTPROFIL, raising=False)
    monkeypatch.delenv(B.UMGEBUNG_ZIEL, raising=False)
    e = B.Egress(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                 log=stiller_logger())
    assert e.host == "api.anthropic.com" and e.tls is True
    assert e.zustand()["testprofil"] is False


def test_ziel_allein_wirkt_nicht(hub, monkeypatch):
    # Ohne das Testprofil ist die Variable wirkungslos -- sonst waere sie die
    # Umleitung, die es hier nicht geben darf.
    monkeypatch.delenv(B.UMGEBUNG_TESTPROFIL, raising=False)
    monkeypatch.setenv(B.UMGEBUNG_ZIEL, "http://127.0.0.1:1/v1/messages")
    e = B.Egress(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                 log=stiller_logger())
    assert e.host == "api.anthropic.com"
    assert e.zustand()["testprofil"] is False


def test_testprofil_ist_sichtbar(hub, attrappe, tmp_path, monkeypatch):
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    assert e.zustand()["testprofil"] is True
    assert "127.0.0.1" in e.zustand()["ziel"]


def test_proxy_variablen_werden_geloescht(hub, attrappe, tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9/")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9/")
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    assert "HTTPS_PROXY" not in os.environ and "https_proxy" not in os.environ
    # Und die Anfrage geht trotzdem zur Attrappe, nicht in den toten Port.
    k = {"model": "m", "messages": []}
    assert e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket_holen(hub, k),
                      "koerper": k})["ok"]


def test_totes_ziel_heisst_ziel_weg_und_nennt_keinen_koerper(hub, tmp_path,
                                                            monkeypatch):
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / B.CREDENTIAL_NAME).write_text("sk-ant-" + "y" * 40)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    monkeypatch.setenv(B.UMGEBUNG_TESTPROFIL, "1")
    monkeypatch.setenv(B.UMGEBUNG_ZIEL, "http://127.0.0.1:1/v1/messages")
    e = B.Egress(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                 log=stiller_logger())
    k = {"model": "m", "messages": [{"role": "user", "content": KANARIE}]}
    a = e.anfrage({"v": 1, "art": "anfrage", "ticket": ticket_holen(hub, k),
                   "koerper": k})
    assert a["grund"] == "ziel_weg"
    assert KANARIE not in json.dumps(a)


# --------------------------------------------------------------------------
# Mind: kein Token, und die Bindung haelt
# --------------------------------------------------------------------------

def mind_mit(hub, egress_sock) -> M.Mind:
    persona = persona_laden(Config(data={"persona": {"name": "Ember"}},
                                   config_dir=Path("/gibt/es/nicht")))
    return M.Mind(hub_socket=str(hub.runtime_dir / TICKET_SOCKET),
                  egress_socket=str(egress_sock), persona=persona,
                  log=stiller_logger())


def test_mind_meldet_keinen_token(hub):
    m = mind_mit(hub, "/gibt/es/nicht")
    z = m.zustand()
    assert z["token_vorhanden"] is False
    assert z["persona"] == "Ember" and z["prompt_zeichen"] > 100


def test_der_koerper_traegt_den_persona_prompt_woertlich(hub):
    m = mind_mit(hub, "/gibt/es/nicht")
    koerper = m.koerper("Wie steht der Build?")
    assert koerper["system"] == m.persona.prompt()
    assert "Glut-Geist" in koerper["system"]
    # Die Frage steht WOERTLICH und am Anfang. Sie ist seit dem 09.08. nicht
    # mehr der ganze Inhalt: dahinter haengt die Ausgabeform als eigener,
    # abgesetzter Block -- dasselbe Muster wie die Referenzen. Grund: der
    # Validator aus T-3.9 wies eine zweizeilige Antwort ab, und das Pet sagte
    # den Ersatzsatz statt der Antwort. Die frueher hier stehende
    # Gleichheitszusicherung ist damit bewusst zu einer Praefix-Zusicherung
    # geworden; der SYSTEMPROMPT bleibt unberuehrt woertlich, und das ist die
    # Zusage, die dieser Test im Namen traegt.
    inhalt = koerper["messages"][0]["content"]
    assert inhalt.startswith("Wie steht der Build?")
    assert "[Ausgabeform]" in inhalt
    assert "Glut-Geist" not in inhalt, "die Persona bleibt im Systemprompt"


def test_mind_ohne_egress_meldet_die_gegenstelle(hub):
    m = mind_mit(hub, "/gibt/es/nicht")
    a = m.frage("Hallo?")
    # Das Kontingent wurde erteilt, der Egress war weg -- der Grund wird
    # DURCHGEREICHT und nicht in "kein_kontingent" umgedeutet.
    assert not a["ok"] and a["grund"] == "gegenstelle_weg"


def test_die_ganze_kette_ohne_netz_in_mind(hub, attrappe, tmp_path, monkeypatch):
    """Mind -> Hub (Ticket) -> Egress -> Attrappe, ueber echte Sockets."""
    e = egress_mit(hub, attrappe, tmp_path, monkeypatch)
    sock = tmp_path / "egress.sock"
    srv = B.eigener_socket(str(sock))
    threading.Thread(target=B.lauf, args=(e, srv), daemon=True).start()
    try:
        m = mind_mit(hub, sock)
        a = m.frage(f"Frage mit {KANARIE}")
        assert a["ok"], a
        assert a["antwort"]["content"][0]["text"] == KANARIE
        gesendet = json.loads(Attrappe.empfangen[0]["roh"].decode())
        # Der Systemprompt der Persona ist wirklich rausgegangen.
        assert gesendet["system"] == m.persona.prompt()
    finally:
        srv.close()


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

def ohne_kommentare(text: str) -> str:
    return "\n".join(z for z in text.splitlines()
                     if not z.lstrip().startswith("#"))


def test_mind_hat_nur_unix_und_keinen_token():
    """BERICHTIGT am 14.08. (T-6.9). Vorher verlangte diese Pruefung, dass
    `ANTHROPIC_API_KEY` im Unit-TEXT nirgends vorkommt -- und riss an
    `UnsetEnvironment=ANTHROPIC_API_KEY`, also an einer Zeile, die ihre
    eigene Absicht ERFUELLT.

    Genau das Muster, vor dem die Uebergabe warnt: wer Quelltext per Suche
    prueft, prueft die Schreibweise. Getrennt wird jetzt nach SETZEN und
    ENTFERNEN, wie es die Egress-Pruefung darunter schon tat.
    """
    text = ohne_kommentare(
        (REPO / "config/systemd/daimon-mind.service").read_text())
    assert "RestrictAddressFamilies=AF_UNIX" in text
    for verboten in ("AF_INET", "LoadCredential"):
        assert verboten not in text, verboten

    # Kein Environment= setzt irgendetwas mit einem Schluessel darin.
    gesetzt = [z for z in text.splitlines() if z.startswith("Environment=")]
    for zeile in gesetzt:
        for name in ("ANTHROPIC", "API_KEY", "TOKEN", "SECRET"):
            assert name not in zeile.upper(), zeile

    # Und der Anthropic-Token wird ausdruecklich ENTFERNT.
    unset = [z for z in text.splitlines() if z.startswith("UnsetEnvironment=")]
    assert any("ANTHROPIC_API_KEY" in z for z in unset), unset


def test_mind_traegt_keinen_fremden_schluessel_in_der_umgebung():
    """T-6.9, und diesmal am LAUFENDEN Prozess statt am Unit-Text.

    Gemessen am 14.08.: `/proc/<pid>/environ` von daimon-mind enthielt
    `ELEVENLABS_API_KEY` und `MISTRAL_API_KEY`, geerbt aus der Umgebung des
    systemd-Benutzermanagers. Der anthropic-token war gesperrt, fremde
    Schluessel waren es nicht -- und der Egress traegt Prompt-Koerper, ohne
    sie zu deuten.

    Geprueft werden NAMEN, nie Werte: ein Pruefstand, der ein Geheimnis in
    seine eigene Fehlermeldung schreibt, ist der Angriff, den er sucht.
    """
    import shutil
    import subprocess
    if shutil.which("systemctl") is None:
        pytest.skip("kein systemd")
    roh = subprocess.run(["systemctl", "--user", "show",
                          "daimon-mind.service", "-p", "MainPID", "--value"],
                         capture_output=True, text=True, timeout=10)
    pid = int((roh.stdout or "0").strip() or 0)
    if pid <= 0:
        pytest.skip("daimon-mind laeuft nicht -- nicht messbar")
    try:
        umgebung = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
    except OSError as exc:
        pytest.skip(f"environ nicht lesbar: {exc}")
    namen = [v.split(b"=", 1)[0].decode("utf-8", "replace")
             for v in umgebung.split(b"\0") if b"=" in v]
    verdacht = [n for n in namen
                if any(m in n.upper()
                       for m in ("KEY", "TOKEN", "SECRET", "ANTHROPIC"))]
    assert verdacht == [], verdacht


def test_egress_hat_netz_und_den_token_ueber_credentials():
    text = ohne_kommentare(
        (REPO / "config/systemd/daimon-egress.service").read_text())
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in text
    assert f"LoadCredential={B.CREDENTIAL_NAME}:" in text
    # Der Token darf NICHT als Umgebungsvariable gesetzt werden. Proxy-
    # Variablen werden ENTFERNT (`UnsetEnvironment=`) und nicht geleert: eine
    # geleerte Variable steht weiterhin in der Umgebung.
    umgebung = [z for z in text.splitlines() if z.startswith("Environment=")]
    assert umgebung == [], umgebung
    unset = [z for z in text.splitlines() if z.startswith("UnsetEnvironment=")]
    assert unset, "Proxy-Variablen muessen entfernt werden"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        assert any(name in z for z in unset), name


@pytest.mark.parametrize("unit", ["daimon-egress.service",
                                  "daimon-mind.service"])
def test_beide_units_sind_gehaertet(unit):
    text = ohne_kommentare((REPO / "config/systemd" / unit).read_text())
    for direktive in ("NoNewPrivileges=yes", "CapabilityBoundingSet=",
                      "ProtectSystem=strict", "ProtectHome=read-only",
                      "ProtectProc=invisible", "ProcSubset=pid",
                      "PrivateTmp=yes", "LimitCORE=0", "UMask=0077",
                      "MemoryDenyWriteExecute=yes", "PrivateDevices=yes",
                      "RuntimeDirectory=daimon",
                      "RuntimeDirectoryPreserve=yes"):
        assert direktive in text, f"{unit}: {direktive}"
    # `[Install]` haengt daran, ob die Unit jemand ANDERS zieht. Hier stand
    # `assert "[Install]" not in text` fuer BEIDE -- und fuer den Mind war das
    # dieselbe falsche Annahme, die auch in seiner Unit stand („gestartet wird
    # ueber den Socket"). Eine `daimon-mind.socket` gibt es nicht, und
    # `PartOf=` startet nichts: der Dienst lief schlicht nicht, bis es am
    # 16.08. auffiel. Der Egress hat seine `.socket` und braucht deshalb
    # weiterhin kein `[Install]`.
    zieht_ihn_wer = (REPO / "config/systemd"
                     / unit.replace(".service", ".socket")).exists()
    assert ("[Install]" not in text) is zieht_ihn_wer, unit


def test_das_rotationsverfahren_ist_dokumentiert():
    doku = (REPO / "docs/TOKEN-ROTATION.md").read_text()
    # Kein Textvergleich um seiner selbst willen: das Kriterium verlangt, dass
    # die drei Fragen beantwortet sind -- wo er liegt, wie er ersetzt wird, und
    # woran man merkt, dass der alte tot ist.
    for pflicht in (f"~/.config/daimon/{B.CREDENTIAL_NAME}", "LoadCredential",
                    "systemctl --user restart daimon-egress.service",
                    "token_vorhanden", "401", "kein_token"):
        assert pflicht in doku, pflicht


# --------------------------------------------------------------------------
# Opaker Transport: die Originalbytes ueberleben beide Richtungen
# --------------------------------------------------------------------------

def test_roher_koerper_schneidet_die_originalbytes_heraus():
    # Leerzeichen und Schluesselreihenfolge bleiben, wie Mind sie geschrieben
    # hat -- der Egress ist kein zweiter Autor.
    zeile = (b'{"v":1,"art":"anfrage","ticket":"t",'
             b'"koerper":{"model":"m", "messages":[1,2] }}\n')
    anfrage = json.loads(zeile)
    roh = B.roher_koerper(zeile, anfrage["koerper"])
    assert roh == b'{"model":"m", "messages":[1,2] }'


def test_roher_koerper_faellt_nicht_auf_die_eigene_zeichenkette_herein():
    # Ein Koerper, der `"koerper":` selbst enthaelt, darf den Schnitt nicht
    # verschieben.
    zeile = (b'{"art":"anfrage","koerper":{"text":"das Wort \\"koerper\\": hier"}}'
             b'\n')
    anfrage = json.loads(zeile)
    roh = B.roher_koerper(zeile, anfrage["koerper"])
    assert json.loads(roh) == anfrage["koerper"]


def test_antwortzeile_stellt_die_rohantwort_ans_ende():
    zeile = B.antwortzeile({"v": 1, "ok": True, "status": 200,
                            "antwort": B.RohJSON(b'{"a":1}')})
    assert zeile.endswith(b',"antwort":{"a":1}}\n')
    assert json.loads(zeile)["antwort"] == {"a": 1}
