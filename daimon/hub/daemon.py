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
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import threading
from pathlib import Path

from daimon.common import ipc
from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.common.protocol import Event, ProtocolError
from daimon.hub.bus import Bus, mood_of, projekt_aus_cwd
from daimon.hub.diag import Diagnose
from daimon.hub.state import HubState

STATE_SOCKET = "state.sock"
DIAG_SOCKET = "diag.sock"
MAX_ZEILE = 1 << 20  # 1 MiB. Eine Hook-Nutzlast ist Kilobytes gross.


class Hub:
    def __init__(self, cfg: Config | None = None, *, log: Logger | None = None,
                 runtime_dir: Path | None = None) -> None:
        self.cfg = cfg or load_config()
        self.runtime_dir = runtime_dir or self.cfg.runtime_dir
        self.log = log or get_logger("daimon-hub")
        self.state = HubState(ttl_s=float(self.cfg.get("hub.state_ttl_s", 3600)))
        self.diag = Diagnose()
        self.bus = Bus()
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
                    return
                self.bus.publish(event)

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

    # -- Leben --------------------------------------------------------------

    def start(self, produzenten: list[str] | None = None) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.runtime_dir, 0o700)
        for p in produzenten or ["hookbridge"]:
            t = threading.Thread(target=self._horche_produzent, args=(p,), daemon=True)
            t.start()
            self._threads.append(t)
        for datei, liefere in ((STATE_SOCKET, self.state.snapshot),
                               (DIAG_SOCKET, self.diag.snapshot)):
            t = threading.Thread(target=self._horche_einfach,
                                 args=(datei, liefere), daemon=True)
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
