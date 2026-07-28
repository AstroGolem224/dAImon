#!/usr/bin/env python3
"""Startet den STT-Worker mit Watchdog und misst dessen GPU-Speicher."""

from __future__ import annotations

import argparse
import json
import os
import queue
import statistics
import subprocess
import threading
import time
from pathlib import Path


def communicate_with_watchdog(process: subprocess.Popen[str], timeout_s: float) -> tuple[str, str]:
    try:
        return process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.communicate()


def gpu_memory_mb() -> int:
    process = subprocess.Popen(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = communicate_with_watchdog(process, 5)
    if process.returncode != 0:
        raise RuntimeError(f"nvidia-smi fehlgeschlagen: {stderr.strip()}")
    values = [int(line.strip()) for line in stdout.splitlines() if line.strip()]
    if len(values) != 1:
        raise RuntimeError(f"Genau eine GPU erwartet, erhalten: {values}")
    return values[0]


def pump(stream: object, name: str, messages: queue.Queue[tuple[str, str]]) -> None:
    for line in stream:
        messages.put((name, line.rstrip("\n")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--english-0", type=Path, required=True)
    parser.add_argument("--english-1", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--german", type=Path, required=True)
    args = parser.parse_args()

    idle_samples = [gpu_memory_mb() for _ in range(3)]
    idle_mb = round(statistics.median(idle_samples))
    command = [
        str(args.python),
        str(args.worker),
        "--model-dir",
        str(args.model_dir),
        "--english-0",
        str(args.english_0),
        "--english-1",
        str(args.english_1),
        "--transcript",
        str(args.transcript),
        "--german",
        str(args.german),
        "--steady-n",
        "12",
    ]
    environment = os.environ.copy()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=environment,
    )
    assert process.stdout is not None and process.stderr is not None
    messages: queue.Queue[tuple[str, str]] = queue.Queue()
    threads = [
        threading.Thread(target=pump, args=(process.stdout, "stdout", messages), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, "stderr", messages), daemon=True),
    ]
    for thread in threads:
        thread.start()

    started = time.monotonic()
    bench_started = False
    vram_infer_samples: list[int] = []
    worker_result: dict[str, object] | None = None
    stderr_lines: list[str] = []
    stdout_lines: list[str] = []
    timed_out = False

    while process.poll() is None:
        if time.monotonic() - started > 300:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            break
        while True:
            try:
                source, line = messages.get_nowait()
            except queue.Empty:
                break
            if source == "stderr":
                stderr_lines.append(line)
            else:
                stdout_lines.append(line)
                if line == "BENCH_START":
                    bench_started = True
                elif line.startswith("RESULT "):
                    worker_result = json.loads(line.removeprefix("RESULT "))
        if bench_started:
            vram_infer_samples.append(gpu_memory_mb())
        else:
            time.sleep(0.02)

    for thread in threads:
        thread.join(timeout=1)
    while True:
        try:
            source, line = messages.get_nowait()
        except queue.Empty:
            break
        if source == "stderr":
            stderr_lines.append(line)
        else:
            stdout_lines.append(line)
            if line.startswith("RESULT "):
                worker_result = json.loads(line.removeprefix("RESULT "))

    # KWin allokiert und verwirft parallel Display-Speicher. Zwei Sekunden
    # Nachlauf und das Minimum zeigen, ob der deutlich groessere Worker-Kontext
    # verschwunden ist, ohne einen zufaelligen KWin-Ausschlag festzuschreiben.
    after_samples = []
    for _ in range(20):
        after_samples.append(gpu_memory_mb())
        time.sleep(0.1)
    after_mb = min(after_samples)

    output = {
        "watchdog_timeout_s": 300,
        "watchdog_timed_out": timed_out,
        "worker_returncode": process.returncode,
        "vram_idle_samples_mb": idle_samples,
        "vram_idle_mb": idle_mb,
        "vram_infer_samples_mb": vram_infer_samples,
        "vram_infer_mb": max(vram_infer_samples) if vram_infer_samples else None,
        "vram_after_exit_samples_mb": after_samples,
        "vram_after_exit_mb": after_mb,
        "vram_released": after_mb <= idle_mb,
        "worker_result": worker_result,
        "worker_stdout": stdout_lines,
        "worker_stderr": stderr_lines,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if timed_out or process.returncode != 0 or worker_result is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
