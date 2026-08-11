//! T-2.5 — Auswahl des `wl_output`, an den die Layer-Surface gebunden wird.
//!
//! Die Layer-Surface bekommt **immer** einen benannten Output. `None` wuerde
//! den Compositor waehlen lassen; welchen, ist nicht zugesagt und nach einem
//! Monitorwechsel nicht mehr dieselbe Zuordnung.
//!
//! Auswahlregel, bewusst kurz gehalten: `DAIMON_FACE_OUTPUT=<name>` gewinnt,
//! wenn es einen Output dieses Namens gibt. Sonst der **erste** verfuegbare.
//! Ein unbekannter Name ist kein Abbruch — ein Pet, das wegen eines Tippfehlers
//! gar nicht erscheint, waere die schlechtere Fehlerrichtung.

use smithay_client_toolkit::output::OutputState;
use wayland_client::protocol::wl_output;

/// Der vom Compositor gemeldete Name, z.B. `HDMI-A-1`. Leer, wenn der
/// Compositor keinen liefert (`wl_output` vor Version 4).
pub fn name(output_state: &OutputState, output: &wl_output::WlOutput) -> String {
    output_state
        .info(output)
        .and_then(|info| info.name)
        .unwrap_or_default()
}

/// Reine Auswahllogik, ohne Wayland. `None` nur bei leerer Liste.
pub fn index_waehlen(namen: &[String], wunsch: Option<&str>) -> Option<usize> {
    wunsch
        .and_then(|wunsch| namen.iter().position(|name| name == wunsch))
        .or_else(|| (!namen.is_empty()).then_some(0))
}

/// Waehlt den zu bindenden Output. `ausser` schliesst den gerade verlorenen
/// Output aus — beim Removal steht er je nach Ereignisreihenfolge noch in der
/// Registry.
///
/// Rueckgabe `None` heisst: es gibt keinen Output mehr.
pub fn waehlen(
    output_state: &OutputState,
    wunsch: Option<&str>,
    ausser: Option<&wl_output::WlOutput>,
) -> Option<(wl_output::WlOutput, String)> {
    let outputs: Vec<_> = output_state
        .outputs()
        .filter(|output| Some(output) != ausser)
        .collect();
    let namen: Vec<String> = outputs
        .iter()
        .map(|output| name(output_state, output))
        .collect();
    let index = index_waehlen(&namen, wunsch)?;
    Some((outputs[index].clone(), namen[index].clone()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn namen(werte: &[&str]) -> Vec<String> {
        werte.iter().map(|w| (*w).to_string()).collect()
    }

    #[test]
    fn gewuenschter_name_gewinnt_unabhaengig_von_der_reihenfolge() {
        let namen = namen(&["DP-1", "HDMI-A-1", "DP-2"]);
        assert_eq!(index_waehlen(&namen, Some("HDMI-A-1")), Some(1));
        assert_eq!(index_waehlen(&namen, Some("DP-2")), Some(2));
    }

    #[test]
    fn unbekannter_oder_fehlender_name_faellt_auf_den_ersten_zurueck() {
        let namen = namen(&["DP-1", "HDMI-A-1"]);
        // Der Tippfehler darf das Pet nicht verschwinden lassen.
        assert_eq!(index_waehlen(&namen, Some("HDMI-A-9")), Some(0));
        assert_eq!(index_waehlen(&namen, None), Some(0));
        assert_eq!(index_waehlen(&namen, Some("")), Some(0));
    }

    #[test]
    fn ohne_output_gibt_es_keine_auswahl() {
        assert_eq!(index_waehlen(&[], Some("HDMI-A-1")), None);
        assert_eq!(index_waehlen(&[], None), None);
    }
}
