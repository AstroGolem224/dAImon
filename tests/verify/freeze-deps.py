#!/usr/bin/env python3
"""Geschlossene Abhaengigkeitspruefung fuer eingefrorene Verifizierer."""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path

PFAD = re.compile(r"^tests/(?:verify|harness)/[A-Za-z0-9_.+@/-]+$")
TASK = re.compile(r"^T--?[0-9]+(?:\.[0-9]+)*(?:\.v[0-9]+)?$")
SHELL_REPO = re.compile(r"\$\{?REPO\}?/(tests/(?:verify|harness)/[A-Za-z0-9_.+@/-]+)")
SHELL_HIER = re.compile(r"\$\{?HIER\}?/([A-Za-z0-9_.+@-]+\.(?:sh|py))")
SHELL_RELATIV = re.compile(r"(?<![A-Za-z0-9_./-])((?:\./|\.\./)+[A-Za-z0-9_.+@/-]+\.(?:sh|py))")
SHELL_DIRNAME = re.compile(
    r"\$\(\s*dirname\b[^)\n]*(?:\$0|BASH_SOURCE\[0\])[^)\n]*\)\s*/\s*"
    r"([A-Za-z0-9_.+@-]+\.(?:sh|py))"
)
LITERAL = re.compile(r"tests/(?:verify|harness)/[A-Za-z0-9_.+@/-]+\.(?:sh|py)")


class Fehler(Exception):
    pass


def normalisiere(pfad: str) -> str:
    if not PFAD.fullmatch(pfad) or ".." in Path(pfad).parts:
        raise Fehler(f"Deklarationsgrammatik: unzulaessiger Pfad {pfad!r}")
    return pfad


def deklarationen(pfad: Path) -> dict[str, set[str]]:
    kanten: dict[str, set[str]] = defaultdict(set)
    for nr, roh in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        zeile = roh.strip()
        if not zeile or zeile.startswith("#"):
            continue
        teile = zeile.split()
        if len(teile) != 2:
            raise Fehler(f"Deklarationsgrammatik: {pfad}:{nr}: genau zwei Literalpfade erwartet")
        quelle, ziel = map(normalisiere, teile)
        if ziel in kanten[quelle]:
            raise Fehler(f"Deklarationsgrammatik: {pfad}:{nr}: doppelte Kante {quelle} -> {ziel}")
        kanten[quelle].add(ziel)
    return kanten


def ohne_shell_kommentare(text: str) -> str:
    # Kommentare tragen Beispiele und Angriffspfade; sie sind keine Aufrufe.
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def shell_funde(repo: Path, rel: str, text: str) -> set[str]:
    text = ohne_shell_kommentare(text)
    if re.search(r"\$\{?HIER\}?/\$|\$\{?HIER\}?/\$\{", text):
        raise Fehler(f"Abhaengigkeits-Entdeckung: {rel}: dynamischer $HIER-Pfad ist nicht statisch aufloesbar")
    if re.search(r"\$\{?REPO\}?/tests/(?:verify|harness)/\$", text):
        raise Fehler(f"Abhaengigkeits-Entdeckung: {rel}: dynamischer Framework-Pfad ist nicht statisch aufloesbar")
    funde = {p for p in SHELL_REPO.findall(text) if p.endswith((".sh", ".py"))}
    funde.update(f"tests/verify/{name}" for name in SHELL_HIER.findall(text))
    basis = Path(rel).parent
    funde.update((basis / name).as_posix() for name in SHELL_DIRNAME.findall(text))
    for roh in SHELL_RELATIV.findall(text):
        kandidat = (repo / basis / roh).resolve(strict=False)
        try:
            relativ = kandidat.relative_to(repo.resolve()).as_posix()
        except ValueError:
            continue
        if relativ.startswith(("tests/verify/", "tests/harness/")):
            funde.add(relativ)
    return funde


def python_funde(repo: Path, rel: str, text: str) -> set[str]:
    try:
        baum = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        raise Fehler(f"Abhaengigkeits-Entdeckung: {rel}: Python nicht parsebar: {exc}") from exc
    funde = set(LITERAL.findall(text))
    basis = Path(rel).parent
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module = [a.name for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom) and knoten.level == 0 and knoten.module:
            module = [knoten.module]
        else:
            module = []
        for name in module:
            kandidat = basis / (name.replace(".", "/") + ".py")
            if (repo / kandidat).is_file():
                funde.add(kandidat.as_posix())
        if isinstance(knoten, ast.Call):
            name = ""
            if isinstance(knoten.func, ast.Name):
                name = knoten.func.id
            elif isinstance(knoten.func, ast.Attribute):
                name = knoten.func.attr
            if name in {"import_module", "__import__"} and knoten.args:
                arg = knoten.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.startswith(("tests.verify", "tests.harness")):
                        funde.add(arg.value.replace(".", "/") + ".py")
                elif "tests/verify" in ast.unparse(knoten) or "tests.harness" in ast.unparse(knoten):
                    raise Fehler(f"Abhaengigkeits-Entdeckung: {rel}: dynamischer repo-lokaler Python-Import")
    # Die Schreibweise des String-Literals darf die Erkennung nicht steuern.
    dynamische_shellpfade = [n for n in ast.walk(baum) if isinstance(n, ast.JoinedStr)
                             and ".sh" in "".join(
                                 x.value for x in n.values
                                 if isinstance(x, ast.Constant) and isinstance(x.value, str))]
    if rel != "tests/verify/freeze-deps.py" and dynamische_shellpfade:
        tasks = {n.value for n in ast.walk(baum)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str) and TASK.fullmatch(n.value)}
        if not tasks:
            raise Fehler(f"Abhaengigkeits-Entdeckung: {rel}: dynamischer Verifiziererpfad ohne endliche Literalmenge")
        funde.update(f"tests/verify/{t}.sh" for t in tasks)
    return funde


def entdecke(repo: Path, rel: str) -> set[str]:
    voll = repo / rel
    if not voll.is_file():
        raise Fehler(f"Abhaengigkeits-Entdeckung: {rel} fehlt")
    text = voll.read_text(encoding="utf-8", errors="strict")
    if rel.endswith(".sh"):
        funde = shell_funde(repo, rel, text)
    elif rel.endswith(".py"):
        funde = python_funde(repo, rel, text)
    else:
        funde = set()
    return {p for p in funde if p != rel and (repo / p).is_file()}


def inventar(pfad: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for nr, roh in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        zeile = roh.strip()
        if not zeile or zeile.startswith("#"):
            continue
        teile = zeile.split()
        if len(teile) != 2 or not re.fullmatch(r"[0-9a-f]{64}", teile[0]):
            raise Fehler(f"Manifest: {pfad}:{nr}: '<sha256> <pfad>' erwartet")
        result[normalisiere(teile[1])] = teile[0]
    return result


def pruefen(repo: Path, deps_pfad: Path, wurzeln: list[str], manifest: Path | None) -> list[str]:
    kanten = deklarationen(deps_pfad)
    gesehen: set[str] = set()
    stapel = [normalisiere(w) for w in wurzeln]
    while stapel:
        rel = stapel.pop()
        if rel in gesehen:
            continue
        gesehen.add(rel)
        gefunden = entdecke(repo, rel)
        fehlt = sorted(gefunden - kanten.get(rel, set()))
        if fehlt:
            raise Fehler(f"Abhaengigkeits-Entdeckung: {rel}: nicht deklariert: {', '.join(fehlt)}")
        for ziel in sorted(kanten.get(rel, set())):
            if not (repo / ziel).is_file():
                raise Fehler(f"Abhaengigkeits-Entdeckung: deklarierte Datei fehlt: {ziel}")
            stapel.append(ziel)
    if manifest is not None:
        vorhanden = inventar(manifest)
        for rel, soll in vorhanden.items():
            voll = repo / rel
            if not voll.is_file():
                raise Fehler(f"Hashvergleich: Datei fehlt: {rel}")
            ist = hashlib.sha256(voll.read_bytes()).hexdigest()
            if ist != soll:
                raise Fehler(f"Hashvergleich: {rel}: erwartet {soll}, gefunden {ist}")
        fehlt = sorted(gesehen - vorhanden.keys())
        if fehlt:
            raise Fehler("Abhaengigkeits-Entdeckung: nicht im FROZEN-Manifest: " + ", ".join(fehlt))
    return sorted(gesehen)


def spur_pruefen(repo: Path, erlaubt: set[str], log: Path) -> None:
    nicht_erlaubt: set[str] = set()
    for zeile in log.read_text(encoding="utf-8", errors="replace").splitlines():
        for roh in re.findall(r'"((?:[^"\\]|\\.)*)"', zeile):
            pfad = bytes(roh, "utf-8").decode("unicode_escape", errors="replace")
            p = Path(pfad)
            if not p.is_absolute():
                if pfad.startswith("tests/"):
                    p = repo / p
                else:
                    continue
            try:
                rel = p.resolve(strict=False).relative_to(repo).as_posix()
            except (ValueError, OSError):
                continue
            if rel.startswith(("tests/verify/", "tests/harness/")) and rel not in erlaubt:
                if rel.endswith((".sh", ".py")):
                    nicht_erlaubt.add(rel)
    if nicht_erlaubt:
        raise Fehler("Laufzeitspur: nicht deklarierte repo-lokale Datei geoeffnet: " + ", ".join(sorted(nicht_erlaubt)))


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="befehl", required=True)
    q = sub.add_parser("pruefen")
    q.add_argument("--repo", type=Path, required=True)
    q.add_argument("--deps", type=Path, required=True)
    q.add_argument("--manifest", type=Path)
    q.add_argument("wurzeln", nargs="+")
    s = sub.add_parser("spur")
    s.add_argument("--repo", type=Path, required=True)
    s.add_argument("--erlaubt", type=Path, required=True)
    s.add_argument("--log", type=Path, required=True)
    a = p.parse_args()
    try:
        repo = a.repo.resolve()
        if a.befehl == "pruefen":
            for rel in pruefen(repo, a.deps, a.wurzeln, a.manifest):
                print(rel)
        else:
            erlaubt = {z.strip() for z in a.erlaubt.read_text().splitlines() if z.strip()}
            spur_pruefen(repo, erlaubt, a.log)
    except (Fehler, OSError, UnicodeError) as exc:
        print(f"freeze-deps: ABGELEHNT — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
