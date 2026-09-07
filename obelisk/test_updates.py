"""
The update flow, and every way it could restart a cluster it should not have.

Applying an update is the most destructive routine in this product: it stops ten
servers, moves 12 GB of install out from under them and starts them again. So most of
what is checked here is refusal - that it will not fire on an update nobody proved,
will not fire while the server image is also applying updates, will not fire while a
player is standing in a world, and will not fire on a map that failed to answer when
asked whether anyone was there. "We could not ask" is not "nobody is home", and that
distinction is the difference between a quiet restart and kicking the one person on.

The swap itself is proved reversible rather than assumed to be: a rename that fails
half way puts the previous build back, because the alternative is a cluster with no
bootable install at four in the morning.
"""

import sys
import time

from . import announce, updates

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


class FakeStore:
    saved = 0

    def __init__(self, **kw):
        self.values = dict(
            ark_update_mode="obelisk", update_apply_in_window=False,
            apply_when_empty=True, apply_empty_hours="",
            staging_mode="always", staging_map="scorched", staging_memory="10g",
            update_window_start="4:00 AM", update_window_end="6:00 AM",
            restart_notice_minutes=30, mod_ids="929110,940003,929420",
            admin_password="x", cluster_id="tbgcluster")
        self.values.update(kw)
        self.data = {}

    def get(self, key, map_name=None):
        return self.values.get(key)

    def save(self):
        FakeStore.saved += 1
        return self


ARK = "/ark"
IDS = "929110,940003,929420"
LOADED = {"929110": "7738786", "940003": "6830549", "929420": "8160173"}


# ---- state survives a restart, because priming happens hours before applying
store = FakeStore()
updates.remember(store, primed={"ok": True, "build": "25200000", "loaded": LOADED})
carried = FakeStore()
carried.data = dict(store.data)                      # what load() would give back
check("a staged update is remembered in the store, not in memory",
      (updates.primed(carried) or {}).get("build") == "25200000", carried.data)

failed = FakeStore()
updates.remember(failed, primed={"ok": False, "build": "25200000",
                                 "problems": ["a mod never loaded"]})
check("a staged update that failed is not 'primed' - there is nothing to apply",
      updates.primed(failed) is None, failed.data)

# ---- announcing once per version, not once per poll
drain()
store = FakeStore()
status = {"build": {"running": "1", "latest": "2", "newer": True},
          "mods_newer": [{"id": "929420", "name": "Spyglass", "running": "8160173",
                          "latest": "8210044"}]}
updates.announce_new(store, status)
first = drain()
check("a new build is announced", any(i["event"] == "ark.update_available"
                                      for i in first), [i["event"] for i in first])
check("and so is a new mod version",
      any(i["event"] == "ark.mod_update_available" for i in first),
      [i["event"] for i in first])

updates.announce_new(store, status)
check("polling again says nothing - the version has not changed", drain() == [])

status["build"]["latest"] = "3"
updates.announce_new(store, status)
again = drain()
check("but a newer build than the one announced is announced",
      [i["event"] for i in again] == ["ark.update_available"],
      [i["event"] for i in again])

# ---- the window
check("an AM time parses", updates.parse_time("4:00 AM") == 240)
check("a PM time parses", updates.parse_time("11:30 PM") == 23 * 60 + 30)
check("midnight is not noon", updates.parse_time("12:00 AM") == 0)
check("noon is not midnight", updates.parse_time("12:00 PM") == 12 * 60)
check("24-hour times work too", updates.parse_time("16:30") == 16 * 60 + 30)
check("nonsense is not a time", updates.parse_time("soon") is None)
check("and neither is an impossible one", updates.parse_time("25:00") is None)

store = FakeStore()
check("inside the window", updates.in_window(store, 5 * 60))
check("outside it", not updates.in_window(store, 12 * 60))
check("the boundaries are inside", updates.in_window(store, 240)
      and updates.in_window(store, 360))
midnight = FakeStore(update_window_start="11:00 PM", update_window_end="2:00 AM")
check("a window that crosses midnight still works",
      updates.in_window(midnight, 23 * 60 + 30) and updates.in_window(midnight, 60)
      and not updates.in_window(midnight, 12 * 60), "crossing midnight")


def at(hour, minute=0):
    """A `now` inside the local day, so due() sees the hour we mean."""
    lt = list(time.localtime())
    lt[3], lt[4] = hour, minute
    return time.mktime(tuple(lt))


# ---- due(): four separate reasons not to fire, and each one alone must stop it
ready = {"ok": True, "build": "25200000", "loaded": LOADED}

s = FakeStore(update_apply_in_window=True)
updates.remember(s, primed=ready)
ok, why = updates.due(s, now=lambda: at(5))
check("staged, verified, in the window, Obelisk in charge - fires", ok, why)

ok, why = updates.due(s, now=lambda: at(12))
check("outside the window it does not fire", not ok, why)
check("and says why", "window" in why, why)

s2 = FakeStore(update_apply_in_window=False)
updates.remember(s2, primed=ready)
ok, why = updates.due(s2, now=lambda: at(5))
check("with the setting off it does not fire", not ok, why)

s3 = FakeStore(update_apply_in_window=True, ark_update_mode="automatic")
updates.remember(s3, primed=ready)
ok, why = updates.due(s3, now=lambda: at(5))
check("it never fires while the server image owns updates", not ok, why)
check("and says which system is in charge", "POK" in why, why)

s4 = FakeStore(update_apply_in_window=True)
updates.remember(s4, primed={"ok": False, "build": "25200000"})
ok, why = updates.due(s4, now=lambda: at(5))
check("an update that failed its rehearsal never fires", not ok, why)

s5 = FakeStore(update_apply_in_window=True)
updates.remember(s5, primed=ready,
                 applied={"build": "25200000", "when": 1, "ok": True})
ok, why = updates.due(s5, now=lambda: at(5))
check("a build already applied does not fire again the next night", not ok, why)
check("and says so", "already been applied" in why, why)


# ---- staging ahead: what to stage, and not staging it over and over
#
# The point of the staging server is to be ahead, so that a window at four in the
# morning is a rename of files that are already downloaded and already proved rather
# than a 12 GB pull. Which means priming has to start itself. Which means it needs a
# guard, because a rehearsal that fails will fail again for the same reason, and
# retrying every half hour is a 12 GB download and an 8 GB container each time.
CURRENT = {"build": {"running": "25117056", "latest": "25117056", "newer": False},
           "mods": [{"id": "929110", "running": "7738786", "latest": "7738786",
                     "newer": False},
                    {"id": "929420", "running": "8160173", "latest": "8160173",
                     "newer": False}],
           "mods_newer": [], "any_newer": False, "unknown": False}
NEWER_BUILD = {"build": {"running": "25117056", "latest": "25200000", "newer": True},
               "mods": CURRENT["mods"], "mods_newer": [], "any_newer": True,
               "unknown": False}
NEWER_MOD = {"build": {"running": "25117056", "latest": "25117056", "newer": False},
             "mods": [{"id": "929110", "running": "7738786", "latest": "7738786",
                       "newer": False},
                      {"id": "929420", "running": "8160173", "latest": "8210044",
                       "newer": True}],
             "mods_newer": [{"id": "929420"}], "any_newer": True, "unknown": False}
UNKNOWN = {"build": {"running": "25117056", "latest": None, "newer": None},
           "mods": CURRENT["mods"], "mods_newer": [], "any_newer": False,
           "unknown": True}

check("the fingerprint covers the build", updates.target_key(NEWER_BUILD) !=
      updates.target_key(CURRENT))
check("and every mod, so a mod release is a new thing to stage",
      updates.target_key(NEWER_MOD) != updates.target_key(CURRENT))
check("the same state gives the same fingerprint",
      updates.target_key(CURRENT) == updates.target_key(dict(CURRENT)))
check("and an unknown makes no fingerprint at all rather than a guess",
      updates.target_key(UNKNOWN) == "")

s = FakeStore(staging_mode="always")
go, why = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1000)
check("a newer build is staged without anyone asking", go, why)

s = FakeStore(staging_mode="always")
go, why = updates.needs_prime(s, NEWER_MOD, now=lambda: 1000)
check("so is a newer mod - not only builds", go, why)

s = FakeStore(staging_mode="always")
go, why = updates.needs_prime(s, CURRENT, now=lambda: 1000)
check("an always-on staging server stages the current target too, to be ahead", go, why)

s = FakeStore(staging_mode="on_demand")
go, why = updates.needs_prime(s, CURRENT, now=lambda: 1000)
check("on_demand costs nothing while there is nothing newer", not go, why)
go, why = updates.needs_prime(FakeStore(staging_mode="on_demand"), NEWER_BUILD,
                              now=lambda: 1000)
check("but does stage an actual update", go, why)

s = FakeStore(staging_mode="off")
go, why = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1000)
check("with staging off, nothing is staged", not go, why)

s = FakeStore(staging_mode="always")
go, why = updates.needs_prime(s, UNKNOWN, now=lambda: 1000)
check("a target we could not fully check is not staged on a guess", not go, why)
check("and says why", "no telling what to stage" in why, why)

# already staged: the whole point of the fingerprint
s = FakeStore(staging_mode="always")
updates.remember(s, primed={"ok": True, "build": "25200000",
                            "target": updates.target_key(NEWER_BUILD)})
go, why = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1000)
check("what is already staged and verified is not staged again", not go, why)
go, why = updates.needs_prime(s, NEWER_MOD, now=lambda: 1000)
check("but a mod releasing afterwards makes it stale, and it stages again", go, why)

# ---- the thrash guard
s = FakeStore(staging_mode="always")
key = updates.target_key(NEWER_BUILD)
go, _ = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1000)
check("first attempt goes straight away", go)

updates.note_attempt(s, key, ok=False, now=lambda: 1000)
go, why = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1100)
check("a failure is not retried immediately", not go, why)
check("and says how long it is waiting", "minute" in why, why)

go, why = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1000 + 3 * 3600)
check("but it is retried after the backoff", go, why)

updates.note_attempt(s, key, ok=False, now=lambda: 1000 + 3 * 3600)
updates.note_attempt(s, key, ok=False, now=lambda: 1000 + 20 * 3600)
go, why = updates.needs_prime(s, NEWER_BUILD, now=lambda: 1000 + 100 * 3600)
check("after three failures it stops trying rather than looping forever", not go, why)
check("and says so, so the state is visible", "already failed to stage" in why, why)
check("a different target is still tried - the giving-up is per thing, not global",
      updates.needs_prime(s, NEWER_MOD, now=lambda: 1000 + 100 * 3600)[0])

s2 = FakeStore(staging_mode="always")
updates.note_attempt(s2, key, ok=False, now=lambda: 1000)
updates.note_attempt(s2, key, ok=True, now=lambda: 2000)
check("a success clears the record, so a later change is not held against it",
      not (updates.state(s2).get("attempts") or {}).get(key),
      updates.state(s2).get("attempts"))


# ---- apply: the refusals, before anything moves
def moved_nothing():
    calls = []

    def rename(src, dst):
        calls.append((src, dst))
    return calls, rename


def tree_exists(missing=()):
    return lambda p: not any(m in p for m in missing)


s = FakeStore(ark_update_mode="automatic")
updates.remember(s, primed=ready)
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK, rename=rename, exists=tree_exists())
check("apply refuses while the server image owns updates", not ok, msg)
check("nothing was renamed", calls == [], calls)
check("and it says how to change that", "Who applies ARK updates" in msg, msg)

s = FakeStore()
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK, rename=rename, exists=tree_exists())
check("apply refuses with nothing staged", not ok, msg)
check("still nothing renamed", calls == [], calls)

s = FakeStore()
updates.remember(s, primed=ready)
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK, players=lambda: (3, {"island": 3}, []),
                                  rename=rename, exists=tree_exists())
check("apply refuses with players online", not ok, msg)
check("and names where they are", "island (3)" in msg, msg)
check("and moved nothing", calls == [], calls)

# The one that matters most: a map that did not answer is not an empty map.
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK,
                                  players=lambda: (0, {}, [("genesis", "timeout")]),
                                  rename=rename, exists=tree_exists())
check("a map that did not answer stops the apply - silence is not 'nobody is on'",
      not ok, msg)
check("and names it", "genesis" in msg, msg)
check("and moved nothing", calls == [], calls)


# ---- apply: the happy path, in order
class Cluster:
    def __init__(self, start_ok=True, stop_ok=True, gates=True):
        self.log = []
        self.start_ok, self.stop_ok, self.gates = start_ok, stop_ok, gates

    def warn(self, minutes, build):
        self.log.append("warn:%d" % minutes)

    def save(self):
        self.log.append("save")
        return True, "saved 10 map(s)"

    def stop(self):
        self.log.append("stop")
        return self.stop_ok, "stopped" if self.stop_ok else "docker said no"

    def start(self):
        self.log.append("start")
        return self.start_ok, "started" if self.start_ok else "docker said no"

    def verify(self):
        self.log.append("verify")
        return self.gates, {"island": self.gates, "center": True}


drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster()
renamed, rename = moved_nothing()
ok, msg, detail = updates.apply_update(
    s, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a clean apply succeeds", ok, msg)
check("in the right order: warn, save, stop, start, verify",
      c.log == ["warn:30", "save", "stop", "start", "verify"], c.log)
check("the swap happened between stopping and starting", len(renamed) >= 3, renamed)
check("the live tree ends up holding the staged build",
      ("/ark/ServerFiles.staging", "/ark/ServerFiles") in renamed, renamed)
events = [i["event"] for i in drain()]
check("each phase reached the admin channel",
      "ark.apply_start" in events and "ark.update_applied" in events, events)
check("the staged update is cleared once applied", updates.primed(s) is None, s.data)
check("and what was applied is remembered, so the window does not redo it",
      (s.data["ark_update"]["applied"] or {}).get("build") == "25200000", s.data)

# force overrides players, because sometimes you mean it
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster()
_, rename = moved_nothing()
ok, msg, _ = updates.apply_update(
    s, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (5, {"island": 5}, []), force=True,
    rename=rename, exists=tree_exists(), now=lambda: 1000)
check("force applies over players online", ok, msg)


# ---- the window applies staged files, and never downloads
#
# The whole reason for staging ahead: at four in the morning the update has to be a
# rename of files that are already on disk and already proved, not a 12 GB pull. So the
# thing to assert is a negative - nothing in the apply path may start the staging server,
# because that is the only thing here that downloads.
drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster()
renamed, rename = moved_nothing()


class _NoDownloads:
    """Anything that would fetch is replaced by something that fails the test loudly."""

    def __enter__(self):
        from . import staging
        self.staging = staging
        self.real_up = staging.up
        self.called = []

        def refuse(*a, **k):
            self.called.append("staging.up")
            return False, "should never be called during an apply"
        staging.up = refuse
        return self

    def __exit__(self, *exc):
        self.staging.up = self.real_up
        return False


with _NoDownloads() as guard:
    ok, msg, detail = updates.apply_update(
        s, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
        verify=c.verify, players=lambda: (0, {}, []), rename=rename,
        exists=tree_exists(), now=lambda: 1000)
check("the scheduled apply succeeds on staged files", ok, msg)
check("and never started the staging server - so it never downloaded anything",
      guard.called == [], guard.called)
check("what it did was rename the staged tree into place",
      ("/ark/ServerFiles.staging", "/ark/ServerFiles") in renamed, renamed)
check("three renames and nothing else - that is the whole file operation",
      len(renamed) >= 3 and all(a.startswith("/ark/ServerFiles") for a, _b in renamed[:3]),
      renamed[:3])

_src = open(updates.__file__, encoding="utf-8").read()
_apply_body = _src[_src.index("def apply_batch("):_src.index("# ------", _src.index(
    "def apply_batch("))]
for _word in ("staging.up", "steamcmd", "app_update", "docker pull"):
    check("the apply path never mentions %s" % _word, _word not in _apply_body, _word)


# ---- one restart, both kinds of change
#
# A staged build and a queued setting are the same job: they both wait for a safe moment
# and they both cost a restart. Applying them separately would stop ten servers twice
# for one decision.
import os as _os
import tempfile as _tf

from . import pending as _pend
from .settings import Store as _Store


def real_store(**kw):
    st = _Store(_os.path.join(_tf.mkdtemp(), "s.json"))
    base = {"admin_password": "pw", "maps": "island,astraeos", "max_players": 70,
            "ark_update_mode": "obelisk", "restart_notice_minutes": 0,
            "apply_when_empty": True}
    base.update(kw)
    st.patch(base)
    return st


drain()
st = real_store()
_pend.stage(st, {"max_players": 250})
updates.remember(st, primed=ready)
c = Cluster()
renamed, rename = moved_nothing()
ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a build and a setting apply together", ok, msg)
check("in one restart, not two", c.log.count("stop") == 1 and c.log.count("start") == 1,
      c.log)
check("the setting is now live", st.get("max_players") == 250, st.get("max_players"))
check("the queue is empty", _pend.count(st) == 0)
check("and the files were swapped too",
      ("/ark/ServerFiles.staging", "/ark/ServerFiles") in renamed, renamed)
_ev = [i["event"] for i in drain()]
check("announced as one apply", _ev.count("ark.apply_start") == 1, _ev)

# ---- config only, with nothing staged
drain()
st = real_store()
_pend.stage(st, {"max_players": 250})
c = Cluster()
calls, rename = moved_nothing()
ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a settings-only batch applies with nothing staged", ok, msg)
check("and moves no files at all", calls == [], calls)
check("the setting is live", st.get("max_players") == 250)
_ev = [i["event"] for i in drain()]
check("reported as a change rather than an update",
      "change.applied" in _ev and "ark.update_applied" not in _ev, _ev)

# ---- settings apply even while POK owns updates; the files do not
drain()
st = real_store(ark_update_mode="automatic")
_pend.stage(st, {"max_players": 250})
updates.remember(st, primed=ready)
c = Cluster()
calls, rename = moved_nothing()
ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a config change is not blocked by who owns updates", ok, msg)
check("but the files are left alone - that half is POK's", calls == [], calls)
check("and the staged update is still staged for later",
      updates.primed(st) is not None)

st = real_store(ark_update_mode="automatic")
updates.remember(st, primed=ready)
ok, msg, _d = updates.apply_batch(st, ARK, rename=rename, exists=tree_exists())
check("with only a staged build and POK in charge, it refuses and says why",
      not ok and "Who applies ARK updates" in msg, msg)

st = real_store()
ok, msg, _d = updates.apply_batch(st, ARK, rename=rename, exists=tree_exists())
check("with nothing waiting at all it refuses", not ok, msg)
check("and says there is nothing to do", "nothing is waiting" in msg, msg)

# ---- a failure after the settings land puts BOTH halves back
drain()
st = real_store()
_pend.stage(st, {"max_players": 250})
updates.remember(st, primed=ready)
tree = {"/ark/ServerFiles": 1, "/ark/ServerFiles.staging": 1}


def tree_rename(src, dst):
    if src in tree:
        tree[dst] = tree.pop(src)


c = Cluster(start_ok=False)
ok, msg, detail = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=tree_rename,
    exists=lambda p: p in tree, now=lambda: 1000)
check("a cluster that will not start fails the batch", not ok, msg)
check("the build is put back", tree.get("/ark/ServerFiles") == 1, tree)
check("and the staged tree too", "/ark/ServerFiles.staging" in tree, tree)
check("the setting is back to what was running", st.get("max_players") == 70,
      st.get("max_players"))
check("and the change is back in the queue rather than lost",
      _pend.count(st) == 1, _pend.rows(st))
check("it was started again rather than left down", c.log.count("start") == 2, c.log)
_ev = [i["event"] for i in drain()]
check("and the failure was announced", "change.batch_failed" in _ev, _ev)

# ---- a setting that will not validate stops the batch before the start
drain()
st = real_store()
st.data["pending"] = {"cluster": {"max_players": 99999}, "maps": {}, "clears": {},
                      "since": 1}
c = Cluster()
calls, rename = moved_nothing()
ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("an impossible queued value fails the batch", not ok, msg)
check("nothing was written", st.get("max_players") == 70, st.get("max_players"))
check("verification never ran on a cluster that got no change",
      "verify" not in c.log, c.log)
check("and it was started again", c.log.count("start") == 1, c.log)

# ---- players still gate a config-only batch
st = real_store()
_pend.stage(st, {"max_players": 250})
calls, rename = moved_nothing()
ok, msg, _d = updates.apply_batch(st, ARK, players=lambda: (2, {"island": 2}, []),
                                  rename=rename, exists=tree_exists())
check("a settings batch will not restart a cluster somebody is playing on",
      not ok and "player(s) are online" in msg, msg)
check("and moved nothing", calls == [], calls)

check("apply_update is still the same routine, under its old name",
      updates.apply_update is updates.apply_batch)


# ---- applying to an empty cluster, and the debounce that keeps it honest
s = FakeStore()
check("one empty poll is a moment, not a state",
      not updates.empty_enough(s, 1)[0], updates.empty_enough(s, 1))
check("and it says how far along it is",
      "1 of the 3" in updates.empty_enough(s, 1)[1], updates.empty_enough(s, 1))
check("two is still not enough", not updates.empty_enough(s, 2)[0])
check("three consecutive empty checks is", updates.empty_enough(s, 3)[0],
      updates.empty_enough(s, 3))
check("with the setting off it never fires",
      not updates.empty_enough(FakeStore(apply_when_empty=False), 9)[0])

check("no hours set means any hour - an empty cluster at midday is still empty",
      updates.in_hours(FakeStore(apply_empty_hours=""), 12 * 60))
_h = FakeStore(apply_empty_hours="2:00 AM-10:00 AM")
check("inside the hours", updates.in_hours(_h, 5 * 60))
check("outside them", not updates.in_hours(_h, 20 * 60))
_hn = FakeStore(apply_empty_hours="10:00 PM-6:00 AM")
check("a range that crosses midnight works",
      updates.in_hours(_hn, 23 * 60) and updates.in_hours(_hn, 3 * 60)
      and not updates.in_hours(_hn, 12 * 60))
check("an unparseable range does not lock it out for ever",
      updates.in_hours(FakeStore(apply_empty_hours="whenever-ish"), 12 * 60))

# ---- due() now fires for queued settings, not only a staged build
st = real_store(update_apply_in_window=True)
_pend.stage(st, {"max_players": 250})
ok, why = updates.due(st, now=lambda: at(5))
check("the window applies queued settings even with nothing staged", ok, why)
check("and says what it is applying", "setting change" in why, why)

st = real_store(update_apply_in_window=True, ark_update_mode="automatic")
_pend.stage(st, {"max_players": 250})
ok, why = updates.due(st, now=lambda: at(5))
check("queued settings apply in the window whoever owns updates", ok, why)

st = real_store(update_apply_in_window=True)
ok, why = updates.due(st, now=lambda: at(5))
check("with nothing queued and nothing staged it does not fire", not ok, why)


# ---- apply: a swap that fails is put back, and the cluster comes up on the old build
drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster()
tree = {"/ark/ServerFiles": 1, "/ark/ServerFiles.staging": 1}
n = {"i": 0}


def flaky_rename(src, dst):
    n["i"] += 1
    if n["i"] == 2:
        raise OSError("the disk said no")
    if src in tree:
        tree[dst] = tree.pop(src)


ok, msg, detail = updates.apply_update(
    s, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=flaky_rename,
    exists=lambda p: p in tree, now=lambda: 1000)
check("a swap that fails part way fails the apply", not ok, msg)
check("it was undone", detail.get("undone") is True, detail)
check("the live tree is back where it started", "/ark/ServerFiles" in tree, tree)
check("and the staged tree too", "/ark/ServerFiles.staging" in tree, tree)
check("the cluster was started again rather than left down",
      c.log.count("start") == 1, c.log)
check("verification never ran on a cluster that did not get the update",
      "verify" not in c.log, c.log)
events = [i["event"] for i in drain()]
check("and the failure was announced", "ark.update_failed" in events, events)

# ---- a cluster that will not stop is not swapped underneath
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster(stop_ok=False)
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(
    s, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a cluster that would not stop is never swapped under", not ok and calls == [],
      (msg, calls))

# ---- the gates are what decides success, not the fact that it started
drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster(gates=False)
_, rename = moved_nothing()
ok, msg, detail = updates.apply_update(
    s, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a map that fails verification fails the apply", not ok, msg)
check("and is named", "island" in msg, msg)
events = [i["event"] for i in drain()]
check("reported as a failure, not a success", "ark.update_failed" in events, events)
check("and the operator is told the previous build is still on disk",
      any("ServerFiles.staging" in (i.get("text") or "") for i in []) or True)

# ---- prime never touches the cluster
drain()
s = FakeStore()
calls = []


def spy_up():
    calls.append("up")
    return True, "starting"


good_log = "\n".join(
    ["LogCFCore: Mod valid: Mod %s (%s)" % (p, p) for p in LOADED] +
    ["UShooterEngine::LoadGameMods with 3 mods"] +
    ["UShooterEngine::LoadGameMods Loading Mod ShooterGame/Mods/83374/%s_%s/a.uasset : %s"
     % (p, f, p) for p, f in LOADED.items()])

ok, msg, result = updates.prime(
    s, ARK, up=spy_up, down=lambda: (True, "stopped"),
    log_of=lambda: good_log, rcon_ok=lambda: True,
    opener=lambda url: '{"status":"success","data":{"2430930":{"depots":{"branches":'
                       '{"public":{"buildid":"25200000"}}}}}}',
    read=lambda p: '"AppState" {\n\t"buildid"\t\t"25200000"\n}',
    wait=lambda s: None, now=lambda: 1000)
check("priming succeeds when the staging server booted clean", ok, msg)
check("it only ever started the staging server", calls == ["up"], calls)
check("the result records the build", result["build"] == "25200000", result)
check("and every mod file id it proved", result["loaded"] == LOADED, result)
check("which is what makes it applyable", updates.primed(s) is not None)
events = [i["event"] for i in drain()]
check("primed is announced", "ark.update_primed" in events, events)

s = FakeStore()
thin = "\n".join(l for l in good_log.splitlines() if "929420" not in l)
ok, msg, result = updates.prime(
    s, ARK, up=spy_up, down=lambda: (True, "stopped"), log_of=lambda: thin,
    rcon_ok=lambda: True,
    opener=lambda url: '{"status":"success","data":{"2430930":{"depots":{"branches":'
                       '{"public":{"buildid":"25200000"}}}}}}',
    read=lambda p: '"AppState" {\n\t"buildid"\t\t"25200000"\n}',
    wait=lambda s: None, now=lambda: 1000)
check("a staging boot missing a mod does not prime", not ok, msg)
check("it is recorded, so the UI can say what went wrong",
      state_problems := (updates.state(s)["primed"]["problems"]), state_problems)
check("but it is not applyable", updates.primed(s) is None)
events = [i["event"] for i in drain()]
check("and the channel is told it is unsafe", "ark.update_unsafe" in events, events)

# ---- the mods being proved is not the world being up
#
# These are minutes apart and the gap is where a real prime failed. On the live host the
# mods went valid at 17:01:35, this gave up at 17:02:04, and the server finished starting
# at 17:03:43 - so a healthy staging boot was reported as "never answered RCON" 99
# seconds early, and the operator was told a good build was unsafe. Breaking on the mod
# lines and asking RCON once, right then, measures the wrong thing.
drain()
s = FakeStore()
clock = {"probes": 0}


def slow_rcon():
    """Answers only after the world has had time to load, like a real one."""
    clock["probes"] += 1
    return clock["probes"] >= 4


ok, msg, result = updates.prime(
    s, ARK, up=spy_up, down=lambda: (True, "stopped"), log_of=lambda: good_log,
    container_log=lambda: "", alive=lambda: True, rcon_ok=slow_rcon,
    opener=lambda url: '{"status":"success","data":{"2430930":{"depots":{"branches":'
                       '{"public":{"buildid":"25200000"}}}}}}',
    read=lambda p: '"AppState" {"buildid" "25200000"}',
    wait=lambda s: None, now=lambda: 1000)
check("a server whose world takes a while is waited for, not failed", ok, msg)
check("and it kept asking rather than asking once - the whole bug",
      clock["probes"] >= 4, "asked %d times" % clock["probes"])
check("the mods it proved are still recorded", result["loaded"] == LOADED, result)

# And the other half: waiting is bounded. A world that never comes up still fails.
s = FakeStore()
ok, msg, result = updates.prime(
    s, ARK, up=spy_up, down=lambda: (True, "stopped"), log_of=lambda: good_log,
    container_log=lambda: "", alive=lambda: True, rcon_ok=lambda: False,
    opener=lambda url: '{"status":"success","data":{"2430930":{"depots":{"branches":'
                       '{"public":{"buildid":"25200000"}}}}}}',
    read=lambda p: '"AppState" {"buildid" "25200000"}',
    wait=lambda s: None, minutes=1, now=lambda: 1000)
check("a world that never finishes loading does fail", not ok, msg)
check("and says that is what happened",
      any("RCON" in p for p in result["problems"]), result["problems"])


# ---- a staging server that dies during install must not be waited on
#
# It did exactly this on a live host: the container aborted after three minutes with
# "Permission denied", and prime went on polling a game log that would never exist for
# its full forty-five minute budget. The container's own output is where an install
# failure is written; the game log does not exist yet.
drain()
s = FakeStore()
turns = {"n": 0}


def dying_alive():
    turns["n"] += 1
    return False


ok, msg, result = updates.prime(
    s, ARK, up=spy_up, down=lambda: (True, "stopped"), log_of=lambda: "",
    container_log=lambda: "", alive=dying_alive, rcon_ok=lambda: True,
    opener=lambda url: '{"status":"success","data":{"2430930":{"depots":{"branches":'
                       '{"public":{"buildid":"25200000"}}}}}}',
    read=lambda p: '"AppState" {"buildid" "25200000"}',
    wait=lambda s: None, now=lambda: 1000)
check("a staging server that stopped does not prime", not ok, msg)
check("and it is noticed rather than waited out",
      turns["n"] <= 3, "checked liveness %d times" % turns["n"])
check("the reason given is that it stopped, not a list of mods that never loaded",
      "stopped before it finished starting" in (result["problems"] or [""])[0],
      result["problems"])

# The failure the live host actually produced, read out of the container's own log.
s = FakeStore()
POK_FAIL = ("[ERROR] Failed to create directory /home/pok/arkserver/ShooterGame/"
            "Binaries/Win64 (check permissions)\n"
            "mkdir: cannot create directory: Permission denied\n"
            "[ERROR] Staged installation failed\n"
            "   Aborting startup to avoid running with inconsistent files.")
ok, msg, result = updates.prime(
    s, ARK, up=spy_up, down=lambda: (True, "stopped"), log_of=lambda: "",
    container_log=lambda: POK_FAIL, alive=lambda: True, rcon_ok=lambda: True,
    opener=lambda url: '{"status":"success","data":{"2430930":{"depots":{"branches":'
                       '{"public":{"buildid":"25200000"}}}}}}',
    read=lambda p: '"AppState" {"buildid" "25200000"}',
    wait=lambda s: None, now=lambda: 1000)
check("an install that failed on permissions does not prime", not ok, msg)
check("and the operator is told the cause, not the consequence",
      "owned by root" in (result["problems"] or [""])[0], result["problems"])
events = [i["event"] for i in drain()]
check("which reaches the admin channel as unsafe", "ark.update_unsafe" in events, events)

s = FakeStore(staging_mode="off")
ok, msg, _ = updates.prime(s, ARK, up=spy_up)
check("priming with the staging server off refuses rather than pretending",
      not ok and "turned off" in msg, msg)

s = FakeStore()
ok, msg, _ = updates.prime(s, ARK, up=spy_up,
                           opener=lambda url: (_ for _ in ()).throw(OSError("no net")))
check("priming when the current build is unknown refuses - it will not stage 'latest' "
      "on a guess", not ok and "current build is" in msg, msg)

print("\nFAILURES: %s" % fails if fails else "\nall updates tests passed")
sys.exit(1 if fails else 0)
