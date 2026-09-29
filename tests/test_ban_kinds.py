"""Ban kinds: temp/permanent bans are appealable, blacklist bans are final."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cogs.appeals import is_final_ban
from embeds import build_ban_dm_embed, dm_headline


class TestFinalBans:
    @pytest.mark.parametrize("action", ["ban", "tempban", None])
    def test_temp_and_permanent_are_appealable(self, action):
        assert not is_final_ban(action, globally_blacklisted=False)

    @pytest.mark.parametrize("action", ["blacklist", "global_ban"])
    def test_blacklists_are_final(self, action):
        assert is_final_ban(action, globally_blacklisted=False)

    def test_global_blacklist_overrides_an_appealable_case(self):
        assert is_final_ban("tempban", globally_blacklisted=True)


class TestBanViews:
    def _view(self, **kwargs):
        from views import BanAppealView
        return BanAppealView(**kwargs)

    def _labels(self, view):
        return [getattr(item, "label", None) or getattr(getattr(item, "item", None), "label", None) for item in view.children]

    @pytest.mark.asyncio
    async def test_appealable_bans_get_the_appeal_button(self):
        with patch("cogs.appeals.APPEALS_CHANNEL_ID", 123):
            view = self._view(guild_id=5, appealable=True)
        assert view.has_appeal_button and not view.has_developer_button
        assert "Submit an appeal" in self._labels(view)

    @pytest.mark.asyncio
    async def test_blacklist_gets_developer_button_only(self):
        with patch("cogs.appeals.APPEALS_CHANNEL_ID", 123), patch("views.DEVELOPER_URL", "https://discord.com/users/1"):
            view = self._view(guild_id=5, appealable=False, contact_developer=True)
        assert not view.has_appeal_button and view.has_developer_button
        assert view.children[0].url == "https://discord.com/users/1"


class TestDmText:
    def test_headline_reads_naturally(self):
        assert dm_headline("mute", "FJUSA").endswith("You were muted in FJUSA")
        assert dm_headline("blacklist", "FJUSA").endswith("You were blacklisted from FJUSA")

    def test_blacklist_dm_says_final(self):
        guild = SimpleNamespace(name="FJUSA", icon=None)
        embed = build_ban_dm_embed("abuse", kind="blacklist", guild=guild, can_contact_developer=True)
        text = " ".join(field.value for field in embed.fields)
        assert "cannot be appealed" in text and "Message Developer" in text

    def test_permanent_ban_dm_offers_appeal(self):
        guild = SimpleNamespace(name="FJUSA", icon=None)
        embed = build_ban_dm_embed("rules", kind="ban", guild=guild, can_appeal_here=True)
        text = " ".join(field.value for field in embed.fields)
        assert "Submit an appeal" in text and "cannot be appealed" not in text

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            build_ban_dm_embed("x", kind="softban")
