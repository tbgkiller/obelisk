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


def named(key):
    """The confirmation restore_map now wants: the map's own name, typed.

    Spelled through the catalogue rather than written out, so a map that gets renamed
    breaks this loudly instead of leaving every restore test refused.
    """
    from .maps import BY_KEY
    return BY_KEY[key]["name"]


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
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
                                      confirm=named("ragnarok"),
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
    st3, arc3, "island", confirm=named("island"),
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
    st3, arc3, "island", confirm=named("island"),
    stop=lambda k: (True, ""), start=lambda k: (True, ""),
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
                                      confirm=named("island"),
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
    st5, arc5, "island", confirm=named("island"), stop=lambda k: (True, ""),
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
    st6, arc6, "island", confirm=named("island"),
    stop=lambda k: (True, ""), start=lambda k: (True, ""),
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
    st7, arc7, "island", confirm=named("island"),
    stop=lambda k: (True, ""), start=lambda k: (True, ""),
    verify=lambda k: (seen.append(k) or (True, [])),
    on_step=lambda t: seen.append("step:%s" % t))
check("the restore with gates wired succeeds", ok_g, msg_g)
check("the verify step is actually called", "island" in seen, seen)
_swapped_at = [i for i, x in enumerate(seen) if x.startswith("step:swapped")]
check("and it runs after the swap, not before",
      "island" in seen and _swapped_at
      and seen.index("island") > max(_swapped_at), seen)
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


# ---------------------------------------------------------------- the guards
#
# This replaces one map's whole world directory - every base, every dino, every day
# since the archive was taken - while people may be standing in it. Nothing is deleted
# (the world it replaces becomes .superseded-<stamp>) but there is no undo anywhere in
# the product, so from the operator's seat it does not come back. It was the least
# guarded action in Obelisk: the save-point rollback beside it already refused for
# players, already asked the map to save, already took a force flag. The bigger hammer
# had none of them.

st_g, ark_g = fresh()
worlds(ark_g, "livenow")
_ok, _m, arc_g = backup.create(st_g)
worlds(ark_g, "livelater")
live_g = os.path.join(ark_g, "shared", "SavedArks", "TheIsland_WP", "TheIsland_WP.ark")


class Tripwire:
    """Everything a refusal must not have done by the time it says no."""

    def __init__(self):
        self.calls = []

    def stop(self, key):
        self.calls.append("stop:%s" % key)
        return True, ""

    def start(self, key):
        self.calls.append("start:%s" % key)
        return True, ""

    def preflight(self, *a, **k):
        self.calls.append("preflight")
        return True, []


def guarded(**over):
    """restore_map with the tripwires in, and everything else at its default."""
    trip = Tripwire()
    real_pre, real_copy = restore.preflight, restore.shutil.copytree
    copied = []
    restore.preflight = trip.preflight
    restore.shutil.copytree = lambda *a, **k: (copied.append(a[:2])
                                               or real_copy(*a, **k))
    try:
        kw = dict(stop=trip.stop, start=trip.start, verify=lambda k: (True, []))
        kw.update(over)
        out = restore.restore_map(st_g, arc_g, "island", **kw)
    finally:
        restore.preflight, restore.shutil.copytree = real_pre, real_copy
    return out, trip, copied


# 1. no confirmation at all
(ok_c, msg_c, det_c), trip_c, copied_c = guarded()
check("a restore with no confirmation is refused", not ok_c, msg_c)
check("it says which word to type", "type The Island" in msg_c, msg_c)
check("and that nothing has been changed", "Nothing has been changed" in msg_c, msg_c)
check("nothing was stopped or started", trip_c.calls == [], trip_c.calls)
check("and nothing was copied", copied_c == [], copied_c)
check("the refusal is marked as a refusal, not a failure",
      det_c.get("refused") == "confirm", det_c)
check("the live world is exactly as it was", world_marker(live_g) == "livelater")

# 2. the guard runs BEFORE preflight - a refusal never opens the archive
check("a refusal never gets as far as reading the archive",
      "preflight" not in trip_c.calls, trip_c.calls)

# 3. the wrong map's name does not do
(ok_w, msg_w, _dw), trip_w, _cw = guarded(confirm="Ragnarok")
check("another map's name does not confirm this one", not ok_w, msg_w)
check("and still stops nothing", trip_w.calls == [], trip_w.calls)

# 4. the right name, however it was typed
check("the name confirms", restore.confirms("island", "The Island"))
check("case is not the point", restore.confirms("island", "the island"))
check("nor is stray whitespace", restore.confirms("island", "  The Island  "))
check("an empty confirmation never passes", not restore.confirms("island", ""))
check("and neither does None", not restore.confirms("island", None))
check("a map id is not a map name", not restore.confirms("island", "TheIsland_WP"))

# 5. players on the map - a hard block, the way the save-point rollback has always
#    been. The message has to name the map and the count, because "someone is on"
#    with ten maps is not an answer anybody can act on.
(ok_p, msg_p, det_p), trip_p, _cp = guarded(
    confirm=named("island"), players=lambda: (3, {"island": 3}, []))
check("players on the map stop the restore", not ok_p, msg_p)
check("the message names the map", "The Island" in msg_p, msg_p)
check("and the count", "3 player" in msg_p, msg_p)
check("it says nothing has been changed", "Nothing has been changed" in msg_p, msg_p)
check("it offers force rather than just refusing", "force" in msg_p, msg_p)
check("nothing was stopped", trip_p.calls == [], trip_p.calls)
check("marked as refused for players", det_p.get("refused") == "players", det_p)

(ok_p1, msg_p1, _d), _t, _c = guarded(
    confirm=named("island"), players=lambda: (1, {"island": 1}, []))
check("one player reads as one player, not 1 players", "1 player is on" in msg_p1,
      msg_p1)

# a player on a DIFFERENT map is not a reason to refuse this one
(ok_o, msg_o, _do), trip_o, _co = guarded(
    confirm=named("island"), players=lambda: (4, {"ragnarok": 4}, []))
check("players on another map do not block this one", ok_o, msg_o)

# 6. a map that did not answer counts as occupied - the same rule restore_point
#    keeps. "It is not known whether anyone is on it" is not "nobody is on it".
(ok_s, msg_s, det_s), trip_s, _cs = guarded(
    confirm=named("island"), players=lambda: (0, {}, [("island", "timed out")]))
check("a map that did not answer is treated as occupied", not ok_s, msg_s)
check("and says so rather than implying it is empty",
      "did not answer" in msg_s and "not known" in msg_s, msg_s)
check("nothing was stopped", trip_s.calls == [], trip_s.calls)
check("marked as refused because it went silent",
      det_s.get("refused") == "silent", det_s)

# 7. force overrides both, because "restore anyway" is a real thing to mean
(ok_fp, msg_fp, _dfp), _tfp, _cfp = guarded(
    confirm=named("island"), force=True, players=lambda: (3, {"island": 3}, []))
check("force restores with players on", ok_fp, msg_fp)
(ok_fs, msg_fs, _dfs), _tfs, _cfs = guarded(
    confirm=named("island"), force=True,
    players=lambda: (0, {}, [("island", "timed out")]))
check("force restores past a map that did not answer", ok_fs, msg_fs)
check("but force is not a substitute for the confirmation",
      not guarded(force=True)[0][0], guarded(force=True)[0][1])

# 8. the save is best effort. The world being replaced is copied aside as a file a few
#    lines later, so a save that does not happen costs nothing - and blocking on it
#    would refuse to restore a map exactly when it is unhealthy, which is when
#    somebody most wants to.
saved = []
(ok_sv, msg_sv, det_sv), _tsv, _csv = guarded(
    confirm=named("island"),
    save=lambda k: (saved.append(k) or (True, "saved")))
check("the map is asked to save before it is stopped", saved == ["island"], saved)
check("and the restore goes ahead", ok_sv, msg_sv)
_steps = det_sv.get("steps") or []
check("the save is a step somebody can see",
      any("save first" in x for x in _steps), _steps)
check("and it runs before the archive is verified",
      next(i for i, x in enumerate(_steps) if "save first" in x)
      < next(i for i, x in enumerate(_steps) if "archive verified" in x), _steps)


def boom(_key):
    raise RuntimeError("RCON is not answering")


try:
    (ok_b, msg_b, det_b), _tb, _cb = guarded(confirm=named("island"), save=boom)
    raised_b = ""
except Exception as e:                            # noqa: BLE001 - that is the assertion
    ok_b, msg_b, det_b = False, "", {}
    raised_b = "%s: %s" % (e.__class__.__name__, e)
check("a save that raises does not stop the restore", ok_b and not raised_b,
      raised_b or msg_b)
check("and the exception never escapes restore_map", raised_b == "", raised_b)
_bsteps = det_b.get("steps") or []
check("and the step says it carried on",
      any("carrying on" in x for x in _bsteps), _bsteps)
check("naming what went wrong",
      any("RCON is not answering" in x for x in _bsteps), _bsteps)

(ok_r2, msg_r2, det_r2), _tr2, _cr2 = guarded(
    confirm=named("island"), save=lambda k: (False, "it is not running"))
check("a save that answers no does not stop the restore either", ok_r2, msg_r2)

# 9. the messages are sentences, not tuples. A stray trailing comma made the success
#    message a 1-tuple, so a finished restore announced the brackets and quotes to
#    Discord along with the sentence.
check("a successful restore's message is a string", isinstance(msg_sv, str),
      type(msg_sv).__name__)
check("and reads as a sentence", msg_sv.startswith("Restored"), msg_sv)
check("with no tuple punctuation in it",
      not msg_sv.startswith("(") and not msg_sv.endswith(",)"), msg_sv)

st_t, ark_t = fresh()
worlds(ark_t, "there")
_okt, _mt, arc_t = backup.create(st_t)
ok_ns, msg_ns, _dns = restore.restore_map(
    st_t, arc_t, "island", confirm=named("island"),
    stop=lambda k: (True, ""), start=lambda k: (False, "it would not come back"))
check("a map that does not come back is reported", not ok_ns, msg_ns)
check("as a string, not a tuple", isinstance(msg_ns, str), type(msg_ns).__name__)
check("that names the world kept on disk", "still on disk" in msg_ns, msg_ns)
# ...and names the map the way every other sentence in this flow does. This was the
# last message left saying TheIsland_WP, after the success line moved to The Island.
check("naming the map, not the folder on disk",
      "The Island did not start again" in msg_ns, msg_ns)
check("while the folder it kept is still a real path",
      "TheIsland_WP.superseded-" in msg_ns, msg_ns)

# and the player refusal agrees with its count through the shared helper
_ok_pl, _msg_pl, _d_pl = restore.restore_map(
    st_t, arc_t, "island", confirm=named("island"),
    players=lambda: (1, {"island": 1}, []))
check("one player still reads as one player", "1 player is on The Island" in _msg_pl,
      _msg_pl)
check("with no parenthetical plural anywhere", "player(s)" not in _msg_pl, _msg_pl)



# ---- the success line speaks the operator's language
#
# It said "Restored TheIsland_WP", which is the folder on disk. Every other sentence in
# this flow - the dropdown, the confirmation, the refusals, the announcements - says
# The Island, which is what somebody picked.
st_fn, ark_fn = fresh()
worlds(ark_fn, "before")
_okfn, _mfn, arc_fn = backup.create(st_fn)
ok_fn, msg_fn, det_fn = restore.restore_map(
    st_fn, arc_fn, "island", confirm=named("island"),
    stop=lambda k: (True, ""), start=lambda k: (True, ""),
    verify=lambda k: (True, []))
check("the restore for the naming check succeeds", ok_fn, msg_fn)
check("the success line names the map the way the rest of the flow does",
      "Restored The Island from" in msg_fn, msg_fn)
check("not the folder name nobody picked from a dropdown",
      not msg_fn.startswith("Restored TheIsland_WP"), msg_fn)
check("while the folder it kept stays a real path somebody can go and find",
      "TheIsland_WP.superseded-" in msg_fn, msg_fn)


print("\nFAILURES: %s" % fails if fails else "\nall restore tests passed")
sys.exit(1 if fails else 0)
