"""Der Dienstmantel um einen Broker. Einmal, nicht viermal.

Warum das hier steht und nicht dreimal kopiert
----------------------------------------------------------------------------
Vier Broker, vier Sockets, vier Male dieselbe Schleife: annehmen, eine Zeile
lesen, pruefen, antworten. Drei Kopien haetten drei Stellen, an denen die
Groessengrenze oder das Timeout auseinanderlaufen -- und die Grenze ist kein
Detail, sondern die Zusage, dass hier niemand Speicher fuellt.

Was der Mantel NICHT tut
----------------------------------------------------------------------------
Er entscheidet nichts. Policy, Vorschau und Freigabe liegen im Hub; der
Mantel nimmt entgegen, reicht an den Broker weiter und schickt dessen
Antwort zurueck. Ein Broker, der bei unerreichbarem Hub selbst entscheidet,
waere die zweite Wahrheit, die dieses Projekt an keiner Stelle haben will.

Ein Auftrag je Verbindung. Kein Rahmenprotokoll, keine Warteschlange: die
Verbindung IST die Klammer.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Callable

# Groesser als jeder ehrliche Auftrag, klein genug, dass niemand hier
# Speicher fuellt.
MAX_BYTES = 64 * 1024
LESE_TIMEOUT_S = 10.0
HUB_SOCKET = "hub.sock"


def ticket_beim_hub_einloesen(hub_pfad: Path, ticket: str,
                              timeout_s: float = 5.0) -> None:
    """Wirft, wenn der Hub nicht einloest. Kein Rueckfall auf "dann eben ohne".

    Die Einmaligkeit ist eine Aussage ueber ALLE Broker zusammen; nur der Hub
    kann sie treffen.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(timeout_s)
        c.connect(str(hub_pfad))
        c.sendall(json.dumps(
            {"v": 1, "art": "ticket_einloesen", "ticket": ticket}).encode()
            + b"\n")
        antwort = c.recv(4096).decode("utf-8", "replace").strip()
    try:
        daten = json.loads(antwort)
    except ValueError as exc:
        raise RuntimeError(f"Hub-Antwort unlesbar: {antwort[:120]}") from exc
    if not daten.get("ok"):
        raise RuntimeError(str(daten.get("grund") or "Hub hat abgelehnt"))


def socket_anlegen(pfad: Path) -> socket.socket:
    """0600 schon bei der Erzeugung -- nicht erst per `chmod` danach."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    if pfad.exists():
        pfad.unlink()
    alt = os.umask(0o177)
    try:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(pfad))
    finally:
        os.umask(alt)
    srv.listen(8)
    return srv


def lauf(pfad: Path, verarbeite: Callable[[bytes], dict], *,
         einmal: bool = False, log=None) -> int:
    """Die Schleife. `einmal=True` beendet nach dem ersten Auftrag.

    Das ist die One-shot-Zusage des Input-Brokers (T-4.13) -- sie steht hier
    als Schalter, damit sie nicht in drei Mantelkopien verschieden
    ausgelegt wird.
    """
    srv = socket_anlegen(pfad)
    print(f"READY pid={os.getpid()} socket={pfad}")
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(LESE_TIMEOUT_S)
                stuecke, gesamt = [], 0
                while True:
                    try:
                        stueck = conn.recv(4096)
                    except OSError:
                        stueck = b""
                    if not stueck:
                        break
                    gesamt += len(stueck)
                    if gesamt > MAX_BYTES:
                        conn.sendall(b'{"ok":false,"grund":"zu_gross"}\n')
                        stuecke = []
                        break
                    stuecke.append(stueck)
                    if stueck.endswith(b"\n"):
                        break
                if stuecke:
                    try:
                        antwort = verarbeite(b"".join(stuecke).strip())
                    except Exception as fehler:
                        # Ein Broker, der an einer Zeile stirbt, ist ein
                        # Broker, den systemd gleich wieder startet -- und
                        # dann stirbt er an der naechsten.
                        antwort = {"ok": False, "grund": "fehler",
                                   "meldung": str(fehler)[:200]}
                    if log is not None:
                        log.info("Auftrag bearbeitet",
                                 DAIMON_OK=str(antwort.get("ok")),
                                 DAIMON_GRUND=str(antwort.get("grund"))[:60])
                    try:
                        conn.sendall(
                            json.dumps(antwort, ensure_ascii=False).encode()
                            + b"\n")
                    except OSError:
                        pass
            if einmal:
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        srv.close()
        pfad.unlink(missing_ok=True)
