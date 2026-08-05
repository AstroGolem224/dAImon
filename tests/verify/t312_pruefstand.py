#!/usr/bin/env python3
"""Blinder Laufzeit-Prüfstand für T-3.12 (Routing, Durchgang 1).

Gebaut ausschließlich aus dem Vertrag (T-3.12-Router-Plan.md §2/§3/§8). Die
Implementierung wurde nie gelesen. Kein Aufruf verlässt die Maschine: Hub-
Ticketbuch, Hub-Zustand, Egress und die lokalen Quellen (wpctl, KWin) sind
Attrappen des Reviewers. Negative Aussagen bekommen im selben Lauf eine
Positivkontrolle am gleichen Messpunkt. `api` wird an den eingelösten
Tickets und den Egress-Aufrufen gemessen, nicht an der Selbstauskunft.
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

FENSTER_KANARIE_A = "T312-FENSTER-KANARIE-A-4f9c2e"
FENSTER_KANARIE_B = "T312-FENSTER-KANARIE-B-81bd07"
SITZUNG_KANARIE = "T312-SITZUNG-KANARIE-5c31aa"
PROJEKT_KANARIE = "T312-PROJEKT-KANARIE-7d0e3b"
ANTWORT_KANARIE = "T312-ANTWORT-KANARIE-6b80fe"
NUTZER_KANARIE = "T312-NUTZER-KANARIE-2e94d1"


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
            print(f"  FAIL [{k}] {name} (erwartet {soll!r}, war {ist!r})", flush=True)
            self.rot[k] += 1
            self.fail = True

    def info(self, text: str) -> None:
        print(f"  INFO {text}", flush=True)

    def kapitel(self, k: str, text: str) -> None:
        print(f"\n--- K{k}: {text} ---", flush=True)


P = Pruefstand()
laufzeit_basis = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "daimon"
laufzeit_basis.mkdir(parents=True, exist_ok=True)
RT = Path(tempfile.mkdtemp(prefix="t312-", dir=laufzeit_basis))
CONFIG = RT / "config"
STATE = RT / "state"
RUNTIME = RT / "runtime"
DAIMON_RT = RUNTIME / "daimon"
QUELLEN = RT / "quellen"
BEOBACHTER = RT / "beobachter"
for d in (CONFIG / "daimon", STATE / "daimon", DAIMON_RT, QUELLEN, BEOBACHTER):
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


def warten(pfad: Path, prozess: subprocess.Popen | None = None, sekunden: float = 8) -> bool:
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
    roh = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    antwort = unix_roh(pfad, roh)
    try:
        return json.loads(antwort), antwort
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, antwort


# ---------------------------------------------------------------- Attrappen


class HubAttrappe:
    """Das Ticketbuch und der Zustand des Reviewers. Zählt Ausgaben und
    Einlösungen selbst — die Selbstauskunft des Prüflings wird gegen diese
    Zählung gehalten."""

    def __init__(self, rt: Path) -> None:
        self.rt = rt
        self.ausgegeben = 0
        self.eingeloest = 0
        self.kein_ticket = False
        self.state_weg = False
        self.tickets: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._stopp = threading.Event()
        self._server: list[socket.socket] = []
        for name, behandler in (("ticket.sock", self._ticket), ("state.sock", self._state)):
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            pfad = rt / name
            pfad.unlink(missing_ok=True)
            srv.bind(str(pfad))
            pfad.chmod(0o600)
            srv.listen(16)
            srv.settimeout(0.3)
            self._server.append(srv)
            threading.Thread(target=self._schleife, args=(srv, behandler), daemon=True).start()

    def _schleife(self, srv: socket.socket, behandler) -> None:
        while not self._stopp.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=behandler, args=(conn,), daemon=True).start()

    def _ticket(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(5)
                req = json.loads(conn.makefile("rb").readline(1 << 20))
                with self.lock:
                    if req.get("art") == "ausgeben" and not self.kein_ticket:
                        tid = secrets.token_hex(16)
                        self.tickets[tid] = {"hash": req.get("auftrag_hash"),
                                             "verbraucht": False}
                        self.ausgegeben += 1
                        ans = {"v": 1, "ok": True, "ticket": tid, "frist_s": 300}
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
                conn.sendall(json.dumps(ans, separators=(",", ":")).encode() + b"\n")
            except OSError:
                pass

    def _state(self, conn: socket.socket) -> None:
        with conn:
            if self.state_weg:
                return
            # Vertrag §8: die Kanarie liegt in der opaken session_id (trusted-
            # fähig). Der Projektname bleibt als zweite Kanarie im tainted-
            # Feld focus.project und darf die trusted-Auskunft nie erreichen.
            snap = {"v": 2, "rev": 7, "mood": "working", "sessions": 1,
                    "focus": {"session_id": SITZUNG_KANARIE,
                              "project": PROJEKT_KANARIE},
                    "bubble": None,
                    "voice": {"state": "idle", "listening": False, "tts_active": False},
                    "perception": {"ears": False, "eyes": False, "gpu_loaded": []}}
            try:
                conn.sendall(json.dumps(snap, separators=(",", ":")).encode() + b"\n")
            except OSError:
                pass

    def zaehler(self) -> tuple[int, int]:
        with self.lock:
            return self.ausgegeben, self.eingeloest

    def zuruecksetzen(self) -> None:
        with self.lock:
            self.ausgegeben = 0
            self.eingeloest = 0
            self.tickets = {}


class EgressAttrappe:
    """Der Egress des Reviewers: löst Tickets am eigenen Ticketbuch ein,
    zeichnet jeden Körper auf und kann auf Knopf absagen oder weg sein."""

    def __init__(self, rt: Path, hub: HubAttrappe) -> None:
        self.rt = rt
        self.hub = hub
        self.pfad = rt / "egress.sock"
        self.modus = "normal"  # normal | absage
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
            threading.Thread(target=self._bediene, args=(conn,), daemon=True).start()

    def _bediene(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(10)
                zeile = conn.makefile("rb").readline(4 << 20)
                req = json.loads(zeile)
                if req.get("art") != "anfrage":
                    ans: dict = {"v": 1, "ok": False, "grund": "unbekannte_art"}
                else:
                    ans = self._anfrage(req)
            except Exception:
                ans = {"v": 1, "ok": False, "grund": "unlesbar"}
            try:
                conn.sendall(json.dumps(ans, ensure_ascii=False,
                                        separators=(",", ":")).encode() + b"\n")
            except OSError:
                pass

    def _anfrage(self, req: dict) -> dict:
        koerper = req.get("koerper")
        if not isinstance(koerper, dict):
            return {"v": 1, "ok": False, "grund": "kein_koerper"}
        kanonisch = json.dumps(koerper, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        with self.lock:
            self.aufrufe.append(kanonisch)
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
                   "content": [{"type": "text", "text": ANTWORT_KANARIE}],
                   "stop_reason": "end_turn"}
        return {"v": 1, "ok": True, "status": 200, "bytes": 80,
                "dauer_ms": 1.0, "antwort": antwort}

    def zaehler(self) -> int:
        with self.lock:
            return len(self.aufrufe)


# ------------------------------------------------------- Quellen-Attrappen


def schreibe_stub(pfad: Path, name: str) -> None:
    """Ein Stub, der seinen Aufruf protokolliert und sonst nichts tut —
    außer den beiden lesenden Quellenantworten, die der Vertrag kennt."""
    rumpf = f"""#!/usr/bin/env bash
# T-3.12-Quellenattrappe "{name}" — protokolliert, veraendert nichts.
LOG="{QUELLEN}/aufrufe.log"
printf '%s %s\\n' "{name}" "$*" >> "$LOG"
case "{name}" in
  wpctl)
    [[ -f "{QUELLEN}/AUSFALL_WPCTL" ]] && exit 1
    if [[ " $* " == *" get-volume "* ]]; then
      if [[ -f "{QUELLEN}/STUMM" ]]; then
        echo "Volume: 0.42 [MUTED]"
      else
        echo "Volume: 0.42"
      fi
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
    Werkzeuge am 05.08. am lebenden KWin gezeigt haben. Icon-Rohdaten werden
    weggelassen — sie tragen keine Aussage des Vertrags."""
    (QUELLEN / "fenster.json").write_text(json.dumps(
        [{"kennung": k, "titel": t} for k, t in fenster], ensure_ascii=False),
        encoding="utf-8")
    teile = [
        f'[Argument: (sssida{{sv}}) "{k}", "{t}", "", 100, 0.8, '
        f'[Argument: a{{sv}} {{}}]]' for k, t in fenster]
    (QUELLEN / "fenster.qdbus").write_text(
        "[Argument: a(sssida{sv}) {" + ", ".join(teile) + "}]\n", encoding="utf-8")
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


FENSTER_A = [("0_{aaaa1111-2222-3333-4444-555566667777}",
              f"{FENSTER_KANARIE_A} — Discord"),
             ("1_{bbbb2222-3333-4444-5555-666677778888}",
              "projekt — Konsole")]
FENSTER_B = [("2_{cccc3333-4444-5555-6666-777788889999}",
              f"{FENSTER_KANARIE_B} — Firefox")]

for name in ("wpctl", "pactl", "qdbus", "qdbus6", "dbus-send", "gdbus"):
    schreibe_stub(QUELLEN / name, name)
setze_fenster(FENSTER_A)

for name in ("systemctl", "kdotool", "xdotool", "wmctrl", "t312-werkzeug"):
    stub = BEOBACHTER / name
    stub.write_text(f"""#!/usr/bin/env bash
# T-3.12-Beobachter "{name}" — protokolliert Argumente, tut nichts.
printf '%s %s\\n' "{name}" "$*" >> "{BEOBACHTER}/aufrufe.log"
exit 0
""", encoding="utf-8")
    stub.chmod(0o700)


# ------------------------------------------------------------------ Mind


def unit_execstart(pfad: Path) -> list[str]:
    text = pfad.read_text(encoding="utf-8")
    text = re.sub(r"\\\s*\n\s*", " ", text)
    treffer = re.search(r"(?m)^ExecStart=(.+)$", text)
    if not treffer:
        return []
    argv = shlex.split(treffer.group(1))
    return [a.replace(str(REPO), str(TARGET)) for a in argv]


MIND_UNIT = TARGET / "config/systemd/daimon-mind.service"
MIND_CMD = unit_execstart(MIND_UNIT) if MIND_UNIT.is_file() else []


def mind_umgebung(*, testprofil: bool = True, quellen: bool = True,
                  extra: dict[str, str] | None = None) -> dict[str, str]:
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
        # versuch (systemctl, kdotool, t312-werkzeug) wird protokolliert
        # und richtet nichts an. QUELLEN steht absichtlich NICHT im PATH —
        # die Umleitung muss der Prüfling selbst aus DAIMON_ROUTER_QUELLEN
        # ableiten, sonst misst K12 die Shell statt des Schalters.
        "PATH": str(BEOBACHTER) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
    })
    if not env["LC_ALL"]:
        env.pop("LC_ALL")
    # Kein Session-Bus im Prüfraum: ein ScreenCast-Portal ist damit nicht
    # einmal erreichbar, und K9 misst am Prozess, ob es trotzdem versucht wird.
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    for name in ("DAIMON_ROUTER_QUELLEN", "DAIMON_ROUTER_TESTPROFIL"):
        env.pop(name, None)
    if quellen:
        env["DAIMON_ROUTER_QUELLEN"] = str(QUELLEN)
    if testprofil:
        env["DAIMON_ROUTER_TESTPROFIL"] = "1"
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
        self.p = subprocess.Popen(MIND_CMD, env=mind_umgebung(**opts), cwd=TARGET,
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

    @property
    def pid(self) -> int:
        assert self.p is not None
        return self.p.pid


def frage(text: str, *, marke: str = "user_ptt", runde: object = "haupt") -> tuple[dict, bytes]:
    return unix_json(MIND_SOCK, {"v": 1, "art": "frage", "text": text,
                                 "marke": marke, "runde": runde})


def zustand() -> dict:
    ans, _ = unix_json(MIND_SOCK, {"v": 1, "art": "zustand"})
    return ans


def keine_kanarien(roh: bytes) -> bool:
    return (NUTZER_KANARIE.encode() not in roh
            and FENSTER_KANARIE_A.encode() not in roh
            and FENSTER_KANARIE_B.encode() not in roh)


# ------------------------------------------------------------- Prüfverlauf

print("T-3.12 — Router-Prüfstand, ausschließlich lokale Attrappen")
print(f"  Baum: {TARGET}")
print(f"  Modus: {'FIXTURE' if FIXTURE else 'ARBEITSBAUM'}")
print(f"  Laufzeitraum: {RT}")

hub = HubAttrappe(DAIMON_RT)
egress = EgressAttrappe(DAIMON_RT, hub)
mind = Mind()

P.kapitel("V", "Voraussetzungen und Messstrecke")
for name, ok in (
    ("Mind-Service-Unit vorhanden", MIND_UNIT.is_file()),
    ("Mind-ExecStart aus Unit auflösbar", bool(MIND_CMD)),
    ("wpctl-Attrappe antwortet lesbar", subprocess.run(
        [str(QUELLEN / "wpctl"), "get-volume", "@DEFAULT_AUDIO_SINK@"],
        capture_output=True).stdout == b"Volume: 0.42\n"),
    ("qdbus6-Attrappe liefert die Kanarienfenster", FENSTER_KANARIE_A in subprocess.run(
        [str(QUELLEN / "qdbus6"), "--literal", "org.kde.KWin", "/WindowsRunner",
         "org.kde.krunner1.Match", ""], capture_output=True).stdout.decode()),
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
P.check("V", "Messstrecke: Ticketbuch gibt ein Ticket aus", ausg.get("ok"), True)
selbst_ans, _ = unix_json(DAIMON_RT / "egress.sock",
                          {"v": 1, "art": "anfrage",
                           "ticket": ausg.get("ticket", ""),
                           "koerper": selbst_koerper})
P.check("V", "Messstrecke: Egress-Attrappe nimmt das Ticket an", selbst_ans.get("ok"), True)
P.check("V", "Messstrecke: Einlösung wurde gezählt", hub.zaehler(), (1, 1))
hub.zuruecksetzen()
egress.aufrufe.clear()
quellen_log()  # Logdatei existiert ab jetzt sicher

mind.start()
P.check("V", "Mind-Socket erscheint", warten(MIND_SOCK, mind.p), True)
if P.rot["V"]:
    print("\nT-3.12: FEHLGESCHLAGEN — Voraussetzungen fehlen, keine Scheinmessung.")
    raise SystemExit(1)

z0 = zustand()
P.check("V", "Zustand antwortet und meldet das Testprofil sichtbar",
        (z0.get("ok"), z0.get("testprofil")), (True, True))
P.check("V", "Zustand nennt genau die sechs Absichten des Vertrags",
        sorted(z0.get("absichten", [])),
        sorted(["uhrzeit", "lautstaerke", "sitzung", "fensterliste", "aktion", "api"]))
P.check("V", "Selbstauskunft pid stimmt mit dem gestarteten Prozess überein",
        z0.get("pid"), mind.pid)


def kein_ticket_verbraucht(k: str, basis: tuple[int, int], egress_basis: int,
                           name: str) -> None:
    P.check(k, f"{name}: kein Ticket ausgegeben oder eingelöst",
            hub.zaehler(), basis)
    P.check(k, f"{name}: kein Aufruf an der Egress-Attrappe",
            egress.zaehler(), egress_basis)


P.kapitel("1", "vier lokale Absichten, lokal und ohne Ticket")
basis = hub.zaehler()
ebasis = egress.zaehler()

ans, _ = frage("wie spät ist es")
P.check("1", "uhrzeit wird lokal beantwortet", (ans.get("ok"), ans.get("weg")), (True, "lokal"))
P.check("1", "uhrzeit heißt uhrzeit und ist trusted",
        (ans.get("absicht"), ans.get("marke")), ("uhrzeit", "trusted"))
P.check("1", "uhrzeit-Antwort trägt eine Uhrzeit (Positivkontrolle)",
        bool(re.search(r"\d{1,2}:\d{2}", str(ans.get("antwort", "")))), True)
P.check("1", "uhrzeit meldet api: false", ans.get("api"), False)
kein_ticket_verbraucht("1", basis, ebasis, "uhrzeit")

ans, _ = frage("wie laut ist es gerade")
P.check("1", "lautstaerke wird lokal beantwortet", (ans.get("ok"), ans.get("weg")), (True, "lokal"))
P.check("1", "lautstaerke heißt lautstaerke und ist trusted",
        (ans.get("absicht"), ans.get("marke")), ("lautstaerke", "trusted"))
P.check("1", "lautstaerke-Antwort trägt den Attrappenwert 42 (Positivkontrolle)",
        "42" in str(ans.get("antwort", "")), True)
P.check("1", "lautstaerke meldet api: false", ans.get("api"), False)
kein_ticket_verbraucht("1", basis, ebasis, "lautstaerke")

ans, _ = frage("welche sitzungen sind aktiv")
P.check("1", "sitzung wird lokal beantwortet", (ans.get("ok"), ans.get("weg")), (True, "lokal"))
P.check("1", "sitzung heißt sitzung und ist trusted",
        (ans.get("absicht"), ans.get("marke")), ("sitzung", "trusted"))
P.check("1", "sitzung-Antwort trägt den Hub-Zustand (Positivkontrolle)",
        SITZUNG_KANARIE in str(ans.get("antwort", "")), True)
P.check("1", "Projektname (tainted) fehlt in der trusted-Sitzungsauskunft",
        PROJEKT_KANARIE in str(ans.get("antwort", "")), False)
P.check("1", "sitzung meldet api: false", ans.get("api"), False)
kein_ticket_verbraucht("1", basis, ebasis, "sitzung")

ans, _ = frage("welche fenster sind offen")
P.check("1", "fensterliste wird lokal beantwortet", (ans.get("ok"), ans.get("weg")), (True, "lokal"))
P.check("1", "fensterliste heißt fensterliste und ist tainted",
        (ans.get("absicht"), ans.get("marke")), ("fensterliste", "tainted"))
P.check("1", "fensterliste-Antwort trägt den Kanarientitel (Positivkontrolle)",
        FENSTER_KANARIE_A in str(ans.get("antwort", "")), True)
P.check("1", "fensterliste meldet api: false", ans.get("api"), False)
kein_ticket_verbraucht("1", basis, ebasis, "fensterliste")

ans, _ = frage("erkläre mir den unterschied zwischen prozess und thread")
P.check("1", "Positivkontrolle: Inhaltsfrage geht an die API und gelingt",
        (ans.get("ok"), ans.get("weg"), ans.get("api")), (True, "api", True))
P.check("1", "Positivkontrolle: Antwortkanarie der Attrappe kommt an",
        ANTWORT_KANARIE in str(ans.get("antwort", "")), True)
P.check("1", "Positivkontrolle: genau ein Ticket ausgegeben und eingelöst",
        hub.zaehler(), (basis[0] + 1, basis[1] + 1))
P.check("1", "Positivkontrolle: genau ein Egress-Aufruf gezählt",
        egress.zaehler(), ebasis + 1)

P.kapitel("2", "Absichtserkennung ist deterministisch")
erste = [frage("wie spät ist es")[0].get("absicht") for _ in range(10)]
P.check("2", "dieselbe Äußerung ergibt zehnmal dieselbe Absicht",
        len(set(erste)), 1)
P.check("2", "und es ist die richtige", erste[0], "uhrzeit")
P.check("2", "englische Entsprechung ergibt dieselbe Absicht",
        frage("what time is it")[0].get("absicht"), "uhrzeit")
mind.start(extra={"TZ": "Pacific/Kiritimati", "LC_ALL": "C", "LANG": "C"})
P.check("2", "Mind startet unter gewechselter Umgebung", warten(MIND_SOCK, mind.p), True)
P.check("2", "Umgebungswechsel (TZ, LC_ALL=C) ändert die Absicht nicht",
        frage("wie spät ist es")[0].get("absicht"), "uhrzeit")
mind.start()
P.check("2", "Mind startet unter Normalumgebung wieder", warten(MIND_SOCK, mind.p), True)

P.kapitel("3", "Aktionswünsche werden abgelehnt, nicht ausgeführt")
bbasis = beobachter_log()
qbasis = quellen_log()
tbasis = hub.zaehler()
tebasis = egress.zaehler()
aktionen = [
    "mach das fenster zu",
    "stell die lautstärke auf 30",
    "starte den browser",
    "close the discord window",
    "starte t312-werkzeug",
    f"mach das fenster »{NUTZER_KANARIE} äöü« zu",
]
for text in aktionen:
    ans, roh = frage(text)
    P.check("3", f"Aktionswunsch abgelehnt: {text[:38]!r}",
            (ans.get("ok"), ans.get("weg"), ans.get("absicht"), ans.get("api")),
            (True, "abgelehnt", "aktion", False))
    P.check("3", "Ablehnung nennt weder Nutzertext noch Fenstertitel",
            keine_kanarien(roh), True)
P.check("3", "kein Ausführungswerkzeug wurde angerührt (Beobachter leer)",
        beobachter_log(), bbasis)
neue_quellen = quellen_log()[len(qbasis):]
P.check("3", "keine verändernde Quellenbedienung hinter den Ablehnungen",
        [z for z in neue_quellen
         if "set-volume" in z or " Run" in z or "close" in z.lower()], [])
P.check("3", "Ablehnungen kosten kein Kontingent und keinen Egress",
        (hub.zaehler(), egress.zaehler()), (tbasis, tebasis))
ans, _ = frage("wie spät ist es")
P.check("3", "Positivkontrolle: gültige Anfrage nach den Ablehnungen gelingt",
        (ans.get("ok"), ans.get("weg")), (True, "lokal"))

P.kapitel("4", "kein Egress-Aufruf ohne Kontingent")
hub.kein_ticket = True
ebasis = egress.zaehler()
ans, roh = frage(f"sag mir etwas über {NUTZER_KANARIE}")
P.check("4", "ohne Ticket des Hubs: kein_kontingent",
        (ans.get("ok"), ans.get("grund")), (False, "kein_kontingent"))
P.check("4", "der Egress-Zähler bleibt ohne Ticket stehen", egress.zaehler(), ebasis)
P.check("4", "Absage nennt den Nutzertext nicht", NUTZER_KANARIE.encode() in roh, False)
hub.kein_ticket = False
ans, _ = frage(f"sag mir etwas über {NUTZER_KANARIE}")
P.check("4", "Positivkontrolle: mit Ticket steigt der Egress-Zähler",
        (ans.get("ok"), egress.zaehler()), (True, ebasis + 1))

P.kapitel("5", "user_audio erreicht Durchgang 1 nicht; Protokoll-Absagen")
tbasis = hub.zaehler()
ebasis = egress.zaehler()
qbasis = quellen_log()
ans, roh = frage("wie spät ist es", marke="user_audio")
P.check("5", "user_audio wird marke_verboten", (ans.get("ok"), ans.get("grund")),
        (False, "marke_verboten"))
P.check("5", "kein Ticketversuch vor der Senke", hub.zaehler(), tbasis)
P.check("5", "kein Egress-Versuch vor der Senke", egress.zaehler(), ebasis)
P.check("5", "keine Quellenabfrage vor der Senke", quellen_log(), qbasis)
P.check("5", "Absage nennt den Nutzertext nicht", keine_kanarien(roh), True)
ans, _ = frage("wie spät ist es", marke="user_ptt")
P.check("5", "Positivkontrolle: dieselbe Frage als user_ptt gelingt",
        (ans.get("ok"), ans.get("absicht")), (True, "uhrzeit"))
# Die drei protokollnahen Gründe aus Vertrag §2 (unter K5 abgerechnet,
# weil die Akzeptanzliste ihnen kein eigenes Kriterium gibt):
roh = unix_roh(MIND_SOCK, b"{kein json}\n")
try:
    ans = json.loads(roh)
except json.JSONDecodeError:
    ans = {}
P.check("5", "keine JSON-Zeile wird unlesbar", ans.get("grund"), "unlesbar")
ans, _ = unix_json(MIND_SOCK, {"v": 1, "art": "was_anderes"})
P.check("5", "fremde art wird unbekannte_art", ans.get("grund"), "unbekannte_art")
ans, _ = unix_json(MIND_SOCK, {"v": 1, "art": "frage", "marke": "user_ptt"})
P.check("5", "fehlender text wird kein_text", ans.get("grund"), "kein_text")
ans, _ = unix_json(MIND_SOCK, {"v": 1, "art": "frage", "text": "  ",
                               "marke": "user_ptt"})
P.check("5", "leerer text wird kein_text", ans.get("grund"), "kein_text")
ans, _ = frage("wie spät ist es")
P.check("5", "Positivkontrolle nach den Protokoll-Absagen", ans.get("ok"), True)

P.kapitel("6", "kein Fenstertitel im Prompt")
ebasis = egress.zaehler()
ans, _ = frage("sieh dir meine offenen fenster an und sag mir, womit ich "
               "gerade beschäftigt bin")
P.check("6", "fensterbezogene Äußerung geht an die API", (ans.get("ok"), ans.get("weg")),
        (True, "api"))
P.check("6", "ein Körper wurde an der Attrappe aufgezeichnet",
        egress.zaehler() > ebasis, True)
koerper = egress.aufrufe[-1]
P.check("6", "Kanarien-Fenstertitel fehlt im gesendeten Körper",
        FENSTER_KANARIE_A.encode() in koerper, False)
P.check("6", "Körper trägt eine opake Referenz (w_N)",
        bool(re.search(rb"w_\d+", koerper)), True)
P.check("6", "Körper trägt die app_id aus der geschlossenen Aufzählung",
        b"discord" in koerper, True)
P.check("6", "auch der zweite reale Titel fehlt im Körper",
        "projekt — Konsole".encode() in koerper, False)

P.kapitel("7", "Gegenprobe: die Antwort an den Nutzer trägt die Titel")
ans, _ = frage("welche fenster sind offen")
P.check("7", "dieselbe Kanarie erscheint in der Nutzerantwort",
        FENSTER_KANARIE_A in str(ans.get("antwort", "")), True)
P.check("7", "und ist als tainted markiert", ans.get("marke"), "tainted")
P.check("7", "und bleibt lokal ohne Ticket", ans.get("weg"), "lokal")

P.kapitel("8", "Referenzen halten nur eine Runde")
ans, _ = frage("welche fenster sind offen", runde="runde-eins")
P.check("8", "Runde eins: Kanarie A in der Nutzerantwort (Aufbau)",
        FENSTER_KANARIE_A in str(ans.get("antwort", "")), True)
ebasis = egress.zaehler()
frage("was läuft in w_1", runde="runde-eins")
P.check("8", "Runde eins: API-Aufruf fand statt", egress.zaehler(), ebasis + 1)
k1 = egress.aufrufe[-1]
P.check("8", "Positivkontrolle: Runde eins trägt die app_id von Kanarie A",
        b"discord" in k1, True)
P.check("8", "Runde eins trägt den Titel trotzdem nicht",
        FENSTER_KANARIE_A.encode() in k1, False)
setze_fenster(FENSTER_B)
ans, roh = frage("welche fenster sind offen", runde="runde-zwei")
P.check("8", "Runde zwei: die neue Kanarie B erscheint",
        FENSTER_KANARIE_B in str(ans.get("antwort", "")), True)
P.check("8", "Runde zwei: die alte Kanarie A ist aus der Antwort verschwunden",
        FENSTER_KANARIE_A in str(ans.get("antwort", "")), False)
ebasis = egress.zaehler()
ans2, roh2 = frage("was läuft in w_1", runde="runde-zwei")
if egress.zaehler() > ebasis:
    k2 = egress.aufrufe[-1]
    P.check("8", "Referenz aus der Vorrunde wird nicht aufgelöst (kein Titel A im Körper)",
            FENSTER_KANARIE_A.encode() in k2, False)
    P.check("8", "Referenz aus der Vorrunde wird nicht aufgelöst (keine alte app_id)",
            b"discord" in k2, False)
else:
    P.info("Runde zwei erzeugte keinen API-Aufruf; Auflösung nur in der Antwort prüfbar.")
P.check("8", "Antwort der zweiten Runde trägt die alte Kanarie nicht",
        FENSTER_KANARIE_A.encode() in roh2, False)
setze_fenster(FENSTER_A)

P.kapitel("9", "kein Bildschirmkontext am laufenden Prozess")
pid = mind.pid
fd_ziele: list[str] = []
try:
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            fd_ziele.append(os.readlink(fd))
        except OSError:
            pass
    fds_lesbar = True
except OSError:
    fds_lesbar = False
P.check("9", "Deskriptortabelle des Mind ist lesbar (Positivkontrolle)",
        fds_lesbar and bool(fd_ziele), True)
P.check("9", "kein /dev/dri-Deskriptor im Mind",
        [z for z in fd_ziele if "/dev/dri" in z], [])
try:
    maps = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8", errors="replace")
except OSError:
    maps = ""
P.check("9", "Adressraum-Landkarte ist lesbar (Positivkontrolle)", bool(maps), True)
P.check("9", "keine OCR-Bibliothek im Adressraum",
        any(b in maps.lower() for b in ("tesseract", "leptonica")), False)
inodes = {m.group(1) for z in fd_ziele for m in [re.match(r"socket:\[(\d+)\]", z)] if m}
pfade: list[str] = []
try:
    for zeile in Path("/proc/net/unix").read_text().splitlines()[1:]:
        teile = zeile.split()
        if len(teile) >= 7 and teile[6] in inodes and len(teile) >= 8:
            pfade.append(teile[7])
        elif len(teile) >= 7 and teile[6] in inodes:
            pfade.append("")
except OSError:
    pass
P.check("9", "Unix-Socket-Liste des Mind ist auffindbar (Positivkontrolle)",
        any("mind.sock" in p for p in pfade), True)
P.check("9", "keine Verbindung zu einem ScreenCast-Portal",
        [p for p in pfade if "portal" in p.lower()], [])
try:
    environ = Path(f"/proc/{pid}/environ").read_bytes()
except OSError:
    environ = b""
P.check("9", "Mind hat keinen Session-Bus in der Umgebung bekommen",
        b"DBUS_SESSION_BUS_ADDRESS" in environ, False)
schluessel: list[str] = []
def _keys(obj: object) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            schluessel.append(str(k).lower())
            _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v)
for kbytes in egress.aufrufe:
    try:
        _keys(json.loads(kbytes))
    except json.JSONDecodeError:
        pass
P.check("9", "kein Körperfeld trägt Bildschirminhalt (Schlüsselnamen)",
        [s for s in schluessel
         if re.search(r"bildschirm|screenshot|screen|ocr|pixel|image", s)], [])
P.check("9", "Positivkontrolle: aufgezeichnete Körper haben erwartbare Felder",
        ("messages" in schluessel or "model" in schluessel), True)

P.kapitel("10", "ein API-Fehler wird sprechbar")
egress.modus = "absage"
ans, roh = frage(f"erkläre mir {NUTZER_KANARIE}")
P.check("10", "Egress-Absage wird sprechbar: ok falsch, weg api, egress_weg",
        (ans.get("ok"), ans.get("weg"), ans.get("grund")), (False, "api", "egress_weg"))
P.check("10", "Fehlermeldung enthält weder Nutzertext noch Fenstertitel",
        keine_kanarien(roh), True)
egress.modus = "normal"
ans, _ = frage("erkläre mir etwas über züge")
P.check("10", "Positivkontrolle: nach der Absage gelingt die API wieder",
        ans.get("ok"), True)
egress.stop()
ans, roh = frage(f"erkläre mir noch etwas über {NUTZER_KANARIE}")
P.check("10", "unerreichbarer Egress wird sprechbar: ok falsch, weg api, egress_weg",
        (ans.get("ok"), ans.get("weg"), ans.get("grund")), (False, "api", "egress_weg"))
P.check("10", "Fehlermeldung ohne Nutzertext auch bei totem Egress",
        NUTZER_KANARIE.encode() in roh, False)
egress.start()
P.check("10", "Egress-Attrappe ist wieder da", warten(egress.pfad), True)
ans, _ = frage("erkläre mir etwas über schiffe")
P.check("10", "Positivkontrolle: nach dem Neustart gelingt die API wieder",
        ans.get("ok"), True)

P.kapitel("11", "jede lokale Quelle kann ausfallen")
(QUELLEN / "AUSFALL_KWIN").touch()
ans, roh = frage("welche fenster sind offen", runde="ausfall-kwin")
P.check("11", "KWin weg: quelle_weg", (ans.get("ok"), ans.get("grund")),
        (False, "quelle_weg"))
P.check("11", "Meldung nennt keinen Fenstertitel", FENSTER_KANARIE_A.encode() in roh, False)
(QUELLEN / "AUSFALL_KWIN").unlink()
ans, _ = frage("welche fenster sind offen", runde="ausfall-kwin-ok")
P.check("11", "Positivkontrolle: KWin wieder da, Kanarie erscheint",
        FENSTER_KANARIE_A in str(ans.get("antwort", "")), True)
(QUELLEN / "AUSFALL_WPCTL").touch()
ans, _ = frage("wie laut ist es gerade")
P.check("11", "PipeWire weg: quelle_weg", (ans.get("ok"), ans.get("grund")),
        (False, "quelle_weg"))
(QUELLEN / "AUSFALL_WPCTL").unlink()
ans, _ = frage("wie laut ist es gerade")
P.check("11", "Positivkontrolle: PipeWire wieder da, Wert 42 erscheint",
        "42" in str(ans.get("antwort", "")), True)
hub.state_weg = True
ans, _ = frage("welche sitzungen sind aktiv")
P.check("11", "Hub weg: quelle_weg", (ans.get("ok"), ans.get("grund")),
        (False, "quelle_weg"))
hub.state_weg = False
ans, _ = frage("welche sitzungen sind aktiv")
P.check("11", "Positivkontrolle: Hub wieder da, Zustandskanarie erscheint",
        SITZUNG_KANARIE in str(ans.get("antwort", "")), True)
P.check("11", "Positivkontrolle: Projektname bleibt auch hier draußen",
        PROJEKT_KANARIE in str(ans.get("antwort", "")), False)

P.kapitel("12", "Testschalter doppelt verriegelt und sichtbar")
mind.start(quellen=True, testprofil=False)
P.check("12", "Mind startet mit QUELLEN ohne TESTPROFIL", warten(MIND_SOCK, mind.p), True)
z = zustand()
P.check("12", "DAIMON_ROUTER_QUELLEN allein: testprofil bleibt falsch",
        z.get("testprofil"), False)
qbasis = quellen_log()
ans, _ = frage("wie laut ist es gerade")
P.check("12", "Anfrage wird ohne Testprofil wenigstens beantwortet",
        "ok" in ans, True)
P.check("12", "QUELLEN allein lenkt keine Quelle um (Stub-Log unverändert)",
        quellen_log(), qbasis)
mind.start(quellen=False, testprofil=True)
P.check("12", "Mind startet mit TESTPROFIL ohne QUELLEN", warten(MIND_SOCK, mind.p), True)
z = zustand()
P.check("12", "TESTPROFIL allein: testprofil bleibt falsch", z.get("testprofil"), False)
mind.start()
P.check("12", "Mind startet mit beiden Schaltern", warten(MIND_SOCK, mind.p), True)
z = zustand()
P.check("12", "beide Schalter zusammen: testprofil wahr und sichtbar",
        z.get("testprofil"), True)
qbasis = quellen_log()
ans, _ = frage("wie laut ist es gerade")
P.check("12", "Positivkontrolle: mit beiden Schaltern wirkt die Attrappe",
        "42" in str(ans.get("antwort", "")), True)
P.check("12", "Positivkontrolle: der Stub wurde auch wirklich gerufen",
        len(quellen_log()) > len(qbasis), True)

P.kapitel("13", "api ist gemessen, nicht behauptet")
hub.zuruecksetzen()
with egress.lock:
    egress.aufrufe.clear()
mind.start()
P.check("13", "frischer Mind für die Zählung", warten(MIND_SOCK, mind.p), True)
a1, _ = frage("erkläre mir den unterschied zwischen stapel und haldenspeicher",
              runde="k13-a")
a2, _ = frage("erkläre mir den unterschied zwischen prozess und thread",
              runde="k13-b")
a3, _ = frage("welche sitzungen sind aktiv", runde="k13-c")
P.check("13", "zwei API-Antworten melden api: wahr",
        (a1.get("api"), a2.get("api")), (True, True))
P.check("13", "lokale Antwort meldet api: falsch", a3.get("api"), False)
z = zustand()
P.check("13", "Selbstauskunft api_aufrufe stimmt mit der Ticketbuch-Zählung überein",
        z.get("api_aufrufe"), hub.zaehler()[1])
P.check("13", "Ticketbuch-Zählung und Egress-Zählung stimmen überein",
        hub.zaehler(), (2, 2))
P.check("13", "Egress-Attrappe zählte genau zwei Aufrufe", egress.zaehler(), 2)
P.check("13", "Selbstauskunft runden stimmt mit den drei Fragen überein",
        z.get("runden"), 3)

P.kapitel("14", "eingefrorene Prüfstände bleiben grün")
if FIXTURE:
    P.info("Fixture/Mutanten sind keine vollständigen Arbeitsbäume; K14 läuft "
           "nur im Arbeitsbaumlauf.")
else:
    kind_env = os.environ.copy()
    kind_env.pop("DAIMON_FIXTURE", None)
    for task in ("T-3.9", "T-3.8", "T-3.10", "T-3.11"):
        rc = subprocess.run([str(REPO / "tests/verify" / f"{task}.sh")],
                            cwd=REPO, env=kind_env).returncode
        P.check("14", f"{task} bleibt einzeln vollständig grün", rc, 0)
        # Aufräumphase: zwei Prüfstände nie Rücken an Rücken.
        subprocess.run(["systemctl", "--user", "reset-failed"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)

P.kapitel("15", "pytest grün")
if FIXTURE:
    P.info("Fixture/Mutanten haben keine Testsuite; K15 läuft nur im "
           "Arbeitsbaumlauf.")
else:
    # --tb=no: ein roter Test darf dem Reviewer keinen Quelltext zeigen.
    r = subprocess.run([str(PYTHON), "-B", "-P", "-m", "pytest", "-q", "--tb=no"],
                       cwd=REPO, env=kind_env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=900)
    P.check("15", "pytest ist im Arbeitsbaum grün", r.returncode, 0)

print("\n=== Ergebnis je Kriterium ===")
for k in [str(x) for x in range(1, 16)]:
    print(f"  K{k}: {P.n[k]} Prüfungen, {P.rot[k]} rot")
print(f"  Voraussetzungen: {P.n['V']} Prüfungen, {P.rot['V']} rot")
gesamt = sum(P.n.values())
rot = sum(P.rot.values())
print(f"T-3.12: {gesamt} Prüfungen, {rot} rot")
raise SystemExit(1 if P.fail else 0)
