# Sicherheitsreview der Phase 4 (T-4.18)

**Ziel:** ein zweites Paar Augen auf der Vertrauensgrenze.
**Ausgang: `gruen`** — 95 Prüflistenpunkte abgearbeitet, 41 Befunde,
davon **19 offen** und **kein `high` oder `critical` mehr darunter**.

Der erste Lauf am 21.08. war `produktdefekt-rot`: 21 offene Befunde, zwei
davon `high` (F-01, F-02). Der Builder hat beide noch am selben Tag
behoben; sie stehen seither als `closed` im Register, nachgemessen und
nicht geglaubt — siehe [Der Nachlauf des Builders](#der-nachlauf-des-builders--f-01-und-f-02).

Gemessen gegen den laufenden Hauptbaum `/mnt/data/AI/repos/dAImon`,
Commit `a47b1c8` für das Review, Commit `243d9ac` für den Nachlauf,
am 21.08.2026. Nicht gegen den Plantext, nicht gegen
die Commit-Meldungen und nicht gegen die Ledger der Einzeltasks — deren
Aussagen sind hier **Ausgangspunkt**, nicht Ergebnis: jeder übernommene
Befund ist am heutigen Baum nachgemessen, und fünf von ihnen sind
seitdem eingelöst worden.

---

## Was dieser Task ist, und was er ausdrücklich nicht ist

Der Implementierungsplan sagt es selbst, unter der Akzeptanzliste:

> T-4.18 ist ein Architekturreview mit Prüfliste und Belegpflicht. Seine
> maschinelle Abnahme liegt in den eingefrorenen Verifizierern der
> Einzeltasks, nicht in diesem Task — Review und ausführbare Verifikation
> sind getrennte Artefakte mit getrennter Zuständigkeit.

`tests/verify/T-4.18.sh` misst deshalb **nicht das Produkt**, sondern
**dieses Review**: ob die Prüfliste vollständig abgearbeitet ist, ob jeder
Punkt und jeder Befund einen auflösbaren Beleg trägt, und ob sich jede
Feststellung heute reproduzieren lässt — die geschlossenen wie die offenen.

Die Reproduktion läuft bewusst **in beide Richtungen**:

| Status | Die Probe muss messen | Warum |
|---|---|---|
| `closed` | Defekt **weg** | Sonst ist die Entwarnung unbelegt. |
| `open` | Defekt **da** | Positivkontrolle: sonst ist unbelegt, dass die Probe einen Defekt überhaupt sehen kann. |
| — | nicht messbar | Immer ein Verstoß. „Nicht gemessen" ist nicht grün. |

Das ist Punkt 3 aus `CLAUDE.md` in Bauform gegossen. Ein Prüfstand, der
nur `closed` bestätigen könnte, meldete auch dann `ok`, wenn er gar nichts
misst — und genau daran sind an einem Tag im August vier Falschbefunde
entstanden. Die 19 offenen Befunde sind die Gegenprobe: sie belegen, dass
diese 41 Proben scharf sind.

---

## Die vier Akzeptanzpunkte, jeder einzeln

### (a) Jeder Pfad, auf dem Modellausgabe zu einer Systemaktion wird

Seit T-4.16 K1 ist das ein **echter** Pfad, kein hypothetischer. Er ist in
voller Länge abgegangen:

```
daimon/mind/router.py:525          router.frage erkennt eine Aktionsäußerung
  → daimon/mind/daemon.py:243      Mind.frage_werkzeug -- 20 Werkzeuge aus dem
                                   echten Katalog an die API
  → daimon/mind/daemon.py:297      hub_anfrage(aktion.sock, art="ausfuehren")
  → daimon/hub/daemon.py:988       Hub.aktion_anfrage
  → daimon/hub/coordinator.py:122  Koordinator.ausfuehren
  → Policy → Vorschau → Rückfrage → Undo → Ticket → Broker → Audit
```

Daneben, an **derselben** Naht, die zweite Vertrauensgrenze:

```
daimon/ears/daemon.py:337          Ohren-Dienst fragt den Hub-Parser
  → daimon/hub/daemon.py:1499      Hub.parser_anfrage (quelle="parser")
  → daimon/hub/coordinator.py:122  derselbe Koordinator
```

**Was hält, und woran es gemessen ist:**

* **Die `quelle` gehört dem Hub.** `aktion.sock` setzt `quelle="modell"`
  als Konstante (`daimon/hub/daemon.py:1087`). Wer das Feld mitschickt,
  ändert nichts. Der Socketweg *kann* strukturell nie der Parser sein —
  ein deterministischer Parser läuft im Hub und ruft den Koordinator
  direkt. Das ist die strukturelle Antwort statt einer Prüfung, und sie
  ist am Syntaxbaum nachgemessen, nicht am Kommentar (F-18).
* **Modellausgabe hebt `allow` auf `ask`, an zwei Stellen.** **Zwölf**
  Katalogeinträge tragen `foreground: allow`. Mit einer echten
  `Policy.laden()` gegen den Hauptbaum-Katalog nachgerechnet: über den
  Modellpfad wird aus **allen zwölf** ein `ask`.

  ```
  media.playpause            ask   modellausgabe_immer_vorschau
  media.next                 ask   modellausgabe_immer_vorschau
  …
  audio.volume.set           ask   missing_argument
  desktop.previous           ask   modellausgabe_immer_vorschau
  ```

  Der Grund kommt aus der **Regelebene** (`config/policy.yaml`,
  `when: {quelle: modell}`); darunter greift zusätzlich der Hub selbst
  (`policy.py:280`: Direktbefehl-Ausnahme nur bei `direct: true` **und**
  `quelle == "parser"`, sonst `vorschau_pflicht`). Zwei Vorrichtungen für
  dieselbe Zusage sind hier **nicht** der Fehler aus `CLAUDE.md` Punkt 4:
  die Regeldatei benennt die Doppelung im Kommentar und nennt den Grund
  („eine Zusage, die nur im Code steht, ist im Audit nicht ablesbar"),
  und beide Fassungen ziehen in dieselbe Richtung — die schärfere gewinnt
  ohnehin. Gemessen ist, dass **keine** der zwölf ohne Rückfrage
  durchgeht, nicht welche der beiden zuerst greift.
* **Der Direktpfad fragt nach der Taste.** `parser_anfrage` weist jede
  Äußerung ohne `marke == "user_ptt"` ab (`daemon.py:1524`, Commit
  `87c2281`). Ohne diese Schranke hätte jede *mitgehörte* Äußerung im
  offenen 120-s-Fenster gewirkt — auf dem mächtigeren der beiden Wege,
  denn der Direktpfad spart die Vorschau (F-19).
* **Der Parser-Socket hat eine Unit-Allowlist** (`PARSER_UNITS =
  ("daimon-ears.service",)`, `daemon.py:81`) — Wegweiser im Sinne von
  §1.3, keine Authentifizierung, und als solcher benannt.

**Was nicht hält — und der schwerste Befund dieses Reviews liegt hier:**

> **F-01 (`high`): Der Bestätigungsdialog nennt drei der zwanzig Aktionen
> nicht, die er bestätigen lässt.**
>
> **Status: `closed`** — behoben in `243d9ac`,
> `daimon/auth/preview.py:161-163`, nachgemessen. Der Befundtext unten
> steht so, wie er gemessen wurde; der Wächter fehlt weiter.

`daimon/auth/preview.py:128` führt `AKTIONS_BESCHRIFTUNGEN`. Am 20.08. hat
T-4.12 K6 dort die Katalogaktionen nachgetragen, damit
`Hub._vorschau_bauen` nicht mehr auf einen selbstgebauten Text mit rohem
Aktionsschlüssel fällt. Am **21.08.** hat T-4.16 K2 drei neue Einträge in
`config/actions/core.yaml` aufgenommen — `fs.file.read`,
`exec.launch.kcalc`, `input.type.kcalc` — und die Tabelle blieb, wo sie
war. Gemessen:

```
$ .venv/bin/python -c '…vorschau(aktion=…, ziel="x", umkehr="unkritisch")'
input.type.kcalc  -> VorschauFehler: unbekannter Aktionsschluessel 'input.type.kcalc'
fs.file.read      -> VorschauFehler: unbekannter Aktionsschluessel 'fs.file.read'
exec.launch.kcalc -> VorschauFehler: unbekannter Aktionsschluessel 'exec.launch.kcalc'
media.next        -> 'Ember will ausführen:\n  Aktion:  Zum naechsten Titel springe…'
```

`daemon.py:936` fängt den `VorschauFehler` und liefert:

> „Ember will eine unbekannte Aktion ausführen. Bitte ablehnen."

Das ist als Fehlerrichtung richtig gewählt und trotzdem der Befund. Die
drei betroffenen Aktionen sind **Dateilesen, Anwendungsstart und
Tastensynthese** — die drei mächtigsten im Katalog, und die einzigen
drei, die nicht über den argumentvalidierenden DBus-Broker gehen. Der
Mensch bekommt für sie einen Dialog, der weder die Aktion noch das Ziel
noch die Umkehrbarkeit nennt. Und derselbe Satz wird als `prompt_shown`
in die Auditkette geschrieben: die Spur weiß hinterher genauso wenig wie
der Mensch vorher.

Zwei kleinere Befunde derselben Stelle:

* **F-04 (`medium`):** `_vorschau_bauen` nimmt
  `next(iter(params.values()))` — **einen** Parameterwert, und die
  Reihenfolge kommt aus dem `tool_use`-Block der Modellausgabe. Bei einer
  Aktion mit mehreren Parametern entscheidet also das Modell, welchen
  davon der Mensch sieht.
* **F-03 (`medium`):** Unbekannte Schlüssel in `params` werden nicht
  verworfen. Design §1.3 verlangt das für den Hub ausdrücklich
  („Unbekannte Felder werden verworfen, nicht durchgereicht") — es gilt
  für die Nachricht, nicht für die Schlüssel *innerhalb* von `params`.
  Gemessen an der echten Policy:

  ```
  input.type.kcalc: `voellig_unbekannt` passiert die Policy
  (verdikt=ask, grund=katalog:foreground) und steht im params_hash
  ```

  Das Modell kann `input.type.kcalc` eine `folge` anhängen, die im
  Katalogschema nicht vorkommt. Zusammengenommen mit F-01 und F-04 hieß
  das: der Dialog zeigt nichts, das gezeigte Feld muss nicht das
  wirksame sein, und das wirksame Feld prüft niemand vor dem Broker.
  Seit F-01 behoben ist, benennt der Dialog die Aktion wieder — die
  hintere Hälfte des Satzes gilt unverändert.

Ein vierter Befund an der Naht, der eine Entscheidung ist und kein
Versehen:

* **F-06 (`medium`):** Die Rundenmarke wird **nie eingelöst.** Design
  §6.1 zeichnet „Rundenmarke einlösen" als ersten Schritt des Hubs; der
  Code schlägt sie nur nach, mit begründetem Vorsatz („sonst wäre eine
  Runde mit zwei Aktionen nach der ersten tot"). Folge: ein Tastendruck
  autorisiert 120 Sekunden lang beliebig viele Modellaktionen als
  `foreground`. Am Syntaxbaum gemessen: weder `aktion_anfrage` noch
  `parser_anfrage` ruft `marken.einloesen`. Das ist eine vertretbare
  Produktentscheidung — sie steht nur im Code und nicht im Maßstab, und
  damit sind es zwei Fassungen einer Regel.

### (b) Proxy-Filterliste gegen den Katalog

> **F-02 (`high`): Der Filter gibt `invokeShortcut` für **alle**
> kglobalaccel-Komponenten frei. Der Katalog braucht vier.**
>
> **Status: `closed`** — behoben in `243d9ac`,
> `config/dbus-filter.conf:32-35`, vier einzelne Regeln, Zuschnitt gegen
> den Katalog nachgemessen. Der Befundtext unten steht so, wie er
> gemessen wurde.

`config/dbus-filter.conf:24`:

```
--call=org.kde.kglobalaccel=org.kde.kglobalaccel.Component.invokeShortcut@/component/*
```

Der Katalog nennt vier Komponenten: `mediacontrol`, `kmix`, `kwin`,
`org.kde.spectacle.desktop`. `allMainComponents` liefert auf dieser
Maschine **26**. Freigegeben sind damit unter anderem:

| Komponente | Kurzbefehle darin (Auszug) |
|---|---|
| `ksmserver` | `Halt Without Confirmation`, `Reboot Without Confirmation`, `Log Out Without Confirmation`, `Lock Session` |
| `org_kde_powerdevil` | `PowerOff`, `Hibernate`, `Bildschirm ausschalten` |
| `daimon-ears` | `ohren_aus` — der Mikrofon-Kill-Switch |
| `daimon-auth` | `ptt` — der Push-to-Talk-Auslöser |

Das ist genau die Gruppe, die `config/actions/core.yaml` im Kopf
ausdrücklich zurückhält:

> `status: approved` steht nur an Einträgen mit `rationale`. Alles andere
> — org_kde_powerdevil, kwin.script.load, Aktivitäten, Sitzungsverwaltung
> und jede nicht eingeordnete Aktion — bleibt `candidate` und steht hier
> gar nicht erst drin.

Und `daimon-auth`/`ptt` ist der Sonderfall, der wehtut: Design §1.3 nennt
`kglobalaccel.invokeShortcut` beim Namen als den Weg, auf dem ein
Angreifer „das Push-to-Talk-Ereignis erzeugen" könnte — als etwas, das
nur der ausdrücklich ausgeschlossene same-uid-Angreifer kann. Die zweite
Schicht lässt es für den DBus-Broker offen, also für die Komponente, die
es zu begrenzen gäbe.

**Live gemessen, mit zwei Positivkontrollen**, damit „kam durch" von
„habe nicht gemessen" unterscheidbar ist:

```
$ P=$XDG_RUNTIME_DIR/daimon/dbus-proxy.sock

# 1. Der Proxy filtert DIENSTE -- Gegenprobe:
$ DBUS_SESSION_BUS_ADDRESS=unix:path=$P gdbus call --session \
    --dest org.kde.KWin --object-path /KWin \
    --method org.freedesktop.DBus.Peer.Ping
Fehler: GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown          rc=1

# 1a. POSITIVKONTROLLE: derselbe Dienst am ECHTEN Bus
$ gdbus call --session --dest org.kde.KWin --object-path /KWin \
    --method org.freedesktop.DBus.Peer.Ping
()                                                                     rc=0

# 2. Der Proxy filtert METHODEN -- Gegenprobe:
$ DBUS_SESSION_BUS_ADDRESS=unix:path=$P gdbus call --session \
    --dest org.kde.kglobalaccel --object-path /component/ksmserver \
    --method org.kde.kglobalaccel.Component.shortcutNames
Fehler: GDBus.Error:org.freedesktop.DBus.Error.AccessDenied            rc=1

# 3. Er filtert KOMPONENTEN NICHT -- der Befund:
$ DBUS_SESSION_BUS_ADDRESS=unix:path=$P gdbus call --session \
    --dest org.kde.kglobalaccel --object-path /component/ksmserver \
    --method org.kde.kglobalaccel.Component.invokeShortcut \
    "T-4.18-existiert-nicht"
()                                                                     rc=0

# 3a. POSITIVKONTROLLE: dieselbe Form auf einer FREIGEGEBENEN Komponente
$ … --object-path /component/mediacontrol … "T-4.18-existiert-nicht"
()                                                                     rc=0
```

Der Kurzbefehlsname ist bewusst einer, den es nicht gibt: die Messung
beantwortet „passiert der Aufruf den Filter", ohne etwas auszulösen.

**Was das nicht heißt.** Die erste Schicht hält. Der DBus-Broker baut je
`approved`-Aktion eine feste Operation aus Katalogwerten
(`brokers/dbus/broker.py:60`), kennt nur `org.kde.kglobalaccel` als Dienst
und hat für `ksmserver` keine Operation. Ein nicht genehmigter Kurzbefehl
derselben Komponente wird abgewiesen — das ist von T-4.7.v mit
Positiv-Kanarienvogel gemessen. Design §6.4 nennt den Proxy selbst
„Vorfilter, Defense in Depth" und nicht die Grenze. Der Befund ist
trotzdem einer: **die zweite Schicht deckt an der einzigen Achse, die sie
decken könnte, nichts ab.** Sie unterscheidet Dienste und Methoden —
beides gemessen — und ausgerechnet die Komponente nicht, obwohl
`allMainComponents` sie aufzählbar macht und der Katalog vier davon
braucht.

**Was der Filter richtig macht:** `org.kde.KWin` und
`org.kde.kwin.Scripting` stehen nicht darin, `loadScript` ist damit an
beiden Schichten ausgeschlossen (§6.4, gemessen als ServiceUnknown).
`--log` ist an (§7.6, dritter Strom). Und der Proxy hat seit T-4.7 K3
überhaupt einen Zulauf: `daimon-dbus-proxy.service` startet ihn, und
`daimon-dbus.service` zeigt mit `Environment=DBUS_SESSION_BUS_ADDRESS=`
auf seinen Socket und hängt per `Requires=` an ihm (F-23, geschlossen).

**Nichts Überflüssiges auf der anderen Achse.** Die beiden lesenden
Aufrufe im Filter — `allMainComponents` und `allActionsForComponent` —
braucht der Broker beim Start, um seine Operationstabelle gegen die
Wirklichkeit zu halten. Kein Dienst außer `org.kde.kglobalaccel` steht
darin. Der Überschuss ist ausschließlich der Komponenten-Platzhalter.

### (c) Unit-Härtungen gegen Design §7.5

**22 Units** liegen unter `config/systemd/` (der Auftrag nannte 21;
`daimon-gpu@.service` als Vorlage ist die zweiundzwanzigste). Jede ist
gegen die Basisliste und, wo vorhanden, gegen ihre Zeile in der
Abweichungstabelle geprüft.

**Die Basis hält bei 22 von 22.** `NoNewPrivileges=yes`,
`CapabilityBoundingSet=`, `ProtectSystem=strict`,
`SystemCallFilter=@system-service`, `RestrictAddressFamilies=AF_UNIX`,
`InaccessiblePaths=` auf `.ssh`/`.gnupg`/`keyrings`/`.pki` — je Unit
einzeln nachgelesen, mit den dokumentierten Ausnahmen und keiner
undokumentierten.

**T-4.14 hat die Tabelle am 20.08. korrigiert. Ich habe nachgeprüft, ob
sie jetzt zum Baum passt — nicht dem Commit-Text geglaubt.** Ergebnis:

* **`ProtectProc=`/`ProcSubset=` sind wirklich weg.** Keine der 22 Units
  setzt eine der beiden Zeilen. Die Berichtigung in §7.5 ist damit keine
  Behauptung mehr (P26).
* **Die fünf `@resources`-Ausnahmen stimmen jetzt in beide Richtungen.**
  Der Prüfstand geht den Weg vom Baum zum Maßstab: für jede Unit ohne
  `@resources` in der Sperrliste wird die zugehörige Zeile in §7.5
  gesucht. Fünf Units (`cli-broker`, `gpu@`, `stt`, `tts`, `recorder`),
  fünf Zeilen, kein Rest. **Die `recorder`-Zeile, an der T-4.14 K1 rot
  war, steht jetzt im Maßstab** (F-32, geschlossen — nachgemessen, nicht
  übernommen).
* **T-4.14 K6 ist eingelöst.** Alle 22 installierten Units unter
  `~/.config/systemd/user/` sind Symlinks ins Repo; keine Kopie mehr
  (F-33). Die vier Sockets und Timer, die als reguläre Datei dort liegen,
  sind mit der Repo-Fassung byteweise identisch — geprüft, weil „Kopie"
  und „veraltete Kopie" zweierlei sind.
* **T-4.9 K3 ist eingelöst.** `daimon-fs.service` trägt jetzt
  `ProtectHome=tmpfs`; `$HOME` ist im Dienst nicht mehr vollständig
  sichtbar (F-30).
* **T-7.1 K1 ist eingelöst.** Keine Unit außer `daimon-recorder.service`
  erreicht `~/.local/share/daimon` über `ReadWritePaths=` oder
  `BindPaths=` (F-37, an allen 22 Units geprüft).

**Was nicht stimmt (F-16, `low`):** Drei Zeilen der Tabelle beschreiben
den Baum nicht zeichengleich. In allen drei Fällen ist die **Wirkung**
richtig und die **Beschreibung** falsch:

| Tabelle §7.5 | Baum | Wirkung |
|---|---|---|
| `gpu@`: `PrivateDevices=no` | die Unit hat die Zeile nicht | gleich (Vorgabe ist `no`) |
| `fs`: „enge `ReadWritePaths=` für Arbeits-, Trash- und Undo-Pfade" | `ProtectHome=tmpfs` + vier `BindPaths=` | gleich, anderer Mechanismus |
| `recorder`: „`ReadWritePaths=` nur fürs Archivverzeichnis" | `ProtectHome=tmpfs` + `BindPaths=%h/.local/share/daimon` | gleich, anderer Mechanismus |

Das ist kein Sandkastenfehler und wird auch nicht als einer gebucht. Es
steht im Register, weil eine Tabelle, die den Baum um drei Zeilen
danebenbeschreibt, beim nächsten Abgleich drei Gelegenheiten für einen
Falschbefund bietet — und weil genau diese Tabelle vor vier Wochen
`ProtectProc=` als wirksam führte.

**F-17 (`low`), unverändert offen seit T-4.14 K4:** 18 von 22 Units haben
keine dokumentierte `systemd-analyze security`-Note.
`docs/broker-sandboxes.md` führt vier. Dokulücke, kein Code-Fix — steht
hier, weil `bekannt-offen` sonst mit der Zeit zu `erledigt` verrutscht.

### (d) Audit-Redaktion gegen die Verbotsliste

Design §7.6, Verbotsliste:

> **Nie im Klartext:** Clipboard, synthetisierte Tastenanschläge,
> Dateiinhalte, Tokens, sowie **jedes `tainted`-Material** — nur Hash und
> Länge.

**Die Verbotsliste hält, jeder Punkt einzeln:**

* **Tastenanschläge.** Der Input-Broker protokolliert `laenge` und
  `klasse` — `klassenlabel()` gibt nur die *Arten* zurück, sortiert und
  ohne Wiederholung (`brokers/input/broker.py:140`). Kein Zeichen.
* **Dateiinhalte.** Der FS-Broker antwortet `{"ok": true, "bytes": N}`.
  Der Koordinator schreibt Inhalte nicht — er kennt sie nicht.
* **Tokens.** `mind` und alle Nicht-Egress-Units haben
  `InaccessiblePaths=` auf die Tokendatei; `egress` liest ausschließlich
  aus `$CREDENTIALS_DIRECTORY` und protokolliert `{ticket, bytes, status,
  dauer_ms}`.
* **`tainted`-Material.** `Audit.schreiben()` redigiert alles, was der
  Aufrufer in `tainted` nennt — **und `prompt_shown` bedingungslos**
  (`audit.py:171`), weil der Vorschautext Parameterwerte trägt. Die
  Redaktion liegt im Audit und nicht beim Aufrufer: „ein Aufrufer, der
  sie vergisst, soll keinen Klartext hinterlassen können." Das ist die
  Zusage an der Stelle, die sie einhält (Design §1.3, `CLAUDE.md` Punkt 5).
* **Parameterwerte** erreichen die Kette überhaupt nicht: der Koordinator
  schreibt `params_hash`, nie `params`.

Die Gegenrichtung von §7.6 — **„Immer: Ablehnungen, Cancels, Timeouts,
Policy-Änderungen, Markenausgaben und -einlösungen"** — hält nicht
vollständig:

* **Ablehnungen, Cancels, Timeouts: ja.** Der Koordinator schreibt in
  *jedem* Ausgang, auch bei `deny` und `cancel`; `AUSGAENGE` trennt
  `denied` (Policy) von `declined` (Mensch) ausdrücklich.
* **F-11 (`medium`), unverändert seit T-5.9.v K4:** Der **Umfang** einer
  Deklassifizierung steht ausschließlich in `prompt_shown` — dem einen
  Feld, das das Audit immer redigiert (`declassify.py:186`). Was
  freigegeben wurde, ist hinterher nicht rekonstruierbar. Das ist die
  eine Frage, für die man dieses Audit aufschlägt.
* **F-40 (`low`):** Markenausgaben und -einlösungen gehen über den
  injizierten Logger ins **Journal**, nicht in die verkettete JSONL-Spur.
  §7.6 zählt sie in dem Absatz auf, der die Kette beschreibt. Sie sind
  damit Teil des Stroms, *gegen den* verankert wird, statt Teil des
  Stroms, *der* verankert wird.
* **F-38 (`low`):** Die 90-Tage-Frist aus §7.1 steht weder in
  `daimon/hub/audit.py` noch in der `audit-verify`-Unit. Es gibt
  Rotation, aber keinen Ort, an dem eine Frist steht. Heute konservativ
  verletzt (es wird nichts gelöscht) — die Zusage gilt trotzdem in keine
  Richtung.

**Die Kette selbst:** `seq` + `prev_hash` unter einem Schloss, das im
Audit liegt und nicht beim Aufrufer; 0700/0600; Rotation übernimmt den
Kopf. **T-4.6 K5 ist eingelöst** (F-22): `audit_verankern` ruft bei
gerissener Kette `state.warnblase()`, und die setzt `urgent: True` fest —
am Syntaxbaum gemessen und bis in den Setter verfolgt, weil ein Aufruf
ohne Dringlichkeit derselbe Befund von der anderen Seite wäre. Die
Erstlauf-Unterscheidung über `anker_gefunden == 0` ist da, damit die
Blase nicht zum Wegklick-Reflex wird.

**F-05 (`medium`) am zweiten Strom:** `_ins_journal` schreibt den Anker
über `systemd-cat` (`audit.py:236`). Design §7.6 sagt zu genau diesem
Weg:

> **Journal** als Zweitschrift über `sd_journal_send()` mit
> `DAIMON_*`-Feldern; die `_`-präfigierten Felder (`_PID`, `_UID`,
> `_EXE`, `_BOOT_ID`) setzt das Journal selbst. `systemd-cat` reicht
> nicht **[V]**.

Der Anker trägt damit keine strukturierten Felder und keine vom Journal
gesetzte Herkunft. Der Kommentar im Code begründet die Wahl mit „ohne
zusätzliche Abhängigkeit" — und daneben liegt seit T-0.6
`daimon/common/logging.py`, das das native Journal-Protokoll ohne jede
Abhängigkeit spricht und Feldnamen prüft. Der zweite Strom ist die
einzige Vorrichtung, die die Kette gegen eine komplett neu gerechnete
Datei trägt; er sollte über den Weg gehen, den das Design gemessen hat.

---

## Das Register in Zahlen

| | `high` | `medium` | `low` | Summe |
|---|---|---|---|---|
| **offen** | **0** | 11 | 8 | **19** |
| geschlossen | 7 | 15 | — | 22 |
| Summe | 7 | 26 | 8 | **41** |

Prüfliste: **95 Punkte** — 13 aus §1.3, 36 aus §6, 46 aus §7. Davon
**80 erfüllt**, **15 mit Abweichung**: B10, B11, A1, A5, A16, A23, A30,
A31, A35, P3, P13, P23, P28, P33, P38.

### Die offenen Befunde, nach Schwere

Die zwei `high`-Befunde F-01 und F-02 standen hier bis zum Nachlauf des
Builders; sie sind heute `closed` und stehen unten mit ihrem Fix-Beleg.

| ID | Schwere | Kurz |
|---|---|---|
| F-03 | medium | Unbekannte `params`-Schlüssel werden nicht verworfen |
| F-04 | medium | Vorschau zeigt einen Parameterwert; die Reihenfolge kommt aus der Modellausgabe |
| F-05 | medium | Auditanker über `systemd-cat` statt `sd_journal_send()` |
| F-06 | medium | Rundenmarke wird auf dem Aktionspfad nie eingelöst |
| F-07 | medium | Zustimmungs-Cache hat keinen Schreiber |
| F-08 | medium | `never_cacheable` hat keinen Leser |
| F-09 | medium | Kein Vorlauf mit gehaltenem Deskriptor, keine Bindung an die Consent-Nonce |
| F-10 | medium | `braucht_modal()` hat keinen Aufrufer |
| F-11 | medium | Umfang der Deklassifizierung nur in einem immer redigierten Feld |
| F-12 | medium | STT-Arbeitsprozess endet bei Stille nicht |
| F-13 | medium | Systemweiter `ydotoold` neben der Input-Unit (Umgebung) |
| F-14 | low | Zwei `approved`-Aktionen ohne Operation im Broker |
| F-15 | low | Der `declined`-Knopf heißt „Abbrechen" |
| F-16 | low | Drei Zeilen der §7.5-Tabelle beschreiben den Baum nicht zeichengleich |
| F-17 | low | 18 von 22 Units ohne dokumentierte Sandbox-Note |
| F-38 | low | 90-Tage-Frist des Audits steht nirgends im Code |
| F-39 | low | Zwei Sockets im Laufzeitverzeichnis sind 0700 |
| F-40 | low | Markenausgaben stehen im Journal, nicht in der Kette |
| F-41 | low | Keine Deckelung der Verschachtelungstiefe (§1.3 nennt sie) |

### Vier Zusagen ohne Zulauf — dieselbe Bauform, viermal

F-07, F-08, F-09 und F-10 sind ein Muster, nicht vier Einzelfälle. Alle
vier sind **gebaut, geprüft und im Betrieb unerreichbar**:

| Zusage | Gebaut in | Aufrufer im Betrieb |
|---|---|---|
| Zustimmungs-Cache, vier Gültigkeiten (§6.5) | `policy.py:211` | **0** |
| `never_cacheable` (§6.7 „nie gecacht") | `core.yaml`, 20 von 20 Einträgen | **0 Leser** |
| Modalpflicht bei `destructive` ohne Undo (§6.5) | `modal.py:65` | **0** |
| Vorlauf-FD an der Consent-Nonce (§6.9) | — | — |

Die Fehlerrichtung ist bei allen vieren konservativ: jedes `ask` fragt
wirklich, jede Aktion ist heute nicht-destruktiv, und der FS-Broker löst
wenigstens vor der Ticket-Einlösung auf. Genau das macht sie gefährlich.
Es ist die Tabelle aus `CLAUDE.md`, Zeile für Zeile — sechs Fälle standen
schon darin, hier sind vier weitere. Und F-08 ist der besonders teure
Fall: sobald jemand F-07 verdrahtet, gilt „Immer `ask`, nie gecacht" für
die Tastensynthese still nicht mehr, weil das Katalogfeld, das es
verhindern soll, niemanden hat, der es liest. **Was hier fehlt, ist kein
Code, sondern ein Wächter** — die Bauform aus
`tests/test_gate_zulauf.py`.

### Was seit den Ledgern eingelöst wurde

Fünf `high`-Befunde und fünfzehn `medium`-Befunde der Runden vom 18. bis
21.08. sind heute weg. Jeder ist nachgemessen, keiner übernommen:

| Befund | Ledger | Heute |
|---|---|---|
| `quelle` aus der Nachricht → `allow` ohne Vorschau | T-4.4.v K8 | Konstante im Hub (F-18) |
| Direktpfad ohne PTT-Prüfung | T-4.16.v K3 R2 | `marke == "user_ptt"` (F-19) |
| Auflösung zwischen Genehmigung und Mutation | T-4.9.v K2 | vor der Ticket-Einlösung (F-20) |
| Kein Weg für ein NEIN | T-4.11.v K5/K9 | Agent meldet `antwort` (F-21) |
| Gerissene Kette nur im Journal | T-4.6.v K5 | `state.warnblase`, `urgent: True` (F-22) |
| `dbus-filter.conf` ohne Leser | T-4.7.v K3/K4 | eigene Proxy-Unit (F-23) |
| Feste Zwischendatei zerstört Rückfragen | T-4.11.v K8 | PID + Thread-ID (F-24) |
| Acht Koordinatoren bei acht Anfragen | T-4.11.v K8 | `_aktion_lock` (F-25) |
| Selbstgebauter Vorschautext | T-4.12.v K6 | `preview.vorschau()` (F-26) |
| Rückfrageplatz kommt nicht zurück | T-4.15.v K4 | `finally` (F-27) |
| `outcome=unknown` unerreichbar | T-4.15.v K2 | bedingte Bestätigung (F-28) |
| Undo-Hop ohne Zulauf | T-4.8.v | `undo=self._undo_vorbereiten` (F-29) |
| `ProtectHome` auskommentiert | T-4.9.v K3 | `tmpfs` (F-30) |
| Nur die erste `--wurzel` zählt | T-4.9.v K6 | Schleife (F-31) |
| `recorder`-Ausnahme nicht im Maßstab | T-4.14.v K1 | in §7.5 (F-32) |
| 13 Units als Kopie installiert | T-4.14.v K6 | 22 Symlinks (F-33) |
| Input-Broker ohne Portal/Allowlist/Breaker/Log | T-4.13.v K4/K7/K8/K10 | alle vier da (F-34) |
| `gio launch` — die App lief nie | T-4.10.v K4 | `KillMode=none` (F-35) |
| fs/exec/input ohne Katalogeintrag | T-4.9/4.10/4.13 K6/K11 | alle vier `audience` (F-36) |
| `daimon-fs` schreibt ins Archiv | T-7.1.v K1 | nur der Recorder (F-37) |

### Die zwei Punkte, nach denen ausdrücklich gefragt war

**T-4.13 K1/K4/K6/K11 („zurückgestellt, Produktentscheidung nötig"):
erledigt, nicht mehr offen.** Die Rückstellung galt der Frage, ob der
Katalog Input-Aktionen über das Minimalset hinaus tragen soll. T-4.16 K2
hat sie beantwortet: `core.yaml` führt seit `a8e63e9`
`input.type.kcalc` mit `audience: input` und `apps: ["org.kde.kcalc"]`,
und `allowlist_aus_katalog()` (`brokers/input/daemon.py:41`) zieht die
Allowlist daraus. Damit sind K1 (One-shot), K4 (Portal-Regelweg), K6
(Naht durch den echten Koordinator) und K11 (Zulauf) **gegenständlich**
geworden — sie waren nicht falsch, sie hatten keinen Messgegenstand. Alle
vier sind heute gemessen (F-34, F-36). Was aus dieser Freigabe *neu*
entstanden war, war F-01: der neue Katalogeintrag hatte keine
Vorschau-Beschriftung bekommen. Inzwischen behoben, siehe unten.

**Die Dokulücke „Notenspalte": noch offen.** Sie gehört zu **T-4.14.v
K4**, nicht zu T-4.16.v. Nachgezählt am heutigen Baum: 18 von 22 Units
ohne Note (T-4.14 zählte 14 von 22 gegen eine andere Quelle). Als F-17
mit `low` im Register.

### Der Nachlauf des Builders — F-01 und F-02

Der Builder hat beide `high`-Befunde noch am 21.08. im Hauptbaum
behoben. Nachgemessen wurde mit denselben zwei Proben, die sie gefunden
haben — nicht mit neuen, und nicht mit dem Wort des Builders:

| Befund | Fix | Beleg im Hauptbaum | Probe misst heute |
|---|---|---|---|
| F-01 | `AKTIONS_BESCHRIFTUNGEN` trägt `fs.file.read`, `exec.launch.kcalc`, `input.type.kcalc` | `243d9ac`, `daimon/auth/preview.py:161-163` | „alle 20 approved-Aktionen haben eine Beschriftung" |
| F-02 | vier einzelne `--call=…invokeShortcut@/component/<name>`-Regeln statt `/component/*` | `243d9ac`, `config/dbus-filter.conf:32-35` | „die invokeShortcut-Regeln nennen Komponenten einzeln" |

Beide liegen in **einem** Commit, `243d9ac`, und er berührt genau diese
zwei Dateien — sonst nichts. Eines gehört trotzdem dazugesagt, weil das
Register sonst zu grün liest:

* **Der empfohlene Wächter fehlt weiter.** Empfehlung 1 nannte zwei
  Teile: die drei Beschriftungen *und* eine Prüfung, dass jede
  `approved`-Aktion eine hat. Gebaut ist der erste Teil. Im Hauptbaum
  liest niemand `AKTIONS_BESCHRIFTUNGEN` gegen den Katalog gegen —
  `grep` findet außerhalb von `preview.py` nur `tests/verify/T-1.7.sh`.
  Beim nächsten Katalogeintrag ist es damit wieder so weit, wie es
  zwischen dem 20. und dem 21.08. schon einmal war. Das ist keine neue
  Beanstandung an F-01, sondern der offene Rest der Empfehlung.

Gegengeprüft wurde auch der Zuschnitt des F-02-Fixes, nicht nur sein
Vorhandensein: die vier Regeln decken genau die vier
`kglobalaccel`-Komponenten, die `core.yaml` in seinen 20
`approved`-Einträgen nennt — `mediacontrol`, `kmix`, `kwin`,
`org.kde.spectacle.desktop`, letztere als `org_kde_spectacle_desktop`,
weil `_kglobalaccel_operation` den Objektpfad so bildet. Keine Regel zu
viel, keine zu wenig. Der Platzhalter `/component/*` steht nur noch im
erklärenden Kommentar (Z. 20) — die Probe liest deshalb ausschließlich
Zeilen, die mit `--call=` beginnen.

---

## Ledger

### Provenienz

| | |
|---|---|
| Rolle | reviewer (`DAIMON_ROLE=reviewer`), **kein Produktivcode geschrieben** |
| Worktree | `/mnt/data/AI/repos/dAImon-t418`, Branch `reviewer/p4-T-4.18` |
| Prüfling (nur lesend, über `DAIMON_FIXTURE`) | `/mnt/data/AI/repos/dAImon`, Commit `a47b1c8` (Review), `243d9ac` (Nachlauf F-01/F-02) |
| Maschine | cachyos, Linux 7.2.0-1-cachyos-bore, KDE/KWin 6, Wayland |
| Neue Artefakte | `tests/evidence/phase4-findings.json`, `docs/phase4-security-review.md`, `tests/verify/T-4.18.sh`, `tests/verify/t418_reproduktion.py` |
| sha256 (Kurzform) | `T-4.18.sh` `300a175dae929d36…` · `t418_reproduktion.py` `4344098490390618…` · `phase4-findings.json` `3f3daf5f0fc32149…` (nach dem Nachziehen von F-01/F-02; davor `2dbab65ad7aa6c12…`) |
| `freeze.sh` | **nicht aufgerufen** — das entscheidet Matthias |
| Fremde Tasks | unberührt; der Hauptbaum ist ausschließlich gelesen worden |
| Laufzeit | 2,2 s je Lauf (41 Proben, davon eine mit DBus-Aufruf und eine mit `pgrep`; kein `sleep`, kein Warten) |

**Gelesen:** `docs/IMPLEMENTATION-PLAN.md` T-4.18 (Z. 1411–1424) ·
`docs/DESIGN.md` §1.3, §6, §7 vollständig · `config/actions/core.yaml`,
`config/dbus-filter.conf`, alle 22 `config/systemd/daimon-*.service` ·
`daimon/hub/{daemon,coordinator,policy,consent,audit,marks,state,
declassify,action_queue}.py` · `daimon/mind/daemon.py` ·
`daimon/auth/{agent,modal,preview}.py` ·
`daimon/brokers/{dbus,fs,exec,input,egress}/` · `daimon/common/{ipc,
logging,taint,order,protocol}.py` · die vierzehn Ledger
`LEDGER-T-4.4.v` bis `LEDGER-T-4.16.v`, `LEDGER-T-5.9.v`,
`LEDGER-T-7.1.v` bis `LEDGER-T-7.5.v` (aus ihren Reviewer-Zweigen
gelesen, nicht aus dem Hauptbaum — dort liegen nur elf davon).

### Der Lauf gegen den Hauptbaum

**Lauf 1 (21.08., vor dem Nachlauf des Builders) — rot an K5:**

```
$ DAIMON_FIXTURE=/mnt/data/AI/repos/dAImon tests/verify/T-4.18.sh
T-4.18: Review-Abnahme gegen /mnt/data/AI/repos/dAImon (a47b1c8)

ok   [K1] alle 95 Prueflistenpunkte aus Design 1.3/6/7 sind beantwortet
ok   [K1] kein Punkt im Register, den die feste Liste nicht kennt
ok   [K2] 136 Beleg-Verweise, alle im Pruefling aufgeloest
ok   [K3] 41 Befunde, jeder mit {id, severity, status} und einer Reproduktion
ok   [K4] 41 Befunde nachgemessen; davon 21 offen (die Positivkontrolle: die
     Proben sehen einen Defekt)
FAIL [K5] offen und schwer: F-01(high) F-02(high)
     F-01(high) Drei Katalogaktionen haben keine Vorschau-Beschriftung; der
     Dialog nennt die Aktion nicht, die er bestaetigen laesst
     F-02(high) xdg-dbus-proxy gibt invokeShortcut fuer ALLE
     kglobalaccel-Komponenten frei, der Katalog braucht vier

T-4.18: ROT -- 1 Beanstandung(en).
$ echo $?
1
```

**Rot war hier das erwartete Ergebnis, nicht der Fehlschlag.** Die
Vollständigkeit hielt, die Belegpflicht hielt, jeder der 41 Befunde
reproduzierte sich in der Richtung, in der er gemeldet war. Rot war der
Prüfstand allein an K5 — weil zwei `high`-Befunde offen waren.

**Lauf 2 (21.08., nach den Builder-Fixes, Register noch unverändert) —
der Prüfstand meldet von sich aus, dass er veraltet ist:**

```
FAIL [K4] F-01 steht als offen, ist aber weg: alle 20 approved-Aktionen haben
          eine Beschriftung -- das Register ist veraltet
FAIL [K4] F-02 steht als offen, ist aber weg: die invokeShortcut-Regeln nennen
          Komponenten einzeln -- das Register ist veraltet
FAIL [K5] offen und schwer: F-01(high) F-02(high)

T-4.18: ROT -- 3 Beanstandung(en).
```

Das ist die Gegenrichtung der Reproduktion bei der Arbeit: nicht der
Builder meldet „behoben", der Prüfstand widerspricht dem Register. Erst
danach sind F-01 und F-02 auf `closed` gesetzt worden, jeweils mit einem
`fix`-Feld, das die Fundstelle im Hauptbaum nennt.

**Lauf 3 (21.08., Register nachgezogen, Fixes als `243d9ac` kommittiert)
— GRÜN:**

```
$ DAIMON_FIXTURE=/mnt/data/AI/repos/dAImon tests/verify/T-4.18.sh
T-4.18: Review-Abnahme gegen /mnt/data/AI/repos/dAImon (243d9ac)

ok   [K1] alle 95 Prueflistenpunkte aus Design 1.3/6/7 sind beantwortet
ok   [K1] kein Punkt im Register, den die feste Liste nicht kennt
ok   [K2] 136 Beleg-Verweise, alle im Pruefling aufgeloest
ok   [K3] 41 Befunde, jeder mit {id, severity, status} und einer Reproduktion
ok   [K4] 41 Befunde nachgemessen; davon 19 offen (die Positivkontrolle: die
     Proben sehen einen Defekt)
ok   [K5] kein high/critical-Befund offen

T-4.18: GRUEN -- Pruefliste vollstaendig, Belege aufgeloest, jeder Befund
reproduziert, nichts Schweres offen.
$ echo $?
0
```

Die 19 verbliebenen offenen Befunde sind dabei kein Schönheitsfehler,
sondern die Bedingung, unter der dieses Grün überhaupt etwas heißt: K4
fällt ausdrücklich rot aus, wenn sich **kein einziger** Befund mehr als
vorhanden reproduziert — dann wäre unbelegt, dass diese Proben einen
Defekt sehen können.

### Kann er den Fehler sehen? — sieben Mutanten am Register

Ein Prüfstand, der nur ein einziges Register kennt, belegt nicht, dass er
etwas misst. Sieben Mutanten, jeder mit genau einer Verfälschung, keiner
am Produkt — jeder erkannt, jeder an seinem zugedachten Kriterium:

```
punkt-fehlt            FAIL [K1] unbeantwortet: A35
fremder-punkt          FAIL [K1] nicht in der festen Pruefliste: X9
beleg-tot              FAIL [K2] B1: …/nirgendwo.py gibt es im Pruefling nicht
register-leer          FAIL [K3] das Befundregister ist leer -- ein Review ohne
                                 Befund hat nicht gemessen
entwarnung-unbelegt    FAIL [K4] F-06 ist als behoben gemeldet, der Defekt ist
                                 aber da: weder `aktion_anfrage` noch
                                 `parser_anfrage` ruft `marken.einloesen`
befund-veraltet        FAIL [K4] F-20 steht als offen, ist aber weg -- das
                                 Register ist veraltet
probe-erfunden         FAIL [K4] F-18: keine Probe namens 'gibt_es_nicht'
```

Die zwei interessanten sind `entwarnung-unbelegt` und `befund-veraltet`:
sie belegen, dass die Abrechnung **in beide Richtungen** trägt. Ein
Register, das einen Defekt zu früh für behoben erklärt, fällt genauso auf
wie eines, das einen behobenen Befund mitschleppt.

### Drei Befunde über den Prüfstand selbst — alle behoben, alle notiert

Beim ersten vollständigen Lauf meldeten vier Proben das Falsche. Alle
vier waren Werkzeugfehler, keiner ein Produktfehler — und alle vier sind
Wiederholungen von Fehlerformen, die in diesem Repo schon Geld gekostet
haben:

1. **`vorschau_nutzt_vorlage` maß den Kommentar.** Der Docstring von
   `_vorschau_bauen` **zitiert** den alten, selbstgebauten Text als
   behobenen Befund. Ein `grep` über den Quelltext fand ihn und meldete
   F-26 als offen. Dieselbe Form wie in `LEDGER-T-4.5.v.md` K1, wo der
   Absatz über den gestrichenen HMAC nicht als HMAC gelten darf. Jetzt
   liest die Probe den Rumpf **ohne Docstring** (`ohne_docstring()`).
2. **`kette_meldet_blase` suchte am falschen Namen.** Sie suchte nach
   `bubble`/`urgent` in `audit_verankern` — der Hub ruft aber
   `state.warnblase()`, und `urgent: True` steht im Setter. „Nichts
   gefunden" war „an der falschen Stelle gemessen". Jetzt folgt die Probe
   dem Aufruf bis in `state.py` und verlangt beides.
3. **`audit_frist` fand eine Frist, die es gibt — für etwas anderes.**
   Die Suche `\b90\b.*Tag` über ganz `daimon/` traf die Ringgröße in
   `daimon/eyes/context.py`. Jetzt sucht sie in `audit.py` und der
   `audit-verify`-Unit, also dort, wo die Frist stehen müsste.
4. **`proxy_komponenten` schnitt die Messung ab.** Die Hilfsfunktion
   `gdbus()` kürzt Antworten auf 200 Zeichen — gedacht für
   Fehlermeldungen. `allMainComponents` wurde damit nach vier Einträgen
   abgeschnitten, und aus 26 Komponenten wurden „4". Der Befund wäre
   dadurch **kleiner** erschienen, als er ist. Jetzt gibt es `voll=True`,
   und die Zahl steht oben.

Es sind vier statt drei; die Überschrift bleibt trotzdem hier stehen,
weil der vierte erst beim Nachrechnen der Zahl auffiel und genau das der
Punkt ist.

### Was dieses Review nicht geleistet hat

* **Keine Laufzeitmessung des vollständigen Aktionspfads.** Der
  Ende-zu-Ende-Weg mit echtem Hub, echten Brokern und echten Zielobjekten
  ist von `T-4.16.sh` gemessen (142 Prüfungen, 23 Mutanten); hier ist er
  gelesen und an seinen Verzweigungen geprüft, nicht gefahren. Wer ihn
  gefahren sehen will, ruft den dortigen Verifizierer.
* **Keine Prüfung der Phasen 1–3 und 5–8.** Die Prüfliste deckt Design
  §1.3, §6 und §7. Wahrnehmung (§4) und Kognition (§5) sind nur dort
  berührt, wo sie an der Vertrauensgrenze liegen.
* **F-13 und F-39 hängen an dieser Maschine.** Läuft der `ydotoold` nicht
  mehr oder ist der Hub gestoppt, melden ihre Proben „Befund weg" bzw.
  „nicht messbar", und `T-4.18.sh` wird an K4 rot. Das ist gewollt: ein
  Umgebungsbefund, der sich nicht mehr reproduzieren lässt, gehört aus
  dem Register — nicht stumm hineingeglaubt.
* **Kein `freeze.sh`.** Das entscheidet Matthias.

### Empfohlene Reihenfolge

1. ~~**F-01** — die drei fehlenden Beschriftungen in
   `AKTIONS_BESCHRIFTUNGEN`~~ **erledigt** (`preview.py:161-163`). Der
   zweite Teil der Empfehlung steht noch: ein **Wächter**, der beim Laden
   des Katalogs prüft, dass jede `approved`-Aktion eine Beschriftung hat.
   Ohne ihn ist es beim nächsten Katalogeintrag wieder so weit; genau das
   ist hier zwischen dem 20. und dem 21.08. passiert.
2. ~~**F-02** — die Komponenten einzeln in `config/dbus-filter.conf`
   aufzählen statt `/component/*`~~ **erledigt**
   (`dbus-filter.conf:32-35`, vier Zeilen, Zuschnitt gegen den Katalog
   nachgemessen). Auch hier bleibt der Wächter offen: wächst der Katalog
   um eine Komponente, muss der Filter mitwachsen — heute merkt das
   niemand.
3. **F-03 + F-04** — unbekannte `params`-Schlüssel im Hub verwerfen, und
   die Vorschau alle Parameter zeigen lassen statt des ersten.
4. **F-05** — `_ins_journal` auf `daimon/common/logging.py` umstellen.
   Der Apparat liegt schon da.
5. **F-07/F-08/F-10** — je einen Wächter nach dem Muster von
   `tests/test_gate_zulauf.py`, damit die drei Zusagen auffallen, sobald
   ihr Zulauf entsteht. **Kein Vorratscode.**
6. **F-06, F-09, F-12** — Produktentscheidungen, keine Patches: entweder
   der Code folgt dem Design, oder das Design wird berichtigt. Zwei
   Fassungen einer Regel ist die eine Antwort, die nicht in Frage kommt.
