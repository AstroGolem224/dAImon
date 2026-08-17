"""T-4.8 — Umkehrbarkeit wird HERGESTELLT und GEPRUEFT, bevor mutiert wird.

Die Reihenfolge ist die ganze Zusage
----------------------------------------------------------------------------
1. Undo-Artefakt anlegen,
2. Artefakt **verifizieren** (existiert, lesbar, erwartete Groesse),
3. erst dann herabstufen auf `reversible` und die Mutation zulassen.

Faellt Schritt 1 oder 2, wird die Mutation **abgebrochen** -- nicht
ungeschuetzt ausgefuehrt. Das ist der Unterschied zwischen "wir haben ein
Backup" und "wir haben es versucht". Ein `cp`, dessen Rueckgabewert 0 ist,
waehrend das Ziel-Dateisystem voll lief, hat kein Artefakt hinterlassen,
sondern eine Behauptung.

Drei Faelle, drei Artefakte (Design 6.6)
----------------------------------------------------------------------------
* **Loeschen** -> XDG-Trash mit `.trashinfo`. Kein `unlink`.
* **Ueberschreiben** -> Kopie in die Undo-Ablage, moeglichst `--reflink`
  (kostenlos auf btrfs/XFS, faellt sonst auf eine echte Kopie zurueck).
* **Git-Verwerfen** -> vorher `git stash`.

Die Dateisystemgrenze ist keine Kleinigkeit
----------------------------------------------------------------------------
Der XDG-Trash ist je Dateisystem definiert (`$topdir/.Trash-$uid`). Eine
Datei ueber eine Grenze in den Home-Trash zu SCHIEBEN heisst kopieren und
loeschen -- und genau dazwischen liegt der Moment, in dem beides halb
passiert ist. Dieser Broker weist den Fall deshalb ab, statt ihn zu
ueberspielen: `st_dev` von Quelle und Trash muessen gleich sein.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class UndoFehler(RuntimeError):
    """Das Artefakt kam nicht zustande. Die Mutation unterbleibt."""


@dataclass(frozen=True)
class Artefakt:
    """Was hergestellt wurde, und womit es sich zurueckholen laesst."""

    art: str            # "trash" | "kopie" | "git-stash"
    pfad: Path | None   # wo das Artefakt liegt
    quelle: Path | None
    groesse: int | None
    verifiziert: bool
    hinweis: str = ""


def _trash_wurzel(ziel: Path) -> Path:
    """`$XDG_DATA_HOME/Trash` -- der Home-Trash, nicht der je Datentraeger."""
    basis = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(basis) / "Trash"


def _gleiches_dateisystem(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _verifizieren(pfad: Path, erwartet: int) -> None:
    """Lesbar und in erwarteter Groesse -- gemessen, nicht geglaubt."""
    try:
        groesse = pfad.stat().st_size
    except OSError as fehler:
        raise UndoFehler(f"Artefakt {pfad} nicht auffindbar: {fehler}") from fehler
    if groesse != erwartet:
        raise UndoFehler(
            f"Artefakt {pfad} ist {groesse} Bytes gross, erwartet {erwartet} "
            f"-- unvollstaendig geschrieben (volles Dateisystem?)")
    try:
        with pfad.open("rb") as fh:
            fh.read(1)
    except OSError as fehler:
        raise UndoFehler(f"Artefakt {pfad} nicht lesbar: {fehler}") from fehler


def in_den_trash(quelle: Path, *, jetzt: float | None = None,
                 trash: Path | None = None) -> Artefakt:
    """Loeschen heisst hier: verschieben, mit Zettel."""
    quelle = Path(quelle)
    if not quelle.is_file():
        raise UndoFehler(f"{quelle} ist keine Datei")
    wurzel = Path(trash) if trash else _trash_wurzel(quelle)
    dateien, infos = wurzel / "files", wurzel / "info"
    dateien.mkdir(parents=True, exist_ok=True)
    infos.mkdir(parents=True, exist_ok=True)

    if not _gleiches_dateisystem(quelle.parent, dateien):
        # Abgewiesen statt kopiert: ein Kopieren-und-Loeschen ueber die
        # Grenze hat einen Moment, in dem beides halb passiert ist.
        raise UndoFehler(
            f"{quelle} liegt auf einem anderen Dateisystem als {dateien}; "
            f"ein Verschieben waere hier ein Kopieren mit anschliessendem "
            f"Loeschen")

    groesse = quelle.stat().st_size
    name = quelle.name
    ziel = dateien / name
    n = 1
    while ziel.exists():
        ziel = dateien / f"{quelle.stem}.{n}{quelle.suffix}"
        n += 1
    info = infos / f"{ziel.name}.trashinfo"

    # Der Zettel ZUERST: eine Datei im Trash ohne `.trashinfo` ist fuer jeden
    # Dateimanager Muell ohne Herkunft und laesst sich nicht zurueckholen.
    stempel = time.strftime("%Y-%m-%dT%H:%M:%S",
                            time.localtime(jetzt if jetzt is not None else time.time()))
    info.write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(quelle.resolve()))}\n"
        f"DeletionDate={stempel}\n", encoding="utf-8")
    os.replace(quelle, ziel)
    _verifizieren(ziel, groesse)
    if not info.is_file():
        raise UndoFehler(f"{info} fehlt -- der Trash-Eintrag waere herkunftslos")
    return Artefakt(art="trash", pfad=ziel, quelle=quelle, groesse=groesse,
                    verifiziert=True)


def kopie_anlegen(quelle: Path, ablage: Path, *,
                  lauf: Callable[..., Any] = subprocess.run) -> Artefakt:
    """Vor dem Ueberschreiben. `--reflink=auto`, sonst echte Kopie."""
    quelle, ablage = Path(quelle), Path(ablage)
    if not quelle.is_file():
        raise UndoFehler(f"{quelle} ist keine Datei")
    ablage.mkdir(parents=True, exist_ok=True)
    groesse = quelle.stat().st_size
    ziel = ablage / f"{quelle.name}.{int(time.time() * 1000)}"

    e = lauf(["cp", "--reflink=auto", "--preserve=all", "--no-clobber",
              str(quelle), str(ziel)], capture_output=True, text=True,
             timeout=60)
    if int(getattr(e, "returncode", 1)) != 0:
        raise UndoFehler(
            f"Kopie nach {ziel} fehlgeschlagen: "
            f"{(getattr(e, 'stderr', '') or '').strip()[:160]}")
    # `cp` meldet auch dann 0, wenn der letzte Block auf ein volles
    # Dateisystem lief -- deshalb wird hier gemessen und nicht geglaubt.
    _verifizieren(ziel, groesse)
    return Artefakt(art="kopie", pfad=ziel, quelle=quelle, groesse=groesse,
                    verifiziert=True)


def stash_anlegen(repo: Path, *,
                  lauf: Callable[..., Any] = subprocess.run) -> Artefakt:
    """Vor `git checkout -- .` und Verwandten."""
    repo = Path(repo)
    vorher = lauf(["git", "-C", str(repo), "stash", "list"],
                  capture_output=True, text=True, timeout=60)
    anzahl_vorher = len((vorher.stdout or "").strip().splitlines())

    e = lauf(["git", "-C", str(repo), "stash", "push", "--include-untracked",
              "-m", "daimon-undo"], capture_output=True, text=True, timeout=120)
    if int(getattr(e, "returncode", 1)) != 0:
        raise UndoFehler(
            f"git stash fehlgeschlagen: "
            f"{(getattr(e, 'stderr', '') or '').strip()[:160]}")

    nachher = lauf(["git", "-C", str(repo), "stash", "list"],
                   capture_output=True, text=True, timeout=60)
    zeilen = (nachher.stdout or "").strip().splitlines()
    if len(zeilen) != anzahl_vorher + 1:
        # "No local changes to save" liefert ebenfalls 0. Ohne diese Zaehlung
        # haetten wir ein Artefakt behauptet, das es nicht gibt.
        raise UndoFehler(
            "git stash hat nichts abgelegt -- kein Artefakt, keine Mutation")
    return Artefakt(art="git-stash", pfad=None, quelle=repo, groesse=None,
                    verifiziert=True, hinweis=zeilen[0])


def vorbereiten(art: str, **kw) -> Artefakt:
    """Der eine Weg. Wirft `UndoFehler`, und dann unterbleibt die Mutation.

    Keine Rueckgabe `None` und kein `ok`-Feld: ein Aufrufer, der ein Feld
    pruefen muss, vergisst es -- eine Ausnahme kann er nicht uebersehen.
    """
    if art == "trash":
        return in_den_trash(**kw)
    if art == "kopie":
        return kopie_anlegen(**kw)
    if art == "git-stash":
        return stash_anlegen(**kw)
    raise UndoFehler(f"unbekannte Undo-Art {art!r}")


def wiederherstellen(artefakt: Artefakt) -> Path:
    """Zurueck an die Quelle. Nur fuer `trash` und `kopie`.

    `git-stash` bleibt bewusst dem Menschen: ein `stash pop` kann in einen
    Konflikt laufen, und ein Broker, der Konflikte aufloest, entscheidet
    ueber fremde Arbeit.
    """
    if artefakt.art == "git-stash":
        raise UndoFehler(
            "git-stash wird nicht automatisch zurueckgeholt: `git stash pop` "
            f"({artefakt.hinweis}) gehoert einem Menschen")
    if not (artefakt.pfad and artefakt.quelle):
        raise UndoFehler("Artefakt ohne Pfad")
    ziel = Path(artefakt.quelle)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if _gleiches_dateisystem(Path(artefakt.pfad).parent, ziel.parent):
        os.replace(artefakt.pfad, ziel)
    else:
        shutil.copy2(artefakt.pfad, ziel)
        Path(artefakt.pfad).unlink()
    return ziel
