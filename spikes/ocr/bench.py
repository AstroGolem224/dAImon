#!/usr/bin/env python3
"""Spike T-1.10 — OCR cost on this machine.

Variants: cli subprocess, ctypes FFI vs system libtesseract, tesserocr FFI,
persistent worker over a socketpair, and an ollama VLM.

All tesseract variants use: -l deu+eng, --psm 11, the same tessdata dir,
and the same pixel data (RGB, 3 bytes/px).
"""

import base64
import ctypes
import io
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
TESSDATA = str(HERE / "tessdata")
LANG = "deu+eng"
PSM = "11"
IMAGES = ["dense", "sparse", "crop"]


def load(name):
    return Image.open(HERE / "img" / f"{name}.png").convert("RGB")


def pct(xs, p):
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(variant, image, times, chars, notes=""):
    return {
        "variant": variant,
        "image": image,
        "n": len(times),
        "p50_ms": round(pct(times, 50), 1),
        "p95_ms": round(pct(times, 95), 1),
        "chars": chars,
        "notes": notes,
    }


# ---------------------------------------------------------------- CLI


def run_cli(img, tessdata=TESSDATA, include_write=True):
    """One subprocess per call with a temp PNG, exactly as screenpipe does."""
    env = dict(os.environ, TESSDATA_PREFIX=tessdata)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        t0 = time.perf_counter()
        img.save(path)
        t_write = time.perf_counter()
        out = subprocess.run(
            ["tesseract", path, "stdout", "--psm", PSM, "-l", LANG],
            capture_output=True,
            env=env,
        )
        t1 = time.perf_counter()
        return (
            (t1 - t0) * 1000,
            (t1 - t_write) * 1000,
            out.stdout.decode("utf-8", "replace"),
        )
    finally:
        os.unlink(path)


# ---------------------------------------------------------------- ctypes FFI


class CApi:
    """ctypes binding to the *system* libtesseract.so (5.5.3, same binary the
    CLI uses). One API instance reused across calls."""

    def __init__(self, lib="/usr/lib/libtesseract.so", tessdata=TESSDATA):
        self.lib = ctypes.CDLL(lib)
        L = self.lib
        L.TessBaseAPICreate.restype = ctypes.c_void_p
        L.TessBaseAPIInit3.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        L.TessBaseAPIInit3.restype = ctypes.c_int
        L.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.TessBaseAPISetImage.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        L.TessBaseAPIGetUTF8Text.argtypes = [ctypes.c_void_p]
        L.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
        L.TessDeleteText.argtypes = [ctypes.c_void_p]
        L.TessVersion.restype = ctypes.c_char_p

        self.h = ctypes.c_void_p(L.TessBaseAPICreate())
        if L.TessBaseAPIInit3(self.h, tessdata.encode(), LANG.encode()) != 0:
            raise RuntimeError("tesseract init failed")
        L.TessBaseAPISetPageSegMode(self.h, int(PSM))

    def version(self):
        return self.lib.TessVersion().decode()

    def ocr(self, buf, w, h):
        L = self.lib
        L.TessBaseAPISetImage(self.h, buf, w, h, 3, w * 3)
        p = L.TessBaseAPIGetUTF8Text(self.h)
        if not p:
            return ""
        s = ctypes.string_at(p).decode("utf-8", "replace")
        L.TessDeleteText(ctypes.c_void_p(p))
        return s


# ---------------------------------------------------------------- worker


WORKER_SRC = HERE / "worker.py"


class Worker:
    def __init__(self):
        self.sock, child = socket.socketpair()
        self.proc = subprocess.Popen(
            [sys.executable, str(WORKER_SRC), TESSDATA, LANG, PSM],
            pass_fds=(child.fileno(),),
            env=dict(os.environ, WORKER_FD=str(child.fileno())),
        )
        child.close()
        assert self._recv_line() == b"READY"

    def _recv_line(self):
        buf = b""
        while not buf.endswith(b"\n"):
            c = self.sock.recv(1)
            if not c:
                raise RuntimeError("worker died")
            buf += c
        return buf[:-1]

    def ocr(self, buf, w, h):
        self.sock.sendall(f"{w} {h} {len(buf)}\n".encode())
        self.sock.sendall(buf)
        n = int(self._recv_line())
        chunks = []
        got = 0
        while got < n:
            c = self.sock.recv(min(65536, n - got))
            if not c:
                raise RuntimeError("worker died")
            chunks.append(c)
            got += len(c)
        return b"".join(chunks).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.sendall(b"-1 -1 -1\n")
        except OSError:
            pass
        self.proc.wait(timeout=5)


# ---------------------------------------------------------------- VLM

VLM_PROMPT = (
    "Transcribe every piece of text visible in this screenshot, verbatim, "
    "in reading order. Output only the transcribed text, no commentary."
)


def run_vlm(img, model="gemma4:26b", timeout=600):
    import urllib.request

    b = io.BytesIO()
    img.save(b, format="PNG")
    payload = json.dumps(
        {
            "model": model,
            "prompt": VLM_PROMPT,
            "images": [base64.b64encode(b.getvalue()).decode()],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 4096},
        }
    ).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.load(r)
    return (time.perf_counter() - t0) * 1000, resp.get("response", "")


# ---------------------------------------------------------------- main


def bench(which, n=20, n_vlm=5):
    results = []
    extra = {}
    imgs = {k: load(k) for k in IMAGES}
    raw = {k: (np.asarray(v).tobytes(), v.width, v.height) for k, v in imgs.items()}

    if "cli" in which:
        for name in IMAGES:
            tot, sub, txt = [], [], ""
            for i in range(n + 1):
                a, b, t = run_cli(imgs[name])
                if i:  # discard warm-up
                    tot.append(a)
                    sub.append(b)
                txt = t
            r = summarize("cli", name, tot, len(txt.strip()),
                          "subprocess + temp PNG per call (screenpipe style); "
                          f"subprocess-only p50 {pct(sub,50):.0f} ms, "
                          f"PNG encode+write p50 {pct(tot,50)-pct(sub,50):.0f} ms")
            r["_text"] = txt
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "_text"}), flush=True)

    if "ffi" in which:
        api = CApi()
        extra["ffi_lib_version"] = api.version()
        for name in IMAGES:
            buf, w, h = raw[name]
            ts, txt = [], ""
            for i in range(n + 1):
                t0 = time.perf_counter()
                txt = api.ocr(buf, w, h)
                if i:
                    ts.append((time.perf_counter() - t0) * 1000)
            r = summarize("ffi", name, ts, len(txt.strip()),
                          f"ctypes -> system libtesseract ({api.version()}), "
                          "one API instance reused, no disk, RGB buffer in RAM")
            r["_text"] = txt
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "_text"}), flush=True)

    if "tesserocr" in which:
        import tesserocr
        with tesserocr.PyTessBaseAPI(path=TESSDATA, lang=LANG,
                                     psm=tesserocr.PSM.SPARSE_TEXT) as api:
            extra["tesserocr_lib_version"] = tesserocr.tesseract_version()
            for name in IMAGES:
                ts, txt = [], ""
                for i in range(n + 1):
                    t0 = time.perf_counter()
                    api.SetImage(imgs[name])
                    txt = api.GetUTF8Text()
                    if i:
                        ts.append((time.perf_counter() - t0) * 1000)
                r = summarize("tesserocr", name, ts, len(txt.strip()),
                              f"tesserocr wheel, bundles {tesserocr.tesseract_version().splitlines()[0]}")
                r["_text"] = txt
                results.append(r)
                print(json.dumps({k: v for k, v in r.items() if k != "_text"}), flush=True)

    if "worker" in which:
        w_ = Worker()
        for name in IMAGES:
            buf, iw, ih = raw[name]
            ts, txt = [], ""
            for i in range(n + 1):
                t0 = time.perf_counter()
                txt = w_.ocr(buf, iw, ih)
                if i:
                    ts.append((time.perf_counter() - t0) * 1000)
            r = summarize("worker", name, ts, len(txt.strip()),
                          "separate process holding the API, raw RGB over a "
                          f"unix socketpair ({len(buf)//1024} KiB per call)")
            r["_text"] = txt
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "_text"}), flush=True)
        w_.close()

    if "vlm" in which:
        for name in IMAGES:
            ts, txt = [], ""
            for i in range(n_vlm + 1):
                try:
                    dt, txt = run_vlm(imgs[name])
                except Exception as e:  # noqa
                    print(f"VLM {name} failed: {e}", flush=True)
                    break
                if i:
                    ts.append(dt)
            if ts:
                r = summarize("vlm", name, ts, len(txt.strip()),
                              "ollama gemma4:26b (already pulled, 17 GB), "
                              f"n={len(ts)} (fewer than 20: each call is seconds)")
                r["_text"] = txt
                results.append(r)
                print(json.dumps({k: v for k, v in r.items() if k != "_text"}), flush=True)

    return results, extra


if __name__ == "__main__":
    which = sys.argv[1].split(",") if len(sys.argv) > 1 else ["cli", "ffi", "tesserocr", "worker"]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    res, extra = bench(which, n=n)
    out = HERE / f"raw_{'_'.join(which)}.json"
    out.write_text(json.dumps({"results": res, "extra": extra}, indent=1, ensure_ascii=False))
    print("wrote", out)
