"""T-3.11c — der lokale Broker: das Modell laeuft auf dieser Maschine.

Warum es ihn gibt
----------------------------------------------------------------------------
Der CLI-Broker (T-3.11b) nutzt das Abo und zahlt dafuer dreifach: 7--25 s
Latenz, rund 0,11 USD Kontingent je Frage, und eine Unit ohne
`RestrictAddressFamilies` -- die CLI muss ins Netz. Alle drei Kosten
verschwinden, wenn das Modell hier liegt.

Die Zusage, die dieser Broker dafuer zurueckbekommt: **er redet
ausschliesslich mit dem eigenen Rechner.** Nicht als Absichtserklaerung,
sondern in der Unit (`IPAddressDeny=any` + `IPAddressAllow=localhost`) -- der
Kernel laesst nichts anderes zu, auch wenn dieser Code es versuchte.

Was er mit den anderen Brokern teilt
----------------------------------------------------------------------------
Dieselbe Drahtform wie Egress und CLI-Broker, dieselbe Ticketpflicht,
dieselbe Protokolldisziplin: `{ticket, bytes, status, dauer_ms}` und kein
Inhalt. Deshalb aendert sich am Mind wieder keine Zeile -- seine Unit zeigt
auf einen anderen Socket.

Das Modell kommt aus der Konfiguration, NICHT aus dem Koerper. Im Koerper
steht der Anthropic-Modellname, den Mind fuer die API baut; ihn
durchzureichen hiesse, den Mind bestimmen zu lassen, welches Modell auf
dieser Maschine laeuft.

ponytail: HTTP an Ollama, kein Unterprozess je Frage. Obergrenze: kein
Streaming (`stream: false`) -- ein streamendes Pet muesste Teilstuecke an den
TTS reichen, und das ist ein eigener Task. Und keine GPU-Torwache: das Gate
aus T-3.7 (VRAM, Vollbild, Ladeserialisierung) sitzt vor den eigenen Workern,
nicht vor Ollama. Wer das Pet nicht mitten im Spiel laden lassen will,
verdrahtet es hier -- benannt, nicht gebaut.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable

from daimon.brokers.egress.broker import (MAX_ZEILE, antwortzeile,
                                          hub_anfrage, koerper_hash)
from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger

LOKAL_SOCKET = "lokal.sock"
TICKET_SOCKET = "ticket.sock"
# 127.0.0.1, nicht "localhost": ein Name wird aufgeloest, und eine Aufloesung
# kann anderswohin zeigen. Die Zusage "nur dieser Rechner" soll nicht an
# /etc/hosts haengen.
ZIEL = "http://127.0.0.1:11434/api/chat"
# Woher der Broker erfaehrt, was ueberhaupt da ist. Aus ZIEL abgeleitet und
# nicht zweitgeschrieben: zwei Adressen, die auf dieselbe Ollama-Instanz
# zeigen sollen, laufen auseinander, sobald jemand nur eine pflegt.
TAGS = ZIEL.rsplit("/", 1)[0] + "/tags"
# KEIN fest verdrahtetes Modell mehr. Bis zum 17.08. stand hier ein Name,
# und der Broker verlangte GENAU den -- lief ein anderes Modell, antwortete
# Ollama mit 404 und der Nutzer hoerte "Ich komme gerade nicht an die API".
# Bei der ersten Messung der Naht war das der einzige Bruch in einer sonst
# tragenden Kette: jeder Weg funktionierte, nur der NAME passte nicht.
#
# `None` heisst jetzt "nimm, was laeuft". Ein ausdruecklich gesetztes Modell
# (`--modell`, `lokal.modell`) schlaegt das weiterhin -- wer eins nennt,
# meint eins.
MODELL: str | None = None
TIMEOUT_S = 120.0
FENSTER_S = 60.0
HOECHSTENS = 30
# Deckel fuer die Antwortlaenge. Gesprochen werden ohnehin hoechstens 140
# Zeichen (T-3.9); alles darueber ist Rechenzeit fuer etwas, das der Validator
# abweist. Grosszuegig, damit ein Satz nicht mitten drin endet.
NUM_PREDICT = 160

GRUENDE = frozenset({
    "unlesbar", "unbekannte_art", "kein_ticket", "kein_koerper",
    "ticket_ungueltig", "kontingent_fenster",
    "modell_weg", "modell_fehlt", "modell_fehler", "modell_denkt",
})


def http_post(url: str, nutzlast: dict, *,
              timeout_s: float = TIMEOUT_S) -> tuple[int, str]:
    """Eine Anfrage, eine Antwort. `urllib` und nicht `requests`, aus
    demselben Grund wie im Egress: keine Bibliothek, die Proxy-Variablen aus
    der Umgebung liest. Ein Proxy vor einem LOKALEN Ziel waere genau die
    Umleitung, die dieser Broker ausschliesst."""
    daten = json.dumps(nutzlast, ensure_ascii=False).encode("utf-8")
    anfrage = urllib.request.Request(
        url, data=daten, headers={"Content-Type": "application/json"})
    # Ein leerer ProxyHandler: `urllib` liest sonst http_proxy aus der Umgebung.
    oeffner = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with oeffner.open(anfrage, timeout=timeout_s) as antwort:
            return antwort.status, antwort.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as fehler:
        return fehler.code, fehler.read().decode("utf-8", "replace")


def http_get(url: str, *, timeout_s: float = 10.0) -> tuple[int, str]:
    """Wie `http_post`, nur lesend -- und aus demselben Grund ohne Proxy."""
    oeffner = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with oeffner.open(urllib.request.Request(url), timeout=timeout_s) as a:
            return a.status, a.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as fehler:
        return fehler.code, fehler.read().decode("utf-8", "replace")


def modell_waehlen(*kandidaten: object) -> str | None:
    """Der erste nichtleere Kandidat als Zeichenkette, sonst `None`.

    Klingt nach nichts und war ein Fehler: `str(cfg.get(...))` machte aus der
    Vorgabe `None` den Modellnamen `"None"`, und Ollama quittierte den brav
    mit 404. Drei Minuten nach der Umstellung live gemessen. Die Umwandlung
    darf erst NACH der Auswahl passieren.
    """
    for k in kandidaten:
        if k is None:
            continue
        text = str(k).strip()
        if text and text.lower() != "none":
            return text
    return None


def modelle_lesen(roh: str) -> list[str]:
    """Die Namen aus einer `/api/tags`-Antwort. Rein, damit der Pruefstand
    sie ohne laufendes Ollama pruefen kann.

    Sortiert, und das ist keine Kosmetik: bei mehreren Modellen soll zweimal
    dieselbe Wahl herauskommen. Ollama liefert nach Aenderungsdatum -- damit
    haenge die Antwort des Assistenten daran, welches Modell zuletzt
    angefasst wurde.
    """
    try:
        daten = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(daten, dict):
        return []
    namen = []
    for m in daten.get("models") or []:
        if isinstance(m, dict) and isinstance(m.get("model"), str):
            namen.append(m["model"])
    return sorted(namen)


def nutzlast(koerper: dict, *, modell: str,
             num_predict: int = NUM_PREDICT) -> dict:
    """Der Ollama-Chat-Koerper. Rein, damit der Pruefstand ihn pruefen kann,
    ohne ein Modell zu laden."""
    nachrichten: list[dict] = []
    system = koerper.get("system")
    if isinstance(system, str) and system.strip():
        # Woertlich. T-3.10 gibt die Persona unveraendert weiter, und ein
        # lokales Modell ist kein Grund, davon abzuweichen.
        nachrichten.append({"role": "system", "content": system})
    for nachricht in koerper.get("messages") or []:
        if not isinstance(nachricht, dict):
            continue
        inhalt = nachricht.get("content")
        if isinstance(inhalt, list):
            inhalt = "".join(t.get("text", "") for t in inhalt
                             if isinstance(t, dict))
        if isinstance(inhalt, str) and inhalt.strip():
            nachrichten.append({"role": str(nachricht.get("role", "user")),
                                "content": inhalt})
    return {"model": modell, "messages": nachrichten, "stream": False,
            # `think: false` ist keine Feinjustierung, sondern der Unterschied
            # zwischen einer Antwort und keiner. Am 09.08. an gemma4:26b
            # gemessen: mit Denkspur lief `num_predict` im `thinking`-Feld
            # leer, `content` kam LEER zurueck (`done_reason: length`), und
            # das Ganze dauerte 10,7 s. Ohne: dieselbe Frage, eine Antwort,
            # 0,78 s. Fuer ein Pet, das hoechstens 140 Zeichen spricht, ist
            # eine Denkspur reiner Verlust -- niemand hoert dem Nachdenken zu.
            "think": False,
            "options": {"num_predict": int(num_predict)}}


class LokalBroker:
    """Der lokale Rueckgrat. **Ihm ist egal, welches Modell laeuft.**

    Bis zum 17.08. war ein Name fest verdrahtet, und der Broker verlangte
    genau den. Lief etwas anderes, antwortete Ollama mit 404 und der Nutzer
    hoerte "Ich komme gerade nicht an die API" -- eine Meldung ueber das Netz
    fuer einen Fehler, der keiner war. Bei der ersten Messung der Naht war
    das der einzige Bruch in einer sonst tragenden Kette.
    """

    def __init__(self, cfg: Config | None = None, *,
                 log: Logger | None = None, hub_socket: str = "",
                 ziel: str = ZIEL, modell: str | None = MODELL,
                 timeout_s: float = TIMEOUT_S, fenster_s: float = FENSTER_S,
                 hoechstens: int = HOECHSTENS,
                 num_predict: int = NUM_PREDICT,
                 http: Callable[..., tuple[int, str]] = http_post,
                 http_get: Callable[..., tuple[int, str]] = http_get,
                 tags: str = TAGS,
                 hub_anfrage: Callable[..., dict] = hub_anfrage) -> None:
        self.cfg = cfg
        self.log = log or get_logger("daimon-lokal-broker")
        self.hub_socket = hub_socket
        self.ziel = ziel
        self.tags = tags
        # `modell` ist die AUSDRUECKLICHE Wahl (None = nimm, was laeuft),
        # `_erkannt` die ermittelte. Getrennt, damit ein 404 nur die zweite
        # verwirft: wer ein Modell benannt hat, soll nicht stillschweigend
        # ein anderes bekommen.
        self.modell = modell
        self._erkannt: str | None = None
        self._http_get = http_get
        self.timeout_s = float(timeout_s)
        self.fenster_s = float(fenster_s)
        self.hoechstens = int(hoechstens)
        self.num_predict = int(num_predict)
        self._http = http
        self._hub = hub_anfrage
        self._lock = threading.Lock()
        self._fenster: deque[float] = deque()
        self.anfragen = 0

    def _fenster_frei(self) -> tuple[bool, float]:
        jetzt = time.monotonic()
        with self._lock:
            while self._fenster and jetzt - self._fenster[0] > self.fenster_s:
                self._fenster.popleft()
            if len(self._fenster) < self.hoechstens:
                return True, 0.0
            return False, round(self.fenster_s - (jetzt - self._fenster[0]), 2)

    def modell_jetzt(self) -> str | None:
        """Welches Modell diese Anfrage benutzt. `None` = keins da.

        Die Reihenfolge ist die ganze Regel: eine ausdrueckliche Wahl gilt
        immer und wird NIE ueberschrieben; sonst wird einmal ermittelt und
        gemerkt. Gemerkt und nicht je Anfrage geholt -- das waere ein
        HTTP-Aufruf mehr auf dem Weg, den der Nutzer als Wartezeit erlebt.
        Verworfen wird der Fund nur bei einem 404, und dann ist er ja auch
        falsch.
        """
        if self.modell:
            return self.modell
        if self._erkannt:
            return self._erkannt
        try:
            status, roh = self._http_get(self.tags)
        except Exception:      # noqa: BLE001 -- ein totes Ollama ist kein
            return None        # Absturz dieses Dienstes, sondern eine Absage
        if status != 200:
            return None
        namen = modelle_lesen(roh)
        if not namen:
            return None
        self._erkannt = namen[0]
        self.log.info("Modell erkannt", DAIMON_ACTION="modell_erkannt",
                      DAIMON_MODELL=self._erkannt,
                      DAIMON_AUSWAHL=len(namen))
        return self._erkannt

    def _nein(self, grund: str, meldung: str, **extra: Any) -> dict:
        assert grund in GRUENDE, grund
        return {"v": 1, "ok": False, "grund": grund, "meldung": meldung, **extra}

    def anfrage(self, anfrage: object) -> dict:
        if not isinstance(anfrage, dict):
            return self._nein("unlesbar", "kein JSON-Objekt")
        art = anfrage.get("art")
        if art == "zustand":
            return self.zustand()
        if art != "anfrage":
            return self._nein("unbekannte_art", f"art={str(art)[:40]!r}")

        ticket = anfrage.get("ticket")
        if not isinstance(ticket, str) or not ticket.strip():
            return self._nein("kein_ticket", "Feld `ticket` fehlt oder ist leer")
        koerper = anfrage.get("koerper")
        if not isinstance(koerper, dict) or not koerper:
            return self._nein("kein_koerper",
                              "Feld `koerper` fehlt oder ist kein Objekt")

        # Fenster, dann Ticket, dann das Modell -- dieselbe Reihenfolge wie in
        # den beiden anderen Brokern. Lokal kostet der Aufruf kein Geld, aber
        # er kostet die GPU, und ein Ticket, das an der Obergrenze verfaellt,
        # waere trotzdem verbrannt.
        frei, rest_s = self._fenster_frei()
        if not frei:
            return self._nein("kontingent_fenster",
                              f"Obergrenze {self.hoechstens} je "
                              f"{self.fenster_s:.0f} s erreicht", rest_s=rest_s)

        quittung = self._hub(self.hub_socket, {
            "v": 1, "art": "einloesen", "ticket": ticket,
            "auftrag_hash": koerper_hash(koerper)})
        if not quittung.get("ok"):
            return self._nein("ticket_ungueltig",
                              str(quittung.get("grund", "Hub sagt nein"))[:120])

        with self._lock:
            self._fenster.append(time.monotonic())

        modell = self.modell_jetzt()
        if not modell:
            return self._nein("modell_fehlt",
                              f"kein Modell unter {self.tags} gemeldet -- "
                              "laeuft ollama, und ist eins gezogen?")

        t0 = time.monotonic()
        try:
            status, roh = self._http(
                self.ziel, nutzlast(koerper, modell=modell,
                                    num_predict=self.num_predict),
                timeout_s=self.timeout_s)
        except (OSError, urllib.error.URLError) as exc:
            return self._nein("modell_weg",
                              f"{type(exc).__name__} an {self.ziel}")
        except Exception as exc:      # noqa: BLE001 -- wie im Egress
            return self._nein("modell_weg", type(exc).__name__)
        dauer_ms = round((time.monotonic() - t0) * 1000, 2)

        if status == 404:
            # Ollama meldet ein nicht geladenes Modell mit 404. Das ist der
            # haeufigste Betriebsfehler und verdient einen eigenen Grund --
            # "modell_fehler" waere hier eine Diagnose weniger.
            #
            # Die ERKANNTE Wahl wird dabei verworfen: das Modell kann seit der
            # Erkennung entfernt worden sein, und ein Broker, der sich an
            # einen toten Namen klammert, bis jemand ihn neu startet, ist
            # genau die Falle, die dieser Task zumacht. Die ausdrueckliche
            # Wahl bleibt stehen -- sie ist eine Ansage, kein Fund.
            self._erkannt = None
            return self._nein("modell_fehlt", f"{modell!r} ist nicht gezogen")
        if status != 200:
            return self._nein("modell_fehler", f"HTTP {status}")
        try:
            daten = json.loads(roh)
        except (json.JSONDecodeError, ValueError):
            return self._nein("modell_fehler", "Antwort ist kein JSON")
        nachricht = (daten.get("message") or {}) if isinstance(daten, dict) else {}
        text = nachricht.get("content")
        if (not isinstance(text, str) or not text.strip()) and \
                (nachricht.get("thinking") or "").strip():
            # Eigener Grund, kein `modell_fehler`: das Modell hat gedacht
            # statt geantwortet, obwohl `think: false` mitging. Das ist ein
            # Befund ueber das MODELL (eine Reasoning-Variante) und kein
            # Transportfehler -- wer beides gleich nennt, sucht an der
            # falschen Stelle. Die Denkspur selbst geht NICHT in die Absage.
            return self._nein("modell_denkt",
                              f"{self.modell!r} liefert eine Denkspur statt "
                              "einer Antwort -- Instruct-Variante nehmen")
        if not isinstance(text, str) or not text.strip():
            return self._nein("modell_fehler", "keine Antwort im Feld `message`")

        self.anfragen += 1
        antwort = {"content": [{"type": "text", "text": text.strip()}]}
        bytes_ = len(json.dumps(antwort, ensure_ascii=False).encode("utf-8"))
        self.log.info("lokal", DAIMON_ACTION="lokal_anfrage",
                      DAIMON_TICKET=ticket[:12], DAIMON_BYTES=bytes_,
                      DAIMON_STATUS=200, DAIMON_DAUER_MS=dauer_ms)
        return {"v": 1, "ok": True, "status": 200, "bytes": bytes_,
                "dauer_ms": dauer_ms, "antwort": antwort}

    def zustand(self) -> dict:
        # `modell` ist, was WIRKLICH benutzt wird -- der Fund, wenn keine
        # ausdrueckliche Wahl vorliegt. `self.modell` allein staende hier auf
        # `None`, waehrend ein Modell laeuft, und die Auskunft waere die
        # unbrauchbare Sorte: formal richtig, im Betrieb nutzlos.
        return {"v": 1, "ok": True, "modell": self.modell_jetzt(),
                "modell_gewaehlt": self.modell, "ziel": self.ziel,
                "anfragen": self.anfragen, "fenster_s": self.fenster_s,
                "hoechstens": self.hoechstens}


def bediene(broker: LokalBroker, conn: socket.socket) -> None:
    with conn:
        conn.settimeout(180.0)
        try:
            anfrage = json.loads(conn.makefile("rb").readline(MAX_ZEILE))
        except (OSError, json.JSONDecodeError, ValueError):
            anfrage = None
        antwort = (broker.anfrage(anfrage) if anfrage is not None
                   else {"v": 1, "ok": False, "grund": "unlesbar",
                         "meldung": "keine lesbare JSON-Zeile"})
        try:
            conn.sendall(antwortzeile(antwort))
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="dAImon lokaler Broker (T-3.11c)")
    ap.add_argument("--socket", default=None)
    ap.add_argument("--hub-socket", default=None)
    ap.add_argument("--modell", default=None)
    args = ap.parse_args(argv)

    cfg = load_config(make_dirs=False)
    pfad = Path(args.socket or cfg.runtime_dir / LOKAL_SOCKET)
    broker = LokalBroker(
        cfg, hub_socket=args.hub_socket or str(cfg.runtime_dir / TICKET_SOCKET),
        ziel=str(cfg.get("lokal.ziel", ZIEL)),
        # `str()` NUR auf einen echten Wert: die Vorgabe ist seit dem 17.08.
        # `None`, und `str(None)` haette daraus den Modellnamen "None"
        # gemacht -- ein Name, den Ollama brav mit 404 quittiert. Live
        # gemessen, drei Minuten nach der Umstellung.
        modell=modell_waehlen(args.modell, cfg.get("lokal.modell", MODELL)),
        timeout_s=float(cfg.get("lokal.timeout_s", TIMEOUT_S)),
        fenster_s=float(cfg.get("lokal.fenster_s", FENSTER_S)),
        hoechstens=int(cfg.get("lokal.hoechstens", HOECHSTENS)),
        num_predict=int(cfg.get("lokal.num_predict", NUM_PREDICT)))

    pfad.parent.mkdir(parents=True, exist_ok=True)
    if pfad.exists():
        pfad.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    os.chmod(pfad, 0o600)
    srv.listen(8)
    broker.log.info("Lokaler Broker laeuft", DAIMON_ACTION="start",
                    DAIMON_MODELL=broker.modell, DAIMON_ZIEL=broker.ziel)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=bediene, args=(broker, conn),
                         daemon=True).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
