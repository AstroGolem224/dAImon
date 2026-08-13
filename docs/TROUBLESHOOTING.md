# Wenn etwas nicht tut

Jeder Eintrag hier ist einmal wirklich passiert. Die Reihenfolge ist die der
Häufigkeit, nicht die der Schwere.

---

## Erste Fragen, immer dieselben

```bash
systemctl --user list-units 'daimon-*' --all
journalctl --user -u daimon-hub.service --since -10min --no-pager
```

Ein Dienst, der `activating` ist und bleibt, wartet auf etwas. Ein Dienst, der
`inactive (dead)` ist und ein `Restart=on-failure` hat, wurde **absichtlich**
gestoppt — das ist der Kill-Switch, nicht ein Absturz.

---

## „Es spricht nicht"

**Prüfen, ob überhaupt jemand zuhört:**

```bash
python -m daimon.hub.diag
```

Häufigste Ursachen, in dieser Reihenfolge:

| Symptom | Ursache |
|---|---|
| `tts_active` steht auf `true` und geht nicht zurück | Die Rückkopplungssperre hält das Mikrofon. Der Dienst wurde mitten in einer Äußerung beendet |
| Die Charakterstimme kommt nie | `mimic_rueckfaelle` im Zustand ansehen. Bei belegtem VRAM ist der Rückfall auf Piper **beabsichtigt** und still |
| Kurze Antworten klingen anders als lange | Ebenfalls beabsichtigt: unter 80 Zeichen spricht die schnelle Stufe, weil der Vorlauf von dots.tts sonst hörbar wäre |

**Die Stufe kann auch schlicht `silent` sein.** `speech_threshold` in der
Persona-Datei steuert nur **ungefragtes** Reden — auf eine Frage antwortet auch
`silent`.

---

## „Es hört nicht"

Der häufigste Fall ist kein Fehler, sondern die Sperre: solange gesprochen
wird, hört dAImon nicht zu.

```bash
systemctl --user status daimon-ears.service
python -m daimon.ears.killswitch      # stoppt UND belegt, dass nichts mehr aufnimmt
```

**`MemoryDenyWriteExecute` darf in der Ohren-Unit nicht stehen.** Mit der
Direktive stirbt der erste Aufnahmeversuch:

```
MemoryError: Cannot allocate write+execute memory for ffi.callback()
```

`sounddevice` legt seinen Audio-Callback über cffi an. Gemessen am 09.08.

---

## „Es sieht nicht"

```bash
systemctl --user status daimon-eyes.service
python -m daimon.eyes.killswitch      # stoppt, leert den Kontext, belegt beides
```

**Ein Dialog erscheint bei jedem Start.** Der `restore_token` wird nicht
gefunden oder nicht angenommen. Er liegt unter
`~/.local/state/daimon/screencast-token` — **nicht** unter `~/.config`. Wer ihn
dort sucht, sucht am falschen Ort; der Ort wurde am 13.08. geändert, weil die
Unit `ProtectHome=read-only` hat.

**Der Dienst startet nicht und läuft in eine Zeitüberschreitung.** Beim
allerersten Start ohne Token wartet das Portal auf einen Klick. Die Unit gibt
dafür `TimeoutStartSec=180` — wer den Dialog wegklickt, bekommt einen
Startfehler.

**Es kommen schwarze Bilder.** Das ist der DmaBuf-Fall, und dAImon lehnt ihn
ab, statt ihn zu umgehen. Wenn trotzdem schwarz: `MAP_BUFFERS` steht
irgendwo eingeschaltet.

**Nichts wird gelesen, obwohl der Dienst läuft.** Ohne Fokus-Ereignisse läuft
OCR auf dem Vollbild und braucht rund 3,2 s je Runde statt 0,09 s auf einem
Fenster. Der Watcher aus T-0.12 muss laufen:

```bash
systemctl --user status daimon-focus.service
```

---

## „OCR liefert Unsinn oder nichts"

```bash
tesseract --list-langs
```

Stehen dort nur `afr` und `osd`, fehlen die Sprachdaten. dAImon fällt dann auf
das `tessdata_fast` aus `spikes/ocr/tessdata/` zurück. Sauber ist:

```bash
sudo pacman -S tesseract-data-deu tesseract-data-eng
```

Der Standard-`tessdata` ist dabei **277 ms langsamer** als `tessdata_fast` bei
gleichem Ertrag (gemessen, T−1.10). Wer die Wahl hat, nimmt `fast`.

---

## „Das VLM beschreibt nichts"

```
HTTP 500: image input is not supported
          hint: you may need to provide the mmproj
```

Es fehlt die `mmproj`-Datei. Ollama liefert für `qwen3-vl:8b` keine mit, weil
es dort über eine eigene Engine läuft — wer `llama-server` benutzt, braucht
eine und muss sie getrennt beschaffen. `starten()` bricht deshalb schon beim
Start ab und nicht erst bei der ersten Bildanfrage.

**Es ist plötzlich sehr langsam.** Dann läuft es auf der CPU:

```
warning: no usable GPU found, --gpu-layers option will be ignored
```

`GGML_BACKEND_PATH` muss auf die **Datei** zeigen, nicht auf das Verzeichnis.
Auf das Verzeichnis gezeigt, meldet der Server `Is a directory` und läuft still
weiter.

---

## „Der Bildschirmtext erreicht das Modell nicht"

Das ist die Zusage, kein Fehler. Kontext verlässt die Quarantäne nur durch das
Deklassifizierungs-Gate, und das verlangt **beides**: eine frische Rundenmarke
aus Push-to-Talk **und** einen erkennbaren Bildschirmbezug in der Äußerung.

Ein Kontingent aus dem Wake-Word deklassifiziert **nichts** — sonst reichte ein
Video, das den Namen sagt und nach dem Bildschirm fragt.

Der Grund steht im Audit:

| Grund | Bedeutung |
|---|---|
| `keine_marke` | keine Push-to-Talk-Runde |
| `kontingent_deklassifiziert_nicht` | über das Wake-Word versucht |
| `kein_bildschirmbezug` | die Äußerung fragt nicht nach dem Bildschirm |
| `marke_ungueltig` | abgelaufen oder schon eingelöst |
| `ohne_nutzerhandlung` | proaktiv versucht |

---

## „Ein Prüfstand ist rot, aber nichts ist kaputt"

**T-2.7 bricht ab.** Es weigert sich, seine Attrappen zu fahren, solange eine
echte Unit denselben Namen trägt. Ist `daimon-ears.service` installiert,
bricht der Prüfstand **vor** dem ersten `systemctl stop` ab. Das ist die Lehre
aus dem 02.08., an dem ein `systemctl --user stop '*'` den Desktop abgeräumt
hat.

**`pgrep -f` findet sich selbst.** Die eigene Kommandozeile enthält das
Suchmuster. Dreimal in dieses Loch gefallen:

```bash
pgrep -c -x llama-server      # richtig
pgrep -f "llama-server -m …"  # findet die eigene Shell mit
```

---

## Alles anhalten

```bash
systemctl --user stop 'daimon-*'
```

Und alles vergessen:

```bash
python -m daimon.mind.store --loeschen
python -m daimon.eyes.killswitch
```
