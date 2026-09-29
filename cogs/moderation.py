from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import MUTE_ROLE_ID
from database import add_temp_ban, get_guild_settings, get_warn_count, remove_temp_ban
from durations import duration_autocomplete, duration_error, parse_duration
from embeds import audit_reason, build_ban_dm_embed, build_dm_notice_embed, build_notice_embed, format_duration
from guards import has_tier, refusal_reason
from modlog import announce_case, record_case, try_dm
from reasons import reason_autocomplete
from views import BanAppealView

MAX_TIMEOUT = timedelta(days=28)  # Discord's own cap on a timeout
MAX_TEMPBAN = timedelta(days=365)

DURATION_HELP = "How long, e.g. 30m, 2h, 1d12h, 1w (a bare number means minutes)"


async def notify_member(
    member: discord.Member, action_type: str, reason: str, *, expires_at=None
) -> None:
    await try_dm(
        member,
        build_dm_notice_embed(action_type, member.guild.name, reason, guild=member.guild, expires_at=expires_at),
    )


async def perform_or_report(ctx: commands.Context, action_label: str, coroutine) -> bool:
    """Run a moderation action; on failure, tell the moderator plainly rather than letting a
    generic error surface after the member may already have been DMed that it succeeded."""
    try:
        await coroutine
        return True
    except discord.HTTPException as error:
        await ctx.send(
            embed=build_notice_embed(
                f"Sent the notice, but the {action_label} itself failed: `{error}`. "
                "Check my role position and permissions - nothing was recorded.",
                success=False,
            )
        )
        return False


async def refuse(ctx: commands.Context, message: str) -> None:
    await ctx.send(embed=build_notice_embed(message, success=False), ephemeral=True)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def escalate_if_needed(self, ctx: commands.Context, member: discord.Member, warn_count: int) -> None:
        settings = await get_guild_settings(ctx.guild.id)
        reason = f"Automatic action after reaching {warn_count} warns"

        if settings["warn_ban_threshold"] == warn_count:
            action_type = "ban"
        elif settings["warn_kick_threshold"] == warn_count:
            action_type = "kick"
        elif settings["warn_mute_threshold"] == warn_count:
            action_type = "tempmute"  # Escalation mutes are timeouts, not the mute role.
        else:
            return

        duration = None
        try:
            if action_type == "ban":
                await member.ban(reason=reason)
            elif action_type == "kick":
                await member.kick(reason=reason)
            else:
                duration = timedelta(minutes=max(1, settings["warn_mute_minutes"] or 60))
                await member.timeout(discord.utils.utcnow() + duration, reason=reason)
        except discord.HTTPException:
            await ctx.send(
                embed=build_notice_embed(
                    f"{member.mention} hit the warn threshold for an automatic {action_type}, "
                    "but I couldn't carry it out. Check my permissions and role position.",
                    success=False,
                )
            )
            return

        embed = await record_case(ctx.guild, member, self.bot.user, action_type, reason, duration=duration)
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
        await notify_member(member, "kick", reason)
        succeeded = await perform_or_report(
            ctx, "kick", member.kick(reason=audit_reason(ctx.author, "Kick", reason))
        )
        if succeeded:
            await announce_case(ctx, member, "kick", reason)

    @commands.hybrid_command(name="ban", description="Permanently ban a member from this server")
    @app_commands.describe(member="The member to ban", reason="Why they're being banned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff_director")
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        refusal = refusal_reason(ctx.author, member, self.bot.user.id)
        if refusal:
            await refuse(ctx, refusal)
            return

        await ctx.defer()
        # DM before removing them from the server. Permanent bans can't be appealed
        # through the bot, so this view never carries the in-DM appeal button.
        view = BanAppealView(guild_id=ctx.guild.id)
        await try_dm(
            member,
            build_ban_dm_embed(reason, guild=ctx.guild, can_appeal_here=view.has_appeal_button),
            view,
        )
        succeeded = await perform_or_report(
            ctx, "ban", member.ban(reason=audit_reason(ctx.author, "Ban", reason))
        )
        if succeeded:
            await announce_case(ctx, member, "ban", reason)

    @commands.hybrid_command(name="tempban", description="Ban a member and automatically unban them later")
    @app_commands.describe(member="The member to ban", duration=DURATION_HELP, reason="Why they're being banned")
    @app_commands.autocomplete(duration=duration_autocomplete, reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str = "No reason provided",
    ):
        refusal = refusal_reason(ctx.author, member, self.bot.user.id) or duration_error(duration, MAX_TEMPBAN)
        if refusal:
            await refuse(ctx, refusal)
            return

        await ctx.defer()
        length = parse_duration(duration)
        unban_at = discord.utils.utcnow() + length
        # Show the expiry time in the DM so they know exactly when the ban lifts.
        expiry_str = (
            f"{format_duration(length)} - ends {discord.utils.format_dt(unban_at, 'F')} "
            f"({discord.utils.format_dt(unban_at, 'R')})"
        )
        # Temporary bans are the only ones that can be appealed through the bot.
        view = BanAppealView(guild_id=ctx.guild.id, appealable=True)
        await try_dm(
            member,
            build_ban_dm_embed(
                reason, unban_at=expiry_str, guild=ctx.guild, can_appeal_here=view.has_appeal_button
            ),
            view,
        )

        succeeded = await perform_or_report(
            ctx, "ban", member.ban(reason=audit_reason(ctx.author, "Tempban", reason))
        )
        if not succeeded:
            return

        await add_temp_ban(ctx.guild.id, member.id, unban_at)
        embed = await record_case(
            ctx.guild, member, ctx.author, "tempban", reason, duration=length, expires_at=unban_at
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="unban", description="Unban a user from this server")
    @app_commands.describe(user="The user to unban", reason="Why they're being unbanned")
    @app_commands.autocomplete(reason=reason_autocomplete)
    @commands.guild_only()
    @has_tier("staff")
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
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
        await announce_case(ctx, user, "unban", reason)

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
        await notify_member(member, "warn", reason)
        embed = await record_case(ctx.guild, member, ctx.author, "warn", reason)

        warn_count = await get_warn_count(ctx.guild.id, member.id)
        embed.add_field(name="Total warnings", value=f"**{warn_count}**", inline=True)
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

        await notify_member(member, "mute", reason)
        embed = await record_case(ctx.guild, member, ctx.author, "mute", reason)
        embed.add_field(name="Duration", value="Until unmuted", inline=True)
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

        await notify_member(member, "tempmute", reason, expires_at=until)
        embed = await record_case(
            ctx.guild, member, ctx.author, "tempmute", reason, duration=length, expires_at=until
        )
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
        await notify_member(member, "unmute", reason)
        await announce_case(ctx, member, "unmute", reason)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
