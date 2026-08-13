# systemd-User-Dienste installieren

Die Units verwenden absichtlich absolute Pfade zu diesem Checkout. Das Hub-
und Bridge-Python kommt aus dem Projekt-venv, das Face ist das Rust-Release-
Binary, und der Fokus-Empfänger nutzt wegen `dbus-python`/PyGObject das
System-Python.

Vor der Installation das Face bauen:

```bash
cd /home/itiger013/Dokumente/Github/dAImon
cargo build --release --manifest-path face/Cargo.toml
```

Units nach `~/.config/systemd/user/` kopieren und für die grafische Sitzung
aktivieren:

```bash
install -d ~/.config/systemd/user
install -m 0644 config/systemd/daimon-{hub,hookbridge,focus,face}.service \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now \
  daimon-hub.service daimon-hookbridge.service \
  daimon-focus.service daimon-face.service
```

Status und Logs:

```bash
systemctl --user status daimon-hub daimon-hookbridge daimon-focus daimon-face
journalctl --user -u 'daimon-*' -f
```

Zum Entfernen:

```bash
systemctl --user disable --now \
  daimon-face.service daimon-focus.service \
  daimon-hookbridge.service daimon-hub.service
rm ~/.config/systemd/user/daimon-{hub,hookbridge,focus,face}.service
systemctl --user daemon-reload
```

## Phase-1-Alltagstest aufzeichnen

Der Timer liest alle fünf Minuten ausschließlich systemd-Zähler,
`/proc/<pid>/stat` und den Face-Diagnose-Socket. Er schreibt die fortlaufende
Messung nach `tests/evidence/phase1-usage.json`. `fehlalarme`,
`ablenkungen` und `verdict` sind bewusst menschliche Angaben; Matthias trägt
sie nach dem Test zum Beispiel in einer Zeile ein:

```bash
jq '.fehlalarme=0 | .ablenkungen=0 | .verdict="weiter"' tests/evidence/phase1-usage.json > tests/evidence/phase1-usage.json.tmp && mv tests/evidence/phase1-usage.json.tmp tests/evidence/phase1-usage.json
```

Service und Timer installieren und die Uhr starten:

```bash
install -m 0644 config/systemd/daimon-phase1.{service,timer} ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now daimon-phase1.timer
```

## Gemessene Sandbox-Bewertung

Die folgenden tatsächlichen Gesamtnoten stammen am 30. Juli 2026 von
`systemd-analyze security --user` auf der laufenden Installation (systemd
261.2):

| Unit | Exposure Level | Gesamtnote |
|---|---:|---|
| `daimon-hub.service` | 3.6 | OK :-) |
| `daimon-hookbridge.service` | 3.7 | OK :-) |
| `daimon-focus.service` | 3.6 | OK :-) |
| `daimon-face.service` | 3.6 | OK :-) |

## Was diese Härtung nicht leistet

Die Units begrenzen versehentliche Zugriffe und den Schaden eines
kompromittierten dAImon-Teilprozesses. Sie sind keine Sicherheitsgrenze gegen
einen Angreifer, der bereits als derselbe Benutzer läuft: Dieser kann
User-Units ersetzen, lesbare Sitzungsdaten erreichen oder Prozesse über die
Benutzersitzung beeinflussen. Auch Unix-Peer-Credentials sind deshalb ein
Wegweiser zur Prozesszuordnung, keine starke Authentifizierung.

`RestrictAddressFamilies=` trennt Netzwerkprotokollfamilien, ist aber kein
Inhaltsfilter. Die Hook-Bridge behält absichtlich IPv4 für ihren
Loopback-Port; `IPAddressDeny=/IPAddressAllow=` und `SocketBindAllow=` engen
diesen Sonderfall ein. Das Dateisystem-Hardening schützt außerdem nicht vor
Dateien, die für denselben Benutzer außerhalb der Unit ohnehin lesbar sind.

---

# Von null auf lauffähig

Die Abschnitte oben installieren die Dienste. Was sie zum Arbeiten brauchen,
steht hier — in der Reihenfolge, in der es fehlt, wenn man es vergisst.

## 1. Sprachdaten für OCR (T-5.1)

```bash
tesseract --list-langs
```

Stehen dort nur `afr` und `osd`, fehlen sie:

```bash
sudo pacman -S tesseract-data-deu tesseract-data-eng
```

**Besser ist `tessdata_fast`.** Gemessen (T−1.10) auf demselben Ausschnitt:
267,9 ms gegen 545,1 ms beim Standard-`tessdata`, bei 620 gegen 619 Zeichen.
Minus 277 ms für einen Zeichenunterschied. Der Augendienst sucht der Reihe nach
in `$DAIMON_TESSDATA`, `~/.local/share/daimon/tessdata`, dem Spike-Verzeichnis
und zuletzt im System — das Systemverzeichnis kommt **zuletzt**, und das ist
Absicht.

## 2. Modellgewichte

| Was | Woher | Größe |
|---|---|---|
| STT (Parakeet) | `spikes/stt-referenz/modell_holen.sh` | 665 MB |
| VLM (`qwen3-vl:8b`) | `ollama pull qwen3-vl:8b` | 6,1 GB |
| Charakterstimme | eigener Mimic-Dienst | 6,2 GB VRAM zur Laufzeit |

**Das VLM braucht zusätzlich eine `mmproj`-Datei**, die Ollama nicht mitliefert
— dort läuft das Modell über eine eigene Engine. Ohne sie meldet `/props`
`modalities.vision = false`, und der Worker bricht beim **Start** ab statt bei
der ersten Bildanfrage. Das ist gewollt: ein Server, der läuft, VRAM hält und
bei jedem Bild HTTP 500 sagt, sieht aus wie ein kaputtes Modell und nicht wie
eine fehlende Datei.

## 3. Der eine Klick

```bash
systemctl --user enable --now daimon-eyes.service
```

Beim **allerersten** Start zeigt das Portal einen Auswahldialog. Einen Monitor
wählen und bestätigen — danach nie wieder. Der `restore_token` liegt unter
`~/.local/state/daimon/screencast-token`, Modus 0600.

Gemessen: erster Lauf 6,03 s (mit Dialog), zweiter Lauf 0,01 s (ohne). Die Unit
gibt dafür `TimeoutStartSec=180`; wer den Dialog wegklickt, bekommt einen
Startfehler und keinen stillen Ausfall.

**Rückgängig:** Kontextmenü → *Bildschirmzugriff widerrufen*. Das löscht den
Token und schließt die Sitzung.

## 4. Prüfen, dass es steht

```bash
systemctl --user list-units 'daimon-*' --all
python -m daimon.hub.diag
python -m pytest
```

Läuft etwas nicht: [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Jeder Eintrag dort
ist einmal wirklich passiert.
