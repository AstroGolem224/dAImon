# LEDGER T-4.8.v — Verifizierer für den Undo-Broker mit Verifikation

**Ausgang 18.08.: `produktdefekt-rot`**
**Ausgang 19.08.: `produktdefekt-rot` — UNVERAENDERT. NICHT eingefroren.**
Siehe §Nachlauf am Ende.

Der Verifizierer ist gebaut, gegen das Gut-Muster grün (53 Prüfungen in sieben
Kriterien), gegen alle zwölf Mutanten rot, und gegen den Arbeitsbaum rot an
**einem** von sieben Kriterien — mit Beleg.

Die zentrale Zusage hält, und zwar gemessen: **schlägt die Vorbereitung fehl,
wird die Mutation abgebrochen.** Drei erzwungene Fehlerfälle (echtes volles
Dateisystem, Trash über die Dateisystemgrenze, `git stash` im Merge-Konflikt),
und nach jedem steht die Ursprungsdatei byteweise unangetastet da, während der
Broker nie gerufen wurde. Daneben läuft je ein Zwilling, in dem dieselbe Naht
mit gelungenem Artefakt DURCHGEHT und die Datei wirklich mutiert — sonst wäre
„abgebrochen" von „lief gar nicht" nicht zu unterscheiden.

Was nicht hält, ist der **Zulauf**. `daimon/brokers/fs/undo.py` hat im
Produktivbaum **keinen einzigen Aufrufer**: `HubKoordinator(...)` in
`daimon/hub/daemon.py:643` bekommt kein `undo=`, also ist `self.undo is None`
und der ganze Undo-Hop in `coordinator.py:187` wird übersprungen. Die Zusage
„fällt das Undo, fällt die Aktion" ist gebaut, geprüft — und hat im Betrieb
keinen Fall, in dem sie greift.

---

## Provenienz

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Worktree | `/mnt/data/AI/repos/dAImon-t48` |
| Branch | `reviewer/p4-T-4.8v` |
| Ausgangs-Commit | `ea61a13` |
| Gelesen | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` T-4.8 (Z. 1273–1284) · `daimon/brokers/fs/undo.py`, `daimon/hub/coordinator.py`, `daimon/hub/daemon.py`, `daimon/auth/modal.py`, `daimon/auth/agent.py`, `daimon/brokers/fs/daemon.py` · `tests/test_coordinator.py`, `tests/test_undo.py` · `tests/verify/T-4.5.sh` + `t45_pruefstand.py`, `tests/verify/T-4.7.sh` + `t47_pruefstand.py` + `tests/mutants/T-4.7/erzeugen.sh` als Formvorlagen · `tests/verify/t73_pruefstand.py` (Vorschalter-Bauform) · `tests/evidence/LEDGER-T-4.5.v.md`, `LEDGER-T-4.7.v.md`, `LEDGER-T-7.3.v.md` · `meta.sh`, `.claude/roles.toml` |
| Neue Artefakte | `tests/verify/T-4.8.sh`, `tests/verify/t48_pruefstand.py`, `tests/verify/t48_naht.py`, `tests/fixtures/known-good/T-4.8/` (22 Dateien, 190 KB), `tests/mutants/T-4.8/{erzeugen.sh,.gitignore}`, dieses Ledger |
| sha256 | `T-4.8.sh` `2083507a835cfc…` · `t48_pruefstand.py` `027ee12340fab4…` · `t48_naht.py` `d8457d2c3faa8f…` · `erzeugen.sh` `fd0d7f6944f0e8…` |
| Commits | `27f60f8` Gerüst · `7bac77f` Prüfstand · `549ab02` Mutanten · `fe104c8` Bilanz je Kriterium |
| `freeze.sh` | **nicht aufgerufen** |
| Fremde Tasks | `T-3.*`, `T-4.4`–`T-4.7`, `T-5.*`, `T-7.*` unberührt — `git status` listet ausschließlich neue `T-4.8`-Pfade |
| Laufzeit | 0,6 s je Lauf (Gut-Muster wie Arbeitsbaum); `meta.sh` mit zwölf Mutanten unter 15 s |

---

## Was gemessen wird

Sieben Kriterien: sechs für die sechs Akzeptanzpunkte, eines für den Zulauf.
Jedes rechnet einzeln ab; ein Kriterium ohne eine einzige Messung zählt als
rot, und eine Messung, die **wirft**, zählt ebenfalls als rot (nicht als
abwesend).

| | Akzeptanzpunkt | Prüfungen (Gut-Muster) |
|---|---|---|
| K1 | Löschen → XDG-Trash mit korrektem `.trashinfo` | 7 |
| K2 | Überschreiben → `cp --reflink` in die Undo-Ablage | 6 |
| K3 | Git-Verwerfen → **vorher** `git stash` | 4 |
| K4 | Artefakt wird nach dem Anlegen **verifiziert** | 7 |
| K5 | **Vorbereitung fehlgeschlagen → Mutation abgebrochen** | 20 |
| K6 | Herabstufung auf `reversible` erst nach der Verifikation | 5 |
| K7 | Der Zulauf: wer stellt das Artefakt im Betrieb her? | 4 |

### K5 — der Kern, und warum er 20 Messungen und fünf Läufe braucht

Der Plan verlangt es wörtlich: *in **jedem** Fall muss die Ursprungsdatei
unverändert sein*. Gemessen wird das an der **Naht**, nicht am Modul: der echte
`Koordinator` (T-4.16) mit echter Policy, echtem Consent, echtem Auftragsbuch,
echter Schlange und echtem Audit, `undo=` auf das echte
`undo.vorbereiten` gelegt — und einem Broker, der die Datei **wirklich
mutiert**. Ein Broker, der nichts täte, könnte „Ursprungsdatei unverändert"
nicht von „Mutation abgebrochen" trennen.

Je Fall drei Abrechnungen, in dieser Reihenfolge:

1. **Ist der erzwungene Fehler überhaupt eingetreten?** Steht die erwartete
   Spur nicht in der Antwort, wird **laut abgebrochen** — „nichts gemessen"
   ist dann der Befund, nicht „grün".
2. **Wurde abgebrochen?** `broker_gerufen == 0` und `ausgefuehrt == False`.
3. **Und die Ursprungsdatei?** sha256 vor dem Lauf gegen sha256 nach dem Lauf,
   je ein Zeitpunkt, kein Fenster.

```
ok   [K5] POSITIVKONTROLLE: mit gelungenem Artefakt geht die Mutation DURCH
          -- 'abgebrochen' ist damit unterscheidbar von 'lief nicht'
     .... gewogen: sha256 der Kanarienvogel-Datei  'b1ba6123…' -> '82c94556…'
ok   [K5] und die Wirkung steht in der Datei
ok   [K5] voll(tmpfs): der erzwungene Fehler ist eingetreten (No space left on device)
ok   [K5] voll(tmpfs): die Mutation wurde ABGEBROCHEN, der Broker nie gerufen
ok   [K5] voll(tmpfs): die Ursprungsdatei ist unveraendert (8147ae912666a913)
ok   [K5] voll(tmpfs): und in der Ablage liegt kein halbes Artefakt
ok   [K5] voll(rlimit): der erzwungene Fehler ist eingetreten (Kopie nach)
ok   [K5] voll(rlimit): die Mutation wurde ABGEBROCHEN, der Broker nie gerufen
ok   [K5] voll(rlimit): die Ursprungsdatei ist unveraendert (adc64af8ecfd39e9)
ok   [K5] voll(rlimit): die Schranke hat gegriffen -- der Schreibvorgang endet bei 4096 Bytes
ok   [K5] grenze: eingespeist -- st_dev 39 gegen 26
ok   [K5] grenze: der erzwungene Fehler ist eingetreten (anderen Dateisystem)
ok   [K5] grenze: die Mutation wurde ABGEBROCHEN, der Broker nie gerufen
ok   [K5] grenze: die Ursprungsdatei ist unveraendert (e7966908356742be)
ok   [K5] grenze: die Datei liegt noch an ihrem Ort -- nicht halb kopiert, halb geloescht
ok   [K5] konflikt: eingespeist -- `git status` sagt 'UU arbeit.txt'
ok   [K5] konflikt: der erzwungene Fehler ist eingetreten (git stash fehlgeschlagen)
ok   [K5] konflikt: die Mutation wurde ABGEBROCHEN, der Broker nie gerufen
          (Grund: undo: git stash fehlgeschlagen: error: could not write index)
ok   [K5] konflikt: die Ursprungsdatei ist unveraendert (b7b85aa7fc9492ba)
ok   [K5] konflikt: und es liegt kein Stash-Eintrag da, der eine Sicherung behauptete (0)
```

### Das volle Dateisystem — ohne `sudo`, ohne fremdes Dateisystem

Der Plan schlägt „per kleinem tmpfs" vor; das braucht üblicherweise root.
Gefahren werden stattdessen **zwei Gleise**, beide ohne Rechteerhöhung:

* **`unshare -U -r -m`** spannt einen eigenen Benutzer- und Mount-Namensraum
  auf (`kernel.unprivileged_userns_clone = 1` auf dieser Maschine, kein setuid,
  keine Fähigkeit außerhalb des Namensraums), und darin trägt ein `tmpfs` mit
  **64 KiB** die Undo-Ablage. Die Ursprungsdatei (512 KiB) liegt außerhalb.
  `cp` bekommt damit ein **echtes ENOSPC vom Kernel** —
  `cp: error writing '…': No space left on device`. Der Mount ist im
  Namensraum eingesperrt und verschwindet mit dem Prozess. **Kein Dateisystem
  des Nutzers wird angefasst, keines gefüllt** — nicht `/`, nicht `$HOME`,
  nicht `/run/user/1000`.
* **`RLIMIT_FSIZE = 4096`** im Kindprozess, ganz ohne Namensraum. Belegt wird,
  dass die Schranke gegriffen hat: das abgebrochene Artefakt endet auf **genau
  4096 Bytes**.

Zwei Gleise, weil das erste an einer Kernel-Einstellung hängt, die woanders
`0` sein kann. Fällt der Namensraum aus, sagt der Prüfstand das ausdrücklich
(`rc=97`) und zählt es als **rot**, nicht als grün.

### K1 — `.trashinfo` auf INHALT, nicht auf Existenz

Ein Papierkorbeintrag ohne richtigen `Path=` ist nicht wiederherstellbar —
weder von Hand noch von einem Dateimanager. Geprüft werden deshalb:

* der Kopf `[Trash Info]`,
* `Path=` **url-dekodiert gegen den absoluten Ursprungspfad**,
* `DeletionDate=` als Zeitpunkt im Format der Spezifikation, **exakt** gegen
  einen eingespeisten Stempel (`jetzt=1_700_000_000.0` → `2023-11-14T23:13:20`),
* und in einem zweiten Lauf **ohne** Einspeisung: der Stempel muss zwischen
  den beiden Uhrablesungen liegen, die den Aufruf einklammern — er kommt also
  aus der Uhr und nicht aus einer Konstante.

Davor steht die **Negativkontrolle des Lesers**: ein Zettel mit bloßem
Dateinamen und Epoch-Datum muss verworfen werden. Sonst wäre jedes spätere
„stimmt" wertlos.

### K3 — „vorher" ist eine Reihenfolge, keine Behauptung

Im Wegwerf-Repo wird eine ungesicherte Änderung angelegt, die Naht gefahren,
und dann gemessen, **was im Stash steht**: `git show stash@{0}:arbeit.txt` muss
den Stand **vor** der Mutation tragen. Läge der Stash hinter der Mutation,
enthielte er `MUTIERT-DURCH-DEN-BROKER`. Danach `git stash pop` — und die Datei
trägt wieder ihren alten sha256.

### K2 — `--reflink` am ARGV gemessen

Der Vorschalter sieht den echten Aufruf:

```
cp	--reflink=auto --preserve=all --no-clobber /tmp/t48-…/k2/bericht.md /tmp/t48-…/k2/undo/bericht.md.1787051…
```

Nicht der Docstring belegt `--reflink`, sondern die Kommandozeile, die
tatsächlich abgesetzt wurde.

---

## Der Vorschalter — die zweite Reihe, über den ganzen Lauf

Am 18.08. hat ein Prüfstand dieser Serie zwei echte Dienste GESTARTET, weil
eine Einspeisung still verlorenging. Daraus die Auflage, hier umgesetzt:

* Ein eigenes PATH-Verzeichnis hängt **vom ersten bis zum letzten Schritt** im
  `PATH`, auch in jedem Kindprozess (die Naht, die Namensraum-Hülle).
* **Durchlassend, aber nur unterhalb des Arbeitsverzeichnisses:** `cp`, `git`,
  `rm`, `mv`. Jedes absolute Argument außerhalb → `exit 99` und eine Zeile im
  Protokoll.
* **Immer sperrend:** `gio`, `trash`, `trash-put`, `systemctl`. Kein Pfad
  dieses Laufs darf sie brauchen; ruft der Prüfling sie, ist das ein Befund
  und kein Papierkorbeintrag im Konto des Nutzers.
* **Vor dem Eingriff gewogen, dass die Einspeisung sitzt:** `shutil.which()`
  muss für jedes der acht Werkzeuge auf den Vorschalter zeigen, `cp --t48-probe`
  muss mit `rc=42` und der Marke antworten, und ein `gio trash /etc/passwd`
  muss mit `rc=99` zurückgewiesen werden. Sitzt sie nicht: **Abbruch mit
  `rc=2`**, nicht weitermessen.
* Der lügende `cp` (K4) ist **dieselbe** Datei, per Umgebungsvariable
  geschaltet — zwei Fassungen wären eine Regel und eine Attrappe.

```
VORSCHALTER: 4 durchlassend, 4 sperrend, geprueft an `cp --t48-probe` (rc=42)
             und an einem `gio trash /etc/passwd` (rc=99)
…
VORSCHALTER-PROTOKOLL: 48 Aufruf(e) {'cp': 9, 'gio': 1, 'git': 38}
     | cp	--t48-probe
     | gio	PROBE	trash /etc/passwd
     | cp	--reflink=auto --preserve=all --no-clobber …
     | git	-C /tmp/t48-…/k3/repo stash push --include-untracked -m daimon-undo
     …
VORSCHALTER: kein Aufruf ausserhalb des Arbeitsverzeichnisses, keiner an
             `gio`/`trash`/`systemctl`.
```

Alle 48 Aufrufe zeigen unter `/tmp/t48-<zufall>/`. Das Protokoll wird am Ende
**vollständig ausgegeben**, nicht nur zusammengefasst.

---

## Kann er den Fehler sehen? — die Mutantenmatrix

> Welche Zeile im Produktivcode müsste kaputt sein, damit er rot wird — und
> hast du es ausprobiert?

**Zwölf Stellen. Ja, alle zwölf, einzeln gemessen.**

| Mutant | kaputte Stelle | zugeordnet | tatsächlich rot bei |
|---|---|---|---|
| `trashinfo-nur-name` | `undo.py:123` — `Path=` trägt nur `quelle.name` | K1 | **K1** |
| `trashinfo-falscher-name` | `undo.py:115` — Zettel heißt nach `ziel.stem` | K1 | **K1** |
| `kopie-ohne-reflink` | `undo.py:143` — `cp` ohne `--reflink=auto` | K2 | **K2** |
| `kopie-verschiebt` | `undo.py:143` — `mv` statt `cp` | K2 | **K2**, K4, K5 |
| `stash-nicht-vorher` | `undo.py:165` — `git stash push` entfällt, Erfolg behauptet | K3 | **K3**, K5 |
| `stash-zaehlt-nicht` | `undo.py:175` — Zählung der Stash-Einträge entfällt | K4 | **K4** |
| `verifikation-fort` | `undo.py:73` — `_verifizieren` tut nichts | K4 | **K4** |
| `groesse-egal` | `undo.py:78` — nur Lesbarkeit, keine Größe | K4 | **K4** |
| `trash-grenze-egal` | `undo.py:100` + `:125` — Grenzprüfung fort, `shutil.move` | K5 | **K5** |
| `undo-fehler-verschluckt` | `undo.py:146` — `cp`-Fehlschlag → Artefakt statt Ausnahme | K5 | **K5** |
| `verifiziert-vor-der-pruefung` | `undo.py:152` — `verifiziert=True` vor `_verifizieren` | K6 | **K6** |
| `zulauf-fort` | `hub/daemon.py:643` — `undo=` am Koordinator fort | K7 | **K7** |

Neun von zwölf treffen **genau** ihr Kriterium. Die drei breiten Treffer sind
notiert, nicht wegdefiniert:

* `kopie-verschiebt` nimmt dem Undo die Datei, die es sichern soll — damit
  fällt auch die Positivkontrolle von K4 und der Fehlerfall von K5.
* `stash-nicht-vorher` behauptet ein Artefakt, das es nie gab; K5s
  Konfliktfall sieht denselben Riss von der anderen Seite.
* `undo-fehler-verschluckt` lässt die Mutation ungeschützt laufen — genau der
  Fall, gegen den K5 gebaut ist, und nebenbei fällt K4s Gleis a.

Die Mutanten werden **erzeugt, nicht eingecheckt** —
`tests/mutants/T-4.8/erzeugen.sh` plus `.gitignore`, gerufen von `meta.sh`
selbst. Jeder Anker muss genau so oft im Gut-Muster stehen wie deklariert,
sonst bricht die Erzeugung ab.

**Zwei Mutanten hat der Prüfstand zuerst NICHT gesehen** — beide sind
Befunde über den Verifizierer, beide behoben und hier notiert:

* `stash-zaehlt-nicht` starb ursprünglich an einem `IndexError` statt an der
  Prüfung. „Rot" wäre er gewesen, gemessen hätte niemand etwas. K4 verlangt
  jetzt, dass der Abbruchgrund den **Stash** nennt; ein Stolpern zählt nicht
  als Erkennen. Dieselbe Verschärfung gilt für Gleis a (der Grund muss die
  **Größe** nennen).
* Die Reihenfolgeprüfung von K6 las anfangs die *letzte* statt der *ersten*
  Herabstufung und hielt einen vertauschten Köder für richtig. Aufgefallen ist
  das an der Positiv-/Negativkontrolle des Lesers, die vor jeder Messung läuft.

---

## Belege

### `meta.sh` — Gut-Muster grün, zwölf Mutanten rot

```
$ bash tests/verify/meta.sh T-4.8
meta[T-4.8]: Mutanten werden erzeugt (…/tests/mutants/T-4.8/erzeugen.sh) ...
T-4.8: 12 Mutanten erzeugt.
meta[T-4.8]: Gut-Muster ...
meta[T-4.8]: Mutante 'groesse-egal' erkannt.
meta[T-4.8]: Mutante 'kopie-ohne-reflink' erkannt.
meta[T-4.8]: Mutante 'kopie-verschiebt' erkannt.
meta[T-4.8]: Mutante 'stash-nicht-vorher' erkannt.
meta[T-4.8]: Mutante 'stash-zaehlt-nicht' erkannt.
meta[T-4.8]: Mutante 'trash-grenze-egal' erkannt.
meta[T-4.8]: Mutante 'trashinfo-falscher-name' erkannt.
meta[T-4.8]: Mutante 'trashinfo-nur-name' erkannt.
meta[T-4.8]: Mutante 'undo-fehler-verschluckt' erkannt.
meta[T-4.8]: Mutante 'verifikation-fort' erkannt.
meta[T-4.8]: Mutante 'verifiziert-vor-der-pruefung' erkannt.
meta[T-4.8]: Mutante 'zulauf-fort' erkannt.
meta[T-4.8]: 12 Mutanten, alle erkannt.
$ echo $?
0
```

### Zuordnung Mutant → Kriterium, einzeln gemessen

```
$ for m in tests/mutants/T-4.8/*/; do … done
groesse-egal                   -> FAIL K4
kopie-ohne-reflink             -> FAIL K2
kopie-verschiebt               -> FAIL K2 K4 K5
stash-nicht-vorher             -> FAIL K3 K5
stash-zaehlt-nicht             -> FAIL K4
trash-grenze-egal              -> FAIL K5
trashinfo-falscher-name        -> FAIL K1
trashinfo-nur-name             -> FAIL K1
undo-fehler-verschluckt        -> FAIL K5
verifikation-fort              -> FAIL K4
verifiziert-vor-der-pruefung   -> FAIL K6
zulauf-fort                    -> FAIL K7
```

### Gegen das Gut-Muster — grün

```
$ DAIMON_FIXTURE=$PWD/tests/fixtures/known-good/T-4.8 tests/verify/T-4.8.sh; echo $?
…
Bilanz T-4.8:
K1: 7 Pruefungen, 0 rot
K2: 6 Pruefungen, 0 rot
K3: 4 Pruefungen, 0 rot
K4: 7 Pruefungen, 0 rot
K5: 20 Pruefungen, 0 rot
K6: 5 Pruefungen, 0 rot
K7: 4 Pruefungen, 0 rot
T-4.8: GRUEN -- alle 7 Kriterien gemessen und erfuellt
0
```

### Gegen den Arbeitsbaum — rot an K7

```
$ tests/verify/T-4.8.sh; echo $?
ok   [K7] KONTROLLE des Lesers: er sieht eine Verdrahtung, wenn es eine gibt,
          und keine, wenn sie fehlt -- den Koordinator findet er in beiden Faellen
     .... Zulauf: {'koordinatoren': 1, 'verdrahtet': [], 'aufrufe': []}
ok   [K7] im Baum wird 1x ein Koordinator gebaut
FAIL [K7] KEIN Koordinator bekommt ein `undo=` gereicht: `self.undo is None`,
          der Undo-Hop wird uebersprungen. Die Zusage 'schlaegt die Vorbereitung
          fehl, wird die Mutation abgebrochen' hat im Betrieb keinen Fall, in
          dem sie greift -- es wird nie etwas vorbereitet
FAIL [K7] und niemand ruft `vorbereiten`/`in_den_trash`/`kopie_anlegen`/
          `stash_anlegen` -- das Modul hat ausserhalb der Tests keinen einzigen
          Aufrufer

Bilanz T-4.8:
K1: 7 Pruefungen, 0 rot
K2: 6 Pruefungen, 0 rot
K3: 4 Pruefungen, 0 rot
K4: 7 Pruefungen, 0 rot
K5: 20 Pruefungen, 0 rot
K6: 5 Pruefungen, 0 rot
K7: 4 Pruefungen, 2 rot
T-4.8: ROT -- 1 von 7 Kriterien rot: K7
1
```

**Sechs von sieben Kriterien sind gegen den Arbeitsbaum grün** — und darunter
ist der Kern: die drei erzwungenen Fehlerfälle, in jedem die Ursprungsdatei
unangetastet. `undo.py` selbst hält, was T-4.8 zusagt.

### Unabhängig vom Prüfstand nachgesehen

```
$ grep -rn "undo=" daimon/
KEIN Treffer
$ grep -rn "import undo\|from .* import.*undo\b" daimon/
KEIN Treffer
$ grep -rn "undo" daimon/hub/daemon.py
(nichts)
$ grep -rn "braucht_modal" daimon/ | grep -v "def braucht_modal"
daimon/auth/modal.py:109:    Hub importiert dieses Modul nur fuer `braucht_modal`, und ein
```

Die letzte Zeile ist Prosa im Docstring, kein Aufruf.

---

## Der Befund

### 1. Der Undo-Broker hat keinen Aufrufer (K7)

`daimon/hub/coordinator.py:187` hat den Hop und die richtige Reihenfolge:

```python
        # 2. Undo VOR der Mutation. Faellt es, faellt die Aktion.
        if self.undo is not None:
            …
```

`daimon/hub/daemon.py:643` baut den einzigen `HubKoordinator` des Betriebs —
mit `policy`, `consent`, `auftragsbuch`, `schlange`, `audit`, `broker`,
`vorschau`, `sprechen`. **Ohne `undo=`.** Damit ist `self.undo is None`, der
Zweig wird übersprungen, und jede Aktion, die den Hub passiert, läuft ohne
Artefakt.

Das ist exakt die Bauform, die `CLAUDE.md` als teuersten wiederkehrenden
Fehler dieses Repos führt (Ticketbuch, Deklassifizierungs-Gate, Kontextspeicher,
DRM-Sperre, `daimon-mind.service`, `declassify.referenzen()`): gebaut,
dokumentiert, mit grünen Tests belegt — und im Betrieb ruft es niemand auf.
`tests/test_undo.py` (12 Tests) und
`tests/test_coordinator.py:123` messen beide Seiten der Naht, aber nie ihre
Verbindung: der Koordinatortest reicht eine **Attrappe** herein
(`def undo(**kw): raise RuntimeError("Dateisystem voll")`), und der Undo-Test
ruft das Modul selbst.

**Was das praktisch heißt:** Sobald ein Broker eine zerstörerische Operation
bekommt, mutiert der Weg über den Hub ohne Sicherung. Heute trifft das
niemanden — der Katalog `config/actions/core.yaml` führt **keine einzige**
Aktion mit `destructive: true` und keine `fs.*`-Aktion —, aber genau dann ist
der Riss billig zu schließen und fällt bei der ersten destruktiven Aktion
niemandem mehr auf.

### 2. Nebenbefunde (nicht als Kriterium gemessen)

* **Die Herabstufung hat ebenfalls keinen Konsumenten.**
  `daimon/auth/modal.py:65` (`braucht_modal`) ist die Stelle, die entscheidet,
  ob ein verifiziertes Artefakt den leiseren Weg erlaubt. Sie wird im
  Produktivbaum **nirgends gerufen**: `daimon/auth/agent.py:361 ff.` zeigt für
  **jede** Rückfrage `modal.zeigen(...)`, ohne den Katalogeintrag oder ein
  Artefakt zu befragen. K6 misst die Regel (und sie stimmt), nicht ihren
  Zulauf — der gehört zu T-4.12.
* **`daimon/brokers/fs/daemon.py:21` sagt es selbst:** `OPERATIONEN` führt
  bewusst kein `loeschen`, „solange den niemand gebaut hat". Das ist die
  ehrliche Fassung von Befund 1 — der FS-Broker verzichtet lieber auf die
  Fähigkeit, als sie ohne Artefakt anzubieten. Nur schließt der Hub die Lücke
  nicht: er würde jede künftige destruktive Operation ungesichert durchreichen.
* **`in_den_trash` legt `files/` und `info/` an, bevor es die
  Dateisystemgrenze prüft** (`undo.py:96–100`). Wird die Funktion ohne
  explizites `trash=` gerufen, entstehen die Verzeichnisse im **echten
  Papierkorb des Nutzers**, auch wenn der Aufruf gleich darauf abgewiesen
  wird. Harmlos, aber eine Nebenwirkung auf einem Verweigerungspfad. (Der
  Prüfstand ruft ausschließlich mit explizitem `trash=` unterhalb seines
  Arbeitsverzeichnisses.)
* **`_trash_wurzel(ziel)` ignoriert sein Argument** und liefert immer den
  Home-Trash. Das ist konsistent mit der Entscheidung, die Grenze abzuweisen
  statt `$topdir/.Trash-$uid` zu bedienen — der Parameter ist dann aber
  irreführend.
* **`cp --no-clobber` meldet 0, wenn das Ziel existiert**, ohne zu kopieren.
  Der Zielname trägt Millisekunden, eine Kollision ist unwahrscheinlich, und
  `_verifizieren` fiele bei abweichender Größe darüber — bei gleicher Größe
  aber nicht. Nicht gemessen (siehe Grenzen).

---

## Grenzen — was er NICHT misst

1. **Das volle Dateisystem ist kein Dateisystem des Nutzers.** Gemessen ist
   ein echtes ENOSPC eines echten `tmpfs` — aber eines, das dieser Lauf in
   seinem eigenen Namensraum aufgespannt hat. Ob `undo.py` sich auf einer
   wirklich vollen Home-Partition genauso verhält, ist nicht gemessen und
   wäre es nur um den Preis, die Maschine des Nutzers vollzuschreiben.
2. **`RLIMIT_FSIZE` ist EFBIG/SIGXFSZ, nicht ENOSPC.** Das Gleis belegt
   „`cp` bricht ab, die Naht bricht ab, die Datei bleibt" — nicht, dass der
   Kernel dabei denselben Fehlercode meldet. Deshalb steht es **neben**, nicht
   anstelle des tmpfs-Gleises.
3. **Der Konfliktfall ruht auf drei Messungen, nicht auf `git`s Wortlaut.**
   Gemessen sind: der Konflikt steht (`UU arbeit.txt`, gewogen), `git stash`
   meldet einen Fehlschlag, und die Stash-Liste ist danach unverändert. Der
   Text (`error: could not write index`) hängt an der `git`-Version und ist
   ausdrücklich **kein** Kriterium.
4. **Der Undo-Hop wird an einer beliebigen genehmigten Aktion gefahren**
   (`media.playpause`). Der Koordinator behandelt den Hop aktionsunabhängig;
   gemessen ist die **Reihenfolge** und der **Abbruch**, nicht eine
   Aktions-zu-Undo-Art-Zuordnung. Eine solche gibt es heute nicht: der Katalog
   führt keine destruktive und keine `fs.*`-Aktion.
5. **Der Zulauf-Leser sieht Konstruktion, nicht Ausführung.** Er belegt, dass
   irgendwo ein `Koordinator(..., undo=…)` gebaut wird und dass
   `vorbereiten` gerufen wird — nicht, dass dieser Pfad im Betrieb erreicht
   wird oder dass die durchgereichte Funktion das Richtige tut. Für ein
   `undo=lambda **kw: None` bliebe K7 grün.
6. **Der Konsument der Herabstufung ist nicht gemessen.** Dass
   `braucht_modal` im Produktivbaum niemand ruft (Nebenbefund 2), steht im
   Ledger, nicht in einem Kriterium — es wäre T-4.12s Zulauf, und das
   Gut-Muster hätte dafür eine zweite Abweichung gebraucht.
7. **`wiederherstellen()` ist nur für `trash` und `kopie` gemessen.** Für
   `git-stash` verweigert die Funktion bewusst; die Wiederherstellbarkeit ist
   dort mit einem eigenen `git stash pop` belegt — also am Artefakt, nicht am
   Broker.
8. **Nebenläufigkeit, Rechte und Symlinks bleiben außen vor.** Kein
   gleichzeitiger zweiter Undo auf dieselbe Datei, keine schreibgeschützte
   Ablage, kein Symlink als Ursprungsdatei, kein `st_dev`-Wechsel zwischen
   Prüfung und `os.replace`. Das ist T-4.9s Gegenstand (`openat2`, TOCTOU).
9. **`--no-clobber` mit gleich großem Ziel** würde die Verifikation passieren,
   obwohl nichts kopiert wurde. Der Fall ist konstruierbar, aber nur mit einem
   erzwungenen Namenskollision — nicht gemessen.
10. **Das Gut-Muster ist eine Reviewer-Fassung.** Es enthält genau **eine**
    Abweichung vom Arbeitsbaum (`HERKUNFT.txt`): die `undo=`-Verdrahtung in
    `hub/daemon.py`. Sie ist die Positivkontrolle für K7, kein
    Reparaturvorschlag und kein Produktivcode — wie die Verdrahtung am Ende
    aussieht, entscheidet der Builder.
11. **Die 21 übrigen Dateien des Gut-Musters sind ein Ausschnitt.** Der
    Zulauf-Leser sieht dort nur diese Dateien; gegen den Arbeitsbaum läuft er
    über den ganzen Produktivbaum. Ein `undo=` in einer Datei, die im
    Gut-Muster fehlt, wäre dort unsichtbar — gegen den Arbeitsbaum, wo es
    zählt, nicht.

---

## Rücksicht auf den laufenden Betrieb

**Eingegriffen wurde ausschließlich unterhalb eines `mktemp -d`.** Kein
`$HOME/Dokumente`, kein `~/.local/share/daimon`, kein echtes Repo des Nutzers:
die drei Wegwerf-Repos für die `git`-Fälle legt der Prüfstand selbst an und
löscht sie mit.

Nach allen Läufen (Gut-Muster, Arbeitsbaum, zwölf Mutanten, zwei
`meta.sh`-Läufe):

```
$ ls -d /tmp/t48-* /dev/shm/t48-* /var/tmp/t48-*     (nichts)
$ ls ~/.local/share/Trash/files | wc -l               12   (unveraendert,
      juengster Eintrag 2026-08-18 07:24 -- vor dieser Sitzung)
$ systemctl --user list-units 'daimon*' --state=active
  daimon-auth, daimon-face, daimon-focus, daimon-hookbridge, daimon-hub
  + 3 .socket-Units, 2 Timer                          (unveraendert)
$ git status --short                                  (leer)
```

Kein `systemctl`, kein `gio trash`, kein Anklopfen an einem produktiven
`daimon`-Socket, kein Start eines Wahrnehmungsdienstes. Der einzige
Namensraum-Mount lebte im Kindprozess und verschwand mit ihm.

`.claude/hooks/**` unberührt. `freeze.sh` nicht aufgerufen. `git status` listet
ausschließlich die neuen `T-4.8`-Pfade; die Bäume der parallel laufenden
Sitzung (`T-3.*`, `T-4.4`–`T-4.7`, `T-5.*`, `T-7.*`) sind nicht angefasst.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, NICHT eingefroren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main`, Commit `db9a5f5` |
| Verifizierer unverändert | `T-4.8.sh`, `t48_pruefstand.py`, `t48_naht.py` |

**Nicht eingefroren, weil rot.**

### 1. Gegen `main` — rot, an genau demselben Kriterium

```
$ env -u DAIMON_FIXTURE tests/verify/T-4.8.sh; echo $?
FAIL [K7] KEIN Koordinator bekommt ein `undo=` gereicht: `self.undo is None`,
     der Undo-Hop wird uebersprungen. Die Zusage 'schlaegt die Vorbereitung
     fehl, wird die Mutation abgebrochen' hat im Betrieb keinen Fall, in dem
     sie greift -- es wird nie etwas vorbereitet
FAIL [K7] und niemand ruft `vorbereiten`/`in_den_trash`/`kopie_anlegen`/
     `stash_anlegen` -- das Modul hat ausserhalb der Tests keinen einzigen
     Aufrufer
Bilanz T-4.8:
K1:  7 Pruefungen, 0 rot    K5: 20 Pruefungen, 0 rot
K2:  6 Pruefungen, 0 rot    K6:  5 Pruefungen, 0 rot
K3:  4 Pruefungen, 0 rot    K7:  4 Pruefungen, 2 rot
K4:  7 Pruefungen, 0 rot
T-4.8: ROT -- 1 von 7 Kriterien rot: K7
1
```

Zwei von 53 Prüfungen rot, unverändert gegenüber dem 18.08.

### 2. Heute nachgelesen

`daimon/brokers/fs/undo.py` trägt `vorbereiten` (Z. 184), `in_den_trash`
(Z. 89), `kopie_anlegen` (Z. 133) und `stash_anlegen` (Z. 157). Eine Suche
über den ganzen Produktbaum findet als Aufrufer **nur `vorbereiten` selbst**
(Z. 191/193/195, der Dispatch auf die drei) — sonst niemanden.

Das ist der Fehler, den `CLAUDE.md` sechsmal in Folge auflistet, ein siebtes
Mal: ein Stück ist gebaut, dokumentiert und mit grünen Tests belegt, und im
Betrieb ruft es niemand auf. Der Verifizierer misst hier genau die NAHT und
nicht das Stück — deshalb sieht er es, und `pytest tests/test_undo.py`
(20 Prüfungen unter K5, alle grün) sieht es nicht.

**Fehlerszenario:** eine genehmigte Mutation läuft. Die Zusage von T-4.8
lautet „schlägt die Vorbereitung fehl, wird die Mutation abgebrochen". Im
Betrieb ist `self.undo is None`, der Undo-Hop wird übersprungen, und die
Mutation läuft **ohne** Rückholpunkt durch. Die Zusage hat keinen Fall, in
dem sie greift.

### 3. Gut-Muster und Mutanten — der Verifizierer ist gesund

```
$ bash tests/verify/meta.sh T-4.8
T-4.8: 12 Mutanten erzeugt.
meta[T-4.8]: Gut-Muster ...
… groesse-egal · kopie-ohne-reflink · kopie-verschiebt · stash-nicht-vorher ·
  stash-zaehlt-nicht · trash-grenze-egal · trashinfo-falscher-name ·
  trashinfo-nur-name · undo-fehler-verschluckt · verifikation-fort ·
  verifiziert-vor-der-pruefung · zulauf-fort — alle erkannt.
meta[T-4.8]: 12 Mutanten, alle erkannt.
```

`zulauf-fort` ist der Mutant für genau dieses Kriterium: er nimmt den Zulauf
im Gut-Muster wieder heraus und wird erkannt. Der Prüfstand kann „kein
Aufrufer" also von „nicht gemessen" unterscheiden.

### 4. Rücksicht auf den laufenden Betrieb

Der Vorschalter des Prüfstands hat 48 Aufrufe protokolliert
(`{'cp': 9, 'gio': 1, 'git': 38}`) und keinen an `systemctl`, `trash` oder
`trash-put` durchgelassen — die Sperre aus `t48_pruefstand.py:102` hat
gehalten. Der Papierkorb des Nutzers ist nicht angefasst worden.
