#!/usr/bin/env python3
"""Diagnose: Was ändert sich im Baum, wenn ShowMenu auf ein Menü wirkt?"""
import os, subprocess, sys, tempfile, time
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

def safe(fn, d=None):
    try:
        return fn()
    except Exception:
        return d

def states_of(node):
    ss = safe(node.get_state_set)
    if ss is None:
        return set()
    arr = safe(lambda: ss.get_states(), []) or []
    return {Atspi.StateType(s).value_name.replace("ATSPI_STATE_", "").lower()
            for s in arr}

def find_menu_item(root, wanted):
    stack = [root]
    while stack:
        n = stack.pop()
        if (safe(n.get_name, "") or "") == wanted and \
           (safe(n.get_role_name, "") or "").lower() == "menu item":
            return n
        for k in range(safe(n.get_child_count, 0) or 0):
            c = safe(lambda k=k: n.get_child_at_index(k))
            if c is not None:
                stack.append(c)
    return None

def snapshot(root):
    """(name, role, states) aller Knoten als Liste."""
    out = []
    stack = [root]
    while stack:
        n = stack.pop()
        out.append((safe(n.get_name, ""), safe(n.get_role_name, ""),
                    frozenset(states_of(n))))
        for k in range(safe(n.get_child_count, 0) or 0):
            c = safe(lambda k=k: n.get_child_at_index(k))
            if c is not None:
                stack.append(c)
    return out

Atspi.init()
desktop = Atspi.get_desktop(0)
tmp = tempfile.mkdtemp(prefix="atspi_diag_")
f = os.path.join(tmp, "w.txt"); open(f, "w").write("x\n")
env = dict(os.environ, QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1")
p = subprocess.Popen(["kate", f], env=env, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    app = None
    for _ in range(40):
        for i in range(safe(desktop.get_child_count, 0) or 0):
            a = safe(lambda i=i: desktop.get_child_at_index(i))
            if a and "kate" in (safe(a.get_name, "") or "").lower() \
               and (safe(a.get_child_count, 0) or 0) > 0:
                app = a
        if app: break
        time.sleep(0.5)
    assert app, "kate nicht gefunden"
    time.sleep(2)

    mi = find_menu_item(app, "Ansicht")
    print("Ziel gefunden:", mi is not None)
    act = safe(mi.get_action_iface)
    print("Aktionen:", [safe(lambda j=j: act.get_action_name(j))
                        for j in range(safe(act.get_n_actions, 0) or 0)])
    print("States Ziel vorher:", sorted(states_of(mi)))
    ch = safe(lambda: mi.get_child_at_index(0))
    print("Kind vorher:", safe(ch.get_role_name) if ch else None,
          sorted(states_of(ch)) if ch else None)

    before = snapshot(app)
    idx = [safe(lambda j=j: act.get_action_name(j)) for j in range(3)].index("ShowMenu") \
          if "ShowMenu" in [safe(lambda j=j: act.get_action_name(j)) for j in range(3)] else 0
    print("do_action ->", safe(lambda: act.do_action(idx)))
    time.sleep(1.0)

    # Ziel-Referenz evtl. stale -> neu suchen
    mi2 = find_menu_item(app, "Ansicht")
    print("States Ziel nachher:", sorted(states_of(mi2)))
    ch2 = safe(lambda: mi2.get_child_at_index(0))
    print("Kind nachher:", safe(ch2.get_role_name) if ch2 else None,
          sorted(states_of(ch2)) if ch2 else None)
    print("Kinder des Ziels:", safe(mi2.get_child_count, 0))
    if ch2:
        for k in range(safe(ch2.get_child_count, 0) or 0):
            g = safe(lambda k=k: ch2.get_child_at_index(k))
            if g is not None and k < 5:
                print("  Enkel:", safe(g.get_name), safe(g.get_role_name),
                      sorted(states_of(g)))

    after = snapshot(app)
    bset, aset = set(before), set(after)
    print(f"Baumgröße vorher={len(before)} nachher={len(after)}")
    diff = [x for x in aset - bset if "showing" in x[2]]
    print("neu SHOWING Knoten (name/role/states):", diff[:10])
finally:
    import shutil, signal, glob
    try: os.killpg(p.pid, signal.SIGTERM)
    except Exception: pass
    time.sleep(1)
    try: os.killpg(p.pid, signal.SIGKILL)
    except Exception: pass
    for c in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            if tmp.encode() in open(c, "rb").read():
                os.kill(int(c.split("/")[2]), signal.SIGKILL)
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
