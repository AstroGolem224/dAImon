//! Spike T-1.3 — wlr-layer-shell overlay auf KWin 6.7.3.
//!
//! Harte Vorgabe aus DESIGN.md §8.1 / §4.5:
//!   * Layer::Overlay (nicht Top — Fullscreen verdeckt Top)
//!   * Anchor auf allen vier Kanten, exclusive_zone = -1
//!   * keyboard_interactivity = None
//!   * wl_output EXPLIZIT gebunden, niemals NULL
//!   * opaque_region leer
//!   * ausschliesslich wl_shm — kein EGL, kein Vulkan, kein GBM
//!
//! Modi:
//!   map     — mappen, Marker zeichnen, leben bleiben (Test 1, 2, 4, 5, 6)
//!   cycles  — 20 Hide/Show-Zyklen in drei Varianten (Test 3, KDE-Bug 503121)

use std::io::Write as _;
use std::num::NonZeroU32;
use std::time::{Duration, Instant};

use smithay_client_toolkit::{
    compositor::{CompositorHandler, CompositorState, Region},
    delegate_registry,
    output::{OutputHandler, OutputState},
    registry::{ProvidesRegistryState, RegistryState},
    registry_handlers,
    seat::{
        pointer::{PointerEvent, PointerEventKind, PointerHandler},
        Capability, SeatHandler, SeatState,
    },
    shell::{
        wlr_layer::{
            Anchor, KeyboardInteractivity, Layer, LayerShell, LayerShellHandler, LayerSurface,
            LayerSurfaceConfigure,
        },
        WaylandSurface,
    },
    shm::{slot::SlotPool, Shm, ShmHandler},
};
use wayland_client::{
    globals::registry_queue_init,
    protocol::{wl_output, wl_pointer, wl_seat, wl_shm, wl_surface},
    Connection, EventQueue, QueueHandle,
};

// ---------------------------------------------------------------- Konfiguration

#[derive(Clone, Debug)]
struct Cfg {
    mode: String,
    /// 0xRRGGBB — extern gewuerfelt, damit der Beweis nicht vom Client kommt.
    color: u32,
    marker: (i32, i32, i32, i32),
    input_region: Option<(i32, i32, i32, i32)>,
    cycles: u32,
    cycle_mode: String,
    hold: u64,
}

fn parse_rect(s: &str) -> (i32, i32, i32, i32) {
    let v: Vec<i32> = s.split(',').map(|p| p.trim().parse().expect("rect")).collect();
    assert_eq!(v.len(), 4, "rect braucht x,y,w,h");
    (v[0], v[1], v[2], v[3])
}

fn parse_args() -> Cfg {
    let mut cfg = Cfg {
        mode: "map".into(),
        color: 0xFF00FF,
        marker: (200, 200, 300, 300),
        input_region: None,
        cycles: 20,
        cycle_mode: "null".into(),
        hold: 0,
    };
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "map" | "cycles" => cfg.mode = args[i].clone(),
            "--color" => {
                i += 1;
                cfg.color = u32::from_str_radix(args[i].trim_start_matches("0x"), 16).unwrap();
            }
            "--marker" => {
                i += 1;
                cfg.marker = parse_rect(&args[i]);
            }
            "--input-region" => {
                i += 1;
                cfg.input_region = Some(parse_rect(&args[i]));
            }
            "--cycles" => {
                i += 1;
                cfg.cycles = args[i].parse().unwrap();
            }
            "--cycle-mode" => {
                i += 1;
                cfg.cycle_mode = args[i].clone();
            }
            "--hold" => {
                i += 1;
                cfg.hold = args[i].parse().unwrap();
            }
            other => panic!("unbekanntes Argument {other}"),
        }
        i += 1;
    }
    cfg
}

// ---------------------------------------------------------------- App-Zustand

struct App {
    registry_state: RegistryState,
    output_state: OutputState,
    seat_state: SeatState,
    shm: Shm,
    compositor: CompositorState,
    pool: SlotPool,

    layer: Option<LayerSurface>,
    width: u32,
    height: u32,
    cfg: Cfg,

    configure_count: u32,
    exit: bool,
    drawn_once: bool,
    dirty: bool,
    /// Frame-Callback wurde armiert. Bei !dirty NICHT neu armieren (§8.1).
    frame_armed: bool,
    closed_count: u32,

    pointer: Option<wl_pointer::WlPointer>,
    pointer_enters: u32,
    pointer_presses: u32,
    pointer_motions: u32,
}

impl App {
    fn log(&self, s: &str) {
        println!("{s}");
        let _ = std::io::stdout().flush();
    }

    /// Zeichnet: alles transparent, Marker-Rechteck in Volldeckung.
    /// ARGB8888 premultiplied — bei alpha=0xFF ist premultiplied == straight.
    fn draw(&mut self, _qh: &QueueHandle<Self>) {
        let (w, h) = (self.width, self.height);
        let stride = w as i32 * 4;
        let (buffer, canvas) = self
            .pool
            .create_buffer(w as i32, h as i32, stride, wl_shm::Format::Argb8888)
            .expect("shm buffer");

        let (mx, my, mw, mh) = self.cfg.marker;
        let r = ((self.cfg.color >> 16) & 0xFF) as u8;
        let g = ((self.cfg.color >> 8) & 0xFF) as u8;
        let b = (self.cfg.color & 0xFF) as u8;

        canvas.fill(0); // vollstaendig transparent
        for y in my.max(0)..(my + mh).min(h as i32) {
            let row = y as usize * stride as usize;
            for x in mx.max(0)..(mx + mw).min(w as i32) {
                let o = row + x as usize * 4;
                // wl_shm ARGB8888 ist little-endian 0xAARRGGBB -> Bytes B,G,R,A
                canvas[o] = b;
                canvas[o + 1] = g;
                canvas[o + 2] = r;
                canvas[o + 3] = 0xFF;
            }
        }

        let layer = self.layer.as_ref().unwrap();
        layer.wl_surface().damage_buffer(0, 0, w as i32, h as i32);
        buffer.attach_to(layer.wl_surface()).expect("attach");
        layer.commit();
        self.drawn_once = true;
        self.dirty = false;
        // Bewusst KEIN frame()-Request: one-shot Callback, bei !dirty nicht
        // neu armieren. Das ist die Bedingung fuer Idle-CPU ~0.
        self.frame_armed = false;
    }

    fn apply_props(&self) {
        let layer = self.layer.as_ref().unwrap();
        layer.set_size(0, 0); // 0 = vom Compositor bestimmt (bildschirmfuellend)
        layer.set_anchor(Anchor::TOP | Anchor::BOTTOM | Anchor::LEFT | Anchor::RIGHT);
        layer.set_layer(Layer::Overlay);
        layer.set_margin(0, 0, 0, 0);
        layer.set_exclusive_zone(-1);
        layer.set_keyboard_interactivity(KeyboardInteractivity::None);
    }

    fn apply_regions(&self, qh: &QueueHandle<Self>) {
        let layer = self.layer.as_ref().unwrap();
        // Leere opaque region — Pflicht, sonst kein Durchscheinen.
        let opaque = Region::new(&self.compositor).expect("region");
        layer.set_opaque_region(Some(opaque.wl_region()));
        // SICHERHEITSREGEL: die Input-Region wird IMMER gesetzt, auch wenn keine
        // angefordert wurde. Eine Wayland-Surface ohne Input-Region nimmt Eingaben
        // auf ihrer GANZEN Flaeche entgegen -- bei einer bildschirmfuellenden
        // Layer-Surface heisst das: der komplette Schirm schluckt Klicks und der
        // Rechner ist mit der Maus nicht mehr bedienbar. Genau das ist am
        // 2026-07-27 passiert.
        //
        // Vorgabe ist deshalb die LEERE Region = vollstaendig klickdurchlaessig.
        let input = Region::new(&self.compositor).expect("region");
        if let Some((x, y, w, h)) = self.cfg.input_region {
            input.add(x, y, w, h);
        }
        layer.set_input_region(Some(input.wl_region()));
        println!("INFO input_region={:?}", self.cfg.input_region);
        let _ = std::io::stdout().flush();
        std::mem::forget(input);
        std::mem::forget(opaque);
        let _ = qh;
    }

}

// ---------------------------------------------------------------- Handler

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
    fn frame(&mut self, _: &Connection, qh: &QueueHandle<Self>, _: &wl_surface::WlSurface, _: u32) {
        self.frame_armed = false;
        if self.dirty {
            self.draw(qh);
        }
        // sonst: NICHT neu armieren.
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
    fn output_destroyed(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}
}

impl LayerShellHandler for App {
    fn closed(&mut self, _: &Connection, _: &QueueHandle<Self>, _: &LayerSurface) {
        self.closed_count += 1;
        self.log("EVENT closed");
        self.exit = true;
    }
    fn configure(
        &mut self,
        _: &Connection,
        qh: &QueueHandle<Self>,
        _: &LayerSurface,
        configure: LayerSurfaceConfigure,
        serial: u32,
    ) {
        self.configure_count += 1;
        self.width = NonZeroU32::new(configure.new_size.0).map_or(self.width, NonZeroU32::get);
        self.height = NonZeroU32::new(configure.new_size.1).map_or(self.height, NonZeroU32::get);
        self.log(&format!(
            "EVENT configure n={} size={}x{} serial={}",
            self.configure_count, self.width, self.height, serial
        ));
        if self.cfg.mode == "map" && !self.drawn_once {
            self.draw(qh);
        }
    }
}

impl SeatHandler for App {
    fn seat_state(&mut self) -> &mut SeatState {
        &mut self.seat_state
    }
    fn new_seat(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_seat::WlSeat) {}
    fn new_capability(
        &mut self,
        _: &Connection,
        qh: &QueueHandle<Self>,
        seat: wl_seat::WlSeat,
        cap: Capability,
    ) {
        if cap == Capability::Pointer && self.pointer.is_none() {
            let r = self.seat_state.get_pointer(qh, &seat);
            println!("INFO got_pointer ok={}", r.is_ok());
            let _ = std::io::stdout().flush();
            self.pointer = r.ok();
        }
    }
    fn remove_capability(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: wl_seat::WlSeat,
        cap: Capability,
    ) {
        if cap == Capability::Pointer {
            if let Some(p) = self.pointer.take() {
                p.release();
            }
        }
    }
    fn remove_seat(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_seat::WlSeat) {}
}

impl PointerHandler for App {
    fn pointer_frame(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: &wl_pointer::WlPointer,
        events: &[PointerEvent],
    ) {
        let mine = self.layer.as_ref().map(|l| l.wl_surface().clone());
        for e in events {
            // Diagnose: JEDES Ereignis roh mitschreiben, bevor gefiltert wird.
            // Der Filter hat im ersten Anlauf womoeglich alles verschluckt.
            let is_mine = Some(&e.surface) == mine.as_ref();
            self.log(&format!(
                "RAW pointer kind={:?} pos={:?} mine={}",
                std::mem::discriminant(&e.kind), e.position, is_mine
            ));
            if !is_mine {
                continue;
            }
            match e.kind {
                PointerEventKind::Enter { .. } => {
                    self.pointer_enters += 1;
                    self.log(&format!("EVENT pointer_enter @{:?}", e.position));
                }
                PointerEventKind::Leave { .. } => self.log("EVENT pointer_leave"),
                PointerEventKind::Motion { .. } => {
                    self.pointer_motions += 1;
                    if self.pointer_motions <= 40 {
                        self.log(&format!("EVENT pointer_motion @{:?}", e.position));
                    }
                }
                PointerEventKind::Press { button, .. } => {
                    self.pointer_presses += 1;
                    self.log(&format!("EVENT pointer_press btn={button} @{:?}", e.position));
                }
                _ => {}
            }
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
    registry_handlers![OutputState, SeatState];
}

delegate_registry!(App);
smithay_client_toolkit::delegate_dispatch2!(App);

// ---------------------------------------------------------------- Event-Pumpe

/// Nicht-blockierendes Pumpen mit Deadline. Nur fuer den Zyklustest —
/// der Idle-Test benutzt blocking_dispatch, sonst waere die Messung wertlos.
fn pump(eq: &mut EventQueue<App>, app: &mut App, dur: Duration) -> Result<(), String> {
    let deadline = Instant::now() + dur;
    loop {
        eq.flush().map_err(|e| e.to_string())?;
        eq.dispatch_pending(app).map_err(|e| e.to_string())?;
        if let Some(g) = eq.prepare_read() {
            match g.read() {
                Ok(_) => {
                    eq.dispatch_pending(app).map_err(|e| e.to_string())?;
                }
                Err(wayland_client::backend::WaylandError::Io(e))
                    if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(e) => return Err(e.to_string()),
            }
        }
        if Instant::now() >= deadline {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(2));
    }
}

/// Pumpt bis configure_count steigt oder die Frist ablaeuft.
fn wait_configure(eq: &mut EventQueue<App>, app: &mut App, dur: Duration) -> (bool, u128) {
    let before = app.configure_count;
    let t0 = Instant::now();
    let deadline = t0 + dur;
    loop {
        if pump(eq, app, Duration::from_millis(10)).is_err() {
            return (false, t0.elapsed().as_millis());
        }
        if app.configure_count > before {
            return (true, t0.elapsed().as_millis());
        }
        if Instant::now() >= deadline {
            return (false, t0.elapsed().as_millis());
        }
    }
}

// ---------------------------------------------------------------- main

fn main() {
    let cfg = parse_args();

    // Zwangsabschaltung. Der Aufrufer kann abstuerzen, der Agent kann haengen,
    // die Ereignisschleife kann blockieren -- dieser Thread beendet den Prozess
    // trotzdem. Ohne Maus ist ein haengendes Overlay nur noch per TTY loesbar.
    let hard_limit = std::env::var("SPIKE_MAX_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(90);
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(hard_limit));
        eprintln!("WATCHDOG: {hard_limit}s erreicht, Prozess wird beendet");
        std::process::exit(3);
    });

    let conn = Connection::connect_to_env().expect("Wayland-Verbindung");
    let (globals, mut eq) = registry_queue_init::<App>(&conn).expect("registry");
    let qh = eq.handle();

    let compositor = CompositorState::bind(&globals, &qh).expect("wl_compositor");
    let layer_shell = LayerShell::bind(&globals, &qh).expect("zwlr_layer_shell_v1");
    let shm = Shm::bind(&globals, &qh).expect("wl_shm");
    let pool = SlotPool::new(5120 * 1440 * 4, &shm).expect("SlotPool");

    let mut app = App {
        registry_state: RegistryState::new(&globals),
        output_state: OutputState::new(&globals, &qh),
        seat_state: SeatState::new(&globals, &qh),
        shm,
        compositor,
        pool,
        layer: None,
        width: 1,
        height: 1,
        cfg: cfg.clone(),
        configure_count: 0,
        exit: false,
        drawn_once: false,
        dirty: true,
        frame_armed: false,
        closed_count: 0,
        pointer: None,
        pointer_enters: 0,
        pointer_presses: 0,
        pointer_motions: 0,
    };

    // Outputs einsammeln, damit wl_output explizit gebunden werden kann.
    eq.roundtrip(&mut app).expect("roundtrip");
    eq.roundtrip(&mut app).expect("roundtrip");
    let output = app.output_state.outputs().next().expect("kein wl_output gefunden");
    let oinfo = app.output_state.info(&output).expect("output info");
    println!(
        "INFO output name={:?} logical={:?} modes={:?} scale={}",
        oinfo.name, oinfo.logical_size, oinfo.modes.len(), oinfo.scale_factor
    );
    println!("INFO shm_formats={:?}", app.shm.formats());
    println!(
        "INFO globals={:?}",
        globals
            .contents()
            .clone_list()
            .iter()
            .map(|g| format!("{}@v{}", g.interface, g.version))
            .collect::<Vec<_>>()
    );

    let mk_layer = |app: &mut App, qh: &QueueHandle<App>| {
        let surface = app.compositor.create_surface(qh);
        let layer = layer_shell.create_layer_surface(
            qh,
            surface,
            Layer::Overlay,
            Some("daimon-spike"),
            Some(&output), // EXPLIZIT, niemals NULL
        );
        app.layer = Some(layer);
        app.apply_props();
        app.apply_regions(qh);
        app.layer.as_ref().unwrap().commit();
    };

    mk_layer(&mut app, &qh);

    match cfg.mode.as_str() {
        "map" => {
            println!("PID {}", std::process::id());
            let (got, ms) = wait_configure(&mut eq, &mut app, Duration::from_secs(5));
            println!("RESULT initial_configure={got} after_ms={ms}");
            if !got {
                println!("RESULT maps=false");
                return;
            }
            // Erster Draw ist im configure-Handler passiert.
            pump(&mut eq, &mut app, Duration::from_millis(300)).ok();
            println!("RESULT maps=true size={}x{}", app.width, app.height);
            println!("READY");
            let _ = std::io::stdout().flush();
            // Echtes Blockieren in poll() — Voraussetzung fuer die Idle-Messung.
            while !app.exit {
                if eq.blocking_dispatch(&mut app).is_err() {
                    break;
                }
            }
            println!("EXIT pointer_enters={} presses={} motions={}", app.pointer_enters, app.pointer_presses, app.pointer_motions);
        }
        "cycles" => {
            let (got, _) = wait_configure(&mut eq, &mut app, Duration::from_secs(5));
            if !got {
                println!("RESULT error=no_initial_configure");
                return;
            }
            app.draw(&qh);
            pump(&mut eq, &mut app, Duration::from_millis(200)).ok();
            println!("INFO initial map ok, size={}x{}", app.width, app.height);

            let mut ok = 0u32;
            let mut times = Vec::new();
            let mut err: Option<String> = None;

            for i in 0..cfg.cycles {
                match cfg.cycle_mode.as_str() {
                    "null" | "reset" => {
                        // (a) Unmap ueber NULL-Buffer
                        let l = app.layer.as_ref().unwrap();
                        l.wl_surface().attach(None, 0, 0);
                        l.commit();
                        if let Err(e) = pump(&mut eq, &mut app, Duration::from_millis(80)) {
                            err = Some(e);
                            break;
                        }
                        if cfg.cycle_mode == "reset" {
                            // (b) alle Properties vor dem Remap neu setzen
                            app.apply_props();
                            app.apply_regions(&qh);
                        }
                        // Remap: initialer Commit ohne Buffer -> erwartet configure
                        app.layer.as_ref().unwrap().commit();
                    }
                    "recreate" => {
                        // (c) Layer-Surface zerstoeren und neu erzeugen
                        app.layer = None;
                        if let Err(e) = pump(&mut eq, &mut app, Duration::from_millis(50)) {
                            err = Some(e);
                            break;
                        }
                        app.drawn_once = true; // Draw steuern wir hier selbst
                        mk_layer(&mut app, &qh);
                    }
                    m => panic!("unbekannter cycle-mode {m}"),
                }

                let (got, ms) = wait_configure(&mut eq, &mut app, Duration::from_millis(1500));
                times.push(ms);
                if got {
                    ok += 1;
                    app.draw(&qh);
                } else {
                    // Kein configure. Jetzt einen Buffer anzuhaengen ist ein
                    // Protokollfehler ("buffer attached prior to the first
                    // layer_surface.configure") und wuerde die Verbindung
                    // toeten. Also nur zaehlen und weitermessen.
                    println!("EVENT no_configure cycle={i}");
                }
                if let Err(e) = pump(&mut eq, &mut app, Duration::from_millis(120)) {
                    err = Some(format!("cycle {i}: {e}"));
                    break;
                }
                if app.closed_count > 0 {
                    err = Some(format!("cycle {i}: surface closed by compositor"));
                    break;
                }
            }

            println!(
                "RESULT cycle_mode={} cycles={} configures={} closed={} error={:?}",
                cfg.cycle_mode, cfg.cycles, ok, app.closed_count, err
            );
            println!("RESULT times_ms={times:?}");
            if cfg.hold > 0 {
                println!("HOLD {}s pid={}", cfg.hold, std::process::id());
                let _ = std::io::stdout().flush();
                pump(&mut eq, &mut app, Duration::from_secs(cfg.hold)).ok();
            }
        }
        m => panic!("unbekannter Modus {m}"),
    }
}
