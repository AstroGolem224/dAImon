"""T-5.12 -- der Kill-Switch der Augen.

Der teuer bezahlte Punkt aus T-3.15 gilt hier genauso: `rc=0` ist NICHT die
Zusage. Die Zusage ist, dass danach nichts mehr sieht. Jeder Test hier stellt
deshalb den Fall her, in dem `systemctl` Erfolg meldet und trotzdem etwas
uebrig ist -- das geht nur, weil `lauf`, `stroeme` und `speicher` injizierbar
sind.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from daimon.eyes import killswitch as ks


def lauf_attrappe(rc=0, aktiv="active", stderr=""):
    def lauf(argv, **_kw):
        if "is-active" in argv:
            return SimpleNamespace(returncode=0, stdout=aktiv, stderr="")
        return SimpleNamespace(returncode=rc, stdout="", stderr=stderr)
    return lauf


def dump(*eintraege):
    """`(media.class, node.name)`-Paare, oder nur die Klasse fuer `daimon-eyes`."""
    knoten = []
    for e in eintraege:
        klasse, name = e if isinstance(e, tuple) else (e, "daimon-eyes")
        knoten.append({"info": {"props": {"media.class": klasse,
                                          "node.name": name}}})
    return json.dumps(knoten)


# -- Die Strommessung ------------------------------------------------------

def test_ein_geraet_ist_kein_strom():
    """Ein `Video/Source` ist der BILDSCHIRM und steht immer da. Wer ihn
    mitzaehlt, bekommt nie ein sauberes Aus."""
    assert ks.videostroeme(dump_text=dump("Video/Source")) == 0


def test_der_kwin_knoten_IST_unsere_erfassung():
    """BERICHTIGT am 14.08. Hier stand das Gegenteil: der kwin-Knoten galt
    als fremd, gezaehlt wurde ein Klient namens `daimon-eyes`.

    DEN GIBT ES NICHT. Der Augendienst liest ueber den PipeWire-Deskriptor
    des Portals; in `pw-dump` erscheint nur der Knoten, den kwin_wayland fuer
    die ScreenCast-Sitzung erzeugt. Zweimal am laufenden System gemessen:
    Augen an -> genau ein `Stream/Output/Video` von kwin_wayland, Augen aus
    -> null Video-Knoten. Die alte Zahl war IMMER 0, auch bei voller
    Erfassung -- "0 Stroeme nach dem Schalter" war damit keine Aussage.
    """
    assert ks.videostroeme(
        dump_text=dump(("Stream/Output/Video", "kwin_wayland"))) == 1


def test_ein_eingangsstrom_ist_keine_erfassung():
    """`plasmashell` als `Stream/Input/Video` sieht nicht zu -- es zeigt an."""
    assert ks.videostroeme(
        dump_text=dump(("Stream/Input/Video", "plasmashell"))) == 0


def test_zwei_sitzungen_zaehlen_zwei():
    """Fremde Bildschirmaufnahmen zaehlen MIT. Fuer einen Kill-Switch ist das
    die richtige Richtung: bleibt nach dem Abschalten eine Sitzung stehen,
    will man das sehen und nicht wegfiltern."""
    assert ks.videostroeme(dump_text=dump(
        ("Stream/Output/Video", "kwin_wayland"),
        ("Stream/Output/Video", "obs"))) == 2


def test_ein_bildschirmstrom_wird_gezaehlt():
    assert ks.videostroeme(
        dump_text=dump("Stream/Output/Video", "Video/Source")) == 1


def test_nicht_messbar_ist_nicht_null():
    """Wer ein fehlendes `pw-dump` als „null Stroeme" liest, macht aus einem
    Werkzeugfehler eine Sicherheitsaussage."""
    assert ks.videostroeme(dump_text=None) is None
    assert ks.videostroeme(dump_text="kein json") is None


# -- Das Kontextverzeichnis, glob-frei -------------------------------------

def test_eine_versteckte_datei_wird_mitgezaehlt(tmp_path):
    """Ein `glob("*.json")` uebersieht sie -- und genau die bliebe liegen."""
    (tmp_path / ".heimlich").write_text("Bildschirmtext")
    assert ks.kontextdateien(tmp_path) == 1


def test_ein_leeres_verzeichnis_hat_null_dateien(tmp_path):
    assert ks.kontextdateien(tmp_path) == 0


def test_ein_fehlendes_verzeichnis_ist_der_zielzustand(tmp_path):
    assert ks.kontextdateien(tmp_path / "gibtsnicht") == 0


# -- Der Schalter ----------------------------------------------------------

def test_rc0_mit_laufendem_strom_ist_KEIN_erfolg(tmp_path):
    """Der Fall, um den es geht: systemd meldet Erfolg, der Server hat den
    Strom noch."""
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(aktiv="inactive"),
                  stroeme=lambda: 1)
    assert b["ok"] is False
    assert "laufen weiter" in b["meldung"]


def test_rc0_mit_unmessbarem_strom_ist_KEIN_erfolg(tmp_path):
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(aktiv="inactive"),
                  stroeme=lambda: None)
    assert b["ok"] is False
    assert "nicht messbar" in b["meldung"]


def test_rc0_mit_uebrigen_kontextdateien_ist_KEIN_erfolg(tmp_path):
    """Das Sehen ist aus, der schon gelesene Bildschirmtext liegt noch da."""
    (tmp_path / "ocr.json").write_text("[]")
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(aktiv="inactive"),
                  stroeme=lambda: 0)
    assert b["ok"] is False
    assert "Kontextverzeichnis" in b["meldung"]


def test_alles_aus_ist_ein_erfolg(tmp_path):
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(aktiv="inactive"),
                  stroeme=lambda: 0)
    assert b["ok"] is True and b["meldung"] == ""


def test_ein_fehlgeschlagener_stopp_meldet_den_stderr(tmp_path):
    b = ks.stoppe(kontext=tmp_path,
                  lauf=lauf_attrappe(rc=5, stderr="Unit nicht gefunden"),
                  stroeme=lambda: 0)
    assert b["ok"] is False and "nicht gefunden" in b["meldung"]


# -- Die Allowlist ---------------------------------------------------------

def test_eine_fremde_unit_wird_abgelehnt(tmp_path):
    """Der schlimmste Missbrauch soll „Wahrnehmung geht aus" sein und nicht
    „der Auth-Agent geht aus"."""
    with pytest.raises(ValueError):
        ks.stoppe("daimon-auth.service", kontext=tmp_path,
                  lauf=lauf_attrappe(), stroeme=lambda: 0)


def test_die_ohren_stehen_auch_drin():
    assert "daimon-ears.service" in ks.ERLAUBTE_UNITS


# -- Der Speicher ----------------------------------------------------------

class Speicher:
    def __init__(self, wirft=False):
        self.wirft = wirft
        self.geleert = 0

    def leeren(self):
        if self.wirft:
            raise RuntimeError("Platte weg")
        self.geleert += 1
        return 7


def test_der_speicher_wird_geleert(tmp_path):
    s = Speicher()
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(), stroeme=lambda: 0,
                  speicher=s)
    assert s.geleert == 1 and b["geleert"] == 7


def test_der_speicher_wird_AUCH_bei_fehlgeschlagenem_stopp_geleert(tmp_path):
    """Ein Dienst, der sich nicht beenden laesst, ist genau der Fall, in dem
    der schon gesammelte Bildschirmtext nicht liegen bleiben soll."""
    s = Speicher()
    ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(rc=1), stroeme=lambda: 0,
              speicher=s)
    assert s.geleert == 1


def test_ein_klemmender_speicher_macht_daraus_keinen_erfolg(tmp_path):
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(), stroeme=lambda: 0,
                  speicher=Speicher(wirft=True))
    assert b["geleert"] == -1


# -- Die Portal-Sitzung ----------------------------------------------------

class Sitzung:
    def __init__(self, wirft=False):
        self.wirft = wirft
        self.zu = 0

    def widerrufen(self):
        if self.wirft:
            raise RuntimeError("Portal klemmt")
        self.zu += 1
        return {"ok": True}


def test_die_portalsitzung_wird_geschlossen(tmp_path):
    """Eine Kette auf `NULL` beendet nur die Frames -- die Sitzung bliebe
    offen, und mit ihr die Erlaubnis."""
    s = Sitzung()
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(), stroeme=lambda: 0,
                  sitzung=s)
    assert s.zu == 1 and b["sitzung_geschlossen"] is True


def test_ein_klemmendes_portal_wird_gemeldet_nicht_verschluckt(tmp_path):
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(), stroeme=lambda: 0,
                  sitzung=Sitzung(wirft=True))
    assert b["sitzung_geschlossen"] is False


def test_ohne_sitzung_steht_none_und_nicht_true(tmp_path):
    """`None` heisst „dieser Schalter kannte keine Sitzung", nicht
    „geschlossen"."""
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(), stroeme=lambda: 0)
    assert b["sitzung_geschlossen"] is None


# -- Die Tray-Lampe --------------------------------------------------------

def test_die_lampe_zeigt_den_echten_unit_zustand():
    """Nicht den letzten Befehl: eine solche Lampe leuchtet gruen, waehrend
    der Dienst nach einem Absturz laengst wieder hochgekommen ist."""
    assert ks.lampe(lauf=lauf_attrappe(aktiv="active")) == "an"
    assert ks.lampe(lauf=lauf_attrappe(aktiv="inactive")) == "aus"


def test_ein_werkzeugfehler_macht_die_lampe_nicht_aus():
    """Eine Lampe, die bei einem Werkzeugfehler Entwarnung gibt, ist
    schlimmer als gar keine."""
    def kaputt(*_a, **_k):
        raise OSError("systemctl fehlt")
    assert ks.lampe(lauf=kaputt) == "unbekannt"


def test_der_bericht_traegt_die_lampe_mit(tmp_path):
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(aktiv="inactive"),
                  stroeme=lambda: 0)
    assert b["lampe"] == "aus"


def test_eine_noch_aktive_unit_ist_KEIN_erfolg(tmp_path):
    """Am 13.08. am laufenden Dienst gemessen: die Strommessung allein reicht
    nicht. Die Kette baut sich je Frame ab (T-5.3), zwischen zwei Blicken
    gibt es gar keinen Strom -- der Zaehler stand auf 0, waehrend der Dienst
    munter weiterlas.
    """
    b = ks.stoppe(kontext=tmp_path, lauf=lauf_attrappe(aktiv="active"),
                  stroeme=lambda: 0)
    assert b["ok"] is False
    assert b["noch_aktiv"] is True
    assert "weiter aktiv" in b["meldung"]


def test_der_beleg_sagt_ob_die_positivkontrolle_stand():
    """Ein `ok` ohne vorherigen Strom ist kein Nachweis -- der Bericht sagt
    das jetzt selbst, statt es dem Leser zu ueberlassen."""
    class Systemctl:
        def __init__(self): self.aktiv = True
        def __call__(self, argv, **_):
            if argv[2] == "stop": self.aktiv = False
            text = ("active" if self.aktiv else "inactive") if argv[2] == "is-active" else ""
            return type("E", (), {"returncode": 0, "stdout": text, "stderr": ""})()

    stroeme = iter([1, 0])
    gefuehrt = ks.stoppe(lauf=Systemctl(), stroeme=lambda: next(stroeme),
                         kontext=pathlib.Path("/nicht/da"))
    assert gefuehrt["ok"] is True, gefuehrt["meldung"]
    assert gefuehrt["beleg"] == "strom_gemessen"

    leer = ks.stoppe(lauf=Systemctl(), stroeme=lambda: 0,
                     kontext=pathlib.Path("/nicht/da"))
    assert leer["ok"] is True          # die Unit ist weg, das gilt
    assert leer["beleg"] == "nur_unit_zustand"
