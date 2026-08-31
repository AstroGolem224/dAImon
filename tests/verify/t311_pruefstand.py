#!/usr/bin/env python3
"""Blinder Laufzeit-Prüfstand für T-3.11.

Keine Anfrage verlässt 127.0.0.1. Negative Aussagen haben im selben Lauf eine
Positivkontrolle am gleichen Messpunkt. Der Prüfling wird aus TARGET geladen;
die Gegenstelle, Zertifikate und Angriffsdaten gehören dem Reviewer.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
TARGET = Path(sys.argv[2]).resolve()
FIXTURE = bool(os.environ.get("DAIMON_FIXTURE"))
PYTHON = REPO / ".venv/bin/python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
VERIFY = REPO / "tests/verify"
TOKEN_A = "sk-ant-t311-prueftoken-A-7f93c4"
TOKEN_B = "sk-ant-t311-prueftoken-B-28ab61"
KANARIE = "T311-KOERPER-KANARIE-9d23a1"
ANTWORT_KANARIE = "T311-ANTWORT-KANARIE-6b80fe"


class Pruefstand:
    def __init__(self) -> None:
        self.n = defaultdict(int)
        self.rot = defaultdict(int)
        self.fail = False
        self.auslegungen: list[str] = []

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


def abbruch(art, wert, spur) -> None:
    """Ein Absturz ist KEIN rotes Kriterium und darf nicht als eines
    durchgehen.

    Bis heute stuerzte dieser Pruefstand in Kapitel 4 mit
    ConnectionResetError ab. Im umgebenden T-3.13b-Lauf erschien das als ein
    einziger roter Punkt ("bleibt einzeln vollstaendig gruen: erwartet 0, war
    1") -- ununterscheidbar von einem echten Befund, und die Kapitel 5 bis 17
    waren dabei ueberhaupt nicht gefahren.

    Drei Dinge trennen den Abbruch ab jetzt vom Ergebnis: die Ueberschrift
    sagt ABGEBROCHEN statt "Ergebnis je Kriterium", die Zeile nennt die
    Stelle, und der Rueckgabewert ist 3 -- nicht 1 (rot) und nicht 0 (gruen).
    Wer nur den Rueckgabewert sieht, sieht damit trotzdem den Unterschied.
    """
    import traceback
    traceback.print_exception(art, wert, spur)
    letzte = traceback.extract_tb(spur)[-1] if spur else None
    stelle = f"{letzte.filename}:{letzte.lineno}" if letzte else "(unbekannt)"
    gesamt = sum(P.n.values())
    rot = sum(P.rot.values())
    print("\n=== ABGEBROCHEN — keine Bilanz ===", flush=True)
    print(f"  Stelle: {stelle}", flush=True)
    print(f"  Grund:  {art.__name__}: {wert}", flush=True)
    print(f"  Gefahren bis dahin: {gesamt} Pruefungen, {rot} rot.", flush=True)
    print("  Alles dahinter ist UNGEFAHREN — weder gruen noch rot.", flush=True)
    print(f"T-3.11: ABGEBROCHEN nach {gesamt} Pruefungen "
          f"(Rueckgabewert 3, nicht 1)", flush=True)
    sys.stdout.flush()
    # Selbst aufraeumen und hart raus: `os._exit` ueberspringt die
    # atexit-Registrierung, deshalb steht der Aufruf hier -- genau einmal.
    try:
        aufraeumen()
    except Exception:  # noqa: BLE001 -- das Aufraeumen darf den Bericht nicht fressen
        traceback.print_exc()
    os._exit(3)


sys.excepthook = abbruch
laufzeit_basis = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "daimon"
laufzeit_basis.mkdir(parents=True, exist_ok=True)
RT = Path(tempfile.mkdtemp(prefix="t311-", dir=laufzeit_basis))
CONFIG = RT / "config"
STATE = RT / "state"
RUNTIME = RT / "runtime"
DAIMON_RT = RUNTIME / "daimon"
CREDS = RT / "credentials"
for d in (CONFIG / "daimon", STATE / "daimon", DAIMON_RT, CREDS):
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)

prozesse: list[subprocess.Popen] = []
temporare_units: list[str] = []
eigene_socketpfade: list[Path] = []
manager_proxy_original: dict[str, str] | None = None
unit_home = Path.home() / ".config/systemd/user"
unit_home.mkdir(parents=True, exist_ok=True)


def aufraeumen() -> None:
    manager_proxy_wiederherstellen()
    for u in reversed(temporare_units):
        subprocess.run(["systemctl", "--user", "stop", u], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        for p in (unit_home / u, unit_home / f"{u}.d"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    if temporare_units:
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "--user", "reset-failed", *temporare_units],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for pfad in eigene_socketpfade:
        # Nur Pfade, die unmittelbar vor unserem Start nachweislich frei waren.
        try:
            if pfad.is_socket():
                pfad.unlink()
        except OSError:
            pass
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


def manager_proxy_wiederherstellen() -> None:
    global manager_proxy_original
    if manager_proxy_original is None:
        return
    namen = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    subprocess.run(["systemctl", "--user", "unset-environment", *namen],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if manager_proxy_original:
        subprocess.run(["systemctl", "--user", "set-environment", *(
            f"{k}={v}" for k, v in manager_proxy_original.items())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    manager_proxy_original = None


def warten(pfad: Path, prozess: subprocess.Popen | None = None, sekunden: float = 8) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if pfad.exists():
            return True
        if prozess is not None and prozess.poll() is not None:
            return False
        time.sleep(0.03)
    return False


def unix_roh(pfad: Path, roh: bytes, timeout: float = 5) -> bytes:
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
    return json.loads(antwort), antwort


class Attrappe(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, tls: bool = False, cert: Path | None = None, key: Path | None = None):
        super().__init__(("127.0.0.1", 0), Handler)
        self.empfangen: list[bytes] = []
        self.antwort = (b'{"id":"lokal","content":[{"type":"text","text":"'
                        + ANTWORT_KANARIE.encode() + b'"}],"stop_reason":"end_turn"}')
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert), str(key))
            self.socket = ctx.wrap_socket(self.socket, server_side=True)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        art = "https" if isinstance(self.socket, ssl.SSLSocket) else "http"
        return f"{art}://127.0.0.1:{self.server_port}"

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(2)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        self.server.empfangen.append(body)  # type: ignore[attr-defined]
        antwort = self.server.antwort  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(antwort)))
        self.end_headers()
        self.wfile.write(antwort)

    def log_message(self, _fmt: str, *_args) -> None:
        pass


def zertifikat() -> tuple[Path, Path]:
    cert, key = RT / "localhost.crt", RT / "localhost.key"
    r = subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    P.check("V", "selbst ausgestelltes TLS-Zertifikat erzeugt", r.returncode, 0)
    return cert, key


def echtzeit_sprungtreiber() -> tuple[Path, Path]:
    """Reviewer-eigener Treiber: verschiebt nur CLOCK_REALTIME, nie MONOTONIC."""
    quelle = RT / "echtzeit_sprung.c"
    bibliothek = RT / "echtzeit_sprung.so"
    offset = RT / "echtzeit_offset"
    offset.write_text("0", encoding="ascii")
    quelle.write_text(r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
int clock_gettime(clockid_t id, struct timespec *ts) {
    static int (*echt)(clockid_t, struct timespec *);
    if (!echt) echt = dlsym(RTLD_NEXT, "clock_gettime");
    int rc = echt(id, ts);
    if (rc == 0 && id == CLOCK_REALTIME) {
        const char *p = getenv("DAIMON_T311_REALTIME_OFFSET");
        if (p) { FILE *f = fopen(p, "r"); long x = 0;
            if (f) { if (fscanf(f, "%ld", &x) == 1) ts->tv_sec += x; fclose(f); }
        }
    }
    return rc;
}
''', encoding="utf-8")
    r = subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-o", str(bibliothek),
                        str(quelle), "-ldl"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    P.check("V", "Echtzeit-Sprungtreiber kompiliert", r.returncode, 0)
    return bibliothek, offset


def umgebung(token: str = TOKEN_A, *, ziel: str | None = None,
             testprofil: bool = True, proxy: str | None = None,
             ca: Path | None = None) -> dict[str, str]:
    (CREDS / "anthropic-token").chmod(0o600) if (CREDS / "anthropic-token").exists() else None
    (CREDS / "anthropic-token").write_text(token, encoding="utf-8")
    (CREDS / "anthropic-token").chmod(0o400)
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(TARGET),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_RUNTIME_DIR": str(RUNTIME),
        "XDG_CONFIG_HOME": str(CONFIG),
        "XDG_STATE_HOME": str(STATE),
        "CREDENTIALS_DIRECTORY": str(CREDS),
    })
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "HTTP_PROXY", "HTTPS_PROXY",
                 "ALL_PROXY", "NO_PROXY", "DAIMON_EGRESS_ZIEL",
                 "DAIMON_EGRESS_TESTPROFIL", "SSL_CERT_FILE"):
        env.pop(name, None)
    if ziel is not None:
        env["DAIMON_EGRESS_ZIEL"] = ziel
    if testprofil:
        env["DAIMON_EGRESS_TESTPROFIL"] = "1"
    if proxy:
        env["HTTPS_PROXY"] = proxy
    if ca:
        env["SSL_CERT_FILE"] = str(ca)
    return env


def konfig(fenster: float = 60, hoechstens: int = 1000) -> None:
    (CONFIG / "daimon/daimon.toml").write_text(
        f"[egress]\nfenster_s = {fenster}\nhoechstens = {hoechstens}\n",
        encoding="utf-8")


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
    return argv


def rest_kuerzel(argv: list[str]) -> list[str]:
    """Positivkontrolle zu `unit_execstart`: was danach noch nach einem
    systemd-Kuerzel aussieht. Ein stehengebliebenes Kuerzel ist ein Pfad,
    den es nicht gibt -- er darf nie wieder stillschweigend als Modellweg
    durchgehen, sondern muss den Lauf rot faerben."""
    return sorted({m for a in argv for m in re.findall(r"%[a-zA-Z%]", a)})


EGRESS_UNIT = TARGET / "config/systemd/daimon-egress.service"
MIND_UNIT = TARGET / "config/systemd/daimon-mind.service"
SOCKET_UNIT = TARGET / "config/systemd/daimon-egress.socket"
ROTATION = TARGET / "docs/TOKEN-ROTATION.md"
EGRESS_CMD = unit_execstart(EGRESS_UNIT) if EGRESS_UNIT.is_file() else []
MIND_CMD = unit_execstart(MIND_UNIT) if MIND_UNIT.is_file() else []
EGRESS_REST = rest_kuerzel(EGRESS_CMD)
MIND_REST = rest_kuerzel(MIND_CMD)


# Der Name der echten Egress-Testunit aus Kapitel 9 steht HIER und nicht erst
# dort: der Hub muss sie in seiner Ticket-Allowlist haben, und die liest er
# einmal beim Start.
J_NAME = f"daimon-t311-journal-{uuid.uuid4().hex[:8]}.service"


def start_hub() -> subprocess.Popen:
    # NICHT `-m daimon.hub.daemon`, sondern der Starter daneben: er haengt
    # `TICKET_UNITS` auf die Unit dieses Laufs um. Ohne das weist
    # `_horche_einfach` jede Ticketanfrage ab und SCHLIESST die Verbindung
    # ohne Antwort -- der Pruefstand stuerzte darum in Kapitel 4 mit
    # ConnectionResetError ab, und alles dahinter (bis Kapitel 17) blieb
    # ungefahren. Die Begruendung samt Unterscheidungskontrolle steht in
    # t311_hub_lauf.py.
    #
    # stdout in eine Datei, nicht in eine PIPE: die Pipe hier las niemand,
    # 64 KiB Hub-Ausgabe haetten den Hub blockiert.
    logpfad = RT / "hub.log"
    log = logpfad.open("wb")
    cmd = [str(PYTHON), "-B", "-P", str(VERIFY / "t311_hub_lauf.py"),
           str(DAIMON_RT), J_NAME]
    p = subprocess.Popen(cmd, env=umgebung(), cwd=TARGET,
                         stdout=log, stderr=subprocess.STDOUT)
    prozesse.append(p)
    lauscht = warten(DAIMON_RT / "ticket.sock", p)
    log.flush()
    marken = dict(
        z.split("=", 1) for z in logpfad.read_text(errors="replace").splitlines()
        if "=" in z and z.startswith("HUB_"))
    P.check("V", "Hub-Ticketprozess lauscht", lauscht, True)
    P.check("V", "Unit dieses Laufs ist am echten Socket gemessen",
            bool(marken.get("HUB_UNIT")), True)
    P.info(f"Unit dieses Laufs: {marken.get('HUB_UNIT', '(nicht gemessen)')}")
    P.info(f"TICKET_UNITS vor dem Umhaengen: {marken.get('HUB_TICKET_UNITS_VOR', '(nichts)')}")
    # Unterscheidungskontrolle: die Wand steht, sie wird nur umgehaengt.
    # Im Gut-Muster ohne `TICKET_UNITS` gibt es keine Wand -- dort ist "nein"
    # richtig, und der Prueffall haengt deshalb an der Existenz der Liste.
    if marken.get("HUB_TICKET_UNITS_VOR", "None") != "None":
        P.check("4", "Unterscheidungskontrolle: die Ticket-Allowlist sperrt "
                "diese Unit ohne Umhaengen",
                marken.get("HUB_TICKET_SPERRT_MICH"), "ja")
        P.check("4", "Ticket-Allowlist traegt genau die Units dieses Laufs",
                marken.get("HUB_TICKET_UNITS_NACH"),
                repr((marken.get("HUB_UNIT", ""), J_NAME)))
    return p


class EgressProzess:
    def __init__(self, env: dict[str, str], name: str):
        self.sock = DAIMON_RT / f"egress-{name}.sock"
        self.logpfad = RT / f"egress-{name}.log"
        self.log = self.logpfad.open("wb")
        cmd = ["systemd-socket-activate", "-l", str(self.sock)]
        for n in ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH",
                  "PYTHONDONTWRITEBYTECODE", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME",
                  "XDG_STATE_HOME", "CREDENTIALS_DIRECTORY", "DAIMON_EGRESS_ZIEL",
                  "DAIMON_EGRESS_TESTPROFIL", "HTTPS_PROXY", "SSL_CERT_FILE",
                  "LD_PRELOAD", "DAIMON_T311_REALTIME_OFFSET"):
            if n in env:
                cmd += ["-E", n]
        cmd += EGRESS_CMD
        self.p = subprocess.Popen(cmd, env=env, cwd=TARGET, stdout=self.log,
                                  stderr=subprocess.STDOUT)
        prozesse.append(self.p)
        P.check("V", f"Egress-Socket {name} lauscht", warten(self.sock, self.p), True)

    def request(self, obj: dict | None = None, roh: bytes | None = None) -> tuple[dict, bytes]:
        if roh is None:
            roh = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        ans = unix_roh(self.sock, roh)
        try:
            return json.loads(ans), ans
        except json.JSONDecodeError:
            return {}, ans

    def pid(self) -> int | None:
        kinder = Path(f"/proc/{self.p.pid}/task/{self.p.pid}/children")
        for _ in range(100):
            try:
                ids = [int(x) for x in kinder.read_text().split()]
                if ids:
                    return ids[-1]
            except OSError:
                pass
            time.sleep(0.03)
        # systemd-socket-activate führt den Dienst je nach Version per exec
        # im eigenen Prozess aus. Dann ist die Launcher-PID der Dienst selbst.
        try:
            cmdline = Path(f"/proc/{self.p.pid}/cmdline").read_bytes()
        except OSError:
            return None
        return self.p.pid if b"daimon.brokers.egress" in cmdline else None

    def stop(self) -> None:
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(3)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.log.flush()

    def logs(self) -> bytes:
        self.log.flush()
        return self.logpfad.read_bytes()


def hash_body(raw: bytes) -> str:
    koerper = json.loads(raw)
    kanonisch = json.dumps(
        koerper, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(kanonisch).hexdigest()


def ticket(body: bytes) -> str:
    # Der Hub schliesst die Verbindung OHNE Antwort, wenn die Unit der
    # Gegenstelle nicht in `TICKET_UNITS` steht (daemon.py `_horche_einfach`:
    # `conn.close(); continue`). Fuer den Fragenden ist das ein
    # ConnectionResetError -- und genau daran starb dieser Pruefstand. Die
    # Zusage "hier kommt ein Ticket her" gehoert an diese Stelle: eine
    # abgewiesene Verbindung wird rot gemeldet, nicht geworfen, sonst bleibt
    # alles dahinter ungefahren. Verschluckt wird nichts; der Ausnahmetyp
    # steht im Namen der Pruefung.
    try:
        ans, _ = unix_json(DAIMON_RT / "ticket.sock", {
            "v": 1, "art": "ausgeben", "zweck": "api",
            "auftrag_hash": hash_body(body)})
    except (ConnectionResetError, ConnectionAbortedError,
            BrokenPipeError, socket.timeout) as exc:
        P.check("4", f"Hub beantwortet die Ticketanfrage ueberhaupt "
                f"({type(exc).__name__}: Verbindung ohne Antwort geschlossen "
                f"— Unit nicht in TICKET_UNITS?)", False, True)
        return ""
    P.check("4", "Positivkontrolle: Hub gibt ein API-Ticket aus", ans.get("ok"), True)
    P.check("4", "Hub-Ticket trägt eine nichtleere Kennung", bool(ans.get("ticket")), True)
    return str(ans.get("ticket", ""))


def anfrage_roh(body: bytes, tid: str | None = None) -> bytes:
    t = b"" if tid is None else b',"ticket":' + json.dumps(tid).encode()
    return b'{"v":1,"art":"anfrage"' + t + b',"koerper":' + body + b'}\n'


def erfolg(e: EgressProzess, body: bytes, bezeichnung: str = "gültige Anfrage") -> tuple[dict, bytes]:
    tid = ticket(body)
    ans, roh = e.request(roh=anfrage_roh(body, tid))
    P.check("4", f"Positivkontrolle: {bezeichnung} gelingt", ans.get("ok"), True)
    return ans, roh


def speicher_enthaelt(pid: int | None, nadel: bytes) -> tuple[bool, bool]:
    """(Messung möglich, Treffer), ausschließlich lesbare private Mappings."""
    if not pid:
        return False, False
    maps = Path(f"/proc/{pid}/maps")
    mem = Path(f"/proc/{pid}/mem")
    gelesen = False
    try:
        with maps.open() as mf, mem.open("rb", buffering=0) as mm:
            for zeile in mf:
                teile = zeile.split()
                if len(teile) < 2 or "r" not in teile[1] or "p" not in teile[1]:
                    continue
                a, b = (int(x, 16) for x in teile[0].split("-"))
                if b - a > 64 << 20:
                    continue
                try:
                    mm.seek(a)
                    block = mm.read(b - a)
                    gelesen = True
                    if nadel in block:
                        return True, True
                except OSError:
                    continue
    except OSError:
        return False, False
    return gelesen, False


def environ_bytes(pid: int) -> bytes:
    try:
        return Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return b""


def systemd_probe(quellunit: Path, rolle: str) -> tuple[str, dict, int]:
    name = f"daimon-t311-{rolle}-{uuid.uuid4().hex[:8]}.service"
    ziel = unit_home / name
    text = quellunit.read_text(encoding="utf-8")
    ziel.write_text(text, encoding="utf-8")
    drop = unit_home / f"{name}.d"
    drop.mkdir()
    ausgabe = DAIMON_RT / f"probe-{rolle}.json"
    credential_dropin = ""
    if rolle == "egress":
        credential_dropin = ("LoadCredential=\n"
                              f"LoadCredential=anthropic-token:{CREDS / 'anthropic-token'}\n")
    (drop / "pruefstand.conf").write_text(
        "[Unit]\nRequires=\nAfter=\nPartOf=\n"
        "[Service]\nExecStart=\n"
        f"ExecStart=/usr/bin/python3 {VERIFY / 't311_sandbox_probe.py'} {ausgabe} {os.getpid()}\n"
        f"{credential_dropin}"
        "Restart=no\nTimeoutStartSec=10\n",
        encoding="utf-8")
    temporare_units.append(name)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True,
                   stdout=subprocess.DEVNULL)
    r = subprocess.run(["systemctl", "--user", "start", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    P.check("V", f"temporäre echte {rolle}-Unit startet", r.returncode, 0)
    P.check("V", f"{rolle}-Sonde antwortet", warten(ausgabe, None, 4), True)
    daten = json.loads(ausgabe.read_text()) if ausgabe.is_file() else {}
    pid = int(subprocess.check_output(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value", name], text=True).strip() or 0)
    return name, daten, pid


def prop(unit: str, name: str) -> str:
    return subprocess.check_output(
        ["systemctl", "--user", "show", "-p", name, "--value", unit],
        text=True, stderr=subprocess.DEVNULL).strip()


def boolprop(unit: str, name: str) -> bool:
    return prop(unit, name).lower() in {"yes", "true", "1"}


def resources_gruppe() -> set[str]:
    """Was `@resources` heute wirklich umfasst -- gefragt, nicht abgeschrieben.

    `systemd-analyze` ist dieselbe Quelle, aus der systemd den Seccomp-Filter
    baut; eine hier abgetippte Liste waere eine zweite Fassung derselben Regel
    und damit die Attrappe. Leere Rueckgabe faellt in der Positivkontrolle in
    Kapitel 15 auf.
    """
    try:
        aus = subprocess.check_output(
            ["systemd-analyze", "syscall-filter", "@resources"],
            text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {z.strip() for z in aus.splitlines()
            if z.startswith((" ", "\t")) and z.strip() and not z.strip().startswith("#")}


def caps_status() -> dict:
    """Die Faehigkeiten des Pruefers selbst -- Positivkontrolle zu den
    Cap-Feldern der Sonden: "leer" muss von "nicht gemessen" unterscheidbar
    sein."""
    werte = {}
    for zeile in Path("/proc/self/status").read_text().splitlines():
        if zeile.startswith("Cap"):
            name, _, wert = zeile.partition(":")
            werte[name] = wert.strip()
    return werte


print("T-3.11 — Egress-Prüfstand, ausschließlich lokale Attrappen")
print(f"  Baum: {TARGET}")
print(f"  Modus: {'FIXTURE' if FIXTURE else 'ARBEITSBAUM'}")
print(f"  Laufzeitraum: {RT}")

P.kapitel("V", "Voraussetzungen und Prozessgrenzen")
for name, ok in (
    ("Egress-Service-Unit vorhanden", EGRESS_UNIT.is_file()),
    ("Egress-Socket-Unit vorhanden", SOCKET_UNIT.is_file()),
    ("Mind-Service-Unit vorhanden", MIND_UNIT.is_file()),
    ("Egress-ExecStart aus Unit auflösbar", bool(EGRESS_CMD)),
    ("Mind-ExecStart aus Unit auflösbar", bool(MIND_CMD)),
    (f"kein systemd-Kürzel bleibt in den ExecStart-Zeilen stehen "
     f"(gefunden: {EGRESS_REST + MIND_REST or 'keins'})",
     not (EGRESS_REST or MIND_REST)),
    ("systemd-socket-activate vorhanden", bool(shutil.which("systemd-socket-activate"))),
    ("systemctl --user erreichbar", subprocess.run(["systemctl", "--user", "show-environment"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0),
    ("openssl vorhanden", bool(shutil.which("openssl"))),
    ("gcc für externen CLOCK_REALTIME-Sprung vorhanden", bool(shutil.which("gcc"))),
):
    P.check("V", name, ok, True)
if P.rot["V"]:
    print("\nT-3.11: FEHLGESCHLAGEN — Voraussetzungen fehlen, keine Scheinmessung.")
    raise SystemExit(1)

konfig()
hub = start_hub()
http = Attrappe()
cert, key = zertifikat()
sprung_so, sprung_offset = echtzeit_sprungtreiber()
https = Attrappe(tls=True, cert=cert, key=key)

P.kapitel("1", "Mind ohne AF_INET, Egress mit AF_INET")
# Vier Proxy-Kanarien gehen kurz in die Umgebung des User-Managers. Die
# Egress-Unit muss sie aus dem tatsächlich gestarteten Prozess entfernen.
proxy_namen = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
manager_roh = subprocess.check_output(["systemctl", "--user", "show-environment"], text=True)
manager_proxy_original = {}
for zeile in manager_roh.splitlines():
    if "=" in zeile and zeile.split("=", 1)[0] in proxy_namen:
        k, v = zeile.split("=", 1); manager_proxy_original[k] = v
proxy_kanarien = {n: f"t311-{n.lower()}-muss-weg" for n in proxy_namen}
subprocess.run(["systemctl", "--user", "set-environment", *(
    f"{k}={v}" for k, v in proxy_kanarien.items())], check=True)
manager_nachher = subprocess.check_output(["systemctl", "--user", "show-environment"], text=True)
P.check("11", "Positivkontrolle: vier Proxy-Kanarien sind im User-Manager gesetzt",
        all(f"{k}={v}" in manager_nachher.splitlines() for k, v in proxy_kanarien.items()), True)
mind_name, mind_probe, mind_pid = systemd_probe(MIND_UNIT, "mind")
egress_name, egress_probe, egress_probe_pid = systemd_probe(EGRESS_UNIT, "egress")
manager_proxy_wiederherstellen()
P.check("1", "AF_INET-Socket scheitert im laufenden Mind-Prozess", mind_probe.get("af_inet"), False)
P.check("1", "Positivkontrolle: AF_INET-Socket gelingt im laufenden Egress-Prozess", egress_probe.get("af_inet"), True)

P.kapitel("2", "Token nicht in Mind, Suchmethode mit Egress-Kanarie")
mind_env = environ_bytes(mind_pid)
P.check("2", "Mind-Prozessumgebung ist auslesbar (Positivkontrolle)", bool(mind_env), True)
P.check("2", "Token fehlt in /proc/<mind>/environ", TOKEN_A.encode() in mind_env, False)
messbar_mind, im_mind = speicher_enthaelt(mind_pid, TOKEN_A.encode())
P.check("2", "Mind-Adressraum ist extern lesbar (Messvoraussetzung)", messbar_mind, True)
P.check("2", "Token fehlt im Mind-Adressraum", im_mind, False)
# Zusätzlich läuft der echte Mind-ExecStart aus dem geprüften Baum als Kind
# des Reviewers. So deckt die Adressraumprüfung nicht nur die Sandbox-Sonde,
# sondern auch Imports und Initialisierung des tatsächlichen Mind-Prozesses.
mind_env_echt = umgebung()
for n in ("CREDENTIALS_DIRECTORY", "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"):
    mind_env_echt.pop(n, None)
mind_echt = subprocess.Popen(MIND_CMD, env=mind_env_echt, cwd=TARGET,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
prozesse.append(mind_echt)
time.sleep(0.25)
P.check("2", "echter Mind-ExecStart bleibt für die Messung aktiv", mind_echt.poll(), None)
if mind_echt.poll() is None:
    echt_env = environ_bytes(mind_echt.pid)
    P.check("2", "Token fehlt in der Umgebung des echten Mind", TOKEN_A.encode() in echt_env, False)
    echt_messbar, echt_treffer = speicher_enthaelt(mind_echt.pid, TOKEN_A.encode())
    P.check("2", "Adressraum des echten Mind ist extern lesbar", echt_messbar, True)
    P.check("2", "Token fehlt im Adressraum des echten Mind", echt_treffer, False)
    mind_echt.terminate(); mind_echt.wait(2)

P.kapitel("3", "LoadCredential statt Umgebung")
token_hash = hashlib.sha256(TOKEN_A.encode()).hexdigest()
P.check("3", "LoadCredential mountet die Token-Kanarie in die laufende Egress-Unit",
        egress_probe.get("credential_sha256"), token_hash)
P.check("3", "Mind-Prozess hat kein gemountetes API-Credential",
        mind_probe.get("credential_sha256"), "")

normal = EgressProzess(umgebung(ziel=http.url), "normal")
zustand, _ = normal.request({"v": 1, "art": "zustand"})
P.check("3", "Egress sieht den Token aus dem Credential", zustand.get("token_vorhanden"), True)
epid = normal.pid()
P.check("3", "Egress-Dienstprozess ist auffindbar", bool(epid), True)
if epid:
    e_env = environ_bytes(epid)
    P.check("3", "Token fehlt in /proc/<egress>/environ", TOKEN_A.encode() in e_env, False)
    messbar_e, im_e = speicher_enthaelt(epid, TOKEN_A.encode())
    P.check("2", "Positivkontrolle: gleiche Speicherprobe liest Egress", messbar_e, True)
    P.check("2", "Positivkontrolle: gleiche Speicherprobe findet Token im Egress", im_e, True)

body_a = (b'{"model":"claude-test","messages":[{"role":"user","content":"'
          + KANARIE.encode() + b'"}],"max_tokens":7,"folge":[3,2,1]}')
body_b = b'{"model":"claude-test","messages":[],"max_tokens":8}'

P.kapitel("4", "jede Anfrage verlangt ein Ticket")
ans, _ = normal.request(roh=anfrage_roh(body_a))
P.check("4", "Anfrage ohne Ticket: kein_ticket", ans.get("grund"), "kein_ticket")
erfolg(normal, body_a, "Kanarien-Anfrage nach fehlendem Ticket")
ans, _ = normal.request(roh=anfrage_roh(body_a, "erfunden"))
P.check("4", "Anfrage mit falschem Ticket: ticket_ungueltig", ans.get("grund"), "ticket_ungueltig")
erfolg(normal, body_a, "Kanarien-Anfrage nach falschem Ticket")

P.kapitel("5", "Ticket höchstens einmal")
tid = ticket(body_a)
erst, _ = normal.request(roh=anfrage_roh(body_a, tid))
zweit, _ = normal.request(roh=anfrage_roh(body_a, tid))
P.check("5", "erster Einsatz desselben Tickets gelingt", erst.get("ok"), True)
P.check("5", "zweiter Einsatz ist ticket_ungueltig", zweit.get("grund"), "ticket_ungueltig")
erfolg(normal, body_a, "frisches Ticket nach Wiedereinlösung")

P.kapitel("6", "Ticket ist an den Körper gebunden")
tid = ticket(body_a)
falsch, _ = normal.request(roh=anfrage_roh(body_b, tid))
P.check("6", "Ticket A bezahlt Körper B nicht", falsch.get("grund"), "ticket_ungueltig")
erfolg(normal, body_b, "passend gebundener Körper B")

P.kapitel("7", "Obergrenze mit monotonem Fenster")
normal.stop()
konfig(fenster=1.2, hoechstens=2)
rate_env = umgebung(ziel=http.url)
rate_env["LD_PRELOAD"] = str(sprung_so)
rate_env["DAIMON_T311_REALTIME_OFFSET"] = str(sprung_offset)
rate = EgressProzess(rate_env, "rate")
erfolg(rate, body_a, "erste Anfrage im Fenster")
erfolg(rate, body_b, "zweite Anfrage im Fenster")
# Eine Stunde Wanduhrsprung darf das monotone Fenster nicht öffnen.
sprung_offset.write_text("3600", encoding="ascii")
tid = ticket(body_a)
limit, _ = rate.request(roh=anfrage_roh(body_a, tid))
P.check("7", "dritte Anfrage wird kontingent_fenster", limit.get("grund"), "kontingent_fenster")
P.check("7", "CLOCK_REALTIME-Sprung um +1 h öffnet das Fenster nicht",
        limit.get("grund"), "kontingent_fenster")
rest = limit.get("rest_s")
P.check("7", "rest_s ist numerisch und plausibel", isinstance(rest, (int, float)) and 0 < float(rest) <= 1.3, True)
time.sleep(1.35)
sprung_offset.write_text("0", encoding="ascii")
erfolg(rate, body_a, "Positivkontrolle nach Fensterablauf")
rate.stop()
konfig()
normal = EgressProzess(umgebung(ziel=http.url), "transport")

P.kapitel("8", "opaker Transport, beidseitig byte-identisch")
vorher = len(http.empfangen)
ans, roh = erfolg(normal, body_a, "opak transportierte Anfrage")
P.check("8", "Attrappe hat genau einen neuen Körper empfangen", len(http.empfangen), vorher + 1)
P.check("8", "Körper erreicht Attrappe byte-identisch", http.empfangen[-1] if http.empfangen else b"", body_a)
marker = b'"antwort":'
eingebettet = roh.split(marker, 1)[1][:-2] if marker in roh and roh.endswith(b"}\n") else b""
P.check("8", "Antwort erreicht Mind-Seite byte-identisch", eingebettet, http.antwort)

P.kapitel("9", "keine Körper oder Antworten in Logs und Absagen")
normal.stop()
logs = normal.logs()
P.check("9", "Log-Messpunkt enthält überhaupt Egress-Ausgabe (Positivkontrolle)", bool(logs), True)
P.check("9", "Körper-Kanarie fehlt in jeder Egress-Logzeile", KANARIE.encode() in logs, False)
P.check("9", "Antwort-Kanarie fehlt in jeder Egress-Logzeile", ANTWORT_KANARIE.encode() in logs, False)
# Audit-Datensätze haben exakt die vier erlaubten Felder. Ausgaben des
# Socket-Aktivators werden nicht als JSON fehlklassifiziert.
audit = []
for zeile in logs.splitlines():
    try:
        obj = json.loads(zeile)
    except (UnicodeDecodeError, json.JSONDecodeError):
        continue
    if isinstance(obj, dict) and "ticket" in obj:
        audit.append(obj)
P.check("9", "mindestens ein externer Audit-Datensatz ist beobachtbar", bool(audit), True)
P.check("9", "jeder Audit-Datensatz hat exakt ticket, bytes, status, dauer_ms",
        all(set(z) == {"ticket", "bytes", "status", "dauer_ms"} for z in audit), True)
for token in (KANARIE, ANTWORT_KANARIE):
    schlecht, roh = EgressProzess(umgebung(ziel="http://127.0.0.1:1"), f"absage-{token[-2:]}").request(
        roh=anfrage_roh(body_a, ticket(body_a)))
    P.check("9", f"Kanarie {token[-2:]} fehlt in Transportabsage", token.encode() in roh, False)

# Derselbe Prüfling einmal als echte, eindeutig benannte User-Unit: damit ist
# das Journal ein unabhängiger Messpunkt und nicht nur eine umgeleitete Datei.
j_name = J_NAME
j_ziel = unit_home / j_name
j_ziel.write_text(EGRESS_UNIT.read_text(encoding="utf-8"), encoding="utf-8")
j_drop = unit_home / f"{j_name}.d"; j_drop.mkdir()
j_sock = DAIMON_RT / "egress-journal.sock"
j_env = umgebung(ziel=http.url)
weiter = ["PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "XDG_RUNTIME_DIR",
          "XDG_CONFIG_HOME", "XDG_STATE_HOME", "CREDENTIALS_DIRECTORY",
          "DAIMON_EGRESS_ZIEL", "DAIMON_EGRESS_TESTPROFIL", "SSL_CERT_FILE"]
aktivator = ["/usr/bin/systemd-socket-activate", "-l", str(j_sock)]
for n in weiter:
    if n in j_env:
        aktivator += ["-E", n]
aktivator += EGRESS_CMD
env_zeilen = "".join(f"Environment={n}={j_env[n]}\n" for n in weiter if n in j_env)
(j_drop / "pruefstand.conf").write_text(
    "[Unit]\nRequires=\nAfter=\nPartOf=\n[Service]\nExecStart=\n"
    f"ExecStart={shlex.join(aktivator)}\nRestart=no\nLoadCredential=\n"
    f"LoadCredential=anthropic-token:{CREDS / 'anthropic-token'}\n{env_zeilen}",
    encoding="utf-8")
temporare_units.append(j_name)
seit = str(time.time() - 1)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True,
               stdout=subprocess.DEVNULL)
j_start = subprocess.run(["systemctl", "--user", "start", j_name],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
P.check("9", "Journal-Positivkontrolle: echte Egress-Testunit startet", j_start.returncode, 0)
P.check("9", "Journal-Positivkontrolle: deren Socket lauscht", warten(j_sock, None, 5), True)
if j_sock.exists():
    tid = ticket(body_a)
    j_ans = json.loads(unix_roh(j_sock, anfrage_roh(body_a, tid)))
    P.check("9", "Journal-Positivkontrolle: Anfrage an echte Testunit gelingt", j_ans.get("ok"), True)
time.sleep(0.3)
journal = subprocess.run(
    ["journalctl", "--user", "-u", j_name, "--since", "@" + seit,
     "--no-pager", "--output", "cat"], capture_output=True).stdout
P.check("9", "Journal enthält einen Egress-Auditdatensatz (Positivkontrolle)",
        b'"ticket"' in journal and b'"dauer_ms"' in journal, True)
P.check("9", "Körper-Kanarie fehlt im Dienstjournal", KANARIE.encode() in journal, False)
P.check("9", "Antwort-Kanarie fehlt im Dienstjournal", ANTWORT_KANARIE.encode() in journal, False)
subprocess.run(["systemctl", "--user", "stop", j_name],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

P.kapitel("10", "Token wird auf jeder Logzeile redigiert")
tokenziel = f"http://{TOKEN_A}@127.0.0.1:1"
redig = EgressProzess(umgebung(ziel=tokenziel), "redigiert")
redig.request({"v": 1, "art": "zustand"})
redig.stop()
rlog = redig.logs()
P.check("10", "Provozierte Testziel-Logzeile ist vorhanden (Positivkontrolle)", bool(rlog), True)
P.check("10", "Token erscheint nirgends im Egress-Log", TOKEN_A.encode() in rlog, False)
P.check("10", "Redaktionsmarker erscheint im provozierten Log", b"REDACT" in rlog.upper(), True)

P.kapitel("11", "Proxy-Umgebung wird ignoriert")
P.check("11", "laufende Egress-Unit entfernt HTTP_PROXY, HTTPS_PROXY, ALL_PROXY und NO_PROXY",
        egress_probe.get("proxy_umgebung"), {})
proxy = EgressProzess(umgebung(ziel=https.url, proxy="http://127.0.0.1:1", ca=cert), "proxy")
erfolg(proxy, body_a, "HTTPS trotz totem HTTPS_PROXY")
P.check("11", "Attrappe wurde trotz totem Proxy direkt erreicht", bool(https.empfangen), True)
proxy.stop()

P.kapitel("12", "TLS-Verifikation an")
tls_bad = EgressProzess(umgebung(ziel=https.url), "tls-bad")
tid = ticket(body_a)
bad, bad_raw = tls_bad.request(roh=anfrage_roh(body_a, tid))
P.check("12", "selbst ausgestelltes Zertifikat wird als ziel_weg abgelehnt", bad.get("grund"), "ziel_weg")
P.check("12", "TLS-Fehlermeldung enthält keine Körper-Kanarie", KANARIE.encode() in bad_raw, False)
tls_bad.stop()
tls_ok = EgressProzess(umgebung(ziel=https.url, ca=cert), "tls-ok")
erfolg(tls_ok, body_a, "Positivkontrolle mit Vertrauensanker")
tls_ok.stop()

P.kapitel("13", "Testschalter sichtbar und doppelt verriegelt")
nur_ziel = EgressProzess(umgebung(ziel=http.url, testprofil=False), "nur-ziel")
z1, _ = nur_ziel.request({"v": 1, "art": "zustand"})
P.check("13", "DAIMON_EGRESS_ZIEL allein aktiviert Testprofil nicht", z1.get("testprofil"), False)
P.check("13", "DAIMON_EGRESS_ZIEL allein ändert festes Ziel nicht", z1.get("ziel"), "https://api.anthropic.com/v1/messages")
nur_ziel.stop()
beide = EgressProzess(umgebung(ziel=http.url, testprofil=True), "beide")
z2, _ = beide.request({"v": 1, "art": "zustand"})
P.check("13", "beide Schalter machen testprofil sichtbar", z2.get("testprofil"), True)
P.check("13", "Positivkontrolle: beide Schalter wählen lokale Attrappe", z2.get("ziel"), http.url + "/v1/messages")

P.kapitel("14", "acht unterscheidbare Absagegründe")
faelle = [
    ("unlesbar", b"{kein json}\n", "unlesbar"),
    ("unbekannte_art", b'{"v":1,"art":"was_anderes"}\n', "unbekannte_art"),
    ("kein_ticket", anfrage_roh(body_a), "kein_ticket"),
    ("ticket_ungueltig", anfrage_roh(body_a, "falsch"), "ticket_ungueltig"),
    ("kein_koerper", b'{"v":1,"art":"anfrage","ticket":"x"}\n', "kein_koerper"),
]
for name, req, grund in faelle:
    ans, _ = beide.request(roh=req)
    P.check("14", f"{name} hat genau den eigenen Grund", ans.get("grund"), grund)
    erfolg(beide, body_a, f"Positivkontrolle nach {name}")

# Die drei betriebsabhängigen Gründe werden an ihrem echten Auslöser geprüft.
konfig(fenster=3, hoechstens=1)
beide.stop()
limit_e = EgressProzess(umgebung(ziel=http.url), "grund-limit")
erfolg(limit_e, body_a, "Vorlauf für kontingent_fenster")
lim, _ = limit_e.request(roh=anfrage_roh(body_b, ticket(body_b)))
P.check("14", "kontingent_fenster bleibt eigener Grund", lim.get("grund"), "kontingent_fenster")
limit_e.stop()
konfig()
(CREDS / "anthropic-token").unlink(missing_ok=True)
kein_token = EgressProzess(umgebung(ziel=http.url), "grund-token")
(CREDS / "anthropic-token").unlink(missing_ok=True)
# Neustart ohne Credential-Datei, nicht bloß Zustand manipulieren.
kein_token.stop()
env_ohne = umgebung(ziel=http.url)
(CREDS / "anthropic-token").unlink()
kein_token = EgressProzess(env_ohne, "grund-token2")
nt, _ = kein_token.request(roh=anfrage_roh(body_a, ticket(body_a)))
P.check("14", "fehlendes Credential bleibt kein_token", nt.get("grund"), "kein_token")
kein_token.stop()
ziel_weg = EgressProzess(umgebung(ziel="http://127.0.0.1:1"), "grund-ziel")
zw, _ = ziel_weg.request(roh=anfrage_roh(body_a, ticket(body_a)))
P.check("14", "unerreichbares Ziel bleibt ziel_weg", zw.get("grund"), "ziel_weg")
ziel_weg.stop()
erfolg(both := EgressProzess(umgebung(ziel=http.url), "grund-positiv"), body_a,
        "Positivkontrolle nach betriebsabhängigen Gründen")
both.stop()

P.kapitel("15", "Härtung beider laufender Units und Socketmodus")
# HIER STANDEN `ProtectProc=invisible` und `ProcSubset=pid`. Beide sind in einer
# `--user`-Unit WIRKUNGSLOS -- `systemd.exec(5)` sagt es zu `ProcSubset=`
# ausdruecklich: "it is only available to system services". systemd nimmt die
# Zeilen trotzdem widerspruchslos an, und `systemctl --user show` meldet an der
# laufenden Unit `ProtectProc=default` / `ProcSubset=all`, zeichengleich mit
# einem Lauf ganz ohne sie (Design 7.5, Berichtigung vom 20.08., dreifach
# gemessen). Commit `7b7deb9` (T-4.14) hat sie deshalb aus allen 22 Units
# entfernt und durch das ersetzt, was in `--user` WIRKT: den leeren
# `CapabilityBoundingSet=` und die Sperrliste `~@resources` mit benannten
# Ausnahmen je Unit. Vier Testdateien tragen denselben Grund im Kommentar
# (tests/test_egress.py, test_stt.py, test_gpu_worker.py, test_tts.py).
#
# Wer die zwei Zeilen hier wieder einsetzt, friert zum dritten Mal eine Zusage
# ein, die nichts tut. Geprueft wird stattdessen die WIRKUNG des Ersatzes:
# die Faehigkeiten im laufenden Prozess (aus der Sonde, im Prozess selbst
# gelesen) und der aufgeloeste Syscall-Filter an der laufenden Unit.
RESOURCES = resources_gruppe()
P.check("15", "Positivkontrolle: @resources ist als Gruppe auslesbar",
        len(RESOURCES) >= 8, True)
P.check("15", "Positivkontrolle: der Pruefer selbst haelt einen vollen Bounding-Set",
        caps_status().get("CapBnd", "0" * 16) != "0" * 16, True)
# Je Rolle eine eigene Erwartung. Die `@resources`-Ausnahme haben nach
# Design 7.5 nur `auth`, `ears`, `eyes`, `stt`, `tts`, `recorder`, `gpu@` und
# `cli-broker` -- jede mit gemessenem Grund (PipeWire-Echtzeitprioritaet,
# CUDA, node-Thread-Affinitaet). Mind und Egress stehen dort NICHT: sie
# brauchen die Faehigkeit nicht, also duerfen sie sie nicht behalten. Eine
# Unit, die sie sich zurueckholt, faerbt diese Zeile rot.
DARF_RESOURCES = {"mind": False, "egress": False}
for rolle, unit, sonde in (("mind", mind_name, mind_probe),
                           ("egress", egress_name, egress_probe)):
    P.check("15", f"{rolle}: NoNewPrivileges wirksam", boolprop(unit, "NoNewPrivileges"), True)
    P.check("15", f"{rolle}: CapabilityBoundingSet leer", prop(unit, "CapabilityBoundingSet"), "")
    P.check("15", f"{rolle}: ProtectSystem=strict wirksam", prop(unit, "ProtectSystem"), "strict")
    P.check("15", f"{rolle}: ProtectHome=read-only wirksam", prop(unit, "ProtectHome"), "read-only")
    P.check("15", f"{rolle}: PrivateTmp wirksam", boolprop(unit, "PrivateTmp"), True)
    P.check("15", f"{rolle}: LimitCORE=0 wirksam", prop(unit, "LimitCORE"), "0")
    P.check("15", f"{rolle}: UMask=0077 wirksam", prop(unit, "UMask"), "0077")
    P.check("15", f"{rolle}: RuntimeDirectory=daimon wirksam", "daimon" in prop(unit, "RuntimeDirectory"), True)
    P.check("15", f"{rolle}: RuntimeDirectoryPreserve=yes wirksam", prop(unit, "RuntimeDirectoryPreserve"), "yes")
    caps = sonde.get("caps", {})
    P.check("15", f"{rolle}: Sonde hat ihre Faehigkeiten gemeldet (Messvoraussetzung)",
            bool(caps), True)
    for feld in ("CapBnd", "CapEff", "CapPrm", "CapInh", "CapAmb"):
        P.check("15", f"{rolle}: {feld} des laufenden Prozesses ist leer",
                caps.get(feld), "0" * 16)
    filter_ist = set(prop(unit, "SystemCallFilter").split())
    P.check("15", f"{rolle}: Syscall-Filter ist aufgeloest lesbar (Positivkontrolle)",
            "read" in filter_ist, True)
    gefunden = sorted(filter_ist & RESOURCES)
    if DARF_RESOURCES[rolle]:
        # Die Tabelle gilt in beide Richtungen: eine als Ausnahme gefuehrte
        # Rolle MUSS die Syscalls auch wirklich haben, sonst ist die Zeile in
        # Design 7.5 veraltet und beschreibt den Baum nicht mehr.
        P.check("15", f"{rolle}: dokumentierte @resources-Ausnahme ist wirklich offen",
                bool(gefunden), True)
    else:
        P.check("15", f"{rolle}: kein @resources-Syscall im wirksamen Filter",
                gefunden, [])
raf_m = prop(mind_name, "RestrictAddressFamilies")
raf_e = prop(egress_name, "RestrictAddressFamilies")
P.check("15", "Mind effektiv nur AF_UNIX", "AF_UNIX" in raf_m and "AF_INET" not in raf_m, True)
P.check("15", "Egress effektiv mit AF_UNIX, AF_INET und AF_INET6",
        all(x in raf_e.split() for x in ("AF_UNIX", "AF_INET", "AF_INET6")), True)

# Socket-Unit wird unter eigenem Namen wirklich gestartet; keine fremde Unit.
# systemd lehnt einen Socket ab, wenn sein zugehöriger Dienst bereits läuft.
# Die Egress-Härtungsprobe ist abgeschlossen; deshalb wird genau diese eigene
# Probe vor dem Sockettest geordnet beendet.
subprocess.run(["systemctl", "--user", "stop", egress_name],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sockname = f"daimon-t311-socket-{uuid.uuid4().hex[:8]}.socket"
sockziel = unit_home / sockname
sockziel.write_text(SOCKET_UNIT.read_text(encoding="utf-8"), encoding="utf-8")
drop = unit_home / f"{sockname}.d"; drop.mkdir()
socketprobe = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "daimon/egress.sock"
P.check("15", "bindender Egress-Socketpfad ist vor der eigenen Probe frei",
        socketprobe.exists(), False)
if not socketprobe.exists():
    eigene_socketpfade.append(socketprobe)
(drop / "pruefstand.conf").write_text(
    f"[Socket]\nService={egress_name}\n",
    encoding="utf-8")
temporare_units.append(sockname)
subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
sr = subprocess.run(["systemctl", "--user", "start", sockname])
P.check("15", "eigene geprüfte Egress-Socket-Unit startet", sr.returncode, 0)
P.check("15", "Socketpfad wird am laufenden Socket angelegt", warten(socketprobe), True)
modus = stat.S_IMODE(socketprobe.stat().st_mode) if socketprobe.exists() else -1
P.check("15", "SocketMode wirkt als 0600", oct(modus), "0o600")
P.check("15", "Accept=no ist effektiv", prop(sockname, "Accept"), "no")

P.kapitel("16", "Tokenrotation als Datei und als laufender Neustart")
P.check("16", "docs/TOKEN-ROTATION.md liegt als Datei vor", ROTATION.is_file(), True)
rottext = ROTATION.read_text(encoding="utf-8") if ROTATION.is_file() else ""
for wort in ("LoadCredential", "restart", "zustand", "alt"):
    P.check("16", f"Rotationsdokument nennt {wort}", wort.lower() in rottext.lower(), True)
alt = EgressProzess(umgebung(token=TOKEN_A, ziel=http.url), "rotation-alt")
alt.request({"v": 1, "art": "zustand"})
alt_pid = alt.pid(); alt.stop()
neu = EgressProzess(umgebung(token=TOKEN_B, ziel=http.url), "rotation-neu")
neu_pid = neu.pid(); neu.request({"v": 1, "art": "zustand"})
P.check("16", "alter Egress-Prozess ist nach Neustart tot", Path(f"/proc/{alt_pid}").exists() if alt_pid else True, False)
messbar_neu, token_neu = speicher_enthaelt(neu_pid, TOKEN_B.encode())
P.check("16", "neuer Prozessspeicher ist messbar", messbar_neu, True)
P.check("16", "neuer Prozess hält den neuen Token", token_neu, True)
_, token_alt = speicher_enthaelt(neu_pid, TOKEN_A.encode())
P.check("16", "neuer Prozess hält den alten Token nicht", token_alt, False)
neu.stop()

P.kapitel("17", "eingefrorene Hub-Regressionsprüfstände")
if FIXTURE:
    P.info("Fixture/Mutanten sind keine vollständigen Arbeitsbäume; K17 wird im Arbeitsbaumlauf ausgeführt.")
else:
    kind_env = os.environ.copy(); kind_env.pop("DAIMON_FIXTURE", None)
    for task in ("T-3.9", "T-3.8", "T-3.10"):
        rc = subprocess.run([str(REPO / "tests/verify" / f"{task}.sh")],
                            cwd=REPO, env=kind_env).returncode
        P.check("17", f"{task} bleibt einzeln vollständig grün", rc, 0)
        subprocess.run(["systemctl", "--user", "reset-failed"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)

http.close(); https.close()

print("\n=== Ergebnis je Kriterium ===")
for k in [str(x) for x in range(1, 18)]:
    print(f"  K{k}: {P.n[k]} Prüfungen, {P.rot[k]} rot")
print(f"  Voraussetzungen: {P.n['V']} Prüfungen, {P.rot['V']} rot")
gesamt = sum(P.n.values()); rot = sum(P.rot.values())
print(f"T-3.11: {gesamt} Prüfungen, {rot} rot")
raise SystemExit(1 if P.fail else 0)
