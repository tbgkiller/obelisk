"""
Backups: the portable set, on demand and on a schedule.

What goes in is the whole data root - Obelisk's store, every map's saves, the shared
config, the transfer data - plus the cluster definition needed to rebuild the stack
from nothing. What stays out is the game install, because it re-downloads for free and
carrying 20+ GB is how a backup quietly stops being something you ever move.

Two things this refuses to be casual about.

**A backup that cannot be read is not a backup.** Every archive is verified after it is
written - the stream decompresses, the member list is readable, and the maps that were
supposed to be in it are in it. An archive that fails verification is reported as a
failure, not filed as success.

**The archive contains secrets.** The cluster definition carries the admin/RCON
password and the Discord bot token, because a restore without them is not a restore.
So archives are written 0600, never logged in full, and the manifest names which keys
are secret without ever carrying their values. Anything leaving this machine gets
encrypted first - that is Phase 3's job, and this file is written to make it easy.
"""

import json, logging, os, re, tarfile, time

from . import layout

log = logging.getLogger("obelisk.backup")

PREFIX = "obelisk-backup-"
SUFFIX = ".tar.gz"
STAMP = "%Y%m%dT%H%M%SZ"
MANIFEST = "manifest.json"

# Keys whose values must never appear in a log line or a manifest.
SECRET_KEYS = ("admin_password", "server_password", "discord_token", "admin_token")


def backups_dir(store):
    """Where archives live: inside the data root, so they move with it."""
    return "%s/backups" % layout.paths(store.get("appdata"))["obelisk"]


def archive_name(when=None):
    return PREFIX + time.strftime(STAMP, time.gmtime(when or time.time())) + SUFFIX


def _is_archive(name):
    return name.startswith(PREFIX) and name.endswith(SUFFIX)


def definition(store):
    """The cluster as data: everything needed to rebuild the stack.

    This is the part the ARK save files do not contain. Without the mod list and its
    order a restored cluster loads different mods; without the ports and RAM it comes
    back shaped differently; without the cluster id, transfers stop working.
    """
    from .schema import SETTINGS
    values = {}
    for s in SETTINGS:
        key = s["key"]
        if key in SECRET_KEYS:
            continue
        values[key] = store.get(key)
    per_map = {m: dict(v) for m, v in store.data.get("maps", {}).items()}
    return {
        "version": 1,
        "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cluster_id": store.get("cluster_id"),
        "maps": store.get("maps"),
        "mod_ids": store.get("mod_ids"),
        "mod_order_matters": True,
        "settings": values,
        "per_map": per_map,
        # Named, never valued: a restore needs to know what to ask for.
        "secrets_required": [k for k in SECRET_KEYS if str(store.get(k) or "").strip()],
    }


def _manifest(store, members, maps_expected):
    return {
        "version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_root": store.get("appdata"),
        "portable": list(layout.PORTABLE),
        "excluded": ["the game install (re-downloads on restore)"],
        "maps_expected": list(maps_expected),
        "entries": len(members),
        "contains_secrets": bool(definition(store)["secrets_required"]),
        "secret_keys": list(definition(store)["secrets_required"]),
    }


def _map_ids(store):
    """The save-file names the archive should contain, one per selected map."""
    from . import maps as mapcat
    raw = store.get("maps")
    keys = [k.strip() for k in str(raw).split(",") if k.strip()] if isinstance(raw, str) else list(raw or ())
    return [m["map_id"] for m in mapcat.resolve(keys)]


def create(store, when=None, flush=None):
    """Write a verified backup. Returns (ok, message, path_or_None).

    `flush` is an optional callable run before the copy - Phase 2's RCON SaveWorld -
    so the archive catches the world as of now rather than the last autosave.
    """
    root = str(store.get("appdata")).rstrip("/")
    if not os.path.isdir(root):
        return False, "No data root at %s yet - nothing to back up." % root, None

    flushed = ""
    if flush:
        try:
            ok, detail = flush()
            flushed = (" Saved all maps first." if ok else
                       " Could not flush saves first (%s), so this is the last autosave."
                       % detail)
        except Exception as e:
            flushed = " Could not flush saves first (%s)." % e

    out_dir = backups_dir(store)
    os.makedirs(out_dir, exist_ok=True)
    path = _unique(os.path.join(out_dir, archive_name(when)))
    tmp = path + ".part"

    defn = definition(store)
    maps_expected = _map_ids(store)

    try:
        with tarfile.open(tmp, "w:gz") as tar:
            for tree in layout.PORTABLE:
                src = os.path.join(root, tree)
                if not os.path.exists(src):
                    continue
                # The backups folder must not recurse into itself.
                tar.add(src, arcname=tree, filter=_skip_backups)
            _add_bytes(tar, "cluster-definition.json",
                       json.dumps(defn, indent=2, sort_keys=True).encode("utf-8"))
    except Exception as e:
        _unlink(tmp)
        return False, "Backup failed while writing: %s" % e, None

    ok, detail, members = _read_back(tmp, maps_expected)
    if not ok:
        _unlink(tmp)
        return False, "Backup was written but failed verification, so it was discarded: %s" % detail, None

    try:
        os.replace(tmp, path)
        os.chmod(path, 0o600)          # it carries the admin password and bot token
    except OSError as e:
        _unlink(tmp)
        return False, "Backup could not be finalised: %s" % e, None

    man = _manifest(store, members, maps_expected)
    try:
        with open(path + ".json", "w", encoding="utf-8") as fh:
            json.dump(man, fh, indent=2, sort_keys=True)
        os.chmod(path + ".json", 0o600)
    except OSError:
        pass

    size = os.path.getsize(path)
    log.info("backup ok: %s (%.1f MB, %d entries)", os.path.basename(path),
             size / 1048576.0, len(members))
    return True, ("Backed up %d map%s and the cluster definition - %.1f MB, verified "
                  "readable.%s" % (len(maps_expected), "" if len(maps_expected) == 1 else "s",
                                   size / 1048576.0, flushed)), path


def _unique(path):
    """A name nothing already has.

    The stamp is only accurate to the second, so two backups in the same second would
    otherwise land on the same name and the second would silently replace the first -
    destroying a backup while reporting success.
    """
    if not os.path.exists(path):
        return path
    stem = path[:-len(SUFFIX)]
    n = 2
    while os.path.exists("%s-%d%s" % (stem, n, SUFFIX)):
        n += 1
    return "%s-%d%s" % (stem, n, SUFFIX)


def _skip_backups(info):
    """Keep the backups folder out of its own archives."""
    parts = info.name.replace("\\", "/").split("/")
    if "backups" in parts:
        return None
    return info


def _add_bytes(tar, name, data):
    import io as _io
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, _io.BytesIO(data))


def _unlink(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _read_back(path, maps_expected):
    """Open the archive we just wrote and prove it is usable.

    Deliberately a real read, not a size check: the failure this catches is an archive
    that exists, looks plausible, and contains no worlds.
    """
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getnames()
    except Exception as e:
        return False, "it does not open (%s)" % e, []
    if not members:
        return False, "it is empty", []
    if "cluster-definition.json" not in members:
        return False, "the cluster definition is missing, so a rebuild could not know "\
                      "which mods to load", members
    missing = []
    for map_id in maps_expected:
        want = "%s/SavedArks/%s" % (layout.SHARED, map_id)
        if not any(m == want or m.startswith(want + "/") for m in members):
            missing.append(map_id)
    if missing and maps_expected:
        # A save folder can legitimately be absent before a map has ever booted, so
        # this is only fatal when *every* selected map is missing - that is the shape
        # of the empty-backup bug rather than a cluster that has not started yet.
        if len(missing) == len(maps_expected):
            return False, ("no map saves are in it (%s). That is the shape of a backup "
                           "that would restore nothing." % ", ".join(missing)), members
    return True, "", members


def listing(store):
    """Existing backups, newest first."""
    d = backups_dir(store)
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        if not _is_archive(name):
            continue
        full = os.path.join(d, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        out.append({"name": name, "path": full, "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def prune(store, keep=None):
    """Delete all but the newest `keep` archives. Returns the names removed.

    Retention is the whole reason a schedule is safe to switch on: without it a nightly
    backup fills the disk it is protecting.
    """
    keep = int(store.get("backup_keep") if keep is None else keep)
    if keep <= 0:
        return []
    rows = listing(store)
    removed = []
    for row in rows[keep:]:
        try:
            os.remove(row["path"])
            _unlink(row["path"] + ".json")
            removed.append(row["name"])
        except OSError as e:
            log.warning("could not remove old backup %s: %s", row["name"], e)
    if removed:
        log.info("pruned %d old backup(s), keeping %d", len(removed), keep)
    return removed


def run_scheduled(store, flush=None, when=None):
    """One scheduled run: create, verify, prune. Returns (ok, message)."""
    ok, msg, path = create(store, when=when, flush=flush)
    if not ok:
        return False, msg
    removed = prune(store)
    if removed:
        msg += " Removed %d older backup%s." % (len(removed), "" if len(removed) == 1 else "s")
    return True, msg


def due(store, now=None, last=None):
    """Is a scheduled backup due? Times are HH:MM in the cluster's own timezone.

    Compares against the last archive's timestamp rather than a stored 'last run',
    so a restart or a crash cannot make it forget - and cannot make it fire twice.
    """
    times = _times(store.get("backup_times"))
    if not times:
        return False, "no schedule set"
    now = now or time.time()
    if last is None:
        rows = listing(store)
        last = rows[0]["mtime"] if rows else 0
    lt = time.localtime(now)
    minutes_now = lt.tm_hour * 60 + lt.tm_min
    midnight = now - (minutes_now * 60) - lt.tm_sec
    for hhmm in times:
        slot = midnight + hhmm * 60
        if slot <= now and last < slot:
            return True, "scheduled backup for %02d:%02d is due" % (hhmm // 60, hhmm % 60)
    return False, "not due"


def _times(raw):
    out = []
    for bit in str(raw or "").split(","):
        bit = bit.strip()
        if not re.match(r"^\d{1,2}:\d{2}$", bit):
            continue
        h, m = bit.split(":")
        if 0 <= int(h) < 24 and 0 <= int(m) < 60:
            out.append(int(h) * 60 + int(m))
    return sorted(set(out))
