# FJUSA Mod Bot

Discord moderation bot for FJUSA, based on the NFPD Mod Bot. It uses discord.py,
PostgreSQL and Docker, and every setting comes from `.env`.

All commands work as slash commands (`/ban`) and prefix commands (`!ban`).

## Tiers

Each tier gets everything below it. Staff, Staff Director and Gov are **role IDs**.
Dev is **user IDs**. `OWNER_IDS` bypass everything.

| Tier | `.env` variable | Commands |
|---|---|---|
| **Staff** | `STAFF_ROLE_IDS` | `warn` `mute` `tempmute` `unmute` `kick` `tempban` `unban` `cases` `casesearch` `purge` `slowmode` |
| **Staff Director** | `STAFF_DIRECTOR_ROLE_IDS` | + `ban` `caseedit` `casedelete` `caseexport` `modstats` `lockdown` `unlock` `addlockdownrole` `removelockdownrole` `clearlockdownroles` |
| **Gov** | `GOV_ROLE_IDS` | + `globalban` `globalunban` `globalkick` `globalmute` `globalunmute` `globalblacklist list/add/remove/check` `globalannounce` `globallockdown` `globalunlock` `settings` `setlogchannel` `setserverlogchannel` `setannouncechannel` `testlog` `testserverlog` `setraidprotection` `setwarnthresholds` `backupserver` `restorebackup` |
| **Dev** | `DEV_USER_IDS` | + `debug` `health` `servers` `addrole` |

### Mutes
- `/mute` gives the role set in `MUTE_ROLE_ID` and lasts until `/unmute`.
- `/tempmute` uses Discord's timeout for a set number of minutes (max 28 days).
- `/unmute` removes both the role and any timeout.

### Temp bans
`/tempban` lifts itself when it expires. Expiry times are stored in Postgres, so
restarts don't lose them.

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
