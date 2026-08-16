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
