# T-6.9 — Abschluss-Sicherheitsreview

**Stand:** 2026-08-14, **nachgetragen am 17.08.** · **Belege:**
`tests/evidence/final-findings.json`
**Erzeugt von:** `tools/final_security.py` (misst, statt abzuhaken)

**Der Satz unten — „Es ist kein unabhängiges Review" — galt bis zum 17.08.**
Seither gibt es eines: neun Befunde, alle behoben, und drei davon betrafen
dieses Werkzeug selbst. Die Nachträge stehen am Ende; der ursprüngliche Text
bleibt unverändert, damit nachvollziehbar ist, was er behauptet hat.

## Was dieses Dokument nicht ist

**Es ist kein unabhängiges Review.** Der Plan weist T-6.9 der Rolle
`reviewer` zu; erzeugt hat es dieselbe Einheit, die Phase 7 gebaut hat. Wer
den Maßstab dessen setzt, was er selbst gebaut hat, benotet sich selbst.

Der Umgang damit ist nicht Bescheidenheit, sondern Bauart: **jede Feststellung
trägt ein Feld `herkunft`.**

| `herkunft` | Was es wert ist |
|---|---|
| `gemessen` | am laufenden System, hier und jetzt — der einzige unabhängige Beleg |
| `direktive` | aus der Unit gelesen; die Wirkung am Prozess **nicht** geprüft |
| `pruefstand` | durch `pytest` belegt — geschrieben vom selben Erbauer |

Acht der zehn Feststellungen sind `gemessen`, eine ist `direktive`, eine
`pruefstand`. Beide sind unten benannt.

**Nachgetragen am 16.08.:** die Stufe `pruefstand` stand bis dahin nur in
dieser Tabelle. **Keine einzige Feststellung trug sie** — die Entscheidung
des Gates war im Fließtext als testbelegt beschrieben und fehlte in der
maschinenlesbaren Datei ganz. Wer nur `final-findings.json` las, sah §7.2b
als `critical / closed / gemessen` und hatte keinen Anhalt, dass davon nur
die Grenze gemessen war. Sie steht jetzt als eigener Satz
`7.2b-entscheidung`.

**Und das Werkzeug hat sich viermal selbst geirrt** — hier stehen gelassen
statt weggewischt:

1. Es durchsuchte für „kein Rohaudio" das **ganze** Datenverzeichnis — dort
   liegen auch `models/` und `voices/`. Die 13 `RIFF`-Treffer waren die
   mitgelieferten Test-WAVs des STT-Modells und ein Zufall in den
   ONNX-Gewichten. Im Archiv selbst: null.
2. Es las ein `ConnectionResetError` am Kontext-Socket als offenen Befund.
   Ein RST **ist** die Abweisung — der Dienst schließt, während ungelesene
   Bytes im Puffer stehen.
3. **Und dann schlug es an seinem eigenen Text an.** Nach der ersten
   Korrektur suchte es `RIFF` nur noch im Archiv — und fand es. Die Augen
   hatten den Bildschirm gelesen, auf dem der Satz über die Falschbefunde
   stand:

   > `BEOBACHTUNG: das Werkzeug hat sich zweimal selbst geirrt — die 13
   > RIFF-Treffer waren die Test-WA…`

   Das Wort `RIFF` ist kein Audio. Ein WAVE-Kopf ist `RIFF`, vier Bytes
   Größe, dann `WAVE`. Danach wird jetzt gesucht — nach der **Struktur**,
   nicht nach vier Buchstaben. Nebenbei ist das der beste Beleg dafür, dass
   der Bildschirmmitschnitt tut, was er soll.

4. **Und der vierte hat sich vor den anderen versteckt** — gefunden am 16.08.
   vom unabhängigen Review, nicht vom Werkzeug selbst. Es zählte eine Antwort
   `{"ok": false}` am Kontext-Socket als **Abweisung**. Das ist die
   Verwechslung, die genau dieser Befund nicht machen darf: wer eine Antwort
   bekommt, ist **angenommen** worden — abgelehnt hat dann das Gate dahinter,
   mangels Rundenmarke, und so sieht im Normalbetrieb **jede** Anfrage aus.
   Ein Hub ganz ohne Peer-Prüfung hätte §7.2b weiter grün gemeldet.

   Die Zeile war zugleich wirkungslos: gesucht wurde `ok": false` **mit**
   Leerzeichen in einer Zeichenkette, aus der vorher alle Leerzeichen
   entfernt worden waren. Zwei Fehler, die einander verdeckt haben — der
   zweite machte den ersten folgenlos, und wer nur den zweiten repariert
   hätte, hätte den ersten scharf gestellt.

Ein Prüfer, der seine eigenen Falschbefunde nicht findet, findet auch keine
echten. Alle vier Male war das System in Ordnung und das Werkzeug nicht.

**Was daraus folgt, und zwar als Bauart:** die Auswertung hat jetzt einen
eigenen Prüfstand (`tests/test_final_security.py`) und drei Ausgänge statt
zwei — `abgewiesen`, `beantwortet`, `unklar`. Ein Deuter mit zwei Ausgängen
muss Unverstandenes einem davon zuschlagen, und „abgewiesen" ist die bequeme
Wahl.

## §7.2a — Netzsperre

**`RestrictAddressFamilies=AF_UNIX` wirkt auf dieser Maschine** — gemessen,
nicht angenommen: eine Wegwerf-Unit mit der Direktive stirbt beim Anlegen
eines `AF_INET`-Sockets. Das ist die einzige **Kernelgrenze** des Entwurfs und
gilt damit auch gegen einen kompromittierten eigenen Prozess.

Alle zwölf gesperrten Units tragen sie. **Herkunft `direktive`** — die Wirkung
im laufenden Prozess ließe sich nur belegen, indem man Code in ihn trägt.
Diese Schwäche ist benannt und nicht geschlossen.

### Offen: der Zusagentext ist zu weit

> §7.2a (alte Fassung): „für `hub`, `auth`, `ears`, `eyes`, `mind`, `face`,
> **alle Broker** und alle GPU-Worker."

Gemessen:

| Unit | `RestrictAddressFamilies` |
|---|---|
| `daimon-egress` | `AF_INET AF_INET6 AF_UNIX` |
| `daimon-lokal-broker` | `AF_INET AF_UNIX` |
| `daimon-cli-broker` | `~` (gar keine Beschränkung) |

Der Zuschnitt ist **gewollt** — die Unit sagt es selbst: „Kein
`RestrictAddressFamilies=AF_UNIX`: die CLI MUSS ins Netz." Falsch war der
Satz im Entwurf, nicht die Unit.

**Berichtigt am 14.08.:** §7.2a nennt die gesperrten Units jetzt einzeln und
führt die drei Netzträger in einer Tabelle. Die Grenze lautet nicht mehr
„kein Prozess kann ins Netz", sondern **„nur diese drei können, und keiner
davon wertet Modellinhalt aus."** Gleich mit berichtigt: §1.2 sagte
„Bildschirm **und Ton** durchgehend" und widersprach damit §1.1 — siehe
`Phase-7-Tonmitschnitt-Entscheidung.md`.

## §7.2b — Deklassifizierungs-Gate

Gemessen wurde die **Grenze**, nicht die Entscheidung dahinter: eine fremde
Unit — dieser Prüfprozess — verbindet sich mit `kontext.sock` (Modus 0600) und
bekommt keine Antwort, die Verbindung wird abgebrochen.

**Mit zwei Positivkontrollen, seit dem 16.08.** Ohne sie war „keine Antwort"
nichts wert: ein Prüfer, der überhaupt keine Antwort lesen kann, und ein Hub,
dessen Socket jeden abweist, sehen genauso aus.

* **Derselbe Weg gegen `state.sock`** — derselbe Hub, dieselbe
  Verbindungsart, nur ohne Unit-Allowlist. Er antwortet. Das Schweigen oben
  ist also eine Aussage über die Allowlist und nicht über den Prüfer.
* **Das Journal des Hubs nennt die abgewiesene Unit beim Namen**
  (`Kontextanfrage von fremder Unit`, mit aufgelöster Unit im Feld). Damit
  ist belegt, dass die Gegenstelle wirklich aufgelöst und *wegen ihrer Unit*
  abgewiesen wurde — und nicht wegen eines gestorbenen Fadens.

Die Entscheidung selbst (Rundenmarke, Bildschirmbezug, proaktiv) steht als
eigener Satz `7.2b-entscheidung` mit `herkunft: pruefstand`. Sie ist von
außen nicht messbar: dafür braucht es einen Menschen an der Taste und einen
Absender, der `daimon-mind` **ist**.

Strukturell und deshalb ohne eigene Regel: **proaktives Verhalten sieht weder
Bildschirm noch Archiv**, weil die proaktive Abweisung vor allen anderen
Prüfungen steht.

## §7.2c — Egress

**Der Befund dieses Reviews.** `/proc/<pid>/environ` des laufenden
`daimon-mind` enthielt `ELEVENLABS_API_KEY` (51 Zeichen) und
`MISTRAL_API_KEY` (32) — geerbt aus der Umgebung des systemd-Benutzermanagers.

§7.2c sagt: *„`mind` hat weder Token noch `AF_INET`."* Die Netzsperre hielt.
Der Satz zum Token galt nur für **einen** Token: `anthropic-token` ist per
`InaccessiblePaths` gesperrt, fremde Schlüssel waren es nicht.

Direkt ausnutzbar war es nicht — `AF_UNIX` lässt den Prozess nicht ins Netz.
Aber der Egress trägt Prompt-Körper, ohne sie zu deuten: ein kompromittierter
Mind hätte einen fremden Schlüssel als **Text** hinausgeben können.

Geschlossen mit `UnsetEnvironment=` in der Mind-Unit, danach neu gemessen:
95 Variablen, keine verdächtige mehr. **Das ist eine Denylist und damit die
schwächere Hälfte** — ein Schlüssel, der morgen anders heißt, steht wieder
drin. Der strukturelle Weg ist, solche Werte gar nicht erst in die Umgebung
des Benutzermanagers zu geben; das ist Sitzungskonfiguration und gehört nicht
ins Repo.

**Nicht belegbar, und das bleibt so:** die Domain-Beschränkung und das
Kontingent des Egress. Ohne Token startet der Dienst nicht — er sagt ehrlich
`kein_token` ab. Was nicht läuft, lässt sich nicht messen.

## §7.2d — Aufbewahrung im Archiv

Am echten Archiv gemessen, nicht an einer Attrappe:

* `archiv.db` 0600, Verzeichnis 0700
* alle Zeilen auf Stufe `redacted` — **keine einzige auf `full`**
* Arten `ocr` und `titel`; **kein Rohaudio**, weder als Art noch als
  WAVE-Kopf (`RIFF`+`WAVE`) in `archiv.db`, `-wal` oder `-shm`

**Die Zeilenzahl steht seit dem 16.08. im Beleg, und der Status hängt an ihr.**
Beide Aussagen sind auf einem **leeren** Archiv wahr und wertlos — und ein
Archiv wird leer, wenn der Dienst tot ist oder die Redaktion alles sperrt,
also genau in den Fällen, die dieser Abschnitt finden soll. Bei null Zeilen
lautet der Status jetzt `unbelegt`, nicht `closed`. Dass hier bisher Zeilen
standen, war Zufall und keine Kontrolle.

## Was dieses Review nicht abdeckte — und was davon am 17.08. nachgeholt ist

Die Liste stand am 14.08. so da. Drei der vier Punkte sind erledigt; sie
bleiben stehen, damit nachvollziehbar ist, was offen *war*.

1. ~~**Die Kette Push-to-Talk → gesprochene Frage → Kontext im Modell.**~~
   **Gemessen am 17.08., 6 von 6 Stationen**, mit
   `tools/naht_messen.py`. Design §7.2b trägt im Betrieb; dazu T-7.4 (erste
   `transkript`-Zeile im Archiv) und T-3.11c (der lokale Weg antwortet).
   Die Vorrichtung urteilt in drei Zuständen — `nicht messbar` ist der
   Grund, warum es sie gibt.
2. **Die Wirkung der Unit-Härtung im laufenden Prozess** (siehe `direktive`).
   Bleibt offen.
3. ~~**Die eingefrorenen Zusagen.**~~ **`verify-frozen` ist am 17.08.
   gelaufen:** 37 Artefakte unverändert, Abhängigkeiten geschlossen. Der
   Rollenwächter war nie das Hindernis — er greift auf *schreibende*
   Kommandos, und `bash …/verify-frozen.sh` ohne Umleitung ist keins.
   Ausgelöst hatten ihn die `2>&1` im selben Kommando.
4. ~~**Ein zweites Paar Augen.**~~ **Am 17.08. erfolgt**, neun Befunde, alle
   behoben (`2788cfc` … `6138e00`). Einer davon war halb falsch und ist als
   solcher festgehalten (`d7e589d`).

## Nachtrag 17.08.: was das zweite Augenpaar an DIESEM Werkzeug fand

Der Prüfer hat sich nicht drei-, sondern **viermal** geirrt — der vierte
Fehler steht oben. Dazu kamen zwei Lücken, die kein Fehler waren, sondern
eine fehlende Frage:

* **§7.2b war nur eine Negativkontrolle.** „Eine fremde Unit bekommt keinen
  Kontext" ist ohne Gegenprobe nichts wert: ein Prüfer, der überhaupt keine
  Antwort lesen kann, sieht genauso aus. Jetzt stehen zwei Positivkontrollen
  daneben — derselbe Weg gegen `state.sock` (der antwortet), und der
  Journal-Eintrag des Hubs, der die abgewiesene Unit beim Namen nennt.
* **§7.2d war auf einem leeren Archiv trivial grün.** „Keine Zeile auf
  `full`" und „kein Rohaudio" sind ohne Zeilen beide wahr und wertlos — und
  ein Archiv wird leer, wenn der Dienst tot ist oder die Redaktion alles
  sperrt, also genau in den Fällen, die der Abschnitt finden soll. Die
  Zeilenzahl steht jetzt im Beleg; bei null lautet der Status `unbelegt`.

**Und §7.2c steht seit dem 17.08. auf `closed`** statt `unbelegt`:
`daimon-mind` lief nicht, weil seine Unit kein `[Install]` hatte und keine
Socket-Unit sie zog (`d80e8e9`). Zwei Messungen hingen daran, ohne dass eine
Zusage gebrochen war — der Dienst war schlicht nicht da.

## Nachtrag 17.08.: das Audit, das nichts aufschrieb

Nicht Teil des ursprünglichen Reviews, aber sein direktes Ergebnis: die
Frage „was hat das Gate wann freigegeben" war unbeantwortbar. Das Audit war
**doppelt** tot — nie angeschlossen (`getattr(self, "audit", None)`, ein Feld
ohne Existenz), und selbst angeschlossen hätten drei fehlende Pflichtfelder
jeden Satz abgelehnt, verschluckt vom `except` darunter. Behoben in
`cd49823`; live belegt in beide Richtungen:

    07:58:44  seq 86  ok      turn ffd2c27a8148  init foreground
    07:59:47  seq 87  denied  turn -             init background

Die Hash-Kette hat außerdem ihren **zweiten Strom** bekommen (`855def8`,
`d981689`): `verankern()` hatte genau einen Aufrufer, und den rief niemand.
Von den drei Prüfstellen, die `audit.py` in seinem Modulkopf nennt,
existierte eine — die CLI, die ein Mensch startet. Jetzt stehen alle drei,
und die tägliche prüft **von außen und ohne Schreibrecht**. Der Modulkopf
sagt dazu: *„Findet keine davon statt, ist die Kette wertlos und gehört
gestrichen statt behauptet."*
