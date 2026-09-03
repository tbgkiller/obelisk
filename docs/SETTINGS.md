# Obelisk settings

_Generated from the schema - do not edit by hand._


A handful of settings are marked **container template**: bind mounts and the
published port, which Docker needs before Obelisk exists. Those are set when you
create the container and are read-only in the UI. Everything else is set in the
web UI after it is running.


## Cluster

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Maps to run | `maps` | maps | `island` | UI | recreate |
| First game port | `game_port_base` | port | `7777` | UI | recreate |
| First RCON port | `rcon_port_base` | port | `27020` | UI | recreate |
| ARK server image | `ark_image` | text | `acekorneya/asa_server:2_1_latest` | UI | recreate |
| Obelisk image | `obelisk_image` | text | `ghcr.io/obelisk-ark/obelisk:latest` | UI | recreate |
| Update window opens | `update_window_start` | text | `4:00 AM` | UI | recreate |
| Update window closes | `update_window_end` | text | `6:00 AM` | UI | recreate |
| Restart warning | `restart_notice_minutes` | int | `30` | UI | recreate |

**Maps to run** - Which maps this cluster runs, in order. The first one is the update master: it downloads the ~30 GB of server files once and the others wait for it, instead of every map fetching the same thing at once. Ports are assigned in this order, so reordering a live cluster moves everyone's ports - add to the end instead.

**First game port** - Each map takes one UDP port counting up from here. Ten maps starting at 7777 uses 7777-7786. Make sure the range is free and forwarded.

**First RCON port** - One TCP port per map counting up from here. Obelisk uses these to talk to each server. Do NOT forward these - RCON is admin access.

**ARK server image** - The container image each map runs. Pinning a tag rather than :latest means an upstream change can't surprise every map at once.

**Obelisk image** - Obelisk's own image. Change this to run a fork or a pinned version.

**Update window opens** - Servers only apply game updates inside this window, so a patch never restarts your cluster at peak time.

**Update window closes** - The other end of the update window.

**Restart warning** - Minutes of in-game warning before a scheduled restart or update.


## Identity

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Server name prefix | `session_prefix` | text | `` | UI | recreate |
| Server name tag line | `session_tags` | text | `` | UI | recreate |
| Cluster ID | `cluster_id` | text | `arkcluster` | UI | recreate |
| Cluster display name | `cluster_name` | text | `ARK Cluster` | UI | reload |
| Show a message of the day | `motd_enabled` | bool | `True` | UI | recreate |
| Message of the day | `motd` | longtext | `` | UI | recreate |

**Server name prefix** - Shown at the start of every map's name in the in-game browser, e.g. "MYCLUSTER 01 \| The Island". Keep it short so the map name stays visible.

**Server name tag line** - Appended to every map's name. Edit once, applies to all ten. Pipes and spaces are safe.

**Cluster ID** - Every map must share this exact value or character transfers between maps stop working. Lowercase letters, digits, - and _ only.

**Cluster display name** - Shown as the heading on the status page and in the in-game welcome message. Unlike the name prefix this is never part of a server's browser name, so it can be as friendly as you like.

**Show a message of the day** - The banner every player sees on connect.

**Message of the day** - POK regenerates the [MessageOfTheDay] INI section from this variable on every start, so setting it in the shared INI does nothing - it has to live here.


## Access

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Players per map | `max_players` | int | `70` | UI | recreate |
| Join password | `server_password` | password | `` | UI | recreate |
| Admin / RCON password | `admin_password` | password | `` | UI | recreate |
| Require BattlEye | `battleye` | bool | `True` | UI | recreate |

**Players per map** - Per map, not cluster-wide. Higher values need more RAM per container.

**Join password** - Leave blank for an open server. Anyone with this can join.

**Admin / RCON password** - Grants in-game admin AND is the RCON password Obelisk uses. Treat it like a root password. You can save settings without it, but the cluster won't start until it's set.

**Require BattlEye** - Turn off only if you add a mod that isn't BattlEye-compatible.


## Mods

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Mods (CurseForge IDs) | `mod_ids` | csv | `` | UI | recreate |
| Passive mods | `passive_mods` | csv | `` | UI | recreate |
| Extra launch flags | `custom_server_args` | text | `` | UI | recreate |

**Mods (CurseForge IDs)** - Comma-separated CurseForge project IDs, applied to all ten maps. ORDER MATTERS: a mod earlier in the list wins conflicting remaps, which is why stacking mods go first. Blank means vanilla.

**Passive mods** - Mods the server loads but clients aren't forced to download.

**Extra launch flags** - Appended to the server command line, e.g. -ForceAllowCaveFlyers. Wrong values here stop a map booting, so change one at a time.


## Rates

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| XP rate | `xp_multiplier` | float | `1.0` | UI | reload |
| Harvest rate | `harvest_multiplier` | float | `1.0` | UI | reload |
| Taming speed | `taming_multiplier` | float | `1.0` | UI | reload |
| Baby maturation speed | `maturation_multiplier` | float | `1.0` | UI | reload |

**XP rate** - 1.0 is vanilla.

**Harvest rate** - How much you get per swing. 1.0 is vanilla.

**Taming speed** - Higher is faster. 1.0 is vanilla.

**Baby maturation speed** - Higher is faster. Breeding-heavy servers push this up.


## Upkeep

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Decay abandoned structures | `structure_decay` | bool | `True` | UI | reload |
| Wild dino wipe times | `wipe_times` | times | `` | UI | none |
| Wipe warnings | `wipe_warn_minutes` | minutes | `10,5,1` | UI | none |

**Decay abandoned structures** - On means abandoned bases decay by material over time so the map doesn't fill with junk. Active bases refresh their own timer, so this doesn't threaten anyone who still plays.

**Wild dino wipe times** - 24-hour server-local times, comma separated. Blank disables wipes. Respawns fresh high-level wilds and clears overfarmed areas - it never touches anything a player owns.

**Wipe warnings** - Minutes before each wipe to warn players in game, comma separated.


## Discord

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Discord bot token | `discord_token` | password | `` | UI | reload |
| Chat relay channel ID | `discord_channel_id` | text | `` | UI | reload |
| Tribe log channel ID | `discord_tribelog_channel_id` | text | `` | UI | reload |
| Admin channel ID | `discord_admin_channel_id` | text | `` | UI | reload |
| Discord invite link | `discord_invite` | text | `` | UI | reload |
| Announce joins and leaves | `join_leave` | bool | `True` | UI | reload |
| Welcome new arrivals | `welcome_enabled` | bool | `True` | UI | reload |
| Admin role ID | `discord_admin_role_id` | text | `` | UI | reload |

**Discord bot token** - Leave blank to relay chat between maps only, with no Discord.

**Chat relay channel ID** - Right-click the channel in Discord, Copy ID.

**Tribe log channel ID** - Optional. Blank disables tribe logs.

**Admin channel ID** - Optional. Where admin-only notices go.

**Discord invite link** - Posted by the in-game !discord command. Blank means the command replies that no invite is configured.

**Announce joins and leaves** - Posts when a player connects or disconnects.

**Welcome new arrivals** - Whispers a greeting privately to the joining player rather than broadcasting it, so a busy server doesn't fill with welcome spam.

**Admin role ID** - Optional. Only members with this Discord role may use admin commands. Blank means the admin channel itself is the only gate.


## Obelisk

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Status page port | `status_port` | port | `8088` | container template | recreate |
| Admin token | `admin_token` | password | `` | UI | reload |
| Player count refresh | `online_poll_seconds` | int | `60` | UI | reload |
| Time zone | `timezone` | text | `UTC` | container template | recreate |

**Status page port** - The read-only status page and the admin UI. 0 turns both off.

**Admin token** - Required to open /admin and to change anything. Blank means the admin side is switched off entirely and the page stays read-only.

**Player count refresh** - Seconds between cluster-wide player count refreshes. 0 disables.

**Time zone** - Drives wipe times, restart windows and log timestamps.


## Resources

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| RAM cap per map | `mem_limit` | memory | `20g` | UI | recreate |
| Cluster data folder | `appdata` | text | `/mnt/user/appdata/ark` | container template | recreate |

**RAM cap per map** - A cap, not a reservation - unused headroom costs nothing. If a map is OOM-killed you'll see it restart repeatedly with the container itself reporting a clean exit, because only the game process is killed. Override per map for the heavy ones.

**Cluster data folder** - Where server files, saves and the shared config live on the host. Put this on an SSD or NVMe pool, never the spinning array - ASA is very I/O hungry. Changing it moves the whole cluster and needs a recreate.


## Advanced

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Extra GameUserSettings.ini | `extra_gameusersettings` | longtext | `` | UI | reload |
| Extra Game.ini | `extra_game` | longtext | `` | UI | reload |

**Extra GameUserSettings.ini** - Appended verbatim to the generated GameUserSettings.ini. Obelisk only models the settings above, so anything it doesn't know about - mod config blocks, rarely-used options - goes here and is passed through untouched. Full INI syntax, [Sections] and all.

**Extra Game.ini** - Appended verbatim to the generated Game.ini. Same idea - breeding curves, per-dino overrides and anything else not modelled above.

