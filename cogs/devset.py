"""!devset - change non-secret settings from Discord. Owners only, prefix only.

    !devset                              show every setting and where it comes from
    !devset MUTE_ROLE_ID @Muted          set a value (mentions or IDs both work)
    !devset STAFF_ROLE_IDS add @Staff    add to / remove from an ID list
    !devset STAFF_ROLE_IDS remove @Staff
    !devset APPEAL_URL none              clear a value
    !devset MUTE_ROLE_ID reset           back to the .env value

It's deliberately not a slash command: it doesn't show in anyone's command list, and
only OWNER_IDS can run it. Secrets (token, database) and OWNER_IDS stay .env-only.
"""
from discord.ext import commands

import database
import runtime_config
from config import OWNER_IDS
from embeds import add_detail, NEUTRAL_COLOR, SUCCESS_COLOR, base_embed, branded, build_notice_embed, clamp
from modlog import post_to_log_channel
from runtime_config import SETTINGS, SettingError

GROUP_ICONS = {
    "Branding": "\U0001F3A8", "Tiers": "\U0001F6E1️", "Moderation": "\U0001F528",
    "Appeals": "⚖️", "Logs": "\U0001F4DC", "Server control": "\U0001F310",
}
USAGE = (
    "`!devset` - show every setting\n"
    "`!devset KEY value` - set it (mentions or IDs work)\n"
    "`!devset KEY add|remove value` - edit an ID list\n"
    "`!devset KEY none` - clear it\n"
    "`!devset KEY reset` - back to the .env value"
)


def owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in OWNER_IDS:
            return True
        raise commands.CheckFailure("Can't use this shit lil boi, bot dev only.")

    # Tells /syscheck this command is protected, and keeps it out of /help.
    predicate.fjusa_owner_only = True
    return commands.check(predicate)


class DevSet(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _overridden(self) -> set[str]:
        return {row["key"] for row in await database.get_bot_settings()}

    async def _show_all(self, ctx: commands.Context) -> None:
        overridden = await self._overridden()
        embed = base_embed(
            "\U0001F6E0️  Bot Settings", NEUTRAL_COLOR,
            "✏️ = set from Discord (overrides .env)\n\n" + USAGE,
        )
        groups: dict[str, list[str]] = {}
        for setting in SETTINGS.values():
            mark = " \u270F\uFE0F" if setting.key in overridden else ""
            groups.setdefault(setting.group, []).append(
                f"`{setting.key}`{mark} - {runtime_config.display(setting)}"
            )
        sections = [
            f"**{GROUP_ICONS.get(group, '')} {group}**\n" + "\n".join(lines) for group, lines in groups.items()
        ]
        embed.description = clamp(embed.description + "\n\n" + "\n\n".join(sections), 4096)
        await ctx.send(embed=branded(embed, footer_prefix="Secrets, database and OWNER_IDS stay in .env"))

    async def _save(self, ctx: commands.Context, setting, new_value, verb: str) -> None:
        before = runtime_config.display(setting)
        runtime_config.apply(setting, new_value)
        await database.set_bot_setting(setting.key, runtime_config.serialize(setting, new_value), ctx.author.id)
        await self._report(ctx, setting, before, verb)

    async def _report(self, ctx: commands.Context, setting, before: str, verb: str) -> None:
        after = runtime_config.display(setting)
        embed = base_embed(f"⚙️  {setting.key} {verb}", SUCCESS_COLOR, setting.help)
        add_detail(embed, "Before", clamp(before, 1024))
        add_detail(embed, "After", clamp(after, 1024))
        add_detail(embed, "Changed by", f"{ctx.author.mention} (`{ctx.author.id}`)")
        add_detail(embed, "Applies", "Now - and saved for restarts")
        await ctx.send(embed=embed)
        if ctx.guild is not None:
            await post_to_log_channel(ctx.guild, embed)  # an audit trail in Mod Logs

    @commands.command(name="devset", help="Change bot settings from Discord (owners only)")
    @owner_only()
    async def devset(self, ctx: commands.Context, key: str = None, *, value: str = None):
        if key is None or key.lower() in ("help", "list", "show"):
            await self._show_all(ctx)
            return

        if key.lower() == "reset" and value:  # "!devset reset KEY" also works
            key, value = value.split()[0], "reset"

        setting = runtime_config.lookup(key)
        if setting is None:
            await ctx.send(embed=build_notice_embed(
                f"`{key}` isn't a setting I can change from Discord. Run `!devset` to see them all."
                "\nSecrets (token, database) and OWNER_IDS stay in .env on purpose.",
                success=False,
            ))
            return
        if value is None:
            embed = base_embed(f"⚙️  {setting.key}", NEUTRAL_COLOR, setting.help)
            add_detail(embed, "Current", runtime_config.display(setting))
            add_detail(embed, "Change it", USAGE.replace("KEY", setting.key))
            await ctx.send(embed=embed)
            return

        try:
            if value.strip().lower() == "reset":
                before = runtime_config.display(setting)
                runtime_config.apply(setting, runtime_config.env_default(setting))
                await database.delete_bot_setting(setting.key)
                await self._report(ctx, setting, before, "reset to .env")
                return

            words = value.split(maxsplit=1)
            if setting.kind == "ids" and words[0].lower() in ("add", "remove") and len(words) == 2:
                change = runtime_config.parse(setting, words[1])
                current = set(setting.current())
                new_value = current | change if words[0].lower() == "add" else current - change
                await self._save(ctx, setting, new_value, "updated")
                return

            await self._save(ctx, setting, runtime_config.parse(setting, value), "updated")
        except SettingError as error:
            await ctx.send(embed=build_notice_embed(f"`{setting.key}`: {error}", success=False))


async def setup(bot: commands.Bot):
    await bot.add_cog(DevSet(bot))
