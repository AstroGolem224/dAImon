#!/usr/bin/env python3
"""Blinder Laufzeit-Prüfstand für T-3.13b (Markierungsverfolgung und Senken).

Gebaut aus dem Vertrag (T-3.13b-Markierung-Plan.md, Stand 05.08.). §2/§3
liefern die Zusagen; §6 pinnt die Python-API (`SENKEN` als
dict[str, dict[Mark, bool]], `pruefe_senke(wert, *, senke, log=None)`,
`verketten(*teile) -> Marked`, `markiere`, `audit_redigiert`); §7 pinnt die
Drahtform am Hook-Bus (Nutzlast roh, Marken als Begleitfeld `__marken__` —
die `__mark__`-Objektform mit `Marked.from_wire` gilt an neuen Grenzen).
Kein Aufruf verlässt
die Maschine: Hub-Ticketbuch, Hub-Zustand, Egress und die lokalen Quellen
sind Attrappen des Reviewers; die Marken werden am anderen Ende der Grenze
wieder ausgelesen (Socket, dumps/loads, Bridge-Pfad) — Selbstauskunft ist
kein Messwert.

Der Punkt, an dem dieser Prüfstand nicht falsch abbiegen darf (§2):
* Eine ROHE Zeichenkette an einer Senke gilt als `tainted` und wird
  protokolliert (marke_fehlt) — KEIN Wurf. Hier wird kein Wurf verlangt.
* MARKIERTES Material an einer verbotenen Senke wirft SenkenFehler — hier
  wird kein stilles Filtern durchgelassen.
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

FRAGE_KANARIE = "T313B-FRAGE-KANARIE-3f8a1c"
PERSONA_KANARIE = "T313B-PERSONA-KANARIE-9d2e4b"
FENSTER_KANARIE = "T313B-FENSTER-KANARIE-6c5f02"
SITZUNG_KANARIE = "T313B-SITZUNG-KANARIE-58e1d3"
PROJEKT_KANARIE = "T313B-PROJEKT-KANARIE-27aa90"
ANTWORT_KANARIE = "T313B-ANTWORT-KANARIE-1b7d90"
ROH_KANARIE = "T313B-ROH-KANARIE-44ce21"
BRIDGE_KANARIE = "T313B-BRIDGE-KANARIE-77ab19"

# Kanarie für die Vorschau: ASCII-Kern, Bidi-Steuerzeichen, Nullbreite und
# Überlänge — sie muss ESCAPT wieder herauskommen, nicht gar nicht.
VORSCHAU_KERN = "T313B-VORSCHAU-KANARIE"
VORSCHAU_BIDI = "‮"
VORSCHAU_NULLBREITE = "​"
VORSCHAU_KANARIE = (VORSCHAU_KERN + VORSCHAU_BIDI + VORSCHAU_NULLBREITE
                    + "x" * 300)

# Eine strukturiert aussehende Modellausgabe (Kriterium 9): auch sie ist
# freie Modellausgabe und damit tainted.
STRUKTURIERTE_MODELLAUSGABE = '{"antwort": "42", "typ": "info"}'

RUECKFRAGE_FORM = {"v": 1, "ok": True, "weg": "rueckfrage",
                   "absicht": "aktion", "antwort": "Was soll ich womit machen?",
                   "marke": "trusted", "api": False}

VIER_MARKEN = ["tainted", "trusted", "user_audio", "user_ptt"]  # sortiert


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
RT = Path(tempfile.mkdtemp(prefix="t313b-", dir=laufzeit_basis))
CONFIG = RT / "config"
STATE = RT / "state"
RUNTIME = RT / "runtime"
DAIMON_RT = RUNTIME / "daimon"
QUELLEN = RT / "quellen"
BEOBACHTER = RT / "beobachter"
PROBEN = RT / "proben"
for d in (CONFIG / "daimon" / "persona", STATE / "daimon", DAIMON_RT,
          QUELLEN, BEOBACHTER, PROBEN):
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)

MIND_SOCK = DAIMON_RT / "mind.sock"
HOOK_SOCK = DAIMON_RT / "hookbridge.sock"
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


def basis_umgebung(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(TARGET),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_RUNTIME_DIR": str(RUNTIME),
        "XDG_CONFIG_HOME": str(CONFIG),
        "XDG_STATE_HOME": str(STATE),
        # Vollständige Locale weitergeben: ohne LANG läuft ein Kind in der
        # C-Locale, und dieser Task hat Nicht-ASCII-Fälle (Bidi, Nullbreite).
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "LC_ALL": os.environ.get("LC_ALL") or "",
        # Die Beobachter stehen im PATH des Prüflings: ein Ausführungs-
        # versuch wird protokolliert und richtet nichts an.
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


def probe(name: str, *args: str) -> tuple[int, str, str]:
    """Eine Sonde gegen den Prüfling: eigenes Kind mit PYTHONPATH=TARGET.
    Rückgabe (rc, stdout, stderr) — die Sonde druckt ein JSON-Ergebnis."""
    r = subprocess.run(
        [str(PYTHON), "-B", "-P", str(PROBEN / name), *args],
        env=basis_umgebung(), cwd=TARGET,
        capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def probe_json(name: str, *args: str) -> dict:
    rc, out, _ = probe(name, *args)
    if rc != 0:
        return {"__sonde_fehler__": rc}
    try:
        obj = json.loads(out.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"__sonde_fehler__": "unlesbar"}
    return obj if isinstance(obj, dict) else {"__sonde_fehler__": "kein_objekt"}


def journal_marke_fehlt() -> int:
    """Zählt marke_fehlt-Datensätze im Nutzerjournal. -1: Journal nicht
    lesbar — dann trägt der stderr-Rückfall des Prozesses den Nachweis."""
    try:
        r = subprocess.run(["journalctl", "--user", "-o", "json", "-n", "3000",
                            "--no-pager"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return -1
    if r.returncode != 0:
        return -1
    return r.stdout.count(b'"DAIMON_ACTION":"marke_fehlt"')


def pgrep_zaehler(muster: str) -> int:
    r = subprocess.run(["pgrep", "-cf", muster], capture_output=True)
    # pgrep liefert rc 1, wenn nichts passt — das ist 0, kein Fehler.
    try:
        return int(r.stdout.strip() or b"0")
    except ValueError:
        return -1


# ---------------------------------------------------------------- Attrappen


class HubAttrappe:
    """Das Ticketbuch und der Zustand des Reviewers. Zählt Ausgaben und
    Einlösungen selbst und protokolliert jede Anfrage roh."""

    def __init__(self, rt: Path) -> None:
        self.rt = rt
        self.ausgegeben = 0
        self.eingeloest = 0
        self.kein_ticket = False
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

    def zuruecksetzen(self) -> None:
        with self.lock:
            self.ausgegeben = 0
            self.eingeloest = 0
            self.tickets = {}
            self.anfragen = []


class EgressAttrappe:
    """Der Egress des Reviewers: löst Tickets am eigenen Ticketbuch ein,
    zeichnet jeden Körper auf und spielt den Modelltext des Reviewers."""

    SOCKNAME = "egress.sock"

    def __init__(self, rt: Path, hub: HubAttrappe) -> None:
        self.rt = rt
        self.hub = hub
        self.pfad = rt / self.SOCKNAME
        self.antwort_text = ANTWORT_KANARIE
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
        antwort = {"id": "lokal",
                   "content": [{"type": "text", "text": text}],
                   "stop_reason": "end_turn"}
        return {"v": 1, "ok": True, "status": 200, "bytes": 80,
                "dauer_ms": 1.0, "antwort": antwort}

    def zaehler(self) -> int:
        with self.lock:
            return len(self.aufrufe)

    def setze_antwort(self, text: str) -> None:
        with self.lock:
            self.antwort_text = text


class LokalAttrappe(EgressAttrappe):
    """Der lokale Broker des Reviewers -- der Weg, den der Mind heute
    WIRKLICH nimmt.

    `config/systemd/daimon-mind.service` startet ihn mit
    `--egress-socket %t/daimon/lokal.sock`; ohne diese Attrappe redet der
    Prueflaufs-Mind gegen eine Datei, die es nicht gibt, und jede
    Modellfrage endet in der kuratierten Absage ("Ich komme gerade nicht an
    die API.") -- die zu Recht `trusted` traegt und damit sechzehn
    Markierungs-Kriterien gruen-falsch rot faerbt.

    Anderes Protokoll als der Egress, belegt an
    `daimon/brokers/lokal/broker.py: LokalBroker.anfrage`:
    Pflichtfelder `ticket` und `koerper` werden VOR dem Modell geprueft
    (`kein_ticket`, `kein_koerper`), und die Antwort traegt NUR `content` --
    kein `id`, kein `stop_reason` (der lokale Broker baut sie in
    `daimon/mind/lokal.py: antwort_bloecke` selbst, aus Ollamas `message`).
    """

    SOCKNAME = "lokal.sock"
    # Freie Antwortbloecke statt nur Text: nur so kann dieser Pruefstand das
    # Modell EIN WERKZEUG rufen lassen (K11) -- und damit belegen, dass die
    # Stille an `aktion.sock` gemessen und nicht bloss ungebunden ist.
    bloecke: list[dict] | None = None

    def setze_bloecke(self, bloecke: list[dict] | None) -> None:
        with self.lock:
            self.bloecke = bloecke

    def _anfrage(self, req: dict) -> dict:
        ticket = req.get("ticket")
        if not isinstance(ticket, str) or not ticket.strip():
            return {"v": 1, "ok": False, "grund": "kein_ticket",
                    "meldung": "Feld `ticket` fehlt oder ist leer"}
        koerper = req.get("koerper")
        if not isinstance(koerper, dict) or not koerper:
            return {"v": 1, "ok": False, "grund": "kein_koerper",
                    "meldung": "Feld `koerper` fehlt oder ist kein Objekt"}
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
                                   "ticket": ticket, "auftrag_hash": h})
        except OSError:
            eingel = {"ok": False}
        if not eingel.get("ok"):
            return {"v": 1, "ok": False, "grund": "ticket_ungueltig",
                    "meldung": "Hub sagt nein"}
        antwort = {"content": bloecke or [{"type": "text", "text": text}]}
        bytes_ = len(json.dumps(antwort, ensure_ascii=False).encode("utf-8"))
        return {"v": 1, "ok": True, "status": 200, "bytes": bytes_,
                "dauer_ms": 1.0, "antwort": antwort}


class AktionAttrappe:
    """Der Koordinator des Reviewers an `aktion.sock` (T-4.16 K1).

    Er zaehlt, was ankommt, und fuehrt nichts aus. Er MUSS gebunden sein,
    damit "aktion.sock bekommt keinen Aufruf" ein MESSWERT ist: an einem
    ungebundenen Socket ist Stille von Abwesenheit nicht zu unterscheiden --
    genau der Falschbefund, den `scratchpad/mind_absage_messen.py` liefert,
    weil dort `XDG_RUNTIME_DIR` steht und der Mind einen ganz anderen
    `aktion.sock` meint als der Horcher.
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
        try:
            return json.loads(self.aufrufe[-1])
        except json.JSONDecodeError:
            return {}


# ------------------------------------------------------- Quellen-Attrappen


def schreibe_stub(pfad: Path, name: str) -> None:
    rumpf = f"""#!/usr/bin/env bash
# T-3.13b-Quellenattrappe "{name}" — protokolliert, veraendert nichts.
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
for name in ("systemctl", "kdotool", "xdotool", "wmctrl", "t313b-werkzeug"):
    stub = BEOBACHTER / name
    stub.write_text(f"""#!/usr/bin/env bash
# T-3.13b-Beobachter "{name}" — protokolliert Argumente, tut nichts.
printf '%s %s\\n' "{name}" "$*" >> "{BEOBACHTER}/aufrufe.log"
exit 0
""", encoding="utf-8")
    stub.chmod(0o700)

# Die Persona des Reviewers: XDG gewinnt (T-3.10).
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


class Mind:
    def __init__(self) -> None:
        self.p: subprocess.Popen | None = None
        self.logpfad = RT / "mind.log"

    def start(self) -> None:
        self.stop()
        log = self.logpfad.open("ab")
        self.p = subprocess.Popen(MIND_CMD, env=basis_umgebung(), cwd=TARGET,
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

    def logtext(self) -> str:
        if not self.logpfad.is_file():
            return ""
        return self.logpfad.read_text(encoding="utf-8", errors="replace")


def frage(text: str, *, marke: str | None = "user_ptt",
          runde: object = "haupt") -> tuple[dict, bytes]:
    req: dict = {"v": 1, "art": "frage", "text": text, "runde": runde}
    if marke is not None:
        req["marke"] = marke
    return unix_json(MIND_SOCK, req)


def zustand() -> dict:
    ans, _ = unix_json(MIND_SOCK, {"v": 1, "art": "zustand"})
    return ans


def kanariefrei(roh: bytes, *kanarien: str) -> bool:
    return all(k.encode() not in roh for k in kanarien)


# ------------------------------------------------------------------ Bridge


def freier_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


class HookSockAttrappe:
    """Die Hub-Seite des Bridge-Pfads: liest eine Zeile von hookbridge.sock
    und hebt sie roh auf — die Marke wird HIER wieder ausgelesen, nicht an
    der Selbstauskunft der Bridge."""

    def __init__(self, pfad: Path) -> None:
        self.pfad = pfad
        self.zeilen: list[bytes] = []
        self.lock = threading.Lock()
        self._stopp = threading.Event()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
                    zeile = conn.makefile("rb").readline(4 << 20)
                    with self.lock:
                        self.zeilen.append(zeile)
                except OSError:
                    pass

    def letzte(self) -> bytes:
        with self.lock:
            return self.zeilen[-1] if self.zeilen else b""

    def anzahl(self) -> int:
        with self.lock:
            return len(self.zeilen)


BRIDGE_PORT = freier_port()
bridge_prozess: subprocess.Popen | None = None
bridge_logpfad = RT / "bridge.log"


def bridge_starten() -> None:
    global bridge_prozess
    log = bridge_logpfad.open("ab")
    bridge_prozess = subprocess.Popen(
        [str(PYTHON), "-B", "-P", "-m", "daimon.hookbridge.daemon",
         "--runtime-dir", str(DAIMON_RT),
         "--host", "127.0.0.1", "--port", str(BRIDGE_PORT)],
        env=basis_umgebung(), cwd=TARGET, stdout=log,
        stderr=subprocess.STDOUT)
    prozesse.append(bridge_prozess)


def bridge_post(payload: dict, token: str) -> tuple[int, str]:
    import urllib.request
    import urllib.error
    roh = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{BRIDGE_PORT}/hook", data=roh, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as ant:
            return ant.status, ant.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ Sonden

(PROBEN / "probe_protocol.py").write_text('''
import json
from daimon.common.protocol import (ActionApproval, Mark, Marked, dumps,
                                    loads)
out = {}
out["marken"] = sorted(m.value for m in Mark)
n = Marked.from_wire("ROHKANARIE-SONDE")
out["nackt"] = [n.mark.value, n.value]
w = Marked.from_wire({"__mark__": "user_ptt", "value": "DRAHTKANARIE"})
out["draht"] = [w.mark.value, w.value]
a = ActionApproval(request_id="r1", approved=True,
                   reason=Marked("GRUNDKANARIE äöü", Mark.USER_AUDIO))
d = dumps(a)
out["dumps_reason"] = d.get("reason")
b = loads(ActionApproval, json.loads(json.dumps(d, ensure_ascii=False)))
out["loads"] = [b.reason.mark.value, b.reason.value]
c = loads(ActionApproval, {"v": 1, "request_id": "r", "approved": True,
                           "reason": "NACKTKANARIE"})
out["loads_nackt"] = [c.reason.mark.value, c.reason.value]
print(json.dumps(out, ensure_ascii=False))
''', encoding="utf-8")

(PROBEN / "probe_socket.py").write_text('''
import json
import socket
import sys
from daimon.common.protocol import Mark, Marked

modus, pfad = sys.argv[1], sys.argv[2]
if modus == "client":
    marke = sys.argv[3]
    wert = sys.argv[4]
    m = Marked(wert, Mark(marke))
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(10)
    c.connect(pfad)
    c.sendall(json.dumps(m.to_wire(), ensure_ascii=False).encode() + b"\\n")
    c.close()
    print(json.dumps({"gesendet": True}))
elif modus == "server":
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(pfad)
    srv.listen(1)
    print(json.dumps({"bereit": True}), flush=True)
    conn, _ = srv.accept()
    with conn:
        conn.settimeout(10)
        zeile = conn.makefile("rb").readline(1 << 20)
    m = Marked.from_wire(json.loads(zeile))
    print(json.dumps({"marke": m.mark.value, "wert": m.value},
                     ensure_ascii=False))
''', encoding="utf-8")

# Die Sonden folgen der gepinnten API (Vertrag §6): SENKEN ist ein
# dict[str, dict[Mark, bool]] — gezählt werden die Einträge mit True, nicht
# die Schlüssel. verketten ist variadisch, nimmt Werte (markiert oder nackt)
# und gibt ein Marked zurück; die Marke steht in .mark.
(PROBEN / "probe_taint.py").write_text('''
import hashlib
import json
from daimon.common.protocol import Mark, Marked
from daimon.common.taint import (SENKEN, SenkenFehler, audit_redigiert,
                                 markiere, verketten)

out = {"paare": {}, "senken": {}, "fehler": None}
for a, b in [("trusted", "tainted"), ("trusted", "user_ptt"),
             ("user_ptt", "user_audio"), ("user_audio", "tainted"),
             ("trusted", "trusted"), ("user_audio", "user_ptt")]:
    r = verketten(Marked("WERT-A", Mark(a)), Marked("WERT-B", Mark(b)))
    out["paare"][a + "+" + b] = r.mark.value
# Nackter Teil in der Verkettung: Vorgabe ist Misstrauen.
out["paare"]["nackt+trusted"] = verketten("NACKTKANARIE",
                                          Marked("W", Mark.TRUSTED)).mark.value
out["senken"] = {k: sorted(m.value for m, ok in v.items() if ok)
                 for k, v in SENKEN.items()}
out["senkenfehler_ist_wurf"] = issubclass(SenkenFehler, BaseException)
geheim = "AUDIT-KANARIE-GEHEIM"
red = audit_redigiert(Marked(geheim, Mark.TAINTED))
out["audit"] = {"marke": red.mark.value,
                "form": sorted(red.value) if isinstance(red.value, dict)
                        else None,
                "hash_ok": isinstance(red.value, dict) and red.value.get(
                    "sha256") == hashlib.sha256(
                        geheim.encode("utf-8")).hexdigest(),
                "laenge": (red.value or {}).get("laenge")
                          if isinstance(red.value, dict) else None,
                "klartext_drin": geheim in str(red.value)}
print(json.dumps(out))
''', encoding="utf-8")

# Die Bridge-Markierung (Vertrag §7): am Hook-Bus bleibt die Nutzlast roh,
# die Marken wandern als Begleitfeld `__marken__` mit. Die Ausnahmeliste
# selbst wird an markiere_nutzlast gemessen: ein Ereignisname außerhalb der
# geschlossenen Aufzählung ist wieder tainted — sonst wäre „trusted, weil
# Aufzählung" eine Behauptung über ein Feld, das alles enthalten kann.
(PROBEN / "probe_bridge_marks.py").write_text('''
import json
from daimon.hookbridge.bridge import markiere_nutzlast

erfunden = markiere_nutzlast({"hook_event_name": "ERFUNDENES-EVENT-KANARIE",
                              "message": "FREITEXT-KANARIE"})
gueltig = markiere_nutzlast({"hook_event_name": "PostToolUse"})
print(json.dumps({
    "erfunden_event": erfunden["hook_event_name"].mark.value,
    "erfunden_freitext": erfunden["message"].mark.value,
    "gueltig_event": gueltig["hook_event_name"].mark.value,
}))
''', encoding="utf-8")

(PROBEN / "probe_preview.py").write_text('''
import json
import sys
from daimon.auth.preview import vorschau
from daimon.common.protocol import Mark, Marked
from daimon.common.taint import SenkenFehler

kanarie = sys.argv[1]
out = {}
try:
    vorschau(aktion="datei.loeschen", ziel=Marked(kanarie, Mark.USER_AUDIO),
             umkehr="keine")
    out["user_audio"] = "kein_wurf"
except SenkenFehler:
    out["user_audio"] = "senkenfehler"
except Exception as exc:
    out["user_audio"] = "anderer_fehler:" + type(exc).__name__
for name, marke in (("tainted", Mark.TAINTED), ("user_ptt", Mark.USER_PTT),
                    ("trusted", Mark.TRUSTED)):
    try:
        v = vorschau(aktion="datei.loeschen", ziel=Marked(kanarie, marke),
                     umkehr="keine")
        out[name + "_ok"] = True
        out[name + "_ausgabe"] = v
    except Exception as exc:
        out[name + "_ok"] = False
        out[name + "_ausgabe"] = type(exc).__name__
try:
    out["roh_ok"] = True
    out["roh_ausgabe"] = vorschau(aktion="datei.loeschen", ziel=kanarie,
                                  umkehr="keine")
except Exception as exc:
    out["roh_ok"] = False
    out["roh_ausgabe"] = type(exc).__name__
print(json.dumps(out, ensure_ascii=False))
''', encoding="utf-8")


# ------------------------------------------------------------- Prüfverlauf

print("T-3.13b — Prüfstand Markierungsverfolgung und Senken, ausschließlich "
      "lokale Attrappen")
print(f"  Baum: {TARGET}")
print(f"  Modus: {'FIXTURE' if FIXTURE else 'ARBEITSBAUM'}")
print(f"  Laufzeitraum: {RT}")

# Vor dem Lauf gemessen (Regel: was mein Lauf übriglässt, kommt aus meinem
# eigenen Aufbau — und das gehört gefunden statt geerbt).
pgrep_tts_vorher = pgrep_zaehler(r"daimon\.face\.tts")
pgrep_stt_vorher = pgrep_zaehler(r"daimon\.gpu\.stt")

hub = HubAttrappe(DAIMON_RT)
egress = EgressAttrappe(DAIMON_RT, hub)
lokal = LokalAttrappe(DAIMON_RT, hub)
aktion = AktionAttrappe(DAIMON_RT)
mind = Mind()

P.kapitel("V", "Voraussetzungen und Messstrecke")
for name, ok in (
    ("Mind-Service-Unit vorhanden", MIND_UNIT.is_file()),
    ("Mind-ExecStart aus Unit auflösbar", bool(MIND_CMD)),
    (f"kein systemd-Kürzel bleibt im Mind-ExecStart stehen "
     f"(gefunden: {MIND_REST or 'keins'})", not MIND_REST),
    ("Modul daimon/common/protocol.py vorhanden",
     (TARGET / "daimon/common/protocol.py").is_file()),
    ("Modul daimon/common/taint.py vorhanden",
     (TARGET / "daimon/common/taint.py").is_file()),
    ("Modul daimon/hookbridge/bridge.py vorhanden",
     (TARGET / "daimon/hookbridge/bridge.py").is_file()),
    ("Modul daimon/auth/preview.py vorhanden",
     (TARGET / "daimon/auth/preview.py").is_file()),
    ("Modul daimon/mind/router.py vorhanden",
     (TARGET / "daimon/mind/router.py").is_file()),
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

# Selbsttest der Messstrecke, bevor der Prüfling läuft.
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
# Derselbe Selbsttest für den Weg, den der Mind laut Unit wirklich nimmt.
# Ohne ihn wäre eine grüne Markierungsmessung nicht von einer Messung an
# einem toten Socket zu unterscheiden.
ausg_l, _ = unix_json(DAIMON_RT / "ticket.sock",
                      {"v": 1, "art": "ausgeben", "zweck": "api",
                       "auftrag_hash": selbst_hash})
lokal_ans, _ = unix_json(DAIMON_RT / "lokal.sock",
                         {"v": 1, "art": "anfrage",
                          "ticket": ausg_l.get("ticket", ""),
                          "koerper": selbst_koerper})
P.check("V", "Messstrecke: Lokal-Attrappe nimmt das Ticket an",
        lokal_ans.get("ok"), True)
P.check("V", "Messstrecke: Lokal-Attrappe liefert einen Textblock",
        (lokal_ans.get("antwort") or {}).get("content"),
        [{"type": "text", "text": ANTWORT_KANARIE}])
P.check("V", "Messstrecke: Lokal-Attrappe weist ein verbrauchtes Ticket ab",
        unix_json(DAIMON_RT / "lokal.sock",
                  {"v": 1, "art": "anfrage",
                   "ticket": ausg_l.get("ticket", ""),
                   "koerper": selbst_koerper})[0].get("grund"),
        "ticket_ungueltig")
# Und derselbe Selbsttest fuer `aktion.sock`: der Weg, der in K11 STILL
# bleiben soll. Ein Socket, der schon hier nicht antwortet, koennte dort
# keine Stille belegen.
akt_ans, _ = unix_json(DAIMON_RT / "aktion.sock",
                       {"v": 1, "art": "ausfuehren",
                        "action_id": "messstrecke.selbsttest", "params": {}})
P.check("V", "Messstrecke: Aktions-Attrappe antwortet und zaehlt",
        (akt_ans.get("ok"), aktion.zaehler()), (True, 1))
with aktion.lock:
    aktion.aufrufe.clear()
hub.zuruecksetzen()
with egress.lock:
    egress.aufrufe.clear()
with lokal.lock:
    lokal.aufrufe.clear()
quellen_log()  # Logdatei existiert ab jetzt sicher

mind.start()
P.check("V", "Mind-Socket erscheint", warten(MIND_SOCK, mind.p), True)
if P.rot["V"]:
    print("\nT-3.13b: FEHLGESCHLAGEN — Voraussetzungen fehlen, "
          "keine Scheinmessung.")
    raise SystemExit(1)

z0 = zustand()
P.check("V", "Zustand antwortet und meldet das Testprofil sichtbar",
        (z0.get("ok"), z0.get("testprofil")), (True, True))

# Die Sonden laufen einmal; ihre Messwerte werden den Kriterien einzeln
# zugeordnet. Ein Sondenfehler macht jede zugehörige Prüfung rot, nicht
# grün — „0 Treffer" ist sonst keine Aussage.
proto = probe_json("probe_protocol.py")
P.check("V", "Messsonde Protokoll liefert ein Ergebnis",
        "__sonde_fehler__" not in proto, True)
taint = probe_json("probe_taint.py")
P.check("V", "Messsonde taint.py liefert ein Ergebnis",
        "__sonde_fehler__" not in taint, True)
bridge_marks = probe_json("probe_bridge_marks.py")
P.check("V", "Messsonde Bridge-Markierung liefert ein Ergebnis",
        "__sonde_fehler__" not in bridge_marks, True)
j_vor_preview = journal_marke_fehlt()
rc_prev, out_prev, err_prev = probe("probe_preview.py", VORSCHAU_KANARIE)
try:
    prev = json.loads(out_prev.strip().splitlines()[-1])
except (IndexError, json.JSONDecodeError):
    prev = {"__sonde_fehler__": rc_prev}
P.check("V", "Messsonde Vorschau liefert ein Ergebnis",
        "__sonde_fehler__" not in prev, True)
j_nach_preview = journal_marke_fehlt()
if P.rot["V"]:
    print("\nT-3.13b: FEHLGESCHLAGEN — Messstrecke defekt, "
          "keine Scheinmessung.")
    raise SystemExit(1)

senken_tab = taint.get("senken", {})

P.kapitel("1", "vier Marken, und from_wire macht nackte Werte tainted")
P.check("1", "genau die vier Marken des Vertrags",
        proto.get("marken"), VIER_MARKEN)
P.check("1", "from_wire(nackter Wert) wird tainted — Vorgabe ist Misstrauen",
        proto.get("nackt"), ["tainted", "ROHKANARIE-SONDE"])
P.check("1", "loads eines nackten Feldes wird tainted",
        proto.get("loads_nackt"), ["tainted", "NACKTKANARIE"])
P.check("1", "Positivkontrolle: markierter Drahtwert behält seine Marke",
        proto.get("draht"), ["user_ptt", "DRAHTKANARIE"])

P.kapitel("2", "rohe Zeichenkette an der Senke: tainted + protokolliert, "
          "kein Wurf")
basis = hub.zaehler()
ebasis = lokal.zaehler()
j0 = journal_marke_fehlt()
ans2, roh2 = frage(f"erkläre mir {FRAGE_KANARIE} äöü", marke=None)
P.check("2", "rohe Anfrage (ohne marke) wird beantwortet — kein Wurf",
        (ans2.get("ok"), ans2.get("weg"), ans2.get("durchgang"),
         ans2.get("api")),
        (True, "api", 2, True))
P.check("2", "und gilt als tainted", ans2.get("marke"), "tainted")
P.check("2", "sie kostet genau ein Ticket und einen Aufruf",
        (hub.zaehler(), lokal.zaehler()),
        ((basis[0] + 1, basis[1] + 1), ebasis + 1))
P.check("2", "Positivkontrolle: die Antwort trägt die Frage nicht als "
        "Wurfspur, sondern als Text",
        ANTWORT_KANARIE in str(ans2.get("antwort", "")), True)
marke_fehlt_mind = ((journal_marke_fehlt() - j0 > 0
                     if j0 >= 0 and journal_marke_fehlt() >= 0 else False)
                    or "marke_fehlt" in mind.logtext())
P.check("2", "Positivkontrolle: marke_fehlt ist protokolliert (Journal oder "
        "stderr-Rückfall)", marke_fehlt_mind, True)
ans2b, roh2b = frage("wie spät ist es", marke=None)
P.check("2", "rohe Anfrage an Durchgang 1: Absage (tainted), kein Wurf am "
        "Draht", (ans2b.get("ok"), ans2b.get("grund")),
        (False, "marke_verboten"))
P.check("2", "der Daemon lebt nach dem rohen Aufruf",
        zustand().get("ok"), True)
ans2c, _ = frage("wie spät ist es", marke="user_ptt")
P.check("2", "Positivkontrolle: dieselbe Frage mit Marke gelingt lokal",
        (ans2c.get("ok"), ans2c.get("weg")), (True, "lokal"))

P.kapitel("3", "markiertes Material an verbotener Senke wirft — je Senke "
          "erlaubt und verboten")
# Durchgang 1: erlaubt user_ptt/trusted, verboten user_audio/tainted.
ans3, _ = frage("wie spät ist es", marke="user_ptt")
P.check("3", "durchgang1 erlaubt user_ptt",
        (ans3.get("ok"), ans3.get("weg")), (True, "lokal"))
P.check("3", "Positivkontrolle: die Auskunft trägt eine Uhrzeit",
        bool(re.search(r"\d{1,2}:\d{2}", str(ans3.get("antwort", "")))),
        True)
ans3, _ = frage("wie spät ist es", marke="trusted")
P.check("3", "durchgang1 erlaubt trusted", ans3.get("ok"), True)
ans3, _ = frage("wie spät ist es", marke="user_audio")
P.check("3", "durchgang1 verwirft user_audio",
        (ans3.get("ok"), ans3.get("grund")), (False, "marke_verboten"))
ans3, _ = frage("wie spät ist es", marke="tainted")
P.check("3", "durchgang1 verwirft tainted",
        (ans3.get("ok"), ans3.get("grund")), (False, "marke_verboten"))
# Durchgang 2: die Tabelle lässt alle vier Marken zu — es gibt dort keinen
# verbotenen Fall (Feststellung, kein Freifahrtschein: siehe Bericht).
P.info("durchgang2 hat laut Tabelle keinen verbotenen Fall; geprüft wird, "
       "dass alle vier Marken ankommen und die Ausgabe tainted bleibt.")
for marke in ("user_ptt", "user_audio", "trusted", "tainted"):
    ans3, _ = frage(f"erkläre mir {FRAGE_KANARIE}", marke=marke)
    P.check("3", f"durchgang2 erlaubt {marke} und antwortet tainted",
            (ans3.get("ok"), ans3.get("marke")), (True, "tainted"))
# auth_vorschau: an der Sonde gemessen (Verhalten, nicht Tabelle).
P.check("3", "auth_vorschau verwirft user_audio mit SenkenFehler",
        prev.get("user_audio"), "senkenfehler")
P.check("3", "auth_vorschau erlaubt tainted (escapt — K10 misst das)",
        prev.get("tainted_ok"), True)
P.check("3", "auth_vorschau erlaubt user_ptt", prev.get("user_ptt_ok"), True)
P.check("3", "auth_vorschau erlaubt trusted", prev.get("trusted_ok"), True)
P.check("3", "Positivkontrolle: auth_vorschau wirft keinen anderen Fehler",
        str(prev.get("user_audio", "")).startswith("anderer_fehler"), False)
# Die übrigen Senken stehen an der Tabelle (Verhalten: T-3.9 bleibt per
# K13 grün; tts_auf_anfrage/audit_klartext haben in P3 keinen eigenen
# Prüfpfad in diesem Prüfstand — Feststellung im Bericht).
P.check("3", "Messsonde: die Tabelle kennt neun Senken",
        len(senken_tab), 9)
P.check("3", "Tabelle tts_auf_anfrage: alle vier Marken erlaubt",
        senken_tab.get("tts_auf_anfrage"), VIER_MARKEN)
# `user_ptt` stand hier bis zum 26.08. mit drin und fror damit die unsichere
# Fassung ein. Design §8.3: ungefragt wird ausschliesslich `trusted`
# gesprochen. `user_ptt` ist, was das Mikrofon am gehaltenen Taster aufnimmt
# -- auch Video und Lautsprecher --, und das wird ungefragt nicht vorgelesen.
# Die Zeile hat seit T-3.9 ihren Aufrufer: `hub/sprechtext.aus_vorlage`
# entscheidet ueber `taint.pruefe_senke` statt ueber ein eigenes `if`.
P.check("3", "Tabelle tts_ungefragt: nur trusted",
        senken_tab.get("tts_ungefragt"), ["trusted"])
P.check("3", "Tabelle audit_klartext: tainted ist verboten",
        senken_tab.get("audit_klartext"),
        ["trusted", "user_audio", "user_ptt"])
# L1 ist entschieden (Vertrag §6): der Weg an der ❌-Senke vorbei ist die
# Redaktion als eigene Funktion — Hash und Länge, trusted, kein Klartext.
audit = taint.get("audit", {})
P.check("3", "audit_redigiert liefert trusted mit sha256 und laenge",
        (audit.get("marke"), audit.get("form")), ("trusted", ["laenge", "sha256"]))
P.check("3", "Positivkontrolle: der Hash ist der des Klartexts, die Länge "
        "stimmt", (audit.get("hash_ok"), audit.get("laenge")), (True, 20))
P.check("3", "der Klartext steht nicht in der Redaktion",
        audit.get("klartext_drin"), False)

P.kapitel("4", "tainted an Durchgang 1 wirft — nicht stilles Filtern")
tbasis = hub.zaehler()
qbasis = quellen_log()
bbasis = beobachter_log()
ans4, roh4 = frage("wie spät ist es", marke="tainted")
P.check("4", "tainted mit lokaler Absicht wird abgewiesen",
        (ans4.get("ok"), ans4.get("grund")), (False, "marke_verboten"))
P.check("4", "die Abweisung ist sichtbar, kein stiller Durchlass",
        bool(str(ans4.get("meldung", "")).strip()), True)
P.check("4", "kein Ticket ausgegeben oder eingelöst",
        hub.zaehler(), tbasis)
P.check("4", "keine Quelle wurde angerührt", quellen_log(), qbasis)
P.check("4", "kein Ausführungswerkzeug wurde angerührt",
        beobachter_log(), bbasis)
P.check("4", "der Daemon lebt nach dem Wurf", zustand().get("ok"), True)
ans4b, _ = frage("wie spät ist es", marke="user_ptt")
P.check("4", "Positivkontrolle: dieselbe Frage als user_ptt gelingt",
        ans4b.get("ok"), True)

P.kapitel("5", "Ansteckung: verketten nimmt die strengste Marke")
paare = taint.get("paare", {})
for paar, soll in (("trusted+tainted", "tainted"),
                   ("trusted+user_ptt", "user_ptt"),
                   ("user_ptt+user_audio", "user_audio"),
                   ("user_audio+tainted", "tainted"),
                   ("trusted+trusted", "trusted"),
                   ("user_audio+user_ptt", "user_audio")):
    P.check("5", f"verketten({paar.replace('+', ', ')}) ist {soll}",
            paare.get(paar), soll)
P.check("5", "ein nackter Teil macht die Verkettung tainted",
        paare.get("nackt+trusted"), "tainted")
ans5, _ = frage("welche fenster sind offen", marke="user_ptt")
P.check("5", "Ausgabe eines Durchgangs, der tainted sah (Fenstertitel), "
        "ist tainted", ans5.get("marke"), "tainted")
P.check("5", "Positivkontrolle: der Fenstertitel kommt an",
        FENSTER_KANARIE in str(ans5.get("antwort", "")), True)
ans5b, _ = frage("welche sitzungen sind aktiv", marke="user_ptt")
P.check("5", "sitzung bleibt trusted", ans5b.get("marke"), "trusted")
P.check("5", "Positivkontrolle: die Sitzungskennung kommt an",
        SITZUNG_KANARIE in str(ans5b.get("antwort", "")), True)
P.check("5", "Herkunft schlägt Komponente: der Projektname (tainted, vom "
        "Broker) fehlt in der trusted-Auskunft",
        PROJEKT_KANARIE in str(ans5b.get("antwort", "")), False)
ans5c, _ = frage(f"erkläre mir {FRAGE_KANARIE}", marke="trusted")
P.check("5", "selbst eine trusted Anfrage bekommt eine tainted "
        "Modellausgabe", (ans5c.get("ok"), ans5c.get("marke")),
        (True, "tainted"))

P.kapitel("6", "die Marke überlebt IPC — dumps/loads und echter Socket")
P.check("6", "dumps trägt die Marke als Drahtobjekt",
        proto.get("dumps_reason"),
        {"__mark__": "user_audio", "value": "GRUNDKANARIE äöü"})
P.check("6", "loads gibt Marke und Wert unverändert zurück (mit Umlauten)",
        proto.get("loads"), ["user_audio", "GRUNDKANARIE äöü"])
# Echter Socket, Richtung Prüfling → Reviewer: die Sonde sendet einen
# markierten Wert, der Prüfstand liest ihn roh.
ipc_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
ipc_pfad = RT / "ipc-sonde.sock"
ipc_pfad.unlink(missing_ok=True)
ipc_srv.bind(str(ipc_pfad))
ipc_srv.listen(1)
sonde = subprocess.Popen(
    [str(PYTHON), "-B", "-P", str(PROBEN / "probe_socket.py"), "client",
     str(ipc_pfad), "user_audio", f"SOCK-KANARIE {FRAGE_KANARIE}"],
    env=basis_umgebung(), cwd=TARGET,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
prozesse.append(sonde)
ipc_srv.settimeout(15)
try:
    conn, _ = ipc_srv.accept()
    with conn:
        roh6 = conn.makefile("rb").readline(1 << 20)
except OSError:
    roh6 = b""
finally:
    ipc_srv.close()
try:
    draht6 = json.loads(roh6)
except (UnicodeDecodeError, json.JSONDecodeError):
    draht6 = None
P.check("6", "echter Socket: die Marke kommt als Drahtobjekt an",
        isinstance(draht6, dict) and draht6.get("__mark__"), "user_audio")
P.check("6", "Positivkontrolle: der Wert selbst kommt über denselben "
        "Socket an",
        isinstance(draht6, dict)
        and FRAGE_KANARIE in str(draht6.get("value", "")), True)
# Echter Socket, Richtung Reviewer → Prüfling: der Prüfstand sendet, die
# Sonde liest die Marke mit from_wire wieder aus.
ipc2_pfad = RT / "ipc-sonde2.sock"
ipc2_pfad.unlink(missing_ok=True)
sonde2 = subprocess.Popen(
    [str(PYTHON), "-B", "-P", str(PROBEN / "probe_socket.py"), "server",
     str(ipc2_pfad)],
    env=basis_umgebung(), cwd=TARGET,
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
prozesse.append(sonde2)
bereit = warten(ipc2_pfad, sonde2)
P.check("6", "Messsonde: Gegenstelle ist bereit", bereit, True)
if bereit:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(10)
    c.connect(str(ipc2_pfad))
    c.sendall(json.dumps({"__mark__": "user_ptt",
                          "value": "GEGEN-KANARIE äöü"},
                         ensure_ascii=False).encode() + b"\n")
    c.close()
    try:
        out6, _ = sonde2.communicate(timeout=15)
        gelesen = json.loads(out6.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, IndexError, json.JSONDecodeError):
        gelesen = {}
    P.check("6", "echter Socket: from_wire liest die Marke am anderen "
            "Ende wieder aus", gelesen.get("marke"), "user_ptt")
    P.check("6", "Positivkontrolle: der Wert (mit Umlauten) kommt an",
            gelesen.get("wert"), "GEGEN-KANARIE äöü")

P.kapitel("7", "Hook-Freitext ist tainted, hook_event_name trusted — am "
          "echten Bridge-Pfad")
hooks = HookSockAttrappe(HOOK_SOCK)
bridge_starten()
token_pfad = DAIMON_RT / "hook-token"
P.check("7", "Bridge-Token erscheint", warten(token_pfad, bridge_prozess),
        True)
if token_pfad.is_file():
    token = token_pfad.read_text(encoding="utf-8").strip()
    payload = {"hook_event_name": "PostToolUse",
               "session_id": f"s-{BRIDGE_KANARIE}",
               "cwd": f"/tmp/{BRIDGE_KANARIE}-cwd",
               "tool_name": "Bash",
               "message": f"msg {BRIDGE_KANARIE}",
               "prompt": f"prompt {BRIDGE_KANARIE} äöü",
               "last_assistant_message": f"lam {BRIDGE_KANARIE}",
               "error": f"err {BRIDGE_KANARIE}",
               "tool_input": {"command": "ls", "description": "d"},
               "pid": os.getpid()}
    code, text = bridge_post(payload, token)
    P.check("7", "Messsonde: die Bridge nimmt die Nutzlast an",
            (code, text), (200, "ok"))
    ende = time.monotonic() + 5
    while hooks.anzahl() < 1 and time.monotonic() < ende:
        time.sleep(0.05)
    zeile7 = hooks.letzte()
    try:
        bus = json.loads(zeile7)
        nutzlast = bus.get("payload", {}) if isinstance(bus, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        nutzlast = {}
    P.check("7", "Positivkontrolle: eine Zeile kam am Hub-Socket an und "
            "ist lesbar", bool(nutzlast), True)
    # Drahtform am Hook-Bus (Vertrag §7): die Nutzlast bleibt roh — der Hub
    # liest dort rohe Werte, und T-0.11 ist eingefroren —, die Marken
    # wandern als Begleitfeld `__marken__` mit.
    marken7 = nutzlast.get("__marken__", {})
    marken7 = marken7 if isinstance(marken7, dict) else {}
    for feld, kanarie in (("message", f"msg {BRIDGE_KANARIE}"),
                          ("prompt", f"prompt {BRIDGE_KANARIE} äöü"),
                          ("last_assistant_message", f"lam {BRIDGE_KANARIE}"),
                          ("error", f"err {BRIDGE_KANARIE}"),
                          ("cwd", f"/tmp/{BRIDGE_KANARIE}-cwd"),
                          ("tool_name", "Bash")):
        P.check("7", f"{feld} ist als tainted markiert",
                marken7.get(feld), "tainted")
        P.check("7", f"Positivkontrolle: der Wert von {feld} kommt roh an",
                nutzlast.get(feld), kanarie)
    P.check("7", "hook_event_name ist als trusted markiert",
            marken7.get("hook_event_name"), "trusted")
    P.check("7", "Positivkontrolle: der Ereignisname kommt roh an",
            nutzlast.get("hook_event_name"), "PostToolUse")
    P.check("7", "__marken__ deckt jedes Nutzlastfeld ab — kein Feld ohne "
            "Marke", sorted(marken7),
            sorted(k for k in nutzlast if k != "__marken__"))
    # Die Ausnahmeliste selbst (Sonde): trusted gilt nur, weil die
    # Aufzählung geschlossen ist — ein erfundener Name fällt auf tainted
    # zurück.
    P.check("7", "Ausnahmeliste: ein erfundener hook_event_name ist wieder "
            "tainted", bridge_marks.get("erfunden_event"), "tainted")
    P.check("7", "Ausnahmeliste: Freitext ist an der Funktion tainted",
            bridge_marks.get("erfunden_freitext"), "tainted")
    P.check("7", "Positivkontrolle: ein gültiger Ereignisname ist an der "
            "Funktion trusted", bridge_marks.get("gueltig_event"), "trusted")

P.kapitel("8", "user_audio: Durchgang 1 nicht, Gedächtnis nicht")
tbasis = hub.zaehler()
ans8, _ = frage("wie spät ist es", marke="user_audio")
P.check("8", "user_audio mit lokaler Absicht bleibt marke_verboten",
        (ans8.get("ok"), ans8.get("grund")), (False, "marke_verboten"))
ans8b, _ = frage("mach das fenster zu", marke="user_audio")
P.check("8", "user_audio mit Aktionswunsch bleibt marke_verboten",
        (ans8b.get("ok"), ans8b.get("grund")), (False, "marke_verboten"))
P.check("8", "die Senke steht vor dem Ticket: Zähler unverändert",
        hub.zaehler(), tbasis)
ans8c, _ = frage(f"erkläre mir {FRAGE_KANARIE}", marke="user_audio")
P.check("8", "user_audio darf eine Frage beantworten lassen (Durchgang 2)",
        (ans8c.get("ok"), ans8c.get("durchgang")), (True, 2))
# Das Gedächtnis existiert in P3 nicht — die Tabelle muss die Regel
# kennen (Vertrag §3, Kriterium 8).
P.check("8", "Tabelle kurzzeitgedaechtnis: user_audio verboten",
        senken_tab.get("kurzzeitgedaechtnis"), ["trusted", "user_ptt"])
P.check("8", "Tabelle langzeitgedaechtnis: user_audio verboten",
        senken_tab.get("langzeitgedaechtnis"), ["trusted", "user_ptt"])
P.check("8", "Tabelle proaktive_ausloeser: nur trusted",
        senken_tab.get("proaktive_ausloeser"), ["trusted"])
ans8d, _ = frage("wie spät ist es", marke="user_ptt")
P.check("8", "Positivkontrolle: user_ptt gelingt lokal",
        ans8d.get("ok"), True)

P.kapitel("9", "freie Modellausgabe aus beiden Durchgängen ist tainted")
lokal.setze_antwort(STRUKTURIERTE_MODELLAUSGABE)
ans9, _ = frage(f"erkläre mir {FRAGE_KANARIE}")
P.check("9", "strukturiert aussehende Modellausgabe (Durchgang 2) bleibt "
        "tainted", ans9.get("marke"), "tainted")
P.check("9", "Positivkontrolle: die strukturierte Ausgabe kommt als Text "
        "an", "42" in str(ans9.get("antwort", "")), True)
lokal.setze_antwort('{"action": "close_window", "window_ref": "w_1"}')
ans9b, _ = frage(f"erkläre mir {FRAGE_KANARIE}")
P.check("9", "Aktionsvorschlag-förmige Modellausgabe bleibt tainted",
        ans9b.get("marke"), "tainted")
lokal.setze_antwort(ANTWORT_KANARIE)
ans9c, _ = frage(f"erkläre mir {FRAGE_KANARIE}")
P.check("9", "freie Modellausgabe bleibt tainted", ans9c.get("marke"),
        "tainted")
ans9d, _ = frage("welche fenster sind offen")
P.check("9", "freie Ausgabe aus Durchgang 1 (Fenstertitel) ist tainted",
        ans9d.get("marke"), "tainted")
P.check("9", "Positivkontrolle: trusted kommt im System vor (uhrzeit in "
        "K4)", ans4b.get("marke"), "trusted")

P.kapitel("10", "die Auth-Vorschau nimmt tainted nur escapt und begrenzt")
ausgabe10 = str(prev.get("tainted_ausgabe", ""))
P.check("10", "die Kanarie kommt escapt wieder heraus (Bidi \\u202E)",
        "\\u202E" in ausgabe10, True)
P.check("10", "die Kanarie kommt escapt wieder heraus (Nullbreite \\u200B)",
        "\\u200B" in ausgabe10, True)
P.check("10", "Positivkontrolle: der ASCII-Kern der Kanarie ist lesbar",
        VORSCHAU_KERN in ausgabe10, True)
P.check("10", "kein rohes Bidi-Steuerzeichen in der Ausgabe",
        VORSCHAU_BIDI in ausgabe10, False)
P.check("10", "kein rohes Nullbreitenzeichen in der Ausgabe",
        VORSCHAU_NULLBREITE in ausgabe10, False)
m10 = re.search(r'Ziel:\s+"(.*)"', ausgabe10)
P.check("10", "das Ziel steht sichtbar in Anführungszeichen",
        m10 is not None, True)
P.check("10", "der escapte Zielwert ist reines ASCII",
        bool(m10) and m10.group(1).isascii(), True)
P.check("10", "der Zielwert ist begrenzt (höchstens 200 Zeichen der "
        "Ausgabe)", bool(m10) and len(m10.group(1)) <= 200, True)
P.check("10", "die Kürzung ist sichtbar markiert",
        bool(m10) and m10.group(1).endswith("...(gekuerzt)"), True)
P.check("10", "rohe Zeichenkette: dieselbe escapte Ausgabe wie tainted, "
        "kein Wurf",
        (prev.get("roh_ok"), prev.get("roh_ausgabe")),
        (True, ausgabe10))
marke_fehlt_prev = ((j_nach_preview - j_vor_preview > 0
                     if j_vor_preview >= 0 and j_nach_preview >= 0
                     else False) or "marke_fehlt" in err_prev)
P.check("10", "Positivkontrolle: der rohe Aufruf ist protokolliert "
        "(marke_fehlt)", marke_fehlt_prev, True)

P.kapitel("11", "der Aktionsweg: ohne Absichtsmarke abgelehnt, ohne Ziel "
          "Rückfrage, mit beidem werkzeugfähig — und aktion.sock bleibt "
          "still")
# WARUM dieses Kriterium heute anders lautet als bis zum 30.08.
#
# Bis dahin stand hier EINE Erwartung: ein Aktionswunsch mit benanntem Ziel
# ist `weg: "abgelehnt"`. Das war die Zusage der PHASE 3 — "Sprache, Egress,
# Markierungsverfolgung, KEINE Aktionen". Sie galt, solange es keinen
# werkzeugfähigen Weg gab.
#
# Seit T-4.16 K1 gibt es ihn: `Mind.frage_werkzeug` (daemon.py) reicht einen
# `tool_use`-Block über `aktion.sock` an den Koordinator, und
# `router.py:548-586` wählt dafür `weg: "aktion"`. Die alte Erwartung wäre
# heute nicht mehr die Zusage, sondern ihr Nachhall — rot, ohne dass etwas
# kaputt ist.
#
# Aufgeweicht wird dabei NICHTS: aus einer Erwartung werden vier, und die
# teure Zusage — dass eine Ablehnung nichts kostet und dass keine Aktion
# entsteht, solange das Modell kein Werkzeug ruft — wird hier zum ersten Mal
# GEMESSEN statt vorausgesetzt. Gemessen am echten Mind über echte Sockets
# (Reviewer-Attrappen für Hub, Modell und Koordinator), 31.08.:
#
#   tainted    + "mach das fenster zu" -> ok:false, marke_verboten
#   user_audio + dasselbe              -> ok:false, marke_verboten + Hinweis
#   user_ptt   + "mach das"            -> ok:true,  weg "rueckfrage"
#   user_ptt   + "mach das fenster zu" -> ok:true,  weg "aktion"
#   Summe über die vier: 1 Ticket, 1 Modellaufruf, 0 Aufrufe an aktion.sock.
#
# Der Weg heißt `aktion`, weil er werkzeugfähig ist — die Aktion entsteht
# erst, wenn das Modell ein Werkzeug ruft. Katalog, Policy und Consent liegen
# dahinter, in der Kette, nicht hier.

# (a) Ohne Absichtsmarke: abgelehnt, und die Ablehnung ist kostenfrei.
for marke11 in ("tainted", "user_audio"):
    tbasis = hub.zaehler()
    ebasis = lokal.zaehler()
    abasis = aktion.zaehler()
    bbasis = beobachter_log()
    ans, _ = frage("mach das fenster zu", marke=marke11)
    P.check("11", f"{marke11}: der Aktionswunsch wird abgelehnt",
            (ans.get("ok"), ans.get("grund")), (False, "marke_verboten"))
    P.check("11", f"{marke11}: die Ablehnung kostet kein Ticket",
            hub.zaehler(), tbasis)
    P.check("11", f"{marke11}: die Ablehnung kostet keinen Modellaufruf",
            lokal.zaehler(), ebasis)
    P.check("11", f"{marke11}: aktion.sock bekommt keinen Aufruf",
            aktion.zaehler(), abasis)
    P.check("11", f"{marke11}: kein Ausführungswerkzeug wurde angerührt",
            beobachter_log(), bbasis)
# T-4.19: nur wer wirklich gesprochen hat, erfährt WARUM — und die
# Rückmeldung ist die kuratierte Vorlage, nicht Material aus der Äußerung.
ans11a, _ = frage("mach das fenster zu", marke="user_audio")
P.check("11", "user_audio bekommt den Absichtsmarken-Hinweis, trusted",
        (ans11a.get("antwort"), ans11a.get("marke")),
        ("Fuer eine Aktion brauche ich eine Absichtsmarke — bitte "
         "Push-to-Talk druecken.", "trusted"))
ans11a2, _ = frage("mach das fenster zu", marke="tainted")
P.check("11", "tainted bekommt ihn NICHT — injiziertem Text sagt niemand, "
        "wie er eskaliert", ans11a2.get("antwort"), None)

# (b) Mit Marke, ohne benanntes Ziel: Rückfrage, kostenfrei (Design 5.2 —
# ein Fürwort wird nicht aus dem Kontext aufgelöst).
tbasis = hub.zaehler()
ebasis = lokal.zaehler()
abasis = aktion.zaehler()
bbasis = beobachter_log()
ans11, _ = frage("mach das")
P.check("11", "weg ist rueckfrage", ans11.get("weg"), "rueckfrage")
P.check("11", "absicht ist aktion", ans11.get("absicht"), "aktion")
P.check("11", "die Antwortform des Vertrags, Wort für Wort",
        ans11.get("antwort"), RUECKFRAGE_FORM["antwort"])
P.check("11", "marke trusted, api false, ok true",
        (ans11.get("marke"), ans11.get("api"), ans11.get("ok")),
        ("trusted", False, True))
P.check("11", "Rückfrage: kein Ticket ausgegeben oder eingelöst",
        hub.zaehler(), tbasis)
P.check("11", "Rückfrage: kein Aufruf an der Modell-Attrappe",
        lokal.zaehler(), ebasis)
P.check("11", "Rückfrage: aktion.sock bekommt keinen Aufruf",
        aktion.zaehler(), abasis)
P.check("11", "Rückfrage: kein Ausführungswerkzeug wurde angerührt",
        beobachter_log(), bbasis)

# (c) Mit Marke UND Ziel: der werkzeugfähige Weg, genau ein Ticket, genau
# ein Modellaufruf — nicht mehr.
tbasis = hub.zaehler()
ebasis = lokal.zaehler()
abasis = aktion.zaehler()
bbasis = beobachter_log()
ans11b, _ = frage("mach das fenster zu")
P.check("11", "mit Marke und Ziel: weg ist aktion, absicht ist aktion, "
        "ok true",
        (ans11b.get("weg"), ans11b.get("absicht"), ans11b.get("ok")),
        ("aktion", "aktion", True))
P.check("11", "genau ein Ticket ausgegeben und eingelöst",
        hub.zaehler(), (tbasis[0] + 1, tbasis[1] + 1))
P.check("11", "genau ein Modellaufruf", lokal.zaehler(), ebasis + 1)
P.check("11", "kein Ausführungswerkzeug wurde angerührt",
        beobachter_log(), bbasis)

# (d) Die eigene Zusage: solange das Modell KEIN Werkzeug ruft, sieht der
# Koordinator nichts. `aktion.sock` ist gebunden und antwortet (Messstrecke
# in K V) — diese Null ist gemessen, nicht geerbt.
P.check("11", "das Modell rief kein Werkzeug: aktion.sock bekam keinen "
        "Aufruf", aktion.zaehler(), abasis)
# Positivkontrolle: dieselbe Frage, aber das Modell ruft ein Werkzeug aus
# dem freigegebenen Katalog. Jetzt MUSS genau ein Aufruf ankommen — sonst
# wäre die Null oben die Null einer kaputten Vorrichtung.
lokal.setze_bloecke([{"type": "tool_use", "id": "tu-t313b",
                      "name": "media_playpause", "input": {}}])
ans11d, _ = frage("mach die musik an")
lokal.setze_bloecke(None)
P.check("11", "Positivkontrolle: mit tool_use sieht aktion.sock genau "
        "einen Aufruf", aktion.zaehler(), abasis + 1)
P.check("11", "Positivkontrolle: der Koordinator bekommt die action_id aus "
        "dem Katalog, nicht den Werkzeugnamen",
        (aktion.letzte().get("art"), aktion.letzte().get("action_id")),
        ("ausfuehren", "media.playpause"))
P.check("11", "Positivkontrolle: der Router meldet das Verdikt des "
        "Koordinators, nicht sein eigenes",
        (ans11d.get("weg"), ans11d.get("action_id"),
         ans11d.get("ausgefuehrt")),
        ("aktion", "media.playpause", False))

tbasis = hub.zaehler()
ans11c, _ = frage(f"erkläre mir {FRAGE_KANARIE}")
P.check("11", "Positivkontrolle: eine Inhaltsfrage kostet danach genau "
        "ein Ticket",
        (ans11c.get("ok"), hub.zaehler()),
        (True, (tbasis[0] + 1, tbasis[1] + 1)))

P.kapitel("12", "Markierungsverlust-Mutanten an jeder Grenze")
P.info("Die Zurückweisung der Mutanten prüft meta.sh T-3.13b an je einer "
       "Mutation je Grenze: IPC-Serialisierung, Durchgang 1 (user_audio), "
       "Hook-Bridge, freie Modellausgabe, Auth-Vorschau — dazu "
       "Aktionsergebnis und Verkettung. Die Zuordnung Mutation → rotem "
       "Kriterium belegt der Bericht (tests/evidence/T-3.13b-bericht.md). "
       "Im Fixture-Lauf dieser Mutanten muss genau die zugehörige Prüfung "
       "rot werden.")

P.kapitel("13", "die acht eingefrorenen Prüfstände bleiben grün, dazu "
          "pytest")
if FIXTURE:
    P.info("Fixture/Mutanten sind keine vollständigen Arbeitsbäume; "
           "K13 läuft nur im Arbeitsbaumlauf.")
else:
    kind_env = os.environ.copy()
    kind_env.pop("DAIMON_FIXTURE", None)
    # T-0.7 und T-0.11 gehören dazu: sie messen die Bridge-Hub-Kette, die
    # dieser Task anfasst — ohne sie wäre der Drahtform-Bruch am Hook-Bus
    # in diesem Lauf gar nicht aufgefallen.
    for task in ("T-0.7", "T-0.11", "T-3.8", "T-3.9", "T-3.10", "T-3.11",
                 "T-3.12", "T-3.13"):
        rc = subprocess.run([str(REPO / "tests/verify" / f"{task}.sh")],
                            cwd=REPO, env=kind_env).returncode
        P.check("13", f"{task} bleibt einzeln vollständig grün", rc, 0)
        # Aufräumphase: zwei Prüfstände nie Rücken an Rücken.
        subprocess.run(["systemctl", "--user", "reset-failed"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    # --tb=no: ein roter Test darf dem Reviewer keinen Quelltext zeigen.
    r = subprocess.run([str(PYTHON), "-B", "-P", "-m", "pytest", "-q",
                        "--tb=no"],
                       cwd=REPO, env=kind_env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=900)
    P.check("13", "pytest ist im Arbeitsbaum grün", r.returncode, 0)
    # Aufräumphase des eigenen Laufes: nichts darf übrig bleiben.
    mind.stop()
    subprocess.run(["systemctl", "--user", "reset-failed"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    P.check("13", "Prozesszählung daimon.face.tts: vorher/nachher gleich",
            pgrep_zaehler(r"daimon\.face\.tts"), pgrep_tts_vorher)
    P.check("13", "Prozesszählung daimon.gpu.stt: vorher/nachher gleich",
            pgrep_zaehler(r"daimon\.gpu\.stt"), pgrep_stt_vorher)

print("\n=== Ergebnis je Kriterium ===")
for k in [str(x) for x in range(1, 14)]:
    print(f"  K{k}: {P.n[k]} Prüfungen, {P.rot[k]} rot")
print(f"  Voraussetzungen: {P.n['V']} Prüfungen, {P.rot['V']} rot")
gesamt = sum(P.n.values())
rot = sum(P.rot.values())
print(f"T-3.13b: {gesamt} Prüfungen, {rot} rot")
print(f"  Prozesszählung vorher: tts={pgrep_tts_vorher} "
      f"stt={pgrep_stt_vorher}")
raise SystemExit(1 if P.fail else 0)
