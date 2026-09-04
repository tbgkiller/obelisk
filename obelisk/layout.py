"""
The portable data root.

One folder is the cluster: Obelisk's own store, every map's saves, the shared config
and the cross-map transfer data. Copy that folder and you have moved the cluster.

The game install is deliberately *not* in it. That is 20+ GB the update process will
fetch again for free, and leaving it out is the difference between a backup you can
actually move somewhere else and one you never will.

The layout this replaces put each map's real saves behind a symlink inside the map's
own instance folder, pointing at a path that only exists inside a running container.
Back up the instance folder and you got a dangling link and no world - a backup that
looks fine until the day it has to work. Here every path that matters lives inside the
root, so "copy the root" is complete by construction, and `verify()` says so out loud
if anything ever points outside it again.
"""

import os

# Everything below is relative to the data root.
OBELISK   = "obelisk"        # Obelisk's own store: settings.json, snapshots
SHARED    = "shared"         # the shared config and the real per-map saves
CLUSTER   = "cluster"        # cross-map transfer data (survivors, tames, items)
INSTANCES = "instances"      # per-map Saved: that map's config, logs, crash reports

SAVED_ARKS = SHARED + "/SavedArks"
SHARED_CFG = SHARED + "/Config"

# What a portable copy contains. ServerFiles is absent on purpose.
PORTABLE = (OBELISK, SHARED, CLUSTER, INSTANCES)


def paths(root):
    """Every directory the layout defines, as absolute paths."""
    root = str(root).rstrip("/")
    return {
        "root": root,
        "obelisk": "%s/%s" % (root, OBELISK),
        "shared": "%s/%s" % (root, SHARED),
        "saved_arks": "%s/%s" % (root, SAVED_ARKS),
        "shared_config": "%s/%s" % (root, SHARED_CFG),
        "cluster": "%s/%s" % (root, CLUSTER),
        "instances": "%s/%s" % (root, INSTANCES),
    }


def instance_dir(root, map_key):
    """Where one map's own Saved folder lives."""
    return "%s/%s/%s/Saved" % (str(root).rstrip("/"), INSTANCES, map_key)


def serverfiles_dir(store):
    """The game install - outside the portable root.

    Blank means "beside the root", which keeps a default install to one decision while
    still putting the big re-downloadable tree where a backup will not pick it up.
    """
    explicit = str(store.get("serverfiles") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return str(store.get("appdata")).rstrip("/") + "-serverfiles"


def ensure(root, map_keys=(), makedirs=None):
    """Create the layout. Returns the directories it made (or would make).

    `makedirs` is injectable so tests can run without a filesystem; the default is
    os.makedirs with exist_ok, so calling this on a live root is a no-op.
    """
    makedirs = makedirs or (lambda p: os.makedirs(p, exist_ok=True))
    made = []
    p = paths(root)
    for d in (p["root"], p["obelisk"], p["shared"], p["saved_arks"],
              p["shared_config"], p["cluster"], p["instances"]):
        makedirs(d)
        made.append(d)
    for key in map_keys:
        d = instance_dir(root, key)
        makedirs(d)
        made.append(d)
    return made


def _resolve(link, target, root):
    """Where a symlink actually lands, as an absolute path."""
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

def root_of(store):
    """The data root as *this* container sees it.

    Two different paths describe the same folder: the host path, which is what has to
    go into a generated compose file, and the path it is mounted at in here, which is
    what Obelisk actually reads and writes. Conflating them is what forced the install
    form to ask for the folder twice.

    The store always lives at <root>/obelisk/settings.json, so the store knows where the
    root is without being told - and without depending on the two paths being equal.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(store.path)))
