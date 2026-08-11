// T-0.12 — Fokus-Watcher fuer dAImon.
//
// REIN LESEND. Dieses Script ruft nichts auf, was einen Fensterzustand
// aendert -- kein setFullScreen, kein close, kein activate. Es laeuft im
// Compositor-Prozess; ein Fehler hier legt den Desktop lahm, und ein
// schreibender Aufruf waere eine Fernsteuerung, die niemand beauftragt hat.
//
// callDBus ist FIRE-AND-FORGET: ist der Empfaenger tot, verschluckt KWin den
// Aufruf ohne Fehler. Deshalb ist der Empfaenger in T-0.12 eine
// systemd-Unit mit Type=dbus und BusName= -- systemd startet sie bei Bedarf,
// und der Aufruf trifft auf einen lebenden Dienst statt ins Leere zu gehen.
//
// Der Abtast-Timer aus T-5.4 bleibt trotzdem noetig: captionChanged feuert
// nur, wenn die Anwendung ihren Titel aendert. Terminalausgabe, Scrollen und
// ein neuer Absatz erzeugen nichts. Das ist der Befund aus Spike T-1.9 und
// keine Nachlaessigkeit hier.

var SERVICE = "de.daimon.Focus";
var PATH = "/Focus";
var IFACE = "de.daimon.Focus";

// Die Nutzlast ist EIN JSON-String, nicht elf Einzelwerte. Grund, gemessen:
// KWin kappt callDBus bei 13 Argumenten ("Too many arguments, ignoring 2") --
// die frueheren 15 Argumente kamen als 9 an, die Signatur passte nie, und die
// Meldung erreichte den Hub nie. Ein neues Feld verlaengert jetzt den String
// statt der Argumentliste; die Grenze ist strukturell nicht mehr erreichbar.
function melde(kind, w) {
    var d = {
        kind: String(kind), uuid: "", caption: "", cls: "", desktop: "",
        fullscreen: false, pid: 0, x: 0, y: 0, breite: 0, hoehe: 0
    };
    if (w) {
        d.uuid       = String(w.internalId || "");
        d.caption    = String(w.caption || "");
        d.cls        = String(w.resourceClass || "");
        d.desktop    = String(w.desktopFileName || "");
        d.fullscreen = !!w.fullScreen;
        d.pid        = Number(w.pid || 0);
        // frameGeometry ist die Anreicherung, die das VRAM-Gate braucht:
        // ein Vollbildfenster auf dem Ausgabegeraet des Overlays heisst,
        // dass das Overlay verdeckt sein koennte.
        var g = w.frameGeometry;
        if (g) { d.x = g.x; d.y = g.y; d.breite = g.width; d.hoehe = g.height; }
    }
    callDBus(SERVICE, PATH, IFACE, "Event", JSON.stringify(d));
}

var verdrahtet = {};

function verdrahteCaption(w) {
    if (!w) return;
    var id = String(w.internalId);
    if (verdrahtet[id]) return;
    verdrahtet[id] = true;
    w.captionChanged.connect(function () {
        if (workspace.activeWindow &&
            String(workspace.activeWindow.internalId) === id) {
            melde("caption", w);
        }
    });
}

workspace.windowActivated.connect(function (w) {
    melde("activated", w);
    verdrahteCaption(w);
});

// Beim Laden vorhandene Fenster erfassen, damit captionChanged auch fuer
// bereits offene Fenster haengt. Ohne das bliebe alles stumm, was vor dem
// Start des Watchers geoeffnet wurde -- also nach einem kwin --replace
// praktisch der ganze Desktop.
var liste = workspace.windowList();
for (var i = 0; i < liste.length; i++) {
    if (liste[i] && liste[i].normalWindow) verdrahteCaption(liste[i]);
}

melde("loaded", workspace.activeWindow);
