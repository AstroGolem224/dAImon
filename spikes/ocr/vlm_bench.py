#!/usr/bin/env python3
"""Spike T-1.10, VLM-Arm — qwen3-vl:8b gegen dieselben drei Bilder.

Der Plan nennt ausdruecklich `qwen3-vl:8b`. Der erste Durchlauf dieser Sitzung
lief gegen `gemma4:26b`, weil das Modell schon lokal lag; das war eine
Abweichung vom Plan und die Zahlen daraus sind nur ein Nebenbefund. Dieses
Skript misst das im Plan benannte Modell.

Zeitbudget: die VLM-Aufrufe sind zwei Groessenordnungen teurer als tesseract.
Deshalb laeuft je Bild zuerst ein Aufwaermaufruf, daraus wird n abgeleitet und
das Gesamtbudget hart gedeckelt. Ein abgeschnittenes n ist ein Messumstand,
kein Fehler -- es steht in `n` und in `notes`.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import HERE, load, run_vlm, summarize  # noqa: E402

MODEL = "qwen3-vl:8b"
N_TARGET = 20
BUDGET_S = 1200.0  # 20 min fuer alles zusammen


def measure(name, budget_left):
    img = load(name)
    # Aufwaermen: der erste Aufruf laedt das Modell in den VRAM. Kein Messwert.
    warm_dt, warm_txt = run_vlm(img, model=MODEL)
    print(f"{name} aufwaermen {warm_dt:.0f} ms {len(warm_txt.strip())} Zeichen", flush=True)

    per_call_s = warm_dt / 1000.0
    n = max(1, min(N_TARGET, int(budget_left / max(per_call_s, 0.001))))

    times, txt = [], warm_txt
    for i in range(n):
        if sum(times) / 1000.0 > budget_left:
            break
        dt, txt = run_vlm(img, model=MODEL)
        times.append(dt)
        print(f"{name} {i + 1}/{n} {dt:.0f} ms {len(txt.strip())} Zeichen", flush=True)

    note = f"ollama {MODEL}, warm; das Vollbild wird vom Modell intern skaliert"
    if len(times) < N_TARGET:
        note += (
            f"; n={len(times)} statt {N_TARGET}, weil ein Aufruf "
            f"~{per_call_s:.0f} s kostet (Zeitbudget {BUDGET_S:.0f} s)"
        )
    if not txt.strip():
        note += "; LEERE AUSGABE -- das Modell liefert fuer dieses Bild keinen Text"

    (HERE / "text" / f"vlm-qwen3vl_{name}.txt").write_text(txt)
    return summarize("vlm-qwen3vl-8b", name, times, len(txt.strip()), note), sum(times) / 1000.0


def main():
    out, spent = [], 0.0
    for name in ("crop", "dense", "sparse"):
        left = BUDGET_S - spent
        if left <= 0:
            print(f"{name}: uebersprungen, Zeitbudget aufgebraucht", flush=True)
            continue
        try:
            row, used = measure(name, left)
        except Exception as exc:  # noqa: BLE001 -- ein Fehlschlag ist auch ein Ergebnis
            row, used = {
                "variant": "vlm-qwen3vl-8b",
                "image": name,
                "n": 0,
                "p50_ms": None,
                "p95_ms": None,
                "chars": 0,
                "notes": f"FEHLGESCHLAGEN: {type(exc).__name__}: {exc}",
            }, 0.0
        spent += used
        out.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    (HERE / "raw_vlm.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"fertig, {spent:.0f} s in Aufrufen verbraucht", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"Wanduhr {time.time() - t0:.0f} s", flush=True)
