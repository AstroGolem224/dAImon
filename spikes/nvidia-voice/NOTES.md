# Spike T−1.12 — NVIDIA-Sprachstack als zweiter Pfad

Datum: 2026-07-28 · Rolle: `investigator` · Maschine: CachyOS, RTX 5090 (sm_120, 32 607 MiB), Treiber 610.43.02, CUDA 13.3.1

**Ergebnis: offen — Werkzeug steht, Messung nicht gelaufen.**

Spezifikation samt Akzeptanz- und Abbruchkriterien: [SPEC.md](SPEC.md).

---

## Was hier entschieden wird

Nicht „NVIDIA statt sherpa", sondern ob ein **zweiter, GPU-gestützter Pfad**
neben dem bestehenden trägt. T-3.9 bleibt in jedem Fall Vorgabe und Rückfall —
CPU, 0 VRAM, p95 TTFA < 200 ms. Der zweite Pfad muss sich gegen diese Zahlen und
gegen den VRAM-Preis rechtfertigen, nicht gegen eine Marketingangabe.

Zwei Arme, getrennt entscheidbar:

| Arm | Modelle | Lizenz | Laufzeit | Risiko |
|---|---|---|---|---|
| **A — ASR** | `parakeet-tdt-0.6b-v3`, `canary-1b-v2` | CC-BY-4.0, offen | `onnx-asr` auf `onnxruntime-gpu==1.27.0` — der in T−1.2 belegte Pfad | gering |
| **B — TTS** | `magpie_tts_multilingual_357m` | NVIDIA Open Model License, **gated, non-commercial** | NeMo, auf sm_120 ungeprüft | Riva nennt Magpie Multilingual als auf Blackwell **nicht** unterstützt |

Arm A kann bestehen, während Arm B fällt. Dann bekommt dAImon NVIDIA-ASR und
behält sherpa-VITS.

---

## Kommandos

### Aufbau

```bash
export DAIMON_ROLE=investigator
cd spikes/nvidia-voice

./setup.sh models     # sherpa VITS thorsten + Whisper-small, beides Grundlinie
./setup.sh arm-a      # venv-a, prüft dabei auf eingeschleppte nvidia-*-Pakete
./setup.sh arm-b      # venv-b, braucht HF-Token (siehe unten)
```

`setup.sh arm-a` prüft zwei Auflagen aus T-3.8 gleich mit: keine
`nvidia-*`-pip-Pakete, und `cuobjdump` auf dem CUDA-Provider muss `sm_120a`
zeigen. Bricht ab, wenn die erste verletzt ist.

### Messung

Immer über `run.sh` — nur der Wrapper kann `vram_after_exit_mb` messen, weil der
Wert erst entsteht, nachdem der eigene Prozess weg ist.

```bash
# Testmaterial erzeugen und dabei die TTS-Grundlinie messen
./run.sh venv-a/bin/python make_testset.py

# Arm A
./run.sh venv-a/bin/python bench_asr.py --engine sherpa --model sherpa-onnx-whisper-small
./run.sh venv-a/bin/python bench_asr.py --engine onnx-asr --model nemo-parakeet-tdt-0.6b-v3
./run.sh venv-a/bin/python bench_asr.py --engine onnx-asr --model nemo-canary-1b-v2

# Arm B
./run.sh venv-b/bin/python bench_tts.py
```

Alles landet in `results.json`, eine Zeile je Arm und Modell.
`samples/magpie/` enthält die Sprachproben zum Anhören — das Klangurteil steht
in keiner Zahl.

---

## Was Matthias tun muss

**Nichts.** Beide Arme laufen ohne Vorbedingung.

Ursprünglich stand hier eine Anleitung für den Magpie-Gate. Am 2026-07-28
nachgeprüft: **der Gate ist abgeschaltet.**

```bash
curl -s https://huggingface.co/api/models/nvidia/magpie_tts_multilingual_357m \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["gated"])'   # -> False
```

Anonymer Download der `README.md` liefert HTTP 200, kein Token nötig. Die
`extra_gated_*`-Felder samt „I agree to use this model for non-commercial use
ONLY"-Checkbox stehen zwar noch im Frontmatter der Model Card, sind aber
wirkungslos — repo-seitig ist der Gate aus, und der Fließtext sagt
`This model is ready for commercial use.`

Falls der Gate zurückkommt, fällt `snapshot_download` in `setup.sh arm-b` mit 401
auf die Nase. Dann: `hf auth login` — **nicht** `huggingface-cli login`, der alte
Name ist ab `huggingface_hub` 1.x nur noch eine Fehlermeldung. Installiert via
`uv tool install huggingface_hub` nach `~/.local/bin/hf`; das `[cli]`-Extra gibt
es nicht mehr, die CLI steckt im Basispaket.

**Weights kommen trotzdem nie ins Repo**, nur der Downloader in `setup.sh`.

---

## Bekannte Grenzen der Messung

Vorab notiert, damit sie später nicht als Befund verkauft werden.

**Das Testaudio ist synthetisch.** `make_testset.py` erzeugt es mit sherpa-VITS.
Die WER-Zahlen sind zwischen den Engines vergleichbar, **absolut sind sie
wertlos** — TTS-Audio ist sauberer als jedes Mikrofon. Wer absolute Zahlen
braucht, spricht die 20 Sätze selbst ein und legt sie als `NNN.wav` + `NNN.txt`
nach `samples/testset/`.

**TTFA wird nicht geschätzt.** Weder NeMo noch die sherpa-Grundlinie
synthetisieren hier streamend, also gibt es kein echtes Time-to-First-Audio.
`ttfa_ms` bleibt `null`, `ttfa_reason` sagt warum. Eine aus der Gesamtlatenz
abgeleitete Zahl wäre geraten — und der Verifizierer muss genau das abweisen.

**Arm B misst womöglich nur einen Kaltlauf.** Die NeMo-Klassennamen wandern
zwischen Versionen. Findet `bench_tts.py` keine nutzbare Python-API, fällt es auf
das Beispielskript als Unterprozess zurück; dann ist `n=1`, das Modellladen
steckt in der Zahl, und das steht so im `verdict`. Nicht mit der Grundlinie
vergleichen.

**`vram_peak_mb` wird alle 100 ms abgetastet.** Kürzere Spitzen fallen durch.
Für Modellladen und Inferenz im Sekundenbereich reicht das; steht als
`ponytail:`-Vermerk in `common.py`.

---

## Lizenzlage

| Modell | Lizenz | Im Repo? |
|---|---|---|
| `parakeet-tdt-0.6b-v3`, `canary-1b-v2`, `canary-180m-flash` | CC-BY-4.0, kommerziell nutzbar | nein, Downloader |
| `magpie_tts_multilingual_357m` | NVIDIA Open Model License, **ungated**, laut Karte kommerziell nutzbar | nein, Downloader |
| sherpa-onnx | Apache-2.0 | nein, Downloader |
| Stimme `de_DE-thorsten-high` | CC0 | nein, Downloader |

Magpie, Stand v2607 vom 2026-07-22 — geprüft am 2026-07-28:

- `license: other`, `license_name: nvidia-open-model-license`
- Fließtext: „This model is ready for commercial use."
- `gated: false` über die HF-API; die `extra_gated_*`-Felder im Frontmatter sind
  Leichen aus der Zeit, als der Gate aktiv war
- **Zero-Shot-Voice-Cloning wurde entfernt** — laut Karte aus Sicherheitsgründen.
  Es bleiben 5 feste Sprecher (Aria, Jason, Leo, Sofia, John Van Stan) über 12
  Sprachen. Für dAImon heißt das: die Charakterstimme aus T-6.4 ist wählbar,
  aber nicht baubar
- Vor jedem Verlass auf diese Zeilen den Status neu abfragen, `setup.sh arm-b`
  tut das bei jedem Lauf

---

## Vorbefund aus dem Smoke-Test — **keine Messung**

Beim Anlegen des Werkzeugs lief jeder Pfad einmal durch, um die Skripte zu
prüfen. `n` ist 2 bis 3, der Verifizierer existiert noch nicht, `vram_peak_mb`
ist die ganze GPU inklusive Desktop. **Diese Zahlen sind kein Ergebnis** und
liegen deshalb in `smoke.json`, nicht in `results.json` — letzteres entsteht
erst nach T−1.12.v.

| Arm | Modell | p50 | p95 | RTF | WER | VRAM Peak | n |
|---|---|---|---|---|---|---|---|
| B | sherpa-vits thorsten (CPU) | 361 ms | 371 ms | 0,132 | — | 932 MiB | 3 |
| A | sherpa-whisper-small (CPU) | 684 ms | 684 ms | 0,189 | 0,178 | 914 MiB | 2 |
| A | **parakeet-tdt-0.6b-v3 (GPU)** | **27 ms** | **28 ms** | **0,006** | **0,103** | 5 893 MiB | 3 |

Wenn sich das über 20 Läufe hält, ist Arm A entschieden: 24-mal schneller und
klar genauer als die CPU-Grundlinie. Der Preis steht auch schon da — rund 5 GB
VRAM gegen 0.

Zwei Sachen, die der Smoke-Test schon geklärt hat und die in die Planung gehören:

- **`canary-180m-flash` ist in `onnx-asr` 0.12.0 nicht enthalten**, obwohl es auf
  HF liegt. Nur `nemo-parakeet-tdt-0.6b-v3` und `nemo-canary-1b-v2`. Der
  180m-Weg würde NeMo brauchen — also Torch, für ein Modell das nur schneller
  wäre. Fällt raus.
- **Die WER-Normalisierung war der erste echte Fehler.** Ohne Satzzeichen- und
  Groß-/Kleinschreibungsabgleich maß die Zahl vor allem, ob die Engine Kommas
  setzt: whisper-small kam auf 0,28 statt 0,178. Behoben in `common.normalize`.
  Ziffern bleiben Ziffern — „18" gegen „achtzehn" zählt weiter als Fehler, steht
  als `ponytail:`-Vermerk drin.

---

## Ergebnisse

_(nach der Messung ausfüllen — Zahlen aus `results.json`, Urteil je Arm gegen
die Abbruchkriterien in SPEC.md)_

| Arm | Modell | p50 | p95 | RTF | VRAM Peak | VRAM nach Exit | WER | Urteil |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

**Koexistenz:** `vram_asr + vram_tts + vram_vlm` gegen 32 607 MiB. VLM-Wert aus
T−1.10 nehmen, nicht schätzen.

**Folge für den Plan:** _(offen — je nach Ausgang T-3.8 und/oder T-6.4 ergänzen,
niemals ersetzen)_
