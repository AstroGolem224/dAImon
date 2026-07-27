#!/usr/bin/env python3
"""Trivial CUDA inference benchmark for ORT.

Baut ein kleines MatMul-Modell (ohne onnx-Paket, roher Protobuf-Bytes-Aufbau
waere fragil -> wir nutzen onnx wenn vorhanden, sonst Fallback).
Misst cold first inference vs steady state und VRAM.

Aufruf:  python bench.py [--tag NAME]
Env:     CUDA_CACHE_DISABLE=1 setzen fuer JIT-Cache-Test.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import onnxruntime as ort


def build_model(path):
    import onnx
    from onnx import helper, TensorProto

    n = 512
    w = np.random.randn(n, n).astype(np.float32)
    nodes = []
    inits = [helper.make_tensor("W", TensorProto.FLOAT, [n, n], w.flatten().tolist())]
    prev = "X"
    # mehrere Layer, damit echte Kernel-Arbeit anfaellt
    for i in range(8):
        nodes.append(helper.make_node("MatMul", [prev, "W"], [f"h{i}"]))
        nodes.append(helper.make_node("Relu", [f"h{i}"], [f"r{i}"]))
        prev = f"r{i}"
    nodes.append(helper.make_node("Identity", [prev], ["Y"]))
    graph = helper.make_graph(
        nodes,
        "bench",
        [helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, n])],
        [helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, n])],
        inits,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    onnx.save(model, path)
    return n


def vram_mb():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout
        me = str(os.getpid())
        for line in out.strip().splitlines():
            pid, mem = [x.strip() for x in line.split(",")]
            if pid == me:
                return float(mem)
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="A")
    ap.add_argument("--model", default=os.path.join(os.path.dirname(__file__), "bench_model.onnx"))
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--arena", default=None, help="arena_extend_strategy")
    args = ap.parse_args()

    res = {
        "tag": args.tag,
        "ort_version": ort.__version__,
        "python": sys.version.split()[0],
        "cuda_cache_disable": os.environ.get("CUDA_CACHE_DISABLE"),
        "available_providers": ort.get_available_providers(),
    }

    if not os.path.exists(args.model):
        build_model(args.model)

    po = {}
    if args.arena:
        po["arena_extend_strategy"] = args.arena
    providers = [("CUDAExecutionProvider", po), "CPUExecutionProvider"]

    t0 = time.perf_counter()
    try:
        sess = ort.InferenceSession(args.model, providers=providers)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(res, indent=2))
        return 1
    res["session_create_ms"] = (time.perf_counter() - t0) * 1000
    res["session_providers"] = sess.get_providers()
    res["provider_active"] = "CUDAExecutionProvider" in sess.get_providers()

    x = np.random.randn(1, 512).astype(np.float32)

    t0 = time.perf_counter()
    sess.run(None, {"X": x})
    res["first_infer_cold_ms"] = (time.perf_counter() - t0) * 1000

    times = []
    for _ in range(args.runs):
        t0 = time.perf_counter()
        sess.run(None, {"X": x})
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    res["steady_ms"] = times[len(times) // 2]
    res["steady_min_ms"] = times[0]
    res["steady_max_ms"] = times[-1]
    res["cold_to_steady_ratio"] = res["first_infer_cold_ms"] / res["steady_ms"]
    res["vram_mb"] = vram_mb()

    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
