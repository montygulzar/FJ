# /syscheck error codes

Run `/syscheck` (Dev) to check the whole bot: config, database, Discord connection,
every command, permissions in each server, roles, log channels, appeals and background
tasks. Each problem gets one of these codes. `/syscheck code:FJ-DB-001` explains a
single code in Discord. The same codes appear in the startup logs.

🔴 error: something is broken  ·  🟡 warning: works, but should be fixed  ·  🔵 info: optional


## Configuration (.env)

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-CFG-001` | 🔴 | No owner configured | Set OWNER_IDS in .env to your Discord user ID, then restart the bot. |
| `FJ-CFG-002` | 🔴 | No staff tiers configured | Set STAFF_ROLE_IDS / STAFF_DIRECTOR_ROLE_IDS / GOV_ROLE_IDS in .env - until then only owners can use commands. |
| `FJ-CFG-003` | 🟡 | Auto-leave is on with no approved servers | Add your server IDs to APPROVED_GUILD_IDS, or set LEAVE_UNAPPROVED_GUILDS=false - otherwise the bot leaves everywhere. |
| `FJ-CFG-004` | 🟡 | User is both protected and blocked | Remove the user from either PROTECTED_USER_IDS or BLOCKED_USER_IDS. |
| `FJ-CFG-005` | 🟡 | Exempt server isn't approved | Every GLOBAL_ACTION_EXEMPT_GUILD_IDS entry should also be in APPROVED_GUILD_IDS. |
| `FJ-CFG-006` | 🟡 | No mute role set | Set MUTE_ROLE_ID in .env so /mute works. /tempmute works without it. |
| `FJ-CFG-007` | 🔵 | No approved server list | Global actions reach every server the bot is in. Set APPROVED_GUILD_IDS to limit them to yours. |
| `FJ-CFG-008` | 🟡 | Appeal alert channel misconfigured | APPEAL_ALERT_CHANNEL_ID needs APPEALS_CHANNEL_ID set too, and must be a different channel. |

## Database

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-DB-001` | 🔴 | Database unreachable | Check the Postgres container is running and on the same Docker network, and that DATABASE_URL is right. |
| `FJ-DB-002` | 🟡 | Database is slow | Queries are taking over 250ms. Check the host's load and the Postgres container's resources. |
| `FJ-DB-003` | 🔴 | Database tables missing | Restart the bot so it recreates its schema; if it persists, the DB user may lack CREATE rights. |

## Discord connection

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-GW-001` | 🟡 | High Discord latency | Gateway latency is over 1 second. Usually Discord-side or host network; restart if it persists. |
| `FJ-GW-002` | 🟡 | Bot isn't in any server | Invite the bot to your server(s) with the bot and applications.commands scopes. |

## Commands

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-CMD-001` | 🔴 | A command module failed to load | Its commands are unavailable. Check `docker compose logs` for the traceback at startup. |
| `FJ-CMD-002` | 🟡 | Slash commands not synced | Discord rejected or rate-limited the sync. Restart the bot later; prefix commands still work. |
| `FJ-CMD-003` | 🔴 | Command has no tier check | A moderation command can be run by anyone. It needs a @has_tier(...) check - report this to the developer. |
| `FJ-CMD-004` | 🟡 | Command has been failing | The command errored since the last restart. Check `docker compose logs` for the traceback. |

## Bot permissions

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-PERM-001` | 🔴 | Bot is missing permissions | Give the bot's role the listed permissions (or Administrator) in Server Settings > Roles. |
| `FJ-PERM-002` | 🔴 | Mute role is above the bot | Drag the bot's role above the mute role in Server Settings > Roles, or /mute will fail. |

## Roles

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-ROLE-001` | 🟡 | Configured role doesn't exist | A role ID in .env isn't in any server the bot is in. Re-copy the role ID (right-click > Copy ID). |
| `FJ-ROLE-002` | 🟡 | Mute role missing in a server | MUTE_ROLE_ID isn't a role in this server, so /mute won't work here. Mute roles are per server. |

## Log channels

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-LOG-001` | 🟡 | No Mod Logs channel | Cases aren't being logged. Set MOD_LOGS_CHANNEL_IDS in .env or run /setlogchannel. |
| `FJ-LOG-002` | 🔴 | Bot can't post in a log channel | Give the bot View Channel, Send Messages and Embed Links in the listed channel. |
| `FJ-LOG-003` | 🟡 | Log channel ID not found | A *_LOGS_CHANNEL_IDS entry isn't a channel in any server the bot is in. Re-copy the channel ID. |
| `FJ-LOG-004` | 🔵 | Log kind not set up | These logs aren't going anywhere. Optional - set the matching *_LOGS_CHANNEL_IDS if you want them. |

## Ban appeals

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-APL-001` | 🔴 | Appeals channel unreachable | APPEALS_CHANNEL_ID isn't a channel the bot can see. Appeal buttons are hidden until it's fixed. |
| `FJ-APL-002` | 🔴 | Bot can't post in the appeals channel | Give the bot View Channel, Send Messages and Embed Links in the appeals channel. |
| `FJ-APL-003` | 🟡 | No voter roles in the appeals server | None of APPEAL_VOTER_ROLE_IDS exist in the appeals channel's server, so only owners can vote. |
| `FJ-APL-004` | 🟡 | Appeal alert channel unreachable | APPEAL_ALERT_CHANNEL_ID isn't a channel the bot can post in, so nobody is told about new appeals. |
| `FJ-APL-005` | 🔵 | In-Discord appeals are off | Optional. Set APPEALS_CHANNEL_ID to let banned users appeal from their DMs. |

## Background tasks

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-TASK-001` | 🔴 | Temp-ban expiry has stopped | Temp bans won't lift on time. Restart the bot and check the logs for the error. |

## Branding

| Code | | Problem | Fix |
|---|---|---|---|
| `FJ-BRD-001` | 🔵 | Logo not set | Run /setlogo once, or set LOGO_URL, so embeds show the FJUSA logo. |
