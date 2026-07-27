# dAImon

Ein Desktop-Familiar für Linux/Wayland. Eine kleine Figur am Bildschirmrand, die zeigt, was die laufenden Claude-Code-Sessions tun, auf Ansprache antwortet, den Bildschirm mitliest und auf ausdrückliche Bestätigung den PC steuert.

**Status: Planung abgeschlossen, Implementierung nicht begonnen.**

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
| [docs/PRIOR-ART.md](docs/PRIOR-ART.md) | was es schon gibt: übernehmen, lesen oder meiden |
| [docs/PHASE3-original.md](docs/PHASE3-original.md) | der ursprüngliche, engere Plan |

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

## Nächster Schritt

Phase −1 sind Machbarkeits-Spikes. Zwei davon können die Architektur kippen und laufen deshalb vor allem anderen:

- **T−1.1** Erkennt das Wake-Word einen deutschen Namen? Es gibt kein deutsches KWS-Modell.
- **T−1.2** Ist ONNX Runtime mit `sm_120` aus einem cp312-venv erreichbar? Arch' Paket bringt keine Python-Bindings mit.

```bash
export DAIMON_ROLE=investigator
mkdir -p spikes/wakeword && cd spikes/wakeword
```

---

## Lizenz

Noch nicht festgelegt. Referenzprojekte unter Copyleft (`wl_shimeji` GPL-2.0, `clawd-on-desk` AGPL-3.0) werden gelesen, nicht kopiert. Der Audio-Stack läuft bewusst über sherpa-onnx (Apache-2.0) statt Piper (GPL-3.0), damit diese Frage gar nicht erst entsteht — siehe [docs/PRIOR-ART.md](docs/PRIOR-ART.md).
