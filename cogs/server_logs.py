"""Server event logging, laid out like Quark's logs.

Every entry reads the same way:

    [avatar] username
    ✏️ Message Edited
    <what happened, in plain words or the message itself>

    **Key**: value lines (who did it, where, reason)
    WARNING! block for dangerous role permissions
    ID: <user id>  ·  timestamp          [ User ID ] button

Covers messages (edit/delete/bulk delete), members (join/leave/kick/ban/unban,
nicknames, roles given and removed), voice, channels, roles and invites. Each goes to
its kind's channel from the *_LOGS_CHANNEL_IDS env lists (see modlog.post_log).
"""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from embeds import DANGER_COLOR, MUTED_COLOR, NEUTRAL_COLOR, SUCCESS_COLOR, WARNING_COLOR, clamp, logo_url
from modlog import post_to_server_log_channel

COLOR_ROLE = 0x8B5CF6
COLOR_CHANNEL = 0x14B8A6

# Granting any of these is flagged with a WARNING! block.
DANGEROUS_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
    "ban_members", "kick_members", "moderate_members", "manage_messages", "mention_everyone",
)

# Discord writes the audit log a moment after the gateway event arrives, and an old
# entry for the same target must not be mistaken for this one.
AUDIT_LOG_DELAY = 1.5
AUDIT_LOG_MAX_AGE = 20


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def _short(text: str | None, limit: int = 1024) -> str:
    return clamp(text, limit, empty="*empty*")


def ref(user: discord.abc.User | None) -> str | None:
    """'@name (`id`)' - the mention plus an ID that survives renames."""
    return f"{user.mention} (`{user.id}`)" if user is not None else None


def who(user: discord.abc.User | None) -> str:
    """'**xe2b** (ID `1195...`)' - used where a mention would be ambiguous."""
    return f"**{user}** (ID `{user.id}`)" if user is not None else "*Someone*"


def dangerous_permissions(permissions: discord.Permissions) -> list[str]:
    return [name.replace("_", " ").title() for name in DANGEROUS_PERMISSIONS if getattr(permissions, name, False)]


def log_embed(
    title: str,
    color: int,
    user: discord.abc.User | None = None,
    *,
    body: str | None = None,
    details: list[tuple[str | None, str | None]] = (),
    warning: str | None = None,
) -> discord.Embed:
    """A Quark-style log entry. `details` are ("Key", value) pairs rendered as
    **Key**: value lines; a None key renders the value as its own line; a None value
    skips the line."""
    parts = []
    if body:
        parts.append(body)
    lines = [f"**{key}**: {value}" if key else value for key, value in details if value]
    if lines:
        parts.append("\n".join(lines))
    if warning:
        parts.append(f"**WARNING!**\n```diff\n{warning}\n```")

    embed = discord.Embed(title=title, description=clamp("\n\n".join(parts), 4096, empty=None) or None, color=color)
    embed.timestamp = discord.utils.utcnow()
    if user is not None:
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_footer(text=f"ID: {user.id}", icon_url=logo_url())
    else:
        embed.set_footer(text="FJUSA Logs", icon_url=logo_url())
    return embed


class UserIdButton(discord.ui.DynamicItem[discord.ui.Button], template=r"fjusa:uid:(?P<user_id>\d+)"):
    """Replies with the bare ID, so it can be long-pressed and copied on mobile."""

    def __init__(self, user_id: int):
        super().__init__(
            discord.ui.Button(
                label="User ID", emoji="\U0001F194", style=discord.ButtonStyle.secondary,
                custom_id=f"fjusa:uid:{user_id}",
            )
        )
        self.user_id = user_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["user_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(str(self.user_id), ephemeral=True)


def id_view(user: discord.abc.User | None) -> discord.ui.View | None:
    if user is None:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(UserIdButton(user.id))
    return view


async def post(guild: discord.Guild, category: str, embed: discord.Embed, user: discord.abc.User | None) -> None:
    await post_to_server_log_channel(guild, embed, category, id_view(user))


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


def _roles_phrase(roles: list[discord.Role]) -> str:
    mentions = ", ".join(role.mention for role in roles)
    return f"The {mentions} role" if len(roles) == 1 else f"The {mentions} roles"


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------

class ServerLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- Messages ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        attachments = ", ".join(f"`{a.filename}`" for a in message.attachments)
        embed = log_embed(
            "\U0001F5D1️  Message Deleted", DANGER_COLOR, message.author,
            body=_short(message.content, 3000) if message.content else "*No text*",
            details=[
                ("Message author", message.author.mention),
                ("Channel", message.channel.mention),
                ("Attachments", _short(attachments, 500) if attachments else None),
            ],
        )
        await post(message.guild, "chat", embed, message.author)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or messages[0].guild is None:
            return
        authors = {m.author.id for m in messages if not m.author.bot}
        embed = log_embed(
            "\U0001F5D1️  Bulk Message Delete", DANGER_COLOR,
            body=f"**{len(messages)}** messages were deleted in {messages[0].channel.mention}.",
            details=[("From", f"{len(authors)} member(s)")],
        )
        await post(messages[0].guild, "chat", embed, None)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        embed = log_embed(
            "✏️  Message Edited", WARNING_COLOR, before.author,
            body=f"{_short(before.content, 1800)}\n\n**After edit**\n{_short(after.content, 1800)}",
            details=[
                ("Message author", before.author.mention),
                ("Channel", before.channel.mention),
                (None, f"**[Jump to message]({after.jump_url})**"),
            ],
        )
        await post(before.guild, "chat", embed, before.author)

    # --- Members ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        age_days = (discord.utils.utcnow() - member.created_at).days
        embed = log_embed(
            "\U0001F4E5  Member Joined", SUCCESS_COLOR, member,
            body=f"{member.mention} joined the server.",
            details=[
                ("Account created", discord.utils.format_dt(member.created_at, "R")),
                ("Member count", str(member.guild.member_count)),
            ],
            warning=f"- New account: only {age_days} day(s) old" if age_days < 7 else None,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await post(member.guild, "join", embed, member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # A kick looks exactly like a leave from the gateway; only the audit log tells them apart.
        kicker, reason = await find_actor(member.guild, discord.AuditLogAction.kick, member.id)
        roles = [r.mention for r in reversed(member.roles) if r != member.guild.default_role]
        details = [
            ("Kicked by", ref(kicker)),
            ("Reason", _short(reason, 500) if reason else None),
            ("Joined", discord.utils.format_dt(member.joined_at, "R") if member.joined_at else None),
            # Unclamped this overflows on role-heavy members, and Discord rejects the embed.
            ("Roles", _short(", ".join(roles), 1000) if roles else None),
        ]
        if kicker is not None:
            embed = log_embed("\U0001F462  Member Kicked", DANGER_COLOR, member,
                              body=f"{member.mention} was kicked from the server.", details=details)
        else:
            embed = log_embed("\U0001F4E4  Member Left", MUTED_COLOR, member,
                              body=f"{member.mention} left the server.", details=details)
        await post(member.guild, "member" if kicker else "join", embed, member)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        moderator, reason = await find_actor(guild, discord.AuditLogAction.ban, user.id)
        embed = log_embed(
            "\U0001F528  Member Banned", 0x991B1B, user,
            body=f"{user.mention} was banned.",
            details=[("Banned by", ref(moderator)), ("Reason", _short(reason, 1000) if reason else None)],
        )
        await post(guild, "member", embed, user)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        moderator, reason = await find_actor(guild, discord.AuditLogAction.unban, user.id)
        embed = log_embed(
            "\U0001F513  Member Unbanned", SUCCESS_COLOR, user,
            body=f"{user.mention} was unbanned.",
            details=[("Unbanned by", ref(moderator)), ("Reason", _short(reason, 1000) if reason else None)],
        )
        await post(guild, "member", embed, user)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = before.guild

        if before.nick != after.nick:
            changer, reason = await find_actor(guild, discord.AuditLogAction.member_update, after.id)
            by_someone_else = changer is not None and changer.id != after.id
            embed = log_embed(
                "\U0001F3F7️  Nickname Changed", WARNING_COLOR, after,
                body=f"{after.mention}'s nickname was changed." if by_someone_else
                else f"{after.mention} changed their nickname.",
                details=[
                    ("Before", _short(before.nick, 100) if before.nick else "*none*"),
                    ("After", _short(after.nick, 100) if after.nick else "*none*"),
                    ("Changed by", ref(changer) if by_someone_else else None),
                    ("Reason", _short(reason, 500) if reason and by_someone_else else None),
                ],
            )
            await post(guild, "member", embed, after)

        added = [r for r in after.roles if r not in before.roles and r != guild.default_role]
        removed = [r for r in before.roles if r not in after.roles and r != guild.default_role]
        if not added and not removed:
            return
        giver, reason = await find_actor(guild, discord.AuditLogAction.member_role_update, after.id)

        if added:
            dangerous = sorted({perm for role in added for perm in dangerous_permissions(role.permissions)})
            embed = log_embed(
                "\U0001F7E2  Role Given" if len(added) == 1 else "\U0001F7E2  Roles Given", SUCCESS_COLOR, after,
                body=f"{_roles_phrase(added)} {'was' if len(added) == 1 else 'were'} given to {after.mention}",
                details=[("Given by", ref(giver)), ("Reason", _short(reason, 500) if reason else None)],
                warning=f"- Dangerous permissions granted: {', '.join(dangerous)}" if dangerous else None,
            )
            await post(guild, "member", embed, after)

        if removed:
            embed = log_embed(
                "\U0001F534  Role Removed" if len(removed) == 1 else "\U0001F534  Roles Removed", DANGER_COLOR, after,
                body=f"{_roles_phrase(removed)} {'was' if len(removed) == 1 else 'were'} removed from {after.mention}",
                details=[("Removed by", ref(giver)), ("Reason", _short(reason, 500) if reason else None)],
            )
            await post(guild, "member", embed, after)

    # --- Voice -------------------------------------------------------------------

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
            title, body = "\U0001F50A  Joined Voice", f"{member.mention} joined {after.channel.mention}"
        elif after.channel is None:
            title, body = "\U0001F507  Left Voice", f"{member.mention} left {before.channel.mention}"
        else:
            title, body = "\U0001F500  Moved Voice", f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}"
        await post(member.guild, "voice", log_embed(title, NEUTRAL_COLOR, member, body=body), member)

    # --- Channels ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        actor, reason = await find_actor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        embed = log_embed(
            "➕  Channel Created", COLOR_CHANNEL, actor,
            body=f"{channel.mention} was created.",
            details=[
                ("Type", str(channel.type).replace("_", " ").title()),
                ("Category", getattr(getattr(channel, "category", None), "name", None)),
                ("Created by", ref(actor)),
                ("Reason", _short(reason, 500) if reason else None),
            ],
        )
        await post(channel.guild, "server", embed, actor)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        actor, reason = await find_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        embed = log_embed(
            "➖  Channel Deleted", DANGER_COLOR, actor,
            body=f"`#{channel.name}` was deleted.",
            details=[
                ("Type", str(channel.type).replace("_", " ").title()),
                ("Category", getattr(getattr(channel, "category", None), "name", None)),
                ("Channel ID", f"`{channel.id}`"),
                ("Deleted by", ref(actor)),
                ("Reason", _short(reason, 500) if reason else None),
            ],
        )
        await post(channel.guild, "server", embed, actor)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        changes: list[tuple[str, str]] = []
        if before.name != after.name:
            changes.append(("Name", f"`{before.name}` → `{after.name}`"))
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append(("Topic", f"{_short(before.topic or '*none*', 200)} → {_short(after.topic or '*none*', 200)}"))
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(("Slowmode", f"{before.slowmode_delay}s → {after.slowmode_delay}s"))
        if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
            changes.append(("NSFW", f"{before.nsfw} → {after.nsfw}"))
        if not changes:
            return
        actor, reason = await find_actor(after.guild, discord.AuditLogAction.channel_update, after.id)
        embed = log_embed(
            "✏️  Channel Updated", COLOR_CHANNEL, actor,
            body=f"{after.mention} was updated.",
            details=[*changes, ("Updated by", ref(actor)), ("Reason", _short(reason, 500) if reason else None)],
        )
        await post(after.guild, "server", embed, actor)

    # --- Roles ---------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        actor, reason = await find_actor(role.guild, discord.AuditLogAction.role_create, role.id)
        dangerous = dangerous_permissions(role.permissions)
        embed = log_embed(
            "\U0001F3F7️  Role Created", COLOR_ROLE, actor,
            body=f"{role.mention} was created.",
            details=[("Colour", str(role.color)), ("Role ID", f"`{role.id}`"),
                     ("Created by", ref(actor)), ("Reason", _short(reason, 500) if reason else None)],
            warning=f"- Dangerous permissions granted: {', '.join(dangerous)}" if dangerous else None,
        )
        await post(role.guild, "server", embed, actor)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        actor, reason = await find_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
        embed = log_embed(
            "\U0001F3F7️  Role Deleted", DANGER_COLOR, actor,
            body=f"`@{role.name}` was deleted.",
            details=[("Colour", str(role.color)), ("Role ID", f"`{role.id}`"),
                     ("Deleted by", ref(actor)), ("Reason", _short(reason, 500) if reason else None)],
        )
        await post(role.guild, "server", embed, actor)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: list[tuple[str, str]] = []
        if before.name != after.name:
            changes.append(("Name", f"{before.name} → {after.name}"))
        if before.color != after.color:
            changes.append(("Colour", f"{before.color} → {after.color}"))
        if before.hoist != after.hoist:
            changes.append(("Shown separately", f"{before.hoist} → {after.hoist}"))
        if before.mentionable != after.mentionable:
            changes.append(("Mentionable", f"{before.mentionable} → {after.mentionable}"))
        newly_dangerous = sorted(set(dangerous_permissions(after.permissions)) - set(dangerous_permissions(before.permissions)))
        if before.permissions != after.permissions:
            changes.append(("Permissions", "changed"))
        if not changes:
            return  # e.g. position shuffles, which fire for every role at once
        actor, reason = await find_actor(after.guild, discord.AuditLogAction.role_update, after.id)
        embed = log_embed(
            "\U0001F3F7️  Role Updated", COLOR_ROLE, actor,
            body=f"{after.mention} was updated.",
            details=[*changes, ("Updated by", ref(actor)), ("Reason", _short(reason, 500) if reason else None)],
            warning=f"- Dangerous permissions granted: {', '.join(newly_dangerous)}" if newly_dangerous else None,
        )
        await post(after.guild, "server", embed, actor)

    # --- Invites -------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        embed = log_embed(
            "\U0001F517  Invite Created", COLOR_ROLE, invite.inviter,
            body=f"Invite **[{invite.code}]({invite.url})** was created.",
            details=[
                ("Created by", ref(invite.inviter)),
                ("Channel", invite.channel.mention if invite.channel else None),
                ("Expires", "Never" if not invite.max_age else f"in {invite.max_age // 3600}h"),
                ("Max uses", "Unlimited" if not invite.max_uses else str(invite.max_uses)),
            ],
        )
        await post(invite.guild, "server", embed, invite.inviter)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        embed = log_embed(
            "\U0001F517  Invite Deleted", DANGER_COLOR,
            body=f"Invite **{invite.code}** was deleted.",
            details=[("Channel", invite.channel.mention if invite.channel else None)],
        )
        await post(invite.guild, "server", embed, None)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(UserIdButton)
    await bot.add_cog(ServerLogs(bot))
