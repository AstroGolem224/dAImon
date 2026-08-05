# Token-Rotation

Der API-Token liegt ausschließlich in der von `LoadCredential=` gelesenen
Credential-Datei. Für eine Rotation wird eine neue Datei mit Modus 0600
atomar an deren Stelle gesetzt und anschließend `daimon-egress.service` neu
gestartet (`systemctl --user restart daimon-egress.service`). Mind und Hub
werden nicht neu gestartet.

Danach muss `zustand` `token_vorhanden: true` melden und eine lokale bzw.
bewusst von Hand ausgeführte Kanarienanfrage gelingen. Die MainPID muss sich
geändert haben. Der alte Prozess ist damit tot; im Adressraum der neuen PID
darf der alte Token nicht mehr auffindbar sein. Der Token gehört nie in eine
Umgebungsvariable, `daimon.toml`, ein Log oder das Repository.
