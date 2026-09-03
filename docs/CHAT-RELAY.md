# Chat relay

Global chat on any map shows up on every other map as `SERVER: [Map] Name: message`,
and optionally in a Discord channel. Messages typed in that Discord channel are pushed
to every map as `[Discord] Name: message`.

Runs inside Obelisk itself - there is no separate container and nothing to configure
before it starts. Chat between maps works out of the box. Discord is opt-in, and every
value below is entered in **Obelisk → Settings → Discord**; you never edit a file.

## Connecting Discord (one-time)

1. Go to <https://discord.com/developers/applications> → **New Application** and name it.
2. **Bot** tab → **Reset Token** → copy it. Turn **Message Content Intent** ON, or the
   bot can see that messages arrived but not what they say.
3. **OAuth2 → URL Generator**: scope `bot`, permissions `View Channels`, `Send Messages`,
   `Read Message History`. Open the generated URL and invite the bot to your server.
4. In Discord, enable **Developer Mode** (Settings → Advanced), then right-click your
   relay channel → **Copy Channel ID**.
5. In Obelisk, paste the token into **Discord bot token** and the ID into
   **Chat relay channel ID**, then save. The relay reconnects on its own.

## Optional channels

Set these in the same place. Each is independent - leave any of them blank.

| Setting | What it does |
|---|---|
| Tribe log channel ID | Tames, deaths and structure events, prefixed with the map |
| Admin channel ID | Admin commands and "map down / back" alerts, and accepts commands |
| Admin role ID | If set, only members with this role may run admin commands |
| Discord invite link | What the in-game `!discord` command replies with |
| Announce joins and leaves | `-> Name joined The Island` / `<- Name left Ragnarok` |

Keep the admin channel private. Anyone who can post in it can run admin commands
against your cluster, and the role gate is a second lock rather than the only one.

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

All of these live in **Obelisk → Settings**, and all are optional:

| Setting | What it does |
|---|---|
| Discord invite link | What `!discord` replies with. Blank disables the command. |
| Welcome new arrivals | Whispers a greeting to the joining player instead of broadcasting it |
| Welcome message | The greeting itself. `{name}`, `{label}` and `{cluster}` are filled in. |
| Player help message | What `!help` lists |

## Multi-line chat messages

In-game chat wraps badly mid-word, so Obelisk splits on `|` (and on newlines) and sends
each piece as its own chat row. Put a `|` wherever you want a line break:

`Welcome {name} to {cluster} - {label}!|Type !help in chat for commands.`

becomes two clean lines in game.

## Adding commands

New chat commands mean a code change - `player_command()` in `obelisk/bot.py` - rather
than a setting. That is deliberate: a command runs code, so it belongs in the image
that gets built and reviewed, not in configuration anyone with the admin page can edit.
