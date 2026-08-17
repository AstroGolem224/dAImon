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


class Tags:
    """Ersatz fuer `/api/tags`. Zaehlt mit, wie oft gefragt wurde.

    MUSS eingespeist werden, seit der Broker das Modell ermittelt statt es zu
    behaupten (17.08.). Ohne diese Attrappe fragte jeder Test das ECHTE
    Ollama dieser Maschine -- die Suite waere gruen, solange es laeuft, und
    rot auf jedem Rechner ohne. Genau die stille Maschinenabhaengigkeit, die
    ein Prueflauf nicht haben darf.
    """

    def __init__(self, namen=("qwen3.8:27b",), status: int = 200) -> None:
        self.namen = list(namen)
        self.status = status
        self.aufrufe = 0

    def __call__(self, url, *, timeout_s=0.0):
        self.aufrufe += 1
        return self.status, json.dumps(
            {"models": [{"model": n} for n in self.namen]})


def broker(http=None, hub=None, tags=None, **kwargs):
    puffer = io.StringIO()
    b = B.LokalBroker(
        log=get_logger("test", socket_path="/nicht/da", stream=puffer),
        hub_socket="/nicht/da", http=http or Http(), hub_anfrage=hub or Hub(),
        http_get=tags or Tags(), **kwargs)
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


# -- Dem Broker ist egal, welches Modell laeuft (17.08.) -------------------

def test_er_nimmt_was_ollama_vorhaelt():
    """DER BEFUND aus der ersten Messung der Naht. Vorher stand hier ein
    fester Name, und lief etwas anderes, hoerte der Nutzer "Ich komme gerade
    nicht an die API" -- eine Meldung ueber das Netz fuer einen Fehler, der
    keiner war."""
    http = Http()
    frage(broker(http=http, tags=Tags(["qwen3.8:27b"])))
    assert http.nutzlast["model"] == "qwen3.8:27b"


def test_eine_ausdrueckliche_wahl_schlaegt_den_fund():
    """Wer ein Modell nennt, meint eins. Sonst waere `--modell` eine
    Zeile, die nichts tut."""
    http = Http()
    tags = Tags(["etwas-anderes"])
    frage(broker(http=http, tags=tags, modell="genau-dieses"))
    assert http.nutzlast["model"] == "genau-dieses"
    assert tags.aufrufe == 0, "gefragt, obwohl die Wahl feststand"


def test_bei_mehreren_modellen_faellt_zweimal_dieselbe_wahl():
    """Ollama liefert nach Aenderungsdatum. Ohne feste Ordnung haenge die
    Antwort daran, welches Modell zuletzt angefasst wurde."""
    http = Http()
    frage(broker(http=http, tags=Tags(["z:1", "a:2", "m:3"])))
    assert http.nutzlast["model"] == "a:2"


def test_der_fund_wird_gemerkt_und_nicht_je_anfrage_geholt():
    """Ein HTTP-Aufruf mehr auf dem Weg, den der Nutzer als Wartezeit
    erlebt."""
    tags = Tags()
    b = broker(tags=tags)
    frage(b)
    frage(b, ticket="t-2")
    assert tags.aufrufe == 1


def test_ohne_jedes_modell_sagt_er_es_ehrlich():
    a = frage(broker(tags=Tags([])))
    assert a["ok"] is False and a["grund"] == "modell_fehlt"


def test_ein_totes_ollama_ist_keine_ausnahme():
    def kaputt(url, *, timeout_s=0.0):
        raise OSError("connection refused")

    a = frage(broker(tags=kaputt))
    assert a["ok"] is False and a["grund"] == "modell_fehlt"


def test_ein_404_verwirft_den_FUND_und_laesst_die_wahl_stehen():
    """Das Modell kann seit der Erkennung entfernt worden sein. Ein Broker,
    der sich an einen toten Namen klammert, bis jemand ihn neu startet, ist
    die Falle, die dieser Task zumacht -- die AUSDRUECKLICHE Wahl bleibt
    aber stehen, sie ist eine Ansage und kein Fund."""
    tags = Tags(["a:1"])
    b = broker(http=Http(status=404), tags=tags)
    assert frage(b)["grund"] == "modell_fehlt"
    assert b._erkannt is None
    frage(b, ticket="t-2")
    assert tags.aufrufe == 2, "der Fund wurde nicht neu ermittelt"

    fest = broker(http=Http(status=404), tags=Tags(["a:1"]), modell="fest")
    assert frage(fest)["grund"] == "modell_fehlt"
    assert fest.modell == "fest"


def test_modelle_lesen_haelt_muell_aus():
    assert B.modelle_lesen("kein json") == []
    assert B.modelle_lesen("{}") == []
    assert B.modelle_lesen('{"models": [{"kein_model": 1}, {"model": "a"}]}') \
        == ["a"]


def test_der_zustand_nennt_das_WIRKLICH_benutzte_modell():
    """`self.modell` allein staende auf `None`, waehrend eins laeuft -- eine
    Auskunft, die formal richtig und im Betrieb nutzlos ist."""
    z = broker(tags=Tags(["qwen3.8:27b"])).anfrage({"v": 1, "art": "zustand"})
    assert z["modell"] == "qwen3.8:27b"
    assert z["modell_gewaehlt"] is None

    fest = broker(tags=Tags(["andere"]), modell="genau-dieses")
    z2 = fest.anfrage({"v": 1, "art": "zustand"})
    assert z2["modell"] == z2["modell_gewaehlt"] == "genau-dieses"


def test_die_vorgabe_None_wird_nicht_zum_modellnamen_None():
    """`str(cfg.get("lokal.modell", None))` ergab die Zeichenkette "None" --
    einen Namen, den Ollama brav mit 404 quittiert. Drei Minuten nach der
    Umstellung live gemessen: der Broker meldete `modell: 'None'`."""
    assert B.modell_waehlen(None, None) is None
    assert B.modell_waehlen(None, "") is None
    assert B.modell_waehlen(None, "  ") is None
    assert B.modell_waehlen(None, "None") is None      # der Fall selbst
    assert B.modell_waehlen(None, "qwen3.8:27b") == "qwen3.8:27b"
    assert B.modell_waehlen("vom-schalter", "aus-config") == "vom-schalter"
