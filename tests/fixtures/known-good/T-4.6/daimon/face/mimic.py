"""T-3.16 — der Mimic-Client. Verbinden, senden, Rahmen lesen, Fristen halten.

Client und nur Client (PHASE2.md Schritt 8): keine Wiedergabe, keine Policy,
kein `pw-cat`. Was hier entsteht, ist eine abbrechbare Sitzung mit einer
Samplerate und einem Strom von PCM-Blöcken; wer daraus Ton macht, ist `tts.py`.

Warum HTTP von Hand und nicht `http.client`
----------------------------------------------------------------------------
Zwei Gründe, beide aus dem Vertrag. Erstens braucht Schritt 13 **eine**
monotone Gesamtfrist vom Verbindungsbeginn bis zum ersten `A`-Rahmen, nicht
drei aufeinanderfolgende Socket-Timeouts, die sich summieren. Zweitens muss
Schritt 10 die Sitzung von einem **fremden** Thread abbrechen können, und zwar
mit `shutdown(SHUT_RDWR)` vor `close()` -- ein blosses `close()` weckt einen
Thread, der in `recv()` hängt, nicht zuverlässig. Beides ist an
`http.client.HTTPResponse` vorbei einfacher als hindurch.

Die Frist ist eine Gesamtfrist, weil Mimic den 200er-Kopf erst sendet, wenn
sein erster `A`-Rahmen steht: Kopf und erstes Audio sind dasselbe Ereignis, und
eine getrennte „erster Rahmen"-Frist danach wäre wirkungslos.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
import uuid

FRAME = struct.Struct(">cI")
MAX_RAHMEN_BYTES = 64 * 1024 * 1024
MAX_KOPF_BYTES = 64 * 1024

# Schritt 13: eine Gesamtfrist bis zum ersten Ton, danach ein Rahmenabstand.
GESAMTFRIST_S = 0.5
RAHMENABSTAND_S = 2.0
# Schritt 12a: der Warmlauf ist best effort und darf die Gegenwart nicht
# aufhalten. Schritt 6: die Startpruefung ebenso wenig.
WARM_FRIST_S = 0.2
STATUS_FRIST_S = 0.3


class MimicFehler(Exception):
    """Mimic hat nicht geliefert. `grund` ist maschinenlesbar, immer gesetzt."""

    def __init__(self, grund: str, meldung: str = "") -> None:
        super().__init__(meldung or grund)
        self.grund = grund
        self.meldung = meldung or grund


def korrelations_id() -> str:
    return uuid.uuid4().hex


def socket_vorgabe() -> str:
    """Mimics Socket aus `XDG_RUNTIME_DIR`.

    `%t` ist ein systemd-Spezifizierer und expandiert in TOML **nicht** --
    wer ihn dort hinschreibt, bekommt ein Verzeichnis namens `%t`.
    """
    basis = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(basis, "mimic", "mimic.socket") if basis else ""


class _Strom:
    """Rohbytes vom Socket, mit einer Frist, die von aussen kommt.

    `lesen()` bekommt die verbleibende Zeit gesagt, statt sie selbst zu
    verwalten: die Frist gehoert dem Aufrufer, weil sie eine Gesamtfrist ist.
    """

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.puffer = b""
        self.chunk_rest = 0        # offene Bytes im laufenden HTTP-Chunk

    def _nachfuellen(self, ende: float) -> None:
        rest = ende - time.monotonic()
        if rest <= 0:
            raise MimicFehler("frist", "Frist gerissen")
        self.sock.settimeout(rest)
        try:
            block = self.sock.recv(65536)
        except socket.timeout:
            raise MimicFehler("frist", "Frist gerissen") from None
        except OSError as exc:
            raise MimicFehler("verbindung", str(exc)[:120]) from exc
        if not block:
            raise MimicFehler("eof", "Verbindung endete vor dem Rahmen")
        self.puffer += block

    def genau(self, anzahl: int, ende: float) -> bytes:
        while len(self.puffer) < anzahl:
            self._nachfuellen(ende)
        aus, self.puffer = self.puffer[:anzahl], self.puffer[anzahl:]
        return aus

    def zeile(self, ende: float) -> bytes:
        while b"\r\n" not in self.puffer:
            if len(self.puffer) > MAX_KOPF_BYTES:
                raise MimicFehler("protokoll", "Kopfzeile zu lang")
            self._nachfuellen(ende)
        zeile, _, self.puffer = self.puffer.partition(b"\r\n")
        return zeile


class Sitzung:
    """Eine laufende Mimic-Äußerung. Abbrechbar von jedem Thread.

    Der `H`-Rahmen ist beim Anlegen schon gelesen, der erste `A`-Rahmen auch --
    sonst wäre die Gesamtfrist nicht gehalten und `tts.py` wüsste die Rate
    nicht, mit der es `pw-cat` öffnen muss.
    """

    def __init__(self, sock: socket.socket, strom: _Strom, kopf: dict,
                 erster_block: bytes, correlation_id: str) -> None:
        self.sock = sock
        self._strom = strom
        self.kopf = kopf
        self.rate = int(kopf.get("sample_rate", 48000))
        self.erster_block = erster_block
        self.correlation_id = correlation_id
        self.offen = True
        self.grund = ""

    def bloecke(self):
        """Die restlichen Audioblöcke. Endet still, wenn die Frist reisst.

        Kein Werfen: der Aufrufer steht hier schon in der Wiedergabe, und ein
        halber Satz bleibt laut Schritt 15 halb. Der Grund landet in
        `self.grund` und von dort ins Journal.
        """
        try:
            while True:
                ende = time.monotonic() + RAHMENABSTAND_S
                art, nutzlast = self._rahmen(ende)
                if art == "A":
                    yield nutzlast
                elif art == "E":
                    ende_json = _json_oder_leer(nutzlast)
                    if ende_json.get("status") != "ok":
                        self.grund = str(ende_json.get("reason", "stream_fehler"))
                    return
                # H mitten im Strom: ignorieren, nicht abbrechen.
        except MimicFehler as exc:
            self.grund = exc.grund
        finally:
            self.schliessen()

    def _rahmen(self, ende: float) -> tuple[str, bytes]:
        return _rahmen_lesen(self._strom, ende)

    def schliessen(self) -> None:
        """`shutdown` vor `close` -- siehe Modulkopf."""
        self.offen = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _json_oder_leer(roh: bytes) -> dict:
    try:
        wert = json.loads(roh)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return wert if isinstance(wert, dict) else {}


def _rahmen_lesen(strom: _Strom, ende: float) -> tuple[str, bytes]:
    """Ein Rahmen aus dem **chunked** Körper.

    Die Chunk-Grenzen werden hier selbst aufgelöst: ein Rahmen kann über zwei
    Chunks liegen, und ein Chunk kann zwei Rahmen tragen.
    """
    kopf = _chunked_genau(strom, FRAME.size, ende)
    art, groesse = FRAME.unpack(kopf)
    if groesse > MAX_RAHMEN_BYTES:
        raise MimicFehler("protokoll", "Rahmen zu gross")
    if art not in (b"H", b"A", b"E"):
        raise MimicFehler("protokoll", "unbekannter Rahmentyp")
    return art.decode("ascii"), _chunked_genau(strom, groesse, ende)


def _chunked_genau(strom: _Strom, anzahl: int, ende: float) -> bytes:
    aus = b""
    while len(aus) < anzahl:
        if not strom.chunk_rest:
            groesse_zeile = strom.zeile(ende).split(b";")[0].strip()
            try:
                groesse = int(groesse_zeile, 16)
            except ValueError:
                raise MimicFehler("protokoll", "kaputte Chunk-Groesse") from None
            if groesse == 0:
                strom.zeile(ende)          # der abschliessende Leerzeile-Trailer
                raise MimicFehler("eof", "Koerper endete vor dem Rahmen")
            strom.chunk_rest = groesse
        nehmen = min(anzahl - len(aus), strom.chunk_rest)
        aus += strom.genau(nehmen, ende)
        strom.chunk_rest -= nehmen
        if strom.chunk_rest == 0:
            strom.genau(2, ende)           # CRLF hinter dem Chunk
    return aus


def _kopf_lesen(strom: _Strom, ende: float) -> tuple[int, dict]:
    zeile = strom.zeile(ende).decode("latin1")
    teile = zeile.split()
    if len(teile) < 2 or not teile[1].isdigit():
        raise MimicFehler("protokoll", f"kaputte Statuszeile: {zeile[:60]!r}")
    status = int(teile[1])
    kopfzeilen: dict[str, str] = {}
    while True:
        z = strom.zeile(ende)
        if not z:
            break
        name, _, wert = z.decode("latin1").partition(":")
        kopfzeilen[name.strip().lower()] = wert.strip()
    return status, kopfzeilen


def _anfrage(sock: socket.socket, pfad: str, koerper: dict) -> None:
    roh = json.dumps(koerper, ensure_ascii=False, separators=(",", ":")).encode()
    sock.sendall(
        f"POST {pfad} HTTP/1.1\r\nHost: mimic\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(roh)}\r\n"
        f"Connection: close\r\n\r\n".encode() + roh)


def _verbinden(pfad: str, ende: float) -> socket.socket:
    rest = ende - time.monotonic()
    if rest <= 0:
        raise MimicFehler("frist", "Frist schon vor dem Verbinden gerissen")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(rest)
    try:
        sock.connect(pfad)
    except (socket.timeout, OSError) as exc:
        sock.close()
        grund = "frist" if isinstance(exc, socket.timeout) else "nicht_erreichbar"
        raise MimicFehler(grund, str(exc)[:120]) from exc
    return sock


def _koerper_json(strom: _Strom, kopfzeilen: dict, ende: float) -> dict:
    try:
        laenge = int(kopfzeilen.get("content-length", "0"))
    except ValueError:
        return {}
    return _json_oder_leer(strom.genau(laenge, ende)) if laenge > 0 else {}


def sprechen(socket_pfad: str, text: str, *, stimme: str = "matthias",
             nur_warm: bool = True, correlation_id: str = "",
             frist_s: float = GESAMTFRIST_S, mode: str = "mf") -> Sitzung:
    """Bis zum ersten `A`-Rahmen. Wirft `MimicFehler`, wenn er nicht kommt.

    `aussprache` bleibt Mimics Vorgabe: die Ersetzungstabelle gehört dorthin,
    wo der Text gesprochen wird, nicht in den Client.
    """
    if not socket_pfad:
        raise MimicFehler("aus", "kein Mimic-Socket konfiguriert")
    ende = time.monotonic() + frist_s
    cid = correlation_id or korrelations_id()
    sock = _verbinden(socket_pfad, ende)
    strom = _Strom(sock)
    try:
        _anfrage(sock, "/speak", {"text": text, "voice": stimme, "mode": mode,
                                  "require_warm": bool(nur_warm),
                                  "correlation_id": cid})
        status, kopfzeilen = _kopf_lesen(strom, ende)
        if status != 200:
            fehler = _koerper_json(strom, kopfzeilen, ende)
            raise MimicFehler(str(fehler.get("reason", f"http_{status}")),
                              str(fehler.get("message", ""))[:200])
        art, nutzlast = _rahmen_lesen(strom, ende)
        if art != "H":
            raise MimicFehler("protokoll", f"erster Rahmen ist {art}, nicht H")
        kopf = _json_oder_leer(nutzlast)
        while True:
            art, nutzlast = _rahmen_lesen(strom, ende)
            if art == "A":
                break
            if art == "E":
                fehler = _json_oder_leer(nutzlast)
                raise MimicFehler(str(fehler.get("reason", "kein_audio")),
                                  str(fehler.get("message", ""))[:200])
    except BaseException:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        raise
    return Sitzung(sock, strom, kopf, nutzlast, cid)


def warmlauf(socket_pfad: str, *, correlation_id: str = "",
             frist_s: float = WARM_FRIST_S) -> str:
    """`POST /warm`, best effort. Rückgabe ist ein Grund, leer heisst gut.

    Wirft nie: Schritt 12a sagt, der Warmlauf verbessert die Zukunft und darf
    die Gegenwart nicht aufhalten -- also auch nicht durch eine Ausnahme, die
    jemand fangen muss.
    """
    if not socket_pfad:
        return "aus"
    ende = time.monotonic() + frist_s
    try:
        sock = _verbinden(socket_pfad, ende)
    except MimicFehler as exc:
        return exc.grund
    try:
        _anfrage(sock, "/warm", {"mode": "mf",
                                 "correlation_id": correlation_id or korrelations_id()})
        status, _ = _kopf_lesen(_Strom(sock), ende)
        return "" if status in (200, 202, 409) else f"http_{status}"
    except (MimicFehler, OSError):
        return "frist"
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


def status(socket_pfad: str, *, frist_s: float = STATUS_FRIST_S) -> dict:
    """`GET /status` mit harter Gesamtfrist. Leeres Dict heisst: nicht nutzbar.

    Die Frist ist der Punkt (Schritt 6): ein erreichbarer, aber hängender
    Mimic würde sonst den Start von `daimon-tts` blockieren -- und damit
    ausgerechnet den Rückfall.
    """
    if not socket_pfad:
        return {}
    ende = time.monotonic() + frist_s
    try:
        sock = _verbinden(socket_pfad, ende)
    except MimicFehler:
        return {}
    try:
        sock.sendall(b"GET /status HTTP/1.1\r\nHost: mimic\r\nConnection: close\r\n\r\n")
        strom = _Strom(sock)
        code, kopfzeilen = _kopf_lesen(strom, ende)
        return _koerper_json(strom, kopfzeilen, ende) if code == 200 else {}
    except (MimicFehler, OSError):
        return {}
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
