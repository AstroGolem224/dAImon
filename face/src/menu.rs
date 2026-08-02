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
//! **2. Es gibt kein Einschalten.** `Aktion` kennt drei Werte, und keiner
//! davon startet etwas. Die Eintraege "Ohren an", "Augen an" und "Persona
//! wechseln" stehen sichtbar und ausgegraut im Menue, ohne Aktion dahinter.
//! Ein Overlay, das Wahrnehmung nur abschalten kann, ist fail-safe;
//! Einschalten gehoert zum Auth-Agenten und existiert in P2 nicht.
//!
//! **3. Die Input-Region der Popup-Surface steht vor ihrem ersten Commit.**
//! Dieselbe Regel wie in `input.rs`, aus demselben Grund. Deshalb liegt hier
//! ein eigenes `InputRegion`-Gate, obwohl sonst alle Commits in `surface.rs`
//! liegen: das Popup ist eine eigene Surface mit eigener Lebensdauer.

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

/// Was ein Menueeintrag ausloesen kann. Bewusst nur diese drei: jeder weitere
/// Wert waere eine neue Faehigkeit des Overlays.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Aktion {
    EarsAus,
    EyesAus,
    Beenden,
}

impl Aktion {
    /// Der Name, der in `letzte_menu_aktion` landet und den der Steuer-Socket
    /// annimmt. Eine Quelle, damit beide nicht auseinanderlaufen koennen.
    pub fn name(self) -> &'static str {
        match self {
            Self::EarsAus => "ears_aus",
            Self::EyesAus => "eyes_aus",
            Self::Beenden => "beenden",
        }
    }

    pub fn aus_name(name: &str) -> Option<Self> {
        [Self::EarsAus, Self::EyesAus, Self::Beenden]
            .into_iter()
            .find(|aktion| aktion.name() == name)
    }

    /// Der Schluessel fuer `wahrnehmung_aus`. **Kein Unit-Name** -- welche
    /// Unit dahinter steht, entscheidet allein die Konfiguration des Hubs.
    /// Stuende der Name hier, koennte das Overlay den Hub selbst, den
    /// Auth-Agenten oder jede Unit des Nutzers stoppen.
    pub fn ziel(self) -> Option<&'static str> {
        match self {
            Self::EarsAus => Some("ears"),
            Self::EyesAus => Some("eyes"),
            Self::Beenden => None,
        }
    }
}

pub struct Eintrag {
    pub text: &'static str,
    /// `None` heisst: sichtbar, aber deaktiviert. Nicht versteckt -- ein
    /// Menue, das spaeter Eintraege dazubekommt, verwirrt mehr als eines, das
    /// zeigt, was kommt.
    pub aktion: Option<Aktion>,
}

pub const EINTRAEGE: &[Eintrag] = &[
    // Kein Einschalten in P2: das gehoert zum Auth-Agenten.
    Eintrag {
        text: "Ohren an",
        aktion: None,
    },
    Eintrag {
        text: "Ohren aus",
        aktion: Some(Aktion::EarsAus),
    },
    Eintrag {
        text: "Augen an",
        aktion: None,
    },
    Eintrag {
        text: "Augen aus",
        aktion: Some(Aktion::EyesAus),
    },
    // Der Persona-Lader ist T-3.10.
    Eintrag {
        text: "Persona wechseln",
        aktion: None,
    },
    Eintrag {
        text: "Beenden",
        aktion: Some(Aktion::Beenden),
    },
];

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
    2 * RAND + EINTRAEGE.len() as i32 * ZEILE_H
}

/// Zeilenindex zu einem Klick in Popup-Koordinaten.
pub fn index_bei(x: f64, y: f64) -> Option<usize> {
    if x < 0.0 || y < f64::from(RAND) || x >= f64::from(BREITE) {
        return None;
    }
    let index = ((y - f64::from(RAND)) / f64::from(ZEILE_H)) as usize;
    (index < EINTRAEGE.len()).then_some(index)
}

/// Die Aktion unter dem Zeiger. `None` fuer deaktivierte Eintraege und fuer
/// alles ausserhalb -- ein deaktivierter Eintrag darf nicht versehentlich auf
/// den Nachbarn durchschlagen.
pub fn aktion_bei(x: f64, y: f64) -> Option<Aktion> {
    EINTRAEGE[index_bei(x, y)?].aktion
}

pub fn rendern(font: &Font) -> Raster {
    let hoehe = hoehe();
    let mut pixel = vec![0u8; (BREITE * hoehe * 4) as usize];
    let hintergrund = premultipliziert(24, 27, 34, 242);
    for p in pixel.chunks_exact_mut(4) {
        p.copy_from_slice(&hintergrund);
    }
    for (index, eintrag) in EINTRAEGE.iter().enumerate() {
        let alpha = if eintrag.aktion.is_some() {
            ALPHA_AKTIV
        } else {
            ALPHA_DEAKTIVIERT
        };
        let y = RAND + index as i32 * ZEILE_H + 4;
        zeile_zeichnen(font, &mut pixel, hoehe, eintrag.text, y, alpha);
    }
    Raster {
        breite: BREITE,
        hoehe,
        pixel,
        zeilen: EINTRAEGE.len(),
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
        // Der Vertrag in einem Test: jede vorhandene Aktion schaltet ab oder
        // beendet. Ein `EarsAn` waere hier sofort rot.
        for eintrag in EINTRAEGE {
            match eintrag.aktion {
                None | Some(Aktion::Beenden) => {}
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
        assert_eq!(Aktion::aus_name("persona"), None);
        assert_eq!(Aktion::aus_name(""), None);
    }

    #[test]
    fn alle_geforderten_eintraege_stehen_im_menue() {
        let texte: Vec<&str> = EINTRAEGE.iter().map(|e| e.text).collect();
        assert_eq!(
            texte,
            vec![
                "Ohren an",
                "Ohren aus",
                "Augen an",
                "Augen aus",
                "Persona wechseln",
                "Beenden"
            ]
        );
    }

    #[test]
    fn deaktivierte_eintraege_loesen_nichts_aus() {
        let mitte = |index: usize| f64::from(RAND + index as i32 * ZEILE_H + ZEILE_H / 2);
        assert_eq!(aktion_bei(20.0, mitte(0)), None); // Ohren an
        assert_eq!(aktion_bei(20.0, mitte(1)), Some(Aktion::EarsAus));
        assert_eq!(aktion_bei(20.0, mitte(2)), None); // Augen an
        assert_eq!(aktion_bei(20.0, mitte(3)), Some(Aktion::EyesAus));
        assert_eq!(aktion_bei(20.0, mitte(4)), None); // Persona wechseln
        assert_eq!(aktion_bei(20.0, mitte(5)), Some(Aktion::Beenden));
    }

    #[test]
    fn klicks_ausserhalb_treffen_keinen_eintrag() {
        assert_eq!(index_bei(20.0, 0.0), None);
        assert_eq!(index_bei(-1.0, 40.0), None);
        assert_eq!(index_bei(f64::from(BREITE), 40.0), None);
        assert_eq!(index_bei(20.0, f64::from(hoehe())), None);
        assert_eq!(index_bei(20.0, f64::from(hoehe() + 100)), None);
    }

    #[test]
    fn namen_und_ziele_sind_stabil() {
        assert_eq!(Aktion::aus_name("ears_aus"), Some(Aktion::EarsAus));
        assert_eq!(Aktion::aus_name("eyes_aus"), Some(Aktion::EyesAus));
        assert_eq!(Aktion::aus_name("beenden"), Some(Aktion::Beenden));
        assert_eq!(Aktion::EarsAus.ziel(), Some("ears"));
        assert_eq!(Aktion::EyesAus.ziel(), Some("eyes"));
        assert_eq!(Aktion::Beenden.ziel(), None);
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
