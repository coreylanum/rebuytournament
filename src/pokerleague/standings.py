"""Season standings and aggregate stats, computed from approved tournaments.

Points follow the 2026 sheet: 4 for 1st, 3 for 2nd, 2 for 3rd, 1 for 4th
(4th only counts when it is a paid place, i.e. 11+ players). Bubbles, first
outs and bounties are tracked but score nothing.
"""

from __future__ import annotations

from collections import defaultdict

from .rules import DEFAULT_RULES, Rules

PLACING_KEYS = ["first", "second", "third", "fourth"]
STAT_KEYS = ["hands", "vpip", "pfr", "showdowns", "showdown_wins", "pots_won", "all_ins", "all_ins_survived", "knockouts"]


def _rank(sorted_rows: list[dict], key: str) -> None:
    """Competition ranking (1, 2, 2, 4) on `key`, rows already sorted."""
    last_value, last_rank = None, 0
    for i, row in enumerate(sorted_rows, start=1):
        if row[key] != last_value:
            last_value, last_rank = row[key], i
        row["rank"] = last_rank


def season_standings(tournaments: list[dict], rules: Rules = DEFAULT_RULES) -> list[dict]:
    rows: dict[str, dict] = defaultdict(lambda: {
        "tourneys": 0, "points": 0, "in_money": 0, "wins": 0, "seconds": 0, "thirds": 0,
        "fourths": 0, "bubbles": 0, "first_outs": 0, "bounties": 0.0, "winnings": 0.0,
        "finishes": [], "stats": {k: 0 for k in STAT_KEYS},
    })
    for t in sorted(tournaments, key=lambda t: t["date"]):
        placings = t.get("placings", {})
        attendees = {r["player"] for r in t.get("results", [])}
        attendees |= {v for k, v in placings.items() if isinstance(v, str)}
        for player in attendees:
            rows[player]["tourneys"] += 1
        for i, key in enumerate(PLACING_KEYS, start=1):
            player = placings.get(key)
            if player:
                row = rows[player]
                row["points"] += rules.points[i]
                row["in_money"] += 1
                row[["wins", "seconds", "thirds", "fourths"][i - 1]] += 1
        if placings.get("bubble"):
            rows[placings["bubble"]]["bubbles"] += 1
        if placings.get("first_out"):
            rows[placings["first_out"]]["first_outs"] += 1
        winners = t.get("bounty", {}).get("winners", [])
        for w in winners:
            rows[w]["bounties"] += 1 / len(winners)
        for r in t.get("results", []):
            if r.get("place"):
                rows[r["player"]]["finishes"].append({"date": t["date"], "place": r["place"], "of": len(t["results"])})
        for player, amount in t.get("payouts", {}).get("gross", {}).items():
            rows[player]["winnings"] = round(rows[player]["winnings"] + amount, 2)
        for player, stats in t.get("player_stats", {}).items():
            for k in STAT_KEYS:
                rows[player]["stats"][k] = round(rows[player]["stats"][k] + stats.get(k, 0), 2)

    table = []
    for player, row in rows.items():
        row["player"] = player
        row["points_per_tourney"] = round(row["points"] / row["tourneys"], 3) if row["tourneys"] else 0
        row["bounties"] = round(row["bounties"], 2)
        table.append(row)
    table.sort(key=lambda r: (-r["points"], -r["wins"], -r["points_per_tourney"], r["player"]))
    _rank(table, "points")
    return table


def points_race(tournaments: list[dict], rules: Rules = DEFAULT_RULES) -> dict:
    """Cumulative points per player after each tournament (for the race chart)."""
    dates, totals, series = [], defaultdict(int), defaultdict(list)
    for t in sorted(tournaments, key=lambda t: t["date"]):
        dates.append(t["date"])
        for i, key in enumerate(PLACING_KEYS, start=1):
            player = t.get("placings", {}).get(key)
            if player:
                totals[player] += rules.points[i]
        for player in set(totals) | set(series):
            series[player].append(totals[player])
    width = len(dates)
    padded = {p: [0] * (width - len(s)) + s for p, s in series.items()}
    return {"dates": dates, "series": padded}


def knockout_matrix(tournaments: list[dict]) -> dict:
    """counts[hunter][victim] = knockouts across the season (log-based weeks only)."""
    counts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in tournaments:
        for ko in t.get("knockouts", []):
            by = ko.get("by", [])
            for hunter in by:
                counts[hunter][ko["victim"]] += 1 / len(by)
    return {h: {v: round(n, 2) for v, n in vs.items()} for h, vs in counts.items()}
