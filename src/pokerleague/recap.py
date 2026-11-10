"""Weekly recap email, written by Claude from the week's facts.

Calls the Anthropic API with the league's API key by default
(RECAP_PROVIDER=anthropic); Amazon Bedrock is still supported with
RECAP_PROVIDER=bedrock. The model only sees results, knockouts and hand
highlights under player aliases ("Corey L"), never emails or payment details.
If no key is configured or the call fails, a plain template recap is used
instead so the weekly email still goes out.

The API key is looked up in this order:
  ANTHROPIC_API_KEY             environment variable
  ANTHROPIC_API_KEY_SECRET_ARN  AWS Secrets Manager secret (plain string), used in Lambda
  ANTHROPIC_API_KEY_FILE        a local file holding only the key, used for local runs
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .standings import season_standings

log = logging.getLogger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "bedrock": "anthropic.claude-opus-5-5"}
BEDROCK_FALLBACK_MODEL = "anthropic.claude-opus-4-8"

SYSTEM_PROMPT = """You write the weekly recap email for a friendly neighborhood online poker league that has played a $10 hold'em tournament every week since 2021. Readers are neighbors who know each other, so a warm, playful, lightly teasing tone works; never mean.

Write about 250 words of plain text (no Markdown headings or bold; simple line breaks are fine). Include:
- the top finishers and what they won, and who was on the bubble and first out
- the bounty: who collected it for knocking out last week's winner, or that it rolls over and what it is worth next week
- two or three of the best moments from the hand highlights (bad beats, big pots, coolers), with the cards
- a line on the season standings race

Use only the facts provided. Do not invent hands, quotes or numbers, and do not mention who has or hasn't paid. Refer to players exactly by the names given. End with a short sign-off from "the Poker Bot"."""


def _name(aliases: dict[str, str], player: str | None) -> str | None:
    if player is None:
        return None
    return aliases.get(player, player)


def build_facts(record: dict, tournaments: list[dict], aliases: dict[str, str]) -> dict:
    """Everything the recap may mention, with player ids replaced by aliases."""
    n = lambda p: _name(aliases, p)  # noqa: E731
    placings = {k: n(v) for k, v in record.get("placings", {}).items() if v}
    payouts = record.get("payouts", {})
    bounty = record.get("bounty", {})
    approved = [t for t in tournaments if t.get("status") == "approved" and t["season"] == record["season"]]
    if record not in approved:
        approved = [t for t in approved if t["date"] != record["date"]] + [record]
    table = season_standings(approved)
    before = {r["player"]: r["rank"] for r in season_standings([t for t in approved if t["date"] < record["date"]])}

    knockouts = [{
        "minute": k["minute"],
        "eliminated": n(k["victim"]),
        "by": [n(p) for p in k["by"]],
        "eliminated_cards": k["victim_cards"],
        "winner_cards": {n(p): c for p, c in k["by_cards"].items()},
        "board": k["board"],
        "winning_hand": k["winning_hand"],
    } for k in record.get("knockouts", [])]
    showdowns = [{
        "street_when_all_in": s["street"],
        "pot": s["pot"],
        "board": s["board"],
        "players": [{"name": n(p["pid"]), "cards": p["cards"], "chance_to_win_when_all_in": f"{round(p['equity'] * 100)}%", "won": p["won"]} for p in s["players"]],
        "suckout": s["suckout"],
    } for s in record.get("allin_showdowns", [])]
    showdowns.sort(key=lambda s: (not s["suckout"], -s["pot"]))

    stats = record.get("player_stats", {})
    most_kos = max(stats.items(), key=lambda kv: kv[1].get("knockouts", 0), default=(None, {}))
    loosest = max(((p, s) for p, s in stats.items() if s.get("hands", 0) >= 30), key=lambda kv: kv[1]["vpip"] / kv[1]["hands"], default=(None, {}))

    return {
        "date": record["date"],
        "players": record.get("players"),
        "prize_pool": payouts.get("pool"),
        "duration_minutes": record.get("duration_minutes"),
        "hands_played": record.get("hands_played"),
        "finish": placings,
        "winnings": {n(p): amt for p, amt in payouts.get("gross", {}).items() if amt > 0},
        "bounty": {
            "target_last_weeks_winner": n(bounty.get("target")),
            "target_played": bounty.get("target_played"),
            "worth": bounty.get("pot"),
            "collected_by": [n(p) for p in bounty.get("winners", [])],
            "rolls_over_to_next_week": bounty.get("carry_out", 0) > 0,
            "next_week_bounty_worth": round(bounty.get("carry_out", 0) + 10, 2),
            "next_week_target": placings.get("first"),
        },
        "knockouts_in_order": knockouts,
        "allin_showdowns": showdowns[:6],
        "biggest_pots": [{"pot": b["pot"], "won_by": [n(p) for p in b["winners"]], "with": b["description"], "board": b["board"],
                          "shown": {n(p): c for p, c in b["shown"].items()}} for b in record.get("biggest_pots", [])[:3]],
        "most_knockouts": {"name": n(most_kos[0]), "count": most_kos[1].get("knockouts")} if most_kos[0] else None,
        "played_the_most_hands_pct": {"name": n(loosest[0]), "pct": round(100 * loosest[1]["vpip"] / loosest[1]["hands"])} if loosest[0] else None,
        "season_top5": [{"rank": r["rank"], "name": n(r["player"]), "points": r["points"], "rank_last_week": before.get(r["player"])} for r in table[:5]],
    }


def template_recap(facts: dict) -> str:
    f = facts["finish"]
    lines = [f"Results for {facts['date']} ({facts['players']} players, ${facts['prize_pool']:g} pool):", ""]
    for key, label in [("first", "1st"), ("second", "2nd"), ("third", "3rd"), ("fourth", "4th")]:
        if f.get(key):
            won = facts["winnings"].get(f[key])
            lines.append(f"{label}: {f[key]}" + (f" (${won:g})" if won else ""))
    if f.get("bubble"):
        lines.append(f"Bubble: {f['bubble']}")
    if f.get("first_out"):
        lines.append(f"First out: {f['first_out']}")
    b = facts["bounty"]
    lines.append("")
    if b["collected_by"]:
        lines.append(f"Bounty: {' and '.join(b['collected_by'])} knocked out {b['target_last_weeks_winner']} for ${b['worth']:g}.")
    elif b["target_last_weeks_winner"]:
        lines.append(f"Bounty: nobody collected it, so it rolls over. Next week it's worth ${b['next_week_bounty_worth']:g}.")
    lines.append(f"Next week's bounty target: {b['next_week_target']}.")
    lines += ["", "Season standings:"] + [f"{r['rank']}. {r['name']} ({r['points']} pts)" for r in facts["season_top5"]]
    lines += ["", "- the Poker Bot"]
    return "\n".join(lines)


def _api_key() -> tuple[str | None, str]:
    """(key, where it came from). Placeholders and malformed values count as missing."""
    key, source = os.environ.get("ANTHROPIC_API_KEY"), "environment"
    if not key and os.environ.get("ANTHROPIC_API_KEY_SECRET_ARN"):
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["ANTHROPIC_API_KEY_SECRET_ARN"])["SecretString"]
        try:
            raw = json.loads(raw).get("api_key", "")
        except (ValueError, AttributeError):
            pass
        key, source = raw, "Secrets Manager"
    if not key and os.environ.get("ANTHROPIC_API_KEY_FILE"):
        path = Path(os.environ["ANTHROPIC_API_KEY_FILE"])
        key, source = (path.read_text().strip() if path.exists() else None), str(path)
    key = (key or "").strip()
    return (key, source) if key.startswith("sk-ant-") else (None, source)


def _user_message(facts: dict) -> list[dict]:
    return [{"role": "user", "content": "Here are this week's facts as JSON. Write the recap.\n\n" + json.dumps(facts, ensure_ascii=False, indent=1)}]


def _result(response, facts: dict) -> dict:
    if response.stop_reason == "refusal":
        return {"text": template_recap(facts), "source": "template", "reason": "model declined"}
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise ValueError("empty response")
    return {"text": text, "source": "claude", "model": response.model}


def _generate_anthropic(facts: dict, model: str) -> dict:
    import anthropic

    key, source = _api_key()
    if not key:
        return {"text": template_recap(facts), "source": "template", "reason": f"no Anthropic API key configured (checked {source})"}
    client = anthropic.Anthropic(api_key=key)
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=_user_message(facts),
            # If the model declines, the API retries on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        return {"text": template_recap(facts), "source": "template", "reason": "the Anthropic API key was rejected"}
    return _result(response, facts)


def _generate_bedrock(facts: dict, model: str, region: str | None) -> dict:
    import anthropic
    from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware

    if not region:
        return {"text": template_recap(facts), "source": "template", "reason": "no Bedrock region configured"}
    client = AnthropicBedrockMantle(aws_region=region, middleware=[BetaRefusalFallbackMiddleware([{"model": BEDROCK_FALLBACK_MODEL}])])
    unavailable = []
    # If the account hasn't been granted the main model, try the fallback model directly.
    for candidate in dict.fromkeys([model, BEDROCK_FALLBACK_MODEL]):
        try:
            response = client.beta.messages.create(
                model=candidate, max_tokens=4000, output_config={"effort": "low"},
                system=SYSTEM_PROMPT, messages=_user_message(facts),
            )
        except (anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            log.warning("model %s unavailable: %s", candidate, exc)
            unavailable.append(candidate)
            continue
        result = _result(response, facts)
        if unavailable:
            result["reason"] = f"not enabled for this account: {', '.join(unavailable)}"
        return result
    return {"text": template_recap(facts), "source": "template",
            "reason": f"no Claude model enabled on Bedrock for this account ({', '.join(unavailable)})"}


def generate_recap(facts: dict, model: str | None = None, provider: str | None = None, region: str | None = None) -> dict:
    """Ask Claude for the recap; fall back to the template on any failure."""
    provider = provider or os.environ.get("RECAP_PROVIDER", "anthropic")
    model = model or os.environ.get("RECAP_MODEL") or DEFAULT_MODELS[provider]
    try:
        if provider == "bedrock":
            return _generate_bedrock(facts, model, region or os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION"))
        return _generate_anthropic(facts, model)
    except Exception as exc:  # the weekly email must still go out
        log.exception("recap generation failed")
        return {"text": template_recap(facts), "source": "template", "reason": f"{type(exc).__name__}: {exc}"}
