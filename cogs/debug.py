"""Owner-only diagnostics.

Answers the questions you actually have when something looks wrong on the host:
is the gateway up, is Postgres reachable and how big is the pool, is the temp-ban
loop still running, what has been failing recently, and exactly which build is
running in this container.
"""
import logging
import math
import os
import platform
import time
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import config
import database
import diagnostics
import syscheck as syscheck_module
from embeds import (
    DANGER_COLOR,
    NEUTRAL_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
    base_embed,
    branded,
    build_notice_embed,
    clamp,
)
from error_codes import CODES, ERROR, INFO, SEVERITY_ICONS, WARNING, lookup
from guards import has_tier
from modlog import check_log_channel

try:
    import psutil
    _process = psutil.Process(os.getpid())
    _process.cpu_percent(interval=None)  # first call always returns 0.0; this just seeds the baseline
except Exception:
    # Missing psutil, or a sandboxed host that refuses process introspection - degrade gracefully.
    psutil = None
    _process = None

logger = logging.getLogger("modbot.syscheck")

TICK = "✅"
CROSS = "❌"
WARN = "⚠️"


def format_duration(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


def in_container() -> bool:
    """Whether this process is running inside a container.

    /.dockerenv is created by the Docker daemon; the cgroup check covers the
    podman/containerd cases where it is absent.
    """
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text() or "containerd" in Path("/proc/1/cgroup").read_text()
    except OSError:
        return False


def format_age(timestamp: float | None) -> str:
    if timestamp is None:
        return "never"
    return f"{time.time() - timestamp:.0f}s ago"


SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}
MAX_FINDINGS_SHOWN = 20


def build_report_embed(report: "syscheck_module.Report") -> discord.Embed:
    errors, warnings, infos = report.count(ERROR), report.count(WARNING), report.count(INFO)
    if errors:
        color, headline = DANGER_COLOR, "Problems found - fix the \U0001F534 errors first."
    elif warnings:
        color, headline = WARNING_COLOR, "Working, with a few things worth fixing."
    else:
        color, headline = SUCCESS_COLOR, "All systems go."
    embed = discord.Embed(
        title="\U0001FA7A  System Check",
        description=(
            f"{headline}\n\n"
            f"\u2705 **{report.passed}** passed  \u2022  "
            f"{SEVERITY_ICONS[ERROR]} **{errors}**  \u2022  "
            f"{SEVERITY_ICONS[WARNING]} **{warnings}**  \u2022  "
            f"{SEVERITY_ICONS[INFO]} **{infos}**  (of {report.checks_run} checks)"
        ),
        color=color,
    )
    findings = sorted(report.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.code))
    for finding in findings[:MAX_FINDINGS_SHOWN]:
        entry = CODES[finding.code]
        embed.add_field(
            name=f"{SEVERITY_ICONS[entry.severity]}  {finding.code}  \u2022  {entry.title}",
            value=clamp(f"{finding.detail}\n**Fix:** {entry.fix}" if finding.detail else f"**Fix:** {entry.fix}", 1024),
            inline=False,
        )
    if len(findings) > MAX_FINDINGS_SHOWN:
        embed.add_field(
            name=f"...and {len(findings) - MAX_FINDINGS_SHOWN} more",
            value="The full list is in the bot's logs (`docker compose logs`).",
            inline=False,
        )
    return branded(embed, footer_prefix="Quote the code when asking for help")


def build_code_embed(code: str) -> discord.Embed:
    entry = lookup(code)
    if entry is None:
        return build_notice_embed(f"`{code}` isn't a known error code. Codes look like `FJ-DB-001`.", success=False)
    color = {ERROR: DANGER_COLOR, WARNING: WARNING_COLOR, INFO: NEUTRAL_COLOR}[entry.severity]
    embed = discord.Embed(title=f"{SEVERITY_ICONS[entry.severity]}  {code.upper()}", description=f"**{entry.title}**", color=color)
    embed.add_field(name="Severity", value=entry.severity.title(), inline=True)
    embed.add_field(name="How to fix", value=entry.fix, inline=False)
    return branded(embed)


async def error_code_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    typed = current.strip().upper()
    return [
        app_commands.Choice(name=f"{code} - {entry.title}"[:100], value=code)
        for code, entry in CODES.items()
        if typed in code or typed.lower() in entry.title.lower()
    ][:25]


class Debug(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="debug", description="Full diagnostic report (Dev only)")
    @has_tier("dev")
    async def debug(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)
        embeds = [
            self._overview_embed(),
            await self._health_embed(ctx.guild),
            self._config_embed(),
            self._activity_embed(),
        ]
        await ctx.send(embeds=embeds, ephemeral=True)

    @commands.hybrid_command(name="syscheck", description="Check every command and system; problems get an error code")
    @app_commands.describe(code="Look up one error code, e.g. FJ-DB-001")
    @app_commands.autocomplete(code=error_code_autocomplete)
    @has_tier("dev")
    async def syscheck(self, ctx: commands.Context, code: str | None = None):
        if code:
            await ctx.send(embed=build_code_embed(code), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        report = await syscheck_module.run_all(self.bot)
        for finding in report.findings:
            logger.warning("syscheck %s %s: %s", finding.code, CODES[finding.code].title, finding.detail)
        await ctx.send(embed=build_report_embed(report), ephemeral=True)

    @commands.hybrid_command(name="health", description="Quick liveness summary: database, gateway, uptime.")
    @has_tier("dev")
    async def health(self, ctx: commands.Context):
        """The short version of /debug, for a fast check that everything is up."""
        await ctx.defer(ephemeral=True)

        database_ok, database_detail = await database.check_connection()
        gateway_ok = self.bot.is_ready() and not self.bot.is_closed()
        latency = "n/a" if math.isnan(self.bot.latency) else f"{self.bot.latency * 1000:.0f}ms"

        overall_ok = database_ok and gateway_ok
        embed = base_embed(
            f"{TICK if overall_ok else WARN}  Health",
            SUCCESS_COLOR if overall_ok else WARNING_COLOR,
        )
        embed.add_field(name="Gateway", value=f"{TICK if gateway_ok else CROSS} {latency}", inline=True)
        embed.add_field(name="Database", value=f"{TICK if database_ok else CROSS} {clamp(database_detail, 100)}", inline=True)
        embed.add_field(name="Uptime", value=format_duration(diagnostics.uptime_seconds()), inline=True)
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Build",
            value=f"`{config.APP_VERSION}` / `{config.GIT_COMMIT[:7]}`",
            inline=True,
        )
        stats = database.pool_stats()
        if "size" in stats:
            embed.add_field(
                name="DB pool",
                value=f"{stats['idle']}/{stats['size']} idle (max {stats['max_size']})",
                inline=True,
            )
        await ctx.send(embed=embed, ephemeral=True)

    def _overview_embed(self) -> discord.Embed:
        embed = base_embed("\U0001F6E0\uFE0F  Debug - Overview", NEUTRAL_COLOR)

        version_lines = [
            f"Version `{config.APP_VERSION}`",
            f"Commit `{config.GIT_COMMIT[:12]}`",
            f"discord.py `{discord.__version__}`",
            f"Python `{platform.python_version()}`",
            f"Platform `{platform.system()} {platform.machine()}`",
            f"Container `{'yes' if in_container() else 'no'}`",
        ]
        embed.add_field(name="Build", value="\n".join(version_lines), inline=True)

        embed.add_field(
            name="Uptime",
            value=format_duration(diagnostics.uptime_seconds()),
            inline=True,
        )
        embed.add_field(
            name="Latency",
            value="n/a (no heartbeat yet)" if math.isnan(self.bot.latency) else f"{self.bot.latency * 1000:.0f}ms",
            inline=True,
        )

        embed.add_field(
            name="Scope",
            value=(
                f"Guilds: **{len(self.bot.guilds)}**\n"
                f"Users cached: **{len(self.bot.users)}**\n"
                f"Commands loaded: **{len(self.bot.commands)}**"
            ),
            inline=True,
        )

        loaded_cogs = ", ".join(sorted(self.bot.cogs.keys())) or "None"
        embed.add_field(name=f"Loaded cogs ({len(self.bot.cogs)})", value=clamp(loaded_cogs), inline=False)

        if _process is not None:
            memory_mb = _process.memory_info().rss / (1024 * 1024)
            cpu_percent = _process.cpu_percent(interval=None)  # non-blocking: uses the seeded baseline
            embed.add_field(
                name="Resource usage",
                value=(
                    f"Memory: **{memory_mb:.1f} MB**\n"
                    f"CPU: **{cpu_percent:.1f}%**\n"
                    f"Threads: **{_process.num_threads()}**\n"
                    f"PID: `{os.getpid()}`"
                ),
                inline=True,
            )
        else:
            embed.add_field(name="Resource usage", value="psutil not installed", inline=True)

        return embed

    async def _health_embed(self, guild: discord.Guild | None) -> discord.Embed:
        embed = base_embed("\U0001FA7A  Debug - Health", NEUTRAL_COLOR)

        gateway_ok = self.bot.is_ready() and not self.bot.is_closed()
        embed.add_field(
            name="Discord gateway",
            value=f"{TICK if gateway_ok else CROSS} {'Connected' if gateway_ok else 'Not connected'}",
            inline=True,
        )

        db_ok, db_detail = await database.check_connection()
        embed.add_field(name="Database", value=f"{TICK if db_ok else CROSS} {clamp(db_detail, 200)}", inline=True)

        stats = database.pool_stats()
        if "size" in stats:
            pool_value = (
                f"In use: **{stats['size'] - stats['idle']}**\n"
                f"Idle: **{stats['idle']}**\n"
                f"Open: **{stats['size']}** (min {stats['min_size']}, max {stats['max_size']})"
            )
        else:
            pool_value = "Pool not open"
        pool_value += f"\nLast success: {format_age(stats.get('last_success_at'))}"
        if stats.get("last_failure"):
            pool_value += f"\nLast error: {clamp(stats['last_failure'], 120)}"
        embed.add_field(name="Connection pool", value=pool_value, inline=True)

        temp_ban_count = await database.get_active_temp_ban_count() if db_ok else 0
        total_cases = await database.get_total_case_count() if db_ok else 0
        blacklist_count = await database.get_blacklist_count() if db_ok else 0
        embed.add_field(
            name="Data",
            value=(
                f"Cases logged: **{total_cases}**\nActive temp-bans: **{temp_ban_count}**\n"
                f"Globally blacklisted: **{blacklist_count}**"
            ),
            inline=True,
        )

        scheduled_cog = self.bot.get_cog("ScheduledTasks")
        if scheduled_cog is not None:
            loop = scheduled_cog.expire_temp_bans
            loop_ok = loop.is_running() and not loop.failed()
            status = "Running" if loop_ok else ("Failed" if loop.failed() else "Not running")
            embed.add_field(
                name="Temp-ban expiry loop",
                value=f"{TICK if loop_ok else CROSS} {status}\nIterations: **{loop.current_loop}**",
                inline=True,
            )

        if config.HEALTH_SERVER_ENABLED:
            health_value = f"Listening on `{config.HEALTH_HOST}:{config.HEALTH_PORT}`\n`/health` and `/ready`"
        else:
            health_value = "Disabled"
        embed.add_field(name="Health endpoint", value=health_value, inline=True)

        if guild is not None:
            log_ok, log_detail = await check_log_channel(guild)
            embed.add_field(
                name="Mod-log (this server)",
                value=f"{TICK if log_ok else WARN} {clamp(log_detail, 200)}",
                inline=False,
            )

        return embed

    def _config_embed(self) -> discord.Embed:
        embed = base_embed("\u2699\uFE0F  Debug - Config", NEUTRAL_COLOR)

        # Presence and counts only - never the actual token, credentials or raw ID values.
        embed.add_field(
            name="Environment",
            value=(
                f"BOT_TOKEN: {'set' if config.BOT_TOKEN else 'MISSING'} (redacted)\n"
                f"COMMAND_PREFIX: `{config.COMMAND_PREFIX}`\n"
                f"BRAND_NAME: `{config.BRAND_NAME}`\n"
                f"LOG_LEVEL: `{config.LOG_LEVEL}` ({config.LOG_FORMAT})\n"
                f"Database: `{config.redacted_database_url()}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Database tuning",
            value=(
                f"Pool size: **{config.DB_POOL_MIN_SIZE}-{config.DB_POOL_MAX_SIZE}**\n"
                f"Command timeout: **{config.DB_COMMAND_TIMEOUT:.0f}s**\n"
                f"Acquire timeout: **{config.DB_ACQUIRE_TIMEOUT:.0f}s**\n"
                f"Query retries: **{config.DB_QUERY_MAX_RETRIES}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Access control",
            value=(
                f"Owners configured: **{len(config.OWNER_IDS)}**\n"
                f"Staff roles: **{len(config.STAFF_ROLE_IDS)}**\n"
                f"Staff Director roles: **{len(config.STAFF_DIRECTOR_ROLE_IDS)}**\n"
                f"Gov roles: **{len(config.GOV_ROLE_IDS)}**\n"
                f"Dev users/roles: **{len(config.DEV_USER_IDS)}** / **{len(config.DEV_ROLE_IDS)}**\n"
                f"Appeals: **{'on' if config.APPEALS_CHANNEL_ID else 'off'}**"
                f" (min votes {config.APPEAL_MIN_VOTES}, voter roles {len(config.APPEAL_VOTER_ROLE_IDS) or 'Staff Director+'})\n"
                f"Env log channels: **{sum(len(ids) for ids in config.LOG_CHANNEL_IDS.values())}**\n"
                f"Mute role: **{'set' if config.MUTE_ROLE_ID else 'not set'}**\n"
                f"Approved servers: **{len(config.APPROVED_GUILD_IDS) or 'all (no allowlist)'}**\n"
                f"Global-exempt servers: **{len(config.GLOBAL_ACTION_EXEMPT_GUILD_IDS) or 'none'}**\n"
                f"Auto-leave unapproved: **{config.LEAVE_UNAPPROVED_GUILDS}**\n"
                f"Protected users: **{len(config.PROTECTED_USER_IDS)}**\n"
                f"Blocked users: **{len(config.BLOCKED_USER_IDS)}**"
            ),
            inline=True,
        )

        warnings = diagnostics.validate_config()
        embed.add_field(
            name=f"Validation ({'clean' if not warnings else f'{len(warnings)} issue(s)'})",
            value=clamp("\n".join(f"{WARN} {warning}" for warning in warnings), empty=f"{TICK} No issues found."),
            inline=False,
        )
        return embed

    def _activity_embed(self) -> discord.Embed:
        embed = base_embed("\U0001F4CA  Debug - Activity", NEUTRAL_COLOR)

        top_commands, total_invocations, total_errors = diagnostics.get_command_stats()
        if top_commands:
            command_lines = "\n".join(f"`/{name}` - **{count}**" for name, count in top_commands)
        else:
            command_lines = "No commands used since last restart."
        embed.add_field(
            name=f"Command usage ({total_invocations} total, {total_errors} errors)",
            value=clamp(command_lines),
            inline=False,
        )

        recent_logs = diagnostics.get_recent_logs(limit=8)
        embed.add_field(
            name="Recent warnings/errors",
            value=clamp("\n".join(f"`{line}`" for line in recent_logs), empty="None recorded since last restart."),
            inline=False,
        )

        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Debug(bot))
