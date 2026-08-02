//! Schreibender Steuer-Socket fuer Tests und lokale Werkzeuge.
//!
//! Absichtlich getrennt vom rein lesenden Diagnose-Socket. Der Thread
//! blockiert in `accept()` und weckt die Hauptschleife nur bei einem Befehl.

use std::{
    io::{self, BufRead, BufReader, Read, Write},
    os::unix::net::{UnixListener, UnixStream},
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

use calloop::channel::Sender;

pub struct ControlSocket {
    pfad: PathBuf,
}

struct UmaskGuard(libc::mode_t);
static UMASK_SPERRE: Mutex<()> = Mutex::new(());

impl Drop for UmaskGuard {
    fn drop(&mut self) {
        // SAFETY: umask akzeptiert jeden mode_t-Wert. Der Guard stellt den
        // unmittelbar zuvor gelesenen Prozesswert wieder her.
        unsafe {
            libc::umask(self.0);
        }
    }
}

fn restriktiv_binden(pfad: &Path) -> io::Result<UnixListener> {
    // umask ist prozessweit. Besonders parallele Tests (oder mehrere lokale
    // Sockets beim Start) duerfen ihre Restore-Werte nicht verschachteln.
    let _sperre = UMASK_SPERRE
        .lock()
        .unwrap_or_else(|vergiftet| vergiftet.into_inner());
    // 0177 entfernt Gruppen-/Fremdrechte und die fuer einen Socket
    // unnoetigen Ausfuehrungsbits bereits bei seiner atomaren Erzeugung.
    // SAFETY: umask hat keine ungueltigen Zeiger oder Lebenszeiten.
    let vorher = unsafe { libc::umask(0o177) };
    let guard = UmaskGuard(vorher);
    let ergebnis = UnixListener::bind(pfad);
    drop(guard);
    ergebnis
}

impl ControlSocket {
    pub fn starten(pfad: &Path, sender: Sender<String>) -> Result<Self, String> {
        Self::starten_mit_timeout(pfad, sender, Duration::from_secs(3))
    }

    fn starten_mit_timeout(
        pfad: &Path,
        sender: Sender<String>,
        lese_timeout: Duration,
    ) -> Result<Self, String> {
        let _ = std::fs::remove_file(pfad);
        if let Some(eltern) = pfad.parent() {
            std::fs::create_dir_all(eltern)
                .map_err(|fehler| format!("Control-Socket-Verzeichnis: {fehler}"))?;
        }
        let listener = restriktiv_binden(pfad)
            .map_err(|fehler| format!("Control-Socket {}: {fehler}", pfad.display()))?;

        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(stream) = stream else { continue };
                // Ein Thread pro Client waere fuer diesen lokalen Testkanal
                // unverhaeltnismaessig. Das Timeout gibt den sequentiellen
                // Accept-Loop nach einem stillen Client wieder frei.
                verbindung_bearbeiten(stream, &sender, lese_timeout);
            }
        });

        Ok(Self {
            pfad: pfad.to_path_buf(),
        })
    }
}

fn verbindung_bearbeiten(mut stream: UnixStream, sender: &Sender<String>, lese_timeout: Duration) {
    if stream.set_read_timeout(Some(lese_timeout)).is_err() {
        let _ = stream.write_all(b"err\n");
        return;
    }
    let mut zeile = String::new();
    // Das Limit liegt vor read_line: 129 gelesene Bytes beweisen, dass die
    // erlaubten 128 ueberschritten wurden, auch ganz ohne Zeilenumbruch.
    let gelesen = BufReader::new((&stream).take(129))
        .read_line(&mut zeile)
        .unwrap_or(0);
    let ok = gelesen <= 128
        && befehl_parsen(&zeile)
            .and_then(|zustand| sender.send(zustand.to_owned()).ok())
            .is_some();
    let _ = stream.write_all(if ok { b"ok\n" } else { b"err\n" });
}

/// Bekannte Hub-Moods. Der Steuer-Socket nimmt sie an, damit T-1.8 pruefbar
/// ist: der Ton haengt am Mood, und `needs_input` und `failed` sind
/// derselbe Sprite -- ueber `state dringend` waeren sie nicht zu trennen.
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

/// Gibt den weiterzureichenden Befehl zurueck. `state X` liefert den
/// Sprite-Namen, `mood X` das mit `mood:` vorangestellte Wort -- die
/// Hauptschleife unterscheidet daran, welchen Weg sie nimmt. Sichtbarkeit
/// wird ebenfalls nur markiert; den echten Zustandswechsel fuehrt dieselbe
/// App-Methode aus, die auch von einem Mood-Wechsel benutzt wird.
fn befehl_parsen(zeile: &str) -> Option<String> {
    // Der Zeilenumbruch ist Teil des Vertrags, nicht Beiwerk: eine halbe
    // Zeile ist keine Nachricht. `strip_suffix` statt `trim` -- sonst waere
    // "state ruhig" ohne Abschluss plötzlich gueltig, und ein Client, der
    // mitten im Senden abbricht, wuerde einen Zustandswechsel ausloesen.
    let rumpf = zeile.strip_suffix('\n')?;
    match rumpf {
        "state ruhig" => Some("ruhig".to_owned()),
        "state dringend" => Some("dringend".to_owned()),
        "menu ears_aus" => Some("menu:ears_aus".to_owned()),
        "menu eyes_aus" => Some("menu:eyes_aus".to_owned()),
        "menu beenden" => Some("menu:beenden".to_owned()),
        "sichtbar an" => Some("sichtbar:true".to_owned()),
        "sichtbar aus" => Some("sichtbar:false".to_owned()),
        _ => rumpf
            .strip_prefix("mood ")
            .filter(|name| MOODS.contains(name))
            .map(|name| format!("mood:{name}")),
    }
}

impl Drop for ControlSocket {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.pfad);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        io::{Read, Write},
        os::unix::fs::PermissionsExt,
        sync::atomic::{AtomicU64, Ordering},
    };

    static NAECHSTER_PFAD: AtomicU64 = AtomicU64::new(0);

    fn socket_pfad(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "daimon-control-{name}-{}-{}.sock",
            std::process::id(),
            NAECHSTER_PFAD.fetch_add(1, Ordering::Relaxed)
        ))
    }

    #[test]
    fn mood_wird_erkannt_und_unbekanntes_abgelehnt() {
        assert_eq!(
            befehl_parsen("mood needs_input\n").as_deref(),
            Some("mood:needs_input")
        );
        assert_eq!(
            befehl_parsen("mood failed\n").as_deref(),
            Some("mood:failed")
        );
        assert_eq!(befehl_parsen("mood gibt-es-nicht\n"), None);
        assert_eq!(befehl_parsen("mood\n"), None);
        assert_eq!(befehl_parsen("mood \n"), None);
    }

    #[test]
    fn nur_bekannte_exakte_befehle_werden_angenommen() {
        assert_eq!(befehl_parsen("state ruhig\n").as_deref(), Some("ruhig"));
        assert_eq!(
            befehl_parsen("state dringend\n").as_deref(),
            Some("dringend")
        );
        assert_eq!(befehl_parsen("state panik\n"), None);
        assert_eq!(befehl_parsen("state ruhig extra\n"), None);
        assert_eq!(befehl_parsen("state ruhig"), None);
        assert_eq!(
            befehl_parsen("sichtbar an\n").as_deref(),
            Some("sichtbar:true")
        );
        assert_eq!(
            befehl_parsen("sichtbar aus\n").as_deref(),
            Some("sichtbar:false")
        );
        assert_eq!(befehl_parsen("sichtbar vielleicht\n"), None);
    }

    #[test]
    fn stille_verbindung_blockiert_folgenden_client_nicht() {
        let pfad = socket_pfad("timeout");
        let (sender, _empfaenger) = calloop::channel::channel();
        let _socket =
            ControlSocket::starten_mit_timeout(&pfad, sender, Duration::from_millis(50)).unwrap();
        let _stiller_client = UnixStream::connect(&pfad).unwrap();
        let mut client = UnixStream::connect(&pfad).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();
        client.write_all(b"state ruhig\n").unwrap();
        let mut antwort = String::new();
        client.read_to_string(&mut antwort).unwrap();
        assert_eq!(antwort, "ok\n");
    }

    #[test]
    fn riesenzeile_wird_vor_dem_timeout_begrenzt() {
        let pfad = socket_pfad("limit");
        let (sender, _empfaenger) = calloop::channel::channel();
        let _socket =
            ControlSocket::starten_mit_timeout(&pfad, sender, Duration::from_secs(2)).unwrap();
        let mut client = UnixStream::connect(&pfad).unwrap();
        client
            .set_read_timeout(Some(Duration::from_millis(500)))
            .unwrap();
        client.write_all(&[b'x'; 129]).unwrap();
        let mut antwort = String::new();
        client.read_to_string(&mut antwort).unwrap();
        assert_eq!(antwort, "err\n");
    }

    #[test]
    fn socket_ist_bereits_beim_bind_nur_fuer_eigentuemer_offen() {
        let pfad = socket_pfad("umask");
        let listener = restriktiv_binden(&pfad).unwrap();
        let modus = fs::metadata(&pfad).unwrap().permissions().mode() & 0o777;
        assert_eq!(modus, 0o600);
        drop(listener);
        fs::remove_file(pfad).unwrap();
    }
}
