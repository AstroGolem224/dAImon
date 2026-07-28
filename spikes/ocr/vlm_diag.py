#!/usr/bin/env python3
"""T-1.10, Nebenmessung: warum liefert das VLM auf dem Vollbild nichts?

Der Messlauf gab fuer `dense` und `sparse` durchgaengig 0 Zeichen zurueck.
Bevor man daraus "das Modell kann kein Vollbild" macht, muss man wissen, ob
der Aufruf falsch war. Dieses Skript holt die vollen Antwortfelder von ollama,
nicht nur `response`.

Befund (qwen3-vl:8b, 5120x1440):
  num_predict=4096  -> done_reason=length, eval_count=4096, response=0 Zeichen
  num_predict=8192, think=false -> done_reason=length, thinking=25529 Zeichen,
                                   response=0 Zeichen
  num_predict=8192, think=true  -> done_reason=length, thinking=30317 Zeichen,
                                   response=0 Zeichen

Also: das Modell schreibt den gesamten Tokenvorrat in den Denkfaden und faengt
mit dem Transkript nie an. `think=false` wird von ollama fuer dieses Modell
nicht durchgesetzt. Auf dem Zuschnitt (600x300) gehen 3006 der Tokens ebenfalls
groesstenteils in den Denkfaden, aber es bleibt genug fuer 598 Zeichen Ausgabe.
Das ist kein Aufrufsfehler, sondern eine Eigenschaft des Modells bei dieser
Bildgroesse -- und es macht den Vollbildfall unbenutzbar, nicht nur langsam.
"""

import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import HERE, VLM_PROMPT, load  # noqa: E402

MODEL = "qwen3-vl:8b"


def probe(name, num_predict, think):
    img = load(name)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    payload = json.dumps(
        {
            "model": MODEL,
            "prompt": VLM_PROMPT,
            "images": [base64.b64encode(buf.getvalue()).decode()],
            "stream": False,
            "think": think,
            "options": {"temperature": 0, "num_predict": num_predict},
        }
    ).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=900) as fh:
        resp = json.load(fh)
    return {
        "image": name,
        "size": list(img.size),
        "num_predict": num_predict,
        "think": think,
        "ms": round((time.perf_counter() - t0) * 1000),
        "done_reason": resp.get("done_reason"),
        "eval_count": resp.get("eval_count"),
        "prompt_eval_count": resp.get("prompt_eval_count"),
        "response_chars": len(resp.get("response") or ""),
        "thinking_chars": len(resp.get("thinking") or ""),
    }


def main():
    rows = [
        probe("dense", 4096, True),
        probe("dense", 8192, False),
        probe("dense", 8192, True),
        probe("crop", 4096, True),
    ]
    (HERE / "raw_vlm_diag.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    for r in rows:
        print(json.dumps(r, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
