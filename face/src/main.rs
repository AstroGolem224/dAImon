//! dAImon-Face: minimale, transparente Layer-Surface nur mit `wl_shm`.

mod control;
mod diag;
mod input;
mod sprite;
mod surface;

use std::{
    ffi::OsString,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use calloop::{
    channel::{channel, Event as ChannelEvent},
    EventLoop,
};
use control::ControlSocket;
use diag::{DiagSocket, FaceState};
use smithay_client_toolkit::reexports::calloop_wayland_source::WaylandSource;
use smithay_client_toolkit::{
    compositor::{CompositorHandler, CompositorState},
    delegate_registry,
    output::{OutputHandler, OutputState},
    registry::{ProvidesRegistryState, RegistryState},
    registry_handlers,
    shell::wlr_layer::{LayerShell, LayerShellHandler, LayerSurface, LayerSurfaceConfigure},
    shm::{slot::SlotPool, Shm, ShmHandler},
    subcompositor::SubcompositorState,
};
use sprite::{zustand_abbilden, SpriteAtlas};
use surface::OverlaySurface;
use wayland_client::{
    globals::registry_queue_init,
    protocol::{wl_output, wl_surface},
    Connection, QueueHandle,
};

#[derive(Debug, Default)]
struct Optionen {
    diag_socket: Option<PathBuf>,
    control_socket: Option<PathBuf>,
    sprite_position: Option<(i32, i32)>,
    pet_manifest: Option<PathBuf>,
}

fn punkt(s: &str) -> Result<(i32, i32), String> {
    let teile = s
        .split(',')
        .map(str::trim)
        .map(str::parse::<i32>)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| format!("ungueltige Position: {s}"))?;
    if teile.len() != 2 {
        return Err(format!("Position braucht x,y: {s}"));
    }
    Ok((teile[0], teile[1]))
}

fn optionen() -> Result<Optionen, String> {
    optionen_aus(std::env::args().skip(1))
}

fn optionen_aus<I>(args: I) -> Result<Optionen, String>
where
    I: IntoIterator<Item = String>,
{
    let mut ergebnis = Optionen::default();
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--diag-socket" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--diag-socket braucht einen Pfad".to_string())?;
                ergebnis.diag_socket = Some(PathBuf::from(wert));
            }
            "--control-socket" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--control-socket braucht einen Pfad".to_string())?;
                ergebnis.control_socket = Some(PathBuf::from(wert));
            }
            "--input-region" => {
                return Err("--input-region ist entfallen; Sprite-Position mit \
                     --sprite-position x,y setzen"
                    .to_string());
            }
            "--sprite-position" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--sprite-position braucht x,y".to_string())?;
                ergebnis.sprite_position = Some(punkt(&wert)?);
            }
            "--pet-manifest" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--pet-manifest braucht einen Pfad".to_string())?;
                ergebnis.pet_manifest = Some(PathBuf::from(wert));
            }
            "--help" | "-h" => {
                println!(
                    "Verwendung: daimon-face [--diag-socket PFAD] \
                     [--control-socket PFAD] [--pet-manifest PFAD] \
                     [--sprite-position x,y]"
                );
                std::process::exit(0);
            }
            _ => return Err(format!("unbekanntes Argument: {arg}")),
        }
    }
    Ok(ergebnis)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ManifestQuelle {
    Kommandozeile,
    Umgebung,
    Arbeitsverzeichnis,
    Entwicklungsfallback,
}

impl ManifestQuelle {
    fn beschreibung(self) -> &'static str {
        match self {
            Self::Kommandozeile => "--pet-manifest",
            Self::Umgebung => "DAIMON_PET_MANIFEST",
            Self::Arbeitsverzeichnis => "Arbeitsverzeichnis",
            Self::Entwicklungsfallback => "CARGO_MANIFEST_DIR-Entwicklungsfallback",
        }
    }
}

fn pet_manifest_waehlen(
    kommandozeile: Option<PathBuf>,
    umgebung: Option<OsString>,
    arbeitsverzeichnis: &Path,
    entwicklungsmanifest: &Path,
) -> (PathBuf, ManifestQuelle) {
    if let Some(pfad) = kommandozeile {
        return (pfad, ManifestQuelle::Kommandozeile);
    }
    if let Some(pfad) = umgebung.filter(|wert| !wert.is_empty()) {
        return (PathBuf::from(pfad), ManifestQuelle::Umgebung);
    }
    let lokales_manifest = arbeitsverzeichnis.join("assets/pet.json");
    if lokales_manifest.is_file() {
        return (lokales_manifest, ManifestQuelle::Arbeitsverzeichnis);
    }
    (
        entwicklungsmanifest.to_path_buf(),
        ManifestQuelle::Entwicklungsfallback,
    )
}

fn watchdog_starten() {
    let sekunden = std::env::var("DAIMON_MAX_SECS")
        .ok()
        .and_then(|wert| wert.parse::<u64>().ok())
        .unwrap_or(90);
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(sekunden));
        eprintln!("WATCHDOG: {sekunden}s erreicht, Prozess wird hart beendet");
        std::process::exit(3);
    });
}

struct App {
    registry_state: RegistryState,
    output_state: OutputState,
    shm: Shm,
    compositor: CompositorState,
    pool: SlotPool,
    atlas: SpriteAtlas,
    overlay: Option<OverlaySurface>,
    diagnose: Arc<Mutex<FaceState>>,
    aktueller_zustand: String,
    beendet: bool,
}

impl App {
    fn commit_zaehlen(&self, mit_buffer: bool) {
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.commit_gezaehlt(mit_buffer),
            Err(vergiftet) => vergiftet.into_inner().commit_gezaehlt(mit_buffer),
        }
    }

    fn commits_zaehlen(&self, anzahl: u64) {
        for _ in 0..anzahl {
            self.commit_zaehlen(true);
        }
    }

    fn diagnose_sprite_setzen(&self, name: &str) {
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.sprite = name.to_owned(),
            Err(vergiftet) => vergiftet.into_inner().sprite = name.to_owned(),
        }
    }

    fn zustand_setzen(&mut self, name: &str) {
        if self.aktueller_zustand == name {
            return;
        }
        if self.overlay.is_none()
            || !self
                .overlay
                .as_ref()
                .is_some_and(OverlaySurface::hat_puffer)
        {
            self.aktueller_zustand = name.to_owned();
            return;
        }

        let ergebnis = {
            let Self {
                overlay,
                compositor,
                pool,
                atlas,
                ..
            } = self;
            overlay
                .as_mut()
                .expect("Overlay wurde oben geprueft")
                .sprite_committen(compositor, pool, atlas, name)
        };
        match ergebnis {
            Ok(commits) => {
                self.aktueller_zustand = name.to_owned();
                self.commits_zaehlen(commits);
                self.diagnose_sprite_setzen(name);
            }
            Err(fehler) => {
                eprintln!("Sprite-Zustand {name} konnte nicht gerendert werden: {fehler}")
            }
        }
    }
}

impl CompositorHandler for App {
    fn scale_factor_changed(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: i32,
    ) {
    }

    fn transform_changed(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: wl_output::Transform,
    ) {
    }

    fn frame(&mut self, _: &Connection, _: &QueueHandle<Self>, _: &wl_surface::WlSurface, _: u32) {
        // Es gibt fuer die transparente Basissurface nie einen frame()-Request.
        // Spaetere Renderer duerfen ihn bei !dirty nicht erneut armieren.
    }

    fn surface_enter(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: &wl_output::WlOutput,
    ) {
    }

    fn surface_leave(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_surface::WlSurface,
        _: &wl_output::WlOutput,
    ) {
    }
}

impl OutputHandler for App {
    fn output_state(&mut self) -> &mut OutputState {
        &mut self.output_state
    }

    fn new_output(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}

    fn update_output(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}

    fn output_destroyed(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {
        // Die Layer-Surface wird bei Output-Removal vom Compositor geschlossen.
        // Eine spaetere Multi-Monitor-Stufe erzeugt sie am neuen Output neu.
    }
}

impl LayerShellHandler for App {
    fn closed(&mut self, _: &Connection, _: &QueueHandle<Self>, _: &LayerSurface) {
        self.overlay = None;
        self.beendet = true;
    }

    fn configure(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        layer: &LayerSurface,
        _: LayerSurfaceConfigure,
        _: u32,
    ) {
        let muss_zeichnen = self
            .overlay
            .as_ref()
            .is_some_and(|overlay| overlay.layer() == layer && !overlay.hat_puffer());
        if !muss_zeichnen {
            return;
        }

        let basis_committiert = self
            .overlay
            .as_mut()
            .is_some_and(|overlay| overlay.transparenten_puffer_committen(&mut self.pool));
        if basis_committiert {
            self.commit_zaehlen(true);
            let sprite_ergebnis = {
                let Self {
                    overlay,
                    compositor,
                    pool,
                    atlas,
                    aktueller_zustand,
                    ..
                } = self;
                overlay
                    .as_mut()
                    .expect("Overlay existiert im configure")
                    .sprite_committen(compositor, pool, atlas, aktueller_zustand)
            };
            match sprite_ergebnis {
                Ok(commits) => {
                    self.commits_zaehlen(commits);
                    self.diagnose_sprite_setzen(&self.aktueller_zustand);
                    println!("READY pid={}", std::process::id());
                }
                Err(fehler) => {
                    eprintln!("Initiales Sprite konnte nicht gerendert werden: {fehler}");
                    self.beendet = true;
                }
            }
        } else {
            self.beendet = true;
        }
    }
}

impl ShmHandler for App {
    fn shm_state(&mut self) -> &mut Shm {
        &mut self.shm
    }
}

impl ProvidesRegistryState for App {
    fn registry(&mut self) -> &mut RegistryState {
        &mut self.registry_state
    }
    registry_handlers![OutputState];
}

delegate_registry!(App);
smithay_client_toolkit::delegate_dispatch2!(App);

fn main() {
    let optionen = optionen().unwrap_or_else(|fehler| {
        eprintln!("{fehler}");
        std::process::exit(2);
    });
    watchdog_starten();

    let entwicklungsmanifest = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("assets/pet.json");
    let arbeitsverzeichnis = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let (manifest, manifest_quelle) = pet_manifest_waehlen(
        optionen.pet_manifest.clone(),
        std::env::var_os("DAIMON_PET_MANIFEST"),
        &arbeitsverzeichnis,
        &entwicklungsmanifest,
    );
    eprintln!(
        "Pet-Manifest ({}): {}",
        manifest_quelle.beschreibung(),
        manifest.display()
    );
    let atlas = SpriteAtlas::laden(&manifest).unwrap_or_else(|fehler| {
        eprintln!("{fehler}");
        std::process::exit(2);
    });
    let fehlende_zustaende = ["ruhig", "dringend"]
        .into_iter()
        .filter(|name| zustand_abbilden(name, &atlas.layout).zurueckgefallen)
        .collect::<Vec<_>>();
    if !fehlende_zustaende.is_empty() {
        eprintln!(
            "Sprite-Manifest fehlen Zustand/Zeile fuer {}; Rueckfall auf idle",
            fehlende_zustaende.join(", ")
        );
    }

    let verbindung = Connection::connect_to_env().expect("Wayland-Verbindung");
    let (globals, mut event_queue) =
        registry_queue_init::<App>(&verbindung).expect("Wayland-Registry");
    let qh = event_queue.handle();

    let compositor = CompositorState::bind(&globals, &qh).expect("wl_compositor fehlt");
    let subcompositor = SubcompositorState::bind(compositor.wl_compositor().clone(), &globals, &qh)
        .expect("wl_subcompositor fehlt");
    let layer_shell = LayerShell::bind(&globals, &qh).expect("zwlr_layer_shell_v1 fehlt");
    let shm = Shm::bind(&globals, &qh).expect("wl_shm fehlt");
    let pool = SlotPool::new(4, &shm).expect("wl_shm-SlotPool");
    let diagnose = Arc::new(Mutex::new(FaceState {
        mood: "idle".into(),
        sprite: "ruhig".into(),
        ..FaceState::default()
    }));
    let _diag_socket = optionen
        .diag_socket
        .as_deref()
        .and_then(|pfad| DiagSocket::starten(pfad, Arc::clone(&diagnose)));

    let mut app = App {
        registry_state: RegistryState::new(&globals),
        output_state: OutputState::new(&globals, &qh),
        shm,
        compositor,
        // Fuer den 1x1-ARGB-Puffer reichen vier Byte; SlotPool vergroessert
        // sich spaeter fuer Sprites selbststaendig.
        pool,
        atlas,
        overlay: None,
        diagnose,
        aktueller_zustand: "ruhig".into(),
        beendet: false,
    };

    // Erst nach den Roundtrips kennt OutputState die wl_output-Objekte.
    event_queue.roundtrip(&mut app).expect("Output-Roundtrip");
    event_queue.roundtrip(&mut app).expect("Output-Roundtrip");
    let output = app
        .output_state
        .outputs()
        .next()
        .expect("kein wl_output gefunden");

    let overlay = OverlaySurface::neu(
        &app.compositor,
        &subcompositor,
        &layer_shell,
        &qh,
        &output,
        optionen.sprite_position.unwrap_or((0, 0)),
    );
    if !overlay.initial_commit() {
        std::process::exit(4);
    }
    app.commit_zaehlen(false);
    app.overlay = Some(overlay);

    let mut event_loop: EventLoop<App> =
        EventLoop::try_new().expect("calloop-EventLoop konnte nicht starten");
    let handle = event_loop.handle();
    WaylandSource::new(verbindung.clone(), event_queue)
        .insert(handle.clone())
        .expect("Wayland-FD konnte nicht in calloop eingefuegt werden");

    let (control_sender, control_channel) = channel::<String>();
    handle
        .insert_source(control_channel, |event, _, app| {
            if let ChannelEvent::Msg(zustand) = event {
                app.zustand_setzen(&zustand);
            }
        })
        .expect("Control-Kanal konnte nicht in calloop eingefuegt werden");
    let _control_socket = optionen.control_socket.as_deref().map(|pfad| {
        ControlSocket::starten(pfad, control_sender).unwrap_or_else(|fehler| {
            eprintln!("{fehler}");
            std::process::exit(2);
        })
    });

    // None blockiert bis Wayland oder ein Steuerbefehl den poll()-Aufruf
    // weckt. Es gibt weder Timer noch dauernd neu armierte Frame-Callbacks.
    while !app.beendet {
        if let Err(fehler) = event_loop.dispatch(None, &mut app) {
            eprintln!("calloop-Ereignisschleife beendet: {fehler}");
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        sync::atomic::{AtomicU64, Ordering},
    };

    static NAECHSTES_VERZEICHNIS: AtomicU64 = AtomicU64::new(0);

    fn argumente(werte: &[&str]) -> Vec<String> {
        werte.iter().map(|wert| (*wert).to_string()).collect()
    }

    fn testverzeichnis() -> PathBuf {
        std::env::temp_dir().join(format!(
            "daimon-manifest-{}-{}",
            std::process::id(),
            NAECHSTES_VERZEICHNIS.fetch_add(1, Ordering::Relaxed)
        ))
    }

    #[test]
    fn sprite_position_hat_eigene_option() {
        let optionen = optionen_aus(argumente(&["--sprite-position", "17,-4"])).unwrap();
        assert_eq!(optionen.sprite_position, Some((17, -4)));
    }

    #[test]
    fn input_region_wird_nicht_still_als_position_gedeutet() {
        let fehler = optionen_aus(argumente(&["--input-region", "1,2,3,4"])).unwrap_err();
        assert!(fehler.contains("--input-region ist entfallen"));
        assert!(fehler.contains("--sprite-position"));
    }

    #[test]
    fn manifest_prioritaet_ist_cli_env_cwd_dev() {
        let cwd = testverzeichnis();
        let assets = cwd.join("assets");
        fs::create_dir_all(&assets).unwrap();
        fs::write(assets.join("pet.json"), b"{}").unwrap();
        let dev = PathBuf::from("/entwicklung/pet.json");
        let cli = PathBuf::from("/cli/pet.json");
        let env = OsString::from("/env/pet.json");

        assert_eq!(
            pet_manifest_waehlen(Some(cli.clone()), Some(env.clone()), &cwd, &dev),
            (cli, ManifestQuelle::Kommandozeile)
        );
        assert_eq!(
            pet_manifest_waehlen(None, Some(env), &cwd, &dev),
            (PathBuf::from("/env/pet.json"), ManifestQuelle::Umgebung)
        );
        assert_eq!(
            pet_manifest_waehlen(None, None, &cwd, &dev),
            (assets.join("pet.json"), ManifestQuelle::Arbeitsverzeichnis)
        );

        fs::remove_file(assets.join("pet.json")).unwrap();
        assert_eq!(
            pet_manifest_waehlen(None, None, &cwd, &dev),
            (dev, ManifestQuelle::Entwicklungsfallback)
        );
        fs::remove_dir_all(cwd).unwrap();
    }
}
