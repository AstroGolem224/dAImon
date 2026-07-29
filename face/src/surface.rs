//! Sichere Basissurface fuer das Overlay.
//!
//! Hier liegen alle `commit()`-Aufrufe. Dadurch gibt es keinen bequemen
//! Nebenweg an `InputRegion::darf_committen()` vorbei.

use smithay_client_toolkit::{
    compositor::{CompositorState, Region, SurfaceData},
    shell::{
        wlr_layer::{Anchor, KeyboardInteractivity, Layer, LayerShell, LayerSurface},
        WaylandSurface,
    },
    shm::slot::SlotPool,
};
use wayland_client::{
    protocol::{wl_output, wl_shm},
    QueueHandle,
};

use crate::input::{Box2D, InputRegion};

pub struct OverlaySurface {
    layer: LayerSurface,
    input_region: InputRegion,
    puffer_committiert: bool,
}

impl OverlaySurface {
    pub fn neu<State>(
        compositor: &CompositorState,
        layer_shell: &LayerShell,
        qh: &QueueHandle<State>,
        output: &wl_output::WlOutput,
        gewuenschte_input_region: Option<Box2D>,
    ) -> Self
    where
        State: wayland_client::Dispatch<
                wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::ZwlrLayerSurfaceV1,
                smithay_client_toolkit::shell::wlr_layer::LayerSurfaceData,
            > + wayland_client::Dispatch<
                wayland_client::protocol::wl_surface::WlSurface,
                SurfaceData<()>,
            > + 'static,
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

        let mut ergebnis = Self {
            layer,
            input_region: InputRegion::new(),
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
            .anwenden(compositor, &ergebnis.layer, gewuenschte_input_region);
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
