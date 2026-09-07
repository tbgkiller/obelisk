"""
Putting a world back, one map at a time.

A backup nobody can restore is not a backup, and until now that was the honest state of
this feature: archives were written, verified and listed, and the only way to use one
was to hand-extract a tarball - at exactly the moment somebody is least able to.

Restore is a migration whose source happens to be an archive, so it keeps the invariant
the migration was built on: **nothing is taken away until its replacement is proven**.
The world being replaced is copied first and kept afterwards, the archive is opened and
read before anything is stopped, the new world has to be a real file that SQLite will
open, and a restore that fails to verify says so with the old world still on disk.

Three things it deliberately does not do.

**It does not restore the cluster definition.** The archive carries one - the mod list
and its order, the ports, the cluster id - and putting a month-old definition onto a
cluster somebody has since retuned is its own disaster. Worlds are additive; the
definition overwrites. That is Phase 2, opt-in, and diffed before it runs.

**It does not touch the other maps.** One map's world lives in one folder. Restoring it
stops one container.

**It does not extract over live data.** Everything lands in a staging folder first and
is checked there, because a half-finished extract on top of the real world leaves
neither the old one nor the new.
"""

import json
import logging
import os
import shutil
import sqlite3
import tarfile
import time

from . import backup, layout
from . import maps as mapcat

log = logging.getLogger("obelisk.restore")

STAGING = ".restore-staging"
SUPERSEDED = ".superseded-%s"


def _unique_dir(path):
    """A directory name nothing already has.

    The stamp is only accurate to the second, so two restores of the same map inside one
    second would land on the same snapshot name - and the second would either fail or
    overwrite the first. backup.py learned this the same way, about archives.
    """
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists("%s-%d" % (path, n)):
        n += 1
    return "%s-%d" % (path, n)


def _map_id(key):
    return mapcat.BY_KEY[key]["map_id"]


def _world_dir(ark, map_id):
    return os.path.join(ark, layout.SAVED_ARKS.replace("/", os.sep), map_id)


def _member_prefix(map_id):
    return "ark/%s/SavedArks/%s" % (layout.SHARED, map_id)


# --------------------------------------------------------------------------- inspect
def inspect(path):
    """What is in this archive, without unpacking it. Returns a dict, never raises.

    Read from the archive's own manifest and definition rather than guessed from the
    filename, so what the operator is shown is what they would actually get.
    """
    out = {"path": path, "name": os.path.basename(path), "ok": False, "problem": "",
           "maps": [], "cluster_id": None, "mod_ids": "", "created": None,
           "bytes": 0, "has_secrets": False}
    try:
        out["bytes"] = os.path.getsize(path)
    except OSError:
        pass
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getnames()
            defn = {}
            if "cluster-definition.json" in members:
                fh = tar.extractfile("cluster-definition.json")
                defn = json.loads(fh.read().decode("utf-8")) if fh else {}
    except Exception as e:                        # noqa: BLE001 - reported, not raised
        out["problem"] = "this archive does not open (%s)" % e
        return out

    prefix = "ark/%s/SavedArks/" % layout.SHARED
    found = sorted({m[len(prefix):].split("/")[0] for m in members
                    if m.startswith(prefix) and m != prefix and "/" in m[len(prefix):]})
    out["maps"] = [m for m in found if m]
    out["cluster_id"] = defn.get("cluster_id")
    out["mod_ids"] = defn.get("mod_ids") or ""
    out["created"] = defn.get("written")
    out["has_secrets"] = bool(defn.get("secrets_required"))
    out["ok"] = bool(out["maps"])
    if not out["maps"]:
        out["problem"] = "it contains no map saves, so it would restore nothing"
    return out


def compare(store, info):
    """How this archive differs from the cluster as it stands. Notes, not blockers.

    The point is to be read before committing: "this archive has nine maps, you run ten"
    is the sort of thing worth knowing in advance rather than afterwards.
    """
    notes = []
    raw = store.get("maps")
    keys = [k.strip() for k in str(raw).split(",") if k.strip()] if isinstance(raw, str) else list(raw or ())
    running = [_map_id(k) for k in keys if k in mapcat.BY_KEY]

    missing = [m for m in running if m not in info["maps"]]
    extra = [m for m in info["maps"] if m not in running]
    if missing:
        notes.append("not in this archive: %s" % ", ".join(missing))
    if extra:
        notes.append("in the archive but not in this cluster: %s" % ", ".join(extra))

    here = str(store.get("cluster_id") or "")
    if info["cluster_id"] and info["cluster_id"] != here:
        notes.append("cluster id differs: archive %r, this cluster %r - characters "
                     "transfer between maps by cluster id, so restoring a world from a "
                     "different cluster is usually not what you want"
                     % (info["cluster_id"], here))

    mods_here = str(store.get("mod_ids") or "")
    if info["mod_ids"] and info["mod_ids"] != mods_here:
        notes.append("mod list differs (order matters): archive %s, this cluster %s"
                     % (info["mod_ids"], mods_here))
    return notes


# --------------------------------------------------------------------------- checking
def verify_world(path):
    """(ok, detail) - is this actually a world, or just a path that exists?

    The failure this exists for is the symlink trap: saves live behind a link into a
    container-only path, so an extract can leave a dangling link where a world should
    be and everything downstream looks fine until the server starts empty. So: a real
    file, of a plausible size, that SQLite will open and that has rows in it.
    """
    if not os.path.exists(path):
        return False, "there is no world file at %s" % path
    if os.path.islink(path):
        return False, ("the restored world is a symlink, not a file - that is the "
                       "dangling-link failure, and the world it points at is not here")
    if not os.path.isfile(path):
        return False, "%s is not a file" % path
    size = os.path.getsize(path)
    if size < 1024:
        return False, "the world file is only %d bytes, which is not a world" % size
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("?", "%3f"), uri=True)
        try:
            integrity = con.execute("PRAGMA integrity_check;").fetchone()[0]
            rows = con.execute("SELECT count(*) FROM game;").fetchone()[0]
        finally:
            con.close()
    except Exception as e:                        # noqa: BLE001 - reported
        return False, "SQLite will not read it (%s)" % e
    if integrity != "ok":
        return False, "SQLite reports it damaged: %s" % integrity
    if not rows:
        return False, "it opens but holds nothing"
    return True, "%s, %d rows, integrity ok" % (backup.human_size(size), rows)


def preflight(store, path, map_key):
    """(ok, problems) - everything checkable before a container is touched."""
    problems = []
    if map_key not in mapcat.BY_KEY:
        return False, ["unknown map %r" % map_key]
    map_id = _map_id(map_key)

    info = inspect(path)
    if not info["ok"]:
        return False, [info["problem"] or "the archive is not usable"]
    if map_id not in info["maps"]:
        problems.append("%s is not in this archive - it holds %s"
                        % (map_id, ", ".join(info["maps"]) or "nothing"))

    ok, detail, _members = backup._read_back(path, [map_id])
    if not ok:
        problems.append("the archive does not verify: %s" % detail)

    ark = layout.ark_root_of(store)
    try:
        free = shutil.disk_usage(ark).free
        need = info["bytes"] * 3          # staged copy, the swap, and room to breathe
        if free < need:
            problems.append("not enough room: this needs about %s free and there is %s"
                            % (backup.human_size(need), backup.human_size(free)))
    except OSError:
        pass
    return (not problems), problems


# --------------------------------------------------------------------------- doing it
def restore_map(store, path, map_key, stop=None, start=None, verify=None,
                snapshot=True, now=None):
    """Put one map's world back from an archive. (ok, message, detail).

    The order is the whole safety argument: verify the archive, copy the world that is
    about to be replaced, stop only this map, extract beside the real data, prove what
    came out, swap, start, and prove the server. A failure at any point leaves the map
    on the world it already had.
    """
    map_id = _map_id(map_key)
    ark = layout.ark_root_of(store)
    live = _world_dir(ark, map_id)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now or time.time()))
    detail = {"map": map_key, "map_id": map_id, "archive": os.path.basename(path),
              "steps": []}

    def step(text):
        detail["steps"].append(text)
        log.info("restore %s: %s", map_id, text)

    ok, problems = preflight(store, path, map_key)
    if not ok:
        return False, "Not restoring: " + "; ".join(problems), detail
    step("archive verified")

    if snapshot and os.path.isdir(live):
        keep = _unique_dir(os.path.join(backup.backups_dir(store),
                                        "pre-restore-%s-%s" % (map_id, stamp)))
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        shutil.copytree(live, keep)
        good, why = verify_world(os.path.join(keep, "%s.ark" % map_id))
        detail["snapshot"] = keep
        if not good:
            # The world on disk right now is already unreadable. Restoring is very
            # probably the right thing to do - but say so rather than implying the copy
            # taken is something it is not.
            step("snapshot taken, but the CURRENT world does not verify (%s)" % why)
        else:
            step("current world copied to %s (%s)" % (os.path.basename(keep), why))

    stop = stop or (lambda key: (True, "stopped"))
    ok_s, why_s = stop(map_key)
    if not ok_s:
        return False, "Could not stop %s, so nothing was changed: %s" % (map_id, why_s), detail
    step("stopped %s" % map_key)

    staging = os.path.join(ark, STAGING)
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    prefix = _member_prefix(map_id)
    try:
        with tarfile.open(path, "r:gz") as tar:
            wanted = [m for m in tar.getmembers()
                      if m.name == prefix or m.name.startswith(prefix + "/")]
            if not wanted:
                raise ValueError("the archive has no %s" % prefix)
            for m in wanted:
                # Never follow a link out of the staging folder, and never write above
                # it: an archive is untrusted input even when we wrote it.
                if m.issym() or m.islnk():
                    continue
                target = os.path.normpath(os.path.join(staging, m.name))
                if not target.startswith(os.path.abspath(staging) + os.sep) \
                        and target != os.path.abspath(staging):
                    continue
                tar.extract(m, staging)
    except Exception as e:                        # noqa: BLE001 - reported
        shutil.rmtree(staging, ignore_errors=True)
        _restart(start, map_key, detail)
        return False, ("Could not unpack the archive, so nothing was replaced: %s" % e), detail
    step("unpacked to staging")

    staged = os.path.join(staging, prefix.replace("/", os.sep))
    good, why = verify_world(os.path.join(staged, "%s.ark" % map_id))
    if not good:
        shutil.rmtree(staging, ignore_errors=True)
        _restart(start, map_key, detail)
        return False, ("The world in that archive did not verify, so it was not put in "
                       "place: %s" % why), detail
    step("restored world verified: %s" % why)

    superseded = None
    try:
        if os.path.isdir(live):
            superseded = _unique_dir(live + (SUPERSEDED % stamp))
            os.replace(live, superseded)
            detail["superseded"] = superseded
        os.makedirs(os.path.dirname(live), exist_ok=True)
        os.replace(staged, live)
        layout.give_to_server([live])
    except OSError as e:
        if superseded and not os.path.exists(live):
            os.replace(superseded, live)          # put it back exactly as it was
            step("swap failed and the previous world was put back")
        shutil.rmtree(staging, ignore_errors=True)
        _restart(start, map_key, detail)
        return False, "Could not swap the world into place: %s" % e, detail
    shutil.rmtree(staging, ignore_errors=True)
    step("swapped in; previous world kept as %s" % os.path.basename(superseded or "-"))

    ok_r, why_r = _restart(start, map_key, detail)
    if not ok_r:
        return False, ("The world was restored but %s did not start again: %s. The "
                       "previous world is still on disk as %s."
                       % (map_id, why_r, os.path.basename(superseded or "-")), ), detail

    if verify:
        ok_v, reasons = verify(map_key)
        detail["gates"] = reasons
        if not ok_v:
            return False, ("%s came back but did not pass its checks: %s. The previous "
                           "world is kept as %s - nothing was deleted."
                           % (map_id, "; ".join(reasons),
                              os.path.basename(superseded or "-"))), detail
        step("all checks passed")

    return True, ("Restored %s from %s. The world it replaced is kept as %s until you "
                  "remove it." % (map_id, os.path.basename(path),
                                  os.path.basename(superseded or "-")), ), detail


def _restart(start, map_key, detail):
    if not start:
        return True, "not started (no starter given)"
    ok, why = start(map_key)
    detail["steps"].append("started %s: %s" % (map_key, why))
    return ok, why


def superseded_worlds(store):
    """What previous restores left behind, so somebody can reclaim the space."""
    ark = layout.ark_root_of(store)
    root = os.path.join(ark, layout.SAVED_ARKS.replace("/", os.sep))
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in sorted(names):
        if ".superseded-" not in name:
            continue
        p = os.path.join(root, name)
        size = 0
        for here, _d, files in os.walk(p):
            for f in files:
                try:
                    size += os.path.getsize(os.path.join(here, f))
                except OSError:
                    pass
        out.append({"name": name, "path": p, "bytes": size,
                    "human": backup.human_size(size)})
    return out
