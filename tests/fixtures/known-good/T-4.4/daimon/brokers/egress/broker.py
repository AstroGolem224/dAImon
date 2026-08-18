"""T-3.11 — die einzige ausgehende Verbindung, in einem Prozess ohne Inhalt.

Die Prozessgrenze IST der Task
----------------------------------------------------------------------------
    Mind (daimon-mind)                 Egress (daimon-egress)
    - RestrictAddressFamilies=AF_UNIX  - AF_UNIX + AF_INET + AF_INET6
    - KEIN Token, nirgends             - Token ueber LoadCredential=
    - baut den Anfragekoerper          - transportiert ihn OPAK
    - hat den Persona-Prompt           - kennt den Inhalt nicht

In v2.0 des Entwurfs hielt Mind beides -- Netz und Token. "Kein API-Aufruf ohne
Autorisierung" war damit reine Anwendungslogik in genau dem Prozess, der die
Anfragen stellt: ein Fehler dort, und die Zusage ist weg. Jetzt steht die erste
Haelfte in der Unit (der Kernel laesst Mind keinen AF_INET-Socket anlegen) und die
zweite im Protokollformat.

Warum stdlib und nicht `requests`
----------------------------------------------------------------------------
`http.client` plus `ssl` fragt **keine** Proxy-Umgebungsvariablen. Mit `requests`
oder `urllib` muesste man `trust_env=False` bzw. einen leeren `ProxyHandler`
setzen und daran denken -- hier ist es die Vorgabe. "Kein Proxy aus der Umgebung"
ist damit eine Eigenschaft des Bauwerks und keine Einstellung, die jemand
vergessen kann. Die Unit loescht die Variablen zusaetzlich; zwei Netze, weil eine
Umgebungsvariable durch eine Drop-in-Datei zurueckkommen kann.

Was protokolliert wird
----------------------------------------------------------------------------
`{ticket, bytes, status, dauer_ms}` -- und NICHTS weiter. Kein Koerper, kein
Antworttext, kein Modellname, keine Nachrichtenzahl, kein Auszug. Der Egress ist
ein Transport; was er transportiert, geht ihn nichts an.

Der Token erscheint in keinem Log, und das ist keine Absichtserklaerung: jede
Logzeile dieses Moduls laeuft durch `redigieren()`, nicht nur die, an die jemand
gedacht hat. Auch Laengen und Praefixe fallen -- "sk-ant-...(72 Zeichen)" ist
schon ein Auszug.

Das Kontingent
----------------------------------------------------------------------------
Jede Anfrage braucht ein Einmal-Ticket aus dem Hub-Ticketbuch (T-0.8), gebunden
an den **Hash dieses Koerpers**. Ohne die Bindung waere ein Ticket eine Zaehlung
und keine Autorisierung: es wuerde Anfrage A bezahlen und Anfrage B mitnehmen.
Den Hash bildet der EGRESS selbst -- Mind liefert ihn nicht und kann ihn deshalb
nicht faelschen.

Dazu eine Obergrenze je Zeitfenster, gegen die Schleife in Mind. Sie wird
absichtlich NICHT persistiert: ein Neustart ist ein legitimer Neuanfang, und eine
Schleife in Mind kann den Egress nicht neu starten. (Anders als die Abkuehlung in
T-3.9, die genau deshalb ueber den Reboot haelt.)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import threading
import time
from collections import deque
from http import client as httpclient
from typing import Any

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger

EGRESS_SOCKET = "egress.sock"
TICKET_SOCKET = "ticket.sock"          # der Kontingent-Endpunkt des Hubs
LISTEN_FDS_START = 3
MAX_ZEILE = 1 << 20                    # 1 MiB: ein Anfragekoerper ist Kilobytes

# DAS ZIEL STEHT IM CODE, nicht in der Konfiguration. Ein Konfigurationswert
# waere ein Schalter, mit dem sich der Egress umleiten laesst -- und genau das
# darf er nicht sein. Wer das Ziel aendert, aendert Code und wird gelesen.
ZIEL_HOST = "api.anthropic.com"
ZIEL_PFAD = "/v1/messages"
ZIEL_PORT = 443
API_VERSION = "2023-06-01"

# Der Name der Credential-Datei unter $CREDENTIALS_DIRECTORY. Er steht hier
# als Konstante, weil er ein VERTRAG zwischen Unit und Code ist: die Unit
# schreibt `LoadCredential=anthropic-token:...`, dieses Modul liest denselben
# Namen. Beim Gegenlesen am 04.08. hiessen die beiden Seiten verschieden --
# meine `api-token` gegen `anthropic-token` im Pruefstand -- und mein Vertrag
# hatte den Namen nicht festgelegt. Eine Zusage, die aus zwei Dateien besteht,
# braucht den Namen an einer Stelle.
CREDENTIAL_NAME = "anthropic-token"

# Der Testschalter, und er ist absichtlich unbequem: `DAIMON_EGRESS_ZIEL` allein
# wirkt NICHT. Erst zusammen mit `DAIMON_EGRESS_TESTPROFIL=1`, und dann steht
# `testprofil: true` im Zustand. Ein Schalter, den man nicht sieht, ist die
# Umleitung, die es hier nicht geben darf.
UMGEBUNG_ZIEL = "DAIMON_EGRESS_ZIEL"
UMGEBUNG_TESTPROFIL = "DAIMON_EGRESS_TESTPROFIL"

# Proxy-Variablen. `http.client` liest sie ohnehin nicht -- sie werden zusaetzlich
# aus der eigenen Umgebung geloescht, damit kein spaeter hinzugefuegter Aufruf
# (etwa ein `urllib`-Einzeiler in einem Nachfolgetask) sie doch beachtet.
PROXY_VARIABLEN = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy")

FENSTER_S = 60.0
HOECHSTENS = 10

GRUENDE = frozenset({
    "unlesbar", "unbekannte_art", "kein_ticket", "ticket_ungueltig",
    "kein_koerper", "kontingent_fenster", "kein_token", "ziel_weg",
})

# Der Redaktionsmarker. `[REDACTED]` und nicht `[redigiert]`, obwohl dieses
# Projekt deutsch schreibt: der Marker ist eine MASCHINENLESBARE Marke, nach der
# ein Pruefstand suchen kann, und mein Vertrag hat ihn nicht benannt (Fund vom
# 04.08., Vertragsluecke wie `anthropic-token` und der kanonische Hash). Wer
# einen Marker nicht festlegt, hat die Zusage "der Token erscheint redigiert"
# nur zur Haelfte geschrieben -- "er fehlt" und "er wurde entfernt" sind ohne
# Marke nicht zu unterscheiden.
MARKER = "[REDACTED]"

# Alles, was nach einem Schluessel aussieht. Absichtlich breit: ein Muster, das
# nur `sk-ant-` kennt, laesst den naechsten Schluesseltyp durch.
_GEHEIM = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|(?i:x-api-key)\s*[:=]\s*\S+"
    r"|[A-Za-z0-9_\-]{40,}")            # jede lange Zeichenkette ohne Leerraum


def redigieren(text: object) -> str:
    """Alles Geheimniswuerdige zu `[redigiert]`.

    Laeuft auf JEDE Logzeile dieses Moduls, nicht nur auf die, an die jemand
    gedacht hat -- und auch auf Fehlermeldungen, denn dort landen Tokens am
    haeufigsten (eine HTTP-Bibliothek, die den Header in die Ausnahme schreibt).
    Laengen und Praefixe fallen mit: "sk-ant-...(72 Zeichen)" ist schon ein
    Auszug.
    """
    return _GEHEIM.sub(MARKER, str(text))


class RedigierenderLogger:
    """Ein Logger, durch den kein Geheimnis kommt.

    Kein Mixin und keine Unterklasse: der Egress soll seinen Logger nicht
    umgehen KOENNEN. Wer hier am `_echt` vorbei schreibt, muss es absichtlich
    tun, und dann steht es im Diff.
    """

    def __init__(self, echt: Logger) -> None:
        self._echt = echt

    def _felder(self, felder: dict) -> dict:
        return {k: redigieren(v) if isinstance(v, str) else v
                for k, v in felder.items()}

    def info(self, m: str, **f: Any) -> None:
        self._echt.info(redigieren(m), **self._felder(f))

    def warn(self, m: str, **f: Any) -> None:
        self._echt.warn(redigieren(m), **self._felder(f))

    def error(self, m: str, **f: Any) -> None:
        self._echt.error(redigieren(m), **self._felder(f))


def token_lesen() -> tuple[str, str]:
    """`(token, herkunft)` aus `$CREDENTIALS_DIRECTORY`. Nie aus der Umgebung.

    `LoadCredential=` legt die Datei in ein tmpfs, das nur dieser Unit gehoert.
    Eine Umgebungsvariable stuende dagegen in `/proc/<pid>/environ` -- lesbar
    fuer jeden Prozess desselben Nutzers, und genau das prueft der Pruefstand.
    """
    verzeichnis = os.environ.get("CREDENTIALS_DIRECTORY")
    if not verzeichnis:
        return ("", "kein CREDENTIALS_DIRECTORY (LoadCredential= fehlt)")
    pfad = os.path.join(verzeichnis, CREDENTIAL_NAME)
    try:
        with open(pfad, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError as exc:
        return ("", f"{pfad}: {exc.strerror}")
    if not token:
        return ("", f"{pfad} ist leer")
    return (token, "LoadCredential")


def koerper_hash(koerper: object) -> str:
    """Der Hash, an den das Ticket gebunden ist.

    `sort_keys` und feste Trenner: derselbe Koerper muss denselben Hash ergeben,
    auch wenn die Schluessel in anderer Reihenfolge kommen. Sonst waere die
    Bindung von der Laune eines JSON-Serialisierers abhaengig.
    """
    roh = json.dumps(koerper, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(roh).hexdigest()


class RohJSON:
    """Bytes, die UNVERAENDERT in die Antwortzeile wandern.

    Der Vertrag sagt "`antwort` kommt unveraendert zurueck. Nicht geparst, nicht
    gerendert, nicht gekuerzt". Meine erste Fassung hat sie mit `json.loads`
    geparst und neu serialisiert -- das ist genau das Gegenteil, und es ist beim
    ersten Arbeitsbaumlauf aufgefallen: aus `{"id":"lokal"}` wurde
    `{"id": "lokal"}`. Harmlos aussehend, aber der Egress hatte den Inhalt damit
    in der Hand, und "interpretiert keine Koerper" war Prosa statt Bauwerk.
    """

    __slots__ = ("bytes",)

    def __init__(self, roh: bytes) -> None:
        self.bytes = roh


def antwortzeile(antwort: dict) -> bytes:
    """Die Ausgangszeile. `antwort` steht als LETZTES Feld und roh."""
    roh = antwort.get("antwort")
    if not isinstance(roh, RohJSON):
        return json.dumps(antwort).encode() + b"\n"
    kopf = json.dumps({k: v for k, v in antwort.items() if k != "antwort"})
    return kopf[:-1].encode("utf-8") + b',"antwort":' + roh.bytes + b"}\n"


def roher_koerper(zeile: bytes, koerper: object) -> bytes | None:
    """Die Originalbytes des Werts `koerper` aus der Anfragezeile.

    Der Egress schickt weiter, was Mind geschrieben hat -- Byte fuer Byte, samt
    Schluesselreihenfolge und Leerzeichen. Neu serialisieren waere ein zweiter
    Autor, und der Vertrag verbietet ihn ("kein Feld ergaenzt, keines entfernt,
    keine Umsortierung").

    Der Hash fuer das Ticket bleibt davon unberuehrt: der ist ausdruecklich
    kanonisch (aus dem geparsten Objekt), weil beide Seiten ihn unabhaengig
    nachrechnen koennen muessen.
    """
    text = zeile.decode("utf-8", "surrogateescape")
    dec = json.JSONDecoder()
    for treffer in re.finditer(r'"koerper"\s*:\s*', text):
        try:
            wert, ende = dec.raw_decode(text, treffer.end())
        except ValueError:
            continue
        # Ein Koerper, der die Zeichenkette `"koerper":` selbst enthaelt, wuerde
        # sonst den falschen Ausschnitt liefern.
        if wert == koerper:
            return text[treffer.end():ende].encode("utf-8", "surrogateescape")
    return None


def hub_anfrage(sock: str, anfrage: dict, *, timeout_s: float = 10.0) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout_s)
    try:
        c.connect(sock)
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        roh = c.makefile("rb").readline(MAX_ZEILE)
    except OSError as exc:
        return {"v": 1, "ok": False, "grund": "hub_weg",
                "meldung": str(exc)[:120]}
    finally:
        c.close()
    try:
        antwort = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return {"v": 1, "ok": False, "grund": "hub_weg",
                "meldung": "Antwort unlesbar"}
    return antwort if isinstance(antwort, dict) else {
        "v": 1, "ok": False, "grund": "hub_weg", "meldung": "kein Objekt"}


def _ziel_zerlegen(ziel: str) -> tuple[str, int, str, bool]:
    """`http://127.0.0.1:8443/v1/messages` -> `(host, port, pfad, tls)`."""
    from urllib.parse import urlparse
    u = urlparse(ziel)
    tls = u.scheme == "https"
    port = u.port or (443 if tls else 80)
    return (u.hostname or "127.0.0.1", port, u.path or ZIEL_PFAD, tls)


class Egress:
    """Transport. Kennt den Inhalt nicht und will ihn nicht kennen."""

    def __init__(self, *, hub_socket: str, fenster_s: float = FENSTER_S,
                 hoechstens: int = HOECHSTENS, log: Logger | None = None,
                 ca_datei: str | None = None) -> None:
        # Proxy-Variablen aus der eigenen Umgebung entfernen -- zweites Netz
        # neben der Unit, siehe Modulkopf.
        for name in PROXY_VARIABLEN:
            os.environ.pop(name, None)

        self.hub_socket = hub_socket
        self.fenster_s = float(fenster_s)
        self.hoechstens = int(hoechstens)
        self.log = RedigierenderLogger(log or get_logger("daimon-egress"))
        self._token, self._token_herkunft = token_lesen()
        self._lock = threading.Lock()
        self._fenster: deque[float] = deque()
        self.anfragen = 0

        # Testprofil: NUR wenn beide Variablen gesetzt sind.
        self.testprofil = (os.environ.get(UMGEBUNG_TESTPROFIL) == "1"
                           and bool(os.environ.get(UMGEBUNG_ZIEL)))
        if self.testprofil:
            self.host, self.port, self.pfad, self.tls = _ziel_zerlegen(
                os.environ[UMGEBUNG_ZIEL])
            # Der ROHE Wert der Variablen, nicht der zerlegte: `urlparse` wirft
            # den Userinfo-Teil (`http://sk-ant-...@host/`) weg, und dann steht
            # in der Logzeile nichts Geheimes mehr -- die Redaktion haette
            # nichts zu tun, und ihre Wirksamkeit waere unbeobachtbar. Genau so
            # war es am 04.08.: "Token erscheint nirgends" war gruen, weil der
            # Token nie in die Zeile kam. Hier geht er absichtlich hinein und
            # muss redigiert wieder herauskommen.
            roh_ziel = os.environ.get(UMGEBUNG_ZIEL, "")
            self.log.warn("TESTPROFIL aktiv -- das Ziel ist nicht die API",
                          DAIMON_ACTION="egress_testprofil",
                          DAIMON_ZIEL=roh_ziel)
            # Und dieselbe Zeile auf den eigenen Ausgabestrom. Der Projekt-
            # logger schickt Datagramme ans Journal, sobald dessen Socket da ist
            # -- auf stderr faellt er nur zurueck, wenn es ihn NICHT gibt. Damit
            # war die Redaktion an einem Prozess ohne Journalzugriff gar nicht
            # zu beobachten: "kein Token im Log" war gruen, weil ueberhaupt
            # nichts im Log stand. Eine Zusage braucht ihren Messpunkt dort, wo
            # gemessen wird.
            print(redigieren(f"TESTPROFIL aktiv -- Ziel {roh_ziel}"),
                  flush=True)
        else:
            self.host, self.port, self.pfad, self.tls = (
                ZIEL_HOST, ZIEL_PORT, ZIEL_PFAD, True)
        self.ca_datei = ca_datei or os.environ.get("DAIMON_EGRESS_CA") or None

    # -- Zustand -----------------------------------------------------------

    def ziel(self) -> str:
        schema = "https" if self.tls else "http"
        # Der Standardport wird WEGGELASSEN: der Vertrag nennt als Ziel
        # `https://api.anthropic.com/v1/messages`, und `:443` daranzuschreiben
        # macht aus einer festgelegten Zeichenkette eine zweite Schreibweise
        # derselben Sache. Zwei Schreibweisen sind eine zu viel, wenn jemand
        # gegen den Wert vergleicht.
        vorgabe = 443 if self.tls else 80
        haefen = "" if self.port == vorgabe else f":{self.port}"
        return f"{schema}://{self.host}{haefen}{self.pfad}"

    def zustand(self) -> dict:
        with self._lock:
            self._fenster_aufraeumen(time.monotonic())
            im_fenster = len(self._fenster)
        return {
            "v": 1, "ok": True, "ziel": self.ziel(),
            "testprofil": self.testprofil,
            # Ein WAHRHEITSWERT. Nie ein Auszug, nie eine Laenge -- beides waere
            # schon eine Aussage ueber das Geheimnis.
            "token_vorhanden": bool(self._token),
            "anfragen": self.anfragen, "fenster_s": self.fenster_s,
            "hoechstens": self.hoechstens, "im_fenster": im_fenster,
            "pid": os.getpid(),
        }

    # -- Obergrenze --------------------------------------------------------

    def _fenster_aufraeumen(self, jetzt: float) -> None:
        while self._fenster and jetzt - self._fenster[0] > self.fenster_s:
            self._fenster.popleft()

    def _fenster_frei(self) -> tuple[bool, float]:
        """`(frei, restsekunden)`. Monotone Zeit: eine NTP-Korrektur darf keine
        Obergrenze aufheben."""
        with self._lock:
            jetzt = time.monotonic()
            self._fenster_aufraeumen(jetzt)
            if len(self._fenster) < self.hoechstens:
                return (True, 0.0)
            if not self._fenster:
                # `hoechstens = 0`: es gibt gar kein Kontingent, und dann ist die
                # Warteschlange leer -- ein `self._fenster[0]` wirft hier
                # IndexError. Ein Egress, der an der Obergrenze ABSTUERZT statt
                # abzusagen, ist schlimmer als einer ohne Obergrenze: der
                # Aufrufer bekommt gar keine Antwort. Gefunden vom eigenen Test.
                # `rest_s` ist 0, weil kein Warten hilft -- das ist eine
                # Einstellung und kein Fenster.
                return (False, 0.0)
            return (False, round(self.fenster_s - (jetzt - self._fenster[0]), 3))

    # -- Der Weg nach draussen ---------------------------------------------

    def anfrage(self, anfrage: object, roh_koerper: bytes | None = None) -> dict:
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
        if not self._token:
            return self._nein("kein_token", self._token_herkunft)

        # Reihenfolge: Fenster, dann Ticket, dann Netz. Das Fenster ist lokal und
        # kostet nichts; ein Ticket einzuloesen VERBRAUCHT es, und ein Ticket, das
        # an der Obergrenze verfaellt, waere ein bezahltes Kontingent ohne
        # Gegenwert.
        frei, rest_s = self._fenster_frei()
        if not frei:
            return self._nein("kontingent_fenster",
                              f"Obergrenze {self.hoechstens} je "
                              f"{self.fenster_s:.0f} s erreicht",
                              rest_s=rest_s)

        quittung = hub_anfrage(self.hub_socket, {
            "v": 1, "art": "einloesen", "ticket": ticket,
            "auftrag_hash": koerper_hash(koerper)})
        if not quittung.get("ok"):
            return self._nein("ticket_ungueltig",
                              str(quittung.get("grund", "Hub sagt nein"))[:120])

        with self._lock:
            self._fenster.append(time.monotonic())

        t0 = time.monotonic()
        try:
            status, rohantwort = self._senden(koerper, roh_koerper)
        except Exception as exc:      # noqa: BLE001 -- siehe Begruendung
            # Absichtlich breit: was eine TLS- oder Netzbibliothek wirft, ist
            # nicht Teil ihrer Zusage. Die Meldung wird redigiert UND gekuerzt --
            # eine Ausnahme aus dem Transport kann Koerperinhalt enthalten (etwa
            # in einem Fehlerobjekt der Gegenseite), und dann waere die Zusage
            # "kein Koerper im Protokoll" durch die Hintertuer gebrochen.
            dauer_ms = round((time.monotonic() - t0) * 1000, 2)
            self.log.warn("Ziel nicht erreicht", DAIMON_ACTION="egress_fehler",
                          DAIMON_TICKET=ticket[:12], DAIMON_DAUER_MS=dauer_ms,
                          DAIMON_FEHLER=type(exc).__name__)
            return self._nein("ziel_weg",
                              f"{type(exc).__name__}: {str(exc)[:200]}")
        dauer_ms = round((time.monotonic() - t0) * 1000, 2)
        self.anfragen += 1

        # NUR diese vier Felder. Kein Koerper, kein Modellname, keine
        # Nachrichtenzahl -- der Plantext nennt sie einzeln.
        self._audit(ticket, len(rohantwort), status, dauer_ms)

        return {"v": 1, "ok": True, "status": status,
                "bytes": len(rohantwort), "dauer_ms": dauer_ms,
                # Roh, unveraendert, ungeparst -- siehe `RohJSON`.
                "antwort": RohJSON(rohantwort)}

    def _audit(self, ticket: str, bytes_: int, status: int,
               dauer_ms: float) -> None:
        """Ein Audit-Datensatz je durchgereichter Anfrage, mit EXAKT vier Feldern.

        Eigene Zeile und nicht der Projektlogger: der schreibt Zeitstempel,
        Stufe und Meldung dazu, und dann steht in der Zeile mehr als
        `{ticket, bytes, status, dauer_ms}`. "Ausschliesslich diese vier" ist
        nur pruefbar, wenn eine Zeile auch wirklich nur aus ihnen besteht.
        Diagnosezeilen daneben bleiben erlaubt -- sie tragen kein `ticket`.
        """
        satz = {"ticket": redigieren(ticket), "bytes": bytes_,
                "status": status, "dauer_ms": dauer_ms}
        print(json.dumps(satz), flush=True)

    def _senden(self, koerper: dict,
                roh_koerper: bytes | None = None) -> tuple[int, bytes]:
        """Der eigentliche Transport. `http.client` fragt keine Proxy-Variablen."""
        # Bevorzugt die Originalbytes des Aufrufers. Nur wenn keine vorliegen
        # (Aufruf aus einem Test, nicht ueber den Socket) wird serialisiert.
        nutzlast = (roh_koerper if roh_koerper is not None else
                    json.dumps(koerper, ensure_ascii=False).encode("utf-8"))
        kopf = {"content-type": "application/json",
                "x-api-key": self._token,
                "anthropic-version": API_VERSION,
                "accept": "application/json"}
        if self.tls:
            # `create_default_context` hat Hostnamenspruefung und Verifikation
            # AN. Wer sie abschaltet, tut es sichtbar und in einem Diff.
            kontext = ssl.create_default_context(cafile=self.ca_datei)
            verbindung = httpclient.HTTPSConnection(
                self.host, self.port, timeout=120, context=kontext)
        else:
            # Nur im Testprofil erreichbar: `ZIEL_PORT` ist 443 und `tls` steht
            # im Normalbetrieb fest auf True.
            verbindung = httpclient.HTTPConnection(
                self.host, self.port, timeout=120)
        try:
            verbindung.request("POST", self.pfad, body=nutzlast, headers=kopf)
            antwort = verbindung.getresponse()
            return (antwort.status, antwort.read())
        finally:
            verbindung.close()

    def _nein(self, grund: str, meldung: str, **extra: Any) -> dict:
        assert grund in GRUENDE, grund
        return {"v": 1, "ok": False, "grund": grund,
                "meldung": redigieren(meldung)[:300], **extra}


# -- Sockets (Muster aus daimon/gpu/worker.py) ------------------------------

def sd_socket() -> socket.socket | None:
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        n = int(os.environ.get("LISTEN_FDS", "0") or 0)
    except ValueError:
        return None
    if n < 1:
        return None
    return socket.socket(fileno=LISTEN_FDS_START, family=socket.AF_UNIX,
                         type=socket.SOCK_STREAM)


def eigener_socket(pfad: str) -> socket.socket:
    if os.path.exists(pfad):
        os.unlink(pfad)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(pfad)
    os.chmod(pfad, 0o600)
    srv.listen(8)
    return srv


def bediene(egress: Egress, conn: socket.socket) -> None:
    with conn:
        conn.settimeout(180.0)
        roh = b""
        try:
            roh = conn.makefile("rb").readline(MAX_ZEILE)
            anfrage = json.loads(roh)
        except (OSError, json.JSONDecodeError, ValueError):
            anfrage = None
        rohk = (roher_koerper(roh, anfrage.get("koerper"))
                if isinstance(anfrage, dict) else None)
        antwort = (egress.anfrage(anfrage, rohk) if anfrage is not None
                   else {"v": 1, "ok": False, "grund": "unlesbar",
                         "meldung": "keine lesbare JSON-Zeile"})
        try:
            conn.sendall(antwortzeile(antwort))
        except OSError:
            pass


def lauf(egress: Egress, srv: socket.socket) -> int:
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=bediene, args=(egress, conn),
                         daemon=True).start()
    return 0


def einstellungen(cfg: Config) -> dict:
    return {"fenster_s": float(cfg.get("egress.fenster_s", FENSTER_S)),
            "hoechstens": int(cfg.get("egress.hoechstens", HOECHSTENS))}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="dAImon Egress (T-3.11)")
    ap.add_argument("--socket", default=None)
    ap.add_argument("--hub-socket", default=None)
    ap.add_argument("--ca", default=None,
                    help="Vertrauensanker fuer das Testprofil")
    args = ap.parse_args(argv)

    cfg = load_config(make_dirs=False)
    hub_socket = args.hub_socket or str(cfg.runtime_dir / TICKET_SOCKET)
    egress = Egress(hub_socket=hub_socket, ca_datei=args.ca,
                    **einstellungen(cfg))

    srv = sd_socket()
    if srv is None:
        # Ohne Socket-Aktivierung UND ohne `--socket`: der dokumentierte
        # Vorgabepfad unter $XDG_RUNTIME_DIR. Vorher stand hier ein
        # `SystemExit` -- die Absicht war, einen gestarteten und unerreichbaren
        # Dienst zu verhindern. Das war zu streng und hat zwei Dinge gekostet:
        # eine KOPIE der Unit ohne `%t`-Substitution ist unstartbar (am 04.08.
        # im Pruefstand aufgefallen), und ein Mensch, der den Dienst von Hand
        # startet, bekommt einen Fehler statt eines Dienstes. Unerreichbar ist er
        # trotzdem nicht: der Pfad ist derselbe, den die Socket-Unit benutzt, und
        # er steht im Log.
        pfad = args.socket or str(cfg.runtime_dir / EGRESS_SOCKET)
        srv = eigener_socket(pfad)
    try:
        return lauf(egress, srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
