"""
The relay comes up on its own, says when it cannot, and Discord hears about it anyway.

From 4 October: the manager had restarted while the maps were down, logged "relay idle
until maps are launched", and never started the relay again. The bot token and three
channel IDs were set, and a twenty-minute update posted nothing to Discord. The cluster
page meanwhile said "Generating the world" for every map, and an Apply refused over an
unreadable player count went back to "1 change pending" without a word.

No game servers here - the relay's run, the RCON probe and Discord's HTTP API are all
handed in, the same way the rest of the suite tests the apply without a Docker socket.
"""

import asyncio
import sys
import time

from . import announce, app, bot, discordrest, progress, ui

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def drain():
    out, batch = [], announce.pop_all(limit=100)
    while batch:
        out += batch
        batch = announce.pop_all(limit=100)
    return out


class Stop(Exception):
    """Ends a loop that would otherwise run forever."""


def sleeper(limit):
    """A sleep that records what it was asked for and stops the loop after `limit`."""
    asked = []

    async def sleep(seconds):
        asked.append(seconds)
        if len(asked) >= limit:
            raise Stop()
    return sleep, asked


# ---------------------------------------------------------------- the supervisor

_real_wire, _real_reach = app._wire_relay, app.clusterctl.reachable
_maps_up = {"now": False}


def fake_wire(store, b):
    if not _maps_up["now"]:
        return False
    b.SERVERS = {"The Island": ("asa-island", 27020), "The Center": ("asa-center", 27021)}
    return True


app._wire_relay = fake_wire
app.clusterctl.reachable = lambda store, **kw: (["The Island", "The Center"], [])

try:
    # No maps yet: it waits, and does not give up.
    drain()
    app.RELAY_INFO.clear()
    runs = []

    async def never_run():
        runs.append(1)

    sleep, asked = sleeper(3)
    try:
        asyncio.run(app.relay_supervisor({}, bot, sleep=sleep, run=never_run))
    except Stop:
        pass
    check("with no maps running the relay is not started", runs == [], runs)
    check("but it keeps looking rather than giving up", len(asked) == 3, asked)
    check("and the page is told why it is not up",
          app.RELAY_INFO.get("state") == "waiting"
          and "starts on its own" in app.RELAY_INFO.get("why", ""), app.RELAY_INFO)

    # Maps come up later: it starts, without anybody restarting the manager.
    _maps_up["now"] = True
    calls = {"n": 0}

    async def crashes():
        calls["n"] += 1
        raise RuntimeError("gateway closed")

    drain()
    app.RELAY_INFO.clear()
    sleep, asked = sleeper(4)
    try:
        asyncio.run(app.relay_supervisor({}, bot, sleep=sleep, run=crashes,
                                         alert_after=3))
    except Stop:
        pass
    events = drain()
    names = [e["event"] for e in events]
    check("once maps are running it starts on its own", calls["n"] >= 1, calls)
    check("and is started again after it stops", calls["n"] == 4, calls)
    check("with a backoff that grows", asked[:3] == [5, 10, 20], asked)
    check("the first stop is said, as a warning",
          names.count("relay.restarting") == 1
          and [e for e in events if e["event"] == "relay.restarting"][0]["level"]
          == "warning", names)
    check("with the reason in it",
          any("gateway closed" in e["text"] for e in events
              if e["event"] == "relay.restarting"), names)
    check("repeated stops become an error alert, said once",
          names.count("relay.failing") == 1
          and [e for e in events if e["event"] == "relay.failing"][0]["level"] == "error",
          names)
    check("and the page knows it is failing, not merely restarting",
          app.RELAY_INFO.get("state") == "failing", app.RELAY_INFO)
    check("coverage is announced once, not on every restart",
          names.count("relay.up") == 1, names)
finally:
    app._wire_relay, app.clusterctl.reachable = _real_wire, _real_reach


# ---------------------------------------------------------------- coverage

drain()
app.RELAY_INFO.clear()
app._say_coverage(0, 5, [("a", "x"), ("b", "x"), ("c", "x"), ("d", "x"), ("e", "x")])
app._say_coverage(0, 5, [("a", "x"), ("b", "x"), ("c", "x"), ("d", "x"), ("e", "x")])
app._say_coverage(5, 5, [])
_cov = [e["event"] for e in drain()]
check("an unchanged coverage reading is not said twice", _cov.count("relay.degraded") == 1,
      _cov)
check("but recovering from 0 of 5 is said, so the feed does not end on it",
      _cov[-1:] == ["relay.up"], _cov)


# ---------------------------------------------------------------- bot.main

_real_df = bot.discord_forever
_real_port = bot.STATUS_PORT
_real_servers = bot.SERVERS
_real_poll = bot.ONLINE_POLL_SECONDS


async def _df_boom(relay):
    await asyncio.sleep(0.05)
    raise RuntimeError("boom")


bot.discord_forever = _df_boom
bot.STATUS_PORT = 0
bot.SERVERS = {}
bot.ONLINE_POLL_SECONDS = 0
_left = []


async def _main_and_count():
    try:
        await bot.main()
    except RuntimeError:
        pass
    await asyncio.sleep(0)
    _left.extend(t for t in asyncio.all_tasks() if t is not asyncio.current_task())


try:
    asyncio.run(_main_and_count())
    check("a relay that fails leaves nothing of itself running", _left == [], _left)
    check("and stops claiming to be the live relay", bot.LIVE is None, bot.LIVE)
finally:
    bot.discord_forever = _real_df
    bot.STATUS_PORT = _real_port
    bot.SERVERS = _real_servers
    bot.ONLINE_POLL_SECONDS = _real_poll


# ---------------------------------------------------------------- discord_forever

class LoginFailure(Exception):
    pass


_saved = (bot.DISCORD_TOKEN, bot.DISCORD_CHANNEL_ID, bot.ADMIN_CHANNEL_ID,
          bot.TRIBELOG_CHANNEL_ID)
try:
    bot.DISCORD_TOKEN, bot.DISCORD_CHANNEL_ID = "", 0
    bot.ADMIN_CHANNEL_ID = bot.TRIBELOG_CHANNEL_ID = 0
    sleep, asked = sleeper(2)
    try:
        asyncio.run(bot.discord_forever(bot.Relay(), sleep=sleep))
    except Stop:
        pass
    check("with no token Discord waits for one instead of exiting",
          bot.DISCORD_STATUS["state"] == "off" and asked == [30, 30], (bot.DISCORD_STATUS,
                                                                       asked))

    # Only an admin channel set is enough to connect - it used to need the chat one.
    bot.DISCORD_TOKEN, bot.ADMIN_CHANNEL_ID = "t", 42

    async def bad_token(relay):
        raise LoginFailure("Improper token has been passed.")

    drain()
    bot._DISCORD_SAID[0] = None
    sleep, asked = sleeper(3)
    try:
        asyncio.run(bot.discord_forever(bot.Relay(), sleep=sleep, connect=bad_token))
    except Stop:
        pass
    _dev = [e for e in drain() if e["event"] == "discord.failed"]
    check("an admin channel alone is enough to try connecting", len(asked) == 3, asked)
    check("a rejected token is said in words an admin can act on",
          _dev and "rejected the bot token" in _dev[0]["text"], _dev)
    check("at error level", _dev and _dev[0]["level"] == "error", _dev)
    check("said once and then quietly retried, not three hundred times a day",
          len(_dev) <= 2, [e["text"] for e in _dev])
    check("retried slowly, since a bad token does not fix itself",
          asked[0] >= 120, asked)
finally:
    (bot.DISCORD_TOKEN, bot.DISCORD_CHANNEL_ID, bot.ADMIN_CHANNEL_ID,
     bot.TRIBELOG_CHANNEL_ID) = _saved


# ---------------------------------------------------------------- the REST fallback

class FakeDiscord:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    async def __call__(self, method, url, headers, payload):
        self.calls.append((method, url, headers.get("Authorization"), payload))
        if self.status != 200:
            return self.status, {"message": "Unknown Channel"}
        return 200, {"id": str(len(self.calls))}


def run_fallback(http, relay_up=False, passes=3, token="tok", channel=99):
    sleep, _asked = sleeper(passes)
    said = []
    try:
        asyncio.run(discordrest.fallback_loop(
            lambda: (token, channel), lambda: relay_up, announce.pop_all,
            lambda ev, text: said.append((ev, text)), http=http, sleep=sleep,
            grace=0))
    except Stop:
        pass
    return said


drain()
announce.say("ark.apply_start", "Applying ARK build 1 in one restart.")
announce.say("ark.phase", "stopping the cluster", slot="apply")
announce.say("ark.phase", "starting the cluster", slot="apply")
_d = FakeDiscord()
run_fallback(_d)
_posts = [c for c in _d.calls if c[0] == "POST"]
check("with the relay down, admin notices still reach the admin channel",
      len(_posts) >= 1 and "/channels/99/messages" in _posts[0][1], _d.calls)
check("using the bot token directly", _posts and _posts[0][2] == "Bot tok", _posts)
check("and a story in a slot is edited in place there too",
      any(c[0] == "PATCH" for c in _d.calls) or len(_posts) == 2, _d.calls)
check("nobody gets pinged by it",
      _posts and _posts[0][3].get("allowed_mentions") == {"parse": []}, _posts)

drain()
announce.say("ark.apply_start", "Applying.")
_d = FakeDiscord()
run_fallback(_d, relay_up=True)
check("while the relay holds the admin channel the fallback stays out of its way",
      _d.calls == [] and len(drain()) == 1, _d.calls)

drain()
announce.say("ark.apply_start", "Applying.")
_d = FakeDiscord(status=404)
_said = run_fallback(_d)
check("a channel Discord does not know is said, with what to check",
      _said and "channel ID" in _said[0][1], _said)
check("and the page has the same reason",
      discordrest.STATUS.get("state") == "failed"
      and "does not exist" in discordrest.STATUS.get("why", ""), discordrest.STATUS)
check("401 is the token, in plain words",
      "rejected the bot token" in discordrest.explain(401, 1))
drain()
_d = FakeDiscord()
run_fallback(_d, token="", passes=2)
check("with no token nothing is popped, so the relay delivers it later",
      _d.calls == [], _d.calls)


# ---------------------------------------------------------------- the boot watch

class Booting:
    def __init__(self):
        self.serving = False

    def __call__(self, store):
        if self.serving:
            return {"services": [{"service": "center", "state": "running",
                                  "health": "healthy"}]}
        return {"services": [
            {"service": "center", "state": "running", "health": "starting",
             "says": "Generating the world (8m so far)",
             "last_line": "Server log file not created yet... (510s elapsed)",
             "game_line": "Log file open, 10/04/26 12:00:00",
             "expect": "Its last full start took 15m; 6-16 min per map is normal.",
             "uptime_seconds": 510},
            {"service": "island", "state": "running", "health": "healthy"}]}


_real_plan = app.build_plan
app.build_plan = lambda store: {"maps": [{"instance": "center", "name": "The Center"}]}
try:
    drain()
    _b = Booting()
    _clock = [1000.0]

    async def _boot_sleep(seconds, _n=[0]):
        _n[0] += 1
        _clock[0] += seconds
        if _n[0] == 3:
            _b.serving = True
        if _n[0] > 3:
            raise Stop()

    try:
        asyncio.run(app.boot_watch({}, status=_b, sleep=_boot_sleep, say_every=0,
                                   now=lambda: _clock[0]))
    except Stop:
        pass
    _bev = [e for e in drain() if e["event"] == "cluster.boot"]
    check("a starting map is said by name, with what it last printed",
          _bev and "The Center" in _bev[0]["text"]
          and "510s elapsed" in _bev[0]["text"], _bev)
    check("and with what is normal, so slow does not read as stuck",
          _bev and "normal" in _bev[0]["text"], _bev)
    check("the serving map is left out", _bev and "island" not in _bev[0]["text"], _bev)
    check("unchanged progress is not said again", len(_bev) == 2,
          [e["text"][:40] for e in _bev])
    check("and the story ends when every map is serving",
          _bev and _bev[-1].get("slot_end") and "Every map is serving" in _bev[-1]["text"],
          _bev[-1:])
    check("all in one Discord message, edited in place",
          all(e.get("slot") == "boot" for e in _bev), _bev)
finally:
    app.build_plan = _real_plan


# ---------------------------------------------------------------- the page

_job = {"state": "done", "what": "apply", "ok": False, "refused": True,
        "finished": time.time(),
        "message": "the player count could not be read, so it is not known whether "
                   "anyone is on."}
_html = ui.render_pending([{"label": "Max players", "map": "", "key": "max_players",
                            "from": "70", "to": "250"}], job=_job)
check("a refused apply says why, where the button was pressed",
      "player count could not be read" in _html, _html[:400])
check("in amber, because nothing was touched", "class=warn" in _html, _html[:400])
check("and the buttons are still there to try again", "name=apply" in _html)
_failed = dict(_job, refused=False, message="the cluster did not start")
check("a failure part way is red, not amber",
      "class=problem><b>Apply failed" in ui.render_pending(
          [{"label": "x", "map": "", "key": "x", "from": "1", "to": "2"}], job=_failed))

_row = {"state": "running", "health": "starting", "says": "Generating the world",
        "last_line": "Server log file not created yet... (510s elapsed)",
        "game_line": "Log file open", "expect": "Its last full start took 15m."}
_bd = ui.boot_detail(_row)
check("the cluster table shows the last thing the server said",
      "510s elapsed" in _bd and "Log file open" in _bd and "15m" in _bd, _bd)
check("the Right now panel lists maps still starting",
      "Maps starting (1)" in ui.render_boot([dict(_row, label="The Center")]))
check("a relay that is waiting for maps says so instead of rendering nothing",
      "waiting for maps" in ui.render_relay({"state": "waiting", "why": "no map"}))
check("a relay that keeps failing is red, with the reason",
      'badge bad' in ui.render_relay({"state": "failing", "why": "boom"})
      and "boom" in ui.render_relay({"state": "failing", "why": "boom"}))
check("Discord trouble is shown under the relay",
      "rejected" in ui.render_relay({"total": 2, "reachable": 2, "discord": {
          "state": "failed", "why": "Discord rejected the bot token."}}))


# ---------------------------------------------------------------- progress

_docker = ("[2026-10-04 12:00:01] Waiting for server to complete initialization\n"
           "Server log file not created yet... (510s elapsed)\n\n----------\n")
check("the last line skips separators and blanks",
      progress.last_line(_docker) == "Server log file not created yet... (510s elapsed)",
      progress.last_line(_docker))
check("and drops the game log's timestamp prefix",
      progress.last_line("[2026.10.04-12.00.00:123][  0]Log file open") == "Log file open",
      progress.last_line("[2026.10.04-12.00.00:123][  0]Log file open"))
check("Full Startup is read from the game log",
      progress.full_startup_seconds("x\nFull Startup: 932.35 seconds\ny") == 932.35)


class _St:
    def __init__(self, m, size=10):
        self.st_mtime, self.st_size = m, size


_files = {"/l/ShooterGame.log": ("booting, no full startup yet", 300),
          "/l/ShooterGame_backup-2026.10.02.log": ("Full Startup: 932.35 seconds", 200),
          "/l/ShooterGame_backup-2026.09.30.log": ("Full Startup: 400.0 seconds", 100),
          "/l/other.txt": ("Full Startup: 1 seconds", 400)}
_prev = progress.previous_startup(
    "/l", listdir=lambda d: [p.rsplit("/", 1)[1] for p in _files],
    stat=lambda p: _St(_files[p][1]), read=lambda p: _files[p][0])
check("the previous start time comes from the newest log that has one", _prev == 932.35,
      _prev)
check("a boot inside its usual time says what usual is",
      "15m" in progress.expectation(600, 932.35))
check("one well past it says so",
      "longer than its last start" in progress.expectation(3000, 932.35))
check("with no history the typical range is given",
      "6-16 min" in progress.expectation(600, None))
check("nothing is said in the first minute", progress.expectation(30, 932.35) == "")


print("\nFAILURES: %s" % fails if fails else "\nall relay tests passed")
sys.exit(1 if fails else 0)
