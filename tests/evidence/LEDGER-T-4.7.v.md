# LEDGER T-4.7.v — Verifizierer für den DBus-Broker mit Argumentvalidierung

**Ausgang: `produktdefekt-rot`**

Der Verifizierer ist gebaut, gegen das Gut-Muster grün (51 Prüfungen), gegen
alle zwölf Mutanten rot, und gegen den Arbeitsbaum rot an drei von sechs
Kriterien — mit Beleg.

Der Kern des Tasks hält: **ein nicht genehmigter Shortcut derselben Komponente
wird abgewiesen**, und der Positiv-Kanarienvogel belegt, dass dieselbe
Maschinerie einen genehmigten Shortcut wirklich ausführt. Was nicht hält, ist
die **zweite Schicht**: `config/dbus-filter.conf` enthält fünf Zeilen, die
`xdg-dbus-proxy` nicht kennt — an dreien bricht er beim Start ab. Niemand
startet ihn ohnehin, und der Broker zeigt nirgends auf seinen Socket. Dazu
weicht die Unit an zwei Stellen von Design §7.5 ab, eine davon ist eine
Direktive, die das Design ausdrücklich als brechend führt.

---

## Provenienz

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), kein Produktivcode geschrieben |
| Worktree | `/mnt/data/AI/repos/dAImon-t47` |
| Branch | `reviewer/p4-T-4.7v` |
| Ausgangs-Commit | `d610d4d` |
| Gelesen | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` T-4.7 (Z. 1260–1271) · `docs/DESIGN.md` §7.5 (Z. 967–1000) · `tests/verify/T-4.5.sh` + `t45_pruefstand.py` + `tests/mutants/T-4.5/erzeugen.sh` + `tests/evidence/LEDGER-T-4.5.v.md` (direkter Vorgänger im Auftragsweg, samt dortigem Produktdefekt) · `tests/verify/T-4.4.sh` + `meta.sh` als Formvorlage · `man 1 xdg-dbus-proxy` |
| Neue Artefakte | `tests/verify/T-4.7.sh`, `tests/verify/t47_pruefstand.py`, `tests/fixtures/known-good/T-4.7/` (154 Dateien, 1,7 MB), `tests/mutants/T-4.7/{erzeugen.sh,.gitignore}`, dieses Ledger |
| sha256 | `T-4.7.sh` `21c0ab7826be…` · `t47_pruefstand.py` `c9241b11386d…` · `erzeugen.sh` `74b611a87ad6…` |
| Commits | `577ca08` Gerüst · `fc55bdb` Prüfstand · `39c9ccb` Zulauf-Leser · `1cd16d5` Mutanten · `f59bdf5` Mutantenfix · `3a65e01` Kanarienvogel deterministisch |
| `freeze.sh` | **nicht aufgerufen** |
| Fremde Tasks | `T-3.*`, `T-4.4`, `T-4.5`, `T-4.6`, `T-5.*`, `T-7.*` unberührt — `git status` listet ausschließlich neue `T-4.7`-Pfade |
| Laufzeit | ~30 s je Lauf (Proxy-Start, drei echte Shortcut-Aufrufe mit Wartefenster) |

---

## Was gemessen wird

Sechs Kriterien: fünf für die fünf Akzeptanzpunkte, eines für den vom
Verifikationsabsatz verlangten Positiv-Kanarienvogel. Jedes rechnet einzeln
ab; ein Kriterium ohne eine einzige Messung zählt als rot.

| | Akzeptanzpunkt | Prüfungen (Gut-Muster) |
|---|---|---|
| K1 | Eine Operation je `approved`-Aktion, feste Parameter | 8 |
| K2 | **Nicht genehmigter Shortcut derselben Komponente wird abgewiesen** | 7 |
| K3 | `xdg-dbus-proxy --filter` als zweite Schicht, `--log` aktiv, Log enthält die Versuche | 9 |
| K4 | `org.kde.kwin.Scripting.loadScript` nicht erreichbar, keine Datei | 11 |
| K5 | Sandbox nach Design §7.5 | 12 |
| K6 | Positiv-Kanarienvogel: ausgeführt, Wirkung geprüft | 4 |

### K2 — der Kern, und warum er zwei Kataloge braucht

Der Plan benennt ihn selbst: *nicht genehmigter Shortcut **derselben
Komponente***. Nicht ein fremder Dienst, nicht eine fremde Methode — dieselbe
Komponente `kwin`, dieselbe Methode `invokeShortcut`, derselbe Objektpfad
`/component/kwin`. Genau dieser Fall passiert den Methodenfilter des Proxys
und kann nur an der Katalogprüfung scheitern.

Gemessen wird an **zwei Katalogen, die sich in genau einem Wort
unterscheiden**: `pruef.desktop.eins` (Komponente `kwin`, Kurzbefehl
`Switch to Desktop 1` — steht so in `candidates.yaml`, in `core.yaml` an
keiner Stelle) einmal als `candidate`, einmal als `approved`. Gewogen wird der
Unterschied (sha256 beider Dateien), nicht behauptet.

```
Eingriff 'Kern-Kurzbefehl als approved': True (ok), Arbeitsflaeche 2fd76f05 -> b279a8cd
```

* Als `candidate`: `{"ok": false, "grund": "keine_operation"}` — und die
  Arbeitsfläche bleibt, wo sie war.
* Als `approved`: derselbe Kurzbefehl, dieselbe Codezeile — **er wirkt**. Ohne
  diese Positivkontrolle wäre „nichts geschehen" von „der Kurzbefehl tut
  ohnehin nichts" nicht zu unterscheiden.
* Und die zweite Schicht sieht den Unterschied **nicht**: derselbe Aufruf mit
  einem beliebigen Kurzbefehlsnamen passiert den Proxy mit `rc=0` und steht
  als erlaubt im Log. Genau deshalb ist die Katalogprüfung im Broker der Kern
  und nicht der Filter.

Damit die Messung überhaupt etwas sehen kann, steht der Prüfstand vorher
nachweislich **nicht** auf Arbeitsfläche 1 — sonst wäre ein Durchschlupf
unsichtbar.

### K1 — feste Parameter, gemessen am Spitzel

Derselbe Auftrag wird zweimal eingereicht: einmal mit leeren `params`, einmal
mit `{"shortcut": "Switch to Desktop 1", "component": "kwin", …}`. Der
Unterschied der beiden Auftragszeilen wird gewogen; die erzeugte
`gdbus`-Argumentzeile muss **identisch** sein, und kein Wert aus `params` darf
darin vorkommen. Dazu strukturell: `ausfuehren` greift nirgends nach
`auftrag.params` — mit Positivkontrolle des Lesers an einem eingelegten Köder.

### K4 — `loadScript`: abgewiesen, nicht ausprobiert

Vier Messgleise, keines davon lädt ein Skript:

1. **Erste Schicht, als Eigenschaft über alle Kandidaten.** Aus
   `candidates.yaml` werden **alle 400+ Einträge** als `approved` in einen
   Katalog gedreht, jeder zusätzlich mit `dienst: org.kde.kwin.Scripting`,
   `schnittstelle: org.kde.kwin.Scripting`, `methode: loadScript` versehen —
   also mit genau den Feldern, die ein Angreifer setzen würde. Keine der
   gebauten Operationen zeigt auf etwas anderes als
   `org.kde.kglobalaccel.Component.invokeShortcut`. Positivkontrolle: es
   entstehen überhaupt Operationen (sonst maße der Sweep nichts).
2. **Zweite Schicht.** Der Versuch geht durch den Proxy, auf einen Pfad, den
   es nicht gibt. Gemessen wird, **wer** das Nein sagt:
   `org.freedesktop.DBus.Error.ServiceUnknown` — der Dienst ist hinter dem
   Filter nicht einmal sichtbar. Positivkontrolle derselben Leitung: ein
   erlaubter Aufruf (`allMainComponents`) kommt durch. Sonst wäre
   „abgewiesen" von „der Proxy ist tot" nicht zu unterscheiden.
3. **Keine Datei.** sha256-Abzug über `~/.local/share/kwin/scripts` und ein
   eigens angelegtes leeres Verzeichnis, vor und nach dem Versuch — mit
   Positivkontrolle des Differs (eine selbst angelegte Datei muss auffallen)
   **vor** der Messung.
4. **Kein Skript im Compositor.** `isScriptLoaded("daimon-t47-verifizierer")`
   am echten Bus. Das ist die schärfere Grenze: **KWin quittiert `loadScript`
   mit einer Skriptkennung, auch für einen Pfad, den es nicht gibt** (am
   18.08. gemessen: `(2,)`). „Abgewiesen" und „durchgekommen" sind daran
   sauber zu trennen. Käme einer durch, hängt der Prüfstand ihn sofort wieder
   aus und sagt es laut — gestartet (`Scripting.start()`) wird nie eines.

---

## Der Positiv-Kanarienvogel — welcher, und warum harmlos

**Gewählt: `desktop.next`** (kglobalaccel-Komponente `kwin`, Kurzbefehl
`Switch to Next Desktop`), `status: approved` in `config/actions/core.yaml`.

* **Messbar:** die Wirkung steht als Eigenschaft am Bus —
  `org.kde.KWin.VirtualDesktopManager.current`. Kein Bildvergleich, kein
  Warten auf einen Menschen.
* **Harmlos:** kein Fenster ändert seinen Zustand, keine Datei entsteht, keine
  Sitzung endet, kein `daimon-*`-Dienst wird berührt. Der Katalog selbst
  begründet es: *„Umkehrbar und rein visuell. Kein Fenster ändert seinen
  Zustand, nur der Blick darauf."*
* **Wiederherstellbar, zweifach:** erst über die Umkehraktion `desktop.previous`
  (ebenfalls über den Broker, also dieselbe Maschinerie), dann im `finally`
  über die schreibbare Eigenschaft `current`. Stimmt der Vorzustand danach
  nicht, **bricht der Lauf mit einem Fehler ab** statt grün zu melden.

```
Ausgangslage fuer den Kanarienvogel: b279a8cd (Arbeitsflaeche 1)
Eingriff 'Kanarienvogel desktop.next':  True (ok), Arbeitsflaeche b279a8cd -> 2fd76f05
Eingriff 'Umkehr desktop.previous':     True (ok), Arbeitsflaeche 2fd76f05 -> b279a8cd
Arbeitsflaeche wiederhergestellt: True (2fd76f05-771a-42a8-959a-2132e2b51aad)
```

**Eine Falle, gemessen und entschärft:** `navigationWrappingAround` ist auf
dieser Maschine `false` — auf der letzten Arbeitsfläche bewirkt „Switch to
Next Desktop" **nichts**. Ein Kanarienvogel, dessen Wirkung von der
Vorposition abhängt, meldet zufällig „nichts geschehen" und wäre ein
Falschbefund; ein früher Lauf dieses Prüfstands hat genau das getan. Deshalb
stellt der Prüfstand vor dem Eingriff die erste Arbeitsfläche her und wartet
danach bis zu drei Sekunden auf den Zustandswechsel, statt einen Augenblick
zu raten.

---

## Kann er den Fehler sehen? — die Mutantenmatrix

> Welche Zeile im Produktivcode müsste kaputt sein, damit er rot wird — und
> hast du es ausprobiert?

**Zwölf Stellen. Ja, alle zwölf, einzeln gemessen.**

| Mutant | kaputte Stelle | zugeordnet | tatsächlich rot bei |
|---|---|---|---|
| `shortcut-aus-auftrag` | `brokers/dbus/broker.py:164` — Argumentzeile aus `auftrag.params["shortcut"]` | K1 | **K1** |
| `operation-generisch` | `broker.py:105` — `argumente=(aktion,)` → `("",)` | K1 | **K1**, K2, K6 |
| `status-egal` | `broker.py:119` — `if eintrag.get("status") != "approved"` | K2 | **K2** |
| `dienst-aus-katalog` | `broker.py:105` + `:128` — Dienst/Schnittstelle aus dem Katalogeintrag, Allowlist fort | K4 | **K4** |
| `kwin-durch-den-filter` | `config/dbus-filter.conf` — `--call=org.kde.KWin=*` | K4 | **K4** |
| `filter-startet-nicht` | `dbus-filter.conf` — `--own=none` (Startfehler) | K3 | **K3**, K4 |
| `filter-ohne-log` | `dbus-filter.conf` — `--log` fort | K3 | **K3**, K2, K4, K6 |
| `filter-ohne-filter` | `dbus-filter.conf` — `--filter` fort | K3 | **K3**, K2, K4, K6 |
| `proxy-ohne-zulauf` | `daimon-dbus-proxy.service` gelöscht + `Environment=` fort | K3 | **K3** |
| `sandbox-privateusers` | `daimon-dbus.service` — `PrivateUsers=yes` | K5 | **K5** |
| `sandbox-ohne-caps` | `daimon-dbus.service` — `CapabilityBoundingSet=` fort | K5 | **K5** |
| `broker-fuehrt-nicht-aus` | `broker.py:164` — meldet `ok`, ruft nichts | K6 | **K6**, K1, K2 |

Acht von zwölf treffen **genau** ihr Kriterium. Die vier breiten Treffer sind
notiert, nicht wegdefiniert:

* `broker-fuehrt-nicht-aus` und `operation-generisch` lassen den Broker nichts
  mehr bewirken — damit fallen **alle Positivkontrollen**, die auf einer
  Wirkung beruhen (K1 zählt keine Argumentzeile mehr, K2 verliert seine
  Gegenprobe). Das ist die richtige Reaktion: ein Verifizierer, der bei einem
  toten Broker weiter „abgewiesen, also gut" meldete, wäre der Fehler, gegen
  den K6 überhaupt gebaut ist.
* `filter-ohne-log`/`filter-ohne-filter` nehmen dem Log seine Aussage; die
  Log-Gleise von K2 und K6 fallen mit.

Die Mutanten werden **erzeugt, nicht eingecheckt** —
`tests/mutants/T-4.7/erzeugen.sh` plus `.gitignore`, gerufen von `meta.sh`
selbst. Jeder Anker muss genau so oft im Gut-Muster stehen wie deklariert,
sonst bricht die Erzeugung ab.

---

## Belege

### `meta.sh` — Gut-Muster grün, zwölf Mutanten rot

```
$ bash tests/verify/meta.sh T-4.7
meta[T-4.7]: Mutanten werden erzeugt (…/tests/mutants/T-4.7/erzeugen.sh) ...
T-4.7: 12 Mutanten erzeugt.
meta[T-4.7]: Gut-Muster ...
meta[T-4.7]: Mutante 'broker-fuehrt-nicht-aus' erkannt.
meta[T-4.7]: Mutante 'dienst-aus-katalog' erkannt.
meta[T-4.7]: Mutante 'filter-ohne-filter' erkannt.
meta[T-4.7]: Mutante 'filter-ohne-log' erkannt.
meta[T-4.7]: Mutante 'filter-startet-nicht' erkannt.
meta[T-4.7]: Mutante 'kwin-durch-den-filter' erkannt.
meta[T-4.7]: Mutante 'operation-generisch' erkannt.
meta[T-4.7]: Mutante 'proxy-ohne-zulauf' erkannt.
meta[T-4.7]: Mutante 'sandbox-ohne-caps' erkannt.
meta[T-4.7]: Mutante 'sandbox-privateusers' erkannt.
meta[T-4.7]: Mutante 'shortcut-aus-auftrag' erkannt.
meta[T-4.7]: Mutante 'status-egal' erkannt.
meta[T-4.7]: 12 Mutanten, alle erkannt.
$ echo $?
0
```

### Zuordnung Mutant → Kriterium, einzeln gemessen

```
$ for m in tests/mutants/T-4.7/*/; do … done
broker-fuehrt-nicht-aus  -> FAIL K1 K2 K6
dienst-aus-katalog       -> FAIL K4
filter-ohne-filter       -> FAIL K2 K3 K4 K6
filter-ohne-log          -> FAIL K2 K3 K4 K6
filter-startet-nicht     -> FAIL K3 K4
kwin-durch-den-filter    -> FAIL K4
operation-generisch      -> FAIL K1 K2 K6
proxy-ohne-zulauf        -> FAIL K3
sandbox-ohne-caps        -> FAIL K5
sandbox-privateusers     -> FAIL K5
shortcut-aus-auftrag     -> FAIL K1
status-egal              -> FAIL K2
```

### Gegen das Gut-Muster — grün

```
$ DAIMON_FIXTURE=$PWD/tests/fixtures/known-good/T-4.7 tests/verify/T-4.7.sh; echo $?
Proxy-Zulauf: {'starter': ['daimon-dbus-proxy.service'],
               'adresse': 'DBUS_SESSION_BUS_ADDRESS=unix:path=%t/daimon/dbus-proxy.sock',
               'verlangt': True}
loadScript durch den Proxy: rc=1 GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown
Arbeitsflaeche wiederhergestellt: True (2fd76f05-771a-42a8-959a-2132e2b51aad)

Bilanz T-4.7:
K1: 8 Pruefungen, 0 rot
K2: 7 Pruefungen, 0 rot
K3: 9 Pruefungen, 0 rot
K4: 11 Pruefungen, 0 rot
K5: 12 Pruefungen, 0 rot
K6: 4 Pruefungen, 0 rot
0
```

### Gegen den Arbeitsbaum — rot an K3, K4, K5

```
$ tests/verify/T-4.7.sh; echo $?
FAIL K3: ein Proxy laesst sich mit genau diesen Optionen STARTEN:
  'org.kde.kwin.Scripting=none' is not a valid dbus name
FAIL K3: im Betrieb startet ihn jemand: NIEMAND
FAIL K3: und der Broker spricht durch ihn -- `gdbus call --session` nimmt die
  Adresse aus der Umgebung: daimon-dbus.service setzt sie nicht
FAIL K4: ohne startbaren Proxy ist die zweite Schicht nicht messbar: der
  Versuch kann nicht abgewiesen werden, weil ihn niemand sieht
FAIL K5: jede Basiszeile aus Design 7.5 steht in der Unit:
  ['CapabilityBoundingSet= (hat: nichts)']
FAIL K5: und die Unit setzt PrivateUsers=yes NICHT (hat: ['yes'])

Proxy startet NICHT: 'org.kde.kwin.Scripting=none' is not a valid dbus name
Proxy-Zulauf: {'starter': [], 'adresse': '', 'verlangt': False}
Eingriff 'Kanarienvogel desktop.next': True (ok), Arbeitsflaeche b279a8cd -> 2fd76f05
Eingriff 'Kern-Kurzbefehl als approved': True (ok), Arbeitsflaeche 2fd76f05 -> b279a8cd

Bilanz T-4.7:
K1: 8 Pruefungen, 0 rot
K2: 5 Pruefungen, 0 rot
K3: 9 Pruefungen, 3 rot
K4: 3 Pruefungen, 1 rot
K5: 12 Pruefungen, 2 rot
K6: 3 Pruefungen, 0 rot
1
```

**Drei von sechs Kriterien sind gegen den Arbeitsbaum grün** — und es sind die
drei, die der Plan den Kern nennt: die feste Operation je genehmigter Aktion,
die Abweisung des nicht genehmigten Shortcuts derselben Komponente, und der
ausgeführte Kanarienvogel. Der Broker selbst hält, was T-4.7 zusagt.

---

## Der Befund

### 1. Die zweite Schicht existiert nicht — an drei Enden

**a) `config/dbus-filter.conf` startet keinen Proxy.** Fünf Zeilen sind keine
gültigen Optionen; an der ersten bricht `xdg-dbus-proxy` ab:

```
$ xdg-dbus-proxy unix:path=$XDG_RUNTIME_DIR/bus /tmp/p.sock --filter \
    '--own=org.kde.kwin.Scripting=none'
'org.kde.kwin.Scripting=none' is not a valid dbus name        (rc=1)
$ …'--own=none'          'none' is not a valid dbus name       (rc=1)
$ …'--broadcast=none'    'none' is not a valid name + rule     (rc=1)
```

`xdg-dbus-proxy` hat **keinen Verbots-Operator** (`man 1 xdg-dbus-proxy`): die
Voreinstellung ist Verweigerung, erlaubt wird mit `--see/--talk/--own/--call`.
Die beiden übrigen Zeilen — `--call=org.kde.KWin=none`,
`--call=org.kde.kwin.Scripting=none` — sind deshalb **keine Verbote**, sondern
Erlaubnisse für eine Methode namens `none`; sie starten zwar, verbieten aber
nichts. Der Kommentar darüber sagt „AUSDRÜCKLICH NICHT, und zwar an beiden
Schichten"; tatsächlich macht er die Datei unbrauchbar und nimmt damit auch
den drei ehrlichen `--call`-Erlaubnissen die Wirkung.

Nebenbefund derselben Datei: ihr Kopf beschreibt die Übergabe als
`--args=<fd>`. `--args=FD` liest **NUL-getrennte** Argumente; eine Datei mit
Kommentaren und Zeilenumbrüchen geht so nicht hinein. Der Prüfstand liest sie
wohlwollend (Kommentare fort, NUL-verbunden) — auch dann startet sie nicht.

**b) Niemand startet ihn.** `xdg-dbus-proxy` kommt im Produktivbaum an drei
Stellen vor: im Modulkopf von `daimon/brokers/dbus/broker.py`, im Kopf der
Filterdatei und in `docs/DESIGN.md` — **dreimal Prosa, keinmal ein Aufruf**.
`config/systemd/daimon-dbus.service` hat weder `ExecStartPre` noch eine
zweite Unit; es gibt keine `daimon-dbus-proxy.service`.

> Der erste Lauf dieses Prüfstands hat den Modulkopf von `broker.py` selbst
> für den Zulauf gehalten und „gefunden" gemeldet. Der Leser überspringt
> jetzt Docstrings und trägt dafür eine Negativkontrolle. Ein Absatz, der
> eine Zusage beschreibt, darf sie nicht selbst belegen.

**c) Selbst mit laufendem Proxy führe der Weg daran vorbei.** `Operation.argv()`
ruft `gdbus call --session`; welcher Bus das ist, entscheidet
`DBUS_SESSION_BUS_ADDRESS`. `daimon-dbus.service` setzt die Variable nicht —
der Broker spräche also mit dem echten Sitzungsbus, auch wenn ein Proxy liefe.

**Was daraus folgt:** Akzeptanzpunkt 3 („`xdg-dbus-proxy --filter` davor als
zweite Schicht, `--log` aktiv") gilt heute nicht, und mit ihm fällt der letzte
Satz des Verifikationsabsatzes („der Proxy-Log enthält die Versuche"): es gibt
keinen Log. Damit ruht `loadScript`-Unerreichbarkeit **allein** auf der ersten
Schicht. Die hält — der Sweep über alle Kandidaten zeigt es —, aber sie hält
allein, und der Modulkopf des Brokers sagt selbst, warum das zu wenig ist:

> „Die zweite Schicht ist nicht die Begründung für eine schwache erste. Sie
> fängt den Fall ab, in dem dieser Code einen Fehler hat — und umgekehrt."

Das „und umgekehrt" gibt es nicht.

### 2. Die Sandbox weicht an zwei Stellen von Design §7.5 ab

```
FAIL K5: jede Basiszeile aus Design 7.5 steht in der Unit:
  ['CapabilityBoundingSet= (hat: nichts)']
FAIL K5: und die Unit setzt PrivateUsers=yes NICHT (hat: ['yes'])
```

* **`CapabilityBoundingSet=` fehlt.** Sie ist die zweite Zeile des Basisblocks
  („Basis für alle dAImon-Units außer egress"). Ohne sie behält der Dienst den
  Capability-Satz seines Elternprozesses.
* **`PrivateUsers=yes` ist gesetzt** — und steht in §7.5 unter **„Direktiven,
  die brechen"**, mit Begründung: *„bricht uid-ACLs und Peer-Credentials"*.
  Das ist nicht irgendeine Härtung: Peer-Credentials sind nach Design §1.3/§6.2
  das, was **an die Stelle der gestrichenen Signatur getreten ist**. T-4.5.v
  hat gemessen, dass die Peer-Prüfung im Auftragsweg fehlt; diese Zeile würde
  sie zusätzlich unterlaufen, sobald jemand sie baut. Zwei Enden desselben
  Risses.

Der Kommentar der Unit nennt außerdem den Sitzungsbus-Socket „unten
ausdrücklich in `ReadWritePaths`" — dort steht `%t/daimon`, nicht `%t/bus`.
Das ist nicht gemessen (der Dienst läuft nicht), aber es passt ins Bild: die
Unit beschreibt eine Konfiguration, die sie nicht hat.

### Der Zulauf — die Frage vor jedem „fertig"

| Ende | Zulauf heute | Beleg |
|---|---|---|
| `daimon-dbus.service` | Unit existiert mit `[Install]`, ist aber `inactive (dead)` | `systemctl --user list-units 'daimon*'` |
| Broker-Socket | `dbus-broker.sock` liegt nicht in `/run/user/1000/daimon/` — seit dem Neustart um 09:36 hat der Dienst nicht gelaufen | `ls /run/user/1000/daimon/` |
| Proxy | **kein Zulauf, an keiner Stelle** | siehe 1b |

Der Broker ist also gebaut und funktionsfähig — der Kanarienvogel beweist es
—, aber im Betrieb ruft ihn heute niemand. Das ist für T-4.7 kein Befund
(T-4.16/T-4.19 verdrahten ihn), für die zweite Schicht schon: die ist auch
dann nicht da, wenn der Broker anläuft.

---

## Grenzen — was er NICHT misst

1. **Der Proxy wird vom Prüfstand gestartet, nicht von `systemd`.** Gemessen
   ist: *diese Optionsliste startet einen filternden, protokollierenden
   Proxy*, und *im Baum gibt es (bzw. gibt es nicht) eine Unit, die ihn
   startet, sowie eine Adresse, die auf ihn zeigt*. **Nicht** gemessen ist,
   ob die Unit des Gut-Musters unter `systemd` tatsächlich anläuft — ihre
   `ExecStart`-Zeile mit Prozesssubstitution ist eine mögliche Fassung, keine
   geprüfte. Ein Reviewer darf keine Unit installieren; das wäre ein Eingriff
   in die laufende Sitzung.
2. **`--args=FD` ist nicht als Kriterium gemessen.** Der Prüfstand liest die
   Filterdatei wohlwollend (Kommentare fort, NUL-verbunden). Dass die Datei so,
   wie ihr eigener Kopf sie beschreibt, nicht in `--args` passt, steht als
   Nebenbefund im Ledger — als Kriterium hätte es das Gut-Muster gezwungen,
   eine kommentarlose Datei zu führen.
3. **Der laufende Desktop ist die Messumgebung.** Fällt KWin aus, ist der
   Kanarienvogel nicht messbar; der Lauf wäre dann `umgebungs-blockiert` und
   nicht grün. Ebenso bei nur einer Arbeitsfläche — der Prüfstand sagt das
   ausdrücklich, statt still durchzulaufen.
4. **`loadScript` wird nicht scharf ausprobiert.** Gemessen ist die Abweisung
   (durch den Proxy), dass keine Datei entsteht und dass KWin kein Skript
   dieses Namens kennt. **Nicht** gemessen ist, was ein tatsächlich geladenes
   und gestartetes Skript anrichten könnte — das wäre nicht zurückzudrehen und
   ist absichtlich unterlassen.
5. **Der Katalog-Sweep ist eine Eigenschaftsprüfung, kein Beweis.** Er dreht
   alle Kandidaten auf `approved` und legt Felder ein, die ein Angreifer
   setzen würde. Ein Katalogfeld, das der Broker morgen liest und das hier
   nicht vorkommt, fällt nicht auf.
6. **Die Ticketeinlösung ist ein Stummel.** `ticket_einloesen` gibt in jedem
   Lauf `None` zurück; die Reihenfolge „Ticket vor der Tat" ist T-4.5 und dort
   gemessen. Hier wird sie nicht noch einmal geprüft — auch nicht, was
   passiert, wenn der Hub das Ticket ablehnt, während `gdbus` schon läuft.
7. **Der Broker-Dienst (`daemon.py`) ist nicht getrieben.** Gemessen ist die
   Broker-Klasse und ihre Operationstabelle, nicht der Socket, nicht
   `MAX_BYTES`, nicht Nebenläufigkeit, nicht die Peer-Prüfung (die es nach
   T-4.5.v ohnehin nicht gibt).
8. **Design §7.5 wird als Text gelesen.** Die Basiszeilen kommen aus dem
   ini-Block, die verbotenen Direktiven aus einer Namensliste mit
   Positivkontrolle gegen den Design-Text. Ändert jemand §7.5 in *Prosa*
   (statt im Block), misst der Prüfstand die alte Regel weiter.
9. **Die Sandbox ist gelesen, nicht gefahren.** Ob `ProtectSystem=strict` mit
   `ReadWritePaths=%t/daimon` den Broker tatsächlich laufen lässt — und ob er
   an den Bus-Socket kommt —, ist ungemessen. Das ginge nur, indem man die
   Unit startet.
10. **Das Gut-Muster ist eine Reviewer-Fassung.** Es enthält drei Abweichungen
    vom Arbeitsbaum (`HERKUNFT.txt`). Sie sind die Positivkontrolle des
    Verifizierers, kein Reparaturvorschlag und kein Produktivcode.

---

## Rücksicht auf den laufenden Betrieb

**Eingegriffen wurde genau einmal je Lauf, an einer Stelle:** die aktive
Arbeitsfläche. Drei Wechsel (Kanarienvogel, Umkehr, Kern-Positivkontrolle),
jeder gewogen, alle zurückgestellt. Der Prüfstand liest den Vorzustand vor dem
ersten Eingriff und schreibt ihn im `finally` zurück — auch wenn eine Messung
wirft; stimmt er danach nicht, bricht er ab.

Kein `systemctl`, kein `kill`, kein Anklopfen an einem produktiven
`daimon`-Socket, kein Start eines Wahrnehmungsdienstes
(`daimon-eyes`/`-ears`/`-recorder` blieben `dead`). Proxy, Kataloge und
Protokolle liegen in einem `tempfile.TemporaryDirectory`.

Nach allen Läufen (Gut-Muster, Arbeitsbaum, zwölf Mutanten, vier `meta.sh`-Läufe):

```
$ pgrep -c xdg-dbus-proxy                     0
$ ls -d /tmp/t47-*                            (nichts)
$ gdbus introspect … /Scripting | grep -c node Script   2   (wie vorgefunden)
$ … VirtualDesktopManager current             2fd76f05-…  (Vorzustand)
$ systemctl --user list-units 'daimon*' --state=active
  daimon-auth, daimon-face, daimon-focus, daimon-hookbridge, daimon-hub
  + 3 .socket-Units, 2 Timer                  (unverändert seit Sitzungsbeginn)
```

`.claude/hooks/**` unberührt. `freeze.sh` nicht aufgerufen. `git status` listet
ausschließlich die neuen `T-4.7`-Pfade; die Bäume der parallel laufenden
Sitzungen (`T-3.*`, `T-4.4`, `T-4.5`, `T-4.6`, `T-5.*`, `T-7.*`) sind nicht
angefasst.
