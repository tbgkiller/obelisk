"""
Rolling a map back to a save the game already took.

Three things carry this file. The timestamp in the filename is UTC and the operator is
not - read it as local time and every restore point on this cluster is offered five
hours from when it happened, which is the sort of wrong that is only noticed after
somebody restores the wrong one.

The listing is read from disk every time, because the game prunes these on its own
schedule; anything that remembered them would confidently offer a point that no longer
exists, and the operator would find out by clicking it.

And the swap is reversible. The world about to be replaced is copied first, and a
restored world that will not open puts the previous one back before the map restarts -
the whole point of a restore point is that it is not a one-way door.
"""

import os
import sqlite3
import sys
import tempfile
import time

from . import savepoints
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def store():
    st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
    st.patch({"admin_password": "pw", "maps": "island,ragnarok"})
    return st


def world(path, rows=1):
    """A file that looks enough like an ARK save to pass the same checks one does."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE game (k TEXT, v BLOB)")
    for i in range(max(1, rows)):
        con.execute("INSERT INTO game VALUES (?, ?)", ("k%d" % i, b"x" * 2048))
    con.commit()
    con.close()
    return path


def ark_root(points=(), live=True, map_id="Ragnarok_WP"):
    """A data root holding one map's SavedArks folder."""
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "shared", "SavedArks", map_id)
    os.makedirs(folder, exist_ok=True)
    if live:
        world(os.path.join(folder, "%s.ark" % map_id), rows=3)
    for name in points:
        world(os.path.join(folder, name))
    return root, folder


# ---- the UTC trap
#
# The game writes the stamp in UTC. Ragnarok_WP_06.09.2026_04.47.30.ark was written at
# 23:47 the previous evening in Chicago. Reading it as local time would offer every
# point on this cluster five hours from when it happened.
STAMP = {"Y": "2026", "m": "09", "d": "06", "H": "04", "M": "47", "S": "30"}
when = savepoints._utc_to_local(STAMP)
check("a stamp is read as UTC",
      time.strftime("%Y-%m-%d %H:%M", time.gmtime(when)) == "2026-09-06 04:47",
      time.strftime("%Y-%m-%d %H:%M", time.gmtime(when)))
check("and not as local time - that is the five-hour error",
      when == __import__("calendar").timegm((2026, 9, 6, 4, 47, 30, 0, 0, 0)))


# ---- listing, read from disk every time
NAMES = ["Ragnarok_WP_07.09.2026_16.47.33.ark",
         "Ragnarok_WP_07.09.2026_19.02.33.ark",
         "Ragnarok_WP_06.09.2026_04.47.30.ark"]
root, folder = ark_root(NAMES)
st = store()
NOW = savepoints._utc_to_local({"Y": "2026", "m": "09", "d": "07",
                                "H": "21", "M": "02", "S": "33"})

points = savepoints.list_points(st, "ragnarok", ark_root=root, now=lambda: NOW)
check("every dated save is found", len(points) == 3, [p["name"] for p in points])
check("newest first, because that is what anybody wants",
      points[0]["name"] == "Ragnarok_WP_07.09.2026_19.02.33.ark",
      [p["name"] for p in points])
check("the live world is not offered as a restore point",
      all(".ark" in p["name"] and "_" in p["name"].replace("Ragnarok_WP", "")
          for p in points) and
      "Ragnarok_WP.ark" not in [p["name"] for p in points],
      [p["name"] for p in points])
check("each carries its age", points[0]["age"] == 2 * 3600, points[0]["age"])
check("in words", points[0]["ago"] == "2h ago", points[0]["ago"])
check("and its size", points[0]["size"] > 1024 and points[0]["human_size"],
      points[0]["human_size"])
check("and a path that exists", os.path.isfile(points[0]["path"]))

check("a map that has never run lists nothing rather than failing",
      savepoints.list_points(st, "island", ark_root=root) == [])

# Another map's saves in the same folder must not be offered for this one.
world(os.path.join(folder, "TheIsland_WP_07.09.2026_10.00.00.ark"))
points = savepoints.list_points(st, "ragnarok", ark_root=root, now=lambda: NOW)
check("a different map's save is not offered for this map",
      all(p["name"].startswith("Ragnarok_WP_") for p in points),
      [p["name"] for p in points])

# And anything that is not a dated save is not a restore point.
open(os.path.join(folder, "notes.txt"), "w").close()
open(os.path.join(folder, "Ragnarok_WP_backup.ark"), "w").close()
points = savepoints.list_points(st, "ragnarok", ark_root=root, now=lambda: NOW)
check("a file that is not a dated save is ignored", len(points) == 3,
      [p["name"] for p in points])

check("ages read the way a person says them",
      savepoints.human_age(30) == "just now"
      and savepoints.human_age(45 * 60) == "45m ago"
      and savepoints.human_age(2 * 3600) == "2h ago"
      and savepoints.human_age(3 * 86400) == "3d ago",
      [savepoints.human_age(n) for n in (30, 2700, 7200, 259200)])


# ---- a point has to actually be a world
root2, folder2 = ark_root(["Ragnarok_WP_07.09.2026_19.02.33.ark"])
good = os.path.join(folder2, "Ragnarok_WP_07.09.2026_19.02.33.ark")
check("a real save verifies", savepoints.verify_point(good)[0])

junk = os.path.join(folder2, "Ragnarok_WP_07.09.2026_20.00.00.ark")
with open(junk, "wb") as fh:
    fh.write(b"not a database" * 500)
ok, why = savepoints.verify_point(junk)
check("a file that is not a database does not", not ok, why)
check("and says so rather than being restored", "SQLite" in why, why)


# ---- the restore itself
class Cluster:
    def __init__(self, start_ok=True, stop_ok=True, gates=True):
        self.log, self.start_ok, self.stop_ok, self.gates = [], start_ok, stop_ok, gates

    def stop(self, key):
        self.log.append("stop:%s" % key)
        return self.stop_ok, "stopped" if self.stop_ok else "docker said no"

    def start(self, key):
        self.log.append("start:%s" % key)
        return self.start_ok, "started" if self.start_ok else "docker said no"

    def verify(self, key):
        self.log.append("verify:%s" % key)
        return self.gates, [] if self.gates else ["mods did not load"]


def fresh(**kw):
    root, folder = ark_root(["Ragnarok_WP_07.09.2026_19.02.33.ark"])
    st = store()
    st.patch({"appdata": "/mnt/data/ark"}, source="install")
    return st, root, folder


st2, root3, folder3 = fresh()
c = Cluster()
ok, msg, detail = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark",
    stop=c.stop, start=c.start, verify=c.verify,
    players=lambda: (0, {}, []), ark_root=root3, now=lambda: NOW)
check("a restore point applies", ok, msg)
check("only that map was stopped and started",
      c.log == ["stop:ragnarok", "start:ragnarok", "verify:ragnarok"], c.log)
check("the live world is now the restored one",
      os.path.getsize(os.path.join(folder3, "Ragnarok_WP.ark")) ==
      os.path.getsize(os.path.join(folder3, "Ragnarok_WP_07.09.2026_19.02.33.ark")))
check("the world it replaced was copied aside first", detail.get("kept")
      and os.path.isfile(detail["kept"]), detail.get("kept"))
check("so it is reversible", savepoints.verify_point(detail["kept"])[0])
check("and the point itself is still there for another try",
      os.path.isfile(os.path.join(folder3, "Ragnarok_WP_07.09.2026_19.02.33.ark")))
# Deliberately not "19:02": that is the UTC in the filename, and the message is meant to
# be local. Asserting the UTC here would have passed only in London and would have been
# testing the bug rather than the fix.
_expect = [p for p in savepoints.list_points(st2, "ragnarok", ark_root=root3)
           if p["name"] == "Ragnarok_WP_07.09.2026_19.02.33.ark"]
check("the message says when it went back to, in local time",
      _expect and _expect[0]["local"] in msg, (msg, _expect[0]["local"] if _expect else None))
check("which is not the UTC that is in the filename",
      _expect and _expect[0]["local"] != "07 Sep 19:02" or time.timezone == 0,
      _expect[0]["local"] if _expect else None)

# ---- players on that map
st2, root3, _ = fresh()
c = Cluster()
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    players=lambda: (2, {"ragnarok": 2}, []), ark_root=root3)
check("it refuses while somebody is on that map", not ok, msg)
check("and nothing was stopped", c.log == [], c.log)
check("and says how to override", "force" in msg, msg)

st2, root3, _ = fresh()
c = Cluster()
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    verify=c.verify, players=lambda: (2, {"ragnarok": 2}, []), force=True,
    ark_root=root3)
check("force goes ahead anyway", ok, msg)

# A map that did not answer is not an empty map - the same rule the update flow keeps.
st2, root3, _ = fresh()
c = Cluster()
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    players=lambda: (0, {}, [("ragnarok", "timeout")]), ark_root=root3)
check("a map that did not answer stops the restore", not ok, msg)
check("because silence is not 'nobody is on'", "did not answer" in msg, msg)

# ---- players elsewhere do not block this map
st2, root3, _ = fresh()
c = Cluster()
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    verify=c.verify, players=lambda: (5, {"island": 5}, []), ark_root=root3)
check("somebody on another map does not block this one", ok, msg)

# ---- the failures
st2, root3, _ = fresh()
c = Cluster(stop_ok=False)
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    players=lambda: (0, {}, []), ark_root=root3)
check("a map that will not stop is not swapped under", not ok, msg)
check("and nothing was started", "start:ragnarok" not in c.log, c.log)

st2, root3, folder3 = fresh()
bad = os.path.join(folder3, "Ragnarok_WP_07.09.2026_20.00.00.ark")
with open(bad, "wb") as fh:
    fh.write(b"junk" * 400)
before = os.path.getsize(os.path.join(folder3, "Ragnarok_WP.ark"))
c = Cluster()
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_20.00.00.ark", stop=c.stop, start=c.start,
    players=lambda: (0, {}, []), ark_root=root3)
check("a damaged point is refused before anything stops", not ok, msg)
check("nothing was stopped", c.log == [], c.log)
check("and the live world is untouched",
      os.path.getsize(os.path.join(folder3, "Ragnarok_WP.ark")) == before)

st2, root3, _ = fresh()
c = Cluster(gates=False)
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    verify=c.verify, players=lambda: (0, {}, []), ark_root=root3)
check("a map that comes back but fails the gates fails the restore", not ok, msg)
check("and says which gate", "mods did not load" in msg, msg)

st2, root3, _ = fresh()
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "Ragnarok_WP_01.01.2020_00.00.00.ark",
    players=lambda: (0, {}, []), ark_root=root3)
check("a point that has aged out says so rather than failing obscurely", not ok, msg)
check("and mentions that the game prunes them", "prunes" in msg, msg)

# ---- a name cannot reach outside the map's own folder
st2, root3, folder3 = fresh()
outside = os.path.join(root3, "Ragnarok_WP_07.09.2026_19.02.33.ark")
world(outside)
ok, msg, _d = savepoints.restore_point(
    st2, "ragnarok", "../Ragnarok_WP_07.09.2026_19.02.33.ark",
    players=lambda: (0, {}, []), ark_root=root3)
check("a path in the name is resolved against the listing, not joined on",
      ok or "no restore point" in msg, msg)
found = savepoints.find_point(st2, "ragnarok", "../../etc/passwd", ark_root=root3)
check("and something outside entirely is simply not a point", found is None, found)

# ---- worlds only, and the module says so
src = open(savepoints.__file__, encoding="utf-8").read()
for word in ("arkprofile", "profilebak", "arktribe", "tribebak"):
    check("profiles and tribes are never touched (%s)" % word, word not in src, word)
check("and the warning text says what that means for players",
      "NOT" in savepoints.WARNING and "disappears from the world" in savepoints.WARNING,
      savepoints.WARNING)

print("\nFAILURES: %s" % fails if fails else "\nall savepoints tests passed")
sys.exit(1 if fails else 0)
