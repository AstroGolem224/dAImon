//! T-1.6 — Anbindung an den Hub.
//!
//! Der Hub schiebt (`events.sock`), das Face fragt nicht nach. Das ist der
//! ganze Punkt: ein Poll-Timer im Face haette die Null-Idle-CPU aus T-1.5
//! wieder aufgemacht -- gemessen 0,000 % ueber 60 s, und das ist die Zusage,
//! auf der die Overlay-Architektur steht. Hier blockiert stattdessen ein
//! Thread in `read_line()` und schickt nur dann etwas in die Hauptschleife,
//! wenn sich wirklich etwas geaendert hat.
//!
//! **Kein Log-Spam.** Ein nicht laufender Hub ist der Normalfall, nicht ein
//! Fehler: das Overlay darf ohne ihn starten und soll ihn spaeter einsammeln.
//! Gemeldet wird deshalb nur der *Wechsel* zwischen verbunden und getrennt,
//! nie der Wiederholungsversuch.

use std::{
    io::{BufRead, BufReader, Write},
    os::unix::net::UnixStream,
    path::{Path, PathBuf},
    time::Duration,
};

use calloop::channel::Sender;
use serde_json::Value;

use crate::bubble::Bubble;

/// Das einzige, was das Face vom Hub-Zustand braucht.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HubZustand {
    pub rev: u64,
    pub mood: String,
    pub bubble: Option<Bubble>,
}

impl HubZustand {
    /// Was gilt, wenn kein Hub da ist. Auch bei unbekanntem `v`: ein Snapshot
    /// in einer Fassung, die wir nicht kennen, ist kein Zustand, den wir
    /// darstellen duerfen -- lieber zugeben, dass wir nichts wissen.
    pub fn schlafend() -> Self {
        Self {
            rev: 0,
            mood: "sleeping".into(),
            bubble: None,
        }
    }
}

/// Bekannte Fassung des Hub-Snapshots (T-0.9 liefert `"v": 2`).
const SNAPSHOT_VERSION: u64 = 2;

/// Hub-Mood zum Sprite. In P1 gibt es genau zwei Sprites, und mehr braucht es
/// nicht: entweder das Pet will etwas von Matthias, oder nicht.
///
/// `sleeping` faellt bewusst auf `ruhig` -- ein eigener Schlaf-Sprite ist
/// Phase 2. Ein unbekannter Mood landet ebenfalls auf `ruhig`: das Pet still
/// stehen zu lassen ist der harmlosere Fehler, verglichen mit einem
/// Alarmzustand, den niemand ausgeloest hat.
pub fn mood_zu_sprite(mood: &str) -> &'static str {
    match mood {
        "needs_input" | "failed" => "dringend",
        _ => "ruhig",
    }
}

/// Parst eine Snapshot-Zeile. `None` heisst „unbrauchbar" und wird vom
/// Aufrufer wie ein fehlender Hub behandelt.
pub fn snapshot_lesen(zeile: &str) -> Option<HubZustand> {
    let wert: Value = serde_json::from_str(zeile).ok()?;
    if wert.get("v").and_then(Value::as_u64) != Some(SNAPSHOT_VERSION) {
        return None;
    }
    Some(HubZustand {
        rev: wert.get("rev").and_then(Value::as_u64)?,
        mood: wert.get("mood").and_then(Value::as_str)?.to_owned(),
        bubble: match wert.get("bubble") {
            None | Some(Value::Null) => None,
            Some(bubble) => Some(Bubble {
                title: bubble.get("title").and_then(Value::as_str)?.to_owned(),
                body: bubble.get("body").and_then(Value::as_str)?.to_owned(),
                urgent: bubble.get("urgent").and_then(Value::as_bool)?,
            }),
        },
    })
}

/// Wartezeit zwischen zwei Verbindungsversuchen. Bewusst konstant statt
/// exponentiell: der Hub laeuft entweder oder nicht, und eine Sekunde ist bei
/// einem lokalen Unix-Socket weder Last noch spuerbare Verzoegerung.
const WIEDERHOLUNG: Duration = Duration::from_secs(1);

#[derive(Clone)]
pub struct HubVerbindung {
    _pfad: PathBuf,
    melde_pfad: PathBuf,
}

impl HubVerbindung {
    pub fn starten(pfad: &Path, sender: Sender<HubZustand>) -> Self {
        let pfad_buf = pfad.to_path_buf();
        let melde_pfad = pfad
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("face.sock");
        let arbeits_pfad = pfad_buf.clone();
        std::thread::spawn(move || {
            let mut war_verbunden = false;
            loop {
                match UnixStream::connect(&arbeits_pfad) {
                    Ok(strom) => {
                        if !war_verbunden {
                            eprintln!("Hub verbunden: {}", arbeits_pfad.display());
                            war_verbunden = true;
                        }
                        lesen_bis_ende(strom, &sender);
                        // Verbindung weg: erst melden, dann schlafend melden.
                        eprintln!("Hub getrennt: {}", arbeits_pfad.display());
                        war_verbunden = false;
                        if sender.send(HubZustand::schlafend()).is_err() {
                            return; // Hauptschleife ist weg.
                        }
                    }
                    Err(_) => {
                        // Kein Hub. Kein Log -- das ist der Normalfall beim
                        // Start und wuerde sonst im Sekundentakt ins Journal
                        // laufen.
                        if war_verbunden {
                            war_verbunden = false;
                        }
                    }
                }
                std::thread::sleep(WIEDERHOLUNG);
            }
        });
        Self {
            _pfad: pfad_buf,
            melde_pfad,
        }
    }

    /// Einer von zwei Face->Hub-Typen. Eine neue Verbindung pro Nutzerklick
    /// haelt keinen zusaetzlichen Idle-Poller oder langlebigen Schreibkanal
    /// offen.
    pub fn bubble_dismiss_melden(&self) -> Result<(), String> {
        self.senden("{\"v\":1,\"type\":\"bubble_dismiss\",\"payload\":{}}\n")
    }

    /// T-2.7: Wahrnehmung abschalten. `ziel` ist ein **Schluessel**
    /// (`"ears"`/`"eyes"`) aus `menu::Aktion::ziel`, nie ein Unit-Name --
    /// welche Unit dahintersteht, entscheidet allein die Allowlist im Hub.
    ///
    /// Es gibt bewusst **kein Gegenstueck zum Einschalten**: ein Overlay, das
    /// Wahrnehmung nur abschalten kann, ist fail-safe. Wer hier ein
    /// `wahrnehmung_an` ergaenzt, gibt dem Face eine Faehigkeit, die T-1.7
    /// ihm genommen hat.
    pub fn wahrnehmung_aus_melden(&self, ziel: &str) -> Result<(), String> {
        self.senden(&format!(
            "{{\"v\":1,\"type\":\"wahrnehmung_aus\",\"payload\":{{\"ziel\":\"{ziel}\"}}}}\n"
        ))
    }

    fn senden(&self, zeile: &str) -> Result<(), String> {
        let mut strom = UnixStream::connect(&self.melde_pfad)
            .map_err(|fehler| format!("Face-Meldeweg {}: {fehler}", self.melde_pfad.display()))?;
        strom
            .write_all(zeile.as_bytes())
            .map_err(|fehler| format!("Face-Meldung senden: {fehler}"))
    }
}

fn lesen_bis_ende(strom: UnixStream, sender: &Sender<HubZustand>) {
    let leser = BufReader::new(strom);
    for zeile in leser.lines() {
        let Ok(zeile) = zeile else { return };
        let zustand = match snapshot_lesen(&zeile) {
            Some(z) => z,
            // Unbekanntes `v` oder Muell: als schlafend melden, aber die
            // Verbindung halten -- ein Hub, der eine neuere Fassung spricht,
            // wird nicht dadurch besser, dass wir uns dauernd neu verbinden.
            None => HubZustand::schlafend(),
        };
        if sender.send(zustand).is_err() {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nur_needs_input_und_failed_sind_dringend() {
        assert_eq!(mood_zu_sprite("needs_input"), "dringend");
        assert_eq!(mood_zu_sprite("failed"), "dringend");
        for ruhig in [
            "sleeping",
            "idle",
            "observing",
            "thinking",
            "working",
            "done",
        ] {
            assert_eq!(mood_zu_sprite(ruhig), "ruhig", "{ruhig}");
        }
    }

    #[test]
    fn unbekannter_mood_bleibt_ruhig() {
        assert_eq!(mood_zu_sprite("gibt-es-nicht"), "ruhig");
        assert_eq!(mood_zu_sprite(""), "ruhig");
    }

    #[test]
    fn snapshot_wird_gelesen() {
        let z = snapshot_lesen(
            r#"{"v":2,"rev":7,"mood":"working","sessions":1,"focus":null,"bubble":null}"#,
        )
        .expect("gueltiger Snapshot");
        assert_eq!(z.rev, 7);
        assert_eq!(z.mood, "working");
        assert_eq!(z.bubble, None);
    }

    #[test]
    fn snapshot_enthaelt_blasentext() {
        let z = snapshot_lesen(
            r#"{"v":2,"rev":8,"mood":"done","bubble":{"title":"fertig","body":"Task durch.","urgent":false}}"#,
        )
        .unwrap();
        assert_eq!(
            z.bubble,
            Some(Bubble {
                title: "fertig".into(),
                body: "Task durch.".into(),
                urgent: false,
            })
        );
    }

    #[test]
    fn unbekanntes_v_wird_verworfen() {
        // Der Aufrufer macht daraus "sleeping" -- hier zaehlt nur, dass wir
        // die Zeile NICHT als gueltigen Zustand durchwinken.
        assert!(snapshot_lesen(r#"{"v":99,"rev":7,"mood":"working"}"#).is_none());
        assert!(snapshot_lesen(r#"{"rev":7,"mood":"working"}"#).is_none());
    }

    #[test]
    fn muell_und_fehlende_felder_werden_verworfen() {
        assert!(snapshot_lesen("{kaputt").is_none());
        assert!(snapshot_lesen("").is_none());
        assert!(snapshot_lesen(r#"{"v":2,"mood":"working"}"#).is_none());
        assert!(snapshot_lesen(r#"{"v":2,"rev":7}"#).is_none());
    }

    #[test]
    fn schlafend_ist_der_rueckfall() {
        let s = HubZustand::schlafend();
        assert_eq!(s.mood, "sleeping");
        assert_eq!(mood_zu_sprite(&s.mood), "ruhig");
    }
}
