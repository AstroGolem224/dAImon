# Handprüfung des Aktionskatalogs (T-4.2)

Freigegeben am **09.08.2026** von Matthias. Grundlage: `config/actions/candidates.yaml`
mit **282 Kandidaten aus 25 Komponenten**, erzeugt von
`tools/generate-action-candidates.py` (T-4.1). Freigegeben sind **17**.

Dieses Dokument ist die Begründung. `config/actions/core.yaml` ist die Whitelist;
was hier nicht steht, steht dort nicht drin.

---

## Der Maßstab

Drei Fragen je Aktion, in dieser Reihenfolge:

1. **Gibt es eine Umkehraktion?** Nicht „könnte man bauen" — gibt es eine, und
   steht ihre ID im Katalog? Design §846 macht die Existenz der Inversen zur
   operativen Definition von umkehrbar. Ohne sie: `foreground: ask`.
2. **Was ist der schlimmste Fehlgriff?** Nicht der wahrscheinliche. Ein
   verhörtes Wort, ein verrutschter Parameter, ein Modell, das etwas anderes
   meinte.
3. **Muss der Hub das ohne Vorschau erkennen dürfen?** `direct: true` ist eine
   Abkürzung am Bestätigungsdialog vorbei (Design §263). Sie gilt nur für
   Aktionen mit Inverse und ohne freien Wert — und **nie** für etwas, das aus
   einer Modellausgabe kommt, egal was im Katalog steht.

`background` und `scheduled` sind bei **allen 17** Einträgen `deny`. In Phase 4
gibt es keinen Anlass, bei dem das Pet ungefragt handelt; ein `allow` dort wäre
eine Fähigkeit, die niemand angefordert hat.

---

## Freigegeben

### Medien — 6 Aktionen, alle `direct`

`media.playpause`, `media.next`, `media.previous`, `media.stop`,
`media.seek.forward`, `media.seek.backward`.

Die Gruppe ist der Grund, warum es `direct` überhaupt gibt: jede Aktion hat
eine Inverse, oft sich selbst. Der schlimmste Fehlgriff ist ein übersprungenes
Stück Musik. `open_world: true`, weil der Empfänger eine beliebige
MPRIS-Anwendung ist — wir wissen nicht, wer zuhört, und behaupten es auch nicht.

`media.stop` trägt die schwächere Zusage: bei manchen Abspielern geht die
Abspielposition verloren. Vernichtet wird trotzdem nichts, was es vorher gab.

### Lautstärke — 4 Aktionen

`audio.volume.up`, `audio.volume.down`, `audio.mute.toggle`,
`audio.volume.set`.

Schrittweise, umkehrbar, und durch die Anlage selbst auf 0–100 % beschränkt.
`audio.volume.set` ist der **einzige Eintrag mit freiem Wert** und deshalb der
einzige mit einer Zahlenschranke: `value_between: [0.0, 1.0]`. Er hat keinen
kglobalaccel-Eintrag — eine Tastenkombination kennt keinen Wert —, der Broker
dazu entsteht in T-4.7. Bis dahin ist der Eintrag eine Zusage über Schranken,
keine Behauptung über eine Fähigkeit.

**Nicht freigegeben, ausdrücklich:** `kmix/increase_microphone_volume`,
`kmix/decrease_microphone_volume`, `kmix/mic_mute`. Ein Pet, das die
Mikrofonlautstärke aufdrehen oder die Stummschaltung aufheben kann, hebt genau
die Zusage auf, die dieses Projekt trägt: **kein Mikrofon ohne Push-to-Talk**.
Dass die Aktion „nur" die Lautstärke ändert, ist kein Gegenargument — sie
ändert sie in die falsche Richtung, und niemand hört es.

### Fenster und Arbeitsflächen — 5 Aktionen

`desktop.next` und `desktop.previous` sind `direct`: rein visuell, umkehrbar,
kein Fenster ändert seinen Zustand.

`window.to_next_desktop` und `window.to_previous_desktop` sind es **nicht**.
Sie verschieben das aktive Fenster, also etwas, woran gerade jemand arbeitet.
Umkehrbar ja — aber ein Parser, der sich verhört, räumt einem Menschen das
Fenster weg, und das merkt er erst, wenn es weg ist.

`kde.window.raise` heißt **raise und nicht focus**. KWin 6 hat
`activateWindow()` aus dem Workspace-Wrapper entfernt (Design §1545): anheben
geht, fokussieren nicht. Eine Aktion, die mehr verspricht als sie tut, ist
schlimmer als eine fehlende. Sie hat keine Inverse — welches Fenster vorher
oben lag, weiß der Katalog nicht — und ist deshalb `ask` und nicht `direct`.
Das ist der erste Eintrag, an dem die Regel „ohne `reversible_by` wird gefragt"
tatsächlich greift.

### Bildschirmfoto — 2 Aktionen

`screenshot.fullscreen` und `screenshot.region`, beide `ask`, keine `direct`,
keine Inverse.

Sie erzeugen eine Datei mit Bildschirminhalt — genau das Material, das dieses
Projekt sonst in Quarantäne hält (Phase 5, Deklassifizierung am Ende). Die
Datei verlässt den Rechner nicht; eine Aktion, die sie verschicken würde, gibt
es nicht, und sie bekäme `externally_visible: true` und damit nie eine
Freigabe im Hintergrund.

`screenshot.region` ist die bevorzugte Variante: der Mensch zieht den Bereich
selbst auf, der Auslöser bestimmt nicht, was aufgenommen wird.

**Nicht freigegeben** aus derselben Komponente: `RecordScreen`,
`RecordRegion`, `RecordWindow` (laufende Aufzeichnung statt Einzelbild),
`ActiveWindowScreenShot`, `CurrentMonitorScreenShot`,
`WindowUnderCursorScreenShot`, `OpenWithoutScreenshot`, `_launch`.

---

## Bleibt `candidate` — und warum

| Komponente / Aktion | Kandidaten | Grund |
|---|---|---|
| `org_kde_powerdevil` | 13 | Ruhezustand, Bildschirm aus, Energieprofil. Die Akzeptanzliste von T-4.2 nennt sie namentlich. Ein Ruhezustand ist nicht umkehrbar — was danach kommt, ist ein neuer Zustand, kein wiederhergestellter |
| `kwin` Skript-/Effekt-Aktionen, u. a. `kwin.script.load` | — | Namentlich ausgeschlossen. Ein geladenes Skript ist beliebiger Code im Compositor; damit wäre der Katalog eine Formalität |
| `ksmserver` | 8 | Abmelden, Neustart, Herunterfahren, Bildschirm sperren. Vernichtet ungespeicherte Arbeit in jeder offenen Anwendung |
| `plasmashell` (Aktivitäten, Verlauf, Hintergrundbild) | 30 | Aktivitätswechsel ändert Fenster*mengen*, nicht nur den Blick; die Zwischenablage-Aktionen lesen fremden Inhalt |
| `ActivityManager` | 1 | Wie oben |
| `kwin` Fensterverwaltung im Übrigen | ~160 | Schließen, minimieren, Vollbild, Kachelung, „auf allen Arbeitsflächen", Fenster töten. Teils destruktiv, teils ohne Inverse, alles unklassifiziert |
| Anwendungsstarter (`claude`, `claude-desktop`, `com.anthropic.*`, `org.chromium.Chromium`, `konsole`, `dolphin`, `kcalc`, `krunner`, `systemsettings`, `spectacle/_launch`, `plasma-systemmonitor`, `emojier`) | ~20 | Eine Anwendung starten heißt, einen Prozess mit den Rechten des Nutzers zu erzeugen. `org.kde.konsole` ist davon der deutlichste Fall: eine Shell |
| `daimon-auth` (`ptt`) | 1 | Das Pet darf seine eigene Push-to-Talk-Umschaltung nicht auslösen. Genau diese Schleife schließt Design §1.2, und sie bliebe zu, wenn hier ein `approved` stünde |
| `kaccess`, `KDE Keyboard Layout Switcher`, `touchpadshortcuts`, `kscreen` | 7 | Eingabe- und Anzeigegeräte. Unklassifiziert, und ein umgestelltes Tastaturlayout oder ein abgeschaltetes Touchpad nimmt dem Menschen das Mittel, es zurückzustellen |

**Regel für alles Weitere:** unklassifiziert heißt `candidate`. Ein Eintrag
wandert nicht dadurch nach `core.yaml`, dass er harmlos aussieht, sondern
dadurch, dass jemand die drei Fragen oben beantwortet und die Antwort
hinschreibt.

---

## Was diese Datei nicht ist

Sie ist **keine Ausführbarkeit**. Der Katalog beschreibt Schranken; ausgeführt
wird erst über Policy (T-4.4), Ausführungsauftrag (T-4.5) und einen
argumentvalidierenden Broker (T-4.7). Bis dahin steht hier eine Liste, die
niemand liest — und das ist die richtige Reihenfolge: die Schranke vor der
Fähigkeit.

Der Verifizierer `tests/verify/T-4.2.sh` fehlt noch; `tests/verify/**` gehört
der Rolle `reviewer`.
