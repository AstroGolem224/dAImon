"""Eine Datei so schreiben, dass ein Stromausfall keinen halben Inhalt hinterlaesst.

Die Folge ist immer dieselbe: temporaere Datei **im Zielverzeichnis** (damit
`rename` nicht ueber Dateisystemgrenzen geht), schreiben, `flush`, `fsync`,
`os.replace` -- und danach `fsync` auf das **Verzeichnis**, damit auch der neue
Name auf der Platte steht und nicht nur der Inhalt.

Stand vor diesem Modul in `daimon/hub/tickets.py._schreiben()` inline. Beim
zweiten Aufrufer (der Abkuehlung aus T-3.9) waere das eine Kopie geworden --
und eine Kopie einer fsync-Folge ist genau die Art Duplikat, bei dem spaeter
eine Haelfte korrigiert wird und die andere nicht.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def schreibe_atomar(pfad: Path, nutzlast: bytes, *, modus: int = 0o600) -> None:
    """`nutzlast` nach `pfad`. Entweder ganz oder gar nicht.

    `modus` wird auf der temporaeren Datei gesetzt, **vor** dem `replace` --
    danach waere zwischen Sichtbarkeit und `chmod` ein Fenster, in dem die Datei
    mit den Rechten aus der umask dasteht.
    """
    pfad = Path(pfad)
    verzeichnis = pfad.parent
    verzeichnis.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=verzeichnis, prefix=pfad.name + ".",
                                    suffix=".tmp")
    try:
        os.chmod(tmp_name, modus)
        with os.fdopen(fd, "wb") as fh:
            fh.write(nutzlast)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, pfad)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    dir_fd = os.open(verzeichnis, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
