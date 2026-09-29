"""Every embed the bot sends is built here, so the whole bot shares one look.

Design rules:
- One accent colour (BRAND_COLOR) for neutral/info embeds; green/red/amber only
  carry meaning (success, failure/danger, warning).
- Every embed ends with the branded footer (bot name + avatar) and a timestamp.
- Case embeds read top to bottom: what happened, to whom, by whom, why.
"""
import re
from datetime import datetime, timedelta
from typing import NamedTuple

import discord

from config import APPEAL_URL, BRAND_COLOR, BRAND_NAME, LOGO_URL, SERVER_DISPLAY_NAME


class ActionStyle(NamedTuple):
    color: int
    icon: str
    title: str
    dm_line: str


NEUTRAL_COLOR = BRAND_COLOR
SUCCESS_COLOR = 0x22C55E
DANGER_COLOR = 0xEF4444
WARNING_COLOR = 0xF59E0B
MUTED_COLOR = 0x64748B

SUCCESS_ICON = "✅"
ERROR_ICON = "❌"
WARNING_ICON = "⚠️"

ACTION_STYLES = {
    "warn":          ActionStyle(0xF59E0B, "⚠️", "Warning",       "You were warned in {location}."),
    "mute":          ActionStyle(0xF97316, "\U0001F507", "Mute",            "You were muted in {location}."),
    "tempmute":      ActionStyle(0xFB923C, "⏲️", "Temp Mute",     "You were temporarily muted in {location}."),
    "unmute":        ActionStyle(SUCCESS_COLOR, "\U0001F50A", "Unmute",     "You were unmuted in {location}."),
    "kick":          ActionStyle(0xEA580C, "\U0001F462", "Kick",            "You were kicked from {location}."),
    "ban":           ActionStyle(DANGER_COLOR, "\U0001F528", "Ban",         "You were banned from {location}."),
    "tempban":       ActionStyle(0xDC2626, "⏳", "Temp Ban",            "You were temporarily banned from {location}."),
    "unban":         ActionStyle(SUCCESS_COLOR, "\U0001F513", "Unban",      "You were unbanned from {location}."),
    "global_mute":   ActionStyle(0xC2410C, "\U0001F507", "Global Mute",     "You were muted across all {network} servers."),
    "global_unmute": ActionStyle(SUCCESS_COLOR, "\U0001F50A", "Global Unmute", "You were unmuted across all {network} servers."),
    "global_kick":   ActionStyle(0xB91C1C, "\U0001F462", "Global Kick",     "You were removed from all {network} servers."),
    "global_ban":    ActionStyle(0x991B1B, "\U0001F310", "Global Ban",      "You were banned across all {network} servers."),
    "global_unban":  ActionStyle(SUCCESS_COLOR, "\U0001F513", "Global Unban", "You were unbanned across all {network} servers."),
    "blacklist":     ActionStyle(0x7F1D1D, "\u26D4", "Blacklist",         "You were blacklisted from {location}."),
}

FALLBACK_STYLE = ActionStyle(NEUTRAL_COLOR, "\U0001F4CB", "Action", "")

# Set once at startup via set_brand_icon() so every embed carries the bot's own avatar.
BRAND_ICON_URL: str | None = None

AUDIT_REASON_LIMIT = 512
EMBED_FIELD_LIMIT = 1024
EMBED_DESCRIPTION_LIMIT = 4096



def set_brand_icon(url: str) -> None:
    global BRAND_ICON_URL
    BRAND_ICON_URL = url


def logo_url() -> str | None:
    """The FJUSA logo: LOGO_URL if configured, otherwise the bot's avatar (see /setlogo)."""
    return LOGO_URL or BRAND_ICON_URL


def clamp(text: str | None, limit: int = EMBED_FIELD_LIMIT, *, empty: str = "*Not specified*") -> str:
    """Discord rejects embed fields that are empty or over the character limit."""
    if not text or not text.strip():
        return empty
    if len(text) <= limit:
        return text
    if limit <= 3:
        # No room for the ellipsis, and `text[:limit - 3]` would slice from the end
        # and return more characters than the limit allows.
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def audit_reason(actor: discord.abc.User, action_label: str, reason: str) -> str:
    """Build a moderator-attributed reason string that fits Discord's audit-log limit.

    The user ID is included alongside the name so audit log entries remain attributable
    even if the moderator later changes their username.
    """
    prefix = f"{action_label} by {actor} ({actor.id}): "
    available = AUDIT_REASON_LIMIT - len(prefix)
    if available <= 0:
        return clamp(reason, AUDIT_REASON_LIMIT, empty="No reason provided")
    return prefix + clamp(reason, available, empty="No reason provided")


def style_for(action_type: str) -> ActionStyle:
    return ACTION_STYLES.get(action_type, FALLBACK_STYLE)


def format_timestamp(iso_string: str, style: str = "f") -> str:
    """Render a stored ISO timestamp as a Discord timestamp that respects the viewer's timezone."""
    try:
        parsed = datetime.fromisoformat(iso_string)
    except (TypeError, ValueError):
        return iso_string
    return discord.utils.format_dt(parsed, style=style)


def format_duration(delta: timedelta) -> str:
    """timedelta(days=1, hours=2) -> '1 day, 2 hours'. Shows at most the two largest units."""
    seconds = int(delta.total_seconds())
    parts = []
    for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount} {unit}{'' if amount == 1 else 's'}")
    return ", ".join(parts[:2]) or "less than a minute"


def branded(embed: discord.Embed, *, footer_prefix: str | None = None) -> discord.Embed:
    """Apply the shared footer and timestamp. Every outgoing embed should pass through here."""
    text = f"{footer_prefix}  •  {BRAND_NAME}" if footer_prefix else BRAND_NAME
    embed.set_footer(text=text, icon_url=logo_url())
    if embed.timestamp is None:
        embed.timestamp = discord.utils.utcnow()
    return embed


def base_embed(title: str, color: int, description: str | None = None) -> discord.Embed:
    return branded(discord.Embed(title=title, description=description, color=color))


def detail_line(key: str | None, value) -> str:
    """**Key**: value - or, for multi-line values, **Key** as a heading above them."""
    if not key:
        return str(value)
    value = str(value)
    return f"**{key}**\n{value}" if "\n" in value.strip() else f"**{key}**: {value}"


def details_text(details) -> str:
    """("Key", value) pairs as detail lines. None keys print the value alone;
    None/empty values are skipped."""
    return "\n".join(detail_line(key, value) for key, value in details if value)


def card(
    title: str,
    color: int,
    *,
    user: discord.abc.User | None = None,
    author: tuple[str, str | None] | None = None,
    body: str | None = None,
    details=(),
    warning: str | None = None,
    thumbnail: str | None = None,
    footer: str | None = None,
) -> discord.Embed:
    """The house style (after Quark): who it's about on top, a titled headline, the
    story in plain words, then **Key**: value lines. Reads cleanly on mobile, where
    side-by-side fields stack awkwardly."""
    parts = [body] if body else []
    lines = details_text(details)
    if lines:
        parts.append(lines)
    if warning:
        parts.append(f"**WARNING!**\n```diff\n{warning}\n```")
    embed = discord.Embed(
        title=title[:256],
        description=clamp("\n\n".join(parts), EMBED_DESCRIPTION_LIMIT, empty="") or None,
        color=color,
    )
    if user is not None:
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
    elif author is not None:
        embed.set_author(name=author[0][:256], icon_url=author[1])
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return branded(embed, footer_prefix=footer)


_DETAIL_LINE = re.compile(r"^\*\*[^*\n]+\*\*(:|$)", re.M)


def add_detail(embed: discord.Embed, key: str, value) -> discord.Embed:
    """Append one detail line to an embed. The first one after an intro sentence gets
    a blank line above it, like the rest of the house style."""
    line = detail_line(key, value)
    description = embed.description or ""
    if not description:
        embed.description = clamp(line, EMBED_DESCRIPTION_LIMIT)
        return embed
    separator = "\n" if _DETAIL_LINE.search(description) else "\n\n"
    embed.description = clamp(description + separator + line, EMBED_DESCRIPTION_LIMIT)
    return embed


def when(moment: datetime) -> str:
    """'<full date> (<in 2 hours>)' - absolute and relative, in the viewer's timezone."""
    return f"{discord.utils.format_dt(moment, 'f')} ({discord.utils.format_dt(moment, 'R')})"


def ref(user: discord.abc.User) -> str:
    """'@mention (`id`)'."""
    return f"{user.mention} (`{user.id}`)"


def build_notice_embed(message: str, *, success: bool = True, title: str | None = None) -> discord.Embed:
    """The one-line reply every command uses for confirmations and refusals."""
    icon = SUCCESS_ICON if success else ERROR_ICON
    embed = discord.Embed(
        title=f"{icon}  {title}" if title else None,
        description=clamp(message if title else f"{icon}  {message}", EMBED_DESCRIPTION_LIMIT, empty="Done."),
        color=SUCCESS_COLOR if success else DANGER_COLOR,
    )
    return branded(embed)


def build_case_embed(
    action_type: str,
    target: discord.abc.User,
    moderator: discord.abc.User,
    reason: str,
    case_id: int,
    *,
    duration: timedelta | None = None,
    expires_at: datetime | None = None,
) -> discord.Embed:
    style = style_for(action_type)
    return card(
        f"{style.icon}  {style.title}",
        style.color,
        user=target,
        details=[
            ("User", ref(target)),
            ("Moderator", ref(moderator)),
            ("Duration", format_duration(duration) if duration is not None else None),
            ("Expires", when(expires_at) if expires_at is not None else None),
            ("Reason", clamp(reason, 1000)),
        ],
        footer=f"Case #{case_id}  \u2022  ID: {target.id}",
    )


def dm_headline(action_type: str, location_name: str) -> str:
    """'You were muted in FJUSA' - the sentence a DM leads with."""
    style = style_for(action_type)
    line = style.dm_line.format(location=location_name, network=SERVER_DISPLAY_NAME).rstrip(".")
    return f"{style.icon}  {line or style.title}"


def _dm_author(guild: discord.Guild | None) -> tuple[str, str | None]:
    if guild is not None:
        return guild.name, guild.icon.url if guild.icon else None
    return f"{SERVER_DISPLAY_NAME} Network", logo_url()


def build_dm_notice_embed(
    action_type: str,
    location_name: str,
    reason: str,
    *,
    guild: discord.Guild | None = None,
    expires_at: datetime | None = None,
    duration: timedelta | None = None,
    case_id: int | None = None,
    note: str | None = None,
) -> discord.Embed:
    """What the member receives: a headline, the reason, and when it ends."""
    style = style_for(action_type)
    return card(
        dm_headline(action_type, location_name),
        style.color,
        author=_dm_author(guild),
        body=note,
        details=[
            ("Reason", clamp(reason, 1000)),
            ("Duration", format_duration(duration) if duration is not None else None),
            ("Ends", when(expires_at) if expires_at is not None else None),
            ("Case", f"`#{case_id}`" if case_id is not None else None),
        ],
        thumbnail=logo_url(),
        footer="Automated notice",
    )


def build_summary_embed(
    action_type: str,
    user: discord.abc.User,
    affected: list[str],
    failed: list[str],
) -> discord.Embed:
    style = style_for(action_type)
    return card(
        f"{style.icon}  {style.title}",
        style.color,
        user=user,
        body=f"Applied to {ref(user)} in **{len(affected)}** server(s).",
        details=[
            ("Servers", clamp(", ".join(affected), 1500) if affected else "*none*"),
            (f"{WARNING_ICON} Skipped - missing permissions", clamp(", ".join(failed), 1000) if failed else None),
        ],
        footer=f"ID: {user.id}",
    )


def build_case_line(row, guild: discord.Guild) -> tuple[str, str]:
    """One case rendered as an embed field name/value pair."""
    style = style_for(row["action_type"])
    moderator = guild.get_member(row["moderator_id"])
    moderator_name = moderator.mention if moderator else f"`{row['moderator_id']}`"
    name = f"{style.icon}  Case #{row['id']}  •  {style.title}"
    reason = clamp(row["reason"], limit=800)
    value = f"{reason}\n{moderator_name}  •  {format_timestamp(row['created_at'], 'R')}"
    return name, value


BAN_KINDS = ("tempban", "ban", "blacklist", "global")


def build_ban_dm_embed(
    reason: str,
    *,
    kind: str = "ban",
    guild: discord.Guild | None = None,
    unban_at: str | None = None,
    can_appeal_here: bool = False,
    can_contact_developer: bool = False,
) -> discord.Embed:
    """The DM sent before a ban lands.

    kind:
      tempban   - ends on its own, can be appealed
      ban       - permanent, can be appealed
      blacklist - permanent and final, cannot be appealed
      global    - blacklisted from every server, final
    """
    if kind not in BAN_KINDS:
        raise ValueError(f"Unknown ban kind {kind!r}")
    location = guild.name if guild is not None else SERVER_DISPLAY_NAME
    action_type = {"tempban": "tempban", "ban": "ban", "blacklist": "blacklist", "global": "global_ban"}[kind]
    headline = (
        f"\U0001F310  You were blacklisted from all {SERVER_DISPLAY_NAME} servers"
        if kind == "global"
        else dm_headline(action_type, location)
    )
    if kind == "tempban":
        duration = unban_at or "Temporary"
    elif kind == "ban":
        duration = "Permanent - until an appeal is accepted"
    else:
        duration = "\u26D4 Permanent and **final** - this ban cannot be appealed"

    if kind in ("blacklist", "global"):
        next_step = (
            ("Staff abuse?", "If you believe this was an abuse of power, press **Message Developer** below.")
            if can_contact_developer else (None, None)
        )
    elif can_appeal_here:
        next_step = ("Appeal", "Think this was a mistake? Press **Submit an appeal** below and staff will review it.")
    elif APPEAL_URL:
        next_step = ("Appeal", f"You can appeal in the [{SERVER_DISPLAY_NAME} Appeals server]({APPEAL_URL}).")
    else:
        next_step = (None, None)

    return card(
        headline,
        style_for(action_type).color,
        author=_dm_author(guild if kind != "global" else None),
        details=[("Reason", clamp(reason, 1000)), ("Duration", duration), next_step],
        thumbnail=logo_url(),
        footer="Automated notice",
    )


def build_appeal_receipt_dm(appeal_id: int, guild: discord.Guild | None, team: str, answer: str) -> discord.Embed:
    """Sent when an appeal is submitted, so the user has a record of it."""
    return card(
        "\U0001F4E8  Appeal Received",
        NEUTRAL_COLOR,
        author=_dm_author(guild),
        body=(
            f"Thanks - the **{team}** has your appeal and will review your case.\n"
            "You'll get a DM from me as soon as there's a decision."
        ),
        details=[
            ("Appeal", f"`#{appeal_id}`"),
            ("Status", "\u23F3 Under review"),
            ("What you wrote", f"\n>>> {clamp(answer, 1500)}"),
        ],
        thumbnail=logo_url(),
        footer=team,
    )


def build_appeal_decision_dm(
    appeal_id: int,
    guild: discord.Guild | None,
    team: str,
    approved: bool,
    *,
    retry_at: datetime | None = None,
) -> discord.Embed:
    """"The FJUSA Ban Team has reviewed your case and approved/denied your appeal." """
    where = f"**{guild.name}**" if guild else "the server"
    if approved:
        body = (
            f"The **{team}** has reviewed your case and **approved** your appeal. \U0001F7E2\n"
            f"Your ban from {where} has been lifted."
        )
        details = [("Appeal", f"`#{appeal_id}`"), ("Result", "\U0001F7E2 Approved"),
                   ("Next steps", "Use **Rejoin** below if it's there, and please follow the rules.")]
    else:
        body = (
            f"The **{team}** has reviewed your case and **denied** your appeal. \U0001F534\n"
            f"Your ban from {where} stays in place."
        )
        details = [("Appeal", f"`#{appeal_id}`"), ("Result", "\U0001F534 Denied"),
                   ("Appeal again", when(retry_at) if retry_at else "Now")]
    return card(
        "\u2696\uFE0F  Appeal Approved" if approved else "\u2696\uFE0F  Appeal Denied",
        SUCCESS_COLOR if approved else DANGER_COLOR,
        author=_dm_author(guild),
        body=body,
        details=details,
        thumbnail=logo_url(),
        footer=team,
    )
