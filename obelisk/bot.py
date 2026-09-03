#!/usr/bin/env python3
"""
Obelisk - cross-map chat relay, scheduler and status page for an
ARK: Survival Ascended cluster.

Polls every map over RCON (GetChat), rebroadcasts global chat to the other
maps (ServerChat) and mirrors it to a Discord channel. Messages typed in
that Discord channel are pushed into every map. No in-game plugin needed.

Env:
  SERVERS            "Label=host:port,Label=host:port,..."   (required)
  RCON_PASSWORD      admin/RCON password shared by all maps  (required)
  DISCORD_TOKEN      bot token  (optional - without it, only map<->map relay runs)
  DISCORD_CHANNEL_ID numeric channel id for the relay channel (required if token set)
  DISCORD_TRIBELOG_CHANNEL_ID  optional channel for tribe logs (tames, deaths, raids...)
  JOIN_LEAVE         TRUE (default) posts "joined/left <map>" notices to the relay channel
  DISCORD_ADMIN_CHANNEL_ID  optional admin channel: receives AdminCmd lines + map up/down alerts,
                     and accepts !players / !broadcast <msg> / !save [map] / !rcon <map|all> <cmd>
  DISCORD_ADMIN_ROLE_ID     optional: only members with this role may use admin commands
                     (if unset, anyone who can post in the admin channel may)
  POLL_SECONDS       how often to poll each map (default 2)
  GAME_FORMAT        in-game line format (default "[{label}] {name}: {msg}")
  DISCORD_FORMAT     Discord line format (default "**[{label}] {name}**: {msg}")
"""
import asyncio, json, logging, os, re, struct, sys, time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("obelisk")

SERVERS = {}
for item in os.environ.get("SERVERS", "").split(","):
    item = item.strip()
    if not item or "=" not in item:
        continue
    label, hp = item.split("=", 1)
    host, port = hp.rsplit(":", 1)
    SERVERS[label.strip()] = (host.strip(), int(port))

RCON_PASSWORD = os.environ.get("RCON_PASSWORD", "")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
DISCORD_CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0") or 0)
TRIBELOG_CHANNEL_ID = int(os.environ.get("DISCORD_TRIBELOG_CHANNEL_ID", "0") or 0)
JOIN_LEAVE = os.environ.get("JOIN_LEAVE", "TRUE").upper() == "TRUE"
ADMIN_CHANNEL_ID = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0") or 0)
ADMIN_ROLE_ID = int(os.environ.get("DISCORD_ADMIN_ROLE_ID", "0") or 0)
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "2"))
GAME_FORMAT = os.environ.get("GAME_FORMAT", "[{label}] {name}: {msg}")
DISCORD_FORMAT = os.environ.get("DISCORD_FORMAT", "**[{label}] {name}**: {msg}")

# ---- player-facing chat commands (typed in-game by any player) ----
# Blank by default - set DISCORD_INVITE in the stack .env to enable !discord.
DISCORD_INVITE = os.environ.get("DISCORD_INVITE", "").strip()
# Reply shown for !help / !list. Keep it one line, plain ASCII (in-game chat is picky).
PLAYER_HELP_MSG = os.environ.get(
    "PLAYER_HELP_MSG",
    "Commands: !help (list), !online (who is on), !discord (invite)").replace("\\n", "\n")
# Welcome broadcast to a map when a player joins it. {name} and {label} are filled in.
WELCOME_ENABLED = os.environ.get("WELCOME_ENABLED", "TRUE").upper() == "TRUE"
# Use \n to force a line break; each line is sent as its own chat row.
WELCOME_MSG = os.environ.get(
    "WELCOME_MSG",
    "Welcome {name} to {cluster} - {label}!|Type !help in chat for commands.").replace("\\n", "\n")

# ---- scheduled wild-dino wipes (single source: .env; warns players in-game) ----
def _hhmm_to_min(x):
    x=x.strip()
    if ":" not in x: return None
    h,m=x.split(":"); return int(h)*60+int(m)
WIPE_TIMES = [x.strip() for x in os.environ.get("WIPE_TIMES","").split(",") if x.strip()]
WIPE_WARN_MINUTES = sorted({int(x) for x in os.environ.get("WIPE_WARN_MINUTES","10,5,1").split(",") if x.strip()}, reverse=True)
# How often to refresh the cluster-wide online count (seconds). 0 disables the poller.
ONLINE_POLL_SECONDS = int(os.environ.get("ONLINE_POLL_SECONDS", "60"))
# Read-only status page. 0 disables. Optional host stats feed for memory/restarts.
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "ARK Cluster")
STATUS_PORT = int(os.environ.get("STATUS_PORT", "8088"))
STATS_FILE = os.environ.get("STATS_FILE", "/home/pok/shared/cluster_stats.json")

if not SERVERS or not RCON_PASSWORD:
    log.error("SERVERS and RCON_PASSWORD must be set"); sys.exit(1)

# ---------------------------------------------------------------- Source RCON
SERVERDATA_AUTH, SERVERDATA_EXECCOMMAND, SERVERDATA_RESPONSE_VALUE = 3, 2, 0

async def rcon(host, port, command, timeout=6.0):
    """Open a connection, auth, run one command, return the response text."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        async def send(pid, ptype, body):
            payload = struct.pack("<ii", pid, ptype) + body.encode("utf-8", "replace") + b"\x00\x00"
            writer.write(struct.pack("<i", len(payload)) + payload)
            await writer.drain()
        async def recv():
            raw = await asyncio.wait_for(reader.readexactly(4), timeout)
            (size,) = struct.unpack("<i", raw)
            data = await asyncio.wait_for(reader.readexactly(size), timeout)
            pid, ptype = struct.unpack("<ii", data[:8])
            return pid, ptype, data[8:-2].decode("utf-8", "replace")
        await send(1, SERVERDATA_AUTH, RCON_PASSWORD)
        pid, ptype, _ = await recv()
        if ptype == SERVERDATA_RESPONSE_VALUE:      # some servers send an empty value packet first
            pid, ptype, _ = await recv()
        if pid == -1:
            raise PermissionError("RCON auth failed")
        await send(2, SERVERDATA_EXECCOMMAND, command)
        _, _, body = await recv()
        return body
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

# ---------------------------------------------------------------- chat parsing
# ASA GetChat lines look like:  "PlayerName (CharacterName): hello"  or "SERVER: text"
CHAT_RE = re.compile(r"^(?P<player>.+?) \((?P<char>.+?)\): (?P<msg>.+)$")
IGNORE = ("Server received, But no response!!", "AdminCmd", "RCON: Not connected")

# ASA also pushes presence through the chat feed:
#   "2026.08.31_06.01.54: Bethii_94 [UniqueNetId:abc... Platform:None] joined"
PRESENCE_RE = re.compile(r"(?P<name>[^:\[\]]+?)\s*\[UniqueNetId:[^\]]*\]\s*(?P<what>joined|left)\b", re.I)

NETID_RE = re.compile(r"\s*\[UniqueNetId:[^\]]*\]\s*")
TS_PREFIX_RE = re.compile(r"^\s*\d{4}\.\d{2}\.\d{2}[_\-][\d.:]+:\s*")

def clean_name(raw):
    """'2026.08.31_06.19.15: SomePlayer [UniqueNetId:abc Platform:None]' -> 'SomePlayer'"""
    n = NETID_RE.sub(" ", raw)
    n = TS_PREFIX_RE.sub("", n)
    n = re.sub(r"\s*\[[^\]]*\]\s*$", "", n)          # any other trailing [..] block
    return n.strip(" :") or raw.strip()

def parse_presence(text):
    """Join/leave events hidden in GetChat output -> [(name, 'joined'|'left')]."""
    out = []
    for line in text.splitlines():
        m = PRESENCE_RE.search(line)
        if m:
            name = m.group("name").split(":")[-1].strip()      # drop any "timestamp:" prefix
            out.append((name, m.group("what").lower()))
    return out

def parse_chat(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or any(x in line for x in IGNORE) or "[UniqueNetId:" in line:
            continue
        if line.startswith("SERVER:"):         # our own relays / admin broadcasts -> never re-relay
            continue
        m = CHAT_RE.match(line)
        if not m:
            continue
        msg = m.group("msg").strip()
        if not msg or msg.startswith("/"):    # slash commands are not chat
            continue
        out.append((m.group("char").strip() or m.group("player").strip(), msg))
    return out

# ---------------------------------------------------------------- game log parsing
# GetGameLog lines look like:
#   [2026.08.31-05.10.12]: Tribe Alpha, ID 123: Day 5, 12:34:56: <RichColor Color="1,1,0,1">Bob Tamed a Dodo - Lvl 10!</>
#   [2026.08.31-05.10.12]: Bob joined this ARK!
LOG_TS_RE = re.compile(r"^\[[\d.\-:]+\]:\s*")
RICH_RE = re.compile(r"<RichColor[^>]*>|</>")
JOIN_RE = re.compile(r"^(?P<name>.+?) (?P<what>joined|left) this ARK!?$", re.I)
TRIBE_RE = re.compile(r"^Tribe (?P<tribe>.+?), ID (?P<tid>\d+): Day (?P<day>\d+), (?P<clock>[\d:]+): (?P<msg>.+)$")

def tribe_color(msg):
    m = msg.lower()
    if "tamed" in m or "claimed" in m or "hatched" in m:      return 0x2ecc71   # green
    if "killed" in m or "destroyed" in m or "died" in m or "was auto-decay" in m: return 0xe74c3c  # red
    if "demolished" in m or "unclaimed" in m or "starved" in m: return 0xf1c40f # yellow
    return 0x5865f2                                             # blurple

def parse_gamelog(text):
    """Return (tribe_lines, join_events, admin_lines) from a GetGameLog response."""
    tribe, joins, admin = [], [], []
    for line in text.splitlines():
        line = LOG_TS_RE.sub("", RICH_RE.sub("", line.strip())).strip()
        if not line or "Server received, But no response" in line:
            continue
        if "AdminCmd" in line:
            admin.append(line)
            continue
        if line.startswith("Tribe "):
            m = TRIBE_RE.match(line)
            if m:
                tribe.append({"tribe": m.group("tribe"), "tid": m.group("tid"),
                              "day": m.group("day"), "clock": m.group("clock"), "msg": m.group("msg").strip()})
            else:
                tribe.append({"tribe": "", "tid": "", "day": "", "clock": "", "msg": line})
            continue
        m = JOIN_RE.match(line)
        if m:
            joins.append((m.group("name").strip(), m.group("what").lower()))
    return tribe, joins, admin

# ---------------------------------------------------------------- relay core
class Relay:
    def __init__(self):
        self.discord_send = None            # set by the Discord side when ready
        self.tribelog_send = None           # set by the Discord side if a tribelog channel is configured
        self.admin_send = None              # set by the Discord side if an admin channel is configured
        self.down = set()                   # labels currently unreachable (for one-shot alerts)
        self.broken = {}                    # label -> next retry time
        self.recent = {}                    # (label, name, what) -> time, for join/leave de-dupe
        self.online_by_map = {}             # label -> player count (refreshed by poll_online)
        self.online_total = 0               # cluster-wide player count, cached
        self.map_up = {}                    # label -> bool, RCON reachable
        self.last_refresh = 0.0             # epoch of last successful refresh

    async def broadcast(self, text, exclude=None):
        """Send a ServerChat to every map except `exclude`."""
        async def one(label, hp):
            try:
                await rcon(hp[0], hp[1], f"ServerChat {text}")
            except Exception as e:
                log.warning("ServerChat -> %s failed: %s", label, e)
        await asyncio.gather(*(one(l, hp) for l, hp in SERVERS.items() if l != exclude))

    async def poll_one(self, label, hp):
        if time.time() < self.broken.get(label, 0):
            return
        try:
            text = await rcon(hp[0], hp[1], "GetChat")
        except Exception as e:
            log.warning("GetChat %s failed (%s) - backing off 30s", label, e)
            self.broken[label] = time.time() + 30
            if label not in self.down:
                self.down.add(label)
                await self.admin_note(f"\u26a0\ufe0f **{label}** is not answering RCON ({e or 'timeout'})")
            return
        if label in self.down:
            self.down.discard(label)
            await self.admin_note(f"\u2705 **{label}** is back")
        for name, what in parse_presence(text):
            await self.presence(label, name, what)
        for name, msg in parse_chat(text):
            if msg.strip().startswith("!"):
                if await self.player_command(label, hp, name, msg):
                    continue                      # handled as a command; don't relay it
            log.info("[%s] %s: %s", label, name, msg)
            game_line = GAME_FORMAT.format(label=label, name=name, msg=msg)
            await self.broadcast(game_line, exclude=label)
            if self.discord_send:
                try:
                    await self.discord_send(DISCORD_FORMAT.format(label=label, name=name, msg=msg))
                except Exception as e:
                    log.warning("discord send failed: %s", e)

    async def say(self, hp, text):
        """Post into ONE map's in-game chat. A message may contain \\n line breaks;
        each line is sent as its own chat row (so ARK never wraps mid-word)."""
        for line in str(text).replace("|", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                await rcon(hp[0], hp[1], f"ServerChat {line}")
            except Exception as e:
                log.warning("ServerChat (say) failed: %s", e)

    async def whisper(self, hp, player, text):
        """Send text to ONE player by name (private). Splits on | / newline into rows."""
        for line in str(text).replace("|", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                await rcon(hp[0], hp[1], f'ServerChatToPlayer "{player}" {line}')
            except Exception as e:
                log.warning("whisper failed: %s", e)

    async def announce(self, text):
        """Broadcast a notice to every map's in-game chat and the Discord relay channel."""
        await self.broadcast(text)
        if self.discord_send:
            try:
                await self.discord_send(text)
            except Exception as e:
                log.warning("announce discord failed: %s", e)

    async def wipe_wild(self):
        async def one(label, hp):
            try:
                await rcon(hp[0], hp[1], "DestroyWildDinos")
                log.info("DestroyWildDinos -> %s", label)
            except Exception as e:
                log.warning("DestroyWildDinos %s failed: %s", label, e)
        await asyncio.gather(*(one(l, hp) for l, hp in SERVERS.items()))

    async def maintenance_loop(self):
        """Fire scheduled wild-dino wipes with in-game countdown warnings."""
        targets = [t for t in (_hhmm_to_min(x) for x in WIPE_TIMES) if t is not None]
        if not targets:
            return
        log.info("wild-dino wipes at %s (warn %s min before)", ", ".join(WIPE_TIMES), WIPE_WARN_MINUTES)
        fired = set()
        while True:
            now = time.localtime()
            day = now.tm_yday
            cur = now.tm_hour * 60 + now.tm_min
            for T in targets:
                for w in WIPE_WARN_MINUTES:
                    if cur == (T - w) % 1440:
                        k = (day, "warn", T, w)
                        if k not in fired:
                            fired.add(k)
                            await self.announce(f"\u26a0\ufe0f Wild dinos wipe in {w} minute{'s' if w != 1 else ''} - fresh spawns incoming!")
                if cur == T:
                    k = (day, "wipe", T)
                    if k not in fired:
                        fired.add(k)
                        await self.announce("\U0001f996 Wiping wild dinos now - fresh spawns incoming!")
                        await self.wipe_wild()
            if len(fired) > 500:
                fired = {x for x in fired if x[0] == day}
            await asyncio.sleep(20)

    @staticmethod
    def _count_players(text):
        """Count players in an RCON ListPlayers response."""
        if not text:
            return 0
        low = text.lower()
        if "no players" in low:
            return 0
        # ASA lists one player per line as "0. Name, <netid>"
        n = len(re.findall(r"(?m)^\s*\d+\.\s+\S", text))
        if n:
            return n
        # fall back: non-empty lines that look like entries
        return sum(1 for ln in text.splitlines() if ln.strip() and "," in ln)

    async def refresh_online(self):
        """Ask every map who is connected; update the cached cluster count."""
        async def one(label, hp):
            try:
                txt = await rcon(hp[0], hp[1], "ListPlayers")
                return label, self._count_players(txt)
            except Exception:
                return label, None            # unreachable: keep last known below
        results = await asyncio.gather(*(one(l, hp) for l, hp in SERVERS.items()))
        for label, n in results:
            self.map_up[label] = n is not None
            if n is not None:
                self.online_by_map[label] = n
        self.online_total = sum(self.online_by_map.values())
        self.last_refresh = time.time()
        return self.online_total

    async def poll_online(self):
        """Background refresh so join-time whispers cost no extra RCON calls."""
        if ONLINE_POLL_SECONDS <= 0:
            log.info("cluster online poller disabled")
            return
        log.info("cluster online poller every %ss", ONLINE_POLL_SECONDS)
        while True:
            try:
                await self.refresh_online()
            except Exception as e:
                log.warning("refresh_online failed: %s", e)
            await asyncio.sleep(ONLINE_POLL_SECONDS)

    def online_summary(self):
        """One-line cluster population summary for in-game chat."""
        total = self.online_total
        if total <= 0:
            return "No other survivors online right now - you have the cluster to yourself!"
        busy = sorted(((n, l) for l, n in self.online_by_map.items() if n > 0), reverse=True)
        where = ", ".join(f"{l} {n}" for n, l in busy)
        s = "s" if total != 1 else ""
        return f"{total} survivor{s} online across the cluster: {where}"

    # ---------------------------------------------------------- status page
    def _stats_file(self):
        """Optional JSON written by a host-side cron (docker stats / restart counts)."""
        try:
            if time.time() - os.path.getmtime(STATS_FILE) > 300:
                return {}
            with open(STATS_FILE, "r") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def next_wipe_text(self):
        if not WIPE_TIMES:
            return "disabled"
        now = time.localtime()
        cur = now.tm_hour * 60 + now.tm_min
        mins = sorted(m for m in (_hhmm_to_min(t) for t in WIPE_TIMES) if m is not None)
        if not mins:
            return "disabled"
        nxt = next((m for m in mins if m > cur), mins[0] + 1440)
        left = nxt - cur
        return "%02d:%02d  (in %dh %02dm)" % ((nxt % 1440) // 60, nxt % 60, left // 60, left % 60)

    def status_data(self):
        stats = self._stats_file()
        maps = []
        for label in SERVERS:
            s = stats.get(label, {}) if isinstance(stats, dict) else {}
            maps.append({
                "map": label,
                "up": bool(self.map_up.get(label)),
                "players": self.online_by_map.get(label, 0),
                "mem": s.get("mem"), "mem_pct": s.get("mem_pct"),
                "restarts": s.get("restarts"), "uptime": s.get("uptime"),
            })
        return {
            "total": self.online_total,
            "maps_up": sum(1 for m in maps if m["up"]),
            "maps_total": len(maps),
            "next_wipe": self.next_wipe_text(),
            "wipe_times": ", ".join(WIPE_TIMES) if WIPE_TIMES else "disabled",
            "last_refresh": int(time.time() - self.last_refresh) if self.last_refresh else None,
            "stats_age": stats.get("_age_seconds") if isinstance(stats, dict) else None,
            "maps": maps,
        }

    def status_html(self):
        d = self.status_data()
        rows = []
        for m in d["maps"]:
            dot = "ok" if m["up"] else "bad"
            state = "up" if m["up"] else "DOWN"
            mem = "-" if m["mem"] is None else m["mem"]
            pct = m["mem_pct"]
            memcls = "warn" if (isinstance(pct, (int, float)) and pct >= 75) else ""
            memtxt = mem if pct is None else "%s <span class=pct>(%s%%)</span>" % (mem, pct)
            rs = m["restarts"]
            rscls = "warn" if (isinstance(rs, int) and rs > 0) else ""
            rows.append(
                "<tr><td><span class='dot %s'></span>%s</td><td class=%s>%s</td>"
                "<td class=num>%s</td><td class='num %s'>%s</td><td class='num %s'>%s</td></tr>"
                % (dot, m["map"], dot, state, m["players"], memcls, memtxt, rscls,
                   "-" if rs is None else rs))
        age = d["last_refresh"]
        agetxt = "just now" if age is None or age < 5 else "%ds ago" % age
        note = ""
        if all(m["mem"] is None for m in d["maps"]):
            note = ("<p class=note>Memory and restart columns need the host stats feed "
                    "(cluster_stats.json). Not present yet - everything else is live.</p>")
        return """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=30><title>%s Status</title><style>
:root{color-scheme:dark}
body{background:#12151a;color:#e6e9ef;font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:18px;margin:0 0 2px;letter-spacing:.3px}
.sub{color:#8b94a3;font-size:12px;margin-bottom:18px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.card{background:#1a1f27;border:1px solid #262d38;border-radius:10px;padding:12px 16px;min-width:130px}
.card .k{color:#8b94a3;font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.card .v{font-size:22px;font-weight:600;margin-top:2px}
table{width:100%%;border-collapse:collapse;background:#1a1f27;border:1px solid #262d38;border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#8b94a3;padding:10px 14px;border-bottom:1px solid #262d38}
td{padding:10px 14px;border-bottom:1px solid #20262f}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%%;margin-right:9px;vertical-align:middle}
.dot.ok{background:#3fb950}.dot.bad{background:#f85149}
td.ok{color:#3fb950}td.bad{color:#f85149;font-weight:600}
.warn{color:#d29922}
.pct{color:#8b94a3;font-size:12px}
.note{color:#8b94a3;font-size:12px;margin-top:14px}
.foot{color:#5b6472;font-size:11px;margin-top:16px}
</style></head><body><div class=wrap>
<h1>%s</h1>
<div class=sub>read-only status &middot; auto-refreshes every 30s</div>
<div class=cards>
<div class=card><div class=k>Players online</div><div class=v>%s</div></div>
<div class=card><div class=k>Maps up</div><div class=v>%s / %s</div></div>
<div class=card><div class=k>Next wild wipe</div><div class=v style=font-size:16px>%s</div></div>
</div>
<table><tr><th>Map</th><th>State</th><th class=num>Players</th><th class=num>Memory</th><th class=num>Restarts</th></tr>
%s
</table>
%s
<div class=foot>RCON polled %s &middot; wipe schedule %s</div>
</div></body></html>""" % (CLUSTER_NAME, CLUSTER_NAME, d["total"], d["maps_up"], d["maps_total"], d["next_wipe"],
                           "\n".join(rows), note, agetxt, d["wipe_times"])

    async def status_server(self):
        if STATUS_PORT <= 0:
            log.info("status page disabled")
            return
        try:
            from aiohttp import web
        except Exception as e:
            log.warning("status page unavailable (aiohttp missing): %s", e)
            return
        async def page(_req):
            return web.Response(text=self.status_html(), content_type="text/html")
        async def api(_req):
            return web.json_response(self.status_data())
        app = web.Application()
        app.router.add_get("/", page)
        app.router.add_get("/api/status", api)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", STATUS_PORT).start()
        log.info("status page on :%d", STATUS_PORT)

    async def player_command(self, label, hp, name, msg):
        """Handle an in-game !command from a player. Returns True if it was a command."""
        cmd = msg.strip().lower().split()[0]
        if cmd in ("!help", "!list", "!commands"):
            await self.say(hp, PLAYER_HELP_MSG)
            return True
        if cmd == "!discord":
            if DISCORD_INVITE:
                await self.say(hp, f"Join our Discord: {DISCORD_INVITE}")
            else:
                await self.say(hp, "No Discord invite is configured.")
            return True
        if cmd in ("!online", "!players", "!pop"):
            await self.refresh_online()       # explicit ask gets fresh numbers
            await self.say(hp, self.online_summary())
            return True
        return False                              # unknown ! - let it relay as normal chat

    async def presence(self, label, name, what):
        name = clean_name(name)
        key = (label, name, what)
        now = time.time()
        if now - self.recent.get(key, 0) < 20:          # same event seen twice (chat feed + game log)
            return
        self.recent[key] = now
        log.info("[%s] %s %s", label, name, what)
        if what == "joined" and WELCOME_ENABLED:
            hp = SERVERS.get(label)
            if hp:
                await self.whisper(hp, name, WELCOME_MSG.format(name=name, label=label, cluster=CLUSTER_NAME))
                if self.online_total > 0:
                    await self.whisper(hp, name, self.online_summary())
        if JOIN_LEAVE and self.discord_send:
            arrow = "\u2192" if what == "joined" else "\u2190"
            try:
                await self.discord_send(f"{arrow} Player **{name}** {what} {label}")
            except Exception as e:
                log.warning("join/leave send failed: %s", e)

    async def admin_note(self, text):
        if self.admin_send:
            try:
                await self.admin_send(text)
            except Exception as e:
                log.warning("admin send failed: %s", e)

    async def poll_gamelog(self, label, hp):
        """Tribe logs -> tribelog channel; joined/left -> relay channel."""
        if time.time() < self.broken.get(label, 0):
            return
        if not (self.tribelog_send or self.admin_send or (JOIN_LEAVE and self.discord_send)):
            return
        try:
            text = await rcon(hp[0], hp[1], "GetGameLog")
        except Exception:
            return                                   # poll_one already logs/backs off for this map
        tribe, joins, admin = parse_gamelog(text)
        for line in admin:
            await self.admin_note(f"\U0001f6e0\ufe0f **[{label}]** {line}")
        if tribe and self.tribelog_send:
            for i in range(0, len(tribe), 10):
                try:
                    await self.tribelog_send(label, tribe[i:i + 10])
                except Exception as e:
                    log.warning("tribelog send failed: %s", e)
        for name, what in joins:
            await self.presence(label, name, what)

    async def poll_forever(self):
        log.info("relaying chat between: %s", ", ".join(SERVERS))
        while True:
            await asyncio.gather(*(self.poll_one(l, hp) for l, hp in SERVERS.items()))
            await asyncio.gather(*(self.poll_gamelog(l, hp) for l, hp in SERVERS.items()))
            await asyncio.sleep(POLL_SECONDS)

# ---------------------------------------------------------------- discord side
async def run_discord(relay):
    import discord
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        ch = client.get_channel(DISCORD_CHANNEL_ID)
        if ch is None:
            log.error("Discord channel %s not found - is the bot in the server?", DISCORD_CHANNEL_ID)
            return
        async def send(text):
            await ch.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
        relay.discord_send = send
        log.info("Discord ready as %s, relaying #%s", client.user, getattr(ch, "name", ch.id))
        if TRIBELOG_CHANNEL_ID:
            tl = client.get_channel(TRIBELOG_CHANNEL_ID)
            if tl is None:
                log.error("Tribelog channel %s not found / not visible to the bot", TRIBELOG_CHANNEL_ID)
            else:
                async def send_tl(label, entries):
                    embeds = []
                    for e in entries:
                        title = f"{label}" + (f" \u2022 Tribe {e['tribe']}" if e['tribe'] else "")
                        em = discord.Embed(title=title[:256], description=e["msg"][:4000], color=tribe_color(e["msg"]))
                        foot = " ".join(x for x in (f"Day {e['day']} {e['clock']}" if e['day'] else "",
                                                     f"TribeID {e['tid']}" if e['tid'] else "") if x)
                        if foot:
                            em.set_footer(text=foot)
                        em.timestamp = discord.utils.utcnow()
                        embeds.append(em)
                    await tl.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
                relay.tribelog_send = send_tl
                log.info("Tribe logs -> #%s", getattr(tl, "name", tl.id))
        if ADMIN_CHANNEL_ID:
            ad = client.get_channel(ADMIN_CHANNEL_ID)
            if ad is None:
                log.error("Admin channel %s not found / not visible to the bot", ADMIN_CHANNEL_ID)
            else:
                async def send_ad(text):
                    await ad.send(text[:1990], allowed_mentions=discord.AllowedMentions.none())
                relay.admin_send = send_ad
                log.info("Admin channel -> #%s (role gate: %s)", getattr(ad, "name", ad.id), ADMIN_ROLE_ID or "none")

    def is_admin(member):
        if not ADMIN_ROLE_ID:
            return True
        return any(r.id == ADMIN_ROLE_ID for r in getattr(member, "roles", []))

    def find_map(word):
        w = word.lower().replace("_", " ")
        for label in SERVERS:
            if label.lower() == w or label.lower().replace(" ", "") == w.replace(" ", ""):
                return label
        for label in SERVERS:                       # prefix match: "isl" -> The Island
            if label.lower().replace("the ", "").startswith(w.replace("the ", "")):
                return label
        return None

    async def run_admin_command(message):
        text = message.content.strip()
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()
        async def reply(t):
            await message.channel.send(t[:1990], allowed_mentions=discord.AllowedMentions.none())
        if cmd in ("!help", "!maps"):
            await reply("Maps: " + ", ".join(SERVERS) + "\n"
                        "`!players` \u2022 `!broadcast <msg>` \u2022 `!save [map|all]` \u2022 `!rcon <map|all> <command>`")
            return
        if not is_admin(message.author):
            await reply("You need the admin role to use that."); return
        if cmd == "!players":
            out = []
            for label, hp in SERVERS.items():
                try:
                    r = (await rcon(hp[0], hp[1], "ListPlayers")).strip()
                    names = [l.split(". ", 1)[-1].split(",")[0] for l in r.splitlines() if ". " in l]
                    out.append(f"**{label}**: " + (", ".join(names) if names else "nobody"))
                except Exception as e:
                    out.append(f"**{label}**: unreachable")
            await reply("\n".join(out)); return
        if cmd == "!broadcast":
            msg = text[len("!broadcast"):].strip()
            if not msg: await reply("Usage: `!broadcast <message>`"); return
            await relay.broadcast(f"[Admin] {msg}")
            await reply(f"Broadcast to all maps: {msg}"); return
        if cmd == "!save":
            target = find_map(parts[1]) if len(parts) > 1 and parts[1].lower() != "all" else None
            targets = [target] if target else list(SERVERS)
            res = []
            for label in targets:
                hp = SERVERS[label]
                try:
                    await rcon(hp[0], hp[1], "SaveWorld"); res.append(f"{label}: saved")
                except Exception:
                    res.append(f"{label}: failed")
            await reply("\n".join(res)); return
        if cmd == "!rcon":
            if len(parts) < 3: await reply("Usage: `!rcon <map|all> <command>`"); return
            targets = list(SERVERS) if parts[1].lower() == "all" else [find_map(parts[1])]
            if targets == [None]: await reply(f"Unknown map `{parts[1]}` - try `!maps`"); return
            res = []
            for label in targets:
                hp = SERVERS[label]
                try:
                    r = (await rcon(hp[0], hp[1], parts[2])).strip() or "(no output)"
                    res.append(f"**{label}**: {r[:400]}")
                except Exception as e:
                    res.append(f"**{label}**: failed ({e or 'timeout'})")
            log.info("[admin] %s ran %r on %s", message.author, parts[2], ",".join(targets))
            await reply("\n".join(res)); return

    @client.event
    async def on_message(message):
        if message.author.bot:
            return
        if ADMIN_CHANNEL_ID and message.channel.id == ADMIN_CHANNEL_ID:
            if message.content.startswith("!"):
                try:
                    await run_admin_command(message)
                except Exception as e:
                    log.warning("admin command failed: %s", e)
            return
        if message.channel.id != DISCORD_CHANNEL_ID:
            return
        content = message.clean_content.strip()
        if not content:
            return
        name = message.author.display_name
        log.info("[Discord] %s: %s", name, content)
        await relay.broadcast(GAME_FORMAT.format(label="Discord", name=name, msg=content))

    await client.start(DISCORD_TOKEN)

async def main():
    relay = Relay()
    tasks = [asyncio.create_task(relay.poll_forever())]
    tasks.append(asyncio.create_task(relay.maintenance_loop()))
    tasks.append(asyncio.create_task(relay.poll_online()))
    tasks.append(asyncio.create_task(relay.status_server()))
    if DISCORD_TOKEN and DISCORD_CHANNEL_ID:
        tasks.append(asyncio.create_task(run_discord(relay)))
    else:
        log.info("DISCORD_TOKEN / DISCORD_CHANNEL_ID not set - running map<->map relay only")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
