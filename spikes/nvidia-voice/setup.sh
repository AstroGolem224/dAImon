#!/usr/bin/env bash
# T-1.12 Aufbau. Getrennte venvs, weil Arm B den ganzen Torch-Stack zieht und
# Arm A ausdruecklich ohne nvidia-*-pip-Pakete laufen soll (Auflage aus T-3.8).
#
#   ./setup.sh models    Grundlinien-Modelle (sherpa VITS + Whisper)
#   ./setup.sh arm-a     venv-a: onnx-asr auf onnxruntime-gpu 1.27.0
#   ./setup.sh arm-b     venv-b: NeMo + Magpie  -- braucht den HF-Gate
set -euo pipefail
cd "$(dirname "$0")"
: "${DAIMON_ROLE:=investigator}"; export DAIMON_ROLE

fetch() {  # url, zielverzeichnisname
  [ -d "models/$2" ] && { echo "vorhanden: models/$2"; return; }
  mkdir -p models && curl -fL "$1" | tar -xj -C models
}

case "${1:-}" in
models)
  R=https://github.com/k2-fsa/sherpa-onnx/releases/download
  fetch "$R/tts-models/vits-piper-de_DE-thorsten-high.tar.bz2" vits-piper-de_DE-thorsten-high
  fetch "$R/asr-models/sherpa-onnx-whisper-small.tar.bz2" sherpa-onnx-whisper-small
  ;;

arm-a)
  uv venv --python 3.12 venv-a
  # onnxruntime-gpu nackt gepinnt, CUDA kommt vom System (spart ~2 GB, siehe T-1.2).
  uv pip install --python venv-a/bin/python \
    "onnxruntime-gpu==1.27.0" onnx-asr sherpa-onnx numpy huggingface_hub
  echo "--- keine nvidia-*-Pakete? (Auflage T-3.8) ---"
  uv pip list --python venv-a/bin/python | grep -i '^nvidia-' && {
    echo "FEHLER: nvidia-*-pip-Pakete eingeschleppt"; exit 1; } || echo "sauber"
  echo "--- sm_120-Cubins vorhanden? ---"
  cuobjdump --list-elf \
    venv-a/lib/python3.12/site-packages/onnxruntime/capi/libonnxruntime_providers_cuda.so \
    | grep -o 'sm_[0-9]*[a-z]*' | sort | uniq -c
  ;;

arm-b)
  # Stand 2026-07-28: Magpie ist NICHT mehr gated (API: gated=false, anonymer
  # Download liefert 200). Das extra_gated_*-Frontmatter in der Model Card ist
  # eine Leiche. Kein Token noetig -- aber wenn der Gate zurueckkommt, faellt
  # snapshot_download unten mit 401 auf die Nase, und dann hilft: hf auth login
  echo "Gate-Status live pruefen:"
  curl -fsS "https://huggingface.co/api/models/nvidia/magpie_tts_multilingual_357m" \
    | python3 -c 'import json,sys; print("  gated =", json.load(sys.stdin)["gated"])'
  uv venv --python 3.12 venv-b
  uv pip install --python venv-b/bin/python \
    "nemo_toolkit[tts] @ git+https://github.com/NVIDIA-NeMo/NeMo.git" kaldialign huggingface_hub
  [ -d NeMo ] || git clone --depth 1 https://github.com/NVIDIA-NeMo/NeMo.git NeMo
  # Zieht den Gate-Fehler jetzt, nicht mitten in der Messung.
  venv-b/bin/python - <<'PY'
from huggingface_hub import snapshot_download
for repo in ("nvidia/magpie_tts_multilingual_357m",
             "nvidia/nemo-nano-codec-22khz-1.89kbps-21.5fps"):
    print(repo, "->", snapshot_download(repo))
PY
  ;;

*) sed -n '2,9p' "$0"; exit 1 ;;
esac
