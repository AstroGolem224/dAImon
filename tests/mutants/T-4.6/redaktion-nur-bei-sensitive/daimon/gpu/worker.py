"""T-3.7 — das Muster fuer Modellprozesse, die sich selbst beenden.

Hier steht **kein Modell**. Was hier steht, ist das Geruest: socket-aktiviert
starten, vor dem Laden am Hub um Erlaubnis fragen, laden, bedienen, und nach
einer Leerlauffrist **den Prozess beenden**. T-3.8 haengt das STT in
`_platzhalter_laden()` ein und aendert sonst nichts.

Warum Prozessende und nicht "Modell entladen"
----------------------------------------------------------------------------
Design 7.4 fuehrt fuer die GPU genau eine belastbare Groesse: das Prozessende.
Ein Worker, der das Modell entlaedt und weiterlaeuft, gibt VRAM erfahrungs-
gemaess nicht vollstaendig zurueck -- Allokator-Arenen, CUDA-Kontexte und
Treiberpuffer bleiben am lebenden Prozess haengen. Spike T−1.2 hat die
Rueckgabe deshalb **nach dem Exit** gemessen: 1392 MiB Idle-Median vor dem
Start, 4341 MiB Maximum waehrend der Inferenz, 1362 MiB nach dem Exit, kein
zurueckgebliebener Compute-PID. Das ist die Zusage dieses Moduls: **weg heisst
weg.** Wer den Leerlauf hier je in einen Zustand statt in ein `return` umbaut,
hat die Zusage entfernt, ohne dass ein Test sie vermisst -- deshalb steht sie
hier und nicht nur im Plan.

Der Leerlauftimer ist genau eine Zeile: `srv.settimeout(idle_s)` auf dem
horchenden Socket. Laeuft die Frist ohne neue Verbindung ab, faellt `main()`
mit `return 0` heraus, der Interpreter endet, der Kernel raeumt das VRAM.
Kein Timer-Thread, kein Signal, kein Aufraeumpfad, der vergessen werden kann.

Socket-Aktivierung
----------------------------------------------------------------------------
`daimon-gpu@.socket` haelt den horchenden Socket, `daimon-gpu@.service` ist
**eine** Template-Unit fuer alle Modelltypen; die Instanz (`%i`) ist der
Modelltyp. Der Socket ueberlebt das Prozessende, also darf der Worker jederzeit
gehen: die naechste Verbindung startet ihn neu. Ohne Socket-Aktivierung waere
"beendet sich selbst" gleichbedeutend mit "ist danach weg".

Ohne systemd (Tests, Handlauf) legt `--socket` denselben Socket selbst an.

Die Sperre liegt im Hub, nicht hier
----------------------------------------------------------------------------
Ein Worker kennt nur sich selbst. Zwei gleichzeitig ladende Worker belegen
beide ihr volles VRAM, **nachdem beide VRAM-Pruefungen gruen waren** -- eine
Sperre im Worker kann das nicht verhindern. Sie liegt deshalb dort, wo alle
durchmuessen: im Hub (`daimon/hub/daemon.py`, `gpu.sock`). Der Worker fragt,
laedt, und meldet `fertig` zurueck; stirbt er dazwischen, verfaellt die Sperre
im Hub nach Frist.

Drei unterscheidbare Absagegruende
----------------------------------------------------------------------------
`vram`, `fullscreen`, `lade_sperre` -- maschinenlesbar getrennt, nie ein
gemeinsames `error: true`. T-3.14 macht daraus Overlay-Zustaende, und "zu wenig
VRAM" braucht dort eine andere Anzeige als "der Nutzer spielt gerade".
Dazu kommt `hub_weg`: ohne den Hub gibt es keine Serialisierung, also wird
nicht geladen. Das ist die vierte, ehrliche Absage und keine der drei.

Absage heisst Prozessende
----------------------------------------------------------------------------
Wird das Laden abgelehnt, antwortet der Worker mit dem Grund und **geht**. Ein
Worker ohne Modell hat nichts zu bedienen; ihn warten zu lassen waere ein
belegter Prozess, der auf eine Bedingung pollt, die der naechste Aufruf
ohnehin neu prueft. Die naechste Verbindung startet ihn neu.

Was hier absichtlich fehlt
----------------------------------------------------------------------------
Kein Modell, kein ONNX, kein CUDA. `_platzhalter_laden()` schlaeft
`--ladedauer-s` und **meldet** `--vram-mib` an den Hub, ohne etwas zu
allozieren. Das ist Bedingung dafuer, dass dieses Geruest unabhaengig von T-3.8
pruefbar bleibt -- und es heisst zugleich, dass die VRAM-Pruefung hier gegen
eine *behauptete* Zahl laeuft. Die echte Zahl liefert erst T-3.8.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time

from daimon.common.config import Config, load as load_config
from daimon.common.logging import Logger, get_logger

GPU_SOCKET = "gpu.sock"           # der Sperr-Endpunkt des Hubs
LISTEN_FDS_START = 3              # sd_listen_fds(3), fest im Protokoll
MAX_ZEILE = 1 << 16               # eine Anfrage ist Bytes, keine Kilobytes

# Vorgaben. Sie stehen zusaetzlich in daimon/common/config.py -- hier, damit
# das Modul ohne Konfiguration lauffaehig bleibt.
IDLE_S = 60.0
LADEDAUER_S = 0.5
VRAM_MIB = 2600                   # parakeet-tdt-0.6b-v3 (2,4 GB) mit Luft
HUB_TIMEOUT_S = 15.0              # deckt busctl (2 s) + nvidia-smi (5 s) ab

_IMPORT_T = time.monotonic()


# -- Messungen, die auch der Hub braucht -----------------------------------
#
# Beide Aufrufe stehen OHNE absoluten Pfad da. Das ist Absicht und die Lehre
# aus T-2.7: ein Verifizierer kann einen Stub in den PATH legen und messen,
# was der Pruefling tun WOLLTE, ohne die echte GPU oder den echten Compositor
# zu beruehren. Ein absoluter Pfad haette diesen Messpunkt zugemauert.

def vram_frei_mib(*, timeout_s: float = 5.0) -> int | None:
    """Freies VRAM in MiB, oder None wenn nicht messbar.

    None ist **keine** Erlaubnis: der Hub weist bei None ab. Anders als beim
    Fullscreen ist die Fehlrichtung hier teuer -- ein Modell, das ins volle
    VRAM laedt, nimmt den Compositor mit, und dann ist die ganze Sitzung weg.
    """
    try:
        lauf = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    if lauf.returncode != 0:
        return None
    erste = (lauf.stdout or "").strip().splitlines()
    try:
        # Mehrere GPUs: die erste. Diese Maschine hat eine.
        # ponytail: kein GPU-Index. Obergrenze: sobald eine zweite Karte im
        # Rechner steckt, gehoert hier `--id=` plus ein Konfigurationswert hin.
        return int(erste[0].split(",")[0].strip())
    except (IndexError, ValueError):
        return None


def fullscreen_aktiv(*, timeout_s: float = 2.0) -> bool | None:
    """Liegt ein Vollbildfenster vorn? None heisst "niemand weiss es".

    Gefragt wird der laufende `daimon-focus.service` (T-0.12) ueber seinen
    DBus-Namen -- der Watcher wird **nicht** neu gebaut. `busctl` statt
    dbus-python, weil der Hub im venv laeuft und dbus-python eine kompilierte
    Systembibliothek ist (dieselbe Zwei-Interpreter-Trennung wie beim
    Auth-Agenten).

    Fail-Richtung: None **erlaubt**. Ist der Fokus-Dienst tot, kostet ein
    Ladevorgang schlimmstenfalls ein paar Frames in einem Spiel; ihn zu
    verweigern kostet das Sprachsystem, und zwar dauerhaft, weil ein toter
    Dienst nicht von selbst wiederkommt. Der Hub meldet `fullscreen_bekannt`
    in der Antwort, damit diese Nachsicht sichtbar bleibt und nicht schweigend
    passiert. Genau umgekehrt zu `vram_frei_mib()` -- siehe dort.
    """
    try:
        lauf = subprocess.run(
            ["busctl", "--user", "--no-pager", "--json=short", "call",
             "de.daimon.Focus", "/Focus", "de.daimon.Focus", "Zustand"],
            capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    if lauf.returncode != 0:
        return None
    try:
        daten = json.loads(lauf.stdout)["data"]
        voll, alter_s = bool(daten[0]), float(daten[1])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if alter_s < 0.0:
        # Der Dienst laeuft, hat aber noch kein Fenster gesehen. Das ist
        # "unbekannt" und nicht "kein Vollbild".
        return None
    return voll


# -- Prozessalter ----------------------------------------------------------

def alter_s() -> float:
    """Sekunden seit Prozessstart -- **einschliesslich Interpreterstart**.

    `time.monotonic()` beim Import wuerde genau den Teil verschweigen, der bei
    einem socket-aktivierten Prozess am meisten kostet: exec, Import,
    Bibliotheken. Die Kaltstartzeit ist die Zeit vom Verbindungsversuch bis zur
    Antwort, also gehoert sie am Prozessstart gemessen. /proc/self/stat Feld 22
    (starttime, Ticks seit Boot) gegen /proc/uptime.
    """
    try:
        with open("/proc/self/stat", "r") as fh:
            # Der Prozessname steht in Klammern und darf Leerzeichen und
            # Klammern enthalten -- deshalb am LETZTEN ") " trennen.
            felder = fh.read().rsplit(") ", 1)[1].split()
        start_ticks = int(felder[19])           # Feld 22, 1-basiert
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime", "r") as fh:
            up = float(fh.read().split()[0])
        return max(0.0, up - start_ticks / hz)
    except (OSError, ValueError, IndexError, ZeroDivisionError):
        return time.monotonic() - _IMPORT_T


# -- Hub-Sperre ------------------------------------------------------------

def hub_anfrage(hub_socket: str, anfrage: dict, *,
                timeout_s: float = HUB_TIMEOUT_S) -> dict:
    """Eine Zeile hin, eine Zeile zurueck. Verbindung danach zu.

    Eine Verbindung je Anfrage, nicht eine gehaltene: eine gehaltene
    Verbindung waere ein zweiter, stiller Sperrmechanismus -- der Hub muesste
    ihren Abriss deuten. Die Sperre haengt an einer Marke mit Frist, an sonst
    nichts.
    """
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout_s)
    try:
        c.connect(hub_socket)
        c.sendall(json.dumps(anfrage).encode() + b"\n")
        roh = c.makefile("rb").readline(MAX_ZEILE)
    except OSError as exc:
        return {"v": 1, "ok": False, "grund": "hub_weg",
                "meldung": str(exc)[:120]}
    finally:
        c.close()
    try:
        antwort = json.loads(roh)
    except (json.JSONDecodeError, ValueError):
        return {"v": 1, "ok": False, "grund": "hub_weg",
                "meldung": "Antwort unlesbar"}
    return antwort if isinstance(antwort, dict) else {
        "v": 1, "ok": False, "grund": "hub_weg", "meldung": "Antwort kein Objekt"}


# -- Der Platzhalter -------------------------------------------------------

def _platzhalter_laden(dauer_s: float) -> None:
    """Hier haengt T-3.8 das Modell ein.

    Es wird nichts alloziert -- der VRAM-Bedarf wird nur *behauptet* und an
    den Hub gemeldet. Ein Platzhalter, der wirklich VRAM belegt, braeuchte
    CUDA im Worker und damit genau die Abhaengigkeit, die dieses Geruest
    unabhaengig von T-3.8 pruefbar halten soll.

    ponytail: ein `sleep`. Obergrenze: sobald hier ein echter Ladevorgang
    steht, wird `vram_mib` aus der Messung statt aus der Konfiguration
    genommen, und dann muss die Hub-Antwort zwischen "angefragt" und
    "tatsaechlich belegt" unterscheiden koennen.
    """
    time.sleep(max(0.0, dauer_s))


# -- Sockets ---------------------------------------------------------------

def sd_socket() -> socket.socket | None:
    """Der von systemd uebergebene horchende Socket, oder None.

    `LISTEN_PID` wird gegen die eigene PID geprueft: die Variablen werden
    vererbt, und ein Kindprozess, der fd 3 fuer seinen haelt, uebernimmt einen
    fremden Deskriptor.
    """
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        n = int(os.environ.get("LISTEN_FDS", "0") or 0)
    except ValueError:
        return None
    if n < 1:
        return None
    return socket.socket(fileno=LISTEN_FDS_START, family=socket.AF_UNIX,
                         type=socket.SOCK_STREAM)


def eigener_socket(pfad: str) -> socket.socket:
    """Ohne systemd. Modus 0600 nach dem Binden -- bind() beachtet die umask."""
    if os.path.exists(pfad):
        os.unlink(pfad)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(pfad)
    os.chmod(pfad, 0o600)
    srv.listen(8)
    return srv


class Worker:
    """Ein Modellprozess. Laedt einmal, bedient, und geht.

    Kein Zustandsautomat und keine Basisklasse: der ganze Lebenslauf sind die
    zwoelf Zeilen in `lauf()`. Wer hier eine Klassenhierarchie fuer "den
    naechsten Modelltyp" anlegt, baut Vererbung fuer eine Instanzvariable.
    """

    def __init__(self, *, modell: str, hub_socket: str,
                 idle_s: float = IDLE_S, ladedauer_s: float = LADEDAUER_S,
                 vram_mib: int = VRAM_MIB, log: Logger | None = None) -> None:
        self.modell = modell
        self.hub_socket = hub_socket
        self.idle_s = float(idle_s)
        self.ladedauer_s = float(ladedauer_s)
        self.vram_mib = int(vram_mib)
        self.log = log or get_logger(f"daimon-gpu@{modell}")
        self.geladen = False
        self.kaltstart_ms: float | None = None
        self.anfragen = 0

    # -- Kriterium 3 bis 6: fragen, laden, Kaltstart melden ----------------

    def laden(self) -> dict:
        """Am Hub fragen, dann laden. Antwort ist die Antwort an den Client.

        Reihenfolge: erst die Erlaubnis (die Sperre, die Fullscreen- und die
        VRAM-Pruefung liegen alle im Hub und werden dort **unter derselben
        Sperre** ausgewertet), dann laden, dann freigeben. Eine VRAM-Pruefung
        vor dem Erwerb der Sperre waere veraltet, sobald sie gilt: der andere
        Ladevorgang belegt sein VRAM ja gerade waehrend man wartet.
        """
        antwort = hub_anfrage(self.hub_socket, {
            "v": 1, "art": "laden", "modell": self.modell,
            "vram_mib": self.vram_mib,
        })
        if not antwort.get("ok"):
            grund = str(antwort.get("grund", "unbekannt"))
            self.log.warn("Laden abgelehnt", DAIMON_MODELL=self.modell,
                          DAIMON_GRUND=grund)
            return {**antwort, "v": 1, "ok": False, "grund": grund,
                    "modell": self.modell, "geladen": False,
                    "kaltstart_ms": round(alter_s() * 1000, 3)}

        _platzhalter_laden(self.ladedauer_s)
        self.geladen = True
        self.kaltstart_ms = round(alter_s() * 1000, 3)

        # Freigeben, auch wenn es scheitert: die Frist im Hub faengt das.
        frei = hub_anfrage(self.hub_socket, {
            "v": 1, "art": "fertig", "sperre": antwort.get("sperre", ""),
        })
        if not frei.get("ok"):
            self.log.warn("Sperre nicht freigegeben -- sie verfaellt nach Frist",
                          DAIMON_MODELL=self.modell,
                          DAIMON_GRUND=str(frei.get("grund", ""))[:80])
        self.log.info("Modell geladen", DAIMON_ACTION="laden",
                      DAIMON_MODELL=self.modell,
                      DAIMON_KALTSTART_MS=self.kaltstart_ms,
                      DAIMON_VRAM_MIB=self.vram_mib)
        return self.zustand()

    def zustand(self) -> dict:
        return {
            "v": 1, "ok": True, "modell": self.modell,
            "geladen": self.geladen,
            # Kriterium 6. Steht in JEDER Antwort, nicht nur in der ersten:
            # ein Wert, den man nur im Journal findet, wird nicht gemessen.
            "kaltstart_ms": self.kaltstart_ms,
            "vram_mib": self.vram_mib,
            "leerlauf_s": self.idle_s,
            "anfragen": self.anfragen,
            "pid": os.getpid(),
        }

    # -- Kriterium 2: der Leerlauf endet im Prozessende --------------------

    def lauf(self, srv: socket.socket) -> int:
        srv.settimeout(self.idle_s)   # das ist der ganze Leerlauftimer
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                self.log.info("Leerlauf abgelaufen -- Prozessende",
                              DAIMON_ACTION="exit", DAIMON_GRUND="leerlauf",
                              DAIMON_MODELL=self.modell,
                              DAIMON_LEERLAUF_S=self.idle_s,
                              DAIMON_ANFRAGEN=self.anfragen)
                return 0   # kein Entladen, kein Schlafen: Ende.
            except OSError:
                return 1
            with conn:
                conn.settimeout(5.0)
                self.anfragen += 1
                if not self.geladen:
                    antwort = self.laden()
                    _antworte(conn, antwort)
                    if not antwort.get("ok"):
                        self.log.info("Absage -- Prozessende",
                                      DAIMON_ACTION="exit",
                                      DAIMON_GRUND=str(antwort.get("grund")),
                                      DAIMON_MODELL=self.modell)
                        return 0
                    continue
                _antworte(conn, self.zustand())


def _antworte(conn: socket.socket, daten: dict) -> None:
    try:
        conn.sendall(json.dumps(daten).encode() + b"\n")
    except OSError:
        pass   # Client weg. Kein Log: ein Neustart darf keine Zeile kosten.


def einstellungen(cfg: Config, modell: str) -> dict:
    """`[gpu]` plus `[gpu.modelle]` aus einer Config."""
    return {
        "idle_s": float(cfg.get("gpu.idle_s", IDLE_S)),
        "ladedauer_s": float(cfg.get("gpu.ladedauer_s", LADEDAUER_S)),
        "vram_mib": int(cfg.get(f"gpu.modelle.{modell}", VRAM_MIB)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon GPU-Worker (T-3.7)")
    ap.add_argument("--modell", required=True,
                    help="Modelltyp; in der Unit die Instanz %%i")
    ap.add_argument("--socket", default=None,
                    help="ohne systemd: hier selbst horchen")
    ap.add_argument("--hub-socket", default=None)
    ap.add_argument("--idle-s", type=float, default=None)
    ap.add_argument("--ladedauer-s", type=float, default=None)
    ap.add_argument("--vram-mib", type=int, default=None)
    args = ap.parse_args(argv)

    # make_dirs=False: `load()` legt sonst $XDG_STATE_HOME/daimon an und
    # chmod-et es -- unter `ProtectHome=read-only` ist das ein EROFS, und der
    # Worker stirbt vor der ersten Zeile. Am 03.08. genau so passiert. Er
    # schreibt dort ohnehin nichts; wer hier je Zustand ablegen will, braucht
    # ein ReadWritePaths in der Unit und keine Aenderung an dieser Zeile.
    cfg = load_config(make_dirs=False)
    werte = einstellungen(cfg, args.modell)
    if args.idle_s is not None:
        werte["idle_s"] = args.idle_s
    if args.ladedauer_s is not None:
        werte["ladedauer_s"] = args.ladedauer_s
    if args.vram_mib is not None:
        werte["vram_mib"] = args.vram_mib
    hub_socket = args.hub_socket or str(cfg.runtime_dir / GPU_SOCKET)

    srv = sd_socket()
    if srv is None:
        if not args.socket:
            raise SystemExit(
                "Weder Socket-Aktivierung (LISTEN_FDS) noch --socket. Der "
                "Worker legt ohne beides keinen Socket an: er waere dann "
                "gestartet, aber unerreichbar.")
        srv = eigener_socket(args.socket)

    w = Worker(modell=args.modell, hub_socket=hub_socket, **werte)
    try:
        return w.lauf(srv)
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
