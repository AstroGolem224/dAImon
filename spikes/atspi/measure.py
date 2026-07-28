#!/usr/bin/env python3
"""
Spike T-1.11: AT-SPI2 als Aktionsfläche — Messung je Anwendung.

Startet eine Anwendung (mit Wegwerf-Datei in frischem mktemp-Verzeichnis),
wartet auf ihren AT-SPI2-Baum, misst:
  - Knotenzahl, Anteil mit Action-Interface, fehlende Namen
  - Kosten Top-Level-Abfrage und voller Baumwalk (n Iterationen, p50/p95)
Aktiviert genau EINE harmlose Aktion (Menü aufklappen) und verifiziert
eine beobachtbare Zustandsänderung. Beendet die App in jedem Fall.

Watchdog: hartes Limit HARD_TIMEOUT Sekunden, dann terminate+kill.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

HARD_TIMEOUT = 60          # Sekunden, Watchdog
N_ITERS = 25               # Iterationen je Timing-Messung (>= 20 gefordert)
WAIT_FOR_TREE = 20         # max. Sekunden bis App im Baum auftaucht

# Apps: launch-Befehl, ob Qt-Flag gesetzt wird, Datei-Argument ja/nein,
# Erwarteter Baumname (für Matching)
APPS = {
    "dolphin":     {"cmd": ["dolphin", "--new-window"], "qt_flag": True,  "with_file": True},
    "kate":        {"cmd": ["kate"],                    "qt_flag": True,  "with_file": True},
    "konsole":     {"cmd": ["konsole"],                 "qt_flag": True,  "with_file": False},
    "pluma":       {"cmd": ["pluma"],                   "qt_flag": False, "with_file": True},
    "pavucontrol": {"cmd": ["pavucontrol"],             "qt_flag": False, "with_file": False},
    "gtk4demo":    {"cmd": [sys.executable,
                            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "gtk4_demo_app.py")],
                    "qt_flag": False, "with_file": False, "match": "gtk4demo"},
}

# Erlaubte Aktionsziele: NUR Menüs/Menüeinträge, die ein Untermenü aufklappen.
# Substring-Match, case-insensitiv. Blacklist schlägt Allowlist.
ALLOW_SUBSTR = ["hilfe", "help", "ansicht", "view", "bearbeiten", "edit",
                "datei", "file", "lesezeichen", "bookmarks", "fenster", "window"]
DENY_SUBSTR = ["speicher", "save", "lösch", "delete", "entfern", "remove", "neu",
               "new", "quit", "beenden", "close", "schließ", "send", "druck",
               "print", "rename", "umbenenn", "move", "verschieb", "copy",
               "kopier", "cut", "ausschneid", "paste", "einfüg", "undo",
               "rückgängig", "redo", "wiederhol", "such", "find", "ersetz",
               "replace", "öffn", "open", "import", "export", "einstellungen",
               "settings", "preferences", "konfigur", "about", "über"]

ACTION_ROLES = {"menu", "menu item"}


def die_watchdog(signum, frame):
    raise TimeoutError("Watchdog: hartes Zeitlimit erreicht")


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def find_app_in_tree(desktop, proc_pid, app_id, match=None):
    """App-Accessible anhand PID oder Name finden. Bevorzugt gestartete PID."""
    needle = (match or app_id).lower()
    candidates = []
    for i in range(safe(lambda: desktop.get_child_count(), 0) or 0):
        app = safe(lambda i=i: desktop.get_child_at_index(i))
        if app is None:
            continue
        name = safe(app.get_name, "") or ""
        pid = safe(app.get_process_id, -1)
        children = safe(app.get_child_count, 0) or 0
        if children == 0:
            continue  # Hilfsprozesse ohne Fenster ignorieren
        if pid == proc_pid:
            return app, "pid"
        if needle in name.lower():
            candidates.append(app)
    if candidates:
        return candidates[0], "name"
    return None, None


def walk_tree(root):
    """Voller rekursiver Walk. Liefert Statistik-Dict."""
    stats = {"nodes": 0, "with_action": 0, "missing_name": 0,
             "roles": {}, "action_names": {}, "errors": 0}
    stack = [root]
    while stack:
        node = stack.pop()
        stats["nodes"] += 1
        name = safe(node.get_name)
        if not name:
            stats["missing_name"] += 1
        role = safe(node.get_role_name, "UNKNOWN") or "UNKNOWN"
        stats["roles"][role] = stats["roles"].get(role, 0) + 1
        act = safe(node.get_action_iface)
        if act is not None:
            n = safe(act.get_n_actions, 0) or 0
            if n > 0:
                stats["with_action"] += 1
                for j in range(n):
                    an = safe(lambda j=j: act.get_action_name(j))
                    if an:
                        stats["action_names"][an] = stats["action_names"].get(an, 0) + 1
        nch = safe(node.get_child_count, 0) or 0
        for k in range(nch):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
            else:
                stats["errors"] += 1
    return stats


def top_level_probe(app):
    """Nur Wurzel-/Top-Level-Ebene abfragen."""
    out = []
    n = safe(app.get_child_count, 0) or 0
    for i in range(n):
        child = safe(lambda i=i: app.get_child_at_index(i))
        if child is None:
            continue
        out.append((safe(child.get_name, ""), safe(child.get_role_name, "")))
    return out


def time_queries(desktop, proc_pid, app_id, full, match=None):
    """n Iterationen; jede Iteration sucht die App frisch ab dem Desktop."""
    samples = []
    for _ in range(N_ITERS):
        t0 = time.perf_counter_ns()
        app, _ = find_app_in_tree(desktop, proc_pid, app_id, match)
        if app is not None:
            if full:
                walk_tree(app)
            else:
                top_level_probe(app)
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1e6)  # ms
        time.sleep(0.02)
    samples.sort()
    n = len(samples)
    return {
        "n": n,
        "p50_ms": round(samples[n // 2], 3),
        "p95_ms": round(samples[min(int(n * 0.95), n - 1)], 3),
        "min_ms": round(samples[0], 3),
        "max_ms": round(samples[-1], 3),
        "samples_ms": [round(s, 3) for s in samples],
    }


def action_candidates(root):
    """Sammelt (node, name, role, action_iface, n_actions) für erlaubte Ziele."""
    found = []
    stack = [root]
    while stack:
        node = stack.pop()
        role = (safe(node.get_role_name, "") or "").lower()
        name = safe(node.get_name, "") or ""
        act = safe(node.get_action_iface)
        if act is not None and role in ACTION_ROLES and name:
            low = name.lower()
            if any(d in low for d in DENY_SUBSTR):
                pass
            elif any(a in low for a in ALLOW_SUBSTR):
                n_act = safe(act.get_n_actions, 0) or 0
                if n_act > 0:
                    found.append((node, name, role, act, n_act))
        nch = safe(node.get_child_count, 0) or 0
        for k in range(nch):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
    # Bevorzuge Menü-Einträge MIT Untermenü (aufklappen = garantiert harmlos)
    def has_children(item):
        return (safe(item[0].get_child_count, 0) or 0) > 0
    found.sort(key=lambda it: (not has_children(it), it[1].lower()))
    return found


def expanded_somewhere(root):
    """True, wenn irgendein Knoten State EXPANDED oder SHOWING-Menü zeigt."""
    stack = [root]
    while stack:
        node = stack.pop()
        ss = safe(node.get_state_set)
        if ss is not None:
            if safe(lambda: ss.contains(Atspi.StateType.EXPANDED), False):
                return True
        nch = safe(node.get_child_count, 0) or 0
        for k in range(min(nch, 500)):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
    return False


def menu_open_evidence(app, node):
    """Belege, dass das Zielmenü geöffnet ist:
    - EXPANDED am Ziel oder irgendwo im Baum
    - Kind des Ziels (Submenü) mit SHOWING+VISIBLE
    """
    out = {"target_expanded": False, "child_showing": False,
           "expanded_anywhere": False}
    ss = safe(node.get_state_set)
    if ss is not None and safe(lambda: ss.contains(Atspi.StateType.EXPANDED), False):
        out["target_expanded"] = True
    child = safe(lambda: node.get_child_at_index(0))
    if child is not None:
        cs = safe(child.get_state_set)
        if cs is not None:
            showing = safe(lambda: cs.contains(Atspi.StateType.SHOWING), False)
            visible = safe(lambda: cs.contains(Atspi.StateType.VISIBLE), False)
            out["child_showing"] = bool(showing and visible)
    out["expanded_anywhere"] = expanded_somewhere(app)
    return out


def find_about_item(root):
    """Sucht einen 'Über …'/'About …'-Menüeintrag (explizit erlaubte Aktion).
    Liefert (node, name, role, action_iface, n_actions) oder None."""
    stack = [root]
    while stack:
        node = stack.pop()
        role = (safe(node.get_role_name, "") or "").lower()
        name = (safe(node.get_name, "") or "").strip()
        low = name.lower()
        if role == "menu item" and (low.startswith("über ") or
                                    low.startswith("about ")):
            act = safe(node.get_action_iface)
            n_act = safe(act.get_n_actions, 0) if act else 0
            if act is not None and n_act:
                return (node, name, role, act, n_act)
        nch = safe(node.get_child_count, 0) or 0
        for k in range(nch):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
    return None


DIALOG_ROLES = {"dialog", "frame", "window", "alert"}

BUTTON_ALLOW = ["ansicht", "view", "menü", "menu", "über", "about", "hilfe", "help"]


def find_harmless_button(root):
    """Button (push/toggle) mit harmlosem Namen und Action-Interface.
    Nur Labels aus BUTTON_ALLOW, keine Deny-Substrings. Liefert Tupel oder None."""
    stack = [root]
    while stack:
        node = stack.pop()
        role = (safe(node.get_role_name, "") or "").lower()
        name = (safe(node.get_name, "") or "").strip()
        low = name.lower()
        if role in ("push button", "toggle button") and name \
           and not any(d in low for d in DENY_SUBSTR) \
           and any(a in low for a in BUTTON_ALLOW):
            act = safe(node.get_action_iface)
            n_act = safe(act.get_n_actions, 0) if act else 0
            if act is not None and n_act:
                return (node, name, role, act, n_act)
        nch = safe(node.get_child_count, 0) or 0
        for k in range(nch):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
    return None


def showing_snapshot(root):
    """Menge {(name, role)} aller Knoten mit State SHOWING."""
    out = set()
    stack = [root]
    while stack:
        node = stack.pop()
        ss = safe(node.get_state_set)
        if ss is not None and safe(lambda: ss.contains(Atspi.StateType.SHOWING), False):
            out.add((safe(node.get_name, "") or "",
                     safe(node.get_role_name, "") or ""))
        nch = safe(node.get_child_count, 0) or 0
        for k in range(nch):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
    return out


def dialog_snapshot(app):
    """Top-Level-Kinder (name, role) + Anzahl dialogartiger Knoten im Baum."""
    tops = []
    for i in range(safe(app.get_child_count, 0) or 0):
        c = safe(lambda i=i: app.get_child_at_index(i))
        if c is not None:
            tops.append((safe(c.get_name, ""), safe(c.get_role_name, "")))
    n_dialogs = 0
    stack = [app]
    while stack:
        node = stack.pop()
        if (safe(node.get_role_name, "") or "").lower() in DIALOG_ROLES:
            n_dialogs += 1
        nch = safe(node.get_child_count, 0) or 0
        for k in range(nch):
            child = safe(lambda k=k: node.get_child_at_index(k))
            if child is not None:
                stack.append(child)
    return tops, n_dialogs


def main():
    app_id = sys.argv[1]
    cfg = APPS[app_id]

    signal.signal(signal.SIGALRM, die_watchdog)
    signal.alarm(HARD_TIMEOUT)

    Atspi.init()
    desktop = Atspi.get_desktop(0)

    tmpdir = tempfile.mkdtemp(prefix=f"atspi_{app_id}_")
    tmpfile = os.path.join(tmpdir, "wegwerf.txt")
    with open(tmpfile, "w") as f:
        f.write("AT-SPI Spike Wegwerf-Datei. Darf geloescht werden.\n")

    env = dict(os.environ)
    if cfg["qt_flag"]:
        env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"

    cmd = list(cfg["cmd"])
    if cfg["with_file"]:
        cmd.append(tmpfile)
    match = cfg.get("match", app_id)

    proc = subprocess.Popen(cmd, env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = {"app": app_id, "cmd": cmd, "pid": proc.pid,
              "qt_flag": cfg["qt_flag"], "tmpdir": tmpdir,
              "matched_by": None, "matched_pid": None, "stats": None,
              "top_level_timing": None, "full_tree_timing": None,
              "action": {"attempted": None, "target": None,
                         "worked": False, "verification": None},
              "error": None}
    try:
        # Auf Baum warten
        app = None
        deadline = time.time() + WAIT_FOR_TREE
        while time.time() < deadline:
            app, how = find_app_in_tree(desktop, proc.pid, app_id, match)
            if app is not None:
                result["matched_by"] = how
                break
            if proc.poll() is not None and time.time() > deadline - WAIT_FOR_TREE + 3:
                pass  # Prozess evtl. in laufende Instanz delegiert (Single-Instance)
            time.sleep(0.5)
        if app is None:
            result["error"] = ("App nicht im AT-SPI-Baum aufgetaucht "
                               f"(innerhalb {WAIT_FOR_TREE}s)")
            return result
        result["matched_pid"] = safe(app.get_process_id, None)
        time.sleep(2.0)  # Baum stabilisieren lassen

        # Statistik über vollen Baum
        result["stats"] = walk_tree(app)

        # Timings
        result["top_level_timing"] = time_queries(desktop, proc.pid, app_id, full=False, match=match)
        result["full_tree_timing"] = time_queries(desktop, proc.pid, app_id, full=True, match=match)

        # Harmlose Aktion: erstes erlaubtes Menü-Ziel
        cands = action_candidates(app)
        result["action"]["candidates"] = [
            {"name": c[1], "role": c[2],
             "actions": [safe(lambda j=j: c[3].get_action_name(j))
                         for j in range(c[4])]}
            for c in cands[:20]
        ]

        # Strategie 1 (bevorzugt, explizit erlaubt): „Über …"-Dialog öffnen.
        # Verifikation: danach existiert ein NEUER dialogartiger Knoten.
        about = find_about_item(app)
        strategy = None
        node = act = None
        names = []
        if about is not None:
            strategy = "about_dialog"
            node, name, role, act, n_act = about
            names = [safe(lambda j=j: act.get_action_name(j)) for j in range(n_act)]
        elif cands:
            # Strategie 2 (Fallback): Menü aufklappen (ShowMenu)
            strategy = "show_menu"
            node, name, role, act, n_act = cands[0]
            names = [safe(lambda j=j: act.get_action_name(j)) for j in range(n_act)]
        else:
            # Strategie 3 (Fallback): harmlosen Button drücken (z. B. GtkMenuButton
            # mit Popover -> Öffnen ist beobachtbar). Nur Namen aus Allowlist.
            btn = find_harmless_button(app)
            if btn is not None:
                strategy = "press_button"
                node, name, role, act, n_act = btn
                names = [safe(lambda j=j: act.get_action_name(j)) for j in range(n_act)]

        result["action"]["strategy"] = strategy
        if strategy is None:
            result["action"]["attempted"] = False
            result["action"]["verification"] = "kein erlaubtes Aktionsziel gefunden"
        else:
            low_names = {a.lower(): a for a in names if a}
            pref = (["press", "click", "activate"] if strategy == "about_dialog"
                    else ["showmenu", "press", "click", "activate"])
            chosen = next((low_names[p] for p in pref if p in low_names), None)
            result["action"]["attempted"] = True
            result["action"]["target"] = {"name": name, "role": role,
                                          "available_actions": names,
                                          "chosen_action": chosen}
            if chosen is None:
                result["action"]["verification"] = "keine Press/Click/Activate-Aktion am Ziel"
            else:
                idx = names.index(chosen)
                if strategy == "about_dialog":
                    tops_b, dlg_b = dialog_snapshot(app)
                    ret = safe(lambda: act.do_action(idx), None)
                    time.sleep(1.2)
                    tops_a, dlg_a = dialog_snapshot(app)
                    new_tops = [t for t in tops_a if t not in tops_b]
                    worked = bool(ret) and (dlg_a > dlg_b or
                                            any((r or "").lower() in DIALOG_ROLES
                                                for _, r in new_tops))
                    result["action"]["worked"] = worked
                    result["action"]["verification"] = (
                        f"do_action({chosen!r}) -> {ret}; "
                        f"Dialog-Knoten vorher={dlg_b} nachher={dlg_a}; "
                        f"neue Top-Level-Fenster={new_tops}")
                elif strategy == "press_button":
                    show_b = showing_snapshot(app)
                    ret = safe(lambda: act.do_action(idx), None)
                    time.sleep(1.0)
                    show_a = showing_snapshot(app)
                    new_shown = sorted(show_a - show_b)
                    worked = bool(ret) and bool(new_shown)
                    result["action"]["worked"] = worked
                    result["action"]["verification"] = (
                        f"do_action({chosen!r}) -> {ret}; "
                        f"neu sichtbare Knoten (name/role): {new_shown[:10]}")
                else:
                    before = menu_open_evidence(app, node)
                    ret = safe(lambda: act.do_action(idx), None)
                    time.sleep(0.8)
                    after = menu_open_evidence(app, node)
                    newly = [k for k in after if after[k] and not before[k]]
                    worked = bool(ret) and bool(newly)
                    result["action"]["worked"] = worked
                    result["action"]["verification"] = (
                        f"do_action({chosen!r}) -> {ret}; "
                        f"vorher={before}; nachher={after}; "
                        + (f"neu geöffnet belegt durch: {', '.join(newly)}"
                           if newly else "keine neue Zustandsänderung beobachtet"))
                # Geöffneter Dialog/Menü bleibt bis zum App-Ende bestehen;
                # synthetische Eingabe (Escape) ist nicht erlaubt.
    except TimeoutError as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
        for pid in {proc.pid, result.get("matched_pid")} - {None}:
            # Nur Prozesse anfassen, die eindeutig von uns stammen:
            # entweder unsere eigene Prozessgruppe oder cmdline im Wegwerf-Tmpdir
            if pid != proc.pid:
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as cf:
                        if tmpdir.encode() not in cf.read():
                            continue
                except Exception:
                    continue
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        for pid in {proc.pid, result.get("matched_pid")} - {None}:
            try:
                os.kill(pid, 0)  # lebt noch?
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    return result


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in APPS:
        print("usage: measure.py <" + "|".join(APPS) + ">", file=sys.stderr)
        sys.exit(2)
    res = main()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"raw_{sys.argv[1]}.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: res[k] for k in ("app", "matched_by", "error")}, ensure_ascii=False))
    if res["stats"]:
        print(json.dumps({"nodes": res["stats"]["nodes"],
                          "with_action": res["stats"]["with_action"],
                          "missing_name": res["stats"]["missing_name"]}))
    print(json.dumps({"action": {k: res["action"][k] for k in
                                 ("attempted", "worked", "verification")}},
                     ensure_ascii=False))
    if res["full_tree_timing"]:
        print(json.dumps({"top_p50": res["top_level_timing"]["p50_ms"],
                          "top_p95": res["top_level_timing"]["p95_ms"],
                          "full_p50": res["full_tree_timing"]["p50_ms"],
                          "full_p95": res["full_tree_timing"]["p95_ms"]}))
