"""T-4.3.t — die Auswertungsreihenfolge steht fest, BEVOR es die Engine gibt.

Warum diese Datei vor `daimon/hub/policy.py` entsteht
----------------------------------------------------------------------------
Eine Policy, die nach der Implementierung beschrieben wird, beschreibt die
Implementierung -- nicht die Zusage. Hier steht deshalb zuerst, was gelten
soll; T-4.4 baut danach etwas, das diese Tests grün macht. Solange die Engine
fehlt, ist **jeder** Test dieser Datei rot, und zwar mit einer lesbaren
Meldung statt eines Importfehlers: `lade_policy()` fängt den fehlenden Import
und ruft `pytest.fail`. Ein `ImportError` beim Einsammeln würde die Datei
unbrauchbar machen, ein `skip` würde Rot in Grün verwandeln.

Der gepinnte Vertrag
----------------------------------------------------------------------------
`daimon.hub.policy` stellt bereit:

    Anfrage(action_id, params, session_id, request_id, quelle,
            marke=None, initiator=None, params_hash=None)

        `quelle`   -- "parser" (deterministischer Hub-Parser aus der
                      Äußerung) oder "modell" (aus einer Modellausgabe).
        `marke`    -- die eingelöste Rundenmarke oder None.
        `initiator` und `params_hash` sind im Request ABSICHTLICH vorhanden
                      und werden ABSICHTLICH ignoriert: sie stehen hier, damit
                      ein Test sie fälschen kann. Design §1357: „Ein Feld, das
                      der Absender setzt, sagt nichts."

    Policy.aus_dateien(katalog, regeln)  -> Policy
    Policy.entscheide(anfrage)           -> Entscheidung

    Entscheidung.verdikt      -- "allow" | "ask" | "deny"
    Entscheidung.grund        -- maschinenlesbar, z.B. "unknown_action"
    Entscheidung.initiator    -- vom Hub abgeleitet, nie aus dem Request
    Entscheidung.params_hash  -- vom Hub berechnet, nie aus dem Request

    Policy.sicherung_werfen(grund)   -- Circuit Breaker an
    Policy.geste_gesehen(zeitpunkt)  -- Gestenfenster öffnen (Design §2.5)

Die Regeln (`regeln`) kommen als Liste von Ebenen, damit `deny` als
**Vereinigung über alle Ebenen** prüfbar ist: eine Ebene, die verbietet,
kann von keiner anderen überstimmt werden.
"""
from __future__ import annotations

import pytest

# Der Katalog, gegen den entschieden wird. Bewusst klein und hier im Test
# aufgeschrieben statt aus config/actions/core.yaml gelesen: dieser Test prüft
# die Auswertung, nicht die Freigabeliste. Die Felder sind dieselben.
KATALOG = {
    "media.playpause": {
        "destructive": False, "reversible_by": "media.playpause",
        "externally_visible": False, "open_world": True,
        "never_cacheable": True, "direct": True,
        "foreground": "allow", "background": "deny", "scheduled": "deny",
    },
    "window.to_next_desktop": {
        "destructive": False, "reversible_by": "window.to_previous_desktop",
        "externally_visible": False, "open_world": False,
        "never_cacheable": True, "direct": False,
        "foreground": "ask", "background": "deny", "scheduled": "deny",
    },
    "audio.volume.set": {
        "destructive": False, "reversible_by": "audio.volume.set",
        "externally_visible": False, "open_world": False,
        "never_cacheable": True, "direct": True,
        "foreground": "allow", "background": "deny", "scheduled": "deny",
        "params": {"value": {"type": "float", "value_between": [0.0, 1.0],
                             "required": True}},
    },
    # Design §2.5: erteilt UND nur im Gestenfenster benutzbar.
    "clipboard.read": {
        "destructive": False, "reversible_by": None,
        "externally_visible": False, "open_world": False,
        "never_cacheable": True, "direct": False,
        "foreground": "ask", "background": "deny", "scheduled": "deny",
        "gestenfenster_s": 2.0,
    },
}

# Zwei Ebenen, wie sie in T-4.4 aus mitgelieferter und eigener Konfiguration
# entstehen. Die zweite ist die "spezifischere" -- und darf trotzdem nichts
# überstimmen, was die erste verbietet.
EBENE_BASIS = [
    {"action_id": "media.playpause", "verdikt": "allow"},
    {"action_id": "window.to_next_desktop", "verdikt": "ask"},
]
EBENE_EIGEN = [
    {"action_id": "media.playpause", "verdikt": "deny",
     "grund": "vom Nutzer verboten"},
]


def lade_policy():
    """Die Engine -- oder ein lesbares Rot.

    Kein `importorskip`: ein übersprungener Test ist grün, und grün ist hier
    genau falsch. Bis T-4.4 existiert, soll jede einzelne Zusage dieser Datei
    sichtbar offen sein.
    """
    try:
        from daimon.hub import policy
    except ImportError as fehler:
        pytest.fail(f"daimon.hub.policy fehlt noch (T-4.4): {fehler}")
    return policy


def policy_mit(regeln=None, katalog=None):
    p = lade_policy()
    return p.Policy.aus_dateien(katalog if katalog is not None else KATALOG,
                                regeln if regeln is not None else [EBENE_BASIS])


# Eine eingeloeste Rundenmarke traegt eine Frist -- eine ohne waere keine.
# Diese Form ist beim Bau von T-4.4 entstanden: vorher stand hier eine blosse
# Zeichenkette, und "abgelaufen" war ein Name, kein Zustand.
MARKE_GUELTIG = {"id": "m1", "gueltig_bis": 1_000_000.0}
MARKE_ABGELAUFEN = {"id": "m1", "gueltig_bis": 50.0}


def anfrage(action_id, **felder):
    p = lade_policy()
    grund = {"params": {}, "session_id": "s1", "request_id": "r1",
             "quelle": "parser", "marke": MARKE_GUELTIG}
    grund.update(felder)
    return p.Anfrage(action_id=action_id, **grund)


# --------------------------------------------------------------------------
# Reihenfolge: deny -> ask -> allow, erster Treffer, Spezifität irrelevant
# --------------------------------------------------------------------------

def test_deny_schlaegt_ask_und_allow():
    pol = policy_mit([[
        {"action_id": "media.playpause", "verdikt": "allow"},
        {"action_id": "media.playpause", "verdikt": "ask"},
        {"action_id": "media.playpause", "verdikt": "deny"},
    ]])
    assert pol.entscheide(anfrage("media.playpause")).verdikt == "deny"


def test_ask_schlaegt_allow():
    pol = policy_mit([[
        {"action_id": "media.playpause", "verdikt": "allow"},
        {"action_id": "media.playpause", "verdikt": "ask"},
    ]])
    assert pol.entscheide(anfrage("media.playpause")).verdikt == "ask"


def test_reihenfolge_haengt_nicht_an_der_reihenfolge_der_regeln():
    """`deny` gewinnt, egal an welcher Stelle es steht.

    Ein Auswerter, der die Liste einfach von oben abarbeitet, besteht den
    Test darüber und fällt hier durch -- deshalb steht `deny` diesmal zuerst
    und einmal zuletzt.
    """
    vorn = policy_mit([[
        {"action_id": "media.playpause", "verdikt": "deny"},
        {"action_id": "media.playpause", "verdikt": "allow"},
    ]])
    hinten = policy_mit([[
        {"action_id": "media.playpause", "verdikt": "allow"},
        {"action_id": "media.playpause", "verdikt": "deny"},
    ]])
    assert vorn.entscheide(anfrage("media.playpause")).verdikt == "deny"
    assert hinten.entscheide(anfrage("media.playpause")).verdikt == "deny"


def test_spezifitaet_ist_irrelevant():
    """Eine Regel mit mehr Bedingungen gewinnt NICHT dadurch.

    Das ist die Stelle, an der Policy-Sprachen üblicherweise kippen: wer
    Spezifität gewinnen lässt, kann ein Verbot durch eine engere Erlaubnis
    aushebeln.
    """
    pol = policy_mit([[
        {"action_id": "media.playpause", "verdikt": "deny"},
        {"action_id": "media.playpause", "verdikt": "allow",
         "when": {"initiator": "foreground", "quelle": "parser",
                  "session_id": "s1"}},
    ]])
    assert pol.entscheide(anfrage("media.playpause")).verdikt == "deny"


def test_deny_ist_vereinigung_ueber_alle_ebenen():
    """Die zweite Ebene verbietet; die erste erlaubt. Verboten bleibt verboten."""
    pol = policy_mit([EBENE_BASIS, EBENE_EIGEN])
    e = pol.entscheide(anfrage("media.playpause"))
    assert e.verdikt == "deny"


def test_eine_ebene_kann_ein_deny_nicht_zuruecknehmen():
    """Auch andersherum: erst verboten, dann erlaubt -- bleibt verboten."""
    pol = policy_mit([EBENE_EIGEN, EBENE_BASIS])
    assert pol.entscheide(anfrage("media.playpause")).verdikt == "deny"


# --------------------------------------------------------------------------
# Unbekannt und unlesbar sind VERSCHIEDENE Zustände
# --------------------------------------------------------------------------

def test_unbekannte_aktion_ist_deny():
    pol = policy_mit()
    e = pol.entscheide(anfrage("fs.file.delete"))
    assert e.verdikt == "deny"
    assert e.grund == "unknown_action"


def test_unlesbares_argument_ist_ask_nicht_deny():
    """`ask`, nicht `deny` -- und ausdrücklich ein anderer Grund.

    Beides in einen Topf zu werfen wäre bequem und falsch: „kenne ich nicht"
    ist eine Aussage über den Katalog, „verstehe ich nicht" eine über diese
    eine Anfrage. Wer sie zusammenlegt, kann später nicht unterscheiden, ob
    das Modell etwas Verbotenes wollte oder nur Unsinn geschickt hat.
    """
    pol = policy_mit()
    e = pol.entscheide(anfrage("audio.volume.set", params={"value": "laut"}))
    assert e.verdikt == "ask"
    assert e.grund == "unparseable_argument"


def test_wert_ausserhalb_der_schranke_ist_nicht_dasselbe_wie_unlesbar():
    """1.5 ist eine Zahl und trotzdem außerhalb von [0.0, 1.0]."""
    pol = policy_mit()
    e = pol.entscheide(anfrage("audio.volume.set", params={"value": 1.5}))
    assert e.verdikt == "deny"
    assert e.grund == "argument_out_of_range"


# --------------------------------------------------------------------------
# initiator: dieselbe Aktion, drei Herkünfte, drei Verdikte
# --------------------------------------------------------------------------

def test_dieselbe_aktion_drei_initiator_drei_verdikte():
    pol = policy_mit()
    im_vordergrund = pol.entscheide(anfrage("window.to_next_desktop",
                                            marke=MARKE_GUELTIG))
    im_hintergrund = pol.entscheide(anfrage("window.to_next_desktop",
                                            marke=None))
    geplant = pol.entscheide(anfrage("window.to_next_desktop", marke=None,
                                     quelle="scheduler"))
    assert im_vordergrund.initiator == "foreground"
    assert im_hintergrund.initiator == "background"
    assert geplant.initiator == "scheduled"
    assert im_vordergrund.verdikt == "ask"
    assert im_hintergrund.verdikt == "deny"
    assert geplant.verdikt == "deny"


def test_initiator_kommt_aus_der_marke_nicht_aus_dem_request():
    """Design §1357. Der eigene Test, den die Akzeptanzliste verlangt.

    Der Request behauptet `foreground` und legt keine Marke vor. Glaubt die
    Engine dem Feld, ist die gesamte Autorisierung eine Selbstauskunft --
    genau der Fehler, den dieses Projekt an mehreren Stellen vermieden hat.
    """
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop", marke=None,
                               initiator="foreground"))
    assert e.initiator == "background"
    assert e.verdikt == "deny"


def test_abgelaufene_marke_zaehlt_wie_keine():
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop",
                               marke=MARKE_ABGELAUFEN, jetzt=100.0))
    assert e.initiator == "background"
    assert e.verdikt == "deny"


# --------------------------------------------------------------------------
# params_hash rechnet der Hub
# --------------------------------------------------------------------------

def test_params_hash_wird_selbst_berechnet_und_der_mitgeschickte_ignoriert():
    pol = policy_mit()
    echt = pol.entscheide(anfrage("audio.volume.set", params={"value": 0.5}))
    gefaelscht = pol.entscheide(anfrage("audio.volume.set",
                                        params={"value": 0.5},
                                        params_hash="sha256:deadbeef"))
    assert echt.params_hash == gefaelscht.params_hash
    assert gefaelscht.params_hash != "sha256:deadbeef"
    assert echt.params_hash.startswith("sha256:")


def test_kanonisierung_macht_gleiche_parameter_gleich():
    """Reihenfolge und Schreibweise dürfen den Hash nicht ändern.

    Sonst hätte dieselbe Zustimmung je nach Serialisierung einen anderen
    Schlüssel im Zustimmungs-Cache -- und wäre damit wertlos.
    """
    pol = policy_mit()
    a = pol.entscheide(anfrage("audio.volume.set",
                               params={"value": 0.5}))
    b = pol.entscheide(anfrage("audio.volume.set",
                               params={"value": 0.50}))
    assert a.params_hash == b.params_hash


# --------------------------------------------------------------------------
# Die Direktbefehl-Ausnahme ist Hub-Eigentum (Design §263)
# --------------------------------------------------------------------------

def test_direkt_gilt_nur_fuer_parser_erkannte_aktionen():
    pol = policy_mit()
    vom_parser = pol.entscheide(anfrage("media.playpause", quelle="parser"))
    assert vom_parser.verdikt == "allow"


def test_dieselbe_aktion_aus_einer_modellausgabe_geht_durch_die_vorschau():
    """Das Katalogflag `direct: true` hilft dem Modell NICHT.

    Wäre es anders, könnte eine übernommene Modellausgabe alles auslösen, was
    im Katalog als direkt markiert ist -- und die Vorschau wäre eine
    Empfehlung statt einer Schranke.
    """
    pol = policy_mit()
    e = pol.entscheide(anfrage("media.playpause", quelle="modell"))
    assert e.verdikt == "ask"


def test_ohne_direct_im_katalog_hilft_auch_der_parser_nicht():
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop", quelle="parser"))
    assert e.verdikt == "ask"


# --------------------------------------------------------------------------
# Gestenfenster (Design §2.5): erteilt UND offen
# --------------------------------------------------------------------------

def test_gestenfenster_geschlossen_verweigert_trotz_erteilter_faehigkeit():
    pol = policy_mit()
    e = pol.entscheide(anfrage("clipboard.read", jetzt=100.0))
    assert e.verdikt == "deny"
    assert e.grund == "gesture_window_closed"


def test_gestenfenster_offen_fragt_nach():
    """Offen heißt nicht erlaubt -- es heißt nur: nicht schon deshalb verboten."""
    pol = policy_mit()
    pol.geste_gesehen(100.0)
    e = pol.entscheide(anfrage("clipboard.read", jetzt=101.0))
    assert e.verdikt == "ask"


def test_gestenfenster_faellt_nach_zwei_sekunden_zu():
    pol = policy_mit()
    pol.geste_gesehen(100.0)
    e = pol.entscheide(anfrage("clipboard.read", jetzt=102.5))
    assert e.verdikt == "deny"
    assert e.grund == "gesture_window_closed"


# --------------------------------------------------------------------------
# Zustimmungs-Cache: Schlüssel und Gültigkeiten
# --------------------------------------------------------------------------

def test_zustimmung_gilt_fuer_genau_diese_parameter():
    pol = policy_mit()
    erst = pol.entscheide(anfrage("window.to_next_desktop"))
    assert erst.verdikt == "ask"
    pol.zustimmung_merken(session_id="s1", action_id="window.to_next_desktop",
                          params_hash=erst.params_hash, gueltigkeit="session")
    assert pol.entscheide(anfrage("window.to_next_desktop")).verdikt == "allow"


def test_zustimmung_gilt_nicht_in_einer_anderen_sitzung():
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop"))
    pol.zustimmung_merken(session_id="s1", action_id="window.to_next_desktop",
                          params_hash=e.params_hash, gueltigkeit="session")
    fremd = pol.entscheide(anfrage("window.to_next_desktop", session_id="s2"))
    assert fremd.verdikt == "ask"


def test_once_gilt_genau_einmal():
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop"))
    pol.zustimmung_merken(session_id="s1", action_id="window.to_next_desktop",
                          params_hash=e.params_hash, gueltigkeit="once")
    assert pol.entscheide(anfrage("window.to_next_desktop")).verdikt == "allow"
    assert pol.entscheide(anfrage("window.to_next_desktop")).verdikt == "ask"


def test_ttl_laeuft_ab():
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop", jetzt=100.0))
    pol.zustimmung_merken(session_id="s1", action_id="window.to_next_desktop",
                          params_hash=e.params_hash, gueltigkeit="ttl:60",
                          jetzt=100.0)
    assert pol.entscheide(anfrage("window.to_next_desktop",
                                  jetzt=150.0)).verdikt == "allow"
    assert pol.entscheide(anfrage("window.to_next_desktop",
                                  jetzt=161.0)).verdikt == "ask"


def test_die_vier_gueltigkeiten_sind_genau_diese_vier():
    p = lade_policy()
    assert set(p.GUELTIGKEITEN) == {"once", "session", "ttl", "persistent"}


# --------------------------------------------------------------------------
# Circuit Breaker: schlägt jede Regel und jeden Modus
# --------------------------------------------------------------------------

def test_sicherung_schlaegt_allow():
    pol = policy_mit()
    pol.sicherung_werfen("zu viele Fehlschlaege")
    e = pol.entscheide(anfrage("media.playpause"))
    assert e.verdikt == "deny"
    assert e.grund == "circuit_breaker"


def test_sicherung_schlaegt_eine_erteilte_zustimmung():
    """Auch eine gemerkte Zustimmung hilft nicht mehr."""
    pol = policy_mit()
    e = pol.entscheide(anfrage("window.to_next_desktop"))
    pol.zustimmung_merken(session_id="s1", action_id="window.to_next_desktop",
                          params_hash=e.params_hash, gueltigkeit="persistent")
    pol.sicherung_werfen("Notaus")
    assert pol.entscheide(anfrage("window.to_next_desktop")).verdikt == "deny"


def test_sicherung_schlaegt_die_direktbefehl_ausnahme():
    pol = policy_mit()
    pol.sicherung_werfen("Notaus")
    e = pol.entscheide(anfrage("media.playpause", quelle="parser"))
    assert e.verdikt == "deny"
    assert e.grund == "circuit_breaker"


def test_sicherung_schlaegt_in_jedem_initiator_modus():
    for marke, quelle in ((MARKE_GUELTIG, "parser"), (None, "parser"),
                          (None, "scheduler")):
        pol = policy_mit()
        pol.sicherung_werfen("Notaus")
        e = pol.entscheide(anfrage("media.playpause", marke=marke,
                                   quelle=quelle))
        assert e.verdikt == "deny", (marke, quelle)
        assert e.grund == "circuit_breaker", (marke, quelle)


# --------------------------------------------------------------------------
# Jede Entscheidung ist protokollierbar -- auch die verweigerte
# --------------------------------------------------------------------------

def test_jede_entscheidung_nennt_ihren_grund_und_ihre_regel():
    """Design §722: protokolliert wird auch bei `deny`.

    Ohne `grund` und `regel` steht im Audit „verweigert" und sonst nichts --
    und niemand kann nachvollziehen, welche Zeile welcher Ebene das war.
    """
    pol = policy_mit([EBENE_BASIS, EBENE_EIGEN])
    e = pol.entscheide(anfrage("media.playpause"))
    assert e.grund
    assert e.regel is not None


# --------------------------------------------------------------------------
# Laden von der Platte (T-4.4): nur Freigegebenes wird zur Entscheidung
# --------------------------------------------------------------------------

def test_nur_approved_kommt_in_den_katalog(tmp_path):
    p = lade_policy()
    katalog = tmp_path / "core.yaml"
    katalog.write_text(
        "version: 1\nactions:\n"
        "  - id: a.erlaubt\n    status: approved\n    rationale: weil\n"
        "    foreground: allow\n    direct: true\n"
        "  - id: a.kandidat\n    status: candidate\n"
        "    foreground: allow\n    direct: true\n", encoding="utf-8")
    regeln = tmp_path / "policy.yaml"
    regeln.write_text("version: 1\nebenen: []\n", encoding="utf-8")
    pol = p.Policy.laden(katalog_pfad=katalog, regel_pfad=regeln)
    assert set(pol.katalog) == {"a.erlaubt"}
    # Und der Kandidat ist nicht bloss abwesend, sondern unbekannt:
    e = pol.entscheide(anfrage("a.kandidat"))
    assert (e.verdikt, e.grund) == ("deny", "unknown_action")


def test_approved_ohne_begruendung_ist_ein_ladefehler(tmp_path):
    p = lade_policy()
    katalog = tmp_path / "core.yaml"
    katalog.write_text(
        "version: 1\nactions:\n"
        "  - id: a.ohne\n    status: approved\n    foreground: allow\n",
        encoding="utf-8")
    regeln = tmp_path / "policy.yaml"
    regeln.write_text("version: 1\nebenen: []\n", encoding="utf-8")
    with pytest.raises(p.PolicyFehler) as fehler:
        p.Policy.laden(katalog_pfad=katalog, regel_pfad=regeln)
    assert "rationale" in str(fehler.value)


def test_der_mitgelieferte_katalog_laedt_und_entscheidet():
    """Gegen die echten Dateien im Repo, nicht gegen ein Muster."""
    p = lade_policy()
    pol = p.Policy.laden()
    assert len(pol.katalog) == 17
    vom_parser = pol.entscheide(anfrage("media.playpause", quelle="parser"))
    vom_modell = pol.entscheide(anfrage("media.playpause", quelle="modell"))
    ohne_marke = pol.entscheide(anfrage("media.playpause", marke=None))
    assert vom_parser.verdikt == "allow"
    assert vom_modell.verdikt == "ask"
    assert ohne_marke.verdikt == "deny"
    # Nichts aus der Kandidatenliste ist entscheidbar.
    assert pol.entscheide(anfrage("kwin:Kill Window")).grund == "unknown_action"
