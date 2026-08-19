# Übergabe, 19.08.2026 — Stand `2d4a231` auf `main`

Für die Sitzung von morgen. Geschrieben am Ende eines Tages, an dem eine
Reviewer-Sitzung elf Verifizierer gefahren und fünf Befunde gemeldet hat,
von denen drei erledigt sind.

**Alles ist committet und gepusht.** Uncommitted sind nur zwei Messartefakte
(`tests/evidence/phase1-usage.json`, `phase6-integration.json`) — die ändern
sich bei jedem Lauf, das ist normal.

---

## 1. Wo anfangen

**Zuerst aufräumen, es kostet zehn Minuten und verhindert Doppelarbeit:**

Im PMTool liegen **Doppelkarten**. Fünf Karten vom 18.08. tragen dieselben
Befunde wie fünf von heute:

| alt (18.08.) | neu (19.08.) | Stand |
|---|---|---|
| `5e704593` T-4.6 | `beb6c008` T-4.6 K5 | **behoben** (`925f4b2`) |
| `ebdb563d` T-4.7 | `cdd29c31` T-4.7 K3/K4+K5 | K5 behoben (`d8c5e32`), K3/K4 offen |
| `8c65a9d7` T-4.8 | `6e6c4b55` T-4.8 K7 | Wächter statt Fix (`2d4a231`) |
| `176d8c2e` T-5.9 | `bbe4f9e6` T-5.9 K9 | offen |
| `4b156b51` T-7.4 | `ccc0797c` T-7.4 K3 | Doku entschieden (`f535fc7`) |

Dazu steht `e9bdc15a` („BEFUND T-7.2: behoben") noch im Backlog.

Die neuen Karten tragen die K-Nummern und die Nachträge; die alten sind die
ursprünglichen Meldungen. Zusammenführen oder die alten schließen — **aber
erst nachsehen, ob die alte Karte etwas enthält, das die neue nicht hat.**

---

## 2. Was heute passiert ist

Elf Commits, `c0d0c64..2d4a231`. Die Reviewer-Sitzung hat elf Verifizierer
gegen `main` gefahren, ihre Mutanten geprüft und **sechs eingefroren**
(T-4.4, T-4.5, T-7.1, T-7.2, T-7.3, T-7.5). `FROZEN`: 37 → 52 Einträge.

**Die Frage, die den ganzen Tag trug, ist beantwortet: 116 von 116 Mutanten
erkannt** — darunter die sechs, die genau den Zustand vor dem jeweiligen Fix
herstellen. Kein Fix von gestern war zu freundlich. Das war der Punkt, an dem
die Builder-Sitzung sich selbst nicht traute (`REVIEWER-UEBERGABE-19.08.md`,
Abschnitt 4.1).

Fünf Verifizierer bleiben rot, **keiner „nicht messbar"** — die Umgebung
reichte für alle elf. Der volle Bericht: `docs/REVIEWER-BERICHT-19.08.md`.

Danach am Produkt:

| Commit | Was |
|---|---|
| `c5dbb71` | Produzentensockets bekamen Unit-Allowlisten — die Listen von gestern saßen nur in der *anderen* Annahmefunktion |
| `f535fc7` | STT residiert: `DESIGN.md` und Plan nachgezogen, der Code war richtig |
| `d8c5e32` | `PrivateUsers=yes` aus `daimon-dbus.service` |
| `925f4b2` | gerissene Audit-Kette setzt eine dringende Blase; **mimic am GPU-Gate zugelassen** |
| `2d4a231` | Undo-Broker: Wächter statt Fix (Begründung unten) |

---

## 3. Was offen ist, in der Reihenfolge, die ich nehmen würde

### 3.1 T-4.7 K3/K4 — `xdg-dbus-proxy` startet niemand (`cdd29c31`)

**Der einzige der fünf, den ich heute bewusst nicht angefasst habe.**

`config/dbus-filter.conf` beschreibt in seinem eigenen Kopf einen Aufruf, der
in keiner Unit steht. Über den ganzen Baum gemessen kommt `xdg-dbus-proxy` in
Kommentaren und Dokumenten vor, in **keiner** startenden Codezeile.

**Warum frisch anfangen:** das ist ein Sandbox-Umbau mit fd-Übergabe an eine
Unit. Der dbus-Broker ist die Stelle, an der Modellausgabe zu Fensteraktionen
wird — ein Fehler dort merkt man erst, wenn eine Aktion still nicht mehr
durchgeht. Das braucht einen wachen Kopf und einen Betriebstest mit echter
Aktion, nicht nur `is-active`.

Die Unit läuft derzeit **nicht** (`inactive`, das ist ihr Normalzustand — sie
wird bei Bedarf gestartet). Wer sie zum Testen startet, stoppt sie danach.

### 3.2 T-5.9 K9 — der Umfang fährt in einem redigierten Feld mit (`bbe4f9e6`)

`daimon/hub/declassify.py:195` packt den Umfang in `prompt_shown`, und das
Feld ist als `tainted` deklariert — die Redaktion ersetzt es ganz. Wer später
fragt, *was* freigegeben wurde, findet einen Hash.

Beide Zeilen sind für sich richtig: `prompt_shown` **gehört** redigiert.
Falsch ist, dass ein Pflichtfeld der Akzeptanzliste darin transportiert wird.
Der Fix ist vermutlich ein eigenes, nicht-taintetes Feld — aber das ist eine
Änderung am Auditsatz, und die will überlegt sein.

### 3.3 Die zwei Nebenbefunde von heute

* `3111d6d7` — **`CapabilityBoundingSet=` fehlt vier Units** (dbus, exec, fs,
  input), ausgerechnet den Aktionsbrokern. Je Unit einzeln messen: Zeile
  setzen, Dienst starten, **seine Kernaufgabe ausführen**. Ein „startet" ist
  kein Beleg — der fs-Broker muss danach noch eine Datei anlegen können.
* `725cca5c` — die Liste „Direktiven, die brechen" in `DESIGN.md` mischt
  „bricht immer" mit „bricht unter Umständen" und ist deshalb nicht
  maschinell prüfbar.

### 3.4 Was eine Reviewer-Sitzung braucht, nicht diese

Drei Verifizierer messen gegen Zusagen, die sich heute geändert haben. Sie
sind zu Recht rot und können erst danach einfrieren:

* **T-7.4** — die Akzeptanzliste sagt jetzt „residiert"; der Verifizierer
  misst noch die alte Fassung.
* **T-4.7** — K5 ist behoben, der Verifizierer kennt die neue Unit nicht.
* **T-4.8** — misst eine Zusage, deren Fall es nicht gibt. Ob das „rot" oder
  „nicht anwendbar" ist, gehört entschieden.

`tests/verify/**` ist der builder-Rolle gesperrt. Eine Reviewer-Sitzung
öffnet man so:

```bash
env DAIMON_ROLE=reviewer claude -p "$(cat auftrag.txt)" --dangerously-skip-permissions
```

**Ohne `--dangerously-skip-permissions` läuft sie ins Leere** — `acceptEdits`
erlaubt Dateiänderungen, aber keine Bash-Aufrufe, und dann kann sie keinen
Verifizierer fahren. Der Rollen-Wächter bleibt trotzdem aktiv: `daimon/`,
`face/`, `kwin-script/` und `config/systemd/` sind ihr gesperrt.

**Committe in Schritten.** Heute ist eine Sitzung nach zwei Stunden an einem
abgelaufenen Token gestorben und hätte alles verloren, wenn nicht ein Merge
schon dringewesen wäre.

---

## 4. Zwei Entscheidungen von heute, die man kennen muss

**Der STT residiert.** Der Leerlauf-Exit stand zweimal im Repo,
gegensätzlich. Entschieden zugunsten der Residenz: das Modell liegt auf der
CPU und belegt 0 VRAM, kann also keinen Ladevorgang blockieren — Design §5.4
hat ihn nie gemeint. §5.4 grenzt das jetzt ab.

**`PrivateUsers=yes` ist aus `daimon-dbus.service` raus.** Von den zwei Wegen
aus dem Widerspruch der konservative. Die Messung des Reviewers steht in
`DESIGN.md`: für gleiche uid bricht die Direktive die Peer-Prüfung **nicht** —
gemessen ist nur der Peer-Teil und nur für gleiche uid.

---

## 5. Wo ich mir nicht traue

### 5.1 T-4.8: ich habe den Verifizierer bewusst rot gelassen

`undo=` an den Koordinator zu hängen hätte K7 grün gemacht, ohne dass sich im
Betrieb etwas ändert — der Zweig `if self.undo is not None` liefe weiterhin
nie, weil **keine der 17 Katalogaktionen destruktiv ist**. Stattdessen ein
Wächter, der auffällt, sobald die erste mutierende Aktion kommt.

Das ist eine Auslegung von `CLAUDE.md §6`, und sie kann falsch sein. Wer
anderer Meinung ist: der Wächter steht in `tests/test_undo_zulauf.py` und
lässt sich in zehn Minuten durch die Verdrahtung ersetzen.

### 5.2 Meine Wächter waren heute dreimal zu breit

Erst der Leerlauf-Wächter (34 Treffer im GPU-Worker, der zu Recht einen
Exit hat), dann die Verbotsliste (17 Units mit `MemoryDenyWriteExecute`),
dann der Privatmodus-Wächter (Dateiname und Nachrichtentyp heißen gleich).
Alle drei hätten bei richtigen Zeilen angeschlagen und wären abgeschaltet
worden. Jeder neue Wächter gehört gegen den **Ist-Zustand** gefahren, bevor
man ihm glaubt.

### 5.3 Der Rust-Teil ist nur am Quelltext geprüft

`test_face_mitschnitt_zaehler.py` und `test_privatmodus_zulauf.py` lesen
`face/src/*.rs`. **Kein Test startet das Face** — das bräuchte einen
Compositor. Ob der Menüpunkt „Mitschnitt pausieren" beim Klick wirklich die
Zeile sendet, ist im Repo unbelegt. Ein Klick von Hand würde es klären.

### 5.4 `docs/DEBT.md` driftet ständig

Sechs Zeilendrifts in drei Tagen, jedes Mal vom selben Wächter gefangen und
von Hand nachgezogen. Die Häufigkeit stellt die Bauform infrage:
Zeilennummern in einem Dokument, das beim Editieren niemand ansieht. Eine
Karte dafür gibt es noch nicht.

---

## 6. Systemzustand

`daimon-hub` und `daimon-eyes` laufen, `daimon-face` nicht. Hub und
Hook-Bridge sind heute neu gestartet — sie liefen mit Code von vor zwei
Fixes.

**`daimon-eyes` braucht beim Neustart eine Portal-Zustimmung am Bildschirm.**
Der Integrationstest startet alle Units; wer ihn fährt, sollte danebensitzen.
Heute Mittag stand der Dienst deshalb zwei Stunden.

**`mimic-worker.service` ist seit heute am GPU-Gate zugelassen** — Matthias'
eigener Dienst, der die Ladesperre ordentlich anfragt. Die Allowlist hatte
ihn ausgesperrt, und `worker.py:992` fällt bei `OSError` **offen**: er hat
stundenlang ohne Sperre geladen. Falls weitere fremde GPU-Nutzer auftauchen,
gehören sie in `GPU_UNITS` und in `FREMDE_DIENSTE` in
`tests/test_hub_socket_allowlisten.py`.

Die Suite läuft grün ohne `test_integration.py`; mit ihm auch, aber dann
startet sie Dienste neu.

```bash
.venv/bin/python -m pytest tests/ -q -p no:randomly --timeout=60 --ignore=tests/test_integration.py
```
