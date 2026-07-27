# Spike T−1.3 — wlr-layer-shell auf KWin

**Frage:** Trägt `zwlr_layer_shell_v1` auf KWin 6.7.3 als bildschirmfüllendes,
klickdurchlässiges Overlay — und ist KDE-Bug 503121 beherrschbar?

**Verdikt: pass.** Alle sechs Teilfragen sind beantwortet, keine blockiert das Design.

| Messung | Ergebnis | Beleg |
|---|---|---|
| Layer-Surface mappt | ja, `initial_configure` nach ~10 ms, 5120×1440 | `evidence/fullscreen_test.json` |
| bleibt über Vollbild sichtbar | ja, per Pixelprobe, mit Negativkontrolle | `evidence/C_fullscreen_plus_overlay.png` |
| KDE-Bug 503121 | reproduziert: 0/20 configures bei NULL-Buffer-Unmap | `evidence/cycle_test.json` |
| Umgehung | `reset_props` und `recreate` je 20/20, `reset_props` empfohlen | `evidence/cycle_test.json` |
| Idle-CPU | 0,17 % eines Kerns über 60 s | `evidence/idle_cpu_test.json` |
| GPU-Kontext | keiner — 0 DRI-fds, 0 GPU-Bibliotheken, 1 memfd (wl_shm) | `evidence/idle_cpu_test.json` |
| **`set_input_region` / Click-Through** | **funktioniert, sauber getrennt** | `evidence/input_region_test.json` |

Umgebung: CachyOS, KWin 6.7.3 / Plasma 6.7.3, Wayland, ein Output HDMI-A-1,
5120×1440, Scale 1, Geometrie 0,0. Client: Rust, `smithay-client-toolkit` 0.21,
ausschließlich `wl_shm`.

---

## 1. Der Vorfall: das Overlay hat die Maus blockiert

Beim ersten Anlauf am 2026-07-27 war der Rechner mit der Maus nicht mehr
bedienbar. Matthias musste um Hilfe bitten.

**Ursache.** `apply_regions` in `src/main.rs` rief `set_input_region` nur auf,
wenn `--input-region` übergeben wurde. Eine Wayland-Surface **ohne** gesetzte
Input-Region nimmt Eingaben auf ihrer **gesamten** Fläche entgegen. Bei einer
bildschirmfüllenden Layer-Surface auf der Overlay-Ebene heißt das: der komplette
Schirm schluckt jeden Klick, und darunter kommt nichts mehr an.

**Behoben, und so muss es bleiben:**

1. Die **leere Region ist jetzt die Vorgabe**. `apply_regions` setzt
   `set_input_region` *immer*; ohne `--input-region` bleibt die Region leer =
   vollständig klickdurchlässig. Nachgewiesen durch die Negativkontrolle in
   Abschnitt 6.
2. Ein **Watchdog im Prozess selbst**: ein Thread beendet den Prozess nach
   `SPIKE_MAX_SECS` Sekunden (Vorgabe 90) mit `exit(3)`, unabhängig davon, ob
   die Ereignisschleife hängt oder der Aufrufer abgestürzt ist. Ohne Maus wäre
   ein hängendes Overlay nur noch per TTY loszuwerden.

**Regeln für jeden weiteren Lauf des Binaries:**

* Immer `SPIKE_MAX_SECS=15` (oder weniger) setzen. Den Watchdog nie abschalten.
* Nie ohne explizites Input-Region-Argument laufen lassen — außer man testet
  bewusst die Vorgabe der leeren Region.
* Nie eine Input-Region größer als ein kleines Rechteck setzen.
* Nach jedem Lauf prüfen: `ps -eo comm | grep -c '^spike$'` muss `0` ausgeben.
* Wenn etwas komisch aussieht: sofort `pkill -x spike`.

---

## 2. Bauen

```bash
export DAIMON_ROLE=investigator
cd /home/itiger013/Dokumente/Github/dAImon/spikes/layershell
cargo build --release          # -> target/release/spike
```

Modi des Binaries:

* `spike map` — mappen, Marker zeichnen, am Leben bleiben (Tests 1, 2, 4, 5, 6)
* `spike cycles --cycle-mode {null|reset|recreate} --cycles 20` — Hide/Show-Zyklen (Test 3)

Argumente: `--color RRGGBB`, `--marker x,y,w,h`, `--input-region x,y,w,h`,
`--hold SEK`.

---

## 3. Mappen und Sichtbarkeit über Vollbild

```bash
export DAIMON_ROLE=investigator
python3 run_fullscreen_test.py
```

Aufbau: fünf Screenshots. `A` leerer Desktop, `E` nur ein Vollbildfenster in
einer extern gewürfelten Farbe, `C` Vollbild **plus** Overlay, `B` nur Overlay,
`D` nach dem Aufräumen. Geprüft wird der Pixel an der Marker-Position.

Ergebnis: `C_probe == overlay_color` (11,83,229) — das Overlay ist über dem
Vollbildfenster sichtbar. Kontrollpunkt außerhalb des Markers zeigt weiterhin
die Vollbildfarbe (211,133,50). Die Negativkontrolle `E_probe == fullscreen_color`
belegt, dass das Vollbildfenster den Punkt vorher tatsächlich verdeckt hat —
`baseline_false_positive: false`. Nach dem Aufräumen ist der Punkt wieder
Desktop (`D_probe == A_probe`).

Wichtig für das Design: **`Layer::Overlay`, nicht `Layer::Top`.** Ein
Vollbildfenster verdeckt `Top`.

Weitere Pflichteinstellungen (alle in `apply_props` / `apply_regions`):
Anchor auf allen vier Kanten, `set_size(0,0)`, `exclusive_zone = -1`,
`keyboard_interactivity = None`, `wl_output` **explizit** gebunden (nie NULL),
`opaque_region` leer.

---

## 4. KDE-Bug 503121 — Hide/Show-Zyklen

```bash
export DAIMON_ROLE=investigator
python3 run_cycle_test.py
```

20 Zyklen in drei Varianten, gemessen wird, wie viele `layer_surface.configure`
zurückkommen.

| Variante | configures | Zeit pro Zyklus | sichtbar danach |
|---|---|---|---|
| `null` — Unmap per `attach(None)`, dann Remap-Commit | **0 / 20** | 1508 ms (Timeout) | nein |
| `reset` — wie `null`, aber alle Properties vor dem Remap neu setzen | **20 / 20** | 10 ms | ja |
| `recreate` — Layer-Surface zerstören und neu erzeugen | **20 / 20** | ~10 ms | ja |

Der Bug ist damit reproduziert: nach einem NULL-Buffer-Unmap schickt KWin nie
wieder ein `configure`. Die Surface bleibt tot, ohne dass ein `closed` kommt.

**Empfehlung: `reset_props`.** Vor dem Remap-Commit `apply_props()` und
`apply_regions()` erneut aufrufen. Das ist billiger als die Surface wegzuwerfen
und neu aufzubauen, und es funktioniert genauso zuverlässig.

Falle beim Nachbauen: nach einem ausbleibenden `configure` darf man **keinen**
Buffer anhängen — das ist ein Protokollfehler
(`buffer attached prior to the first layer_surface.configure`) und killt die
Verbindung. Der Testcode zählt in dem Fall nur weiter.

---

## 5. Idle-CPU und GPU-Kontext

```bash
export DAIMON_ROLE=investigator
python3 run_idle_cpu_test.py
```

Gemessen extern über `/proc/<pid>/stat` und `top -b` (`pidstat` ist auf dieser
Maschine nicht installiert), 60 s.

* **0,1667 %** eines Kerns (10 Ticks bei `CLK_TCK=100` über 60,0 s). `top` im
  Mittel 0,164 %, Spitze 1,0 %.
* `dri_fds: []` — **kein einziger** offener `/dev/dri/*`-Deskriptor.
* `gpu_libs: []` — kein libEGL, libGL, libvulkan, libgbm gemappt.
* Genau **ein** memfd: `/memfd:smithay-client-toolkit` — der `wl_shm`-Pool.

Bedingung für die niedrige Idle-Last: der Frame-Callback wird **one-shot**
benutzt. `draw()` fordert bewusst keinen neuen `frame()`-Callback an; nur wenn
`dirty` gesetzt ist, wird neu armiert. Der Hauptthread hängt sonst in
`blocking_dispatch` und damit in `poll()`.

Damit ist Design §8.1 empirisch bestätigt: das Overlay kostet praktisch nichts
und öffnet keinen GPU-Kontext, der mit dem Spiel oder anderen Anwendungen um
die RTX 5090 konkurrieren würde.

---

## 6. `set_input_region` — Click-Through (die offene Frage, jetzt beantwortet)

```bash
export DAIMON_ROLE=investigator
python3 run_input_region_test.py
```

**Ergebnis: die Input-Region funktioniert, und zwar exakt so, wie Design §8.1 es
braucht.**

Aufbau: darunter ein bildschirmfüllendes, einfarbiges XWayland-Fenster
(`below_window.py`), das jeden Klick **und** jede Bewegung mit Wurzelkoordinaten
protokolliert. Darüber das Overlay mit `set_input_region` auf ein 200×200-Rechteck.

| Kriterium | Ergebnis |
|---|---|
| Overlay bekommt `pointer_enter` innen | ja, `@(1491.8, 699.9)` |
| Overlay bekommt `pointer_motion` innen | ja |
| Overlay bekommt `pointer_press` innen | ja, `btn=272` (BTN_LEFT) |
| Fenster darunter bekommt den Klick innen **nicht** | bestätigt, null Klicks |
| Overlay bekommt `pointer_leave` beim Verlassen | ja |
| Overlay bekommt `pointer_press` außen **nicht** | bestätigt, null Ereignisse |
| Fenster darunter bekommt den Klick außen | ja, `click 3366 701` |

Der schönste Beleg für „keine Überschneidung" steht im Bewegungsprotokoll des
Fensters darunter. Die Spur läuft in 48-px-Schritten auf die Region zu und
**bricht exakt am Mittelpunkt der Region ab**, um erst weit außerhalb wieder
einzusetzen:

```
motion 1588 699
motion 1540 701
motion 1491 699      <- Regionsmitte; ab hier hat das Overlay den Zeiger
motion 2430 701      <- erst hier ist er wieder unten angekommen
click  3366 701
```

**Negativkontrolle.** Dasselbe Overlay, dieselbe Zeigerposition, nur ohne
`--input-region` (also mit der leeren Vorgaberegion):

```
"ctrl_input_region_line": ["INFO input_region=None"],
"ctrl_overlay_got_press": false,
"ctrl_below_clicks":      ["click 1496 700"],
"ctrl_click_through":     true
```

Der Klick geht an **genau derselben Stelle** durch. Damit misst der Test die
Region und nicht etwa „Layer-Surfaces bekommen unter KWin nie Eingaben".

### Warum der erste Anlauf `false` gemeldet hat

**Ein Fehler des Prüfstands, keine Grenze von KWin.**

`ydotool mousemove -a -x <X> -y <Y>` setzt auf dieser Maschine den Zeiger für
**jedes** Ziel auf `(0,0)`. Nachgemessen mit einem bildschirmfüllenden
XWayland-Fenster (damit die X-Zeigerposition live ist) und `xdotool
getmouselocation`: fünf verschiedene Ziele, fünfmal `(0,0)`, Exit-Code jeweils 0.
Beleg: `evidence/ydotool_calibration.json`.

Der Zeiger ist also nie in die Input-Region gefahren. Folge: kein
`pointer_enter`, kein `pointer_press`, und die Klicks landeten an zufälligen
Stellen im Fenster darunter — genau das Bild, das
`enter_inside: false` / `below_got_click_inside: true` erzeugt hat.

Zwei weitere kleinere Fehler im alten Prüfstand, ebenfalls behoben:

* Das `pointer_enter` fällt schon **beim Mappen**, weil der Zeiger dann bereits
  in der Region steht. Die alte Auswertung hat nur die spätere Bewegungsphase
  betrachtet und den Enter übersehen.
* „Hat das Fenster darunter geklickt bekommen?" wurde als Textvergleich der
  ganzen Logdatei ausgewertet. Seit das Fenster auch Bewegungen protokolliert,
  wird gezielt auf `click `-Zeilen gefiltert.

### Merke für alle weiteren Eingabetests in diesem Projekt

* **`ydotool mousemove -a` ist auf dieser Maschine unbrauchbar.** Nur relative
  Bewegung (`ydotool mousemove -x DX -y DY`) verwenden.
* Relative Bewegung unterliegt der Zeigerbeschleunigung: ein Schritt von
  `(30,30)` kommt als `(53,53)` an. Exakte Zielpunkte gehen nur im Regelkreis
  mit Rückmeldung — oder man umgeht das Problem wie hier, indem man das
  Zielrechteck **um die Ist-Position des Zeigers herum** legt.
* Die Zeigerposition lässt sich unter KWin/Wayland nur auslesen, solange der
  Zeiger über einer XWayland-Fläche steht. `xdotool getmouselocation` friert
  ein, sobald er über einer nativen Wayland-Fläche ist. Das ist kein Fehler,
  sondern selbst ein brauchbares Signal — das Einfrieren am Regionsrand ist
  oben genau der Beweis für die Trennung.

---

## 7. Was das für das Design heißt

* §8.1 trägt unverändert: **eine** bildschirmfüllende `Layer::Overlay`-Surface,
  `wl_shm`, leere `opaque_region`, `keyboard_interactivity = None`, und die
  Bounding-Box des Pets als `input_region`. Der Rest der Fläche ist
  klickdurchlässig — nachgewiesen, nicht angenommen.
* Die `input_region` muss bei **jeder** Bewegung des Pets neu gesetzt und
  committet werden. Sie ist doppelt gepuffert und wird erst mit dem nächsten
  `wl_surface.commit` wirksam.
* Beim Verstecken/Zeigen des Pets **nicht** über `attach(None)` unmappen, ohne
  vorher die Properties neu zu setzen — sonst KDE-Bug 503121. Empfohlen:
  `reset_props`.
* Eine Sicherung im Produktivcode ist Pflicht: die Input-Region muss
  ausfallsicher leer sein, nie „nicht gesetzt". Ein Absturz zwischen
  „Region entfernt" und „Region gesetzt" darf niemals einen Zustand
  hinterlassen, in dem die ganze Fläche Eingaben schluckt.

---

## 8. Dateien

```
src/main.rs                       Rust-Client, Modi map/cycles
below_window.py                   Vollbildfenster darunter, protokolliert Klicks + Bewegung
run_fullscreen_test.py            Test 1/2: mappt, bleibt über Vollbild
run_cycle_test.py                 Test 3: KDE-Bug 503121, drei Varianten
run_idle_cpu_test.py              Test 4/5: Idle-CPU, GPU-Kontext
run_input_region_test.py          Test 6: Click-Through + Negativkontrolle
results.json                      Maschinenlesbares Gesamtergebnis
evidence/                         Screenshots, Logs, JSON pro Test
evidence/ydotool_calibration.json Beleg, dass ydotool -a hier kaputt ist
```

Stand: 2026-07-27. Alle Läufe auf KWin 6.7.3 / Plasma 6.7.3, Wayland.
`kwin --replace` wurde nicht ausgeführt, nichts systemweit installiert.
