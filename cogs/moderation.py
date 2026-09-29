from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import MUTE_ROLE_ID
from cogs.appeals import is_final_ban
from database import (
    add_temp_ban,
    get_blacklist_entry,
    get_guild_settings,
    get_latest_ban_case,
    get_warn_count,
    remove_temp_ban,
)
from durations import duration_autocomplete, duration_error, parse_duration
from embeds import add_detail, audit_reason, build_ban_dm_embed, build_notice_embed, format_duration
from guards import has_tier, member_tier_index, refusal_reason, tier_index
from modlog import record_case_full, try_dm
from notify import dm_action, dm_unban
from reasons import reason_autocomplete
from views import BLACKLIST_MEANING, BanAppealView, request_confirmation, safe_defer

MAX_TIMEOUT = timedelta(days=28)  # Discord's own cap on a timeout
MAX_TEMPBAN = timedelta(days=365)

DURATION_HELP = "How long, e.g. 30m, 2h, 1d12h, 1w (a bare number means minutes)"


async def perform_or_report(ctx: commands.Context, action_label: str, coroutine) -> bool:
    """Run a moderation action; on failure, tell the moderator plainly rather than letting a
    generic error surface after the member may already have been DMed that it succeeded."""
    try:
        await coroutine
        return True
    except discord.HTTPException as error:
        await ctx.send(
            embed=build_notice_embed(
                f"The {action_label} failed: `{error}`. "
                "Check my role position and permissions - nothing was recorded.",
                success=False,
            )
        )
        return False


async def refuse(ctx: commands.Context, message: str) -> None:
    await ctx.send(embed=build_notice_embed(message, success=False), ephemeral=True)


def dm_status(delivered: bool) -> str:
    return "\U0001F4E8 User notified by DM" if delivered else "\U0001F4ED Couldn't DM the user (DMs closed)"


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def is_blacklisted_here(self, guild: discord.Guild, user: discord.abc.User) -> bool:
        """Whether they're currently banned under a final (blacklist) ban in this server."""
        latest = await get_latest_ban_case(guild.id, user.id)
        if not is_final_ban(latest["action_type"] if latest else None, await get_blacklist_entry(user.id) is not None):
            return False
        try:
            await guild.fetch_ban(user)
        except discord.HTTPException:
            return False  # Not banned (any more) - nothing to protect.
        return True

    def target_member(self, guild: discord.Guild, user: discord.abc.User) -> discord.abc.User:
        """The Member object when they're in the server (so rank checks apply), else the User."""
        return guild.get_member(user.id) or user

    async def escalate_if_needed(self, ctx: commands.Context, member: discord.Member, warn_count: int) -> None:
        settings = await get_guild_settings(ctx.guild.id)
        reason = f"Automatic action after reaching {warn_count} warnings"

        if settings["warn_ban_threshold"] == warn_count:
            action_type = "ban"
        elif settings["warn_kick_threshold"] == warn_count:
            action_type = "kick"
        elif settings["warn_mute_threshold"] == warn_count:
            action_type = "tempmute"  # Escalation mutes are timeouts, not the mute role.
        else:
            return

        # /warn deliberately skips the rank check, but escalation kicks and bans. Without
        # this, Staff could warn a Staff Director until the bot removed them.
        if refusal_reason(ctx.author, member, self.bot.user.id):
            await ctx.send(
                embed=build_notice_embed(
                    f"{member.mention} reached {warn_count} warnings, but the automatic {action_type} was "
                    "skipped because they rank equal to or above you.",
                    success=False,
                )
            )
            return

        duration = until = None
        delivered = False
        try:
            if action_type == "ban":
                view = BanAppealView(ctx.guild.id, appealable=True)
                delivered = await try_dm(
                    member,
                    build_ban_dm_embed(reason, kind="ban", guild=ctx.guild, can_appeal_here=view.has_appeal_button),
                    view,
                )
                await member.ban(reason=reason)
            elif action_type == "kick":
                delivered = await dm_action(member, "kick", reason, guild=ctx.guild)
                await member.kick(reason=reason)
            else:
                duration = timedelta(minutes=max(1, settings["warn_mute_minutes"] or 60))
                until = discord.utils.utcnow() + duration
                await member.timeout(until, reason=reason)
        except discord.HTTPException:
            await ctx.send(
                embed=build_notice_embed(
                    f"{member.mention} hit the warn threshold for an automatic {action_type}, "
                    "but I couldn't carry it out. Check my permissions and role position.",
                    success=False,
                )
            )
            return

        embed, case_id = await record_case_full(
            ctx.guild, member, self.bot.user, action_type, reason, duration=duration, expires_at=until
        )
        if action_type == "tempmute":
            delivered = await dm_action(
                member, "tempmute", reason, guild=ctx.guild, case_id=case_id, duration=duration, expires_at=until
            )
        add_detail(embed, "Notification", dm_status(delivered))
        await ctx.send(embed=embed)

    async def _ban(
        self,
        ctx: commands.Context,
        user: discord.abc.User,
        reason: str,
        *,
        kind: str,
        length: timedelta | None = None,
    ) -> None:
        """Shared by /tempban, /ban and /blacklist. `kind` decides appealability:
        tempban and ban can be appealed; blacklist is final (Message Developer only)."""
        target = self.target_member(ctx.guild, user)
        refusal = refusal_reason(ctx.author, target, self.bot.user.id)
        if refusal:
            await refuse(ctx, refusal)
            return
        if kind != "blacklist" and await self.is_blacklisted_here(ctx.guild, user):
            # A ban or tempban on top would quietly make a final blacklist appealable,
            # or let it expire.
            await refuse(ctx, f"**{user}** is blacklisted here. A Gov+ member has to `/unban` them first.")
            return
        if kind == "blacklist" and not await request_confirmation(
            ctx, f"Blacklist **{user}** from **{ctx.guild.name}**?\n\n{BLACKLIST_MEANING}",
            title="Confirm Blacklist", note=None,
        ):
            await ctx.send(embed=build_notice_embed("Blacklist cancelled - nothing was done.", success=False))
            return

        await safe_defer(ctx)
        unban_at = discord.utils.utcnow() + length if length else None
        expiry_text = (
            f"{format_duration(length)} - ends {discord.utils.format_dt(unban_at, 'F')} "
            f"({discord.utils.format_dt(unban_at, 'R')})"
            if length else None
        )
        final = kind == "blacklist"
        view = BanAppealView(ctx.guild.id, appealable=not final, contact_developer=final)
        # DM first: once banned, they may share no server with the bot and DMs fail.
        delivered = await try_dm(
            user,
            build_ban_dm_embed(
                reason,
                kind=kind,
                guild=ctx.guild,
                unban_at=expiry_text,
                can_appeal_here=view.has_appeal_button,
                can_contact_developer=view.has_developer_button,
            ),
            view,
        )

        label = {"tempban": "Tempban", "ban": "Ban", "blacklist": "Blacklist"}[kind]
        succeeded = await perform_or_report(
            ctx, label.lower(), ctx.guild.ban(user, reason=audit_reason(ctx.author, label, reason))
        )
        if not succeeded:
            return

        if length:
            await add_temp_ban(ctx.guild.id, user.id, unban_at)
        else:
            # A permanent ban replaces any earlier tempban - otherwise the expiry loop
            # would lift the permanent ban when the old tempban ran out.
            await remove_temp_ban(ctx.guild.id, user.id)

        embed, _ = await record_case_full(
            ctx.guild, user, ctx.author, kind, reason, duration=length, expires_at=unban_at
        )
        add_detail(embed, "Appealable", "\U0001F534 No - final" if final else "\U0001F7E2 Yes")
        add_detail(embed, "Notification", dm_status(delivered))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="kick", description="Kick a member from this server")
    @app_commands.describe(member="The member to kick", reason="Why they're being kicked")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        refusal = refusal_reason(ctx.author, member, self.bot.user.id)
        if refusal:
            await refuse(ctx, refusal)
            return

        await ctx.defer()
        delivered = await dm_action(member, "kick", reason, guild=ctx.guild)
        succeeded = await perform_or_report(
            ctx, "kick", member.kick(reason=audit_reason(ctx.author, "Kick", reason))
        )
        if succeeded:
            embed, _ = await record_case_full(ctx.guild, member, ctx.author, "kick", reason)
            add_detail(embed, "Notification", dm_status(delivered))
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="ban", description="Permanent ban that they CAN appeal (use /blacklist for a final ban)")
    @app_commands.describe(user="The user to ban (doesn't need to be in the server)", reason="Why they're being banned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff_director")
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, user: discord.Member | discord.User, *, reason: str = "No reason provided"):
        await self._ban(ctx, user, reason, kind="ban")

    @commands.hybrid_command(
        name="blacklist", description="Final ban: permanent and can NEVER be appealed (use /ban if they may appeal)"
    )
    @app_commands.describe(
        user="Who to blacklist - a final, unappealable ban (they don't need to be in the server)",
        reason="Why they're being blacklisted - shown to them in their DM",
    )
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("gov")
    @commands.bot_has_permissions(ban_members=True)
    async def blacklist(self, ctx: commands.Context, user: discord.Member | discord.User, *, reason: str = "No reason provided"):
        await self._ban(ctx, user, reason, kind="blacklist")

    @commands.hybrid_command(name="tempban", description="Ban a user and automatically unban them later")
    @app_commands.describe(user="The user to ban (doesn't need to be in the server)", duration=DURATION_HELP, reason="Why they're being banned")
    @app_commands.autocomplete(duration=duration_autocomplete, reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx: commands.Context,
        user: discord.Member | discord.User,
        duration: str,
        *,
        reason: str = "No reason provided",
    ):
        problem = duration_error(duration, MAX_TEMPBAN)
        if problem:
            await refuse(ctx, problem)
            return
        await self._ban(ctx, user, reason, kind="tempban", length=parse_duration(duration))

    @commands.hybrid_command(name="unban", description="Unban a user from this server")
    @app_commands.describe(user="The user to unban", reason="Why they're being unbanned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
        globally_blacklisted = await get_blacklist_entry(user.id) is not None
        latest = await get_latest_ban_case(ctx.guild.id, user.id)
        is_final = is_final_ban(latest["action_type"] if latest else None, globally_blacklisted)
        actual = member_tier_index(ctx.author)
        if is_final and (actual is None or actual < tier_index("gov")):
            await refuse(ctx, f"**{user}** is under a blacklist ban - only **Gov+** can lift it.")
            return

        await ctx.defer()
        try:
            await ctx.guild.unban(user, reason=audit_reason(ctx.author, "Unban", reason))
        except discord.NotFound:
            await ctx.send(embed=build_notice_embed(f"**{user}** isn't banned here.", success=False))
            return
        except discord.HTTPException as error:
            await ctx.send(embed=build_notice_embed(f"Couldn't unban **{user}**: `{error}`", success=False))
            return

        await remove_temp_ban(ctx.guild.id, user.id)
        embed, case_id = await record_case_full(ctx.guild, user, ctx.author, "unban", reason)
        delivered = await dm_unban(user, ctx.guild, reason, case_id=case_id)
        add_detail(embed, "Notification", dm_status(delivered))
        if globally_blacklisted:
            add_detail(
                embed, "\u26A0\uFE0F Still globally blacklisted",
                "they'll be banned again if they rejoin - use `/globalunban` to lift it everywhere",
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="warn", description="Warn a member")
    @app_commands.describe(member="The member to warn", reason="Why they're being warned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        refusal = refusal_reason(ctx.author, member, self.bot.user.id, check_hierarchy=False)
        if refusal:
            await refuse(ctx, refusal)
            return

        await ctx.defer()
        embed, case_id = await record_case_full(ctx.guild, member, ctx.author, "warn", reason)
        warn_count = await get_warn_count(ctx.guild.id, member.id)
        delivered = await dm_action(
            member, "warn", reason, guild=ctx.guild, case_id=case_id,
            note=f"This is warning **#{warn_count}** on your record.",
        )
        add_detail(embed, "Total warnings", f"**{warn_count}**")
        add_detail(embed, "Notification", dm_status(delivered))
        await ctx.send(embed=embed)
        await self.escalate_if_needed(ctx, member, warn_count)

    @commands.hybrid_command(name="mute", description="Mute a member with the mute role until they are unmuted")
    @app_commands.describe(member="The member to mute", reason="Why they're being muted")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        refusal = refusal_reason(ctx.author, member, self.bot.user.id)
        if refusal:
            await refuse(ctx, refusal)
            return

        mute_role = ctx.guild.get_role(MUTE_ROLE_ID) if MUTE_ROLE_ID else None
        if mute_role is None:
            await refuse(
                ctx,
                "No mute role is set up for this server. Set `MUTE_ROLE_ID` in the bot's .env, "
                "or use `/tempmute` instead.",
            )
            return
        if mute_role in member.roles:
            await refuse(ctx, f"{member.mention} is already muted.")
            return

        await ctx.defer()
        succeeded = await perform_or_report(
            ctx, "mute", member.add_roles(mute_role, reason=audit_reason(ctx.author, "Mute", reason))
        )
        if not succeeded:
            return

        embed, case_id = await record_case_full(ctx.guild, member, ctx.author, "mute", reason)
        delivered = await dm_action(
            member, "mute", reason, guild=ctx.guild, case_id=case_id, note="This lasts until a staff member unmutes you."
        )
        add_detail(embed, "Duration", "Until unmuted")
        add_detail(embed, "Notification", dm_status(delivered))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="tempmute", description="Timeout a member for a set duration")
    @app_commands.describe(member="The member to mute", duration=DURATION_HELP + " - max 28d", reason="Why they're being muted")
    @app_commands.autocomplete(duration=duration_autocomplete, reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(moderate_members=True)
    async def tempmute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str = "No reason provided",
    ):
        refusal = refusal_reason(ctx.author, member, self.bot.user.id) or duration_error(duration, MAX_TIMEOUT)
        if refusal:
            await refuse(ctx, refusal)
            return

        await ctx.defer()
        length = parse_duration(duration)
        until = discord.utils.utcnow() + length
        succeeded = await perform_or_report(
            ctx, "mute", member.timeout(until, reason=audit_reason(ctx.author, "Tempmute", reason))
        )
        if not succeeded:
            return

        embed, case_id = await record_case_full(
            ctx.guild, member, ctx.author, "tempmute", reason, duration=length, expires_at=until
        )
        delivered = await dm_action(
            member, "tempmute", reason, guild=ctx.guild, case_id=case_id, duration=length, expires_at=until
        )
        add_detail(embed, "Notification", dm_status(delivered))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="unmute", description="Remove a member's mute role and any active timeout")
    @app_commands.describe(member="The member to unmute", reason="Why they're being unmuted")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(moderate_members=True, manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        mute_role = ctx.guild.get_role(MUTE_ROLE_ID) if MUTE_ROLE_ID else None
        has_mute_role = mute_role is not None and mute_role in member.roles
        is_timed_out = member.is_timed_out()
        if not has_mute_role and not is_timed_out:
            await refuse(ctx, f"{member.mention} isn't currently muted.")
            return

        await ctx.defer()
        audit = audit_reason(ctx.author, "Unmute", reason)
        try:
            if is_timed_out:
                await member.timeout(None, reason=audit)
            if has_mute_role:
                await member.remove_roles(mute_role, reason=audit)
        except discord.HTTPException as error:
            await ctx.send(
                embed=build_notice_embed(
                    f"Couldn't unmute {member.mention}: `{error}`. "
                    "Check my permissions and role position.",
                    success=False,
                )
            )
            return
        embed, case_id = await record_case_full(ctx.guild, member, ctx.author, "unmute", reason)
        delivered = await dm_action(member, "unmute", reason, guild=ctx.guild, case_id=case_id)
        add_detail(embed, "Notification", dm_status(delivered))
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
