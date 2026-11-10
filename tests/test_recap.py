from types import SimpleNamespace

import pytest

from pokerleague import recap


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_FILE", "ANTHROPIC_API_KEY_SECRET_ARN", "RECAP_PROVIDER", "RECAP_MODEL"):
        monkeypatch.delenv(var, raising=False)


FACTS = {
    "date": "2026-11-03", "players": 12, "prize_pool": 120.0,
    "finish": {"first": "Doug B", "second": "Corey L", "third": "Paul W", "fourth": "Dave B", "bubble": "John P", "first_out": "Heather B"},
    "winnings": {"Doug B": 51.0, "Corey L": 41.0, "Paul W": 18.0, "Dave B": 10.0},
    "bounty": {"target_last_weeks_winner": "Suzanne B", "collected_by": ["Corey L"], "worth": 10.0,
               "next_week_bounty_worth": 10.0, "next_week_target": "Doug B"},
    "season_top5": [{"rank": 1, "name": "Corey L", "points": 42}],
}


def test_key_from_file_and_placeholder_rejected(tmp_path, monkeypatch):
    f = tmp_path / "key"
    f.write_text("REPLACE_ME\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(f))
    assert recap._api_key()[0] is None
    f.write_text("sk-ant-test123\n")
    assert recap._api_key()[0] == "sk-ant-test123"


def test_no_key_uses_template():
    result = recap.generate_recap(FACTS)
    assert result["source"] == "template" and "no Anthropic API key" in result["reason"]
    assert "Doug B ($51)" in result["text"]


def test_anthropic_call_shape(monkeypatch):
    calls = {}

    class FakeMessages:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(stop_reason="end_turn", model=kwargs["model"],
                                   content=[SimpleNamespace(type="text", text="Doug took it down.")])

    class FakeClient:
        def __init__(self, api_key):
            calls["api_key"] = api_key
            self.beta = SimpleNamespace(messages=FakeMessages())

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    result = recap.generate_recap(FACTS)
    assert result == {"text": "Doug took it down.", "source": "claude", "model": "claude-opus-5-5"}
    assert calls["fallbacks"] == "default" and calls["betas"] == ["server-side-fallback-2026-07-01"]
    assert "Doug B" in calls["messages"][0]["content"] and "@" not in calls["messages"][0]["content"]
