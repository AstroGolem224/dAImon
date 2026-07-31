//! Mood-Farben und endliche Render-Uebergaenge.
//!
//! Das Pet-Sheet enthaelt Posen, keine Mood-Zeilen. Deshalb wird jede bereits
//! premultiplizierte BGRA-Zelle beim Rendern getoent. Die Glutintensitaet ist
//! der Mittelwert aus dem staerksten Farbkanal und Alpha: So bleibt die
//! Helligkeitszeichnung des Community-Pets sichtbar, waehrend auch Pets mit
//! fast einfarbiger Vorlage deutlich verschiedene Farbtoene annehmen. Alle
//! Faktoren sind hoechstens 255 und die Intensitaet hoechstens Alpha; damit
//! bleibt jeder Ausgabekanal premultipliziert.

use std::time::{Duration, Instant};

/// 320 ms sind lang genug fuer mehrere 60-Hz-Frames und kurz genug, dass eine
/// Reaktion unmittelbar wirkt. Die feste Schranke ist zugleich wichtig fuer
/// die Ruhe-CPU: Nach diesem Zeitpunkt wird exakt der Zielwert gesetzt und
/// kein weiterer Frame-Callback angefordert.
pub const UEBERGANGSDAUER: Duration = Duration::from_millis(320);

#[cfg(test)]
const MOODS: [&str; 8] = [
    "sleeping",
    "idle",
    "observing",
    "thinking",
    "working",
    "done",
    "failed",
    "needs_input",
];

/// RGB-Multiplikatoren fuer dieselben Sprite-Pixel.
///
/// Gewaehlte Mood-Tabelle:
/// - sleeping (64, 80, 128): gedimmtes Blau wirkt ruhig und naechtlich.
/// - idle (150, 135, 110): neutrales, schwaches Warmweiss zeigt Bereitschaft.
/// - observing (90, 190, 220): kuehles Cyan steht fuer wache Aufmerksamkeit.
/// - thinking (150, 90, 220): Violett hebt Nachdenken klar vom Beobachten ab.
/// - working (255, 150, 55): kraeftiges Orange vermittelt aktive Glut.
/// - done (80, 220, 110): Gruen ist das eindeutige positive Abschlusssignal.
/// - failed (255, 55, 55): Rot signalisiert einen Fehler ohne Verwechslung.
/// - needs_input (255, 220, 70): helles Gelb fordert sichtbar Aufmerksamkeit.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Toenung {
    pub r: u8,
    pub g: u8,
    pub b: u8,
}

pub fn mood_toenung(mood: &str) -> Toenung {
    match mood {
        "sleeping" => Toenung {
            r: 64,
            g: 80,
            b: 128,
        },
        // MUTANT: observing bekommt die Toenung von thinking.
        "observing" => Toenung {
            r: 150,
            g: 90,
            b: 220,
        },
        "thinking" => Toenung {
            r: 150,
            g: 90,
            b: 220,
        },
        "working" => Toenung {
            r: 255,
            g: 150,
            b: 55,
        },
        "done" => Toenung {
            r: 80,
            g: 220,
            b: 110,
        },
        "failed" => Toenung {
            r: 255,
            g: 55,
            b: 55,
        },
        "needs_input" => Toenung {
            r: 255,
            g: 220,
            b: 70,
        },
        // Auch ein unbekannter Mood ist harmlos und sichtbar: idle.
        "idle" | _ => Toenung {
            r: 150,
            g: 135,
            b: 110,
        },
    }
}

fn kanal_toenen(intensitaet: u8, faktor: u8) -> u8 {
    ((u16::from(intensitaet) * u16::from(faktor) + 127) / 255) as u8
}

/// Toent premultiplizierte BGRA-Pixel. Alpha wird wortwoertlich kopiert.
pub fn frame_toenen(frame: &[u8], toenung: Toenung) -> Vec<u8> {
    let mut ausgabe = frame.to_vec();
    for pixel in ausgabe.chunks_exact_mut(4) {
        let staerkster_farbkanal = pixel[0].max(pixel[1]).max(pixel[2]);
        let intensitaet = ((u16::from(staerkster_farbkanal) + u16::from(pixel[3])) / 2) as u8;
        pixel[0] = kanal_toenen(intensitaet, toenung.b);
        pixel[1] = kanal_toenen(intensitaet, toenung.g);
        pixel[2] = kanal_toenen(intensitaet, toenung.r);
        // pixel[3] ist Alpha und bleibt absichtlich unveraendert.
    }
    ausgabe
}

#[derive(Clone, Copy, Debug)]
struct FarbUebergang {
    von: Toenung,
    nach: Toenung,
}

impl FarbUebergang {
    fn wert_nach(self, vergangen: Duration) -> (Toenung, bool) {
        if vergangen >= UEBERGANGSDAUER {
            return (self.nach, true);
        }
        let zaehler = vergangen.as_nanos();
        let nenner = UEBERGANGSDAUER.as_nanos();
        let kanal = |von: u8, nach: u8| {
            let von = i128::from(von);
            let delta = i128::from(nach) - von;
            let gerundet = if delta >= 0 {
                delta * zaehler as i128 + nenner as i128 / 2
            } else {
                delta * zaehler as i128 - nenner as i128 / 2
            };
            (von + gerundet / nenner as i128).clamp(0, 255) as u8
        };
        (
            Toenung {
                r: kanal(self.von.r, self.nach.r),
                g: kanal(self.von.g, self.nach.g),
                b: kanal(self.von.b, self.nach.b),
            },
            false,
        )
    }
}

/// Besitzt die beiden Flags aus dem Frame-Callback-Vertrag. `dirty` ist nur
/// waehrend eines endlichen Farbluebergangs wahr; `frame_pending` verhindert
/// doppelte Requests.
pub struct RenderSteuerung {
    aktuell: Toenung,
    uebergang: Option<FarbUebergang>,
    begonnen: Instant,
    pub dirty: bool,
    pub frame_pending: bool,
}

impl RenderSteuerung {
    pub fn neu(mood: &str, jetzt: Instant) -> Self {
        Self {
            aktuell: mood_toenung(mood),
            uebergang: None,
            begonnen: jetzt,
            dirty: false,
            frame_pending: false,
        }
    }

    pub fn ziel_setzen(&mut self, mood: &str, jetzt: Instant) -> bool {
        let (von, _) = self.wert(jetzt);
        let nach = mood_toenung(mood);
        if von == nach {
            self.uebergang = None;
            self.aktuell = nach;
            self.dirty = false;
            return false;
        }
        self.uebergang = Some(FarbUebergang { von, nach });
        self.begonnen = jetzt;
        self.dirty = true;
        true
    }

    pub fn wert(&mut self, jetzt: Instant) -> (Toenung, bool) {
        let Some(uebergang) = self.uebergang else {
            return (self.aktuell, true);
        };
        let (wert, fertig) = uebergang.wert_nach(jetzt.saturating_duration_since(self.begonnen));
        self.aktuell = wert;
        if fertig {
            self.uebergang = None;
            self.dirty = false;
        }
        (wert, fertig)
    }

    pub fn frame_empfangen(&mut self) {
        self.frame_pending = false;
    }

    /// Gibt genau einmal `true`, bis der entsprechende Callback eintrifft.
    pub fn callback_armieren(&mut self) -> bool {
        if !self.dirty || self.frame_pending {
            return false;
        }
        self.frame_pending = true;
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn acht_moods_ergeben_acht_literal_verschiedene_pixel() {
        let pixel = [80, 100, 120, 160];
        let erwartet = [
            [70, 44, 35, 160],
            [60, 74, 82, 160],
            [121, 104, 49, 160],
            [121, 49, 82, 160],
            [30, 82, 140, 160],
            [60, 121, 44, 160],
            [30, 30, 140, 160],
            [38, 121, 140, 160],
        ];
        let tatsaechlich: [[u8; 4]; 8] =
            MOODS.map(|mood| frame_toenen(&pixel, mood_toenung(mood)).try_into().unwrap());
        assert_eq!(tatsaechlich, erwartet);
        for links in 0..erwartet.len() {
            for rechts in links + 1..erwartet.len() {
                assert_ne!(erwartet[links], erwartet[rechts]);
            }
        }
    }

    #[test]
    fn alpha_und_sichtbare_flaeche_bleiben_fuer_alle_moods_identisch() {
        let frame = [10, 20, 30, 0, 80, 100, 120, 160, 1, 2, 3, 255];
        let alpha_vor: Vec<_> = frame.chunks_exact(4).map(|p| p[3]).collect();
        for mood in MOODS {
            let getoent = frame_toenen(&frame, mood_toenung(mood));
            let alpha_nach: Vec<_> = getoent.chunks_exact(4).map(|p| p[3]).collect();
            assert_eq!(alpha_nach, alpha_vor, "{mood}");
        }
    }

    #[test]
    fn unbekannter_mood_toent_exakt_wie_idle() {
        let pixel = [80, 100, 120, 160];
        assert_eq!(
            frame_toenen(&pixel, mood_toenung("nicht-im-protokoll")),
            [60, 74, 82, 160]
        );
        assert_eq!(mood_toenung("nicht-im-protokoll"), mood_toenung("idle"));
    }

    #[test]
    fn premultiplikation_gilt_nach_der_toenung() {
        // Literale Pixelprobe, nicht aus der Implementierungsformel abgeleitet.
        assert_eq!(
            frame_toenen(&[100, 120, 127, 127], mood_toenung("needs_input")),
            [35, 110, 127, 127]
        );
        for mood in MOODS {
            let pixel = frame_toenen(&[100, 120, 127, 127], mood_toenung(mood));
            assert!(pixel[0] <= pixel[3]);
            assert!(pixel[1] <= pixel[3]);
            assert!(pixel[2] <= pixel[3]);
        }
    }

    #[test]
    fn uebergang_erreicht_nach_fester_dauer_exakt_das_ziel_und_endet() {
        let start = Instant::now();
        let mut render = RenderSteuerung::neu("sleeping", start);
        assert!(render.ziel_setzen("failed", start));
        assert!(render.callback_armieren());
        render.frame_empfangen();

        let (mitte, fertig) = render.wert(start + UEBERGANGSDAUER / 2);
        assert_eq!(
            mitte,
            Toenung {
                r: 160,
                g: 67,
                b: 91
            }
        );
        assert!(!fertig);
        assert!(render.dirty);

        let (ziel, fertig) = render.wert(start + UEBERGANGSDAUER);
        assert_eq!(
            ziel,
            Toenung {
                r: 255,
                g: 55,
                b: 55
            }
        );
        assert!(fertig);
        assert!(!render.dirty);
        assert!(!render.callback_armieren());
    }
}
