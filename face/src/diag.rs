//! T-1.2 — Diagnose-Socket im Face. Das Overlay ist testbar, ohne hinzuschauen.
//!
//! Ein Unix-Socket mit Modus 0600, eine Zeile JSON je Verbindung. Kein TCP:
//! dieselbe Begruendung wie beim Hub, und zusaetzlich, weil hier Fenstertitel
//! und Sprite-Zustand herauskommen.
//!
//! `last_render_ts` ist der Zeitpunkt des **tatsaechlichen Commits**, nicht
//! des Zustandsempfangs. Der Unterschied ist der ganze Sinn der Sache: ein
//! Face, das den Zustand entgegennimmt und dann nicht zeichnet, waere mit
//! einem Empfangszeitstempel gruen und trotzdem kaputt.
//!
//! **Kein Einfluss auf die Idle-CPU, wenn niemand liest.** Der Thread
//! blockiert in `accept()`; er pollt nichts und weckt nichts. Ein Timer hier
//! haette die 0,17 % aus Spike T-1.3 zunichte gemacht, die das Argument fuer
//! die ganze Architektur waren.

use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Debug)]
pub struct FaceState {
    pub rev: u64,
    pub mood: String,
    pub sprite: String,
    pub bubble_visible: bool,
    /// T-2.4: Pet-Sprite und eine etwaige Blase sind tatsaechlich sichtbar.
    pub sichtbar: bool,
    /// Unix-Zeit des letzten *Commits*. 0 = noch nie gezeichnet.
    pub last_render_ts: f64,
    pub frames_rendered: u64,
    /// T-2.2: gezeichnete Buffer der eigenen Blasen-Subsurface.
    pub bubble_frames_rendered: u64,
    /// T-1.8: wie oft ein Ton tatsaechlich gestartet wurde.
    pub toene_gespielt: u64,
    /// Jedes vom Compositor empfangene Layer-Surface-configure.
    pub configure_empfangen: u64,
    /// Aktuelle Subsurface-Position, damit Ziehen von aussen messbar ist.
    pub sprite_x: i32,
    pub sprite_y: i32,
    /// T-2.5: Name des `wl_output`, an den die Layer-Surface gebunden ist.
    /// Leer, solange nichts gebunden ist.
    pub output: String,
    /// T-2.5: +1 je Neuerzeugung der Layer-Surface nach Output-Removal.
    /// Die Erstbindung zaehlt nicht mit.
    pub output_wechsel: u64,
    /// T-2.7: das Kontextmenue-Popup ist gemappt.
    pub menu_offen: bool,
    /// T-2.7: +1 je tatsaechlich ausgeloester Menue-Aktion. Deaktivierte
    /// Eintraege bewegen den Zaehler nicht -- sie loesen ja nichts aus.
    pub menu_aktionen: u64,
    /// T-2.7: "ears_aus" | "eyes_aus" | "beenden" | "" (noch keine).
    pub letzte_menu_aktion: String,
    /// T-3.14: was das Face DARSTELLT, nicht was es empfangen hat. Ohne Hub
    /// oder bei unlesbarem Schnappschuss: "idle".
    pub voice_state: String,
    /// T-3.14: +1 je tatsaechlich in den Sprite-Puffer gezeichnetem
    /// Indikator. `idle` bewegt ihn nicht -- dort wird nichts gemalt.
    pub voice_indikator_gezeichnet: u64,
}

/// Von Hand statt abgeleitet, wegen genau eines Feldes: `voice_state` faengt
/// bei `"idle"` an, nicht bei `""`. Der leere String waere ein fuenfter,
/// namenloser Sprachzustand -- und die Diagnose soll nie etwas ausgeben, das
/// der Verifizierer nicht kennt.
impl Default for FaceState {
    fn default() -> Self {
        Self {
            rev: 0,
            mood: String::new(),
            sprite: String::new(),
            bubble_visible: false,
            sichtbar: false,
            last_render_ts: 0.0,
            frames_rendered: 0,
            bubble_frames_rendered: 0,
            toene_gespielt: 0,
            configure_empfangen: 0,
            sprite_x: 0,
            sprite_y: 0,
            output: String::new(),
            output_wechsel: 0,
            menu_offen: false,
            menu_aktionen: 0,
            letzte_menu_aktion: String::new(),
            voice_state: "idle".to_owned(),
            voice_indikator_gezeichnet: 0,
        }
    }
}

impl FaceState {
    pub fn als_json(&self) -> String {
        format!(
            concat!(
                "{{\"v\":1,\"rev\":{},\"mood\":\"{}\",\"sprite\":\"{}\",",
                "\"bubble_visible\":{},\"sichtbar\":{},\"last_render_ts\":{:.6},",
                "\"frames_rendered\":{},\"bubble_frames_rendered\":{},",
                "\"toene_gespielt\":{},\"configure_empfangen\":{},",
                "\"sprite_x\":{},\"sprite_y\":{},",
                "\"output\":\"{}\",\"output_wechsel\":{},",
                "\"menu_offen\":{},\"menu_aktionen\":{},",
                "\"letzte_menu_aktion\":\"{}\",",
                "\"voice_state\":\"{}\",\"voice_indikator_gezeichnet\":{}}}"
            ),
            self.rev,
            escape(&self.mood),
            escape(&self.sprite),
            self.bubble_visible,
            self.sichtbar,
            self.last_render_ts,
            self.frames_rendered,
            self.bubble_frames_rendered,
            self.toene_gespielt,
            self.configure_empfangen,
            self.sprite_x,
            self.sprite_y,
            escape(&self.output),
            self.output_wechsel,
            self.menu_offen,
            self.menu_aktionen,
            escape(&self.letzte_menu_aktion),
            escape(&self.voice_state),
            self.voice_indikator_gezeichnet
        )
    }

    /// T-2.7: eine Aktion wurde ausgeloest -- egal ob ueber einen Klick im
    /// Popup oder ueber den Steuer-Socket. Gezaehlt wird die AUSLOESUNG, nicht
    /// der Erfolg der Zustellung: ob der Hub die Unit wirklich gestoppt hat,
    /// misst der Verifizierer an `systemctl --user is-active`, und diese beiden
    /// Groessen duerfen sich nicht gegenseitig bestaetigen.
    pub fn menu_aktion_gezaehlt(&mut self, name: &str) {
        self.menu_aktionen += 1;
        self.letzte_menu_aktion = name.to_owned();
    }

    /// Nur Commits mit angehaengtem Buffer sind gezeichnete Frames. Ein
    /// bufferloser Initial- oder Property-Commit darf die Diagnose nicht von
    /// "noch nie gezeichnet" wegbewegen.
    pub fn commit_gezaehlt(&mut self, mit_buffer: bool) {
        if !mit_buffer {
            return;
        }
        self.frames_rendered += 1;
        self.last_render_ts = jetzt();
    }

    pub fn bubble_frame_gezaehlt(&mut self) {
        self.bubble_frames_rendered += 1;
    }
}

fn escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

pub fn jetzt() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub struct DiagSocket {
    pfad: PathBuf,
}

impl DiagSocket {
    /// Startet einen Thread, der in `accept()` blockiert. Gibt den Pfad
    /// zurueck; bei Fehlern wird nichts gestartet -- die Diagnose darf das
    /// Overlay nicht am Starten hindern.
    pub fn starten(pfad: &Path, zustand: Arc<Mutex<FaceState>>) -> Option<Self> {
        let _ = std::fs::remove_file(pfad);
        if let Some(eltern) = pfad.parent() {
            let _ = std::fs::create_dir_all(eltern);
        }
        let listener = UnixListener::bind(pfad).ok()?;
        std::fs::set_permissions(pfad, std::fs::Permissions::from_mode(0o600)).ok()?;

        let pfad_owned = pfad.to_path_buf();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut s) = stream else { continue };
                let zeile = match zustand.lock() {
                    Ok(z) => z.als_json(),
                    // Ein vergifteter Mutex darf die Diagnose nicht zum
                    // Absturz bringen -- sie soll ueber Fehler berichten,
                    // nicht an ihnen sterben.
                    Err(vergiftet) => vergiftet.into_inner().als_json(),
                };
                let _ = s.write_all(zeile.as_bytes());
                let _ = s.write_all(b"\n");
            }
        });

        Some(Self { pfad: pfad_owned })
    }
}

impl Drop for DiagSocket {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.pfad);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_enthaelt_alle_geforderten_felder() {
        let s = FaceState {
            rev: 41,
            mood: "needs_input".into(),
            sprite: "alert".into(),
            bubble_visible: true,
            sichtbar: true,
            last_render_ts: 1.5,
            frames_rendered: 7,
            bubble_frames_rendered: 2,
            toene_gespielt: 3,
            configure_empfangen: 4,
            sprite_x: 120,
            sprite_y: 80,
            output: "HDMI-A-1".into(),
            output_wechsel: 2,
            menu_offen: true,
            menu_aktionen: 1,
            letzte_menu_aktion: "ears_aus".into(),
            voice_state: "speaking".into(),
            voice_indikator_gezeichnet: 5,
        };
        let j = s.als_json();
        for feld in [
            "toene_gespielt",
            "rev",
            "mood",
            "sprite",
            "bubble_visible",
            "sichtbar",
            "last_render_ts",
            "frames_rendered",
            "bubble_frames_rendered",
            "configure_empfangen",
            "sprite_x",
            "sprite_y",
        ] {
            assert!(j.contains(feld), "{feld} fehlt in {j}");
        }
    }

    /// T-3.14: der Verifizierer misst die PTT-Latenz an `voice_state` und
    /// belegt am Zaehler, dass tatsaechlich Pixel geflossen sind. Ohne den
    /// Zaehler waere „das Face zeigt den Zustand an" eine Selbstauskunft.
    #[test]
    fn sprachzustand_steht_wortwoertlich_im_json() {
        let mut s = FaceState::default();
        assert!(
            s.als_json().contains("\"voice_state\":\"idle\""),
            "{}",
            s.als_json()
        );
        assert!(s.als_json().contains("\"voice_indikator_gezeichnet\":0"));
        s.voice_state = "listening".into();
        s.voice_indikator_gezeichnet = 12;
        assert!(s.als_json().contains("\"voice_state\":\"listening\""));
        assert!(s.als_json().contains("\"voice_indikator_gezeichnet\":12"));
    }

    /// Der Verifizierer liest genau diese zwei Schluessel zur Laufzeit. Der
    /// Test prueft deshalb die vollstaendige Schreibweise samt Wert, nicht
    /// nur das Vorkommen des Namens.
    #[test]
    fn output_und_wechselzaehler_stehen_wortwoertlich_im_json() {
        let mut s = FaceState::default();
        assert!(s.als_json().contains("\"output\":\"\""), "{}", s.als_json());
        assert!(s.als_json().contains("\"output_wechsel\":0"));
        s.output = "HDMI-A-1".into();
        s.output_wechsel = 3;
        assert!(s.als_json().contains("\"output\":\"HDMI-A-1\""));
        assert!(s.als_json().contains("\"output_wechsel\":3"));
    }

    /// T-2.7: der Verifizierer liest genau diese drei Schluessel zur Laufzeit.
    /// Deshalb die vollstaendige Schreibweise samt Wert -- und `false`
    /// ausdruecklich, weil `jq '.menu_offen // empty'` es wie `null`
    /// behandelt und ein Test auf blosses Vorkommen das nicht faengt.
    #[test]
    fn menu_schluessel_stehen_wortwoertlich_im_json() {
        let mut s = FaceState::default();
        let j = s.als_json();
        assert!(j.contains("\"menu_offen\":false"), "{j}");
        assert!(j.contains("\"menu_aktionen\":0"), "{j}");
        assert!(j.contains("\"letzte_menu_aktion\":\"\""), "{j}");
        s.menu_offen = true;
        s.menu_aktion_gezaehlt("beenden");
        let j = s.als_json();
        assert!(j.contains("\"menu_offen\":true"), "{j}");
        assert!(j.contains("\"menu_aktionen\":1"), "{j}");
        assert!(j.contains("\"letzte_menu_aktion\":\"beenden\""), "{j}");
    }

    #[test]
    fn commit_setzt_zeitstempel_und_zaehler() {
        let mut s = FaceState::default();
        assert_eq!(s.last_render_ts, 0.0);
        s.commit_gezaehlt(true);
        assert_eq!(s.frames_rendered, 1);
        assert!(
            s.last_render_ts > 1_700_000_000.0,
            "Zeitstempel nicht gesetzt"
        );
    }

    #[test]
    fn bufferloser_commit_ist_kein_frame() {
        let mut s = FaceState::default();
        s.commit_gezaehlt(false);
        assert_eq!(s.frames_rendered, 0);
        assert_eq!(s.last_render_ts, 0.0);
    }

    #[test]
    fn blasenframe_bewegt_sprite_zaehler_nicht() {
        let mut s = FaceState {
            frames_rendered: 3,
            ..Default::default()
        };
        s.bubble_frame_gezaehlt();
        assert_eq!(s.frames_rendered, 3);
        assert_eq!(s.bubble_frames_rendered, 1);
    }

    #[test]
    fn anfuehrungszeichen_werden_escapet() {
        let s = FaceState {
            mood: "a\"b".into(),
            ..Default::default()
        };
        assert!(s.als_json().contains("a\\\"b"));
    }

    #[test]
    fn sichtbar_wandert_in_die_diagnose() {
        let mut s = FaceState {
            sichtbar: true,
            ..Default::default()
        };
        assert!(s.als_json().contains("\"sichtbar\":true"));
        s.sichtbar = false;
        assert!(s.als_json().contains("\"sichtbar\":false"));
    }
}
