"""Der STT-Dienst residiert -- und das steht nur noch an einer Stelle so.

BEFUND T-7.4 K3 (Reviewer-Sitzung 19.08.): der Leerlauf-Exit stand zweimal im
Repo, gegensätzlich.

    docs/DESIGN.md:382     "bei Stille beendet er sich wie gehabt"
    daimon/gpu/stt.py:24   Abschnitt "Kein Leerlauf-Exit"
    daimon/gpu/stt.py:384  `while True` ohne Frist

Beide Fassungen trugen eine Begründung, und beide waren für sich tragfähig.
Nicht tragfähig war ihr Nebeneinander -- welche gilt, entschied der Zufall
dessen, der zuerst nachschlug. Entschieden am 19.08. von Matthias: **es gilt
die Residenz.**

**Warum das für den STT richtig ist und für den GPU-Worker nicht.** Die
Residenzpolitik aus Design §5.4 existiert, weil ein belegter CUDA-Primärkontext
den nächsten Ladevorgang scheitern lässt -- auf Linux verdrängt der Treiber
nicht, Allokationen scheitern. Der STT hält aber **kein VRAM**: sherpa-onnx
läuft auf der CPU (`stt.py:1`). Er kann also keinen Ladevorgang blockieren, und
ein Prozessende kostete 843 ms Ladezeit und spart nichts.

Diese Datei hält beide Hälften fest: dass der Code residiert, und dass die
Dokumente es nicht mehr anders behaupten.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STT = REPO / "daimon" / "gpu" / "stt.py"
DESIGN = REPO / "docs" / "DESIGN.md"
PLAN = REPO / "docs" / "IMPLEMENTATION-PLAN.md"

# Der Satz, der die Attrappe war. Wortlaut aus dem Befund.
ALTE_FASSUNG = "bei Stille beendet er sich wie gehabt"


# -- Der Code: residiert er wirklich --------------------------------------

def test_die_schleife_hat_keine_frist():
    """DIE ZUSAGE AM CODE. `while True` ohne Zeitlimit auf dem annehmenden
    Socket -- ein `settimeout` dort WÄRE der Leerlauf-Exit, so wie ihn der
    GPU-Worker hat (`worker.py:349`: `srv.settimeout(self.idle_s)`)."""
    baum = ast.parse(STT.read_text(encoding="utf-8"))
    fristen = [k.lineno for k in ast.walk(baum)
               if isinstance(k, ast.Call)
               and getattr(k.func, "attr", "") == "settimeout"
               and any(getattr(a, "id", "") in ("srv", "server")
                       for a in [getattr(k.func, "value", None)] if a)]
    assert not fristen, (
        f"stt.py:{fristen} setzt eine Frist auf den annehmenden Socket -- "
        "das wäre ein Leerlauf-Exit, und der Dienst soll residieren (T-7.4 K3)")


def test_das_modell_liegt_auf_der_cpu():
    """Der Grund für die Ausnahme, und er ist die ganze Begründung: wer kein
    VRAM hält, kann keinen Ladevorgang blockieren.

    `provider="cpu"` steht im Code und NICHT in der Konfiguration -- sonst
    ließe sich die 0-VRAM-Zusage per Datei aushebeln, und mit ihr der Grund,
    warum dieser Dienst residieren darf.
    """
    text = STT.read_text(encoding="utf-8")
    assert 'provider="cpu"' in text or "provider='cpu'" in text
    zeilen = [z for z in text.splitlines() if "provider" in z and "cpu" in z]
    assert zeilen, "kein fester CPU-Provider mehr"
    for z in zeilen:
        assert "cfg" not in z and "config" not in z and "get(" not in z, (
            f"der Provider kommt aus der Konfiguration: {z.strip()!r} -- "
            "damit ist die 0-VRAM-Zusage per Datei aushebelbar")


# -- Die Dokumente: behaupten sie noch das Gegenteil ----------------------

@pytest.mark.parametrize("datei", [DESIGN, PLAN], ids=lambda p: p.name)
def test_die_alte_fassung_steht_nirgends_mehr_als_zusage(datei):
    """DER BEFUND in einer Zeile.

    Erlaubt bleibt der Satz in einem Korrekturvermerk -- dort steht er als
    Zitat dessen, was NICHT mehr gilt, und ein Repo, das seine eigenen
    Korrekturen verschweigt, verliert die Begründung mit.
    """
    treffer = []
    for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
        if ALTE_FASSUNG not in zeile:
            continue
        # Zitat in einem Korrekturvermerk: Blockzitat (>) oder in
        # Anführungszeichen.
        nackt = zeile.strip()
        if nackt.startswith(">") or "„" in zeile or '"' in zeile:
            continue
        treffer.append(f"{datei.name}:{nr}")
    assert not treffer, (
        f"die alte Fassung steht wieder als Zusage da: {treffer} -- "
        "entschieden ist die Residenz (T-7.4 K3, 19.08.)")


def test_die_dokumente_nennen_die_residenz():
    """Die Gegenrichtung. Ohne sie bestünde der Test darüber auch, wenn
    jemand den Satz einfach löscht -- dann stünde nirgends mehr, was gilt,
    und die nächste Sitzung müsste es aus dem Code raten."""
    for datei in (DESIGN, PLAN):
        text = datei.read_text(encoding="utf-8")
        assert re.search(r"residiert|Residenz", text), datei.name
        assert "0 VRAM" in text or "kein VRAM" in text.lower(), (
            f"{datei.name} nennt die Residenz, aber nicht ihren Grund -- "
            "ohne den liest die nächste Sitzung sie als Bequemlichkeit")


def test_die_residenzpolitik_zieht_ihre_grenze():
    """§5.4 sagt "Kein Modell bleibt geladen". Ohne die Grenze daneben ist
    der Widerspruch nur verschoben, nicht gelöst."""
    text = DESIGN.read_text(encoding="utf-8")
    i = text.index("### 5.4")
    abschnitt = text[i:i + 2000]
    assert "VRAM-Bewohner" in abschnitt or "nur für sie" in abschnitt, (
        "§5.4 grenzt nicht mehr ab, für wen die Regel gilt -- der STT "
        "residiert und widerspräche ihr wieder")


# -- Der Wächter für den Tag, an dem jemand es zurückdreht ----------------

STT_UNIT = REPO / "config" / "systemd" / "daimon-stt.service"


@pytest.mark.parametrize("datei", [STT, STT_UNIT], ids=lambda p: p.name)
def test_WAECHTER_beide_stt_dateien_sagen_es_ausdruecklich(datei):
    """Die Verneinung muss DASTEHEN, nicht bloss aus dem Fehlen einer Frist
    folgen.

    Eine erste Fassung dieses Waechters suchte im ganzen Baum nach dem Wort
    "Leerlauf" und traf 34 Stellen -- fast alle im GPU-Worker, der zu Recht
    einen Leerlauf-Exit hat. Ein Waechter, der bei jeder richtigen Zeile
    anschlaegt, wird abgeschaltet und bewacht dann nichts. Deshalb hier
    positiv formuliert und auf die zwei Dateien des Dienstes beschraenkt: sie
    muessen die Ausnahme benennen, statt dass irgendwo ihr Gegenteil fehlt.

    Wer die Zeile streicht, faellt hier auf -- und findet im Docstring oben,
    warum sie steht.
    """
    text = datei.read_text(encoding="utf-8").lower()
    assert "leerlauf" in text, (
        f"{datei.name} sagt nichts mehr ueber den Leerlauf-Exit -- die "
        "Ausnahme von Design 5.4 steht dann nur noch im Verhalten")
    verneint = any(w in text for w in ("kein leerlauf", "ohne leerlauf",
                                       "keinen leerlauf"))
    assert verneint, (
        f"{datei.name} nennt den Leerlauf-Exit, verneint ihn aber nicht mehr "
        "-- entschieden ist die Residenz (T-7.4 K3, 19.08.)")
    assert "vram" in text, (
        f"{datei.name} nennt den Grund nicht -- ohne '0 VRAM' liest die "
        "naechste Sitzung die Residenz als Bequemlichkeit")
