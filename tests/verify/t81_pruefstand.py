#!/usr/bin/env python3
"""Pruefstand fuer T-8.1 -- der Zeitplaner (Termine und Fokusbloecke).

Geprueft wird Kriterium fuer Kriterium, ohne `&&`-Verkettung. Ein rotes
Kriterium verhindert nicht die Messung der uebrigen; ein Kriterium OHNE
Messung zaehlt als rot.

  K1  Ein faelliger Termin feuert: Blase am Ereignis-Weg UND Spruch am
      Sprechweg, Anlass `termin_faellig` (mit Positivkontrolle)
  K2  Ein noch nicht faelliger Termin feuert NICHT
  K3  Ein Termin feuert genau EINMAL -- zwei Runden, ein Ereignis
  K4  Neustart-Persistenz: ein waehrend des "Stopps" faellig gewordener
      Termin wird vom neu geoeffneten Store nachgeholt
  K5  Die Herkunftsmarke wird NICHT umgeschrieben -- was am Eintrag steht,
      geht hinaus -- und sie kommt vom PEER, nicht aus dem Feld `marke`
  K6  Fokus: `fokus_start` legt an, `fokus_stop` verhindert das Feuern, ein
      ausgelaufener Block meldet `fokus_ende`
  K7  Rechte: plan.db, -wal, -shm und der Anfrage-Socket sind 0600
  K8  Ein Nutzertitel kommt an der WIRKLICHEN Senke nicht durch:
      `hub.sprechtext.aus_vorlage`, nicht ein Feld im Selbstbericht
  K9  Der Umzug der Datenbank aus der alten Lage toetet den Dienst NICHT,
      wenn das Elternverzeichnis nur lesbar ist (mit Positivkontrolle,
      dass die Schreibsperre ueberhaupt wirkte)
  K10 Ein Spruch OHNE Feld `markierung` wird abgelehnt -- eine fehlende
      Marke ist keine starke Marke (mit Positivkontrolle)

Zu K5 und K8, dem Befund vom 26.08.: hier stand als Sollverhalten, dass der
Dienst `user_ptt` in `trusted` umschreibt. Das war die Luecke selbst -- was
das Mikrofon am gehaltenen Taster aufnahm, wurde damit ungefragt gesprochen.
Gueltig ist `sprechtext.aus_vorlage` (Design 8.3: variable Anteile sind
ausschliesslich `trusted`); K8 misst gegen sie und nicht gegen ein Feld.

DIE ZENTRALE PRUEFFRAGE DIESES AUFTRAGS:

    Erinnert der Zeitplaner zur richtigen Zeit, genau einmal, auch ueber
    einen Neustart hinweg -- und bleibt die Herkunft eines Titels auf dem
    ganzen Weg erhalten?

Gemessen wird an der Naht, nicht am Stueck: ECHTER `Store` auf einer
Datenbank im Temp-Verzeichnis, ECHTER `Plan` mit injizierter Uhr, und die
Senken sind Sammler an der Stelle, an der im Betrieb plan.sock und
tts-say.sock haengen. Die Uhr ist injiziert -- keine einzige Sekunde
Wartezeit ist eine Messung, sondern jede gelesene Faelligkeit eine.

Der Pruefstand fasst den Produktcode nicht an und braucht keine laufenden
Dienste.
"""

import json
import os
import stat
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

PRUEFLING = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(PRUEFLING))
os.chdir(PRUEFLING)

from daimon.common.protocol import Mark, Marked          # noqa: E402
from daimon.plan.daemon import (MIND_UNIT, Plan,          # noqa: E402
                                 eigener_socket)
from daimon.plan.store import Store                      # noqa: E402

ERGEBNISSE = []


def kriterium(name: str, ok: bool, beleg: str = "") -> None:
    ERGEBNISSE.append((name, bool(ok)))
    print(f"[{'GRUEN' if ok else 'ROT'}] {name}" + (f" -- {beleg}" if beleg
                                                    else ""))


class Sammler:
    """Die Senke an der Stelle von plan.sock / tts-say.sock."""

    def __init__(self) -> None:
        self.nachrichten = []

    def __call__(self, n: dict) -> None:
        self.nachrichten.append(n)


def plan_mit(tmp: Path, jetzt: list[float]):
    """ECHTER Store, ECHTER Plan, injizierte Uhr, gesammelte Senken."""
    store = Store(tmp / "plan.db", uhr=lambda: jetzt[0])
    store.migrieren()
    blasen, sprueche = Sammler(), Sammler()
    plan = Plan(store, uhr=lambda: jetzt[0],
                ereignis_senden=blasen, spruch_senden=sprueche)
    return store, plan, blasen, sprueche


def k1_faelliger_termin_feuert() -> None:
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, blasen, sprueche = plan_mit(Path(d), jetzt)
        store.anlegen("termin", Marked("K1-Kanarie", Mark.USER_PTT), 1_060.0)
        ok_vorher = plan.runde() == 0 and not blasen.nachrichten
        jetzt[0] = 1_061.0
        gemeldet = plan.runde()
        blase = blasen.nachrichten[0] if blasen.nachrichten else {}
        spruch = sprueche.nachrichten[0] if sprueche.nachrichten else {}
        ok = (ok_vorher and gemeldet == 1
              and blase.get("type") == "termin_faellig"
              and blase.get("payload", {}).get("text") == "K1-Kanarie"
              and spruch.get("art") == "sprich"
              and spruch.get("anlass") == "termin_faellig"
              and spruch.get("werte", {}).get("titel") == "K1-Kanarie")
        kriterium("K1 faelliger Termin feuert (Blase UND Spruch)", ok,
                  f"blase={blase.get('type')}, anlass={spruch.get('anlass')}")


def k2_unfaelliger_termin_schweigt() -> None:
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, blasen, sprueche = plan_mit(Path(d), jetzt)
        store.anlegen("termin", Marked("K2-Kanarie", Mark.USER_PTT), 9_999.0)
        for _ in range(3):
            plan.runde()
        ok = not blasen.nachrichten and not sprueche.nachrichten
        kriterium("K2 unfaelliger Termin feuert nicht", ok,
                  f"{len(blasen.nachrichten)} Blasen nach 3 Runden")


def k3_genau_einmal() -> None:
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, blasen, sprueche = plan_mit(Path(d), jetzt)
        store.anlegen("termin", Marked("K3-Kanarie", Mark.USER_PTT), 1_000.0)
        plan.runde()
        plan.runde()
        plan.runde()
        ok = (len(blasen.nachrichten) == 1
              and store.liste("gemeldet") != []
              and store.liste("offen") == [])
        kriterium("K3 ein Termin feuert genau einmal", ok,
                  f"{len(blasen.nachrichten)} Blasen nach 3 Runden")


def k4_neustart_holt_nach() -> None:
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, blasen, _ = plan_mit(Path(d), jetzt)
        store.anlegen("termin", Marked("K4-Kanarie", Mark.USER_PTT), 1_100.0)
        store.schliessen()          # "Stopp" -- der Dienst ist weg
        jetzt[0] = 1_200.0          # waehrenddessen wird der Termin faellig
        store2 = Store(Path(d) / "plan.db", uhr=lambda: jetzt[0])
        blasen2 = Sammler()
        plan2 = Plan(store2, uhr=lambda: jetzt[0], ereignis_senden=blasen2)
        gemeldet = plan2.runde()
        ok = (gemeldet == 1
              and blasen2.nachrichten
              and blasen2.nachrichten[0]["payload"]["text"] == "K4-Kanarie")
        kriterium("K4 Neustart holt faellig Gewordenes nach", ok)


def k5_marke_bleibt_erhalten() -> None:
    # Jede Herkunft bekommt einen EIGENEN Plan: `Proaktiv` haelt einen
    # Mindestabstand pro Anlass, und zwei Termine in derselben Minute waeren
    # ein Fall fuer genau diese Sperre -- nicht fuer die Markierung.
    marken = {}
    blasen_gesamt = 0
    for titel, mark in (("K5-ptt", Mark.USER_PTT),
                        ("K5-fremd", Mark.TAINTED)):
        with tempfile.TemporaryDirectory() as d:
            jetzt = [1_000.0]
            store, plan, blasen, sprueche = plan_mit(Path(d), jetzt)
            store.anlegen("termin", Marked(titel, mark), 1_000.0)
            plan.runde()
            blasen_gesamt += len(blasen.nachrichten)
            if sprueche.nachrichten:
                marken[titel] = sprueche.nachrichten[0].get("markierung")

    # Und die Herkunft kommt vom PEER: eine Anfrage ohne Unit darf sich keine
    # geben ("Ein Feld, das der Absender setzt, sagt nichts", hub/policy.py).
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, _, _ = plan_mit(Path(d), jetzt)
        plan.bediene_anfrage({"v": 1, "art": "neu", "titel": "K5-behauptet",
                              "wann": "in 20 minuten", "marke": "trusted"})
        behauptet = store.liste()[0]["titel"].mark
        plan.bediene_anfrage({"v": 1, "art": "neu", "titel": "K5-mind",
                              "wann": "in 30 minuten", "marke": "user_ptt"},
                             unit=MIND_UNIT)
        vom_mind = [e["titel"].mark for e in store.liste()
                    if str(e["titel"].value) == "K5-mind"][0]

    ok = (marken.get("K5-ptt") == "user_ptt"        # NICHT umgeschrieben
          and marken.get("K5-fremd") == "tainted"
          # Die Blase kommt in BEIDEN Faellen -- Sichtbarmachung ist
          # keine Aeusserung.
          and blasen_gesamt == 2
          and behauptet is Mark.TAINTED
          # Positivkontrolle: der Mind reicht durch -- eine Ableitung, die
          # immer `tainted` liefert, bestuende die Zeile darueber.
          and vom_mind is Mark.USER_PTT)
    kriterium("K5 Herkunftsmarke unveraendert und vom Peer", ok,
              json.dumps({**marken, "behauptet": behauptet.value,
                          "vom_mind": vom_mind.value}, ensure_ascii=False))


def k7_rechte() -> None:
    """0600 auf allem, was Termine enthaelt -- Datenbank, WAL, Socket."""
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, _, _ = plan_mit(Path(d), jetzt)
        store.anlegen("termin", Marked("K7-Kanarie", Mark.TAINTED), 9_999.0)
        pfade = [Path(d) / f"plan.db{e}" for e in ("", "-wal", "-shm")]
        vorhanden = [p for p in pfade if p.exists()]
        sock_pfad = str(Path(d) / "plan-anfrage.sock")
        srv = eigener_socket(sock_pfad)
        try:
            vorhanden.append(Path(sock_pfad))
            modi = {p.name: stat.S_IMODE(p.stat().st_mode) for p in vorhanden}
        finally:
            srv.close()
        # Positivkontrolle: WAL und Socket muessen ueberhaupt dagewesen sein,
        # sonst misst "alles 0600" eine leere Menge.
        ok = (len(modi) == 4 and set(modi.values()) == {0o600})
        kriterium("K7 Datenbank, WAL, SHM und Socket sind 0600", ok,
                  json.dumps({k: oct(v) for k, v in sorted(modi.items())}))


def k8_nutzertitel_kommt_an_der_senke_nicht_durch() -> None:
    """Die Naht bis zur SENKE. Ein Feld im Selbstbericht des Dienstes sagt
    nichts darueber, ob etwas gesprochen wird -- das entscheidet
    `hub.sprechtext.aus_vorlage`, und die nimmt nur `trusted`."""
    from daimon.hub import sprechtext

    urteile = {}
    for titel, mark in (("K8-ptt", Mark.USER_PTT),
                        ("K8-fremd", Mark.TAINTED)):
        with tempfile.TemporaryDirectory() as d:
            jetzt = [1_000.0]
            store, plan, _, sprueche = plan_mit(Path(d), jetzt)
            store.anlegen("termin", Marked(titel, mark), 1_000.0)
            plan.runde()
            s = sprueche.nachrichten[0]
            urteile[titel] = sprechtext.aus_vorlage(
                s["anlass"], s["werte"], markierung=s["markierung"])

    # Positivkontrolle: das Fokusende formuliert der Dienst selbst und wird
    # gesprochen -- eine Senke, die alles ablehnt, bestuende den Rest.
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        _, plan, _, sprueche = plan_mit(Path(d), jetzt)
        plan.bediene_anfrage({"v": 1, "art": "fokus_start", "minuten": 5})
        jetzt[0] += 6 * 60
        plan.runde()
        s = sprueche.nachrichten[0]
        fokus = sprechtext.aus_vorlage(s["anlass"], s["werte"],
                                       markierung=s["markierung"])

    ok = (all(not u.ok and u.grund == sprechtext.GRUND_NICHT_TRUSTED
              for u in urteile.values())
          and fokus.ok)
    kriterium("K8 Nutzertitel wird nicht gesprochen, Fokusende schon", ok,
              json.dumps({k: (u.ok, u.grund) for k, u in urteile.items()}
                         | {"fokus_ende": (fokus.ok, fokus.text)},
                         ensure_ascii=False))


def k6_fokus() -> None:
    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        store, plan, blasen, sprueche = plan_mit(Path(d), jetzt)
        start = plan.bediene_anfrage({"v": 1, "art": "fokus_start",
                                      "minuten": 25})
        ok_start = start.get("ok") and store.liste("offen")[0]["art"] == "fokus"
        stopp = plan.bediene_anfrage({"v": 1, "art": "fokus_stop"})
        jetzt[0] = 1_000.0 + 26 * 60   # der gestoppte Block waere faellig
        plan.runde()
        ok_stopp = (stopp.get("gestoppt") == 1 and not blasen.nachrichten)
        # Ein Block, der NICHT gestoppt wird, meldet `fokus_ende`.
        plan.bediene_anfrage({"v": 1, "art": "fokus_start", "minuten": 5})
        jetzt[0] += 6 * 60
        plan.runde()
        ok_ende = (len(blasen.nachrichten) == 1
                   and blasen.nachrichten[0]["type"] == "fokus_ende"
                   and sprueche.nachrichten
                   and sprueche.nachrichten[0]["anlass"] == "fokus_ende")
        kriterium("K6 Fokus startet, Stopp verhindert, Ende meldet",
                  ok_start and ok_stopp and ok_ende,
                  f"start={ok_start}, stopp={ok_stopp}, ende={ok_ende}")


def k9_umzug_ueberlebt_nur_lesbares_elternverzeichnis() -> None:
    """Der Umzug aus `state_dir()/plan.db` nach `state_dir()/plan/plan.db`,
    gemessen unter der Rechtelage des Betriebs.

    Befund vom 26.08.: `os.replace` braucht Schreibrecht am QUELLverzeichnis,
    und genau das hat die Unit nicht mehr, seit `ReadWritePaths` nur noch
    `.../plan` freigibt. Der Umzug warf `OSError`, `main()` fing nichts,
    `Restart=on-failure` machte daraus eine Dauerschleife -- die Anlage, fuer
    die die Migration gebaut wurde, waere nicht mehr gestartet.

    Gemessen wird das ECHTE Verhalten am Dateisystem: Verzeichnis auf
    dr-x------, dann ein Store auf dem neuen Pfad. Ein Selbstbericht des
    Stores ("Umzug gelungen") sagt darueber nichts.
    """
    with tempfile.TemporaryDirectory() as d:
        basis = Path(d) / "state"
        basis.mkdir()
        jetzt = [1_000.0]
        alt = Store(basis / "plan.db", uhr=lambda: jetzt[0])
        alt.migrieren()
        alt.anlegen("termin", Marked("K9-Kanarie", Mark.TRUSTED), 9_999.0)
        alt.schliessen()
        (basis / "plan").mkdir()      # solange hier noch geschrieben werden darf
        os.chmod(basis, 0o500)        # dr-x------: lesen ja, schreiben nein
        try:
            # Positivkontrolle: die Sperre muss WIRKEN. Als root -- oder auf
            # einem Dateisystem ohne Rechte -- ginge der Schreibversuch durch,
            # und "der Dienst lebt" waere dann keine Messung, sondern eine
            # Tautologie: nicht gemessen, nicht rot.
            try:
                (basis / "schreibprobe").touch()
                (basis / "schreibprobe").unlink()
                gesperrt = False
            except OSError:
                gesperrt = True
            neu = Store(basis / "plan" / "plan.db", uhr=lambda: jetzt[0])
            neu.migrieren()                       # lebt er ueberhaupt?
            titel = [str(e["titel"].value) for e in neu.liste()]
            # "Lebt" heisst mehr als "wirft nicht": er nimmt auch NEUE Termine.
            neu.anlegen("termin", Marked("K9-danach", Mark.TRUSTED), 9_999.0)
            lebt = len(neu.liste()) == 2
            neu.schliessen()
        finally:
            os.chmod(basis, 0o700)    # sonst raeumt TemporaryDirectory nicht auf
        ok = gesperrt and lebt and "K9-Kanarie" in titel
        kriterium("K9 Umzug bei nur lesbarem Elternverzeichnis toetet nicht", ok,
                  json.dumps({"quellverzeichnis_gesperrt": gesperrt,
                              "titel_nach_umzug": titel, "lebt": lebt},
                             ensure_ascii=False))


def k10_spruch_ohne_marke_wird_abgelehnt() -> None:
    """Eine FEHLENDE Marke ist keine starke Marke.

    Bis zum 26.08. stand in `sprechtext.aus_vorlage` `markierung: str =
    "trusted"`: wer das Feld wegliess, bekam die staerkste Marke geschenkt,
    und die Regel fuer `tts_ungefragt` war durch WEGLASSEN zu umgehen. Der
    Socket steht unter %t/daimon und ist von jedem Prozess desselben Nutzers
    erreichbar.

    Gemessen wird an der gueltigen Stelle -- `aus_vorlage` entscheidet ueber
    `taint.pruefe_senke` -- und mit der Nachricht, die der Dienst wirklich
    schickt, nicht mit einer nachgebauten.
    """
    from daimon.hub import sprechtext

    with tempfile.TemporaryDirectory() as d:
        jetzt = [1_000.0]
        _, plan, _, sprueche = plan_mit(Path(d), jetzt)
        plan.bediene_anfrage({"v": 1, "art": "fokus_start", "minuten": 5})
        jetzt[0] += 6 * 60
        plan.runde()
        s = sprueche.nachrichten[0]

    # Feld fehlt -> die Vorgabe der Senke greift. Kein Wert wird gesetzt,
    # sonst maesse die Zeile die Vorgabe DIESES Pruefstands.
    ohne = sprechtext.aus_vorlage(s["anlass"], s["werte"])
    # Positivkontrolle: dieselbe Nachricht MIT `trusted` kommt durch. Ohne sie
    # bestuende K10 auch eine Senke, die grundsaetzlich alles ablehnt.
    mit = sprechtext.aus_vorlage(s["anlass"], s["werte"], markierung="trusted")

    ok = (not ohne.ok and ohne.grund == sprechtext.GRUND_NICHT_TRUSTED
          and mit.ok)
    kriterium("K10 Spruch ohne Marke wird abgelehnt, mit trusted nicht", ok,
              json.dumps({"anlass": s.get("anlass"),
                          "ohne_marke": (ohne.ok, ohne.grund),
                          "mit_trusted": (mit.ok, mit.text)},
                         ensure_ascii=False))


def messen(name: str, fn) -> None:
    """Ein Kriterium OHNE Messung zaehlt als rot -- und haelt die uebrigen
    nicht auf. Ohne diesen Rahmen bricht eine leere Sammlerliste (`[0]`) den
    ganzen Lauf ab, und die danach stehenden Kriterien waeren nicht rot,
    sondern ungemessen: genau der Unterschied, den ein Pruefstand zeigen muss.
    """
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 -- ungemessen ist rot, nicht Ende
        print(traceback.format_exc(), file=sys.stderr)
        kriterium(name, False, f"nicht gemessen: {type(exc).__name__}: {exc}")


def main() -> int:
    print(f"Pruefling: {PRUEFLING}")
    messen("K1", k1_faelliger_termin_feuert)
    messen("K2", k2_unfaelliger_termin_schweigt)
    messen("K3", k3_genau_einmal)
    messen("K4", k4_neustart_holt_nach)
    messen("K5", k5_marke_bleibt_erhalten)
    messen("K6", k6_fokus)
    messen("K7", k7_rechte)
    messen("K8", k8_nutzertitel_kommt_an_der_senke_nicht_durch)
    messen("K9", k9_umzug_ueberlebt_nur_lesbares_elternverzeichnis)
    messen("K10", k10_spruch_ohne_marke_wird_abgelehnt)
    gruen = sum(1 for _, ok in ERGEBNISSE if ok)
    print(f"\nBilanz: {gruen}/{len(ERGEBNISSE)} Kriterien gruen")
    return 0 if gruen == len(ERGEBNISSE) and len(ERGEBNISSE) == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
