//! Sichere Basissurface fuer das Overlay.
//!
//! Hier liegen alle `commit()`-Aufrufe. Dadurch gibt es keinen bequemen
//! Nebenweg an `InputRegion::darf_committen()` vorbei.

use std::collections::HashMap;

use smithay_client_toolkit::{
    compositor::{CompositorState, Region, SurfaceData},
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
    input::{sichtbare_laeufe, Box2D, InputRegion},
    sprite::{zustand_abbilden, SpriteAtlas},
};

struct SpriteSurface {
    subsurface: wl_subsurface::WlSubsurface,
    surface: wl_surface::WlSurface,
    input_region: InputRegion,
    input_laeufe: HashMap<(u32, u32), Vec<Box2D>>,
    position: (i32, i32),
}

pub struct OverlaySurface {
    layer: LayerSurface,
    input_region: InputRegion,
    sprite: SpriteSurface,
    puffer_committiert: bool,
}

impl OverlaySurface {
    pub fn neu<State>(
        compositor: &CompositorState,
        subcompositor: &SubcompositorState,
        layer_shell: &LayerShell,
        qh: &QueueHandle<State>,
        output: &wl_output::WlOutput,
        sprite_position: (i32, i32),
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

        let mut ergebnis = Self {
            layer,
            input_region: InputRegion::new(),
            sprite: SpriteSurface {
                subsurface,
                surface: sprite_surface,
                input_region: InputRegion::new(),
                input_laeufe: HashMap::new(),
                position: sprite_position,
            },
            puffer_committiert: false,
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
    ) -> Result<u64, String> {
        let abbildung = zustand_abbilden(zustand, &atlas.layout);
        let frame = atlas.frame(abbildung.zeile, 0)?;
        let breite = i32::try_from(atlas.layout.cell_w)
            .map_err(|_| "Sprite-Breite passt nicht in i32".to_string())?;
        let hoehe = i32::try_from(atlas.layout.cell_h)
            .map_err(|_| "Sprite-Hoehe passt nicht in i32".to_string())?;
        let stride = breite
            .checked_mul(4)
            .ok_or_else(|| "Sprite-Stride ist zu gross".to_string())?;

        let bbox = Box2D {
            x: self.sprite.position.0,
            y: self.sprite.position.1,
            w: breite,
            h: hoehe,
        };
        // Erst alle falliblen Pufferschritte abschliessen. So kann danach kein
        // bereits erfolgter Input-Commit in einem Err-Rueckgabepfad verloren
        // gehen.
        let (buffer, canvas) = pool
            .create_buffer(breite, hoehe, stride, wl_shm::Format::Argb8888)
            .map_err(|fehler| format!("wl_shm-Sprite-Puffer: {fehler}"))?;
        canvas.copy_from_slice(&frame);
        self.sprite.surface.damage_buffer(0, 0, breite, hoehe);
        buffer
            .attach_to(&self.sprite.surface)
            .map_err(|fehler| format!("wl_shm-Sprite anhaengen: {fehler}"))?;

        if self
            .input_region
            .anwenden(compositor, self.layer.wl_surface(), Some(bbox))
            && !self.sicher_committen()
        {
            return Err("Input-Region konnte nicht sicher committet werden".into());
        }

        let frame_koordinaten = (abbildung.zeile, 0);
        let input_laeufe = self
            .sprite
            .input_laeufe
            .entry(frame_koordinaten)
            .or_insert_with(|| sichtbare_laeufe(&frame, atlas.layout.cell_w, atlas.layout.cell_h));
        self.sprite
            .input_region
            .laeufe_anwenden(compositor, &self.sprite.surface, input_laeufe);
        if !self.sprite.input_region.darf_committen() {
            eprintln!("SICHERHEITSABBRUCH: Sprite-Subsurface ohne gesetzte Input-Region");
            return Err("Sprite-Commit durch eigenes Input-Gate abgelehnt".into());
        }
        self.sprite.surface.commit();
        Ok(1)
    }

    /// Verschiebt das Pet ohne Buffer-Aenderung. Nur die Position und die
    /// Input-Bounding-Box werden auf dem Parent angewandt.
    #[allow(dead_code)]
    pub fn sprite_position_setzen(
        &mut self,
        compositor: &CompositorState,
        position: (i32, i32),
        groesse: (i32, i32),
    ) -> bool {
        if self.sprite.position == position {
            return false;
        }
        self.sprite.position = position;
        self.sprite.subsurface.set_position(position.0, position.1);
        self.input_region.anwenden(
            compositor,
            self.layer.wl_surface(),
            Some(Box2D {
                x: position.0,
                y: position.1,
                w: groesse.0,
                h: groesse.1,
            }),
        );
        self.sicher_committen()
    }

    /// KDE-Bug 503121: Nach einem NULL-Buffer-Unmap liefert KWin ohne diese
    /// Wiederholung kein neues configure. Im Spike waren es 0/20 ohne und
    /// 20/20 mit erneut gesetzten Layer-Properties.
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
