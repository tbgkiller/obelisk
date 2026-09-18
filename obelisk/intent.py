"""What Obelisk MEANT each map to be doing, remembered on disk.

The ARK containers carry no restart policy by default any more, so nothing brings a
crashed map back by itself. The crash watch does - and the only thing standing between
"bring back a map that fell over" and "keep restarting a map somebody deliberately
stopped" is a record of which of those two happened. That record is this.

ON DISK, not in memory, and that is not a preference. The manager image is pinned
`:latest` and Watchtower restarts it on any push, so in-memory intent is erased at a
moment nobody chose - and an empty memory that defaulted to "up" would relaunch every
map the operator had just stopped. Same `store.data[...]` + `store.save()` pattern
`updates.remember` uses, in a module of its own because this is about what a map should
be doing and that bag is about which build is staged.

THE OWNER'S RULE, verbatim: "Get this wrong in the safe direction: if intent is
ambiguous, do NOT relaunch."

So `wants_up` is written as an exact string compare against "up" and nothing else. A
record that is missing, unreadable, a dict of the wrong shape, a stray `True`, the
string "Up", or anything a future version writes that this one has never heard of, is
not "up". Every one of those reads as "do not act", which costs a map staying down and
an announcement; the other direction costs a relaunch storm against a map that was
stopped on purpose.
"""

import logging
import time

log = logging.getLogger("obelisk.intent")

STATE = "map_intent"          # store.data[STATE] - manager state, not a setting

UP = "up"
DOWN = "down"

# How many times one map may be relaunched inside the rolling window before the watch
# stands down on it for good.
#
# Three, in six hours. A map that has crashed three times in six hours is not having bad
# luck - it has a reason, and the reasons are a mod that will not load, a world the
# server cannot read, or a disk with nothing left on it. A fourth relaunch writes more
# over the evidence and puts the same failure in the channel again. Three is also what
# this project already answers "how many times before you stop" with, at PRIME_TRIES and
# CLOSE_ATTEMPTS, so it is one number to learn rather than three.
BUDGET = 3
WINDOW = 6 * 3600


def _all(store):
    got = store.data.get(STATE)
    return dict(got) if isinstance(got, dict) else {}


def _save(store, table):
    store.data[STATE] = table
    try:
        store.save()
    except OSError as e:
        # Never fatal. A stop that could not write its intent still has to be a stop -
        # the cost is that the watch may later read the map as one it should bring back,
        # which is why the failure is logged loudly rather than swallowed.
        log.warning("could not record what this cluster's maps are meant to be doing: "
                    "%s", e)


def read(store, key):
    """This map's record, or {} when there has never been one or it is the wrong shape."""
    got = _all(store).get(str(key))
    return dict(got) if isinstance(got, dict) else {}


def wants_up(store, key):
    """Is this map's intent EXACTLY "up"? The owner's rule, as one line.

    `is` is not used and `==` is: the stored value comes back out of JSON, so identity
    would be a lie about a string that is genuinely equal. What matters is that nothing
    is coerced - no truthiness, no casefold, no "anything that is not down". A record
    nobody wrote, a record from a version that spelled this differently, and a record
    holding True all answer False here.
    """
    return read(store, key).get("intent") == UP


def remember(store, key, intent, by, now=None):
    """Record what this map is meant to be doing. Returns the record written.

    A FRESH record every time, which clears both the relaunch history and any
    stand-down. That is the "until a human acts" in "do not try again until a human
    acts": somebody deciding this map should be up or down is a clean slate for it.

    Which makes who calls this load-bearing. Every caller is a human action or the
    direct consequence of one - a Launch, a Stop, an apply. The watch is NOT one of
    them: it relaunches through start_one(record=False) precisely so that it cannot
    hand itself a new budget and rebuild the restart loop it exists to replace.
    """
    now = now or time.time
    table = _all(store)
    table[str(key)] = {"intent": UP if intent == UP else DOWN, "at": int(now()),
                       "by": str(by), "relaunches": []}
    _save(store, table)
    return table[str(key)]


def remember_many(store, keys, intent, by, now=None):
    """The same, for every map in one go - a launch or a whole-cluster stop."""
    for key in keys:
        remember(store, key, intent, by, now=now)


def relaunches(store, key, now=None, window=WINDOW):
    """How many relaunches this map has had inside the rolling window."""
    now = now or time.time
    cut = now() - window
    return len([t for t in (read(store, key).get("relaunches") or [])
                if isinstance(t, (int, float)) and t >= cut])


def may_relaunch(store, key, now=None, budget=BUDGET, window=WINDOW):
    """(may it, why not). The whole gate, in one place, and it fails closed.

    Everything an unattended relaunch is allowed to rest on is here, and nothing that
    decides it lives anywhere else. The caller adds the two facts this module cannot
    see: the container really is down, and no apply is in flight.
    """
    got = read(store, key)
    if got.get("stood_down"):
        return False, "the watch has stood down on it: %s" % (got.get("why") or "")
    if got.get("intent") != UP:
        # The owner's rule. Missing, unreadable, "down", or a spelling nobody here
        # recognises - all of them land on this line and none of them relaunches.
        return False, ("Obelisk does not have it recorded as a map that should be up "
                       "(%r)" % (got.get("intent"),))
    spent = relaunches(store, key, now=now, window=window)
    if spent >= budget:
        return False, ("it has already been brought back %d times in %d hours"
                       % (spent, int(window / 3600)))
    return True, ""


def record_relaunch(store, key, now=None, window=WINDOW):
    """Note that this map was just brought back. Returns how many are in the window."""
    now = now or time.time
    at = int(now())
    cut = at - window
    table = _all(store)
    got = dict(table.get(str(key)) or {}) if isinstance(table.get(str(key)), dict) else {}
    kept = [t for t in (got.get("relaunches") or [])
            if isinstance(t, (int, float)) and t >= cut]
    got["relaunches"] = kept + [at]
    table[str(key)] = got
    _save(store, table)
    return len(got["relaunches"])


def stand_down(store, key, why, now=None):
    """Stop watching this map until a human writes an intent for it again.

    Permanent on purpose. An unbounded watch is the restart loop rebuilt inside Obelisk,
    where it is harder to see than Docker's was - so when the budget is gone the answer
    is to stop, say so loudly, and wait for somebody to decide.
    """
    now = now or time.time
    table = _all(store)
    got = dict(table.get(str(key)) or {}) if isinstance(table.get(str(key)), dict) else {}
    got["stood_down"], got["why"] = int(now()), str(why)
    table[str(key)] = got
    _save(store, table)
    return got


def stood_down(store):
    """Every map the watch has given up on, and why. {key: why}."""
    return {k: (v or {}).get("why") or "" for k, v in _all(store).items()
            if isinstance(v, dict) and v.get("stood_down")}


def forget(store, key):
    """Drop this map's record entirely - for a map that is no longer in the cluster."""
    table = _all(store)
    if str(key) in table:
        table.pop(str(key))
        _save(store, table)
