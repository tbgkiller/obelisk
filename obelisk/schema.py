"""
Obelisk settings schema - declared once, used everywhere.

Every setting is described here and nowhere else. From this one list we get:

  * the admin UI          - rendered from `group`, `label`, `help` and `type`
  * validation            - on the API, the UI and hand edits alike
  * generation            - `target` says whether a value becomes an env var,
                            a line in a game INI, or something only Obelisk reads
  * the docs              - `python -m obelisk.schema --markdown`

Adding a setting means adding a dict here. There is no UI code to touch.

`target` values:
    env:NAME                      -> generated .env  (POK reads it at container start)
    ini:GameUserSettings:Sec:Key  -> Shared/Config/GameUserSettings.ini
    ini:Game:Sec:Key              -> Shared/Config/Game.ini
    obelisk:name                  -> consumed by Obelisk itself, never written out

`apply` says what it takes for a change to take effect:
    none      immediate
    reload    the affected map re-fetches config and restarts itself
    recreate  the stack has to be recreated (launch args / mods / ports changed)
"""

# --- types -----------------------------------------------------------------
# text longtext password int bool choice csv memory times minutes port

def _timezones():
    """Valid IANA zone names for the picker.

    Read from the system's tz database when there is one, so the list matches what the
    container can actually resolve. The fallback keeps the field usable on an image
    without tzdata instead of offering an empty dropdown.
    """
    try:
        from zoneinfo import available_timezones
        zones = {z for z in available_timezones() if "/" in z or z == "UTC"}
        if len(zones) > 50:
            return ["UTC"] + sorted(zones - {"UTC"})
    except Exception:
        pass
    return ["UTC", "America/Anchorage", "America/Chicago", "America/Denver",
            "America/Halifax", "America/Los_Angeles", "America/Mexico_City",
            "America/New_York", "America/Phoenix", "America/Sao_Paulo",
            "America/Toronto", "Asia/Dubai", "Asia/Hong_Kong", "Asia/Kolkata",
            "Asia/Seoul", "Asia/Shanghai", "Asia/Singapore", "Asia/Tokyo",
            "Australia/Brisbane", "Australia/Melbourne", "Australia/Perth",
            "Australia/Sydney", "Europe/Amsterdam", "Europe/Berlin", "Europe/Dublin",
            "Europe/Helsinki", "Europe/Lisbon", "Europe/London", "Europe/Madrid",
            "Europe/Moscow", "Europe/Oslo", "Europe/Paris", "Europe/Prague",
            "Europe/Rome", "Europe/Stockholm", "Europe/Warsaw", "Europe/Zurich",
            "Pacific/Auckland", "Pacific/Honolulu"]


TIMEZONES = _timezones()


SETTINGS = [
    # ---------------------------------------------------------------- Identity
    dict(key="session_prefix", label="Server name prefix", group="Identity",
         type="text", default="", target="env:SESSION_PREFIX", apply="recreate",
         help="Shown at the start of every map's name in the in-game browser, "
              "e.g. \"MYCLUSTER 01 | The Island\". Keep it short so the map name stays visible.",
         max_len=24, ascii_only=True, no_leading_hash=True),

    dict(key="session_tags", label="Server name tag line", group="Identity",
         type="text", default="", target="env:SESSION_TAGS",
         apply="recreate", max_len=64, ascii_only=True,
         help="Appended to every map's name. Edit once, applies to all ten. "
              "Pipes and spaces are safe."),

    dict(key="cluster_id", label="Cluster ID", group="Identity",
         type="text", default="arkcluster", target="env:CLUSTER_ID", apply="recreate",
         pattern=r"^[a-z0-9_-]{3,32}$",
         help="Every map must share this exact value or character transfers between "
              "maps stop working. Lowercase letters, digits, - and _ only."),

    dict(key="cluster_name", label="Cluster display name", group="Identity",
         type="text", default="ARK Cluster", target="env:CLUSTER_NAME", apply="reload",
         max_len=48, ascii_only=True,
         help="Shown as the heading on the status page and in the in-game welcome "
              "message. Unlike the name prefix this is never part of a server's "
              "browser name, so it can be as friendly as you like."),

    dict(key="motd_enabled", label="Show a message of the day", group="Identity",
         type="bool", default=True, target="env:ENABLE_MOTD", apply="recreate",
         help="The banner every player sees on connect."),

    dict(key="motd", label="Message of the day", group="Identity",
         type="longtext", default="", target="env:MOTD", apply="recreate", max_len=800,
         help="POK regenerates the [MessageOfTheDay] INI section from this variable on "
              "every start, so setting it in the shared INI does nothing - it has to "
              "live here."),

    # ---------------------------------------------------------------- Access
    dict(key="max_players", label="Players per map", group="Access",
         type="int", default=70, min=1, max=255, target="env:MAX_PLAYERS",
         apply="recreate",
         help="Per map, not cluster-wide. Higher values need more RAM per container."),

    dict(key="server_password", label="Join password", group="Access",
         type="password", default="", target="env:SERVER_PASSWORD", apply="recreate",
         help="Leave blank for an open server. Anyone with this can join."),

    dict(key="admin_password", label="Admin / RCON password", group="Access",
         type="password", default="", target="env:SERVER_ADMIN_PASSWORD",
         apply="recreate", required_before_start=True,
         help="Grants in-game admin AND is the RCON password Obelisk uses. "
              "Treat it like a root password. You can save settings without it, "
              "but the cluster won't start until it's set."),

    dict(key="battleye", label="Require BattlEye", group="Access",
         type="bool", default=True, target="env:BATTLEEYE", apply="recreate",
         help="Turn off only if you add a mod that isn't BattlEye-compatible."),

    # ---------------------------------------------------------------- Mods
    dict(key="mod_ids", label="Mods (CurseForge IDs)", group="Mods",
         type="csv", default="", target="env:MOD_IDS", apply="recreate",
         item_pattern=r"^\d{4,8}$", stacking_first=True,
         help="Comma-separated CurseForge project IDs, applied to all ten maps. "
              "ORDER MATTERS: a mod earlier in the list wins conflicting remaps, which "
              "is why stacking mods go first. Blank means vanilla."),

    dict(key="passive_mods", label="Passive mods", group="Mods",
         type="csv", default="", target="env:PASSIVE_MODS", apply="recreate",
         item_pattern=r"^\d{4,8}$",
         help="Mods the server loads but clients aren't forced to download."),

    dict(key="custom_server_args", label="Extra launch flags", group="Mods",
         type="text", default="", target="env:CUSTOM_SERVER_ARGS", apply="recreate",
         max_len=400, ascii_only=True,
         help="Appended to the server command line, e.g. -ForceAllowCaveFlyers. "
              "Wrong values here stop a map booting, so change one at a time."),

    # ---------------------------------------------------------------- Rates
    dict(key="xp_multiplier", label="XP rate", group="Rates",
         type="float", default=1.0, min=0.1, max=1000.0,
         target="ini:GameUserSettings:ServerSettings:XPMultiplier", apply="reload",
         help="1.0 is vanilla."),

    dict(key="harvest_multiplier", label="Harvest rate", group="Rates",
         type="float", default=1.0, min=0.1, max=1000.0,
         target="ini:GameUserSettings:ServerSettings:HarvestAmountMultiplier",
         apply="reload", help="How much you get per swing. 1.0 is vanilla."),

    dict(key="taming_multiplier", label="Taming speed", group="Rates",
         type="float", default=1.0, min=0.1, max=1000.0,
         target="ini:GameUserSettings:ServerSettings:TamingSpeedMultiplier",
         apply="reload", help="Higher is faster. 1.0 is vanilla."),

    dict(key="maturation_multiplier", label="Baby maturation speed", group="Rates",
         type="float", default=1.0, min=0.1, max=1000.0,
         target="ini:Game:/Script/ShooterGame.ShooterGameMode:BabyMatureSpeedMultiplier",
         apply="reload", help="Higher is faster. Breeding-heavy servers push this up."),

    # ---------------------------------------------------------------- Upkeep
    dict(key="structure_decay", label="Decay abandoned structures", group="Upkeep",
         type="bool", default=True, invert=True,
         target="ini:GameUserSettings:ServerSettings:DisableStructureDecayPVE",
         apply="reload",
         help="On means abandoned bases decay by material over time so the map doesn't "
              "fill with junk. Active bases refresh their own timer, so this doesn't "
              "threaten anyone who still plays."),

    dict(key="wipe_times", label="Wild dino wipe times", group="Upkeep",
         type="times", default="", target="obelisk:wipe_times", apply="none",
         import_from="WIPE_TIMES",
         help="24-hour server-local times, comma separated. Blank disables wipes. "
              "Respawns fresh high-level wilds and clears overfarmed areas - it never "
              "touches anything a player owns."),

    dict(key="wipe_warn_minutes", label="Wipe warnings", group="Upkeep",
         type="minutes", default="10,5,1", target="obelisk:wipe_warn_minutes",
         apply="none", import_from="WIPE_WARN_MINUTES",
         help="Minutes before each wipe to warn players in game, comma separated."),

    # ---------------------------------------------------------------- Discord
    dict(key="discord_token", label="Discord bot token", group="Discord",
         type="password", default="", target="env:DISCORD_TOKEN", apply="reload",
         help="Leave blank to relay chat between maps only, with no Discord."),

    dict(key="discord_channel_id", label="Chat relay channel ID", group="Discord",
         type="text", default="", target="env:DISCORD_CHANNEL_ID", apply="reload",
         pattern=r"^\d{0,20}$", help="Right-click the channel in Discord, Copy ID."),

    dict(key="discord_tribelog_channel_id", label="Tribe log channel ID", group="Discord",
         type="text", default="", target="env:DISCORD_TRIBELOG_CHANNEL_ID",
         apply="reload", pattern=r"^\d{0,20}$", help="Optional. Blank disables tribe logs."),

    dict(key="discord_admin_channel_id", label="Admin channel ID", group="Discord",
         type="text", default="", target="env:DISCORD_ADMIN_CHANNEL_ID", apply="reload",
         pattern=r"^\d{0,20}$", help="Optional. Where admin-only notices go."),

    dict(key="discord_invite", label="Discord invite link", group="Discord",
         type="text", default="", target="env:DISCORD_INVITE", apply="reload",
         pattern=r"^(https://\S+)?$", max_len=200,
         help="Posted by the in-game !discord command. Blank means the command "
              "replies that no invite is configured."),

    dict(key="join_leave", label="Announce joins and leaves", group="Discord",
         type="bool", default=True, target="env:JOIN_LEAVE", apply="reload",
         help="Posts when a player connects or disconnects."),

    dict(key="welcome_enabled", label="Welcome new arrivals", group="Discord",
         type="bool", default=True, target="env:WELCOME_ENABLED", apply="reload",
         help="Whispers a greeting privately to the joining player rather than "
              "broadcasting it, so a busy server doesn't fill with welcome spam."),

    # ---------------------------------------------------------------- Obelisk
    dict(key="status_port", label="Status page port", group="Obelisk",
         type="port", default=8088, target="env:STATUS_PORT", apply="recreate",
         phase="install",
         help="The read-only status page and the admin UI. 0 turns both off."),

    dict(key="admin_token", label="Admin token", group="Obelisk",
         type="password", default="", target="obelisk:admin_token", apply="reload",
         help="Required to open /admin and to change anything. Blank means the admin "
              "side is switched off entirely and the page stays read-only."),

    dict(key="online_poll_seconds", label="Player count refresh", group="Obelisk",
         type="int", default=60, min=0, max=3600, target="env:ONLINE_POLL_SECONDS",
         apply="reload", help="Seconds between cluster-wide player count refreshes. 0 disables."),

    dict(key="timezone", label="Time zone", group="Obelisk",
         type="choice", default="UTC", choices=TIMEZONES, target="env:TZ",
         apply="reload",
         help="Drives wipe times, restart windows and log timestamps. Picked from the "
              "IANA zone list rather than typed: \"chicago\" and \"CST\" are not zones, "
              "and a name the server can't resolve silently leaves it on UTC."),

    # ---------------------------------------------------------------- Resources
    dict(key="mem_limit", label="RAM cap per map", group="Resources",
         type="memory", default="20g", target="env:MEM_LIMIT", apply="recreate",
         per_map=True,
         help="A cap, not a reservation - unused headroom costs nothing. If a map is "
              "OOM-killed you'll see it restart repeatedly with the container itself "
              "reporting a clean exit, because only the game process is killed. "
              "Override per map for the heavy ones."),
    dict(key="discord_admin_role_id", label="Admin role ID", group="Discord",
         type="text", default="", target="env:DISCORD_ADMIN_ROLE_ID", apply="reload",
         pattern=r"^\d{0,20}$",
         help="Optional. Only members with this Discord role may use admin commands. "
              "Blank means the admin channel itself is the only gate."),

    dict(key="serverfiles", label="Game install folder", group="Resources",
         type="text", default="", target="obelisk:serverfiles", apply="recreate",
         pattern=r"^(/[A-Za-z0-9._/-]{3,120})?$",
         help="Where the ~20 GB ARK server files live. Deliberately outside the data "
              "folder: it is re-downloadable, and keeping it out is what makes a backup "
              "small enough to move. Blank puts it beside the data folder."),

    dict(key="appdata", label="Cluster data folder", group="Resources",
         type="text", default="/mnt/user/appdata/ark", target="env:APPDATA",
         apply="recreate", phase="install", pattern=r"^/[A-Za-z0-9._/-]{3,120}$",
         help="Where server files, saves and the shared config live on the host. Put "
              "this on an SSD or NVMe pool, never the spinning array - ASA is very "
              "I/O hungry. Changing it moves the whole cluster and needs a recreate."),

    # ---------------------------------------------------------------- Cluster
    dict(key="maps", label="Maps to run", group="Cluster",
         type="maps", default="island", target="obelisk:maps", apply="recreate",
         help="Which maps this cluster runs, in order. The first one is the update "
              "master: it downloads the ~30 GB of server files once and the others "
              "wait for it, instead of every map fetching the same thing at once. "
              "Ports are assigned in this order, so reordering a live cluster moves "
              "everyone's ports - add to the end instead."),

    dict(key="game_port_base", label="First game port", group="Cluster",
         type="port", default=7777, target="obelisk:game_port_base", apply="recreate",
         help="Each map takes one UDP port counting up from here. Ten maps starting "
              "at 7777 uses 7777-7786. Make sure the range is free and forwarded."),

    dict(key="rcon_port_base", label="First RCON port", group="Cluster",
         type="port", default=27020, target="obelisk:rcon_port_base", apply="recreate",
         help="One TCP port per map counting up from here. Obelisk uses these to talk "
              "to each server. Do NOT forward these - RCON is admin access."),

    dict(key="ark_image", label="ARK server image", group="Cluster",
         type="text", default="acekorneya/asa_server:2_1_latest",
         target="obelisk:ark_image", apply="recreate", max_len=120, ascii_only=True,
         help="The container image each map runs. Pinning a tag rather than :latest "
              "means an upstream change can't surprise every map at once."),

    dict(key="obelisk_image", label="Obelisk image", group="Cluster",
         type="text", default="ghcr.io/obelisk-ark/obelisk:latest",
         target="obelisk:obelisk_image", apply="recreate", max_len=120, ascii_only=True,
         help="Obelisk's own image. Change this to run a fork or a pinned version."),

    dict(key="update_window_start", label="Update window opens", group="Cluster",
         type="text", default="4:00 AM", target="obelisk:update_window_start",
         apply="recreate", pattern=r"^\d{1,2}:\d{2} ?[AaPp][Mm]$",
         help="Servers only apply game updates inside this window, so a patch never "
              "restarts your cluster at peak time."),

    dict(key="update_window_end", label="Update window closes", group="Cluster",
         type="text", default="6:00 AM", target="obelisk:update_window_end",
         apply="recreate", pattern=r"^\d{1,2}:\d{2} ?[AaPp][Mm]$",
         help="The other end of the update window."),

    dict(key="restart_notice_minutes", label="Restart warning", group="Cluster",
         type="int", default=30, min=0, max=180,
         target="obelisk:restart_notice_minutes", apply="recreate",
         help="Minutes of in-game warning before a scheduled restart or update."),

    dict(key="host_ram_gb", label="RAM budget for this host", group="Resources",
         type="int", default=0, min=0, max=4096, target="obelisk:host_ram_gb",
         apply="none",
         help="How much memory this machine can give to ARK, in GB. Obelisk refuses to "
              "launch a cluster whose caps exceed it, rather than letting the host "
              "start killing servers under load. 0 turns the check off."),

    # ---------------------------------------------------------------- Advanced
    dict(key="extra_gameusersettings", label="Extra GameUserSettings.ini",
         group="Advanced", type="longtext", default="", max_len=20000,
         target="obelisk:extra_gameusersettings", apply="reload",
         help="Appended verbatim to the generated GameUserSettings.ini. Obelisk only "
              "models the settings above, so anything it doesn't know about - mod "
              "config blocks, rarely-used options - goes here and is passed through "
              "untouched. Full INI syntax, [Sections] and all."),

    dict(key="extra_game", label="Extra Game.ini", group="Advanced",
         type="longtext", default="", max_len=20000,
         target="obelisk:extra_game", apply="reload",
         help="Appended verbatim to the generated Game.ini. Same idea - breeding "
              "curves, per-dino overrides and anything else not modelled above."),

]

GROUPS = ["Cluster", "Identity", "Access", "Mods", "Rates", "Upkeep",
          "Discord", "Obelisk", "Resources", "Advanced"]

BY_KEY = {s["key"]: s for s in SETTINGS}

# Two phases, because Docker needs some answers before the app exists.
#
#   phase="install"  a bind mount or a published port - Docker has to know it at
#                    container-create time, so it comes from the container template
#                    (Unraid fields, or compose) and the UI shows it read-only.
#   phase="ui"       everything else. Not needed to boot, so it is set in Obelisk
#                    after the container is running. This is the default.
#
# Adding phase="install" to a setting is a promise that changing it requires
# recreating the container - keep the list as short as it honestly needs to be.
INSTALL_KEYS = [s["key"] for s in SETTINGS if s.get("phase") == "install"]



def markdown():
    out = ["# Obelisk settings\n", "_Generated from the schema - do not edit by hand._\n",
       "\nA handful of settings are marked **container template**: bind mounts and the\n"
       "published port, which Docker needs before Obelisk exists. Those are set when you\n"
       "create the container and are read-only in the UI. Everything else is set in the\n"
       "web UI after it is running.\n"]
    for g in GROUPS:
        rows = [s for s in SETTINGS if s["group"] == g]
        if not rows:
            continue
        out.append("\n## %s\n" % g)
        out.append("| Setting | Key | Type | Default | Set in | Takes effect |")
        out.append("|---|---|---|---|---|---|")
        for s in rows:
            d = "" if s.get("type") == "password" else str(s.get("default", ""))
            d = d.replace("|", "\\|")          # pipes would break the table row
            where = "container template" if s.get("phase") == "install" else "UI"
            out.append("| %s | `%s` | %s | `%s` | %s | %s |"
                       % (s["label"], s["key"], s["type"], d, where, s.get("apply", "none")))
        out.append("")
        for s in rows:
            out.append("**%s** - %s\n" % (s["label"], s.get("help", "").replace("|", "\\|")))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if "--markdown" in sys.argv:
        print(markdown())
    else:
        print("%d settings in %d groups" % (len(SETTINGS), len(GROUPS)))
