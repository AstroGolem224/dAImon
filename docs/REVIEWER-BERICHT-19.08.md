# Reviewer-Bericht, 19.08.2026 — Einfrieren der elf Phase-4/7-Verifizierer

Antwort auf `docs/REVIEWER-UEBERGABE-19.08.md`. Rolle `reviewer`, kein
Produktivcode geschrieben. Ausgangs-Commit `029a557`, Ergebnis `774e6d9`.

---

## 0. Die kurze Antwort

**Sechs von elf sind eingefroren, fünf nicht — und alle fünf sind rot, keiner
ist „nicht messbar".** Die Umgebung hat für alle elf gereicht: Compositor,
PipeWire, DBus, systemd, `strace` waren da; kein Kriterium ist wegen fehlender
Umgebung ungemessen geblieben.

| | eingefroren | Lauf gegen `main` | Mutanten |
|---|---|---|---|
| T-4.4 | ✅ | 78 Prüfungen, 0 rot | 9/9 erkannt |
| T-4.5 | ✅ | 115 Prüfungen, 0 rot | 12/12 erkannt |
| T-4.6 | ❌ **rot** | 91 Prüfungen, **1 rot** (K5) | 4/4 erkannt |
| T-4.7 | ❌ **rot** | 40 Prüfungen, **6 rot** (K3, K4, K5) | 12/12 erkannt |
| T-4.8 | ❌ **rot** | 53 Prüfungen, **2 rot** (K7) | 12/12 erkannt |
| T-5.9 | ❌ **rot** | 115 Prüfungen, **1 rot** (K9) | 12/12 erkannt |
| T-7.1 | ✅ | 126 Prüfungen, 0 rot | 13/13 erkannt |
| T-7.2 | ✅ | 85 Prüfungen, 0 rot | 8/8 erkannt |
| T-7.3 | ✅ | 10/10 Kriterien | 13/13 erkannt |
| T-7.4 | ❌ **rot** | 9 Kriterien, **1 rot** (K3) | 10/10 erkannt |
| T-7.5 | ✅ | 110 Prüfungen, 0 rot | 11/11 erkannt |

`tests/verify/FROZEN` trägt jetzt 52 Einträge (vorher 37): die sechs
Verifizierer plus ihre neun Helfer. `verify-frozen.sh` läuft grün
(„52 eingefrorene Dateien unveraendert; Abhaengigkeiten geschlossen").

**Keiner der elf ist blind geworden.** Alle 116 Mutanten werden weiter
erkannt, einschließlich der sechs, die genau den Zustand vor dem jeweiligen
Fix wiederherstellen (`naht-quelle-vom-absender`, `peer-egal`,
`eyes-darf-schreiben`, `kennung-ueber-rohklasse`, `pause-schaltet-stumm`,
`pause-laesst-den-ton-offen`). Das war die Frage aus Übergabe §4.1, und die
Antwort ist: die Fixes sind an der gemessenen Stelle nicht zu freundlich.

---

## 1. Der Zwischenstand der abgebrochenen Sitzung — welche der beiden Möglichkeiten es war

Die Übergabe nannte zwei: (a) gefahren, aber vor dem Ledger gestorben, oder
(b) eingetragen, ohne zu fahren.

**Nachträglich sieht es nach (a) aus** — die sechs eingetragenen sind genau
die sechs, die heute grün laufen, und die fünf roten hatte sie nicht
eingetragen. Das ist ein starkes Indiz und **kein Beleg**; ich habe den
Eintrag deshalb wie angewiesen als offene Frage behandelt: `tests/verify/FROZEN`
auf den committeten Stand zurückgesetzt und alle sechs von vorn erarbeitet.

Was von ihrem Zwischenstand übernommen ist: die **Deklarationen** in
`tests/verify/FROZEN.deps`. Sie sind keine Messung, sondern die Aussage, welche
Helfer ein Verifizierer aufruft; ohne sie lehnt `freeze.sh` schon die statische
Entdeckung ab. Jede der fünfzehn Kanten ist beim Einfrieren von `freeze.sh`
gegen die Laufzeitspur geprüft worden („ohne undeklarierte Helfer").

Zurückgesetzt habe ich außerdem vier Messartefakte, die ihr Lauf überschrieben
hatte (`T-3.14-ptt-latenz.json`, `T-3.9-ttfa.json`, `phase1-usage.json`,
`phase6-integration.json`). In `phase6-integration.json` fehlte ein ganzes
Szenario, und die Datei trug `"rolle": "reviewer"` — ein halber Lauf, der
einen ganzen ersetzt hätte.

---

## 2. Was nicht eingefroren ist, und warum — fünfmal „rot", nullmal „nicht messbar"

Jeder Befund unten ist gegen `main` reproduziert und am Quelltext nachgelesen.

### 2.1 T-4.6 K5 — die gerissene Audit-Kette meldet sich nur im Journal

`daimon/hub/daemon.py:1176`

```python
kette_kaputt = [f for f in befund["fehler"] if "Anker" not in f]
if kette_kaputt:
    self.log.warn("AUDIT-KETTE GERISSEN", …)
```

`docs/DESIGN.md:1034` verlangt: „meldet eine Abweichung als **Bubble mit hoher
Dringlichkeit**". Es gibt auf diesem Weg keinen `set_bubble`-Aufruf.

**Fehlerszenario:** jemand ändert eine Zeile in
`$XDG_STATE_HOME/daimon/audit/`. Beim nächsten Hub-Start steht eine Warnung im
Journal, die niemand liest; das Overlay bleibt ruhig. Die Zusage ist, dass der
Nutzer es *sieht*.

Beide Klammern der Messung halten: die Vorbedingung („die Kette, die der Hub
gleich liest, ist nachweislich gerissen") ist grün, und die Positivkontrolle
nach der Messung — eine über `hookbridge.sock` gesetzte dringende Blase — ist
im Schnappschuss sichtbar. `bubble=None` ist damit von „der Apparat sieht nie
eine Blase" unterschieden.

### 2.2 T-4.7 K3/K4 — die zweite Schicht ist konfiguriert und wird nie gestartet

`config/dbus-filter.conf` sagt in seinem eigenen Kopf:

> Aufruf (steht so in `config/systemd/daimon-dbus.service`):
> `xdg-dbus-proxy --args=<fd> …`

**Er steht dort nicht.** Über den ganzen Baum gemessen kommt `xdg-dbus-proxy`
in `daimon/brokers/dbus/broker.py:18` (Kommentar), `docs/broker-sandboxes.md:8`,
`docs/DESIGN.md:171/833/1030` und `docs/IMPLEMENTATION-PLAN.md:1267` vor — in
**keiner** Unit und in **keiner** startenden Codezeile.

**Fehlerszenario:** der DBus-Broker hat einen Fehler in seiner
Argumentvalidierung. Die zweite Schicht, die genau dafür da ist, existiert im
Betrieb nicht; der Broker spricht direkt mit dem Sitzungsbus. K4 ist rot als
Folge: eine Schicht, die niemand startet, kann auch nichts abweisen.

Das ist das Muster aus `CLAUDE.md` §„Prüfe den Zulauf, nicht nur das Stück" —
diesmal ist es die **Konfigurationsdatei**, die den nicht existierenden
Aufrufer beschreibt.

### 2.3 T-4.7 K5 — und eine Korrektur an einem der Belege

`config/systemd/daimon-dbus.service:30` setzt `PrivateUsers=yes`;
`docs/DESIGN.md:1020` führt genau diese Zeile unter den „Direktiven, die
brechen" — „bricht uid-ACLs und **Peer-Credentials**".
`CapabilityBoundingSet=` (Basiszeile, `docs/DESIGN.md:994`) fehlt der Unit
tatsächlich.

**Die Begründung habe ich nachgemessen, statt sie zu übernehmen.** Zwei
transiente Units, sonst gleich, ein Client aus einer fremden Unit:

```
ohne PrivateUsers   peercred {'pid': 829575, 'uid': 1000, 'gid': 1000}
                    SO_PEERPIDFD ok, cgroup lesbar
mit  PrivateUsers   peercred {'pid': 829591, 'uid': 1000, 'gid': 1000}
                    SO_PEERPIDFD ok, cgroup lesbar
```

systemd bildet die eigene uid des Dienstes in den Namensraum ab; für einen
Peer **derselben** uid ändert `PrivateUsers=yes` nichts, und `ipc.peer_of`
löst weiter auf. Gemessen ist nur der Peer-Teil und nur für gleiche uid — was
die Direktive sonst bricht, ist damit nicht widerlegt.

**Das rote Kriterium bleibt trotzdem.** Zwei Aussagen über dieselbe Zeile
stehen im Repo, und eine von beiden ist falsch. Entweder gehört
`PrivateUsers=yes` aus der Unit, oder die Begründung in `docs/DESIGN.md:1020`
gehört korrigiert — was nicht bleiben kann, ist beides nebeneinander.

### 2.4 T-4.8 K7 — der Undo-Broker hat außerhalb der Tests keinen Aufrufer

`daimon/brokers/fs/undo.py` trägt `vorbereiten` (Z. 184), `in_den_trash`
(Z. 89), `kopie_anlegen` (Z. 133), `stash_anlegen` (Z. 157). Einziger Aufrufer
im Produktbaum ist `vorbereiten` selbst (Z. 191/193/195, der Dispatch auf die
drei anderen).

**Fehlerszenario:** eine genehmigte Mutation läuft. `self.undo is None`, der
Undo-Hop wird übersprungen, die Mutation läuft **ohne Rückholpunkt** durch.
Die Zusage „schlägt die Vorbereitung fehl, wird die Mutation abgebrochen" hat
im Betrieb keinen Fall, in dem sie greift.

Das ist der Fehler aus der Tabelle in `CLAUDE.md`, ein siebtes Mal. Bemerkens-
wert daran: **die 20 Prüfungen unter K5 sind grün** — `pytest` am Stück sieht
nichts. Rot wird nur die Naht.

### 2.5 T-5.9 K9 — der Umfang fährt in einem redigierten Feld mit

`daimon/hub/declassify.py:195`

```python
prompt_shown=f"{grund} {sorted(umfang.items())}",
tainted=("prompt_shown",)
```

Der Umfang reist als Teil von `prompt_shown` mit, und `prompt_shown` ist als
`tainted` deklariert. Die Redaktion ersetzt das **ganze Feld** durch
`<redacted:sha256:…>` — mitsamt dem Umfang, der dort nur mitgefahren ist.
Geschriebener Satz aus dem Lauf:

```json
{"action_id": "context.declassify", "initiator": "foreground",
 "mark_id": "f758fc4f…", "turn_id": "f758fc4f…", "outcome": "ok",
 "prompt_shown": "<redacted:sha256:89e2d324…:len=89>"}
```

Beide Zeilen sind für sich richtig: `prompt_shown` **gehört** redigiert, es
trägt die Äußerung des Nutzers. Falsch ist, dass ein Pflichtfeld der
Akzeptanzliste („mit Umfang und turn_id") darin transportiert wird.

**Fehlerszenario:** eine Deklassifizierung wird freigegeben. Wer später fragt,
*was* freigegeben wurde — nur der Titel? auch das OCR? auch das VLM-Bild? —
findet `initiator`, `turn_id` und einen Hash. Die Frage „was hat das Modell an
diesem Tag zu sehen bekommen" ist aus dem Audit nicht beantwortbar, und genau
dafür gibt es das Audit.

Gemessen mit Sollwert daneben: `gefunden [] von [('ocr', 1), ('titel', 0),
('vlm', 0)]`. Es ist nicht bloß „nichts gefunden".

### 2.6 T-7.4 K3 — der Leerlauf-Exit steht zweimal im Repo, gegensätzlich

Von den zwei roten Kriterien vom 18.08. ist K7 mit `d521148` zu. K3 bleibt,
und es ist **kein Versehen, sondern ein Widerspruch**:

| Fassung | sagt |
|---|---|
| `docs/DESIGN.md:382` | „bei Stille **beendet er sich wie gehabt**" |
| `daimon/gpu/stt.py:24` | Abschnitt **„Kein Leerlauf-Exit"** — kein VRAM zurückzugeben, 843 ms Ladezeit, „das Modell im Speicher IST die Latenzzusage" |
| `daimon/gpu/stt.py:384` | `while True` ohne Frist, Docstring „Kein Leerlauf-Exit — siehe Modulkopf" |

Beide Fassungen tragen eine Begründung, und beide sind für sich tragfähig.
Nicht tragfähig ist ihr Nebeneinander — welche gilt, entscheidet der Zufall
dessen, der zuerst nachschlägt.

**Fehlerszenario, falls DESIGN §382 gilt:** der STT-Dienst wird beim ersten
Wort socket-aktiviert und läuft danach unbegrenzt mit parakeet-tdt-0.6b-v3 im
Speicher, bis jemand die Unit stoppt; die Residenzpolitik aus §5.4 hat für
ihn keinen Fall, in dem sie greift. **Falls `stt.py` gilt:** DESIGN §382 und
Akzeptanzpunkt 2 von T-7.4 beschreiben Verhalten, das es nicht gibt, und jeder
künftige Verifizierer misst dagegen — so wie dieser.

Die Entscheidung gehört nicht in einen Prüfstand. Solange die Akzeptanzliste
steht, ist der Verifizierer zu Recht rot.

---

## 3. Zwei Befunde, die kein Verifizierer misst

Beide kommen aus den Stellen, an denen die Übergabe um Nachsehen gebeten hat
(§4.2, §4.3).

### Befund 1 — vier Hub-Sockets ohne Unit-Allowlist, darunter der, der Rundenmarken ausgibt

`daimon/hub/daemon.py:555` (`_horche_produzent`) ruft `ipc.accept(...)` **ohne**
`erlaubte_units`. Betroffen sind die vier Produzenten aus Zeile 1427:
`hookbridge`, `face`, `auth`, `ears`.

Die Allowlisten vom 19.08. (`d5c012b`) sitzen ausschließlich in der **anderen**
Annahmefunktion, `_horche_einfach` (Z. 1287–1341). Der Kommentar dort spricht
von „FUENF Endpunkte" und liest sich wie eine Aussage über den Hub; er ist eine
über eine seiner beiden Türen. Übergabe §4.2 vermutete die Lücke bei einem
Absender, der den Socketpfad zusammensetzt — den gibt es nicht (gemessen: jede
Stelle nennt den Dateinamen wörtlich, auch `auth/agent.py:330` und
`mind/router.py:302`, die `.parent /` bzw. `.with_name()` benutzen). Die Lücke
ist eine ganze Funktion.

**Am laufenden System gemessen, mit Positiv- und Unterscheidungskontrolle.**
Verbinden und sofort wieder gehen, ohne eine Zeile zu senden; aus
`app-com.anthropic.Claude-58898.scope`:

```
kontext.sock   Anfrage von fremder Unit    Unit 'app-com.anthropic.Claude-58898.scope'
aktion.sock    Anfrage von fremder Unit    Unit 'app-com.anthropic.Claude-58898.scope'
ticket.sock    Anfrage von fremder Unit    Unit 'app-com.anthropic.Claude-58898.scope'
auth           ipc  angenommen             app-com.anthropic.Claude-58898.scope
face           ipc  angenommen             app-com.anthropic.Claude-58898.scope
ears           ipc  angenommen             app-com.anthropic.Claude-58898.scope
hookbridge     ipc  angenommen             app-com.anthropic.Claude-58898.scope
```

Die drei Abweisungen sind die Positivkontrolle: die Vorrichtung funktioniert
und das Journal nennt die abgewiesene Unit beim Namen. Die Zeile
`hookbridge ipc angenommen daimon-hookbridge.service` aus demselben Zeitfenster
ist die Unterscheidungskontrolle: „angenommen" ist nicht das, was der Hub zu
allem sagt.

**Warum `auth.sock` der wichtige ist.** `daimon/hub/daemon.py:529–535`:

```python
if event.type == "intent_mark":
    self.marken.ausgeben(quelle="auth", turn_id=secrets.token_hex(16))
```

Eine Zeile `{"v":1,"type":"intent_mark","payload":{}}` an `auth.sock` lässt den
Hub eine **frische Rundenmarke** ausgeben. Auf dieser Marke steht die halbe
Vertrauenskette des Projekts: `daemon.py:882–888` liest sie als
`initiator="user"`, T-4.4 K3 misst „`initiator` aus der eingelösten
Rundenmarke", und T-7.5/T-5.9 machen die Archivsuche und die
Deklassifizierung davon abhängig. `aktion.sock` hat seit `d5c012b` eine Liste;
der Socket, der die Marke ausgibt, auf die `aktion.sock` sich verlässt, hat
keine.

`face.sock` ist der zweite: `wahrnehmung_aus` führt über `daemon.py:413` zu
einem echten `systemctl --user stop`. Der Code begrenzt den Schaden bewusst
(Ziel aus einer festen Menge, Unitname aus der Konfiguration, nie aus der
Nachricht) — der schlimmste Fall ist „Ohren oder Augen gehen aus".

**Was das ist und was nicht.** Kein Bruch des Bedrohungsmodells:
`docs/DESIGN.md` §1.3 sagt seit `a58d08b` ausdrücklich, dass es gegen einen
same-uid-Angreifer keinen Herkunftsnachweis gibt, und die Peer-Prüfung ist
erklärtermaßen ein **Wegweiser**. Der Befund ist, dass der Wegweiser an den
fünf unwichtigeren Türen steht und an den vier wichtigeren nicht — und dass
der Kommentar bei `daemon.py:1315` das Gegenteil nahelegt. Nach demselben
Maßstab, mit dem `d5c012b` begründet wurde („einen falsch verdrahteten eigenen
Dienst aufhalten und im Nachhinein sichtbar machen, wer gefragt hat"), gehören
`AUTH_UNITS = ("daimon-auth.service",)` und
`FACE_UNITS = ("daimon-face.service",)` dazu.

**Nachtrag zu Übergabe §4.3** (`ipc.unit_erlaubt` prüft den Instanzteil nicht):
gelesen, für vertretbar befunden, kein eigener Befund. `daimon-gpu@` ist der
einzige Template-Eintrag, das Präfix verlangt zusätzlich `.service`, und wer
eine `daimon-gpu@beliebig.service` anlegen kann, läuft schon unter dieser uid.
Solange die Peer-Prüfung ein Wegweiser ist, ist das die richtige Kosten-Nutzen-
Lage; die Zeile ist in `ipc.py:319` auch so kommentiert.

### Befund 2 — der laufende Hub ist älter als zwei Fixes von heute

`daimon-hub.service` läuft seit `Wed 2026-08-19 12:02:37 CEST`. Danach kamen:

| Commit | Zeit | was fehlt im laufenden Prozess |
|---|---|---|
| `fe4a33a` | 12:09 | `ipc._unit` traf die richtige Unit nur bei gerader Segmentzahl |
| `91e59aa` | 13:00 | der Privatmodus war nicht einschaltbar |

**`fe4a33a` ist der schwerere.** Die Funktion trägt jede Peer-Prüfung im
Projekt, einschließlich der fünf Allowlisten aus `d5c012b`. Der laufende Hub
prüft also mit dem kaputten Regex — für `daimon-fs.service` fällt zufällig das
Richtige heraus, für die Template-Instanz `daimon-gpu@…` die Slice darüber.
Wirkung: ein Dienst wird abgewiesen, wo er dürfte.

**`91e59aa`** heißt: der Privatmodus — nach `d521148`/`ae7c72e` die **einzige**
Sperre des Tonpfads, denn die Anwendungs-Denylist sperrt Fenster und ein
gesprochener Satz hat keines — ist im Repo belegt und im laufenden Prozess
nicht einschaltbar. Wer heute ein Passwort diktiert, hat es im Archiv.

Dasselbe Muster wie in Übergabe §4.4, zweiter Punkt („T-4.4 wurde um 00:24
committet, der Hub-Prozess lief bis 10:07 mit dem alten Code weiter"), nur mit
anderen Commits. **Ein `systemctl --user restart daimon-hub` schließt beides**
— ich habe es nicht getan; Dienste umzustellen war nicht mein Auftrag, und die
Entscheidung gehört Matthias.

Betriebslage der übrigen: `daimon-fs` (12:02:37) ist **nach** `6cdf0e4`
gestartet und trägt die reparierte Sandbox; `daimon-eyes` (15:15:02, von der
abgebrochenen Sitzung neu gestartet) trägt die eine Denylist-Fassung aus
`ae7c72e`.

---

## 4. Kleinkram, der beim Nachlesen aufgefallen ist

* **`LEDGER-T-4.4.v.md` und `LEDGER-T-4.5.v.md` behaupten, die laufenden Units
  liefen „gegen ein anderes Checkout (`~/Dokumente/Github/dAImon`)".** Das ist
  falsch: `~/Dokumente/Github/dAImon` ist ein Symlink auf
  `/mnt/data/AI/repos/dAImon`, also **dieses** Repo. Beide Ledger sind sonst
  belastbar, aber die Beruhigung an dieser Stelle trägt nicht — und es ist
  genau die Verwechslung, die Übergabe §4.4 als teuer beschreibt.
* **`t45_pruefstand.py` K6 meldet `'datei': False`.** Das ist die Schleife für
  `state.sock` und `diag.sock` (`daemon.py:1431`), beide absichtlich ohne
  Liste. Der AST-Leser sieht den Namen der Schleifenvariablen und kann die
  beiden nicht auseinanderhalten; wer dort einen dritten, schreibenden Endpunkt
  einhängt, fällt K6 nicht auf. Im Ledger vermerkt.
* **Im Arbeitsbaum liegen Reste der abgebrochenen Sitzung**, die ich nicht
  angefasst habe: acht `tests/evidence/T-3.9-ttfa-fixture-*.json` und
  `spikes/tts-abkuehlung/{conf,state}/`, alle unversioniert.
  `tests/evidence/phase1-usage.json` ist geändert, weil `daimon-phase1.timer`
  alle fünf Minuten hineinschreibt — das ist der laufende Betrieb und nicht
  diese Sitzung.

---

## 5. Rücksicht auf den laufenden Betrieb

* **Keine Portal-Zustimmung ausgelöst.** `daimon-eyes` ist nicht neu gestartet
  worden und läuft unverändert seit 15:15:02.
* **Kein Dienst umgestellt.** Kein `systemctl start|stop|restart` an einer
  `daimon-*`-Unit. Die drei Prüfstände, die `systemctl` brauchen, haben ihre
  Vorschalter gemeldet: T-7.3 „3 Aufrufe durchgereicht, kein Aufruf an eine
  echte Unit", T-7.4 „0 Aufrufe", T-4.8 „48 Aufrufe `{'cp': 9, 'gio': 1,
  'git': 38}`, keiner an `systemctl`/`trash`". T-7.1 und T-4.7 haben
  ausschließlich eigene transiente Units (`t71v-rec-<pid>`, `t47pu-*`)
  gestartet und wieder gestoppt.
* **Die Sonde aus Befund 1** hat sieben Verbindungen aufgebaut und sofort
  wieder geschlossen, ohne eine Zeile zu senden. Sie hat keinen Zustand
  geändert; belegt ist sie ausschließlich über die Annahme-/Abweisungszeilen im
  Hub-Journal.
* **`.claude/hooks/**` unberührt.** Kein Produktivcode geschrieben — der
  Rollen-Wächter sperrt `daimon/`, `face/`, `kwin-script/` und
  `config/systemd/` ohnehin.

---

## 6. Was als Nächstes ansteht

Nach Dringlichkeit, nicht nach Aufwand:

1. **`systemctl --user restart daimon-hub`** — schließt Befund 2. Kostet
   nichts, braucht keine Portal-Zustimmung, und der Privatmodus ist danach
   einschaltbar.
2. **T-4.8 K7** — der Undo-Broker. Eine Zusage über Datenverlust, die im
   Betrieb keinen Fall hat, in dem sie greift.
3. **T-4.7 K3/K4** — `xdg-dbus-proxy`. Entweder starten oder
   `config/dbus-filter.conf` und die vier Dokumentstellen entschärfen; was
   nicht bleiben kann, ist eine Konfigurationsdatei, die ihren Aufrufer
   beschreibt.
4. **T-5.9 K9** — den Umfang aus `prompt_shown` in ein eigenes, nicht
   redigiertes Feld.
5. **T-4.6 K5** — die Blase bei gerissener Kette.
6. **T-7.4 K3 und T-4.7 K5** — zwei Entscheidungen, kein Code: welche der
   beiden Fassungen gilt (Leerlauf-Exit, `PrivateUsers=yes`). Beide gehören
   an Matthias und nicht an einen Prüfstand.
7. **Befund 1** — `erlaubte_units` für `auth.sock` und `face.sock`, und den
   Kommentar bei `daemon.py:1315` auf das korrigieren, was er tatsächlich
   abdeckt.

Punkte 2 bis 5 sind rote Verifizierer: wer sie behebt, hat den Beleg schon —
`tests/verify/T-4.8.sh` und die drei anderen werden grün, und dann friert man
sie ein.
