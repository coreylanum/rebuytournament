"""Card parsing, 7-card hand evaluation and all-in equity.

Cards are represented as (rank, suit) tuples with rank 2..14 (ace high) and
suit one of "shdc". PokerNow writes cards like "A♠" or "10♦".
"""

from __future__ import annotations

import itertools
import random
import re
from collections import Counter

Card = tuple[int, str]

_SUIT_SYMBOLS = {"♠": "s", "♥": "h", "♦": "d", "♣": "c"}
_RANKS = {"J": 11, "Q": 12, "K": 13, "A": 14, "T": 10}
_RANK_NAMES = {11: "J", 12: "Q", 13: "K", 14: "A", 10: "10"}
_SUIT_DISPLAY = {v: k for k, v in _SUIT_SYMBOLS.items()}

CATEGORY_NAMES = [
    "High Card",
    "Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
]

FULL_DECK: list[Card] = [(r, s) for r in range(2, 15) for s in "shdc"]


def parse_card(text: str) -> Card:
    text = text.strip().rstrip(".")
    suit_char = text[-1]
    suit = _SUIT_SYMBOLS.get(suit_char, suit_char.lower())
    if suit not in "shdc":
        raise ValueError(f"bad card: {text!r}")
    rank_text = text[:-1].upper()
    rank = _RANKS.get(rank_text) or int(rank_text)
    if not 2 <= rank <= 14:
        raise ValueError(f"bad card: {text!r}")
    return (rank, suit)


_CARD_RE = re.compile(r"(10|[2-9TJQKA])([♠♥♦♣])")


def parse_cards(text: str) -> list[Card]:
    """Every card mentioned in `text`, e.g. "4♥, 5♣, 3♥ [9♣]" -> 4 cards."""
    return [parse_card(rank + suit) for rank, suit in _CARD_RE.findall(text)]


def card_str(card: Card) -> str:
    rank, suit = card
    return f"{_RANK_NAMES.get(rank, str(rank))}{_SUIT_DISPLAY[suit]}"


def _straight_high(ranks: set[int]) -> int:
    """Highest card of the best straight in `ranks`, or 0."""
    if 14 in ranks:
        ranks = ranks | {1}
    for high in range(14, 4, -1):
        if all(r in ranks for r in range(high - 4, high + 1)):
            return high
    return 0


def evaluate(cards: list[Card]) -> tuple:
    """Score the best 5-card hand from 5-7 cards. Higher tuples win."""
    by_suit: dict[str, list[int]] = {}
    for rank, suit in cards:
        by_suit.setdefault(suit, []).append(rank)
    flush_ranks = next((sorted(rs, reverse=True) for rs in by_suit.values() if len(rs) >= 5), None)
    if flush_ranks:
        sf = _straight_high(set(flush_ranks))
        if sf:
            return (8, sf)

    counts = Counter(rank for rank, _ in cards)
    # Sort ranks by (count, rank) descending.
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = max(r for r in counts if r != quad)
        return (7, quad, kicker)
    trips = sorted((r for r, c in counts.items() if c >= 3), reverse=True)
    pairs = sorted((r for r, c in counts.items() if c == 2), reverse=True)
    if trips and (len(trips) > 1 or pairs):
        full_of = max(trips[1] if len(trips) > 1 else 0, pairs[0] if pairs else 0)
        return (6, trips[0], full_of)
    if flush_ranks:
        return (5, *flush_ranks[:5])
    st = _straight_high(set(counts))
    if st:
        return (4, st)
    if trips:
        kickers = sorted((r for r in counts if r != trips[0]), reverse=True)[:2]
        return (3, trips[0], *kickers)
    if len(pairs) >= 2:
        kicker = max(r for r in counts if r not in pairs[:2])
        return (2, pairs[0], pairs[1], kicker)
    if pairs:
        kickers = sorted((r for r in counts if r != pairs[0]), reverse=True)[:3]
        return (1, pairs[0], *kickers)
    return (0, *sorted(counts, reverse=True)[:5])


def category_name(score: tuple) -> str:
    return CATEGORY_NAMES[score[0]]


def equity(hands: list[list[Card]], board: list[Card], iterations: int = 3000, seed: int = 7) -> list[float]:
    """Share of the pot each hand wins on average from this point (ties split).

    Exact when two or fewer board cards are still to come, Monte Carlo otherwise
    (seeded so results are reproducible).
    """
    used = {c for h in hands for c in h} | set(board)
    deck = [c for c in FULL_DECK if c not in used]
    need = 5 - len(board)
    shares = [0.0] * len(hands)

    def score_runout(runout: tuple[Card, ...]) -> None:
        full_board = board + list(runout)
        scores = [evaluate(h + full_board) for h in hands]
        best = max(scores)
        winners = [i for i, s in enumerate(scores) if s == best]
        for i in winners:
            shares[i] += 1 / len(winners)

    if need <= 0:
        score_runout(())
        total = 1
    elif need <= 2:
        total = 0
        for runout in itertools.combinations(deck, need):
            score_runout(runout)
            total += 1
    else:
        rng = random.Random(seed)
        for _ in range(iterations):
            score_runout(tuple(rng.sample(deck, need)))
        total = iterations
    return [s / total for s in shares]
