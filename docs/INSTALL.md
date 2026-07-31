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
