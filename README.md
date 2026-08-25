# dAImon

Ein Desktop-Familiar für Linux/Wayland. Eine kleine Figur am Bildschirmrand, die zeigt, was die laufenden Claude-Code-Sessions tun, auf Ansprache antwortet, den Bildschirm mitliest und auf ausdrückliche Bestätigung den PC steuert.

**Status: Phasen −1 bis 6 gebaut.** 1850 Tests, 32 eingefrorene Verifizierer,
23 systemd-Units. Was fehlt, steht unten unter *Bekannte Einschränkungen* --
und in [HANDOVER.md](HANDOVER.md) mit Datum.

Zielsystem: CachyOS (Arch), KDE Plasma 6.7 auf Wayland, KWin 6.7, RTX 5090, PipeWire. Kein Anspruch auf Portabilität.

---

## Warum es das gibt

Der Kern ist banal und trägt sich täglich: Am Rand des Blickfelds zu sehen, dass ein Agent auf eine Freigabe wartet, ohne ins Terminal zu schauen. Alles Weitere baut darauf auf.

## Was es ist

| | |
|---|---|
| **Anzeige** | Claude-Code-Hook-Events → Mood-Zustand → Overlay |
| **Gehör** | Wake-Word und Push-to-Talk, lokale Transkription, lokale Sprachausgabe |
| **Sicht** | Screencast über das Portal, Änderungserkennung, OCR, optional ein lokales VLM |
| **Hände** | Whitelist typisierter DBus-Aktionen mit Bestätigungsdialog und Audit-Log |
| **Gedächtnis** | Bildschirm und Ton durchgehend mitgeschnitten, auf Nachfrage durchsuchbar |
| **Zeitplanung** | Termine und Fokusblöcke („erinnere mich in 20 Minuten an Tee") — Blase und Spruch, keine Aktionen |
| **Charakter** | eine TOML-Datei |

## Was es nicht ist

Keine Cloud-Verarbeitung des Mitschnitts ohne Nutzerauslösung. Kein automatisches Durchsuchen des Archivs durch das Modell. Keine freie Maus- und Tastatursteuerung. Kein Multi-User. Nicht portabel.

### Zum Dauermitschnitt

Bildschirm und Ton laufen durchgehend mit: OCR-Text und Transkripte 30 Tage, Bilder 48 Stunden, Rohaudio nie. Alles lokal, durchsuchbar **nur auf Nachfrage** — proaktives Verhalten sieht die Historie nicht, sonst wäre die Injektionsfläche die gesamte aufgezeichnete Vergangenheit.

**Der Tonmitschnitt erfasst Dritte** — Gesprächspartner in Calls, Menschen im Raum. In Deutschland ist die Aufnahme des nichtöffentlich gesprochenen Worts ohne Einwilligung nach §201 StGB strafbar, unabhängig davon, wem der Rechner gehört. Deshalb ist der Pausenschalter kein Komfortmerkmal: globaler Hotkey, automatische Pause bei Konferenz-Apps oder fremden Mikrofonstreams, und der laufende Mitschnitt ist am Sprite sichtbar. Fällt der Pausenschalter durch die Abnahme, wird der Tonmitschnitt abgeschaltet und nur der Bildschirm archiviert.

---

## Sicherheitsmodell in drei Sätzen

1. **Passiver Kontext kann keine Aktion auslösen und keinen Dialog öffnen.** Bildschirmtext, Hook-Nutzlasten und Hintergrundschleifen dürfen eine Sprechblase anzeigen — mehr nicht.
2. **Sprache fragt, sie autorisiert nicht.** Audio ist nicht authentifizierbar: Lautsprecher, Videos und die eigene Sprachausgabe können den Namen sagen. Aktionen brauchen Push-to-Talk und eine Bestätigung der kanonisierten Aktion.
3. **Ein Angreifer mit Codeausführung unter derselben uid wird nicht abgewehrt.** Das steht so im Bedrohungsmodell, weil es nicht leistbar ist und alles andere unehrlich wäre.

Die Prozessgrenzen begrenzen den Schaden aus kompromittierter **Modellausgabe** — das ist der reale Angriffsweg. Sie sind keine Grenze gegen lokalen Code.

Vollständig: [docs/DESIGN.md §1.2](docs/DESIGN.md).

---

## Dokumente

| Datei | Inhalt |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Architektur, Bedrohungsmodell, Diagramme, Risikoregister |
| [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) | 161 Tasks in 10 Phasen, agentenlesbar |
| [PLAN-REVIEW-LOG.md](PLAN-REVIEW-LOG.md) | 5 Runden adversarialer Review, vollständig |
| [HANDOVER.md](HANDOVER.md) | **Stand, laufende Prozesse, Fallen — für die nächste Sitzung** |
| [docs/PRIOR-ART.md](docs/PRIOR-ART.md) | was es schon gibt: übernehmen, lesen oder meiden |
| [docs/PHASE3-original.md](docs/PHASE3-original.md) | der ursprüngliche, engere Plan |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | **wenn etwas nicht tut** — jeder Eintrag ist einmal wirklich passiert |
| [docs/DEBT.md](docs/DEBT.md) | die bewussten Vereinfachungen, aus den `ponytail:`-Kommentaren geerntet |
| [docs/INSTALL.md](docs/INSTALL.md) | von null auf lauffähig |

### Der Review ist nicht konvergiert

Fünf Runden gegen OpenAI Codex (`gpt-5.6-sol`), Maximum erreicht, **kein `APPROVED`**. Schlussurteil: das Design sei „broadly sound", der Plan „still not safe to start". Die beiden dort benannten offenen Punkte sind inzwischen geschlossen (Anhang C4 und D des Plans) — **diese Nacharbeit ist selbst nicht gegengelesen.**

Das Protokoll steht vollständig im Repo, samt der Stellen, an denen die Kritik eigene Behauptungen widerlegt hat. Eine davon: Der Testbefehl für Push-to-Talk löste den Shortcut über `kglobalaccel.invokeShortcut` aus — und bewies damit, dass jeder Prozess derselben uid das „physische" Ereignis erzeugen kann.

---

## Verifikationsregime

Der Plan wird von Agenten in Schleifen abgearbeitet. Damit die Abnahme nicht von dem geschrieben wird, der auch implementiert:

```
T-x.y.v  (reviewer)   Verifizierer + Mutanten + Gut-Muster
   │                  meta.sh: jede Mutante muss scheitern
   │                  freeze.sh: Hash nach tests/verify/FROZEN
   ▼
T-x.y    (builder)    implementieren — kann tests/verify/ nicht anfassen
   ▼
Gate                  verify-frozen.sh zuerst
```

Durchgesetzt, nicht behauptet — drei Schichten:

| Schicht | Datei |
|---|---|
| `PreToolUse`-Hook | [.claude/hooks/role_guard.py](.claude/hooks/role_guard.py) |
| git `pre-commit` | [.githooks/pre-commit](.githooks/pre-commit) |
| Gate-Vorbedingung | [tests/verify/verify-frozen.sh](tests/verify/verify-frozen.sh) |

Rolle über `DAIMON_ROLE`. Fehlt sie, ist Schreiben überall verboten.

```bash
git config core.hooksPath .githooks
export DAIMON_ROLE=builder      # oder reviewer / investigator
tests/verify/T-0.0.sh           # 19 Assertions, muss grün sein
```

---

## Steht das nicht längst irgendwo?

Ungefähr fünfzehnmal — als „Pet beobachtet deinen Coding-Agenten". Aber jedes Projekt mit echtem Feinschliff ist Electron oder Tauri auf X11 oder macOS. Das einzige Wayland-native ([`agent-pet`](https://github.com/MasonRhodesDev/agent-pet), MIT, Rust, SCTK) ist drei Wochen alt, hat null Sterne — und deckt trotzdem 40–50 % von Phase 1. **Phase 1 startet deshalb als Fork davon, nicht aus `cargo new`.**

Was dAImon von den fünfzehn unterscheidet, gibt es nirgends: lokale Wahrnehmung mit erzwungener Netzsperre, Herkunftsmarkierung, und ein typisierter Aktionskatalog mit Bestätigungsdialog. Das ganze Computer-Use-Feld löst Sicherheit über Eindämmung (VM, Container) oder ein pauschales Ja/Nein auf undurchsichtige Codeblöcke.

Details, inklusive der Lizenzfallen: [docs/PRIOR-ART.md](docs/PRIOR-ART.md).

## Bedienung

### Installieren und starten

```bash
git config core.hooksPath .githooks
uv sync --all-extras
sudo pacman -S tesseract-data-deu tesseract-data-eng
for u in config/systemd/*.service; do systemctl --user link "$PWD/$u"; done
systemctl --user daemon-reload
systemctl --user enable --now daimon-hub.service daimon-face.service
```

Vollständig, mit den Modellgewichten und dem einen Portal-Klick:
[docs/INSTALL.md](docs/INSTALL.md).

### Die Kill-Switches

Es gibt keinen zweiten Weg. Kein Steuer-Socket, kein Signal, keine
Konfigurationsflagge — ein zweiter Weg wäre eine zweite Angriffsfläche.

| Was | Befehl | Was er belegt |
|---|---|---|
| **Wahrnehmung aus (Ohren *und* Augen)** | `Meta+Shift+M` | beides zusammen — das Kürzel aus Design §7.4 |
| Ohren aus | `python -m daimon.ears.killswitch` | Unit inaktiv **und** null Aufnahmeströme |
| Augen aus | `python -m daimon.eyes.killswitch` | Unit inaktiv, null eigene Videoströme, Kontextverzeichnis leer |
| Mitschnitt anhalten | `Meta+Shift+P` (Umschalter) | Units gestoppt, keine Bildschirmströme mehr — mit Positivkontrolle `beleg` |
| Alles aus | `systemctl --user stop 'daimon-*'` | — |
| Gedächtnis vergessen | `python -m daimon.mind.store --loeschen` | Zeilen **und** Datei |
| **Archiv vergessen** | `python -m daimon.recorder.store --loeschen` | Zeilen, Datei, WAL und SHM |
| Bildschirmzugriff widerrufen | Kontextmenü → *Bildschirmzugriff widerrufen* | Token gelöscht, Portal-Sitzung geschlossen |

**Beide Löschwege gehören zusammen.** Das Gedächtnis hält Notizen, das
**Archiv** 30 Tage Fenstertitel und Bildschirmtext — der größere Bestand.
Wer nur den ersten fährt, hat das Meiste behalten. Bis zum 17.08. stand hier
nur der erste.

### Tastenkürzel

Registriert vom Auth-Agenten über `org.kde.kglobalaccel`. Scheitert die
Registrierung, sagt er es und läuft weiter — ein Kürzel, das lautlos nicht
greift, wäre schlimmer als keines.

| Kürzel | Wirkung |
|---|---|
| `Meta+Space` | Push-to-Talk (Umschalter, mit Zeitlimit) |
| `Meta+Shift+M` | Wahrnehmung aus — Ohren **und** Augen |
| `Meta+Shift+P` | Mitschnitt anhalten / fortsetzen |

**Fortsetzen ist ein Tastendruck, nie automatisch.** Eine Pause, die sich
selbst beendet, sobald die Konferenz weg ist, wäre ein Mitschnitt, der wieder
anläuft, ohne dass jemand es gesagt hat.

### Der Privatmodus

**Menü des Pets → „Mitschnitt pausieren (15 min)".** Danach legt die
Redaktion **nichts** ab, Bild wie Ton, bis die Frist abläuft.

Er ist die einzige Sperre, die den **Tonpfad** erreicht. Die
Anwendungs-Denylist sperrt Fenster, und ein gesprochener Satz hat keines —
`urteil_ton()` prüft deshalb genau diesen einen Zustand. Wer während eines
offenen Passwortmanagers spricht, ist ohne ihn nicht geschützt: der Bildteil
wäre gesperrt, der Tonteil nicht.

**Die Frist ist Absicht, kein Komfortverzicht.** Ein Privatmodus, den man
einschaltet und vergisst, ist ein abgeschalteter Mitschnitt mit einem
beruhigenden Namen. Er überlebt einen Neustart absichtlich **nicht** — die
Pause aus `Meta+Shift+P` schon, und das ist der Unterschied zwischen beiden.

**Es gibt kein Ausschalten.** Das Overlay kann ihn anfordern und sonst
nichts; eine Meldung, die ihn beendet, gäbe einem kompromittierten Face den
Weg, den Mitschnitt wieder anzuschalten. Auch die Dauer kommt nicht aus der
Nachricht, sondern steht im Hub.

Bis zum 19.08. stand hier, er sei „gebaut und nicht einschaltbar" — gebaut,
geprüft, und im Betrieb rief ihn niemand auf. Das war dieselbe Sorte Lücke,
die diese Woche elfmal aufgefallen ist; siehe `CLAUDE.md`. Aufgefallen ist
sie durch einen Wächter, der genau darauf wartete
(`tests/test_gate_zulauf.py`).

Beide Schalter geben JSON zurück und setzen `ok` erst, wenn die **Wirkung**
gemessen ist — nicht, wenn `systemctl` mit 0 endet. Ein Dienst, der beim
Beenden seinen Strom nicht schließt, hätte sonst ein grünes `rc=0`.

### Prüfbefehle

```bash
tests/verify/verify-frozen.sh   # kein Verifizierer wurde nach dem Einfrieren geändert
tests/verify/T-0.0.sh           # die Rollentrennung greift wirklich (19 Assertions)
python -m pytest                # 1850 Tests
python -m daimon.hub.diag       # Live-Zustand aller Dienste
```

### Persona anpassen

`~/.config/daimon/persona/<name>.toml`. Name, Wake-Words, Stimme, Farben,
Charakterzüge und der System-Prompt stehen dort — Format in
[DESIGN.md §10.1](docs/DESIGN.md).

Der eine Schlüssel, den man verstehen muss:

```toml
speech_threshold = "helpful"   # silent | urgent | helpful | chatty
```

Er regelt **ungefragtes** Reden. Auf eine Frage antwortet auch `silent` — sonst
wäre das kein Assistent, sondern ein abgeschaltetes Gerät, und dafür gibt es
den Kill-Switch.

| Stufe | spricht von selbst |
|---|---|
| `silent` | nie |
| `urgent` | wenn etwas kaputt ist oder auf eine Freigabe wartet |
| `helpful` | zusätzlich, wenn Schweigen Zeit kostet |
| `chatty` | auch beiläufig |

---

## Bekannte Einschränkungen

Nicht Vorhaben, sondern Stand.

| Einschränkung | Warum |
|---|---|
| **KDE-Bug 503121** | betrifft das Overlay; Umgehung im Design beschrieben |
| **Kein Cursor-Tracking** | auf Wayland nicht implementierbar. Kommt nie |
| **Kein deutsches KWS-Modell** | das Wake-Word erkennt einen deutschen Namen schlechter als einen englischen |
| **Keine Passwortfeld-Erkennung** | ein Passwortfeld sieht für OCR aus wie Text. Deshalb die Anwendungs-Denylist statt einer Feldheuristik |
| **Egress-Beschränkung nur auf Anwendungsebene** | die Domain-Prüfung liegt im Egress-Broker, nicht im Kernel. Die Netzsperre selbst (`RestrictAddressFamilies=AF_UNIX`) ist eine Kernelgrenze, die Ziel-Domain ist es nicht |
| **VLM ohne `mmproj`** | Ollama liefert für `qwen3-vl:8b` keine mit. Der Worker bricht beim Start ab statt bei der ersten Bildanfrage |
| **`daimon/eyes/daemon.py` steht in keinem Task** | die Lücke fiel erst beim Schreiben der Unit auf. Nachgebaut, aber nicht geplant |
| **T-5.10, T-5.11, T-6.7b, T-6.8, T-6.9 offen** | die Angriffs- und Abnahmetests. Zurückgestellt, nicht erledigt |
| **17 Verifizierer fehlen** | bewusste Umkehrung: Funktionalität zuerst, Abnahme wird nachgezogen |

Die bewussten Vereinfachungen im Code stehen einzeln in
[docs/DEBT.md](docs/DEBT.md) — 21 Stück, jede mit benannter Obergrenze.

---

## Lizenz

Noch nicht festgelegt. Referenzprojekte unter Copyleft (`wl_shimeji` GPL-2.0, `clawd-on-desk` AGPL-3.0) werden gelesen, nicht kopiert. Der Audio-Stack läuft bewusst über sherpa-onnx (Apache-2.0) statt Piper (GPL-3.0), damit diese Frage gar nicht erst entsteht — siehe [docs/PRIOR-ART.md](docs/PRIOR-ART.md).
