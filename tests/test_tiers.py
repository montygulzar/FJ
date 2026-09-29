"""Tests for the FJUSA tier hierarchy: Staff < Staff Director < Gov < Dev."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from discord.ext import commands

import guards

STAFF_ROLE, DIRECTOR_ROLE, GOV_ROLE = 10, 20, 30
DEV_USER, OWNER_USER = 900, 999

TIER_ROLES = {"staff": {STAFF_ROLE}, "staff_director": {DIRECTOR_ROLE}, "gov": {GOV_ROLE}}


def _predicate(tier: str):
    def command():
        pass

    guards.has_tier(tier)(command)
    return command.__commands_checks__[0]


def _ctx(user_id: int = 1, role_ids=(), in_guild: bool = True):
    author = SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=r) for r in role_ids])
    return SimpleNamespace(author=author, guild=object() if in_guild else None)


@pytest.fixture(autouse=True)
def configured_tiers():
    with patch.dict(guards._TIER_ROLE_IDS, TIER_ROLES), \
         patch.dict(guards._TIER_USER_IDS, {"dev": {DEV_USER}}), \
         patch("guards.OWNER_IDS", {OWNER_USER}):
        yield


async def _allowed(tier: str, ctx) -> bool:
    try:
        return await _predicate(tier)(ctx)
    except commands.CheckFailure:
        return False


# Which tiers each holder can use. Every tier inherits everything below it.
EXPECTED = {
    "staff": {"staff"},
    "staff_director": {"staff", "staff_director"},
    "gov": {"staff", "staff_director", "gov"},
}


class TestTierInheritance:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("holder,role", [("staff", STAFF_ROLE), ("staff_director", DIRECTOR_ROLE), ("gov", GOV_ROLE)])
    async def test_role_tiers_inherit_downwards_only(self, holder, role):
        ctx = _ctx(role_ids=[role])
        for tier in guards._TIERS:
            assert await _allowed(tier, ctx) == (tier in EXPECTED[holder]), (holder, tier)

    @pytest.mark.asyncio
    async def test_dev_user_can_use_everything(self):
        for tier in guards._TIERS:
            assert await _allowed(tier, _ctx(user_id=DEV_USER))

    @pytest.mark.asyncio
    async def test_owner_bypasses_everything_even_in_dms(self):
        for tier in guards._TIERS:
            assert await _allowed(tier, _ctx(user_id=OWNER_USER, in_guild=False))

    @pytest.mark.asyncio
    async def test_no_roles_denied_everywhere(self):
        for tier in guards._TIERS:
            assert not await _allowed(tier, _ctx(role_ids=[12345]))

    @pytest.mark.asyncio
    async def test_dev_tier_is_not_granted_by_roles(self):
        assert not await _allowed("dev", _ctx(role_ids=[STAFF_ROLE, DIRECTOR_ROLE, GOV_ROLE]))

    @pytest.mark.asyncio
    async def test_denial_messages(self):
        with pytest.raises(commands.CheckFailure, match="Staff Director"):
            await _predicate("staff_director")(_ctx(role_ids=[STAFF_ROLE]))
        with pytest.raises(commands.CheckFailure, match="bot dev only"):
            await _predicate("dev")(_ctx(role_ids=[GOV_ROLE]))


class TestIsGlobalTarget:
    def _guild(self, guild_id):
        return SimpleNamespace(id=guild_id)

    def test_exempt_guild_is_not_a_target(self):
        from cogs.global_moderation import is_global_target

        with patch("cogs.global_moderation.APPROVED_GUILD_IDS", set()), \
             patch("cogs.global_moderation.GLOBAL_ACTION_EXEMPT_GUILD_IDS", {2}):
            assert is_global_target(self._guild(1))
            assert not is_global_target(self._guild(2))

    def test_unapproved_guild_is_not_a_target(self):
        from cogs.global_moderation import is_global_target

        with patch("cogs.global_moderation.APPROVED_GUILD_IDS", {1}), \
             patch("cogs.global_moderation.GLOBAL_ACTION_EXEMPT_GUILD_IDS", set()):
            assert is_global_target(self._guild(1))
            assert not is_global_target(self._guild(3))
