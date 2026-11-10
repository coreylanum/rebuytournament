"""One-time import of the 2026 Google Sheet (exported as .xlsx).

Reads three tabs:
  Player List     -> the player registry (aliases, PokerNow names/IDs, Venmo, PayPal)
  Weekly Players  -> who played each week (and the host)
  Standings       -> 1st-4th, bubble, first out and bounty winner per week

Sheet dates are month/day only in practice (the year cells are inconsistent),
so every column is taken as 2026.
"""

from __future__ import annotations

import re
from datetime import date as Date

from ..players import Player, Registry, slugify
from ..rules import DEFAULT_RULES

PLACING_ROWS = {"first": 3, "second": 4, "third": 5, "fourth": 6, "bubble": 7, "first_out": 8, "bounty": 9}


def _text(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v not in (None, "") else ""


def _alias_from_name(name: str) -> str:
    parts = name.split()
    return f"{parts[0].capitalize()} {parts[-1][0].upper()}" if len(parts) >= 2 else ""


def import_players(ws) -> tuple[Registry, list[str]]:
    registry, notes = Registry(), []
    for row in ws.iter_rows(min_row=2, values_only=True):
        cells = [_text(c) for c in (list(row) + [None] * 7)[:7]]
        person, handle, alias, ids, venmo, paypal, last4 = cells
        m = re.match(r"^(.*?)\s*<([^>]+)>", person)
        full_name, email = (m.group(1).strip(), m.group(2).strip()) if m else (person, "")
        if "@" in full_name and not alias and not handle:
            continue  # email-only row or a stray "name @ id" note
        if not alias:
            alias = _alias_from_name(full_name) if handle else ""
        if not alias:
            continue
        pokernow_ids = [i.replace("DETAILS", "").strip() for i in ids.split(",") if i.strip()]
        registry.add(Player(
            id=slugify(alias), alias=alias, full_name=full_name, email=email,
            pokernow_ids=pokernow_ids, handles=[handle] if handle else [],
            venmo=venmo, paypal=paypal, phone_last4=last4.replace(".0", ""),
        ))
    return registry, notes


def _column_date(value) -> str | None:
    if hasattr(value, "month"):
        return Date(2026, value.month, value.day).isoformat()
    m = re.match(r"(\d+)/(\d+)", _text(value))
    return Date(2026, int(m[1]), int(m[2])).isoformat() if m else None


def import_history(weekly_ws, standings_ws, registry: Registry) -> tuple[list[dict], list[str]]:
    records, warnings = [], []

    def pid(alias: str) -> str:
        player = registry.by_alias(alias)
        if not player:
            player = registry.add(Player(id=slugify(alias), alias=alias))
            warnings.append(f"added '{alias}' to the registry (in results but not on the Player List)")
        return player.id

    previous_winner = None
    for col in range(2, weekly_ws.max_column + 1):
        date = _column_date(weekly_ws.cell(1, col).value)
        first = _text(standings_ws.cell(PLACING_ROWS["first"], col).value)
        if not date or not first:
            continue
        host = _text(weekly_ws.cell(2, col).value)
        attendees = []
        for r in range(3, weekly_ws.max_row + 1):
            name = _text(weekly_ws.cell(r, col).value)
            if name and pid(name) not in attendees:
                attendees.append(pid(name))
        raw = {k: _text(standings_ws.cell(r, col).value) for k, r in PLACING_ROWS.items()}
        placings = {k: (pid(v) if v else None) for k, v in raw.items() if k != "bounty"}
        for key, player in placings.items():
            if player and player not in attendees:
                attendees.append(player)
                warnings.append(f"{date}: {raw[key]} recorded as {key} but missing from Weekly Players; added")
        if placings["first"] and placings["first"] == placings["first_out"]:
            warnings.append(f"{date}: {raw['first']} is recorded as both first and first out")
        n = len(attendees)
        place_of = {placings[k]: i for i, k in enumerate(["first", "second", "third", "fourth"], start=1) if placings.get(k)}
        if placings.get("bubble"):
            place_of.setdefault(placings["bubble"], DEFAULT_RULES.bubble_place(n) or n)
        if placings.get("first_out"):
            place_of.setdefault(placings["first_out"], n)
        results = [{"player": p, "place": place_of.get(p)} for p in attendees]
        results.sort(key=lambda r: (r["place"] is None, r["place"] or 0))
        bounty_winner = pid(raw["bounty"]) if raw["bounty"] else None
        records.append({
            "date": date,
            "season": 2026,
            "status": "approved",
            "source": "sheet",
            "host": host,
            "players": n,
            "results": results,
            "placings": placings,
            "bounty": {
                "target": previous_winner,
                "winners": [bounty_winner] if bounty_winner else [],
                "carry_out": 0.0,
            },
            "warnings": [w for w in warnings if w.startswith(date)],
        })
        previous_winner = placings["first"]
    return records, warnings


def import_workbook(path: str) -> tuple[Registry, list[dict], list[str]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    registry, notes = import_players(wb["Player List"])
    records, warnings = import_history(wb["Weekly Players"], wb["Standings"], registry)
    return registry, records, notes + warnings
