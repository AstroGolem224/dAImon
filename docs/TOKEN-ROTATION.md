# Der API-Token: wo er liegt, wie er ersetzt wird, wie man merkt, dass der alte tot ist

Gehört zu T-3.11. Diese Datei ist Teil der Abnahme, nicht ihre Dokumentation:
das Akzeptanzkriterium heißt „Rotationsverfahren dokumentiert", und ein Verfahren,
das nur eine Person im Kopf hat, ist keins.

---

## Wo er liegt

```
~/.config/daimon/anthropic-token          # eine Zeile, nur der Token, kein "Bearer"
```

Rechte: **0600**, Besitzer der Nutzer. Prüfen:

```bash
stat -c '%a %U %n' ~/.config/daimon/anthropic-token
```

Die Unit lädt ihn über `LoadCredential=anthropic-token:%h/.config/daimon/anthropic-token`.
systemd kopiert ihn dann in ein tmpfs unter `$CREDENTIALS_DIRECTORY`, das
**nur** dem Egress-Dienst gehört (0400) und mit dem Stopp verschwindet.

### Wo er ausdrücklich NICHT liegt

* **Nicht in `daimon.toml`.** Die Datei ist zum Anschauen und Kopieren gedacht;
  ein Token darin landet im nächsten Screenshot.
* **Nicht in einer Umgebungsvariablen.** `/proc/<pid>/environ` ist für jeden
  Prozess desselben Nutzers lesbar. Genau das prüft der Prüfstand.
* **Nicht im Repo.** Auch nicht in `.gitignore`-Form „nur lokal" — was einmal
  committet war, bleibt in der Historie.
* **Nicht bei Mind.** `daimon-mind.service` hat kein `LoadCredential=` und
  `InaccessiblePaths=` auf die Token-Datei. Der Prüfstand sucht den Token in
  Minds Umgebung **und** in seinem Adressraum.

---

## Rotation, vier Befehle

```bash
# 1. Neuen Token in der Konsole der Gegenseite erzeugen, alten NICHT löschen.
# 2. Ablegen, Rechte setzen:
install -m 600 /dev/stdin ~/.config/daimon/anthropic-token   # dann einfügen, Strg-D

# 3. Egress neu starten -- LoadCredential liest nur beim Start:
systemctl --user restart daimon-egress.service

# 4. Nachsehen, ob er den neuen hat (Wahrheitswert, kein Auszug):
python3 - <<'PY'
import json, socket
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(10)
c.connect("/run/user/1000/daimon/egress.sock")
c.sendall(json.dumps({"v": 1, "art": "zustand"}).encode() + b"\n")
print(json.loads(c.makefile("rb").readline()))
PY
```

`token_vorhanden: true` heißt: eine Datei war da und war nicht leer. Es heißt
**nicht**, dass der Token gültig ist — das sagt erst der erste Aufruf mit
Status 200. Ein `401` steht danach im Journal als `DAIMON_STATUS=401`, ohne
Körper und ohne Token.

**Erst danach den alten Token auf der Gegenseite löschen.** Wer zuerst löscht,
hat zwischen Löschen und Neustart ein Fenster, in dem das Pet stumm ist und die
Ursache nach einem Netzfehler aussieht.

---

## Woran man merkt, dass der alte tot ist

| Beobachtung | Bedeutung |
|---|---|
| `token_vorhanden: false` | Datei fehlt, ist leer, oder `LoadCredential=` greift nicht |
| `grund: kein_token` auf jede Anfrage | dasselbe, aus Sicht des Aufrufers |
| `DAIMON_STATUS=401` im Journal | Token da, aber von der Gegenseite abgelehnt |
| `grund: ziel_weg` | Netz oder TLS — **nicht** der Token |

Die vier Fälle sind absichtlich unterscheidbar. Ein gemeinsames „geht nicht"
hätte hier bedeutet, dass man bei jedem Ausfall zuerst den Token verdächtigt.

---

## Was passiert, wenn der Token in ein Log gerät

Er wird redigiert, und zwar auf **jeder** Logzeile des Egress — nicht nur auf
denen, an die jemand gedacht hat. `redigieren()` in
`daimon/brokers/egress/broker.py` läuft über Meldung *und* Felder, inklusive
Fehlermeldungen: dort landen Tokens am häufigsten, weil manche HTTP-Bibliothek
den Header in die Ausnahme schreibt.

Auch **Länge und Präfix fallen**. `sk-ant-…(72 Zeichen)` ist schon ein Auszug.

Der Prüfstand provoziert diesen Fall über den dokumentierten Testschalter, statt
ihn zu behaupten.

---

## Der Testschalter, und warum er unbequem ist

```bash
DAIMON_EGRESS_TESTPROFIL=1 DAIMON_EGRESS_ZIEL=https://127.0.0.1:8443/v1/messages
```

`DAIMON_EGRESS_ZIEL` **allein wirkt nicht**. Erst zusammen mit
`DAIMON_EGRESS_TESTPROFIL=1`, und dann meldet `zustand` `testprofil: true`.

Ein Schalter, der still umleitet, wäre genau die Umleitung, die es hier nicht
geben darf: die Ziel-Domain steht im Code (`api.anthropic.com`), damit sie nicht
einstellbar ist. Der Schalter existiert, weil ein Prüfstand ohne Attrappe gegen
die echte API laufen müsste — sechs Mal je `meta.sh`-Lauf, mit Kosten, und ein
API-Ausfall wäre Rot ohne Defekt.

---

## Wenn der Token kompromittiert ist

1. **Auf der Gegenseite widerrufen.** Zuerst dort, nicht hier — solange er gültig
   ist, hilft kein lokales Löschen.
2. `rm ~/.config/daimon/anthropic-token` und `systemctl --user stop
   daimon-egress.socket daimon-egress.service`. Ohne Socket kommt keine Anfrage
   mehr durch; ohne Token wäre jede ohnehin `kein_token`.
3. **Journal prüfen, nicht auf Verdacht löschen:**
   `journalctl --user -u daimon-egress.service | grep DAIMON_STATUS` zeigt jede
   Anfrage mit Status und Bytes. Körper stehen dort nicht — man sieht also
   *wieviel* rausging und *wann*, nicht *was*. Das ist der Preis des opaken
   Transports, und er ist bewusst bezahlt.
4. Neuen Token nach dem Verfahren oben ablegen.

---

## Offen und benannt

* **Der Token liegt zum Zeitpunkt von T-3.11 noch nicht auf dieser Maschine.**
  Alles außer einem echten Aufruf ist ohne ihn prüfbar — die Attrappe des
  Prüfstands braucht keinen echten Schlüssel. Der erste echte Aufruf ist ein
  Handlauf und gehört als Evidenz nach `tests/evidence/`.
* **Keine Rotationserinnerung.** Es gibt keinen Timer, der nach 90 Tagen mahnt.
  Wer das will, baut eine `systemd`-Timer-Unit — hier steht sie nicht, weil eine
  Erinnerung ohne Empfänger nur ein Journaleintrag ist.
