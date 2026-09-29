"""Full server event logging - clean, categorised audit logging.

Covers: messages (delete/edit/bulk delete), members (join/leave/ban/unban/nickname/roles),
voice state (join/move/leave), channels (create/delete/edit), roles (create/delete/edit),
and invites (create/delete).

Each event goes to its kind's channel - Chat, Join, Member, Voice or Server Logs -
from the *_LOGS_CHANNEL_IDS env lists, falling back to /setserverlogchannel and then the
mod-log channel so a single-channel setup keeps working.
"""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from embeds import (
    DANGER_COLOR,
    MUTED_COLOR,
    NEUTRAL_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
    base_embed,
    branded,
)
from modlog import post_to_server_log_channel

# Log colours come from the shared palette so every log looks like part of one bot.
COLOR_JOIN     = SUCCESS_COLOR
COLOR_LEAVE    = MUTED_COLOR
COLOR_DELETE   = DANGER_COLOR
COLOR_EDIT     = WARNING_COLOR
COLOR_VOICE    = NEUTRAL_COLOR
COLOR_ROLE     = 0x8B5CF6
COLOR_CHANNEL  = 0x14B8A6
COLOR_BAN      = 0x991B1B
COLOR_UNBAN    = SUCCESS_COLOR
COLOR_INVITE   = 0x8B5CF6
COLOR_NICKNAME = WARNING_COLOR


def _base(title: str, color: int, description: str | None = None) -> discord.Embed:
    return base_embed(title, color, description)


def _author(embed: discord.Embed, user: discord.abc.User | discord.Member) -> discord.Embed:
    """Who the event is about, with their ID in the footer so it can be searched for."""
    embed.set_author(name=str(user), icon_url=user.display_avatar.url)
    return branded(embed, footer_prefix=f"User ID {user.id}")


def _short(text: str | None, limit: int = 1024) -> str:
    if not text or not text.strip():
        return "*empty*"
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + "..."


class ServerLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------------
    # Messages
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        embed = _base("\U0001F5D1\uFE0F  Message Deleted", COLOR_DELETE)
        _author(embed, message.author)
        embed.add_field(name="Author", value=f"{message.author.mention} `{message.author.id}`", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        if message.content:
            embed.add_field(name="Content", value=_short(message.content, 900), inline=False)
        if message.attachments:
            embed.add_field(
                name=f"Attachments ({len(message.attachments)})",
                value=_short("\n".join(f"`{a.filename}`" for a in message.attachments)),
                inline=False,
            )
        await post_to_server_log_channel(message.guild, embed, "chat")

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or messages[0].guild is None:
            return
        non_bot = [m for m in messages if not m.author.bot]
        embed = _base("\U0001F5D1\uFE0F  Bulk Message Delete", COLOR_DELETE)
        embed.add_field(name="Channel", value=messages[0].channel.mention, inline=True)
        embed.add_field(name="User messages removed", value=str(len(non_bot)), inline=True)
        embed.add_field(name="Total removed", value=str(len(messages)), inline=True)
        await post_to_server_log_channel(messages[0].guild, embed, "chat")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return

        embed = _base("\u270F\uFE0F  Message Edited", COLOR_EDIT)
        _author(embed, before.author)
        embed.add_field(name="Author", value=f"{before.author.mention} `{before.author.id}`", inline=True)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="Jump", value=f"[View message]({after.jump_url})", inline=True)
        embed.add_field(name="Before", value=_short(before.content, 512), inline=False)
        embed.add_field(name="After", value=_short(after.content, 512), inline=False)
        await post_to_server_log_channel(before.guild, embed, "chat")

    # -----------------------------------------------------------------------
    # Members
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        age = discord.utils.utcnow() - member.created_at
        age_days = age.days

        embed = _base("\U0001F49A  Member Joined", COLOR_JOIN)
        _author(embed, member)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(
            name="Account age",
            value=f"{discord.utils.format_dt(member.created_at, 'R')}\n({age_days}d old)",
            inline=True,
        )
        embed.add_field(name="Member #", value=str(member.guild.member_count), inline=True)
        if age_days < 7:
            embed.add_field(
                name="\u26A0\uFE0F  New Account Warning",
                value=f"This account is only **{age_days} day(s)** old.",
                inline=False,
            )
        await post_to_server_log_channel(member.guild, embed, "join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # A kick looks exactly like a leave from the gateway; only the audit log tells them apart.
        kicker, reason = await find_actor(member.guild, discord.AuditLogAction.kick, member.id)
        if kicker is not None:
            embed = _base("\U0001F462  Member Kicked", COLOR_BAN, f"{who(kicker)} kicked {who(member)}.")
        else:
            embed = _base("\U0001F44B  Member Left", COLOR_LEAVE, f"{who(member)} left the server.")
        _author(embed, member)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        if member.joined_at:
            embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R"), inline=True)
        add_actor(embed, kicker, reason, "Kicked by")
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        if roles:
            # Unclamped this overflows the 1024-char field limit on role-heavy members,
            # and Discord then rejects the entire embed - losing the log entry outright.
            embed.add_field(name="Roles", value=_short(", ".join(roles)), inline=False)
        await post_to_server_log_channel(member.guild, embed, "member" if kicker else "join")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        moderator, reason = await find_actor(guild, discord.AuditLogAction.ban, user.id)
        embed = _base("\U0001F528  Member Banned", COLOR_BAN, f"{who(moderator)} banned {who(user)}.")
        _author(embed, user)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user.mention}\n`{user.id}`", inline=True)
        add_actor(embed, moderator, reason, "Banned by")
        await post_to_server_log_channel(guild, embed, "member")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        moderator, reason = await find_actor(guild, discord.AuditLogAction.unban, user.id)
        embed = _base("\U0001F513  Member Unbanned", COLOR_UNBAN, f"{who(moderator)} unbanned {who(user)}.")
        _author(embed, user)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user.mention}\n`{user.id}`", inline=True)
        add_actor(embed, moderator, reason, "Unbanned by")
        await post_to_server_log_channel(guild, embed, "member")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = before.guild

        if before.nick != after.nick:
            changer, reason = await find_actor(guild, discord.AuditLogAction.member_update, after.id)
            if changer is None or changer.id == after.id:
                sentence = f"{who(after)} changed their nickname."
            else:
                sentence = f"{who(changer)} changed {who(after)}'s nickname."
            embed = _base("\U0001F3F7\uFE0F  Nickname Changed", COLOR_NICKNAME, sentence)
            _author(embed, after)
            embed.add_field(name="Before", value=before.nick or "*none*", inline=True)
            embed.add_field(name="After", value=after.nick or "*none*", inline=True)
            if changer is not None and changer.id != after.id:
                add_actor(embed, changer, reason, "Changed by")
            await post_to_server_log_channel(guild, embed, "member")

        added = [r for r in after.roles if r not in before.roles and r != guild.default_role]
        removed = [r for r in before.roles if r not in after.roles and r != guild.default_role]
        if added or removed:
            giver, reason = await find_actor(guild, discord.AuditLogAction.member_role_update, after.id)
            # "xe2b (ID 1195...) gave @Role to hf0u (ID 564...)"
            lines = []
            if added:
                lines.append(f"{who(giver)} gave {', '.join(r.mention for r in added)} to {who(after)}")
            if removed:
                lines.append(f"{who(giver)} removed {', '.join(r.mention for r in removed)} from {who(after)}")
            embed = _base("\U0001F3AD  Member Roles Updated", COLOR_ROLE, _short("\n".join(lines), 4000))
            _author(embed, after)
            embed.add_field(name="Recipient", value=f"{after.mention}\n`{after.id}`", inline=True)
            add_actor(embed, giver, reason, "Given by" if added and not removed else "Changed by")
            await post_to_server_log_channel(guild, embed, "member")

    # -----------------------------------------------------------------------
    # Voice
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return

        if before.channel is None:
            embed = _base("\U0001F50A  Joined Voice", COLOR_VOICE)
            embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
        elif after.channel is None:
            embed = _base("\U0001F507  Left Voice", COLOR_VOICE)
            embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
            embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        else:
            embed = _base("\U0001F500  Moved Voice Channel", COLOR_VOICE)
            embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
            embed.add_field(name="From", value=before.channel.mention, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)

        _author(embed, member)
        await post_to_server_log_channel(member.guild, embed, "voice")

    # -----------------------------------------------------------------------
    # Channels
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = _base("\u2795  Channel Created", COLOR_CHANNEL)
        embed.add_field(name="Name", value=channel.mention, inline=True)
        embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        if hasattr(channel, "category") and channel.category:
            embed.add_field(name="Category", value=channel.category.name, inline=True)
        actor, reason = await find_actor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        add_actor(embed, actor, reason)
        await post_to_server_log_channel(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = _base("\u2796  Channel Deleted", COLOR_DELETE)
        embed.add_field(name="Name", value=f"`#{channel.name}`", inline=True)
        embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        embed.add_field(name="ID", value=f"`{channel.id}`", inline=True)
        if hasattr(channel, "category") and channel.category:
            embed.add_field(name="Category", value=channel.category.name, inline=True)
        actor, reason = await find_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        add_actor(embed, actor, reason)
        await post_to_server_log_channel(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        changes: list[tuple[str, str, str]] = []
        if before.name != after.name:
            changes.append(("Name", f"`{before.name}`", f"`{after.name}`"))
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append(("Topic", _short(before.topic or "*none*", 200), _short(after.topic or "*none*", 200)))
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(("Slowmode", f"{before.slowmode_delay}s", f"{after.slowmode_delay}s"))
        if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
            changes.append(("NSFW", str(before.nsfw), str(after.nsfw)))
        if not changes:
            return

        embed = _base("\u270F\uFE0F  Channel Updated", COLOR_CHANNEL)
        embed.add_field(name="Channel", value=after.mention, inline=False)
        for name, old, new in changes:
            embed.add_field(name=name, value=f"{old} \u2192 {new}", inline=False)
        actor, reason = await find_actor(after.guild, discord.AuditLogAction.channel_update, after.id)
        add_actor(embed, actor, reason)
        await post_to_server_log_channel(after.guild, embed, "server")

    # -----------------------------------------------------------------------
    # Roles
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = _base("\U0001F3F7\uFE0F  Role Created", COLOR_ROLE)
        embed.add_field(name="Name", value=role.mention, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        actor, reason = await find_actor(role.guild, discord.AuditLogAction.role_create, role.id)
        add_actor(embed, actor, reason)
        await post_to_server_log_channel(role.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = _base("\U0001F3F7\uFE0F  Role Deleted", COLOR_DELETE)
        embed.add_field(name="Name", value=f"@{role.name}", inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        actor, reason = await find_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
        add_actor(embed, actor, reason)
        await post_to_server_log_channel(role.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: list[tuple[str, str, str]] = []
        if before.name != after.name:
            changes.append(("Name", before.name, after.name))
        if before.color != after.color:
            changes.append(("Color", str(before.color), str(after.color)))
        if before.hoist != after.hoist:
            changes.append(("Hoisted", str(before.hoist), str(after.hoist)))
        if before.mentionable != after.mentionable:
            changes.append(("Mentionable", str(before.mentionable), str(after.mentionable)))
        if not changes:
            return

        embed = _base("\U0001F3F7\uFE0F  Role Updated", COLOR_ROLE)
        embed.add_field(name="Role", value=after.mention, inline=False)
        for name, old, new in changes:
            embed.add_field(name=name, value=f"{old} \u2192 {new}", inline=False)
        actor, reason = await find_actor(after.guild, discord.AuditLogAction.role_update, after.id)
        add_actor(embed, actor, reason)
        await post_to_server_log_channel(after.guild, embed, "server")

    # -----------------------------------------------------------------------
    # Invites
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        embed = _base("\U0001F517  Invite Created", COLOR_INVITE)
        embed.add_field(name="Code", value=f"[{invite.code}]({invite.url})", inline=True)
        if invite.inviter:
            embed.add_field(name="Created by", value=f"{invite.inviter.mention}", inline=True)
        if invite.channel:
            embed.add_field(name="Channel", value=invite.channel.mention, inline=True)
        embed.add_field(name="Expires", value="Never" if invite.max_age == 0 else f"{invite.max_age // 3600}h", inline=True)
        embed.add_field(name="Max uses", value="Unlimited" if invite.max_uses == 0 else str(invite.max_uses), inline=True)
        await post_to_server_log_channel(invite.guild, embed, "server")

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        embed = _base("\U0001F517  Invite Deleted", COLOR_DELETE)
        embed.add_field(name="Code", value=invite.code, inline=True)
        if invite.channel:
            embed.add_field(name="Channel", value=invite.channel.mention, inline=True)
        await post_to_server_log_channel(invite.guild, embed, "server")


# ---------------------------------------------------------------------------
# Audit log helpers
# ---------------------------------------------------------------------------

# Discord writes the audit log a moment after the gateway event arrives, and an old
# entry for the same target must not be mistaken for this one.
AUDIT_LOG_DELAY = 1.5
AUDIT_LOG_MAX_AGE = 20


async def find_actor(
    guild: discord.Guild, action: discord.AuditLogAction, target_id: int
) -> tuple[discord.abc.User | None, str | None]:
    """Who just did `action` to `target_id`, and their reason - (None, None) if unknown."""
    if not guild.me or not guild.me.guild_permissions.view_audit_log:
        return None, None
    await asyncio.sleep(AUDIT_LOG_DELAY)
    try:
        async for entry in guild.audit_logs(limit=10, action=action):
            age = (discord.utils.utcnow() - entry.created_at).total_seconds()
            if age > AUDIT_LOG_MAX_AGE:
                break  # Newest first, so everything after this is older still.
            if entry.target is not None and entry.target.id == target_id:
                return entry.user, entry.reason
    except discord.HTTPException:
        pass
    return None, None


def who(user: discord.abc.User | None) -> str:
    """'**xe2b** (ID `1195...`)' - name plus ID, which survives renames."""
    return f"**{user}** (ID `{user.id}`)" if user is not None else "*Someone*"


def add_actor(embed: discord.Embed, actor: discord.abc.User | None, reason: str | None, label: str = "By") -> None:
    if actor is not None:
        embed.add_field(name=label, value=f"{actor.mention}\n`{actor.id}`", inline=True)
    if reason:
        embed.add_field(name="Reason", value=_short(reason, 500), inline=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerLogs(bot))
