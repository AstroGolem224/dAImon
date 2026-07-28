"""Gemeinsame Messhilfen fuer T-1.12. Bewusst klein gehalten."""
import json
import math
import pathlib
import subprocess
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE / "results.json"

# Vollstaendiges Schema einer results.json-Zeile. Der Verifizierer prueft dagegen,
# deshalb steht es hier und nicht verstreut in den Benchmarks.
FIELDS = (
    "arm model license gated loaded backend cold_start_ms p50_ms p95_ms "
    "ttfa_ms ttfa_reason rtf vram_idle_mb vram_peak_mb vram_after_exit_mb "
    "wer audio_source n verdict"
).split()


def vram_mb() -> int:
    """Belegter VRAM der GPU 0. Ganzes Geraet, nicht nur der eigene Prozess --
    genau das misst der Verifizierer auch, sonst waeren die Zahlen unvergleichbar."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip().splitlines()[0])


class VramWatch:
    """Sampelt VRAM im Hintergrund und haelt das Maximum fest.

    ponytail: 100-ms-Abtastung statt NVML-Callbacks. Verfehlt Spitzen, die
    kuerzer sind -- fuer Modell-Laden und Inferenz im Sekundenbereich reicht es.
    Auf NVML umstellen, falls eine Spitze zwischen zwei Proben verschwindet.
    """

    def __init__(self, interval=0.1):
        self.interval = interval
        self.idle = vram_mb()
        self.peak = self.idle
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.peak = max(self.peak, vram_mb())

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=1.0)
        self.peak = max(self.peak, vram_mb())


def percentile(values, q):
    """Nearest-rank. Bei n=20 und q=95 ist das der 19. Wert.

    ponytail: keine Interpolation. Bei n>=20 ist der Unterschied kleiner als die
    Streuung der Messung; auf numpy.percentile wechseln, falls je n<10 gemessen wird.
    """
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return round(s[k], 2)


def normalize(text: str) -> list:
    """Kleinschreibung, Satzzeichen weg. Ohne das misst die WER vor allem, ob die
    Engine Kommas setzt -- die Referenztexte haben keine. Kostet sonst ~20 Punkte.

    ponytail: Ziffern bleiben als Ziffern stehen, "18" und "achtzehn" zaehlen als
    Fehler. Erst auf eine Zahlwort-Normalisierung wechseln, wenn eine Engine
    deshalb schlechter aussieht als sie ist.
    """
    return "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower()).split()


def wer(reference: str, hypothesis: str) -> float:
    """Wortfehlerrate ueber Levenshtein auf Wortebene."""
    r = normalize(reference)
    h = normalize(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, (time.perf_counter() - t0) * 1000


def append_result(row: dict):
    """Eine Zeile in results.json. Unbekannte Felder fliegen auf, fehlende werden
    null -- damit der Verifizierer nicht ueber Tippfehler stolpert statt ueber Zahlen."""
    unknown = set(row) - set(FIELDS)
    if unknown:
        raise KeyError(f"Feld nicht im Schema: {sorted(unknown)}")
    row = {k: row.get(k) for k in FIELDS}
    rows = json.loads(RESULTS.read_text()) if RESULTS.exists() else []
    rows = [r for r in rows if not (r["arm"] == row["arm"] and r["model"] == row["model"])]
    rows.append(row)
    RESULTS.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    print(f"-> results.json: {row['arm']}/{row['model']}")


def demo():
    assert wer("das ist ein test", "das ist ein test") == 0.0
    assert wer("das ist ein test", "das ist kein test") == 0.25
    assert wer("a b c d", "a b") == 0.5
    assert wer("", "") == 0.0 and wer("", "x") == 1.0
    # Satzzeichen und Grossschreibung duerfen die Zahl nicht bewegen
    assert wer("das ist ein test", "Das ist, ein Test!") == 0.0
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile(list(range(1, 21)), 95) == 19
    assert percentile([], 95) is None
    assert vram_mb() >= 0
    print("common.py: ok")


if __name__ == "__main__":
    demo()
