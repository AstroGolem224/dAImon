# Plan: Reviewer-Session für die offenen Verifizierer

_Round 5 revision — MAX_ROUNDS erreicht ohne APPROVED. Die fünf Befunde
der Schlussrunde sind eingearbeitet; die Abnahme dieser letzten Fassung
liegt bei Matthias (siehe PLAN-REVIEW-LOG.md)._

## Goal

Eine Sitzung mit `DAIMON_ROLE=reviewer` baut die offenen Verifizierer und
klärt die vorbestehenden Rot-Fälle. **Erfolg ist ein Ausgangs-LEDGER, kein
grünes Gate:** jeder Posten endet als `gruen`, `mensch-blockiert`,
`umgebungs-blockiert`, `zieltask-offen` (das Prüfziel ist planmäßig noch
nicht gebaut, z. B. die Eyes-Unit für T-5.13) oder `produktdefekt-rot`;
„gebaut und ehrlich rot"
ist ein gültiger Ausgang und wird nie als Abnahme ausgegeben. Der Ledger
vermerkt je Posten auch die PROVENIENZ (welcher Kontext hat gebaut, was
hat er gelesen). Offen: T-3.14.v, T-3.15.v, T-4.4.v–T-4.16.v, T-4.17.sh,
T-4.18.sh, Klärung T-0.9 und T-3.12 K4/K6. `T-4.17.v` (zweiter Ordnung,
Anhang E) bleibt zurückgestellt — `T-4.17.sh` selbst nicht.

## Vorab-Festlegungen (gelten für jeden Schritt)

* **Benannter Reviewer-Branch statt losem Worktree.** Der Hauptbaum trägt
  ein unautorisiertes `T-3.9.sh`/`FROZEN`-Paar; verschachtelte Läufe
  (T-3.13b, T-3.14 u. a.) würden GEGEN diese Fassung messen, auch
  ungestagt. Deshalb: Branch `reviewer/p4-verifizierer` vom
  protokollierten HEAD, frischer Worktree AUF DIESEM BRANCH, alle Commits
  nur dort. Übergabe an Matthias als Branch + Commit-Hashes; KEIN Merge
  und kein Cherry-Pick in den schmutzigen Hauptbaum durch diese Session.
* **T-3.13b-Transfer mit Provenienz:** die T-3.13b-Artefakte sind im
  Hauptbaum UNTRACKED und fehlen im frischen Worktree. Transfer nur als
  inventarisiertes Patch (Dateiliste + sha256 je Datei am Ursprung),
  Einspielen in den Reviewer-Branch, Hash-Abgleich gegen die unberührten
  Originale VOR dem ersten Testlauf.
* **FROZEN-Inventar nach kanonischer Vorgabe:** eingefroren werden
  `T-3.13b.sh`, `T-3.14.sh`, `T-3.15.sh` und JEDER `T-4.4.sh`–`T-4.16.sh`
  (die `.v`-Blöcke enden ausdrücklich mit `freeze.sh`; das globale Regime
  friert Reviewer-Verifizierer ein — kein Ledger-Ermessen), jeweils
  einschließlich aller repo-lokalen Laufzeit-Helfer.
  **Helfer-Hash-Sperre, maschinell und geschlossen:** Abhängigkeiten
  werden über eine GESCHLOSSENE Deklarationsgrammatik geführt (wörtliche
  Pfade unter `tests/verify/**`/`tests/harness/**`, rekursiv validiert).
  Berechnete Pfade (`$HIER`, Task-Interpolation — der Bestand nutzt sie)
  sind zulässig, WENN ihr aufgelöstes Ziel in der endlichen, deklarierten
  Menge liegt; alles darüber hinaus wird zurückgewiesen, und jeder
  Bestands-Wrapper, der dafür umgeschrieben werden müsste, bekommt einen
  eigenen autorisierten `.v2`-Task statt einer stillen Änderung.
  Ergänzend läuft eine LAUFZEIT-Dateiöffnungs-Spur (rekursiv ab
  Prozessstart, Kindprozesse eingeschlossen) über Gut-, Mutanten- UND
  Echtbaum-Lauf. **Gegenstand ist nur Prüf-Rahmenwerk:** Öffnungen unter
  den SUBJEKT-Wurzeln (Produktquelle `daimon/**`/`face/**`,
  Fixture-Bäume, erzeugte Testdaten) sind KEINE Abhängigkeiten — verfolgt
  und deklarationspflichtig ist ausschließlich ausführbarer
  Verifizierer-Code und seine Helfer. Jede dort entdeckte, nicht
  deklarierte Abhängigkeit lässt das Einfrieren SCHEITERN. Wo der
  kanonische Vertrag einen Pfad festschreibt (z. B.
  `tests/verify/lib/sandbox_units.sh`, Anhang D), wird der Pfad nicht
  verschoben.
* **Voraussetzungs-Task „Freeze-Erweiterung", einzeln autorisiert:** die
  Änderung an `freeze.sh`/`verify-frozen.sh` ist Sicherheitsmaschinerie
  und läuft als EIGENER, von Matthias beim Kickoff freizugebender Task
  mit Akzeptanzkriterien (Deklarationsgrammatik + Laufzeitspur) und
  eigenen Mutanten, deren jeder an der RICHTIGEN Prüfung scheitern muss:
  (a) Wrapper mit GÜLTIG aktualisiertem Hash, der einen im Manifest
  absichtlich FEHLENDEN Helfer ruft → der Fehlschlag muss der
  Abhängigkeits-Entdeckung zugeschrieben sein, nicht dem alten
  Hash-Vergleich; (b) ein undeklarierter TRANSITIVER Helfer; (c) ein
  dynamisch aufgelöster Pfad außerhalb der deklarierten endlichen Menge.
  Ein bloß manipulierter, bereits gelisteter Helfer beweist nur das alte
  Hashing und zählt nicht als Nachweis. Atomarer Commit — BEVOR irgendein
  Verifizierer den erweiterten Mechanismus benutzt. **Die Migration deckt
  den BESTAND auf:** T-3.10, T-3.11, T-3.12 und T-3.13 delegieren an
  ungehashte Helfer bzw. bauen Pfade variabel; ein echter Scan über die
  26 bestehenden Einträge KANN nicht grün bleiben. Der Task nimmt die
  UNVERÄNDERTEN Bestands-Helfer einmalig autorisiert ins Manifest auf
  (Inhalte unangetastet); nur Wrapper, deren Pfadbau die Grammatik
  verletzt, bekommen eigene `.v2`-Tasks.
* **Re-Freeze (T-3.12.v2):** atomare DREI-Artefakt-Prüfung — Wrapper,
  Helfer (`t312_pruefstand.py`), Manifest. Alte Wrapper-Zeile bleibt bis
  zum Abschluss des Mutantenlaufs; danach in EINEM Commit: Wrapper-Hash
  ersetzt, Helfer-Hash ergänzt, Diff aller drei Artefakte angesehen,
  `verify-frozen.sh` grün. Nur unter ausdrücklich autorisiertem
  `.v2`-Task.
* **Mutanten vollständig, externe Verträge haben Vorrang:** je Task der
  Satz aus Anhang D — außer wo ein externer Vertrag mehr verlangt:
  T-3.14 nutzt alle SECHS Vertragsmutanten (Anhang D kennt den Task
  nicht), T-3.15 alle VIER aus seinem Vertrag (Anhang D nennt nur zwei).
  Kriterium-zu-Mutant-Matrix; jeder Mutant als erkannt GEMESSEN.
* **Commit-Protokoll:** Für Tasks MIT Freeze-Pflicht EIN atomarer Commit
  (Verifizierer + Fixtures + Mutanten + Freeze + Evidenzverweis), erst
  nach `meta.sh` UND Lauf gegen den echten Baum — kein committeter, aber
  unfixierter Abnahmeskript-Zustand in der Historie. **Sonderregeln ohne
  `meta.sh`-Ziel:** `T-4.17.sh` wird nach seinen VORGESCHRIEBENEN Prüfungen
  committet (pytest grün + externe Prompt-Beobachtung gelaufen) — ohne so
  zu tun, als wäre der zurückgestellte `T-4.17.v` gelaufen. `T-4.18.sh`
  wird VOR jeder Befunderhebung committet (siehe Schritt 8); sein Beleg
  sind Läufe gegen synthetische Befundregister: ein vollständiges →
  Exit 0, und JEDES der folgenden → Exit ≠ 0: leer, unbelegt geschlossen,
  `closed` mit scheiterndem Reproduktions-Handler, `closed` mit
  unbekanntem Handler, `closed` mit ungültigen/grenzverletzenden
  Handler-Daten.
  Vor jedem Commit: `git diff --cached --name-only` gegen eine je Task
  ERZEUGTE, exakte Dateinamens-Allowlist. Ausdrücklich verweigert, außer
  einzeln autorisiert: jede bereits in `FROZEN` gelistete Datei,
  `freeze.sh`/`verify-frozen.sh`/`meta.sh` selbst, `docs/DESIGN.md`,
  `docs/IMPLEMENTATION-PLAN.md`, vorbestehende Dateien unter
  `tests/evidence/**`.
* **Aufräumen:** Vorzustand erfassen (Units, DND, Sockets,
  `pgrep`-Basislinie), `trap`-Restauration, eigene Runtime-Verzeichnisse,
  Leck-Prüfung danach; ohne garantierte Restauration wird der Fall als
  blockiert vermerkt statt ausgeführt. Echte Units NUR über `systemctl`;
  `kill -- -PGID` ausschließlich für selbst erzeugte `setsid`-Gruppen,
  nach Prüfung, dass PGID = bekannter Leader-PID.
* **Evidenz ist lesend:** `tests/evidence/phase3-latency.json` u. a.
  werden ausgewertet, nie erzeugt oder überschrieben.

## Approach

1. **Hauptbaum: erhalten, klassifizieren, Owner-Stopp.** Uncommittete
   Reviewer-Artefakte (T-3.13b-Satz; `T-3.9.sh`+`FROZEN`-Paar) werden
   NICHT verworfen, NICHT pauschal committet. Für das Paar gilt: ohne
   ausdrücklich autorisierten `T-3.9.v2`/`v3`-Task keine Übernahme —
   Matthias entscheidet. Die T-3.13b-Artefakte durchlaufen Mutantenlauf +
   `meta.sh` im sauberen Worktree und werden erst danach committet.
2. **T-0.9 klären** (nicht eingefroren): Listener-Inode gegen
   `/proc/<hub>/fd` korrelieren statt `ss | grep pid`; Positivkontrolle
   mit eigenem Unix-Listener; Mutant mit TCP-Listener im Hub, der erkannt
   werden MUSS.
3. **T-3.12 diagnostizieren:** Entscheidungsbaum (bekannt-gutes Fixture →
   HEAD → Socket-Waisen → Import-Baum). Die Helfer-Hash-Abdeckung von
   T-3.12 kommt bereits EINMALIG aus der Bestandsmigration des
   Voraussetzungs-Tasks (unveränderte Inhalte); Schritt 3 hasht Wrapper
   und Helfer NUR DANN neu, wenn ein nachgewiesener Harness-Defekt eine
   Inhaltsänderung erzwingt (`T-3.12.v2`, Drei-Artefakt-Verfahren).
   Produktdefekt → Befund an den Builder, Test bleibt rot. Umgebung →
   dokumentieren, aufräumen, erneut messen.
4. **T-3.14.v:** dessen Vertrag verbietet das Lesen von `daimon/**` und
   `face/**` vor dem ersten Lauf — Blindheit ist hier VERTRAGLICH. Ein
   frischer, isolierter Autor-Kontext erhält nur Vertrag + gepinnte
   Schnittstelle; sein Werkzeug-Transkript ist Abnahmebestandteil. Ohne
   dieses Transkript oder nach Vertragsbruch ist der Posten GESCHEITERT
   (neuer frischer Autor), nicht „nicht-blind etikettiert".
5. **T-3.15.v:** nur die `n ≥ 20`-Latenz-Evidenz ist ein
   Verifizierer-Kriterium (rot bei unzureichender Evidenz, lesend). Die
   Falsch-Positiv-Woche ist laut Vertrag KEIN Verifizierer-Kriterium und
   steht ausschließlich als `mensch-blockiert` im Ledger.
6. **P4-Verifizierer T-4.4.v → T-4.16.v** nach den bindenden Blöcken in
   `docs/IMPLEMENTATION-PLAN.md` (Z. 1226–1392). Besonderheiten:
   * **T-4.14.v/T-5.13.v wird als kombinierter Vertrag AUSGEFÜHRT, wie
     geschrieben** — beide Einstiege, gemeinsame
     `tests/verify/lib/sandbox_units.sh` am kanonischen Ort, zwei
     Mutantensätze, zwei `meta.sh`-Läufe; die Bibliothek wird über den
     erweiterten (mutantengeprüften) Freeze-Mechanismus fixiert.
     `T-5.13.sh` SCHEITERT (Exit ≠ 0) auf dem echten Baum, solange die
     Eyes-Unit fehlt — ein Erfolg ohne getestete Unit widerspräche seiner
     Positivkontrolle; das Rot steht im Ledger als `zieltask-offen`, nie
     als Grün und nie als Defekt.
   * **exec-Positivfall:** Wegwerf-`.desktop` + Test-Katalog unter
     temporären XDG-Verzeichnissen im Prüfstand-Baum; Produktivkatalog
     unberührt; geprüft wird die echte cgroup des gestarteten Prozesses.
   * **Live-Kanarien vs. Beobachten:** Positivfälle gegen Wegwerf-Ziele
     echt; eng abgefangen nur an gefährlichen Endpunkten
     (`systemctl`-Stub-Muster); jede Attrappe mit Positivkontrolle, dass
     das Abfangen selbst greift.
7. **T-4.17.sh:** pytest-Anteil plus EXTERNE Prompt-Beobachtung
   (Fensterliste/Notification-Bus); `/diag`-Zähler nur als Gegenprobe.
   **Zwei getrennte Beobachtungsfenster:** zuerst die Positivkontrolle
   (ein autorisierter Prompt WIRD gesehen) in einem eigenen, markierten
   Fenster; dann Beobachter zurücksetzen; dann das Angriffsfenster, in
   dem die Zählung null sein muss — die Kontrolle darf die Messung nicht
   kontaminieren.
8. **T-4.18, entzirkelt und mit getrennter Autorenschaft:** Kontext A
   pinnt Prüfliste (nummerierte IDs aus Design §1.3/§6/§7) + JSON-Schema
   UND schreibt `T-4.18.sh` — BEVOR irgendein Befund existiert; eigener
   Commit. **Reproduktion ohne Kommando-Loch:** der Prüfer führt NIE
   Kommandos aus dem Befundregister aus; er kennt VORDEKLARIERTE, sichere
   Reproduktions-Handler je Prüflisten-/Befundklasse (z. B.
   „Unit-Property lesen", „Socket-Verbindungsversuch", „Datei-Hash
   vergleichen"), und jeder als `closed` gemeldete Befund referenziert
   einen Handler plus begrenzte Daten — kein ausführbarer Text. Kontext B
   (frisch) erhebt die Befunde. Dann läuft der bereits fixierte Prüfer
   über Befundregister und Belege. Provenienz beider Kontexte steht im
   Ledger.

## Key decisions & tradeoffs

* Aller unabhängige Bau läuft im sauberen Worktree; der Owner-Stopp zum
  T-3.9-Paar blockiert damit nichts außer der Übernahme des Paars selbst.
* Kombinierter T-4.14/T-5.13-Vertrag statt Teilung: teurer jetzt, aber
  keine stillschweigende Vertragsänderung; der Freeze-Mechanismus wird
  erweitert statt der Artefakt-Topologie geändert.
* Atomarer Einzel-Commit bei Freeze-Pflicht: verhindert „committet, aber
  weich" in der Historie; Preis ist ein größerer Commit, den der
  Drei-Artefakt-Diff-Blick abfedert.
* Der Ledger ersetzt jede Gate-Behauptung.

## Risks / open questions

* Owner-Entscheidung zum T-3.9-Paar steht aus (Stopp-Punkt nur für die
  Übernahme; dank Worktree kein Blocker für den Rest).
* T-3.12 kann Produktdefekt sein → bleibt rot, Befund an Builder.
* Die Freeze-Mechanismus-Erweiterung (Zusatzpfade) ist selbst neue
  Maschinerie und einzeln autorisiert + mutantengeprüft, bevor sie
  irgendetwas fixiert.
* Latenzzusagen eng am Dienst messen; GPU-Nachbarlast vor Messläufen
  prüfen. Verschachtelte Ketten nur mit Einzelabrechnung und Nachlauf.

## Out of scope

* Produktcode-Änderungen (Defekte werden Befunde).
* Die zwei Menschmessungen selbst; der API-Token.
* `T-4.17.v` (Anhang E), T-6.x. Der P5-Anteil von T-5.13.v wird gemessen,
  soweit heute Units existieren — nicht mehr.
