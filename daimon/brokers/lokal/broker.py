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
# Die Rumpfumsetzung liegt beim MIND, nicht hier. Der Broker haelt den Draht
# und wendet sie an; die Zusage "ich frage lokal" macht der Mind, und mit ihr
# gehoert ihm die Form. Zwei Fassungen waeren eine Regel und eine Attrappe
# (CLAUDE.md Regel 4) -- und geprueft waere erfahrungsgemaess die andere.
from daimon.mind.lokal import anfrage_rumpf, antwort_bloecke

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
# Frist fuer EINEN Versuch. Der Deckel darueber ist `GESAMT_S` -- die beiden
# gehoeren zusammen und werden nicht einzeln gesetzt.
TIMEOUT_S = 120.0
# Deckel ueber ALLE Versuche samt Rueckstau, gerechnet und nicht geraten:
# der Mind wartet auf `lokal.sock` 180 s (`mind/daemon.py: hub_anfrage`), und
# `bediene()` unten gibt derselben Verbindung 180 s. Wer laenger rechnet,
# antwortet in eine Leitung, die niemand mehr liest. 150 s lassen 30 s Luft
# fuer Ticket, Rumpfbau und die Antwortzeile.
GESAMT_S = 150.0
# Hoechstens so viele Versuche. Am 27.08. gemessen: ein Ollama unter Fremdlast
# antwortet SPAET (49,7 s bei zwei, 79,7 s bei drei Fremdclients), nicht mit
# einem eigenen Fehler -- selbst 14 gleichzeitige Anfragen gegen
# `OLLAMA_MAX_QUEUE=8` kamen alle mit 200 zurueck. Ein zweiter Versuch
# unmittelbar nach einer Zeitueberschreitung lief erneut in die Frist; erst
# nach Ende der Fremdlast kam dieselbe Frage in 8,4 s. Wiederholung zahlt sich
# also NICHT gegen Langsamkeit aus -- sondern gegen den Abriss (56 Neustarts
# von `ollama.service` an einem Tag, `RemoteDisconnected`), und der faellt
# SCHNELL. Deshalb bekommt jeder Versuch den Rest der Gesamtfrist: ein
# schneller Fehlschlag laesst Zeit fuer einen weiteren, eine lange Wartezeit
# nicht.
VERSUCHE = 3
# Rueckstau zwischen zwei Versuchen, verdoppelt sich. Klein, weil die Zeit im
# Warten fuer den Nutzer dieselbe Wartezeit ist wie die im Rechnen.
RUECKSTAU_S = 2.0
FENSTER_S = 60.0
HOECHSTENS = 30
# Deckel fuer die Antwortlaenge. Gesprochen werden ohnehin hoechstens 140
# Zeichen (T-3.9); alles darueber ist Rechenzeit fuer etwas, das der Validator
# abweist. Grosszuegig, damit ein Satz nicht mitten drin endet.
NUM_PREDICT = 160

GRUENDE = frozenset({
    "unlesbar", "unbekannte_art", "kein_ticket", "kein_koerper",
    "ticket_ungueltig", "kontingent_fenster",
    # `modell_beschaeftigt` ist NICHT dasselbe wie `modell_weg`, auch wenn
    # beide bis zum 27.08. so hiessen: das eine ist ein Endpunkt, der nicht
    # antwortet, das andere einer, der GAR NICHT DA ist. Am 27.08. gemessen
    # sind das zwei verschiedene Ausgaenge desselben Aufrufs -- eine
    # Zeitueberschreitung (`TimeoutError`, nach der vollen Frist) gegen ein
    # `URLError [Errno 111] Connection refused` nach 0,0 s. Wer beides gleich
    # nennt, sucht bei einer belegten GPU nach einem toten Dienst.
    "modell_weg", "modell_beschaeftigt",
    "modell_fehlt", "modell_fehler", "modell_denkt",
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
    """Der Ollama-Chat-Koerper -- die Fassung aus `daimon/mind/lokal.py`.

    Bis zum 26.08. stand die Umsetzung HIER, und sie liess `tools` fallen.
    Das war die Luecke, an der der lokale Weg fuer Durchgang 1 endete: der
    Mind schickte seine Werkzeugliste, der Broker warf sie weg, und das
    Modell konnte gar kein Werkzeug rufen. Kein Fehler war sichtbar -- es kam
    nur nie ein `tool_use`.

    Der Deckel fuer die Antwortlaenge bleibt beim BROKER: er kostet GPU-Zeit
    auf dieser Maschine, und `max_tokens` aus dem Koerper ist die Angabe des
    Mind fuer ein fremdes Modell.
    """
    return anfrage_rumpf(koerper, modell=modell, num_predict=int(num_predict))


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
                 timeout_s: float = TIMEOUT_S, gesamt_s: float = GESAMT_S,
                 versuche: int = VERSUCHE, rueckstau_s: float = RUECKSTAU_S,
                 fenster_s: float = FENSTER_S,
                 hoechstens: int = HOECHSTENS,
                 num_predict: int = NUM_PREDICT,
                 schlafen: Callable[[float], None] = time.sleep,
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
        self.gesamt_s = float(gesamt_s)
        self.versuche = max(1, int(versuche))
        self.rueckstau_s = float(rueckstau_s)
        self._schlafen = schlafen
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

    def _rufen(self, last: dict) -> tuple[int, str] | dict:
        """Der Aufruf an Ollama, mit Wiederholung. `(status, rumpf)` oder eine
        fertige Absage.

        Die Wiederholung sitzt HIER und nicht um `anfrage()` herum, und das
        ist der ganze Punkt: das Ticket ist zu diesem Zeitpunkt bereits
        eingeloest und das Fenster gezaehlt. Ein zweiter Versuch ist derselbe
        bezahlte Auftrag, kein neuer -- wer ihn oben ansetzte, zoege je
        Wiederholung ein weiteres Ticket und verbrauchte das Kontingent
        dreifach fuer eine Antwort.

        Jeder Versuch bekommt den REST der Gesamtfrist (hoechstens
        `timeout_s`), nicht eine feste eigene: gegen Langsamkeit hilft
        Wiederholen nachweislich nicht (27.08. gemessen), gegen einen Abriss
        schon -- und der faellt schnell und laesst die Frist stehen.
        """
        frist = time.monotonic() + self.gesamt_s
        rueckstau = self.rueckstau_s
        letzte = ("modell_weg", "kein Versuch unternommen")
        versuch = 0
        rest = self.gesamt_s
        while versuch < self.versuche and rest > 0:
            versuch += 1
            try:
                return self._http(self.ziel, last,
                                  timeout_s=min(self.timeout_s, rest))
            except urllib.error.URLError as exc:
                # Eine Zeitueberschreitung beim VERBINDEN kommt hier verpackt
                # an, eine beim Lesen als blanker `TimeoutError`. Beide meinen
                # dasselbe und muessen denselben Grund bekommen.
                letzte = (("modell_beschaeftigt"
                           if isinstance(exc.reason, TimeoutError)
                           else "modell_weg"),
                          f"{type(exc).__name__} an {self.ziel}")
            except TimeoutError:
                letzte = ("modell_beschaeftigt",
                          f"keine Antwort binnen {min(self.timeout_s, rest):.0f} s "
                          f"-- Modell belegt")
            except OSError as exc:
                # `RemoteDisconnected` faellt hier hinein (Unterklasse von
                # `ConnectionResetError`) -- der Fall der 56 Neustarts.
                letzte = ("modell_weg", f"{type(exc).__name__} an {self.ziel}")
            except Exception as exc:      # noqa: BLE001 -- wie im Egress
                letzte = ("modell_weg", type(exc).__name__)
            # Ein weiterer Versuch lohnt nur, wenn nach dem Rueckstau noch Zeit
            # zum Fragen bleibt. Keine eigene Untergrenze dafuer: die Frist
            # rechnet sich selbst leer, und eine zweite Zahl waere eine zweite
            # Fassung derselben Regel.
            rest = frist - time.monotonic() - rueckstau
            if versuch >= self.versuche or rest <= 0:
                break
            self._schlafen(rueckstau)
            rueckstau *= 2
        return self._nein(letzte[0], letzte[1], versuche=versuch)

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
        ergebnis = self._rufen(nutzlast(koerper, modell=modell,
                                        num_predict=self.num_predict))
        if isinstance(ergebnis, dict):
            self.log.warn("Modell nicht erreicht", DAIMON_ACTION="lokal_absage",
                          DAIMON_TICKET=ticket[:12],
                          DAIMON_GRUND=str(ergebnis.get("grund"))[:40],
                          DAIMON_VERSUCHE=ergebnis.get("versuche"),
                          DAIMON_DAUER_MS=round((time.monotonic() - t0) * 1000, 2))
            return ergebnis
        status, roh = ergebnis
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
        # Die Blockliste kommt aus derselben Fassung, die der Mind liest.
        # Sie kann `text`, `tool_use` oder beides tragen.
        bloecke = antwort_bloecke(nachricht)
        # LEER heisst hier "nichts Verwertbares", nicht mehr "kein Text": ein
        # Modell, das ein Werkzeug ruft, sagt oft NICHTS dazu -- `content` ist
        # dann leer, und die alte Pruefung auf `content` allein wies genau die
        # Antwort ab, wegen der Durchgang 1 den lokalen Weg ueberhaupt nimmt.
        # Sichtbar war das nirgends: die Absage hiess "modell_fehler".
        if not bloecke:
            if (nachricht.get("thinking") or "").strip():
                # Eigener Grund, kein `modell_fehler`: das Modell hat gedacht
                # statt geantwortet, obwohl `think: false` mitging. Das ist ein
                # Befund ueber das MODELL (eine Reasoning-Variante) und kein
                # Transportfehler -- wer beides gleich nennt, sucht an der
                # falschen Stelle. Die Denkspur selbst geht NICHT in die Absage.
                return self._nein("modell_denkt",
                                  f"{self.modell!r} liefert eine Denkspur statt "
                                  "einer Antwort -- Instruct-Variante nehmen")
            return self._nein("modell_fehler", "keine Antwort im Feld `message`")

        self.anfragen += 1
        antwort = {"content": bloecke}
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
                "hoechstens": self.hoechstens,
                # Damit die Obergrenze von aussen ablesbar ist statt aus drei
                # Konstanten zusammengesucht: `gesamt_s` ist die Zahl, die
                # gilt.
                "gesamt_s": self.gesamt_s, "versuche": self.versuche}


def bediene(broker: LokalBroker, conn: socket.socket) -> None:
    with conn:
        # 180 s ist die Frist, unter der `GESAMT_S` bleiben MUSS -- dieselbe
        # Zahl wartet auf der anderen Seite der Leitung im Mind
        # (`mind/daemon.py: hub_anfrage`). Wer eine der beiden anfasst, fasst
        # beide an, sonst rechnet dieser Broker in eine Leitung hinein, die
        # niemand mehr liest.
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
        gesamt_s=float(cfg.get("lokal.gesamt_s", GESAMT_S)),
        versuche=int(cfg.get("lokal.versuche", VERSUCHE)),
        rueckstau_s=float(cfg.get("lokal.rueckstau_s", RUECKSTAU_S)),
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
