"""Ban appeals submitted from the bot's DMs and decided by staff with buttons.

Only temporary bans can be appealed here: a /tempban DM carries a "Submit an appeal"
button, while permanent bans (/ban, /globalban) never do. Everything identifying the
appellant comes from the interaction itself (their real account, ID and age), never
from what they type, and several limits keep appeals from being spammed:

- the user must still be banned, and the ban must still be temporary
- one open appeal per user per server (enforced by a unique index)
- a cooldown after a denial (APPEAL_COOLDOWN_DAYS)

The buttons are DynamicItems keyed by their custom_id, so they keep working after a
restart without the bot having to remember any open views.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from config import APPEAL_COOLDOWN_DAYS, APPEAL_URL, APPEALS_CHANNEL_ID, SERVER_DISPLAY_NAME
from database import (
    count_appeals,
    create_appeal,
    decide_appeal,
    delete_appeal,
    get_appeal,
    get_case_counts_for_user,
    get_last_denied_appeal,
    get_latest_ban_case,
    get_open_appeal,
    get_temp_ban,
    remove_temp_ban,
    set_appeal_message,
)
from embeds import (
    DANGER_COLOR,
    NEUTRAL_COLOR,
    SUCCESS_COLOR,
    WARNING_ICON,
    audit_reason,
    branded,
    build_notice_embed,
    clamp,
    format_timestamp,
    style_for,
)
from guards import is_blocked, member_tier_index, tier_index
from modlog import record_case, try_dm

logger = logging.getLogger("modbot.appeals")

NEW_ACCOUNT_AGE = timedelta(days=7)


def appeals_enabled() -> bool:
    return bool(APPEALS_CHANNEL_ID)


async def _appeals_channel(bot: commands.Bot) -> discord.abc.Messageable | None:
    channel = bot.get_channel(APPEALS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(APPEALS_CHANNEL_ID)
        except discord.HTTPException:
            return None
    return channel if isinstance(channel, discord.abc.Messageable) else None


async def appeal_blocker(bot: commands.Bot, guild_id: int, user: discord.abc.User) -> str | None:
    """Why this user can't appeal their ban in this guild right now, or None if they can."""
    if not appeals_enabled():
        return "Appeals through the bot are currently closed."

    guild = bot.get_guild(guild_id)
    if guild is None:
        return "That server is no longer available."

    try:
        await guild.fetch_ban(user)
    except discord.NotFound:
        return f"You're not banned from **{guild.name}** any more, so there's nothing to appeal."
    except discord.HTTPException:
        return "I couldn't check your ban right now. Please try again later."

    if await get_temp_ban(guild_id, user.id) is None:
        message = "This ban is permanent and can't be appealed through the bot."
        if APPEAL_URL:
            message += f"\nYou can still reach staff through the [{SERVER_DISPLAY_NAME} Appeals server]({APPEAL_URL})."
        return message

    open_appeal = await get_open_appeal(guild_id, user.id)
    if open_appeal is not None:
        return f"You already have an appeal waiting for review (**#{open_appeal['id']}**). Please be patient."

    denied = await get_last_denied_appeal(guild_id, user.id)
    if denied is not None and denied["decided_at"]:
        retry_at = datetime.fromisoformat(denied["decided_at"]) + timedelta(days=APPEAL_COOLDOWN_DAYS)
        if retry_at > datetime.now(timezone.utc):
            return (
                f"Your last appeal (**#{denied['id']}**) was denied. "
                f"You can appeal again {discord.utils.format_dt(retry_at, 'R')}."
            )
    return None


def build_appeal_embed(
    appeal_id: int,
    guild: discord.Guild,
    user: discord.abc.User,
    *,
    answer: str,
    extra: str | None,
    ban_case,
    unban_at: str | None,
    prior_appeals: int,
    case_counts: dict[str, int],
) -> discord.Embed:
    embed = discord.Embed(title=f"\U0001F4E8  Ban Appeal #{appeal_id}", color=NEUTRAL_COLOR)
    embed.set_thumbnail(url=user.display_avatar.url)

    # Identity comes from Discord, not the form, so it can't be faked.
    account_age = discord.utils.utcnow() - user.created_at
    created = f"{discord.utils.format_dt(user.created_at, 'D')} ({discord.utils.format_dt(user.created_at, 'R')})"
    if account_age < NEW_ACCOUNT_AGE:
        created += f"\n{WARNING_ICON} **New account**"
    embed.add_field(name="User", value=f"{user.mention}\n`{user}`", inline=True)
    embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
    embed.add_field(name="Server", value=f"{guild.name}\n`{guild.id}`", inline=True)
    embed.add_field(name="Account created", value=created, inline=True)
    embed.add_field(
        name="Record here",
        value=(
            f"{sum(case_counts.values())} case(s)\n"
            f"{prior_appeals} previous appeal(s)"
        ),
        inline=True,
    )
    embed.add_field(
        name="Ban ends",
        value=format_timestamp(unban_at, "R") if unban_at else "Unknown",
        inline=True,
    )
    if ban_case is not None:
        style = style_for(ban_case["action_type"])
        embed.add_field(
            name=f"Ban reason  •  Case #{ban_case['id']}",
            value=f"{style.icon} {clamp(ban_case['reason'], 900)}",
            inline=False,
        )
    embed.add_field(name="Why should the ban be lifted?", value=f">>> {clamp(answer, 1000)}", inline=False)
    if extra:
        embed.add_field(name="Anything else", value=f">>> {clamp(extra, 1000)}", inline=False)
    return branded(embed, footer_prefix=f"Appeal #{appeal_id}")


def _decision_view(appeal_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AppealDecisionButton(appeal_id, "accept"))
    view.add_item(AppealDecisionButton(appeal_id, "deny"))
    return view


def _stamp_decision(message: discord.Message, accepted: bool, moderator: discord.abc.User, note: str | None) -> discord.Embed:
    embed = message.embeds[0] if message.embeds else discord.Embed()
    embed.color = SUCCESS_COLOR if accepted else DANGER_COLOR
    outcome = "✅ Accepted" if accepted else "❌ Denied"
    value = f"{outcome} by {moderator.mention} {discord.utils.format_dt(discord.utils.utcnow(), 'R')}"
    if note:
        value += f"\n>>> {clamp(note, 800)}"
    embed.add_field(name="Decision", value=value, inline=False)
    return embed


class AppealModal(discord.ui.Modal, title="Ban Appeal"):
    answer = discord.ui.TextInput(
        label="Why should your ban be lifted?",
        style=discord.TextStyle.paragraph,
        min_length=30,
        max_length=1000,
        placeholder="Explain what happened and why you should be unbanned.",
    )
    extra = discord.ui.TextInput(
        label="Anything else staff should know? (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, guild_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        user = interaction.user
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Checked again: the ban could have changed while the form was open.
        problem = await appeal_blocker(bot, self.guild_id, user)
        if problem:
            await interaction.followup.send(embed=build_notice_embed(problem, success=False), ephemeral=True)
            return

        appeal_id = await create_appeal(self.guild_id, user.id, self.answer.value, self.extra.value or None)
        if appeal_id is None:
            await interaction.followup.send(
                embed=build_notice_embed("You already have an appeal waiting for review.", success=False),
                ephemeral=True,
            )
            return

        guild = bot.get_guild(self.guild_id)
        temp_ban = await get_temp_ban(self.guild_id, user.id)
        embed = build_appeal_embed(
            appeal_id, guild, user,
            answer=self.answer.value,
            extra=self.extra.value or None,
            ban_case=await get_latest_ban_case(self.guild_id, user.id),
            unban_at=temp_ban["unban_at"] if temp_ban else None,
            prior_appeals=await count_appeals(self.guild_id, user.id) - 1,
            case_counts=await get_case_counts_for_user(self.guild_id, user.id),
        )

        channel = await _appeals_channel(bot)
        message = None
        if channel is not None:
            try:
                message = await channel.send(embed=embed, view=_decision_view(appeal_id))
            except discord.HTTPException as error:
                logger.warning("Could not post appeal #%s to channel %s: %s", appeal_id, APPEALS_CHANNEL_ID, error)
        if message is None:
            await delete_appeal(appeal_id)
            await interaction.followup.send(
                embed=build_notice_embed(
                    "Your appeal couldn't be delivered to staff. Please try again later.", success=False
                ),
                ephemeral=True,
            )
            return

        await set_appeal_message(appeal_id, message.id)
        confirmation = build_notice_embed(
            f"Your appeal to **{guild.name}** has been sent to staff. "
            "You'll get a DM from me when they've made a decision.",
            title=f"Appeal #{appeal_id} submitted",
        )
        await interaction.followup.send(embed=confirmation, ephemeral=True)


class DenyModal(discord.ui.Modal, title="Deny Appeal"):
    note = discord.ui.TextInput(
        label="Reason (sent to the user)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, appeal_id: int, message: discord.Message):
        super().__init__(timeout=300)
        self.appeal_id = appeal_id
        self.message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        note = self.note.value or None
        if not await decide_appeal(self.appeal_id, "denied", interaction.user.id, note):
            await interaction.response.send_message(
                embed=build_notice_embed("Someone already decided this appeal.", success=False), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        await self.message.edit(embed=_stamp_decision(self.message, False, interaction.user, note), view=None)

        appeal = await get_appeal(self.appeal_id)
        guild = interaction.client.get_guild(appeal["guild_id"])
        retry_at = discord.utils.utcnow() + timedelta(days=APPEAL_COOLDOWN_DAYS)
        dm = discord.Embed(
            title="❌  Appeal denied",
            description=f"Your appeal (**#{self.appeal_id}**) to **{guild.name if guild else 'the server'}** was denied.",
            color=DANGER_COLOR,
        )
        if note:
            dm.add_field(name="Staff note", value=f">>> {clamp(note, 1000)}", inline=False)
        if APPEAL_COOLDOWN_DAYS:
            dm.add_field(name="Appeal again", value=discord.utils.format_dt(retry_at, "R"), inline=False)
        user = interaction.client.get_user(appeal["user_id"])
        if user is None:
            try:
                user = await interaction.client.fetch_user(appeal["user_id"])
            except discord.HTTPException:
                user = None
        if user is not None:
            await try_dm(user, branded(dm))
        await interaction.followup.send(embed=build_notice_embed(f"Appeal #{self.appeal_id} denied."), ephemeral=True)


class AppealButton(discord.ui.DynamicItem[discord.ui.Button], template=r"fjusa:appeal:(?P<guild_id>\d+)"):
    """The button in a tempban DM. Its custom_id carries which server the ban is in."""

    def __init__(self, guild_id: int):
        super().__init__(
            discord.ui.Button(
                label="Submit an appeal",
                style=discord.ButtonStyle.primary,
                emoji="\U0001F4DD",
                custom_id=f"fjusa:appeal:{guild_id}",
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        problem = await appeal_blocker(interaction.client, self.guild_id, interaction.user)
        if problem:
            await interaction.response.send_message(
                embed=build_notice_embed(problem, success=False), ephemeral=True
            )
            return
        await interaction.response.send_modal(AppealModal(self.guild_id))


class AppealDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fjusa:appealdecide:(?P<appeal_id>\d+):(?P<action>accept|deny)",
):
    """Accept/Deny on the staff copy of an appeal. Staff Director+ only."""

    def __init__(self, appeal_id: int, action: str):
        accept = action == "accept"
        super().__init__(
            discord.ui.Button(
                label="Accept" if accept else "Deny",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.danger,
                emoji="✅" if accept else "❌",
                custom_id=f"fjusa:appealdecide:{appeal_id}:{action}",
            )
        )
        self.appeal_id = appeal_id
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["appeal_id"]), match["action"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        actual = member_tier_index(interaction.user)
        if is_blocked(interaction.user.id) or actual is None or actual < tier_index("staff_director"):
            await interaction.response.send_message(
                embed=build_notice_embed(
                    "Only users who are **Staff Director+** can decide appeals.", success=False
                ),
                ephemeral=True,
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        appeal = await get_appeal(self.appeal_id)
        if appeal is None or appeal["status"] != "pending":
            await interaction.response.send_message(
                embed=build_notice_embed("This appeal has already been decided.", success=False), ephemeral=True
            )
            return

        if self.action == "deny":
            await interaction.response.send_modal(DenyModal(self.appeal_id, interaction.message))
            return

        if not await decide_appeal(self.appeal_id, "accepted", interaction.user.id, None):
            await interaction.response.send_message(
                embed=build_notice_embed("Someone already decided this appeal.", success=False), ephemeral=True
            )
            return
        await interaction.response.defer()
        await self._accept(interaction, appeal)

    async def _accept(self, interaction: discord.Interaction, appeal) -> None:
        bot = interaction.client
        moderator = interaction.user
        guild = bot.get_guild(appeal["guild_id"])
        user = bot.get_user(appeal["user_id"])
        if user is None:
            try:
                user = await bot.fetch_user(appeal["user_id"])
            except discord.HTTPException:
                user = discord.Object(id=appeal["user_id"])

        outcome = None
        if guild is None:
            outcome = "The server is no longer available, so nobody was unbanned."
        else:
            reason = f"Appeal #{self.appeal_id} accepted"
            try:
                await guild.unban(user, reason=audit_reason(moderator, "Appeal accepted", reason))
            except discord.NotFound:
                outcome = "They were already unbanned."
            except discord.HTTPException as error:
                outcome = f"Unban failed: `{error}` - unban them manually."
            await remove_temp_ban(guild.id, appeal["user_id"])
            if outcome is None and isinstance(user, discord.abc.User):
                await record_case(guild, user, moderator, "unban", reason)

        await interaction.message.edit(
            embed=_stamp_decision(interaction.message, True, moderator, outcome), view=None
        )

        if isinstance(user, discord.abc.User) and guild is not None:
            dm = discord.Embed(
                title="✅  Appeal accepted",
                description=f"Your appeal (**#{self.appeal_id}**) to **{guild.name}** was accepted. You've been unbanned.",
                color=SUCCESS_COLOR,
            )
            if guild.icon is not None:
                dm.set_thumbnail(url=guild.icon.url)
            await try_dm(user, branded(dm))

        await interaction.followup.send(
            embed=build_notice_embed(f"Appeal #{self.appeal_id} accepted." + (f"\n{outcome}" if outcome else "")),
            ephemeral=True,
        )


class Appeals(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if appeals_enabled() and await _appeals_channel(self.bot) is None:
            logger.warning(
                "APPEALS_CHANNEL_ID %s is not a channel I can see - appeals will fail to post",
                APPEALS_CHANNEL_ID,
            )


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(AppealButton, AppealDecisionButton)
    await bot.add_cog(Appeals(bot))
