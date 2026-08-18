"""T-5.4 -- wann ueberhaupt hingesehen wird.

Zwei Quellen loesen aus, und die Reihenfolge der Wichtigkeit ist die
umgekehrte von der, die man erwartet:

**Der Timer traegt den Grossteil, nicht das Fokus-Ereignis.** T--1.9 hat
belegt, dass KWins `captionChanged` nur bei einer TITELaenderung feuert.
Terminalausgabe, Scrollen, ein neuer Absatz in einem Editor -- nichts davon
erzeugt ein Ereignis. Wer nur auf Fokus hoert, sieht einem Fenster beim
Stillstehen zu, waehrend sich sein Inhalt aendert. Das Ereignis ist der
billige Sonderfall, der Timer ist der Dienst.

**Beide muessen durch dasselbe Tor.** Ein gesperrter Bildschirm ist der eine
Zustand, in dem Hinsehen nicht bloss unnoetig, sondern falsch ist. Deshalb
gaebe es hier keinen Zweig, der am Tor vorbeikommt -- auch nicht fuer das
Fokus-Ereignis, das ja gerade dann kommt, wenn der Sperrschirm den Fokus
uebernimmt.

**Der Abstand ist gedeckelt, nicht die Quelle.** Ein Fenstermarathon -- Alt-Tab
zwoelfmal in zwei Sekunden -- wuerde sonst zwoelf Erfassungen ausloesen, von
denen elf denselben Bildschirm sehen. Der Deckel sitzt deshalb am Ausgang und
gilt fuer beide Quellen gemeinsam. Was er abweist, wird gezaehlt: eine
abgewiesene Erfassung, die niemand zaehlt, ist von einer nicht stattgefundenen
nicht zu unterscheiden.
"""
from __future__ import annotations

import time
from typing import Callable

GRUND_FOKUS = "fokus"
GRUND_TIMER = "timer"

# Eine Erfassung kostet gemessen 39 ms (T-5.3). Alle 5 s sind das 0,8 % der
# Zeit. Konservativ heisst hier: lieber eine Aenderung eine Sekunde spaeter
# sehen als den halben Tag Frames durch `videoconvert` schieben.
PERIODE_S = 5.0

# Zwei Erfassungen im selben Sekundenbruchteil sehen denselben Bildschirm.
MINDESTABSTAND_S = 1.0


class Ausloeser:
    """Entscheidet, ob jetzt hingesehen wird. Sieht selbst nicht hin.

    Die Trennung ist Absicht: diese Klasse laesst sich mit einer Uhr und einem
    Tor aus dem Testcode vollstaendig durchspielen, ohne Bildschirm, ohne
    PipeWire und ohne einen Menschen, der etwas anklickt.
    """

    def __init__(self, *, tor: Callable[[], bool],
                 periode_s: float = PERIODE_S,
                 mindestabstand_s: float = MINDESTABSTAND_S,
                 jetzt: Callable[[], float] = time.monotonic) -> None:
        if mindestabstand_s > periode_s:
            # Sonst frisst der Deckel den Timer auf, und der Dienst haengt
            # still am Fokus-Ereignis -- also an der Quelle, die laut T--1.9
            # gerade nicht traegt.
            raise ValueError(
                f"Mindestabstand {mindestabstand_s} s ist groesser als die "
                f"Periode {periode_s} s -- der Timer kaeme nie durch")
        self._tor = tor
        self._periode = periode_s
        self._abstand = mindestabstand_s
        self._jetzt = jetzt
        # `-unendlich` heisst: der erste `tick()` feuert sofort. Ein
        # Beobachter, der nach dem Start erst eine Periode wartet, haette
        # keine Beobachtung, auf die er die naechste beziehen koennte.
        self._letzte = float("-inf")
        self._zaehler = {GRUND_FOKUS: 0, GRUND_TIMER: 0,
                         "tor_zu": 0, "zu_dicht": 0}

    def _ausloesen(self, grund: str) -> str | None:
        if not self._tor():
            self._zaehler["tor_zu"] += 1
            return None
        jetzt = self._jetzt()
        if jetzt - self._letzte < self._abstand:
            self._zaehler["zu_dicht"] += 1
            return None
        self._letzte = jetzt
        self._zaehler[grund] += 1
        return grund

    def fokus(self) -> str | None:
        """Ein Fenster hat den Fokus bekommen oder seinen Titel geaendert."""
        return self._ausloesen(GRUND_FOKUS)

    def tick(self) -> str | None:
        """Von der Schleife gerufen. Loest aus, wenn die Periode um ist.

        Die Periode zaehlt ab der letzten ERFASSUNG, nicht ab dem letzten
        Tick: nach einem Fokus-Ereignis waere ein Timer, der stur weiterzaehlt,
        sofort wieder faellig und saehe denselben Bildschirm noch einmal.
        """
        if self._jetzt() - self._letzte < self._periode:
            return None
        return self._ausloesen(GRUND_TIMER)

    def zaehler(self) -> dict[str, int]:
        return dict(self._zaehler)


class SystemTor:
    """Das echte Tor: Sperrschirm und Leerlauf.

    Gemessen am 12.08. auf dieser Maschine:

        org.freedesktop.ScreenSaver GetActive           -> b false
        org.freedesktop.ScreenSaver GetSessionIdleTime  -> not supported
        login1 Session IdleHint                         -> b false

    Die Sperre ist damit belegt: die Eigenschaft antwortet und meldet den
    wahren Zustand. Der LEERLAUF ist es NICHT. `GetSessionIdleTime` gibt es
    auf dieser Plattform gar nicht, und von `IdleHint` ist nur bekannt, dass
    die Eigenschaft existiert und `false` liefert -- nicht, dass sie jemals
    `true` wird. Ein Tor, das nie schliesst, ist kein Tor.

    Das steht hier und nicht in einer Notiz, weil der Unterschied nach aussen
    unsichtbar ist: beide Faelle sehen aus wie „darf hinsehen". Wer das
    Kriterium „Idle-Gate schaltet ab" abhaken will, muss belegen, dass die
    Eigenschaft kippt -- diese Einheit hat es nicht belegt.

    Faellt DBus ganz aus, ist die Antwort ZU. Bei einem Dienst, der den
    Bildschirm mitliest, ist Nichtstun der harmlose Fehlerfall.
    """

    # Keine Schwelle in Sekunden: `IdleHint` ist ein Wahrheitswert, und wer
    # ihn festlegt, ist logind -- nicht dieser Dienst. Ein Parameter
    # `leerlauf_s`, der nirgends einginge, saehe aus wie ein Stellknopf.
    LEERLAUF_UNGEPRUEFT = True

    def gesperrt(self) -> bool:
        try:
            import dbus
            bus = dbus.SessionBus()
            objekt = bus.get_object("org.freedesktop.ScreenSaver",
                                    "/org/freedesktop/ScreenSaver")
            return bool(objekt.GetActive(
                dbus_interface="org.freedesktop.ScreenSaver"))
        except Exception:
            return True                      # kein Bus -> nicht hinsehen

    def leerlauf(self) -> bool:
        """`IdleHint` von logind. UNBELEGT, ob das je `true` wird."""
        try:
            import dbus
            bus = dbus.SystemBus()
            objekt = bus.get_object("org.freedesktop.login1",
                                    "/org/freedesktop/login1/session/auto")
            return bool(objekt.Get("org.freedesktop.login1.Session", "IdleHint",
                                   dbus_interface="org.freedesktop.DBus.Properties"))
        except Exception:
            return True                      # kein Bus -> nicht hinsehen

    def __call__(self) -> bool:
        """`True` heisst: hinsehen ist erlaubt."""
        return not self.gesperrt() and not self.leerlauf()
