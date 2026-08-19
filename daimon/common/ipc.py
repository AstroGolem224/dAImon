"""T-0.7 — Unix-Sockets, die wissen, wer spricht.

WAS DAS HIER IST UND WAS NICHT
------------------------------
Design 1.3 ist ausdruecklich: ein Angreifer mit Codeausfuehrung unter
derselben uid wird **nicht** abgewehrt. Diese Schicht ist damit ein
**Wegweiser**, keine Authentifizierung. Sie verhindert, dass ein falsch
verdrahteter oder verwirrter eigener Prozess Unsinn an der falschen Stelle
einwirft -- und sie macht im Nachhinein nachvollziehbar, wer was gesendet hat.
Sie haelt niemanden auf, der ohnehin schon als derselbe Nutzer Code ausfuehrt.

Das ist keine Bescheidenheitsfloskel: wer sie fuer mehr haelt, baut spaeter
eine Berechtigungsentscheidung darauf.

WARUM SO_PEERPIDFD UND NICHT SO_PEERCRED
----------------------------------------
`SO_PEERCRED` liefert PID, UID, GID in einem Rutsch. Bequem und falsch, sobald
man die PID benutzt, um spaeter unter `/proc/<pid>/` nachzuschlagen: zwischen
dem Auslesen und dem Nachschlagen kann der Prozess sterben und die PID neu
vergeben werden. Was man dann liest, gehoert einem anderen Prozess. Das ist
ein Wiederverwendungsrennen, und es ist ausnutzbar -- ein Angreifer kann es
provozieren, indem er in einer Schleife Prozesse startet und beendet.

`SO_PEERPIDFD` (Linux >= 6.5) liefert stattdessen einen **pidfd**. Solange
dieser Deskriptor offen ist, wird die PID nicht neu vergeben: der Kernel haelt
den Eintrag, notfalls als Zombie. Erst dadurch ist es zulaessig, aus
`/proc/<pid>/` zu lesen. Die PID selbst kommt aus `/proc/self/fdinfo/<pidfd>`,
Zeile `Pid:` -- ein gestorbener Prozess zeigt dort `Pid: -1`.

Die Reihenfolge ist also: pidfd holen, festhalten, DANN aufloesen. Nicht
umgekehrt, und nicht dazwischen loslassen.

NACHRICHTENTYPEN SIND PRODUZENTENSPEZIFISCH
-------------------------------------------
Ein Socket je Produzent, und jeder Produzent darf nur seine eigenen Typen
senden. `eyes` darf kein `hook`-Event schicken. Das ist die Haelfte des Werts
dieser Schicht: selbst wenn ein Produzent kompromittiert ist, kann er nicht in
die Rolle eines anderen schluepfen, ohne auch dessen Socket zu erreichen.
"""

from __future__ import annotations

import os
import re
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# In Pythons socket-Modul (bis 3.12) nicht exportiert. Wert aus
# <asm-generic/socket.h>. Auf dieser Maschine geprueft.
SO_PEERPIDFD = getattr(socket, "SO_PEERPIDFD", 77)

# Ein Socket je Produzent, und die erlaubten Ereignistypen dazu.
PRODUZENTEN: dict[str, frozenset[str]] = {
    "hookbridge": frozenset({"hook"}),
    # ENTSCHIEDEN mit dem Ohren-Dienst (T-3.15, nachgezogen 10.08.):
    # `intent_mark` ist GESTRICHEN. Design 2.4 erteilt dem Wake-Word nur ein
    # API-Kontingent, keine Rundenmarke -- und mit Plan C gibt es nicht
    # einmal das Wake-Word; die Marke kommt vom Auth-Agenten (PTT).
    # Wirkungslos war der Eintrag ohnehin (der Hub behandelt `intent_mark`
    # nur auf dem auth-Socket, `MarkenBuch.ausgeben` erzwingt quelle="auth"),
    # aber er BEHAUPTETE eine Faehigkeit -- dieselbe Behauptungsklasse, die
    # T-1.7 beim Face beseitigt hat. Wer ein Wake-Word nachruestet, bekommt
    # einen EIGENEN Typ, der auf `KontingentBuch.ausgeben(quelle="wake_word")`
    # fuehrt, nicht diesen zurueck.
    "ears": frozenset({"utterance"}),
    "eyes": frozenset({"screen"}),
    "kwin": frozenset({"window"}),
    # T-1.7 bleibt die Grenze: Das Face darf weder `intent_mark` noch
    # `freigabe` senden. T-2.2 oeffnete ausschliesslich den engen Meldeweg
    # fuer eine vom Nutzer weggeklickte Blase.
    #
    # T-2.7 laesst diese Menge trotz des frueheren "darf nicht wieder
    # wachsen" um genau einen Typ wachsen -- und zwar um den
    # schwaechstmoeglichen, den es fuer ein Kontextmenue gibt. Warum das
    # vertretbar ist, in drei Punkten:
    #
    #   1. EINSEITIG. `wahrnehmung_aus` kann nur abschalten. Ein Gegenstueck
    #      zum Einschalten gibt es nicht, in keinem Produzenten. Einschalten
    #      gehoert zum Auth-Agenten und existiert in P2 nicht.
    #   2. FAIL-SAFE. Der schlimmste Missbrauch ist, dass Ohren oder Augen
    #      ausgehen -- also weniger Wahrnehmung, nicht mehr. Das ist die
    #      Richtung, in die ein Fehler laufen darf.
    #   3. ZIEL AUS DER ALLOWLIST. Die Nutzlast traegt `{"ziel": "ears"|
    #      "eyes"}`, einen SCHLUESSEL. Welche Unit dahintersteht, steht in
    #      der Konfiguration des Hubs (`hub.wahrnehmung_units`), nie in der
    #      Nachricht. Das ist die eigentliche Grenze: naehme der Hub den
    #      Unit-Namen aus der Nachricht, koennte das Overlay den Hub selbst,
    #      den Auth-Agenten oder jede beliebige Unit des Nutzers stoppen.
    #
    # Was dieser Eintrag ausdruecklich NICHT erlaubt: eine Freigabe zu
    # erteilen, eine Rundenmarke zu erzeugen, eine Unit zu STARTEN oder
    # ueberhaupt eine Unit zu BENENNEN. Wer hier `wahrnehmung_an` oder einen
    # Typ mit freiem Unit-Namen ergaenzen will, hebt genau das auf, was T-1.7
    # und T-2.7 zusammen halten -- und braucht dafuer eine eigene
    # Entscheidung, keine Erweiterung dieser Zeile.
    "face": frozenset({"bubble_dismiss", "wahrnehmung_aus"}),
    # T-3.14 laesst diese Menge um genau einen Typ wachsen: `ptt`. Dieselbe
    # Pruefung wie bei T-2.7, mit denselben drei Punkten:
    #
    #   1. DER ABSENDER BESITZT ES SCHON. Der PTT-Umschaltautomat steht in
    #      `daimon/auth/ptt.py` und gehoert dem Auth-Agenten. Die Meldung sagt
    #      nichts, was er nicht ohnehin entscheidet.
    #   2. KEINE FAEHIGKEIT. `ptt` erteilt keine Freigabe, erzeugt keine
    #      Rundenmarke, startet und stoppt nichts. Die Nutzlast ist ein
    #      Wahrheitswert. Insbesondere erteilt `listening` KEIN Mikrofonrecht --
    #      das Mikrofon oeffnet der Ohren-Dienst, nicht dieses Feld.
    #   3. SCHLIMMSTER MISSBRAUCH: das Overlay zeigt "hoert zu", obwohl niemand
    #      zuhoert. Sichtbar falsch, folgenlos fuer jede Schranke.
    "auth": frozenset({"intent_mark", "freigabe", "ptt"}),
    # T-7.1: HIER BENENNT DER SCHLUESSEL DEN SOCKET, NICHT DEN SPRECHER.
    # `recorder.sock` gehoert dem Archivdienst, und wer dort spricht, ist
    # `daimon-eyes` -- festgenagelt ueber `erlaubte_units` in `accept`, nicht
    # ueber diesen Namen. Der Grund fuer die Ausnahme: der Recorder ist der
    # EINZIGE Prozess mit Schreibrecht aufs Archiv (Plan T-7.1), und die
    # Augen sollen melden statt schreiben. Ein zweiter Weg ins Archiv waere
    # genau die Tuer, die dieser Zuschnitt zumacht.
    #
    # Ein Typ, und er kann nur ablegen: `archiv`. Was er ausdruecklich NICHT
    # kann -- lesen, suchen, loeschen, eine Frist aendern. Wer hier `suche`
    # oder `loeschen` ergaenzt, gibt dem Bildschirmleser Zugriff auf die
    # aufgezeichnete Vergangenheit; das ist T-7.5 und geht durch das
    # Deklassifizierungs-Gate, nicht durch diesen Socket.
    "recorder": frozenset({"archiv"}),
}


class PeerError(PermissionError):
    """Gegenstelle ist nicht die, die an diesem Socket sein duerfte."""


class MessageTypeError(PermissionError):
    """Nachrichtentyp gehoert nicht zu diesem Produzenten."""


@dataclass(frozen=True)
class Peer:
    """Was wir ueber die Gegenstelle wissen -- und woher.

    `unit` ist die systemd-Unit aus dem cgroup-Pfad. Sie ist ein Hinweis, kein
    Beweis: wer als derselbe Nutzer laeuft, kann sich eine eigene Unit anlegen.
    Siehe Klassenkopf.
    """

    pid: int
    uid: int
    gid: int
    unit: str
    produzent: str

    @property
    def lebt(self) -> bool:
        return self.pid > 0


def _pid_aus_pidfd(pidfd: int) -> int:
    """PID aus /proc/self/fdinfo/<pidfd>. -1, wenn der Prozess weg ist."""
    try:
        with open(f"/proc/self/fdinfo/{pidfd}", "r") as fh:
            for zeile in fh:
                if zeile.startswith("Pid:"):
                    return int(zeile.split()[1])
    except (OSError, ValueError, IndexError):
        return -1
    return -1


def _uid_gid(pid: int) -> tuple[int, int]:
    with open(f"/proc/{pid}/status", "r") as fh:
        uid = gid = -1
        for zeile in fh:
            if zeile.startswith("Uid:"):
                uid = int(zeile.split()[1])   # effective uid
            elif zeile.startswith("Gid:"):
                gid = int(zeile.split()[1])
            if uid >= 0 and gid >= 0:
                break
    return uid, gid


_UNIT = re.compile(r"/([^/]+\.(?:service|scope|slice))(?:/|$)")


def _unit(pid: int) -> str:
    """systemd-Unit aus dem cgroup-Pfad. Leer, wenn keine erkennbar ist."""
    try:
        with open(f"/proc/{pid}/cgroup", "r") as fh:
            inhalt = fh.read()
    except OSError:
        return ""
    treffer = _UNIT.findall(inhalt)
    # Der spezifischste Eintrag steht am Ende des Pfades.
    return treffer[-1] if treffer else ""


def peer_of(conn: socket.socket, produzent: str) -> Peer:
    """Gegenstelle bestimmen. Loest AUSSCHLIESSLICH am gepinnten pidfd auf.

    Der pidfd wird bis zum Schluss offen gehalten und erst danach geschlossen.
    Wer diese Funktion umbaut: das Schliessen darf nicht vor die
    /proc-Zugriffe wandern, sonst ist das Rennen wieder da.
    """
    try:
        pidfd = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    except OSError as exc:
        raise PeerError(
            f"SO_PEERPIDFD nicht verfuegbar ({exc}). Ein Rueckfall auf "
            "SO_PEERCRED waere ein PID-Wiederverwendungsrennen und findet "
            "deshalb nicht statt."
        ) from exc

    try:
        pid = _pid_aus_pidfd(pidfd)
        if pid <= 0:
            raise PeerError("Gegenstelle ist bereits beendet (Pid: -1)")
        try:
            uid, gid = _uid_gid(pid)
            unit = _unit(pid)
        except OSError as exc:
            raise PeerError(f"Gegenstelle nicht mehr auflösbar ({exc})") from exc
    finally:
        os.close(pidfd)

    return Peer(pid=pid, uid=uid, gid=gid, unit=unit, produzent=produzent)


def socket_path(runtime_dir: Path, produzent: str) -> Path:
    return runtime_dir / f"{produzent}.sock"


def listen(runtime_dir: Path, produzent: str, *, backlog: int = 8) -> socket.socket:
    """Horchender Socket fuer einen Produzenten, Modus 0600.

    Der Modus wird nach dem Binden gesetzt: bind() beachtet die umask, ein
    `os.umask()` davor waere prozessweit und wuerde andere Threads treffen.
    """
    if produzent not in PRODUZENTEN:
        raise ValueError(f"unbekannter Produzent {produzent!r}")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pfad = socket_path(runtime_dir, produzent)
    if pfad.exists():
        pfad.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(pfad))
    os.chmod(pfad, 0o600)
    srv.listen(backlog)
    return srv


def pruefe_typ(produzent: str, typ: str) -> None:
    erlaubt = PRODUZENTEN.get(produzent, frozenset())
    if typ not in erlaubt:
        raise MessageTypeError(
            f"Produzent {produzent!r} darf {typ!r} nicht senden "
            f"(erlaubt: {', '.join(sorted(erlaubt)) or 'nichts'})"
        )


def unit_erlaubt(unit: str, erlaubte: Iterable[str]) -> bool:
    """Steht `unit` auf der Liste -- mit genau einer Ausnahme fuer Templates.

    Ein Eintrag, der auf `@` endet, meint alle Instanzen dieses Templates:
    `daimon-gpu@` erlaubt `daimon-gpu@qwen3.service` ebenso wie
    `daimon-gpu@llama.service`. Anders geht es nicht -- der Instanzname steht
    erst zur Laufzeit fest, und eine Liste kann ihn nicht vorwegnehmen.

    **Der Instanzteil wird NICHT geprueft, und das ist der Preis.** Wer eine
    eigene `daimon-gpu@beliebig.service` anlegt, steht damit auf der Liste.
    Das ist hinnehmbar, weil die Peer-Pruefung ohnehin ein Wegweiser ist und
    keine Authentifizierung (DESIGN.md 1.3): wer Units anlegen kann, laeuft
    bereits unter dieser uid und braucht diesen Weg nicht. Eine Regel, die
    hier mehr verspraeche, waere die schlechtere.

    Alles ohne `@` am Ende wird exakt verglichen, wie vorher. Ein Praefix
    „irgendwie aehnlich" gibt es nicht -- `daimon-gpu` erlaubt keine Instanz,
    und `daimon-gpu@` erlaubt nicht `daimon-gpu.service`.
    """
    liste = set(erlaubte)
    if unit in liste:
        return True
    return any(e.endswith("@") and unit.startswith(e) and unit.endswith(".service")
               for e in liste)


def accept(srv: socket.socket, produzent: str, *,
           erlaubte_uid: int | None = None,
           erlaubte_units: Iterable[str] | None = None,
           audit: Callable[[str, Peer], None] | None = None
           ) -> tuple[socket.socket, Peer]:
    """Verbindung annehmen und die Gegenstelle pruefen.

    Bei Ablehnung wird die Verbindung geschlossen UND ein Audit-Eintrag
    erzeugt. Ohne den Eintrag waere eine abgewiesene Verbindung unsichtbar --
    und gerade die will man sehen.
    """
    conn, _ = srv.accept()
    try:
        peer = peer_of(conn, produzent)
    except PeerError:
        conn.close()
        raise

    soll_uid = os.getuid() if erlaubte_uid is None else erlaubte_uid
    if peer.uid != soll_uid:
        if audit:
            audit("uid_abweichung", peer)
        conn.close()
        raise PeerError(f"uid {peer.uid} != {soll_uid}")

    if erlaubte_units is not None and not unit_erlaubt(peer.unit,
                                                       erlaubte_units):
        if audit:
            audit("fremde_unit", peer)
        conn.close()
        raise PeerError(f"Unit {peer.unit!r} nicht zugelassen")

    if audit:
        audit("angenommen", peer)
    return conn, peer


def ist_0600(pfad: Path) -> bool:
    return stat.S_IMODE(pfad.stat().st_mode) == 0o600
