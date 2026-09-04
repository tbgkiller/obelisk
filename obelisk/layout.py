"""
Two folders, because they hold two different kinds of thing.

**Obelisk data** is the definition: settings, the map selection, the mod list and its
order, ports, RAM, credentials, snapshots. Kilobytes. All of it is backed up, because
none of it can be fetched again from anywhere.

**Ark data** is the bulk: the server install, the mods it downloads, and the worlds.
Tens of gigabytes, and it belongs on fast storage - ASA is very I/O hungry. Only part
of it is worth backing up. The saves are irreplaceable; the server install and the mods
are not, because the mod *ids* live in Obelisk data and the files come back from those.

That asymmetry is the whole reason for the split. Backing up the pair as one folder
would mean carrying 20+ GB of re-downloadable files to protect a few hundred megabytes
that actually matter, and a backup that large is one nobody moves off the machine.

This is not the duplicate-field trap that APPDATA was. That asked for one folder twice
and let the two answers disagree silently. These are two paths because they are two
places, with different contents and different storage needs.

The one rule inherited from the old layout: every path a save lives at is inside the
Ark root, so "copy the portable part of the root" is complete by construction, and
verify() says so out loud if a link ever points out of it again.
"""

import logging, os

log = logging.getLogger("obelisk.layout")

# The server image runs as this user. Docker creates a missing bind-mount source as
# root, and Obelisk itself runs as root, so every folder the game has to write would
# otherwise be root-owned and the server would come up unable to install itself: it
# loops on "Permission denied", downloads nothing, and looks like a slow first start.
SERVER_UID = 7777
SERVER_GID = 7777

# ---- Obelisk data: the definition. Small, and entirely worth keeping.
STORE_NAME = "settings.json"
BACKUPS = "backups"

# ---- Ark data: the bulk.
SERVERFILES = "ServerFiles"   # the game install - re-downloads
MODS = "Mods"                 # downloaded mods - re-download from the ids in the store
SHARED = "shared"             # the shared config and the real per-map saves
CLUSTER = "cluster"           # cross-map transfer data (survivors, tames, items)
INSTANCES = "instances"       # per-map Saved: that map's config, logs, crash reports

SAVED_ARKS = SHARED + "/SavedArks"
SHARED_CFG = SHARED + "/Config"

# What a backup takes from the Ark root, and what it deliberately leaves behind.
ARK_PORTABLE = (SHARED, CLUSTER, INSTANCES)
ARK_EXCLUDED = (SERVERFILES, MODS)

# Where each folder is mounted inside the container.
OBELISK_MOUNT = "/data"
ARK_MOUNT = "/ark"


def obelisk_paths(root):
    root = str(root).rstrip("/")
    return {"root": root,
            "store": "%s/%s" % (root, STORE_NAME),
            "backups": "%s/%s" % (root, BACKUPS)}


def ark_paths(root):
    root = str(root).rstrip("/")
    return {
        "root": root,
        "serverfiles": "%s/%s" % (root, SERVERFILES),
        "mods": "%s/%s" % (root, MODS),
        "shared": "%s/%s" % (root, SHARED),
        "saved_arks": "%s/%s" % (root, SAVED_ARKS),
        "shared_config": "%s/%s" % (root, SHARED_CFG),
        "cluster": "%s/%s" % (root, CLUSTER),
        "instances": "%s/%s" % (root, INSTANCES),
    }


def instance_dir(root, map_key):
    """Where one map's own Saved folder lives, under the Ark root."""
    return "%s/%s/%s/Saved" % (str(root).rstrip("/"), INSTANCES, map_key)


def root_of(store):
    """The Obelisk data folder, as this container sees it.

    The store sits at the top of it, so the store knows where it is without being told.
    """
    return os.path.dirname(os.path.abspath(store.path))


def ark_root_of(store, environ=None):
    """The Ark data folder, as this container sees it.

    A fixed mount point rather than the host path: the host path is a different string
    and only matters when writing a compose file.
    """
    environ = os.environ if environ is None else environ
    return (environ.get("OBELISK_ARK") or ARK_MOUNT).rstrip("/")


def serverfiles_dir(store):
    """The game install, inside the Ark folder with the other large files."""
    return ark_paths(store.get("appdata"))["serverfiles"]


def mods_dir(store):
    return ark_paths(store.get("appdata"))["mods"]


def ensure_obelisk(root, makedirs=None):
    makedirs = makedirs or (lambda p: os.makedirs(p, exist_ok=True))
    p = obelisk_paths(root)
    made = []
    for d in (p["root"], p["backups"]):
        makedirs(d)
        made.append(d)
    return made


def ensure_ark(root, map_keys=(), makedirs=None, chown=None):
    """Create the Ark layout, owned by the user the server runs as. Safe to repeat."""
    makedirs = makedirs or (lambda p: os.makedirs(p, exist_ok=True))
    p = ark_paths(root)
    made = []
    for d in (p["root"], p["serverfiles"], p["mods"], p["shared"], p["saved_arks"],
              p["shared_config"], p["cluster"], p["instances"]):
        makedirs(d)
        made.append(d)
    for key in map_keys:
        d = instance_dir(root, key)
        makedirs(d)
        made.append(d)
    give_to_server(made, chown=chown)
    return made


def give_to_server(paths, chown=None, uid=SERVER_UID, gid=SERVER_GID):
    """Hand these folders to the server's user.

    Without this the game server cannot write into its own data folders. It does not
    fail loudly - it retries, forever, having installed nothing, which reads as a slow
    download rather than a broken one.

    Best effort: a filesystem with no ownership (or a host that refuses) is reported and
    carried on with, because refusing to launch over it would be worse.
    """
    # os.chown does not exist on every platform - a dev box is not the deployment
    # target, and "this OS has no ownership" is not a failure worth shouting about.
    chown = chown or getattr(os, "chown", None)
    if chown is None:
        return True
    failed = []
    for d in paths:
        try:
            chown(d, uid, gid)
        except Exception as e:                 # no POSIX ownership, or not permitted
            failed.append("%s (%s)" % (d, e))
    if failed:
        log.warning("could not give %d folder(s) to uid %d - the server may not be able "
                    "to write to them: %s", len(failed), uid, "; ".join(failed[:3]))
    return not failed


def _resolve(link, target, root):
    if os.path.isabs(target):
        return os.path.normpath(target)
    return os.path.normpath(os.path.join(os.path.dirname(link), target))


def verify(root, walker=None, readlink=None, exists=None):
    """Problems that would make a copy of this root incomplete.

    The one that matters: a symlink pointing outside the root. That is exactly how the
    old layout lost every map's saves - the link resolved only inside a container, so a
    backup of the tree captured the link and none of the data.
    """
    root_abs = os.path.normpath(str(root).rstrip("/"))
    walker = walker or os.walk
    readlink = readlink or os.readlink
    exists = exists or os.path.exists

    problems = []
    for dirpath, dirnames, filenames in walker(root_abs):
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            try:
                target = readlink(full)
            except OSError:
                continue                      # not a symlink
            dest = _resolve(full, target, root_abs)
            inside = dest == root_abs or dest.startswith(root_abs + os.sep) \
                or dest.startswith(root_abs + "/")
            if not inside:
                problems.append(
                    "%s points outside the data root (-> %s). A copy of the root would "
                    "not contain what it refers to." % (full, target))
            elif not exists(dest):
                problems.append("%s is a broken link (-> %s)." % (full, target))
    return problems

def not_writable_by_server(root, stat=None, uid=SERVER_UID, gid=SERVER_GID):
    """Folders the server's user could not write to. [] means it can get to work.

    The failure this catches is silent and expensive: the server loops on "Permission
    denied", installs nothing, and reports `running (health: starting)` forever. Checked
    before a launch so it is a refusal with a fix rather than an hour of watching a
    progress bar that was never moving.
    """
    stat = stat or os.stat
    problems = []
    for name, path in sorted(ark_paths(root).items()):
        try:
            st = stat(path)
        except OSError:
            continue                       # not created yet; ensure_ark will make it
        mode = getattr(st, "st_mode", 0)
        owner_ok = getattr(st, "st_uid", uid) == uid and (mode & 0o200)
        group_ok = getattr(st, "st_gid", gid) == gid and (mode & 0o020)
        other_ok = mode & 0o002
        if not (owner_ok or group_ok or other_ok):
            problems.append("%s (owned by %s:%s, mode %o)"
                            % (path, getattr(st, "st_uid", "?"),
                               getattr(st, "st_gid", "?"), mode & 0o777))
    return problems
