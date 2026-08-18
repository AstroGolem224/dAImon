#!/usr/bin/env python3
"""Die NAHT fuer T-4.8, als eigener Prozess -- einmal gebaut, viermal gefahren.

    t48_naht.py <pruefling> <konfig-json>

Warum ein eigener Prozess
----------------------------------------------------------------------------
Zwei der drei erzwungenen Fehlerfaelle brauchen eine Umgebung, die man einem
laufenden Pruefstand nicht anziehen kann: ein winziges tmpfs (eigener
Mount-Namensraum) und ein `RLIMIT_FSIZE`. Wer die Naht dafuer zweimal baut --
einmal im Prozess, einmal daneben --, hat eine Regel und eine Attrappe, und
gemessen wird erfahrungsgemaess die Attrappe. Also laeuft JEDER Lauf, auch
die Positivkontrolle, durch genau diese Datei.

Was hier zusammenkommt
----------------------------------------------------------------------------
Der echte `Koordinator` (T-4.16) mit echter Policy, echtem Consent, echtem
Auftragsbuch, echter Schlange und echtem Audit. Attrappe ist nur der Broker
-- und der ist keine Attrappe im ueblichen Sinn: er MUTIERT die Datei
wirklich. Genau darum geht es. Ein Broker, der nichts taete, koennte
"Ursprungsdatei unveraendert" nicht von "Mutation abgebrochen" trennen.

Ausgabe: eine Zeile JSON auf stdout. Alles andere geht nach stderr.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARKE = {"id": "t48", "gueltig_bis": 1e9}
# Was der Broker anrichtet, wenn er drankommt. Der Text ist die Wirkung:
# steht er hinterher in der Datei, hat die Mutation stattgefunden.
MUTATION = b"MUTIERT-DURCH-DEN-BROKER\n"


def main(argv: list[str]) -> int:
    pruefling, konfig_roh = argv[1], argv[2]
    konfig = json.loads(konfig_roh)
    sys.path.insert(0, pruefling)

    paket = "dai" + "mon"
    mod = __import__(f"{paket}.hub.coordinator", fromlist=["Koordinator"])
    Koordinator = mod.Koordinator
    Policy = __import__(f"{paket}.hub.policy", fromlist=["Policy"]).Policy
    Consent = __import__(f"{paket}.hub.consent", fromlist=["Consent"]).Consent
    Auftragsbuch = __import__(f"{paket}.hub.order",
                              fromlist=["Auftragsbuch"]).Auftragsbuch
    Aktionsschlange = __import__(f"{paket}.hub.action_queue",
                                 fromlist=["Aktionsschlange"]).Aktionsschlange
    Audit = __import__(f"{paket}.hub.audit", fromlist=["Audit"]).Audit
    undo = __import__(f"{paket}.brokers.fs", fromlist=["undo"]).undo

    art = konfig["art"]
    quelle = Path(konfig["quelle"])
    zustand = Path(konfig["zustand"])
    zustand.mkdir(parents=True, exist_ok=True)

    gerufen: list = []
    artefakt: dict = {}
    undo_fehler = ""

    def undo_hop(*, action_id: str, params: dict):
        if art == "trash":
            a = undo.vorbereiten("trash", quelle=quelle,
                                 trash=Path(konfig["trash"]))
        elif art == "kopie":
            a = undo.vorbereiten("kopie", quelle=quelle,
                                 ablage=Path(konfig["ablage"]))
        elif art == "git-stash":
            a = undo.vorbereiten("git-stash", repo=Path(konfig["repo"]))
        else:
            raise SystemExit(f"unbekannte Art {art!r}")
        artefakt.update(art=a.art, pfad=str(a.pfad) if a.pfad else "",
                        groesse=a.groesse, verifiziert=a.verifiziert,
                        hinweis=a.hinweis)
        return a

    def broker(auftrag) -> dict:
        """Die MUTATION. Sie findet wirklich statt, sonst misst niemand etwas."""
        gerufen.append(getattr(auftrag, "action_id", "?"))
        if art == "trash":
            # Das Verschieben in den Papierkorb IST hier die Mutation; sie ist
            # schon passiert. Der Broker haette nichts mehr zu loeschen.
            pass
        else:
            quelle.write_bytes(MUTATION)
        return {"ok": True, "grund": ""}

    class K(Koordinator):
        def consent_abwarten(self, rueckfrage):        # kein Mensch im Lauf
            return "granted"

    k = K(policy=Policy.laden(),
          consent=Consent.laden(zustand / "consent"),
          auftragsbuch=Auftragsbuch(),
          schlange=Aktionsschlange(),
          audit=Audit.oeffnen(zustand / "audit"),
          broker=broker,
          vorschau=lambda action_id, params: f"{action_id} ausfuehren?",
          sprechen=lambda text: None,
          undo=undo_hop,
          uhr=lambda: 100.0)

    try:
        lauf = k.ausfuehren(action_id=konfig.get("action_id",
                                                 "media.playpause"),
                            params={"pfad": str(quelle)}, quelle="parser",
                            marke=MARKE, session_id="s48", turn_id="r48",
                            tool_use_id="t48")
        ergebnis = {"ausgefuehrt": bool(lauf.ausgefuehrt),
                    "grund": lauf.grund, "verdikt": lauf.verdikt,
                    "gesprochen": lauf.gesprochen,
                    "hops": sorted(lauf.dauer_ms)}
    except Exception as fehler:                 # nichts verschlucken
        undo_fehler = f"{type(fehler).__name__}: {fehler}"
        ergebnis = {"ausgefuehrt": False, "grund": "ausnahme", "verdikt": "",
                    "gesprochen": "", "hops": []}

    ergebnis.update(broker_gerufen=len(gerufen), artefakt=artefakt,
                    ausnahme=undo_fehler)
    print(json.dumps(ergebnis, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
