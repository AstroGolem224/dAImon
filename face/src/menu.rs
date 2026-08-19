//! T-2.7 — Kontextmenue am Pet.
//!
//! # Drei Grenzen, die dieses Modul haelt
//!
//! **1. Das Popup entsteht ausschliesslich aus einem echten Rechtsklick.**
//! `oeffnen` verlangt das `serial` eines tatsaechlichen Pointer-Press. Der
//! Steuer-Socket kann die *Aktionen* ausloesen (`menu ears_aus`), aber nie
//! dieses Popup: ein Popup mit Grab nimmt Zeiger und Tastatur an sich, und ein
//! Steuerkanal, der das kann, waere ein Klickfaenger.
//!
//! **2. Es gibt kein Einschalten.** Keine Aktion startet einen Prozess oder
//! eine Unit. Die Eintraege "Ohren an" und "Augen an" stehen sichtbar und
//! ausgegraut im Menue, ohne Aktion dahinter. Ein Overlay, das Wahrnehmung nur
//! abschalten kann, ist fail-safe; Einschalten gehoert zum Auth-Agenten und
//! existiert in P2 nicht.
//!
//! **Die Personaauswahl ist die eine Ausnahme -- und sie schaltet nichts.**
//! `Aktion::Persona` schreibt einen Namen in
//! `~/.local/state/daimon/persona.json` und sonst nichts: kein Unit-Start,
//! kein Reload, kein Signal. Wirksam wird die Wahl beim naechsten Start des
//! Mind, und genau das sagt die Blase auch.
//! Wollte man den Wechsel sofort wirksam machen, waere das ein Neustart einer
//! Unit aus dem Overlay heraus -- die Faehigkeit, die dieses Modul gerade
//! nicht haben soll.
//!
//! **3. Die Input-Region der Popup-Surface steht vor ihrem ersten Commit.**
//! Dieselbe Regel wie in `input.rs`, aus demselben Grund. Deshalb liegt hier
//! ein eigenes `InputRegion`-Gate, obwohl sonst alle Commits in `surface.rs`
//! liegen: das Popup ist eine eigene Surface mit eigener Lebensdauer.

use std::{
    path::{Path, PathBuf},
    sync::OnceLock,
};

use fontdue::{
    layout::{CoordinateSystem, Layout, LayoutSettings, TextStyle},
    Font,
};
use smithay_client_toolkit::{
    compositor::{CompositorState, Surface},
    error::GlobalError,
    globals::{GlobalData, ProvidesBoundGlobal},
    reexports::protocols::xdg::shell::client::{
        xdg_positioner::{Anchor, ConstraintAdjustment, Gravity},
        xdg_wm_base,
    },
    shell::{
        wlr_layer::LayerSurface,
        xdg::{popup::Popup, XdgPositioner},
    },
    shm::slot::SlotPool,
};
use wayland_client::{
    globals::{BindError, GlobalList},
    protocol::{wl_seat, wl_shm, wl_surface},
    Dispatch, QueueHandle,
};

use crate::{
    bubble::{glyph_pixel, premultipliziert, ueberblenden, Raster},
    input::{Box2D, InputRegion},
};

/// Was ein Menueeintrag ausloesen kann. Bewusst knapp: jeder weitere Wert
/// waere eine neue Faehigkeit des Overlays.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Aktion {
    EarsAus,
    EyesAus,
    /// T-5.2: den Bildschirmzugriff widerrufen. Schreibt eine Marke nach
    /// `~/.local/state/daimon/` und **sendet nichts** -- wie die
    /// Personaauswahl. Der Weg ueber den Hub schiede aus: das Face darf
    /// hoechstens `bubble_dismiss` und `wahrnehmung_aus` senden (T-1.7.v4),
    /// und ein dritter Nachrichtentyp haette T-2.7 rot gemacht, das
    /// `PRODUZENTEN["face"]` als exakte Menge prueft.
    BildschirmWiderrufen,
    /// T-7.4: Mitschnitt pausieren, auf Zeit. Bild UND Ton.
    ///
    /// Der einzige Menuepunkt, der den TONPFAD erreicht: die
    /// Anwendungs-Denylist sperrt Fenster, und ein gesprochener Satz hat
    /// keines. `redaktion.urteil_ton` prueft genau einen Zustand, und das
    /// ist dieser -- nur konnte ihn bis zum 19.08. niemand einschalten.
    ///
    /// Kein Gegenstueck zum Ausschalten, und das ist kein Vergessen: der
    /// Modus laeuft von selbst ab. Ein Overlay, das ihn beenden koennte,
    /// koennte den Mitschnitt wieder anschalten -- die falsche Richtung.
    Privatmodus,
    Beenden,
    /// Index in [`personas()`]. Der Index statt des Namens, damit `Aktion`
    /// `Copy` bleibt -- und weil ein Index nur dann existiert, wenn die Datei
    /// beim Start wirklich gefunden wurde. Ein Name als Nutzlast waere ein
    /// Feld, in das der Steuer-Socket schreiben koennte.
    Persona(usize),
}

impl Aktion {
    /// Der Name, der in `letzte_menu_aktion` landet und den der Steuer-Socket
    /// annimmt. Eine Quelle, damit beide nicht auseinanderlaufen koennen.
    ///
    /// Fuer die Persona lautet er `persona:<dateiname>`. ABSICHTLICH nicht
    /// `persona`, `persona_wechseln` oder `persona naechste`: der eingefrorene
    /// Pruefstand T-2.7 (Abschnitt 8c) verlangt, dass genau diese drei Befehle
    /// wirkungslos bleiben. Sie bleiben es.
    pub fn name(self) -> String {
        match self {
            Self::EarsAus => "ears_aus".to_owned(),
            Self::EyesAus => "eyes_aus".to_owned(),
            // ABSICHTLICH nicht "eyes_aus": das stoppt die Unit. Der Widerruf
            // nimmt die Portal-Erlaubnis zurueck, was etwas anderes ist --
            // wer nur die Unit stoppt, laesst den Token liegen.
            Self::BildschirmWiderrufen => "bildschirm_widerrufen".to_owned(),
            Self::Privatmodus => "privatmodus".to_owned(),
            Self::Beenden => "beenden".to_owned(),
            Self::Persona(index) => match personas().get(index) {
                Some(p) => format!("persona:{}", p.datei),
                // Unerreichbar, solange der Index aus `personas()` stammt.
                None => "persona:?".to_owned(),
            },
        }
    }

    pub fn aus_name(name: &str) -> Option<Self> {
        match name {
            "ears_aus" => Some(Self::EarsAus),
            "eyes_aus" => Some(Self::EyesAus),
            "bildschirm_widerrufen" => Some(Self::BildschirmWiderrufen),
            "privatmodus" => Some(Self::Privatmodus),
            "beenden" => Some(Self::Beenden),
            _ => {
                let datei = name.strip_prefix("persona:")?;
                personas()
                    .iter()
                    .position(|p| p.datei == datei)
                    .map(Self::Persona)
            }
        }
    }

    /// Der Schluessel fuer `wahrnehmung_aus`. **Kein Unit-Name** -- welche
    /// Unit dahinter steht, entscheidet allein die Konfiguration des Hubs.
    /// Stuende der Name hier, koennte das Overlay den Hub selbst, den
    /// Auth-Agenten oder jede Unit des Nutzers stoppen.
    ///
    /// **`None` heisst nicht
    /// "beenden"** -- es heisst nur, dass diese Aktion keine Wahrnehmung
    /// abschaltet. Wer hier auf `None` hin beendet, beendet seit der
    /// Personaauswahl auch bei einem Personaklick.
    pub fn ziel(self) -> Option<&'static str> {
        match self {
            Self::EarsAus => Some("ears"),
            Self::EyesAus => Some("eyes"),
            // Kein Ziel: der Widerruf schaltet KEINE Unit ab. Stuende hier
            // "eyes", wuerde ein Klick die Wahrnehmung stoppen und die
            // Erlaubnis behalten -- genau andersherum als gemeint.
            Self::Beenden
            | Self::Persona(_)
            | Self::BildschirmWiderrufen
            // Kein Ziel: der Privatmodus schaltet KEINE Unit ab. Er
            // pausiert die ABLAGE, das Mikrofon bleibt an -- wer nur
            // still sein will, soll nicht die Ohren verlieren.
            | Self::Privatmodus => None,
        }
    }
}

pub struct Eintrag {
    pub text: String,
    /// `None` heisst: sichtbar, aber deaktiviert. Nicht versteckt -- ein
    /// Menue, das spaeter Eintraege dazubekommt, verwirrt mehr als eines, das
    /// zeigt, was kommt.
    pub aktion: Option<Aktion>,
}

fn fest(text: &str, aktion: Option<Aktion>) -> Eintrag {
    Eintrag {
        text: text.to_owned(),
        aktion,
    }
}

/// Die Eintraege, wie sie gerade im Menue stehen.
///
/// Neu berechnet statt konstant, weil die aktive Persona darin markiert ist
/// und sich zwischen zwei Oeffnungen geaendert haben kann. Die ANZAHL steht
/// dagegen ab dem ersten Aufruf fest -- `personas()` scannt genau einmal.
/// Waere sie nicht stabil, waere die beim Oeffnen berechnete Popup-Hoehe eine
/// andere als die beim Zeichnen.
pub fn eintraege() -> Vec<Eintrag> {
    let aktiv = aktive_persona();
    let mut liste = vec![
        // Kein Einschalten in P2: das gehoert zum Auth-Agenten.
        fest("Ohren an", None),
        fest("Ohren aus", Some(Aktion::EarsAus)),
        fest("Augen an", None),
        fest("Augen aus", Some(Aktion::EyesAus)),
        // Ueberschrift der Auswahl, selbst ohne Aktion.
        fest("Persona wechseln", None),
    ];
    if personas().is_empty() {
        liste.push(fest("   (keine gefunden)", None));
    }
    for (index, persona) in personas().iter().enumerate() {
        // Der Vergleich ist gross-/kleinschreibungsblind, weil der Lader die
        // Datei ueber `name.lower()` sucht: `name = "NORDOM"` in der
        // Konfiguration meint dieselbe Datei wie `Nordom`.
        let ist_aktiv = persona.datei.eq_ignore_ascii_case(&aktiv);
        liste.push(Eintrag {
            text: format!(
                "   {} {}",
                if ist_aktiv { "●" } else { "○" },
                persona.anzeige
            ),
            // Die aktive Persona traegt keine Aktion: sie ist schon gewaehlt,
            // und ein Klick, der die Datei unveraendert neu schreibt, waere
            // eine Wirkung, die nur so aussieht.
            aktion: (!ist_aktiv).then_some(Aktion::Persona(index)),
        });
    }
    // Ans Ende, nicht zwischen die Wahrnehmungseintraege: der Widerruf ist
    // die seltenste und folgenreichste Aktion im Menue, und die Positionen
    // der ersten fuenf Eintraege sind in den Tests unten festgeschrieben.
    // Vor dem Widerruf und nach der Persona-Auswahl: die Positionen der
    // ersten fuenf Eintraege sind in den Tests unten festgeschrieben, und die
    // Zahl der Personas ist es nicht -- ans Ende gehoert also, was neu ist.
    liste.push(fest("Mitschnitt pausieren (15 min)", Some(Aktion::Privatmodus)));
    liste.push(fest(
        "Bildschirmzugriff widerrufen",
        Some(Aktion::BildschirmWiderrufen),
    ));
    liste.push(fest("Beenden", Some(Aktion::Beenden)));
    liste
}

// -- Personas: finden, lesen, schreiben ------------------------------------

/// Eine gefundene Persona-Datei. `datei` ist der Dateiname ohne `.toml` und
/// damit genau das, was `persona.name` in der Konfiguration tragen muss;
/// `anzeige` ist nur fuer das Auge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PersonaDatei {
    pub datei: String,
    pub anzeige: String,
}

static PERSONAS: OnceLock<Vec<PersonaDatei>> = OnceLock::new();

fn xdg(variable: &str, unter: &str) -> PathBuf {
    match std::env::var_os(variable) {
        Some(wert) if !wert.is_empty() => PathBuf::from(wert),
        _ => match std::env::var_os("HOME") {
            Some(home) => PathBuf::from(home).join(unter),
            None => PathBuf::from(unter),
        },
    }
    .join("daimon")
}

fn config_dir() -> PathBuf {
    xdg("XDG_CONFIG_HOME", ".config")
}

/// Wohin die Wahl geschrieben wird.
///
/// **Nicht** nach `~/.config/daimon/daimon.toml`, und das ist keine Bequemlichkeit:
/// `daimon-face.service` traegt `ProtectHome=read-only` und gibt nur
/// `%t/daimon` und `%h/.local/state/daimon` frei. Ein Schreibrecht auf
/// `~/.config/daimon` haette dem Overlay ein Verzeichnis geoeffnet, in dem
/// laut `docs/TOKEN-ROTATION.md` der `anthropic-token` liegt. Der Zustand ist
/// ohnehin der ehrlichere Ort: die Konfiguration sagt, was eingestellt IST,
/// die Auswahl sagt, was zuletzt GEWAEHLT wurde.
fn state_dir() -> PathBuf {
    xdg("XDG_STATE_HOME", ".local/state")
}

/// Die Datei, die der Mind beim Start vor der Konfiguration liest.
pub const AUSWAHL_DATEI: &str = "persona.json";

/// T-5.2: die Marke fuer den widerrufenen Bildschirmzugriff.
pub const WIDERRUF_DATEI: &str = "screencast-widerruf";

/// Dieselbe Reihenfolge wie im Python-Lader (`daimon/mind/persona.py`):
/// erst die eigene Datei unter XDG, dann die mitgelieferte. Wer eine eigene
/// `nordom.toml` anlegt, sieht im Menue seine, nicht die aus dem Repo.
///
/// Die beiden relativen Pfade sind kein Versehen: `daimon-face.service` setzt
/// `WorkingDirectory=` auf **das Crate-Verzeichnis** (`<repo>/face`), ein
/// Aufruf aus der Repowurzel steht dagegen eine Ebene hoeher. Nur einen von
/// beiden zu nennen hiesse, dass die Auswahl in genau einem der beiden
/// Betriebswege leer bleibt -- und ein leeres Menue sieht aus wie "es gibt
/// keine Personas", nicht wie "hier stimmt ein Pfad nicht".
///
/// ponytail: relative Pfade. Ein absoluter gehoert in die Konfiguration,
/// sobald es einen dritten Betriebsweg gibt.
fn persona_verzeichnisse() -> Vec<PathBuf> {
    vec![
        config_dir().join("persona"),
        PathBuf::from("config/persona"),
        PathBuf::from("../config/persona"),
    ]
}

/// Einmal gescannt, dann fest. Ein Menue, dessen Zeilenzahl sich zwischen
/// Oeffnen und Zeichnen aendert, waere ein Popup mit falscher Hoehe.
pub fn personas() -> &'static [PersonaDatei] {
    PERSONAS.get_or_init(|| personas_aus(&persona_verzeichnisse()))
}

/// Der Scan selbst, ohne `OnceLock` -- damit ihn ein Test gegen ein eigenes
/// Verzeichnis fahren kann. Ein Scan, den nur der Prozessstart aufruft, ist
/// ein Scan, den niemand prueft.
fn personas_aus(verzeichnisse: &[PathBuf]) -> Vec<PersonaDatei> {
    let mut gefunden: Vec<PersonaDatei> = Vec::new();
    for verzeichnis in verzeichnisse {
        let Ok(eintraege) = std::fs::read_dir(verzeichnis) else {
            continue;
        };
        for eintrag in eintraege.flatten() {
            let pfad = eintrag.path();
            if pfad.extension().and_then(|e| e.to_str()) != Some("toml") {
                continue;
            }
            let Some(datei) = pfad.file_stem().and_then(|s| s.to_str()) else {
                continue;
            };
            if !ist_zulaessiger_dateiname(datei) {
                continue;
            }
            if gefunden.iter().any(|p| p.datei.eq_ignore_ascii_case(datei)) {
                continue; // Die XDG-Datei gewinnt, wie beim Lader.
            }
            gefunden.push(PersonaDatei {
                anzeige: anzeigename(&pfad, datei),
                datei: datei.to_owned(),
            });
        }
    }
    gefunden.sort_by(|a, b| a.datei.cmp(&b.datei));
    gefunden
}

/// Nur das, was auch ein Dateiname sein darf und was in `daimon.toml` ohne
/// Maskierung wieder herauskommt. Ein Punkt, ein Anfuehrungszeichen oder ein
/// Zeilenumbruch im Namen waere eine Konfigurationsdatei, die der naechste
/// Start nicht mehr lesen kann.
fn ist_zulaessiger_dateiname(datei: &str) -> bool {
    !datei.is_empty()
        && datei.len() <= 64
        && datei
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// Der Name aus der Datei (`name = "Nordom"`), sonst der Dateiname mit grossem
/// Anfangsbuchstaben. Gelesen wird nur diese eine Zeile -- das Face hat keinen
/// TOML-Parser und braucht fuer eine Beschriftung auch keinen.
fn anzeigename(pfad: &Path, datei: &str) -> String {
    if let Ok(inhalt) = std::fs::read_to_string(pfad) {
        if let Some(name) = erster_namenswert(&inhalt) {
            if !name.is_empty() && name.chars().count() <= 32 {
                return name;
            }
        }
    }
    let mut zeichen = datei.chars();
    match zeichen.next() {
        Some(erstes) => erstes.to_uppercase().collect::<String>() + zeichen.as_str(),
        None => datei.to_owned(),
    }
}

/// `name = "..."` in der obersten Ebene einer Persona-Datei.
fn erster_namenswert(inhalt: &str) -> Option<String> {
    for zeile in inhalt.lines() {
        let zeile = zeile.trim();
        if zeile.starts_with('[') {
            break; // ab hier ist es eine Untertabelle
        }
        if ist_namenszeile(zeile) {
            let wert = zeile.split_once('=')?.1.trim();
            return Some(wert.trim_matches('"').trim().to_owned());
        }
    }
    None
}

fn ist_namenszeile(zeile: &str) -> bool {
    zeile
        .strip_prefix("name")
        .is_some_and(|rest| rest.trim_start().starts_with('='))
}

/// Welche Persona der naechste Start des Mind nehmen wird.
///
/// Dieselbe Reihenfolge, die auch der Lader in `daimon/mind/persona.py`
/// anwendet: die getroffene Auswahl im Zustand, sonst `persona.name` aus der
/// Konfiguration, sonst `Ember` -- **dieselbe** Vorgabe wie in
/// `daimon/common/config.py`. Zwei Vorgaben waeren zwei Wahrheiten, und das
/// Menue wuerde den Punkt an eine Persona malen, die gar nicht laeuft.
pub fn aktive_persona() -> String {
    if let Ok(inhalt) = std::fs::read_to_string(state_dir().join(AUSWAHL_DATEI)) {
        if let Some(name) = auswahl_lesen(&inhalt) {
            return name;
        }
    }
    let Ok(inhalt) = std::fs::read_to_string(config_dir().join("daimon.toml")) else {
        return "Ember".to_owned();
    };
    persona_name_lesen(&inhalt).unwrap_or_else(|| "Ember".to_owned())
}

/// `{"name": "..."}` -- genau ein Feld, und deshalb kein JSON-Parser.
/// Geschrieben hat die Datei dieses Modul; wer sie von Hand aendert, bekommt
/// bei einem anderen Aufbau `None` und damit den Wert aus der Konfiguration.
fn auswahl_lesen(inhalt: &str) -> Option<String> {
    let rest = inhalt.split_once("\"name\"")?.1;
    let rest = rest.trim_start().strip_prefix(':')?.trim_start();
    let wert: String = rest.strip_prefix('"')?.chars().take_while(|c| *c != '"').collect();
    ist_zulaessiger_dateiname(&wert).then_some(wert)
}

/// `name` aus dem Abschnitt `[persona]`.
pub fn persona_name_lesen(inhalt: &str) -> Option<String> {
    let mut im_abschnitt = false;
    for zeile in inhalt.lines() {
        let zeile = zeile.trim();
        if zeile.starts_with('#') {
            continue;
        }
        if zeile.starts_with('[') {
            im_abschnitt = zeile.starts_with("[persona]");
            continue;
        }
        if im_abschnitt && ist_namenszeile(zeile) {
            let wert = zeile.split_once('=')?.1.trim();
            return Some(wert.trim_matches('"').trim().to_owned());
        }
    }
    None
}

/// T-5.2: vermerkt den Widerruf des Bildschirmzugriffs.
///
/// Schreibt eine leere Marke nach `~/.local/state/daimon/screencast-widerruf`
/// und gibt ihren Pfad zurueck. Sie SAGT den Widerruf, sie vollzieht ihn
/// nicht: die Tokendatei liegt unter `$XDG_CONFIG_HOME/daimon/`, wo das Face
/// wegen `ProtectHome=read-only` nichts zu suchen hat -- und dort liegt auch
/// der `anthropic-token`. Wer dem Overlay dieses Verzeichnis oeffnete, gaebe
/// ihm Zugriff auf beides.
///
/// Wie bei der Persona ueber eine Nachbardatei mit `rename`: eine halbe Marke
/// waere schlimmer als keine, weil sie den Widerruf behaupten wuerde.
pub fn widerruf_vermerken() -> Result<String, String> {
    let verzeichnis = state_dir();
    std::fs::create_dir_all(&verzeichnis)
        .map_err(|fehler| format!("{}: {fehler}", verzeichnis.display()))?;
    let pfad = verzeichnis.join(WIDERRUF_DATEI);
    let vorlaeufig = verzeichnis.join(format!("{WIDERRUF_DATEI}.neu"));
    std::fs::write(&vorlaeufig, b"")
        .map_err(|fehler| format!("{}: {fehler}", vorlaeufig.display()))?;
    std::fs::rename(&vorlaeufig, &pfad)
        .map_err(|fehler| format!("{}: {fehler}", pfad.display()))?;
    Ok(pfad.display().to_string())
}

/// Schreibt die Wahl nach `~/.local/state/daimon/persona.json` und gibt den
/// Anzeigenamen zurueck. Schaltet nichts, startet nichts, sendet nichts.
///
/// Geschrieben wird ueber eine Nachbardatei mit anschliessendem `rename`:
/// ein abgebrochener Schreibvorgang darf keine halbe Datei hinterlassen, die
/// der naechste Start nicht mehr lesen kann.
///
/// Der Name kommt aus [`personas()`] und ist damit ein Dateiname, den dieses
/// Modul selbst gefunden und gegen [`ist_zulaessiger_dateiname`] gehalten hat.
/// Die zweite Pruefung hier ist trotzdem keine Doppelung: sie ist die Stelle,
/// an der ein Anfuehrungszeichen im Namen die JSON-Datei aufbrechen wuerde.
pub fn persona_setzen(index: usize) -> Result<String, String> {
    let persona = personas()
        .get(index)
        .ok_or_else(|| format!("Persona-Index {index} gibt es nicht"))?;
    if !ist_zulaessiger_dateiname(&persona.datei) {
        return Err(format!(
            "Persona-Name {:?} ist nicht zulaessig",
            persona.datei
        ));
    }
    let verzeichnis = state_dir();
    std::fs::create_dir_all(&verzeichnis)
        .map_err(|fehler| format!("{}: {fehler}", verzeichnis.display()))?;
    let pfad = verzeichnis.join(AUSWAHL_DATEI);
    let vorlaeufig = verzeichnis.join(format!("{AUSWAHL_DATEI}.neu"));
    let inhalt = format!("{{\"v\": 1, \"name\": \"{}\"}}\n", persona.datei);
    std::fs::write(&vorlaeufig, inhalt.as_bytes())
        .map_err(|fehler| format!("{}: {fehler}", vorlaeufig.display()))?;
    std::fs::rename(&vorlaeufig, &pfad).map_err(|fehler| {
        let _ = std::fs::remove_file(&vorlaeufig);
        format!("{}: {fehler}", pfad.display())
    })?;
    Ok(persona.anzeige.clone())
}

pub const BREITE: i32 = 208;
const ZEILE_H: i32 = 30;
const RAND: i32 = 6;
const TEXT_X: i32 = 14;
const SCHRIFTGROESSE: f32 = 16.0;
/// Deckung des Textes. Der deaktivierte Wert ist so gewaehlt, dass der
/// Eintrag lesbar bleibt und trotzdem eindeutig blasser ist.
const ALPHA_AKTIV: u8 = 255;
const ALPHA_DEAKTIVIERT: u8 = 80;

pub fn hoehe() -> i32 {
    2 * RAND + eintraege().len() as i32 * ZEILE_H
}

/// Zeilenindex zu einem Klick in Popup-Koordinaten.
pub fn index_bei(x: f64, y: f64) -> Option<usize> {
    if x < 0.0 || y < f64::from(RAND) || x >= f64::from(BREITE) {
        return None;
    }
    let index = ((y - f64::from(RAND)) / f64::from(ZEILE_H)) as usize;
    (index < eintraege().len()).then_some(index)
}

/// Die Aktion unter dem Zeiger. `None` fuer deaktivierte Eintraege und fuer
/// alles ausserhalb -- ein deaktivierter Eintrag darf nicht versehentlich auf
/// den Nachbarn durchschlagen.
pub fn aktion_bei(x: f64, y: f64) -> Option<Aktion> {
    let liste = eintraege();
    liste[index_bei(x, y)?].aktion
}

pub fn rendern(font: &Font) -> Raster {
    let liste = eintraege();
    let hoehe = 2 * RAND + liste.len() as i32 * ZEILE_H;
    let mut pixel = vec![0u8; (BREITE * hoehe * 4) as usize];
    let hintergrund = premultipliziert(24, 27, 34, 242);
    for p in pixel.chunks_exact_mut(4) {
        p.copy_from_slice(&hintergrund);
    }
    for (index, eintrag) in liste.iter().enumerate() {
        let alpha = if eintrag.aktion.is_some() {
            ALPHA_AKTIV
        } else {
            ALPHA_DEAKTIVIERT
        };
        let y = RAND + index as i32 * ZEILE_H + 4;
        zeile_zeichnen(font, &mut pixel, hoehe, &eintrag.text, y, alpha);
    }
    Raster {
        breite: BREITE,
        hoehe,
        pixel,
        zeilen: liste.len(),
    }
}

fn zeile_zeichnen(font: &Font, pixel: &mut [u8], hoehe: i32, text: &str, y: i32, alpha: u8) {
    let mut layout = Layout::new(CoordinateSystem::PositiveYDown);
    layout.reset(&LayoutSettings {
        x: TEXT_X as f32,
        y: y as f32,
        ..LayoutSettings::default()
    });
    layout.append(&[font], &TextStyle::new(text, SCHRIFTGROESSE, 0));
    for glyph in layout.glyphs() {
        let (_, deckung) = font.rasterize_config(glyph.key);
        for gy in 0..glyph.height {
            for gx in 0..glyph.width {
                let x = glyph.x.floor() as i32 + gx as i32;
                let y = glyph.y.floor() as i32 + gy as i32;
                if x < 0 || y < 0 || x >= BREITE || y >= hoehe {
                    continue;
                }
                let quelle = glyph_pixel(deckung[gy * glyph.width + gx], alpha);
                let index = ((y * BREITE + x) * 4) as usize;
                ueberblenden(&mut pixel[index..index + 4], quelle);
            }
        }
    }
}

/// Nur `xdg_wm_base`, bewusst nicht `XdgShell`.
///
/// `XdgShell::bind` bindet zusaetzlich `zxdg_decoration_manager_v1` und
/// verlangt dafuer einen `WindowHandler`. Das Face hat keine Fenster, soll
/// keine bekommen, und ein leerer Handler waere genau die Attrappe, die
/// spaeter jemand fuellt.
pub struct PopupShell(xdg_wm_base::XdgWmBase);

impl PopupShell {
    pub fn binden<State>(globals: &GlobalList, qh: &QueueHandle<State>) -> Result<Self, BindError>
    where
        State: Dispatch<xdg_wm_base::XdgWmBase, GlobalData> + 'static,
    {
        // Version 3 reicht fuer Popup, Grab und Constraint-Adjustment.
        Ok(Self(globals.bind(qh, 1..=3, GlobalData)?))
    }
}

// Beide Kompatibilitaetsstufen, weil `XdgPositioner::new` gegen 6 und
// `Popup::from_surface` gegen 5 gebunden ist.
impl ProvidesBoundGlobal<xdg_wm_base::XdgWmBase, 5> for PopupShell {
    fn bound_global(&self) -> Result<xdg_wm_base::XdgWmBase, GlobalError> {
        Ok(self.0.clone())
    }
}

impl ProvidesBoundGlobal<xdg_wm_base::XdgWmBase, 6> for PopupShell {
    fn bound_global(&self) -> Result<xdg_wm_base::XdgWmBase, GlobalError> {
        Ok(self.0.clone())
    }
}

/// Haelt das offene Popup. Kein Popup = kein Menue; einen zweiten Zustand
/// gibt es nicht.
pub struct Menu {
    popup: Option<Popup>,
    input_region: InputRegion,
}

impl Menu {
    pub fn neu() -> Self {
        Self {
            popup: None,
            input_region: InputRegion::new(),
        }
    }

    pub fn ist_menu_surface(&self, surface: &wl_surface::WlSurface) -> bool {
        self.popup
            .as_ref()
            .is_some_and(|popup| popup.wl_surface() == surface)
    }

    pub fn ist_popup(&self, popup: &Popup) -> bool {
        self.popup.as_ref() == Some(popup)
    }

    /// Gibt zurueck, ob wirklich etwas geschlossen wurde. Drop zerstoert
    /// xdg_popup, xdg_surface und wl_surface in dieser Reihenfolge.
    pub fn schliessen(&mut self) -> bool {
        self.popup.take().is_some()
    }

    /// `serial` stammt aus dem Rechtsklick-Press. Ein erfundenes Serial wuerde
    /// der Compositor mit `popup_done` beantworten -- die richtige
    /// Fehlerrichtung, aber wir erfinden hier ohnehin keines.
    #[allow(clippy::too_many_arguments)]
    pub fn oeffnen(
        &mut self,
        compositor: &CompositorState,
        xdg_shell: &PopupShell,
        layer: &LayerSurface,
        qh: &QueueHandle<crate::App>,
        seat: &wl_seat::WlSeat,
        serial: u32,
        anker: (i32, i32),
    ) -> Result<(), String> {
        if self.popup.is_some() {
            return Ok(());
        }
        let hoehe = hoehe();
        let positioner =
            XdgPositioner::new(xdg_shell).map_err(|fehler| format!("xdg_positioner: {fehler}"))?;
        positioner.set_size(BREITE, hoehe);
        // Ein 1x1-Anker an der Klickstelle. Die Layer-Surface ist
        // bildschirmfuellend, ihre Fenstergeometrie also der Output.
        positioner.set_anchor_rect(anker.0, anker.1, 1, 1);
        positioner.set_anchor(Anchor::BottomRight);
        positioner.set_gravity(Gravity::BottomRight);
        // Am Bildschirmrand darf der Compositor umklappen und schieben, statt
        // das Menue halb abzuschneiden.
        positioner.set_constraint_adjustment(
            ConstraintAdjustment::FlipX
                | ConstraintAdjustment::FlipY
                | ConstraintAdjustment::SlideX
                | ConstraintAdjustment::SlideY,
        );

        let surface = Surface::new(compositor, qh)
            .map_err(|fehler| format!("Popup-Surface: {fehler}"))?;
        let popup = Popup::from_surface(None, &positioner, qh, surface, xdg_shell)
            .map_err(|fehler| format!("xdg_popup: {fehler}"))?;
        // Elternteil ist die Layer-Surface, nicht ein xdg_surface. Ohne diesen
        // Aufruf vor dem ersten Commit gaebe es `invalid_popup_parent`.
        layer.get_popup(popup.xdg_popup());
        // Grab und Auto-Dismiss sind hier ausdruecklich gewollt: ein Klick
        // daneben schliesst das Menue, ohne dass wir irgendwo mithoeren.
        popup.xdg_popup().grab(seat, serial);

        // Neue Surface, neues Gate. Und die Region steht VOR dem ersten
        // Commit -- danach waere sie zu spaet.
        self.input_region = InputRegion::new();
        self.input_region.anwenden(
            compositor,
            popup.wl_surface(),
            Some(Box2D {
                x: 0,
                y: 0,
                w: BREITE,
                h: hoehe,
            }),
        );
        if !self.input_region.darf_committen() {
            return Err("Popup-Commit durch eigenes Input-Gate abgelehnt".into());
        }
        popup.wl_surface().commit();
        self.popup = Some(popup);
        Ok(())
    }

    /// Antwort auf `xdg_surface.configure`: erst jetzt darf ein Puffer daran.
    /// Rueckgabe `true` heisst: das Popup ist gemappt.
    pub fn puffer_committen(&mut self, pool: &mut SlotPool, raster: &Raster) -> Result<bool, String> {
        let Some(popup) = self.popup.as_ref() else {
            return Ok(false);
        };
        if !self.input_region.darf_committen() {
            return Err("Popup-Commit durch eigenes Input-Gate abgelehnt".into());
        }
        let stride = raster
            .breite
            .checked_mul(4)
            .ok_or_else(|| "Menue-Stride ist zu gross".to_string())?;
        let (buffer, canvas) = pool
            .create_buffer(raster.breite, raster.hoehe, stride, wl_shm::Format::Argb8888)
            .map_err(|fehler| format!("wl_shm-Menue-Puffer: {fehler}"))?;
        canvas.copy_from_slice(&raster.pixel);
        popup
            .wl_surface()
            .damage_buffer(0, 0, raster.breite, raster.hoehe);
        buffer
            .attach_to(popup.wl_surface())
            .map_err(|fehler| format!("wl_shm-Menue anhaengen: {fehler}"))?;
        popup.wl_surface().commit();
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bubble::BubbleRenderer;

    #[test]
    fn es_gibt_keine_aktion_die_einschaltet() {
        // Der Vertrag in einem Test: jede vorhandene Aktion schaltet ab,
        // beendet, schreibt eine Persona in eine Datei -- oder vermerkt einen
        // Widerruf, ebenfalls in einer Datei. Ein `EarsAn` waere hier sofort
        // rot.
        //
        // `BildschirmWiderrufen` steht hier NICHT, weil es bequem waere: es
        // schaltet nichts ein und nichts an, es NIMMT eine Erlaubnis zurueck.
        // Wer hier je eine Aktion eintraegt, die etwas startet, hat den
        // Vertrag dieses Moduls entfernt.
        for eintrag in eintraege() {
            match eintrag.aktion {
                None
                | Some(Aktion::Beenden)
                | Some(Aktion::Persona(_))
                | Some(Aktion::BildschirmWiderrufen)
                // Ebenfalls kein Einschalten: er PAUSIERT die Ablage.
                | Some(Aktion::Privatmodus) => {}
                Some(aktion) => assert!(
                    eintrag.text.ends_with("aus"),
                    "{} traegt eine Aktion {:?}, die nicht abschaltet",
                    eintrag.text,
                    aktion.name()
                ),
            }
        }
        assert_eq!(Aktion::aus_name("ears_an"), None);
        assert_eq!(Aktion::aus_name("eyes_an"), None);
        assert_eq!(Aktion::aus_name(""), None);
    }

    /// Der eingefrorene Pruefstand T-2.7 (8c) verlangt, dass genau diese
    /// Befehle wirkungslos bleiben. Die Personaauswahl hat deshalb ein
    /// eigenes Namensschema bekommen, statt eines dieser Woerter zu belegen.
    #[test]
    fn die_toten_persona_befehle_aus_t_2_7_bleiben_tot() {
        for name in [
            "persona",
            "persona_wechseln",
            "persona naechste",
            "persona:",
            "persona:gibtesnicht",
            "persona:../../etc/passwd",
        ] {
            assert_eq!(Aktion::aus_name(name), None, "{name} ist wirksam geworden");
        }
    }

    #[test]
    fn alle_geforderten_eintraege_stehen_im_menue() {
        let texte: Vec<String> = eintraege().into_iter().map(|e| e.text).collect();
        // Die vier festen Eintraege und die Ueberschrift stehen vorn, in
        // dieser Reihenfolge; dazwischen die gefundenen Personas; "Beenden"
        // bleibt der letzte.
        assert_eq!(
            texte[..5],
            [
                "Ohren an",
                "Ohren aus",
                "Augen an",
                "Augen aus",
                "Persona wechseln"
            ]
        );
        assert_eq!(texte.last().map(String::as_str), Some("Beenden"));
        assert!(texte.len() >= 7, "keine Auswahlzeile im Menue: {texte:?}");
    }

    #[test]
    fn deaktivierte_eintraege_loesen_nichts_aus() {
        let mitte = |index: usize| f64::from(RAND + index as i32 * ZEILE_H + ZEILE_H / 2);
        let liste = eintraege();
        assert_eq!(aktion_bei(20.0, mitte(0)), None); // Ohren an
        assert_eq!(aktion_bei(20.0, mitte(1)), Some(Aktion::EarsAus));
        assert_eq!(aktion_bei(20.0, mitte(2)), None); // Augen an
        assert_eq!(aktion_bei(20.0, mitte(3)), Some(Aktion::EyesAus));
        assert_eq!(aktion_bei(20.0, mitte(4)), None); // Ueberschrift
        assert_eq!(
            aktion_bei(20.0, mitte(liste.len() - 1)),
            Some(Aktion::Beenden)
        );
    }

    /// Genau eine Persona ist markiert, und genau die traegt keine Aktion:
    /// sie ist schon gewaehlt.
    #[test]
    fn die_aktive_persona_ist_markiert_und_ohne_aktion() {
        let aktiv: Vec<_> = eintraege()
            .into_iter()
            .filter(|e| e.text.contains('●'))
            .collect();
        assert!(aktiv.len() <= 1, "mehr als eine Persona markiert");
        for eintrag in aktiv {
            assert_eq!(eintrag.aktion, None, "{} ist klickbar", eintrag.text);
        }
    }

    /// Gegen die echten mitgelieferten Dateien im Repo. `..`, weil der
    /// Testlauf im Crate-Verzeichnis steht und der Betrieb in der Repowurzel.
    #[test]
    fn die_mitgelieferten_personas_werden_gefunden_und_beschriftet() {
        let repo = PathBuf::from("../config/persona");
        if !repo.is_dir() {
            return; // aus einem anderen Verzeichnis gebaut; kein Befund
        }
        let liste = personas_aus(&[repo]);
        let dateien: Vec<&str> = liste.iter().map(|p| p.datei.as_str()).collect();
        assert!(dateien.contains(&"ember"), "{dateien:?}");
        assert!(dateien.contains(&"nordom"), "{dateien:?}");
        // Der Anzeigename kommt aus der Datei, nicht aus dem Dateinamen.
        let nordom = liste.iter().find(|p| p.datei == "nordom").unwrap();
        assert_eq!(nordom.anzeige, "Nordom");
    }

    /// Die Datei unter XDG gewinnt gegen die mitgelieferte -- dieselbe
    /// Reihenfolge wie im Python-Lader. Gewaenne die mitgelieferte, waere die
    /// eigene Persona im Menue unsichtbar.
    #[test]
    fn die_eigene_datei_verdraengt_die_mitgelieferte() {
        let temp = std::env::temp_dir().join(format!("daimon-menu-{}", std::process::id()));
        let eigen = temp.join("eigen");
        let mitgeliefert = temp.join("mitgeliefert");
        std::fs::create_dir_all(&eigen).unwrap();
        std::fs::create_dir_all(&mitgeliefert).unwrap();
        std::fs::write(eigen.join("nordom.toml"), "name = \"Meiner\"\n").unwrap();
        std::fs::write(mitgeliefert.join("nordom.toml"), "name = \"Nordom\"\n").unwrap();
        std::fs::write(mitgeliefert.join("nicht.txt"), "x").unwrap();
        std::fs::write(mitgeliefert.join("kiesel.toml"), "voice = \"x\"\n").unwrap();

        let liste = personas_aus(&[eigen, mitgeliefert]);
        let _ = std::fs::remove_dir_all(&temp);

        assert_eq!(liste.len(), 2, "{liste:?}");
        let nordom = liste.iter().find(|p| p.datei == "nordom").unwrap();
        assert_eq!(nordom.anzeige, "Meiner");
        // Ohne `name`-Zeile: Dateiname mit grossem Anfangsbuchstaben.
        let kiesel = liste.iter().find(|p| p.datei == "kiesel").unwrap();
        assert_eq!(kiesel.anzeige, "Kiesel");
    }

    #[test]
    fn gefundene_personas_haben_zulaessige_dateinamen() {
        for persona in personas() {
            assert!(
                ist_zulaessiger_dateiname(&persona.datei),
                "{:?} haette nie in die Liste gedurft",
                persona.datei
            );
        }
    }

    #[test]
    fn dateinamen_mit_pfad_oder_anfuehrungszeichen_werden_abgewiesen() {
        for schlecht in ["", "../etc", "a b", "na\"me", "punkt.toml", "zeile\n"] {
            assert!(!ist_zulaessiger_dateiname(schlecht), "{schlecht:?}");
        }
        for gut in ["ember", "kiesel", "nordom", "mein_pet-2"] {
            assert!(ist_zulaessiger_dateiname(gut), "{gut:?}");
        }
    }

    #[test]
    fn der_persona_name_wird_aus_dem_richtigen_abschnitt_gelesen() {
        let inhalt = "[tts]\nname = \"nicht diese\"\n\n[persona]\nname = \"Nordom\"\nvoice = \"x\"\n";
        assert_eq!(persona_name_lesen(inhalt).as_deref(), Some("Nordom"));
    }

    #[test]
    fn ein_auskommentierter_name_gilt_nicht() {
        assert_eq!(persona_name_lesen("[persona]\n# name = \"Ember\"\n"), None);
        assert_eq!(persona_name_lesen("[logging]\nid = \"d\"\n"), None);
    }

    /// Die Auswahldatei schreibt dieses Modul selbst. Gelesen wird sie
    /// trotzdem misstrauisch: sie liegt im Zustand, und der ueberlebt
    /// Abstuerze, Bastelei und aeltere Fassungen.
    #[test]
    fn die_auswahldatei_wird_gelesen_und_geprueft() {
        assert_eq!(
            auswahl_lesen("{\"v\": 1, \"name\": \"nordom\"}\n").as_deref(),
            Some("nordom")
        );
        // Andere Reihenfolge, mehr Leerraum: dasselbe Ergebnis.
        assert_eq!(
            auswahl_lesen("{ \"name\"  :   \"kiesel\" , \"v\": 1 }").as_deref(),
            Some("kiesel")
        );
        for muell in [
            "",
            "{}",
            "{\"name\": \"\"}",
            "{\"name\": \"../etc/passwd\"}",
            "{\"name\": \"mit leer\"}",
            "{\"name\": 7}",
            "kein json",
        ] {
            assert_eq!(auswahl_lesen(muell), None, "{muell:?} kam durch");
        }
    }

    /// Der geschriebene Name ist der DATEINAME, nicht der Anzeigename: der
    /// Lader sucht `<name>.toml`. Stuende dort "Meiner", fiele der naechste
    /// Start auf eine Datei, die es nicht gibt.
    #[test]
    fn geschrieben_wird_der_dateiname() {
        let inhalt = format!("{{\"v\": 1, \"name\": \"{}\"}}\n", "nordom");
        assert_eq!(auswahl_lesen(&inhalt).as_deref(), Some("nordom"));
    }

    /// Deaktiviert heisst sichtbar-aber-blass, nicht unsichtbar und nicht
    /// versteckt. Gemessen an den Pixeln, nicht an einem Flag.
    #[test]
    fn deaktivierte_eintraege_sind_sichtbar_und_blasser() {
        let renderer = BubbleRenderer::neu().unwrap();
        let raster = rendern(renderer.font());
        let hellster = |index: usize| {
            let von = ((RAND + index as i32 * ZEILE_H) * BREITE * 4) as usize;
            let bis = ((RAND + (index as i32 + 1) * ZEILE_H) * BREITE * 4) as usize;
            raster.pixel[von..bis]
                .chunks_exact(4)
                .map(|p| p[0])
                .max()
                .unwrap()
        };
        let hintergrund = premultipliziert(24, 27, 34, 242)[0];
        // "Ohren an" ist deaktiviert, "Ohren aus" nicht.
        assert!(hellster(0) > hintergrund, "deaktivierter Eintrag unsichtbar");
        assert!(
            hellster(0) < hellster(1),
            "deaktiviert ({}) nicht blasser als aktiv ({})",
            hellster(0),
            hellster(1)
        );
    }
}
