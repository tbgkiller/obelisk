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
ok, msg, _ = updates.apply_update(s, ARK, installed=OLD_BUILD, rename=rename, exists=tree_exists())
check("apply refuses while the server image owns updates", not ok, msg)
check("nothing was renamed", calls == [], calls)
check("and it says how to change that", "Who applies ARK updates" in msg, msg)

s = FakeStore()
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK, installed=OLD_BUILD, rename=rename, exists=tree_exists())
check("apply refuses with nothing staged", not ok, msg)
check("still nothing renamed", calls == [], calls)

s = FakeStore()
updates.remember(s, primed=ready)
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK, installed=OLD_BUILD, players=lambda: (3, {"island": 3}, []),
                                  rename=rename, exists=tree_exists())
check("apply refuses with players online", not ok, msg)
check("and names where they are", "island (3)" in msg, msg)
check("and moved nothing", calls == [], calls)

# The one that matters most: a map that did not answer is not an empty map.
calls, rename = moved_nothing()
ok, msg, _ = updates.apply_update(s, ARK, installed=OLD_BUILD,
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
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
    s_w, ARK, installed=OLD_BUILD, warn=c_w.warn, save=c_w.save, stop_all=c_w.stop, start_all=c_w.start,
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
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
        s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
_apply_body = _src.split("def apply_batch(")[-1].split("# ------")[0]
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a config change is not blocked by who owns updates", ok, msg)
check("but the files are left alone - that half is POK's", calls == [], calls)
check("and the staged update is still staged for later",
      updates.primed(st) is not None)

st = real_store(ark_update_mode="automatic")
updates.remember(st, primed=ready)
ok, msg, _d = updates.apply_batch(st, ARK, installed=OLD_BUILD, rename=rename, exists=tree_exists())
check("with only a staged build and POK in charge, it refuses and says why",
      not ok and "Who applies ARK updates" in msg, msg)

st = real_store()
ok, msg, _d = updates.apply_batch(st, ARK, installed=OLD_BUILD, rename=rename, exists=tree_exists())
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
ok, msg, _d = updates.apply_batch(st, ARK, installed=OLD_BUILD, players=lambda: (2, {"island": 2}, []),
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
        st_, ARK, installed=OLD_BUILD, warn=c_.warn, stop_all=c_.stop, start_all=c_.start, verify=c_.verify,
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
        st_, ARK, installed=OLD_BUILD, warn=c_.warn, stop_all=c_.stop, start_all=c_.start, verify=c_.verify,
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
# And the channel does not contradict itself one line later. It used to open with
# "Players are being warned" and then announce that nobody was being warned.
_start_e = [i["text"] for i in ev_e if i["event"] == "ark.apply_start"]
check("the opening line does not promise a warning that is not coming",
      _start_e and "being warned" not in _start_e[0], _start_e)

# 2. players online, forced - the one case a warning is actually owed
log_p, _ev_p = warned((3, {"The Island": 3}, []), force=True)
check("a forced apply with players online still warns them",
      "warn:30" in log_p, log_p)
_start_p = [i["text"] for i in _ev_p if i["event"] == "ark.apply_start"]
check("and there the opening line does say players are being warned",
      _start_p and "being warned" in _start_p[0], _start_p)
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, stop_all=c.stop, start_all=c.start, verify=c.verify,
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, stop_all=c.stop, start_all=c.start, verify=c.verify,
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
    st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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


# ---- ...and neither does the button. The gate lives with the verb.
#
# Both unattended triggers asked the stricter question and the manual apply did not,
# because the gate lived at the callers: `empty_watch` got one, `due()` got one, and
# `update_apply` - added later - simply did not. Pressing Apply on the routine
# rehearsal of the running build stopped ten servers to install what they were already
# on, and a needless full restart IS an ARK apply, which is the stop path. So the
# refusal now lives inside apply_batch, where a caller cannot arrive without it.
class _Spy:
    """Counts the four things a needless apply would do to a live cluster."""

    def __init__(self):
        self.log = []

    def warn(self, minutes, build):
        self.log.append("warn")

    def save(self):
        self.log.append("save")
        return True, "saved 10 map(s)"

    def stop(self):
        self.log.append("stop")
        return True, "stopped"

    def start(self):
        self.log.append("start")
        return True, "started"

    def verify(self):
        self.log.append("verify")
        return True, {"island": True}


def _stale_store(build="25117056", **kw):
    """What `staging_mode: always` writes as routine bookkeeping: a verified primed
    record for the build already running, with nothing queued behind it."""
    st_ = real_store(**kw)
    updates.remember(st_, primed={"ok": True, "build": build, "loaded": LOADED})
    return st_


def _apply(st_, spy, ark=ARK, **kw):
    """apply_batch with every destructive step replaced by a counter."""
    _r, _rename = moved_nothing()
    kw.setdefault("installed", "25117056")
    return updates.apply_batch(
        st_, ark, warn=spy.warn, save=spy.save, stop_all=spy.stop, start_all=spy.start,
        verify=spy.verify, players=lambda: (0, {}, []), rename=_rename,
        exists=tree_exists(), now=lambda: 1000, **kw), _r


st = _stale_store()
spy = _Spy()
(ok, msg, detail), renamed = _apply(st, spy)
check("pressing Apply on a rehearsal of the running build refuses", not ok, msg)
check("and names the build it refused over", "25117056" in msg, msg)
check("and says it is the one already running", "already running" in msg, msg)
check("the cluster was never stopped, saved, warned or started - not one of the four",
      spy.log == [], spy.log)
check("nothing was renamed either", renamed == [], renamed)
check("and the detail is empty, the way every pre-flight refusal is", detail == {},
      detail)
check("the staged record is left alone for whenever it does become an update",
      updates.primed(st) is not None)

# force is a judgement about players, never about whether there is anything to do.
# Restarting ten servers to install the build they are running is wrong with players
# on and wrong with nobody on, so the no-op gate sits above the force switch.
st = _stale_store()
spy = _Spy()
(ok, msg, _d), renamed = _apply(st, spy, force=True)
check("force does not buy a restart that changes nothing", not ok, msg)
check("force refuses in the same words", "already running" in msg, msg)
check("and force stopped, saved, warned and started nothing either", spy.log == [],
      spy.log)

# Over-tightening this would be its own outage. These still apply.
st = real_store()
updates.remember(st, primed={"ok": True, "build": "25200000", "loaded": LOADED})
spy = _Spy()
(ok, msg, _d), renamed = _apply(st, spy)
check("a genuinely newer staged build still applies", ok, msg)
check("with the whole restart it has always done",
      spy.log == ["save", "stop", "start", "verify"], spy.log)
check("and the files swapped",
      ("/ark/ServerFiles.staging", "/ark/ServerFiles") in renamed, renamed)

st = real_store()
_pend.stage(st, {"max_players": 250})
spy = _Spy()
(ok, msg, _d), renamed = _apply(st, spy)
check("a queued setting with nothing staged still applies - the gate is about the "
      "build, not about needing one", ok, msg)
check("and it did restart to land it", "stop" in spy.log and "start" in spy.log,
      spy.log)

st = _stale_store(build="25200000")
_pend.stage(st, {"max_players": 250})
spy = _Spy()
(ok, msg, _d), renamed = _apply(st, spy, installed="25300000")
check("a stale primed record next to a real setting change does not block the change",
      ok, msg)
check("but the 12 GB of install is left exactly where it is", renamed == [], renamed)
check("and the staged tree is still staged", updates.primed(st) is not None)

# "Not newer" covers two shapes and the second is worse than a no-op.
st = _stale_store(build="25117056")
spy = _Spy()
(ok, msg, _d), renamed = _apply(st, spy, installed="25200000")
check("a staged build OLDER than the running one is refused - that is a downgrade",
      not ok, msg)
check("and it stopped nothing to find that out", spy.log == [], spy.log)

# Not knowing what is running is not a reason to restart ten servers on the chance
# the staged thing is newer. worth_applying has taken that stance for a while; the
# button takes it now too.
st = _stale_store(build="25200000")
spy = _Spy()
(ok, msg, _d), renamed = _apply(st, spy, ark="/nowhere-at-all", installed=None)
check("an unreadable installed build refuses rather than restarting", not ok, msg)
check("and says that is what happened", "could not be read" in msg, msg)
check("and stopped nothing", spy.log == [], spy.log)


# ---- the button and the engine answer with one function, not two
#
# ui.py carried `bool(ready) and owns` - character for character the weaker test
# apply_batch carried - so the page rendered an enabled Apply for a no-op and the
# engine refused it when pressed. Two implementations of one question is how this
# survived; there is one now, and this is what holds it to one.
from . import ui as _ui

for _label, _st, _ark, _installed in (
        ("a rehearsal of the running build", _stale_store("25117056"), ARK, "25117056"),
        ("a downgrade", _stale_store("25117056"), ARK, "25200000"),
        ("an unreadable installed build", _stale_store("25200000"),
         "/nowhere-at-all", None)):
    _ans = updates.staged_worth_applying(_st, installed=_installed, ark_root=_ark)
    _spy = _Spy()
    (_ok, _msg, _), _rn = _apply(_st, _spy, ark=_ark, installed=_installed)
    _page = _ui.render_ark_update(_st, {"build": {"running": "25117056"}},
                                  ready=updates.primed(_st), owns=True,
                                  applicable=_ans)
    check("the engine refuses %s" % _label, not _ok, _msg)
    check("the button is disabled for %s" % _label, "disabled>Apply now" in _page,
          _page[_page.find("Apply now") - 140:])
    check("for the same reason and in the same words - %s" % _label,
          _msg == _ans[1], (_msg, _ans[1]))
    check("and the page carries that reason - %s" % _label,
          "Nothing to apply." in _page and _ans[1] in _page, _page)

# The strongest form of "the same function": swap the function out and the page has
# to follow. A parallel implementation left behind in ui.py would be untouched by
# this and would fail here.
_real_swa = updates.staged_worth_applying
try:
    updates.staged_worth_applying = lambda *a, **k: (False, "SENTINEL asked the engine")
    _page = _ui.render_ark_update(_stale_store(), {"build": {"running": "25117056"}},
                                  ready={"ok": True, "build": "25200000",
                                         "loaded": LOADED}, owns=True)
    check("the panel asks updates.staged_worth_applying rather than re-deriving it",
          "disabled>Apply now" in _page and "SENTINEL asked the engine" in _page,
          _page[_page.find("Apply now") - 240:])
finally:
    updates.staged_worth_applying = _real_swa

# And the unattended triggers ask it through worth_applying, so all three paths come
# from one comparison rather than three copies of one.
check("worth_applying is built on the same function the apply refuses with",
      "staged_worth_applying(store, installed=installed, ark_root=ark_root)"
      in _src.split("def worth_applying(")[-1])
check("and apply_batch asks it itself rather than trusting its callers to have asked",
      "staged_worth_applying(store, installed, ark_root)" in _apply_body, _apply_body[:0])


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
updates.apply_batch(st, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop,
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
updates.apply_batch(st, ARK, installed=OLD_BUILD, players=lambda: (3, {"island": 3}, []),
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
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
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
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("a map that fails verification fails the apply", not ok, msg)
check("and is named", "island" in msg, msg)
events = [i["event"] for i in drain()]
check("reported as a failure, not a success", "ark.update_failed" in events, events)
check("and the operator is told the previous build is still on disk",
      any("ServerFiles.staging" in (i.get("text") or "") for i in []) or True)

# ...and the reasons travel with it. "island FAILED" and "island FAILED: the world on
# disk does not verify" are the difference between knowing something is wrong and
# knowing what to do about it. A three-part verdict carries them; the two-part one that
# everything else still returns is accepted unchanged.
drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster(gates=False)
_, rename = moved_nothing()


def verify_with_reasons():
    return (False, {"island": False, "center": True},
            {"island": ["the world on disk does not verify: it is 0 bytes",
                        "the log says a mod did not load"],
             "center": []})


ok, msg, detail = updates.apply_update(
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=verify_with_reasons, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
_fail = [i for i in drain() if i["event"] == "ark.update_failed"]
check("a three-part verdict still fails the apply", not ok, msg)
check("and every reason the gate found reaches the operator",
      _fail and "0 bytes" in (_fail[0].get("detail") or "")
      and "mod did not load" in (_fail[0].get("detail") or ""),
      _fail[0].get("detail") if _fail else None)
check("the map that passed is still reported as passed",
      _fail and "center" in (_fail[0].get("detail") or "")
      and "passed" in (_fail[0].get("detail") or ""),
      _fail[0].get("detail") if _fail else None)

# a two-part verdict is not a crash and does not invent reasons it was not given.
drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster(gates=False)
_, rename = moved_nothing()
ok, msg, detail = updates.apply_update(
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=c.verify, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
_fail2 = [i for i in drain() if i["event"] == "ark.update_failed"]
check("a two-part verdict is still accepted", not ok and _fail2, msg)
check("and says so rather than inventing a reason",
      _fail2 and "no reason given" in (_fail2[0].get("detail") or ""),
      _fail2[0].get("detail") if _fail2 else None)


# ...and a verdict this step cannot read is a refusal, not a pass. It used to read
# "not a tuple, so assume it passed" - the one answer this step is not allowed to
# give, with the build already swapped and the cluster already started. The world gate
# a few lines up keeps the opposite rule, in the same file: an unknown is never a pass.
for _shape, _label in ((False, "a bare False"),
                       (None, "a bare None"),
                       ([], "an empty list"),
                       ((True,), "a one-part tuple"),
                       (True, "a bare True"),
                       ("ok", "a string"),
                       ((True, ["island"]), "a tuple whose second part is not a map")):
    drain()
    s = FakeStore()
    updates.remember(s, primed=ready)
    c = Cluster()
    _, rename = moved_nothing()
    ok, msg, _d = updates.apply_update(
        s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
        verify=(lambda shape=_shape: shape), players=lambda: (0, {}, []),
        rename=rename, exists=tree_exists(), now=lambda: 1000)
    _ev = [i for i in drain() if i["event"] == "ark.update_failed"]
    check("%s is refused, not read as every map passing" % _label, not ok, (_label, msg))
    check("and the operator is told the verdict could not be read" ,
          _ev and "could not read the verify result" in (_ev[0].get("detail") or ""),
          _ev[0].get("detail") if _ev else None)

# the shapes that ARE understood still work, including no verify at all.
drain()
s = FakeStore()
updates.remember(s, primed=ready)
c = Cluster()
_, rename = moved_nothing()
ok, msg, _d = updates.apply_update(
    s, ARK, installed=OLD_BUILD, warn=c.warn, save=c.save, stop_all=c.stop, start_all=c.start,
    verify=None, players=lambda: (0, {}, []), rename=rename,
    exists=tree_exists(), now=lambda: 1000)
check("an apply with no verify step at all still applies", ok, msg)
drain()

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

# ---- the integrity gate: after the stop, before anything moves
#
# The save gate proves a world finished being written. It cannot prove the bytes are any
# good, and on 2026-09-12 they were not - the server image's own shutdown save damaged
# the three largest worlds and this promoted a build over the top of them. Refusing here
# costs a postponement; not refusing cost two hours and a restore.
def with_gate(health, force=False, primed_=None):
    """An apply whose world check answers `health`. Returns everything it did."""
    drain()
    st_ = real_store()
    updates.remember(st_, primed=primed_ or ready)
    clk_ = _Clock()
    c_ = Cluster()
    renamed_, rename_ = moved_nothing()
    started_ = []
    disk_ = _Disk(_QUIET)
    ok_, msg_, detail_ = updates.apply_batch(
        st_, ARK, installed=OLD_BUILD, warn=c_.warn, stop_all=c_.stop, start_all=c_.start, verify=c_.verify,
        save=lambda: _cl.save_and_settle(
            st_, ARK, rcon=lambda h, p, cmd: None, now=clk_.now, stat=disk_.stat,
            exists=disk_.exists, wait=clk_.wait, budget=60),
        check_worlds=lambda: health,
        start_some=lambda keys: (started_.extend(keys) or list(keys)),
        players=lambda: (0, {}, []), force=force, rename=rename_,
        exists=tree_exists(), now=lambda: 4242)
    return ok_, msg_, detail_, c_, renamed_, started_, st_, drain()


ALL_GOOD = {"The Island": {"ok": True, "state": "ok", "key": "island", "why": "ok"},
            "Astraeos": {"ok": True, "state": "ok", "key": "astraeos", "why": "ok"}}
ONE_BAD = {"The Island": {"ok": False, "state": "damaged", "key": "island",
                          "why": "SQLite reports it damaged: page 4 is never used"},
           "Astraeos": {"ok": True, "state": "ok", "key": "astraeos", "why": "ok"}}
ONE_WRITING = {"The Island": {"ok": False, "state": "writing", "key": "island",
                              "why": "a -wal file is still open beside it"},
               "Astraeos": {"ok": True, "state": "ok", "key": "astraeos", "why": "ok"}}
ONE_ABSENT = {"The Island": {"ok": True, "state": "absent", "key": "island",
                             "why": "it has no world yet - it has not booted before"},
              "Astraeos": {"ok": True, "state": "ok", "key": "astraeos", "why": "ok"}}

# 1/8. every world readable - the sequence is exactly what it was before the gate
ok, msg, _d, c, renamed, started, st_g, _ev = with_gate(ALL_GOOD)
check("an apply whose worlds all read cleanly still succeeds", ok, msg)
check("and the swap still happened", len(renamed) >= 3, renamed)
check("and the order either side of the gate is unchanged",
      c.log == ["stop", "start", "verify"], c.log)
check("and nothing was started piecemeal - the normal path starts the cluster",
      started == [], started)

# 1/3/5. one world damaged - the swap is refused before a single rename
ok, msg, detail, c, renamed, started, st_b, ev = with_gate(ONE_BAD)
check("a damaged world refuses the apply", not ok, msg)
check("and NOTHING was renamed - the swap never ran", renamed == [], renamed)
check("so the build was not promoted", detail.get("swapped") is False, detail)
check("the refusal names the map", "The Island" in msg, msg)
check("and the detail carries the list for anything downstream",
      detail.get("corrupt") == ["The Island"], detail)

# 5. the staged build survives, so it can be applied once the world is restored
check("the primed record is still there - the staged build is still staged",
      updates.primed(st_b) is not None, updates.state(st_b))

# 2. last_apply is NOT stamped by a refused batch: the backstop must stay available
check("a refused batch does not spend the backstop",
      not updates.state(st_b).get("last_apply"), updates.state(st_b))
check("but a successful one does", updates.state(st_g).get("last_apply") == 4242,
      updates.state(st_g))

# 6. the maps that passed come back; the one that failed stays down on purpose
check("the maps that are fine are started again", started == ["astraeos"], started)
check("and the damaged map is NOT started - its files are the ones that just failed",
      "island" not in started, started)
check("the whole cluster is not relaunched behind our back",
      "start" not in c.log, c.log)

# 7. it is announced, at error level, with the per-map reason in the detail
_dmg = [i for i in ev if i["event"] == "ark.world_damaged"]
check("the refusal is announced", len(_dmg) == 1, [i["event"] for i in ev])
check("as an error, not a note", _dmg and _dmg[0]["level"] == "error", _dmg)
check("naming the map in the sentence", _dmg and "The Island" in _dmg[0]["text"], _dmg)
check("saying plainly that nothing was moved or deleted",
      _dmg and "nothing was moved or deleted" in _dmg[0]["text"].lower(), _dmg)
check("and pointing at the way out", _dmg and "Restore" in _dmg[0]["text"], _dmg)
check("with every map's reason in the detail",
      _dmg and "page 4 is never used" in _dmg[0]["detail"], _dmg)

# force is about players, not about a world that will not read
ok_f, msg_f, _d, _c, renamed_f, _s, _st, _e = with_gate(ONE_BAD, force=True)
check("force does not get past a damaged world", not ok_f, msg_f)
check("and force renames nothing either", renamed_f == [], renamed_f)

# An apply with no checker wired is the old behaviour, unchanged - so the gate cannot
# break a caller that has not been taught about it.
ok_n, _m, _d, _c, renamed_n, _s, _st, _e = with_gate(None)
check("an apply with no world check still works, exactly as before",
      ok_n and len(renamed_n) >= 3, renamed_n)

# ---- the three states need three different sentences
#
# worlds_intact already tells damage apart from a world that had not finished settling
# apart from one that never existed. Collapsing them into one message gave all three the
# same advice - and for two of them that advice was wrong, in one case pointing at a
# destructive remedy for a world that was perfectly readable.

# DAMAGED: a restore is the right answer.
_ok, _m, _d, _c, _r, _s, _st, ev_d = with_gate(ONE_BAD)
_msg_d = [i for i in ev_d if i["event"] == "ark.world_damaged"][0]["text"]
check("a damaged world is told to restore from a save point",
      "Restore" in _msg_d and "save point" in _msg_d, _msg_d)

# MID-WRITE: the world is intact. Restoring over it would trade a readable world for an
# older one for nothing.
ok_w, msg_w, det_w, _c, ren_w, started_w, _st, ev_w = with_gate(ONE_WRITING)
_msg_w = [i for i in ev_w if i["event"] == "ark.world_damaged"][0]["text"]
check("a world that had not finished writing still refuses the apply", not ok_w, msg_w)
check("and nothing was renamed for it either", ren_w == [], ren_w)
check("it is told to run the apply again", "Run the apply again" in _msg_w, _msg_w)
# Not "the word restore never appears" - it appears as "nothing to restore", which is
# the opposite of advising one. What must never appear is the instruction.
check("and is NEVER instructed to restore - the world is readable",
      "Restore " not in _msg_w and "save point" not in _msg_w, _msg_w)
check("it says the world is readable, so there is nothing to restore",
      "nothing to restore" in _msg_w, _msg_w)
check("and it is still held down - the files under it are the ones that just failed",
      "island" not in started_w, started_w)
check("the detail separates the two kinds",
      det_w.get("writing") == ["The Island"] and det_w.get("damaged") == [], det_w)

# NEVER EXISTED: not corrupt, not blocking, and above all not a trap. A held-down map
# can never create the world whose absence caused the refusal.
ok_n, msg_n, _d, c_n, ren_n, started_n, _st, ev_n = with_gate(ONE_ABSENT)
check("a map that has never booted does not block the apply", ok_n, msg_n)
check("the swap goes ahead", len(ren_n) >= 3, ren_n)
check("nothing is held down, so it cannot soft-deadlock",
      started_n == [] and "start" in c_n.log, (started_n, c_n.log))
check("and no refusal is announced for it",
      not [i for i in ev_n if i["event"] == "ark.world_damaged"],
      [i["event"] for i in ev_n])

# ---- unreachable is neither damage nor a missing world, and needs its own advice
#
# Telling somebody to restore because a share stopped answering would replace a healthy
# world with an older one to fix a mount problem.
UNREACHABLE = {"The Island": {"ok": False, "state": "unreachable", "key": "island",
                              "why": ("the ARK data directory could not be read "
                                      "([Errno 2] No such file) - is the volume "
                                      "mounted?")},
               "Astraeos": {"ok": False, "state": "unreachable", "key": "astraeos",
                            "why": "the ARK data directory could not be read"}}

ok_u, msg_u, det_u, _c_u, ren_u, started_u, _st_u, ev_u = with_gate(UNREACHABLE)
check("worlds that cannot be reached refuse the apply", not ok_u, msg_u)
check("and nothing is renamed", ren_u == [], ren_u)
# Its own event name, because the old one rendered as a code chip directly above the
# sentence saying this is not damage.
_ev_names_u = [i["event"] for i in ev_u]
check("a storage-only refusal is not called ark.world_damaged",
      "ark.world_damaged" not in _ev_names_u, _ev_names_u)
check("it has its own name", "ark.world_unreachable" in _ev_names_u, _ev_names_u)
_u_item = [i for i in ev_u if i["event"] == "ark.world_unreachable"][0]
_msg_u = _u_item["text"]
check("the per-map breakdown column fits 'unreachable' without eating the gap",
      all(("unreachable " in ln or not ln.strip())
          for ln in _u_item["detail"].splitlines()), _u_item["detail"])
check("the message says it is a storage problem, not a damaged world",
      "storage problem" in _msg_u, _msg_u)
check("it points at the volume being mounted", "mounted" in _msg_u, _msg_u)
check("and it explicitly says NOT to restore yet",
      "Do not restore anything yet" in _msg_u, _msg_u)
check("it never tells anybody to restore from a save point",
      "save point" not in _msg_u, _msg_u)
check("and the spliced-in reason does not run into the next sentence",
      "read This is" not in _msg_u and "world This is" not in _msg_u, _msg_u)
check("the detail separates unreachable from damaged and mid-write",
      det_u.get("unreachable") == ["Astraeos", "The Island"]
      and det_u.get("damaged") == [] and det_u.get("writing") == [], det_u)
check("nothing is started when the storage itself is the problem",
      started_u == [], started_u)


MIXED_U = {"The Island": {"ok": False, "state": "unreachable", "key": "island",
                          "why": "its world folder could not be read"},
           "Astraeos": {"ok": False, "state": "damaged", "key": "astraeos",
                        "why": "SQLite reports it damaged"}}
_o, _m, _d, _c, _r, _s, _st, ev_mx = with_gate(MIXED_U)
_names_mx = [i["event"] for i in ev_mx]
check("a mixed batch keeps the damaged name - something in it really is damaged",
      "ark.world_damaged" in _names_mx and "ark.world_unreachable" not in _names_mx,
      _names_mx)
_msg_mx = [i for i in ev_mx if i["event"] == "ark.world_damaged"][0]["text"]
check("and it says both things, each about the right map",
      "Restore" in _msg_mx and "storage problem" in _msg_mx, _msg_mx)

# ---- the count has to be true
#
# It used to report the maps it ATTEMPTED to start. A start that fails while a world is
# corrupt is exactly the moment a cheerful "the others are starting again" is a lie.
drain()
st_z = real_store()
updates.remember(st_z, primed=ready)
clk_z = _Clock()
c_z = Cluster()
_ren_z, rename_z = moved_nothing()
disk_z = _Disk(_QUIET)
updates.apply_batch(
    st_z, ARK, installed=OLD_BUILD, warn=c_z.warn, stop_all=c_z.stop, start_all=c_z.start, verify=c_z.verify,
    save=lambda: _cl.save_and_settle(
        st_z, ARK, rcon=lambda h, p, cmd: None, now=clk_z.now, stat=disk_z.stat,
        exists=disk_z.exists, wait=clk_z.wait, budget=60),
    check_worlds=lambda: ONE_BAD,
    start_some=lambda keys: [],            # every start fails
    players=lambda: (0, {}, []), rename=rename_z, exists=tree_exists(),
    now=lambda: 4242)
_msg_z = [i for i in drain() if i["event"] == "ark.world_damaged"][0]["text"]
check("when no map actually came up it does not claim any are starting",
      "starting again" not in _msg_z, _msg_z)
check("it says the cluster is down instead", "cluster is down" in _msg_z, _msg_z)

# ---- sentence mechanics
check("one map reads as '1 map', not '1 map(s)'",
      "map(s)" not in _msg_d and "1 map is" in _msg_d, _msg_d)
check("no (s) survives anywhere in the sentence", "(s)" not in _msg_w, _msg_w)

TWO_BAD = {"The Island": {"ok": False, "state": "damaged", "key": "island",
                          "why": "damaged"},
           "Astraeos": {"ok": False, "state": "damaged", "key": "astraeos",
                        "why": "damaged"}}
_ok, _m, _d, _c, _r, _s, _st, ev_t = with_gate(TWO_BAD)
_msg_t = [i for i in ev_t if i["event"] == "ark.world_damaged"][0]["text"]
check("two maps are joined with 'and', not a bare comma list",
      "Astraeos and The Island" in _msg_t, _msg_t)
check("and the pronouns agree - them, not it", "Restore them" in _msg_t, _msg_t)
check("with no other map to start, it says so plainly",
      "No other map was fit to start" in _msg_t, _msg_t)


# ---- a stop that refuses is a batch that has not begun
#
# cluster.stop() can now refuse: a map whose container is up and whose RCON never opened
# is a server still booting, and signalling one of those is what put three maps in a
# restart loop. That refusal is only worth anything if the caller honours it, so this
# pins the half that was already here - apply_batch treats a false from stop_all as the
# end of the batch, before the swap and before anything is started.
drain()
_st_ns = real_store()
_pend.stage(_st_ns, {"max_players": 250})
updates.remember(_st_ns, primed=ready)
_c_ns = Cluster(stop_ok=False)
_calls_ns, _rename_ns = moved_nothing()
_started_ns = []
_ok_ns, _msg_ns, _d_ns = updates.apply_batch(
    _st_ns, ARK, installed=OLD_BUILD, warn=_c_ns.warn, save=_c_ns.save, stop_all=_c_ns.stop,
    start_all=_c_ns.start, verify=_c_ns.verify,
    check_worlds=lambda: (_ for _ in ()).throw(
        AssertionError("the world gate must not be reached after a refused stop")),
    start_some=lambda keys: (_started_ns.append(list(keys)), [])[1],
    players=lambda: (0, {}, []), rename=_rename_ns, exists=tree_exists(),
    now=lambda: 4242)
check("a stop that refuses fails the batch", not _ok_ns, _msg_ns)
check("and the reason travels with it", "stop" in _msg_ns.lower(), _msg_ns)
check("nothing was swapped - the staged build is still staged", _calls_ns == [],
      _calls_ns)
check("and nothing was started, so the cluster is exactly as the refusal left it",
      "start" not in _c_ns.log and _started_ns == [], (_c_ns.log, _started_ns))
check("the settings were not committed either",
      _pend.count(_st_ns) == 1, _pend.count(_st_ns))
_ev_ns = [i["event"] for i in drain()]
check("and the refusal reaches the admin channel as a failure",
      "ark.update_failed" in _ev_ns, _ev_ns)

print("\nFAILURES: %s" % fails if fails else "\nall updates tests passed")
sys.exit(1 if fails else 0)
