"""Reason suggestions for moderation commands, configured with REASON_PRESETS."""
import discord
from discord import app_commands

from config import REASON_PRESETS


async def reason_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    typed = current.strip()
    choices = []
    if typed:
        # Whatever they've typed is always the first option, so free text still works.
        choices.append(app_commands.Choice(name=typed[:100], value=typed[:100]))
    for preset in REASON_PRESETS:
        if typed.lower() in preset.lower() and preset != typed:
            choices.append(app_commands.Choice(name=preset[:100], value=preset[:100]))
    return choices[:25]
