"""
The admin channel, reached without the chat relay.

Obelisk's own announcements - an update starting, a map not answering, an apply that
was refused - used to reach Discord only through the relay's gateway connection. The
relay only starts once there are maps to relay between, and it is exactly when the
maps are down or restarting that an admin most needs to be told what is happening. On
4 October the relay had not started, the bot token and admin channel were both set, and
a twenty-minute update posted nothing at all.

So this posts with the bot token over Discord's plain HTTP API, which needs no gateway,
no intents and no relay. It is the fallback: while the relay's own Discord connection
is up, the relay drains the queue and this stays out of the way.
"""
import asyncio
import logging
import time

log = logging.getLogger("obelisk")

API = "https://discord.com/api/v10"


class DiscordRestError(Exception):
    """A refusal from Discord, already turned into something an admin can act on."""

    def __init__(self, status, text, retry_after=None):
        super().__init__(text)
        self.status = status
        self.retry_after = retry_after


def explain(status, channel_id, body=""):
    """What an HTTP status from Discord means for the person who set this up."""
    if status == 401:
        return ("Discord rejected the bot token. Paste a fresh one from the Developer "
                "Portal (Bot -> Reset Token) into Settings -> Discord.")
    if status == 403:
        return ("the bot cannot post in admin channel %s. Give it View Channels and "
                "Send Messages there." % channel_id)
    if status == 404:
        return ("admin channel %s does not exist, or the bot is not in that server. "
                "Check the channel ID in Settings -> Discord." % channel_id)
    if status == 429:
        return "Discord is rate-limiting the bot"
    return "Discord answered HTTP %s%s" % (status, (": " + body[:200]) if body else "")


class RestMessage:
    """Enough of a discord.py Message for post_or_edit: something with .edit()."""

    def __init__(self, channel, message_id):
        self.channel = channel
        self.id = message_id

    async def edit(self, content):
        await self.channel.request(
            "PATCH", "/channels/%s/messages/%s" % (self.channel.channel_id, self.id),
            {"content": content[:1990]})


class RestChannel:
    """One channel, posted to with the bot token. `send` has the relay's shape."""

    def __init__(self, token, channel_id, http=None):
        self.token = token
        self.channel_id = channel_id
        self._http = http                 # (method, url, headers, json) -> (status, dict)

    async def request(self, method, path, payload):
        http = self._http or _aiohttp_request
        status, data = await http(
            method, API + path,
            {"Authorization": "Bot %s" % self.token,
             "User-Agent": "Obelisk (https://github.com/tbgkiller/obelisk, 1)"},
            payload)
        if status == 429:
            retry = float((data or {}).get("retry_after") or 1.0)
            raise DiscordRestError(429, explain(429, self.channel_id), retry)
        if status >= 400:
            raise DiscordRestError(status, explain(
                status, self.channel_id, str((data or {}).get("message") or "")))
        return data or {}

    async def send(self, text):
        data = await self.request(
            "POST", "/channels/%s/messages" % self.channel_id,
            {"content": text[:1990], "allowed_mentions": {"parse": []}})
        return RestMessage(self, data.get("id")) if data.get("id") else None


async def _aiohttp_request(method, url, headers, payload, timeout=15):
    import aiohttp
    async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.request(method, url, headers=headers, json=payload) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:                        # noqa: BLE001 - body is optional
                data = {}
            return r.status, data if isinstance(data, dict) else {}


# For the page: what the fallback last managed, so "nothing reached Discord" has a
# reason next to it rather than a guess.
STATUS = {"state": "idle", "why": "", "at": 0.0}


async def fallback_loop(settings, relay_has_admin, pop_all, say, http=None,
                        sleep=asyncio.sleep, interval=3, grace=20):
    """Drain the announcement queue to the admin channel while the relay cannot.

    `settings()` returns (token, admin channel id), read fresh every pass so a token
    pasted into the page is used on the next pass. `relay_has_admin()` is True while
    the relay's own connection holds the admin channel; it gets `grace` seconds to
    connect after it last lost it, so a relay that is merely reconnecting is not
    raced for the queue.

    Nothing popped is dropped on a failure: it is held and retried first, up to a
    limit, so a rate-limit or a blip delays the notices rather than losing them.
    """
    from . import bot
    from . import announce as ann
    held, slots = [], {}
    relay_seen = 0.0
    said = None
    while True:
        await sleep(interval)
        try:
            if relay_has_admin():
                relay_seen = time.time()
                STATUS.update(state="idle", why="the chat relay is posting")
                continue
            if time.time() - relay_seen < grace:
                continue
            token, channel_id = settings()
            if not (token and channel_id):
                STATUS.update(state="off", why="no bot token or admin channel is set")
                continue
            channel = RestChannel(token, channel_id, http=http)
            if not held:
                held = list(bot._coalesce_slots(pop_all()))
            while held:
                await bot.post_or_edit(slots, channel.send, ann, held[0])
                held.pop(0)
            if STATUS.get("state") != "ok":
                STATUS.update(state="ok", why="posting directly (the chat relay is "
                              "not connected)", at=time.time())
            said = None
        except DiscordRestError as e:
            STATUS.update(state="failed", why=str(e), at=time.time())
            if e.status == 429:
                await sleep(min(60.0, e.retry_after or 1.0))
                continue
            if said != e.status:
                said = e.status
                log.error("discord admin channel: %s", e)
                # Into the feed, where it can be seen. It also queues for Discord, and
                # will arrive there once whatever this is has been fixed.
                say("discord.admin_unreachable",
                    "Admin notices cannot reach Discord: %s" % e)
            del held[50:]
            await sleep(60)
        except Exception as e:                       # noqa: BLE001 - never fatal
            STATUS.update(state="failed", why=str(e), at=time.time())
            log.warning("discord admin fallback hiccup: %s", e)
            del held[50:]
            await sleep(30)
