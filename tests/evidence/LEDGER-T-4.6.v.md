# Ledger T-4.6.v — Verifizierer: Audit mit Hash-Kette und Verankerung

**Ausgang: `produktdefekt-rot`**

Der Verifizierer ist gebaut, gegen das Gut-Muster grün, gegen alle vier
Anhang-D-Mutanten rot — und gegen den echten Baum rot, an genau einem
Akzeptanzkriterium. Nicht die Messung fehlt; die Zusage fehlt.

---

## Provenienz

| | |
|---|---|
| Gebaut von | Reviewer-Sitzung vom 2026-08-17, `DAIMON_ROLE=reviewer` |
| Worktree | `/mnt/data/AI/repos/dAImon-reviewer`, Branch `reviewer/p4-verifizierer` |
| HEAD beim Bau | `ea447d03790ef2f9b738266c77be7940bc9f70d0` |
| Gelesen, in dieser Reihenfolge | `PLAN.md` §Vorab-Festlegungen (Z. 21–115) · `docs/IMPLEMENTATION-PLAN.md` Block T-4.6 (Z. 1244–1258) und Anhang D T-4.6.v (Z. 2339–2351) · `docs/REVIEWER-UEBERGABE-17.08.md` §1 Punkt 3 und §4 · `docs/DESIGN.md` §7.6 (Z. 1000–1020) · Formvorlage `tests/verify/T-3.14.sh`, `tests/verify/t314_pruefstand.py`, `tests/verify/meta.sh` · Bestandsmuster für Mutanten `tests/mutants/T-3.11/erzeugen.sh` |
| Produktquelle gelesen | `daimon/hub/audit.py`, `daimon/hub/daemon.py` (Audit-Teile), `daimon/hub/state.py::apply`, `daimon/hub/bus.py::mood_of`, `config/systemd/daimon-audit-verify.{service,timer}` — lesend; Blindheit war für T-4.6.v **nicht** vertraglich gefordert (das ist T-3.14.v) |
| Nicht getan | `freeze.sh` **nicht** aufgerufen (eigener autorisierter Task laut `PLAN.md`), `role_guard.py` **nicht** repariert (eigener Auftrag), kein Produktivcode geändert, kein Merge, kein Push |

Neue Dateien:

```
tests/verify/T-4.6.sh
tests/verify/t46_pruefstand.py
tests/fixtures/known-good/T-4.6/          (1,2 M)
tests/mutants/T-4.6/erzeugen.sh + 4 Bäume (6,5 M)
tests/evidence/LEDGER-T-4.6.v.md
```

---

## Die Prüffrage des Builders

> *Schreibt das Audit, was es behauptet, und erkennt `pruefe` eine
> Manipulation?*

**Es schreibt, was es behauptet — bis auf eine Zusage. `pruefe` erkennt alle
vier vorgeschriebenen Manipulationen, gemessen.** Was fehlt, ist die dritte
Hälfte der Frage, die der Builder nicht gestellt hat: *und erfährt es jemand?*

---

## Befund: der Hub bemerkt die gerissene Kette und sagt es niemandem

Akzeptanzliste T-4.6, fünfter Punkt, und Design §7.6 wörtlich:

> `daimon-hub` prüft die Kette **beim Start** gegen die Journal-Anker und
> **meldet eine Abweichung als Bubble mit hoher Dringlichkeit**.

Gemessen: der Hub prüft (`daemon.py::audit_verankern` ruft `pruefe`, davon
gibt es einen Beleg — der Anker liegt vor), er erkennt den Riss, und er
schreibt eine Zeile ins Journal. Der Schnappschuss auf `state.sock` trägt
danach `bubble: null`. Der Nutzer sieht nichts.

```
FAIL K5: der Hub meldet die gerissene Kette als dringende Blase
         (Design 7.6: "Bubble mit hoher Dringlichkeit"); bubble=None
```

Der Weg dorthin, vollständig gemessen, in dieser Reihenfolge:

1. Fünf Datensätze mit dem Produkt selbst geschrieben, verankert.
2. Zeile 3 geändert. **Prüfsumme vorher/nachher verglichen:**
   `008774e9355f → 60d40194c258`. Die Manipulation hat Bytes bewegt.
3. Vorbedingung geprüft: `pruefe()` meldet für genau diese Datei einen
   Kettenfehler. Es gibt also etwas zu melden — eine fehlende Blase kann
   nicht mehr „nichts kaputt" bedeuten.
4. Hub gestartet, temporäre XDG-Verzeichnisse, eigene Prozessgruppe.
5. Anker im abgefangenen Journal → der Hub hat die Datei gelesen.
6. `state.sock` gelesen: `bubble: null`.
7. **Positivkontrolle danach** (nicht davor, sie hätte die Messung gefärbt):
   ein `permission_prompt`-Hook auf `hookbridge.sock` → `bubble` mit
   `urgent: true` steht im selben Schnappschuss. Der Apparat kann eine
   dringende Blase sehen. Er hat vorher keine gesehen, weil keine da war.

Die Meldung existiert also — als Journalzeile. Eine Meldung, die man suchen
muss, ist keine; das steht so im Kopf der eigenen Timer-Unit des Builders.

Das ist **nicht** in der Zuständigkeit dieser Sitzung zu reparieren
(`daimon/**` ist für `reviewer` gesperrt). Der Befund geht an den Builder,
der Test bleibt rot.

**Reparaturort und -umfang** (Vorschlag, ungeprüft im Arbeitsbaum, aber im
Gut-Muster laufend belegt): in `audit_verankern`, im `kette_kaputt`-Zweig,
neben die Journalzeile ein `self.state.apply("failed", session_id="audit",
bubble={..., "urgent": True})`. Sieben Zeilen. Genau diese Zeilen sind der
einzige Unterschied zwischen Gut-Muster und Arbeitsbaum.

---

## Was grün ist, und woran

Alle acht Kriterien einzeln abgerechnet, ohne `&&`-Verkettung; jedes
Kriterium ohne eine einzige Messung zählt in der Bilanz **als rot**.

| | Kriterium | Prüfungen | gemessen woran |
|---|---|---|---|
| K1 | JSONL unter `$XDG_STATE_HOME/daimon/audit/`, 0700/0600 | 6 | Rechte am Ist-Objekt; der **Zulauf** über die CLI ohne `--verzeichnis`: sie legt die Kette nach `$XDG_STATE_HOME/daimon/audit/audit.jsonl` |
| K2 | `seq` und `prev_hash` je Datensatz | 5 | 100 Sätze, `seq` = 1..100, `prev_hash` **unabhängig nachgerechnet** (sha256 der Vorgängerzeile), nicht mit der Funktion des Prüflings |
| K3 | Kopf periodisch und bei jeder Rotation verankert | 13 | Rotationsanker in-process; **live**: der gestartete Hub verankert von selbst; Periode als endliche Zahl ≤ 24 h |
| K4 | beide Ströme, ersetzte Datei wird gemeldet | 12 | die vier vorgeschriebenen Manipulationen, jede einzeln, jede gewogen |
| K5 | drei benannte Prüfstellen, keine mit Modelltext | 19 | CLI real (Exit 0 heil / Exit 1 gerissen), Timer-Unit, Hub live, `sys.modules`-Messung |
| K6 | alle Pflichtfelder aus §7.6 | 17 | jedes der sieben Felder **einzeln** weggelassen; `outcome` als endliche Menge inkl. `unknown` |
| K7 | Redaktion nach Herkunft, nicht nach Katalogflag | 9 | ein `tainted`-Feld, das kein Katalog führen würde; Abdruck + Länge nachgerechnet |
| K8 | Rotation trägt den letzten Hash weiter | 10 | Kopfsatz-`prev_hash` gegen den unabhängig gerechneten sha256 der letzten alten Zeile |

Zu K4 im Einzelnen — die vier Fälle aus dem Verifikationssatz von T-4.6:

```
Manipulation 'eine Zeile geaendert':                       sha256 21fa35c0888e -> 9260e6c84ddd   erkannt
Manipulation 'eine Zeile geloescht':                       sha256 21fa35c0888e -> 8ea406de17ea   erkannt
Manipulation 'zwei Zeilen getauscht':                      sha256 21fa35c0888e -> 391155fb039a   erkannt
Manipulation 'ganze Datei durch neue stimmige Kette ersetzt': sha256 21fa35c0888e -> 128273e02fdb erkannt
```

Der vierte Fall wird zweimal gemessen, und das ist der Punkt: **ohne** Anker
findet die Kettenprüfung an der Ersatzdatei nichts (sie ist in sich stimmig —
mit dem Prüfling selbst gebaut), **mit** Ankern fällt sie mit
`anker_getroffen: 0`. Erst die Trennung zeigt, dass wirklich der zweite Strom
den Fund macht und nicht zufällig die Kette.

---

## Die zwei Auflagen dieses Auftrags, und wie sie eingelöst sind

### 1. Positivkontrolle, die wirklich etwas verändert

`manipulieren()` wiegt jede Datei vor und nach dem Eingriff und **bricht
laut ab**, wenn die sha256 gleich bleibt:

```
POSITIVKONTROLLE GESCHEITERT: '...' hat ... nicht veraendert
(sha256 vorher X == nachher X). Der folgende Befund waere nicht gemessen,
sondern erfunden.
```

Das war keine Vorsichtsmaßnahme auf Verdacht — **die Waage hat beim ersten
Lauf sofort zugeschlagen**: meine Ersatzkette für K4(d) war mit denselben
Marken und denselben `ts` gebaut wie das Original und deshalb byteweise
identisch. Ohne die Waage hätte der Verifizierer an dieser Stelle grün
gemeldet, ohne je eine Ersetzung gesehen zu haben — derselbe Fehler wie am
17.08. beim Audit-Timer (§4 Punkt 3 der Übergabe), an derselben Art Prüfung.
Behoben durch andere Marken in der Ersatzkette; seither wandert die Prüfsumme
sichtbar.

Dazu eigene Positivkontrollen je Kriterium, u. a.:
Rechtemessung sieht auch `0o755` · Ankersammler sieht **zwei verschiedene**
Anker statt zweimal dasselbe · Klartextsuche findet einen absichtlich nicht
redigierten Wert (sonst wäre „Geheimnis nicht gefunden" nicht von „gar nichts
geschrieben" zu unterscheiden) · Modelltext-Detektor sieht `daimon.mind.*`,
wenn welches da ist · dringende Blase ist sichtbar, wenn eine gesetzt wird.

### 2. Eine Messung ist ein Zeitpunkt, kein Zeitfenster

Kein `journalctl --since`-Fenster gegen ein zweites. Der zweite Strom läuft
während des ganzen Laufs über eine **abgefangene Journal-Datei**:
`systemd-cat` (Attrappe) schreibt hinein, `journalctl` (Attrappe) liest
daraus. Bezugspunkt ist der Dateiinhalt, gezählt ab einem festen Index
(`anker()[vorher:]`) — nicht die Uhr.

Die Attrappen haben ihre eigene Positivkontrolle **vor** der ersten Messung:
ein geschriebener Anker kommt zurück, ein fremder Tag wird gefiltert, danach
ist die Datei nachweislich leer. Ein Abfang, der nicht greift, fällt damit
auf, statt Ruhe zu melden.

Nebenwirkung mit Absicht: der Lauf schreibt **keine** Anker seiner
synthetischen Ketten ins echte Journal des Nutzers. Die rund dreißig
`AUDIT-ANKER seq=0 head=`-Zeilen aus Übergabe §5 bekommen keine Geschwister.

---

## Welche Zeile im Produktivcode müsste kaputt sein — und ausprobiert?

Die Auflage aus dem Auftrag, beantwortet, mit Lauf. Vier Anhang-D-Mutanten
plus vier Wegwerf-Proben unter `/tmp` (nicht im Repo), jede eine einzelne
gebrochene Zeile im Gut-Muster:

| Kriterium | gebrochene Zeile | Ergebnis |
|---|---|---|
| K3, K4 | `verankern`: `(journal or _ins_journal)(text)` → `pass` | Mutant `kette-ohne-journal-anker`: **K3 6 rot, K4 3 rot, K5 3 rot** |
| K7 | `schreiben`: die `for name in tainted`-Schleife → `pass` | Mutant `tainted-im-klartext`: **K7 5 rot** |
| K7 | dieselbe Schleife, zusätzlich `and name in SENSITIV` | Mutant `redaktion-nur-bei-sensitive`: **K7 5 rot** |
| K8 | `rotieren`: `"prev_hash": self._prev` → `""` | Mutant `rotation-traegt-hash-nicht-weiter`: **K8 2 rot** |
| K1 | `schreiben`: `os.chmod(self.datei, 0o600)` → `0o644` | Probe: **K1 1 rot, K8 1 rot** |
| K2 | `schreiben`: `"prev_hash": self._prev` → `""` | Probe: **K2 1 rot** (+ K4, K5, K8) |
| K4 | `pruefe`: `if satz.get("prev_hash", "") != prev …` → `if False` | Probe: **K4 1 rot** (+ K5) |
| K6 | `schreiben`: `fehlend = [f for f in PFLICHTFELDER …]` → `[]` | Probe: **K6 7 rot** |
| K5 | die sieben Blasen-Zeilen im Gut-Muster entfernt (= der Arbeitsbaum) | **K5 1 rot** — der Befund oben |

Jeder Mutant fällt an dem Kriterium, das die Matrix im Prüfstandkopf ihm
zuordnet, und an keinem fremden. Kein Kriterium ist blind: für jedes der acht
ist ein Lauf belegt, in dem es rot wird.

---

## Belege (Befehl + Ausgabe)

```
$ git rev-parse HEAD
ea447d03790ef2f9b738266c77be7940bc9f70d0

$ bash tests/verify/verify-frozen.sh
verify-frozen: 37 eingefrorene Dateien unveraendert; Abhaengigkeiten geschlossen.
EXIT 0

$ bash tests/verify/meta.sh T-4.6
meta[T-4.6]: Gut-Muster ...
meta[T-4.6]: Mutante 'kette-ohne-journal-anker' erkannt.
meta[T-4.6]: Mutante 'redaktion-nur-bei-sensitive' erkannt.
meta[T-4.6]: Mutante 'rotation-traegt-hash-nicht-weiter' erkannt.
meta[T-4.6]: Mutante 'tainted-im-klartext' erkannt.
meta[T-4.6]: 4 Mutanten, alle erkannt.
META EXIT 0

$ DAIMON_FIXTURE=tests/fixtures/known-good/T-4.6 bash tests/verify/T-4.6.sh
K1: 6 Pruefungen, 0 rot        K5: 19 Pruefungen, 0 rot
K2: 5 Pruefungen, 0 rot        K6: 17 Pruefungen, 0 rot
K3: 13 Pruefungen, 0 rot       K7: 9 Pruefungen, 0 rot
K4: 12 Pruefungen, 0 rot       K8: 10 Pruefungen, 0 rot
EXIT 0

$ bash tests/verify/T-4.6.sh                       # der echte Baum
FAIL K5: der Hub meldet die gerissene Kette als dringende Blase
         (Design 7.6: "Bubble mit hoher Dringlichkeit"); bubble=None
K1: 6 Pruefungen, 0 rot        K5: 19 Pruefungen, 1 rot
K2: 5 Pruefungen, 0 rot        K6: 17 Pruefungen, 0 rot
K3: 13 Pruefungen, 0 rot       K7: 9 Pruefungen, 0 rot
K4: 12 Pruefungen, 0 rot       K8: 10 Pruefungen, 0 rot
EXIT 1

$ pgrep -af "daimon.hub.daemon"                    # nach allen Läufen
727459 …/Dokumente/Github/dAImon/.venv/bin/python -m daimon.hub.daemon …
        (die vorbestehende Sitzungs-Unit aus einem anderen Checkout;
         kein Prozess dieses Laufs ist übrig, kein /tmp/t46-* geblieben)
```

---

## Grenzen dieses Verifizierers — ehrlich, damit ihn niemand falsch liest

1. **„Periodisch" ist nicht über eine Periode gemessen.** Belegt sind: der
   Hub verankert **beim Start** von selbst (live), und `AUDIT_ANKER_INTERVALL_S`
   ist eine endliche Zahl ≤ 24 h. Dass nach 3600 s ein zweiter Anker kommt,
   hat dieser Lauf nicht abgewartet. Aufwertung nur mit einem injizierbaren
   Intervall — das wäre eine Produktänderung und damit ein eigener Task.
2. **Der zweite Strom läuft im Lauf über Attrappen**, nicht über das echte
   Journal. Dass der ECHTE Weg trägt, ist außerhalb dieses Verifizierers
   belegt (eine Probe dieser Sitzung sah `AUDIT-ANKER seq=0 head=` real im
   `journalctl --user -t daimon-audit`) — im Verifizierer selbst steht es
   nicht, weil es entweder ein Zeitfenster oder eine Journal-Verschmutzung
   je Lauf gekostet hätte. Wer das anders gewichtet, hat einen Grund für
   einen `.v2`.
3. **Der Timer ist als Datei geprüft, nicht als Lauf.** `OnCalendar=daily`,
   `[Install]`, `Unit=`, `ExecStart` mit `--verify` — dass `systemd` ihn
   zieht, prüft `tests/test_units_werden_gezogen.py` als Wächter, und ein
   Wächter ist keine Zusage (Übergabe §3).
4. **„Kein Modelltext im Prozess" ist als Importmenge gemessen**
   (`sys.modules` ohne `daimon.mind.*`, mit Positivkontrolle). Das ist die
   nachprüfbare Hälfte der Design-Aussage; die andere Hälfte — dass zur
   Laufzeit kein fremder Text in den Prozess gereicht wird — ist eine
   Eigenschaft der Aufrufer und gehört zu den Broker-Tasks.
5. **Design §7.6 verlangt für die Zweitschrift `sd_journal_send()` und sagt
   ausdrücklich „`systemd-cat` reicht nicht [V]".** Der Anker geht heute über
   `systemd-cat` (`audit.py::_ins_journal`). Das ist **kein Kriterium der
   T-4.6-Akzeptanzliste** und deshalb hier nicht rot gewertet — aber es ist
   eine offene Frage an den Designtext, und sie gehört jemandem gestellt.
6. **Nicht eingefroren.** `freeze.sh` bleibt ungerufen; die Freeze-Erweiterung
   ist laut `PLAN.md` ein eigener, einzeln autorisierter Task. Damit ist
   `tests/verify/T-4.6.sh` noch nicht gegen Änderung geschützt.

---

## Was der Builder als Nächstes tun kann

* Die sieben Blasen-Zeilen aus dem Gut-Muster übernehmen (`audit_verankern`,
  `kette_kaputt`-Zweig). Danach ist dieser Verifizierer gegen den echten Baum
  grün, **ohne dass er angefasst werden muss** — das ist die Probe darauf,
  dass er das Kriterium und nicht eine Implementierung prüft.
* Die Frage aus Grenze 5 entscheiden: `sd_journal_send()` oder den Designsatz
  streichen. Zwei Fassungen einer Regel sind eine Regel und eine Attrappe.
