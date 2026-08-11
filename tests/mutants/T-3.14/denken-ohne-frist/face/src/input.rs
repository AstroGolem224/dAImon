//! T-1.3 — Input-Region und Click-Through.
//!
//! # Die wichtigste Regel im ganzen Programm
//!
//! **Die Input-Region wird IMMER gesetzt, auch wenn keine gewuenscht ist.**
//!
//! Eine Wayland-Surface **ohne** gesetzte `input_region` nimmt Eingaben auf
//! ihrer **ganzen** Flaeche entgegen. Bei einer bildschirmfuellenden
//! Layer-Surface heisst das: der komplette Schirm schluckt Klicks, und der
//! Rechner ist mit der Maus nicht mehr bedienbar. Im Journal steht **nichts**,
//! weil aus Sicht des Compositors alles korrekt ist.
//!
//! Das ist am 2026-07-27 in Spike T-1.3 real passiert. Die Vorgabe ist
//! deshalb die **leere** Region -- vollstaendig klickdurchlaessig -- und nicht
//! "keine Region".
//!
//! Und sie steht **vor dem ersten `commit`**. Danach waere zu spaet: zwischen
//! erstem Commit und Nachreichen liegt ein Zeitfenster, in dem die Surface
//! schon sichtbar ist und alles schluckt.

use smithay_client_toolkit::compositor::{CompositorState, Region};
use wayland_client::protocol::wl_surface;

/// Rechteck in Surface-Koordinaten.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Box2D {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl Box2D {
    pub fn enthaelt(&self, px: i32, py: i32) -> bool {
        px >= self.x && px < self.x + self.w && py >= self.y && py < self.y + self.h
    }
    pub fn leer(&self) -> bool {
        self.w <= 0 || self.h <= 0
    }
}

/// Verwaltet die Input-Region und aktualisiert sie **nur bei Aenderung**.
///
/// Jedes `set_input_region` erzwingt beim Compositor eine Neuberechnung. Bei
/// einem Sprite, das sich jeden Frame minimal bewegt, waere das eine
/// Dauerlast -- und die Idle-CPU von 0,17 % war das Argument fuer diese ganze
/// Architektur.
pub struct InputRegion {
    aktuell: Vec<Box2D>,
    gesetzt_mindestens_einmal: bool,
}

impl InputRegion {
    pub fn new() -> Self {
        Self {
            aktuell: Vec::new(),
            gesetzt_mindestens_einmal: false,
        }
    }

    /// Muss vor dem ersten Commit aufgerufen werden. Gibt zurueck, ob die
    /// Region tatsaechlich neu gesetzt wurde.
    pub fn anwenden(
        &mut self,
        compositor: &CompositorState,
        surface: &wl_surface::WlSurface,
        gewuenscht: Option<Box2D>,
    ) -> bool {
        let rechtecke = gewuenscht.into_iter().collect::<Vec<_>>();
        self.laeufe_anwenden(compositor, surface, &rechtecke)
    }

    /// Setzt eine aus beliebig vielen Rechtecken bestehende Region.
    pub fn laeufe_anwenden(
        &mut self,
        compositor: &CompositorState,
        surface: &wl_surface::WlSurface,
        gewuenscht: &[Box2D],
    ) -> bool {
        let normiert = gewuenscht
            .iter()
            .copied()
            .filter(|b| !b.leer())
            .collect::<Vec<_>>();
        if self.gesetzt_mindestens_einmal && self.aktuell == normiert {
            return false;
        }

        // Region ueber das Toolkit -- so wie im Spike T-1.3, der nachweislich
        // trennt: innerhalb bekommt das Overlay pointer_enter und Klicks, das
        // Fenster darunter bekommt NICHTS; ausserhalb genau umgekehrt.
        let region = Region::new(compositor).expect("wl_region");
        for b in &normiert {
            region.add(b.x, b.y, b.w, b.h);
        }
        surface.set_input_region(Some(region.wl_region()));

        self.aktuell = normiert;
        self.gesetzt_mindestens_einmal = true;
        true
    }

    pub fn aktuelle(&self) -> Option<Box2D> {
        (self.aktuell.len() == 1).then(|| self.aktuell[0])
    }

    /// Sicherheitsnetz fuer den Aufrufer: wurde ueberhaupt je eine Region
    /// gesetzt? Wenn nicht, darf nicht committet werden.
    pub fn darf_committen(&self) -> bool {
        self.gesetzt_mindestens_einmal
    }
}

impl Default for InputRegion {
    fn default() -> Self {
        Self::new()
    }
}

pub const SICHTBAR_ALPHA_SCHWELLE: u8 = 128;

/// Alpha-Test: Klicks auf durchsichtige Raender verwerfen.
///
/// Die Bounding-Box eines Sprites ist rechteckig, das Sprite selbst nicht.
/// Ohne diesen Test faengt das Pet Klicks in seinen leeren Ecken ab -- was von
/// aussen wie ein kaputter Desktop aussieht und nicht wie ein Feature.
pub fn undurchsichtig_an(pixels: &[u8], breite: i32, x: i32, y: i32, schwelle: u8) -> bool {
    if x < 0 || y < 0 || breite <= 0 {
        return false;
    }
    let idx = ((y as usize) * (breite as usize) + (x as usize)) * 4;
    // ARGB8888 little endian: Alpha ist das oberste Byte, also Index +3.
    pixels.get(idx + 3).map_or(false, |a| *a >= schwelle)
}

/// Zusammenhaengende Laeufe sichtbarer Pixel je Zeile.
pub fn sichtbare_laeufe(pixel: &[u8], breite: u32, hoehe: u32) -> Vec<Box2D> {
    let (Ok(breite_i32), Ok(hoehe_i32)) = (i32::try_from(breite), i32::try_from(hoehe)) else {
        return Vec::new();
    };
    let mut laeufe = Vec::new();
    for y in 0..hoehe_i32 {
        let mut x = 0;
        while x < breite_i32 {
            while x < breite_i32
                && !undurchsichtig_an(pixel, breite_i32, x, y, SICHTBAR_ALPHA_SCHWELLE)
            {
                x += 1;
            }
            let start = x;
            while x < breite_i32
                && undurchsichtig_an(pixel, breite_i32, x, y, SICHTBAR_ALPHA_SCHWELLE)
            {
                x += 1;
            }
            if start < x {
                laeufe.push(Box2D {
                    x: start,
                    y,
                    w: x - start,
                    h: 1,
                });
            }
        }
    }
    laeufe
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sprite::SpriteAtlas;
    use std::path::Path;

    #[test]
    fn box_enthaelt_nur_innen() {
        let b = Box2D {
            x: 10,
            y: 10,
            w: 20,
            h: 20,
        };
        assert!(b.enthaelt(10, 10));
        assert!(b.enthaelt(29, 29));
        assert!(!b.enthaelt(30, 30));
        assert!(!b.enthaelt(9, 15));
    }

    #[test]
    fn leere_box_erkannt() {
        assert!(Box2D {
            x: 0,
            y: 0,
            w: 0,
            h: 10
        }
        .leer());
        assert!(!Box2D {
            x: 0,
            y: 0,
            w: 1,
            h: 1
        }
        .leer());
    }

    #[test]
    fn alpha_test_verwirft_durchsichtiges() {
        // 2x1 Bild: erstes Pixel transparent, zweites deckend.
        let px = [0, 0, 0, 0, 255, 255, 255, 255];
        assert!(!undurchsichtig_an(&px, 2, 0, 0, 128));
        assert!(undurchsichtig_an(&px, 2, 1, 0, 128));
    }

    #[test]
    fn alpha_test_ausserhalb_ist_durchlaessig() {
        let px = [255, 255, 255, 255];
        assert!(!undurchsichtig_an(&px, 1, -1, 0, 128));
        assert!(!undurchsichtig_an(&px, 1, 0, -1, 128));
        assert!(!undurchsichtig_an(&px, 1, 99, 99, 128));
    }

    #[test]
    fn vollstaendig_deckend_ergibt_einen_lauf_je_zeile() {
        assert_eq!(
            sichtbare_laeufe(&[255; 24], 3, 2),
            vec![
                Box2D {
                    x: 0,
                    y: 0,
                    w: 3,
                    h: 1
                },
                Box2D {
                    x: 0,
                    y: 1,
                    w: 3,
                    h: 1
                }
            ]
        );
    }

    #[test]
    fn vollstaendig_durchsichtig_ergibt_keinen_lauf() {
        assert_eq!(sichtbare_laeufe(&[0; 24], 3, 2), vec![]);
    }

    #[test]
    fn einzelnes_pixel_in_der_mitte_ergibt_lauf_der_breite_eins() {
        let mut pixel = [0; 36];
        pixel[(1 * 3 + 1) * 4 + 3] = 255;
        assert_eq!(
            sichtbare_laeufe(&pixel, 3, 3),
            vec![Box2D {
                x: 1,
                y: 1,
                w: 1,
                h: 1
            }]
        );
    }

    #[test]
    fn getrennte_bereiche_bleiben_zwei_laeufe() {
        let pixel = [0, 0, 0, 255, 0, 0, 0, 0, 0, 0, 0, 255];
        assert_eq!(
            sichtbare_laeufe(&pixel, 3, 1),
            vec![
                Box2D {
                    x: 0,
                    y: 0,
                    w: 1,
                    h: 1
                },
                Box2D {
                    x: 2,
                    y: 0,
                    w: 1,
                    h: 1
                }
            ]
        );
    }

    #[test]
    fn alpha_knapp_unter_der_schwelle_ist_nicht_sichtbar() {
        let pixel = [0, 0, 0, 127, 0, 0, 0, 128];
        assert_eq!(
            sichtbare_laeufe(&pixel, 2, 1),
            vec![Box2D {
                x: 1,
                y: 0,
                w: 1,
                h: 1
            }]
        );
    }

    #[test]
    fn echtes_idle_sprite_deckt_weniger_als_die_zelle_ab() {
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("assets/pet.json");
        let atlas = SpriteAtlas::laden(&manifest).expect("Pet-Atlas laden");
        let frame = atlas.frame(0, 0).expect("Idle-Frame 0");
        let laeufe = sichtbare_laeufe(&frame, 192, 208);
        let flaeche: i32 = laeufe.iter().map(|lauf| lauf.w * lauf.h).sum();

        assert_eq!(laeufe.len(), 95);
        assert_eq!(flaeche, 3760);
        assert!(3760 < 39936);
    }
}
