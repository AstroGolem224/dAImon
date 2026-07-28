# T-1.2: STT-Messung

Gemessen wurde am 2026-07-28 mit `onnx-asr` 0.12.0, `onnxruntime-gpu`
1.27.0 und dem FP16-Split-Export `onnx-community/whisper-base`. Encoder und
Decoder meldeten jeweils den `CUDAExecutionProvider` an erster Stelle. Das venv
enthält keine `nvidia-*`-Pakete; CUDA 13.3.1 und cuDNN kommen vom System.

Für die Latenz wurde aus `test_wavs/0.wav` exakt 5,0 s PCM16/16 kHz gelesen:
erste Inferenz nach Prozessstart, danach je 12 Inferenzläufe. Drei vollständige
Prozessläufe ergaben kalt 292,964/402,867/418,690 ms; eingetragen ist deren
Median. Die zusammen 36 Dauerläufe ergeben p50 20,388 ms und p95 56,087 ms.

Englisch wurde über beide WAVs gegen `trans.txt` ausgewertet: 3 Fehler auf 66
Wörtern, WER 0,045455 (4,55 %; Kleinschreibung, Satzzeichen entfernt). Die
deutsche `probe.wav` enthält laut Wakeword-Befund 16 deutsch gesprochene
Wiederholungen von „Embershard“, aber keine Satzreferenz. Whisper-Base erzeugte
stattdessen eine lange Wiederholung von „im basat“: keine berechenbare deutsche
WER und qualitativ unbrauchbar.

VRAM über drei Läufe: 1372–1408 MiB vorher und höchstens 4341 MiB während der
Inferenz. Nach Exit wurden 1383/1339/1362 MiB gemessen; eingetragen sind 1392
MiB Idle-Median, 4341 MiB Maximalwert und 1362 MiB After-Median. Kein
Python-/ORT-Compute-PID blieb zurück; die kleinen Schwankungen waren
KDE-/Display-Drift. Der Worker-Kontext wurde vollständig freigegeben. `bogusValue` für
`arena_extend_strategy` wurde erneut mit `Failed to map enum name to value`
abgelehnt.

sherpa-onnx 1.13.4 trägt hier nicht auf der GPU: Das vorhandene Wheel enthält
nur seine CPU-ORT-Bibliothek und keinen CUDA-Provider. Der zuerst getestete
`istupakov/whisper-base-onnx`-Beamsearch-Export scheiterte auf CUDA mit
`GetPyObjFromTensor: Either data transfer manager ... is needed`. Variante C
bleibt gestrichen: Variante A hat native sm_120a-Cubins; AUR-Bauzeit und zweite
Toolchain ändern die Entscheidung nicht.

Empfehlung für T-3.8: Der nvidia-freie ORT-GPU-Prozesspfad, seine Latenz und die
VRAM-Rückgabe sind brauchbar. Dieses Whisper-Base-Modell aber nicht akzeptieren,
solange Deutsch so versagt. T-3.8 braucht ein besseres deutsches/multilinguales
ONNX-Modell und einen echten deutschen Referenzsatz; als vorläufiger englischer
Bezugswert gelten 4,55 % WER und 56,087 ms p95.
