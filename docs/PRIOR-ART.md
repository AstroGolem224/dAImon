# Prior Art — was wir übernehmen, lesen oder meiden

Stand 2026-07-27. Metadaten live über die GitHub-API geholt. `[U]` = unverifiziert.

Der Zweck ist nicht Vollständigkeit, sondern eine Entscheidung je Projekt: **VENDOR** (als Abhängigkeit nutzen), **FORK** (Code übernehmen und divergieren), **READ** (studieren, selbst schreiben), **AVOID**.

---

## Kurzfassung

**Nichts macht den Bau überflüssig.** Aber drei Funde ändern den Plan spürbar, und zwei korrigieren das Design.

| Fund | Wirkung |
|---|---|
| **`agent-pet`** — Rust, SCTK, layer-shell, MIT, drei Wochen alt | Deckt **40–50 % von Phase 1** und teilt unsere Architektur. Phase 1 startet als Fork, nicht als `cargo new`. |
| **`sherpa-onnx`** deckt KWS **und** VAD **und** STT **und** TTS unter Apache-2.0 | **Piper wird nicht als Bibliothek gebraucht.** Damit entfällt die GPL-3.0-Frage aus Design §8.2 vollständig — Piper-Stimmen laufen über den VITS-Pfad. |
| **ScreenCast v6: Node-ID ist veraltet** | **Korrektur an Design §4.5.** IDs werden nach Node-Zerstörung wiederverwendet; Hotplug und Auflösungswechsel routen den Stream still falsch. Ziel muss `pipewire-serial` über `PW_KEY_TARGET_OBJECT` sein. |

Dazu eine kleinere Korrektur: **KWin 6 hat `activateWindow()` aus dem Workspace-Wrapper entfernt** — Fenster lassen sich anheben, aber nicht fokussieren. Betrifft den Aktionskatalog.

---

## Was wir übernehmen

| Projekt | Lizenz | Rolle |
|---|---|---|
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Apache-2.0 · 13,8k★ | KWS, VAD, STT, TTS in **einer** Abhängigkeit |
| [Smithay/client-toolkit](https://github.com/Smithay/client-toolkit) | MIT · 427★ | layer-shell-Basis, wie geplant |
| [waycrate/exwlshelleventloop](https://github.com/waycrate/exwlshelleventloop) | MIT · 145★ | `layershellev` — winit-artige Schleife über SCTK, spart Delegate-Boilerplate |
| [samschott/desktop-notifier](https://github.com/samschott/desktop-notifier) | MIT · 153★ | Async-Benachrichtigungen mit Aktionsknöpfen und Rückrufen — unsere Consent-Primitive |
| [OHF-Voice/wyoming](https://github.com/OHF-Voice/wyoming) | MIT · 381★ | Drahtformat zwischen den Audiostufen, statt eines eigenen |
| [KoljaB/RealtimeTTS](https://github.com/KoljaB/RealtimeTTS) | MIT · 4k★ | unterbrechbare Streaming-Ausgabe, falls sherpa-onnx' TTS-Pfad zu starr ist |

### Fork-Kandidaten

**[MasonRhodesDev/agent-pet](https://github.com/MasonRhodesDev/agent-pet)** — Rust, MIT, 0★, ~35 Commits, Stand 2026-07-27.

Ein layer-shell-Maskottchen, dessen Animation den Zustand von Coding-Agenten spiegelt, getrieben von einem DBus-Daemon mit Ereignis-Zustandsautomat. Crates: `pet-proto`, `pet-core`, `pet-adapters`, `pet-cli`, `pet-daemon`, `pet-render`. Adapter normalisieren Claude-Code- und Codex-Hooks; der Automat arbitriert parallele Sessions nach Priorität — `needs-input > blocked > ready > running`. **Das ist unser Mood-Mapping, unabhängig zur selben Lösung gekommen.** Sprites über `pet.json` + Spritesheet.

Zu übernehmen: Ereignisschema, Prioritätsautomat, Crate-Schnitt, SCTK-Aufsetzung, und vor allem `pet-render` als Keim für unser CPU-Blitting. Zu ersetzen: der Daemon (deren Stack ist durchgehend Rust, unserer hat einen Python-Hub).

Kein Nachteil beim Forken: null Nutzer, keine konkurrierende Richtung. Upstreaming wäre denkbar.

> **Korrektur:** In der ersten Fassung stand hier `@open-pets/pet-format` als Manifest-Schema. Beim Blick in den Quelltext: **Das Paket ist ein 5-Zeilen-Stub**, der einen Typmarker und eine Zeichenkette exportiert; sein eigenes `codemap.md` nennt es „a static marker package". Es gibt keine veröffentlichte Formatspezifikation. Wer das zitiert, zitiert nichts.

Die tiefe Durchsicht beider Projekte steht unten in einem eigenen Abschnitt.

**[rhasspy/wyoming-satellite](https://github.com/rhasspy/wyoming-satellite)** — MIT, 1,2k★, **archiviert**. Die Referenzschleife Wake → Stream → Transkript, inklusive Refraktärzeit nach der Erkennung. Klein und permissiv; archiviert heißt kein Upstream zum Nachziehen.

---

## Was wir lesen, aber nicht kopieren

**[CluelessCatBurger/wl_shimeji](https://github.com/CluelessCatBurger/wl_shimeji)** — C, **GPL-2.0**, 189★.

Die beste Referenz für exakt unsere Overlay-Technik: bildschirmfüllende transparente layer-shell-Surface, `wl_subsurface` je Maskottchen, **geteilte Prototyp-`wl_shm`-Puffer** über Instanzen hinweg. Dazu ein `kwinsupport`-Plugin, das Fenstererkennung und Zeigerverfolgung über KWin-DBus und -Scripting macht — genau das Loch, das Wayland uns lässt.

Copyleft: Designtransfer, kein Codetransfer. Aber es entschärft das Overlay-Risiko erheblich, weil dort schon steht, was wir im ersten Anlauf falsch machen würden.

**[rullerzhou-afk/clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk)** — JS/Electron, **AGPL-3.0**, 5,7k★.

Der Platzhirsch der Kategorie. Bemerkenswert für uns: Es bindet Claude Code über **HTTP-Permission-Hooks** an — dieselbe Idee wie unser Consent-Gate, nur über den Hook-Kanal. Linux-Unterstützung ist Electron auf Ubuntu; Wayland/KDE-Verhalten `[U]`, vermutlich XWayland. AGPL plus separat lizenzierte Assets: nicht anfassen, nur lesen.

**Weitere Lektüre:** [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) (Lizenz unklar `[U]`) hat die ausgereifteste offene Umsetzung von Barge-in und Sprecherwechsel — der schwierigste Teil unserer Sprachpipeline. [manthanabc/polkit-agent](https://github.com/manthanabc/polkit-agent) zeigt einen modalen Autorisierungsdialog **auf der Overlay-Ebene**, strukturell unser Auth-Agent. [kurojs/Gemini-Kchat](https://github.com/kurojs/Gemini-Kchat) (LGPL-2.1) ist der einzige KDE-native Fund mit Funktionsaufrufen und Dateioperationen.

---

## Was wir meiden

| Projekt | Grund |
|---|---|
| `OHF-Voice/piper1-gpl` **als Bibliothek** | GPL-3.0. Stattdessen sherpa-onnx' VITS-Pfad mit Piper-Stimmgewichten |
| `waydeerwm/layer-shika` | AGPL-3.0 auf einer UI-Bibliothek, dazu Slint im Schlepptau |
| `wmww/gtk4-layer-shell` | Widerspricht der Entscheidung gegen einen GPU-Kontext |
| `winit` | Immer noch kein layer-shell (Issue #2582) |
| `bytedance/UI-TARS-desktop` als Executor | PyAutoGUI funktioniert auf Wayland nicht |
| `bytebot`, `rhasspy3`, `Shijima-Qt` | Archiviert |
| `leon-ai/leon`, `toverainc/willow` | Monolith bzw. ESP32-Fokus |
| `jihe520/Agentic-Desktop-Pet`, `dhruvkumar1805/nekopet` | **Keine Lizenzdatei** = alle Rechte vorbehalten |
| `felixgr/pytaint` | Patcht CPython. Tot. Markierung geht bei Serialisierung verloren — genau unser Problem |

---

## Zwei Projekte im Detail

Beide auf Zuruf tief durchgesehen. Ergebnis: **beide READ, keines FORK** — keines hat eine Zeile Wayland-Surface-Code. Die Fensterebene beider überlebt den Kontakt mit Wayland nicht.

### rainnoon/oc-claw — Rust/Tauri, 335★, 527 Commits

Ein-Autor-Projekt, aber ein ausgeliefertes Produkt (v1.8.6, Product Hunt, eigene Website). `lib.rs` hat **17.401 Zeilen in einer Datei**.

**Lizenzfalle:** GitHub meldet `NOASSERTION`, und zu Recht. Die `LICENSE` hat drei Abschnitte: Code ist MIT, **aber alle Sprites, GIFs und Hintergründe sind KI-generiert und ausdrücklich nicht-kommerziell**. Die `Cargo.toml` behauptet pauschal `license = "MIT"` — das ist ein Metadatenfehler, keine Rechteeinräumung. Code darf man nehmen, **kein einziges Bild**.

**Plattform:** macOS und Windows. 75 `cfg(target_os="macos")`-Blöcke, 85 für Windows, **zwei** für Linux — beide nur `xdg-open`. Null Treffer für „wayland", „x11", „layer-shell". Das README ist ehrlich, es verspricht Linux gar nicht erst.

**Was wir mitnehmen:** die **Hook-Integration**. Neun Events statt unserer sieben, mit `PreCompact` und `SessionEnd`; Payload je Werkzeug beschnitten; die PID von Claude Code mitgeschickt, damit der Daemon ein Strg-C erkennt; ein Subagenten-Zähler, der „fertig" erst meldet, wenn alle durch sind. Dazu die **blockierende Zustimmungs-Rückreise über einen Unix-Socket** — der Hook wartet, der Daemon antwortet — mit vier Entscheidungen: ablehnen, einmal erlauben, für die Sitzung je Werkzeug erlauben, Modus umschalten. Das ist unser Consent-Dialog, und es beweist, dass die Latenz im Alltag trägt.

Ihr Socket liegt in `/tmp` und ist damit weltweit beschreibbar. Unserer gehört nach `$XDG_RUNTIME_DIR` — einer der wenigen Punkte, an denen wir es besser machen als die Vorlage.

Außerdem brauchbar: ein **SAD-Algorithmus zur Bildregistrierung** (`spriteUtils.ts`), der KI-generierte Spritesheets entzittert. Rund 60 Zeilen, MIT, an einem Nachmittag nach Rust portiert.

### alvinunreal/openpets — TypeScript/Electron, MIT, 970★

Sauber MIT, eine Lizenzdatei, konsistent über alle `package.json`. 278 Commits, aktiv, 51 Testdateien, ausführbare Vertragstests, ordentliche Dokumentation. Kein Boilerplate — das ist ernsthaft gebaut, mit ungewöhnlich disziplinierter Eingabevalidierung.

**Der wichtigste Fund ist ein negativer.** Sie haben Wayland versucht und aufgegeben, und es ehrlich dokumentiert:

> *„Native Wayland deliberately forbids clients from positioning or restacking their own toplevels, so under a native Wayland backend every position write is silently ignored… gravity, walkabout, follow-cursor, cross-display roaming, drag, and z-order all become no-ops."*

Ihre Lösung ist, Ozone auf XWayland zu zwingen. Zusätzlich: Auf Linux funktioniert die Weiterleitung von Mausereignissen gar nicht, weshalb ihre Pet-Fenster dort Klicks schlucken — genau das Problem, das unsere Input-Region auf die Bounding-Box löst.

**Das ist die empirische Begründung für layer-shell.** Ein gut gebautes Electron-Projekt ist exakt gegen die Wand gelaufen, um die herum unser Entwurf konstruiert ist.

**Was wir mitnehmen — vier Dinge:**

1. **Der Sprech-Validator** (`packages/agent-events`, 23 Zeilen). Kuratierte Textpools für ungefragte Äußerungen, plus eine Prüfung, die mehrzeiligen Text, Überlänge, Code, URLs, Pfadförmiges und Geheimnis-Zuweisungen ablehnt. **Das schließt eine echte Lücke bei uns** — unsere Senkentabelle ließ markiertes Material uneingeschränkt in die Sprachausgabe.
2. **Gestenfenster** (`userCommandDepth`). Eine Fähigkeit ist erteilt **und** nur innerhalb zwei Sekunden nach einer expliziten Nutzerhandlung nutzbar. Clipboard-Lesen geht dort nur innerhalb eines nutzerausgelösten Kommandos. Enger als unsere Rundenmarke und genau richtig für Clipboard, Deklassifizierung und synthetische Eingabe.
3. **Zustimmung nur bei Zuwachs.** Ein Update, das eine Berechtigung oder einen Netz-Host hinzufügt, fragt erneut; eines, das nichts erweitert, fragt nicht. Verhindert Zustimmungsmüdigkeit.
4. **Zwei unabhängige Tore.** Neben der Einzelerteilung gibt es globale Schalter, die unabhängig davon standardmäßig **aus** sind — Mikrofon, dynamische Sprache — plus ein Ruhezeitfenster, das jeder Ausgabekanal abfragt.

Dazu das **Lease-Modell**: Erste Sitzung öffnet das Pet, letzte schließt es, PID-Prüfung räumt binnen Sekunden auf, eine Nonce schützt gegen PID-Wiederverwendung. Unser bisheriger Stunden-TTL hätte das Pet nach einem Strg-C bis zu einer Stunde auf `needs_input` hängen lassen.

**Ihr Berechtigungsmodell** hat 32 Namen und eine „sensitive" Teilmenge; durchgesetzt wird an genau einer Stelle im SDK-Bau, indem die erteilten Rechte mit dem Manifest geschnitten werden und jede Fähigkeit oben einen `requirePermission`-Aufruf trägt. Nicht erteilte Methoden fehlen im übergebenen Objekt schlicht. Bekannte Lücke dort: keine Laufzeit-Nachfrage und kein Widerruf außer „Plugin abschalten".

### Das gemeinsame Sprite-Format

Beide Projekte verwenden **unabhängig voneinander dasselbe Atlas-Layout**, das aus `openai/skills` hatch-pet stammt:

```
Zelle 192×208, 8 Spalten, 9 Zeilen
Zeile 0 idle (6)        Zeile 3 waving (4)     Zeile 6 waiting (6)
Zeile 1 run-right (8)   Zeile 4 jumping (5)    Zeile 7 running (6)
Zeile 2 run-left (8)    Zeile 5 failed (8)     Zeile 8 review (6)
```

Manifest: `id`, `displayName`, `description`, `spritesheetPath`. Zwei ausgelieferte Projekte, byteidentisches Raster — das ist ein De-facto-Standard, und für einen `wl_shm`-Blit ist es ein Festschritt-Kopieren ohne jede Laufzeit. Wir übernehmen ihn, damit vorhandene Community-Pets unverändert laufen.

**Eine Abweichung mit Absicht:** Beide kodieren die Zeilentabelle fest im Host. Bei uns steht sie im Manifest, mit obigem Layout als Vorgabe. Sonst kann kein Pet je ein abweichendes Raster mitbringen.

Von `openpets` außerdem die Trennung **Reaktion → Sprite-Zustand**: Der Hub sagt, was gemeint ist (`thinking`, `working`, `waiting`, `error`), das Face bildet über eine überschreibbare Tabelle ab, wie es aussieht. Das ist exakt unsere Hub-/Face-Grenze.

---

## Wo es nichts gibt

Vier Stellen mit echtem Baukostenrisiko, weil keine Vorlage existiert:

1. **ScreenCast-`restore_token` plus PipeWire-Serial-Targeting.** Keine gepflegte Bibliothek, weder Python noch Rust. Die kanonische Referenz ist ein Rohskript in [xdg-desktop-portal #1371](https://github.com/flatpak/xdg-desktop-portal/issues/1371) — und dort ist ein Fehler dokumentiert, bei dem die Wiederherstellung den falschen Monitor liefert.
2. **KWin-6.7-Scripting-Brücke.** Keine Bibliothek. Die Umstellung von QScriptEngine auf QJSEngine hat Plasma-5-Skripte ungültig gemacht.
3. **Herkunftsmarkierung in Python.** Es gibt nichts Produktionstaugliches — bestätigt unsere Entscheidung für einen engen `Tainted[str]`-Typ an der Aktuationsgrenze. Automatische Propagation in Python ist ein ungelöstes Problem, keine fehlende Bibliothek.
4. **Ein typisierter, whitelistgeprüfter, zustimmungspflichtiger DBus-Aktionskatalog.** Das gesamte Computer-Use-Feld löst Sicherheit über **Eindämmung** (VM, Container) oder über ein pauschales Ja/Nein auf undurchsichtige Codeblöcke. Niemand hat einen geprüften Aktionskatalog. Wenn dAImon einen liefert, wäre das neu.

Zu Punkt 4 gibt es eine gute akademische Begründung: Ein `click(450,320)` ist semantisch undurchsichtig und deshalb nicht sinnvoll zustimmungsfähig. Das stützt die Entscheidung, auf DBus zu gaten statt auf Pixel.

---

## Die ehrliche Antwort auf „gibt es das schon?"

Ja, ungefähr fünfzehnmal — als „Pet beobachtet deinen Coding-Agenten". Aber **jedes Projekt mit echtem Feinschliff ist Electron oder Tauri auf X11 oder macOS**, und das einzige Wayland-native ist drei Wochen alt und hat null Sterne.

Die Nische Wayland/KWin ist offen. Und der Teil, der dAImon von den fünfzehn unterscheidet — lokale Wahrnehmung mit Netzsperre, Herkunftsmarkierung, ein geprüfter Aktionskatalog mit Bestätigungsdialog — existiert nirgends.
