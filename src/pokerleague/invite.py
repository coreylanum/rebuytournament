"""The weekly invite email (Phase 2).

PokerNow creates, admits and starts the tournament itself (a weekly repeating
scheduled tournament in the group's PokerNow Club), so the invite only needs
the club link, which never changes. A schedule in AWS calls `send_weekly_invite`
on game day; the treasurer edits the settings on the admin's Settings page.
"""

from __future__ import annotations

from datetime import date as Date, datetime, timedelta

from . import league
from .config import settings
from .outbound import paypal_link, send_email, venmo_pay_link, venmo_username
from .rules import DEFAULT_RULES
from .store import Store

SETTINGS_KEY = "private/settings.json"
TIMEZONE = "America/New_York"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DEFAULTS = {
    "invites_enabled": False,   # off until the club link is filled in
    "club_url": "",
    "game_weekday": 1,          # Tuesday
    "start_time": "19:30",      # Eastern
    "treasurer_venmo": "",
    "treasurer_paypal": "",
    "skip_dates": [],
    "note": "",
}


def load_settings(store: Store) -> dict:
    return {**DEFAULTS, **(store.get_json(SETTINGS_KEY) or {})}


def save_settings(store: Store, values: dict) -> dict:
    current = load_settings(store)
    current.update({k: v for k, v in values.items() if k in DEFAULTS})
    if current["invites_enabled"] and not current["club_url"]:
        raise ValueError("add the PokerNow club link before turning invites on")
    store.put_json(SETTINGS_KEY, current)
    return current


def local_today() -> Date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(TIMEZONE)).date()
    except Exception:  # no tz database: UTC is the same day at 10 am Eastern
        return Date.today()


def next_game_date(cfg: dict, today: Date | None = None) -> Date:
    today = today or local_today()
    return today + timedelta(days=(int(cfg["game_weekday"]) - today.weekday()) % 7)


def _time_label(hhmm: str) -> str:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return f"{(hour - 1) % 12 + 1}:{minute:02d} {'am' if hour < 12 else 'pm'}"


def _money(amount: float) -> str:
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def build_invite(store: Store, game: Date, cfg: dict | None = None) -> tuple[str, str]:
    cfg = cfg or load_settings(store)
    league_name = settings().league_name
    when = f"{WEEKDAYS[game.weekday()]}, {game.strftime('%B')} {game.day}"
    start = _time_label(cfg["start_time"])
    buy_in = DEFAULT_RULES.buy_in
    lines = [f"Poker tonight: {when}, {start} Eastern.", ""]

    lines += [f"Register here: {cfg['club_url'] or '(club link not set)'}",
              "New to the club? Open the link, log in to PokerNow and ask to join. You only do that once.", ""]

    pay = [f"The buy-in is {_money(buy_in)}, paid to the treasurer before you play:"]
    note = f"Poker {game.month}/{game.day}"
    if (handle := venmo_username(cfg["treasurer_venmo"])):
        pay.append(f"  Venmo: {venmo_pay_link(handle, buy_in, note)}")
    if (link := paypal_link(cfg["treasurer_paypal"], buy_in)):
        pay.append(f"  PayPal: {link}")
    elif cfg["treasurer_paypal"]:
        pay.append(f"  PayPal: {cfg['treasurer_paypal']}")
    if len(pay) > 1:
        lines += pay + [f"Unpaid winnings have the {_money(buy_in)} taken out.", ""]

    tournaments = league.all_tournaments(store)
    target, carry_in = league.bounty_state_before(tournaments, game.isoformat())
    if target:
        alias = league.load_registry(store).public().get(target, target)
        worth = carry_in + DEFAULT_RULES.bounty_per_week
        rolled = f" It rolled over from last week, so it's worth {_money(worth)}." if carry_in else ""
        lines += [f"Bounty: {alias} won last week. Knock out {alias} to collect {_money(worth)}.{rolled}", ""]

    if cfg["note"].strip():
        lines += [cfg["note"].strip(), ""]
    lines.append(f"Standings and past results: {settings().site_url}")
    return f"{league_name}: poker tonight at {start}", "\n".join(lines) + "\n"


def sent_key(game: Date) -> str:
    return f"invites/{game.isoformat()}.json"


def invite_status(store: Store, game: Date, cfg: dict | None = None) -> str:
    """Why the scheduled invite will or won't go out for `game`."""
    cfg = cfg or load_settings(store)
    if not cfg["invites_enabled"]:
        return "Invites are turned off."
    if game.isoformat() in cfg["skip_dates"]:
        return "Skipped: this week is on the skip list."
    if (sent := store.get_json(sent_key(game))):
        return f"Already sent at {sent['sent_at']}."
    if not settings().group_email:
        return "No group email address is configured."
    return "Will send."


def send_weekly_invite(store: Store, game: Date | None = None, *, force: bool = False, to: str | None = None,
                       scheduled: bool = False) -> dict:
    """Send the invite for `game` (default: the next game day). Scheduled runs are
    idempotent: a retry or a second run the same week doesn't send twice."""
    cfg = load_settings(store)
    if scheduled:
        # The schedule fires every morning; only game day sends, so the game day lives in Settings alone.
        today = local_today()
        if today.weekday() != int(cfg["game_weekday"]):
            return {"sent": False, "date": today.isoformat(), "reason": "Not game day."}
        game = today
    game = game or next_game_date(cfg)
    if to is None:
        status = invite_status(store, game, cfg)
        if status != "Will send." and not (force and status.startswith("Already sent")):
            return {"sent": False, "date": game.isoformat(), "reason": status}
    subject, text = build_invite(store, game, cfg)
    recipient = to or settings().group_email
    send_email(store, recipient, subject, text)
    if to is None:
        store.put_json(sent_key(game), {"sent_at": league.now(), "to": recipient, "subject": subject})
    return {"sent": True, "date": game.isoformat(), "to": recipient}
