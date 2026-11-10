"""The weekly workflow: logs in -> draft results -> treasurer review -> approved.

A tournament record keeps the raw log analysis (keyed by PokerNow ID) and
everything derived from it (keyed by player id). Derived fields are rebuilt
whenever something changes: a new log upload, a player mapping, who has
paid, or an earlier week's approval (which moves the bounty).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .analysis import analyze
from .logparser import parse_logs, read_log
from .players import Player, Registry, slugify
from .rules import DEFAULT_RULES, Rules, compute_payouts
from .store import Store

REGISTRY_KEY = "private/players.json"
UNMATCHED_PREFIX = "?"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tournament_key(date: str) -> str:
    return f"tournaments/{date}.json"


def load_registry(store: Store) -> Registry:
    return Registry.from_dict(store.get_json(REGISTRY_KEY, {"players": []}))


def save_registry(store: Store, registry: Registry) -> None:
    store.put_json(REGISTRY_KEY, registry.to_dict())


def all_tournaments(store: Store) -> list[dict]:
    records = [store.get_json(k) for k in store.list("tournaments/") if k.endswith(".json")]
    return sorted((r for r in records if r), key=lambda r: r["date"])


def get_tournament(store: Store, date: str) -> dict | None:
    return store.get_json(tournament_key(date))


def save_tournament(store: Store, record: dict) -> None:
    record["updated_at"] = now()
    store.put_json(tournament_key(record["date"]), record)


def bounty_state_before(tournaments: list[dict], date: str) -> tuple[str | None, float]:
    """(target, carry_in) for the tournament on `date`: last approved week's winner
    and whatever bounty money that week left unclaimed."""
    prior = [t for t in tournaments if t["date"] < date and t.get("status") == "approved"]
    if not prior:
        return None, 0.0
    last = prior[-1]
    return last.get("placings", {}).get("first"), float(last.get("bounty", {}).get("carry_out", 0.0))


def _map_ids(value, mapping: dict[str, str]):
    """Recursively replace PokerNow IDs with player ids in lists/dicts."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_map_ids(v, mapping) for v in value]
    if isinstance(value, dict):
        return {mapping.get(k, k): _map_ids(v, mapping) for k, v in value.items()}
    return value


def rebuild(record: dict, registry: Registry, tournaments: list[dict], rules: Rules = DEFAULT_RULES) -> dict:
    """Recompute every derived field of a log-based record."""
    analysis = record.get("analysis")
    if not analysis:
        return record
    names = analysis["names"]
    mapping, unmatched = registry.match_all(names)
    for pid in names:
        mapping.setdefault(pid, f"{UNMATCHED_PREFIX}{pid}")

    results = [{
        "place": f["place"],
        "player": mapping[f["pid"]],
        "pokernow_id": f["pid"],
        "handle": f["name"],
        "busted_by": [mapping.get(p, p) for p in f["busted_by"]],
        "busted_minute": f["busted_minute"],
    } for f in analysis["finish"]]
    order = [r["player"] for r in results]
    n = len(order)

    placings = {key: (order[i] if i < n else None) for i, key in enumerate(["first", "second", "third"])}
    placings["fourth"] = order[3] if rules.paid_places(n) >= 4 and n >= 4 else None
    bubble = rules.bubble_place(n)
    placings["bubble"] = order[bubble - 1] if bubble else None
    placings["first_out"] = order[-1] if n > 1 else None
    placings.update(record.get("overrides", {}).get("placings", {}))

    target, carry_in = bounty_state_before(tournaments, record["date"])
    target_result = next((r for r in results if r["player"] == target), None)
    bounty_winners = record.get("overrides", {}).get("bounty_winners")
    if bounty_winners is None:
        bounty_winners = target_result["busted_by"] if target_result else []
    payouts = compute_payouts(order, set(record.get("paid", [])), target, carry_in, bounty_winners, analysis["buy_ins"], rules)

    record.update({
        "season": int(record["date"][:4]),
        "players": n,
        "results": results,
        "placings": placings,
        "bounty": payouts["bounty"],
        "payouts": payouts,
        "unmatched": unmatched,
        "knockouts": _map_ids(analysis["knockouts"], mapping),
        "player_stats": _map_ids(analysis["player_stats"], mapping),
        "chip_series": _map_ids(analysis["chip_series"], mapping),
        "allin_showdowns": _map_ids(analysis["allin_showdowns"], mapping),
        "biggest_pots": _map_ids(analysis["biggest_pots"], mapping),
        "blind_levels": analysis["blind_levels"],
        "hands_played": analysis["hands_played"],
        "duration_minutes": analysis["duration_minutes"],
        "warnings": analysis["warnings"],
    })
    return record


def ingest_logs(store: Store, date: str, files: list[tuple[str, str]], *, tournament_url: str | None = None,
                host: str | None = None, rules: Rules = DEFAULT_RULES) -> dict:
    """Save uploaded table logs for `date` and (re)build the draft from all logs so far.

    Tables can be uploaded one at a time; each upload re-analyzes every log for the date.
    """
    record = get_tournament(store, date) or {"date": date, "status": "draft", "source": "logs", "paid": [], "created_at": now()}
    if record.get("status") == "approved":
        raise ValueError(f"{date} is already approved; reopen it before uploading new logs")
    for name, text in files:
        safe = name.replace("/", "_")
        store.put_bytes(f"raw/{date}/{safe}", text.encode(), "text/csv")
    entries = []
    for key in store.list(f"raw/{date}/"):
        entries += read_log(key, text=store.get_bytes(key).decode("utf-8-sig"))
    record["analysis"] = analyze(parse_logs(entries))
    record["log_files"] = [k.rsplit("/", 1)[-1] for k in store.list(f"raw/{date}/")]
    if tournament_url:
        record["tournament_url"] = tournament_url
    if host:
        record["host"] = host
    registry = load_registry(store)
    rebuild(record, registry, all_tournaments(store), rules)
    save_registry(store, registry)  # keeps any newly learned PokerNow IDs
    save_tournament(store, record)
    return record


def _refresh(store: Store, record: dict, registry: Registry | None = None) -> dict:
    registry = registry or load_registry(store)
    rebuild(record, registry, all_tournaments(store))
    save_tournament(store, record)
    return record


def set_paid(store: Store, date: str, paid: list[str]) -> dict:
    record = get_tournament(store, date)
    record["paid"] = sorted(set(paid))
    return _refresh(store, record)


def assign_player(store: Store, date: str, pokernow_id: str, player_id: str | None = None,
                  new_alias: str | None = None) -> dict:
    """Map an unrecognized PokerNow ID to an existing player, or create a new player."""
    registry = load_registry(store)
    record = get_tournament(store, date)
    handle = record["analysis"]["names"].get(pokernow_id, "")
    if new_alias:
        player_id = slugify(new_alias)
        registry.add(Player(id=player_id, alias=new_alias))
    if not player_id or not registry.get(player_id):
        raise ValueError("unknown player")
    registry.learn(player_id, pokernow_id, handle)
    save_registry(store, registry)
    return _refresh(store, record, registry)


def approve(store: Store, date: str, approved_by: str = "") -> dict:
    record = get_tournament(store, date)
    if record.get("unmatched"):
        raise ValueError("map every player before approving")
    earlier_drafts = [t["date"] for t in all_tournaments(store) if t["date"] < date and t.get("status") != "approved"]
    if earlier_drafts:
        raise ValueError(f"approve earlier tournaments first: {', '.join(earlier_drafts)}")
    record["status"] = "approved"
    record["approved_at"] = now()
    record["approved_by"] = approved_by
    save_tournament(store, record)
    # Later drafts depend on this week's winner and bounty.
    registry = load_registry(store)
    for t in all_tournaments(store):
        if t["date"] > date and t.get("status") != "approved" and t.get("analysis"):
            _refresh(store, t, registry)
    return record


def reopen(store: Store, date: str) -> dict:
    record = get_tournament(store, date)
    later = [t["date"] for t in all_tournaments(store) if t["date"] > date and t.get("status") == "approved"]
    if later:
        raise ValueError(f"reopen later tournaments first: {', '.join(later)}")
    record["status"] = "draft"
    record.pop("approved_at", None)
    return _refresh(store, record)
