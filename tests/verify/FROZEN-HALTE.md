# FROZEN — Halte und ihre Begründungen

`tests/verify/FROZEN` trägt Hashes, sonst nichts. Die Begründungen stehen hier.

**Warum getrennt.** `tests/verify/freeze.sh:90` sortiert das Manifest mit
`sort -k2 -o "$FROZEN" "$FROZEN"`. Eine Kommentarzeile hat in Spalte 2 keinen
Pfad, sondern ein beliebiges Wort — sie wird beim Sortieren zwischen die
Hashzeilen und untereinander einsortiert. Nach drei Nachzügen (26., 27., 28.08.)
standen 86 Kommentarzeilen alphabetisch zerhackt in FROZEN: Satzanfänge neben
Satzmitten, Halte von T-3.13b zwischen denen von T-3.14.
`verify-frozen.sh:28` überspringt `#`-Zeilen und meldete weiter Exit 0 — die
Zusage hielt, ihre Begründung war zerlegt.

In FROZEN bleibt genau **eine** Kommentarzeile. Eine einzelne Zeile übersteht
jede Sortierung; sie zeigt hierher. Neue Begründungen kommen in dieses
Dokument, **nicht** nach FROZEN zurück.

Die Wortlaute unten sind die gemessenen. Sie wurden aus dem Git-Verlauf
zurückgeholt, nicht neu formuliert (Herkunft je Abschnitt genannt).

---

## T-3.13b — Nachzug vom 26.08.

**Betroffen:** `tests/verify/t313b_pruefstand.py`
**Herkunft des Wortlauts:** `02055af:tests/verify/FROZEN`, Zeilen 26–65 (dort
noch zusammenhängend).

**Was gemessen wurde**

```
NACHGEZOGEN am 26.08. (Rolle reviewer), VORLAEUFIG -- der Mutationstest steht
noch aus. Vorher: 4922461f5e9e7b86a2603664adf8e43f3e50c6fe5fd25bf1d6d47f65e23f23fe
Grund: Zeile 1037-1044 erwartet fuer die Senke `tts_ungefragt` jetzt
[trusted] statt [trusted, user_ptt]. Stuetzt sich auf daimon/common/taint.py
(SENKEN["tts_ungefragt"][Mark.USER_PTT] ist False) und Design 8.3 "Variable
Anteile sind ausschliesslich trusted-Werte"; die Tabellenzeile war bis zum
26.08. die Attrappe neben `hub/sprechtext.aus_vorlage` und hatte keinen
Aufrufer. Der Pruefstand folgt damit der Fassung, die jetzt gilt.

Stand 26.08. abends (Rolle reviewer), Punkte 2 und 3 ERLEDIGT:
  2. ERLEDIGT -- das Gut-Muster fuehrt `tts_ungefragt` jetzt mit
     Mark.USER_PTT: False; nur diese eine Regelzeile wurde nachgezogen,
     nicht die Umgestaltung von e83c961.
  3. ERLEDIGT -- tests/mutants/T-3.13b/erzeugen.sh hat einen achten
     Mutanten `ptt-wird-ungefragt-gesprochen`, der genau den behobenen
     Fehler wieder einbaut. `bash tests/verify/meta.sh T-3.13b` ist gruen:
     Gut-Muster besteht, alle acht Mutanten werden erkannt. Jeder der
     sieben alten Mutanten scheitert weiterhin an seiner EIGENEN Mutation
     (je Mutant nachgemessen) -- keiner ist durch die Regel wirkungslos.
```

**Erster Halt: undeklarierte Helfer in der Spur 'echt'**

```
OFFEN bleibt der Hash selbst -- er ist weiterhin VORLAEUFIG, weil
`freeze.sh T-3.13b` NICHT durchlaeuft. Gemessen am 26.08. mit von Hand
entfernten T-3.13b-Zeilen; freeze.sh brach nach gruenem Mutationstest und
gruener Spur 'gut' an der Spur 'echt' ab:

  freeze-deps: ABGELEHNT -- Laufzeitspur: nicht deklarierte repo-lokale
  Datei geoeffnet: tests/verify/T-metaprobe.sh, tests/verify/meta.sh

Ursache: K13 faehrt im Arbeitsbaumlauf die ganze pytest-Suite
(t313b_pruefstand.py:1373). Seit 5a78c51 (17.08., NACH dem Einfrieren von
T-3.13b) enthaelt sie tests/test_meta_erzeugung.py, das
tests/verify/T-metaprobe.sh anlegt und tests/verify/meta.sh ruft. Beide
sind repo-lokal und in FROZEN.deps nicht deklariert.
Die Zeile nachzutragen ist KEINE Reviewer-Entscheidung: freeze.sh friert
jede deklarierte und vorhandene Abhaengigkeit mit ein -- tests/verify/
meta.sh laege danach selbst hinter einem .v-Task.
```

**Zweiter Halt: der Prüfling selbst**

```
Zweiter, unabhaengiger Halt: freeze.sh verlangt die Spur 'echt' gruen.
`bash tests/verify/T-3.13b.sh` im Arbeitsbaum ist am 26.08. 24 rot
(K13: T-3.12 und T-3.13 einzeln rot, pytest rot an
test_egress.py::test_mind_traegt_keinen_fremden_schluessel_in_der_umgebung).
Beide Halte sind aelter als dieser Nachzug und von ihm unabhaengig.
```

**Was zur Auflösung fehlt**

```
  1. offen: neuer .v-Task fuer T-3.13b, der beide Halte aufloest
```

---

## T-3.14 — Nachzug vom 27.08. (überholt)

**Betroffen:** `tests/verify/t314_pruefstand.py`
**Herkunft des Wortlauts:** `8dbbbca:tests/verify/FROZEN`, Zeilen 67–98 (dort
noch zusammenhängend). Am 28.08. durch den folgenden Abschnitt ersetzt; hier
festgehalten, weil der 28.08.-Stand sich auf ihn beruft ("Von den zehn roten
Pruefungen des 27.08. sind neun weg").

**Was gemessen wurde**

```
NACHGEZOGEN am 26.08. (Rolle reviewer), weiterhin VORLAEUFIG.
Vorher: c544d7e2f48fb63f2c52bea84e01b80e08e57386c56b5b685d6db10425380d4d
Grund: `tts_beginnt` setzt fuer den Kanal `ungefragt` jetzt ausdruecklich
markierung=trusted (Zeile 522ff). Vorgabe bei fehlender Marke ist seit dem
26.08. `tainted` (hub/daemon.py:1178, face/tts.py:519 und :993) -- ohne das
Feld liefe der Pruefstand an seiner eigenen Freigabe auf. Das Gut-Muster
T-3.14 vertraegt den Zusatz (sein daemon.py:801 liest `markierung`).

Stand 27.08. (Rolle reviewer): der Mutationstest ist ERLEDIGT -- `bash
tests/verify/meta.sh T-3.14` ist gruen, Gut-Muster besteht, 6 Mutanten,
alle erkannt.
```

**Erster Halt: undeklarierte Helfer in der Spur 'echt'**

```
OFFEN bleibt der Hash selbst: er ist weiterhin VORLAEUFIG,
weil `freeze.sh T-3.14` NICHT durchlaeuft. Gemessen am 27.08. mit von Hand
entfernten T-3.14-Zeilen (t314_pruefstand.py und T-3.14.sh); freeze.sh
brach nach gruenem Mutationstest und gruener Spur 'gut' an der Spur 'echt'
ab:
  freeze-deps: ABGELEHNT -- Laufzeitspur: nicht deklarierte repo-lokale
  Datei geoeffnet: tests/verify/T-metaprobe.sh, tests/verify/meta.sh
Ursache wie bei T-3.13b: K13 faehrt im Arbeitsbaumlauf die ganze
pytest-Suite, und die enthaelt seit 5a78c51 tests/test_meta_erzeugung.py,
das genau diese beiden Dateien anfasst. Sie in FROZEN.deps nachzutragen ist
KEINE Reviewer-Entscheidung: freeze.sh friert jede deklarierte und
vorhandene Abhaengigkeit mit ein -- tests/verify/meta.sh laege danach
selbst hinter einem .v-Task.
```

**Zweiter Halt: der Prüfling selbst**

```
Zweiter, unabhaengiger Halt, am 27.08. eigens nachgemessen (die Spur 'echt'
bricht schon vorher ab und misst ihn nicht mehr): freeze.sh verlangt sie
gruen, `bash tests/verify/T-3.14.sh` ist im Arbeitsbaum aber Exit 1 mit 11
roten Pruefungen -- K4 bis K11 laufen in Zeitfenster fuer
voice.state=listening, K13 ist zweimal rot. Darunter weiterhin die
T-3.9-Positivkontrolle 'ein Sprech-Endpunkt ist am Hub auffindbar'
(erwartet ja, war nein), in Erst- UND Nachlauf; ohne sie ist der Rest von
T-3.9 ungemessen. K13-pytest: 55 failed, 1844 passed.
Beide Halte sind aelter als dieser Nachzug und von ihm unabhaengig.
```

**Was zur Auflösung fehlte**

```
  1. offen: neuer .v-Task fuer T-3.14, der beide Halte aufloest
```

---

## T-3.14 — Nachzug vom 28.08. (gültig)

**Betroffen:** `tests/verify/t314_pruefstand.py`, mitgezogen
`tests/verify/T-3.14.sh`
**Herkunft des Wortlauts:** die 43 Kommentarzeilen, die `6a4802f` zu
`tests/verify/FROZEN` hinzufügte, plus die zwei aus dem 27.08.-Nachzug
übernommenen Schlusszeilen. Der Sortierlauf hatte sie über die ganze Datei
verteilt; die Reihenfolge unten ist aus dem Satzfluss wiederhergestellt und
deckt sich mit der Commit-Nachricht von `6a4802f`.

**Was gemessen wurde**

```
tests/verify/t314_pruefstand.py: NACHGEZOGEN am 28.08. (Rolle reviewer),
weiterhin VORLAEUFIG. Vorher: bcff10c4f5f275b805a1cdd35e5ff5ce481a89a9f504c7be8ff6da3e35295438
tests/verify/T-3.14.sh ist unveraendert und steht nur mit, weil freeze.sh
beide Zeilen zusammen schreibt.

Grund der Aenderung: der Pruefstand startete den Hub als
`python -m daimon.hub.daemon` und hatte damit keine Stelle, an der sich
etwas umhaengen liesse. Er startet ihn jetzt ueber einen eigenen Starter
(HUB_STARTER), der seine EIGENE Unit ueber `ipc.peer_of` misst und
PRODUZENT_UNITS['auth'], ['ears'], ['hookbridge'] sowie TTS_UNITS darauf
umhaengt -- eine Liste je Endpunkt, den der Pruefstand anspricht; 'face'
bleibt unberuehrt. Ohne das wies `ipc.accept` den Pruefstand als fremde
Unit ab (dritte Fassung derselben Wand nach T-0.9 und T-3.9). Gesetzt wird
nur das Modulattribut des GELADENEN Hubs; daimon/hub/daemon.py bleibt
byte-identisch, und `None`/`()` werden nie gesetzt.

Dazu ein neues Kriterium W (WANDKONTROLLE): ein zweiter Hub OHNE das
Umhaengen muss genau dann abgewiesen werden, wenn der geladene Baum diese
Unit sperrt (`ipc.unit_erlaubt`, mit seiner eigenen Liste gerechnet). Das
Gut-Muster vom 11.08. kennt keine der vier Listen und muss folgerichtig
durchlassen -- eine pauschale Erwartung waere dort falsch rot.

Stand 28.08.: `bash tests/verify/T-3.14.sh` ist im Arbeitsbaum Exit 1 mit
GENAU EINER roten Pruefung, K13-pytest. K1 9, K2 8, K3 3, K4 3, K5 10,
K6 19, K7 6, K8 10, K9 5, K10 22, K11 5, K12 6, W 12 -- alle 0 rot. Von
den zehn roten Pruefungen des 27.08. sind neun weg; die Prozesszaehlungen
waren Messverschmutzung durch gleichzeitig laufende Sitzungs-Shells (der
Zaehler pgrep-t auf den Prueflingspfad) und sind im ruhigen Lauf gruen.
```

**Erster Halt: undeklarierte Helfer in der Spur 'echt'** — jetzt drei statt zwei

```
OFFEN bleibt der Hash: `freeze.sh T-3.14` laeuft NICHT durch. Gemessen am
28.08. mit von Hand entfernten T-3.14-Zeilen; Mutationstest gruen
(Gut-Muster besteht, 6 Mutanten, alle erkannt), Spur 'gut' ohne
undeklarierte Helfer, Abbruch an der Spur 'echt':

  freeze-deps: ABGELEHNT — Laufzeitspur: nicht deklarierte repo-lokale
  Datei geoeffnet: tests/harness/vollbildfenster.py,
  tests/verify/T-metaprobe.sh, tests/verify/meta.sh

Ursache wie am 27.08.: K13 faehrt im Arbeitsbaumlauf die ganze
pytest-Suite, und die fasst diese drei Helfer an (am 27.08. waren es zwei;
vollbildfenster.py ist seither dazugekommen). Sie in FROZEN.deps
nachzutragen ist KEINE Reviewer-Entscheidung -- freeze.sh friert jede
deklarierte und vorhandene Abhaengigkeit mit ein, tests/verify/meta.sh
laege danach selbst hinter einem .v-Task.
```

**Zweiter Halt: der Prüfling selbst**

```
Zweiter, unabhaengiger Halt: freeze.sh verlangt die Spur 'echt' gruen, und
der Lauf ist wegen K13-pytest Exit 1 (Erstlauf Exit 1, erlaubter Nachlauf
Exit 1; massgeblich ist der Nachlauf). Die T-3.9-Positivkontrolle 'ein
Sprech-Endpunkt ist am Hub auffindbar' steht NICHT mehr dabei -- T-3.9 ist
seit 7b385e6 gruen und laeuft in K13 durch.
Beide Halte sind aelter als dieser Nachzug und von ihm unabhaengig.
```

**Was zur Auflösung fehlt**

```
  1. offen: neuer .v-Task fuer T-3.14, der beide Halte aufloest
```

---

## T-3.14 — Stand 29.08.

**Was gemessen wurde.** Der Stand vom 28.08. hält: von den zehn roten Prüfungen
des 27.08. sind neun weg, übrig ist allein **K13-pytest**. Damit steht nur noch
diese eine Prüfung zwischen T-3.14 und einer grünen Spur 'echt'.

**Und der K13-Befund selbst — K13 misst den eigenen Lauf, nicht den Prüfling:**

- Im K13-Lauf: **53 Fehlschläge**.
- Dieselben Dateien einzeln aufgerufen: **grün**.
- Die ganze Suite **außerhalb des Prüfstands**: **1872 passed** mit genau
  **einem** Fehlschlag —
  `test_egress.py::test_mind_traegt_keinen_fremden_schluessel_in_der_umgebung`.
  Der ist **echt**: `NVIDIA_API_KEY` war in der Sitzung gesetzt.

52 der 53 Fehlschläge sind also Messverschmutzung des K13-Laufs — dieselbe
Bauform wie die Prozesszählungen vom 27.08., die sich am 28.08. als
Sitzungs-Shells erwiesen. K13 fährt die ganze pytest-Suite innerhalb des
Prüfstands und misst dabei die Nebenwirkungen seines eigenen Laufs mit.

**Was zur Auflösung fehlt**

1. Ein `.v`-Task für T-3.14, der beide Halte auflöst — unverändert offen.
2. Für den Prüflings-Halt: K13 so aufstellen, dass er die Suite misst und nicht
   den eigenen Lauf. Solange der Unterschied 53 gegen 1 beträgt, ist der rote
   K13-Ausgang keine Aussage über den Prüfling.
3. Der eine echte Fehlschlag bleibt: `NVIDIA_API_KEY` darf in der Umgebung des
   Mind nicht stehen. Das ist ein Befund am Betrieb, kein Prüfstandsfehler.
4. Der Helfer-Halt bleibt unberührt davon: `tests/harness/vollbildfenster.py`,
   `tests/verify/T-metaprobe.sh` und `tests/verify/meta.sh` sind weiterhin nicht
   in `FROZEN.deps` deklariert, und sie dort nachzutragen ist keine
   Reviewer-Entscheidung.

**Nachtrag 30.08. — der Helfer-Halt ist AUFGELÖST**

`freeze.sh T-3.14` ist am 30.08. durchgelaufen: Mutationstest grün (Gut-Muster
besteht, 6 Mutanten, alle erkannt), Spur 'gut' **und** Spur 'echt' ohne
undeklarierte Helfer. Der neue Hash steht in `tests/verify/FROZEN`:
`e3e5a779…`, vorher `e8fa3681…`.

**Woran es lag.** Der pytest-Vollsuitelauf ist aus K13 heraus
(`t314_pruefstand.py`, `pruefe_k13`; die Begründung steht dort im Quelltext).
Die drei Helfer `tests/harness/vollbildfenster.py`, `tests/verify/T-metaprobe.sh`
und `tests/verify/meta.sh` wurden ausschließlich über
`tests/test_meta_erzeugung.py` angefasst, und das lief nur als Teil dieser
Vollsuite. Ohne sie erreicht die Laufzeitspur die drei nicht mehr — es musste
also kein Helfer nachdeklariert und mit eingefroren werden. `FROZEN.deps` bleibt
unverändert (`tests/verify/T-3.14.sh tests/verify/t314_pruefstand.py`), `FROZEN`
trägt weiterhin 53 Zeilen. Der Punkt 4 der Liste oben ist damit erledigt.

Die Vollsuite ist nicht weggefallen, nur umgezogen: `pytest -q` ist letzte Zeile
jedes Phasen-Gates (`docs/IMPLEMENTATION-PLAN.md`, Gate P0/P3/P4/P5/P6/P7/P8) —
dort misst sie nicht die Sitzung eines Prüfstandslaufs mit.

**Der zweite Halt ist damit ebenfalls weg.** `bash tests/verify/T-3.14.sh` ist im
Arbeitsbaum am 30.08. vollständig grün: 13 Kriterien plus die Wandkontrolle W,
**0 rot**. Von den elf roten Prüfungen des 27.08. und der einen des 29.08.
(K13-pytest) ist keine mehr übrig.

---

## T-3.13b — Stand 30.08. (Änderung zurückgenommen)

**Betroffen:** `tests/verify/t313b_pruefstand.py`

**Was vorbereitet war.** Dieselbe Änderung wie bei T-3.14: der pytest-Vollsuitelauf
fällt aus K13 heraus (`t313b_pruefstand.py:1373`), mit derselben Begründung — der
Prüfstand misst sonst die Nebenwirkungen seines eigenen Laufs statt des
Prüflings, und die Vollsuite läuft ohnehin an jedem Phasen-Gate. Bei T-3.14 hat
genau das den Helfer-Halt aufgelöst (Abschnitt oben), und derselbe Halt steht
seit dem 26.08. auch vor T-3.13b: `T-metaprobe.sh` und `meta.sh` kommen dort über
denselben Weg in die Spur.

**Warum sie hier nicht anwendbar ist.** Die Änderung verschiebt den Hash, und neu
einfrieren geht nicht:

- `bash tests/verify/T-3.13b.sh` meldet mit der Änderung **GEÄNDERT** — erwartet
  `fe9060d3…`, gefunden `f762fccb…`. Das Gate ist damit rot, ohne dass ein
  Befund über den Prüfling dahinterstünde.
- `freeze.sh` verlangt für ein Neueinfrieren die Spur 'echt' grün. `bash
  tests/verify/T-3.13b.sh` ist im Arbeitsbaum am 30.08. mit **22 roten
  Prüfungen** breit rot (K2, K3, K5, K8, K9, K11, K13). Am 26.08. waren es 24.
  Die Ursachen sind älter als diese Arbeit und **ungemessen**.

Anders als bei T-3.14 löst der K13-Wegfall hier also nichts auf: er nimmt einen
von zwei Halten weg und lässt den zweiten — den Prüfling selbst — unberührt
stehen, während er zugleich das Gate rot macht.

**Entscheidung (Matthias, 30.08.):** Änderung zurückgenommen.
`tests/verify/t313b_pruefstand.py` steht wieder auf `fe9060d3…`;
`bash tests/verify/verify-frozen.sh` meldet Exit 0, „53 eingefrorene Dateien
unverändert; Abhängigkeiten geschlossen".

**Was zur Auflösung fehlt**

1. Die 22 roten Prüfungen von `T-3.13b.sh` klären — Ursachen ungemessen, keine
  davon stammt aus dieser Arbeit. Erst wenn die Spur 'echt' grün ist, kann der
  K13-Wegfall hier nachgezogen und der Prüfstand neu eingefroren werden.
2. Bis dahin bleibt der Nachzug vom 26.08. **VORLÄUFIG** und der `.v`-Task für
  T-3.13b offen.

---

## T-3.13b — Stand 31.08. (Prüfstand gemessen einen Pfad, den es nicht gibt)

**Betroffen:** `tests/verify/t313b_pruefstand.py`

Punkt 1 der Liste vom 30.08. — „die 22 roten Prüfungen klären, Ursachen
ungemessen" — ist zur Hälfte erledigt. Zwei Befunde am **Prüfstand**, nicht am
Prüfling; beide dieselbe Bauform: gemessen wurde ein Weg, den der Mind im
Betrieb gar nicht nimmt.

**Befund 1: keine Attrappe für `lokal.sock`.** Der Prüfstand stellte nur eine
Attrappe für `egress.sock` bereit. Der Mind wird aber seit dem 26.08. über die
Unit auf den lokalen Broker gewiesen (`--egress-socket %t/daimon/lokal.sock`,
die Weiche steht seither in `daimon/mind/daemon.py: main`). Jede Modellfrage
lief also ins Leere und endete in der kuratierten Absage — die zu Recht
`trusted` trägt und damit die Markierungskriterien grün-falsch rot färbte.

Die neue `LokalAttrappe` spricht das Protokoll des lokalen Brokers, belegt an
`daimon/brokers/lokal/broker.py`: die Pflichtfelder `ticket` und `koerper`
werden **vor** dem Modell geprüft, und die Antwort trägt **nur** `content` —
kein `id`, kein `stop_reason`, die baut der Broker selbst. Dazu drei neue
Voraussetzungs-Prüfungen: die Attrappe nimmt das Ticket, liefert einen
Textblock, weist ein verbrauchtes Ticket ab. Ohne sie wäre grün nicht von „am
toten Socket gemessen" zu unterscheiden.

**Befund 2: `unit_execstart` löste die systemd-Kürzel nicht auf.** Es ersetzte
nur den Repo-Pfad; `%t` blieb wörtlich stehen, und der Mind bekam als Modellweg
die Zeichenkette `%t/daimon/lokal.sock`. Neu aufgelöst werden `%t` und `%h` —
genau die, die in den ExecStart-Zeilen unter `config/systemd` vorkommen. Dazu
eine Positivkontrolle `rest_kuerzel`: bleibt nach der Auflösung ein
`%`-Kürzel stehen, wird der Lauf rot statt still falsch.

**Gemessen, Gegenprobe an beiden Enden.** Hub-, Modell- und Aktions-Attrappe
echt, nur der Modellweg variiert:

| Modellweg | Antwort des Mind | Zähler |
|---|---|---|
| aufgelöst | `ok: true`, Marke `tainted` | 3 Tickets, 3 Modellaufrufe |
| wörtlich `%t/daimon/lokal.sock` | `ok: false`, `grund: egress_weg`, Absage mit `trusted` | 3 Tickets, 0 Modellaufrufe |

**Wirkung, voller Lauf.** `bash tests/verify/T-3.13b.sh` fällt von **23 rot**
(148 Prüfungen) auf **9 rot** (149). Die gesamte Markierungsgruppe K2, K3, K5,
K8, K9 ist grün.

**Vier Fassungen derselben Funktion, eine davon richtig.** `unit_execstart`
steht viermal im Repo: `t311_pruefstand.py:291`, `t312_pruefstand.py:445`,
`t313_pruefstand.py:548` und `t313b_pruefstand.py:617`. Nur `t312` löst `%t`
auf — dort ist der Befund am 26.08. bereits lokal repariert worden, und die
Reparatur ist nie zu den Geschwistern gewandert. `t311` und `t313` führen
weiterhin die naive Fassung. Das ist die Regel aus `CLAUDE.md` in Reinform:
zwei (hier: vier) Fassungen einer Regel sind eine Regel und drei Attrappen.

**Offen und ausdrücklich nicht Teil dieser Arbeit**

1. Die restlichen **9 roten Prüfungen**: die drei Hook-Roten, `T-0.11` und die
   Umgebungspunkte (`onnxruntime-gpu`, `ProtectProc`, `ProcSubset`).
2. Die eingebetteten Prüfstände `t311` und `t313` führen dieselbe naive
   Fassung von `unit_execstart` und messen denselben Fantasiepfad — ungemessen,
   ob es dort auffällt.
3. Der K13-Wegfall vom 30.08. bleibt zurückgenommen und weiterhin nicht
   anwendbar.

**Der Hash bleibt VORLÄUFIG, und das Einchecken hängt daran.** `freeze.sh`
verlangt die Spur 'echt' grün; mit 9 rot ist der Prüfstand nicht dort, ein
regulärer Neueinfrierlauf scheidet also aus. `.githooks/pre-commit` lehnt die
geänderte Datei folgerichtig ab:

```
pre-commit: tests/verify/t313b_pruefstand.py ist eingefroren, der gestagte
            Inhalt weicht ab.
            erwartet fe9060d3…
            gestaged  e1cd306d…
            Eine Aenderung braucht einen neuen .v-Task mit Mutationstest.
```

Damit stehen genau zwei Wege offen, und keiner davon ist eine
Reviewer-Entscheidung:

1. Den Hash **von Hand** nach `tests/verify/FROZEN` nachziehen und hier als
   VORLÄUFIG führen — so sind die Nachzüge vom 26.08. und 28.08. entstanden,
   und so kam `fe9060d3…` überhaupt erst dorthin.
2. Die Änderung liegen lassen, bis die 9 verbleibenden Roten geklärt sind und
   `freeze.sh T-3.13b` durchläuft — der Weg, den Matthias am 30.08. für den
   K13-Wegfall gewählt hat.

Der `.v`-Task für T-3.13b bleibt in beiden Fällen offen.

---

## Nachzug vom 31.08. — vier Hashes, VORLAEUFIG

**Betroffen:** `tests/verify/t311_pruefstand.py`,
`tests/verify/t311_sandbox_probe.py`, `tests/verify/t313_pruefstand.py`,
`tests/verify/t313b_pruefstand.py`; dazu neu aufgenommen
`tests/verify/t311_hub_lauf.py`.

Der Abschnitt oben („Stand 31.08.") hält den Zwischenstand des Tages fest —
zwei Befunde an T-3.13b, 23 rot auf 9 rot. Hier steht, was danach noch gemessen
wurde, und der Nachzug der Hashes, mit dem das alles eincheckbar wird.

### Warum von Hand nachgezogen

`freeze.sh` scheidet für alle vier aus: es verlangt die Spur 'echt' grün.
`T-3.11.sh` ist 3 rot, `T-3.13b.sh` 7 rot — beide Male ohne Befund am Prüfling
selbst (siehe „Was offen bleibt"). Es bleibt der Weg der Nachzüge vom 26.08.
und 28.08.: Hash von Hand nach `tests/verify/FROZEN`, hier als **VORLAEUFIG**
geführt, mit Grund und Datum. Der `.v`-Task bleibt für jeden der vier offen.

| Datei | vorher | nachgezogen am 31.08. |
|---|---|---|
| `t311_pruefstand.py` | `72ab1f15…` | `5167f1f4…` |
| `t311_sandbox_probe.py` | `f7a119a1…` | `a0670390…` |
| `t313_pruefstand.py` | `842fa97e…` | `c47283bb…` |
| `t313b_pruefstand.py` | `fe9060d3…` | `ba0abe01…` |
| `t311_hub_lauf.py` | — (neu) | `be69fb1f…` |

`t311_hub_lauf.py` ist neu und in `FROZEN.deps` als Abhängigkeit von
`t311_pruefstand.py` deklariert; `freeze-deps.py pruefen` verlangt deshalb
auch für ihn einen Manifest-Eintrag. Eine Zeile im Modulkopf nannte
`tests/verify/T-3.9.sh` als Vorbild und wurde von der Entdeckung als
undeklarierte Kante gelesen — der Starter ruft T-3.9 nicht auf. Der Verweis
heißt jetzt „im Verifizierer T-3.9", ohne Pfadliteral; eine falsche Kante in
`FROZEN.deps` wäre der teurere Weg gewesen.

`bash tests/verify/verify-frozen.sh` meldet danach Exit 0: 54 eingefrorene
Dateien unverändert, Abhängigkeiten geschlossen.

### Die Befunde, alle derselben Klasse

Der Prüfstand ist jeweils älter als eine bewusste Entscheidung. Die Klasse
selbst steht seit heute in `CLAUDE.md`: „Ein eingefrorener Prüfstand ist eine
Zusage MIT DATUM".

**1. `unit_execstart` löste `%t` nicht auf** — in `t311`, `t313` und `t313b`.
`t312` hatte den Befund am 26.08. bereits lokal repariert; die Reparatur ist
nie zu den Geschwistern gewandert. Der Mind bekam `%t/daimon/lokal.sock`
wörtlich, fand nichts und gab die kuratierte Absage zurück, die zu Recht
`trusted` trägt. Gegenprobe an beiden Enden: aufgelöst → `ok: true`, Marke
`tainted`, 3 Modellaufrufe; wörtlich → `ok: false`, `grund: egress_weg`,
0 Modellaufrufe. Wirkung an T-3.13b: **23 rot → 9 rot**.

**2. Attrappe für `lokal.sock`** (`t313b`) — der Prüfstand stellte nur eine für
`egress.sock` bereit, seit die Weiche am 26.08. auf den lokalen Broker zeigt.
Drei neue Voraussetzungs-Prüfungen: nimmt Ticket, liefert Textblock, weist
verbrauchtes Ticket ab. Ohne sie wäre grün nicht von „am toten Socket
gemessen" zu unterscheiden. (Beide Befunde im Abschnitt oben ausführlich.)

**3. Aktionswünsche** (`t313b`) — aus **einer** Erwartung („wird abgelehnt",
die Zusage der Phase 3) wurden **fünf**, die messen, was seit T-4.16 gilt:
ohne Absichtsmarke abgelehnt und kostenfrei; ohne Ziel Rückfrage und
kostenfrei; mit beidem `weg: "aktion"` mit genau einem Ticket und einem
Modellaufruf; und — neu, vorher gab es das nicht — `aktion.sock` bekommt
keinen Aufruf, solange kein Werkzeug gerufen wird. Wirkung: **9 rot → 7 rot**
bei **170 statt 149** Prüfungen. Ein angepasstes Kriterium wird strenger, nicht
schwächer; wer nur die Erwartung umschreibt, damit es grün wird, hat eine
Attrappe mit neuem Hash.

**4. `ProtectProc`/`ProcSubset`** (`t311`, K15) — beide wurden mit `7b7deb9`
(T-4.14) absichtlich entfernt: in `--user`-Units wirkungslos laut
systemd.exec(5), ersetzt durch leeren `CapabilityBoundingSet` und benannte
`@resources`-Ausnahmen. Das Kriterium prüft jetzt diese. Das Gut-Muster
(`tests/fixtures/known-good/T-3.11/config/systemd/daimon-{mind,egress}.service`)
trug noch die zwei widerlegten Zeilen und keinen `SystemCallFilter` — es wurde
im selben Zug mitgezogen, mit einer Kommentarzeile, die den Rückbau erklärt.

**5. `T-3.11.sh` stürzte ab, statt rot zu melden.** `ConnectionResetError` an
`ticket.sock` nach 51 Prüfungen; die Kapitel 5 bis 17 liefen nie. In der Bilanz
des umgebenden Laufs sah das aus wie **ein** roter Punkt. Behoben; ein Abbruch
liefert jetzt Rückgabewert **3** — nicht 1 wie rot, nicht 0 wie grün — belegt
mit einer Positivkontrolle. Wirkung: **Absturz nach 51 → 225 Prüfungen, 3 rot**;
Kapitel 15 lief zum ersten Mal überhaupt, mit 43 grünen Prüfungen.

### Was offen bleibt

1. **T-3.11, 3 rot:** alle drei sind eingebettete Prüfstände (`T-3.8`, `T-3.9`,
   `T-3.10`), kein Befund an T-3.11 selbst. Wurzel ist `T-3.8`
   (`ProtectProc`/`ProcSubset` in `T-3.8.sh:563`, dazu `onnxruntime-gpu`);
   `T-3.10` ist nur rot, weil es `T-3.8` einbettet.
2. **`T-3.9` kippt zwischen Läufen** — der Reviewer maß 2 rot, der Hauptfaden
   3. Ursache gemessen: die Sonde zeigt auf den **echten**
   `$XDG_RUNTIME_DIR/daimon/egress.sock`; ein Rest aus einem früheren Lauf
   entscheidet mit. Ein Prüfstand, dessen Ergebnis von der Reihenfolge abhängt,
   misst nicht den Prüfling.
3. **T-3.13b, 7 rot:** drei am Hook-Weg (Kanarienvogel/`rev`, `Stop` meldet
   `sleeping`, Claude-Code-PID), vier über eingebettete Prüfstände.
4. **Dieselbe alte Erwartung steht noch** in `T-0.14.sh:62-63`, `T-3.8.sh:563`,
   `t312_pruefstand.py:686-711` und `t313_pruefstand.py:1009-1012` — gemeldet,
   nicht geändert, jeder mit eigenem Auftrag.
5. **Nebenbefund, dreimal gemessen:** `systemd --user` hält hier kein
   `CAP_SETPCAP`. Eine Nutzer-Unit mit `CapabilityBoundingSet=` und **ohne**
   Mount-Sandboxing startet nicht (`218/CAPABILITIES`); mit `PrivateTmp=` oder
   `ProtectSystem=` daneben geht es.

---

## Nachzug vom 01.09. — die Haertungskriterien, VORLAEUFIG

**Betroffen:** `tests/verify/T-0.14.sh`, `tests/verify/T-3.8.sh`.

Das ist der Punkt 4 aus „Was offen bleibt" des Nachzugs vom 31.08. — dort
gemeldet und ausdrücklich nicht geändert, weil jeder einen eigenen Auftrag
bekam. Hier sind die ersten zwei der vier abgearbeitet.

| Datei | vorher | nachgezogen am 01.09. |
|---|---|---|
| `T-0.14.sh` | `058a1e00…` | `c3189ca8…` |
| `T-3.8.sh` | `6f0f7966…` | `6563e165…` |

### Der Befund, an beiden derselbe

Beide Prüfstände verlangten `ProtectProc=invisible` und `ProcSubset=pid`
**als Text in der Unit-Datei** (`T-0.14.sh:62-63`, `T-3.8.sh:563`). Beide
Direktiven wurden mit `7b7deb9` (21.08., T-4.14) **absichtlich** aus allen 22
Units entfernt: in einer `--user`-Unit sind sie wirkungslos — `systemd.exec(5)`
sagt es zu `ProcSubset=` ausdrücklich („it is only available to system
services") — und systemd nimmt sie trotzdem widerspruchslos an. An der
laufenden Unit gemessen: `ProtectProc=default`, `ProcSubset=all`,
zeichengleich mit einem Lauf ganz ohne die Zeilen. An ihre Stelle trat, was in
`--user` wirklich greift: der leere `CapabilityBoundingSet=` und die
Syscall-Sperrliste mit benannten `@resources`-Ausnahmen.

Damit war es dieselbe Klasse wie am 31.08.: der Code war in Ordnung, der
Prüfstand hielt eine widerlegte Zusage fest. `t311_pruefstand.py` K15 wurde am
31.08. bereits so berichtigt; diese zwei zogen nach.

### Was die Kriterien jetzt prüfen

Nicht den Dateitext, sondern die **Wirkung** — und strenger als vorher:

- die **Abwesenheit** der zwei Direktiven, mit `7b7deb9` als Grund im
  Kriterium selbst, damit sie niemand ein viertes Mal einfriert;
- `systemctl --user show` an der laufenden Unit: `ProtectProc=default` und
  `ProcSubset=all` als **gemessener Beleg** zur Streichung, dazu
  `NoNewPrivileges`, leerer `CapabilityBoundingSet`, `ProtectSystem`,
  `UMask`, `LimitCORE` und die übrigen als wirksam;
- den aufgelösten Seccomp-Filter statt der Sperrzeile: **kein einziger**
  Syscall aus `@privileged` bzw. `@resources` bleibt im wirksamen Filter
  übrig. Die Gruppen werden bei `systemd-analyze syscall-filter` erfragt,
  nicht abgeschrieben — eine abgetippte Liste wäre eine zweite Fassung
  derselben Regel;
- die Gegenrichtung am STT: die **eine** dokumentierte `@resources`-Ausnahme
  (onnxruntime heftet Rechenthreads an Kerne, sonst `status=31/SYS`) muss
  wirklich offen sein. Fällt der Grund weg, wird die Zeile rot und verlangt
  die Sperre zurück;
- `T-0.14.sh` führt den Beleg zusätzlich als **Versuch**: zwei transiente
  Wegwerf-Units zählen fremde PIDs, einmal ohne und einmal mit
  `ProtectProc=invisible`. Gleiche Zahl — die Direktive tut in `--user`
  nichts.

Positivkontrollen an jeder dieser Messungen: die PID-Sonde muss überhaupt
fremde Prozesse sehen, die Gruppen müssen auflösbar sein, der Filter muss
lesbar sein (`read` steht drin). „Kein `@resources`-Syscall übrig" ist sonst
nicht von „nichts gemessen" zu unterscheiden. `T-3.8.sh` weist zudem aus,
wenn die **installierte** Unit gar nicht der geprüfte Baum ist: dann steht
dort „nicht gemessen" statt eines grünen Punktes ohne Messung.

### Mitgezogen, weil sonst rot zu Recht

Das Gut-Muster ist eine Kopie des Quellbaums; ändert sich die Regel, altert es
mit. Alle drei trugen noch die widerlegten Zeilen:
`tests/fixtures/known-good/T-0.14/config/systemd/daimon-{hub,face}.service`
und `tests/fixtures/known-good/T-3.8/config/systemd/daimon-stt.service`. Sie
tragen jetzt an derselben Stelle den Grund des Rückbaus als Kommentar. Die
acht betroffenen Mutanten ebenso — ein Mutant, der eine widerlegte Zeile
mitschleppt, prüft nicht mehr seine eigene Mutation.

### Zahlen

| Prüfstand | vorher | nachher |
|---|---|---|
| `T-0.14.sh` | 179 Prüfungen, 2 rot | **206 Prüfungen, 0 rot** |
| `T-3.8.sh` | 2 rot | **0 rot** |

Ein angepasstes Kriterium wird strenger, nicht schwächer: aus zwei
Textvergleichen wurden 27 zusätzliche Prüfungen, die meisten davon am
laufenden Dienst statt an der Datei.

### Warum von Hand nachgezogen

Wie am 26., 28. und 31.08.: `freeze.sh` verlangt die Spur `echt` grün, und
`T-3.13b` steht bei 2 rot (`T-0.11` und `T-3.13`, beide eingebettet, kein
Befund am Prüfling). Also Hash von Hand nach `tests/verify/FROZEN`, hier als
**VORLAEUFIG** geführt. Der `.v`-Task bleibt für beide offen.

---

## Nachzug vom 01.09. — t312 und t313, VORLAEUFIG

**Betroffen:** `tests/verify/t312_pruefstand.py`, `tests/verify/t313_pruefstand.py`.

Die anderen zwei aus Punkt 4 der Liste „Was offen bleibt" vom 31.08. Damit ist
sie abgearbeitet.

| Datei | vorher | nachgezogen am 01.09. |
|---|---|---|
| `t312_pruefstand.py` | `454708eb…` | `65fe0e63…` |
| `t313_pruefstand.py` | `c47283bb…` | `54625643…` |

### Der Befund

Beide hielten die Zusage der **Phase 3** fest — „keine Aktionen". Seit
**T-4.16 K1** gibt es den werkzeugfähigen Weg, seit **T-8.5** die Absichten
`erinnerung` und `fokus` (acht statt sechs). Das ist dieselbe Klasse wie am
31.08. an `t313b`, wo aus derselben einen Erwartung fünf Prüfungen wurden: der
Prüfstand ist älter als eine bewusste Entscheidung.

### Was die Kriterien jetzt prüfen

Aus je **einer** Erwartung wurden mehrere Prüfungen, gemessen am echten Mind
über echte Sockets:

- ohne Absichtsmarke **abgelehnt** — und kostenfrei;
- ohne Ziel **Rückfrage** — und kostenfrei;
- mit beidem `weg: "aktion"`, mit **genau einem** Ticket und **einem**
  Modellaufruf;
- und `aktion.sock` bekommt **keinen** Aufruf, solange kein Werkzeug gerufen
  wird — diese Prüfung gab es vorher überhaupt nicht.

### Zahlen

| Prüfstand | vorher | nachher |
|---|---|---|
| `T-3.12` | 136 Prüfungen, 8 rot | **177 Prüfungen, 0 rot** |
| `t313` (K1–K9) | 8 rot | **135 Prüfungen, 0 rot** |

K6 wuchs von 14 auf 20 Prüfungen, K9 von 12 auf 52. Wer nur die Erwartung
umschreibt, damit es grün wird, hat eine Attrappe mit neuem Hash.

### Mitgezogen

Gut-Muster und Mutanten bei beiden:
`tests/fixtures/known-good/T-3.12/daimon/mind/router.py`,
`tests/fixtures/known-good/T-3.13/daimon/mind/{answer,router}.py`. `T-3.12`
hat jetzt **sechs** Mutanten, `T-3.13` **sieben** — je einer neu
(`aktion-ohne-absichtsmarke`, dazu `d2-antwort-im-werkzeugprompt` an T-3.13),
alle erkannt.

### Warum von Hand nachgezogen

Derselbe Grund wie im Abschnitt darüber: `freeze.sh` verlangt die Spur `echt`
grün, `T-3.13b` steht bei 2 rot. Der `.v`-Task bleibt für beide offen.

### Stand der Prüfstände am 01.09.

- `T-3.13b`: 23 rot → **2 rot** (`T-0.11` und `T-3.13`, beide eingebettet)
- `T-3.11`, `T-3.8`, `T-0.14`, `T-3.12` einzeln grün
- **Neuer Befund, noch ungemessen:** `T-3.11` ist *eingebettet* rot, *einzeln*
  grün (225 Prüfungen, 0 rot). Im eingebetteten Protokoll steht
  `POSITIVKONTROLLE: der Hauptdienst hat den geprueften Baum im PYTHONPATH
  (war nein)` — die Verschachtelung reicht die Umgebung nicht durch. Kein
  Befund am Prüfling; eigener Faden.

---

## Nicht zuordenbar

Keine. Die Buchführung geht auf: `tests/verify/FROZEN` trug vor dem Aufräumen
86 Kommentarzeilen. Davon

| Anzahl | Herkunft |
|---:|---|
| 1 | Kopfzeile von freeze.sh: `# <sha256> <pfad relativ zum Repo> — von tests/verify/freeze.sh gepflegt` |
| 40 | T-3.13b-Nachzug 26.08. (`02055af`, Zeilen 26–65) |
| 43 | T-3.14-Nachzug 28.08. (die in `6a4802f` hinzugefügten Zeilen) |
| 2 | aus dem 27.08.-Nachzug übernommen und vom 28.08.-Nachzug weiterbenutzt: `# Beide Halte sind aelter als dieser Nachzug und von ihm unabhaengig.` und `#   1. offen: neuer .v-Task fuer T-3.14, der beide Halte aufloest` |
| **86** | |

Jede Zeile wurde durch Mengenvergleich gegen die Git-Fassungen zugeordnet, nicht
durch Lesen geraten; kein Rest blieb übrig.

**Ein Vorgang steht nicht mehr in FROZEN und darum auch nicht oben:** der
T-3.9-Nachzug vom 28.08. (`590f8e5`, Zeilen 106–130). Er ist aufgelöst — T-3.9
ist seit `7b385e6` grün — und seine Zeilen wurden mit demselben Commit aus
FROZEN entfernt.
