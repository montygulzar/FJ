"""Integration tests for the global blacklist and announce-channel storage.

These need a real, disposable PostgreSQL database and are skipped unless
TEST_DATABASE_URL points at one, e.g.

    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/fjusa_test pytest
"""
import os

import pytest
import pytest_asyncio

import database

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest_asyncio.fixture
async def db(monkeypatch):
    monkeypatch.setattr(database.config, "DATABASE_URL", TEST_DATABASE_URL)
    database._pool = None
    database._closing = False
    await database.connect_database()
    await database._execute("DELETE FROM global_blacklist", idempotent=True)
    await database._execute("DELETE FROM guild_settings", idempotent=True)
    await database._execute("DELETE FROM appeals", idempotent=True)
    await database._execute("DELETE FROM appeal_votes", idempotent=True)
    await database._execute("DELETE FROM cases", idempotent=True)
    yield
    await database.close_database()


@pytest.mark.asyncio
async def test_blacklist_round_trip(db):
    assert await database.get_blacklist_entry(1) is None

    await database.add_blacklist(1, 50, "raiding")
    entry = await database.get_blacklist_entry(1)
    assert entry["moderator_id"] == 50 and entry["reason"] == "raiding"

    # Re-adding updates the entry instead of failing.
    await database.add_blacklist(1, 51, "raiding again")
    assert (await database.get_blacklist_entry(1))["reason"] == "raiding again"

    await database.add_blacklist(2, 50, "alt")
    assert {row["user_id"] for row in await database.list_blacklist()} == {1, 2}
    assert await database.get_blacklist_count() == 2

    assert await database.remove_blacklist(1) is True
    assert await database.remove_blacklist(1) is False
    assert await database.get_blacklist_entry(1) is None


@pytest.mark.asyncio
async def test_announce_channel_setting(db):
    assert (await database.get_guild_settings(7))["announce_channel_id"] is None
    await database.set_announce_channel(7, 123)
    assert (await database.get_guild_settings(7))["announce_channel_id"] == 123
    await database.set_announce_channel(7, None)
    assert (await database.get_guild_settings(7))["announce_channel_id"] is None


@pytest.mark.asyncio
async def test_locked_channel_ids(db):
    await database._execute("DELETE FROM channel_locks WHERE guild_id = 8", idempotent=True)
    await database.save_channel_lock(8, 100, {1: "none"})
    await database.save_channel_lock(8, 101, {1: "true"})
    assert sorted(await database.get_locked_channel_ids(8)) == [100, 101]
    await database.pop_channel_lock(8, 100)
    assert await database.get_locked_channel_ids(8) == [101]


@pytest.mark.asyncio
async def test_appeal_lifecycle(db):
    first = await database.create_appeal(9, 1, "please unban me " * 3, None)
    assert first is not None
    # Only one open appeal per user per server, enforced by the database.
    assert await database.create_appeal(9, 1, "again", None) is None
    assert (await database.get_open_appeal(9, 1))["id"] == first
    # A different server is a different ban.
    assert await database.create_appeal(10, 1, "other server", None) is not None

    assert await database.decide_appeal(first, "denied", 50, "no") is True
    # Two staff clicking at once: only the first decision counts.
    assert await database.decide_appeal(first, "accepted", 51, None) is False
    denied = await database.get_last_denied_appeal(9, 1)
    assert denied["id"] == first and denied["decided_by"] == 50 and denied["decided_at"]
    assert await database.get_open_appeal(9, 1) is None

    # After a decision a new appeal can be opened.
    second = await database.create_appeal(9, 1, "second try", "extra")
    assert second is not None and await database.count_appeals(9, 1) == 2
    await database.delete_appeal(second)
    assert await database.count_appeals(9, 1) == 1

    with pytest.raises(ValueError):
        await database.decide_appeal(first, "maybe", 1, None)


@pytest.mark.asyncio
async def test_latest_ban_case_and_counts(db):
    await database.add_case(9, 1, 50, "warn", "one")
    await database.add_case(9, 1, 50, "tempban", "two")
    await database.add_case(9, 1, 50, "warn", "three")
    latest = await database.get_latest_ban_case(9, 1)
    assert latest["action_type"] == "tempban" and latest["reason"] == "two"
    assert await database.get_case_counts_for_user(9, 1) == {"warn": 2, "tempban": 1}
    assert await database.get_latest_ban_case(9, 2) is None
    # A later blacklist supersedes the tempban - that's what makes it unappealable.
    await database.add_case(9, 1, 50, "blacklist", "four")
    assert (await database.get_latest_ban_case(9, 1))["action_type"] == "blacklist"


@pytest.mark.asyncio
async def test_votes_cast_switch_and_withdraw(db):
    appeal = await database.create_appeal(9, 1, "please unban me " * 3, None)
    await database.cast_vote(appeal, 100, True)
    await database.cast_vote(appeal, 101, False)
    await database.cast_vote(appeal, 101, True)      # switches, doesn't double count
    votes = await database.get_votes(appeal)
    assert {(v["voter_id"], v["approve"]) for v in votes} == {(100, True), (101, True)}
    await database.cast_vote(appeal, 100, None)      # withdrawn
    assert [v["voter_id"] for v in await database.get_votes(appeal)] == [101]
    await database.delete_appeal(appeal)             # votes go with it
    assert await database.get_votes(appeal) == []
