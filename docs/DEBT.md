# Technische Schuld

Erzeugt aus den `ponytail:`-Kommentaren im Produktbaum (T-6.10). Jeder
Eintrag ist eine BEWUSSTE Vereinfachung mit benannter Obergrenze -- keine
Nachlaessigkeit, sondern eine Entscheidung, die jemand zurueckdrehen darf,
wenn die Obergrenze erreicht ist.

**21 Eintraege in 18 Dateien.**

Gesucht wird in `daimon/`, `face/src/`, `tools/` und `config/` -- NICHT in
`tests/mutants/` und `tests/fixtures/known-good/`. Die enthalten Kopien des
Quellbaums; wer sie mitzaehlt, bekommt dieselbe Schuld vielfach. Der erste
Lauf meldete so 295 Eintraege in 240 Dateien statt der tatsaechlichen Zahl.

Diese Datei wird ERZEUGT, nicht gepflegt:

```bash
grep -rn 'ponytail:' daimon face/src tools config
```

## `daimon/brokers/cli/broker.py`

- **Zeile 39** — kein Streaming. Obergrenze: die Antwort kommt am Stueck, TTFA ist

## `daimon/brokers/lokal/broker.py`

- **Zeile 27** — HTTP an Ollama, kein Unterprozess je Frage. Obergrenze: kein

## `daimon/ears/capture.py`

- **Zeile 127** — die Parameter sind Konstanten mit Ueberschreibmoeglichkeit,

## `daimon/ears/vad.py`

- **Zeile 72** — ein Segment ist ein Paar von Chunk-Indizes, beide einschliesslich --
- **Zeile 257** — absichtlich duenn und ohne eigenen Zustand. Obergrenze: wer

## `daimon/ears/ring.py`

- **Zeile 214** — keine Klasse, kein Iterator-Protokoll, kein Zusammenfuegen.

## `daimon/ears/interlock.py`

- **Zeile 282** — eine Zeile numpy statt eines AEC. Obergrenze: sobald der
- **Zeile 376** — `select`-Schleife, kein Dienstgeruest, keine Steuerung. Obergrenze:

## `daimon/ears/daemon.py`

- **Zeile 31** — der Vorlauf sind hier acht Chunks (256 ms) vor dem ersten lauten

## `daimon/face/echo.py`

- **Zeile 34** — blockweises Resampling verliert den Bruchteil-Versatz an den

## `daimon/gpu/worker.py`

- **Zeile 121** — kein GPU-Index. Obergrenze: sobald eine zweite Karte im
- **Zeile 231** — ein `sleep`. Obergrenze: sobald hier ein echter Ladevorgang

## `daimon/hub/sprechtext.py`

- **Zeile 150** — eine Heuristik, keine Grammatik. Obergrenze: sie laesst

## `daimon/hub/abkuehlung.py`

- **Zeile 44** — eine JSON-Datei, ein Schlüssel je Kanal. Obergrenze: sobald die

## `daimon/hub/daemon.py`

- **Zeile 835** — der Ersatzsatz hat KEINE eigene Frist. Obergrenze: er ist

## `daimon/hub/state.py`

- **Zeile 200** — nur ein Wahrheitswert plus Ablauf. Obergrenze: die Sperre

## `daimon/mind/daemon.py`

- **Zeile 111** — 20 Zeichen Rand, geraten und einmal nachgemessen. Obergrenze: wer

## `daimon/mind/router.py`

- **Zeile 226** — sauberer waere ein KWin-Script, das `resourceClass` meldet; das

## `face/src/main.rs`

- **Zeile 1048** — // ponytail: kein Grund-Unterscheiden. Der Compositor sagt nicht, warum

## `face/src/menu.rs`

- **Zeile 270** — /// ponytail: relative Pfade. Ein absoluter gehoert in die Konfiguration,

## `tools/generate-action-candidates.py`

- **Zeile 106** — eigener Serialisierer. Obergrenze: sobald verschachtelte oder

