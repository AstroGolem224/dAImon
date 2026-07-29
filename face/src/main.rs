//! dAImon-Face: minimale, transparente Layer-Surface nur mit `wl_shm`.

mod diag;
mod input;
mod surface;

use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use diag::{DiagSocket, FaceState};
use input::Box2D;
use smithay_client_toolkit::{
    compositor::{CompositorHandler, CompositorState},
    delegate_registry,
    output::{OutputHandler, OutputState},
    registry::{ProvidesRegistryState, RegistryState},
    registry_handlers,
    shell::wlr_layer::{LayerShell, LayerShellHandler, LayerSurface, LayerSurfaceConfigure},
    shm::{slot::SlotPool, Shm, ShmHandler},
};
use surface::OverlaySurface;
use wayland_client::{
    globals::registry_queue_init,
    protocol::{wl_output, wl_surface},
    Connection, QueueHandle,
};

#[derive(Debug, Default)]
struct Optionen {
    diag_socket: Option<PathBuf>,
    input_region: Option<Box2D>,
}

fn rechteck(s: &str) -> Result<Box2D, String> {
    let teile = s
        .split(',')
        .map(str::trim)
        .map(str::parse::<i32>)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| format!("ungueltiges Rechteck: {s}"))?;
    if teile.len() != 4 {
        return Err(format!("Rechteck braucht x,y,w,h: {s}"));
    }
    Ok(Box2D {
        x: teile[0],
        y: teile[1],
        w: teile[2],
        h: teile[3],
    })
}

fn optionen() -> Result<Optionen, String> {
    let mut ergebnis = Optionen::default();
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--diag-socket" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--diag-socket braucht einen Pfad".to_string())?;
                ergebnis.diag_socket = Some(PathBuf::from(wert));
            }
            "--input-region" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--input-region braucht x,y,w,h".to_string())?;
                ergebnis.input_region = Some(rechteck(&wert)?);
            }
            "--help" | "-h" => {
                println!(
                    "Verwendung: daimon-face [--diag-socket PFAD] \
                     [--input-region x,y,w,h]"
                );
                std::process::exit(0);
            }
            _ => return Err(format!("unbekanntes Argument: {arg}")),
        }
    }
    Ok(ergebnis)
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
    overlay: Option<OverlaySurface>,
    diagnose: Arc<Mutex<FaceState>>,
    beendet: bool,
}

impl App {
    fn commit_zaehlen(&self) {
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.commit_gezaehlt(),
            Err(vergiftet) => vergiftet.into_inner().commit_gezaehlt(),
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

        let committiert = self
            .overlay
            .as_mut()
            .is_some_and(|overlay| overlay.transparenten_puffer_committen(&mut self.pool));
        if committiert {
            self.commit_zaehlen();
            println!("READY pid={}", std::process::id());
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

    let verbindung = Connection::connect_to_env().expect("Wayland-Verbindung");
    let (globals, mut event_queue) =
        registry_queue_init::<App>(&verbindung).expect("Wayland-Registry");
    let qh = event_queue.handle();

    let compositor = CompositorState::bind(&globals, &qh).expect("wl_compositor fehlt");
    let layer_shell = LayerShell::bind(&globals, &qh).expect("zwlr_layer_shell_v1 fehlt");
    let shm = Shm::bind(&globals, &qh).expect("wl_shm fehlt");
    let pool = SlotPool::new(4, &shm).expect("wl_shm-SlotPool");
    let diagnose = Arc::new(Mutex::new(FaceState {
        mood: "idle".into(),
        sprite: "transparent".into(),
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
        overlay: None,
        diagnose,
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
        &layer_shell,
        &qh,
        &output,
        optionen.input_region,
    );
    if !overlay.initial_commit() {
        std::process::exit(4);
    }
    app.overlay = Some(overlay);

    // blocking_dispatch haelt den Idle-Pfad in poll(); es gibt weder Timer
    // noch dauernd neu armierte Frame-Callbacks.
    while !app.beendet {
        if let Err(fehler) = event_queue.blocking_dispatch(&mut app) {
            eprintln!("Wayland-Ereignisschleife beendet: {fehler}");
            break;
        }
    }
}
