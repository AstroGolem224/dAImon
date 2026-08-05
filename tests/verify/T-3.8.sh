#!/usr/bin/env bash
# Verifizierer fuer T-3.8: sherpa-onnx STT auf der CPU.
#
# Der Vertrag ist nicht entdeckt, sondern wortgetreu aus §2 des Plandokuments
# uebernommen. Qualitaet und Laufzeit werden extern gemessen: WER aus Referenz
# und Antworttext, Wanduhr um jede Anfrage, Prozessdaten aus /proc und
# Paketmetadaten aus genau dem laufenden Interpreter.
#
# Aufruf:
#   tests/verify/T-3.8.sh
#   DAIMON_FIXTURE=tests/fixtures/known-good/T-3.8 tests/verify/T-3.8.sh
#
# T-3.8.v2: das Aufraeumen killt die PROZESSGRUPPE (start_new_session +
# killpg), nicht nur Aktivator bzw. strace. Der Befund vom 05.08.: 34
# verwaiste daimon/gpu/stt.py-Prozesse, alle vom strace-Pfad -- terminate()
# auf strace detachet nur, der Tracee laeuft weiter und verwaist auf
# systemd --user. Am Ende steht eine Leck-Pruefung mit Positivkontrolle
# (Abschnitt "L").
set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$HIER/../.." || exit 1; pwd)"
TARGET="${DAIMON_FIXTURE:-$REPO}"
TARGET="$(cd "$TARGET" || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
MODELL="${DAIMON_T38_MODELL_DIR:-$REPO/spikes/stt-referenz/models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8}"
REFERENZ="$REPO/tests/fixtures/stt-referenz"
RT="$(mktemp -d)"
trap 'rm -rf -- "$RT"' EXIT INT TERM

echo "T-3.8 — STT-Pruefstand (blind aus Protokoll §2)"
echo "  Baum: $TARGET"
echo "  Modell: $MODELL"
echo "  Interpreter: $PY"
if [[ -n "${DAIMON_FIXTURE:-}" ]]; then echo "  Modus: FIXTURE"; else echo "  Modus: ARBEITSBAUM"; fi

"$PY" -B -P - "$REPO" "$TARGET" "$MODELL" "$REFERENZ" "$RT" "$PY" <<'PY'
from __future__ import annotations

import json
import math
import os
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import unicodedata
import wave
from collections import defaultdict
from pathlib import Path

REPO, TARGET, MODELL, REFERENZ, RT, PYTHON = map(Path, sys.argv[1:7])
URSPRUNG_PYTHON = PYTHON
STT = TARGET / "daimon/gpu/stt.py"
SERVICE = TARGET / "config/systemd/daimon-stt.service"
SOCKET_UNIT = TARGET / "config/systemd/daimon-stt.socket"
ENGINE = "sherpa-onnx-transducer"
MODELLNAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


class Pruefstand:
    def __init__(self) -> None:
        self.n = defaultdict(int)
        self.rot = defaultdict(int)
        self.fail = False
        self.befunde: list[str] = []

    def check(self, k: str, name: str, ist, soll) -> None:
        # Absichtlich ein Aufruf je Zusage: kein && kann Folgepruefungen tilgen.
        self.n[k] += 1
        if ist == soll:
            print(f"  ok   [{k}] {name}")
        else:
            print(f"  FAIL [{k}] {name} (erwartet {soll!r}, war {ist!r})")
            self.rot[k] += 1
            self.fail = True

    def bef(self, text: str) -> None:
        self.befunde.append(text)
        print(f"  !!   {text}")


P = Pruefstand()
prozesse: list[subprocess.Popen] = []
dateien = []

# Das Projekt-venv traegt aus dem ueberholten GPU-Pfad noch
# onnxruntime-gpu. Ein Fixture darf diesen Fremdbestand nicht erben, sonst
# kann das CPU-Gut-Muster K3 prinzipiell nie bestehen. Fuer Fixture-Laeufe
# entsteht deshalb ein hermetisches venv mit genau numpy und sherpa-onnx.
# Der Arbeitsbaumlauf benutzt dagegen unveraendert sein echtes Dienst-venv.
if os.environ.get("DAIMON_FIXTURE"):
    fixture_venv = RT / "fixture-venv"
    subprocess.run([str(URSPRUNG_PYTHON), "-m", "venv", "--without-pip",
                    str(fixture_venv)], check=True)
    fixture_python = fixture_venv / "bin/python"
    site = Path(subprocess.check_output(
        [str(fixture_python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        text=True).strip())
    quelle_site = Path(subprocess.check_output(
        [str(URSPRUNG_PYTHON), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        text=True).strip())
    for eintrag in quelle_site.iterdir():
        klein = eintrag.name.lower()
        if klein.startswith("numpy") or klein.startswith("sherpa_onnx"):
            (site / eintrag.name).symlink_to(eintrag, target_is_directory=eintrag.is_dir())
    PYTHON = fixture_python


def voraussetzung(name: str, ok: bool) -> None:
    P.check("V", name, bool(ok), True)


print("\n--- Voraussetzungen ---")
voraussetzung("Python-Interpreter vorhanden", PYTHON.is_file())
voraussetzung("systemd-socket-activate vorhanden", bool(subprocess.run(
    ["sh", "-c", "command -v systemd-socket-activate"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0))
voraussetzung("strace vorhanden", bool(subprocess.run(
    ["sh", "-c", "command -v strace"], stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL).returncode == 0))
voraussetzung("jq-unabhaengiger JSON-Treiber kann laufen", True)
voraussetzung("STT-Modul im geprueften Baum", STT.is_file())
voraussetzung("Service-Unit im geprueften Baum", SERVICE.is_file())
voraussetzung("Socket-Unit im geprueften Baum", SOCKET_UNIT.is_file())
voraussetzung("Referenz saetze.json vorhanden", (REFERENZ / "saetze.json").is_file())
voraussetzung("Referenz manifest.json vorhanden", (REFERENZ / "aufnahmen/manifest.json").is_file())
for name in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"):
    voraussetzung(f"Modelldatei {name}", (MODELL / name).is_file())
try:
    subprocess.run([str(PYTHON), "-c", "import sherpa_onnx, numpy"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    importe = True
except subprocess.CalledProcessError:
    importe = False
voraussetzung("sherpa_onnx und numpy importierbar", importe)
if P.fail:
    print("\nT-3.8: FEHLGESCHLAGEN — Voraussetzungen fehlen, nichts gemessen.")
    raise SystemExit(1)


def umgebung(config_home: Path, runtime: Path, ziel: Path = TARGET) -> dict[str, str]:
    # Keine gestrippte Umgebung: LANG/LC_* und alle anderen Werte bleiben da.
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ziel),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_RUNTIME_DIR": str(runtime),
        "VIRTUAL_ENV": str(PYTHON.parent.parent),
        "PATH": str(PYTHON.parent) + os.pathsep + env.get("PATH", ""),
    })
    return env


def konfiguration(wurzel: Path, modell: Path, threads: int) -> Path:
    cfg = wurzel / "daimon"
    cfg.mkdir(parents=True, exist_ok=True)
    datei = cfg / "daimon.toml"
    datei.write_text(f'[stt]\nmodell_dir = "{modell}"\nthreads = {threads}\n',
                     encoding="utf-8")
    return datei


def anfrage(sockpfad: Path, objekt=None, roh: bytes | None = None,
            timeout: float = 15.0) -> tuple[dict, float]:
    if roh is None:
        roh = json.dumps(objekt, ensure_ascii=False).encode("utf-8") + b"\n"
    t0 = time.monotonic_ns()
    klient = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    klient.settimeout(timeout)
    try:
        klient.connect(str(sockpfad))
        klient.sendall(roh)
        zeile = klient.makefile("rb").readline(1 << 20)
    finally:
        klient.close()
    wand_ms = (time.monotonic_ns() - t0) / 1_000_000
    return json.loads(zeile), wand_ms


def kinder(pid: int) -> list[int]:
    out = []
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            text = status.read_text(errors="replace")
            ppid = next(int(z.split()[1]) for z in text.splitlines()
                        if z.startswith("PPid:"))
            if ppid == pid:
                out.append(int(status.parent.name))
        except (OSError, StopIteration, ValueError):
            pass
    return out


def warte_datei(pfad: Path, prozess: subprocess.Popen, sekunden: float = 5.0) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if pfad.exists():
            return True
        if prozess.poll() is not None:
            return False
        time.sleep(0.02)
    return False


def basis_ok(antwort: dict, modellname: str = MODELLNAME) -> bool:
    return (antwort.get("v") == 1 and antwort.get("engine") == ENGINE
            and antwort.get("modell") == modellname
            and antwort.get("provider") == "cpu")


def positiv_sprache(sockpfad: Path, wav: Path, bezeichnung: str) -> dict:
    antwort, _ = anfrage(sockpfad, {"v": 1, "art": "transkribiere", "wav": str(wav)})
    P.check("K13", f"POSITIVKONTROLLE zu {bezeichnung}: gueltige Sprache ist ok",
            antwort.get("ok"), True)
    P.check("K13", f"POSITIVKONTROLLE zu {bezeichnung}: Sprache ergibt Text",
            bool(antwort.get("text")), True)
    return antwort


cfg_home = RT / "config"
runtime = RT / "runtime"
(runtime / "daimon").mkdir(parents=True)
konfiguration(cfg_home, MODELL, 8)
sockpfad = runtime / "daimon/stt.sock"
log = (RT / "aktivierung.log").open("wb")
env = umgebung(cfg_home, runtime)
# Die Locale wird neben der vererbten Gesamtumgebung ausdruecklich kopiert.
kopieren = ["PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "VIRTUAL_ENV",
            "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "PYTHONPATH",
            "PYTHONDONTWRITEBYTECODE"]
cmd = ["systemd-socket-activate", "-l", str(sockpfad)]
for name in kopieren:
    if name in env:
        cmd.extend(["-E", name])
cmd.extend([str(PYTHON), "-B", "-P", str(STT)])
launcher = subprocess.Popen(cmd, env=env, cwd=REPO, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
prozesse.append(launcher)
P.check("K10", "Socket-Aktivator lauscht", warte_datei(sockpfad, launcher), True)
P.check("K10", "vor der ersten Verbindung ist der Dienst inactive",
        len(kinder(launcher.pid)), 0)
P.check("K10", "Aktivator hat wirklich einen Unix-Socket angelegt",
        stat.S_ISSOCK(sockpfad.stat().st_mode), True)

# Erste Verbindung aktiviert den Dienst; die Modellladezeit darf hier anfallen.
zustand0, kalt_wand = anfrage(sockpfad, {"v": 1, "art": "zustand"}, timeout=30)
pid = int(zustand0.get("pid", 0) or 0)
P.check("K10", "Verbindung aktiviert einen laufenden Dienst", Path(f"/proc/{pid}").is_dir(), True)
P.check("K10", "Aktivator ist nach Verbindung in den Dienst uebergegangen",
        pid == launcher.pid or pid in kinder(launcher.pid), True)
P.check("K1", "Zustandsantwort belegt Engine/Modell/CPU zur Laufzeit",
        basis_ok(zustand0), True)
P.check("K4", "dieselbe Instanz meldet beide Sprachen ohne Schalter",
        zustand0.get("sprachen"), ["de", "en"])
P.check("K9", "Modell ist nach der Aktivierung geladen", zustand0.get("geladen"), True)
P.check("K9", "gemessene Ladezeit ist positiv", float(zustand0.get("ladezeit_ms", 0)) > 0, True)
P.check("K9", "vor Transkriptionen steht der Anfragezaehler auf null",
        zustand0.get("anfragen"), 0)

# Prozessherkunft: nicht nur PYTHONPATH behaupten, sondern absoluter Modulpfad
# in der echten Kommandozeile und derselbe Interpreter-Inode.
cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
cmdline_text = [x.decode("utf-8", "replace") for x in cmdline if x]
P.check("K1", "laufender Prozess nennt das STT-Modul aus dem geprueften Baum",
        str(STT) in cmdline_text, True)
P.check("K1", "laufender Prozess benutzt den gewaehlten Interpreter",
        os.path.samefile(f"/proc/{pid}/exe", PYTHON), True)
environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
P.check("K1", "PYTHONPATH des Prozesses zeigt auf den geprueften Baum",
        f"PYTHONPATH={TARGET}".encode() in environ, True)
P.check("K1", "LANG ist im laufenden Dienst vorhanden (keine C-Locale-Falle)",
        any(x.startswith(b"LANG=") and x != b"LANG=" for x in environ), True)

saetze = json.loads((REFERENZ / "saetze.json").read_text(encoding="utf-8"))["saetze"]
manifest = json.loads((REFERENZ / "aufnahmen/manifest.json").read_text(encoding="utf-8"))["aufnahmen"]
antworten: dict[str, dict] = {}
wand: dict[str, float] = {}
for satz in saetze:
    wav = (REFERENZ / "aufnahmen" / manifest[satz["id"]]["datei"]).resolve()
    ant, ms = anfrage(sockpfad, {"v": 1, "art": "transkribiere", "wav": str(wav)})
    antworten[satz["id"]] = ant
    wand[satz["id"]] = ms
    print(f"  mess {satz['id']}: selbst={ant.get('latenz_ms')} ms, wand={ms:.1f} ms, text={ant.get('text')!r}")

print("\n--- K1: Laufzeitvertrag und rohe Modellausgabe ---")
for sid, ant in antworten.items():
    P.check("K1", f"Antwort {sid} belegt Engine/Modell/CPU", basis_ok(ant), True)
    P.check("K1", f"Antwort {sid} ist eine erfolgreiche Transkription", ant.get("ok"), True)

# Unabhaengiges Orakel: dasselbe Modell direkt auf Satz 01. Dadurch faellt eine
# Nachbearbeitung auf, obwohl die WER-Normalisierung Gross-/Kleinschreibung und
# Satzzeichen absichtlich neutralisiert.
oracle_code = r'''
import json, sys, time, wave
from pathlib import Path
import numpy as np
import sherpa_onnx
m, w = Path(sys.argv[1]), Path(sys.argv[2])
with wave.open(str(w), "rb") as f:
    rate = f.getframerate()
    x = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype("float32") / 32768.0
r = sherpa_onnx.OfflineRecognizer.from_transducer(
 encoder=str(m/"encoder.int8.onnx"), decoder=str(m/"decoder.int8.onnx"),
 joiner=str(m/"joiner.int8.onnx"), tokens=str(m/"tokens.txt"),
 num_threads=8, provider="cpu", model_type="nemo_transducer")
s=r.create_stream(); s.accept_waveform(rate,x); r.decode_stream(s)
print(json.dumps({"text": s.result.text.strip()}, ensure_ascii=False))
'''
wav01 = (REFERENZ / "aufnahmen" / manifest["01"]["datei"]).resolve()
oracle = subprocess.run([str(PYTHON), "-B", "-P", "-c", oracle_code,
                         str(MODELL), str(wav01)], text=True,
                        capture_output=True, timeout=30)
P.check("K1", "unabhaengiger sherpa-Orakellauf gelingt (POSITIVKONTROLLE)",
        oracle.returncode, 0)
try:
    oracle_text = json.loads(oracle.stdout)["text"]
except (json.JSONDecodeError, KeyError):
    oracle_text = "__ORAKEL_FEHLT__"
P.check("K1", "Orakel erkennt auf Sprache nichtleeren Rohtext",
        bool(oracle_text and oracle_text != "__ORAKEL_FEHLT__"), True)
P.check("K1", "Dienst gibt exakt rohe Modellausgabe aus, ohne Schoenung",
        antworten["01"].get("text"), oracle_text)

print("\n--- K2/K3: kein CUDA, Paketbestand des laufenden Interpreters ---")
maps = Path(f"/proc/{pid}/maps").read_text(errors="replace")
P.check("K2", "/proc/<pid>/maps ist lesbar (POSITIVKONTROLLE)", len(maps.splitlines()) > 0, True)
P.check("K2", "keine CUDA/TensorRT-Bibliothek im Adressraum",
        any(x in maps.lower() for x in ("libcuda", "libcudart", "libnvinfer")), False)
fds = []
for f in Path(f"/proc/{pid}/fd").iterdir():
    try:
        fds.append(os.readlink(f))
    except OSError:
        pass
P.check("K2", "/proc/<pid>/fd ist lesbar und nichtleer (POSITIVKONTROLLE)", len(fds) > 0, True)
P.check("K2", "keine DRI- oder NVIDIA-Geraetedeskriptoren",
        any("/dev/dri/" in x or "/dev/nvidia" in x for x in fds), False)
nvidia = subprocess.run(["nvidia-smi", "--query-compute-apps=pid",
                         "--format=csv,noheader,nounits"], text=True,
                        capture_output=True)
P.check("K2", "nvidia-smi ist abfragbar (POSITIVKONTROLLE)", nvidia.returncode, 0)
gpu_pids = {z.strip() for z in nvidia.stdout.splitlines() if z.strip()}
P.check("K2", "eigene PID steht nicht in nvidia-smi Compute-Prozessen", str(pid) in gpu_pids, False)

paket_code = r'''
import importlib.metadata, json
print(json.dumps(sorted({d.metadata.get("Name", "") for d in importlib.metadata.distributions()})))
'''
paketlauf = subprocess.run([str(PYTHON), "-B", "-c", paket_code], text=True,
                           capture_output=True)
pakete = {x.lower().replace("_", "-") for x in json.loads(paketlauf.stdout or "[]")}
P.check("K3", "Paketliste des Dienst-Interpreters ist auswertbar (POSITIVKONTROLLE)",
        len(pakete) > 0, True)
P.check("K3", "sherpa-onnx steht in genau dieser Paketliste (POSITIVKONTROLLE)",
        "sherpa-onnx" in pakete, True)
P.check("K3", "onnxruntime-gpu fehlt", "onnxruntime-gpu" in pakete, False)
P.check("K3", "alle nvidia-*-pip-Pakete fehlen",
        any(x.startswith("nvidia-") for x in pakete), False)

ZAHLWORT = {
 "null":"0","eins":"1","ein":"1","eine":"1","zwei":"2","drei":"3",
 "vier":"4","fünf":"5","sechs":"6","sieben":"7","acht":"8","neun":"9",
 "zehn":"10","elf":"11","zwölf":"12","dreizehn":"13","vierzehn":"14",
 "fünfzehn":"15","sechzehn":"16","siebzehn":"17","achtzehn":"18",
 "neunzehn":"19","zwanzig":"20","dreißig":"30","vierzig":"40",
 "fünfzig":"50","sechzig":"60","hundert":"100","one":"1","two":"2",
 "three":"3","four":"4","five":"5","six":"6","seven":"7","eight":"8",
 "nine":"9","ten":"10"}


def norm(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    text = text.replace("-", "").replace("\u2011", "")
    text = "".join(z if (z.isalnum() or z.isspace()) else " " for z in text)
    return [ZAHLWORT.get(w, w) for w in text.split()]


def abstand(ref: list[str], hyp: list[str]) -> int:
    alt = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        neu = [i] + [0] * len(hyp)
        for j, hw in enumerate(hyp, 1):
            neu[j] = min(alt[j] + 1, neu[j - 1] + 1, alt[j - 1] + (rw != hw))
        alt = neu
    return alt[-1]


print("\n--- K4–K7: dieselbe Instanz, externe WER und Stille ---")
zustand1, _ = anfrage(sockpfad, {"v": 1, "art": "zustand"})
P.check("K4", "alle 20 Sprachaufnahmen liefen durch dieselbe PID",
        zustand1.get("pid"), pid)
P.check("K4", "18 deutsche Antworten sind nichtleer",
        sum(bool(antworten[f"{i:02d}"].get("text")) for i in range(1, 19)), 18)
P.check("K4", "2 englische Antworten sind nichtleer",
        sum(bool(antworten[f"{i:02d}"].get("text")) for i in range(19, 21)), 2)
gruppen = {"de": [], "en": []}
for satz in saetze:
    if satz["sprache"] in gruppen:
        r, h = norm(satz["text"]), norm(antworten[satz["id"]].get("text", ""))
        gruppen[satz["sprache"]].append((abstand(r, h), len(r)))
werte_wer = {}
for sprache, k in (("de", "K5"), ("en", "K6")):
    fehler = sum(x for x, _ in gruppen[sprache])
    woerter = sum(x for _, x in gruppen[sprache])
    wer = fehler / woerter
    werte_wer[sprache] = wer
    print(f"  {sprache}: {fehler} Fehler / {woerter} Referenzwoerter = {wer:.3%}")
    P.check(k, f"{sprache}: Referenzwortzahl ist positiv (POSITIVKONTROLLE)", woerter > 0, True)
P.check("K5", "WER deutsch <= 7,2 %", werte_wer["de"] <= 0.072, True)
P.check("K6", "WER englisch <= 2,0 %", werte_wer["en"] <= 0.020, True)
P.check("K7", "POSITIVKONTROLLE: unmittelbar vor Stille kam englische Sprache",
        bool(antworten["20"].get("text")), True)
P.check("K7", "Aufnahme 21 bleibt exakt stumm", antworten["21"].get("text"), "")

print("\n--- K8/K9: Selbstauskunft gegen Wanduhr, Modell bleibt geladen ---")
selbst = [float(antworten[f"{i:02d}"]["latenz_ms"]) for i in range(1, 22)]
wandwerte = [wand[f"{i:02d}"] for i in range(1, 22)]
def p95(xs: list[float]) -> float:
    ys = sorted(xs)
    return ys[math.ceil(0.95 * len(ys)) - 1]
print(f"  p95 selbst={p95(selbst):.1f} ms, p95 wand={p95(wandwerte):.1f} ms")
P.check("K8", "21 Selbstauskuenfte sind positiv (Messung lief)", all(x > 0 for x in selbst), True)
P.check("K8", "Wanduhr ist bei jeder Anfrage positiv (POSITIVKONTROLLE)", all(x > 0 for x in wandwerte), True)
P.check("K8", "Latenz-p95 der Erkennung <= 300 ms", p95(selbst) <= 300.0, True)
differenzen = [abs(w - s) for w, s in zip(wandwerte, selbst)]
P.check("K8", "Selbstauskunft und Wanduhr weichen nie stark (>100 ms) ab",
        max(differenzen) <= 100.0, True)
id5 = min(antworten, key=lambda sid: abs(float(antworten[sid].get("audio_s", 999)) - 5.0))
P.check("K8", "5-Sekunden-Messpunkt ist eine Sprachaufnahme",
        next(s["sprache"] for s in saetze if s["id"] == id5) != "stille", True)
print(f"  getrennt: Aufnahme {id5}, audio={antworten[id5]['audio_s']} s, "
      f"selbst={antworten[id5]['latenz_ms']} ms, wand={wand[id5]:.1f} ms")
P.check("K8", "5-Sekunden-Aeusserung liegt ebenfalls <= 300 ms",
        float(antworten[id5]["latenz_ms"]) <= 300.0, True)
median_2n = sorted(wandwerte[1:])[(len(wandwerte[1:]) - 1) // 2]
ladezeit = float(zustand0["ladezeit_ms"])
print(f"  Median Wanduhr Anfragen 2..n={median_2n:.1f} ms; halbe Ladezeit={ladezeit/2:.1f} ms")
P.check("K9", "Median Anfragen 2..n < 420 ms", median_2n < 420.0, True)
P.check("K9", "Median Anfragen 2..n < halbe gemessene Ladezeit", median_2n < ladezeit / 2, True)
P.check("K9", "Anfragezaehler stieg um alle Transkriptionen",
        int(zustand1.get("anfragen", -1)) >= 21, True)
time.sleep(1.0)
P.check("K10", "kein Leerlauf-Exit: Dienst lebt nach Wartezeit", Path(f"/proc/{pid}").is_dir(), True)
zustand2, _ = anfrage(sockpfad, {"v": 1, "art": "zustand"})
P.check("K10", "Folgeverbindung erreicht denselben aktiven Prozess", zustand2.get("pid"), pid)

print("\n--- K13: fester Absagevertrag, jeweils mit Sprach-Kanarienvogel ---")
sprachwav = wav01
negative: list[dict] = []

def absage(name: str, antwort: dict, grund: str) -> None:
    negative.append(antwort)
    P.check("K13", f"{name}: ok=false", antwort.get("ok"), False)
    P.check("K13", f"{name}: genau der Grund {grund}", antwort.get("grund"), grund)
    P.check("K13", f"{name}: Detailmeldung vorhanden", bool(antwort.get("meldung")), True)
    P.check("K13", f"{name}: kein gemeinsames error-Feld", "error" in antwort, False)
    P.check("K13", f"{name}: Engine/Modell/CPU auch in Absage", basis_ok(antwort), True)
    positiv_sprache(sockpfad, sprachwav, name)

ant, _ = anfrage(sockpfad, roh=b'{dies ist kein json}\n')
absage("unlesbare JSON-Zeile", ant, "unlesbar")
ant, _ = anfrage(sockpfad, {"v": 1, "art": "zaubere"})
absage("unbekannte art", ant, "unbekannte_art")
ant, _ = anfrage(sockpfad, {"v": 1, "art": "transkribiere",
                            "wav": str((RT / "gibt-es-nicht.wav").resolve())})
absage("fehlende Datei", ant, "datei_fehlt")

# Stereo und 8-bit aus demselben gueltigen Sprachsignal; 8 kHz mono s16 ist
# dagegen laut Vertrag erlaubt und eine eigene Positivkontrolle.
with wave.open(str(sprachwav), "rb") as src:
    params = src.getparams()
    mono = src.readframes(src.getnframes())
stereo = RT / "stereo.wav"
samples = struct.unpack("<" + "h" * (len(mono) // 2), mono)
stereoroh = b"".join(struct.pack("<hh", x, x) for x in samples)
with wave.open(str(stereo), "wb") as out:
    out.setnchannels(2); out.setsampwidth(2); out.setframerate(params.framerate)
    out.writeframes(stereoroh)
ant, _ = anfrage(sockpfad, {"v": 1, "art": "transkribiere", "wav": str(stereo)})
absage("Stereo-WAV", ant, "format_falsch")

achtbit = RT / "achtbit.wav"
roh8 = bytes(max(0, min(255, (x >> 8) + 128)) for x in samples)
with wave.open(str(achtbit), "wb") as out:
    out.setnchannels(1); out.setsampwidth(1); out.setframerate(params.framerate)
    out.writeframes(roh8)
ant, _ = anfrage(sockpfad, {"v": 1, "art": "transkribiere", "wav": str(achtbit)})
absage("8-bit-WAV", ant, "format_falsch")

achtkhz = RT / "achtkhz.wav"
samples8 = samples[::2]
with wave.open(str(achtkhz), "wb") as out:
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000)
    out.writeframes(struct.pack("<" + "h" * len(samples8), *samples8))
ant8, _ = anfrage(sockpfad, {"v": 1, "art": "transkribiere", "wav": str(achtkhz)})
P.check("K13", "abweichende 8-kHz-Rate wird angenommen (POSITIVKONTROLLE)", ant8.get("ok"), True)
P.check("K13", "tatsaechliche 8-kHz-Rate wird sichtbar gemeldet", ant8.get("rate"), 8000)

print("\n--- K11/K13: fehlendes Modell, kein Netz und --socket-Pfad ---")
miss_cfg = RT / "config-fehlt"
miss_runtime = RT / "runtime-fehlt"
miss_runtime.mkdir()
miss_modell = RT / "absichtlich-fehlendes-modell"
konfiguration(miss_cfg, miss_modell, 3)
miss_sock = miss_runtime / "direkt.sock"
strace_log = RT / "modell-fehlt.strace"
miss_log = (RT / "modell-fehlt.log").open("wb")
miss_cmd = ["strace", "-f", "-e", "trace=network", "-o", str(strace_log),
            str(PYTHON), "-B", "-P", str(STT), "--socket", str(miss_sock)]
miss = subprocess.Popen(miss_cmd, cwd=REPO, env=umgebung(miss_cfg, miss_runtime),
                        stdout=miss_log, stderr=subprocess.STDOUT,
                        start_new_session=True)
prozesse.append(miss)
P.check("K11", "Direktstart mit --socket legt den Socket an",
        warte_datei(miss_sock, miss), True)
miss_state, _ = anfrage(miss_sock, {"v": 1, "art": "zustand"})
P.check("K11", "modell_dir aus [stt] wird beachtet: nicht geladen",
        miss_state.get("geladen"), False)
P.check("K11", "threads aus [stt] wird beachtet", miss_state.get("threads"), 3)
t0 = time.monotonic()
miss_ant, _ = anfrage(miss_sock, {"v": 1, "art": "transkribiere", "wav": str(sprachwav)})
absage_dauer = time.monotonic() - t0
negative.append(miss_ant)
P.check("K13", "fehlendes Modell: ok=false", miss_ant.get("ok"), False)
P.check("K13", "fehlendes Modell: Grund modell_fehlt", miss_ant.get("grund"), "modell_fehlt")
P.check("K13", "fehlendes Modell: Detailmeldung", bool(miss_ant.get("meldung")), True)
P.check("K13", "fehlendes Modell: Engine/konfiguriertes Modell/CPU",
        basis_ok(miss_ant, miss_modell.name), True)
P.check("K11", "fehlendes Modell wird geordnet und schnell abgelehnt", absage_dauer < 2.0, True)
positiv_sprache(sockpfad, sprachwav, "modell_fehlt")
time.sleep(0.1)
netzspur = strace_log.read_text(errors="replace") if strace_log.exists() else ""
P.check("K11", "strace sah AF_UNIX-Netzaufrufe (POSITIVKONTROLLE)", "AF_UNIX" in netzspur, True)
P.check("K11", "bei fehlendem Modell kein AF_INET/AF_INET6-Aufruf",
        "AF_INET" in netzspur, False)

print("\n--- K10–K12: feste Socket- und Haertungs-Units ---")
def ohne_kommentare(pfad: Path) -> tuple[str, str]:
    roh = pfad.read_text(encoding="utf-8")
    aktiv = "\n".join(z for z in roh.splitlines() if not z.lstrip().startswith("#"))
    return roh, aktiv

svc_roh, svc = ohne_kommentare(SERVICE)
sck_roh, sck = ohne_kommentare(SOCKET_UNIT)
P.check("K10", "Socketpfad ist exakt %t/daimon/stt.sock",
        "ListenStream=%t/daimon/stt.sock" in sck.splitlines(), True)
P.check("K10", "SocketMode=0600", "SocketMode=0600" in sck.splitlines(), True)
P.check("K10", "Accept=no", "Accept=no" in sck.splitlines(), True)
P.check("K10", "Socket wird ueber sockets.target aktiviert",
        "WantedBy=sockets.target" in sck.splitlines(), True)
P.check("K10", "Service hat keinen Leerlaufzeit-Schalter",
        "idle" in svc.lower() or "max-secs" in svc.lower(), False)
P.check("K11", "RestrictAddressFamilies ist exakt AF_UNIX",
        "RestrictAddressFamilies=AF_UNIX" in svc.splitlines(), True)
for direktive in (
    "NoNewPrivileges=yes", "CapabilityBoundingSet=", "ProtectSystem=strict",
    "ProtectHome=read-only", "ProtectProc=invisible", "ProcSubset=pid",
    "PrivateTmp=yes", "LimitCORE=0", "UMask=0077", "PrivateDevices=yes",
    "MemoryDenyWriteExecute=yes"):
    P.check("K12", f"Service: {direktive}", direktive in svc.splitlines(), True)
for pfadname, text in (("Service", svc), ("Socket", sck)):
    P.check("K12", f"{pfadname}: RuntimeDirectory=daimon",
            "RuntimeDirectory=daimon" in text.splitlines(), True)
    P.check("K12", f"{pfadname}: RuntimeDirectoryPreserve=yes",
            "RuntimeDirectoryPreserve=yes" in text.splitlines(), True)
if "~@resources" not in svc:
    P.check("K12", "fehlendes ~@resources ist in der Unit begruendet",
            "resources" in svc_roh.lower() and any(w in svc_roh.lower()
            for w in ("sperr", "toetet", "tötet", "braucht")), True)

print("\n--- K13: Abschluss-Kanarienvogel nach allen Absagen ---")
schluss = positiv_sprache(sockpfad, sprachwav, "allen Absagefaellen")
P.check("K13", "Schluss-Kanarienvogel belegt weiter dieselbe PID",
        anfrage(sockpfad, {"v": 1, "art": "zustand"})[0].get("pid"), pid)

# Rohwerte als Evidence, nicht nur die daraus gebildeten Schwellen.
evidence_dir = REPO / "tests/evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)
suffix = TARGET.name if os.environ.get("DAIMON_FIXTURE") else "arbeitsbaum"
evidence = evidence_dir / f"T-3.8-stt-{suffix}.json"
daten = {
    "task": "T-3.8", "baum": str(TARGET), "zeit": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "wer_de": werte_wer["de"], "wer_en": werte_wer["en"],
    "latenz_selbst_ms": selbst, "latenz_wand_ms": wandwerte,
    "latenz_p95_selbst_ms": p95(selbst), "latenz_p95_wand_ms": p95(wandwerte),
    "aufnahme_5s": id5, "median_anfragen_2_bis_n_ms": median_2n,
    "ladezeit_ms": ladezeit, "pid": pid,
}
evidence.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"  Evidence: {evidence}")

print("\n--- Abrechnung je Kriterium ---")
gesamt = gesamt_rot = 0
for k in ["V"] + [f"K{i}" for i in range(1, 14)]:
    n, rot = P.n[k], P.rot[k]
    gesamt += n; gesamt_rot += rot
    print(f"  {k:>3}: {n:3d} Pruefungen, {rot} rot")
print(f"  gesamt: {gesamt} Pruefungen, {gesamt_rot} rot")

print("\n--- Auslegungen und Grenzen ---")
print("  (1) p95 ist nearest-rank: sortiert, Index ceil(0,95*n)-1; alle 21 Aufnahmen zaehlen.")
print(f"  (2) '5-Sekunden-Aeusserung' ist die Aufnahme mit der Dauer am naechsten an 5,0 s: {id5}.")
print("  (3) 'stark abweichend' zwischen Dienstwert und Wanduhr bedeutet hier >100 ms je Anfrage.")
print("  (4) Socket-Aktivierung wird mit systemd-socket-activate als inactive->Verbindung->active")
print("      gemessen; die Fixture-Units werden nicht in die echte Nutzersitzung installiert.")
print("  (5) Kein Netz bei fehlendem Modell wird an strace-Netzsyscalls gemessen; AF_UNIX ist")
print("      der Positivkanarienvogel, AF_INET/AF_INET6 muss fehlen.")
print("  (6) Nicht geprueft: andere Stimmen, Nebengeraeusche, Entfernung und Spontansprache;")
print("      diese vier Grenzen stammen aus herkunft.json. Ebenso keine Diarisierung oder Wortzeiten.")

# Aufraeumen mit Leck-Pruefung (T-3.8.v2). Der Befund vom 05.08.: 34 verwaiste
# daimon/gpu/stt.py-Prozesse, alle vom strace-Pfad -- `terminate()` auf strace
# DETACHET nur, der Tracee laeuft weiter und verwaist auf systemd --user.
# Deshalb laufen beide Dienststarts in eigenen Prozessgruppen
# (start_new_session=True), und aufgeraeumt wird die GRUPPE: killpg trifft
# strace und Tracee bzw. Aktivator und Dienst gemeinsam. terminate/kill auf
# dem direkten Kind bleibt der Rueckfall.
def stt_leichen() -> list[str]:
    """PIDs laufender stt.py-Prozesse DIESES Laufs (RT in Kommandozeile oder
    Umgebung -- der socket-aktivierte Dienst traegt RT nur in der Umgebung)."""
    treffer = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            args = (p / "cmdline").read_bytes().replace(b"\0", b" ")
            if b"daimon/gpu/stt.py" not in args:
                continue
            um = (p / "environ").read_bytes()
        except OSError:
            continue
        if str(RT).encode() in args or str(RT).encode() in um:
            treffer.append(p.name)
    return treffer

P.check("L", "POSITIVKONTROLLE: waehrend des Laufs liefen Dienste aus diesem RT",
        len(stt_leichen()) >= 2, True)
for prozess in reversed(prozesse):
    if prozess.poll() is None:
        try:
            os.killpg(prozess.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            prozess.terminate()
for prozess in reversed(prozesse):
    try:
        prozess.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(prozess.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            prozess.kill()
        prozess.wait(timeout=2)
time.sleep(0.3)
leichen = stt_leichen()
P.check("L", "nach dem Aufraeumen ist KEIN stt.py-Prozess dieses Laufs uebrig",
        leichen, [])
print(f"    L: {P.n['L']:3d} Pruefungen, {P.rot['L']} rot")
print(f"  gesamt mit Leck-Pruefung: {gesamt + P.n['L']} Pruefungen, "
      f"{gesamt_rot + P.rot['L']} rot")
log.close(); miss_log.close()

if P.fail:
    print("\nT-3.8: FEHLGESCHLAGEN")
    raise SystemExit(1)
print("\nT-3.8: gruen — alle 13 Kriterien wurden einzeln abgerechnet.")
raise SystemExit(0)
PY
