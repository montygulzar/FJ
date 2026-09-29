from datetime import datetime, timezone

import discord
from discord.ext import commands

from database import get_guild_settings
from embeds import add_detail, WARNING_COLOR, base_embed
from modlog import post_to_server_log_channel


class RaidProtection(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await get_guild_settings(member.guild.id)
        minimum_age_hours = settings["raid_min_account_age_hours"]
        if not minimum_age_hours:
            return

        account_age = datetime.now(timezone.utc) - member.created_at
        if account_age.total_seconds() >= minimum_age_hours * 3600:
            return

        embed = base_embed(
            "\U0001F195  New Account Alert",
            WARNING_COLOR,
            f"{member.mention} joined with an account under **{minimum_age_hours}h** old.",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        add_detail(embed, "Account created", discord.utils.format_dt(member.created_at, "R"))
        add_detail(embed, "User ID", f"`{member.id}`")
        await post_to_server_log_channel(member.guild, embed, "security")


async def setup(bot: commands.Bot):
    await bot.add_cog(RaidProtection(bot))
