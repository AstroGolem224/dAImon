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
        "observing" => Toenung {
            r: 90,
            g: 190,
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

/// Die Weiche: toent nur, wenn das Pet-Manifest es erlaubt.
///
/// Eigene Funktion und nicht ein `if` in `surface.rs`, weil dort kein Test
/// hinkommt -- der Aufrufer braucht eine Wayland-Verbindung. Ein `if`, das
/// niemand pruefen kann, ist ein `if`, das niemand prueft.
pub fn frame_toenen_wenn(frame: &[u8], toenung: Toenung, erlaubt: bool) -> Vec<u8> {
    if erlaubt {
        frame_toenen(frame, toenung)
    } else {
        frame.to_vec()
    }
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

/// Schaltet die Spalte eines animierten Moods weiter.
///
/// Der Takt gehoert nicht in die `RenderSteuerung`: der Farbuebergang dort ist
/// endlich und laeuft nach 320 ms aus, dieser hier laeuft, solange der Mood
/// gilt. Zwei Uhren mit verschiedener Lebensdauer in einer Struktur waeren
/// zwei Fassungen derselben Regel.
///
/// `spalten == 1` ist der Vertrag mit der Idle-CPU-Zusage aus T-1.5: ein Mood
/// mit einer Spalte hat keinen Takt, also auch keinen Timer und keinen
/// Frame-Callback. Das gilt ueber die Zahl, nicht ueber den Namen des Moods.
pub struct Animator {
    zeile: u32,
    spalten: u32,
    schrittdauer: Duration,
    spalte: u32,
    letzter_schritt: Instant,
}

impl Animator {
    /// `spalten` und `fps` kommen aus dem Pet-Manifest, also von ausserhalb.
    /// Null waere eine Division durch null bzw. ein Modulo durch null, darum
    /// hier die einzige Klemme -- weiter innen gibt es keine mehr.
    pub fn neu(zeile: u32, spalten: u32, fps: u32, jetzt: Instant) -> Self {
        Self {
            zeile,
            spalten: spalten.max(1),
            schrittdauer: Duration::from_secs(1) / fps.max(1),
            spalte: 0,
            letzter_schritt: jetzt,
        }
    }

    pub fn spalte(&self) -> u32 {
        self.spalte
    }

    /// Wie viele Bilder die gezeigte Zeile hat. Der Eingaberegion-Cache
    /// braucht die Zahl, weil dieselbe Zeile als Standbild eine andere
    /// Silhouette hat als im Lauf.
    pub fn spalten(&self) -> u32 {
        self.spalten
    }

    /// Abstand zweier Bilder. Der Timer braucht ihn, um sich selbst
    /// nachzulegen.
    pub fn schrittdauer(&self) -> Duration {
        self.schrittdauer
    }

    /// Ob ueberhaupt ein Takt noetig ist. Die Weiche fuer den Timer.
    pub fn laeuft(&self) -> bool {
        self.spalten > 1
    }

    /// Schaltet auf den Stand von `jetzt` und meldet, ob sich die Spalte
    /// geaendert hat -- das ist das `dirty` des Aufrufers.
    ///
    /// Es werden alle seit dem letzten Schritt faelligen Schritte auf einmal
    /// nachgeholt. Ein verspaeteter Weckruf darf die Schleife nicht bummeln
    /// lassen, sonst laeuft ein Loop unter Last langsamer als der naechste.
    pub fn tick(&mut self, jetzt: Instant) -> bool {
        if !self.laeuft() {
            return false;
        }
        let vergangen = jetzt.saturating_duration_since(self.letzter_schritt);
        let schritte = (vergangen.as_nanos() / self.schrittdauer.as_nanos()) as u32;
        if schritte == 0 {
            return false;
        }
        self.letzter_schritt += self.schrittdauer * schritte;
        let neue_spalte = (self.spalte + schritte) % self.spalten;
        let geaendert = neue_spalte != self.spalte;
        self.spalte = neue_spalte;
        geaendert
    }

    /// Beim Wechsel der Zeile oder der Bildzahl: Loop faengt vorn an.
    ///
    /// Unveraendert heisst hier: nichts tun. Der Aufrufer ruft das vor jedem
    /// Rendern auf, und ein bedingungsloses Zuruecksetzen wuerde die Spalte
    /// bei jedem Frame wieder auf 0 stellen -- die Animation stuende dann
    /// still und saehe aus wie der Zustand von vorher.
    pub fn mood_setzen(&mut self, zeile: u32, spalten: u32, fps: u32, jetzt: Instant) {
        if self.zeile == zeile && self.spalten == spalten.max(1) {
            return;
        }
        *self = Self::neu(zeile, spalten, fps, jetzt);
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

    /// 12 fps ergeben 83,3 ms je Schritt. Die Weckzeitpunkte liegen absichtlich
    /// auf 84 ms und nicht exakt auf dem Schritt: ein Timer trifft nie genau.
    #[test]
    fn tick_vier_spalten_bei_zwoelf_fps_schaltet_der_reihe_nach_und_kehrt_zurueck() {
        // GIVEN einen Animator mit vier Spalten bei 12 fps:
        let start = Instant::now();
        let mut animator = Animator::neu(0, 4, 12, start);
        assert_eq!(animator.spalte(), 0);
        assert!(animator.laeuft());

        // WHEN wir ihn viermal im Schritttakt wecken,
        // THEN durchlaeuft er 1, 2, 3 und landet wieder auf 0:
        let schritt = Duration::from_millis(84);
        for erwartet in [1, 2, 3, 0] {
            assert!(animator.tick(start + schritt * erwartet_index(erwartet)));
            assert_eq!(animator.spalte(), erwartet);
        }
    }

    /// Hilft nur dem Test oben: die Spalten 1,2,3,0 liegen auf den Weckrufen
    /// 1,2,3,4 -- die Null am Ende ist der vierte, nicht der nullte.
    fn erwartet_index(spalte: u32) -> u32 {
        if spalte == 0 {
            4
        } else {
            spalte
        }
    }

    /// Positivkontrolle zur Zusage aus T-1.5: ein Standbild-Mood hat keinen
    /// Takt. Ohne diesen Test waere „`idle` zappelt nicht" nur eine Behauptung
    /// ueber den Namen des Moods statt ueber seine Spaltenzahl.
    #[test]
    fn tick_eine_spalte_bleibt_auf_null_und_meldet_nie_eine_aenderung() {
        // GIVEN einen Animator mit genau einer Spalte:
        let start = Instant::now();
        let mut animator = Animator::neu(0, 1, 12, start);

        // WHEN wir ihn ueber eine volle Sekunde immer wieder wecken,
        // THEN meldet er nie eine Aenderung und laeuft nach eigener Auskunft nicht:
        assert!(!animator.laeuft());
        for ms in [84, 168, 500, 1000] {
            assert!(!animator.tick(start + Duration::from_millis(ms)), "{ms} ms");
            assert_eq!(animator.spalte(), 0, "{ms} ms");
        }
    }

    #[test]
    fn tick_verspaeteter_weckruf_holt_alle_faelligen_schritte_auf_einmal_nach() {
        // GIVEN einen Animator mit vier Spalten bei 12 fps:
        let start = Instant::now();
        let mut animator = Animator::neu(0, 4, 12, start);

        // WHEN der erste Weckruf erst nach 250 ms kommt -- drei Schritte spaet,
        // THEN steht er auf Spalte 3 und nicht auf 1:
        assert!(animator.tick(start + Duration::from_millis(250)));
        assert_eq!(animator.spalte(), 3);

        // WHEN weitere 500 ms ohne Weckruf vergehen -- sechs Schritte,
        // THEN laeuft der Loop einmal ganz durch und zwei darueber:
        assert!(animator.tick(start + Duration::from_millis(750)));
        assert_eq!(animator.spalte(), 1);
    }

    /// `animator_nachfuehren()` laeuft vor JEDEM Rendern. Setzte
    /// `mood_setzen` bedingungslos zurueck, stuende die Spalte dauerhaft auf 0
    /// -- die Animation saehe aus wie das Standbild von vorher, und zwar ohne
    /// dass irgendein anderer Test es merkt.
    #[test]
    fn mood_setzen_mit_unveraenderter_zeile_laesst_den_laufenden_loop_stehen() {
        // GIVEN einen Animator, der schon bei Spalte 2 steht:
        let start = Instant::now();
        let mut animator = Animator::neu(4, 8, 12, start);
        let jetzt = start + Duration::from_millis(168);
        animator.tick(jetzt);
        assert_eq!(animator.spalte(), 2);

        // WHEN dieselbe Zeile mit derselben Bildzahl erneut gesetzt wird,
        // THEN bleibt die Spalte stehen:
        animator.mood_setzen(4, 8, 12, jetzt);
        assert_eq!(animator.spalte(), 2);

        // WHEN dagegen die Bildzahl derselben Zeile wechselt,
        // THEN faengt der Loop vorn an:
        animator.mood_setzen(4, 6, 12, jetzt);
        assert_eq!(animator.spalte(), 0);
    }

    #[test]
    fn mood_setzen_nach_halbem_loop_faengt_mit_neuer_spaltenzahl_wieder_vorn_an() {
        // GIVEN einen Animator, der schon bei Spalte 2 steht:
        let start = Instant::now();
        let mut animator = Animator::neu(0, 4, 12, start);
        animator.tick(start + Duration::from_millis(168));
        assert_eq!(animator.spalte(), 2);

        // WHEN ein Standbild-Mood gesetzt wird,
        // THEN steht die Spalte wieder auf 0 und der Takt ist aus:
        animator.mood_setzen(7, 1, 12, start + Duration::from_millis(168));
        assert_eq!(animator.spalte(), 0);
        assert!(!animator.laeuft());
    }

    /// Positivkontrolle im selben Test: `true` MUSS die Pixel veraendern.
    /// Sonst waere „`false` laesst sie unveraendert" gruen, weil die Toenung
    /// gar nichts tut -- und nicht, weil der Schalter greift.
    #[test]
    fn toenung_aus_laesst_die_pixel_unveraendert() {
        let zelle: Vec<u8> = vec![40, 80, 120, 200, 10, 20, 30, 255];
        let violett = mood_toenung("thinking");

        let aus = frame_toenen_wenn(&zelle, violett, false);
        assert_eq!(aus, zelle);

        let an = frame_toenen_wenn(&zelle, violett, true);
        assert_ne!(an, zelle);
        assert_eq!(an, frame_toenen(&zelle, violett));
    }
}
