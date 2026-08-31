//! dAImon-Face: minimale, transparente Layer-Surface nur mit `wl_shm`.

mod bubble;
mod control;
mod diag;
mod hub;
mod input;
mod menu;
mod output;
mod position;
mod render;
mod sound;
mod sprite;
mod surface;

use std::{
    ffi::OsString,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use bubble::{Bubble, BubbleRenderer};
use calloop::{
    channel::{channel, Event as ChannelEvent},
    EventLoop,
};
use control::ControlSocket;
use diag::{DiagSocket, FaceState};
use hub::{HubVerbindung, HubZustand};
use position::{Loslassen, Ziehen};
use render::RenderSteuerung;
use smithay_client_toolkit::reexports::calloop_wayland_source::WaylandSource;
use smithay_client_toolkit::{
    compositor::{CompositorHandler, CompositorState},
    delegate_registry,
    output::{OutputHandler, OutputState},
    registry::{ProvidesRegistryState, RegistryState},
    registry_handlers,
    seat::{
        pointer::{PointerEvent, PointerEventKind, PointerHandler},
        Capability, SeatHandler, SeatState,
    },
    shell::{
        wlr_layer::{LayerShell, LayerShellHandler, LayerSurface, LayerSurfaceConfigure},
        xdg::popup::{Popup, PopupConfigure, PopupHandler},
    },
    shm::{slot::SlotPool, Shm, ShmHandler},
    subcompositor::SubcompositorState,
};
use sound::Tonspieler;
use sprite::{zustand_abbilden, SpriteAtlas};
use surface::OverlaySurface;
use wayland_client::{
    globals::registry_queue_init,
    protocol::{wl_output, wl_pointer, wl_seat, wl_surface},
    Connection, QueueHandle,
};

#[derive(Debug, Default)]
struct Optionen {
    diag_socket: Option<PathBuf>,
    control_socket: Option<PathBuf>,
    hub_socket: Option<PathBuf>,
    ton: Option<bool>,
    sprite_position: Option<(i32, i32)>,
    pet_manifest: Option<PathBuf>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Sichtbarkeit(bool);

impl Sichtbarkeit {
    fn fuer_mood(mood: &str) -> bool {
        mood != "sleeping"
    }

    /// `true` bedeutet, dass genau ein echter Sichtbarkeits-Commit faellig ist.
    fn setzen(&mut self, sichtbar: bool) -> bool {
        if self.0 == sichtbar {
            return false;
        }
        self.0 = sichtbar;
        true
    }
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
            "--ton" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--ton braucht ein|aus".to_string())?;
                ergebnis.ton = Some(match wert.as_str() {
                    "ein" => true,
                    "aus" => false,
                    _ => return Err(format!("--ton braucht ein|aus, war: {wert}")),
                });
            }
            "--hub-socket" => {
                let wert = args
                    .next()
                    .ok_or_else(|| "--hub-socket braucht einen Pfad".to_string())?;
                ergebnis.hub_socket = Some(PathBuf::from(wert));
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
                     [--control-socket PFAD] [--hub-socket PFAD] [--pet-manifest PFAD] \
                     [--ton ein|aus] \
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
    // Ein Doppel-Self-Pet gilt vor dem Ember-Sheet -- WENN es da ist.
    //
    // Vorrang mit Rueckfall und kein fester Pfad: die acht Gesichter stehen
    // absichtlich nicht im Repo (`.gitignore`; Bilder eines echten Gesichts
    // gehoeren nicht in ein oeffentliches). Ein frischer Checkout hat sie
    // also nicht. Zeigte die Vorgabe fest dorthin, waere das dort kein Pet,
    // sondern Exit 2 aus `SpriteAtlas::laden` -- und unter systemd ein
    // Neustartkarussell. So bleibt es dort schlicht beim Ember.
    //
    // `tools/doppelself_gesichter.py` legt das Verzeichnis an; wer es
    // loescht, ist beim naechsten Start wieder beim Ember, ohne Zutun.
    let doppelself = arbeitsverzeichnis.join("assets/doppelself/pet.json");
    if doppelself.is_file() {
        return (doppelself, ManifestQuelle::Arbeitsverzeichnis);
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
    seat_state: SeatState,
    pointer: Option<wl_pointer::WlPointer>,
    /// T-2.7: fuer `xdg_popup.grab`. Ohne Seat gibt es kein Kontextmenue --
    /// und ohne Zeiger ohnehin keinen Rechtsklick.
    seat: Option<wl_seat::WlSeat>,
    shm: Shm,
    compositor: CompositorState,
    // T-2.5: fuer die Neuerzeugung der Layer-Surface nach Output-Removal.
    // Vorher lagen beide nur als lokale Variablen in `main`.
    subcompositor: SubcompositorState,
    layer_shell: LayerShell,
    /// T-2.7: nur fuer das Kontextmenue-Popup. Es entsteht kein Toplevel und
    /// keine zweite Rolle fuer die Layer-Surface.
    xdg_shell: menu::PopupShell,
    qh: QueueHandle<App>,
    pool: SlotPool,
    atlas: SpriteAtlas,
    bubble_renderer: BubbleRenderer,
    overlay: Option<OverlaySurface>,
    diagnose: Arc<Mutex<FaceState>>,
    aktueller_zustand: String,
    /// T-3.14: der zuletzt vom Hub GERECHNETE Sprachzustand. Das Face leitet
    /// ihn nicht ab, es traegt ihn nur bis zum Puffer.
    aktueller_voice: String,
    /// T-7.3: laeuft gerade ein Mitschnitt? Nur zum Zeichnen -- abgeleitet
    /// wird hier nichts, der Wert kommt aus dem Hub-Schnappschuss.
    aktueller_mitschnitt: bool,
    aktueller_mood: String,
    sichtbarkeit: Sichtbarkeit,
    aktuelle_bubble: Option<Bubble>,
    /// Der Zipfel, der zuletzt in den Blasenpuffer gemalt wurde. Beim Ziehen
    /// des Pet wird die Blase nur VERSCHOBEN, nicht neu gerendert -- ohne
    /// diesen Vergleich zeigte der Zipfel nach dem Klemmen am Bildschirmrand
    /// in die falsche Richtung weiter.
    letzter_zipfel: Option<bubble::Zipfel>,
    hub: Option<HubVerbindung>,
    render: RenderSteuerung,
    ton: Tonspieler,
    ziehen: Option<Ziehen>,
    menu: menu::Menu,
    sprite_groesse: (i32, i32),
    output_groesse: (i32, i32),
    /// T-2.5: der Output, an den die Layer-Surface explizit gebunden ist.
    gebundener_output: Option<wl_output::WlOutput>,
    /// T-2.5: `DAIMON_FACE_OUTPUT`, auch nach einem Wechsel noch der Wunsch.
    output_wunsch: Option<String>,
    positionsdatei: PathBuf,
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

    fn diagnose_position_setzen(&self, position: (i32, i32)) {
        let mut zustand = match self.diagnose.lock() {
            Ok(zustand) => zustand,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.sprite_x = position.0;
        zustand.sprite_y = position.1;
    }

    /// T-3.14: der Sprachzustand, den das Face gerade DARSTELLT.
    fn voice_state(&self) -> String {
        self.aktueller_voice.clone()
    }

    /// Nur echte Pixel zaehlen. Ohne diesen Zaehler waere "das Face zeigt den
    /// Sprachzustand an" eine Selbstauskunft.
    fn indikator_zaehlen(&self, gemalt: bool) {
        if !gemalt {
            return;
        }
        let mut zustand = match self.diagnose.lock() {
            Ok(z) => z,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.voice_indikator_gezeichnet += 1;
    }

    /// T-7.3: dasselbe fuer den Mitschnitt-Punkt. Eine eigene Funktion und
    /// ein eigenes Feld, weil er etwas anderes sagt: der erste heisst "ich
    /// hoere gerade zu", der zweite "das hier wird aufbewahrt".
    ///
    /// Bis zum 19.08. gab es sie nicht -- `surface.rs` warf das Ergebnis von
    /// `mitschnitt_malen` weg, und der Kommentar daneben behauptete das
    /// Gegenteil (BEFUND T-7.3 K9).
    fn mitschnitt_indikator_zaehlen(&self, gemalt: bool) {
        if !gemalt {
            return;
        }
        let mut zustand = match self.diagnose.lock() {
            Ok(z) => z,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.mitschnitt_indikator_gezeichnet += 1;
    }

    fn diagnose_voice_setzen(&self, voice: &str) {
        let mut zustand = match self.diagnose.lock() {
            Ok(z) => z,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.voice_state = voice.to_owned();
    }

    fn diagnose_hub_setzen(&self, rev: u64, mood: &str) {
        let mut zustand = match self.diagnose.lock() {
            Ok(z) => z,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.rev = rev;
        zustand.mood = mood.to_owned();
    }

    fn diagnose_bubble_setzen(&self, sichtbar: bool, frame: bool) {
        let mut zustand = match self.diagnose.lock() {
            Ok(z) => z,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.bubble_visible = sichtbar;
        if frame {
            zustand.bubble_frame_gezaehlt();
        }
    }

    /// `wechsel` unterscheidet die Erstbindung (false) von der Neuerzeugung
    /// nach Output-Removal (true). Nur letztere zaehlt `output_wechsel` hoch.
    fn diagnose_output_setzen(&self, name: &str, wechsel: bool) {
        let mut zustand = match self.diagnose.lock() {
            Ok(z) => z,
            Err(vergiftet) => vergiftet.into_inner(),
        };
        zustand.output = name.to_owned();
        if wechsel {
            zustand.output_wechsel += 1;
        }
    }

    /// T-2.5: Erzeugt die Layer-Surface an einem anderen Output neu. Der
    /// verlorene Output wird ausgeschlossen -- je nach Ereignisreihenfolge
    /// steht er noch in der Registry, wenn `closed` zuerst eintrifft.
    ///
    /// `false` heisst: es ist kein Output mehr da. Dann ist Beenden richtig --
    /// ein Overlay ohne Ausgabe hat nichts, worauf es zeichnen koennte.
    fn output_neu_binden(&mut self) -> bool {
        let verloren = self.gebundener_output.take();
        let alte_position = self
            .overlay
            .as_ref()
            .map(OverlaySurface::sprite_position)
            .unwrap_or((0, 0));
        // Die alte Surface gehoert zu einem Output, den es nicht mehr gibt.
        self.overlay = None;
        // Ein Frame-Callback der alten Surface kommt nie mehr. Bliebe
        // frame_pending stehen, waere `callback_armieren` fuer immer false
        // und der Farbluebergang eingefroren -- still und ohne Fehlermeldung.
        self.render.frame_empfangen();

        let Some((output, name)) = output::waehlen(
            &self.output_state,
            self.output_wunsch.as_deref(),
            verloren.as_ref(),
        ) else {
            eprintln!("Kein wl_output mehr verfuegbar; Face beendet sich");
            self.beendet = true;
            return false;
        };

        let output_groesse = self
            .output_state
            .info(&output)
            .and_then(|info| info.logical_size)
            .unwrap_or(self.output_groesse);
        self.output_groesse = output_groesse;
        let position =
            position::position_klemmen(alte_position, self.sprite_groesse, output_groesse);

        let overlay = OverlaySurface::neu(
            &self.compositor,
            &self.subcompositor,
            &self.layer_shell,
            &self.qh,
            &output,
            position,
            self.sprite_groesse,
            output_groesse,
        );
        if !overlay.initial_commit() {
            eprintln!("Neue Layer-Surface konnte nicht committet werden");
            self.beendet = true;
            return false;
        }
        self.commit_zaehlen(false);
        self.gebundener_output = Some(output);
        self.overlay = Some(overlay);
        self.diagnose_position_setzen(position);
        // Der Zaehler steigt hier, nicht erst beim configure: die Bindung an
        // den neuen Output ist zu diesem Zeitpunkt vollzogen, und ob ein
        // configure folgt, entscheidet der Compositor.
        self.diagnose_output_setzen(&name, true);
        eprintln!("Layer-Surface an Output {name} neu erzeugt");
        true
    }

    fn diagnose_sichtbarkeit_setzen(&self, sichtbar: bool) {
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.sichtbar = sichtbar,
            Err(vergiftet) => vergiftet.into_inner().sichtbar = sichtbar,
        }
    }

    /// Uebernimmt einen Hub-Snapshot. `rev` und `mood` sind die Wahrheit des
    /// Hubs und werden immer gespiegelt. Pose und Mood-Farbe sind getrennt:
    /// Auch `thinking -> working` bei derselben Pose startet den endlichen
    /// Farbluebergang.
    fn hub_zustand_uebernehmen(&mut self, zustand: &hub::HubZustand) {
        // Der Ton haengt am MOOD und am UEBERGANG, nicht am Sprite:
        // `needs_input` und `failed` sind beide `dringend`, und ein
        // spritebasierter Ausloeser piepte bei jedem Fehlschlag.
        if sound::loest_ton_aus(&self.aktueller_mood, &zustand.mood) && self.ton.spielen() {
            match self.diagnose.lock() {
                Ok(mut d) => d.toene_gespielt += 1,
                Err(vergiftet) => vergiftet.into_inner().toene_gespielt += 1,
            }
        }
        self.render.ziel_setzen(&zustand.mood, Instant::now());
        self.aktueller_mood = zustand.mood.clone();
        self.diagnose_hub_setzen(zustand.rev, &zustand.mood);
        let neuer_zustand = hub::mood_zu_sprite(&zustand.mood);
        let pose_geaendert = self.aktueller_zustand != neuer_zustand;
        self.aktueller_zustand = neuer_zustand.to_owned();
        let bubble_geaendert = self.aktuelle_bubble != zustand.bubble;
        if bubble_geaendert {
            self.aktuelle_bubble = zustand.bubble.clone();
        }
        // T-3.14: der Sprachzustand aendert die Pose NICHT -- er wird
        // darueber gemalt. Deshalb ein eigenes Kennzeichen: sonst bliebe ein
        // Wechsel von `listening` nach `speaking` ungezeichnet, weil der Mood
        // sich nicht bewegt hat.
        let voice_geaendert = self.aktueller_voice != zustand.voice;
        if voice_geaendert {
            self.aktueller_voice = zustand.voice.clone();
            self.diagnose_voice_setzen(&zustand.voice);
        }
        let mitschnitt_geaendert = self.aktueller_mitschnitt != zustand.mitschnitt;
        if mitschnitt_geaendert {
            self.aktueller_mitschnitt = zustand.mitschnitt;
        }

        let sichtbarkeit_geaendert =
            self.sichtbarkeit_setzen(Sichtbarkeit::fuer_mood(&zustand.mood));
        if self.sichtbarkeit.0 && !sichtbarkeit_geaendert {
            if pose_geaendert || voice_geaendert || mitschnitt_geaendert || self.render.dirty {
                self.sprite_rendern();
            }
            if bubble_geaendert {
                self.bubble_aktualisieren();
            }
        }
    }

    /// Ein Steuerbefehl und die Mood-Regel landen beide hier. Jeder echte
    /// Wechsel zeichnet einen Puffer; ein doppeltes `aus` bleibt ein No-op.
    fn sichtbarkeit_setzen(&mut self, sichtbar: bool) -> bool {
        if !self.sichtbarkeit.setzen(sichtbar) {
            return false;
        }
        if !self
            .overlay
            .as_ref()
            .is_some_and(OverlaySurface::hat_puffer)
        {
            self.diagnose_sichtbarkeit_setzen(sichtbar);
            return true;
        }

        self.sprite_rendern();
        if sichtbar {
            self.bubble_aktualisieren();
        } else {
            let bubble_ausgeblendet = {
                let Self {
                    overlay,
                    compositor,
                    pool,
                    ..
                } = self;
                overlay
                    .as_mut()
                    .is_some_and(|o| o.bubble_ausblenden(compositor, pool))
            };
            if bubble_ausgeblendet {
                self.diagnose_bubble_setzen(false, true);
            }
        }
        self.diagnose_sichtbarkeit_setzen(sichtbar);
        true
    }

    /// Fuer den Steuer-Socket: zaehlt die rev weiter, damit ein per `mood`
    /// gesetzter Zustand von aussen genauso aussieht wie einer vom Hub.
    fn naechste_rev(&self) -> u64 {
        match self.diagnose.lock() {
            Ok(d) => d.rev + 1,
            Err(vergiftet) => vergiftet.into_inner().rev + 1,
        }
    }

    fn zustand_setzen(&mut self, name: &str) {
        let pose_geaendert = self.aktueller_zustand != name;
        if !pose_geaendert && !self.render.dirty {
            return;
        }
        if pose_geaendert {
            self.aktueller_zustand = name.to_owned();
        }
        if self.overlay.is_none()
            || !self
                .overlay
                .as_ref()
                .is_some_and(OverlaySurface::hat_puffer)
            || !self.sichtbarkeit.0
        {
            return;
        }
        self.sprite_rendern();
    }

    fn sprite_rendern(&mut self) {
        let (toenung, _) = self.render.wert(Instant::now());
        let callback_armieren = self.sichtbarkeit.0 && self.render.callback_armieren();
        let sichtbar = self.sichtbarkeit.0;
        let qh = self.qh.clone();
        let name = self.aktueller_zustand.clone();
        let mood = self.aktueller_mood.clone();
        let voice = self.aktueller_voice.clone();
        let mitschnitt = self.aktueller_mitschnitt;
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
                .expect("Overlay existiert beim Rendern")
                .sprite_committen(
                    compositor,
                    pool,
                    atlas,
                    &name,
                    &mood,
                    &voice,
                    mitschnitt,
                    toenung,
                    sichtbar,
                    &qh,
                    callback_armieren,
                )
        };
        match ergebnis {
            Ok((commits, indikator, mitschnitt_indikator)) => {
                self.commits_zaehlen(commits);
                self.diagnose_sprite_setzen(&name);
                self.indikator_zaehlen(indikator);
                self.mitschnitt_indikator_zaehlen(mitschnitt_indikator);
            }
            Err(fehler) => {
                // Ein fehlgeschlagener Request darf frame_pending nicht fuer
                // immer verriegeln. Der naechste echte Zustandswechsel kann
                // dadurch wieder einen Callback anfordern.
                if callback_armieren {
                    self.render.frame_empfangen();
                }
                eprintln!("Sprite-Zustand {name} konnte nicht gerendert werden: {fehler}")
            }
        }
    }

    fn bubble_aktualisieren(&mut self) {
        if !self
            .overlay
            .as_ref()
            .is_some_and(OverlaySurface::hat_puffer)
        {
            return;
        }
        match self.aktuelle_bubble.as_ref() {
            Some(bubble) => {
                let mut raster = self.bubble_renderer.rendern(bubble);
                // Erst der Korpus, dann der Zipfel: seine Richtung haengt an
                // der Klemmung, und die braucht die fertige Blasengroesse.
                let zipfel = self
                    .overlay
                    .as_ref()
                    .expect("Overlay existiert beim Blasenrendern")
                    .zipfel_fuer_blase((raster.breite, raster.hoehe));
                bubble::zipfel_malen(&mut raster, zipfel, bubble.urgent);
                self.letzter_zipfel = Some(zipfel);
                let ergebnis = {
                    let Self {
                        overlay,
                        compositor,
                        pool,
                        ..
                    } = self;
                    overlay
                        .as_mut()
                        .expect("Overlay existiert beim Blasenrendern")
                        .bubble_committen(compositor, pool, &raster)
                };
                match ergebnis {
                    Ok(1) => self.diagnose_bubble_setzen(true, true),
                    Ok(_) => {}
                    Err(fehler) => eprintln!("Blase konnte nicht gerendert werden: {fehler}"),
                }
            }
            None => {
                let ausgeblendet = {
                    let Self {
                        overlay,
                        compositor,
                        pool,
                        ..
                    } = self;
                    overlay
                        .as_mut()
                        .is_some_and(|o| o.bubble_ausblenden(compositor, pool))
                };
                if ausgeblendet {
                    self.diagnose_bubble_setzen(false, true);
                }
            }
        }
    }

    /// Beim Ziehen wird die Blase nur verschoben. Kippt die Klemmung dabei
    /// auf die andere Seite, zeigt der gemalte Zipfel ins Leere -- dann, und
    /// nur dann, wird neu gerendert. Ein Neurendern je Mausbewegung waere ein
    /// Puffer je Pixel Zugweg.
    fn zipfel_nachfuehren(&mut self) {
        let faellig = self.overlay.as_ref().and_then(|overlay| {
            let groesse = overlay.bubble_groesse()?;
            let zipfel = overlay.zipfel_fuer_blase(groesse);
            (Some(zipfel) != self.letzter_zipfel).then_some(())
        });
        if faellig.is_some() {
            self.bubble_aktualisieren();
        }
    }

    fn bubble_schliessen(&mut self) {
        if self.aktuelle_bubble.take().is_none() {
            return;
        }
        self.bubble_aktualisieren();
        if let Some(hub) = &self.hub {
            if let Err(fehler) = hub.bubble_dismiss_melden() {
                eprintln!("{fehler}");
            }
        }
    }

    // -- T-2.7: Kontextmenue ------------------------------------------------

    fn diagnose_menu_offen_setzen(&self, offen: bool) {
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.menu_offen = offen,
            Err(vergiftet) => vergiftet.into_inner().menu_offen = offen,
        }
    }

    /// `anker` liegt in Koordinaten der bildschirmfuellenden Layer-Surface --
    /// dieselbe Bezugsflaeche, auf die sich die Fenstergeometrie des
    /// xdg_positioner bezieht.
    ///
    /// `serial` stammt aus dem Rechtsklick. Es gibt keinen zweiten Aufrufer:
    /// der Steuer-Socket erreicht diese Funktion nicht.
    fn menu_oeffnen(&mut self, anker: (i32, i32), serial: u32) {
        let Some(seat) = self.seat.clone() else {
            eprintln!("Kein Seat; Kontextmenue nicht moeglich");
            return;
        };
        let Self {
            menu,
            compositor,
            xdg_shell,
            overlay,
            qh,
            ..
        } = self;
        let Some(overlay) = overlay.as_ref() else {
            return;
        };
        if let Err(fehler) = menu.oeffnen(
            compositor,
            xdg_shell,
            overlay.layer(),
            qh,
            &seat,
            serial,
            anker,
        ) {
            eprintln!("Kontextmenue konnte nicht geoeffnet werden: {fehler}");
        }
    }

    /// T-9.1 A2/A4: neuen Puffer an die offene Popup-Surface.
    ///
    /// Ohne `configure`, weil es keines gibt: Groesse und Position aendern sich
    /// nicht, nur der Inhalt. Ein gemappter Puffer darf jederzeit durch einen
    /// neuen ersetzt werden.
    fn menu_neu_zeichnen(&mut self) {
        let raster = menu::rendern(self.bubble_renderer.font(), self.menu.schwebe());
        let Self { menu, pool, .. } = self;
        match menu.puffer_committen(pool, &raster) {
            Ok(_) => {}
            Err(fehler) => {
                eprintln!("Kontextmenue konnte nicht neu gezeichnet werden: {fehler}");
                self.menu_schliessen();
            }
        }
    }

    /// T-9.1 A1: der Zeiger ist nicht mehr ueber dem Menue. Zeichnet nur neu,
    /// wenn ueberhaupt eine Zeile hinterlegt war.
    fn menu_schwebe_loeschen(&mut self) {
        if self.menu.schwebe_setzen(None) {
            self.menu_neu_zeichnen();
        }
    }

    fn menu_schliessen(&mut self) {
        if self.menu.schliessen() {
            self.diagnose_menu_offen_setzen(false);
        }
    }

    /// Der einzige Weg von einer Aktion zur Wirkung -- fuer den Klick im
    /// Popup und fuer `menu ...` am Steuer-Socket. Zwei Wege waeren zwei
    /// Verhalten, und geprueft wuerde nur eines.
    fn menu_aktion_ausfuehren(&mut self, aktion: menu::Aktion) {
        // Auf die Aktion selbst verzweigt, NICHT auf `ziel()`: seit es die
        // Personaauswahl gibt, heisst `ziel() == None` nicht mehr "beenden".
        match aktion {
            // Das Face ruft NICHT selbst systemctl. Es meldet ein Ziel; der
            // Hub schlaegt den Unit-Namen in seiner Allowlist nach und
            // schaltet. Ein Overlay, das Units benennen darf, kann den Hub
            // und den Auth-Agenten stoppen.
            menu::Aktion::EarsAus | menu::Aktion::EyesAus => {
                let ziel = aktion.ziel().expect("Abschalt-Aktion ohne Ziel");
                match self.hub.as_ref() {
                    Some(hub) => {
                        if let Err(fehler) = hub.wahrnehmung_aus_melden(ziel) {
                            eprintln!("{fehler}");
                        }
                    }
                    None => eprintln!("Kein Hub-Meldeweg; {ziel} bleibt unveraendert"),
                }
            }
            menu::Aktion::Persona(index) => self.persona_waehlen(index),
            menu::Aktion::BildschirmWiderrufen => self.bildschirm_widerrufen(),
            menu::Aktion::Privatmodus => self.privatmodus(),
            // Geordnet: die Hauptschleife laeuft aus, und erst dadurch
            // raeumen Diagnose- und Steuer-Socket ihre Dateien ab.
            menu::Aktion::Beenden => self.beendet = true,
        }
        let name = aktion.name();
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.menu_aktion_gezaehlt(&name),
            Err(vergiftet) => vergiftet.into_inner().menu_aktion_gezaehlt(&name),
        }
    }

    /// T-7.4: den Mitschnitt pausieren, Bild und Ton.
    ///
    /// Gemeldet und nicht selbst geschrieben -- die Begruendung steht bei
    /// `hub::privatmodus_melden`. Die Blase sagt, wie lange es gilt: ein
    /// Modus, dessen Frist man nicht sieht, ist einer, auf den man sich
    /// faelschlich verlaesst.
    fn privatmodus(&mut self) {
        let (titel, text, gescheitert) = match self.hub.as_ref() {
            Some(hub) => match hub.privatmodus_melden() {
                Ok(()) => (
                    "Mitschnitt pausiert".to_owned(),
                    "15 Minuten lang wird nichts abgelegt -- Bild und Ton"
                        .to_owned(),
                    false,
                ),
                Err(fehler) => {
                    // Der Wortlaut geht ins Journal, nicht in die Blase: dort
                    // stehen Socketpfade und errno-Texte, die die eine Zeile
                    // verdraengen, auf die es ankommt -- dass es NICHT still
                    // ist.
                    eprintln!("Privatmodus nicht gemeldet: {fehler}");
                    (
                        "Privatmodus NICHT aktiv".to_owned(),
                        "Fehlgeschlagen; Grund steht im Journal".to_owned(),
                        true,
                    )
                }
            },
            None => {
                eprintln!("Kein Hub-Meldeweg; Privatmodus bleibt aus");
                (
                    "Privatmodus NICHT aktiv".to_owned(),
                    "Kein Meldeweg zum Hub".to_owned(),
                    true,
                )
            }
        };
        self.aktuelle_bubble = Some(Bubble {
            title: titel,
            body: text,
            // Eine Fehlmeldung MUSS auffallen: der Nutzer glaubt sonst, es
            // sei still, und spricht weiter. Der Widerruf daneben kommt ohne
            // aus -- dort ist die Folge eines Fehlschlags, dass etwas
            // ERLAUBT bleibt, hier, dass etwas AUFGEZEICHNET wird.
            urgent: gescheitert,
        });
        self.bubble_aktualisieren();
    }

    /// T-5.2: den Bildschirmzugriff widerrufen.
    ///
    /// Geschrieben wird eine Marke, mehr nicht -- derselbe Weg wie bei der
    /// Persona und aus demselben Grund: das Face darf hoechstens
    /// `bubble_dismiss` und `wahrnehmung_aus` senden (T-1.7.v4), und ein
    /// dritter Nachrichtentyp haette T-2.7 rot gemacht.
    ///
    /// Die Marke SAGT den Widerruf, sie vollzieht ihn nicht. Loeschen kann
    /// die Tokendatei nur, wer sie besitzt -- sie liegt unter
    /// `$XDG_CONFIG_HOME/daimon/`, und das Face hat dort `ProtectHome`.
    /// Deshalb sagt die Blase auch ausdruecklich, wann es wirkt.
    fn bildschirm_widerrufen(&mut self) {
        let (titel, text) = match menu::widerruf_vermerken() {
            Ok(pfad) => {
                eprintln!("Widerruf vermerkt in {pfad}");
                (
                    "Bildschirmzugriff widerrufen".to_owned(),
                    "Vermerkt; gilt beim naechsten Blick".to_owned(),
                )
            }
            Err(fehler) => {
                eprintln!("Widerruf nicht vermerkt: {fehler}");
                (
                    "Widerruf nicht vermerkt".to_owned(),
                    "Fehlgeschlagen; Grund steht im Journal".to_owned(),
                )
            }
        };
        self.aktuelle_bubble = Some(Bubble {
            title: titel,
            body: text,
            urgent: false,
        });
        self.bubble_aktualisieren();
    }

    /// Die Wahl wird geschrieben und sonst nichts: kein Unit-Neustart, kein
    /// Signal an den Mind. Die Blase sagt deshalb ausdruecklich, wann sie
    /// wirkt -- eine Auswahl, die scheinbar sofort gilt, waere eine Luege
    /// gegenueber dem laufenden Modell, das weiter mit dem alten Prompt redet.
    fn persona_waehlen(&mut self, index: usize) {
        let (titel, text) = match menu::persona_setzen(index) {
            Ok(anzeige) => (
                format!("Persona: {anzeige}"),
                "gilt ab dem naechsten Start des Mind".to_owned(),
            ),
            Err(fehler) => {
                eprintln!("Persona nicht gewechselt: {fehler}");
                (
                    "Persona nicht gewechselt".to_owned(),
                    "Fehlgeschlagen; Grund steht im Journal".to_owned(),
                )
            }
        };
        self.aktuelle_bubble = Some(Bubble {
            title: titel,
            body: text,
            urgent: false,
        });
        self.bubble_aktualisieren();
    }

    fn position_speichern(&self, position: (i32, i32)) {
        if let Err(fehler) = position::speichern(&self.positionsdatei, position) {
            eprintln!(
                "Pet-Position konnte nicht in {} gespeichert werden: {fehler}",
                self.positionsdatei.display()
            );
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

    fn frame(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        surface: &wl_surface::WlSurface,
        _: u32,
    ) {
        if !self
            .overlay
            .as_ref()
            .is_some_and(|overlay| overlay.ist_sprite_surface(surface))
            || !self.render.frame_pending
        {
            return;
        }
        self.render.frame_empfangen();
        if !self.sichtbarkeit.0 {
            return;
        }
        // `sprite_rendern` tastet die feste Dauer ab. Am oder nach dem Ende
        // setzt es exakt den Zielwert, dirty=false und armiert NICHT erneut.
        self.sprite_rendern();
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
        capability: Capability,
    ) {
        if capability == Capability::Pointer && self.pointer.is_none() {
            self.pointer = self.seat_state.get_pointer(qh, &seat).ok();
            // Denselben Seat braucht `xdg_popup.grab`.
            self.seat = Some(seat);
        }
    }

    fn remove_capability(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        _: wl_seat::WlSeat,
        capability: Capability,
    ) {
        if capability == Capability::Pointer {
            if let Some(pointer) = self.pointer.take() {
                pointer.release();
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
        const BTN_LEFT: u32 = 0x110;
        const BTN_RIGHT: u32 = 0x111;
        for event in events {
            let auf_menu = self.menu.ist_menu_surface(&event.surface);
            let auf_bubble = self
                .overlay
                .as_ref()
                .is_some_and(|overlay| overlay.ist_bubble_surface(&event.surface));
            let auf_sprite = self
                .overlay
                .as_ref()
                .is_some_and(|overlay| overlay.ist_sprite_surface(&event.surface));
            let auf_pet_parent = self
                .overlay
                .as_ref()
                .is_some_and(|overlay| overlay.ist_layer_surface(&event.surface));
            match event.kind {
                // Klick im Menue. Ein deaktivierter Eintrag laesst das Menue
                // offen -- er hat ja nichts getan.
                PointerEventKind::Press {
                    button: BTN_LEFT, ..
                } if auf_menu => {
                    if let Some(aktion) = menu::aktion_bei(event.position.0, event.position.1) {
                        if aktion == menu::Aktion::Beenden {
                            // Erst schliessen, dann handeln: sonst laeuft die
                            // Schleife mit einem Popup im Zustand aus.
                            self.menu_schliessen();
                            self.menu_aktion_ausfuehren(aktion);
                        } else {
                            // T-9.1 A4: das Menue bleibt offen und zeichnet neu.
                            //
                            // ENTSCHIEDEN: die Zeile zeigt weiter den GEMELDETEN
                            // Zustand, nie den eigenen Wunsch. Fuer die Persona
                            // ist das die eben geschriebene Auswahldatei, die
                            // `eintraege()` neu liest -- der Punkt wandert also
                            // sofort und zu Recht. Fuer "Ohren aus"/"Augen aus"
                            // gibt es heute KEINEN gemeldeten Zustand: der Hub
                            // meldet dem Face nichts zurueck, `hub.rs` ist eine
                            // Einbahn. Solange nichts gemeldet ist, bleibt die
                            // Zeile deshalb unveraendert klickbar -- ein Menue,
                            // das "aus" zeigt, waehrend die Unit noch laeuft,
                            // waere schlimmer als Stille. Sichtbar ist dort
                            // heute nur, dass das Menue offen bleibt; eine
                            // ehrliche Zeile braucht erst einen Rueckkanal vom
                            // Hub, und den gibt es nicht (siehe T-9.1).
                            self.menu_aktion_ausfuehren(aktion);
                            self.menu_neu_zeichnen();
                        }
                    }
                }
                // Rechtsklick auf das Pet. Das ist die EINZIGE Stelle, an der
                // ein Popup mit Grab entsteht.
                PointerEventKind::Press {
                    button: BTN_RIGHT,
                    serial,
                    ..
                } if auf_sprite || auf_pet_parent => {
                    let sprite = self
                        .overlay
                        .as_ref()
                        .map(OverlaySurface::sprite_position)
                        .unwrap_or((0, 0));
                    // Auf der Sprite-Subsurface sind die Koordinaten lokal,
                    // auf der Elternsurface bereits Output-Koordinaten.
                    let anker = if auf_sprite {
                        (
                            sprite.0 + event.position.0 as i32,
                            sprite.1 + event.position.1 as i32,
                        )
                    } else {
                        (event.position.0 as i32, event.position.1 as i32)
                    };
                    self.menu_oeffnen(anker, serial);
                }
                PointerEventKind::Press {
                    button: BTN_LEFT, ..
                } if auf_bubble => self.bubble_schliessen(),
                PointerEventKind::Press {
                    button: BTN_LEFT, ..
                } if auf_sprite || auf_pet_parent => {
                    let position = self
                        .overlay
                        .as_ref()
                        .map(OverlaySurface::sprite_position)
                        .unwrap_or((0, 0));
                    self.ziehen = Some(Ziehen::beginnen(event.position, position));
                }
                // T-9.1 A1: Bewegung ueber dem Menue setzt den Schwebeindex,
                // Bewegung ausserhalb loescht ihn. Getrennt vom Ziehen des
                // Pets: die beiden teilen keine Koordinaten.
                PointerEventKind::Motion { .. } if auf_menu => {
                    if self
                        .menu
                        .zeiger_bewegt(event.position.0, event.position.1)
                    {
                        self.menu_neu_zeichnen();
                    }
                }
                // Der Zeiger hat die Popup-Surface verlassen. `Motion` kommt
                // danach nicht mehr fuer sie -- ohne dieses Ereignis bliebe
                // die zuletzt beruehrte Zeile hinterlegt stehen.
                PointerEventKind::Leave { .. } if auf_menu => self.menu_schwebe_loeschen(),
                PointerEventKind::Motion { .. } => {
                    self.menu_schwebe_loeschen();
                    let neue_position = self.ziehen.as_mut().and_then(|ziehen| {
                        ziehen.bewegen(event.position, self.sprite_groesse, self.output_groesse)
                    });
                    if let (Some(position), Some(overlay)) = (neue_position, self.overlay.as_mut())
                    {
                        overlay.sprite_position_setzen(
                            &self.compositor,
                            position,
                            self.sprite_groesse,
                        );
                        self.diagnose_position_setzen(position);
                        self.zipfel_nachfuehren();
                    }
                }
                PointerEventKind::Release {
                    button: BTN_LEFT, ..
                } => {
                    if let Some(ziehen) = self.ziehen.take() {
                        if let Loslassen::Zug(position) = ziehen.loslassen() {
                            if let Some(overlay) = self.overlay.as_mut() {
                                overlay.sprite_position_abschliessen(
                                    &self.compositor,
                                    position,
                                    self.sprite_groesse,
                                );
                            }
                            self.position_speichern(position);
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

impl OutputHandler for App {
    fn output_state(&mut self) -> &mut OutputState {
        &mut self.output_state
    }

    fn new_output(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}

    fn update_output(&mut self, _: &Connection, _: &QueueHandle<Self>, _: wl_output::WlOutput) {}

    fn output_destroyed(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        output: wl_output::WlOutput,
    ) {
        // Nur der gebundene Output zaehlt. Ist er bereits abgeraeumt, hat
        // `closed` den Wechsel schon erledigt und `gebundener_output` ist None.
        if self.gebundener_output.as_ref() != Some(&output) {
            return;
        }
        self.output_neu_binden();
    }
}

impl LayerShellHandler for App {
    fn closed(&mut self, _: &Connection, _: &QueueHandle<Self>, layer: &LayerSurface) {
        // Nach einer Neuerzeugung trifft das `closed` der ALTEN Surface ein.
        // Das darf den Prozess nicht mehr beenden -- genau daran starb das
        // Face vor T-2.5 bei jedem Monitorwechsel.
        if !self
            .overlay
            .as_ref()
            .is_some_and(|overlay| overlay.layer() == layer)
        {
            return;
        }
        // ponytail: kein Grund-Unterscheiden. Der Compositor sagt nicht, warum
        // er schliesst; wir versuchen einen anderen Output und beenden nur,
        // wenn keiner uebrig ist. Obergrenze: schliesst der Compositor uns bei
        // genau einem Monitor aus einem anderen Grund, beenden wir wie bisher.
        // Ausbaupfad, falls das je auffaellt: Wiederholung am selben Output
        // mit Backoff statt sofortigem Beenden.
        self.output_neu_binden();
    }

    fn configure(
        &mut self,
        _: &Connection,
        _: &QueueHandle<Self>,
        layer: &LayerSurface,
        configure: LayerSurfaceConfigure,
        _: u32,
    ) {
        match self.diagnose.lock() {
            Ok(mut zustand) => zustand.configure_empfangen += 1,
            Err(vergiftet) => vergiftet.into_inner().configure_empfangen += 1,
        }
        let muss_zeichnen = self
            .overlay
            .as_ref()
            .is_some_and(|overlay| overlay.layer() == layer && !overlay.hat_puffer());
        if !muss_zeichnen {
            return;
        }
        if let Some(overlay) = self.overlay.as_mut() {
            overlay
                .output_groesse_setzen((configure.new_size.0 as i32, configure.new_size.1 as i32));
        }

        let basis_committiert = self
            .overlay
            .as_mut()
            .is_some_and(|overlay| overlay.transparenten_puffer_committen(&mut self.pool));
        if basis_committiert {
            let sprite_ergebnis = {
                let sichtbar = self.sichtbarkeit.0;
                let Self {
                    overlay,
                    compositor,
                    pool,
                    atlas,
                    aktueller_zustand,
                    aktueller_mood,
                    aktueller_voice,
                    aktueller_mitschnitt,
                    render,
                    qh,
                    ..
                } = self;
                let (toenung, _) = render.wert(Instant::now());
                let callback_armieren = sichtbar && render.callback_armieren();
                overlay
                    .as_mut()
                    .expect("Overlay existiert im configure")
                    .sprite_committen(
                        compositor,
                        pool,
                        atlas,
                        aktueller_zustand,
                        aktueller_mood,
                        aktueller_voice,
                        *aktueller_mitschnitt,
                        toenung,
                        sichtbar,
                        qh,
                        callback_armieren,
                    )
            };
            match sprite_ergebnis {
                Ok((commits, indikator, mitschnitt_indikator)) => {
                    self.commits_zaehlen(commits);
                    self.indikator_zaehlen(indikator);
                    self.mitschnitt_indikator_zaehlen(mitschnitt_indikator);
                    self.diagnose_sprite_setzen(&self.aktueller_zustand);
                    self.bubble_aktualisieren();
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

impl PopupHandler for App {
    /// Erst nach dem configure darf ein Puffer an die Popup-Surface. Gemappt
    /// ist sie ab genau diesem Commit -- deshalb steht `menu_offen` hier und
    /// nicht schon beim Erzeugen.
    fn configure(&mut self, _: &Connection, _: &QueueHandle<Self>, popup: &Popup, _: PopupConfigure) {
        if !self.menu.ist_popup(popup) {
            return;
        }
        let raster = menu::rendern(self.bubble_renderer.font(), self.menu.schwebe());
        let Self { menu, pool, .. } = self;
        match menu.puffer_committen(pool, &raster) {
            Ok(true) => self.diagnose_menu_offen_setzen(true),
            Ok(false) => {}
            Err(fehler) => {
                eprintln!("Kontextmenue konnte nicht gezeichnet werden: {fehler}");
                self.menu_schliessen();
            }
        }
    }

    /// Auto-Dismiss: der Compositor hat das Popup verworfen -- Klick daneben,
    /// Escape, Fokusverlust. Wir zerstoeren es hier und halten insbesondere
    /// keinen Grab.
    fn done(&mut self, _: &Connection, _: &QueueHandle<Self>, popup: &Popup) {
        if self.menu.ist_popup(popup) {
            self.menu_schliessen();
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
        .filter(|name| zustand_abbilden(name, "", &atlas.layout).zurueckgefallen)
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
    // T-2.7: nur fuer das Kontextmenue. `xdg_wm_base` bringt keinen
    // GPU-Kontext mit -- die Zusage aus T-1.4 bleibt unberuehrt.
    let xdg_shell = menu::PopupShell::binden(&globals, &qh).expect("xdg_wm_base fehlt");
    let shm = Shm::bind(&globals, &qh).expect("wl_shm fehlt");
    let pool = SlotPool::new(4, &shm).expect("wl_shm-SlotPool");
    let diagnose = Arc::new(Mutex::new(FaceState {
        mood: "idle".into(),
        sprite: "ruhig".into(),
        sichtbar: true,
        ..FaceState::default()
    }));
    let _diag_socket = optionen
        .diag_socket
        .as_deref()
        .and_then(|pfad| DiagSocket::starten(pfad, Arc::clone(&diagnose)));

    // Kommandozeile gewinnt ueber die Umgebung. Ein ungueltiger Wert ist
    // oben schon Exit 2 -- ein stilles Standardverhalten waere bei einer
    // abschaltbaren Zusage die falsche Fehlerrichtung.
    let ton_an = match optionen.ton {
        Some(wert) => wert,
        None => match std::env::var("DAIMON_FACE_TON") {
            Err(_) => true,
            Ok(wert) => match wert.as_str() {
                "0" => false,
                "1" => true,
                // Kein stilles Standardverhalten bei einem Tippfehler: wer
                // den Ton abschalten will und sich vertippt, bekaeme ihn
                // sonst weiter zu hoeren und haette keinen Hinweis darauf.
                _ => {
                    eprintln!("DAIMON_FACE_TON braucht 0 oder 1, war: {wert}");
                    std::process::exit(2);
                }
            },
        },
    };

    let mut app = App {
        registry_state: RegistryState::new(&globals),
        output_state: OutputState::new(&globals, &qh),
        seat_state: SeatState::new(&globals, &qh),
        pointer: None,
        seat: None,
        shm,
        compositor,
        subcompositor,
        layer_shell,
        xdg_shell,
        qh: qh.clone(),
        // Fuer den 1x1-ARGB-Puffer reichen vier Byte; SlotPool vergroessert
        // sich spaeter fuer Sprites selbststaendig.
        pool,
        atlas,
        bubble_renderer: BubbleRenderer::neu().unwrap_or_else(|fehler| {
            eprintln!("{fehler}");
            std::process::exit(2);
        }),
        overlay: None,
        diagnose,
        aktueller_zustand: "ruhig".into(),
        aktueller_voice: "idle".into(),
        aktueller_mitschnitt: false,
        aktueller_mood: "idle".into(),
        sichtbarkeit: Sichtbarkeit(true),
        aktuelle_bubble: None,
        letzter_zipfel: None,
        hub: None,
        render: RenderSteuerung::neu("idle", Instant::now()),
        ton: Tonspieler::neu(ton_an),
        ziehen: None,
        menu: menu::Menu::neu(),
        sprite_groesse: (0, 0),
        output_groesse: (0, 0),
        gebundener_output: None,
        // Ein leerer Wert ist wie „nicht gesetzt"; sonst waere ein
        // versehentlich geleertes DAIMON_FACE_OUTPUT ein unbekannter Name.
        output_wunsch: std::env::var("DAIMON_FACE_OUTPUT")
            .ok()
            .filter(|wert| !wert.is_empty()),
        positionsdatei: position::zustandspfad(),
        beendet: false,
    };

    // Erst nach den Roundtrips kennt OutputState die wl_output-Objekte.
    event_queue.roundtrip(&mut app).expect("Output-Roundtrip");
    event_queue.roundtrip(&mut app).expect("Output-Roundtrip");
    let (output, output_name) =
        output::waehlen(&app.output_state, app.output_wunsch.as_deref(), None)
            .unwrap_or_else(|| {
                eprintln!("kein wl_output gefunden");
                std::process::exit(2);
            });
    match app.output_wunsch.as_deref() {
        Some(wunsch) if wunsch != output_name => eprintln!(
            "DAIMON_FACE_OUTPUT={wunsch} ist unbekannt; Rueckfall auf ersten Output {output_name}"
        ),
        Some(_) => eprintln!("Output gebunden (DAIMON_FACE_OUTPUT): {output_name}"),
        None => eprintln!("Kein DAIMON_FACE_OUTPUT gesetzt; erster Output: {output_name}"),
    }
    app.gebundener_output = Some(output.clone());
    app.diagnose_output_setzen(&output_name, false);
    let output_groesse = app
        .output_state
        .info(&output)
        .and_then(|info| info.logical_size)
        .unwrap_or((1920, 1080));
    let sprite_groesse = (
        app.atlas.layout.cell_w as i32,
        app.atlas.layout.cell_h as i32,
    );

    let positionsdatei = position::zustandspfad();
    // --sprite-position ist die Vorgabe fuer fehlenden/kaputten Zustand.
    // Eine heile gemerkte Position gewinnt, sonst wuerde jede mit einer
    // Standardposition gestartete Session die Persistenz wirkungslos machen.
    let vorgabeposition = optionen.sprite_position.unwrap_or((0, 0));
    let startposition = position::laden(&positionsdatei, vorgabeposition);
    let startposition = position::position_klemmen(startposition, sprite_groesse, output_groesse);
    app.sprite_groesse = sprite_groesse;
    app.output_groesse = output_groesse;
    app.positionsdatei = positionsdatei;
    app.diagnose_position_setzen(startposition);

    let overlay = OverlaySurface::neu(
        &app.compositor,
        &app.subcompositor,
        &app.layer_shell,
        &qh,
        &output,
        startposition,
        sprite_groesse,
        output_groesse,
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
            if let ChannelEvent::Msg(befehl) = event {
                match befehl.strip_prefix("mood:") {
                    // Derselbe Weg wie ein Hub-Snapshot: ein zweiter,
                    // eigener Pfad wuerde etwas anderes pruefen als den
                    // Ernstfall.
                    Some(mood) => {
                        let rev = app.naechste_rev();
                        let zustand = HubZustand {
                            rev,
                            mood: mood.to_owned(),
                            bubble: app.aktuelle_bubble.clone(),
                            // Der Steuerbefehl setzt den MOOD. Den
                            // Sprachzustand traegt er nicht mit -- der
                            // gehoert dem Hub, und ein Steuerpfad, der ihn
                            // nebenbei aendert, waere eine zweite Quelle.
                            voice: app.voice_state(),
                            // Ebenso der Mitschnitt: er gehoert dem Hub.
                            mitschnitt: app.aktueller_mitschnitt,
                        };
                        app.hub_zustand_uebernehmen(&zustand);
                    }
                    // T-2.7: der Steuerbefehl loest die AKTION aus. Er
                    // oeffnet kein Popup -- siehe `control.rs`.
                    None if befehl.starts_with("menu:") => {
                        if let Some(aktion) = befehl
                            .strip_prefix("menu:")
                            .and_then(menu::Aktion::aus_name)
                        {
                            app.menu_aktion_ausfuehren(aktion);
                        }
                    }
                    None => match befehl.as_str() {
                        "sichtbar:true" => {
                            app.sichtbarkeit_setzen(true);
                        }
                        "sichtbar:false" => {
                            app.sichtbarkeit_setzen(false);
                        }
                        _ => app.zustand_setzen(&befehl),
                    },
                }
            }
        })
        .expect("Control-Kanal konnte nicht in calloop eingefuegt werden");
    let _control_socket = optionen.control_socket.as_deref().map(|pfad| {
        ControlSocket::starten(pfad, control_sender).unwrap_or_else(|fehler| {
            eprintln!("{fehler}");
            std::process::exit(2);
        })
    });

    let (hub_sender, hub_channel) = channel::<HubZustand>();
    handle
        .insert_source(hub_channel, |event, _, app| {
            if let ChannelEvent::Msg(zustand) = event {
                app.hub_zustand_uebernehmen(&zustand);
            }
        })
        .expect("Hub-Kanal konnte nicht in calloop eingefuegt werden");
    app.hub = optionen
        .hub_socket
        .as_deref()
        .map(|pfad| HubVerbindung::starten(pfad, hub_sender));

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

    #[test]
    fn sleeping_blendet_aus_und_jeder_andere_mood_ein() {
        assert!(!Sichtbarkeit::fuer_mood("sleeping"));
        for mood in [
            "idle",
            "observing",
            "thinking",
            "working",
            "done",
            "failed",
            "needs_input",
        ] {
            assert!(Sichtbarkeit::fuer_mood(mood), "{mood}");
        }
    }

    #[test]
    fn doppeltes_aus_ist_harmloser_noop() {
        let mut zustand = Sichtbarkeit(true);
        assert!(zustand.setzen(false));
        assert!(!zustand.setzen(false));
        assert!(!zustand.0);
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

    /// Das Doppel-Self-Pet ist die Vorgabe, sobald es da ist -- und nur dann.
    ///
    /// Die zweite Haelfte ist die wichtigere: die Gesichter sind nicht im
    /// Repo. Ohne den Rueckfall waere ein frischer Checkout kein Ember,
    /// sondern Exit 2 beim Laden des Sheets.
    #[test]
    fn doppelself_gilt_vor_ember_und_faellt_sauber_zurueck() {
        let cwd = testverzeichnis();
        let assets = cwd.join("assets");
        fs::create_dir_all(&assets).unwrap();
        fs::write(assets.join("pet.json"), b"{}").unwrap();
        let dev = PathBuf::from("/entwicklung/pet.json");

        // Ohne Doppel-Self bleibt es beim Ember -- die Positivkontrolle zum
        // Fall darunter. Ohne sie hiesse ein gruener Test nur, dass
        // IRGENDEIN Pfad herauskommt.
        assert_eq!(
            pet_manifest_waehlen(None, None, &cwd, &dev),
            (assets.join("pet.json"), ManifestQuelle::Arbeitsverzeichnis)
        );

        let ds = assets.join("doppelself");
        fs::create_dir_all(&ds).unwrap();
        fs::write(ds.join("pet.json"), b"{}").unwrap();
        assert_eq!(
            pet_manifest_waehlen(None, None, &cwd, &dev),
            (ds.join("pet.json"), ManifestQuelle::Arbeitsverzeichnis)
        );

        // Und die ausdrueckliche Wahl schlaegt die Vorgabe weiterhin.
        let cli = PathBuf::from("/cli/pet.json");
        assert_eq!(
            pet_manifest_waehlen(Some(cli.clone()), None, &cwd, &dev),
            (cli, ManifestQuelle::Kommandozeile)
        );

        // Verzeichnis da, Manifest weg: kein halber Zustand, sondern Ember.
        fs::remove_file(ds.join("pet.json")).unwrap();
        assert_eq!(
            pet_manifest_waehlen(None, None, &cwd, &dev),
            (assets.join("pet.json"), ManifestQuelle::Arbeitsverzeichnis)
        );
        fs::remove_dir_all(cwd).unwrap();
    }
}
