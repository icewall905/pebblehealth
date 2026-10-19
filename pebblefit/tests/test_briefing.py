import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "bridge"))
import briefing as b  # noqa: E402

FACTS = {"sleep": {"last_night_duration": "5h 41m", "debt": "2h 18m", "score": 42},
         "today": {"weight_kg": 112.4, "steps_avg": 5952, "time": "06:00"}}


def test_grounded_numbers_pass_and_invented_ones_fail():
    allowed = b._allowed(FACTS)
    assert b.ungrounded("You slept 5h 41m with a score of 42 and weigh 112.4 kg.", allowed) == []
    assert b.ungrounded("Steps averaged 5,952.", allowed) == [] or b.ungrounded("Steps averaged 5952.", allowed) == []
    assert b.ungrounded("Your weight is 112 kg.", allowed) == []          # rounding is fine
    assert b.ungrounded("Your HRV rose to 71 ms.", allowed) == ["71"]      # invented
    assert b.ungrounded("2 of 3 recordings arrived.", allowed) == []       # small counts are fine


def test_scrub_drops_only_ungrounded_items():
    allowed = b._allowed(FACTS)
    page = {"headline": "Short night", "summary": "You slept 5h 41m.", "focus": "Rest.",
            "highlights": [{"text": "Score 42.", "tone": "watch"}, {"text": "HRV 71 ms.", "tone": "good"}],
            "data_notes": ["Up 99 points"]}
    out = b._scrub({p: dict(page) for p in b.PAGES}, allowed)
    assert [h["text"] for h in out["sleep"]["highlights"]] == ["Score 42."]
    assert out["sleep"]["data_notes"] == [] and out["sleep"]["summary"] == "You slept 5h 41m."


def test_plain_string_highlights_are_accepted():
    out = b._normalise({"today": {"headline": "x", "highlights": ["a", {"text": "b", "tone": "loud"}]}})
    assert out["today"]["highlights"] == [{"text": "a", "tone": "neutral"}, {"text": "b", "tone": "neutral"}]
    assert out["sleep"]["highlights"] == []


def test_next_run_walks_through_the_daily_times(monkeypatch):
    tz = ZoneInfo("UTC")
    monkeypatch.setenv("BRIEFING_TIME", "05:00,05:40")
    t, first = b._next_run(datetime(2026, 10, 19, 4, 0, tzinfo=tz))
    assert (t.hour, t.minute, first) == (5, 0, True)
    t, first = b._next_run(datetime(2026, 10, 19, 5, 10, tzinfo=tz))
    assert (t.hour, t.minute, first) == (5, 40, False)
    t, first = b._next_run(datetime(2026, 10, 19, 6, 0, tzinfo=tz))
    assert (t.day, t.hour, first) == (20, 5, True)


def test_disabled_without_endpoint(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert b.run() == "disabled" and b.run_in_background() == "disabled"
