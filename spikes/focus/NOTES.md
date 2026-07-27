# T−1.9 — KWin-Fokusereignis

**Ergebnis: bestanden.** Das Signal trägt die Gatterkette. Risiko R25 ist entschärft.

## Aufbau

KWin-Script `daimon-focusprobe` (rein lesend) meldet `windowActivated` und
`captionChanged` per `callDBus` an einen Python-Empfänger, der als JSONL mit
monotonem Zeitstempel mitschreibt. Umschaltung im Messlauf über
`org.kde.KWin /WindowsRunner` — kein `loadScript` nötig.

```bash
kpackagetool6 -t KWin/Script -i spikes/focus/daimon-focusprobe
# daimon-focusprobeEnabled=true unter [Plugins] in ~/.config/kwinrc
qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure
python3 spikes/focus/probe.py &
python3 spikes/focus/measure.py 50
```

## Zahlen

| | |
|---|---|
| Wechsel angefordert | 50 |
| gemeldet | **50** |
| verpasst | **0** |
| Latenz p50 | **0,5 ms** |
| Latenz p95 | **0,9 ms** |

Zielwert war p95 < 200 ms. Wir liegen zwei Größenordnungen darunter.

## Der eigentliche Befund

`captionChanged` feuert bei Änderungen **innerhalb** eines Fensters — beobachtet
an FreeCAD: `Unnamed` → `Wicking_v` → `* Wicking_v`, also Dokumentwechsel und
Änderungsmarker.

Aber: **nur wenn die Anwendung ihren Titel ändert.** Neue Terminalausgabe,
Scrollen, ein neuer Absatz im Editor — nichts davon ändert den Titel und nichts
davon erzeugt ein Ereignis.

Ein Gegentest mit Konsole und der Escape-Sequenz `\033]0;...` erzeugte in zehn
Versuchen **ein** Ereignis: Konsole setzt ihren Titel nach eigenem Schema und
überschreibt die Sequenz.

> **Konsequenz für T-5.4:** Der Abtast-Timer ist nicht optional. Fokus- und
> Titelereignisse decken Fenster- und Dokumentwechsel ab; den Großteil der
> tatsächlichen Inhaltsänderung trägt der Timer.

## Offen

`kwin --replace` wurde **nicht** getestet: In FreeCAD stand ein `*`, also
ungespeicherte Arbeit. Ein Compositor-Neustart hätte die Sitzung riskiert.
Nachzuholen, wenn nichts Offenes läuft.

## Abweichung von der Vorlage

Screenpipes `focus_tracker/linux.rs` ist ein 60-Zeilen-Stub, der immer `Unknown`
meldet; sein Kommentar wägt X11 und wlr-foreign-toplevel ab und erwähnt KDE nicht.
Auf KWin über das Scripting-Interface ist das Signal verlustfrei und praktisch
latenzfrei. Sie haben an der falschen Stelle gesucht.
