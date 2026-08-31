//! CPU-gerasterte Sprechblase fuer eine eigene Wayland-Subsurface.
//!
//! Der Hub liefert bereits gesaeubertes ASCII. Dieses Modul macht deshalb
//! ausschliesslich Layout und Pixel: keine zweite Unicode-Policy, keine GPU
//! und keine System-Fontsuche.

use fontdue::{
    layout::{CoordinateSystem, Layout, LayoutSettings, TextStyle, WrapStyle},
    Font, FontSettings,
};

pub const BREITE: i32 = 448;
const INNEN_ABSTAND: i32 = 18;
/// Titel und Fliesstext unterscheiden sich in der Groesse, nicht im Schnitt --
/// eingebettet ist genau ein Font, und ein zweiter waere ein zweites
/// `include_bytes!` im Binary.
const SCHRIFT_TITEL: f32 = 16.5;
const SCHRIFT_TEXT: f32 = 13.5;
const ZEILEN_HOEHE: f32 = 1.35;
const MIN_HOEHE: i32 = 78;
const PET_ABSTAND: i32 = 12;

// -- Form der Blase --------------------------------------------------------
const ECKRADIUS: f32 = 14.0;
/// Wie weit der Zipfel aus dem Korpus ragt.
const ZIPFEL_HOEHE: i32 = 14;
/// Breite seiner Basis am Korpus.
const ZIPFEL_BREITE: i32 = 22;

/// An welcher Kante der Zipfel sitzt.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kante {
    Oben,
    Unten,
    Links,
    Rechts,
}

/// Wo der Zipfel sitzt. Er zeigt zur Pet-Mitte, und die liegt je nach
/// Klemmung ueber, unter oder NEBEN der Blase -- im Betrieb ist "neben" der
/// haeufigste Fall, weil die Blase bevorzugt rechts vom Pet steht.
///
/// Der Zipfel wird NACH dem Korpus gemalt (`zipfel_malen`): die Blasengroesse
/// haengt nur am Text, die Kante nur an der Klemmung. Wer beides in einem Zug
/// rendern wollte, braeuchte die Groesse, bevor er sie hat.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Zipfel {
    pub kante: Kante,
    /// Anfang der Basis auf dieser Kante, in Blasenkoordinaten: bei `Oben`
    /// und `Unten` ein x, bei `Links` und `Rechts` ein y.
    pub pos: i32,
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
            x: (INNEN_ABSTAND + ZIPFEL_HOEHE) as f32,
            y: (INNEN_ABSTAND + ZIPFEL_HOEHE) as f32,
            max_width: Some((BREITE - 2 * (INNEN_ABSTAND + ZIPFEL_HOEHE)) as f32),
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
        // An ALLEN VIER Kanten bleibt ein Band frei: welche davon der Zipfel
        // belegt, steht erst nach dem Klemmen fest, und die Blasengroesse
        // darf davon nicht abhaengen -- sonst braeuchte das Klemmen eine
        // Groesse, die es selbst erst bestimmt.
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
    for y in ZIPFEL_HOEHE..(hoehe - ZIPFEL_HOEHE) {
        for x in ZIPFEL_HOEHE..(BREITE - ZIPFEL_HOEHE) {
            let (px, py) = (x as f32 + 0.5, y as f32 + 0.5);
            let deckung = (0.5 - sdf_rundrect(px, py, hoehe)).clamp(0.0, 1.0);
            if deckung <= 0.0 {
                continue;
            }
            let alpha = (f32::from(a) * deckung).round() as u8;
            let index = ((y * BREITE + x) * 4) as usize;
            pixel[index..index + 4].copy_from_slice(&premultipliziert(r, g, b, alpha));
        }
    }
}

/// Vorzeichenbehafteter Abstand zum abgerundeten Korpus. Negativ innen.
/// Der Korpus ist die Blase ohne die vier Zipfelbaender.
fn sdf_rundrect(px: f32, py: f32, hoehe: i32) -> f32 {
    let rand = ZIPFEL_HOEHE as f32;
    let (mx, my) = (BREITE as f32 / 2.0, hoehe as f32 / 2.0);
    let hx = (BREITE as f32 - 2.0 * rand) / 2.0 - ECKRADIUS;
    let hy = (hoehe as f32 - 2.0 * rand) / 2.0 - ECKRADIUS;
    let qx = (px - mx).abs() - hx;
    let qy = (py - my).abs() - hy;
    qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - ECKRADIUS
}

/// Malt den Zipfel in das freie Band an der Kante, die `zipfel` nennt.
///
/// Getrennt vom Korpus, weil die Kante erst nach dem Klemmen feststeht. Ohne
/// Kantenglaettung: das Dreieck ist 14 px hoch, und der harte Rand faellt
/// neben dem weichen Korpus nicht auf.
pub fn zipfel_malen(raster: &mut Raster, zipfel: Zipfel, urgent: bool) {
    let (r, g, b, a) = blasenfarbe(urgent);
    let farbe = premultipliziert(r, g, b, a);
    let mitte = zipfel.pos + ZIPFEL_BREITE / 2;
    let (breite, hoehe) = (raster.breite, raster.hoehe);
    for y in 0..hoehe {
        for x in 0..breite {
            // `abstand` ist der Weg aus dem Korpus heraus: 0 an der Basis,
            // ZIPFEL_HOEHE-1 an der Spitze. `quer` laeuft entlang der Kante.
            let (abstand, quer) = match zipfel.kante {
                Kante::Oben => (ZIPFEL_HOEHE - 1 - y, x),
                Kante::Unten => (y - (hoehe - ZIPFEL_HOEHE), x),
                Kante::Links => (ZIPFEL_HOEHE - 1 - x, y),
                Kante::Rechts => (x - (breite - ZIPFEL_HOEHE), y),
            };
            if !(0..ZIPFEL_HOEHE).contains(&abstand) {
                continue;
            }
            let halb = ZIPFEL_BREITE as f32 / 2.0 * (1.0 - abstand as f32 / ZIPFEL_HOEHE as f32);
            if (quer - mitte).abs() as f32 > halb {
                continue;
            }
            let index = ((y * breite + x) * 4) as usize;
            raster.pixel[index..index + 4].copy_from_slice(&farbe);
        }
    }
}

/// Wohin der Zipfel gehoert, damit er zur Pet-Mitte zeigt. Dieselbe Klemmung
/// wie `position_klemmen` -- eine zweite Rechnung waere eine zweite Fassung
/// derselben Regel, und geprueft waere erfahrungsgemaess die andere.
///
/// Gewaehlt wird die Kante, die der Strahl von der Blasenmitte zur Pet-Mitte
/// DURCHSTOESST, und die Basis sitzt auf dem Durchstosspunkt. Die naheliegende
/// Regel "die Kante, ueber die das Pet am weitesten hinausragt" ist nicht
/// dasselbe: liegt das Pet schraeg -- der Normalfall, die Blase steht ja
/// rechts UNTERHALB -- entscheiden dann zwei fast gleiche Zahlen, und der
/// Zipfel landet an der Kante, die zufaellig um ein paar Pixel gewinnt.
pub fn zipfel_fuer(
    pet: (i32, i32),
    pet_groesse: (i32, i32),
    bubble_groesse: (i32, i32),
    output_groesse: (i32, i32),
) -> Zipfel {
    let relativ = position_klemmen(pet, pet_groesse, bubble_groesse, output_groesse);
    let blase = (pet.0 + relativ.0, pet.1 + relativ.1);
    let (bw, bh) = bubble_groesse;
    let blasenmitte = (blase.0 + bw / 2, blase.1 + bh / 2);
    let petmitte = (pet.0 + pet_groesse.0 / 2, pet.1 + pet_groesse.1 / 2);
    let (dx, dy) = (petmitte.0 - blasenmitte.0, petmitte.1 - blasenmitte.1);

    // Wie weit der Strahl skaliert werden muss, um die senkrechte bzw. die
    // waagerechte Kante zu erreichen. Der kleinere Wert gewinnt -- das ist
    // die Kante, die zuerst kommt. `INFINITY` fuer eine Richtung ohne
    // Bewegung: sie wird nie erreicht.
    let bis_senkrecht = if dx == 0 {
        f32::INFINITY
    } else {
        (bw / 2) as f32 / dx.abs() as f32
    };
    let bis_waagerecht = if dy == 0 {
        f32::INFINITY
    } else {
        (bh / 2) as f32 / dy.abs() as f32
    };
    if !bis_senkrecht.is_finite() && !bis_waagerecht.is_finite() {
        // Pet-Mitte genau auf der Blasenmitte: keine Richtung ist besser als
        // die andere.
        return Zipfel {
            kante: Kante::Oben,
            pos: ZIPFEL_HOEHE + ECKRADIUS as i32,
        };
    }
    let t = bis_senkrecht.min(bis_waagerecht);
    let (kante, ziel, laenge) = if bis_senkrecht <= bis_waagerecht {
        let kante = if dx > 0 { Kante::Rechts } else { Kante::Links };
        let treffer = blasenmitte.1 + (dy as f32 * t) as i32;
        (kante, treffer - blase.1, bh)
    } else {
        let kante = if dy > 0 { Kante::Unten } else { Kante::Oben };
        let treffer = blasenmitte.0 + (dx as f32 * t) as i32;
        (kante, treffer - blase.0, bw)
    };

    // Die Basis bleibt auf dem geraden Stueck zwischen den beiden Rundungen;
    // an einer Rundung sieht der Zipfel abgerissen aus.
    let von = ZIPFEL_HOEHE + ECKRADIUS as i32;
    let bis = (laenge - ZIPFEL_HOEHE - ECKRADIUS as i32 - ZIPFEL_BREITE).max(von);
    Zipfel {
        kante,
        pos: (ziel - ZIPFEL_BREITE / 2).clamp(von, bis),
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
        let z = ZIPFEL_HOEHE;
        assert_eq!(alpha(&raster, z, raster.hoehe - 1 - z), 0, "untere Ecke");
        assert_eq!(alpha(&raster, BREITE - 1 - z, z), 0, "obere Ecke");
        assert!(alpha(&raster, BREITE / 2, raster.hoehe / 2) > 200, "Mitte");
    }

    /// Ohne Zipfel bleiben ALLE VIER Baender leer. Das ist die
    /// Positivkontrolle zu den Tests darunter: sie zeigen sonst nur, dass
    /// irgendwo Farbe ist.
    #[test]
    fn ohne_zipfel_sind_alle_vier_baender_leer() {
        let raster = probe();
        for x in 0..BREITE {
            assert_eq!(alpha(&raster, x, 0), 0, "oben bei x={x}");
            assert_eq!(alpha(&raster, x, raster.hoehe - 1), 0, "unten bei x={x}");
        }
        for y in 0..raster.hoehe {
            assert_eq!(alpha(&raster, 0, y), 0, "links bei y={y}");
            assert_eq!(alpha(&raster, BREITE - 1, y), 0, "rechts bei y={y}");
        }
    }

    /// Jede Kante malt in IHR Band und in kein anderes, und die Basis ist
    /// breiter als die Spitze.
    #[test]
    fn jede_kante_malt_nur_ihr_eigenes_band() {
        for kante in [Kante::Oben, Kante::Unten, Kante::Links, Kante::Rechts] {
            let mut raster = probe();
            let pos = 40;
            zipfel_malen(&mut raster, Zipfel { kante, pos }, false);
            let mitte = pos + ZIPFEL_BREITE / 2;
            let (spitze, basis, fremd) = match kante {
                Kante::Oben => ((mitte, 0), (mitte, ZIPFEL_HOEHE - 1), (mitte, raster.hoehe - 1)),
                Kante::Unten => (
                    (mitte, raster.hoehe - 1),
                    (mitte, raster.hoehe - ZIPFEL_HOEHE),
                    (mitte, 0),
                ),
                Kante::Links => ((0, mitte), (ZIPFEL_HOEHE - 1, mitte), (BREITE - 1, mitte)),
                Kante::Rechts => (
                    (BREITE - 1, mitte),
                    (BREITE - ZIPFEL_HOEHE, mitte),
                    (0, mitte),
                ),
            };
            assert!(alpha(&raster, spitze.0, spitze.1) > 200, "{kante:?} Spitze");
            assert!(alpha(&raster, basis.0, basis.1) > 200, "{kante:?} Basis");
            assert_eq!(alpha(&raster, fremd.0, fremd.1), 0, "{kante:?} fremdes Band");
        }
    }

    /// Der Kern der Zusage und der Befund vom 31.08.: die Blase steht
    /// bevorzugt rechts unterhalb des Pet, wird am unteren Bildschirmrand
    /// aber hochgeklemmt und liegt dann NEBEN ihm. Das ist die haeufigste
    /// Lage im Betrieb -- eine Fassung mit nur oben/unten zeigte hier ins
    /// Leere. Die Zahlen sind die echten: Pet 192x208 auf (0, 1180),
    /// HDMI-A-1.
    #[test]
    fn bevorzugte_lage_setzt_den_zipfel_an_die_linke_kante() {
        let z = zipfel_fuer((0, 1180), (192, 208), (BREITE, 130), (2560, 1440));
        assert_eq!(z.kante, Kante::Links);
    }

    #[test]
    fn pet_rechts_der_blase_setzt_den_zipfel_nach_rechts() {
        // Am rechten Rand wird die Blase nach links geklemmt; das Pet bleibt
        // rechts daneben.
        let z = zipfel_fuer((2400, 1300), (192, 208), (BREITE, 130), (2560, 1440));
        assert_eq!(z.kante, Kante::Rechts);
    }

    #[test]
    fn pet_ueber_der_blase_setzt_den_zipfel_nach_oben() {
        // Ein Output, der nur so breit ist wie die Blase: sie kann nicht
        // seitlich ausweichen und liegt unter dem Pet.
        let z = zipfel_fuer((0, 0), (192, 208), (BREITE, 130), (BREITE, 1440));
        assert_eq!(z.kante, Kante::Oben);
    }

    #[test]
    fn pet_unter_der_blase_setzt_den_zipfel_nach_unten() {
        let z = zipfel_fuer((0, 1380), (192, 208), (BREITE, 130), (BREITE, 1440));
        assert_eq!(z.kante, Kante::Unten);
    }

    /// Der eigentliche Zweck: die Basis liegt dort, wo der Strahl zur
    /// Pet-Mitte die Kante trifft -- nicht in der Ecke und nicht in der
    /// Mitte. Ohne diese Pruefung waere ein Zipfel, der immer am
    /// Kantenanfang klebt, bei allen Tests oben gruen.
    #[test]
    fn die_basis_trifft_die_pet_mitte() {
        // Beide Pets liegen links neben der hochgeklemmten Blase, das zweite
        // 50 px tiefer. Der Zipfel muss auf der linken Kante mitwandern.
        let hoch = zipfel_fuer((0, 1240), (192, 208), (BREITE, 130), (2560, 1440));
        let tief = zipfel_fuer((0, 1290), (192, 208), (BREITE, 130), (2560, 1440));
        assert_eq!(hoch.kante, Kante::Links);
        assert_eq!(tief.kante, Kante::Links);
        assert!(
            tief.pos > hoch.pos,
            "tieferes Pet muss den Zipfel nach unten schieben: {} vs {}",
            tief.pos,
            hoch.pos
        );
        // Beide echt zwischen den Rundungen -- sonst zeigte der Vergleich
        // oben nur, dass zwei Klemmwerte verschieden sind.
        let (von, bis) = (
            ZIPFEL_HOEHE + ECKRADIUS as i32,
            130 - ZIPFEL_HOEHE - ECKRADIUS as i32 - ZIPFEL_BREITE,
        );
        for z in [hoch, tief] {
            assert!(z.pos > von && z.pos < bis, "pos={} liegt am Anschlag", z.pos);
        }
    }

    /// Die Basis bleibt auf dem geraden Stueck der Kante -- sonst haengt der
    /// Zipfel an einer Rundung und sieht abgerissen aus.
    #[test]
    fn die_basis_bleibt_zwischen_den_rundungen() {
        let grenze = ZIPFEL_HOEHE + ECKRADIUS as i32;
        for pet_y in [0, 400, 1300] {
            let z = zipfel_fuer((100, pet_y), (192, 208), (BREITE, 130), (2560, 1440));
            let laenge = match z.kante {
                Kante::Links | Kante::Rechts => 130,
                Kante::Oben | Kante::Unten => BREITE,
            };
            assert!(z.pos >= grenze, "pet_y={pet_y}: pos={} zu klein", z.pos);
            assert!(
                z.pos + ZIPFEL_BREITE <= laenge - grenze,
                "pet_y={pet_y}: pos={} zu gross",
                z.pos
            );
        }
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


