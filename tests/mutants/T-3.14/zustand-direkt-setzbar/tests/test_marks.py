"""T-0.8 — Tests fuer Rundenmarke, Aktionsfreigabe und API-Kontingent.

Je Ablehnung gibt es eine Positivkontrolle: derselbe Ablauf muss im
gueltigen Fall funktionieren. Sonst ist "wurde abgelehnt" nicht von "ist
ganz kaputt" zu unterscheiden. Erwartete Werte stehen als Literale in den
Zusicherungen, nicht als Formel der Implementierung.
"""

import threading

import pytest

from daimon.hub.marks import (
    Aktionsfreigabe,
    FreigabeBuch,
    KontingentBuch,
    MarkenBuch,
    MarkenFehler,
    Rundenmarke,
    neue_request_id,
)


class Uhr:
    """Injizierte Zeitquelle. Kein sleep -- die Zeit laeuft per Hand."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class Aufzeichner:
    """Fängt jede Logzeile. Der Logger aus daimon.common.logging wird hier
    nicht gebraucht -- geprueft wird, dass das Buch alles meldet."""

    def __init__(self) -> None:
        self.zeilen: list[dict] = []

    def info(self, message: str, **felder) -> None:
        self.zeilen.append({"message": message, **felder})

    def warn(self, message: str, **felder) -> None:
        self.zeilen.append({"message": message, **felder})

    def handlungen(self, typ: str) -> list[str]:
        return [z["DAIMON_HANDLUNG"] for z in self.zeilen
                if z.get("DAIMON_TYP") == typ]


# ---------------------------------------------------------------------------
# Rundenmarke
# ---------------------------------------------------------------------------

def test_rundenmarke_glueckspfad():
    uhr, log = Uhr(), Aufzeichner()
    buch = MarkenBuch(frist_s=120.0, jetzt=uhr, log=log)
    marke = buch.ausgeben(quelle="auth", turn_id="turn-1")
    assert isinstance(marke, Rundenmarke)
    assert marke.turn_id == "turn-1"
    assert marke.ablauf_ts == 10_120.0  # injizierte Zeit + Frist, als Literal
    assert buch.initiator("turn-1") == "user"
    eingeloest = buch.einloesen("turn-1")
    assert eingeloest.turn_id == "turn-1"


def test_rundenmarke_nur_aus_auth():
    buch = MarkenBuch(jetzt=Uhr(), log=Aufzeichner())
    for quelle in ("mind", "wake_word", "request", "", "AUTH"):
        with pytest.raises(MarkenFehler):
            buch.ausgeben(quelle=quelle, turn_id="turn-1")
    # Positivkontrolle: "auth" geht.
    buch.ausgeben(quelle="auth", turn_id="turn-1")


def test_rundenmarke_wiedereinloesung_abgelehnt():
    buch = MarkenBuch(jetzt=Uhr(), log=Aufzeichner())
    buch.ausgeben(quelle="auth", turn_id="turn-1")
    buch.einloesen("turn-1")
    with pytest.raises(MarkenFehler):
        buch.einloesen("turn-1")
    # Nach der Einloesung ist der Initiator wieder background.
    assert buch.initiator("turn-1") == "background"


def test_rundenmarke_verbrauchte_turn_id_nicht_wiederausgebbar():
    """Eine verbrauchte turn_id darf keine neue Marke bekommen. turn_id
    kommt aus dem Aufruf -- ein Request-Feld darf nicht darueber
    entscheiden, ob eine abgeschlossene Runde wieder gilt."""
    log = Aufzeichner()
    buch = MarkenBuch(jetzt=Uhr(), log=log)
    buch.ausgeben(quelle="auth", turn_id="turn-1")
    buch.einloesen("turn-1")
    with pytest.raises(MarkenFehler):
        buch.ausgeben(quelle="auth", turn_id="turn-1")
    # Die Runde bleibt abgeschlossen, auch nach dem Versuch.
    with pytest.raises(MarkenFehler):
        buch.einloesen("turn-1")
    assert buch.initiator("turn-1") == "background"
    # Positivkontrolle: eine frische turn_id funktioniert weiterhin.
    buch.ausgeben(quelle="auth", turn_id="turn-2")
    buch.einloesen("turn-2")
    assert log.handlungen("rundenmarke") == [
        "ausgabe", "einloesung", "ablehnung", "ablehnung",
        "ausgabe", "einloesung"]


def test_rundenmarke_ablauf_abgelehnt():
    uhr = Uhr()
    buch = MarkenBuch(frist_s=120.0, jetzt=uhr, log=Aufzeichner())
    buch.ausgeben(quelle="auth", turn_id="turn-1")
    uhr.t += 121.0
    with pytest.raises(MarkenFehler):
        buch.einloesen("turn-1")
    assert buch.initiator("turn-1") == "background"
    # Positivkontrolle: innerhalb der Frist geht es.
    buch.ausgeben(quelle="auth", turn_id="turn-2")
    uhr.t += 119.0
    buch.einloesen("turn-2")


def test_rundenmarke_fremde_turn_id_abgelehnt():
    buch = MarkenBuch(jetzt=Uhr(), log=Aufzeichner())
    buch.ausgeben(quelle="auth", turn_id="turn-1")
    with pytest.raises(MarkenFehler):
        buch.einloesen("turn-2")
    # Fremde turn_id veraendert die echte Marke nicht.
    assert buch.initiator("turn-1") == "user"
    buch.einloesen("turn-1")


def test_initiator_bei_fehlender_marke_background():
    buch = MarkenBuch(jetzt=Uhr())
    assert buch.initiator("gibts-nicht") == "background"


def test_rundenmarke_kein_zustand_aus_dem_request():
    """Der Aufrufer kann sich keine laengere Frist oder ein eigenes
    Ablaufdatum erschleichen -- dafuer gibt es schlicht keinen Parameter."""
    uhr = Uhr()
    buch = MarkenBuch(frist_s=120.0, jetzt=uhr)
    marke = buch.ausgeben(quelle="auth", turn_id="turn-1")
    assert marke.ablauf_ts == 10_120.0
    with pytest.raises(TypeError):
        buch.ausgeben(quelle="auth", turn_id="t", ablauf_ts=9e18)
    with pytest.raises(TypeError):
        buch.ausgeben(quelle="auth", turn_id="t", frist_s=9e18)
    with pytest.raises(TypeError):
        buch.einloesen("turn-1", ablauf_ts=9e18)


def test_rundenmarke_paralleles_einloesen_genau_einmal():
    buch = MarkenBuch(jetzt=Uhr(), log=Aufzeichner())
    buch.ausgeben(quelle="auth", turn_id="turn-1")
    erfolge, fehler = [], []
    start = threading.Barrier(9)

    def arbeiter():
        start.wait()
        try:
            buch.einloesen("turn-1")
            erfolge.append(1)
        except MarkenFehler:
            fehler.append(1)

    threads = [threading.Thread(target=arbeiter) for _ in range(8)]
    for t in threads:
        t.start()
    start.wait()
    for t in threads:
        t.join()
    assert len(erfolge) == 1
    assert len(fehler) == 7


# ---------------------------------------------------------------------------
# Aktionsfreigabe
# ---------------------------------------------------------------------------

def test_freigabe_glueckspfad():
    uhr, log = Uhr(), Aufzeichner()
    buch = FreigabeBuch(frist_s=60.0, jetzt=uhr, log=log)
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    freigabe = buch.bestaetigen(nonce=nonce, action_hash="hash-a")
    assert isinstance(freigabe, Aktionsfreigabe)
    assert freigabe.action_hash == "hash-a"
    assert freigabe.ablauf_ts == 10_060.0
    eingeloest = buch.einloesen(action_hash="hash-a")
    assert eingeloest.action_hash == "hash-a"


def test_freigabe_falsche_nonce_abgelehnt():
    buch = FreigabeBuch(jetzt=Uhr(), log=Aufzeichner())
    buch.nonce_ausgeben(action_hash="hash-a")
    with pytest.raises(MarkenFehler):
        buch.bestaetigen(nonce="erfunden", action_hash="hash-a")
    # Positivkontrolle.
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    buch.bestaetigen(nonce=nonce, action_hash="hash-a")


def test_freigabe_falscher_hash_verbrennt_nonce():
    buch = FreigabeBuch(jetzt=Uhr(), log=Aufzeichner())
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    with pytest.raises(MarkenFehler):
        buch.bestaetigen(nonce=nonce, action_hash="hash-b")
    # Ein Fehlversuch darf keinen zweiten erlauben: die Nonce ist verbrannt.
    with pytest.raises(MarkenFehler):
        buch.bestaetigen(nonce=nonce, action_hash="hash-a")
    # Und fuer hash-b gibt es deshalb auch keine Freigabe.
    with pytest.raises(MarkenFehler):
        buch.einloesen(action_hash="hash-b")
    # Positivkontrolle: frische Nonce, richtiger Hash.
    nonce2 = buch.nonce_ausgeben(action_hash="hash-a")
    buch.bestaetigen(nonce=nonce2, action_hash="hash-a")
    buch.einloesen(action_hash="hash-a")


def test_freigabe_nonce_nur_einmal_bestaetigbar():
    buch = FreigabeBuch(jetzt=Uhr(), log=Aufzeichner())
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    buch.bestaetigen(nonce=nonce, action_hash="hash-a")
    with pytest.raises(MarkenFehler):
        buch.bestaetigen(nonce=nonce, action_hash="hash-a")


def test_freigabe_wiedereinloesung_abgelehnt():
    buch = FreigabeBuch(jetzt=Uhr(), log=Aufzeichner())
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    buch.bestaetigen(nonce=nonce, action_hash="hash-a")
    buch.einloesen(action_hash="hash-a")
    with pytest.raises(MarkenFehler):
        buch.einloesen(action_hash="hash-a")


def test_freigabe_ablauf_abgelehnt():
    uhr = Uhr()
    buch = FreigabeBuch(frist_s=60.0, jetzt=uhr, log=Aufzeichner())
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    buch.bestaetigen(nonce=nonce, action_hash="hash-a")
    uhr.t += 61.0
    with pytest.raises(MarkenFehler):
        buch.einloesen(action_hash="hash-a")
    # Auch die Nonce selbst laeuft ab.
    nonce2 = buch.nonce_ausgeben(action_hash="hash-b")
    uhr.t += 61.0
    with pytest.raises(MarkenFehler):
        buch.bestaetigen(nonce=nonce2, action_hash="hash-b")
    # Positivkontrolle.
    nonce3 = buch.nonce_ausgeben(action_hash="hash-c")
    buch.bestaetigen(nonce=nonce3, action_hash="hash-c")
    buch.einloesen(action_hash="hash-c")


def test_freigabe_einloesen_ohne_bestaetigung_abgelehnt():
    buch = FreigabeBuch(jetzt=Uhr(), log=Aufzeichner())
    buch.nonce_ausgeben(action_hash="hash-a")
    with pytest.raises(MarkenFehler):
        buch.einloesen(action_hash="hash-a")


def test_freigabe_paralleles_einloesen_genau_einmal():
    buch = FreigabeBuch(jetzt=Uhr(), log=Aufzeichner())
    nonce = buch.nonce_ausgeben(action_hash="hash-a")
    buch.bestaetigen(nonce=nonce, action_hash="hash-a")
    erfolge, fehler = [], []
    start = threading.Barrier(9)

    def arbeiter():
        start.wait()
        try:
            buch.einloesen(action_hash="hash-a")
            erfolge.append(1)
        except MarkenFehler:
            fehler.append(1)

    threads = [threading.Thread(target=arbeiter) for _ in range(8)]
    for t in threads:
        t.start()
    start.wait()
    for t in threads:
        t.join()
    assert len(erfolge) == 1
    assert len(fehler) == 7


# ---------------------------------------------------------------------------
# API-Kontingent
# ---------------------------------------------------------------------------

def test_kontingent_glueckspfad():
    buch = KontingentBuch(jetzt=Uhr(), log=Aufzeichner())
    kid = buch.ausgeben(quelle="wake_word")
    assert isinstance(kid, str) and kid
    buch.einloesen_fuer_egress(kid)
    kid2 = buch.ausgeben(quelle="rundenmarke")
    buch.einloesen_fuer_egress(kid2)


def test_kontingent_nur_aus_erlaubten_quellen():
    buch = KontingentBuch(jetzt=Uhr(), log=Aufzeichner())
    for quelle in ("auth", "mind", "request", "", "runden_marke"):
        with pytest.raises(MarkenFehler):
            buch.ausgeben(quelle=quelle)
    # Positivkontrolle beider erlaubter Quellen.
    buch.einloesen_fuer_egress(buch.ausgeben(quelle="wake_word"))
    buch.einloesen_fuer_egress(buch.ausgeben(quelle="rundenmarke"))


def test_kontingent_genau_ein_egress_aufruf():
    buch = KontingentBuch(jetzt=Uhr(), log=Aufzeichner())
    kid = buch.ausgeben(quelle="wake_word")
    buch.einloesen_fuer_egress(kid)
    with pytest.raises(MarkenFehler):
        buch.einloesen_fuer_egress(kid)


def test_kontingent_ablauf_abgelehnt():
    uhr = Uhr()
    buch = KontingentBuch(frist_s=60.0, jetzt=uhr, log=Aufzeichner())
    kid = buch.ausgeben(quelle="wake_word")
    uhr.t += 61.0
    with pytest.raises(MarkenFehler):
        buch.einloesen_fuer_egress(kid)
    # Positivkontrolle.
    kid2 = buch.ausgeben(quelle="wake_word")
    buch.einloesen_fuer_egress(kid2)


def test_kontingent_unbekannte_id_abgelehnt():
    buch = KontingentBuch(jetzt=Uhr(), log=Aufzeichner())
    with pytest.raises(MarkenFehler):
        buch.einloesen_fuer_egress("erfunden")


def test_kontingent_autorisiert_keine_aktion_und_deklassifiziert_nichts():
    """Die Zusage ist aufrufbar und konstant -- gueltig wie verbraucht,
    bekannt wie erfunden."""
    buch = KontingentBuch(jetzt=Uhr())
    kid = buch.ausgeben(quelle="wake_word")
    assert buch.erlaubt_aktion(kid) is False
    assert buch.erlaubt_deklassifizierung(kid) is False
    buch.einloesen_fuer_egress(kid)
    assert buch.erlaubt_aktion(kid) is False
    assert buch.erlaubt_deklassifizierung(kid) is False
    assert buch.erlaubt_aktion("erfunden") is False
    assert buch.erlaubt_deklassifizierung("erfunden") is False


def test_kontingent_paralleles_einloesen_genau_einmal():
    buch = KontingentBuch(jetzt=Uhr(), log=Aufzeichner())
    kid = buch.ausgeben(quelle="wake_word")
    erfolge, fehler = [], []
    start = threading.Barrier(9)

    def arbeiter():
        start.wait()
        try:
            buch.einloesen_fuer_egress(kid)
            erfolge.append(1)
        except MarkenFehler:
            fehler.append(1)

    threads = [threading.Thread(target=arbeiter) for _ in range(8)]
    for t in threads:
        t.start()
    start.wait()
    for t in threads:
        t.join()
    assert len(erfolge) == 1
    assert len(fehler) == 7


# ---------------------------------------------------------------------------
# request_id und Audit
# ---------------------------------------------------------------------------

def test_request_id_autorisiert_nichts():
    """Die request_id fuer Mind ist eine opake Kennung. Kein Buch kennt sie;
    wer sie vorweist, bekommt nichts."""
    rid = neue_request_id()
    assert isinstance(rid, str) and rid
    assert neue_request_id() != rid
    marken = MarkenBuch(jetzt=Uhr())
    with pytest.raises(MarkenFehler):
        marken.einloesen(rid)
    assert marken.initiator(rid) == "background"
    freigaben = FreigabeBuch(jetzt=Uhr())
    with pytest.raises(MarkenFehler):
        freigaben.einloesen(action_hash=rid)
    kontingente = KontingentBuch(jetzt=Uhr())
    with pytest.raises(MarkenFehler):
        kontingente.einloesen_fuer_egress(rid)
    assert kontingente.erlaubt_aktion(rid) is False
    assert kontingente.erlaubt_deklassifizierung(rid) is False


def test_audit_sieht_ausgabe_einloesung_und_ablehnung():
    log = Aufzeichner()

    marken = MarkenBuch(jetzt=Uhr(), log=log)
    marken.ausgeben(quelle="auth", turn_id="t1")
    marken.einloesen("t1")
    with pytest.raises(MarkenFehler):
        marken.einloesen("t1")
    assert log.handlungen("rundenmarke") == [
        "ausgabe", "einloesung", "ablehnung"]

    freigaben = FreigabeBuch(jetzt=Uhr(), log=log)
    nonce = freigaben.nonce_ausgeben(action_hash="h1")
    freigaben.bestaetigen(nonce=nonce, action_hash="h1")
    freigaben.einloesen(action_hash="h1")
    with pytest.raises(MarkenFehler):
        freigaben.einloesen(action_hash="h1")
    assert log.handlungen("aktionsfreigabe") == [
        "nonce_ausgabe", "ausgabe", "einloesung", "ablehnung"]

    kontingente = KontingentBuch(jetzt=Uhr(), log=log)
    kid = kontingente.ausgeben(quelle="wake_word")
    kontingente.einloesen_fuer_egress(kid)
    with pytest.raises(MarkenFehler):
        kontingente.einloesen_fuer_egress(kid)
    assert log.handlungen("api_kontingent") == [
        "ausgabe", "einloesung", "ablehnung"]


def test_sprachregelung_keine_verbotenen_begriffe():
    """Design 1.3: im Code und in Meldungen keine Woerter, die mehr
    behaupten als eine Marke leistet."""
    import inspect

    import daimon.hub.marks as m
    import daimon.hub.tickets as t

    quelltext = inspect.getsource(m) + inspect.getsource(t)
    for begriff in ("capability", "unfälschbar", "unfaelschbar",
                    "physisch", "beweist", "beweis"):
        assert begriff not in quelltext.lower(), begriff
