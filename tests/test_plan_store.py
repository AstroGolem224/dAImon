"""T-8.1 -- die Datenbank des Zeitplaners.

Was hier geprueft wird, ist nicht „SQLite funktioniert". Es ist derselbe
Kern wie im Mind-Store: die MARKIERUNG ueberlebt als Typ, und die
Faelligkeitsfrage (`faellige`) ist die einzige, die der Dienst stellt --
suspend- und neustartsicher, weil sie die Datenbank jedes Mal neu liest.
"""
from __future__ import annotations

import json
import sqlite3
import stat

import pytest

from daimon.common.protocol import Mark, Marked
from daimon.plan import store as st


def db(tmp_path, **kw):
    s = st.Store(tmp_path / "plan.db", **kw)
    s.migrieren()
    return s


# -- Datei und Rechte ------------------------------------------------------

def test_die_datenbank_hat_modus_0600(tmp_path):
    s = db(tmp_path)
    assert stat.S_IMODE(s.pfad.stat().st_mode) == 0o600


def test_auch_die_wal_datei_hat_0600(tmp_path):
    """Im WAL stehen dieselben Zeilen. Eine Datenbank mit 0600 neben einem
    WAL mit 0644 ist eine Datenbank mit 0644."""
    s = db(tmp_path)
    s.anlegen("termin", Marked("zahnarzt", Mark.USER_PTT), ts_faellig=100.0)
    wal = tmp_path / "plan.db-wal"
    if wal.exists():                       # WAL erscheint erst beim Schreiben
        assert stat.S_IMODE(wal.stat().st_mode) == 0o600


def test_die_datenbank_liegt_im_eigenen_unterverzeichnis(tmp_path, monkeypatch):
    """Die Zusage der Unit haengt daran: `ReadWritePaths` gibt genau diesen
    Ordner frei und nicht das state-Verzeichnis mit `audit/audit.jsonl`."""
    monkeypatch.setattr(st, "state_dir", lambda: tmp_path)
    s = st.Store()
    assert s.pfad == tmp_path / st.UNTERVERZEICHNIS / "plan.db"


def test_eine_datenbank_aus_der_alten_lage_zieht_um(tmp_path, monkeypatch):
    """Ohne Umzug faengt der Dienst nach dem Update mit einer leeren
    Terminliste an, und die alten Termine liegen still daneben.

    KOPIERT, nicht verschoben: der Altbestand bleibt liegen -- ihn zu
    loeschen braeuchte Schreibrecht am QUELLverzeichnis, und genau das hat
    die Unit nicht."""
    monkeypatch.setattr(st, "state_dir", lambda: tmp_path)
    alt = st.Store(tmp_path / "plan.db")
    alt.migrieren()
    alt.anlegen("termin", Marked("Zahnarzt", Mark.USER_PTT), ts_faellig=100.0)
    alt.schliessen()

    neu = st.Store()
    neu.migrieren()
    assert [str(e["titel"].value) for e in neu.liste()] == ["Zahnarzt"]
    assert (tmp_path / "plan.db").exists()


def test_ein_umzug_aus_nur_lesbarem_elternverzeichnis_toetet_nichts(
        tmp_path, monkeypatch):
    """Der Befund vom 26.08.: `os.replace` braucht Schreibrecht am QUELL-
    verzeichnis, die Unit gibt aber nur noch `.../plan` frei. Der Umzug warf
    `OSError`, `main()` fing nichts, `Restart=on-failure` machte daraus eine
    Dauerschleife -- genau auf der Anlage, fuer die die Migration ist."""
    monkeypatch.setattr(st, "state_dir", lambda: tmp_path)
    alt = st.Store(tmp_path / "plan.db")
    alt.migrieren()
    alt.anlegen("termin", Marked("Zahnarzt", Mark.USER_PTT), ts_faellig=100.0)
    alt.schliessen()
    (tmp_path / st.UNTERVERZEICHNIS).mkdir(exist_ok=True)
    tmp_path.chmod(0o500)                     # nur lesen, kein Schreiben
    try:
        neu = st.Store()
        neu.migrieren()                       # darf NICHT werfen
        assert [str(e["titel"].value) for e in neu.liste()] == ["Zahnarzt"]
        neu.schliessen()
    finally:
        tmp_path.chmod(0o700)


def test_ein_misslungener_umzug_laesst_keine_halbe_datenbank_zurueck(
        tmp_path, monkeypatch):
    """Positivkontrolle zur Fehlerbehandlung: schlaegt das Kopieren mitten
    im Satz fehl, startet der Dienst leer statt mit einem WAL ohne seine
    Datenbank -- und er startet ueberhaupt."""
    monkeypatch.setattr(st, "state_dir", lambda: tmp_path)
    alt = st.Store(tmp_path / "plan.db")
    alt.migrieren()
    alt.anlegen("termin", Marked("Zahnarzt", Mark.USER_PTT), ts_faellig=100.0)
    alt.schliessen()
    # SQLite raeumt das WAL beim Schliessen weg; hier soll es liegen, damit
    # der Kopiervorgang wirklich zwei Schritte hat.
    (tmp_path / "plan.db-wal").write_bytes(b"x")

    echt = st.shutil.copy2
    ruf = {"n": 0}

    def kaputt(q, z):
        ruf["n"] += 1
        if ruf["n"] == 1:
            return echt(q, z)
        raise OSError("kein Platz")

    monkeypatch.setattr(st.shutil, "copy2", kaputt)
    neu = st.Store()
    neu.migrieren()
    assert neu.liste() == []                   # leer, nicht halb
    assert neu.version() == st.SCHEMA_VERSION   # und benutzbar
    assert (tmp_path / "plan.db").exists()      # der Altbestand liegt noch da


# -- Migrationen, vor und zurueck -----------------------------------------

def test_migration_hinauf_setzt_die_version(tmp_path):
    s = st.Store(tmp_path / "plan.db")
    assert s.version() == 0
    assert s.migrieren() == st.SCHEMA_VERSION


def test_migration_hinunter_und_wieder_hinauf(tmp_path):
    """Eine Migration ohne Rueckweg ist keine Migration, sondern eine
    Einbahnstrasse mit Schemaversion."""
    s = db(tmp_path)
    s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    assert s.migrieren(0) == 0
    with pytest.raises(sqlite3.OperationalError):
        s.oeffnen().execute("SELECT 1 FROM eintraege")
    assert s.migrieren() == st.SCHEMA_VERSION
    assert s.anlegen("termin", Marked("wieder da", Mark.USER_PTT),
                     ts_faellig=100.0) > 0


def test_eine_unmoegliche_zielversion_wird_abgelehnt(tmp_path):
    s = db(tmp_path)
    with pytest.raises(st.StoreFehler):
        s.migrieren(99)
    with pytest.raises(st.StoreFehler):
        s.migrieren(-1)


# -- Anlegen: geschlossene Mengen -----------------------------------------

@pytest.mark.parametrize("art", ["termin", "fokus", "TERMIN", " Fokus "])
def test_die_beiden_arten_gehen_durch(tmp_path, art):
    s = db(tmp_path)
    assert s.anlegen(art, Marked("x", Mark.USER_PTT), ts_faellig=100.0) > 0


@pytest.mark.parametrize("art", ["aktion", "mood", "", "kalender"])
def test_eine_unbekannte_art_wird_abgelehnt(tmp_path, art):
    """Die Menge ist geschlossen. Wer eine dritte Art will, baut eine
    Migration -- und denkt dabei ueber den Rueckweg nach."""
    s = db(tmp_path)
    with pytest.raises(st.StoreFehler):
        s.anlegen(art, Marked("x", Mark.USER_PTT), ts_faellig=100.0)


def test_ein_leerer_titel_wird_abgelehnt(tmp_path):
    s = db(tmp_path)
    with pytest.raises(st.StoreFehler):
        s.anlegen("termin", Marked("   ", Mark.USER_PTT), ts_faellig=100.0)


def test_ts_erstellt_kommt_aus_der_uhr(tmp_path):
    s = st.Store(tmp_path / "plan.db", uhr=lambda: 500.0)
    s.migrieren()
    s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=600.0)
    assert s.liste()[0]["ts_erstellt"] == 500.0


# -- Die Markierung ueberlebt ---------------------------------------------

@pytest.mark.parametrize("marke", list(Mark))
def test_jede_markierung_kommt_unveraendert_zurueck(tmp_path, marke):
    s = db(tmp_path)
    s.anlegen("termin", Marked("Titel", marke), ts_faellig=100.0)
    zurueck = s.liste()[0]["titel"]
    assert isinstance(zurueck, Marked)
    assert zurueck.mark is marke
    assert zurueck.value == "Titel"


def test_ein_nackter_titel_wird_tainted(tmp_path):
    """Nicht weil das haeufig richtig ist, sondern weil das die harmlose
    Richtung ist, wenn jemand es vergisst."""
    s = db(tmp_path)
    s.anlegen("termin", "einfach so", ts_faellig=100.0)
    assert s.liste()[0]["titel"].mark is Mark.TAINTED


def test_eine_zerstoerte_markierung_kommt_als_TAINTED_zurueck(tmp_path):
    """Hier wird die Markierung in der Datenbank absichtlich zerstoert -- so
    wie es ein Fehler taete, der das Format vergisst. Zurueckkommen darf dann
    NICHT `trusted`."""
    s = db(tmp_path)
    s.anlegen("termin", Marked("geheim", Mark.TRUSTED), ts_faellig=100.0)
    s.oeffnen().execute("UPDATE eintraege SET titel = ?", (json.dumps("geheim"),))
    zurueck = s.liste()[0]["titel"]
    assert zurueck.mark is Mark.TAINTED
    assert zurueck.value == "geheim"


def test_ein_unlesbarer_titel_kommt_als_TAINTED_zurueck(tmp_path):
    s = db(tmp_path)
    s.anlegen("termin", Marked("x", Mark.TRUSTED), ts_faellig=100.0)
    s.oeffnen().execute("UPDATE eintraege SET titel = 'kein json'")
    assert s.liste()[0]["titel"].mark is Mark.TAINTED


# -- Faelligkeit: die einzige Frage des Dienstes ---------------------------

def test_faellige_sind_offen_und_ueberfaellig(tmp_path):
    s = db(tmp_path)
    s.anlegen("termin", Marked("alt", Mark.USER_PTT), ts_faellig=100.0)
    s.anlegen("termin", Marked("genau", Mark.USER_PTT), ts_faellig=200.0)
    s.anlegen("termin", Marked("zukunft", Mark.USER_PTT), ts_faellig=300.0)
    f = s.faellige(200.0)
    assert [e["titel"].value for e in f] == ["alt", "genau"]


def test_gemeldete_sind_nicht_mehr_faellig(tmp_path):
    """Ohne diese Zeile feuert jede Abtastrunde denselben Termin erneut."""
    s = db(tmp_path)
    i = s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    assert len(s.faellige(150.0)) == 1
    assert s.markiere(i, "gemeldet") is True
    assert s.faellige(150.0) == []


def test_faellige_kommen_in_faelligkeitsreihenfolge(tmp_path):
    s = db(tmp_path)
    s.anlegen("termin", Marked("spaet", Mark.USER_PTT), ts_faellig=300.0)
    s.anlegen("termin", Marked("frueh", Mark.USER_PTT), ts_faellig=100.0)
    assert [e["titel"].value for e in s.faellige(400.0)] == ["frueh", "spaet"]


def test_ein_waehrend_des_stopps_verpasster_termin_ist_danach_faellig(tmp_path):
    """Die Suspend-Sicherheit dieses Moduls liegt im Lesen, nicht in einem
    Wecker: was faellig ist, ist faellig -- auch drei Tage spaeter."""
    s = db(tmp_path)
    s.anlegen("termin", Marked("verpasst", Mark.USER_PTT), ts_faellig=100.0)
    drei_tage = 100.0 + 3 * 86400
    assert [e["titel"].value for e in s.faellige(drei_tage)] == ["verpasst"]


# -- Status und Liste ------------------------------------------------------

@pytest.mark.parametrize("status", ["offen", "gemeldet", "gestoppt"])
def test_jeder_bekannte_status_geht(tmp_path, status):
    s = db(tmp_path)
    i = s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    assert s.markiere(i, status) is True
    assert s.liste()[0]["status"] == status


def test_ein_unbekannter_status_wird_abgelehnt(tmp_path):
    s = db(tmp_path)
    i = s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    with pytest.raises(st.StoreFehler):
        s.markiere(i, "erledigt-vielleicht")
    with pytest.raises(st.StoreFehler):
        s.liste("alles-irgendwie")


def test_markiere_meldet_ob_es_den_eintrag_gab(tmp_path):
    s = db(tmp_path)
    assert s.markiere(424242, "gemeldet") is False


def test_liste_filtert_nach_status(tmp_path):
    s = db(tmp_path)
    a = s.anlegen("termin", Marked("a", Mark.USER_PTT), ts_faellig=100.0)
    s.anlegen("fokus", Marked("b", Mark.USER_PTT), ts_faellig=200.0)
    s.markiere(a, "gemeldet")
    assert [e["titel"].value for e in s.liste("offen")] == ["b"]
    assert len(s.liste()) == 2


def test_quelle_ist_intern_und_laesst_sich_setzen(tmp_path):
    """Der CalDAV-Haken: heute immer `intern`, morgen ohne Schemaaenderung
    mehr."""
    s = db(tmp_path)
    s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    assert s.liste()[0]["quelle"] == "intern"
    s.anlegen("termin", Marked("y", Mark.USER_PTT), ts_faellig=100.0,
              quelle="caldav")
    assert s.liste("offen")[0]["quelle"] in ("intern", "caldav")


# -- Loeschen --------------------------------------------------------------

def test_einzelnes_loeschen_meldet_ob_es_den_eintrag_gab(tmp_path):
    s = db(tmp_path)
    i = s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    assert s.loeschen(i) is True
    assert s.loeschen(i) is False
    assert s.liste() == []


def test_alles_loeschen_entfernt_zeilen_UND_datei(tmp_path):
    s = db(tmp_path)
    s.anlegen("termin", Marked("streng geheim", Mark.USER_PTT), ts_faellig=100.0)
    s.anlegen("fokus", Marked("noch eins", Mark.USER_PTT), ts_faellig=200.0)
    assert s.alles_loeschen() == 2
    assert not s.pfad.exists()
    assert not (tmp_path / "plan.db-wal").exists()


def test_nach_dem_alles_loeschen_laesst_sich_weiterarbeiten(tmp_path):
    s = db(tmp_path)
    s.anlegen("termin", Marked("x", Mark.USER_PTT), ts_faellig=100.0)
    s.alles_loeschen()
    s.migrieren()
    assert s.anlegen("termin", Marked("neu", Mark.USER_PTT),
                     ts_faellig=100.0) > 0
    assert len(s.liste()) == 1


def test_der_store_uebersteht_threads(tmp_path):
    """Live-Befund vom 24.08.: der Dienst fragt aus Threads (Anfragen und
    Abtastung), sqlite3 warf `ProgrammingError ... same thread`, und der
    laufende Dienst antwortete auf `liste` mit Schweigen. Diese Probe
    haemmert den Store aus mehreren Threads -- ohne die Sperre in
    `Store.oeffnen` fliegt genau dieser Fehler."""
    import threading

    s = db(tmp_path)
    s.migrieren()
    fehler = []

    def arbeiten(n: int) -> None:
        try:
            for i in range(25):
                s.anlegen("termin", Marked(f"t{n}-{i}", Mark.USER_PTT),
                          ts_faellig=1000.0 + i)
                s.liste("offen")
                s.faellige(2000.0)
        except Exception as exc:  # noqa: BLE001 -- der Test will sie sehen
            fehler.append(exc)

    threads = [threading.Thread(target=arbeiten, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fehler == [], [f"{type(e).__name__}: {e}" for e in fehler]
    # Positivkontrolle: alle 100 Eintraege sind da, keiner ging still weg.
    assert len(s.liste()) == 4 * 25
