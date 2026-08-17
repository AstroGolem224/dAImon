"""T-5.3 -- Frames abholen, ohne dauernd dafuer zu bezahlen.

Die Kette ist `pipewiresrc ! videoconvert ! videorate ! appsink`. Kurz genug,
dass man jedes Glied begruenden muss:

**`videoconvert` bleibt drin.** Nicht aus Bequemlichkeit -- KDE-Bug 476602:
xdg-desktop-portal-kde liefert Formate aus, die `appsink` ohne Umwandlung
nicht annimmt. Wer das Glied streicht, bekommt eine Aushandlung, die
scheitert, und keine Meldung, die das sagt.

**`videorate drop-only=true` ist noetig, ein Caps-Deckel reicht NICHT.** Am
12.08. gemessen, drei Varianten gegen dieselbe Quelle:

    framerate=[0/1,5/1]                    -> ausgehandelt 0/1 (variabel)
    framerate=5/1                          -> KEIN FRAME
    videorate drop-only=true ! 1/1         -> ausgehandelt 1/1

Der Bereich ist wirkungslos, weil `0/1` darin liegt und „variabel" bedeutet;
ohne `videorate` liefen 5 Frames in 0,14 s durch, also rund 35 fps. Ein fester
Wert direkt an der Quelle scheitert dagegen ganz. `drop-only` sagt, dass
Frames verworfen und nicht verdoppelt werden -- Duplikate waeren Arbeit ohne
Erkenntnis.

**`max-buffers=1 drop=true`.** Ein Bildschirm mit 5120x1440 ist gut 22 MB je
Frame. Ohne Deckel fuellt sich die Warteschlange, waehrend niemand abholt, und
der Speicherbedarf waechst mit der Zeit statt mit der Arbeit. Der neueste
Frame ist ausserdem der einzige, der interessiert: ein Bildschirm von vor zwei
Sekunden ist keine Beobachtung, sondern eine Erinnerung.

**Das Ziel ist die `object.serial`, nicht die Node-ID.** Node-IDs werden nach
einem Hotplug wiederverwendet. Wer sie festhaelt, filmt nach dem Aus- und
Einschalten eines Ausgangs unter Umstaenden einen anderen Bildschirm als den,
den der Nutzer freigegeben hat -- und merkt es nicht, weil weiter Bilder
kommen. Die Serial ist innerhalb einer PipeWire-Sitzung eindeutig und wird
nicht neu vergeben.

**Der Ruhezustand ist `NULL`, nicht `PAUSED`.** Die Akzeptanzliste nennt
`PAUSED` „schlechter" als ein `INACTIVE`-Stream. Gemessen ist `PAUSED` nicht
schlechter, sondern unbrauchbar -- eine Einbahnstrasse:

    dauerhaft PLAYING              5 Frames in 0,14 s
    PLAYING -> PAUSED -> PLAYING   3x nichts, je 5 s Frist

`pipewiresrc` bietet kein `set_active()`. Der Weg, der beides erfuellt --
Compositor hoert ganz auf UND die Portal-Sitzung lebt weiter, also kein neuer
Dialog -- ist, die Kette je Frame aufzubauen und wieder abzubauen. Gemessen:
fuenf Zyklen, 34 bis 44 ms je Zyklus, kein Schwarzframe, Sitzung danach offen.
35 ms fuer einen Blick, der hoechstens jede Sekunde faellt, ist der Preis, zu
dem die Zeit dazwischen wirklich nichts kostet.
"""
from __future__ import annotations

from typing import Any

# Ein Bild je Sekunde. Wer auf Veraenderungen schaut, braucht nicht mehr, und
# jedes zusaetzliche Bild ist ein voller 22-MB-Puffer durch `videoconvert`.
FRAMERATE = 1

# Der Name, unter dem unser Lesestrom bei PipeWire erscheint. Ohne ihn heisst
# der Knoten wie der Prozess -- gemessen `python3.14` -- und ist von der
# Vorschau-Verrohrung der Arbeitsflaeche nicht zu unterscheiden. Der
# Kill-Switch (T-5.12) zaehlt genau diesen Namen; ein systemweiter Zaehler
# stuende dauerhaft auf "laeuft weiter", weil kwin_wayland und plasmashell
# immer je einen Videostrom halten.
KLIENT_NAME = "daimon-eyes"


def pipeline_beschreibung(*, serial: str | None = None,
                          node_id: int | None = None,
                          framerate: int = FRAMERATE) -> str:
    """Die Kette als Text. Getrennt vom Aufbau, damit sie pruefbar ist.

    Ohne diese Trennung liesse sich `max-buffers=1` nur gegen ein laufendes
    GStreamer und einen echten Bildschirm pruefen -- also gar nicht.
    """
    if serial:
        # `target-object` nimmt Name ODER Serial. Die Serial ueberlebt einen
        # Hotplug, die Node-ID nicht.
        ziel = f'target-object="{serial}"'
    elif node_id is not None:
        ziel = f"path={node_id}"
    else:
        raise ValueError("weder Serial noch Node-ID -- ohne Ziel kein Stream")

    return (
        f"pipewiresrc name=quelle client-name={KLIENT_NAME} {ziel} "
        # `always-copy=false` waere sparsamer, gibt aber Puffer weiter, die
        # PipeWire wieder einzieht. Der Frame gehoert uns erst nach der Kopie.
        "always-copy=true ! "
        "videoconvert ! "                       # KDE-Bug 476602, siehe oben
        # `drop-only`: verwerfen, nicht verdoppeln. Ein verdoppelter Frame
        # ist Arbeit ohne Erkenntnis.
        "videorate drop-only=true ! "
        f"video/x-raw,format=RGB,framerate={framerate}/1 ! "
        "appsink name=senke emit-signals=false max-buffers=1 drop=true sync=false"
    )


class AufnahmeFehler(RuntimeError):
    """Etwas an der Kette ist unbrauchbar. Immer mit Grund."""


def _gst():
    """GStreamer, oder eine Meldung, die sagt was fehlt und warum."""
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError) as exc:
        raise AufnahmeFehler(
            f"kein GStreamer: {exc}. `PyGObject` und die GStreamer-Plugins "
            "liegen im System-Python, nicht im venv -- wie beim Portal-Weg "
            "(T-5.2) muss der Augendienst unter /usr/bin/python3 laufen."
        ) from exc
    if not Gst.is_initialized():
        Gst.init(None)
    return Gst


def _bruch(struktur) -> str:
    """Die ausgehandelte Framerate als `n/d`, oder `unbekannt`.

    `get_value` scheitert an `GstFraction` mit „unknown type", auch wenn das
    Feld da ist. `get_fraction` ist der Weg.
    """
    ok, zaehler, nenner = struktur.get_fraction("framerate")
    return f"{zaehler}/{nenner}" if ok else "unbekannt"


class Aufnahme:
    """Ein Blick auf einen PipeWire-Knoten. Zwischen den Blicken: nichts.

    Es gibt bewusst kein `oeffnen()`/`schliessen()`. Eine Kette, die zwischen
    zwei Frames stehen bleibt, laesst den Compositor weiter liefern -- und das
    waere bei einem Dienst, der den ganzen Tag laeuft, die groesste einzelne
    Rechnung des Projekts. Der Deskriptor bleibt offen, die Portal-Sitzung
    auch; nur die Kette entsteht und vergeht je Frame.
    """

    def __init__(self, *, fd: int, serial: str | None = None,
                 node_id: int | None = None,
                 framerate: int = FRAMERATE) -> None:
        self.beschreibung = pipeline_beschreibung(
            serial=serial, node_id=node_id, framerate=framerate)
        self._fd = fd

    def frame(self, frist_s: float = 8.0) -> dict[str, Any]:
        """Kette aufbauen, EINEN Frame holen, Kette abbauen. Rund 35 ms.

        Der Abbau steht im `finally`: bliebe die Kette nach einem Fehlschlag
        stehen, liefe genau der Dauerverbrauch weiter, den dieses Modul
        vermeiden soll -- und niemand saehe es, weil keine Frames mehr kommen.
        """
        Gst = _gst()
        pipeline = Gst.parse_launch(self.beschreibung)
        pipeline.get_by_name("quelle").set_property("fd", self._fd)
        senke = pipeline.get_by_name("senke")
        try:
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise AufnahmeFehler("Kette liess sich nicht nach PLAYING bringen")
            probe = senke.emit("try-pull-sample", int(frist_s * Gst.SECOND))
            if probe is None:
                raise AufnahmeFehler(f"kein Frame binnen {frist_s:.1f} s")
            return _auslesen(Gst, probe)
        finally:
            pipeline.set_state(Gst.State.NULL)


def _auslesen(Gst, probe) -> dict[str, Any]:
    puffer = probe.get_buffer()
    struktur = probe.get_caps().get_structure(0)
    ok, karte = puffer.map(Gst.MapFlags.READ)
    if not ok:
        raise AufnahmeFehler("Puffer liess sich nicht lesen")
    try:
        rohdaten = bytes(karte.data)
    finally:
        puffer.unmap(karte)
    return {
        "breite": struktur.get_value("width"),
        "hoehe": struktur.get_value("height"),
        "framerate": _bruch(struktur),
        "bytes": len(rohdaten),
        # Die Summe entscheidet, ob der Frame schwarz ist. Ein Schwarzframe
        # ist der Fehlerfall aus T-5.2 (DmaBuf ohne GPU-Kontext) und sieht
        # ohne diese Zahl aus wie ein Erfolg.
        "summe": sum(rohdaten[::997]),
        "rohdaten": rohdaten,
    }
