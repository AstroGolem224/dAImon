//! Sichere Basissurface fuer das Overlay.
//!
//! Hier liegen alle `commit()`-Aufrufe. Dadurch gibt es keinen bequemen
//! Nebenweg an `InputRegion::darf_committen()` vorbei.

use std::collections::HashMap;

use smithay_client_toolkit::{
    compositor::{CompositorState, FrameCallbackData, Region, SurfaceData},
    shell::{
        wlr_layer::{Anchor, KeyboardInteractivity, Layer, LayerShell, LayerSurface},
        WaylandSurface,
    },
    shm::slot::SlotPool,
    subcompositor::{SubcompositorState, SubsurfaceData},
};
use wayland_client::{
    protocol::{wl_output, wl_shm, wl_subsurface, wl_surface},
    QueueHandle,
};

use crate::{
    bubble::{position_klemmen, zipfel_fuer, Raster as BubbleRaster, Zipfel},
    input::{sichtbare_laeufe, Box2D, InputRegion},
    render::{frame_toenen_wenn, Animator, Toenung},
    sprite::{indikator_malen, mitschnitt_malen, zustand_abbilden, SpriteAtlas, ZustandsAbbildung},
};

struct SpriteSurface {
    subsurface: wl_subsurface::WlSubsurface,
    surface: wl_surface::WlSurface,
    input_region: InputRegion,
    input_laeufe: InputLaufCache,
    aktuelle_input_laeufe: Vec<Box2D>,
    position: (i32, i32),
    letzter_frame: Vec<u8>,
}

struct BubbleSurface {
    subsurface: wl_subsurface::WlSubsurface,
    surface: wl_surface::WlSurface,
    input_region: InputRegion,
    position: (i32, i32),
    groesse: (i32, i32),
    sichtbar: bool,
}

pub struct OverlaySurface {
    layer: LayerSurface,
    input_region: InputRegion,
    sprite: SpriteSurface,
    bubble: BubbleSurface,
    sprite_groesse: (i32, i32),
    output_groesse: (i32, i32),
    puffer_committiert: bool,
    letzte_parent_region_position: Option<(i32, i32)>,
}

const INPUT_REGION_SCHRITT_PX: i32 = 8;

#[derive(Default)]
struct InputLaufCache {
    laeufe: HashMap<(u32, u32), Vec<Box2D>>,
    #[cfg(test)]
    berechnungen: u64,
}

impl InputLaufCache {
    /// Die klickbare Silhouette einer ZEILE, nicht einer Zelle.
    ///
    /// `maske` wird nur beim Fehltreffer gerufen -- das Vereinigen kostet
    /// `spalten` mal eine Zelle, und das soll einmal je Zeile anfallen und
    /// nicht zwoelfmal je Sekunde.
    fn fuer_zeile<F>(&mut self, schluessel: (u32, u32), maske: F, breite: u32, hoehe: u32) -> &[Box2D]
    where
        F: FnOnce() -> Vec<u8>,
    {
        self.laeufe.entry(schluessel).or_insert_with(|| {
            #[cfg(test)]
            {
                self.berechnungen += 1;
            }
            sichtbare_laeufe(&maske(), breite, hoehe)
        })
    }
}

impl OverlaySurface {
    pub fn neu<State>(
        compositor: &CompositorState,
        subcompositor: &SubcompositorState,
        layer_shell: &LayerShell,
        qh: &QueueHandle<State>,
        output: &wl_output::WlOutput,
        sprite_position: (i32, i32),
        sprite_groesse: (i32, i32),
        output_groesse: (i32, i32),
    ) -> Self
    where
        State: wayland_client::Dispatch<
                wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::ZwlrLayerSurfaceV1,
                smithay_client_toolkit::shell::wlr_layer::LayerSurfaceData,
            > + wayland_client::Dispatch<
                wayland_client::protocol::wl_surface::WlSurface,
                SurfaceData<()>,
            > + wayland_client::Dispatch<wl_subsurface::WlSubsurface, SubsurfaceData>
            + 'static,
    {
        let wl_surface = compositor.create_surface(qh);
        let layer = layer_shell.create_layer_surface(
            qh,
            wl_surface,
            Layer::Overlay,
            Some("daimon-face"),
            // NULL wuerde den zuletzt benutzten Monitor waehlen und ist fuer
            // ein pro-Output-Overlay deshalb keine stabile Zuordnung.
            Some(output),
        );
        let (subsurface, sprite_surface) =
            subcompositor.create_subsurface(layer.wl_surface().clone(), qh);
        subsurface.set_desync();
        subsurface.set_position(sprite_position.0, sprite_position.1);
        // Die Blase ist ein Kind des Pet, nicht der bildschirmgrossen
        // Layer-Surface. Dadurch folgt sie jeder Pet-Bewegung strukturell.
        let (bubble_subsurface, bubble_surface) =
            subcompositor.create_subsurface(sprite_surface.clone(), qh);
        bubble_subsurface.set_desync();
        bubble_subsurface.set_position(sprite_groesse.0, sprite_groesse.1);

        let mut ergebnis = Self {
            layer,
            input_region: InputRegion::new(),
            sprite: SpriteSurface {
                subsurface,
                surface: sprite_surface,
                input_region: InputRegion::new(),
                input_laeufe: InputLaufCache::default(),
                aktuelle_input_laeufe: Vec::new(),
                position: sprite_position,
                letzter_frame: Vec::new(),
            },
            bubble: BubbleSurface {
                subsurface: bubble_subsurface,
                surface: bubble_surface,
                input_region: InputRegion::new(),
                position: sprite_groesse,
                groesse: (0, 0),
                sichtbar: false,
            },
            sprite_groesse,
            output_groesse,
            puffer_committiert: false,
            letzte_parent_region_position: None,
        };
        ergebnis.properties_neu_setzen();

        // Eine explizit leere opaque_region verhindert, dass der Compositor
        // den transparenten Basispuffer als deckend optimiert.
        let opaque = Region::new(compositor).expect("wl_region fuer opaque_region");
        ergebnis.layer.set_opaque_region(Some(opaque.wl_region()));

        // Das ist absichtlich auch fuer `None` ein Protokollaufruf: None
        // bedeutet hier eine gesetzte, leere Region und damit Click-Through.
        ergebnis
            .input_region
            .anwenden(compositor, ergebnis.layer.wl_surface(), None);
        ergebnis
    }

    pub fn layer(&self) -> &LayerSurface {
        &self.layer
    }

    pub fn hat_puffer(&self) -> bool {
        self.puffer_committiert
    }

    pub fn ist_sprite_surface(&self, surface: &wl_surface::WlSurface) -> bool {
        &self.sprite.surface == surface
    }

    pub fn ist_layer_surface(&self, surface: &wl_surface::WlSurface) -> bool {
        self.layer.wl_surface() == surface
    }

    pub fn ist_bubble_surface(&self, surface: &wl_surface::WlSurface) -> bool {
        &self.bubble.surface == surface
    }

    pub fn sprite_position(&self) -> (i32, i32) {
        self.sprite.position
    }

    /// Initialer Commit ohne Buffer; erst danach darf der Compositor das
    /// verpflichtende configure senden.
    pub fn initial_commit(&self) -> bool {
        self.sicher_committen()
    }

    /// Mappt die bildschirmfuellende Rolle mit einem einzigen transparenten
    /// Pixel. Sprite und Sprechblase kommen spaeter als Subsurfaces dazu; die
    /// Basissurface braucht deshalb keinen bildschirmgrossen Speicher.
    pub fn transparenten_puffer_committen(&mut self, pool: &mut SlotPool) -> bool {
        if !self.input_region.darf_committen() {
            eprintln!("SICHERHEITSABBRUCH: transparenter Puffer ohne gesetzte Input-Region");
            return false;
        }

        let (buffer, pixel) = pool
            .create_buffer(1, 1, 4, wl_shm::Format::Argb8888)
            .expect("1x1-wl_shm-Puffer");
        pixel.fill(0);
        self.layer.wl_surface().damage_buffer(0, 0, 1, 1);
        buffer
            .attach_to(self.layer.wl_surface())
            .expect("wl_shm-Puffer anhaengen");
        self.layer.commit();
        self.puffer_committiert = true;
        true
    }

    /// Zeichnet Frame 0 des gewaehlten Zustands auf die desynchronisierte
    /// Sprite-Subsurface. Beim ersten sichtbaren Sprite wird zuvor die
    /// Input-Regionen der Elternsurface und der Sprite-Subsurface gesetzt.
    /// Beide Surfaces laufen vor ihrem jeweiligen Commit durch ihr eigenes
    /// Sicherheits-Gate.
    ///
    /// Rueckgabewert ist die Anzahl tatsaechlicher Wayland-Commits sowie,
    /// je Indikator, ob er wirklich in den Puffer gemalt wurde. ZWEI
    /// Wahrheitswerte und nicht einer: der Sprach- und der
    /// Mitschnitt-Punkt sagen Verschiedenes, und der zweite ist der
    /// rechtlich schwerere. Bis zum 19.08. wurde er hier verworfen
    /// (BEFUND T-7.3 K9).
    pub fn sprite_committen(
        &mut self,
        compositor: &CompositorState,
        pool: &mut SlotPool,
        atlas: &SpriteAtlas,
        zustand: &str,
        mood: &str,
        voice: &str,
        mitschnitt: bool,
        animator: &Animator,
        toenung: Toenung,
        sichtbar: bool,
        qh: &QueueHandle<crate::App>,
        callback_armieren: bool,
    ) -> Result<(u64, bool, bool), String> {
        let (abbildung, mut frame) =
            sprite_zelle_bauen(atlas, zustand, mood, animator.spalte(), toenung, sichtbar)?;
        // T-3.14: der Indikator gehoert in das BILD, nicht in die
        // Eingabemaske. Waere er in beidem, waechse die Klickflaeche des Pets
        // mit dem Sprachzustand -- und das Ziehen aus T-2.4 haette je nach
        // Zustand eine andere Trefferflaeche. Seit T-9.2 ist das strukturell
        // erledigt: die Maske kommt aus dem Atlas und sieht den Indikator nie.
        let indikator_gemalt = sichtbar
            && indikator_malen(
                &mut frame,
                voice,
                atlas.layout.cell_w,
                atlas.layout.cell_h,
            );
        // T-7.3: der zweite Punkt, links oben. Bewusst getrennt gemalt und
        // getrennt gezaehlt -- er sagt etwas anderes als der erste.
        let mitschnitt_gemalt = sichtbar
            && mitschnitt_malen(
                &mut frame,
                mitschnitt,
                atlas.layout.cell_w,
                atlas.layout.cell_h,
            );
        let breite = i32::try_from(atlas.layout.cell_w)
            .map_err(|_| "Sprite-Breite passt nicht in i32".to_string())?;
        let hoehe = i32::try_from(atlas.layout.cell_h)
            .map_err(|_| "Sprite-Hoehe passt nicht in i32".to_string())?;
        let stride = breite
            .checked_mul(4)
            .ok_or_else(|| "Sprite-Stride ist zu gross".to_string())?;

        let schaden = geaendertes_rechteck(
            &self.sprite.letzter_frame,
            &frame,
            atlas.layout.cell_w,
            atlas.layout.cell_h,
        );

        // Bei einem Rundungsschritt ohne geaenderte Pixel wird kein Puffer
        // angehaengt und kein damage gemeldet. Ein laufender Uebergang braucht
        // dennoch seinen bereits fest zugesagten naechsten Callback; der
        // bufferlose Commit aktiviert nur diesen Request und zaehlt nicht als
        // gerenderter Frame.
        if schaden.is_none() {
            if callback_armieren {
                self.sprite
                    .surface
                    .frame(qh, FrameCallbackData(self.sprite.surface.clone()));
                self.sprite.surface.commit();
            }
            return Ok((0, indikator_gemalt, mitschnitt_gemalt));
        }

        // Erst alle falliblen Pufferschritte abschliessen. So kann danach kein
        // bereits erfolgter Input-Commit in einem Err-Rueckgabepfad verloren
        // gehen.
        let (buffer, canvas) = pool
            .create_buffer(breite, hoehe, stride, wl_shm::Format::Argb8888)
            .map_err(|fehler| format!("wl_shm-Sprite-Puffer: {fehler}"))?;
        canvas.copy_from_slice(&frame);
        let schaden = schaden.expect("oben auf None geprueft");
        self.sprite
            .surface
            .damage_buffer(schaden.x, schaden.y, schaden.w, schaden.h);
        if callback_armieren {
            self.sprite
                .surface
                .frame(qh, FrameCallbackData(self.sprite.surface.clone()));
        }
        buffer
            .attach_to(&self.sprite.surface)
            .map_err(|fehler| format!("wl_shm-Sprite anhaengen: {fehler}"))?;

        let spalten = animator.spalten();
        let input_laeufe = if sichtbar {
            self.sprite
                .input_laeufe
                .fuer_zeile(
                    (abbildung.zeile, spalten),
                    || alpha_vereinigen(atlas, abbildung.zeile, spalten),
                    atlas.layout.cell_w,
                    atlas.layout.cell_h,
                )
                .to_vec()
        } else {
            Vec::new()
        };
        self.sprite.aktuelle_input_laeufe.clone_from(&input_laeufe);
        let parent_laeufe = laeufe_versetzen(&input_laeufe, self.sprite.position);
        if self
            .input_region
            .laeufe_anwenden(compositor, self.layer.wl_surface(), &parent_laeufe)
        {
            self.letzte_parent_region_position = Some(self.sprite.position);
            if !self.sicher_committen() {
                return Err("Input-Region konnte nicht sicher committet werden".into());
            }
        }

        self.sprite
            .input_region
            .laeufe_anwenden(compositor, &self.sprite.surface, &input_laeufe);
        if !self.sprite.input_region.darf_committen() {
            eprintln!("SICHERHEITSABBRUCH: Sprite-Subsurface ohne gesetzte Input-Region");
            return Err("Sprite-Commit durch eigenes Input-Gate abgelehnt".into());
        }
        self.sprite.surface.commit();
        self.sprite.letzter_frame = frame;
        Ok((1, indikator_gemalt, mitschnitt_gemalt))
    }

    /// Wohin der Zipfel einer Blase dieser Groesse gehoert. Getrennt vom
    /// Commit, weil der Aufrufer ihn VOR dem Commit in den Puffer malen muss
    /// -- und weil das Ziehen des Pet ihn ohne Neurendern abfragen koennen
    /// muss.
    pub fn zipfel_fuer_blase(&self, bubble_groesse: (i32, i32)) -> Zipfel {
        zipfel_fuer(
            self.sprite.position,
            self.sprite_groesse,
            bubble_groesse,
            self.output_groesse,
        )
    }

    /// Groesse der zuletzt gezeigten Blase, oder `None`, wenn keine steht.
    pub fn bubble_groesse(&self) -> Option<(i32, i32)> {
        self.bubble.sichtbar.then_some(self.bubble.groesse)
    }

    /// Zeichnet ausschliesslich die Blasen-Subsurface. Der Sprite-Puffer wird
    /// weder angehaengt noch beschaedigt; seine Diagnose bleibt unveraendert.
    pub fn bubble_committen(
        &mut self,
        compositor: &CompositorState,
        pool: &mut SlotPool,
        raster: &BubbleRaster,
    ) -> Result<u64, String> {
        let stride = raster
            .breite
            .checked_mul(4)
            .ok_or_else(|| "Blasen-Stride ist zu gross".to_string())?;
        let (buffer, canvas) = pool
            .create_buffer(
                raster.breite,
                raster.hoehe,
                stride,
                wl_shm::Format::Argb8888,
            )
            .map_err(|fehler| format!("wl_shm-Blasen-Puffer: {fehler}"))?;
        puffer_fuellen(canvas, &raster.pixel).map_err(|fehler| format!("Blase: {fehler}"))?;

        let groesse = (raster.breite, raster.hoehe);
        let position = position_klemmen(
            self.sprite.position,
            self.sprite_groesse,
            groesse,
            self.output_groesse,
        );
        if self.bubble.position != position {
            self.bubble.position = position;
            self.bubble.subsurface.set_position(position.0, position.1);
            // set_position ist Zustand des Parent (hier: Pet). Der
            // bufferlose Commit bewegt nur die Subsurface.
            self.sprite.surface.commit();
        }

        self.bubble.input_region.anwenden(
            compositor,
            &self.bubble.surface,
            Some(Box2D {
                x: 0,
                y: 0,
                w: raster.breite,
                h: raster.hoehe,
            }),
        );
        if !self.bubble.input_region.darf_committen() {
            return Err("Blasen-Commit durch eigenes Input-Gate abgelehnt".into());
        }
        self.bubble
            .surface
            .damage_buffer(0, 0, raster.breite, raster.hoehe);
        buffer
            .attach_to(&self.bubble.surface)
            .map_err(|fehler| format!("wl_shm-Blase anhaengen: {fehler}"))?;
        self.bubble.surface.commit();
        self.bubble.groesse = groesse;
        self.bubble.sichtbar = true;
        Ok(1)
    }

    /// Zeichnet einen transparenten Puffer und leert die Input-Region. Die
    /// Surface bleibt dabei gemappt.
    pub fn bubble_ausblenden(&mut self, compositor: &CompositorState, pool: &mut SlotPool) -> bool {
        if !self.bubble.sichtbar {
            return false;
        }
        self.bubble
            .input_region
            .anwenden(compositor, &self.bubble.surface, None);
        if !self.bubble.input_region.darf_committen() {
            return false;
        }
        let (breite, hoehe) = self.bubble.groesse;
        let Some(stride) = breite.checked_mul(4) else {
            return false;
        };
        let Ok((buffer, canvas)) =
            pool.create_buffer(breite, hoehe, stride, wl_shm::Format::Argb8888)
        else {
            return false;
        };
        canvas.fill(0);
        self.bubble.surface.damage_buffer(0, 0, breite, hoehe);
        if buffer.attach_to(&self.bubble.surface).is_err() {
            return false;
        }
        self.bubble.surface.commit();
        self.bubble.sichtbar = false;
        self.bubble.groesse = (0, 0);
        true
    }

    pub fn output_groesse_setzen(&mut self, groesse: (i32, i32)) {
        if groesse.0 > 0 && groesse.1 > 0 {
            self.output_groesse = groesse;
        }
    }

    /// Verschiebt das Pet ohne Buffer-Aenderung. Nur die Position und die
    /// Input-Bounding-Box werden auf dem Parent angewandt.
    /// Die Sprite-Groesse nachziehen, wenn ein anderes Pet eine andere
    /// Zelle hat (Ember 192x208, ein quadratisches Doppel-Self 208x208).
    ///
    /// Der PUFFER braucht das nicht -- `sprite_committen` baut ihn bei jedem
    /// Commit aus `atlas.layout`. Hier haengen nur die beiden Dinge, die die
    /// Groesse ausserhalb des Puffers braucht: die Position der Sprechblase,
    /// die als Kind am unteren rechten Eck des Pets sitzt, und der Wert, mit
    /// dem `position_klemmen` das Pet am Bildschirmrand haelt.
    pub fn sprite_groesse_setzen(&mut self, groesse: (i32, i32)) {
        if self.sprite_groesse == groesse {
            return;
        }
        self.sprite_groesse = groesse;
        self.bubble.position = groesse;
        self.bubble
            .subsurface
            .set_position(groesse.0, groesse.1);
    }

    pub fn sprite_position_setzen(
        &mut self,
        compositor: &CompositorState,
        position: (i32, i32),
        groesse: (i32, i32),
    ) -> bool {
        self.sprite_position_setzen_intern(compositor, position, groesse, false)
    }

    /// Zieht die Parent-Region beim Loslassen exakt an die Sprite-Position.
    pub fn sprite_position_abschliessen(
        &mut self,
        compositor: &CompositorState,
        position: (i32, i32),
        groesse: (i32, i32),
    ) -> bool {
        self.sprite_position_setzen_intern(compositor, position, groesse, true)
    }

    fn sprite_position_setzen_intern(
        &mut self,
        compositor: &CompositorState,
        position: (i32, i32),
        _groesse: (i32, i32),
        region_erzwingen: bool,
    ) -> bool {
        let bewegt = self.sprite.position != position;
        if bewegt {
            self.sprite.position = position;
            self.sprite.subsurface.set_position(position.0, position.1);
        }
        if self.bubble.sichtbar {
            let bubble_position = position_klemmen(
                position,
                self.sprite_groesse,
                self.bubble.groesse,
                self.output_groesse,
            );
            if self.bubble.position != bubble_position {
                self.bubble.position = bubble_position;
                self.bubble
                    .subsurface
                    .set_position(bubble_position.0, bubble_position.1);
                self.sprite.surface.commit();
            }
        }
        // Acht Pixel sind deutlich kleiner als die 192x208-Pet-Box: der
        // Zeiger bleibt sicher in der Parent-Region. Zugleich erzeugt ein
        // 200-px-Zug hoechstens 25 statt 200 set_input_region-Aufrufe. Beim
        // Loslassen wird die Region unabhaengig vom Raster exakt nachgezogen.
        let region_faellig = region_erzwingen
            || self.letzte_parent_region_position.map_or(true, |alt| {
                (position.0 - alt.0).abs().max((position.1 - alt.1).abs())
                    >= INPUT_REGION_SCHRITT_PX
            });
        let parent_laeufe =
            region_faellig.then(|| laeufe_versetzen(&self.sprite.aktuelle_input_laeufe, position));
        let region_geaendert = parent_laeufe.is_some_and(|laeufe| {
            self.input_region
                .laeufe_anwenden(compositor, self.layer.wl_surface(), &laeufe)
        });
        if region_geaendert {
            self.letzte_parent_region_position = Some(position);
        }
        (bewegt || region_geaendert) && self.sicher_committen()
    }

    /// Historische Umgehung fuer KDE-Bug 503121: Nach einem NULL-Buffer-Unmap
    /// liefert KWin ohne diese Wiederholung kein neues configure. Im Spike
    /// waren es 0/20 ohne und 20/20 mit erneut gesetzten Layer-Properties.
    /// Seit T-2.4 wird sie nicht mehr benutzt: Ausblenden behaelt die Surface
    /// gemappt und committet einen transparenten Puffer. Der Kommentar bleibt,
    /// damit der gefaehrliche NULL-Buffer-Pfad nicht versehentlich zurueckkehrt.
    #[allow(dead_code)]
    pub fn remap_commit(&mut self) -> bool {
        self.properties_neu_setzen();
        self.puffer_committiert = false;
        self.sicher_committen()
    }

    fn properties_neu_setzen(&self) {
        self.layer.set_size(0, 0);
        self.layer
            .set_anchor(Anchor::TOP | Anchor::BOTTOM | Anchor::LEFT | Anchor::RIGHT);
        self.layer.set_layer(Layer::Overlay);
        self.layer.set_margin(0, 0, 0, 0);
        self.layer.set_exclusive_zone(-1);
        self.layer
            .set_keyboard_interactivity(KeyboardInteractivity::None);
    }

    fn sicher_committen(&self) -> bool {
        if !self.input_region.darf_committen() {
            // Lieber unsichtbar bleiben als mit einer vollflaechigen
            // Standard-Input-Region den Desktop unbedienbar machen.
            eprintln!("SICHERHEITSABBRUCH: commit ohne gesetzte Input-Region");
            return false;
        }
        self.layer.commit();
        true
    }
}

fn laeufe_versetzen(laeufe: &[Box2D], position: (i32, i32)) -> Vec<Box2D> {
    laeufe
        .iter()
        .map(|lauf| Box2D {
            x: lauf.x.saturating_add(position.0),
            y: lauf.y.saturating_add(position.1),
            ..*lauf
        })
        .collect()
}

/// Waehlt die Zelle und baut die Pixel, die committet werden. Gibt die
/// Abbildung mit zurueck, weil der Eingaberegion-Cache dieselbe Zeile braucht
/// -- zwei Fassungen derselben Wahl waeren eine Wahl und eine Attrappe.
///
/// Eigene Funktion und nicht ein Block in `sprite_committen`, weil dorthin
/// kein Test kommt: der Aufrufer braucht eine Wayland-Verbindung. Genau hier
/// wird aber die Spalte gewaehlt, und genau die soll pruefbar sein.
///
/// `spalte` kommt ueber das Pet-Manifest von aussen und wird darum auf die
/// vorhandene Spaltenzahl zurueckgefaltet statt in einen Fehler zu laufen --
/// dieselbe Nachsicht, die `zustand_abbilden` einer zu grossen Zeile gewaehrt.
/// Ein Fehler waere hier ein schwarzes Pet.
pub fn sprite_zelle_bauen(
    atlas: &SpriteAtlas,
    zustand: &str,
    mood: &str,
    spalte: u32,
    toenung: Toenung,
    sichtbar: bool,
) -> Result<(ZustandsAbbildung, Vec<u8>), String> {
    let abbildung = zustand_abbilden(zustand, mood, &atlas.layout);
    let spalte = spalte % atlas.layout.cols.max(1);
    // Ein Pet mit eigener Zeile je Mood traegt den Mood im Bild. Die
    // Toenung darueberzulegen zerstoert genau die Information, fuer die
    // die Zeile da ist.
    let frame = sichtbaren_frame_bauen(
        &frame_toenen_wenn(
            &atlas.frame(abbildung.zeile, spalte)?,
            toenung,
            atlas.layout.toenung,
        ),
        sichtbar,
    );
    Ok((abbildung, frame))
}

/// Vereinigt die Alphamasken aller Bilder einer Zeile zu einer Silhouette.
///
/// Sonst wanderte die klickbare Flaeche zwoelfmal je Sekunde mit der Bewegung:
/// ein Klick auf den Arm ginge ins Leere, sobald der Arm sich geruehrt hat, und
/// landete auf dem Desktop. Nur Alpha wird gelesen -- die Toenung laesst es
/// unveraendert, und die Farbe entscheidet nicht ueber Treffer.
fn alpha_vereinigen(atlas: &SpriteAtlas, zeile: u32, spalten: u32) -> Vec<u8> {
    let breite = atlas.layout.cell_w;
    let hoehe = atlas.layout.cell_h;
    let mut maske = vec![0u8; (breite as usize) * (hoehe as usize) * 4];
    for spalte in 0..spalten.max(1) {
        let Ok(zelle) = atlas.frame(zeile, spalte) else {
            continue;
        };
        if zelle.len() != maske.len() {
            continue;
        }
        for (ziel, quelle) in maske.chunks_exact_mut(4).zip(zelle.chunks_exact(4)) {
            ziel[3] = ziel[3].max(quelle[3]);
        }
    }
    maske
}

/// T-2.4 umgeht KDE-Bug 503121 vollstaendig: statt NULL-Buffer-Unmap bleibt
/// die Surface gemappt. Der Spike lieferte nach NULL-Unmap 0/20 configure ohne
/// erneute Properties und 20/20 mit; ein transparenter Puffer betritt diesen
/// fehlerhaften Pfad gar nicht. Der zusaetzliche Ruhe-RAM ist bewusst akzeptiert.
fn sichtbaren_frame_bauen(frame: &[u8], sichtbar: bool) -> Vec<u8> {
    if sichtbar {
        frame.to_vec()
    } else {
        vec![0; frame.len()]
    }
}

/// Kleinste achsenparallele Box, die alle geaenderten Pixel enthaelt. Beim
/// ersten Frame ist die ganze Zelle beschaedigt; unveraenderte Frames liefern
/// `None` und erzeugen damit kein `damage_buffer`.
fn geaendertes_rechteck(vorher: &[u8], nachher: &[u8], breite: u32, hoehe: u32) -> Option<Box2D> {
    if vorher.len() != nachher.len() || vorher.is_empty() {
        return Some(Box2D {
            x: 0,
            y: 0,
            w: i32::try_from(breite).ok()?,
            h: i32::try_from(hoehe).ok()?,
        });
    }

    let mut min_x = breite;
    let mut min_y = hoehe;
    let mut max_x = 0;
    let mut max_y = 0;
    let mut gefunden = false;
    for (index, (a, b)) in vorher
        .chunks_exact(4)
        .zip(nachher.chunks_exact(4))
        .enumerate()
    {
        if a == b {
            continue;
        }
        let index = index as u32;
        let x = index % breite;
        let y = index / breite;
        min_x = min_x.min(x);
        min_y = min_y.min(y);
        max_x = max_x.max(x);
        max_y = max_y.max(y);
        gefunden = true;
    }
    gefunden.then(|| Box2D {
        x: min_x as i32,
        y: min_y as i32,
        w: (max_x - min_x + 1) as i32,
        h: (max_y - min_y + 1) as i32,
    })
}

/// Legt ein Raster in einen wl_shm-Puffer, der GROESSER sein darf als das
/// Raster.
///
/// Der `SlotPool` gibt einen freien Slot heraus, sobald er gross genug ist,
/// und `create_buffer` liefert dessen ganzen Speicher zurueck -- nicht den
/// angeforderten Ausschnitt. `copy_from_slice` verlangt aber gleiche Laengen
/// und bricht sonst ab. Der Ueberhang wird geleert: der Compositor liest ihn
/// zwar nicht, aber Reste eines fremden Puffers gehoeren nicht in einen, den
/// wir gerade als fertig ausgeben.
fn puffer_fuellen(canvas: &mut [u8], pixel: &[u8]) -> Result<(), String> {
    if canvas.len() < pixel.len() {
        return Err(format!(
            "Puffer zu klein: {} Bytes fuer {} Bytes Raster",
            canvas.len(),
            pixel.len()
        ));
    }
    canvas[..pixel.len()].copy_from_slice(pixel);
    canvas[pixel.len()..].fill(0);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// BEFUND 31.08.: das Face stuerzte beim ersten Blasen-Commit ab --
    /// `copy_from_slice: source slice length (216720) does not match
    /// destination slice length (216768)`. Der `SlotPool` gibt einen freien
    /// Slot heraus, sobald er GROSS GENUG ist, und `canvas` ist dann der
    /// ganze Slot, nicht der angeforderte Ausschnitt. Solange die Blase eine
    /// feste Hoehe hatte, traf das nie zu.
    #[test]
    fn ein_zu_grosser_slot_wird_vorne_gefuellt_und_hinten_geleert() {
        let mut canvas = vec![9u8; 12];
        assert!(puffer_fuellen(&mut canvas, &[1, 2, 3, 4, 5, 6, 7, 8]).is_ok());
        assert_eq!(canvas, [1, 2, 3, 4, 5, 6, 7, 8, 0, 0, 0, 0]);
    }

    /// Positivkontrolle und Gegenrichtung: passgenau muss durchgehen, zu
    /// klein muss auffallen. Ohne beides waere ein `puffer_fuellen`, das gar
    /// nichts kopiert, genauso gruen wie das richtige.
    #[test]
    fn passgenau_geht_durch_und_zu_klein_faellt_auf() {
        let mut genau = vec![0u8; 4];
        assert!(puffer_fuellen(&mut genau, &[1, 2, 3, 4]).is_ok());
        assert_eq!(genau, [1, 2, 3, 4]);
        assert!(puffer_fuellen(&mut vec![0u8; 3], &[1, 2, 3, 4]).is_err());
    }

    #[test]
    fn damage_box_umschliesst_nur_geaenderte_pixel() {
        let vorher = vec![0; 4 * 4 * 3];
        let mut nachher = vorher.clone();
        nachher[(1 * 4 + 1) * 4] = 1;
        nachher[(2 * 4 + 3) * 4] = 1;
        assert_eq!(
            geaendertes_rechteck(&vorher, &nachher, 4, 3),
            Some(Box2D {
                x: 1,
                y: 1,
                w: 3,
                h: 2
            })
        );
        assert_eq!(geaendertes_rechteck(&vorher, &vorher, 4, 3), None);
    }

    fn standard_atlas() -> SpriteAtlas {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("assets/pet.json");
        SpriteAtlas::laden(&manifest).unwrap()
    }

    /// Die Naht aus T-9.2: eine Spaltennummer geht hinein, andere Pixel kommen
    /// heraus. Bis zum 01.09. stand hier eine feste 0, und jedes zusaetzliche
    /// Bild im Sheet war Rechenzeit fuer Pixel, die niemand sah.
    #[test]
    fn sprite_zelle_bauen_zwei_spalten_derselben_zeile_liefert_verschiedene_pixel() {
        // GIVEN das mitgelieferte Pet -- acht Spalten je Zeile:
        let atlas = standard_atlas();
        assert!(atlas.layout.cols > 1, "Testvoraussetzung: mehrspaltiges Sheet");
        let farblos = Toenung {
            r: 255,
            g: 255,
            b: 255,
        };

        // WHEN dieselbe Zeile einmal mit Spalte 0 und einmal mit Spalte 1 gebaut wird,
        let (abbildung_null, erste) =
            sprite_zelle_bauen(&atlas, "ruhig", "idle", 0, farblos, true).unwrap();
        let (abbildung_eins, zweite) =
            sprite_zelle_bauen(&atlas, "ruhig", "idle", 1, farblos, true).unwrap();

        // THEN unterscheiden sich die Pixel, und die Zeile bleibt dieselbe:
        assert_eq!(abbildung_null.zeile, abbildung_eins.zeile);
        assert_ne!(erste, zweite);
    }

    /// Die Spaltenzahl kommt aus dem Pet-Manifest, also von aussen. Eine zu
    /// grosse Spalte darf kein `Err` werden -- das waere ein schwarzes Pet,
    /// weil `sprite_committen` den Fehler nach oben reicht.
    #[test]
    fn sprite_zelle_bauen_spalte_jenseits_der_spaltenzahl_faltet_zurueck_statt_zu_scheitern() {
        // GIVEN dasselbe achtspaltige Sheet:
        let atlas = standard_atlas();
        let spalten = atlas.layout.cols;
        let farblos = Toenung {
            r: 255,
            g: 255,
            b: 255,
        };
        let bauen = |spalte| sprite_zelle_bauen(&atlas, "ruhig", "idle", spalte, farblos, true);

        // WHEN eine Spalte jenseits der Spaltenzahl verlangt wird,
        // THEN kommt genau die zurueckgefaltete Zelle, kein Fehler:
        assert_eq!(bauen(spalten).unwrap(), bauen(0).unwrap());
        assert_eq!(bauen(spalten + 1).unwrap(), bauen(1).unwrap());
    }

    /// Die Zusage aus T-9.2 Schritt 4: die klickbare Flaeche gehoert der
    /// ZEILE, nicht dem Einzelbild. Sonst ginge ein Klick auf den Arm ins
    /// Leere, sobald der Arm sich geruehrt hat.
    #[test]
    fn alpha_vereinigen_deckt_jedes_einzelbild_der_zeile_ab() {
        // GIVEN eine Zeile des mitgelieferten Pets mit sechs Bildern:
        let atlas = standard_atlas();
        let zeile = 0;
        let spalten = 6;
        let breite = atlas.layout.cell_w;
        let hoehe = atlas.layout.cell_h;

        // WHEN die Vereinigung gebildet wird,
        let vereint = sichtbare_laeufe(&alpha_vereinigen(&atlas, zeile, spalten), breite, hoehe);
        let flaeche = |laeufe: &[Box2D]| laeufe.iter().map(|b| b.w * b.h).sum::<i32>();

        // THEN deckt sie jedes Einzelbild ab und ist mindestens so gross wie
        // das groesste davon:
        let mut groesstes_einzelbild = 0;
        let mut irgendeines_kleiner = false;
        for spalte in 0..spalten {
            let zelle = atlas.frame(zeile, spalte).unwrap();
            let einzeln = flaeche(&sichtbare_laeufe(&zelle, breite, hoehe));
            assert!(einzeln > 0, "Spalte {spalte} ist leer -- Test misst nichts");
            groesstes_einzelbild = groesstes_einzelbild.max(einzeln);
            irgendeines_kleiner |= einzeln < flaeche(&vereint);
        }
        assert!(flaeche(&vereint) >= groesstes_einzelbild);
        // Positivkontrolle: die Bilder unterscheiden sich ueberhaupt. Waeren
        // alle sechs gleich, waere der Test oben gruen ohne etwas zu belegen.
        assert!(
            irgendeines_kleiner,
            "alle Einzelbilder gleich gross wie die Vereinigung"
        );
    }

    /// Und die Kehrseite: dieselbe Zeile als Standbild hat eine ANDERE, engere
    /// Silhouette als im Lauf. Darum steht die Bildzahl im Cache-Schluessel.
    #[test]
    fn alpha_vereinigen_ueber_ein_bild_ist_enger_als_ueber_die_ganze_zeile() {
        let atlas = standard_atlas();
        let breite = atlas.layout.cell_w;
        let hoehe = atlas.layout.cell_h;
        let flaeche = |spalten| {
            sichtbare_laeufe(&alpha_vereinigen(&atlas, 0, spalten), breite, hoehe)
                .iter()
                .map(|b| b.w * b.h)
                .sum::<i32>()
        };
        assert!(flaeche(1) < flaeche(6));
    }

    #[test]
    fn verschieben_berechnet_sprite_zeilenlaeufe_nicht_neu() {
        let mut cache = InputLaufCache::default();
        let maske = || vec![0, 0, 0, 255, 0, 0, 0, 0];
        assert_eq!(cache.fuer_zeile((0, 1), maske, 2, 1).len(), 1);
        // Eine andere Parent-Position aendert weder Zeilen-Schluessel noch
        // lokale Sprite-Region; der zweite Zugriff muss ein Cache-Treffer sein.
        assert_eq!(
            laeufe_versetzen(cache.fuer_zeile((0, 1), maske, 2, 1), (200, 100))[0],
            Box2D {
                x: 200,
                y: 100,
                w: 1,
                h: 1
            }
        );
        assert_eq!(cache.fuer_zeile((0, 1), maske, 2, 1).len(), 1);
        assert_eq!(cache.berechnungen, 1);
    }

    #[test]
    fn ausblenden_leert_pixel_und_input_einblenden_stellt_lauefe_wieder_her() {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("assets/pet.json");
        let atlas = SpriteAtlas::laden(&manifest).unwrap();
        let frame = atlas.frame(0, 0).unwrap();
        let aus = sichtbaren_frame_bauen(&frame, false);
        let ein = sichtbaren_frame_bauen(&frame, true);

        assert!(aus.chunks_exact(4).all(|pixel| pixel[3] == 0));
        assert_eq!(sichtbare_laeufe(&aus, 192, 208).len(), 0);
        assert_eq!(sichtbare_laeufe(&ein, 192, 208).len(), 95);
    }
}
