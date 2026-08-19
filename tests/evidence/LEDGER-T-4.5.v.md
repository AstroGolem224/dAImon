# LEDGER T-4.5.v — Verifizierer für den Ausführungsauftrag

**Ausgang 18.08.: `produktdefekt-rot`** — der Befund unten ist mit `590ee02`
und `d5c012b` behoben.
**Ausgang 19.08.: `gruen`, eingefroren.** Siehe §Nachlauf am Ende.

Stand 18.08.: der Verifizierer ist gebaut, gegen das Gut-Muster grün
(115 Prüfungen), gegen alle zwölf Mutanten rot — einschließlich der beiden **umgekehrten**, die eine
HMAC-Prüfung *einführen* — und gegen den Arbeitsbaum rot an genau einem
Kriterium, mit Beleg.

Der Befund ist Akzeptanzpunkt 6: **„Herkunft über den Socket" ist eine Zusage
ohne Vorrichtung.** Kein Broker prüft die Gegenstelle, und `aktion.sock`,
über den die Ticketeinlösung läuft, hat keine Unit-Allowlist. Da der HMAC
gestrichen ist, bleibt für die Herkunft eines Auftrags heute nichts übrig
außer dem Dateimodus 0600.

---

## Provenienz

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Worktree | `/mnt/data/AI/repos/dAImon-t45` |
| Branch | `reviewer/p4-T-4.5v` |
| Ausgangs-Commit | `3c1b843ead93cd638b0c3a61083f45d0441650fa` |
| Gelesen | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` T-4.5 (Z. 1229–1241) · `docs/DESIGN.md` §1.3 (Z. 95–140) und §6.2 (Z. 756–790), dazu Z. 943 (Peer-Prüfung als Wegweiser) · `tests/verify/T-4.4.sh` + `t44_pruefstand.py` + `tests/mutants/T-4.4/erzeugen.sh` + `tests/evidence/LEDGER-T-4.4.v.md` als Formvorlage und wegen des dortigen Produktdefekts |
| Neue Artefakte | `tests/verify/T-4.5.sh`, `tests/verify/t45_pruefstand.py`, `tests/fixtures/known-good/T-4.5/` (153 Dateien, 1,6 MB), `tests/mutants/T-4.5/{erzeugen.sh,.gitignore}`, dieses Ledger |
| sha256 | `T-4.5.sh` `b7b86cb6…577e52fe7` · `t45_pruefstand.py` `f05dec35…c7fb679f9d` · `erzeugen.sh` `3788b19f…83ae37980c50a4f98` |
| `freeze.sh` | **nicht aufgerufen** (eigener autorisierter Task, trotz „(eingefroren)" im Plan) |
| Fremde Tasks | `T-7.1`, `T-7.2`, `T-7.5`, `T-4.4`, `T-4.6`, `T-5.9` unberührt — `git status` listet ausschließlich die vier neuen `T-4.5`-Pfade |
| Laufzeit | 0,17 s je Verifiziererlauf (kein `sleep`, kein Warten; die Socket-Proben klopfen und lesen sofort) |

---

## Was gemessen wird

Sieben Kriterien, eines je Akzeptanzpunkt aus dem Implementierungsplan. Jedes
rechnet einzeln ab; ein Kriterium ohne eine einzige Messung zählt als rot
(„nicht gemessen" ist nicht grün).

| | Akzeptanzpunkt | Prüfungen |
|---|---|---|
| K1 | **Keine Signatur** (Design §1.3/§6.2 haben den HMAC gestrichen) | 14 |
| K2 | Die acht Felder; `params_hash` bindet die Parameter | 16 |
| K3 | `audience` bindet an genau **einen** Broker | 25 |
| K4 | Kanonische Serialisierung; `schema` verhindert Lesarten | 18 |
| K5 | **Monotone** Frist — eine Zeitumstellung verlängert nichts | 13 |
| K6 | Herkunft über den Socket, nicht über Kryptografie | 6 |
| K7 | Ticketeinlösung beim Hub, unmittelbar vor der Ausführung | 23 |

Die sechs vom Plan geforderten Einzelabweisungen sind darin, jede mit
Positivkontrolle:

| Verlangt | Kriterium | Positivkontrolle |
|---|---|---|
| manipulierte Parameter | K2 | derselbe Auftrag unverändert → angenommen |
| falsche `audience` | K3 | derselbe Auftrag bei `daimon-dbus` → angenommen |
| abgelaufene monotone Frist | K5 | `jetzt=999.0` bei Frist 1000.0 → angenommen |
| wiederholtes Ticket | K7 | die erste Einlösung → gelingt |
| unbekanntes `schema` | K4 | `daimon.order.v1` → angenommen |
| abweichende Serialisierung | K4 | die kanonische Zeile → angenommen |
| **gültiger Auftrag** | K1/K2 | die acht Felder aus §6.2 → angenommen |

**Reiner Prüflogik-Test, ohne Ausführung.** Es wird nie ein `gdbus` gerufen,
nie eine Datei geschrieben, nie ein Kurzbefehl ausgelöst. Wo eine
*Reihenfolge* gemessen wird („Ticket vor der Tat"), steht an der Stelle der
Tat ein Spitzel, der nur mitschreibt, dass er gerufen wurde
(`DBusBroker.lauf` ist injizierbar). Ausführungs-Kanarienvögel liegen bei den
Broker-Tasks.

### K1 — die umgekehrte Mutante, und warum sie zwei Messungen braucht

Der Verifikationsabsatz verlangt, eine Mutante zurückzuweisen, die *Sicherheit
hinzufügt*. Der Grund steht in §6.2 und ist kein Geiz: ein Broker kann nicht
mit einem Schlüssel prüfen, den nur der Hub hat; verteilt man ihn, kann jeder
Broker fälschen; und für den nach §1.3 ausgeschlossenen Angreifer ist er per
`ptrace` ohnehin lesbar. Eine HMAC-Prüfung wäre Scheinsicherheit — teurer als
keine, weil sie Vertrauen erzeugt, das nicht gedeckt ist, und weil sie
verdeckt, dass die Herkunft ausschließlich an der Leitung hängt.

Deshalb misst K1 **zweigleisig**:

* **Am Verhalten.** Ein Auftrag mit genau den acht Feldern wird angenommen;
  ein neuntes Feld `sig` wird abgewiesen (`unbekannte Felder: sig`). Damit
  fällt jede Mutante, die eine Signatur *verlangt*, und eine kann auch nicht
  als „optionale Erweiterung" hineinwachsen.
* **Am Bauteil.** Ein AST-Leser sucht in den neun Dateien des Auftragswegs
  nach Authentifizierungs-Primitiven (`hmac`, `compare_digest`, `nacl`,
  `Fernet`, …). Gelesen wird der **Syntaxbaum, nicht der Text** — der Absatz
  in `order.py`, der den gestrichenen HMAC *erklärt*, ist kein Befund; ein
  `import hmac` ist einer.

Belegt ist, dass beide Gleise Gewicht tragen: Mutant `hmac-optional` prüft
eine Signatur nur, wenn eine mitkommt — am Verhalten fällt das **nicht** auf,
und `kanonisch()` lässt ein `sig`-Feld gar nicht erst durch. Er wird
ausschließlich vom AST-Leser gefangen. Ohne diese Mutante wäre der Leser eine
unbelegte Zutat.

Zwei Positivkontrollen des Lesers stehen **vor** jeder Messung: er findet ein
eingelegtes `import hmac` samt `compare_digest`, und er schlägt *nicht* an
einem Kommentar an, der den gestrichenen HMAC erklärt.

### K5 — die monotone Frist: gemessen, nicht behauptet

Der subtilste Punkt, und er ist ohne Wanduhr-Verstellen und ohne `root`
messbar. Drei unabhängige Messungen:

1. **Der Größenordnungs-Diskriminator.** Die monotone Uhr ist die
   Betriebszeit (10⁴ s), die Wanduhr die Unix-Zeit (10⁹ s). Wer die falsche
   liest, liegt um Jahrzehnte daneben:

   ```
   Frist 7323.472 | monoton+Frist 7323.472 (Abstand 0.000)
                  | wanduhr+Frist 1787036764.562 (Abstand 1787029441.091)
   ```

   Verlangt: Abstand zur monotonen Uhr < 1 s **und** Abstand zur Wanduhr
   > 10⁶ s. Beides einzeln abgerechnet.

2. **Die Zeitumstellung selbst.** `time.time` wird um +10⁶ s vorgestellt →
   das Ticket bleibt gültig. `time.monotonic` wird um Frist+1 s vorgestellt →
   es verfällt. Gewogen:

   ```
   Manipulation 'Wanduhr um +1e6 s vorgestellt vs. monotone Uhr um
   +121 s vorgestellt': True -> False
   ```

   Beide Eingriffe werden im `finally` zurückgenommen, und danach wird
   geprüft, dass das Ticket wieder gültig ist — sonst hätte der Prüfstand
   seine eigene Uhr verbogen und gemessen hätte er sich selbst.

3. **Die Frist an der Prüfung.** `jetzt=999.0` → angenommen, `jetzt=1001.0` →
   „Frist abgelaufen", `jetzt=1000.0` (genau auf der Frist) → abgelaufen. Der
   Rand ist benannt, nicht offen gelassen.

Zusätzlich lesen alle vier Broker die *monotone* Uhr in `pruefe(jetzt=…)` —
mit Positivkontrolle des Lesers an einem eingelegten `time.time()`.

**Warum eingespeiste Uhr statt Wanduhr verstellen:** Die Wanduhr dieser
Maschine zu verstellen bräuchte `root`, träfe fünf laufende Dienste und wäre
nicht restaurierbar-garantiert — nach den Vorab-Festlegungen wäre der Fall
dann *blockiert* zu vermerken statt auszuführen. Der Diskriminator aus (1)
leistet dasselbe ohne diesen Preis: er unterscheidet die beiden Uhren an
ihrem Wertebereich, und der lässt sich nicht wegdefinieren. Ein Prüfstand,
der nur `time.time` patcht, hätte die Lücke „liest gar keine Uhr" — die
schließt (1).

### K6 — die Peer-Prüfung: mit einer echten Leitung, ohne Ausführung

Der Prüfstand bindet einen eigenen Socket in seinem `TemporaryDirectory`,
startet den **echten** `dienst.lauf` darauf und klopft von diesem Prozess an.
Der Broker-Rumpf ist ein Spitzel: er führt nichts aus, er schreibt auf, dass
er gerufen wurde. Gemessen wird genau eine Sache — ob die Leitung geprüft
wird, bevor der Auftrag jemanden erreicht.

```
Eigene Unit dieses Pruefstands: 'app-com.anthropic.Claude-2595.scope'
```

Drei Messungen, mit Wägung: `ipc.peer_of` gerufen? — eine als fremd
aufgelöste Gegenstelle erreicht den Rumpf nicht? — eine als
`daimon-hub.service` aufgelöste Gegenstelle erreicht ihn (Positivkontrolle,
sonst wäre „nicht erreicht" von „die Probe kommt nie an" nicht zu
unterscheiden)? Dazu der Socketmodus 0600 und die AST-Lesung der
Hub-Allowlisten, deren Positivkontrolle `kontext.sock` ist.

### Positivkontrollen

Zu **jeder** Verweigerung steht ein Fall, der angenommen wird. Zusätzlich
vier Apparat-Kontrollen, die vor der jeweiligen Messung laufen: der
Krypto-Leser findet einen eingelegten HMAC und übergeht eine HMAC-*Prosa*;
der `jetzt=`-Leser findet einen eingelegten Uhraufruf; der Reihenfolge-Leser
findet beide Zeilen eines eingelegten Köders; der `Auftragsbuch`-Sucher
findet überhaupt eine Baustelle (`daimon/hub/daemon.py:660`), bevor er
meldet, dass keine außerhalb des Hubs liegt.

**Jede Manipulation wird gewogen.** `waegen()` bricht den Lauf ab, wenn ein
Eingriff nichts verändert hat — Wert- oder Byte-Vergleich vorher/nachher. Bei
den Serialisierungsabweichungen zusätzlich der Nachweis, dass die *Werte*
gleich geblieben sind (`json.loads(a) == json.loads(b)`): sonst maße man
einen anderen Auftrag statt einer anderen Schreibweise.

---

## Kann er den Fehler sehen? — die Mutantenmatrix

> Welche Zeile im Produktivcode müsste kaputt sein, damit er rot wird — und
> hast du es ausprobiert?

**Zwölf Zeilen. Ja, alle zwölf, einzeln gemessen.**

| Mutant | kaputte Zeile | zugeordnet | tatsächlich rot bei |
|---|---|---|---|
| `hmac-pflicht` | `common/order.py:49` `FELDER` + HMAC-Pflichtfeld `sig` | K1 | **K1**, K2, K3, K4, K5, K7 |
| `hmac-optional` | `common/order.py:42` `import hmac` + `compare_digest`, nur bei vorhandenem `sig` gerufen | K1 | **K1** |
| `feld-faellt-weg` | `common/order.py:50` `turn_id` fällt aus `FELDER` | K2 | K1, **K2**, K3, K4, K5, K7 |
| `params-hash-egal` | `common/order.py:139` `erwartet = params_hash(daten["params"])` | K2 | **K2** |
| `audience-egal` | `common/order.py:135` `if daten["audience"] != audience` | K3 | **K3** |
| `schema-egal` | `common/order.py:130` `if daten["schema"] != SCHEMA` | K4 | **K4** |
| `serialisierung-egal` | `common/order.py:121` `if kanonisch(daten) != bytes(roh)` | K4 | **K4** |
| `frist-nach-wanduhr` | `hub/order.py` alle vier `time.monotonic()` → `time.time()` | K5 | **K5** |
| `broker-frist-nach-wanduhr` | `brokers/fs/daemon.py:49` `jetzt=…monotonic()` | K5 | **K5** |
| `peer-egal` | `brokers/dienst.py::lauf`, die Peer-Prüfung | K6 | **K6** |
| `ticket-wieder-einloesbar` | `hub/order.py:79` `if ticket in self._eingeloest` | K7 | **K7** |
| `ticket-nach-der-tat` | `brokers/exec/daemon.py:55` Einlösung hinter `broker.starten()` | K7 | **K7** |

Zehn von zwölf treffen **genau** ihr Kriterium und sonst nichts. Die zwei
breiten Treffer (`hmac-pflicht`, `feld-faellt-weg`) verändern die *Feldmenge*
selbst; da jeder gültige Auftrag aller Kriterien acht Felder trägt, fällt mit
ihr alles. Das ist notiert, nicht wegdefiniert — und beide sind an ihrem
zugeordneten Kriterium ebenfalls rot.

Die Mutanten werden **erzeugt, nicht eingecheckt** —
`tests/mutants/T-4.5/erzeugen.sh` plus `.gitignore`, gerufen von `meta.sh`
selbst. Jeder Anker muss genau so oft im Gut-Muster stehen wie deklariert,
sonst bricht die Erzeugung ab.

---

## Belege

### `meta.sh` — Gut-Muster grün, zwölf Mutanten rot

```
$ bash tests/verify/meta.sh T-4.5
meta[T-4.5]: Mutanten werden erzeugt (…/tests/mutants/T-4.5/erzeugen.sh) ...
T-4.5: 12 Mutanten erzeugt.
meta[T-4.5]: Gut-Muster ...
meta[T-4.5]: Mutante 'audience-egal' erkannt.
meta[T-4.5]: Mutante 'broker-frist-nach-wanduhr' erkannt.
meta[T-4.5]: Mutante 'feld-faellt-weg' erkannt.
meta[T-4.5]: Mutante 'frist-nach-wanduhr' erkannt.
meta[T-4.5]: Mutante 'hmac-optional' erkannt.
meta[T-4.5]: Mutante 'hmac-pflicht' erkannt.
meta[T-4.5]: Mutante 'params-hash-egal' erkannt.
meta[T-4.5]: Mutante 'peer-egal' erkannt.
meta[T-4.5]: Mutante 'schema-egal' erkannt.
meta[T-4.5]: Mutante 'serialisierung-egal' erkannt.
meta[T-4.5]: Mutante 'ticket-nach-der-tat' erkannt.
meta[T-4.5]: Mutante 'ticket-wieder-einloesbar' erkannt.
meta[T-4.5]: 12 Mutanten, alle erkannt.
```

### Zuordnung Mutant → Kriterium, einzeln gemessen

```
$ for m in tests/mutants/T-4.5/*/; do … done
audience-egal                -> FAIL K3
broker-frist-nach-wanduhr    -> FAIL K5
feld-faellt-weg              -> FAIL K1 FAIL K2 FAIL K3 FAIL K4 FAIL K5 FAIL K7
frist-nach-wanduhr           -> FAIL K5
hmac-optional                -> FAIL K1
hmac-pflicht                 -> FAIL K1 FAIL K2 FAIL K3 FAIL K4 FAIL K5 FAIL K7
params-hash-egal             -> FAIL K2
peer-egal                    -> FAIL K6
schema-egal                  -> FAIL K4
serialisierung-egal          -> FAIL K4
ticket-nach-der-tat          -> FAIL K7
ticket-wieder-einloesbar     -> FAIL K7
```

### Gegen das Gut-Muster — grün

```
$ DAIMON_FIXTURE=$PWD/tests/fixtures/known-good/T-4.5 tests/verify/T-4.5.sh; echo $?
…
Eigene Unit dieses Pruefstands: 'app-com.anthropic.Claude-2595.scope'
Peer-Pruefung gerufen: 1x, Rumpf erreicht: 0x, Antwort b''
Manipulation 'Gegenstelle einmal als fremde, einmal als
  `daimon-hub.service` aufgeloest': 0 -> 1
Unit-Allowlisten der Hub-Sockets: {'GPU_SOCKET': False, 'TTS_SOCKET': False,
  'TICKET_SOCKET': False, 'AKTION_SOCKET': True, 'KONTEXT_SOCKET': True}

Bilanz T-4.5:
K1: 14 Pruefungen, 0 rot
K2: 16 Pruefungen, 0 rot
K3: 25 Pruefungen, 0 rot
K4: 18 Pruefungen, 0 rot
K5: 13 Pruefungen, 0 rot
K6: 6 Pruefungen, 0 rot
K7: 23 Pruefungen, 0 rot
0
```

### Gegen den Arbeitsbaum — rot, viermal K6

```
$ tests/verify/T-4.5.sh; echo $?
FAIL K6: der Broker-Socket prueft die Gegenstelle, bevor er eine Zeile
  liest (`ipc.peer_of` gerufen: 0x). Der Auftrag traegt keine Signatur
  (Design 6.2) -- diese Pruefung IST der Herkunftsnachweis
FAIL K6: Zaehne der Peer-Pruefung NICHT gemessen: es findet keine statt
FAIL K6: Positivkontrolle der Probe NICHT gemessen: ohne Peer-Pruefung
  gibt es nichts zu passieren
FAIL K6: `aktion.sock` hat eine Unit-Allowlist (False) -- ueber diesen
  Socket laufen Aktionsbitte UND Ticketeinloesung (T-4.5, Akzeptanzpunkte
  6 und 7)
…
Peer-Pruefung gerufen: 0x, Rumpf erreicht: 1x,
  Antwort b'{"ok": false, "grund": "spitzel"}\n'
Unit-Allowlisten der Hub-Sockets: {'GPU_SOCKET': False, 'TTS_SOCKET': False,
  'TICKET_SOCKET': False, 'AKTION_SOCKET': False, 'KONTEXT_SOCKET': True}

Bilanz T-4.5:
K1: 14 Pruefungen, 0 rot
K2: 16 Pruefungen, 0 rot
K3: 25 Pruefungen, 0 rot
K4: 18 Pruefungen, 0 rot
K5: 13 Pruefungen, 0 rot
K6: 6 Pruefungen, 4 rot
K7: 23 Pruefungen, 0 rot
1
```

**Sechs von sieben Kriterien sind gegen den Arbeitsbaum grün.** Der
Ausführungsauftrag selbst — keine Signatur, acht Felder, `audience`-Bindung,
kanonische Serialisierung, monotone Frist, Ticketeinlösung beim Hub — hält,
was er zusagt. Der Riss liegt an der einen Stelle, die §6.2 ausdrücklich an
die Stelle der gestrichenen Signatur gesetzt hat.

---

## Der Befund

**Akzeptanzpunkt 6 gilt nicht: es gibt keine Peer-Prüfung im Auftragsweg.**

> „Herkunft über den Socket (Peer-Prüfung nach Design §1.3), nicht über
> Kryptografie"

Zwei Stellen, ein Befund an seinen zwei Enden.

### 1. `daimon/brokers/dienst.py:88` — der Broker nimmt jeden an

```python
while True:
    conn, _ = srv.accept()     # <-- keine Peer-Pruefung
    with conn:
        ...
```

`dienst.lauf` ist der **eine** Annahmepunkt aller vier Broker
(`fs/daemon.py:73`, `exec/daemon.py:61`, `input/daemon.py:52`; der
DBus-Daemon hat in `daemon.py:124` dieselbe nackte `srv.accept()`). Im ganzen
Produktivbaum ruft **kein** Broker `ipc.peer_of` oder `ipc.accept`. Gemessen,
nicht gelesen: der Prüfstand hängt einen Spitzel an `ipc.peer_of`, treibt
eine echte Verbindung durch den echten `dienst.lauf` und zählt null Aufrufe —
während der Broker-Rumpf erreicht wird:

```
Peer-Pruefung gerufen: 0x, Rumpf erreicht: 1x
```

### 2. `daimon/hub/daemon.py:1315` — `aktion.sock` ohne Allowlist

```python
t = threading.Thread(target=self._horche_einfach,
                     args=(AKTION_SOCKET, self.aktion_anfrage),
                     kwargs={"liest": True, "nebenlaeufig": True},
                     …)                       # <-- kein erlaubte_units
```

Zum Vergleich, drei Zeilen weiter:

```python
                     kwargs={"liest": True, "erlaubte_units": KONTEXT_UNITS},
```

Die Vorrichtung ist da (`_horche_einfach` nimmt `erlaubte_units` und prüft
per `ipc.peer_of`, `daemon.py:1193–1203`); an `aktion.sock` ist sie nicht
angeschlossen. Über diesen Socket laufen **beide Richtungen** des
Auftragswegs: die Aktionsbitte des Minds hinein (`art: ausfuehren`) und die
Ticketeinlösung des Brokers unmittelbar vor der Ausführung
(`art: ticket_einloesen`, `daemon.py:720`).

Der T-4.4.v-Lauf hatte das Fehlen dieser Allowlist als Nebensatz vermerkt.
Für T-4.5 ist es kein Nebensatz.

### Warum das ein Befund ist und nicht Design

§1.3 endet die Streichung des HMAC mit einem Verweis, und der ist der
Angelpunkt:

> **Konsequenz:** Signaturen zwischen eigenen Prozessen wären Zeremonie — der
> Schlüssel ist für den ausgeschlossenen Angreifer ohnehin lesbar, und **der
> ehrliche Fall läuft bereits über einen direkten Socket.**

Die Signatur ist also nicht *ersatzlos* entfallen — sie ist durch die Leitung
**ersetzt** worden, und §6.2 sagt es noch einmal:

> **Herkunft über den Socket.** Broker nehmen nur Verbindungen vom Hub an
> (Peer-Prüfung im Sinne von §1.2).

„Nehmen nur Verbindungen vom Hub an" ist heute nicht wahr. Sie nehmen jede
an. Damit ruht die Herkunft eines Ausführungsauftrags allein auf dem
Dateimodus 0600 des Sockets.

**Was das nicht ist:** kein Einwand gegen die Streichung des HMAC. Der
Verifizierer bestätigt sie im Gegenteil zwölffach (K1, zwei umgekehrte
Mutanten). Und es schließt weiterhin keinen same-uid-Angreifer aus — das soll
es nach §1.3 auch gar nicht.

**Was es ist:** genau der Fall, den §1.3 der Peer-Prüfung *ausdrücklich*
zuschreibt, fällt aus:

> Es fängt Verwechslung, Fehlkonfiguration und einen kompromittierten
> *eigenen* Prozess, der sich als anderer ausgeben will.

Ein falsch verdrahteter Dienst, ein Broker mit vertauschtem `--socket`, ein
zweiter Hub aus einem anderen Checkout — nichts davon fällt auf. Auf dieser
Maschine laufen zwei Checkouts nebeneinander (`~/Dokumente/Github/dAImon`
liefert die aktiven Units, gemessen wurde dieser Worktree); genau die
Verwechslung, die die Prüfung fangen soll, ist hier keine Theorie.

### Der Zulauf — die Frage vor jedem „fertig"

| Ende | Zulauf heute | Beleg |
|---|---|---|
| Broker-Sockets | Units existieren (`config/systemd/daimon-{fs,dbus,exec,input}.service`), laufen aber nicht; `dbus-broker.sock`/`fs-broker.sock` liegen als **tote Dateien** in `/run/user/1000/daimon/` — sie haben also gelaufen | `ss -xlp` zeigt keinen Horcher darauf |
| `aktion.sock` | **hört jetzt zu** — der laufende Hub bindet ihn | `ss -xlp`: `u_str LISTEN 0 8 /run/user/1000/daimon/aktion.sock` |

Die zweite Zeile ist der Unterschied zu T-4.4: dort war der Befund eine
scharfe Kante *ohne* Zulauf. Hier hört ein Socket ohne Herkunftsprüfung
gerade zu. Was er heute annimmt, ist begrenzt (`art: ausfuehren` schickt im
Betrieb noch niemand, `mind/router.py:507` lehnt Aktionsbitten ab), aber
`art: ticket_einloesen` und `art: offene` sind erreichbar, und die Kante wird
mit T-4.16/T-4.19 scharf.

### Reparaturweg (nicht vom Verifizierer vorgeschrieben)

Das Gut-Muster zeigt **eine** Fassung, die grün wird: eine Allowlist im
Dienstmantel (`HUB_UNITS`) und eine an `aktion.sock` (`AKTION_UNITS` = Mind
plus die vier Broker aus `BROKER_SOCKETS`). Die Messung schreibt das nicht
vor. Sie prüft, dass `ipc.peer_of` auf dem Weg zum Broker-Rumpf gerufen wird
und dass eine abgelehnte Gegenstelle den Rumpf nicht erreicht. Wer die
Prüfung anders aufhängt — je Broker, über `ipc.accept` statt `srv.accept`,
über einen Socket mit anderem Besitzer — wird ebenso grün.

Die Prüfung im Dienstmantel und nicht viermal in den Brokern ist ein
bewusster Zuschnitt des Gut-Musters: vier Kopien wären vier Stellen, an denen
sie einzeln vergessen werden kann — Regel 4 aus `CLAUDE.md`.

---

## Grenzen — was er NICHT misst

1. **Keine Ausführung, absichtlich.** Kein `gdbus`, keine Datei, kein
   Kurzbefehl. Die K7-Reihenfolgemessung sieht, dass `DBusBroker.lauf` bei
   abgelehntem Ticket *nicht gerufen* wird — nicht, dass der Aufruf sonst
   wirkt. Die drei Mantel-Broker (fs/exec/input) sind bei der Reihenfolge
   **strukturell** gemessen (AST: Zeile der Einlösung < Zeile der Tat), nicht
   am Verhalten. Ausführungs-Kanarienvögel liegen bei T-4.7 ff.
2. **`aktion.sock` ist gelesen, nicht getrieben.** Die fehlende Allowlist ist
   per AST am Aufrufort belegt, mit `kontext.sock` als Positivkontrolle des
   Lesers. Am *laufenden* Hub wurde nicht angeklopft — das wäre ein Eingriff
   in einen produktiven Dienst, und der Auftrag verbietet ihn. Ungemessen
   bleibt damit, was der laufende Hub einem fremden Absender tatsächlich
   antwortet.
3. **Die Peer-Prüfung wird an einem eigenen Socket gemessen, nicht am
   Broker-Socket im Betrieb.** Der Prüfstand bindet `…/spur.sock` in seinem
   Tempverzeichnis und treibt den echten `dienst.lauf` darauf. Ungemessen:
   Nebenläufigkeit, Verhalten bei abgebrochener Verbindung, die
   Größengrenze `MAX_BYTES`, und ob `systemd` den Socket im Betrieb anders
   anlegt (Socket-Activation gibt es für die Broker nicht — sie binden
   selbst).
4. **`SO_PEERPIDFD` selbst ist nicht geprüft.** K6 misst, *dass* `peer_of`
   gerufen wird und dass sein Urteil trägt. Ob `peer_of` korrekt auflöst, ist
   T-1.2/T-2.7 und hat dort eigene Tests. Ein Kernel ohne `SO_PEERPIDFD`
   ließe `peer_of` werfen — der Gut-Muster-Pfad schlösse dann jede
   Verbindung, was hier als grün durchginge, im Betrieb aber ein toter Broker
   wäre.
5. **Die Uhren sind eingespeist bzw. gepatcht.** Ein Fehler, der erst durch
   das Zusammenspiel *zweier* echter Zeitquellen entstünde — Hub setzt die
   Frist monoton, ein Broker misst gegen `CLOCK_BOOTTIME` — fällt nicht auf.
   Gemessen ist nur: dieselbe *Funktion* (`time.monotonic`) auf beiden
   Seiten. Über einen Suspend hinweg laufen `monotonic` und `boottime`
   auseinander; das ist hier weder gemessen noch entschieden.
6. **Die Frist überlebt keinen Neustart, und das wird nicht geprüft.** Das
   Auftragsbuch liegt nur im Speicher (`hub/order.py`, Modulkopf). Ob ein
   Hub-Neustart offene Aufträge sauber verfallen lässt oder ein Broker mit
   einem Ticket dasteht, das niemand mehr kennt, ist ungemessen.
7. **Die kanonische Form ist gepinnt, nicht hergeleitet.** K2/K4 rechnen
   `sha256` über `json.dumps(sort_keys=True, separators=(",",":"),
   ensure_ascii=False)`. Ändert jemand die Kanonisierung *absichtlich*, wird
   dieser Verifizierer rot, ohne dass ein Fehler vorläge — das ist gewollt
   (jemand soll hinsehen), aber kein Beweis, dass diese Form die richtige
   ist. Ungeprüft bleibt insbesondere, ob `float`-Werte über
   Sprachgrenzen hinweg gleich serialisiert werden; heute liest nur Python.
8. **`params` selbst wird nicht bewertet.** K2 misst, dass `params_hash` die
   Parameter *bindet* — nicht, ob die Parameter zulässig sind. Das ist die
   Policy (T-4.4) und die Argumentvalidierung im Broker (T-4.7 ff.).
9. **Der Krypto-Leser ist eine Namensliste.** Er findet `hmac`,
   `compare_digest`, `nacl`, `Fernet` und Verwandte im Syntaxbaum der neun
   Dateien des Auftragswegs. Eine von Hand geschriebene Signatur ohne diese
   Namen — ein selbstgebauter Keyed-Digest über `hashlib.sha256` — fände er
   nicht. Dagegen steht die zweite Messung (die geschlossene Feldmenge: eine
   Signatur bräuchte einen Träger, und ein neuntes Feld wird abgewiesen),
   aber lückenlos ist das nicht.
10. **Der Auftragsweg ist eine Liste von neun Dateien.** Wächst er, wächst
    sie nicht mit. Wer einen fünften Broker baut, muss `AUFTRAGSWEG` und
    `MANTEL_BROKER` im Prüfstand ergänzen — sonst misst K1 dort nichts und
    meldet trotzdem grün. Das ist die bekannte Decke dieser Bauform.
11. **Das Gut-Muster ist eine Reviewer-Fassung.** Es enthält zwei
    Abweichungen vom Arbeitsbaum (`HERKUNFT.txt`). Sie sind die
    Positivkontrolle des Verifizierers, kein Reparaturvorschlag und kein
    Produktivcode.

---

## Rücksicht auf den laufenden Betrieb

Kein `systemctl`-Aufruf, kein `kill`, kein Anhalten, kein Anklopfen an einem
produktiven Socket. Der Prüfstand legt alles in ein
`tempfile.TemporaryDirectory`; die drei Socket-Proben binden dort und werden
danach entfernt (`ls /tmp | grep t45-` → leer). Er schreibt nichts ins
Journal, nichts ins Audit, nichts in `$XDG_STATE_HOME`.

Vor und nach dem Lauf unverändert aktiv: `daimon-hub`, `daimon-auth`,
`daimon-face`, `daimon-focus`, `daimon-hookbridge` sowie die drei
`.socket`-Units und zwei Timer. Sie laufen gegen ein anderes Checkout
(`~/Dokumente/Github/dAImon`), nicht gegen diesen Worktree. `daimon-mind`,
`daimon-ears`, `daimon-eyes` und `daimon-recorder` waren beim Sitzungsbeginn
bereits inaktiv — vorgefunden, nicht von dieser Sitzung gestoppt.

`.claude/hooks/**` unberührt. `freeze.sh` nicht aufgerufen. `git status`
listet ausschließlich die vier neuen `T-4.5`-Pfade; die Bäume der parallel
laufenden Sitzung (`T-7.1`, `T-7.2`, `T-7.5`, `T-4.4`, `T-4.6`, `T-5.9`)
sind nicht angefasst.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, Einfrieren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main` |
| Ausgangs-Commit | `9034ce3` (nach dem Einfrieren von T-4.4) |
| Verifizierer unverändert | `T-4.5.sh` `b7b86cb6…`, `t45_pruefstand.py` `f05dec35…` |

Wie bei T-4.4: der uncommittete `FROZEN`-Eintrag der abgebrochenen Sitzung
war kein Beleg. Zurückgenommen, von vorn gemessen.

### 1. Gegen `main` — grün

```
$ env -u DAIMON_FIXTURE tests/verify/T-4.5.sh; echo $?
Bilanz T-4.5:
K1: 14 Pruefungen, 0 rot    K5: 13 Pruefungen, 0 rot
K2: 16 Pruefungen, 0 rot    K6:  6 Pruefungen, 0 rot
K3: 25 Pruefungen, 0 rot    K7: 23 Pruefungen, 0 rot
K4: 18 Pruefungen, 0 rot
0
```

115 Prüfungen, keine rot. K6 war am 18.08. das rote Kriterium; die Belege
heute:

```
Eigene Unit dieses Pruefstands: 'app-com.anthropic.Claude-58898.scope'
Peer-Pruefung gerufen: 1x, Rumpf erreicht: 0x, Antwort b''
Manipulation 'Gegenstelle einmal als fremde, einmal als
              `daimon-hub.service` aufgeloest': 0 -> 1
Unit-Allowlisten der Hub-Sockets: {'GPU_SOCKET': True, 'TTS_SOCKET': True,
  'TICKET_SOCKET': True, 'AKTION_SOCKET': True, 'KONTEXT_SOCKET': True,
  'datei': False}
```

Die Peer-Prüfung findet statt (`590ee02`), sie hat Zähne (eine fremde
Gegenstelle erreicht den Broker-Rumpf **nicht**, `daimon-hub.service` sehr
wohl), und `aktion.sock` trägt seit `d5c012b` eine Unit-Allowlist.

**Die Kriterien gelesen, nicht nur den Ausgang** (Übergabe §4.1). Der Fix ist
nicht auf die gemessene Stelle zugeschnitten: `brokers/dienst.py:76` ruft
`ipc.accept(..., erlaubte_units=(HUB_UNIT,))`, also die **eine** Fassung der
Regel, die auch `kontext.sock` benutzt — nicht eine zweite daneben.

### 2. Gegen das Gut-Muster und alle zwölf Mutanten

```
$ bash tests/verify/meta.sh T-4.5
T-4.5: 12 Mutanten erzeugt.
meta[T-4.5]: Gut-Muster ...
… audience-egal · broker-frist-nach-wanduhr · feld-faellt-weg ·
  frist-nach-wanduhr · hmac-optional · hmac-pflicht · params-hash-egal ·
  peer-egal · schema-egal · serialisierung-egal · ticket-nach-der-tat ·
  ticket-wieder-einloesbar — alle erkannt.
meta[T-4.5]: 12 Mutanten, alle erkannt.
```

Die beiden **umgekehrten** Mutanten (`hmac-pflicht`, `hmac-optional`), die
Kryptografie *hinzufügen*, werden weiter erkannt — der AST-Leser aus K1 ist
nicht stumpf geworden. Und `peer-egal` — der Mutant, der genau den Zustand
von gestern wiederherstellt — wird erkannt.

### 3. Was dieser Nachlauf offenlässt

Die Grenzen des Ledgers gelten unverändert. Zusätzlich hat der Nachlauf
**zwei Stellen gesehen, die dieser Verifizierer nicht misst** und die
deshalb hier stehen, statt still zu bleiben:

1. **`horch_aufrufe` liest nur `_horche_einfach`.** Die vier
   Produzentensockets (`hookbridge`, `face`, `auth`, `ears`) entstehen in
   `_horche_produzent` (`daimon/hub/daemon.py:555–574`) und bekommen dort
   **kein** `erlaubte_units`. Der Reader kann das nicht sehen. Gemessen am
   laufenden System, mit Positivkontrolle — siehe Bericht der Sitzung,
   Befund 1.
2. **Der Eintrag `'datei': False`** in der Ausgabe oben ist die Schleife für
   `state.sock` und `diag.sock`. Beide sind absichtlich ohne Liste (lesende
   Diagnose); der Reader kann sie aber nicht auseinanderhalten, weil er den
   Namen der Schleifenvariablen sieht. Wer dort einen dritten, schreibenden
   Endpunkt einhängt, fällt K6 nicht auf.
