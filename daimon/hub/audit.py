"""T-4.6 — das Audit: eine Hash-Kette, verankert im Journal.

Was diese Kette kann, und was nicht
----------------------------------------------------------------------------
Sie macht **Manipulation erkennbar**, nicht unmöglich. Wer unter derselben
uid Code ausfuehrt, kann die Datei loeschen, ersetzen oder neu berechnen --
das steht so im Bedrohungsmodell und wird hier nicht anders behauptet.
Erkennbar wird es durch den zweiten Strom: der Kettenkopf geht periodisch und
bei jeder Rotation ins **systemd-Journal**. Eine in sich stimmige, komplett
neu gerechnete Datei faellt genau daran auf, denn sie muesste auch die
Journal-Anker der Vergangenheit treffen.

`chattr +a` waere die naheliegende Verstaerkung und ist als Nutzer nicht
setzbar. Es wird deshalb nicht behauptet.

Drei benannte Pruefstellen, keine davon mit Modelltext im Prozess
----------------------------------------------------------------------------
1. `daimon-hub` beim Start (Abweichung -> dringende Blase),
2. ein systemd-Timer taeglich,
3. `python -m daimon.hub.audit --verify` fuer den Nutzer.

Findet keine davon statt, ist die Kette wertlos und gehoert gestrichen statt
behauptet (Design 7.6).

Redaktion nach HERKUNFT, nicht nach Katalogflag
----------------------------------------------------------------------------
Jeder Wert, der als `tainted` hereinkommt, wird zu
`<redacted:sha256:...:len=N>` -- unabhaengig davon, ob der Katalog ihn als
`sensitive` fuehrt. Das Katalogflag ist eine ZUSAETZLICHE Bedingung, keine
alternative: waere es die einzige, entschiede eine Liste darueber, ob
fremder Bildschirmtext im Klartext im Audit landet.

Die Laenge bleibt sichtbar. Sie verraet wenig und beantwortet die Frage, die
man im Nachhinein wirklich hat: war da etwas, und wieviel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = "daimon.audit.v1"
DATEI = "audit.jsonl"
# Der Anker im Journal. Ein Praefix, das `journalctl --grep` findet, und ein
# Feld, das strukturiert danebensteht.
ANKER_PRAEFIX = "AUDIT-ANKER"

# Design 7.6. Fehlt eines davon, wird der Datensatz nicht geschrieben: ein
# Audit mit halben Datensaetzen beantwortet die Frage nicht, fuer die es da
# ist.
PFLICHTFELDER = ("prompt_shown", "params_hash", "mark_id", "initiator",
                 "turn_id", "tool_use_id", "outcome")

# `unknown` steht ausdruecklich dabei: eine Ausfuehrung, deren Ausgang wir
# nicht kennen, ist ein eigener Zustand und kein Misserfolg.
AUSGAENGE = ("ok", "failed", "denied", "cancelled", "timeout", "unknown")


class AuditFehler(ValueError):
    """Der Datensatz oder die Kette ist unbrauchbar. Nennt die Stelle."""


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def redigieren(wert: Any) -> str:
    """Aus einem Wert wird sein Abdruck und seine Laenge."""
    text = wert if isinstance(wert, str) else json.dumps(
        wert, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str)
    return f"<redacted:{_hash(text)}:len={len(text)}>"


def _zeile(datensatz: dict) -> str:
    return json.dumps(datensatz, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


@dataclass
class Audit:
    verzeichnis: Path
    _seq: int = 0
    _prev: str = ""

    @classmethod
    def oeffnen(cls, verzeichnis: Path | str) -> "Audit":
        pfad = Path(verzeichnis)
        pfad.mkdir(parents=True, exist_ok=True)
        # 0700: hier steht, was das Pet getan hat, samt Vorschautexten.
        os.chmod(pfad, 0o700)
        audit = cls(verzeichnis=pfad)
        audit._nachladen()
        return audit

    @property
    def datei(self) -> Path:
        return self.verzeichnis / DATEI

    def _nachladen(self) -> None:
        """Kopf und Nummer aus der vorhandenen Datei uebernehmen."""
        if not self.datei.is_file():
            return
        letzte = None
        for zeile in self.datei.read_text(encoding="utf-8").splitlines():
            if zeile.strip():
                letzte = zeile
        if letzte is None:
            return
        try:
            daten = json.loads(letzte)
        except ValueError as exc:
            raise AuditFehler(
                f"{self.datei}: letzte Zeile ist kein JSON ({exc}). Die Kette "
                f"wird NICHT fortgesetzt -- ein Anhaengen wuerde die "
                f"Beschaedigung ueberschreiben.") from exc
        self._seq = int(daten.get("seq", 0))
        self._prev = _kette_hash(daten)

    # -- Schreiben ----------------------------------------------------------

    def schreiben(self, *, tainted: Iterable[str] = (), ts: float | None = None,
                  **felder) -> dict:
        """Ein Datensatz. Alles in `tainted` wird redigiert, bevor es Bytes gibt.

        `tainted` nennt die FELDNAMEN, deren Werte aus fremder Quelle stammen
        -- Bildschirmtext, Zwischenablage, Modellausgabe. Die Redaktion
        passiert hier und nicht beim Aufrufer: ein Aufrufer, der sie vergisst,
        soll keinen Klartext hinterlassen koennen.
        """
        fehlend = [f for f in PFLICHTFELDER if f not in felder]
        if fehlend:
            raise AuditFehler(
                f"Pflichtfelder fehlen: {', '.join(fehlend)} (Design 7.6)")
        if felder["outcome"] not in AUSGAENGE:
            raise AuditFehler(
                f"outcome {felder['outcome']!r} ist keiner von "
                + ", ".join(AUSGAENGE))

        daten = dict(felder)
        for name in tainted:
            if name in daten:
                daten[name] = redigieren(daten[name])
        # Der Vorschautext ist IMMER redigiert: er enthaelt Parameterwerte,
        # und die stammen im Zweifel aus einer Modellausgabe. Sichtbar bleibt
        # sein Abdruck -- damit laesst sich pruefen, ob genau dieser Text
        # gezeigt wurde, ohne ihn aufzubewahren.
        daten["prompt_shown"] = redigieren(daten["prompt_shown"])

        self._seq += 1
        # Die Wanduhr, nicht die monotone: ein Audit beantwortet "wann war
        # das" und nicht "wieviel spaeter als der Start". Injizierbar, damit
        # ein Test eine Kette zweimal gleich schreiben kann -- und weil ohne
        # das jede Probe an der Uhr haenge statt am Inhalt.
        satz = {"schema": SCHEMA, "seq": self._seq, "prev_hash": self._prev,
                "ts": float(time.time() if ts is None else ts), **daten}
        zeile = _zeile(satz)
        with open(self.datei, "a", encoding="utf-8") as fh:
            fh.write(zeile + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(self.datei, 0o600)
        self._prev = _kette_hash(satz)
        return satz

    @property
    def kopf(self) -> str:
        return self._prev

    # -- Verankern und rotieren --------------------------------------------

    def verankern(self, *, journal: Any = None) -> str:
        """Den Kettenkopf ins Journal schreiben.

        Der zweite Strom. Ohne ihn ist die Kette nur gegen sich selbst
        pruefbar -- und eine komplett neu gerechnete Datei ist gegen sich
        selbst immer stimmig.
        """
        text = f"{ANKER_PRAEFIX} seq={self._seq} head={self._prev}"
        (journal or _ins_journal)(text)
        return text

    def rotieren(self, *, journal: Any = None) -> Path:
        """Die alte Datei zur Seite, der letzte Hash in die neue.

        Ohne den uebernommenen Hash begaenne nach jeder Rotation eine neue,
        unverbundene Kette -- und die Luecke waere genau die Stelle, an der
        sich etwas herausschneiden liesse.
        """
        self.verankern(journal=journal)
        ziel = self.verzeichnis / f"{DATEI}.{self._seq}"
        if self.datei.is_file():
            self.datei.rename(ziel)
        kopf = {"schema": SCHEMA, "seq": self._seq, "prev_hash": self._prev,
                "rotation_von": ziel.name}
        with open(self.datei, "w", encoding="utf-8") as fh:
            fh.write(_zeile(kopf) + "\n")
        os.chmod(self.datei, 0o600)
        self._prev = _kette_hash(kopf)
        return ziel


def _ins_journal(text: str) -> None:
    # `systemd-cat` statt einer Bibliothek: ein Anker, der nur mit einer
    # zusaetzlichen Abhaengigkeit geschrieben werden kann, wird bei ihrem
    # Fehlen still weggelassen.
    try:
        subprocess.run(["systemd-cat", "-t", "daimon-audit"],
                       input=text.encode("utf-8"), timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        # Kein stiller Erfolg: der Aufrufer erfaehrt es ueber die Rueckgabe
        # von `pruefe`, wenn spaeter ein Anker fehlt.
        print(f"Anker konnte nicht ins Journal: {text}", file=sys.stderr)


def _kette_hash(satz: dict) -> str:
    """Der Hash EINES Datensatzes, so wie er in der Datei steht."""
    return _hash(_zeile(satz))


def anker_aus_journal(*, seit: str = "-30d", lauf: Any = None) -> set[str]:
    """Die verankerten Koepfe aus dem Journal."""
    befehl = ["journalctl", "--user", "-t", "daimon-audit", "--since", seit,
              "--output", "cat", "--no-pager"]
    try:
        e = (lauf or subprocess.run)(befehl, capture_output=True, text=True,
                                     timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    koepfe = set()
    for zeile in (e.stdout or "").splitlines():
        if zeile.startswith(ANKER_PRAEFIX) and "head=" in zeile:
            koepfe.add(zeile.split("head=", 1)[1].strip())
    return koepfe


# -- Pruefen ----------------------------------------------------------------

def _saetze(pfad: Path) -> Iterator[dict]:
    for nummer, zeile in enumerate(
            pfad.read_text(encoding="utf-8").splitlines(), start=1):
        if not zeile.strip():
            continue
        try:
            yield json.loads(zeile)
        except ValueError as exc:
            raise AuditFehler(f"{pfad}:{nummer}: kein JSON ({exc})") from exc


def pruefe(verzeichnis: Path | str, *, anker: set[str] | None = None) -> dict:
    """Beide Stroeme. Gibt einen Befund zurueck, statt zu werfen.

    Geprueft wird die Kette gegen sich selbst UND gegen die Journal-Anker.
    Nur zusammen ergeben sie eine Aussage: die Kette allein faellt auf eine
    neu gerechnete Datei herein, die Anker allein sagen nichts ueber die
    Zeilen dazwischen.
    """
    pfad = Path(verzeichnis) / DATEI
    befund = {"datei": str(pfad), "saetze": 0, "ok": False, "fehler": [],
              "anker_gefunden": 0, "anker_getroffen": 0}
    if not pfad.is_file():
        befund["fehler"].append("Datei fehlt")
        return befund

    prev = ""
    erwartete_seq = None
    koepfe = []
    for satz in _saetze(pfad):
        befund["saetze"] += 1
        seq = satz.get("seq")
        if erwartete_seq is None:
            erwartete_seq = seq
        if seq != erwartete_seq:
            befund["fehler"].append(
                f"seq {seq!r} erwartet {erwartete_seq!r} -- Zeile geloescht, "
                f"eingefuegt oder vertauscht")
        if satz.get("prev_hash", "") != prev and befund["saetze"] > 1:
            befund["fehler"].append(f"prev_hash passt nicht bei seq={seq}")
        prev = _kette_hash(satz)
        koepfe.append(prev)
        erwartete_seq = (seq or 0) + 1

    anker = anker_aus_journal() if anker is None else anker
    befund["anker_gefunden"] = len(anker)
    befund["anker_getroffen"] = len(anker & set(koepfe))
    if anker and not (anker & set(koepfe)):
        # Die Kette ist in sich stimmig und trifft trotzdem keinen einzigen
        # Anker: genau das Bild einer ersetzten Datei.
        befund["fehler"].append(
            "kein einziger Journal-Anker liegt in dieser Kette -- die Datei "
            "wurde ersetzt")
    if not anker:
        befund["fehler"].append(
            "keine Journal-Anker gefunden; die Kette ist nur gegen sich "
            "selbst geprueft und damit gegen eine Neuberechnung blind")

    befund["ok"] = not befund["fehler"]
    return befund


def main(argv: list[str] | None = None) -> int:
    """Die dritte Pruefstelle: der Nutzer."""
    from daimon.common.config import load as load_config

    ap = argparse.ArgumentParser(description="dAImon Audit (T-4.6)")
    ap.add_argument("--verify", action="store_true", help="Kette pruefen")
    ap.add_argument("--verzeichnis", default=None)
    args = ap.parse_args(argv)

    verzeichnis = args.verzeichnis
    if verzeichnis is None:
        verzeichnis = Path(load_config(make_dirs=False).state_dir) / "audit"
    if not args.verify:
        ap.error("nichts zu tun; --verify angeben")

    befund = pruefe(verzeichnis)
    print(json.dumps(befund, ensure_ascii=False, indent=2))
    return 0 if befund["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
