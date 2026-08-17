# Übergabe an die Reviewer-Sitzung — Stand 2026-08-17

**Verfasser:** builder (Sitzung vom 17.08., Commits `2788cfc`…`84ec9ee`)
**Adressat:** eine Sitzung mit `DAIMON_ROLE=reviewer`
**Ergänzt:** `PLAN.md` (die Sitzung selbst), `HANDOVER.md` (der Gesamtstand)

Diese Datei sagt, **wo ich mir selbst nicht traue**. Sie ist keine
Zusammenfassung meiner Arbeit — die steht in den Commit-Botschaften — sondern
eine Liste der Stellen, an denen dieselbe Einheit gebaut und geprüft hat.

---

## Warum diese Sitzung nicht selbst weitermachen konnte

`DAIMON_ROLE` kommt aus der Umgebung, die **Claude Code** an seine Hooks
vererbt. Ein `DAIMON_ROLE=reviewer` vor einem Bash-Kommando ändert nur den
einen Subprozess; der `PreToolUse`-Hook sieht weiter `builder`. Gemessen:

```
Der Hook selbst, mit gesetzter Rolle:
  DAIMON_ROLE=builder   -> deny
  DAIMON_ROLE=reviewer  -> durchgelassen
Was Claude Code vererbt:  builder
```

Wirksam wird die Rolle nur beim Start: `DAIMON_ROLE=reviewer claude`.

**Und das ist die kleinere Hälfte des Grundes.** Diese Sitzung hat achtzehn
Commits Produktivcode geschrieben. Baut sie die Verifizierer dazu, ist wieder
dieselbe Einheit Erbauer und Prüfer — genau der Mangel, den T-6.9 benennt und
den diese Sitzung als „zweites Augenpaar" abgearbeitet hat. Der Wert liegt in
der **anderen Sitzung**, nicht in der anderen Variablen.

---

## 1. Was ungeprüft ist, und zwar von außen

Alles unten ist von mir gebaut **und** von mir belegt. Jede Zeile hat Tests;
keine hat einen Verifizierer, den jemand anderes geschrieben hätte.

| Zusage | wo | wie ich sie belegt habe | Verifizierer |
|---|---|---|---|
| Gate gibt Live-Kontext frei | `2788cfc` | pytest + live (`deklassifiziert 0→1`) | **T-5.9.v fehlt** |
| DRM-Sperre hat eine Quelle | `ab922d7` | pytest + live an `excludeFromCapture` | **T-7.2.v fehlt** |
| Art gehört zum Absender | `548330d` | pytest über echten Socket | **T-7.1.v fehlt** |
| Wahrnehmung ≠ fremder Strom | `64c7bb2` | pytest + live (Augen an/aus) | **T-7.3.v fehlt** |
| Gate-Audit schreibt | `cd49823` | pytest + live (beide Richtungen) | **T-4.6.v fehlt** |
| Kette ist verankert | `855def8`, `d981689` | live, `ok: true`, 4 Anker | **T-4.6.v fehlt** |
| Archivsuche nur mit Schein | `6138e00` | pytest, beide Ausgänge | **T-7.5.v fehlt** |
| Transkript im Archiv | `ed58559` | live, Station 6 der Naht | **T-7.4.v fehlt** |

**Die fünf Verifizierer der Phase 7 fehlen vollständig** (T-7.1.v … T-7.5.v),
ebenso T-5.9.v für das Deklassifizierungs-Gate. Das sind genau die Zusagen,
an denen diese Sitzung am meisten gearbeitet hat.

### Der Zuschnitt, den ich empfehle

Nicht nach Task-Nummer, sondern nach Risiko:

1. **T-5.9.v** — das Gate. Es hat diese Woche dreimal seine Gestalt geändert
   (Aufrufer, Audit, Zähler). Prüffrage: *kommt Bildschirmkontext ohne
   Rundenmarke ans Modell?* Der Weg dorthin ist `kontext.sock`, und die
   Unit-Allowlist lässt nur `daimon-mind` durch — ein Verifizierer braucht
   also entweder eine transiente Unit dieses Namens oder muss die Grenze
   anders messen. **Meine Messung war eine Negativkontrolle mit zwei
   Positivkontrollen daneben; prüfe, ob das genügt.**
2. **T-7.2.v** — die Redaktion. Sie entscheidet, was auf die Platte kommt.
   Prüffrage: *gibt es einen zweiten Weg ins Archiv?* Ich habe genau einen
   `Archiv.schreiben`-Aufruf gefunden und die `art`-Wahl an die Peer-Unit
   gebunden — aber ich habe meinen eigenen Fix geprüft.
3. **T-4.6.v** — das Audit. Es war doppelt tot, und beide Fehler haben sich
   gegenseitig versteckt. Prüffrage: *schreibt es, was es behauptet, und
   erkennt `pruefe` eine Manipulation?* Meine Positivkontrolle dazu ist
   einmal danebengegangen (siehe §4).
4. Danach T-7.1.v, T-7.3.v, T-7.4.v, T-7.5.v.

Die 29 fehlenden Verifizierer insgesamt:

```
T-3.7.v  T-3.15.v
T-4.4.v … T-4.17.v        (14 Stück, die Aktuationsphase)
T-5.3.v  T-5.6.v  T-5.9.v  T-5.10.v  T-5.11.v  T-5.12.v  T-5.13.v
T-6.7b.v
T-7.1.v … T-7.5.v
```

`PLAN.md` schneidet davon einen Teil zu und nennt Ausgänge (`gruen`,
`mensch-blockiert`, `umgebungs-blockiert`, `zieltask-offen`,
`produktdefekt-rot`). Der Zuschnitt oben ist mein Vorschlag, nicht sein
Ersatz.

---

## 2. Aufträge, die als `xfail(strict=True)` dastehen

Drei Marken sind **Arbeitsaufträge** — sie werden grün, sobald jemand den
zugehörigen Umbau macht. `strict` ist Absicht: ein XPASS erzwingt das
Entfernen der Marke, damit der Fortschritt nicht verschwiegen wird.

| Datei | was fehlt | wer darf |
|---|---|---|
| `tests/test_rollen.py:165` | eine Fehlerumleitung (`2>&1`) darf kein Schreiben sein | **reviewer** (`.claude/hooks/**`) |
| `tests/test_rollen.py:182` | ein bloß genannter Pfad darf kein Schreibziel sein | **reviewer** |
| `tests/test_debt_ledger.py:117` | `daimon/face/echo.py:34` nennt keine Obergrenze | builder |

**Der Rollenwächter ist der lohnendste Einzelposten.** Er hat mich am 17.08.
**fünfmal** blockiert, jedes Mal bei einem lesenden Kommando: `cat … 2>/dev/null`,
`ls … 2>&1`, ein Heredoc mit Verifiziererpfaden, `cat >` und ein `git add` im
selben Kommando wie ein `git commit`, dessen Botschaft Pfade nannte. Zehn
Fälle, die blockiert bleiben **müssen**, stehen als grüne Prüfungen darüber —
diese Liste zuerst fahren, dann lockern.

Der Nachrüstweg steht in `HANDOVER.md`: Ausführung von Änderung trennen, Ziele
nur hinter schreibenden Verben suchen. **Nicht** einfach `>` aus `WRITING_CMD`
streichen — dann fällt `echo x > verifizierer.sh` durch.

Die vier Marken in `tests/test_mood_mapping.py` sind älter und gehören nicht
zu dieser Sitzung. Gesamtstand: **9 xfail**, davon 4 aus `test_rollen.py`
(zwei parametrisierte Marken) und 1 aus `test_debt_ledger.py`.

> Beim Schreiben dieser Zeile stand hier zuerst „sechs Marken" — gezählt mit
> `grep -c xfail`, das auch Erwähnungen im Fließtext trifft. Fünfter Zählfehler
> derselben Art an einem Tag (§4). Die Zahl kommt jetzt aus `pytest -rxX`.

---

## 3. Wächter, und was sie NICHT prüfen

Diese Sitzung hat vier Wächter gebaut. Sie melden, wenn ein Zulauf entsteht —
sie prüfen **keine Wirkung**:

| Wächter | meldet | prüft NICHT |
|---|---|---|
| `test_gate_zulauf.py` | `KontingentBuch` bekommt einen Aufrufer; ein zweiter ruft das Gate; `privat_setzen` bekommt einen Schalter | ob das Gate richtig entscheidet |
| `test_units_werden_gezogen.py` | eine Unit ohne `[Install]`/`.socket`/`.timer`; ein Timer ohne `[Install]` | ob die Unit startet |
| `test_debt_ledger.py` | Ledger und Code laufen auseinander | den **Wortlaut** der Einträge |
| `test_WAECHTER_start_verdrahtet_die_ankerschleife` | der Audit-Faden fehlt in `start()` | ob er verankert |

Ein Wächter, der zur Zusage wird, ist ein Fehler — wer einen davon als Beleg
liest, liest ihn falsch.

---

## 4. Vier Messfehler, alle meine

Damit sie nicht ein zweites Mal passieren, und weil sie zeigen, wo meine
Belege dünn sein könnten:

1. **Zwei Zeitfenster statt zwei Zuständen.** `journalctl --since -1min`
   gegen `--since -2min` und aus der Differenz „16 Anker je Testlauf"
   geschlossen. Mit festem Bezugspunkt: null.
2. **Dasselbe nochmal**, eine Stunde vorher: Station 5 der Naht stand auf
   „nicht getragen", 41 Sekunden später meldete der Mind „Antwort erhalten".
   Eine Messung ist ein *Zeitpunkt*.
3. **Eine Positivkontrolle, die nichts verändert hat.** Beim Prüfen des
   Audit-Timers traf mein `replace` nicht, die Kopie blieb byteweise
   identisch — der Prüfer meldete brav `ok`. Erst der Vergleich der
   Prüfsummen zeigte es. **Wenn ein Beleg von mir eine Positivkontrolle
   nennt: prüfe, ob sie wirklich etwas verändert hat.**
4. **`grep -c` für eine Eintragszahl gehalten** und daraus „4 von 21
   ponytail-Kommentaren erfasst" geschlossen. Tatsächlich 18 von 18.

---

## 5. Was am System hängt, nicht im Repo

Ein Verifizierer, der in einem frischen Konto läuft, findet das **nicht** vor:

* **Dienste**, von dieser Sitzung gestartet: `ollama` (per `sudo`),
  `daimon-lokal-broker`, `daimon-ears`, `daimon-recorder`. Der Mitschnitt
  läuft.
* **Aktiviert:** `daimon-mind.service`, `daimon-audit-verify.service` +
  `.timer`.
* **`~/.config/daimon/daimon.toml`:** die Zeile `modell = "gemma4:26b"` ist
  ersetzt — das Modell lag nicht mehr vor. Der Broker nimmt jetzt, was Ollama
  vorhält.
* **Rund dreißig `AUDIT-ANKER seq=0 head=`-Zeilen im Journal**, aus
  Zwischenfassungen der Anker-Prüfstände. `anker_aus_journal` sieht 30 Tage
  zurück; bis etwa **16.09.** sind sie in jedem `--verify` sichtbar.
  `tests/conftest.py` verhindert die Wiederholung.

**T-6.10 verlangt einen Verifizierer, der die Installationsanleitung
maschinell in einem frischen Nutzerkonto fährt.** Ich habe `docs/INSTALL.md`
am 17.08. von 4 auf 21 Units gebracht — sorgfältig gegen den Ist-Stand
geschrieben, aber **nie ausprobiert**. Das ist der Punkt, an dem meine Arbeit
am ehesten falsch ist.

---

## 6. Reihenfolge für den ersten Tag

1. `bash tests/verify/verify-frozen.sh` — er läuft (am 17.08. belegt: 37
   Artefakte unverändert). Erste Zeile jedes Gates.
2. `PLAN.md` lesen, besonders die Vorab-Festlegungen: **eigener Branch, eigener
   Worktree**, kein Merge in den schmutzigen Hauptbaum.
3. **T-5.9.v**, dann **T-7.2.v**, dann **T-4.6.v** — siehe §1.
4. Der Rollenwächter, wenn die Reibung stört: drei xfail warten (§2).

Und die Frage, die diese Woche dreizehnmal etwas gefunden hat:

> Wer ruft das auf, im Betrieb, heute — und woran sehe ich das, ohne den
> Aufruf selbst zu schreiben?

Sie steht in `CLAUDE.md`, mit den Belegen.
