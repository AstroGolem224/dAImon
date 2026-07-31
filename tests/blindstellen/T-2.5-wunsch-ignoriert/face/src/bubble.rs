//! CPU-gerasterte Sprechblase fuer eine eigene Wayland-Subsurface.
//!
//! Der Hub liefert bereits gesaeubertes ASCII. Dieses Modul macht deshalb
//! ausschliesslich Layout und Pixel: keine zweite Unicode-Policy, keine GPU
//! und keine System-Fontsuche.

use fontdue::{
    layout::{CoordinateSystem, Layout, LayoutSettings, TextStyle, WrapStyle},
    Font, FontSettings,
};

pub const BREITE: i32 = 320;
const INNEN_ABSTAND: i32 = 16;
const SCHRIFTGROESSE: f32 = 17.0;
const MIN_HOEHE: i32 = 56;
const PET_ABSTAND: i32 = 12;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bubble {
    pub title: String,
    pub body: String,
    pub urgent: bool,
}

#[derive(Debug)]
pub struct Raster {
    pub breite: i32,
    pub hoehe: i32,
    pub pixel: Vec<u8>,
    pub zeilen: usize,
}

pub struct BubbleRenderer {
    font: Font,
}

impl BubbleRenderer {
    pub fn neu() -> Result<Self, String> {
        let font = Font::from_bytes(
            include_bytes!("../assets/DejaVuSans.ttf").as_slice(),
            FontSettings::default(),
        )
        .map_err(|fehler| format!("eingebettete DejaVu-Sans-Schrift: {fehler}"))?;
        Ok(Self { font })
    }

    pub fn rendern(&self, bubble: &Bubble) -> Raster {
        let text = if bubble.title.is_empty() {
            bubble.body.clone()
        } else if bubble.body.is_empty() {
            bubble.title.clone()
        } else {
            format!("{}\n{}", bubble.title, bubble.body)
        };
        self.text_rendern(&text, bubble.urgent)
    }

    fn text_rendern(&self, text: &str, urgent: bool) -> Raster {
        let mut layout = Layout::new(CoordinateSystem::PositiveYDown);
        layout.reset(&LayoutSettings {
            x: INNEN_ABSTAND as f32,
            y: INNEN_ABSTAND as f32,
            max_width: Some((BREITE - 2 * INNEN_ABSTAND) as f32),
            wrap_style: WrapStyle::Word,
            wrap_hard_breaks: true,
            ..LayoutSettings::default()
        });
        layout.append(&[&self.font], &TextStyle::new(text, SCHRIFTGROESSE, 0));

        let zeilen = layout.lines().map_or(0, Vec::len);
        let hoehe = ((layout.height().ceil() as i32) + 2 * INNEN_ABSTAND).max(MIN_HOEHE);
        let mut pixel = vec![0; (BREITE * hoehe * 4) as usize];
        let hintergrund = if urgent {
            premultipliziert(78, 30, 35, 244)
        } else {
            premultipliziert(26, 31, 38, 238)
        };
        for p in pixel.chunks_exact_mut(4) {
            p.copy_from_slice(&hintergrund);
        }

        for glyph in layout.glyphs() {
            let (_, deckung) = self.font.rasterize_config(glyph.key);
            for gy in 0..glyph.height {
                for gx in 0..glyph.width {
                    let x = glyph.x.floor() as i32 + gx as i32;
                    let y = glyph.y.floor() as i32 + gy as i32;
                    if x < 0 || y < 0 || x >= BREITE || y >= hoehe {
                        continue;
                    }
                    let quelle = glyph_pixel(deckung[gy * glyph.width + gx], 255);
                    let index = ((y * BREITE + x) * 4) as usize;
                    ueberblenden(&mut pixel[index..index + 4], quelle);
                }
            }
        }

        Raster {
            breite: BREITE,
            hoehe,
            pixel,
            zeilen,
        }
    }
}

/// Position der Blase relativ zur Sprite-Surface. Bevorzugt wird rechts
/// unterhalb des Pet; die absolute Position wird auf den Output geklemmt und
/// anschliessend wieder in Pet-Koordinaten umgerechnet.
pub fn position_klemmen(
    pet: (i32, i32),
    pet_groesse: (i32, i32),
    bubble_groesse: (i32, i32),
    output_groesse: (i32, i32),
) -> (i32, i32) {
    let bevorzugt = (
        pet.0 + pet_groesse.0 + PET_ABSTAND,
        pet.1 + pet_groesse.1 + PET_ABSTAND,
    );
    let max_x = (output_groesse.0 - bubble_groesse.0).max(0);
    let max_y = (output_groesse.1 - bubble_groesse.1).max(0);
    let absolut = (bevorzugt.0.clamp(0, max_x), bevorzugt.1.clamp(0, max_y));
    (absolut.0 - pet.0, absolut.1 - pet.1)
}

/// ARGB8888 liegt auf Little-Endian als BGRA im Speicher. Jeder Farbkanal ist
/// mit Alpha vormultipliziert, bevor der Pixel in einen wl_shm-Puffer kommt.
fn premultipliziert(r: u8, g: u8, b: u8, a: u8) -> [u8; 4] {
    let kanal = |wert: u8| ((u16::from(wert) * u16::from(a) + 127) / 255) as u8;
    [kanal(b), kanal(g), kanal(r), a]
}

fn glyph_pixel(graustufe: u8, text_alpha: u8) -> [u8; 4] {
    let alpha = ((u16::from(graustufe) * u16::from(text_alpha) + 127) / 255) as u8;
    // Weisser Text: vormultiplizierte RGB-Kanaele entsprechen dem Alpha.
    [alpha, alpha, alpha, alpha]
}

fn ueberblenden(ziel: &mut [u8], quelle: [u8; 4]) {
    let inv = 255 - u16::from(quelle[3]);
    for kanal in 0..3 {
        ziel[kanal] = (u16::from(quelle[kanal]) + (u16::from(ziel[kanal]) * inv + 127) / 255) as u8;
    }
    ziel[3] = (u16::from(quelle[3]) + (u16::from(ziel[3]) * inv + 127) / 255) as u8;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn breiter_text_bricht_auf_vier_zeilen_um() {
        let renderer = BubbleRenderer::neu().unwrap();
        let raster = renderer.text_rendern(
            "Diese Zeile ist absichtlich deutlich breiter als die Sprechblase und muss umbrechen.",
            false,
        );
        assert_eq!(raster.zeilen, 4);
    }

    #[test]
    fn rechts_ausserhalb_wird_auf_erwartete_koordinate_geklemmt() {
        // Bevorzugt waere absolut x=792. Bei 800 px Output und 320 px Blase
        // ist x=480 die letzte sichtbare Position, relativ zum Pet also -220.
        assert_eq!(
            position_klemmen((700, 500), (80, 80), (320, 90), (800, 600)),
            (-220, 10)
        );
    }

    #[test]
    fn glyph_graustufe_wird_premultipliziert() {
        assert_eq!(glyph_pixel(128, 200), [100, 100, 100, 100]);
    }
}
