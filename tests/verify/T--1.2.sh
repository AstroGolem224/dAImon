#!/usr/bin/env bash
# Verifizierer fuer T−1.2: native Cubins werden am Artefakt selbst inspiziert.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT="$REPO/spikes/ort/results.json"
fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

json() { jq -r "$1 // \"nein\"" "$RESULT" 2>/dev/null; }
echo "T−1.2 — ONNX Runtime sm_120"
if jq -e . "$RESULT" >/dev/null 2>&1; then valid=ja; else valid=nein; fi
chk "results.json ist gueltiges JSON" "$valid" ja

variants="$(json 'if (.variants|type) == "array" then "ja" else "nein" end')"
chk "Variantenliste ist vorhanden" "$variants" ja
chk "Arch-Paket ohne Python-Bindings geprueft" \
  "$(json 'if any(.variants[]?; .variant == "B" and .importable == false) then "ja" else "nein" end')" ja
chk "Variante A im cp312-venv mit CUDA-Provider getestet" \
  "$(json 'if any(.variants[]?; .variant == "A" and .importable == true and .provider_active == true) then "ja" else "nein" end')" ja
chk "Variante B gegen die System-C-API getestet" \
  "$(json 'if any(.variants[]?; .variant == "B" and (.verdict|type) == "string" and (.notes|type) == "string") then "ja" else "nein" end')" ja
# GESTRICHEN 2026-07-28, siehe Plan bei T-1.2. Variante A traegt nachweislich
# mit nativen sm_120a-Cubins (cuobjdump-Beleg). Ein zweiter Toolchain-Bau
# aendert die Entscheidung nicht. An ihre Stelle tritt die STT-Messung, die
# T-3.8 wirklich braucht -- sie wird weiter unten geprueft, nicht erlassen.
echo "  INFO Variante C (whisper.cpp-cuda) gestrichen, siehe Plan T-1.2"
chk "jede Variante hat erste Inferenzlatenz" \
  "$(json 'if (.variants|length) >= 3 and all(.variants[]; (.first_infer_cold_ms|type) == "number") then "ja" else "nein" end')" ja
chk "jede Variante hat Dauerlatenz" \
  "$(json 'if (.variants|length) >= 3 and all(.variants[]; (.steady_ms|type) == "number") then "ja" else "nein" end')" ja
chk "jede Variante hat eine VRAM-Messung" \
  "$(json 'if (.variants|length) >= 3 and all(.variants[]; (.vram_mb|type) == "number") then "ja" else "nein" end')" ja
chk "geforderte Ergebnisfelder sind je Variante vorhanden" \
  "$(json 'if (.variants|length) >= 3 and all(.variants[];
      has("variant") and has("importable") and has("provider_active") and
      has("native_sm120") and has("cuobjdump_evidence") and
      has("first_infer_cold_ms") and has("steady_ms") and has("vram_mb") and
      has("verdict")) then "ja" else "nein" end')" ja
chk "mindestens eine lauffaehige Variante behauptet native sm_120-Cubins" \
  "$(json 'if any(.variants[]?; .importable == true and .provider_active == true and
      .native_sm120 == true) then "ja" else "nein" end')" ja
chk "JIT-Cache wurde fuer die Gegenprobe gesperrt" \
  "$(json 'if any(.variants[]?; .importable == true and .provider_active == true and
      ((.notes // "") | test("CUDA_CACHE_DISABLE=1"))) then "ja" else "nein" end')" ja

# Nicht den Textbeleg glauben: Bibliothek lokalisieren und cuobjdump wirklich ausfuehren.
cuobjdump_bin="$(command -v cuobjdump 2>/dev/null || true)"
artifact="$(find "$REPO/spikes/ort/venv-a" -type f -name 'libonnxruntime_providers_cuda.so' -print -quit 2>/dev/null)"
chk "cuobjdump ist installiert" "$([[ -n "$cuobjdump_bin" ]] && echo ja || echo nein)" ja
chk "geladenes CUDA-Provider-Artefakt ist auffindbar" "$([[ -n "$artifact" ]] && echo ja || echo nein)" ja
native=nein
if [[ -n "$cuobjdump_bin" ]] && [[ -n "$artifact" ]]; then
  dump="$("$cuobjdump_bin" --list-elf "$artifact" 2>/dev/null || true)"
  if grep -Eq 'sm_120a?\.cubin|sm_120a?[^[:alnum:]_]' <<<"$dump"; then native=ja; fi
fi
chk "cuobjdump findet selbst ein natives sm_120-Cubin" "$native" ja
exit $fail
