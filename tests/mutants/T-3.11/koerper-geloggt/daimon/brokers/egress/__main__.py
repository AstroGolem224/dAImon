"""Lauffähiges Gut-Muster des Egress-Vertrags von T-3.11."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import ssl
import sys
import time
import tomllib
import urllib.parse
from pathlib import Path

MAX = 4 << 20
ECHTES_ZIEL = "https://api.anthropic.com/v1/messages"


def redigiere(text: str, token: str) -> str:
    return text.replace(token, "[REDACTED]") if token else text


def log(text: str, token: str) -> None:
    print(redigiere(text, token), flush=True)


def credential() -> str:
    verzeichnis = os.environ.get("CREDENTIALS_DIRECTORY")
    if not verzeichnis:
        return ""
    try:
        return (Path(verzeichnis) / "anthropic-token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def config() -> tuple[float, int]:
    basis = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    try:
        with (basis / "daimon/daimon.toml").open("rb") as fh:
            e = tomllib.load(fh).get("egress", {})
        return float(e.get("fenster_s", 60)), int(e.get("hoechstens", 10))
    except OSError:
        return 60.0, 10


def ziel() -> tuple[str, bool]:
    test = os.environ.get("DAIMON_EGRESS_TESTPROFIL") == "1"
    basis = os.environ.get("DAIMON_EGRESS_ZIEL") if test else None
    if not basis:
        return ECHTES_ZIEL, False
    return basis.rstrip("/") + "/v1/messages", True


def koerper_roh(zeile: bytes) -> bytes | None:
    """Rohen Objektwert hinter koerper ohne Neu-Serialisierung abtrennen."""
    m = __import__("re").search(rb'"koerper"\s*:\s*', zeile)
    if not m:
        return None
    start = m.end()
    try:
        text = zeile[start:].decode("utf-8")
        _, ende = json.JSONDecoder().raw_decode(text)
        roh = text[:ende].encode("utf-8")
        if not isinstance(json.loads(roh), dict):
            return None
        return roh
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def hub(obj: dict) -> dict:
    rt = Path(os.environ["XDG_RUNTIME_DIR"]) / "daimon/ticket.sock"
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(3)
    try:
        c.connect(str(rt))
        c.sendall(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
        return json.loads(c.makefile("rb").readline(MAX))
    finally:
        c.close()


def transport(url: str, body: bytes, token: str) -> tuple[int, bytes, float]:
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    port = u.port
    t0 = time.monotonic()
    if u.scheme == "https":
        verbindung = http.client.HTTPSConnection(host, port, timeout=5,
                                                  context=ssl.create_default_context())
    elif u.scheme == "http":
        verbindung = http.client.HTTPConnection(host, port, timeout=5)
    else:
        raise OSError("unbekanntes Zielschema")
    try:
        verbindung.request("POST", u.path or "/v1/messages", body=body, headers={
            "content-type": "application/json",
            "content-length": str(len(body)),
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        })
        r = verbindung.getresponse()
        antwort = r.read(MAX)
        return r.status, antwort, (time.monotonic() - t0) * 1000
    finally:
        verbindung.close()


class Dienst:
    def __init__(self) -> None:
        self.token = credential()
        self.ziel, self.testprofil = ziel()
        self.fenster_s, self.hoechstens = config()
        self.zeiten: list[float] = []
        self.anfragen = 0
        if self.testprofil:
            log(f"testziel={self.ziel}", self.token)

    def absage(self, grund: str, meldung: str, **extra) -> bytes:
        obj = {"v": 1, "ok": False, "grund": grund, "meldung": meldung, **extra}
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"

    def bearbeite(self, zeile: bytes) -> bytes:
        try:
            req = json.loads(zeile)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.absage("unlesbar", "keine lesbare JSON-Zeile")
        if not isinstance(req, dict):
            return self.absage("unlesbar", "JSON-Wurzel ist kein Objekt")
        art = req.get("art")
        if art == "zustand":
            jetzt = time.monotonic()
            self.zeiten = [t for t in self.zeiten if jetzt - t < self.fenster_s]
            return json.dumps({
                "v": 1, "ok": True, "ziel": self.ziel,
                "testprofil": self.testprofil, "token_vorhanden": bool(self.token),
                "anfragen": self.anfragen, "fenster_s": self.fenster_s,
                "hoechstens": self.hoechstens, "im_fenster": len(self.zeiten),
                "pid": os.getpid(),
            }, separators=(",", ":")).encode() + b"\n"
        if art != "anfrage":
            return self.absage("unbekannte_art", "art ist weder anfrage noch zustand")
        tid = req.get("ticket")
        if not isinstance(tid, str) or not tid:
            return self.absage("kein_ticket", "ticket fehlt oder ist leer")
        body = koerper_roh(zeile)
        if body is None:
            return self.absage("kein_koerper", "koerper fehlt oder ist kein Objekt")
        h = hashlib.sha256(json.dumps(
            json.loads(body), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")).hexdigest()
        try:
            eingel = hub({"v": 1, "art": "einloesen", "ticket": tid,
                          "auftrag_hash": h})
        except OSError:
            eingel = {"ok": False}
        if not eingel.get("ok"):
            return self.absage("ticket_ungueltig", "Ticket unbekannt, abgelaufen oder verbraucht")
        jetzt = time.monotonic()
        self.zeiten = [t for t in self.zeiten if jetzt - t < self.fenster_s]
        if len(self.zeiten) >= self.hoechstens:
            rest = max(0.001, self.fenster_s - (jetzt - self.zeiten[0]))
            return self.absage("kontingent_fenster", "Obergrenze erreicht", rest_s=round(rest, 3))
        if not self.token:
            return self.absage("kein_token", "Credential fehlt")
        self.zeiten.append(jetzt)
        log(f"koerper={body.decode('utf-8')}", self.token)  # MUTATION
        try:
            status, antwort, dauer = transport(self.ziel, body, self.token)
        except Exception as exc:
            log(f"ziel_weg: {type(exc).__name__}: {exc}", self.token)
            return self.absage("ziel_weg", "Ziel oder TLS nicht erreichbar")
        self.anfragen += 1
        protokoll = {"ticket": tid, "bytes": len(antwort), "status": status,
                     "dauer_ms": round(dauer, 3)}
        log(json.dumps(protokoll, separators=(",", ":")), self.token)
        kopf = json.dumps({
            "v": 1, "ok": True, "status": status, "bytes": len(antwort),
            "dauer_ms": round(dauer, 3),
        }, separators=(",", ":"))[:-1].encode()
        return kopf + b',"antwort":' + antwort + b"}\n"


def socket_von_systemd() -> socket.socket:
    if int(os.environ.get("LISTEN_FDS", "0")) < 1:
        raise SystemExit("Egress muss socket-aktiviert laufen")
    return socket.socket(fileno=3)


def main() -> int:
    dienst = Dienst()
    horcher = socket_von_systemd()
    while True:
        c, _ = horcher.accept()
        with c:
            c.settimeout(5)
            zeile = c.makefile("rb").readline(MAX)
            c.sendall(dienst.bearbeite(zeile))


if __name__ == "__main__":
    raise SystemExit(main())
