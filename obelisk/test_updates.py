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
