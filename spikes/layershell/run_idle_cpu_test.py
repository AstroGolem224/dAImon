#!/usr/bin/env python3
"""Test 4 + 5: Idle-CPU ueber 60 s und Puffertyp.

pidstat ist auf dieser Maschine nicht installiert (sysstat fehlt, kein sudo).
Ersatz, gleichwertig und ebenfalls ausserhalb des Prozesses gemessen:
  * /proc/<pid>/stat utime+stime, Delta ueber genau 60 s  (autoritative Zahl)
  * top -b -d 1 -n 61 -p <pid>                            (Gegenprobe)

Puffertyp: der Client benutzt ausschliesslich wl_shm. Nachweis von aussen
ueber /proc/<pid>/maps und /proc/<pid>/fd — kein libEGL, kein libvulkan,
kein libgbm, kein /dev/dri, dafuer memfd-Segmente.
"""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / "evidence"
EV.mkdir(exist_ok=True)
SPIKE = HERE / "target/release/spike"
HZ = os.sysconf("SC_CLK_TCK")
DURATION = 60


def cpu_ticks(pid):
    with open(f"/proc/{pid}/stat") as fh:
        f = fh.read().rsplit(") ", 1)[1].split()
    return int(f[11]) + int(f[12])  # utime, stime (Felder 14,15)


def main():
    res = {"clk_tck": HZ, "duration_s": DURATION, "pidstat_available": False,
           "pidstat_note": "sysstat/pidstat auf dieser Maschine nicht installiert; "
                           "extern gemessen ueber /proc/<pid>/stat und top -b"}
    ov = subprocess.Popen(
        [str(SPIKE), "map", "--color", "20A0C0", "--marker", "4700,1200,60,60"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pid = ov.pid
    res["pid"] = pid
    t0 = time.time()
    while time.time() - t0 < 15:
        line = ov.stdout.readline()
        if not line:
            break
        if line.startswith("READY"):
            break
    time.sleep(3)  # erste Frames abklingen lassen

    try:
        # --- Puffertyp / GPU-Freiheit
        maps = Path(f"/proc/{pid}/maps").read_text()
        libs = sorted({m for m in re.findall(r"/[^\s]+\.so[^\s]*", maps)})
        res["mapped_libs"] = [os.path.basename(x) for x in libs]
        res["gpu_libs"] = [x for x in res["mapped_libs"]
                           if re.search(r"EGL|GL\b|GLX|vulkan|gbm|drm|nvidia|GLdispatch",
                                        x, re.I)]
        res["memfd_mappings"] = len(re.findall(r"/memfd:", maps))
        fds = {}
        d = Path(f"/proc/{pid}/fd")
        for f in d.iterdir():
            try:
                fds[f.name] = os.readlink(f)
            except OSError:
                pass
        res["fds"] = fds
        res["dri_fds"] = [v for v in fds.values() if "/dev/dri" in v]
        res["memfd_fds"] = [v for v in fds.values() if "memfd" in v]
        res["buffer_type"] = "shm" if (not res["dri_fds"] and not res["gpu_libs"]
                                       and res["memfd_fds"]) else "unclear"

        # --- Idle-CPU
        top = subprocess.Popen(
            ["top", "-b", "-d", "1", "-n", str(DURATION + 1), "-p", str(pid)],
            stdout=subprocess.PIPE, text=True)
        t_start = time.time()
        c0 = cpu_ticks(pid)
        time.sleep(DURATION)
        c1 = cpu_ticks(pid)
        elapsed = time.time() - t_start
        cpu_pct = (c1 - c0) / HZ / elapsed * 100.0
        res["ticks_delta"] = c1 - c0
        res["elapsed_s"] = round(elapsed, 3)
        res["idle_cpu_pct"] = round(cpu_pct, 4)
        print(f"idle CPU: {cpu_pct:.4f} % einer Kerns ueber {elapsed:.1f} s "
              f"({c1 - c0} Ticks)")

        try:
            out = top.communicate(timeout=15)[0]
        except subprocess.TimeoutExpired:
            top.kill()
            out = top.communicate()[0]
        (EV / "top_idle.txt").write_text(out)
        vals = []
        for line in out.splitlines():
            m = re.match(rf"\s*{pid}\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.,]+)",
                         line)
            if m:
                vals.append(float(m.group(1).replace(",", ".")))
        res["top_samples"] = vals
        res["top_max_pct"] = max(vals) if vals else None
        res["top_mean_pct"] = round(sum(vals) / len(vals), 4) if vals else None

        res["still_alive"] = ov.poll() is None
    finally:
        ov.send_signal(signal.SIGTERM)
        try:
            ov.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ov.kill()

    (EV / "idle_cpu_test.json").write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k not in ("fds", "top_samples",
                                                                "mapped_libs")}, indent=2))


if __name__ == "__main__":
    main()
