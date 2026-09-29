import discord

import embeds as embeds_module
from config import APPEAL_URL, BRAND_NAME, DEVELOPER_ID, DEVELOPER_NAME
from embeds import NEUTRAL_COLOR, WARNING_COLOR, branded, build_case_line, build_notice_embed


DEVELOPER_URL = f"https://discord.com/users/{DEVELOPER_ID}" if DEVELOPER_ID else None


class BanAppealView(discord.ui.View):
    """Buttons attached to ban DMs.

    - "Submit an appeal" (in-Discord appeal) when `appealable` - temporary and
      permanent bans - and an appeals channel is configured.
    - "Appeals server" link when there's no in-Discord option but APPEAL_URL is set.
    - "Message Developer" for final (blacklist / global) bans, which can't be
      appealed, so someone who was abused by staff still has somewhere to go.
    """

    def __init__(
        self, guild_id: int | None = None, *, appealable: bool = False, contact_developer: bool = False
    ) -> None:
        # timeout=None means the buttons stay active indefinitely in the DM.
        super().__init__(timeout=None)
        from cogs.appeals import AppealButton, appeals_enabled

        self.has_appeal_button = bool(appealable and guild_id and appeals_enabled())
        self.has_developer_button = bool(contact_developer and DEVELOPER_URL)
        if self.has_appeal_button:
            self.add_item(AppealButton(guild_id))
        elif appealable and APPEAL_URL:
            self.add_item(
                discord.ui.Button(label="Appeals server", style=discord.ButtonStyle.link, url=APPEAL_URL, emoji="\U0001F4DD")
            )
        if self.has_developer_button:
            self.add_item(
                discord.ui.Button(
                    label=f"Message Developer ({DEVELOPER_NAME})" if DEVELOPER_NAME else "Message Developer",
                    style=discord.ButtonStyle.link,
                    url=DEVELOPER_URL,
                    emoji="\U0001F4AC",
                )
            )


def link_view(label: str, url: str | None, emoji: str | None = None) -> discord.ui.View | None:
    """A single link button, e.g. "Rejoin server" on an unban DM. None if there's no URL."""
    if not url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.link, url=url, emoji=emoji))
    return view


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, *, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed: bool | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=build_notice_embed("Only the person who ran this command can respond to this.", success=False),
                ephemeral=True,
            )
            return False
        return True

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_timeout(self) -> None:
        self._disable_all()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.secondary, emoji="\U0001F7E2")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="\U0001F534")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(view=self)


class CasesPaginatorView(discord.ui.View):
    def __init__(
        self,
        author_id: int,
        member: discord.abc.User,
        case_rows: list,
        guild: discord.Guild,
        per_page: int = 5,
    ):
        super().__init__(timeout=120.0)
        self.author_id = author_id
        self.member = member
        self.case_rows = case_rows
        self.guild = guild
        self.per_page = per_page
        self.page = 0
        self.last_page = max(0, (len(case_rows) - 1) // per_page)
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.last_page

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        page_rows = self.case_rows[start : start + self.per_page]

        embed = discord.Embed(color=NEUTRAL_COLOR, timestamp=discord.utils.utcnow())
        # Read BRAND_ICON_URL through the module at call time, not at import time.
        # Importing it by value would bind to None permanently since set_brand_icon
        # updates the module attribute after views.py has already been imported.
        embed.set_author(
            name=f"Case History  \u2022  {self.member}",
            icon_url=embeds_module.BRAND_ICON_URL,
        )
        for row in page_rows:
            field_name, field_value = build_case_line(row, self.guild)
            embed.add_field(name=field_name, value=field_value, inline=False)

        embed.set_footer(
            text=f"Page {self.page + 1} of {self.last_page + 1}  \u2022  "
            f"{len(self.case_rows)} total  \u2022  {BRAND_NAME}",
            icon_url=embeds_module.BRAND_ICON_URL,
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=build_notice_embed("Only the person who ran this command can page through this.", success=False),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self.previous_button.disabled = True
        self.next_button.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


def build_confirm_prompt(description: str) -> discord.Embed:
    embed = discord.Embed(
        title="\u26A0\uFE0F  Confirm global action",
        description=description,
        color=WARNING_COLOR,
    )
    embed.add_field(name="Heads up", value="This affects every server the bot is in. You have 30 seconds.", inline=False)
    return branded(embed)
