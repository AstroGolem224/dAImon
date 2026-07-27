# Phase 3 — Agent-Status (Linux, Anthropic-Stack)

**Ziel:** Ein Pet in der Bildschirmecke, das zuverlässig anzeigt, was deine Claude-Code-Sessions gerade tun.
**Kein LLM. Kein Screen-Watching. Kein Mikrofon.** Reine Event-Anzeige.
**Aufwand:** 3–4 Abende.

```
Claude Code ──hook (JSON auf stdin)──> curl ──POST /hook──> pet_daemon.py
                                                                  │
Godot-Client  ◀────────── GET /state alle 250 ms ─────────────────┘
```

---

## 1. Dateien

```
pet/
├── daemon/pet_daemon.py        # stdlib-only, keine Dependencies
├── config/claude-hooks.json    # in ~/.claude/settings.json einfügen
├── godot/pet_client.gd         # Overlay + Polling + Animation
└── PHASE3.md
```

---

## 2. Setup — Schritt für Schritt

### 2.1 Daemon starten
```bash
python3 pet/daemon/pet_daemon.py
# -> pet-daemon laeuft auf http://127.0.0.1:8787
```

Prüfen:
```bash
curl -s http://127.0.0.1:8787/state
# {"v":1,"rev":0,"mood":"sleeping","sessions":0,"bubble":null}
```

### 2.2 Hooks eintragen
Inhalt aus `config/claude-hooks.json` (den `hooks`-Block) nach `~/.claude/settings.json`.
Danach in Claude Code `/hooks` aufrufen und prüfen, ob alle sieben Events gelistet sind.

Ein **einziger** Befehl für alle Events — das Event-JSON auf stdin enthält `hook_event_name`, sortiert wird im Daemon. `|| true` und `-m 1` sorgen dafür, dass ein toter Daemon Claude Code niemals blockiert oder verlangsamt.

> Falls deine Claude-Code-Version HTTP-Hooks kann (`"type": "http"`), spart das den curl-Umweg. Steht als Alternative in der Config. Der curl-Weg funktioniert überall.

### 2.3 Smoke-Test ohne Godot
```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"hook_event_name":"Notification","session_id":"s1","notification_type":"permission_prompt","message":"Bash ausführen?"}' \
  http://127.0.0.1:8787/hook
curl -s http://127.0.0.1:8787/state
```
Erwartung: `"mood":"needs_input"` plus Bubble. Danach eine echte Claude-Code-Session starten und das Log des Daemons beobachten — das ist der eigentliche Test.

### 2.4 Godot-Szene
```
PetRoot (Node2D)          <- pet_client.gd
├── Sprite (AnimatedSprite2D)
├── Bubble (Label)
├── Http   (HTTPRequest)
├── Poll   (Timer, 0.25s, autostart, one_shot=off)
└── Chime  (AudioStreamPlayer)   optional
```

Projekt-Settings:
```
display/window/size/transparent                 = true
display/window/per_pixel_transparency/allowed   = true
rendering/viewport/transparent_background       = true
display/window/size/borderless                  = true
application/run/low_processor_mode              = true
```

---

## 3. Linux — der unangenehme Teil

**CachyOS läuft per Default auf Plasma Wayland. Dort darf ein Client seine Fensterposition nicht setzen.** `DisplayServer.window_set_position()` ist dann ein No-Op, das Pet landet irgendwo.

Optionen, in der Reihenfolge, in der ich sie probieren würde:

1. **XWayland erzwingen** ★
   ```bash
   godot --display-driver x11 --path pet/godot
   ```
   Als X11-Client positioniert KWin das Fenster brav. Transparenz und Always-on-top funktionieren. Zwei Minuten Aufwand, löst 95 % des Problems.

2. **MX Linux nehmen** — Xfce ist X11, dort ist gar nichts zu tun.

3. **KWin-Fensterregel** (Systemeinstellungen → Fensterverwaltung → Fensterregeln): Fensterklasse matchen, dann *Position*, *Immer im Vordergrund*, *Keine Titelleiste*, *Nicht in Fensterleiste*, *Nicht im Pager* erzwingen. Funktioniert auch nativ unter Wayland. Nachteil: statisch — Draggen wird ausgehebelt. Für Phase 3 egal, da das Pet nicht laufen muss.

4. **Eigener `wlr-layer-shell`-Client** in Rust/GTK4. Die saubere Lösung, ein bis zwei Wochen Arbeit. Erst wenn 1–3 wirklich nicht reichen.

**Empfehlung:** 1 nehmen, 3 als Rückfallebene notieren, 4 vergessen bis es weh tut.

Zusätzlich: Fenster taucht in Alt-Tab auf. Unter X11 über `_NET_WM_WINDOW_TYPE_UTILITY` lösbar, braucht aber einen Native-Shim oder eben Option 3.

---

## 4. Mood-Mapping (lebt in `State._map()`)

| Hook-Event | Mood | Bubble |
|---|---|---|
| `SessionStart` | `observing` | — |
| `UserPromptSubmit` | `thinking` | — |
| `PreToolUse` / `PostToolUse` | `working` | — |
| `Notification` + `permission_prompt` | `needs_input` ★ | ja, dringend |
| `Notification` + `idle_prompt` | `idle` | — |
| `Stop` | `done` | ja, mit `last_assistant_message` |
| `StopFailure` / `PostToolUseFailure` | `failed` | ja, dringend |
| `SessionEnd` | `sleeping` | — |

Bei mehreren parallelen Sessions gewinnt der Mood mit der höchsten Priorität — `needs_input` schlägt alles. Das ist die eigentliche Killer-Funktion: **du siehst am Rand des Blickfelds, dass ein Agent auf dich wartet, ohne ins Terminal zu schauen.**

Eskalation nach Spec §5: nur `needs_input` darf einen Ton machen. `done` bewegt sich sichtbar, aber schweigt.

---

## 5. Nötige Animationen (Minimum 8)

`idle` · `observing` · `thinking` · `working` · `happy` · `worried` · `alert` · `sleeping`

Fehlt eine, fällt der Client automatisch auf `idle` zurück — du kannst also mit **zwei** Animationen anfangen und den Rest nachliefern, ohne dass etwas kaputtgeht.

---

## 6. Bewusst weggelassen

- Attention Tracking, Idle-Gesten, Squash/Stretch → Phase 2, kommt nach dem Nutzwert-Beweis
- Persistenz — der Daemon vergisst beim Neustart alles. Für Live-Status korrekt.
- Auth auf dem HTTP-Port — localhost, Einzelnutzer, reicht.
- Reconnect-Logik — Client pollt eh, Daemon weg = Pet schläft. Ehrlicher Zustand statt Fehlermeldung.

---

## 7. Nächster Schritt

`python3 pet/daemon/pet_daemon.py` starten, Hooks eintragen, eine echte Claude-Code-Session laufen lassen und **nur das Daemon-Log anschauen**. Wenn die Event-Abfolge sich richtig anfühlt, ist das Mapping fertig — und Godot ist danach reine Fleißarbeit.

## 8. Mögliche Verbesserung

**`cwd` mitbenutzen.** Der Daemon bekommt bei jedem Event das Arbeitsverzeichnis, wirft es aktuell weg. Bei mehreren parallelen Sessions ist "welches Projekt wartet auf mich" die interessantere Information als "irgendwas wartet". Zwei Zeilen im Bubble-Text — und das Pet wird von einer Statusanzeige zu etwas, das deinen Arbeitstag tatsächlich sortiert.
