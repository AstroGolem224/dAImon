# Broker-Sandboxes (T-4.14)

Gemeinsame Basis nach Design §7.5, Abweichungen je Unit begründet. Gemessen am
09.08.2026 mit `systemd-analyze security --user`.

| Unit | Exposure | Abweichung von der Basis | Warum |
|---|---|---|---|
| `daimon-dbus.service` | **4.0 OK** | keine | Spricht nur mit dem Sitzungsbus; die zweite Schicht ist `xdg-dbus-proxy` (`config/dbus-filter.conf`) |
| `daimon-fs.service` | **4.2 OK** | `ProtectHome=tmpfs` statt `read-only`, dazu vier `ReadWritePaths` | `read-only` hieße: der Dateisystem-Broker kann nichts tun. `tmpfs` blendet `$HOME` aus — sichtbar ist nur, was aufgezählt ist |
| `daimon-exec.service` | **4.2 OK** | `ProtectHome=read-only` | Er muss `.desktop`-Dateien lesen können, auch die unter `~/.local/share/applications`. Geschrieben wird dort nie — deshalb hängt die Freigabe am sha256 der Datei |
| `daimon-input.service` | **4.2 OK** | `Type=oneshot`, `RuntimeMaxSec=30`, `Restart=no` | One-shot ist die Zusage selbst. Ein neu gestarteter Input-Broker wäre ein dauerhafter |

## Was überall gilt

* `RestrictAddressFamilies=AF_UNIX` — **nicht** `IPAddressDeny`. Der
  Unterschied ist nicht kosmetisch: `IPAddressDeny` filtert Adressen, lässt
  den Socket aber entstehen. `RestrictAddressFamilies` verhindert, dass es
  überhaupt einen TCP-Socket gibt. Ein Broker, der Aktionen ausführt, hat
  keinen Anlass, mit etwas außerhalb dieser Maschine zu sprechen.
* `InaccessiblePaths` für `.ssh`, `.gnupg`, `~/.local/share/keyrings`, `.pki`
  — bei `daimon-fs` zusätzlich zu `ProtectHome=tmpfs`, das sie ohnehin
  ausblendet. Doppelt, damit eine spätere Lockerung von `ProtectHome` nicht
  lautlos ausfällt.
* `NoNewPrivileges`, `PrivateDevices`, `PrivateTmp`, `ProtectProc=invisible`,
  `ProcSubset=pid`, `ProtectSystem=strict`, `LimitCORE=0`, `UMask=0077`,
  `SystemCallFilter=@system-service` minus `@privileged @resources @obsolete
  @mount @swap @reboot @module`.

## `/dev/uinput`

Kein Broker hat Zugriff — auch `daimon-input` nicht. `PrivateDevices=yes`
blendet es aus, und das ist konsistent: der `ydotool`-Weg, der es bräuchte,
ist abgeschaltet (Spike T−1.3: positioniert nicht; verlangt einen dauerhaften
privilegierten `ydotoold`, was der One-shot-Zusage widerspricht). Der
Regelweg ist libei über das Portal `RemoteDesktop`, und das läuft über einen
Portal-Socket, nicht über ein Gerät.

Sollte der `ydotool`-Weg je eingeschaltet werden, ist `PrivateDevices=yes` die
Zeile, die dann fällt — und die Exposure-Zahl steigt sichtbar. Das ist die
richtige Reihenfolge: die Lockerung wird gemessen, nicht nebenbei gemacht.

## Was diese Zahlen nicht sagen

`systemd-analyze security` bewertet Direktiven, nicht Verhalten. Ein Broker
mit 4.0 und einem Fehler in der Argumentprüfung ist gefährlicher als einer mit
6.0 ohne. Die Zahl steht hier, weil ihre **Veränderung** aussagekräftig ist:
wer eine Schranke entfernt, sieht es hier.

Die eigentlichen Prüfungen laufen im Verifizierer `T-4.14.sh` — und zwar
**in den tatsächlichen Units**, nicht in einer eigens erzeugten Testunit: `ls
~/.ssh` muss scheitern, `curl` muss scheitern, jede Bedingung einzeln
ausgewertet. Eine neu erzeugte Unit mit denselben Direktiven würde beweisen,
dass die Direktiven wirken — nicht, dass die echte Unit sie trägt.
