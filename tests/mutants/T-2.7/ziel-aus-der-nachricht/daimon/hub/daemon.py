"""T-0.9 — der Hub. Nimmt Ereignisse, liefert State. Ausschliesslich ueber
Unix-Sockets.

KEIN TCP. Nicht als Vorsichtsmassnahme, sondern damit
`RestrictAddressFamilies=AF_UNIX` in der systemd-Unit erfuellbar ist (T-0.14).
Ein einziger `socket.AF_INET` irgendwo macht diese Direktive unmoeglich, und
dann faellt eine ganze Schutzschicht weg, weil jemand einen Debug-Endpunkt
bequem fand. Der Verifizierer prueft es deshalb mit `ss -ltn` am laufenden
Prozess und nicht am Quelltext.

Zwei Arten von Socket:

  * je Produzent einer (`hookbridge.sock`, `eyes.sock`, ...), Zeilen-JSON rein
  * `state.sock` und `diag.sock`, beide nur lesend

Die Trennung ist nicht Kosmetik: die Produzentensockets pruefen die
Gegenstelle ueber T-0.7, die beiden lesenden nicht. Und die Diagnose bleibt
aus demselben Grund auf einem Unix-Socket wie alles andere -- sie verraet
Warteschlangenlaengen und Ereigniszaehler, also mehr ueber den Nutzer als in
einen Netzwerkendpunkt gehoert.

Der `auth`-Socket (T-1.7) ist der einzige Produzent, dessen Ereignisse nicht
auf den Bus gehen: `intent_mark` und `freigabe` werden hier im Hub gegen
MarkenBuch und FreigabeBuch verarbeitet -- die Marke bleibt im Hub (Design
2.4), und die turn_id erzeugt der Hub selbst, nie der Absender.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import socket
import subprocess
import time
import threading
from pathlib import Path

from daimon.auth.preview import wert_saeubern
from daimon.common import ipc
from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.common.protocol import Event, ProtocolError
from daimon.hub.bus import Bus, mood_of, projekt_aus_cwd
from daimon.hub.diag import Diagnose
from daimon.hub.marks import FreigabeBuch, MarkenBuch, MarkenFehler
from daimon.hub.state import HubState

STATE_SOCKET = "state.sock"
DIAG_SOCKET = "diag.sock"
EVENTS_SOCKET = "events.sock"
MAX_ZEILE = 1 << 20  # 1 MiB. Eine Hook-Nutzlast ist Kilobytes gross.

# T-2.7: was das Face abschalten darf. Der Schluessel kommt aus der
# Nachricht, der UNIT-NAME aus dieser Tabelle -- nie umgekehrt.
WAHRNEHMUNG_UNITS = {
    "ears": "daimon-ears.service",
    "eyes": "daimon-eyes.service",
}

# Wie oft der Push-Endpunkt nachsieht, ob sich `rev` bewegt hat. 50 ms deckelt
# die Zustellverzoegerung; T-1.6 verlangt p95 < 300 ms, das ist reichlich Luft.
PUSH_INTERVALL_S = 0.05
PR_SET_DUMPABLE = 4


def _dumpbarkeit_abschalten() -> None:
    """Design 7.5: keine ptrace-/Core-Dump-Freigabe fuer den Hub.

    Das ist nur eine Haertungsgeste gegen versehentliche Diagnosezugriffe,
    keine Grenze gegen einen bereits kompromittierten Benutzerprozess.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        fehler = ctypes.get_errno()
        raise OSError(fehler, os.strerror(fehler))


class Hub:
    def __init__(self, cfg: Config | None = None, *, log: Logger | None = None,
                 runtime_dir: Path | None = None) -> None:
        self.cfg = cfg or load_config()
        self.runtime_dir = runtime_dir or self.cfg.runtime_dir
        self.log = log or get_logger("daimon-hub")
        self.state = HubState(ttl_s=float(self.cfg.get("hub.state_ttl_s", 3600)))
        self.diag = Diagnose()
        self.bus = Bus()
        # T-1.7: Marken- und Freigabebuch leben im Hub (Design 2.4: "Die
        # Marke bleibt im Hub"). Der Auth-Agent meldet nur; ausgegeben und
        # bestaetigt wird hier.
        self.marken = MarkenBuch(log=self.log)
        self.freigaben = FreigabeBuch(log=self.log)
        self._server: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.bus.subscribe(self._on_event)

    # -- Ereignisse --------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        if event.type != "hook":
            self.diag.verworfen("fremder_typ")
            return
        t0 = time.perf_counter()
        p = event.payload or {}
        mood, bubble = mood_of(p)
        if bubble is not None:
            # Hook-Text wird genau an der Hub-Grenze fuer alle Anzeigen
            # gesaeubert. Das Face rendert nur diesen Zustand und baut die
            # Unicode-/Laengenregeln aus preview.py absichtlich nicht nach.
            bubble = {
                **bubble,
                "title": wert_saeubern(str(bubble.get("title", ""))),
                "body": wert_saeubern(str(bubble.get("body", ""))),
            }
        cwd = p.get("cwd", "") or ""
        pid = p.get("pid")
        self.state.apply(
            mood,
            session_id=p.get("session_id", "?"),
            bubble=bubble,
            cwd=cwd,
            project=projekt_aus_cwd(cwd),
            pid=int(pid) if isinstance(pid, int) else None,
            nonce=p.get("nonce", "") or "",
        )
        if mood is None:
            # Unbekanntes Ereignis. Es aendert nichts, aber es ist ein Befund:
            # T-1.5 hat SubagentStop und Notification:auth_success genau so
            # gefunden. Ohne Zaehler bliebe die Luecke unsichtbar.
            self.diag.verworfen(f"unbekanntes_ereignis:{p.get('hook_event_name','?')}")
        self.diag.hop("hook_to_state", (time.perf_counter() - t0) * 1000)

    # -- Produzentensockets ------------------------------------------------

    def _bediene_produzent(self, conn: socket.socket, produzent: str) -> None:
        with conn, conn.makefile("rb") as fh:
            for roh in fh:
                if len(roh) > MAX_ZEILE:
                    self.log.warn("Zeile zu lang, Verbindung ab",
                                  DAIMON_PRODUZENT=produzent)
                    return
                roh = roh.strip()
                if not roh:
                    continue
                try:
                    daten = json.loads(roh)
                    event = Event.from_dict(daten)
                    ipc.pruefe_typ(produzent, event.type)
                except (json.JSONDecodeError, ProtocolError) as exc:
                    self.log.warn("Nutzlast verworfen", DAIMON_PRODUZENT=produzent,
                                  DAIMON_GRUND=str(exc)[:200])
                    continue
                except ipc.MessageTypeError as exc:
                    # Das ist der Fall, um den es in T-0.7 geht: ein Produzent
                    # in fremder Rolle. Verbindung ab, nicht bloss verwerfen.
                    self.log.error("Typ nicht erlaubt, Verbindung ab",
                                   DAIMON_PRODUZENT=produzent,
                                   DAIMON_GRUND=str(exc)[:200])
                    self._zaehle_abweisung(event.type)
                    return
                if produzent == "auth":
                    # Auth-Ereignisse gehen nicht auf den Bus: der Hub gibt
                    # Marken aus und bestaetigt Freigaben selbst (T-1.7).
                    if not self._verarbeite_auth(event):
                        return  # Abweisung: Verbindung ab, Hub laeuft weiter
                    continue
                if produzent == "face":
                    if event.type == "wahrnehmung_aus":
                        self._wahrnehmung_aus(event.payload or {})
                    else:
                        self.state.clear_bubble()
                    continue
                self.bus.publish(event)

    def _wahrnehmung_aus(self, payload: dict) -> None:
        """MUTANT: faellt auf den Namen aus der NACHRICHT zurueck."""
        ziel = payload.get("ziel")
        if not isinstance(ziel, str) or not ziel:
            return
        unit = WAHRNEHMUNG_UNITS.get(ziel, ziel)
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=20, check=False)

    def _zaehle_abweisung(self, typ: str) -> None:
        """Diagnose-Zaehler fuer abgewiesene Anfragen. Nur zaehlen -- keine
        turn_id, keine Nonce, kein Hash: die Diagnose ist kein Auskunftskanal
        ueber Geheimnisse."""
        if typ == "intent_mark":
            self.diag.zaehle("rundenmarke", "abgelehnt")
        elif typ == "freigabe":
            self.diag.zaehle("aktionsfreigabe", "abgelehnt")

    def _verarbeite_auth(self, event: Event) -> bool:
        """`intent_mark` und `freigabe` vom auth-Socket. True = angenommen,
        False = abgewiesen (die Verbindung wird geschlossen, nicht der Hub).

        `ipc.pruefe_typ` hat den Typ bereits gegen PRODUZENTEN gewehrt; hier
        koennen nur noch die beiden auth-Typen ankommen.
        """
        p = event.payload or {}
        try:
            if event.type == "intent_mark":
                # Die turn_id erzeugt der HUB, nicht der Absender (Design
                # 2.4: "Die Marke bleibt im Hub"). Eine mitgeschickte
                # turn_id im Payload wird nicht gelesen.
                self.marken.ausgeben(quelle="auth",
                                     turn_id=secrets.token_hex(16))
                self.diag.zaehle("rundenmarke", "ausgegeben")
            else:  # "freigabe"
                # Nonce und Hash kommen aus der Nachricht, alles andere
                # nicht.
                self.freigaben.bestaetigen(
                    nonce=p.get("nonce", ""),
                    action_hash=p.get("action_hash", ""))
                self.diag.zaehle("aktionsfreigabe", "ausgegeben")
            return True
        except MarkenFehler as exc:
            self.log.warn("Auth-Anfrage abgewiesen, Verbindung ab",
                          DAIMON_TYP=event.type, DAIMON_GRUND=str(exc)[:200])
            self._zaehle_abweisung(event.type)
            return False

    def _horche_produzent(self, produzent: str) -> None:
        srv = ipc.listen(self.runtime_dir, produzent)
        self._server.append(srv)
        srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, peer = ipc.accept(
                    srv, produzent,
                    audit=lambda was, p: self.log.info(
                        "ipc", DAIMON_ACTION=was, DAIMON_PRODUZENT=produzent,
                        DAIMON_PEER_PID=p.pid, DAIMON_PEER_UNIT=p.unit),
                )
            except socket.timeout:
                continue
            except (ipc.PeerError, OSError):
                continue
            t = threading.Thread(target=self._bediene_produzent,
                                 args=(conn, produzent), daemon=True)
            t.start()
            self._threads.append(t)

    # -- State-Socket ------------------------------------------------------

    def _horche_einfach(self, dateiname: str, liefere) -> None:
        """Ein Socket, eine Zeile JSON, fertig. Fuer State und Diagnose --
        beide sind lesend und brauchen kein Protokoll."""
        pfad = self.runtime_dir / dateiname
        if pfad.exists():
            pfad.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
        os.chmod(pfad, 0o600)
        srv.listen(8)
        srv.settimeout(0.5)
        self._server.append(srv)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.sendall(json.dumps(liefere()).encode() + b"\n")
                except OSError:
                    pass

    # -- Push-Socket -------------------------------------------------------

    def _horche_push(self) -> None:
        """`events.sock`: beim Verbinden sofort ein Snapshot, danach je einer
        pro `rev`-Aenderung. Der Endpunkt liest nichts vom Client.

        Warum Push und nicht der vorhandene lesende `state.sock`: das Face
        haengt damit an einem Deskriptor und braucht **keinen Timer**. Die
        Null-Idle-CPU aus T-1.5 (gemessen: 0,000 % ueber 60 s) war die Zusage,
        auf der die ganze Overlay-Architektur steht -- ein Poll-Timer im Face
        haette sie wieder aufgemacht. Nachgesehen wird stattdessen hier, in
        einem Daemon, der ohnehin Threads haelt.
        """
        pfad = self.runtime_dir / EVENTS_SOCKET
        if pfad.exists():
            pfad.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
        os.chmod(pfad, 0o600)
        srv.listen(8)
        srv.settimeout(0.5)
        self._server.append(srv)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._push_schleife, args=(conn,),
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def _push_schleife(self, conn: socket.socket) -> None:
        letzte_rev = None
        with conn:
            while not self._stop.is_set():
                schnapp = self.state.snapshot()
                if schnapp["rev"] != letzte_rev:
                    try:
                        conn.sendall(json.dumps(schnapp).encode() + b"\n")
                    except OSError:
                        # Client weg. Kein Log: ein Face, das neu startet,
                        # darf keine Zeile im Journal kosten.
                        return
                    letzte_rev = schnapp["rev"]
                self._stop.wait(PUSH_INTERVALL_S)

    # -- Leben --------------------------------------------------------------

    def start(self, produzenten: list[str] | None = None) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.runtime_dir, 0o700)
        for p in produzenten or ["hookbridge", "face", "auth"]:
            t = threading.Thread(target=self._horche_produzent, args=(p,), daemon=True)
            t.start()
            self._threads.append(t)
        for datei, liefere in ((STATE_SOCKET, self.state.snapshot),
                               (DIAG_SOCKET, self.diag.snapshot)):
            t = threading.Thread(target=self._horche_einfach,
                                 args=(datei, liefere), daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._horche_push, daemon=True)
        t.start()
        self._threads.append(t)
        self.log.info("Hub laeuft", DAIMON_ACTION="start",
                      DAIMON_RUNTIME=str(self.runtime_dir))

    def stop(self) -> None:
        self._stop.set()
        for s in self._server:
            try:
                s.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2.0)


def main() -> int:
    ap = argparse.ArgumentParser(description="dAImon-Hub")
    ap.add_argument("--runtime-dir", type=Path, default=None)
    args = ap.parse_args()

    _dumpbarkeit_abschalten()
    hub = Hub(runtime_dir=args.runtime_dir)
    hub.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
