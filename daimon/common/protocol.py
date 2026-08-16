"""T-0.4 — die JSON-Vertraege aus Design 9 als Dataclasses mit Validierung.

Drei Entwurfsentscheidungen tragen hier die Sicherheit, und alle drei sind
Weglassungen. Sie stehen zuerst, weil man sie im Code sonst nicht sieht:

  * `ActionRequest` hat **kein** `initiator` und **kein** `params_hash`. Beides
    bestimmt der Hub. Ein Feld, das der Absender setzt, sagt nichts darueber,
    wer etwas will -- und ein vom Absender mitgelieferter Hash bestaetigt nur
    sich selbst. Beim Deserialisieren werden sie deshalb nicht etwa abgelehnt,
    sondern **stillschweigend verworfen**: ein Angreifer soll aus der
    Fehlermeldung nicht lernen, dass das Feld existiert.
  * `Event` hat **kein** `source`. Die Quelle ergibt sich aus dem Socket, ueber
    den das Ereignis kam. Auch hier: mitgeschickte Angaben fliegen raus.
  * Die Markierung ist ein **Typ**, keine Zeichenkette. `Marked` ueberlebt den
    Weg durch JSON, weil sie im Draht als Objekt steht und nicht als Praefix im
    Text. Ein Praefix waere von einem Angreifer schreibbar -- er koennte
    "trusted:" in einen Fenstertitel tippen.

Und die Vorgabe ist `tainted`, nicht `trusted` (Design 5.2). Ein neu
hinzugefuegtes Textfeld ist damit automatisch markiert, bis jemand begruendet,
warum nicht. Die Ausnahmeliste steht im Design, nicht hier.

stdlib-only. Der Hub spricht ueber Unix-Sockets; alles Schwere haengt an den
GPU-Arbeitsprozessen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, ClassVar, TypeVar

__all__ = [
    "Mark", "Marked", "tainted", "trusted", "ist_freigabeschein",
    "ProtocolError", "UnsupportedVersion",
    "Event", "State", "ActionRequest", "ExecutionOrder", "RoundMark",
    "ActionApproval", "ApiQuota", "Ticket", "AuditRecord",
    "loads", "dumps",
]


# --------------------------------------------------------------------------
# Fehler
# --------------------------------------------------------------------------

class ProtocolError(ValueError):
    """Nutzlast passt nicht zum Vertrag."""


class UnsupportedVersion(ProtocolError):
    """Unbekanntes `v`. Face faellt darauf auf `sleeping` zurueck, statt zu raten."""

    def __init__(self, got: Any, supported: tuple[int, ...]) -> None:
        super().__init__(f"v={got!r} nicht unterstuetzt, erwartet {supported}")
        self.got = got
        self.supported = supported


# --------------------------------------------------------------------------
# Markierung (Design 5.2)
# --------------------------------------------------------------------------

class Mark(str, Enum):
    USER_PTT = "user_ptt"
    USER_AUDIO = "user_audio"
    TRUSTED = "trusted"
    TAINTED = "tainted"


@dataclass(frozen=True)
class Marked:
    """Ein Wert mit Herkunftsmarkierung.

    Im Draht steht `{"__mark__": "tainted", "value": ...}`. Das ist bewusst
    umstaendlicher als ein Praefix im Text: ein Praefix koennte ein Angreifer
    selbst tippen, dieses Objekt nicht -- es entsteht nur beim Serialisieren
    durch uns.

    `bool(Marked(""))` ist False, damit sich markierte Texte in Bedingungen so
    verhalten wie nackte. Wer den Rohwert will, muss `.value` schreiben -- das
    ist die Stelle, an der man beim Lesen stolpert und sich fragt, ob der Wert
    hier hin darf.
    """

    value: Any
    mark: Mark = Mark.TAINTED

    WIRE_KEY: ClassVar[str] = "__mark__"

    def __post_init__(self) -> None:
        if not isinstance(self.mark, Mark):
            object.__setattr__(self, "mark", Mark(self.mark))

    def __bool__(self) -> bool:
        return bool(self.value)

    def __len__(self) -> int:
        return len(self.value)

    def is_trusted(self) -> bool:
        return self.mark is Mark.TRUSTED

    def to_wire(self) -> dict:
        return {self.WIRE_KEY: self.mark.value, "value": self.value}

    @classmethod
    def from_wire(cls, raw: Any) -> "Marked":
        """Nackte Werte werden `tainted` -- Vorgabe ist Misstrauen."""
        if isinstance(raw, Marked):
            return raw
        if isinstance(raw, dict) and cls.WIRE_KEY in raw:
            return cls(raw.get("value"), Mark(raw[cls.WIRE_KEY]))
        return cls(raw, Mark.TAINTED)


def tainted(value: Any) -> Marked:
    return Marked(value, Mark.TAINTED)


def ist_freigabeschein(schein: Any) -> bool:
    """Traegt dieses Objekt einen Freigabeschein des Deklassifizierungs-Gates?

    **Warum hier und nicht `isinstance`.** Die beiden Speicher hinter der
    Quarantaene -- der Kontextspeicher (T-5.7) und die Archivsuche (T-7.5) --
    duerfen das Gate NICHT kennen. Sie verlangen den Schein und pruefen ihn
    nicht nach; ausgestellt wird er allein in `daimon.hub.declassify`. Ein
    `isinstance` waere ein Import von `eyes`/`recorder` nach `hub` und damit
    genau die Schichtung, die T-5.7 aufmacht.

    **Warum ueberhaupt eine gemeinsame Funktion.** Bis zum 16.08. prueften die
    beiden verschieden: die Archivsuche den Typnamen, der Kontextspeicher nur
    `getattr(schein, "turn_id", "") != ""` -- also jedes beliebige Objekt mit
    diesem Attribut, ein `Namespace(turn_id="x")` genuegte. Zwei Fassungen
    einer Regel sind eine Regel und eine Attrappe; welche gilt, entscheidet
    dann der Aufrufer.

    **Was diese Pruefung NICHT leistet.** Sie haelt ein Versehen auf, keinen
    Angreifer: eine hier definierte Klasse namens `Freigabeschein` kommt
    durch. Wer im selben Prozess Code ausfuehrt, hat den Speicher ohnehin --
    er braucht dafuer keinen Schein. Der Punkt ist, dass ein `True`, eine `1`
    oder ein durchgereichtes Konfigurationsobjekt aus jedem Versehen
    entstehen, ein Schein aber nur dort, wo das Gate ihn herstellt.
    """
    return (type(schein).__name__ == "Freigabeschein"
            and bool(str(getattr(schein, "turn_id", "")).strip()))


def trusted(value: Any) -> Marked:
    """Muss ausdruecklich behauptet werden. Gilt nur fuer geschlossene
    Aufzaehlungen und gepruefte Zahlen -- siehe Design 5.2."""
    return Marked(value, Mark.TRUSTED)


# --------------------------------------------------------------------------
# Grundgeruest
# --------------------------------------------------------------------------

T = TypeVar("T", bound="Message")

# Felder, die ein Absender nicht setzen darf. Sie werden beim Deserialisieren
# entfernt, nicht abgelehnt: eine Fehlermeldung waere ein Hinweis darauf, dass
# das Feld ueberhaupt existiert.
VERBOTEN: dict[str, frozenset[str]] = {
    "ActionRequest": frozenset({"initiator", "params_hash"}),
    "Event": frozenset({"source"}),
}


@dataclass
class Message:
    V: ClassVar[tuple[int, ...]] = (1,)
    MARKED_FIELDS: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def from_dict(cls: type[T], raw: dict) -> T:
        if not isinstance(raw, dict):
            raise ProtocolError(f"{cls.__name__}: erwartet ein Objekt, war {type(raw).__name__}")

        v = raw.get("v", cls.V[0])
        if v not in cls.V:
            raise UnsupportedVersion(v, cls.V)

        verboten = VERBOTEN.get(cls.__name__, frozenset())
        bekannt = {f.name for f in fields(cls)}
        werte: dict[str, Any] = {}
        for name, value in raw.items():
            if name in ("v",) or name in verboten:
                continue
            # Unbekannte optionale Felder werden ignoriert, nicht abgelehnt --
            # sonst bricht jeder aeltere Leser an einem neuen Feld.
            if name not in bekannt:
                continue
            werte[name] = Marked.from_wire(value) if name in cls.MARKED_FIELDS else value

        fehlend = [
            f.name for f in fields(cls)
            if f.name not in werte and f.default is _MISSING and f.default_factory is _MISSING  # type: ignore[misc]
        ]
        if fehlend:
            raise ProtocolError(f"{cls.__name__}: Pflichtfelder fehlen: {', '.join(sorted(fehlend))}")

        return cls(**werte)  # type: ignore[arg-type]

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"v": self.V[-1]}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Marked):
                value = value.to_wire()
            elif isinstance(value, Enum):
                value = value.value
            elif is_dataclass(value) and isinstance(value, Message):
                value = value.to_dict()
            out[f.name] = value
        return out


from dataclasses import MISSING as _MISSING  # noqa: E402  (nach Message, wegen Lesbarkeit)


# --------------------------------------------------------------------------
# Die neun Vertraege aus Design 9
# --------------------------------------------------------------------------

@dataclass
class Event(Message):
    """Produzent -> Hub. KEIN `source` -- die Quelle ergibt sich aus dem Socket."""

    type: str
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    V: ClassVar[tuple[int, ...]] = (1,)


@dataclass
class State(Message):
    """Hub -> Face, ueber den Unix-Socket gepollt."""

    rev: int = 0
    mood: str = "sleeping"
    sessions: int = 0
    focus: dict | None = None
    bubble: dict | None = None
    voice: dict = field(default_factory=lambda: {
        "state": "idle", "listening": False, "tts_active": False})
    perception: dict = field(default_factory=lambda: {
        "ears": False, "eyes": False, "gpu_loaded": []})

    V: ClassVar[tuple[int, ...]] = (2,)


@dataclass
class ActionRequest(Message):
    """Mind -> Hub. OHNE `initiator` und OHNE `params_hash` -- beides bestimmt
    der Hub. `request_id` autorisiert nichts, es ordnet nur zu."""

    action_id: str
    params: dict = field(default_factory=dict)
    request_id: str = ""
    turn_id: str = ""

    V: ClassVar[tuple[int, ...]] = (1,)


@dataclass
class ExecutionOrder(Message):
    """Hub -> Broker (Design 6.2). Der Hub setzt `params_hash` und `initiator`
    selbst -- hier duerfen sie stehen, weil sie hier entstehen."""

    order_id: str
    action_id: str
    params: dict = field(default_factory=dict)
    params_hash: str = ""
    initiator: str = "hub"
    expires_at: float = 0.0

    V: ClassVar[tuple[int, ...]] = (1,)


@dataclass
class RoundMark(Message):
    """Absichtsmarke aus dem Auth-Agenten. Bindet eine Vollmacht an eine
    kanonisierte Aktion, nicht an einen Tastendruck -- ein Tastendruck sagt
    *dass*, nicht *was* (Design 11)."""

    mark_id: str
    action_id: str
    params_hash: str
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    origin: Mark = Mark.USER_PTT

    V: ClassVar[tuple[int, ...]] = (1,)


@dataclass
class ActionApproval(Message):
    """Antwort des Nutzers auf die Vorschau."""

    request_id: str
    approved: bool
    mark_id: str = ""
    reason: Marked = field(default_factory=lambda: Marked(""))

    V: ClassVar[tuple[int, ...]] = (1,)
    MARKED_FIELDS: ClassVar[frozenset[str]] = frozenset({"reason"})


@dataclass
class ApiQuota(Message):
    """Kontingent fuer den bezahlten Weg."""

    window_s: int = 3600
    spent: int = 0
    limit: int = 0
    resets_at: float = 0.0

    V: ClassVar[tuple[int, ...]] = (1,)


@dataclass
class Ticket(Message):
    """Opake Referenz auf ein Ergebnis. Der Inhalt bleibt beim Hub -- ein
    Ticket kann man weiterreichen, ohne markiertes Material mitzureichen."""

    ticket_id: str
    kind: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    V: ClassVar[tuple[int, ...]] = (1,)


@dataclass
class AuditRecord(Message):
    """Design 7: nie im Klartext -- markiertes Material nur als Hash und Laenge."""

    at: float
    what: str
    outcome: str
    action_id: str = ""
    params_hash: str = ""
    detail_hash: str = ""
    detail_len: int = 0

    V: ClassVar[tuple[int, ...]] = (1,)


# --------------------------------------------------------------------------
# Bequemlichkeit
# --------------------------------------------------------------------------

def dumps(msg: Message) -> dict:
    return msg.to_dict()


def loads(cls: type[T], raw: dict) -> T:
    return cls.from_dict(raw)
