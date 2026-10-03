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

from . import backup, cluster, layout, restore
from . import maps as mapcat

log = logging.getLogger("obelisk.savepoints")

# <MapId>_DD.MM.YYYY_HH.MM.SS.ark - written by the game, not by us.
_STAMPED = re.compile(r"^(?P<map>.+)_(?P<d>\d{2})\.(?P<m>\d{2})\.(?P<Y>\d{4})_"
                      r"(?P<H>\d{2})\.(?P<M>\d{2})\.(?P<S>\d{2})\.ark$")


def _map_id(store, map_key):
    """The level name this map saves under. Asked of the store, because a cluster
    can run a map Obelisk does not ship."""
    e = mapcat.entry(store, map_key)
    if not e:
        raise KeyError("unknown map %r" % map_key)
    return e["map_id"]


def world_dir(store, map_key, ark_root=None):
    """Where this map's saves live: shared/SavedArks/<MapId>."""
    ark = ark_root or layout.ark_root_of(store)
    return restore._world_dir(ark, _map_id(store, map_key))


def live_world(store, map_key, ark_root=None):
    map_id = _map_id(store, map_key)
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
    map_id = _map_id(store, map_key)
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
                  listdir=None, getsize=None, save=None, restart_on_failure=True):
    """Put one map back on one of its own dated saves. (ok, message, detail).

    The order is the safety argument, and it is restore_map's: prove the point first,
    copy the world that is about to be replaced, stop only this map, swap one file,
    start, and prove the server. A failure at any point leaves the map on the world it
    already had - and the copy taken means even a successful one is reversible.

    `restart_on_failure=False` is for auto_restore, and it is the difference between
    "leaves the map on the world it already had" being a comfort and being the harm.
    Somebody pressing a restore point on a serving map wants it back up on its old world
    if the swap fails. A map the integrity gate is holding down is held BECAUSE the world
    it already had is damaged, so starting it again on that world is the exact thing the
    hold exists to prevent - and an unattended restore that failed would otherwise undo
    the hold without anybody having decided to.
    """
    ark = ark_root or layout.ark_root_of(store)
    map_id = _map_id(store, map_key)
    # The name for sentences, the id for paths and the log. This flow said "The Island"
    # in its refusals and "TheIsland_WP" in its result two clicks later, while the
    # archive restore beside it said The Island throughout. restore.restore_map keeps
    # both, for the same reason.
    map_name = (mapcat.entry(store, map_key) or {}).get("name") or map_key
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
                       % (os.path.basename(str(name or "")), map_name)), detail
    detail["point"] = point["name"]

    if not force:
        # A guard saying no is not a restore that broke. `refused` is what the route
        # reads to announce it at warning and the page reads to render it amber - the
        # archive restore beside this one has said it that way since the guards went
        # in, and this one was still announcing its refusals with a red cross.
        total, counts, silent = (players or (lambda: (0, {}, [])))()
        mine = counts.get(map_key, counts.get(map_name, 0))
        if any(l in (map_key, map_name) for l, _w in silent):
            detail["refused"] = "silent"
            return False, ("%s did not answer, so it is not known whether anyone is on "
                           "it. Nothing has been changed. Restore with force if you "
                           "mean to anyway." % map_name), detail
        if mine:
            detail["refused"] = "players"
            return False, ("%s on %s. Nothing has been changed. Restore with force, or "
                           "wait until they are off."
                           % (cluster._are(mine), map_name)), detail

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

    # Ask the map to write its world out before it is stopped, and carry on either way.
    # Best effort on purpose: the world being replaced has already been copied aside as
    # a file, so a save that does not happen costs nothing here - and blocking on it
    # would refuse to restore a map exactly when it is unhealthy, which is when somebody
    # most wants to. It is still worth trying, because a map that shuts down cleanly is
    # a map that does not leave a hot journal behind.
    if save:
        step("asking %s to save first" % map_key)
        ok_sv, why_sv = save(map_key)
        step("saved" if ok_sv else "it did not answer, carrying on: %s" % why_sv)

    stop = stop or (lambda key: (True, "stopped"))
    ok_s, why_s = stop(map_key)
    if not ok_s:
        return False, ("could not stop %s, so nothing was changed: %s"
                       % (map_name, why_s)), detail
    step("stopped %s" % map_key)

    # Any journal beside the world belongs to the world being replaced, not to the one
    # arriving. Left in place it would be replayed into a database it knows nothing
    # about - see restore.clear_sidecars.
    gone = restore.clear_sidecars(live)
    if gone:
        step("cleared a stale journal left by the last shutdown (%s)" % ", ".join(gone))

    try:
        # copy, not move: the point stays where it is, so the same one can be tried
        # again if the first attempt does not come up.
        shutil.copy2(point["path"], live)
        restore.give_world_to_server(live)
    except OSError as e:
        if not restart_on_failure:
            step("left stopped - the world it had is the one being replaced")
            return False, ("could not put the world in place, so %s is still stopped on "
                           "the world it had: %s" % (map_name, e)), detail
        _restart(start, map_key, detail, step)
        return False, ("could not put the world in place, so %s is starting again on "
                       "the world it had: %s" % (map_name, e)), detail
    step("world replaced with %s" % point["name"])

    ok_v, why_v = verify_point(live)
    if not ok_v:
        if keep and os.path.isfile(keep):
            restore.clear_sidecars(live)
            shutil.copy2(keep, live)
            restore.give_world_to_server(live)
            step("the swapped world did not verify, so the previous one was put back")
        if not restart_on_failure:
            step("left stopped - the world it had is the one being replaced")
            return False, ("the restored world does not verify, so %s is still stopped: "
                           "%s" % (map_name, why_v)), detail
        _restart(start, map_key, detail, step)
        return False, "the restored world does not verify: %s" % why_v, detail

    ok_st, why_st = _restart(start, map_key, detail, step)
    if not ok_st:
        return False, ("the world was replaced but %s did not start: %s"
                       % (map_name, why_st)), detail

    if verify:
        step("checking it is really serving")
        ok_g, reasons = verify(map_key)
        detail["gates"] = reasons
        if not ok_g:
            return False, ("%s came back on the restored world but did not pass "
                           "verification: %s"
                           % (map_name, "; ".join(reasons or []))), detail

    # What it kept, named, for the same reason the archive restore names it: the copy
    # taken at the top of this function is the only way back from a rollback nobody
    # wanted, and a folder somebody has to already know about is not a way back.
    kept = os.path.basename(detail.get("kept") or "")
    return True, ("%s is back on its save from %s.%s"
                  % (map_name, point["local"],
                     (" The world it replaced is kept as %s until you remove it." % kept)
                     if kept else "")), detail


def _restart(start, map_key, detail, step):
    start = start or (lambda key: (True, "started"))
    step("starting %s" % map_key)
    ok, why = start(map_key)
    detail["steps"].append("start: %s" % why)
    return ok, why


# ---------------------------------------------------------------- bringing a map back
#
# The incident this is for: on 1 October The Island and Ragnarok crashed during their
# own saves, the crash watch stood them down, their live worlds were malformed SQLite,
# and two applies later the integrity gate was still holding them down - correctly, and
# uselessly, beside twenty dated saves each that read perfectly well. Everything needed
# to bring them back was on disk. What was missing was anybody deciding to.
#
# So this decides, and it is narrow on purpose. It acts on ONE finding - SQLite itself
# says the live world is damaged - and refuses everything that merely looks like it. A
# world that cannot be reached is a mount, not damage, and restoring over a mount
# problem trades a healthy world for an older one. A world with a journal beside it has
# not finished settling, and the journal may be exactly what makes it whole again. A
# world that is not there at all has nothing to be damaged. And a world that verifies
# now - because somebody restored it by hand while this was waiting - is left alone.

def newest_good_point(store, map_key, ark_root=None, verify=None, listdir=None,
                      getsize=None, now=None):
    """(point or None, rejected) - the newest dated save that actually opens.

    Newest first, and the first one that verifies wins: the least world lost is the
    whole point. `rejected` is [(name, why)] for every newer point that would not open,
    because "it went back three hours" reads very differently once you know the two
    points in between were damaged too - which is what a crash during a save does to the
    copy it was writing at the time.
    """
    verify = verify or verify_point
    rejected = []
    for point in list_points(store, map_key, ark_root=ark_root, listdir=listdir,
                             getsize=getsize, now=now):
        ok, why = verify(point["path"])
        if ok:
            return point, rejected
        rejected.append((point["name"], why))
    return None, rejected


def live_state(path, verify=None, listdir=None):
    """(state, why) for one live world, in the words the apply gate uses.

    "ok", "damaged", "writing", "absent" or "unreachable" - and only "damaged" is a
    licence to restore. The order of the questions is the argument:

      * the folder has to LIST, or nothing below it is a finding about the world;
      * nothing at all there is "absent" - lexists, so a dangling link is not absence;
      * a link, or anything that is not a plain file, is "unreachable": restore_point
        copies onto this path, and copying onto a link writes through it to wherever it
        points, which is the symlink trap with a restore on top;
      * a sidecar beside it is "writing" - verify_world opens with immutable=1, which
        ignores a hot journal, so a world whose journal would make it whole reads as
        damaged when it is not;
      * a file that will not even open for reading is "unreachable" - a permission, not
        a page SQLite has looked at;
      * and only then is SQLite asked. Whatever it says against the file is "damaged".
    """
    verify = verify or restore.verify_world
    listdir = listdir or os.listdir
    folder = os.path.dirname(path)
    try:
        listdir(folder)
    except FileNotFoundError:
        return "absent", "its world folder %s does not exist" % folder
    except OSError as e:
        return "unreachable", "its world folder could not be read (%s)" % e
    if not os.path.lexists(path):
        return "absent", "there is no world file at %s" % path
    if os.path.islink(path) or not os.path.isfile(path):
        return "unreachable", ("%s is not a plain file, so nothing will be copied onto "
                               "it" % path)
    hot = sorted(s for s in restore.SIDECARS if os.path.lexists(path + s))
    if hot:
        return "writing", ("a %s file is beside it, so it had not finished being "
                           "written" % ", ".join(hot))
    try:
        with open(path, "rb") as f:
            f.read(1)
    except OSError as e:
        return "unreachable", "the world file will not open for reading (%s)" % e
    ok, why = verify(path)
    return ("ok" if ok else "damaged"), why


def human_span(seconds):
    """"about 54 minutes", "about 3 hours" - how much world a rollback costs, said the
    way somebody reads it in a channel."""
    seconds = max(0, int(seconds or 0))
    if seconds < 90:
        return "under a minute"
    if seconds < 90 * 60:
        return "about %d minutes" % round(seconds / 60.0)
    if seconds < 36 * 3600:
        hours = round(seconds / 3600.0, 1)
        return "about %s hours" % (("%d" % hours) if hours == int(hours) else hours)
    return "about %d days" % round(seconds / 86400.0)


def auto_restore(store, map_key, restore=None, live_ok=None, pick=None, ark_root=None,
                 mtime=None, **wiring):
    """Bring a map whose live world is damaged back on its newest good save point.

    (ok, message, detail). `wiring` is stop/start/verify/save/on_step and goes straight
    to restore_point, so this path and the button stop, start and prove a map the same
    way. `restore`, `live_ok` and `pick` are restore_point, live_state and
    newest_good_point, handed in for the tests.

    The caller has to have positively established the map is not running - that is why
    the restore is forced past the player check, and the only reason it may be. This
    then asks the live world again rather than trusting whoever said it was damaged: the
    hold may be hours old, and in that time somebody may have restored it by hand, or
    the share may have dropped out from under it. Either of those is a reason to do
    nothing, and `detail["refused"]` says which.

    The damaged world is not lost: restore_point copies it aside to the backups folder
    before anything moves, and names the copy in the message.
    """
    ark = ark_root or layout.ark_root_of(store)
    map_name = (mapcat.entry(store, map_key) or {}).get("name") or map_key
    detail = {"map": map_key, "point": "", "rejected": [], "rolled_back": 0,
              "steps": []}
    live = live_world(store, map_key, ark)

    state, why = (live_ok or live_state)(live)
    detail["live_state"], detail["live_why"] = state, why
    if state == "ok":
        detail["refused"] = "intact"
        return False, ("%s's world reads cleanly now (%s), so it was not rolled back. "
                       "Somebody may already have restored it." % (map_name, why)), detail
    if state != "damaged":
        detail["refused"] = state or "unknown"
        return False, ("%s's world was not rolled back, because it is not damaged - it "
                       "is %s: %s. Only a world SQLite itself reports damaged is restored "
                       "automatically." % (map_name, state or "unknown", why)), detail

    point, rejected = (pick or newest_good_point)(store, map_key, ark_root=ark)
    detail["rejected"] = list(rejected or [])
    if not point:
        return False, ("%s's world is damaged and none of its %d save point%s would "
                       "open, so there is nothing good to restore from. Restore it from "
                       "an archive." % (map_name, len(detail["rejected"]),
                                        "" if len(detail["rejected"]) == 1 else "s")
                       ), detail
    detail["point"] = point["name"]
    detail["point_local"] = point.get("local") or ""
    # How much world goes: from the point to when the live world was last written. The
    # file's own mtime rather than "now", because a map that has been down for a day
    # has not been accumulating a day of building.
    try:
        written = (mtime or os.path.getmtime)(live)
    except OSError:
        written = point["when"]
    detail["rolled_back"] = max(0, int(written - point["when"]))
    detail["rolled_back_human"] = human_span(detail["rolled_back"])

    ok, msg, got = (restore or restore_point)(
        store, map_key, point["name"], force=True, ark_root=ark,
        restart_on_failure=False, **wiring)
    for k, v in (got or {}).items():
        if k not in ("map", "point"):
            detail[k] = v
    if not ok:
        return False, msg, detail
    kept = os.path.basename(detail.get("kept") or "")
    skipped = (" %d newer save point%s would not open and %s skipped."
               % (len(detail["rejected"]), "" if len(detail["rejected"]) == 1 else "s",
                  "was" if len(detail["rejected"]) == 1 else "were")
               if detail["rejected"] else "")
    return True, ("%s's world was damaged, so it has been restored from its save point "
                  "of %s, rolling the world back %s.%s%s Player characters and tribes "
                  "are not rolled back."
                  % (map_name, point.get("local") or point["name"],
                     detail["rolled_back_human"], skipped,
                     (" The damaged world is kept as %s." % kept) if kept else "")
                  ), detail

