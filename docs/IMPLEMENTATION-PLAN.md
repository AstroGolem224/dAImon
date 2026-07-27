# dAImon — Implementierungsplan

**Version:** 3.2 (nach adversarialem Review durch Codex, Runden 1–5; Nacharbeit ungeprüft)
**Datum:** 2026-07-27
**Gehört zu:** [dAImon-Design.md](dAImon-Design.md) v3.1 — insbesondere §1.2 Bedrohungsmodell und §5.2 Markierung
**Repo:** `/home/itiger013/Dokumente/Github/dAImon`

---

## Wie dieser Plan zu lesen ist

| Feld | Bedeutung |
|---|---|
| **Ziel** | Ein Satz. Was danach existiert. |
| **Dateien** | Konkrete Pfade, `[neu]` oder `[ändern]`. |
| **Abhängigkeiten** | Task-IDs oder `keine`. |
| **Akzeptanz** | Checkliste, verifizierbar. |
| **Verifikation** | Ein Skript, dessen Exit-Code den Erfolg beweist. |
| **Agent** | `builder` \| `investigator` \| `reviewer` |
| **Umfang** | S (<1 h) \| M (1–3 h) \| L (>3 h) |
| **∥** | Parallelisierbar innerhalb der Phase. |

### Regeln für Verifikationsbefehle

Der Review der v1.0 hat 16 Verifikationen als untauglich verworfen. Daraus die verbindlichen Regeln:

1. **Niemals `grep -qv`.** Es passt, sobald *irgendeine* Zeile das Muster nicht enthält — also praktisch immer. Stattdessen: `! grep -q <Fehlermuster>` **und** separat den Exit-Code des Programms prüfen.
2. **Niemals ein selbstgeschriebenes Dokument nach einer Überschrift durchsuchen.** Das beweist, dass jemand getippt hat, nicht dass etwas funktioniert. Untersuchungs-Tasks erzeugen stattdessen eine **maschinenlesbare Ergebnisdatei** (`results.json`), und das Gate rechnet mit den Zahlen darin.
3. **Keine Abnahme per Hinsehen.** Interner Zustand kommt aus einem Diagnose-Socket; **Sichtbarkeit auf dem Bildschirm** kommt aus einer unabhängigen Aufnahme mit Pixelprüfung (siehe Regel 9).
4. **Negativtests brauchen einen Positiv-Kanarienvogel.** „Aufruf X wurde abgelehnt" beweist nichts, wenn der Dienst tot ist. Immer zusätzlich: ein erlaubter Aufruf muss durchgehen.
5. **Keine Verkettung mit `;` oder `&&` über mehrere unabhängige Prüfungen** — sonst besteht der Test, wenn nur eine Teilprüfung aus dem falschen Grund fehlschlägt. Jede Bedingung einzeln, Exit-Code am Ende zusammengeführt.
6. **Latenz- und Ratenkriterien werden gemessen, nicht behauptet.** Mit Stichprobenzahl und Perzentil.
7. **Der Verifizierer wird nicht von dem geschrieben, der implementiert.** Jeder Verifizierer ist ein eigener Task mit `.v`-Suffix, **vor** dem Implementierungs-Task, Agent-Typ `reviewer`. Sonst schreibt derselbe Agent Implementierung und Abnahme — und kann eine Abnahme schreiben, die nicht fehlschlagen kann.
8. **Jeder Verifizierer wird gegen eine absichtlich fehlerhafte Umsetzung geprüft** (Mutationstest). Ein Verifizierer, der die Mutante besteht, ist selbst defekt und wird zurückgewiesen.
9. **Selbstberichtete Wahrheitswerte zählen nicht.** Meldet eine Komponente „ich war sichtbar" oder „ich habe nativ gerechnet", ist das eine Behauptung. Sichtbarkeit wird per Screenshot und Pixelprobe geprüft, Ressourcen per Betriebssystemabfrage, Rechenpfade per Artefaktinspektion.

Verifikationsskripte liegen unter `tests/verify/T-<id>.sh` und beenden sich mit 0 nur bei Erfolg.

> **Zur Regel 9:** Diagnose-Sockets bleiben nützlich für **internen Zustand** — welchen Mood eine Komponente meint, wann sie zuletzt committet hat. Sie beweisen **nicht**, dass KWin den Puffer angezeigt, über einem Vollbildfenster gehalten oder unterscheidbare Pixel gerendert hat. Dafür: kontrollierte Region, **zufällig gewählte Markerfarbe**, Aufnahme vorher und nachher, Vergleich nur innerhalb der Region. Eine bloße Anwesenheitsprobe könnte fremde Bildschirmpixel treffen.

### Der Verifizierer-Aufgabengraph

v2.1 schrieb diese Regeln vor, ohne die Tasks anzulegen — eine Absichtserklärung, kein durchsetzbarer Graph. Verbindlich gilt jetzt:

**Jeder Implementierungs-Task `T-x.y` mit Sicherheits-, Policy-, Markierungs- oder Ressourcenbezug hat einen vorgelagerten Verifizierer-Task `T-x.y.v`:**

```
### T-x.y.v — Verifizierer für <Titel>
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn T-x.y seine Akzeptanzkriterien erfüllt.
- **Dateien:** tests/verify/T-x.y.sh [neu], tests/mutants/T-x.y/ [neu]
- **Abhängigkeiten:** die Akzeptanzliste von T-x.y (nicht dessen Implementierung)
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von T-x.y ist einzeln geprüft
  - [ ] Mindestens eine **Mutante** je Kriterium unter tests/mutants/: eine absichtlich
        fehlerhafte Minimalumsetzung, die genau dieses Kriterium verletzt
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück** (Exit != 0)
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9)
- **Verifikation:** tests/verify/meta.sh T-x.y — fährt den Verifizierer gegen jede Mutante
       und verlangt Fehlschlag, dann gegen die Referenzumsetzung und verlangt Erfolg
- **Agent:** reviewer · **Umfang:** je nach Task
```

Regeln für den Graphen:

1. **`T-x.y` hängt von `T-x.y.v` ab**, nie umgekehrt. Der Verifizierer entsteht zuerst.
2. **Der Verifizierer wird eingefroren.** Nach Abnahme des `.v`-Tasks wird sein sha256 in `tests/verify/FROZEN` eingetragen. Das Phasen-Gate prüft alle Hashes und **schlägt fehl, wenn ein Verifizierer nach dem Einfrieren verändert wurde**. Eine Änderung braucht einen neuen `.v`-Task mit erneutem Mutationstest.
3. **Verifizierer und Implementierung werden von verschiedenen Agenten bearbeitet.** Der Builder von `T-x.y` darf `tests/verify/` und `tests/mutants/` nicht schreiben.
4. **Mutationsoperatoren**, je nach Kriterium: Prüfung entfernen, Vergleich invertieren, Grenzwert um eins verschieben, Markierung beim Serialisieren fallen lassen, Nonce nicht verbrauchen, Ticket zweimal akzeptieren, Sandbox-Direktive entfernen, Fehlerpfad still schlucken.
5. **Architekturreview ist kein Verifizierer.** T-4.18 und T-6.9 bleiben Reviews mit Prüfliste und Belegpflicht; ihre maschinelle Abnahme liegt in getrennten `.v`-Tasks.

**Die 41 Verifizierer-Tasks**, jeweils `T-<id>.v`, Agent `reviewer`, Umfang aus dem Ziel-Task übernommen:

| `.v`-Task | prüft | Mutanten (mindestens) |
|---|---|---|
| T−1.1.v | Wake-Word-Messung | FRR aus falscher Grundgesamtheit gerechnet; Schwelle nachträglich angepasst |
| T−1.2.v | ONNX sm_120 | `cuobjdump`-Prüfung entfernt; JIT-Cache nicht gesperrt |
| T−1.3.v | layer-shell | Pixelprobe gegen ganzen Bildschirm statt Region; `configure`-Zählung geschönt |
| T−1.4.v | Portal-Persistenz | Dialogerkennung aus Selbstauskunft statt DBus-Signal |
| T-0.7.v | IPC-Peer-Prüfung | `SO_PEERCRED` + späte PID-Auflösung; Typprüfung entfernt |
| T-0.8.v | Marken/Tickets | Wiedereinlösung erlaubt; Ablauf ignoriert; Kontingent autorisiert Aktion |
| T-0.11.v | Hook-Bridge | Token-Prüfung entfernt; Präfix-Route; Body-Limit entfernt |
| T-0.14.v | Kern-Sandboxes | `RestrictAddressFamilies` entfernt; TCP-Socket im Hub |
| T-1.1.v | Overlay-Sichtbarkeit | `layer=top` statt `overlay`; Marker außerhalb der Region |
| T-1.5.v | Idle-CPU | Frame-Callback dauernd rearmiert |
| T-1.7.v | Auth-Agent | Face darf freigeben; Vorschau ohne Escaping; Halten statt Umschaltung |
| T-2.1.v | Mood-Unterscheidbarkeit | zwei Moods mit identischem Sprite |
| T-2.4.v | Ein-/Ausblenden | NULL-Buffer-Unmap |
| T-3.1.v | Mikrofon-Lebenszyklus | `stop()` statt `close()` |
| T-3.4.v | Rückkopplungssperre | Nachlauf auf 0; Echo-Referenz entfernt |
| T-3.7.v | GPU-Gate | Serialisierung entfernt; Fullscreen-Prüfung entfernt |
| T-3.11.v | Egress | Mind behält Token; Rohkörper geloggt; Kontingent nicht verlangt |
| T-3.13b.v | Markierung | Markierung bei Serialisierung verloren; `user_audio` in Durchgang 1; Hook-Feld als `trusted` |
| T-3.15.v | Ohren-Kill-Switch | Stream nur pausiert; Netz erlaubt |
| T-4.4.v | Policy | Reihenfolge vertauscht; `initiator` aus dem Request gelesen; `params_hash` übernommen |
| T-4.5.v | Auftragsformat | `audience` ignoriert; Frist nicht geprüft; HMAC wieder eingeführt |
| T-4.6.v | Audit | Kette ohne Journal-Anker; `tainted` im Klartext |
| T-4.7.v | DBus-Broker | generisches `invokeShortcut`; Katalogprüfung entfernt |
| T-4.8.v | Undo | Herabstufung ohne Artefaktprüfung; Mutation trotz `failed` |
| T-4.9.v | FS-Broker | Pfad nach dem Consent neu aufgelöst; `RESOLVE_NO_SYMLINKS` entfernt |
| T-4.10.v | Exec-Broker | `desktop_id` ohne Hash; Hash vor statt nach der Freigabe geprüft |
| T-4.11.v | Consent | Nonce ignoriert; Timeout als `allow`; Pending-State nicht persistiert |
| T-4.12.v | Modaler Dialog | Dialog im Face; per DND unterdrückbar |
| T-4.13.v | Input-Broker | Prozess bleibt am Leben; Allowlist entfernt; Zeichen geloggt |
| T-4.14.v | Broker-Sandboxes | je Unit eine entfernte Direktive |
| T-4.15.v | Höchstens-einmal | Ticket nach der Mutation verbraucht; automatischer Neuversuch |
| T-4.16.v | Ende-zu-Ende | ein Broker ausgelassen; Vorschau übersprungen |
| T-4.17.v | Basisangriffe | Rückfrage statt stiller Ablehnung |
| T-5.3.v | ScreenCast-Kosten | Grundlinie weggelassen |
| T-5.6.v | OCR-Kosten | Messfenster zu kurz |
| T-5.9.v | Deklassifizierung | Kontingent reicht aus; abgelaufene Marke akzeptiert |
| T-5.10.v | Exfiltration | Spoof-Audio-Phase ausgelassen; Rohkörper gespeichert |
| T-5.11.v | Injektion | Ergebnis aus Labels statt aus Nebenwirkungen |
| T-5.12.v | Augen-Kill-Switch | Portal-Session bleibt offen |
| T-5.13.v | Eyes-Sandbox | Netz erlaubt |
| T-6.1.v · T-6.2.v · T-6.3.v | Gedächtnis-Herkunft | Markierung beim DB-Roundtrip verloren; `user_audio` gespeichert; Modellzusammenfassung als Erinnerung |
| T-6.7b.v | Rundengrenzen | eine Wäsche-Grenze ausgelassen |

**46 Verifizierer**, einzeln ausgeschrieben in **Anhang D**. Mit 100 Implementierungs-Tasks ergibt das **146 Tasks**.

**Durchsetzung, nicht Absichtserklärung:**

- `tests/verify/FROZEN` liegt in einem **reviewer-eigenen Pfad**, den die Rollenrichtlinie des Builders nicht beschreiben darf. Der Builder kann weder den Verifizierer noch dessen Hash ändern — beides zusammen wäre sonst trivial.
- **Jedes Phasen-Gate beginnt mit `tests/verify/verify-frozen.sh`.** Der Befehl vergleicht alle Hashes und bricht bei jeder Abweichung ab.
- Ein eigener, reviewer-eigener Orchestrierungs-Task (**T-0.0**) richtet die Rollen-Pfadlisten ein und **mutationstestet sie**: ein Schreibversuch des Builders nach `tests/verify/` muss scheitern.
- **Der Meta-Verifizierer läuft zweistufig.** Solange die Implementierung nicht existiert, prüft `tests/verify/meta.sh` den Verifizierer gegen reviewer-eigene Mutanten **und** gegen ein reviewer-eigenes Gut-Muster unter `tests/fixtures/known-good/`. Nach der Implementierung läuft der eingefrorene Verifizierer zusätzlich gegen den echten Code. v3.0 verlangte eine „Referenzumsetzung", die es zu diesem Zeitpunkt nicht geben konnte.

### Ausführungsregel für Agentenschleifen

```
für jede Phase:
    für jeden Task in topologischer Reihenfolge (∥-Gruppen parallel):
        Agent liest Task + zugehörigen Design-Abschnitt
        Agent implementiert
        Agent führt tests/verify/T-<id>.sh aus
        wenn Exit != 0: max. 2 Nachbesserungen, dann eskalieren
    Phasen-Gate ausführen
    wenn Gate rot: Abbruchkriterium der Phase anwenden
```

**Jede Phase endet in einem lauffähigen, vorführbaren Zustand.**

---

## Phasenübersicht

| Phase | Ergebnis | Tasks | Aufwand |
|---|---|---|---|
| **P0.0** | Rollen-Pfadlisten und Verifizierer-Infrastruktur | 1 | ~1 h |
| **P−1** | Machbarkeit geklärt — zwei Spikes können die Architektur kippen | 8 | ~2 Abende |
| **P0** | Kern: Hub, Marken/Tickets, IPC-Auth, Hook-Bridge, Fokus-Watcher, Diagnose | 14 | ~3 Abende |
| **P1** | Natives Overlay minimal, Auth-Agent abgetrennt | 11 | ~3–4 Abende |
| **P2** | Overlay vollständig: Animation, Blase, Ziehen, Multi-Monitor | 7 | ~2 Abende |
| **P3** | Sprache, Egress-Broker, Markierungsverfolgung. Keine Aktionen. | 17 | ~5 Abende |
| **P4** | Aktuation: Katalog, Policy, Auftrag+Ticket, vier Broker, Consent, Audit | 19 | ~5 Abende |
| **P5** | Augen: Wahrnehmung in Quarantäne, dann Deklassifizierung | 13 | ~4 Abende |
| **P6** | Gedächtnis, Charakter, Rundengrenzen-Test, Abschlussreview | 11 | ~3 Abende |
| | **Summe** | **100 Implementierung + 46 Verifizierer = 146** | |

### Warum diese Reihenfolge

**P−1 zuerst**, weil zwei Annahmen die Architektur tragen und beide unbewiesen sind: dass das Wake-Word einen deutschen Namen erkennt, und dass sich ONNX Runtime mit sm_120-Kernen aus einem Python-3.12-venv importieren lässt. Fällt eine, ändert sich der Plan — besser vor 90 Tasks als nach 40.

**Kein Godot-Wegwerfprototyp mehr.** v1.0 sah acht Tasks und eine Woche vor, um eine Godot-Anwendung zu bauen und danach zu löschen. Das Mood-Mapping lässt sich aus den Hook-Logs validieren, ohne jeden Client (T−1.5), und der native Client braucht ohnehin einen frühen Machbarkeits-Spike (T−1.3). P1 baut deshalb direkt ein minimales, aber echtes Overlay.

**Fokus-Watcher in P0**, nicht in P5. Das VRAM-Gate der GPU-Worker braucht `fullScreen` — in v1.0 hing T-3.7 an einem Watcher, der erst in T-5.1 gebaut wurde. Der Watcher ist read-only und billig; er gehört nach vorn.

**P4 vor P5.** Die Injektionsminderung muss stehen, bevor fremder Bildschirmtext das Modell erreicht. Die Wahrnehmung selbst darf früher gebaut werden — aber in **Quarantäne**, ohne Verbindung zu Mind. Deshalb liegt die Deklassifizierung am Ende von P5 und der adversariale Test dahinter, nicht davor.

**Der Ende-zu-Ende-Aktionspfad ist ein eigener Task** (T-4.16). In v1.0 gab es keinen Task, der Mind → Hub → Policy → Consent → Broker tatsächlich verdrahtet — jeder Baustein existierte, der Zusammenbau nicht.

---

## Konventionen

```
dAImon/
├── daimon/
│   ├── hub/          # Bus, State, Policy, Marken, Tickets, Auftrag, Audit, Diagnose
│   ├── hookbridge/   # einziger TCP-Port
│   ├── ears/  eyes/  mind/  gpu/
│   ├── brokers/      # dbus/ fs/ exec/ input/
│   └── common/       # Protokoll, Konfiguration, Logging, IPC
├── face/             # Rust, layer-shell
├── config/           # actions/ persona/ systemd/ policy.yaml
├── kwin-script/
├── spikes/           # P−1, Ergebnisse als results.json
├── tests/
│   ├── verify/       # T-<id>.sh, Exit 0 == Erfolg
│   └── evidence/     # maschinenlesbare Messergebnisse
└── docs/
```

- Python 3.12 in einem `uv`-venv (**vorbehaltlich T−1.2**). Tests mit `pytest`.
- Jede Vertrauensgrenze und jede Policy-Verzweigung braucht einen Test. Sprites nicht.
- Code englisch, Kommentare und Commits deutsch.
- Bewusste Vereinfachungen mit `ponytail:`-Kommentar, inklusive Obergrenze und Ausbaupfad.

---

# Phase 0.0 — Verifizierer-Infrastruktur

### T-0.0 — Rollen-Durchsetzung und Meta-Verifizierer
- **Ziel:** Die Verifizierer-Regeln sind **maschinell durchgesetzt**, bevor der erste Verifizierer entsteht.
- **Dateien:** `.claude/roles.toml`, `.claude/hooks/role_guard.py`, `.claude/settings.json` [ändern], `.githooks/pre-commit`, `tests/verify/verify-frozen.sh`, `tests/verify/freeze.sh`, `tests/verify/meta.sh`, `tests/verify/FROZEN` — **alle bereits im Repo vorhanden**
- **Abhängigkeiten:** **T-0.0.v**
- **Status:** ✅ umgesetzt und selbstgetestet (`tests/verify/T-0.0.sh` läuft grün)

**Der Mechanismus, den Review-Runde 5 vermisst hat — drei Schichten:**

| Schicht | Wirkt wann | Datei |
|---|---|---|
| **`PreToolUse`-Hook** | bevor der Agent schreibt | `.claude/hooks/role_guard.py` |
| **git `pre-commit`** | bevor etwas in die Historie kommt | `.githooks/pre-commit` |
| **`verify-frozen.sh`** | erste Zeile jedes Phasen-Gates | `tests/verify/verify-frozen.sh` |

Die Rolle kommt aus `DAIMON_ROLE`. **Fehlt sie, ist Schreiben überall verboten** — fail closed, nicht fail open. Der Hook folgt derselben Auswertungsordnung wie die Policy-Engine des Projekts (deny → allow, erster Treffer). Pfade werden über `resolve()` aufgelöst, sonst umginge ein Symlink die Liste. `Bash`-Kommandos werden auf schreibende Verben und Umleitungen geprüft.

- **Akzeptanz:**
  - [x] `builder` kann `tests/verify/`, `tests/mutants/`, `tests/fixtures/known-good/` und `.claude/` **nicht** schreiben — weder per `Write`/`Edit` noch per `Bash`-Umleitung oder `sed -i`
  - [x] `reviewer` kann Verifizierer schreiben, aber keinen Produktivcode unter `daimon/`, `face/`, `kwin-script/`, `config/systemd/`
  - [x] `investigator` schreibt nur unter `spikes/`, `docs/`, `tests/evidence/`
  - [x] Fehlende oder unbekannte Rolle → alles Schreibende abgelehnt
  - [x] Symlink-Umgehung (`docs/../tests/verify/…`) wird erkannt
  - [x] `verify-frozen.sh` bricht bei jeder Hash-Abweichung ab
  - [x] `freeze.sh` friert erst nach bestandenem Mutationstest ein und **verweigert das erneute Einfrieren** eines bereits eingefrorenen Verifizierers
  - [x] `FROZEN` liegt unter `tests/verify/` und ist damit für `builder` gesperrt — Skript und Hash lassen sich nicht gemeinsam ändern
- **Verifikation:** `tests/verify/T-0.0.sh` — 19 Prüfungen in sechs Gruppen: Ablehnungen je Rolle, **Positiv-Kanarienvögel** (Builder *darf* Produktivcode, Reviewer *darf* Verifizierer), fail-closed, Symlink, und ein Manipulationstest gegen `verify-frozen.sh` mit Vorher/Nachher.

> Der Selbsttest hat beim ersten Lauf einen echten Fehler in `role_guard.py` gefunden: Die `deny`/`allow`-Auswertung war für Default-Deny-Rollen falsch herum, sodass `investigator` gar nicht schreiben konnte. Genau dafür ist er da.

**Einrichtung:**
```bash
git config core.hooksPath .githooks
export DAIMON_ROLE=builder        # bzw. reviewer / investigator
tests/verify/T-0.0.sh             # muss grün sein, bevor P−1 beginnt
```

---

---

# Phase −1 — Machbarkeit

**Ergebnis:** Die vier Annahmen, die kippen könnten, sind gemessen. Jeder Spike schreibt `spikes/<name>/results.json`.

**Abbruchkriterium:** T−1.1 und T−1.2 sind harte Weichen. Fällt eine, wird der Plan geändert, bevor P0 beginnt.

---

### T−1.1 — Wake-Word auf Deutsch messen ⚠️ WEICHE ∥
- **Ziel:** Belastbare FRR/FAR-Zahlen für mindestens zwei Kandidatennamen.
- **Dateien:** `spikes/wakeword/` [neu], `spikes/wakeword/results.json` [erzeugt]
- **Abhängigkeiten:** **T−1.1.v**
- **Akzeptanz:**
  - [ ] ≥2 Kandidatennamen, je 3–4 Silben mit ungewöhnlicher Phonemfolge
  - [ ] ≥50 eigene Aufnahmen je Name unter wechselnden Bedingungen (nah, fern, mit Hintergrundmusik)
  - [ ] ≥3 h Hintergrundmaterial ohne den Namen (Podcast, Video, Gespräch)
  - [ ] Schwelle je Name über die Testmenge optimiert
  - [ ] `results.json` enthält je Name: `{name, threshold, trials, false_rejects, frr, background_hours, false_accepts, far_per_hour, verdict}`
- **Verifikation:** `tests/verify/T--1.1.sh` — prüft, dass `results.json` ≥2 Namen enthält, jeder ≥50 `trials` und ≥3 `background_hours`, und **rechnet FRR/FAR selbst aus den Rohzählungen nach**; Exit 0 nur, wenn mindestens ein Name `frr < 0.10` **und** `far_per_hour < 1.0` erreicht
- **Agent:** investigator · **Umfang:** L

### T−1.2 — ONNX Runtime mit sm_120 aus cp312 ⚠️ WEICHE ∥
- **Ziel:** Klarheit, ob der geplante Python-Stack die nativen Blackwell-Kerne überhaupt erreicht.
- **Dateien:** `spikes/ort/` [neu], `spikes/ort/results.json` [erzeugt]
- **Abhängigkeiten:** **T−1.2.v**
- **Akzeptanz:**
  - [ ] Geprüft: Arch' `onnxruntime-opt-cuda` enthält **keine** Python-Bindings (Erwartung laut Recherche)
  - [ ] Variante A getestet: `pip install onnxruntime-gpu` im cp312-venv — läuft `CUDAExecutionProvider`, und **mit nativen sm_120-Cubins oder per PTX-JIT?**
  - [ ] Variante B getestet: Worker außerhalb des venv gegen die C-API des Systempakets
  - [ ] Variante C getestet: AUR `whisper.cpp-cuda` als Ersatzweg
  - [ ] Je Variante: erste Inferenzlatenz (JIT-Indikator), Dauerlatenz, VRAM
  - [ ] **`native_sm120` wird durch Artefaktinspektion belegt**, nicht durch Latenzvergleich: `cuobjdump --list-elf` auf der geladenen Bibliothek muss `sm_120` zeigen; zusätzlich Kontrollversuch mit geleertem und gesperrtem JIT-Cache (`CUDA_CACHE_DISABLE=1`)
  - [ ] `results.json` mit `{variant, importable, provider_active, native_sm120, cuobjdump_evidence, first_infer_cold_ms, steady_ms, vram_mb, verdict}`
- **Verifikation:** `tests/verify/T--1.2.sh` — Exit 0 nur, wenn mindestens eine Variante `importable && provider_active && native_sm120` hat **und** der Verifizierer `cuobjdump` **selbst** ausführt und `sm_120` in der Ausgabe findet. Ein Latenzverhältnis allein genügt nicht — ein warmer PTX-JIT-Cache erfüllt es ebenfalls.
- **Agent:** investigator · **Umfang:** L

### T−1.3 — layer-shell-Smoke-Test ∥
- **Ziel:** Beweis, dass eine Overlay-Surface auf diesem KWin trägt — inklusive des Bugs, der später weh tut.
- **Dateien:** `spikes/layershell/` [neu], `spikes/layershell/results.json` [erzeugt]
- **Abhängigkeiten:** **T−1.3.v**
- **Akzeptanz:**
  - [ ] Minimaler Rust-Client: `Layer::Overlay`, bildschirmfüllend, 1×1 transparenter Buffer
  - [ ] Bleibt über einem Vollbildfenster sichtbar
  - [ ] **20 Ein-/Ausblende-Zyklen** — protokolliert, ob jeder Zyklus ein `configure` erhält (KDE-Bug 503121)
  - [ ] Beide Umgehungen erprobt: Properties neu setzen vs. Surface neu erzeugen
  - [ ] Idle-CPU über 60 s gemessen
  - [ ] **Sichtbarkeit über Vollbild wird per Screenshot und Pixelprobe belegt**, nicht vom Client berichtet: bekannte Markerfarbe an bekannter Position, `spectacle`-Aufnahme, Pixel geprüft
  - [ ] `results.json` mit `{cycles, configures_received, workaround_used, idle_cpu_pct, fullscreen_pixel_match}`
- **Verifikation:** `tests/verify/T--1.3.sh` — Exit 0 nur, wenn `configures_received == cycles`, `idle_cpu_pct < 1.0` (vom Verifizierer selbst per `pidstat` gemessen) und der Verifizierer **selbst** einen Screenshot über einem Vollbildfenster aufnimmt und die Markerfarbe findet
- **Agent:** builder · **Umfang:** M

### T−1.4 — Portal-Persistenz über einen Neustart ∥
- **Ziel:** Klarheit, ob `restore_token` wirklich hält.
- **Dateien:** `spikes/portal/` [neu], `spikes/portal/results.json` [erzeugt]
- **Abhängigkeiten:** **T−1.4.v**
- **Akzeptanz:**
  - [ ] ScreenCast-Session mit `persist_mode=2` aufgebaut, Dialog einmal bestätigt
  - [ ] Token gespeichert, Prozess beendet, neu gestartet → **kein** Dialog
  - [ ] Nach einem echten Reboot erneut geprüft
  - [ ] Verhalten bei absichtlich verfälschtem Token dokumentiert
  - [ ] `results.json` mit `{restart_prompted, reboot_prompted, invalid_token_behaviour}`
- **Verifikation:** `tests/verify/T--1.4.sh` — der Verifizierer startet den Client **selbst** zweimal und leitet `restart_prompted` aus den Portal-DBus-Signalen ab (ein `Request`-Objekt mit Nutzerinteraktion), nicht aus einem gemeldeten Wahrheitswert. Exit 0 nur, wenn beim zweiten Start kein Dialog geöffnet wurde. Das Reboot-Verhalten wird aus dem Journal des vorigen Bootvorgangs abgeleitet und berichtet, aber nicht erzwungen (bekannte Upstream-Schwäche)
- **Agent:** investigator · **Umfang:** M

### T−1.5 — Mood-Mapping aus echten Logs ∥
- **Ziel:** Das Mapping gegen die Realität prüfen, ohne einen Client zu bauen.
- **Dateien:** `spikes/mood/` [neu], `spikes/mood/results.json` [erzeugt]
- **Abhängigkeiten:** keine
- **Akzeptanz:**
  - [ ] Hooks eingetragen, bestehender `pet_daemon.py` läuft, Journal wird mitgeschnitten
  - [ ] ≥5 echte Sessions: erfolgreiche, mit Freigabe-Prompt, fehlgeschlagene, parallele, abgebrochene
  - [ ] Je Session Event-Abfolge mit Zeitstempeln maschinenlesbar abgelegt
  - [ ] Abweichung erwartet/tatsächlich je Event gezählt
  - [ ] `results.json` mit `{sessions, events_total, mismatches: [{event, expected, actual}], recommendation}`
- **Verifikation:** `tests/verify/T--1.5.sh` — Exit 0 nur, wenn `sessions >= 5` und die Datei je Session eine nichtleere Ereignisliste enthält; die `mismatches`-Liste wird ausgegeben und fließt in T-0.7 ein
- **Agent:** investigator · **Umfang:** M

### T−1.6 — Hook-Overhead messen ∥
- **Ziel:** Beweis, dass ein toter oder langsamer Daemon Claude Code nicht ausbremst.
- **Dateien:** `spikes/hookoverhead/results.json` [erzeugt]
- **Abhängigkeiten:** T−1.5
- **Akzeptanz:**
  - [ ] ≥30 gepaarte Läufe desselben trivialen Prompts, Bridge an vs. aus
  - [ ] Zusätzlich: Bridge läuft, antwortet aber absichtlich verzögert
  - [ ] p50 und p95 je Bedingung
  - [ ] `results.json` mit `{n, p50_on_ms, p50_off_ms, p95_on_ms, p95_off_ms, p50_slow_ms}`
- **Verifikation:** `tests/verify/T--1.6.sh` — Exit 0 nur, wenn `p95_on_ms <= p95_off_ms * 1.05` und `p50_slow_ms <= p50_off_ms * 1.10`
- **Agent:** investigator · **Umfang:** M

### T−1.7 — Entscheidungsprotokoll
- **Ziel:** Die Weichen sind gestellt und dokumentiert.
- **Dateien:** `docs/feasibility-decisions.md` [neu], `spikes/summary.json` [erzeugt]
- **Abhängigkeiten:** T−1.1 … T−1.6
- **Akzeptanz:**
  - [ ] Je Spike: Ergebnis, Entscheidung, Auswirkung auf den Plan
  - [ ] Bei durchgefallenem T−1.1: gewählter Plan (B oder C) benannt und die betroffenen Tasks in P3 angepasst
  - [ ] Bei durchgefallenem T−1.2: gewählte STT-Variante benannt und T-3.8 angepasst
  - [ ] `summary.json` aggregiert alle `results.json` mit Verdikt je Spike
- **Verifikation:** `tests/verify/T--1.7.sh` — prüft, dass `summary.json` für jeden der sechs Spikes einen Eintrag mit `verdict` und `decision` enthält und dass kein Verdikt `pending` ist
- **Agent:** investigator · **Umfang:** S

### T−1.8 — Test-Eingabevorrichtung ∥
- **Ziel:** Automatisierte Klicks und Tasten für Tests, ohne auf den Produktions-Input-Broker zu warten.
- **Dateien:** `tests/fixtures/input/` [neu]
- **Abhängigkeiten:** **T−1.8.v**
- **Akzeptanz:**
  - [ ] Minimaler `uinput`-Wrapper, **ausschließlich** in Tests verwendet, nie von einer Unit geladen
  - [ ] Klick an Bildschirmkoordinate, Tastendruck, Modifikatoren
  - [ ] Deutlich als Testcode markiert; in keiner systemd-Unit referenziert
  - [ ] Wird von T-1.3, T-2.3 und T-2.7 genutzt, die sonst an P4 hingen
- **Verifikation:** `tests/verify/T--1.8.sh` — klickt auf ein Testfenster mit Zähler und prüft den Zähler numerisch; prüft zusätzlich per `grep`, dass keine Datei unter `config/systemd/` die Vorrichtung referenziert
- **Agent:** builder · **Umfang:** M

### Gate P−1
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
# Die blockierenden Spikes sind hier fest verdrahtet — NICHT aus summary.json gelesen,
# sonst liesse sich ein gescheiterter Blocker per Handeintrag als "nonblocking" umetikettieren.
set -e
for t in T--1.1 T--1.2 T--1.3 T--1.4; do tests/verify/$t.sh; done   # Exit-Status IST das Verdikt
tests/verify/T--1.5.sh
tests/verify/T--1.6.sh
tests/verify/T--1.8.sh
tests/verify/T--1.7.sh        # prueft nur, dass zu jedem Spike eine Entscheidung dokumentiert ist
```

---

# Phase 0 — Kern

**Ergebnis:** Hub mit Marken- und Ticketverwaltung, authentifizierter IPC, isolierter Hook-Bridge, Fokus-Watcher und Diagnose. Noch kein sichtbares Pet, aber der State ist über den Socket korrekt abfragbar.

**Abbruchkriterium:** Keins.

---

### T-0.1 — Repo-Struktur und Werkzeugkette ∥
- **Ziel:** Verzeichnisbaum, `pyproject.toml`, venv, `pytest` läuft.
- **Dateien:** `pyproject.toml` [neu], `.gitignore` [neu], Verzeichnisse nach Konvention
- **Abhängigkeiten:** T−1.7
- **Akzeptanz:**
  - [ ] venv mit der in T−1.2 bestätigten Python-Version
  - [ ] `uv pip install -e '.[dev]'` läuft durch
  - [ ] `pytest` startet und findet die Testverzeichnisse
  - [ ] Git-Repo initialisiert, erster Commit existiert
- **Verifikation:** `tests/verify/T-0.1.sh` — prüft einzeln: venv-Python-Version stimmt, Paket ist importierbar, `pytest --collect-only` Exit 0 oder 5, `git rev-parse HEAD` liefert einen Commit, alle Konventions-Verzeichnisse existieren
- **Agent:** builder · **Umfang:** S

### T-0.2 — Bestandsdateien einsortieren
- **Ziel:** Die vier Bestandsdateien liegen am Zielort, nachweislich unverändert.
- **Dateien:** `pet_daemon.py` → `daimon/hub/legacy_daemon.py`, `claude-hooks.json` → `config/`, `pet_client.gd` → `docs/attic/`, `PHASE3.md` → `docs/PHASE3-original.md`
- **Abhängigkeiten:** T-0.1
- **Akzeptanz:**
  - [ ] Prüfsummen **vor** dem Verschieben festgehalten
  - [ ] Alle vier Dateien byte-identisch am neuen Ort
  - [ ] `pet_client.gd` landet im Archiv — Godot wird nicht gebaut, die Datei bleibt als Referenz
  - [ ] Der Legacy-Daemon startet weiterhin und beantwortet den Smoke-Test aus `docs/PHASE3-original.md` §2.3 vollständig
- **Verifikation:** `tests/verify/T-0.2.sh` — vergleicht die vorab gespeicherten sha256-Summen und fährt danach den vollständigen Original-Smoke-Test, inklusive der Erwartung `mood == "needs_input"` **und** eines nichtleeren `bubble`
- **Agent:** builder · **Umfang:** S

### T-0.3.t — Tests für das Mood-Mapping
- **Ziel:** Das Mapping ist festgeschrieben, bevor refaktoriert wird — inklusive der in T−1.5 gefundenen Abweichungen.
- **Dateien:** `tests/test_mood_mapping.py` [neu]
- **Abhängigkeiten:** T-0.2, T−1.5
- **Akzeptanz:**
  - [ ] Ein Test je Zeile der Mood-Tabelle
  - [ ] Ein Test je in `spikes/mood/results.json` gemeldeter Abweichung
  - [ ] Prioritätsarbitrierung bei mehreren Sessions
  - [ ] Session-TTL-Aufräumung
  - [ ] Unbekanntes Event → keine Zustandsänderung
  - [ ] Alle Tests laufen grün gegen den Bestandscode, außer den Abweichungs-Tests, die dokumentiert rot sind
- **Verifikation:** `tests/verify/T-0.3.sh` — `pytest tests/test_mood_mapping.py`; erwartet genau die als `xfail` markierten Abweichungen und keine weiteren Fehlschläge
- **Agent:** builder · **Umfang:** M

### T-0.4 — Protokoll-Schemas ∥
- **Ziel:** Die JSON-Verträge aus Design §9 als Dataclasses mit Validierung.
- **Dateien:** `daimon/common/protocol.py` [neu], `tests/test_protocol.py` [neu]
- **Abhängigkeiten:** T-0.1
- **Akzeptanz:**
  - [ ] `Event`, `State`, `ActionRequest`, `ExecutionOrder`, `RoundMark`, `ActionApproval`, `ApiQuota`, `Ticket`, `AuditRecord`
  - [ ] **Markierte Werte sind ein eigener Typ**, der Serialisierung übersteht (Design §5.2)
  - [ ] `ActionRequest` hat **kein** `initiator`- und **kein** `params_hash`-Feld — beide bestimmt der Hub
  - [ ] `Event` hat **kein** `source`-Feld — die Quelle kommt aus dem Socket
  - [ ] Unbekanntes `v` → `UnsupportedVersion`
  - [ ] Unbekannte optionale Felder werden ignoriert, nicht abgelehnt
  - [ ] Roundtrip-Test je Typ
- **Verifikation:** `tests/verify/T-0.4.sh` — `pytest tests/test_protocol.py`; enthält einen Test, der belegt, dass ein hineingeschmuggeltes `initiator`-Feld beim Deserialisieren **verworfen** wird
- **Agent:** builder · **Umfang:** M

### T-0.5 — Konfiguration ∥
- **Ziel:** Laden aus XDG-Pfaden mit Vorgaben.
- **Dateien:** `daimon/common/config.py` [neu], `config/daimon.toml` [neu], `tests/test_config.py` [neu]
- **Abhängigkeiten:** T-0.1
- **Akzeptanz:**
  - [ ] Lädt aus `$XDG_CONFIG_HOME/daimon/`, fällt auf mitgelieferte Vorgaben zurück
  - [ ] `$XDG_STATE_HOME/daimon/` wird mit Modus 0700 angelegt
  - [ ] Fehlende Datei ist kein Fehler; fehlerhafte nennt die Zeile
  - [ ] Kein globaler Zustand
- **Verifikation:** `tests/verify/T-0.5.sh` — `pytest tests/test_config.py`; prüft zusätzlich per `stat` den Modus des angelegten Zustandsverzeichnisses
- **Agent:** builder · **Umfang:** S

### T-0.6 — Strukturiertes Logging ∥
- **Ziel:** Journal-Logging mit `DAIMON_*`-Feldern, stderr als Rückfall.
- **Dateien:** `daimon/common/logging.py` [neu]
- **Abhängigkeiten:** T-0.5
- **Akzeptanz:**
  - [ ] `sd_journal_send()` wenn verfügbar, sonst stderr
  - [ ] Eigene Felder nur `[A-Z0-9_]`, nie mit `_` beginnend
  - [ ] `SYSLOG_IDENTIFIER` je Prozess
  - [ ] Ohne python-systemd sauberer Rückfall statt Absturz
- **Verifikation:** `tests/verify/T-0.6.sh` — schreibt einen Datensatz mit eindeutiger Marke, liest ihn per `journalctl -o json` zurück und prüft, dass `DAIMON_ACTION` **und** das vom Journal gesetzte `_PID` vorhanden sind
- **Agent:** builder · **Umfang:** M

### T-0.7 — Authentifizierte IPC-Schicht
- **Ziel:** Sockets, die wissen, wer spricht.
- **Dateien:** `daimon/common/ipc.py` [neu], `tests/test_ipc.py` [neu]
- **Abhängigkeiten:** T-0.4, **T-0.7.v**
- **Akzeptanz:**
  - [ ] Ein Socket je Produzent unter `$XDG_RUNTIME_DIR/daimon/`, Modus 0600
  - [ ] **`SO_PEERPIDFD`** liefert einen pidfd; uid und Unit werden **am gepinnten pidfd** aufgelöst
  - [ ] `SO_PEERCRED` plus nachträgliche PID-Auflösung ist ausdrücklich **nicht** zulässig — dazwischen liegt ein PID-Wiederverwendungsrennen
  - [ ] Abweichende Unit → Verbindung ab und Audit-Eintrag
  - [ ] Dokumentiert nach Design §1.2: das ist ein **Wegweiser**, keine Authentifizierung
  - [ ] **Nachrichtentypen sind produzentenspezifisch** — `eyes` darf kein `hook`-Event senden
  - [ ] Test: Ein Prozess, der auf dem falschen Socket den falschen Typ sendet, wird abgewiesen
- **Verifikation:** `tests/verify/T-0.7.sh` — `pytest tests/test_ipc.py`; Positiv-Kanarienvogel (richtiger Socket, richtiger Typ → akzeptiert) und vier Negativfälle (falscher Typ, falscher Socket, falsche uid, fremde Unit); dazu eine **Rennen-Mutante**: eine Umsetzung mit `SO_PEERCRED` + späterer PID-Auflösung muss zurückgewiesen werden
- **Agent:** builder · **Umfang:** L

### T-0.8 — Marken, Freigaben, Kontingente, Tickets
- **Ziel:** Vier getrennte Zustandsautomaten für Absicht, Aktionsfreigabe, API-Kontingent und Broker-Ticket.
- **Dateien:** `daimon/hub/marks.py` [neu], `daimon/hub/tickets.py` [neu]
- **Abhängigkeiten:** T-0.7, T-0.8.v
- **Akzeptanz:**
  - [ ] **Rundenmarke** — entsteht nur aus `intent_mark` des Auth-Agenten; an `turn_id` und Frist gebunden; einmal einlösbar
  - [ ] **Aktionsfreigabe** — entsteht nur aus einer Auth-Bestätigung mit passender Nonce; an `action_hash` gebunden; gilt für genau diese kanonisierte Aktion
  - [ ] **API-Kontingent** — entsteht aus Wake-Word oder Rundenmarke; erlaubt einen Egress-Aufruf; **autorisiert keine Aktion und deklassifiziert nichts**
  - [ ] **Broker-Ticket** — persistentes, atomares Ticketbuch; Einlösung unmittelbar vor der Ausführung; höchstens einmal
  - [ ] Kein Zustand wird je aus einem Feld eines eingehenden Requests übernommen
  - [ ] Mind bekommt eine `request_id`, die nichts autorisiert
  - [ ] Fehlende oder abgelaufene Marke ⇒ `initiator = background`
  - [ ] Ausgabe und Einlösung jedes Typs landen im Audit
  - [ ] Die Begriffe folgen Design §1.2 — im Code und in Meldungen kein „Capability", „unfälschbar", „physisch"
- **Verifikation:** `tests/verify/T-0.8.sh` (eingefroren) — je Automat: Wiedereinlösung, Ablauf, Fremdbindung (`turn_id`, `action_hash`, `audience`); ein Test belegt, dass ein Kontingent **keine** Aktion und **keine** Deklassifizierung erlaubt; ein Test versucht per manipuliertem Request eine Marke zu erschleichen; das Ticketbuch überlebt einen Neustart mit verbrauchten Tickets
- **Agent:** builder · **Umfang:** L

### T-0.9 — Hub: Bus und State
- **Ziel:** Der Hub nimmt Events aus mehreren Quellen und liefert State — ausschließlich über Unix-Sockets.
- **Dateien:** `daimon/hub/daemon.py` [neu], `daimon/hub/state.py` [neu], `daimon/hub/bus.py` [neu]
- **Abhängigkeiten:** T-0.3.t, T-0.7, T-0.8
- **Akzeptanz:**
  - [ ] **Kein TCP-Socket** — `RestrictAddressFamilies=AF_UNIX` ist erfüllbar
  - [ ] Mood-Mapping verhält sich wie in T-0.3.t festgeschrieben
  - [ ] `GET /state` liefert Schema v2 aus Design §9
  - [ ] `rev` steigt bei jeder Änderung, bleibt bei No-Ops gleich
  - [ ] `focus.project` bei mehreren Sessions gesetzt
- **Verifikation:** `tests/verify/T-0.9.sh` — `pytest`; prüft zusätzlich per `ss -lx` und `ss -ltn`, dass der Hub-Prozess **einen** Unix-Socket und **keinen** TCP-Socket hält
- **Agent:** builder · **Umfang:** L

### T-0.10 — Gegendruck und Ratenbegrenzung
- **Ziel:** Ein Hook-Sturm bringt den Hub nicht um.
- **Dateien:** `daimon/hub/backpressure.py` [neu], `tests/test_backpressure.py` [neu]
- **Abhängigkeiten:** T-0.9
- **Akzeptanz:**
  - [ ] Gedeckelte Warteschlange je Produzent
  - [ ] Bei Überlauf werden **die ältesten** verworfen und gezählt
  - [ ] Ratenbegrenzung je Quelle
  - [ ] Verworfene Ereignisse erscheinen in der Diagnose
  - [ ] Test: 10 000 Events in schneller Folge — Speicher bleibt begrenzt, `GET /state` antwortet weiter unter 50 ms
- **Verifikation:** `tests/verify/T-0.10.sh` — `pytest tests/test_backpressure.py`; misst RSS vor und nach dem Sturm und die p95-Antwortzeit von `/state` währenddessen
- **Agent:** builder · **Umfang:** M

### T-0.11 — Hook-Bridge
- **Ziel:** Der einzige TCP-Port, isoliert und authentifiziert.
- **Dateien:** `daimon/hookbridge/` [neu], `config/systemd/daimon-hookbridge.service` [neu], `config/claude-hooks.json` [ändern]
- **Abhängigkeiten:** T-0.9, **T-0.11.v**
- **Akzeptanz:**
  - [ ] Lauscht nur auf `127.0.0.1:8787`, leitet an den Hub-Socket weiter
  - [ ] Shared Secret in `$XDG_RUNTIME_DIR/daimon/hook-token` (0600), beim Start erzeugt, ins Hook-Kommando eingesetzt
  - [ ] Ohne gültiges Token: 401, Versuch landet im Audit
  - [ ] **Exakte Routen** statt Präfix-Matching
  - [ ] `Content-Length` gedeckelt, Lese-Timeout gesetzt, Nebenläufigkeit begrenzt
  - [ ] Gültige Hooks bekommen weiterhin sofort 200; fehlerhafte einen unterscheidbaren Code
  - [ ] Bridge kann selbst nicht nach außen telefonieren
- **Verifikation:** `tests/verify/T-0.11.sh` — Positiv-Kanarienvogel (mit Token → 200 **und** `rev` steigt), dann einzeln: ohne Token → 401, überlanger Body → abgewiesen, unbekannte Route → 404, `/hoo` (Präfix) → 404, und ein `curl` aus der Bridge-Sandbox heraus schlägt fehl
- **Agent:** builder · **Umfang:** L

### T-0.12 — KWin-Fokus-Watcher
- **Ziel:** Fensterwechsel und `fullScreen` sind verfügbar — gebraucht schon vom VRAM-Gate.
- **Dateien:** `kwin-script/daimon-watcher/` [neu], `daimon/hub/focus.py` [neu]
- **Abhängigkeiten:** T-0.7
- **Akzeptanz:**
  - [ ] KWin-Script mit `workspace.windowActivated` und `captionChanged`, meldet per `callDBus`
  - [ ] Empfänger ist `Type=dbus` mit `BusName=` — `callDBus` ist fire-and-forget und verschluckt Aufrufe an tote Dienste
  - [ ] Reichert per `getWindowInfo(uuid)` an: Geometrie, PID, **`fullScreen`**
  - [ ] Überlebt `kwin --replace`
  - [ ] Read-only: Der Watcher kann nichts verändern
- **Verifikation:** `tests/verify/T-0.12.sh` — schaltet per `kglobalaccel` automatisiert zwischen zwei Fenstern um und prüft, dass **je Wechsel genau ein** korrelierbares Watcher-Ereignis mit passender UUID eintrifft; danach `kwin --replace` und Wiederholung
- **Agent:** builder · **Umfang:** L

### T-0.13 — Diagnose-Endpunkt ∥
- **Ziel:** Ein elfteiliges System ohne Messpunkte ist nicht betreibbar.
- **Dateien:** `daimon/hub/diag.py` [neu]
- **Abhängigkeiten:** T-0.10
- **Akzeptanz:**
  - [ ] `GET /diag` auf dem Hub-Socket
  - [ ] Warteschlangenlängen, verworfene Ereignisse, Gegendruck-Zustand
  - [ ] Latenz-Histogramme je Hop
  - [ ] Zähler je Typ (Rundenmarke, Aktionsfreigabe, API-Kontingent, Ticket): ausgegeben, eingelöst, abgelaufen, abgelehnt
  - [ ] Unit-Zustände
  - [ ] Verlässt den Rechner nicht — nur Unix-Socket
- **Verifikation:** `tests/verify/T-0.13.sh` — erzeugt Last, liest `/diag`, prüft dass Zähler sich bewegt haben und dass die gemeldete Anzahl verworfener Ereignisse zur erzeugten Last passt
- **Agent:** builder · **Umfang:** M

### T-0.14 — systemd-Units für den Kern
- **Ziel:** Hub, Bridge und Watcher starten mit der Sitzung, gehärtet.
- **Dateien:** `config/systemd/daimon-hub.service` [neu], `docs/INSTALL.md` [neu]
- **Abhängigkeiten:** T-0.11, T-0.12, T-0.13, **T-0.14.v**
- **Akzeptanz:**
  - [ ] `PartOf=graphical-session.target`, `Restart=on-failure`
  - [ ] Hub: `RestrictAddressFamilies=AF_UNIX` — jetzt möglich, weil der TCP-Port in der Bridge liegt
  - [ ] Härtung nach Design §7.5
  - [ ] `systemd-analyze security` je Unit dokumentiert
- **Verifikation:** `tests/verify/T-0.14.sh` — startet die Units, prüft `is-active` je Unit einzeln, liest `RestrictAddressFamilies` per `systemctl show` aus und führt in der Hub-Sandbox einen `curl`-Versuch aus, der fehlschlagen muss
- **Agent:** builder · **Umfang:** M

### Gate P0
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-0.14.sh
pytest -q
# Hub hat keinen TCP-Socket:
ss -ltnp 2>/dev/null | grep -q "pid=$(systemctl --user show -p MainPID --value daimon-hub)" && exit 1 || true
# Hook ohne Token wird abgewiesen, mit Token akzeptiert:
tests/verify/T-0.11.sh
```

---

# Phase 1 — Overlay minimal

**Ergebnis:** Das Pet steht auf dem Bildschirm und zeigt den Session-Status. Zwei Zustände reichen — es ist echt, nicht wegwerfbar.

**Abbruchkriterium:** Falls Bug 503121 trotz beider Umgehungen aus T−1.3 nicht beherrschbar ist, bleibt das Pet permanent gemappt und wird nur über Alpha ausgeblendet. Kein Rückfall auf Godot.

---

### T-1.1 — Rust-Projekt und Layer-Surface ∥
- **Ziel:** Bildschirmfüllendes, transparentes Overlay auf `Layer::Overlay`.
- **Dateien:** `face/Cargo.toml` [neu], `face/src/main.rs` [neu], `face/src/surface.rs` [neu]
- **Abhängigkeiten:** T−1.3, **T-1.1.v**
- **Akzeptanz:**
  - [ ] Crates nach Design §8.1
  - [ ] `Layer::Overlay`, Anker rundum, `exclusive_zone = -1`, `keyboard_interactivity = None`
  - [ ] `wl_output` **explizit** gebunden
  - [ ] 1×1 transparenter Buffer, `opaque_region` leer
  - [ ] Übernimmt die in T−1.3 bewährte Umgehung für Bug 503121
- **Verifikation:** `tests/verify/T-1.1.sh` — startet den Client, prüft dass der Prozess nach 10 s lebt; belegt Sichtbarkeit über einem Vollbildfenster per **Screenshot und Pixelprobe** auf eine Markerfarbe an bekannter Position (`supportInformation` ist eine Fähigkeitsauskunft, keine Surface-Inventur und taugt dafür nicht); prüft separat, dass ein darunterliegendes Testfenster weiterhin Eingaben empfängt
- **Agent:** builder · **Umfang:** M

### T-1.2 — Diagnose-Socket im Face
- **Ziel:** Das Overlay ist testbar, ohne hinzuschauen.
- **Dateien:** `face/src/diag.rs` [neu]
- **Abhängigkeiten:** T-1.1
- **Akzeptanz:**
  - [ ] Unix-Socket, 0600, meldet `{rev, mood, sprite, bubble_visible, last_render_ts, frames_rendered}`
  - [ ] `last_render_ts` ist der Zeitpunkt des tatsächlichen Commits, nicht des Zustandsempfangs
  - [ ] Kein Einfluss auf die Idle-CPU, wenn niemand liest
- **Verifikation:** `tests/verify/T-1.2.sh` — setzt einen Zustand über den Hub, liest den Face-Diagnose-Socket und prüft, dass `mood` übereinstimmt und `last_render_ts` **nach** dem Setzzeitpunkt liegt
- **Agent:** builder · **Umfang:** M

### T-1.3 — Input-Region und Click-Through
- **Ziel:** Klicks gehen überall durch, außer über dem Pet.
- **Dateien:** `face/src/input.rs` [neu]
- **Abhängigkeiten:** T-1.1, T−1.8
- **Akzeptanz:**
  - [ ] `set_input_region` nur über der Pet-Bounding-Box
  - [ ] Klick daneben erreicht das Fenster darunter
  - [ ] Alpha-Test verwirft Klicks auf transparente Ränder
  - [ ] Region wird nur bei Änderung der Bounding-Box aktualisiert
- **Verifikation:** `tests/verify/T-1.3.sh` — platziert ein Testfenster mit Klickzähler unter dem Pet, sendet über die **Test-Eingabevorrichtung aus T−1.8** je fünf Klicks neben und auf das Pet, prüft die Zähler beider Seiten numerisch. (v2.0 hing hier an `daimon-input`, das erst in P4 gebaut wird.)
- **Agent:** builder · **Umfang:** M

### T-1.4 — Sprite-Subsurface und SlotPool
- **Ziel:** Zwei Zustände, aus einem `wl_shm`-SlotPool gerendert.
- **Dateien:** `face/src/sprite.rs` [neu], `face/assets/` [neu]
- **Abhängigkeiten:** T-1.1
- **Akzeptanz:**
  - [ ] Sprite-Sheet einmal beim Start dekodiert, ARGB8888 **premultiplied**
  - [ ] `wl_subsurface` mit `set_desync()`
  - [ ] Zwei Zustände genügen: ruhig und dringend, unterschieden über die Glut-Palette
  - [ ] Bewegung über `set_position()` ohne Neuzeichnen
- **Verifikation:** `tests/verify/T-1.4.sh` — `cargo test -p face`; zusätzlich Zustandswechsel über den Hub und Prüfung im Diagnose-Socket, dass `sprite` wechselt und `frames_rendered` steigt
- **Agent:** builder · **Umfang:** L

### T-1.5 — Frame-Callback-Drosselung
- **Ziel:** Idle-CPU nahe null.
- **Dateien:** `face/src/render.rs` [neu]
- **Abhängigkeiten:** T-1.4, **T-1.5.v**
- **Akzeptanz:**
  - [ ] `dirty`/`frame_pending`; bei `!dirty` wird das Callback **nicht** neu armiert
  - [ ] `damage_buffer` nur auf geänderte Rechtecke
  - [ ] `calloop` bündelt Display-FD und Timer in einem `poll()`
- **Verifikation:** `tests/verify/T-1.5.sh` — misst 60 s Ruhe-CPU per `pidstat`, verlangt Mittel < 0,5 % **und** dass `frames_rendered` im Diagnose-Socket während dieser Zeit konstant bleibt
- **Agent:** builder · **Umfang:** M

### T-1.6 — Hub-Anbindung
- **Ziel:** Face spiegelt den Hub-Zustand.
- **Dateien:** `face/src/hub.rs` [neu]
- **Abhängigkeiten:** T-1.4, T-0.9
- **Akzeptanz:**
  - [ ] Verbindet sich über den Unix-Socket, nicht über TCP
  - [ ] Diffed auf `rev`
  - [ ] Unbekanntes `v` → `sleeping`
  - [ ] Hub weg → `sleeping`, kein Absturz, kein Log-Spam
- **Verifikation:** `tests/verify/T-1.6.sh` — misst über 20 Zustandswechsel die Latenz zwischen Hub-`rev`-Erhöhung und `last_render_ts`; verlangt p95 < 300 ms; stoppt danach den Hub und prüft den Wechsel auf `sleeping`
- **Agent:** builder · **Umfang:** M

### T-1.7 — Auth-Agent mit Absichtsmarken
- **Ziel:** Der Prozess, der Marken ausgibt und Freigaben einholt — von Anfang an getrennt vom Face.
- **Dateien:** `daimon/auth/` [neu], `config/systemd/daimon-auth.service` [neu]
- **Abhängigkeiten:** T-1.6, T-0.8, **T-1.7.v**

> v2.1 baute in T-1.7 zunächst den abgelösten Face-eigenen Pfad und trennte ihn in T-1.7b wieder ab. Eine überholte Sicherheitsgrenze erst zu implementieren und dann zu migrieren ist Verschwendung und lädt dazu ein, dass Reste stehenbleiben. Der Auth-Agent entsteht direkt.
- **Akzeptanz:**
  - [ ] PTT-Shortcut und alle Bestätigungsdialoge liegen hier, **nie** im Face
  - [ ] PTT ist eine **Umschaltung**, nicht Halten (`kglobalaccel` liefert keine verlässlichen Loslass-Ereignisse); Zeitlimit als Rückfall
  - [ ] Meldet `intent_mark` an den Hub; **der Hub** erzeugt die Rundenmarke
  - [ ] Aktionsvorschau nach Design §2.4: **feste Vorlage, feste Beschriftungen**, Parameterwerte escapt, längenbegrenzt, in Anführungszeichen. Kein freier Text, keine Auszeichnungssprache, keine Steuerzeichen
  - [ ] Face kann keine Freigabe erteilen — vom Hub abgewiesen
  - [ ] Eigener Socket, `SO_PEERPIDFD` geprüft
- **Verifikation:** `tests/verify/T-1.7.sh` (eingefroren nach T-1.7.v) — Positiv-Kanarienvogel: Freigabe vom Auth-Agenten wird angenommen; dieselbe Nachricht vom Face wird abgewiesen. Vorschau-Test mit je einem Fall für Steuerzeichen, ANSI-Sequenzen, Zeilenumbrüche, 10 000 Zeichen, **Bidi-Overrides (U+202E) und Isolate (U+2066-U+2069), Nullbreitenzeichen (U+200B), fehlende NFC-Normalisierung und einen verwechselbaren Pfad** (`~/Bilder/urlaub.png` mit kyrillischem Zeichen, real auf `~/.ssh/id_ed25519` zeigend) — jeder muss escapt, normalisiert, gekürzt und zitiert dargestellt werden — der Verifizierer prüft die gerenderte Region per Pixel- und Textextraktion, nicht die interne Zeichenkette. Zeitmessung PTT → Zustandswechsel (p95 < 200 ms)
- **Agent:** builder · **Umfang:** L

### T-1.8 — Ton bei `needs_input` ∥
- **Ziel:** Nur `needs_input` macht ein Geräusch.
- **Dateien:** `face/src/sound.rs` [neu]
- **Abhängigkeiten:** T-1.6
- **Akzeptanz:**
  - [ ] Ton ausschließlich beim Wechsel **nach** `needs_input`
  - [ ] Kein Ton bei `done`, `failed` oder wiederholtem `needs_input`
  - [ ] Abschaltbar über Konfiguration
  - [ ] Zähler im Diagnose-Socket
- **Verifikation:** `tests/verify/T-1.8.sh` — postet die Sequenz `needs_input, needs_input, done, failed, needs_input` und prüft im Diagnose-Socket, dass der Tonzähler **genau 2** beträgt
- **Agent:** builder · **Umfang:** S

### T-1.9 — systemd-Unit für Face ∥
- **Ziel:** Face startet mit der Sitzung.
- **Dateien:** `config/systemd/daimon-face.service` [neu]
- **Abhängigkeiten:** T-1.7
- **Akzeptanz:**
  - [ ] `PartOf=graphical-session.target`, `After=daimon-hub.service`
  - [ ] `RestrictAddressFamilies=AF_UNIX`
  - [ ] `Restart=on-failure`
- **Verifikation:** `tests/verify/T-1.9.sh` — `systemctl --user restart`, danach `is-active`, Diagnose-Socket antwortet, und ein `curl` aus der Sandbox schlägt fehl
- **Agent:** builder · **Umfang:** S

### T-1.10 — Alltagstauglichkeit
- **Ziel:** Belastbares Urteil, ob der Kernnutzen trägt.
- **Dateien:** `tests/evidence/phase1-usage.json` [erzeugt], `docs/phase1-verdict.md` [neu]
- **Abhängigkeiten:** T-1.9
- **Akzeptanz:**
  - [ ] ≥5 Arbeitstage Normalbetrieb, Zustandswechsel automatisch protokolliert
  - [ ] Gezählt: `needs_input`-Ereignisse, davon wie viele vor dem Blick ins Terminal bemerkt
  - [ ] Gezählt: Fehlalarme, Ablenkungen
  - [ ] Ressourcenverbrauch über die Laufzeit aufgezeichnet
  - [ ] Explizites Urteil: weiter, oder Mapping ändern und wiederholen
- **Verifikation:** `tests/verify/T-1.10.sh` — prüft in `phase1-usage.json`: `days >= 5`, `needs_input_events > 0`, `idle_cpu_p95 < 1.0`, `crashes == 0`, und dass `verdict` gesetzt und nicht `pending` ist
- **Agent:** investigator · **Umfang:** L (Kalenderzeit)

### Gate P1
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-1.10.sh
tests/verify/T-1.5.sh          # Idle-CPU
# Vollbild-Test: Pet bleibt sichtbar
tests/verify/T-1.1.sh
# Face oeffnet ueberhaupt keinen GPU-Kontext — Compute UND Graphics UND Geraete-FDs:
FACEPID=$(systemctl --user show -p MainPID --value daimon-face)
nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -qx "$FACEPID" && exit 1
nvidia-smi -q -x | grep -A2 '<process_info>' | grep -q ">$FACEPID<" && exit 1
ls -l /proc/$FACEPID/fd 2>/dev/null | grep -qE '/dev/(nvidia|dri/)' && exit 1
true
```

---

# Phase 2 — Overlay vollständig

**Ergebnis:** Animation, Sprechblase, Ziehen, Multi-Monitor.

**Abbruchkriterium:** Multi-Monitor-Wandern (T-2.6) ist optional und darf entfallen, ohne die Phase zu blockieren.

---

### T-2.1 — Vollständige Zustandsanimationen ∥
- **Ziel:** Alle acht Moods sind unterscheidbar.
- **Dateien:** `face/src/sprite.rs` [ändern], `face/assets/` [ändern]
- **Abhängigkeiten:** T-1.4, **T-2.1.v**
- **Akzeptanz:**
  - [ ] Acht Zustände über Glut-Helligkeit und Farbe
  - [ ] Fehlende Animation fällt auf `idle` zurück, ohne Fehler
  - [ ] Übergänge sind weich, kein Springen
- **Verifikation:** `tests/verify/T-2.1.sh` — durchläuft alle acht Moods, nimmt je Mood die **Pet-Region** auf und vergleicht normalisierte Bildhashes: alle acht müssen paarweise verschieden sein. Ein unterschiedlicher `sprite`-Bezeichner im Diagnose-Socket beweist nichts — identische Sprites bestünden ihn.
- **Agent:** builder · **Umfang:** M

### T-2.2 — Sprechblase als zweite Subsurface
- **Ziel:** Textblase, unabhängig vom Sprite-Takt.
- **Dateien:** `face/src/bubble.rs` [neu]
- **Abhängigkeiten:** T-2.1
- **Akzeptanz:**
  - [ ] Eigene `wl_subsurface`, relativ zum Pet
  - [ ] Textumbruch, klemmt am Bildschirmrand
  - [ ] Aktualisierung zeichnet den Sprite **nicht** neu
  - [ ] Klick auf die Blase schließt sie und meldet dem Hub
- **Verifikation:** `tests/verify/T-2.2.sh` — postet ein Bubble-Ereignis, prüft `bubble_visible == true` im Diagnose-Socket, merkt sich `frames_rendered` des Sprites, aktualisiert nur den Blasentext und prüft, dass der Sprite-Zähler **unverändert** blieb
- **Agent:** builder · **Umfang:** M

### T-2.3 — Ziehen über Subsurface-Position
- **Ziel:** Flüssiges Verschieben ohne Compositor-Roundtrip.
- **Dateien:** `face/src/input.rs` [ändern]
- **Abhängigkeiten:** T-1.3
- **Akzeptanz:**
  - [ ] Über `wl_subsurface.set_position()`, **nicht** `set_margin`
  - [ ] Kein `configure` während des Ziehens
  - [ ] Input-Region wandert mit
  - [ ] Position wird gemerkt
- **Verifikation:** `tests/verify/T-2.3.sh` — simuliert einen Zug über 200 px, zählt im Diagnose-Socket die empfangenen `configure`-Ereignisse (muss 0 sein) und misst die CPU während des Zugs (< 5 % eines Kerns)
- **Agent:** builder · **Umfang:** M

### T-2.4 — Ein-/Ausblenden härten
- **Ziel:** 503121 ist im Alltag beherrscht.
- **Dateien:** `face/src/surface.rs` [ändern]
- **Abhängigkeiten:** T-2.1, **T-2.4.v**
- **Akzeptanz:**
  - [ ] NULL-Buffer-Unmap wird nicht verwendet
  - [ ] 100 Zyklen ohne Hänger
  - [ ] Gewählter Weg im Code kommentiert, mit Verweis auf den Bug
- **Verifikation:** `tests/verify/T-2.4.sh` — 100 Zyklen; nach jedem Zyklus muss der Diagnose-Socket innerhalb 1 s einen frischen `last_render_ts` melden; ein einziger Ausfall lässt den Test scheitern
- **Agent:** builder · **Umfang:** M

### T-2.5 — Multi-Output und Hotplug
- **Ziel:** Monitorwechsel bringt das Pet nicht um.
- **Dateien:** `face/src/output.rs` [neu]
- **Abhängigkeiten:** T-2.4
- **Akzeptanz:**
  - [ ] Eine Layer-Surface je `wl_output`
  - [ ] Output-Removal → Surface neu erzeugen
  - [ ] Pet erscheint auf genau einem Output, konfigurierbar
  - [ ] Monitor-Sleep überlebt
- **Verifikation:** `tests/verify/T-2.5.sh` — schaltet per `kscreen-doctor` einen Ausgang aus und wieder ein, prüft danach Prozessleben, genau eine sichtbare Instanz und frischen `last_render_ts`
- **Agent:** builder · **Umfang:** M

### T-2.6 — Wandern zwischen Monitoren *(optional)* ∥
- **Ziel:** Das Pet lässt sich auf einen anderen Bildschirm ziehen.
- **Dateien:** `face/src/output.rs` [ändern]
- **Abhängigkeiten:** T-2.5, T-2.3
- **Akzeptanz:**
  - [ ] Zug über die Bildschirmgrenze übergibt die Instanz
  - [ ] Kein Flackern, keine doppelte Darstellung
- **Verifikation:** `tests/verify/T-2.6.sh` — simuliert den Zug über die Grenze, prüft dass genau ein Output das Pet meldet, vorher und nachher
- **Agent:** builder · **Umfang:** L

### T-2.7 — Kontextmenü ∥
- **Ziel:** Rechtsklick öffnet Grundfunktionen.
- **Dateien:** `face/src/menu.rs` [neu]
- **Abhängigkeiten:** T-2.2
- **Akzeptanz:**
  - [ ] Über `get_popup` (Grab und Auto-Dismiss sind hier erwünscht)
  - [ ] Einträge: Ohren an/aus, Augen an/aus, Persona wechseln, Beenden
  - [ ] Schaltet die tatsächlichen Units, nicht interne Flags
- **Verifikation:** `tests/verify/T-2.7.sh` — löst den Menüeintrag programmatisch aus und prüft, dass die konfigurierte Ziel-Unit gestoppt wurde; in P2 gegen eine **Attrappen-Unit**, weil `daimon-ears` erst in P3 entsteht. Die Prüfung gegen die echte Unit wird in T-3.15 nachgeholt.
- **Agent:** builder · **Umfang:** M

### Gate P2
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-2.4.sh    # 100 Ein-/Ausblende-Zyklen
tests/verify/T-2.5.sh    # Hotplug
tests/verify/T-1.5.sh    # Idle-CPU weiterhin < 0,5 %
```

---

# Phase 3 — Sprache

**Ergebnis:** Das Pet hört zu und antwortet. Es führt **nichts** aus.

**Abbruchkriterium:** Der in T−1.7 festgelegte Wake-Word-Plan gilt. Ist er Plan C (nur PTT), entfallen T-3.5 und T-3.6; der Rest läuft unverändert.

---

### T-3.1 — Audio-Aufnahme mit hartem Lebenszyklus ∥
- **Ziel:** Aufnahme, die den Plasma-Indikator korrekt bedient.
- **Dateien:** `daimon/ears/capture.py` [neu]
- **Abhängigkeiten:** T-0.5, **T-3.1.v**
- **Akzeptanz:**
  - [ ] `sounddevice` mit `device="pipewire"` — **nicht** `"default"`
  - [ ] `blocksize=512`, 16 kHz, mono, int16
  - [ ] `PIPEWIRE_LATENCY` **vor** dem Import gesetzt
  - [ ] Ausschalten ruft `stream.close()` — nicht `stop()`
- **Verifikation:** `tests/verify/T-3.1.sh` — prüft per `pw-dump` einzeln: vor dem Start kein `Stream/Input/Audio`, nach dem Start genau einer mit unserem Namen, nach `close()` wieder keiner
- **Agent:** builder · **Umfang:** M

### T-3.2 — VAD mit Hysterese ∥
- **Ziel:** Segmentierung, die Wortenden nicht abschneidet.
- **Dateien:** `daimon/ears/vad.py` [neu]
- **Abhängigkeiten:** T-3.1
- **Akzeptanz:**
  - [ ] `pysilero-vad >= 3.4.0`, exakt 512-Sample-Chunks
  - [ ] **Hysterese:** Einsatz bei ≥0,5, Ende erst nach 300–500 ms unter ~0,35
  - [ ] Konfigurierbar
- **Verifikation:** `tests/verify/T-3.2.sh` — `pytest`; enthält einen Test mit synthetischem Signal, in dem Sprache mit 200 ms Pause **ein** Segment ergibt und mit 800 ms Pause **zwei**
- **Agent:** builder · **Umfang:** M

### T-3.3 — Ringpuffer mit Vorlauf
- **Ziel:** Silben vor dem Auslöser bleiben erhalten; Fehltreffer hinterlassen nichts.
- **Dateien:** `daimon/ears/ring.py` [neu]
- **Abhängigkeiten:** T-3.2
- **Akzeptanz:**
  - [ ] Feste Größe, 20 s = 640 KB, vorab alloziert
  - [ ] `mlock()`
  - [ ] Vorlauf 1,0–1,5 s wird beim Auslösen mitgegeben
  - [ ] **Bei abgelehntem Auslöser wird der Ring verworfen**
  - [ ] Schreibt niemals auf Platte
- **Verifikation:** `tests/verify/T-3.3.sh` — `pytest`; zusätzlich läuft der Prozess unter `strace -f -e trace=openat,write` und der Test prüft, dass **keine** Schreiboperation auf einen regulären Dateideskriptor erfolgt
- **Agent:** builder · **Umfang:** M

### T-3.4 — Rückkopplungssperre
- **Ziel:** Die eigene Stimme kann sich nicht selbst reaktivieren.
- **Dateien:** `daimon/ears/interlock.py` [neu]
- **Abhängigkeiten:** T-3.3, **T-3.4.v**
- **Akzeptanz:**
  - [ ] KWS und STT gesperrt, solange TTS spielt, **plus 500 ms Nachlauf**
  - [ ] Gilt auch bei gedrücktem PTT
  - [ ] Echo-Referenz: TTS-Ausgabepuffer wird verglichen, Treffer verworfen
  - [ ] Sperrzustand im Diagnose-Socket sichtbar
- **Verifikation:** `tests/verify/T-3.4.sh` — lässt das System per TTS den eigenen Wake-Word-Namen aussprechen, wiederholt 20-mal, und prüft im `/diag`, dass **null** Wake-Word-Auslösungen registriert wurden
- **Agent:** builder · **Umfang:** L

### T-3.5 — Wake-Word-Erkennung *(entfällt bei Plan C)*
- **Ziel:** Der Name löst aus, alles andere nicht.
- **Dateien:** `daimon/ears/wakeword.py` [neu]
- **Abhängigkeiten:** T-3.4, T−1.7
- **Akzeptanz:**
  - [ ] Engine und Schwellen aus `spikes/wakeword/results.json`
  - [ ] Läuft nur, wenn VAD Sprache meldet und die Sperre offen ist
  - [ ] Erzeugt **kein** Autorisierungsobjekt. Ears meldet ein Transkript mit der Markierung **`user_audio`**; der Hub leitet daraus höchstens ein API-Kontingent ab
  - [ ] CPU unter 1 % eines Kerns
- **Verifikation:** `tests/verify/T-3.5.sh` — spielt die Testmenge aus T−1.1 ab, verlangt die dort gemessene FRR ±5 Prozentpunkte, misst 60 s CPU (< 1 %), und prüft im `/diag`, dass aus Wake-Word-Ereignissen **null** Rundenmarken und **null** Aktionsfreigaben entstanden sind, sondern ausschließlich API-Kontingente
- **Agent:** builder · **Umfang:** M

### T-3.6 — Aufnahmepfad unter Rundenmarke ∥
- **Ziel:** Ears nimmt auf, wenn der Auth-Agent eine Runde geöffnet hat.
- **Dateien:** `daimon/ears/marked_capture.py` [neu]
- **Abhängigkeiten:** T-3.3, T-1.7
- **Akzeptanz:**
  - [ ] **Der PTT-Umschalter liegt im Auth-Agenten** (T-1.7), nicht in Ears. Ears reagiert auf den vom Hub gemeldeten Rundenzustand
  - [ ] Transkripte innerhalb einer offenen Runde tragen **`user_ptt`**, außerhalb **`user_audio`**
  - [ ] Kein Halten, keine Loslass-Ereignisse — der Hub schließt die Runde per Umschaltung oder Zeitlimit
  - [ ] Die Rückkopplungssperre (T-3.4) gilt auch innerhalb einer offenen Runde
- **Verifikation:** `tests/verify/T-3.6.sh` — prüft, dass ein Transkript bei offener Runde `user_ptt` und bei geschlossener `user_audio` trägt; prüft, dass Ears selbst **keine** Marke erzeugen kann (ein direkt an den Hub gesendetes `intent_mark` von Ears wird abgewiesen; Positiv-Kanarienvogel: dasselbe vom Auth-Agenten wird angenommen)
- **Agent:** builder · **Umfang:** M

### T-3.7 — GPU-Worker-Gerüst ∥
- **Ziel:** Ein Muster für Modellprozesse, die sich selbst beenden.
- **Dateien:** `daimon/gpu/worker.py` [neu], `config/systemd/daimon-gpu@.service` [neu]
- **Abhängigkeiten:** T-0.12, T-0.13, **T-3.7.v**
- **Akzeptanz:**
  - [ ] Socket-aktiviert, ein Socket je Modelltyp, eine Template-Unit
  - [ ] Idle-Timer → **Prozessende**
  - [ ] Vor dem Laden: Fullscreen-Prüfung (über T-0.12) und VRAM-Prüfung
  - [ ] **Serialisierung:** höchstens ein Ladevorgang gleichzeitig, Sperre im Hub
  - [ ] Bei zu wenig VRAM: geordnete Absage mit Grund
  - [ ] Kaltstartzeit wird gemeldet
- **Verifikation:** `tests/verify/T-3.7.sh` — startet zwei Ladeanfragen gleichzeitig und prüft, dass sie serialisiert werden; simuliert Fullscreen und prüft die Absage; misst VRAM per `nvidia-smi` vor Start, während Betrieb und 5 s nach dem Idle-Timeout (muss auf den Ausgangswert ±50 MB zurück)
- **Agent:** builder · **Umfang:** L

### T-3.8 — STT-Worker
- **Ziel:** Audio rein, Text raus, VRAM danach frei.
- **Dateien:** `daimon/gpu/stt.py` [neu]
- **Abhängigkeiten:** T-3.7, T−1.2
- **Akzeptanz:**
  - [ ] Umsetzung nach der in T−1.2 gewählten Variante
  - [ ] Deutsch und Englisch
  - [ ] Prozessende gibt VRAM vollständig frei
  - [ ] Latenz für eine 5-s-Äußerung gemessen
- **Verifikation:** `tests/verify/T-3.8.sh` — transkribiert 20 Referenzaufnahmen, verlangt WER unter dem in T−1.2 gemessenen Wert +2 Prozentpunkte, misst p95-Latenz, prüft VRAM-Rückgabe nach dem Exit
- **Agent:** builder · **Umfang:** L

### T-3.9 — Piper-TTS ∥
- **Ziel:** Gesprochene Antworten ohne GPU.
- **Dateien:** `daimon/face/tts.py` [neu]
- **Abhängigkeiten:** T-0.5
- **Akzeptanz:**
  - [ ] Piper `de_DE-thorsten-high`, CPU
  - [ ] Stimme aus der Persona-Datei
  - [ ] Unterbrechbar; neue Äußerung bricht die laufende ab
  - [ ] Meldet Start und Ende an die Rückkopplungssperre
  - [ ] 0 VRAM
- **Verifikation:** `tests/verify/T-3.9.sh` — misst TTFA über 20 Läufe (p95 < 200 ms), prüft `nvidia-smi` auf 0 zusätzliche Compute-Prozesse, und testet Unterbrechung: zweite Äußerung während der ersten beendet diese innerhalb 100 ms
- **Agent:** builder · **Umfang:** M

### T-3.10 — Persona-Lader ∥
- **Ziel:** Charakter aus der Datei, nicht aus dem Code.
- **Dateien:** `daimon/mind/persona.py` [neu], `config/persona/ember.toml` [neu]
- **Abhängigkeiten:** T-0.5
- **Akzeptanz:**
  - [ ] TOML nach Design §10.1
  - [ ] `speech_threshold` als Enum mit vier Stufen
  - [ ] `system_prompt` fließt in den API-Aufruf
  - [ ] Fehlende Persona → sprechende Fehlermeldung, kein stiller Vorgabe-Charakter
  - [ ] Zweite Beispiel-Persona als Beleg der Austauschbarkeit
- **Verifikation:** `tests/verify/T-3.10.sh` — `pytest`; prüft zusätzlich, dass der erzeugte Prompt den Persona-Text wörtlich enthält und dass ein Wechsel der Persona-Datei den Prompt ändert
- **Agent:** builder · **Umfang:** S

### T-3.11 — Egress-Broker und Mind-Unit
- **Ziel:** Die einzige ausgehende Verbindung liegt in einem Prozess, der keinen Modellinhalt verarbeitet.
- **Dateien:** `daimon/brokers/egress/` [neu], `config/systemd/daimon-egress.service` [neu], `config/systemd/daimon-mind.service` [neu]
- **Abhängigkeiten:** T-3.10, T-0.8, **T-3.11.v**
- **Akzeptanz:**
  - [ ] **`daimon-mind` hat `RestrictAddressFamilies=AF_UNIX` und keinen Token.** In v2.0 hielt Mind beides — „kein API-Aufruf ohne Autorisierung" war damit reine Anwendungslogik in dem Prozess, der die Anfragen stellt
  - [ ] `daimon-egress` hält den Token über `LoadCredential=`, nie in Umgebungsvariablen
  - [ ] Egress verlangt für **jede** Anfrage ein Einmal-Kontingent aus dem Hub-Ticketbuch
  - [ ] Feste Ziel-Domain, Zertifikatsprüfung, kein Proxy aus der Umgebung
  - [ ] Obergrenze pro Zeitfenster — eine Schleife in Mind kann die API nicht leerlaufen lassen
  - [ ] **Egress transportiert opak** — interpretiert, rendert, speichert und protokolliert keine Körper. Geloggt werden nur `{quota_id, bytes, status, duration}`
  - [ ] Der Mitschnitt für T-5.10 ist **nur im Testprofil** übersetzbar bzw. aktivierbar, prüft im Speicher und schreibt ausschließlich Hashes und Strukturmerkmale. Im Normalbetrieb ist Rohkörper-Protokollierung nicht erreichbar
  - [ ] Token erscheint in keinem Log
  - [ ] Rotationsverfahren dokumentiert
- **Verifikation:** `tests/verify/T-3.11.sh` — prüft einzeln: `curl` aus der Mind-Sandbox schlägt fehl; Token nicht in `/proc/<mind-pid>/environ` **und nicht** im Adressraum von Mind; eine Anfrage ohne Kontingent wird vom Egress abgewiesen; eine mit Kontingent gelingt (Kanarienvogel); dieselbe Anfrage zweimal mit demselben Kontingent wird beim zweiten Mal abgewiesen; ein absichtlich geloggter Token erscheint redigiert
- **Agent:** builder · **Umfang:** L

### T-3.12 — Routing, Durchgang 1 (werkzeugfähig, kontextlos)
- **Ziel:** Absicht wird ausschließlich aus der Nutzeräußerung bestimmt.
- **Dateien:** `daimon/mind/router.py` [neu]
- **Abhängigkeiten:** T-3.11, T-0.8
- **Akzeptanz:**
  - [ ] Lokal ohne LLM: Lautstärkeabfrage, Fensterliste, Session-Status, Uhrzeit — **nur lesend**, weil noch kein Executor existiert
  - [ ] Aktionswünsche werden in P3 als „noch nicht unterstützt" beantwortet, nicht ausgeführt
  - [ ] **Kein Bildschirmkontext in diesem Durchgang**
  - [ ] **Kein Egress-Aufruf ohne gültiges API-Kontingent**
  - [ ] API-Fehler → gesprochene Fehlermeldung
- **Verifikation:** `tests/verify/T-3.12.sh` — `pytest`; enthält einen Test, der belegt, dass ein Hintergrundereignis ohne Kontingent **keinen** Egress-Aufruf erzeugt (Netzwerkaufrufe werden gezählt), und einen, der belegt, dass in Durchgang 1 kein OCR-Text im Prompt landet
- **Agent:** builder · **Umfang:** L

### T-3.13 — Routing, Durchgang 2 (kontextfähig, werkzeuglos)
- **Ziel:** Antworten mit Kontext, ohne die Fähigkeit, Aktionen zu emittieren.
- **Dateien:** `daimon/mind/answer.py` [neu]
- **Abhängigkeiten:** T-3.12
- **Akzeptanz:**
  - [ ] Diesem Durchgang wird **keine** Werkzeugliste übergeben
  - [ ] Rückgabe ist ausschließlich Text; das Schema lässt keinen `action_request` zu
  - [ ] In P3 ist der Kontext noch leer — die Struktur steht für P5
- **Verifikation:** `tests/verify/T-3.13.sh` — `pytest`; enthält einen Test, in dem die Modellantwort einen wohlgeformten Aktionsvorschlag enthält und der Router ihn nachweislich **verwirft**, statt ihn weiterzureichen
- **Agent:** builder · **Umfang:** M

### T-3.13b — Markierungsverfolgung
- **Ziel:** Jedes Textstück kennt seine Herkunft, und die Senkentabelle wird durchgesetzt.
- **Dateien:** `daimon/common/taint.py` [neu], `daimon/common/protocol.py` [ändern], `daimon/common/ipc.py` [ändern], `daimon/hookbridge/` [ändern], `daimon/hub/results.py` [ändern], `daimon/auth/preview.py` [ändern], `daimon/mind/router.py` [ändern]
- **Abhängigkeiten:** T-3.13, **T-3.13b.v**
- **Akzeptanz:**
  - [ ] **Vier** Markierungen nach Design §5.2: `user_ptt`, `user_audio`, `trusted`, `tainted`
  - [ ] **Typisierte Werte, keine Konvention:** markierte Werte überleben IPC, Serialisierung, Verkettung, Datenbankschreibung und -lesung. Geschützte Senken nehmen **keine rohen Zeichenketten** entgegen — der Aufruf ist ein Typfehler
  - [ ] Markierung wird **aus der Datenherkunft** abgeleitet, nicht aus der holenden Komponente: ein Aktionsergebnis mit Dateiinhalt oder Fenstertitel ist in diesem Anteil `tainted`
  - [ ] **Freie Hook-Textfelder sind `tainted`** (`last_assistant_message`, `message`, `error`, `cwd`); nur `hook_event_name` und die abgeleitete Mood sind `trusted`
  - [ ] Markierung ist **ansteckend**: Ausgabe eines Durchgangs, der `tainted` gesehen hat, ist selbst `tainted`
  - [ ] Senkentabelle aus Design §5.2 wird erzwungen, nicht dokumentiert — inklusive der Auth-Vorschau als eigener Senke mit Escaping und Längengrenze
  - [ ] `user_audio` erreicht weder Durchgang 1 noch Gedächtnis noch proaktive Auslöser
  - [ ] Ein Versuch, `tainted`-Material in den werkzeugfähigen Durchgang zu geben, wirft — er wird nicht still gefiltert, damit Fehler auffallen
  - [ ] **Keine Handlungs-Anapher:** „mach das" ohne benannte Aktion und Ziel führt zu einer Rückfrage, nicht zu einer Auflösung aus dem Kontext
- **Verifikation:** `tests/verify/T-3.13b.sh` (eingefroren nach T-3.13b.v) — prüft je Senke und je Markierung einen erlaubten und einen verbotenen Fall; **Markierungsverlust-Mutanten an jeder in P3 existierenden Grenze** (IPC-Serialisierung, Hook-Bridge, Aktionsergebnis, Auth-Vorschau, Verkettung, Formatierung) müssen zurückgewiesen werden — der Datenbank-Roundtrip liegt in T-6.1, weil die Datenbank erst dort existiert; Tests belegen, dass **freie Modellausgabe aus beiden Durchgängen** `tainted` wird und dass eine Mutante, die strukturierte Modellzeichenketten zu `trusted` befördert, zurückgewiesen wird; einer, dass `user_audio` nicht ins Gedächtnis gelangt; einer, dass „mach das" eine Rückfrage statt einer Aktion erzeugt
- **Agent:** builder · **Umfang:** L

### T-3.14 — Sprachzustände im Overlay
- **Ziel:** Man sieht, dass das Pet zuhört, denkt und spricht.
- **Dateien:** `face/src/sprite.rs` [ändern], `daimon/hub/state.py` [ändern]
- **Abhängigkeiten:** T-3.13, T-1.6
- **Akzeptanz:**
  - [ ] `voice.state`: `idle` \| `listening` \| `processing` \| `speaking`
  - [ ] Überlagert den Session-Mood, ersetzt ihn nicht
  - [ ] Rückfall nach der Antwort
- **Verifikation:** `tests/verify/T-3.14.sh` — misst über 20 Auslösungen die Zeit zwischen PTT-Druck und dem Wechsel auf `listening` im Face-Diagnose-Socket; verlangt p95 < 200 ms
- **Agent:** builder · **Umfang:** M

### T-3.15 — Kill-Switch und Ende-zu-Ende-Messung
- **Ziel:** Abschaltbarkeit belegt, Gesamtlatenz gemessen.
- **Dateien:** `daimon/ears/killswitch.py` [neu], `config/systemd/daimon-ears.service` [neu], `tests/evidence/phase3-latency.json` [erzeugt]
- **Abhängigkeiten:** T-3.14, T-3.9, **T-3.15.v**
- **Akzeptanz:**
  - [ ] Hotkey stoppt `daimon-ears.service`
  - [ ] Nach dem Stopp kein Aufnahmestream, kein Plasma-Mikrofonsymbol
  - [ ] `RestrictAddressFamilies=AF_UNIX`
  - [ ] ≥20 echte Sprachanfragen mit Latenz je Stufe protokolliert
  - [ ] Falsch-Positiv-Rate über eine Woche Alltagsbetrieb
- **Verifikation:** `tests/verify/T-3.15.sh` — prüft einzeln: nach Stopp meldet `pw-dump` null Aufnahmestreams; `curl` aus der Ears-Sandbox schlägt fehl; `phase3-latency.json` enthält `n >= 20` und `p95_wake_to_audio_ms < 1500`
- **Agent:** builder · **Umfang:** L

### Gate P3
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-3.15.sh
tests/verify/T-3.4.sh     # Rückkopplungssperre
tests/verify/T-3.12.sh    # kein Egress-Aufruf ohne Kontingent
pytest -q
# VRAM nach Ruhe wieder frei — nur unsere Worker prüfen, leere Liste ist Erfolg:
sleep 90
for p in $(systemctl --user list-units 'daimon-gpu@*' --state=active --no-legend | awk '{print $1}'); do
  pid=$(systemctl --user show -p MainPID --value "$p")
  nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -qx "$pid" && exit 1
done
```

---

# Phase 4 — Aktuation

**Ergebnis:** Das Pet führt aus, was unter einer gültigen, vom Auth-Agenten ausgestellten Absichtsmarke angefordert wurde — nichts sonst. Über den Handelnden wird keine Aussage getroffen (Design §1.2).

**Abbruchkriterium:** Ist der argumentvalidierende Broker (T-4.7) nicht sauber zu bekommen, wird die Aktionsmenge auf die reduziert, die er sicher abdeckt. Die Phase wird **nicht** mit einer generischen `invokeShortcut`-Freigabe abgeschlossen.

---

### T-4.1 — Aktionskandidaten generieren ∥
- **Ziel:** Vorschlagsliste aus `kglobalaccel` — ausdrücklich noch keine Whitelist.
- **Dateien:** `tools/generate-action-candidates.py` [neu], `config/actions/candidates.yaml` [erzeugt]
- **Abhängigkeiten:** T-0.5
- **Akzeptanz:**
  - [ ] Liest alle Komponenten und `shortcutNames()`
  - [ ] Erzeugt Einträge mit `status: candidate`
  - [ ] Idempotent
  - [ ] **Kein Kandidat ist ohne Handprüfung ausführbar**
- **Verifikation:** `tests/verify/T-4.1.sh` — prüft, dass `candidates.yaml` ≥20 Einträge hat und dass **jeder** `status: candidate` trägt; ein Eintrag mit `status: approved` lässt den Test scheitern
- **Agent:** builder · **Umfang:** M

### T-4.2 — Geprüfter Aktionskatalog
- **Ziel:** Die tatsächliche Whitelist, von Hand freigegeben.
- **Dateien:** `config/actions/core.yaml` [neu], `docs/action-review.md` [neu]
- **Abhängigkeiten:** T-4.1
- **Akzeptanz:**
  - [ ] Je Aktion: `destructive`, `reversible_by`, `externally_visible`, `open_world`, `never_cacheable`
  - [ ] Je Aktion Vorgaben für `foreground` / `background` / `scheduled`
  - [ ] Parameter typisiert und wertbeschränkt (`audio.volume.set` mit `value_between: [0.0, 1.0]`)
  - [ ] Jeder freigegebene Eintrag trägt eine Begründung
  - [ ] `org_kde_powerdevil`, `kwin.script.load` und alles Unklassifizierte bleiben `candidate`
- **Verifikation:** `tests/verify/T-4.2.sh` — validiert das Schema; prüft, dass **jede** `destructive`-Aktion entweder `reversible_by` gesetzt hat oder `foreground: ask`; prüft, dass keine `approved`-Aktion ohne `rationale`-Feld existiert
- **Agent:** builder · **Umfang:** L

### T-4.3.t — Tests für die Policy-Engine
- **Ziel:** Die Auswertungsreihenfolge steht fest, bevor sie existiert.
- **Dateien:** `tests/test_policy.py` [neu]
- **Abhängigkeiten:** T-4.2
- **Akzeptanz:**
  - [ ] deny → ask → allow, erster Treffer, Spezifität irrelevant
  - [ ] deny ist Vereinigung über alle Ebenen
  - [ ] `unknown_action` → deny; `unparseable_argument` → ask (verschiedene Zustände)
  - [ ] Dieselbe Aktion, drei `initiator`, drei Verdikte
  - [ ] **`initiator` stammt aus der eingelösten Rundenmarke, nicht aus dem Request** — eigener Test
  - [ ] Circuit Breaker schlägt jede Regel und jeden Modus
  - [ ] Alle Tests schlagen zunächst fehl
- **Verifikation:** `tests/verify/T-4.3.sh` — `pytest tests/test_policy.py`; erwartet, dass **alle** Tests fehlschlagen und **keiner** einen Importfehler wirft
- **Agent:** builder · **Umfang:** M

### T-4.4 — Policy-Engine
- **Ziel:** Der Entscheider.
- **Dateien:** `daimon/hub/policy.py` [neu], `config/policy.yaml` [neu]
- **Abhängigkeiten:** T-4.3.t, T-0.8, **T-4.4.v**
- **Akzeptanz:**
  - [ ] Alle Tests aus T-4.3.t grün
  - [ ] **Der Hub kanonisiert die Parameter selbst** und berechnet `params_hash` selbst
  - [ ] `initiator` wird aus der eingelösten Rundenmarke abgeleitet
  - [ ] Strukturierte `when:`-Prädikate, keine String-Globs
  - [ ] Zustimmungs-Cache mit Schlüssel `(session_id, action_id, params_hash)`
  - [ ] Vier Gültigkeiten: `once`, `session`, `ttl:*`, `persistent`
  - [ ] **Direktbefehl-Ausnahme ist Hub-Eigentum:** greift nur bei `direct: true` im Katalog **und** Erkennung durch den deterministischen Hub-Parser. Jede aus einer Modellausgabe stammende Aktion geht durch die Vorschau, unabhängig von ihrem Katalogflag
- **Verifikation:** `tests/verify/T-4.4.sh` — `pytest tests/test_policy.py` (alle grün); zusätzlich ein Test, der einen manipulierten `params_hash` im Request mitschickt und belegt, dass der Hub ihn ignoriert und selbst rechnet
- **Agent:** builder · **Umfang:** L

### T-4.5 — Ausführungsauftrag
- **Ziel:** Ein Auftragsformat, das Ziel, Frist und Einmaligkeit trägt — ohne Signatur.
- **Dateien:** `daimon/common/order.py` [neu], `daimon/hub/order.py` [neu]
- **Abhängigkeiten:** T-4.4, T-0.8, T-4.5.v
- **Akzeptanz:**
  - [ ] **Keine Signatur.** Design §1.2 und §6.2 haben den HMAC gestrichen: ein Broker kann nicht mit einem Schlüssel prüfen, den nur der Hub hat, und für den ausgeschlossenen Angreifer wäre er ohnehin per `ptrace` lesbar
  - [ ] Felder: `audience`, `schema`, `action_id`, `params`, `params_hash`, `ticket`, `deadline_monotonic`, `turn_id`
  - [ ] **`audience`** bindet an genau einen Broker; ein DBus-Auftrag ist bei `daimon-fs` nicht einreichbar
  - [ ] Festgelegte **kanonische Serialisierung**; `schema` verhindert abweichende Lesarten
  - [ ] **Monotone Frist** — eine Zeitumstellung verlängert nichts
  - [ ] Herkunft über den Socket (Peer-Prüfung nach Design §1.2), nicht über Kryptografie
  - [ ] Ticketeinlösung **beim Hub**, unmittelbar vor der Ausführung
- **Verifikation:** `tests/verify/T-4.5.sh` (eingefroren) — **reiner Prüflogik-Test ohne Ausführung**: gültiger Auftrag wird angenommen; einzeln abgewiesen werden manipulierte Parameter, falsche `audience`, abgelaufene monotone Frist, wiederholtes Ticket, unbekanntes `schema`, abweichende Serialisierung. Eine Mutante, die eine HMAC-Prüfung einführt, wird als Verstoß gegen §6.2 zurückgewiesen. Ausführungs-Kanarienvögel liegen bei den Broker-Tasks
- **Agent:** builder · **Umfang:** M

### T-4.6 — Audit mit Hash-Kette und Verankerung
- **Ziel:** Manipulation ist erkennbar, ehrlich in ihren Grenzen.
- **Dateien:** `daimon/hub/audit.py` [neu]
- **Abhängigkeiten:** T-0.6, **T-4.6.v**
- **Akzeptanz:**
  - [ ] JSONL unter `$XDG_STATE_HOME/daimon/audit/`, 0700/0600
  - [ ] `seq` und `prev_hash` je Datensatz
  - [ ] **Kettenkopf periodisch und bei jeder Rotation ins Journal verankert**
  - [ ] Verifizierer prüft **beide** Ströme und meldet eine ersetzte Datei
  - [ ] Alle Felder aus Design §7.6, inklusive `prompt_shown`, `params_hash`, **`mark_id`**, `initiator`, `turn_id`, `outcome`
  - [ ] **Redaktion nach Herkunft, nicht nach Katalogflag:** jeder `tainted`-Wert wird zu `<redacted:sha256:…>` plus Länge — unabhängig davon, ob der Katalog ihn als `sensitive` führt. Das Katalogflag ist eine zusätzliche, keine alternative Bedingung
  - [ ] Rotation trägt den letzten Hash in die neue Datei
- **Verifikation:** `tests/verify/T-4.6.sh` — erzeugt 100 Datensätze, dann einzeln: eine Zeile geändert → erkannt; eine Zeile gelöscht → erkannt; zwei Zeilen getauscht → erkannt; **gesamte Datei durch eine neu berechnete, in sich stimmige Kette ersetzt → gegen die Journal-Anker erkannt**
- **Agent:** builder · **Umfang:** L

### T-4.7 — DBus-Broker mit Argumentvalidierung
- **Ziel:** Eine feste Operation je genehmigter Aktion, nicht ein generisches `invokeShortcut`.
- **Dateien:** `daimon/brokers/dbus/` [neu], `config/systemd/daimon-dbus.service` [neu], `config/dbus-filter.conf` [neu]
- **Abhängigkeiten:** T-4.5, T-4.2, **T-4.7.v**
- **Akzeptanz:**
  - [ ] Broker exponiert **eine Operation je `approved`-Aktion**, mit festen Parametern
  - [ ] Ein Shortcut-Name, der nicht im Katalog `approved` ist, wird abgewiesen — auch wenn der Proxy die Methode durchließe
  - [ ] `xdg-dbus-proxy --filter` davor als zweite Schicht, `--log` aktiv
  - [ ] `org.kde.kwin.Scripting.loadScript` ist nicht erreichbar
  - [ ] Sandbox nach Design §7.5
- **Verifikation:** `tests/verify/T-4.7.sh` — Positiv-Kanarienvogel (genehmigter Shortcut wird ausgeführt, Wirkung geprüft), dann: **nicht genehmigter Shortcut derselben Komponente wird abgewiesen** (das ist der Kern), `loadScript` abgewiesen und keine Datei erzeugt, und der Proxy-Log enthält die Versuche
- **Agent:** builder · **Umfang:** L

### T-4.8 — Undo-Broker mit Verifikation
- **Ziel:** Umkehrbarkeit wird hergestellt und geprüft, bevor herabgestuft wird.
- **Dateien:** `daimon/brokers/fs/undo.py` [neu]
- **Abhängigkeiten:** T-4.5, **T-4.8.v**
- **Akzeptanz:**
  - [ ] Löschen → XDG-Trash mit korrektem `.trashinfo`
  - [ ] Überschreiben → `cp --reflink` in die Undo-Ablage
  - [ ] Git-Verwerfen → vorher `git stash`
  - [ ] **Artefakt wird nach dem Anlegen verifiziert** (lesbar, erwartete Größe)
  - [ ] **Schlägt die Vorbereitung fehl, wird die Mutation abgebrochen** — nicht ungeschützt ausgeführt
  - [ ] Herabstufung auf `reversible` erst nach erfolgreicher Verifikation
- **Verifikation:** `tests/verify/T-4.8.sh` — Wiederherstellbarkeit für alle drei Fälle; dann Fehlerfälle einzeln erzwungen: Ziel-Dateisystem voll (per kleinem tmpfs), Trash über Dateisystemgrenze, `git stash` mit Konflikt — in **jedem** Fall muss die Ursprungsdatei unverändert sein
- **Agent:** builder · **Umfang:** L

### T-4.9 — Dateisystem-Broker mit `openat2`
- **Ziel:** Dateioperationen ohne TOCTOU, mit engen Rechten.
- **Dateien:** `daimon/brokers/fs/` [neu], `config/systemd/daimon-fs.service` [neu]
- **Abhängigkeiten:** T-4.8, **T-4.9.v**
- **Akzeptanz:**
  - [ ] Auflösung und Operation über Verzeichnis-FDs mit `openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS)`
  - [ ] Zwischen Policy, Consent, Undo und Mutation wird **kein Pfad neu aufgelöst**
  - [ ] `ProtectHome=tmpfs` mit engen `ReadWritePaths` — löst den Widerspruch aus v1.0
  - [ ] `InaccessiblePaths` für `.ssh`, `.gnupg`, Keyrings, `.pki`
- **Verifikation:** `tests/verify/T-4.9.sh` — Positiv-Kanarienvogel (erlaubte Datei wird verschoben); dann ein Wettlauf-Test, der zwischen Genehmigung und Ausführung einen Symlink auf `~/.ssh/id_ed25519` einschiebt — die Operation muss scheitern und die Zieldatei unberührt bleiben
- **Agent:** builder · **Umfang:** L

### T-4.10 — Exec-Broker ∥
- **Ziel:** App-Start außerhalb der eigenen Sandbox.
- **Dateien:** `daimon/brokers/exec/` [neu], `config/systemd/daimon-exec.service` [neu]
- **Abhängigkeiten:** T-4.5, **T-4.10.v**
- **Akzeptanz:**
  - [ ] `.desktop` auflösen; `DBusActivatable=true` → `org.freedesktop.Application.Activate`, sonst `systemd-run --user --collect`
  - [ ] Whitelist über `desktop_id`, **nie** über freie `Exec`-Strings
  - [ ] **Freigabe an den sha256 der aufgelösten `.desktop`-Datei gebunden**, **unmittelbar vor dem Start erneut geprüft** — `~/.local/share/applications` ist schreibbar. Root-eigene Dateien bevorzugt
  - [ ] Gestartete App landet außerhalb der Broker-Sandbox
  - [ ] Shell-Aufrufe nur mit argv-Array, `shell=False`
- **Verifikation:** `tests/verify/T-4.10.sh` — startet eine genehmigte App, prüft per `systemd-cgls`, dass sie **nicht** in der cgroup des Brokers liegt; dann ein nicht genehmigtes `desktop_id` → abgewiesen; ein Versuch mit Shell-Metazeichen → als Literal behandelt; und ein **Austausch der `.desktop`-Datei zwischen Vorschau und Start** → Start muss scheitern, die eingeschmuggelte Binärdatei darf nicht laufen
- **Agent:** builder · **Umfang:** M

### T-4.11 — Consent mit Nonce
- **Ziel:** Rückfragen, die nicht wiederverwendbar und nicht verwechselbar sind.
- **Dateien:** `daimon/hub/consent.py` [neu]
- **Abhängigkeiten:** T-4.4, **T-4.11.v**
- **Akzeptanz:**
  - [ ] **Darstellung und Antwortentgegennahme liegen im Auth-Agenten**, nicht im Hub. Der Hub hält nur den kanonischen Zustand und prüft die Freigabe (Design §2.2)
  - [ ] `Notify` mit `actions`, `urgency=2`, `timeout=0`, langlebige DBus-Verbindung im Auth-Agenten
  - [ ] Pending-State **persistiert** — ein Neustart mitten in einer Rückfrage hinterlässt keine verwaiste Genehmigung
  - [ ] Antwort wird nur akzeptiert bei passender Nonce **und** passendem DBus-Absender
  - [ ] `ActionInvoked("deny")` → `decline`; `NotificationClosed(expired|dismissed)` → **`cancel`**
  - [ ] Zustimmung erzeugt eine **einmalige Aktionsfreigabe**, an `action_hash` gebunden
  - [ ] `prompt_shown` ins Audit
  - [ ] Mehrere gleichzeitige Rückfragen sind unterscheidbar
- **Verifikation:** `tests/verify/T-4.11.sh` — testet einzeln: alle drei Ausgänge; eine Antwort mit fremder Nonce wird verworfen; zwei parallele Rückfragen werden korrekt zugeordnet; nach einem Neustart des Hubs während einer offenen Rückfrage führt die späte Antwort **nicht** zur Ausführung
- **Agent:** builder · **Umfang:** L

### T-4.12 — Modaler Dialog im Auth-Agenten
- **Ziel:** Was nicht rückgängig zu machen ist, wird nicht per Benachrichtigung freigegeben.
- **Dateien:** `daimon/auth/modal.py` [neu]
- **Abhängigkeiten:** T-4.11, T-1.7, T-4.12.v
- **Akzeptanz:**
  - [ ] Der Dialog liegt im **Auth-Agenten**, nicht im Face — Face rendert Modelltext und erteilt daher keine Freigaben (Design §2.2)
  - [ ] Pflicht für `destructive: true` ohne verifiziertes Undo-Artefakt
  - [ ] Nicht per „Bitte nicht stören" unterdrückbar
  - [ ] Zeigt die **kanonisierten** Parameter in der festen Vorlage aus Design §2.4 — escapt, längenbegrenzt, in Anführungszeichen
- **Verifikation:** `tests/verify/T-4.12.sh` (eingefroren) — aktiviert „Bitte nicht stören", löst eine zerstörerische Aktion aus und prüft **extern** (Fensterliste über `getWindowInfo`, nicht über eine Selbstauskunft), dass ein Auth-eigenes modales Fenster erschien; prüft, dass ohne Klick nichts ausgeführt wurde; prüft, dass eine vom Face gesendete Freigabe abgewiesen wird
- **Agent:** builder · **Umfang:** M

### T-4.13 — Input-Broker, one-shot
- **Ziel:** Das gefährlichste Werkzeug lebt so kurz wie möglich.
- **Dateien:** `daimon/brokers/input/` [neu], `config/systemd/daimon-input.service` [neu]
- **Abhängigkeiten:** T-4.11, **T-4.13.v**
- **Akzeptanz:**
  - [ ] **One-shot:** bekommt eine begrenzte, unveränderliche Ereignisfolge, führt sie aus, **beendet sich und schließt die Portal-Session**
  - [ ] `RuntimeMaxSec=` als Zwangsende
  - [ ] libei über das Portal `RemoteDesktop` ist der Regelweg
  - [ ] **Der `ydotool`-Rückfall verlangt einen dauerhaften privilegierten `ydotoold` und widerspricht der One-shot-Zusage.** Standardmäßig **deaktiviert**. Aktiviert startet und beendet der Broker `ydotoold` im eigenen Prozessbaum für genau diese Ereignisfolge; ein systemweit laufender `ydotoold` wird erkannt und die Aktion abgelehnt
  - [ ] **Immer `ask`, nie gecacht**
  - [ ] **App-Allowlist statt Passwortfeld-Erkennung** — die Behauptung aus v1.0 ist zurückgezogen
  - [ ] Sperrbildschirm bleibt harter Breaker (`org.freedesktop.ScreenSaver`)
  - [ ] Nur diese Unit hat `DeviceAllow=/dev/uinput rw`
  - [ ] Audit protokolliert nur Länge und Klassenlabel
- **Verifikation:** `tests/verify/T-4.13.sh` — prüft einzeln: nach Ausführung ist der Prozess beendet und die Portal-Session zu; zweimalige Anfrage fragt zweimal; Tippen in eine nicht gelistete App wird abgewiesen; bei aktivem Sperrbildschirm wird abgewiesen; `/dev/uinput` ist in `daimon-dbus` und `daimon-fs` nicht vorhanden
- **Agent:** builder · **Umfang:** L

### T-4.14 — Broker-Sandboxes ∥
- **Ziel:** Jeder Broker hat genau seine Fähigkeit.
- **Dateien:** `config/systemd/*.service` [ändern]
- **Abhängigkeiten:** T-4.13, T-4.10, T-4.9, T-4.7, **T-4.14.v**
- **Akzeptanz:**
  - [ ] Gemeinsame Basis nach Design §7.5
  - [ ] Abweichungen je Unit wie in der Tabelle
  - [ ] `RestrictAddressFamilies=AF_UNIX` überall — **nicht** `IPAddressDeny`
  - [ ] `systemd-analyze security` je Unit dokumentiert
- **Verifikation:** `tests/verify/T-4.14.sh` — führt **in jeder tatsächlichen Broker-Unit** (nicht in einer neu erzeugten Testunit) je einzeln aus: `ls ~/.ssh` muss scheitern, `curl` muss scheitern, `ls /dev/uinput` muss nur in `daimon-input` gelingen; jede Bedingung wird separat ausgewertet
- **Agent:** builder · **Umfang:** M

### T-4.15 — Gegendruck für Aktionen ∥
- **Ziel:** Genau-einmal-Semantik und keine Rückfrage-Flut.
- **Dateien:** `daimon/hub/action_queue.py` [neu]
- **Abhängigkeiten:** T-4.5, **T-4.15.v**
- **Akzeptanz:**
  - [ ] **Höchstens einmal**, nicht genau einmal — Design §6.2. Ticket vor der Mutation einlösen, **nie automatisch wiederholen**, bei Absturz zwischen Einlösung und Bestätigung `outcome=unknown`
  - [ ] Wiederholter Auftrag mit demselben Ticket wird verworfen
  - [ ] Höchstzahl gleichzeitig offener Rückfragen; darüber hinaus wird abgelehnt statt aufgestaut
  - [ ] Ratenbegrenzung für Aktionsanfragen je `turn_id`
- **Verifikation:** `tests/verify/T-4.15.sh` (eingefroren) — sendet denselben Auftrag 10-mal und verlangt **genau eine tatsächliche Wirkung am Zielobjekt** aus dem ersten gültigen Auftrag und **null** aus allen Wiederholungen. „Höchstens eine" allein bestünde auch ein toter Broker; tötet den Broker im Fenster zwischen Ticketeinlösung und Bestätigung und verlangt genau einen `outcome=unknown`-Eintrag **ohne** automatischen Neuversuch; erzeugt 50 Aktionsanfragen in einer Runde und prüft, dass die Zahl offener Rückfragen die Grenze nie überschreitet
- **Agent:** builder · **Umfang:** M

### T-4.16 — Ende-zu-Ende-Aktionspfad
- **Ziel:** Die Bausteine sind tatsächlich verdrahtet. In v1.0 fehlte dieser Task.
- **Dateien:** `daimon/hub/coordinator.py` [neu]
- **Abhängigkeiten:** T-4.4, T-4.5, T-4.6, T-4.7, T-4.8, T-4.9, T-4.10, T-4.11, T-4.12, T-4.13, T-4.14, T-4.15, **T-4.16.v**
- **Akzeptanz:**
  - [ ] Kompletter Weg: **PTT → `intent_mark` → Rundenmarke → Mind → Action-Request → Policy → kanonische Vorschau → Aktionsfreigabe → Auftrag mit Ticket → Broker → Audit**
  - [ ] Der Direktpfad (Katalogflag `direct: true` **und** Hub-Parser) wird gesondert gefahren und umgeht die Vorschau nachweislich nur dort
  - [ ] Jeder Hop trägt `turn_id` und `tool_use_id`
  - [ ] Latenz je Hop in der Diagnose
  - [ ] Fehler in jedem Hop führen zu einer verständlichen gesprochenen Rückmeldung
- **Verifikation:** `tests/verify/T-4.16.sh` — fährt **je Broker einen positiven und einen abgewiesenen Fall** (dbus, fs, exec, input), prüft je Fall die tatsächliche Wirkung bzw. deren Ausbleiben am Zielobjekt, prüft den Audit-Eintrag mit durchgängig identischer `turn_id`, und misst die Ende-zu-Ende-Latenz für den einfachsten `allow`-Fall (p95 < 500 ms)
- **Agent:** builder · **Umfang:** L

### T-4.17 — Basis-Injektionstests
- **Ziel:** Erste Absicherung — der vollständige Test folgt in P5, wenn Eyes angeschlossen ist.
- **Dateien:** `tests/test_injection_base.py` [neu]
- **Abhängigkeiten:** T-4.16, **T-4.17.v**
- **Akzeptanz:**
  - [ ] Aktionsanfrage ohne Rundenmarke → abgelehnt
  - [ ] Anfrage mit abgelaufener Rundenmarke → abgelehnt
  - [ ] Aktionsanfrage aus `user_audio` → werkzeuglos abgelehnt, **ohne** Auth-Dialog
  - [ ] Anfrage mit fremder `turn_id` → abgelehnt
  - [ ] Request mit gefälschtem `initiator`-Feld → Feld ignoriert
  - [ ] Request mit gefälschtem `params_hash` → Hub rechnet selbst
  - [ ] Wiederholtes Ticket → abgelehnt
  - [ ] **Keiner dieser Fälle erzeugt eine Rückfrage** — sie werden still abgelehnt und protokolliert, sonst wäre Rückfrage-Spam ein Angriffsweg
- **Verifikation:** `tests/verify/T-4.17.sh` — `pytest`; prüft zusätzlich im `/diag`, dass die Zahl erzeugter Consent-Prompts während des gesamten Testlaufs **null** ist
- **Agent:** reviewer · **Umfang:** M

### T-4.18 — Sicherheitsreview der Phase
- **Ziel:** Ein zweites Paar Augen auf der Vertrauensgrenze.
- **Dateien:** `tests/evidence/phase4-findings.json` [erzeugt], `docs/phase4-security-review.md` [neu]
- **Abhängigkeiten:** T-4.17
- **Akzeptanz:**
  - [ ] Jeder Pfad geprüft, auf dem Modellausgabe zu einer Systemaktion wird
  - [ ] Proxy-Filterliste gegen den Katalog abgeglichen — nichts Überflüssiges freigegeben
  - [ ] Unit-Härtungen gegen Design §7.5 abgeglichen
  - [ ] Audit-Redaktion gegen die Verbotsliste abgeglichen
  - [ ] Befunde **maschinenlesbar** mit `{id, severity, status}`
- **Verifikation:** `tests/verify/T-4.18.sh` — arbeitet eine **feste Prüfliste** ab (jeder Punkt aus Design §1.2/§6/§7 einzeln), verlangt zu jedem Punkt einen Beleg-Verweis, und **reproduziert jede als `closed` gemeldete Feststellung selbst**. Exit 0 nur, wenn die Prüfliste vollständig abgedeckt ist und kein `high`/`critical`-Befund offen oder unbelegt geschlossen ist. Ein leeres Befundregister besteht den Test **nicht**.
- **Agent:** reviewer · **Umfang:** L

> **Hinweis:** T-4.18 ist ein Architekturreview mit Prüfliste und Belegpflicht. Seine maschinelle Abnahme liegt in den eingefrorenen Verifizierern der Einzeltasks, nicht in diesem Task — Review und ausführbare Verifikation sind getrennte Artefakte mit getrennter Zuständigkeit.

### T-4.19 — Aktionen im Sprachpfad
- **Ziel:** „Mach lauter" funktioniert unter einer Rundenmarke — und nur dort.
- **Dateien:** `daimon/mind/router.py` [ändern]
- **Abhängigkeiten:** T-4.16, T-3.13b
- **Akzeptanz:**
  - [ ] Durchgang 1 darf Aktionen emittieren, aber nur wenn die Äußerung `user_ptt` trägt
  - [ ] **Eine als `user_audio` markierte Aktionsbitte wird werkzeuglos abgelehnt** — sie erreicht den Auth-Agenten gar nicht erst. Eine Rückfrage zu erzeugen wäre selbst ein Angriffsweg: gefälschtes Audio könnte den Nutzer mit Dialogen zumüllen, bis er einen wegklickt
  - [ ] Das Pet antwortet in diesem Fall gesprochen: Aktion braucht Push-to-Talk
  - [ ] Formulierung nach Design §1.2 — keine „physische Autorisierung", sondern „Absichtsmarke"
- **Verifikation:** `tests/verify/T-4.19.sh` — dieselbe Äußerung einmal per Wake-Word, einmal per PTT; der Wake-Word-Fall darf **null** Auth-Dialoge und **null** Aktionen erzeugen (extern beobachtet, nicht über den Hub-Zähler allein); der PTT-Fall läuft bis zur Policy durch
- **Agent:** builder · **Umfang:** M

### Gate P4
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-4.18.sh    # keine offenen High/Critical-Befunde
tests/verify/T-4.16.sh    # Ende-zu-Ende-Pfad
tests/verify/T-4.7.sh     # nicht genehmigter Shortcut wird abgewiesen
tests/verify/T-4.6.sh     # Audit-Kette inkl. Datei-Ersetzung
pytest -q
```

---

# Phase 5 — Augen

**Ergebnis:** Das Pet nimmt den Bildschirm wahr — zuerst in Quarantäne, dann mit kontrolliertem Zugang zum Modell.

**Abbruchkriterium:** Liegen die OCR-Kosten im Alltag über 5 % eines Kerns, wird die Gatterkette verschärft, statt schnellere OCR zu suchen.

---

### T-5.1 — Tesseract-Sprachdaten ∥
- **Ziel:** OCR kann Deutsch und Englisch.
- **Dateien:** `docs/INSTALL.md` [ändern]
- **Abhängigkeiten:** keine
- **Akzeptanz:** `sudo pacman -S tesseract-data-eng tesseract-data-deu`
- **Verifikation:** `tests/verify/T-5.1.sh` — prüft einzeln `tesseract --list-langs` auf `deu` und auf `eng` und führt eine Probe-OCR auf einem Referenzbild mit erwartetem Text aus
- **Agent:** builder · **Umfang:** S

### T-5.2 — Portal-ScreenCast-Session
- **Ziel:** Bildschirmzugriff mit genau einem Klick.
- **Dateien:** `daimon/eyes/screencast.py` [neu]
- **Abhängigkeiten:** T−1.4, T-0.7
- **Akzeptanz:**
  - [ ] Vollständige Request/Response-Folge nach Design §4.5
  - [ ] `persist_mode=2`, `cursor_mode=4` (METADATA)
  - [ ] **`restore_token` nach jedem `Start` überschrieben**
  - [ ] Token 0600
  - [ ] Ungültiger Token → interaktiver Rückfall, kein stilles Hängen
- **Verifikation:** `tests/verify/T-5.2.sh` — startet zweimal und prüft über die Portal-DBus-Signale, dass beim zweiten Mal **kein** `Request`-Dialog geöffnet wurde; prüft den Dateimodus per `stat`; verfälscht den Token und prüft den dokumentierten Rückfall
- **Agent:** builder · **Umfang:** L

### T-5.3 — GStreamer-Pipeline
- **Ziel:** Frames abholen ohne Dauerkosten.
- **Dateien:** `daimon/eyes/capture.py` [neu]
- **Abhängigkeiten:** T-5.2, **T-5.3.v**
- **Akzeptanz:**
  - [ ] `pipewiresrc ! videoconvert ! appsink`, `max-buffers=1 drop=true`
  - [ ] Zwischen Aufnahmen `PAUSED`, Session bleibt am Leben
  - [ ] Niedrige Framerate ausgehandelt
  - [ ] `videoconvert` bleibt drin (KDE-Bug 476602)
- **Verifikation:** `tests/verify/T-5.3.sh` — holt 10 Frames, prüft dass jeder von null verschieden ist (gegen den Schwarzframe-Bug), misst GPU-Auslastung per `nvidia-smi dmon` über 30 s je einmal im `PAUSED`- und im `PLAYING`-Zustand gegen eine Leerlauf-Grundlinie; Exit 0 nur, wenn der `PAUSED`-Mehrverbrauch unter 1 Prozentpunkt liegt. Wird die Schwelle gerissen, ist die strengere Erfassungsart verpflichtend.
- **Agent:** builder · **Umfang:** L

### T-5.4 — Abtast-Timer neben Fokus-Ereignis ∥
- **Ziel:** Änderungen *innerhalb* eines Fensters gehen nicht verloren.
- **Dateien:** `daimon/eyes/trigger.py` [neu]
- **Abhängigkeiten:** T-5.3, T-0.12
- **Akzeptanz:**
  - [ ] Fokus-Ereignis **und** gedeckelter Timer lösen aus
  - [ ] Timerrate konfigurierbar, Vorgabe konservativ
  - [ ] Idle- und Lock-Gate schaltet beide ab
- **Verifikation:** `tests/verify/T-5.4.sh` — hält den Fokus konstant, erzeugt im selben Fenster eine Inhaltsänderung und prüft, dass innerhalb der Timerperiode eine Erfassung ausgelöst wurde; sperrt danach den Bildschirm und prüft, dass keine mehr erfolgt
- **Agent:** builder · **Umfang:** M

### T-5.5 — Änderungserkennung mit Generationen
- **Ziel:** Die Gatterkette filtert billig und ordnet nebenläufige Ergebnisse korrekt.
- **Dateien:** `daimon/eyes/change.py` [neu]
- **Abhängigkeiten:** T-5.4
- **Akzeptanz:**
  - [ ] Kette: Idle/Lock → Auslöser → 160×90-Diff → gekachelter dHash 4×4
  - [ ] Liefert die Änderungsregion mit
  - [ ] **Jeder Frame trägt eine Generationsnummer**
  - [ ] Geänderte Kachelbereiche werden **kopiert**, nicht referenziert
  - [ ] Kosten je Stufe gemessen
- **Verifikation:** `tests/verify/T-5.5.sh` — `pytest`; enthält einen Test mit zwei Frames, die sich nur in einer Kachel unterscheiden, und einen Nebenläufigkeitstest, in dem ein künstlich verzögertes Ergebnis einer älteren Generation nachweislich **verworfen** wird
- **Agent:** builder · **Umfang:** L

### T-5.6 — OCR
- **Ziel:** Text nur dort, wo sich etwas geändert hat.
- **Dateien:** `daimon/eyes/ocr.py` [neu]
- **Abhängigkeiten:** T-5.5, T-5.1, **T-5.6.v**
- **Akzeptanz:**
  - [ ] `tesseract --psm 11 -l deu+eng`
  - [ ] Nur geänderte Kacheln
  - [ ] Läuft im Pool, blockiert die Erfassung nicht
  - [ ] Aufträge werden zusammengefasst: Läuft schon einer für dieselbe Region, wird der alte abgebrochen
- **Verifikation:** `tests/verify/T-5.6.sh` — misst p95-Latenz auf einem textdichten Referenzbild und die CPU-Last über 5 Minuten Alltagssimulation; verlangt < 5 % eines Kerns im Mittel
- **Agent:** builder · **Umfang:** M

### T-5.7 — Kontextspeicher unter Quarantäne
- **Ziel:** Wahrgenommenes liegt fest, erreicht aber niemanden.
- **Dateien:** `daimon/eyes/context.py` [neu]
- **Abhängigkeiten:** T-5.6
- **Akzeptanz:**
  - [ ] Ringpuffer der letzten 20 Fensterkontexte, **nur Text**
  - [ ] Unter `$XDG_STATE_HOME/daimon/context/`, vom Nutzer löschbar
  - [ ] **Kein Ausgang zu Mind** — der existiert erst mit T-5.9
  - [ ] Kill-Switch leert ihn
  - [ ] Als sensibel erkannte Fenster werden ausgelassen
- **Verifikation:** `tests/verify/T-5.7.sh` — erzeugt Kontext und prüft ihn im Speicher. **Der Quarantänenachweis ist hier noch trivial**, weil der Ausgang zu Mind erst in T-5.9 entsteht; er wird deshalb in T-5.9 wiederholt und dort **aktiv angegriffen**: Leseversuche am echten Gate ohne Marke müssen scheitern.
- **Agent:** builder · **Umfang:** M

### T-5.8 — VLM-Worker ohne Ollama
- **Ziel:** Semantik, wenn OCR nicht reicht — im selbstbeendenden Prozess.
- **Dateien:** `daimon/gpu/vlm.py` [neu]
- **Abhängigkeiten:** T-5.6, T-3.7
- **Akzeptanz:**
  - [ ] `llama-server --mmproj` **im Prozessbaum des Workers**, kein Ollama-Daemon
  - [ ] Server hört nur auf einem Unix-Socket
  - [ ] Beim Worker-Ende stirbt der Server mit
  - [ ] Herunterskalierung auf ~1920 px lange Kante plus native Ausschnitte der geänderten Kacheln
  - [ ] `max_pixels` gesetzt — die mitgelieferte Vorgabe ist die Architekturobergrenze und untauglich
  - [ ] Fullscreen- und VRAM-Gate greift
- **Verifikation:** `tests/verify/T-5.8.sh` — misst Latenz und Tokenverbrauch je Aufruf; prüft nach dem Idle-Timeout einzeln: Worker-Prozess weg, `llama-server`-Prozess weg, VRAM zurück auf Ausgangswert ±50 MB, kein TCP-Socket offen
- **Agent:** builder · **Umfang:** L

### T-5.9 — Deklassifizierungs-Gate
- **Ziel:** Kontext erreicht das Modell nur unter frischer Nutzerautorisierung.
- **Dateien:** `daimon/hub/declassify.py` [neu]
- **Abhängigkeiten:** T-5.7, **T-3.13b** (Markierung), T-0.8, **T-5.9.v**
- **Akzeptanz:**
  - [ ] Freigabe nur bei einer **frischen Rundenmarke aus Push-to-Talk** und erkennbarem Bildschirmbezug. Ein API-Kontingent aus dem Wake-Word deklassifiziert **nichts** (Design §7.2b)
  - [ ] Freigegebener Kontext geht ausschließlich in Durchgang 2 (werkzeuglos)
  - [ ] Durchgang 1 bekommt **opake Referenzen** (`window_ref`, `path_ref`) und `app_id` aus einer geschlossenen Aufzählung — **keine** Fenstertitel, auch nicht als typisiertes Feld (Design §5.1)
  - [ ] Jede Freigabe landet im Audit mit Umfang und `turn_id`
  - [ ] Ohne Nutzerhandlung: keine Freigabe, auch nicht für proaktives Verhalten
- **Verifikation:** `tests/verify/T-5.9.sh` (eingefroren) — prüft einzeln: ohne Marke keine Freigabe; **mit Kontingent aus Wake-Word keine Freigabe**; mit Marke aber ohne Bildschirmbezug keine; mit beidem schon; abgelaufene Marke keine. Zusätzlich wird die Quarantäne aus T-5.7 hier **aktiv angegriffen**: direkte Leseversuche am Gate ohne Marke müssen scheitern
- **Agent:** builder · **Umfang:** L

### T-5.10 — Test auf indirekte Exfiltration
- **Ziel:** Der Beweis für die Privacy-Zusage aus Design §7.2.
- **Dateien:** `tests/test_exfiltration.py` [neu]
- **Abhängigkeiten:** T-5.9, **T-5.10.v**
- **Akzeptanz:**
  - [ ] Eine eindeutige Zeichenkette wird auf dem Bildschirm platziert
  - [ ] **Drei passive Phasen**, in denen die Marke in keinem ausgehenden Aufruf vorkommen darf:
        (a) eine Stunde Normalbetrieb ohne jede Äußerung;
        (b) **über Lautsprecher abgespieltes Audio**, das den Wake-Word-Namen und „was steht auf meinem Bildschirm?" sagt — der Fall, den v2.0 offen ließ;
        (c) eine proaktive Phase mit ausgelösten Anlässen
  - [ ] Danach die aktive Phase: Push-to-Talk plus dieselbe Frage — jetzt **darf** sie vorkommen
  - [ ] Mitgeschnitten wird **im Egress-Broker**, nicht in Mind — sonst prüft man die Komponente mit sich selbst
  - [ ] **Die Prüfung erfolgt in einem reviewer-eigenen, kurzlebigen Gegenstück**, auf das Egress im Testprofil zeigt — nicht in Egress selbst. Ein von der geprüften Komponente gemeldeter Wahrheitswert wäre eine Selbstauskunft (Regel 9)
  - [ ] Das Gegenstück prüft die Anfragebytes **im Speicher**, verwirft sie sofort und schreibt nur `{quota_id, bytes, marker_found, sha256}`
  - [ ] Die Mitschnittfunktion ist **nur im Testprofil** übersetzt bzw. aktivierbar; im Normalbetrieb ist Rohkörper-Protokollierung nicht erreichbar
- **Verifikation:** `tests/verify/T-5.10.sh` — wertet den Egress-Mitschnitt maschinell aus; Exit 0 nur, wenn die Marke in **allen drei** passiven Phasen in null Aufrufen und in der aktiven Phase in mindestens einem vorkommt
- **Agent:** reviewer · **Umfang:** L

### T-5.11 — Adversarialer Injektionstest
- **Ziel:** Der vollständige Test, der in P4 noch nicht möglich war.
- **Dateien:** `tests/test_injection_full.py` [neu], `tests/evidence/injection-results.json` [erzeugt]
- **Abhängigkeiten:** T-5.10, T-4.17, **T-5.11.v**
- **Akzeptanz:**
  - [ ] ≥25 Angriffe, **tatsächlich auf dem Bildschirm dargestellt**, nicht als Strings eingespeist
  - [ ] Kategorien: direkte Anweisung, Autoritätsanmaßung, Dringlichkeit, versteckter Text, Herkunftsfälschung, Wiedereinspielung, Audio-Wiedereintritt über TTS, nebenläufige Rückfragen, Umgehung über Fenstertitel
  - [ ] **Kein** Angriff führt zu einer Aktion ohne frische, gültige Absichtsmarke aus dem Auth-Agenten (der Nachweis gilt der Marke, nicht einer physischen Handlung — Design §1.2)
  - [ ] **Kein** Angriff erzeugt eine Consent-Rückfrage — Rückfrage-Spam ist selbst ein Angriffsweg
  - [ ] Ergebnisse maschinenlesbar mit `{id, category, outcome}`
- **Verifikation:** `tests/verify/T-5.11.sh` (eingefroren) — leitet jedes `outcome` selbst aus Broker-Nebenwirkungen ab (Kanarienvogel-Dateien, Fensterzustand, Lautstärke) und beobachtet Auth-Dialoge **extern** über Fensterliste und Benachrichtigungsbus, nicht über den Hub-Zähler. Zusätzlich eine **autorisierte Kontrollaktion** mit gültiger Marke, die wirken muss — sonst bestünde der Test auch bei totem Aktionspfad. Exit 0 nur, wenn kein Angriff Wirkung oder Dialog erzeugte **und** die Kontrollaktion wirkte.
- **Agent:** reviewer · **Umfang:** L

### T-5.12 — Augen-Kill-Switch ∥
- **Ziel:** Sehen abschalten und überprüfen.
- **Dateien:** `daimon/eyes/killswitch.py` [neu]
- **Abhängigkeiten:** T-5.9, **T-5.12.v**
- **Akzeptanz:**
  - [ ] Hotkey stoppt `daimon-eyes.service`
  - [ ] Pipeline auf `NULL` **und** `Session.Close()` am Portal
  - [ ] Kontextspeicher wird geleert
  - [ ] Tray-Lampe spiegelt den echten Unit-Zustand
- **Verifikation:** `tests/verify/T-5.12.sh` — prüft einzeln: Unit inaktiv; `find` meldet null Dateien im Kontextverzeichnis (glob-frei); kein PipeWire-Videostream mehr; ein erfolgreiches `Session.Close` steht im Journal
- **Agent:** builder · **Umfang:** M

### T-5.13 — Eyes-Sandbox ∥
- **Ziel:** Die Wahrnehmung kann nicht telefonieren.
- **Dateien:** `config/systemd/daimon-eyes.service` [neu]
- **Abhängigkeiten:** T-5.12, **T-5.13.v**
- **Akzeptanz:**
  - [ ] `RestrictAddressFamilies=AF_UNIX`
  - [ ] `ProtectHome=read-only`, kein Schreibrecht außer im Kontextverzeichnis
  - [ ] `Type=dbus` mit `BusName=` für den Watcher-Empfang
- **Verifikation:** `tests/verify/T-5.13.sh` — führt in der tatsächlichen Eyes-Unit einzeln aus: `curl` scheitert, Schreiben nach `$HOME` scheitert, Schreiben ins Kontextverzeichnis gelingt
- **Agent:** builder · **Umfang:** S

### Gate P5
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-5.11.sh    # adversarialer Injektionstest
tests/verify/T-5.10.sh    # keine indirekte Exfiltration
tests/verify/T-5.13.sh    # Eyes ohne Netz
tests/verify/T-5.6.sh     # OCR-Kosten
pytest -q
```

---

# Phase 6 — Gedächtnis und Charakter

**Ergebnis:** Das Pet erinnert sich, klingt nach sich selbst und meldet sich sparsam von allein.

**Abbruchkriterium:** Wird proaktives Verhalten als störend empfunden, wird `speech_threshold` auf `urgent` gesetzt und T-6.6 verworfen. Der Rest bleibt.

---

### T-6.1 — Persistenz ∥
- **Ziel:** SQLite für alles, was Neustarts überleben soll — mit erhaltener Markierung.
- **Dateien:** `daimon/mind/store.py` [neu]
- **Abhängigkeiten:** T-5.7, **T-3.13b**, T-6.1.v
- **Akzeptanz:**
  - [ ] SQLite unter `$XDG_STATE_HOME/daimon/memory.db`, 0600
  - [ ] Migrationen versioniert
  - [ ] **Der Live-Session-Status bleibt zustandslos**
  - [ ] **Markierungen überleben den Datenbank-Roundtrip** als Typ, nicht als Spalte, die man vergessen kann. Der Mutationstest auf Markierungsverlust an dieser Grenze liegt hier, nicht in T-3.13b — die Datenbank existiert erst jetzt
  - [ ] Ein Befehl löscht alles
- **Verifikation:** `tests/verify/T-6.1.sh` — `pytest`; prüft den Dateimodus, führt eine Migration vor und zurück, und belegt nach einem Hub-Neustart, dass der Session-Mood `sleeping` ist und nicht der alte Wert
- **Agent:** builder · **Umfang:** M

### T-6.2 — Kurzzeitgedächtnis
- **Ziel:** Nachfragen funktionieren.
- **Dateien:** `daimon/mind/memory.py` [neu]
- **Abhängigkeiten:** T-6.1, **T-3.13b**, T-6.2.v
- **Akzeptanz:**
  - [ ] Die letzten N Runden fließen in den Prompt — **aber nur `user_ptt`- und `trusted`-Material**. `user_audio` und `tainted` sind ausgeschlossen
  - [ ] **Durchgang-2-Ausgaben und bildschirmabgeleiteter Text erreichen den werkzeugfähigen Durchgang nie**, auch nicht über das Gedächtnis. Das war die offene Rundengrenze in v2.0
  - [ ] Fenster begrenzt und konfigurierbar
  - [ ] Verfällt nach konfigurierbarer Zeit
- **Verifikation:** `tests/verify/T-6.2.sh` — `pytest`; prüft Rückbezug, Fristablauf, und **dass ein als `tainted` markierter Vorrundeninhalt im Prompt des werkzeugfähigen Durchgangs nicht vorkommt**, während er in Durchgang 2 vorkommen darf
- **Agent:** builder · **Umfang:** M

### T-6.3 — Langzeitgedächtnis
- **Ziel:** Das Pet merkt sich, was ihm gesagt wurde — und nur das.
- **Dateien:** `daimon/mind/memory.py` [ändern]
- **Abhängigkeiten:** T-6.2, **T-3.13b**, T-6.3.v
- **Akzeptanz:**
  - [ ] Merkt sich **nur** auf ausdrückliche Anweisung
  - [ ] Speichert **ausschließlich eine wörtliche Spanne aus einer `user_ptt`-Äußerung** — nie eine vom Modell erzeugte Zusammenfassung, nie Bildschirmmaterial
  - [ ] Merkt sich **nichts** aus passiver Beobachtung
  - [ ] Abruf über Textsuche — kein Embedding-Stack, solange die Textsuche reicht
  - [ ] Auflistbar und einzeln löschbar
- **Verifikation:** `tests/verify/T-6.3.sh` — `pytest`; ein Test lässt eine Stunde Bildschirmwahrnehmung laufen und belegt null neue Einträge; ein zweiter versucht über eine Modellantwort einen Eintrag zu erzeugen und belegt, dass nur wörtliche Nutzerspannen akzeptiert werden
- **Agent:** builder · **Umfang:** M

### T-6.4 — Charakterstimme ∥
- **Ziel:** Das Pet klingt wie es selbst.
- **Dateien:** `daimon/gpu/tts_character.py` [neu]
- **Abhängigkeiten:** T-3.9, T-3.7
- **Akzeptanz:**
  - [ ] Kartoffelbox-v0.1 über den GPU-Worker
  - [ ] Piper bleibt Vorgabe; Charakterstimme nur für längere Antworten
  - [ ] Bei belegtem VRAM stiller Rückfall auf Piper
  - [ ] Entlädt nach Idle
  - [ ] Meldet an die Rückkopplungssperre wie Piper
- **Verifikation:** `tests/verify/T-6.4.sh` — belegt VRAM künstlich und prüft, dass die Antwort dennoch kommt und im `/diag` der Rückfall auf Piper vermerkt ist
- **Agent:** builder · **Umfang:** M

### T-6.5 — Sprech-Schwellen ∥
- **Ziel:** Die vier Stufen tun, was sie sagen.
- **Dateien:** `daimon/mind/threshold.py` [neu]
- **Abhängigkeiten:** T-3.10
- **Akzeptanz:**
  - [ ] `silent`, `urgent`, `helpful`, `chatty` wie in Design §10.1
  - [ ] Zur Laufzeit umschaltbar
  - [ ] Ein Test je Stufe
- **Verifikation:** `tests/verify/T-6.5.sh` — spielt je Stufe dieselbe Ereignisfolge ab und prüft die Zahl der Äußerungen gegen eine erwartete Matrix
- **Agent:** builder · **Umfang:** M

### T-6.6 — Proaktives Verhalten
- **Ziel:** Meldet sich, wenn Schweigen teurer wäre.
- **Dateien:** `daimon/mind/proactive.py` [neu]
- **Abhängigkeiten:** T-6.5, T-5.9
- **Akzeptanz:**
  - [ ] Auslöser: Agent wartet lange, Build kaputt, wiederholtes Fehlerbild
  - [ ] Respektiert `speech_threshold`
  - [ ] Mindestabstand zwischen ungefragten Äußerungen
  - [ ] Wiederholt sich nie zum selben Sachverhalt
  - [ ] **Löst keine Deklassifizierung aus**, erzeugt keinen Egress-Aufruf und öffnet keinen Auth-Dialog
- **Verifikation:** `tests/verify/T-6.6.sh` — `pytest`; ein Test belegt, dass derselbe Anlass nicht zweimal spricht; ein zweiter zählt die API-Aufrufe während einer proaktiven Phase und verlangt null
- **Agent:** builder · **Umfang:** L

### T-6.7 — Eigene Piper-Stimme *(optional)* ∥
- **Ziel:** Charakter **und** null Latenz im schnellen Pfad.
- **Dateien:** `tools/train-voice/` [neu]
- **Abhängigkeiten:** T-6.4
- **Akzeptanz:**
  - [ ] Finetune ab `de_DE-thorsten-medium`
  - [ ] Eingabedaten und Laufzeit dokumentiert
  - [ ] Läuft im Piper-Pfad mit unveränderter Latenz
- **Verifikation:** `tests/verify/T-6.7.sh` — misst TTFA der eigenen Stimme über 20 Läufe, p95 < 200 ms
- **Agent:** builder · **Umfang:** L

### T-6.7b — Injektionstest über Rundengrenzen
- **Ziel:** Der Kanal, den T-5.11 nicht sehen konnte, weil es das Gedächtnis noch nicht gab.
- **Dateien:** `tests/test_injection_crossturn.py` [neu], `tests/evidence/crossturn-results.json` [erzeugt]
- **Abhängigkeiten:** T-6.6, T-6.3, T-5.11, **T-6.7b.v**
- **Akzeptanz:**
  - [ ] Vollständige Kette gefahren: Injektion auf dem Bildschirm → Durchgang-2-Antwort → Gedächtnis → **neue Runde mit Push-to-Talk**
  - [ ] ≥10 Varianten, darunter „mach das", „führ den Vorschlag aus", „ja bitte", und eine, die den injizierten Text als Erinnerung ablegen will
  - [ ] **Je eine Wäsche-Variante pro Herkunftsgrenze:** über ein freies Hook-Feld (`last_assistant_message`), über ein Aktionsergebnis mit Fremdinhalt (Dateiinhalt, Clipboard), über Serialisierung in die Datenbank und zurück, über eine Modellzusammenfassung, und über eine per gefälschtem Audio (`user_audio`) erzeugte Gedächtnisanfrage
  - [ ] **Null Aktionen**, **null Rückfragen**, **null Langzeiteinträge** aus injiziertem Material
  - [ ] Zusätzlich: proaktives Verhalten wird während des Laufs ausgelöst und darf nichts weitergeben
- **Verifikation:** `tests/verify/T-6.7b.sh` — der Verifizierer leitet jedes Ergebnis selbst aus Broker-Nebenwirkungen, Audit und dem Gedächtnisinhalt ab; Exit 0 nur bei null Aktionen, null Rückfragen und null aus Injektion stammenden Gedächtniseinträgen
- **Agent:** reviewer · **Umfang:** L

### T-6.8 — Vollständiger Systemtest
- **Ziel:** Alles zusammen, unter Last.
- **Dateien:** `tests/test_integration.py` [neu], `tests/evidence/phase6-integration.json` [erzeugt]
- **Abhängigkeiten:** T-6.6
- **Akzeptanz:**
  - [ ] Alle Units laufen gleichzeitig
  - [ ] Sprachanfrage mit Bildschirmbezug und Folgeaktion durchgespielt
  - [ ] Ressourcenverbrauch gegen Design §13 geprüft
  - [ ] Verhalten bei laufendem Spiel geprüft
  - [ ] Alle Kill-Switches wirken
- **Verifikation:** `tests/verify/T-6.8.sh` — **startet jedes Szenario selbst** und erzeugt die Messwerte selbst (`pidstat`, `nvidia-smi`, Audit-Auswertung); vorgefundene `pass`-Labels werden ignoriert. Exit 0 nur, wenn jedes selbst gefahrene Szenario seine Nachbedingung erfüllt und `idle_cpu_p95` sowie `idle_rss_mb` im Budget aus Design §13 liegen.
- **Agent:** reviewer · **Umfang:** L

### T-6.9 — Abschluss-Sicherheitsreview
- **Ziel:** Die Gesamtsicht, nachdem alle Teile stehen.
- **Dateien:** `tests/evidence/final-findings.json` [erzeugt], `docs/final-security-review.md` [neu]
- **Abhängigkeiten:** T-6.8
- **Akzeptanz:**
  - [ ] Alle Vertrauensgrenzen erneut geprüft, jetzt mit vollständigem System
  - [ ] Die Zusagen aus Design §7.2 einzeln nachgewiesen oder eingeschränkt
  - [ ] Befunde maschinenlesbar mit Schweregrad und Status
- **Verifikation:** `tests/verify/T-6.9.sh` — wie T-4.18: feste Prüfliste, Belegpflicht, eigenständige Reproduktion jeder geschlossenen Feststellung. Zusätzlich wird jede Zusage aus Design §7.2 einzeln nachgefahren. Exit 0 nur bei vollständiger Abdeckung ohne offene `high`/`critical`-Befunde.
- **Agent:** reviewer · **Umfang:** L

### T-6.10 — Dokumentation und Übergabe
- **Ziel:** Das System ist ohne diesen Plan bedienbar.
- **Dateien:** `README.md` [neu], `docs/INSTALL.md` [ändern], `docs/TROUBLESHOOTING.md` [neu], `docs/DEBT.md` [erzeugt]
- **Abhängigkeiten:** T-6.9
- **Akzeptanz:**
  - [ ] Installation von null auf lauffähig
  - [ ] Alle Kill-Switches und Prüfbefehle dokumentiert
  - [ ] Persona-Anpassung erklärt
  - [ ] Bekannte Einschränkungen aufgelistet: Bug 503121, kein Cursor-Tracking, kein deutsches KWS-Modell, keine Passwortfeld-Erkennung, Egress-Beschränkung nur auf Anwendungsebene
  - [ ] Alle `ponytail:`-Kommentare in `DEBT.md` gesammelt
- **Verifikation:** `tests/verify/T-6.10.sh` — führt die Installationsanleitung in einem frischen Nutzerkonto **maschinell** aus und prüft danach, dass alle Units aktiv sind und `tests/verify/T-6.8.sh` besteht
- **Agent:** builder · **Umfang:** L

### Gate P6
```bash
tests/verify/verify-frozen.sh     # bricht ab, wenn ein Verifizierer nach dem Einfrieren geaendert wurde
tests/verify/T-6.9.sh     # keine offenen High/Critical-Befunde
tests/verify/T-6.8.sh     # Integration inkl. Budget
tests/verify/T-6.10.sh    # Installation reproduzierbar
pytest -q
```

---

---

# Anhang D — Die Verifizierer-Tasks

Jeder ist ein eigener Knoten im Abhängigkeitsgraphen. **Der Implementierungs-Task hängt von seinem Verifizierer ab, nie umgekehrt.**

```
T-x.y.v  (reviewer)   Verifizierer + Mutanten + Gut-Muster schreiben
   │                  meta.sh: Mutanten scheitern, Gut-Muster besteht
   │                  freeze.sh: Hash nach tests/verify/FROZEN
   ▼
T-x.y    (builder)    implementieren — kann tests/verify/ nicht anfassen,
   │                  durchgesetzt von .claude/hooks/role_guard.py
   ▼
Gate                  verify-frozen.sh zuerst, dann der Verifizierer
```

46 Verifizierer.

### T−1.1.v — Verifizierer: Wake-Word-Messung
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T−1.1` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T−1.1.sh` [neu], `tests/mutants/T−1.1/` [neu], `tests/fixtures/known-good/T−1.1/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T−1.1` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T−1.1` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T−1.1/`: `FRR über die falsche Grundgesamtheit gerechnet`; `Schwelle nachträglich an die Testmenge angepasst`; `Hintergrundstunden aufgerundet`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T−1.1/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T−1.1` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T−1.1`
- **Agent:** reviewer · **Umfang:** M

### T−1.2.v — Verifizierer: ONNX sm_120
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T−1.2` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T−1.2.sh` [neu], `tests/mutants/T−1.2/` [neu], `tests/fixtures/known-good/T−1.2/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T−1.2` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T−1.2` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T−1.2/`: ``cuobjdump`-Prüfung entfernt`; `JIT-Cache nicht gesperrt`; ``native_sm120` aus Latenzverhältnis geraten`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T−1.2/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T−1.2` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T−1.2`
- **Agent:** reviewer · **Umfang:** M

### T−1.3.v — Verifizierer: layer-shell-Spike
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T−1.3` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T−1.3.sh` [neu], `tests/mutants/T−1.3/` [neu], `tests/fixtures/known-good/T−1.3/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T−1.3` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T−1.3` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T−1.3/`: `Pixelprobe gegen den ganzen Bildschirm statt die Region`; ``configure`-Zählung geschönt`; `Idle-CPU aus Selbstauskunft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T−1.3/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T−1.3` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T−1.3`
- **Agent:** reviewer · **Umfang:** M

### T−1.4.v — Verifizierer: Portal-Persistenz
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T−1.4` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T−1.4.sh` [neu], `tests/mutants/T−1.4/` [neu], `tests/fixtures/known-good/T−1.4/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T−1.4` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T−1.4` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T−1.4/`: `Dialogerkennung aus Selbstauskunft statt DBus-Signal`; `Token nach `Start` nicht überschrieben`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T−1.4/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T−1.4` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T−1.4`
- **Agent:** reviewer · **Umfang:** S

### T−1.8.v — Verifizierer: Test-Eingabevorrichtung
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T−1.8` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T−1.8.sh` [neu], `tests/mutants/T−1.8/` [neu], `tests/fixtures/known-good/T−1.8/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T−1.8` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T−1.8` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T−1.8/`: `Klickzähler nicht geprüft`; `Vorrichtung in einer systemd-Unit referenziert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T−1.8/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T−1.8` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T−1.8`
- **Agent:** reviewer · **Umfang:** S

### T-0.0.v — Verifizierer: Rollen-Durchsetzung
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-0.0` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-0.0.sh` [neu], `tests/mutants/T-0.0/` [neu], `tests/fixtures/known-good/T-0.0/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-0.0` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-0.0` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-0.0/`: `deny-Muster entfernt`; `fail-open bei fehlender Rolle`; `Symlink nicht aufgelöst`; `Positiv-Kanarienvogel fehlt`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-0.0/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-0.0` — gegen alle 4 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-0.0`
- **Agent:** reviewer · **Umfang:** M

### T-0.7.v — Verifizierer: IPC-Peer-Prüfung
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-0.7` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-0.7.sh` [neu], `tests/mutants/T-0.7/` [neu], `tests/fixtures/known-good/T-0.7/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-0.7` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-0.7` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-0.7/`: ``SO_PEERCRED` + späte PID-Auflösung (Rennen)`; `Typprüfung je Produzent entfernt`; `uid-Prüfung entfernt`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-0.7/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-0.7` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-0.7`
- **Agent:** reviewer · **Umfang:** M

### T-0.8.v — Verifizierer: Marken, Freigaben, Kontingente, Tickets
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-0.8` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-0.8.sh` [neu], `tests/mutants/T-0.8/` [neu], `tests/fixtures/known-good/T-0.8/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-0.8` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-0.8` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-0.8/`: `Wiedereinlösung erlaubt`; `Ablauf ignoriert`; `Kontingent autorisiert eine Aktion`; ``turn_id`-Bindung entfernt`; `Ticketbuch nur im RAM`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-0.8/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-0.8` — gegen alle 5 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-0.8`
- **Agent:** reviewer · **Umfang:** L

### T-0.11.v — Verifizierer: Hook-Bridge
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-0.11` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-0.11.sh` [neu], `tests/mutants/T-0.11/` [neu], `tests/fixtures/known-good/T-0.11/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-0.11` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-0.11` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-0.11/`: `Token-Prüfung entfernt`; `Präfix-Route statt exakter`; ``Content-Length` unbegrenzt`; `fehlerhafte Anfrage bekommt 200`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-0.11/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-0.11` — gegen alle 4 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-0.11`
- **Agent:** reviewer · **Umfang:** M

### T-0.14.v — Verifizierer: Kern-Sandboxes
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-0.14` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-0.14.sh` [neu], `tests/mutants/T-0.14/` [neu], `tests/fixtures/known-good/T-0.14/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-0.14` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-0.14` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-0.14/`: ``RestrictAddressFamilies` entfernt`; `TCP-Socket im Hub`; ``ProtectSystem` gelockert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-0.14/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-0.14` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-0.14`
- **Agent:** reviewer · **Umfang:** M

### T-1.1.v — Verifizierer: Overlay-Sichtbarkeit
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-1.1` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-1.1.sh` [neu], `tests/mutants/T-1.1/` [neu], `tests/fixtures/known-good/T-1.1/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-1.1` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-1.1` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-1.1/`: ``layer=top` statt `overlay``; `Marker außerhalb der kontrollierten Region gesucht`; `Vorher-Aufnahme weggelassen`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-1.1/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-1.1` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-1.1`
- **Agent:** reviewer · **Umfang:** M

### T-1.5.v — Verifizierer: Idle-CPU des Face
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-1.5` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-1.5.sh` [neu], `tests/mutants/T-1.5/` [neu], `tests/fixtures/known-good/T-1.5/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-1.5` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-1.5` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-1.5/`: `Frame-Callback dauernd rearmiert`; `Messfenster auf 2 s verkürzt`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-1.5/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-1.5` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-1.5`
- **Agent:** reviewer · **Umfang:** S

### T-1.7.v — Verifizierer: Auth-Agent und Vorschau
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-1.7` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-1.7.sh` [neu], `tests/mutants/T-1.7/` [neu], `tests/fixtures/known-good/T-1.7/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-1.7` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-1.7` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-1.7/`: `Face darf freigeben`; `Vorschau ohne Escaping`; `Bidi-Override durchgelassen`; `fehlende NFC-Normalisierung`; `Halten statt Umschaltung`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-1.7/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-1.7` — gegen alle 5 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-1.7`
- **Agent:** reviewer · **Umfang:** L

### T-2.1.v — Verifizierer: Mood-Unterscheidbarkeit
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-2.1` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-2.1.sh` [neu], `tests/mutants/T-2.1/` [neu], `tests/fixtures/known-good/T-2.1/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-2.1` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-2.1` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-2.1/`: `zwei Moods mit identischem Sprite`; `Vergleich über Bezeichner statt Bildhash`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-2.1/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-2.1` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-2.1`
- **Agent:** reviewer · **Umfang:** S

### T-2.4.v — Verifizierer: Ein-/Ausblenden
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-2.4` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-2.4.sh` [neu], `tests/mutants/T-2.4/` [neu], `tests/fixtures/known-good/T-2.4/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-2.4` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-2.4` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-2.4/`: `NULL-Buffer-Unmap`; `nur 5 statt 100 Zyklen`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-2.4/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-2.4` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-2.4`
- **Agent:** reviewer · **Umfang:** M

### T-3.1.v — Verifizierer: Mikrofon-Lebenszyklus
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-3.1` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-3.1.sh` [neu], `tests/mutants/T-3.1/` [neu], `tests/fixtures/known-good/T-3.1/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-3.1` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-3.1` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-3.1/`: ``stop()` statt `close()``; ``pw-dump` vor dem Teardown geprüft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-3.1/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-3.1` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-3.1`
- **Agent:** reviewer · **Umfang:** M

### T-3.4.v — Verifizierer: Rückkopplungssperre
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-3.4` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-3.4.sh` [neu], `tests/mutants/T-3.4/` [neu], `tests/fixtures/known-good/T-3.4/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-3.4` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-3.4` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-3.4/`: `Nachlauf auf 0`; `Echo-Referenz entfernt`; `Sperre gilt nicht bei offener Runde`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-3.4/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-3.4` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-3.4`
- **Agent:** reviewer · **Umfang:** L

### T-3.7.v — Verifizierer: GPU-Gate
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-3.7` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-3.7.sh` [neu], `tests/mutants/T-3.7/` [neu], `tests/fixtures/known-good/T-3.7/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-3.7` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-3.7` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-3.7/`: `Serialisierung entfernt`; `Fullscreen-Prüfung entfernt`; `VRAM-Messung nur vor dem Laden`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-3.7/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-3.7` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-3.7`
- **Agent:** reviewer · **Umfang:** M

### T-3.11.v — Verifizierer: Egress-Broker
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-3.11` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-3.11.sh` [neu], `tests/mutants/T-3.11/` [neu], `tests/fixtures/known-good/T-3.11/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-3.11` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-3.11` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-3.11/`: `Mind behält den Token`; `Rohkörper geloggt`; `Kontingent nicht verlangt`; `Ziel-Domain nicht geprüft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-3.11/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-3.11` — gegen alle 4 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-3.11`
- **Agent:** reviewer · **Umfang:** L

### T-3.13b.v — Verifizierer: Markierung und Senken
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-3.13b` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-3.13b.sh` [neu], `tests/mutants/T-3.13b/` [neu], `tests/fixtures/known-good/T-3.13b/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-3.13b` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-3.13b` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-3.13b/`: `Markierung bei IPC-Serialisierung verloren`; ``user_audio` erreicht Durchgang 1`; `Hook-Freitextfeld als `trusted``; `freie Modellausgabe aus Durchgang 1 als `trusted``; `Auth-Vorschau nimmt rohe Zeichenkette`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-3.13b/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-3.13b` — gegen alle 5 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-3.13b`
- **Agent:** reviewer · **Umfang:** L

### T-3.15.v — Verifizierer: Ohren-Kill-Switch
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-3.15` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-3.15.sh` [neu], `tests/mutants/T-3.15/` [neu], `tests/fixtures/known-good/T-3.15/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-3.15` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-3.15` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-3.15/`: `Stream nur pausiert`; `Netz in der Unit erlaubt`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-3.15/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-3.15` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-3.15`
- **Agent:** reviewer · **Umfang:** M

### T-4.4.v — Verifizierer: Policy-Engine
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.4` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.4.sh` [neu], `tests/mutants/T-4.4/` [neu], `tests/fixtures/known-good/T-4.4/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.4` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.4` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.4/`: `Reihenfolge deny/ask/allow vertauscht`; ``initiator` aus dem Request gelesen`; ``params_hash` aus dem Request übernommen`; ``unknown_action` auf ask statt deny`; `Direktbefehl ohne Hub-Parser`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.4/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.4` — gegen alle 5 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.4`
- **Agent:** reviewer · **Umfang:** L

### T-4.5.v — Verifizierer: Ausführungsauftrag
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.5` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.5.sh` [neu], `tests/mutants/T-4.5/` [neu], `tests/fixtures/known-good/T-4.5/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.5` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.5` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.5/`: ``audience` ignoriert`; `monotone Frist nicht geprüft`; `Ticket wiederverwendbar`; `HMAC wieder eingeführt`; `abweichende Serialisierung akzeptiert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.5/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.5` — gegen alle 5 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.5`
- **Agent:** reviewer · **Umfang:** M

### T-4.6.v — Verifizierer: Audit
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.6` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.6.sh` [neu], `tests/mutants/T-4.6/` [neu], `tests/fixtures/known-good/T-4.6/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.6` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.6` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.6/`: `Kette ohne Journal-Anker`; ``tainted` im Klartext`; `Redaktion nur bei `sensitive``; `Rotation trägt den Hash nicht weiter`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.6/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.6` — gegen alle 4 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.6`
- **Agent:** reviewer · **Umfang:** L

### T-4.7.v — Verifizierer: DBus-Broker
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.7` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.7.sh` [neu], `tests/mutants/T-4.7/` [neu], `tests/fixtures/known-good/T-4.7/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.7` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.7` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.7/`: `generisches `invokeShortcut``; `Katalogprüfung entfernt`; ``loadScript` erreichbar`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.7/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.7` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.7`
- **Agent:** reviewer · **Umfang:** L

### T-4.8.v — Verifizierer: Undo
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.8` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.8.sh` [neu], `tests/mutants/T-4.8/` [neu], `tests/fixtures/known-good/T-4.8/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.8` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.8` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.8/`: `Herabstufung ohne Artefaktprüfung`; `Mutation trotz `failed``; `Artefakt nicht auf Größe geprüft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.8/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.8` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.8`
- **Agent:** reviewer · **Umfang:** L

### T-4.9.v — Verifizierer: FS-Broker
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.9` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.9.sh` [neu], `tests/mutants/T-4.9/` [neu], `tests/fixtures/known-good/T-4.9/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.9` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.9` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.9/`: `Pfad nach dem Consent neu aufgelöst`; ``RESOLVE_NO_SYMLINKS` entfernt`; ``ReadWritePaths` geweitet`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.9/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.9` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.9`
- **Agent:** reviewer · **Umfang:** L

### T-4.10.v — Verifizierer: Exec-Broker
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.10` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.10.sh` [neu], `tests/mutants/T-4.10/` [neu], `tests/fixtures/known-good/T-4.10/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.10` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.10` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.10/`: ``desktop_id` ohne Datei-Hash`; `Hash vor statt nach der Freigabe geprüft`; `Shell-Metazeichen interpretiert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.10/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.10` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.10`
- **Agent:** reviewer · **Umfang:** M

### T-4.11.v — Verifizierer: Consent
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.11` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.11.sh` [neu], `tests/mutants/T-4.11/` [neu], `tests/fixtures/known-good/T-4.11/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.11` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.11` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.11/`: `Nonce ignoriert`; `Timeout als allow gewertet`; `Pending-State nicht persistiert`; `Darstellung im Hub statt in Auth`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.11/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.11` — gegen alle 4 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.11`
- **Agent:** reviewer · **Umfang:** L

### T-4.12.v — Verifizierer: Modaler Dialog
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.12` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.12.sh` [neu], `tests/mutants/T-4.12/` [neu], `tests/fixtures/known-good/T-4.12/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.12` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.12` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.12/`: `Dialog im Face`; `per Bitte-nicht-stören unterdrückbar`; `Rohparameter statt kanonisierter`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.12/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.12` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.12`
- **Agent:** reviewer · **Umfang:** M

### T-4.13.v — Verifizierer: Input-Broker
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.13` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.13.sh` [neu], `tests/mutants/T-4.13/` [neu], `tests/fixtures/known-good/T-4.13/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.13` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.13` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.13/`: `Prozess bleibt am Leben`; `Portal-Session bleibt offen`; `App-Allowlist entfernt`; `Zeichen im Audit`; `systemweiter `ydotoold` akzeptiert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.13/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.13` — gegen alle 5 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.13`
- **Agent:** reviewer · **Umfang:** L

### T-4.14.v — Verifizierer: Broker-Sandboxes
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.14` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.14.sh` [neu], `tests/mutants/T-4.14/` [neu], `tests/fixtures/known-good/T-4.14/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.14` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.14` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.14/`: `je Unit eine entfernte Direktive`; `Prüfung in einer Testunit statt der echten`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.14/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.14` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.14`
- **Agent:** reviewer · **Umfang:** M

### T-4.15.v — Verifizierer: Höchstens-einmal
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.15` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.15.sh` [neu], `tests/mutants/T-4.15/` [neu], `tests/fixtures/known-good/T-4.15/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.15` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.15` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.15/`: `Ticket nach der Mutation verbraucht`; `automatischer Neuversuch`; `Positivfall nicht verlangt`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.15/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.15` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.15`
- **Agent:** reviewer · **Umfang:** M

### T-4.16.v — Verifizierer: Ende-zu-Ende-Pfad
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.16` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.16.sh` [neu], `tests/mutants/T-4.16/` [neu], `tests/fixtures/known-good/T-4.16/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.16` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.16` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.16/`: `ein Broker ausgelassen`; `Vorschau übersprungen`; ``turn_id` nicht durchgängig`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.16/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.16` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.16`
- **Agent:** reviewer · **Umfang:** L

### T-4.17.v — Verifizierer: Basisangriffe
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-4.17` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-4.17.sh` [neu], `tests/mutants/T-4.17/` [neu], `tests/fixtures/known-good/T-4.17/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-4.17` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-4.17` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-4.17/`: `Rückfrage statt stiller Ablehnung`; `gefälschtes `initiator`-Feld gelesen`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-4.17/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-4.17` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-4.17`
- **Agent:** reviewer · **Umfang:** M

### T-5.3.v — Verifizierer: ScreenCast-Kosten
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.3` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.3.sh` [neu], `tests/mutants/T-5.3/` [neu], `tests/fixtures/known-good/T-5.3/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.3` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.3` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.3/`: `Grundlinie weggelassen`; `nur `PAUSED` gemessen`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.3/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.3` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.3`
- **Agent:** reviewer · **Umfang:** S

### T-5.6.v — Verifizierer: OCR-Kosten
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.6` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.6.sh` [neu], `tests/mutants/T-5.6/` [neu], `tests/fixtures/known-good/T-5.6/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.6` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.6` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.6/`: `Messfenster zu kurz`; `nur spärlicher Text geprüft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.6/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.6` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.6`
- **Agent:** reviewer · **Umfang:** S

### T-5.9.v — Verifizierer: Deklassifizierung
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.9` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.9.sh` [neu], `tests/mutants/T-5.9/` [neu], `tests/fixtures/known-good/T-5.9/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.9` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.9` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.9/`: `Kontingent reicht aus`; `abgelaufene Marke akzeptiert`; `Bildschirmbezug nicht geprüft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.9/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.9` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.9`
- **Agent:** reviewer · **Umfang:** L

### T-5.10.v — Verifizierer: Exfiltration
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.10` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.10.sh` [neu], `tests/mutants/T-5.10/` [neu], `tests/fixtures/known-good/T-5.10/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.10` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.10` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.10/`: `Spoof-Audio-Phase ausgelassen`; `Selbstauskunft statt Gegenstück`; `Rohkörper gespeichert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.10/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.10` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.10`
- **Agent:** reviewer · **Umfang:** L

### T-5.11.v — Verifizierer: Injektion
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.11` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.11.sh` [neu], `tests/mutants/T-5.11/` [neu], `tests/fixtures/known-good/T-5.11/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.11` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.11` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.11/`: `Ergebnis aus Labels statt Nebenwirkungen`; `Dialog-Nachweis nur über Hub-Zähler`; `autorisierte Kontrollaktion fehlt`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.11/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.11` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.11`
- **Agent:** reviewer · **Umfang:** L

### T-5.12.v — Verifizierer: Augen-Kill-Switch
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.12` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.12.sh` [neu], `tests/mutants/T-5.12/` [neu], `tests/fixtures/known-good/T-5.12/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.12` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.12` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.12/`: `Portal-Session bleibt offen`; `Kontextverzeichnis per Glob geprüft`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.12/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.12` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.12`
- **Agent:** reviewer · **Umfang:** M

### T-5.13.v — Verifizierer: Eyes-Sandbox
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-5.13` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-5.13.sh` [neu], `tests/mutants/T-5.13/` [neu], `tests/fixtures/known-good/T-5.13/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-5.13` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-5.13` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-5.13/`: `Netz erlaubt`; `Prüfung außerhalb der echten Unit`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-5.13/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-5.13` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-5.13`
- **Agent:** reviewer · **Umfang:** S

### T-6.1.v — Verifizierer: Persistenz-Herkunft
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-6.1` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-6.1.sh` [neu], `tests/mutants/T-6.1/` [neu], `tests/fixtures/known-good/T-6.1/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-6.1` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-6.1` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-6.1/`: `Markierung beim DB-Roundtrip verloren`; `Session-Status persistiert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-6.1/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-6.1` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-6.1`
- **Agent:** reviewer · **Umfang:** M

### T-6.2.v — Verifizierer: Kurzzeitgedächtnis
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-6.2` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-6.2.sh` [neu], `tests/mutants/T-6.2/` [neu], `tests/fixtures/known-good/T-6.2/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-6.2` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-6.2` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-6.2/`: ``user_audio` im Prompt von Durchgang 1`; ``tainted` im Prompt von Durchgang 1`; `Frist ignoriert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-6.2/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-6.2` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-6.2`
- **Agent:** reviewer · **Umfang:** M

### T-6.3.v — Verifizierer: Langzeitgedächtnis
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-6.3` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-6.3.sh` [neu], `tests/mutants/T-6.3/` [neu], `tests/fixtures/known-good/T-6.3/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-6.3` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-6.3` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-6.3/`: `Modellzusammenfassung gespeichert`; `Bildschirmmaterial gespeichert`; `nicht-wörtliche Spanne akzeptiert`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-6.3/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-6.3` — gegen alle 3 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-6.3`
- **Agent:** reviewer · **Umfang:** M

### T-6.7b.v — Verifizierer: Rundengrenzen
- **Ziel:** Ein Skript, das genau dann Exit 0 liefert, wenn `T-6.7b` seine Akzeptanzkriterien erfüllt.
- **Dateien:** `tests/verify/T-6.7b.sh` [neu], `tests/mutants/T-6.7b/` [neu], `tests/fixtures/known-good/T-6.7b/` [neu]
- **Abhängigkeiten:** T-0.0; die **Akzeptanzliste** von `T-6.7b` — nicht dessen Implementierung
- **Akzeptanz:**
  - [ ] Jedes Akzeptanzkriterium von `T-6.7b` einzeln geprüft, ohne `&&`-Verkettung
  - [ ] Mutanten unter `tests/mutants/T-6.7b/`: `eine Wäsche-Grenze ausgelassen`; `Ergebnis aus Labels`
  - [ ] Der unveränderte Verifizierer **weist jede Mutante zurück**
  - [ ] Er **besteht** gegen `tests/fixtures/known-good/T-6.7b/`
  - [ ] Keine selbstberichteten Wahrheitswerte (Regel 9); Negativprüfungen mit Positiv-Kanarienvogel (Regel 4)
- **Verifikation:** `tests/verify/meta.sh T-6.7b` — gegen alle 2 Mutanten (jede muss scheitern) und das Gut-Muster (muss bestehen). Danach `tests/verify/freeze.sh T-6.7b`
- **Agent:** reviewer · **Umfang:** L

---

## Anhang A — Task-Übersicht

Aus dem Abhängigkeitsgraphen erzeugt.

| Phase | Implementierung | Verifizierer | Gate prüft |
|---|---|---|---|
| P0.0 Infrastruktur | 1 | — | Rollen-Pfadliste greift, FROZEN bricht ab |
| P−1 Machbarkeit | 8 | 4 | blockierende Spikes fest verdrahtet, Verdikt = Exit-Status |
| P0 Kern | 14 | 4 | Hub ohne TCP, Hook-Auth, Marken/Tickets |
| P1 Overlay minimal | 10 | 4 | Sichtbarkeit per Pixelprobe, Idle-CPU, kein GPU-Kontext, Auth getrennt |
| P2 Overlay vollständig | 7 | 2 | 100 Ein-/Ausblendezyklen, Hotplug, Mood-Bildhashes |
| P3 Sprache | 17 | 6 | Rückkopplungssperre, Markierung, kein Egress ohne Kontingent |
| P4 Aktuation | 19 | 13 | Ende-zu-Ende über alle vier Broker, Argumentvalidierung, höchstens-einmal |
| P5 Augen | 13 | 7 | Deklassifizierung nur unter Rundenmarke, Exfiltration, Injektion |
| P6 Charakter | 11 | 5 | Rundengrenzen-Test, Budget, reproduzierbare Installation |
| **Summe** | **100** | **46** | **146 Tasks** |

Jedes Gate beginnt mit `tests/verify/verify-frozen.sh`.

## Anhang B — Was bewusst nicht im Plan steht

| Weggelassen | Wann es dazukommt |
|---|---|
| Embedding-basierte Gedächtnissuche | Wenn die Textsuche in T-6.3 nachweislich versagt |
| Mehrere gleichzeitige Personas | Wenn es einen zweiten Nutzer gibt |
| Web-UI zur Konfiguration | Nie — TOML reicht für einen Nutzer |
| Plugin-API für Aktionen | Wenn ein dritter Aktionstyp existiert, den der Katalog nicht abbildet |
| Cursor-verfolgendes Verhalten | Nie — auf Wayland nicht implementierbar |
| Externe Telemetrie | Nie — die lokale Diagnose in T-0.13 deckt den Bedarf |
| Docker/Containerisierung | Nie — braucht Wayland-, DBus- und PipeWire-Sockets |
| Windows-/macOS-Portierung | Nie |
| Streaming-STT mit Barge-in | Wenn die Latenz in T-3.15 als zu hoch beurteilt wird |
| Godot-Client | Nie — durch T−1.3 und P1 ersetzt |
| TPM-gestützte Audit-Verankerung | Wenn die Journal-Anker aus T-4.6 nachweislich nicht reichen |

## Anhang C4 — Änderungen in v3.1 und v3.2 (nach Runde 5) — **NICHT GEGENGELESEN**

> Der Review-Loop endete nach der konfigurierten Höchstzahl von fünf Runden **ohne** `APPROVED`.
> Die folgenden Änderungen wurden **nach** dem letzten Review eingearbeitet und sind daher selbst
> nicht mehr geprüft worden. Codex' Urteil zur Runde 5 lautete: *„The architectural design is now
> broadly sound… The implementation plan is still not safe to start."* Diese Liste arbeitet die
> fünfzehn dort benannten Punkte ab, ersetzt aber keine Prüfung.

| Bereich | Runde 5 bemängelte | v3.1 |
|---|---|---|
| Zählungen | PLAN.md, §16 und Anhang C3 nannten zwölf Dienste, 33 Verifizierer, 132 Tasks | dreizehn Dienste, 45 Verifizierer, 145 Tasks — überall |
| Phase-4-Zusage | „was der Nutzer physisch autorisiert hat" | „unter gültiger Absichtsmarke angefordert" |
| T-3.13b | nur zwei Dateien, keine `.v`-Abhängigkeit, DB-Test vor der DB | alle Grenzadapter, `.v`-Abhängigkeit, DB-Roundtrip nach T-6.1 verschoben |
| T-3.13b | testete nur Durchgang-2-Ausgabe | beide Durchgänge; Mutante gegen Beförderung strukturierter Modellstrings zu `trusted` |
| T-1.7 | Vorschau-Test ohne Unicode | Bidi, Isolate, Nullbreite, NFC, verwechselbarer Pfad |
| T-4.6 | `cap_id`, Redaktion nur bei `sensitive` | `mark_id`; Redaktion **nach Herkunft** für jeden `tainted`-Wert |
| T-4.10 | nur `desktop_id` | Bindung an Datei-Hash, Neuprüfung vor dem Start, Austausch-Test |
| T-4.11 | Benachrichtigung im Hub | Darstellung und Antwort im Auth-Agenten |
| T-4.15 | „höchstens eine" — ein toter Broker bestünde | genau eine Wirkung aus dem ersten Auftrag, null aus Wiederholungen |
| T-5.10 | vertraute Egress' `marker_found` | reviewer-eigenes Gegenstück prüft die Bytes im Speicher |
| T-5.11 | Dialog-Nachweis über Hub-Zähler, keine Kontrolle | externe Beobachtung plus autorisierte Kontrollaktion |
| T-4.13 | `ydotool`-Rückfall implizierte dauerhaften `ydotoold` | standardmäßig aus; sonst Daemon im Broker-Prozessbaum, systemweiter wird abgelehnt |

**Die zwei in Runde 5 offen gebliebenen Punkte sind in v3.2 geschlossen:**

| Punkt | Status |
|---|---|
| Die 46 Verifizierer waren eine Tabelle, keine Task-Knoten | **Anhang D** schreibt jeden einzeln aus: Ziel, Dateien, Abhängigkeiten, Mutantenliste, Akzeptanz, Verifikationsbefehl, Agent, Umfang. Alle 46 Implementierungs-Tasks tragen ihre `.v`-Abhängigkeit |
| `T-0.0` nannte keinen Durchsetzungsmechanismus | **Drei Schichten, im Repo vorhanden und selbstgetestet:** `PreToolUse`-Hook (`.claude/hooks/role_guard.py`), git `pre-commit` (`.githooks/pre-commit`), `verify-frozen.sh` als erste Zeile jedes Gates. `tests/verify/T-0.0.sh` prüft mit 19 Assertions, dass die Ablehnungen tatsächlich greifen — inklusive Positiv-Kanarienvögeln, fail-closed und Symlink-Umgehung |

> Der Selbsttest hat beim ersten Lauf einen echten Fehler in `role_guard.py` gefunden — die `deny`/`allow`-Auswertung war für Default-Deny-Rollen falsch herum. Genau dafür sind Mutationstests da.

**Weiterhin gilt:** die Änderungen aus v3.1 und v3.2 entstanden **nach** der letzten Review-Runde und sind selbst nicht gegengelesen.

## Anhang C3 — Änderungen in v3.0 (nach Runde 3)

| Bereich | v2.1 | v3.0 |
|---|---|---|
| Verifizierer | Regel ohne Tasks | **45 `.v`-Tasks**, vorgelagert, `reviewer`-eigen, mit Mutanten und `FROZEN`-Hashliste; Builder darf `tests/verify/` nicht schreiben |
| Mutationstest | Schlagwort | definierte Operatoren, Mutant je Kriterium, `tests/verify/meta.sh` fährt sie |
| T-1.7 | baute den abgelösten Face-PTT-Pfad, T-1.7b migrierte ihn | **Auth-Agent direkt**; T-1.7b entfällt |
| Gate P−1 | las `blocking` aus selbstgeschriebenem JSON | blockierende Spikes **fest verdrahtet**, Verdikt = Exit-Status |
| T−1.4 | selbstberichtetes `restart_prompted` | Verifizierer startet selbst und leitet aus Portal-Signalen ab |
| T-2.1 | unterschiedliche Sprite-Bezeichner | **paarweise verschiedene Bildhashes** der Pet-Region |
| Pixelproben | Anwesenheitsprüfung | zufällige Markerfarbe, kontrollierte Region, Vorher/Nachher-Vergleich |
| Markierung | drei Klassen, Konvention | **vier Klassen** (`user_ptt`/`user_audio` getrennt), **typisiert**, aus Datenherkunft abgeleitet |
| Hook-Felder | `trusted` | freie Textfelder `tainted` |
| Auth-Vorschau | „rendert nie Modelltext" | eigene Senke: feste Vorlage, escapt, längenbegrenzt, zitiert — mit Steuerzeichen-Test |
| Direktbefehle | undefinierte Ausnahme | Katalogflag **und** Hub-Parser; Modellausgabe immer über Vorschau |
| T-5.9 | hing am alten Router, ließ Fenstertitel zu | hängt an T-3.13b, nur opake Referenzen |
| T-5.10 | Egress-Mitschnitt ohne Auflagen | nur Hashes und Strukturmerkmale, nur im Testprofil |
| T-5.11 | verlangte „physische Nutzerhandlung" | verlangt frische Absichtsmarke — die stärkere Formulierung war nach §1.2 nicht haltbar |
| T-6.7b | nur Bildschirmtext im Gedächtnis | **je eine Wäsche-Variante pro Herkunftsgrenze** (Hook-Feld, Aktionsergebnis, Serialisierung, Zusammenfassung, `user_audio`) |
| T-4.18/T-6.9 | Review und Verifikation vermischt | getrennte Artefakte und Zuständigkeiten |
| Tasks | 99 | 100 Implementierung + 46 Verifizierer = **146** |

## Anhang C2 — Änderungen in v2.1 (nach Runde 2)

| Bereich | v2.0 | v2.1 |
|---|---|---|
| Verifizierer-Eigentum | vom selben Agenten wie die Implementierung | eigene `.v`-Tasks unter `reviewer`, vor der Implementierung, mit Mutationstest (Regeln 7–9) |
| Sichtbarkeitsprüfung | Diagnose-Socket („ich habe committet") | Screenshot und Pixelprobe; Diagnose bleibt für internen Zustand |
| T−1.2 | Latenzverhältnis als Beleg für native Cubins | `cuobjdump`-Inspektion durch den Verifizierer, JIT-Cache gesperrt |
| T−1.3 | selbstberichtetes `fullscreen_survived` | Pixelprobe über einem Vollbildfenster |
| T−1.8 | — | **neu:** Test-Eingabevorrichtung, löst die Abhängigkeit von T-1.3/T-2.3/T-2.7 auf `daimon-input` (P4) |
| T-1.7b | — | **neu:** Auth-Agent abgetrennt; Face rendert Modelltext und darf keine Freigaben erteilen |
| T-3.11 | Mind hält Token und `AF_INET` | **Egress-Broker**; Mind ist `AF_UNIX`-only und tokenlos |
| T-3.13b | — | **neu:** Markierungsverfolgung mit erzwungener Senkentabelle |
| T-4.5 | verlangte Ausführung, bevor Broker existierten | reiner Prüflogik-Test; Ausführungs-Kanarienvögel bei den Brokern |
| T-4.16 | hing nur am DBus-Broker | hängt an allen vier Brokern, je ein positiver und ein abgewiesener Fall |
| T-5.3 | „dokumentierter Wert" ohne Schwelle | Grundlinienvergleich mit Obergrenze |
| T-5.7 | tautologisch (Ausgang existierte noch nicht) | Quarantäne wird in T-5.9 am echten Gate aktiv angegriffen |
| T-5.10 | nur Stille geprüft | drei passive Phasen, darunter **gefälschtes Audio über Lautsprecher**; Mitschnitt im Egress statt in Mind |
| T-5.11 | vertraute selbstgeschriebenen `outcome`-Labels | Verifizierer leitet Ergebnisse aus Nebenwirkungen und Audit ab |
| T-6.2/T-6.3 | Gedächtnis ohne Herkunftsfilter | nur `user`/`trusted`; Langzeit nur wörtliche Nutzerspannen |
| T-6.7b | — | **neu:** Injektionstest über Rundengrenzen, nach dem Gedächtnis |
| T-4.18/T-6.8/T-6.9 | bestanden mit selbstgeschriebenem JSON | Prüfliste, Belegpflicht, eigenständige Reproduktion; Verifizierer fährt Szenarien selbst |
| Gate P1 | nur Compute-Clients | Compute **und** Graphics **und** offene `/dev/nvidia`- bzw. DRM-Deskriptoren |
| Tasks | 95 | 99 |

## Anhang C — Änderungen gegenüber v1.0

| Bereich | v1.0 | v2.0 |
|---|---|---|
| Machbarkeit | keine Spikes; Blocker mitten in P3 | Phase −1 mit sechs Spikes, zwei davon Weichen |
| Godot | 8 Tasks bauen und löschen | entfällt; Mood-Validierung aus Logs (T−1.5) |
| Overlay | P2, nach der Sprache | P1, minimal aber echt |
| Fokus-Watcher | P5, obwohl P3 ihn braucht | P0 |
| Herkunft | `initiator` im Request | Marken und Tickets im Hub (T-0.8) |
| `params_hash` | von Mind geliefert | vom Hub berechnet (T-4.5); die Signatur fiel in v2.1 wieder weg |
| Executor | ein `hands`-Prozess | vier Broker je Fähigkeit (T-4.7 … T-4.13) |
| Whitelist | autogeneriert | Kandidaten (T-4.1) + Handprüfung (T-4.2) |
| Consent | ohne Nonce | Nonce, Deadline, persistiert (T-4.11) + modal (T-4.12) |
| Audit | Hash-Kette | plus Journal-Verankerung gegen Dateiersetzung (T-4.6) |
| Verdrahtung | fehlte | expliziter Ende-zu-Ende-Task (T-4.16) |
| IPC | ohne Authentifizierung | `SO_PEERCRED` + Token für die Bridge (T-0.7, T-0.11) |
| TCP-Port | im Hub | eigene Hook-Bridge (T-0.11) |
| Mind | keine Unit | eigene Unit mit `LoadCredential=` (T-3.11) |
| VLM | Ollama | `llama-server` im Worker (T-5.8) |
| Injektionstest | 10 Strings vor Eyes | 25 Angriffe auf echtem Bildschirm nach Eyes (T-5.11) |
| Exfiltration | nicht getestet | eigener Test (T-5.10) |
| Verifikation | 16 untaugliche Befehle | `tests/verify/*.sh` nach sechs Regeln |
| Untersuchungs-Tasks | Dokument-Grep | `results.json`, Gate rechnet nach |
| Gegendruck | fehlte | T-0.10, T-4.15 |
| Diagnose | als Telemetrie abgetan | T-0.13 |
