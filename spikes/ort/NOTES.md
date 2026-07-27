# Spike T−1.2 — ONNX Runtime mit nativen sm_120-Kerneln aus Python 3.12

Datum: 2026-07-27 · Rolle: `investigator` · Maschine: CachyOS, RTX 5090 (sm_120), Treiber 610.43.02, CUDA 13.3.1, cuDNN 9.24.0

**Ergebnis in einem Satz:** Das pip-Wheel `onnxruntime-gpu==1.27.0` bringt native `sm_120a`-Cubins mit, laeuft im cp312-venv gegen das System-CUDA ohne Zusatzarbeit — Variante A ist die Empfehlung. Das System-Paket ist als Bibliothek zwar sm_120-tauglich, aber aus Python nicht erreichbar.

## Kommandos

### Setup

```bash
export DAIMON_ROLE=investigator
uv venv --python 3.12 spikes/ort/venv-a
uv pip install --python spikes/ort/venv-a/bin/python onnxruntime-gpu numpy onnx onnx-asr
```

Aufgeloest: `onnxruntime-gpu==1.27.0`, `numpy==2.5.1`, `onnx==1.22.0`, `onnx-asr==0.12.0`.

### Cubin-Inventar (der eigentliche Beweis)

```bash
SP=spikes/ort/venv-a/lib/python3.12/site-packages/onnxruntime/capi
cuobjdump --list-elf $SP/libonnxruntime_providers_cuda.so | grep -o 'sm_[0-9]*[a-z]*' | sort | uniq -c
cuobjdump --list-ptx $SP/libonnxruntime_providers_cuda.so | grep -o 'sm_[0-9]*[a-z]*' | sort | uniq -c
cuobjdump --list-elf /usr/lib/libonnxruntime_providers_cuda.so | grep -o 'sm_[0-9]*[a-z]*' | sort | uniq -c
cuobjdump --list-ptx /usr/lib/libonnxruntime_providers_cuda.so | grep -o 'sm_[0-9]*[a-z]*' | sort | uniq -c
```

| | pip-Wheel 1.27.0 | System 1.27.1-2 |
|---|---|---|
| Cubin-Archs | sm_75, 80, 86, 89, 90a, 100a, **120a** | sm_75, 80, 86, 87, 88, 89, 90a, 100a, 103, 110a, **120a**, 121 |
| sm_120a-Cubins | 203 | 234 |
| PTX-Ziel | **sm_120** (234) | **sm_121** (266) |

Kernel-genaue Abdeckung (Namen statt Zaehlern):

```bash
cuobjdump -res-usage $SP/libonnxruntime_providers_cuda.so \
  | grep -E '^arch = |^ Function ' > wheel_res.txt
# dann pro "arch = X"-Block die Function-Namen sammeln und Mengen differenzieren
```

Wheel: 9037 unterschiedliche Kernel-Symbole insgesamt, davon 7740 mit `sm_120a`-Cubin.
Die 1297 ohne `sm_120a`:

* 1008 × cutlass `MoeFCGemm` / `fused_moe` / `TmaWarpSpecializedGroupedGemm`
* 287 × `onnxruntime::llm::kernels` (`fpA_intB_gemv`, `weight_only`, …)
* 2 × `onnxruntime::contrib::cuda::kDequantizeBlockwise`

Alles MoE-/int4-Weight-only-LLM-Pfade. **Sämtliche** `onnxruntime::cuda` (5131) und
`onnxruntime::contrib::cuda` (1337) Kernel — also der Conformer-/TDT-relevante Teil —
haben ein natives `sm_120a`-Cubin.

### Laufzeitmessung

```bash
CUDA_CACHE_DISABLE=1 CUDA_CACHE_PATH=~/.nv/ComputeCache.spiketest \
  ./spikes/ort/venv-a/bin/python spikes/ort/bench.py --tag A-nocache
CUDA_FORCE_PTX_JIT=1 CUDA_CACHE_DISABLE=1 \
  ./spikes/ort/venv-a/bin/python spikes/ort/bench.py --tag A-forcejit
./spikes/ort/venv-a/bin/python spikes/ort/bench.py --tag A-arena --arena kSameAsRequested
```

Modell: 8× (MatMul 512×512 + ReLU), fp32, Batch 1. 20 Laeufe, Median.

| Lauf | session_create | cold 1st infer | steady (median) | VRAM |
|---|---|---|---|---|
| warm Cache | 496.8 ms | 50.04 ms | 0.0552 ms | 648 MB |
| `CUDA_CACHE_DISABLE=1` | 318.9 ms | 41.83 ms | 0.0541 ms | 648 MB |
| `CUDA_CACHE_DISABLE=1` (Wdh.) | 189.2 ms | 46.09 ms | 0.0572 ms | — |
| `CUDA_FORCE_PTX_JIT=1` | 277.5 ms | 40.54 ms | 0.0552 ms | — |
| `arena=kSameAsRequested` | — | 37.87 ms | 0.0543 ms | 646 MB |

### Arena-Kontrolle

```bash
./spikes/ort/venv-a/bin/python -c "
import onnxruntime as ort
for v in ['kSameAsRequested','kNextPowerOfTwo','bogusValue']:
    ort.InferenceSession('spikes/ort/bench_model.onnx',
        providers=[('CUDAExecutionProvider',{'arena_extend_strategy':v})])"
```

`bogusValue` → `Failed to map enum name to value: bogusValue`, Fallback auf CPU.
Die Option wird also tatsaechlich geparst. `onnx_asr.load_model()` reicht
`sess_options`, `providers` (mit Options-Tupeln) und `provider_options` durch.

### Variante B

```bash
cp -r spikes/ort/venv-a spikes/ort/venv-b
SPB=spikes/ort/venv-b/lib/python3.12/site-packages/onnxruntime/capi
mv $SPB/libonnxruntime_providers_cuda.so $SPB/libonnxruntime_providers_cuda.so.wheelbak
cp /usr/lib/libonnxruntime_providers_cuda.so $SPB/
./spikes/ort/venv-b/bin/python spikes/ort/bench.py --tag B-sysprovider   # rc=139 (SIGSEGV)

readelf -d $SP/onnxruntime_pybind11_state.cpython-312-*.so | grep NEEDED
nm -D --defined-only /usr/lib/libonnxruntime.so.1 | grep OrtGetApiBase
```

### Variante C

```bash
curl -s "https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=whisper.cpp-cuda"
curl -s "https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=ggml-cuda-git"
```

Nichts installiert, kein sudo, kein Build.

## Was ueberrascht hat

1. **Das pip-Wheel ist auf dieser Maschine besser aufgestellt als das Arch-Paket.**
   Beide haben `sm_120a`-Cubins. Aber der PTX-Fallback der System-Lib ist auf
   `compute_121` uebersetzt — und `compute_121`-PTX laedt auf einer sm_120-GPU
   *nicht*. Fuer die 36 Kernel-Gruppen ohne `sm_120a`-Cubin hat die System-Lib
   auf der 5090 also gar keinen Pfad, das Wheel dagegen `sm_120`-PTX. Die
   Ausgangsannahme des Spikes ("Arch hat sm_120, das Wheel wahrscheinlich nicht")
   war genau falsch herum.

2. **`onnxruntime_pybind11_state.so` hat kein `NEEDED` auf `libonnxruntime.so`.**
   Der ORT-Kern ist statisch ins pybind-Modul gelinkt. Damit ist die ganze Idee
   "Python-Layer vom Wheel + Kern vom System per LD_LIBRARY_PATH/LD_PRELOAD"
   gegenstandslos — es gibt nichts zum Umlenken. Nur der *Provider* wird per
   dlopen nachgeladen, und dessen Schnittstelle ist die interne Bridge-ABI:
   1.27.0-Kern + 1.27.1-Provider = SIGSEGV bei `InferenceSession(...)`,
   ohne jede Fehlermeldung.

3. **Latenz-Ratio taugt hier nicht als JIT-Nachweis — in beide Richtungen.**
   Cold/steady liegt bei ~770–900×, auch mit deaktiviertem JIT-Cache. Das kommt
   aus cuBLAS-/cuDNN-Handle-Init und Arena-Allokation, nicht aus JIT. Umgekehrt:
   `CUDA_FORCE_PTX_JIT=1` *erzwingt* JIT und aendert die Zahlen praktisch nicht
   (40.5 vs 41.8 ms cold), weil CUDA seit 12.x lazy module loading macht und nur
   die tatsaechlich benutzten Module uebersetzt. Beide Richtungen der Messung
   sind blind. Nur das Cubin-Inventar beantwortet die Frage.

4. **`sm_120a`, nicht `sm_120`.** Die Cubins tragen das arch-spezifische
   `a`-Suffix (family-specific features). Das ist fuer uns unproblematisch —
   die 5090 *ist* sm_120, also matcht es exakt — aber `sm_120a`-Cubins laufen
   auch nicht auf sm_121-Hardware. Wer das Setup je auf eine RTX PRO 6000
   portiert, faellt dort auf PTX-JIT zurueck.

5. **`onnx-asr` zieht in der Basis-Installation gar kein onnxruntime.**
   Nur die Extras `[cpu]`/`[gpu]` tun das. Wir installieren nackt und pinnen
   `onnxruntime-gpu` selbst. Die `[gpu]`-Constraint waere ohnehin erfuellt
   (`>=1.18.1,!=1.24.1,!=1.25.*,!=1.26.0`).

6. **Keine `nvidia-*`-pip-Pakete noetig.** Das Wheel braucht `libcublas.so.13`,
   `libcudart.so.13`, `libnvrtc.so.13`, `libcufft.so.12`, `libcurand.so.10`,
   `libcudnn.so.9` — alle bereits vom System (CUDA 13.3.1 + cuDNN 9.24)
   aufgeloest, `ldd` meldet nichts als `not found`. Spart ~2 GB.

## Offene Punkte

* Gemessen wurde ein Trivialmodell, **nicht** Parakeet. Die Kernel-Abdeckung
  ist statisch belegt, aber ein realer Parakeet-TDT-Lauf (Attention-Kernel,
  Conv1d-Subsampling, RNNT/TDT-Decoder-Schleife) steht aus.
* VRAM-Zahl (648 MB) ist das Trivialmodell inkl. CUDA-Kontext, sagt nichts
  ueber Parakeets Bedarf.
* Ob die Arena bei Idle wirklich freigibt, ist damit noch nicht gezeigt —
  nur, dass die Option gesetzt werden *kann*.

## Artefakte

* `spikes/ort/bench.py` — Benchmark-Skript
* `spikes/ort/bench_model.onnx` — generiertes Testmodell
* `spikes/ort/results.json` — strukturiertes Ergebnis
* `spikes/ort/venv-a/` — funktionierendes cp312-venv (Variante A)
* `spikes/ort/venv-b/` — Klon mit eingesetztem System-Provider, segfaultet
  (Original unter `.../capi/libonnxruntime_providers_cuda.so.wheelbak`).
  Beide venvs sind ~1.5 GB und gehoeren nicht ins Git.
