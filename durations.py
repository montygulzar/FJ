"""Human-friendly durations: "30m", "2h", "1d12h", "1w". A bare number means minutes."""
import re
from datetime import timedelta

import discord
from discord import app_commands

from embeds import format_duration

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_PART = re.compile(r"(\d+)\s*([smhdw])")
_WHOLE = re.compile(r"(?:\d+\s*[smhdw]\s*)+")

# Offered in the slash-command autocomplete.
SUGGESTIONS = (
    ("10 minutes", "10m"), ("30 minutes", "30m"), ("1 hour", "1h"), ("6 hours", "6h"),
    ("12 hours", "12h"), ("1 day", "1d"), ("3 days", "3d"), ("1 week", "1w"),
    ("2 weeks", "2w"), ("28 days", "28d"),
)


def parse_duration(text: str) -> timedelta | None:
    """Parse "1d2h30m" style text. Returns None if the text isn't a valid positive duration."""
    cleaned = text.strip().lower().replace(",", "").replace(" ", "")
    if not cleaned:
        return None
    if cleaned.isdigit():
        seconds = int(cleaned) * 60
    elif _WHOLE.fullmatch(cleaned):
        seconds = sum(int(amount) * _UNIT_SECONDS[unit] for amount, unit in _PART.findall(cleaned))
    else:
        return None
    return timedelta(seconds=seconds) if seconds > 0 else None


def duration_error(text: str, maximum: timedelta) -> str | None:
    """A reply explaining what's wrong with the duration, or None if it's usable."""
    delta = parse_duration(text)
    if delta is None:
        return f"`{text}` isn't a duration I understand. Try `30m`, `2h`, `1d12h` or `1w`."
    if delta < timedelta(minutes=1):
        return "The shortest duration is 1 minute."
    if delta > maximum:
        return f"That's too long - the most allowed here is **{format_duration(maximum)}**."
    return None


async def duration_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    typed = current.strip()
    if typed and parse_duration(typed) is not None:
        choices.append(app_commands.Choice(name=f"{format_duration(parse_duration(typed))} ({typed})", value=typed))
    for label, value in SUGGESTIONS:
        if not typed or typed.lower() in value or typed.lower() in label:
            choices.append(app_commands.Choice(name=label, value=value))
    return choices[:25]
