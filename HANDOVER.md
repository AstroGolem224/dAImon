# Übergabe — Stand 2026-07-31

Alles, was nicht aus dem Repo hervorgeht. Die vorige Fassung ist über zwölf Tasks
gewachsen und war nicht mehr lesbar; diese ist neu geschrieben und ersetzt sie.

---

## Wo wir stehen

| | |
|---|---|
| **Gate P−1** | 8 von 9 grün. Rot nur `T--1.12` — Messung nicht gelaufen, korrekt so |
| **Gate P0** | 11 von 11 grün |
| **Gate P1** | **4 von 5 grün.** Rot nur `T-1.10` — 5 Arbeitstage Normalbetrieb, **Uhr läuft seit 31.07.** |
| **Phase 2** | **T-2.1 bis T-2.4 stehen.** Offen: T-2.5, T-2.6 (optional), T-2.7 |

**Zehn Verifizierer sind eingefroren**, 37 existieren insgesamt.
`pytest` grün mit 4 per `xfail(strict=True)` dokumentiert roten.
`cargo test -p face` 62 von 62.

**Quelle der Planungsdokumente ist `/home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/`,
`docs/` ist die Kopie. Beide pflegen.**

---

## Was Matthias tun muss

**Nichts Blockierendes.** Zwei Dinge, wenn es passt:

1. **Gate P1 kann am 04.08. schließen.** Der Timer läuft und sammelt.
   Vorher einzutragen — kein Programm kann das messen:
   * `fehlalarme` und `ablenkungen` in `tests/evidence/phase1-usage.json`
     (stehen auf `null`, und `null` heißt „noch niemand hat hingesehen")
   * `verdict` (steht auf `pending`, was der Verifizierer ausdrücklich als **rot**
     wertet) und `docs/phase1-verdict.md`
2. **T−1.12** (NVIDIA-Sprachstack) ist weiterhin ungemessen. Werkzeug liegt unter
   `spikes/nvidia-voice/` samt `SPEC.md`. Nicht blockierend.

```bash
# T−1.4, Reboot-Teil — nach dem nächsten regulären Neustart, ein Befehl:
timeout --foreground --signal=TERM --kill-after=5s 130s python3 spikes/portal/reboot_check.py
```

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

Dazu zwei Fälle, in denen ein **Mutant** nichts bewies: einmal enthielt die
Fixture-Kopie ein gebautes `target/` der *unmutierten* Quelle, einmal brach das
Erzeugungsskript ab und der zweite Mutant blieb eine unveränderte Kopie. Beide
fielen nur auf, weil *alle* Mutanten grün waren — und das kann nicht sein.

---

## Fallen dieser Maschine

Alles gemessen, nicht vermutet.

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
- **T-1.10** braucht Kalenderzeit, siehe oben.
- **T-2.5 bis T-2.7** stehen aus. T-2.6 (Wandern zwischen Monitoren) ist laut Plan
  optional und darf entfallen, ohne die Phase zu blockieren.
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
