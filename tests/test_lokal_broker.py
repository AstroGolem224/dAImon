"""T-3.11c — der lokale Broker (Ollama).

Kein Test spricht mit einem laufenden Ollama: `http` ist injiziert. Gemessen
wird die ANFRAGE an Ollama und die Uebersetzung der Antwortform -- beides
Dinge, die man an einem laufenden Modell nur muehsam sieht.

Die Zusage, die diesen Broker vom CLI-Broker unterscheidet: er redet
ausschliesslich mit dem lokalen Rechner. Das steht in der Unit
(`IPAddressDeny=any` + `IPAddressAllow=localhost`) und hier im Test als
Adresspruefung.
"""

import io
import json

import pytest

from daimon.brokers.lokal import broker as B
from daimon.common.logging import get_logger


class Http:
    """Ersatz fuer den HTTP-Aufruf. Merkt sich Ziel und Nutzlast."""

    def __init__(self, antwort: str = "Es ist kurz nach drei.",
                 status: int = 200, roh: str | None = None) -> None:
        self.antwort = antwort
        self.status = status
        self.roh = roh
        self.url: str | None = None
        self.nutzlast: dict | None = None

    def __call__(self, url, nutzlast, *, timeout_s=0.0):
        self.url = url
        self.nutzlast = nutzlast
        if self.roh is not None:
            return self.status, self.roh
        return self.status, json.dumps(
            {"model": "soofi", "message": {"role": "assistant",
                                           "content": self.antwort},
             "done": True, "eval_count": 42})


class Hub:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.eingeloest: list[str] = []

    def __call__(self, pfad, anfrage, **kwargs):
        if anfrage.get("art") == "einloesen":
            self.eingeloest.append(anfrage.get("ticket", ""))
            return {"v": 1, "ok": self.ok, "grund": "" if self.ok else "verbraucht"}
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}


KOERPER = {"model": "claude-sonnet-4-5", "max_tokens": 1024,
           "system": "Du bist Nordom, ein Modron.",
           "messages": [{"role": "user", "content": "wie spaet ist es"}]}


def broker(http=None, hub=None, **kwargs):
    puffer = io.StringIO()
    b = B.LokalBroker(
        log=get_logger("test", socket_path="/nicht/da", stream=puffer),
        hub_socket="/nicht/da", http=http or Http(), hub_anfrage=hub or Hub(),
        **kwargs)
    b._protokoll = puffer
    return b


def frage(b, ticket="t-1"):
    return b.anfrage({"v": 1, "art": "anfrage", "ticket": ticket,
                      "koerper": KOERPER})


# -- Die Drahtform --------------------------------------------------------

def test_antwort_traegt_messages_gestalt():
    a = frage(broker())
    assert a["ok"] is True and a["status"] == 200
    assert a["antwort"]["content"][0]["text"] == "Es ist kurz nach drei."


def test_die_anfrage_geht_nur_an_den_eigenen_rechner():
    """Die Zusage dieses Brokers. Ein Ziel ausserhalb von localhost waere
    genau das, was der lokale Weg vermeiden soll."""
    http = Http()
    frage(broker(http=http))
    assert http.url.startswith("http://127.0.0.1:")


def test_das_modell_kommt_aus_der_konfiguration_nicht_aus_dem_koerper():
    """Im Koerper steht der Anthropic-Modellname -- der geht das lokale
    Modell nichts an. Wer ihn durchreichte, liesse den Mind bestimmen,
    welches Modell auf dieser Maschine laeuft."""
    http = Http()
    frage(broker(http=http, modell="soofi-s"))
    assert http.nutzlast["model"] == "soofi-s"
    assert "claude" not in json.dumps(http.nutzlast)


def test_die_persona_wird_woertlich_uebergeben():
    http = Http()
    frage(broker(http=http))
    rollen = {n["role"]: n["content"] for n in http.nutzlast["messages"]}
    assert rollen["system"] == KOERPER["system"]
    assert rollen["user"] == "wie spaet ist es"


def test_ohne_stream_und_mit_begrenzter_laenge():
    """Ein Pet, das streamt, muesste Teilstuecke sprechen -- das ist ein
    eigener Task. Und `num_predict` deckelt, was ohnehin abgewiesen wuerde."""
    http = Http()
    frage(broker(http=http))
    assert http.nutzlast["stream"] is False
    assert http.nutzlast["options"]["num_predict"] > 0


# -- Ticket und Kontingent ------------------------------------------------

def test_ohne_ticket_kein_modellaufruf():
    http = Http()
    a = broker(http=http).anfrage({"v": 1, "art": "anfrage", "koerper": KOERPER})
    assert a["ok"] is False and a["grund"] == "kein_ticket"
    assert http.url is None


def test_ungueltiges_ticket_haelt_das_modell_zurueck():
    http = Http()
    a = frage(broker(http=http, hub=Hub(ok=False)))
    assert a["ok"] is False and a["grund"] == "ticket_ungueltig"
    assert http.url is None


def test_obergrenze_je_fenster():
    hub = Hub()
    b = broker(hub=hub, hoechstens=2)
    assert frage(b, "t-1")["ok"] is True
    assert frage(b, "t-2")["ok"] is True
    dritte = frage(b, "t-3")
    assert dritte["grund"] == "kontingent_fenster"
    assert hub.eingeloest == ["t-1", "t-2"]


# -- Fehlerfaelle ---------------------------------------------------------

def test_kein_ollama_ist_eine_ehrliche_absage():
    def weg(url, nutzlast, *, timeout_s=0.0):
        raise OSError(111, "Connection refused")

    a = frage(broker(http=weg))
    assert a["ok"] is False and a["grund"] == "modell_weg"


def test_unbekanntes_modell_wird_benannt():
    a = frage(broker(http=Http(status=404, roh='{"error":"model not found"}')))
    assert a["ok"] is False and a["grund"] == "modell_fehlt"


def test_unlesbare_antwort_ist_modell_fehler():
    a = frage(broker(http=Http(roh="kein JSON")))
    assert a["ok"] is False and a["grund"] == "modell_fehler"


def test_leere_antwort_ist_modell_fehler():
    a = frage(broker(http=Http(antwort="   ")))
    assert a["ok"] is False and a["grund"] == "modell_fehler"


# -- Kein Inhalt im Protokoll --------------------------------------------

def test_weder_frage_noch_antwort_stehen_im_protokoll():
    kanarie_f, kanarie_a = "KANARIE-FRAGE-4711", "KANARIE-ANTWORT-0815"
    b = broker(http=Http(antwort=kanarie_a))
    b.anfrage({"v": 1, "art": "anfrage", "ticket": "t-1",
               "koerper": dict(KOERPER, messages=[
                   {"role": "user", "content": kanarie_f}])})
    p = b._protokoll.getvalue()
    assert kanarie_f not in p and kanarie_a not in p
    assert "t-1" in p or "ticket" in p


def test_zustand_nennt_modell_und_ziel():
    z = broker().anfrage({"v": 1, "art": "zustand"})
    assert z["ok"] is True
    assert set(z) >= {"modell", "ziel", "anfragen", "fenster_s", "hoechstens"}
    assert z["ziel"].startswith("http://127.0.0.1:")


# -- Denkspuren: der teuerste Weg, nichts zu sagen ------------------------

def test_die_anfrage_schaltet_das_denken_ab():
    """Am 09.08. an gemma4:26b gemessen: das Modell legte seine Denkspur in
    `thinking`, verbrauchte damit das ganze `num_predict`, und `content` kam
    LEER zurueck (`done_reason: length`). Mit `think: false` dieselbe Frage in
    0,78 s statt 10,7 s -- und mit einer Antwort.

    Fuer ein sprechendes Pet ist eine Denkspur reiner Verlust: gesprochen
    werden hoechstens 140 Zeichen, und niemand hoert dem Nachdenken zu.
    """
    http = Http()
    frage(broker(http=http))
    assert http.nutzlast["think"] is False


def test_ein_denkendes_modell_wird_benannt_und_nicht_verschwiegen():
    """Wenn ein Modell trotzdem denkt und darueber die Antwort vergisst, ist
    das ein eigener Befund -- `modell_fehler` waere eine Diagnose weniger."""
    roh = json.dumps({"message": {"role": "assistant", "content": "",
                                  "thinking": "Der Nutzer fragt..."},
                      "done_reason": "length"})
    a = frage(broker(http=Http(roh=roh)))
    assert a["ok"] is False and a["grund"] == "modell_denkt"
    assert "Der Nutzer fragt" not in json.dumps(a), "kein Inhalt in der Absage"
