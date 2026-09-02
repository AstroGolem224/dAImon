#!/usr/bin/env bash
# Erzeugt die T-9.3-Mutanten reproduzierbar aus dem Gut-Muster.
#
# Basis ist `tests/fixtures/known-good/T-9.3/face` und NICHT der Arbeitsbaum
# und nicht HEAD. Zwei Gruende, beide teuer gelernt:
#
#  * Der Arbeitsbaum wird waehrend eines Verifiziererlaufs von einer zweiten
#    Sitzung beschrieben. Ein Mutant, der eine halbfertige Implementierung
#    mitkopiert, beweist nichts.
#  * Das Gut-Muster ist die Fassung, gegen die meta.sh unmittelbar davor
#    gruen gemessen hat. Damit ist jeder rote Mutantenlauf ein Unterschied
#    genau zu dieser einen Fassung -- und zu nichts sonst.
#
# ----------------------------------------------------------------------------
# Warum jeder Mutant seine eigenen Einheitentests MITNIMMT
# ----------------------------------------------------------------------------
# Ein Mutant, der bloss `cargo test` rot macht, sagt ueber die Messung nichts:
# der Verifizierer faellt dann am Kriterium "keine fehlgeschlagenen
# Rust-Tests", und ob er die Zusage im laufenden Face ueberhaupt sehen kann,
# bleibt offen.
#
# Deshalb entfernt jeder Mutant hier genau die Einheitentests, die seine
# Aenderung festhalten. Das ist zugleich die realistische Bauform des Fehlers,
# vor der CLAUDE.md warnt: jemand aendert eine Zusage und schreibt die
# Erwartung mit um -- "eine Attrappe mit neuem Hash". Danach ist `cargo test`
# gruen, und nur die Messung am laufenden Face kann es noch merken.
#
# Die Ausnahme ist `toenung-dauerhaft`: dort IST der Einheitentest das
# Kriterium (T-9.3 verlangt ihn namentlich im Lauf), und der Mutant entfernt
# ihn genau deshalb -- er soll belegen, dass "der Test fehlt" von "der Test
# ist gruen" unterschieden wird.
#
# Am Ende wird jeder Mutant gebaut UND getestet. Ein Mutant mit roten
# Einheitentests wuerde hier abgelehnt, nicht stillschweigend durchgereicht.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
BASIS="$REPO/tests/fixtures/known-good/T-9.3/face"
[[ -f "$BASIS/src/render.rs" ]] || { echo "Gut-Muster $BASIS unvollstaendig"; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" <<'PYEOF'
import os, shutil, sys

ziel_wurzel, basis = sys.argv[1], sys.argv[2]


def baum(name):
    """Legt einen Mutantenbaum als Kopie des Gut-Musters an.

    `symlinks=True`: die grossen Binaerassets sind im Gut-Muster Symlinks mit
    genau dieser Verzeichnistiefe. Als Kopien waeren es 1,3 MB je Mutant in
    der Historie.
    """
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

    Rueckwaerts werden nur `///` und `#[...]` mitgenommen -- in Rust gehoert
    genau das zum folgenden Item. Vorwaerts wird geklammert gezaehlt, nicht
    nach einer Zeile `    }` gesucht: ein verschachtelter Block mit derselben
    Einrueckung haette den Schnitt sonst zu frueh gesetzt.
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
    # Die Leerzeile davor mitnehmen, damit keine doppelten Luecken entstehen.
    if anfang > 0 and zeilen[anfang - 1].strip() == "":
        anfang -= 1
    return "\n".join(zeilen[:anfang] + zeilen[ende + 1:])


def notiz(ziel, text):
    with open(os.path.join(ziel, "mutation.txt"), "w", encoding="utf-8") as f:
        f.write(text)


# --- 1: emote-vier-umlaeufe --------------------------------------------------
z = baum("emote-vier-umlaeufe")
t = lies(z, "render.rs")
t = ersetzen(t, "pub const EMOTE_UMLAEUFE: u32 = 3;",
             "pub const EMOTE_UMLAEUFE: u32 = 4;", "render.rs/EMOTE_UMLAEUFE")
for name in ("tick_spielt_das_emote_drei_volle_umlaeufe_und_faellt_dann_auf_den_atem",
             "tick_ueberspringt_beide_emote_umlaeufe_auf_einmal_und_atmet_danach"):
    t = test_entfernen(t, name, "render.rs")
schreib(z, "render.rs", t)
notiz(z, """Das Emote spielt VIER Umlaeufe statt drei; die beiden Einheitentests, die
drei festhielten, sind entfernt -- `cargo test` ist gruen.

Zu fangen an der Stichprobe 4,4 s: der vierte Umlauf endet erst bei 5,333 s,
also steht dort noch die Emote-Zeile und `emote_laeuft` meldet true. Das
Atem-Taktfenster misst zusaetzlich Emote-Tempo statt Atemtempo.

Aus der anderen Richtung faengt ihn niemand: bei 1,0 s, 2,0 s und 3,2 s
verhaelt sich dieser Mutant exakt wie der Pruefling.
""")

# --- 2: emote-zwei-umlaeufe --------------------------------------------------
z = baum("emote-zwei-umlaeufe")
t = lies(z, "render.rs")
t = ersetzen(t, "pub const EMOTE_UMLAEUFE: u32 = 3;",
             "pub const EMOTE_UMLAEUFE: u32 = 2;", "render.rs/EMOTE_UMLAEUFE")
t = test_entfernen(
    t, "tick_spielt_das_emote_drei_volle_umlaeufe_und_faellt_dann_auf_den_atem",
    "render.rs")
schreib(z, "render.rs", t)
notiz(z, """Die Zusage von VOR dem 02.09.: das Emote spielt zweimal. Der Einheitentest,
der drei festhaelt, ist entfernt -- `cargo test` ist gruen.

Zu fangen an der Stichprobe 3,2 s: zwei Umlaeufe enden bei 2,667 s, also
steht dort schon die Atemzeile und `emote_laeuft` meldet false.

Dieser Mutant ist der Grund, warum "bei 4,4 s steht die Atemzeile" allein
nichts belegt: er bestuende dieses Kriterium.
""")

# --- 3: atem-so-schnell-wie-emote --------------------------------------------
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
notiz(z, """Atem und Emote laufen im selben Takt (ATEM_FPS = ANIMATION_FPS = 12) --
genau das, was der Nutzer am 02.09. beanstandet hat: ein Ruhezustand, der mit
zwoelf Bildern je Sekunde wogt. Die vier Einheitentests, die den langsamen
Atem festhielten, sind entfernt.

Drei Kriterien fangen ihn, und das ist Absicht: das Atem-Taktfenster gegen
ATEM_FPS, der Atem-Block von `idle`, und der Vergleich der beiden Raten
gegeneinander (das Emote muss mindestens doppelt so schnell schlagen). Der
letzte haengt an keinem der beiden Sollwerte und bliebe auch dann rot, wenn
jemand beide Konstanten zusammen verschoebe.

Die Zeilen-Kriterien bestuende er alle: welche Zeile wann gilt, aendert sich
nicht.
""")

# --- 4: idle-zeigt-wieder-emote ----------------------------------------------
z = baum("idle-zeigt-wieder-emote")
t = lies(z, "main.rs")
t = ersetzen(t, 'const MOODS_OHNE_EMOTE: [&str; 2] = ["idle", "sleeping"];',
             'const MOODS_OHNE_EMOTE: [&str; 1] = ["sleeping"];',
             "main.rs/MOODS_OHNE_EMOTE")
t = test_entfernen(t, "nur_sleeping_steht_still_idle_atmet", "main.rs")
schreib(z, "main.rs", t)
notiz(z, """`idle` ist aus MOODS_OHNE_EMOTE entfernt: der Ruhezustand fuehrt bei jeder
Rueckkehr wieder eine Geste auf. Der Einheitentest dazu ist entfernt --
`cargo test` ist gruen.

Zu fangen im Block `atem_block idle`: das Pruefstand-Pet bietet `idle` eine
Emote-Zeile an, und dieser Mutant nimmt sie. Drei der vier Stichproben stehen
dann auf der Emote- statt auf der Atemzeile, `emote_laeuft` meldet true, und
der Takt liegt bei 12 statt bei 3 fps.

Genau dafuer bietet das Pruefstand-Pet `idle` ueberhaupt ein Emote an: waere
sein Manifest sparsam, maesse der Lauf sein eigenes Schweigen.
""")

# --- 5: toenung-dauerhaft ----------------------------------------------------
z = baum("toenung-dauerhaft")
t = lies(z, "render.rs")
t = ersetzen(t, """pub fn toenungsanteil(manifest: f32, emote_laeuft: bool) -> f32 {
    if emote_laeuft {
        manifest.clamp(0.0, 1.0)
    } else {
        0.0
    }
}""", """pub fn toenungsanteil(manifest: f32, emote_laeuft: bool) -> f32 {
    // MUTANT: der Manifestwert gilt immer -- das Pet steht dauerhaft gruen,
    // rot oder gelb ueberlagert da.
    let _ = emote_laeuft;
    manifest.clamp(0.0, 1.0)
}""", "render.rs/toenungsanteil")
t = test_entfernen(t, "die_toenung_liegt_nur_waehrend_des_emotes", "render.rs")
schreib(z, "render.rs", t)
notiz(z, """Die Toenung liegt dauerhaft an statt nur waehrend des Emotes -- die dritte
Beanstandung des Nutzers vom 02.09. Der Einheitentest, der das festhielt, ist
mitentfernt; `cargo test` ist gruen und meldet nur weniger Tests.

Zu fangen am Kriterium "der Einheitentest 'die_toenung_liegt_nur_waehrend_des
_emotes' ist gelaufen und gruen". Es sucht die Zeile NAMENTLICH im
cargo-test-Protokoll und unterscheidet damit "der Test ist gruen" von "der
Test ist weg" -- ein Kriterium "keine roten Tests" bestuende dieser Mutant.

Von aussen ist er nicht messbar: der Diagnose-Socket kennt keine Pixel.
Deshalb steht das Kriterium ueberhaupt dort.
""")

# --- 6: toenung-an-der-aufrufstelle ------------------------------------------
z = baum("toenung-an-der-aufrufstelle")
t = lies(z, "surface.rs")
t = ersetzen(t, "toenungsanteil(atlas.layout.toenung, animator.emote_laeuft())",
             "toenungsanteil(atlas.layout.toenung, true)",
             "surface.rs/Aufrufstelle")
schreib(z, "surface.rs", t)
notiz(z, """`render::toenungsanteil` ist unveraendert und ihr Einheitentest gruen -- nur
die AUFRUFSTELLE umgeht sie: sie uebergibt `true` statt
`animator.emote_laeuft()`. Das Pet steht dauerhaft getoent da, und jede
Pruefung an der Funktion meldet Ruhe.

Genau der Fehler, den CLAUDE.md als teuersten dieses Repos fuehrt: das Stueck
ist gebaut, dokumentiert und gruen, und im Betrieb ruft es niemand so auf.

Zu fangen am Naht-Kriterium "genau ein Zeichenweg holt den Toenungsanteil aus
animator.emote_laeuft()". Kein Einheitentest sieht ihn (nach `sprite_committen`
kommt keiner ohne Wayland-Verbindung), und der Diagnose-Socket auch nicht.
""")

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe: unterscheiden sie sich, bauen sie, sind ihre Tests gruen? ----
# Ein Mutant mit roten Einheitentests wuerde vom Verifizierer am Kriterium
# "keine fehlgeschlagenen Rust-Tests" gefangen -- und damit waere ueber die
# Messung am laufenden Face nichts belegt.
rc=0
for m in emote-vier-umlaeufe emote-zwei-umlaeufe atem-so-schnell-wie-emote \
         idle-zeigt-wieder-emote toenung-dauerhaft toenung-an-der-aufrufstelle; do
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
exit $rc
