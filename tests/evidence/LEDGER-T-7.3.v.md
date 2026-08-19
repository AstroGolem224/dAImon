# Ledger T-7.3.v — Verifizierer für den Pausenschalter

**Ausgang 18.08.: `produktdefekt-rot`** — B1 (K8) ist mit `d521148` behoben,
B2 (K9) und B3 (K5) mit `b41cd41`.
**Ausgang 19.08.: `gruen`, eingefroren.** Siehe §Nachlauf am Ende.

Stand 18.08.: der Verifizierer ist gebaut, er kann grün werden
(Gut-Muster: 10 von 10), er kann rot werden (13 von 13 Mutanten erkannt) — und gegen den echten Baum ist
er rot an drei Kriterien. Alle drei sind Befunde am Produkt, keiner ist ein
Werkzeugfehler.

---

## Provenienz

| | |
|---|---|
| Worktree | `/mnt/data/AI/repos/dAImon-t73` |
| Branch | `reviewer/p4-T-7.3v`, ausgehend von `d610d4d` |
| Rolle | `DAIMON_ROLE=reviewer` — kein Produktivcode geschrieben |
| Datum | 2026-08-18, 09:50–11:0x CEST |
| Artefakte | `tests/verify/T-7.3.sh`, `tests/verify/t73_pruefstand.py`, `tests/fixtures/known-good/T-7.3/` (29 Dateien), `tests/mutants/T-7.3/erzeugen.sh` (13 Mutanten, erzeugt) |
| Gelesen | `PLAN.md` (Vorab-Festlegungen), `docs/IMPLEMENTATION-PLAN.md` T-7.3 (Z. 1918 ff.) und T-3.15 (Z. 1135 ff.), `docs/DESIGN.md` §1.2/§4.2/§5/§7.2d/§7.4, `tests/verify/T-7.1.sh` + `t71_pruefstand.py` als Formvorlage |
| Freeze | **NICHT** aufgerufen (Auflage) |

Commits auf `reviewer/p4-T-7.3v`:

```
55c4c76  T-7.3.v: Geruest des Verifizierers
6681f69  T-7.3.v: Pruefstand -- zehn Kriterien, von aussen gemessen
ba24f4d  T-7.3.v: Pruefstand misst -- und eine Sperre gegen den eigenen Fehler
4b479d1  T-7.3.v: Gut-Muster -- der erfuellte Stand, gegen den es gruen wird
c421717  T-7.3.v: dreizehn Mutanten, erzeugt statt eingecheckt
```

---

## Zwischenfall: dieser Lauf hat zwei echte Dienste gestartet

**Um 10:16:08 hat ein Fehler in meinem Prüfstand `daimon-recorder.service`
und `daimon-eyes.service` gestartet.** Sie liefen rund 30 Sekunden und haben
**zwei Einträge ins Archiv des Nutzers geschrieben** (1 × `ocr`, 1 × `titel`).
Ich habe sie um 10:20 gestoppt; der Vorzustand (beide `inactive`) ist
wiederhergestellt.

**Ursache.** `modul_laden()` leerte `sys.modules` bei *jedem* Aufruf. K2 rief
es dreimal hintereinander; der dritte Aufruf verwarf die Einspeisungen des
ersten. Der lokale Import in `AuthAgent._mitschnitt_umschalten()` griff
danach wieder auf die echten Module zu, `schneidet_mit()` meldete „steht"
und `fortsetzen()` setzte `systemctl --user start` an den echten Units ab.

**Beleg:**

```
$ systemctl --user show -p ActiveEnterTimestamp daimon-recorder.service
ActiveEnterTimestamp=Tue 2026-08-18 10:16:08 CEST

$ stat -c '%y' ~/.local/share/daimon/archiv.db
2026-08-18 10:16:38.947178396 +0200

$ # nur lesend gezaehlt:
Eintraege ab 10:15: 2       davon Arten: [('ocr', 1), ('titel', 1)]
```

**Was daraus folgt, ist eingebaut** (Commit `ba24f4d`):

1. `modul_satz()` lädt einen *Satz* Module in einem Zug. Einzelaufrufe, die
   sich gegenseitig die Einspeisung wegräumen, gibt es nicht mehr.
2. K2 **wiegt** vor dem Eingriff, dass die Einspeisung tatsächlich in
   `sys.modules` sitzt, und bricht sonst ab.
3. **Eine Sperre hängt über den ganzen Lauf im PATH**: ein `systemctl`-
   Vorschalter, der *jede* daimon-Unit zurückweist (Exit 99). Nur K6 hängt
   für die Dauer seines Eingriffs den abbildenden Vorschalter davor. Am Ende
   jedes Laufs wird das Protokoll ausgewertet und ausgegeben.

Die Einspeisung war die erste Reihe und sie war falsch. Die zweite Reihe hat
gefehlt. Genau das ist der Unterschied zwischen „geht gut" und „kann nicht
schiefgehen" — und er hat hier zwei Zeilen Fremddaten gekostet.

**Für Matthias, falls die beiden Zeilen weg sollen** (ich fasse das Archiv
nicht an):

```bash
sqlite3 ~/.local/share/daimon/archiv.db \
  "delete from archiv where ts >= strftime('%s','2026-08-18 08:15:00');"
```

---

## Die Bauform, und warum sie so ist

### Die Falle des Auftrags: den Pausenschalter messen, ohne fremde Dienste anzufassen

Der T-7.2.v-Lauf musste die automatische Pause **stilllegen**, weil sie
`systemctl --user stop` an den echten Units gerufen hätte. Hier ist genau
diese Vorrichtung der Prüfgegenstand — stilllegen ist also keine Option.

**Lösung: ein `systemctl`-Vorschalter im PATH.** Er bildet die drei
daimon-Units auf transiente Units dieses Laufs ab
(`t73v<pid>-rec.service`, `-eyes`, `-ears`) und weist jede andere daimon-Unit
laut zurück. Jede der drei Units hält einen **echten PipeWire-Aufnahmestrom**
(`pw-record` mit eindeutigem `node.name`).

Der Prüfling läuft dabei **unverändert**: echtes `pause.stoppe()`, echtes
`subprocess.run`, echtes Argument `daimon-recorder.service`. Nur das Ziel des
Befehls ist ein anderes. Das Protokoll des Vorschalters hält jede Zeile im
**Originalwortlaut** fest — daran hängt K8, und daran hängt der Nachweis,
dass kein echter Dienst angefasst wurde.

### Die zentrale Zusage, von außen gemessen

> Die Pause schließt den Stream, sie schaltet ihn nicht stumm (§4.2).

Gemessen wird an `pw-dump`, **ein Lesevorgang vor dem Eingriff, ein
Lesevorgang danach** — kein Warten auf das Verschwinden, kein Zeitfenster.

Das allein reicht nicht: „nicht mehr in `pw-dump`" wäre auch dann wahr, wenn
`pw-dump` nichts gefunden hätte. Deshalb läuft im selben Lauf eine
**Unterscheidungskontrolle**: ein vierter Strom, der per `SIGSTOP` nur
*angehalten* wird. Er **muss danach noch da sein**. Erst damit heißt
„verschwunden" *geschlossen* und nicht *still*.

```
Stroeme vorher:  {rec: 1, eyes: 1, ears: 1, still: 1}
Stroeme nachher: {rec: 0, eyes: 0, ears: 1, still: 1}   (echter Baum)
Stroeme nachher: {rec: 0, eyes: 0, ears: 0, still: 1}   (Gut-Muster)
```

### Der kglobalaccel-Bus ist eine Attrappe, die die Pathologie nachbildet

K1 lässt die **echte** `_kglobalaccel_registrieren()` laufen — gegen einen
Bus, der sich verhält wie der kglobalaccel dieser Maschine: ein zweiter
`setShortcut` in derselben Komponente **verdrängt** den ersten (belegt am
09.08.). Ein Prüfling, der zusammenlegt, fällt damit an seiner *eigenen*
Nachschau (`allShortcutInfos`) durch.

**Positivkontrolle der Attrappe:** derselbe Lauf mit zusammengelegten
Komponenten muss rot werden. Er tut es (`ueberlebende == 1`). Ohne diese
Kontrolle wäre „alle drei überlebt" ein Zufallsbefund.

Der echte Agent läuft unter System-Python mit GTK4; ihn hier zu starten hieße,
ein Fenster auf den Schirm des Nutzers zu setzen. `gi` ist deshalb durch
Attrappen ersetzt — der gemessene Code ist trotzdem der des Prüflings.

---

## Kriterien und Messergebnis

Zehn Kriterien, einzeln, ohne `&&`-Verkettung. Ein Kriterium **ohne** Messung
zählt in der Bilanz als rot.

| | Kriterium | Echter Baum |
|---|---|---|
| K1 | Hotkey in **eigener** kglobalaccel-Komponente, an der Registrierung geprüft | ✅ |
| K2 | Das Kürzelsignal erreicht den Umschalter, und der schaltet **um** | ✅ |
| K3 | Automatische Pause: Konferenz **allein**, fremdes Mikrofon **allein** | ✅ |
| K4 | Fremder Mikrofonstrom von **außen** gemessen, echtes `pw-dump` | ✅ |
| K5 | Liste **konfigurierbar** und standardmäßig gefüllt | ❌ |
| K6 | **Die Pause schließt den Strom, sie schaltet ihn nicht stumm** | ✅ |
| K7 | `ok` heißt nicht „rc war 0"; nicht messbar ist kein Erfolg | ✅ |
| K8 | **Bild und Ton gemeinsam** abgeschaltet | ❌ |
| K9 | Sichtbarkeit am Sprite über den **Face-Diagnosezähler** | ❌ |
| K10 | `Restart=on-failure`, nicht `always` | ✅ |

```
$ tests/verify/T-7.3.sh ; echo "Exit: $?"
...
T-7.3: ROT -- 3 von 10 Kriterien rot: K5, K8, K9
Exit: 1
```

40 Einzelurteile grün, 6 rot.

---

## Die drei Befunde

### B1 (K8) — Der Pausenschalter schließt den Ton nicht

`PAUSE_UNITS` in `daimon/recorder/pause.py:46` enthält `daimon-recorder` und
`daimon-eyes`. **Die Ohren-Unit fehlt** — obwohl sie in `ERLAUBTE_UNITS`
(Z. 41) bereits steht.

Damit stoppt der Pausenschalter den Bildpfad und den Schreiber, und der
**Mikrofonstrom bleibt offen**. Genau er ist es, an dem das Plasma-Symbol
hängt (§4.2, aus dem `plasma-pa`-Quelltext verifiziert). Design §1.2 Punkt 2
sagt zu: *„Die Pause pausiert den Ton, nicht nur die Transkription."* Der
Akzeptanzpunkt sagt zu: *„Beide Pfade — Bild und Ton — werden gemeinsam
abgeschaltet."*

Der Docstring in `pause.py:13–17` begründet das mit T-7.4: solange es keinen
Tonmitschnitt gibt, seien „beide Pfade" der Archivdienst und die Augen. Das
trägt nicht: §5 (Z. 375–386) beschreibt **einen** Mikrofonstrom mit zwei
daran hängenden Pfaden, und der gehört heute `daimon-ears`. Wer den Hotkey
drückt, während `daimon-ears` läuft, bekommt ein Plasma-Mikrofonsymbol, das
weiterleuchtet — den Zustand, den §4.2 „ein Symbol, das lügt" nennt.

**Gemessen, nicht gelesen:**

```
vom Schalter angefasst: ['daimon-eyes.service', 'daimon-recorder.service']
Stroeme nachher: {rec: 0, eyes: 0, ears: 1, still: 1}
```

Der `ears`-Strom ist nach der Pause noch da. Im Gut-Muster (eine Zeile
geändert) ist er weg.

### B2 (K9) — Den geforderten Face-Diagnosezähler gibt es nicht

Der Verifikationsabsatz von T-7.3 verlangt ausdrücklich *„den Nachweis, dass
der Sprite-Zustand sich ändert, über den Face-Diagnosezähler und nicht über
eine Selbstauskunft"*.

`face/src/diag.rs` führt `voice_indikator_gezeichnet` (T-3.14) — und **keinen
Zähler für den Mitschnitt-Punkt**. `face/src/surface.rs:265` berechnet
`mitschnitt_gemalt` und wirft das Ergebnis in der nächsten Zeile weg:

```rust
        let mitschnitt_gemalt = sichtbar
            && mitschnitt_malen(...);
        let _ = mitschnitt_gemalt;          // <-- hier endet es
```

Der Kommentar zwei Zeilen darüber sagt: *„Bewusst getrennt gemalt und
getrennt gezählt — er sagt etwas anderes als der erste."* Gezählt wird er
nirgends. Die Rust-Tests in `sprite.rs:541 ff.` prüfen `mitschnitt_malen()`
direkt und können das deshalb nicht sehen — dasselbe Muster wie die sechs
Fälle in `CLAUDE.md`: gebaut, dokumentiert, grün geprüft, ohne Zulauf.

Die Kette davor **ist** vollständig und wurde hier auch gemessen: Recorder
schreibt den Herzschlag → `schneidet_mit()` → `HubState.snapshot()`
`mitschnitt` → `hub.rs` → `main.rs` → `sprite_committen`. Es fehlt genau das
letzte Glied, das die Zusage von außen prüfbar machen würde.

### B3 (K5) — „Konfigurierbar" bedient keinen Aufrufer

`daimon/recorder/pause.py:66–68` schreibt: *„hier steht die Vorgabe, ergänzt
wird in `config/redaktion.yaml` unter `konferenz`."*

- `config/redaktion.yaml` hat **keinen** `konferenz:`-Schlüssel.
- `daimon/recorder/daemon.py:main()` nennt `konferenz` **nirgends** und baut
  den Recorder mit der eingebauten Vorgabe.

Das Schlüsselwort `konferenz=` im Konstruktor funktioniert — es bedient nur
keinen Aufrufer, den es im Betrieb gibt. Die Akzeptanz sagt „Liste
konfigurierbar, standardmäßig gefüllt"; gefüllt ist sie (15 Einträge),
konfigurierbar ist sie nur für einen Prüfstand.

Der Prüfstand fällt hier **zweimal** getrennt: einmal am Leser, einmal am
Ort. Das ist Absicht — ein `konferenz:` in der YAML, den niemand liest, wäre
genau derselbe Fehler von der anderen Seite.

---

## Kann der Verifizierer den Fehler sehen?

> Welche Zeile im Produktivcode müsste kaputt sein, damit er rot wird — und
> hast du es ausprobiert?

Ja, dreizehnmal, jede einzeln gemessen. `meta.sh T-7.3`:

```
meta[T-7.3]: Gut-Muster ...
meta[T-7.3]: Mutante 'eigener-strom-zaehlt-mit' erkannt.
... (13 Zeilen)
meta[T-7.3]: 13 Mutanten, alle erkannt.
```

„Erkannt" allein ist kein Nachweis — ein Mutant, der aus dem falschen Grund
auffällt, hat sein Kriterium nicht gemessen. Deshalb die Matrix, aus 13
Einzelläufen:

| Mutant | Kaputte Zeile | rot an |
|---|---|---|
| `hotkey-in-fremder-komponente` | `agent.py` `KG_AKTION_MITSCHNITT[0]` → `daimon-auth` | **K1**, K2¹ |
| `kuerzel-nicht-verteilt` | `agent.py` `_kuerzel_verteilen`, `elif`-Zweig entfernt | **K2** |
| `schalter-schaltet-nicht-um` | `agent.py` `if schneidet_mit(rt)` → nie wahr | **K2** |
| `konferenz-loest-nicht-aus` | `daemon.py` `if pause.ist_konferenz(...)` → nie wahr | **K3**, K5² |
| `fremdes-mikrofon-loest-nicht-aus` | `daemon.py` `if fremd:` → nie wahr | **K3** |
| `eigener-strom-zaehlt-mit` | `pause.py` `if any(m in wer...): continue` entfernt | **K4**, K3³ |
| `konferenzliste-leer` | `pause.py` `KONFERENZ_VORGABE` leer | **K5**, K3⁴ |
| `pause-schaltet-stumm` | `pause.py` `systemctl stop` → `systemctl show` | **K6**, K8⁵ |
| `rc-null-heisst-ok` | `pause.py` `elif nachher > 0:` entfernt | **K7** |
| `nicht-messbar-heisst-ok` | `pause.py` `elif nachher is None:` entfernt | **K7** |
| `nur-der-recorder` | `pause.py` `PAUSE_UNITS = (RECORDER_UNIT,)` | **K8** |
| `herzschlag-ohne-frist` | `pause.py` `_frisch()` → `return True` | **K9** |
| `restart-always` | Recorder-Unit `Restart=always` | **K10** |

¹ die verdrängte Aktion verteilt auch nichts mehr · ² die eigene Liste
erreicht die Automatik über denselben Zweig · ³ dieselbe Zählung trägt beide
· ⁴ ohne Liste löst die Konferenz nicht mehr aus · ⁵ gestoppt wird dann gar
nichts. Jeder Mitbefund ist erklärt; keiner ist ein Zufallstreffer.

**Und die Gegenrichtung:** gegen `tests/fixtures/known-good/T-7.3/` — den
Stand, in dem die drei Befunde behoben sind — meldet derselbe Verifizierer
**10 von 10 grün**. Ein Verifizierer, der immer rot ist, misst so wenig wie
einer, der immer grün ist.

---

## Positivkontrollen

Jede Verweigerung hat einen Lauf daneben, in dem das Verweigerte **da** ist.

| Verweigerung | Positivkontrolle | Ergebnis |
|---|---|---|
| „kein fremder Mikrofonstrom" | echter `pw-record` unter fremder Marke | 0 → 1 → 0 |
| „der eigene Strom zählt nicht" | derselbe Strom unter Marke `daimon-ears…` | 1 → 1 (unbewegt) |
| „der Strom ist nach der Pause weg" | angehaltener Strom (`SIGSTOP`) daneben | bleibt sichtbar |
| „die Aktion überlebt in ihrer Komponente" | zusammengelegte Komponenten | überlebt genau 1 |
| „ohne Herzschlag zeigt das Sprite nichts" | frischer Herzschlag | False → True |
| „der Bericht meldet ok" | vier Fehlerfälle daneben | alle `ok=False` |

## Wägungen

Jede Manipulation wird gewogen; ist der Stand vorher und nachher gleich,
fällt das Kriterium **laut** aus („MESSUNG UNGUELTIG").

```
gewogen: schneidet_mit() um den Eingriff herum          True -> False
gewogen: fremde_mikrofonstroeme() mit echtem Fremdstrom  0 -> 1
gewogen: Aufnahmestroeme um den Eingriff herum   {…ears: 1} -> {…ears: 1}
gewogen: beleg mit vs. ohne Strom davor  'strom_gemessen' -> 'nur_unit_zustand'
gewogen: Hub-Schnappschuss mitschnitt um den Herzschlag  False -> True
gewogen: ist_konferenz('meine-videokonferenz')          False -> True
```

## Aufräumen

Vorzustand erfasst, `finally`-Restauration, eigene Runtime-Verzeichnisse,
Leckprüfung danach:

```
$ systemctl --user is-active daimon-recorder daimon-eyes daimon-ears
inactive / inactive / inactive
$ systemctl --user list-units 't73v*' --all --no-legend
(leer)
$ pgrep -af pw-record
(leer)
```

Der Vorschalter räumt am Ende jedes Laufs sein Protokoll aus; transiente
Units werden nur gestoppt, wenn ihr Name mit `t73v<pid>` beginnt.

---

## Grenzen — was er NICHT misst

1. **Er misst nicht an der PID des Dienstes.** Der Verifikationsabsatz nennt
   `pw-dump` „an der PID des Dienstes". Die Sonden dieses Laufs setzen
   `application.process.id` nicht (`pw-record` füllt es nicht), gemessen wird
   deshalb an einem eindeutigen `node.name`. Für den echten Recorder wäre die
   PID der schärfere Griff — hier ist sie nicht verfügbar, und die Marke ist
   ebenso eindeutig.

2. **Er kompiliert kein Rust.** K9 prüft den Face-Diagnosezähler an der
   *Quelle* (`diag.rs`, `surface.rs`), nicht an einem laufenden Face. `cargo`
   in jedem der 14 Bäume wäre Minuten je Lauf, und `daimon-face` läuft — es
   anzufassen war ausdrücklich untersagt. **Das ist eine benannte Grenze und
   kein grünes Kriterium:** K9 ist heute rot, und zwar an genau der Stelle,
   die nicht ausführbar geprüft werden kann. Wer den Zähler baut, sollte den
   Nachweis danach am *laufenden* Face über den Diagnose-Socket führen.

3. **Er sieht den echten Tastendruck nicht.** K1 misst die Registrierung
   (so verlangt: „prüfbar an der Registrierung, nicht am Tastendruck"), K2
   misst die Verteilung eines *nachgestellten* `globalShortcutPressed`. Dass
   `Meta+Shift+P` auf dieser Tastaturbelegung tatsächlich durchkommt, ist
   damit **nicht** geprüft — Tot-Tasten erreichen kglobalaccel nachweislich
   nie (09.08.), und diese Klasse Fehler bliebe hier unsichtbar.

4. **Der Bus ist nicht kglobalaccel.** Die Attrappe bildet *eine* gemessene
   Eigenschaft nach (Verdrängung in derselben Komponente). Andere
   Eigenheiten des echten Dienstes — Persistenz über `kglobalaccelrc`,
   Verhalten bei Namenskollision mit einer fremden Anwendung — sieht sie
   nicht.

5. **K7 misst an eingespeisten Grenzen.** Der Fall „rc=0, und der Strom läuft
   weiter" ist von außen nicht herstellbar, ohne einen Dienst zu bauen, der
   genau diesen Fehler macht. `stoppe()` nimmt `lauf` und `video` ausdrücklich
   entgegen, damit dieser Fall prüfbar ist; die Naht ist echt und ist hier
   benannt. K6 misst denselben Mechanismus ohne Einspeisung.

6. **Der Bildstrom wird nicht als Bildstrom gemessen.** Die Sonde der
   `eyes`-Unit hält einen *Audio*-Aufnahmestrom. Eine echte
   ScreenCast-Portal-Sitzung lässt sich in einem Prüflauf nicht ohne
   Nutzerinteraktion aufbauen. Gemessen ist damit „die Unit ist weg und ihr
   Strom mit ihr", nicht „die ScreenCast-Sitzung ist geschlossen".
   `stoppe()`s eigene `bildschirmstroeme()`-Messung lief gegen den echten
   `pw-dump` und meldete im Lauf ehrlich `beleg: nur_unit_zustand`.

7. **Er misst nicht am laufenden Betrieb.** Alles hier läuft gegen transiente
   Instanzen unter eigenem Namen. Ob der Hotkey in der *laufenden*
   Plasma-Sitzung registriert ist, ob der *echte* Recorder seinen Herzschlag
   schreibt, ob das *echte* Sprite den Punkt zeigt — dafür bräuchte es die
   produktiven Dienste, und die zu starten entscheidet der Nutzer.

   Wollte Matthias das nachziehen, wäre das der Weg:

   ```bash
   systemctl --user start daimon-recorder daimon-eyes
   sleep 3 && cat "$XDG_RUNTIME_DIR/daimon/mitschnitt"      # Herzschlag da?
   qdbus6 org.kde.kglobalaccel /component/daimon_recorder \
          org.kde.kglobalaccel.Component.allShortcutInfos   # Kuerzel scharf?
   pw-dump | jq -r '.[]|select(.info.props["media.class"]
                    =="Stream/Input/Audio").info.props["node.name"]'
   # Meta+Shift+P druecken, dann dieselben drei Zeilen erneut
   ```

8. **Zwei Zeilen Fremddaten im Archiv** stammen aus dem Zwischenfall oben und
   nicht aus dem Prüfling. Sie sind oben quantifiziert und benannt.

---

## Was der Zieltask braucht

Drei Änderungen, alle klein, alle im Gut-Muster vorgeführt:

1. `daimon/recorder/pause.py`: `PAUSE_UNITS` um die Ohren-Unit ergänzen.
   Sie steht in `ERLAUBTE_UNITS` schon drin. **Eine Zeile.**
2. `face/src/diag.rs` + `face/src/surface.rs`: `mitschnitt_gemalt`
   weiterreichen und in einem eigenen Feld zählen, wie es der Kommentar
   daneben ohnehin behauptet. **Sechs Zeilen.**
3. `daimon/recorder/daemon.py:main()` + `config/redaktion.yaml`: den
   `konferenz`-Schlüssel lesen bzw. anlegen — den Verweis in `pause.py:68`
   einlösen. **Zwei Zeilen und ein Listenblock.**

Danach ist `tests/verify/T-7.3.sh` grün, und zwar gemessen und nicht behauptet.

---

## Nachlauf 19.08.2026 — Reviewer-Sitzung, Einfrieren

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Arbeitsbaum | `/mnt/data/AI/repos/dAImon`, Zweig `main` |
| Ausgangs-Commit | `e5a3fc3` (nach dem Einfrieren von T-7.2) |
| Verifizierer unverändert | `T-7.3.sh` `f02b6812…`, `t73_pruefstand.py` `3cb5b013…` |

Der uncommittete `FROZEN`-Eintrag der abgebrochenen Sitzung war kein Beleg.
Zurückgenommen, von vorn gemessen.

### 1. Gegen `main` — grün

```
$ env -u DAIMON_FIXTURE tests/verify/T-7.3.sh; echo $?
…
T-7.3: GRUEN -- alle 10 Kriterien gemessen und erfuellt
0
```

Alle drei Befunde von gestern sind zu, und zwar an der Stelle, die sie
messen:

* **B1 (K8) — der Ton.** Der Schalter setzt jetzt drei `stop`-Zeilen ab, und
  der Prüfstand misst nicht die Zeilen, sondern die **Ströme**:
  `gewaegt: Aufnahmestroeme um den Eingriff herum {rec:1, eyes:1, ears:1,
  still:1} -> {rec:0, eyes:0, ears:0, still:1}`. Der vierte Strom ist die
  Unterscheidungskontrolle: ein bloß angehaltener Strom steht danach **noch**
  in `pw-dump`. „Verschwunden" heißt damit geschlossen und nicht still.
* **B2 (K9) — der Zähler.** `mitschnitt_indikator_gezeichnet` steht im
  Face-Diagnosezustand, und das Ergebnis von `mitschnitt_malen()` wird
  weitergereicht.
* **B3 (K5) — die Konferenzliste.** Sie kommt aus
  `config/daemon/redaktion.yaml` und hat einen Aufrufer.

### 2. Gegen das Gut-Muster und alle dreizehn Mutanten

```
$ bash tests/verify/meta.sh T-7.3
T-7.3: 13 Mutanten erzeugt.
meta[T-7.3]: Gut-Muster ...
… eigener-strom-zaehlt-mit · fremdes-mikrofon-loest-nicht-aus ·
  herzschlag-ohne-frist · hotkey-in-fremder-komponente · konferenzliste-leer ·
  konferenz-loest-nicht-aus · kuerzel-nicht-verteilt · nicht-messbar-heisst-ok ·
  nur-der-recorder · pause-schaltet-stumm · rc-null-heisst-ok ·
  restart-always · schalter-schaltet-nicht-um — alle erkannt.
meta[T-7.3]: 13 Mutanten, alle erkannt.
```

Drei liegen genau auf den reparierten Achsen und werden erkannt:
`pause-schaltet-stumm` (B1 — stumm statt geschlossen), `nur-der-recorder` (B1
— nur ein Pfad), `konferenzliste-leer` (B3). Dazu die beiden
Blindheits-Mutanten `nicht-messbar-heisst-ok` und `rc-null-heisst-ok`, die
prüfen, ob der Verifizierer „nicht gemessen" für grün nimmt — auch sie werden
erkannt.

### 3. Rücksicht auf den laufenden Betrieb

Die Sperre des Prüfstands hat gehalten:

```
SPERRE: 3 Aufruf(e) durchgereicht.
SPERRE: kein Aufruf an eine echte Unit.
```

Der `systemctl`-Vorschalter hat jeden Aufruf auf die transienten Units dieses
Laufs (`t73v796216*`) begrenzt. **Anders als am 18.08.** (siehe §Zwischenfall
oben) ist in diesem Nachlauf kein echter Dienst des Nutzers gestartet oder
gestoppt worden.
