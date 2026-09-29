"""Tests for duration parsing and reason suggestions."""
from datetime import timedelta

import pytest

from durations import duration_error, parse_duration
from embeds import format_duration


class TestParseDuration:
    @pytest.mark.parametrize("text,expected", [
        ("30m", timedelta(minutes=30)),
        ("2h", timedelta(hours=2)),
        ("1d12h", timedelta(days=1, hours=12)),
        ("1w", timedelta(weeks=1)),
        ("1d 2h 30m", timedelta(days=1, hours=2, minutes=30)),
        ("90", timedelta(minutes=90)),          # bare number = minutes
        ("2H", timedelta(hours=2)),
    ])
    def test_valid(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["", "abc", "10x", "h", "0m", "-5m", "1d banana"])
    def test_invalid(self, text):
        assert parse_duration(text) is None

    def test_errors(self):
        assert duration_error("2h", timedelta(days=28)) is None
        assert "isn't a duration" in duration_error("soon", timedelta(days=28))
        assert "too long" in duration_error("5w", timedelta(days=28))
        assert "shortest" in duration_error("30s", timedelta(days=28))


def test_format_duration():
    assert format_duration(timedelta(days=1, hours=2, minutes=5)) == "1 day, 2 hours"
    assert format_duration(timedelta(weeks=2)) == "2 weeks"
    assert format_duration(timedelta(seconds=20)) == "less than a minute"


@pytest.mark.asyncio
async def test_reason_autocomplete_keeps_free_text_first():
    from reasons import reason_autocomplete

    choices = await reason_autocomplete(None, "spam")
    assert choices[0].value == "spam"
    assert any(choice.value == "Spamming" for choice in choices)

    all_choices = await reason_autocomplete(None, "")
    assert 0 < len(all_choices) <= 25
