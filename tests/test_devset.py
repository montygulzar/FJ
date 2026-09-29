"""!devset: parsing, live application, and persistence of Discord-set settings."""
import sys
import types

import pytest

import config
import runtime_config as rc


@pytest.fixture
def restore():
    """Put every touched setting back afterwards."""
    saved = {key: rc._copy(setting.current()) for key, setting in rc.SETTINGS.items()}
    yield
    for key, value in saved.items():
        rc.apply(rc.SETTINGS[key], value)


class TestParse:
    def test_ids_accept_mentions_and_raw_ids(self):
        setting = rc.SETTINGS["STAFF_ROLE_IDS"]
        assert rc.parse(setting, "<@&111111111111111111> 222222222222222222") == {111111111111111111, 222222222222222222}
        assert rc.parse(setting, "none") == set()
        with pytest.raises(rc.SettingError):
            rc.parse(setting, "@Staff")  # a name, not a real mention

    def test_single_id_and_channel_mention(self):
        assert rc.parse(rc.SETTINGS["APPEALS_CHANNEL_ID"], "<#333333333333333333>") == 333333333333333333
        with pytest.raises(rc.SettingError):
            rc.parse(rc.SETTINGS["MUTE_ROLE_ID"], "111111111111111111 222222222222222222")

    def test_numbers_bools_colours_urls(self):
        assert rc.parse(rc.SETTINGS["APPEAL_MIN_VOTES"], "5") == 5
        with pytest.raises(rc.SettingError):
            rc.parse(rc.SETTINGS["APPEAL_MIN_VOTES"], "0")
        assert rc.parse(rc.SETTINGS["APPEAL_PING_VOTERS"], "off") is False
        assert rc.parse(rc.SETTINGS["BRAND_COLOR"], "#ff0000") == 0xFF0000
        with pytest.raises(rc.SettingError):
            rc.parse(rc.SETTINGS["APPEAL_URL"], "discord.gg/x")
        assert rc.parse(rc.SETTINGS["REASON_PRESETS"], "Spam | Raid") == ["Spam", "Raid"]

    def test_secrets_are_not_editable(self):
        for key in ("BOT_TOKEN", "DATABASE_URL", "OWNER_IDS", "COMMAND_PREFIX", "POSTGRES_PASSWORD"):
            assert rc.lookup(key) is None


class TestApply:
    def test_id_lists_change_in_place_so_importers_see_it(self, restore):
        import guards

        rc.apply(rc.SETTINGS["GOV_ROLE_IDS"], {444444444444444444})
        assert guards._TIER_ROLE_IDS["gov"] == {444444444444444444}
        assert config.GOV_ROLE_IDS is guards._TIER_ROLE_IDS["gov"]

    def test_single_values_rebind_in_modules_that_imported_them(self, restore):
        module = types.ModuleType("fake_importer")
        module.MUTE_ROLE_ID = config.MUTE_ROLE_ID
        sys.modules["fake_importer"] = module
        try:
            rc.apply(rc.SETTINGS["MUTE_ROLE_ID"], 555555555555555555)
            assert config.MUTE_ROLE_ID == 555555555555555555
            assert module.MUTE_ROLE_ID == 555555555555555555
        finally:
            del sys.modules["fake_importer"]

    def test_log_channels_update_routing(self, restore):
        import modlog

        rc.apply(rc.SETTINGS["CHAT_LOGS_CHANNEL_IDS"], {666666666666666666})
        assert modlog.LOG_CHANNEL_IDS["chat"] == {666666666666666666}

    def test_vote_minimum_is_read_live(self, restore):
        from cogs.appeals import vote_outcome

        rc.apply(rc.SETTINGS["APPEAL_MIN_VOTES"], 1)
        assert vote_outcome(1, 0) == "accepted"
        rc.apply(rc.SETTINGS["APPEAL_MIN_VOTES"], 5)
        assert vote_outcome(3, 0) is None

    def test_round_trip_through_storage(self):
        for key, setting in rc.SETTINGS.items():
            value = setting.current()
            assert rc.parse_stored(setting, rc.serialize(setting, value)) == value, key


def test_devset_is_owner_only_and_hidden_from_help():
    from cogs.devset import DevSet
    from cogs.help import command_tier

    command = DevSet.devset
    assert any(getattr(check, "fjusa_owner_only", False) for check in command.checks)
    assert command_tier(command) is None  # /help lists tiered commands only
