# Übergabe — Stand 2026-07-27, Abend

Für die nächste Sitzung. Diese Datei enthält alles, was nicht aus dem Repo hervorgeht.

---

## Wo wir stehen

Planung ist durch, **Phase −1 (Machbarkeits-Spikes) läuft**. Kein Produktivcode geschrieben.

Repo: `https://github.com/AstroGolem224/dAImon` — alles gepusht.

| Dokument | Inhalt |
|---|---|
| `docs/DESIGN.md` v5.3 | Architektur, **§1.3 Bedrohungsmodell zuerst lesen** |
| `docs/IMPLEMENTATION-PLAN.md` v5.3 | 161 Tasks, 10 Phasen, Anhang D = 46 Verifizierer |
| `docs/PRIOR-ART.md` | was übernommen / gelesen / gemieden wird |
| `PLAN-REVIEW-LOG.md` | 5 Runden adversarialer Review gegen Codex |
| `README.md` | Einstieg |

Die Planungsdokumente liegen zusätzlich unter `/home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/` — **das ist die Quelle**, `docs/` ist eine Kopie. Bei Änderungen beide pflegen:

```bash
cp /home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Design.md docs/DESIGN.md
cp /home/itiger013/Dokumente/UMBRA-Notes/DDs/dAImon/dAImon-Implementierungsplan.md docs/IMPLEMENTATION-PLAN.md
```

---

## Phase −1: Spike-Stand

| Spike | Status | Ergebnis |
|---|---|---|
| **T−1.2** ONNX sm_120 | ✅ **bestanden** | pip-Wheel `onnxruntime-gpu==1.27.0` hat native `sm_120a`-Cubins. Arch-Paket ist *schlechter* (PTX auf `compute_121`, lädt auf sm_120 nicht). C-API-Worker hart blockiert — ORT-Kern ist statisch ins pybind-Modul gelinkt |
| **T−1.3** layer-shell | ✅ **bestanden** | Overlay über Vollbild sichtbar, Idle-CPU **0,17 %**, **kein GPU-Kontext** (null DRI-FDs), Click-Through funktioniert. KDE-Bug 503121 reproduziert (0/20), Umgehung „Properties neu setzen" 20/20 |
| **T−1.9** KWin-Fokus | ✅ **bestanden** | 50/50, keine Auslassung, p95 **0,9 ms**. Aber: `captionChanged` feuert nur bei Titeländerung — der Abtast-Timer trägt den Großteil |
| **T−1.10** OCR-Kosten | ✅ **bestanden** | tesseract bleibt, als Arbeitsprozess mit `tessdata_fast`, ein Thread. Vollbild 3,3 s, Ausschnitt 0,35 s. **Korrektur:** Regionen-Zuschnitt bringt nichts (deckt 97–99 %), der Gewinn liegt im Fensterzuschnitt. VLM kann es nicht ersetzen — liefert auf Vollbildern nichts und halluziniert |
| **T−1.1** Wake-Word | ⏸ **wartet auf Matthias** | Werkzeug fertig, Aufnahmen fehlen |
| T−1.4 Portal-Persistenz | offen | ich hatte es noch nicht angefangen |
| T−1.11 AT-SPI2 | offen | dito |
| T−1.5 Mood-Mapping | teilweise | Fokus-Probe läuft mit und sammelt |
| `kwin --replace` | offen | **nicht getestet**, weil FreeCAD ungespeicherte Arbeit offen hatte |

**Alle drei harten Weichen sind durch.** T−1.1 ist die letzte blockierende.

---

## Was Matthias tun muss (T−1.1)

```bash
cd /home/itiger013/Dokumente/Github/dAImon/spikes/wakeword
python3 record.py positive              # 50 Proben, geführt durch 8 Bedingungen
python3 record.py background --min 60   # mehrfach, bis 3 h zusammen sind
python3 record.py status
venv/bin/python evaluate.py             # rechnet FRR und FAR
```

Wake-Word ist **„Embershard"**. Wichtiger Befund vor der ersten Aufnahme:

> `EMBERSHARD` als **ein Wort** schlägt **nie** an — bei keiner Schwelle.
> `EMBER SHARD` als **zwei Wörter** feuert zuverlässig.
> Unterschied liegt allein in der Tokenisierung: `▁E M BER SH ARD` gegen `▁E M BER ▁SHA R D`.
> `keywords.txt` nutzt deshalb die Zweiwortform. Gesprochen bleibt es dasselbe Wort.

Zielwerte: FRR < 10 %, FAR < 1/h. Wird beides nicht gleichzeitig erreicht → Plan B `livekit-wakeword` (trainiert deutsch), Plan C nur Push-to-Talk.

`samples/` ist ignoriert — Stimmaufnahmen bleiben lokal.

---

## Laufende Prozesse

**Fokus-Probe** sammelt weiter für T−1.5:
```bash
pgrep -af spikes/focus/probe.py
# Beenden:
pkill -f spikes/focus/probe.py
kwriteconfig6 --file kwinrc --group Plugins --key daimon-focusprobeEnabled false
qdbus6 org.kde.KWin /KWin org.kde.KWin.reconfigure
```

**OCR-Benchmark ist fertig.** `spikes/ocr/results.json` und `NOTES.md` liegen vor.

---

## Fallen, die uns schon erwischt haben

**Ein Overlay ohne Input-Region blockiert die Maus.** Eine Wayland-Surface **ohne** gesetzte `input_region` nimmt Eingaben auf ihrer **ganzen** Fläche an. Bildschirmfüllend heißt: der Rechner ist mit der Maus nicht mehr bedienbar. Im Journal steht **nichts**, weil aus Compositor-Sicht alles korrekt ist. Ist real passiert.
→ Behoben: leere Region ist Vorgabe, plus Watchdog im Prozess (`SPIKE_MAX_SECS`, Vorgabe 90 s). **Beide Fixes nicht rückgängig machen.**
→ Beim Beauftragen von Overlay-Arbeit: immer `SPIKE_MAX_SECS=15` mitgeben.

**`ydotool mousemove -a` positioniert auf dieser Maschine nicht.** Jedes absolute Ziel landet bei `(0,0)`, Exit-Code 0, keine Fehlermeldung. Nur relative Bewegung geht, und die unterliegt der Zeigerbeschleunigung (30er-Schritt kommt als 53 an). Hat einen ganzen Spike-Durchlauf verfälscht.
→ Für Phase 4: **libei ist die einzige Option für alles mit Positionierung.**

**Latenzverhältnisse taugen nicht als JIT-Nachweis.** Kalt/warm liegt auch mit gesperrtem Cache bei 770–900× (cuBLAS-Init). Nur `cuobjdump` beantwortet die Frage.

**`kwin --replace` nicht laufen lassen**, ohne vorher zu fragen — Matthias hat oft ungespeicherte Arbeit offen.

---

## Wie hier gearbeitet wird

**Rollen sind maschinell durchgesetzt.** `.claude/hooks/role_guard.py` als `PreToolUse`-Hook, `.githooks/pre-commit`, `tests/verify/verify-frozen.sh`.

```bash
export DAIMON_ROLE=investigator   # spikes/, docs/, tests/evidence/
export DAIMON_ROLE=builder        # Produktivcode, NICHT tests/verify/
export DAIMON_ROLE=reviewer       # Verifizierer, NICHT daimon/ face/
```

**Fehlt die Variable, ist Schreiben überall verboten** — fail closed. `tests/verify/T-0.0.sh` prüft das mit 19 Assertions.

**Verifizierer-Regime:** `T-x.y.v` (reviewer) kommt **vor** `T-x.y` (builder). Mutanten unter `tests/mutants/`, Hash eingefroren in `tests/verify/FROZEN`. Jedes Phasen-Gate beginnt mit `verify-frozen.sh`.

**Commits:** deutsch, ausführlich, erklären *warum*. Kein `git add -A` ohne vorher `git status` zu prüfen — hat schon versehentlich Spike-Artefakte eingesammelt.

**PMTool:** Projekt `dAImon`, ID `d9e36f7c-8e0f-480f-890d-7c52258ed12c`. Spalten: backlog `709e3cb5…`, in_progress `11dfbfdc…`, review `fe52c15a…`, done `922f0db3…`. Endpunkt für Tasks: `GET /api/projects/<id>/tasks`, `PUT /api/tasks/<id>`.

---

## Wichtige Designentscheidungen, die nicht offensichtlich sind

1. **§1.3 Bedrohungsmodell:** Ein Angreifer mit Codeausführung unter derselben uid wird **nicht** abgewehrt. Deshalb gibt es keine Signaturen zwischen eigenen Prozessen — der Schlüssel wäre ohnehin per `ptrace` lesbar.
2. **Sprache autorisiert nicht.** Audio ist nicht authentifizierbar. Aktionen brauchen Push-to-Talk plus Bestätigung der kanonisierten Aktion.
3. **Injektionsabwehr durch Fähigkeitsentzug**, nicht durch Prompt-Delimiter: Der Durchgang, der Bildschirmtext liest, hat kein Werkzeugschema.
4. **Vorgabe der Herkunftsmarkierung ist `tainted`**, `trusted` muss behauptet werden.
5. **Dauermitschnitt ist gewollt** (Bildschirm + Ton, 30 Tage Text, 48 h Bilder). Aber: nur auf Nachfrage durchsuchbar, proaktives Verhalten sieht die Historie nicht. **Der Pausenschalter beim Ton ist nicht optional** (§201 StGB, Dritte im Raum) — Abbruchkriterium von Phase 7.
6. **Gatterkette:** kein gekacheltes dHash (screenpipe hat das zweimal verworfen) — **und auch kein Zuschnitt auf die Regionen-Vereinigung**, die deckt 97–99 % des Vollbilds ab. Der Gewinn liegt im **Zuschnitt aufs fokussierte Fenster** plus quantisierter Signatur. Gemessen in T−1.10.
7. **OCR als dauerhafter Arbeitsprozess**, nicht wegen Geschwindigkeit, sondern Isolation: tesseracts OpenMP kostet ~800 ms extra, wenn numpy im selben Prozess liegt.

---

## Nächste sinnvolle Schritte

1. **T−1.4** (Portal-`restore_token` über Neustart) und **T−1.11** (AT-SPI2) — beide allein machbar
3. **T−1.1** sobald Matthias aufgenommen hat
4. Dann **T−1.7 Entscheidungsprotokoll**, danach Gate P−1
5. Erst danach P0.0 → P0

**Nicht** mit P0 anfangen, bevor T−1.1 entschieden ist — davon hängt ab, ob Phase 3 ein Wake-Word bekommt oder nur Push-to-Talk.

---

## Offene Ehrlichkeit

Der Codex-Review endete nach 5 Runden **ohne `APPROVED`**. Schlussurteil: Design „broadly sound", Plan „still not safe to start". Die zwei dort benannten Punkte sind inzwischen geschlossen (Anhang C4 und D) — **aber diese Nacharbeit und alles danach ist nicht gegengelesen.** Steht so im README und in den Anhängen.
