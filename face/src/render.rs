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

/// Toent zu dem Anteil, den das Pet-Manifest angibt.
///
/// Eigene Funktion und nicht ein `if` in `surface.rs`, weil dort kein Test
/// hinkommt -- der Aufrufer braucht eine Wayland-Verbindung. Ein `if`, das
/// niemand pruefen kann, ist ein `if`, das niemand prueft.
pub fn frame_toenen_anteilig(frame: &[u8], toenung: Toenung, anteil: f32) -> Vec<u8> {
    let anteil = anteil.clamp(0.0, 1.0);
    if anteil <= 0.0 {
        return frame.to_vec();
    }
    if anteil >= 1.0 {
        return frame_toenen(frame, toenung);
    }
    // Zwischen Original und Vollton linear mischen. Beide Seiten sind mit
    // DEMSELBEN Alpha vormultipliziert, und Vormultiplikation ist linear in
    // der Farbe -- die Mischung ist deshalb wieder ein gueltiges
    // vormultipliziertes Pixel. Ohne diese Eigenschaft muesste hier erst
    // dividiert, gemischt und wieder multipliziert werden.
    let voll = frame_toenen(frame, toenung);
    let mut ausgabe = frame.to_vec();
    for (ziel, getoent) in ausgabe.chunks_exact_mut(4).zip(voll.chunks_exact(4)) {
        for kanal in 0..3 {
            let a = f32::from(ziel[kanal]);
            let b = f32::from(getoent[kanal]);
            ziel[kanal] = (a + (b - a) * anteil).round().clamp(0.0, 255.0) as u8;
        }
        // Alpha bleibt woertlich: die Toenung aendert die Deckung nicht, und
        // ein gemischtes Alpha wuerde die Silhouette ausfransen.
    }
    ausgabe
}

/// Wie stark der Mood-Farbton JETZT liegen darf.
///
/// Der Wert aus dem Manifest gilt nur, solange ein Emote laeuft; im
/// Ruhezustand ist er null. Ein Pet, das dauerhaft gruen, rot oder gelb
/// ueberlagert dasteht, sagt nichts mehr aus -- die Farbe soll den Moment
/// markieren, in dem sich etwas geaendert hat, nicht den Normalfall.
///
/// Eigene Funktion und nicht ein `if` in `surface.rs`, aus demselben Grund
/// wie bei `frame_toenen_anteilig`: dorthin kommt kein Test.
pub fn toenungsanteil(manifest: f32, emote_laeuft: bool) -> f32 {
    if emote_laeuft {
        manifest.clamp(0.0, 1.0)
    } else {
        0.0
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
/// Wie oft ein Emote nach einem Moodwechsel spielt.
///
/// Fest drei und nicht wechselnd: eine feste Zahl ist pruefbar -- nach genau
/// drei Umlaeufen steht die Atemzeile im Diagnose-Socket. Eine zufaellige
/// waere eine Zusage, die sich nur statistisch belegen laesst, und das Face
/// hat bis heute keine Zufallsquelle.
pub const EMOTE_UMLAEUFE: u32 = 3;

/// Der Takt des Atems. Er ist so hoch wie der der Geste, und das ist die
/// Korrektur eines Denkfehlers vom 02.09.
///
/// Damals stand hier 3. Die Ueberlegung war: ein ruhiger Atem braucht wenige
/// Bilder. Sie ist falsch. Ruhe ist eine Frage der AMPLITUDE, nicht der
/// Bildrate -- wer dieselbe Bewegung viermal seltener zeigt, bekommt keinen
/// ruhigen Atem, sondern dieselben Spruenge mit sichtbaren Luecken dazwischen.
/// Genau so kam es an: „die ruckeln jetzt alle". Die Atemzeile war ohnehin
/// fuer zwoelf Bilder gebaut (`SCHRITT_ATEM` in `tools/pet_animieren.py`).
///
/// Langsam wird der Atem jetzt dort, wo er entsteht: der Clip nimmt JEDEN
/// Frame statt jeden zweiten, das sind 41 Spalten und bei 12 fps ein Atemzug
/// von 3,4 s, also rund achtzehn pro Minute.
///
/// Die Idle-CPU-Zusage aus T-1.5 (unter 1,0 %, nachgefahren von T-2.1, T-2.2
/// und T-2.4) haengt an dieser Zahl. Sie hielt vor dem 02.09. schon einmal
/// bei zwoelf Bildern; die Konstante bleibt trotzdem eigenstaendig, damit
/// jemand sie senken kann, ohne die Geste mitzunehmen.
pub const ATEM_FPS: u32 = 12;

/// Eine Zeile des Sheets samt ihrer Bildzahl.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Spur {
    pub zeile: u32,
    pub spalten: u32,
}

pub struct Animator {
    atem: Spur,
    /// Die Zeile, die nach einem Moodwechsel spielt. `None` heisst: nur Atem.
    emote: Option<Spur>,
    /// Verbleibende Emote-Umlaeufe. 0 heisst: es wird geatmet.
    emote_umlaeufe: u32,
    /// Zwei Dauern statt einer: der Atem hat seinen eigenen, langsamen Takt.
    /// Eine gemeinsame waere entweder ein hektischer Ruhezustand oder ein
    /// Emote in Zeitlupe.
    atem_dauer: Duration,
    emote_dauer: Duration,
    spalte: u32,
    letzter_schritt: Instant,
}

impl Animator {
    /// `emote_fps` heisst so, weil es NUR das Emote taktet. Der Atem laeuft
    /// immer mit [`ATEM_FPS`] -- er ist der Ruhezustand, und dessen Tempo ist
    /// keine Eigenschaft eines einzelnen Moods, sondern die Bedingung, unter
    /// der die Idle-CPU-Zusage aus T-1.5 haelt. Ein Manifest soll sie nicht
    /// hochdrehen koennen.
    ///
    /// Null waere eine Division durch null bzw. ein Modulo durch null, darum
    /// hier die einzige Klemme -- weiter innen gibt es keine mehr.
    pub fn neu(atem: Spur, emote: Option<Spur>, emote_fps: u32, jetzt: Instant) -> Self {
        // Ein Emote mit einer einzigen Spalte ist ein Standbild und kein
        // Emote; es waere zweimal dasselbe Bild und danach ein Sprung.
        let emote = emote.filter(|spur| spur.spalten > 1);
        Self {
            atem: Spur {
                zeile: atem.zeile,
                spalten: atem.spalten.max(1),
            },
            emote,
            emote_umlaeufe: if emote.is_some() { EMOTE_UMLAEUFE } else { 0 },
            atem_dauer: Duration::from_secs(1) / ATEM_FPS.max(1),
            emote_dauer: Duration::from_secs(1) / emote_fps.max(1),
            spalte: 0,
            letzter_schritt: jetzt,
        }
    }

    /// Die gerade gueltige Spur: das Emote, solange Umlaeufe offen sind,
    /// sonst der Atem.
    fn spur(&self) -> Spur {
        match self.emote {
            Some(spur) if self.emote_umlaeufe > 0 => spur,
            _ => self.atem,
        }
    }

    pub fn spalte(&self) -> u32 {
        self.spalte
    }

    /// Die Zeile des Sheets, aus der gerade gezeichnet wird.
    ///
    /// Seit T-9.3 kommt sie vom Animator und nicht mehr allein aus
    /// `zustand_abbilden`: welche der beiden Zeilen eines Moods gilt, weiss
    /// nur er. Zwei Stellen mit derselben Auskunft waeren eine Auskunft und
    /// eine Attrappe.
    pub fn zeile(&self) -> u32 {
        self.spur().zeile
    }

    /// Laeuft gerade ein Emote? Die Diagnose meldet es, damit ein Verifizierer
    /// die zwei Umlaeufe von aussen zaehlen kann.
    pub fn emote_laeuft(&self) -> bool {
        self.emote.is_some() && self.emote_umlaeufe > 0
    }

    /// Wie viele Bilder die gezeigte Zeile hat. Der Eingaberegion-Cache
    /// braucht die Zahl, weil dieselbe Zeile als Standbild eine andere
    /// Silhouette hat als im Lauf.
    pub fn spalten(&self) -> u32 {
        self.spur().spalten.max(1)
    }

    /// Abstand zweier Bilder der GERADE gezeigten Spur. Der Timer braucht ihn,
    /// um sich selbst nachzulegen -- und er aendert sich, wenn das Emote in
    /// den Atem uebergeht.
    pub fn schrittdauer(&self) -> Duration {
        if self.emote_laeuft() {
            self.emote_dauer
        } else {
            self.atem_dauer
        }
    }

    /// Ob ueberhaupt ein Takt noetig ist. Die Weiche fuer den Timer.
    pub fn laeuft(&self) -> bool {
        self.spur().spalten > 1
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
        let dauer = self.schrittdauer();
        let vergangen = jetzt.saturating_duration_since(self.letzter_schritt);
        let schritte = (vergangen.as_nanos() / dauer.as_nanos()) as u32;
        if schritte == 0 {
            return false;
        }
        self.letzter_schritt += dauer * schritte;
        let vorher = (self.zeile(), self.spalte);
        let spalten = self.spur().spalten.max(1);
        let roh = self.spalte + schritte;
        self.spalte = roh % spalten;
        // Ein Umlauf zaehlt erst, wenn er VOLL ist. Mitten im Umlauf auf die
        // Atemzeile zu springen ruckt -- und genau diesen Ruck soll das Ganze
        // vermeiden.
        if self.emote_umlaeufe > 0 {
            self.emote_umlaeufe = self.emote_umlaeufe.saturating_sub(roh / spalten);
            if self.emote_umlaeufe == 0 {
                // Die Atemzeile hat eine andere Spaltenzahl; ohne diesen
                // Schnitt zeigte der erste Atemzug eine Zelle, die es in ihr
                // nicht gibt.
                self.spalte = 0;
                // Und sie hat einen anderen Takt. Ohne diesen Neuanfang
                // rechnete der naechste Tick die im Emote verstrichene Zeit
                // mit der langsamen Atemdauer um und holte einen Schwung
                // Schritte auf einmal nach.
                self.letzter_schritt = jetzt;
            }
        }
        (self.zeile(), self.spalte) != vorher
    }

    /// Beim Wechsel der Zeile oder der Bildzahl: Loop faengt vorn an.
    ///
    /// Unveraendert heisst hier: nichts tun. Der Aufrufer ruft das vor jedem
    /// Rendern auf, und ein bedingungsloses Zuruecksetzen wuerde die Spalte
    /// bei jedem Frame wieder auf 0 stellen -- die Animation stuende dann
    /// still und saehe aus wie der Zustand von vorher.
    pub fn mood_setzen(&mut self, atem: Spur, emote: Option<Spur>, emote_fps: u32,
                       jetzt: Instant) {
        let atem = Spur {
            zeile: atem.zeile,
            spalten: atem.spalten.max(1),
        };
        if self.atem == atem && self.emote == emote.filter(|s| s.spalten > 1) {
            return;
        }
        *self = Self::neu(atem, emote, emote_fps, jetzt);
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
    fn tick_vier_spalten_im_atemtakt_schaltet_der_reihe_nach_und_kehrt_zurueck() {
        // GIVEN einen Animator mit vier Atemspalten -- ohne Emote gilt
        // ATEM_FPS, nicht die uebergebene Emote-Rate:
        let start = Instant::now();
        let mut animator = Animator::neu(Spur { zeile: 0, spalten: 4 }, None, 12, start);
        assert_eq!(animator.spalte(), 0);
        assert!(animator.laeuft());

        // WHEN wir ihn viermal im Schritttakt wecken,
        // THEN durchlaeuft er 1, 2, 3 und landet wieder auf 0:
        let schritt = Duration::from_secs(1) / ATEM_FPS;
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
        let mut animator = Animator::neu(Spur { zeile: 0, spalten: 1 }, None, 12, start);

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
        let mut animator = Animator::neu(Spur { zeile: 0, spalten: 4 }, None, 12, start);

        // Ohne Emote gilt der ATEMTAKT, nicht die uebergebenen 12 fps. Der
        // Schritt wird aus der Konstante gerechnet und nicht als Millisekunde
        // hingeschrieben: bis zum 02.09. stand hier 1000 ms fuer drei
        // Schritte, was nur bei ATEM_FPS = 3 stimmte und den Test rot machte,
        // als die Konstante stieg -- ohne dass die Zusage sich geaendert
        // haette.
        let schritt = Duration::from_secs(1) / ATEM_FPS;

        // WHEN der erste Weckruf erst drei Schritte spaet kommt,
        // THEN steht er auf Spalte 3 und nicht auf 1:
        assert!(animator.tick(start + schritt * 3));
        assert_eq!(animator.spalte(), 3);

        // WHEN weitere sechs Schritte ohne Weckruf vergehen,
        // THEN laeuft der Loop einmal ganz durch und einen darueber:
        assert!(animator.tick(start + schritt * 9));
        assert_eq!(animator.spalte(), 1);
    }

    /// `animator_nachfuehren()` laeuft vor JEDEM Rendern. Setzte
    /// `mood_setzen` bedingungslos zurueck, stuende die Spalte dauerhaft auf 0
    /// -- die Animation saehe aus wie das Standbild von vorher, und zwar ohne
    /// dass irgendein anderer Test es merkt.
    /// Die Zusage aus T-9.3: das Emote spielt genau zweimal, dann wird
    /// geatmet. Vorher lief eine Mood-Schleife endlos -- darum lachte das Pet
    /// ununterbrochen.
    #[test]
    fn tick_spielt_das_emote_drei_volle_umlaeufe_und_faellt_dann_auf_den_atem() {
        // GIVEN einen Mood mit vier Emote- und drei Atemspalten:
        let start = Instant::now();
        let schritt = Duration::from_millis(84);
        let mut animator = Animator::neu(
            Spur { zeile: 1, spalten: 3 },
            Some(Spur { zeile: 9, spalten: 4 }),
            12,
            start,
        );
        assert!(animator.emote_laeuft());
        assert_eq!(animator.zeile(), 9);

        // WHEN wir Schritt fuer Schritt takten,
        let mut gesehen = vec![(animator.zeile(), animator.spalte())];
        for i in 1..=12 {
            animator.tick(start + schritt * i);
            gesehen.push((animator.zeile(), animator.spalte()));
        }

        // THEN laeuft die Emote-Zeile 9 genau DREIMAL durch ihre vier Spalten,
        // und ab dem dreizehnten Bild steht die Atemzeile 1:
        assert_eq!(
            gesehen,
            vec![(9, 0), (9, 1), (9, 2), (9, 3),
                 (9, 0), (9, 1), (9, 2), (9, 3),
                 (9, 0), (9, 1), (9, 2), (9, 3),
                 (1, 0)],
            "{gesehen:?}"
        );
        assert!(!animator.emote_laeuft());
    }

    /// Die Farbe markiert den MOMENT, nicht den Normalfall.
    ///
    /// Die Positivkontrolle steht im selben Test: waehrend des Emotes MUSS
    /// der Manifestwert ankommen. Sonst waere „im Ruhezustand null" gruen,
    /// weil die Funktion immer null gibt -- und das Pet bliebe fuer immer
    /// ungefaerbt, auch wenn etwas passiert.
    #[test]
    fn die_toenung_liegt_nur_waehrend_des_emotes() {
        assert_eq!(toenungsanteil(0.4, true), 0.4, "Emote: der Manifestwert");
        assert_eq!(toenungsanteil(0.4, false), 0.0, "Ruhe: keine Farbe");
        assert_eq!(toenungsanteil(1.0, true), 1.0);
        assert_eq!(toenungsanteil(1.0, false), 0.0);
        // Ein Pet ohne Toenung bleibt auch im Emote ungefaerbt.
        assert_eq!(toenungsanteil(0.0, true), 0.0);
        // Und Unsinn im Manifest wird geklemmt, nicht gerechnet.
        assert_eq!(toenungsanteil(9.0, true), 1.0);
        assert_eq!(toenungsanteil(-2.0, true), 0.0);
    }

    /// Die zwei Takte, und WANN welcher gilt. Ohne diesen Test waere der
    /// getrennte Takt nur eine Behauptung im Kommentar: die Tests darueber
    /// fahren je nur einen von beiden.
    ///
    /// Gemessen wird die HERKUNFT der Rate, nicht ihre Groesse: die Geste
    /// nimmt das Argument, der Atem die Konstante. Bis zum 02.09. stand hier
    /// `langsam > schnell` -- das ging nur gut, solange die beiden Zahlen
    /// zufaellig verschieden waren, und wurde in dem Moment rot, in dem
    /// `ATEM_FPS` auf 12 stieg, obwohl die eigentliche Zusage unberuehrt war.
    /// Die Emote-Rate ist darum hier absichtlich eine dritte Zahl.
    #[test]
    fn das_emote_nimmt_die_uebergebene_rate_der_atem_die_konstante() {
        let start = Instant::now();
        // Weder 12 noch der Wert von ATEM_FPS -- eine Verwechslung der beiden
        // Quellen faellt so auf, egal wie die Konstante gerade steht.
        let emote_fps = 40;
        let geste = Duration::from_secs(1) / emote_fps;
        let atem = Duration::from_secs(1) / ATEM_FPS;
        assert_ne!(geste, atem, "die Messung braucht zwei unterscheidbare Takte");

        let mut mit = Animator::neu(
            Spur { zeile: 1, spalten: 2 },
            Some(Spur { zeile: 9, spalten: 2 }),
            emote_fps,
            start,
        );
        assert_eq!(mit.schrittdauer(), geste, "die Geste nimmt das Argument");

        // Nach EMOTE_UMLAEUFE vollen Umlaeufen faellt er auf den Atem.
        for i in 1..=(2 * EMOTE_UMLAEUFE) {
            mit.tick(start + geste * i);
        }
        assert!(!mit.emote_laeuft(), "Emote muesste durch sein");
        assert_eq!(mit.schrittdauer(), atem, "der Atem nimmt die Konstante");

        // Ohne Emote: von Anfang an die Konstante, auch wenn die Emote-Rate
        // hoch angegeben wird. Das Manifest darf den Ruhezustand nicht
        // hochdrehen -- das ist der Grund, warum ATEM_FPS eine Konstante ist
        // und kein Manifestfeld.
        let ohne = Animator::neu(Spur { zeile: 1, spalten: 4 }, None, 60, start);
        assert_eq!(ohne.schrittdauer(), atem);
        assert_ne!(ohne.schrittdauer(), Duration::from_secs(1) / 60);
    }

    #[test]
    fn tick_ohne_emote_bleibt_von_anfang_an_auf_der_atemzeile() {
        let start = Instant::now();
        let mut animator = Animator::neu(Spur { zeile: 1, spalten: 3 }, None, 12, start);
        assert!(!animator.emote_laeuft());
        for i in 0..=6 {
            assert_eq!(animator.zeile(), 1, "Schritt {i}");
            animator.tick(start + Duration::from_millis(84) * i);
        }
    }

    /// Ein verspaeteter Weckruf darf keine Umlaeufe verschlucken. Springt der
    /// Timer ueber beide Emote-Umlaeufe hinweg, steht danach der Atem -- und
    /// nicht ein halb abgelaufenes Emote.
    #[test]
    fn tick_ueberspringt_beide_emote_umlaeufe_auf_einmal_und_atmet_danach() {
        let start = Instant::now();
        let mut animator = Animator::neu(
            Spur { zeile: 1, spalten: 3 },
            Some(Spur { zeile: 9, spalten: 4 }),
            12,
            start,
        );
        // Ein einziger Weckruf nach zwoelf Schritten -- drei Emote-Umlaeufe weit.
        animator.tick(start + Duration::from_millis(84) * 12);
        assert!(!animator.emote_laeuft());
        assert_eq!(animator.zeile(), 1);
        assert_eq!(animator.spalte(), 0);
    }

    /// Ein Emote mit einer einzigen Spalte ist keines: es waere zweimal
    /// dasselbe Bild und danach ein Sprung.
    #[test]
    fn emote_mit_einer_spalte_wird_verworfen() {
        let start = Instant::now();
        let animator = Animator::neu(
            Spur { zeile: 1, spalten: 3 },
            Some(Spur { zeile: 9, spalten: 1 }),
            12,
            start,
        );
        assert!(!animator.emote_laeuft());
        assert_eq!(animator.zeile(), 1);
    }

    #[test]
    fn mood_setzen_mit_unveraenderter_zeile_laesst_den_laufenden_loop_stehen() {
        // GIVEN einen Animator, der schon bei Spalte 2 steht:
        let start = Instant::now();
        let mut animator = Animator::neu(Spur { zeile: 4, spalten: 8 }, None, 12, start);
        // Zwei Schritte im ATEMTAKT, nicht bei den uebergebenen 12 fps.
        let jetzt = start + (Duration::from_secs(1) / ATEM_FPS) * 2;
        animator.tick(jetzt);
        assert_eq!(animator.spalte(), 2);

        // WHEN dieselbe Zeile mit derselben Bildzahl erneut gesetzt wird,
        // THEN bleibt die Spalte stehen:
        animator.mood_setzen(Spur { zeile: 4, spalten: 8 }, None, 12, jetzt);
        assert_eq!(animator.spalte(), 2);

        // WHEN dagegen die Bildzahl derselben Zeile wechselt,
        // THEN faengt der Loop vorn an:
        animator.mood_setzen(Spur { zeile: 4, spalten: 6 }, None, 12, jetzt);
        assert_eq!(animator.spalte(), 0);
    }

    #[test]
    fn mood_setzen_nach_halbem_loop_faengt_mit_neuer_spaltenzahl_wieder_vorn_an() {
        // GIVEN einen Animator, der schon bei Spalte 2 steht:
        let start = Instant::now();
        let mut animator = Animator::neu(Spur { zeile: 0, spalten: 4 }, None, 12, start);
        animator.tick(start + (Duration::from_secs(1) / ATEM_FPS) * 2);
        assert_eq!(animator.spalte(), 2);

        // WHEN ein Standbild-Mood gesetzt wird,
        // THEN steht die Spalte wieder auf 0 und der Takt ist aus:
        animator.mood_setzen(Spur { zeile: 7, spalten: 1 }, None, 12,
                             start + Duration::from_millis(168));
        assert_eq!(animator.spalte(), 0);
        assert!(!animator.laeuft());
    }

    /// Die beiden Enden des Anteils. Die Positivkontrolle steht im selben
    /// Test: 1.0 MUSS die Pixel veraendern -- sonst waere „0.0 laesst sie in
    /// Ruhe" gruen, weil die Toenung gar nichts tut, und nicht weil der
    /// Anteil greift.
    #[test]
    fn anteil_null_laesst_in_ruhe_und_eins_toent_voll() {
        let zelle: Vec<u8> = vec![40, 80, 120, 200, 10, 20, 30, 255];
        let violett = mood_toenung("thinking");

        assert_eq!(frame_toenen_anteilig(&zelle, violett, 0.0), zelle);
        let voll = frame_toenen_anteilig(&zelle, violett, 1.0);
        assert_ne!(voll, zelle);
        assert_eq!(voll, frame_toenen(&zelle, violett));

        // Ausserhalb von 0..1 wird geklemmt, nicht gerechnet: ein Manifest
        // mit `"toenung": 5` soll nicht heller faerben als voll.
        assert_eq!(frame_toenen_anteilig(&zelle, violett, 5.0), voll);
        assert_eq!(frame_toenen_anteilig(&zelle, violett, -1.0), zelle);

        // Die Voraussetzung, auf der die Mischung ruht: `frame_toenen` laesst
        // Alpha in Ruhe. Nur deshalb darf `frame_toenen_anteilig` die
        // Alpha-Stelle ueberspringen. Faellt diese Zusage, faellt sie hier
        // auf -- und nicht erst an einer ausgefransten Silhouette.
        for i in (3..zelle.len()).step_by(4) {
            assert_eq!(voll[i], zelle[i], "frame_toenen hat Alpha an {i} veraendert");
        }
    }

    /// Der Mischweg selbst: die halbe Toenung muss ZWISCHEN beiden Enden
    /// liegen, und zwar je Kanal. Ein Test, der nur „ungleich beiden" prueft,
    /// waere auch fuer eine Zufallszahl gruen.
    #[test]
    fn ein_anteil_dazwischen_liegt_zwischen_den_enden() {
        let zelle: Vec<u8> = vec![40, 80, 120, 200, 10, 20, 30, 255];
        let violett = mood_toenung("thinking");
        let voll = frame_toenen(&zelle, violett);
        let halb = frame_toenen_anteilig(&zelle, violett, 0.5);

        assert_ne!(halb, zelle, "0.5 darf nicht das Original sein");
        assert_ne!(halb, voll, "0.5 darf nicht der Vollton sein");
        for i in 0..zelle.len() {
            if i % 4 == 3 {
                // Alpha bleibt woertlich -- eine gemischte Deckung wuerde die
                // Silhouette ausfransen.
                assert_eq!(halb[i], zelle[i], "Alpha an {i} veraendert");
                continue;
            }
            let (lo, hi) = (zelle[i].min(voll[i]), zelle[i].max(voll[i]));
            assert!(
                (lo..=hi).contains(&halb[i]),
                "Kanal {i}: {} liegt nicht zwischen {lo} und {hi}",
                halb[i]
            );
        }
        // Und er waechst mit dem Anteil, statt irgendwo zu springen.
        let werte: Vec<u8> = [0.0f32, 0.25, 0.5, 0.75, 1.0]
            .iter()
            .map(|a| frame_toenen_anteilig(&zelle, violett, *a)[0])
            .collect();
        let steigend = werte.windows(2).all(|p| p[0] <= p[1]);
        let fallend = werte.windows(2).all(|p| p[0] >= p[1]);
        assert!(steigend || fallend, "Kanal 0 springt: {werte:?}");
    }
}
