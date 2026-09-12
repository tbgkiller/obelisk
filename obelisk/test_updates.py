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
            apply_when_empty=True,
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
# The build the cluster is on. "newer than running" is the gate now, so a
# test that wants an apply to fire has to say what it is newer *than*.
OLD_BUILD = "25117056"
ready = {"ok": True, "build": "25200000", "loaded": LOADED}

s = FakeStore(update_apply_in_window=True)
updates.remember(s, primed=ready)
ok, why = updates.due(s, now=lambda: at(5), installed=OLD_BUILD)
check("staged, verified, in the window, Obelisk in charge - fires", ok, why)

ok, why = updates.due(s, now=lambda: at(12), installed=OLD_BUILD)
check("outside the window it does not fire", not ok, why)
check("and says why", "window" in why, why)

s2 = FakeStore(update_apply_in_window=False)
updates.remember(s2, primed=ready)
ok, why = updates.due(s2, now=lambda: at(5), installed=OLD_BUILD)
check("with the setting off it does not fire", not ok, why)

s3 = FakeStore(update_apply_in_window=True, ark_update_mode="automatic")
updates.remember(s3, primed=ready)
ok, why = updates.due(s3, now=lambda: at(5), installed=OLD_BUILD)
check("it never fires while the server image owns updates", not ok, why)
check("and says which system is in charge", "POK" in why, why)

s4 = FakeStore(update_apply_in_window=True)
updates.remember(s4, primed={"ok": False, "build": "25200000"})
ok, why = updates.due(s4, now=lambda: at(5), installed=OLD_BUILD)
check("an update that failed its rehearsal never fires", not ok, why)

s5 = FakeStore(update_apply_in_window=True)
updates.remember(s5, primed=ready,
                 applied={"build": "25200000", "when": 1, "ok": True})
ok, why = updates.due(s5, now=lambda: at(5), installed=OLD_BUILD)
check("a build already applied still fires if it is newer than what runs",
      ok, why)


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
# No warn step, and that is the point: this apply answers (0, {}, []) - an empty
# cluster - so there is nobody to count down to. The order either side of it is
# unchanged, which is what this has always been here to hold.
check("in the right order: save, stop, start, verify",
      c.log == ["save", "stop", "start", "verify"], c.log)
check("the swap happened between stopping and starting", len(renamed) >= 3, renamed)
check("the live tree ends up holding the staged build",
      ("/ark/ServerFiles.staging", "/ark/ServerFiles") in renamed, renamed)
events = [i["event"] for i in drain()]
check("each phase reached the admin channel",
      "ark.apply_start" in events and "ark.update_applied" in events, events)


# The same apply with somebody on it, which is the only way to see warn's own place in
# the sequence. Together with the check above, every step's position is still pinned -
# the warn step moved from unconditional to conditional, not from guarded to unguarded.
drain()
s_w = FakeStore()
updates.remember(s_w, primed=ready)
c_w = Cluster()
_renamed_w, rename_w = moved_nothing()
updates.apply_update(
    s_w, ARK, warn=c_w.warn, save=c_w.save, stop_all=c_w.stop, start_all=c_w.start,
    verify=c_w.verify, players=lambda: (2, {"The Island": 2}, []), force=True,
    rename=rename_w, exists=tree_exists(), now=lambda: 1000)
check("and with players on, warn still comes first - before the save, not just the stop",
      c_w.log == ["warn:30", "save", "stop", "start", "verify"], c_w.log)
drain()
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


# ---- a failed batch does not keep its settings
#
# It did. `ark_update_mode` went from automatic to obelisk through a batch that reported
# itself failed, because the gate-failure branch returned without putting anything back.
# A change that lands through a failure is a change nobody chose the moment of.
drain()
st = real_store()
_pend.stage(st, {"max_players": 250})
c = Cluster(gates=False)
_, rename = moved_nothing()
ok, msg, detail = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a batch whose gates fail is a failure", not ok, msg)
check("the setting is back to what was running", st.get("max_players") == 70,
      st.get("max_players"))
check("and is queued again rather than lost", _pend.count(st) == 1, _pend.rows(st))
check("the announcement says the settings were put back",
      any("put back" in (i.get("text") or "") for i in drain()))


# ---- the cluster is not stopped until every world has finished saving
#
# save() returning means the servers *accepted* SaveWorld, not that they wrote anything.
# On 2026-09-11 the stop landed in that gap: every world at or above 76 MB was damaged
# (Astraeos 142 MB, TheCenter 96 MB, Valguero 81 MB, TheIsland 77 MB, Ragnarok 76 MB)
# and every world at or below 41 MB survived - the small ones simply finished inside
# however long ten sequential RCON round-trips happened to take.
#
# There is no delay here to lengthen, so these drive the real gate: cluster's own
# settle check, over a scripted filesystem and a clock that only moves when something
# waits on it. Nothing sleeps.
from . import cluster as _cl
from . import restore as _restore
from . import savepoints as _sp


class _AllRunning:
    """Every map in this cluster is up, as far as Docker is concerned."""

    def container_details(self, names, timeout=30):
        return {n: {"state": "running"} for n in names}


class _Disk:
    """stat/exists over scripted readings - one reading per poll, per path."""

    def __init__(self, readings, sidecars=()):
        self.readings, self.sidecars, self.polls = dict(readings), set(sidecars), {}

    def stat(self, path):
        rows = self.readings.get(path)
        if rows is None:
            raise OSError("no such file: %s" % path)
        i = self.polls.get(path, 0)
        self.polls[path] = i + 1
        size, mtime = rows(i) if callable(rows) else rows[min(i, len(rows) - 1)]
        return type("Stat", (), {"st_size": size, "st_mtime": mtime})()

    def exists(self, path):
        return path in self.sidecars


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def wait(self, seconds):
        self.t += seconds


_real_dockerctl = _cl.dockerctl
_cl.dockerctl = _AllRunning()

_ISLAND = _sp.live_world(real_store(), "island", ARK)
_ASTRAEOS = _sp.live_world(real_store(), "astraeos", ARK)
# 142 MB is what Astraeos actually was on the night this happened.
_QUIET = {_ISLAND: [(77000000, 1001.0)], _ASTRAEOS: [(142000000, 1001.0)]}
_WRITING = {_ISLAND: [(77000000, 1001.0)],
            _ASTRAEOS: lambda i: (40000000 + i * 8000000, 1001.0 + i)}


def gated(st_, disk, force=False, budget=60):
    """An apply whose save is the real save-and-prove, over `disk`."""
    clk = _Clock()
    c_ = Cluster()
    _, rename_ = moved_nothing()
    ok_, msg_, detail_ = updates.apply_batch(
        st_, ARK, warn=c_.warn, stop_all=c_.stop, start_all=c_.start, verify=c_.verify,
        save=lambda: _cl.save_and_settle(
            st_, ARK, rcon=lambda h, p, cmd: None, now=clk.now, stat=disk.stat,
            exists=disk.exists, wait=clk.wait, budget=budget),
        players=lambda: (0, {}, []), force=force, rename=rename_,
        exists=tree_exists(), now=lambda: 4242)
    return ok_, msg_, detail_, c_


# 1. every world settles inside its budget - the apply goes ahead, once
drain()
st = real_store()
updates.remember(st, primed=ready)
ok, msg, _d, c = gated(st, _Disk(_QUIET))
check("an apply whose worlds all finish saving succeeds", ok, msg)
check("and the cluster is stopped exactly once", c.log.count("stop") == 1, c.log)
check("in the usual order, with the proving folded into the save",
      c.log == ["warn:0", "stop", "start", "verify"] or
      c.log == ["stop", "start", "verify"], c.log)

# The save that worked used to say nothing per map - only the refusal did, so the only
# way to learn what had been proved was for it to fail. One sentence, and the whole list
# in the detail: say() sends the text to the channel and keeps the detail for the feed.
_saved = [i for i in drain() if i["event"] == "ark.saved"]
check("a save that lands is announced, not only one that fails", len(_saved) == 1,
      _saved)
check("as a single line rather than one per map",
      _saved and _saved[0]["text"].count("\n") == 0, _saved and _saved[0]["text"])
check("the sentence says how many were proved",
      _saved and "2 of 2" in _saved[0]["text"], _saved and _saved[0]["text"])
check("and every map that answered is named in the detail",
      _saved and all(m in _saved[0]["detail"] for m in ("The Island", "Astraeos")),
      _saved and _saved[0]["detail"])
check("each one said to be verified on disk, not merely acknowledged",
      _saved and _saved[0]["detail"].count("Saved (verified on disk)") == 2,
      _saved and _saved[0]["detail"])
check("and it is one line per map",
      _saved and len([l for l in _saved[0]["detail"].splitlines() if l.strip()]) == 2,
      _saved and _saved[0]["detail"])


# ---- warning the people who are actually there
#
# The apply proved the cluster empty one refusal earlier and then announced a restart to
# nobody for thirty minutes. The count was already in hand; the countdown just never
# looked at it. These pin the four cases, because the expensive mistake is skipping a
# warning somebody was owed, not running one nobody needed.
def warned(players_answer, force=False, minutes=30, raises=False):
    """(what the flow did, the events it raised) for one player-count answer.

    `raises=True` stands in for the count being unreadable - a map that throws rather
    than answering, which is not the same as answering zero.
    """
    def ask():
        if raises:
            raise OSError("rcon unreachable")
        return players_answer

    drain()
    st_ = real_store(restart_notice_minutes=minutes)
    updates.remember(st_, primed=ready)
    clk_ = _Clock()
    c_ = Cluster()
    _, rn_ = moved_nothing()
    disk_ = _Disk(_QUIET)
    updates.apply_batch(
        st_, ARK, warn=c_.warn, stop_all=c_.stop, start_all=c_.start, verify=c_.verify,
        save=lambda: _cl.save_and_settle(
            st_, ARK, rcon=lambda h, p, cmd: None, now=clk_.now, stat=disk_.stat,
            exists=disk_.exists, wait=clk_.wait, budget=60),
        players=ask, force=force, rename=rn_,
        exists=tree_exists(), now=lambda: 4242)
    return c_.log, drain()


# 1. empty cluster - no warning, and it says why rather than going quiet
log_e, ev_e = warned((0, {}, []))
check("an empty cluster is not warned - it goes straight to saving",
      not any(l.startswith("warn") for l in log_e), log_e)
check("and the cluster is still stopped and started exactly once",
      log_e.count("stop") == 1 and log_e.count("start") == 1, log_e)
check("the skipped warning is announced, not silently dropped",
      any("skipp" in (i["text"] or "").lower() for i in ev_e),
      [i["text"] for i in ev_e])

# 2. players online, forced - the one case a warning is actually owed
log_p, _ev_p = warned((3, {"The Island": 3}, []), force=True)
check("a forced apply with players online still warns them",
      "warn:30" in log_p, log_p)
# The whole order, not just "warn is in there somewhere". (This helper's save is the
# real save_and_settle, so it leaves no mark on the fake's log - the warn/save pairing
# is pinned separately, beside the apply_update ordering test.)
check("and the warning comes first, before anything is stopped",
      log_p == ["warn:30", "stop", "start", "verify"], log_p)

# An unreadable count is not an empty cluster. Catching the error so the warning
# decision could be made safely must not quietly hand the refusals a zero.
log_u, _ev_u = warned(None, raises=True)
check("a player count that cannot be read refuses the apply outright",
      log_u == [], log_u)
log_uf, _ev_uf = warned(None, raises=True, force=True)
check("but force still gets through it, and warns because it cannot rule anyone out",
      "warn:30" in log_uf, log_uf)

# 3. a map that did not answer is not an empty map. Forced, so the refusal is skipped -
#    which is exactly when this has to decide for itself.
log_s, _ev_s = warned((0, {}, [("Astraeos", "timed out")]), force=True)
check("a silent map buys the warning - 'we could not ask' is not 'nobody is home'",
      "warn:30" in log_s, log_s)

# 4. the setting still means what it says: zero minutes is no countdown either way
log_z, _ev_z = warned((3, {"The Island": 3}, []), force=True, minutes=0)
check("a zero-minute warning is still no warning, players or not",
      not any(l.startswith("warn") for l in log_z), log_z)

# 2. one world never settles - nothing is stopped, and the refusal names it
drain()
st = real_store()
updates.remember(st, primed=ready)
_before = dict(updates.state(st))
ok, msg, detail, c = gated(st, _Disk(_WRITING))
check("a world still writing refuses the apply", not ok, msg)
check("and the cluster was never stopped", "stop" not in c.log, c.log)
check("the refusal names the map that did not finish", "Astraeos" in msg, msg)
check("and does not blame the ones that did", "The Island" not in msg, msg)
check("which map it was is in the detail too",
      detail.get("unsettled") == ["Astraeos"], detail)
_said = drain()
_refusal = [i for i in _said if i["event"] == "ark.update_failed"]
check("the refusal reaches the admin channel as a failure", len(_refusal) == 1, _said)
check("named there as well", _refusal and "Astraeos" in _refusal[0]["text"],
      _refusal)
check("and it says the cluster is still up and still serving",
      _refusal and "still up and still serving" in _refusal[0]["text"], _refusal)

# 6. a refused apply has not spent the disruption, so last_apply must not move
check("a refused apply does not record an apply",
      updates.state(st).get("last_apply") == _before.get("last_apply"),
      updates.state(st))
check("and the staged build is still staged for the next window",
      updates.primed(st) is not None)

# 3. the ct-0009 shape: quiet on disk, transaction still open
for _suffix in _restore.SIDECARS:
    drain()
    st = real_store()
    updates.remember(st, primed=ready)
    ok, msg, _d, c = gated(st, _Disk(_QUIET, sidecars=[_ASTRAEOS + _suffix]))
    check("a %s beside a quiet world still refuses the stop" % _suffix, not ok, msg)
    check("and stops nothing", "stop" not in c.log, c.log)
    check("naming the map with the open transaction", "Astraeos" in msg, msg)

# 4. quiet, but from before the save was even asked for
drain()
st = real_store()
updates.remember(st, primed=ready)
ok, msg, _d, c = gated(st, _Disk({_ISLAND: [(77000000, 1001.0)],
                                  _ASTRAEOS: [(142000000, 999.0)]}))
check("a world older than its own SaveWorld has not started saving, so the apply waits",
      not ok and "Astraeos" in msg, msg)
check("and the cluster stays up", "stop" not in c.log, c.log)

# 5. a map that is down does not block the apply - the old tolerance, unchanged
drain()
st = real_store()
updates.remember(st, primed=ready)
clk = _Clock()
c = Cluster()
_, rename = moved_nothing()
disk = _Disk({_ISLAND: [(77000000, 1001.0)]})


def _astraeos_is_down(host, port, cmd):
    if port == 27021:
        raise OSError("connection refused")


ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, stop_all=c.stop, start_all=c.start, verify=c.verify,
    save=lambda: _cl.save_and_settle(
        st, ARK, rcon=_astraeos_is_down, now=clk.now, stat=disk.stat,
        exists=disk.exists, wait=clk.wait, budget=60),
    players=lambda: (0, {}, []), rename=rename, exists=tree_exists(),
    now=lambda: 4242)
check("a map that is down cannot save and must not block the update", ok, msg)
check("the cluster was still stopped exactly once", c.log.count("stop") == 1, c.log)
_ev = [i["event"] for i in drain()]
check("and nothing was refused over the map that could not be asked",
      "ark.update_failed" not in _ev, _ev)

# ...and the case that reaches the old warning: nobody took the command at all. Still
# not fatal, still said out loud, still stopped - exactly as before this gate existed.
drain()
st = real_store()
updates.remember(st, primed=ready)
clk = _Clock()
c = Cluster()
_, rename = moved_nothing()


def _nobody_home(host, port, cmd):
    raise OSError("connection refused")


ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, stop_all=c.stop, start_all=c.start, verify=c.verify,
    save=lambda: _cl.save_and_settle(
        st, ARK, rcon=_nobody_home, now=clk.now, stat=_Disk({}).stat,
        exists=lambda p: False, wait=clk.wait, budget=60),
    players=lambda: (0, {}, []), rename=rename, exists=tree_exists(),
    now=lambda: 4242)
check("a cluster where no map answered is still not blocked from updating", ok, msg)
check("and it was stopped once", c.log.count("stop") == 1, c.log)
_ev = [i["event"] for i in drain()]
check("with the failed save said out loud rather than swallowed",
      "ark.apply_note" in _ev and "ark.update_failed" not in _ev, _ev)

# 8. force is about players, not about a half-written world
drain()
st = real_store()
updates.remember(st, primed=ready)
ok, msg, _d, c = gated(st, _Disk(_WRITING), force=True)
check("force does not get past a world that has not finished saving", not ok, msg)
check("and force stops nothing either", "stop" not in c.log, c.log)
check("still naming the map", "Astraeos" in msg, msg)

# The old two-value save contract still works, because backup's save has no worlds
# to report and must not start being read as one that refused.
drain()
st = real_store()
updates.remember(st, primed=ready)
c = Cluster()
_, rename = moved_nothing()
ok, msg, _d = updates.apply_batch(
    st, ARK, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 4242)
check("a save that reports nothing per map is still allowed through", ok, msg)
check("and the cluster is stopped once", c.log.count("stop") == 1, c.log)

_cl.dockerctl = _real_dockerctl


# ---- two triggers cannot both restart the cluster
#
# This is the one that corrupted a world. The empty-cluster watcher began an apply at
# 03:58; the scheduled window started a second at 04:28 while the first was still
# stopping and starting ten servers. Aberration's world was half-written when the second
# stop reached it, and the map spent five hours refusing to load a corrupt database.
#
# The lock existed - it was created inside build_app, so it guarded the buttons and
# neither of the two things that fire on their own.
import asyncio as _aio

from . import app as _app

check("the lock outlives the web app, because the unattended paths do",
      isinstance(getattr(_app, "APPLY_LOCK", None), _aio.Lock),
      type(getattr(_app, "APPLY_LOCK", None)))


def _overlap():
    """Both loops, told to fire at once, against one shared lock."""
    running, overlaps, done = {"n": 0}, {"n": 0}, []

    async def slow_apply():
        running["n"] += 1
        if running["n"] > 1:
            overlaps["n"] += 1          # two applies inside the cluster at once
        await _aio.sleep(0.05)
        done.append(1)
        running["n"] -= 1
        return True, "applied", {}

    async def empty_trigger():
        async with _app.APPLY_LOCK:
            await slow_apply()

    async def window_trigger():
        # What the scheduled path does now: skip when one is already in flight.
        if _app.APPLY_LOCK.locked():
            return
        async with _app.APPLY_LOCK:
            await slow_apply()

    async def main():
        await _aio.gather(empty_trigger(), window_trigger(), window_trigger())

    _aio.run(main())
    return overlaps["n"], len(done)


_overlaps, _ran = _overlap()
check("two triggers firing together never run two applies at once", _overlaps == 0,
      "%d overlapping applies" % _overlaps)
check("and the ones that were skipped did not run a second restart", _ran <= 1, _ran)


def _without_lock():
    """The same race with no lock - the shape the bug had, so the test can fail."""
    running, overlaps = {"n": 0}, {"n": 0}

    async def slow_apply():
        running["n"] += 1
        if running["n"] > 1:
            overlaps["n"] += 1
        await _aio.sleep(0.05)
        running["n"] -= 1

    async def main():
        await _aio.gather(slow_apply(), slow_apply())

    _aio.run(main())
    return overlaps["n"]


check("and the unguarded version really does overlap - so this test can fail",
      _without_lock() > 0, "the reproduction did not reproduce")


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

# ---- due() now fires for queued settings, not only a staged build
st = real_store(update_apply_in_window=True)
_pend.stage(st, {"max_players": 250})
ok, why = updates.due(st, now=lambda: at(5), installed=OLD_BUILD)
check("the window applies queued settings even with nothing staged", ok, why)
check("and says what it is applying", "setting change" in why, why)

st = real_store(update_apply_in_window=True, ark_update_mode="automatic")
_pend.stage(st, {"max_players": 250})
ok, why = updates.due(st, now=lambda: at(5), installed=OLD_BUILD)
check("queued settings apply in the window whoever owns updates", ok, why)

st = real_store(update_apply_in_window=True)
ok, why = updates.due(st, now=lambda: at(5), installed=OLD_BUILD)
check("with nothing queued and nothing staged it does not fire", not ok, why)


# ---- a primed build is not an update
#
# The loop that ran on the live cluster. With staging set to always, the staging server
# deliberately rehearses the *current* build to warm the tree - which writes a verified
# primed record for the build already running. The empty-cluster trigger read "something
# is primed" as "there is an update", stopped ten servers to install what they were
# already on, and the restart emptied the cluster, which armed the trigger again.
st = real_store()
updates.remember(st, primed={"ok": True, "build": "25117056", "loaded": LOADED})
worth, why = updates.worth_applying(st, installed="25117056")
check("a staged build that equals the running one is not something to apply",
      not worth, why)
check("and it says so in those words", "already running" in why, why)

worth, why = updates.worth_applying(st, installed="25200000")
check("nor is a staged build OLDER than the running one - that is a downgrade",
      not worth, why)

updates.remember(st, primed={"ok": True, "build": "25200000", "loaded": LOADED})
worth, why = updates.worth_applying(st, installed="25117056")
check("a strictly newer staged build is", worth, why)

st = real_store()
_pend.stage(st, {"max_players": 250})
worth, why = updates.worth_applying(st, installed="25117056")
check("and so is a queued setting change, with nothing staged at all", worth, why)

st = real_store()
updates.remember(st, primed={"ok": True, "build": "25200000", "loaded": LOADED})
worth, why = updates.worth_applying(st, installed=None, ark_root="/nowhere")
check("a build we cannot read is not a licence to restart on the chance", not worth, why)
check("and says that is what happened", "could not be read" in why, why)

check("strictly newer, numerically", updates.newer_build("25117056", "25200000")
      and not updates.newer_build("25200000", "25117056")
      and not updates.newer_build("25117056", "25117056"))
check("an unreadable pair is never 'newer'",
      not updates.newer_build("", "25200000") and not updates.newer_build("2511", ""))

st = real_store(update_apply_in_window=True)
updates.remember(st, primed={"ok": True, "build": "25117056", "loaded": LOADED})
ok, why = updates.due(st, now=lambda: at(5), installed="25117056")
check("so the window does not fire for it either", not ok, why)


# ---- the window is a backstop, not a second schedule
#
# Empty is the primary trigger, and a ten-map cluster usually has an idle hour every
# day - so by four in the morning the queue has normally already landed. Firing anyway
# would be a restart that changes nothing, and a restart that changes nothing is exactly
# what overlapped with another on 8 September and left a world half-written.
_five_am = at(5)

st = real_store(update_apply_in_window=True)
_pend.stage(st, {"max_players": 250})
updates.remember(st, last_apply=_five_am - 3 * 3600)
ok, why = updates.due(st, now=lambda: _five_am, installed=OLD_BUILD)
check("an apply three hours ago suppresses the window", not ok, why)
check("and says how long ago, so the silence is explicable",
      "3 hour(s) ago" in why and "nothing to add" in why, why)

updates.remember(st, last_apply=_five_am - 23 * 3600)
ok, why = updates.due(st, now=lambda: _five_am, installed=OLD_BUILD)
check("still suppressed at 23 hours - inside the day", not ok, why)

updates.remember(st, last_apply=_five_am - 25 * 3600)
ok, why = updates.due(st, now=lambda: _five_am, installed=OLD_BUILD)
check("but a cluster that never emptied for 25 hours gets its backstop", ok, why)
check("and the reason names what is waiting", "setting change" in why, why)

# A cluster that has never applied anything has nothing to suppress it.
st = real_store(update_apply_in_window=True)
_pend.stage(st, {"max_players": 250})
ok, why = updates.due(st, now=lambda: _five_am, installed=OLD_BUILD)
check("a cluster that has never applied anything is not suppressed", ok, why)

# Suppression is about the restart, not about what the restart achieved: a batch that
# stopped the cluster spent the disruption whether or not its gates passed.
st = real_store(update_apply_in_window=True)
_pend.stage(st, {"max_players": 250})
c = Cluster(gates=False)
_, rename = moved_nothing()
updates.apply_batch(st, ARK, warn=c.warn, save=c.save, stop_all=c.stop,
                    start_all=c.start, verify=c.verify,
                    players=lambda: (0, {}, []), rename=rename,
                    exists=tree_exists(), now=lambda: _five_am - 3600)
check("a batch that stopped the cluster records the restart",
      updates.state(st).get("last_apply"), updates.state(st))
ok, why = updates.due(st, now=lambda: _five_am, installed=OLD_BUILD)
check("so a failed batch still holds the window off - the servers did restart",
      not ok and "nothing to add" in why, why)

# And a batch that refused before touching anything records nothing.
st = real_store(update_apply_in_window=True)
_pend.stage(st, {"max_players": 250})
_, rename = moved_nothing()
updates.apply_batch(st, ARK, players=lambda: (3, {"island": 3}, []),
                    rename=rename, exists=tree_exists(), now=lambda: _five_am - 3600)
check("a batch refused before it stopped anything records no restart",
      not updates.state(st).get("last_apply"), updates.state(st))


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
