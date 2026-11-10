from pathlib import Path

import pytest

from pokerleague.analysis import analyze
from pokerleague.cards import equity, evaluate, parse_cards
from pokerleague.league import (
    all_tournaments, approve, assign_player, get_tournament, ingest_logs, save_registry, save_tournament, set_paid,
)
from pokerleague.logparser import load_files
from pokerleague.players import Player, Registry
from pokerleague.rules import compute_payouts
from pokerleague.standings import season_standings
from pokerleague.store import LocalStore

FIXTURES = Path(__file__).parent / "fixtures" / "2026-11-03"
LOGS = sorted(FIXTURES.glob("*.csv"))

# PokerNow IDs as they appear in the 11/3/2026 logs.
PLAYERS_1103 = {
    "doug-b": ("Doug B", "xrol6CmlAr", "Doug USN"),
    "corey-l": ("Corey L", "mmFiRkqQT-", "CoreyMarathon"),
    "paul-w": ("Paul W", "k6n3G5LiWn", "wink978"),
    "dave-b": ("Dave B", "6gpip_WTZ-", "Dave B7"),
    "john-p": ("John P", "JMbMIiNlJ6", "John P"),
    "susie-b": ("Susie B", "CQldUTxVQA", "TAZ30"),
    "david-a": ("David A", "jVcvYdOyUH", "carbondale123"),
    "rob-b": ("Rob B", "7R2Drno_MT", "RB33"),
    "suzanne-b": ("Suzanne B", "VZ9F4tWmla", "Suzanne06"),
    "jim-o": ("Jim O", "58sUXGf9b3", "Jim O"),
    "charlie-h": ("Charlie H", "265TZrdQEI", "Charlie H"),
    "heather-b": ("Heather B", "fyAvO09bBJ", "sassaheat"),
}
FINISH_1103 = ["doug-b", "corey-l", "paul-w", "dave-b", "john-p", "susie-b", "david-a", "rob-b",
               "suzanne-b", "jim-o", "charlie-h", "heather-b"]


@pytest.fixture(scope="module")
def analysis():
    return analyze(load_files(LOGS))


def registry_1103(skip: set[str] = frozenset()) -> Registry:
    return Registry([
        Player(id=pid, alias=alias, pokernow_ids=[pn_id], handles=[handle])
        for pid, (alias, pn_id, handle) in PLAYERS_1103.items() if pid not in skip
    ])


# --- cards ------------------------------------------------------------------

def test_hand_ranking_order():
    board = parse_cards("2♣, 7♦, 9♠, J♥, K♣")
    pair = evaluate(parse_cards("K♦ 3♠") + board)
    two_pair = evaluate(parse_cards("K♦ 9♦") + board)
    straight = evaluate(parse_cards("10♦ Q♦") + board)
    assert pair < two_pair < straight
    wheel = evaluate(parse_cards("A♠, 2♦, 3♣, 4♥, 5♠, K♦, Q♣"))
    assert wheel[0] == 4 and wheel[1] == 5


def test_equity_aces_vs_kings():
    aa, kk = parse_cards("A♠ A♥"), parse_cards("K♠ K♥")
    eq = equity([aa, kk], [], iterations=4000)
    assert 0.78 < eq[0] < 0.86
    # Exact on the turn: kings need one of two kings.
    turn = parse_cards("2♣ 7♦ 9♠ J♥")
    assert equity([aa, kk], turn)[1] == pytest.approx(2 / 44)


# --- log analysis -------------------------------------------------------------

def test_finish_order_matches_recorded_results(analysis):
    names = [f["name"] for f in analysis["finish"]]
    assert names == ["Doug USN", "CoreyMarathon", "wink978", "Dave B7", "John P", "TAZ30",
                     "carbondale123", "RB33", "Suzanne06", "Jim O", "Charlie H", "sassaheat"]
    assert analysis["players"] == 12 and analysis["warnings"] == []


def test_knockouts_identify_bounty_winner(analysis):
    by_victim = {analysis["names"][k["victim"]]: [analysis["names"][p] for p in k["by"]] for k in analysis["knockouts"]}
    assert by_victim["Suzanne06"] == ["CoreyMarathon"]  # the 11/3 bounty
    assert by_victim["CoreyMarathon"] == ["Doug USN"]
    assert by_victim["Dave B7"] == ["wink978"]


def test_allin_equity_flags_suckouts(analysis):
    suzanne_ko = next(s for s in analysis["allin_showdowns"] if s["hand"].endswith("#56"))
    corey = next(p for p in suzanne_ko["players"] if p["cards"] == ["A♣", "Q♦"])
    assert corey["won"] and corey["equity"] < 0.35 and suzanne_ko["suckout"]


# --- payouts ------------------------------------------------------------------

def test_payouts_match_the_11_3_sheet():
    # Sheet: Doug paid, Corey unpaid, Paul unpaid, Dave paid; Corey knocked out Suzanne (bounty).
    paid = set(FINISH_1103) - {"corey-l", "paul-w"}
    p = compute_payouts(FINISH_1103, paid, "suzanne-b", 0.0, ["corey-l"])
    transfers = {t["player"]: t["amount"] for t in p["transfers"]}
    assert transfers == {"doug-b": 51.0, "corey-l": 31.0, "paul-w": 8.0, "dave-b": 10.0}
    assert p["bounty"]["carry_out"] == 0 and p["totals"]["balance"] == 0


def test_small_field_has_no_fourth_place_and_bubble_is_fourth():
    finish = [f"p{i}" for i in range(1, 10)]
    p = compute_payouts(finish, set(finish), None, 0.0, [])
    assert {t["player"]: t["amount"] for t in p["transfers"]} == {"p1": 44.5, "p2": 22.0, "p3": 13.5}
    assert not p["fourth_paid"]
    assert p["bounty"]["carry_out"] == 10.0 and p["totals"]["balance"] == 0


def test_bounty_rolls_over_when_target_wins_again():
    finish = [f"p{i}" for i in range(1, 13)]
    week1 = compute_payouts(finish, set(finish), "p1", 0.0, [])
    assert week1["bounty"]["carry_out"] == 10.0
    week2 = compute_payouts(finish, set(finish), "p1", week1["bounty"]["carry_out"], ["p3"])
    assert week2["bounty"]["pot"] == 20.0
    assert {l["player"]: l["amount"] for l in week2["lines"] if l["reason"].startswith("bounty:")} == {"p3": 20.0}
    assert week2["totals"]["balance"] == 0


def test_bounty_split_between_two_eliminators():
    finish = [f"p{i}" for i in range(1, 13)]
    p = compute_payouts(finish, set(finish), "p12", 0.0, ["p4", "p5"])
    bounty = {l["player"]: l["amount"] for l in p["lines"] if l["reason"].startswith("bounty:")}
    assert bounty == {"p4": 5.0, "p5": 5.0}


def test_unpaid_loser_shows_as_still_owing():
    finish = [f"p{i}" for i in range(1, 13)]
    p = compute_payouts(finish, set(finish) - {"p9"}, None, 0.0, [])
    assert p["still_owe"] == [{"player": "p9", "amount": 10.0}]


# --- workflow -----------------------------------------------------------------

def _seed_previous_week(store):
    save_tournament(store, {
        "date": "2026-10-27", "season": 2026, "status": "approved", "source": "sheet",
        "results": [{"player": "suzanne-b", "place": 1}], "placings": {"first": "suzanne-b"},
        "bounty": {"winners": [], "carry_out": 0.0},
    })


def test_weekly_workflow(tmp_path):
    store = LocalStore(tmp_path)
    save_registry(store, registry_1103(skip={"heather-b"}))
    _seed_previous_week(store)
    files = [(p.name, p.read_text(encoding="utf-8")) for p in LOGS]

    # Upload tables one at a time; the second upload completes the picture.
    ingest_logs(store, "2026-11-03", files[:1])
    record = ingest_logs(store, "2026-11-03", files[1:])
    assert record["unmatched"] == [{"pokernow_id": "fyAvO09bBJ", "handle": "sassaheat"}]
    with pytest.raises(ValueError):
        approve(store, "2026-11-03")

    record = assign_player(store, "2026-11-03", "fyAvO09bBJ", new_alias="Heather B")
    assert record["unmatched"] == []
    assert [r["player"] for r in record["results"]] == FINISH_1103
    assert record["placings"] == {"first": "doug-b", "second": "corey-l", "third": "paul-w", "fourth": "dave-b",
                                  "bubble": "john-p", "first_out": "heather-b"}
    assert record["bounty"]["target"] == "suzanne-b" and record["bounty"]["winners"] == ["corey-l"]

    record = set_paid(store, "2026-11-03", sorted(set(FINISH_1103) - {"corey-l", "paul-w"}))
    assert {t["player"]: t["amount"] for t in record["payouts"]["transfers"]}["corey-l"] == 31.0
    approve(store, "2026-11-03", approved_by="treasurer")
    assert get_tournament(store, "2026-11-03")["status"] == "approved"

    table = season_standings(all_tournaments(store))
    doug = next(r for r in table if r["player"] == "doug-b")
    assert doug["points"] == 4 and doug["stats"]["knockouts"] == 5.0
