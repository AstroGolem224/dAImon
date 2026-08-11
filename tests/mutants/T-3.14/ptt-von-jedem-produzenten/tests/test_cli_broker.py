"""T-3.11b — der CLI-Broker.

Vertrag: `UMBRA-Notes/DDs/dAImon/T-3.11b-CLI-Broker-Plan.md`.

Kein einziger Test startet die echte CLI: `lauf` ist injiziert. Was hier
gemessen wird, ist die KOMMANDOZEILE und die Uebersetzung der Antwortform --
beides Dinge, die man an einem laufenden `claude` nur muehsam sieht.
"""

import io
import json

import pytest

from daimon.brokers.cli import broker as B
from daimon.common.logging import get_logger


class Lauf:
    """Ersatz fuer `subprocess.run`. Merkt sich argv, antwortet wie die CLI."""

    def __init__(self, text: str = "Es ist kurz nach drei.", rc: int = 0,
                 stdout: str | None = None) -> None:
        self.text = text
        self.rc = rc
        self.stdout = stdout
        self.argv: list[str] | None = None

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        roh = self.stdout if self.stdout is not None else json.dumps(
            {"is_error": False, "result": self.text, "duration_ms": 2814,
             "total_cost_usd": 0.11, "subtype": "success"})

        class Fertig:
            returncode = self.rc
            stdout = roh
            stderr = ""
        return Fertig()


class Hub:
    """Der Ticket-Endpunkt. Sagt ja, ausser man sagt ihm etwas anderes."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.eingeloest: list[str] = []

    def __call__(self, pfad, anfrage, **kwargs):
        if anfrage.get("art") == "einloesen":
            self.eingeloest.append(anfrage.get("ticket", ""))
            return ({"v": 1, "ok": True} if self.ok else
                    {"v": 1, "ok": False, "grund": "verbraucht"})
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}


KOERPER = {"model": "claude-sonnet-4-5", "max_tokens": 1024,
           "system": "Du bist Nordom, ein Modron.",
           "messages": [{"role": "user", "content": "wie spaet ist es"}]}


def broker(lauf=None, hub=None, **kwargs):
    puffer = io.StringIO()
    b = B.CliBroker(
        log=get_logger("test", socket_path="/nicht/da", stream=puffer),
        hub_socket="/nicht/da", lauf=lauf or Lauf(), hub_anfrage=hub or Hub(),
        **kwargs)
    b._protokoll = puffer          # fuer K5
    return b


def frage(b, ticket="t-1"):
    return b.anfrage({"v": 1, "art": "anfrage", "ticket": ticket,
                      "koerper": KOERPER})


# -- K1: die Drahtform ----------------------------------------------------

def test_antwort_traegt_messages_gestalt_nicht_cli_gestalt():
    """Mind soll nicht wissen, welcher Ruecken antwortet."""
    a = frage(broker())
    assert a["ok"] is True
    assert a["status"] == 200
    assert a["antwort"]["content"][0]["text"] == "Es ist kurz nach drei."
    assert a["antwort"]["content"][0]["type"] == "text"
    assert "result" not in a["antwort"]


def test_dauer_und_bytes_stehen_dabei():
    a = frage(broker())
    assert isinstance(a["dauer_ms"], float)
    assert a["bytes"] > 0


# -- K3/K4: die Kommandozeile --------------------------------------------

def test_kommandozeile_schaltet_werkzeuge_ab():
    argv = B.argumente(KOERPER, programm="claude", modell="sonnet")
    assert "--allowed-tools" in argv
    assert argv[argv.index("--allowed-tools") + 1] == ""


def test_kommandozeile_nennt_modell_und_json():
    argv = B.argumente(KOERPER, programm="claude", modell="sonnet")
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--output-format") + 1] == "json"


def test_persona_kommt_aus_dem_koerper():
    """T-3.10: die Persona wird woertlich weitergegeben. Ohne diese Zeile
    antwortete Claude Code als Claude Code."""
    argv = B.argumente(KOERPER, programm="claude", modell="sonnet")
    assert argv[argv.index("--system-prompt") + 1] == KOERPER["system"]


def test_keine_gefaehrlichen_schalter():
    argv = B.argumente(KOERPER, programm="claude", modell="sonnet")
    for verboten in ("--dangerously-skip-permissions", "--add-dir",
                     "--permission-mode"):
        assert verboten not in argv


def test_der_text_ist_die_letzte_nutzeraeusserung():
    koerper = dict(KOERPER, messages=[
        {"role": "user", "content": "alt"},
        {"role": "assistant", "content": "antwort"},
        {"role": "user", "content": "neu"}])
    argv = B.argumente(koerper, programm="claude", modell="sonnet")
    assert "neu" in argv
    assert "alt" not in argv


# -- K2: das Ticket -------------------------------------------------------

def test_ohne_ticket_keine_anfrage():
    a = broker().anfrage({"v": 1, "art": "anfrage", "koerper": KOERPER})
    assert a["ok"] is False and a["grund"] == "kein_ticket"


def test_ungueltiges_ticket_wird_abgelehnt():
    lauf = Lauf()
    a = frage(broker(lauf=lauf, hub=Hub(ok=False)))
    assert a["ok"] is False and a["grund"] == "ticket_ungueltig"
    assert lauf.argv is None, "die CLI darf ohne gueltiges Ticket nicht starten"


def test_das_ticket_wird_beim_hub_eingeloest():
    hub = Hub()
    frage(broker(hub=hub), ticket="t-42")
    assert hub.eingeloest == ["t-42"]


def test_ohne_koerper_keine_anfrage():
    a = broker().anfrage({"v": 1, "art": "anfrage", "ticket": "t-1"})
    assert a["ok"] is False and a["grund"] == "kein_koerper"


# -- K5: kein Inhalt im Protokoll ----------------------------------------

def test_weder_frage_noch_antwort_stehen_im_protokoll():
    """Der Prozess KENNT den Inhalt -- er protokolliert ihn trotzdem nicht."""
    kanarie_frage = "KANARIE-FRAGE-4711"
    kanarie_antwort = "KANARIE-ANTWORT-0815"
    b = broker(lauf=Lauf(text=kanarie_antwort))
    b.anfrage({"v": 1, "art": "anfrage", "ticket": "t-1",
               "koerper": dict(KOERPER, messages=[
                   {"role": "user", "content": kanarie_frage}])})
    protokoll = b._protokoll.getvalue()
    assert kanarie_frage not in protokoll
    assert kanarie_antwort not in protokoll
    assert "t-1" in protokoll or "ticket" in protokoll, \
        "Positivkontrolle: es wird ueberhaupt protokolliert"


# -- K6/K7: die CLI fehlt oder haengt ------------------------------------

def test_fehlendes_programm_ist_eine_ehrliche_absage():
    def weg(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    a = frage(broker(lauf=weg))
    assert a["ok"] is False and a["grund"] == "cli_fehlt"


def test_zeitlimit_ergibt_ziel_weg():
    import subprocess

    def haengt(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 120.0)

    a = frage(broker(lauf=haengt))
    assert a["ok"] is False and a["grund"] == "ziel_weg"


def test_rueckgabewert_ungleich_null_ist_cli_fehler():
    a = frage(broker(lauf=Lauf(rc=1)))
    assert a["ok"] is False and a["grund"] == "cli_fehler"


def test_unlesbare_ausgabe_ist_cli_fehler():
    a = frage(broker(lauf=Lauf(stdout="kein JSON")))
    assert a["ok"] is False and a["grund"] == "cli_fehler"


def test_is_error_der_cli_wird_durchgereicht():
    a = frage(broker(lauf=Lauf(stdout=json.dumps(
        {"is_error": True, "result": "Nutzungsgrenze erreicht"}))))
    assert a["ok"] is False and a["grund"] == "cli_fehler"


# -- K8: das Kontingentfenster -------------------------------------------

def test_obergrenze_je_fenster_greift_vor_dem_ticket():
    """Ein Ticket, das an der Obergrenze verfaellt, waere ein bezahltes
    Kontingent ohne Gegenwert -- dieselbe Reihenfolge wie im Egress."""
    hub = Hub()
    b = broker(hub=hub, hoechstens=2, fenster_s=60.0)
    assert frage(b, "t-1")["ok"] is True
    assert frage(b, "t-2")["ok"] is True
    dritte = frage(b, "t-3")
    assert dritte["ok"] is False and dritte["grund"] == "kontingent_fenster"
    assert hub.eingeloest == ["t-1", "t-2"], "das dritte Ticket bleibt heil"


# -- Zustand --------------------------------------------------------------

def test_zustand_verraet_keinen_inhalt():
    z = broker().anfrage({"v": 1, "art": "zustand"})
    assert z["ok"] is True
    assert z["programm"] == "claude"
    assert set(z) >= {"programm", "modell", "vorhanden", "anfragen",
                      "fenster_s", "hoechstens"}
