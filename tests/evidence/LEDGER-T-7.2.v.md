# Ledger T-7.2.v — Verifizierer: Redaktion vor dem Schreiben

**Ausgang 18.08.: `produktdefekt-rot`** — der Befund unten ist mit `ae7c72e`
behoben.
**Ausgang 19.08.: `gruen`, eingefroren.** Siehe §Nachlauf am Ende.

Stand 18.08.: der Verifizierer ist gebaut, gegen das Gut-Muster grün,
gegen alle acht Mutanten rot — und gegen den echten Baum rot, an genau einem Punkt. Die
Redaktion **vor dem Schreiben** hält, was T-7.2 zusagt; die Prüfung **vor dem
Diff** hält es nicht. Der Kanarienvogel kommt nicht auf die Platte, aber das
Fenster wird trotzdem gelesen.

---

## Provenienz

| | |
|---|---|
| Gebaut von | Reviewer-Sitzung vom 2026-08-17, `DAIMON_ROLE=reviewer` |
| Worktree | `/mnt/data/AI/repos/dAImon-reviewer-t72`, Branch `reviewer/p4-T-7.2v` |
| HEAD beim Bau | `33e549680bb5875b2b47bbaa25e1746ad1ce324e` |
| Gelesen, in dieser Reihenfolge | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` Block T-7.2 (Z. 1886–1922, Verifikationsabsatz ab Z. 1908) und Anhang D (Z. 2035 ff., **kennt T-7.2.v nicht**) · `docs/DESIGN.md` §4.4, §7.2, §7.2d · `docs/REVIEWER-UEBERGABE-17.08.md` §1 Punkt 2 und §3 · Formvorlage `tests/verify/T-4.6.sh`, `tests/verify/t46_pruefstand.py`, `tests/mutants/T-4.6/erzeugen.sh`, `tests/verify/meta.sh` |
| Produktquelle gelesen | `daimon/recorder/{redaktion,daemon,store,melder,pause,audio,suche}.py`, `daimon/common/{ipc,config}.py`, `daimon/eyes/{daemon,change,context}.py`, `config/redaktion.yaml` — **nur lesend** |
| Nicht getan | `freeze.sh` **nicht** gerufen (eigener autorisierter Task) · `tests/verify/meta.sh`, `T-4.6.sh`, `t46_pruefstand.py`, `tests/mutants/T-4.6/**` **nicht** angefasst · `role_guard.py` **nicht** repariert (eigener Auftrag) · kein Produktivcode geändert, kein Merge, kein Push |

Neue Dateien:

```
tests/verify/T-7.2.sh
tests/verify/t72_pruefstand.py                    (der Prüfstand, 8 Kriterien)
tests/verify/t72_dienst.py                        (startet den echten Dienst)
tests/fixtures/known-good/T-7.2/                  (272 K, 27 Dateien)
tests/mutants/T-7.2/erzeugen.sh + .gitignore      (8 Bäume, NICHT eingecheckt)
tests/evidence/LEDGER-T-7.2.v.md
```

Die Mutantenbäume entstehen aus dem Gut-Muster und liegen unter
`.gitignore` — acht Kopien wären 4,6 M und beim nächsten Fix eine zweite,
stillschweigend veraltete Fassung desselben Codes. Der Nachweis läuft
zweistufig:

```
bash tests/mutants/T-7.2/erzeugen.sh
bash tests/verify/meta.sh T-7.2
```

---

## Die Prüffrage des Builders

> *Gibt es einen zweiten Weg ins Archiv?*

**Ins Archiv nicht.** Es gibt genau einen `Archiv.schreiben`-Aufruf im ganzen
Produktbaum, er sitzt hinter dem Urteil, und die Art hängt am Absender — alles
drei gemessen (K7). Der Kanarienvogel aus einer gelisteten Anwendung steht
weder in der Datenbank noch im WAL noch in einer Zwischendatei, einem Log oder
einem Temp-Verzeichnis, und `sqlite_sequence` beweist, dass es die Zeile nie
gegeben hat.

**Aber die Frage war zu eng gestellt.** Das Archiv ist nicht der einzige Ort,
an dem ein Passwortmanager landen kann. Der Weg dorthin führt an drei
Denylist-Prüfungen vorbei, und nur die letzte kennt die `.desktop`-Kennung.

---

## Befund: die Liste führt Kennungen, zwei von drei Prüfungen vergleichen Klassen

Akzeptanzliste T-7.2, zweiter und dritter Punkt:

> **Anwendungs-Denylist**: Passwortmanager, Banking und was der Nutzer ergänzt
> werden **gar nicht erfasst**. Die Prüfung sitzt **vor dem Diff und vor dem
> Schreiben**, nicht danach.
>
> Die Zuordnung Anwendung → Denylist geht über die `.desktop`-Kennung, nicht
> über den Fenstertitel.

`config/redaktion.yaml` führt `.desktop`-Kennungen — die Datei sagt es im
eigenen Kopf („Die Eintraege sind `.desktop`-KENNUNGEN"). KWin liefert die
`resource_class`. Zwischen beiden steht ein Nachschlagewerk, und es steht nur
an einer der drei Stellen:

| Stelle | Datei | vergleicht | Wirkung |
|---|---|---|---|
| vor dem Diff | `daimon/eyes/change.py:192` | `fenster.klasse.strip().lower()` roh | Frame wird gelesen, zugeschnitten, **geOCRt** |
| Live-Kontext | `daimon/eyes/context.py:128` | `fenster.strip().lower()` roh | OCR-Text liegt im Quarantäne-Kontextspeicher |
| vor dem Schreiben | `daimon/recorder/redaktion.py:152` | **`desktop_kennungen()`**, dann die Liste | Archiveintrag fällt weg |

Für jede gelistete Anwendung, deren Fensterklasse nicht zufällig gleich ihrer
Kennung ist, gilt damit: **der Bildschirm wird gelesen und in den Live-Kontext
gelegt, und nur der Archiveintrag fällt weg.** „Gar nicht erfasst" ist das
nicht. Gemessen:

```
FAIL K8: die Gatterkette VOR DEM DIFF sperrt dieselbe Anwendung:
         grund='kein_text' -- sie vergleicht die rohe Fensterklasse und kennt
         die .desktop-Zuordnung nicht, die Akzeptanzpunkt 3 verlangt
FAIL K8: der Quarantaene-Kontextspeicher sperrt dieselbe Anwendung
```

`grund='kein_text'` ist der Beleg, nicht die Nebensache: der Befund kommt aus
der Regionenerkennung, also **hinter** dem Diff. Das Fenster ist die ganze
Kette durchgelaufen. Daneben steht die Positivkontrolle: dieselbe Kette weist
eine **wörtlich** gelistete Klasse mit `grund='denylist'` ab — die Messung
kann sehen, wenn gesperrt wird.

Der Riss ist heute **strukturell und nicht instanziiert**: von den fünfzehn
Einträgen der Liste ist auf dieser Maschine keiner installiert, gemessen mit
`desktop_kennungen()` gegen die Liste (Ergebnis: leere Menge). Er wird live,
sobald jemand eine Anwendung installiert, die nur in einer der beiden Formen
gelistet ist — `org.kde.kmymoney`, `org.gnome.World.Secrets` und
`com.bitwarden.desktop` stehen ohne ihre üblichen Fensterklassen in der Datei,
`hibiscus`, `moneyplex` und `gnucash` ohne ihre Kennungen. Der Verifizierer
stellt den Fall deshalb synthetisch her: eine `.desktop`-Datei mit
`StartupWMClass=tarnkappe` unter einem eigenen `XDG_DATA_HOME`.

Das ist derselbe Fehlertyp, den `config/redaktion.yaml` schon einmal in die
andere Richtung hatte (Kommentar in `eyes/daemon.py:68`: „Bis T-7.2 standen
hier fuenf Eintraege und dort sechzehn"). Damals liefen die **Listen**
auseinander; jetzt läuft die **Zuordnung** auseinander. Zwei Fassungen einer
Regel sind eine Regel und eine Attrappe — geprüft war erfahrungsgemäß die
andere.

**Reparaturort und -umfang** (Vorschlag, im Gut-Muster laufend belegt, im
Arbeitsbaum ungeprüft): `daimon/eyes/daemon.py::_denylist_aus_datei()` nimmt
zusätzlich jede Fensterklasse auf, die über ihre `.desktop`-Datei auf eine
gelistete Kennung zeigt — zwölf Zeilen, **eine** Stelle, beide Leser bedient.
Genau das ist der einzige Unterschied zwischen Gut-Muster und Arbeitsbaum.
Die Messung schreibt den Ort **nicht** vor: sie holt die Liste dort, wo der
Augendienst sie seinen beiden Vorrichtungen gibt (`_denylist_aus_datei()`).
Wer die Auflösung stattdessen in `Kette` und `Kontextspeicher` legt, wird
ebenso grün.

Reparieren darf diese Sitzung nicht — `daimon/**` ist für `reviewer` gesperrt.
Der Befund geht an den Builder, der Test bleibt rot.

---

## Was grün ist, und woran

85 Prüfungen, acht Kriterien, jedes einzeln abgerechnet, ohne
`&&`-Verkettung. Ein Kriterium ohne eine einzige Messung zählt in der Bilanz
**als rot**.

| | Kriterium (Akzeptanzpunkt) | Prüfungen | gemessen woran |
|---|---|---|---|
| K1 | Redaktion läuft **vor** dem Schreiben, nicht als Nachbearbeitung (1) | 4 | `sqlite_sequence` steht still — die Tabelle trägt `AUTOINCREMENT`, eine geschriebene und wieder gelöschte Zeile ließe den Zähler stehen, wo sie war. Dazu Größe, `st_mtime_ns` und sha256 von `archiv.db` **und** `archiv.db-wal` |
| K2 | Denylist: gar nicht erfasst — auch nicht in Zwischendatei, Log, Temp (2) | 9 | Byte-Streifzug über **jede Datei** des Laufbaums (Datenbank, WAL, Laufzeitverzeichnis, Zustand, Cache, `TMPDIR`) **und** die Prozessausgabe des Dienstes; Positivkontrolle mit **derselben** Zeichenkette aus einer nicht gelisteten Anwendung |
| K3 | Zuordnung über die `.desktop`-Kennung, nicht über den Titel (3) | 20 | Klasse nur über `StartupWMClass` gelistet → gesperrt; ähnliche Klasse ohne `.desktop`-Eintrag → kommt an; gelistetes Programm mit fremdem Titel bleibt gesperrt; harmloses Programm mit gelistetem Namen im Titel bleibt erfasst; Fenster ohne Kennung → gesperrt |
| K4 | DRM nach §4.4 greift **zusätzlich** (4) | 8 | `drm=True` an einer **nicht** gelisteten Anwendung sperrt; dieselbe Anwendung ohne DRM kommt an |
| K5 | Zeitlich begrenzter Privatmodus schreibt nichts (5) | 11 | Datenbank unverändert (Zeilenzahl **und** Dateizeitstempel **und** sha256); die Datei liegt 0600 im Laufzeitverzeichnis; **nach Ablauf** wird dieselbe Probe wieder geschrieben — das ist die Positivkontrolle und zugleich der Beleg für „zeitlich begrenzt" |
| K6 | Wahrnehmung aus → alles `transient` (6, §7.2d) | 9 | am Lebenszeichen der **Augen**, das der Dienst selbst auswertet: gelöscht → gesperrt, frisch → geschrieben. Beide Male über den 5-s-Zwischenspeicher der Redaktion hinweg gewartet |
| K7 | Kein zweiter Weg ins Archiv (Prüffrage) | 16 | genau **ein** `Archiv.schreiben` im Produktbaum, und es sitzt hinter dem Urteil; jedes `INSERT INTO archiv` steht im Archivmodul; der Augendienst kann seinen Text **nicht** als `transkript` deklarieren (das ginge an `urteil_ton` vorbei, das weder Denylist noch DRM kennt); der Fokusdienst kann kein OCR melden; Positivkontrolle: der Ohren-Dienst darf sein Transkript |
| K8 | Die Prüfung **vor dem Diff** urteilt wie die vor dem Schreiben (2+3) | 8 | `eyes.change.Kette`, `eyes.context.Kontextspeicher` und `recorder.redaktion.Redaktion` gegen dieselbe Anwendung — **2 rot**, siehe Befund |

Gemessen wird an einem **echten Dienstprozess**: `t72_dienst.py` ruft
`daimon.recorder.daemon.main()`, also die Verdrahtung des Betriebs — dieselbe
Denylist aus `denylist_laden(denylist_pfade())`, dieselbe `Redaktion`,
dieselbe Wahrnehmungsmessung, dasselbe `Archiv`. Gemeldet wird über den
echten Socket mit dem echten Melder (`melde_ocr`, `melde_titel`). Der
Prüfstand baut nichts davon nach; er würde sonst seine eigene Verdrahtung
prüfen.

---

## Die drei Auflagen dieses Auftrags, und wie sie eingelöst sind

### 1. Kanarienvogel plus Positivkontrolle

Jede Negativmessung hat ihre Positivkontrolle, und zwar mit **derselben
Zeichenkette** — so verlangt es der Verifikationsabsatz. Die Reihenfolge ist
Absicht: erst die Sperre und der Streifzug, dann die Kontrolle. Umgekehrt läge
die Zeichenkette schon in der Datenbank und der Streifzug wäre nicht mehr
auswertbar.

```
gelistete Anwendung          → nirgends im Baum, Zeilenzahl 0→0, seq 0→0
Positivkontrolle, dieselbe   → id 1, Zeilenzahl 0→1, seq 0→1,
Zeichenkette, nicht gelistet   Streifzug findet sie in archiv/archiv.db-wal
```

Dazu eigene Positivkontrollen je Kriterium: die Quellensuche findet überhaupt
Dateien · eine ähnliche Klasse ohne `.desktop`-Eintrag kommt durch (die Sperre
kam aus der Karte, nicht aus der Zeichenkette) · dieselbe Anwendung ohne DRM
kommt an · nach Ablauf des Privatmodus wird wieder geschrieben · der
Ohren-Dienst darf sein Transkript (sonst wäre „abgewiesen" nicht von
„Transport kaputt" zu unterscheiden) · die Gatterkette weist eine wörtlich
gelistete Klasse ab (sonst wäre „sperrt nicht" nicht von „misst nichts" zu
unterscheiden).

**Selbstauskunft ist kein Beleg.** Der `grund` in der Antwort des Dienstes
wird geprüft, aber ausdrücklich als Diagnose — der Beleg ist der Zustand der
Platte. Der Mutant `art-nicht-an-unit-gebunden` zeigt, warum: dort meldet der
Dienst `{'ok': True, 'id': 5}` statt `art_nicht_erlaubt`, und rot wird er an
der Zeile im WAL.

### 2. Jede Manipulation wird gewogen

`manipulieren()` vergleicht sha256 vor und nach dem Eingriff und bricht laut
ab, wenn sie gleich bleibt — die Waage aus `t46_pruefstand.py`, unverändert
übernommen. Sie hat in diesem Lauf **nichts** zu tun gehabt: dieser Prüfstand
verändert keine Produktdatei, er misst Zustände. Die Gegenprobe zu „hat der
Eingriff gewirkt" trägt hier die Mutantenerzeugung: `erzeugen.sh` bricht ab,
wenn ein Mutationsanker nicht **genau einmal** im Gut-Muster vorkommt. Ein
Mutant, der nichts geändert hätte, entsteht gar nicht erst.

### 3. Eine Messung ist ein Zeitpunkt, kein Zeitfenster

Kein `--since`, kein Fenster gegen ein zweites. Bezugspunkt jeder Messung ist
die **Antwort des Dienstes** auf genau die Zeile, die er gerade bearbeitet hat
— das Archiv steht im Autocommit, nach der Antwort ist geschrieben oder eben
nicht. Daran hängen fünf feste Größen: Zeilenzahl, `sqlite_sequence`,
Dateigröße, `st_mtime_ns` und sha256, für `archiv.db` und `archiv.db-wal`.

`archiv.db-shm` bleibt aus dem Vergleich draußen und ist der einzige Verzicht:
das ist der gemeinsame WAL-Index, den auch ein **Leser** anfasst. Was ein
Schreibvorgang bewegt, steht in `-wal`. Gesucht wird der Kanarienvogel
trotzdem auch dort — der Streifzug liest jede Datei.

Die zwei Wartezeiten in K6 (je 5,4 s) sind keine Fenster, sondern das
Überholen zweier bekannter Fristen: `HERZSCHLAG_FRIST_S = 5,0` und
`WAHRNEHMUNG_CACHE_S = 5,0`. Während der Wartezeit wird das Lebenszeichen
weitergeschrieben; gemessen wird danach an einer einzelnen Sonde.

---

## Welche Zeile im Produktivcode müsste kaputt sein — und ausprobiert?

Die Auflage aus dem Auftrag, beantwortet, mit Lauf. Acht Mutanten, jeder
**eine** gebrochene Zeile im Gut-Muster, jeder gemessen:

| Kriterium | gebrochene Zeile | Ergebnis |
|---|---|---|
| K2 | `redaktion.py`: `if k.lower() in self.denylist:` → `if False:` | `denylist-greift-nicht`: **K2 4 rot** (dazu K1 1, K3 8, K8 2 — jede Sperre, die über die Liste läuft) |
| K3 | `redaktion.py`: `return self.kennungen.get(roh.lower(), roh)` → `return roh` | `kennung-ueber-rohklasse`: **K3 4 rot** (+ K8 1, die Vorbedingung) |
| K3 | `redaktion.py`: `if not k: return Urteil(STUFE_TRANSIENT, GRUND_UNBEKANNT)` → `return Urteil(stufe)` | `unbekanntes-fenster-durchgelassen`: **K3 4 rot**, sonst nichts |
| K4 | `redaktion.py`: `if drm:` → `if False:` | `drm-ignoriert`: **K4 4 rot**, sonst nichts |
| K5 | `redaktion.py`: `if self._uhr() < privat_bis(...)` (in `urteil`) → `if False:` | `privatmodus-ohne-wirkung`: **K5 4 rot**, sonst nichts |
| K6 | `redaktion.py`: `if not self.wahrnehmung_an():` → `if False:` | `wahrnehmung-egal`: **K6 4 rot**, sonst nichts |
| K1/K2/K7 | `daemon.py`: ein zweiter `self.archiv.schreiben(...)` **vor** dem Urteil (die Screenpipe-Reihenfolge, die der Task ausschließt) | `rohkopie-vor-der-redaktion`: **K1 2, K2 5, K7 3 rot** — und jede andere Sperre dazu |
| K7 | `daemon.py`: `if unit is not None and art not in ART_JE_UNIT...` → `if False:` | `art-nicht-an-unit-gebunden`: **K7 8 rot**, sonst nichts |
| K8 | die zwölf Zeilen `_klassen_zu_kennungen` aus dem Gut-Muster entfernt (= der Arbeitsbaum) | **K8 2 rot** — der Befund oben |

Jedes der acht Kriterien wird in mindestens einem Lauf rot. Kein Kriterium ist
blind. Die sechs schmalen Mutanten fallen **ausschließlich** an ihrem eigenen
Kriterium; die zwei breiten (`denylist-greift-nicht`,
`rohkopie-vor-der-redaktion`) reißen naturgemäß mit, was hinter ihnen liegt —
das steht in der Zuordnung im Prüfstandkopf und wird bei jedem Lauf mit
ausgegeben.

---

## Belege (Befehl + Ausgabe)

```
$ git rev-parse HEAD
33e549680bb5875b2b47bbaa25e1746ad1ce324e

$ bash tests/verify/verify-frozen.sh
verify-frozen: 37 eingefrorene Dateien unveraendert; Abhaengigkeiten geschlossen.
EXIT 0

$ bash tests/mutants/T-7.2/erzeugen.sh
T-7.2: 8 Mutanten erzeugt.

$ bash tests/verify/meta.sh T-7.2
meta[T-7.2]: Gut-Muster ...
meta[T-7.2]: Mutante 'art-nicht-an-unit-gebunden' erkannt.
meta[T-7.2]: Mutante 'denylist-greift-nicht' erkannt.
meta[T-7.2]: Mutante 'drm-ignoriert' erkannt.
meta[T-7.2]: Mutante 'kennung-ueber-rohklasse' erkannt.
meta[T-7.2]: Mutante 'privatmodus-ohne-wirkung' erkannt.
meta[T-7.2]: Mutante 'rohkopie-vor-der-redaktion' erkannt.
meta[T-7.2]: Mutante 'unbekanntes-fenster-durchgelassen' erkannt.
meta[T-7.2]: Mutante 'wahrnehmung-egal' erkannt.
meta[T-7.2]: 8 Mutanten, alle erkannt.
META EXIT 0

$ DAIMON_FIXTURE=$PWD/tests/fixtures/known-good/T-7.2 bash tests/verify/T-7.2.sh
K1: 4 Pruefungen, 0 rot        K5: 11 Pruefungen, 0 rot
K2: 9 Pruefungen, 0 rot        K6: 9 Pruefungen, 0 rot
K3: 20 Pruefungen, 0 rot       K7: 16 Pruefungen, 0 rot
K4: 8 Pruefungen, 0 rot        K8: 8 Pruefungen, 0 rot
EXIT 0

$ bash tests/verify/T-7.2.sh                       # der echte Baum
FAIL K8: die Gatterkette VOR DEM DIFF sperrt dieselbe Anwendung:
         grund='kein_text' -- ...
FAIL K8: der Quarantaene-Kontextspeicher sperrt dieselbe Anwendung ...
K1: 4 Pruefungen, 0 rot        K5: 11 Pruefungen, 0 rot
K2: 9 Pruefungen, 0 rot        K6: 9 Pruefungen, 0 rot
K3: 20 Pruefungen, 0 rot       K7: 16 Pruefungen, 0 rot
K4: 8 Pruefungen, 0 rot        K8: 8 Pruefungen, 2 rot
EXIT 1

$ pgrep -af "[t]72_dienst"; ls -d /tmp/t72*       # nach allen Läufen
(kein Treffer, kein Verzeichnis geblieben)

$ systemctl --user is-active daimon-eyes.service daimon-recorder.service
active
active                                             # vor wie nach den Läufen
```

---

## Grenzen dieses Verifizierers — ehrlich, damit ihn niemand falsch liest

1. **Die Auflösung der Peer-Unit ist NICHT gemessen.** `ipc.accept` bestimmt
   die Gegenstelle über `SO_PEERPIDFD` und den cgroup-Pfad; ein Prüflauf läuft
   in der Sitzungs-Scope des Nutzers und trägt keine der drei erlaubten Units.
   `daimon-eyes.service` **läuft** auf dieser Maschine — eine transiente Unit
   desselben Namens wäre ein Eingriff in den Betrieb des Nutzers und ist
   deshalb unterblieben. `t72_dienst.py` ruft das echte `accept` (uid- und
   pidfd-Prüfung bleiben) und **setzt** danach die Unit aus einer Datei. Damit
   läuft die Art-je-Unit-Tabelle echt (K7 misst sie), aber wer die Unit ist,
   glaubt der Dienst dem Prüfstand. Das gehört T-7.1.v.
2. **Die automatische Pause (T-7.3) ist im Prüflauf stillgelegt.**
   `Recorder.start()` fährt sie einmal vor dem Horchen; sie ruft bei einem
   Treffer `systemctl --user stop daimon-recorder.service daimon-eyes.service`
   — an den echten Units dieser Maschine. `fremde_mikrofonstroeme`, `stoppe`
   und `ist_konferenz` sind ersetzt; `stoppe` wirft, falls sie doch gerufen
   wird. Ihr Verifizierer ist T-7.3.v.
3. **Die Quelle der DRM-Flagge ist nicht gemessen**, nur ihre Wirkung. Dass
   `excludeFromCapture` aus KWin bis in `melde_ocr(drm=…)` durchkommt, ist die
   Naht aus `ab922d7` und gehört zu T-7.1b/T-5.5 — hier steht die Flagge in
   der Nachricht, weil der Melder sie so trägt.
4. **Der Tonpfad kennt die Anwendungs-Denylist nicht, und das ist Absicht** —
   `urteil_ton()` begründet es (ein gesprochener Satz hat kein Fenster).
   Gemessen und **nicht** rot gewertet: ein Transkript des Ohren-Dienstes wird
   auch dann abgelegt, wenn ein gelisteter Passwortmanager vorn ist. Wer ein
   Passwort diktiert, hat es im Archiv. Das ist eine Design-Frage, keine
   Abweichung von der Akzeptanzliste von T-7.2 — aber sie gehört jemandem
   gestellt.
5. **Die Stufe kommt weiter aus der Nachricht.** `daemon.py:176` liest
   `stufe` aus dem Zulauf; ein Absender kann `full` verlangen. Heute folgenlos
   (`redacted` und `full` legen denselben Text ab, und `melder.py` setzt das
   Feld nie), aber es ist dieselbe Klasse Riss, die der Builder gerade für
   `art` geschlossen hat: „die Art gehört zum Absender, nicht zur Nachricht"
   gilt für die Stufe noch nicht. Nicht Teil der sechs Akzeptanzpunkte, daher
   nicht rot gewertet.
6. **Der Privatmodus hat eine Vorrichtung und keinen Zulauf.** K5 misst, dass
   `privat_setzen()` wirkt — nicht, dass ihn im Betrieb heute jemand aufruft.
   Das tut niemand; `tests/test_gate_zulauf.py` bewacht es als Wächter, und
   ein Wächter ist keine Zusage (Übergabe §3).
7. **`desktop_kennungen()` ist im Prüflauf gegen ein eigenes `XDG_DATA_HOME`
   gemessen.** Im Betrieb läuft der Recorder mit `ProtectHome=tmpfs`; ob er
   `~/.local/share/applications` überhaupt sieht, ist hier **nicht** gemessen.
   Sieht er sie nicht, greift die Zuordnung im Betrieb schwächer als im
   Prüflauf — die Sperre fällt dann auf den Vergleich mit der rohen Klasse
   zurück. Das braucht einen Lauf in der echten Unit und gehört zu T-7.1.v.
8. **Der Streifzug sucht UTF-8-Bytes.** Eine Kopie in anderer Kodierung,
   komprimiert oder verschlüsselt fände er nicht. Für die drei Wege, die der
   Plan nennt (Zwischendatei, Log, Temp), reicht es; für einen absichtlich
   versteckenden Schreiber nicht — den deckt K7 statisch ab, nicht dynamisch.
9. **Ein Prozess, kein Neustart.** Dass der Privatmodus einen Neustart nicht
   überlebt (so der Kommentar in `redaktion.py`), ist nicht gemessen: im
   Prüflauf ist das Laufzeitverzeichnis ein temporäres Verzeichnis, im Betrieb
   räumt `RuntimeDirectory=` es weg.
10. **Frames sind nicht gemessen** — es gibt keinen Produzenten (T-7.1, der
    Plan sagt es selbst).
11. **Nicht eingefroren.** `freeze.sh` bleibt ungerufen; die Freeze-Erweiterung
    ist laut `PLAN.md` ein eigener, einzeln autorisierter Task. `T-7.2.sh`,
    `t72_pruefstand.py` und `t72_dienst.py` sind damit noch nicht gegen
    Änderung geschützt — und `t72_dienst.py` wäre beim Einfrieren als Helfer zu
    deklarieren.

---

## Was der Builder als Nächstes tun kann

* Die zwölf Zeilen `_klassen_zu_kennungen` aus dem Gut-Muster übernehmen (oder
  die Auflösung in `Kette` und `Kontextspeicher` legen — die Messung nimmt
  beides). Danach ist dieser Verifizierer gegen den echten Baum grün, **ohne
  dass er angefasst werden muss**; das ist die Probe darauf, dass er das
  Kriterium prüft und nicht eine Implementierung.
* Grenze 4 entscheiden: soll die Denylist den Tonpfad sperren, wenn eine
  gelistete Anwendung vorn ist? Heute tut sie es nicht, mit Begründung — die
  Akzeptanzliste sagt „werden gar nicht erfasst" und meint Fenster.
* Grenze 5 nachziehen: `stufe` gehört zum Absender wie `art`, oder die
  Akzeptanzliste sagt ausdrücklich, dass sie es nicht tut.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, Einfrieren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main` |
| Ausgangs-Commit | `0d62ec5` (nach dem Einfrieren von T-7.1) |
| Verifizierer unverändert | `T-7.2.sh` `3e2d943a…`, `t72_pruefstand.py` `7badf985…`, `t72_dienst.py` `1f632d5b…` |

Der uncommittete `FROZEN`-Eintrag der abgebrochenen Sitzung war kein Beleg.
Zurückgenommen, von vorn gemessen.

### 1. Gegen `main` — grün

```
$ env -u DAIMON_FIXTURE tests/verify/T-7.2.sh; echo $?
Bilanz T-7.2:
K1:  4 Pruefungen, 0 rot    K5: 11 Pruefungen, 0 rot
K2:  9 Pruefungen, 0 rot    K6:  9 Pruefungen, 0 rot
K3: 20 Pruefungen, 0 rot    K7: 16 Pruefungen, 0 rot
K4:  8 Pruefungen, 0 rot    K8:  8 Pruefungen, 0 rot
0
```

85 Prüfungen, keine rot. K8 war am 18.08. mit zwei roten Prüfungen der
Befund: die Denylist entschied an drei Stellen, und nur eine löste die
`.desktop`-Kennung auf.

**Der Fix ist gelesen, nicht nur bestanden.** `ae7c72e` hat keine zwei
Vergleiche nachgezogen, sondern die Entscheidung an **eine** Stelle gelegt —
`recorder/redaktion.py::gesperrt()`. Die beiden anderen Aufrufer
(`eyes/change.py`, `eyes/context.py`) rufen sie jetzt. Das ist genau die
Bauform, die CLAUDE.md Regel 4 verlangt, und nicht ein dritter Vergleich
neben zwei anderen. Der Commit hält zusätzlich fest, dass ein erster Anlauf
(`kennungen or {}`) still die schwächere Prüfung ergab und vom Verifizierer
gefangen wurde.

### 2. Gegen das Gut-Muster und alle acht Mutanten

```
$ bash tests/verify/meta.sh T-7.2
T-7.2: 8 Mutanten erzeugt.
meta[T-7.2]: Gut-Muster ...
… art-nicht-an-unit-gebunden · denylist-greift-nicht · drm-ignoriert ·
  kennung-ueber-rohklasse · privatmodus-ohne-wirkung ·
  rohkopie-vor-der-redaktion · unbekanntes-fenster-durchgelassen ·
  wahrnehmung-egal — alle erkannt.
meta[T-7.2]: 8 Mutanten, alle erkannt.
```

`kennung-ueber-rohklasse` ist der Mutant auf der reparierten Achse: er stellt
den rohen Klassenvergleich wieder her und wird erkannt.

### 3. Betriebslage — was hier NICHT gilt

Der laufende `daimon-eyes.service` ist seit `Wed 2026-08-19 15:15:02 CEST`
aktiv, also **nach** `ae7c72e` (18.08. 21:50). Der laufende Prozess trägt die
eine Fassung der Regel.

Die Grenze aus dem Commit gilt unverändert und ist heute nicht neu gemessen:
**der Tonpfad kennt die Denylist nicht.** Seine einzige Sperre ist der
Privatmodus — und der laufende `daimon-hub.service` (seit 12:02:37) ist
**älter** als `91e59aa` (13:00), der den Privatmodus überhaupt erst
einschaltbar gemacht hat. Im Repo ist der Weg belegt, im laufenden Prozess
ist er es heute nicht. Siehe Bericht der Sitzung, Befund 2.
