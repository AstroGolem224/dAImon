# Spike T−1.10 — Was OCR auf dieser Maschine wirklich kostet

**Frage:** Was kostet OCR hier, und verdient tesseract seinen Platz noch, wenn der VLM-Worker ohnehin läuft?

**Antwort in einem Satz:** tesseract verdient seinen Platz — der lokale VLM kann einen Vollbild-Frame
überhaupt nicht lesen und ist auf dem Zuschnitt 45× langsamer. Aber der Wechsel CLI → FFI, den §4.4
vorschlägt, bringt **60 ms pro Aufruf**, nicht die erhoffte Größenordnung.

Empfehlung: **`persistent_worker`**.

---

## 0. Vorbedingung: Sprachdaten

`tesseract --list-langs` zeigte auf `/usr/share/tessdata/` nur **`afr`** und **`osd`** — wie im
Auftrag vermutet. Ohne root gelöst: `eng.traineddata` und `deu.traineddata` aus **tessdata_fast**
nach `spikes/ocr/tessdata/` geladen, `TESSDATA_PREFIX` darauf gesetzt. Funktioniert ohne jede
Systemänderung. Zusätzlich `spikes/ocr/tessdata_std/` (Standard-tessdata) für den Qualitätsvergleich.

Alle Messungen: `-l deu+eng`, `--psm 11`, dieselbe tessdata, dieselben Pixel.

## 1. Testmaterial

Aufgenommen mit `spectacle -b -n -f` auf dem echten Desktop (ein Monitor, **5120×1440 = 7,37 MP** —
vergleichbar mit screenpipes 3456×2234 = 7,7 MP).

| Bild | Was | Textdichte (Boxen-Flächenanteil) |
|---|---|---|
| `dense` | Konsole im Vollbild, `less` auf `docs/DESIGN.md` | 8,9 % |
| `sparse` | realer Arbeitsdesktop: Spiele-Launcher-Gitter, FreeCAD, Konsole, Panel | 10,4 % |
| `crop` | 600×300 aus `dense`, so wie das Gatter ihn erzeugen würde | 36,5 % |

> `sparse` ist ehrlicherweise *nicht* dünn besetzt — es ist ein echter, voller Arbeitsdesktop und
> enthält u. a. ein Terminal. Es ist damit der teuerste der drei Fälle, kein Bestfall. Der Versuch,
> ein Dolphin-Fenster im Vollbild davorzulegen, hat den Fokus nicht bekommen; das Bild zeigt den
> tatsächlichen Zustand der Maschine. Das ist als Messgrundlage brauchbarer als ein künstlich
> leergeräumter Desktop, aber es heißt: **die 0,095 s aus §4.4 für „spärlichen Text" sind hier
> nicht reproduzierbar, weil dieser Desktop nicht spärlich ist.**

## 2. Ergebnis (n = 20, `OMP_NUM_THREADS=1`, je Variante ein eigener Prozess)

| Variante | dense p50 / p95 | sparse p50 / p95 | crop p50 / p95 | Zeichen (dense/sparse/crop) |
|---|---|---|---|---|
| `cli` (Subprozess + Tempdatei) | 3725 / 4475 ms | 4236 / 5082 ms | **338 / 342 ms** | 6455 / 7368 / 620 |
| `ffi` (ctypes → System-libtesseract 5.5.3) | 4063 / 4264 ms | 4727 / 4755 ms | 338 / 342 ms | 6455 / 7368 / 620 |
| `worker` (eigener Prozess, Socketpair) | **3273 / 3284 ms** | **4084 / 4744 ms** | 352 / 360 ms | 6455 / 7368 / 620 |
| `tesserocr` (Wheel, **eigenes** tesseract 5.5.1) | 3397 / 3403 ms | 4040 / 4048 ms | **277 / 281 ms** | 6455 / 7369 / 620 |
| `vlm` (ollama gemma4:26b, n=5) | 23040 ms → **0 Zeichen** | 22993 ms → **0 Zeichen** | 15221 ms → 606 Zeichen | — |

**Die Zeichenzahlen sind über alle vier tesseract-Varianten identisch** (bis auf ein einziges Zeichen
bei tesserocr/sparse). Der Vergleich ist damit fair: gleiches Bild, gleiche Sprachen, gleicher PSM,
gleiches Ergebnis — nur die Aufrufmechanik unterscheidet sich.

Die Qualität ist gut, nicht bloß die Menge: der dense-Frame wird als vollständiges, lesbares
Markdown-Dokument zurückgegeben (siehe `text/cli_dense.txt`).

## 3. Was die Zahlen sagen

### 3.1 Die Aufrufmechanik ist fast egal — 60 ms, nicht eine Größenordnung

Direkt gemessen (`tesseract` auf einem 32×32-Blankobild, n=20):

| | fixe Kosten je Aufruf |
|---|---|
| CLI-Subprozess | **59,9 ms** p50 (fork/exec + traineddata laden + Tempdatei) |
| FFI, wiederverwendetes Handle | **0,0 ms** |

Das sind **18 % auf einem 600×300-Zuschnitt** und **1,6 % auf einem Vollbild**. In zwei unabhängigen
Läufen mit Standard-OpenMP war `ffi` auf dem Zuschnitt exakt um diesen Betrag schneller
(271 ms vs. 331 ms). Mehr ist da nicht zu holen — §4.4 erwartet vom FFI-Wechsel implizit mehr,
als er liefert.

Die Aussage aus §4.4, der CLI-Wrapper werde „bei mehreren Regionen je Frame absurd", stimmt der
Richtung nach: 60 ms × 10 Regionen = 0,6 s reine Prozess-Startkosten je Frame. Bei *einem*
Vereinigungs-Zuschnitt je Frame sind es 60 ms.

### 3.2 Der eigentliche Fund: tesseracts OpenMP ist hier ein Verlust

| `OMP_NUM_THREADS` | dense, ffi | dense, cli |
|---|---|---|
| unset (= 24 Threads) | 4387 ms | 4115 ms |
| 24 | 4433 ms | 3342 ms |
| **1** | **3303 ms** | **3338 ms** |

Auf 24 Threads ist tesseract **~25 % langsamer** als einkernig. Und der Effekt ist ansteckend:
`ffi` (ctypes-libtesseract **im selben Prozess wie numpy/OpenBLAS**) landet reproduzierbar bei
4,1–4,4 s über vier Läufe, während dieselbe Bibliothek in einem sauberen Prozess (`worker`) bei
3,27 s liegt — **~800 ms / 20 % Aufschlag allein durch Ko-Residenz zweier OpenMP-Laufzeiten.**

Das ist das stärkste Argument gegen „libtesseract direkt in den Hauptprozess linken".

### 3.3 Ein Messfehler, der fast im Ergebnis gelandet wäre

Der erste Lauf maß die Varianten *blockweise* nacheinander. Die Drift zwischen den Blöcken
(3,4 s → 4,4 s auf demselben Bild derselben Variante, je nach Zeitpunkt) war **größer als der
gemessene Effekt**. Der zweite Lauf interleavte die Varianten — aber lud damit *zwei* libtesseract-
Kopien (System 5.5.3 + gebündelte 5.5.1 im tesserocr-Wheel) in einen Prozess, was die `ffi`-Zahlen
erneut verfälschte. Erst der dritte Aufbau — **je Variante ein eigener Prozess, `OMP_NUM_THREADS=1`** —
liefert die Tabelle oben. Alle drei Rohläufe liegen bei (`raw_*.json`, `run_*.log`).

`tesserocr` ist **kein** FFI-vs-CLI-Vergleich: das Wheel bringt seine eigene tesseract 5.5.1 mit
(leptonica 1.85), nicht die System-5.5.3. Dass es auf dem Zuschnitt mit 277 ms am schnellsten ist,
ist ein Versions-, kein Mechanikunterschied.

### 3.4 Der VLM kann das nicht

`gemma4:26b` ist lokal vorhanden (17 GB, `vision`-Capability) — **nichts heruntergeladen.**

- **Zuschnitt:** 15,2 s, 606 Zeichen, und **genauer als tesseract** — liest `itiger013` und `CachyOS`
  korrekt, wo tesseract `itiger@13` und `Cachy0S` produziert. Aber **45× langsamer** (15221 ms vs. 337 ms).
- **Vollbild:** 0 verwertbare Zeichen, deterministisch, 6/6 Aufrufe, dense wie sparse.
  `prompt_eval_count = 291` verrät den Grund: der 7,4-MP-Frame wird als **~256 Bildtoken** kodiert.
  Bei `num_predict=300` sichtbar gemacht: das Modell halluziniert eine Zeile
  („Revision Runde 1.5" statt „Review Runden 1-5") und wiederholt sie wörtlich bis zum Tokenlimit.
  Herunterskalieren auf 2048×576 und Zuschneiden auf ein Drittel des Frames ändern nichts.

Das ist der **stille Fehlermodus**, vor dem §4.4 an anderer Stelle warnt: der VLM meldet keinen
Fehler, er erfindet plausiblen Text. Für den Kontextspeicher ist das schlimmer als gar kein OCR.

**tesseract verdient seinen Platz.** Nicht weil es gut ist, sondern weil die Alternative auf dieser
Hardware bei dieser Auflösung nicht funktioniert.

## 4. Textregionen-Erkennung (§4.4)

Portierung der OpenCV-Sequenz nach reinem numpy (`region_detect.py`): BT.601-Graustufen →
3×3-Morphologiegradient (separabel) → Otsu → 9×1-Schließung → Lauflängen-basierte
Zusammenhangskomponenten mit Union-Find → Formfilter (`MIN_BOX_W=8`, `MIN_BOX_H=6`, AR 1–40,
`MAX_AREA_FRACTION=0.5`). Kein OpenCV, kein scipy (beides nicht installiert).

| Bild | p50 | p95 | Boxen |
|---|---|---|---|
| dense (7,37 MP) | **57,1 ms** | 59,0 ms | 261 |
| sparse (7,37 MP) | **77,2 ms** | 78,1 ms | 542 |
| crop (0,18 MP) | 2,1 ms | 2,1 ms | 29 |

Stufenaufteilung (dense): CC 27,7 ms · Otsu 15,5 ms · Graustufen 6,2 ms · 9×1-Schließung 4,7 ms ·
Gradient 2,7 ms.

**Screenpipes 10–19 ms auf 3456×2234 sind plausibel.** Wir sind bei gleicher Pixelzahl 3–6× darüber,
und das ist genau der erwartete Abstand Python/numpy zu Rust — die Hälfte unserer Zeit steckt in
der Komponentenmarkierung, dem einzigen Teil, der nicht vektorisiert ist. In Rust oder mit einer
vektorisierten CC-Implementierung landet man im zweistelligen Millisekundenbereich.

Für die Entscheidung ist das ohnehin zweitrangig: **57 ms Regionserkennung gegen 3300 ms OCR.**
Die Lehre aus §4.4 („OCR ist zwei Größenordnungen teurer als alles davor") bestätigt sich exakt —
Faktor 58.

### 4.1 Warnung: der Vereinigungs-Zuschnitt spart auf einem Vollbild nichts

| Bild | Fläche der Vereinigung | Fläche der Einzelboxen |
|---|---|---|
| dense | **97,0 %** des Frames | 8,9 % |
| sparse | **99,1 %** des Frames | 10,4 % |

Auf einem echten Desktop verteilt sich Text über den ganzen Bildschirm — die Vereinigungsbox ist
dann fast der ganze Frame. **„Auf die Vereinigung der Regionen zuschneiden" ist auf einem
Vollbild-Frame ein No-Op.** Der Durchsatzgewinn, den §4.4 dem Zuschnitt zuschreibt, kommt in
Wahrheit aus dem Schritt davor — **„auf das fokussierte Fenster zuschneiden"** — und aus der
Signatur, die unveränderte Frames verwirft. Wer nur die Regionsvereinigung nimmt, OCRt weiter
7,4 MP für 3,3 s.

Alternative, die die Messung nahelegt: **je Box (oder je Box-Cluster) einzeln OCRen** — die Boxen
decken nur 9–10 % der Fläche. Dann werden die 60 ms Prozessstartkosten je Aufruf zum
dominierenden Posten, und der persistente Worker ist nicht mehr nur nett, sondern zwingend.

## 5. Zwei Nebenbefunde

**PSM:** `--psm 11` ist auf beiden Bildern zugleich das schnellste *und* das ergiebigste. Die Wahl
in §4.4 ist richtig.

| PSM | dense | crop |
|---|---|---|
| 3 (auto) | 4274 ms / 6305 Z. | 368 ms / 616 Z. |
| 6 (uniform block) | 4140 ms / 6376 Z. | 335 ms / 611 Z. |
| **11 (sparse)** | **4079 ms / 6455 Z.** | **336 ms / 620 Z.** |

**tessdata_fast vs. Standard:** Standard-tessdata ist auf dem Zuschnitt **doppelt so teuer** bei
identischer Ausbeute.

| tessdata | dense | crop |
|---|---|---|
| fast | 4051 ms / 6455 Z. | **268 ms / 620 Z.** |
| standard | 4608 ms / 6454 Z. | 545 ms / 619 Z. |

→ `tessdata_fast` nehmen. Das ist ein größerer Hebel (−277 ms auf dem Zuschnitt) als der ganze
CLI-→-FFI-Wechsel (−60 ms).

## 6. Empfehlung: `persistent_worker`

1. **Ein dauerhafter OCR-Prozess** (bzw. ein Pool davon), der eine `TessBaseAPI` offen hält und
   rohe Frames über einen Socket entgegennimmt. Spart die 60 ms Fixkosten je Aufruf, hält
   tesseracts OpenMP-Laufzeit aus dem Hauptprozess (dort kostet sie ~20 %), erfüllt die
   Nebenläufigkeitsanforderung aus §4.4 und kapselt Abstürze. Der IPC-Aufwand ist vernachlässigbar:
   21 MB je Vollbild über ein Socketpair sind in den Zahlen oben enthalten.
2. **`OMP_NUM_THREADS=1` je Worker setzen**, dafür mehrere Worker. Einkernig ist tesseract hier
   25 % schneller als auf 24 Threads, und n einkernige Worker skalieren sauber.
3. **`tessdata_fast`**, nicht Standard-tessdata.
4. **tesseract behalten.** Der VLM ist auf Zuschnitten 45× langsamer und auf Vollbildern unbrauchbar
   — und er scheitert still, mit halluziniertem Text.
5. **§4.4 korrigieren:** der Vereinigungs-Zuschnitt ist auf einem Vollbild-Frame wirkungslos
   (97–99 % Flächenanteil). Der Gewinn muss aus dem Fensterzuschnitt und der Signatur kommen,
   oder aus OCR je Einzelbox.

### Was das für das Budget heißt

Ein Vollbild-Frame kostet **~3,3 s**. Ein Fenster- oder Regionszuschnitt kostet **~0,27–0,35 s**.
Die Aussage aus §4.4 bleibt gültig und wird durch nichts in diesem Spike entkräftet:
**die Aufgabe des Gatters ist nicht, OCR billig zu machen, sondern selten.**

---

## Reproduktion

```fish
export DAIMON_ROLE=investigator
cd spikes/ocr
env OMP_NUM_THREADS=1 ./.venv/bin/python bench.py cli 20      # je Variante ein Prozess
env OMP_NUM_THREADS=1 ./.venv/bin/python bench.py ffi 20
env OMP_NUM_THREADS=1 ./.venv/bin/python bench.py worker 20
env OMP_NUM_THREADS=1 ./.venv/bin/python bench.py tesserocr 20
env OMP_NUM_THREADS=1 ./.venv/bin/python bench2.py            # Regionen, PSM, tessdata
./.venv/bin/python vlm_bench.py                               # VLM
```

| Datei | Inhalt |
|---|---|
| `results.json` | Ergebnis im geforderten Schema |
| `bench.py` | alle Varianten, eine je Prozess |
| `bench3.py` | interleavter Lauf (Drift-Kontrolle; enthält den Zwei-Bibliotheken-Konflikt) |
| `bench2.py` | Regionserkennung, PSM-, tessdata-Vergleich |
| `worker.py` | persistenter OCR-Worker über Socketpair |
| `region_detect.py` | §4.4-Regionserkennung, reines numpy |
| `img/` | dense.png, sparse.png, crop.png |
| `text/` | OCR- und VLM-Ausgaben je Variante, zum Qualitätsvergleich |
| `raw_*.json`, `run_*.log` | Rohläufe, inkl. der verworfenen |

## Einschränkungen

- Die Maschine war während der Messung **nicht leer** — ein realer Desktop mit laufenden Sessions.
  Daher p95 und die dokumentierte Drift ernst nehmen; Einzelwerte streuen bis 25 %.
- `tesserocr` vergleicht tesseract 5.5.1 gegen 5.5.3, nicht FFI gegen CLI. Als Mechanik-Vergleich
  zählt nur `ffi`/`worker` (beide System-5.5.3) gegen `cli` (ebenfalls System-5.5.3).
- Es gibt **keinen Ground-Truth-Text**; „Zeichen" misst Ausbeute, nicht Korrektheit. Die
  Ausgaben liegen in `text/` zur Sichtprüfung. Für die Aufrufmechanik ist das unerheblich —
  alle vier Varianten liefern denselben Text.
- Nur **ein** VLM getestet (`gemma4:26b`). Ein dediziertes OCR-VLM (z. B. ein Modell mit
  hochauflösendem Tiling) könnte am Vollbild besser abschneiden — das wäre ein Mehr-GB-Download
  und wurde bewusst **nicht** gemacht.
- Nichts systemweit installiert, kein sudo. Alles unter `spikes/ocr/`.
