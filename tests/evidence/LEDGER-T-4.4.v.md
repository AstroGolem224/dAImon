# LEDGER T-4.4.v — Verifizierer für die Policy-Engine

**Ausgang 18.08.: `produktdefekt-rot`** — der Befund unten ist mit `c092539`
behoben.
**Ausgang 19.08.: `gruen`, eingefroren.** Siehe §Nachlauf am Ende.

Stand 18.08.: der Verifizierer ist gebaut, gegen das Gut-Muster grün, gegen
alle neun Mutanten rot, und gegen den Arbeitsbaum rot — an genau einer
Stelle, mit Beleg. Der Befund ist Akzeptanzpunkt 8: die Direktbefehl-Ausnahme
gehört im Betrieb dem Absender, nicht dem Hub.

---

## Provenienz

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Worktree | `/mnt/data/AI/repos/dAImon-t44` |
| Branch | `reviewer/p4-T-4.4v` |
| Ausgangs-Commit | `5724d23471d524ea7a8fc877c53dcda9c1f18ece` |
| Gelesen | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` T-4.3.t (Z. 1198) und T-4.4 (Z. 1213) · `docs/DESIGN.md` §2.5 (Z. 287) und Z. 263/1375/1616 · `tests/verify/T-4.6.sh` + `t46_pruefstand.py` als Formvorlage |
| Neue Artefakte | `tests/verify/T-4.4.sh`, `tests/verify/t44_pruefstand.py`, `tests/fixtures/known-good/T-4.4/` (107 Dateien, 1,3 MB), `tests/mutants/T-4.4/{erzeugen.sh,.gitignore}`, dieses Ledger |
| sha256 | `T-4.4.sh` `d5cfe4fe…3d30a9b4f` · `t44_pruefstand.py` `20d6256a…443e2d4d` · `erzeugen.sh` `e507e522…4c7cf9d860` |
| `freeze.sh` | **nicht aufgerufen** (eigener autorisierter Task) |
| Fremde Tasks | `T-7.5`, `T-7.2`, `T-5.9`, `T-4.6` unberührt — `git status` listet ausschließlich die vier neuen `T-4.4`-Pfade |
| Laufzeit | ~1 s je Verifiziererlauf (kein `sleep`; die zwei `ask`-Fälle an der Naht warten je 200 ms auf eine Antwort, die niemand gibt) |

---

## Was gemessen wird

Acht Kriterien, eines je Akzeptanzpunkt aus dem Implementierungsplan. Jedes
rechnet einzeln ab; ein Kriterium ohne eine einzige Messung zählt als rot
(„nicht gemessen" ist nicht grün).

| | Akzeptanzpunkt | Prüfungen |
|---|---|---|
| K1 | Alle Tests aus T-4.3.t grün | 4 |
| K2 | Hub kanonisiert und rechnet `params_hash` selbst | 10 |
| K3 | `initiator` aus der eingelösten Rundenmarke | 11 |
| K4 | Strukturierte `when:`-Prädikate, keine String-Globs | 9 |
| K5 | Zustimmungs-Cache mit `(session_id, action_id, params_hash)` | 8 |
| K6 | Vier Gültigkeiten `once` / `session` / `ttl:*` / `persistent` | 10 |
| K7 | Gestenfenster: erteilt **und** ≤ 2 s (Design §2.5) | 12 |
| K8 | Direktbefehl-Ausnahme ist Hub-Eigentum (Design §263) | 14 |

**`pytest tests/test_policy.py` ist nur K1 von acht.** Der Plan verlangt
ausdrücklich „zusätzlich" den Manipulationstest; ein Verifizierer, der nur
die Testdatei des Builders aufruft, prüft dessen eigene Annahmen. Der
Prüfstand entscheidet deshalb gegen einen **eigenen** Katalog, nicht gegen
den aus `tests/test_policy.py`.

### Die zentrale Prüffrage — manipulierter `params_hash`

Zweimal gemessen, weil eine Zusage an der Naht gilt und nicht im Modul:

* **In der Engine** (`Policy.entscheide`): Request mit
  `params_hash="sha256:dede…"` → Entscheidung trägt
  `sha256:7497b209…`, identisch mit dem Lauf ohne Manipulation, und
  identisch mit dem vom Prüfstand **selbst** gerechneten sha256 über die
  kanonische Form. Zusätzlich: Schlüsselreihenfolge und `0.50` vs `0.5`
  ändern den Hash nicht, andere Parameter ändern ihn sehr wohl.
* **An der Naht** (`Hub.aktion_anfrage`, der Endpunkt hinter `aktion.sock`):
  Request mit `params_hash="sha256:abab…"` → im geschriebenen Auditsatz
  steht `sha256:fe6965d5…` = der selbst gerechnete Wert.

Beide Male gewogen: der mitgeschickte Wert wird gegen den gerechneten
gehalten, und wenn beide gleich wären, bricht der Lauf ab statt grün zu
melden.

### Die drei anderen, die leicht durchrutschen

* **`initiator` wird abgeleitet.** Engine: `initiator="foreground"` im
  Request ohne Marke → `background`, Verdikt `deny`; abgelaufene Marke →
  `background`; `quelle="scheduler"` mit gültiger Marke → `scheduled`; eine
  Marke ohne Frist (bloße Zeichenkette) trägt nicht. Naht: ohne Marke im
  Markenbuch bleibt es `deny`, obwohl der Request `foreground` behauptet —
  mit echtem `MarkenBuch`, nicht mit einer Attrappe.
* **Das Gestenfenster ist zweiseitig.** Erteilt + Fenster zu → `deny`
  (`gesture_window_closed`, nicht `ask` — sonst wäre die Rückfrage selbst
  die Verlängerung). Fenster offen + nicht erteilt → `ask`. Beides →
  `allow`. Rand: bei +2,000 s offen, bei +2,001 s zu.
* **Die Direktbefehl-Ausnahme.** Engine: `direct: true` + `quelle="parser"`
  → `allow`; dieselbe Aktion aus `quelle="modell"` → `ask`
  (`vorschau_pflicht`); `direct: false` + Parser → `ask`; `"Parser"`,
  `"parser "`, `"hub-parser"`, `""` sind nicht `parser`; ein `deny` wird von
  der Ausnahme nicht aufgehoben. **Naht: hier ist der Befund.**

### Uhr: eingespeist, nicht abgewartet — und warum

`daimon/hub/policy.py` hat absichtlich keine eigene Uhr; `Anfrage.jetzt`
kommt herein (Modulkopf: „Ein Entscheider, der selbst auf die Uhr sieht, ist
nicht prüfbar"). Der Prüfstand nutzt genau das: Gestenfenster und `ttl:*`
werden mit gesetzten Zeitpunkten gemessen. **Es wird nirgends gewartet.**

Begründung: ein Verifizierer, der gegen `time.time()` misst, misst einen
Zeitpunkt und keine Bedingung — das sind die vier Messfehler aus
`docs/REVIEWER-UEBERGABE-17.08.md` §4. Damit die eingespeiste Uhr nicht
selbst zur Attrappe wird, hat jede Fenstermessung eine Positivkontrolle:
dieselbe Anfrage, nur `jetzt` verschoben, **muss** ein anderes Verdikt
ergeben (`waegen()` bricht ab, wenn nicht). Belegt im Lauf:

```
Manipulation 'Uhr von 102.000 auf 102.001 gestellt (Geste bei 100.0)': 'allow' -> 'deny'
Manipulation 'Uhr von 159 auf 161 gestellt (ttl:60 ab 100)': 'allow' -> 'ask'
```

### Positivkontrollen

Zu **jeder** Verweigerung steht ein Fall, der angenommen wird — sonst wäre
„wurde abgelehnt" nicht von „die Engine lief gar nicht" zu unterscheiden.
Unter anderem: `media.playpause` mit gültiger Marke → `allow` (K3); das
passende Cache-Tripel hebt `ask` auf `allow` (K5); eine Aktion ohne
`gestenfenster_s` läuft bei geschlossenem Fenster weiter (K7); ein
literales `when` greift, während derselbe Ausdruck als Glob nichts trifft
(K4); an der Naht bekommt eine Modellaktion `ask` und nicht pauschal `deny`
(K8). Zusätzlich zwei Apparat-Kontrollen: ein absichtlich roter Test macht
den pytest-Apparat rot (Exit 1), und der Aufrufersucher findet einen
untergeschobenen echten `geste_gesehen()`-Aufruf.

---

## Kann er den Fehler sehen? — die Mutantenmatrix

> Welche Zeile im Produktivcode müsste kaputt sein, damit er rot wird — und
> hast du es ausprobiert?

Neun Zeilen. Ja, alle neun, einzeln gemessen. Ein Mutant je Akzeptanzpunkt,
plus ein neunter für die Naht (Akzeptanzpunkt 8 ist im Betrieb eine Aussage
über den Aktionsendpunkt, nicht über die Engine — ein Mutant nur in
`policy.py` könnte den Riss dort nicht sehen).

| Mutant | kaputte Zeile | zugeordnet | tatsächlich rot bei |
|---|---|---|---|
| `schranke-nur-unverstanden` | `policy.py::_params_pruefen` → `argument_out_of_range` wird zu `unparseable_argument` | K1 | K1 |
| `hash-aus-dem-request` | `policy.py:231` `hash_ = params_hash(anfrage.params)` | K2 | K1, **K2** |
| `initiator-aus-dem-request` | `policy.py::_initiator`, Kopf | K3 | K1, **K3** |
| `when-als-glob` | `policy.py::_passt`, Prädikatvergleich | K4 | **K4** |
| `cache-ohne-hash` | `policy.py::_zustimmung_gilt`, Schlüsselsuche | K5 | **K5** |
| `ttl-ohne-frist` | `policy.py::_zustimmung_gilt`, Fristprüfung | K6 | K1, **K6** |
| `geste-immer-offen` | `policy.py:256` Fensterbedingung | K7 | K1, **K7** |
| `direkt-fuer-jede-quelle` | `policy.py:280` `… and anfrage.quelle == "parser"` | K8 | K1, K5, **K8** |
| `naht-quelle-vom-absender` | `daemon.py:791` `quelle=str(anfrage.get("quelle") …)` | K8 | **K8** |

Jeder Mutant wird von seinem zugeordneten Kriterium gefangen. Kollateraltreffer
(meist K1, weil `tests/test_policy.py` dieselbe Engine benutzt) sind
notiert, nicht wegdefiniert.

Die Mutanten werden **erzeugt, nicht eingecheckt** —
`tests/mutants/T-4.4/erzeugen.sh` plus `.gitignore`, gerufen von `meta.sh`
selbst. Jeder Anker muss genau einmal im Gut-Muster stehen, sonst bricht die
Erzeugung ab.

---

## Belege

### `meta.sh` — Gut-Muster grün, neun Mutanten rot

```
$ bash tests/verify/meta.sh T-4.4
meta[T-4.4]: Mutanten werden erzeugt (…/tests/mutants/T-4.4/erzeugen.sh) ...
T-4.4: 9 Mutanten erzeugt.
meta[T-4.4]: Gut-Muster ...
meta[T-4.4]: Mutante 'cache-ohne-hash' erkannt.
meta[T-4.4]: Mutante 'direkt-fuer-jede-quelle' erkannt.
meta[T-4.4]: Mutante 'geste-immer-offen' erkannt.
meta[T-4.4]: Mutante 'hash-aus-dem-request' erkannt.
meta[T-4.4]: Mutante 'initiator-aus-dem-request' erkannt.
meta[T-4.4]: Mutante 'naht-quelle-vom-absender' erkannt.
meta[T-4.4]: Mutante 'schranke-nur-unverstanden' erkannt.
meta[T-4.4]: Mutante 'ttl-ohne-frist' erkannt.
meta[T-4.4]: Mutante 'when-als-glob' erkannt.
meta[T-4.4]: 9 Mutanten, alle erkannt.
```

### Zuordnung Mutant → Kriterium, einzeln gemessen

```
$ for m in tests/mutants/T-4.4/*/; do … done
cache-ohne-hash          -> FAIL K5
direkt-fuer-jede-quelle  -> FAIL K1 FAIL K5 FAIL K8
geste-immer-offen        -> FAIL K1 FAIL K7
hash-aus-dem-request     -> FAIL K1 FAIL K2
initiator-aus-dem-request-> FAIL K1 FAIL K3
naht-quelle-vom-absender -> FAIL K8
schranke-nur-unverstanden-> FAIL K1
ttl-ohne-frist           -> FAIL K1 FAIL K6
when-als-glob            -> FAIL K4
```

### Gegen das Gut-Muster — grün

```
$ DAIMON_FIXTURE=$PWD/tests/fixtures/known-good/T-4.4 tests/verify/T-4.4.sh; echo $?
…
Bilanz T-4.4:
K1: 4 Pruefungen, 0 rot
K2: 10 Pruefungen, 0 rot
K3: 11 Pruefungen, 0 rot
K4: 9 Pruefungen, 0 rot
K5: 8 Pruefungen, 0 rot
K6: 10 Pruefungen, 0 rot
K7: 12 Pruefungen, 0 rot
K8: 14 Pruefungen, 0 rot
0
```

### Gegen den Arbeitsbaum — rot, zweimal K8

```
$ tests/verify/T-4.4.sh; echo $?
FAIL K8: ein Absender kann sich `quelle: parser` NICHT selbst geben -- die
  Direktbefehl-Ausnahme ist Hub-Eigentum (Design 263, Akzeptanzpunkt 8):
  {'v': 1, 'ok': True, 'verdikt': 'allow', 'ausgefuehrt': False,
   'direkt': True, 'grund': 'broker_weg', …}
FAIL K8: das Verdikt haengt nicht am Feld `quelle` des Absenders:
  behauptet='allow' vs ehrlich='ask'
…
Naht K8: ehrlich={… 'verdikt': 'ask', 'direkt': False …}
      behauptet={… 'verdikt': 'allow', 'direkt': True …}
Waechter Hub-Parser: []
…
K8: 14 Pruefungen, 2 rot
1
```

Alle anderen sieben Kriterien sind gegen den Arbeitsbaum grün.

---

## Der Befund

**`daimon/hub/daemon.py:791` — die Direktbefehl-Ausnahme gehört dem Absender.**

```python
lauf = teile.ausfuehren(
    action_id=action_id, params=params,
    quelle=str(anfrage.get("quelle") or "modell"),   # <-- aus der Anfrage
    …)
```

`quelle == "parser"` ist die eine Bedingung, die in
`daimon/hub/policy.py:280` die Vorschau abschaltet:

```python
direkt = bool(eintrag.get("direct")) and anfrage.quelle == "parser"
```

Die Engine selbst ist korrekt — K8 ist auf Engine-Ebene grün. Der Riss liegt
davor: `quelle` kommt aus der Anfrage. Ein **deterministischer Hub-Parser
existiert in diesem Baum nicht** — gemessen, nicht vermutet: der Prüfstand
sucht im ganzen Produktivbaum nach einer Stelle, die `quelle="parser"`
*erzeugt* (statt sie zu vergleichen), und findet keine (`Waechter Hub-Parser:
[]`). Damit ist das Anfragefeld der einzige Weg zu `parser`, und
Akzeptanzpunkt 8 gilt nicht:

> „greift nur bei `direct: true` im Katalog **und** Erkennung durch den
> deterministischen Hub-Parser. Jede aus einer Modellausgabe stammende
> Aktion geht durch die Vorschau, unabhängig von ihrem Katalogflag"

Gemessene Wirkung: dieselbe Anfrage, einmal ohne und einmal mit
`"quelle": "parser"`:

| | Verdikt | `direkt` | Vorschau |
|---|---|---|---|
| ohne Behauptung | `ask` | `False` | ja, wartet auf den Menschen |
| mit `"quelle": "parser"` | `allow` | `True` | **nein** |

Verschärfend, aber nicht Gegenstand dieses Tasks: `aktion.sock` bekommt in
`daemon.py:1314` kein `erlaubte_units` — anders als `kontext.sock`. Es gibt
also auch keine Peer-Prüfung als zweite Schicht.

**Warum das heute noch nicht ausnutzbar ist:** `art: "ausfuehren"` schickt im
Betrieb derzeit niemand. `daimon/mind/router.py:507` lehnt jede Aktionsbitte
ausdrücklich ab („Kein Executor existiert"). Der Befund ist damit **eine
scharfe Kante ohne Zulauf** — genau die Lage, in der dieses Repo sechsmal
etwas gebaut hat, das im Betrieb niemand aufrief, nur mit umgekehrtem
Vorzeichen. Er bleibt ein Befund, weil der Zulauf mit T-4.16/T-4.19 entsteht
und die Zusage dann rückwirkend nicht gilt.

**Reparaturweg (nicht vom Verifizierer vorgeschrieben):** entweder `quelle`
im Hub setzen, solange kein Parser existiert (so das Gut-Muster), oder einen
deterministischen Parser bauen, der als einziger `parser` erzeugt. Beide
werden grün.

### Nebenbefund (kein rotes Kriterium)

`daimon/hub/policy.py:255` liest `eintrag["gestenfenster_s"]` nur als
Wahrheitswert; die *Breite* des Fensters kommt aus
`geste_gesehen(jetzt, fenster_s=…)`. Ein Katalogeintrag mit
`gestenfenster_s: 0.5` würde also ignoriert und trotzdem 2 s gelten. Nicht
rot gewertet: die Akzeptanzliste verlangt 2 s, und `VORGABE_GESTENFENSTER_S`
ist 2,0.

### Wächterlage Gestenfenster (heute grün, absichtlich)

Kein Eintrag in `config/actions/core.yaml` trägt `gestenfenster_s`, und
`geste_gesehen()` ruft im Produktivbaum niemand
(`Waechter Gestenfenster: Katalogeintraege [], Aufrufer []`). Das ist **kein**
Fehler — Vorratscode für einen Zulauf, den es nicht gibt, wäre derselbe
Fehler von der anderen Seite (CLAUDE.md, Regel 6). Deshalb steht dort ein
Wächter statt einer Prüfung: sobald der Katalog eine Aktion mit
Gestenfenster bekommt (oder eine der drei aus Design §2.5:
`clipboard.read`, `input.type`, Deklassifizierung), **muss** es im Betrieb
einen Aufrufer von `geste_gesehen()` geben, sonst wird K7 rot. Der Sucher
hat eine Positivkontrolle: er findet einen untergeschobenen echten Aufruf.

---

## Grenzen — was er NICHT misst

1. **Keine echten Sockets.** Die Naht läuft in-process über
   `Hub.aktion_anfrage`, nicht über `aktion.sock`. Ungemessen bleiben damit:
   Peer-Prüfung (bzw. deren Fehlen), Zeilenlängenbegrenzung,
   Nebenläufigkeit des Endpunkts, Verhalten bei abgebrochener Verbindung.
   Der oben genannte fehlende `erlaubte_units`-Eintrag ist **gelesen, nicht
   gemessen**.
2. **Kein Broker, keine Ausführung.** Der Broker ist im Prüflauf nicht da
   (`grund: broker_weg`). Gemessen wird das Verdikt und die
   `direkt`-Eigenschaft, nicht die Wirkung. Ausführungs-Kanarienvögel
   liegen bei den Broker-Tasks (T-4.7 ff.).
3. **Die Uhr ist eingespeist.** Ein Fehler, der erst durch die *echte*
   Zeitquelle entstünde — etwa wenn `geste_gesehen()` gegen `time.time()`
   und `Koordinator` gegen `time.monotonic()` misst — fällt hier nicht auf.
   Er ist heute auch nicht prüfbar: `geste_gesehen()` hat keinen Aufrufer.
   Der Wächter aus K7 fällt auf, sobald einer entsteht; die *Einheit* der
   beiden Uhren prüft er nicht.
4. **`session` verfällt nie.** Die Engine kennt kein Sitzungsende; ein
   `session`-Eintrag lebt so lange wie das `Policy`-Objekt. Ob der Hub eine
   Sitzung je beendet und den Cache leert, ist hier nicht gemessen.
5. **Der Circuit Breaker** (ein T-4.3.t-Kriterium) wird ausschließlich über
   K1 gemessen, also mit den Tests des Builders — kein eigener Fall.
6. **Der Katalog wird nicht bewertet.** Ob `config/actions/core.yaml` die
   richtigen Aktionen freigibt, `rationale` trägt und `direct` sparsam setzt,
   ist T-4.2. K4 prüft an den beiden Dateien nur die *Form* der Regeln
   (bekannte `when`-Schlüssel, keine Glob-Zeichen, bekannte Verdikte).
7. **Die kanonische Form ist gepinnt, nicht hergeleitet.** K2 rechnet
   `sha256` über `json.dumps(sort_keys=True, separators=(",",":"),
   ensure_ascii=False)`. Ändert jemand die Kanonisierung *absichtlich*, wird
   dieser Verifizierer rot, ohne dass ein Fehler vorläge — das ist gewollt
   (jemand soll hinsehen), aber es ist kein Beweis, dass diese Form die
   richtige ist.
8. **Das Gut-Muster ist eine Reviewer-Fassung.** Es enthält eine Abweichung
   vom Arbeitsbaum (`HERKUNFT.txt`). Sie ist die Positivkontrolle des
   Verifizierers, kein Reparaturvorschlag und kein Produktivcode.
9. **Nicht gemessen: ob der Befund je erreichbar wird.** Der Prüfstand sieht
   den Endpunkt, nicht seine künftigen Absender. Ob T-4.16/T-4.19 `quelle`
   mitschicken, entscheidet sich dort.

---

## Rücksicht auf den laufenden Betrieb

Kein `systemctl`-Aufruf, kein `kill`, kein Anhalten. Der Prüfstand bindet
keinen Socket, legt alles in ein `tempfile.TemporaryDirectory` und patcht
`daimon.hub.audit._ins_journal` auf einen Leerlauf — **es geht kein Anker
dieses Laufs ins Journal dieser Maschine** (dieselbe Vorkehrung wie
`tests/conftest.py`).

Zustand vor und nach dem Lauf unverändert: die laufenden Units
(`daimon-hub`, `daimon-auth`, `daimon-face`, `daimon-focus`,
`daimon-hookbridge`) laufen gegen ein anderes Checkout
(`~/Dokumente/Github/dAImon`), nicht gegen diesen Worktree.
`daimon-mind`, `daimon-ears`, `daimon-eyes` und `daimon-recorder` waren beim
Sitzungsbeginn bereits `inactive dead` — vorgefunden, nicht von dieser
Sitzung gestoppt.

`.claude/hooks/**` unberührt. `freeze.sh` nicht aufgerufen.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, Einfrieren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main` |
| Ausgangs-Commit | `029a557` (Merge von `reviewer/p4-verifizierer`) |
| Verifizierer unverändert | `T-4.4.sh` `d5cfe4fe…`, `t44_pruefstand.py` `20d6256a…` — dieselben Hashes wie am 18.08. |

**Warum überhaupt ein Nachlauf.** Die abgebrochene Reviewer-Sitzung hatte
`T-4.4.sh` bereits in `tests/verify/FROZEN` eingetragen, ohne einen Beleg zu
hinterlassen, dass sie ihn gefahren hat. Der Eintrag war nicht committet und
ist deshalb als offene Frage behandelt worden: zurückgenommen und von vorn
gemessen, mitsamt Mutanten.

### 1. Gegen `main` — grün

```
$ env -u DAIMON_FIXTURE tests/verify/T-4.4.sh; echo $?
Bilanz T-4.4:
K1: 4 Pruefungen, 0 rot     K5: 8 Pruefungen, 0 rot
K2: 10 Pruefungen, 0 rot    K6: 10 Pruefungen, 0 rot
K3: 11 Pruefungen, 0 rot    K7: 12 Pruefungen, 0 rot
K4: 9 Pruefungen, 0 rot     K8: 14 Pruefungen, 0 rot
0
```

78 Prüfungen, keine rot. K8 war am 18.08. an zwei Stellen rot; beide sind zu.
Der Lauf zeigt jetzt

```
Naht K8: ehrlich={… 'verdikt': 'ask', 'direkt': False …}
      behauptet={… 'verdikt': 'ask', 'direkt': False …}
```

— dieselbe Anfrage einmal ohne und einmal mit `"quelle": "parser"` ergibt
dasselbe Verdikt. Die beiden Wächter melden weiter Ruhe und dürfen das:
`Waechter Hub-Parser: []` (es gibt keinen Erzeuger von `parser`) und
`Waechter Gestenfenster: Katalogeintraege [], Aufrufer []`.

**Der Fix ist gelesen, nicht nur bestanden** (Übergabe §4.1 — „ich habe gegen
den Verifizierer gebaut"). `daimon/hub/daemon.py:917` setzt jetzt
`quelle="modell"` **fest**, statt das Feld aus der Anfrage zu lesen. Das ist
keine Prüfung, die genau die gemessene Stelle abdeckt, sondern die
strukturelle Antwort: über `aktion.sock` kommt per Definition eine fremde
Anfrage, und ein deterministischer Hub-Parser liefe im Hub. Ein Absender kann
den Wert nicht mehr setzen — auch nicht auf einem Weg, den der Prüfstand
nicht misst.

### 2. Gegen das Gut-Muster und alle neun Mutanten

```
$ bash tests/verify/meta.sh T-4.4
T-4.4: 9 Mutanten erzeugt.
meta[T-4.4]: Gut-Muster ...
meta[T-4.4]: Mutante 'cache-ohne-hash' erkannt.
meta[T-4.4]: Mutante 'direkt-fuer-jede-quelle' erkannt.
meta[T-4.4]: Mutante 'geste-immer-offen' erkannt.
meta[T-4.4]: Mutante 'hash-aus-dem-request' erkannt.
meta[T-4.4]: Mutante 'initiator-aus-dem-request' erkannt.
meta[T-4.4]: Mutante 'naht-quelle-vom-absender' erkannt.
meta[T-4.4]: Mutante 'schranke-nur-unverstanden' erkannt.
meta[T-4.4]: Mutante 'ttl-ohne-frist' erkannt.
meta[T-4.4]: Mutante 'when-als-glob' erkannt.
meta[T-4.4]: 9 Mutanten, alle erkannt.
```

Der Mutant `naht-quelle-vom-absender` ist der wichtigste: er stellt genau den
Zustand von gestern wieder her (`quelle` aus der Anfrage) und wird erkannt.
Der Verifizierer ist an der Stelle, an der repariert wurde, also **nicht**
blind geworden.

### 3. Was dieser Nachlauf NICHT ändert

Alle neun Grenzen aus §„Was er NICHT misst" gelten unverändert; keine ist
heute gemessen worden. Insbesondere läuft die Naht weiter in-process und
nicht über `aktion.sock`, und der dort genannte fehlende
`erlaubte_units`-Eintrag ist inzwischen gesetzt (`daemon.py:1461`,
`AKTION_UNITS`) — gelesen, nicht von diesem Verifizierer gemessen.
