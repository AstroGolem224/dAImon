#!/usr/bin/env bash
# Verifizierer fuer T-0.7: IPC-Peer-Pruefung.
#
# Die Laufzeitprobe arbeitet mit echten Unix-Sockets und echten /proc-Daten.
# Der Quelltexttest auf SO_PEERPIDFD ist absichtlich zusaetzlich enthalten:
# Er ist kein Beweis gegen jedes Rennen, faengt aber den wahrscheinlichen
# Rueckschritt ab, spaeter wieder auf den bequemeren SO_PEERCRED-Weg zu wechseln.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1; pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." || exit 1; pwd)"
PY="$REPO/.venv/bin/python"
TARGET="${DAIMON_FIXTURE:-$REPO}"
SOURCE="$TARGET/daimon/common/ipc.py"
TESTS="$TARGET/tests/test_ipc.py"

fail=0
chk() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (erwartet $3, war $2)"; fail=1; fi; }

echo "T-0.7 — IPC-Peer-Pruefung"
source_exists=nein
tests_exist=nein
python_exists=nein
if [[ -f "$SOURCE" ]]; then source_exists=ja; fi
if [[ -f "$TESTS" ]]; then tests_exist=ja; fi
if [[ -x "$PY" ]]; then python_exists=ja; fi
chk "ipc.py existiert" "$source_exists" ja
chk "tests/test_ipc.py existiert" "$tests_exist" ja
chk "venv-Python vorhanden" "$python_exists" ja

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
xml="$tmp/pytest.xml"

pytest_rc=127
if [[ -x "$PY" ]]; then
  if [[ -f "$TESTS" ]]; then
    (
      cd "$TARGET" || exit 1
      PYTHONDONTWRITEBYTECODE=1 timeout 60s "$PY" -m pytest tests/test_ipc.py \
        --tb=no -p no:cacheprovider --junitxml="$xml"
    ) >/dev/null 2>&1
    pytest_rc=$?
  fi
fi

passed=0
failed=0
errors=0
if [[ -s "$xml" ]]; then
  read -r passed failed errors <<<"$("$PY" - "$xml" <<'PYEOF'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
passed = failed = errors = 0
for case in root.iter("testcase"):
    kinds = {child.tag for child in case}
    if "error" in kinds:
        errors += 1
    elif "failure" in kinds:
        failed += 1
    elif "skipped" not in kinds:
        passed += 1
print(passed, failed, errors)
PYEOF
)"
fi
echo "  pytest: $passed bestanden, $failed fehlgeschlagen, $errors Fehler"
chk "pytest endet mit Status 0" "$pytest_rc" 0
chk "pytest hat keine fehlgeschlagenen Tests" "$failed" 0
chk "pytest hat keine Sammel- oder Laufzeitfehler" "$errors" 0
has_tests=nein
if [[ "$passed" -gt 0 ]]; then has_tests=ja; fi
chk "pytest fuehrt mindestens einen Test aus" "$has_tests" ja

# Quelltext-Gegenprobe: bewusst schwach, siehe Kommentar am Dateianfang.
pidfd_source=nein
peercred_source=nein
wegweiser=nein
if [[ -f "$SOURCE" ]]; then
  pidfd_source="$("$PY" - "$SOURCE" <<'PYEOF'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
print("ja" if re.search(r"\bSO_PEERPIDFD\b|(?<!\d)77(?!\d)", text) else "nein")
PYEOF
)"
  peercred_source="$("$PY" - "$SOURCE" <<'PYEOF'
import ast
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
lines = text.splitlines()
hits = [i for i, line in enumerate(lines) if "SO_PEERCRED" in line]
tree = ast.parse(text)
executable = any(
    (isinstance(node, ast.Name) and node.id == "SO_PEERCRED")
    or (isinstance(node, ast.Attribute) and node.attr == "SO_PEERCRED")
    for node in ast.walk(tree)
)
ok = not executable
for i in hits:
    nearby = "\n".join(lines[max(0, i - 6):i + 7])
    if not re.search(r"nicht|falsch|Rennen|race|Rueckfall", nearby, re.IGNORECASE):
        ok = False
print("ja" if ok else "nein")
PYEOF
)"
  wegweiser="$("$PY" - "$SOURCE" <<'PYEOF'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
ok = "Wegweiser" in text and bool(
    re.search(r"keine\s+Authentifizierung|nicht\s+.*Authentifizierung", text, re.I)
)
print("ja" if ok else "nein")
PYEOF
)"
fi
chk "Quelltext verwendet SO_PEERPIDFD beziehungsweise 77" "$pidfd_source" ja
chk "SO_PEERCRED ist nicht unkommentierte Autorisierungsquelle" "$peercred_source" ja
chk "Peer-Pruefung ist als Wegweiser, nicht Authentifizierung dokumentiert" "$wegweiser" ja

# Eigene Laufzeitprobe, unabhaengig von Behauptungen in tests/test_ipc.py.
probe=""
runtime="$tmp/runtime"
mkdir -m 700 "$runtime"
if [[ -f "$SOURCE" ]]; then
  if [[ -x "$PY" ]]; then
    probe="$(
      cd "$TARGET" || exit 1
      XDG_RUNTIME_DIR="$runtime" PYTHONDONTWRITEBYTECODE=1 \
        timeout 25s "$PY" - <<'PYEOF' 2>&1
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

from daimon.common import ipc


def sag(name: str, value: bool) -> None:
    print(f"{name}={'ja' if value else 'nein'}")


def current_unit() -> str:
    units = []
    with open("/proc/self/cgroup", encoding="ascii") as cgroup:
        for line in cgroup:
            units.extend(
                part
                for part in line.rstrip().split(":", 2)[-1].split("/")
                if part.endswith((".service", ".scope"))
            )
    return units[-1]


runtime = Path(os.environ["XDG_RUNTIME_DIR"])
socket_dir = runtime / "daimon"
eyes = ipc.listen(socket_dir, "eyes")
hook = ipc.listen(socket_dir, "hookbridge")
for listener in (eyes, hook):
    listener.settimeout(2.0)

sag("eyes_unter_runtime", Path(eyes.getsockname()).parent == socket_dir)
sag("hook_unter_runtime", Path(hook.getsockname()).parent == socket_dir)
sag("eyes_modus_0600", (os.stat(eyes.getsockname()).st_mode & 0o777) == 0o600)
sag("hook_modus_0600", (os.stat(hook.getsockname()).st_mode & 0o777) == 0o600)


def connect(listener):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    client.connect(listener.getsockname())
    return client


unit = current_unit()
client = connect(eyes)
positive = False
try:
    server, peer = ipc.accept(
        eyes, "eyes", erlaubte_uid=os.getuid(), erlaubte_units={unit}
    )
    ipc.pruefe_typ("eyes", "screen")
    positive = peer.pid == os.getpid() and peer.uid == os.getuid()
    server.close()
except Exception:
    positive = False
client.close()
sag("positiv_kanarienvogel", positive)

try:
    ipc.pruefe_typ("eyes", "hook")
    wrong_type = False
except ipc.MessageTypeError:
    wrong_type = True
sag("falscher_typ", wrong_type)

try:
    ipc.pruefe_typ("hookbridge", "screen")
    wrong_socket = False
except ipc.MessageTypeError:
    wrong_socket = True
sag("falscher_socket", wrong_socket)

client = connect(eyes)
uid_rejected = False
accepted = None
try:
    accepted, _ = ipc.accept(eyes, "eyes", erlaubte_uid=os.getuid() + 1)
except ipc.PeerError:
    uid_rejected = True
except Exception:
    uid_rejected = True
if accepted is not None:
    accepted.close()
sag("falsche_uid", uid_rejected)
try:
    uid_closed = client.recv(1) == b""
except OSError:
    uid_closed = True
sag("falsche_uid_verbindung_ab", uid_closed)
client.close()

client = connect(eyes)
audit = []
unit_rejected = False
accepted = None
try:
    accepted, _ = ipc.accept(
        eyes,
        "eyes",
        erlaubte_uid=os.getuid(),
        erlaubte_units={"fremde-unit.service"},
        audit=lambda event, peer: audit.append((event, peer.pid)),
    )
except ipc.PeerError:
    unit_rejected = True
except Exception:
    unit_rejected = True
if accepted is not None:
    accepted.close()
sag("fremde_unit", unit_rejected)
sag("fremde_unit_audit", bool(audit and audit[0][0] == "fremde_unit"))
try:
    unit_closed = client.recv(1) == b""
except OSError:
    unit_closed = True
sag("fremde_unit_verbindung_ab", unit_closed)
client.close()

# Den pidfd halten, waehrend der Peer stirbt. Danach muss fdinfo wirklich
# `Pid: -1` zeigen; anschliessend muss die Implementierung denselben Fall
# ablehnen und darf weder abstuerzen noch akzeptieren.
child_dir = Path(tempfile.mkdtemp(dir=runtime)) / "daimon"
child_listener = ipc.listen(child_dir, "eyes")
child_listener.settimeout(2.0)
code = (
    "import socket,time,sys;"
    "s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);"
    "time.sleep(30)"
)
child = subprocess.Popen([sys.executable, "-c", code, child_listener.getsockname()])
dead_server, _ = child_listener.accept()
dead_server.settimeout(2.0)
held_pidfd = dead_server.getsockopt(socket.SOL_SOCKET, 77)
child.terminate()
try:
    child.wait(timeout=3)
except subprocess.TimeoutExpired:
    child.kill()
    child.wait(timeout=3)
with open(f"/proc/self/fdinfo/{held_pidfd}", encoding="ascii") as info:
    dead_pid = int(next(line for line in info if line.startswith("Pid:")).split(":")[1])
sag("fdinfo_pid_minus_eins", dead_pid == -1)
os.close(held_pidfd)

dead_rejected = False
try:
    ipc.peer_of(dead_server, "eyes")
except ipc.PeerError:
    dead_rejected = True
except Exception:
    dead_rejected = True
sag("gestorbener_peer_abgewiesen", dead_rejected)
dead_server.close()
child_listener.close()
eyes.close()
hook.close()
PYEOF
    )"
  fi
fi

for key in eyes_unter_runtime hook_unter_runtime eyes_modus_0600 hook_modus_0600 \
           positiv_kanarienvogel falscher_typ falscher_socket \
           falsche_uid falsche_uid_verbindung_ab \
           fremde_unit fremde_unit_audit fremde_unit_verbindung_ab \
           fdinfo_pid_minus_eins gestorbener_peer_abgewiesen; do
  wert="$(grep -oP "(?<=^${key}=).*" <<<"$probe" | head -1)"
  chk "eigene Probe: $key" "$wert" ja
done

exit $fail
