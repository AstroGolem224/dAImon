#!/usr/bin/env python3
"""Blinder Laufzeit-Prüfstand für T-3.13 (Routing, Durchgang 2).

Gebaut ausschließlich aus dem Vertrag (T-3.13-Durchgang2-Plan.md §2/§3,
dazu der Nachtrag §6 vom 05.08.: der Kontext steht als abgesetzter Block
im Nutzertext, und der Körper trägt nur Top-Level-Felder der Messages-API).
Die Implementierung wurde nie gelesen. Kein Aufruf verlässt die Maschine:
Hub-Ticketbuch, Hub-Zustand, Egress und die lokalen Quellen (wpctl, KWin)
sind Attrappen des Reviewers; der Persona-Prompt ist eine Kanarie des
Reviewers in der eigenen XDG-Konfiguration. Negative Aussagen bekommen im
selben Lauf eine Positivkontrolle am gleichen Messpunkt: „keine
Werkzeugliste" steht neben „Frage und Persona sind im Körper", „Vorschlag
verworfen" neben „harmlose Antwort setzt das Flag nicht", „user_audio in
Durchgang 1 abgelehnt" neben „in Durchgang 2 beantwortet".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
TARGET = Path(sys.argv[2]).resolve()
FIXTURE = bool(os.environ.get("DAIMON_FIXTURE"))
PYTHON = REPO / ".venv/bin/python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

FRAGE_KANARIE = "T313-FRAGE-KANARIE-3f8a1c"
FRAGE_KANARIE_2 = "T313-FRAGE-KANARIE-2-b604de"
PERSONA_KANARIE = "T313-PERSONA-KANARIE-9d2e4b"
FENSTER_KANARIE = "T313-FENSTER-KANARIE-6c5f02"
SITZUNG_KANARIE = "T313-SITZUNG-KANARIE-58e1d3"
PROJEKT_KANARIE = "T313-PROJEKT-KANARIE-27aa90"
ANTWORT_KANARIE = "T313-ANTWORT-KANARIE-1b7d90"
# Zwei eigene Kanarien fuer den Gedaechtnisschnitt in K9: eine Frage,
# die Durchgang 2 gestellt bekam, und die Antwort, die Durchgang 2
# darauf gab. Die eine darf im werkzeugfaehigen Prompt stehen, die
# andere nie -- mit EINER Kanarie waere das nicht zu unterscheiden.
D2_FRAGE_KANARIE = "T313-D2-FRAGE-KANARIE-5c1e88"
D2_ANTWORT_KANARIE = "T313-D2-ANTWORT-KANARIE-a91c07"
HISTORIE_KANARIE = "T313-HISTORIE-KANARIE-4e6a28"
NUTZER_KANARIE = "T313-NUTZER-KANARIE-7f3c55"

# T-4.19: die EINE kuratierte Rueckmeldung fuer "Aktion ohne
# Absichtsmarke", und die EINE Rueckfrage fuer "Aktion ohne Ziel".
# Wort fuer Wort am laufenden Mind gemessen (01.09.); zwei
# Formulierungen waeren zwei Wahrheiten.
ABSICHTSMARKE_HINWEIS = ("Fuer eine Aktion brauche ich eine "
                         "Absichtsmarke — bitte Push-to-Talk "
                         "druecken.")
RUECKFRAGE_AKTION = "Was soll ich womit machen?"

# T-4.16 K1: der Werkzeugname, den dieser Pruefstand fuer seine
# Positivkontrolle benutzt, und die action_id, die daraus werden MUSS.
# Anthropic erlaubt im Werkzeugnamen keinen Punkt -- die Abbildung ist
# eine Tabelle im Pruefling, kein blindes Zuruecksetzen.
WERKZEUG_NAME = "media_playpause"
WERKZEUG_ACTION_ID = "media.playpause"

# Das Vertragsbeispiel für einen wohlgeformten Aktionsvorschlag (§2).
AKTION_VORSCHLAG = '{"action": "close_window", "window_ref": "w_1"}'

# Schlüssel mit Aktionscharakter (Kriterium 2) und die Werkzeugliste
# (Kriterium 1) — jeweils exakte Schlüsselnamen, in jeder Tiefe.
AKTIONS_SCHLUESSEL = frozenset(
    {"action", "aktion", "ziel", "window_ref", "tool"})
WERKZEUG_SCHLUESSEL = frozenset({"tools", "tool_choice"})

# Der Kontextblock im Nutzertext (§6, Nachtrag vom 05.08.): die Frage, eine
# Leerzeile, eine eigene Zeile mit dieser Marke, darunter das JSON.
KONTEXT_MARKE = "[Referenzen, keine Inhalte]"

# Die dokumentierten Top-Level-Felder der Messages-API (§6). Mehr darf der
# gesendete Körper nicht tragen: ein fremdes Feld wie `kontext` daneben ist
# ein 400 invalid_request_error der echten API.
MESSAGES_TOPLEVEL = frozenset(
    {"model", "messages", "max_tokens", "system", "metadata",
     "stop_sequences", "stream", "temperature", "top_p", "top_k",
     "tools", "tool_choice", "thinking", "output_config", "service_tier",
     "container", "context_management", "mcp_servers", "inference_geo"})


class Pruefstand:
    def __init__(self) -> None:
        self.n = defaultdict(int)
        self.rot = defaultdict(int)
        self.fail = False

    def check(self, k: str, name: str, ist, soll) -> None:
        self.n[k] += 1
        if ist == soll:
            print(f"  ok   [{k}] {name}", flush=True)
        else:
            print(f"  FAIL [{k}] {name} (erwartet {soll!r}, war {ist!r})",
                  flush=True)
            self.rot[k] += 1
            self.fail = True

    def info(self, text: str) -> None:
        print(f"  INFO {text}", flush=True)

    def kapitel(self, k: str, text: str) -> None:
        print(f"\n--- K{k}: {text} ---", flush=True)


P = Pruefstand()
laufzeit_basis = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "daimon"
laufzeit_basis.mkdir(parents=True, exist_ok=True)
RT = Path(tempfile.mkdtemp(prefix="t313-", dir=laufzeit_basis))
CONFIG = RT / "config"
STATE = RT / "state"
RUNTIME = RT / "runtime"
DAIMON_RT = RUNTIME / "daimon"
QUELLEN = RT / "quellen"
BEOBACHTER = RT / "beobachter"
for d in (CONFIG / "daimon" / "persona", STATE / "daimon", DAIMON_RT,
          QUELLEN, BEOBACHTER):
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)

MIND_SOCK = DAIMON_RT / "mind.sock"
prozesse: list[subprocess.Popen] = []


def aufraeumen() -> None:
    for p in reversed(prozesse):
        if p.poll() is None:
            p.terminate()
    deadline = time.monotonic() + 2
    for p in reversed(prozesse):
        if p.poll() is None:
            try:
                p.wait(max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
    shutil.rmtree(RT, ignore_errors=True)


import atexit
atexit.register(aufraeumen)


def warten(pfad: Path, prozess: subprocess.Popen | None = None,
           sekunden: float = 8) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if pfad.exists():
            return True
        if prozess is not None and prozess.poll() is not None:
            return False
        time.sleep(0.03)
    return False


def unix_roh(pfad: Path, roh: bytes, timeout: float = 10) -> bytes:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    try:
        c.connect(str(pfad))
        c.sendall(roh if roh.endswith(b"\n") else roh + b"\n")
        return c.makefile("rb").readline(4 << 20)
    finally:
        c.close()


def unix_json(pfad: Path, obj: dict) -> tuple[dict, bytes]:
    roh = json.dumps(obj, ensure_ascii=False,
                     separators=(",", ":")).encode() + b"\n"
    antwort = unix_roh(pfad, roh)
    try:
        return json.loads(antwort), antwort
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, antwort


def schluessel_weg(obj: object, gesucht: frozenset) -> list[str]:
    """Alle Schlüsselnamen aus `gesucht`, exakt und in jeder Tiefe."""
    treffer: list[str] = []

    def walk(o: object) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if str(k).lower() in gesucht:
                    treffer.append(str(k))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return treffer


def kontext_aus_inhalt(inhalt: object) -> tuple[str | None, dict | None, str]:
    """Den abgesetzten Kontextblock im Nutzertext lesen (§6): Marke suchen,
    das JSON darunter lesen, den REST zurueckgeben.

    Bis zum 31.08. las diese Funktion mit `json.loads` bis zum Zeilenende
    und meldete `(Frageteil, None)`, sobald hinter dem JSON noch etwas
    stand. Seit T-3.9 steht dort etwas -- der Ausgabeform-Block. Der
    Kontext war die ganze Zeit da; nur der Leser kam nicht an ihm vorbei
    (gemessen am 01.09., siehe die Begruendung in K6). `raw_decode` liest
    genau EIN JSON-Gefuege und sagt, wo es endet; was danach kommt, geht
    als `rest` heraus und wird geprueft statt verschwiegen.

    (None, None, "") wenn die Marke fehlt; (Frageteil, None, Rohtext),
    wenn das JSON darunter nicht lesbar ist.
    """
    if not isinstance(inhalt, str):
        return None, None, ""
    marke = "\n" + KONTEXT_MARKE + "\n"
    if marke not in inhalt:
        return None, None, ""
    frage_teil, json_teil = inhalt.rsplit(marke, 1)
    rumpf = json_teil.lstrip()
    try:
        obj, ende = json.JSONDecoder().raw_decode(rumpf)
    except json.JSONDecodeError:
        return frage_teil, None, json_teil
    return (frage_teil, obj if isinstance(obj, dict) else None, rumpf[ende:])


# ---------------------------------------------------------------- Attrappen


class HubAttrappe:
    """Das Ticketbuch und der Zustand des Reviewers. Zählt Ausgaben und
    Einlösungen selbst und protokolliert jede Anfrage roh — ein
    action_request würde hier sichtbar, nicht an der Selbstauskunft."""

    def __init__(self, rt: Path) -> None:
        self.rt = rt
        self.ausgegeben = 0
        self.eingeloest = 0
        self.kein_ticket = False
        self.state_weg = False
        self.tickets: dict[str, dict] = {}
        self.anfragen: list[str] = []
        self.lock = threading.Lock()
        self._stopp = threading.Event()
        self._server: list[socket.socket] = []
        for name, behandler in (("ticket.sock", self._ticket),
                                ("state.sock", self._state)):
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            pfad = rt / name
            pfad.unlink(missing_ok=True)
            srv.bind(str(pfad))
            pfad.chmod(0o600)
            srv.listen(16)
            srv.settimeout(0.3)
            self._server.append(srv)
            threading.Thread(target=self._schleife, args=(srv, behandler),
                             daemon=True).start()

    def _schleife(self, srv: socket.socket, behandler) -> None:
        while not self._stopp.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=behandler, args=(conn,),
                             daemon=True).start()

    def _ticket(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(5)
                roh = conn.makefile("rb").readline(1 << 20)
                req = json.loads(roh)
                with self.lock:
                    self.anfragen.append(
                        roh.decode("utf-8", "replace").strip())
                    if req.get("art") == "ausgeben" and not self.kein_ticket:
                        tid = secrets.token_hex(16)
                        self.tickets[tid] = {"hash": req.get("auftrag_hash"),
                                             "verbraucht": False}
                        self.ausgegeben += 1
                        ans = {"v": 1, "ok": True, "ticket": tid,
                               "frist_s": 300}
                    elif req.get("art") == "einloesen":
                        t = self.tickets.get(str(req.get("ticket")))
                        ok = bool(t and not t["verbraucht"]
                                  and t["hash"] == req.get("auftrag_hash"))
                        if t:
                            t["verbraucht"] = True
                        if ok:
                            self.eingeloest += 1
                        ans = {"v": 1, "ok": ok}
                    else:
                        ans = {"v": 1, "ok": False, "grund": "abgelehnt"}
            except Exception:
                ans = {"v": 1, "ok": False}
            try:
                conn.sendall(json.dumps(ans, separators=(",", ":")).encode()
                             + b"\n")
            except OSError:
                pass

    def _state(self, conn: socket.socket) -> None:
        with conn:
            if self.state_weg:
                return
            snap = {"v": 2, "rev": 7, "mood": "working", "sessions": 1,
                    "focus": {"session_id": SITZUNG_KANARIE,
                              "project": PROJEKT_KANARIE},
                    "bubble": None,
                    "voice": {"state": "idle", "listening": False,
                              "tts_active": False},
                    "perception": {"ears": False, "eyes": False,
                                   "gpu_loaded": []}}
            try:
                conn.sendall(json.dumps(snap, separators=(",", ":")).encode()
                             + b"\n")
            except OSError:
                pass

    def zaehler(self) -> tuple[int, int]:
        with self.lock:
            return self.ausgegeben, self.eingeloest

    def log(self) -> list[str]:
        with self.lock:
            return list(self.anfragen)

    def zuruecksetzen(self) -> None:
        with self.lock:
            self.ausgegeben = 0
            self.eingeloest = 0
            self.tickets = {}
            self.anfragen = []


class EgressAttrappe:
    """Der Egress des Reviewers: löst Tickets am eigenen Ticketbuch ein,
    zeichnet jeden Körper auf, spielt den Modelltext des Reviewers und
    kann auf Knopf absagen oder weg sein."""

    def __init__(self, rt: Path, hub: HubAttrappe) -> None:
        self.rt = rt
        self.hub = hub
        self.pfad = rt / "egress.sock"
        self.modus = "normal"  # normal | absage
        self.antwort_text = ANTWORT_KANARIE
        # Freie Antwortbloecke statt nur Text: nur so kann dieser Pruefstand
        # das Modell EIN WERKZEUG rufen lassen (K9) -- und damit belegen,
        # dass die Stille an `aktion.sock` gemessen und nicht bloss
        # ungebunden ist.
        self.bloecke: list[dict] | None = None
        self.aufrufe: list[bytes] = []
        self.lock = threading.Lock()
        self._stopp = threading.Event()
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self.start()

    def start(self) -> None:
        self._stopp.clear()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.pfad.unlink(missing_ok=True)
        srv.bind(str(self.pfad))
        self.pfad.chmod(0o600)
        srv.listen(16)
        srv.settimeout(0.3)
        self._srv = srv
        self._thread = threading.Thread(target=self._schleife, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopp.set()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        self.pfad.unlink(missing_ok=True)
        if self._thread is not None:
            self._thread.join(2)

    def _schleife(self) -> None:
        assert self._srv is not None
        while not self._stopp.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._bediene, args=(conn,),
                             daemon=True).start()

    def _bediene(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(10)
                zeile = conn.makefile("rb").readline(4 << 20)
                req = json.loads(zeile)
                if req.get("art") != "anfrage":
                    ans: dict = {"v": 1, "ok": False,
                                 "grund": "unbekannte_art"}
                else:
                    ans = self._anfrage(req)
            except Exception:
                ans = {"v": 1, "ok": False, "grund": "unlesbar"}
            try:
                conn.sendall(json.dumps(ans, ensure_ascii=False,
                                        separators=(",", ":")).encode()
                             + b"\n")
            except OSError:
                pass

    def _anfrage(self, req: dict) -> dict:
        koerper = req.get("koerper")
        if not isinstance(koerper, dict):
            return {"v": 1, "ok": False, "grund": "kein_koerper"}
        kanonisch = json.dumps(koerper, sort_keys=True,
                               separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        with self.lock:
            self.aufrufe.append(kanonisch)
            text = self.antwort_text
            bloecke = self.bloecke
        h = hashlib.sha256(kanonisch).hexdigest()
        try:
            eingel, _ = unix_json(self.rt / "ticket.sock",
                                  {"v": 1, "art": "einloesen",
                                   "ticket": req.get("ticket"),
                                   "auftrag_hash": h})
        except OSError:
            eingel = {"ok": False}
        if not eingel.get("ok"):
            return {"v": 1, "ok": False, "grund": "ticket_ungueltig",
                    "meldung": "Ticket unbekannt oder verbraucht"}
        if self.modus == "absage":
            return {"v": 1, "ok": False, "grund": "ziel_weg",
                    "meldung": "Ziel nicht erreichbar"}
        antwort = {"id": "lokal",
                   "content": bloecke or [{"type": "text", "text": text}],
                   "stop_reason": "tool_use" if bloecke else "end_turn"}
        return {"v": 1, "ok": True, "status": 200, "bytes": 80,
                "dauer_ms": 1.0, "antwort": antwort}

    def zaehler(self) -> int:
        with self.lock:
            return len(self.aufrufe)

    def letzter_koerper(self) -> bytes:
        with self.lock:
            return self.aufrufe[-1]

    def setze_antwort(self, text: str) -> None:
        with self.lock:
            self.antwort_text = text

    def setze_bloecke(self, bloecke: list[dict] | None) -> None:
        with self.lock:
            self.bloecke = bloecke


class AktionAttrappe:
    """Der Koordinator des Reviewers an `aktion.sock` (T-4.16 K1).

    Er zaehlt, was ankommt, und fuehrt nichts aus. Er MUSS gebunden sein,
    damit "aktion.sock bekommt keinen Aufruf" ein MESSWERT ist: an einem
    ungebundenen Socket ist Stille von Abwesenheit nicht zu unterscheiden --
    und genau das waere ein Falschbefund in die harmlose Richtung.
    """

    def __init__(self, rt: Path) -> None:
        self.aufrufe: list[bytes] = []
        self.lock = threading.Lock()
        self._stopp = threading.Event()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        pfad = rt / "aktion.sock"
        pfad.unlink(missing_ok=True)
        srv.bind(str(pfad))
        pfad.chmod(0o600)
        srv.listen(16)
        srv.settimeout(0.3)
        self._srv = srv
        threading.Thread(target=self._schleife, daemon=True).start()

    def _schleife(self) -> None:
        while not self._stopp.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    zeile = conn.makefile("rb").readline(1 << 20)
                    with self.lock:
                        self.aufrufe.append(zeile.strip())
                    conn.sendall(json.dumps(
                        {"v": 1, "ok": True, "ausgefuehrt": False,
                         "verdikt": "deny", "grund": "pruefstand",
                         "gesprochen": ""}).encode() + b"\n")
                except OSError:
                    continue

    def zaehler(self) -> int:
        with self.lock:
            return len(self.aufrufe)

    def letzte(self) -> dict:
        with self.lock:
            if not self.aufrufe:
                return {}
            roh = self.aufrufe[-1]
        try:
            return json.loads(roh)
        except json.JSONDecodeError:
            return {}

    def zuruecksetzen(self) -> None:
        with self.lock:
            self.aufrufe.clear()


# ------------------------------------------------------- Quellen-Attrappen


def schreibe_stub(pfad: Path, name: str) -> None:
    """Ein Stub, der seinen Aufruf protokolliert und sonst nichts tut —
    außer den beiden lesenden Quellenantworten, die der Vertrag kennt."""
    rumpf = f"""#!/usr/bin/env bash
# T-3.13-Quellenattrappe "{name}" — protokolliert, veraendert nichts.
LOG="{QUELLEN}/aufrufe.log"
printf '%s %s\\n' "{name}" "$*" >> "$LOG"
case "{name}" in
  wpctl)
    [[ -f "{QUELLEN}/AUSFALL_WPCTL" ]] && exit 1
    if [[ " $* " == *" get-volume "* ]]; then
      echo "Volume: 0.42"
    fi
    ;;
  pactl)
    [[ -f "{QUELLEN}/AUSFALL_WPCTL" ]] && exit 1
    ;;
  qdbus|qdbus6)
    [[ -f "{QUELLEN}/AUSFALL_KWIN" ]] && exit 1
    [[ " $* " == *"Match"* ]] && cat "{QUELLEN}/fenster.qdbus"
    ;;
  dbus-send)
    [[ -f "{QUELLEN}/AUSFALL_KWIN" ]] && exit 1
    [[ " $* " == *"Match"* ]] && cat "{QUELLEN}/fenster.dbus-send"
    ;;
  gdbus)
    [[ -f "{QUELLEN}/AUSFALL_KWIN" ]] && exit 1
    [[ " $* " == *"Match"* ]] && cat "{QUELLEN}/fenster.gdbus"
    ;;
esac
exit 0
"""
    pfad.write_text(rumpf, encoding="utf-8")
    pfad.chmod(0o700)


def setze_fenster(fenster: list[tuple[str, str]]) -> None:
    """Schreibt die Fensterliste in den drei gemessenen Dialekten, die die
    Werkzeuge am 05.08. am lebenden KWin gezeigt haben."""
    teile = [
        f'[Argument: (sssida{{sv}}) "{k}", "{t}", "", 100, 0.8, '
        f'[Argument: a{{sv}} {{}}]]' for k, t in fenster]
    (QUELLEN / "fenster.qdbus").write_text(
        "[Argument: a(sssida{sv}) {" + ", ".join(teile) + "}]\n",
        encoding="utf-8")
    zeilen = ["method return time=1.0 sender=:1.11 -> destination=:1.999 "
              "serial=2 reply_serial=2", "   array ["]
    for k, t in fenster:
        zeilen += ["      struct {",
                   f'         string "{k}"',
                   f'         string "{t}"',
                   '         string ""',
                   "         int32 100",
                   "         double 0.8",
                   "         array [",
                   "         ]",
                   "      }"]
    zeilen.append("   ]")
    (QUELLEN / "fenster.dbus-send").write_text("\n".join(zeilen) + "\n",
                                               encoding="utf-8")
    g = ", ".join(f"('{k}', '{t}', '', 100, 0.8, {{}})" for k, t in fenster)
    (QUELLEN / "fenster.gdbus").write_text(f"([{g}],)\n", encoding="utf-8")


def quellen_log() -> list[str]:
    pfad = QUELLEN / "aufrufe.log"
    if not pfad.is_file():
        return []
    return pfad.read_text(encoding="utf-8", errors="replace").splitlines()


def beobachter_log() -> list[str]:
    pfad = BEOBACHTER / "aufrufe.log"
    if not pfad.is_file():
        return []
    return pfad.read_text(encoding="utf-8", errors="replace").splitlines()


FENSTER = [("0_{aaaa1111-2222-3333-4444-555566667777}",
            f"{FENSTER_KANARIE} — Discord"),
           ("1_{bbbb2222-3333-4444-5555-666677778888}",
            "projekt — Konsole")]

for name in ("wpctl", "pactl", "qdbus", "qdbus6", "dbus-send", "gdbus"):
    schreibe_stub(QUELLEN / name, name)
setze_fenster(FENSTER)

# Angriffsnutzlasten werden beobachtet, nicht ausgeführt: diese Stubs
# stehen im PATH des Prüflings und protokollieren nur ihre Argumente.
for name in ("systemctl", "kdotool", "xdotool", "wmctrl", "t313-werkzeug"):
    stub = BEOBACHTER / name
    stub.write_text(f"""#!/usr/bin/env bash
# T-3.13-Beobachter "{name}" — protokolliert Argumente, tut nichts.
printf '%s %s\\n' "{name}" "$*" >> "{BEOBACHTER}/aufrufe.log"
exit 0
""", encoding="utf-8")
    stub.chmod(0o700)

# Die Persona des Reviewers: XDG gewinnt (T-3.10), also liegt die Kanarie
# in der eigenen Konfiguration des Laufzeitraums.
(CONFIG / "daimon" / "daimon.toml").write_text(
    '[persona]\nname = "Pruefpersona"\n', encoding="utf-8")
(CONFIG / "daimon" / "persona" / "pruefpersona.toml").write_text(
    f'''name = "Prüfpersona"
speech_threshold = "helpful"
traits = []
system_prompt = "Du bist die Prüfpersona. Erkennungszeichen: {PERSONA_KANARIE}."
''', encoding="utf-8")


# ------------------------------------------------------------------ Mind


# systemd loest seine Kuerzel vor dem exec auf; ein direkter Popen-Start
# muss dasselbe tun. Sonst bekommt der Mind seinen Modellweg als woertliches
# "%t/daimon/lokal.sock" -- einen Pfad, den es nicht gibt -- und jede
# Modellfrage endet in der kuratierten Absage mit Marke `trusted`.
# Aufgeloest wird genau, was in den ExecStart-Zeilen unter config/systemd
# vorkommt: %t (Laufzeitverzeichnis) und %h (Heimatverzeichnis). Das dritte,
# %i, steht nur in der Vorlage daimon-gpu@.service und hat ohne Instanz
# keinen Wert -- es bleibt stehen und faellt in `rest_kuerzel` auf.
# Bewusst wortgleich zu t312/t313/t313b dupliziert: die vier Pruefstaende
# importieren kein gemeinsames Modul, und eine neue gemeinsame Datei waere
# eine weitere eingefrorene Abhaengigkeit -- das ist Matthias' Entscheidung.
KUERZEL = {"%t": str(RUNTIME), "%h": os.environ.get("HOME", str(RT))}


def unit_execstart(pfad: Path) -> list[str]:
    text = pfad.read_text(encoding="utf-8")
    text = re.sub(r"\\\s*\n\s*", " ", text)
    treffer = re.search(r"(?m)^ExecStart=(.+)$", text)
    if not treffer:
        return []
    argv = []
    for a in shlex.split(treffer.group(1)):
        a = a.replace(str(REPO), str(TARGET))
        for k, v in KUERZEL.items():
            a = a.replace(k, v)
        argv.append(a)
    if "--egress-socket" in argv:
        i = argv.index("--egress-socket")
        if i + 1 < len(argv):
            # Wie in t312: die Produkt-Unit zeigt auf den vorgeschalteten
            # lokalen Broker (%t/daimon/lokal.sock). T-3.13 haelt an dieser
            # Stelle keine Attrappe -- es misst den Router bewusst an seiner
            # eigenen Egress-Attrappe. Ohne diese Umleitung liefe der Mind
            # trotz aufgeloestem %t gegen eine Datei, die es nicht gibt.
            argv[i + 1] = str(DAIMON_RT / "egress.sock")
    return argv


def rest_kuerzel(argv: list[str]) -> list[str]:
    """Positivkontrolle zu `unit_execstart`: was danach noch nach einem
    systemd-Kuerzel aussieht. Ein stehengebliebenes Kuerzel ist ein Pfad,
    den es nicht gibt -- er darf nie wieder stillschweigend als Modellweg
    durchgehen, sondern muss den Lauf rot faerben."""
    return sorted({m for a in argv for m in re.findall(r"%[a-zA-Z%]", a)})


MIND_UNIT = TARGET / "config/systemd/daimon-mind.service"
MIND_CMD = unit_execstart(MIND_UNIT) if MIND_UNIT.is_file() else []
MIND_REST = rest_kuerzel(MIND_CMD)


def mind_umgebung(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(TARGET),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_RUNTIME_DIR": str(RUNTIME),
        "XDG_CONFIG_HOME": str(CONFIG),
        "XDG_STATE_HOME": str(STATE),
        # Vollständige Locale weitergeben: ohne LANG läuft ein Kind in der
        # C-Locale, und dann sind Nicht-ASCII-Angriffstexte keine mehr.
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "LC_ALL": os.environ.get("LC_ALL") or "",
        # Die Beobachter stehen im PATH des Prüflings: ein Ausführungs-
        # versuch wird protokolliert und richtet nichts an. QUELLEN steht
        # absichtlich NICHT im PATH — die Umleitung muss der Prüfling
        # selbst aus DAIMON_ROUTER_QUELLEN ableiten.
        "PATH": str(BEOBACHTER) + os.pathsep
                + os.environ.get("PATH", "/usr/bin:/bin"),
        "DAIMON_ROUTER_QUELLEN": str(QUELLEN),
        "DAIMON_ROUTER_TESTPROFIL": "1",
    })
    if not env["LC_ALL"]:
        env.pop("LC_ALL")
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    if extra:
        env.update(extra)
    return env


class Mind:
    def __init__(self) -> None:
        self.p: subprocess.Popen | None = None
        self.logpfad = RT / "mind.log"

    def start(self, **opts) -> None:
        self.stop()
        log = self.logpfad.open("ab")
        self.p = subprocess.Popen(MIND_CMD, env=mind_umgebung(**opts),
                                  cwd=TARGET,
                                  stdout=log, stderr=subprocess.STDOUT)
        prozesse.append(self.p)

    def stop(self) -> None:
        if self.p is not None and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
        MIND_SOCK.unlink(missing_ok=True)


def frage(text: str, *, marke: str = "user_ptt",
          runde: object = "haupt") -> tuple[dict, bytes]:
    return unix_json(MIND_SOCK, {"v": 1, "art": "frage", "text": text,
                                 "marke": marke, "runde": runde})


def zustand() -> dict:
    ans, _ = unix_json(MIND_SOCK, {"v": 1, "art": "zustand"})
    return ans


def kanariefrei(roh: bytes, *kanarien: str) -> bool:
    return all(k.encode() not in roh for k in kanarien)


# ------------------------------------------------------------- Prüfverlauf

print("T-3.13 — Prüfstand Durchgang 2, ausschließlich lokale Attrappen")
print(f"  Baum: {TARGET}")
print(f"  Modus: {'FIXTURE' if FIXTURE else 'ARBEITSBAUM'}")
print(f"  Laufzeitraum: {RT}")

hub = HubAttrappe(DAIMON_RT)
egress = EgressAttrappe(DAIMON_RT, hub)
aktion = AktionAttrappe(DAIMON_RT)
mind = Mind()

P.kapitel("V", "Voraussetzungen und Messstrecke")
for name, ok in (
    ("Mind-Service-Unit vorhanden", MIND_UNIT.is_file()),
    ("Mind-ExecStart aus Unit auflösbar", bool(MIND_CMD)),
    (f"kein systemd-Kürzel bleibt im Mind-ExecStart stehen "
     f"(gefunden: {MIND_REST or 'keins'})", not MIND_REST),
    ("wpctl-Attrappe antwortet lesbar", subprocess.run(
        [str(QUELLEN / "wpctl"), "get-volume", "@DEFAULT_AUDIO_SINK@"],
        capture_output=True).stdout == b"Volume: 0.42\n"),
    ("qdbus6-Attrappe liefert das Kanarienfenster",
     FENSTER_KANARIE in subprocess.run(
         [str(QUELLEN / "qdbus6"), "--literal", "org.kde.KWin",
          "/WindowsRunner", "org.kde.krunner1.Match", ""],
         capture_output=True).stdout.decode()),
    ("Persona-Kanarie liegt in der XDG-Konfiguration",
     PERSONA_KANARIE in (CONFIG / "daimon" / "persona"
                         / "pruefpersona.toml").read_text("utf-8")),
):
    P.check("V", name, ok, True)

# Selbsttest der Messstrecke, bevor der Prüfling läuft: Ticket ausgeben,
# Körper an die Egress-Attrappe, Einlösung muss gezählt werden.
selbst_koerper = {"model": "selbsttest", "messages": [], "max_tokens": 1}
selbst_hash = hashlib.sha256(json.dumps(
    selbst_koerper, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False).encode()).hexdigest()
ausg, _ = unix_json(DAIMON_RT / "ticket.sock",
                    {"v": 1, "art": "ausgeben", "zweck": "api",
                     "auftrag_hash": selbst_hash})
P.check("V", "Messstrecke: Ticketbuch gibt ein Ticket aus",
        ausg.get("ok"), True)
selbst_ans, _ = unix_json(DAIMON_RT / "egress.sock",
                          {"v": 1, "art": "anfrage",
                           "ticket": ausg.get("ticket", ""),
                           "koerper": selbst_koerper})
P.check("V", "Messstrecke: Egress-Attrappe nimmt das Ticket an",
        selbst_ans.get("ok"), True)
P.check("V", "Messstrecke: Einlösung wurde gezählt", hub.zaehler(), (1, 1))
# Derselbe Selbsttest für `aktion.sock`: den Weg, der in K9 STILL bleiben
# soll. Ein Socket, der schon hier nicht antwortet, könnte dort keine Stille
# belegen — er zeigte nur seine eigene Abwesenheit.
akt_ans, _ = unix_json(DAIMON_RT / "aktion.sock",
                       {"v": 1, "art": "ausfuehren",
                        "action_id": "messstrecke.selbsttest", "params": {}})
P.check("V", "Messstrecke: Aktions-Attrappe antwortet und zählt",
        (akt_ans.get("ok"), aktion.zaehler()), (True, 1))
aktion.zuruecksetzen()
hub.zuruecksetzen()
with egress.lock:
    egress.aufrufe.clear()
quellen_log()  # Logdatei existiert ab jetzt sicher

mind.start()
P.check("V", "Mind-Socket erscheint", warten(MIND_SOCK, mind.p), True)
if P.rot["V"]:
    print("\nT-3.13: FEHLGESCHLAGEN — Voraussetzungen fehlen, "
          "keine Scheinmessung.")
    raise SystemExit(1)

z0 = zustand()
P.check("V", "Zustand antwortet und meldet das Testprofil sichtbar",
        (z0.get("ok"), z0.get("testprofil")), (True, True))


def kein_ticket_verbraucht(k: str, basis: tuple[int, int], egress_basis: int,
                           name: str) -> None:
    P.check(k, f"{name}: kein Ticket ausgegeben oder eingelöst",
            hub.zaehler(), basis)
    P.check(k, f"{name}: kein Aufruf an der Egress-Attrappe",
            egress.zaehler(), egress_basis)


P.kapitel("1", "keine Werkzeugliste im gesendeten Körper, in keiner Tiefe")
# Messsonde zuerst: der Schlüsselgänger muss eine gelegte Werkzeugliste
# finden, sonst ist „0 Treffer" keine Aussage.
probe = {"a": {"tools": []}, "b": [{"c": {"tool_choice": "auto"}}],
         "antwort": "tools im Fließtext sind kein Schlüssel"}
P.check("1", "Messsonde: Gänger findet tools/tool_choice in jeder Tiefe",
        sorted(schluessel_weg(probe, WERKZEUG_SCHLUESSEL)),
        ["tool_choice", "tools"])
basis = hub.zaehler()
ebasis = egress.zaehler()
ans1, _ = frage(f"erkläre mir {FRAGE_KANARIE} äöü")
P.check("1", "Inhaltsfrage geht an Durchgang 2 und gelingt",
        (ans1.get("ok"), ans1.get("weg"), ans1.get("durchgang"),
         ans1.get("absicht"), ans1.get("api")),
        (True, "api", 2, "api", True))
P.check("1", "genau ein Ticket ausgegeben und eingelöst",
        hub.zaehler(), (basis[0] + 1, basis[1] + 1))
P.check("1", "genau ein Aufruf an der Egress-Attrappe",
        egress.zaehler(), ebasis + 1)
koerper1 = egress.letzter_koerper()
try:
    koerper1_json = json.loads(koerper1)
except json.JSONDecodeError:
    koerper1_json = None
P.check("1", "der aufgezeichnete Körper ist lesbares JSON",
        isinstance(koerper1_json, dict), True)
P.check("1", "Körper trägt weder tools noch tool_choice, in keiner Tiefe",
        schluessel_weg(koerper1_json, WERKZEUG_SCHLUESSEL), [])
P.check("1", "Positivkontrolle: der Körper trägt die Frage (mit Umlauten)",
        FRAGE_KANARIE.encode() in koerper1
        and "äöü".encode() in koerper1, True)
P.check("1", "Positivkontrolle: der Körper trägt den Persona-Prompt",
        PERSONA_KANARIE.encode() in koerper1, True)
P.check("1", "Positivkontrolle: Antwortkanarie der Attrappe kommt an",
        ANTWORT_KANARIE in str(ans1.get("antwort", "")), True)

P.kapitel("2", "die Antwort ist ausschließlich Text")
P.check("2", "Messsonde: Gänger findet einen gelegten Aktions-Schlüssel",
        schluessel_weg({"x": {"action": 1}}, AKTIONS_SCHLUESSEL),
        ["action"])
P.check("2", "antwort ist eine Zeichenkette, keine Struktur",
        isinstance(ans1.get("antwort"), str), True)
P.check("2", "kein Feld des Ergebnisses trägt Objekt oder Liste",
        [k for k, v in ans1.items() if isinstance(v, (dict, list))], [])
P.check("2", "kein Feld trägt Aktionscharakter (action, aktion, ziel, "
        "window_ref, tool)", schluessel_weg(ans1, AKTIONS_SCHLUESSEL), [])
P.check("2", "die Antwortform trägt alle Felder des Vertrags",
        sorted({"v", "ok", "weg", "durchgang", "absicht", "antwort",
                "marke", "api", "aktionsvorschlag_erkannt"}
               - set(ans1)), [])
P.check("2", "harmlose Antwort: aktionsvorschlag_erkannt ist false",
        ans1.get("aktionsvorschlag_erkannt"), False)

P.kapitel("3", "ein Aktionsvorschlag wird verworfen, nicht durchgereicht")
hbasis = hub.log()
tbasis = hub.zaehler()
ebasis = egress.zaehler()
bbasis = beobachter_log()
egress.setze_antwort(AKTION_VORSCHLAG)
ans3, _ = frage(f"was kannst du über {FRAGE_KANARIE_2} sagen")
P.check("3", "die Frage gelingt trotz Vorschlag in der Modellantwort",
        (ans3.get("ok"), ans3.get("weg"), ans3.get("durchgang")),
        (True, "api", 2))
P.check("3", "aktionsvorschlag_erkannt ist true — verworfen heißt nicht "
        "unbemerkt", ans3.get("aktionsvorschlag_erkannt"), True)
P.check("3", "der Text selbst bleibt erhalten",
        "close_window" in str(ans3.get("antwort", "")), True)
P.check("3", "der Text bleibt tainted", ans3.get("marke"), "tainted")
neue_hub = hub.log()[len(hbasis):]
P.check("3", "der Vorschlag erreicht den Hub nicht: nur ausgeben/einloesen "
        "am Ticketbuch",
        sorted({json.loads(a).get("art") for a in neue_hub}),
        ["ausgeben", "einloesen"])
P.check("3", "der Vorschlag erreicht den Hub nicht: kein Byte von "
        "close_window am Ticketbuch",
        any("close_window" in a for a in neue_hub), False)
P.check("3", "kein Ausführungswerkzeug wurde angerührt (Beobachter leer)",
        beobachter_log(), bbasis)
P.check("3", "das Ergebnis trägt den Vorschlag nicht als Struktur",
        schluessel_weg(ans3, AKTIONS_SCHLUESSEL), [])
P.check("3", "der Vorschlag kostet genau das eine Ticket der Frage",
        (hub.zaehler(), egress.zaehler()),
        ((tbasis[0] + 1, tbasis[1] + 1), ebasis + 1))
egress.setze_antwort("Das kann ich nicht ausführen, aber ich erkläre "
                     "es gern.")
ans3b, _ = frage(f"was kannst du über {FRAGE_KANARIE_2} sagen")
P.check("3", "Positivkontrolle: harmlose Antwort setzt das Flag nicht",
        ans3b.get("aktionsvorschlag_erkannt"), False)
egress.setze_antwort("Ein Fenster wie w_1 schließe ich nicht von selbst, "
                     "auch nicht mit close_window.")
ans3c, _ = frage(f"was kannst du über {FRAGE_KANARIE_2} sagen")
P.check("3", "Positivkontrolle: Fließtext über Aktionen setzt das Flag "
        "nicht", ans3c.get("aktionsvorschlag_erkannt"), False)
egress.setze_antwort(ANTWORT_KANARIE)

P.kapitel("4", "die Antwort ist immer tainted")
P.check("4", "tainted bei marke user_ptt", ans1.get("marke"), "tainted")
P.check("4", "tainted bei Aktionsvorschlag-Antwort",
        ans3.get("marke"), "tainted")
P.check("4", "tainted bei harmloser Antwort", ans3b.get("marke"), "tainted")

P.kapitel("5", "user_audio: Durchgang 2 ja, Durchgang 1 nein — ein Lauf")
tbasis = hub.zaehler()
ebasis = egress.zaehler()
qbasis = quellen_log()
bbasis = beobachter_log()
ans5, _ = frage(f"erkläre mir {FRAGE_KANARIE}", marke="user_audio")
P.check("5", "user_audio mit Inhaltsfrage wird in Durchgang 2 beantwortet",
        (ans5.get("ok"), ans5.get("weg"), ans5.get("durchgang")),
        (True, "api", 2))
P.check("5", "und ist trotzdem tainted", ans5.get("marke"), "tainted")
P.check("5", "die Beantwortung kostet genau ein Ticket und einen Aufruf",
        (hub.zaehler(), egress.zaehler()),
        ((tbasis[0] + 1, tbasis[1] + 1), ebasis + 1))
ans5l, roh5l = frage("wie spät ist es", marke="user_audio")
P.check("5", "user_audio mit lokaler Absicht bleibt marke_verboten",
        (ans5l.get("ok"), ans5l.get("grund")), (False, "marke_verboten"))
ans5a, roh5a = frage("mach das fenster zu", marke="user_audio")
P.check("5", "user_audio mit Aktionswunsch bleibt marke_verboten",
        (ans5a.get("ok"), ans5a.get("grund")), (False, "marke_verboten"))
P.check("5", "die Senke steht vor dem Ticket: Zähler unverändert",
        hub.zaehler(), (tbasis[0] + 1, tbasis[1] + 1))
P.check("5", "die Senke steht vor dem Egress: Zähler unverändert",
        egress.zaehler(), ebasis + 1)
P.check("5", "die Senke steht vor der Quelle: Stub-Log unverändert",
        quellen_log(), qbasis)
P.check("5", "die Senke steht vor jeder Ausführung: Beobachter leer",
        beobachter_log(), bbasis)
P.check("5", "Absagen nennen keinen Nutzertext",
        kanariefrei(roh5l + roh5a, NUTZER_KANARIE, FENSTER_KANARIE), True)
ans5p, _ = frage("wie spät ist es", marke="user_ptt")
P.check("5", "Positivkontrolle: dieselbe Frage als user_ptt gelingt lokal",
        (ans5p.get("ok"), ans5p.get("weg")), (True, "lokal"))

P.kapitel("6", "der Kontext ist leer, aber vorhanden — im Nutzertext (§6)")
# Messsonden zuerst: der Blockleser muss einen gelegten Block finden und
# einen fehlenden als fehlend melden, und der Feldwächter muss ein fremdes
# Top-Level-Feld erkennen — sonst sind die Nullmessungen keine Aussagen.
probe_frage, probe_block, probe_rest = kontext_aus_inhalt(
    f"Frage {FRAGE_KANARIE}\n\n{KONTEXT_MARKE}\n"
    + json.dumps({"kontext": {"quellen": ["probe"],
                              "deklassifiziert": []}}))
P.check("6", "Messsonde: Blockleser findet den abgesetzten Kontextblock",
        ((probe_frage or "").strip(),
         (probe_block or {}).get("kontext", {}).get("quellen"), probe_rest),
        (f"Frage {FRAGE_KANARIE}", ["probe"], ""))
P.check("6", "Messsonde: ohne Marke meldet der Leser keinen Kontext",
        kontext_aus_inhalt(f"Frage {FRAGE_KANARIE} ohne Block"),
        (None, None, ""))
# Die Sonde, die am 31.08. gefehlt hat: hinter dem JSON steht seit T-3.9 ein
# weiterer Block. Ein Leser, der daran scheitert, meldet "kein Kontext" --
# und das sah fuenf Laeufe lang wie ein Befund am Pruefling aus.
_, sonde_block, sonde_rest = kontext_aus_inhalt(
    f"Frage\n\n{KONTEXT_MARKE}\n"
    + json.dumps({"kontext": {"quellen": [], "deklassifiziert": []}})
    + "\n\n[Ausgabeform]\nEine Zeile.")
P.check("6", "Messsonde: ein Block HINTER dem JSON verdeckt den Kontext "
        "nicht mehr",
        ((sonde_block or {}).get("kontext"), sonde_rest.strip()),
        ({"quellen": [], "deklassifiziert": []}, "[Ausgabeform]\nEine Zeile."))
P.check("6", "Messsonde: Feldwächter erkennt kontext als fremdes "
        "Top-Level-Feld",
        sorted(set({"model": "m", "messages": [], "kontext": {}})
               - MESSAGES_TOPLEVEL), ["kontext"])
# WARUM dieses Kriterium heute anders MISST als bis zum 31.08.
#
# Bis dahin standen hier fuenf Erwartungen, und alle fuenf waren rot:
#
#   die Kontextstruktur steht als abgesetzter Block   (erwartet True, war False)
#   kontext.quellen steht und ist leer                (erwartet [],   war None)
#   kontext.deklassifiziert steht und ist leer        (erwartet [],   war None)
#   die Frage steht vor dem Block                     (erwartet True, war False)
#   keine Historie im Koerper                         (erwartet False, war True)
#
# Ob das ein alter Pruefstand oder ein Befund am Pruefling war, hatte
# niemand gemessen -- `erwartet [], war None` kann beides heissen. Gemessen
# am 01.09. am echten Mind ueber echte Sockets, roher Koerper der letzten
# API-Frage (gekuerzt):
#
#   {"max_tokens": 1024, "model": "...", "system": "<Persona>",
#    "messages": [ ...vier Vorrunden als eigene Nachrichten... ,
#      {"role": "user", "content":
#       "sag mir etwas ueber <Frage>\n\n[Referenzen, keine Inhalte]\n"
#       "{\"kontext\": {\"deklassifiziert\": [], \"quellen\": []}}\n\n"
#       "[Ausgabeform]\nDiese Antwort wird VORGELESEN. ..."}]}
#
# Damit ist die Sache entschieden, und zwar zweimal VERSCHIEDEN:
#
#  * Die ersten VIER waren ein Fehler dieses PRUEFSTANDS, kein Befund am
#    Pruefling. Die Zusage aus §6 haelt woertlich -- der Block steht da, mit
#    `quellen: []` und `deklassifiziert: []`, die Frage davor, und kein
#    `kontext` neben `messages`. Der Leser sah ihn nur nicht: er suchte in
#    `messages[0]`, und das ist seit T-6.2 die AELTESTE Runde statt der
#    aktuellen; und `json.loads` scheiterte am Ausgabeform-Block, den T-3.9
#    dahinter haengt. Beides ist am Leser repariert (`kontext_aus_inhalt`),
#    die Erwartungen selbst bleiben Wort fuer Wort dieselben.
#  * Die FUENFTE war eine Zusage MIT DATUM. "Keine Historie" galt, solange
#    es kein Kurzzeitgedaechtnis gab; seit T-6.2 traegt der Koerper den
#    Verlauf absichtlich. Sie wird nicht gestrichen und nicht abgeschwaecht,
#    sondern durch das ersetzt, was heute die teure Zusage ist: der Verlauf
#    steht als eigene NACHRICHTEN und nicht im Nutzertext; nur die aktuelle
#    Aeusserung traegt einen Kontextblock; und -- die Gegenprobe, die den
#    Schnitt wirklich bindet -- die Antwort aus Durchgang 2 erreicht den
#    werkzeugfaehigen Durchgang 1 nie. Die steht in K9, wo der Werkzeugweg
#    laeuft.
egress.setze_antwort(D2_ANTWORT_KANARIE)
frage(f"erkläre mir {HISTORIE_KANARIE}")
egress.setze_antwort(ANTWORT_KANARIE)
ans6, _ = frage(f"sag mir etwas über {FRAGE_KANARIE_2}")
koerper6 = egress.letzter_koerper()
try:
    koerper6_json = json.loads(koerper6)
    if not isinstance(koerper6_json, dict):
        koerper6_json = None
except json.JSONDecodeError:
    koerper6_json = None
P.check("6", "der Körper trägt ausschließlich Top-Level-Felder der "
        "Messages-API (kein kontext neben messages)",
        (sorted(set(koerper6_json) - MESSAGES_TOPLEVEL)
         if koerper6_json is not None else ["<kein lesbarer JSON-Körper>"]),
        [])
nachrichten6 = (koerper6_json or {}).get("messages")
P.check("6", "messages ist eine nichtleere Liste",
        isinstance(nachrichten6, list) and bool(nachrichten6), True)
nachrichten6 = nachrichten6 if isinstance(nachrichten6, list) else []
P.check("6", "jede Nachricht trägt eine Rolle aus {user, assistant} und "
        "content als Zeichenkette",
        [i for i, m in enumerate(nachrichten6)
         if not isinstance(m, dict)
         or m.get("role") not in ("user", "assistant")
         or not isinstance(m.get("content"), str)], [])
letzte6 = nachrichten6[-1] if nachrichten6 else {}
inhalt6 = letzte6.get("content") if isinstance(letzte6, dict) else None
P.check("6", "die aktuelle Äußerung ist die LETZTE Nachricht und hat die "
        "Rolle user",
        (letzte6.get("role") if isinstance(letzte6, dict) else None,
         isinstance(inhalt6, str)), ("user", True))
frage6, block6, rest6 = kontext_aus_inhalt(inhalt6)
kontext6 = (block6 or {}).get("kontext")
P.check("6", "die Kontextstruktur steht als abgesetzter Block im Nutzertext",
        isinstance(kontext6, dict), True)
P.check("6", "kontext.quellen steht und ist leer",
        (kontext6 or {}).get("quellen"), [])
P.check("6", "kontext.deklassifiziert steht und ist leer",
        (kontext6 or {}).get("deklassifiziert"), [])
P.check("6", "die Frage steht vor dem Block (angehängt, nicht ersetzt)",
        FRAGE_KANARIE_2 in (frage6 or ""), True)
# Hinter dem Kontextblock darf genau eines stehen: der Ausgabeform-Block aus
# T-3.9. Alles andere waere ein dritter, ungeprueffter Anbau am Nutzertext.
P.check("6", "hinter dem Kontextblock steht nichts außer der Ausgabeform "
        "(T-3.9)",
        rest6.strip() == "" or rest6.strip().startswith("[Ausgabeform]"), True)
P.check("6", "nur die aktuelle Äußerung trägt einen Kontextblock, keine "
        "Vorrunde",
        [i for i, m in enumerate(nachrichten6[:-1])
         if KONTEXT_MARKE in str(m.get("content", ""))], [])
# T-6.2: die Historie IST da, und das ist die Zusage -- nicht ihre Duldung.
P.check("6", "die Frage der Vorrunde steht im Verlauf (T-6.2)",
        any(HISTORIE_KANARIE in str(m.get("content", ""))
            for m in nachrichten6[:-1]), True)
P.check("6", "und sie steht dort als eigene Nachricht, nicht im Nutzertext "
        "dieser Runde", HISTORIE_KANARIE in str(inhalt6 or ""), False)
P.check("6", "die Antwort der Vorrunde steht genau einmal, und als "
        "assistant-Nachricht",
        [m.get("role") for m in nachrichten6
         if D2_ANTWORT_KANARIE in str(m.get("content", ""))], ["assistant"])
P.check("6", "kein Fenstertitel im Körper",
        FENSTER_KANARIE.encode() in koerper6, False)
P.check("6", "kein Hub-Zustand im Körper (Sitzungs- und Projekt-Kanarie "
        "fehlen)",
        kanariefrei(koerper6, SITZUNG_KANARIE, PROJEKT_KANARIE), True)
P.check("6", "Positivkontrolle: die eigene Frage steht im Körper",
        FRAGE_KANARIE_2.encode() in koerper6, True)

P.kapitel("7", "kein API-Aufruf ohne Kontingent — gezählt am Ticketbuch")
hub.kein_ticket = True
ebasis = egress.zaehler()
ans7, roh7 = frage(f"sag mir etwas über {NUTZER_KANARIE}")
P.check("7", "ohne Ticket des Hubs: kein_kontingent",
        (ans7.get("ok"), ans7.get("grund")), (False, "kein_kontingent"))
P.check("7", "der Egress-Zähler bleibt ohne Ticket stehen",
        egress.zaehler(), ebasis)
P.check("7", "Absage nennt weder Nutzertext noch Körper",
        kanariefrei(roh7, NUTZER_KANARIE, PERSONA_KANARIE), True)
hub.kein_ticket = False
ans7b, _ = frage(f"sag mir etwas über {NUTZER_KANARIE}")
P.check("7", "Positivkontrolle: mit Ticket steigt der Egress-Zähler",
        (ans7b.get("ok"), egress.zaehler()), (True, ebasis + 1))

P.kapitel("8", "ein API-Fehler wird sprechbar")
egress.modus = "absage"
ans8, roh8 = frage(f"erkläre mir {NUTZER_KANARIE}")
P.check("8", "Egress-Absage: ok falsch, weg api, egress_weg",
        (ans8.get("ok"), ans8.get("weg"), ans8.get("grund")),
        (False, "api", "egress_weg"))
P.check("8", "Fehlermeldung nennt weder Nutzertext noch Körper",
        kanariefrei(roh8, NUTZER_KANARIE, FRAGE_KANARIE, PERSONA_KANARIE),
        True)
egress.modus = "normal"
ans8b, _ = frage("erkläre mir etwas über züge")
P.check("8", "Positivkontrolle: nach der Absage gelingt die API wieder",
        ans8b.get("ok"), True)
egress.stop()
ans8c, roh8c = frage(f"erkläre mir noch etwas über {NUTZER_KANARIE}")
P.check("8", "unerreichbarer Egress: ok falsch, weg api, egress_weg",
        (ans8c.get("ok"), ans8c.get("weg"), ans8c.get("grund")),
        (False, "api", "egress_weg"))
P.check("8", "Fehlermeldung ohne Nutzertext auch bei totem Egress",
        kanariefrei(roh8c, NUTZER_KANARIE, PERSONA_KANARIE), True)
egress.start()
P.check("8", "Egress-Attrappe ist wieder da", warten(egress.pfad), True)
ans8d, _ = frage("erkläre mir etwas über schiffe")
P.check("8", "Positivkontrolle: nach dem Neustart gelingt die API wieder",
        ans8d.get("ok"), True)

P.kapitel("9", "Durchgang 1 bleibt unberührt — die lokalen Auskünfte, "
          "und der Aktionsweg, wie er heute läuft")
basis = hub.zaehler()
ebasis = egress.zaehler()
bbasis = beobachter_log()
abasis = aktion.zaehler()
ans, _ = frage("wie spät ist es")
P.check("9", "uhrzeit bleibt lokal und trusted",
        (ans.get("ok"), ans.get("weg"), ans.get("absicht"),
         ans.get("marke"), ans.get("api")),
        (True, "lokal", "uhrzeit", "trusted", False))
P.check("9", "uhrzeit trägt eine Uhrzeit (Positivkontrolle)",
        bool(re.search(r"\d{1,2}:\d{2}", str(ans.get("antwort", "")))),
        True)
ans, _ = frage("wie laut ist es gerade")
P.check("9", "lautstaerke bleibt lokal und trusted",
        (ans.get("ok"), ans.get("weg"), ans.get("absicht"),
         ans.get("marke"), ans.get("api")),
        (True, "lokal", "lautstaerke", "trusted", False))
P.check("9", "lautstaerke trägt den Attrappenwert 42 (Positivkontrolle)",
        "42" in str(ans.get("antwort", "")), True)
ans, _ = frage("welche sitzungen sind aktiv")
P.check("9", "sitzung bleibt lokal und trusted",
        (ans.get("ok"), ans.get("weg"), ans.get("absicht"),
         ans.get("marke"), ans.get("api")),
        (True, "lokal", "sitzung", "trusted", False))
P.check("9", "sitzung trägt den Hub-Zustand (Positivkontrolle)",
        SITZUNG_KANARIE in str(ans.get("antwort", "")), True)
P.check("9", "Projektname (tainted) fehlt in der trusted-Sitzungsauskunft",
        PROJEKT_KANARIE in str(ans.get("antwort", "")), False)
ans, _ = frage("welche fenster sind offen")
P.check("9", "fensterliste bleibt lokal und tainted",
        (ans.get("ok"), ans.get("weg"), ans.get("absicht"),
         ans.get("marke"), ans.get("api")),
        (True, "lokal", "fensterliste", "tainted", False))
P.check("9", "fensterliste trägt den Kanarientitel (Positivkontrolle)",
        FENSTER_KANARIE in str(ans.get("antwort", "")), True)
P.check("9", "kein Ausführungswerkzeug wurde angerührt (Beobachter leer)",
        beobachter_log(), bbasis)
kein_ticket_verbraucht("9", basis, ebasis, "die vier lokalen Auskünfte")
P.check("9", "die vier lokalen Auskünfte rühren aktion.sock nicht an",
        aktion.zaehler(), abasis)

# --- Der Aktionsweg ------------------------------------------------------
#
# WARUM diese Prüfung heute anders lautet als bis zum 31.08.
#
# Bis dahin stand hier EINE Erwartung: (ok, weg, absicht, api) ==
# (True, "abgelehnt", "aktion", False), dazu "Durchgang 1 gesamt: kein
# Ticket ausgegeben oder eingelöst" über alle fünf Äußerungen. Das war die
# Zusage der PHASE 3 — "Sprache, Egress, Markierungsverfolgung, KEINE
# Aktionen". Sie galt, solange es keinen werkzeugfähigen Weg gab.
#
# Seit T-4.16 K1 gibt es ihn: der Router wählt für einen Aktionswunsch mit
# Absichtsmarke und benanntem Ziel `weg: "aktion"` mit `api: true`, und ein
# `tool_use`-Block des Modells geht über `aktion.sock` an den Koordinator.
# Die alte Erwartung ist heute nicht mehr die Zusage, sondern ihr Nachhall:
# gemessen am 31.08. im eingebetteten Lauf `(True, 'aktion', ...)` statt
# `(True, 'abgelehnt', ...)` und `(13, 12)` statt `(12, 11)` — drei rote
# Punkte, ohne dass etwas kaputt wäre. CLAUDE.md, "Ein eingefrorener
# Prüfstand ist eine Zusage MIT DATUM", trägt die Bauform.
#
# Aufgeweicht wird dabei NICHTS. Aus einer Erwartung werden vier Familien,
# und die teure Zusage — dass eine Ablehnung nichts kostet und dass keine
# Aktion entsteht, solange das Modell kein Werkzeug ruft — wird hier zum
# ersten Mal GEMESSEN statt vorausgesetzt. Am echten Mind über echte
# Sockets (Reviewer-Attrappen für Hub, Modell und Koordinator), 01.09.:
#
#   tainted    + "mach das fenster zu" -> ok:false, marke_verboten, ohne Zeile
#   user_audio + dasselbe              -> ok:false, marke_verboten + Zeile
#   trusted    + dasselbe              -> ok:true,  weg "abgelehnt", Zeile
#   user_ptt   + "mach das"            -> ok:true,  weg "rueckfrage"
#   user_ptt   + "mach das fenster zu" -> ok:true,  weg "aktion"
#   Summe über die fünf: 1 Ticket, 1 Modellaufruf, 0 Aufrufe an aktion.sock.
#
# Der Weg heißt `aktion`, weil er werkzeugFÄHIG ist — die Aktion entsteht
# erst, wenn das Modell ein Werkzeug ruft. Katalog, Policy und Consent
# liegen dahinter, in der Kette, nicht hier.

# (a) Ohne Absichtsmarke fällt der Wunsch an der Senke, und zwar
# kostenfrei: werkzeuglos, ohne Ticket, ohne Modellaufruf, ohne Aufruf am
# Koordinator.
for marke9 in ("tainted", "user_audio"):
    tb9, eb9, ab9 = hub.zaehler(), egress.zaehler(), aktion.zaehler()
    bb9 = beobachter_log()
    ans, roh9 = frage("mach das fenster zu", marke=marke9)
    P.check("9", f"{marke9}: der Aktionswunsch fällt an der Senke",
            (ans.get("ok"), ans.get("grund")), (False, "marke_verboten"))
    P.check("9", f"{marke9}: kostet kein Ticket, keinen Modellaufruf, "
            f"keinen Aufruf am Koordinator",
            (hub.zaehler(), egress.zaehler(), aktion.zaehler()),
            (tb9, eb9, ab9))
    P.check("9", f"{marke9}: kein Ausführungswerkzeug wurde angerührt",
            beobachter_log(), bb9)
    P.check("9", f"{marke9}: die Absage nennt weder Fenstertitel noch "
            f"Nutzertext", kanariefrei(roh9, FENSTER_KANARIE, NUTZER_KANARIE),
            True)
# T-4.19: nur wer wirklich gesprochen hat, erfährt WARUM — und die
# Rückmeldung ist die kuratierte Vorlage, nicht Material aus der Äußerung.
ans9a, _ = frage("mach das fenster zu", marke="user_audio")
P.check("9", "user_audio bekommt den Absichtsmarken-Hinweis, trusted",
        (ans9a.get("antwort"), ans9a.get("marke")),
        (ABSICHTSMARKE_HINWEIS, "trusted"))
ans9b, _ = frage("mach das fenster zu", marke="tainted")
P.check("9", "tainted bekommt ihn NICHT — injiziertem Text sagt niemand, "
        "wie er eskaliert", ans9b.get("antwort"), None)

# (b) `trusted` kommt an der Senke vorbei — eine Systemzeile ist kein
# injizierter Text. Sie ist aber auch keine ABSICHT: ein Tastendruck belegt,
# dass jemand etwas wollte, und den hat hier niemand getan. Dies ist die
# Prüfung, die den Riegel `marke != "user_ptt"` bindet; die beiden Fälle
# oben fallen schon vorher an der Senke.
tb9, eb9, ab9 = hub.zaehler(), egress.zaehler(), aktion.zaehler()
bb9 = beobachter_log()
ans9t, _ = frage("mach das fenster zu", marke="trusted")
P.check("9", "trusted: ohne Absichtsmarke werkzeuglos abgelehnt",
        (ans9t.get("ok"), ans9t.get("weg"), ans9t.get("absicht"),
         ans9t.get("api")), (True, "abgelehnt", "aktion", False))
P.check("9", "trusted: bekommt die kuratierte Vorlage, trusted",
        (ans9t.get("antwort"), ans9t.get("marke")),
        (ABSICHTSMARKE_HINWEIS, "trusted"))
P.check("9", "trusted: kostet kein Ticket, keinen Modellaufruf, keinen "
        "Aufruf am Koordinator",
        (hub.zaehler(), egress.zaehler(), aktion.zaehler()),
        (tb9, eb9, ab9))
P.check("9", "trusted: kein Ausführungswerkzeug wurde angerührt",
        beobachter_log(), bb9)

# (c) Mit Marke, ohne benanntes Ziel: Rückfrage, kostenfrei. Ein Fürwort
# wird NICHT aus dem Kontext aufgelöst (Design 5.2) — sonst hinge die
# Aktion an etwas, das der Nutzer in dieser Runde nie gesagt hat.
tb9, eb9, ab9 = hub.zaehler(), egress.zaehler(), aktion.zaehler()
bb9 = beobachter_log()
ans9c, _ = frage("mach das")
P.check("9", "ohne Ziel: weg ist rueckfrage, absicht bleibt aktion",
        (ans9c.get("weg"), ans9c.get("absicht")), ("rueckfrage", "aktion"))
P.check("9", "ohne Ziel: die Antwortform des Vertrags, Wort für Wort",
        ans9c.get("antwort"), RUECKFRAGE_AKTION)
P.check("9", "ohne Ziel: ok true, marke trusted, api false",
        (ans9c.get("ok"), ans9c.get("marke"), ans9c.get("api")),
        (True, "trusted", False))
P.check("9", "Rückfrage: kostet kein Ticket, keinen Modellaufruf, keinen "
        "Aufruf am Koordinator",
        (hub.zaehler(), egress.zaehler(), aktion.zaehler()),
        (tb9, eb9, ab9))
P.check("9", "Rückfrage: kein Ausführungswerkzeug wurde angerührt",
        beobachter_log(), bb9)

# (d) Mit Marke UND Ziel: der werkzeugfähige Weg. Je Äußerung genau ein
# Ticket und genau ein Modellaufruf — nicht mehr, und kein zweiter Versuch.
bb9 = beobachter_log()
qb9 = quellen_log()
ab9 = aktion.zaehler()
for text9 in ("mach das fenster zu", "stell die lautstärke auf 30",
              f"mach das fenster »{NUTZER_KANARIE} äöü« zu"):
    tb9, eb9 = hub.zaehler(), egress.zaehler()
    ans, roh9 = frage(text9)
    P.check("9", f"Aktionswunsch geht den Werkzeugweg: {text9[:38]!r}",
            (ans.get("ok"), ans.get("weg"), ans.get("absicht"),
             ans.get("api")), (True, "aktion", "aktion", True))
    P.check("9", f"und kostet genau ein Ticket und einen Modellaufruf: "
            f"{text9[:38]!r}", (hub.zaehler(), egress.zaehler()),
            ((tb9[0] + 1, tb9[1] + 1), eb9 + 1))
    P.check("9", f"und nennt im Ergebnis keinen Fenstertitel: {text9[:38]!r}",
            kanariefrei(roh9, FENSTER_KANARIE), True)
P.check("9", "der Aktionsweg rührt kein Ausführungswerkzeug an "
        "(Beobachter leer)", beobachter_log(), bb9)
neue_quellen9 = quellen_log()[len(qb9):]
P.check("9", "keine verändernde Quellenbedienung hinter dem Aktionsweg",
        [z for z in neue_quellen9
         if "set-volume" in z or " Run" in z or "close" in z.lower()], [])
P.check("9", "Positivkontrolle: die freie Modellausgabe kommt an und bleibt "
        "tainted",
        (ANTWORT_KANARIE in str(ans.get("antwort", "")), ans.get("marke")),
        (True, "tainted"))

# (e) Die eigene Zusage: solange das Modell KEIN Werkzeug ruft, sieht der
# Koordinator nichts. `aktion.sock` ist gebunden und hat in K V geantwortet
# — diese Null ist gemessen, nicht geerbt.
P.check("9", "acht Aktionswünsche, kein Werkzeugruf: aktion.sock bekam "
        "keinen Aufruf", aktion.zaehler(), ab9)
# Positivkontrolle: dieselbe Bauform, aber das Modell ruft ein Werkzeug aus
# dem freigegebenen Katalog. Jetzt MUSS genau ein Aufruf ankommen — sonst
# wäre die Null oben die Null einer kaputten Vorrichtung.
egress.setze_bloecke([{"type": "tool_use", "id": "tu-t313",
                       "name": WERKZEUG_NAME, "input": {}}])
ans9d, _ = frage("mach die musik an")
P.check("9", "Positivkontrolle: mit tool_use sieht aktion.sock genau einen "
        "Aufruf", aktion.zaehler(), ab9 + 1)
P.check("9", "Positivkontrolle: der Koordinator bekommt die action_id aus "
        "dem Katalog, nicht den Werkzeugnamen",
        (aktion.letzte().get("art"), aktion.letzte().get("action_id")),
        ("ausfuehren", WERKZEUG_ACTION_ID))
P.check("9", "Positivkontrolle: der Router meldet das Verdikt des "
        "Koordinators, nicht sein eigenes",
        (ans9d.get("weg"), ans9d.get("action_id"), ans9d.get("ausgefuehrt")),
        ("aktion", WERKZEUG_ACTION_ID, False))
# (f) Und die Gegenprobe zur Positivkontrolle: ein erfundener Werkzeugname
# fuehrt zu einer Textantwort, nicht zu einem Rateversuch. Sonst waere
# „genau ein Aufruf" mit „jeder Name kommt durch" verwechselbar.
egress.setze_bloecke([{"type": "tool_use", "id": "tu-t313b",
                       "name": "erfundenes_werkzeug", "input": {}}])
ans9e, _ = frage("mach die musik an")
P.check("9", "erfundener Werkzeugname: aktion.sock bekommt keinen Aufruf",
        aktion.zaehler(), ab9 + 1)
P.check("9", "erfundener Werkzeugname: keine action_id im Ergebnis",
        ans9e.get("action_id"), None)
egress.setze_bloecke(None)

# (g) Der Schnitt, der die alte K6-Zeile „keine Historie im Körper"
# ersetzt. Historie gibt es seit T-6.2 — aber NICHT jede. Die Antwort aus
# Durchgang 2 ist Modellausgabe; sie trägt die Herkunft `durchgang2` und
# wird darüber erzwungen-`tainted`, und die Senke `kurzzeitgedaechtnis`
# nimmt kein `tainted`. Damit erreicht sie den werkzeugfähigen Durchgang
# nie — auch nicht über das Gedächtnis. Gemessen am 01.09.: die eigene
# `user_ptt`-Frage steht im Werkzeugkörper, die Antwort darauf nicht.
egress.setze_antwort(D2_ANTWORT_KANARIE)
frage(f"erzähl mir etwas über {D2_FRAGE_KANARIE}")
egress.setze_antwort(ANTWORT_KANARIE)
frage("mach das fenster zu")
koerper9 = egress.letzter_koerper()
P.check("9", "der werkzeugfähige Körper trägt die Antwort aus Durchgang 2 "
        "nicht", D2_ANTWORT_KANARIE.encode() in koerper9, False)
P.check("9", "Positivkontrolle: die eigene user_ptt-Frage der Vorrunde "
        "steht sehr wohl darin — die Null oben ist gefiltert, nicht leer",
        D2_FRAGE_KANARIE.encode() in koerper9, True)

P.kapitel("10", "eingefrorene Prüfstände bleiben grün, dazu pytest")
if FIXTURE:
    P.info("Fixture/Mutanten sind keine vollständigen Arbeitsbäume; "
           "K10 läuft nur im Arbeitsbaumlauf.")
else:
    kind_env = os.environ.copy()
    kind_env.pop("DAIMON_FIXTURE", None)
    for task in ("T-3.9", "T-3.8", "T-3.10", "T-3.11", "T-3.12"):
        rc = subprocess.run([str(REPO / "tests/verify" / f"{task}.sh")],
                            cwd=REPO, env=kind_env).returncode
        P.check("10", f"{task} bleibt einzeln vollständig grün", rc, 0)
        # Aufräumphase: zwei Prüfstände nie Rücken an Rücken.
        subprocess.run(["systemctl", "--user", "reset-failed"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    # --tb=no: ein roter Test darf dem Reviewer keinen Quelltext zeigen.
    r = subprocess.run([str(PYTHON), "-B", "-P", "-m", "pytest", "-q",
                        "--tb=no"],
                       cwd=REPO, env=kind_env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=900)
    P.check("10", "pytest ist im Arbeitsbaum grün", r.returncode, 0)

print("\n=== Ergebnis je Kriterium ===")
for k in [str(x) for x in range(1, 11)]:
    print(f"  K{k}: {P.n[k]} Prüfungen, {P.rot[k]} rot")
print(f"  Voraussetzungen: {P.n['V']} Prüfungen, {P.rot['V']} rot")
gesamt = sum(P.n.values())
rot = sum(P.rot.values())
print(f"T-3.13: {gesamt} Prüfungen, {rot} rot")
raise SystemExit(1 if P.fail else 0)
