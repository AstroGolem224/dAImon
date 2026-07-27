#!/usr/bin/env python3
"""Follow-up measurements: region detection cost, PSM sensitivity,
tessdata_fast vs tessdata (standard), and text-quality dumps."""

import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import region_detect as rd
from bench import CApi, IMAGES, load, pct, HERE, TESSDATA


def region_bench(n=20):
    out = {}
    stages = {}
    for name in IMAGES:
        a = np.asarray(load(name))
        ts = []
        for i in range(n + 1):
            t0 = time.perf_counter()
            boxes, u = rd.detect(a)
            if i:
                ts.append((time.perf_counter() - t0) * 1000)
        out[name] = {
            "p50_ms": round(pct(ts, 50), 1),
            "p95_ms": round(pct(ts, 95), 1),
            "n": len(ts),
            "boxes": len(boxes),
            "union": u,
            "frame_px": [a.shape[1], a.shape[0]],
            "union_area_fraction": round(
                ((u[2] - u[0]) * (u[3] - u[1])) / (a.shape[0] * a.shape[1]), 3
            ) if u else None,
            "boxes_area_fraction": round(
                sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
                / (a.shape[0] * a.shape[1]), 3
            ),
        }
        # per-stage breakdown
        st = {}
        t = time.perf_counter(); g = rd.to_gray(a); st["gray"] = (time.perf_counter()-t)*1000
        t = time.perf_counter()
        grad = (rd._dilate(g,3,3).astype(np.int16) - rd._erode(g,3,3).astype(np.int16)).astype(np.uint8)
        st["gradient"] = (time.perf_counter()-t)*1000
        t = time.perf_counter(); thr = rd.otsu(grad); st["otsu"] = (time.perf_counter()-t)*1000
        b = (grad > thr).view(np.uint8)
        t = time.perf_counter(); closed = rd._erode(rd._dilate(b,1,9),1,9).astype(bool); st["close9x1"] = (time.perf_counter()-t)*1000
        t = time.perf_counter(); bx = rd.connected_components(closed); st["cc"] = (time.perf_counter()-t)*1000
        stages[name] = {k: round(v, 2) for k, v in st.items()}
    return out, stages


def psm_bench(n=10):
    """Same image, same langs, different --psm. FFI, system lib."""
    out = []
    for psm in ["3", "6", "11"]:
        import bench as B
        B.PSM = psm
        api = CApi()
        for name in ["dense", "crop"]:
            img = load(name)
            buf = np.asarray(img).tobytes()
            ts, txt = [], ""
            for i in range(n + 1):
                t0 = time.perf_counter()
                txt = api.ocr(buf, img.width, img.height)
                if i:
                    ts.append((time.perf_counter() - t0) * 1000)
            out.append({"psm": psm, "image": name, "n": len(ts),
                        "p50_ms": round(pct(ts, 50), 1), "chars": len(txt.strip())})
            print(out[-1], flush=True)
            (HERE / "text" / f"psm{psm}_{name}.txt").write_text(txt)
    import bench as B
    B.PSM = "11"
    return out


def tessdata_bench(n=10):
    out = []
    import bench as B
    B.PSM = "11"
    for label, path in [("fast", str(HERE / "tessdata")), ("standard", str(HERE / "tessdata_std"))]:
        api = CApi(tessdata=path)
        for name in ["dense", "crop"]:
            img = load(name)
            buf = np.asarray(img).tobytes()
            ts, txt = [], ""
            for i in range(n + 1):
                t0 = time.perf_counter()
                txt = api.ocr(buf, img.width, img.height)
                if i:
                    ts.append((time.perf_counter() - t0) * 1000)
            out.append({"tessdata": label, "image": name, "n": len(ts),
                        "p50_ms": round(pct(ts, 50), 1), "chars": len(txt.strip())})
            print(out[-1], flush=True)
            (HERE / "text" / f"tessdata-{label}_{name}.txt").write_text(txt)
    return out


if __name__ == "__main__":
    (HERE / "text").mkdir(exist_ok=True)
    res = {}
    res["region_detect"], res["region_stages"] = region_bench()
    print(json.dumps(res["region_detect"], indent=1), flush=True)
    print(json.dumps(res["region_stages"], indent=1), flush=True)
    res["psm"] = psm_bench()
    res["tessdata"] = tessdata_bench()
    (HERE / "raw_bench2.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print("wrote raw_bench2.json")
