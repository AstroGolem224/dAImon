#!/usr/bin/env bash
# Erzeugt die drei T-2.7-Mutanten reproduzierbar.
#
# BESONDERHEIT GEGENUEBER ALLEN FRUEHEREN MUTANTEN: die Grenze dieses Tasks
# liegt im HUB, nicht im Face. Ein Mutantenbaum enthaelt deshalb `face/` UND
# `daimon/`, und der Verifizierer importiert den Hub aus dem Baum
# (PYTHONPATH), statt den des Arbeitsbaums zu benutzen.
#
# Basis ist ein GEPINNTER Commit, NICHT HEAD und nicht der Arbeitsbaum.
# Grund, bei T-2.5 real passiert: sobald die Implementierung committet war,
# enthielt HEAD die neuen Felder schon, die Anker fanden nichts mehr, und das
# Skript war exakt bis zu dem Moment reproduzierbar, ab dem man es braucht.
# 6d71dc5 ist der Stand unmittelbar vor T-2.7.
#
# Alle drei Mutanten bekommen zuerst eine VOLLSTAENDIGE und KORREKTE
# T-2.7-Umsetzung eingesetzt (Allowlist im Hub, `wahrnehmung_aus` in der
# Produzententabelle, Diagnose-Felder, `menu`-Steuerbefehl) und danach genau
# einen Defekt. Ohne die korrekte Grundlage wuerde jeder Mutant schon am
# Vertrag scheitern und ueber sein eigentliches Kriterium nichts aussagen --
# dieselbe Ueberlegung wie bei den T-2.5-Mutanten.
#
# Zwei Fallen aus der Projekthistorie sind abgesichert:
#   * `git archive` traegt kein `target/` mit. Eine Fixture-Kopie mit dem
#     Binary der UNMUTIERTEN Quelle hat schon einmal einen Mutanten gruen
#     gemeldet.
#   * Am Ende wird geprueft, dass sich jeder Baum vom Basisbaum UND von den
#     anderen unterscheidet und dass er baut. Ein abgebrochenes
#     Erzeugungsskript hat schon einmal eine unveraenderte Kopie hinterlassen.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../../.." || exit 1; pwd)"
BASIS="$(mktemp -d)"
trap 'rm -rf -- "$BASIS"' EXIT

BASIS_COMMIT="${DAIMON_MUTANT_BASIS:-6d71dc5b3167f7324f6fa130700b66cdcb95d76b}"
git -C "$REPO" archive "$BASIS_COMMIT" face daimon | tar -x -C "$BASIS" \
  || { echo "git archive $BASIS_COMMIT fehlgeschlagen"; exit 1; }
[[ -f "$BASIS/face/src/main.rs" && -f "$BASIS/daimon/hub/daemon.py" ]] \
  || { echo "Basisbaum unvollstaendig"; exit 1; }

python3 - "$SCRIPT_DIR" "$BASIS" <<'PYEOF'
import os, shutil, sys

ziel_wurzel, basis = sys.argv[1], sys.argv[2]
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ziel_wurzel)))


def ersetzen(text, alt, neu, wo):
    if text.count(alt) != 1:
        raise SystemExit(f"Anker nicht genau einmal gefunden in {wo}: "
                         f"{alt[:70]!r} ({text.count(alt)}x)")
    return text.replace(alt, neu)


# Binaerassets werden VERLINKT, nicht kopiert: Schrift und Spritesheet sind
# zusammen 1,3 MB, und drei Baeume mal 1,3 MB waeren 4 MB duplizierte Blobs in
# der Historie. Der Bestand macht das bei T-2.4 und T-2.5 schon so.
GROSSE_ASSETS = ("DejaVuSans.ttf", "spritesheet.png", "DejaVu-LICENSE.txt")


def assets_verlinken(ziel):
    assets = os.path.join(ziel, "face", "assets")
    for name in GROSSE_ASSETS:
        pfad = os.path.join(assets, name)
        if not os.path.exists(pfad):
            continue
        os.remove(pfad)
        os.symlink(os.path.relpath(os.path.join(REPO, "face/assets", name), assets), pfad)


def baum(name):
    ziel = os.path.join(ziel_wurzel, name)
    for teil in ("face", "daimon"):
        if os.path.exists(os.path.join(ziel, teil)):
            shutil.rmtree(os.path.join(ziel, teil))
    os.makedirs(ziel, exist_ok=True)
    for teil in ("face", "daimon"):
        shutil.copytree(os.path.join(basis, teil), os.path.join(ziel, teil))
    assets_verlinken(ziel)
    return ziel


def lies(ziel, datei):
    with open(os.path.join(ziel, datei), encoding="utf-8") as f:
        return f.read()


def schreib(ziel, datei, text):
    with open(os.path.join(ziel, datei), "w", encoding="utf-8") as f:
        f.write(text)


# =============================================================================
# Die gemeinsame, KORREKTE T-2.7-Umsetzung
# =============================================================================
# Sie ist absichtlich so knapp wie moeglich: sie muss nur so weit stimmen, dass
# der Verifizierer ueberhaupt zu dem Kriterium kommt, das der jeweilige Mutant
# reisst. Sie ist kein Vorschlag an den Builder und sieht dessen Umsetzung
# nicht -- beide entstehen blind gegen dieselbe Akzeptanzliste.

# --- Python: die Allowlist ---------------------------------------------------
IPC_FACE = (
    '    "face": frozenset({"bubble_dismiss"}),\n',
    '    "face": frozenset({"bubble_dismiss", "wahrnehmung_aus"}),\n',
)

DAEMON_IMPORT = (
    "import secrets\nimport socket\n",
    "import secrets\nimport socket\nimport subprocess\n",
)

DAEMON_KONSTANTE = (
    "MAX_ZEILE = 1 << 20  # 1 MiB. Eine Hook-Nutzlast ist Kilobytes gross.\n",
    "MAX_ZEILE = 1 << 20  # 1 MiB. Eine Hook-Nutzlast ist Kilobytes gross.\n"
    "\n"
    "# T-2.7: was das Face abschalten darf. Der Schluessel kommt aus der\n"
    "# Nachricht, der UNIT-NAME aus dieser Tabelle -- nie umgekehrt.\n"
    'WAHRNEHMUNG_UNITS = {\n'
    '    "ears": "daimon-ears.service",\n'
    '    "eyes": "daimon-eyes.service",\n'
    "}\n",
)

DAEMON_FACE_ZWEIG = (
    """                if produzent == "face":
                    # `ipc.pruefe_typ` laesst hier ausschliesslich
                    # bubble_dismiss durch. Das Face erhaelt insbesondere
                    # keine Auth-Faehigkeit aus T-1.7 zurueck.
                    self.state.clear_bubble()
                    continue
""",
    """                if produzent == "face":
                    if event.type == "wahrnehmung_aus":
                        self._wahrnehmung_aus(event.payload or {})
                    else:
                        self.state.clear_bubble()
                    continue
""",
)

DAEMON_METHODE_ANKER = "    def _zaehle_abweisung(self, typ: str) -> None:"

DAEMON_METHODE = '''    def _wahrnehmung_aus(self, payload: dict) -> None:
        """T-2.7: Wahrnehmung abschalten. Nur abschalten, nur diese Units."""
        ziel = payload.get("ziel")
        unit = WAHRNEHMUNG_UNITS.get(ziel) if isinstance(ziel, str) else None
        if unit is None:
            self.log.warn("wahrnehmung_aus mit unbekanntem Ziel verworfen",
                          DAIMON_ZIEL=str(ziel)[:80])
            return
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=20, check=False)

'''

# --- Rust: Diagnose ----------------------------------------------------------
DIAG_FELDER = (
    """    /// T-2.5: +1 je Neuerzeugung der Layer-Surface nach Output-Removal.
    /// Die Erstbindung zaehlt nicht mit.
    pub output_wechsel: u64,
}
""",
    """    /// T-2.5: +1 je Neuerzeugung der Layer-Surface nach Output-Removal.
    /// Die Erstbindung zaehlt nicht mit.
    pub output_wechsel: u64,
    /// T-2.7: das Kontextmenue-Popup ist gemappt.
    pub menu_offen: bool,
    /// T-2.7: +1 je tatsaechlich ausgeloester Menueaktion.
    pub menu_aktionen: u64,
    /// T-2.7: "ears_aus" | "eyes_aus" | "beenden" | "".
    pub letzte_menu_aktion: String,
}
""",
)

DIAG_JSON = (
    """                "\\"output\\":\\"{}\\",\\"output_wechsel\\":{}}}"
""",
    """                "\\"output\\":\\"{}\\",\\"output_wechsel\\":{},",
                "\\"menu_offen\\":{},\\"menu_aktionen\\":{},",
                "\\"letzte_menu_aktion\\":\\"{}\\"}}"
""",
)

DIAG_ARGS = (
    """            escape(&self.output),
            self.output_wechsel
        )
""",
    """            escape(&self.output),
            self.output_wechsel,
            self.menu_offen,
            self.menu_aktionen,
            escape(&self.letzte_menu_aktion)
        )
""",
)

DIAG_TEST = (
    """            output_wechsel: 2,
        };
""",
    """            output_wechsel: 2,
            menu_offen: false,
            menu_aktionen: 0,
            letzte_menu_aktion: String::new(),
        };
""",
)

# --- Rust: Meldeweg zum Hub --------------------------------------------------
HUB_MELDEN = (
    """        strom
            .write_all(b"{\\"v\\":1,\\"type\\":\\"bubble_dismiss\\",\\"payload\\":{}}\\n")
            .map_err(|fehler| format!("bubble_dismiss senden: {fehler}"))
    }
""",
    """        strom
            .write_all(b"{\\"v\\":1,\\"type\\":\\"bubble_dismiss\\",\\"payload\\":{}}\\n")
            .map_err(|fehler| format!("bubble_dismiss senden: {fehler}"))
    }

    /// T-2.7: Wahrnehmung abschalten. `ziel` ist ein SCHLUESSEL, kein
    /// Unit-Name -- welche Unit dahintersteht, entscheidet allein der Hub.
    pub fn wahrnehmung_aus_melden(&self, ziel: &str) -> Result<(), String> {
        let mut strom = UnixStream::connect(&self.melde_pfad)
            .map_err(|fehler| format!("Face-Meldeweg {}: {fehler}", self.melde_pfad.display()))?;
        let zeile = format!(
            "{{\\"v\\":1,\\"type\\":\\"wahrnehmung_aus\\",\\"payload\\":{{\\"ziel\\":\\"{ziel}\\"}}}}\\n"
        );
        strom
            .write_all(zeile.as_bytes())
            .map_err(|fehler| format!("wahrnehmung_aus senden: {fehler}"))
    }
""",
)

# --- Rust: Steuerbefehl ------------------------------------------------------
CONTROL_BEFEHLE = (
    """        "sichtbar an" => Some("sichtbar:true".to_owned()),
""",
    """        "menu ears_aus" => Some("menu:ears_aus".to_owned()),
        "menu eyes_aus" => Some("menu:eyes_aus".to_owned()),
        "menu beenden" => Some("menu:beenden".to_owned()),
        "sichtbar an" => Some("sichtbar:true".to_owned()),
""",
)

MAIN_ZWEIG = (
    """                    None => match befehl.as_str() {
                        "sichtbar:true" => {
""",
    """                    None => match befehl.as_str() {
                        "menu:ears_aus" => app.menu_aktion("ears_aus", Some("ears")),
                        "menu:eyes_aus" => app.menu_aktion("eyes_aus", Some("eyes")),
                        "menu:beenden" => app.menu_aktion("beenden", None),
                        "sichtbar:true" => {
""",
)

MAIN_METHODE_ANKER = "    fn position_speichern(&self, position: (i32, i32)) {"

MAIN_METHODE = """    /// T-2.7: eine Menueaktion. Der Steuerbefehl fuehrt hierher; das Popup
    /// wird dabei NICHT geoeffnet.
    fn menu_aktion(&mut self, name: &str, ziel: Option<&str>) {
        if let (Some(ziel), Some(hub)) = (ziel, &self.hub) {
            if let Err(fehler) = hub.wahrnehmung_aus_melden(ziel) {
                eprintln!("{fehler}");
            }
        }
        match self.diagnose.lock() {
            Ok(mut zustand) => {
                zustand.menu_aktionen += 1;
                zustand.letzte_menu_aktion = name.to_owned();
            }
            Err(vergiftet) => {
                let mut zustand = vergiftet.into_inner();
                zustand.menu_aktionen += 1;
                zustand.letzte_menu_aktion = name.to_owned();
            }
        }
        if ziel.is_none() {
            self.beendet = true;
        }
    }

"""


def grundlage(ziel, *, ipc_face=None, daemon_methode=None,
              control_befehle=None, main_methode=None):
    """Setzt die korrekte T-2.7-Umsetzung ein. Die Parameter erlauben es einem
    Mutanten, genau ein Stueck davon durch seine eigene Fassung zu ersetzen."""
    # Python
    t = lies(ziel, "daimon/common/ipc.py")
    t = ersetzen(t, IPC_FACE[0], ipc_face or IPC_FACE[1], "ipc.py/PRODUZENTEN")
    schreib(ziel, "daimon/common/ipc.py", t)

    t = lies(ziel, "daimon/hub/daemon.py")
    t = ersetzen(t, *DAEMON_IMPORT, "daemon.py/Importe")
    t = ersetzen(t, *DAEMON_KONSTANTE, "daemon.py/Allowlist")
    t = ersetzen(t, *DAEMON_FACE_ZWEIG, "daemon.py/face-Zweig")
    t = ersetzen(t, DAEMON_METHODE_ANKER,
                 (daemon_methode or DAEMON_METHODE) + DAEMON_METHODE_ANKER,
                 "daemon.py/Methode")
    schreib(ziel, "daimon/hub/daemon.py", t)

    # Rust
    t = lies(ziel, "face/src/diag.rs")
    t = ersetzen(t, *DIAG_FELDER, "diag.rs/Felder")
    t = ersetzen(t, *DIAG_JSON, "diag.rs/JSON")
    t = ersetzen(t, *DIAG_ARGS, "diag.rs/Argumente")
    t = ersetzen(t, *DIAG_TEST, "diag.rs/Test")
    schreib(ziel, "face/src/diag.rs", t)

    t = lies(ziel, "face/src/hub.rs")
    t = ersetzen(t, *HUB_MELDEN, "hub.rs/Meldeweg")
    schreib(ziel, "face/src/hub.rs", t)

    t = lies(ziel, "face/src/control.rs")
    t = ersetzen(t, CONTROL_BEFEHLE[0], control_befehle or CONTROL_BEFEHLE[1],
                 "control.rs/Befehle")
    schreib(ziel, "face/src/control.rs", t)

    t = lies(ziel, "face/src/main.rs")
    t = ersetzen(t, *MAIN_ZWEIG, "main.rs/Steuerzweig")
    t = ersetzen(t, MAIN_METHODE_ANKER,
                 (main_methode or MAIN_METHODE) + MAIN_METHODE_ANKER,
                 "main.rs/Methode")
    schreib(ziel, "face/src/main.rs", t)


def notiz(ziel, text):
    with open(os.path.join(ziel, "mutation.txt"), "w", encoding="utf-8") as f:
        f.write(text)


# =============================================================================
# Mutant 1: ziel-aus-der-nachricht
# =============================================================================
# Der Hub nimmt den Unit-Namen aus der Nachricht, wenn der Schluessel nicht in
# der Allowlist steht. Genau die Bequemlichkeit, die den ganzen Task erledigt:
# ears und eyes funktionieren weiterhin, alle Kanarienvoegel sind gruen -- und
# das Face kann jede beliebige Unit des Nutzers stoppen.
z = baum("ziel-aus-der-nachricht")
grundlage(z, daemon_methode='''    def _wahrnehmung_aus(self, payload: dict) -> None:
        """MUTANT: faellt auf den Namen aus der NACHRICHT zurueck."""
        ziel = payload.get("ziel")
        if not isinstance(ziel, str) or not ziel:
            return
        unit = WAHRNEHMUNG_UNITS.get(ziel, ziel)
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=20, check=False)

''')
notiz(z, """MUTANT: der Hub nimmt den Unit-Namen aus der Nachricht.

`WAHRNEHMUNG_UNITS.get(ziel, ziel)` -- steht der Schluessel nicht in der
Allowlist, gilt er selbst als Unit-Name. Alles andere ist korrekt: die
Produzententabelle, der Diagnose-Vertrag, der Steuerbefehl, kein Popup, kein
Einschaltweg. `ziel=ears` und `ziel=eyes` wirken genau wie zugesagt.

Das ist der gefaehrlichste denkbare Fehler dieses Tasks und zugleich der
unauffaelligste: ein Verifizierer, der nur prueft "nach ears_aus ist die
ears-Attrappe aus", ist hier vollstaendig gruen. Das Face kann in dieser
Fassung `daimon-hub.service`, `daimon-auth.service` oder jede andere Unit des
Nutzers stoppen.

Gefangen wird er von Abschnitt 5b des Verifizierers: eine Kontroll-Unit, die
in keiner Allowlist steht, wird als `ziel` benannt und muss weiterlaufen --
und zwar zwischen zwei Kanarienvoegeln, die belegen, dass der Kanal in
demselben Lauf ueberhaupt funktioniert.
""")

# =============================================================================
# Mutant 2: einschalten-moeglich
# =============================================================================
z = baum("einschalten-moeglich")
grundlage(
    z,
    ipc_face='    "face": frozenset({"bubble_dismiss", "wahrnehmung_aus", "wahrnehmung_an"}),\n',
    daemon_methode='''    def _wahrnehmung_an(self, payload: dict) -> None:
        """MUTANT: es gibt doch ein Gegenstueck zum Einschalten."""
        ziel = payload.get("ziel")
        unit = WAHRNEHMUNG_UNITS.get(ziel) if isinstance(ziel, str) else None
        if unit is None:
            return
        subprocess.run(["systemctl", "--user", "start", unit],
                       capture_output=True, timeout=20, check=False)

    def _wahrnehmung_aus(self, payload: dict) -> None:
        ziel = payload.get("ziel")
        unit = WAHRNEHMUNG_UNITS.get(ziel) if isinstance(ziel, str) else None
        if unit is None:
            return
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=20, check=False)

''',
    control_befehle="""        "menu ears_an" => Some("menu:ears_an".to_owned()),
        "menu eyes_an" => Some("menu:eyes_an".to_owned()),
        "menu ears_aus" => Some("menu:ears_aus".to_owned()),
        "menu eyes_aus" => Some("menu:eyes_aus".to_owned()),
        "menu beenden" => Some("menu:beenden".to_owned()),
        "sichtbar an" => Some("sichtbar:true".to_owned()),
""",
)
# Der face-Zweig im Hub muss den neuen Typ auch bedienen, und der Steuerzweig
# im Face die neuen Befehle.
t = lies(z, "daimon/hub/daemon.py")
t = ersetzen(t, """                    if event.type == "wahrnehmung_aus":
                        self._wahrnehmung_aus(event.payload or {})
""", """                    if event.type == "wahrnehmung_aus":
                        self._wahrnehmung_aus(event.payload or {})
                    elif event.type == "wahrnehmung_an":
                        self._wahrnehmung_an(event.payload or {})
""", "daemon.py/face-Zweig (Mutant 2)")
schreib(z, "daimon/hub/daemon.py", t)

t = lies(z, "face/src/main.rs")
t = ersetzen(t, """                        "menu:ears_aus" => app.menu_aktion("ears_aus", Some("ears")),
""", """                        "menu:ears_an" => app.menu_einschalten("ears_an", "ears"),
                        "menu:eyes_an" => app.menu_einschalten("eyes_an", "eyes"),
                        "menu:ears_aus" => app.menu_aktion("ears_aus", Some("ears")),
""", "main.rs/Steuerzweig (Mutant 2)")
t = ersetzen(t, MAIN_METHODE_ANKER, """    /// MUTANT: der Weg zurueck, den es nicht geben darf.
    fn menu_einschalten(&mut self, name: &str, ziel: &str) {
        if let Some(hub) = &self.hub {
            if let Err(fehler) = hub.wahrnehmung_an_melden(ziel) {
                eprintln!("{fehler}");
            }
        }
        match self.diagnose.lock() {
            Ok(mut zustand) => {
                zustand.menu_aktionen += 1;
                zustand.letzte_menu_aktion = name.to_owned();
            }
            Err(vergiftet) => {
                let mut zustand = vergiftet.into_inner();
                zustand.menu_aktionen += 1;
                zustand.letzte_menu_aktion = name.to_owned();
            }
        }
    }

""" + MAIN_METHODE_ANKER, "main.rs/Methode (Mutant 2)")
schreib(z, "face/src/main.rs", t)

t = lies(z, "face/src/hub.rs")
t = ersetzen(t, """    /// T-2.7: Wahrnehmung abschalten.""", """    /// MUTANT: das Gegenstueck zum Einschalten.
    pub fn wahrnehmung_an_melden(&self, ziel: &str) -> Result<(), String> {
        let mut strom = UnixStream::connect(&self.melde_pfad)
            .map_err(|fehler| format!("Face-Meldeweg {}: {fehler}", self.melde_pfad.display()))?;
        let zeile = format!(
            "{{\\"v\\":1,\\"type\\":\\"wahrnehmung_an\\",\\"payload\\":{{\\"ziel\\":\\"{ziel}\\"}}}}\\n"
        );
        strom
            .write_all(zeile.as_bytes())
            .map_err(|fehler| format!("wahrnehmung_an senden: {fehler}"))
    }

    /// T-2.7: Wahrnehmung abschalten.""", "hub.rs/Meldeweg (Mutant 2)")
schreib(z, "face/src/hub.rs", t)
notiz(z, """MUTANT: es gibt doch einen Weg zum Einschalten.

`wahrnehmung_an` steht in der Produzententabelle des Face, der Hub startet die
Unit damit wieder, und das Menue bekommt `menu ears_an` / `menu eyes_an` als
wirksame Eintraege -- statt als dargestellte, aber deaktivierte.

Die Allowlist bleibt dabei streng: eingeschaltet werden koennen nur dieselben
zwei Units, die auch abgeschaltet werden koennen. Der Mutant besteht Abschnitt
5 des Verifizierers deshalb vollstaendig -- sein Durchfallen ist eine Aussage
ueber die Einseitigkeit und ueber nichts anderes.

Warum das ein Fehler ist und nicht bloss ein fehlendes Feature: ein Overlay,
das Wahrnehmung nur abschalten kann, ist fail-safe -- im schlimmsten Fall
sieht und hoert dAImon nichts mehr. Ein Overlay, das sie wieder einschalten
kann, kann Mikrofon und Bildschirmzugriff ohne den Auth-Agenten reaktivieren.
Einschalten gehoert nach der Entscheidung von Matthias zum Auth-Agenten und
existiert in P2 nicht.

Gefangen von: Abschnitt 1 (die face-Menge ist nicht mehr genau zwei Typen),
Abschnitt 7 (eine abgeschaltete Attrappe laeuft nach den Einschaltversuchen
wieder) und Abschnitt 8c (`menu ears_an` zaehlt menu_aktionen hoch, obwohl der
Eintrag deaktiviert sein muesste).
""")

# =============================================================================
# Mutant 3: popup-per-steuerbefehl
# =============================================================================
z = baum("popup-per-steuerbefehl")
grundlage(z, main_methode="""    /// MUTANT: der Steuerbefehl zieht das Popup mit auf.
    fn menu_aktion(&mut self, name: &str, ziel: Option<&str>) {
        if let (Some(ziel), Some(hub)) = (ziel, &self.hub) {
            if let Err(fehler) = hub.wahrnehmung_aus_melden(ziel) {
                eprintln!("{fehler}");
            }
        }
        match self.diagnose.lock() {
            Ok(mut zustand) => {
                zustand.menu_offen = true;
                zustand.menu_aktionen += 1;
                zustand.letzte_menu_aktion = name.to_owned();
            }
            Err(vergiftet) => {
                let mut zustand = vergiftet.into_inner();
                zustand.menu_offen = true;
                zustand.menu_aktionen += 1;
                zustand.letzte_menu_aktion = name.to_owned();
            }
        }
        if ziel.is_none() {
            self.beendet = true;
        }
    }

""")
notiz(z, """MUTANT: der Steuerbefehl oeffnet das Popup.

`menu ears_aus` setzt `menu_offen` auf true. Alles andere ist korrekt.

Warum das ein Fehler ist: das Popup entsteht ausschliesslich aus einem echten
Rechtsklick. Ein Steuerbefehl, der es aufziehen kann, ist ein Klickfaenger --
er naehme ueber den Grab Tastatur und Zeiger an sich, ohne dass ein Mensch
geklickt hat, und der naechste Klick des Nutzers landete auf einem Menue, das
er nicht geoeffnet hat. Und der Steuer-Socket ist bewusst ein einfacher
Testkanal ohne Peer-Pruefung.

EHRLICHE EINSCHRAENKUNG: dieser Mutant setzt nur das Diagnose-Feld; er sendet
kein echtes `get_popup` ueber die Wayland-Leitung. Ein echtes xdg_popup mit
Grab in einem Mutantenbaum aufzuziehen, waere auf dieser Maschine
leichtsinnig -- ein haengender Grab macht die Maus unbedienbar, am 27.07. real
passiert. Damit ist die Mitschnitt-Pruefung des Verifizierers
("kein get_popup im Protokoll") durch KEINEN Mutanten belegt; belegt ist nur
die Diagnose-Pruefung ("menu_offen bleibt false"). Wer das schliessen will,
braucht einen Mutanten mit echtem Popup und eine Maschine, auf der ein
haengender Grab folgenlos bleibt.

Gefangen von: Abschnitt 8b (menu_offen wird ueber 2 s abgetastet und muss
durchgehend false sein).
""")

print("erzeugt")
PYEOF
[[ $? -eq 0 ]] || { echo "Mutation fehlgeschlagen"; exit 1; }

# --- Gegenprobe --------------------------------------------------------------
# Ein abgebrochenes Erzeugungsskript hat schon einmal eine unveraenderte Kopie
# hinterlassen, und eine Fixture-Kopie mit fremdem target/ hat schon einmal
# einen Mutanten gruen gemeldet. Beides wird hier ausgeschlossen.
rc=0
MUTANTEN=(ziel-aus-der-nachricht einschalten-moeglich popup-per-steuerbefehl)
for m in "${MUTANTEN[@]}"; do
  n_rs="$(diff -r "$BASIS/face/src" "$SCRIPT_DIR/$m/face/src" | grep -c '^[<>]')"
  n_py="$(diff -r "$BASIS/daimon" "$SCRIPT_DIR/$m/daimon" | grep -c '^[<>]')"
  echo "$m: $n_rs geaenderte Rust-Zeilen, $n_py geaenderte Python-Zeilen gegenueber $BASIS_COMMIT"
  if [[ "$n_rs" -lt 3 || "$n_py" -lt 3 ]]; then
    echo "  FEHLER: $m ist praktisch eine unveraenderte Kopie"; rc=1
  fi
  if [[ -e "$SCRIPT_DIR/$m/face/target" ]]; then
    echo "  FEHLER: $m enthaelt ein target/ -- das schleppt fremde Binaries mit"; rc=1
  fi
  if ! python3 -m compileall -q "$SCRIPT_DIR/$m/daimon" >/dev/null; then
    echo "  FEHLER: $m/daimon ist syntaktisch kaputt"; rc=1
  fi
  find "$SCRIPT_DIR/$m/daimon" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
  bd="$(mktemp -d)"
  ( cd "$SCRIPT_DIR/$m/face" && CARGO_TARGET_DIR="$bd" timeout 900 cargo build -p face ) >"$bd/log" 2>&1
  if [[ $? -ne 0 ]]; then echo "  FEHLER: $m baut nicht"; tail -25 "$bd/log"; rc=1
  else echo "  baut durch"; fi
  rm -rf -- "$bd"
done

# Und: die Mutanten muessen sich voneinander unterscheiden. Bei T-2.4 waren
# zwei Baeume versehentlich identisch, und beide meldeten dasselbe.
for a in 0 1; do
  for b in 1 2; do
    [[ "$a" -lt "$b" ]] || continue
    ma="${MUTANTEN[$a]}"; mb="${MUTANTEN[$b]}"
    if diff -rq "$SCRIPT_DIR/$ma" "$SCRIPT_DIR/$mb" >/dev/null 2>&1; then
      echo "FEHLER: $ma und $mb sind identisch"; rc=1
    fi
  done
done
exit $rc
