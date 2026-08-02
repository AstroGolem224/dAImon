# Verworfene Messfenster

## phase1-usage-2026-07-31-bis-08-02.json — T-1.10, erster Anlauf

**Verworfen am 02.08.2026, nicht gelöscht.** Die Zahlen darin sind echt, sie
messen nur nicht, was T-1.10 zusagt.

```
crashes: 135     days: 3     needs_input_events: 0     verdict: pending
```

**Warum die Messung unbrauchbar ist:** In diesem Fenster hingen alle vier Units
im Neustart-Karussell — `ReadWritePaths=%t/daimon` verlangt ein Verzeichnis, das
nach jedem Neustart fehlt, Ergebnis `226/NAMESPACE` in Endlosschleife
(Restart-Zähler stand bei 97). Dazu drei Neustarts der Maschine, ausgelöst durch
einen Verifizierer, der `systemctl --user stop '*'` erlaubt bekam. Und das Face
lief die ganze Zeit auf einem Release-Binary vom 30.07., also ohne T-2.3, T-2.4
und T-2.5.

T-1.10 fragt: **stört das Pet im Alltag?** Gemessen wurde stattdessen ein System,
das gar nicht lief. `needs_input_events: 0` sagt es am deutlichsten — in drei
Tagen keine einzige Rückfrage, also nichts, was hätte stören können.

Die Uhr läuft seit dem **02.08.2026** neu, mit stabilen Units und aktuellem
Binary. Diese Datei bleibt liegen, damit die Verwerfung nachvollziehbar ist und
niemand später die alten Zahlen für einen Verlauf hält.
