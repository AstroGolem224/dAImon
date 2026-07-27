#!/usr/bin/env python3
"""Interleaved re-run: the CLI-vs-FFI effect is smaller than the drift seen in
a blocked run, so round-robin the variants within each iteration."""

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import CApi, Worker, HERE, TESSDATA, LANG, PSM, load, pct, run_cli, summarize


def main(images, n=20):
    import tesserocr

    api = CApi()
    w_ = Worker()
    tapi = tesserocr.PyTessBaseAPI(path=TESSDATA, lang=LANG, psm=tesserocr.PSM.SPARSE_TEXT)

    imgs = {k: load(k) for k in images}
    raw = {k: (np.asarray(v).tobytes(), v.width, v.height) for k, v in imgs.items()}
    variants = ["cli", "ffi", "tesserocr", "worker"]
    times = {(v, i): [] for v in variants for i in images}
    subt = {i: [] for i in images}
    texts = {}

    for it in range(n + 1):
        for name in images:
            img = imgs[name]
            buf, iw, ih = raw[name]

            tot, sub, txt = run_cli(img)
            if it:
                times[("cli", name)].append(tot); subt[name].append(sub)
            texts[("cli", name)] = txt

            t0 = time.perf_counter(); txt = api.ocr(buf, iw, ih)
            if it: times[("ffi", name)].append((time.perf_counter() - t0) * 1000)
            texts[("ffi", name)] = txt

            t0 = time.perf_counter(); tapi.SetImage(img); txt = tapi.GetUTF8Text()
            if it: times[("tesserocr", name)].append((time.perf_counter() - t0) * 1000)
            texts[("tesserocr", name)] = txt

            t0 = time.perf_counter(); txt = w_.ocr(buf, iw, ih)
            if it: times[("worker", name)].append((time.perf_counter() - t0) * 1000)
            texts[("worker", name)] = txt
        print(f"iter {it}/{n}", flush=True)

    (HERE / "text").mkdir(exist_ok=True)
    out = []
    for v in variants:
        for name in images:
            ts = times[(v, name)]
            txt = texts[(v, name)]
            note = {
                "cli": "one subprocess + temp PNG per call (screenpipe style); "
                       f"subprocess-only p50 {pct(subt[name],50):.0f} ms",
                "ffi": "ctypes -> system libtesseract 5.5.3, one API reused, no disk",
                "tesserocr": "tesserocr wheel, bundles tesseract 5.5.1, one API reused",
                "worker": "separate process holding the API, raw RGB over a unix socketpair",
            }[v] + " [interleaved run]"
            r = summarize(v, name, ts, len(txt.strip()), note)
            out.append(r)
            print(json.dumps(r), flush=True)
            (HERE / "text" / f"{v}_{name}.txt").write_text(txt)

    w_.close()
    tapi.End()
    (HERE / f"raw_interleaved_{'_'.join(images)}.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1].split(","), int(sys.argv[2]))
