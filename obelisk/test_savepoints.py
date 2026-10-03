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

import io
import os
import sqlite3
import sys
import tempfile
import time

from . import savepoints
from .settings import Store

fails = []



def _in_order(seq, *needles):
    """True when every needle is in `seq`, in this order.

    .index raises when a needle is missing, so an assertion built on it reports a crash
    instead of a failure - and a crash names no check and stops the suite. This asks the
    ordering question in a way that can answer "no".
    """
    seq = list(seq)
    at = [seq.index(n) if n in seq else -1 for n in needles]
    return all(i >= 0 for i in at) and at == sorted(at)


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

# ---- the Genesis failure, reproduced
#
# A restore point was refused on a healthy world with "SQLite will not read it (attempt
# to write a readonly database)". Genesis had stopped answering RCON, so its save could
# not be verified and the container went down hard - and a hard-killed SQLite leaves a
# hot journal beside its database. The swap then put a *different* world at that path
# and left the old journal next to it. Opening it meant replaying that journal first,
# which needs a write, which read-only refuses.
#
# The refusal was the system working. The danger was one step further on: had the check
# passed, the server would have started on a world with a foreign journal beside it.
def hot_journal(world_path):
    """What a server that was killed mid-write leaves behind."""
    with open(world_path + "-journal", "wb") as fh:
        # SQLite's rollback-journal magic, enough for it to be treated as hot.
        fh.write(b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7" + b"\x00" * 64)
    return world_path + "-journal"


root4, folder4 = ark_root(["Ragnarok_WP_07.09.2026_19.02.33.ark"])
live4 = os.path.join(folder4, "Ragnarok_WP.ark")
journal = hot_journal(live4)
check("a world with a hot journal beside it is the failing shape",
      os.path.isfile(journal))

# The old check - read-only but not immutable - is what produced the Genesis message.
import sqlite3 as _sq

try:
    _con = _sq.connect("file:%s?mode=ro" % live4, uri=True)
    _con.execute("PRAGMA integrity_check;").fetchone()
    _con.close()
    _old_failed = False
except Exception as _e:
    _old_failed = "readonly database" in str(_e)
check("mode=ro alone fails on it, exactly as it did on Genesis", _old_failed,
      "the reproduction did not reproduce")

ok, why = savepoints.verify_point(live4)
check("and immutable=1 reads it fine - the fix", ok, why)

# And the swap must take the journal away, not leave it beside a world it does not
# describe.
st5, root5, folder5 = fresh()
live5 = os.path.join(folder5, "Ragnarok_WP.ark")
hot_journal(live5)
c = Cluster()
ok, msg, detail = savepoints.restore_point(
    st5, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark",
    stop=c.stop, start=c.start, verify=c.verify, players=lambda: (0, {}, []),
    ark_root=root5, now=lambda: NOW)
check("a restore over a hot journal now succeeds", ok, msg)
check("and the stale journal is gone, not left beside the new world",
      not os.path.exists(live5 + "-journal"),
      os.listdir(folder5))
check("the operator is told it was there", any("stale journal" in s
                                               for s in detail["steps"]), detail["steps"])

for _suffix in ("-wal", "-shm", "-journal"):
    st6, root6, folder6 = fresh()
    live6 = os.path.join(folder6, "Ragnarok_WP.ark")
    open(live6 + _suffix, "wb").close()
    savepoints.restore_point(
        st6, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark",
        stop=Cluster().stop, start=Cluster().start, players=lambda: (0, {}, []),
        ark_root=root6, now=lambda: NOW)
    check("%s is cleared too" % _suffix, not os.path.exists(live6 + _suffix))


# ---- the world is handed to the server's user
st7, root7, folder7 = fresh()
_owned = []
_real_chown = getattr(os, "chown", None)
try:
    os.chown = lambda p, u, g: _owned.append((os.path.basename(p), u, g))
    savepoints.restore_point(
        st7, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark",
        stop=Cluster().stop, start=Cluster().start, players=lambda: (0, {}, []),
        ark_root=root7, now=lambda: NOW)
finally:
    if _real_chown is None:
        delattr(os, "chown")
    else:
        os.chown = _real_chown
check("the swapped world is given to the server's user",
      ("Ragnarok_WP.ark", 7777, 7777) in _owned, _owned)


# ---- the pre-stop save is best effort
st8, root8, _f8 = fresh()
c = Cluster()
_saves = []
ok, msg, detail = savepoints.restore_point(
    st8, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark",
    stop=c.stop, start=c.start, verify=c.verify, players=lambda: (0, {}, []),
    save=lambda k: (_saves.append(k), (True, "saved"))[1],
    ark_root=root8, now=lambda: NOW)
check("the map is asked to save before it is stopped", _saves == ["ragnarok"], _saves)
check("and that happens before the stop",
      _in_order(detail["steps"], "saved", "stopped ragnarok"), detail["steps"])

st9, root9, _f9 = fresh()
c = Cluster()
ok, msg, detail = savepoints.restore_point(
    st9, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark",
    stop=c.stop, start=c.start, verify=c.verify, players=lambda: (0, {}, []),
    save=lambda k: (False, "not answering RCON"),
    ark_root=root9, now=lambda: NOW)
check("a map that will not answer RCON does not block the restore", ok, msg)
check("it is recorded rather than swallowed",
      any("carrying on" in s for s in detail["steps"]), detail["steps"])
check("because the world was already copied aside as a file, not over RCON",
      any("copied aside" in s for s in detail["steps"]), detail["steps"])


# ---- worlds only, and the module says so
src = open(savepoints.__file__, encoding="utf-8").read()
for word in ("arkprofile", "profilebak", "arktribe", "tribebak"):
    check("profiles and tribes are never touched (%s)" % word, word not in src, word)
check("and the warning text says what that means for players",
      "NOT" in savepoints.WARNING and "disappears from the world" in savepoints.WARNING,
      savepoints.WARNING)


# ---- one name for the map, all the way through
#
# N7 converted the two refusals to the friendly name and left six other
# operator-visible messages on the raw folder id, so the same flow said "The Island"
# and then "TheIsland_WP" two clicks apart - while the archive restore beside it said
# The Island throughout. The id is still what paths and the log use; it is not what a
# sentence uses.
import inspect as _insp_sp                                       # noqa: E402
import ast as _ast_sp                                            # noqa: E402

_sp_src = _insp_sp.getsource(savepoints.restore_point)
_sp_tree = _ast_sp.parse(_sp_src)

# Every string this function hands back as a message, found rather than listed, so a
# seventh one added later is covered without anybody remembering to add it here.
_returned = []
for _n in _ast_sp.walk(_sp_tree):
    if not (isinstance(_n, _ast_sp.Return) and isinstance(_n.value, _ast_sp.Tuple)):
        continue
    if len(_n.value.elts) < 2:
        continue
    _returned.append(_ast_sp.dump(_n.value.elts[1]))

check("every message restore_point returns was found", len(_returned) >= 8,
      len(_returned))
_id_msgs = [d for d in _returned if "'map_id'" in d or '"map_id"' in d]
check("none of them names the map by its folder id", _id_msgs == [],
      [d[:120] for d in _id_msgs])

# ...and the id is still doing the jobs it should
check("the log line still carries the id, which is what a log is for",
      'log.info("restore point %s: %s", map_id, text)' in _sp_src, "log lost the id")
check("the pre-point backup is still named by id, because it is a path",
      'pre-point-%s-%s.ark" % (map_id, stamp)' in _sp_src, "path lost the id")
check("and the detail dict still carries both",
      '"map_id": map_id' in _sp_src, "detail lost the id")
check("the map's name is worked out once, not per message",
      _sp_src.count('mapcat.entry(store, map_key)') == 1,
      _sp_src.count('mapcat.entry(store, map_key)'))
# And the id it builds every path from is asked of the store, not of the built-in list:
# a cluster can run a map Obelisk does not ship, and this module is where that map's
# saves are found or not found.
_sp_mod = io.open(os.path.join(os.path.dirname(__file__), "savepoints.py"),
                  encoding="utf-8").read()
check("and the level name every path is built from comes from this cluster's catalogue",
      "def _map_id(store, map_key):" in _sp_mod
      and "mapcat.BY_KEY" not in _sp_mod, _sp_mod.count("mapcat.BY_KEY"))
check("and it does not shadow the save point's own name",
      "map_name" in _sp_src and "def restore_point(store, map_key, name," in _sp_src,
      "the point's name was shadowed")

# the messages themselves, end to end
st_n, root_n, _ = fresh()
c = Cluster()
ok_n, msg_n, _d_n = savepoints.restore_point(
    st_n, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    players=lambda: (2, {"ragnarok": 2}, []), ark_root=root_n)
check("a refusal names the map", "Ragnarok" in msg_n and "Ragnarok_WP" not in msg_n,
      msg_n)

st_n, root_n, _ = fresh()
c = Cluster(stop_ok=False)
ok_s2, msg_s2, _d = savepoints.restore_point(
    st_n, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    players=lambda: (0, {}, []), ark_root=root_n)
check("a map that will not stop is named, not id'd",
      not ok_s2 and "Ragnarok_WP" not in msg_s2 and "Ragnarok" in msg_s2, msg_s2)

st_n, root_n, _ = fresh()
c = Cluster()
ok_g2, msg_g2, _d = savepoints.restore_point(
    st_n, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    verify=lambda k: (False, ["RCON is not answering"]),
    players=lambda: (0, {}, []), ark_root=root_n)
check("a map that fails its gates is named too",
      not ok_g2 and "Ragnarok_WP" not in msg_g2 and "Ragnarok" in msg_g2, msg_g2)

st_n, root_n, _ = fresh()
c = Cluster()
ok_m, msg_m, _d = savepoints.restore_point(
    st_n, "ragnarok", "no-such-point.ark", stop=c.stop, start=c.start,
    players=lambda: (0, {}, []), ark_root=root_n)
check("a point that aged out names the map, not the folder",
      not ok_m and "Ragnarok_WP" not in msg_m, msg_m)
check("while still naming the point that is gone", "no-such-point.ark" in msg_m, msg_m)

# ---- the success banner says what it kept
st_k, root_k, _ = fresh()
c = Cluster()
ok_k, msg_k, det_k = savepoints.restore_point(
    st_k, "ragnarok", "Ragnarok_WP_07.09.2026_19.02.33.ark", stop=c.stop, start=c.start,
    verify=c.verify, players=lambda: (0, {}, []), ark_root=root_k)
check("the rollback succeeds", ok_k, msg_k)
check("the success line names the map", msg_k.startswith("Ragnarok is back"), msg_k)
check("not the folder", "Ragnarok_WP is back" not in msg_k, msg_k)
check("it says which save it went back to",
      "is back on its save from" in msg_k, msg_k)
check("a world was copied aside before the swap", det_k.get("kept"), det_k)
check("and the banner names it, so there is a way back from the rollback",
      os.path.basename(det_k["kept"]) in msg_k, [msg_k, det_k.get("kept")])
check("saying it is kept until somebody removes it, like the archive flow does",
      "until you remove it" in msg_k, msg_k)
check("and naming it as a pre-point copy rather than a mystery file",
      "pre-point-" in msg_k, msg_k)


# ---- the automatic restore: newest point that opens, and only over real damage
#
# 1 October: two maps crashed mid-save, their live worlds were malformed SQLite, and they
# sat held down beside twenty good points each. What follows pins the narrow licence the
# automatic path has - SQLite says damaged, nothing else - and that the point it picks is
# the newest one that actually opens, not merely the newest.
def damage(path):
    """A file SQLite will not read as a database: the shape a crash mid-save leaves."""
    with open(path, "wb") as fh:
        fh.write(b"SQLite format 3\x00" + b"\xde\xad" * 4000)
    return path


_NEW = "Ragnarok_WP_01.10.2026_07.30.00.ark"     # damaged by the same crash
_MID = "Ragnarok_WP_01.10.2026_06.47.00.ark"     # good
_OLD = "Ragnarok_WP_01.10.2026_05.00.00.ark"     # good, older


def auto_fresh():
    root, folder = ark_root([_MID, _OLD])
    damage(os.path.join(folder, _NEW))
    st = store()
    st.patch({"appdata": "/mnt/data/ark"}, source="install")
    return st, root, folder


st_a, root_a, folder_a = auto_fresh()
pt, rej = savepoints.newest_good_point(st_a, "ragnarok", ark_root=root_a)
check("the newest good point skips a newer one that does not open",
      pt and pt["name"] == _MID, (pt, rej))
check("and says which one it skipped, and why",
      [n for n, _w in rej] == [_NEW] and rej[0][1], rej)

_seen = []
pt_i, rej_i = savepoints.newest_good_point(
    st_a, "ragnarok", ark_root=root_a,
    verify=lambda p: (_seen.append(os.path.basename(p)) or False, "injected no"))
check("with nothing that opens it returns no point at all", pt_i is None, pt_i)
check("having tried every one of them, newest first",
      _seen == [_NEW, _MID, _OLD], _seen)

# live_state: only "damaged" is a licence
_live_a = os.path.join(folder_a, "Ragnarok_WP.ark")
check("an intact live world reads ok", savepoints.live_state(_live_a)[0] == "ok",
      savepoints.live_state(_live_a))
damage(_live_a)
check("a malformed live world reads damaged",
      savepoints.live_state(_live_a)[0] == "damaged", savepoints.live_state(_live_a))
open(_live_a + "-journal", "wb").close()
check("but one with a journal beside it is still being written, not damaged - the "
      "journal may be what makes it whole", savepoints.live_state(_live_a)[0] == "writing",
      savepoints.live_state(_live_a))
os.remove(_live_a + "-journal")
check("a world folder that will not list is unreachable, never damaged",
      savepoints.live_state(_live_a, listdir=lambda _p: (_ for _ in ()).throw(
          PermissionError("denied")))[0] == "unreachable")
check("a world that is not there is absent",
      savepoints.live_state(os.path.join(folder_a, "Nope.ark"))[0] == "absent")
_lnk = os.path.join(folder_a, "Linked.ark")
os.symlink(os.path.join(folder_a, "gone-elsewhere.ark"), _lnk)
check("a link is unreachable - copying onto it would write through it",
      savepoints.live_state(_lnk)[0] == "unreachable", savepoints.live_state(_lnk))


class _Restores:
    """Records what auto_restore asked restore_point to do, without doing it."""
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, store, key, name, **kw):
        self.calls.append((key, name, kw))
        return (self.ok, "Ragnarok is back on its save from x." if self.ok
                else "the restored world does not verify: nope",
                {"steps": ["stopped ragnarok"], "kept": "/b/pre-point-x.ark"})


# refuses when the live world verifies now - somebody already fixed it
st_a, root_a, folder_a = auto_fresh()
r = _Restores()
ok_v, msg_v, det_v = savepoints.auto_restore(st_a, "ragnarok", restore=r,
                                             ark_root=root_a)
check("auto restore does nothing when the live world verifies now", not ok_v
      and r.calls == [] and det_v.get("refused") == "intact", (msg_v, det_v))
check("and says it may already have been restored", "already" in msg_v, msg_v)

# refuses when the live world cannot be reached or is missing
for _state in ("unreachable", "absent", "writing"):
    r = _Restores()
    ok_u, msg_u, det_u = savepoints.auto_restore(
        st_a, "ragnarok", restore=r, ark_root=root_a,
        live_ok=lambda _p, _s=_state: (_s, "injected %s" % _s))
    check("auto restore never restores over a world that is %s" % _state,
          not ok_u and r.calls == [] and det_u.get("refused") == _state, (msg_u, det_u))
check("and says only damage is restored automatically",
      "Only a world SQLite itself reports damaged" in msg_u, msg_u)

# restores the newest good point otherwise, forced, and never restarting on failure
damage(os.path.join(folder_a, "Ragnarok_WP.ark"))
_pt_mid = [p for p in savepoints.list_points(st_a, "ragnarok", ark_root=root_a)
           if p["name"] == _MID][0]
r = _Restores()
ok_d, msg_d, det_d = savepoints.auto_restore(
    st_a, "ragnarok", restore=r, ark_root=root_a,
    mtime=lambda _p: _pt_mid["when"] + 54 * 60, stop="STOP", start="START")
check("a damaged live world is restored from the newest point that opens",
      ok_d and [c[1] for c in r.calls] == [_MID], (msg_d, r.calls))
check("forced past the player check - the caller established the map is down",
      r.calls and r.calls[0][2].get("force") is True, r.calls)
check("and told never to restart the map onto the damaged world if it fails",
      r.calls and r.calls[0][2].get("restart_on_failure") is False, r.calls)
check("with the stop and start wiring handed straight through",
      r.calls and r.calls[0][2].get("stop") == "STOP"
      and r.calls[0][2].get("start") == "START", r.calls)
check("the message names the point", _pt_mid["local"] in msg_d, msg_d)
check("and how much world was rolled back, from the live world's own mtime",
      "rolling the world back about 54 minutes" in msg_d
      and det_d.get("rolled_back") == 54 * 60, (msg_d, det_d.get("rolled_back")))
check("and the newer point it skipped", "1 newer save point would not open" in msg_d,
      msg_d)
check("and where the damaged world is kept", "pre-point-x.ark" in msg_d, msg_d)
check("and that characters are not rolled back", "not rolled back" in msg_d, msg_d)

r = _Restores(ok=False)
ok_f, msg_f, det_f = savepoints.auto_restore(st_a, "ragnarok", restore=r,
                                             ark_root=root_a)
check("a restore that fails is a failure, with restore_point's reason",
      not ok_f and "does not verify" in msg_f and not det_f.get("refused"), msg_f)

ok_n, msg_n, det_n = savepoints.auto_restore(
    st_a, "ragnarok", restore=_Restores(), ark_root=root_a,
    pick=lambda *_a, **_k: (None, [(_NEW, "malformed")]))
check("with no point that opens it fails and says so",
      not ok_n and "none of its 1 save point would open" in msg_n, msg_n)

# end to end, through the real restore_point: the damaged world is swapped and kept
st_e, root_e, folder_e = auto_fresh()
_live_e = os.path.join(folder_e, "Ragnarok_WP.ark")
damage(_live_e)
c = Cluster()
ok_e, msg_e, det_e = savepoints.auto_restore(
    st_e, "ragnarok", ark_root=root_e, stop=c.stop, start=c.start, verify=c.verify)
check("end to end the damaged world is replaced by the newest good point", ok_e
      and savepoints.verify_point(_live_e)[0]
      and os.path.getsize(_live_e) == os.path.getsize(os.path.join(folder_e, _MID)),
      (msg_e, det_e))
check("and the damaged one is kept aside, not lost",
      det_e.get("kept") and os.path.isfile(det_e["kept"]), det_e.get("kept"))
check("only that map was stopped and started",
      c.log == ["stop:ragnarok", "start:ragnarok", "verify:ragnarok"], c.log)

# restart_on_failure=False: a held map is never started on the world that failed
_real_give = savepoints.restore.give_world_to_server


def _refuse_give(_path):
    raise OSError("injected: the copy did not land")


for _restart, _want in ((True, True), (False, False)):
    st_r, root_r, folder_r = auto_fresh()
    c = Cluster()
    savepoints.restore.give_world_to_server = _refuse_give
    try:
        ok_r, msg_r, _d_r = savepoints.restore_point(
            st_r, "ragnarok", _MID, stop=c.stop, start=c.start, force=True,
            ark_root=root_r, restart_on_failure=_restart)
    finally:
        savepoints.restore.give_world_to_server = _real_give
    check("a swap that fails %s the map again when restart_on_failure is %s"
          % ("starts" if _want else "does NOT start", _restart),
          not ok_r and (("start:ragnarok" in c.log) == _want), (c.log, msg_r))
check("and says it is still stopped", "still stopped" in msg_r, msg_r)


print("\nFAILURES: %s" % fails if fails else "\nall savepoints tests passed")
sys.exit(1 if fails else 0)
