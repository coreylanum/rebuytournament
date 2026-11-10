"""Parse PokerNow table logs (the CSV from "Log / Ledger -> Download full log").

A multi-table tournament produces one log per table. `parse_logs` merges them
in global order (PokerNow's `order` column) and returns hands plus the
tournament-level events (entries, eliminations, table moves, blind changes).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .cards import Card, parse_cards

_P = r'"(?P<name>.+?) @ (?P<pid>[\w-]+)"'

RE_START = re.compile(r"^-- starting hand #(?P<num>\d+) \(id: (?P<hid>\w+)\)\s*(?P<game>.*?)(?: \(dealer: " r'"(?P<dname>.+?) @ (?P<did>[\w-]+)"\))? --$')
RE_END = re.compile(r"^-- ending hand #(?P<num>\d+) --$")
RE_STACKS = re.compile(r"^Player stacks: (?P<rest>.*)$")
RE_STACK_ITEM = re.compile(r'#(?P<seat>\d+) "(?P<name>.+?) @ (?P<pid>[\w-]+)" \((?P<stack>[\d.]+)\)')
RE_POST = re.compile(rf"^{_P} posts a (?P<what>.+?) of (?P<amt>[\d.]+)(?P<allin> and go all in)?$")
RE_SIMPLE = re.compile(rf"^{_P} (?P<kind>folds|checks)$")
RE_BET = re.compile(rf"^{_P} (?P<kind>calls|bets|raises to) (?P<amt>[\d.]+)(?P<allin> and go all in)?$")
RE_UNCALLED = re.compile(rf"^Uncalled bet of (?P<amt>[\d.]+) returned to {_P}$")
RE_COLLECT = re.compile(rf"^{_P} collected (?P<amt>[\d.]+) from pot(?: with (?P<desc>.+?)(?: \(combination: (?P<combo>.+)\))?)?$")
RE_SHOWS = re.compile(rf"^{_P} shows a (?P<cards>.+?)\.?$")
RE_STREET = re.compile(r"^(?P<street>Flop|Turn|River)(?P<second> \(second run\))?:\s*(?P<cards>.*)$")
RE_IN_TOURNEY = re.compile(rf"^The player {_P} is in the tournament with a stack of (?P<amt>[\d.]+)\.$")
RE_JOINED = re.compile(rf"^The player {_P} joined the game with a stack of (?P<amt>[\d.]+)\.$")
RE_QUITS = re.compile(rf"^The player {_P} quits the game with a stack of (?P<amt>[\d.]+)\.$")
RE_MOVED = re.compile(rf"^The player {_P} was moved to table #(?P<table>\d+)\.$")
RE_WILL_PLAY = re.compile(rf"^{_P} will be playing on table #(?P<table>\d+) \(ID: (?P<tid>[\w-]+)\)\.$")
RE_START_PLAY = re.compile(rf"^The player {_P} will start to play in this table with a stack of (?P<amt>\d+(?:\.\d+)?)\.?$")
RE_BLIND_CHANGE = re.compile(r"^The game's (?P<what>small blind|big blind|ante) was changed from (?P<old>[\d.]+) to (?P<new>[\d.]+)\.$")
RE_YOUR_HAND = re.compile(r"^Your hand is ")

STREETS = ["preflop", "flop", "turn", "river"]


@dataclass
class LogEntry:
    order: int
    at: datetime
    text: str
    table_id: str


@dataclass
class Action:
    street: int
    pid: str
    kind: str  # post, fold, check, call, bet, raise
    amount: float = 0.0
    all_in: bool = False
    what: str = ""  # for posts: "small blind", "big blind", ...


@dataclass
class Collect:
    pid: str
    amount: float
    description: str | None = None
    combination: list[Card] | None = None


@dataclass
class Hand:
    table_id: str
    number: int
    hand_id: str
    dealer_id: str | None
    start_order: int
    start_at: datetime
    end_order: int | None = None
    end_at: datetime | None = None
    stacks: dict[str, float] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    board: list[Card] = field(default_factory=list)
    board_at_show: list[Card] | None = None
    shows: dict[str, list[Card]] = field(default_factory=dict)
    post_shows: dict[str, list[Card]] = field(default_factory=dict)
    collected: list[Collect] = field(default_factory=list)
    uncalled: list[tuple[str, float]] = field(default_factory=list)
    run_twice: bool = False
    street: int = 0

    @property
    def key(self) -> str:
        return f"{self.table_id}#{self.number}"

    @property
    def big_blind(self) -> float:
        for a in self.actions:
            if a.kind == "post" and a.what == "big blind":
                return a.amount
        return 0.0

    @property
    def pot(self) -> float:
        return round(sum(c.amount for c in self.collected), 2)

    @property
    def players(self) -> list[str]:
        return list(self.stacks)


@dataclass
class Event:
    kind: str  # entered, joined, quit, moved, will_play, start_play, blinds
    order: int
    at: datetime
    table_id: str
    pid: str | None = None
    amount: float | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class ParsedLogs:
    hands: list[Hand]
    events: list[Event]
    names: dict[str, str]  # PokerNow ID -> latest screen name
    tables: list[str]
    unparsed: list[str]


def table_id_from_filename(name: str) -> str | None:
    m = re.search(r"poker_now_log_([\w-]+)\.csv$", name)
    return m.group(1) if m else None


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def read_log(source: str | Path, text: str | None = None, table_id: str | None = None) -> list[LogEntry]:
    """Read one table's CSV. Pass `text` to parse content already in memory."""
    name = str(source)
    if text is None:
        text = Path(source).read_text(encoding="utf-8-sig")
    table_id = table_id or table_id_from_filename(name) or Path(name).stem
    rows = csv.DictReader(io.StringIO(text))
    entries = []
    for row in rows:
        if not row.get("entry"):
            continue
        entries.append(LogEntry(int(row["order"]), _parse_time(row["at"]), row["entry"], table_id))
    return entries


def parse_logs(entries: list[LogEntry]) -> ParsedLogs:
    entries = sorted(entries, key=lambda e: e.order)
    hands: list[Hand] = []
    events: list[Event] = []
    names: dict[str, str] = {}
    current: dict[str, Hand | None] = {}
    last_hand: dict[str, Hand] = {}
    unparsed: list[str] = []
    tables: list[str] = []

    def note(m: re.Match) -> str:
        names[m["pid"]] = m["name"]
        return m["pid"]

    for e in entries:
        if e.table_id not in tables:
            tables.append(e.table_id)
        t = e.text
        hand = current.get(e.table_id)

        if m := RE_START.match(t):
            hand = Hand(e.table_id, int(m["num"]), m["hid"], m["did"], e.order, e.at)
            if m["did"]:
                names[m["did"]] = m["dname"]
            current[e.table_id] = hand
            hands.append(hand)
            continue
        if m := RE_END.match(t):
            if hand:
                hand.end_order, hand.end_at = e.order, e.at
                last_hand[e.table_id] = hand
            current[e.table_id] = None
            continue
        if RE_YOUR_HAND.match(t):
            continue  # private to whoever downloaded the log
        if m := RE_STACKS.match(t):
            if hand:
                for item in RE_STACK_ITEM.finditer(m["rest"]):
                    names[item["pid"]] = item["name"]
                    hand.stacks[item["pid"]] = float(item["stack"])
                    hand.seats[item["pid"]] = int(item["seat"])
            continue
        if m := RE_POST.match(t):
            if hand:
                hand.actions.append(Action(hand.street, note(m), "post", float(m["amt"]), bool(m["allin"]), m["what"]))
            continue
        if m := RE_SIMPLE.match(t):
            if hand:
                hand.actions.append(Action(hand.street, note(m), "fold" if m["kind"] == "folds" else "check"))
            continue
        if m := RE_BET.match(t):
            if hand:
                kind = {"calls": "call", "bets": "bet", "raises to": "raise"}[m["kind"]]
                hand.actions.append(Action(hand.street, note(m), kind, float(m["amt"]), bool(m["allin"])))
            continue
        if m := RE_UNCALLED.match(t):
            if hand:
                hand.uncalled.append((note(m), float(m["amt"])))
            continue
        if m := RE_COLLECT.match(t):
            target = hand or last_hand.get(e.table_id)
            if target:
                combo = parse_cards(m["combo"]) if m["combo"] else None
                target.collected.append(Collect(note(m), float(m["amt"]), m["desc"], combo))
            continue
        if m := RE_SHOWS.match(t):
            pid = note(m)
            try:
                cards = parse_cards(m["cards"])
            except ValueError:
                unparsed.append(t)
                continue
            if hand:
                if hand.board_at_show is None:
                    hand.board_at_show = list(hand.board)
                hand.shows[pid] = cards
            elif e.table_id in last_hand:
                last_hand[e.table_id].post_shows[pid] = cards
            continue
        if m := RE_STREET.match(t):
            if hand:
                if m["second"]:
                    hand.run_twice = True
                else:
                    hand.street = STREETS.index(m["street"].lower())
                    hand.board = parse_cards(m["cards"])
            continue

        ev = None
        if m := RE_IN_TOURNEY.match(t):
            ev = Event("entered", e.order, e.at, e.table_id, note(m), float(m["amt"]))
        elif m := RE_JOINED.match(t):
            ev = Event("joined", e.order, e.at, e.table_id, note(m), float(m["amt"]))
        elif m := RE_QUITS.match(t):
            ev = Event("quit", e.order, e.at, e.table_id, note(m), float(m["amt"]))
            ref = hand or last_hand.get(e.table_id)
            if ref:
                ev.detail["hand_key"] = ref.key
        elif m := RE_MOVED.match(t):
            ev = Event("moved", e.order, e.at, e.table_id, note(m), detail={"table_number": int(m["table"])})
        elif m := RE_WILL_PLAY.match(t):
            ev = Event("will_play", e.order, e.at, e.table_id, note(m), detail={"table_number": int(m["table"]), "to_table_id": m["tid"]})
        elif m := RE_START_PLAY.match(t):
            ev = Event("start_play", e.order, e.at, e.table_id, note(m), float(m["amt"]))
        elif m := RE_BLIND_CHANGE.match(t):
            ev = Event("blinds", e.order, e.at, e.table_id, detail={"what": m["what"], "old": float(m["old"]), "new": float(m["new"])})
        if ev:
            events.append(ev)
        else:
            unparsed.append(t)

    return ParsedLogs(hands, events, names, tables, unparsed)


def load_files(paths: list[str | Path]) -> ParsedLogs:
    entries: list[LogEntry] = []
    for p in paths:
        entries.extend(read_log(p))
    return parse_logs(entries)
