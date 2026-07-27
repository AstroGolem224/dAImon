#!/usr/bin/env python3
"""T-1.9 Messlauf: schaltet N-mal zwischen Fenstern um, rechnet Trefferquote
und Latenz aus den probe-Ereignissen. Nutzt den WindowsRunner, damit kein
loadScript noetig ist."""
import json, re, sys, time
from pathlib import Path
import dbus

HERE = Path(__file__).parent
EV = HERE / "events.jsonl"

bus = dbus.SessionBus()
runner = dbus.Interface(
    bus.get_object("org.kde.KWin", "/WindowsRunner"), "org.kde.krunner1")

def windows():
    out = []
    for m in runner.Match(""):
        mid = str(m[0])
        if re.search(r"\{[0-9a-f-]{36}\}", mid) and mid not in [o[0] for o in out]:
            out.append((mid, str(m[1])))
    return out

def read_events():
    if not EV.exists(): return []
    return [json.loads(l) for l in EV.read_text().splitlines() if l.strip()]

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    wins = windows()
    if len(wins) < 2:
        print(f"Zu wenige Fenster: {len(wins)}. Mindestens 2 noetig."); sys.exit(2)
    print(f"{len(wins)} Fenster:")
    for mid, name in wins: print(f"  {mid}  {name[:70]}")

    before = len(read_events())
    stamps = []
    for i in range(n):
        mid, _ = wins[i % len(wins)]
        t0 = time.monotonic()
        runner.Run(mid, "")
        stamps.append((t0, mid))
        time.sleep(0.35)
    time.sleep(1.2)

    evs = read_events()[before:]
    acts = [e for e in evs if e["kind"] == "activated"]
    caps = [e for e in evs if e["kind"] == "caption"]

    lat, missed = [], 0
    for t0, mid in stamps:
        m = re.search(r"\{[0-9a-f-]{36}\}", mid)
        key = m.group(0) if m else mid
        cand = [e for e in acts if e["t"] >= t0 and e["uuid"] == key]
        if cand: lat.append((min(c["t"] for c in cand) - t0) * 1000)
        else: missed += 1
    lat.sort()
    res = {
        "switches_requested": n,
        "switches_reported": len(lat),
        "missed": missed,
        "p50_latency_ms": round(lat[len(lat)//2], 1) if lat else None,
        "p95_latency_ms": round(lat[min(int(len(lat)*0.95), len(lat)-1)], 1) if lat else None,
        "caption_events_same_window": len(caps),
        "activated_events_total": len(acts),
        "windows_tested": len(wins),
    }
    print(json.dumps(res, indent=2))
    (HERE / "measure.json").write_text(json.dumps(res, indent=2))
