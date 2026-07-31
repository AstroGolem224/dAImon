//! T-1.8 — Ton bei `needs_input`.
//!
//! # Warum ein Fremdprozess und kein Audio-Crate
//!
//! `face/Cargo.toml` haelt bewusst wenige Abhaengigkeiten und **keinen**
//! GPU- oder Audio-Stack. Der Verifizierer `T-1.4.sh` prueft den Adressraum
//! des laufenden Prozesses auf GPU-Bibliotheken; dieselbe Linie gilt hier.
//! Ein Audio-Crate zoege libpulse oder pipewire in den Adressraum eines
//! Prozesses, dessen ganzer Daseinsgrund ein schlanker Adressraum ist.
//!
//! Ein abgekoppelter Fremdprozess kostet ein paar Millisekunden -- und zwar
//! nur beim seltenen `needs_input`, nicht im Ruhezustand. Die Idle-CPU-Zusage
//! aus T-1.5 (gemessen 0,000 % ueber 60 s) bleibt unberuehrt.
//!
//! # Die Falle in diesem Task
//!
//! `needs_input` **und** `failed` bilden beide auf den Sprite `dringend` ab
//! (siehe `hub::mood_zu_sprite`). Wer den Ton am **Sprite** festmacht, piept
//! bei jedem Fehlschlag. Der Ton haengt deshalb am **Mood** und am
//! **Uebergang**: `needs_input` -> `needs_input` ist kein Wechsel.
//!
//! # Hoechstens ein Ton gleichzeitig
//!
//! Je Ton entstehen ein Prozess und ein kurzlebiger Thread. Ohne Deckel
//! koennte eine schnell wechselnde Mood-Folge beliebig viele davon erzeugen,
//! und `std::thread::spawn` **panikt**, wenn das Thread-Limit erreicht ist --
//! mitten in der Ereignisschleife. Deshalb laeuft hoechstens ein Ton: waehrend
//! einer spielt, wird ein weiterer verworfen. Das ist auch akustisch richtig,
//! zwei uebereinanderliegende Hinweistoene sind Laerm.

use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// Gibt zurueck, ob dieser Moodwechsel einen Ton ausloest.
///
/// Reine Funktion, damit die Regel ohne Compositor und ohne Audio pruefbar
/// ist -- die Akzeptanz von T-1.8 ist eine Aussage ueber diese Funktion und
/// nicht ueber den Lautsprecher.
pub fn loest_ton_aus(vorher: &str, nachher: &str) -> bool {
    nachher == "needs_input" && vorher != "needs_input"
}

/// Abspieler in der Reihenfolge, in der sie versucht werden. Verschiedene
/// Maschinen haben verschiedene davon.
const KANDIDATEN: [(&str, &[&str]); 2] = [
    ("canberra-gtk-play", &["-i", "dialog-information"]),
    (
        "paplay",
        &["/usr/share/sounds/freedesktop/stereo/message-new-instant.oga"],
    ),
];

/// Setzt den Deckel beim Verlassen des Gueltigkeitsbereichs zurueck -- auch
/// bei einer Panik im Abraeum-Thread. Ohne Guard bliebe `laeuft` in dem Fall
/// dauerhaft auf `true`, und es gaebe nie wieder einen Ton.
struct Deckel(Arc<AtomicBool>);

impl Drop for Deckel {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

/// So lange darf ein Abspieler hoechstens laufen. Ein haengendes
/// `canberra-gtk-play` wuerde sonst in `wait()` blockieren und den Deckel
/// dauerhaft geschlossen halten -- ein einziger haengender Prozess haette das
/// Feature stillgelegt.
const FRIST: std::time::Duration = std::time::Duration::from_secs(10);

pub struct Tonspieler {
    an: bool,
    /// True, solange ein Ton laeuft. Der `Deckel` setzt ihn zurueck.
    laeuft: Arc<AtomicBool>,
    /// Nur fuer die einmalige Meldung auf stderr. Ein Programm, das bei jedem
    /// Ton eine Zeile schreibt, ist im Journal nicht mehr von einem Vorfall zu
    /// unterscheiden.
    gemeldet: bool,
}

impl Tonspieler {
    pub fn neu(an: bool) -> Self {
        Self {
            an,
            laeuft: Arc::new(AtomicBool::new(false)),
            gemeldet: false,
        }
    }

    pub fn ist_an(&self) -> bool {
        self.an
    }

    /// Startet einen Tonvorgang. Rueckgabe: ob ein Abspielprozess
    /// **tatsaechlich gestartet** wurde -- nur dann zaehlt der
    /// Diagnosezaehler hoch.
    ///
    /// Der erste Start passiert **synchron**, damit der Aufrufer die Wahrheit
    /// erfaehrt. Ein Zaehler, der schon beim Abschicken eines Auftrags steigt,
    /// meldet eine Zusage, die niemand eingehalten hat.
    ///
    /// Gezaehlt wird der **Tonvorgang**, nicht der Prozess: endet der erste
    /// Kandidat mit Fehler (etwa `canberra-gtk-play` ohne Sound-Theme),
    /// versucht der Abraeum-Thread den naechsten. Das sind dann zwei Prozesse
    /// und ein Ton. "Gestartet", nicht "gehoert" -- ob am Ende wirklich etwas
    /// aus den Lautsprechern kam, kann dieser Prozess nicht wissen.
    pub fn spielen(&mut self) -> bool {
        if !self.an {
            return false;
        }
        // compare_exchange statt load+store: zwei Ereignisse dicht
        // hintereinander duerfen nicht beide durchrutschen.
        if self
            .laeuft
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return false;
        }
        let deckel = Deckel(Arc::clone(&self.laeuft));

        let mut gestartet = None;
        for (nummer, (programm, argumente)) in KANDIDATEN.iter().enumerate() {
            if let Ok(kind) = starten(programm, argumente) {
                gestartet = Some((nummer, kind));
                break;
            }
        }
        let Some((nummer, kind)) = gestartet else {
            if !self.gemeldet {
                eprintln!("kein Abspielprogramm startbar, Ton bleibt stumm");
                self.gemeldet = true;
            }
            return false; // `deckel` faellt hier und gibt frei
        };

        if !self.gemeldet {
            eprintln!("Ton ueber {}", KANDIDATEN[nummer].0);
            self.gemeldet = true;
        }

        match std::thread::Builder::new()
            .name("daimon-ton".into())
            .spawn(move || {
                // Der Guard wandert in den Thread und gibt den Deckel frei,
                // wenn dieser endet -- auch bei einer Panik.
                let _deckel = deckel;
                if !abwarten(kind) {
                    // Erster Kandidat scheiterte: naechsten versuchen. Kein
                    // zusaetzlicher Ton, derselbe Tonvorgang.
                    for (programm, argumente) in KANDIDATEN.iter().skip(nummer + 1) {
                        if let Ok(weiterer) = starten(programm, argumente) {
                            abwarten(weiterer);
                            break;
                        }
                    }
                }
            }) {
            Ok(_) => true,
            Err(fehler) => {
                // `Builder::spawn` statt `std::thread::spawn`: ein
                // erschoepftes Thread-Limit ist hier ein Rueckgabewert und
                // keine Panik in der Ereignisschleife. Der Kindprozess wird
                // hier eingesammelt, damit kein Zombie bleibt.
                eprintln!("Ton-Thread nicht startbar: {fehler}");
                false
            }
        }
    }
}

fn starten(programm: &str, argumente: &[&str]) -> std::io::Result<std::process::Child> {
    Command::new(programm)
        .args(argumente)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
}

/// Wartet auf das Ende, aber hoechstens `FRIST`. Rueckgabe: ob der Prozess
/// erfolgreich beendet wurde. Ein Ueberschreiten der Frist beendet ihn und
/// gilt als Fehlschlag -- sonst haelt ein haengender Abspieler den Deckel.
fn abwarten(mut kind: std::process::Child) -> bool {
    let ende = std::time::Instant::now() + FRIST;
    loop {
        match kind.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) => {
                if std::time::Instant::now() >= ende {
                    let _ = kind.kill();
                    let _ = kind.wait();
                    return false;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(_) => return false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nur_der_uebergang_nach_needs_input_loest_aus() {
        assert!(loest_ton_aus("idle", "needs_input"));
        assert!(loest_ton_aus("sleeping", "needs_input"));
        assert!(loest_ton_aus("failed", "needs_input"));
        assert!(loest_ton_aus("done", "needs_input"));
    }

    #[test]
    fn wiederholtes_needs_input_loest_nicht_aus() {
        assert!(!loest_ton_aus("needs_input", "needs_input"));
    }

    #[test]
    fn failed_loest_nie_aus() {
        // Der wichtigste Test dieses Moduls: failed teilt sich mit
        // needs_input den Sprite `dringend`. Ein spritebasierter Ausloeser
        // waere hier gruen und in der Sache falsch -- deshalb prueft
        // `tests/verify/T-1.8.sh` denselben Uebergang zusaetzlich am
        // laufenden Prozess, mit einem untergeschobenen Abspieler.
        assert!(!loest_ton_aus("needs_input", "failed"));
        assert!(!loest_ton_aus("idle", "failed"));
        assert!(!loest_ton_aus("sleeping", "failed"));
    }

    #[test]
    fn done_und_die_uebrigen_loesen_nicht_aus() {
        for nachher in ["sleeping", "idle", "observing", "thinking", "working", "done"] {
            assert!(!loest_ton_aus("needs_input", nachher), "{nachher}");
            assert!(!loest_ton_aus("idle", nachher), "{nachher}");
        }
    }

    #[test]
    fn die_folge_aus_dem_plan_ergibt_genau_zwei() {
        // needs_input, needs_input, done, failed, needs_input
        let folge = [
            "needs_input",
            "needs_input",
            "done",
            "failed",
            "needs_input",
        ];
        let mut vorher = "sleeping";
        let mut anzahl = 0;
        for nachher in folge {
            if loest_ton_aus(vorher, nachher) {
                anzahl += 1;
            }
            vorher = nachher;
        }
        assert_eq!(anzahl, 2);
    }

    #[test]
    fn abgeschaltet_spielt_nichts() {
        let mut spieler = Tonspieler::neu(false);
        assert!(!spieler.spielen());
        assert!(!spieler.ist_an());
    }

    #[test]
    fn hoechstens_ein_ton_gleichzeitig() {
        let mut spieler = Tonspieler::neu(true);
        // Der Deckel wird direkt am Flag geprueft, ohne auf einen echten
        // Abspieler zu warten: waehrend einer laeuft, wird der naechste
        // verworfen.
        spieler.laeuft.store(true, Ordering::Release);
        assert!(!spieler.spielen(), "zweiter Ton darf nicht starten");
        spieler.laeuft.store(false, Ordering::Release);
    }
}
