#!/usr/bin/env python3
"""Blinder Prüfstand für T-3.16b: der Hub und die aktive Marke (Schritt 17).

Gebaut aus Mimic/PHASE2.md Schritt 17 und den Abnahmekriterien P2-H und P2-I.
Anders als T-3.16 läuft hier der **echte** Hub -- eine Attrappe kann nicht
belegen, dass `_tts_gesprochen` die Sperre nicht mehr fremdlöscht.

Der Vertrag in einem Satz: eine verspätete `gesprochen`-Meldung einer
abgebrochenen Äußerung A darf `tts_active` nicht löschen, nachdem B bereits
`beginnt` gemeldet hat. Und die Gegenkontrolle im selben Lauf: die Meldung der
**aktiven** Marke löscht sehr wohl -- sonst wäre das Kriterium mit einem
`return` am Anfang der Funktion zu bestehen.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
TARGET = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else REPO
PYTHON = REPO / ".venv/bin/python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)


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

    def kapitel(self, k: str, text: str) -> None:
        print(f"\n--- {k}: {text} ---", flush=True)


P = Pruefstand()

basis = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "daimon"
basis.mkdir(parents=True, exist_ok=True)
RT = Path(tempfile.mkdtemp(prefix="t316b-", dir=basis))
CONFIG, STATE, RUNTIME = RT / "config", RT / "state", RT / "runtime"
DAIMON_RT = RUNTIME / "daimon"
for d in (CONFIG / "daimon", STATE / "daimon", DAIMON_RT):
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
TTS_SOCK = DAIMON_RT / "tts.sock"
STATE_SOCK = DAIMON_RT / "state.sock"

prozesse: list[subprocess.Popen] = []


def aufraeumen() -> None:
    for p in reversed(prozesse):
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(2)
            except subprocess.TimeoutExpired:
                p.kill()
    shutil.rmtree(RT, ignore_errors=True)


import atexit  # noqa: E402

atexit.register(aufraeumen)


def unix_json(pfad: Path, obj: dict, timeout: float = 10) -> dict:
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


def warten(pfad: Path, prozess: subprocess.Popen, sekunden: float = 20) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if pfad.exists():
            return True
        if prozess.poll() is not None:
            return False
        time.sleep(0.03)
    return False


env = dict(os.environ)
env.update({"XDG_CONFIG_HOME": str(CONFIG), "XDG_STATE_HOME": str(STATE),
            "XDG_RUNTIME_DIR": str(RUNTIME), "PYTHONPATH": str(TARGET)})
hub = subprocess.Popen([str(PYTHON), "-B", "-P", "-m", "daimon.hub.daemon",
                        "--runtime-dir", str(DAIMON_RT)],
                       cwd=str(TARGET), env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
prozesse.append(hub)
if not warten(TTS_SOCK, hub, 30):
    print("FAIL: Hub kam nicht hoch", flush=True)
    raise SystemExit(1)


def tts_active() -> object:
    zustand = unix_json(STATE_SOCK, {"v": 1, "art": "zustand"})
    stimme = zustand.get("voice") or zustand.get("stimme") or {}
    return stimme.get("tts_active")


def freigabe(text: str) -> str:
    antwort = unix_json(TTS_SOCK, {"v": 1, "art": "freigabe", "kanal": "reaktion",
                                   "text": text})
    return str(antwort.get("marke", ""))


P.kapitel("P2-H", "verspaetetes `gesprochen` von A loescht Bs Sperre nicht")
marke_a = freigabe("Der erste Satz, der gleich abgebrochen wird.")
P.check("P2-H", "Freigabe A erteilt", bool(marke_a), True)
unix_json(TTS_SOCK, {"v": 1, "art": "beginnt", "marke": marke_a})
P.check("P2-H", "tts_active nach As beginnt", tts_active(), True)

# B unterbricht A. Zwei Kanaele, damit die Abkuehlung nicht dazwischenfunkt.
marke_b = unix_json(TTS_SOCK, {"v": 1, "art": "freigabe", "kanal": "rueckfrage",
                               "text": "Der zweite Satz uebernimmt."}).get("marke", "")
P.check("P2-H", "Freigabe B erteilt", bool(marke_b), True)
unix_json(TTS_SOCK, {"v": 1, "art": "beginnt", "marke": marke_b})
P.check("P2-H", "tts_active nach Bs beginnt", tts_active(), True)

# Jetzt trifft As verspaetete Meldung ein.
unix_json(TTS_SOCK, {"v": 1, "art": "gesprochen", "marke": marke_a})
P.check("P2-H", "As verspaetetes gesprochen laesst tts_active stehen",
        tts_active(), True)

P.kapitel("P2-I", "Gegenkontrolle -- die aktive Marke loescht sehr wohl")
unix_json(TTS_SOCK, {"v": 1, "art": "gesprochen", "marke": marke_b})
P.check("P2-I", "Bs gesprochen loescht tts_active", tts_active(), False)

P.kapitel("P2-I", "Abschluss ist idempotent")
unix_json(TTS_SOCK, {"v": 1, "art": "gesprochen", "marke": marke_b})
P.check("P2-I", "zweites gesprochen aendert nichts", tts_active(), False)

P.kapitel("P2-I", "eine fremde Marke bewegt die Sperre nicht")
# `rueckfrage` hat die kuerzeste Abkuehlung (3 s) -- abwarten statt umgehen,
# sonst misst dieser Abschnitt die Abkuehlung statt der Marke.
time.sleep(3.2)
marke_c = unix_json(TTS_SOCK, {"v": 1, "art": "freigabe", "kanal": "rueckfrage",
                               "text": "Der dritte Satz spricht wirklich."}).get("marke", "")
P.check("P2-I", "Freigabe C erteilt", bool(marke_c), True)
unix_json(TTS_SOCK, {"v": 1, "art": "beginnt", "marke": marke_c})
P.check("P2-I", "tts_active nach Cs beginnt", tts_active(), True)
unix_json(TTS_SOCK, {"v": 1, "art": "gesprochen", "marke": "gibt-es-nicht"})
P.check("P2-I", "unbekannte Marke loescht nicht", tts_active(), True)
unix_json(TTS_SOCK, {"v": 1, "art": "gesprochen", "marke": marke_c})
P.check("P2-I", "C raeumt selbst auf", tts_active(), False)

print("\n=== T-3.16b ===", flush=True)
for k in sorted(P.n):
    print(f"  {k}: {P.n[k] - P.rot[k]}/{P.n[k]}", flush=True)
print("ERGEBNIS: " + ("ROT" if P.fail else "GRUEN"), flush=True)
raise SystemExit(1 if P.fail else 0)
