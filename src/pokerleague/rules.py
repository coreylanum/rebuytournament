"""League rules: payouts, bounty rollover and standings points.

Taken from the 2026 spreadsheet ("Money" and "Standings" tabs) plus the
2027 bounty-rollover decision. Amounts are in dollars.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rules:
    buy_in: float = 10.0
    split: tuple[float, float, float] = (0.55, 0.30, 0.15)
    # With this many players or more, 4th place gets their buy-in back
    # (taken out of 1st place's share).
    fourth_paid_min_players: int = 11
    # $5 each from 1st and 2nd funds a $10 bounty every week. If nobody
    # collects it (target absent or wins again), it rolls over.
    bounty_contribution: tuple[float, float] = (5.0, 5.0)
    points: dict[int, int] = field(default_factory=lambda: {1: 4, 2: 3, 3: 2, 4: 1})

    def paid_places(self, players: int) -> int:
        return 4 if players >= self.fourth_paid_min_players else 3

    def bubble_place(self, players: int) -> int | None:
        place = self.paid_places(players) + 1
        return place if players >= place else None

    @property
    def bounty_per_week(self) -> float:
        return sum(self.bounty_contribution)


DEFAULT_RULES = Rules()


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def compute_payouts(
    finish: list[str],
    paid: set[str],
    bounty_target: str | None,
    bounty_carry_in: float,
    bounty_winners: list[str],
    buy_ins: int | None = None,
    rules: Rules = DEFAULT_RULES,
) -> dict:
    """Work out what everyone is owed for one tournament.

    finish: player ids in finishing order (1st first).
    paid: player ids whose buy-in the treasurer has received.
    bounty_target: last week's winner (None if there isn't one).
    bounty_carry_in: unclaimed bounty money rolling over from earlier weeks.
    bounty_winners: whoever eliminated the target (split evenly), or [].
    buy_ins: total entries including re-entries (defaults to len(finish)).
    """
    players = len(finish)
    buy_ins = buy_ins or players
    pool = _money(buy_ins * rules.buy_in)
    lines: list[dict] = []

    def add(player: str, reason: str, amount: float) -> None:
        lines.append({"player": player, "reason": reason, "amount": _money(amount)})

    places = ["1st", "2nd", "3rd"]
    fourth_paid = players >= rules.fourth_paid_min_players and players >= 4
    for i, share in enumerate(rules.split):
        if i < players:
            add(finish[i], f"{places[i]} place ({int(share * 100)}% of ${pool:g})", pool * share)
    if fourth_paid:
        add(finish[3], "4th place (buy-in back)", rules.buy_in)
        add(finish[0], "pays for 4th place's buy-in", -rules.buy_in)

    # Bounty: always funded by 1st and 2nd; paid out only if someone
    # knocked out the target, otherwise it rolls over.
    bounty_pot = _money(bounty_carry_in)
    if players >= 2:
        for i, contribution in enumerate(rules.bounty_contribution):
            add(finish[i], "bounty contribution", -contribution)
            bounty_pot = _money(bounty_pot + contribution)
    target_played = bounty_target in finish if bounty_target else False
    winners = [w for w in bounty_winners if w != bounty_target] if target_played else []
    if winners:
        reason = "bounty: knocked out last week's winner"
        if len(winners) > 1:
            reason += f" (${bounty_pot:g} split {len(winners)} ways)"
        for w in winners:
            add(w, reason, bounty_pot / len(winners))
        carry_out = 0.0
    else:
        carry_out = bounty_pot

    gross: dict[str, float] = {}
    for line in lines:
        gross[line["player"]] = _money(gross.get(line["player"], 0) + line["amount"])

    transfers = []
    still_owe = []
    for player in finish:
        owes = 0.0 if player in paid else rules.buy_in
        net = _money(gross.get(player, 0) - owes)
        if net > 0:
            transfers.append({"player": player, "amount": net, "unpaid_buy_in_deducted": owes > 0})
        elif net < 0:
            still_owe.append({"player": player, "amount": -net})

    paid_out = _money(sum(gross.values()))
    return {
        "players": players,
        "buy_ins": buy_ins,
        "pool": pool,
        "fourth_paid": fourth_paid,
        "lines": lines,
        "gross": gross,
        "transfers": transfers,
        "still_owe": still_owe,
        "bounty": {
            "target": bounty_target,
            "target_played": target_played,
            "pot": bounty_pot,
            "carry_in": _money(bounty_carry_in),
            "winners": winners,
            "carry_out": carry_out,
        },
        "totals": {
            "pool": pool,
            "collected": _money(rules.buy_in * sum(1 for p in finish if p in paid)),
            "paid_out": paid_out,
            "bounty_carry_change": _money(carry_out - bounty_carry_in),
            # Pool in = winnings out + growth in the rolled-over bounty. Always 0.
            "balance": _money(pool - paid_out - (carry_out - bounty_carry_in)),
        },
    }


def place_labels(players: int, rules: Rules = DEFAULT_RULES) -> dict[int, str]:
    labels = {1: "First", 2: "Second", 3: "Third"}
    if rules.paid_places(players) >= 4:
        labels[4] = "Fourth"
    return labels
