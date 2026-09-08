"""
Rolling one map back to a save the game already took.

ARK saves each world every fifteen minutes and, every ninth save, leaves a dated copy
beside the live one - twenty of them per map, about forty-five hours of history, already
on disk and costing nothing. Obelisk was ignoring all of it and offering only its own
archives, which are the right answer to a dead disk and a heavy one for "a mod ate the
base I built this afternoon".

So: read what is there, and put one back. No bookkeeping, no schedule, no new storage.
The directory *is* the record - anything that remembered which points existed would be a
second copy of the truth, free to drift from it the moment the game prunes one.

**The timestamp in the filename is UTC.** `Ragnarok_WP_06.09.2026_04.47.30.ark` was
written at 23:47 the previous evening in Chicago. Every restore point on this cluster
would otherwise be offered five hours away from when it happened, which is the sort of
wrong that gets noticed only after somebody restores the wrong one.

**Worlds only.** The game rotates the world and nothing else: player profiles and tribes
get one undated `.bak` each, with no relationship to any world file's timestamp. Pairing
them would be guesswork dressed as precision. So a restore point rolls back the world,
and the UI says plainly that characters keep what they earned - which is a real
consequence, not a footnote.
"""

import logging
import os
import re
import shutil
import time

from . import backup, layout, restore
from . import maps as mapcat

log = logging.getLogger("obelisk.savepoints")

# <MapId>_DD.MM.YYYY_HH.MM.SS.ark - written by the game, not by us.
_STAMPED = re.compile(r"^(?P<map>.+)_(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<Y>\d{4})_"
                      r"(?P<H>\d{2})\.(?P<M>\d{2})\.(?P<S>\d{2})\.ark$")


def world_dir(store, map_key, ark_root=None):
    """Where this map's saves live: shared/SavedArks/<MapId>."""
    ark = ark_root or layout.ark_root_of(store)
    return restore._world_dir(ark, mapcat.BY_KEY[map_key]["map_id"])


def live_world(store, map_key, ark_root=None):
    map_id = mapcat.BY_KEY[map_key]["map_id"]
    return os.path.join(world_dir(store, map_key, ark_root), "%s.ark" % map_id)


def _utc_to_local(parts, timezone=None):
    """Epoch seconds for a filename stamp, which the game writes in UTC.

    calendar.timegm rather than time.mktime: mktime reads a struct as *local* time, and
    on this cluster that would place every point five hours from when it happened.
    """
    import calendar
    return calendar.timegm((int(parts["Y"]), int(parts["m"]), int(parts["d"]),
                            int(parts["H"]), int(parts["M"]), int(parts["S"]), 0, 0, 0))


def list_points(store, map_key, ark_root=None, listdir=None, getsize=None, now=None):
    """Every dated save for one map, newest first. Read from disk, every time.

    Nothing is remembered between calls. The game prunes these on its own schedule, so
    a list held anywhere else would confidently offer a restore point that no longer
    exists - and the operator would find out when they clicked it.
    """
    map_id = mapcat.BY_KEY[map_key]["map_id"]
    folder = world_dir(store, map_key, ark_root)
    listdir = listdir or os.listdir
    getsize = getsize or os.path.getsize
    now = (now or time.time)()

    try:
        names = listdir(folder)
    except OSError:
        return []                      # never started, or no saves yet: not an error

    out = []
    for name in names:
        m = _STAMPED.match(name)
        if not m or m.group("map") != map_id:
            continue
        path = os.path.join(folder, name)
        try:
            size = getsize(path)
        except OSError:
            continue
        when = _utc_to_local(m.groupdict())
        out.append({
            "map": map_key,
            "name": name,
            "path": path,
            "when": when,
            "age": max(0, int(now - when)),
            "size": size,
            "human_size": backup.human_size(size),
            "local": time.strftime("%d %b %H:%M", time.localtime(when)),
            "ago": human_age(now - when),
        })
    out.sort(key=lambda p: p["when"], reverse=True)
    return out


def human_age(seconds):
    """"2h ago", "45m ago" - what somebody actually wants to read on a button."""
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return "%dm ago" % (seconds // 60)
    if seconds < 48 * 3600:
        hours, mins = seconds // 3600, (seconds % 3600) // 60
        return "%dh ago" % hours if mins < 5 else "%dh %dm ago" % (hours, mins)
    return "%dd ago" % (seconds // 86400)


def verify_point(path):
    """(ok, detail) - is this actually a world? The same check archives get."""
    return restore.verify_world(path)


def find_point(store, map_key, name, ark_root=None, listdir=None, getsize=None):
    """Resolve a posted name to a point that really exists, and nowhere else.

    Matched against the listing rather than joined onto the folder, so a name with a
    path in it cannot reach outside - the same reason the archive picker resolves inside
    the backups folder and refuses anything that lands above it.
    """
    wanted = os.path.basename(str(name or ""))
    for point in list_points(store, map_key, ark_root=ark_root, listdir=listdir,
                             getsize=getsize):
        if point["name"] == wanted:
            return point
    return None


WARNING = ("Rolls this map's world back to %s. Player characters and tribes are NOT "
           "rolled back - anything gained since then stays on the player but disappears "
           "from the world.")


def restore_point(store, map_key, name, stop=None, start=None, verify=None,
                  players=None, force=False, on_step=None, ark_root=None, now=None,
                  listdir=None, getsize=None):
    """Put one map back on one of its own dated saves. (ok, message, detail).

    The order is the safety argument, and it is restore_map's: prove the point first,
    copy the world that is about to be replaced, stop only this map, swap one file,
    start, and prove the server. A failure at any point leaves the map on the world it
    already had - and the copy taken means even a successful one is reversible.
    """
    ark = ark_root or layout.ark_root_of(store)
    map_id = mapcat.BY_KEY[map_key]["map_id"]
    detail = {"map": map_key, "map_id": map_id, "point": "", "steps": []}

    def step(text):
        detail["steps"].append(text)
        log.info("restore point %s: %s", map_id, text)
        if on_step:
            on_step(text)

    point = find_point(store, map_key, name, ark_root=ark, listdir=listdir,
                       getsize=getsize)
    if not point:
        return False, ("there is no restore point called %s for %s any more - the game "
                       "prunes these, so it may have aged out since the page was loaded"
                       % (os.path.basename(str(name or "")), map_id)), detail
    detail["point"] = point["name"]

    if not force:
        total, counts, silent = (players or (lambda: (0, {}, [])))()
        mine = counts.get(map_key, counts.get(mapcat.BY_KEY[map_key]["name"], 0))
        if any(l in (map_key, mapcat.BY_KEY[map_key]["name"]) for l, _w in silent):
            return False, ("%s did not answer, so it is not known whether anyone is on "
                           "it. Restore with force if you mean to anyway." % map_id), detail
        if mine:
            return False, ("%d player(s) are on %s. Restore with force, or wait until "
                           "they are off." % (mine, map_id)), detail

    ok, why = verify_point(point["path"])
    if not ok:
        return False, "that restore point will not open: %s" % why, detail
    step("restore point verified (%s)" % why)

    live = live_world(store, map_key, ark)
    # `now` is a callable here and in list_points, the way it is everywhere else in
    # this codebase - restore.py takes a value, and mixing the two conventions in one
    # module is how a lambda ends up being handed to gmtime.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime((now or time.time)()))
    keep = ""
    if os.path.isfile(live):
        keep = restore._unique_dir(
            os.path.join(backup.backups_dir(store),
                         "pre-point-%s-%s.ark" % (map_id, stamp)))
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        shutil.copy2(live, keep)
        good, detail_now = verify_point(keep)
        detail["kept"] = keep
        if good:
            step("current world copied aside (%s)" % detail_now)
        else:
            # Worth restoring over, almost certainly - but say what was copied rather
            # than implying it is a world somebody could go back to.
            step("current world copied aside, but it does not verify (%s)" % detail_now)

    stop = stop or (lambda key: (True, "stopped"))
    ok_s, why_s = stop(map_key)
    if not ok_s:
        return False, ("could not stop %s, so nothing was changed: %s"
                       % (map_id, why_s)), detail
    step("stopped %s" % map_key)

    try:
        # copy, not move: the point stays where it is, so the same one can be tried
        # again if the first attempt does not come up.
        shutil.copy2(point["path"], live)
    except OSError as e:
        _restart(start, map_key, detail, step)
        return False, ("could not put the world in place, so %s is starting again on "
                       "the world it had: %s" % (map_id, e)), detail
    step("world replaced with %s" % point["name"])

    ok_v, why_v = verify_point(live)
    if not ok_v:
        if keep and os.path.isfile(keep):
            shutil.copy2(keep, live)
            step("the swapped world did not verify, so the previous one was put back")
        _restart(start, map_key, detail, step)
        return False, "the restored world does not verify: %s" % why_v, detail

    ok_st, why_st = _restart(start, map_key, detail, step)
    if not ok_st:
        return False, "the world was replaced but %s did not start: %s" % (map_id,
                                                                           why_st), detail

    if verify:
        step("checking it is really serving")
        ok_g, reasons = verify(map_key)
        detail["gates"] = reasons
        if not ok_g:
            return False, ("%s came back on the restored world but did not pass "
                           "verification: %s" % (map_id, "; ".join(reasons or []))), detail

    return True, ("%s is back on its save from %s" % (map_id, point["local"])), detail


def _restart(start, map_key, detail, step):
    start = start or (lambda key: (True, "started"))
    step("starting %s" % map_key)
    ok, why = start(map_key)
    detail["steps"].append("start: %s" % why)
    return ok, why
