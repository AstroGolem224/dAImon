#!/usr/bin/env bash
# Erzeugt die beiden T-2.5-Mutanten reproduzierbar.
#
# Basis ist bewusst ein GEPINNTER Commit, NICHT der Arbeitsbaum und nicht HEAD: waehrend
# dieser Verifizierer entstand, schrieb der Builder parallel und blind an
# `face/src`. Ein Mutant, der eine halbfertige Implementierung mitkopiert,
# beweist nichts. HEAD ist der Stand vor T-2.5 und liegt fest.
#
# Zwei Fallen aus der Projekthistorie sind hier ausdruecklich abgesichert:
#   * `git archive` traegt kein `target/` mit -- eine Fixture-Kopie mit dem
#     Binary der UNMUTIERTEN Quelle hat schon einmal einen Mutanten gruen
#     gemeldet.
# Erzeugt ausserdem den Blindstellen-Beleg unter tests/blindstellen/ --
# einen Baum, den der Verifizierer ABSICHTLICH nicht faengt. Er liegt
# bewusst nicht unter tests/mutants/, weil meta.sh dort Erkennung
# verlangt und das Einfrieren sonst scheiterte.
#
#   * Am Ende wird geprueft, dass sich jeder Mutant vom Basisbaum
#     UNTERSCHEIDET und dass er baut. Ein abgebrochenes Erzeugungsskript hat
#     schon einmal eine unveraenderte Kopie hinterlassen.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

# Der Basis-Commit ist GEPINNT, nicht HEAD. Grund, real passiert: sobald die
# T-2.5-Implementierung committet war, enthielt HEAD die Diagnose-Felder schon
# und die Anker unten fanden nichts mehr -- das Skript war exakt bis zu dem
# Zeitpunkt reproduzierbar, ab dem man es braucht. b1db898 ist der Stand
# unmittelbar vor T-2.5.
BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-b1db898292f6b6ae175f4a16be545c14238a5574}"
git -C "$REPO" archive "$BASIS_COMMIT" face | tar -x -C "$BASIS" || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/face/src/main.rs" ]] || { echo "Basisbaum unvollstaendig"; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" <<'PYEOF'
import os, shutil, sys

ziel_wurzel, basis = sys.argv[1], sys.argv[2]

# --- die gemeinsame Vertragserweiterung --------------------------------------
# Beide Mutanten muessen die Diagnose-Felder ueberhaupt liefern, sonst
# scheitern sie schon an der Vertragspruefung und beweisen nichts ueber das
# Kriterium, das sie verletzen sollen.
DIAG_FELDER = ("""    pub sprite_x: i32,
    pub sprite_y: i32,
""", """    pub sprite_x: i32,
    pub sprite_y: i32,
    /// T-2.5: Name des gebundenen Outputs. Leer = nichts gebunden.
    pub output: String,
    /// T-2.5: +1 je Neuerzeugung der Layer-Surface nach Output-Removal.
    pub output_wechsel: u64,
""")

DIAG_JSON = ("""                "\\"sprite_x\\":{},\\"sprite_y\\":{}}}"
""", """                "\\"sprite_x\\":{},\\"sprite_y\\":{},",
                "\\"output\\":\\"{}\\",\\"output_wechsel\\":{}}}"
""")

DIAG_ARGS = ("""            self.sprite_y
        )
""", """            self.sprite_y,
            escape(&self.output),
            self.output_wechsel
        )
""")

def ersetzen(text, alt, neu, wo):
    if text.count(alt) != 1:
        raise SystemExit(f"Anker nicht genau einmal gefunden in {wo}: {alt[:60]!r} "
                         f"({text.count(alt)}x)")
    return text.replace(alt, neu)

# Binaerassets werden VERLINKT, nicht kopiert: Schrift und Spritesheet sind
# zusammen 1,3 MB, und drei Baeume mal 1,3 MB waeren 4 MB duplizierte Blobs in
# der Historie. Der Bestand macht das bei T-2.4 schon so. pet.json bleibt eine
# echte Datei -- sie ist Text, klein, und ein Mutant koennte sie eines Tages
# veraendern wollen.
GROSSE_ASSETS = ("DejaVuSans.ttf", "spritesheet.png", "DejaVu-LICENSE.txt")

def assets_verlinken(ziel):
    repo = os.path.dirname(os.path.dirname(ziel_wurzel))          # .../tests
    repo = os.path.dirname(repo)                                   # Repo-Wurzel
    assets = os.path.join(ziel, "face", "assets")
    for name in GROSSE_ASSETS:
        pfad = os.path.join(assets, name)
        if not os.path.exists(pfad):
            continue
        os.remove(pfad)
        os.symlink(os.path.relpath(os.path.join(repo, "face/assets", name), assets), pfad)

def baum_blindstelle(name):
    """Belege fuer Blindstellen liegen NICHT unter tests/mutants/.

    meta.sh verlangt von jedem Verzeichnis dort, dass der Verifizierer es
    zurueckweist. Ein Beleg, der absichtlich durchgelassen wird, wuerde das
    Einfrieren blockieren -- und wer ihn dann loeschte, haette die Blindstelle
    mit entfernt. Deshalb tests/blindstellen/.
    """
    ziel = os.path.join(os.path.dirname(os.path.dirname(ziel_wurzel)), "blindstellen", name)
    if os.path.exists(os.path.join(ziel, "face")):
        shutil.rmtree(os.path.join(ziel, "face"))
    os.makedirs(ziel, exist_ok=True)
    shutil.copytree(os.path.join(basis, "face"), os.path.join(ziel, "face"))
    assets_verlinken(ziel)
    return ziel

def baum(name):
    ziel = os.path.join(ziel_wurzel, name)
    if os.path.exists(os.path.join(ziel, "face")):
        shutil.rmtree(os.path.join(ziel, "face"))
    os.makedirs(ziel, exist_ok=True)
    shutil.copytree(os.path.join(basis, "face"), os.path.join(ziel, "face"))
    assets_verlinken(ziel)
    return ziel

def lies(ziel, datei):
    with open(os.path.join(ziel, "face/src", datei), encoding="utf-8") as f:
        return f.read()

def schreib(ziel, datei, text):
    with open(os.path.join(ziel, "face/src", datei), "w", encoding="utf-8") as f:
        f.write(text)

def diag_erweitern(text, mit_wake_flag=False):
    text = ersetzen(text, *DIAG_FELDER, "diag.rs/Felder")
    text = ersetzen(text, *DIAG_JSON, "diag.rs/JSON")
    text = ersetzen(text, *DIAG_ARGS, "diag.rs/Argumente")
    if mit_wake_flag:
        text += """
/// MUTANT (kein-redraw-nach-wake): wird beim zweiten `configure` gesetzt --
/// das ist auf dieser Maschine gemessen genau der DPMS-Wake.
pub static NACH_WAKE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
"""
    return text

# --- Mutant 1: bindung-an-none ----------------------------------------------
z = baum("bindung-an-none")
schreib(z, "diag.rs", diag_erweitern(lies(z, "diag.rs")))
s = lies(z, "surface.rs")
s = ersetzen(s, """            // NULL wuerde den zuletzt benutzten Monitor waehlen und ist fuer
            // ein pro-Output-Overlay deshalb keine stabile Zuordnung.
            Some(output),
""", """            // MUTANT: die Layer-Surface wird OHNE expliziten Output erzeugt.
            // Der Compositor waehlt dann selbst; `output` bleibt leer, und
            // welcher Monitor es wird, ist nicht mehr zugesagt.
            {
                let _ = output;
                None
            },
""", "surface.rs/get_layer_surface")
schreib(z, "surface.rs", s)
with open(os.path.join(z, "mutation.txt"), "w", encoding="utf-8") as f:
    f.write(
        "Die Layer-Surface wird mit None statt mit Some(output) erzeugt -- der "
        "Compositor waehlt den Monitor. Der Diagnose-Vertrag ist erfuellt "
        "(output/output_wechsel existieren), aber `output` bleibt leer und "
        "DAIMON_FACE_OUTPUT hat keine Wirkung.\n\n"
        "Das soll Kriterium 1 reissen (explizite, benannte Bindung) und "
        "Kriterium 3 (konfigurierbarer Output). Gemessen wird beides zur "
        "Laufzeit: am Diagnose-Feld und daran, dass `get_layer_surface` im "
        "Protokoll-Mitschnitt kein `wl_output@N` nennt.\n")

# --- Mutant 2: kein-redraw-nach-wake ----------------------------------------
z = baum("kein-redraw-nach-wake")
schreib(z, "diag.rs", diag_erweitern(lies(z, "diag.rs"), mit_wake_flag=True))

m = lies(z, "main.rs")
# Die Output-Wahl ist hier absichtlich KORREKT umgesetzt, damit der Mutant
# nicht schon an Kriterium 1/3 haengenbleibt und ueber Kriterium 4 nichts
# aussagen wuerde.
m = ersetzen(m, """    let output = app
        .output_state
        .outputs()
        .next()
        .expect("kein wl_output gefunden");
""", """    let wunsch = std::env::var("DAIMON_FACE_OUTPUT")
        .ok()
        .filter(|s| !s.is_empty());
    let output = wunsch
        .as_ref()
        .and_then(|name| {
            app.output_state.outputs().find(|o| {
                app.output_state
                    .info(o)
                    .and_then(|i| i.name)
                    .as_deref()
                    == Some(name.as_str())
            })
        })
        .or_else(|| app.output_state.outputs().next())
        .expect("kein wl_output gefunden");
    let output_name = app
        .output_state
        .info(&output)
        .and_then(|i| i.name)
        .unwrap_or_default();
    match app.diagnose.lock() {
        Ok(mut z) => z.output = output_name.clone(),
        Err(v) => v.into_inner().output = output_name.clone(),
    }
""", "main.rs/Output-Wahl")

m = ersetzen(m, """        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.configure_empfangen += 1,
            Err(vergiftet) => vergiftet.into_inner().configure_empfangen += 1,
        }
""", """        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.configure_empfangen += 1,
            Err(vergiftet) => vergiftet.into_inner().configure_empfangen += 1,
        }
        // MUTANT: das zweite configure ist auf dieser Maschine gemessen der
        // DPMS-Wake. Ab hier wird nicht mehr gezeichnet.
        let empfangen = match self.diagnose.lock() {
            Ok(z) => z.configure_empfangen,
            Err(v) => v.into_inner().configure_empfangen,
        };
        if empfangen >= 2 {
            crate::diag::NACH_WAKE.store(true, std::sync::atomic::Ordering::Relaxed);
        }
""", "main.rs/configure")
schreib(z, "main.rs", m)

s = lies(z, "surface.rs")
s = ersetzen(s, """        callback_armieren: bool,
    ) -> Result<u64, String> {
""", """        callback_armieren: bool,
    ) -> Result<u64, String> {
        // MUTANT: nach dem Wake wird kein Sprite mehr committet. Der Prozess
        // lebt, die Steuerung antwortet -- nur last_render_ts steht still.
        if crate::diag::NACH_WAKE.load(std::sync::atomic::Ordering::Relaxed) {
            let _ = callback_armieren;
            return Ok(0);
        }
""", "surface.rs/sprite_committen")
schreib(z, "surface.rs", s)
with open(os.path.join(z, "mutation.txt"), "w", encoding="utf-8") as f:
    f.write(
        "Output-Bindung und DAIMON_FACE_OUTPUT sind korrekt umgesetzt -- der "
        "Mutant besteht Kriterium 1 und 3 absichtlich, damit sein Durchfallen "
        "eine Aussage ueber Kriterium 4 ist und nicht ueber irgendetwas "
        "anderes.\n\n"
        "Kaputt ist nur: ab dem zweiten `configure` -- auf dieser Maschine "
        "gemessen genau der DPMS-Wake -- committet `sprite_committen` nichts "
        "mehr und meldet Ok(0). Der Prozess lebt weiter, der Steuer-Socket "
        "antwortet weiter mit `ok`, aber `last_render_ts` steht still.\n\n"
        "Genau dafuer reicht 'der Prozess lebt noch' als Messung nicht. Der "
        "Verifizierer erzwingt nach dem Wake einen Zustandswechsel und "
        "verlangt einen frischen Zeitstempel binnen 2 s.\n")

# --- Mutant 3: wunsch-ignoriert ---------------------------------------------
# Der Beleg fuer eine BLINDSTELLE des Verifizierers, nicht fuer einen Fehler
# des Pruefling. Siehe mutation.txt: dieser Mutant wird absichtlich NICHT
# gefangen, und das ist der Punkt.
z = baum_blindstelle("T-2.5-wunsch-ignoriert")
schreib(z, "diag.rs", diag_erweitern(lies(z, "diag.rs")))
m = lies(z, "main.rs")
m = ersetzen(m, """    let output = app
        .output_state
        .outputs()
        .next()
        .expect("kein wl_output gefunden");
""", """    // MUTANT: DAIMON_FACE_OUTPUT wird gelesen und dann WEGGEWORFEN. Es
    // gewinnt immer der erste Output. Auf einer Maschine mit genau einem
    // Monitor ist das von einer korrekten Auswahl nach Namen nicht zu
    // unterscheiden -- genau das soll dieser Mutant zeigen.
    let _wunsch_wird_ignoriert = std::env::var("DAIMON_FACE_OUTPUT").ok();
    let output = app
        .output_state
        .outputs()
        .next()
        .expect("kein wl_output gefunden");
    let output_name = app
        .output_state
        .info(&output)
        .and_then(|i| i.name)
        .unwrap_or_default();
    match app.diagnose.lock() {
        Ok(mut z) => z.output = output_name.clone(),
        Err(v) => v.into_inner().output = output_name.clone(),
    }
""", "main.rs/Output-Wahl (ignoriert)")
schreib(z, "main.rs", m)
with open(os.path.join(z, "mutation.txt"), "w", encoding="utf-8") as f:
    f.write(
        "ACHTUNG: Dieser Mutant wird vom Verifizierer ABSICHTLICH NICHT "
        "gefangen. Er ist der Beleg fuer eine Blindstelle, kein Testfall, der "
        "gruen werden soll.\n\n"
        "Die Mutation: `DAIMON_FACE_OUTPUT` wird gelesen und sofort "
        "verworfen; gebunden wird immer der erste Output. Die Bindung selbst "
        "ist explizit und benannt (Kriterium 1 erfuellt), der Name wandert "
        "korrekt in die Diagnose, der DPMS-Zyklus wird ueberlebt.\n\n"
        "Warum das nicht auffaellt: diese Maschine hat genau einen Output "
        "(HDMI-A-1). 'der erste Output' und 'der per Namen gewuenschte "
        "Output' sind derselbe Name. Alle drei Teilpruefungen von Kriterium 3 "
        "melden gruen, obwohl die zugesagte Auswahl nach Namen gar nicht "
        "stattfindet.\n\n"
        "Warum das nicht 'behoben' wird: die einzige ehrliche Gegenprobe "
        "braucht einen zweiten Monitor -- DAIMON_FACE_OUTPUT auf den zweiten "
        "setzen und pruefen, dass NICHT der erste gebunden wird. Ein Ersatz "
        "ueber ein Selbstzeugnis des Prueflings (etwa eine stderr-Zeile 'habe "
        "Output X gewaehlt') wuerde nur den vom Pruefling selbst gefuehrten "
        "Bericht messen -- derselbe Fehler wie beim T-1.8-Verifizierer, Fall "
        "9 in HANDOVER.md. Die Blindstelle wird deshalb benannt und "
        "dokumentiert statt zugedeckt.\n\n"
        "Erwartetes Ergebnis: tests/verify/T-2.5.sh meldet gegen diesen "
        "Baum Exit 0. Wenn er hier jemals rot wird, hat jemand eine Pruefung "
        "hinzugefuegt, die mehr kann -- dann gehoert dieser Text korrigiert.\n")

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe: unterscheiden sich die Mutanten wirklich? -------------------
rc=0
for m in bindung-an-none kein-redraw-nach-wake; do
  n="$(diff -r "$BASIS/face/src" "$SCRIPT_DIR/$m/face/src" | grep -c '^[<>]')"
  echo "$m: $n geaenderte Zeilen gegenueber HEAD"
  if [[ "$n" -lt 3 ]]; then echo "  FEHLER: $m ist praktisch eine unveraenderte Kopie"; rc=1; fi
  if [[ -e "$SCRIPT_DIR/$m/face/target" ]]; then
    echo "  FEHLER: $m enthaelt ein target/ -- das schleppt fremde Binaries mit"; rc=1
  fi
  bd="$(mktemp -d)"
  ( cd "$SCRIPT_DIR/$m/face" && CARGO_TARGET_DIR="$bd" timeout 900 cargo build -p face ) >"$bd/log" 2>&1
  brc=$?
  if [[ "$brc" -ne 0 ]]; then echo "  FEHLER: $m baut nicht"; tail -25 "$bd/log"; rc=1
  else echo "  baut durch"; fi
  rm -rf -- "$bd"
done
# Und: die beiden Mutanten muessen sich voneinander unterscheiden.
if diff -rq "$SCRIPT_DIR/bindung-an-none/face/src" "$SCRIPT_DIR/kein-redraw-nach-wake/face/src" >/dev/null; then
  echo "FEHLER: die beiden Mutanten sind identisch"; rc=1
fi
exit $rc
