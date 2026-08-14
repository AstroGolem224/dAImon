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
    bubble::{position_klemmen, Raster as BubbleRaster},
    input::{sichtbare_laeufe, Box2D, InputRegion},
    render::{frame_toenen, Toenung},
    sprite::{indikator_malen, mitschnitt_malen, zustand_abbilden, SpriteAtlas},
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
    fn fuer_frame(
        &mut self,
        frame_koordinaten: (u32, u32),
        frame: &[u8],
        breite: u32,
        hoehe: u32,
    ) -> &[Box2D] {
        self.laeufe.entry(frame_koordinaten).or_insert_with(|| {
            #[cfg(test)]
            {
                self.berechnungen += 1;
            }
            sichtbare_laeufe(frame, breite, hoehe)
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
    /// Rueckgabewert ist die Anzahl tatsaechlicher Wayland-Commits.
    pub fn sprite_committen(
        &mut self,
        compositor: &CompositorState,
        pool: &mut SlotPool,
        atlas: &SpriteAtlas,
        zustand: &str,
        voice: &str,
        mitschnitt: bool,
        toenung: Toenung,
        sichtbar: bool,
        qh: &QueueHandle<crate::App>,
        callback_armieren: bool,
    ) -> Result<(u64, bool), String> {
        let abbildung = zustand_abbilden(zustand, &atlas.layout);
        let mut frame = sichtbaren_frame_bauen(
            &frame_toenen(&atlas.frame(abbildung.zeile, 0)?, toenung),
            sichtbar,
        );
        // T-3.14: der Indikator gehoert in das BILD, nicht in die
        // Eingabemaske. Waere er in beidem, waechse die Klickflaeche des Pets
        // mit dem Sprachzustand -- und das Ziehen aus T-2.4 haette je nach
        // Zustand eine andere Trefferflaeche. Deshalb hier eine Kopie des
        // Standes davor, und zwar nur, wenn ueberhaupt gemalt wird.
        let ohne_indikator =
            (sichtbar && (voice != "idle" || mitschnitt)).then(|| frame.clone());
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
        let _ = mitschnitt_gemalt;
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
            return Ok((0, indikator_gemalt));
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

        let frame_koordinaten = (abbildung.zeile, 0);
        let input_laeufe = if sichtbar {
            self.sprite
                .input_laeufe
                .fuer_frame(
                    frame_koordinaten,
                    ohne_indikator.as_deref().unwrap_or(&frame),
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
        Ok((1, indikator_gemalt))
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
        canvas.copy_from_slice(&raster.pixel);

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

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn verschieben_berechnet_sprite_zeilenlaeufe_nicht_neu() {
        let mut cache = InputLaufCache::default();
        let frame = [0, 0, 0, 255, 0, 0, 0, 0];
        assert_eq!(cache.fuer_frame((0, 0), &frame, 2, 1).len(), 1);
        // Eine andere Parent-Position aendert weder Frame-Schluessel noch
        // lokale Sprite-Region; der zweite Zugriff muss ein Cache-Treffer sein.
        assert_eq!(
            laeufe_versetzen(cache.fuer_frame((0, 0), &frame, 2, 1), (200, 100))[0],
            Box2D {
                x: 200,
                y: 100,
                w: 1,
                h: 1
            }
        );
        assert_eq!(cache.fuer_frame((0, 0), &frame, 2, 1).len(), 1);
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
