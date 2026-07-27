// Spike T-1.9 — misst, ob das KWin-Fokussignal die Gatterkette tragen kann.
// Meldet per callDBus an de.daimon.FocusProbe. Rein lesend.

function send(kind, w) {
    var uuid = "", caption = "", cls = "", desk = "", full = false;
    if (w) {
        uuid    = String(w.internalId || "");
        caption = String(w.caption || "");
        cls     = String(w.resourceClass || "");
        desk    = String(w.desktopFileName || "");
        full    = !!w.fullScreen;
    }
    callDBus("de.daimon.FocusProbe", "/Probe", "de.daimon.FocusProbe", "Event",
             String(kind), uuid, caption, cls, desk, full);
}

var wired = {};

function wireCaption(w) {
    if (!w) return;
    var id = String(w.internalId);
    if (wired[id]) return;
    wired[id] = true;
    // Genau die Frage aus T-1.9: feuert das bei Inhaltsaenderung IM Fenster?
    w.captionChanged.connect(function () {
        if (workspace.activeWindow && String(workspace.activeWindow.internalId) === id) {
            send("caption", w);
        }
    });
}

workspace.windowActivated.connect(function (w) {
    send("activated", w);
    wireCaption(w);
});

// Beim Laden vorhandene Fenster erfassen, damit captionChanged auch fuer
// bereits offene Fenster haengt.
var list = workspace.windowList();
for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].normalWindow) wireCaption(list[i]);
}
send("loaded", workspace.activeWindow);
