# Entscheidungsprotokoll Phase −1 (T−1.7)

Stand 2026-07-28. Maschinenlesbar in `spikes/summary.json`, Rohdaten je Spike unter
`spikes/*/results.json`.

Dieses Dokument stellt die Weichen. Für jeden Spike: was gemessen wurde, was daraus
entschieden ist, und was das für den Plan bedeutet. Wo die Datenlage schwächer ist als
vorgesehen, steht das hier und nicht im Kleingedruckten.

---

## Die eine Weiche, die anders gestellt wurde als erhofft

### T−1.1 Wake-Word — **durchgefallen → Plan C**

**Entscheidung: Push-to-Talk als Grundlage. Kein Wake-Word in Phase 3. T-3.5 und T-3.6 entfallen.**

Das Ziel war FRR < 10 % bei FAR < 1/h. Belegt ist FRR 19 %, gemessen an 16 Aussprachen
aus *einer* Bedingung. Die FAR ist **überhaupt nicht gemessen** — es gibt keine
Hintergrundaufnahmen. Ein `pass` wäre auf dieser Grundlage nicht zu halten.

Die Entscheidung kostet wenig. §1.3 des Designs verlangt für Aktionen ohnehin
Push-to-Talk plus Bestätigung der kanonisierten Aktion, weil Audio nicht
authentifizierbar ist. Das Wake-Word war Bequemlichkeit, keine Sicherheitsfunktion.

**Der Weg dahin ist wichtiger als das Ergebnis**, weil er zwei Messfehler aufgedeckt hat,
die beide beinahe zu einer falschen Schlussfolgerung geführt hätten:

Der erste Durchlauf ergab FRR 100 % und wurde als Werkzeugfehler abgehakt — der Rekorder
starte zu spät. Die Aufschlüsselung nach Bedingung zeigte etwas anderes: alle acht
Bedingungen sahen gleich aus, „laut" und „leise" trennten sich nur um Faktor 2 im RMS.
Es war ein Pegelproblem, kein Zeitproblem.

Danach habe ich einen Eingangstest gebaut und ihn **ausschließlich an Negativbeispielen**
geeicht. Matthias fiel dreimal durch, obwohl dasselbe Mikrofon im Diktat der Desktop-App
tadellos funktioniert. Zwei der drei Urteile waren nachweislich falsch: „übersteuert"
war aus der Spitze allein behauptet, ohne je zu zählen, wie viele Samples wirklich
anstehen (es waren 0,010 %), und „Abschnitte verschmelzen" bewertete, wie jemand spricht,
nicht ob das Mikrofon taugt.

Was gefehlt hat, war eine **Positivkontrolle**. Ohne eine ist „0 Treffer" nicht
interpretierbar — schwieriges Wort, schlechte Aufnahme und kaputter Spotter sind nicht
zu unterscheiden. Sie liegt jetzt als `spikes/wakeword/control.py` fest und prüft in drei
Stufen, dass der Aufbau trägt, bevor irgendjemand eine Stimme beurteilt.

**Der nachrüstbare Hebel ist beziffert.** Das Modell ist auf englischem Gigaspeech
trainiert, die Schreibweise des Keywords dominiert alles andere:

| Keyword | Treffer von 16 |
|---|---:|
| `EMBER SHARD` | 3 |
| `EMBA SHARD` | 11 |
| `EMBA SHARD` + `EMBA SHOT` | 13 |

Dazu: `BOOST` dominiert die Schwelle. Acht Schwellenwerte verändern die Trefferzahl nicht,
`boost 1.5 → 3.0` verdoppelt sie. `evaluate.py` variierte bis dahin nur die Schwelle —
es suchte die falsche Achse ab. Das ist behoben, es misst jetzt über beide.

Wer das aufgreift, braucht 50 Aufnahmen über acht Bedingungen und 3 h Hintergrundton.
Dann entscheiden FRR und FAR **zusammen** — jede zusätzliche Keyword-Variante macht das
Wort leichter zu treffen und leichter zu verwechseln.

---

## Die drei harten Weichen, die halten

### T−1.2 ONNX auf sm_120 — bestanden

Das pip-Wheel `onnxruntime-gpu==1.27.0` bringt native `sm_120a`-Cubins mit. Das
Arch-Paket ist *schlechter*: es liefert PTX für `compute_121`, das auf sm_120 nicht lädt.
Der Nachweis kommt aus `cuobjdump`, nicht aus einem Latenzverhältnis — kalt/warm liegt
auch mit gesperrtem Cache bei 770–900×, das ist cuBLAS-Initialisierung und beantwortet
die Frage nicht.

**Entscheidung:** pip-Wheel, nicht das Distributionspaket.

**Nachgemessen am 2026-07-28**, weil die Zahlen fehlten, an denen T-3.8 hängt.
`onnx-asr 0.12.0` mit `whisper-base` (split ONNX, fp16) über den
`CUDAExecutionProvider`:

| | |
|---|---|
| Kaltlatenz (erste Inferenz) | 403 ms |
| Dauerlatenz, 5-s-Äußerung | 20,4 ms p50 · 56,1 ms p95 (n=36) |
| VRAM leer → Inferenz → nach Exit | 1392 → 4341 → 1362 MB |
| **VRAM-Rückgabe** | **ja** |
| WER Englisch | 4,5 % |

Die Dauerlatenz ist unkritisch — 20 ms für 5 s Audio ist Faktor 250 schneller als
Echtzeit. Die VRAM-Rückgabe funktioniert, das war die eigentliche Sorge aus T-3.8.

**Aber: `whisper-base` ist für Deutsch unbrauchbar.** Auf `probe.wav` (16 deutsch
gesprochene Wiederholungen) halluziniert es vielfach „im basat". Das ist ein Befund
über die Modellgröße, nicht über den Aufbau — Englisch liegt mit 4,5 % WER im
Erwartungsbereich.

> **Korrektur 2026-07-28, noch am selben Tag.** Diese Messung hat das *falsche
> Modell* geprüft. `docs/DESIGN.md` v5.4 legt die Transkription längst fest:
> `onnx-asr` + **`nemo-parakeet-tdt-0.6b-v3`**, int8, deutsche WER 3,64. Der
> Auftrag an den messenden Agenten nannte „sherpa-onnx, ersatzweise onnx-asr"
> ohne Modellvorgabe — ich hatte vor dem Beauftragen nicht ins Design gesehen.
>
> Was die Messung damit **trotzdem** belegt, und darum bleibt sie stehen: die
> *Infrastruktur* trägt. CUDA-Provider aktiv, Kaltlatenz 403 ms, Dauerlatenz
> 20 ms, und vor allem die VRAM-Rückgabe nach Prozessende — das war die
> eigentliche offene Frage aus T-3.8, und sie ist beantwortet.
>
> Was sie **nicht** belegt: die Modellwahl. Dass `whisper-base` auf Deutsch
> halluziniert, ist keine Überraschung und kein Argument gegen irgendetwas —
> es ist das kleinste Modell der Familie. Die deutsche WER gehört gegen
> `parakeet-tdt-0.6b-v3` gemessen, und dafür gibt es bereits einen eigenen
> Spike: **T−1.12**, `spikes/nvidia-voice/`.

**Auflage für T-3.8:** ein größeres oder deutsch-spezifisches Modell wählen und die
deutsche WER gegen echte Satzreferenzen messen. `whisper-base` ist die untere Schranke,
nicht die Wahl. Der Plan nennt `sherpa-onnx` als bevorzugten Weg, weil es zugleich VAD
und TTS liefert — das ist damit noch offen und gehört dort geprüft.

**Variante C (`whisper.cpp-cuda`) ist gestrichen**, siehe Plananmerkung bei T−1.2. Ein C-API-Arbeitsprozess ist
hart blockiert, weil der ORT-Kern statisch ins pybind-Modul gelinkt ist.

### T−1.3 layer-shell — bestanden

Overlay über Vollbild sichtbar (per Screenshot und Pixelprobe belegt, nicht vom Client
berichtet), Leerlauf-CPU 0,17 %, **kein GPU-Kontext** (null DRI-FDs), Click-Through
funktioniert. KDE-Fehler 503121 reproduziert (0 von 20), Umgehung „Properties neu setzen"
20 von 20.

**Entscheidung:** natives layer-shell-Overlay wie geplant, mit der Umgehung fest eingebaut.

### T−1.4 Portal-Persistenz — bestanden

`restore_token` mit `persist_mode=2` hält über einen echten Prozessneustart: kein Dialog,
`Start` → `Response` in 5,07 ms. Der Wert stammt aus dem DBus-Mitschnitt, nicht aus einer
Selbstauskunft — die drei Läufe mit echtem Dialog dauerten 3,6 / 4,6 / 2,9 s und enthalten
KDE-Interaktionssignale, die beim Neustart fehlen. Positivkontrolle inklusive.

**Ein Befund gehört ins Bedrohungsmodell:** ein noch unbenutzter `restore_token` aus einer
*anderen* Portal-Session wurde in 0,012 s **ohne Dialog** akzeptiert. Der Token ist nicht
an das Session-Objekt gebunden. `token.json` ist damit eine **Fähigkeit**, keine
Einstellung — wer sie lesen kann, bekommt aus einem beliebigen Prozess Bildschirmzugriff
ohne Rückfrage. Das liegt innerhalb von §1.3 (Angreifer mit Codeausführung unter derselben
uid wird nicht abgewehrt), gehört dort aber ausdrücklich benannt. Datei liegt mit Modus
0600 und ist per `.gitignore` ausgeschlossen.

**Offen:** das Verhalten über einen echten Reboot. Nicht erzwungen; `reboot_check.py` holt
es nach dem nächsten regulären Neustart mit einem Befehl nach.

---

## Die übrigen

### T−1.5 Mood-Mapping — bestanden mit Auflage

Datenbasis sind 2 Sitzungen statt der geforderten 5. Matthias hat entschieden, die
Erhebung nicht abzuwarten; der Rekorder läuft weiter, `analyze.py` ist jederzeit erneut
ausführbar.

Belegt ist bereits: **`StopFailure` ist ein echtes Hook-Ereignis und feuert** — das war
offen. Und die Hooks greifen ohne Neustart der Sitzung.

**Auflage für T-0.3.t:** die Abweichungsklasse `NICHT_ENTSCHEIDBAR` muss dort als Test
auftauchen. Eine `Notification` ohne `notification_type` macht `needs_input` und `idle`
ununterscheidbar — und `needs_input` ist der Mood, an dem die ganze Idee hängt.

### T−1.6 Hook-Overhead — bestanden mit Auflage

Gemessen wurde der Hook-Pfad, nicht Ende-zu-Ende: das `claude`-CLI meldet
`OAuth session expired`. Die Claude-Code-Grundlast fehlt in allen Bedingungen
gleichermaßen und verschiebt die Differenzen nicht.

| Bedingung | p50 | Aufschlag |
|---|---:|---:|
| aus | 0 ms | — |
| gesund | 3,07 ms | 3,07 ms |
| **tot** | 2,78 ms | 2,78 ms |
| **hängend** | 1005 ms | **1005 ms** |

Ein **toter** Daemon kostet nichts — weniger als ein gesunder, weil „connection refused"
sofort zurückkommt. Das Alltagsrisiko ist ausgeräumt. Ein **hängender** kostet 1005 ms je
Ereignis; `-m 1` deckelt den Schaden, aber eine Sekunde je Werkzeugaufruf ist untragbar,
wenn `PreToolUse` an jedem hängt.

**Auflage für T-0.11:** die Bridge muss abgekoppelt aufgerufen werden oder mit einem
deutlich kleineren Zeitlimit (Größenordnung 100 ms).

> **Korrektur 2026-07-29, beim Umsetzen von T-0.11.** Hier stand, ein einfacher
> Hintergrundaufruf schlage fehl, weil der abgekoppelte Prozess stdout und stderr erbe
> und der Aufrufer deshalb weiter warte. **Das war falsch.** Nachgemessen gegen eine
> künstlich hängende Bridge, jeweils Median aus mehreren Läufen, und ausdrücklich auch
> mit einem Aufrufer, der die Ausgabe über eine Pipe einliest — so ruft Claude Code
> seine Hooks auf:
>
> | Variante | Median |
> |---|---:|
> | blockierend, `curl -m 1` (Zustand aus T−1.6) | 1004 ms |
> | `curl … >/dev/null 2>&1 &` | **0,9 ms** |
> | `setsid` mit gelösten Deskriptoren | 1,4 ms |
>
> Der Hintergrundaufruf trägt also, sofern **die Deskriptoren des curl selbst** nach
> `/dev/null` gehen. Was seinerzeit fehlschlug, war mein Messaufbau: er hielt eine Pipe
> offen und wartete auf deren EOF. Die Schlussfolgerung stammte aus dem Werkzeug, nicht
> aus der Sache — derselbe Fehler wie beim Wake-Word, eine Stufe kleiner.
>
> **Und noch eine Korrektur, eine Stunde später — die obige war ebenfalls falsch.**
> Die Tabelle misst nur die *Zeit*. Wer auch die **Zustellung** misst, sieht das hier:
>
> | Variante | Median | zugestellt |
> |---|---:|---:|
> | `curl … >/dev/null 2>&1 &` | 0,7 ms | **0 von 10** |
> | `cat > tmpfile`, dann `setsid` | 1,4 ms | **10 von 10** |
>
> Der einfache Hintergrundaufruf ist schnell, **weil er nichts tut**. Die Nutzlast kommt
> über stdin; der abgekoppelte `curl` liest sie erst nach dem Abkoppeln, und bis dahin
> hat der Aufrufer die Pipe geschlossen. Zehn von zehn Nutzlasten kamen kaputt an.
>
> Die ursprüngliche Sorge war also berechtigt, nur die Begründung stimmte nicht: es
> scheitert nicht am Erben von stdout/stderr, sondern am Wettlauf um stdin.
>
> Umgesetzt ist deshalb `cat > tmpfile` im Vordergrund — das kostet genau die Zeit, die
> das Lesen von stdin braucht — und danach `setsid` mit gelösten Deskriptoren.
> 1,4 ms gegen eine Grenze von 200.
>
> Der Fehler dahinter ist der interessante Teil und derselbe wie beim Wake-Word: ich habe
> die bequeme Größe gemessen (Latenz) statt der, um die es geht (kommt es an), und aus
> einer grünen Zahl eine Aussage gemacht. Der Verifizierer zu T-0.11 misst deshalb beides.

### T−1.9 KWin-Fokus — bestanden

50 von 50 Wechseln, keine Auslassung, p95 = 0,9 ms. Das Script überlebt
`kwin_wayland --replace` in der echten Sitzung: PID 1962 → 166314, nach 1,4 s wieder
geladen, nach weiteren 9,8 s wieder echter Fensterverkehr.

**Einschränkung, die bleibt:** `captionChanged` feuert nur, wenn die Anwendung ihren Titel
ändert. Terminalausgabe, Scrollen, ein neuer Absatz erzeugen nichts. **Der Abtast-Timer
aus T-5.4 ist damit nicht optional**, er trägt den Großteil der echten Inhaltsänderung.

### T−1.10 OCR — bestanden

**Entscheidung: tesseract behalten, als dauerhafter Arbeitsprozess.** Nicht als
CLI-Wrapper mit Temporärdatei je Aufruf und nicht als ctypes-FFI im Hauptprozess — letzteres
kostet reproduzierbar ~800 ms je Vollbild durch OpenMP-Koexistenz mit numpy.

Das VLM ersetzt tesseract nicht: `qwen3-vl:8b` ist auf dem Zuschnitt 56× langsamer und
liefert auf dem Vollbild in 37 von 37 Aufrufen gar nichts — es verlässt den Denkfaden nie
und beginnt das Transkript nicht. `"think": false` wird von ollama für dieses Modell nicht
durchgesetzt.

**Zwei Korrekturen am Plan:** Der Zuschnitt auf die *Vereinigung* der Textregionen ist ein
No-Op — sie deckt 97 bis 99 % des Vollbilds ab. Der Zuschnitt auf die *Einzelboxen* wäre
zwar 8,9 % der Fläche, kostet aber 261 Aufrufe × 60 ms = 15,7 s gegen 3,3 s Vollbild. **Der
Gewinn liegt im Zuschnitt aufs fokussierte Fenster.**

**Und ein Befund für die Sicherheitsschicht:** die Fehlerarten sind nicht gleichwertig.
tesseract erzeugt sichtbaren Zeichenmüll, das VLM plausible falsche Wörter
(`Bedrohungsmeldung` statt `Bedrohungsmodell`). Für eine Schicht, die Bildschirmtext in die
Kognition einspeist, ist stille Verfälschung der schlechtere Fehler — sie ist von korrektem
Eingang nicht unterscheidbar. **Kein VLM im Textpfad.**

### T−1.11 AT-SPI2 — bestanden mit Einschränkungen

Sechs Anwendungen geprüft, vier Aktivierungen über die `Action`-Schnittstelle, jeweils per
Vorher/Nachher-Baumvergleich belegt.

**Entscheidung: AT-SPI kommt in den Aktionskatalog, aber als Teilfläche, nicht als
allgemeine.** Qt-Anwendungen exportieren im Auslieferungszustand **gar keinen Baum**;
`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` ist nötig und für fremde, bereits laufende Programme
nicht erzwingbar. MATE-Programme sind gar nicht erreichbar.

Weiter gilt: voller Baumwalk 91–245 ms gegen 1–2 ms für die Top-Level-Ebene, also cachen und
nur das Delta live abfragen. 22 bis 40 % der Knoten haben keinen Namen. Und ein
`do_action`-Rückgabewert beweist nichts — Qts `ShowMenu` liefert `True` ohne Wirkung.

**Jede aus dem Baum abgeleitete Bezeichnung ist `tainted`** und gehört durch die Vorschau des
Auth-Agenten.

---

## Was das für die weiteren Phasen heißt

| Änderung | betrifft |
|---|---|
| Wake-Word entfällt, nur Push-to-Talk | **T-3.5, T-3.6 entfallen** |
| Bridge muss abgekoppelt oder mit ~100 ms Zeitlimit | **T-0.11** (Auflage) |
| `NICHT_ENTSCHEIDBAR` als Testfall | **T-0.3.t** (Auflage) |
| tesseract als Arbeitsprozess, Fensterzuschnitt | **T-4.x** |
| Abtast-Timer ist nicht optional | **T-5.4** |
| `restore_token` ist eine Fähigkeit | **§1.3 Bedrohungsmodell** |
| ONNX aus dem pip-Wheel, nicht aus Arch | **T-0.1** (venv) |

---

## Offene Ehrlichkeit

Zwei Spikes tragen ein „mit Auflage", und beide aus demselben Grund: die volle Datenlage
wurde nicht abgewartet. T−1.5 hat 2 statt 5 Sitzungen, T−1.6 misst den Hook-Pfad statt
Ende-zu-Ende. Beides ist bewusst entschieden und hier benannt, damit es nicht später als
gemessen durchgeht.

T−1.1 ist der einzige gescheiterte Blocker. Er blockiert den Fortgang trotzdem nicht, weil
der Plan für genau diesen Fall Plan C vorsieht und Plan C gewählt ist.

Der Codex-Review des Gesamtplans endete nach 5 Runden ohne `APPROVED`. Die zwei dort
benannten Punkte sind geschlossen (Anhang C4 und D), **diese Nacharbeit und alles danach ist
nicht gegengelesen** — einschließlich dieses Dokuments.


---

## Nachtrag: T−1.12 und eine Lücke zwischen Design und Plan

`spikes/nvidia-voice/` enthält den Spike **T−1.12 — NVIDIA-Sprachstack als zweiter
Pfad**. Er steht in `docs/DESIGN.md` v5.4, aber **nicht** in
`docs/IMPLEMENTATION-PLAN.md` v5.2. Design und Plan sind hier auseinandergelaufen.

Folgen, die benannt gehören:

- **Gate P−1 kennt T−1.12 nicht.** Es prüft die acht Spikes des Plans. Ein grünes
  Gate bedeutet also nicht, dass T−1.12 entschieden ist.
- **Der Stand ist „offen"**: Werkzeug steht (`bench_asr.py`, `bench_tts.py`,
  `SPEC.md` mit Akzeptanz- und Abbruchkriterien), die Messung ist nicht gelaufen.
- Der Vorbefund in `smoke.json` ist ausdrücklich **kein Ergebnis**: n = 2–3,
  synthetisches Testaudio, kein Verifizierer. Die Zahlen sind untereinander
  vergleichbar und absolut wertlos. Dazu kommt: `smoke.json` steht in der
  `.gitignore` des Spikes — das Design verweist damit auf eine Datei, die im Repo
  nicht liegt.
- **Endgültig** ist ein Nebenbefund: `canary-180m-flash` ist in `onnx-asr` 0.12.0
  nicht enthalten. Der Weg dorthin bräuchte NeMo und damit Torch, für ein Modell,
  das lediglich schneller wäre. Fällt aus.

**Vor P0 zu entscheiden:** wird T−1.12 in den Plan aufgenommen und ins Gate gezogen,
oder läuft er als eigenständige Untersuchung neben der Phasenkette? Solange das offen
ist, steht die Modellwahl für T-3.8 nicht fest — und `whisper-base` ist sicher nicht
die Antwort.
