#!/usr/bin/env python3
"""T-1.6 — was kostet ein Hook, und was kostet ein kaputter Hook?

    python3 bench.py

ABWEICHUNG VOM PLAN, ausdruecklich
----------------------------------
Der Plan verlangt "≥30 gepaarte Laeufe desselben trivialen Prompts, Bridge an
vs. aus" -- also Ende-zu-Ende ueber echte Claude-Code-Aufrufe. Das ist auf
dieser Maschine derzeit nicht moeglich: das claude-CLI meldet
"OAuth session expired and could not be refreshed". Gemessen wird deshalb der
Hook-Pfad selbst, also genau der Teil, den die Bridge zur Antwortzeit
beitraegt. Was dabei NICHT erfasst wird, ist der Aufwand, den Claude Code um
den Hook herum betreibt. Da dieser Aufwand in allen Bedingungen derselbe ist,
verschiebt er die Differenzen nicht -- er verwaessert sie nur, macht die
Messung also konservativ und nicht schoenfaerberisch.

Vier Bedingungen, weil die interessante Frage nicht "was kostet es" ist,
sondern "was passiert, wenn es kaputt ist":

  aus     kein Hook. Der Bezugswert ist null, nicht messbar-klein.
  gesund  Bridge antwortet sofort.
  tot     nichts lauscht auf dem Port. Das ist der Alltagsfall, denn der
          Daemon laeuft nicht immer.
  langsam Bridge antwortet erst nach 2 s, laenger als das curl-Zeitlimit.
          Der boeseste Fall: die Gegenstelle lebt, haengt aber.

Der Befehl ist woertlich der aus config/claude-hooks.json, inklusive
`-m 1` und `|| true`. Beide sind der Grund, warum ein toter Daemon nichts
kostet -- die Messung prueft, ob das stimmt.
"""

import json
import statistics
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
N = 40
SLOW_DELAY = 2.0
PORT_HEALTHY = 8791
PORT_SLOW = 8792
PORT_DEAD = 8799  # hier lauscht absichtlich nichts

PAYLOAD = json.dumps({
    "hook_event_name": "PreToolUse", "session_id": "bench",
    "tool_name": "Bash", "cwd": "/tmp",
}).encode()


def hook_cmd(port):
    return (f"curl -s -m 1 -X POST -H 'Content-Type: application/json' "
            f"--data-binary @- http://127.0.0.1:{port}/hook >/dev/null 2>&1 || true")


class Handler(BaseHTTPRequestHandler):
    delay = 0.0

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.delay:
            time.sleep(self.delay)
        # In der Bedingung "langsam" hat curl nach 1 s aufgelegt, waehrend wir
        # noch 2 s schlafen. Der Schreibfehler ist dann kein Fehler, sondern
        # der Beweis, dass das Zeitlimit gegriffen hat.
        try:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def serve(port, delay):
    handler = type("H", (Handler,), {"delay": delay})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def measure(label, cmd):
    times = []
    for _ in range(N):
        t0 = time.perf_counter()
        if cmd:
            subprocess.run(cmd, shell=True, input=PAYLOAD, capture_output=True)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p50 = statistics.median(times)
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
    print(f"  {label:22s} p50 {p50:8.2f} ms   p95 {p95:8.2f} ms")
    return p50, p95


def main():
    healthy = serve(PORT_HEALTHY, 0.0)
    slow = serve(PORT_SLOW, SLOW_DELAY)
    time.sleep(0.3)
    try:
        print(f"n={N} je Bedingung\n")
        off50, off95 = measure("aus (kein Hook)", None)
        on50, on95 = measure("gesund", hook_cmd(PORT_HEALTHY))
        dead50, dead95 = measure("tot (nichts lauscht)", hook_cmd(PORT_DEAD))
        slow50, slow95 = measure(f"langsam ({SLOW_DELAY:.0f}s)", hook_cmd(PORT_SLOW))
    finally:
        healthy.shutdown()
        slow.shutdown()

    # Die Akzeptanz des Plans vergleicht Ende-zu-Ende-Zeiten. Ohne die
    # Claude-Code-Grundlast sind die Verhaeltnisse hier nicht direkt
    # vergleichbar -- deshalb werden auch die absoluten Aufschlaege berichtet,
    # denn die sind es, was tatsaechlich hinzukommt.
    result = {
        "spike": "T-1.6",
        "frage": "Bremst ein toter oder langsamer Daemon Claude Code aus?",
        "n": N,
        "p50_off_ms": round(off50, 2), "p95_off_ms": round(off95, 2),
        "p50_on_ms": round(on50, 2), "p95_on_ms": round(on95, 2),
        "p50_dead_ms": round(dead50, 2), "p95_dead_ms": round(dead95, 2),
        "p50_slow_ms": round(slow50, 2), "p95_slow_ms": round(slow95, 2),
        "aufschlag_gesund_ms": round(on50 - off50, 2),
        "aufschlag_tot_ms": round(dead50 - off50, 2),
        "aufschlag_langsam_ms": round(slow50 - off50, 2),
        "befehl": hook_cmd("<port>"),
        "abweichung_vom_plan": (
            "Der Plan verlangt gepaarte Claude-Code-Laeufe. Das claude-CLI meldet "
            "'OAuth session expired and could not be refreshed', Ende-zu-Ende ist "
            "damit derzeit nicht messbar. Gemessen ist der Hook-Pfad allein, also "
            "der Beitrag der Bridge. Die Claude-Code-Grundlast fehlt in allen vier "
            "Bedingungen gleichermassen; sie verschiebt die Differenzen nicht."
        ),
    }
    (HERE / "results.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    print(f"\n  Aufschlag gesund  {result['aufschlag_gesund_ms']:.2f} ms")
    print(f"  Aufschlag tot     {result['aufschlag_tot_ms']:.2f} ms")
    print(f"  Aufschlag langsam {result['aufschlag_langsam_ms']:.2f} ms  "
          f"(curl-Zeitlimit greift bei 1000 ms)")
    return result


if __name__ == "__main__":
    main()
