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

## Kopieren oder verlinken — eine Entscheidung, keine Geschmacksfrage

Diese Anleitung sagte bis zum 17.08. `install -m 0644` (kopieren) und nannte
vier Units. Mehrere Unit-Dateien sagen in ihrem Kopf `systemctl --user link`.
Auf der Entwicklungsmaschine war am 17.08. **beides** im Einsatz: Symlinks für
die meisten, eine echte Kopie für `daimon-dbus.service`. Der Unterschied ist
nicht kosmetisch:

| | wirkt eine Repo-Änderung? | wofür |
|---|---|---|
| `systemctl --user link <absoluter Pfad>` | ja, nach `daemon-reload` | **Entwicklung** |
| `install -m 0644 … ~/.config/systemd/user/` | **nein** | eine feste Installation |

Eine Kopie, die niemand nachzieht, ist genau die Drift, die T-0.12 gefunden
hat: das Repo und das laufende System sagen Verschiedenes, und geprüft wird
das Repo. **Wer an dAImon arbeitet, verlinkt.** Wer es nur benutzt, kopiert —
und zieht nach jedem `git pull` nach.

```bash
# Entwicklung (empfohlen): alle Units aus dem Checkout verlinken
systemctl --user link "$PWD"/config/systemd/daimon-*.{service,socket,timer}
systemctl --user daemon-reload
```

**Der Pfad muss der ECHTE sein**, nicht der Symlink durch `$HOME`. Auf der
Entwicklungsmaschine zeigen ältere Verweise auf
`~/Dokumente/Github/dAImon`, neuere auf `/mnt/data/AI/repos/dAImon` — dasselbe
Verzeichnis über zwei Wege. Units mit `ProtectHome=` sehen den Weg durch `$HOME`
**nicht** (dort ist ein leeres tmpfs), und der Dienst stirbt mit `203/EXEC`.
Der Befund steht ausführlich im Kopf von `daimon-recorder.service`.

## Was aktiviert werden muss, und was nicht

29 Unit-Dateien (23 `.service`, 4 `.socket`, 2 `.timer`), drei Sorten. Nur
die erste braucht `enable`.

```bash
# 1. Dauerhaft laufende Dienste (10 Stück, alle mit [Install])
systemctl --user enable --now \
  daimon-hub.service daimon-auth.service daimon-hookbridge.service \
  daimon-focus.service daimon-face.service \
  daimon-ears.service daimon-eyes.service daimon-mind.service \
  daimon-recorder.service daimon-plan.service
```

**Die vier Aktionsbroker bleiben aus, bis eine Aktion sie braucht** — sie
haben `[Install]`, werden aber vom Hub bei Bedarf gestartet:
`daimon-dbus`, `daimon-fs`, `daimon-exec`, `daimon-input`.

**Ein Rückgrat wählen, nicht drei.** Der Mind zeigt über `--egress-socket` auf
genau einen Weg (siehe Kopf von `daimon-mind.service`):

| Unit | Weg | Voraussetzung |
|---|---|---|
| `daimon-lokal-broker` | Modell auf dieser Maschine | `ollama` läuft, ein Modell gezogen |
| `daimon-cli-broker` | `claude -p` über das Abo | Netz, angemeldete CLI |
| `daimon-egress` | Messages-API | `~/.config/daimon/anthropic-token` |

```bash
systemctl --user enable --now daimon-lokal-broker.service   # oder cli-broker
```

```bash
# 2. Socket-aktiviert -- KEIN enable des Dienstes, nur des Sockets
systemctl --user enable --now \
  daimon-stt.socket daimon-tts.socket daimon-egress.socket
```

`daimon-gpu@.socket` ist eine Vorlage und wird je Worker instanziiert.

```bash
# 3. Timer
systemctl --user enable --now daimon-audit-verify.timer   # taeglich, prueft die Audit-Kette
systemctl --user enable --now daimon-phase1.timer         # optional, Alltagsmessung
```

Status und Logs:

```bash
systemctl --user list-units 'daimon-*' --all
journalctl --user -u 'daimon-*' -f
```

Zum Entfernen:

```bash
systemctl --user disable --now 'daimon-*'
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

Vom Billigsten zum Aussagekräftigsten. Die ersten drei kosten Sekunden.

```bash
systemctl --user list-units 'daimon-*' --all   # laeuft ueberhaupt etwas
python -m daimon.hub.diag                      # Zaehler, Warteschlangen, Latenz
python -m pytest -q                            # 1850 Tests (24.08.)
```

**Die eingefrorenen Zusagen** — erste Zeile jedes Phasen-Gates. Bricht ab,
wenn ein abgenommener Verifizierer nachträglich geändert wurde:

```bash
bash tests/verify/verify-frozen.sh
```

**Die Audit-Kette**, beide Ströme. `ok: false` heißt entweder gerissen oder
ohne Journal-Anker — der Befund nennt, welches:

```bash
python -m daimon.hub.audit --verify
```

Dasselbe läuft täglich von selbst (`daimon-audit-verify.timer`) und **von
außen ohne Schreibrecht**. Ein Fehlschlag steht in `systemctl --user --failed`.

**Der Sicherheitsstand** (T-6.9). Misst am laufenden System, statt eine Liste
abzuhaken; jeder Befund trägt seine `herkunft`:

```bash
python tools/final_security.py    # schreibt tests/evidence/final-findings.json
```

**Die Naht** — Push-to-Talk → gesprochene Bildschirmfrage → Kontext im Modell.
Der einzige Prüfschritt, der einen Menschen an der Taste braucht:

```bash
python tools/naht_messen.py vorher
#   ... Taste druecken und "was steht gerade auf dem bildschirm" fragen ...
python tools/naht_messen.py nachher
```

Sechs Stationen, jede mit `getragen`, `nicht getragen` oder **`nicht
messbar`**. Der dritte Zustand ist der Grund für das Werkzeug: eine Station,
deren Dienst nicht läuft, hat nicht versagt.

Läuft etwas nicht: [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Jeder Eintrag dort
ist einmal wirklich passiert.
