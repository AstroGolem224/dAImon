#!/usr/bin/env python3
"""Aggregiert raw_<app>.json zu results.json (Schema laut Arbeitsauftrag)."""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

apps = []
for path in sorted(glob.glob(os.path.join(HERE, "raw_*.json"))):
    d = json.load(open(path))
    stats = d.get("stats") or {}
    top = d.get("top_level_timing") or {}
    full = d.get("full_tree_timing") or {}
    apps.append({
        "app": d["app"],
        "actions_found": stats.get("with_action", 0),
        "activation_worked": bool(d.get("action", {}).get("worked")),
        "tree_query_ms": {
            "top_level": {"n": top.get("n"), "p50": top.get("p50_ms"),
                          "p95": top.get("p95_ms")},
            "full_tree": {"n": full.get("n"), "p50": full.get("p50_ms"),
                          "p95": full.get("p95_ms")},
        },
    })

activations_ok = sum(1 for a in apps if a["activation_worked"])
n_apps = len(apps)
verdict = "pass" if (n_apps >= 4 and activations_ok >= 2) else "fail"

out = {
    "n_apps": n_apps,
    "activations_ok": activations_ok,
    "apps": apps,
    "notes": [
        "Qt-Apps (kate, dolphin, konsole) exportieren im Auslieferungszustand "
        "(toolkit-accessibility=false) KEINEN Baum; gemessen wurde mit "
        "QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 beim Start.",
        "Nach gsettings toolkit-accessibility=true exportieren auch bereits "
        "laufende Qt-Apps dynamisch; Zurücksetzen auf false entfernt die "
        "Bäume laufender Prozesse NICHT mehr (bis Neustart der App).",
        "GTK4 spricht AT-SPI nativ (gtk4demo via PyGObject); GTK3 "
        "(pavucontrol) funktioniert, da at-spi2-atk in at-spi2-core 2.60 "
        "aufgegangen ist (libatk-bridge vorhanden).",
        "pluma (MATE/GTK3) tauchte trotz toolkit-accessibility=true und "
        "org.mate.interface accessibility=true nicht im Baum auf; "
        "pavucontrol lieferte einen Baum, aber kein eindeutig harmloses "
        "Aktionsziel (49/78 Knoten ohne Namen).",
        "Aktivierungsnachweise: bei kate/dolphin/konsole wurde 'Über KDE — "
        "<App>' per Action 'Press' geöffnet und der neue Dialog-Knoten im "
        "Baum verifiziert; bei gtk4demo 'Ansicht'-Menübutton per 'click', "
        "Popover-Knoten wurden neu SHOWING.",
        "DESIGN-ZUSAGE: Jede aus dem AT-SPI-Baum abgeleitete Bezeichnung "
        "ist tainted und muss durch die Vorschau des Auth-Agenten.",
    ],
    "verdict": verdict,
    "recommendation": (
        "AT-SPI2 kommt als Aktionsfläche in den Katalog, mit Einschränkungen: "
        "(1) Sichtbarkeit der Qt-Bäume muss erzwungen werden "
        "(QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 beim Start eigener Instanzen oder "
        "systemweiter A11y-Schalter) — nicht global erzwingbar für fremde, "
        "bereits laufende Apps. (2) Voller Baumwalk kostet bei KDE-Apps "
        "80–250 ms (p95); Top-Level ~1–2 ms — daher Baum cachen und nur "
        "Delta/Top-Level live abfragen. (3) 13–40 % der Knoten ohne Namen; "
        "Bezeichnungen sind tainted und gehören in die Auth-Vorschau. "
        "(4) GTK-Abdeckung ungleichmäßig: GTK4 nativ, GTK3 über Bridge, "
        "MATE-Apps (pluma) nicht erreichbar. (5) Nur Aktionsziele mit "
        "eindeutig harmlosem Label automatisiert auslösen; alles andere "
        "bleibt manuell freizugeben."
    ),
}

with open(os.path.join(HERE, "results.json"), "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print(json.dumps({"n_apps": n_apps, "activations_ok": activations_ok,
                  "verdict": verdict}, ensure_ascii=False))
