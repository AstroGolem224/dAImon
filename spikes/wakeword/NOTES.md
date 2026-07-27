# T−1.1 — Wake-Word „Embershard"

**Status: Werkzeug fertig, Aufnahmen offen.** Ein Befund steht schon fest und ändert
die Konfiguration.

## Der Befund vor der ersten Aufnahme

`EMBERSHARD` als **ein Wort** schlägt **nie** an — bei keiner Schwelle, auch nicht
bei 0,02. `EMBER SHARD` als **zwei Wörter** feuert zuverlässig, mit null
Fehltreffern auf einem Negativsatz.

Der Unterschied liegt allein in der Tokenisierung:

```
EMBERSHARD   →  ▁E M BER SH ARD      schlägt nie an
EMBER SHARD  →  ▁E M BER ▁SHA R D    feuert bei 0,02 / 0,05 / 0,10
```

Die Wortgrenze `▁` vor `SHA` macht es. Gesprochen bleibt es dasselbe Wort — das
Modell zerlegt nur anders. Die `keywords.txt` nutzt deshalb die Zweiwortform.

Der Test lief gegen synthetische Sprache (sherpa-onnx VITS, `en_US-amy-low`), mit
einem **Positiv-Kanarienvogel**: `hello world` gegen das mitgelieferte
Modell-Keyword feuert bei 0,05. Ohne den wäre nicht unterscheidbar gewesen, ob
das Wort schlecht ist oder die Kette kaputt.

> Synthetische englische Sprache ist **kein Ersatz** für deutsche Aussprache durch
> einen echten Sprecher. Der Test zeigt nur, dass die Tokenisierung überhaupt
> erreichbar ist. Das Urteil fällt mit den echten Aufnahmen.

## Ablauf

```bash
cd spikes/wakeword

python3 record.py positive          # 50 Proben, geführt durch 8 Bedingungen
python3 record.py background --min 60   # Hintergrund, mehrfach bis 3 h
python3 record.py status            # Fortschritt

venv/bin/python evaluate.py         # rechnet FRR und FAR über ein Schwellenraster
```

Aufnahme über `pw-record`, 16 kHz mono S16LE — genau das Format des Erkenners,
keine Umrechnung, keine Python-Audio-Abhängigkeit.

Die acht Bedingungen (normal, leise, laut, fern, schnell, nebenbei, mit
Hintergrund, abgewandt) werden reihum durchlaufen. Die Streuung ist der Punkt:
Ein Wake-Word, das nur im Idealfall anschlägt, ist im Alltag nutzlos.
`evaluate.py` weist die Falsch-Negativ-Rate **je Bedingung** aus — daran sieht
man, welche Situation das Wort killt.

## Zielwerte

| | |
|---|---|
| Falsch-Negativ-Rate (FRR) | < 10 % |
| Falsch-Positiv-Rate (FAR) | < 1 pro Stunde |

Erreicht kein Schwellenwert beides gleichzeitig, gilt Plan B: `livekit-wakeword`
trainiert ein deutsches Modell und exportiert kompatibel. Plan C: nur
Push-to-Talk, der Rest von Phase 3 läuft unverändert.

## Aufgezeichnetes

`samples/` ist **nicht** im Repository — Stimmaufnahmen gehören nicht auf GitHub.
Die Auswertung schreibt nur Zahlen nach `results.json`.
