"""T-8.3 -- der Zeitplaner. Erinnert an Termine und schliesst Fokusbloecke.

Drei Entscheidungen tragen diesen Dienst:

**Er loest nichts aus, er erinnert nur.** Die Policy verbietet
`initiator: scheduled` flaechend (`config/policy.yaml`) -- und das bleibt
so. Dieser Dienst kennt keinen Aktions-Socket. Was er kann: eine Blase
ueber den Hub (Produzent `plan`, Typ `termin_faellig`/`fokus_ende`) und
eine kuratierte Sprechvorlage ueber `tts-say.sock`. Beides ist sichtbar,
keines davon ist eine Handlung am Rechner.

**Suspend- und Neustart-Sicherheit liegt im Lesen, nicht in einem Wecker.**
Die Abtastschleife fragt jede Runde `store.faellige(jetzt)` -- ein Termin,
der waehrend eines Suspend oder Stopps faellig wurde, ist beim naechsten
Lauf einfach faellig. Es gibt keinen In-Memory-Wecker, der verloren gehen
koennte, und `gemeldet` in der Datenbank verhindert das Doppelfeuern nach
einem Neustart.

**Die Blase kommt immer, die Sprache nur durch das Gatter.** Der Anlass
laeuft durch `Proaktiv` (mind/proactive.py) -- der Baustein aus T-6.6, der
bis heute keinen Produktiv-Aufrufer hatte. Hier bekommt er seinen ersten:
die Schwelle kommt aus der Persona (`speech_threshold`), der Mindestabstand
und die Wiederholungssperre aus `Proaktiv`. Ein Termin, der unter der
Schwelle liegt, wird zur Blase und schweigt.

**Dieser Dienst schreibt keine Marke um.** `sprechtext.aus_vorlage` nimmt
variable Anteile nur als `trusted` (Design 8.3), und diese Entscheidung
faellt dort -- an der Stelle, die sie einhaelt. Der Zeitplaner schickt die
Marke, die am Eintrag steht, und sonst nichts. Bis zum 26.08. machte er aus
`user_ptt` ein `trusted`; damit wurde alles gesprochen, was das Mikrofon
waehrend des gehaltenen Tasters aufnahm. Praktische Folge heute: gesprochen
wird nur, was der Dienst selbst formuliert (Fokusende) -- ein Nutzertitel
wird zur Blase und schweigt.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from daimon.common import ipc
from daimon.common.config import load as load_config
from daimon.common.logging import get_logger
from daimon.common.protocol import Mark, Marked
from daimon.mind.proactive import Proaktiv, ProaktivFehler
from daimon.mind.threshold import Schwelle
from daimon.plan import zeit
from daimon.plan.store import Store, StoreFehler

MAX_ZEILE = 1 << 20

# Der Anfrage-Socket dieses Dienstes. NICHT `plan.sock`: der gehoert dem
# Hub, der dort als Produzenten-Socket fuer unsere Ereignisse horcht
# (`ipc.PRODUZENTEN["plan"]`). Wer beide auf denselben Namen legt, hat zwei
# Binder fuer einen Pfad.
ANFRAGE_SOCKET = "plan-anfrage.sock"
EREIGNIS_SOCKET = "plan.sock"        # Produzent beim Hub
SAY_SOCKET = "tts-say.sock"

# Die einzige Gegenstelle, deren Herkunftsmarke uebernommen wird.
MIND_UNIT = "daimon-mind.service"

# Die Scope, in die sich `python -m daimon.plan` selbst haengt (siehe
# `plan/__main__.py`). Ohne sie waere die Liste unten eine Attrappe: der
# zweite legitime Anrufer liefe in einer `app-*.scope` mit wechselndem Namen,
# und eine Liste, die ihn nicht nennen kann, laesst am Ende jeden durch.
CLI_UNIT = "daimon-plan-cli.scope"

# Wer SCHREIBEN darf. `liste` und `status` bleiben frei -- sie geben nur
# zurueck, was der Anrufer als Nutzer ohnehin lesen koennte.
#
# Die alte Begruendung ("die Schranke sitzt an der Marke") deckte nur das
# SPRECHEN. Nicht gedeckt und am 26.08. gemessen: ein fremder Prozess ohne
# Unit bekam auf `loeschen` ein `{"ok": true}` und loeschte fremde Eintraege,
# und `neu` trug seinen Text als Blase auf das Overlay -- damit war die
# Unit-Allowlist des Hub-Produzentensockets `plan.sock` gewaschen.
#
# Was diese Schranke NICHT ist: eine Authentifizierung (DESIGN.md 1.3). Wer
# unter dieser uid laeuft, kann sich selbst in eine `daimon-plan-cli.scope`
# haengen. Sie ist der Wegweiser, der den beilaeufigen Weg schliesst -- ein
# Hook, ein Skript, ein Helfer ohne systemd-Zugriff kommt nicht mehr durch.
SCHREIB_UNITS = frozenset({MIND_UNIT, CLI_UNIT})
SCHREIBENDE_ARTEN = frozenset({"neu", "loeschen", "fokus_start", "fokus_stop"})


def schreibrecht(art: object, unit: str) -> bool:
    """Darf `unit` diese `art` senden? Lesen immer, Schreiben nur benannt."""
    return art not in SCHREIBENDE_ARTEN or unit in SCHREIB_UNITS

ABTAST_S = 15.0

# Anlaesse, die dieser Dienst kennt. Die Dringlichkeiten stehen in
# `mind/proactive.ANLAESSE` -- dieselbe Tabelle, keine zweite Fassung.
ANLASS_TERMIN = "termin_faellig"
ANLASS_FOKUS = "fokus_ende"


class PlanFehler(RuntimeError):
    """Eine Anfrage, die dieser Dienst nicht bedienen kann."""


def _sock_senden(pfad: str, nachricht: dict, *, timeout_s: float = 5.0) -> None:
    """Eine Zeile hin, keine Antwort noetig. Fehler werden geloggt, nicht
    weitergereicht -- eine Blase, die den Scheduler umwirft, waere ein
    schlechter Handel."""
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout_s)
        try:
            c.connect(pfad)
            c.sendall(json.dumps(nachricht).encode() + b"\n")
        finally:
            c.close()
    except OSError:
        raise


class Plan:
    """Der Scheduler. Pruefbar ohne Socket: Uhr und Senken sind injizierbar."""

    def __init__(self, store: Store, *,
                 proaktiv: Proaktiv | None = None,
                 uhr: Callable[[], float] = time.time,
                 ereignis_senden: Callable[[dict], None] | None = None,
                 spruch_senden: Callable[[dict], None] | None = None,
                 log: Any = None) -> None:
        self.store = store
        self._uhr = uhr
        self.proaktiv = proaktiv or Proaktiv()
        self._ereignis = ereignis_senden or (lambda n: None)
        self._spruch = spruch_senden or (lambda n: None)
        self.log = log or get_logger("daimon-plan")
        self.gemeldet = 0

    # -- Der Kern: was ist faellig, und was passiert dann ------------------

    def runde(self) -> int:
        """Eine Abtastrunde. Gibt die Zahl der gemeldeten Eintraege zurueck."""
        jetzt = self._uhr()
        gemeldet = 0
        for e in self.store.faellige(jetzt):
            if not self._erinnere(e):
                # `gemeldet` waere hier eine Behauptung: es kam nichts an.
                # Der Eintrag bleibt `offen` und ist in der naechsten Runde
                # wieder faellig -- ein Hub-Neustart waehrend der Faelligkeit
                # hat die Erinnerung sonst lautlos verschluckt.
                continue
            self.store.markiere(e["id"], "gemeldet")
            gemeldet += 1
        self.gemeldet += gemeldet
        return gemeldet

    def _erinnere(self, eintrag: dict) -> bool:
        """Blase raus, Sprache durchs Gatter.

        Gibt zurueck, ob die BLASE angekommen ist -- sie ist die Zusage
        dieses Dienstes. Die Sprache ist der Zusatz: sie darf ausbleiben
        (Schwelle, Abstand, Marke), ohne dass die Erinnerung als erledigt
        gilt; ein toter Sprechweg soll den Termin nicht wiederholen.
        """
        titel = str(eintrag["titel"].value or "").strip()
        if eintrag["art"] == "fokus":
            anlass = ANLASS_FOKUS
            blase_titel, blase_text = "Fokus vorbei", "Kurz durchatmen."
        else:
            anlass = ANLASS_TERMIN
            blase_titel, blase_text = "Erinnerung", titel

        # Die Blase kommt IMMER. Sie ist Sichtbarmachung, keine Aeusserung.
        try:
            self._ereignis({"v": 1, "type": anlass, "ts": float(self._uhr()),
                            "payload": {"titel": blase_titel,
                                        "text": blase_text}})
        except OSError as exc:
            self.log.warn("Blasen-Ereignis nicht zugestellt",
                          DAIMON_GRUND=str(exc)[:160])
            return False

        # Die Sprache geht durch das Gatter. Kein Vorschlag, kein Spruch.
        #
        # Der Sachverhalt ist die EINTRAGS-ID, nicht der Text: `Proaktiv`
        # sperrt das Paar (anlass, sachverhalt) fuer die Prozesslebensdauer,
        # und mit "Kurz durchatmen." als Sachverhalt schwieg der Dienst ab
        # dem zweiten Fokusblock. Zwei Termine mit gleichem Titel sind zwei
        # Sachverhalte. Direkt danach `erledigt`: eine Faelligkeit ist ein
        # Ereignis und kein Zustand, der noch anliegen koennte -- sonst
        # waechst die Menge mit jedem Termin des Tages.
        sachverhalt = f"eintrag:{eintrag['id']}"
        try:
            vorschlag = self.proaktiv.melden(anlass, sachverhalt)
        except ProaktivFehler:
            vorschlag = None
        if vorschlag is None:
            return True
        self.proaktiv.erledigt(anlass, sachverhalt)

        # Die ECHTE Marke, nicht eine umgeschriebene. Wer entscheidet, ob ein
        # Titel gesprochen werden darf, ist `hub/sprechtext.aus_vorlage` --
        # eine Stelle, nicht zwei. Bis zum 26.08. ging `user_ptt` hier als
        # `trusted` hinaus, und damit wurde das, was das Mikrofon waehrend
        # des gehaltenen Tasters aufnahm, ungefragt gesprochen.
        try:
            self._spruch({"v": 1, "art": "sprich", "kanal": "ungefragt",
                          "anlass": anlass,
                          "werte": {"titel": titel} if eintrag["art"] != "fokus"
                                     else {},
                          "markierung": eintrag["titel"].mark.value})
        except OSError as exc:
            self.log.warn("Sprachanfrage nicht zugestellt",
                          DAIMON_GRUND=str(exc)[:160])
        return True

    # -- Anfragen ----------------------------------------------------------

    def bediene_anfrage(self, anfrage: object, *, unit: str = "") -> dict:
        """Eine Anfrage, eine Antwort. Ohne Socket, damit sie pruefbar ist.

        `unit` ist die systemd-Unit der GEGENSTELLE (aus `ipc.accept`), nicht
        eine Angabe der Nachricht. Sie entscheidet ueber die Herkunftsmarke.
        """
        if not isinstance(anfrage, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        art = anfrage.get("art")
        try:
            if art == "neu":
                return self._neu(anfrage, unit)
            if art == "liste":
                return self._liste(anfrage)
            if art == "loeschen":
                return self._loeschen(anfrage)
            if art == "fokus_start":
                return self._fokus_start(anfrage)
            if art == "fokus_stop":
                return self._fokus_stop()
            if art == "status":
                return self._status()
        except (PlanFehler, StoreFehler, zeit.ZeitFehler) as exc:
            return {"v": 1, "ok": False, "grund": "unbrauchbar",
                    "meldung": str(exc)[:200]}
        except (OSError, OverflowError) as exc:
            # `time.localtime` wirft bei riesigen Zeitstempeln OSError(75).
            # Eine einzelne unlesbare Zeile darf `--liste` nicht umwerfen --
            # sonst legt ein einziges `--in 99999999999999h` den Dienst
            # dauerhaft still, ueber Neustarts hinweg.
            return {"v": 1, "ok": False, "grund": "unbrauchbar",
                    "meldung": f"{type(exc).__name__}: {exc}"[:200]}
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}

    def _marke_aus(self, anfrage: dict, unit: str) -> Mark:
        """Die Herkunft des Titels -- aus dem PEER, nicht aus dem Feld.

        „Ein Feld, das der Absender setzt, sagt nichts" (hub/policy.py). Nur
        der Mind reicht eine Marke durch, naemlich die der Aeusserung, aus
        der der Titel stammt; `trusted` ist dabei nicht erreichbar, denn
        Nutzertext ist nie kuratiert. Jede andere Gegenstelle -- die CLI
        eingeschlossen, deren Unit eine `app-*.scope` mit wechselndem Namen
        ist -- bekommt `tainted`.

        Was die Marke bewirkt: sie entscheidet, ob der Titel spaeter
        GESPROCHEN werden darf. Die Entscheidung selbst faellt woanders, in
        `hub/sprechtext.aus_vorlage`.
        """
        if unit != MIND_UNIT:
            return Mark.TAINTED
        roh = str(anfrage.get("marke", "")).strip().lower()
        try:
            marke = Mark(roh)
        except ValueError:
            return Mark.TAINTED
        return Mark.TAINTED if marke is Mark.TRUSTED else marke

    def _neu(self, anfrage: dict, unit: str = "") -> dict:
        titel = str(anfrage.get("titel") or "").strip()
        wann = str(anfrage.get("wann") or "").strip()
        if not titel:
            raise PlanFehler("Titel fehlt")
        if not wann:
            raise PlanFehler("Zeitpunkt fehlt (`wann`)")
        ts = zeit.parse(wann, jetzt=self._uhr)
        marke = self._marke_aus(anfrage, unit)
        eintrag_id = self.store.anlegen("termin", Marked(titel, marke), ts)
        return {"v": 1, "ok": True, "id": eintrag_id, "ts_faellig": ts,
                "beschreibung": zeit.beschreibe(ts, jetzt=self._uhr)}

    def _liste(self, anfrage: dict) -> dict:
        status = anfrage.get("status")
        eintraege = [{
            "id": e["id"], "art": e["art"],
            "titel": str(e["titel"].value or ""),
            "ts_faellig": e["ts_faellig"], "status": e["status"],
            "quelle": e["quelle"],
            "beschreibung": zeit.beschreibe(e["ts_faellig"], jetzt=self._uhr),
        } for e in self.store.liste(
            str(status) if status is not None else None)]
        return {"v": 1, "ok": True, "eintraege": eintraege}

    def _loeschen(self, anfrage: dict) -> dict:
        try:
            eintrag_id = int(anfrage.get("id"))
        except (TypeError, ValueError):
            raise PlanFehler("`id` fehlt oder ist keine Zahl")
        return {"v": 1, "ok": True, "entfernt": self.store.loeschen(eintrag_id)}

    def _fokus_start(self, anfrage: dict) -> dict:
        try:
            minuten = float(anfrage.get("minuten"))
        except (TypeError, ValueError):
            raise PlanFehler("`minuten` fehlt oder ist keine Zahl")
        if not 1 <= minuten <= 480:
            raise PlanFehler("Fokus zwischen 1 und 480 Minuten")
        ts = self._uhr() + minuten * 60.0
        eintrag_id = self.store.anlegen(
            "fokus", Marked(f"Fokus {int(minuten)} Minuten", Mark.TRUSTED), ts)
        return {"v": 1, "ok": True, "id": eintrag_id, "ts_faellig": ts,
                "beschreibung": zeit.beschreibe(ts, jetzt=self._uhr)}

    def _fokus_stop(self) -> dict:
        gestoppt = 0
        for e in self.store.liste("offen"):
            if e["art"] == "fokus":
                self.store.markiere(e["id"], "gestoppt")
                gestoppt += 1
        return {"v": 1, "ok": True, "gestoppt": gestoppt}

    def _status(self) -> dict:
        offen = self.store.liste("offen")
        return {"v": 1, "ok": True, "offen": len(offen),
                "gemeldet_gesamt": self.gemeldet,
                "proaktiv": self.proaktiv.zaehler()}


# -- Draht -----------------------------------------------------------------# -- Draht -----------------------------------------------------------------

def bediene(plan: Plan, conn: socket.socket, unit: str = "") -> None:
    with conn:
        conn.settimeout(10.0)
        try:
            anfrage: Any = json.loads(conn.makefile("rb").readline(MAX_ZEILE))
        except (OSError, json.JSONDecodeError, ValueError):
            anfrage = None
        # Die Schranke sitzt HIER, am Draht: `unit` gibt es nur hier, weil sie
        # aus dem Peer kommt und nicht aus der Nachricht. `bediene_anfrage`
        # bleibt die reine Logik.
        if isinstance(anfrage, dict) and not schreibrecht(anfrage.get("art"),
                                                          unit):
            plan.log.warn("Schreibanfrage abgewiesen",
                          DAIMON_ACTION="plan_fremde_unit",
                          DAIMON_PEER_UNIT=str(unit)[:80],
                          DAIMON_ART=str(anfrage.get("art"))[:20])
            antwort = {"v": 1, "ok": False, "grund": "fremde_unit"}
        else:
            antwort = plan.bediene_anfrage(anfrage, unit=unit)
        try:
            conn.sendall(json.dumps(antwort, ensure_ascii=False).encode() + b"\n")
        except OSError:
            pass


def eigener_socket(pfad: str) -> socket.socket:
    if os.path.exists(pfad):
        os.unlink(pfad)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(pfad)
    os.chmod(pfad, 0o600)
    srv.listen(8)
    return srv


def lauf(plan: Plan, srv: socket.socket, *,
         abtast_s: float = ABTAST_S) -> int:
    """Anfragen in Threads, Abtastung im eigenen. Beendet nie von selbst."""
    stopp = threading.Event()

    def abtasten() -> None:
        while not stopp.wait(abtast_s):
            try:
                plan.runde()
            except Exception as exc:  # noqa: BLE001 -- der Dienst steht nie
                plan.log.error("Abtastrunde fehlgeschlagen",
                               DAIMON_GRUND=f"{type(exc).__name__}: {exc}"[:160])

    threading.Thread(target=abtasten, daemon=True).start()
    while True:
        # `ipc.accept` statt `srv.accept`: uid-Pruefung, Peer-Aufloesung und
        # ein Audit-Eintrag je Verbindung. Hier stand nacktes `accept()` --
        # derselbe Befund wie in `brokers/dienst.py` (T-4.5 K6). Die Unit der
        # Gegenstelle geht weiter an `bediene_anfrage`; sie und nicht das
        # Feld `marke` bestimmt die Herkunft.
        #
        # KEINE `erlaubte_units`-Liste an dieser Stelle, und das ist eine
        # Entscheidung: `liste` und `status` bleiben fuer jeden Prozess des
        # Nutzers offen, gefiltert wird erst nach der ART (`schreibrecht` in
        # `bediene`). Eine Liste hier wuerde auch das Lesen sperren und damit
        # jedes Skript, das nur nachsehen will.
        #
        # Die Marke regelt weiterhin nur das SPRECHEN: nur der Mind darf eine
        # mitbringen, alles andere ist `tainted` und schweigt.
        try:
            conn, peer = ipc.accept(
                srv, "plan-anfrage",
                audit=lambda was, p: plan.log.info(
                    "ipc", DAIMON_ACTION=was, DAIMON_PEER_PID=p.pid,
                    DAIMON_PEER_UNIT=p.unit))
        except ipc.PeerError as exc:
            plan.log.warn("Anfrage abgewiesen", DAIMON_GRUND=str(exc)[:160])
            continue
        except OSError:
            break
        threading.Thread(target=bediene, args=(plan, conn, peer.unit),
                         daemon=True).start()
    return 0


def _persona_schwelle(cfg: Any, log: Any) -> Schwelle:
    """Die Sprechstufe aus der Persona. Fehlt sie, gilt die Vorgabe --
    ein Zeitplaner, der wegen einer fehlenden Persona gar nicht startet,
    verhindert auch die Blasen, und die sind der wichtigere Teil."""
    try:
        from daimon.mind.persona import lade as persona_lade
        return Schwelle(persona_lade(cfg).speech_threshold)
    except Exception as exc:  # noqa: BLE001 -- Vorgabe statt Startverweigerung
        log.warn("Persona nicht ladbar, Sprechstufe = Vorgabe",
                 DAIMON_GRUND=str(exc)[:160])
        return Schwelle()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Zeitplaner (T-8.3)")
    ap.add_argument("--socket", default=None,
                    help="Pfad des Anfrage-Sockets (Vorgabe: plan-anfrage.sock)")
    args = ap.parse_args(argv)

    cfg = load_config(make_dirs=False)
    log = get_logger("daimon-plan")
    store = Store()
    store.migrieren()

    runtime = cfg.runtime_dir
    plan = Plan(
        store,
        proaktiv=Proaktiv(schwelle=_persona_schwelle(cfg, log)),
        ereignis_senden=lambda n: _sock_senden(str(runtime / EREIGNIS_SOCKET), n),
        spruch_senden=lambda n: _sock_senden(str(runtime / SAY_SOCKET), n),
        log=log)

    pfad = args.socket or str(runtime / ANFRAGE_SOCKET)
    srv = eigener_socket(pfad)
    log.info("Zeitplaner hoert", DAIMON_SOCKET=pfad)
    try:
        return lauf(plan, srv, abtast_s=float(cfg.get("plan.abtast_s", ABTAST_S)))
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
