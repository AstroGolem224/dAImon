# Ledger T-5.9.v — Verifizierer: das Deklassifizierungs-Gate

**Ausgang 18.08.: `produktdefekt-rot`**
**Ausgang 19.08.: `produktdefekt-rot` — UNVERAENDERT. NICHT eingefroren.**
Siehe §Nachlauf am Ende.

Der Verifizierer ist gebaut, gegen das Gut-Muster grün, gegen alle zwölf
Mutanten rot — und gegen den echten Baum rot, an genau einem Punkt. Die
Prüffrage des Builders ist beantwortet: **nein**, Bildschirmkontext kommt ohne
Rundenmarke nicht ans Modell; das ist an der Naht gemessen, nicht am Stück.
Rot ist Akzeptanzpunkt 4: der **Umfang** einer Freigabe landet im Audit nur in
einem Feld, das das Audit immer redigiert.

---

## Provenienz

| | |
|---|---|
| Gebaut von | Reviewer-Sitzung vom 2026-08-18, `DAIMON_ROLE=reviewer` |
| Worktree | `/mnt/data/AI/repos/dAImon-reviewer-t59`, Branch `reviewer/p4-T-5.9v` |
| HEAD beim Bau | `eddcc29c911108fa1a1f2df7d3ebf692903c649a` |
| Gelesen, in dieser Reihenfolge | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` Block **T-5.9** (Z. 1563 ff., fünf Akzeptanzpunkte + Verifikationsabsatz) **und** Block **T-5.9b** (Verdrahtung, `kontext.sock`, Unit-Allowlist, „die `turn_id` wandert nicht") · `docs/DESIGN.md` §7.2b und §5.1 (dazu §5.2 für die Senkentabelle) · `docs/REVIEWER-UEBERGABE-17.08.md` §1 Punkt 1 und §4 · Formvorlagen `tests/verify/T-4.6.sh` + `t46_pruefstand.py` und `tests/verify/T-7.2.sh` + `t72_pruefstand.py` + `t72_dienst.py`, dazu `tests/mutants/T-7.2/erzeugen.sh` und `tests/verify/meta.sh` |
| Produktquelle gelesen | `daimon/hub/{declassify,daemon,marks,audit}.py`, `daimon/eyes/context.py`, `daimon/mind/{router,answer,daemon,proactive}.py`, `daimon/common/{ipc,protocol,taint}.py` — **nur lesend** |
| Nicht getan | `freeze.sh` **nicht** gerufen (eigener autorisierter Task) · `.claude/hooks/**` **nicht** angefasst (zweite Sitzung) · `T-4.6.*`, `T-7.2.*`, `meta.sh` **nicht** angefasst · kein Produktivcode geändert, kein Merge, kein Push · keine transiente Unit erzeugt, kein `systemctl` gerufen |

Neue Dateien:

```
tests/verify/T-5.9.sh
tests/verify/t59_pruefstand.py                    (der Prüfstand, 10 Kriterien)
tests/verify/t59_hub.py                           (startet den echten Hub)
tests/fixtures/known-good/T-5.9/                  (368 K Bytes, 41 Dateien)
tests/mutants/T-5.9/erzeugen.sh + .gitignore      (12 Bäume, NICHT eingecheckt)
tests/evidence/LEDGER-T-5.9.v.md
```

Der Nachweis läuft zweistufig; `meta.sh` ruft den Erzeuger inzwischen selbst
auf — **geprüft, nicht angenommen** (`meta.sh` Z. 30–46, Commit `5a78c51`; im
Lauf unten steht die Zeile „Mutanten werden erzeugt"):

```
bash tests/verify/meta.sh T-5.9
```

---

## Die Hürde: wie diese Messung an `kontext.sock` herankommt

Der Weg des Bildschirmkontexts zum Modell ist `kontext.sock`, und die
Unit-Allowlist dort lässt nur `daimon-mind.service` durch
(`daemon.py:73 KONTEXT_UNITS`). Eine transiente Unit desselben Namens wäre ein
Eingriff in den Betrieb — die Unit ist auf dieser Maschine **enabled**
(gemessen: `systemctl --user is-enabled daimon-mind.service` → `enabled`,
`is-active` → `inactive`; `daimon-hub.service` **läuft**). Ein Verifizierer,
der eine gleichnamige transiente Unit hochzieht, kollidiert mit ihr und mit
dem laufenden Hub. Das ist unterblieben.

**Gewählter Weg: zwei Hub-Prozesse, ein Unterschied.** `t59_hub.py` startet
den *echten* `daimon.hub.daemon.Hub` in einem vollständig eigenen XDG-Baum.
Der einzige Eingriff ist `daemon.KONTEXT_UNITS`, und er ist **gewogen**: der
Treiber vergleicht sha256 der Allowlist vor und nach dem Setzen und bricht ab,
wenn sie gleich bleibt.

| | Allowlist | gemessen |
|---|---|---|
| Hub A | **unberührt** (`daimon-mind.service`) | weist den Prüfstand ab — die Verbindung fällt, keine Antwort |
| Hub B | Unit des Prüfstands (`app-com.anthropic.Claude-…scope`, zur Laufzeit aufgelöst) | bedient ihn — hier läuft die ganze Naht |

Hub A ist damit **keine Attrappe**: dort läuft die echte Allowlist gegen die
echte Unit-Auflösung (`ipc.peer_of` über `SO_PEERPIDFD` und den cgroup-Pfad).
Daneben steht die Positivkontrolle, dass Hub A überhaupt lebt und horcht
(`state.sock` antwortet) — sonst wäre „abgewiesen" nicht von „nie gestartet"
zu unterscheiden. Und Hub B ist die Gegenprobe: derselbe Weg, dieselbe Zeile,
dieselbe Unit, geändert ist **nur** die Allowlist.

**Was dieser Weg NICHT sieht** — ausführlich unter „Grenzen", Punkt 1: dass
die Unit des echten Mind-Prozesses `daimon-mind.service` *heißt*. Gemessen ist
„eine fremde Unit kommt nicht durch" und „eine gelistete kommt durch"; nicht
gemessen ist die Zuordnung Mind ↔ Unitname. Das ist der Zulauf von T-5.9b und
gehört T-5.9b.v/T-7.1.v.

---

## Die Prüffrage des Builders

> *Kommt Bildschirmkontext ohne Rundenmarke ans Modell?*

**Nein — gemessen an der Naht, nicht am Stück.** Ein Kanarienvogel geht mit
dem *echten* `Kontextspeicher` in die Quarantäne, eine *echte* Rundenmarke
entsteht über den *echten* `auth.sock` des *echten* Hubs, gefragt wird über
`kontext.sock` — der Weg, den `Router._api` im Betrieb nimmt. Gesucht wird der
Kanarienvogel in den **rohen Antwortbytes**; das ist, was der Mind bekäme.

```
r1  ohne Marke, mit Bezug                      -> ok:false grund:keine_marke
r2  ohne Marke, mit geratener turn_id
    UND einem Kontingentfeld                   -> ok:false grund:keine_marke
r3  mit Marke, ohne Bezug                      -> ok:false grund:kein_bildschirmbezug
r4  mit Marke, mit Bezug                       -> ok:true, Kanarienvogel in den Bytes
r5  dieselbe Frage sofort noch einmal          -> ok:false grund:keine_marke
```

In r1, r2, r3 und r5 steht der Kanarienvogel **nicht** in den Antwortbytes; in
r4 steht er drin. Ohne r4 wäre keine der vier Sperren auswertbar.

### Und die Selbstauskunft, die ich prüfen sollte

> *Meine Messung war eine Negativkontrolle mit zwei Positivkontrollen daneben;
> prüfe, ob das genügt.*

**Für die gestellte Frage genügte sie, für die Akzeptanzliste nicht.** Zwei
Positivkontrollen decken die zwei Richtungen des Zustands „Marke ja/nein" ab.
Was sie nicht abdecken, sind die anderen vier Akzeptanzpunkte und die drei
übrigen Fälle des Verifikationsabsatzes — und genau dort liegt der Befund. Der
Punkt, an dem ein zu gutmütiger Verifizierer grün meldet, ist außerdem **nicht**
die Marke, sondern das Kontingent (Design §7.2b): es ist ein echtes, gültiges,
für den Egress einlösbares Recht. Der Prüfstand stellt deshalb neben die
Negativmessung ausdrücklich die Kontrolle, dass **dasselbe** Kontingent
`einloesen_fuer_egress` besteht — es ist ein Recht, nur nicht dieses.

---

## Befund: der Umfang einer Freigabe ist im Audit nicht lesbar

**Akzeptanzpunkt 4 von T-5.9:**

> Jede Freigabe landet im Audit **mit Umfang und `turn_id`**.

Die `turn_id` steht im Klartext im Datensatz. Der Umfang nicht. Er wandert in
`prompt_shown`:

```python
# daimon/hub/declassify.py, Deklassifizierung._schreiben
prompt_shown=f"{grund} {sorted(umfang.items())}",
tainted=("prompt_shown",))
```

und `Audit.schreiben` redigiert dieses Feld **immer**, unabhängig von
`tainted` (`daimon/hub/audit.py:167–171`, Kommentar: „Der Vorschautext ist
IMMER redigiert"). Übrig bleibt ein Abdruck. Gemessen, der echte Datensatz
einer echten Freigabe:

```
FAIL K9: der Umfang der Freigabe steht LESBAR im Datensatz (Akzeptanz 4:
"mit Umfang und turn_id"); gefunden [] von [('ocr', 1), ('titel', 0), ('vlm', 0)];
Datensatz {"action_id": "context.declassify", "initiator": "foreground",
"mark_id": "35ae4cd4…", "outcome": "ok", "params_hash": "sha256:6f1d4479…",
"prev_hash": "sha256:d397ccd8…",
"prompt_shown": "<redacted:sha256:89e2d324…:len=89>",
"schema": "daimon.audit.v1", "seq": 4, "tool_use_id": "-",
"turn_id": "35ae4cd44a746826aff445a0ff29b969"}
```

Wer nach einem Vorfall fragt „wie viel Bildschirm ist in dieser Runde
herausgegangen", bekommt aus dem Audit `<redacted:…:len=89>`. Ob ein Eintrag
freigegeben wurde oder zwanzig, steht dort nicht — und die Länge 89 ist für
jede Umfangskombination fast gleich. **Dasselbe trifft den Grund einer
Ablehnung**: `denied` steht da, warum nicht. Das ist nicht Teil von
Akzeptanzpunkt 4 und deshalb nicht rot gewertet, gehört aber zum selben Riss.

Der Fehler ist derselbe Typ wie die zwei Fehler, die der Builder am 17.08.
übereinander gefunden hat (Dokstring in `_schreiben`): das Audit war
angeschlossen und schrieb trotzdem nicht das, was zugesagt war. Diesmal
schreibt es — nur nicht lesbar.

**Warum das kein Redaktionsgewinn ist.** Der Umfang sind Zählwerte über eine
geschlossene Aufzählung von Arten (`titel`, `ocr`, `vlm`). Nach Design §5.2
sind „validierte Zahlenwerte" und „geschlossene Aufzählungen" `trusted`; sie
tragen keinen Bildschirminhalt, nur seine Menge. Die Äußerung gehört
redigiert — sie steht als `params_hash` da, und das ist richtig. Der Umfang
gehört gelesen.

**Reparaturort und -umfang** (Vorschlag, im Gut-Muster laufend belegt, im
Arbeitsbaum ungeprüft): `_schreiben` gibt `umfang=dict(umfang)` (und
sinnvollerweise `grund=grund`) als eigene Felder mit. `Audit.schreiben` nimmt
Zusatzfelder an und lässt sie unredigiert — **eine** Zeile, eine Stelle. Die
Messung schreibt den Ort nicht vor: sie sucht die Zahlen in *irgendeinem* Feld
des Datensatzes, in jeder Schreibweise, die ein Mensch als Umfang läse
(`"ocr": 1`, `('ocr', 1)`, `ocr=1`). Daneben steht die Positivkontrolle, dass
dieselbe Suche denselben Umfang in einem Zusatzfeld **desselben** `Audit`
findet — ohne sie wäre „nicht gefunden" nicht von „die Suche greift nicht" zu
unterscheiden.

Reparieren darf diese Sitzung nicht — `daimon/**` ist für `reviewer` gesperrt.
Der Befund geht an den Builder, der Test bleibt rot.

---

## Was grün ist, und woran

**115 Prüfungen, zehn Kriterien**, jedes einzeln abgerechnet, ohne
`&&`-Verkettung. Ein Kriterium ohne eine einzige Messung zählt in der Bilanz
**als rot**.

| | Kriterium (Quelle) | Prüfungen | gemessen woran |
|---|---|---|---|
| K1 | Ohne frische Rundenmarke keine Freigabe; die `turn_id` wandert nicht (Akzeptanz 1, T-5.9b) | 11 | Naht über `kontext.sock`: Hub A weist die fremde Unit ab (bei lebendem `state.sock`), Hub B antwortet; ohne Marke `keine_marke` und kein Kanarienvogel in den Antwortbytes; eine **mitgeschickte** `turn_id` plus Kontingentfeld ändert daran nichts. Dazu am Modul: `freigeben()` ohne `turn_id` fasst den Speicher nicht an und zählt die Ablehnung |
| K2 | Ein Kontingent aus dem Wake-Word deklassifiziert **nichts** (Akzeptanz 1, Design §7.2b) | 12 | `erlaubt_deklassifizierung` ist für Wake-Word **und** Rundenmarke `False`; `MarkenBuch.ausgeben(quelle="wake_word")` wird abgelehnt, `quelle="auth"` nicht; am Gate: Kontingent ohne Marke → eigener Grund, Kontingent **neben** gültiger Marke → ebenfalls abgelehnt, **und die Marke bleibt unverbraucht**. Positivkontrollen: dasselbe Kontingent ist für den Egress einlösbar; dieselbe Marke ohne Kontingent gibt frei |
| K3 | Mit Marke, ohne Bildschirmbezug: keine Freigabe (Akzeptanz 1) | 12 | Naht: echte Marke über `auth.sock` (Zähler `ausgegeben` +1 als Vorbedingung), Frage ohne Bezug → `kein_bildschirmbezug`, kein Kanarienvogel, **Marke nicht eingelöst** (sie gehört dem, was gelingt). Dazu die enge Liste in beide Richtungen: „schau mal hier", „was ist das da" sind kein Bezug; „was steht auf dem bildschirm", „what is on my screen" sind einer |
| K4 | Mit beidem **muss** er ankommen; die Marke gilt einmal (Verifikationsabsatz) | 13 | Kanarienvogel in den rohen Antwortbytes; `turn_id` ist die des Hubs und **nicht** die geratene; `umfang.ocr ≥ 1`; `senke == durchgang2`; der Diagnosezähler `rundenmarke.eingeloest` wächst um genau eins — der Zähler, der bis zum 17.08. keinen Schreiber hatte. Dieselbe Frage sofort noch einmal: `keine_marke` |
| K5 | Abgelaufene Marke: keine Freigabe (Verifikationsabsatz) | 6 | echtes `MarkenBuch` mit **injizierter Uhr** (kein `sleep`; die Frist des Hubs ist 120 s): vor Ablauf gibt dasselbe Gate frei, nach Ablauf ist `aktuelle()` `None` und `freigeben` scheitert mit `marke_ungueltig`; ebenso für die zweite Einlösung und für eine erfundene `turn_id` |
| K6 | Ohne Nutzerhandlung keine Freigabe, auch nicht proaktiv (Akzeptanz 5) | 7 | `proaktiv=True` **mit** gültiger Marke und perfektem Bezug wird abgelehnt, die Marke bleibt unverbraucht, derselbe Aufruf ohne `proaktiv` gibt frei. Dazu der **Zulauf**, über den Syntaxbaum statt über Textsuche: `mind/proactive.py` importiert das Gate nicht, ruft weder `freigeben` noch holt es `kontext` — Positivkontrolle: dieselbe Messung findet den `getattr(self._quellen, "kontext", …)` im Router |
| K7 | Die Quarantäne aus T-5.7, **aktiv angegriffen** (Verifikationsabsatz) | 15 | neun Leseversuche am echten `Kontextspeicher` mit dem Kanarienvogel darin: kein Argument, `None`, `True`, `1`, ein nacktes Objekt, `SimpleNamespace(turn_id="x")` (der Fehler vom 16.08.), ein `dict`, ein Schein mit **leerer** `turn_id`, eine gleichnamige Attrappe ohne `turn_id` — alle scheitern. Dazu wird **jede** öffentliche, argumentlose Vorrichtung angefasst; keine gibt Inhalt heraus. Positivkontrolle: der echte `Freigabeschein` öffnet |
| K8 | Freigegebener Kontext geht ausschließlich in Durchgang 2 (Akzeptanz 2) | 14 | `Freigabe.senke`, jeder Eintrag `Mark.TAINTED`, und die **echte** Senkentabelle aus T-3.13b (`taint.pruefe_senke`) sperrt ihn gegen `durchgang1` und lässt ihn in `durchgang2`. Dazu der Weg im Prozess mit dem Modell: echter `Router` + echter `Durchgang2`, ein Aufzeichner an der Stelle des Egress — der freigegebene Kontext steht in **genau** dem Körper der Bildschirmfrage und in keinem anderen; „wie spät ist es" und „mach das Fenster zu" fragen das Gate nicht einmal |
| K9 | Jede Freigabe landet im Audit mit Umfang und `turn_id` (Akzeptanz 4) | 13 | die echte Kette, die der echte Hub während der Naht geschrieben hat, als **Folge**: fünf Anfragen, fünf Datensätze, Ausgänge `[denied, denied, denied, ok, denied]` in dieser Reihenfolge; `turn_id` im Klartext, `initiator=foreground`, Äußerung und Kanarienvogel **nicht** im Klartext, `params_hash` gesetzt, `prev_hash` gesetzt. **Umfang: 1 rot, siehe Befund** |
| K10 | Durchgang 1 bekommt opake Referenzen, keine Fenstertitel (Akzeptanz 3, Design §5.1) | 12 | `Router._referenzen_bilden` gegen einen Giftttitel: kein Titel und kein Feld, das einen tragen könnte; nur `{ref, app_id}`; `ref` opak (`w_N`); eine nicht installierte `app_id` wird `unbekannt`, eine installierte kommt durch (eigenes `XDG_DATA_HOME` mit einer Kontroll-`.desktop`). Der Titel bleibt in der Referenztabelle des Hubs. Dazu: die Zusage hat einen **Aufrufer**, und die alte Fassung `declassify.referenzen()` ist wirklich weg — zwei Fassungen einer Regel sind eine Regel und eine Attrappe |

Gemessen wird an **echten Prozessen**: `t59_hub.py` ruft `Hub(...).start()`,
also die Verdrahtung des Betriebs — dieselbe `MarkenBuch`, dasselbe
`_gate_teile()` mit `Kontextspeicher`, `Deklassifizierung` und
`audit_buch()`, derselbe `kontext.sock` mit derselben Prüfung in
`_horche_einfach`. Der Kanarienvogel geht mit der echten
`Kontextspeicher.hinzufuegen` in die Quarantäne, die Marke über den echten
`auth.sock`. Der Prüfstand baut nichts davon nach.

---

## Die harten Auflagen dieses Auftrags, und wie sie eingelöst sind

### 1. Negativkontrolle plus Positivkontrolle, beide gewogen

Jede Sperre hat ihre Gegenprobe, und zwischen Probe und Gegenprobe ist genau
**eine** Bedingung anders:

```
Hub A, unberührte Allowlist   -> Verbindung fällt   |  Hub B, Allowlist gesetzt -> Antwort
ohne Marke                    -> keine_marke        |  mit Marke               -> ok, Kanari in den Bytes
mit Kontingent                -> kontingent_…       |  ohne Kontingent         -> ok
ohne Bildschirmbezug          -> kein_bildschirm…   |  mit Bezug               -> ok
abgelaufene Marke             -> marke_ungueltig    |  frische Marke           -> ok
proaktiv=True                 -> ohne_nutzerhandl.  |  proaktiv=False          -> ok
Leseversuch ohne Schein       -> QuarantaeneFehler  |  echter Freigabeschein   -> Einträge
nicht installierte app_id     -> "unbekannt"        |  installierte app_id     -> kommt durch
Umfang nicht im Datensatz     -> rot                |  Umfang in einem Zusatzfeld desselben
                                                    |  Audit -> von derselben Suche gefunden
```

Dazu drei Kontrollen, die nicht Probe/Gegenprobe sind, sondern die Messung
selbst absichern:

* **Hub A lebt**, während er abweist (`state.sock` antwortet) — sonst wäre
  „abgewiesen" nicht von „nie gestartet" zu unterscheiden.
* **Der Aufzeichner hat überhaupt Modellkörper gesehen** (K8) — sonst wäre
  „der Kanarienvogel steht nirgends" auch bei totem Routerpfad grün.
* **Die Journal-Attrappe hat den Anker des Hubs abgefangen** (K9) — siehe
  Punkt 2 und die Grenze 6.

**Selbstauskunft ist kein Beleg.** Der `grund` in der Antwort des Hubs wird
geprüft, aber ausdrücklich als Diagnose; der Beleg ist der Zustand der
Antwortbytes und der Audit-Kette. Der Mutant `keine-marke-egal` zeigt, warum:
dort meldet der Hub `ok:true` — rot wird er daran, dass der Kanarienvogel in
den Bytes steht.

### 2. Jede Manipulation wird gewogen

Dieser Prüfstand verändert **keine Produktdatei**. Er hat genau einen
Eingriff, und der wird gewogen: `t59_hub.py` vergleicht sha256 der Allowlist
von `kontext.sock` vor und nach dem Setzen und **bricht mit Exit 3 ab**, wenn
sie gleich bleibt — ein Hub, der die Unit des Prüfstands ohnehin erlaubte,
wäre keine Gegenprobe zur Abweisung. Belegt im Lauf:

```
Allowlist unberuehrt: ('daimon-mind.service',)                       # Hub A
Allowlist gesetzt: sha256 4527d65a5e04 -> 17c7f5308798 (…scope,)     # Hub B
```

Die zweite Waage trägt die Mutantenerzeugung: `erzeugen.sh` bricht ab, wenn
ein Mutationsanker nicht **genau einmal** im Gut-Muster vorkommt. Ein Mutant,
der nichts geändert hätte, entsteht gar nicht erst. Das ist der Fehler vom
17.08. (§4 Punkt 3), an dieser Stelle konstruktiv ausgeschlossen.

### 3. Eine Messung ist ein Zeitpunkt, kein Zeitfenster

Kein `--since`, kein Fenster gegen ein zweites. Bezugspunkt jeder
Naht-Messung ist die **Antwort des Hubs** auf genau die Zeile, die er gerade
bearbeitet hat. Die Audit-Kette wird als **Folge** gelesen (fünf Anfragen,
fünf Datensätze, feste Reihenfolge der Ausgänge) und nicht als Zählerstand
zwischen zwei Zeitpunkten. Der Ablauf einer Marke läuft über eine **injizierte
Uhr**, nicht über `sleep` — ein Prüfstand, der zwei Minuten wartet, wird nicht
gefahren. Die einzigen Wartezeiten sind `warte_auf(hub.bereit)` (Abbruch bei
Erfolg) und 0,4 s nach `intent_mark`, direkt danach an einem festen Zähler
belegt (`rundenmarke.ausgegeben` +1).

---

## Welche Zeile im Produktivcode müsste kaputt sein — und ausprobiert?

Die Auflage aus dem Auftrag, beantwortet, **mit Lauf**. Zwölf Mutanten, jeder
eine gebrochene Stelle im Gut-Muster, jeder gemessen:

| Kriterium | gebrochene Zeile | Ergebnis |
|---|---|---|
| K1 | `declassify.py`: der `if turn_id is None:`-Block gibt frei, statt abzulehnen | `keine-marke-egal`: **K1 7 rot** (+ K2 2, K4 3, K9 2 — die Naht ohne Marke reißt jede Messung mit, die auf ihr aufbaut) |
| K2 | `marks.py`: `erlaubt_deklassifizierung` → `return True` | `kontingent-deklassifiziert`: **K2 2 rot**, sonst nichts |
| K2 | `marks.py`: `if quelle != self.QUELLE_AUTH:` → `if False:` | `marke-aus-jeder-quelle`: **K2 1 rot**, sonst nichts |
| K3 | `declassify.py`: `bildschirmbezug` → `return True` | `bezug-immer-erkannt`: **K3 8 rot** (+ K4 6, K9 4 — die Frage ohne Bezug gibt jetzt frei und verbraucht die Marke) |
| K4 | `declassify.py`: `bildschirmbezug` → `return False` | `bezug-nie-erkannt`: **K4 7 rot** (+ K2/K3/K5/K6/K8/K9 je 1–3) — der Mutant tötet **jede** Positivkontrolle. Ein Verifizierer, der ihn nicht fängt, meldet grün, weil er nichts messen kann |
| K5 | `declassify.py`: `except MarkenFehler: raise self._ablehnen(…)` → `pass` | `abgelaufene-marke-egal`: **K5 3 rot**, sonst nichts |
| K6 | `declassify.py`: `if proaktiv:` → `if False:` | `proaktiv-erlaubt`: **K6 3 rot**, sonst nichts |
| K7 | `context.py`: `if not ist_freigabeschein(schein):` → `if False:` | `quarantaene-ohne-schein`: **K7 9 rot**, sonst nichts |
| K8 | `declassify.py`: `senke: str = "durchgang2"` → `"durchgang1"` | `senke-durchgang1`: **K8 1 rot** (+ K4 1, die Naht liest dasselbe Feld) |
| K8 | `declassify.py`: `markiere(e, Mark.TAINTED)` → der Eintrag ohne Markierung | `freigabe-nicht-markiert`: **K8 1 rot**, sonst nichts |
| K9 | `declassify.py`: `if self._audit is None: return` → immer `return` | `audit-schweigt`: **K9 4 rot**, sonst nichts — der Zustand, in dem das Gate bis zum 17.08. tatsächlich lief |
| K10 | `router.py`: `offen.append({…})` trägt zusätzlich `"titel"` | `titel-in-durchgang1`: **K10 3 rot**, sonst nichts |
| K9 | die eine Abweichung des Gut-Musters entfernt (= der Arbeitsbaum) | **K9 1 rot** — der Befund oben |

**Jedes** der zehn Kriterien wird in mindestens einem Lauf rot. Kein Kriterium
ist blind. Acht der zwölf Mutanten fallen **ausschließlich** an ihrem eigenen
Kriterium; die vier breiten (`keine-marke-egal`, `bezug-immer-erkannt`,
`bezug-nie-erkannt`, `senke-durchgang1`) reißen naturgemäß mit, was hinter
ihnen liegt — die Zuordnung steht im Prüfstandkopf und wird bei jedem Lauf mit
ausgegeben.

---

## Belege (Befehl + Ausgabe)

```
$ git rev-parse HEAD
eddcc29c911108fa1a1f2df7d3ebf692903c649a

$ bash tests/verify/verify-frozen.sh
verify-frozen: 37 eingefrorene Dateien unveraendert; Abhaengigkeiten geschlossen.
EXIT 0

$ bash tests/verify/meta.sh T-5.9
meta[T-5.9]: Mutanten werden erzeugt (…/tests/mutants/T-5.9/erzeugen.sh) ...
T-5.9: 12 Mutanten erzeugt.
meta[T-5.9]: Gut-Muster ...
meta[T-5.9]: Mutante 'abgelaufene-marke-egal' erkannt.
meta[T-5.9]: Mutante 'audit-schweigt' erkannt.
meta[T-5.9]: Mutante 'bezug-immer-erkannt' erkannt.
meta[T-5.9]: Mutante 'bezug-nie-erkannt' erkannt.
meta[T-5.9]: Mutante 'freigabe-nicht-markiert' erkannt.
meta[T-5.9]: Mutante 'keine-marke-egal' erkannt.
meta[T-5.9]: Mutante 'kontingent-deklassifiziert' erkannt.
meta[T-5.9]: Mutante 'marke-aus-jeder-quelle' erkannt.
meta[T-5.9]: Mutante 'proaktiv-erlaubt' erkannt.
meta[T-5.9]: Mutante 'quarantaene-ohne-schein' erkannt.
meta[T-5.9]: Mutante 'senke-durchgang1' erkannt.
meta[T-5.9]: Mutante 'titel-in-durchgang1' erkannt.
meta[T-5.9]: 12 Mutanten, alle erkannt.
META EXIT 0

$ DAIMON_FIXTURE=$PWD/tests/fixtures/known-good/T-5.9 bash tests/verify/T-5.9.sh
Unit des Pruefstands: 'app-com.anthropic.Claude-2595.scope'
K1: 11 Pruefungen, 0 rot        K6:  7 Pruefungen, 0 rot
K2: 12 Pruefungen, 0 rot        K7: 15 Pruefungen, 0 rot
K3: 12 Pruefungen, 0 rot        K8: 14 Pruefungen, 0 rot
K4: 13 Pruefungen, 0 rot        K9: 13 Pruefungen, 0 rot
K5:  6 Pruefungen, 0 rot        K10:12 Pruefungen, 0 rot
EXIT 0

$ bash tests/verify/T-5.9.sh                       # der echte Baum
FAIL K9: der Umfang der Freigabe steht LESBAR im Datensatz (Akzeptanz 4:
         "mit Umfang und turn_id"); gefunden [] von
         [('ocr', 1), ('titel', 0), ('vlm', 0)]; Datensatz {…
         "prompt_shown": "<redacted:sha256:89e2d324…:len=89>" …}
K1: 11 Pruefungen, 0 rot        K6:  7 Pruefungen, 0 rot
K2: 12 Pruefungen, 0 rot        K7: 15 Pruefungen, 0 rot
K3: 12 Pruefungen, 0 rot        K8: 14 Pruefungen, 0 rot
K4: 13 Pruefungen, 0 rot        K9: 13 Pruefungen, 1 rot
K5:  6 Pruefungen, 0 rot        K10:12 Pruefungen, 0 rot
EXIT 1

$ systemctl --user is-enabled daimon-mind.service   # vor wie nach den Läufen
enabled
$ systemctl --user is-active daimon-hub.service daimon-auth.service \
      daimon-face.service daimon-focus.service daimon-hookbridge.service
active
active
active
active
active

$ pgrep -af "[t]59_hub"; pgrep -af "[t]59_pruefstand"; ls -d /tmp/t59-*
(kein Treffer, kein Verzeichnis geblieben)
```

---

## Grenzen — was er NICHT misst

1. **Die Zuordnung Mind ↔ Unitname ist nicht gemessen.** Gemessen ist, dass
   `kontext.sock` eine fremde Unit abweist (Hub A, echte Allowlist, echte
   Auflösung über `SO_PEERPIDFD` und cgroup) und eine gelistete bedient
   (Hub B). Nicht gemessen ist, dass der echte Mind-Prozess unter
   `daimon-mind.service` läuft — dafür bräuchte es einen Lauf **in** dieser
   Unit. Sie ist auf dieser Maschine `enabled`, aber `inactive`; eine
   transiente Unit desselben Namens wäre ein Eingriff und ist unterblieben.
   Das gehört T-5.9b.v/T-7.1.v.
2. **Der Weg vom Mind zum Egress ist nicht gemessen.** K8 belegt, dass
   freigegebener Kontext im Körper von Durchgang 2 landet und in keinem
   anderen; dass dieser Körper dann tatsächlich (und nur er) über den
   Egress-Broker hinausgeht, ist T-5.10 und ausdrücklich ein anderer Task —
   der Plan verlangt dafür einen Mitschnitt **im** Egress.
3. **Kein echter Augendienst.** Der Kanarienvogel wird mit der echten
   `Kontextspeicher.hinzufuegen` abgelegt, nicht von `daimon-eyes` erzeugt.
   Dass die Augen im Betrieb genau dorthin schreiben, ist T-5.7/T-7.1b. Der
   Prüflauf schaltet dafür auch nichts an — er startet keine Wahrnehmung.
4. **Die Frist der Rundenmarke im Betrieb ist nicht live gemessen.** K5 fährt
   das echte `MarkenBuch` mit injizierter Uhr; der Hub baut es mit seiner
   Vorgabe (120 s, `daemon.py:186`, nicht konfigurierbar). Dass diese Zahl im
   Betrieb 120 s ist, steht im Code und ist hier nicht gegen die Wanduhr
   geprüft.
5. **Die Denylist spielt hier nicht mit.** Der Kanarienvogel liegt unter
   einer nicht gelisteten Fensterklasse. Ob ein gelistetes Fenster gar nicht
   erst in den Kontextspeicher kommt, ist T-7.2 — und dort **rot**
   (`LEDGER-T-7.2.v.md`, K8). Ein Verifizierer, der beides mischte, wüsste
   hinterher nicht, welche Sperre gegriffen hat.
6. **Ein Vorläufer dieses Prüfstands hat einen Anker ins echte Journal
   geschrieben.** Beim Bau lief ein Hub-Versuch ohne Journal-Attrappe; im
   Journal des Nutzers steht dadurch eine zusätzliche Zeile
   `AUDIT-ANKER seq=0 head=` vom 2026-08-18 07:23:15. Sie reiht sich in die
   rund dreißig gleichartigen Zeilen ein, die die Übergabe §5 bereits nennt,
   und ist wie diese bis etwa 17.09. in jedem `--verify` sichtbar. Entfernen
   ließe sie sich nur durch ein Vacuum des ganzen Journals; das wäre der
   größere Schaden und ist unterblieben. Der fertige Prüfstand fängt
   `systemd-cat` **und** `journalctl` ab und **belegt das mit einer
   Positivkontrolle** (K9, erste Prüfung): bleibt die abgefangene Datei leer,
   wird er rot statt still das Journal zu verschmutzen.
7. **Zwei Hub-Prozesse, kein Neustart.** Dass eine Freigabe einen Neustart
   des Hubs überlebt oder nicht überlebt, ist nicht gemessen; der
   Kontextspeicher wird bei jeder Anfrage neu geladen (`_gate_teile`), und
   genau das ist hier ausgenutzt, nicht geprüft.
8. **Die Suche nach dem Kanarienvogel ist eine Bytesuche in UTF-8.** Sie
   deckt den Weg ab, um den es geht (die Antwortzeile auf `kontext.sock` und
   die Audit-Datei). Einen Schreiber, der denselben Text kodiert oder
   komprimiert herausgäbe, fände sie nicht.
9. **`kontingent` und `proaktiv` haben im Betrieb keinen Aufrufer.** Der
   Dokstring von `freigeben` sagt es selbst. K2 und K6 messen deshalb die
   **Vorrichtung**, nicht ihren Zulauf — der Zulauf wird von
   `tests/test_gate_zulauf.py` bewacht, und ein Wächter ist keine Zusage
   (Übergabe §3). Was hier zusätzlich gemessen ist und strukturell trägt:
   ein Wake-Word kann keine Rundenmarke erzeugen, und der proaktive Pfad
   ruft das Gate nachweislich nicht (K6, über den Syntaxbaum).
10. **Nicht eingefroren.** `freeze.sh` bleibt ungerufen; die
    Freeze-Erweiterung ist laut `PLAN.md` ein eigener, einzeln autorisierter
    Task. `T-5.9.sh`, `t59_pruefstand.py` und `t59_hub.py` sind damit noch
    nicht gegen Änderung geschützt — beim Einfrieren sind
    `t59_pruefstand.py` **und** `t59_hub.py` als Helfer zu deklarieren.

---

## Was der Builder als Nächstes tun kann

* Den Umfang lesbar ins Audit legen (die eine Abweichung des Gut-Musters
  übernehmen — oder eine andere Stelle wählen, die Messung nimmt beides).
  Danach ist dieser Verifizierer gegen den echten Baum grün, **ohne dass er
  angefasst werden muss**; das ist die Probe darauf, dass er das Kriterium
  prüft und nicht eine Implementierung.
* Dabei entscheiden, ob der **Grund** einer Ablehnung dieselbe Behandlung
  bekommt. Heute steht im Audit `denied` und sonst nichts Lesbares; „warum kam
  der Bildschirm nicht heraus" ist damit nicht beantwortbar, obwohl der Code
  den Grund ausdrücklich unterscheidet.
* T-5.9b.v anschließen: die eine Sache, die dieser Verifizierer bewusst nicht
  sehen kann, ist der Unitname des Mind-Prozesses am `kontext.sock`.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, NICHT eingefroren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main`, Commit `9172fa5` |
| Verifizierer unverändert | `T-5.9.sh`, `t59_pruefstand.py`, `t59_hub.py` |

**Nicht eingefroren, weil rot.**

### 1. Gegen `main` — rot, an genau demselben Kriterium

```
$ env -u DAIMON_FIXTURE tests/verify/T-5.9.sh; echo $?
FAIL K9: der Umfang der Freigabe steht LESBAR im Datensatz
         (Akzeptanz 4: "mit Umfang und turn_id");
         gefunden [] von [('ocr', 1), ('titel', 0), ('vlm', 0)]
Bilanz T-5.9:
K1: 11 · K2: 12 · K3: 12 · K4: 13 · K5: 6 · K6: 7 · K7: 15 · K8: 14 ·
K10: 12  — je 0 rot
K9: 13 Pruefungen, 1 rot
1
```

Eine von 115 Prüfungen rot, unverändert gegenüber dem 18.08. Die neun Commits
vom 19.08. haben diesen Befund nicht berührt.

### 2. Heute nachgelesen — warum der Umfang nicht lesbar ist

Der geschriebene Datensatz aus dem Lauf:

```json
{"action_id": "context.declassify", "initiator": "foreground",
 "mark_id": "f758fc4f…", "outcome": "ok",
 "params_hash": "sha256:6f1d4479…",
 "prompt_shown": "<redacted:sha256:89e2d324…:len=89>",
 "schema": "daimon.audit.v1", "seq": 4, "turn_id": "f758fc4f…"}
```

`turn_id` steht drin. Der **Umfang** steht nicht drin — und die Ursache ist
eine Zeile weiter oben, nicht eine fehlende:

`daimon/hub/declassify.py:195–196`

```python
prompt_shown=f"{grund} {sorted(umfang.items())}",
tainted=("prompt_shown",)
```

Der Umfang reist als Teil von `prompt_shown` mit, und `prompt_shown` ist
**als `tainted` deklariert**. Die Redaktion ersetzt das ganze Feld durch
`<redacted:sha256:…>` — mitsamt dem Umfang, der dort nur mitgefahren ist.

Beide Zeilen sind für sich richtig: `prompt_shown` **gehört** redigiert (es
trägt die Äußerung des Nutzers). Falsch ist, dass ein Pflichtfeld der
Akzeptanzliste in einem redigierten Feld transportiert wird.

**Fehlerszenario:** eine Deklassifizierung wird freigegeben. Wer später fragt,
*was* freigegeben wurde — nur der Titel? auch das OCR? auch das VLM-Bild? —
findet im Audit `initiator`, `turn_id` und einen Hash. Der Umfang, den
Akzeptanzpunkt 4 ausdrücklich verlangt („mit Umfang und turn_id"), ist nicht
rekonstruierbar. Damit ist die Frage „was hat das Modell an diesem Tag zu
sehen bekommen" aus dem Audit nicht beantwortbar — und genau dafür gibt es
das Audit.

Der Verifizierer misst dabei mit Positivkontrolle: `gefunden []` steht neben
`von [('ocr', 1), ('titel', 0), ('vlm', 0)]` — der Umfang, der hätte
dastehen müssen, ist bekannt, und es ist nicht nur „nichts gefunden".

### 3. Gut-Muster und Mutanten — der Verifizierer ist gesund

```
$ bash tests/verify/meta.sh T-5.9
T-5.9: 12 Mutanten erzeugt.
meta[T-5.9]: Gut-Muster ...
… abgelaufene-marke-egal · audit-schweigt · bezug-immer-erkannt ·
  bezug-nie-erkannt · freigabe-nicht-markiert · keine-marke-egal ·
  kontingent-deklassifiziert · marke-aus-jeder-quelle · proaktiv-erlaubt ·
  quarantaene-ohne-schein · senke-durchgang1 · titel-in-durchgang1
  — alle erkannt.
meta[T-5.9]: 12 Mutanten, alle erkannt.
```

`audit-schweigt` deckt genau diese Achse ab und wird erkannt.
