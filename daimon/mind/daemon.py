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
from daimon.hub import sprechtext
from daimon.mind.persona import Persona, PersonaFehler, lade as persona_laden
from daimon.mind.answer import Durchgang2
from daimon.mind.router import Router, quellen_aus_umgebung

MIND_SOCKET = "mind.sock"
TICKET_SOCKET = "ticket.sock"
EGRESS_SOCKET = "egress.sock"
STATE_SOCKET = "state.sock"
# T-4.16 K1: der einzige Weg zu einer Aktion. `daimon-mind.service` steht seit
# diesem Zulauf in AKTION_UNITS (daimon/hub/daemon.py) -- vorher zu Recht
# nicht, siehe der Kommentar dort ("wer den Zulauf baut, traegt ihn ein").
AKTION_SOCKET = "aktion.sock"
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


def _tool_schema(eintrag: dict) -> dict:
    """Ein Katalogeintrag-`params` als Anthropic `input_schema`.

    Dieselben zwei Typen wie in der Policy (`daimon/hub/policy.py:
    _params_pruefen`) -- absichtlich dieselben, sonst waere das Schema, das
    das Modell sieht, eine zweite Fassung der Schranke, die die Policy
    tatsaechlich durchsetzt.
    """
    eigenschaften: dict = {}
    pflicht: list = []
    for name, regel in (eintrag.get("params") or {}).items():
        art = regel.get("type")
        if art in ("float", "int"):
            js: dict = {"type": "number" if art == "float" else "integer"}
            schranke = regel.get("value_between")
            if schranke:
                js["minimum"], js["maximum"] = schranke
        elif art == "string":
            js = {"type": "string"}
            if regel.get("pattern"):
                js["pattern"] = regel["pattern"]
        else:
            js = {}
        eigenschaften[name] = js
        if regel.get("required"):
            pflicht.append(name)
    schema = {"type": "object", "properties": eigenschaften}
    if pflicht:
        schema["required"] = pflicht
    return schema


def werkzeuge_aus_katalog(katalog: dict) -> tuple[list[dict], dict[str, str]]:
    """Der freigegebene Katalog als Anthropic-Tool-Liste.

    Anthropic-Werkzeugnamen erlauben keinen Punkt (`^[a-zA-Z0-9_-]+$`), eine
    `action_id` wie `media.playpause` aber schon -- deshalb die Abbildung
    zurueck, statt den Punkt fortzulassen und zwei IDs kollidieren zu lassen.
    """
    werkzeuge: list[dict] = []
    namen: dict[str, str] = {}
    for action_id, eintrag in sorted(katalog.items()):
        name = action_id.replace(".", "_")
        namen[name] = action_id
        werkzeuge.append({
            "name": name,
            "description": (eintrag.get("rationale") or action_id).strip()[:1024],
            "input_schema": _tool_schema(eintrag),
        })
    return werkzeuge, namen


# Die Ausgabeform. Sie steht in der ANFRAGE, nicht in einer Filterstufe
# danach -- am 09.08. live gelernt: Nordom antwortete zweizeilig, der
# Validator aus T-3.9 wies `mehrzeilig` zurueck, und das Pet sagte den
# Ersatzsatz ("die Antwort steht auf dem Bildschirm"). Der Validator hat dabei
# genau das getan, wofuer er da ist. Falsch war die Stelle davor: dem Modell
# hatte niemand gesagt, dass seine Antwort GESPROCHEN wird.
#
# Die Zahl haengt am Validator und steht nicht daneben -- zwei Zahlen an zwei
# Orten laufen auseinander, und dann weist der eine ab, was der andere erlaubt
# hat. Sie liegt aber BEWUSST darunter: am 09.08. an gemma4:26b gemessen,
# Vorgabe 140, geliefert 146. Ein Modell zielt auf die genannte Zahl, es trifft
# sie nicht -- und ein Treffer knapp darueber kostet die ganze Antwort.
#
# ponytail: 20 Zeichen Rand, geraten und einmal nachgemessen. Obergrenze: wer
# ein Modell einsetzt, das enger trifft, dreht den Rand herunter; wer eins
# einsetzt, das weiter streut, hoch. Ein Kalibrierknopf, kein Naturgesetz.
SPRECHFORM_RAND = 20
SPRECHFORM_ZEICHEN = sprechtext.MAX_ZEICHEN - SPRECHFORM_RAND
SPRECHFORM = (
    "[Ausgabeform]\n"
    f"Diese Antwort wird VORGELESEN. Antworte in genau EINER Zeile mit "
    f"hoechstens {SPRECHFORM_ZEICHEN} Zeichen. Keine Zeilenumbrueche, keine "
    "Aufzaehlung, kein Code, keine URLs, keine Dateipfade -- all das wird "
    "sonst abgewiesen und gar nicht gesprochen."
)


class Mind:
    """Frage rein, Antwort raus. Ohne Netz, ohne Token."""

    def __init__(self, *, hub_socket: str, egress_socket: str,
                 persona: Persona, modell: str = MODELL,
                 max_tokens: int = MAX_TOKENS,
                 aktion_socket: str | None = None,
                 werkzeuge: list[dict] | None = None,
                 werkzeug_namen: dict[str, str] | None = None,
                 log: Logger | None = None) -> None:
        self.hub_socket = hub_socket
        self.egress_socket = egress_socket
        self.persona = persona
        self.modell = modell
        self.max_tokens = int(max_tokens)
        self.log = log or get_logger("daimon-mind")
        self.anfragen = 0
        # T-4.16 K1: Durchgang 1, werkzeugfaehig. `aktion_socket` bleibt
        # `None` erlaubt (Handlauf/Pruefstaende ohne Hub) -- `frage_werkzeug`
        # weist dann klar ab statt am Verbindungsversuch zu haengen.
        self.aktion_socket = aktion_socket
        self._werkzeuge = werkzeuge or []
        self._werkzeug_namen = werkzeug_namen or {}

    def koerper(self, frage: str, kontext: dict | None = None,
                werkzeuge: list[dict] | None = None) -> dict:
        """Der API-Koerper. Der Systemprompt kommt WOERTLICH aus der Persona.

        `kontext` traegt in T-3.12 ausschliesslich **opake Referenzen**
        (`{"fenster": [{"ref": "w_1", "app_id": "..."}]}`) -- nie einen
        Fenstertitel, nie Bildschirmtext. Er wird als eigener, klar abgesetzter
        Block angehaengt und nicht in den Nutzertext gemischt: was das Modell
        als Anweisung lesen koennte, soll wenigstens sichtbar getrennt sein.

        `werkzeuge` (T-4.16 K1) und `kontext` schliessen sich in der Praxis
        aus: Durchgang 1 (werkzeugfaehig) sieht keinen markierten Inhalt,
        Durchgang 2 (kontextfaehig) hat kein Schema fuer ein Werkzeug. Diese
        Methode erzwingt das nicht -- der Aufrufer traegt die Zusage, wie
        `daimon/mind/answer.py`'s Modulkopf es beschreibt.
        """
        inhalt = frage
        if kontext:
            inhalt = (f"{frage}\n\n[Referenzen, keine Inhalte]\n"
                      f"{json.dumps(kontext, ensure_ascii=False, sort_keys=True)}")
        # Als eigener, abgesetzter Block -- dasselbe Muster wie die Referenzen
        # darueber. Der Systemprompt bleibt unberuehrt: T-3.10 gibt die Persona
        # WOERTLICH weiter, und eine Ausgabeform ist keine Persona.
        inhalt = f"{inhalt}\n\n{SPRECHFORM}"
        koerper: dict = {
            "model": self.modell,
            "max_tokens": self.max_tokens,
            "system": self.persona.prompt(),
            "messages": [{"role": "user", "content": inhalt}],
        }
        if werkzeuge:
            koerper["tools"] = werkzeuge
        return koerper

    def frage_api(self, frage: str, kontext: dict | None = None) -> dict:
        """Der Weg nach draussen, wie ihn der Router benutzt."""
        return self.frage(frage, kontext)

    def frage_werkzeug(self, frage: str) -> dict:
        """T-4.16 K1, Durchgang 1: werkzeugfaehig, KEIN Kontext.

        Erkennt der Antwortblock `tool_use`, wird die Aktion ueber
        `aktion.sock` an den Koordinator gereicht -- `quelle="modell"` setzt
        der Hub selbst (daemon.py:1049), diese Methode kann sie nicht
        beeinflussen. Ein erfundener oder unbekannter Werkzeugname fuehrt zu
        `tool_erkannt=False`, nicht zu einem Rateversuch.
        """
        if not isinstance(frage, str) or not frage.strip():
            return {"v": 1, "ok": False, "grund": "keine_frage",
                    "meldung": "Feld `text` fehlt oder ist leer"}
        if not self._werkzeuge or not self.aktion_socket:
            return {"v": 1, "ok": False, "grund": "kein_werkzeug",
                    "meldung": "kein Katalog geladen oder kein aktion.sock"}

        koerper = self.koerper(frage, werkzeuge=self._werkzeuge)
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
            self.log.warn("Egress hat abgelehnt",
                          DAIMON_ACTION="mind_werkzeug_abgelehnt",
                          DAIMON_GRUND=str(antwort.get("grund", ""))[:60])
            return {"v": 1, "ok": False, "grund": str(antwort.get("grund", "")),
                    "meldung": str(antwort.get("meldung", ""))[:200]}

        inhalt = (antwort.get("antwort") or {}).get("content")
        bloecke = inhalt if isinstance(inhalt, list) else []
        text = "\n".join(str(b.get("text", "")) for b in bloecke
                         if isinstance(b, dict) and b.get("type") == "text").strip()
        werkzeug = next((b for b in bloecke if isinstance(b, dict)
                         and b.get("type") == "tool_use"), None)
        if werkzeug is None:
            return {"v": 1, "ok": True, "tool_erkannt": False, "antwort": text}

        action_id = self._werkzeug_namen.get(str(werkzeug.get("name") or ""))
        if action_id is None:
            self.log.warn("Unbekanntes Werkzeug genannt",
                          DAIMON_ACTION="mind_werkzeug_unbekannt",
                          DAIMON_NAME=str(werkzeug.get("name"))[:60])
            return {"v": 1, "ok": True, "tool_erkannt": False,
                    "grund": "unbekanntes_werkzeug", "antwort": text}

        lauf = hub_anfrage(self.aktion_socket, {
            "v": 1, "art": "ausfuehren", "action_id": action_id,
            "params": werkzeug.get("input") or {},
            "tool_use_id": str(werkzeug.get("id") or "")})
        self.log.info("Werkzeug ausgefuehrt", DAIMON_ACTION="mind_werkzeug",
                      DAIMON_AKTION_ID=action_id[:60],
                      DAIMON_AUSGEFUEHRT=str(bool(lauf.get("ausgefuehrt"))))
        return {"v": 1, "ok": True, "tool_erkannt": True,
                "action_id": action_id,
                "ausgefuehrt": bool(lauf.get("ausgefuehrt")),
                "verdikt": lauf.get("verdikt"), "grund": lauf.get("grund"),
                "gesprochen": lauf.get("gesprochen"), "antwort": text}

    def frage(self, frage: object, kontext: dict | None = None) -> dict:
        if not isinstance(frage, str) or not frage.strip():
            return {"v": 1, "ok": False, "grund": "keine_frage",
                    "meldung": "Feld `text` fehlt oder ist leer"}

        koerper = self.koerper(frage, kontext)
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


def bediene_anfrage(mind: Mind, anfrage: object, *, router) -> dict:
    """Eine Anfrage, eine Antwort. Ohne Socket, damit sie pruefbar ist.

    `zustand` bleibt beim Mind -- er kennt Persona und Modell. Alles andere
    geht durch den ROUTER: seit T-3.12 entscheidet nicht mehr der Dienst, dass
    eine Frage an die API gehoert, sondern die Absicht. Vorher war jede Frage
    ein API-Aufruf, auch "wie spaet ist es".
    """
    if not isinstance(anfrage, dict):
        return {"v": 1, "ok": False, "grund": "unlesbar",
                "meldung": "keine lesbare JSON-Zeile"}
    if anfrage.get("art") == "zustand":
        # Ein Zustand, FLACH -- so steht er im Vertrag. Ihn unter `router` zu
        # verschachteln waere eine zweite Form derselben Auskunft, und dann
        # sucht der Pruefstand `testprofil` an der falschen Stelle.
        zustand = mind.zustand()
        zustand.update({k: v for k, v in router.zustand().items()
                        if k not in ("v", "ok", "pid")})
        return zustand
    return router.frage(anfrage)


def bediene(mind: Mind, conn: socket.socket, *, router) -> None:
    with conn:
        conn.settimeout(200.0)
        try:
            anfrage: Any = json.loads(conn.makefile("rb").readline(MAX_ZEILE))
        except (OSError, json.JSONDecodeError, ValueError):
            anfrage = None
        antwort = bediene_anfrage(mind, anfrage, router=router)
        try:
            conn.sendall(json.dumps(antwort).encode() + b"\n")
        except OSError:
            pass


def lauf(mind: Mind, srv: socket.socket, *, router) -> int:
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        threading.Thread(target=bediene, args=(mind, conn),
                         kwargs={"router": router}, daemon=True).start()
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

    # T-4.16 K1: derselbe freigegebene Katalog wie der Hub (Policy.laden()),
    # nicht eine zweite Lesart derselben Datei. Scheitert das Laden, startet
    # der Dienst trotzdem -- ohne Werkzeuge ist `frage_werkzeug` nur ohne
    # Aktion nutzbar (`aktion_socket`/`_werkzeuge` bleiben leer).
    werkzeuge: list[dict] = []
    werkzeug_namen: dict[str, str] = {}
    try:
        from daimon.hub.policy import Policy
        werkzeuge, werkzeug_namen = werkzeuge_aus_katalog(Policy.laden().katalog)
    except Exception as exc:  # noqa: BLE001 -- Start darf davon nicht abhaengen
        get_logger("daimon-mind").warn(
            "Katalog fuer Werkzeuge nicht ladbar", DAIMON_ACTION="mind_start",
            DAIMON_GRUND=f"{type(exc).__name__}: {str(exc)[:160]}")

    mind = Mind(
        hub_socket=args.hub_socket or str(cfg.runtime_dir / TICKET_SOCKET),
        egress_socket=args.egress_socket or str(cfg.runtime_dir / EGRESS_SOCKET),
        aktion_socket=str(cfg.runtime_dir / AKTION_SOCKET),
        werkzeuge=werkzeuge, werkzeug_namen=werkzeug_namen,
        persona=persona,
        modell=str(cfg.get("mind.modell", MODELL)),
        max_tokens=int(cfg.get("mind.max_tokens", MAX_TOKENS)))

    # Der Router bekommt die echten Quellen und den Mind als Weg nach draussen.
    # Kein Router im Mind selbst: `Mind` soll weiterhin nur eines koennen, und
    # wer das eine mit dem anderen mischt, hat zwei Zusagen an einer Stelle.
    # Seit T-3.13 sitzt zwischen Router und Mind der zweite Durchgang: der
    # Router waehlt den Weg, Durchgang 2 fuehrt ihn nach draussen -- ohne
    # Werkzeugliste und mit einer Antwort, die nur Text sein kann. Durchgang 1
    # (T-4.16 K1) ist `mind` selbst -- `Mind.frage_werkzeug` sieht dafuer nie
    # den Kontext, den Durchgang 2 bekommt.
    router = Router(quellen=quellen_aus_umgebung(
        hub_socket=str(cfg.runtime_dir / STATE_SOCKET)),
        mind=Durchgang2(mind=mind), executor=mind)

    if args.frage is not None:
        antwort = router.frage({"v": 1, "art": "frage", "text": args.frage,
                                "marke": "user_ptt"})
        print(json.dumps(antwort, ensure_ascii=False)[:4000])
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
        return lauf(mind, srv, router=router)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
