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

#[derive(Clone, Debug, Default)]
pub struct FaceState {
    pub rev: u64,
    pub mood: String,
    pub sprite: String,
    pub bubble_visible: bool,
    /// Unix-Zeit des letzten *Commits*. 0 = noch nie gezeichnet.
    pub last_render_ts: f64,
    pub frames_rendered: u64,
}

impl FaceState {
    pub fn als_json(&self) -> String {
        format!(
            concat!(
                "{{\"v\":1,\"rev\":{},\"mood\":\"{}\",\"sprite\":\"{}\",",
                "\"bubble_visible\":{},\"last_render_ts\":{:.6},",
                "\"frames_rendered\":{}}}"
            ),
            self.rev,
            escape(&self.mood),
            escape(&self.sprite),
            self.bubble_visible,
            self.last_render_ts,
            self.frames_rendered
        )
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
            last_render_ts: 1.5,
            frames_rendered: 7,
        };
        let j = s.als_json();
        for feld in [
            "rev",
            "mood",
            "sprite",
            "bubble_visible",
            "last_render_ts",
            "frames_rendered",
        ] {
            assert!(j.contains(feld), "{feld} fehlt in {j}");
        }
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
    fn anfuehrungszeichen_werden_escapet() {
        let s = FaceState {
            mood: "a\"b".into(),
            ..Default::default()
        };
        assert!(s.als_json().contains("a\\\"b"));
    }
}
