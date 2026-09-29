"""Appeal voting rules and categorised log routing."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cogs.appeals import add_vote_field, can_vote, tally, vote_outcome


class TestVoteOutcome:
    @pytest.mark.parametrize("approve,deny,expected", [
        (0, 0, None), (2, 0, None),          # not enough votes yet
        (3, 0, "accepted"), (2, 1, "accepted"),
        (0, 3, "denied"), (1, 2, "denied"),
        (2, 2, None),                        # tie at/above minimum waits for another vote
        (3, 2, "accepted"),
    ])
    def test_minimum_three_majority_wins(self, approve, deny, expected):
        assert vote_outcome(approve, deny, minimum=3) == expected

    def test_custom_minimum(self):
        assert vote_outcome(1, 0, minimum=1) == "accepted"
        assert vote_outcome(3, 1, minimum=5) is None

    def test_tally_splits_votes(self):
        rows = [{"voter_id": 1, "approve": True}, {"voter_id": 2, "approve": False}, {"voter_id": 3, "approve": True}]
        assert tally(rows) == ([1, 3], [2])


class TestCanVote:
    def _member(self, user_id=10, roles=()):
        return SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=r) for r in roles])

    def test_voter_roles_when_configured(self):
        with patch("cogs.appeals.APPEAL_VOTER_ROLE_IDS", {77}), patch("cogs.appeals.OWNER_IDS", set()):
            assert can_vote(self._member(roles=[77]))
            assert not can_vote(self._member(roles=[5]))

    def test_owner_always_and_blocked_never(self):
        with patch("cogs.appeals.APPEAL_VOTER_ROLE_IDS", {77}), patch("cogs.appeals.OWNER_IDS", {1}), \
             patch("cogs.appeals.is_blocked", lambda uid: uid == 2):
            assert can_vote(self._member(user_id=1))
            assert not can_vote(self._member(user_id=2, roles=[77]))

    def test_falls_back_to_staff_director(self):
        import guards
        with patch("cogs.appeals.APPEAL_VOTER_ROLE_IDS", set()), patch("cogs.appeals.OWNER_IDS", set()), \
             patch.dict(guards._TIER_ROLE_IDS, {"staff": {1}, "staff_director": {2}}):
            assert can_vote(self._member(roles=[2]))
            assert not can_vote(self._member(roles=[1]))


def test_vote_field_is_replaced_not_duplicated():
    import discord
    embed = discord.Embed(title="Appeal")
    add_vote_field(embed, [], [])
    add_vote_field(embed, [1, 2], [3])
    vote_fields = [f for f in embed.fields if "Staff vote" in f.name]
    assert len(vote_fields) == 1
    assert "Approve (2)" in vote_fields[0].value and "Deny (1)" in vote_fields[0].value


class TestLogRouting:
    def _guild(self, channel_ids):
        return SimpleNamespace(id=1, get_channel_or_thread=lambda cid: object() if cid in channel_ids else None)

    def test_picks_the_channel_in_this_server(self):
        import modlog
        routes = {"chat": [111, 222], "mod": []}
        with patch.dict(modlog.LOG_CHANNEL_IDS, routes):
            assert modlog.env_log_channel_id(self._guild({222}), "chat") == 222
            assert modlog.env_log_channel_id(self._guild({111}), "chat") == 111
            assert modlog.env_log_channel_id(self._guild({999}), "chat") is None
            assert modlog.env_log_channel_id(self._guild({111}), "mod") is None

    @pytest.mark.asyncio
    async def test_falls_back_to_database_channels(self):
        import modlog
        settings = {"log_channel_id": 5, "server_log_channel_id": None}

        async def fake_settings(guild_id):
            return settings

        with patch.dict(modlog.LOG_CHANNEL_IDS, {"chat": [], "mod": []}), \
             patch("modlog.get_guild_settings", fake_settings):
            guild = self._guild(set())
            assert await modlog.resolve_log_channel_id(guild, "mod") == 5
            assert await modlog.resolve_log_channel_id(guild, "chat") == 5  # server-log unset -> mod-log
            settings["server_log_channel_id"] = 6
            assert await modlog.resolve_log_channel_id(guild, "chat") == 6

    @pytest.mark.asyncio
    async def test_unknown_category_rejected(self):
        import modlog
        with pytest.raises(ValueError):
            await modlog.post_log(self._guild(set()), "nonsense", None)


def test_every_error_code_is_documented():
    from pathlib import Path
    from error_codes import CODES

    docs = (Path(__file__).resolve().parent.parent / "docs" / "ERROR_CODES.md").read_text()
    missing = [code for code in CODES if f"`{code}`" not in docs]
    assert not missing, f"Add these to docs/ERROR_CODES.md: {missing}"


def test_syscheck_config_codes():
    from types import SimpleNamespace
    import syscheck

    cfg = SimpleNamespace(
        OWNER_IDS=set(), STAFF_ROLE_IDS={1}, STAFF_DIRECTOR_ROLE_IDS=set(), GOV_ROLE_IDS=set(),
        LEAVE_UNAPPROVED_GUILDS=True, APPROVED_GUILD_IDS=set(), PROTECTED_USER_IDS={5}, BLOCKED_USER_IDS={5},
        GLOBAL_ACTION_EXEMPT_GUILD_IDS=set(), MUTE_ROLE_ID=9, APPEAL_ALERT_CHANNEL_ID=3, APPEALS_CHANNEL_ID=3,
    )
    report = syscheck.Report()
    syscheck.check_config(report, cfg)
    codes = {finding.code for finding in report.findings}
    assert codes == {"FJ-CFG-001", "FJ-CFG-003", "FJ-CFG-004", "FJ-CFG-007", "FJ-CFG-008"}
    assert report.passed == report.checks_run - 5


def test_report_rejects_unknown_codes():
    import syscheck

    with pytest.raises(KeyError):
        syscheck.Report().check(False, "FJ-NOPE-001")
