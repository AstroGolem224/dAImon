"""Der lokale Modellweg: hat er im Betrieb einen Aufrufer -- und traegt die Naht?

Warum es diese Datei gibt
----------------------------------------------------------------------------
`daimon-lokal-broker` laeuft seit Monaten. Bis zum 26.08. hatte `lokal.sock`
**null Verbraucher im Produktivcode**: die einzige Stelle, die ihn nannte, war
eine Zeile in `config/systemd/daimon-mind.service`. Das ist der siebte Fall
des Musters aus CLAUDE.md -- gebaut, geprueft, und niemand ruft es auf. Ein
Test je Stueck findet das nie; `tests/test_lokal_broker.py` ruft den Broker ja
selbst auf und war die ganze Zeit gruen.

Zwei Sorten Pruefung, und beide werden gebraucht
----------------------------------------------------------------------------
1. **Die NAHT**, in der Reihenfolge des Betriebs und ueber echte Sockets:
   Katalog -> `Mind.frage_werkzeug` -> `ticket.sock` -> `lokal.sock` ->
   Ollama-Rumpf -> `tool_calls` -> Blockliste -> `aktion.sock`. Kein Stueck
   dieser Kette wird durch eine Attrappe ersetzt ausser dem HTTP-Aufruf an
   Ollama selbst -- genau der, der ein geladenes Modell braeuchte.
2. **Der WAECHTER** auf die Weiche: dass `daimon/mind/daemon.py: main` den
   Modellweg wirklich auf `lokal.sock` legt. Er hat eine Positivkontrolle
   (findet er ueberhaupt etwas?) und eine Gegenprobe an einer GEPATCHTEN
   KOPIE der Quelle -- ohne die waere "gruen" nicht von "blind" zu trennen.
"""
from __future__ import annotations

import ast
import json
import socket
import threading
from pathlib import Path

import pytest

from daimon.brokers.lokal import broker as B
from daimon.common.logging import get_logger
from daimon.mind import lokal as L
from daimon.mind.daemon import Mind, werkzeuge_aus_katalog
from daimon.mind.persona import Persona

REPO = Path(__file__).resolve().parents[1]
DAEMON_QUELLE = REPO / "daimon" / "mind" / "daemon.py"

# Zwei Eintraege: einer ohne Parameter, einer mit Schranke. Der zweite ist
# der Fall, an dem die Messung vom 26.08. haengt (`value: 30` statt `0.3`).
KATALOG = {
    "media.volumeup": {"rationale": "Lautstaerke eine Stufe hoeher.",
                       "params": {}},
    "audio.setvolume": {"rationale": "Lautstaerke setzen.",
                        "params": {"value": {"type": "float",
                                             "value_between": [0.0, 1.0],
                                             "required": True}}},
}

PERSONA = Persona(name="Nordom", system_prompt="Du bist Nordom, ein Modron.",
                  speech_threshold="mittel", voice="v", traits=(),
                  wake_words=(), palette={}, quelle=Path("/dev/null"))


class OhneLangzeit:
    """Der Mind baut sonst einen echten `Store` samt Datei. `frage_werkzeug`
    fasst ihn nicht an -- diese Attrappe faellt auf, falls doch."""

    def merken(self, *a, **k):                      # pragma: no cover
        raise AssertionError("frage_werkzeug hat das Langzeitgedaechtnis "
                             "angefasst -- das waere neu")


class Dienst:
    """Ein echter AF_UNIX-Dienst: eine JSON-Zeile hin, eine zurueck."""

    def __init__(self, pfad, antwort) -> None:
        self.pfad = str(pfad)
        self._antwort = antwort
        self.gesehen: list[dict] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.pfad)
        self._srv.listen(8)
        threading.Thread(target=self._lauf, daemon=True).start()

    def _lauf(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with conn:
                try:
                    anfrage = json.loads(conn.makefile("rb").readline(1 << 20))
                except (OSError, ValueError):        # pragma: no cover
                    continue
                self.gesehen.append(anfrage)
                try:
                    conn.sendall(
                        json.dumps(self._antwort(anfrage)).encode() + b"\n")
                except OSError:                      # pragma: no cover
                    pass

    def schliessen(self) -> None:
        self._srv.close()


class Ollama:
    """Der einzige Ersatz in der Naht: der HTTP-Aufruf an ein geladenes
    Modell. Merkt sich, was der Broker WIRKLICH abgeschickt hat."""

    def __init__(self, nachricht: dict) -> None:
        self.nachricht = nachricht
        self.gesendet: dict | None = None

    def __call__(self, url, nutzlast, *, timeout_s=0.0):
        self.gesendet = nutzlast
        return 200, json.dumps({"model": "qwen3", "done": True,
                                "message": self.nachricht})


def _tags(url, *, timeout_s=0.0):
    return 200, json.dumps({"models": [{"model": "qwen3:27b"}]})


@pytest.fixture
def naht(tmp_path):
    """Die ganze Strecke, aufgebaut wie im Betrieb. Der Ollama-Ersatz wird je
    Test gesetzt (`naht.ollama.nachricht`)."""

    class Aufbau:
        pass

    a = Aufbau()
    a.ollama = Ollama({"role": "assistant", "content": "Mache ich."})

    a.ticket = Dienst(tmp_path / "ticket.sock", lambda n: (
        {"v": 1, "ok": True, "ticket": "t-1"} if n.get("art") == "ausgeben"
        else {"v": 1, "ok": True}))
    a.aktion = Dienst(tmp_path / "aktion.sock", lambda n: {
        "v": 1, "ok": True, "ausgefuehrt": True, "verdikt": "allow"})

    broker = B.LokalBroker(
        None, log=get_logger("test-lokal"), hub_socket=a.ticket.pfad,
        modell="qwen3:27b", http=a.ollama, http_get=_tags)
    a.lokal = Dienst(tmp_path / "lokal.sock", broker.anfrage)

    werkzeuge, namen = werkzeuge_aus_katalog(KATALOG)
    a.werkzeuge, a.namen = werkzeuge, namen
    a.mind = Mind(hub_socket=a.ticket.pfad, egress_socket=a.lokal.pfad,
                  aktion_socket=a.aktion.pfad, persona=PERSONA,
                  werkzeuge=werkzeuge, werkzeug_namen=namen,
                  langzeit=OhneLangzeit(), log=get_logger("test-mind"))
    yield a
    for d in (a.ticket, a.aktion, a.lokal):
        d.schliessen()


# -- Die Naht ---------------------------------------------------------------

def test_die_werkzeugliste_erreicht_ollama(naht):
    """Der Bruch, an dem der lokale Weg fuer Durchgang 1 endete: der Broker
    liess `tools` fallen. Kein Fehler war sichtbar -- es kam nur nie ein
    `tool_use`."""
    naht.mind.frage_werkzeug("mach lauter")
    gesendet = naht.ollama.gesendet
    assert gesendet is not None, "der Broker hat gar nichts abgeschickt"
    namen = {w["function"]["name"] for w in gesendet["tools"]}
    assert namen == {"media_volumeup", "audio_setvolume"}, namen
    assert all(w["type"] == "function" for w in gesendet["tools"])


def test_die_schranke_wandert_unveraendert_nach_parameters(naht):
    """`input_schema` -> `parameters`, Wort fuer Wort. Eine umgeschriebene
    Schranke waere eine zweite Fassung derselben Regel -- und durchsetzen
    wuerde sie `Policy._params_pruefen`, nicht diese."""
    naht.mind.frage_werkzeug("stell die lautstaerke auf dreissig prozent")
    fn = next(w["function"] for w in naht.ollama.gesendet["tools"]
              if w["function"]["name"] == "audio_setvolume")
    quelle = next(w["input_schema"] for w in naht.werkzeuge
                  if w["name"] == "audio_setvolume")
    assert fn["parameters"] == quelle
    assert fn["parameters"]["properties"]["value"]["maximum"] == 1.0


def test_ein_werkzeugruf_erreicht_aktion_sock(naht):
    """Die ganze Kette. Ollama antwortet mit `tool_calls` und OHNE Text --
    genau der Fall, den der Broker bis zum 26.08. als `modell_fehler` abwies."""
    naht.ollama.nachricht = {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "ruf-7", "function": {
            "name": "audio_setvolume", "arguments": {"value": 0.3}}}]}

    ergebnis = naht.mind.frage_werkzeug("stell die lautstaerke auf dreissig prozent")

    assert ergebnis["ok"] is True, ergebnis
    assert ergebnis["tool_erkannt"] is True, ergebnis
    assert ergebnis["action_id"] == "audio.setvolume"
    assert ergebnis["ausgefuehrt"] is True
    lauf = naht.aktion.gesehen[-1]
    assert lauf["art"] == "ausfuehren"
    assert lauf["action_id"] == "audio.setvolume"
    assert lauf["params"] == {"value": 0.3}
    assert lauf["tool_use_id"] == "ruf-7"


def test_ohne_werkzeugruf_bleibt_es_bei_text_und_keiner_aktion(naht):
    """Die Gegenprobe, und mit ihr die Positivkontrolle des Tests darueber:
    dieselbe Vorrichtung, eine Antwort ohne `tool_calls` -- und `aktion.sock`
    bleibt unberuehrt. Waere die Vorrichtung tot, waere AUCH der Test darueber
    gruen gewesen, ohne etwas gemessen zu haben."""
    naht.ollama.nachricht = {"role": "assistant",
                             "content": "Es ist kurz nach drei."}

    ergebnis = naht.mind.frage_werkzeug("wie spaet ist es")

    assert ergebnis["ok"] is True and ergebnis["tool_erkannt"] is False
    assert ergebnis["antwort"] == "Es ist kurz nach drei."
    assert naht.aktion.gesehen == [], naht.aktion.gesehen


def test_ein_erfundener_name_wird_nicht_geraten(naht):
    naht.ollama.nachricht = {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "x", "function": {"name": "kaffee_kochen",
                                                "arguments": {}}}]}
    ergebnis = naht.mind.frage_werkzeug("mach kaffee")
    assert ergebnis["tool_erkannt"] is False
    assert ergebnis["grund"] == "unbekanntes_werkzeug"
    assert naht.aktion.gesehen == []


def test_ein_belegtes_modell_erreicht_den_mind_unter_seinem_eigenen_namen(
        tmp_path):
    """Der neue Grund muss die NAHT ueberleben, nicht nur den Broker.

    `Mind.frage_werkzeug` reicht den Grund des Brokers woertlich durch (es
    benennt ihn ausdruecklich nicht um, `daemon.py:401`) -- diese Zusage gilt
    fuer `modell_beschaeftigt` genau so wie fuer `modell_weg`, und geprueft
    ist sie hier ueber einen echten Socket. Ob eine Zeitueberschreitung im
    Draht wirklich als `modell_beschaeftigt` entsteht (und ein toter Port
    weiter als `modell_weg`), misst `tests/test_lokal_fremdlast.py` an einem
    echten HTTP-Endpunkt; hier geht es um die Strecke danach.
    """
    def belegt(url, nutzlast, *, timeout_s=0.0):
        raise TimeoutError("timed out")

    ticket = Dienst(tmp_path / "ticket.sock", lambda n: (
        {"v": 1, "ok": True, "ticket": "t-1"} if n.get("art") == "ausgeben"
        else {"v": 1, "ok": True}))
    aktion = Dienst(tmp_path / "aktion.sock", lambda n: {"v": 1, "ok": True})
    b = B.LokalBroker(None, log=get_logger("test-lokal"),
                      hub_socket=ticket.pfad, modell="qwen3:27b", http=belegt,
                      http_get=_tags, timeout_s=0.1, gesamt_s=0.3,
                      rueckstau_s=0.01)
    lokal = Dienst(tmp_path / "lokal.sock", b.anfrage)
    werkzeuge, namen = werkzeuge_aus_katalog(KATALOG)
    mind = Mind(hub_socket=ticket.pfad, egress_socket=lokal.pfad,
                aktion_socket=aktion.pfad, persona=PERSONA,
                werkzeuge=werkzeuge, werkzeug_namen=namen,
                langzeit=OhneLangzeit(), log=get_logger("test-mind"))
    try:
        ergebnis = mind.frage_werkzeug("mach lauter")
    finally:
        for d in (ticket, aktion, lokal):
            d.schliessen()

    assert ergebnis["ok"] is False
    assert ergebnis["grund"] == "modell_beschaeftigt", ergebnis
    assert aktion.gesehen == [], "eine Absage hat gehandelt"


def test_das_ticket_geht_dem_modellaufruf_voraus(naht):
    """Ein Aufruf, der bezahlt ist, bevor er autorisiert wurde, waere die
    Umkehrung der Zusage -- und lokal kostet er GPU statt Geld."""
    naht.mind.frage_werkzeug("mach lauter")
    arten = [n.get("art") for n in naht.ticket.gesehen]
    assert arten == ["ausgeben", "einloesen"], arten


# -- Die Umsetzung, rein ----------------------------------------------------

def test_argumente_als_zeichenkette_werden_abgefangen():
    bloecke = L.antwort_bloecke({"content": "", "tool_calls": [
        {"id": "a", "function": {"name": "n", "arguments": '{"value": 0.3}'}}]})
    assert bloecke == [{"type": "tool_use", "name": "n",
                        "input": {"value": 0.3}, "id": "a"}]


def test_leeres_content_erzeugt_keinen_leeren_textblock():
    """Sonst landete eine Aeusserung im Kurzzeitgedaechtnis, die es nie gab."""
    bloecke = L.antwort_bloecke({"content": "   ", "tool_calls": [
        {"function": {"name": "n", "arguments": {}}}]})
    assert [b["type"] for b in bloecke] == ["tool_use"]
    assert bloecke[0]["id"] == ""


def test_der_systemprompt_wird_die_erste_nachricht():
    rumpf = L.anfrage_rumpf({"system": "Du bist Nordom.", "max_tokens": 512,
                             "messages": [{"role": "user", "content": "hallo"}]},
                            modell="qwen3")
    assert rumpf["messages"][0] == {"role": "system",
                                    "content": "Du bist Nordom."}
    assert rumpf["stream"] is False and rumpf["think"] is False
    assert rumpf["options"]["num_predict"] == 512
    assert "tools" not in rumpf


# -- Der Waechter auf die Weiche -------------------------------------------

def _keyword_namen(quelltext: str, *, funktion: str, aufruf: str,
                   keyword: str) -> set[str] | None:
    """Welche NAMEN stecken in `aufruf(..., keyword=...)` innerhalb `funktion`?

    Ueber den Syntaxbaum und nicht per `grep`: eine Textsuche schlaegt am
    Kommentar an, der die Weiche nur beschreibt -- und dieses Repo hat genau
    diesen Prueferfehler schon gemacht (`tests/test_gate_zulauf.py`).

    `None` heisst **nicht gemessen** (Funktion oder Aufruf nicht gefunden) und
    ist damit von der leeren Menge unterscheidbar.
    """
    baum = ast.parse(quelltext)
    fn = next((k for k in ast.walk(baum)
               if isinstance(k, ast.FunctionDef) and k.name == funktion), None)
    if fn is None:
        return None
    zuweisungen: dict[str, ast.AST] = {}
    for k in ast.walk(fn):
        if isinstance(k, ast.Assign):
            for ziel in k.targets:
                if isinstance(ziel, ast.Name):
                    zuweisungen[ziel.id] = k.value
    ruf = next((k for k in ast.walk(fn) if isinstance(k, ast.Call)
                and isinstance(k.func, ast.Name) and k.func.id == aufruf), None)
    if ruf is None:
        return None
    wert = next((kw.value for kw in ruf.keywords if kw.arg == keyword), None)
    if wert is None:
        return None
    namen: set[str] = set()
    offen = [wert]
    while offen:
        for n in ast.walk(offen.pop()):
            if isinstance(n, ast.Name):
                namen.add(n.id)
                if n.id in zuweisungen:
                    offen.append(zuweisungen.pop(n.id))
    return namen


@pytest.fixture(scope="module")
def quelle() -> str:
    return DAEMON_QUELLE.read_text(encoding="utf-8")


def test_POSITIVKONTROLLE_der_waechter_sieht_ueberhaupt_etwas(quelle):
    """Er findet den Weg zum Hub, den es seit T-3.11 gibt. Findet er IHN
    nicht, sagt ein Fehlen des lokalen Wegs gar nichts -- dann ist das
    Messband leer."""
    namen = _keyword_namen(quelle, funktion="main", aufruf="Mind",
                           keyword="hub_socket")
    assert namen is not None, "Mind( im main nicht gefunden -- nicht gemessen"
    assert "TICKET_SOCKET" in namen, namen


def test_WAECHTER_der_mind_fragt_im_betrieb_den_lokalen_weg(quelle):
    """DER Zulauf. Vorher stand die Weiche ausschliesslich in
    `config/systemd/daimon-mind.service` -- eine Datei, die kein Pruefstand
    liest, und jede Kopie ohne diese Zeile sprach wieder den Egress an, der
    ohne Token nicht laeuft (Befund B8 aus T-6.8.v)."""
    namen = _keyword_namen(quelle, funktion="main", aufruf="Mind",
                           keyword="egress_socket")
    assert namen is not None, "Mind(egress_socket=...) nicht gefunden"
    assert "LOKAL_SOCKET" in namen, (
        "der Mind legt seinen Modellweg nicht mehr auf lokal.sock -- der "
        f"lokale Broker haette wieder null Verbraucher. Gefunden: {sorted(namen)}")


def test_WAECHTER_der_name_kommt_aus_der_einen_fassung(quelle):
    """`LOKAL_SOCKET` aus `daimon/mind/lokal.py` und nicht zweitgeschrieben:
    zwei Sockelnamen, die dasselbe meinen, laufen auseinander, sobald jemand
    nur einen pflegt."""
    baum = ast.parse(quelle)
    quellen = {k.module for k in ast.walk(baum)
               if isinstance(k, ast.ImportFrom)
               and any(a.name == "LOKAL_SOCKET" for a in k.names)}
    assert quellen == {"daimon.mind.lokal"}, quellen


def test_GEGENPROBE_der_waechter_wird_rot_ohne_den_zulauf(quelle):
    """An einer GEPATCHTEN KOPIE, nicht am echten Baum. Ohne diesen Nachweis
    waere "gruen" nicht von "misst nichts" zu unterscheiden -- vier
    Falschbefunde an einem Tag kamen genau daher."""
    ohne = quelle.replace("cfg.runtime_dir / LOKAL_SOCKET",
                          "cfg.runtime_dir / EGRESS_SOCKET")
    assert ohne != quelle, "der Patch hat nicht gegriffen -- Gegenprobe blind"
    namen = _keyword_namen(ohne, funktion="main", aufruf="Mind",
                           keyword="egress_socket")
    assert namen is not None and "LOKAL_SOCKET" not in namen, namen
    # Und die Positivkontrolle greift an der Kopie weiter -- der Waechter
    # wird ROT, nicht blind.
    assert "EGRESS_SOCKET" in namen, namen


def test_GEGENPROBE_ohne_aufruf_meldet_der_waechter_nicht_gemessen():
    assert _keyword_namen("def main():\n    pass\n", funktion="main",
                          aufruf="Mind", keyword="egress_socket") is None
