"""
Putting a world back without losing the one that was there.

Every failure this guards against destroys data, so the shape of the suite is: do the
thing, then prove the *old* world is still on disk. A restore that cannot be undone by
hand five minutes later is not a restore, it is a replacement.

The archives here are real ones, written by backup.create() rather than hand-rolled, so
what is being restored is what Obelisk actually produces. The worlds are real SQLite
databases for the same reason: the symlink trap and the truncated-file trap both pass a
"does the path exist" check and fail an "will SQLite open it" one.
"""

import os, shutil, sqlite3, sys, tempfile

from . import backup, layout, restore
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


def make_world(path, rows=40, marker="original"):
    """A real .ark: SQLite, a `game` table, and something we can tell apart later.

    Replaced rather than appended to - writing a second marker into an existing file
    left the first one still sitting in row 1, so "the world has moved on" quietly
    produced a world that had not.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS game (key TEXT, value BLOB)")
    con.executemany("INSERT INTO game VALUES (?,?)",
                    [("%s-%d" % (marker, i), b"x" * 64) for i in range(rows)])
    con.commit()
    con.close()


def world_marker(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT key FROM game LIMIT 1").fetchone()[0].split("-")[0]
    finally:
        con.close()


def fresh(maps="island,ragnarok"):
    base = tempfile.mkdtemp()
    root = os.path.join(base, "obelisk")
    ark = os.path.join(base, "ark")
    os.makedirs(root, exist_ok=True)
    os.environ["OBELISK_ARK"] = ark
    layout.ensure_ark(ark, ["island", "ragnarok"])
    st = Store(os.path.join(root, "settings.json")).load()
    st.patch({"status_port": 8088}, source="install")
    st.patch({"maps": maps, "admin_password": "synthetic-pw", "cluster_id": "restoretest",
              "mod_ids": "929110,940003"})
    st.data["cluster"]["appdata"] = "/mnt/user/appdata/ark"
    st.save()
    return st, ark


def worlds(ark, marker="original"):
    make_world(os.path.join(ark, "shared", "SavedArks", "TheIsland_WP",
                            "TheIsland_WP.ark"), marker=marker)
    make_world(os.path.join(ark, "shared", "SavedArks", "Ragnarok_WP",
                            "Ragnarok_WP.ark"), marker=marker)


# ---------------------------------------------------------------- a real archive
st, ark = fresh()
worlds(ark, "backedup")
ok, msg, archive = backup.create(st)
check("the fixture archive is a real one from backup.create()", ok, msg)

info = restore.inspect(archive)
check("inspect reads the maps out of it",
      set(info["maps"]) == {"TheIsland_WP", "Ragnarok_WP"}, info["maps"])
check("and the cluster id", info["cluster_id"] == "restoretest", info["cluster_id"])
check("and the mod list, order intact", info["mod_ids"] == "929110,940003", info["mod_ids"])
check("and when it was taken", bool(info["created"]), info["created"])
check("and that it carries secrets, so the operator knows", info["has_secrets"])
check("a size is reported", info["bytes"] > 0)

bad = os.path.join(tempfile.mkdtemp(), "notanarchive.tar.gz")
open(bad, "wb").write(b"this is not a tarball")
info_bad = restore.inspect(bad)
check("a corrupt archive is reported, not raised",
      not info_bad["ok"] and "does not open" in info_bad["problem"], info_bad)

# ---------------------------------------------------------------- the diff
st2, _ark2 = fresh(maps="island,ragnarok")
st2.patch({"cluster_id": "different", "mod_ids": "111222"})
notes = restore.compare(st2, info)
check("a differing cluster id is called out",
      any("cluster id differs" in n for n in notes), notes)
check("and says why it matters", any("transfer" in n for n in notes), notes)
check("a differing mod list is called out",
      any("mod list differs" in n for n in notes), notes)
check("an identical cluster gets no complaints", restore.compare(st, info) == [],
      restore.compare(st, info))

# ---------------------------------------------------------------- verify_world
d = tempfile.mkdtemp()
good = os.path.join(d, "good.ark")
make_world(good)
ok_v, why = restore.verify_world(good)
check("a real world verifies", ok_v, why)
check("and says what it found", "rows" in why and "integrity ok" in why, why)

check("a missing file is refused", not restore.verify_world(os.path.join(d, "nope"))[0])

tiny = os.path.join(d, "tiny.ark")
open(tiny, "wb").write(b"x" * 10)
ok_t, why_t = restore.verify_world(tiny)
check("a truncated file is refused", not ok_t, why_t)
check("and is described as not a world", "not a world" in why_t, why_t)

junk = os.path.join(d, "junk.ark")
open(junk, "wb").write(b"J" * 5000)
ok_j, why_j = restore.verify_world(junk)
check("a file of the right size that is not SQLite is refused", not ok_j, why_j)

empty = os.path.join(d, "empty.ark")
con = sqlite3.connect(empty)
con.execute("CREATE TABLE game (key TEXT)")
con.execute("CREATE TABLE filler (b BLOB)")
con.executemany("INSERT INTO filler VALUES (?)", [(b"y" * 200,) for _ in range(20)])
con.commit(); con.close()
ok_e, why_e = restore.verify_world(empty)
check("a valid database with no world in it is refused", not ok_e, why_e)
check("and says so plainly", "holds nothing" in why_e, why_e)

# the symlink trap, which is the one that started all of this
linked = os.path.join(d, "linked.ark")
made_link = True
try:
    os.symlink("/home/pok/shared/SavedArks/TheIsland_WP/TheIsland_WP.ark", linked)
except (OSError, NotImplementedError, AttributeError):
    made_link = False
if made_link:
    ok_l, why_l = restore.verify_world(linked)
    check("a dangling symlink is refused, not mistaken for a world", not ok_l, why_l)
    # The link is dangling, so os.path.exists() says it is missing. If the existence
    # check runs first the message becomes "there is no world file" - true, and useless.
    # The useful sentence is that something IS there and points somewhere that is not.
    check("and is named as the symlink failure", "symlink" in why_l, why_l)
    check("and says where the link pointed",
          "TheIsland_WP.ark" in why_l, why_l)

    # A link to a world that really is there is still refused: the restored file has to
    # be the world, not a pointer to one, or the next backup captures the pointer.
    real = os.path.join(d, "real.ark")
    make_world(real)
    live_link = os.path.join(d, "live-link.ark")
    os.symlink(real, live_link)
    ok_l2, why_l2 = restore.verify_world(live_link)
    check("even a symlink that resolves is refused", not ok_l2, why_l2)
else:
    check("a dangling symlink is refused, not mistaken for a world", True,
          "skipped: no symlink privileges on this host")
    check("and is named as the symlink failure", True, "skipped")
    check("and says where the link pointed", True, "skipped")
    check("even a symlink that resolves is refused", True, "skipped")

# ---------------------------------------------------------------- preflight
ok_p, probs = restore.preflight(st, archive, "island")
check("preflight passes for a map that is in the archive", ok_p, probs)

ok_p2, probs2 = restore.preflight(st, archive, "center")
check("a map that is not in the archive is refused", not ok_p2, probs2)
check("and the message says what the archive does hold",
      any("TheIsland_WP" in p for p in probs2), probs2)

ok_p3, probs3 = restore.preflight(st, bad, "island")
check("a corrupt archive is refused before anything is stopped", not ok_p3, probs3)

# ---- the manifest is a fast description, not proof
#
# inspect() reads the sidecar manifest so that describing a multi-gigabyte archive does
# not mean decompressing it. But the manifest lists the maps the cluster was configured
# with, and a map that had never booted has no save to be in there. So the manifest can
# name a map the archive does not contain, and the authority has to be the archive.
st_m, ark_m = fresh()
make_world(os.path.join(ark_m, "shared", "SavedArks", "TheIsland_WP",
                        "TheIsland_WP.ark"), marker="only-island")
ok, _m, arc_m = backup.create(st_m)          # ragnarok is configured but never booted
info_m = restore.inspect(arc_m)
check("inspect used the manifest", info_m.get("from_manifest"), info_m)
check("which lists both configured maps",
      set(info_m["maps"]) == {"TheIsland_WP", "Ragnarok_WP"}, info_m["maps"])

ok_i, probs_i = restore.preflight(st_m, arc_m, "island")
check("the map that really is in there passes preflight", ok_i, probs_i)

ok_r2, probs_r = restore.preflight(st_m, arc_m, "ragnarok")
check("the map the manifest names but the archive lacks is refused", not ok_r2, probs_r)
check("and it is refused before anything is stopped - preflight opens the archive",
      any("does not verify" in p or "not in this archive" in p for p in probs_r), probs_r)

_stopped_m = []
ok_x, msg_x, _d = restore.restore_map(st_m, arc_m, "ragnarok",
                                      stop=lambda k: (_stopped_m.append(k) or (True, "")))
check("so restoring it never gets as far as stopping the map",
      not ok_x and _stopped_m == [], (msg_x, _stopped_m))

# ---------------------------------------------------------------- the happy path
st3, ark3 = fresh()
worlds(ark3, "current")                       # what is live right now
ok, _m, arc3 = backup.create(st3)             # archive holds "current"
worlds(ark3, "newer")                         # live has moved on since
live_ark = os.path.join(ark3, "shared", "SavedArks", "TheIsland_WP", "TheIsland_WP.ark")
other_ark = os.path.join(ark3, "shared", "SavedArks", "Ragnarok_WP", "Ragnarok_WP.ark")
check("the live world is the newer one before we start",
      world_marker(live_ark) == "newer")

stopped, started = [], []
ok_r, msg_r, det = restore.restore_map(
    st3, arc3, "island",
    stop=lambda k: (stopped.append(k) or (True, "stopped")),
    start=lambda k: (started.append(k) or (True, "started")),
    verify=lambda k: (True, []))
check("the restore succeeds", ok_r, msg_r)
check("only the map being restored was stopped", stopped == ["island"], stopped)
check("and it was started again", started == ["island"], started)
check("the world is now the archived one", world_marker(live_ark) == "current")
check("the other map's world was never touched",
      world_marker(other_ark) == "newer", world_marker(other_ark))

sup = det.get("superseded")
check("the replaced world is kept", sup and os.path.isdir(sup), sup)
check("and it is the world that was live a moment ago",
      world_marker(os.path.join(sup, "TheIsland_WP.ark")) == "newer")
check("a snapshot of it was taken first", os.path.isdir(det.get("snapshot", "")),
      det.get("snapshot"))
check("the restored world is a real file, not a link",
      os.path.isfile(live_ark) and not os.path.islink(live_ark))
check("the staging folder is cleaned up",
      not os.path.exists(os.path.join(ark3, restore.STAGING)))
check("the cluster id was not touched - worlds only",
      st3.get("cluster_id") == "restoretest")
check("nor the mod list", st3.get("mod_ids") == "929110,940003")
check("the steps are reported in order",
      det["steps"][0].startswith("archive verified"), det["steps"])

# what got left behind is findable
left = restore.superseded_worlds(st3)
check("superseded worlds can be listed for cleanup", len(left) == 1, left)
check("with a size, so somebody can decide", left and left[0]["bytes"] > 0, left)

# ---------------------------------------------------------------- no-op safety
before = world_marker(live_ark)
ok_n, msg_n, det_n = restore.restore_map(
    st3, arc3, "island", stop=lambda k: (True, ""), start=lambda k: (True, ""),
    verify=lambda k: (True, []))
check("restoring the same archive again still succeeds", ok_n, msg_n)
check("and the world is what it should be", world_marker(live_ark) == before)
check("nothing was destroyed - the previous copy is kept again",
      os.path.isdir(det_n.get("superseded", "")), det_n.get("superseded"))

# ---------------------------------------------------------------- failure paths
# 1. a stop that fails changes nothing at all
st4, ark4 = fresh()
worlds(ark4, "live")
ok, _m, arc4 = backup.create(st4)
worlds(ark4, "live2")
live4 = os.path.join(ark4, "shared", "SavedArks", "TheIsland_WP", "TheIsland_WP.ark")
ok_f, msg_f, _d = restore.restore_map(st4, arc4, "island",
                                      stop=lambda k: (False, "container is wedged"))
check("a map that will not stop aborts the restore", not ok_f, msg_f)
check("and says nothing was changed", "nothing was changed" in msg_f, msg_f)
check("the live world is untouched", world_marker(live4) == "live2")

# 2. an archive whose world does not verify never reaches the live folder
st5, ark5 = fresh()
worlds(ark5, "keepme")
bad_src = os.path.join(ark5, "shared", "SavedArks", "TheIsland_WP", "TheIsland_WP.ark")
open(bad_src, "wb").write(b"Z" * 4000)          # right size, not a database
ok, _m, arc5 = backup.create(st5)
worlds(ark5, "keepme")                          # live world is good again
started5 = []
ok_f2, msg_f2, _d2 = restore.restore_map(
    st5, arc5, "island", stop=lambda k: (True, ""),
    start=lambda k: (started5.append(k) or (True, "")))
check("an archive with an unreadable world is refused", not ok_f2, msg_f2)
check("and says it was not put in place", "not put in place" in msg_f2, msg_f2)
check("the live world survives", world_marker(bad_src) == "keepme")
check("and the map is started again rather than left down", started5 == ["island"])

# 3. gates that fail leave the superseded copy and say so
st6, ark6 = fresh()
worlds(ark6, "archived")
ok, _m, arc6 = backup.create(st6)
worlds(ark6, "wasthere")
ok_f3, msg_f3, det3 = restore.restore_map(
    st6, arc6, "island", stop=lambda k: (True, ""), start=lambda k: (True, ""),
    verify=lambda k: (False, ["RCON is not answering"]))
check("a restore that fails its checks is reported as a failure", not ok_f3, msg_f3)
check("the reason is passed through", "RCON is not answering" in msg_f3, msg_f3)
check("and the previous world is still on disk",
      os.path.isdir(det3.get("superseded", "")), det3.get("superseded"))
check("so it can be put back by hand",
      world_marker(os.path.join(det3["superseded"], "TheIsland_WP.ark")) == "wasthere")

# ---------------------------------------------------------------- the six gates
#
# The gap this closes: restore_run passed verify=None, so a restore started the map and
# assumed the rest. Starting a container and it serving a world are minutes apart, so
# "it started" was being reported as "it is restored".
from . import cluster as clusterctl

st7, ark7 = fresh()
worlds(ark7, "gated")
ok, _m, arc7 = backup.create(st7)
worlds(ark7, "before")

seen = []
ok_g, msg_g, det_g = restore.restore_map(
    st7, arc7, "island",
    stop=lambda k: (True, ""), start=lambda k: (True, ""),
    verify=lambda k: (seen.append(k) or (True, [])),
    on_step=lambda t: seen.append("step:%s" % t))
check("the restore with gates wired succeeds", ok_g, msg_g)
check("the verify step is actually called", "island" in seen, seen)
check("and it runs after the swap, not before",
      seen.index("island") > max(i for i, x in enumerate(seen)
                                 if x.startswith("step:swapped")), seen)
check("progress is reported step by step",
      sum(1 for x in seen if x.startswith("step:")) >= 5, seen)

waited = []
ok_w, why_w = clusterctl.wait_healthy(
    st7, "island", minutes=0.05, sleep=lambda n: waited.append(n),
    details=lambda names: {names[0]: {"state": "running", "health": "starting"}})
check("waiting for health gives up with a reason",
      not ok_w and "did not report" in why_w, why_w)
check("and it looked at least once before giving up", bool(waited), waited)

_looks = []
clusterctl.wait_healthy(st7, "island", minutes=0, sleep=lambda n: None,
                        details=lambda names: _looks.append(names) or
                        {names[0]: {"state": "running", "health": "starting"}})
check("even a zero-length wait asks the container once",
      len(_looks) == 1, _looks)

ok_w2, why_w2 = clusterctl.wait_healthy(
    st7, "island", minutes=5, sleep=lambda n: None,
    details=lambda names: {names[0]: {"state": "exited", "health": ""}})
check("a container that exits while starting is not waited on for ever",
      not ok_w2 and "exited" in why_w2, why_w2)

ok_w3, _w3 = clusterctl.wait_healthy(
    st7, "island", minutes=5, sleep=lambda n: None,
    details=lambda names: {names[0]: {"state": "running", "health": "healthy"}})
check("and a healthy map returns straight away", ok_w3)


def gates(state="running", health="healthy", answer="No Players Connected",
          logtext="-mods=929110,940003"):
    return clusterctl.verify_instance(
        st7, "island", rcon=lambda n, p: answer,
        details=lambda names: {names[0]: {"state": state, "health": health}},
        logs=lambda n: logtext)


st7.patch({"mod_ids": "929110,940003"})
ok_v, why_v = gates()
check("a healthy, answering, correctly-modded map passes all gates", ok_v, why_v)
check("a container that is not running fails", not gates(state="exited")[0])
check("a container that is not healthy fails", not gates(health="starting")[0])
check("RCON not answering fails", not gates(answer="ERROR timed out")[0])
ok_m, why_m = gates(logtext="-mods=940003,929110")
check("mods in the wrong ORDER fail, not just missing ones", not ok_m, why_m)
check("and the reason names the order", any("mods differ" in r for r in why_m), why_m)
ok_mm, why_mm = gates(logtext="-mods=929110,940003 Warning: missing mod 929110")
check("a missing-mod line in the log fails", not ok_mm, why_mm)

bad_world = os.path.join(ark7, "shared", "SavedArks", "TheIsland_WP", "TheIsland_WP.ark")
open(bad_world, "wb").write(b"Q" * 4000)
ok_bw, why_bw = gates()
check("a world that will not open fails the gates", not ok_bw, why_bw)
check("and says the world does not verify",
      any("does not verify" in r for r in why_bw), why_bw)

print("\nFAILURES: %s" % fails if fails else "\nall restore tests passed")
sys.exit(1 if fails else 0)
