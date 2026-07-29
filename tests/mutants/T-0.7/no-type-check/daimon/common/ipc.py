"""Mutante: SO_PEERPIDFD korrekt, aber ohne produzentenspezifische Typpruefung.

Die Peer-Pruefung ist nur ein Wegweiser, keine Authentifizierung gegen
Codeausfuehrung unter derselben uid.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
from typing import Callable

SO_PEERPIDFD = 77
PRODUCER_TYPES = {"eyes": frozenset({"screen"}), "hook": frozenset({"hook"})}


class PeerRejected(PermissionError):
    pass


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    unit: str


def create_listener(producer: str, runtime_dir: str | os.PathLike[str]) -> socket.socket:
    if producer not in PRODUCER_TYPES:
        raise ValueError(producer)
    directory = Path(runtime_dir) / "daimon"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{producer}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    listener.listen()
    return listener


def _pid(pidfd: int) -> int:
    with open(f"/proc/self/fdinfo/{pidfd}", encoding="ascii") as info:
        return int(next(line for line in info if line.startswith("Pid:")).split(":")[1])


def _identity(pid: int) -> PeerIdentity:
    with open(f"/proc/{pid}/status", encoding="ascii") as status:
        uid = int(next(line for line in status if line.startswith("Uid:")).split()[1])
    with open(f"/proc/{pid}/cgroup", encoding="ascii") as cgroup:
        units = [
            part
            for line in cgroup
            for part in line.rstrip().split(":", 2)[-1].split("/")
            if part.endswith((".service", ".scope"))
        ]
    return PeerIdentity(pid, uid, units[-1])


def _reject(conn, reason, audit):
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
    pidfd = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    try:
        pid = _pid(pidfd)
        if pid < 0:
            _reject(conn, "Peer ist gestorben", audit)
        peer = _identity(pid)
    except (OSError, ValueError, StopIteration, IndexError) as exc:
        _reject(conn, str(exc), audit)
    finally:
        os.close(pidfd)
    # MUTATION: message_type und socket_producer werden nicht verglichen.
    if peer.uid != expected_uid:
        _reject(conn, "uid stimmt nicht", audit)
    if peer.unit != expected_unit:
        _reject(conn, "Unit stimmt nicht", audit)
    return peer


PRODUZENTEN = {"hookbridge": frozenset({"hook"}), "eyes": frozenset({"screen"})}
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
    def lebt(self):
        return self.pid > 0


def socket_path(runtime_dir: Path, produzent: str) -> Path:
    return runtime_dir / f"{produzent}.sock"


def listen(runtime_dir: Path, produzent: str, *, backlog: int = 8):
    if produzent not in PRODUZENTEN:
        raise ValueError(produzent)
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = socket_path(runtime_dir, produzent)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    os.chmod(path, 0o600)
    srv.listen(backlog)
    return srv


def peer_of(conn: socket.socket, produzent: str) -> Peer:
    pidfd = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    try:
        pid = _pid(pidfd)
        if pid < 0:
            raise PeerError("Peer gestorben")
        ident = _identity(pid)
    except OSError as exc:
        raise PeerError(str(exc)) from exc
    finally:
        os.close(pidfd)
    return Peer(pid, ident.uid, ident.uid, ident.unit, produzent)


def pruefe_typ(produzent: str, typ: str):
    # MUTATION: jeder Typ geht auf jedem Socket durch.
    return None


def accept(srv, produzent, *, erlaubte_uid=None, erlaubte_units=None, audit=None):
    conn, _ = srv.accept()
    try:
        peer = peer_of(conn, produzent)
        uid = os.getuid() if erlaubte_uid is None else erlaubte_uid
        if peer.uid != uid:
            if audit:
                audit("uid_abweichung", peer)
            raise PeerError("uid")
        if erlaubte_units is not None and peer.unit not in set(erlaubte_units):
            if audit:
                audit("fremde_unit", peer)
            raise PeerError("Unit")
        return conn, peer
    except PeerError:
        conn.close()
        raise
