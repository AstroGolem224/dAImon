#!/usr/bin/env bash
# Erzeugt die T-9.2-Mutanten reproduzierbar aus dem Gut-Muster.
#
# Basis ist `tests/fixtures/known-good/T-9.2/face` und NICHT der Arbeitsbaum
# und nicht HEAD -- dieselbe Begruendung wie bei T-9.3: der Arbeitsbaum wird
# waehrend eines Laufs von einer zweiten Sitzung beschrieben, und das
# Gut-Muster ist die Fassung, gegen die meta.sh unmittelbar davor gruen
# gemessen hat.
#
# Beide Mutanten nehmen ihre Einheitentests MIT. Ein Mutant, der bloss
# `cargo test` rot macht, sagt ueber die Messung am laufenden Face nichts --
# der Verifizierer faellt dann am Kriterium "keine fehlgeschlagenen
# Rust-Tests", und ob er die Zusage ueberhaupt sehen kann, bleibt offen. Das
# ist zugleich die realistische Bauform des Fehlers: jemand aendert eine
# Zusage und schreibt die Erwartung mit um.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
BASIS="$REPO/tests/fixtures/known-good/T-9.2/face"
[[ -f "$BASIS/src/render.rs" ]] || { echo "Gut-Muster $BASIS unvollstaendig"; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" <<'PYEOF'
import os, shutil, sys

ziel_wurzel, basis = sys.argv[1], sys.argv[2]


def baum(name):
    """Kopie des Gut-Musters; `symlinks=True` haelt die Binaerassets als Links."""
    ziel = os.path.join(ziel_wurzel, name)
    if os.path.exists(os.path.join(ziel, "face")):
        shutil.rmtree(os.path.join(ziel, "face"))
    os.makedirs(ziel, exist_ok=True)
    shutil.copytree(basis, os.path.join(ziel, "face"), symlinks=True)
    return ziel


def lies(ziel, datei):
    with open(os.path.join(ziel, "face/src", datei), encoding="utf-8") as f:
        return f.read()


def schreib(ziel, datei, text):
    with open(os.path.join(ziel, "face/src", datei), "w", encoding="utf-8") as f:
        f.write(text)


def ersetzen(text, alt, neu, wo):
    if text.count(alt) != 1:
        raise SystemExit(f"Anker nicht genau einmal gefunden in {wo}: "
                         f"{alt[:60]!r} ({text.count(alt)}x)")
    return text.replace(alt, neu)


def test_entfernen(text, name, wo):
    """Schneidet einen `#[test] fn <name>` samt Doc-Block heraus.

    Rueckwaerts nur `///` und `#[...]` -- in Rust gehoert genau das zum
    folgenden Item. Vorwaerts geklammert gezaehlt und nicht nach einer Zeile
    `    }` gesucht: ein verschachtelter Block mit derselben Einrueckung
    haette den Schnitt sonst zu frueh gesetzt.
    """
    zeilen = text.split("\n")
    treffer = [i for i, z in enumerate(zeilen) if z.strip().startswith(f"fn {name}(")]
    if len(treffer) != 1:
        raise SystemExit(f"Test '{name}' nicht genau einmal gefunden in {wo} "
                         f"({len(treffer)}x)")
    i = treffer[0]
    anfang = i
    while anfang > 0 and (zeilen[anfang - 1].strip().startswith("///")
                          or zeilen[anfang - 1].strip().startswith("#[")):
        anfang -= 1
    tiefe, ende, offen = 0, None, False
    for j in range(i, len(zeilen)):
        for zeichen in zeilen[j]:
            if zeichen == "{":
                tiefe += 1
                offen = True
            elif zeichen == "}":
                tiefe -= 1
        if offen and tiefe == 0:
            ende = j
            break
    if ende is None:
        raise SystemExit(f"Test '{name}' in {wo}: Klammern gehen nicht auf")
    if anfang > 0 and zeilen[anfang - 1].strip() == "":
        anfang -= 1
    return "\n".join(zeilen[:anfang] + zeilen[ende + 1:])


def notiz(ziel, text):
    with open(os.path.join(ziel, "mutation.txt"), "w", encoding="utf-8") as f:
        f.write(text)


# --- 1: atem-so-schnell-wie-emote --------------------------------------------
z = baum("atem-so-schnell-wie-emote")
t = lies(z, "render.rs")
t = ersetzen(t, "pub const ATEM_FPS: u32 = 3;", "pub const ATEM_FPS: u32 = 12;",
             "render.rs/ATEM_FPS")
for name in ("das_emote_laeuft_schnell_der_atem_langsam_und_der_wechsel_schaltet_um",
             "mood_setzen_mit_unveraenderter_zeile_laesst_den_laufenden_loop_stehen",
             "mood_setzen_nach_halbem_loop_faengt_mit_neuer_spaltenzahl_wieder_vorn_an",
             "tick_verspaeteter_weckruf_holt_alle_faelligen_schritte_auf_einmal_nach"):
    t = test_entfernen(t, name, "render.rs")
schreib(z, "render.rs", t)
notiz(z, """Der Atem laeuft mit 12 statt mit 3 Bildern je Sekunde -- ein Ruhezustand, der
wogt statt zu atmen, und damit die Idle-CPU-Zusage aus T-1.5 auf der Kippe.
Die vier Einheitentests, die den langsamen Atem festhielten, sind entfernt;
`cargo test` ist gruen.

Zu fangen an drei Kriterien je animiertem Block: "der Atem dreht nicht durch"
(Obergrenze bei ATEM_FPS), "der Atem ist deutlich langsamer als
ANIMATION_FPS" und "es wird nicht frei neugezeichnet".

Die Untergrenzen bestuende er alle -- er laeuft ja zu SCHNELL. Genau darum
hat jeder Atemwert hier zwei Schranken und nicht nur eine.
""")

# --- 2: idle-wieder-still ----------------------------------------------------
z = baum("idle-wieder-still")
t = lies(z, "main.rs")
t = ersetzen(t, 'const RUHIGE_MOODS: [&str; 1] = ["sleeping"];',
             'const RUHIGE_MOODS: [&str; 2] = ["sleeping", "idle"];',
             "main.rs/RUHIGE_MOODS")
t = test_entfernen(t, "nur_sleeping_steht_still_idle_atmet", "main.rs")
schreib(z, "main.rs", t)
notiz(z, """Die Zusage von VOR dem 02.09.: `idle` steht wieder still. Der Einheitentest
dazu ist entfernt, `cargo test` ist gruen.

Zu fangen im Block B: "in idle: der Atem laeuft", "es entstehen auch Bilder"
und die Positivkontrolle des zweiten Messgeraets ("die Weckvorgaenge sehen
den Atem"). Alle drei melden Null, wo Bewegung zugesagt ist.

Dieser Mutant ist der Grund, warum Block B ueberhaupt eine UNTERE Schranke
hat. Bis zum 02.09. mass dieser Verifizierer an derselben Stelle "exakt 0" --
er haette den Mutanten damals fuer den Pruefling gehalten.
""")

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe: unterscheiden sie sich, bauen sie, sind ihre Tests gruen? ----
rc=0
for m in atem-so-schnell-wie-emote idle-wieder-still; do
  n="$(diff -r "$BASIS/src" "$SCRIPT_DIR/$m/face/src" | grep -c '^[<>]')"
  echo "$m: $n geaenderte Zeilen gegenueber dem Gut-Muster"
  # Zwei Diff-Zeilen sind EINE geaenderte Quellzeile -- und der kleinste
  # ehrliche Mutant hier ist genau eine (`toenung-an-der-aufrufstelle`).
  # Gesucht ist die unveraenderte Kopie, und die hat null.
  if [[ "$n" -lt 2 ]]; then echo "  FEHLER: $m ist eine unveraenderte Kopie"; rc=1; fi
  if [[ -e "$SCRIPT_DIR/$m/face/target" ]]; then
    echo "  FEHLER: $m enthaelt ein target/ -- das schleppt fremde Binaries mit"; rc=1
  fi
  bd="$(mktemp -d)"
  ( cd "$SCRIPT_DIR/$m/face" && CARGO_TARGET_DIR="$bd" timeout 900 cargo test -p face ) >"$bd/log" 2>&1
  brc=$?
  if [[ "$brc" -ne 0 ]]; then
    echo "  FEHLER: $m baut nicht oder hat rote Einheitentests"; tail -25 "$bd/log"; rc=1
  else
    echo "  baut durch, $(awk '/^test result:/ {p+=$4} END {print p+0}' "$bd/log") Tests gruen"
  fi
  rm -rf -- "$bd"
done
# Und: die beiden Mutanten muessen sich voneinander unterscheiden.
if diff -rq "$SCRIPT_DIR/atem-so-schnell-wie-emote/face/src" \
            "$SCRIPT_DIR/idle-wieder-still/face/src" >/dev/null; then
  echo "FEHLER: die beiden Mutanten sind identisch"; rc=1
fi
exit $rc
