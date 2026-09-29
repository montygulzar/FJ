"""The checks behind /syscheck. Each problem found is reported under a preset code
from error_codes.py, so it can be looked up and fixed without digging through logs."""
from dataclasses import dataclass, field

import discord
from discord.ext import commands

import config
import database
import diagnostics
from error_codes import CODES
from modlog import log_label, resolve_log_channel_id

# Permissions the bot needs in every server it moderates.
REQUIRED_PERMISSIONS = (
    "view_channel", "send_messages", "embed_links", "ban_members", "kick_members",
    "moderate_members", "manage_roles", "manage_channels", "manage_messages", "view_audit_log",
)
SLOW_DB_MS = 250
SLOW_GATEWAY_S = 1.0
# Commands anyone may run, by design.
PUBLIC_COMMANDS = {"help"}


@dataclass
class Finding:
    code: str
    detail: str

    @property
    def severity(self) -> str:
        return CODES[self.code].severity


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checks_run: int = 0

    def check(self, ok: bool, code: str, detail: str = "") -> bool:
        """Count one check, recording `code` if it failed. Returns `ok`."""
        if code not in CODES:
            raise KeyError(f"Unknown error code {code}")
        self.checks_run += 1
        if not ok:
            self.findings.append(Finding(code, detail))
        return ok

    @property
    def passed(self) -> int:
        return self.checks_run - len(self.findings)

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)


def _can_post(channel, me: discord.Member | None) -> bool:
    if me is None or not hasattr(channel, "permissions_for"):
        return False
    perms = channel.permissions_for(me)
    return perms.view_channel and perms.send_messages and perms.embed_links


def check_config(report: Report, cfg=config) -> None:
    """Pure .env checks - also run at startup (diagnostics.validate_config) to log warnings."""
    report.check(bool(cfg.OWNER_IDS), "FJ-CFG-001", "OWNER_IDS is empty.")
    report.check(
        bool(cfg.STAFF_ROLE_IDS or cfg.STAFF_DIRECTOR_ROLE_IDS or cfg.GOV_ROLE_IDS),
        "FJ-CFG-002", "No STAFF / STAFF_DIRECTOR / GOV role IDs are set.",
    )
    report.check(
        not (cfg.LEAVE_UNAPPROVED_GUILDS and not cfg.APPROVED_GUILD_IDS),
        "FJ-CFG-003", "LEAVE_UNAPPROVED_GUILDS=true but APPROVED_GUILD_IDS is empty.",
    )
    overlap = cfg.PROTECTED_USER_IDS & cfg.BLOCKED_USER_IDS
    report.check(not overlap, "FJ-CFG-004", ", ".join(f"`{uid}`" for uid in sorted(overlap)))
    stray = cfg.GLOBAL_ACTION_EXEMPT_GUILD_IDS - cfg.APPROVED_GUILD_IDS if cfg.APPROVED_GUILD_IDS else set()
    report.check(not stray, "FJ-CFG-005", ", ".join(f"`{gid}`" for gid in sorted(stray)))
    report.check(bool(cfg.MUTE_ROLE_ID), "FJ-CFG-006", "MUTE_ROLE_ID is empty.")
    report.check(bool(cfg.APPROVED_GUILD_IDS), "FJ-CFG-007", "APPROVED_GUILD_IDS is empty.")
    alert, appeals = cfg.APPEAL_ALERT_CHANNEL_ID, cfg.APPEALS_CHANNEL_ID
    report.check(
        not alert or (appeals and alert != appeals),
        "FJ-CFG-008",
        "APPEALS_CHANNEL_ID is empty." if not appeals else "It's the same channel as APPEALS_CHANNEL_ID.",
    )


async def check_database(report: Report) -> None:
    ok, detail = await database.check_connection()
    if not report.check(ok, "FJ-DB-001", detail):
        return
    try:
        latency_ms = float(detail.rstrip("ms"))
    except ValueError:
        latency_ms = 0.0
    report.check(latency_ms <= SLOW_DB_MS, "FJ-DB-002", f"Round trip took {latency_ms:.0f}ms.")
    missing = await database.missing_tables()
    report.check(not missing, "FJ-DB-003", "Missing: " + ", ".join(f"`{t}`" for t in missing))


def check_gateway(report: Report, bot: commands.Bot) -> None:
    latency = bot.latency
    report.check(
        latency == latency and latency <= SLOW_GATEWAY_S,  # NaN before the first heartbeat
        "FJ-GW-001", f"Latency is {latency * 1000:.0f}ms." if latency == latency else "No heartbeat yet.",
    )
    report.check(bool(bot.guilds), "FJ-GW-002", "The bot is in 0 servers.")


def check_commands(report: Report, bot: commands.Bot) -> None:
    failed = getattr(bot, "failed_extensions", [])
    report.check(not failed, "FJ-CMD-001", "Not loaded: " + ", ".join(f"`{name}`" for name in failed))

    sync_error = getattr(bot, "sync_error", None)
    synced = set(getattr(bot, "app_command_ids", {}))
    local = {command.name for command in bot.tree.get_commands()}
    unsynced = sorted(local - synced) if synced else []
    report.check(
        sync_error is None and not unsynced,
        "FJ-CMD-002",
        sync_error or ("Not on Discord yet: " + ", ".join(f"`/{name}`" for name in unsynced)),
    )

    for command in bot.walk_commands():
        if isinstance(command, commands.Group) or command.qualified_name in PUBLIC_COMMANDS:
            continue
        tiered = any(
            getattr(check, "fjusa_tier", None) or getattr(check, "fjusa_owner_only", False)
            for check in command.checks
        )
        report.check(tiered, "FJ-CMD-003", f"`/{command.qualified_name}` has no tier check.")

    _, total_invocations, total_errors = diagnostics.get_command_stats()
    failing = sorted(diagnostics._errors.items(), key=lambda item: -item[1])
    report.check(
        not failing, "FJ-CMD-004",
        ", ".join(f"`/{name}` x{count}" for name, count in failing[:8]) + f" ({total_errors}/{total_invocations} runs failed)",
    )


def moderated_guilds(bot: commands.Bot) -> list[discord.Guild]:
    if config.APPROVED_GUILD_IDS:
        return [guild for guild in bot.guilds if guild.id in config.APPROVED_GUILD_IDS]
    return list(bot.guilds)


async def check_guilds(report: Report, bot: commands.Bot) -> None:
    all_role_ids = {role.id for guild in bot.guilds for role in guild.roles}
    configured_roles = {
        "STAFF_ROLE_IDS": config.STAFF_ROLE_IDS,
        "STAFF_DIRECTOR_ROLE_IDS": config.STAFF_DIRECTOR_ROLE_IDS,
        "GOV_ROLE_IDS": config.GOV_ROLE_IDS,
        "DEV_ROLE_IDS": config.DEV_ROLE_IDS,
        "APPEAL_VOTER_ROLE_IDS": config.APPEAL_VOTER_ROLE_IDS,
    }
    for name, role_ids in configured_roles.items():
        for role_id in sorted(role_ids):
            report.check(role_id in all_role_ids, "FJ-ROLE-001", f"`{role_id}` in {name}.")

    for guild in moderated_guilds(bot):
        me = guild.me
        if me is None:
            continue
        missing = [perm for perm in REQUIRED_PERMISSIONS if not getattr(me.guild_permissions, perm)]
        report.check(
            not missing, "FJ-PERM-001",
            f"**{guild.name}**: " + ", ".join(perm.replace("_", " ") for perm in missing),
        )

        if config.MUTE_ROLE_ID:
            mute_role = guild.get_role(config.MUTE_ROLE_ID)
            if report.check(mute_role is not None, "FJ-ROLE-002", f"**{guild.name}**"):
                report.check(mute_role < me.top_role, "FJ-PERM-002", f"**{guild.name}**: {mute_role.mention}")

        unset = []
        for category in config.LOG_CHANNEL_IDS:
            channel_id = await resolve_log_channel_id(guild, category)
            if channel_id is None:
                if category == "mod":
                    report.check(False, "FJ-LOG-001", f"**{guild.name}**")
                else:
                    unset.append(log_label(category))
                continue
            channel = guild.get_channel_or_thread(channel_id)
            report.check(
                channel is not None and _can_post(channel, me),
                "FJ-LOG-002", f"**{guild.name}**: {log_label(category)} (<#{channel_id}>)",
            )
        # One line per server rather than one per log kind.
        report.check(not unset, "FJ-LOG-004", f"**{guild.name}**: " + ", ".join(unset))

    # Env log channel IDs that don't belong to any server at all.
    for category, channel_ids in config.LOG_CHANNEL_IDS.items():
        for channel_id in channel_ids:
            found = any(guild.get_channel_or_thread(channel_id) for guild in bot.guilds)
            report.check(found, "FJ-LOG-003", f"`{channel_id}` in {log_label(category)}.")


async def check_appeals(report: Report, bot: commands.Bot) -> None:
    import cogs.appeals as appeals

    if not report.check(bool(config.APPEALS_CHANNEL_ID), "FJ-APL-005"):
        return
    channel = await appeals._fetch_channel(bot, config.APPEALS_CHANNEL_ID)
    # A fixed channel re-enables the appeal button without a restart (and vice versa).
    appeals._appeals_channel_ok = channel is not None
    if not report.check(channel is not None, "FJ-APL-001", f"`{config.APPEALS_CHANNEL_ID}`"):
        return
    report.check(_can_post(channel, channel.guild.me), "FJ-APL-002", channel.mention)
    if config.APPEAL_VOTER_ROLE_IDS:
        report.check(
            any(channel.guild.get_role(role_id) for role_id in config.APPEAL_VOTER_ROLE_IDS),
            "FJ-APL-003", f"Appeals server: **{channel.guild.name}**",
        )
    if config.APPEAL_ALERT_CHANNEL_ID:
        alert = await appeals._fetch_channel(bot, config.APPEAL_ALERT_CHANNEL_ID)
        report.check(
            alert is not None and _can_post(alert, alert.guild.me),
            "FJ-APL-004", f"`{config.APPEAL_ALERT_CHANNEL_ID}`",
        )


def check_tasks(report: Report, bot: commands.Bot) -> None:
    scheduled = bot.get_cog("ScheduledTasks")
    loop = getattr(scheduled, "expire_temp_bans", None)
    report.check(
        loop is not None and loop.is_running() and not loop.failed(),
        "FJ-TASK-001", "The ScheduledTasks module isn't loaded." if loop is None else "The loop has stopped.",
    )


def check_branding(report: Report, bot: commands.Bot) -> None:
    has_avatar = bot.user is not None and bot.user.avatar is not None
    report.check(bool(config.LOGO_URL) or has_avatar, "FJ-BRD-001", "The bot still has Discord's default avatar.")


async def run_all(bot: commands.Bot) -> Report:
    report = Report()
    check_config(report)
    await check_database(report)
    check_gateway(report, bot)
    check_commands(report, bot)
    if database.is_connected():
        await check_guilds(report, bot)
    await check_appeals(report, bot)
    check_tasks(report, bot)
    check_branding(report, bot)
    return report
