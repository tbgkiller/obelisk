"""
The staging server, and the three ways it could quietly become part of the cluster.

Most of these checks are about isolation rather than function, because the failure
modes here are not "the staging server did not work" - they are "the staging server
worked, on the live cluster's files". The worst of them is the shared folder: the
server image recreates `SavedArks/<Map>` as a symlink into /home/pok/shared at every
start, so a staging instance pointed at the cluster's shared folder does not read the
live world, it *adopts* it and writes to it. That is the migration's symlink trap with
a new hat on, and it is one line of compose away at all times.

The swap is the other half. It is three renames over 12 GB of live install, so it is
simulated here on a fake filesystem: the end state is asserted, the coordination folders
are followed individually, and the sequence is run twice to prove it is its own inverse.
"""

import sys

from . import staging

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


class FakeStore:
    def __init__(self, **kw):
        self.values = dict(
            staging_mode="always", staging_map="scorched", staging_memory="10g",
            ark_image="acekorneya/asa_server:2_1_latest", timezone="America/Chicago",
            session_prefix="TBG", cluster_id="tbgcluster",
            admin_password="a-secret-nobody-should-see-twice",
            mod_ids="929110,940003,929420", passive_mods="")
        self.values.update(kw)

    def get(self, key, map_name=None):
        return self.values.get(key)


ARK = "/ark"
P = staging.paths(ARK)

# ---- the folders it owns, and the ones it must not
check("the staged tree is beside the live one, never inside it",
      P["staged"] == "/ark/ServerFiles.staging" and
      not P["staged"].startswith(P["live"] + "/"), P)
check("it has a shared folder of its own",
      P["shared"] == "/ark/staging/shared", P["shared"])
check("and an instance folder of its own", P["instance"].startswith("/ark/staging/"),
      P["instance"])

# The backup takes SHARED, CLUSTER and INSTANCES from the ark root. Everything the
# staging server owns sits outside all three, so the portable backup keeps ignoring it
# without anybody having to teach it a new exception.
from . import layout

portable = ["%s/%s" % (ARK, d) for d in layout.ARK_PORTABLE]
for name in ("staged", "instance", "shared"):
    check("the staging %s is outside the backup set" % name,
          not any(P[name].startswith(p + "/") or P[name] == p for p in portable),
          (P[name], portable))

# ---- the compose file, where isolation is actually spent
store = FakeStore()
text = staging.compose_text(store, "tbgcluster", ARK)

check("it runs its own server files",
      '"%s:/home/pok/arkserver"' % P["staged"] in text, text[:200])
check("the live server files are never mounted",
      "/ark/ServerFiles:" not in text and '"/ark/ServerFiles"' not in text)

# The one that would silently eat a live save.
check("it mounts its own shared folder",
      '"%s:/home/pok/shared"' % P["shared"] in text)
check("and never the cluster's - that would symlink its world onto a live map's save",
      '"/ark/shared:' not in text)

check("there is no cluster folder to transfer into", "/clusters" not in text)
check("and no published ports at all", "ports:" not in text)
check("its cluster id is its own, so it cannot join the real one",
      'CLUSTER_ID: "tbgcluster-staging"' in text)
check("it carries the cluster's full mod list - that is the whole point",
      'MOD_IDS: "929110,940003,929420"' in text)
check("it is its own update master, so nothing is waiting for another instance",
      'UPDATE_COORDINATION_ROLE: "MASTER"' in text and 'UPDATE_SERVER: "TRUE"' in text)
check("its name says it is not a game server",
      "STAGING - not a game server" in text)
check("it joins the cluster network rather than creating one",
      "external: true" in text)

check("a separate compose project, so the cluster's lifecycle never moves it",
      staging.project_name("tbgcluster") == "tbgcluster-staging")
check("and a container name that cannot collide with a map",
      staging.container_name("tbgcluster") not in
      ["asa-tbgcluster-%s" % m for m in ("island", "scorched", "center")],
      staging.container_name("tbgcluster"))

# always keeps it up; on_demand must not come back by itself after Obelisk stops it.
check("always-on restarts with the host",
      "restart: unless-stopped" in staging.compose_text(
          FakeStore(staging_mode="always"), "p", ARK))
check("on-demand does not restart itself once it has done its job",
      "restart: no" in staging.compose_text(
          FakeStore(staging_mode="on_demand"), "p", ARK))

check("an unknown map falls back to a real one rather than a broken MAP_NAME",
      staging.map_id(FakeStore(staging_map="not-a-map"))[1] == "scorched",
      staging.map_id(FakeStore(staging_map="not-a-map")))

# ---- modes
check("off means off", not staging.enabled(FakeStore(staging_mode="off")))
check("a mode nobody recognises is off, not a crash",
      staging.mode(FakeStore(staging_mode="banana")) == "off")
check("on_demand and always are both enabled",
      staging.enabled(FakeStore(staging_mode="on_demand")) and
      staging.enabled(FakeStore(staging_mode="always")))


# ---- the swap, simulated on a fake filesystem
#
# Three renames over 12 GB, so it is worth knowing the end state before it runs on a
# real one. Directories are dicts; renaming moves a subtree.
def make_tree():
    return {
        "/ark/ServerFiles": {"build": "old", ".pok-manager": "cluster-notices",
                             "update_coordination": "cluster-cycles",
                             "instance_flags": "cluster-flags"},
        "/ark/ServerFiles.staging": {"build": "new", ".pok-manager": "staging-notices",
                                     "update_coordination": "staging-cycles",
                                     "instance_flags": "staging-flags"},
    }


def fs_ops(tree):
    """rename/exists over paths like /ark/ServerFiles/.pok-manager."""
    def split(path):
        for root in sorted(tree, key=len, reverse=True):
            if path == root:
                return root, None
            if path.startswith(root + "/"):
                return root, path[len(root) + 1:]
        return None, None

    def exists(path):
        if path in tree:
            return True
        root, leaf = split(path)
        return bool(root and leaf and leaf in tree[root])

    def rename(src, dst):
        if src in tree:
            tree[dst] = tree.pop(src)
            return
        sroot, sleaf = split(src)
        droot, dleaf = split(dst)
        if not (sroot and sleaf and droot and dleaf):
            raise OSError("no such path %s -> %s" % (src, dst))
        tree[droot][dleaf] = tree[sroot].pop(sleaf)

    return rename, exists


tree = make_tree()
rename, exists = fs_ops(tree)
ok, done, problem = staging.apply_steps(staging.swap_steps(ARK), rename, exists)
check("the swap runs to the end", ok, problem)
check("the new build is now live", tree["/ark/ServerFiles"]["build"] == "new", tree)
check("and the old one is kept as the next staging tree - so the next prime is a delta",
      tree["/ark/ServerFiles.staging"]["build"] == "old", tree)
check("nothing is left at the intermediate name",
      "/ark/ServerFiles.previous" not in tree, sorted(tree))

# The correction that this whole plan exists for: after the trees change places the
# live tree is holding the staging server's coordination state, which describes an
# instance the cluster has never heard of.
for folder in staging.LIVE_ONLY:
    check("the cluster's %s stayed with the cluster" % folder,
          tree["/ark/ServerFiles"][folder].startswith("cluster-"),
          tree["/ark/ServerFiles"][folder])
    check("and the staging server kept its own %s" % folder,
          tree["/ark/ServerFiles.staging"][folder].startswith("staging-"),
          tree["/ark/ServerFiles.staging"][folder])

check("no holding name survives the swap",
      not any(k.endswith(".swapping") for d in tree.values() for k in d), tree)

# ---- rolling back is the same sequence again
rename, exists = fs_ops(tree)
ok, _, problem = staging.apply_steps(staging.rollback_steps(ARK), rename, exists)
check("the rollback runs", ok, problem)
check("the previous build is live again", tree["/ark/ServerFiles"]["build"] == "old", tree)
check("and the cluster's coordination is still the cluster's",
      tree["/ark/ServerFiles"][".pok-manager"] == "cluster-notices", tree)

# ---- a tree that has never run has none of the coordination folders yet
bare = {"/ark/ServerFiles": {"build": "old", ".pok-manager": "cluster-notices",
                             "update_coordination": "cluster-cycles",
                             "instance_flags": "cluster-flags"},
        "/ark/ServerFiles.staging": {"build": "new"}}
rename, exists = fs_ops(bare)
ok, _, problem = staging.apply_steps(staging.swap_steps(ARK), rename, exists)
check("a staging tree with no coordination folders still swaps", ok, problem)
check("and the cluster's are carried across intact",
      bare["/ark/ServerFiles"][".pok-manager"] == "cluster-notices", bare)

# ---- a swap that cannot start does not start
missing = {"/ark/ServerFiles": {"build": "old"}}
rename, exists = fs_ops(missing)
ok, done, problem = staging.apply_steps(staging.swap_steps(ARK), rename, exists)
check("nothing was staged means the swap refuses rather than half-moving",
      not ok and done == [], (ok, done))
check("and says which path was missing", "ServerFiles.staging" in problem, problem)

# ---- a swap that fails part way is put back
half = make_tree()
rename, exists = fs_ops(half)
calls = {"n": 0}


def flaky(src, dst):
    calls["n"] += 1
    if calls["n"] == 3:
        raise OSError("the disk said no")
    rename(src, dst)


ok, done, problem = staging.apply_steps(staging.swap_steps(ARK), flaky, exists)
check("a failure part way through stops", not ok, problem)
check("and reports what it was doing", "could not move" in problem, problem)
undone, undo_problem = staging.undo(done, rename, exists)
check("undoing it puts the live tree back", undone and
      half["/ark/ServerFiles"]["build"] == "old", (undo_problem, half))
check("and the staged tree back", half["/ark/ServerFiles.staging"]["build"] == "new", half)


# ---- the verdict: four gates, and each one has to be able to fail alone
IDS = "929110,940003,929420"
LIVE = {"929110": "7738786", "940003": "6830549", "929420": "8160173"}
GOOD_LOG = "\n".join(
    ["LogCFCore: Mod valid: Mod %s (%s)" % (p, p) for p in LIVE] +
    ["UShooterEngine::LoadGameMods with 3 mods"] +
    ["UShooterEngine::LoadGameMods Loading Mod ShooterGame/Mods/83374/%s_%s/a/b.uasset : %s"
     % (p, f, p) for p, f in LIVE.items()])

acf = lambda path: '"AppState" {\n\t"buildid"\t\t"25200000"\n}'

ok, problems, detail = staging.verify("/ark/ServerFiles.staging", GOOD_LOG, IDS,
                                      rcon_ok=True, target_build="25200000", read=acf)
check("a clean staging boot passes every gate", ok, problems)
check("and reports the build it proved", detail["build"] == "25200000", detail)
check("and the exact mod files it loaded", detail["loaded"]["929110"] == "7738786", detail)

ok, problems, _ = staging.verify("/ark/ServerFiles.staging", GOOD_LOG, IDS,
                                 rcon_ok=False, target_build="25200000", read=acf)
check("a server that never answered RCON fails, however healthy the container looked",
      not ok and any("RCON" in p for p in problems), problems)

ok, problems, _ = staging.verify("/ark/ServerFiles.staging", GOOD_LOG, IDS,
                                 rcon_ok=True, target_build="25999999", read=acf)
check("mods loading off the wrong build fails - it proved the wrong thing",
      not ok and any("being staged" in p for p in problems), problems)

thin = "\n".join(l for l in GOOD_LOG.splitlines() if "929420" not in l)
ok, problems, _ = staging.verify("/ark/ServerFiles.staging", thin, IDS,
                                 rcon_ok=True, target_build="25200000", read=acf)
check("a world that loaded without one of the mods fails",
      not ok and any("929420" in p for p in problems), problems)


def unreadable(path):
    raise OSError("not there")


ok, problems, _ = staging.verify("/ark/ServerFiles.staging", GOOD_LOG, IDS,
                                 rcon_ok=True, read=unreadable)
check("a staged tree with no readable build id fails rather than being assumed fine",
      not ok and any("no readable build" in p for p in problems), problems)

check("the summary of a good run says what was proved",
      "3 mod(s)" in staging.summary({"ok": True, "build": "25200000", "loaded": LIVE}),
      staging.summary({"ok": True, "build": "25200000", "loaded": LIVE}))
check("and of a bad one says why not",
      "did not come up cleanly" in staging.summary({"ok": False, "problems": ["x"]}))
check("and of no run at all does not pretend", "nothing has been staged" in
      staging.summary(None))

# ---- the admin password is in the compose file because POK needs it, and nowhere else
check("the compose file does not leak the password into a session name or a log line",
      text.count("a-secret-nobody-should-see-twice") == 1, text.count("a-secret"))


# ---- ownership, which is what actually broke this on a live host
#
# The staging server downloaded 325 MB and then aborted with "Permission denied",
# because two folders were owned by root. Both were folders this function believed it
# had made: one an intermediate that makedirs creates and chown never followed, the
# other the bind mount's *destination inside the staged tree*, which Docker invents as
# root when it is missing. So the property is not "did it chown what it listed" - it is
# "is every folder that has to exist actually in the list".
_made, _owned = [], []


def _mk(path):
    _made.append(path)


def _chown(path, uid, gid):
    _owned.append((path, uid, gid))


staging.ensure("/ark", makedirs=_mk, chown=_chown)
_want = [
    "/ark/ServerFiles.staging",
    "/ark/ServerFiles.staging/ShooterGame",
    "/ark/ServerFiles.staging/ShooterGame/Saved",
    "/ark/staging",
    "/ark/staging/instance",
    "/ark/staging/instance/Saved",
    "/ark/staging/shared",
]
for _w in _want:
    check("ensure creates %s" % _w, _w in _made, _made)
    check("and hands %s to the server's user" % _w,
          any(p == _w and uid == layout.SERVER_UID for p, uid, _g in _owned), _owned)

check("no folder is created without also being given away",
      sorted(_made) == sorted(p for p, _u, _g in _owned), (_made, _owned))
check("the folder the compose file mounts into is one ensure makes - Docker must never "
      "be the thing that creates it",
      "/home/pok/arkserver/ShooterGame/Saved" in text and
      "/ark/ServerFiles.staging/ShooterGame/Saved" in _made)

# ---- the container name stopped doubling the project suffix
check("the staging container is named for the cluster, not for the staging project",
      staging.container_name("tbgcluster") == "asa-tbgcluster-staging",
      staging.container_name("tbgcluster"))
check("which is what building it from the staging project produced",
      staging.container_name("tbgcluster") != "asa-tbgcluster-staging-staging")
check("and the compose file agrees with container_name",
      "container_name: %s" % staging.container_name("tbgcluster") in
      staging.compose_text(FakeStore(), "tbgcluster", ARK))

print("\nFAILURES: %s" % fails if fails else "\nall staging tests passed")
sys.exit(1 if fails else 0)
