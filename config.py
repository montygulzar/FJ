"""Environment configuration.

Everything is read from the process environment. In production Docker injects it
from the .env file next to docker-compose.yml; for local runs python-dotenv loads
the same file. Nothing here is tied to a particular host or hosting provider - the
only values with no sensible default are the Discord token and the database
connection details.

Validation collects *every* problem and reports them together, so a misconfigured
container tells you all of what is wrong in one startup instead of one item per
restart cycle.
"""
import os
import sys
from urllib.parse import quote, urlsplit

from dotenv import load_dotenv

load_dotenv()

# Problems found while reading the environment. Reported together at import time.
_errors: list[str] = []


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_id_list(*variable_names: str) -> set[int]:
    """Read a comma-separated list of Discord IDs from the first variable that has a value."""
    for name in variable_names:
        raw_value = _env(name)
        if not raw_value:
            continue
        try:
            return {int(piece.strip()) for piece in raw_value.split(",") if piece.strip()}
        except ValueError:
            _errors.append(f"{name} must be a comma-separated list of numeric Discord IDs, got: {raw_value!r}")
            return set()
    return set()


def _parse_bool(name: str, default: bool = False) -> bool:
    raw_value = _env(name).lower()
    if not raw_value:
        return default
    if raw_value in {"1", "true", "yes", "y", "on"}:
        return True
    if raw_value in {"0", "false", "no", "n", "off"}:
        return False
    _errors.append(f"{name} must be a boolean (true/false), got: {raw_value!r}")
    return default


def _parse_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw_value = _env(name)
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        _errors.append(f"{name} must be a whole number, got: {raw_value!r}")
        return default
    if minimum is not None and value < minimum:
        _errors.append(f"{name} must be at least {minimum}, got {value}")
        return default
    if maximum is not None and value > maximum:
        _errors.append(f"{name} must be at most {maximum}, got {value}")
        return default
    return value


def _parse_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw_value = _env(name)
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        _errors.append(f"{name} must be a number, got: {raw_value!r}")
        return default
    if value < minimum:
        _errors.append(f"{name} must be at least {minimum}, got {value}")
        return default
    return value


def _parse_choice(name: str, default: str, allowed: set[str]) -> str:
    raw_value = _env(name).lower()
    if not raw_value:
        return default
    if raw_value not in allowed:
        _errors.append(f"{name} must be one of {sorted(allowed)}, got: {raw_value!r}")
        return default
    return raw_value


# --- Discord ------------------------------------------------------------------

BOT_TOKEN = _env("BOT_TOKEN")
if not BOT_TOKEN:
    _errors.append("BOT_TOKEN is required. Set it in your .env file.")

COMMAND_PREFIX = os.environ.get("COMMAND_PREFIX", "!")
if not COMMAND_PREFIX:
    _errors.append("COMMAND_PREFIX must not be empty - prefix commands would be unusable.")

BRAND_NAME = os.environ.get("BRAND_NAME", "FJUSA Mod Bot")

# Accent colour for embeds, as hex (e.g. 1D4ED8).
def _parse_color(name: str, default: int) -> int:
    raw_value = _env(name).lstrip("#")
    if not raw_value:
        return default
    try:
        value = int(raw_value, 16)
    except ValueError:
        _errors.append(f"{name} must be a hex colour like 1D4ED8, got: {raw_value!r}")
        return default
    if not 0 <= value <= 0xFFFFFF:
        _errors.append(f"{name} must be between 000000 and FFFFFF, got: {raw_value!r}")
        return default
    return value


BRAND_COLOR = _parse_color("BRAND_COLOR", 0x1D4ED8)

# Public image URL for the logo shown in embed corners and footers. When empty the
# bot's own avatar is used - run /setlogo once to make that avatar assets/fjusa-logo.png.
LOGO_URL = _env("LOGO_URL")

# Name used in ban DMs ("You have been banned from all FJUSA servers").
SERVER_DISPLAY_NAME = _env("SERVER_DISPLAY_NAME", "FJUSA")

# Bot developer, shown in the unapproved-server notice and the /help footer.
DEVELOPER_NAME = _env("DEVELOPER_NAME", "xe2b")
DEVELOPER_ID = _env("DEVELOPER_ID", "1195765102725582968")

# Invite to the appeals server. Ban DMs show an "Appeal your ban" button when set.
APPEAL_URL = _env("APPEAL_URL")
if APPEAL_URL and not APPEAL_URL.startswith(("https://", "http://")):
    _errors.append(f"APPEAL_URL must be a full link starting with https://, got: {APPEAL_URL!r}")

OWNER_IDS = _parse_id_list("OWNER_IDS")

# --- Tiered role system -------------------------------------------------------
# Four tiers, each inheriting every tier below it:
#   DEV > GOV > STAFF_DIRECTOR > STAFF
#
# A user with a role in a higher tier can use every command available to lower
# tiers. OWNER_IDS (user IDs, not role IDs) bypass the tier system entirely.
# DEV_USER_IDS is checked by user ID (like OWNER_IDS), not by role.
STAFF_ROLE_IDS = _parse_id_list("STAFF_ROLE_IDS")
STAFF_DIRECTOR_ROLE_IDS = _parse_id_list("STAFF_DIRECTOR_ROLE_IDS")
GOV_ROLE_IDS = _parse_id_list("GOV_ROLE_IDS")
DEV_USER_IDS = _parse_id_list("DEV_USER_IDS")
DEV_ROLE_IDS = _parse_id_list("DEV_ROLE_IDS", "DEVELOPMENT_ROLE_IDS")

# Reasons suggested as you type in /warn, /ban, /mute etc. Separate with |
REASON_PRESETS = [
    reason.strip()
    for reason in _env(
        "REASON_PRESETS",
        "Spamming|Harassment|NSFW content|Advertising|Trolling|Disrespecting staff|"
        "Breaking server rules|Ban evasion|Alt account|Exploiting",
    ).split("|")
    if reason.strip()
]

# Appeals submitted from ban DMs are posted in APPEALS_CHANNEL_ID, where members
# with APPEAL_VOTER_ROLE_IDS vote to approve or deny the unban. Leave the channel
# empty to turn in-Discord appeals off (APPEAL_URL still works).
APPEALS_CHANNEL_ID = _parse_int("APPEALS_CHANNEL_ID", 0, minimum=0)
# Where "a ban appeal has been sent" alerts and outcomes are posted (optional).
APPEAL_ALERT_CHANNEL_ID = _parse_int("APPEAL_ALERT_CHANNEL_ID", 0, minimum=0)
# Roles allowed to vote. Empty = Staff Director+. Must be roles in the server that
# holds APPEALS_CHANNEL_ID, since that's where the buttons are pressed.
APPEAL_VOTER_ROLE_IDS = _parse_id_list("APPEAL_VOTER_ROLE_IDS")
# Votes needed before a decision; the side with more votes then wins (a tie waits
# for another vote).
APPEAL_MIN_VOTES = _parse_int("APPEAL_MIN_VOTES", 3, minimum=1, maximum=25)
# Mention the voter roles in the alert channel when a new appeal arrives.
APPEAL_PING_VOTERS = _parse_bool("APPEAL_PING_VOTERS", True)
# Who appeal decisions are signed by ("The FJUSA Ban Team has reviewed your case").
APPEAL_TEAM_NAME = _env("APPEAL_TEAM_NAME") or f"{SERVER_DISPLAY_NAME} Ban Team"
# How long someone must wait to appeal again after a denial.
APPEAL_COOLDOWN_DAYS = _parse_int("APPEAL_COOLDOWN_DAYS", 7, minimum=0, maximum=365)

# --- Log channels -------------------------------------------------------------
# Each is a comma-separated list of channel IDs, one per server: an event is posted
# to the channel in the list that belongs to the server it happened in. A kind left
# empty falls back to /setlogchannel (Mod Logs) or /setserverlogchannel (the rest).
# The older *_LOG_CHANNEL_IDS names are still accepted.
LOG_CHANNEL_IDS = {
    "mod": _parse_id_list("MOD_LOGS_CHANNEL_IDS", "MOD_LOG_CHANNEL_IDS"),
    "chat": _parse_id_list("CHAT_LOGS_CHANNEL_IDS", "MESSAGE_LOG_CHANNEL_IDS"),
    "join": _parse_id_list("JOIN_LOGS_CHANNEL_IDS"),
    "member": _parse_id_list("MEMBER_LOGS_CHANNEL_IDS", "MEMBER_LOG_CHANNEL_IDS"),
    "voice": _parse_id_list("VOICE_LOGS_CHANNEL_IDS", "VOICE_LOG_CHANNEL_IDS"),
    "server": _parse_id_list("SERVER_LOGS_CHANNEL_IDS", "SERVER_LOG_CHANNEL_IDS"),
    "security": _parse_id_list("SECURITY_LOGS_CHANNEL_IDS", "ALERT_LOG_CHANNEL_IDS"),
}

# Display name and what each kind of log carries.
LOG_KINDS = {
    "mod":      ("\U0001F528 Mod Logs",      "Cases, lockdowns and purges"),
    "chat":     ("\U0001F4AC Chat Logs",     "Edited and deleted messages"),
    "join":     ("\U0001F6AA Join Logs",     "Members joining and leaving"),
    "member":   ("\U0001F464 Member Logs",   "Role and nickname changes, bans and unbans"),
    "voice":    ("\U0001F3A7 Voice Logs",    "Voice joins, moves and leaves"),
    "server":   ("\U0001F3D7\uFE0F Server Logs", "Channel, role and invite changes"),
    "security": ("\U0001F6E1\uFE0F Security Logs", "Raid and alt-account alerts"),
}

# Role given by /mute and removed by /unmute. /tempmute uses Discord's timeout
# instead and needs no role. The ID must be the same role in every server, or
# /mute reports that the role is missing there.
MUTE_ROLE_ID = _parse_int("MUTE_ROLE_ID", 0, minimum=0)

# Servers that global actions may be run from and applied to. Leaving this empty means
# global actions reach EVERY server the bot is in, including ones added without your knowledge.
APPROVED_GUILD_IDS = _parse_id_list("APPROVED_GUILD_IDS")

# When true, the bot immediately leaves any server not in APPROVED_GUILD_IDS.
LEAVE_UNAPPROVED_GUILDS = _parse_bool("LEAVE_UNAPPROVED_GUILDS")

# Guilds that are exempt from GLOBAL moderation actions (globalban, globalunban,
# globalkick, globalmute, globalunmute). Intended for an Appeals server: a globally
# banned user must still be able to remain in or join it. Normal per-guild moderation
# commands still work inside exempt guilds.
GLOBAL_ACTION_EXEMPT_GUILD_IDS = _parse_id_list("GLOBAL_ACTION_EXEMPT_GUILD_IDS")

# Users who can never be the TARGET of a moderation action. Owners are always protected
# on top of this list, so a compromised moderator account can't remove them.
PROTECTED_USER_IDS = _parse_id_list("PROTECTED_USER_IDS")

# Users who may not USE the bot at all. Every command refuses for them.
BLOCKED_USER_IDS = _parse_id_list("BLOCKED_USER_IDS")


# --- Database -----------------------------------------------------------------

def _database_url() -> str:
    """DATABASE_URL if given, otherwise assembled from discrete POSTGRES_* variables.

    The discrete form exists so a compose file can reuse the same POSTGRES_USER /
    POSTGRES_PASSWORD / POSTGRES_DB values it already passes to the Postgres
    container, instead of repeating the credentials inside a second URL.
    """
    explicit = _env("DATABASE_URL")
    if explicit:
        return explicit

    user = _env("POSTGRES_USER")
    password = _env("POSTGRES_PASSWORD")
    host = _env("POSTGRES_HOST")
    database = _env("POSTGRES_DB")
    port = _env("POSTGRES_PORT", "5432")
    if user and password and host and database:
        # Credentials are percent-encoded: an unescaped '@' or '/' in a password
        # silently produces a URL that points somewhere else entirely.
        return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}"
    return ""


DATABASE_URL = _database_url()
if not DATABASE_URL:
    _errors.append(
        "Database connection details are required. Set DATABASE_URL "
        "(postgresql://user:password@host:5432/dbname), or set POSTGRES_USER, "
        "POSTGRES_PASSWORD, POSTGRES_HOST and POSTGRES_DB and let it be assembled."
    )
elif urlsplit(DATABASE_URL).scheme not in {"postgres", "postgresql"}:
    _errors.append(
        f"DATABASE_URL must be a PostgreSQL URL starting with postgresql://, "
        f"got scheme {urlsplit(DATABASE_URL).scheme!r}."
    )

# Pool sizing. The bot is not query-heavy; a small pool is plenty and keeps the
# footprint low on a shared self-hosted Postgres.
DB_POOL_MIN_SIZE = _parse_int("DB_POOL_MIN_SIZE", 1, minimum=0, maximum=100)
DB_POOL_MAX_SIZE = _parse_int("DB_POOL_MAX_SIZE", 10, minimum=1, maximum=100)
if DB_POOL_MAX_SIZE < DB_POOL_MIN_SIZE:
    _errors.append(
        f"DB_POOL_MAX_SIZE ({DB_POOL_MAX_SIZE}) must be >= DB_POOL_MIN_SIZE ({DB_POOL_MIN_SIZE})."
    )

# Caps on how long a single query, and a single wait for a free connection, may take.
# Without these a stalled database wedges the command that touched it forever.
DB_COMMAND_TIMEOUT = _parse_float("DB_COMMAND_TIMEOUT", 30.0, minimum=1.0)
DB_ACQUIRE_TIMEOUT = _parse_float("DB_ACQUIRE_TIMEOUT", 10.0, minimum=1.0)

# Recycle idle connections so a Postgres restart doesn't leave the pool holding
# handles to a server that no longer exists.
DB_MAX_INACTIVE_CONNECTION_LIFETIME = _parse_float("DB_MAX_INACTIVE_CONNECTION_LIFETIME", 300.0, minimum=0.0)

# Startup retry. 0 attempts means "keep trying forever", which is what you want in
# Docker: the bot waits for Postgres to come up instead of exiting and crash-looping.
DB_CONNECT_MAX_ATTEMPTS = _parse_int("DB_CONNECT_MAX_ATTEMPTS", 0, minimum=0)
DB_CONNECT_BACKOFF_START = _parse_float("DB_CONNECT_BACKOFF_START", 1.0, minimum=0.1)
DB_CONNECT_BACKOFF_MAX = _parse_float("DB_CONNECT_BACKOFF_MAX", 30.0, minimum=1.0)

# Retries for individual queries once the bot is running, covering brief blips
# such as Postgres being restarted underneath a live bot.
DB_QUERY_MAX_RETRIES = _parse_int("DB_QUERY_MAX_RETRIES", 2, minimum=0, maximum=10)

# How long pool shutdown may take before connections are dropped outright. Must stay
# below the container's stop grace period or Docker will SIGKILL mid-cleanup.
DB_CLOSE_TIMEOUT = _parse_float("DB_CLOSE_TIMEOUT", 10.0, minimum=1.0)


# --- Logging ------------------------------------------------------------------

LOG_LEVEL = _parse_choice(
    "LOG_LEVEL", "info", {"critical", "error", "warning", "info", "debug"}
).upper()
LOG_FORMAT = _parse_choice("LOG_FORMAT", "text", {"text", "json"})
# discord.py's gateway/http loggers are extremely chatty at DEBUG. Opt in separately
# so LOG_LEVEL=debug on our own code stays readable.
LOG_LIBRARY_DEBUG = _parse_bool("LOG_LIBRARY_DEBUG")


# --- Health endpoint ----------------------------------------------------------

# Small HTTP server for container health checks and external monitoring. It is not
# published to the host by the compose file - only reachable on the Docker network.
HEALTH_SERVER_ENABLED = _parse_bool("HEALTH_SERVER_ENABLED", True)
HEALTH_HOST = _env("HEALTH_HOST", "0.0.0.0")
HEALTH_PORT = _parse_int("HEALTH_PORT", 8080, minimum=1, maximum=65535)


# --- Build metadata -----------------------------------------------------------
# Set as build args in the Dockerfile so a running container can report exactly
# which commit it was built from.

APP_VERSION = _env("APP_VERSION", "dev")
GIT_COMMIT = _env("GIT_COMMIT", "unknown")


def redacted_database_url(url: str | None = None) -> str:
    """The DSN with its credentials stripped, safe to log or show in Discord.

    A Postgres URL embeds credentials as scheme://user:password@host/db, so it must
    never be logged verbatim or truncated to a fixed prefix.

    Passing None means "the configured URL"; an explicit empty string is reported as
    unconfigured rather than silently falling back to it.
    """
    url = DATABASE_URL if url is None else url
    if not url:
        return "not configured"
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "set (unparseable)"
    if not parsed.hostname:
        return "set (redacted)"
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.path.lstrip("/") or "unknown"
    return f"{parsed.scheme}://***@{parsed.hostname}{port}/{database}"


if _errors:
    # Printed rather than logged: this runs before logging is configured, and these
    # are the messages someone reads in `docker logs` when the container won't start.
    print("Configuration error - the bot cannot start:", file=sys.stderr)
    for problem in _errors:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nFix the values above in your .env file, then recreate the container:\n"
        "  docker compose up -d --force-recreate",
        file=sys.stderr,
    )
    raise SystemExit(2)
