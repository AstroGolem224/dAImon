# Aufgabe: ein zweiter Weg zum Modell (OpenRouter oder lokal)

**Angelegt:** 26.08. · **Anlass:** `daimon-egress` läuft nicht, weil kein
Anthropic-Token vorliegt (Befund B8 aus T-6.8.v). Zwei Akzeptanzkriterien von
T-6.8 sind dadurch bis heute nicht prüfbar.

Nicht entschieden, nur aufgeschrieben. Beide Wege haben Sicherheitsanteil.

---

## Der Stand, gemessen statt vermutet

| | `daimon-egress` | `daimon-lokal-broker` |
|---|---|---|
| Ziel | `api.anthropic.com/v1/messages`, TLS | `127.0.0.1:11434/api/chat` (Ollama) |
| Auth | `x-api-key` + `anthropic-version` aus `LoadCredential` | keine |
| Zustand | **läuft nicht** (243/CREDENTIALS, kein Token) | **läuft** |
| Aufrufer im Betrieb | Mind (`frage_api`, `frage_werkzeug`) | **keiner** |

Der zweite Befund ist der wichtigere: `lokal.sock` hat **null Verbraucher** im
Produktivcode. Der Broker ist gebaut, die Unit läuft seit Monaten mit, und
niemand fragt ihn etwas. Das ist der siebte Fall des Musters aus CLAUDE.md —
ein Stück mit Tests und ohne Zulauf.

Für die Frage „welcher zweite Weg?" heißt das: **der lokale Weg ist nicht zu
bauen, er ist anzuschließen.**

---

## Weg A — lokales Modell über den vorhandenen Broker

**Aufwand: klein.** Der Broker existiert, spricht Ollama, hält seine
Loopback-Grenze im Code (`ZIEL` fest verdrahtet, `brokers/lokal/broker.py:57`)
und zieht Kontingente über `ticket.sock`.

Zu tun:

1. **Zulauf herstellen.** Der Router entscheidet heute zwischen lokalen
   Absichten und `_api` → Mind → Egress. Es fehlt der dritte Ast: Anfragen an
   `lokal.sock` statt an den Egress. Wo genau die Weiche sitzt, ist die
   eigentliche Entwurfsfrage — Router, Mind oder eine Wahl je Runde.
2. **Antwortform.** Ollamas `/api/chat` liefert nicht die Anthropic-Form, die
   `Mind._text_aus_antwort` erwartet. Eine Umsetzung ist nötig, und sie gehört
   an die Stelle, die die Zusage macht, nicht in den Broker.
3. **Werkzeuge.** Durchgang 1 ist werkzeugfähig (`art: "ausfuehren"` an
   `aktion.sock`). Ob das lokale Modell Werkzeugaufrufe in einer Form liefert,
   die der Katalog akzeptiert, ist **ungeprüft** und der Hauptrisikopunkt.
   Fällt es aus, bleibt der lokale Weg auf Durchgang 2 beschränkt — was für
   Kontextfragen genügt und für Aktionen nicht.
4. **Markenführung.** Die Senkentabelle unterscheidet `durchgang1` und
   `durchgang2`. Ein dritter Weg braucht seinen eigenen Eintrag, sonst
   entscheidet der Zufall des Aufrufs, welche Regel gilt (CLAUDE.md Regel 4).

**Was dafür spricht:** kein Netz, kein Token, keine Kosten, und §7.2 wird
nicht angefasst — die Netzsperre bleibt, wie sie ist. Der Broker hält seine
Grenze bereits auf `127.0.0.1`.

**Was dagegen spricht:** Design §13 hält Ollama ausdrücklich für ungeeignet als
VLM-Server, weil der Daemon Modelle nach Belieben hält und die
Selbstbeendigung unterläuft (§13, Zeile zu `llama-server --mmproj`). Für den
**Textweg** ist das ein anderer Fall als für den VRAM-hungrigen VLM-Worker —
aber die Residenzfrage aus §5.4 gehört beantwortet, bevor ein zweites Modell
dauerhaft im Speicher liegt.

---

## Weg B — OpenRouter über den Egress

**Aufwand: mittel, mit Sicherheitsanteil.**

Der Egress ist bewusst auf **einen** Host festgenagelt
(`ZIEL_HOST = "api.anthropic.com"`, `broker.py:76`). Das ist keine
Bequemlichkeit, sondern die Zusage des Brokers: genau ein Ziel ist erreichbar.
Ein zweiter Host weicht sie auf, und das gehört entschieden, nicht nebenbei
geändert.

Zu tun:

1. **Zweites Ziel** in Host, Pfad (`/api/v1/chat/completions`) und Auth
   (`Authorization: Bearer …` statt `x-api-key` + `anthropic-version`).
2. **Rumpfumsetzung** zwischen Anthropic-Messages und der OpenAI-Form, in
   beide Richtungen. `Mind.koerper()` baut heute Anthropic-Nachrichten samt
   Werkzeugblöcken.
3. **Werkzeugaufrufe** haben in der OpenAI-Form eine andere Gestalt
   (`tool_calls` statt `content`-Blöcke). Betrifft denselben Pfad wie oben.
4. **§7.2 nachziehen.** Die Netzsperre nennt heute einen Host. Zwei Hosts sind
   eine andere Zusage, und der Prüfstand, der sie misst, gehört mitgezogen —
   sonst prüft er weiter die alte Fassung (dasselbe Muster wie bei T-7.4/K3
   und T-6.8/K8).
5. **Zweite Zugangsdatei** neben `anthropic-token`, mit demselben
   `LoadCredential`-Weg. Nie aus der Umgebung.

**Was dafür spricht:** derselbe Modellzuschnitt wie heute, Werkzeuge
funktionieren erfahrungsgemäß.

**Was dagegen spricht:** der Inhalt verlässt weiterhin den Rechner, jetzt an
einen Vermittler statt an den Anbieter direkt. Für ein System, dessen
Bedrohungsmodell (§1.3) „versehentliche Preisgabe" führt, ist das eine eigene
Abwägung — kein technisches Detail.

---

## Empfehlung

**Weg A zuerst**, und zwar nicht wegen des kleineren Aufwands, sondern weil er
eine offene Lücke schließt statt eine neue Fläche zu öffnen: der Broker läuft
heute ohne Zulauf, und das ist der Fehler, den dieses Repo am teuersten
gelernt hat.

Der ehrliche Zwischenschritt ist klein: **erst messen, ob das lokale Modell
Werkzeugaufrufe in brauchbarer Form liefert.** Fällt das aus, ist Weg A auf
Durchgang 2 begrenzt, und die Entscheidung sieht anders aus. Das ist eine
Messung von einer Stunde, keine Implementierung.

Weg B bleibt sinnvoll für den Fall, dass Werkzeuge lokal nicht tragen — dann
aber als bewusste Erweiterung von §7.2, mit nachgezogenem Prüfstand.

---

## Gemessen am 26.08.: Werkzeugtreue trägt, mit einer Auflage

Modell `qwen3.8-heretic:27b-96k` (Q6_K, bereits resident), gegen die **echte**
Werkzeugliste aus `Policy.laden().katalog` — 20 Werkzeuge, echter
Persona-Prompt, direkt an Ollama statt über `lokal.sock` (der Broker hat eine
eigene Form und hätte die Messung verfälscht). Je Fall zwölf Läufe in zwei
unabhängigen Durchgängen.

| Fall | Werkzeug gerufen | Name aus Katalog | Argumente schema-konform |
|---|---|---|---|
| „Lautstärke auf dreissig Prozent" | 12/12 | 12/12 | **0/12** |
| „Mach lauter" | 12/12 | 12/12 | 12/12 |
| „Öffne den Taschenrechner" | 12/12 | 12/12 | **0/12** |
| „Wie spät ist es?" (Gegenprobe) | **0/12** | — | — |
| „Hauptstadt von Norwegen?" (Gegenprobe) | **0/12** | — | — |

**Erfundene Werkzeugnamen: 0 von 36. Falschauslösungen: 0 von 30.** Das Modell
antwortet bei der Gegenprobe in Sprechform statt zu handeln — genau richtig.

**Der eigentliche Befund, und er betrifft nicht nur den lokalen Weg:** beide
Fehlschläge sind derselbe Fehler. `value: 30` statt `0.3` (`minimum`/`maximum`
ignoriert), `desktop_id: "1"` statt `org.kde.kcalc.desktop` (`pattern`
ignoriert). Die Schranken stehen **nur im JSON-Schema, nicht im
Beschreibungstext**. `Policy._params_pruefen` fängt beides ab — nicht
gefährlich also, aber nutzlos: die Aktion scheitert jedes Mal.

Gegenmessung mit denselben Schranken zusätzlich wörtlich in der `description`:
**12/12 schema-konform**, `{"value": 0.3}` und der richtige `desktop_id`,
Gegenprobe weiter bei 0 Falschauslösungen. Das ist kein Modellmangel, sondern
eine Lücke in der Funktion, die die Werkzeugliste baut.

**Latenz:** ungestört Median 0,50 s, Maximum 1,23 s — das Ziel „< 3 s" wird um
Faktor sechs unterboten. Kaltstart des Modells 12,3–15,4 s. **Unter Fremdlast
aber Maximum 175,8 s**, weil ein zweiter Ollama-Client trotz
`OLLAMA_NUM_PARALLEL=2` einzelne Anfragen minutenlang blockierte.

**Betriebsbefund nebenbei:** `ollama.service` zählte an diesem Tag **56
Neustarts**; fünf Messanfragen starben an `RemoteDisconnected` und mussten
wiederholt werden. Ein Weg A ohne Wiederholungslogik hätte dort nichts
geliefert.

### Was daraus folgt

Weg A **trägt Durchgang 1**, sofern die Katalogschranken in den
Beschreibungstext wandern. Ohne das bleibt er auf die vierzehn parameterlosen
Werkzeuge beschränkt; die sechs mit Parametern laufen zuverlässig in `deny`.

Die Schrankenänderung gehört in die **eine** Funktion, die die Werkzeugliste
baut (`_tool_schema()` / `werkzeugliste`), nicht in eine lokale Zweitfassung —
sie betrifft beide Wege, und zwei Fassungen einer Regel sind eine Regel und
eine Attrappe (CLAUDE.md Regel 4).

Latenz unter Fremdlast und die Neustartzahl machen Ollama als **einzigen** Weg
unzuverlässig. Als **zweiter** Weg neben dem Egress ist das eine andere
Aussage — und der Fall, um den es hier geht.

**Umsetzung, vier Stellen, keine im Broker:** Anfrage-Rumpf (`system` als
erste Nachricht, `max_tokens` → `options.num_predict`, `think: false`,
`tools`-Einträge auf `{"type":"function","function":{…}}` umhängen,
`input_schema` wandert unverändert nach `parameters`); Antwort-Rumpf (Ollamas
`content`/`tool_calls` in die Blockliste umsetzen, die `frage_werkzeug` ab
Zeile 355 erwartet — danach läuft der Rest **unverändert** durch, inklusive
`_werkzeug_namen` und `aktion.sock`); die Schranken in den Text; und die
Weiche selbst.

**Offen geblieben:** `think: true` ungemessen (ändert Latenz und Antwortform);
mehrrundiger Werkzeugdialog ungemessen (Durchgang 1 braucht ihn heute nicht);
andere Modelle nicht geprüft, weil `OLLAMA_MAX_LOADED_MODELS=1` das residente
verdrängt hätte.

---

## Angeschlossen am 26.08. — Weg A, drei Stellen

Der Broker wurde **nicht umgangen**: der Betrieb geht über `lokal.sock`, wo
die Loopback-Grenze (`ZIEL` fest verdrahtet) und die Kontingente über
`ticket.sock` sitzen. Die Messung oben ging direkt an Ollama, weil sie das
MODELL messen sollte; das ist der Unterschied.

**Das Protokoll, wie es vorgefunden wurde.** Anfrage
`{"v":1,"art":"anfrage","ticket":…,"koerper":…}` mit einem Koerper in
Anthropic-Messages-Gestalt; Antwort `{"v":1,"ok":true,"status":200,"bytes":…,
"dauer_ms":…,"antwort":{"content":[…]}}`. Es hat getragen — bis auf **zwei
Lücken, beide auf der Werkzeugseite**:

1. `nutzlast()` ließ `tools` fallen. Der Mind schickte seine Werkzeugliste,
   der Broker warf sie weg, das Modell konnte gar kein Werkzeug rufen. Kein
   Fehler war sichtbar; es kam nur nie ein `tool_use`.
2. Die Antwortprüfung verlangte nichtleeres `content`. Ein Modell, das ein
   Werkzeug ruft, sagt oft nichts dazu — genau diese Antwort wies der Broker
   als `modell_fehler` ab.

Beides ist im Broker behoben. Die **Umsetzung selbst** steht in
`daimon/mind/lokal.py` und existiert genau einmal; der Broker wendet sie an,
er hat keine eigene Fassung.

| Stelle | Was dort steht |
|---|---|
| `daimon/mind/lokal.py` | `anfrage_rumpf`, `werkzeuge_umsetzen`, `antwort_bloecke` — die eine Fassung |
| `daimon/brokers/lokal/broker.py` | wendet sie an; `tools` und `tool_calls` gehen nicht mehr verloren |
| `daimon/mind/daemon.py: main` | die Weiche: Modellweg = `lokal.sock`, sofern nicht ausdrücklich anders |

`Mind.frage_werkzeug` ist **unverändert** — inklusive `_werkzeug_namen`,
Kurzzeitgedächtnis und `aktion.sock`. Belegt in `tests/test_lokal_zulauf.py`,
das die Naht über echte Sockets fährt und den Zulauf bewacht.

---

## Was zuerst zu klären ist

1. ~~Liefert `qwen3` über Ollama Werkzeugaufrufe, die der Katalog annimmt?~~
   **Beantwortet am 26.08., siehe oben: ja, mit der Schranken-Auflage.**
2. ~~Wo sitzt die Weiche zwischen lokal und Egress?~~ **Entschieden am
   26.08.:** in `daimon/mind/daemon.py: main`, nicht je Runde und nicht als
   Rückfall. **Ein Weg je Anfrage**, und zwar der lokale; der Egress ist der
   Ausbau und über `--egress-socket` erreichbar. Vorher stand die Weiche
   ausschließlich in der Unit — eine Datei, die kein Prüfstand liest.
3. ~~Welche Senke bekommt der lokale Weg in der Markentabelle?~~ **Keine
   neue.** Die Modellantwort ist die Antwort des Assistenten, unabhängig vom
   Modell: `trusted` in Durchgang 1, `tainted` in Durchgang 2 — dieselben
   zwei Marken wie beim Egress. Ein dritter Eintrag wäre eine zweite Regel
   für dieselbe Sache.
4. Gilt die Residenzpolitik aus §5.4 auch für ein dauerhaft geladenes
   Textmodell? **Offen.**
5. ~~Wiederholung bei Zeitüberschreitung.~~ **Gemessen und gebaut am 27.08.,
   siehe unten.**

---

## Gemessen am 27.08.: was Fremdlast wirklich tut — und was daraus folgt

Erst gemessen, dann gebaut. Zweiter (und dritter) Ollama-Client auf demselben
Modell `qwen3.8-heretic:27b-96k`, `OLLAMA_NUM_PARALLEL=2`,
`OLLAMA_MAX_QUEUE=8`; die Messanfrage ist jedes Mal dieselbe kurze Frage mit
`num_predict: 8`.

| Lage | Ergebnis |
|---|---|
| ohne eigene Fremdlast (die Maschine war nicht leer), 3 Läufe | HTTP 200 nach 24,9 s / 81,8 s / 9,4 s |
| zwei Fremdclients | HTTP 200 nach **49,7 s** |
| drei Fremdclients | HTTP 200 nach **79,7 s** |
| 14 gleichzeitige Anfragen gegen `MAX_QUEUE=8` | **14× HTTP 200** nach ~62 s — **kein 503** |
| 20-s-Frist unter Last, danach sofort wiederholt | zweimal `TimeoutError` |
| dieselbe Frage nach Ende der Fremdlast | HTTP 200 nach **8,4 s** |
| Port ohne Dienst (Gegenprobe) | `URLError [Errno 111] Connection refused` nach **0,0 s** |

**Der Befund:** Ollama liefert unter Fremdlast **keinen eigenen Fehler**. Es
antwortet spät, und zwar zuverlässig — auch die Warteschlange lief nicht über.
Der einzige Unterschied, den ein Aufrufer sieht, ist die **Zeit**. Damit war
die naheliegende Erwartung („ein besetzter Endpunkt meldet sich als besetzt")
widerlegt, bevor sie Code geworden ist.

**Und die zweite Widerlegung:** eine Wiederholung hilft **nicht** gegen
Langsamkeit. Der zweite Versuch unmittelbar nach der Zeitüberschreitung lief
erneut in die Frist; erst das Ende der Fremdlast half. Wiederholung zahlt sich
gegen den **Abriss** aus (`RemoteDisconnected`, 56 Neustarts an einem Tag) —
und der fällt schnell und lässt Frist übrig.

Daraus die Bauform: **eine Gesamtfrist, und jeder Versuch bekommt den Rest.**
Ein schneller Fehlschlag lässt Zeit für einen weiteren; eine lange Wartezeit
verbraucht die Frist selbst, und dann ist Aufgeben die ehrliche Antwort. Keine
feste Frist je Versuch, die im Langsamkeitsfall nur früher aufgäbe.

### Der neue Grund

`modell_beschaeftigt` neben `modell_weg` (`brokers/lokal/broker.py`, Liste der
Gründe). Bis zum 27.08. hießen beide `modell_weg` — eine Zeitüberschreitung
nach voller Frist und ein `Connection refused` nach 0,0 s. Wer beides gleich
nennt, sucht bei belegter GPU nach einem toten Dienst. Der Mind reicht den
Grund wörtlich durch (`mind/daemon.py:401`, ausdrückliche Zusage); Router und
`answer.py` fassen ihn wie jeden Transportgrund zu `egress_weg` zusammen —
dasselbe Verhalten wie für `modell_weg`, also **keine** Änderung dort.

Eine zweite Fassung derselben Liste gibt es im Baum **nicht**:
`tests/fixtures/known-good/T-3.14/daimon/brokers/lokal/broker.py` ist eine
eingefrorene Momentaufnahme (sie trägt noch den fest verdrahteten Modellnamen
von vor dem 17.08.) und keine gepflegte Zweitschrift — sie bleibt unberührt.

### Die Obergrenze, zusammengerechnet statt einzeln gesetzt

| Stelle | Frist | Bedeutung |
|---|---|---|
| Ohren → `mind.sock` (`ears/daemon.py: ruf_socket`) | **30 s** | die *wirklich* bindende Grenze für eine gesprochene Runde |
| Mind → `lokal.sock` (`mind/daemon.py: hub_anfrage`) | 180 s | so lange wartet der Mind |
| `lokal.sock`-Verbindung (`broker.py: bediene`) | 180 s | dieselbe Zahl, andere Seite |
| **Broker gesamt (`GESAMT_S`)** | **150 s** | alle Versuche samt Rückstau zusammen |
| je Versuch (`TIMEOUT_S`) | 120 s | gedeckelt auf den Rest der Gesamtfrist |
| Versuche (`VERSUCHE`) | 3 | Rückstau 2 s, verdoppelt sich |

150 s lassen 30 s Luft unter den 180 s für Ticket, Rumpfbau und Antwortzeile.
Die Gesamtfrist ist die **eine** Zahl, die gilt; sie steht seit dem 27.08.
auch in der Auskunft `art: "zustand"`, damit sie im Betrieb ablesbar ist,
statt aus drei Konstanten zusammengesucht zu werden.

**Offen und ausdrücklich nicht mitgebaut:** die 30 s der Ohren machen jede
Antwort jenseits einer halben Minute für den Sprechweg wertlos — ein
Broker-Deckel von 150 s hilft dort niemandem. Das ist eine Entscheidung über
den *Sprechweg* (abbrechen? zwischendurch etwas sagen?) und gehört nicht in
den Broker.

**Nebenbefund, ungefixt:** der Broker nimmt bei automatischer Erkennung das
alphabetisch erste Modell aus `/api/tags`. Auf dieser Maschine ist das
`L3.1-dark-hermes:latest`, und das quittiert eine Anfrage mit Werkzeugliste
mit **HTTP 400** (`modell_fehler`) — live gesehen. Der Betrieb setzt
`lokal.modell` ausdrücklich; für eine Kopie ohne diese Zeile ist der
Werkzeugweg tot, ohne dass es so aussieht.
