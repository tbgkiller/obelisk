# Obelisk settings

_Generated from the schema - do not edit by hand._


A handful of settings are marked **container template**: bind mounts and the
published port, which Docker needs before Obelisk exists. Those are set when you
create the container and are read-only in the UI. Everything else is set in the
web UI after it is running.


## Cluster

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Start with the server | `cluster_autostart` | bool | `True` | UI | none |
| Maps to run | `maps` | maps | `island` | UI | recreate |
| First game port | `game_port_base` | port | `7777` | UI | recreate |
| First RCON port | `rcon_port_base` | port | `27020` | UI | recreate |
| ARK server image | `ark_image` | text | `acekorneya/asa_server:2_1_latest` | UI | recreate |
| Obelisk image | `obelisk_image` | text | `ghcr.io/tbgkiller/obelisk:latest` | UI | recreate |
| Update window opens | `update_window_start` | text | `4:00 AM` | UI | recreate |
| Update window closes | `update_window_end` | text | `6:00 AM` | UI | recreate |
| Restart warning | `restart_notice_minutes` | int | `30` | UI | recreate |
| Who applies ARK updates | `ark_update_mode` | choice | `automatic` | UI | recreate |
| Apply staged updates in the window | `update_apply_in_window` | bool | `False` | UI | none |
| Apply waiting changes when nobody is on | `apply_when_empty` | bool | `True` | UI | none |

**Start with the server** - Bring the cluster back automatically when the array starts, the way a cluster you rely on should. Turn it off for a throwaway or test cluster you would rather start by hand. Only has an effect when Unraid's Compose Manager folder is mounted, which is what makes the cluster a stack.

**Maps to run** - Which maps this cluster runs, in order. The first one downloads first: it downloads the ~30 GB of server files once and the others wait for the download, instead of every map fetching the same thing at once. That job is over once the files are on disk - it is not authority over updates; the thing that tries a new build before your cluster does is the Staging server. Ports are assigned in this order, so reordering a live cluster moves everyone's ports - add to the end instead.

**First game port** - Each map takes one UDP port counting up from here. Ten maps starting at 7777 uses 7777-7786. Make sure the range is free and forwarded.

**First RCON port** - One TCP port per map counting up from here. Obelisk uses these to talk to each server. Do NOT forward these - RCON is admin access.

**ARK server image** - The container image each map runs. Pinning a tag rather than :latest means an upstream change can't surprise every map at once.

**Obelisk image** - Obelisk's own image. Change this to run a fork or a pinned version.

**Update window opens** - Servers only apply game updates inside this window, so a patch never restarts your cluster at peak time.

**Update window closes** - The other end of the update window.

**Restart warning** - Minutes of in-game warning before a scheduled restart or update.

**Who applies ARK updates** - automatic: the server image updates itself inside the update window - no warning, no check, and a build that will not load with your mods is found out by every map failing to come back. obelisk: Obelisk rehearses the update on the staging server first and applies it only once it has booted cleanly, when you click Apply or when the window opens. Changing this recreates the map containers.

**Apply staged updates in the window** - With this on, an update that has been staged and verified is applied automatically the next time the update window opens, instead of waiting for someone to click Apply. Only ever applies something that already booted cleanly on the staging server.

**Apply waiting changes when nobody is on** - Settings that restart your servers wait for a moment that costs nobody anything. With this on, Obelisk applies them as soon as every map reports zero players for a few minutes running. Turn it off to apply them only in the update window or by hand.


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

**Server name tag line** - Appended to every map's name. Edit once, applies to all ten. Pipes and spaces are safe. The finished name - prefix, number, map, this - has to fit the 63 characters the game shows in the browser; Obelisk drops this part first if it does not.

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

**Players per map** - The cluster default, applied to every map that does not set its own. A map can differ - that is set on that map's page, not here. Higher values need more RAM per container.

**Join password** - Leave blank for an open server. Anyone with this can join.

**Admin / RCON password** - Grants in-game admin AND is the RCON password Obelisk uses. Treat it like a root password. You can save settings without it, but the cluster won't start until it's set.

**Require BattlEye** - Turn off only if you add a mod that isn't BattlEye-compatible.


## Mods

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Mods (CurseForge IDs) | `mod_ids` | csv | `` | UI | recreate |
| Passive mods | `passive_mods` | csv | `` | UI | recreate |

**Mods (CurseForge IDs)** - Comma-separated CurseForge project IDs, applied to all ten maps. ORDER MATTERS: a mod earlier in the list wins conflicting remaps, which is why stacking mods go first. Blank means vanilla.

**Passive mods** - Mods the server loads but clients aren't forced to download.


## Rates

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| XP rate | `xp_multiplier` | float | `1.0` | UI | reload |
| Harvest rate | `harvest_multiplier` | float | `1.0` | UI | reload |
| Taming speed | `taming_multiplier` | float | `1.0` | UI | reload |
| Baby maturation speed | `maturation_multiplier` | float | `1.0` | UI | reload |
| Item Stack Size Multiplier | `ItemStackSizeMultiplier` | float | `1.0` | UI | reload |
| Dino Count Multiplier | `DinoCountMultiplier` | float | `1.0` | UI | reload |
| Player Character Water Drain Multiplier | `PlayerCharacterWaterDrainMultiplier` | float | `1.0` | UI | reload |
| Player Character Food Drain Multiplier | `PlayerCharacterFoodDrainMultiplier` | float | `1.0` | UI | reload |
| Dino Character Stamina Drain Multiplier | `DinoCharacterStaminaDrainMultiplier` | float | `1.0` | UI | reload |
| Use Optimized Harvesting Health | `UseOptimizedHarvestingHealth` | bool | `False` | UI | reload |
| Clamp Resource Harvest Damage | `ClampResourceHarvestDamage` | bool | `False` | UI | reload |
| PvE Structure Decay Period Multiplier | `PvEStructureDecayPeriodMultiplier` | float | `1.0` | UI | reload |
| Structure Prevent Resource Radius Multiplier | `StructurePreventResourceRadiusMultiplier` | float | `1.0` | UI | reload |
| Per Platform Max Structures Multiplier | `PerPlatformMaxStructuresMultiplier` | float | `1.0` | UI | reload |
| Platform Saddle Build Area Bounds Multiplier | `PlatformSaddleBuildAreaBoundsMultiplier` | float | `1.0` | UI | reload |
| PvE Dino Decay Period Multiplier | `PvEDinoDecayPeriodMultiplier` | float | `1.0` | UI | reload |
| Max Personal Tamed Dinos | `MaxPersonalTamedDinos` | int | `0` | UI | reload |
| Raid Dino Character Food Drain Multiplier | `RaidDinoCharacterFoodDrainMultiplier` | float | `1.0` | UI | reload |
| Oxygen Swim Speed Stat Multiplier | `OxygenSwimSpeedStatMultiplier` | float | `1.0` | UI | reload |
| Tribute Item Expiration Seconds | `TributeItemExpirationSeconds` | int | `0` | UI | reload |
| Tribute Dino Expiration Seconds | `TributeDinoExpirationSeconds` | int | `0` | UI | reload |
| Tribute Character Expiration Seconds | `TributeCharacterExpirationSeconds` | int | `0` | UI | reload |
| Generic XP Multiplier | `GenericXPMultiplier` | float | `1.0` | UI | reload |
| Craft XP Multiplier | `CraftXPMultiplier` | float | `1.0` | UI | reload |
| Harvest XP Multiplier | `HarvestXPMultiplier` | float | `1.0` | UI | reload |
| Kill XP Multiplier | `KillXPMultiplier` | float | `1.0` | UI | reload |
| Special XP Multiplier | `SpecialXPMultiplier` | float | `1.0` | UI | reload |
| Supply Crate Loot Quality Multiplier | `SupplyCrateLootQualityMultiplier` | float | `1.0` | UI | reload |
| Fishing Loot Quality Multiplier | `FishingLootQualityMultiplier` | float | `1.0` | UI | reload |
| Base Hexagon Reward Multiplier | `BaseHexagonRewardMultiplier` | float | `1.0` | UI | reload |
| Hexagon Cost Multiplier | `HexagonCostMultiplier` | float | `1.0` | UI | reload |
| PvP Zone Structure Damage Multiplier | `PvPZoneStructureDamageMultiplier` | float | `1.0` | UI | reload |

**XP rate** - 1.0 is vanilla.

**Harvest rate** - How much you get per swing. 1.0 is vanilla.

**Taming speed** - Higher is faster. 1.0 is vanilla.

**Baby maturation speed** - Higher is faster. Breeding-heavy servers push this up.

**Item Stack Size Multiplier** - Multiplies every stack limit. The single biggest quality-of-life setting on a modded cluster.

**Dino Count Multiplier** - How many wild creatures spawn. Raising this costs RAM and CPU on every map at once.

**Player Character Water Drain Multiplier** - GameUserSettings.ini [ServerSettings] PlayerCharacterWaterDrainMultiplier

**Player Character Food Drain Multiplier** - GameUserSettings.ini [ServerSettings] PlayerCharacterFoodDrainMultiplier

**Dino Character Stamina Drain Multiplier** - GameUserSettings.ini [ServerSettings] DinoCharacterStaminaDrainMultiplier

**Use Optimized Harvesting Health** - Cheaper harvesting maths. Changes yields slightly.

**Clamp Resource Harvest Damage** - Caps how much a single hit can take off a resource, which stops high-multiplier setups one-shotting nodes.

**PvE Structure Decay Period Multiplier** - How long structures last untouched before they start to decay.

**Structure Prevent Resource Radius Multiplier** - How far a building stops resources respawning around it.

**Per Platform Max Structures Multiplier** - Structure limit on platform saddles and rafts.

**Platform Saddle Build Area Bounds Multiplier** - GameUserSettings.ini [ServerSettings] PlatformSaddleBuildAreaBoundsMultiplier

**PvE Dino Decay Period Multiplier** - How long an unclaimed tame survives before it can be claimed by anyone.

**Max Personal Tamed Dinos** - GameUserSettings.ini [ServerSettings] MaxPersonalTamedDinos

**Raid Dino Character Food Drain Multiplier** - GameUserSettings.ini [ServerSettings] RaidDinoCharacterFoodDrainMultiplier

**Oxygen Swim Speed Stat Multiplier** - GameUserSettings.ini [ServerSettings] OxygenSwimSpeedStatMultiplier

**Tribute Item Expiration Seconds** - GameUserSettings.ini [ServerSettings] TributeItemExpirationSeconds

**Tribute Dino Expiration Seconds** - GameUserSettings.ini [ServerSettings] TributeDinoExpirationSeconds

**Tribute Character Expiration Seconds** - GameUserSettings.ini [ServerSettings] TributeCharacterExpirationSeconds

**Generic XP Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] GenericXPMultiplier

**Craft XP Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] CraftXPMultiplier

**Harvest XP Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] HarvestXPMultiplier

**Kill XP Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] KillXPMultiplier

**Special XP Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] SpecialXPMultiplier

**Supply Crate Loot Quality Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] SupplyCrateLootQualityMultiplier

**Fishing Loot Quality Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] FishingLootQualityMultiplier

**Base Hexagon Reward Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] BaseHexagonRewardMultiplier

**Hexagon Cost Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] HexagonCostMultiplier

**PvP Zone Structure Damage Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] PvPZoneStructureDamageMultiplier


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


## Backups

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Backup schedule | `backup_times` | times | `` | UI | none |
| Backups to keep | `backup_keep` | int | `7` | UI | none |
| Save the world first | `backup_flush` | bool | `True` | UI | none |
| Send backups off-site | `cloud_enabled` | bool | `False` | UI | none |
| Backups to keep off-site | `cloud_keep` | int | `14` | UI | none |

**Backup schedule** - 24-hour times to back up, comma separated - e.g. 04:00. Blank means backups only happen when you press the button.

**Backups to keep** - Older archives are deleted after each successful backup. Without a limit a nightly backup eventually fills the disk it is protecting.

**Save the world first** - Ask every running map to save before copying, so the archive holds the world as of now instead of the last autosave. Costs a few seconds and a brief pause in game.

**Send backups off-site** - Upload each new backup to the connected cloud, encrypted here first so the provider only ever holds ciphertext. Connect a provider on the Cloud page before turning this on.

**Backups to keep off-site** - Older archives are removed from the cloud after each successful upload. Usually higher than the local limit - off-site space is cheaper than the disk the cluster runs on.


## Obelisk

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Status page port | `status_port` | port | `8088` | container template | recreate |
| Admin token | `admin_token` | password | `` | UI | reload |
| Player count refresh | `online_poll_seconds` | int | `60` | UI | reload |
| Time zone | `timezone` | choice | `UTC` | UI | reload |

**Status page port** - The read-only status page and the admin UI. 0 turns both off.

**Admin token** - Required to open /admin and to change anything. Blank means the admin side is switched off entirely and the page stays read-only.

**Player count refresh** - Seconds between cluster-wide player count refreshes. 0 disables.

**Time zone** - Drives wipe times, restart windows and log timestamps. Picked from the IANA zone list rather than typed: "chicago" and "CST" are not zones, and a name the server can't resolve silently leaves it on UTC.


## Resources

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| RAM cap per map | `mem_limit` | memory | `20g` | UI | recreate |
| Game install folder | `serverfiles` | text | `` | UI | recreate |
| Cluster data folder | `appdata` | text | `/mnt/user/appdata/ark` | container template | recreate |
| Staging server | `staging_mode` | choice | `on_demand` | UI | recreate |
| Staging server map | `staging_map` | choice | `scorched` | UI | recreate |
| Staging server RAM cap | `staging_memory` | memory | `10g` | UI | recreate |
| RAM budget for this host | `host_ram_gb` | int | `0` | UI | none |

**RAM cap per map** - A cap, not a reservation - unused headroom costs nothing. If a map is OOM-killed you'll see it restart repeatedly with the container itself reporting a clean exit, because only the game process is killed. This is the cluster default; a heavy map can be given more on that map's own page.

**Game install folder** - Where the ~20 GB ARK server files live. Deliberately outside the data folder: it is re-downloadable, and keeping it out is what makes a backup small enough to move. Blank puts it beside the data folder.

**Cluster data folder** - Where server files, saves and the shared config live on the host. Put this on an SSD or NVMe pool, never the spinning array - ASA is very I/O hungry. Changing it moves the whole cluster and needs a recreate.

**Staging server** - A throwaway ARK server that boots a new build with all your mods before your cluster does - it is the only thing that can pre-fetch mods, because the game downloads those itself at startup. off: never. on_demand: start it when an update appears, stop it after. always: keep it running so every update is rehearsed as it lands. Costs about one map's RAM while it runs.

**Staging server map** - Which map the staging server rehearses on. It only ever generates a throwaway world, and the mods it fetches are the same on any map, so this is purely about which one is cheapest to run.

**Staging server RAM cap** - A cap for the staging server. It has one slot and no players, but ARK's memory goes to the map rather than the players - the lightest map on a live cluster still sits around 8 GB.

**RAM budget for this host** - How much memory this machine can give to ARK, in GB. Obelisk refuses to launch a cluster whose caps exceed it, rather than letting the host start killing servers under load. 0 turns the check off.


## Advanced

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Extra launch flags | `custom_server_args` | text | `` | UI | recreate |
| CurseForge API key | `curseforge_api_key` | password | `` | UI | none |
| Extra GameUserSettings.ini | `extra_gameusersettings` | longtext | `` | UI | reload |
| Extra Game.ini | `extra_game` | longtext | `` | UI | reload |

**Extra launch flags** - Appended to the server command line, e.g. -ForceAllowCaveFlyers. Wrong values here stop a map booting, so change one at a time.

**CurseForge API key** - Optional, and free from console.curseforge.com. With a key the Mods page can search and browse CurseForge; without one you can still add any mod by pasting its Project ID, which is what the keyless lookup can answer. You can paste it straight into the Mods page instead of hunting for it here - same setting, same storage, two doors. Stored like any other secret and never logged.

**Extra GameUserSettings.ini** - Appended verbatim to the generated GameUserSettings.ini. Obelisk only models the settings above, so anything it doesn't know about - mod config blocks, rarely-used options - goes here and is passed through untouched. Full INI syntax, [Sections] and all.

**Extra Game.ini** - Appended verbatim to the generated Game.ini. Same idea - breeding curves, per-dino overrides and anything else not modelled above.


## Breeding

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Disable Imprint Dino Buff | `DisableImprintDinoBuff` | bool | `False` | UI | reload |
| Allow Anyone Baby Imprint Cuddle | `AllowAnyoneBabyImprintCuddle` | bool | `False` | UI | reload |
| Mating Speed Multiplier | `MatingSpeedMultiplier` | float | `1.0` | UI | reload |
| Mating Interval Multiplier | `MatingIntervalMultiplier` | float | `1.0` | UI | reload |
| Egg Hatch Speed Multiplier | `EggHatchSpeedMultiplier` | float | `1.0` | UI | reload |
| Baby Cuddle Interval Multiplier | `BabyCuddleIntervalMultiplier` | float | `1.0` | UI | reload |
| Baby Cuddle Grace Period Multiplier | `BabyCuddleGracePeriodMultiplier` | float | `1.0` | UI | reload |
| Baby Cuddle Lose Imprint Quality Speed Multiplier | `BabyCuddleLoseImprintQualitySpeedMultiplier` | float | `1.0` | UI | reload |
| Baby Food Consumption Speed Multiplier | `BabyFoodConsumptionSpeedMultiplier` | float | `1.0` | UI | reload |
| Lay Egg Interval Multiplier | `LayEggIntervalMultiplier` | float | `1.0` | UI | reload |

**Disable Imprint Dino Buff** - GameUserSettings.ini [ServerSettings] DisableImprintDinoBuff

**Allow Anyone Baby Imprint Cuddle** - GameUserSettings.ini [ServerSettings] AllowAnyoneBabyImprintCuddle

**Mating Speed Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] MatingSpeedMultiplier

**Mating Interval Multiplier** - Time between breedings. Lower is faster - this one is the wrong way round from most.

**Egg Hatch Speed Multiplier** - How fast fertilised eggs hatch. Higher is faster.

**Baby Cuddle Interval Multiplier** - How often a baby wants imprinting. Lower means more frequent, which means more imprint per raise.

**Baby Cuddle Grace Period Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] BabyCuddleGracePeriodMultiplier

**Baby Cuddle Lose Imprint Quality Speed Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] BabyCuddleLoseImprintQualitySpeedMultiplier

**Baby Food Consumption Speed Multiplier** - How fast babies eat. Raising maturation without raising this starves them.

**Lay Egg Interval Multiplier** - Game.ini [/Script/ShooterGame.ShooterGameMode] LayEggIntervalMultiplier


## Dinos

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Disable Dino Decay PvE | `DisableDinoDecayPvE` | bool | `False` | UI | reload |
| PvP Dino Decay | `PvPDinoDecay` | bool | `True` | UI | reload |
| Auto Destroy Decayed Dinos | `AutoDestroyDecayedDinos` | bool | `False` | UI | reload |
| Max Tamed Dinos | `MaxTamedDinos` | int | `0` | UI | reload |
| Allow Flyer Carry PVE | `AllowFlyerCarryPVE` | bool | `False` | UI | reload |
| Allow Raid Dino Feeding | `AllowRaidDinoFeeding` | bool | `False` | UI | reload |
| Prevent Download Dinos | `PreventDownloadDinos` | bool | `False` | UI | reload |
| Prevent Upload Dinos | `PreventUploadDinos` | bool | `False` | UI | reload |
| Cross ARK Allow Foreign Dino Downloads | `CrossARKAllowForeignDinoDownloads` | bool | `False` | UI | reload |
| Passive Defenses Damage Riderless Dinos | `bPassiveDefensesDamageRiderlessDinos` | bool | `False` | UI | reload |
| Allow Flyer Speed Leveling | `bAllowFlyerSpeedLeveling` | bool | `False` | UI | reload |

**Disable Dino Decay PvE** - GameUserSettings.ini [ServerSettings] DisableDinoDecayPvE

**PvP Dino Decay** - GameUserSettings.ini [ServerSettings] PvPDinoDecay

**Auto Destroy Decayed Dinos** - GameUserSettings.ini [ServerSettings] AutoDestroyDecayedDinos

**Max Tamed Dinos** - Cluster-wide tame cap. Reached, it stops taming entirely.

**Allow Flyer Carry PVE** - GameUserSettings.ini [ServerSettings] AllowFlyerCarryPVE

**Allow Raid Dino Feeding** - Whether titanosaurs and the like can be permanently fed.

**Prevent Download Dinos** - GameUserSettings.ini [ServerSettings] PreventDownloadDinos

**Prevent Upload Dinos** - GameUserSettings.ini [ServerSettings] PreventUploadDinos

**Cross ARK Allow Foreign Dino Downloads** - GameUserSettings.ini [ServerSettings] CrossARKAllowForeignDinoDownloads

**Passive Defenses Damage Riderless Dinos** - Game.ini [/Script/ShooterGame.ShooterGameMode] bPassiveDefensesDamageRiderlessDinos

**Allow Flyer Speed Leveling** - Game.ini [/Script/ShooterGame.ShooterGameMode] bAllowFlyerSpeedLeveling


## Players

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Max Hexagons Per Character | `MaxHexagonsPerCharacter` | int | `0` | UI | reload |
| Server Crosshair | `ServerCrosshair` | bool | `False` | UI | reload |
| Allow Third Person Player | `AllowThirdPersonPlayer` | bool | `True` | UI | reload |
| Show Map Player Location | `ShowMapPlayerLocation` | bool | `True` | UI | reload |
| Notify Player Left | `alwaysNotifyPlayerLeft` | bool | `False` | UI | reload |
| Notify Player Joined | `alwaysNotifyPlayerJoined` | bool | `False` | UI | reload |
| Kick Idle Players Period | `KickIdlePlayersPeriod` | int | `3600` | UI | reload |
| Max Number Of Players In Tribe | `MaxNumberOfPlayersInTribe` | int | `70` | UI | reload |
| Increase PvP Respawn Interval | `bIncreasePvPRespawnInterval` | bool | `False` | UI | reload |
| Use Corpse Locator | `bUseCorpseLocator` | bool | `False` | UI | reload |

**Max Hexagons Per Character** - GameUserSettings.ini [ServerSettings] MaxHexagonsPerCharacter

**Server Crosshair** - GameUserSettings.ini [ServerSettings] ServerCrosshair

**Allow Third Person Player** - GameUserSettings.ini [ServerSettings] AllowThirdPersonPlayer

**Show Map Player Location** - GameUserSettings.ini [ServerSettings] ShowMapPlayerLocation

**Notify Player Left** - GameUserSettings.ini [ServerSettings] alwaysNotifyPlayerLeft

**Notify Player Joined** - GameUserSettings.ini [ServerSettings] alwaysNotifyPlayerJoined

**Kick Idle Players Period** - Seconds before an idle player is disconnected.

**Max Number Of Players In Tribe** - Game.ini [/Script/ShooterGame.ShooterGameMode] MaxNumberOfPlayersInTribe

**Increase PvP Respawn Interval** - Game.ini [/Script/ShooterGame.ShooterGameMode] bIncreasePvPRespawnInterval

**Use Corpse Locator** - Game.ini [/Script/ShooterGame.ShooterGameMode] bUseCorpseLocator


## Structures

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Allow Cave Building PvE | `AllowCaveBuildingPvE` | bool | `False` | UI | reload |
| Enable Extra Structure Prevention Volumes | `EnableExtraStructurePreventionVolumes` | bool | `False` | UI | reload |
| The Max Structures In Range | `TheMaxStructuresInRange` | int | `0` | UI | reload |
| PvP Structure Decay | `PvPStructureDecay` | bool | `True` | UI | reload |
| Override Structure Platform Prevention | `OverrideStructurePlatformPrevention` | bool | `False` | UI | reload |
| Force All Structure Locking | `ForceAllStructureLocking` | bool | `False` | UI | reload |
| Only Auto Destroy Core Structures | `OnlyAutoDestroyCoreStructures` | bool | `False` | UI | reload |
| Only Decay Unsnapped Core Structures | `OnlyDecayUnsnappedCoreStructures` | bool | `False` | UI | reload |
| Fast Decay Unsnapped Core Structures | `FastDecayUnsnappedCoreStructures` | bool | `False` | UI | reload |
| Always Allow Structure Pickup | `AlwaysAllowStructurePickup` | bool | `False` | UI | reload |
| Structure Pickup Time After Placement | `StructurePickupTimeAfterPlacement` | float | `30.0` | UI | reload |
| Structure Pickup Hold Duration | `StructurePickupHoldDuration` | float | `0.5` | UI | reload |
| Allow Integrated S Plus Structures | `AllowIntegratedSPlusStructures` | bool | `True` | UI | reload |
| Allow Crate Spawns On Top Of Structures | `AllowCrateSpawnsOnTopOfStructures` | bool | `False` | UI | reload |
| Personal Tamed Dinos Saddle Structure Cost | `PersonalTamedDinosSaddleStructureCost` | int | `0` | UI | reload |
| Tribe Log Destroyed Enemy Structures | `TribeLogDestroyedEnemyStructures` | bool | `False` | UI | reload |
| Allow Platform Saddle Multi Floors | `bAllowPlatformSaddleMultiFloors` | bool | `False` | UI | reload |
| Disable Structure Placement Collision | `bDisableStructurePlacementCollision` | bool | `False` | UI | reload |
| Flyer Platform Allow Unaligned Dino Basing | `bFlyerPlatformAllowUnalignedDinoBasing` | bool | `False` | UI | reload |
| Limit Turrets In Range | `bLimitTurretsInRange` | bool | `False` | UI | reload |
| Hard Limit Turrets In Range | `bHardLimitTurretsInRange` | bool | `False` | UI | reload |
| Genesis Use Structures Prevention Volumes | `bGenesisUseStructuresPreventionVolumes` | bool | `False` | UI | reload |
| Structure Damage Repair Cooldown | `StructureDamageRepairCooldown` | int | `0` | UI | reload |

**Allow Cave Building PvE** - Whether players may build inside caves in PvE.

**Enable Extra Structure Prevention Volumes** - Blocks building in the areas Wildcard marks off, artifact caves included.

**The Max Structures In Range** - The hard cap on structures near one point. Big bases hit this before they hit anything else.

**PvP Structure Decay** - GameUserSettings.ini [ServerSettings] PvPStructureDecay

**Override Structure Platform Prevention** - GameUserSettings.ini [ServerSettings] OverrideStructurePlatformPrevention

**Force All Structure Locking** - GameUserSettings.ini [ServerSettings] ForceAllStructureLocking

**Only Auto Destroy Core Structures** - GameUserSettings.ini [ServerSettings] OnlyAutoDestroyCoreStructures

**Only Decay Unsnapped Core Structures** - GameUserSettings.ini [ServerSettings] OnlyDecayUnsnappedCoreStructures

**Fast Decay Unsnapped Core Structures** - GameUserSettings.ini [ServerSettings] FastDecayUnsnappedCoreStructures

**Always Allow Structure Pickup** - GameUserSettings.ini [ServerSettings] AlwaysAllowStructurePickup

**Structure Pickup Time After Placement** - GameUserSettings.ini [ServerSettings] StructurePickupTimeAfterPlacement

**Structure Pickup Hold Duration** - GameUserSettings.ini [ServerSettings] StructurePickupHoldDuration

**Allow Integrated S Plus Structures** - GameUserSettings.ini [ServerSettings] AllowIntegratedSPlusStructures

**Allow Crate Spawns On Top Of Structures** - GameUserSettings.ini [ServerSettings] AllowCrateSpawnsOnTopOfStructures

**Personal Tamed Dinos Saddle Structure Cost** - GameUserSettings.ini [ServerSettings] PersonalTamedDinosSaddleStructureCost

**Tribe Log Destroyed Enemy Structures** - GameUserSettings.ini [ServerSettings] TribeLogDestroyedEnemyStructures

**Allow Platform Saddle Multi Floors** - Game.ini [/Script/ShooterGame.ShooterGameMode] bAllowPlatformSaddleMultiFloors

**Disable Structure Placement Collision** - Game.ini [/Script/ShooterGame.ShooterGameMode] bDisableStructurePlacementCollision

**Flyer Platform Allow Unaligned Dino Basing** - Game.ini [/Script/ShooterGame.ShooterGameMode] bFlyerPlatformAllowUnalignedDinoBasing

**Limit Turrets In Range** - Game.ini [/Script/ShooterGame.ShooterGameMode] bLimitTurretsInRange

**Hard Limit Turrets In Range** - Game.ini [/Script/ShooterGame.ShooterGameMode] bHardLimitTurretsInRange

**Genesis Use Structures Prevention Volumes** - Game.ini [/Script/ShooterGame.ShooterGameMode] bGenesisUseStructuresPreventionVolumes

**Structure Damage Repair Cooldown** - Game.ini [/Script/ShooterGame.ShooterGameMode] StructureDamageRepairCooldown


## Items

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Clamp Item Spoiling Times | `ClampItemSpoilingTimes` | bool | `False` | UI | reload |
| Prevent Download Items | `PreventDownloadItems` | bool | `False` | UI | reload |
| Prevent Upload Items | `PreventUploadItems` | bool | `False` | UI | reload |
| Disable Loot Crates | `bDisableLootCrates` | bool | `False` | UI | reload |
| Random Supply Crate Points | `RandomSupplyCratePoints` | bool | `False` | UI | reload |
| Disable Default Map Item Sets | `bDisableDefaultMapItemSets` | bool | `False` | UI | reload |

**Clamp Item Spoiling Times** - Caps spoil timers so mods cannot push them absurdly high.

**Prevent Download Items** - GameUserSettings.ini [ServerSettings] PreventDownloadItems

**Prevent Upload Items** - GameUserSettings.ini [ServerSettings] PreventUploadItems

**Disable Loot Crates** - Game.ini [/Script/ShooterGame.ShooterGameMode] bDisableLootCrates

**Random Supply Crate Points** - Game.ini [/Script/ShooterGame.ShooterGameMode] RandomSupplyCratePoints

**Disable Default Map Item Sets** - Game.ini [/Script/ShooterGame.ShooterGameMode] bDisableDefaultMapItemSets


## PvE & PvP

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Server PVE | `ServerPVE` | bool | `False` | UI | reload |
| Server Hardcore | `ServerHardcore` | bool | `False` | UI | reload |
| Prevent Offline PvP | `PreventOfflinePvP` | bool | `False` | UI | reload |
| Enable Cryo Sickness PVE | `EnableCryoSicknessPVE` | bool | `False` | UI | reload |
| Enable PVP Gamma | `EnablePVPGamma` | bool | `True` | UI | reload |
| Disable PvE Gamma | `DisablePvEGamma` | bool | `False` | UI | reload |
| PvE Allow Tribe War | `bPvEAllowTribeWar` | bool | `False` | UI | reload |
| PvE Allow Tribe War Cancel | `bPvEAllowTribeWarCancel` | bool | `False` | UI | reload |
| Disable Friendly Fire | `bDisableFriendlyFire` | bool | `False` | UI | reload |
| PvE Disable Friendly Fire | `bPvEDisableFriendlyFire` | bool | `False` | UI | reload |
| Auto PvE Timer | `bAutoPvETimer` | bool | `False` | UI | reload |

**Server PVE** - PvE mode: players cannot damage each other or each other's things.

**Server Hardcore** - Death resets the survivor to level 1.

**Prevent Offline PvP** - Protects a tribe's things while none of them are online.

**Enable Cryo Sickness PVE** - GameUserSettings.ini [ServerSettings] EnableCryoSicknessPVE

**Enable PVP Gamma** - GameUserSettings.ini [ServerSettings] EnablePVPGamma

**Disable PvE Gamma** - GameUserSettings.ini [ServerSettings] DisablePvEGamma

**PvE Allow Tribe War** - Game.ini [/Script/ShooterGame.ShooterGameMode] bPvEAllowTribeWar

**PvE Allow Tribe War Cancel** - Game.ini [/Script/ShooterGame.ShooterGameMode] bPvEAllowTribeWarCancel

**Disable Friendly Fire** - Game.ini [/Script/ShooterGame.ShooterGameMode] bDisableFriendlyFire

**PvE Disable Friendly Fire** - Game.ini [/Script/ShooterGame.ShooterGameMode] bPvEDisableFriendlyFire

**Auto PvE Timer** - Game.ini [/Script/ShooterGame.ShooterGameMode] bAutoPvETimer


## Tribes

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Prevent Tribe Alliances | `PreventTribeAlliances` | bool | `False` | UI | reload |
| Tribe Name Change Cooldown | `TribeNameChangeCooldown` | float | `1.0` | UI | reload |
| Max Tribe Logs | `MaxTribeLogs` | int | `0` | UI | reload |
| Max Alliances Per Tribe | `MaxAlliancesPerTribe` | int | `0` | UI | reload |
| Max Tribes Per Alliance | `MaxTribesPerAlliance` | int | `0` | UI | reload |

**Prevent Tribe Alliances** - Stops tribes formally allying.

**Tribe Name Change Cooldown** - GameUserSettings.ini [ServerSettings] TribeNameChangeCooldown

**Max Tribe Logs** - Game.ini [/Script/ShooterGame.ShooterGameMode] MaxTribeLogs

**Max Alliances Per Tribe** - Game.ini [/Script/ShooterGame.ShooterGameMode] MaxAlliancesPerTribe

**Max Tribes Per Alliance** - Game.ini [/Script/ShooterGame.ShooterGameMode] MaxTribesPerAlliance


## Difficulty

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Override Official Difficulty | `OverrideOfficialDifficulty` | float | `1.0` | UI | reload |
| Difficulty Offset | `DifficultyOffset` | float | `1.0` | UI | reload |

**Override Official Difficulty** - The real max wild level: 6.0 gives level 180 dinos, against 5.0 for official 150s.

**Difficulty Offset** - Scales wild levels within whatever the override allows. Leave at 1.0 unless you know why you are moving it.


## Server behaviour

| Setting | Key | Type | Default | Set in | Takes effect |
|---|---|---|---|---|---|
| Prevent Diseases | `PreventDiseases` | bool | `False` | UI | reload |
| Non Permanent Diseases | `NonPermanentDiseases` | bool | `False` | UI | reload |
| Prevent Spawn Animations | `PreventSpawnAnimations` | bool | `False` | UI | reload |
| Allow Tek Suit Powers In Genesis | `AllowTekSuitPowersInGenesis` | bool | `False` | UI | reload |
| Disable Weather Fog | `DisableWeatherFog` | bool | `False` | UI | reload |
| Destroy Unconnected Water Pipes | `DestroyUnconnectedWaterPipes` | bool | `False` | UI | reload |
| Max Gate Frame On Saddles | `MaxGateFrameOnSaddles` | int | `0` | UI | reload |
| Force Can Ride Fliers | `bForceCanRideFliers` | bool | `False` | UI | reload |
| Allow Flying Stamina Recovery | `AllowFlyingStaminaRecovery` | bool | `False` | UI | reload |
| Server Force No Hud | `ServerForceNoHud` | bool | `False` | UI | reload |
| Show Floating Damage Text | `ShowFloatingDamageText` | bool | `False` | UI | reload |
| Allow Hit Markers | `AllowHitMarkers` | bool | `True` | UI | reload |
| Voice Chat | `globalVoiceChat` | bool | `True` | UI | reload |
| Chat | `proximityChat` | bool | `False` | UI | reload |
| Allow Shared Connections | `AllowSharedConnections` | bool | `False` | UI | reload |
| Allow Hide Damage Source From Logs | `AllowHideDamageSourceFromLogs` | bool | `True` | UI | reload |
| Admin Logging | `AdminLogging` | bool | `False` | UI | reload |
| Auto Save Period Minutes | `AutoSavePeriodMinutes` | float | `15.0` | UI | reload |
| RCON Enabled | `RCONEnabled` | bool | `False` | UI | reload |
| RCON Server Game Log Buffer | `RCONServerGameLogBuffer` | float | `1.0` | UI | reload |
| No Tribute Downloads | `NoTributeDownloads` | bool | `False` | UI | reload |
| Prevent Download Survivors | `PreventDownloadSurvivors` | bool | `False` | UI | reload |
| Prevent Upload Survivors | `PreventUploadSurvivors` | bool | `False` | UI | reload |
| Allow Custom Recipes | `bAllowCustomRecipes` | bool | `False` | UI | reload |
| Allow Unlimited Respecs | `bAllowUnlimitedRespecs` | bool | `False` | UI | reload |
| Disable Genesis Missions | `bDisableGenesisMissions` | bool | `False` | UI | reload |
| Disable World Buffs | `bDisableWorldBuffs` | bool | `False` | UI | reload |
| Enable World Buff Scaling | `bEnableWorldBuffScaling` | bool | `False` | UI | reload |

**Prevent Diseases** - Turns off Swamp Fever and the rest permanently.

**Non Permanent Diseases** - GameUserSettings.ini [ServerSettings] NonPermanentDiseases

**Prevent Spawn Animations** - GameUserSettings.ini [ServerSettings] PreventSpawnAnimations

**Allow Tek Suit Powers In Genesis** - GameUserSettings.ini [ServerSettings] AllowTekSuitPowersInGenesis

**Disable Weather Fog** - GameUserSettings.ini [ServerSettings] DisableWeatherFog

**Destroy Unconnected Water Pipes** - GameUserSettings.ini [ServerSettings] DestroyUnconnectedWaterPipes

**Max Gate Frame On Saddles** - GameUserSettings.ini [ServerSettings] MaxGateFrameOnSaddles

**Force Can Ride Fliers** - GameUserSettings.ini [ServerSettings] bForceCanRideFliers

**Allow Flying Stamina Recovery** - GameUserSettings.ini [ServerSettings] AllowFlyingStaminaRecovery

**Server Force No Hud** - GameUserSettings.ini [ServerSettings] ServerForceNoHud

**Show Floating Damage Text** - GameUserSettings.ini [ServerSettings] ShowFloatingDamageText

**Allow Hit Markers** - GameUserSettings.ini [ServerSettings] AllowHitMarkers

**Voice Chat** - GameUserSettings.ini [ServerSettings] globalVoiceChat

**Chat** - GameUserSettings.ini [ServerSettings] proximityChat

**Allow Shared Connections** - GameUserSettings.ini [ServerSettings] AllowSharedConnections

**Allow Hide Damage Source From Logs** - GameUserSettings.ini [ServerSettings] AllowHideDamageSourceFromLogs

**Admin Logging** - GameUserSettings.ini [ServerSettings] AdminLogging

**Auto Save Period Minutes** - Minutes between automatic world saves. This is the window a crash can cost you.

**RCON Enabled** - GameUserSettings.ini [ServerSettings] RCONEnabled

**RCON Server Game Log Buffer** - GameUserSettings.ini [ServerSettings] RCONServerGameLogBuffer

**No Tribute Downloads** - GameUserSettings.ini [ServerSettings] NoTributeDownloads

**Prevent Download Survivors** - GameUserSettings.ini [ServerSettings] PreventDownloadSurvivors

**Prevent Upload Survivors** - GameUserSettings.ini [ServerSettings] PreventUploadSurvivors

**Allow Custom Recipes** - Game.ini [/Script/ShooterGame.ShooterGameMode] bAllowCustomRecipes

**Allow Unlimited Respecs** - Game.ini [/Script/ShooterGame.ShooterGameMode] bAllowUnlimitedRespecs

**Disable Genesis Missions** - Game.ini [/Script/ShooterGame.ShooterGameMode] bDisableGenesisMissions

**Disable World Buffs** - Game.ini [/Script/ShooterGame.ShooterGameMode] bDisableWorldBuffs

**Enable World Buff Scaling** - Game.ini [/Script/ShooterGame.ShooterGameMode] bEnableWorldBuffScaling
