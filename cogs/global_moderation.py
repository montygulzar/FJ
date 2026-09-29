import logging
from datetime import timedelta
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.channel_moderation import lock_channel, resolve_lockdown_roles, unlock_channel
from durations import duration_autocomplete, duration_error, parse_duration
from reasons import reason_autocomplete
from config import APPROVED_GUILD_IDS, GLOBAL_ACTION_EXEMPT_GUILD_IDS
from database import (
    add_blacklist,
    get_blacklist_entry,
    get_guild_settings,
    get_locked_channel_ids,
    list_blacklist,
    remove_blacklist,
)
from embeds import (
    DANGER_COLOR,
    MUTED_COLOR,
    NEUTRAL_COLOR,
    SUCCESS_COLOR,
    audit_reason,
    base_embed,
    branded,
    build_ban_dm_embed,
    build_dm_notice_embed,
    build_notice_embed,
    build_summary_embed,
    clamp,
    format_duration,
    format_timestamp,
)
from guards import from_approved_guild, has_tier, is_protected
from modlog import _resolve_channel, post_to_log_channel, record_case, try_dm
from views import BanAppealView
from views import ConfirmView, build_confirm_prompt

MAX_TIMEOUT = timedelta(days=28)  # Discord's own cap on a timeout
BLACKLIST_PAGE_SIZE = 20

logger = logging.getLogger("modbot.global_moderation")

GuildAction = Callable[[discord.Guild, Optional[discord.Member]], Awaitable[None]]


def is_global_target(guild: discord.Guild) -> bool:
    """Whether global actions (and the blacklist) apply to this guild."""
    if APPROVED_GUILD_IDS and guild.id not in APPROVED_GUILD_IDS:
        return False
    return guild.id not in GLOBAL_ACTION_EXEMPT_GUILD_IDS


def target_guilds(bot: commands.Bot) -> list[discord.Guild]:
    """Servers a global action is allowed to touch.

    Falls back to every server if no allowlist is set. Guilds in
    GLOBAL_ACTION_EXEMPT_GUILD_IDS are always excluded — they receive no global
    actions so that (for example) a globally banned user can still access the
    Appeals server.
    """
    return [guild for guild in bot.guilds if is_global_target(guild)]


async def notify_user(user: discord.User, action_type: str, reason: str) -> None:
    await try_dm(user, build_dm_notice_embed(action_type, "all servers", reason))


async def notify_global_ban(user: discord.User, reason: str) -> None:
    """Send the ban DM with the appeal button for global bans."""
    await try_dm(user, build_ban_dm_embed(reason, is_global=True), BanAppealView())


async def refuse_protected(ctx: commands.Context, user: discord.User) -> bool:
    """Global actions bypass per-server role hierarchy entirely, so the protected list is the
    only thing standing between a rogue global moderator and banning an owner everywhere."""
    if not is_protected(user.id):
        return False
    await ctx.send(
        embed=build_notice_embed(f"**{user}** is on the protected list and can't be moderated.", success=False)
    )
    return True


async def request_confirmation(ctx: commands.Context, description: str) -> bool:
    view = ConfirmView(author_id=ctx.author.id)
    view.message = await ctx.send(embed=build_confirm_prompt(description), view=view)
    await view.wait()
    return bool(view.confirmed)


class GlobalModeration(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def apply_everywhere(
        self,
        ctx: commands.Context,
        user: discord.User,
        action_type: str,
        reason: str,
        perform: GuildAction,
        *,
        member_only: bool,
    ) -> None:
        """Run one action across every guild, recording a case per guild it succeeded in."""
        affected, failed = [], []

        for guild in target_guilds(self.bot):
            member = guild.get_member(user.id)
            if member_only and member is None:
                continue

            try:
                await perform(guild, member)
            except discord.NotFound:
                continue  # nothing to undo in this guild
            except (discord.Forbidden, discord.HTTPException):
                failed.append(guild.name)
                continue

            await record_case(guild, user, ctx.author, action_type, reason)
            affected.append(guild.name)

        await ctx.send(embed=build_summary_embed(action_type, user, affected, failed))

    @commands.hybrid_command(name="globalkick", description="Kick a user from every server the bot shares with them")
    @app_commands.describe(user="The user to kick everywhere", reason="Why they're being kicked")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalkick(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        if await refuse_protected(ctx, user):
            return
        if not await request_confirmation(ctx, f"Kick **{user}** from every server they share with this bot?"):
            await ctx.send(embed=build_notice_embed("Global kick cancelled.", success=False))
            return

        await notify_user(user, "global_kick", reason)
        reason_text = audit_reason(ctx.author, "Global kick", reason)

        await self.apply_everywhere(
            ctx, user, "global_kick", reason,
            lambda guild, member: member.kick(reason=reason_text),
            member_only=True,
        )

    @commands.hybrid_command(name="globalban", description="Ban a user from every server the bot is in")
    @app_commands.describe(user="The user to ban everywhere", reason="Why they're being banned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalban(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        if await refuse_protected(ctx, user):
            return
        if not await request_confirmation(ctx, f"Ban **{user}** from **every server** this bot is in?"):
            await ctx.send(embed=build_notice_embed("Global ban cancelled.", success=False))
            return

        await notify_global_ban(user, reason)
        reason_text = audit_reason(ctx.author, "Global ban", reason)
        # Blacklisted first, so a rejoin racing the bans below is still caught.
        await add_blacklist(user.id, ctx.author.id, reason)

        await self.apply_everywhere(
            ctx, user, "global_ban", reason,
            lambda guild, member: guild.ban(user, reason=reason_text, delete_message_seconds=0),
            member_only=False,
        )

    @commands.hybrid_command(name="globalunban", description="Unban a user from every server the bot is in")
    @app_commands.describe(user="The user to unban everywhere", reason="Why they're being unbanned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalunban(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        await ctx.defer()
        reason_text = audit_reason(ctx.author, "Global unban", reason)
        await remove_blacklist(user.id)

        await self.apply_everywhere(
            ctx, user, "global_unban", reason,
            lambda guild, member: guild.unban(user, reason=reason_text),
            member_only=False,
        )

    @commands.hybrid_command(name="globalmute", description="Timeout a user in every server the bot shares with them")
    @app_commands.describe(
        user="The user to mute everywhere",
        duration="How long, e.g. 30m, 2h, 1d, 1w - max 28d",
        reason="Why they're being muted",
    )
    @app_commands.autocomplete(duration=duration_autocomplete, reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalmute(
        self,
        ctx: commands.Context,
        user: discord.User,
        duration: str,
        *,
        reason: str = "No reason provided",
    ):
        if await refuse_protected(ctx, user):
            return
        problem = duration_error(duration, MAX_TIMEOUT)
        if problem:
            await ctx.send(embed=build_notice_embed(problem, success=False), ephemeral=True)
            return
        length = parse_duration(duration)
        if not await request_confirmation(
            ctx, f"Mute **{user}** for **{format_duration(length)}** in every shared server?"
        ):
            await ctx.send(embed=build_notice_embed("Global mute cancelled.", success=False))
            return

        until = discord.utils.utcnow() + length
        await try_dm(user, build_dm_notice_embed("global_mute", "all servers", reason, expires_at=until))
        reason_text = audit_reason(ctx.author, "Global mute", reason)

        await self.apply_everywhere(
            ctx, user, "global_mute", reason,
            lambda guild, member: member.timeout(until, reason=reason_text),
            member_only=True,
        )

    @commands.hybrid_command(name="globalunmute", description="Clear a user's timeout in every shared server")
    @app_commands.describe(user="The user to unmute everywhere", reason="Why they're being unmuted")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalunmute(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        await ctx.defer()
        reason_text = audit_reason(ctx.author, "Global unmute", reason)

        await self.apply_everywhere(
            ctx, user, "global_unmute", reason,
            lambda guild, member: member.timeout(None, reason=reason_text),
            member_only=True,
        )

    # --- Global blacklist -----------------------------------------------------

    async def _ban_blacklisted(self, guild: discord.Guild, user_id: int, reason: str | None) -> None:
        """Ban a blacklisted user who turned up in a guild covered by global actions."""
        if is_protected(user_id) or self.bot.user is None:
            return
        case_reason = f"Global blacklist: {reason or 'No reason provided'}"
        try:
            # Bans are keyed by ID, so this works whether or not they're in the server.
            await guild.ban(discord.Object(id=user_id), reason=clamp(case_reason, 512), delete_message_seconds=0)
        except discord.HTTPException as error:
            logger.warning("Could not ban blacklisted user %s in %s (%s): %s", user_id, guild.name, guild.id, error)
            return

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return  # Ban is in place; only the case-log entry is lost.
        await record_case(guild, user, self.bot.user, "global_ban", case_reason)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or not is_global_target(member.guild):
            return
        try:
            entry = await get_blacklist_entry(member.id)
        except Exception:
            logger.exception("Could not check the global blacklist for %s", member.id)
            return
        if entry is not None:
            await self._ban_blacklisted(member.guild, member.id, entry["reason"])

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Apply the whole blacklist to a newly joined server, so it starts out clean."""
        if not is_global_target(guild):
            return
        try:
            entries = await list_blacklist()
        except Exception:
            logger.exception("Could not load the global blacklist for new guild %s", guild.id)
            return
        for entry in entries:
            await self._ban_blacklisted(guild, entry["user_id"], entry["reason"])

    @commands.hybrid_group(
        name="globalblacklist",
        description="Manage users who are banned on sight in every server",
        fallback="list",
    )
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalblacklist(self, ctx: commands.Context, page: int = 1):
        entries = await list_blacklist()
        if not entries:
            await ctx.send(embed=build_notice_embed("The global blacklist is empty."))
            return

        last_page = max(1, (len(entries) + BLACKLIST_PAGE_SIZE - 1) // BLACKLIST_PAGE_SIZE)
        page = min(max(page, 1), last_page)
        start = (page - 1) * BLACKLIST_PAGE_SIZE
        lines = [
            f"<@{row['user_id']}> `{row['user_id']}` - {clamp(row['reason'], 80, empty='No reason')} "
            f"({format_timestamp(row['created_at'], 'R')})"
            for row in entries[start : start + BLACKLIST_PAGE_SIZE]
        ]
        embed = base_embed("Global Blacklist", DANGER_COLOR, clamp("\n".join(lines), 4096))
        branded(embed, footer_prefix=f"Page {page} of {last_page}  \u2022  {len(entries)} total")
        await ctx.send(embed=embed)

    @globalblacklist.command(name="add", description="Blacklist a user and ban them wherever they are now")
    @app_commands.describe(user="The user to blacklist", reason="Why they're being blacklisted")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalblacklist_add(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        if await refuse_protected(ctx, user):
            return
        if not await request_confirmation(
            ctx,
            f"Blacklist **{user}**? They'll be banned from every server they're in now "
            "and on sight if they join any other.",
        ):
            await ctx.send(embed=build_notice_embed("Blacklist cancelled.", success=False))
            return

        await add_blacklist(user.id, ctx.author.id, reason)
        reason_text = audit_reason(ctx.author, "Global blacklist", reason)
        await self.apply_everywhere(
            ctx, user, "global_ban", f"Global blacklist: {reason}",
            lambda guild, member: guild.ban(user, reason=reason_text, delete_message_seconds=0),
            member_only=True,
        )

    @globalblacklist.command(name="remove", description="Take a user off the blacklist (does not unban them)")
    @app_commands.describe(user="The user to remove from the blacklist")
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalblacklist_remove(self, ctx: commands.Context, user: discord.User):
        if not await remove_blacklist(user.id):
            await ctx.send(embed=build_notice_embed(f"**{user}** isn't on the global blacklist.", success=False))
            return
        await ctx.send(embed=build_notice_embed(
            f"**{user}** is off the global blacklist. Existing bans stay - use `/globalunban` to lift them."
        ))

    @globalblacklist.command(name="check", description="See whether a user is blacklisted, and why")
    @app_commands.describe(user="The user to look up")
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalblacklist_check(self, ctx: commands.Context, user: discord.User):
        entry = await get_blacklist_entry(user.id)
        if entry is None:
            await ctx.send(embed=build_notice_embed(f"**{user}** is not blacklisted."))
            return
        embed = base_embed("Blacklisted", DANGER_COLOR, f"**{user}**\n`{user.id}`")
        embed.add_field(name="Reason", value=clamp(entry["reason"]), inline=False)
        embed.add_field(name="Added by", value=f"<@{entry['moderator_id']}>", inline=True)
        embed.add_field(name="Added", value=format_timestamp(entry["created_at"], "R"), inline=True)
        await ctx.send(embed=embed)

    # --- Global announce and lockdown -------------------------------------------

    @commands.hybrid_command(name="globalannounce", description="Post an announcement in every server")
    @app_commands.describe(
        message="The announcement text",
        title="Optional heading for the announcement",
    )
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalannounce(self, ctx: commands.Context, *, message: str, title: str = "Announcement"):
        if not await request_confirmation(ctx, "Post this announcement in **every server**?\n\n" + clamp(message, 3500)):
            await ctx.send(embed=build_notice_embed("Announcement cancelled.", success=False))
            return

        announcement = base_embed(clamp(title, 256), NEUTRAL_COLOR, clamp(message, 4096))
        announcement.set_author(name=f"From {ctx.author}", icon_url=ctx.author.display_avatar.url)

        posted, failed = [], []
        for guild in target_guilds(self.bot):
            settings = await get_guild_settings(guild.id)
            channel_id = settings.get("announce_channel_id") or settings.get("log_channel_id")
            channel = await _resolve_channel(guild, channel_id) if channel_id else None
            if channel is None:
                failed.append(f"{guild.name} (no channel set)")
                continue
            try:
                await channel.send(embed=announcement)
                posted.append(guild.name)
            except discord.HTTPException:
                failed.append(f"{guild.name} (can't post in #{getattr(channel, 'name', channel_id)})")

        summary = base_embed("Global Announcement Sent", SUCCESS_COLOR if posted else DANGER_COLOR)
        summary.add_field(
            name=f"Posted in {len(posted)} server(s)",
            value=clamp("\n".join(f"- {name}" for name in posted), empty="*None*"),
            inline=False,
        )
        if failed:
            summary.add_field(
                name=f"Not posted ({len(failed)})",
                value=clamp("\n".join(f"- {name}" for name in failed)),
                inline=False,
            )
        await ctx.send(embed=summary)

    @commands.hybrid_command(name="globallockdown", description="Lock every text channel in every server")
    @app_commands.describe(reason="Why everything is being locked")
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globallockdown(self, ctx: commands.Context, *, reason: str = "No reason provided"):
        if not await request_confirmation(
            ctx, "Lock **every text channel in every server**? `/globalunlock` restores them exactly."
        ):
            await ctx.send(embed=build_notice_embed("Global lockdown cancelled.", success=False))
            return

        audit = audit_reason(ctx.author, "Global lockdown", reason)
        locked, failed = [], []
        for guild in target_guilds(self.bot):
            roles = await resolve_lockdown_roles(guild)
            already_locked = set(await get_locked_channel_ids(guild.id))
            channel_count, channel_failures = 0, 0
            for channel in guild.text_channels:
                # Re-locking would overwrite the saved state with "locked" and lose the original.
                if channel.id in already_locked:
                    continue
                # Skip channels nobody in the lockdown roles can post in anyway (rules, logs...).
                if not any(channel.permissions_for(role).send_messages for role in roles):
                    continue
                if not channel.permissions_for(guild.me).manage_roles:
                    channel_failures += 1
                    continue
                if await lock_channel(channel, roles, audit):
                    channel_failures += 1
                channel_count += 1

            if channel_count:
                locked.append(f"{guild.name} ({channel_count} channel(s))")
            if channel_failures:
                failed.append(f"{guild.name} ({channel_failures} channel(s))")

            log_embed = base_embed("Global Lockdown", MUTED_COLOR, f"{channel_count} channel(s) locked.")
            log_embed.add_field(name="Reason", value=clamp(reason), inline=False)
            log_embed.add_field(name="Locked by", value=f"{ctx.author} (`{ctx.author.id}`)", inline=True)
            await post_to_log_channel(guild, log_embed)

        summary = base_embed("Global Lockdown", MUTED_COLOR)
        summary.add_field(name="Locked", value=clamp("\n".join(f"- {n}" for n in locked), empty="*Nothing to lock*"), inline=False)
        if failed:
            summary.add_field(name="Missing permissions in", value=clamp("\n".join(f"- {n}" for n in failed)), inline=False)
        summary.add_field(name="Reason", value=clamp(reason), inline=False)
        await ctx.send(embed=summary)

    @commands.hybrid_command(name="globalunlock", description="Undo /globallockdown and every other channel lock")
    @commands.guild_only()
    @has_tier("gov")
    @from_approved_guild()
    async def globalunlock(self, ctx: commands.Context):
        await ctx.defer()
        unlocked, failed = [], []
        for guild in target_guilds(self.bot):
            channel_count, channel_failures = 0, 0
            for channel_id in await get_locked_channel_ids(guild.id):
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue  # Deleted since; its record stays harmlessly.
                result = await unlock_channel(channel)
                if result is None:
                    continue
                channel_count += 1
                if result[1]:
                    channel_failures += 1

            if channel_count:
                unlocked.append(f"{guild.name} ({channel_count} channel(s))")
                log_embed = base_embed("Global Unlock", SUCCESS_COLOR, f"{channel_count} channel(s) unlocked.")
                log_embed.add_field(name="Unlocked by", value=f"{ctx.author} (`{ctx.author.id}`)", inline=True)
                await post_to_log_channel(guild, log_embed)
            if channel_failures:
                failed.append(f"{guild.name} ({channel_failures} channel(s))")

        summary = base_embed("Global Unlock", SUCCESS_COLOR)
        summary.add_field(name="Unlocked", value=clamp("\n".join(f"- {n}" for n in unlocked), empty="*Nothing was locked*"), inline=False)
        if failed:
            summary.add_field(name="Couldn't fully restore", value=clamp("\n".join(f"- {n}" for n in failed)), inline=False)
        await ctx.send(embed=summary)


async def setup(bot: commands.Bot):
    await bot.add_cog(GlobalModeration(bot))
