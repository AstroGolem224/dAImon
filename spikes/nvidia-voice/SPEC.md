# T−1.12 — NVIDIA-Sprachstack als zweiter Pfad messen ∥

_Spezifikation im Format des Implementierungsplans. Gehört nach der Messung in
`docs/IMPLEMENTATION-PLAN.md` und die Quelle unter `UMBRA-Notes/DDs/dAImon/`._

## Warum es diesen Spike gibt

Der Plan legt den Audio-Stack in T-3.8 (STT) und T-3.9 (TTS) auf **sherpa-onnx**
fest — Apache-2.0, CPU, ein Framework für KWS, VAD, STT und VITS-Synthese. Das
bleibt so. Dieser Spike prüft, ob daneben ein **zweiter, GPU-gestützter Pfad**
trägt: NVIDIA Nemotron/Parakeet für ASR, Magpie-TTS für die Sprachausgabe.

Die Behauptung, die geprüft wird, stammt aus dem NVIDIA-Voice-Agent-Blueprint:
**rund 500 ms Voice-to-Voice auf einer RTX 5090.** Auf dieser Maschine hat das
niemand gemessen. Der Plan hat in T-3.15 ein Gate bei
`p95_wake_to_audio_ms < 1500` und in T-3.9 eines bei `TTFA p95 < 200 ms` —
gegen diese beiden Zahlen wird gemessen, nicht gegen die Marketingzahl.

Der zweite Pfad rechtfertigt sich **nicht** über Latenz allein. sherpa-VITS auf
CPU ist bereits schnell und kostet 0 VRAM. Die Frage ist, ob NVIDIA genug
Qualität und Mehrsprachigkeit bringt, um den VRAM-Preis und die zweite
Abhängigkeit wert zu sein.

## Zwei Arme, getrennt entscheidbar

Die Arme haben unterschiedliche Lizenz- und Installationslasten und werden
deshalb **einzeln** angenommen oder verworfen.

| | Arm A — ASR | Arm B — TTS |
|---|---|---|
| Modelle | `parakeet-tdt-0.6b-v3`, `canary-1b-v2` | `magpie_tts_multilingual_357m` |
| Lizenz | CC-BY-4.0, ungated | NVIDIA Open Model License, ungated (Gate am 2026-07-28 abgeschaltet vorgefunden) |
| Laufzeit | `onnx-asr` + `onnxruntime-gpu==1.27.0` — der in **T−1.2 bewiesene** Pfad | NeMo-Toolkit, ungeprüft auf sm_120 |
| Risiko | gering | Riva-NIM-Release-Notes nennen Magpie-Multilingual als **auf Blackwell nicht unterstützt** |

Arm A kann bestehen, während Arm B fällt. Dann bekommt dAImon NVIDIA-ASR und
behält sherpa-VITS.

## Ziel

Belastbare Zahlen zu Latenz, VRAM und Qualität für beide Arme auf dieser
Maschine, plus eine Aussage darüber, ob beide **gleichzeitig** neben dem
Eyes-VLM in die 32 GB passen.

## Dateien

- `spikes/nvidia-voice/` [neu]
- `spikes/nvidia-voice/results.json` [erzeugt]
- `spikes/nvidia-voice/samples/` [erzeugt, ignoriert]

## Abhängigkeiten

**T−1.12.v** · T−1.2 (bestanden, liefert die ORT-Laufzeit) · T−1.10 (bestanden,
liefert die VLM-VRAM-Zahl für die Koexistenzrechnung)

## Akzeptanz

### Arm A — ASR

- [ ] `onnx-asr` mit `onnxruntime-gpu==1.27.0` **nackt gepinnt**, keine
      `nvidia-*`-pip-Pakete — dieselbe Auflage wie T-3.8
- [ ] Mindestens zwei Modelle gemessen, davon eines mit Deutsch
- [ ] Gegen **sherpa-onnx als Grundlinie** auf demselben Audiomaterial, derselben
      Maschine, im selben Lauf
- [ ] Je Modell: Latenz für eine 5-s-Äußerung (p50, p95 über ≥20 Läufe),
      Kaltstartzeit, VRAM im Betrieb, VRAM nach Prozessende
- [ ] WER gegen bekannten Referenztext. **Die WER-Zahl ist relativ, nicht
      absolut**, solange das Audiomaterial synthetisch ist — das muss in
      `results.json` als `audio_source` stehen und in NOTES.md benannt sein
- [ ] Prozessende gibt VRAM auf den Ausgangswert ±50 MB zurück (dieselbe Prüfung
      wie T-3.7)

### Arm B — TTS

- [ ] Belegt, ob Magpie auf sm_120 **überhaupt lädt und synthetisiert** — die
      Riva-Aussage betrifft den NIM-Container, nicht zwingend NeMo direkt.
      Bei Fehlschlag ist das ein Ergebnis, kein Abbruch
- [ ] Deutsche Synthese aus allen verfügbaren Sprecher-Identitäten, Samples nach
      `samples/` zum Anhören
- [ ] Gesamtlatenz und RTF für einen Satz von ~25 Wörtern, ≥20 Läufe nach
      Aufwärmen, gegen sherpa-VITS `de_DE-thorsten-high` auf CPU
- [ ] **TTFA wird nur berichtet, wenn es echt gemessen wurde.** Das
      NeMo-Inferenzskript synthetisiert am Stück; ohne Streaming-Schleife gibt es
      kein Time-to-First-Audio. Fehlt es, steht `ttfa_ms: null` mit
      `ttfa_reason` — **keine aus der Gesamtlatenz geschätzte Zahl**
- [ ] VRAM im Betrieb und nach Prozessende
- [ ] Notiert: Zahl der verfügbaren Stimmen, ob Zero-Shot-Cloning fehlt, ob
      Langform beschränkt ist. **Vorab bekannt (v2607):** Cloning ist entfernt,
      es bleiben 5 feste Sprecher über 12 Sprachen

### Beide Arme

- [ ] Koexistenz gerechnet: `vram_asr + vram_tts + vram_vlm` gegen 32 607 MiB,
      mit dem VLM-Wert aus T−1.10 statt einer Schätzung
- [ ] `results.json` je Arm und Modell:
      `{arm, model, license, gated, loaded, backend, cold_start_ms, p50_ms,
        p95_ms, ttfa_ms, ttfa_reason, rtf, vram_idle_mb, vram_peak_mb,
        vram_after_exit_mb, wer, audio_source, n, verdict}`
- [ ] Lizenzlage je Modell festgehalten, inklusive des Wortlauts der
      Magpie-Zustimmung

## Abbruch- und Annahmekriterien

| Fall | Folge |
|---|---|
| Arm A schlägt sherpa in WER **oder** deckt mehr Sprachen ab, bei p95 < 500 ms | Arm A wird zweiter STT-Pfad, T-3.8 bekommt einen Absatz |
| Arm A ist nicht besser | verworfen, sherpa bleibt allein — die zweite Abhängigkeit hat sich nicht bezahlt |
| Arm B lädt nicht auf sm_120 | verworfen, in NOTES.md und PRIOR-ART.md vermerkt, T-6.4 bleibt wie geplant |
| Arm B lädt, klingt aber nicht besser als thorsten | verworfen — 3 GB VRAM für gleichwertige Sprache ist kein Handel |
| Arm B lädt und klingt besser | zweiter TTS-Pfad in **T-6.4** (Charakterstimme), nicht in T-3.9. T-3.9 bleibt der VRAM-freie Vorgabeweg mit p95 TTFA < 200 ms |
| Summe VRAM > 28 GB | Pfade schließen einander aus; der Hub serialisiert sie über die Sperre aus T-3.7 |

**Der zweite Pfad ersetzt nie den ersten.** T-3.9 bleibt Vorgabe und Rückfall,
so wie T-6.4 den Rückfall auf Piper bereits vorsieht.

## Verifikation

`tests/verify/T--1.12.sh` — von **reviewer** zu schreiben, vor der Messung, mit
Mutanten. Er muss:

1. `results.json` gegen das Schema prüfen und `n >= 20` je Zeile verlangen
2. **VRAM selbst messen** — `nvidia-smi` vor Start, während des Laufs und 5 s
   nach Prozessende — statt den vom Spike berichteten Wert zu übernehmen. Das
   ist dieselbe Lehre wie in T−1.2: selbstberichtete Werte taugen nicht als Gate
3. Für jede Zeile mit `ttfa_ms != null` **belegen**, dass ein Streaming-Pfad
   existierte; eine aus `p50_ms` ableitbare Zahl muss die Prüfung scheitern lassen
4. Die Grundlinie nachrechnen: sherpa muss im selben `results.json` stehen, sonst
   ist der Vergleich wertlos
5. Für Arm B `loaded == true` **nicht** aus dem Prozess-Exitcode ableiten,
   sondern aus einer nichtleeren Audiodatei mit plausibler Dauer

## Was Matthias tun muss

Nichts. Beide Arme laufen ohne Vorbedingung — der Magpie-Gate war am 2026-07-28
bereits abgeschaltet. Einzelheiten und der Weg zurück, falls er wiederkommt:
NOTES.md, Abschnitt „Was Matthias tun muss".

## Agent / Umfang

investigator · **L**
