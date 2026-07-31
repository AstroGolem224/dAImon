//! Ziehzustand und dauerhafte Pet-Position.

use std::{
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

pub const MINDEST_ZUGSTRECKE_PX: f64 = 6.0;
static NAECHSTE_TEMPDATEI: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Loslassen {
    Klick,
    Zug((i32, i32)),
}

#[derive(Clone, Copy, Debug)]
pub struct Ziehen {
    anker: (f64, f64),
    position: (i32, i32),
    aktiv: bool,
}

impl Ziehen {
    pub fn beginnen(anker: (f64, f64), position: (i32, i32)) -> Self {
        Self {
            anker,
            position,
            aktiv: false,
        }
    }

    /// Wayland liefert Koordinaten relativ zur bewegten Sprite-Surface. Nach
    /// jedem `set_position` liegt der Zeiger deshalb wieder nahe dem Anker;
    /// der Abstand zum Anker ist ab dann genau der noch nicht angewandte
    /// Bewegungsschritt und wird auf die aktuelle Position addiert.
    pub fn bewegen(
        &mut self,
        zeiger: (f64, f64),
        sprite_groesse: (i32, i32),
        output_groesse: (i32, i32),
    ) -> Option<(i32, i32)> {
        let delta = (zeiger.0 - self.anker.0, zeiger.1 - self.anker.1);
        if !self.aktiv {
            let strecke_quadrat = delta.0 * delta.0 + delta.1 * delta.1;
            if strecke_quadrat < MINDEST_ZUGSTRECKE_PX * MINDEST_ZUGSTRECKE_PX {
                return None;
            }
            self.aktiv = true;
        }
        let neu = (
            self.position.0.saturating_add(delta.0.round() as i32),
            self.position.1.saturating_add(delta.1.round() as i32),
        );
        self.position = position_klemmen(neu, sprite_groesse, output_groesse);
        Some(self.position)
    }

    pub fn loslassen(self) -> Loslassen {
        if self.aktiv {
            Loslassen::Zug(self.position)
        } else {
            Loslassen::Klick
        }
    }
}

pub fn position_klemmen(
    position: (i32, i32),
    sprite_groesse: (i32, i32),
    output_groesse: (i32, i32),
) -> (i32, i32) {
    let max_x = (output_groesse.0 - sprite_groesse.0).max(0);
    let max_y = (output_groesse.1 - sprite_groesse.1).max(0);
    (position.0.clamp(0, max_x), position.1.clamp(0, max_y))
}

pub fn zustandspfad() -> PathBuf {
    if let Some(basis) = std::env::var_os("XDG_STATE_HOME").filter(|wert| !wert.is_empty()) {
        return PathBuf::from(basis).join("daimon/face-position.json");
    }
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".local/state/daimon/face-position.json")
}

pub fn laden(pfad: &Path, vorgabe: (i32, i32)) -> (i32, i32) {
    match laden_intern(pfad) {
        Ok(position) => position,
        Err(fehler) => {
            eprintln!(
                "Pet-Position aus {} nicht verwendbar ({fehler}); verwende Vorgabeposition {},{}",
                pfad.display(),
                vorgabe.0,
                vorgabe.1
            );
            vorgabe
        }
    }
}

fn laden_intern(pfad: &Path) -> Result<(i32, i32), String> {
    let roh = fs::read_to_string(pfad).map_err(|fehler| fehler.to_string())?;
    let json: serde_json::Value =
        serde_json::from_str(&roh).map_err(|fehler| format!("kaputtes JSON: {fehler}"))?;
    if json.get("v").and_then(serde_json::Value::as_u64) != Some(1) {
        return Err("unbekannte Formatversion".into());
    }
    let x = json
        .get("x")
        .and_then(serde_json::Value::as_i64)
        .and_then(|wert| i32::try_from(wert).ok())
        .ok_or_else(|| "x fehlt oder liegt ausserhalb i32".to_string())?;
    let y = json
        .get("y")
        .and_then(serde_json::Value::as_i64)
        .and_then(|wert| i32::try_from(wert).ok())
        .ok_or_else(|| "y fehlt oder liegt ausserhalb i32".to_string())?;
    Ok((x, y))
}

pub fn speichern(pfad: &Path, position: (i32, i32)) -> io::Result<()> {
    let verzeichnis = pfad.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(verzeichnis)?;
    let name = pfad
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("face-position.json");
    let temp_pfad = verzeichnis.join(format!(
        ".{name}.{}.{}.tmp",
        std::process::id(),
        NAECHSTE_TEMPDATEI.fetch_add(1, Ordering::Relaxed)
    ));

    let ergebnis = (|| {
        let mut datei = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp_pfad)?;
        write!(
            datei,
            "{{\"v\":1,\"x\":{},\"y\":{}}}\n",
            position.0, position.1
        )?;
        datei.flush()?;
        datei.sync_all()?;
        fs::rename(&temp_pfad, pfad)?;
        File::open(verzeichnis)?.sync_all()?;
        Ok(())
    })();
    if ergebnis.is_err() {
        let _ = fs::remove_file(&temp_pfad);
    }
    ergebnis
}

#[cfg(test)]
mod tests {
    use super::*;

    fn testpfad(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "daimon-position-{}-{}-{name}",
            std::process::id(),
            NAECHSTE_TEMPDATEI.fetch_add(1, Ordering::Relaxed)
        ))
    }

    #[test]
    fn strecke_unter_minimum_bleibt_klick() {
        let mut ziehen = Ziehen::beginnen((20.0, 30.0), (100, 100));
        assert_eq!(ziehen.bewegen((23.0, 34.0), (20, 20), (800, 600)), None);
        assert_eq!(ziehen.loslassen(), Loslassen::Klick);
    }

    #[test]
    fn rechts_ausserhalb_wird_sichtbar_geklemmt() {
        assert_eq!(
            position_klemmen((900, 40), (192, 208), (800, 600)),
            (608, 40)
        );
    }

    #[test]
    fn zustandsdatei_wird_atomar_geschrieben_und_gelesen() {
        let verzeichnis = testpfad("rundlauf");
        let pfad = verzeichnis.join("face-position.json");
        speichern(&pfad, (321, 123)).unwrap();
        assert_eq!(laden(&pfad, (0, 0)), (321, 123));
        fs::remove_dir_all(verzeichnis).unwrap();
    }

    #[test]
    fn kaputte_datei_gibt_vorgabe_ohne_panik() {
        let verzeichnis = testpfad("kaputt");
        fs::create_dir_all(&verzeichnis).unwrap();
        let pfad = verzeichnis.join("face-position.json");
        fs::write(&pfad, b"{halb").unwrap();
        assert_eq!(laden(&pfad, (17, 29)), (17, 29));
        fs::remove_dir_all(verzeichnis).unwrap();
    }
}
