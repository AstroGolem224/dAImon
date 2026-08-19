# Übergabe an die Reviewer-Sitzung, 19.08.2026 — Einfrieren

Stand `91e59aa` auf `main`. Geschrieben von der Builder-Sitzung, die alle
Befunde der Reviewer-Sitzung vom 18.08. bearbeitet hat.

**Der Auftrag:** die elf Verifizierer der Phase-4/7-Prüfung nach `main`
bringen, gegen den heutigen Stand fahren, ihre Mutanten gegenprüfen und
einfrieren.

**Warum jetzt und nicht am 18.08.:** die Sammelkarte `13049e55` hat das
Einfrieren ausdrücklich zurückgestellt — „wer sie jetzt festschreibt, friert
auch die offene Frage ein". Die Frage ist entschieden (unten, Abschnitt 3).

---

## 1. Was einzufrieren ist

Elf Verifizierer liegen auf Zweigen und nicht auf `main`:

    tests/verify/T-4.4.sh  T-4.5.sh  T-4.6.sh  T-4.7.sh  T-4.8.sh
    tests/verify/T-5.9.sh  T-7.1.sh  T-7.2.sh  T-7.3.sh  T-7.4.sh  T-7.5.sh

Jeder Zweig `reviewer/p4-T-*v` bringt außerdem Fixtures unter
`tests/fixtures/known-good/`, Mutanten und einen Ledger unter
`tests/evidence/`. `main` hat heute 37 Einträge in `tests/verify/FROZEN` und
52 Verifizierer — die elf oben sind in beiden Zahlen nicht enthalten.

Von den 26 sonst nicht eingefrorenen (`T--1.*`, `T-0.1`–`T-0.6`, …) sagt
diese Übergabe nichts. Sie sind älter und waren nie Teil des Auftrags.

---

## 2. Was seit dem 18.08. passiert ist

Neun Commits, jeder ein Befund. Die Verifizierer waren rot, weil sie recht
hatten:

| Commit | Befund | war |
|---|---|---|
| `c092539` | T-4.4 K8 — `quelle: "parser"` in der Nachricht umging die Vorschau | produktdefekt-rot |
| `590ee02` | T-4.5 K6 — Broker-Sockets prüften den Absender gar nicht | produktdefekt-rot |
| `6cdf0e4` | T-7.1 K1 — `daimon-fs` durfte ins Archiv schreiben | produktdefekt-rot |
| `b41cd41` | T-7.3 K5+K9 — Konferenzliste nicht konfigurierbar; Face zählte den Mitschnitt-Indikator nicht | rot |
| `ae7c72e` | T-7.2 K8 — Denylist löste die `.desktop`-Kennung nur an einer von drei Stellen auf | produktdefekt-rot |
| `d521148` | T-7.3 K8 — die Pause schloss das Bild und ließ den Ton offen | produktdefekt-rot |
| `a58d08b` | Designtext: die Peer-Prüfung ließ sich zweimal lesen | — |
| `d5c012b` | T-4.5 ZUSÄTZLICH — vier Hub-Endpunkte ohne Unit-Allowlist | — |
| `91e59aa` | Karte 62b90c95 — der Privatmodus war nicht einschaltbar | — |

Dazwischen zwei Befunde, die **beim Beheben** entstanden sind und keinen
Verifizierer hatten:

* **`fe4a33a` — `ipc._unit` traf die richtige Unit nur bei gerader
  Segmentzahl.** Der Regex konsumierte den trailing `/` und übersprang jedes
  zweite cgroup-Segment. Bei `daimon-fs.service` kam zufällig das Richtige
  heraus, bei der Template-Instanz `daimon-gpu@sonde.service` die Slice
  darüber. **Diese Funktion trägt jede Peer-Prüfung im Projekt** —
  `KONTEXT_UNITS` (T-5.9b), `ART_JE_UNIT` im Recorder, die neuen
  Hub-Allowlisten. Sie hatte keinen einzigen eigenen Test.
* **Der Integrationstest maß einen Prozess, nicht das Repo.** T-4.4 wurde um
  00:24 committet, der Hub-Prozess lief bis 10:07 mit dem alten Code weiter.
  Zehn Stunden lang war ein Szenario grün, das `quelle: parser` schickt — den
  Weg, den jener Commit geschlossen hat.

---

## 3. Die entschiedene Frage

Die Sammelkarte nannte zwei Stellen, die sich beide auf Design §1.3 beriefen
und Gegenteiliges sagten. Entschieden zugunsten des Hubs, und zwar nicht per
Abwägung, sondern weil `DESIGN.md` dreimal dasselbe sagt — einmal davon in
der **Tabelle der Irrtümer** („`SO_PEERCRED` verhindert Fälschung").

`common/order.py` war die Attrappe und ist korrigiert. In `DESIGN.md` steht
seit `a58d08b` ausdrücklich, was daraus folgt: **der Auftrag hat keinen
Herkunftsnachweis, der einem same-uid-Angreifer standhält, und ein Ersatz für
den gestrichenen HMAC ist nicht vorgesehen.** Wer einen einführt, ändert
zuerst das Bedrohungsmodell.

Die Peer-Prüfung bleibt als **Wegweiser** — sie fängt einen falsch
verdrahteten eigenen Dienst und macht sichtbar, wer gefragt hat.

---

## 4. Wo ich mir nicht traue

**Das ist der Kern dieser Übergabe.** Jeder Punkt unten ist eine Stelle, an
der die Builder-Sitzung ihre eigene Arbeit geprüft hat.

### 4.1 Ich habe gegen die Verifizierer gebaut

Bei T-4.4 und T-4.5 habe ich den Verifizierer gefahren, den Fix gebaut und
ihn wieder gefahren, bis er grün war. Das ist der richtige Ablauf und
zugleich seine Schwäche: ein Fix, der genau die gemessene Stelle schließt,
kann die Zusage daneben verfehlen. **Bitte die Kriterien lesen, nicht nur den
Ausgang.**

### 4.2 Die Allowlisten sind gemessen, aber nicht erschöpfend

`AKTION_UNITS`, `TICKET_UNITS`, `GPU_UNITS`, `TTS_UNITS` in
`daimon/hub/daemon.py` sind aus dem Quelltext erhoben — wer eine Zeile an den
Socket schickt. Was ich **nicht** ausschließen kann: ein Absender, der den
Socketpfad zusammensetzt statt ihn zu nennen. Meine Suche ging über den
Dateinamen.

`daimon-mind.service` steht bewusst **nicht** auf `AKTION_UNITS` — es gibt
keinen Erzeuger von Aktionsbitten im Quelltext. Sollte die Prüfung einen
finden, ist das ein Befund und keine vergessene Zeile.

### 4.3 Der Instanzvergleich ist neu und breit

`ipc.unit_erlaubt` versteht einen Eintrag, der auf `@` endet, als
Instanzpräfix — `daimon-gpu@` erlaubt jede Instanz. **Der Instanzteil wird
nicht geprüft.** Ich halte das für vertretbar (wer Units anlegen kann, läuft
schon unter dieser uid), aber es ist eine Entscheidung, die ich allein
getroffen habe.

### 4.4 Zwei Szenarien des Integrationstests sind umgebaut

* Die **Folgeaktion** ist ausgebaut, nicht repariert: sie ging über `quelle:
  parser`. An ihrer Stelle steht ein Wächter
  (`test_der_aktionsweg_hat_heute_keinen_erzeuger`). Die Wirkungsmessung über
  `wpctl` gibt es nicht mehr — der Weg braucht jetzt eine bestätigte
  Vorschau, und die gibt ein Mensch.
* Das **Vollbild-Gate** geht jetzt über den echten Worker
  (`daimon-gpu@sonde.socket`) statt über eine nachgespielte Zeile.

Beides sind Zusagen, die im Repo **anders** belegt sind als vorher. Wenn
`tests/verify` sie strenger misst, gilt der Verifizierer.

### 4.5 Der Rust-Teil ist nur am Quelltext geprüft

`test_face_mitschnitt_zaehler.py` und `test_privatmodus_zulauf.py` prüfen
`face/src/*.rs` textuell bzw. am Rückgabetyp. **Kein Test startet das Face**
— das bräuchte einen Compositor. Ob der Menüpunkt „Mitschnitt pausieren" beim
Klick wirklich die Zeile sendet, ist im Repo unbelegt.

### 4.6 Der Privatmodus hat keine Betriebsmessung

Ich habe die Kette Menü → Hub → Datei → `urteil_ton` im Prüfstand belegt,
aber **nie am laufenden System geklickt**. Der Weg über `face.sock` ist
zwischen Rust und Python nur über den Typnamen gekoppelt.

### 4.7 Vier Zeilendrifts im Schuldenzettel an drei Tagen

`docs/DEBT.md` zeigte viermal ins Leere, weil sich Code darunter bewegt hat —
jedes Mal vom selben Wächter gefangen und von mir nachgezogen. Die Häufigkeit
stellt inzwischen die Bauform infrage: Zeilennummern in einem Dokument, das
niemand beim Editieren ansieht. **Das ist kein Befund für diese Sitzung,
sondern eine Anmerkung.**

---

## 5. Was die Sitzung prüfen sollte, bevor sie einfriert

1. **Jeder der elf gegen `main` @ `91e59aa`.** Alle Befunde sind behoben; ein
   Verifizierer, der jetzt rot ist, hat etwas gefunden, das ich übersehen
   habe.
2. **Die Mutanten.** Ein Verifizierer, der seine Mutanten nicht mehr erkennt,
   ist grün geworden, weil er blind wurde — nicht weil das Produkt gesund
   ist. Das ist die einzige Prüfung, die einen zu freundlichen Fix aufdeckt.
3. **Die Positivkontrolle je Verifizierer:** läuft er gegen das Gut-Muster
   grün und gegen den Mutanten rot? Vier Falschbefunde an einem Tag kamen
   genau daher, dass „nichts gefunden" nicht von „nicht gemessen" zu
   unterscheiden war.
4. **Erst dann `tests/verify/freeze.sh`.** Was rot bleibt, wird **nicht**
   eingefroren — ein eingefrorener roter Verifizierer ist eine festgeschriebene
   Lüge.

**Nicht einfrieren, was nicht gefahren wurde.** Wenn eine Umgebung fehlt
(kein Compositor, kein Modell, keine GPU), gehört das in den Ledger und der
Verifizierer bleibt offen.
