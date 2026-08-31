//! CPU-gerasterte Sprechblase fuer eine eigene Wayland-Subsurface.
//!
//! Der Hub liefert bereits gesaeubertes ASCII. Dieses Modul macht deshalb
//! ausschliesslich Layout und Pixel: keine zweite Unicode-Policy, keine GPU
//! und keine System-Fontsuche.

use fontdue::{
    layout::{CoordinateSystem, Layout, LayoutSettings, TextStyle, WrapStyle},
    Font, FontSettings,
};

pub const BREITE: i32 = 420;
const INNEN_ABSTAND: i32 = 18;
/// Titel und Fliesstext unterscheiden sich in der Groesse, nicht im Schnitt --
/// eingebettet ist genau ein Font, und ein zweiter waere ein zweites
/// `include_bytes!` im Binary.
const SCHRIFT_TITEL: f32 = 16.5;
const SCHRIFT_TEXT: f32 = 13.5;
const ZEILEN_HOEHE: f32 = 1.35;
const MIN_HOEHE: i32 = 64;
const PET_ABSTAND: i32 = 12;

// -- Form der Blase --------------------------------------------------------
const ECKRADIUS: f32 = 14.0;
const ZIPFEL_HOEHE: i32 = 14;
const ZIPFEL_BREITE: i32 = 22;

/// Wo der Zipfel sitzt. Er zeigt zum Pet, und weil `position_klemmen` die
/// Blase am Bildschirmrand auf die andere Seite schiebt, ist das nicht immer
/// oben links.
///
/// Der Zipfel wird deshalb NACH dem Korpus gemalt (`zipfel_malen`): die
/// Blasengroesse haengt nur am Text, die Richtung nur an der Klemmung. Wer
/// beides in einem Zug rendern wollte, braeuchte die Groesse, bevor er sie
/// hat.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Zipfel {
    /// An der oberen Kante (Blase liegt unter dem Pet) statt an der unteren.
    pub oben: bool,
    /// Linke Kante des Dreiecks, in Blasenkoordinaten.
    pub x: i32,
}

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

    /// T-2.7: das Kontextmenue rastert mit derselben eingebetteten Schrift.
    /// Ein zweites `include_bytes!` haette die Datei ein zweites Mal ins
    /// Binary gelegt.
    pub fn font(&self) -> &Font {
        &self.font
    }

    pub fn rendern(&self, bubble: &Bubble) -> Raster {
        self.text_rendern(&bubble.title, &bubble.body, bubble.urgent)
    }

    fn text_rendern(&self, titel: &str, body: &str, urgent: bool) -> Raster {
        let mut layout = Layout::new(CoordinateSystem::PositiveYDown);
        layout.reset(&LayoutSettings {
            x: INNEN_ABSTAND as f32,
            y: (INNEN_ABSTAND + ZIPFEL_HOEHE) as f32,
            max_width: Some((BREITE - 2 * INNEN_ABSTAND) as f32),
            line_height: ZEILEN_HOEHE,
            wrap_style: WrapStyle::Word,
            wrap_hard_breaks: true,
            ..LayoutSettings::default()
        });
        if !titel.is_empty() {
            let mit_umbruch = if body.is_empty() {
                titel.to_owned()
            } else {
                format!("{titel}\n")
            };
            layout.append(&[&self.font], &TextStyle::new(&mit_umbruch, SCHRIFT_TITEL, 0));
        }
        if !body.is_empty() {
            layout.append(&[&self.font], &TextStyle::new(body, SCHRIFT_TEXT, 0));
        }

        let zeilen = layout.lines().map_or(0, Vec::len);
        // Oben UND unten bleibt ein Band frei: welches davon der Zipfel
        // belegt, steht erst nach dem Klemmen fest, und die Blasenhoehe darf
        // davon nicht abhaengen -- sonst braeuchte das Klemmen eine Groesse,
        // die es selbst erst bestimmt.
        let hoehe = ((layout.height().ceil() as i32) + 2 * INNEN_ABSTAND + 2 * ZIPFEL_HOEHE)
            .max(MIN_HOEHE);
        let mut pixel = vec![0; (BREITE * hoehe * 4) as usize];
        korpus_fuellen(&mut pixel, hoehe, blasenfarbe(urgent));

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

/// Die eine Stelle, an der die Blasenfarbe steht. Korpus und Zipfel muessen
/// dieselbe treffen, und zwei Fassungen davon waeren eine Farbe und eine
/// Attrappe.
fn blasenfarbe(urgent: bool) -> (u8, u8, u8, u8) {
    if urgent {
        (78, 30, 35, 244)
    } else {
        (26, 31, 38, 238)
    }
}

/// Rundet den Korpus. Ausserhalb bleibt der Puffer durchsichtig -- die
/// Subsurface ist alphafaehig, ein gefuellter Rahmen waere wieder das
/// Rechteck von vorher. Die beiden Zipfelbaender bleiben leer.
fn korpus_fuellen(pixel: &mut [u8], hoehe: i32, farbe: (u8, u8, u8, u8)) {
    let (r, g, b, a) = farbe;
    let korpus_oben = ZIPFEL_HOEHE as f32;
    let korpus_unten = (hoehe - ZIPFEL_HOEHE) as f32;
    for y in ZIPFEL_HOEHE..(hoehe - ZIPFEL_HOEHE) {
        for x in 0..BREITE {
            let (px, py) = (x as f32 + 0.5, y as f32 + 0.5);
            let deckung = (0.5 - sdf_rundrect(px, py, korpus_oben, korpus_unten)).clamp(0.0, 1.0);
            if deckung <= 0.0 {
                continue;
            }
            let alpha = (f32::from(a) * deckung).round() as u8;
            let index = ((y * BREITE + x) * 4) as usize;
            pixel[index..index + 4].copy_from_slice(&premultipliziert(r, g, b, alpha));
        }
    }
}

/// Malt den Zipfel in das freie Band an der Kante, die `zipfel` nennt.
///
/// Getrennt vom Korpus, weil die Richtung erst nach dem Klemmen feststeht.
/// Ohne Kantenglaettung: das Dreieck ist 14 px hoch, und der harte Rand
/// faellt neben dem weichen Korpus nicht auf.
pub fn zipfel_malen(raster: &mut Raster, zipfel: Zipfel, urgent: bool) {
    let (r, g, b, a) = blasenfarbe(urgent);
    let farbe = premultipliziert(r, g, b, a);
    let mitte = zipfel.x + ZIPFEL_BREITE / 2;
    let band = if zipfel.oben {
        0..ZIPFEL_HOEHE
    } else {
        (raster.hoehe - ZIPFEL_HOEHE)..raster.hoehe
    };
    for y in band {
        // Abstand zur Korpuskante: 0 an der Basis, ZIPFEL_HOEHE-1 an der
        // Spitze. Bei 0 ist das Dreieck so breit wie die Basis -- der Zipfel
        // haengt sonst mit einer Luecke am Korpus.
        let abstand = if zipfel.oben {
            ZIPFEL_HOEHE - 1 - y
        } else {
            y - (raster.hoehe - ZIPFEL_HOEHE)
        };
        let halb = ZIPFEL_BREITE as f32 / 2.0 * (1.0 - abstand as f32 / ZIPFEL_HOEHE as f32);
        for x in 0..raster.breite {
            if (x - mitte).abs() as f32 > halb {
                continue;
            }
            let index = ((y * raster.breite + x) * 4) as usize;
            raster.pixel[index..index + 4].copy_from_slice(&farbe);
        }
    }
}

/// Wohin der Zipfel gehoert, damit er zum Pet zeigt. Dieselbe Klemmung wie
/// `position_klemmen` -- eine zweite Rechnung waere eine zweite Fassung
/// derselben Regel, und geprueft waere erfahrungsgemaess die andere.
pub fn zipfel_fuer(
    pet: (i32, i32),
    pet_groesse: (i32, i32),
    bubble_groesse: (i32, i32),
    output_groesse: (i32, i32),
) -> Zipfel {
    let relativ = position_klemmen(pet, pet_groesse, bubble_groesse, output_groesse);
    let blase = (pet.0 + relativ.0, pet.1 + relativ.1);
    let pet_mitte = (
        pet.0 + pet_groesse.0 / 2,
        pet.1 + pet_groesse.1 / 2,
    );
    // Die Spitze folgt der Pet-Mitte, bleibt aber innerhalb der geraden Kante
    // zwischen den beiden Rundungen.
    let rand = ECKRADIUS as i32;
    let x = (pet_mitte.0 - blase.0 - ZIPFEL_BREITE / 2)
        .clamp(rand, (bubble_groesse.0 - rand - ZIPFEL_BREITE).max(rand));
    Zipfel {
        oben: blase.1 >= pet_mitte.1,
        x,
    }
}

/// Vorzeichenbehafteter Abstand zum abgerundeten Korpus. Negativ innen.
fn sdf_rundrect(px: f32, py: f32, oben: f32, unten: f32) -> f32 {
    let (mx, my) = (BREITE as f32 / 2.0, (oben + unten) / 2.0);
    let hx = BREITE as f32 / 2.0 - ECKRADIUS;
    let hy = (unten - oben) / 2.0 - ECKRADIUS;
    let qx = (px - mx).abs() - hx;
    let qy = (py - my).abs() - hy;
    qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - ECKRADIUS
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
pub(crate) fn premultipliziert(r: u8, g: u8, b: u8, a: u8) -> [u8; 4] {
    let kanal = |wert: u8| ((u16::from(wert) * u16::from(a) + 127) / 255) as u8;
    [kanal(b), kanal(g), kanal(r), a]
}

pub(crate) fn glyph_pixel(graustufe: u8, text_alpha: u8) -> [u8; 4] {
    let alpha = ((u16::from(graustufe) * u16::from(text_alpha) + 127) / 255) as u8;
    // Weisser Text: vormultiplizierte RGB-Kanaele entsprechen dem Alpha.
    [alpha, alpha, alpha, alpha]
}

pub(crate) fn ueberblenden(ziel: &mut [u8], quelle: [u8; 4]) {
    let inv = 255 - u16::from(quelle[3]);
    for kanal in 0..3 {
        ziel[kanal] = (u16::from(quelle[kanal]) + (u16::from(ziel[kanal]) * inv + 127) / 255) as u8;
    }
    ziel[3] = (u16::from(quelle[3]) + (u16::from(ziel[3]) * inv + 127) / 255) as u8;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn probe() -> Raster {
        BubbleRenderer::neu()
            .unwrap()
            .text_rendern("fertig", "Task durch.", false)
    }

    fn alpha(raster: &Raster, x: i32, y: i32) -> u8 {
        raster.pixel[((y * raster.breite + x) * 4 + 3) as usize]
    }

    #[test]
    fn breiter_text_bricht_um() {
        let renderer = BubbleRenderer::neu().unwrap();
        let raster = renderer.text_rendern(
            "",
            "Diese Zeile ist absichtlich deutlich breiter als die Sprechblase und muss umbrechen.",
            false,
        );
        assert!(raster.zeilen >= 2, "zeilen={}", raster.zeilen);
    }

    /// Positivkontrolle gegen das alte Rechteck: die Ecke MUSS durchsichtig
    /// sein und die Mitte gefuellt. Eine Pruefung nur der Ecke waere auch
    /// gruen, wenn gar nichts mehr gezeichnet wird.
    #[test]
    fn ecke_ist_durchsichtig_und_mitte_gefuellt() {
        let raster = probe();
        assert_eq!(alpha(&raster, 0, raster.hoehe - 1 - ZIPFEL_HOEHE), 0, "untere Ecke");
        assert_eq!(alpha(&raster, BREITE - 1, ZIPFEL_HOEHE), 0, "obere Ecke");
        assert!(alpha(&raster, BREITE / 2, raster.hoehe / 2) > 200, "Mitte");
    }

    /// Ohne Zipfel bleiben BEIDE Baender leer. Das ist die Positivkontrolle
    /// zu den Tests darunter: sie zeigen sonst nur, dass irgendwo Farbe ist.
    #[test]
    fn ohne_zipfel_sind_beide_baender_leer() {
        let raster = probe();
        for x in 0..BREITE {
            assert_eq!(alpha(&raster, x, 0), 0, "oben bei x={x}");
            assert_eq!(alpha(&raster, x, raster.hoehe - 1), 0, "unten bei x={x}");
        }
    }

    #[test]
    fn zipfel_oben_sitzt_an_der_gefragten_stelle() {
        let mut raster = probe();
        zipfel_malen(&mut raster, Zipfel { oben: true, x: 100 }, false);
        let mitte = 100 + ZIPFEL_BREITE / 2;
        assert!(alpha(&raster, mitte, 0) > 200, "Spitze");
        assert_eq!(alpha(&raster, mitte, raster.hoehe - 1), 0, "unten bleibt leer");
        // Die Basis ist breiter als die Spitze.
        assert_eq!(alpha(&raster, 100, 0), 0, "Rand der Spitze");
        assert!(alpha(&raster, 100, ZIPFEL_HOEHE - 1) > 200, "Rand der Basis");
    }

    #[test]
    fn zipfel_unten_sitzt_im_unteren_band() {
        let mut raster = probe();
        zipfel_malen(&mut raster, Zipfel { oben: false, x: 300 }, false);
        let mitte = 300 + ZIPFEL_BREITE / 2;
        assert!(alpha(&raster, mitte, raster.hoehe - 1) > 200, "Spitze");
        assert_eq!(alpha(&raster, mitte, 0), 0, "oben bleibt leer");
    }

    /// Der Kern der Zusage: die Blase liegt bevorzugt rechts unter dem Pet,
    /// und dann zeigt der Zipfel nach oben zur Pet-Mitte.
    #[test]
    fn ohne_klemmung_zeigt_der_zipfel_nach_oben_auf_die_pet_mitte() {
        let z = zipfel_fuer((100, 100), (80, 80), (BREITE, 120), (1920, 1080));
        assert!(z.oben);
        // Pet-Mitte 140 absolut, Blase beginnt bei 100+80+12 = 192 -- links
        // davon, also faehrt die Klemmung den Zipfel an den linken Rand.
        assert_eq!(z.x, ECKRADIUS as i32);
    }

    #[test]
    fn am_unteren_rand_kippt_der_zipfel_nach_unten() {
        // Bevorzugt waere y=1000+80+12=1092, die Blase ist 120 hoch und der
        // Output 1080: geklemmt auf 960, also OBERHALB der Pet-Mitte (1040).
        let z = zipfel_fuer((100, 1000), (80, 80), (BREITE, 120), (1920, 1080));
        assert!(!z.oben, "Blase liegt ueber dem Pet, Zipfel gehoert nach unten");
    }

    #[test]
    fn am_rechten_rand_wandert_der_zipfel_nach_rechts() {
        // Blase auf x=1500 geklemmt, Pet-Mitte bei 1740 -- der Zipfel muss
        // deutlich rechts der Blasenmitte sitzen.
        let z = zipfel_fuer((1700, 100), (80, 80), (BREITE, 120), (1920, 1080));
        assert!(
            z.x > BREITE / 2,
            "Zipfel bei x={} statt rechts von {}",
            z.x,
            BREITE / 2
        );
        assert!(z.x <= BREITE - ECKRADIUS as i32 - ZIPFEL_BREITE, "im Korpus");
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


