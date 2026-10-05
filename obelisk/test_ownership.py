"""
Everything the game writes belongs to the game's user before an apply restarts it.

From 4 October: an update's precheck found shared/Config/GameUserSettings.ini and the
instances/*/Saved/clusters links owned by root. A launch refuses on paths the server
cannot write, and in an apply the launch comes after the stop - so found there, it
leaves the cluster down. These are fixed first, and each fix is said.

The filesystem is handed in, so this runs the same on any machine.
"""

import sys
from types import SimpleNamespace

from . import cluster, layout

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


ROOT = "/ark"
# path -> (uid, gid). Directories are the keys that have children below.
TREE = {
    "/ark": (7777, 7777),
    "/ark/shared": (7777, 7777),
    "/ark/shared/Config": (7777, 7777),
    "/ark/shared/Config/GameUserSettings.ini": (0, 0),
    "/ark/shared/SavedArks": (7777, 7777),
    "/ark/shared/SavedArks/_corrupt-evidence": (0, 0),
    "/ark/shared/SavedArks/_corrupt-evidence/Aberration_WP.ark": (0, 0),
    "/ark/instances": (7777, 7777),
    "/ark/instances/island": (7777, 7777),
    "/ark/instances/island/Saved": (7777, 7777),
    "/ark/instances/island/Saved/clusters": (0, 0),          # a link: changed, not followed
    "/ark/obelisk": (0, 0),
    "/ark/obelisk/stop-guard.py": (0, 0),
    "/ark/ServerFiles": (7777, 7777),
    "/ark/ServerFiles/stop-guard.py": (0, 0),               # skipped by name anywhere
    "/ark/ServerFiles/ShooterGame": (1000, 100),
}
LINKS = {"/ark/instances/island/Saved/clusters"}
DIRS = {"/ark", "/ark/shared", "/ark/shared/Config", "/ark/shared/SavedArks",
        "/ark/shared/SavedArks/_corrupt-evidence", "/ark/instances",
        "/ark/instances/island", "/ark/instances/island/Saved", "/ark/obelisk",
        "/ark/ServerFiles", "/ark/ServerFiles/ShooterGame", "/ark/cluster"}


def walk(root, topdown=True, followlinks=False):
    """os.walk over TREE: top-down, honouring pruning, listing links as dirs (as
    os.walk does for a link to a folder) without entering them."""
    check("the walk never follows links", followlinks is False, followlinks)
    stack = [root]
    while stack:
        d = stack.pop(0)
        kids = sorted(p for p in TREE if p != d and p.rsplit("/", 1)[0] == d)
        dirs = [k.rsplit("/", 1)[1] for k in kids if k in DIRS or k in LINKS]
        files = [k.rsplit("/", 1)[1] for k in kids if k not in DIRS and k not in LINKS]
        yield d, dirs, files
        stack[0:0] = ["%s/%s" % (d, n) for n in dirs if "%s/%s" % (d, n) not in LINKS]


changed = []


def lstat(p):
    uid, gid = TREE[p]
    return SimpleNamespace(st_uid=uid, st_gid=gid)


def lchown(p, uid, gid):
    changed.append(p)
    TREE[p] = (uid, gid)


fixed, failed = layout.fix_ownership(ROOT, walk=walk, lstat=lstat, lchown=lchown)
fixed_paths = [f[0] for f in fixed]
check("a root-owned config file is handed to the server",
      "/ark/shared/Config/GameUserSettings.ini" in fixed_paths, fixed_paths)
check("so is a root-owned clusters link, itself",
      "/ark/instances/island/Saved/clusters" in fixed_paths, fixed_paths)
check("and anything else not 7777:7777",
      "/ark/ServerFiles/ShooterGame" in fixed_paths, fixed_paths)
check("what it was owned by before is kept for the record",
      ("/ark/shared/Config/GameUserSettings.ini", 0, 0) in fixed, fixed)
check("_corrupt-evidence is left alone, and not walked into",
      not any("_corrupt-evidence" in p for p in changed), changed)
check("Obelisk's own stop guard is left alone, wherever it is",
      not any(p.endswith("stop-guard.py") for p in changed), changed)
check("and so is the folder it is generated into",
      "/ark/obelisk" not in changed, changed)
check("paths that are already right are not touched",
      "/ark/shared" not in changed and "/ark" not in changed, changed)
check("nothing failed", failed == [], failed)

# Run again: a tree that is right costs a walk and nothing else.
changed.clear()
fixed2, _ = layout.fix_ownership(ROOT, walk=walk, lstat=lstat, lchown=lchown)
check("a second pass changes nothing", fixed2 == [] and changed == [], changed)


def refuse_chown(p, uid, gid):
    raise PermissionError("Operation not permitted")


TREE["/ark/cluster"] = (0, 0)
_f, failed = layout.fix_ownership(ROOT, walk=walk, lstat=lstat, lchown=refuse_chown)
check("a chown that is refused is reported, not raised",
      failed and failed[0][0] == "/ark/cluster", failed)
TREE.pop("/ark/cluster")

check("with no POSIX ownership (a dev box) it does nothing and says nothing wrong",
      layout.fix_ownership(ROOT, walk=walk, lstat=lstat, lchown=None)[1] == []
      if not hasattr(__import__("os"), "lchown") else True)


# ---------------------------------------------------------------- the events

said = []


def say(event, text, level="info", detail=None, **fields):
    said.append((event, text, level, detail))


many = [("/ark/ServerFiles/f%d" % i, 0, 0) for i in range(40)]
ok, why = cluster.fix_ownership({}, ark_root=ROOT, say=say,
                                fix=lambda root: (many, []), check=lambda root: [])
fixed_ev = [s for s in said if s[0] == "ownership.fixed"]
check("each fix is an event of its own, up to a limit",
      len(fixed_ev) == cluster.OWNERSHIP_EVENTS + 1, len(fixed_ev))
check("naming the path and who owned it",
      "/ark/ServerFiles/f0" in fixed_ev[0][1] and "0:0" in fixed_ev[0][1], fixed_ev[0])
check("and the rest are one summary carrying every path in its detail",
      "15 more" in fixed_ev[-1][1] and "/ark/ServerFiles/f39" in (fixed_ev[-1][3] or ""),
      fixed_ev[-1][:2])
check("a tree it could fix is fine to apply", ok, why)

said.clear()
ok, why = cluster.fix_ownership(
    {}, ark_root=ROOT, say=say,
    fix=lambda root: ([], [("/ark/shared", "Operation not permitted")]),
    check=lambda root: ["/ark/shared (owned by 0:0, mode 755)"])
check("a path it could not fix is said as a warning",
      any(s[0] == "ownership.failed" and s[2] == "warning" for s in said), said)
check("and if the server still cannot write, the apply is told no",
      not ok and "/ark/shared" in why, why)

said.clear()
ok, why = cluster.fix_ownership({}, ark_root=ROOT, say=say,
                                fix=lambda root: ([], []), check=lambda root: [])
check("a tree that is already right says nothing", ok and said == [], said)


print("\nFAILURES: %s" % fails if fails else "\nall ownership tests passed")
sys.exit(1 if fails else 0)
