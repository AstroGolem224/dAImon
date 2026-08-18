"""T-3.11b — der CLI-Broker: das Abo statt des API-Keys.

Warum er NEBEN dem Egress steht und nicht darin
----------------------------------------------------------------------------
Der Egress transportiert einen Koerper, den er nicht liest -- das ist die
halbe Zusage von T-3.11. `claude -p` braucht den Prompt als TEXT; ein
CLI-Rueckgrat im Egress muesste den Koerper aufmachen, und die Zusage waere
nicht aufgeweicht, sondern weg.

Also ein eigener Prozess mit einer ehrlichen Aussage:

    DIESER PROZESS KENNT DEN INHALT.

Er protokolliert ihn trotzdem nicht -- dieselben vier Felder wie beim Egress
(`{ticket, bytes, status, dauer_ms}`). "Kennt" und "erzaehlt" sind zwei
verschiedene Dinge, und nur das zweite ist hier vermeidbar.

Dieselbe Drahtform wie der Egress
----------------------------------------------------------------------------
Anfrage und Antwort sehen aus wie beim Egress, und die Antwort traegt die
Messages-Gestalt (`content[0].text`), nicht die CLI-Gestalt (`result`). Damit
aendert sich am Mind KEINE Zeile: seine Unit zeigt auf einen anderen Socket.
Waere es andersherum, muesste Mind zwei Antwortformen kennen -- und die Wahl
des Rueckgrats waere bis dorthin sichtbar.

Was das kostet (09.08. gemessen, dieselbe Frage)
----------------------------------------------------------------------------
                     Vorgabeaufruf      schlank (wie hier)
    Wanduhr             6,1 s               2,8 s
    total_cost_usd      0,214               0,112
    cache_creation     18 802              17 676

Jeder Aufruf schickt ein Agentengeruest mit, auch schlank. Auf einem Abo
kostet das kein Bargeld, sondern Nutzungskontingent -- und 2,8 s Latenz
reissen die Zusage aus T-3.15 (p95 < 1500 ms vom Sprechen bis zum Ton) fuer
jede freie Frage. Lokale Absichten laufen nie hier durch und sind nicht
betroffen.

ponytail: kein Streaming. Obergrenze: die Antwort kommt am Stueck, TTFA ist
gleich der vollen Antwortzeit. Wer das aendert, nimmt
`--output-format stream-json` und muss dann Teilstuecke an den TTS reichen --
das ist ein eigener Task, kein Schalter.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from daimon.brokers.egress.broker import (MAX_ZEILE, antwortzeile,
                                          hub_anfrage, koerper_hash)
from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger

CLI_SOCKET = "cli.sock"
TICKET_SOCKET = "ticket.sock"
PROGRAMM = "claude"
MODELL = "sonnet"
TIMEOUT_S = 120.0
FENSTER_S = 60.0
HOECHSTENS = 10

GRUENDE = frozenset({
    "unlesbar", "unbekannte_art", "kein_ticket", "kein_koerper",
    "ticket_ungueltig", "kontingent_fenster", "ziel_weg",
    "cli_fehlt", "cli_fehler",
})


def _letzter_nutzertext(koerper: dict) -> str:
    """Die letzte Aeusserung des Nutzers -- die Frage, um die es geht.

    Der Verlauf davor faellt weg: `claude -p` bekommt eine Frage, keine
    Sitzung. Wer den Verlauf braucht, baut ihn in den Systemprompt oder nimmt
    den Egress -- ihn hier heimlich mitzuschicken waere ein Gedaechtnis, das
    niemand angemeldet hat.
    """
    for nachricht in reversed(koerper.get("messages") or []):
        if not isinstance(nachricht, dict) or nachricht.get("role") != "user":
            continue
        inhalt = nachricht.get("content")
        if isinstance(inhalt, str):
            return inhalt
        if isinstance(inhalt, list):
            return "".join(t.get("text", "") for t in inhalt
                           if isinstance(t, dict))
    return ""


def argumente(koerper: dict, *, programm: str = PROGRAMM,
              modell: str = MODELL) -> list[str]:
    """Die Kommandozeile. Rein und ohne Seiteneffekt.

    Absichtlich eine eigene Funktion: so kann der Pruefstand messen, WAS
    aufgerufen wuerde, ohne die CLI zu starten -- und "keine Werkzeuge" ist
    eine Messung statt einer Absicht.
    """
    argv = [programm, "-p", _letzter_nutzertext(koerper),
            "--output-format", "json",
            "--model", str(modell),
            # Leere Liste, nicht weggelassen: ohne den Schalter gilt die
            # Vorgabe der CLI, und die kennt Bash, Edit und Read. Ein
            # Sprach-Pet, dessen Modell Dateien lesen darf, ist kein Sprach-Pet.
            "--allowed-tools", ""]
    system = koerper.get("system")
    if isinstance(system, str) and system.strip():
        # T-3.10: die Persona kommt woertlich aus dem Koerper. Ohne diese
        # zwei Zeilen antwortete Claude Code als Claude Code, und die
        # Persona-Zusage waere auf diesem Weg still ausgehebelt.
        argv += ["--system-prompt", system]
    return argv


class CliBroker:
    def __init__(self, cfg: Config | None = None, *,
                 log: Logger | None = None, hub_socket: str = "",
                 programm: str = PROGRAMM, modell: str = MODELL,
                 timeout_s: float = TIMEOUT_S,
                 fenster_s: float = FENSTER_S, hoechstens: int = HOECHSTENS,
                 lauf: Callable[..., Any] = subprocess.run,
                 hub_anfrage: Callable[..., dict] = hub_anfrage) -> None:
        self.cfg = cfg
        self.log = log or get_logger("daimon-cli-broker")
        self.hub_socket = hub_socket
        self.programm = programm
        self.modell = modell
        self.timeout_s = float(timeout_s)
        self.fenster_s = float(fenster_s)
        self.hoechstens = int(hoechstens)
        self._lauf = lauf
        self._hub = hub_anfrage
        self._lock = threading.Lock()
        self._fenster: deque[float] = deque()
        self.anfragen = 0

    # -- Kontingentfenster (wie im Egress) ---------------------------------

    def _fenster_frei(self) -> tuple[bool, float]:
        jetzt = time.monotonic()
        with self._lock:
            while self._fenster and jetzt - self._fenster[0] > self.fenster_s:
                self._fenster.popleft()
            if len(self._fenster) < self.hoechstens:
                return True, 0.0
            return False, round(self.fenster_s - (jetzt - self._fenster[0]), 2)

    def _nein(self, grund: str, meldung: str, **extra: Any) -> dict:
        assert grund in GRUENDE, grund
        return {"v": 1, "ok": False, "grund": grund, "meldung": meldung, **extra}

    # -- Der Weg -----------------------------------------------------------

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

        # Reihenfolge wie im Egress: Fenster, dann Ticket, dann der teure Teil.
        # Ein Ticket, das an der Obergrenze verfaellt, waere ein bezahltes
        # Kontingent ohne Gegenwert.
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

        argv = argumente(koerper, programm=self.programm, modell=self.modell)
        t0 = time.monotonic()
        try:
            fertig = self._lauf(argv, capture_output=True, text=True,
                                timeout=self.timeout_s)
        except FileNotFoundError:
            return self._nein("cli_fehlt", f"{self.programm!r} nicht im PATH")
        except subprocess.TimeoutExpired:
            dauer_ms = round((time.monotonic() - t0) * 1000, 2)
            self.log.warn("CLI kam nicht zurueck", DAIMON_ACTION="cli_timeout",
                          DAIMON_TICKET=ticket[:12], DAIMON_DAUER_MS=dauer_ms)
            return self._nein("ziel_weg", f"Zeitlimit {self.timeout_s:.0f} s")
        except Exception as exc:      # noqa: BLE001
            # Breit wie im Egress, und aus demselben Grund: was ein
            # Unterprozess wirft, ist nicht Teil seiner Zusage. Die Meldung
            # nennt den TYP, nie den Text -- der koennte Inhalt tragen.
            return self._nein("ziel_weg", type(exc).__name__)
        dauer_ms = round((time.monotonic() - t0) * 1000, 2)

        if int(getattr(fertig, "returncode", 1)) != 0:
            return self._nein("cli_fehler",
                              f"Rueckgabewert {fertig.returncode}")
        try:
            roh = json.loads(getattr(fertig, "stdout", "") or "")
        except (json.JSONDecodeError, ValueError):
            return self._nein("cli_fehler", "Ausgabe ist kein JSON")
        if not isinstance(roh, dict) or roh.get("is_error"):
            # `is_error` deckt die Faelle ab, die die CLI selbst als Fehler
            # kennt -- Nutzungsgrenze, Anmeldung abgelaufen. Der Text kaeme
            # aus der Gegenseite und wird deshalb NICHT weitergereicht.
            return self._nein("cli_fehler", "CLI meldet is_error")
        text = roh.get("result")
        if not isinstance(text, str) or not text.strip():
            return self._nein("cli_fehler", "keine Antwort im Feld `result`")

        self.anfragen += 1
        antwort = {"content": [{"type": "text", "text": text}]}
        bytes_ = len(json.dumps(antwort, ensure_ascii=False).encode("utf-8"))
        self._audit(ticket, bytes_, 200, dauer_ms)
        return {"v": 1, "ok": True, "status": 200, "bytes": bytes_,
                "dauer_ms": dauer_ms, "antwort": antwort}

    def _audit(self, ticket: str, bytes_: int, status: int,
               dauer_ms: float) -> None:
        """Vier Felder, wie beim Egress. Dieser Prozess KENNT den Inhalt --
        gerade deshalb steht hier keiner."""
        self.log.info("cli", DAIMON_ACTION="cli_anfrage",
                      DAIMON_TICKET=ticket[:12], DAIMON_BYTES=bytes_,
                      DAIMON_STATUS=status, DAIMON_DAUER_MS=dauer_ms)

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "programm": self.programm,
                "modell": self.modell,
                "vorhanden": shutil.which(self.programm) is not None,
                "anfragen": self.anfragen, "fenster_s": self.fenster_s,
                "hoechstens": self.hoechstens}


def bediene(broker: CliBroker, conn: socket.socket) -> None:
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

    ap = argparse.ArgumentParser(description="dAImon CLI-Broker (T-3.11b)")
    ap.add_argument("--socket", default=None)
    ap.add_argument("--hub-socket", default=None)
    args = ap.parse_args(argv)

    cfg = load_config(make_dirs=False)
    pfad = Path(args.socket or cfg.runtime_dir / CLI_SOCKET)
    broker = CliBroker(
        cfg, hub_socket=args.hub_socket or str(cfg.runtime_dir / TICKET_SOCKET),
        programm=str(cfg.get("cli.programm", PROGRAMM)),
        modell=str(cfg.get("cli.modell", MODELL)),
        timeout_s=float(cfg.get("cli.timeout_s", TIMEOUT_S)),
        fenster_s=float(cfg.get("cli.fenster_s", FENSTER_S)),
        hoechstens=int(cfg.get("cli.hoechstens", HOECHSTENS)))

    pfad.parent.mkdir(parents=True, exist_ok=True)
    if pfad.exists():
        pfad.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    os.chmod(pfad, 0o600)
    srv.listen(8)
    broker.log.info("CLI-Broker laeuft", DAIMON_ACTION="start",
                    DAIMON_PROGRAMM=broker.programm,
                    DAIMON_MODELL=broker.modell)
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
