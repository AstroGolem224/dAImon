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

## Was zuerst zu klären ist

1. ~~Liefert `qwen3` über Ollama Werkzeugaufrufe, die der Katalog annimmt?~~
   **Beantwortet am 26.08., siehe oben: ja, mit der Schranken-Auflage.**
2. Wo sitzt die Weiche zwischen lokal und Egress — je Runde, je Absicht, oder
   als Rückfall, wenn der Egress nicht antwortet?
3. Welche Senke bekommt der lokale Weg in der Markentabelle?
4. Gilt die Residenzpolitik aus §5.4 auch für ein dauerhaft geladenes
   Textmodell?
