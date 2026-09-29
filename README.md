# FJUSA Mod Bot

Discord moderation bot for FJUSA, based on the NFPD Mod Bot. It uses discord.py,
PostgreSQL and Docker, and every setting comes from `.env`.

All commands work as slash commands (`/ban`) and prefix commands (`!ban`).
Start with **`/help`**. It shows only the commands your tier can use, grouped in a
dropdown. Every reply is a branded embed; set the accent colour with `BRAND_COLOR`.

Developed by **xe2b** (`1195765102725582968`).

## Tiers

Each tier gets everything below it. Staff, Staff Director and Gov are **role IDs**.
Dev is **user IDs**. `OWNER_IDS` bypass everything.

| Tier | `.env` variable | Commands |
|---|---|---|
| **Staff** | `STAFF_ROLE_IDS` | `warn` `mute` `tempmute` `unmute` `kick` `tempban` `unban` `cases` `casesearch` `purge` `slowmode` `userinfo` + right-click **User Profile** |
| **Staff Director** | `STAFF_DIRECTOR_ROLE_IDS` | + `ban` `caseedit` `casedelete` `caseexport` `modstats` `lockdown` `unlock` `addlockdownrole` `removelockdownrole` `clearlockdownroles` + **appeal votes** (default voters) |
| **Gov** | `GOV_ROLE_IDS` | + `blacklist` `globalban` `globalunban` `globalkick` `globalmute` `globalunmute` `globalblacklist list/add/remove/check` `globalannounce` `globallockdown` `globalunlock` `settings` `setlogchannel` `setserverlogchannel` `setannouncechannel` `testlog` `setraidprotection` `setwarnthresholds` `backupserver` `restorebackup` |
| **Dev** | `DEV_ROLE_IDS` / `DEV_USER_IDS` | + `syscheck` `debug` `health` `servers` `addrole` `setlogo` |

Anyone can run `/help`.

### Mutes
- `/mute` gives the role set in `MUTE_ROLE_ID` and lasts until `/unmute`.
- `/tempmute` uses Discord's timeout (max 28 days).
- `/unmute` removes both the role and any timeout.

### Durations and reasons
`/tempmute`, `/tempban` and `/globalmute` take human durations: `30m`, `2h`,
`1d12h`, `1w`. A bare number means minutes, and suggestions appear as you type.
The reason box suggests common reasons from `REASON_PRESETS`, but you can type
anything.

### User profiles
`/userinfo @user`, or right-click a user → **Apps → User Profile**, shows:
- account age, with new accounts flagged
- when they joined, their roles and their bot tier
- active punishments: timeout, mute role, ban or tempban expiry, global blacklist
- whether they're protected
- their case breakdown and last 3 cases

### Ban types

| Command | Length | Appeal | DM buttons |
|---|---|---|---|
| `/tempban` (Staff+) | Ends on its own | 🟢 Yes | Submit an appeal |
| `/ban` (Staff Director+) | Permanent | 🟢 Yes | Submit an appeal |
| `/blacklist` (Gov+) | Permanent | 🔴 **Final** | Message Developer |
| `/globalban` / global blacklist (Gov+) | Permanent, every server | 🔴 **Final** | Message Developer |

"Message Developer" opens a DM with xe2b, for people who think a blacklist
was staff abuse. Only Gov+ can `/unban` a blacklisted user, and a `/ban` or
`/tempban` can't overwrite a blacklist. A permanent ban or blacklist also clears any running tempban,
so the old expiry can't lift it. `/ban`, `/tempban` and `/blacklist` work on
users who aren't in the server.

### Appeals
When `APPEALS_CHANNEL_ID` is set, tempban and ban DMs get a **Submit an appeal**
button:
1. The user fills in a short form.
2. The appeal is posted in the appeals channel. The bot fills in their real user
   ID, account age (new accounts are flagged), previous appeals, case count, the
   ban reason and the ban type, so none of it can be faked on the form.
3. `APPEAL_ALERT_CHANNEL_ID` gets **"A ban appeal has been sent to #appeals"**
   with a jump link, pinging the voter roles (`APPEAL_PING_VOTERS`).
4. Members with `APPEAL_VOTER_ROLE_IDS` (default Staff Director+) press
   🟢 **Approve unban** or 🔴 **Deny unban**. Pressing the same button again
   withdraws the vote; pressing the other switches it. The embed shows who
   voted which way.
5. Once **`APPEAL_MIN_VOTES` (default 3)** votes are in, the majority wins; a
   tie waits for one more vote. The user is DMed **"The FJUSA Ban Team has
   reviewed your case and approved/denied your appeal"** (`APPEAL_TEAM_NAME`).
   Approved: they're unbanned, a case is logged, and the DM has a one-use
   **Rejoin** invite. Denied: the DM says when they can appeal again. The
   result is also posted in the alert channel.

If the user is blacklisted while their appeal is open, it closes automatically.
Spam limits: the user must still be banned, only
one open appeal per ban, and a cooldown after a denial (`APPEAL_COOLDOWN_DAYS`).

### What the user is told
The user gets a DM for every action taken on them, e.g. **"🔇 You were muted in
FJUSA"**. Each DM shows the reason, the duration and when it ends, the case
number, and the logo in the corner. Warnings say which warning number it is.
Unbans, expired tempbans and accepted appeals include a **Rejoin** button with a
fresh one-use invite. Automatic warn escalations, global actions and blacklist
auto-bans all send DMs too. The staff reply shows whether the DM got through.

### Global commands (Gov+)
These apply to every server in `APPROVED_GUILD_IDS` (or every server the bot is
in when that's empty). Servers in `GLOBAL_ACTION_EXEMPT_GUILD_IDS`, such as an
appeals server, are always skipped.

- **`/globalban`** bans the user everywhere and adds them to the **global
  blacklist**. **`/globalunban`** lifts the bans and removes them from the list.
- **Global blacklist:** anyone on it is banned when they join any covered server.
  When the bot joins a new server, it bans everyone on the list there. Manage it
  with `/globalblacklist add | remove | list | check`.
- **`/globalannounce`** posts an embed in each server's announcement channel (set
  it with `/setannouncechannel`; otherwise it uses the mod-log channel).
- **`/globallockdown`** locks every text channel in every server.
  **`/globalunlock`** puts back exactly the permissions each channel had before.

### Protection
- `PROTECTED_USER_IDS` and all owners can never be moderated through the bot.
- With `APPROVED_GUILD_IDS` and `LEAVE_UNAPPROVED_GUILDS=true`, the bot posts a
  notice and leaves any server it isn't approved for, then DMs the owners.
- `BLOCKED_USER_IDS` can't use the bot at all.

### Logo
The FJUSA logo (`assets/fjusa-logo.png`) sits in the corner of DMs, `/help` and
announcements, and in the footer of every embed. Run **`/setlogo`** once (Dev) to
make it the bot's avatar, which is where embeds read it from. Alternatively, set
`LOGO_URL` to a hosted copy.

### Logs
Each kind of log can go to its own channel, set in `.env` with one channel ID per
server:

| Log | Variable | What goes there |
|---|---|---|
| 🔨 Mod Logs | `MOD_LOGS_CHANNEL_IDS` | Cases, lockdowns, purges |
| 💬 Chat Logs | `CHAT_LOGS_CHANNEL_IDS` | Edited and deleted messages |
| 🚪 Join Logs | `JOIN_LOGS_CHANNEL_IDS` | Members joining and leaving |
| 👤 Member Logs | `MEMBER_LOGS_CHANNEL_IDS` | Role and nickname changes, bans, unbans |
| 🎧 Voice Logs | `VOICE_LOGS_CHANNEL_IDS` | Voice joins, moves, leaves |
| 🏗️ Server Logs | `SERVER_LOGS_CHANNEL_IDS` | Channel, role and invite changes |
| 🛡️ Security Logs | `SECURITY_LOGS_CHANNEL_IDS` | Raid and alt-account alerts |

For each event, the bot uses whichever channel in the list belongs to that server,
and tags the entry with its log name. Logs are laid out like Quark's: the user at the top, what happened, then **Key**: value lines, a **WARNING!** box when a role with dangerous permissions is given, and a **User ID** button to copy the ID. They also say **who did it and to whom**, e.g.
**"xe2b (ID 1195...) gave @Staff to hf0u (ID 564...)"**. This covers role and
nickname changes, kicks (a kick is logged as a kick, not a leave), bans, unbans
and channel/role changes. The bot reads this from Discord's audit log, so it needs
the **View Audit Log** permission. Anything left empty falls back to
`/setlogchannel` (Mod Logs) or `/setserverlogchannel` (everything else).
`/settings` shows where each kind goes, and `/testlog` sends a test to all of them.

### System check
**`/syscheck`** (Dev) checks:
- your config, the database and the Discord connection
- that every command loaded, synced and has a tier check
- the bot's permissions and role position in each server
- configured roles, log channels, appeals, background tasks and the logo

Every problem gets a preset code with its fix, e.g. `🔴 FJ-PERM-001 Bot is missing
permissions`. Look one up with `/syscheck code:FJ-PERM-001`, or see the full list in
[docs/ERROR_CODES.md](docs/ERROR_CODES.md). The same codes appear in the startup logs.

Every action is recorded as a numbered case, posted to the mod-log channel
(`/setlogchannel`), and DMed to the user where possible.

## Setup

1. Create an application at <https://discord.com/developers/applications>, add a
   bot, and turn on the **Server Members** and **Message Content** intents.
2. Invite the bot with the `bot` and `applications.commands` scopes. Give it
   Administrator, or at least Ban/Kick/Moderate Members, Manage Roles, Manage
   Channels, Manage Messages and View Audit Log. Put its role **above** your
   Staff and Muted roles.
3. Configure and run it on your Linux server:

   ```bash
   git clone https://github.com/montygulzar/FJ.git && cd FJ
   cp .env.example .env && nano .env    # token, role IDs, MUTE_ROLE_ID, database...
   chmod 600 .env
   docker network create fjusa-net      # once
   # start Postgres once - see DEPLOYMENT.md
   docker compose up -d --build
   docker compose logs -f
   ```

`.env.example` documents every variable. See [DEPLOYMENT.md](DEPLOYMENT.md) for
creating the database, updating, backups and troubleshooting.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest pytest-asyncio
BOT_TOKEN=x DATABASE_URL=postgresql://u:p@localhost/db pytest
# Also run the database tests against a throwaway Postgres:
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/fjusa_test BOT_TOKEN=x DATABASE_URL=postgresql://u:p@localhost/db pytest
```
