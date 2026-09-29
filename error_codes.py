"""Preset error codes reported by /syscheck.

Each code names one specific, fixable problem. Quote the code when asking for help:
it points straight at the part of the system that's wrong and how to fix it.

Format: FJ-<AREA>-<NUMBER>
  CFG  configuration (.env)          DB    database
  GW   Discord connection            CMD   commands / cogs
  PERM bot permissions in a server   ROLE  configured roles
  LOG  log channels                  APL   ban appeals
  TASK background tasks              BRD   branding
"""
from typing import NamedTuple

ERROR = "error"
WARNING = "warning"
INFO = "info"

SEVERITY_ICONS = {ERROR: "\U0001F534", WARNING: "\U0001F7E1", INFO: "\U0001F535"}


class ErrorCode(NamedTuple):
    severity: str
    title: str
    fix: str


CODES: dict[str, ErrorCode] = {
    # --- Configuration ---------------------------------------------------------
    "FJ-CFG-001": ErrorCode(ERROR, "No owner configured",
        "Set OWNER_IDS in .env to your Discord user ID, then restart the bot."),
    "FJ-CFG-002": ErrorCode(ERROR, "No staff tiers configured",
        "Set STAFF_ROLE_IDS / STAFF_DIRECTOR_ROLE_IDS / GOV_ROLE_IDS in .env - until then only owners can use commands."),
    "FJ-CFG-003": ErrorCode(WARNING, "Auto-leave is on with no approved servers",
        "Add your server IDs to APPROVED_GUILD_IDS, or set LEAVE_UNAPPROVED_GUILDS=false - otherwise the bot leaves everywhere."),
    "FJ-CFG-004": ErrorCode(WARNING, "User is both protected and blocked",
        "Remove the user from either PROTECTED_USER_IDS or BLOCKED_USER_IDS."),
    "FJ-CFG-005": ErrorCode(WARNING, "Exempt server isn't approved",
        "Every GLOBAL_ACTION_EXEMPT_GUILD_IDS entry should also be in APPROVED_GUILD_IDS."),
    "FJ-CFG-006": ErrorCode(WARNING, "No mute role set",
        "Set MUTE_ROLE_ID in .env so /mute works. /tempmute works without it."),
    "FJ-CFG-007": ErrorCode(INFO, "No approved server list",
        "Global actions reach every server the bot is in. Set APPROVED_GUILD_IDS to limit them to yours."),
    "FJ-CFG-008": ErrorCode(WARNING, "Appeal alert channel misconfigured",
        "APPEAL_ALERT_CHANNEL_ID needs APPEALS_CHANNEL_ID set too, and must be a different channel."),

    # --- Database --------------------------------------------------------------
    "FJ-DB-001": ErrorCode(ERROR, "Database unreachable",
        "Check the Postgres container is running and on the same Docker network, and that DATABASE_URL is right."),
    "FJ-DB-002": ErrorCode(WARNING, "Database is slow",
        "Queries are taking over 250ms. Check the host's load and the Postgres container's resources."),
    "FJ-DB-003": ErrorCode(ERROR, "Database tables missing",
        "Restart the bot so it recreates its schema; if it persists, the DB user may lack CREATE rights."),

    # --- Discord connection ----------------------------------------------------
    "FJ-GW-001": ErrorCode(WARNING, "High Discord latency",
        "Gateway latency is over 1 second. Usually Discord-side or host network; restart if it persists."),
    "FJ-GW-002": ErrorCode(WARNING, "Bot isn't in any server",
        "Invite the bot to your server(s) with the bot and applications.commands scopes."),

    # --- Commands ----------------------------------------------------------------
    "FJ-CMD-001": ErrorCode(ERROR, "A command module failed to load",
        "Its commands are unavailable. Check `docker compose logs` for the traceback at startup."),
    "FJ-CMD-002": ErrorCode(WARNING, "Slash commands not synced",
        "Discord rejected or rate-limited the sync. Restart the bot later; prefix commands still work."),
    "FJ-CMD-003": ErrorCode(ERROR, "Command has no tier check",
        "A moderation command can be run by anyone. It needs a @has_tier(...) check - report this to the developer."),
    "FJ-CMD-004": ErrorCode(WARNING, "Command has been failing",
        "The command errored since the last restart. Check `docker compose logs` for the traceback."),

    # --- Permissions --------------------------------------------------------------
    "FJ-PERM-001": ErrorCode(ERROR, "Bot is missing permissions",
        "Give the bot's role the listed permissions (or Administrator) in Server Settings > Roles."),
    "FJ-PERM-002": ErrorCode(ERROR, "Mute role is above the bot",
        "Drag the bot's role above the mute role in Server Settings > Roles, or /mute will fail."),

    # --- Roles ---------------------------------------------------------------------
    "FJ-ROLE-001": ErrorCode(WARNING, "Configured role doesn't exist",
        "A role ID in .env isn't in any server the bot is in. Re-copy the role ID (right-click > Copy ID)."),
    "FJ-ROLE-002": ErrorCode(WARNING, "Mute role missing in a server",
        "MUTE_ROLE_ID isn't a role in this server, so /mute won't work here. Mute roles are per server."),

    # --- Logs ----------------------------------------------------------------------
    "FJ-LOG-001": ErrorCode(WARNING, "No Mod Logs channel",
        "Cases aren't being logged. Set MOD_LOGS_CHANNEL_IDS in .env or run /setlogchannel."),
    "FJ-LOG-002": ErrorCode(ERROR, "Bot can't post in a log channel",
        "Give the bot View Channel, Send Messages and Embed Links in the listed channel."),
    "FJ-LOG-003": ErrorCode(WARNING, "Log channel ID not found",
        "A *_LOGS_CHANNEL_IDS entry isn't a channel in any server the bot is in. Re-copy the channel ID."),
    "FJ-LOG-004": ErrorCode(INFO, "Log kind not set up",
        "These logs aren't going anywhere. Optional - set the matching *_LOGS_CHANNEL_IDS if you want them."),

    # --- Appeals ---------------------------------------------------------------------
    "FJ-APL-001": ErrorCode(ERROR, "Appeals channel unreachable",
        "APPEALS_CHANNEL_ID isn't a channel the bot can see. Appeal buttons are hidden until it's fixed."),
    "FJ-APL-002": ErrorCode(ERROR, "Bot can't post in the appeals channel",
        "Give the bot View Channel, Send Messages and Embed Links in the appeals channel."),
    "FJ-APL-003": ErrorCode(WARNING, "No voter roles in the appeals server",
        "None of APPEAL_VOTER_ROLE_IDS exist in the appeals channel's server, so only owners can vote."),
    "FJ-APL-004": ErrorCode(WARNING, "Appeal alert channel unreachable",
        "APPEAL_ALERT_CHANNEL_ID isn't a channel the bot can post in, so nobody is told about new appeals."),
    "FJ-APL-005": ErrorCode(INFO, "In-Discord appeals are off",
        "Optional. Set APPEALS_CHANNEL_ID to let banned users appeal from their DMs."),

    # --- Background tasks ---------------------------------------------------------------
    "FJ-TASK-001": ErrorCode(ERROR, "Temp-ban expiry has stopped",
        "Temp bans won't lift on time. Restart the bot and check the logs for the error."),

    # --- Branding -------------------------------------------------------------------------
    "FJ-BRD-001": ErrorCode(INFO, "Logo not set",
        "Run /setlogo once, or set LOGO_URL, so embeds show the FJUSA logo."),
}


def lookup(code: str) -> ErrorCode | None:
    return CODES.get(code.strip().upper())
