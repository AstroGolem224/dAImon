"""IPC peer checking.

The peer check is a Wegweiser between our own components, keine
Authentifizierung gegen einen Angreifer mit Codeausfuehrung unter derselben uid.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
from typing import Callable

SO_PEERPIDFD = 77
PRODUCER_TYPES = {
    "eyes": frozenset({"screen"}),
    "hook": frozenset({"hook"}),
}


class PeerRejected(PermissionError):
    pass


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    unit: str


def create_listener(producer: str, runtime_dir: str | os.PathLike[str]) -> socket.socket:
    if producer not in PRODUCER_TYPES:
        raise ValueError(f"unbekannter Produzent: {producer}")
    directory = Path(runtime_dir) / "daimon"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{producer}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    listener.listen()
    return listener


def _pid_from_pidfd(pidfd: int) -> int:
    with open(f"/proc/self/fdinfo/{pidfd}", encoding="ascii") as info:
        for line in info:
            if line.startswith("Pid:"):
                return int(line.split(":", 1)[1])
    raise PeerRejected("pidfd enthaelt keine Pid-Zeile")


def _uid_for_pid(pid: int) -> int:
    with open(f"/proc/{pid}/status", encoding="ascii") as status:
        for line in status:
            if line.startswith("Uid:"):
                return int(line.split()[1])
    raise PeerRejected("Prozessstatus enthaelt keine uid")


def _unit_for_pid(pid: int) -> str:
    units: list[str] = []
    with open(f"/proc/{pid}/cgroup", encoding="ascii") as cgroup:
        for line in cgroup:
            path = line.rstrip().split(":", 2)[-1]
            units.extend(
                part for part in path.split("/")
                if part.endswith((".service", ".scope"))
            )
    if not units:
        raise PeerRejected("Peer gehoert zu keiner systemd-Unit")
    return units[-1]


def _reject(
    conn: socket.socket,
    reason: str,
    audit: Callable[[str, dict[str, object]], None] | None,
) -> None:
    if audit is not None:
        audit("ipc_peer_rejected", {"reason": reason})
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    raise PeerRejected(reason)


def authorize_peer(
    conn: socket.socket,
    socket_producer: str,
    message_type: str,
    expected_uid: int,
    expected_unit: str,
    audit: Callable[[str, dict[str, object]], None] | None = None,
) -> PeerIdentity:
    try:
        pidfd = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    except OSError as exc:
        _reject(conn, f"pidfd nicht verfuegbar: {exc}", audit)

    try:
        pid = _pid_from_pidfd(pidfd)
        if pid < 0:
            _reject(conn, "Peer ist bereits gestorben", audit)
        uid = _uid_for_pid(pid)
        unit = _unit_for_pid(pid)
    except (OSError, ValueError, PeerRejected) as exc:
        _reject(conn, f"Peer nicht aufloesbar: {exc}", audit)
    finally:
        os.close(pidfd)

    if message_type not in PRODUCER_TYPES.get(socket_producer, ()):
        _reject(conn, "Nachrichtentyp passt nicht zum Socket", audit)
    if uid != expected_uid:
        _reject(conn, "uid stimmt nicht ueberein", audit)
    if unit != expected_unit:
        _reject(conn, "systemd-Unit stimmt nicht ueberein", audit)
    return PeerIdentity(pid=pid, uid=uid, unit=unit)


# Oeffentlicher T-0.7-Vertrag.
PRODUZENTEN = {
    "hookbridge": frozenset({"hook"}),
    "eyes": frozenset({"screen"}),
}
PeerError = PeerRejected


class MessageTypeError(PermissionError):
    pass


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int
    unit: str
    produzent: str

    @property
    def lebt(self) -> bool:
        return self.pid > 0


def socket_path(runtime_dir: Path, produzent: str) -> Path:
    return runtime_dir / f"{produzent}.sock"


def listen(runtime_dir: Path, produzent: str, *, backlog: int = 8) -> socket.socket:
    if produzent not in PRODUZENTEN:
        raise ValueError(produzent)
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = socket_path(runtime_dir, produzent)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    listener.listen(backlog)
    return listener


def peer_of(conn: socket.socket, produzent: str) -> Peer:
    pidfd = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    try:
        pid = _pid_from_pidfd(pidfd)
        if pid < 0:
            raise PeerError("Gegenstelle ist bereits beendet (Pid: -1)")
        uid = _uid_for_pid(pid)
        unit = _unit_for_pid(pid)
    except OSError as exc:
        raise PeerError(f"Gegenstelle nicht aufloesbar: {exc}") from exc
    finally:
        os.close(pidfd)
    return Peer(pid, uid, uid, unit, produzent)


def pruefe_typ(produzent: str, typ: str) -> None:
    if typ not in PRODUZENTEN.get(produzent, ()):
        raise MessageTypeError(f"{typ} passt nicht zu {produzent}")


def accept(
    srv: socket.socket,
    produzent: str,
    *,
    erlaubte_uid: int | None = None,
    erlaubte_units=None,
    audit=None,
):
    conn, _ = srv.accept()
    try:
        peer = peer_of(conn, produzent)
        uid = os.getuid() if erlaubte_uid is None else erlaubte_uid
        if peer.uid != uid:
            if audit:
                audit("uid_abweichung", peer)
            raise PeerError("uid stimmt nicht")
        if erlaubte_units is not None and peer.unit not in set(erlaubte_units):
            if audit:
                audit("fremde_unit", peer)
            raise PeerError("Unit stimmt nicht")
        return conn, peer
    except PeerError:
        conn.close()
        raise
