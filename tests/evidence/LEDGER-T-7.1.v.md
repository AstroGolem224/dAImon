# Ledger T-7.1.v — Verifizierer: Archivdienst und Schema

**Ausgang: `produktdefekt-rot`**

Der Verifizierer ist gebaut, gegen das Gut-Muster grün, gegen alle dreizehn
Mutanten rot — und gegen den echten Baum **rot an genau einer Stelle**:

> **`daimon-fs.service` darf ins Archivverzeichnis schreiben.** Akzeptanzpunkt 1
> von T-7.1 sagt, `daimon-recorder` sei der **einzige** Prozess mit Schreibrecht
> aufs Archiv. Gemessen an allen 21 Unit-Dateien sind es zwei.

Alle acht übrigen Kriterien sind gemessen erfüllt, einschließlich der Frage,
die T-7.5.v hierher übergeben hat: **ja**, eine lesende `mode=ro`-Verbindung
trägt neben einem *laufenden* Schreiber in WAL. Grenze 9 ist damit geschlossen.

---

## Provenienz

| | |
|---|---|
| Gebaut von | Reviewer-Sitzung vom 2026-08-18, `DAIMON_ROLE=reviewer` |
| Worktree | `/mnt/data/AI/repos/dAImon-t71`, Branch `reviewer/p4-T-7.1v` |
| HEAD beim Bau | `3c1b843ead93cd638b0c3a61083f45d0441650fa` |
| Gelesen, in dieser Reihenfolge | `PLAN.md` §Vorab-Festlegungen · `docs/IMPLEMENTATION-PLAN.md` Block **T-7.1** (Z. 1836 ff., Akzeptanzliste + Verifikationsabsatz ab Z. 1861) und **T-7.1b** direkt darunter · `docs/DESIGN.md` §7.5 (Sandbox, Zeile `recorder`), §7.2d (Aufbewahrungsstufen), §5.2 (Markierung und Senken) · `docs/REVIEWER-UEBERGABE-17.08.md` §1, Zeilen „Art gehört zum Absender" (`548330d`, **T-7.1.v fehlt**) und „Transkript im Archiv" (`ed58559`) · **`tests/evidence/LEDGER-T-7.5.v.md`** (Commit `8f001a4`), Grenze 9 · `tests/verify/T-7.2.sh` + `t72_pruefstand.py` + `t72_dienst.py` als Formvorlage, dazu `tests/verify/t75_pruefstand.py` und `tests/mutants/T-7.5/erzeugen.sh` |
| Produktquelle gelesen | `daimon/recorder/{store,daemon,suche,pause,redaktion,melder}.py`, `daimon/common/{config,protocol,taint}.py`, `config/systemd/daimon-{recorder,eyes,fs}.service` — **nur lesend** |
| Nicht getan | `freeze.sh` **nicht** gerufen (eigener autorisierter Task) · `.claude/hooks/**` **nicht** angefasst · `T-4.4.*`, `T-4.5.*`, `T-4.6.*`, `T-5.9.*`, `T-7.2.*`, `T-7.5.*`, `meta.sh` **nicht** angefasst · kein Produktivcode geändert, kein Merge, kein Push · **kein `systemctl start|stop` an einer `daimon-*`-Unit**, keine transiente Unit eines ihrer Namen |

Neue Dateien:

```
tests/verify/T-7.1.sh
tests/verify/t71_pruefstand.py                    (der Prüfstand, 9 Kriterien)
tests/verify/t71_dienst.py                        (der Treiber im Sandkasten)
tests/fixtures/known-good/T-7.1/                  (320 K, 47 Dateien)
tests/mutants/T-7.1/erzeugen.sh + .gitignore      (13 Bäume, NICHT eingecheckt)
tests/evidence/LEDGER-T-7.1.v.md
```

Der Nachweis läuft zweistufig; `meta.sh` ruft den Erzeuger selbst auf:

```
bash tests/verify/meta.sh T-7.1
```

---

## Der Befund

### `daimon-fs.service` hat Schreibrecht aufs Archiv

`config/systemd/daimon-fs.service` führt `ProtectHome=` **auskommentiert**
(Zeile 30), mit Begründung: `ProtectHome=tmpfs` blendet `$HOME` aus, und dort
liegt das venv, aus dem der `ExecStart` dieses Dienstes startet. Der Kommentar
darunter (Zeile 43) sagt, was stattdessen gelten soll:

> „Diese Liste ist die eigentliche Schranke des FS-Brokers, und sie ist
> absichtlich kurz. Wer sie verlängert, verlängert die Reichweite des Pets."

Gemeint ist die `BindPaths=`-Liste. Sie ist keine Schranke. `BindPaths=`
blendet ein; **es blendet nichts aus** — eine Schranke wird sie erst
zusammen mit einem `ProtectHome=`, und genau das steht hier aus. Was den
Rest von `$HOME` schreibgeschützt machen müsste, wäre `ProtectHome=`, und das
steht hier auskommentiert. `ProtectSystem=strict` erledigt es nicht — gemessen,
isoliert, mit Positiv- und Negativkontrolle:

```
$ systemd-run --user --wait --collect --pipe --quiet \
      --property=ProtectSystem=strict \
      /bin/sh -c 'echo x > $HOME/.local/share/daimon/t71probe/a \
                  && echo SCHRIEB || echo VERWEIGERT'
SCHRIEB

$ … --property=ProtectSystem=strict --property=ProtectHome=read-only …
/bin/sh: /home/itiger013/.local/share/daimon/t71probe/a: Das Dateisystem ist nur lesbar
VERWEIGERT

$ … --property=ProtectSystem=strict --property=BindPaths=/home/itiger013/Dokumente …
SCHRIEB
```

Die dritte Zeile ist die entscheidende: eine `BindPaths=`-Zeile ändert am
Schreibrecht auf einen *anderen* Pfad unter `$HOME` nichts.

**Was das bedeutet.** Der Dateisystem-Broker ist der Dienst, der mit
nutzerkontrollierten Pfaden hantiert (`openat2`, `RESOLVE_BENEATH`). Er ist
damit einer der exponiertesten im Projekt. Kompromittiert kann er heute
`~/.local/share/daimon/archiv.db` **überschreiben** — und alles, was dort
steht, geht mit einer frischen Rundenmarke über T-7.5 in Durchgang 2 des
Modells. Die Zusage „getrennte Units, damit eine kompromittierte Live-Wahr-
nehmung nicht schreiben kann" hält für `eyes` (gemessen) und für achtzehn
weitere Units (gemessen) — und für `fs` nicht.

**Sie ist nicht die Zusage, an der der Verifizierer aufgehängt war.** Der
Auftrag nannte `eyes`; `eyes` ist sauber. Der Befund fiel, weil K1 die Frage
nicht an einer Unit stellt, sondern an **allen** — „einziger" ist eine
Mengenaussage, und eine Stichprobe hätte sie nicht beantwortet.

**Der Fix ist eine Zeile**, und der Verifizierer schreibt nicht vor, welche.
Das Gut-Muster nimmt die kleinste:

```ini
InaccessiblePaths=-%h/.local/share/daimon
```

`ProtectHome=tmpfs` plus `BindPaths=` für Dokumente, Downloads, Trash und das
venv wäre die gründlichere; ein anderer Ort für das venv die dauerhafte.

---

## Die übernommene Frage aus T-7.5.v — beantwortet

> **Grenze 9, `LEDGER-T-7.5.v.md`:** „Der Prüfstand schließt seinen Schreiber
> sauber (Checkpoint), bevor die `mode=ro`-Verbindung liest. Im Betrieb hält
> `daimon-recorder` die Datenbank dauerhaft offen, und die lesende Verbindung
> braucht dann ein beschreibbares `-shm`. Dass das unter derselben uid trägt,
> steht im Code und ist NICHT gegen einen laufenden Schreiber geprüft."

**Sie trägt.** K9 misst es an einem Schreiber, der die ganze Zeit offen bleibt:
eine transiente Unit mit den Direktiven aus `daimon-recorder.service`, in der
`daimon.recorder.daemon.main()` läuft, die Datenbank in WAL hält und über
`recorder.sock` bedient. Zehn Prüfungen, jede mit ihrer Klammer:

| | gemessen |
|---|---|
| Der Schreiber läuft | `systemctl is-active` = `active` **während** der Messung |
| Journalmodus | `PRAGMA journal_mode` über die **lesende** Verbindung = `wal` — ohne WAL wäre die ganze Frage gegenstandslos, und „hat geklappt" sähe genauso aus |
| Der Kanarienvogel geht durch den Dienst | `melde()` über den echten Socket, `ok:true`, `id>0` |
| Die lesende Verbindung sieht ihn | `mode=ro`-Zeilenzahl **vor** und **nach** genau dieser Meldung: `n` → `n+1` |
| `-shm` | liegt neben dem laufenden Schreiber, Modus **0600** — für den Eigentümer beschreibbar; genau das braucht der Leser |
| Der **echte** Leseweg | `Archivsuche(pfad).freigeben(schein, frage)` findet den Kanarienvogel, während der Schreiber offen ist |
| Die Gegenrichtung | eine **offen gehaltene** `mode=ro`-Verbindung hält den Schreiber nicht auf: die zweite Meldung kommt durch, `ok:true`, und eine frische Verbindung sieht `n+2` |

Und sie ist als Mutant belegt: `shm-nur-lesbar` setzt `-shm` in
`_rechte_ziehen` auf `0o400`. Der Recorder merkt nichts — er hält die Datei
offen. **Jede** lesende Verbindung scheitert dann, und die Archivsuche gibt
still nichts mehr her. Genau der Ausfall, den Grenze 9 befürchtet hat, und der
Verifizierer sieht ihn (K9 2 rot, K4 1 rot).

---

## Was gemessen ist, und woran

**126 Prüfungen, neun Kriterien**, jedes einzeln abgerechnet, ohne
`&&`-Verkettung. Ein Kriterium ohne eine einzige Messung zählt in der Bilanz
**als rot**.

| | Kriterium (Quelle) | Prüf. | gemessen woran |
|---|---|---|---|
| K1 | Getrennte Schreibrechte: `daimon-recorder` schreibt, `eyes` bleibt lesend, sonst niemand (Akzeptanz 1) | 26 | **Eine Frage je Unit, an allen 21**, mit den Direktiven genau dieser Unit, **während der Archivdienst läuft** und die Datenbank offen hält. Nicht am Text der Unit-Datei: an dem, was ein Prozess darunter noch kann. `eyes` wird zusätzlich als **lesend** belegt (`ARCHIV_R=LAS`) — ohne das wäre „kann nicht schreiben" von „sieht das Verzeichnis gar nicht" nicht zu unterscheiden. Positivkontrollen: der Recorder-Sandkasten **schreibt** (sonst wäre jede Verweigerung ein leeres Verzeichnis), und dieselbe Zeile ohne jede Härtung schreibt auch. Eine Unit, deren Schreibrecht **nicht messbar** ist, zählt rot — „einziger" ist keine Aussage über 19 von 21 |
| K2 | Härtung nach Design §7.5: `ProtectHome=tmpfs`, schreibbar **nur** das Archivverzeichnis, `RestrictAddressFamilies=AF_UNIX` (Akzeptanz 2) | 12 | sieben Fragen unter den Direktiven des Prüflings und **dieselben sieben ohne sie**: `$HOME/.config` ist im Prozess **weg** (draußen da); das Archivverzeichnis bleibt **schreibbar**; `~/.local/state/daimon`, `~/.local/share` und `~/.config/daimon` (**der anthropic-token**) sind es **nicht** — und ohne Härtung alle drei schon; `AF_INET` geht **nicht** auf, `AF_UNIX` schon. Die letzte Zeile ist die Kontrolle, dass „kein Netz" nicht bloß ein toter Prozess ist |
| K3 | Kein Modelltext im Prozess (Akzeptanz 2) | 20 | **zwei Hälften, und nur eine ist ganz messbar.** *Importmenge:* ein frischer Interpreter lädt `recorder.{daemon,store,suche}` und meldet `sys.modules` — 16 `daimon`-Module, kein `daimon.mind`, kein `daimon.egress`, kein `daimon.persona`, und keins von `anthropic`, `openai`, `httpx`, `requests`, `urllib3`, `torch`, `transformers`, `llama_cpp`, `sherpa_onnx`, `onnxruntime`. Positivkontrolle: dieselbe Messung **findet** ein Modellpaket, wenn eins geladen wird. *Laufzeit:* ein **Zeitpunkt** am laufenden Dienst — `environ` ohne `ANTHROPIC`, `cmdline` ohne Persona/Modell, offene Deskriptoren ohne `~/.config/daimon` und ohne Modelldatei, `maps` ohne Modell- oder Stimmdatei. Positivkontrolle der Deskriptor-Messung: die **Archivdatei** steht in derselben Liste |
| K4 | `$XDG_DATA_HOME/daimon/archiv.db` mit Volltextindex, Datei 0600, Verzeichnis 0700 (Akzeptanz 3) | 10 | `Archiv()` unter eigenem `XDG_DATA_HOME`, **unter `umask 022`** — die großzügige Richtung: die Rechte müssen trotzdem 0600/0700 sein. Positivkontrolle: eine gewöhnliche Datei **daneben, im selben Verzeichnis, unter derselben umask** trägt 0644 — die Messung kann den Unterschied sehen. `-wal` und `-shm` ebenfalls 0600. Der Index: `archiv_fts` steht in der Datenbank, findet den Kanarienvogel, findet eine Wortmarke, die nicht drinsteht, **nicht**, und verliert die Zeile wieder, wenn sie gelöscht wird |
| K5 | Aufbewahrung je Art getrennt — **der Text überlebt die Frames**; Rohaudio gar nicht (Akzeptanz 4) | 19 | **Ein Zeitstempel, vier Arten.** Titel, OCR, Transkript und Frame gehen mit demselben, künstlich auf **49 h** vorgerückten `ts` ins Archiv; ein `aufraeumen()` — der Frame ist weg, **alle drei Texte stehen noch**. Kein Warten, kein Zeitfenster. Danach dieselben drei mit **31 Tagen**: jetzt gehen auch sie, und die 49 h alten bleiben. Gewogen: 4 Zeilen vorher, der Bestand hat sich geändert, 6 → 3. Positivkontrolle der Frist selbst: eine Minute alte Einträge räumt niemand weg — das Aufräumen löscht nicht einfach alles. Rohaudio: `audio`, `rohaudio`, `pcm`, `wav`, `samples` **wörtlich** geprüft (nicht über `VERBOTENE_ARTEN` — eine leergeräumte Konstante ließe die Schleife nullmal laufen), jede abgewiesen; eine Art ohne Frist ebenso |
| K6 | Harte Obergrenze **verdrängt**, sie meldet keinen Fehler (Akzeptanz 5) | 11 | 40 Einträge zu 2 KB gegen eine Grenze von 32 KB, an der **echten** `Recorder`-Klasse mit diesem Archiv. `melde()` **vor** dem Aufräumen: `ok`. `aufraeumen()`: keine Ausnahme, `verdraengt>0`, Belegung unter der Grenze, und es weichen die **ältesten** — jede weggeräumte id kleiner als jede verbliebene, der älteste weg, der jüngste da. `melde()` **nach** dem Aufräumen: `ok`, neue id — der Dienst bedient weiter. Positivkontrolle: unter der Grenze verdrängt derselbe Aufruf **nichts**. Dazu der Takt: Intervall 3600 s, nach einer Stunde fällig, nach einer Minute nicht |
| K7 | Aufbewahrungsstufe je Eintrag aus §7.2d, Vorgabe `redacted` (Akzeptanz 6) | 8 | ohne Angabe steht `redacted` **in der Zeile**; die vier Stufen sind genau die vier aus §7.2d; `full` und `redacted` landen am Eintrag; `metadata_only` legt Herkunft und Zeit ab und Inhalt **nicht** (`text=''`, `daten=NULL`); `transient` schreibt **gar nicht** — gewogen an der Zeilenzahl **und** am `AUTOINCREMENT`-Zähler (`sqlite_sequence`), denn eine Zeile, die geschrieben und sofort gelöscht würde, ließe den Zähler stehen, wo sie war. Positivkontrolle: derselbe Aufruf mit `redacted` bewegt beide Zahlen. Eine unbekannte Stufe wird abgewiesen |
| K8 | Alles im Archiv ist `tainted` — **als Typ, nicht als Spalte** (Akzeptanz 7, §5.2) | 10 | `PRAGMA table_info` zeigt **keine** Markierungsspalte; `lesen()` **und** `suchen()` geben `Marked(..., TAINTED)`. Der Roundtrip über eine **Prozessgrenze**: eine zweite, frische Python-Sitzung öffnet dieselbe Datei und findet `Marked`/`TAINTED` (am 14.08. ging eine Markierung genau an so einer Grenze verloren, T-6.1-3.v) — mit der Kontrolle, dass sie wirklich den Kanarienvogel gelesen hat und nicht eine leere Zeile. Und was die Markierung **wert** ist, an der echten Senkentabelle aus T-3.13b: gesperrt gegen `durchgang1`, `tts_ungefragt` und `langzeitgedaechtnis`, erlaubt in `durchgang2`. Positivkontrolle: `user_ptt` kommt durch dieselbe Senke — gesperrt ist die **Markierung**, nicht der Aufruf |
| K9 | WAL: lesen neben dem **laufenden** Schreiber (Grenze 9 aus `LEDGER-T-7.5.v.md`) | 10 | siehe oben |

**Gemessen wird an echten Prozessen.** `t71_dienst.py` ruft
`daimon.recorder.daemon.main()` des Prüflings — dieselbe `Redaktion`, dasselbe
`Archiv`, derselbe `ipc.listen`, derselbe Aufräumtakt. Der Sandkasten kommt
Zeile für Zeile aus `<pruefling>/config/systemd/daimon-recorder.service`. Der
Prüfstand baut davon nichts nach.

---

## Zwei Sätze, die dieser Verifizierer nicht sagt

**„eyes kann nicht schreiben" ist hier nie allein gemessen.** Neben jeder
Verweigerung steht dieselbe Zeile aus einem Sandkasten, der es darf, und
zusätzlich ein Lesezugriff, der gelingt. Zwischen Probe und Gegenprobe ist
genau **eine** Bedingung anders:

```
eyes-Direktiven         -> VERWEIGERT   |  recorder-Direktiven    -> SCHRIEB
eyes-Direktiven         -> LAS          |  (dieselbe Datei)
19 weitere Units        -> VERWEIGERT   |  ohne jede Haertung     -> SCHRIEB
~/.config im Sandkasten -> WEG          |  ohne Haertung          -> DA
AF_INET unter Haertung  -> ZU           |  ohne Haertung          -> OFFEN
Token-Verzeichnis       -> VERWEIGERT   |  ohne Haertung          -> SCHRIEB
Frame nach 49 h         -> weg          |  Text nach 49 h         -> da
Text nach 31 Tagen      -> weg          |  Text nach 1 Minute     -> da
transient               -> keine Zeile  |  redacted               -> Zeile
Archivtreffer/durchgang1-> gesperrt     |  user_ptt/durchgang1    -> erlaubt
Archivtreffer/durchgang2-> erlaubt      |
```

**Eine nicht messbare Antwort ist kein Erfolg.** Startet der Sandkasten einer
Unit nicht, wird das rot vermerkt und nicht als „hat nicht geschrieben"
gezählt. Beim Bau hat genau das zugeschlagen: zwei Mutanten schrieben ihre
`# MUTATION`-Marke als **Zeilenkommentar hinter eine systemd-Direktive**, die
Unit wurde ungültig, der Sandkasten kam nicht hoch — und `meta.sh` meldete
beide brav als „erkannt". Erkannt waren sie am *falschen* Grund. Die Marke
steht jetzt auf einer eigenen Zeile; beide fallen seither an ihrem Kriterium.

---

## Jede Manipulation ist gewogen

Dieser Prüfstand verändert **keine Produktdatei**. Seine Eingriffe:

1. **Der Mutationsanker.** `erzeugen.sh` bricht ab, wenn ein Anker nicht
   **genau einmal** im Gut-Muster steht. Ein Mutant, der nichts geändert
   hätte, entsteht gar nicht erst.
2. **Die Rechte-Messung (K4)** setzt `umask 022`, also die *großzügige*
   Richtung, und stellt die 0644-Datei daneben. Wäre die umask streng, sähe
   „0600" auch bei kaputtem Code richtig aus.
3. **Der Bestand vor und nach jedem Eingriff** — Zeilenzahl, id-Mengen,
   `sqlite_sequence`, Belegung in Bytes, `mode=ro`-Zeilenzahl. Wo sich nichts
   geändert hat, wird der Prüfstand laut (K5 „Das Aufräumen hat etwas
   verändert", K6 „Die Verdrängung hat den Bestand verändert").

---

## Eine Messung ist ein Zeitpunkt

Kein `--since`, kein Fenster gegen ein zweites. Die Fristen laufen über
**künstlich vorgerückte Zeitstempel**, nicht über Warten. Die
`mode=ro`-Zeilenzahl wird unmittelbar **vor** und unmittelbar **nach** genau
einer Meldung abgelesen — eine Klammer um einen Aufruf. Es gibt keinen `sleep`
außer der Warteschleife auf `recorder.sock` (Abbruch bei Erfolg, 20 s Deckel).

---

## Auf welcher Datenbank gearbeitet wurde

**Auf einer eigenen** — aber im **echten** Archivverzeichnis, und das mit
Absicht: `BindPaths=%h/.local/share/daimon` der Units zeigt genau dorthin, und
eine Kopie in `/tmp` beantwortete die Frage nach dem Schreibrecht nicht. Jeder
Lauf legt `~/.local/share/daimon/t71v-<pid>/` an und entfernt es im `finally`.
Die in-Prozess-Messungen (K4–K8) laufen unter einem eigenen `XDG_DATA_HOME` in
`/tmp/t71-*`.

Die echte `archiv.db` ist **weder gelesen noch geschrieben**:

```
$ stat -c '%n %a %x %y' ~/.local/share/daimon/archiv.db
/home/itiger013/.local/share/daimon/archiv.db 600 2026-08-13 23:05:40 2026-08-18 07:03:35
```

atime vom 13.08., mtime von 07:03 — beide **vor** dieser Sitzung (Beginn
08:53). Nach allen Läufen:

```
$ ls -a ~/.local/share/daimon
.  ..  archiv.db  models  voices
```

---

## Welche Zeile im Produktivcode müsste kaputt sein — und ausprobiert?

Die Auflage aus dem Auftrag, beantwortet, **mit Lauf**. Dreizehn Mutanten,
jeder eine gebrochene Stelle im Gut-Muster, jeder einzeln gemessen:

| Kriterium | gebrochene Zeile | Ergebnis |
|---|---|---|
| K1 | `daimon-eyes.service`: `ProtectHome=read-only` → `no` | `eyes-darf-schreiben`: **K1 2 rot**, sonst nichts — die Zusage, an der alles hängt |
| K2 | `daimon-recorder.service`: `ProtectHome=tmpfs` entfernt | `recorder-ohne-protecthome`: **K2 4 rot** — `$HOME` offen, Token-Verzeichnis schreibbar |
| K2 | `daimon-recorder.service`: `AF_UNIX` → `AF_UNIX AF_INET` | `recorder-mit-netz`: **K2 1 rot**, sonst nichts |
| K3 | `recorder/daemon.py` importiert `mind.router` (samt Persona-Text) | `recorder-laedt-modelltext`: **K3 1 rot**, sonst nichts — der Dienst läuft weiter, still |
| K4 | `store.py`: `MODUS = 0o600` → `0o644` | `datei-0644`: **K4 3 rot** (+ K9 1 — das `-shm` trägt dieselbe Konstante) |
| K4 | `store.py`: beide FTS-Trigger aus der Migration | `volltextindex-ohne-trigger`: **K4 1, K8 1, K9 1 rot** — der Index steht, füllt sich nie, meldet keinen Fehler |
| K5 | `store.py`: `ART_FRAME: 48 * 3600.0` → `30 * 24 * 3600.0` | `frist-einheitlich`: **K5 5 rot**, sonst nichts — Frames überleben mit dem Text |
| K5 | `store.py`: `VERBOTENE_ARTEN` leer, Rohaudio bekommt Fristen | `rohaudio-erlaubt`: **K5 5 rot**, sonst nichts |
| K6 | `store.py`: `if ueberschuss > 0:` → `if False:` | `grenze-verdraengt-nicht`: **K6 5 rot**, sonst nichts — kein Fehler, kein Verdrängen, volle Platte |
| K7 | `store.py`: `stufe: str = STUFE_REDACTED` → `STUFE_FULL` | `stufe-vorgabe-full`: **K7 1 rot**, sonst nichts |
| K7 | `store.py`: der `if stufe == STUFE_TRANSIENT: return 0`-Block weg | `transient-schreibt`: **K7 2 rot**, sonst nichts — Privatmodus und abgeschaltete Wahrnehmung schreiben ab da mit |
| K8 | `store.py`: `Mark.TAINTED` → `Mark.TRUSTED` in `_zeile` | `tainted-verloren`: **K8 6 rot**, sonst nichts — die Senkentabelle lässt ihn danach ins ungefragte Vorlesen |
| K9 | `store.py`: `_rechte_ziehen` setzt `-shm` auf `0o400` | `shm-nur-lesbar`: **K9 2 rot, K4 1 rot** — der Schreiber merkt nichts, jede lesende Verbindung scheitert |

**Jedes** der neun Kriterien wird in mindestens einem Lauf rot. Kein Kriterium
ist blind. Elf der dreizehn Mutanten fallen **ausschließlich** an ihrem eigenen
Kriterium; die beiden breiten (`datei-0644`, `volltextindex-ohne-trigger`)
reißen mit, was hinter ihnen liegt.

**Und der Befund selbst ist am Gut-Muster gegengeprüft:** dort ist die eine
Zeile in `daimon-fs.service` ergänzt, und K1 wird grün — an *derselben*
Messung, die gegen den Arbeitsbaum rot bleibt.

---

## Belege (Befehl + Ausgabe)

```
$ git rev-parse HEAD
3c1b843ead93cd638b0c3a61083f45d0441650fa

$ bash tests/verify/verify-frozen.sh
verify-frozen: 37 eingefrorene Dateien unveraendert; Abhaengigkeiten geschlossen.
EXIT 0

$ bash tests/verify/meta.sh T-7.1
meta[T-7.1]: Mutanten werden erzeugt (…/tests/mutants/T-7.1/erzeugen.sh) ...
T-7.1: 13 Mutanten erzeugt.
meta[T-7.1]: Gut-Muster ...
meta[T-7.1]: Mutante 'datei-0644' erkannt.
meta[T-7.1]: Mutante 'eyes-darf-schreiben' erkannt.
meta[T-7.1]: Mutante 'frist-einheitlich' erkannt.
meta[T-7.1]: Mutante 'grenze-verdraengt-nicht' erkannt.
meta[T-7.1]: Mutante 'recorder-laedt-modelltext' erkannt.
meta[T-7.1]: Mutante 'recorder-mit-netz' erkannt.
meta[T-7.1]: Mutante 'recorder-ohne-protecthome' erkannt.
meta[T-7.1]: Mutante 'rohaudio-erlaubt' erkannt.
meta[T-7.1]: Mutante 'shm-nur-lesbar' erkannt.
meta[T-7.1]: Mutante 'stufe-vorgabe-full' erkannt.
meta[T-7.1]: Mutante 'tainted-verloren' erkannt.
meta[T-7.1]: Mutante 'transient-schreibt' erkannt.
meta[T-7.1]: Mutante 'volltextindex-ohne-trigger' erkannt.
meta[T-7.1]: 13 Mutanten, alle erkannt.
META EXIT 0

$ bash tests/verify/T-7.1.sh                       # der echte Baum
Pruefling: /mnt/data/AI/repos/dAImon-t71
Archivdienst: t71v-rec-145162.service (PID 145168),
              Archiv /home/itiger013/.local/share/daimon/t71v-145162/archiv.db
FAIL K1: daimon-fs.service: Schreibrecht aufs Archivverzeichnis = SCHRIEB
         (erwartet: VERWEIGERT)
FAIL K1: Genau ein Dienst darf ins Archiv schreiben; gemessen:
         ['daimon-fs.service', 'daimon-recorder.service']

Bilanz T-7.1:
K1: 26 Pruefungen, 2 rot          K6: 11 Pruefungen, 0 rot
K2: 12 Pruefungen, 0 rot          K7:  8 Pruefungen, 0 rot
K3: 20 Pruefungen, 0 rot          K8: 10 Pruefungen, 0 rot
K4: 10 Pruefungen, 0 rot          K9: 10 Pruefungen, 0 rot
K5: 19 Pruefungen, 0 rot
EXIT 1

$ DAIMON_FIXTURE=tests/fixtures/known-good/T-7.1 bash tests/verify/T-7.1.sh
K1: 26 Pruefungen, 0 rot   … alle neun 0 rot
EXIT 0

$ for m in tests/mutants/T-7.1/*/; do … done      # jede Mutante einzeln
datei-0644                   | EXIT 1 | K4: 3 rot  K9: 1 rot
eyes-darf-schreiben          | EXIT 1 | K1: 2 rot
frist-einheitlich            | EXIT 1 | K5: 5 rot
grenze-verdraengt-nicht      | EXIT 1 | K6: 5 rot
recorder-laedt-modelltext    | EXIT 1 | K3: 1 rot
recorder-mit-netz            | EXIT 1 | K2: 1 rot
recorder-ohne-protecthome    | EXIT 1 | K2: 4 rot
rohaudio-erlaubt             | EXIT 1 | K5: 5 rot
shm-nur-lesbar               | EXIT 1 | K4: 1 rot  K9: 2 rot
stufe-vorgabe-full           | EXIT 1 | K7: 1 rot
tainted-verloren             | EXIT 1 | K8: 6 rot
transient-schreibt           | EXIT 1 | K7: 2 rot
volltextindex-ohne-trigger   | EXIT 1 | K4: 1 rot  K8: 1 rot  K9: 1 rot
```

**Die installierten Units sind bytegleich mit diesem Arbeitsbaum** — deshalb
gilt die Messung auch für den Betrieb und nicht nur für den Prüfling:

```
$ readlink -f ~/.config/systemd/user/daimon-recorder.service
/mnt/data/AI/repos/dAImon/config/systemd/daimon-recorder.service
$ sha256sum  (installiert | Arbeitsbaum)
recorder  807fc6ce7c913028 | 807fc6ce7c913028
eyes      ff77eee94414180d | ff77eee94414180d
fs        9e806b2b5efa9a9a | 9e806b2b5efa9a9a
```

**Zum Zustand der Dienste, ausdrücklich.** Der Auftrag sagt,
`daimon-recorder`, `daimon-eyes`, `daimon-ears` und `daimon-mind` liefen.
Gemessen ist etwas anderes, und das ist für „am **laufenden** Dienst"
wesentlich:

```
$ systemctl --user show daimon-recorder.service \
      -p UnitFileState -p ActiveState -p ActiveEnterTimestamp -p NRestarts
UnitFileState=linked
ActiveState=inactive
ActiveEnterTimestamp=            # leer: dieser Dienst ist auf dieser
NRestarts=0                      # Maschine NIE gelaufen

$ systemctl --user list-units 'daimon*' --all
daimon-auth / face / focus / hookbridge / hub          active running
daimon-ears / eyes / mind / fs / dbus / egress / …     inactive dead
daimon-egress.socket / stt.socket / tts.socket         active listening
daimon-audit-verify.timer / phase1.timer               active waiting

$ journalctl --user --since "2026-08-18 08:53" | grep -E '(Started|Stopped) dAImon'
Aug 18 08:57:47  Starting dAImon Phase-1-Alltagsmessung...      # der Timer,
Aug 18 09:02:48  Starting dAImon Phase-1-Alltagsmessung...      # nicht diese
Aug 18 09:07:58  Starting dAImon Phase-1-Alltagsmessung...      # Sitzung
Aug 18 09:12:58  Starting dAImon Phase-1-Alltagsmessung...

$ systemctl --user list-units 't71*' --all;  ls -d ~/.local/share/daimon/t71v-*
(keine Unit, kein Verzeichnis geblieben)
$ ls -d /run/user/1000/daimon-t71v-*
(kein Verzeichnis geblieben)
```

Diese Sitzung hat **keinen** `daimon-*`-Dienst gestartet und keinen gestoppt.

---

## Grenzen — was er NICHT misst

1. **Der laufende Dienst ist eine transiente Unit, nicht
   `daimon-recorder.service` selbst.** `daimon-recorder.service` ist auf
   dieser Maschine `linked`, aber **nie gelaufen** (`ActiveEnterTimestamp`
   leer, `NRestarts=0`) — gemessen, oben belegt. Ihn zu starten hätte
   bedeutet: `aufraeumen()` auf der **echten** `archiv.db` (ein `DELETE` nach
   Fristen, ungefragt) und ein `automatik()`-Durchgang, der über
   `pause.stoppe()` `daimon-eyes` **stoppen** kann. Der Auftrag verbietet
   genau das. Gemessen ist deshalb: derselbe Code unter denselben Direktiven,
   aus derselben Datei, deren sha256 mit der installierten übereinstimmt.
   **Nicht** gemessen ist der Start der echten Unit — insbesondere nicht ihr
   `ExecStartPre=+/usr/bin/install -d -m 0700 %h/.local/share/daimon` und
   nicht ihr `RuntimeDirectory=daimon`.
2. **Zwei Direktiven werden in den Proben ersetzt, und beide aus Sorge um den
   Betrieb.** `RuntimeDirectory=daimon` würde beim Beenden einer transienten
   Unit `/run/user/1000/daimon` **wegräumen** — samt aller Sockets von Hub,
   Auth, Face und Focus; ersetzt durch `daimon-t71v-<pid>`. `ReadWritePaths=`
   unter `%t/` hängt daran und fällt mit weg (Auslassung kann nur strenger
   machen, nie milder). Dazu `LoadCredential=` (ohne den echten Unit-Namen
   nicht auflösbar, 243/CREDENTIALS) und `BusName=` (verlangt `Type=dbus`).
   Die vollständige Liste steht in `NICHT_UEBERNOMMEN` im Prüfstand.
3. **„Kein Modelltext im Prozess" ist zur Hälfte offen.** Die *Importmenge*
   ist ganz gemessen. Die *Laufzeit* ist ein **Zeitpunkt**: Umgebung,
   Kommandozeile, offene Deskriptoren, abgebildete Dateien — einmal, kurz
   nachdem der Dienst bereit war. Dass zu keinem **anderen** Zeitpunkt
   Modelltext durch diesen Prozess geht, ist damit nicht belegt. Ein
   vollständiger Nachweis wäre eine Laufzeit-Dateiöffnungsspur über die ganze
   Lebensdauer, wie `PLAN.md` sie fürs Einfrieren vorsieht; sie ist hier nicht
   gebaut. Was daneben steht: der Prozess hat gar keinen Kanal dorthin —
   `AF_UNIX` allein, `~/.config/daimon` unerreichbar, kein `LoadCredential`.
4. **Die Schreibrecht-Messung ist eine Aussage über die Unit-Sandkästen, nicht
   über das Dateisystem.** Sie fragt: „darf ein Prozess unter diesen
   Direktiven?" Sie fragt **nicht** nach anderen Wegen an die Datei —
   `systemd-run` aus einem der Dienste heraus (`daimon-exec` startet
   Anwendungen ausdrücklich **außerhalb** seiner Sandbox, Design §7.5), ein
   Prozess des Nutzers außerhalb jeder Unit, oder ein Angreifer mit derselben
   uid. Design §1.3 schließt den letzten ohnehin aus dem Bedrohungsmodell aus;
   der mittlere ist gemessen (`ohne Haertung -> SCHRIEB`) und **gewollt**.
5. **Der Zulauf ist nicht gemessen.** Ob `daimon-eyes` und `daimon-focus` im
   Betrieb tatsächlich über `recorder.sock` melden und ob die Unit-Auflösung
   per `SO_PEERPIDFD` die richtige Unit findet, ist **T-7.1b** und **T-7.2**.
   `t71_dienst.py` **setzt** die Peer-Unit, statt sie aufzulösen (siehe dort);
   die Art-je-Unit-Tabelle läuft damit echt, die Auflösung nicht.
6. **Die Redaktion spielt hier nicht mit.** Der Kanarienvogel des laufenden
   Dienstes ist ein `transkript` — `urteil_ton()`, ohne Denylist, ohne
   Fensterklasse, ohne Lebenszeichen der Augen. Ob ein gelistetes Fenster gar
   nicht erst ins Archiv kommt, ist **T-7.2** und dort **rot**
   (`LEDGER-T-7.2.v.md`). Ein Verifizierer, der beides mischte, wüsste
   hinterher nicht, welche Sperre gegriffen hat.
7. **Der Aufräumer ist als Takt und als Wirkung gemessen, nicht als Uhr.**
   `intervall == 3600 s` und `aufraeumen_faellig()` in beide Richtungen, dazu
   ein echter Durchgang mit echter Verdrängung. **Nicht** gemessen ist ein
   Dienst, der eine Stunde läuft und dann von selbst aufräumt.
8. **Die GB-Grenze ist an 32 KB gemessen, nicht an 5 GB.** Der Mechanismus ist
   derselbe (laufende Summe über `bytes`, ältester zuerst); dass er bei fünf
   Gigabyte und Millionen Zeilen in vertretbarer Zeit läuft, ist eine
   Leistungsfrage und hier nicht gestellt. Dass er auf `bytes` und nicht auf
   `st_size` rechnet, ist im Modulkopf begründet und wird von dieser Messung
   nur mittelbar bestätigt.
9. **`tainted` ist als Rückgabetyp gemessen, nicht als Beweis, dass es keinen
   zweiten Leseweg gibt.** `lesen()` und `suchen()` verpacken; ein `SELECT`
   direkt auf der Datei umgeht das — und genau das tut K9 selbst. Die Zusage
   des Tasks ist der Typ an der Schnittstelle, und der hält. Wer sie im
   Prozess umgeht, hat die Datei ohnehin.
10. **`t71_dienst.py` ist eine dritte Fassung des Musters aus `t72_dienst.py`
    und `t59_hub.py`.** Absicht: `T-7.2.*` gehört einem anderen,
    abgeschlossenen Auftrag und wurde nicht angefasst; ein gemeinsamer Helfer
    hätte ihn geändert. Es sind Fassungen eines **Gerüsts**, nicht einer
    Regel — die Regel steht im Prüfling. Ein Zusammenführen braucht einen
    eigenen autorisierten Task.
11. **Nicht eingefroren.** `freeze.sh` bleibt ungerufen; die
    Freeze-Erweiterung ist laut `PLAN.md` ein eigener, einzeln autorisierter
    Task. Beim Einfrieren sind `t71_pruefstand.py` **und** `t71_dienst.py` als
    Helfer zu deklarieren.
12. **Der Lauf legt kurzzeitig ein Verzeichnis im echten Archivverzeichnis
    an** (`~/.local/share/daimon/t71v-<pid>/`) und entfernt es im `finally`.
    Ein `SIGKILL` mitten im Lauf ließe es stehen. Es enthält nur eigene
    Kanarienvögel; die echte `archiv.db` wird nicht angefasst.

---

## Was der Builder als Nächstes tun kann

* **Eine Zeile in `config/systemd/daimon-fs.service`.** Der Verifizierer wird
  damit grün — das ist am Gut-Muster gegengeprüft, nicht behauptet. Welche
  Zeile, entscheidet der Builder: `InaccessiblePaths=-%h/.local/share/daimon`
  ist die kleinste, `ProtectHome=tmpfs` plus vollständige `BindPaths=` die
  gründlichere. Wer die Gelegenheit nutzt, sieht sich den Kommentar in Zeile
  43 an: „Diese Liste ist die eigentliche Schranke des FS-Brokers." Das stimmt
  **nur** zusammen mit einem `ProtectHome=`, und der Kommentar sagt es nicht —
  wer ihn liest, hält die Unit für enger, als sie ist.
* **Zwei Nachbarn lohnen denselben Blick**, ohne dass dieser Verifizierer sie
  prüft: `daimon-exec.service` startet Anwendungen ausdrücklich außerhalb
  seiner Sandbox (Design §7.5) — was diese Kinder dürfen, misst hier niemand.
  Und `daimon-fs` ist nicht der einzige Dienst, dessen `ProtectHome` am venv
  in `$HOME` hängt; `daimon-recorder` löst dasselbe Problem mit
  `/usr/bin/python3` und der Standardbibliothek.
* **Nichts an den übrigen acht Kriterien.** Sie sind gemessen erfüllt, und
  dass die Messung etwas sieht, belegen dreizehn Mutanten.
* **Grenze 9 aus T-7.5.v ist erledigt** und braucht nicht weitergereicht zu
  werden. Der Nachfolger für T-7.4.v: der Kanarienvogel dieses Laufs ist ein
  `transkript`, das über den echten Socket in die echte Datenbank ging — die
  Naht „Ohren → Recorder → Archiv" ist damit **nicht** gemessen, nur ihr
  letztes Stück.
