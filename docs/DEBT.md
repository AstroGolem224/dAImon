# Technische Schuld

Erzeugt aus den `ponytail:`-Kommentaren im Produktbaum (T-6.10). Jeder
Eintrag ist eine BEWUSSTE Vereinfachung mit benannter Obergrenze -- keine
Nachlaessigkeit, sondern eine Entscheidung, die jemand zurueckdrehen darf,
wenn die Obergrenze erreicht ist.

**25 Eintraege in 21 Dateien.**

Gesucht wird in `daimon/`, `face/src/`, `tools/` und `config/` -- NICHT in
`tests/mutants/` und `tests/fixtures/known-good/`. Die enthalten Kopien des
Quellbaums; wer sie mitzaehlt, bekommt dieselbe Schuld vielfach. Der erste
Lauf meldete so 295 Eintraege in 240 Dateien statt der tatsaechlichen Zahl.

Diese Datei wird ERZEUGT, nicht gepflegt:

```bash
grep -rn 'ponytail:' daimon face/src tools config
```

**Und seit dem 17.08. bewacht.** Erzeugt wurde sie einmal, und dann hat sich
der Code darunter bewegt: vier von 21 Zeilenangaben zeigten ins Leere, wer
eine Schuld nachlesen wollte, landete auf einer beliebigen Zeile. Eine
Momentaufnahme, die aussieht wie ein Verzeichnis, ist die schlechtere Hälfte
von beidem.

`tests/test_debt_ledger.py` prüft beide Richtungen — jeder Eintrag zeigt auf
einen echten Vermerk, jeder Vermerk hat einen Eintrag, und die Zahl im Kopf
wird nachgerechnet.

**Seit dem 02.09. steht je Eintrag der Vermerktext, keine Zeilennummer mehr.**
Die Zeilennummer war die einzige Angabe hier, die jede Änderung *oberhalb*
ungültig macht — dreimal rot, ohne dass jemand eine Schuld angefasst hätte:
am 01.09. früh drei gewanderte Vermerke (`face/src/main.rs` +85, `menu.rs`
+39, `doppelself_gesichter.py` +51), am 02.09. um 09:0x das Buch neu erzeugt
und grün, um 11:0x wieder rot, weil eine parallele Sitzung in
`doppelself_gesichter.py` weitergearbeitet hatte (510 → 522). Da `pytest` in
vier eingebetteten Prüfständen ein Kriterium ist, kostete das rote Punkte in
Belegen, die von etwas ganz anderem handeln.

Der Eintrag ist jetzt der **Text hinter `ponytail:`**, wörtlich und
ungekürzt — er wandert nicht mit und ändert sich nur, wenn jemand die Schuld
selbst umformuliert. Dann *gehört* dieses Buch angefasst. Zwei gleich
lautende Vermerke in einer Datei wären nicht auseinanderzuhalten; das macht
der Test eigens rot, statt es still zu schlucken. Die Stelle im Code findet
der `grep` oben, und die Begründung steht ohnehin dort, nicht hier.

## `daimon/brokers/cli/broker.py`
- kein Streaming. Obergrenze: die Antwort kommt am Stueck, TTFA ist

## `daimon/brokers/lokal/broker.py`
- HTTP an Ollama, kein Unterprozess je Frage. Obergrenze: kein

## `daimon/common/config.py`
- eine Zahl aus einer Messung, kein adaptives Fenster.

## `daimon/ears/capture.py`
- die Parameter sind Konstanten mit Ueberschreibmoeglichkeit,

## `daimon/ears/daemon.py`
- der Vorlauf sind hier acht Chunks (256 ms) vor dem ersten lauten

## `daimon/ears/interlock.py`
- eine Zeile numpy statt eines AEC. Obergrenze: sobald der
- `select`-Schleife, kein Dienstgeruest, keine Steuerung. Obergrenze:

## `daimon/ears/ring.py`
- keine Klasse, kein Iterator-Protokoll, kein Zusammenfuegen.

## `daimon/ears/vad.py`
- ein Segment ist ein Paar von Chunk-Indizes, beide einschliesslich --
- absichtlich duenn und ohne eigenen Zustand. Obergrenze: wer

## `daimon/face/echo.py`
- blockweises Resampling verliert den Bruchteil-Versatz an den

## `daimon/gpu/worker.py`
- kein GPU-Index. Obergrenze: sobald eine zweite Karte im
- ein `sleep`. Obergrenze: sobald hier ein echter Ladevorgang

## `daimon/hub/abkuehlung.py`
- eine JSON-Datei, ein Schlüssel je Kanal. Obergrenze: sobald die

## `daimon/hub/daemon.py`
- der Ersatzsatz hat KEINE eigene Frist. Obergrenze: er ist

## `daimon/hub/sprechtext.py`
- eine Heuristik, keine Grammatik. Obergrenze: sie laesst

## `daimon/hub/state.py`
- EIN Platz, der neuere Termin gewinnt. Decke: mehrere
- nur ein Wahrheitswert plus Ablauf. Obergrenze: die Sperre

## `daimon/mind/daemon.py`
- 20 Zeichen Rand, geraten und einmal nachgemessen. Obergrenze: wer

## `daimon/mind/router.py`
- sauberer waere ein KWin-Script, das `resourceClass` meldet; das

## `daimon/plan/__main__.py`
- ein fester Unit-Name, keine Eindeutigkeit. Decke: zwei

## `face/src/main.rs`
- kein Grund-Unterscheiden. Der Compositor sagt nicht, warum

## `face/src/menu.rs`
- relative Pfade. Ein absoluter gehoert in die Konfiguration,

## `tools/doppelself_gesichter.py`
- die Decke des Verfahrens, ausdruecklich festgehalten statt

## `tools/generate-action-candidates.py`
- eigener Serialisierer. Obergrenze: sobald verschachtelte oder
