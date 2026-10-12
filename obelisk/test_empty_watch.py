"""
The empty-cluster apply: it says "applying now" only when it is, and a failure is said
and backed off rather than repeated every three minutes.

From 11 October: with "Apply waiting changes when nobody is on" switched on, the watcher
announced "the cluster has been empty for 3 minute(s) ... so it is being applied now"
every three minutes for over a day - 200 times, in the feed and in Discord - and then
crashed on the next line every time with NameError: name 'lock' is not defined. The
crash went to the log only. Nothing was ever applied.

The real loop is driven here. The cluster, the clock and the apply are handed in; no
game server is involved.
"""

import asyncio
import sys

from . import announce, app

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
    pass


class Store:
    def __init__(self, **kw):
        self.values = dict(apply_when_empty=True)
        self.values.update(kw)

    def get(self, key, map_name=None):
        return self.values.get(key)


def run(minutes, apply_result, worth=(True, "build 2 is staged and verified, newer than 1"),
        ready=(True, "", 0)):
    """Drive empty_watch for `minutes` one-minute ticks. Returns (apply calls, events)."""
    clock = [1000.0]
    ticks = [0]
    calls = []

    async def sleep(seconds):
        ticks[0] += 1
        if ticks[0] > minutes:
            raise Stop()
        clock[0] += seconds

    def fake_apply(store, force=False, opening=None, opening_event="change.window_open"):
        calls.append(opening)
        res = apply_result(len(calls)) if callable(apply_result) else apply_result
        if isinstance(res, Exception):
            raise res
        if res[0] and opening:
            announce.say(opening_event, opening)
        return res

    real = (app._scheduled_apply, app.clusterctl.cluster_ready,
            app.arkupdate.installed_build)
    from . import updates as upd
    real_worth = upd.worth_applying
    app._scheduled_apply = fake_apply
    app.clusterctl.cluster_ready = lambda store: ready
    app.arkupdate.installed_build = lambda path: ("1", "")
    upd.worth_applying = (lambda store, installed=None, ark_root=None:
                          worth() if callable(worth) else worth)
    drain()
    try:
        asyncio.run(app.empty_watch(Store(), interval=60, needed=3, sleep=sleep,
                                    now=lambda: clock[0]))
    except Stop:
        pass
    finally:
        (app._scheduled_apply, app.clusterctl.cluster_ready,
         app.arkupdate.installed_build) = real
        upd.worth_applying = real_worth
    return calls, drain()


# ---- it actually applies. This is the line that raised NameError.
calls, events = run(4, (True, "applied", {}))
names = [e["event"] for e in events]
check("after three empty minutes the apply really runs", len(calls) == 1, calls)
check("it says so, once", names.count("change.window_open") == 1, names)
check("with the reason it is applying",
      "newer than 1" in [e for e in events if e["event"] == "change.window_open"][0]["text"],
      events)
check("and holds the module-level lock while it does",
      "async with APPLY_LOCK:" in __import__("inspect").getsource(app.empty_watch))
check("the name that did not exist is gone",
      "async with lock:" not in __import__("inspect").getsource(app.empty_watch))

# ---- a refusal: said with its reason, and backed off, not repeated every 3 minutes
calls, events = run(60, (False, "the player count could not be read", {}))
said = [e for e in events if e["event"] == "change.empty_apply_failed"]
opened = [e for e in events if e["event"] == "change.window_open"]
check("a refused apply is said, with its reason",
      said and "player count could not be read" in said[0]["text"], said)
check("as a warning, since nothing was touched", said and said[0]["level"] == "warning",
      said)
check("'being applied now' is never said about an apply that did not happen",
      opened == [], opened)
# Minute 3, then 15 held + 3 counted = minute 20 (or so), then 30 + 3 = minute 53.
# Every three minutes would have been 20.
check("in an hour it tries three times, not twenty", len(calls) == 3, len(calls))
check("each failure says it is backing off further",
      len(said) == 3 and "15 minute" in said[0]["text"]
      and "30 minute" in said[1]["text"] and "2 times in a row" in said[1]["text"]
      and "60 minute" in said[2]["text"], [s["text"] for s in said])

# ---- an exception (the 11 October case) is an error event, and backed off too
calls, events = run(17, NameError("name 'lock' is not defined"))
said = [e for e in events if e["event"] == "change.empty_apply_failed"]
check("an apply that raises is said, at error level, with the exception",
      said and said[0]["level"] == "error" and "NameError" in said[0]["text"], said)
check("and is not retried every three minutes", len(calls) == 1, len(calls))
check("and nothing announced it as starting",
      not any(e["event"] == "change.window_open" for e in events), events)

# ---- somebody arriving on the final count is not a failure, and is not backed off
calls, events = run(10, (False, "somebody is on after all", {"deferred": True}))
check("a deferral is not reported as a failure",
      not any(e["event"] == "change.empty_apply_failed" for e in events), events)
check("and it is free to try again after three more empty minutes", len(calls) == 3,
      len(calls))

# ---- a new thing to apply clears the backoff
_whys = iter(["build 2 is staged"] * 4 + ["build 3 is staged"] * 100)
calls, events = run(10, (False, "refused", {}), worth=lambda: (True, next(_whys)))
check("when what is waiting changes, the backoff is not inherited", len(calls) == 2,
      len(calls))

# ---- a cluster that cannot be counted never gets as far as the apply
calls, events = run(10, (True, "applied", {}), ready=(False, "1 map did not answer", 0))
check("an unreadable count is not empty: nothing is applied", calls == [], calls)
check("and nothing is said", events == [], events)

# ---- the window path also announces only through the apply
src = __import__("inspect").getsource(app.ark_update_watch)
check("the update window announces through the apply, after its final count",
      'opening_event="ark.window_open"' in src
      and 'announce.say("ark.window_open"' not in src)

print("\nFAILURES: %s" % fails if fails else "\nall empty-watch tests passed")
sys.exit(1 if fails else 0)
