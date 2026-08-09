#!/usr/bin/env python3
"""Blinder Laufzeit-Prüfstand für T-3.16 (Mimic-Anbindung, Phase 2c).

Gebaut ausschließlich aus dem Vertrag: Mimic/PHASE2.md Schritte 8–19 und die
Abnahmetabelle (P2-A, P2-B, P2-C, P2-E(a), P2-H, P2-I, P2-J, P2-K). Die
Implementierung von `daimon/face/mimic.py` wurde nie gelesen — sie existiert
beim Schreiben dieses Prüfstands nicht.

Kein Aufruf verlässt die Maschine und kein Ton erreicht die Soundkarte: Mimic
ist eine Attrappe des Reviewers, die Mimics Rahmenprotokoll selbst spricht und
jede Anfrage roh protokolliert; der Hub ist eine Attrappe; `pw-cat` ist ein
Stub im PATH, der Argumente (also die **Rate**) und die geschriebenen Bytes
mitschreibt. Die Rate ist der Messpunkt für die Engine-Wahl: 48000 kann nur aus
dem `H`-Rahmen kommen, 22050 nur aus sherpa.

Negative Aussagen bekommen im selben Lauf eine Positivkontrolle am gleichen
Messpunkt: „bei abgelehntem Text null Mimic-Anfragen" steht neben „bei
freigegebenem Text genau eine", „kalt wartet nicht" neben „warm spricht Mimic".
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
TARGET = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else REPO
PYTHON = REPO / ".venv/bin/python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

FRAME = struct.Struct(">cI")
MEDIA_TYPE = "application/vnd.mimic.frames"

# Der Korpus. 79 und 80 Zeichen sind die Grenzfälle aus P2-A -- exakt, nicht
# ungefähr: eine Auswahlregel mit `>` statt `>=` faellt genau hier durch.
KURZ_79 = "Der Aufzug oeffnet sich und der letzte Boss wartet schon hinter der Tuer darauf"
LANG_80 = KURZ_79 + "!"
assert len(KURZ_79) == 79, len(KURZ_79)
assert len(LANG_80) == 80, len(LANG_80)

# Fristen aus Schritt 13 und der Abnahme. Der Prüfstand misst gegen diese
# Zahlen und nicht gegen "fühlt sich schnell an".
GESAMTFRIST_MS = 500       # Verbindungsbeginn bis erster A-Rahmen
NAECHSTE_FRIST_MS = 800    # P2-C nach Rev 9: 500 ms Mimic-Frist plus sherpa-TTFA.
                           # Die 700 der ersten Fassung waren Arithmetik ohne
                           # Rechnung -- sie halten nur, solange sherpa unter
                           # 200 ms bleibt, und gemessen sind es 206.
KALT_FRIST_MS = 400        # P2-B: Ton bei kaltem Mimic
WARM_FRIST_MS = 200        # Schritt 12a: /warm ist best effort


class Pruefstand:
    def __init__(self) -> None:
        self.n: dict[str, int] = defaultdict(int)
        self.rot: dict[str, int] = defaultdict(int)
        self.fail = False

    def check(self, k: str, name: str, ist, soll) -> None:
        self.n[k] += 1
        if ist == soll:
            print(f"  ok   [{k}] {name}", flush=True)
        else:
            print(f"  FAIL [{k}] {name} (erwartet {soll!r}, war {ist!r})", flush=True)
            self.rot[k] += 1
            self.fail = True

    def unter(self, k: str, name: str, ist_ms: float, grenze_ms: float) -> None:
        self.n[k] += 1
        if ist_ms is not None and ist_ms < grenze_ms:
            print(f"  ok   [{k}] {name} ({ist_ms:.0f} ms < {grenze_ms:.0f})", flush=True)
        else:
            print(f"  FAIL [{k}] {name} ({ist_ms} ms, Grenze {grenze_ms:.0f})", flush=True)
            self.rot[k] += 1
            self.fail = True

    def info(self, text: str) -> None:
        print(f"  INFO {text}", flush=True)

    def kapitel(self, k: str, text: str) -> None:
        print(f"\n--- {k}: {text} ---", flush=True)


P = Pruefstand()

laufzeit_basis = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "daimon"
laufzeit_basis.mkdir(parents=True, exist_ok=True)
RT = Path(tempfile.mkdtemp(prefix="t316-", dir=laufzeit_basis))
CONFIG, STATE, RUNTIME, BIN = RT / "config", RT / "state", RT / "runtime", RT / "bin"
DAIMON_RT = RUNTIME / "daimon"
MIMIC_RT = RUNTIME / "mimic"
for d in (CONFIG / "daimon", STATE / "daimon", DAIMON_RT, MIMIC_RT, BIN):
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)

HUB_SOCK = DAIMON_RT / "tts.sock"
MIMIC_SOCK = MIMIC_RT / "mimic.socket"
TTS_SOCK = DAIMON_RT / "daimon-tts.sock"
PWCAT_LOG = RT / "pwcat.jsonl"

prozesse: list[subprocess.Popen] = []


def aufraeumen() -> None:
    for p in reversed(prozesse):
        if p.poll() is None:
            p.terminate()
    ende = time.monotonic() + 2
    for p in reversed(prozesse):
        if p.poll() is None:
            try:
                p.wait(max(0.01, ende - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
    shutil.rmtree(RT, ignore_errors=True)


import atexit  # noqa: E402

atexit.register(aufraeumen)


# ---------------------------------------------------------------- Werkzeuge

def unix_json(pfad: Path, obj: dict, timeout: float = 20) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    try:
        c.connect(str(pfad))
        c.sendall(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
        roh = c.makefile("rb").readline(1 << 20)
    finally:
        c.close()
    try:
        return json.loads(roh)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def warten(pfad: Path, prozess: subprocess.Popen | None = None,
           sekunden: float = 20) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if pfad.exists():
            return True
        if prozess is not None and prozess.poll() is not None:
            return False
        time.sleep(0.03)
    return False


def pwcat_zeilen() -> list[dict]:
    if not PWCAT_LOG.exists():
        return []
    aus = []
    for zeile in PWCAT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            aus.append(json.loads(zeile))
        except json.JSONDecodeError:
            pass
    return aus


def rate_der_naechsten_wiedergabe(vorher: int, sekunden: float = 5) -> int | None:
    """Die Rate der Wiedergabe NACH `vorher` Zeilen.

    Der Stub schreibt sein Protokoll erst, wenn stdin schliesst -- also nachdem
    der Hintergrundteil fertig ist. Wer sofort liest, misst die vorige
    Aeusserung. Genau dieser Fehler hat den ersten Lauf gruen aussehen lassen,
    wo er rot war.
    """
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        zeilen = pwcat_zeilen()
        if len(zeilen) > vorher:
            return zeilen[-1].get("rate")
        time.sleep(0.05)
    return None


# ---------------------------------------------------------------- Attrappen

class HubAttrappe:
    """Validator und Markenbuch des Reviewers.

    Zählt `freigabe`, `beginnt`, `gesprochen` selbst und merkt sich die aktive
    Marke. `tts_active` wird hier nachgebildet, wie der Vertrag es in Schritt 17
    verlangt: gesetzt bei `beginnt`, gelöscht **nur** von der aktiven Marke.
    """

    def __init__(self) -> None:
        self.freigaben = 0
        self.beginnt: list[str] = []
        self.gesprochen: list[str] = []
        self.aktive_marke: str | None = None
        self.tts_active = False
        self.ablehnen: str = ""      # Grund, wenn die naechste Freigabe scheitern soll
        self.lock = threading.Lock()
        self._marken = 0
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(HUB_SOCK))
        os.chmod(HUB_SOCK, 0o600)
        self.srv.listen(16)
        threading.Thread(target=self._lauf, daemon=True).start()

    def _lauf(self) -> None:
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._bediene, args=(conn,), daemon=True).start()

    def _bediene(self, conn: socket.socket) -> None:
        with conn:
            try:
                roh = conn.makefile("rb").readline(1 << 16)
                anfrage = json.loads(roh)
            except (OSError, json.JSONDecodeError, ValueError):
                return
            conn.sendall(json.dumps(self._antwort(anfrage)).encode() + b"\n")

    def _antwort(self, anfrage: dict) -> dict:
        art = anfrage.get("art")
        with self.lock:
            if art == "freigabe":
                self.freigaben += 1
                if self.ablehnen:
                    return {"v": 1, "ok": False, "grund": self.ablehnen}
                self._marken += 1
                marke = f"m{self._marken}"
                text = anfrage.get("text")
                if not isinstance(text, str):
                    text = "Ersatzsatz vom Hub."
                return {"v": 1, "ok": True, "text": text, "marke": marke}
            if art == "beginnt":
                marke = str(anfrage.get("marke", ""))
                self.beginnt.append(marke)
                self.aktive_marke = marke
                self.tts_active = True
                return {"v": 1, "ok": True}
            if art == "gesprochen":
                marke = str(anfrage.get("marke", ""))
                self.gesprochen.append(marke)
                # Schritt 17: nur die aktive Marke darf loeschen.
                if marke == self.aktive_marke:
                    self.tts_active = False
                    self.aktive_marke = None
                return {"v": 1, "ok": True}
        return {"v": 1, "ok": False, "grund": "unbekannte_art"}


class MimicAttrappe:
    """Mimics Frontend, nachgebaut aus PHASE2.md und dem Rahmenformat.

    Verhalten je Anfrage steuerbar. Protokolliert jeden Anfragekörper roh --
    `require_warm`, `correlation_id` und „ein Satz, nicht drei Segmente" sind
    nur hier messbar, nicht an einer Selbstauskunft von dAImon.
    """

    def __init__(self) -> None:
        self.modus = "ok"        # ok | cold | langsam | tod_im_stream | stockt | stumm
        self.verzoegerung_s = 0.0
        self.speak: list[dict] = []
        self.warm: list[dict] = []
        self.rate = 48000
        self.lock = threading.Lock()
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(MIMIC_SOCK))
        os.chmod(MIMIC_SOCK, 0o600)
        self.srv.listen(16)
        self.laeuft = True
        threading.Thread(target=self._lauf, daemon=True).start()

    def stoppen(self) -> None:
        self.laeuft = False
        try:
            self.srv.close()
        except OSError:
            pass
        MIMIC_SOCK.unlink(missing_ok=True)

    def _lauf(self) -> None:
        while self.laeuft:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._bediene, args=(conn,), daemon=True).start()

    def _bediene(self, conn: socket.socket) -> None:
        try:
            datei = conn.makefile("rwb")
            zeile = datei.readline(1 << 16)
            if not zeile:
                return
            teile = zeile.decode("latin1").split()
            pfad = teile[1] if len(teile) > 1 else ""
            laenge = 0
            while True:
                kopf = datei.readline(1 << 16)
                if kopf in (b"\r\n", b"\n", b""):
                    break
                name, _, wert = kopf.decode("latin1").partition(":")
                if name.strip().lower() == "content-length":
                    laenge = int(wert.strip())
            koerper = json.loads(datei.read(laenge) or b"{}")
            if pfad == "/status":
                self._status(datei)
            elif pfad == "/warm":
                self._warm(datei, koerper)
            elif pfad == "/speak":
                self._speak(datei, koerper)
            else:
                self._fehler(datei, 400, "bad_request", "unbekannter Endpunkt")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _status(self, datei) -> None:
        """`GET /status` mit `voices` -- die Startpruefung aus Schritt 6.

        Im Modus `stumm` nimmt die Attrappe an und antwortet nie: genau der
        Fall aus P2-J, der schlimmer ist als „Mimic nicht installiert".
        """
        with self.lock:
            modus = self.modus
        if modus == "stumm":
            time.sleep(30)
            return
        self._json(datei, 200, {"v": 1, "state": "warm", "mode": "mf", "queue": 0,
                                "voices": [{"name": "matthias", "schema": 1}]})

    def _warm(self, datei, koerper: dict) -> None:
        with self.lock:
            self.warm.append(koerper)
            modus = self.modus
        if modus == "warm_haengt":
            time.sleep(30)
            return
        self._json(datei, 202, {"v": 1, "state": "vermerkt"})

    def _speak(self, datei, koerper: dict) -> None:
        with self.lock:
            self.speak.append(koerper)
            modus, verzug, rate = self.modus, self.verzoegerung_s, self.rate
        if modus == "cold" or (koerper.get("require_warm") and modus == "kaltstart"):
            self._fehler(datei, 503, "cold", "Modus mf ist nicht warm")
            return
        if modus == "stumm":
            time.sleep(30)          # nimmt an, antwortet nie
            return
        if verzug:
            time.sleep(verzug)
        # Mimic sendet den 200er-Kopf erst, wenn der erste A-Rahmen steht
        # (Schritt 13). Die Attrappe haelt sich daran.
        datei.write(b"HTTP/1.1 200 OK\r\n")
        datei.write(f"Content-Type: {MEDIA_TYPE}\r\n".encode())
        datei.write(b"Transfer-Encoding: chunked\r\n\r\n")
        datei.flush()
        kopf = {"v": 1, "sample_rate": rate, "channels": 1, "format": "s16le",
                "request_id": "r" * 32, "mode": koerper.get("mode", "mf"),
                "voice": koerper.get("voice", "matthias")}
        self._rahmen(datei, b"H", json.dumps(kopf).encode())
        ton = b"\x40\x30" * 4800            # 0.2 s, deutlich ueber jeder Stummschwelle
        self._rahmen(datei, b"A", ton)
        if modus == "tod_im_stream":
            datei.close()                    # SIGKILL-Aequivalent: Verbindung faellt
            return
        if modus == "stockt":
            time.sleep(30)                   # kein weiterer Rahmen
            return
        self._rahmen(datei, b"A", ton)
        self._rahmen(datei, b"E", json.dumps({"status": "ok", "samples": 9600}).encode())
        datei.write(b"0\r\n\r\n")
        datei.flush()

    def _rahmen(self, datei, art: bytes, nutzlast: bytes) -> None:
        block = FRAME.pack(art, len(nutzlast)) + nutzlast
        datei.write(f"{len(block):X}\r\n".encode())
        datei.write(block)
        datei.write(b"\r\n")
        datei.flush()

    def _json(self, datei, status: int, wert: dict) -> None:
        koerper = json.dumps(wert).encode()
        datei.write(f"HTTP/1.1 {status} X\r\n".encode())
        datei.write(b"Content-Type: application/json\r\n")
        datei.write(f"Content-Length: {len(koerper)}\r\n\r\n".encode())
        datei.write(koerper)
        datei.flush()

    def _fehler(self, datei, status: int, grund: str, meldung: str) -> None:
        self._json(datei, status, {"v": 1, "reason": grund, "message": meldung})


# ------------------------------------------------------------ pw-cat-Stub

PWCAT = f"""#!/usr/bin/env python3
import json, sys, time
rate = None
for a in sys.argv[1:]:
    if a.startswith("--rate="):
        rate = int(a.split("=", 1)[1])
t0 = time.monotonic()
daten = sys.stdin.buffer.read()
with open({str(PWCAT_LOG)!r}, "a") as fh:
    fh.write(json.dumps({{"rate": rate, "bytes": len(daten),
                          "dauer_s": round(time.monotonic() - t0, 3),
                          "argv": sys.argv[1:]}}) + "\\n")
"""


def pwcat_stub() -> None:
    ziel = BIN / "pw-cat"
    ziel.write_text(PWCAT, encoding="utf-8")
    ziel.chmod(0o755)


# ------------------------------------------------------------ Dienst starten

def daimon_toml(**tts) -> None:
    zeilen = ["[tts]"]
    for k, v in tts.items():
        if isinstance(v, bool):
            zeilen.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            zeilen.append(f"{k} = {v}")
        else:
            zeilen.append(f'{k} = "{v}"')
    (CONFIG / "daimon" / "daimon.toml").write_text("\n".join(zeilen) + "\n",
                                                   encoding="utf-8")


def umgebung() -> dict:
    env = dict(os.environ)
    env.update({
        "XDG_CONFIG_HOME": str(CONFIG), "XDG_STATE_HOME": str(STATE),
        "XDG_RUNTIME_DIR": str(RUNTIME),
        "PATH": f"{BIN}:{env.get('PATH', '')}",
        "PYTHONPATH": str(TARGET),
    })
    return env


def dienst_starten() -> subprocess.Popen:
    TTS_SOCK.unlink(missing_ok=True)
    p = subprocess.Popen(
        [str(PYTHON), "-B", "-P", "-m", "daimon.face.tts",
         "--socket", str(TTS_SOCK), "--hub-socket", str(HUB_SOCK)],
        cwd=str(TARGET), env=umgebung(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    prozesse.append(p)
    if not warten(TTS_SOCK, p, 40):
        raise SystemExit("TTS-Dienst kam nicht hoch")
    return p


def sprich(text: str, kanal: str = "reaktion", timeout: float = 20) -> tuple[dict, float]:
    t0 = time.monotonic()
    antwort = unix_json(TTS_SOCK, {"v": 1, "art": "sprich", "kanal": kanal,
                                   "text": text}, timeout=timeout)
    return antwort, (time.monotonic() - t0) * 1000


# ------------------------------------------------------------------ Ablauf

pwcat_stub()
hub = HubAttrappe()
mimic = MimicAttrappe()
daimon_toml(mimic_socket=str(MIMIC_SOCK), mimic_stimme="matthias",
            mimic_ab_zeichen=80, mimic_nur_warm=False)
dienst = dienst_starten()

P.kapitel("P2-A", "Auswahl greift -- 79 Zeichen sherpa, 80 Zeichen Mimic")
vorher, toene = len(mimic.speak), len(pwcat_zeilen())
antwort, _ = sprich(KURZ_79)
P.check("P2-A", "79 Zeichen: keine Mimic-Anfrage", len(mimic.speak) - vorher, 0)
P.check("P2-A", "79 Zeichen: engine ist sherpa",
        str(antwort.get("engine", "")).startswith("sherpa"), True)
P.check("P2-A", "79 Zeichen: Rate 22050 bei pw-cat",
        rate_der_naechsten_wiedergabe(toene), 22050)

vorher, toene = len(mimic.speak), len(pwcat_zeilen())
antwort, _ = sprich(LANG_80)
P.check("P2-A", "80 Zeichen: genau eine Mimic-Anfrage", len(mimic.speak) - vorher, 1)
P.check("P2-A", "80 Zeichen: engine ist mimic", antwort.get("engine"), "mimic")
P.check("P2-A", "80 Zeichen: Rate 48000 aus dem H-Rahmen",
        rate_der_naechsten_wiedergabe(toene), 48000)

letzte = mimic.speak[-1] if mimic.speak else {}

P.kapitel("Schritt 14", "Mimic bekommt den ganzen Satz, nicht Segmente")
P.check("Schritt 14", "gesendeter Text ist der ganze Hub-Satz",
        letzte.get("text"), LANG_80)

P.kapitel("Schritt 18", "Korrelations-ID ist 32-stelliges Hex")
kid = str(letzte.get("correlation_id", ""))
P.check("Schritt 18", "ID vorhanden und 32 Hex-Stellen",
        len(kid) == 32 and all(c in "0123456789abcdefABCDEF" for c in kid), True)

P.kapitel("P2-E(a)", "Hub unumgehbar -- abgelehnter Text erreicht Mimic nie")
hub.ablehnen = "zu_lang"
vorher = len(mimic.speak)
antwort, _ = sprich(LANG_80)
P.check("P2-E(a)", "null Mimic-Anfragen bei Ablehnung", len(mimic.speak) - vorher, 0)
P.check("P2-E(a)", "Antwort meldet den Hub-Grund", antwort.get("grund"), "zu_lang")
hub.ablehnen = ""
vorher = len(mimic.speak)
sprich(LANG_80)
P.check("P2-E(a)", "Positivkontrolle: freigegeben ergibt eine Anfrage",
        len(mimic.speak) - vorher, 1)

P.kapitel("P2-B", "Kalt wartet nicht -- 503 cold, sherpa spricht, /warm folgt")
mimic.modus = "cold"
warm_vorher = len(mimic.warm)
antwort, dauer_ms = sprich(LANG_80)
P.unter("P2-B", "Antwort trotz kaltem Mimic", dauer_ms, KALT_FRIST_MS)
P.check("P2-B", "gesprochen hat sherpa",
        str(antwort.get("engine", "")).startswith("sherpa"), True)
P.check("P2-B", "Grund maschinenlesbar vermerkt", antwort.get("mimic_grund"), "cold")
time.sleep(0.5)
P.check("P2-B", "/warm wurde angestossen", len(mimic.warm) > warm_vorher, True)
P.check("P2-B", "hoechstens ein Warmlauf je Aeusserung",
        len(mimic.warm) - warm_vorher, 1)

P.kapitel("P2-K", "Warmlauf-Gegenfall -- ein haengendes /warm bremst nichts")
mimic.modus = "warm_haengt"
warm_vorher = len(mimic.warm)
_, dauer_ms = sprich(KURZ_79)
P.unter("P2-K", "sherpa-TTFA unveraendert trotz haengendem /warm",
        dauer_ms, NAECHSTE_FRIST_MS)
_, dauer_ms = sprich(KURZ_79)
P.unter("P2-K", "auch die naechste Aeusserung bleibt in der Frist",
        dauer_ms, NAECHSTE_FRIST_MS)

P.kapitel("P2-C", "Ausfall unsichtbar -- fuenf Faelle, je mit Frist")
mimic.modus = "ok"

# (a) Dienst gestoppt: kein Socket
mimic.stoppen()
antwort, dauer_ms = sprich(LANG_80)
P.unter("P2-C(a)", "Dienst weg: Ton binnen Frist", dauer_ms, NAECHSTE_FRIST_MS)
P.check("P2-C(a)", "vollstaendig mit sherpa gesprochen",
        str(antwort.get("engine", "")).startswith("sherpa") and antwort.get("ok"), True)
P.check("P2-C(a)", "Grund maschinenlesbar", bool(antwort.get("mimic_grund")), True)

# (b) Socket da, niemand horcht
MIMIC_SOCK.parent.mkdir(parents=True, exist_ok=True)
tot = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
tot.bind(str(MIMIC_SOCK))          # gebunden, aber kein listen()
antwort, dauer_ms = sprich(LANG_80)
P.unter("P2-C(b)", "niemand horcht: Ton binnen Frist", dauer_ms, NAECHSTE_FRIST_MS)
P.check("P2-C(b)", "vollstaendig mit sherpa",
        str(antwort.get("engine", "")).startswith("sherpa") and antwort.get("ok"), True)
tot.close()
MIMIC_SOCK.unlink(missing_ok=True)

mimic = MimicAttrappe()

# (d) Mimic lebt, verzoegert den ersten A ueber die Gesamtfrist
mimic.modus = "ok"
mimic.verzoegerung_s = 1.2
antwort, dauer_ms = sprich(LANG_80)
P.unter("P2-C(d)", "Verzoegerung: Rueckfall binnen Frist", dauer_ms, NAECHSTE_FRIST_MS)
P.check("P2-C(d)", "vollstaendig mit sherpa",
        str(antwort.get("engine", "")).startswith("sherpa") and antwort.get("ok"), True)
P.check("P2-C(d)", "Grund nennt die Frist", antwort.get("mimic_grund"), "frist")
P.info(f"Rechnung: {GESAMTFRIST_MS} ms Mimic-Frist + sherpa-TTFA gegen "
       f"{NAECHSTE_FRIST_MS} ms aus P2-C -- sherpa hat damit "
       f"{NAECHSTE_FRIST_MS - GESAMTFRIST_MS} ms.")
mimic.verzoegerung_s = 0.0

# (c) Tod mitten im Stream
mimic.modus = "tod_im_stream"
antwort, dauer_ms = sprich(LANG_80)
P.check("P2-C(c)", "Satz endet still, kein sherpa hinterher",
        str(antwort.get("engine", "")), "mimic")
naechste, dauer_ms = sprich(LANG_80)
P.unter("P2-C(c)", "die naechste Aeusserung wird bedient", dauer_ms, NAECHSTE_FRIST_MS)
P.check("P2-C(c)", "und sie spricht wirklich", naechste.get("ok"), True)

# (e) Stocken im Stream ueber den Rahmenabstand
mimic.modus = "stockt"
antwort, _ = sprich(LANG_80, timeout=30)
P.check("P2-C(e)", "Stocken endet still mit Mimic-Engine",
        str(antwort.get("engine", "")), "mimic")
mimic.modus = "ok"
naechste, dauer_ms = sprich(LANG_80)
P.unter("P2-C(e)", "die naechste Aeusserung wird bedient", dauer_ms, NAECHSTE_FRIST_MS)
P.check("P2-C(e)", "und sie spricht wirklich", naechste.get("ok"), True)

P.kapitel("P2-I", "Terminalpfade raeumen auf")
# Auf das Ende der letzten Wiedergabe warten, nicht auf die Uhr: die Zusage ist
# "nach jedem Ende", nicht "binnen x Sekunden".
ende_bis = time.monotonic() + 10
while time.monotonic() < ende_bis:
    zustand = unix_json(TTS_SOCK, {"v": 1, "art": "zustand"})
    if not zustand.get("spricht") and not zustand.get("mimic_sitzung"):
        break
    time.sleep(0.1)
P.check("P2-I", "keine laufende Wiedergabe nach allen Enden",
        zustand.get("spricht"), False)
P.check("P2-I", "keine offene Mimic-Sitzung", zustand.get("mimic_sitzung"), False)

# P2-H steht in t314b: gegen den ECHTEN Hub. Eine Attrappe koennte hier nur
# bestaetigen, was der Pruefstand selbst hineingeschrieben hat.

P.kapitel("P2-J", "Haengendes Mimic blockiert den Start nicht")
mimic.modus = "stumm"
neu = dienst_starten()
P.check("P2-J", "Dienst ist trotz schweigendem Mimic erreichbar",
        bool(unix_json(TTS_SOCK, {"v": 1, "art": "zustand"}).get("ok")), True)
_, dauer_ms = sprich(KURZ_79)
P.unter("P2-J", "sherpa spricht in seinem Budget", dauer_ms, NAECHSTE_FRIST_MS)

P.kapitel("Schritt 16", "Auswahlregel konfigurierbar -- andere Schwelle")
for p in list(prozesse):
    if p.poll() is None:
        p.terminate()
mimic.modus = "ok"
daimon_toml(mimic_socket=str(MIMIC_SOCK), mimic_stimme="matthias",
            mimic_ab_zeichen=20, mimic_nur_warm=False)
dienst_starten()
vorher = len(mimic.speak)
antwort, _ = sprich(KURZ_79)
P.check("Schritt 16", "Schwelle 20: 79 Zeichen gehen an Mimic",
        len(mimic.speak) - vorher, 1)
P.check("Schritt 16", "engine meldet mimic", antwort.get("engine"), "mimic")

P.kapitel("Schritt 16", "Leerer Socket-Eintrag schaltet Mimic ab")
for p in list(prozesse):
    if p.poll() is None:
        p.terminate()
daimon_toml(mimic_socket="", mimic_ab_zeichen=20)
dienst_starten()
vorher = len(mimic.speak)
antwort, _ = sprich(LANG_80)
P.check("Schritt 16", "keine Mimic-Anfrage bei leerem Pfad",
        len(mimic.speak) - vorher, 0)
P.check("Schritt 16", "engine ist sherpa",
        str(antwort.get("engine", "")).startswith("sherpa"), True)

P.kapitel("Schritt 11", "Sherpa-Ausfall nimmt Mimic nicht mit")
for p in list(prozesse):
    if p.poll() is None:
        p.terminate()
daimon_toml(mimic_socket=str(MIMIC_SOCK), mimic_ab_zeichen=20)
leer = RT / "keine-stimmen"
leer.mkdir(exist_ok=True)
p = subprocess.Popen(
    [str(PYTHON), "-B", "-P", "-m", "daimon.face.tts", "--socket", str(TTS_SOCK),
     "--hub-socket", str(HUB_SOCK), "--modell-dir", str(leer)],
    cwd=str(TARGET), env=umgebung(), stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT, text=True)
prozesse.append(p)
if warten(TTS_SOCK, p, 30):
    time.sleep(0.2)
    vorher = len(mimic.speak)
    antwort, _ = sprich(LANG_80)
    P.check("Schritt 11", "ohne sherpa-Stimme spricht Mimic trotzdem",
            antwort.get("engine"), "mimic")
    P.check("Schritt 11", "Mimic wurde gefragt", len(mimic.speak) - vorher, 1)
    # Unter der Schwelle (hier 20) ist sherpa zustaendig -- und der kann nicht.
    antwort, _ = sprich("Fertig.")
    P.check("Schritt 11", "kurzer Text meldet ehrlich die sherpa-Absage",
            str(antwort.get("grund", "")).startswith("stimme_"), True)
else:
    P.check("Schritt 11", "Dienst startet ohne sherpa-Stimme", False, True)
    P.info((p.stdout.read() if p.stdout else "")[-1500:])

print("\n=== T-3.16 ===", flush=True)
for k in sorted(P.n):
    print(f"  {k}: {P.n[k] - P.rot[k]}/{P.n[k]}", flush=True)
print("ERGEBNIS: " + ("ROT" if P.fail else "GRUEN"), flush=True)
raise SystemExit(1 if P.fail else 0)
