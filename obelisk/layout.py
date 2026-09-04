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

import os

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


def ensure_ark(root, map_keys=(), makedirs=None):
    """Create the Ark layout. Safe to repeat."""
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
    return made


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
