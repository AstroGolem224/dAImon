# Ledger T-7.4.v — Verifizierer für den Tonmitschnitt in die Datenbank

**Ausgang 18.08.: `produktdefekt-rot`** (K3 und K7)
**Ausgang 19.08.: `produktdefekt-rot` — nur noch K3. NICHT eingefroren.**
Siehe §Nachlauf am Ende.

Der Verifizierer ist gebaut, er kann grün werden (Gut-Muster: 9 von 9), er
kann rot werden (10 von 10 Mutanten erkannt, jeder am zugedachten Kriterium)
— und gegen den echten Baum ist er rot an zwei Kriterien. Beide sind Befunde
am Produkt, keiner ist ein Werkzeugfehler.

Die zentrale Zusage des Tasks — **Rohaudio wird nie geschrieben** — hält.
Sie ist gemessen, und zwar nach Inhalt und mit Positivkontrolle.

---

## Provenienz

| | |
|---|---|
| Worktree | `/mnt/data/AI/repos/dAImon-t74` |
| Branch | `reviewer/p4-T-7.4v`, ausgehend von `ea61a13` |
| Rolle | `DAIMON_ROLE=reviewer` — kein Produktivcode geschrieben (`git status` sauber außerhalb `tests/**`) |
| Datum | 2026-08-18, 12:55–14:2x CEST |
| Artefakte | `tests/verify/T-7.4.sh`, `tests/verify/t74_pruefstand.py`, `tests/fixtures/known-good/T-7.4/` (24 Dateien), `tests/mutants/T-7.4/erzeugen.sh` (10 Mutanten, erzeugt) |
| Gelesen | `PLAN.md` (Vorab-Festlegungen), `docs/IMPLEMENTATION-PLAN.md` T-7.4 (Z. 1946 ff.) und T-7.3, `docs/DESIGN.md` §1.1/§4.2/§5.4/§7.2d und Z. 382–386, `docs/REVIEWER-UEBERGABE-17.08.md` §1, `tests/verify/T-7.1.sh`+`t71_pruefstand.py`, `tests/verify/T-7.3.sh`+`t73_pruefstand.py` und `tests/evidence/LEDGER-T-7.3.v.md` als Formvorlage |
| Freeze | **NICHT** aufgerufen (Auflage) |

Commits auf `reviewer/p4-T-7.4v`:

```
7af200e  T-7.4.v: Geruest des Verifizierers -- neun Kriterien, Sperre im PATH
36cefad  T-7.4.v: Pruefstand misst -- neun Kriterien, Inhaltssuche gemessen kalibriert
4e649a6  T-7.4.v: Gut-Muster -- der erfuellte Stand, gegen den es gruen wird
f147d9b  T-7.4.v: zehn Mutanten, erzeugt statt eingecheckt -- meta.sh 10 von 10
```

**Kein fremder Prozess angefasst, kein fremdes Datum geschrieben.** Der
`systemctl`-Vorschalter hat über den ganzen Lauf **null** Aufrufe an eine
echte Unit gesehen; die vier Zeilen, die überhaupt abgesetzt wurden, stehen
im Protokoll des abbildenden Vorschalters und trafen die transienten Units
dieses Laufs. Das echte Archiv des Nutzers wurde ausschließlich `mode=ro`
gelesen.

---

## Der Zuschnitt: neun Kriterien

Die fünf Akzeptanzpunkte und der Verifikationsabsatz, aufgelöst in einzeln
abrechnende Kriterien. Ein Kriterium ohne Messung zählt als rot.

| | Was | Echtbaum |
|---|---|---|
| K1 | Stille erzeugt **keinen** Archiveintrag **und keinen** STT-Aufruf | ✅ |
| K2 | Positivkontrolle: Sprache erzeugt **genau einen** Aufruf und **genau einen** Eintrag, und das Transkript steht in der Datenbank | ✅ |
| K3 | STT-Arbeitsprozess: warm bei Sprache, **Ende bei Stille** | ❌ |
| K4 | Im ganzen Archivverzeichnis **keine Audiodatei**, gesucht nach Inhalt | ✅ |
| K5 | Kein Weg für Rohaudio: kein Audio-Parameter, verbotene Arten abgewiesen, WAV nach der Runde weg | ✅ |
| K6 | Archivpfad am **selben** Strom: eine Aufnahme, ein STT-Aufruf, ein Eintrag | ✅ |
| K7 | Der Pausenschalter schließt **Ton und Bild** | ❌ |
| K8 | Nach der Pause erzeugt dieselbe Einspielung nichts | ✅ |
| K9 | Das Transkript ist `tainted` | ✅ |

```
T-7.4: ROT -- 2 von 9 Kriterien rot: K3, K7
```

---

## Die zwei Befunde

### Befund 1 (K7) — die Pause lässt den Tonstrom offen

`daimon/recorder/pause.py:46`

```python
PAUSE_UNITS = (RECORDER_UNIT, EYES_UNIT)
```

`daimon-ears.service` steht in `ERLAUBTE_UNITS` (Z. 41), aber **nicht** in
`PAUSE_UNITS`. Der Modulkopf derselben Datei sagt selbst, was fehlt:

> „Wer T-7.4 baut, ergänzt die Ohren-Unit in `PAUSE_UNITS` und den Tonstrom
> in der Messung."

T-7.4 ist gebaut (`daimon/recorder/audio.py`, Aufrufer in
`daimon/ears/daemon.py:291`), die Ergänzung ist unterblieben.

**Gemessen, nicht angenommen.** Der Prüfling läuft unverändert; nur der
`systemctl` im PATH zeigt auf transiente Units dieses Laufs, von denen jede
einen echten PipeWire-Aufnahmestrom hält. Ein `pw-dump` vor dem Eingriff,
eines danach:

```
Aufnahmestroeme vorher:  {rec: 1, eyes: 1, ears: 1, still: 1}
gestoppte Units (Originalwortlaut): ['daimon-eyes.service', 'daimon-recorder.service']
Aufnahmestroeme nachher: {rec: 0, eyes: 0, ears: 1, still: 1}
```

`still` ist die **Unterscheidungskontrolle**: ein vierter Strom, der per
`SIGSTOP` nur angehalten wurde. Er ist nachher noch da. Damit heißt
„verschwunden" hier *geschlossen* und nicht *pw-dump hat nichts gefunden* —
und das ist genau die Unterscheidung, um die es in Design §4.2 geht.

**Warum das für T-7.4 ein eigener Befund ist und nicht nur eine Wiederholung
von T-7.3.** T-7.3 hat denselben Ursprung gefunden (dortiges K8). Die
Wirkung, die T-7.4 zusagt, ist eine andere und schlimmer herum:

> Der Archivpfad hängt am **selben** Stream wie die Live-Wahrnehmung und
> wird vom Pausenschalter **gemeinsam mit ihr** geschlossen.

Gemessen wird das Gegenteil: die Pause schließt den **Archivweg** (der
Recorder ist weg, K8 belegt es — dieselbe Einspielung erzeugt danach keinen
Eintrag), und sie lässt den **Strom offen**. Der Nutzer drückt „Mitschnitt
pausieren", das Archiv steht still, und das Mikrofon läuft weiter: STT,
Mind, Antwort. Was aufhört, ist das Protokollieren, nicht das Zuhören. Von
zwei Dingen, die gemeinsam aufhören sollen, hört genau das falsche auf.

**Ein Zeile-für-Zeile-Fix reicht nicht.** `PAUSE_UNITS` um `EARS_UNIT` zu
ergänzen (so tut es das Gut-Muster) schließt den Strom. Die **Messung** in
`stoppe()` bleibt danach blind: `video()` zählt nur
`Stream/Output/Video`, der Bericht kann also weiter `ok: true` melden, wenn
der Tonstrom stehen bliebe. Der Modulkopf verlangt beides — „ergänzt die
Ohren-Unit **und den Tonstrom in der Messung**". Ohne den zweiten Teil ist
die Zusage wieder nur eine Behauptung über `systemctl`-Rückgabewerte.

### Befund 2 (K3) — der STT-Prozess beendet sich nie

Akzeptanzpunkt 2: *„Bei anhaltender Sprache bleibt der STT-Arbeitsprozess
warm, bei Stille beendet er sich wie gehabt."* Design Z. 382 sagt dasselbe,
§5.4 schärfer: *„Kein Modell bleibt geladen. Idle-Timer → Prozessende."*

Der Prozess, den die Ohren rufen, ist `daimon-stt.service` →
`daimon/gpu/stt.py`. Sein `lauf()` hat keine Leerlauffrist:

```python
def lauf(erkenner: Erkenner, srv: socket.socket) -> int:
    """Kein Leerlauf-Exit -- siehe Modulkopf."""
```

**Gemessen an drei Prozessen, eine Ablesung:**

```
Leerlauffrist 1.0s, 12 Anfragen an den warmen Prozess;
lebt nach 3.0s: {'stt-still': True, 'stt-warm': True, 'worker-still': False}
```

`worker-still` ist die **Positivkontrolle** und trägt den Befund: derselbe
Bauform-Fall aus `daimon/gpu/worker.py` (Accept-Schleife mit
`srv.settimeout(idle_s)`) **ist** nach seiner Frist beendet. Die Messung kann
ein Prozessende also sehen. `stt-still` lebt trotzdem. Ohne diese dritte
Messung wäre „lebt noch" von „mein `poll()` funktioniert nicht" nicht zu
unterscheiden gewesen.

Der Prüfstand setzt vor dem Lauf `stt.LEERLAUF_S = 1.0` auf dem Modul. Wirkt
die Frist nicht, weil `lauf()` sie gar nicht liest, läuft die Schleife
weiter — und genau das ist der Befund. Es gibt keinen Ausgang, den ein
falscher Parameter hätte verfehlen können.

#### Zwei Zusagen, die sich widersprechen — das ist eine Entscheidung, kein Fix

Der Modulkopf von `daimon/gpu/stt.py` **lehnt den Leerlauf-Exit
ausdrücklich ab** und begründet es:

> „Anders als der GPU-Worker aus T-3.7 […]: dort war die belastbare Größe
> die VRAM-Rückgabe nach dem Exit. Hier gibt es kein VRAM zurückzugeben, und
> ein Neustart kostet 843 ms Ladezeit. Das Modell im Speicher IST die
> Latenzzusage."

Das ist ein Argument, kein Versehen — T-3.8 ist auf die CPU gewandert und
belegt 0 VRAM. Dagegen stehen T-7.4 Punkt 2 und Design §5.4 wörtlich. Zwei
Fassungen einer Regel sind eine Regel und eine Attrappe; welche gilt,
entscheidet nicht der Verifizierer.

**Was Matthias entscheiden muss** — eines von beiden, und die Entscheidung
gehört ins Dokument, nicht in den Code:

* **(a) Der Plan gilt.** `lauf()` bekommt eine Leerlauffrist (so wie im
  Gut-Muster; die Unit hat `Restart=on-failure`, ein Leerlauf-Exit 0 löst
  keinen Neustart aus, und der Socket startet ihn beim nächsten Wort). Preis:
  843 ms auf der ersten Äußerung nach jeder Pause.
* **(b) T-3.8 gilt.** Dann ist Akzeptanzpunkt 2 von T-7.4 gegenstandslos und
  Design §5.4 braucht eine Ausnahme für Modelle ohne VRAM. Der Satz
  „bei Stille beendet er sich **wie gehabt**" ist dann falsch — „wie gehabt"
  heißt heute: gar nicht.

Der Befund ist unabhängig davon echt: der Ist-Zustand hält den Wortlaut
nicht ein. Was T-7.4 selbst betrifft, ist erfüllt und gemessen — **der
Archivpfad verlängert keine Wärme**: ein Sprachabschnitt erzeugt genau einen
STT-Aufruf (K2/K6), das Archiv nutzt das Transkript, das ohnehin da war.

---

## Die Bauform, und warum sie so ist

### Der STT-Aufruf wird an einem fremden Prozess gezählt

Der Verifikationsabsatz verlangt „gemessen am Prozess, nicht an einem Zähler
des Prüflings". Am `stt.sock` des Laufs horcht deshalb ein **eigener
Prozess**, der je Verbindung eine Zeile schreibt (Pfad der WAV-Datei,
existierte sie beim Aufruf, wie groß war sie). Der Prüfling ruft dorthin mit
seinem echten `ruf_socket` — an seinem Sprachpfad ist nichts eingespeist.
„Kein STT-Aufruf bei Stille" heißt damit: diese Datei hat keine Zeile
bekommen.

Die Einspielung selbst geht durch den echten Pfad: PTT an →
`Ohren.block()` mit 40 Blöcken → 20 Blöcke Nachlauf → PTT aus. Segmentiert
wird von der echten `vad.Hysterese`, gemeldet von der echten
`melde_transkript`, abgelegt vom echten `Recorder` auf einer eigenen
Datenbank unter `$(mktemp -d)`.

### Rohaudio wird nach Inhalt gesucht — und die Suche wird in jedem Lauf geprüft

Eine Suche nach `*.wav` findet `abschnitt.dat` nicht. Gesucht wird nach
Signaturen (RIFF/WAVE, OggS, fLaC, ID3, Matroska/WebM, CAF, AIFF, AU, ADTS)
an **jeder** Stelle jeder Datei, und nach rohem PCM an seiner Statistik — in
den Dateien des Archivverzeichnisses **und** in jeder Spalte jeder Zeile
jeder SQLite-Datei darin (ein Blob ist der bequemste Ort für Rohaudio, und
eine reine Dateisuche fände ihn nur als „irgendwo in archiv.db").

**Die PCM-Erkennung ist gemessen kalibriert, nicht geraten.** Zwei Achsen:

| Datei | Betrag | Gleichanteil | Differenz/Betrag |
|---|---|---|---|
| `test_wavs/de.wav` (echte Sprache) | 5000 | 0,03 | 0,26 |
| synthetisches Sprachsignal | 2068 | 0,00 | 0,25 |
| reiner Sinus | 5729 | 0,00 | 0,09 |
| `archiv.db` (nur Text darin) | 13995 | **0,82** | **0,61** |
| espeak-Wörterbuch `de_dict` | 17870 | **0,58** | **0,81** |
| Zufallsbytes | 16305 | — | **1,34** |

Grenzen: `Differenz/Betrag < 0,40` und `Gleichanteil < 0,15`. Audio ist
bandbegrenzt und gleichanteilsfrei; Bytes, die nur als int16 gelesen werden,
sind es nicht. **Die erste Fassung hatte nur die eine Achse und meldete die
eigene Datenbank als Rohaudio** — ein Melder, der auf das eigene Archiv
anspringt, ist so wenig wert wie einer, der schweigt.

**Die Positivkontrolle ist Pflicht und läuft in jedem Lauf.** Nach der
Messung werden sechs getarnte Audiostücke ins Archivverzeichnis gelegt
(`abschnitt.dat` = WAV, `notizen.txt` = Ogg, `index.bin` = FLAC,
`tabelle.csv` = ID3, `puffer` = nacktes PCM, `stueck.json` = ADTS) und ein
WAV-Blob in die Datenbank. **Alle sieben müssen gefunden werden**, sonst
zählt das negative Ergebnis oben nicht. Danach wird aufgeräumt und die
Fundzahl gewogen (8 → 1).

### Die schärfere Frage, die die Verzeichnissuche nicht beantwortet

Eine Datei, die im Archivverzeichnis entsteht und gleich wieder verschwindet,
entgeht jeder Suche, die nur den Zustand *nach* dem Lauf kennt. Die
STT-Sonde hat den Pfad im **Moment des Aufrufs** protokolliert, und K4 misst
daran mit: liegt der Audioabschnitt zu irgendeinem Zeitpunkt im
Archivverzeichnis? Echtbaum:

```
WAV-Pfade, die dem STT gereicht wurden: ['<runtime>/ears-215634-1.wav']
   (existierten beim Aufruf: [True], Bytes: [54316])
ok [K4] der Audioabschnitt fuer den STT liegt zu KEINEM Zeitpunkt im Archivverzeichnis
ok [K5] die WAV-Datei fuer den STT ist nach der Runde weg
```

Diese Zusatzmessung ist **aus einem unerkannten Mutanten entstanden**: die
erste Fassung von `rohaudio-bleibt-liegen` legte den Abschnitt ins
Archivverzeichnis, und der `finally`-Zweig der Ohren räumte ihn weg, bevor
die Verzeichnissuche lief. Der Mutant meldete grün. Jetzt greift beides.

### Die Sperre gegen den eigenen Fehler

Nach dem T-7.3-Zwischenfall vom 18.08., 10:16 (zwei echte
Wahrnehmungsdienste gestartet, zwei Fremdeinträge im Archiv des Nutzers):

1. **`systemctl`-Vorschalter im PATH über den ganzen Lauf**, der jede
   `daimon-*`-Unit mit Exit 99 zurückweist und jeden Aufruf protokolliert.
   Nur K7 hängt für die Dauer seines Eingriffs den abbildenden Vorschalter
   davor. Beide Protokolle werden am Ende ausgewertet und ausgegeben.
2. **Vor jedem Eingriff wird gewogen**, dass die geladenen Module wirklich
   aus dem Prüfling stammen (`eingespeist()` prüft `__file__` gegen den
   Prüflingspfad). Tun sie es nicht, bricht das Kriterium ab, statt zu
   messen.
3. **Der Recorder dieses Laufs bekommt `fokus_klasse`/`mikrofone`
   stillgelegt**: seine automatische Pause riefe sonst `systemctl --user
   stop` an den echten Units — genau der Weg des Zwischenfalls.
4. **Eigene Datenbank unter `$(mktemp -d)`**, `XDG_DATA_HOME` umgebogen. Das
   echte Archiv nur `mode=ro`.

`XDG_RUNTIME_DIR` bleibt **unverändert** — der erste Versuch bog es mit um,
und `systemd-run --user` fand daraufhin den Sitzungsbus nicht: K7 hätte
seine transienten Units nicht bekommen und den Stromteil **still
weggelassen**. Das Laufzeitverzeichnis wird stattdessen jedem Beteiligten
ausdrücklich übergeben.

---

## Kann der Verifizierer den Fehler sehen?

**Ja, und es ist ausprobiert.** Zehn Mutanten, aus dem Gut-Muster erzeugt
(`tests/mutants/T-7.4/erzeugen.sh`, von `meta.sh` selbst aufgerufen; jede
Mutation wird gewogen — der Anker muss genau einmal vorkommen). Der echte
Baum ist an zwei Kriterien ohnehin rot, ohne dass jemand etwas kaputtmachen
musste.

```
meta[T-7.4]: Gut-Muster ...
meta[T-7.4]: 10 Mutanten, alle erkannt.
```

| Mutant | geänderte Zeile | zugedacht | tatsächlich rot |
|---|---|---|---|
| `stille-gilt-als-sprache` | `vad.Hysterese.schritt`: `if p >= self.einsatz` → `if True` | K1 | K1, K2, K6 |
| `eintrag-ohne-erkannte-sprache` | `ears/daemon._runde`: archiviert vor der Transkript-Prüfung | K2 | K2 |
| `stt-ohne-leerlauf` | `gpu/stt.lauf`: `srv.settimeout(frist)` → `settimeout(None)` | K3 | K3 |
| `rohaudio-bleibt-liegen` | `ears/daemon._wav_schreiben` → `data_dir()/abschnitt-N.dat`, kein `unlink` | K4 | K4, K5 |
| `verbotene-arten-leer` | `store`: `VERBOTENE_ARTEN` leer **und** `"wav"` in `AUFBEWAHRUNG` | K5 | K5 |
| `audio-parameter-am-melder` | `audio.melde_transkript`: `audio: bytes \| None = None` | K5 | K5 |
| `zweiter-stt-aufruf-fuers-archiv` | `ears/daemon._archivieren`: eigener STT-Aufruf | K6 | K2, K6 |
| `pause-laesst-den-ton-offen` | `pause.PAUSE_UNITS` ohne `EARS_UNIT` | K7 | K7 |
| `melder-schreibt-am-recorder-vorbei` | `melder.senden`: Direktschreibweg bei `kein_recorder` | K8 | K8 |
| `transkript-nicht-tainted` | `store.Archiv._zeile`: `Marked(…, TAINTED)` → roher Text | K9 | K9 |

Die Mitbefunde sind alle erklärt und keiner ist Zufall:

* `stille-gilt-als-sprache`: die Hysterese zerlegt dieselbe Sprach-Einspielung
  in zwei Segmente — zwei STT-Aufrufe (K2/K6) sind die Folge derselben
  Mutation.
* `rohaudio-bleibt-liegen`: die WAV-Datei überlebt die Runde, und genau das
  ist die zweite Hälfte von K5.
* `zweiter-stt-aufruf-fuers-archiv`: der zweite Aufruf fällt schon an der
  Zählung in K2 auf, bevor K6 ihn benennt.

**Zwei Mutanten haben den Prüfstand nachgeschärft, statt ihn zu bestätigen** —
beide waren zunächst *unerkannt*:

1. `verbotene-arten-leer` in der ersten Fassung leerte nur
   `VERBOTENE_ARTEN`. Ohne Wirkung: `AUFBEWAHRUNG` weist `wav` ohnehin ab.
   **Die beiden Sperren sind redundant** — das ist kein Fehler, aber es
   heißt, dass `VERBOTENE_ARTEN` allein nichts trägt. Und mein Kriterium
   prüfte `VERBOTENE_ARTEN` gegen sich selbst: eine leere Liste hätte nichts
   zu prüfen gehabt und wäre grün gewesen. Die Liste der Rohaudio-Arten steht
   jetzt im **Prüfstand**, nicht im Prüfling.
2. `stille-gilt-als-sprache` als Mutation der Konstanten `EINSATZ = 0.5` war
   wirkungslos: die verbindliche Quelle ist `config/daimon.toml`, und die
   liegt außerhalb des Gut-Musters. Der Mutant sitzt jetzt in der
   Zustandsmaschine.

---

## Was ohne `sherpa_onnx` nicht messbar ist — `umgebungs-blockiert`, benannt

`sherpa_onnx` ist auf dieser Maschine nicht installiert:

```
$ python3 -c "import sherpa_onnx"
ModuleNotFoundError: No module named 'sherpa_onnx'

$ python3 -m daimon.gpu.stt --wav …/test_wavs/de.wav
  File "…/daimon/gpu/stt.py", line 197, in laden
    import sherpa_onnx
ModuleNotFoundError: No module named 'sherpa_onnx'
```

**Nicht gemessen, und keine Attrappe hat so getan als ob:**

* **Eine echte Referenzaufnahme durch den echten Erkenner.** Der
  Verifikationsabsatz sagt „spielt eine Referenzaufnahme ein". Dieser Lauf
  spielt ein synthetisches Signal ein und bekommt das Transkript von einer
  Sonde am `stt.sock`. Was damit **nicht** belegt ist: dass `parakeet-tdt`
  aus einem echten Satz einen brauchbaren Text macht — das ist T-3.8s
  Zusage, nicht T-7.4s, aber die Kette ist an dieser Stelle unterbrochen.
* **Der Speicherbedarf des warmen Modells.** K3 misst, ob der Prozess endet,
  nicht wie viel er hält.

**Das Kommando, sobald die Abhängigkeit da ist** (Reihenfolge: erst der
Handlauf, dann der Verifizierer):

```bash
uv pip install sherpa-onnx            # oder: pip install sherpa-onnx
python -m daimon.gpu.stt --wav ~/.local/share/daimon/models/\
sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/test_wavs/de.wav
tests/verify/T-7.4.sh
```

Der Prüfstand braucht die Abhängigkeit **nicht**, um seine neun Kriterien zu
messen — er ersetzt den Modellprozess, nicht den Prüfling.

---

## Das echte Archiv des Nutzers — nur gelesen

Nebenbefund, ohne Kriteriumsrang: der Prüfstand hat dieses Archiv nicht
gefüllt und kann daher nichts beweisen. Die Frage des Tasks hat trotzdem
eine Antwort:

```
echtes Archiv /home/itiger013/.local/share/daimon (NUR GELESEN): 7 Audiofund(e)
    models/…-parakeet-…/test_wavs/{de,en,es,fr}.wav      RIFF/WAVE
    models/…-parakeet-…/encoder.int8.onnx                (Signatur bei Versatz 390073821)
    voices/vits-piper-…/de_DE-thorsten-high.onnx         (Signatur bei Versatz 92393188)
    voices/vits-piper-…/espeak-ng-data/ru_dict           (Signatur bei Versatz 4925474)
```

**`archiv.db`, `archiv.db-wal` und `archiv.db-shm` sind sauber.** Im
Mitschnitt liegt kein Rohaudio.

Die vier `test_wavs` sind echte Audiodateien und liegen tatsächlich unter
`$XDG_DATA_HOME/daimon` — sie gehören zum STT-Modell und sind kein
Mitschnitt. Wörtlich gelesen („im gesamten Archivverzeichnis existiert keine
Audiodatei") wäre die Zusage damit verletzt; sachlich gelesen (Rohaudio aus
dem Mikrofon) nicht. **Empfehlung: Modelle und Stimmen gehören nicht in
dasselbe Verzeichnis wie der Mitschnitt** — nicht wegen dieser Zusage,
sondern weil `data_dir()` in der Sicherung des Nutzers landet und 4 GB
Modelldateien dort nichts verloren haben. Eigener Task, kein Befund an
T-7.4.

Die drei `.onnx`/`_dict`-Treffer sind **falsche Positive** meiner
Signatursuche: ein `OggS`/`ID3` irgendwo tief in einer 400-MB-Binärdatei ist
Zufall. Die Suche meldet lieber zu viel als zu wenig; im Archivverzeichnis
selbst (drei Dateien) kostet das nichts.

---

## Grenzen — was er NICHT misst

**Rohaudio-Formen, die dieser Suche entgehen:**

* **Verschlüsseltes oder komprimiertes Audio ohne Container** — nach jeder
  Kompression sieht PCM wie Zufall aus, und meine zweite Achse
  (Differenz/Betrag) schlägt genau darauf nicht mehr an. Ein `opus`- oder
  `mp3`-Datenstrom **ohne** OggS-/ID3-Kopf entgeht der Suche.
* **Breitbandiges oder verrauschtes PCM.** Die Grenze `Differenz/Betrag <
  0,40` ist gegen echte 16-kHz-Sprache (0,26) und gegen Binärmüll (≥ 0,58)
  gesetzt. PCM mit viel Höhenanteil, sehr leises PCM (Betrag < 200) und
  **Stille als PCM** (nur Nullen) fallen durch.
* **ADTS/MPEG nur am Dateianfang.** Das Synchronwort `0xFF 0xEx` kommt in
  jeder Binärdatei zufällig vor (im WAL der eigenen Datenbank elfmal). Ein
  MP3-Rahmen mitten in einem Blob entgeht der Suche.
* **8-bit-, 24-bit- und 32-bit-Float-PCM.** Erkannt wird 16-bit-LE.
* **Audio außerhalb des Archivverzeichnisses.** Der Task fragt nach dem
  Archivverzeichnis, und danach wird gesucht. Der Prüfstand belegt
  *zusätzlich*, dass die WAV-Datei für den STT dort nicht liegt und nach der
  Runde weg ist — aber sie liegt für die Dauer eines STT-Aufrufs im
  Laufzeitverzeichnis (`%t/daimon`, tmpfs, 0600, 54 KB im gemessenen Lauf).
  Design Z. 384 sagt „nie auf Platte"; tmpfs ist keine Platte, aber es ist
  auch nicht der `mlock`-Ringpuffer, von dem derselbe Satz spricht. **Wer
  den Prozess zwischen `_wav_schreiben` und dem `finally` mit SIGKILL
  trifft, lässt die Datei liegen.** Das ist T-3.15s Pfad, nicht T-7.4s —
  aber es ist die einzige Stelle, an der Rohaudio dieses Systems ein
  Dateisystem berührt, und sie gehört benannt.

**Am Messaufbau:**

* **Der VAD-Detektor ist eingespeist.** `pysilero_vad` fehlt auf dieser
  Maschine; eingespeist ist ein Energie-Detektor an genau der Stelle, an der
  Silero sitzt — er geht in die **echte** `vad.Erkenner` und von dort in die
  **echte** `vad.Hysterese`. Gemessen wird die Segmentierung des Prüflings,
  nicht meine. Was das nicht deckt: ob Silero bei echter Sprache dieselben
  Segmentgrenzen zieht.
* **Die Konfiguration kommt aus dem echten Baum, nicht aus dem Prüfling.**
  `load_config()` findet `config/daimon.toml` des Repos, auch unter
  `DAIMON_FIXTURE`. Eine Mutation in der Konfiguration wäre für diesen
  Verifizierer unsichtbar. (Gefunden, weil ein Mutant daran wirkungslos
  blieb.)
* **K8 stellt die Pause her, indem der Recorder dieses Laufs beendet wird**,
  nicht durch `systemctl stop` an einer echten Unit. Das ist dieselbe
  Wirkung (kein Horcher am `recorder.sock`), aber es ist nicht derselbe Weg.
  Der Weg selbst misst K7.
* **K3 wartet.** Der Messgegenstand ist eine Frist; die Frist steht auf einer
  Sekunde und die Ablesung ist ein einziger `poll()` über drei Prozesse.
  Anders ist „beendet sich nach Leerlauf" nicht zu messen.
* **Der Peer-Unit-Pfad des Recorders ist nicht geprüft.** Der Prüfstand
  fährt mit `erlaubte_units=None` (der dokumentierte Weg ohne systemd);
  `ART_JE_UNIT["daimon-ears.service"] == {transkript}` liegt damit außerhalb
  der Messung. Das ist T-7.1s Zusage („Art gehört zum Absender") und ist
  dort geprüft.
* **Ein Lauf, keine Wiederholung.** Jedes Kriterium ist einmal gemessen. Ein
  seltener Aussetzer im Zusammenspiel Ohren → Sonde → Recorder wäre nicht
  aufgefallen; die Ausgänge waren über rund fünfzehn Läufe (Gut-Muster,
  zehn Mutanten, Echtbaum) stabil.
* **Ein Nebenbefund, den ich nicht verfolgt habe:** eine gelöschte
  Archivzeile lebt im WAL weiter — nach dem `DELETE` der Blob-Positivkontrolle
  fand die Suche das WAV noch in `archiv.db-wal`. Für ein Archiv mit
  Verfallsdatum und Kill-Switch ist das eine eigene Frage (T-7.1/T-6.10),
  nicht T-7.4s.

---

## Was ich empfehle

1. **Befund 1 (K7) ist der dringende.** Er ist derselbe Ursprung wie
   T-7.3/K8, aber er trägt hier eine zweite, schwerere Folge: „Mitschnitt
   pausiert" schaltet das Archiv ab und lässt das Mikrofon offen. Fix in
   `pause.py` **plus** Tonstrom in der Messung von `stoppe()`; ohne den
   zweiten Teil bleibt `ok: true` eine Aussage über Rückgabewerte.
2. **Befund 2 (K3) braucht zuerst eine Entscheidung**, keinen Patch:
   T-7.4 Punkt 2 / Design §5.4 gegen den Modulkopf von T-3.8. Das
   Gut-Muster zeigt Variante (a) in acht Zeilen; welche gilt, ist nicht
   meine Wahl.
3. **Die zentrale Zusage hält.** Rohaudio wird nicht geschrieben — nicht ins
   Verzeichnis, nicht in die Datenbank, nicht als Blob, nicht getarnt. Das
   ist gemessen, mit Positivkontrolle, und die Signatur von
   `melde_transkript` macht den Weg dorthin in einem Diff sichtbar. Von den
   fünf Akzeptanzpunkten sind drei ganz erfüllt, einer (Punkt 2) hängt an
   einer Dokumentenentscheidung, einer (Punkt 4) ist zur Hälfte verletzt.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, NICHT eingefroren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main`, Commit `5c94c3c` |
| Verifizierer unverändert | `T-7.4.sh`, `t74_pruefstand.py` |

**Nicht eingefroren, weil rot** — aber nur noch an einem der beiden Kriterien
von gestern.

### 1. Gegen `main` — K7 ist zu, K3 nicht

```
$ env -u DAIMON_FIXTURE tests/verify/T-7.4.sh; echo $?
ok   [K7] der BILD-Pfad wird gestoppt (daimon-eyes.service)
ok   [K7] der TON-Pfad wird gestoppt (daimon-ears.service)
ok   [K7] Unterscheidungskontrolle: der nur ANGEHALTENE Strom ist noch da --
     'verschwunden' heisst hier geschlossen und nicht still
ok   [K7] der Mikrofonstrom des Ohren-Dienstes ist nach der Pause geschlossen
FAIL [K3] der STT-Prozess laeuft bei Stille WEITER: `lauf()` in
     daimon/gpu/stt.py hat keine Leerlauffrist.
T-7.4: ROT -- 1 von 9 Kriterien rot: K3
1
```

**K7 ist mit `d521148` geschlossen** — derselbe Commit, der T-7.3 B1 behoben
hat. Der Nachweis läuft hier nicht über die Zeilen des Schalters, sondern über
die Ströme, mit der Unterscheidungskontrolle daneben.

### 2. K3 ist kein Versehen, sondern ein Widerspruch im Repo

Das ist der Unterschied zu den vier anderen offenen Befunden dieser Sitzung,
und er gehört benannt: hier fehlt nichts, hier stehen **zwei Fassungen einer
Regel** nebeneinander.

`docs/DESIGN.md:382`:

> Bei anhaltender Sprache bleibt der STT-Arbeitsprozess warm, **bei Stille
> beendet er sich wie gehabt.**

`daimon/gpu/stt.py:24–30`, Abschnittsüberschrift **„Kein Leerlauf-Exit"**:

> Anders als der GPU-Worker aus T-3.7, und aus demselben Grund wie beim TTS:
> dort war die belastbare Größe die VRAM-Rückgabe nach dem Exit. Hier gibt es
> kein VRAM zurückzugeben, und ein Neustart kostet 843 ms Ladezeit. Das Modell
> im Speicher IST die Latenzzusage.

`daimon/gpu/stt.py:384–385` setzt das um: `def lauf(...)` mit dem Docstring
„Kein Leerlauf-Exit — siehe Modulkopf" und einer `while True`-Schleife ohne
Frist.

Beide Fassungen tragen eine Begründung, und beide Begründungen sind für sich
tragfähig. **Was nicht tragfähig ist, ist ihr Nebeneinander:** welche gilt,
entscheidet der Zufall dessen, der zuerst nachschlägt. Genau davor warnt
`CLAUDE.md` Regel 4.

**Fehlerszenario, falls DESIGN §382 gilt:** der STT-Dienst wird beim ersten
Wort socket-aktiviert und läuft danach unbegrenzt weiter — mit
parakeet-tdt-0.6b-v3 im Speicher, bis jemand die Unit stoppt. Die
Residenzpolitik aus §5.4 hat für diesen Dienst keinen Fall, in dem sie greift.
**Fehlerszenario, falls `stt.py` gilt:** DESIGN §382 und Akzeptanzpunkt 2 von
T-7.4 beschreiben Verhalten, das es nicht gibt, und jeder künftige
Verifizierer misst dagegen — so wie dieser.

Dieser Verifizierer misst gegen die Akzeptanzliste. Solange die steht, ist er
zu Recht rot. Die Entscheidung, welche der beiden Fassungen fällt, gehört
nicht in einen Prüfstand.

### 3. Gut-Muster und Mutanten — der Verifizierer ist gesund

```
$ bash tests/verify/meta.sh T-7.4
T-7.4: 10 Mutanten erzeugt.
meta[T-7.4]: Gut-Muster ...
… audio-parameter-am-melder · eintrag-ohne-erkannte-sprache ·
  melder-schreibt-am-recorder-vorbei · pause-laesst-den-ton-offen ·
  rohaudio-bleibt-liegen · stille-gilt-als-sprache · stt-ohne-leerlauf ·
  transkript-nicht-tainted · verbotene-arten-leer ·
  zweiter-stt-aufruf-fuers-archiv — alle erkannt.
meta[T-7.4]: 10 Mutanten, alle erkannt.
```

`stt-ohne-leerlauf` ist der Mutant für K3 und `pause-laesst-den-ton-offen`
der für K7 — beide werden erkannt. Der Verifizierer ist an der reparierten
Achse (K7) also nicht blind geworden.

### 4. Rücksicht auf den laufenden Betrieb

```
SPERRE: 0 Aufruf(e).
SPERRE: kein Aufruf an eine echte Unit.
```

Der `systemctl`-Vorschalter hat keinen Aufruf an eine echte Unit
durchgelassen.
