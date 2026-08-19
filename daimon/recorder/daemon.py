"""T-7.1 -- der Archivdienst. Der einzige Prozess mit Schreibrecht.

    python -m daimon.recorder.daemon [--runtime-dir DIR] [--archiv DATEI]

**Warum ein eigener Dienst und nicht ein Modul in den Augen.** Wer den
Bildschirm liest, haelt Frames, spricht mit dem Portal und laedt fremde
Bibliotheken -- die groesste Angriffsflaeche im Projekt. Bekommt derselbe
Prozess Schreibrecht aufs Archiv, schreibt eine kompromittierte Wahrnehmung
in die Vergangenheit. Getrennte Units, getrennte `ReadWritePaths`: die Augen
MELDEN, geschrieben wird hier.

**Der Zulauf ist ein Socket, kein gemeinsames Verzeichnis.** `recorder.sock`,
0600, Gegenstelle ueber `SO_PEERPIDFD` aufgeloest und gegen eine Unit-Liste
geprueft -- und seit dem 16.08. entscheidet dieselbe Gegenstelle auch, WELCHE
Art sie ablegen darf (`ART_JE_UNIT`): die Art waehlt das Regelwerk, und wer
sie frei setzen kann, waehlt sich seine Redaktion selbst. Das haelt keinen same-uid-Angreifer auf (Design §1.3) und soll es
nicht -- es haelt einen falsch verdrahteten eigenen Prozess auf und macht im
Nachhinein sichtbar, wer abgelegt hat.

**Der Aufraeumer laeuft im selben Faden wie das Annehmen.** Er ist ein
`DELETE` je Art und eine laufende Summe; dafuer einen Thread zu starten,
waere ein Nebenlaeufigkeitsproblem fuer nichts. Der annehmende Socket hat
ohnehin ein Zeitlimit -- der Takt haengt daran.

**Was hier NOCH NICHT verdrahtet ist:** `daimon-eyes` schickt nichts. Der
Zulauf steht und ist gemessen, aber der Absender kommt mit der Redaktion
(T-7.2) -- vorher waere jede Zeile im Archiv eine ungefilterte, und die
Reihenfolge "erst redigieren, dann schreiben" ist der Kern von T-7.2.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Callable, Iterable

from daimon.common import ipc
from daimon.common.config import (denylist_laden, denylist_pfade,
                                  konferenz_laden, load)
from daimon.common.logging import get_logger
from daimon.recorder import pause
from daimon.recorder.redaktion import Redaktion
from daimon.recorder.store import (ART_OCR, ART_TITEL, ART_TRANSKRIPT, Archiv,
                                   ArchivFehler)

PRODUZENT = "recorder"

# Takt der automatischen Pause. Gemessen am 13.08.: `pw-dump` kostet 6--9 ms
# und liefert 283 KiB, dazu das Auswerten. Bei 5 s waeren das ~0,24 % eines
# Kerns -- ein Fuenftel des Dauerlastbudgets aus Design 13.1 fuer eine
# Abfrage. Bei 15 s sind es ~0,08 %. Eine Konferenz, die kuerzer dauert als
# fuenfzehn Sekunden, gibt es nicht; ein Kalibrierknopf ist es trotzdem.
AUTOMATIK_INTERVALL_S = 15.0


def _fokus_klasse() -> str:
    """Die Fensterklasse des fokussierten Fensters, ueber `de.daimon.Focus`.

    Derselbe Weg, den die Augen gehen (T-5.5) -- eine ABFRAGE, weil
    `callDBus` aus dem KWin-Script genau einen Empfaenger trifft und der
    Fokusdienst ihn schon hat. Faellt sie aus, ist die Klasse leer: dann
    traegt der zweite Ausloeser, der fremde Mikrofonstrom.
    """
    try:
        import dbus
        objekt = dbus.SessionBus().get_object("de.daimon.Focus", "/Focus")
        roh = json.loads(str(objekt.Fenster(dbus_interface="de.daimon.Focus")))
        return str(roh.get("resource_class", ""))
    except Exception:      # noqa: BLE001 -- kein Watcher, keine Klasse
        return ""

# Wer an diesem Socket sprechen darf, UND WOMIT. Eine Tabelle und kein Feld
# in der Nachricht -- sonst suchte sich der Absender seine Rolle selbst aus.
#
# Die zweite Spalte ist neu und schliesst einen Riss, den die erste allein
# offenliess: `art` steht in der NACHRICHT und waehlt aus, WELCHES Urteil
# gefaellt wird. `transkript` fuehrt auf `urteil_ton()`, und das kennt weder
# Denylist noch DRM -- mit gutem Grund, ein gesprochener Satz hat kein
# Fenster. Nur durfte bis hierher JEDER der drei Absender jeden Text als
# Transkript deklarieren: der Augendienst haette den OCR-Text eines
# gesperrten Passwortmanagers als `art: "transkript"` ablegen koennen, und
# die Denylist waere nie gefragt worden. Die groesste Angriffsflaeche des
# Projekts (Modulkopf oben) haette damit an der Redaktion vorbeigeschrieben.
#
# Jetzt gilt: wer OCR meldet, meldet OCR. Die Art gehoert zum Absender, nicht
# zur Nachricht.
ART_JE_UNIT: dict[str, frozenset[str]] = {
    "daimon-eyes.service": frozenset({ART_OCR}),
    # T-7.1b: der Fokusdienst meldet Fenstertitel. Er ist der einzige, der
    # sie hat -- die Augen bekommen den `caption` absichtlich nicht.
    "daimon-focus.service": frozenset({ART_TITEL}),
    # T-7.4: der Ohren-Dienst meldet das Transkript, das er ohnehin in der
    # Hand hat. KEIN Leserecht, keine zweite Faehigkeit -- derselbe Socket,
    # derselbe eine Typ, dieselbe Redaktion davor.
    "daimon-ears.service": frozenset({ART_TRANSKRIPT}),
}

# Abgeleitet und nicht danebengeschrieben: zwei Listen, die dasselbe meinen,
# laufen auseinander, sobald jemand nur eine pflegt.
ERLAUBTE_UNITS = tuple(ART_JE_UNIT)

# Ein Zeilenzulauf ist eine Meldung. Groesser als das ist kein OCR-Text mehr,
# sondern ein Versuch, den Dienst mit einer Zeile vollzuschreiben.
MAX_ZEILE = 256 * 1024


class Recorder:
    def __init__(self, *, runtime_dir: Path, archiv: Archiv,
                 redaktion: Redaktion | None = None,
                 aufraeum_intervall_s: float = 3600.0,
                 automatik_intervall_s: float = AUTOMATIK_INTERVALL_S,
                 konferenz: Iterable[str] = pause.KONFERENZ_VORGABE,
                 fokus_klasse: Callable[[], str] = _fokus_klasse,
                 mikrofone: Callable[[], int | None] =
                 pause.fremde_mikrofonstroeme,
                 pausieren: Callable[..., dict] = pause.stoppe,
                 erlaubte_units=ERLAUBTE_UNITS, log=None) -> None:
        self.runtime_dir = runtime_dir
        self.archiv = archiv
        # Ohne Redaktion keine Ablage. Eine Vorgabe „dann eben ungefiltert"
        # waere genau die Reihenfolge, die T-7.2 umdreht -- deshalb steht
        # hier eine leere Denylist und KEIN Weg an ihr vorbei.
        self.redaktion = redaktion or Redaktion(runtime_dir=runtime_dir,
                                                kennungen={})
        self.intervall = float(aufraeum_intervall_s)
        self.automatik_intervall = float(automatik_intervall_s)
        self.konferenz = tuple(konferenz)
        self._fokus_klasse = fokus_klasse
        self._mikrofone = mikrofone
        self._pausieren = pausieren
        self._letzte_automatik = 0.0
        # `None` heisst "jede Unit" und ist der Weg, den ein Prueflauf ohne
        # systemd nimmt. Im Betrieb steht hier die Liste.
        self.erlaubte_units = (None if erlaubte_units is None
                               else tuple(erlaubte_units))
        self.log = log or get_logger("daimon-recorder")
        self._srv: socket.socket | None = None
        self._halt = False
        self._letztes_aufraeumen = 0.0

    # -- Eine Meldung ------------------------------------------------------

    def melde(self, nachricht: dict, *, unit: str | None = None) -> dict:
        """Eine abgelegte Meldung. Der Rueckweg traegt nur die id.

        `unit` ist die Gegenstelle aus `SO_PEERPIDFD`. `None` heisst "jede
        Art" und ist der Weg, den ein Prueflauf ohne systemd nimmt -- dieselbe
        Bedeutung wie bei `erlaubte_units`.
        """
        if not isinstance(nachricht, dict):
            return {"v": 1, "ok": False, "grund": "unlesbar"}
        typ = str(nachricht.get("typ", ""))
        try:
            ipc.pruefe_typ(PRODUZENT, typ)
        except ipc.MessageTypeError as exc:
            return {"v": 1, "ok": False, "grund": "typ", "meldung": str(exc)}
        art = str(nachricht.get("art", "")).strip().lower()
        if unit is not None and art not in ART_JE_UNIT.get(unit, frozenset()):
            # Kein Sonderfall fuer eine unbekannte Unit: die leere Menge oben
            # weist sie ab. `accept` haette sie ohnehin nicht durchgelassen --
            # aber ein Fail-open an dieser Stelle waere genau die Tuer, die
            # der Task schliesst.
            self.log.warn("Art gehoert nicht zu dieser Unit",
                          DAIMON_UNIT=str(unit)[:80], DAIMON_ART=art[:40])
            return {"v": 1, "ok": False, "grund": "art_nicht_erlaubt"}
        # T-7.2: DIE REDAKTION STEHT VOR DEM SCHREIBEN, nicht dahinter. Es
        # gibt in diesem Prozess keinen anderen Aufruf von
        # `Archiv.schreiben` -- wer einen ergaenzt, hebt den Task auf.
        #
        # T-7.4: ein Transkript hat kein Fenster. Es bekommt deshalb sein
        # eigenes Urteil -- nicht die Fensterpruefung mit leerer Klasse, die
        # es als „Kennung fehlt" verwerfen wuerde.
        stufe = str(nachricht.get("stufe", "redacted"))
        if art == ART_TRANSKRIPT:
            urteil = self.redaktion.urteil_ton(stufe=stufe)
        else:
            urteil = self.redaktion.urteil(
                str(nachricht.get("klasse", "")),
                drm=bool(nachricht.get("drm", False)),
                stufe=stufe)
        if not urteil.schreibt:
            self.log.info("nicht mitgeschnitten",
                          DAIMON_GRUND=urteil.grund,
                          DAIMON_KLASSE=str(nachricht.get("klasse", ""))[:120])
            return {"v": 1, "ok": True, "id": 0, "stufe": urteil.stufe,
                    "grund": urteil.grund}
        try:
            eintrag_id = self.archiv.schreiben(
                str(nachricht.get("art", "")),
                str(nachricht.get("text", "")),
                fenster=str(nachricht.get("fenster", "")),
                stufe=urteil.stufe)
        except ArchivFehler as exc:
            # Eine abgewiesene Ablage ist ein Befund und kein Rauschen: sie
            # heisst, dass jemand etwas schreiben wollte, was nicht ins
            # Archiv gehoert.
            self.log.warn("Ablage abgewiesen",
                          DAIMON_GRUND=str(exc)[:200])
            return {"v": 1, "ok": False, "grund": "abgewiesen",
                    "meldung": str(exc)[:200]}
        return {"v": 1, "ok": True, "id": eintrag_id}

    # -- Aufraeumen --------------------------------------------------------

    def aufraeumen_faellig(self, jetzt: float) -> bool:
        return jetzt - self._letztes_aufraeumen >= self.intervall

    def aufraeumen(self) -> dict:
        self._letztes_aufraeumen = time.monotonic()
        bericht = self.archiv.aufraeumen()
        if bericht["verfallen"] or bericht["verdraengt"]:
            self.log.info("aufgeraeumt",
                          DAIMON_VERFALLEN=json.dumps(bericht["verfallen"]),
                          DAIMON_VERDRAENGT=bericht["verdraengt"],
                          DAIMON_BYTES=bericht["bytes"])
        return bericht

    # -- Automatische Pause (T-7.3) ----------------------------------------

    def pause_grund(self) -> str:
        """Warum jetzt pausiert werden muss -- leer heisst: kein Grund.

        Zwei Ausloeser, und JEDER muss allein reichen: eine
        Konferenzanwendung im Fokus, oder ein Mikrofonstrom, der nicht uns
        gehoert. Das zweite faengt den Fall, den das erste nicht sieht --
        ein Anruf im Browser hat keine eigene Fensterklasse.
        """
        klasse = self._fokus_klasse()
        if pause.ist_konferenz(klasse, self.konferenz):
            return f"konferenz:{klasse[:60]}"
        fremd = self._mikrofone()
        if fremd:
            return f"fremdes_mikrofon:{fremd}"
        return ""

    def automatik(self) -> str:
        """Ein Durchgang der Automatik. Gibt den Grund zurueck, wenn
        pausiert wurde."""
        grund = self.pause_grund()
        if not grund:
            return ""
        self.log.warn("automatisch pausiert", DAIMON_GRUND=grund)
        bericht = self._pausieren(runtime_dir=self.runtime_dir)
        if not bericht.get("ok"):
            # Eine Pause, die nicht greift, ist der gefaehrlichste Zustand
            # dieses Dienstes: er glaubt, er sei still.
            self.log.warn("Pause NICHT belegt",
                          DAIMON_MELDUNG=str(bericht.get("meldung"))[:200])
        self._halt = True
        return grund

    # -- Schleife ----------------------------------------------------------

    def start(self) -> None:
        self.archiv.migrieren()
        # ERST aufraeumen und die Automatik einmal fahren, DANN horchen.
        # Umgekehrt existiert der Socket bereits, waehrend der Dienst noch
        # `pw-dump` startet und den Fokus abfragt -- ein Klient verbindet
        # sich dann erfolgreich und wartet auf eine Antwort, die erst nach
        # dieser Arbeit kommt. Unter Last hat das am 14.08. drei Pruefungen
        # in Zeitueberschreitungen laufen lassen, waehrend dieselben
        # Pruefungen einzeln gruen waren. Wer horcht, bevor er bereit ist,
        # verteilt Zeitueberschreitungen.
        self.aufraeumen()
        if self.automatik():
            return
        self._srv = ipc.listen(self.runtime_dir, PRODUZENT)
        self._srv.settimeout(1.0)
        self.log.info("bereit", DAIMON_ARCHIV=str(self.archiv.pfad),
                      DAIMON_GRENZE_BYTES=self.archiv.grenze_bytes)

    def lauf(self) -> None:
        if self._srv is None:
            # `start()` hat schon pausiert -- es gibt nichts zu bedienen.
            self.archiv.schliessen()
            return
        try:
            self._schleife()
        finally:
            # Die SQLite-Verbindung gehoert dem Faden, der sie geoeffnet hat.
            # Deshalb schliesst SIE hier und nicht in `stop()`: `stop()` darf
            # von aussen kommen -- aus einem Signalgriff, aus einem Test --
            # und wuerde die Verbindung dann aus dem falschen Faden zumachen.
            self.archiv.schliessen()

    def _schleife(self) -> None:
        while not self._halt:
            # Der Herzschlag je Runde: das Sprite soll zeigen, dass gerade
            # mitgeschnitten wird, und zwar den echten Zustand. Ein Schreiben
            # ins tmpfs je Sekunde kostet nichts.
            pause.herzschlag(self.runtime_dir)
            jetzt = time.monotonic()
            if self.aufraeumen_faellig(jetzt):
                self.aufraeumen()
            if jetzt - self._letzte_automatik >= self.automatik_intervall:
                self._letzte_automatik = jetzt
                if self.automatik():
                    break
            # Den Server EINMAL greifen: `stop()` darf aus einem anderen
            # Faden kommen (Signalgriff, Test) und setzt `_srv` auf None --
            # zwischen der Pruefung oben und dem `accept()` hier. Ohne die
            # lokale Referenz stirbt die Schleife dann an einem
            # AttributeError statt sauber zu enden.
            srv = self._srv
            if srv is None:
                break
            try:
                conn, peer = ipc.accept(
                    srv, PRODUZENT,
                    erlaubte_units=self.erlaubte_units,
                    audit=lambda was, p: self.log.info(
                        "ipc", DAIMON_ACTION=was, DAIMON_PEER_PID=p.pid,
                        DAIMON_PEER_UNIT=p.unit))
            except socket.timeout:
                continue
            except (ipc.PeerError, OSError):
                continue
            with conn:
                # Die Unit haengt an der VERBINDUNG und nicht an der Zeile:
                # `SO_PEERPIDFD` beantwortet sie einmal beim Annehmen, und ein
                # Feld in der Nachricht waere wieder etwas, das der Absender
                # selbst setzt.
                #
                # Die beiden Schalter gehoeren ZUSAMMEN: `erlaubte_units=None`
                # heisst "ohne systemd" (siehe Konstruktor), und dann ist auch
                # `peer.unit` keine Aussage -- sie traegt dort die Unit des
                # Prueflaufs. Die Art-Tabelle daran zu haengen hiesse, jeden
                # Prueflauf gegen die Einheiten des Betriebs zu messen.
                unit = (getattr(peer, "unit", None)
                        if self.erlaubte_units is not None else None)
                self._bediene(conn, unit)

    def _bediene(self, conn: socket.socket, unit: str | None = None) -> None:
        conn.settimeout(5.0)
        puffer = b""
        try:
            while not self._halt:
                stueck = conn.recv(4096)
                if not stueck:
                    return
                puffer += stueck
                while b"\n" in puffer:
                    zeile, puffer = puffer.split(b"\n", 1)
                    conn.sendall(
                        (json.dumps(self._zeile(zeile, unit),
                                    ensure_ascii=False) + "\n").encode())
                if len(puffer) > MAX_ZEILE:
                    self.log.warn("Zeile zu lang, Verbindung ab",
                                  DAIMON_BYTES=len(puffer))
                    return
        except (OSError, socket.timeout):
            return

    def _zeile(self, roh: bytes, unit: str | None = None) -> dict:
        try:
            return self.melde(json.loads(roh.decode("utf-8")), unit=unit)
        except (ValueError, UnicodeDecodeError):
            return {"v": 1, "ok": False, "grund": "unlesbar"}

    def stop(self) -> None:
        """Haelt an. Die Datenbank schliesst `lauf()` -- siehe dort."""
        self._halt = True
        if self._srv is not None:
            self._srv.close()
            self._srv = None


def _wahrnehmung_an(runtime_dir: Path) -> bool:
    """Sehen die Augen gerade? Gemessen an IHREM Lebenszeichen.

    ZWEI FEHLER UEBEREINANDER, beide am 14.08. live gefunden, und deshalb
    steht hier jetzt etwas anderes als vorher:

    **Der erste war der Messweg.** Die erste Fassung rief
    `daimon.eyes.killswitch.lampe()`, und die ruft `systemctl --user
    is-active`. Im Sandkasten dieses Dienstes scheitert das:
    „Failed to connect to user scope bus via local transport" -- der
    Unit-Zustand ist ein Etikett, das dieser Prozess gar nicht lesen kann.

    **Der zweite war meiner.** `lampe()` kennt DREI Werte und begruendet das
    selbst: „Unbekannt ist nicht ‚aus'. Eine Lampe, die bei einem
    Werkzeugfehler Entwarnung gibt, ist schlimmer als gar keine."
    `lampe() == "an"` faltet drei auf zwei -- und zwar so, dass ein
    Werkzeugfehler wie „abgeschaltet" aussieht. Ergebnis im Betrieb: die
    Redaktion sperrte JEDE Meldung mit `wahrnehmung_aus`, bei laufenden
    Augen, und das Archiv blieb leer, waehrend alle Tests gruen waren.

    **Der dritte war wieder der Messweg, gefunden am 16.08.** Danach stand
    hier `bildschirmstroeme()`, und die zaehlt JEDEN `Stream/Output/Video`.
    Fuer den Kill-Switch ist das Mitzaehlen fremder Stroeme richtig; fuer
    diese Frage ist es falsch. Wer waehrend einer Konferenz seinen Bildschirm
    teilt und dann die Augen abschaltet, bekam hier weiter „an" -- der
    Fokusdienst lieferte Fenstertitel, und die Redaktion liess sie durch.
    Der Kill-Switch stoppt `daimon-eyes`, nicht `daimon-focus`.

    Gemessen wird jetzt das Lebenszeichen des Augendienstes: er schreibt es
    je Runde, hier wird sein ALTER geprueft. Kein Herzschlag heisst „aus",
    und das ist keine Wiederholung des zweiten Fehlers -- diese Messung
    gelingt immer, sie liest eine Datei im eigenen Laufzeitverzeichnis.
    Ihr Fehlen ist eine Aussage und kein Werkzeugfehler.
    """
    return pause.augen_sehen(runtime_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dAImon Archivdienst (T-7.1)")
    ap.add_argument("--runtime-dir", type=Path, default=None)
    ap.add_argument("--archiv", type=Path, default=None)
    args = ap.parse_args(argv)

    # `make_dirs=False`: `load()` legt sonst das Zustandsverzeichnis an, und
    # dieser Dienst hat dort nichts zu suchen -- die Unit verwehrt es ihm
    # auch (ProtectHome=tmpfs, nur das Archiv gebunden). Das Laufzeit- und
    # das Archivverzeichnis entstehen dort, wo sie hingehoeren:
    # `RuntimeDirectory=` in der Unit und `Archiv.oeffnen()` hier.
    cfg = load(make_dirs=False)
    rt = args.runtime_dir or cfg.runtime_dir
    grenze = float(cfg.get("archiv.grenze_gb", 5.0)) * 1024 ** 3

    log = get_logger("daimon-recorder")
    denylist, herkunft = denylist_laden(denylist_pfade())
    if herkunft is None:
        # Keine Liste ist KEIN Grund, alles mitzuschneiden -- aber auch kein
        # Grund, den Dienst zu verweigern: unbekannte Fenster sperrt die
        # Redaktion ohnehin. Laut im Journal, damit es auffaellt.
        log.warn("keine Denylist gefunden -- nur der Fail-closed-Pfad greift")
    # T-7.3 K5: die Konferenzliste ERGAENZT die Vorgabe aus `pause.py`. Bis
    # zum 19.08. stand der Parameter da und wurde hier nie gefuellt -- die
    # Zusage aus dem Kommentar in `pause.py` ("ergaenzt wird in
    # config/redaktion.yaml unter `konferenz`") galt nicht.
    zusatz, konf_herkunft = konferenz_laden(denylist_pfade())
    dienst = Recorder(
        runtime_dir=rt,
        archiv=Archiv(args.archiv, grenze_bytes=int(grenze)),
        redaktion=Redaktion(denylist=denylist, runtime_dir=rt,
                            wahrnehmung_an=lambda: _wahrnehmung_an(rt)),
        konferenz=tuple(pause.KONFERENZ_VORGABE) + tuple(zusatz),
        aufraeum_intervall_s=float(cfg.get("archiv.aufraeum_intervall_s",
                                           3600.0)),
        log=log)
    log.info("Denylist geladen", DAIMON_QUELLE=str(herkunft or ""),
             DAIMON_EINTRAEGE=len(denylist))
    log.info("Konferenzliste geladen", DAIMON_QUELLE=str(konf_herkunft or ""),
             DAIMON_VORGABE=len(pause.KONFERENZ_VORGABE),
             DAIMON_ZUSATZ=len(zusatz))

    def halt(*_: object) -> None:
        dienst.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, halt)

    dienst.start()
    try:
        dienst.lauf()
    finally:
        dienst.stop()
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
