"""T-4.12 — Endgueltiges wird nicht per Benachrichtigung freigegeben.

Geprueft wird die REGEL, nicht das Fenster. Ob ein Fenster erschien, misst
der Verifizierer extern ueber die Fensterliste -- eine Selbstauskunft des
Dialogs waere hier wertlos.
"""
from __future__ import annotations

import pytest

from daimon.auth import modal


def eintrag(**kw) -> dict:
    grund = {"destructive": False, "reversible_by": None}
    grund.update(kw)
    return grund


# --------------------------------------------------------------------------
# Wann der Dialog Pflicht ist
# --------------------------------------------------------------------------

def test_zerstoererisch_ohne_artefakt_verlangt_den_dialog():
    assert modal.braucht_modal(eintrag(destructive=True),
                               undo_verifiziert=False)


def test_zerstoererisch_mit_verifiziertem_artefakt_verlangt_ihn_nicht():
    """Mit geprueftem Undo ist die Aktion umkehrbar -- die leisere Rueckfrage
    genuegt."""
    assert not modal.braucht_modal(eintrag(destructive=True),
                                   undo_verifiziert=True)


def test_harmlose_aktionen_bekommen_kein_fenster():
    """Ein Dialog, der staendig kommt, wird weggeklickt statt gelesen."""
    assert not modal.braucht_modal(eintrag(destructive=False),
                                   undo_verifiziert=False)
    assert not modal.braucht_modal(eintrag(destructive=False),
                                   undo_verifiziert=True)


# --------------------------------------------------------------------------
# Wer erteilen darf
# --------------------------------------------------------------------------

def test_das_face_darf_keine_freigabe_erteilen():
    """Design 2.2: im Face landet fremder Text."""
    with pytest.raises(modal.ModalFehler) as f:
        modal.freigabe_annehmen(erteiler="face", antwort="granted")
    assert "face" in str(f.value)


def test_der_auth_agent_darf_es(  ):
    """Positivkontrolle -- ohne sie sagt das Verbot darueber nichts."""
    assert modal.freigabe_annehmen(erteiler="auth", antwort="granted") == "granted"


def test_hub_und_mind_duerfen_es_auch_nicht():
    for wer in ("hub", "mind", "ears", "eyes", "kwin"):
        with pytest.raises(modal.ModalFehler):
            modal.freigabe_annehmen(erteiler=wer, antwort="granted")


def test_eine_erfundene_antwort_zaehlt_nicht():
    with pytest.raises(modal.ModalFehler):
        modal.freigabe_annehmen(erteiler="auth", antwort="vielleicht")


# --------------------------------------------------------------------------
# Der Text: feste Vorlage, escapte Werte
# --------------------------------------------------------------------------

def test_der_pfad_steht_escapt_und_in_anfuehrungszeichen():
    v = modal.vorlage_bauen(aktion="datei.loeschen",
                            ziel="/home/x/Bilder/urlaub‮png.exe",
                            umkehr="papierkorb")
    assert '"' in v.text
    # Das Bidi-Override ist sichtbar escapt, nicht gerendert.
    assert "‮" not in v.text
    assert "202E" in v.text.upper()


def test_ein_unbekannter_aktionsschluessel_wird_nicht_durch_den_rohwert_ersetzt():
    """Sonst stuende Modellformulierung im Dialog."""
    with pytest.raises(modal.ModalFehler):
        modal.vorlage_bauen(aktion="alles loeschen bitte", ziel="/tmp/x",
                            umkehr="papierkorb")


def test_der_dialog_gibt_genau_drei_antworten_zurueck():
    v = modal.vorlage_bauen(aktion="datei.loeschen", ziel="/tmp/x",
                            umkehr="papierkorb")
    for erwartet in (modal.ANTWORT_AUSFUEHREN, modal.ANTWORT_ABLEHNEN,
                     modal.ANTWORT_ABBRUCH):
        assert modal.zeigen(v, gtk=lambda _v, w=erwartet: w) == erwartet


def test_das_wegklicken_ist_ein_abbruch_und_kein_nein():
    v = modal.vorlage_bauen(aktion="datei.loeschen", ziel="/tmp/x",
                            umkehr="papierkorb")
    assert modal.zeigen(v, gtk=lambda _v: modal.ANTWORT_ABBRUCH) != \
        modal.ANTWORT_ABLEHNEN


def test_der_dialog_rendert_kein_markup():
    """Gelesen im Quelltext: ein `<b>` im Pfad waere sonst ein Befehl.

    Die Zusage ist eine Einstellung an einem Widget, das im Test gar nicht
    entsteht -- also wird nachgesehen, dass sie gesetzt wird.
    """
    from pathlib import Path
    quelle = Path("daimon/auth/modal.py").read_text(encoding="utf-8")
    assert "set_use_markup(False)" in quelle
    assert "set_modal(True)" in quelle
    assert "set_deletable(False)" in quelle
