"""Settings the owner can change from Discord with !devset, without touching .env.

Only non-secret settings are editable. The bot token, database connection, owners,
command prefix and infrastructure tuning stay in .env on purpose: anyone with a
Discord account that got compromised could otherwise lock the owner out or take the
bot over.

Overrides are stored in the database and applied at startup (before commands load)
and immediately when changed. Values are updated in place wherever the rest of the
code holds them:
  - lists of IDs are sets, cleared and refilled in place, so every module that
    imported the set sees the change
  - single values (strings, numbers) are re-bound on `config` and on every loaded
    module that imported them by name
"""
import re
import sys
from dataclasses import dataclass

import config

_MISSING = object()


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str          # ids | id | int | bool | text | url | color | presets
    group: str
    help: str
    minimum: int | None = None
    maximum: int | None = None
    log_kind: str | None = None  # for *_LOGS_CHANNEL_IDS: the LOG_CHANNEL_IDS entry

    def current(self):
        if self.log_kind:
            return config.LOG_CHANNEL_IDS[self.log_kind]
        return getattr(config, self.key)


SETTINGS: dict[str, Setting] = {s.key: s for s in (
    # Branding
    Setting("BRAND_NAME", "text", "Branding", "Bot name in footers"),
    Setting("SERVER_DISPLAY_NAME", "text", "Branding", "Name in DMs, e.g. 'banned from all FJUSA servers'"),
    Setting("BRAND_COLOR", "color", "Branding", "Embed accent colour (hex) - fully applies after a restart"),
    Setting("LOGO_URL", "url", "Branding", "Logo image link (empty = bot avatar)"),
    Setting("DEVELOPER_NAME", "text", "Branding", "Developer shown in /help and Message Developer"),
    Setting("DEVELOPER_ID", "text", "Branding", "Developer's user ID for Message Developer"),
    # Tiers
    Setting("STAFF_ROLE_IDS", "ids", "Tiers", "Staff roles"),
    Setting("STAFF_DIRECTOR_ROLE_IDS", "ids", "Tiers", "Staff Director roles"),
    Setting("GOV_ROLE_IDS", "ids", "Tiers", "Gov roles"),
    Setting("DEV_ROLE_IDS", "ids", "Tiers", "Dev roles"),
    Setting("DEV_USER_IDS", "ids", "Tiers", "Dev users"),
    # Moderation
    Setting("MUTE_ROLE_ID", "id", "Moderation", "Role /mute gives"),
    Setting("REASON_PRESETS", "presets", "Moderation", "Reason suggestions, separated by |"),
    # Appeals
    Setting("APPEALS_CHANNEL_ID", "id", "Appeals", "Where appeals are voted on"),
    Setting("APPEAL_ALERT_CHANNEL_ID", "id", "Appeals", "Where new-appeal alerts go"),
    Setting("APPEAL_VOTER_ROLE_IDS", "ids", "Appeals", "Who can vote (empty = Staff Director+)"),
    Setting("APPEAL_MIN_VOTES", "int", "Appeals", "Votes needed before a decision", 1, 25),
    Setting("APPEAL_PING_VOTERS", "bool", "Appeals", "Ping voter roles on new appeals"),
    Setting("APPEAL_TEAM_NAME", "text", "Appeals", "Who decisions come from"),
    Setting("APPEAL_COOLDOWN_DAYS", "int", "Appeals", "Days before re-appealing after a denial", 0, 365),
    Setting("APPEAL_URL", "url", "Appeals", "Appeals server link (fallback)"),
    # Logs
    Setting("MOD_LOGS_CHANNEL_IDS", "ids", "Logs", "Mod Logs", log_kind="mod"),
    Setting("CHAT_LOGS_CHANNEL_IDS", "ids", "Logs", "Chat Logs", log_kind="chat"),
    Setting("JOIN_LOGS_CHANNEL_IDS", "ids", "Logs", "Join Logs", log_kind="join"),
    Setting("MEMBER_LOGS_CHANNEL_IDS", "ids", "Logs", "Member Logs", log_kind="member"),
    Setting("VOICE_LOGS_CHANNEL_IDS", "ids", "Logs", "Voice Logs", log_kind="voice"),
    Setting("SERVER_LOGS_CHANNEL_IDS", "ids", "Logs", "Server Logs", log_kind="server"),
    Setting("SECURITY_LOGS_CHANNEL_IDS", "ids", "Logs", "Security Logs", log_kind="security"),
    # Server control
    Setting("APPROVED_GUILD_IDS", "ids", "Server control", "Servers the bot may stay in"),
    Setting("LEAVE_UNAPPROVED_GUILDS", "bool", "Server control", "Leave servers not on the list"),
    Setting("GLOBAL_ACTION_EXEMPT_GUILD_IDS", "ids", "Server control", "Skipped by global actions"),
    Setting("PROTECTED_USER_IDS", "ids", "Server control", "Can never be moderated"),
    Setting("BLOCKED_USER_IDS", "ids", "Server control", "Can't use the bot"),
)}

# Values captured from other settings at import time that must follow them.
_ALIASES = {"BRAND_COLOR": ("NEUTRAL_COLOR",)}

_TRUE = {"1", "true", "yes", "y", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "n", "off", "disable", "disabled"}
CLEAR_WORDS = {"none", "clear", "empty", "off"}


class SettingError(ValueError):
    """A value that can't be used for a setting, with a message for the user."""


def lookup(key: str) -> Setting | None:
    return SETTINGS.get(key.strip().upper())


def parse(setting: Setting, raw: str):
    """Turn what was typed in Discord into the stored value. Mentions (<@&123>, <#123>,
    <@123>) and plain IDs both work for ID settings."""
    text = raw.strip()
    kind = setting.kind
    if kind in ("ids", "id"):
        if text.lower() in CLEAR_WORDS:
            return set() if kind == "ids" else 0
        ids = [int(match) for match in re.findall(r"\d{15,21}", text)]
        leftovers = re.sub(r"<[@#&!]*\d+>|\d{15,21}|[\s,]", "", text)
        if not ids or leftovers:
            raise SettingError("Use a mention (@role, #channel, @user) or a Discord ID.")
        if kind == "id":
            if len(ids) != 1:
                raise SettingError("This setting takes exactly one ID.")
            return ids[0]
        return set(ids)
    if kind == "int":
        if not re.fullmatch(r"-?\d+", text):
            raise SettingError("That needs to be a whole number.")
        value = int(text)
        if setting.minimum is not None and value < setting.minimum or setting.maximum is not None and value > setting.maximum:
            raise SettingError(f"That needs to be between {setting.minimum} and {setting.maximum}.")
        return value
    if kind == "bool":
        if text.lower() in _TRUE:
            return True
        if text.lower() in _FALSE:
            return False
        raise SettingError("Use `true` or `false`.")
    if kind == "color":
        cleaned = text.lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
            raise SettingError("Use a hex colour like `1D4ED8`.")
        return int(cleaned, 16)
    if kind == "url":
        if text.lower() in CLEAR_WORDS:
            return ""
        if not text.startswith(("https://", "http://")):
            raise SettingError("That needs to be a full link starting with https://")
        return text
    if kind == "presets":
        presets = [part.strip() for part in text.split("|") if part.strip()]
        if not presets:
            raise SettingError("Give at least one reason, separated by |")
        return presets
    if kind == "text":
        if not text or len(text) > 100:
            raise SettingError("That needs to be 1-100 characters.")
        return text
    raise SettingError(f"Unknown setting type {kind}")


def serialize(setting: Setting, value) -> str:
    if setting.kind == "ids":
        return ",".join(str(i) for i in sorted(value))
    if setting.kind == "presets":
        return "|".join(value)
    if setting.kind == "color":
        return f"{value:06X}"
    if setting.kind == "bool":
        return "true" if value else "false"
    return str(value)


def display(setting: Setting, value=_MISSING) -> str:
    """How a value reads in Discord: mentions for roles/channels/users."""
    value = setting.current() if value is _MISSING else value
    kind, key = setting.kind, setting.key
    if kind in ("ids", "id"):
        ids = sorted(value) if kind == "ids" else ([value] if value else [])
        if not ids:
            return "*not set*"
        if "CHANNEL" in key:
            fmt = "<#{}>"
        elif "ROLE" in key:
            fmt = "<@&{}>"
        elif "USER" in key:
            fmt = "<@{}>"
        else:
            fmt = "`{}`"
        return ", ".join(fmt.format(i) for i in ids)
    if kind == "color":
        return f"`#{value:06X}`"
    if kind == "bool":
        return "`true`" if value else "`false`"
    if kind == "presets":
        return " | ".join(value) or "*not set*"
    return f"`{value}`" if value else "*not set*"


def _rebind(name: str, old, new) -> None:
    """Point `config.name` and every module that imported it by name at the new value."""
    setattr(config, name, new)
    for module in list(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if module is config or namespace is None:
            continue
        if namespace.get(name, _MISSING) is old:
            namespace[name] = new


def apply(setting: Setting, value) -> None:
    """Make `value` live everywhere, without a restart."""
    current = setting.current()
    if setting.kind == "ids":
        current.clear()           # in place: guards, cogs etc. hold this same set
        current.update(value)
    elif setting.kind == "presets":
        current[:] = value        # in place, same reason
    else:
        _rebind(setting.key, current, value)
        for alias in _ALIASES.get(setting.key, ()):
            _rebind(alias, current, value)
    _after_change(setting.key)


def _after_change(key: str) -> None:
    """Values derived from a setting at import time."""
    views = sys.modules.get("views")
    if key == "DEVELOPER_ID" and views is not None:
        views.DEVELOPER_URL = f"https://discord.com/users/{config.DEVELOPER_ID}" if config.DEVELOPER_ID else None
    appeals = sys.modules.get("cogs.appeals")
    if key == "APPEALS_CHANNEL_ID" and appeals is not None:
        appeals._appeals_channel_ok = None  # unknown until checked; /syscheck re-checks it


# .env values, remembered so `!devset reset` can go back to them.
_ENV_DEFAULTS: dict[str, str] = {}


def remember_env_defaults() -> None:
    for key, setting in SETTINGS.items():
        _ENV_DEFAULTS.setdefault(key, serialize(setting, _copy(setting.current())))


def env_default(setting: Setting):
    return parse_stored(setting, _ENV_DEFAULTS[setting.key])


def parse_stored(setting: Setting, stored: str):
    """Values from the database/.env snapshot are already clean; empty means unset."""
    if setting.kind == "ids":
        return {int(part) for part in stored.split(",") if part}
    if setting.kind == "id":
        return int(stored) if stored else 0
    if setting.kind == "int":
        return int(stored)
    if setting.kind == "bool":
        return stored == "true"
    if setting.kind == "color":
        return int(stored, 16)
    if setting.kind == "presets":
        return [part for part in stored.split("|") if part]
    return stored


def _copy(value):
    return set(value) if isinstance(value, set) else list(value) if isinstance(value, list) else value


async def load_overrides() -> list[str]:
    """Apply every stored override. Called at startup, before commands load."""
    import database

    remember_env_defaults()
    applied = []
    for row in await database.get_bot_settings():
        setting = SETTINGS.get(row["key"])
        if setting is None:
            continue  # a setting that no longer exists
        try:
            apply(setting, parse_stored(setting, row["value"]))
            applied.append(setting.key)
        except (ValueError, KeyError):
            continue
    return applied


# Captured at import, before any override is applied, so `!devset reset` can restore them.
remember_env_defaults()
