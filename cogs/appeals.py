"""Ban appeals submitted from the bot's DMs and decided by a staff vote.

Temporary (/tempban) and permanent (/ban) bans can be appealed: their DMs carry a
"Submit an appeal" button. Blacklist bans (/blacklist, /globalban, the global
blacklist) are final: their DMs get "Message Developer" instead, and the checks
below refuse an appeal even if an old button is pressed. Everything identifying the
appellant comes from the interaction itself (their real account, ID and age), never
from what they type, and several limits keep appeals from being spammed:

- the user must still be banned, and the ban must not be a blacklist ban
- one open appeal per user per server (enforced by a unique index)
- a cooldown after a denial (APPEAL_COOLDOWN_DAYS)

Appeals land in APPEALS_CHANNEL_ID with "Approve unban" / "Deny unban" buttons.
Members holding APPEAL_VOTER_ROLE_IDS (default: Staff Director+) vote; pressing the
same button again withdraws a vote, pressing the other switches it. Once at least
APPEAL_MIN_VOTES votes are in and one side leads, that side wins. An alert with a
jump link is posted in APPEAL_ALERT_CHANNEL_ID when an appeal arrives and when it
is decided.

The buttons are DynamicItems keyed by their custom_id, so they keep working after a
restart without the bot having to remember any open views.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from config import (
    APPEAL_ALERT_CHANNEL_ID,
    APPEAL_COOLDOWN_DAYS,
    APPEAL_MIN_VOTES,
    APPEAL_PING_VOTERS,
    APPEAL_TEAM_NAME,
    APPEAL_VOTER_ROLE_IDS,
    APPEALS_CHANNEL_ID,
    DEVELOPER_NAME,
    OWNER_IDS,
)
from database import (
    count_appeals,
    create_appeal,
    decide_appeal,
    delete_appeal,
    get_blacklist_entry,
    get_appeal,
    get_case_counts_for_user,
    get_last_denied_appeal,
    get_latest_ban_case,
    get_open_appeal,
    get_temp_ban,
    get_votes,
    cast_vote,
    remove_temp_ban,
    set_appeal_message,
)
from embeds import (
    DANGER_COLOR,
    NEUTRAL_COLOR,
    SUCCESS_COLOR,
    WARNING_ICON,
    branded,
    build_appeal_decision_dm,
    build_appeal_receipt_dm,
    build_notice_embed,
    clamp,
    format_timestamp,
    style_for,
)
from guards import is_blocked, member_tier_index, tier_index
from modlog import record_case_full, try_dm
from notify import rejoin_invite
from views import link_view

logger = logging.getLogger("modbot.appeals")

NEW_ACCOUNT_AGE = timedelta(days=7)


# Ban cases that can never be appealed through the bot.
FINAL_BAN_TYPES = frozenset({"blacklist", "global_ban"})


def is_final_ban(latest_ban_action: str | None, globally_blacklisted: bool) -> bool:
    """Blacklist bans (local or global) are final; temp and permanent bans can be appealed."""
    return globally_blacklisted or latest_ban_action in FINAL_BAN_TYPES


# Set on startup once the appeals channel has been checked. When it's missing or
# unusable, ban DMs fall back to the APPEAL_URL link instead of offering a button
# that can only fail.
_appeals_channel_ok: bool | None = None


def appeals_enabled() -> bool:
    return bool(APPEALS_CHANNEL_ID) and _appeals_channel_ok is not False


async def _fetch_channel(bot: commands.Bot, channel_id: int) -> discord.abc.GuildChannel | None:
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return channel if isinstance(channel, discord.abc.Messageable) else None


async def _appeals_channel(bot: commands.Bot):
    return await _fetch_channel(bot, APPEALS_CHANNEL_ID)


def tally(votes) -> tuple[list[int], list[int]]:
    """(approver IDs, denier IDs) from vote rows."""
    return [v["voter_id"] for v in votes if v["approve"]], [v["voter_id"] for v in votes if not v["approve"]]


def vote_outcome(approve: int, deny: int, minimum: int = APPEAL_MIN_VOTES) -> str | None:
    """'accepted' / 'denied' once enough votes are in and one side leads, else None."""
    if approve + deny < minimum or approve == deny:
        return None
    return "accepted" if approve > deny else "denied"


def can_vote(member: discord.abc.User) -> bool:
    if is_blocked(member.id):
        return False
    if member.id in OWNER_IDS:
        return True
    if APPEAL_VOTER_ROLE_IDS:
        return any(role.id in APPEAL_VOTER_ROLE_IDS for role in getattr(member, "roles", ()))
    actual = member_tier_index(member)
    return actual is not None and actual >= tier_index("staff_director")


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

    ban_case = await get_latest_ban_case(guild_id, user.id)
    if is_final_ban(ban_case["action_type"] if ban_case else None, await get_blacklist_entry(user.id) is not None):
        message = "\u26D4 You were **blacklisted**. Blacklist bans are final and can't be appealed."
        if DEVELOPER_NAME:
            message += f"\nIf you believe this was staff abuse, message the developer, **{DEVELOPER_NAME}**."
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
        name="Ban type",
        value=f"\u23F3 Temporary - ends {format_timestamp(unban_at, 'R')}" if unban_at else "\U0001F528 Permanent",
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


def add_vote_field(embed: discord.Embed, approvers: list[int], deniers: list[int]) -> discord.Embed:
    """Replace (or add) the live vote tally on the staff copy of an appeal."""
    for index, field in enumerate(embed.fields):
        if field.name.startswith("\U0001F5F3"):
            embed.remove_field(index)
            break
    total = len(approvers) + len(deniers)
    progress = (
        f"{total}/{APPEAL_MIN_VOTES} votes needed"
        if total < APPEAL_MIN_VOTES
        else "Tied - one more vote decides it" if len(approvers) == len(deniers) else "Decided"
    )

    def names(ids: list[int]) -> str:
        return ", ".join(f"<@{voter}>" for voter in ids) if ids else "*none*"

    embed.add_field(
        name="\U0001F5F3\uFE0F  Staff vote",
        value=clamp(
            f"\U0001F7E2 **Approve ({len(approvers)})**: {names(approvers)}\n"
            f"\U0001F534 **Deny ({len(deniers)})**: {names(deniers)}\n"
            f"*{progress} - majority wins*"
        ),
        inline=False,
    )
    return embed


def vote_view(appeal_id: int, approve: int, deny: int, *, disabled: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AppealVoteButton(appeal_id, "approve", approve, disabled=disabled))
    view.add_item(AppealVoteButton(appeal_id, "deny", deny, disabled=disabled))
    return view


def _stamp_decision(embed: discord.Embed, accepted: bool, approve: int, deny: int, note: str | None) -> discord.Embed:
    embed.color = SUCCESS_COLOR if accepted else DANGER_COLOR
    outcome = "\U0001F7E2 **Unban approved**" if accepted else "\U0001F534 **Unban denied**"
    value = f"{outcome} by staff vote ({approve}-{deny}) {discord.utils.format_dt(discord.utils.utcnow(), 'R')}"
    if note:
        value += f"\n{note}"
    embed.add_field(name="Decision", value=value, inline=False)
    return embed


async def post_appeal_alert(bot, appeal_id: int, user: discord.abc.User, guild, message: discord.Message) -> None:
    """'A ban appeal has been sent to #appeals' with a jump link, in the alert channel."""
    channel = await _fetch_channel(bot, APPEAL_ALERT_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(
        title=f"\U0001F4E8  New ban appeal #{appeal_id}",
        description=(
            f"A ban appeal has been sent to {message.channel.mention}.\n"
            f"**[Jump to the appeal]({message.jump_url})** to cast your vote."
        ),
        color=NEUTRAL_COLOR,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="User", value=f"{user.mention}\n`{user.id}`", inline=True)
    embed.add_field(name="Banned from", value=guild.name if guild else "Unknown", inline=True)
    embed.add_field(name="Votes needed", value=f"{APPEAL_MIN_VOTES}, majority wins", inline=True)

    content, mentions = None, discord.AllowedMentions.none()
    if APPEAL_PING_VOTERS and APPEAL_VOTER_ROLE_IDS and getattr(channel, "guild", None):
        roles = [channel.guild.get_role(role_id) for role_id in APPEAL_VOTER_ROLE_IDS]
        roles = [role for role in roles if role is not None]
        if roles:
            content = " ".join(role.mention for role in roles)
            mentions = discord.AllowedMentions(roles=roles, users=False, everyone=False)
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Go to appeal", style=discord.ButtonStyle.link, url=message.jump_url, emoji="\U0001F517"))
    try:
        await channel.send(content=content, embed=branded(embed), view=view, allowed_mentions=mentions)
    except discord.HTTPException as error:
        logger.warning("Could not post appeal alert for #%s: %s", appeal_id, error)


async def post_decision_alert(bot, appeal_id: int, accepted: bool, approve: int, deny: int, jump_url: str) -> None:
    channel = await _fetch_channel(bot, APPEAL_ALERT_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(
        title=("\U0001F7E2  Appeal #{0} approved" if accepted else "\U0001F534  Appeal #{0} denied").format(appeal_id),
        description=f"Staff vote finished **{approve}-{deny}**. [View the appeal]({jump_url})",
        color=SUCCESS_COLOR if accepted else DANGER_COLOR,
    )
    try:
        await channel.send(embed=branded(embed))
    except discord.HTTPException as error:
        logger.warning("Could not post decision alert for #%s: %s", appeal_id, error)


async def _resolve_user(bot, user_id: int):
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except discord.HTTPException:
            return None
    return user


async def carry_out(bot: commands.Bot, appeal, accepted: bool, approve: int, deny: int) -> str | None:
    """Act on a finished vote: unban or not, record it, DM the user. Returns a note for staff."""
    guild = bot.get_guild(appeal["guild_id"])
    user = await _resolve_user(bot, appeal["user_id"])
    tally_text = f"staff vote {approve}-{deny}"

    if not accepted:
        if user is not None:
            retry_at = (
                discord.utils.utcnow() + timedelta(days=APPEAL_COOLDOWN_DAYS) if APPEAL_COOLDOWN_DAYS else None
            )
            await try_dm(user, build_appeal_decision_dm(appeal["id"], guild, APPEAL_TEAM_NAME, False, retry_at=retry_at))
        return None

    if guild is None:
        return "The server is no longer available, so nobody was unbanned."

    reason = f"Appeal #{appeal['id']} approved ({tally_text})"
    note, unbanned = None, False
    try:
        await guild.unban(discord.Object(id=appeal["user_id"]), reason=clamp(reason, 512))
        unbanned = True
    except discord.NotFound:
        note, unbanned = "They were already unbanned.", True
    except discord.HTTPException as error:
        note = f"\u26A0\uFE0F Unban failed: `{error}` - unban them manually."
    if unbanned:
        # Only once the ban is really gone: dropping the record after a failed unban
        # would leave a tempban that never expires.
        await remove_temp_ban(guild.id, appeal["user_id"])

    if unbanned and user is not None:
        if note is None and bot.user is not None:
            await record_case_full(guild, user, bot.user, "unban", reason)
        invite = await rejoin_invite(guild, f"Rejoin invite after appeal #{appeal['id']} was approved")
        await try_dm(
            user,
            build_appeal_decision_dm(appeal["id"], guild, APPEAL_TEAM_NAME, True),
            link_view(f"Rejoin {guild.name}", invite, "\U0001F6AA"),
        )
    return note


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

        add_vote_field(embed, [], [])
        channel = await _appeals_channel(bot)
        message = None
        if channel is not None:
            try:
                message = await channel.send(embed=embed, view=vote_view(appeal_id, 0, 0))
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
        await post_appeal_alert(bot, appeal_id, user, guild, message)
        # A DM copy, since the ephemeral confirmation disappears.
        await try_dm(user, build_appeal_receipt_dm(appeal_id, guild, APPEAL_TEAM_NAME, self.answer.value))
        confirmation = build_notice_embed(
            f"Your appeal to **{guild.name}** has been sent to staff. "
            f"The **{APPEAL_TEAM_NAME}** will review your case, and I'll DM you the result.",
            title=f"Appeal #{appeal_id} submitted",
        )
        await interaction.followup.send(embed=confirmation, ephemeral=True)


class AppealButton(discord.ui.DynamicItem[discord.ui.Button], template=r"fjusa:appeal:(?P<guild_id>\d+)"):
    """The button in a tempban or ban DM. Its custom_id carries which server the ban is in."""

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


class AppealVoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fjusa:appealvote:(?P<appeal_id>\d+):(?P<side>approve|deny)",
):
    """Approve unban / Deny unban on the staff copy of an appeal."""

    def __init__(self, appeal_id: int, side: str, count: int = 0, *, disabled: bool = False):
        approve = side == "approve"
        super().__init__(
            discord.ui.Button(
                label=f"{'Approve unban' if approve else 'Deny unban'} ({count})",
                style=discord.ButtonStyle.secondary,
                emoji="\U0001F7E2" if approve else "\U0001F534",
                custom_id=f"fjusa:appealvote:{appeal_id}:{side}",
                disabled=disabled,
            )
        )
        self.appeal_id = appeal_id
        self.side = side

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["appeal_id"]), match["side"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not can_vote(interaction.user):
            who = "the appeal voter role" if APPEAL_VOTER_ROLE_IDS else "**Staff Director+**"
            await interaction.response.send_message(
                embed=build_notice_embed(f"Only members with {who} can vote on appeals.", success=False),
                ephemeral=True,
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        appeal = await get_appeal(self.appeal_id)
        if appeal is None or appeal["status"] != "pending":
            await interaction.response.send_message(
                embed=build_notice_embed("Voting on this appeal has closed.", success=False), ephemeral=True
            )
            return
        if interaction.user.id == appeal["user_id"]:
            await interaction.response.send_message(
                embed=build_notice_embed("You can't vote on your own appeal.", success=False), ephemeral=True
            )
            return

        # Blacklisted since they appealed: blacklist bans are final, so the vote closes.
        latest = await get_latest_ban_case(appeal["guild_id"], appeal["user_id"])
        if is_final_ban(latest["action_type"] if latest else None, await get_blacklist_entry(appeal["user_id"]) is not None):
            if await decide_appeal(self.appeal_id, "denied", interaction.user.id, "blacklisted after appealing"):
                approvers, deniers = tally(await get_votes(self.appeal_id))
                embed = _stamp_decision(
                    interaction.message.embeds[0], False, len(approvers), len(deniers),
                    "\u26D4 Closed automatically: they were **blacklisted** after appealing.",
                )
                await interaction.response.edit_message(
                    embed=embed, view=vote_view(self.appeal_id, len(approvers), len(deniers), disabled=True)
                )
            else:
                await interaction.response.send_message(
                    embed=build_notice_embed("Voting on this appeal has closed.", success=False), ephemeral=True
                )
            return

        approve = self.side == "approve"
        previous = {v["voter_id"]: v["approve"] for v in await get_votes(self.appeal_id)}.get(interaction.user.id)
        # Same button again withdraws the vote; the other button switches it.
        new_vote = None if previous is approve else approve
        await cast_vote(self.appeal_id, interaction.user.id, new_vote)

        approvers, deniers = tally(await get_votes(self.appeal_id))
        outcome = vote_outcome(len(approvers), len(deniers))
        embed = add_vote_field(interaction.message.embeds[0], approvers, deniers)

        if outcome is None:
            await interaction.response.edit_message(embed=embed, view=vote_view(self.appeal_id, len(approvers), len(deniers)))
            return

        # decide_appeal only succeeds once, so two final votes landing together
        # can't both carry out the decision.
        note = f"{len(approvers)}-{len(deniers)}"
        if not await decide_appeal(self.appeal_id, outcome, interaction.user.id, f"staff vote {note}"):
            await interaction.response.send_message(
                embed=build_notice_embed("Voting on this appeal has closed.", success=False), ephemeral=True
            )
            return
        await interaction.response.defer()
        accepted = outcome == "accepted"
        staff_note = await carry_out(interaction.client, appeal, accepted, len(approvers), len(deniers))
        embed = _stamp_decision(embed, accepted, len(approvers), len(deniers), staff_note)
        await interaction.message.edit(
            embed=embed, view=vote_view(self.appeal_id, len(approvers), len(deniers), disabled=True)
        )
        await post_decision_alert(
            interaction.client, self.appeal_id, accepted, len(approvers), len(deniers), interaction.message.jump_url
        )


class Appeals(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        global _appeals_channel_ok
        if not APPEALS_CHANNEL_ID:
            return
        channel = await _appeals_channel(self.bot)
        _appeals_channel_ok = channel is not None
        if channel is None:
            logger.warning(
                "APPEALS_CHANNEL_ID %s is not a channel I can see - appeal buttons are hidden until it is fixed",
                APPEALS_CHANNEL_ID,
            )
            return
        if APPEAL_VOTER_ROLE_IDS and not any(channel.guild.get_role(r) for r in APPEAL_VOTER_ROLE_IDS):
            logger.warning(
                "None of APPEAL_VOTER_ROLE_IDS exist in %s, where the appeals channel is - nobody but owners can vote",
                channel.guild.name,
            )
        if APPEAL_ALERT_CHANNEL_ID and await _fetch_channel(self.bot, APPEAL_ALERT_CHANNEL_ID) is None:
            logger.warning("APPEAL_ALERT_CHANNEL_ID %s is not a channel I can see", APPEAL_ALERT_CHANNEL_ID)


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(AppealButton, AppealVoteButton)
    await bot.add_cog(Appeals(bot))
