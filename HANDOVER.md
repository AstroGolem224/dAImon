# Übergabe — Stand 2026-07-29

Alles, was nicht aus dem Repo hervorgeht. Die vorige Fassung (27.07., Phase −1 lief
noch) ist überholt und ersetzt.

---

## Wo wir stehen

**Phase −1 abgeschlossen, Gate P0 durchfahren, Phase 1 begonnen.** Das Overlay läuft.

| | |
|---|---|
| **Gate P−1** | **8 von 9 grün**. Rot nur `T--1.12` — Messung nicht gelaufen, korrekt so |
| **Gate P0** | **11 von 11 grün**, `verify-frozen` sauber, `pytest` 154 grün + 4 dokumentiert rot |
| **Phase 2** | **T-2.1 steht.** `T-2.1.sh` eingefroren, neun Verifizierer insgesamt |
| **Gate P1** | **4 von 5 grün**. Rot nur `T-1.10` — 5 Arbeitstage Normalbetrieb, Uhr läuft seit 31.07. |
| **Phase 1** | T-1.1 bis T-1.9 stehen, am laufenden Prozess belegt. **Das MVP läuft: das Pet reagiert auf echte Sitzungen.** |

Dokumente: `docs/DESIGN.md` v5.4, `docs/IMPLEMENTATION-PLAN.md` v3.3,
`docs/feasibility-decisions.md` (Entscheidungsprotokoll aus T−1.7),
`spikes/summary.json` (maschinenlesbar).

**Quelle der Planungsdokumente ist `/home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/`,
`docs/` ist die Kopie. Beide pflegen** — derzeit identisch.

---

## Fertig in Phase 0

| Task | Inhalt |
|---|---|
| T-0.1 | Verzeichnisbaum, `pyproject.toml`, venv 3.12.13, pytest |
| T-0.2 | Bestandsdateien einsortiert, Legacy-Daemon läuft nachweislich |
| T-0.3.t | Mood-Mapping festgeschrieben: 21 grün + **4 `xfail(strict=True)`** |
| T-0.4 | Protokoll-Schemas, 44 Tests |
| T-0.5 / T-0.6 | Konfiguration, strukturiertes Logging ins Journal |
| T-0.7 | IPC über `SO_PEERPIDFD`, **eingefroren**, 3 Mutanten |
| T-0.9 | Hub: Bus, State, Sitzungs-Leases |
| T-0.11 | Hook-Bridge, **eingefroren**, 5 Mutanten, 43 Prüfungen |
| T-0.12 / T-0.13 | KWin-Fokus-Watcher, Diagnose-Endpunkt |

**T-0.8 ist nachgeholt** (Commit `337acba`), zusammen mit **T-0.8.v**. Vorgezogen aus
Phase 1: T-1.7 wäre ohne Marken, Freigaben und Ticketbuch die Hülle einer
Sicherheitsgrenze gewesen. `T-0.8.sh` ist **eingefroren**, **sechs** Mutanten werden
erkannt — die fünf aus dem Plan plus `turn-id-wiederverwendbar` aus einem Review-Befund.

**T-0.14 ist nachgeholt** (Commit `36a3dff`), zusammen mit **T-1.9**. Vier Units,
`T-0.14.sh` eingefroren. **Noch aufgeschoben:** nur T-0.10 (Gegendruck).

**Eingefroren** (`tests/verify/FROZEN`): `T-0.0`, `T-0.7`, `T-0.11`. Änderungen daran
brauchen einen neuen `.v`-Task mit Mutationstest; der pre-commit-Hook lässt sie sonst
nicht durch.

---

## Phase 1 — Stand und nächste Schritte

`face/` ist ein Rust-Crate, gebaut aus dem gemessenen Spike T−1.3 statt neu erfunden.

**Belegt am laufenden Prozess**, nicht am Quelltext:

```
DRI-Deskriptoren   0        Kernzusage §8.1
GPU-Bibliotheken   0        kein libEGL, libGL, libvulkan, libgbm
Diagnose-Socket    srw-------  antwortet mit gültigem JSON
Watchdog           DAIMON_MAX_SECS, Vorgabe 90, Exit 3
```

**T-1.4 ist fertig** (Commit `fda0578`). Zwei Zustände, `ruhig` und `dringend`, je
Frame 0 der Zeile aus `pet.json`, aus einem einmal beim Start dekodierten Sheet.
Keine Animation — das ist T-1.5. Die Hauptschleife läuft jetzt über `calloop`,
weil der Steuer-Thread die in `poll()` schlafende Wayland-Schleife wecken muss.

Zustandswechsel läuft über `--control-socket` (0600, `state ruhig|dringend`),
**nicht** über den Diagnose-Socket: Diagnose ist lesend, ein Socket der beides
kann ist eine Fähigkeit und keine Einstellung. T-1.6 füttert später denselben
Kanal. Assets über `--pet-manifest`, `DAIMON_PET_MANIFEST` oder
`./assets/pet.json`; der gewählte Pfad steht auf stderr.

**T-1.5 ist fertig** (Commit `3a1310e`), zusammen mit **T-1.5.v**. `T-1.5.sh` ist
**eingefroren**, beide Mutanten (`rearmed-callback`, `late-burn`) werden erkannt.
Gemessen am echten Binary über 60 s Ruhe: **0,000 % CPU-Mittel**,
`frames_rendered` konstant, null `damage_buffer` und null `commit` im Ruhefenster.
Positivkontrolle steht daneben — 80 Zustandswechsel erzeugen 0,01 CPU-Sekunden und
`frames_rendered` 2 → 82; die Messung sieht Last, wenn welche da ist.

> **`face/src/render.rs` entsteht nicht — bewusst.** Der Plan verlangt einen
> `dirty`/`frame_pending`-Automaten. Seit T-1.4 armiert das Face überhaupt kein
> Frame-Callback und hält keinen Timer; `calloop` blockiert in `poll()`. Das ist
> strenger als die Zusage. Ein Automat ohne Callback wäre toter Code.
> **Nachzuholen, sobald in Phase 2 Animation oder Sprechblase tatsächlich ein
> Callback armieren** — der Kommentar an `CompositorHandler::frame()` in
> `main.rs` sagt das an Ort und Stelle. Ohne das fällt die Idle-CPU-Zusage.

Zweite Abweichung: gemessen wird über `/proc/<pid>/stat`, nicht per `pidstat` —
`sysstat` ist auf dieser Maschine nicht installiert, und das Tick-Delta ist ohnehin
die genauere Größe. Steht im Skriptkopf.

**T-1.6 ist fertig** (Commit `755a5be`) — **das MVP läuft.**

Der Hub sprach bisher nur `state.sock`: eine Zeile auf Anfrage, dann zu. Damit hätte
das Face pollen müssen, und ein Poll-Timer hätte die gerade gemessene Null-Idle-CPU
wieder aufgerissen. Deshalb hat der Hub jetzt einen **schiebenden Endpunkt**:

- **`events.sock`**, 0600. Beim Verbinden sofort ein Snapshot, danach je einer pro
  `rev`-Änderung. Liest nichts vom Client. Nachgesehen wird alle 50 ms — **im Hub**,
  der ohnehin Threads hält, nicht im Face.
- `face/src/hub.rs`: ein Thread blockiert in `read_line()`. Kein Timer im Face.
- Mood → Sprite: `needs_input`/`failed` → `dringend`, alles andere → `ruhig`.
- Unbekanntes `v` → `sleeping`, **Verbindung bleibt**. Hub weg → `sleeping`, kein
  Absturz, kein Spam.

Gemessen: Latenz p95 **50,5 ms** über 20 Wechsel (gefordert < 300 ms), `rev` synchron,
`needs_input` → `dringend` belegt, Hub gekillt → `sleeping` und das Face lebt weiter,
Idle-CPU **0,000 %** mit angehängtem Hub *und* in der Gegenprobe ohne.

`tests/verify/T-1.6.sh` plus ein Mutant `ignoriert-rev` (verbindet sich brav,
übernimmt neue `rev` nicht) — er fällt an acht Stellen durch. **Kein `freeze`**: der
Plan sieht für T-1.6 keinen `.v`-Task vor.

> **Der Plan misst die falsche Größe.** Er verlangt die Latenz gegen `last_render_ts`.
> Mit zwei Sprites bilden `working` und `done` beide auf `ruhig` ab, `zustand_setzen`
> steigt bei gleichem Namen aus und committet korrekterweise **nicht** —
> `last_render_ts` bleibt stehen, obwohl alles funktioniert. Gemessen wird deshalb
> das Nachziehen der Face-`rev`. Das ist eine Obergrenze und im Skriptkopf benannt.

---

## T-0.8 — vorgezogen, weil T-1.7 daran hängt

Vier Automaten mit **getrennten** Speichern: Rundenmarke, Aktionsfreigabe,
API-Kontingent, Broker-Ticket. `daimon/hub/marks.py` und `daimon/hub/tickets.py`.

`KontingentBuch.erlaubt_aktion()` und `.erlaubt_deklassifizierung()` geben konstant
`False` zurück und sind **absichtlich aufrufbar** — eine Zusage, die man testen kann,
ist mehr wert als ein Kommentar.

**Wie gearbeitet wurde, und warum das Ergebnis zählt:** der Verifizierer entstand gegen
die Akzeptanzliste, die Implementierung parallel gegen dieselbe Liste, ohne dass eine
Seite die andere sah. Beim ersten Zusammentreffen **48 von 48 grün**. Zwei unabhängige
Lesarten, die sich decken, sind der beste verfügbare Beleg, dass die Anforderung
eindeutig war.

**Zwei Befunde, die weder der Verifizierer noch die Tests der Gegenseite hatten:**

1. Eine verbrauchte Rundenmarke ließ sich wiederbeleben, indem für dieselbe `turn_id`
   erneut ausgegeben wurde. `turn_id` kommt aus dem Aufruf — ein Request-Feld steuerte
   damit, ob eine abgeschlossene Runde wieder gilt.
2. Ein Ticket mit falschem `auftrag_hash` wurde nicht verbraucht: Fehlversuche gratis,
   Hash erratbar. Kein Umgehen der Bindung, aber ein Auskunftskanal — und inkonsistent,
   weil `FreigabeBuch` die Nonce genau dafür verbrennt.

Beide mit **Rücknahme-Probe** belegt: Fix raus → Test rot → Fix rein → grün.

> **Ein dritter, ähnlich aussehender Fall wurde begründet nicht geändert.**
> `FreigabeBuch.bestaetigen` setzt den Verbrauch ebenfalls zurück. Anders als bei der
> `turn_id` braucht eine erneute Bestätigung aber eine **frische Nonce**, also eine
> frische Vorschau und einen frischen Klick — neue Autorisierung, kein Replay. Der
> Einwand kam vom Builder, und er hatte recht.

---

---

## T-1.7 — Teil 1 steht, Teil 2 ist das Fenster

**GUI-Stack ist entschieden: GTK4.** Und daraus folgt eine Eigenheit, die man kennen
muss, bevor man Teil 2 anfängt:

> **Der Auth-Agent läuft unter System-Python 3.14, nicht im venv.** PyGObject liegt nur
> für 3.14; das venv ist durch T-1.2 auf 3.12 festgenagelt (cuobjdump-Nachweis für
> `onnxruntime-gpu`, nicht verhandelbar). `gi` ist eine kompilierte Erweiterung — aus
> dem venv nicht importierbar, auch nicht mit `--system-site-packages`.
> Deshalb ist `daimon/auth/preview.py` **reines stdlib**: es muss unter beiden
> Interpretern laufen, damit es im venv getestet und im Dialog benutzt werden kann.
> Der Auth-Agent ist ohnehin ein eigener Prozess mit eigener systemd-Unit.

**Fertig in Teil 1** (Commit `466e6ef`):

- `daimon/auth/preview.py` — der Sanitizer. **Die Ausgabe ist reines ASCII**, schärfer
  als §2.4 verlangt: eine Tabelle verwechselbarer Glyphen ist unvollständig, sobald
  Unicode wächst, und eine unvollständige Sperrliste ist bei einer Sicherheitsanzeige
  die falsche Fehlerrichtung. NFC zuerst, Backslash verdoppelt, Längengrenze auf die
  **Ausgabe**, `isascii()`-Wache am Funktionsende.
- Beschriftungen kommen aus festen Tabellen, `vorschau()` nimmt **Schlüssel statt
  Texte** — wer keinen Text übergeben kann, kann keinen Modelltext hineinreichen.
- **Das Face hat keinen Produzenteneintrag mehr.** `"face": {"intent_mark"}` stand in
  `PRODUZENTEN` — damit war „alle Bestätigungen liegen im Auth-Agenten" nur behauptet.
  Neu: `"auth": {"intent_mark", "freigabe"}`, und die `turn_id` erzeugt der Hub.

**Teil 2 ist fertig** (Commit `2fa2350`), `tests/verify/T-1.7.sh` ist **eingefroren**,
alle fünf Plan-Mutanten werden erkannt. `daimon/auth/ptt.py` (Umschaltautomat, stdlib,
venv-testbar), `daimon/auth/agent.py` (GTK4, System-Python), `config/systemd/daimon-auth.service`.

### Vier Dinge, die man wissen muss, bevor man hier weitermacht

1. **`kglobalaccel` schweigt bei `flags=0`.** `setShortcut` nimmt die Bindung an und
   meldet Erfolg, aber das Signal kommt nie — weder bei `invokeShortcut` noch bei einem
   echten Tastendruck. Der `flags`-Parameter steuert `setPresent`; mit 0 wird die
   Verknüpfung nie scharf. **`SetPresent=2`** (hier `flags=6`) lässt die Kette laufen.
2. **Ein Wayland-Client kennt seine eigene Bildschirmposition nicht.** AT-SPI meldet für
   das Fenster `(0, 0)` — das Protokoll gibt sie ihm nicht. Ein Screenshot-Zuschnitt auf
   diese Koordinaten fotografiert die linke obere Bildschirmecke. Weil die sich nicht
   ändert, kommt zweimal dasselbe Bild heraus, und das sieht aus wie ein kaputter
   Sanitizer. **`spectacle -a`** nimmt das aktive Fenster, ganz ohne Koordinaten.
3. **Screenshots gehen nur über `spectacle`.** `grim` scheitert (KWin implementiert
   `wlr-screencopy` nicht), und `org.kde.KWin.ScreenShot2` weist einen beliebigen
   `python3` mit *„The process is not authorized to take a screenshot"* ab — die
   Schnittstelle prüft das aufrufende Programm.
4. **`jq -r '.feld // empty'` behandelt `false` wie `null`.** Ein Vergleich gegen
   `"false"` ist damit immer leer. Hat im Verifizierer eine echte Prüfung entwertet.

**OCR ist kein Kriterium**, sondern ein Hinweis: `/usr/share/tessdata` hat nur `afr` und
`osd`, keine lateinischen Sprachdaten, und damit scheitert die OCR-Positivkontrolle.
Nachrüstbar mit `pacman -S tesseract-data-eng`. Die Zusage wird stattdessen direkt
gemessen — derselbe Dialog zweimal aufgenommen ergibt **0** Unterschied, der
verwechselbare Pfad gegen den harmlosen **43 498 700**.

> **Offen und benannt: `ears` darf weiterhin `intent_mark` senden.** Design §2.4 erteilt
> dem Wake-Word nur ein **API-Kontingent**, keine Rundenmarke. Wirkungslos ist es heute
> — der Hub behandelt den Typ nur auf dem `auth`-Socket, und `MarkenBuch.ausgeben`
> erzwingt `quelle="auth"` — aber es *behauptet* eine Fähigkeit, die es nicht geben
> soll. Genau so eine Behauptung war der `face`-Eintrag. Nicht mitentschieden, weil der
> Ears-Agent noch nicht existiert; der Hinweis steht als Kommentar **direkt an der
> Tabelle** in `ipc.py`, nicht nur hier.

---

## T-1.8 — Ton bei `needs_input`

Fertig (Commit `5974d94`). `face/src/sound.rs`, Ton über einen abgekoppelten
Fremdprozess (`canberra-gtk-play`, Rückfall `paplay`) — **kein Audio-Crate**, aus
demselben Grund wie kein GPU-Stack. Abschaltbar über `--ton ein|aus` und
`DAIMON_FACE_TON`; ungültiger Wert ist Exit 2. Der Steuer-Socket kennt jetzt
`mood <name>`, weil `state dringend` `needs_input` und `failed` nicht trennt.

**Der Ton hängt am Mood und am Übergang, nie am Sprite** — beide bilden auf
`dringend` ab, und ein spritebasierter Auslöser piepte bei jedem Fehlschlag.

> **Dieser Task hatte keinen unabhängigen Builder.** Beide Subagenten waren beim
> Bauen weg (codex am Limit, kimi mit 429). Verifizierer und Mutant standen davor
> und unabhängig, die Trennung, die zählt, ist also erhalten. Das Gegenlesen wurde
> nachgeholt, sobald codex zurück war — **zwei Runden, neun Befunde, alle
> abgearbeitet**. Der wichtigste: der Verifizierer beobachtete nur den vom
> Prüfling **selbst geführten** Zähler. Ein Prüfling, der gar keinen Ton startet
> und bloß hochzählt, hätte jede Prüfung bestanden. Jetzt schiebt der Verifizierer
> Attrappen in den `PATH` und gleicht echte Aufrufe gegen den Zähler ab — **je
> Übergang**, nicht als Summe.

---

## T-1.7.v2 — die flakige Pixelprobe, erledigt

`T-1.7.sh` fiel etwa jeden zweiten Lauf durch. Behoben über den vorgesehenen Weg:
FROZEN-Zeile gelöst, geändert, `meta.sh`, neu eingefroren (Commit `ed27776`).

`spectacle -a` nimmt das **aktive** Fenster; wandert der Fokus zwischen zwei
Aufnahmen, werden zwei verschiedene Fenster verglichen. Jetzt prüft AT-SPI vor *und*
nach jeder Aufnahme, ob das aktive Fenster dem Agenten gehört (`StateType.ACTIVE`
plus PID), sonst wird wiederholt. Fünf Läufe: Exit 0, Rauschen 0, Unterschied jedes
Mal exakt 43 498 700.

> **Der erste Fix war falsch:** Bildmaße gegen AT-SPI-Maße prüfen. AT-SPI meldet die
> **Client-Fläche** (612×173), `spectacle` liefert Rahmen und Schatten mit (742×331) —
> das Verhältnis ist nicht einmal konstant. Ein Vergleich, der eine Übereinstimmung
> erwartete, die es nie gab.

---

## T-0.14 und T-1.9 — die Units laufen

Vier Units: `daimon-hub`, `daimon-hookbridge`, `daimon-focus`, `daimon-face`, alle
nach ~/.config/systemd/user installiert und aktiv. `systemd-analyze security`: Hub
**3.6 OK**, Face **3.6 OK**. Details in [docs/INSTALL.md](docs/INSTALL.md).

**Die Bridge hat eine eigene Unit**, und das ist der Punkt: sie hält den einzigen
TCP-Listener und braucht `AF_INET`. Im Hub-Prozess würde sie dessen
`RestrictAddressFamilies=AF_UNIX` unmöglich machen.

### Vier Dinge, die man wissen muss

1. **`InaccessiblePaths=` ohne `-`-Präfix tötet die Unit**, wenn der Pfad fehlt —
   `status=226/NAMESPACE`, alle Units auf einmal, weil `~/.gnupg` hier nicht existiert.
   Mit `-%h/.gnupg` werden fehlende Pfade übergangen. Design §7.5 nennt den Pfad, nicht
   die Fallstricke seiner Schreibweise.
2. **`PR_SET_DUMPABLE=0` sperrt `/proc/<pid>/fd`** — auch für Prüfungen. Eine
   Positivkontrolle, die dort liest, hält eine erfolgreiche Härtung für einen Befund.
3. **curl-Exitcodes taugen nicht als Sandbox-Beleg.** Bei nicht erreichbarem Ziel
   liefern beide Varianten `rc=7`; gleiche Codes sehen aus wie „die Beschränkung
   bewirkt nichts". Messbar ist es eine Ebene tiefer: unter `AF_UNIX` lässt sich ein
   AF_INET-Socket gar nicht erst anlegen (rc=0 gegen rc=9).
4. **DBus-Objekte brauchen starke Referenzen.** In `focus.py` wurden `BusName` und
   Objekt weggeräumt, der Busname freigegeben, und `Type=dbus` hielt den Dienst für
   gescheitert. Von Hand gestartet fällt das nicht auf.

`tests/verify/T-1.9.sh` prüft am laufenden Dienst: `frames_rendered > 0` (ein Face,
das nur den Socket anlegt und nie zeichnet, wäre sonst grün) und `Restart=on-failure`
per `kill -9` mit anschließend neuer MainPID.


---

## T-1.1 bis T-1.3 — die Verifizierer sind nachgezogen

Commit `5c1ff30`. **Acht Verifizierer eingefroren.** `T-1.1.sh` mit drei Mutanten
und `meta.sh`; T-1.2 und T-1.3 mit je einem Mutanten, kein `freeze` (der Plan sieht
für sie keinen `.v`-Task vor).

**Der Alpha-Test ist verdrahtet.** Die Sprite-Region sind jetzt die sichtbaren
Zeilenläufe: **95 Rechtecke, 3 760 statt 39 936 Pixel** — 90,6 % der Zelle sind
wieder klickdurchlässig.

Die Vorrichtung aus Spike T−1.8 liegt jetzt unter [tests/harness/](tests/harness/).

### Drei Fallen, alle in meinen eigenen Vorrichtungen

1. **Der Zeiger lässt sich hier gar nicht positionieren.** Dass `ydotool mousemove -a`
   nicht funktioniert, war bekannt. Neu: auch **relative** Bewegungen sind unbrauchbar,
   weil sie durch die Zeigerbeschleunigung laufen. Gemessen: nominell auf `(996,604)`
   geschickt, der Klick kam bei `(3984,1439)` an. Die erste Fassung von `T-1.3.sh` hat
   daraus einen Befund *gegen* das Face gemacht, den es nicht gab. `T-1.3.sh` misst
   deshalb **koordinatenfrei**: eine Klickspur über den Schirm, und gezählt wird der
   Unterschied zwischen „kommt unten an" und „kommt nicht an" (55 an, 5 gefangen).
2. **Fixture-Kopien bringen ein gebautes `target/` mit.** Beide Mutanten bestanden,
   weil sie das Binary der *unmutierten* Quelle starteten. Fixture-Bäume werden jetzt
   immer neu gebaut, über `CARGO_TARGET_DIR` in ein temporäres Verzeichnis — sonst
   lägen kompilierte Rust-Targets in der Historie.
3. **Zu früh eingefroren.** `T-1.1.sh` wurde vor Fix 2 eingefroren; `verify-frozen` hat
   es gefangen und das Gate abgebrochen. Nachgezogen über den vorgesehenen Weg.

> **`last_render_ts` braucht einen schärferen Test als der Plan nennt.** „Liegt nach dem
> Setzzeitpunkt" gilt für einen **Empfangs**zeitstempel genauso. Unterscheidbar wird es
> an einem Wechsel, bei dem der Hub sich bewegt und das Face korrekterweise *nicht*
> zeichnet — `working` → `done`, beide Sprite `ruhig`. Der Zeitstempel muss dann
> **stehen bleiben**.

Damit ist Phase 1 verifiziert.

---

## Gate P1 — durchgefahren, 4 von 5

| Prüfung | Ergebnis |
|---|---|
| `verify-frozen.sh` | **grün** — acht Verifizierer unverändert |
| `T-1.10.sh` | **rot** — 1 Tag von 5, Urteil offen. Siehe unten |
| `T-1.5.sh` (Idle-CPU) | **grün** — 0,000 % über 60 s |
| `T-1.1.sh` (Vollbild) | **grün** — 29 von 29 |
| GPU-Freiheit am systemd-Dienst | **grün** — kein Compute-Client, nicht in `process_info`, kein `/dev/nvidia`- oder `/dev/dri`-Deskriptor. Positivkontrolle: `nvidia-smi` sieht 13 Prozesse, das Face ist keiner davon |

### Der eine rote Punkt

**T-1.10 verlangt ≥5 Arbeitstage Normalbetrieb.** Das ist Kalenderzeit und
nicht abzukürzen — dieselbe Lage wie `T--1.12` in Gate P−1. Was gebaut wurde,
ist die Aufzeichnung, damit die Uhr überhaupt laufen kann:
`tools/phase1_recorder.py` plus `daimon-phase1.timer` (alle 5 Minuten,
~96 Stichproben je Arbeitstag). Stand Tag 1: `idle_cpu_p95` 0,091 %,
`crashes` 0.

**Scharfschalten, falls noch nicht geschehen:**

```bash
install -m 0644 config/systemd/daimon-phase1.{service,timer} ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now daimon-phase1.timer
```

**Was Matthias selbst eintragen muss** — kein Programm kann es messen:
`fehlalarme` und `ablenkungen` in `tests/evidence/phase1-usage.json` (Vorgabe
`null`, und `null` heißt „noch niemand hat hingesehen"), sowie das `verdict`
und `docs/phase1-verdict.md`. Der Verifizierer wertet `pending` ausdrücklich
als **rot** — ein Recorder, der sich selbst ein „gut" ausstellt, hätte kein
Urteil gefällt, sondern eine Prüfung umgangen.

> **Kopplung, die man kennen muss:** `needs_input_events` kommt aus dem
> Ton-Zähler `toene_gespielt` — die richtige Quelle, weil der nur beim
> *Übergang* nach `needs_input` steigt. Wer den Ton abschaltet
> (`--ton aus` / `DAIMON_FACE_TON=0`), schaltet damit auch diese Messung ab.
> Die Face-Unit setzt nichts, der Ton ist also an.

**Offen und benannt:**

- Der Alpha-Test in `face/src/input.rs` ist geschrieben und getestet, aber **weiterhin
  nicht verdrahtet**. Bis T-1.4 gab es kein Sprite; jetzt gibt es eins, und damit ist
  das T-1.3-Kriterium „Alpha-Test verwirft Klicks auf transparente Ränder" **ab jetzt
  überhaupt erst umsetzbar**. Die Sprite-Subsurface nimmt derzeit Klicks auf ihrer
  ganzen 192×208-Zelle an, Ecken eingeschlossen.
- Die Verifizierer `T-1.1.sh` bis `T-1.3.sh` **fehlen**. Die Akzeptanz verlangt
  Screenshot plus Pixelprobe über einem Vollbildfenster und Klickzähler über die
  Test-Eingabevorrichtung aus T−1.8.
- „Bewegung über `set_position()` **ohne Neuzeichnen**" ist aus dem
  Protokoll-Mitschnitt nicht belegbar — es stünde nur mit einer Korrelation gegen
  `attach`/`commit`. Steht in `T-1.4.sh` als INFO, nicht als Kriterium. Bewusst so.

---

## Was Matthias tun kann

**Nichts Blockierendes.** Zwei Kleinigkeiten bei Gelegenheit:

```bash
# T−1.4, Reboot-Teil — nach dem nächsten regulären Neustart, ein Befehl:
timeout --foreground --signal=TERM --kill-after=5s 130s python3 spikes/portal/reboot_check.py
```

**T−1.12** (NVIDIA-Sprachstack) ist im Plan und im Gate, aber ungemessen. Werkzeug liegt
unter `spikes/nvidia-voice/` samt `SPEC.md`. Nicht blockierend.

---

## Entscheidungen, die feststehen

**T−1.1 Wake-Word → Plan C.** Push-to-Talk als Grundlage, kein Wake-Word in Phase 3,
**T-3.5 und T-3.6 entfallen**. FRR 19 % auf 16 Aussprachen aus *einer* Bedingung, FAR
ungemessen — ein `pass` wäre darauf nicht zu halten. Kostet wenig, weil §1.3 für
Aktionen ohnehin PTT plus Bestätigung verlangt.

> **Nachrüstbar, Hebel beziffert:** die Keyword-*Schreibweise* dominiert alles.
> `EMBER SHARD` trifft 3 von 16, `EMBA SHARD` trifft 11, plus `EMBA SHOT` 13. Und
> `BOOST` dominiert die Schwelle — acht Schwellenwerte ändern nichts, `boost 1.5 → 3.0`
> verdoppelt. `evaluate.py` suchte vorher die falsche Achse ab.

**T−1.10 OCR → tesseract behalten, als dauerhafter Arbeitsprozess.** Zuschnitt aufs
**fokussierte Fenster**, nicht auf Textregionen: deren Vereinigung deckt 97–99 % ab, und
Einzelboxen kosten 261 × 60 ms = 15,7 s gegen 3,3 s Vollbild. **Kein VLM im Textpfad** —
es erzeugt plausible falsche Wörter statt sichtbaren Mülls, und das ist der schlechtere
Fehler.

**T−1.11 AT-SPI2 → in den Katalog, als Teilfläche.** Qt exportiert im
Auslieferungszustand **gar keinen Baum**; `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` ist nötig
und für fremde, bereits laufende Programme nicht erzwingbar.

**T−1.4 Portal:** `restore_token` hält über den Prozessneustart. **Aber:** ein Token aus
einer *anderen* Session wird ohne Dialog akzeptiert — `token.json` ist damit eine
**Fähigkeit**, keine Einstellung. Gehört ins Bedrohungsmodell.

**T-3.8 STT:** Design v5.4 legt `nemo-parakeet-tdt-0.6b-v3` fest. `whisper-base` ist für
Deutsch unbrauchbar (halluziniert „im basat") — gemessen, war aber ohnehin das falsche
Modell.

---

## Fallen, die uns erwischt haben

**Ein Overlay ohne Input-Region blockiert die Maus.** Eine Wayland-Surface **ohne**
gesetzte `input_region` nimmt Eingaben auf ihrer **ganzen** Fläche an. Bildschirmfüllend
heißt: der Rechner ist mit der Maus nicht mehr bedienbar, und im Journal steht
**nichts**. Real passiert am 27.07.
→ `face/src/input.rs`: leere Region ist Vorgabe, `darf_committen()` gated **jeden**
Commit, bei `false` wird abgebrochen. **Jeden** Overlay-Lauf mit `DAIMON_MAX_SECS=15`.

**`ydotool mousemove -a` positioniert auf dieser Maschine nicht.** Jedes absolute Ziel
landet bei `(0,0)`, Exit 0, keine Meldung. Für Phase 4: **libei ist die einzige Option**.

**`kwin_wayland --replace` ist auf Plasma 6 sicher** — entgegen der alten Annahme.
`kwin_wayland_wrapper` startet neu, die Sitzung überlebt. Belegt: PID 1962 → 166314,
Script nach 1,4 s wieder da. Trotzdem vorher fragen.

**Der schnelle Weg war der, der nichts tat.** Das Hook-Kommando mit
`curl --data-binary @- … &` misst 0,7 ms und stellt **0 von 10** Nutzlasten zu: die
Nutzlast kommt über stdin, der abgekoppelte curl rennt gegen den Aufrufer, der die Pipe
schließt. Richtig ist `cat > tmpfile`, dann `setsid` — 1,4 ms, 10 von 10.

---

## Wie hier gearbeitet wird

**Rollen sind maschinell durchgesetzt.** `.claude/hooks/role_guard.py` als `PreToolUse`,
`.githooks/pre-commit`, `tests/verify/verify-frozen.sh`.

```bash
export DAIMON_ROLE=investigator   # spikes/, docs/, tests/evidence/
export DAIMON_ROLE=builder        # Produktivcode, NICHT tests/verify/
export DAIMON_ROLE=reviewer       # Verifizierer, NICHT daimon/ face/
```

> **Falle:** der Hook wird gegen die **CWD des Tool-Calls** aufgelöst. Ein `cd` in einen
> Unterordner legt jeden weiteren Aufruf lahm, auch den von Subagenten. Der Pfad ist
> inzwischen absolut, aber: **keine `cd` in Bash-Aufrufen**, nur absolute Pfade. Und
> `2>&1` löst die Schreib-Erkennung des Guards aus.

**Verifizierer vor Implementierung, und delegiert.** Der Verifizierer entsteht
unabhängig gegen die Akzeptanzliste, nicht gegen den Code. Bei T-0.11 hat er **vier
echte Lücken** gefunden — darunter eine, die ich für seinen Fehler hielt und umschreiben
wollte. Er hatte recht; ich hatte ihn nicht zu Ende gelesen.

**Delegation:** `codex exec --dangerously-bypass-approvals-and-sandbox -` mit dem
Auftrag auf stdin; `kimi -p "$(cat auftrag.md)"` (**kein** `-y`/`--auto`, kollidiert mit
`-p`). Aufträge nach `/tmp/claude-…/scratchpad/`, und sie benennen ausdrücklich Rolle,
Auflagen und was **nicht** angefasst werden darf.

**Commits:** deutsch, ausführlich, erklären das *Warum* — und benennen eigene Fehler.
Kein `git add -A` ohne vorheriges `git status`.

**PMTool:** Projekt `dAImon`, ID `d9e36f7c-8e0f-480f-890d-7c52258ed12c`.

---

## Der wiederkehrende Fehler dieser Sitzung

Dreimal dasselbe, jedes Mal von einem Test oder Build aufgedeckt, nie durch Nachdenken:

1. **Wake-Word:** Schwellen ausschließlich an Negativbeispielen geeicht. Matthias fiel
   dreimal durch, obwohl sein Mikrofon im Diktat tadellos läuft. Es fehlte eine
   **Positivkontrolle** — sie liegt jetzt als `spikes/wakeword/control.py` fest.
2. **Hook-Kommando:** Latenz gemessen statt Zustellung. „0,9 ms" war schnell, weil
   nichts ankam.
3. **`commit_gezaehlt`:** ein `grep` mit `head -6` abgeschnitten, das Fehlen des
   Treffers für einen Befund gehalten, eine Lücke gemeldet die keine war.

**Die Lehre, die im Repo bleiben soll:** die bequeme Größe zu messen ist nicht dasselbe
wie die richtige zu messen. Und ohne Positivkontrolle ist „0 Treffer" nicht
interpretierbar — schwieriges Wort, schlechte Aufnahme und kaputter Aufbau sind dann
nicht zu unterscheiden.

**In T-1.4 dreimal wieder, in neuen Kleidern:**

4. **Der Verifizierer war grün und hat nichts gemessen.** Er baute nur, wenn das
   Binary *fehlte* — und `cargo test` baut den Unit-Test-Harness, nicht das
   Bin-Target. Ein absichtlich kaputt gemachter Zustandswechsel blieb grün, weil
   ein Binary von vorgestern lief. → **Ein grüner Verifizierer sagt nichts, solange
   nicht gezeigt ist, dass er rot werden KANN.** Jeder neue Verifizierer bekommt ab
   sofort mindestens einen Mutanten, bevor er als bestanden gilt.
5. **Das Sicherheitsgate war nur scheinbar aktiv.** Die Sprite-Subsurface hatte keine
   eigene `input_region`; die der Elternsurface beschneidet sie **nicht**. Dieselbe
   Falle wie am 27.07., eine Etage tiefer. → **Jede committete Surface braucht ihre
   eigene gesetzte Region**, nicht die des Elternteils.
6. **Eine `.gitignore`-Regel, die lautlos gefressen hätte.** Die Stand-ins der
   T-1.5-Fixtures liegen unter `<fixture>/face/target/debug/`, weil der Verifizierer
   denselben Pfad wie im echten Repo erwartet — und `target/` steht in `.gitignore`.
   Nach einem frischen Clone wäre `meta.sh T-1.5` kaputt gewesen, ohne eine einzige
   Meldung. Gefunden nur, weil `git status` vor dem `git add` gelesen wurde.
7. **`assert ... or True`** — in `tests/test_hub_push.py` selbst geschrieben, drei Tage
   nachdem dieselbe tautologische Assertion im Review von T-1.4 angemahnt worden war.
   Der Test war grün, egal was passierte. Jetzt `== b""`, und ein Mutant macht ihn rot.
8. **Ein Pfad, der zur Bauzeit aufgelöst wurde.** `env!("CARGO_MANIFEST_DIR")` machte
   die Prüfung „unverändertes Community-Pet lädt" wertlos: kopiert wurde, gelesen
   wurden weiter die Repo-Assets. Der Test wäre auch bei zerstörter Kopie grün
   geblieben.

---

## Offene Ehrlichkeit

Der Codex-Review des **Gesamtplans** endete nach 5 Runden ohne `APPROVED`. Die zwei dort
benannten Punkte sind geschlossen — **aber diese Nacharbeit und alles danach ist nicht
gegengelesen**, einschließlich der gesamten Phase 0 und dieses Dokuments.

**`T--1.12` ist rot, weil die Messung nicht gelaufen ist.** Das ist korrekt so und der
einzige rote Punkt in beiden Gates.

---

## Phase 2 — T-2.1 steht

Commit `0562701`. Acht Moods über **Tönung im Code**, nicht über neue Assets —
`pet.json` sagt es selbst: „Der Mood ist die Helligkeit." Eigene Mood-Zeilen hätte
ein fremdes Community-Pet nicht, und T-1.4 sagt zu, dass eines lädt.

**Der Alpha-Kanal wird nicht getönt.** Sonst änderte sich die Input-Region mit dem
Mood, und die Zeilenläufe aus T-1.3 müssten je Mood neu gerechnet werden.

**Die aufgeschobene Schuld aus T-1.5 ist bezahlt.** `face/src/render.rs` gibt es
jetzt: `dirty`/`frame_pending`, 320 ms Übergang, garantiertes Ende. Gegenprobe:
`T-1.5.sh` misst weiterhin **0,000 % über 60 s** — das Callback entwaffnet sich. Die
Prüfung ist zusätzlich in `T-2.1.sh` eingebaut, wer die Animation ändert fährt sie mit.

> **Bezeichner können die Zusage nicht tragen.** Über alle acht Moods gibt es genau
> **zwei** `sprite`-Bezeichner (`ruhig`, `dringend`). `T-2.1.sh` vergleicht deshalb
> Pixel: 28 Paare, Rauschen gemessen **0,000**, geringster Abstand **2,949**
> (`observing` gegen `done`).

### T-2.1 hat `T-1.2.sh` kaputt gemacht — zu Recht

Der Test nutzte `working` → `done` als Fall *ohne* Neuzeichnen, weil beide auf
`ruhig` abbilden. Seit T-2.1 hat jeder Mood eine eigene Tönung; der Wechsel zeichnet
neu. **Die Prämisse war weg, nicht der Code kaputt.** Der Fall ohne Neuzeichnen ist
jetzt eine *zweite Sitzung mit demselben Mood*: `rev` steigt, der gewinnende Mood
bleibt, es gibt nichts zu committen.

### Fixture-Bäume mit Rust-Crate

Sie brauchen `pet.json` (kopiert, 788 Byte) **und** das Sprite-Sheet — letzteres als
**Symlink** auf die echten Assets. Ein Rust-Test zieht es über `include_str!` zur
Bauzeit herein; 547 KB je Fixture in die Historie wären Verschwendung.

**Als nächstes:** T-2.2 (Sprechblase als zweite Subsurface). Sie verlangt
ausdrücklich, dass eine Textänderung den **Sprite nicht neu zeichnet** — der
Zähler `frames_rendered` muss dabei stehen bleiben.
