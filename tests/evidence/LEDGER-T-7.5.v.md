# Ledger T-7.5.v — Verifizierer: Archivsuche mit Deklassifizierung

**Ausgang 18.08.: `gruen`** — als einziger der elf ohne Produktbefund.
**Ausgang 19.08.: `gruen`, eingefroren.** Siehe §Nachlauf am Ende.

Stand 18.08.: der Verifizierer ist gebaut, gegen das Gut-Muster grün,
gegen alle elf Mutanten rot — und gegen den echten Baum grün. Die
zentrale Prüffrage dieses Auftrags ist beantwortet: **nein**, ein Archivtreffer erreicht das Modell nicht ohne
frische Rundenmarke; und mit Marke kommt **nur der Treffer**, nicht seine
Umgebung. Beides ist an der Naht gemessen, nicht am Stück.

Das Gut-Muster ist **bytegleich mit dem Arbeitsbaum**. Dass dieser Verifizierer
trotzdem etwas misst, steht nicht in ihm, sondern in den elf Mutantenbäumen —
jeder mit genau einer gebrochenen Zeile, jeder zurückgewiesen, und **jedes der
acht Kriterien wird in mindestens einem Lauf rot**.

---

## Provenienz

| | |
|---|---|
| Gebaut von | Reviewer-Sitzung vom 2026-08-18, `DAIMON_ROLE=reviewer` |
| Worktree | `/mnt/data/AI/repos/dAImon-t75`, Branch `reviewer/p4-T-7.5v` |
| HEAD beim Bau | `5724d23471d524ea7a8fc877c53dcda9c1f18ece` |
| Gelesen, in dieser Reihenfolge | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` Block **T-7.5** (Z. 1969 ff., fünf Akzeptanzpunkte + Verifikationsabsatz) und Gate P7 · `docs/DESIGN.md` §1.1 (Nicht-Ziel „Automatisches Durchsuchen des Archivs durch das Modell") und §5.2 (Senkentabelle) · `docs/REVIEWER-UEBERGABE-17.08.md` §1 (Zeile „Archivsuche nur mit Schein", `6138e00`, „pytest, beide Ausgänge") · `tests/verify/T-5.9.sh` + `t59_pruefstand.py` + `t59_hub.py` und `tests/evidence/LEDGER-T-5.9.v.md` (dieselbe Vertrauensgrenze von der anderen Seite) · `tests/mutants/T-5.9/erzeugen.sh`, `tests/verify/meta.sh` · `tests/verify/T-7.2.sh` als zweite Formvorlage |
| Produktquelle gelesen | `daimon/recorder/{suche,store}.py`, `daimon/hub/{declassify,daemon,marks}.py`, `daimon/mind/{router,answer,proactive}.py`, `daimon/eyes/context.py`, `daimon/common/{protocol,taint,config}.py` — **nur lesend** |
| Nicht getan | `freeze.sh` **nicht** gerufen (eigener autorisierter Task) · `.claude/hooks/**` **nicht** angefasst · `T-4.4.*`, `T-4.6.*`, `T-5.9.*`, `T-7.2.*`, `meta.sh` **nicht** angefasst · kein Produktivcode geändert, kein Merge, kein Push · **kein `systemctl`**, keine transiente Unit erzeugt, kein Dienst angehalten |

Neue Dateien:

```
tests/verify/T-7.5.sh
tests/verify/t75_pruefstand.py                    (der Prüfstand, 8 Kriterien)
tests/verify/t75_hub.py                           (startet den echten Hub)
tests/fixtures/known-good/T-7.5/                  (468 K Bytes, 41 Dateien)
tests/mutants/T-7.5/erzeugen.sh + .gitignore      (11 Bäume, NICHT eingecheckt)
tests/evidence/LEDGER-T-7.5.v.md
```

Der Nachweis läuft zweistufig; `meta.sh` ruft den Erzeuger selbst auf —
geprüft, nicht angenommen (im Lauf unten steht die Zeile „Mutanten werden
erzeugt"):

```
bash tests/verify/meta.sh T-7.5
```

---

## Die zentrale Prüffrage

> *Erreicht ein Archivtreffer das Modell ohne frische Rundenmarke — und wenn er
> sie hat, kommt dann nur der Treffer oder auch seine Umgebung?*

**Nein, und nur der Treffer.** Gemessen an der Naht: sieben Kanarienvögel gehen
mit dem *echten* `Archiv` in eine *echte* SQLite-Datenbank, eine *echte*
Rundenmarke entsteht über den *echten* `auth.sock` des *echten* Hubs, gefragt
wird über `kontext.sock` — der Weg, den `Router._api` im Betrieb nimmt.
Gesucht wird in den **rohen Antwortbytes**; das ist, was der Mind bekäme.

```
r1  Zeitfrage mit Bildschirmbezug, KEINE Marke   -> ok:false grund:keine_marke
r2  Marke M1, KEIN Bildschirmbezug               -> ok:false grund:kein_bildschirmbezug
                                                    M1 bleibt offen
r3  Marke M1, Bildschirmbezug, KEIN Zeitbezug    -> ok:true, Live-Kanari drin,
                                                    Archivfeld LEER, kein
                                                    `archiv` im Umfang
r4  Marke M2, Bildschirmbezug UND Zeitbezug      -> ok:true, Archivtreffer drin,
                                                    Nachbarn NICHT, umfang.archiv=1
r5  dieselbe Frage sofort noch einmal            -> ok:false grund:keine_marke
```

In r1, r2, r3 und r5 steht der Archivtreffer **nicht** in den Antwortbytes; in
r4 steht er drin, und zwar allein. Ohne r4 wäre keine der vier Sperren
auswertbar.

**Die Folge ist so gelegt, dass am Ende keine Marke mehr offen ist.** Das ist
keine Kosmetik, sondern ein Befund aus dem Bau: `MarkenBuch` führt mehrere
offene Runden zugleich, und `aktuelle()` nimmt die jüngste gültige. Die erste
Fassung dieses Prüfstands gab vor r2, r3 und r4 je eine neue Marke aus; die von
r2 überlebte (sie gehört dem, was gelingt) und bediente dann r5 — die Sperre
„eine Marke gilt einmal" war ungemessen und meldete trotzdem einen Befund. Die
Folge oben nutzt für r2 und r3 **dieselbe** Marke und misst dabei gleich mit,
dass r2 sie nicht verbrannt hat (Diagnosezähler `rundenmarke.eingelöst`, vorher
und nachher).

### Die zwei, die leicht untergehen

**Proaktives Verhalten sieht das Archiv nicht** (K6) — gemessen **an der
Datenbank**, wie der Plan es verlangt, nicht am Router und nicht an einem
Zähler des Prüflings: ein `sys.addaudithook` auf `sqlite3.connect` sitzt
*unterhalb* des Produktcodes und sieht jede Öffnung der Archivdatei, gleich
welcher Weg sie ausgelöst hat. Ein proaktiver Aufruf **mit** gültiger Marke,
perfektem Bildschirm- **und** Zeitbezug lässt die Datei ungeöffnet; derselbe
Aufruf ohne `proaktiv` öffnet sie. Daneben läuft ein echter proaktiver Anlass
(`Proaktiv.melden("agent_wartet", …)`) durch — mit der Positivkontrolle, dass
er überhaupt etwas entschieden hat.

**Ein Treffer bleibt `tainted`** (K5) — nachgewiesen an der echten
Senkentabelle aus T-3.13b und nicht an einem Feldwert: `pruefe_senke` sperrt
ihn gegen `durchgang1` **und** gegen `tts_ungefragt` (ein per OCR erfasstes
Passwort wird nicht vorgelesen) und lässt ihn in `durchgang2`.

---

## Was grün ist, und woran

**110 Prüfungen, acht Kriterien**, jedes einzeln abgerechnet, ohne
`&&`-Verkettung. Ein Kriterium ohne eine einzige Messung zählt in der Bilanz
**als rot**.

| | Kriterium (Quelle) | Prüfungen | gemessen woran |
|---|---|---|---|
| K1 | Volltextsuche über OCR-Text, Fenstertitel und Transkripte (Akzeptanz 1) | 8 | drei Arten, drei eigene Wortmarken, drei eigene Runden — **durch das echte Gate**, nicht an `Archivsuche` vorbei: die Suche kommt laut Akzeptanz 2 nur dort heraus. Der Titeltreffer trägt seine Art und ist **nicht** der OCR-Treffer; der Umfang nennt je genau einen |
| K2 | Ohne frische Rundenmarke erreicht kein Archivtreffer das Modell (Akzeptanz 2, Verifikationsabsatz) | 20 | Naht über `kontext.sock`: Hub A weist die fremde Unit ab (bei lebendem `state.sock`), Hub B antwortet; r1 ohne Marke und r5 mit verbrauchter Marke → `keine_marke`, weder Treffer noch Umgebung noch Live-Kontext in den Antwortbytes. Dazu **das Archiv als zweite Tür, aktiv angegriffen**: acht Leseversuche an `Archivsuche.freigeben` ohne echten Schein (`None`, `True`, `1`, nacktes Objekt, `SimpleNamespace(turn_id="x")` — der Fehler vom 16.08. —, `dict`, Schein mit leerer `turn_id`, gleichnamige Attrappe) scheitern alle; Positivkontrolle: der echte Schein öffnet. Und die Journal-Attrappe hat den Anker abgefangen |
| K3 | Ohne erkennbaren Bezug kein Archivtreffer — der Zeitbezug **verengt** (Akzeptanz 2) | 19 | zwei verschiedene Sperren: r2 ohne Bildschirmbezug gibt gar nichts frei; **r3 mit Bildschirmbezug, ohne Zeitbezug gibt Live-Kontext frei und Archiv nicht** — das Archivfeld ist leer *und* der Umfang nennt gar keine Archivmenge (die Suche lief nicht, sie fand nicht nur nichts). r3 ist zugleich seine eigene Positivkontrolle. Dazu die enge Zeitliste in beide Richtungen: „nochmal", „wieder", „schon" sind kein Zeitbezug, „vorhin", „gestern", „letzte Woche" sind einer. Und: r2 hat die Marke nicht eingelöst, r3 dann schon |
| K4 | Mit Marke und Bezug kommt **nur der Treffer**, nicht die Umgebung (Akzeptanz 2, Verifikationsabsatz) | 18 | eine **Mengenaussage**, deshalb liegen fünf unterscheidbare Kanarienvögel als Nachbarn im Archiv — zwei davor, der Treffer, zwei danach. In r4 steht der Treffer in den Antwortbytes, der Live-Kontext auch, **jeder der vier Nachbarn nicht**; `umfang.archiv == 1` und das Archivfeld trägt genau einen Eintrag. **Die Gegenprobe daneben**: eine Frage nach den Nachbarn holt sie sehr wohl heraus — ohne sie wäre „die Umgebung kam nicht mit" von „die Umgebung liegt gar nicht im Archiv" nicht zu unterscheiden, und ein leeres Archiv lieferte dieselbe grüne Zahl |
| K5 | Ein Treffer bleibt `tainted`, auch aus der eigenen Datenbank (Akzeptanz 3) | 5 | `Mark.TAINTED` am Eintrag **und** die echte Senkentabelle aus T-3.13b: gesperrt gegen `durchgang1`, gesperrt gegen `tts_ungefragt`, erlaubt in `durchgang2`. Positivkontrolle: es wurde überhaupt ein Treffer freigegeben |
| K6 | Proaktives Verhalten sieht das Archiv **nicht** (Akzeptanz 4, Design §1.1) | 13 | **an der Datenbank**: `sys.addaudithook` auf `sqlite3.connect`, unterhalb des Produktcodes. `proaktiv=True` mit gültiger Marke und beiden Bezügen → `ohne_nutzerhandlung`, **null Öffnungen**, die Archivsuche gar nicht erst gefragt; derselbe Aufruf ohne `proaktiv` → Öffnung und Treffer. Ein echter proaktiver Anlass → null Öffnungen, mit der Kontrolle, dass er überhaupt entschieden hat. Dazu der **Zulauf** über den Syntaxbaum statt Textsuche: `mind/proactive.py` importiert weder Archiv noch Gate und ruft weder `freigeben` noch `suchen` noch `kontext` noch `Archivsuche` — Positivkontrolle: dieselbe Messung findet die Verdrahtung in `hub/daemon.py` |
| K7 | Die Suche läuft nur auf Nachfrage, nie von selbst (Akzeptanz 5) | 12 | ebenfalls an der Datenbank, **Positivkontrolle der Spur zuerst**: eine echte Suche bewegt sie. Danach vier Fälle mit je einer Klammer um genau einen Aufruf — ohne Marke, ohne Bildschirmbezug, ohne Zeitbezug, beiläufige Frage: die Archivdatei bleibt jedes Mal ungeöffnet. Dazu der Router an der echten Vorrichtung: weder der lokale noch der Aktionsweg fragen das Gate, die Archivfrage schon |
| K8 | Live-Kontext und Archivtreffer aus **demselben** `freigeben()` (Plan T-7.5) | 15 | Spione, die an die echten Speicher durchreichen: beide werden je genau einmal gefragt, **mit demselben Schein-Objekt (Identität)**, der die `turn_id` dieser Runde trägt; die Marke wird **genau einmal** eingelöst. Getrennte Felder: Live in `eintraege`, Treffer in `archiv`, der Umfang nennt beide einzeln. Und der Weg im Prozess mit dem Modell: echter `Router` + echter `Durchgang2`, ein Aufzeichner an der Stelle des Egress — der Archivtreffer steht in **genau** dem Körper der Archivfrage, im Feld `archiv`, der Live-Kontext im Feld `bildschirm`, die beiläufige Frage trägt ihn nicht |

Gemessen wird an **echten Prozessen**: `t75_hub.py` ruft `Hub(...).start()`,
also die Verdrahtung des Betriebs — dasselbe `_gate_teile()` mit
`Kontextspeicher`, `Archivsuche`, `Deklassifizierung` und `audit_buch()`,
derselbe `kontext.sock` mit derselben Prüfung in `_horche_einfach`. Die
Kanarienvögel gehen mit dem echten `Archiv.schreiben` in eine echte
FTS5-Datenbank, die Marke über den echten `auth.sock`. Der Prüfstand baut
nichts davon nach.

---

## Die Hürde: wie diese Messung an `kontext.sock` herankommt

Dieselbe wie bei T-5.9, und derselbe Weg: die Unit-Allowlist von
`kontext.sock` lässt nur `daimon-mind.service` durch (`daemon.py`,
`KONTEXT_UNITS`). Eine transiente Unit dieses Namens wäre ein Eingriff in den
Betrieb — die Unit ist auf dieser Maschine **enabled**. Das ist unterblieben.

| | Allowlist | gemessen |
|---|---|---|
| Hub A | **unberührt** (`daimon-mind.service`) | weist den Prüfstand ab — die Verbindung fällt, keine Antwort |
| Hub B | Unit des Prüfstands (`app-com.anthropic.Claude-2595.scope`, zur Laufzeit aufgelöst) | bedient ihn — hier läuft die ganze Naht |

Hub A ist damit **keine Attrappe**: dort läuft die echte Allowlist gegen die
echte Unit-Auflösung. Daneben steht die Positivkontrolle, dass Hub A überhaupt
lebt und horcht (`state.sock` antwortet).

---

## Die harten Auflagen dieses Auftrags, und wie sie eingelöst sind

### 1. Kanarienvogel plus Positivkontrolle, beide gewogen

Zwischen Probe und Gegenprobe ist genau **eine** Bedingung anders:

```
Hub A, unberührte Allowlist   -> Verbindung fällt   |  Hub B, Allowlist gesetzt -> Antwort
ohne Marke (r1)               -> keine_marke        |  mit Marke (r4)          -> Treffer in den Bytes
verbrauchte Marke (r5)        -> keine_marke        |  frische Marke (r4)      -> Treffer
ohne Bildschirmbezug (r2)     -> kein_bildschirm…   |  mit beidem (r4)         -> Treffer
ohne Zeitbezug (r3)           -> Live ja, Archiv NEIN|  mit Zeitbezug (r4)      -> Live UND Archiv
Leseversuch ohne Schein       -> QuarantaeneFehler  |  echter Freigabeschein   -> Treffer
proaktiv=True                 -> 0 DB-Öffnungen     |  proaktiv=False          -> Öffnung + Treffer
Nachbarn kommen nicht mit     -> nicht in den Bytes |  Frage nach den Nachbarn -> sie kommen heraus
```

Die letzte Zeile ist die Auflage „mehrere unterscheidbare Kanarienvögel":
**vier Nachbarn**, zwei vor und zwei nach dem Treffer, jeder mit eigener
Wortmarke. „Die Umgebung kam nicht mit" ist erst damit eine Aussage — und die
Gegenprobe belegt, dass genau diese vier auffindbar *wären*.

Dazu vier Kontrollen, die nicht Probe/Gegenprobe sind, sondern die Messung
selbst absichern:

* **Hub A lebt**, während er abweist (`state.sock` antwortet).
* **Die Spur an der Datenbank misst überhaupt** (K7, erste Prüfung): eine
  echte Suche bewegt sie. Ohne diese Kontrolle wäre „null Öffnungen" genau der
  Falschbefund, den dieses Repo an einem Tag viermal hatte.
* **Der proaktive Pfad hat überhaupt entschieden** (K6): `Proaktiv.melden`
  gibt einen `Vorschlag` zurück — sonst wäre „er sah das Archiv nicht" auch
  bei totem proaktivem Pfad grün.
* **Die Journal-Attrappe hat den Anker abgefangen** (K2): bleibt die
  abgefangene Datei leer, wird der Prüfstand rot, statt still das echte
  Journal des Nutzers zu verschmutzen. Gemessen und nachgeprüft: seit 08:10
  steht **keine** neue `AUDIT-ANKER`-Zeile im echten Journal.

**Selbstauskunft ist kein Beleg.** Der `grund` in der Antwort des Hubs wird
geprüft, aber ausdrücklich als Diagnose; der Beleg ist der Zustand der
Antwortbytes und der Stand der Datenbank-Spur. Der Mutant `archiv-ohne-marke`
zeigt, warum: dort meldet der Hub `ok:true` — rot wird er daran, dass der
Treffer in den Bytes steht.

### 2. Jede Manipulation wird gewogen

Dieser Prüfstand verändert **keine Produktdatei**. Er hat genau einen Eingriff,
und der wird gewogen: `t75_hub.py` vergleicht sha256 der Allowlist von
`kontext.sock` vor und nach dem Setzen und **bricht mit Exit 3 ab**, wenn sie
gleich bleibt — ein Hub, der die Unit des Prüfstands ohnehin erlaubte, wäre
keine Gegenprobe zur Abweisung. Belegt im Lauf:

```
Allowlist unberuehrt: ('daimon-mind.service',)                       # Hub A
Allowlist gesetzt: sha256 4527d65a5e04 -> 17c7f5308798 (…scope,)     # Hub B
```

Die zweite Waage trägt die Mutantenerzeugung: `erzeugen.sh` bricht ab, wenn ein
Mutationsanker nicht **genau einmal** im Gut-Muster vorkommt. Ein Mutant, der
nichts geändert hätte, entsteht gar nicht erst.

### 3. Eine Messung ist ein Zeitpunkt, kein Zeitfenster

Kein `--since`, kein Fenster gegen ein zweites. Bezugspunkt jeder
Naht-Messung ist die **Antwort des Hubs** auf genau die Zeile, die er gerade
bearbeitet hat. Die Datenbank-Spur wird unmittelbar **vor** und unmittelbar
**nach** genau einem Aufruf abgelesen — eine Klammer um einen Aufruf, kein
Zeitraum. Der Markenzähler ebenso (vor r2 / nach r2 / nach r3). Es gibt keinen
`sleep` außer `warte_auf(hub.bereit)` (Abbruch bei Erfolg) und 0,4 s nach
`intent_mark`, direkt danach an einem festen Zähler belegt.

### 4. Auf welcher Datenbank gearbeitet wurde

**Auf einer eigenen, nicht auf der echten.** Der Prüfstand legt für jeden Lauf
einen vollständigen eigenen XDG-Baum unter `/tmp/t75-*` an und setzt
`XDG_DATA_HOME` **vor dem ersten Produktimport**; die Archivdatei entsteht dort
frisch und wird beim Aufräumen mit dem Baum entfernt. Zusätzlich bekommt jede
in-Prozess-`Archivsuche` ihren Pfad ausdrücklich mitgegeben, statt sich auf
`data_dir()` zu verlassen. Gegengeprüft am echten Archiv:

```
$ stat -c '%n %x %y' ~/.local/share/daimon/archiv.db
/home/itiger013/.local/share/daimon/archiv.db  2026-08-13 23:05:40  2026-08-18 07:03:35
```

atime vom 13.08., mtime von 07:03 — beide **vor** dieser Sitzung (Beginn
08:17). Die echte Datenbank ist weder gelesen noch geschrieben worden.

---

## Welche Zeile im Produktivcode müsste kaputt sein — und ausprobiert?

Die Auflage aus dem Auftrag, beantwortet, **mit Lauf**. Elf Mutanten, jeder
eine gebrochene Stelle im Gut-Muster, jeder gemessen:

| Kriterium | gebrochene Zeile | Ergebnis |
|---|---|---|
| K2 | `suche.py`: `if not ist_freigabeschein(schein):` → `if False:` | `archiv-ohne-schein`: **K2 8 rot**, sonst nichts — das Archiv wird zur zweiten Tür aus der Quarantäne |
| K2 | `declassify.py`: der `if turn_id is None:`-Block gibt das **Archiv** frei (den Live-Kontext weiter nicht) | `archiv-ohne-marke`: **K2 5 rot** (+ K7 3 — ohne Marke wird jetzt gesucht). Das ist die zentrale Prüffrage, mit `ja` beantwortet |
| K3, K7 | `declassify.py`: `if self._archiv is not None and zeitbezug(…):` → ohne die zweite Bedingung | `archiv-ohne-zeitbezug`: **K3 3 rot, K7 2 rot**, sonst nichts |
| K3 | `declassify.py`: `zeitbezug` → `return True` | `zeitbezug-immer-erkannt`: **K3 8 rot** (+ K7 2) |
| K1, K4 | `declassify.py`: `zeitbezug` → `return False` | `zeitbezug-nie-erkannt`: **K1 7, K3 3, K4 5, K5 1, K6 2, K7 1, K8 4 rot** — der Mutant tötet **jede** Positivkontrolle. Ein Verifizierer, der ihn nicht fängt, meldet grün, weil er nichts messen kann |
| K1 | `suche.py`: `suchbegriff` setzt einen FTS5-Spaltenfilter `text :` vor jedes Wort | `titel-nicht-durchsucht`: **K1 3 rot**, sonst nichts — OCR und Transkript gehen weiter, Fenstertitel nicht |
| K4 | `suche.py`: die Abfrage liefert zu jedem Treffer die zwei Einträge davor und danach mit | `treffer-mit-umgebung`: **K4 7 rot** (+ K1 3, K8 1) |
| K5 | `suche.py`: `Mark.TAINTED` → `Mark.TRUSTED` | `treffer-trusted`: **K5 3 rot**, sonst nichts |
| K6 | `declassify.py`: `if proaktiv:` → `if False:` | `proaktiv-sucht`: **K6 4 rot**, sonst nichts — an der Datenbank aufgefallen, nicht am Router |
| K8 | `declassify.py`: das Archiv bekommt einen **eigenen**, dort erfundenen Schein | `archiv-eigener-schein`: **K8 2 rot**, sonst nichts |
| K8 | `router.py`: `kontext["archiv"] = list(frei.get("archiv") or [])` → `= []` | `router-verwirft-archiv`: **K8 2 rot**, sonst nichts — die Freigabe stimmt, im Modellkörper steht nichts. Der Fehlertyp, der diesem Repo sechsmal passiert ist |

**Jedes** der acht Kriterien wird in mindestens einem Lauf rot. Kein Kriterium
ist blind. Sieben der elf Mutanten fallen **ausschließlich** an ihrem eigenen
Kriterium; die vier breiten (`archiv-ohne-marke`, `zeitbezug-immer-erkannt`,
`zeitbezug-nie-erkannt`, `treffer-mit-umgebung`) reißen naturgemäß mit, was
hinter ihnen liegt — die Zuordnung steht im Prüfstandkopf und wird bei jedem
Lauf mit ausgegeben.

---

## Belege (Befehl + Ausgabe)

```
$ git rev-parse HEAD
5724d23471d524ea7a8fc877c53dcda9c1f18ece

$ bash tests/verify/verify-frozen.sh
verify-frozen: 37 eingefrorene Dateien unveraendert; Abhaengigkeiten geschlossen.
EXIT 0

$ bash tests/verify/meta.sh T-7.5
meta[T-7.5]: Mutanten werden erzeugt (…/tests/mutants/T-7.5/erzeugen.sh) ...
T-7.5: 11 Mutanten erzeugt.
meta[T-7.5]: Gut-Muster ...
meta[T-7.5]: Mutante 'archiv-eigener-schein' erkannt.
meta[T-7.5]: Mutante 'archiv-ohne-marke' erkannt.
meta[T-7.5]: Mutante 'archiv-ohne-schein' erkannt.
meta[T-7.5]: Mutante 'archiv-ohne-zeitbezug' erkannt.
meta[T-7.5]: Mutante 'proaktiv-sucht' erkannt.
meta[T-7.5]: Mutante 'router-verwirft-archiv' erkannt.
meta[T-7.5]: Mutante 'titel-nicht-durchsucht' erkannt.
meta[T-7.5]: Mutante 'treffer-mit-umgebung' erkannt.
meta[T-7.5]: Mutante 'treffer-trusted' erkannt.
meta[T-7.5]: Mutante 'zeitbezug-immer-erkannt' erkannt.
meta[T-7.5]: Mutante 'zeitbezug-nie-erkannt' erkannt.
meta[T-7.5]: 11 Mutanten, alle erkannt.
META EXIT 0

$ bash tests/verify/T-7.5.sh                       # der echte Baum
Pruefling: /mnt/data/AI/repos/dAImon-t75
Unit des Pruefstands: 'app-com.anthropic.Claude-2595.scope'

Bilanz T-7.5:
K1:  8 Pruefungen, 0 rot        K5:  5 Pruefungen, 0 rot
K2: 20 Pruefungen, 0 rot        K6: 13 Pruefungen, 0 rot
K3: 19 Pruefungen, 0 rot        K7: 12 Pruefungen, 0 rot
K4: 18 Pruefungen, 0 rot        K8: 15 Pruefungen, 0 rot
EXIT 0

$ for m in tests/mutants/T-7.5/*/; do … done      # jede Mutante einzeln
archiv-eigener-schein    | EXIT 1 | K8: 2 rot
archiv-ohne-marke        | EXIT 1 | K2: 5 rot  K7: 3 rot
archiv-ohne-schein       | EXIT 1 | K2: 8 rot
archiv-ohne-zeitbezug    | EXIT 1 | K3: 3 rot  K7: 2 rot
proaktiv-sucht           | EXIT 1 | K6: 4 rot
router-verwirft-archiv   | EXIT 1 | K8: 2 rot
titel-nicht-durchsucht   | EXIT 1 | K1: 3 rot
treffer-mit-umgebung     | EXIT 1 | K1: 3 rot  K4: 7 rot  K8: 1 rot
treffer-trusted          | EXIT 1 | K5: 3 rot
zeitbezug-immer-erkannt  | EXIT 1 | K3: 8 rot  K7: 2 rot
zeitbezug-nie-erkannt    | EXIT 1 | K1: 7  K3: 3  K4: 5  K5: 1  K6: 2  K7: 1  K8: 4 rot

$ systemctl --user list-units 'daimon*' --all      # vor wie nach den Laeufen
daimon-auth.service        active running
daimon-face.service        active running
daimon-focus.service       active running
daimon-hookbridge.service  active running
daimon-hub.service         active running
daimon-ears.service        inactive dead      # seit 07:38:53, VOR dieser Sitzung
daimon-eyes.service        inactive dead      # seit 07:38:53, VOR dieser Sitzung
daimon-mind.service        inactive dead      # seit 07:38:53, VOR dieser Sitzung
daimon-egress.socket / daimon-stt.socket / daimon-tts.socket   active listening
daimon-audit-verify.timer / daimon-phase1.timer                active waiting

$ systemctl --user is-enabled daimon-mind.service
enabled

$ systemctl --user list-units --all | grep -i t75
(keine)

$ journalctl --user --since "2026-08-18 08:10" | grep -c AUDIT-ANKER
0

$ pgrep -af "[t]75_hub"; pgrep -af "[t]75_pruefstand"; ls -d /tmp/t75-*
(kein Treffer, kein Verzeichnis geblieben)
```

**Zum Zustand der Dienste, ausdrücklich:** `daimon-ears`, `daimon-eyes` und
`daimon-mind` standen bei Sitzungsbeginn (08:17) bereits auf `inactive`; ihr
letzter Zustandswechsel war um **07:38:53**, also vor dieser Sitzung
(`systemctl --user show … InactiveEnterTimestamp`). Diese Sitzung hat
**keinen** `systemctl`-Aufruf gemacht und keinen Dienst angehalten. Eine
`daimon-recorder.service` gibt es auf dieser Maschine gar nicht.

---

## Grenzen — was er NICHT misst

1. **Die Zuordnung Mind ↔ Unitname ist nicht gemessen.** Gemessen ist, dass
   `kontext.sock` eine fremde Unit abweist (Hub A, echte Allowlist, echte
   Auflösung) und eine gelistete bedient (Hub B). Nicht gemessen ist, dass der
   echte Mind-Prozess unter `daimon-mind.service` läuft — dafür bräuchte es
   einen Lauf **in** dieser Unit. Das gehört T-5.9b.v/T-7.1.v. Dieselbe Grenze
   wie bei T-5.9.v, und sie ist hier nicht kleiner geworden.
2. **Die Datenbank-Spur (K6, K7) läuft IM PRÜFSTANDSPROZESS, nicht im Hub.**
   `sys.addaudithook` gilt je Interpreter; die Öffnungen der beiden
   Hub-Prozesse sind für sie unsichtbar. Gemessen ist damit: das *Gate* öffnet
   das Archiv nur unter allen Bedingungen. **Nicht** gemessen ist, ob im
   Hub-Prozess noch jemand anders die Archivdatei öffnet — ein Zeitgeber, ein
   Hintergrundfaden, eine spätere Erweiterung. Was dagegen steht, ist der
   Zulauf über den Syntaxbaum (K6) und die Naht (r3 gibt kein Archiv heraus);
   ein Prozess-weiter Nachweis wäre eine Laufzeit-Dateiöffnungsspur, wie
   `PLAN.md` sie für das Einfrieren vorsieht, und ist hier nicht gebaut.
3. **Kein echter Recorder, keine echten Augen.** Die Kanarienvögel gehen mit
   `Archiv.schreiben` in die Datenbank, nicht über `daimon-recorder`. Dass der
   Recorder genau dorthin schreibt und dass Rohaudio nie ankommt, ist T-7.1
   und T-7.4. Der Prüflauf schaltet dafür auch nichts an — er startet keine
   Wahrnehmung.
4. **Verfall, Obergrenze und Verdrängung sind nicht gemessen.** Ob ein Treffer
   nach 30 Tagen verschwindet, ist T-7.1 und dort zu prüfen; dieser Prüfstand
   schreibt seine Kanarienvögel mit einem festen `ts` kurz vor jetzt.
5. **Die Redaktion vor dem Schreiben spielt hier nicht mit.** Die
   Kanarienvögel liegen unter einer nicht gelisteten Fensterklasse
   (`harmlos-app`). Ob ein gelistetes Fenster gar nicht erst ins Archiv kommt,
   ist T-7.2 — und dort **rot** (`LEDGER-T-7.2.v.md`). Ein Verifizierer, der
   beides mischte, wüsste hinterher nicht, welche Sperre gegriffen hat.
6. **Der Weg vom Mind zum Egress ist nicht gemessen.** K8 belegt, dass der
   Archivtreffer im Körper von Durchgang 2 landet und in keinem anderen; dass
   dieser Körper dann tatsächlich über den Egress-Broker hinausgeht, ist
   T-5.10.
7. **Der Umfang im AUDIT ist hier nicht gelesen.** `umfang.archiv` wird aus
   der Antwort auf `kontext.sock` gelesen, nicht aus der Audit-Kette. Dass der
   Umfang einer Freigabe im Audit lesbar steht, ist Akzeptanzpunkt 4 von
   **T-5.9** und dort rot (`LEDGER-T-5.9.v.md`). Dieser Verifizierer ist
   bewusst nicht an jenen Befund gebunden — sonst wäre er rot für einen
   Fehler, den er nicht prüft. Das ist auch der Grund, warum sein Gut-Muster
   bytegleich mit dem Arbeitsbaum ist und das von T-5.9 nicht.
8. **Die Suche nach dem Kanarienvogel ist eine Bytesuche in UTF-8.** Sie deckt
   den Weg ab, um den es geht (die Antwortzeile auf `kontext.sock`). Einen
   Schreiber, der denselben Text kodiert oder komprimiert herausgäbe, fände
   sie nicht.
9. **Die WAL-Frage ist umgangen, nicht gemessen.** Das Archiv läuft in WAL;
   der Prüfstand schließt seinen Schreiber sauber (Checkpoint), bevor die
   `mode=ro`-Verbindung liest. Im Betrieb hält `daimon-recorder` die Datenbank
   dauerhaft offen, und die lesende Verbindung braucht dann ein beschreibbares
   `-shm`. Dass das unter derselben uid trägt, steht im Code (0600, gleicher
   Nutzer) und ist hier **nicht** gegen einen laufenden Schreiber geprüft.
   Wer T-7.1.v baut, sollte das mitnehmen.
10. **Die Qualität der Suche ist nicht Gegenstand.** Gemessen ist, welche
    Zeilen für eine Abfrage zurückkommen, deren einziges passendes Token die
    Wortmarke ist. Ob `suchbegriff`s ODER-Verknüpfung für eine echte Frage
    eines Menschen die richtige Zeile findet, ist eine Güte-, keine
    Sicherheitsfrage — und sie steht in keinem Akzeptanzpunkt.
11. **`t75_hub.py` ist eine zweite Fassung von `t59_hub.py`.** Das ist
    Absicht: `T-5.9.*` gehört einem anderen, abgeschlossenen Auftrag und wurde
    von dieser Sitzung nicht angefasst; ein gemeinsamer Helfer hätte ihn
    geändert. Es sind zwei Fassungen eines **Gerüsts**, nicht einer Regel —
    die Regel steht im Prüfling. Wer beide zusammenführen will, braucht dafür
    einen eigenen autorisierten Task.
12. **Nicht eingefroren.** `freeze.sh` bleibt ungerufen; die
    Freeze-Erweiterung ist laut `PLAN.md` ein eigener, einzeln autorisierter
    Task. Beim Einfrieren sind `t75_pruefstand.py` **und** `t75_hub.py` als
    Helfer zu deklarieren.

---

## Was der Builder als Nächstes tun kann

* **Nichts an T-7.5.** Alle fünf Akzeptanzpunkte sind gemessen erfüllt. Der
  Verifizierer ist gegen Gut-Muster *und* Arbeitsbaum grün, und dass er
  trotzdem misst, belegen elf Mutanten.
* Der eine Punkt, der aus diesem Lauf mitzunehmen ist, gehört **T-7.1.v**: die
  lesende `mode=ro`-Verbindung neben einem *laufenden* Recorder in WAL. Hier
  ist sie mit sauber geschlossenem Schreiber gemessen, im Betrieb ist der
  Schreiber offen (Grenze 9).
* Der zweite gehört **T-5.9**: der Umfang einer Freigabe steht im Audit nur
  redigiert. Dieser Prüfstand liest ihn deshalb aus der Socket-Antwort — und
  bleibt davon unberührt.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, Einfrieren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main` |
| Ausgangs-Commit | `d94814e` (nach dem Einfrieren von T-7.3) |
| Verifizierer unverändert | `T-7.5.sh` `8559244b…`, `t75_pruefstand.py` `620da652…`, `t75_hub.py` `a338db78…` |

Der uncommittete `FROZEN`-Eintrag der abgebrochenen Sitzung war kein Beleg.
Zurückgenommen, von vorn gemessen.

**Dieser Verifizierer ist der einzige der elf, der schon am 18.08. grün war.**
Er ist damit auch der einzige, bei dem „grün gegen `main`" allein nichts sagt
— das Gut-Muster ist bytegleich mit dem Arbeitsbaum. Was er misst, steht in
den Mutanten, und deshalb ist der Mutantenlauf hier nicht die Kontrolle,
sondern die Messung.

### 1. Gegen `main` — grün

```
$ env -u DAIMON_FIXTURE tests/verify/T-7.5.sh; echo $?
Pruefling: /mnt/data/AI/repos/dAImon
Treiber:   /mnt/data/AI/repos/dAImon/tests/verify/t75_hub.py
Unit des Pruefstands: 'app-com.anthropic.Claude-58898.scope'

Bilanz T-7.5:
K1:  8 Pruefungen, 0 rot    K5:  5 Pruefungen, 0 rot
K2: 20 Pruefungen, 0 rot    K6: 13 Pruefungen, 0 rot
K3: 19 Pruefungen, 0 rot    K7: 12 Pruefungen, 0 rot
K4: 18 Pruefungen, 0 rot    K8: 15 Pruefungen, 0 rot
0
```

110 Prüfungen, keine rot. Neun Commits liegen zwischen dem Bau und diesem
Lauf, darunter `fe4a33a` (`ipc._unit`) und `d5c012b` (Hub-Allowlisten) — beide
berühren die Naht, an der K2 und K8 messen. Kein Kriterium ist dadurch rot
geworden.

### 2. Gegen das Gut-Muster und alle elf Mutanten

```
$ bash tests/verify/meta.sh T-7.5
T-7.5: 11 Mutanten erzeugt.
meta[T-7.5]: Gut-Muster ...
… archiv-eigener-schein · archiv-ohne-marke · archiv-ohne-schein ·
  archiv-ohne-zeitbezug · proaktiv-sucht · router-verwirft-archiv ·
  titel-nicht-durchsucht · treffer-mit-umgebung · treffer-trusted ·
  zeitbezug-immer-erkannt · zeitbezug-nie-erkannt — alle erkannt.
meta[T-7.5]: 11 Mutanten, alle erkannt.
```

Die Zuordnung aus dem Lauf, unverändert gegenüber dem 18.08.:

```
{"archiv-ohne-schein": "K2 (das Archiv als zweite Tuer)",
 "archiv-ohne-marke": "K2", "archiv-ohne-zeitbezug": "K3, K7",
 "zeitbezug-immer-erkannt": "K3",
 "zeitbezug-nie-erkannt": "K1, K4 (er toetet die Positivkontrollen)",
 "titel-nicht-durchsucht": "K1", "treffer-mit-umgebung": "K4",
 "treffer-trusted": "K5", "proaktiv-sucht": "K6",
 "archiv-eigener-schein": "K8", "router-verwirft-archiv": "K8"}
```

Jedes der acht Kriterien wird weiterhin in mindestens einem Lauf rot.

### 3. Was dieser Nachlauf NICHT ändert

Alle Grenzen des Ledgers gelten unverändert; keine ist heute neu gemessen
worden. Grenze 9 (lesende `mode=ro`-Verbindung neben einem *laufenden*
Schreiber) ist am 18.08. von T-7.1.v K9 beantwortet worden und heute dort
erneut grün gelaufen.
