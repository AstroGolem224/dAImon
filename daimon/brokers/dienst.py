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

from daimon.common import ipc

# Groesser als jeder ehrliche Auftrag, klein genug, dass niemand hier
# Speicher fuellt.
MAX_BYTES = 64 * 1024
LESE_TIMEOUT_S = 10.0
# `aktion.sock`, nicht `ticket.sock`: dort liegen die Kontingente aus T-3.11
# (Egress), hier die Auftragstickets aus T-4.5. Zwei Buecher, zwei Sockets --
# und der Broker fragt das Buch, in dem sein Ticket steht.
HUB_SOCKET = "aktion.sock"
# Wer einen Auftrag einreichen darf. EINE Unit, fest.
HUB_UNIT = "daimon-hub.service"


def annehmen(srv: socket.socket, name: str, *, log=None):
    """Verbindung annehmen -- und zwar nur vom Hub.

    BEFUND T-4.5 K6 (Reviewer-Sitzung 18.08.): hier stand `srv.accept()`, und
    der Rumpf der Nachricht wurde gelesen, ohne dass jemand fragte, wer
    schreibt. Gemessen: `ipc.peer_of` 0x gerufen, Nutzlast 1x gelesen.

    Das wog schwerer als eine fehlende Zusatzpruefung, denn der Auftrag traegt
    BEWUSST keine Signatur: Design 6.2 hat den HMAC gestrichen, weil sein
    Schluessel unter derselben uid laege und damit genau den Angreifer nicht
    abwehrt, um den es geht. An seine Stelle tritt laut Design 7.4 die
    Herkunft ueber den Socket -- "Broker nehmen nur Verbindungen vom Hub an".
    Diese Zusage war die einzige ihrer Art, und sie fand nicht statt.

    Was sie leistet und was nicht: sie ist ein WEGWEISER, keine
    Authentifizierung (Design 1.3). Sie faengt einen falsch verdrahteten
    eigenen Dienst und macht im Nachhinein sichtbar, wer gefragt hat. Einen
    Angreifer, der bereits unter dieser uid Code ausfuehrt, haelt sie nicht
    auf -- der ersetzt die Unit oder liest den Hub direkt. Das Bedrohungs-
    modell wehrt ihn ausdruecklich nicht ab, und eine Zusage, die etwas
    anderes verspraeche, waere die schlechtere Antwort.

    Die Abweisung steht im Log, nicht nur im Nichts: eine Verbindung, die
    still verschwindet, ist bei der Fehlersuche nicht von einem toten Broker
    zu unterscheiden.
    """
    def notiz(was: str, peer) -> None:
        if log is None or was == "angenommen":
            return
        log.warn("Auftrag von fremdem Absender", DAIMON_SOCKET=name,
                 DAIMON_GRUND=was, DAIMON_PEER_UNIT=peer.unit,
                 DAIMON_PEER_PID=peer.pid)

    return ipc.accept(srv, name, erlaubte_units=(HUB_UNIT,), audit=notiz)


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
            try:
                conn, _ = annehmen(srv, pfad.name, log=log)
            except ipc.PeerError:
                continue          # abgewiesen, steht im Log -- weiterlaufen
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
