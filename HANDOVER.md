# Übergabe — Stand 2026-08-03

Alles, was nicht aus dem Repo hervorgeht. Die vorige Fassung ist über zwölf Tasks
gewachsen und war nicht mehr lesbar; diese ist neu geschrieben und ersetzt sie.

---

## Wo wir stehen

| | |
|---|---|
| **Gate P−1** | 8 von 9 grün. Rot nur `T--1.12` — Messung nicht gelaufen, korrekt so |
| **Gate P0** | 11 von 11 grün — **aber `T-0.12` war davon ein hohles Grün**, siehe unten. Seit T-0.12.v2 belegt |
| **Gate P1** | **ROT nur noch wegen `T-1.10`** — und dessen Messfenster ist unbrauchbar, siehe unten. `T-1.7` seit v5 grün (95 Prüfungen) |
| **Gate P2** | **GRÜN** (02.08.): `T-2.4` 17, `T-2.5` 40, `T-1.5` 25 — Idle-CPU **0,000 %**. `verify-frozen` zählte damals 12, heute 20 |
| **Phase 3** | **Block 1 (Ohren) steht**, T-3.1–3.4 eingefroren. **Block 2:** T-0.12.v2 eingefroren, **T-3.7** committet und live belegt, **T-3.9 und T-3.8 fertig und EINGEFROREN** — T-3.9 mit 211 Prüfungen, T-3.8 mit 177, beide 0 rot und je 5 von 5 Mutanten. Offen: 3.10–3.13b, 3.14–3.15 |
| **Phase 2** | **abgeschlossen.** T-2.1 bis T-2.5 und T-2.7 stehen und sind eingefroren. T-2.6 optional, entfällt |

**Zweiundzwanzig Einträge in `FROZEN`**: 19 Verifizierer + 3 Harness-Dateien. `T-3.9` und `T-3.8` kamen am 03.08. dazu.
**`FROZEN` deckt seit T-1.1.v2 auch die Harness ab** — `pixelprobe.py`,
`vollbildfenster.py`, `moodprobe.py`. `freeze.sh` **liest** die Abhängigkeiten aus dem
Skript, statt eine Liste zu pflegen, die veraltet.
`pytest` grün mit 4 per `xfail(strict=True)` dokumentiert roten.
`cargo test -p face` 74 von 74.

> **`T-1.7.sh` ist rot, und das ist eine Entscheidung, kein Unfall.** T-2.7 gibt dem
> Face das Recht, `wahrnehmung_aus` zu senden; der eingefrorene Verifizierer verlangt
> `face darf hoechstens bubble_dismiss`. Das Einfrieren hat damit genau das getan,
> wofür es da ist — die Aufweichung ist nicht stillschweigend passiert.
> **Nächster Schritt: ein `T-1.7.v4`-Task**, der die Prüfung auf
> `{bubble_dismiss, wahrnehmung_aus}` zieht und dabei enger macht (kein Produzent mit
> `*_an`-Typ, Ziel nie aus der Nachricht), neue Mutanten, `meta.sh`, neu einfrieren.
> Präzedenzfall ist `T-1.7.v3`. **Den eingefrorenen Verifizierer nicht von Hand
> anfassen.** — **Erledigt am 02.08. als `T-1.7.v4`**: Obergrenze statt exakter Menge
> (das Gut-Muster stammt aus der Zeit vor T-2.2 und hat gar keinen `face`-Eintrag),
> plus die Einseitigkeit für **alle** Produzenten. Zwei neue Mutanten, beide isoliert.
> `meta.sh`: 7 von 7 erkannt, neu eingefroren.

> **`T-1.7.sh` ist trotzdem rot — an einer Stelle, die es schon vorher war.** Die
> Pixelprobe „der verwechselbare Pfad sieht sichtbar anders aus" verlangt **>20 000**
> abweichende Pixel und begründet das damit, der escapte Pfad sei länger, das Fenster
> werde breiter. **Das Fenster hat feste Breite.** Gemessen: 664 Pixel bei einem
> Rauschen von 0. Derselbe Wert in der **alten, eingefrorenen Fassung vor T-2.7** —
> also kein Regress, sondern ein Befund, der nie aufgefallen ist.
>
> **Die Zusage selbst hält**: der Dialog zeigt `"~/Bilder/url\u0430ub.png"` gegen
> `"~/Bilder/urlaub.png"`, beide Aufnahmen angesehen. Die API-Prüfung
> (`pfad_saeubern` liefert Unterschiedliches, rein ASCII) ist grün.
>
> **Erledigt als `T-1.7.v5` am 02.08.** Die Schwelle ist jetzt ein Verhältnis, keine
> Zahl: der Verifizierer rendert eine dritte Kontrolle (ein einziges ausgetauschtes
> lateinisches Zeichen) und misst daran die Einheit „ein Glyph" — 12 Pixel. Der
> escapte Pfad muss ein Vielfaches abweichen (183). Gemessen wurde auch der kaputte
> Fall, indem der Agent aus dem Mutantenbaum lief: **0 Pixel, pixelgleich.**
>
> **Der größere Fund dabei:** der Live-Teil stieg bei Fixture-Läufen aus („ein Fixture
> ist ein Ersatzbaum ohne GTK-Prozess") — das ist falsch, es fehlten drei Module.
> `meta.sh` erreichte deshalb **nie** die 43 Live-Prüfungen. Jetzt startet der
> Verifizierer den Agenten **aus dem geprüften Baum**, und `vorschau-ohne-escaping`
> hat `agent.py` + `common/logging.py` + `common/config.py` dazubekommen: 15 rote
> Prüfungen statt 11, vier davon aus dem gerenderten Dialog.
>
> **Prüf das bei jedem Verifizierer mit Fenster- oder Hardwareanteil.** In Phase 3
> (Mikrofon, GPU) und Phase 5 (ScreenCast, OCR) hat derselbe Aufbau dieselbe Lücke.

> **Der Rollenwächter lief seit Wochen gar nicht.** `.claude/settings.json` war
> **kaputtes JSON** — die Quotes um `$CLAUDE_PROJECT_DIR` waren nicht escapt, seit
> Commit `21c9a78`. Claude Code lädt eine unparsbare Settings-Datei stillschweigend
> nicht, also feuerte der `PreToolUse`-Hook nie: `touch tests/evidence/.probe` ohne
> gesetztes `DAIMON_ROLE` ging durch, obwohl `roles.toml` „unknown darf nichts
> schreiben" sagt. Am 03.08. repariert. **Derselbe Fall wie Nummer 15**: der
> Mechanismus war tot, und niemand hat es gemerkt, weil niemand geprüft hat, dass er
> überhaupt anspringt. Die zweite Linie (`.githooks/pre-commit`) lief die ganze Zeit.
> **Neu in `tests/test_rollen.py`**: eine Prüfung, dass `settings.json` parsbar ist,
> auf `role_guard.py` zeigt, und dass der Hook einen Schreibversuch ohne Rolle
> ablehnt.
>
> **Praktische Folge für die nächste Sitzung:** `DAIMON_ROLE` muss jetzt in der
> **Umgebung von Claude Code selbst** stehen (nicht als Präfix im Bash-Aufruf — der
> Hook ist ein eigener Prozess und sieht das Präfix nicht). Ohne Variable ist jedes
> Schreiben im Repo blockiert.

> **T-3.9 steht, aber die Reviewer-Seite ist NICHT blind entstanden.** Der Verifizierer
> `tests/verify/T-3.9.sh` wird von kimi gebaut — der Auftrag ging jedoch erst raus, als die
> Implementierung bereits **committet** war, und kimi hat sie gelesen: in seiner Planung
> stehen wörtlich meine Anker aus `daemon.py` und `state.py`. Für die **Mutanten** ist das
> nötig, die leiten sich aus dem abgenommenen Stand ab. Für den **Verifizierer** war es
> untersagt. Damit ist für T-3.9 nicht auszuschließen, dass die Prüfung um die
> Implementierung herum gebaut wurde statt um die Zusage.
>
> Entschieden am 03.08.: kimi läuft weiter, der Verlust steht hier. **Präzedenzfall ist
> T-1.7 Teil 2** („Für T-1.7 Teil 2 hat der Builder den bereits geschriebenen Verifizierer
> gelesen"), nur mit vertauschten Rollen — und die Ursache ist dieselbe: der Auftrag
> untersagte das Ändern, nicht das Lesen.
>
> **Was das für einen künftigen `T-3.9.v2`-Task heißt:** es braucht einen Verifizierer von
> einem Agenten, der `daimon/face/tts.py`, `daimon/hub/sprechtext.py` und
> `daimon/hub/abkuehlung.py` **nie gesehen hat** — ein Gegenlesen des vorhandenen reicht
> nicht. Ein Verifizierer, der die Implementierung kennt, prüft, was sie tut, und nicht,
> was sie versprochen hat. Wer den Auftrag schreibt: **Lesen ausdrücklich verbieten**, nicht
> nur Schreiben.

> **T-3.8 ist der erste echte Blindtreffer dieses Projekts: 174 von 177 beim
> ersten Lauf.** Zum Vergleich T-3.9 am selben Tag: 17 rot. Der Unterschied war
> nicht mehr Sorgfalt, sondern **zwei Verfahrensänderungen**, und beide gehören in
> jeden künftigen `.v`-Task:
>
> 1. **Der Reviewer-Auftrag ging ZUERST raus.** Die Implementierung existierte
>    nicht, als der Prüfstand entstand — es gab also nichts zu lesen. Bei T-3.9
>    war der Code schon committet, und damit war nicht auszuschließen, dass die
>    Prüfung um ihn herum gebaut wurde.
> 2. **Das Protokoll stand im PLAN, nicht im Code.** Socketpfad, `art`-Werte,
>    Feldnamen, Absagegründe, Konfigurationsschlüssel — alles vorher festgelegt
>    und für beide Seiten bindend (`§2` des Plandokuments). Bei T-3.9 musste der
>    Prüfstand das Protokoll **entdecken** und brauchte Kandidatenlisten. **Blind
>    heißt nicht raten.** Wer den nächsten Auftrag schreibt: erst den Vertrag,
>    dann die zwei Aufträge.
>
> Die drei roten waren: zwei echte Defekte von mir (siehe Fälle 22 und 23 unten)
> und eine **Lücke in meinem eigenen Plan** — `§2` sagte nicht, was `modell` bei
> fehlendem Modellverzeichnis heißt. Der Prüfstand verlangte den kanonischen
> Namen, mein Dienst meldete den konfigurierten. Das konnte keine Implementierung
> erfüllen; präzisiert ist es jetzt im Plandokument, und der Prüfstand vergleicht
> gegen den konfigurierten Namen.

**Quelle der Planungsdokumente ist `/home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/`,
`docs/` ist die Kopie. Beide pflegen.**

---

## Was Matthias tun muss

**Zwei Entscheidungen, eine Handbewegung.**

1. **`T-1.10` läuft seit dem 02.08. neu — frühestens ab dem 07.08. abnehmbar.**
   Das alte Messfenster ist verworfen und liegt samt Begründung unter
   `tests/evidence/verworfen/`. Stand 03.08. nachmittags: `days=2, crashes=0`, `idle_cpu_p95=0,216 %`, alle vier Units
   laufen, Timer aktiv, alle fünf Minuten ein Datenpunkt.
   **Nicht vergessen, wenn die fünf Tage voll sind:** `fehlalarme`, `ablenkungen`,
   `verdict` und `docs/phase1-verdict.md` — das sind Urteile, keine Messwerte, und
   der Verifizierer wertet `null` und `pending` ausdrücklich als rot.
   Warum überhaupt neu:
   `tests/evidence/phase1-usage.json` sagt `crashes: 135` bei `days: 3` und
   `needs_input_events: 0`. Gemessen wurde ein System, das im Neustart-Karussell hing
   (Zähler 97, siehe `RuntimeDirectory` unten) und dreimal neu startete — nicht
   Normalbetrieb. Seit dem 02.08. laufen die Units stabil; ab da kann die Uhr
   ehrlich laufen.
   **Und:** `needs_input_events: 0` heißt, das Pet hat in drei Tagen keine einzige
   Rückfrage gesehen. Ohne die ist „stört nicht" keine Aussage — die Messung braucht
   echte Sitzungen, nicht nur Laufzeit.
2. **20 deutsche Sätze einsprechen — T-3.8 hängt daran.** Es gibt auf dieser Maschine
   **keine deutsche Referenzaufnahme mit Text**: `spikes/wakeword/samples/probe.wav`
   enthält 16 Wiederholungen von „Embershard" ohne Satzreferenz, und T−1.2 endet mit
   genau dieser Empfehlung. Ich lege Sätze und ein Aufnahmeskript bereit, du liest sie
   einmal vor (~5 Minuten). Entschieden am 03.08.: **deine Stimme, dein Mikrofon, dein
   Raum** — Piper-Synthese kommt zusätzlich als Regressionstest, nicht stattdessen.
3. ~~**`piper` installieren**~~ — **der Eintrag war doppelt falsch. Erledigt am 03.08.,
   und zwar ohne dich.** `piper-tts` gibt es in den Arch-Repos nicht (`extra/piper` ist
   eine Maus-Konfigurations-GUI), und Design §8.2 schließt Piper **als Bibliothek**
   ausdrücklich aus (GPL-3.0, R18/α). Gebraucht wurde `sherpa-onnx` (Apache-2.0, liegt
   jetzt im venv); die Stimme `de_DE-thorsten-high` lag schon unter
   `spikes/nvidia-voice/models/` und ist CC0. **Lehre: ein Installationsbefehl in diesem
   Dokument ist eine Behauptung wie jede andere — dieser war nie ausgeführt worden.**

3. ~~**Reboot-Test für T-3.9**~~ — **erledigt am 03.08., bestanden.** 239,8 s
   Ausfallzeit, erwartete Restfrist 86160,2 s, gemeldet **86160,221 s**; Boot-ID
   gewechselt, die Ablage trägt noch die alte (also wirklich der Wanduhrzweig),
   und die Positivkontrolle mit der kurzen Frist ist frei. Danach eingefroren:
   `FROZEN` 20 → 21, `verify-frozen` bestätigt. Protokoll unter
   `spikes/tts-abkuehlung/runs/`.

Dazu, wenn es passt:

4. **T−1.12** (NVIDIA-Sprachstack) ist weiterhin ungemessen. Werkzeug liegt unter
   `spikes/nvidia-voice/` samt `SPEC.md`. Nicht blockierend.

**T−1.4, Reboot-Teil: erledigt am 03.08.** nach dem Neustart — `reboot_prompted=false`,
der `restore_token` hält über einen echten Reboot ohne Dialog. Protokoll unter
`spikes/portal/runs/`. Dabei nebenbei belegt, was vorher unbelegt war: **alle vier Units
stehen nach dem Reboot bei `NRestarts=0`**, der `RuntimeDirectory`/`Preserve`-Fix hält also
über einen echten Neustart. (Und ein Fehler von mir: der Check lief zuerst unter dem
venv-Python und starb an `No module named 'dbus'`. Er braucht System-Python — so, wie es
weiter unten auch steht.)

---

## Wie hier gearbeitet wird

**Rollen sind maschinell durchgesetzt.** `.claude/hooks/role_guard.py` als
`PreToolUse`, `.githooks/pre-commit`, `tests/verify/verify-frozen.sh`.

```bash
export DAIMON_ROLE=investigator   # spikes/, docs/, tests/evidence/
export DAIMON_ROLE=builder        # Produktivcode, NICHT tests/verify/
export DAIMON_ROLE=reviewer       # Verifizierer, NICHT daimon/ face/
```

> **Falle:** der Hook wird gegen die **CWD des Tool-Calls** aufgelöst. Keine `cd` in
> Bash-Aufrufen, nur absolute Pfade. Und `2>&1` löst die Schreib-Erkennung aus.

**Das Verfahren, das sich durchgehend bewährt hat:** Verifizierer und
Implementierung entstehen **parallel und blind** gegen dieselbe Akzeptanzliste —
der Builder sieht den Verifizierer nicht, der Reviewer den Code nicht. Bei T-0.8
trafen sich beide Seiten mit **48 von 48 grün** beim ersten Lauf. Die echten
Befunde kamen trotzdem erst beim Gegenlesen.

**Delegation:** `codex exec --dangerously-bypass-approvals-and-sandbox -C <repo> -`
mit dem Auftrag auf stdin; `kimi -p "$(cat auftrag.md)"` (**kein** `-y`/`--auto`,
kollidiert mit `-p`). Aufträge nach `/tmp/claude-…/scratchpad/`, und sie benennen
Rolle, Auflagen und was **nicht** angefasst werden darf.

**Commits:** deutsch, ausführlich, erklären das *Warum* — und benennen eigene Fehler.
Kein `git add -A` ohne vorheriges `git status`.

**PMTool:** Projekt `dAImon`, ID `d9e36f7c-8e0f-480f-890d-7c52258ed12c`.

---

## Der wiederkehrende Fehler dieses Projekts

**Die bequeme Größe zu messen ist nicht dasselbe wie die richtige zu messen.**
Elf dokumentierte Fälle. Die Lehre in einem Satz: **ohne Positivkontrolle ist
„0 Treffer" keine Aussage** — schwieriges Wort, schlechte Aufnahme und kaputter
Aufbau sind dann nicht zu unterscheiden.

Und der Zusatz, der später kam: **ein grüner Verifizierer sagt nichts, solange
nicht gezeigt ist, dass er rot werden KANN.** Jeder neue Verifizierer bekommt
mindestens einen Mutanten.

| # | Fall | Was gemessen wurde statt der Zusage |
|---|---|---|
| 1 | Wake-Word | Schwellen nur an Negativbeispielen geeicht |
| 2 | Hook-Kommando | Latenz statt Zustellung — „0,9 ms" war schnell, weil nichts ankam |
| 3 | `commit_gezaehlt` | `grep` mit `head -6` abgeschnitten, Fehlen des Treffers für einen Befund gehalten |
| 4 | T-1.4-Verifizierer | Baute nur, wenn das Binary *fehlte* → startete ein Binary von vorgestern |
| 5 | Sprite-Subsurface | Gate `darf_committen()` war nur scheinbar aktiv — Subsurface hatte keine eigene Region |
| 6 | `CARGO_MANIFEST_DIR` | Zur Bauzeit aufgelöst → „Community-Pet lädt" wäre auch bei zerstörter Kopie grün |
| 7 | `.gitignore` | `target/` hätte Fixtures lautlos gefressen; `meta.sh` nach Clone kaputt |
| 8 | `assert … or True` | Selbst geschrieben, drei Tage nach der eigenen Rüge dafür |
| 9 | T-1.8-Verifizierer | Beobachtete nur den vom Prüfling **selbst geführten** Zähler |
| 10 | T-2.3-Verifizierer | Nahm den *ersten* ausgebliebenen Klick als Treffer — ohne vorher angekommenen |
| 11 | T-2.4-Verifizierer | Suchte `attach(nil` — wayland-rs schreibt `attach(<anonymous>@0` |
| 15 | T-0.12-Verifizierer | Prüfte, dass ein **verschachtelter** `kwin_wayland` mit aktiviertem Script **nicht stirbt** — und tolerierte ausdrücklich, wenn der Ladevorgang nicht im Protokoll stand. „Eine Meldung ist angekommen“ wurde nie geprüft. **Der Watcher war wochenlang tot, Gate P0 meldete 11 von 11.** Behoben in T-0.12.v2 |
| 14 | `FROZEN` selbst | Deckte nur `tests/verify/*.sh` ab. Die eigentliche Messung steht in `tests/harness/*.py` — dort die Toleranz hochzudrehen hätte einen eingefrorenen Verifizierer aufgeweicht, **ohne dass `verify-frozen` etwas merkt**. Behoben in T-1.1.v2 |
| 13 | T-1.7-Pixelprobe | Schwelle >20 000 Pixel, begründet mit „das Fenster wird breiter" — **es hat feste Breite**. Nie nachgemessen, also seit Wochen rot, ohne dass es jemand sah: `meta.sh` überspringt im Fixture-Modus **alle Live-Prüfungen** (47 statt 90), der Mutationstest deckt nur die Hälfte des Verifizierers ab |
| 12 | T-2.7-Verifizierer | Positivkontrolle „Hub aus dem geprüften Baum" verglich `<baum>/daimon` mit `<baum>` — zwei `dirname` statt drei. **Konnte nie grün werden**, also jeder Lauf rot, also **jede Mutante „erkannt", ohne dass ihre Mutation je gemessen wurde** |
| 16 | `.claude/settings.json` | Der `PreToolUse`-Rollenwächter war **verdrahtet und lief nie**: unescapte Quotes machten die Settings-Datei zu kaputtem JSON, und Claude Code lädt eine unparsbare Datei stillschweigend nicht. Gemessen wurde „der Hook steht in der Konfiguration", nicht „der Hook lehnt ab". Seit Commit `21c9a78`. Behoben am 03.08., plus `tests/test_rollen.py`, das den Hook aufruft und eine Ablehnung verlangt |
| 17 | T-3.9, TTFA-Stempel | Gemessen wurde **nach** dem vollständigen `write()` des ersten Segments in die Wiedergabe-Pipe. Eine Pipe hat 64 KiB, ein Segment 96 KB, und `pw-cat` liest in Echtzeit — das `write()` blockiert also 1014 ms im Median. Damit war die gemessene „Latenz“ die **Abspieldauer**. Selbst geschrieben, selbst gefunden, am selben Tag. Jetzt: ein pipe-großes Stück schreiben, stempeln, dann der Rest |
| 18 | T-3.9, stille Ausgabe | Bei unerreichbarer Wiedergabe (`pw-cat` sofort tot, falsches `XDG_RUNTIME_DIR`) meldete der Dienst `gesprochen: true` mit `ttfa_ms: null`. Eine Selbstauskunft ohne Messung, Fall 9 in Reinform. Aufgefallen nur, weil der eigene Messaufbau PipeWire verlor. Jetzt `grund: ausgabe_weg` |
| 19 | `voice.tts_active` | Stand seit Beginn im Zustandsschnappschuss und hatte **keinen Setter** — behauptete also wochenlang „es spricht nichts“, ohne dass das gemessen wurde. Ein Feld, das nur gelesen werden kann, ist eine Zusage ohne Messpunkt. Setter kam mit T-3.9 |
| 20 | T-3.9, synchrone Antwort | `sprich()` gab erst zurück, wenn die Äußerung **fertig gesprochen** war. Damit war „unterbrechbar" (Kriterium 4) praktisch unerreichbar, und weil die Abkühlung am Ende vermerkt wurde, war die Folgeäußerung garantiert eine Absage — die Unterbrechung war also gar nicht prüfbar, ohne die Abkühlung abzuschalten. Gefunden vom fremden Prüfstand, nicht von meinen eigenen Tests: die hatten den synchronen Vertrag mitkodiert |
| 21 | T-3.9, Abkühlung dreimal falsch | Erst am **Ende** vermerkt (zwei schnelle Anfragen liefen beide durch), dann bei der **Freigabe** (ein Probelauf schaltete das Pet für die ganze Frist stumm und würgte die Entdeckungsphase des Prüfstands ab), schließlich bei **`beginnt`**. Drei Versuche, jeder einzeln gemessen — und die richtige Antwort war keiner der beiden naheliegenden |
| 22 | T-3.8, fauler Import | Die **erste** Anfrage log über ihre eigene Latenz: `import numpy` stand faul in `wav_lesen()` und wurde beim ersten Aufruf bezahlt — **außerhalb** des gemessenen Fensters. Selbstauskunft 110,8 ms, Wanduhr des Aufrufers 226,6 ms; bei allen 20 folgenden Anfragen 0,6 ms Differenz. **Meine eigenen Tests konnten das nicht sehen — sie lesen dieselbe Selbstauskunft.** Gefunden hat es die Wanduhr-Gegenprobe des fremden Prüfstands |
| 23 | T-3.8, Wartezeit an der Sperre | Dieselbe Wurzel wie 22, andere Stelle: die Zeit an der `threading.Lock` lag außerhalb von `latenz_ms`. Eine zweite gleichzeitige Anfrage meldete nur ihre Rechenzeit, obwohl der Aufrufer zusätzlich gewartet hatte. Jetzt getrennt gemeldet: `wartezeit_ms`, `latenz_ms`, `gesamt_ms` |
| 24 | T-3.8, Fehler ohne Antwort | Eine leere oder abgeschnittene WAV-Datei lässt `wave.open` mit **`EOFError`** scheitern, nicht mit `wave.Error`. Gefangen wurde nur letzteres → der Bedienthread starb und der Aufrufer bekam **gar keine** Antwort. Das ist die schlechteste aller Absagen: von einem hängenden Dienst nicht zu unterscheiden. Dazu ein Nachzug: `f"{exc or type(exc).__name__}"` ist falsch, weil ein Ausnahmeobjekt immer truthy ist und `EOFError()` einen leeren Text hat |

Dazu zwei Fälle, in denen ein **Mutant** nichts bewies: einmal enthielt die
Fixture-Kopie ein gebautes `target/` der *unmutierten* Quelle, einmal brach das
Erzeugungsskript ab und der zweite Mutant blieb eine unveränderte Kopie. Beide
fielen nur auf, weil *alle* Mutanten grün waren — und das kann nicht sein.

### Angriffsnutzlasten gehören beobachtet, nicht ausgeführt

**Am 02.08. hat ein Verifizierer dreimal den Desktop abgeräumt.** Alle Bildschirme
schwarz, kein Zeiger, kein Terminal, nur noch Strg-Alt-Entf. Kein Absturz: im Journal
steht ein geordneter Reboot mit `basic.target has 'stop' job queued`.

Der Hergang: `T-2.7.sh` feuert 29 unerlaubte Ziele gegen den Hub, um zu beweisen, dass
keins durchkommt. In der Liste steht `"*"`. Gegen die richtige Umsetzung ist das
folgenlos — `ziel` wird in einer Allowlist nachgeschlagen. Gegen den Mutanten
`ziel-aus-der-nachricht`, der genau diese Nachschlagung weglässt, wurde daraus
`systemctl --user stop '*'`. **systemd versteht Globs.** Das stoppt jede Unit der
Sitzung, `plasma-kwin_wayland` eingeschlossen.

Ein Mutant ist dazu da, einem Angriff zu **gehorchen**. Also darf der Angriff nichts
anrichten können. Die zweite, „gefährlichere" Liste war abgesichert — sie wurde nur
gesendet, wenn die erste sauber blieb. Nur stand das Schlimmste in der ersten: ein Glob
ist schlimmer als jeder einzelne Unit-Name, den man bewusst hinschreibt.

**Die Lösung, und sie ist die Vorlage für jeden künftigen Angriffstest:** das Sperrfeuer
läuft gegen einen eigenen Hub, in dessen `PATH` ein `systemctl`-Stub liegt, der seine
Argumente protokolliert und nichts tut. Gemessen wird, was der Prüfling **tun wollte**.
Das ist strenger als vorher, nicht schwächer — vorher war ein Treffer nur an einer
tatsächlich gestoppten Unit zu sehen, jetzt an jedem einzelnen Aufruf. Und weil nichts
mehr ausgeführt wird, stehen jetzt `plasma-kwin_wayland.service`, `basic.target` und
`graphical-session.target` **ausdrücklich in der Angriffsliste**: was vorher zu
gefährlich zum Prüfen war, ist jetzt der Beweis.

Voraussetzung dafür: der Prüfling ruft `["systemctl", ...]` **ohne absoluten Pfad** auf.
Ein zweites Netz prüft deshalb zusätzlich die echten Attrappen und den laufenden Hub —
falls jemand am `PATH` vorbei aufruft.

---

## Fallen dieser Maschine

Alles gemessen, nicht vermutet.

**KWins `callDBus` nimmt höchstens 13 Argumente** — 4 Ziel plus **9 Nutzlast**. Darüber:
`Too many arguments, ignoring N`, und die Meldung kommt beim Empfänger **nie** an. Am
03.08. gemessen (nicht typabhängig, 11 Strings werden wie 11 gemischte Werte gekappt):

```
 8 Nutzargumente (12 gesamt): durchgelassen
 9 Nutzargumente (13 gesamt): durchgelassen
10 Nutzargumente (14 gesamt): GEKAPPT
```

Deshalb geht die Watcher-Nutzlast als **ein JSON-String** (Signatur `"s"`). Wer stattdessen
Felder kürzt, verschiebt die Grenze nur — beim nächsten Feld steht sie wieder da.

> Und eine Warnung zur Messung selbst: ein erster Versuch meldete „keine Beanstandung"
> für alle Größen, weil nur nach `ignoring` gegrept wurde, ohne zu prüfen, ob die Skripte
> **überhaupt liefen**. Erst `print()`-Marken zeigten, dass gar nichts ausgeführt wurde.

**KWin lädt Scripts aus `kwinrc` selbsttätig nach.** Seit `[Plugins]
daimon-watcherEnabled=true` gesetzt ist, holt **jedes `reconfigure`** den Watcher zurück —
auch mitten in einem Verifiziererlauf, auch nach `unloadScript`. Gemessen:

```
vor  reconfigure:  Mutant true,  Bestand false
nach reconfigure:  Mutant true,  Bestand TRUE
```

Wer einen Mutanten messen will, muss den Bestand **stilllegen** (Schlüssel auf `false` +
`reconfigure`) und danach **nachkontrollieren**, dass er weggeblieben ist.

**`bool("nein")` ist `True`.** Ein defensiver Parser, der `bool(wert)` nimmt, lässt einen
Watcher mit falschem Feldtyp **Vollbild behaupten** — und das GPU-Gate hängt daran. Live
aufgetreten. Richtig ist `wert is True`.

**`mmap.mmap(-1, n)` nimmt in CPython `MAP_SHARED`** — und ein *shared* anonymes Mapping
ist unter Linux tmpfs-gestützt: `rw-s`, echte Inode, Name `/dev/zero (deleted)`. Ein
Puffer mit Mikrofonmaterial hätte damit ein Rückschreibziel, **ohne dass je ein `write()`
im `strace` auftaucht**. `flags=mmap.MAP_PRIVATE` erzwingen (T-3.3).

**Eine gestrippte Umgebung ändert lautlos, WAS gemessen wird.** Der erste
Einfrierversuch von T-3.9 scheiterte am Gut-Muster, und zwar an genau zwei
Prüfungen: Bidi und Nullbreite. Der Steuerzeichenfall lief durch. Ursache war
nicht der Prüfling und nicht der Prüfstand, sondern der Aufrufer — ein Wrapper,
der `env` auf `DAIMON_ROLE`, `PATH` und `HOME` reduzierte. **Ohne `LANG` läuft
bash in der C-Locale, und dann erzeugt `$'\u202e'` kein Bidi-Zeichen mehr**: der
Angriffstext war keiner. BEL ist reines ASCII und blieb deshalb grün — die
Fehlersignatur zeigte also genau auf die Nicht-ASCII-Fälle. Wer einen
Verifizierer aus einem Skript startet, gibt die vollständige Umgebung mit.

**`~@resources` tötet auch onnxruntime — und zwar NACH dem Laden.** Beim STT
(T-3.8) steht „Modell geladen" im Journal, dann `status=31/SYS`: onnxruntime
heftet seine Rechenthreads an Kerne (`sched_setaffinity`/`sched_setattr` liegen in
`@resources`). **„Lädt" ist hier kein Beweis für „läuft".** Damit ist derselbe
Filter zweimal an zwei verschiedenen Bibliotheken aufgefallen — beim TTS wegen
PipeWire, beim STT wegen onnxruntime. Wer eine neue Unit härtet, lässt
`@resources` weg und schreibt den Grund hinein.

**Das Projekt-venv trug 365 MB Altlast.** `onnxruntime-gpu`, `onnx-asr` und `onnx`
lagen dort aus der Zeit von T−1.2, obwohl **beide** Spikes ihr eigenes `venv-a`
bauen und `T--1.2.sh` genau dort sucht. Entfernt: 517 → 152 MB. Der Fund kam vom
Prüfstand, nicht von mir — er wollte belegen, dass der Dienst-Interpreter
CPU-rein ist, und fand das Gegenteil. Wer eine Abhängigkeit für einen Spike
installiert, installiert sie ins Spike-venv.

**`SystemCallFilter=~@resources` tötet jeden PipeWire-Client.** Der TTS-Dienst starb mit
`status=31/SYS`, und zwar **beide** Prozesse (`pw-cat` und der Python-Prozess, SIGSYS in
`coredumpctl`) — ein PipeWire-Client setzt Echtzeitpriorität und ruft `mlock`, beides liegt
in `@resources`. Das Modell lädt vorher noch sauber, es stirbt erst bei der ersten
Äußerung: „lädt“ ist hier also kein Beweis für „läuft“. `MemoryDenyWriteExecute=yes` und
`PrivateDevices=yes` halten dagegen beide (onnxruntime auf der CPU braucht kein W+X, und
PipeWire wird über seinen Socket erreicht, nicht über `/dev/snd`).

**Eine Pipe hat 64 KiB, und `pw-cat` liest in Echtzeit.** Wer ein ganzes Audiosegment mit
einem `write()` hineinschreibt, blockiert bis zur halben Wiedergabe — gemessen 1014 ms im
Median für 96 KB. Und wer danach die Latenz stempelt, misst die Abspieldauer. Dazu: `stdin`
des Wiedergabeprozesses darf beim Abbruch **nicht** von einem zweiten Thread geschlossen
werden. `BufferedWriter` hat ein eigenes Lock, `close()` flusht, und der Abbruch wartet dann
auf genau den Schreiber, den er beenden soll (gemessen 4002 ms statt < 100 ms). Nur `kill()`
— der Schreiber bekommt EPIPE und räumt selbst auf.

**sherpa-onnx VITS synthetisiert satzweise, nicht chunkweise.** Der Callback feuert **einmal
je Satz**, mit dem ganzen Satz — „erstes Sample“ ist bei einer Äußerung ohne Satzzeichen
also dasselbe wie „letztes Sample“, und die Latenz wächst mit der Textlänge. Deshalb wird an
den Satzzeichen segmentiert. Und die Threadzahl entscheidet über das Kriterium: p95 316 ms
bei 2 Threads, 187 ms bei 4, 132 ms bei 8. Die erste Synthese in einem Prozess kostet
ausserdem ein Mehrfaches der folgenden (534 gegen 126 ms) — dafür gibt es den Warmlauf.

**`PIPEWIRE_RUNTIME_DIR` folgt nicht `XDG_RUNTIME_DIR`.** Ein Testaufbau, der
`XDG_RUNTIME_DIR` umbiegt (eigene Sockets im Temp-Verzeichnis), nimmt `pw-cat` damit den
PipeWire-Socket weg. Es stirbt sofort, und ohne Prüfung sieht das aus wie eine gelungene
Wiedergabe. `PIPEWIRE_RUNTIME_DIR=/run/user/<uid>` mitgeben.

**`tests/test_hub_push.py::test_hub_weg_schliesst_die_verbindung` flackert.** Einmal rot
mit `ConnectionError` unter Last (parallele Verifiziererläufe), danach dreimal in Folge
grün. Er wartet mit `settimeout(5.0)` auf ein EOF nach `hub.stop()`; unter Last reicht
das offenbar nicht immer. Bekannt, nicht behoben.

**Das Repo und das laufende System driften auseinander — zweimal am selben Tag
aufgefallen.** Beides ist kein Einzelfall, sondern fehlende Verdrahtung:

* **Das Release-Binary wird von niemandem gebaut.** Die Units zeigen auf
  `face/target/release/daimon-face`, jeder abgenommene Task landet aber in `debug`.
  Am 02.08. lief das Pet auf einem Binary vom **30.07.**, also ohne T-2.3 (Ziehen),
  T-2.4 und T-2.5 — drei fertige Tasks, die der Nutzer nie zu sehen bekam. Ein totes
  oder veraltetes Overlay sieht aus wie ein ruhiges. **Nach jedem Face-Task:
  `cargo build --release` und `systemctl --user restart daimon-face`.**
* **Die installierten Units sind Kopien, keine Symlinks.** `~/.config/systemd/user/`
  enthält Kopien vom 31.07.; Änderungen unter `config/systemd/` kommen dort **nicht**
  an. Vor dem Ändern vergleichen, danach kopieren und `daemon-reload`.
  `daimon-auth.service` ist gar nicht installiert — es laufen hub, hookbridge, face,
  focus.

**`ReadWritePaths=%t/daimon` tötet die Unit nach jedem Neustart**, weil
`/run/user/<uid>` leer hochkommt und systemd den Namespace **vor** `ExecStart` baut:
`226/NAMESPACE`. Am 02.08. standen alle vier Units bei Restart-Zähler 97, seit dem
Reboot, unbemerkt. Behoben mit `RuntimeDirectory=daimon` **und**
`RuntimeDirectoryPreserve=yes` in allen vier — ohne `Preserve` löscht das Stoppen
*einer* Unit das Verzeichnis und zieht den anderen drei die Sockets weg.

**Der Zeiger lässt sich nicht positionieren.** `ydotool mousemove -a` landet
immer bei `(0,0)`, Exit 0, keine Meldung. Und **relative** Bewegungen laufen durch
die Zeigerbeschleunigung: nominell 996 px ergaben real 3984. Jede Prüfung, die auf
berechnete Bildschirmkoordinaten klickt, misst irgendwo. **Koordinatenfrei messen**
— klicken und schauen, ob es unten ankommt.

**Ein Wayland-Client kennt seine eigene Bildschirmposition nicht.** AT-SPI meldet
`(0, 0)`. Ein Screenshot-Zuschnitt darauf fotografiert die Bildschirmecke. Für
Fenster: `spectacle -a` (aktives Fenster) plus AT-SPI-Prüfung, dass es wirklich
das eigene ist. Für das Overlay: die Position gibt man ihm ja vor.

**Screenshots nur über `spectacle`.** `grim` scheitert (KWin kann kein
`wlr-screencopy`), und `org.kde.KWin.ScreenShot2` weist beliebige Prozesse mit
*„not authorized to take a screenshot"* ab.

**`PR_SET_DUMPABLE=0` sperrt `/proc/<pid>/fd`** — auch für Prüfungen. Eine
Positivkontrolle, die dort liest, hält eine erfolgreiche Härtung für einen Befund.

**`InaccessiblePaths=` ohne `-`-Präfix tötet die Unit**, wenn der Pfad fehlt
(`status=226/NAMESPACE`). Hier starben alle Units, weil `~/.gnupg` nicht existiert.

**`kglobalaccel` schweigt bei `flags=0`.** `setShortcut` meldet Erfolg, das Signal
kommt nie. `flags` steuert `setPresent`; **`SetPresent=2`** macht es scharf.

**`jq -r '.feld // empty'` behandelt `false` wie `null`.** Ein Vergleich gegen
`"false"` ist damit immer leer.

**curl-Exitcodes taugen nicht als Sandbox-Beleg** — bei nicht erreichbarem Ziel
liefern alle Varianten `rc=7`. Messbar ist es eine Ebene tiefer: unter
`RestrictAddressFamilies=AF_UNIX` lässt sich ein AF_INET-Socket gar nicht erst
anlegen (rc=0 gegen rc=9).

**`cargo test` baut das Bin-Target nicht.** Verifizierer bauen deshalb **immer**
und prüfen, dass das Binary nicht älter ist als die Quellen. Fixture-Bäume werden
über `CARGO_TARGET_DIR` in ein Temp-Verzeichnis gebaut, sonst liegen kompilierte
Targets in der Historie — und eine Fixture-Kopie schleppt das Binary der
unmutierten Quelle mit.

**Zwei Interpreter.** Das venv ist durch T-1.2 auf **3.12** festgenagelt
(cuobjdump-Nachweis für `onnxruntime-gpu`). PyGObject gibt es nur für System-Python
**3.14**. Der Auth-Agent läuft deshalb unter `/usr/bin/python3`, alles andere im
venv. `daimon/auth/preview.py` und `ptt.py` sind reines stdlib, damit beide sie laden.

**`kwin_wayland --replace` ist auf Plasma 6 sicher** — Sitzung überlebt. Trotzdem
vorher fragen.

**Jeder Overlay-Lauf mit `DAIMON_MAX_SECS`.** Ein Overlay ohne Watchdog kann die
Maschine mit der Maus unbedienbar machen; am 27.07. real passiert.

---

## Architektur in fünf Sätzen

`face/` ist ein Rust-Crate mit **wl_shm und ohne GPU-Kontext** — null
DRI-Deskriptoren, keine `libEGL`/`libGL`/`libvulkan`/`libgbm`, gemessen am
laufenden Prozess. Der **Hub** (Python, venv) hält Zustand, Marken und Policy und
spricht ausschließlich über Unix-Sockets. Der **Auth-Agent** (System-Python, GTK4)
hält PTT und alle Bestätigungsdialoge — das Face darf sie nicht erteilen. Die
**Hook-Bridge** hat als einzige einen TCP-Listener und deshalb eine eigene Unit;
nur dadurch ist `RestrictAddressFamilies=AF_UNIX` im Hub überhaupt erfüllbar.
Vier Units laufen, `systemd-analyze security` je **3.6 OK**.

### Die Zusagen, die alles tragen

| Zusage | Gemessen | Wer bewacht sie |
|---|---|---|
| Kein GPU-Kontext | 0 DRI-Deskriptoren, 0 GPU-Bibliotheken | `T-1.4.sh`, Gate P1 |
| Idle-CPU nahe null | **0,000 %** über 60 s | `T-1.5.sh` (eingefroren) |
| Input-Region immer gesetzt | 3 Surfaces, 3 eigene Regionen | `T-1.3.sh` |
| Face erteilt keine Freigabe | `face` darf nur `bubble_dismiss` | `T-1.7.sh` (eingefroren) |
| Vorschau ist ASCII-rein | Bidi/Nullbreite sichtbar escapt | `T-1.7.sh` |

---

## Entscheidungen, die feststehen

**T−1.1 Wake-Word → Plan C.** Push-to-Talk als Grundlage, kein Wake-Word in Phase 3,
**T-3.5 und T-3.6 entfallen**. FRR 19 % auf 16 Aussprachen aus *einer* Bedingung,
FAR ungemessen.

> Nachrüstbar, Hebel beziffert: die Keyword-*Schreibweise* dominiert alles.
> `EMBER SHARD` trifft 3 von 16, `EMBA SHARD` 11, plus `EMBA SHOT` 13. Und `BOOST`
> dominiert die Schwelle — acht Schwellenwerte ändern nichts, `boost 1.5 → 3.0`
> verdoppelt.

**T−1.10 OCR → tesseract behalten.** Zuschnitt aufs **fokussierte Fenster**:
Vereinigung der Textregionen deckt 97–99 % ab, Einzelboxen kosten 261 × 60 ms =
15,7 s gegen 3,3 s Vollbild. **Kein VLM im Textpfad** — es erzeugt plausible falsche
Wörter statt sichtbaren Mülls.

**T−1.11 AT-SPI2 → Teilfläche.** Qt exportiert im Auslieferungszustand **gar keinen
Baum**; GTK4 tut es ohne Zutun.

**T−1.4 Portal:** `restore_token` hält über den Prozessneustart. **Aber:** ein Token
aus einer *anderen* Session wird ohne Dialog akzeptiert — `token.json` ist eine
**Fähigkeit**, keine Einstellung.

**T-3.8 STT:** `nemo-parakeet-tdt-0.6b-v3`. `whisper-base` ist für Deutsch
unbrauchbar (halluziniert „im basat").

**Moods entstehen durch Tönung, nicht durch Assets.** `pet.json` sagt „Der Mood ist
die Helligkeit"; eigene Mood-Zeilen hätte ein fremdes Community-Pet nicht. Der
Alpha-Kanal wird **nicht** getönt, sonst wanderte die Input-Region mit dem Mood.
Über alle acht Moods gibt es nur **zwei** `sprite`-Bezeichner — Bezeichner können
die Unterscheidbarkeit nicht belegen, `T-2.1.sh` vergleicht deshalb Pixel.

**Ausgeblendet heißt durchsichtig, nicht unmapped.** KDE-Bug **503121** liefert nach
NULL-Buffer-Unmap kein neues `configure` (Spike: 0/20 ohne Umgehung, 20/20 mit).
T-2.4 betritt den Pfad gar nicht mehr.

**Ziehen über `set_position`, nie `set_margin`.** Gemessen: 356 px Zug ergeben
`configure` **1 → 1**; derselbe Zug über `set_margin` ergibt **1 → 41**.

**Die Blase ist eine eigene Subsurface.** Textänderung: Sprite-Zähler **22 → 22**,
Blasen-Zähler **1 → 2**. Ihr Text wird **am Hub** gesäubert, bevor das Face ihn
sieht — zwei Sanitizer in Python und Rust wären auseinandergedriftet.

---

## Offen und benannt

- **`ears` darf weiterhin `intent_mark` senden**, obwohl Design §2.4 dem Wake-Word
  nur ein API-Kontingent erteilt. Heute wirkungslos, aber es *behauptet* eine
  Fähigkeit. Kommentar steht an der Tabelle in `ipc.py`. Wer den Ears-Agenten baut,
  entscheidet mit.
- **T-3.8 ist eingefroren, und die WER-Grundlinie gilt für EINE Stimme.**
  5,17 % deutsch, 0,0 % englisch — gemessen an 21 Aufnahmen, eigenes Mikrofon,
  ruhiger Raum, Nahbesprechung, vorgelesene Sätze. `herkunft.json` nennt die vier
  Dinge, die damit **nicht** belegt sind: fremde Stimmen, Nebengeräusche,
  Entfernung, Spontansprache (mit Äh und Abbruch). Wer den Ohren-Dienst baut,
  wird genau dort andere Zahlen sehen — und sollte die Grundlinie dann erweitern
  statt die Schwelle zu senken.
  Mitgefroren sind außerdem: der Betriebsbereich **8–48 kHz** für die Samplerate
  (darunter und darüber `format_falsch`), und dass `modell` den **konfigurierten**
  Verzeichnisnamen meldet und nicht einen fest verdrahteten.
- **Das STT-Modell liegt noch im Spike-Verzeichnis**, wie die TTS-Stimme.
  `spikes/stt-referenz/models/…` (665 MB, nicht im Repo, `modell_holen.sh` holt
  es). Beides gehört nach `~/.local/share/daimon/`, sobald **T-3.10** den
  Persona-Lader baut — dann kommt auch `persona.voice` aus der Persona-Datei
  statt aus `daimon.toml`.
- **T-3.9 ist eingefroren, mit zwei ausdrücklich mitgefrorenen Zusagen.** (a) Eine
  **Unterbrechung umgeht die Abkühlung** — wörtlich eine Ausnahme von Kriterium 8,
  nötig um es mit Kriterium 4 zu vereinbaren, steht samt Gegenprobe in Design §8.3
  und in der Akzeptanzliste. (b) Der **Ersatzsatz** umgeht sie ebenfalls und
  vermerkt keine. Wer eine der beiden ändern will, braucht einen `T-3.9.v2`-Task:
  der Prüfstand prüft sie, und `tests/verify/freeze.sh` lässt sie nicht anfassen.
- **T-3.9 erfüllt Kriterium 8 nur zur Hälfte.** „Meldet Start und Ende an die
  Rückkopplungssperre" ist heute nicht voll verdrahtbar: `Sperre.wiedergabe_an/aus` sind
  In-Prozess-Methoden, und **kein Prozess hält eine `Sperre`** — der Ohren-Dienst existiert
  nicht. Der TTS-Dienst meldet deshalb an den Hub (`voice.tts_active`). **Was fehlt, ist die
  Echo-Referenz**: `interlock.echo_referenz()` will das ausgegebene Signal (16 kHz mono
  int16), und über ein Zustandsfeld geht kein Audio. Ohne sie erkennt die Sperre Echo nur
  über die Zeit, nicht über das Signal. Wer den Ohren-Dienst baut, schließt das.
- **T-1.10** braucht Kalenderzeit, siehe oben.
- **T-2.6** (Wandern zwischen Monitoren) ist laut Plan optional und darf entfallen.
  Auf dieser Maschine ohnehin nicht belegbar — ein Monitor.
- **T-2.5 ist nur zur Hälfte abgenommen.** Belegt: Bindung an ein benanntes
  `wl_output`, Fallback ohne Abbruch, genau eine Instanz, DPMS-Zyklus überlebt.
  **Unbelegt: Kriterium 2** (Neuerzeugung nach echtem Output-Removal) **und die
  Auswahl nach dem Namen** aus Kriterium 3 — bei einem Monitor sind „der erste" und
  „der gewünschte" derselbe Name. Belegt durch
  `tests/blindstellen/T-2.5-wunsch-ignoriert`: eine Umsetzung, die
  `DAIMON_FACE_OUTPUT` wegwirft, besteht den Verifizierer mit Exit 0. Der Baum liegt
  **absichtlich nicht** unter `tests/mutants/`, weil `meta.sh` dort Erkennung verlangt
  und das Einfrieren sonst scheiterte — und wer ihn deswegen gelöscht hätte, hätte die
  Blindstelle mitentfernt.
- **T-2.7 ist gebaut und geprüft**, 90 Prüfungen grün, drei Mutanten über je drei
  Läufe stabil erkannt. **Der Popup-Nachweis steht** (02.08., von Hand): Menü öffnet
  sich per Rechtsklick und **bleibt offen** — KWin nimmt den Grab ohne
  `keyboard_interactivity=OnDemand` an, die Sorge des Builders war unbegründet.
  `menu_offen=true` in 26 Messungen über 7,0 s, danach von selbst zurück auf `false`
  (Auto-Dismiss). Die sechs Einträge stimmen samt Ausgrauung.
  **Und die echte Kette ist belegt, nicht nur die Attrappe:** ein Klick auf „Ohren
  aus" ergab im Hub-Journal `ziel=ears unit=daimon-ears.service rc=5 "Unit not
  loaded"` — Allowlist nachgeschlagen, konfigurierter Name geholt, `systemctl`
  gerufen. Dass nichts passiert, liegt daran, dass `daimon-ears` erst in P3 entsteht.
  **Eingefroren am 02.08.**, Gut-Muster aus dem abgenommenen Stand.
- **`remap_commit`** in `surface.rs` ist seit T-2.4 tot und bleibt als historische
  Dokumentation stehen.
- **OCR ist kein Kriterium**, nur ein Hinweis: `/usr/share/tessdata` hat nur `afr`
  und `osd`. Nachrüstbar mit `pacman -S tesseract-data-eng`.

> **Ein Verifizierer, der Quelltext per `grep` prüft, ist an der Schreibweise zu
> umgehen.** Real passiert: ein Builder schrieb `'face'` statt `"face"`, und die
> eingefrorene Prüfung schwieg. Offen gemeldet, über `T-1.7.v3` richtiggestellt.
> Wo es geht: den Wert **zur Laufzeit auslesen** statt im Dateitext suchen.

---

## Offene Ehrlichkeit

Der Codex-Review des **Gesamtplans** endete seinerzeit nach 5 Runden ohne
`APPROVED`. Die zwei dort benannten Punkte sind geschlossen — **aber diese
Nacharbeit und alles danach ist nicht gegengelesen**, einschließlich Phase 0 und
dieses Dokuments.

**T-1.8 hatte keinen unabhängigen Builder** (beide Subagenten waren ausgefallen).
Verifizierer und Mutant standen davor und unabhängig; das Gegenlesen wurde
nachgeholt, zwei Runden, neun Befunde.

**Für T-1.7 Teil 2 hat der Builder den bereits geschriebenen Verifizierer gelesen.**
Mein Auftrag untersagte nur das Ändern. Die Kriterien standen vorher fest, aber
„gegen die Prüfung gebaut" lässt sich für diesen Teil nicht ausschließen.
