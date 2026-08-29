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
