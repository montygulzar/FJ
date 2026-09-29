"""/userinfo and the right-click "User Profile" app: a moderator's view of one person."""
import discord
from discord import app_commands
from discord.ext import commands

from config import MUTE_ROLE_ID
from cogs.appeals import FINAL_BAN_TYPES
from database import (
    get_blacklist_entry,
    get_case_counts_for_user,
    get_cases_for_user,
    get_latest_ban_case,
    get_temp_ban,
)
from embeds import (
    DANGER_COLOR,
    NEUTRAL_COLOR,
    WARNING_ICON,
    build_notice_embed,
    card,
    clamp,
    format_timestamp,
    style_for,
)
from guards import has_tier, is_blocked, is_protected, member_tier_index, tier_index, tier_label

NEW_ACCOUNT_DAYS = 7
MAX_ROLES_SHOWN = 10
RECENT_CASES_SHOWN = 3


async def build_profile_embed(guild: discord.Guild, user: discord.abc.User) -> discord.Embed:
    member = guild.get_member(user.id)
    blacklisted = await get_blacklist_entry(user.id)

    ban = None
    if member is None:
        try:
            ban = await guild.fetch_ban(user)
        except discord.HTTPException:
            ban = None
    temp_ban = await get_temp_ban(guild.id, user.id) if ban else None

    # Status lines, most serious first. The embed colour follows the worst one.
    status = []
    if blacklisted:
        status.append(f"\U0001F310 **Globally blacklisted** - {clamp(blacklisted['reason'], 150, empty='no reason')}")
    if ban is not None:
        latest = await get_latest_ban_case(guild.id, user.id)
        if latest is not None and latest["action_type"] in FINAL_BAN_TYPES:
            status.append("\u26D4 **Blacklisted here** - final, can't appeal")
        elif temp_ban:
            status.append(f"\u23F3 **Temp banned here** - ends {format_timestamp(temp_ban['unban_at'], 'R')}")
        else:
            status.append("\U0001F528 **Banned here** - permanent, can appeal")
    if member is not None:
        if member.is_timed_out():
            status.append(f"⏲️ **Timed out** until {discord.utils.format_dt(member.timed_out_until, 'R')}")
        if MUTE_ROLE_ID and any(role.id == MUTE_ROLE_ID for role in member.roles):
            status.append("\U0001F507 **Muted** (mute role)")
    if is_protected(user.id):
        status.append("\U0001F6E1\uFE0F **Protected** - can't be moderated by the bot")
    if not status:
        status.append("✅ No active punishments" if member is not None else "Not in this server")

    created = discord.utils.format_dt(user.created_at, "R")
    if (discord.utils.utcnow() - user.created_at).days < NEW_ACCOUNT_DAYS:
        created += f"  {WARNING_ICON} **new account**"

    roles_text = tier_text = None
    if member is not None:
        roles = [role for role in reversed(member.roles) if not role.is_default()]
        roles_text = " ".join(role.mention for role in roles[:MAX_ROLES_SHOWN]) or "*none*"
        if len(roles) > MAX_ROLES_SHOWN:
            roles_text += f" *+{len(roles) - MAX_ROLES_SHOWN} more*"
        staff_tier = member_tier_index(member)
        if staff_tier is not None:
            tier_text = f"\U0001F6E1\uFE0F {tier_label(staff_tier)}"

    counts = await get_case_counts_for_user(guild.id, user.id)
    record = "\u2728 Clean record"
    recent_lines = []
    if counts:
        record = f"**{sum(counts.values())}** case(s) - " + ", ".join(
            f"{style_for(action).icon} {total} {style_for(action).title.lower()}"
            for action, total in sorted(counts.items(), key=lambda item: -item[1])
        )
        for row in (await get_cases_for_user(guild.id, user.id))[:RECENT_CASES_SHOWN]:
            style = style_for(row["action_type"])
            recent_lines.append(
                f"`#{row['id']}` {style.icon} **{style.title}** - {clamp(row['reason'], 80)} "
                f"({format_timestamp(row['created_at'], 'R')})"
            )

    color = DANGER_COLOR if (blacklisted or ban) else (member.color if member and member.color.value else NEUTRAL_COLOR)
    embed = card(
        "\U0001F464  User Profile",
        color,
        user=user,
        body="\n".join(status),
        details=[
            ("User", f"{user.mention} (`{user.id}`)"),
            ("Account created", created),
            ("Joined", discord.utils.format_dt(member.joined_at, "R") if member and member.joined_at else "*not a member*"),
            ("Bot tier", tier_text),
            (f"Roles ({len(member.roles) - 1})" if member else "Roles", roles_text),
            ("Record", record),
            (None, "\n**Recent cases**\n" + "\n".join(recent_lines) if recent_lines else None),
        ],
        thumbnail=user.display_avatar.url,
        footer=f"ID: {user.id}  \u2022  {guild.name}",
    )
    return embed


class UserInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.profile_menu = app_commands.ContextMenu(name="User Profile", callback=self.profile_context)
        self.profile_menu.guild_only = True

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.profile_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.profile_menu.name, type=self.profile_menu.type)

    @commands.hybrid_command(name="userinfo", aliases=["whois"], description="A moderation profile of a user")
    @app_commands.describe(user="Who to look up (defaults to you)")
    @commands.guild_only()
    @has_tier("staff")
    async def userinfo(self, ctx: commands.Context, user: discord.User | None = None):
        await ctx.defer(ephemeral=True)
        await ctx.send(embed=await build_profile_embed(ctx.guild, user or ctx.author), ephemeral=True)

    async def profile_context(self, interaction: discord.Interaction, user: discord.User) -> None:
        # Context menus don't go through commands.check, so the tier is checked here.
        actual = member_tier_index(interaction.user)
        if (
            interaction.guild is None
            or is_blocked(interaction.user.id)
            or actual is None
            or actual < tier_index("staff")
        ):
            await interaction.response.send_message(
                embed=build_notice_embed(
                    "Only users who are part of the **FJUSA Staff Team+** can view profiles.", success=False
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = await build_profile_embed(interaction.guild, user)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInfo(bot))
