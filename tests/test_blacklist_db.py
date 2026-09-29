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
