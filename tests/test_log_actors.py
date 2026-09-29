"""Logs name who did it: "xe2b (ID ...) gave @Role to hf0u (ID ...)"."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import discord
import pytest

import cogs.server_logs as server_logs


class _User(SimpleNamespace):
    def __str__(self):
        return self.name


def _user(uid, name):
    return _User(id=uid, name=name, mention=f"<@{uid}>", display_avatar=SimpleNamespace(url="https://x/a.png"))


def _guild(entries, can_view=True):
    async def audit_logs(limit, action):
        for entry in entries:
            yield entry
    perms = SimpleNamespace(view_audit_log=can_view)
    return SimpleNamespace(me=SimpleNamespace(guild_permissions=perms), audit_logs=audit_logs)


def _entry(target_id, actor, seconds_ago=1, reason=None):
    return SimpleNamespace(
        target=SimpleNamespace(id=target_id), user=actor, reason=reason,
        created_at=discord.utils.utcnow() - timedelta(seconds=seconds_ago),
    )


@pytest.fixture(autouse=True)
def no_wait():
    with patch.object(server_logs, "AUDIT_LOG_DELAY", 0):
        yield


@pytest.mark.asyncio
async def test_finds_the_recent_entry_for_the_target():
    giver = _user(55455, "xe2b")
    guild = _guild([_entry(999, _user(1, "other")), _entry(564, giver, reason="promotion")])
    assert await server_logs.find_actor(guild, discord.AuditLogAction.member_role_update, 564) == (giver, "promotion")


@pytest.mark.asyncio
async def test_ignores_old_entries():
    guild = _guild([_entry(564, _user(55455, "xe2b"), seconds_ago=600)])
    assert await server_logs.find_actor(guild, discord.AuditLogAction.ban, 564) == (None, None)


@pytest.mark.asyncio
async def test_no_audit_permission_means_unknown():
    guild = _guild([_entry(564, _user(55455, "xe2b"))], can_view=False)
    assert await server_logs.find_actor(guild, discord.AuditLogAction.ban, 564) == (None, None)


def test_who_formats_name_and_id():
    assert server_logs.who(_user(55455, "xe2b")) == "**xe2b** (ID `55455`)"
    assert server_logs.who(None) == "*Someone*"


@pytest.mark.asyncio
async def test_role_log_sentence():
    giver, recipient = _user(55455, "xe2b"), _user(564, "hf0u")
    role = SimpleNamespace(mention="<@&7>", id=7)
    guild = _guild([_entry(564, giver)])
    guild.default_role = object()
    before = SimpleNamespace(guild=guild, nick=None, roles=[], id=564)
    after = SimpleNamespace(
        guild=guild, nick=None, roles=[role], id=564, mention="<@564>", name="hf0u",
        display_avatar=recipient.display_avatar, __str__=lambda self: "hf0u",
    )
    after = _User(**vars(after))
    posted = []

    async def capture(guild, embed, category):
        posted.append((category, embed))

    with patch.object(server_logs, "post_to_server_log_channel", capture):
        await server_logs.ServerLogs(None).on_member_update(before, after)

    category, embed = posted[0]
    assert category == "member"
    assert embed.description == "**xe2b** (ID `55455`) gave <@&7> to **hf0u** (ID `564`)"
    assert any(field.name == "Given by" for field in embed.fields)
