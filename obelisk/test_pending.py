"""
Changes that wait, and the rollback that has to put them back.

Two properties carry this file. The first is that a queued change is never a live one:
compose.py and plan.py read the store, so a pending value written into the store is an
applied value the moment anything regenerates the stack - a restore, a crash recovery, a
launch for some unrelated reason. The overlay exists so that cannot happen by accident,
and the tests below try to make it happen.

The second is the rollback, which is the genuinely new risk. Applying is two writes that
have to succeed or fail together: the files move, and the settings change. The rename
half is already proved reversible. This half was not, and a batch that fails after
writing settings but before starting would otherwise leave a cluster whose configuration
describes a build it is not running.
"""

import os
import sys
import tempfile

from . import pending
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def store(**kw):
    st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
    base = {"admin_password": "pw", "maps": "island,astraeos", "max_players": 70,
            "mem_limit": "20g", "mod_ids": "929110"}
    base.update(kw)
    st.patch(base)
    return st


# ---- what waits and what does not
check("a setting that restarts the maps waits", pending.stageable("max_players"))
check("the mod list waits", pending.stageable("mod_ids"))
check("who-applies waits - which is what lets it be set with a player online",
      pending.stageable("ark_update_mode"))
check("the staging server's own settings do not wait",
      not pending.stageable("staging_memory"))
check("and neither does a live-editable one",
      not pending.stageable("ItemStackSizeMultiplier"))

st = store()
now, later = pending.split({"max_players": 250, "ItemStackSizeMultiplier": 10.0,
                            "staging_memory": "12g"}, st)
check("live settings are applied at once", sorted(now) ==
      ["ItemStackSizeMultiplier", "staging_memory"], sorted(now))
check("and the disruptive one waits", list(later) == ["max_players"], later)

now, later = pending.split({"max_players": 70}, st)
check("setting something to what it already is queues nothing", later == {}, later)
check("because a queue that counts no-ops lies about how much is waiting", True)


# ---- the queue is an overlay, never a write
#
# The single most important property here. If a pending value reached the live store,
# the next thing to regenerate a compose file would apply it - and that thing might be a
# restore putting a world back, carrying a config change nobody meant to make just then.
st = store()
pending.stage(st, {"max_players": 250, "mod_ids": "929110,940003"})
check("the live value is untouched", st.get("max_players") == 70, st.get("max_players"))
check("and the other one too", st.get("mod_ids") == "929110", st.get("mod_ids"))
check("but the change is remembered", pending.count(st) == 2, pending.count(st))
check("nothing pending appears in the cluster values",
      "250" not in str(st.data.get("cluster")), st.data.get("cluster"))

st.save()
again = Store(st.path).load()
check("and it survives a restart", pending.count(again) == 2, pending.count(again))
check("with the live values still live", again.get("max_players") == 70)

# ---- editing the same setting again replaces, it does not stack
st = store()
pending.stage(st, {"max_players": 200})
pending.stage(st, {"max_players": 250})
check("a second edit replaces the first", pending.count(st) == 1, pending.rows(st))
check("with the newer value", pending.queued(st)["cluster"]["max_players"] == 250)

pending.stage(st, {"max_players": 70})
check("setting it back to the running value clears it from the queue",
      pending.count(st) == 0, pending.rows(st))

# ---- per-map
st = store()
pending.stage(st, {"mem_limit": "36g"}, map_name="astraeos")
check("a per-map change is queued against that map",
      pending.queued(st)["maps"]["astraeos"]["mem_limit"] == "36g", pending.queued(st))
check("and the map still runs on the old value",
      st.get("mem_limit", map_name="astraeos") == "20g")
row = [r for r in pending.rows(st) if r["map"] == "astraeos"][0]
check("the row says which map", row["map"] == "astraeos", row)
check("and what it is changing from and to",
      row["from"] == "20g" and row["to"] == "36g", row)

# ---- discard
st = store()
pending.stage(st, {"max_players": 250, "mod_ids": "929110,940003"})
pending.stage(st, {"mem_limit": "36g"}, map_name="astraeos")
check("three changes are queued", pending.count(st) == 3, pending.rows(st))
check("one can be dropped", pending.discard(st, "max_players") == 1)
check("leaving the others", pending.count(st) == 2, pending.rows(st))
check("a per-map one can be dropped by map",
      pending.discard(st, "mem_limit", map_name="astraeos") == 1)
check("and the rest can go at once", pending.discard(st) == 1 and
      pending.count(st) == 0, pending.rows(st))
check("discarding nothing is not an error", pending.discard(st, "max_players") == 0)

# ---- passwords are queued without being displayed
st = store()
pending.stage(st, {"admin_password": "a-brand-new-password"})
row = pending.rows(st)[0]
check("a password change is shown as pending", row["key"] == "admin_password")
check("but the value never is",
      "a-brand-new-password" not in str(row), row)
check("and neither is the old one", row["from"] == "(unchanged)", row)
check("it still applies, though", pending.queued(st)["cluster"]["admin_password"]
      == "a-brand-new-password")

# ---- the loud ones say why they are not ordinary
st = store()
pending.stage(st, {"cluster_id": "somethingelse"})
row = pending.rows(st)[0]
check("renaming the cluster carries a warning", row["warning"], row)
check("that says what it actually does", "compose project" in row["warning"], row)
st2 = store()
pending.stage(st2, {"admin_password": "x-new-password-here"})
check("so does the admin password, about the relay",
      "relay" in pending.rows(st2)[0]["warning"], pending.rows(st2)[0])
st3 = store()
pending.stage(st3, {"max_players": 250})
check("an ordinary change carries none", pending.rows(st3)[0]["warning"] == "")


# ---- committing
st = store()
pending.stage(st, {"max_players": 250})
pending.stage(st, {"mem_limit": "36g"}, map_name="astraeos")
ok, why, before = pending.commit(st)
check("committing succeeds", ok, why)
check("the value is now live", st.get("max_players") == 250)
check("and the per-map override too", st.get("mem_limit", map_name="astraeos") == "36g")
check("the queue is empty afterwards", pending.count(st) == 0)
check("and nothing is left in the store under the pending key",
      "pending" not in st.data, sorted(st.data))

st = store()
pending.stage(st, {"max_players": 250})
ok, why, _ = pending.commit(st, validate=lambda k, v: (False, "nope"))
check("a value that will not validate is refused", not ok, why)
check("and says which setting", "max_players" in why, why)
check("nothing was written", st.get("max_players") == 70, st.get("max_players"))
check("and the change is still queued, not lost", pending.count(st) == 1)


# ---- rollback: the risky half
#
# A batch stops ten servers, moves 12 GB and writes settings. If it fails after the
# settings are written, the cluster has to come back on the *old* configuration - a
# store describing a build that is not running is how a map comes up with the wrong
# ports, or the wrong mods, at four in the morning.
st = store()
pending.stage(st, {"max_players": 250, "mod_ids": "929110,940003"})
pending.stage(st, {"mem_limit": "36g"}, map_name="astraeos")
queue_was = pending.queued(st)
ok, _why, before = pending.commit(st)
check("committed", ok and st.get("max_players") == 250)

ok, why = pending.restore(st, before, requeue=queue_was)
check("the rollback runs", ok, why)
check("the cluster value is back", st.get("max_players") == 70, st.get("max_players"))
check("and the mod list", st.get("mod_ids") == "929110", st.get("mod_ids"))
check("and the per-map override", st.get("mem_limit", map_name="astraeos") == "20g")
check("the changes are back in the queue rather than silently dropped",
      pending.count(st) == 3, pending.rows(st))
check("so the operator can see they did not happen", True)

# The one that is easy to get wrong: a per-map key that had NO override before.
# Restoring the inherited value would write a new override that never existed, quietly
# pinning a map to a value it was only ever inheriting.
st = store()
check("astraeos has no memory override to begin with",
      "mem_limit" not in st.data.get("maps", {}).get("astraeos", {}),
      st.data.get("maps"))
pending.stage(st, {"mem_limit": "36g"}, map_name="astraeos")
ok, _why, before = pending.commit(st)
check("committing creates the override", st.data["maps"]["astraeos"]["mem_limit"] == "36g")
pending.restore(st, before)
check("rolling back removes it rather than pinning the inherited value",
      "mem_limit" not in st.data.get("maps", {}).get("astraeos", {}),
      st.data.get("maps"))
check("so the map goes back to inheriting", st.get("mem_limit", map_name="astraeos")
      == "20g")
check("and the empty map entry is tidied away",
      "astraeos" not in st.data.get("maps", {}), st.data.get("maps"))

# A cluster key that was never set either - same trap, other half.
st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
st.patch({"admin_password": "pw", "maps": "island"})
check("passive_mods is not set", "passive_mods" not in st.data["cluster"])
pending.stage(st, {"passive_mods": "12345"})
ok, _why, before = pending.commit(st)
check("committing sets it", st.data["cluster"]["passive_mods"] == "12345")
pending.restore(st, before)
check("rolling back removes it rather than writing the default as a real value",
      "passive_mods" not in st.data["cluster"], st.data["cluster"])

# Restoring must survive being handed a snapshot for keys that have since vanished.
st = store()
ok, why = pending.restore(st, {"cluster": {}, "maps": {"nope": {}}})
check("an empty snapshot is not an error", ok, why)


# ---- the summary a person reads
st = store()
check("nothing waiting says so", "no changes" in pending.summary(st))
pending.stage(st, {"max_players": 250})
check("one change is singular", "1 change waiting" in pending.summary(st),
      pending.summary(st))
pending.stage(st, {"mod_ids": "1234"})
check("two are plural", "2 changes waiting" in pending.summary(st), pending.summary(st))


# ---- drift: the cluster that stopped matching its own settings
#
# The safety net for what this replaces. Recreate-class settings used to go into the
# store and wait for whatever Launch came next, so a cluster upgrading to the queue may
# already be running a compose file its settings no longer describe - and the queue
# cannot know, because those changes happened before it existed.
st = store()
differs, why = pending.drift(st, read=lambda p: "services:\n  a: {}\n",
                             generate=lambda: "services:\n  a: {}\n")
check("a cluster that matches its settings reports no drift", not differs, why)

differs, why = pending.drift(st, read=lambda p: "services:\n  a: {}\n",
                             generate=lambda: "services:\n  a: {}\n  b: {}\n")
check("one that does not, does", differs, why)
check("and says what to do about it", "apply" in why.lower(), why)

check("trailing whitespace is not drift",
      not pending.drift(st, read=lambda p: "services:  \n  a: {}\n",
                        generate=lambda: "services:\n  a: {}")[0])


def _missing(path):
    raise OSError("no such file")


differs, why = pending.drift(st, read=_missing, generate=lambda: "anything")
check("a cluster that has never been launched has nothing to have drifted from",
      not differs and why == "", (differs, why))


def _broken():
    raise ValueError("this cluster will not boot")


differs, why = pending.drift(st, read=lambda p: "x", generate=_broken)
check("a plan that will not generate is not reported as drift - a different alarm",
      not differs and "could not work out" in why, why)

print("\nFAILURES: %s" % fails if fails else "\nall pending tests passed")
sys.exit(1 if fails else 0)
