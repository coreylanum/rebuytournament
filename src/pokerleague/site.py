"""Build the public website's data files from approved tournaments.

Only approved tournaments are published, and only public fields: aliases,
results, winnings, hand highlights and recaps. Emails, payment accounts and
who has or hasn't paid never leave the private store.

`build_site_files` returns {path: bytes}, so the same build can be written to
a local folder (preview) or uploaded to the website bucket.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from .league import all_tournaments, load_registry
from .rules import DEFAULT_RULES
from .standings import knockout_matrix, points_race, season_standings
from .store import Store

STATIC_DIR = Path(__file__).resolve().parent / "web"

PUBLIC_TOURNAMENT_FIELDS = [
    "date", "season", "source", "host", "players", "results", "placings", "bounty", "knockouts",
    "allin_showdowns", "biggest_pots", "chip_series", "blind_levels", "hands_played", "duration_minutes",
    "player_stats",
]


def _public_tournament(t: dict) -> dict:
    out = {k: t[k] for k in PUBLIC_TOURNAMENT_FIELDS if k in t}
    payouts = t.get("payouts", {})
    if payouts:
        out["winnings"] = {p: amt for p, amt in payouts.get("gross", {}).items() if amt > 0}
        out["pool"] = payouts.get("pool")
    for r in out.get("results", []):
        r.pop("pokernow_id", None)  # internal identifier, not useful publicly
    if t.get("recap", {}).get("text") and t.get("recap", {}).get("published", True):
        out["recap"] = t["recap"]["text"]
    return out


def _dumps(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def build_site_files(store: Store) -> dict[str, bytes]:
    registry = load_registry(store)
    aliases = registry.public()
    approved = [t for t in all_tournaments(store) if t.get("status") == "approved"]
    seasons = sorted({t["season"] for t in approved}, reverse=True)

    files: dict[str, bytes] = {}
    season_index = []
    for season in seasons:
        ts = [t for t in approved if t["season"] == season]
        standings = season_standings(ts)
        latest = ts[-1]
        next_bounty = {
            "target": latest.get("placings", {}).get("first"),
            "worth": round(latest.get("bounty", {}).get("carry_out", 0) + DEFAULT_RULES.bounty_per_week, 2),
            "after": latest["date"],
        }
        summaries = [{
            "date": t["date"],
            "players": t.get("players") or len(t.get("results", [])),
            "first": t.get("placings", {}).get("first"),
            "second": t.get("placings", {}).get("second"),
            "third": t.get("placings", {}).get("third"),
            "bounty_winners": t.get("bounty", {}).get("winners", []),
            "has_logs": t.get("source") == "logs",
            "host": t.get("host"),
        } for t in ts]
        files[f"data/season-{season}.json"] = _dumps({
            "season": season,
            "standings": standings,
            "points_race": points_race(ts),
            "knockouts": knockout_matrix(ts),
            "tournaments": summaries,
            "next_bounty": next_bounty,
        })
        for t in ts:
            files[f"data/t/{t['date']}.json"] = _dumps(_public_tournament(t))
        season_index.append({"season": season, "tournaments": len(ts), "last": latest["date"]})

    used = {r["player"] for t in approved for r in t.get("results", [])}
    files["data/league.json"] = _dumps({
        "seasons": season_index,
        "players": {pid: alias for pid, alias in aliases.items() if pid in used},
        "rules": {"buy_in": DEFAULT_RULES.buy_in, "points": DEFAULT_RULES.points},
    })
    for path in STATIC_DIR.rglob("*"):
        if path.is_file():
            files[str(path.relative_to(STATIC_DIR))] = path.read_bytes()
    return files


def content_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def write_local(files: dict[str, bytes], out_dir: str | Path) -> None:
    out = Path(out_dir)
    for path, data in files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
