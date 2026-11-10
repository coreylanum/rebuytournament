"""Turn parsed PokerNow logs into tournament results and stats.

Produces a plain-dict result (JSON-serializable) keyed by PokerNow player ID:
finishing order, who eliminated whom, per-player play stats, all-in
showdowns with equities, biggest pots, and chip-stack history.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .cards import Card, card_str, category_name, equity, evaluate
from .logparser import Hand, ParsedLogs

PREMIUM_EQUITY_SUCKOUT = 0.35  # winner had at most this equity when the chips went in


def _cards(cards: list[Card] | None) -> list[str]:
    return [card_str(c) for c in cards or []]


def _minutes(at: datetime, start: datetime) -> float:
    return round((at - start).total_seconds() / 60, 1)


def _best_score(hand: Hand, pid: str):
    cards = hand.shows.get(pid)
    if not cards or len(hand.board) < 3:
        return None
    return evaluate(cards + hand.board)


def _eliminators(hand: Hand, victim: str) -> list[str]:
    """Who took the victim's chips: the winner(s) of the pot the victim was in."""
    collectors: dict[str, float] = defaultdict(float)
    for c in hand.collected:
        if c.pid != victim:
            collectors[c.pid] += c.amount
    if len(collectors) <= 1:
        return list(collectors)
    # Several pots: the victim (all-in) was eligible for the main pot, which goes
    # to the best hand at showdown. Equal best hands split it.
    scored = {pid: _best_score(hand, pid) for pid in collectors}
    scored = {pid: s for pid, s in scored.items() if s is not None}
    if scored:
        best = max(scored.values())
        return sorted(pid for pid, s in scored.items() if s == best)
    return [max(collectors, key=collectors.get)]


def _allin_showdown(hand: Hand) -> dict | None:
    if hand.board_at_show is None or len(hand.board_at_show) >= 5 or len(hand.shows) < 2:
        return None
    pids = list(hand.shows)
    eq = equity([hand.shows[p] for p in pids], hand.board_at_show)
    winners = {c.pid for c in hand.collected}
    players = [
        {"pid": p, "cards": _cards(hand.shows[p]), "equity": round(e, 3), "won": p in winners}
        for p, e in zip(pids, eq)
    ]
    winner_eq = max((pl["equity"] for pl in players if pl["won"]), default=1.0)
    street = ["preflop", "", "", "flop", "turn"][len(hand.board_at_show)]
    return {
        "hand": hand.key,
        "hand_number": hand.number,
        "street": street,
        "board_at_allin": _cards(hand.board_at_show),
        "board": _cards(hand.board),
        "pot": hand.pot,
        "big_blind": hand.big_blind,
        "players": players,
        "suckout": winner_eq <= PREMIUM_EQUITY_SUCKOUT,
        "run_twice": hand.run_twice,
    }


def _player_stats(hands: list[Hand], eliminated_in: dict[str, str], ko_credit: dict[str, float]) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(lambda: {
        "hands": 0, "vpip": 0, "pfr": 0, "showdowns": 0, "showdown_wins": 0,
        "pots_won": 0, "chips_won": 0.0, "biggest_pot": 0.0, "all_ins": 0,
        "all_ins_survived": 0, "knockouts": 0.0, "best_hand": None,
    })
    best_scores: dict[str, tuple] = {}
    for h in hands:
        winners = defaultdict(float)
        for c in h.collected:
            winners[c.pid] += c.amount
        for pid in h.stacks:
            s = stats[pid]
            s["hands"] += 1
            pre = [a for a in h.actions if a.pid == pid and a.street == 0 and a.kind != "post"]
            if any(a.kind in ("call", "bet", "raise") for a in pre):
                s["vpip"] += 1
            if any(a.kind in ("bet", "raise") for a in pre):
                s["pfr"] += 1
            if pid in h.shows:
                s["showdowns"] += 1
                if pid in winners:
                    s["showdown_wins"] += 1
            if pid in winners:
                s["pots_won"] += 1
                s["chips_won"] = round(s["chips_won"] + winners[pid], 2)
                s["biggest_pot"] = max(s["biggest_pot"], winners[pid])
            if any(a.pid == pid and a.all_in for a in h.actions):
                s["all_ins"] += 1
                if eliminated_in.get(pid) != h.key:
                    s["all_ins_survived"] += 1
            score = _best_score(h, pid) if len(h.board) == 5 else None
            if score and (pid not in best_scores or score > best_scores[pid]):
                best_scores[pid] = score
                s["best_hand"] = {
                    "name": category_name(score),
                    "cards": _cards(h.shows[pid]),
                    "board": _cards(h.board),
                }
    for pid, credit in ko_credit.items():
        stats[pid]["knockouts"] = round(credit, 2)
    return dict(stats)


def _chip_series(hands: list[Hand], start: datetime, eliminations: dict[str, datetime], max_points: int = 80) -> dict[str, list]:
    series: dict[str, list] = defaultdict(list)
    for h in sorted(hands, key=lambda h: h.start_order):
        for pid, stack in h.stacks.items():
            series[pid].append([_minutes(h.start_at, start), stack])
    for pid, at in eliminations.items():
        series[pid].append([_minutes(at, start), 0])
    out = {}
    for pid, pts in series.items():
        if len(pts) > max_points:
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)] + [pts[-1]]
        out[pid] = pts
    return out


def analyze(parsed: ParsedLogs) -> dict:
    hands = parsed.hands
    hands_by_key = {h.key: h for h in hands}
    events = sorted(parsed.events, key=lambda e: e.order)
    warnings: list[str] = []
    if not hands:
        raise ValueError("no hands found in the logs")
    start = min(h.start_at for h in hands)
    end = max((h.end_at or h.start_at) for h in hands)

    entries: dict[str, int] = defaultdict(int)
    alive: set[str] = set()
    eliminated: dict[str, dict] = {}  # pid -> latest elimination
    seen_entry: set[str] = set()

    for ev in events:
        if ev.kind in ("entered", "joined") and ev.pid:
            if ev.pid in eliminated and ev.pid not in alive:
                # Re-entry after busting.
                entries[ev.pid] += 1
                del eliminated[ev.pid]
            elif ev.pid not in seen_entry:
                entries[ev.pid] += 1
            seen_entry.add(ev.pid)
            alive.add(ev.pid)
        elif ev.kind == "quit" and ev.pid:
            if ev.amount and ev.amount > 0:
                warnings.append(f"{parsed.names.get(ev.pid, ev.pid)} left with {ev.amount} chips (not counted as a bust)")
                continue
            hand = hands_by_key.get(ev.detail.get("hand_key", ""))
            alive.discard(ev.pid)
            eliminated[ev.pid] = {
                "pid": ev.pid,
                "order": ev.order,
                "at": ev.at,
                "hand": hand.key if hand else None,
                "start_stack": hand.stacks.get(ev.pid, 0) if hand else 0,
                "by": _eliminators(hand, ev.pid) if hand else [],
            }

    # Anyone seen in a hand but missing an entry event still counts as an entrant.
    for h in hands:
        for pid in h.stacks:
            if pid not in entries:
                entries[pid] = 1
                warnings.append(f"{parsed.names.get(pid, pid)} played without a registration line in the log")
                if pid not in eliminated:
                    alive.add(pid)

    survivors = [pid for pid in entries if pid not in eliminated]
    if len(survivors) != 1:
        warnings.append(f"expected 1 player left at the end, found {len(survivors)}: tournament may be unfinished or logs missing")
    last_stack: dict[str, float] = {}
    for h in sorted(hands, key=lambda h: h.start_order):
        last_stack.update(h.stacks)
    survivors.sort(key=lambda pid: -last_stack.get(pid, 0))

    # Later bust = better finish. Same hand: bigger starting stack finishes higher.
    busts = sorted(eliminated.values(), key=lambda e: (e["order"], e["start_stack"]), reverse=True)
    ordered = survivors + [b["pid"] for b in busts]
    finish = []
    for place, pid in enumerate(ordered, start=1):
        b = eliminated.get(pid)
        finish.append({
            "place": place,
            "pid": pid,
            "name": parsed.names.get(pid, pid),
            "entries": entries.get(pid, 1),
            "busted_at": b["at"].isoformat() if b else None,
            "busted_minute": _minutes(b["at"], start) if b else None,
            "busted_by": b["by"] if b else [],
            "hand": b["hand"] if b else None,
        })

    knockouts = []
    ko_credit: dict[str, float] = defaultdict(float)
    for b in sorted(eliminated.values(), key=lambda e: e["order"]):
        for pid in b["by"]:
            ko_credit[pid] += 1 / len(b["by"])
        h = hands_by_key.get(b["hand"]) if b["hand"] else None
        knockouts.append({
            "victim": b["pid"],
            "by": b["by"],
            "minute": _minutes(b["at"], start),
            "hand": b["hand"],
            "victim_cards": _cards(h.shows.get(b["pid"])) if h else [],
            "by_cards": {pid: _cards(h.shows.get(pid)) for pid in b["by"]} if h else {},
            "board": _cards(h.board) if h else [],
            "winning_hand": next((c.description for c in h.collected if c.pid in b["by"] and c.description), None) if h else None,
        })

    showdowns = [s for h in hands if (s := _allin_showdown(h))]
    biggest = sorted(hands, key=lambda h: h.pot, reverse=True)[:5]
    biggest_pots = [{
        "hand": h.key,
        "hand_number": h.number,
        "pot": h.pot,
        "big_blind": h.big_blind,
        "winners": sorted({c.pid for c in h.collected}),
        "description": next((c.description for c in h.collected if c.description), None),
        "shown": {pid: _cards(cs) for pid, cs in h.shows.items()},
        "board": _cards(h.board),
    } for h in biggest]

    blinds = []
    for h in sorted(hands, key=lambda h: h.start_order):
        bb = h.big_blind
        if bb and (not blinds or blinds[-1][1] != bb):
            blinds.append([_minutes(h.start_at, start), bb])

    eliminated_in = {pid: b["hand"] for pid, b in eliminated.items()}
    stats = _player_stats(hands, eliminated_in, ko_credit)
    unique_hands = {(h.table_id, h.hand_id) for h in hands}
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_minutes": _minutes(end, start),
        "tables": parsed.tables,
        "hands_played": len(unique_hands),
        "names": dict(parsed.names),
        "players": len(entries),
        "buy_ins": sum(entries.values()),
        "finish": finish,
        "knockouts": knockouts,
        "allin_showdowns": showdowns,
        "biggest_pots": biggest_pots,
        "blind_levels": blinds,
        "player_stats": stats,
        "chip_series": _chip_series(hands, start, {pid: b["at"] for pid, b in eliminated.items()}),
        "warnings": warnings,
    }
