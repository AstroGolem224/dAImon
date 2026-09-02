//! Sprite-Atlas im hatch-pet-Format.
//!
//! Das PNG wird genau einmal beim Start dekodiert. Intern liegen die Pixel
//! bereits so, wie `wl_shm::Format::Argb8888` sie auf Little-Endian erwartet:
//! B, G, R, A mit premultiplizierten Farbkanälen.

use std::{
    collections::HashMap,
    fs::File,
    io::BufReader,
    path::{Path, PathBuf},
};

use serde_json::Value;

pub const STANDARD_CELL_W: u32 = 192;
pub const STANDARD_CELL_H: u32 = 208;
pub const STANDARD_COLS: u32 = 8;
pub const STANDARD_ROWS: u32 = 9;

const STANDARD_STATES: [(&str, u32); 9] = [
    ("idle", 0),
    ("run-right", 1),
    ("run-left", 2),
    ("waving", 3),
    ("jumping", 4),
    ("failed", 5),
    ("waiting", 6),
    ("running", 7),
    ("review", 8),
];

// Kein `Eq`: `toenung` ist ein f32, und f32 hat keine totale Gleichheit.
#[derive(Clone, Debug, PartialEq)]
pub struct AtlasLayout {
    pub cell_w: u32,
    pub cell_h: u32,
    pub cols: u32,
    pub rows: u32,
    pub states: HashMap<String, Zeileneintrag>,
    /// Optionaler zweiter Block: eine Zeile je MOOD statt je Pose. Leer heisst
    /// „dieses Pet hat keine Mood-Zeilen" -- dann gilt der Weg ueber
    /// `mood_zu_sprite` unveraendert. Ein eigener Block und nicht `states`,
    /// weil die Namen kollidieren: das Ember-Sheet hat eine Pose `failed`,
    /// die etwas anderes meint als der Mood `failed`.
    pub moods: HashMap<String, Zeileneintrag>,
    /// ANTEIL, nicht Schalter: 0.0 laesst das Sprite in Ruhe, 1.0 faerbt es
    /// voll, alles dazwischen mischt.
    ///
    /// Ein Schalter reichte, solange es zwei Faelle gab -- Ember, dessen
    /// Sheet gar keine Mood-Zeilen hat und wo die Farbe die ganze Information
    /// ist, und ein Gesichts-Pet, wo sie die Information zerstoert. Eine
    /// Halbfigur liegt dazwischen: gemessen am 01.09. unterscheiden sich dort
    /// zwei Moods im Bild nur um 1,3 bis 2,7 (beim Kopfausschnitt um 6,0), der
    /// Ausdruck traegt also kaum -- aber das Kostuem soll nicht verschwinden.
    pub toenung: f32,
    pub spritesheet_path: PathBuf,
}

impl Default for AtlasLayout {
    fn default() -> Self {
        Self {
            cell_w: STANDARD_CELL_W,
            cell_h: STANDARD_CELL_H,
            cols: STANDARD_COLS,
            rows: STANDARD_ROWS,
            states: STANDARD_STATES
                .into_iter()
                .map(|(name, row)| (name.to_owned(), Zeileneintrag::standbild(row)))
                .collect(),
            moods: HashMap::new(),
            toenung: 1.0,
            spritesheet_path: PathBuf::from("spritesheet.png"),
        }
    }
}

impl AtlasLayout {
    pub fn aus_manifest_text(text: &str) -> Self {
        let Ok(root) = serde_json::from_str::<Value>(text) else {
            return Self::default();
        };

        let mut layout = Self::default();
        if let Some(atlas) = root.get("atlas").and_then(Value::as_object) {
            layout.cell_w = positive_u32(atlas.get("cellW")).unwrap_or(STANDARD_CELL_W);
            layout.cell_h = positive_u32(atlas.get("cellH")).unwrap_or(STANDARD_CELL_H);
            layout.cols = positive_u32(atlas.get("cols")).unwrap_or(STANDARD_COLS);
            layout.rows = positive_u32(atlas.get("rows")).unwrap_or(STANDARD_ROWS);
        }

        if let Some(path) = root.get("spritesheetPath").and_then(Value::as_str) {
            if !path.is_empty() {
                layout.spritesheet_path = PathBuf::from(path);
            }
        }

        // Fehlt der Block ganz, gilt die hatch-pet-Vorgabetabelle. Ist er
        // vorhanden, ist er autoritativ; so kann ein Pet fehlende Zustände
        // kenntlich machen und der Aufrufer sauber auf idle zurückfallen.
        if let Some(states) = root.get("states").and_then(Value::as_object) {
            layout.states.clear();
            for (name, state) in states {
                if let Some(eintrag) = eintrag_lesen(state, layout.rows, layout.cols) {
                    layout.states.insert(name.clone(), eintrag);
                }
            }
        }

        // Der Mood-Block ist optional und fuegt nur hinzu: fehlt er, bleibt
        // die Karte leer und nichts am bisherigen Weg aendert sich.
        if let Some(moods) = root.get("moods").and_then(Value::as_object) {
            for (name, mood) in moods {
                if let Some(eintrag) = eintrag_lesen(mood, layout.rows, layout.cols) {
                    layout.moods.insert(name.clone(), eintrag);
                }
            }
        }

        // `true`/`false` bleiben gueltig -- die bestehenden Manifeste tragen
        // sie, und ein Feld, das nach einer Erweiterung anders gelesen wird
        // als vorher, ist eine stille Verhaltensaenderung.
        if let Some(wert) = root.get("toenung") {
            if let Some(an) = wert.as_bool() {
                layout.toenung = if an { 1.0 } else { 0.0 };
            } else if let Some(anteil) = wert.as_f64() {
                layout.toenung = (anteil as f32).clamp(0.0, 1.0);
            }
        }
        layout
    }

    pub fn aus_manifest_datei(path: &Path) -> Self {
        std::fs::read_to_string(path)
            .map(|text| Self::aus_manifest_text(&text))
            .unwrap_or_default()
    }

    pub fn sheet_pfad_neben(&self, manifest: &Path) -> PathBuf {
        manifest
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(&self.spritesheet_path)
    }

    fn idle_eintrag(&self) -> Zeileneintrag {
        self.states
            .get("idle")
            .copied()
            .unwrap_or_else(|| Zeileneintrag::standbild(0))
    }
}

/// Eine Zeile des Sheets samt der Frage, ob sie ein Standbild ist oder ein
/// Bewegungsloop.
///
/// `spalten` ist der Vertrag mit der Idle-CPU-Zusage aus T-1.5 (T-9.2): eine
/// Zeile mit einer Spalte bekommt keinen Takt, also keinen Timer und keinen
/// Frame-Callback. Die Zusage haengt damit an einer Zahl im Manifest, nicht am
/// Namen eines Moods.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Zeileneintrag {
    pub nummer: u32,
    pub spalten: u32,
}

impl Zeileneintrag {
    pub fn standbild(nummer: u32) -> Self {
        Self { nummer, spalten: 1 }
    }
}

/// `{"row": n}` mit n innerhalb des Sheets. Alles andere ist kein Eintrag.
///
/// `frames` ist optional; fehlt es, ist die Zeile ein Standbild. Das ist
/// bewusst die Vorgabe und nicht `cols`: sonst faengt jedes bestehende Pet mit
/// mehrspaltigem Sheet -- das mitgelieferte hat acht -- ungefragt an zu laufen,
/// auch im Ruhezustand. Mehr Bilder als das Sheet hat, gibt es nicht.
fn eintrag_lesen(eintrag: &Value, rows: u32, cols: u32) -> Option<Zeileneintrag> {
    let nummer = eintrag
        .get("row")
        .and_then(Value::as_u64)
        .and_then(|row| u32::try_from(row).ok())
        .filter(|row| *row < rows)?;
    let spalten = positive_u32(eintrag.get("frames"))
        .unwrap_or(1)
        .min(cols.max(1));
    Some(Zeileneintrag { nummer, spalten })
}

fn positive_u32(value: Option<&Value>) -> Option<u32> {
    value
        .and_then(Value::as_u64)
        .and_then(|wert| u32::try_from(wert).ok())
        .filter(|wert| *wert > 0)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ZustandsAbbildung {
    pub zeile: u32,
    /// Animationsbilder dieser Zeile, mindestens 1.
    pub spalten: u32,
    pub zurueckgefallen: bool,
}

impl ZustandsAbbildung {
    fn zurueckgefallen(eintrag: Zeileneintrag) -> Self {
        Self {
            zeile: eintrag.nummer,
            spalten: eintrag.spalten,
            zurueckgefallen: true,
        }
    }
}

/// `name` ist der bereits ueber `hub::mood_zu_sprite` verdichtete Zustand,
/// `mood` der rohe Mood aus dem Hub. Erklaert das Pet eine eigene Zeile fuer
/// diesen Mood, gilt sie -- sonst bleibt es beim alten Weg ueber die zwei
/// Posen. Zwei Argumente statt eines, weil `mood_zu_sprite` acht Moods auf
/// zwei Namen wirft und die verlorenen sechs genau die sind, die ein
/// Gesichts-Pet unterscheiden will.
pub fn zustand_abbilden(name: &str, mood: &str, layout: &AtlasLayout) -> ZustandsAbbildung {
    if let Some(eintrag) = layout.moods.get(mood).copied() {
        return ZustandsAbbildung {
            zeile: eintrag.nummer,
            spalten: eintrag.spalten,
            zurueckgefallen: false,
        };
    }
    let manifest_name = match name {
        "ruhig" => "idle",
        "dringend" => "waiting",
        _ => {
            return ZustandsAbbildung::zurueckgefallen(layout.idle_eintrag());
        }
    };
    match layout.states.get(manifest_name).copied() {
        Some(eintrag) => ZustandsAbbildung {
            zeile: eintrag.nummer,
            spalten: eintrag.spalten,
            zurueckgefallen: false,
        },
        None => ZustandsAbbildung::zurueckgefallen(layout.idle_eintrag()),
    }
}

// -- T-3.14: der Sprachzustand als Zusatz-Element -------------------------
//
// Der Mood waehlt die Atlas-Zeile wie bisher; der Sprachzustand malt danach
// einen Punkt in denselben Puffer, rechts oben. Damit ist "ueberlagert den
// Mood, ersetzt ihn nicht" keine Aussage, sondern messbar: `sprite` im
// Diagnose-Socket bleibt bei jedem Sprachzustand unveraendert.
//
// Kein neues Sprite-Set: dafuer braeuchte es Assets, die es nicht gibt, und
// Mood x Sprachzustand waere die vierfache Menge davon.

/// Kantenlaenge des Indikators, in Anteilen der Zellbreite.
const INDIKATOR_ANTEIL: u32 = 6;
/// Abstand zum Zellrand, in denselben Anteilen.
const INDIKATOR_RAND: u32 = 24;

/// Premultipliziertes BGRA je sichtbarem Zustand. `idle` steht nicht dabei --
/// die Abwesenheit einer Sprachphase malt nichts.
///
/// Die Farben sind Darstellung und stehen bewusst NICHT im Vertrag: wer sie
/// misst, friert Geschmack ein. Gemessen wird `voice_state` und der Zaehler.
fn indikator_farbe(zustand: &str) -> Option<[u8; 4]> {
    match zustand {
        "listening" => Some([80, 220, 120, 255]),  // gruen: das Mikrofon ist offen
        "processing" => Some([230, 180, 60, 255]), // blau-gelb: es denkt
        "speaking" => Some([90, 130, 240, 255]),   // rot-ish: es spricht
        _ => None,
    }
}

/// T-7.3: der Mitschnitt-Indikator, LINKS oben -- der Sprachindikator sitzt
/// rechts. Zwei Zusagen, zwei Punkte: „es hoert gerade zu" und „es zeichnet
/// auf" sind verschiedene Dinge, und ein Punkt, der beides bedeutet, sagt im
/// Zweifel keines von beiden.
///
/// Design 1.2 nennt die Sichtbarkeit als eine von drei nicht optionalen
/// Folgen des Dauermitschnitts -- und ausdruecklich am Sprite, nicht in einem
/// Einstellungsdialog.
const MITSCHNITT_FARBE: [u8; 4] = [60, 60, 235, 255]; // rot, premultipliziert

/// Malt den Mitschnitt-Punkt. Gibt zurueck, ob gemalt wurde.
pub fn mitschnitt_malen(frame: &mut [u8], aktiv: bool, breite: u32, hoehe: u32) -> bool {
    if !aktiv {
        return false;
    }
    let kante = breite / INDIKATOR_ANTEIL;
    let rand = breite / INDIKATOR_RAND;
    if kante == 0 || breite == 0 || hoehe == 0 {
        return false;
    }
    let (x0, y0) = (rand, rand);
    if x0 + kante > breite || y0 + kante > hoehe / 2 {
        return false;
    }
    if frame.len() < (breite * hoehe * 4) as usize {
        return false;
    }
    for y in y0..y0 + kante {
        for x in x0..x0 + kante {
            let i = ((y * breite + x) * 4) as usize;
            frame[i..i + 4].copy_from_slice(&MITSCHNITT_FARBE);
        }
    }
    true
}

/// Malt den Indikator in einen BGRA-Zellpuffer. Gibt zurueck, ob gemalt wurde.
///
/// `false` heisst: nichts angefasst -- entweder ist der Zustand `idle` oder
/// unbekannt, oder die Zelle ist zu klein. Ein Panic waere hier das
/// schlechteste Ergebnis: ein abgestuerztes Overlay ist schlimmer als ein
/// fehlender Punkt.
pub fn indikator_malen(frame: &mut [u8], zustand: &str, breite: u32, hoehe: u32) -> bool {
    let Some(farbe) = indikator_farbe(zustand) else {
        return false;
    };
    let kante = breite / INDIKATOR_ANTEIL;
    let rand = breite / INDIKATOR_RAND;
    if kante == 0 || breite == 0 || hoehe == 0 {
        return false;
    }
    let x0 = breite.saturating_sub(kante + rand);
    let y0 = rand;
    if x0 + kante > breite || y0 + kante > hoehe / 2 {
        // Der Indikator gehoert in die obere Haelfte. Passt er dort nicht
        // hinein, bleibt er weg, statt ins Gesicht zu rutschen.
        return false;
    }
    if frame.len() < (breite * hoehe * 4) as usize {
        return false;
    }
    for y in y0..y0 + kante {
        for x in x0..x0 + kante {
            let i = ((y * breite + x) * 4) as usize;
            frame[i..i + 4].copy_from_slice(&farbe);
        }
    }
    true
}

#[derive(Debug)]
pub struct SpriteAtlas {
    pub layout: AtlasLayout,
    breite: u32,
    hoehe: u32,
    bgra_premultiplied: Vec<u8>,
}

impl SpriteAtlas {
    pub fn laden(manifest: &Path) -> Result<Self, String> {
        let layout = AtlasLayout::aus_manifest_datei(manifest);
        let sheet = layout.sheet_pfad_neben(manifest);
        let datei = File::open(&sheet)
            .map_err(|fehler| format!("Sprite-Sheet {}: {fehler}", sheet.display()))?;
        let mut decoder = png::Decoder::new(BufReader::new(datei));
        decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
        let mut reader = decoder
            .read_info()
            .map_err(|fehler| format!("PNG-Header {}: {fehler}", sheet.display()))?;
        let mut ausgabe = vec![0; reader.output_buffer_size()];
        let info = reader
            .next_frame(&mut ausgabe)
            .map_err(|fehler| format!("PNG-Daten {}: {fehler}", sheet.display()))?;
        let rgba = rgba_aus_png(&ausgabe[..info.buffer_size()], info.color_type)?;
        let bgra_premultiplied = rgba
            .chunks_exact(4)
            .flat_map(|px| rgba_zu_argb8888_le([px[0], px[1], px[2], px[3]]))
            .collect();

        Ok(Self {
            layout,
            breite: info.width,
            hoehe: info.height,
            bgra_premultiplied,
        })
    }

    pub fn frame(&self, zeile: u32, spalte: u32) -> Result<Vec<u8>, String> {
        zelle_ausschneiden(
            &self.bgra_premultiplied,
            self.breite,
            self.hoehe,
            &self.layout,
            zeile,
            spalte,
        )
    }
}

fn rgba_aus_png(bytes: &[u8], farbe: png::ColorType) -> Result<Vec<u8>, String> {
    let rgba = match farbe {
        png::ColorType::Rgba => bytes.to_vec(),
        png::ColorType::Rgb => bytes
            .chunks_exact(3)
            .flat_map(|p| [p[0], p[1], p[2], 255])
            .collect(),
        png::ColorType::GrayscaleAlpha => bytes
            .chunks_exact(2)
            .flat_map(|p| [p[0], p[0], p[0], p[1]])
            .collect(),
        png::ColorType::Grayscale => bytes.iter().flat_map(|v| [*v, *v, *v, 255]).collect(),
        png::ColorType::Indexed => {
            return Err("indiziertes PNG wurde nicht zu RGB expandiert".into());
        }
    };
    Ok(rgba)
}

/// Exakte Ganzzahlformel: round(channel * alpha / 255).
pub fn rgba_zu_argb8888_le([r, g, b, a]: [u8; 4]) -> [u8; 4] {
    let premul = |kanal: u8| -> u8 { ((u16::from(kanal) * u16::from(a) + 127) / 255) as u8 };
    [premul(b), premul(g), premul(r), a]
}

pub fn zelle_ausschneiden(
    sheet: &[u8],
    sheet_w: u32,
    sheet_h: u32,
    layout: &AtlasLayout,
    zeile: u32,
    spalte: u32,
) -> Result<Vec<u8>, String> {
    let x = spalte
        .checked_mul(layout.cell_w)
        .ok_or_else(|| "Zellen-x uebergelaufen".to_string())?;
    let y = zeile
        .checked_mul(layout.cell_h)
        .ok_or_else(|| "Zellen-y uebergelaufen".to_string())?;
    let x2 = x
        .checked_add(layout.cell_w)
        .ok_or_else(|| "Zellenbreite uebergelaufen".to_string())?;
    let y2 = y
        .checked_add(layout.cell_h)
        .ok_or_else(|| "Zellenhoehe uebergelaufen".to_string())?;
    let erwartete_sheet_bytes = u64::from(sheet_w)
        .checked_mul(u64::from(sheet_h))
        .and_then(|pixel| pixel.checked_mul(4))
        .and_then(|bytes| usize::try_from(bytes).ok())
        .ok_or_else(|| "Sprite-Sheet ist zu gross".to_string())?;
    if x2 > sheet_w || y2 > sheet_h || sheet.len() < erwartete_sheet_bytes {
        return Err(format!(
            "Zelle ({zeile},{spalte}) liegt ausserhalb des Sprite-Sheets {sheet_w}x{sheet_h}"
        ));
    }

    let zeilenbytes = usize::try_from(layout.cell_w)
        .ok()
        .and_then(|w| w.checked_mul(4))
        .ok_or_else(|| "Zelle ist zu breit".to_string())?;
    let mut frame = Vec::with_capacity(
        zeilenbytes
            .checked_mul(layout.cell_h as usize)
            .ok_or_else(|| "Zelle ist zu gross".to_string())?,
    );
    for quell_y in y..y2 {
        let start = (u64::from(quell_y) * u64::from(sheet_w) + u64::from(x))
            .checked_mul(4)
            .and_then(|offset| usize::try_from(offset).ok())
            .ok_or_else(|| "Pixeloffset ist zu gross".to_string())?;
        frame.extend_from_slice(&sheet[start..start + zeilenbytes]);
    }
    Ok(frame)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pet_manifest_hat_erwartetes_layout() {
        let layout = AtlasLayout::aus_manifest_text(include_str!("../assets/pet.json"));
        assert_eq!(
            (layout.cell_w, layout.cell_h, layout.cols, layout.rows),
            (192, 208, 8, 9)
        );
        assert_eq!(layout.states.len(), 9);
        // Das mitgelieferte Sheet traegt seine Bildzahlen seit jeher im
        // hatch-pet-Format. Bis T-9.2 hat sie nur niemand gelesen.
        assert_eq!(
            layout.states["idle"],
            Zeileneintrag {
                nummer: 0,
                spalten: 6
            }
        );
        assert_eq!(
            layout.states["waiting"],
            Zeileneintrag {
                nummer: 6,
                spalten: 6
            }
        );
    }

    /// Ohne `frames` ein Standbild -- und zwar auch dann, wenn das Sheet acht
    /// Spalten hat. Die Vorgabe `cols` waere bequem und wuerde jedes
    /// bestehende Pet ungefragt in Bewegung setzen.
    #[test]
    fn eintrag_ohne_frames_ist_ein_standbild_trotz_mehrspaltigem_sheet() {
        // GIVEN ein achtspaltiges Sheet, dessen Moods nichts ueber Bilder sagen:
        let layout = AtlasLayout::aus_manifest_text(
            r#"{"atlas":{"cols":8,"rows":8},"moods":{"working":{"row":4}}}"#,
        );

        // WHEN der Eintrag gelesen wird,
        // THEN steht er auf einem Bild:
        assert_eq!(
            layout.moods["working"],
            Zeileneintrag {
                nummer: 4,
                spalten: 1
            }
        );
    }

    /// Positivkontrolle zum Test darueber: mit `frames` kommt die Zahl auch
    /// wirklich an. Sonst waere „ohne `frames` ein Standbild" gruen, weil das
    /// Feld gar nicht gelesen wird.
    #[test]
    fn eintrag_mit_frames_uebernimmt_die_zahl_und_deckelt_sie_auf_die_spalten() {
        // GIVEN ein achtspaltiges Sheet mit einer ehrlichen und einer
        // uebertriebenen Angabe:
        let layout = AtlasLayout::aus_manifest_text(
            r#"{"atlas":{"cols":8,"rows":8},
                "moods":{"working":{"row":4,"frames":6},
                         "failed":{"row":6,"frames":99}}}"#,
        );

        // WHEN beide gelesen werden,
        // THEN gilt die ehrliche Zahl, und die uebertriebene endet bei der
        // Spaltenzahl -- sonst liefe der Loop in Zellen, die es nicht gibt:
        assert_eq!(layout.moods["working"].spalten, 6);
        assert_eq!(layout.moods["failed"].spalten, 8);
    }

    #[test]
    fn fehlendes_oder_kaputtes_manifest_nutzt_vorgabe() {
        assert_eq!(
            AtlasLayout::aus_manifest_text("{kaputt"),
            AtlasLayout::default()
        );
        assert_eq!(
            AtlasLayout::aus_manifest_datei(Path::new("/definitiv/nicht/vorhanden")),
            AtlasLayout::default()
        );
    }

    #[test]
    fn atlas_zuschnitt_verwendet_zellenkoordinaten_und_prueft_grenzen() {
        let layout = AtlasLayout {
            cell_w: 2,
            cell_h: 2,
            cols: 3,
            rows: 2,
            ..AtlasLayout::default()
        };
        let mut sheet = vec![0; 6 * 4 * 4];
        // Markiert Pixel x=4,y=2, also Start der Zelle (r=1,c=2).
        let marker = ((2 * 6 + 4) * 4) as usize;
        sheet[marker..marker + 4].copy_from_slice(&[1, 2, 3, 4]);
        let frame = zelle_ausschneiden(&sheet, 6, 4, &layout, 1, 2).unwrap();
        assert_eq!(&frame[..4], &[1, 2, 3, 4]);
        assert!(zelle_ausschneiden(&sheet, 6, 4, &layout, 1, 3).is_err());
        assert!(zelle_ausschneiden(&sheet, 6, 4, &layout, 2, 0).is_err());
    }

    #[test]
    fn premultiplikation_hat_exakte_formel_und_randfaelle() {
        assert_eq!(rgba_zu_argb8888_le([255, 0, 0, 128]), [0, 0, 128, 128]);
        assert_eq!(rgba_zu_argb8888_le([12, 34, 56, 0]), [0, 0, 0, 0]);
        assert_eq!(rgba_zu_argb8888_le([12, 34, 56, 255]), [56, 34, 12, 255]);
        assert_eq!(rgba_zu_argb8888_le([101, 0, 0, 127])[2], 50);
    }

    #[test]
    fn argb8888_little_endian_ist_bgra_im_speicher() {
        let bytes = rgba_zu_argb8888_le([0x33, 0x22, 0x11, 0xff]);
        assert_eq!(bytes, [0x11, 0x22, 0x33, 0xff]);
        assert_eq!(u32::from_le_bytes(bytes), 0xff33_2211);
    }

    #[test]
    fn deutsche_zustaende_werden_auf_manifestzeilen_abgebildet() {
        let layout = AtlasLayout::default();
        assert_eq!(
            zustand_abbilden("ruhig", "idle", &layout),
            ZustandsAbbildung {
                zeile: 0,
                spalten: 1,
                zurueckgefallen: false
            }
        );
        assert_eq!(
            zustand_abbilden("dringend", "needs_input", &layout),
            ZustandsAbbildung {
                zeile: 6,
                spalten: 1,
                zurueckgefallen: false
            }
        );
        assert_eq!(
            zustand_abbilden("unbekannt", "gibt-es-nicht", &layout),
            ZustandsAbbildung {
                zeile: 0,
                spalten: 1,
                zurueckgefallen: true
            }
        );
    }

    /// Das Ember-Sheet hat eine POSE `failed` (Zeile 5). Der MOOD `failed`
    /// muss trotzdem auf `dringend`/Zeile 6 gehen, solange kein `moods`-Block
    /// da ist. Sonst haette der neue Weg das bestehende Pet still umgestellt.
    #[test]
    fn ohne_mood_block_kollidieren_gleiche_namen_nicht() {
        let layout = AtlasLayout::default();
        assert!(layout.moods.is_empty());
        assert_eq!(
            zustand_abbilden("dringend", "failed", &layout),
            ZustandsAbbildung {
                zeile: 6,
                spalten: 1,
                zurueckgefallen: false
            }
        );
    }

    /// Positivkontrolle zum Test darueber: derselbe Mood, dasselbe Sheet --
    /// nur mit `moods`-Block. Faende der neue Weg nichts, waere der Test
    /// oben gruen, ohne irgendetwas zu belegen.
    #[test]
    fn mood_zeile_schlaegt_die_pose() {
        let layout = AtlasLayout::aus_manifest_text(
            r#"{"atlas":{"cols":1,"rows":8},
                "moods":{"failed":{"row":5},"thinking":{"row":3}}}"#,
        );
        assert_eq!(
            zustand_abbilden("dringend", "failed", &layout),
            ZustandsAbbildung {
                zeile: 5,
                spalten: 1,
                zurueckgefallen: false
            }
        );
        assert_eq!(
            zustand_abbilden("ruhig", "thinking", &layout),
            ZustandsAbbildung {
                zeile: 3,
                spalten: 1,
                zurueckgefallen: false
            }
        );
        // Ein Mood ohne eigene Zeile faellt auf den alten Weg zurueck.
        assert_eq!(
            zustand_abbilden("ruhig", "working", &layout).zeile,
            layout.idle_eintrag().nummer
        );
    }

    /// Zeilen ausserhalb des Sheets sind kein Eintrag -- sonst liefe
    /// `frame()` in einen Fehler, und das Pet bliebe schwarz.
    #[test]
    fn mood_zeile_ausserhalb_des_sheets_zaehlt_nicht() {
        let layout = AtlasLayout::aus_manifest_text(
            r#"{"atlas":{"cols":1,"rows":8},"moods":{"failed":{"row":99}}}"#,
        );
        assert!(layout.moods.is_empty());
    }

    /// Der Anteil kommt aus dem Manifest -- als Wahrheitswert wie bisher ODER
    /// als Zahl. Die alte Schreibweise muss weiter gelten: die vorhandenen
    /// Manifeste tragen sie, und ein Feld, das nach einer Erweiterung anders
    /// gelesen wird als vorher, ist eine stille Verhaltensaenderung.
    #[test]
    fn toenung_liest_wahrheitswert_und_anteil() {
        let anteil = |text: &str| AtlasLayout::aus_manifest_text(text).toenung;
        assert_eq!(AtlasLayout::default().toenung, 1.0);
        assert_eq!(anteil(r#"{"toenung":true}"#), 1.0);
        assert_eq!(anteil(r#"{"toenung":false}"#), 0.0);
        assert_eq!(anteil(r#"{"toenung":0.4}"#), 0.4);
        assert_eq!(anteil(r#"{"toenung":0}"#), 0.0);
        assert_eq!(anteil(r#"{"toenung":1}"#), 1.0);
        // Geklemmt statt gerechnet, und Unsinn laesst die Vorgabe stehen.
        assert_eq!(anteil(r#"{"toenung":9}"#), 1.0);
        assert_eq!(anteil(r#"{"toenung":-3}"#), 0.0);
        assert_eq!(anteil(r#"{"toenung":"halb"}"#), 1.0);
        assert_eq!(anteil(r#"{}"#), 1.0);
    }

    /// Die NAHT: Hub-Snapshot -> Mood -> Zeile. Ohne diesen Test belegen die
    /// Tests darueber nur, dass die Funktion rechnet -- nicht, dass der Mood,
    /// den der Hub tatsaechlich schickt, jemals dort ankommt.
    #[test]
    fn mood_aus_dem_hub_erreicht_die_zeile() {
        let layout = AtlasLayout::aus_manifest_text(
            r#"{"atlas":{"cols":1,"rows":8},"moods":{"working":{"row":4}}}"#,
        );
        let zustand = crate::hub::snapshot_lesen(
            r#"{"v":2,"rev":7,"mood":"working","bubble":null}"#,
        )
        .expect("Snapshot ist gueltig");
        let name = crate::hub::mood_zu_sprite(&zustand.mood);
        assert_eq!(zustand_abbilden(name, &zustand.mood, &layout).zeile, 4);
    }

    /// Die NAHT zum Erzeuger: `tools/doppelself_gesichter.py` schreibt genau
    /// dieses Manifest, und sein Selbsttest vergleicht sich mit derselben
    /// Datei. Ohne diesen Test waeren die Tests darueber gruen, waehrend das
    /// Werkzeug ein Manifest erzeugt, das dieser Parser gar nicht liest.
    #[test]
    fn erzeugtes_doppelself_manifest_wird_verstanden() {
        let layout =
            AtlasLayout::aus_manifest_text(include_str!("../tests/doppelself-pet.json"));
        assert_eq!(layout.moods.len(), 8, "{:?}", layout.moods);
        assert_eq!(layout.toenung, 0.0, "das Kopf-Pet toent nicht");
        assert_eq!(layout.cols, 1);
        for mood in [
            "sleeping",
            "idle",
            "observing",
            "thinking",
            "working",
            "done",
            "failed",
            "needs_input",
        ] {
            let ab = zustand_abbilden(crate::hub::mood_zu_sprite(mood), mood, &layout);
            assert!(!ab.zurueckgefallen, "{mood} faellt zurueck");
            assert!(ab.zeile < layout.rows, "{mood} -> Zeile {}", ab.zeile);
        }
        // Acht Moods, acht verschiedene Zeilen -- sonst zeigt das Pet
        // zwei Zustaende mit demselben Gesicht an.
        let zeilen: std::collections::HashSet<u32> =
            layout.moods.values().map(|e| e.nummer).collect();
        assert_eq!(zeilen.len(), 8);
    }

    #[test]
    fn fehlendes_waiting_faellt_auf_idle_zurueck() {
        let layout = AtlasLayout::aus_manifest_text(
            r#"{"states":{"idle":{"row":3}},"spritesheetPath":"spritesheet.png"}"#,
        );
        assert_eq!(
            zustand_abbilden("dringend", "needs_input", &layout),
            ZustandsAbbildung {
                zeile: 3,
                spalten: 1,
                zurueckgefallen: true
            }
        );
    }
}

#[cfg(test)]
mod indikator_tests {
    use super::*;

    const W: u32 = 16;
    const H: u32 = 16;

    fn leerer_frame() -> Vec<u8> {
        vec![0u8; (W * H * 4) as usize]
    }

    #[test]
    fn idle_malt_nichts() {
        // `idle` ist die Abwesenheit einer Sprachphase. Ein Indikator dafuer
        // waere ein Zustand, der behauptet, es passiere etwas.
        let mut frame = leerer_frame();
        assert!(!indikator_malen(&mut frame, "idle", W, H));
        assert!(frame.iter().all(|b| *b == 0));
    }

    #[test]
    fn unbekannter_zustand_malt_nichts() {
        let mut frame = leerer_frame();
        assert!(!indikator_malen(&mut frame, "traeumt", W, H));
        assert!(frame.iter().all(|b| *b == 0));
    }

    #[test]
    fn die_drei_sichtbaren_malen_etwas() {
        for zustand in ["listening", "processing", "speaking"] {
            let mut frame = leerer_frame();
            assert!(indikator_malen(&mut frame, zustand, W, H), "{zustand}");
            assert!(frame.iter().any(|b| *b != 0), "{zustand}");
        }
    }

    #[test]
    fn die_drei_sind_unterscheidbar() {
        // Drei Zustaende, die gleich aussehen, sind einer.
        let mut gemalt: Vec<Vec<u8>> = Vec::new();
        for zustand in ["listening", "processing", "speaking"] {
            let mut frame = leerer_frame();
            indikator_malen(&mut frame, zustand, W, H);
            assert!(!gemalt.contains(&frame), "{zustand} sieht aus wie ein anderer");
            gemalt.push(frame);
        }
    }

    #[test]
    fn der_indikator_bleibt_oben_rechts() {
        // Die Zusage aus dem Plan: er UEBERLAGERT den Mood, er ersetzt ihn
        // nicht. Die untere Haelfte der Zelle bleibt deshalb unberuehrt.
        let mut frame = leerer_frame();
        indikator_malen(&mut frame, "speaking", W, H);
        let zeilenbytes = (W * 4) as usize;
        let untere_haelfte = &frame[(H / 2) as usize * zeilenbytes..];
        assert!(untere_haelfte.iter().all(|b| *b == 0));
        let linke_spalte: Vec<u8> = (0..H)
            .map(|y| frame[y as usize * zeilenbytes])
            .collect();
        assert!(linke_spalte.iter().all(|b| *b == 0));
    }

    // -- T-7.3: der Mitschnitt-Punkt --------------------------------------

    #[test]
    fn ohne_mitschnitt_wird_nichts_gemalt() {
        let mut frame = leerer_frame();
        assert!(!mitschnitt_malen(&mut frame, false, W, H));
        assert!(frame.iter().all(|b| *b == 0));
    }

    #[test]
    fn der_mitschnitt_punkt_sitzt_links_der_sprachpunkt_rechts() {
        // Zwei Zusagen, zwei Punkte -- und sie duerfen sich nicht ueberdecken,
        // sonst waere "es zeichnet auf" von "es hoert zu" nicht zu trennen.
        let mut nur_mitschnitt = leerer_frame();
        assert!(mitschnitt_malen(&mut nur_mitschnitt, true, W, H));
        let mut nur_voice = leerer_frame();
        assert!(indikator_malen(&mut nur_voice, "listening", W, H));

        let ueberlappung = nur_mitschnitt
            .iter()
            .zip(nur_voice.iter())
            .any(|(a, b)| *a != 0 && *b != 0);
        assert!(!ueberlappung, "die beiden Punkte ueberdecken sich");

        // Beide zusammen in EINEN Puffer: danach stehen beide da.
        let mut beide = leerer_frame();
        indikator_malen(&mut beide, "listening", W, H);
        mitschnitt_malen(&mut beide, true, W, H);
        for (i, byte) in nur_mitschnitt.iter().enumerate() {
            if *byte != 0 {
                assert_eq!(beide[i], *byte, "Mitschnitt-Punkt fehlt");
            }
        }
        for (i, byte) in nur_voice.iter().enumerate() {
            if *byte != 0 {
                assert_eq!(beide[i], *byte, "Sprachpunkt fehlt");
            }
        }
    }

    #[test]
    fn der_mitschnitt_punkt_bleibt_in_der_oberen_haelfte() {
        let mut frame = leerer_frame();
        mitschnitt_malen(&mut frame, true, W, H);
        let zeilenbytes = (W * 4) as usize;
        let untere_haelfte = &frame[(H / 2) as usize * zeilenbytes..];
        assert!(untere_haelfte.iter().all(|b| *b == 0));
    }

    #[test]
    fn ein_zu_kleiner_frame_wird_nicht_bemalt() {
        // Kein Panic an einer Zelle, die kleiner ist als der Indikator --
        // ein abgestuerztes Overlay ist schlimmer als ein fehlender Punkt.
        let mut winzig = vec![0u8; 4];
        assert!(!indikator_malen(&mut winzig, "listening", 1, 1));
        assert!(!mitschnitt_malen(&mut winzig, true, 1, 1));
    }
}
