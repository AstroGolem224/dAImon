#!/usr/bin/env bash
# Faehrt einen Messlauf und traegt vram_after_exit_mb nach.
#
# Das kann kein Benchmark selbst messen -- der Wert entsteht erst, nachdem der
# eigene Prozess weg ist. Fuenf Sekunden Wartezeit, wie in T-3.7.
#
#   ./run.sh venv-a/bin/python bench_asr.py --engine onnx-asr --model nemo-parakeet-tdt-0.6b-v3
set -euo pipefail
cd "$(dirname "$0")"
: "${DAIMON_ROLE:=investigator}"; export DAIMON_ROLE

before=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
echo "VRAM vor dem Lauf: ${before} MiB"
"$@"
sleep 5
after=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
echo "VRAM 5 s nach dem Lauf: ${after} MiB (Ausgang ${before} MiB)"

python3 - "$after" <<'PY'
import json, pathlib, sys
p = pathlib.Path("results.json")
rows = json.loads(p.read_text())
rows[-1]["vram_after_exit_mb"] = int(sys.argv[1])   # letzte Zeile ist der eben gelaufene Arm
p.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
print("nachgetragen:", rows[-1]["arm"], rows[-1]["model"])
PY
