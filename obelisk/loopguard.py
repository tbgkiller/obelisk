"""
A background loop that keeps failing the same way says so once, backs off, and shows red.

From 11 October: the empty-cluster watcher raised the same NameError every three
minutes for over a day. Its `except` logged one line and went round again - the loop was
"never fatal", which is the point of those excepts, but it was also never noticed. The
only trace was a log nobody reads, while the feed kept saying an apply was starting.

Every long-lived loop in the manager catches everything so one bad pass cannot take the
web UI down with it. That stays. What this adds is memory: the same exception N times in
a row is not a bad pass, it is a broken loop, and a broken loop is:

  - one error event (loop.failing), not one per pass;
  - an exponential backoff on top of the loop's own interval, so it stops hammering;
  - a red banner on the Cluster page until it works again (loop.recovered).

Use: `guard.tick()` at the top of each pass, and `await guard.failed(e)` in the except.
A pass that reaches the next tick without calling failed() is a success - including one
that ended early with `continue`, which is how most of these loops skip a pass.
"""

import asyncio
import logging
import time

log = logging.getLogger("obelisk")

THRESHOLD = 3          # the same exception this many passes running is a broken loop
BASE = 60              # first extra wait once it is, in seconds; doubles after that
CAP = 3600             # never more than an hour on top of the loop's own interval

# name -> what the Cluster page shows. Only loops currently failing are in it.
FAILING = {}


def failing():
    """The loops that are broken right now, oldest first, for the page."""
    return sorted(FAILING.values(), key=lambda r: r.get("since") or 0)


class Guard:
    def __init__(self, name, label, threshold=THRESHOLD, base=BASE, cap=CAP,
                 say=None, sleep=None, now=None):
        self.name, self.label = name, label
        self.threshold, self.base, self.cap = threshold, base, cap
        self._say = say
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.time
        self.count, self.signature, self.alerted = 0, None, False
        self._ticked, self._failed = False, False

    def say(self, event, text, level="info", **fields):
        if self._say is not None:
            return self._say(event, text, level=level, **fields)
        from . import announce
        return announce.say(event, text, level=level, **fields)

    def tick(self):
        """A new pass begins. If the last one did not fail, it worked."""
        if self._ticked and not self._failed:
            self.ok()
        self._ticked, self._failed = True, False

    def ok(self):
        if self.alerted:
            self.say("loop.recovered", "%s is working again." % self.label,
                     loop=self.name)
        self.count, self.signature, self.alerted = 0, None, False
        FAILING.pop(self.name, None)

    def record(self, exc):
        """Count a failure. Returns the extra seconds to wait before the next pass."""
        self._failed = True
        sig = (exc.__class__.__name__, str(exc)[:300])
        self.count = self.count + 1 if sig == self.signature else 1
        self.signature = sig
        if self.count < self.threshold:
            return 0
        delay = min(self.cap, self.base * 2 ** (self.count - self.threshold))
        why = "%s: %s" % sig
        FAILING[self.name] = {"name": self.name, "label": self.label, "error": why,
                              "count": self.count,
                              "since": (FAILING.get(self.name) or {}).get("since")
                              or self._now(),
                              "retry_at": self._now() + delay}
        if not self.alerted:
            self.alerted = True
            log.error("%s has failed %d times in a row: %s", self.label, self.count, why)
            self.say("loop.failing",
                     "%s has failed %d times in a row with the same error: %s. It is "
                     "backing off and will keep retrying; this is a bug worth "
                     "reporting." % (self.label, self.count, why),
                     level="error", loop=self.name)
        return delay

    async def failed(self, exc):
        delay = self.record(exc)
        if delay:
            await self._sleep(delay)
