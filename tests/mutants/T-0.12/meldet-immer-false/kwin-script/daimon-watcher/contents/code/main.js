// T-0.12: Fokus-Watcher fuer KWin.
//
// MUTANT: meldet zuverlaessig -- aber immer 'kein Vollbild'.
//
// REIN LESEND. Das Script fragt Fenster ab und meldet; es bewegt, schliesst
// und veraendert nichts.
//
// EIN JSON-STRING STATT ELF WERTEN. `callDBus` nimmt vier feste Argumente
// (Dienst, Pfad, Schnittstelle, Methode) und danach die Nutzlast. KWin kappt
// die GESAMTE Argumentliste bei 13 und protokolliert "Too many arguments,
// ignoring N" -- die Meldung erreicht den Hub dann nie, und zwar lautlos.
// Elf Einzelwerte waren 15 Argumente und genau dieser Fehler. Mit einem
// einzigen JSON-String sind es fuenf, und ein neues Feld verlaengert den
// String statt die Argumentliste: die Grenze ist strukturell nicht mehr
// erreichbar.
var SERVICE = "de.daimon.Focus";
var PFAD = "/Focus";
var IFACE = "de.daimon.Focus";

function text(x) {
    return (x === undefined || x === null) ? "" : ("" + x);
}

function zahl(x) {
    var n = Math.round(Number(x));
    return isFinite(n) ? n : 0;
}

function melde(w, anlass) {
    if (!w) { return; }
    var g = w.frameGeometry ? w.frameGeometry : {};
    var nutzlast = {
        anlass: text(anlass),
        caption: text(w.caption),
        titel: text(w.caption),
        klasse: text(w.resourceClass),
        name: text(w.resourceName),
        rolle: text(w.windowRole),
        pid: zahl(w.pid),
        // MUTANT: der Vollbildzustand wird nicht gelesen, sondern
        // behauptet. Das Script MELDET weiter -- puenktlich, vollstaendig,
        // mit richtiger Geometrie und richtigem Titel --, es sagt nur immer
        // "kein Vollbild".
        fullscreen: false,
        vollbild: false,
        full_screen: false,

        x: zahl(g.x),
        y: zahl(g.y),
        breite: zahl(g.width),
        hoehe: zahl(g.height),
        desktop: 0
    };
    callDBus(SERVICE, PFAD, IFACE, "Event", JSON.stringify(nutzlast));

}

workspace.windowActivated.connect(function (w) { melde(w, "activated"); });

if (workspace.windowList) {
    var offen = workspace.windowList();
    for (var i = 0; i < offen.length; i++) {
        if (offen[i] && offen[i].active) { melde(offen[i], "initial"); }
    }
}
