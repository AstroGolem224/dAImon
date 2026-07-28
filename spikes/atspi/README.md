# Spike T−1.11 — AT-SPI2 als Aktionsfläche

**Frage:** Sind typisierte Aktionen *innerhalb* von Anwendungen über AT-SPI2 erreichbar,
ohne synthetische Eingabe (kein ydotool, kein uinput, kein libei)?

**Verdikt: `pass`** — 6 Anwendungen geprüft, 4 Aktivierungen nachweislich erfolgreich.
Aber: Die Aktionsfläche steht nur mit Zusatzbedingungen zur Verfügung (siehe Empfehlung).

## Design-Zusage (kein Messwert)

**Jede aus dem Baum abgeleitete Bezeichnung ist `tainted` und muss durch die
Vorschau des Auth-Agenten.** AT-SPI-Namen/`role`-Strings stammen aus dem
jeweiligen Anwendungsprozess und sind damit ungeprüfte Eingabe; sie dürfen
niemals ungesehen in eine Ausführung übernommen werden.

## Methode und Messaufbau

- Zugriff über GObject-Introspection (`gi.repository.Atspi`, Typelib
  `Atspi-2.0` aus `at-spi2-core 2.60.5`). `pyatspi` ist nicht installiert,
  wurde auch nicht nachinstalliert.
- Je Anwendung: Start in frischem `mktemp -d` mit Wegwerf-Datei, Warten auf
  das Auftauchen im Baum, dann:
  1. **Vollständigkeit:** vollständiger rekursiver Walk; gezählt werden
     Knoten, Knoten mit benutzbarer `Action`-Schnittstelle (n_actions > 0)
     und Knoten ohne `name`.
  2. **Kosten:** n = 25 Iterationen, je Iteration beginnt die Abfrage frisch
     am Desktop (App wird neu gesucht). Fall (a) nur Wurzel-/Top-Level-Ebene
     (Kinder der App + `name`/`role_name`), Fall (b) voller rekursiver Baum
     (`name` + `role_name` + Action-Test je Knoten). Ausgewiesen p50/p95 in ms.
  3. **Aktivierung:** genau eine explizit harmlose Aktion über das
     `Action`-Interface (`do_action`), mit Vorher/Nachher-Verifikation im Baum:
     - kate/dolphin/konsole: Menüeintrag „Über …" per `Press` → danach existiert
       ein **neuer Dialog-Knoten** („Über KDE — \<App\>") als Top-Level-Fenster.
     - gtk4demo: `GtkMenuButton` „Ansicht" per `click` → Popover-Knoten werden
       **neu `SHOWING`**.
     Frühere Versuche mit Qt-`ShowMenu` (Menü aufklappen) lieferten `True`,
     aber **keine beobachtbare Zustandsänderung** bei Kate/Dolphin — nur die
     Dialog-Strategie gilt als belastbarer Nachweis.
- Watchdog: hartes 60-s-Limit (`SIGALRM`) je Lauf; jede App wird im
  `finally` beendet (Prozessgruppe SIGTERM → SIGKILL; Zusatzprozesse nur, wenn
  ihre cmdline auf das eigene Wegwerf-Tmpdir zeigt).

## Ergebnisse je Anwendung

| App | Knoten | mit Action | ohne Name | Top-Level p50/p95 | Voller Baum p50/p95 | Aktivierung |
|---|---|---|---|---|---|---|
| kate | 1032 | 667 (65 %) | 415 (40 %) | 1,14 / 1,29 ms | 206,7 / 245,5 ms | ✅ „Über"-Dialog verifiziert |
| dolphin | 578 | 404 (70 %) | 126 (22 %) | 1,19 / 1,87 ms | 115,2 / 136,4 ms | ✅ „Über"-Dialog verifiziert |
| konsole | 376 | 259 (69 %) | 136 (36 %) | 1,17 / 1,40 ms | 91,1 / 94,7 ms | ✅ „Über"-Dialog verifiziert |
| gtk4demo (GTK4, PyGObject) | 9 | 4 (44 %) | 3 (33 %) | 3,42 / 4,01 ms | 6,2 / 6,7 ms | ✅ Menübutton, Popover `SHOWING` |
| pavucontrol (GTK3) | 78 | 23 (29 %) | 49 (63 %) | 3,50 / 4,28 ms | 22,1 / 26,1 ms | ❌ Baum da, aber kein eindeutig harmloses Ziel (viele unbenannte Knoten) |
| pluma (MATE/GTK3) | — | — | — | — | — | ❌ tauchte nie im Baum auf |

Rohdaten mit allen 25 Einzelmessungen je Fall: `raw_<app>.json`.
Aggregation: `results.json` (via `aggregate.py`).

## Was gemessen wurde — und was nicht

Gemessen: Sichtbarkeit im Baum, Aktionsauslösung über `Action`, Kosten von
Baumabfragen, Vollständigkeit der Auszeichnung — jeweils an frisch gestarteten
Instanzen auf Wegwerf-Dateien.

Nicht gemessen: Aktionen mit Nebenwirkungen (Speichern, Löschen, Einstellungen
ändern etc. — bewusst nicht angefasst), Langzeitstabilität der Baum-Referenzen,
Verhalten unter Last, Chromium/Electron-Apps, Firefox. Kein Eingriff in
fremde, bereits laufende Fenster des Nutzers.

## Befund: Sichtbarkeit des Baums (wichtigstes Nebenergebnis)

- **Auslieferungszustand** (`toolkit-accessibility=false`): Keine einzige
  Qt-Anwendung exportiert einen Baum — auch nicht die bereits laufenden
  (`konsole`, `dolphin --daemon`). Der AT-SPI-Bus lief zwar
  (`org.a11y.Bus`), das allein reicht Qt aber nicht.
- **`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`** beim Start reicht aus: Kate,
  Dolphin, Konsole wurden so messbar. Das kann dAImon nur für **selbst
  gestartete** Instanzen erzwingen, nicht global für fremde.
- **`gsettings set org.gnome.desktop.interface toolkit-accessibility true`**:
  danach exportieren *auch bereits laufende* Qt-Apps dynamisch (plasmashell,
  krunner, kwin, die fremde konsole …). Zurücksetzen auf `false` räumt die
  Bäume laufender Prozesse **nicht** wieder ab — die Einstellung wurde nach
  dem Spike zurückgesetzt, volle Wiederherstellung des Anfangszustands erst
  nach Neustart der jeweiligen Apps. Zustand vorher/nachher dokumentiert:
  `false` → `true` (Test) → `false`.
- **GTK:** `at-spi2-atk` ist in `at-spi2-core 2.60` aufgegangen
  (`libatk-bridge-2.0.so` vorhanden) → GTK3 (pavucontrol) funktioniert nach
  `toolkit-accessibility=true`. GTK4 spricht AT-SPI nativ, ohne Schalter.
  **pluma (MATE)** blieb auch mit `org.mate.interface accessibility=true`
  unsichtbar — MATE-Apps sind über diesen Weg nicht erreichbar.

## Empfehlung

AT-SPI2 **kommt in den Aktionskatalog**, mit diesen Einschränkungen:

1. **Aktivierungsvoraussetzung dokumentieren:** Ohne erzwungene A11y
   (Env-Flag für eigene Instanzen oder Nutzer-opt-in per Systemeinstellung)
   ist die Fläche unter Plasma schlicht leer. Für fremde, bereits laufende
   Apps ohne Flag gibt es keinen Zugriff — das ist eine harte Grenze.
2. **Kosten behandeln:** Voller Baumwalk bei KDE-Apps 91–245 ms (p95) — für
   Interaktion zu langsam, als Basis einer Aktionsauflistung aber okay, wenn
   der Baum **gecacht** wird und nur Top-Level (~1–2 ms) live nachgefragt wird.
3. **Vollständigkeit:** 65–70 % der KDE-Knoten tragen eine Action, aber
   22–40 % aller Knoten haben keinen Namen; bei pavucontrol 63 %. Der
   Katalog muss mit unbenannten Knoten leben (Positions-/Rollen-Fallback)
   und darf sich nie allein auf Label verlassen — siehe Tainted-Zusage oben.
4. **GTK uneinheitlich:** GTK4 nativ, GTK3 über die gebündelte Bridge,
   MATE gar nicht. Keine universelle Abdeckung versprechen.
5. **Auslöseschwelle:** Automatisiert nur Ziele mit eindeutig harmlosem,
   verifiziertem Label; Verifikation immer als Vorher/Nachher-Vergleich im
   Baum (Rückgabewert von `do_action` allein ist kein Nachweis — Qt-`ShowMenu`
   liefert `True` ohne sichtbare Wirkung).

## Dateien

- `measure.py` — Messtreiber je Anwendung (Start, Statistik, Timing, Aktivierung, Cleanup, Watchdog)
- `aggregate.py` — baut `results.json` aus den Rohdaten
- `dump_tree.py` — Desktop-Überblick (wer exportiert gerade einen Baum?)
- `diag_menu.py` — Diagnose des Qt-`ShowMenu`-Verhaltens (Negativbefund)
- `gtk4_demo_app.py` — minimale GTK4-Testanwendung (PyGObject)
- `raw_*.json` — Rohdaten je Anwendung (inkl. aller Timing-Samples)
- `results.json` — aggregiertes Ergebnis im geforderten Schema
