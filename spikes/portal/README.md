# Spike T−1.4 — Portal-Persistenz

## Ergebnis

Auf CachyOS mit KDE Plasma 6.7.3, KWin/Wayland und
`xdg-desktop-portal-kde` 6.7.3 hält der mit `persist_mode=2` erzeugte
`restore_token` über einen echten **Prozessneustart**. Der erste Prozess wurde
nach erfolgreichem `Start` vollständig beendet. Ein separat gestarteter zweiter
Python-Prozess bekam mit dem gespeicherten Token ohne Freigabedialog sofort
einen Stream. Der Reboot-Test ist absichtlich noch offen.

Der Client handelt nur die ScreenCast-Session aus. Er ruft
`OpenPipeWireRemote` nicht auf, liest den Stream nicht und speichert keine
Bildinhalte.

## Methode und DBus-Beweis

`portal_probe.py` führt `CreateSession`, `SelectSources` und `Start` aus.
`SelectSources` erhält `types=1`, `multiple=false`, `persist_mode=2` und bei
Restore-Läufen den gespeicherten Token. Der neue `restore_token` aus jeder
erfolgreichen `Start`-Antwort ersetzt sofort `token.json`.

Parallel läuft ein echter `dbus-monitor --session`. Seine Prozesse werden alle
25 Sekunden rotiert; jeder wird erst mit `terminate()` und nötigenfalls mit
`kill()` beendet. Der Client hat eine globale Obergrenze von 120 Sekunden,
kurze Portal-Schritte 30 Sekunden. Die ungekürzten Mitschnitte liegen unter
`runs/*.dbus.log`.

`restart_prompted` stammt ausschließlich aus
`runs/restart.dbus.log`, nicht aus einer Client-Selbstauskunft:

- Erstlauf und Neustart haben verschiedene DBus-Sender (`:1.534` und `:1.537`)
  und stammen damit nachweislich aus getrennten Clientprozessen
- `ScreenCast.Start`: Zeile 97, Zeit `1785214984.989425`
- zugehörige `org.freedesktop.portal.Request.Response`: Zeile 113, Zeit
  `1785214984.994495`, Ergebnis 0
- Request-Dauer: 0,005070 Sekunden
- im gesamten Start/Response-Intervall kein
  `SettingsChanged` für `org.kde.VirtualKeyboard/active`

Als positive Kontrolle enthalten der manuell bestätigte Erstlauf und beide
interaktiven Fallbacks während ihres offenen `Start`-Request-Objekts mehrere
dieser KDE-Portal-Signale. Ihre Request-Dauern waren 3,645176, 4,578821 und
2,882123 Sekunden. `analyze_trace.py` leitet daraus
`prompted_from_dbus` ab.

## Verfälschte und fremde Tokens

| Fall | DBus-Ergebnis | Verhalten |
|---|---:|---|
| letztes Zeichen geändert | Start/Response 4,578821 s, KDE-Interaktionssignale | Dialog; nach Bestätigung erfolgreicher Fallback |
| Token weggelassen | Start/Response 2,882123 s, KDE-Interaktionssignale | Dialog; nach Bestätigung erfolgreicher Fallback |
| noch unbenutzter Token einer anderen Session | Start/Response 0,011521 s, keine Interaktionssignale | ohne Dialog akzeptiert |

Der dritte Fall zeigt, dass der Token nicht an das Portal-Session-Objekt
gebunden ist. Ein ungültiger oder fehlender Token wird dagegen nicht hart
abgewiesen, sondern führt sicher in den interaktiven Auswahlpfad.

## Reboot-Test nach dem nächsten regulären Neustart

Keinen Neustart für diesen Spike erzwingen. Nach dem nächsten regulären Reboot
im Repo-Wurzelverzeichnis genau diesen **einen Befehl** ausführen:

```bash
timeout --foreground --signal=TERM --kill-after=5s 130s python3 spikes/portal/reboot_check.py
```

Der Befehl startet den gespeicherten Token mit einem neuen Prozess, schreibt
`runs/reboot.dbus.log` und `runs/reboot.analysis.json` und ersetzt
`reboot_prompted` in `results.json` durch den aus dem DBus-Mitschnitt
abgeleiteten booleschen Wert. Falls KDE wider Erwarten einen Dialog zeigt,
diesen innerhalb von 120 Sekunden bestätigen; das Ergebnis bleibt trotzdem
`reboot_prompted=true`.

## Dateien

- `portal_probe.py`: minimaler Portal-Client mit Watchdogs und DBus-Monitor
- `analyze_trace.py`: DBus-only-Auswertung
- `reboot_check.py`: Ein-Befehl-Reboot-Nachtest
- `runs/*.dbus.log`: rohe, ungekürzte Portal-Mitschnitte
- `runs/*.client.json`: Ablaufdiagnostik, nicht als Prompt-Beweis verwendet
- `token.json`: lokaler Tokenbestand, Modus 0600 und per `.gitignore` ausgeschlossen
