"""T-5.7 -- der Kontextspeicher unter Quarantaene.

Der Quarantaenenachweis ist hier noch schwach, und der Plan sagt das selbst:
der Ausgang zu Mind entsteht erst in T-5.9 und wird dort aktiv angegriffen.
Was hier belegt werden kann, ist, dass es den Ausgang WIRKLICH nicht gibt --
und zwar an einer Ausnahme mit Begruendung, nicht an einer Leerstelle.
"""
from __future__ import annotations

import json
import stat

import pytest

from daimon.eyes import context as ctx


class Uhr:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    def weiter(self, s: float) -> None:
        self.t += s


def speicher(tmp_path, **kw):
    return ctx.Kontextspeicher(verzeichnis=tmp_path / "context", **kw)


# -- Vier Stufen, nicht einheitlich zwanzig --------------------------------

def test_die_drei_datenarten_haben_verschiedene_grenzen():
    """Ein Fenstertitel sagt „Posteingang". Der OCR-Text derselben Ansicht
    sagt, von wem die Mail ist und was drinsteht."""
    assert ctx.AUFBEWAHRUNG[ctx.ART_TITEL] == (20, None)
    assert ctx.AUFBEWAHRUNG[ctx.ART_OCR] == (5, 900.0)
    assert ctx.AUFBEWAHRUNG[ctx.ART_VLM] == (3, 300.0)


def test_der_ring_haelt_nur_die_erlaubte_zahl(tmp_path):
    s = speicher(tmp_path)
    for i in range(25):
        s.hinzufuegen(ctx.ART_TITEL, "editor", f"Titel {i}")
    assert s.zaehler()[ctx.ART_TITEL] == 20


def test_ocr_haelt_fuenf_und_vlm_drei(tmp_path):
    s = speicher(tmp_path)
    for i in range(9):
        s.hinzufuegen(ctx.ART_OCR, "editor", f"Text {i}")
        s.hinzufuegen(ctx.ART_VLM, "editor", f"Bild {i}")
    z = s.zaehler()
    assert z[ctx.ART_OCR] == 5 and z[ctx.ART_VLM] == 3


def test_die_frist_laesst_ocr_text_verfallen(tmp_path):
    u = Uhr()
    s = speicher(tmp_path, uhr=u)
    s.hinzufuegen(ctx.ART_OCR, "editor", "eine halbe Mail")
    u.weiter(901.0)
    s.hinzufuegen(ctx.ART_OCR, "editor", "etwas Neues")
    assert s.zaehler()[ctx.ART_OCR] == 1


def test_fenstertitel_haben_keine_frist(tmp_path):
    """Sitzungsdauer -- wenig verraeterisch und lange nuetzlich."""
    u = Uhr()
    s = speicher(tmp_path, uhr=u)
    s.hinzufuegen(ctx.ART_TITEL, "editor", "Posteingang")
    u.weiter(86_400.0)
    s.hinzufuegen(ctx.ART_TITEL, "editor", "noch da")
    assert s.zaehler()[ctx.ART_TITEL] == 2


def test_beim_laden_wird_sofort_beschnitten(tmp_path):
    """Sonst ueberlebte ein OCR-Text seine 15 Minuten um beliebig lange,
    solange niemand etwas Neues hinzufuegt."""
    u = Uhr()
    s = speicher(tmp_path, uhr=u)
    s.hinzufuegen(ctx.ART_OCR, "editor", "alt")
    u.weiter(901.0)
    zweiter = speicher(tmp_path, uhr=u)
    zweiter.laden()
    assert zweiter.zaehler()[ctx.ART_OCR] == 0


# -- Die Vorgabe ist `redacted` --------------------------------------------

def test_die_vorgabe_ist_redacted_und_nicht_full():
    """Wer den vollen Text will, muss ihn benennen."""
    assert ctx.VORGABE_STUFE == ctx.STUFE_REDACTED


def test_redacted_filtert_geheimnisse_und_behaelt_den_rest(tmp_path):
    s = speicher(tmp_path)
    s.hinzufuegen(ctx.ART_OCR, "editor",
                  "Bitte sk-ant-geheim1234567890 nicht weitergeben")
    inhalt = json.loads((tmp_path / "context" / "ocr.json").read_text())[0]["inhalt"]
    assert "sk-ant-geheim1234567890" not in inhalt
    assert "nicht weitergeben" in inhalt


def test_metadata_only_behaelt_keinen_inhalt(tmp_path):
    s = speicher(tmp_path, stufe=ctx.STUFE_METADATA)
    s.hinzufuegen(ctx.ART_OCR, "editor", "streng geheim")
    d = json.loads((tmp_path / "context" / "ocr.json").read_text())[0]
    assert d["inhalt"] == ""
    assert d["fenster"] == "editor" and d["ts"] > 0


def test_transient_kommt_gar_nicht_auf_die_platte(tmp_path):
    """„nur im Arbeitsspeicher, ueberlebt die Runde nicht"."""
    s = speicher(tmp_path, stufe=ctx.STUFE_TRANSIENT)
    s.hinzufuegen(ctx.ART_OCR, "editor", "fluechtig")
    assert s.zaehler()[ctx.ART_OCR] == 1
    assert not (tmp_path / "context" / "ocr.json").exists()


def test_full_behaelt_alles(tmp_path):
    s = speicher(tmp_path, stufe=ctx.STUFE_FULL)
    s.hinzufuegen(ctx.ART_OCR, "editor", "sk-ant-geheim1234567890")
    d = json.loads((tmp_path / "context" / "ocr.json").read_text())[0]
    assert d["inhalt"] == "sk-ant-geheim1234567890"


def test_eine_unbekannte_stufe_wird_abgelehnt(tmp_path):
    with pytest.raises(ValueError):
        speicher(tmp_path, stufe="egal")


# -- Sensible Fenster ------------------------------------------------------

def test_sensible_fenster_kommen_gar_nicht_herein(tmp_path):
    """Die Pruefung steht auch in der Gatterkette (T-5.5) -- dieser Speicher
    bekommt aber auch Titel, die nie durch die Kette gelaufen sind."""
    s = speicher(tmp_path, denylist=["org.keepassxc.KeePassXC"])
    assert s.hinzufuegen(ctx.ART_TITEL, "org.keepassxc.KeePassXC", "Tresor") is False
    assert s.zaehler()[ctx.ART_TITEL] == 0
    assert s.zaehler()["ausgelassen"] == 1
    assert not (tmp_path / "context" / "titel.json").exists()


def test_die_denylist_vergleicht_ohne_ruecksicht_auf_gross_und_klein(tmp_path):
    s = speicher(tmp_path, denylist=["KeePassXC"])
    assert s.hinzufuegen(ctx.ART_TITEL, "keepassxc", "Tresor") is False


# -- Auf der Platte --------------------------------------------------------

def test_die_dateien_haben_modus_0600(tmp_path):
    s = speicher(tmp_path)
    s.hinzufuegen(ctx.ART_TITEL, "editor", "Posteingang")
    datei = tmp_path / "context" / "titel.json"
    assert stat.S_IMODE(datei.stat().st_mode) == 0o600


def test_der_nutzer_darf_loeschen_ohne_dass_etwas_bricht(tmp_path):
    """„vom Nutzer loeschbar" heisst: er loescht die Dateien, und danach
    laeuft der Dienst weiter."""
    s = speicher(tmp_path)
    s.hinzufuegen(ctx.ART_TITEL, "editor", "eins")
    (tmp_path / "context" / "titel.json").unlink()
    zweiter = speicher(tmp_path)
    zweiter.laden()
    assert zweiter.zaehler()[ctx.ART_TITEL] == 0
    assert zweiter.hinzufuegen(ctx.ART_TITEL, "editor", "zwei") is True


# -- Kill-Switch -----------------------------------------------------------

def test_der_killswitch_leert_speicher_UND_platte(tmp_path):
    """Ein geleerter Ring vor einer vollen Datei sieht beim naechsten
    `laden()` aus wie nie geleert."""
    s = speicher(tmp_path)
    s.hinzufuegen(ctx.ART_TITEL, "editor", "eins")
    s.hinzufuegen(ctx.ART_OCR, "editor", "zwei")
    assert s.leeren() == 2
    assert s.zaehler()[ctx.ART_TITEL] == 0
    assert not (tmp_path / "context" / "titel.json").exists()
    assert not (tmp_path / "context" / "ocr.json").exists()

    nach = speicher(tmp_path)
    nach.laden()
    assert sum(nach.zaehler()[a] for a in ctx.AUFBEWAHRUNG) == 0


# -- Kein Ausgang ----------------------------------------------------------

def test_es_gibt_keinen_weg_hinaus(tmp_path):
    """Absichtlich eine Ausnahme und keine Leerstelle: „es gibt keinen Weg"
    und „ich habe den Weg noch nicht gefunden" sehen sonst gleich aus."""
    s = speicher(tmp_path)
    s.hinzufuegen(ctx.ART_OCR, "editor", "etwas")
    with pytest.raises(ctx.QuarantaeneFehler) as fehler:
        s.freigeben()
    assert "T-5.9" in str(fehler.value)
    assert "Rundenmarke" in str(fehler.value)


def test_der_zaehler_gibt_zahlen_und_keinen_inhalt(tmp_path):
    s = speicher(tmp_path)
    s.hinzufuegen(ctx.ART_OCR, "editor", "streng geheim")
    z = s.zaehler()
    assert all(isinstance(w, int) for w in z.values())
    assert "streng geheim" not in json.dumps(z)
