from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import APPROVED_GUILD_IDS
from embeds import NEUTRAL_COLOR, audit_reason, base_embed, branded, build_notice_embed, clamp, set_brand_icon
from guards import has_tier
from notify import create_invite

GUILDS_PER_EMBED = 10
LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "fjusa-logo.png"


async def resolve_invite(guild: discord.Guild) -> str | None:
    """A reusable 24h invite, so repeated /servers calls don't litter the server with new ones."""
    return await create_invite(guild, reason="Requested by bot owner", max_uses=0, unique=False)


class Owner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="servers", description="List every server this bot is in, with invites")
    @has_tier("dev")
    async def servers(self, ctx: commands.Context):
        await ctx.defer()

        if not self.bot.guilds:
            await ctx.send(embed=build_notice_embed("This bot isn't in any servers.", success=False))
            return

        guilds = sorted(self.bot.guilds, key=lambda guild: guild.member_count or 0, reverse=True)
        total_members = sum(guild.member_count or 0 for guild in guilds)

        for chunk_start in range(0, len(guilds), GUILDS_PER_EMBED):
            chunk = guilds[chunk_start : chunk_start + GUILDS_PER_EMBED]

            embed = base_embed(
                f"\U0001F5A5\uFE0F  Servers ({len(guilds)})",
                NEUTRAL_COLOR,
                f"**{total_members:,}** members across all servers."
                if chunk_start == 0
                else None,
            )

            for guild in chunk:
                invite_url = await resolve_invite(guild)
                owner = guild.owner.mention if guild.owner else "Unknown"
                approved = not APPROVED_GUILD_IDS or guild.id in APPROVED_GUILD_IDS
                lines = [
                    f"`{guild.id}`",
                    f"Status: **{'approved' if approved else 'NOT APPROVED'}**",
                    f"Members: **{guild.member_count or 0:,}**",
                    f"Owner: {owner}",
                    invite_url if invite_url else "*No channel I can create an invite in*",
                ]
                embed.add_field(name=clamp(guild.name, limit=256), value=clamp("\n".join(lines)), inline=False)

            branded(
                embed,
                footer_prefix=f"Page {chunk_start // GUILDS_PER_EMBED + 1} of "
                f"{(len(guilds) - 1) // GUILDS_PER_EMBED + 1}",
            )
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="addrole", description="Add a role to a member")
    @app_commands.describe(member="The member to give the role to", role="The role to add", reason="Why")
    @commands.guild_only()
    @has_tier("dev")
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(
        self,
        ctx: commands.Context,
        member: discord.Member,
        role: discord.Role,
        *,
        reason: str = "No reason provided",
    ):
        if role.is_default() or role.managed:
            await ctx.send(embed=build_notice_embed("That role is managed by Discord or an integration.", success=False))
            return
        if role >= ctx.guild.me.top_role:
            await ctx.send(embed=build_notice_embed("That role is at or above my highest role.", success=False))
            return
        if role in member.roles:
            await ctx.send(embed=build_notice_embed(f"{member.mention} already has that role.", success=False))
            return

        await member.add_roles(role, reason=audit_reason(ctx.author, "Add role", reason))
        await ctx.send(embed=build_notice_embed(f"Added **{role.name}** to {member.mention}."))


    @commands.hybrid_command(name="setlogo", description="Set the bot's avatar to the FJUSA logo")
    @has_tier("dev")
    async def setlogo(self, ctx: commands.Context):
        """The avatar doubles as the logo in every embed footer and corner (unless LOGO_URL is set)."""
        await ctx.defer(ephemeral=True)
        try:
            await self.bot.user.edit(avatar=LOGO_PATH.read_bytes())
        except FileNotFoundError:
            await ctx.send(embed=build_notice_embed(f"Couldn't find `{LOGO_PATH.name}` in assets/.", success=False))
            return
        except discord.HTTPException as error:
            # Discord rate-limits avatar changes to a couple per hour.
            await ctx.send(embed=build_notice_embed(f"Discord refused the avatar change: `{error}`", success=False))
            return

        set_brand_icon(self.bot.user.display_avatar.url)
        embed = build_notice_embed("The bot's avatar is now the FJUSA logo - it appears on every embed.", title="Logo Updated")
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
