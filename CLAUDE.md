# CLAUDE.md — Arbeitsregeln für dieses Repo

Ergänzt die globalen Regeln in `~/.claude/CLAUDE.md`. Hier steht nur, was
**dieses** Projekt teuer gelernt hat.

Sprache: Deutsch, in Code-Kommentaren, Commits und Dokumenten. Ohne Umlaute
im Quelltext (bestehende Konvention), im Fließtext mit.

---

## Prüfe den Zulauf, nicht nur das Stück

**Der teuerste wiederkehrende Fehler dieses Repos, sechsmal in Folge:** ein
Stück ist gebaut, dokumentiert und mit grünen Tests belegt — und im Betrieb
ruft es niemand auf. Die Zusage steht im Code, die Prüfung ist grün, und
trotzdem gilt sie nicht.

| Was gebaut war | Was fehlte | Commit |
|---|---|---|
| Ticketbuch, 12 Tests | kein Prozess instanziierte es | T-3.11 |
| Deklassifizierungs-Gate | kein Aufrufer | T-5.9b |
| Kontextspeicher im Hub | nie `laden()` — gab immer Leeres frei | `2788cfc` |
| DRM-Sperre in Kette und Redaktion | kein Produzent setzte je `drm=True` | `ab922d7` |
| `daimon-mind.service` | kein `[Install]`, keine `.socket` — startete nie | `d80e8e9` |
| `declassify.referenzen()`, 3 Tests | kein Aufrufer, Router baute selbst | `0b77245` |

Ein Test je Stück findet das **nie**. Er ruft das Stück ja selbst auf.

### Was daraus folgt

1. **Wer eine Zusage baut, prüft ihre NAHT** — von der Quelle bis zur
   Wirkung, in der Reihenfolge des Betriebs. `tests/test_drm_kette.py` ist
   das Muster: Watcher-Nutzlast → Fokusdienst → Augen → Redaktion → Archiv.
2. **Wer einen Prüfstand schreibt, fragt: kann er das je sehen?** Ersetzt er
   genau die Methode, in der der Fehler säße? (`test_hub_kontext.py` tat das
   und konnte den Befund darum nicht finden.)
3. **Jede Prüfung braucht eine Positivkontrolle.** „Nichts gefunden" muss
   von „nicht gemessen" unterscheidbar sein. Vier Falschbefunde an einem Tag
   kamen genau daher — alle vier meldeten Ruhe, wo die Vorrichtung kaputt war.
4. **Zwei Fassungen einer Regel sind eine Regel und eine Attrappe.** Welche
   von beiden gilt, entscheidet dann der Zufall des Aufrufs — und geprüft ist
   erfahrungsgemäß die andere.
5. **Eine Zusage gehört an die Stelle, die sie einhält**, nicht dorthin, wo
   sie sich gut liest. Nicht in die Quelle der Daten, sondern in den Schritt,
   der die Zusage macht.
6. **Kein Vorratscode für einen Zulauf, den es nicht gibt.** Das wäre
   derselbe Fehler von der anderen Seite. Stattdessen ein Wächter, der
   auffällt, sobald der Zulauf entsteht — `tests/test_gate_zulauf.py` und
   `tests/test_units_werden_gezogen.py` sind die zwei Bauformen.

### Die Frage vor jedem „fertig"

> Wer ruft das auf, im Betrieb, heute — und woran sehe ich das, ohne den
> Aufruf selbst zu schreiben?

Fällt die Antwort auf „der Prüfstand", ist das Stück nicht fertig.

---

## Ein eingefrorener Prüfstand ist eine Zusage MIT DATUM

Die Kehrseite der Regel oben, und am 31.08. dreimal an einem Tag: der Code
war in Ordnung, der Prüfstand hielt eine Zusage fest, die jemand **bewusst**
geändert hatte. Jedes Mal sah es zuerst wie ein Fehler am Prüfling aus.

| Der Prüfstand erwartete | Geändert am | Wirkung |
|---|---|---|
| `%t` im `ExecStart` bleibt wörtlich | 26.08., Modellweg-Weiche wurde Argument | Mind bekam einen Pfad, den es nicht gibt → 16 rote Kriterien |
| Aktionswunsch wird `abgelehnt` | T-4.16, werkzeugfähiger Weg gebaut | 7 rote Kriterien, dazu „kostet kein Kontingent" |
| `ProtectProc=invisible` steht in der Unit | `7b7deb9`, als wirkungslos entfernt | rot in vier eingebetteten Läufen |
| Bridge erreicht den Hub aus jeder Scope | `bd0bb8e`, Peer-Prüfung je Produzent | 3 rote Kriterien in `T-0.11`, ein halbes Jahr unbemerkt |

Der vierte Fall ist der lehrreichste, weil er zeigt, wie lange so etwas
liegen bleibt. `bd0bb8e` gab dem Hub die Peer-Prüfung; `T-0.9.sh` wurde
nachgezogen und hängt seinen Sender seither selbst in `daimon-verify.scope`,
`T-0.11.sh` blieb stehen. Der Hub wies dessen Bridge ab — aber `an_hub` feuert
und vergisst, also antwortete der Hook weiter mit **HTTP 200**. Der Prüfstand
maß genau das und war zufrieden. Ein Kriterium, das den Zustand danach liest,
gab es nicht.

### Was daraus folgt

1. **Wer eine Zusage ändert, sucht die Prüfstände, die die alte Fassung
   festhalten.** `grep` über `tests/verify/` nach dem alten Wortlaut, bevor
   der Commit steht. Billiger als drei Suchläufe an drei Tagen.
2. **Ein rotes Kriterium ist erst ein Befund, wenn gemessen ist, an welcher
   Seite es liegt.** Beide Fassungen gegeneinander fahren — hier war es je
   ein Handlauf gegen den echten Dienst, der zeigte: der Prüfling tut das
   Richtige.
3. **Ein angepasstes Kriterium wird strenger, nicht schwächer.** Aus „wird
   abgelehnt" wurden vier Prüfungen (ohne Marke abgelehnt, ohne Ziel
   Rückfrage, beides kostenfrei, mit beidem genau ein Ticket) plus eine
   fünfte, die es vorher nicht gab: `aktion.sock` bekommt keinen Aufruf,
   solange kein Werkzeug gerufen wird. Wer nur die Erwartung umschreibt,
   damit es grün wird, hat eine Attrappe mit neuem Hash.
4. **Das Gut-Muster altert mit.** Es ist eine Kopie des Quellbaums; ändert
   sich die Regel, wird es rot — und zwar zu Recht. Es gehört in denselben
   Zug wie das Kriterium.
5. **Ein Prüfstand, der abstürzt, meldet nichts.** In der Bilanz des
   umgebenden Laufs erscheint er als ein roter Punkt wie jeder andere. Ob
   dahinter ein Befund oder ein Absturz steckt, sieht nur, wer ihn einzeln
   fährt.
6. **Eine angenommene Anfrage ist kein angekommener Auftrag.** `HTTP 200`,
   ein geschriebener Socket, ein Exit 0 — alle drei sagen nur, dass der
   Absender fertig ist. Wer eine Wirkung zusagt, prüft die Wirkung: den
   Zustand danach, nicht die Quittung davor. `T-0.11` hat ein halbes Jahr
   die Quittung geprüft.
