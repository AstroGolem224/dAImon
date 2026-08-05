"""T-3.11 — Mind, und zwar nur die Haelfte, die dieser Task braucht.

Was hier steht und was NICHT
----------------------------------------------------------------------------
Dieser Dienst existiert in T-3.11 aus einem einzigen Grund: die Zusage "Mind hat
kein Netz und keinen Token" ist nur an einem LAUFENDEN Prozess messbar. Eine Unit
ohne ausfuehrbaren Inhalt waere eine Behauptung.

Er kann deshalb genau eine Sache: eine Frage nehmen, den Persona-Prompt davor
setzen, ein Kontingent beim Hub holen und den Koerper an den Egress geben. Was
er ausdruecklich NICHT kann:

  * **Kein Routing.** Kein Durchgang 1 und 2, keine Werkzeugwahl, keine
    Modellauswahl nach Aufgabe -- das ist T-3.12.
  * **Kein Kontext.** Keine Historie, kein Gedaechtnis, keine Sitzungsbindung --
    das ist T-3.12 und Phase 6.
  * **Keine Markierung.** Ob Material `tainted` oder `user_audio` ist, entscheidet
    T-3.13b. Dieser Dienst kennt den Unterschied nicht und behauptet ihn nicht.
  * **Kein Verbraucher fuer `speech_threshold`.** Auch hier nicht. Die Persona
    stellt sie bereit, niemand liest sie.

Wer das fuer wenig haelt: genau das ist der Punkt. Ein Mind, der in T-3.11 schon
routet, haette Routing ohne Pruefstand -- und der Pruefstand fuer Routing ist
T-3.12.v.

Warum Mind den Hash nicht bildet
----------------------------------------------------------------------------
Das Ticket ist an den Hash des Koerpers gebunden, und diesen Hash bildet der
EGRESS. Mind liefert ihn nicht mit. Der Unterschied ist die halbe Zusage: koennte
Mind den Hash bestimmen, koennte es ein Ticket fuer Koerper A besorgen und Koerper
B senden -- und der Egress haette keine Moeglichkeit, das zu merken. So muss Mind
den Koerper VOR der Ticketausgabe festlegen und kann ihn danach nicht mehr
tauschen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import threading
from typing import Any

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger
from daimon.mind.persona import Persona, PersonaFehler, lade as persona_laden

MIND_SOCKET = "mind.sock"
TICKET_SOCKET = "ticket.sock"
EGRESS_SOCKET = "egress.sock"
LISTEN_FDS_START = 3
MAX_ZEILE = 1 << 20

# Vorgaben fuer den Anfragekoerper. Modell und Obergrenze stehen in der
# Konfiguration, weil sie sich mit dem Angebot aendern -- die ZIELADRESSE
# dagegen steht im Egress und im Code, damit sie nicht einstellbar ist.
MODELL = "claude-sonnet-4-5"
MAX_TOKENS = 1024


def hub_anfrage(sock: str, anfrage: dict, *, timeout_s: float = 180.0) -> dict:
    """Eine Zeile hin, eine zurueck. Fuer Hub UND Egress -- dasselbe Muster."""
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout_s)
    try:
        c.connect(sock)
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        roh = c.makefile("rb").readline(MAX_ZEILE)
    except OSError as exc:
        return {"v": 1, "ok": False, "grund": "gegenstelle_weg",
                "meldung": str(exc)[:160]}
    finally:
        c.close()
    try:
        antwort = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return {"v": 1, "ok": False, "grund": "gegenstelle_weg",
                "meldung": "Antwort unlesbar"}
    return antwort if isinstance(antwort, dict) else {
        "v": 1, "ok": False, "grund": "gegenstelle_weg", "meldung": "kein Objekt"}


def koerper_hash(koerper: object) -> str:
    """Dieselbe Bildung wie im Egress -- Mind braucht sie nur, um das TICKET
    anzufordern, nicht um es einzuloesen. Weichen die beiden Hashes ab, weist
    der Egress ab, und genau das ist die Bindung."""
    roh = json.dumps(koerper, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(roh).hexdigest()


class Mind:
    """Frage rein, Antwort raus. Ohne Netz, ohne Token."""

    def __init__(self, *, hub_socket: str, egress_socket: str,
                 persona: Persona, modell: str = MODELL,
                 max_tokens: int = MAX_TOKENS,
                 log: Logger | None = None) -> None:
        self.hub_socket = hub_socket
        self.egress_socket = egress_socket
        self.persona = persona
        self.modell = modell
        self.max_tokens = int(max_tokens)
        self.log = log or get_logger("daimon-mind")
        self.anfragen = 0

    def koerper(self, frage: str) -> dict:
        """Der API-Koerper. Der Systemprompt kommt WOERTLICH aus der Persona."""
        return {
            "model": self.modell,
            "max_tokens": self.max_tokens,
            "system": self.persona.prompt(),
            "messages": [{"role": "user", "content": frage}],
        }

    def frage(self, frage: object) -> dict:
        if not isinstance(frage, str) or not frage.strip():
            return {"v": 1, "ok": False, "grund": "keine_frage",
                    "meldung": "Feld `text` fehlt oder ist leer"}

        koerper = self.koerper(frage)
        # Erst das Kontingent, dann der Egress. Umgekehrt waere der Aufruf
        # bezahlt, bevor er autorisiert ist.
        ticket = hub_anfrage(self.hub_socket, {
            "v": 1, "art": "ausgeben", "zweck": "api",
            "auftrag_hash": koerper_hash(koerper)})
        if not ticket.get("ok"):
            self.log.warn("Kein Kontingent", DAIMON_ACTION="mind_kein_ticket",
                          DAIMON_GRUND=str(ticket.get("grund", ""))[:60])
            return {"v": 1, "ok": False, "grund": "kein_kontingent",
                    "meldung": str(ticket.get("grund", ""))[:120]}

        antwort = hub_anfrage(self.egress_socket, {
            "v": 1, "art": "anfrage", "ticket": ticket["ticket"],
            "koerper": koerper})
        self.anfragen += 1
        if not antwort.get("ok"):
            # Der Grund des Egress wird DURCHGEREICHT und nicht umbenannt: ein
            # `kontingent_fenster` als "kein_kontingent" zu melden waere eine
            # Umdeutung, die die Diagnose kostet.
            self.log.warn("Egress hat abgelehnt", DAIMON_ACTION="mind_abgelehnt",
                          DAIMON_GRUND=str(antwort.get("grund", ""))[:60])
            return {"v": 1, "ok": False, "grund": str(antwort.get("grund", "")),
                    "meldung": str(antwort.get("meldung", ""))[:200]}

        self.log.info("Antwort erhalten", DAIMON_ACTION="mind_antwort",
                      DAIMON_STATUS=antwort.get("status"),
                      DAIMON_BYTES=antwort.get("bytes"))
        return {"v": 1, "ok": True, "status": antwort.get("status"),
                "antwort": antwort.get("antwort"),
                "dauer_ms": antwort.get("dauer_ms")}

    def zustand(self) -> dict:
        return {"v": 1, "ok": True, "persona": self.persona.name,
                "modell": self.modell, "max_tokens": self.max_tokens,
                "prompt_zeichen": len(self.persona.prompt()),
                "speech_threshold": self.persona.speech_threshold,
                # Ausdruecklich: dieser Dienst hat keinen Token und will keinen.
                "token_vorhanden": False,
                "anfragen": self.anfragen, "pid": os.getpid()}


# -- Sockets ---------------------------------------------------------------

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


def bediene(mind: Mind, conn: socket.socket) -> None:
    with conn:
        conn.settimeout(200.0)
        try:
            anfrage = json.loads(conn.makefile("rb").readline(MAX_ZEILE))
        except (OSError, json.JSONDecodeError, ValueError):
            anfrage = None
        if not isinstance(anfrage, dict):
            antwort: dict[str, Any] = {"v": 1, "ok": False, "grund": "unlesbar",
                                       "meldung": "keine lesbare JSON-Zeile"}
        elif anfrage.get("art") == "zustand":
            antwort = mind.zustand()
        elif anfrage.get("art") == "frage":
            antwort = mind.frage(anfrage.get("text"))
        else:
            antwort = {"v": 1, "ok": False, "grund": "unbekannte_art",
                       "meldung": f"art={str(anfrage.get('art'))[:40]!r}"}
        try:
            conn.sendall(json.dumps(antwort).encode() + b"\n")
        except OSError:
            pass


def lauf(mind: Mind, srv: socket.socket) -> int:
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=bediene, args=(mind, conn), daemon=True).start()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Mind (T-3.11, Teilstueck)")
    ap.add_argument("--socket", default=None)
    ap.add_argument("--hub-socket", default=None)
    ap.add_argument("--egress-socket", default=None)
    ap.add_argument("--frage", default=None,
                    help="einmal fragen und beenden (Handlauf)")
    args = ap.parse_args(argv)

    cfg = load_config(make_dirs=False)
    try:
        persona = persona_laden(cfg)
    except PersonaFehler as exc:
        # Kein Vorgabe-Charakter: T-3.10 hat entschieden, dass eine fehlende
        # Persona ein Fehler ist, und dieser Dienst darf sie nicht umgehen.
        raise SystemExit(f"Persona nicht ladbar: {exc}")

    mind = Mind(
        hub_socket=args.hub_socket or str(cfg.runtime_dir / TICKET_SOCKET),
        egress_socket=args.egress_socket or str(cfg.runtime_dir / EGRESS_SOCKET),
        persona=persona,
        modell=str(cfg.get("mind.modell", MODELL)),
        max_tokens=int(cfg.get("mind.max_tokens", MAX_TOKENS)))

    if args.frage is not None:
        print(json.dumps(mind.frage(args.frage), ensure_ascii=False)[:4000])
        return 0

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
        pfad = args.socket or str(cfg.runtime_dir / MIND_SOCKET)
        srv = eigener_socket(pfad)
    try:
        return lauf(mind, srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
