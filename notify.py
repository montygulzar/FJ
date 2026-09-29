"""DMs to the people moderation actions are taken against."""
from datetime import datetime, timedelta

import discord

from embeds import build_dm_notice_embed
from modlog import try_dm
from views import link_view

REJOIN_INVITE_MAX_AGE = 7 * 86400


async def resolve_user(bot, user_id: int) -> discord.User | None:
    """The user from cache, else from Discord. None if the account can't be fetched."""
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except discord.HTTPException:
            return None
    return user


async def create_invite(
    guild: discord.Guild, *, reason: str, max_age: int = 86400, max_uses: int = 1, unique: bool = True
) -> str | None:
    """An invite from the system channel or the first channel the bot may invite from.
    Returns None when the bot can't create one anywhere."""
    if guild.me is None:
        return None
    channels = ([guild.system_channel] if guild.system_channel else []) + list(guild.text_channels)
    for channel in channels:
        if not channel.permissions_for(guild.me).create_instant_invite:
            continue
        try:
            invite = await channel.create_invite(
                max_age=max_age, max_uses=max_uses, unique=unique, reason=reason[:512]
            )
            return invite.url
        except discord.HTTPException:
            continue
    return None


async def rejoin_invite(guild: discord.Guild, reason: str) -> str | None:
    """A single-use, 7-day invite so someone who was unbanned can find their way back."""
    return await create_invite(guild, reason=reason, max_age=REJOIN_INVITE_MAX_AGE)


async def dm_action(
    user: discord.abc.User,
    action_type: str,
    reason: str,
    *,
    guild: discord.Guild | None = None,
    location: str | None = None,
    case_id: int | None = None,
    duration: timedelta | None = None,
    expires_at: datetime | None = None,
    note: str | None = None,
    view: discord.ui.View | None = None,
) -> bool:
    """DM `user` about an action. Returns whether it was delivered (closed DMs are normal)."""
    if getattr(user, "bot", False):
        return False
    embed = build_dm_notice_embed(
        action_type,
        location or (guild.name if guild is not None else "all servers"),
        reason,
        guild=guild,
        expires_at=expires_at,
        duration=duration,
        case_id=case_id,
        note=note,
    )
    return await try_dm(user, embed, view)


async def dm_unban(user: discord.abc.User, guild: discord.Guild, reason: str, *, case_id: int | None = None,
                   action_type: str = "unban", note: str | None = None) -> bool:
    """Unban DM with a one-use "Rejoin" invite button when the bot can create one."""
    invite = await rejoin_invite(guild, f"Rejoin invite for {user} ({user.id}) after unban")
    view = link_view(f"Rejoin {guild.name}", invite, "\U0001F6AA")
    return await dm_action(user, action_type, reason, guild=guild, case_id=case_id, note=note, view=view)
