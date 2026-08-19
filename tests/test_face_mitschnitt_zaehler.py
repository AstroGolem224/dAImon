"""Der Mitschnitt-Punkt wird gezaehlt -- nicht nur gemalt.

BEFUND T-7.3 K9, gemessen von der Reviewer-Sitzung am 18.08.:

    `surface.rs` verwirft das Ergebnis von `mitschnitt_malen()` mit
    `let _ = ...`; der Kommentar daneben behauptet getrennte Zaehlung.

Warum das mehr ist als ein fehlender Zaehler: der Sprachindikator hat einen
(`voice_indikator_gezeichnet`), und sein Kommentar sagt, wozu -- "ohne diesen
Zaehler waere 'das Face zeigt den Sprachzustand an' eine Selbstauskunft".
Genau dieser Beleg fehlte bei der Anzeige, die rechtlich zaehlt: dass
aufgezeichnet wird. Ein Verifizierer konnte nicht unterscheiden, ob der Punkt
gemalt wurde oder ob das Face es nur behauptet.

**Warum diese Pruefungen in Python stehen.** Die Rust-Tests erreichen
`main.rs` nicht -- dort laeuft die Wayland-Schleife. Der Compiler erzwingt
seit dem 19.08. immerhin, dass der Wert bis dorthin GEREICHT wird (das Tupel
hat drei Glieder); dass er dort auch ANKOMMT statt in einem `_` zu enden,
erzwingt er nicht. Das ist der Riss, den diese Datei bewacht.

Der Zaehler selbst und seine JSON-Ausgabe stehen in `face/src/diag.rs`
(`mitschnitt_indikator_steht_wortwoertlich_im_json`).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SURFACE = REPO / "face" / "src" / "surface.rs"
MAIN = REPO / "face" / "src" / "main.rs"
DIAG = REPO / "face" / "src" / "diag.rs"


def _quelle(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8")


def _ohne_kommentare(text: str) -> str:
    """Zeilenkommentare weg, bevor irgendwas gesucht wird.

    Am 18.08. hat ein Waechter dieses Projekts seinen eigenen Kommentar
    getroffen und deshalb nichts bewacht. Der Befund steht als Kommentar in
    genau diesen Dateien -- eine Textsuche ohne diesen Schritt faende ihn und
    meldete Ruhe.
    """
    return "\n".join(z for z in text.splitlines()
                     if not z.lstrip().startswith("//"))


def test_der_wert_wird_nicht_mehr_verworfen():
    """DER BEFUND in einer Zeile."""
    code = _ohne_kommentare(_quelle(SURFACE))
    verworfen = re.findall(r"let\s+_\s*=\s*mitschnitt\w*", code)
    assert not verworfen, (
        f"surface.rs verwirft den Mitschnitt-Indikator wieder: {verworfen}")


def test_beide_indikatoren_verlassen_surface_gemeinsam():
    """Ein Rueckgabewert je Indikator. Waere es einer, liesse sich "ich hoere
    zu" nicht von "das wird aufbewahrt" unterscheiden -- und die Trennung ist
    der ganze Grund, warum es zwei Punkte sind."""
    code = _ohne_kommentare(_quelle(SURFACE))
    assert "-> Result<(u64, bool, bool), String>" in code, (
        "sprite_committen gibt nicht mehr beide Indikatoren zurueck")
    for rueckgabe in ("Ok((0, indikator_gemalt, mitschnitt_gemalt))",
                      "Ok((1, indikator_gemalt, mitschnitt_gemalt))"):
        assert rueckgabe in code, f"fehlt: {rueckgabe}"


def test_jede_zaehlstelle_zaehlt_BEIDE():
    """DER ZULAUF. Der Compiler erzwingt, dass der Wert ankommt -- nicht,
    dass er benutzt wird. `Ok((commits, indikator, _))` waere derselbe Befund
    eine Station weiter.

    Gezaehlt wird paarweise: es gibt zwei Renderpfade in `main.rs` (der
    laufende und der beim ersten `configure`), und beide brauchen beide
    Zaehler. Vor dem 19.08. hatte der Mitschnitt-Punkt null davon.
    """
    code = _ohne_kommentare(_quelle(MAIN))
    voice = code.count("self.indikator_zaehlen(")
    mitschnitt = code.count("self.mitschnitt_indikator_zaehlen(")
    assert voice >= 2, ("die Sprachzaehlung ist weg -- dann misst dieser "
                        "Vergleich nichts mehr", voice)
    assert mitschnitt == voice, (
        f"{voice} Stellen zaehlen den Sprachindikator, aber {mitschnitt} "
        "den Mitschnitt-Indikator (T-7.3 K9)")
    assert "_)) =>" not in code and ", _))" not in code, (
        "ein Renderergebnis wird stellenweise verworfen")


def test_der_zaehler_geht_auch_hinaus():
    """Ein Zaehler, den die Diagnose nicht ausgibt, ist fuer den Verifizierer
    nicht da -- dasselbe Muster, eine Station weiter."""
    code = _quelle(DIAG)
    assert "pub mitschnitt_indikator_gezeichnet: u64," in code
    assert '\\"mitschnitt_indikator_gezeichnet\\":{}' in code, (
        "das Feld steht nicht im JSON der Diagnose")
    assert "mitschnitt_indikator_gezeichnet: 0," in code, (
        "Default setzt das Feld nicht -- FaceState wird von Hand gebaut")


def test_die_zwei_zaehler_sind_verschiedene_felder():
    """Die Positivkontrolle gegen die bequeme Fassung: EIN Feld, zweimal
    hochgezaehlt. Die haette jede Aussage oben ebenfalls bestanden und die
    Unterscheidung still aufgegeben."""
    code = _quelle(MAIN)
    assert "zustand.voice_indikator_gezeichnet += 1;" in code
    assert "zustand.mitschnitt_indikator_gezeichnet += 1;" in code
