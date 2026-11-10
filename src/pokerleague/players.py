"""Player registry: who's who across PokerNow IDs, screen names and payment accounts.

A person can show up under several PokerNow IDs (a new one per device or
browser) and several screen names, so matching tries the ID first and then
the screen name. Anything unmatched is flagged for someone to map by hand.

The registry holds contact and payment details, so it is never published;
the website only gets `id` and `alias`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


def slugify(alias: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", alias.lower()).strip("-")


def _norm(handle: str) -> str:
    return re.sub(r"\s+", " ", handle).strip().lower()


@dataclass
class Player:
    id: str
    alias: str  # "Corey L": the name used in results and standings
    full_name: str = ""
    email: str = ""
    pokernow_ids: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    venmo: str = ""
    paypal: str = ""
    phone_last4: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Registry:
    def __init__(self, players: list[Player] | None = None):
        self.players: dict[str, Player] = {}
        for p in players or []:
            self.add(p)

    @classmethod
    def from_dict(cls, data: dict) -> "Registry":
        return cls([Player(**p) for p in data.get("players", [])])

    def to_dict(self) -> dict:
        return {"players": [p.to_dict() for p in sorted(self.players.values(), key=lambda p: p.alias.lower())]}

    def add(self, player: Player) -> Player:
        existing = self.players.get(player.id)
        if existing:
            for attr in ("pokernow_ids", "handles"):
                merged = getattr(existing, attr) + [v for v in getattr(player, attr) if v not in getattr(existing, attr)]
                setattr(existing, attr, merged)
            for attr in ("full_name", "email", "venmo", "paypal", "phone_last4"):
                if not getattr(existing, attr) and getattr(player, attr):
                    setattr(existing, attr, getattr(player, attr))
            return existing
        self.players[player.id] = player
        return player

    def get(self, player_id: str) -> Player | None:
        return self.players.get(player_id)

    def by_alias(self, alias: str) -> Player | None:
        return self.players.get(slugify(alias))

    def match(self, pokernow_id: str, handle: str) -> tuple[str | None, str]:
        """Return (player id, how it matched). How: "id", "handle" or "none"."""
        for p in self.players.values():
            if pokernow_id in p.pokernow_ids:
                return p.id, "id"
        wanted = _norm(handle)
        hits = [p for p in self.players.values() if any(_norm(h) == wanted for h in p.handles)]
        if len(hits) == 1:
            return hits[0].id, "handle"
        return None, "none"

    def learn(self, player_id: str, pokernow_id: str, handle: str) -> None:
        """Remember a new ID or screen name for a player (after a match is confirmed)."""
        p = self.players[player_id]
        if pokernow_id and pokernow_id not in p.pokernow_ids:
            p.pokernow_ids.append(pokernow_id)
        if handle and _norm(handle) not in {_norm(h) for h in p.handles}:
            p.handles.append(handle)

    def match_all(self, names: dict[str, str], learn: bool = True) -> tuple[dict[str, str], list[dict]]:
        """Map every PokerNow ID in a tournament to a player id.

        Returns (mapping, unmatched). Handle-only matches teach the registry
        the new PokerNow ID when `learn` is set.
        """
        mapping, unmatched = {}, []
        for pid, handle in names.items():
            player_id, how = self.match(pid, handle)
            if player_id:
                mapping[pid] = player_id
                if learn and how == "handle":
                    self.learn(player_id, pid, handle)
            else:
                unmatched.append({"pokernow_id": pid, "handle": handle})
        return mapping, unmatched

    def public(self) -> dict[str, str]:
        return {p.id: p.alias for p in self.players.values()}
