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

function melde(kind, w) {
    var uuid = "", caption = "", cls = "", desktop = "";
    var full = false, x = 0, y = 0, breite = 0, hoehe = 0, pid = 0;
    if (w) {
        uuid    = String(w.internalId || "");
        caption = String(w.caption || "");
        cls     = String(w.resourceClass || "");
        desktop = String(w.desktopFileName || "");
        full    = !!w.fullScreen;
        pid     = Number(w.pid || 0);
        // frameGeometry ist die Anreicherung, die das VRAM-Gate braucht:
        // ein Vollbildfenster auf dem Ausgabegeraet des Overlays heisst,
        // dass das Overlay verdeckt sein koennte.
        var g = w.frameGeometry;
        if (g) { x = g.x; y = g.y; breite = g.width; hoehe = g.height; }
    }
    callDBus(SERVICE, PATH, IFACE, "Event",
             String(kind), uuid, caption, cls, desktop,
             full, pid, x, y, breite, hoehe);
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
