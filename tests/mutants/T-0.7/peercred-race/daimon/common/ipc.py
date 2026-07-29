"""Mutante: funktional, aber mit PID-Wiederverwendungsrennen.

Die Peer-Pruefung ist nur ein Wegweiser, keine Authentifizierung gegen
Codeausfuehrung unter derselben uid.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
import struct
from typing import Callable

SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)
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


def _unit_for_pid(pid: int) -> str:
    units = []
    with open(f"/proc/{pid}/cgroup", encoding="ascii") as cgroup:
        for line in cgroup:
            units.extend(
                part for part in line.rstrip().split(":", 2)[-1].split("/")
                if part.endswith((".service", ".scope"))
            )
    if not units:
        raise PeerRejected("keine Unit")
    return units[-1]


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
    # FALSCH: PID und uid kommen aus SO_PEERCRED; /proc wird erst danach gelesen.
    raw = conn.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, _gid = struct.unpack("3i", raw)
    try:
        unit = _unit_for_pid(pid)
    except (OSError, PeerRejected) as exc:
        _reject(conn, f"Peer nicht aufloesbar: {exc}", audit)
    if message_type not in PRODUCER_TYPES.get(socket_producer, ()):
        _reject(conn, "Nachrichtentyp passt nicht zum Socket", audit)
    if uid != expected_uid:
        _reject(conn, "uid stimmt nicht ueberein", audit)
    if unit != expected_unit:
        _reject(conn, "systemd-Unit stimmt nicht ueberein", audit)
    return PeerIdentity(pid=pid, uid=uid, unit=unit)


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
    # MUTATION: SO_PEERCRED liest eine wiederverwendbare PID, /proc erst spaeter.
    raw = conn.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    try:
        unit = _unit_for_pid(pid)
    except OSError as exc:
        raise PeerError(str(exc)) from exc
    return Peer(pid, uid, gid, unit, produzent)


def pruefe_typ(produzent: str, typ: str):
    if typ not in PRODUZENTEN.get(produzent, ()):
        raise MessageTypeError(typ)


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
