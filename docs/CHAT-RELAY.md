# Chat relay

Tiny RCON-based relay: global chat on any map shows up on every other map as
`SERVER: [Map] Name: message`, and (optionally) in a Discord channel. Messages
typed in that Discord channel are pushed to all maps as `[Discord] Name: message`.

Runs as the `obelisk` service in the generated stack. Configure it through the settings
store (or the `.env` Obelisk generates):

```
DISCORD_TOKEN=          # from https://discord.com/developers/applications -> Bot -> Reset Token
DISCORD_CHANNEL_ID=     # right-click the channel -> Copy Channel ID (enable Developer Mode in Discord settings)
```

Discord bot checklist (one-time):
1. https://discord.com/developers/applications -> New Application -> name it (e.g. ACME Ark Chat).
2. Bot tab -> Reset Token -> copy it into DISCORD_TOKEN. Turn ON "Message Content Intent".
3. OAuth2 -> URL Generator: scope `bot`, permissions `View Channels`, `Send Messages`,
   `Read Message History`. Open the generated URL and invite the bot to the ACME server.
4. Create/pick the relay channel, copy its ID into DISCORD_CHANNEL_ID.
5. Compose Down/Up the stack (or just restart the `obelisk` container).

Optional extras (all in the stack's `.env`):

```
DISCORD_TRIBELOG_CHANNEL_ID=   # tribe logs (tames, deaths, structures) -> this channel, prefixed [Map]
DISCORD_ADMIN_CHANNEL_ID=      # AdminCmd lines + "map down/back" alerts -> this channel; accepts commands
DISCORD_ADMIN_ROLE_ID=         # if set, only members with this role may run commands (keep the channel private!)
JOIN_LEAVE=TRUE                # "-> Name joined The Island" / "<- Name left Ragnarok" in the relay channel
```

Admin channel commands: `!maps`, `!players`, `!broadcast <msg>`, `!save [map|all]`,
`!rcon <map|all> <command>` (e.g. `!rcon island ListPlayers`, `!rcon all SaveWorld`).
Map names match loosely: `island`, `center`, `scorched`, `lostcolony`, `genesis` all work.

Tribe logs and join/leave come from the RCON `GetGameLog` buffer, which only exists because
the maps run with `-servergamelog -servergamelogincludetribelogs -ServerRCONOutputTribeLogs`
(the POK image adds those). Without a token it still relays map <-> map.


## In-game player commands (v5)

Any player can type these in normal in-game chat on any map; the bot replies into that map's chat (visible to everyone on the map):

- `!help` / `!list` – shows the list of available commands
- `!discord` – posts the Discord invite link (set `DISCORD_INVITE`; blank disables it)

When a player joins a map they get a welcome line pointing them at `!help` and `!discord`.

Configure without touching code via the stack `.env` (all optional – sensible defaults are baked in):

```
DISCORD_INVITE=                         # your own invite; blank disables !discord
PLAYER_HELP_MSG=ACME commands: !help (list), !discord (invite)
WELCOME_ENABLED=TRUE                            # set FALSE to turn off the join greeting
WELCOME_MSG=Welcome {name} to ACME Cluster - {label}! Type !help in chat for commands, or !discord for our Discord.
```

To add more commands, edit `player_command()` in `bot.py` and redeploy (rebuild the `obelisk` service).

## Multi-line chat messages

`say()` splits on `|` (and newline), sending each piece as its own in-game chat row, so long messages never wrap mid-word. Put `|` where you want a break. The welcome default is:

`Welcome {name} to ACME Cluster - {label}!|Type !help in chat for commands,|or !discord for our Discord.`

-> three clean lines in-game. Edit `WELCOME_MSG` / `PLAYER_HELP_MSG` (in `.env` or the bot.py defaults) and add `|` wherever a new line should start.
