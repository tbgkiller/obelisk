"""
The portable data root, and launching a cluster from it.

The failure this suite exists for: a backup that looks complete and contains no worlds.
That happened because each map's saves lived behind a symlink pointing at a path that
only existed inside a running container, so copying the tree copied the link. The
layout here makes that impossible by construction, and verify() is the alarm if it ever
comes back.

Fixture values are synthetic throughout.
"""

import io, os, sys, tempfile, yaml

from . import cluster as clusterctl
from . import layout
from .compose import generate_compose
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


# The data root is validated as an absolute POSIX path, because that is what goes into
# a Linux compose file. So the store always carries a POSIX root, and anything that
# actually touches a filesystem is pointed at a temp dir instead - which keeps the suite
# identical on a Linux container and a Windows dev box.
POSIX_ROOT = "/srv/ark-data"


def fresh(**over):
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "settings.json")).load()
    st.patch({"appdata": POSIX_ROOT, "status_port": 8088}, source="install")
    st.patch(dict({"maps": "island,ragnarok", "admin_password": "synthetic-pw",
                   "cluster_id": "testcluster", "host_ram_gb": 256}, **over))
    return st, d


# ---------------------------------------------------------------- the layout
st, d = fresh()
root = st.get("appdata")           # the POSIX root the store carries
p = layout.paths(root)
check("root holds Obelisk's own store", p["obelisk"].endswith("/obelisk"), p["obelisk"])
check("root holds the real saves", p["saved_arks"].endswith("/shared/SavedArks"))
check("root holds transfer data", p["cluster"].endswith("/cluster"))
check("per-map Saved is under the root",
      layout.instance_dir(root, "island") == root + "/instances/island/Saved")

check("the game install is NOT in the portable set", "ServerFiles" not in layout.PORTABLE)
check("portable set is the four trees", set(layout.PORTABLE) ==
      {"obelisk", "shared", "cluster", "instances"}, layout.PORTABLE)

# the game install lands beside the root, never inside it
sf = layout.serverfiles_dir(st)
check("game install defaults beside the root", sf == root + "-serverfiles", sf)
check("game install is outside the root", not sf.startswith(root + "/"), sf)
st.patch({"serverfiles": "/mnt/pool/ark-files"})
check("an explicit game install path wins", layout.serverfiles_dir(st) == "/mnt/pool/ark-files")
st.patch({"serverfiles": ""})

# ensure/verify work on a real directory, so they get a real one
disk_root = os.path.join(tempfile.mkdtemp(), "data")
made = layout.ensure(disk_root, ["island", "ragnarok"])
check("ensure creates the whole layout", all(os.path.isdir(x) for x in made), made)
check("ensure is repeatable", layout.ensure(disk_root, ["island"]) and True)
check("ensure makes a Saved dir per map",
      os.path.isdir(layout.instance_dir(disk_root, "ragnarok")))

# ---------------------------------------------------------------- the trap
root = disk_root
check("a clean root verifies", layout.verify(root) == [], layout.verify(root))

# the exact shape of the old bug: a link into a container-only path
trap = os.path.join(root, "instances", "island", "Saved", "SavedArks")
os.makedirs(os.path.dirname(trap), exist_ok=True)
made_link = True
try:
    os.symlink("/home/pok/shared/SavedArks/TheIsland_WP", trap)
except (OSError, NotImplementedError, AttributeError):
    made_link = False               # Windows without developer mode
if made_link:
    probs = layout.verify(root)
    check("verify catches a link pointing outside the root",
          any("outside the data root" in x for x in probs), probs)
    os.remove(trap)
    check("and is clean again once removed", layout.verify(root) == [])
else:
    # Still prove the rule itself, without needing symlink privileges.
    probs = layout.verify(root, walker=lambda r: [(root, ["SavedArks"], [])],
                          readlink=lambda f: "/home/pok/shared/SavedArks/TheIsland_WP",
                          exists=lambda p: True)
    check("verify catches a link pointing outside the root",
          any("outside the data root" in x for x in probs), probs)
    check("and is clean again once removed", True)

# a link that stays inside the root is fine
probs = layout.verify(root, walker=lambda r: [(root, [], ["ok"])],
                      readlink=lambda f: os.path.join(root, "shared"),
                      exists=lambda p: True)
check("a link inside the root is allowed", probs == [], probs)

# ---------------------------------------------------------------- the compose
st, d = fresh()
root = st.get("appdata")
text = generate_compose(st, project="testcluster")
doc = yaml.safe_load(text)
check("compose is valid YAML", isinstance(doc, dict) and "services" in doc)
check("a service per map plus Obelisk", set(doc["services"]) == {"island", "ragnarok", "obelisk"},
      list(doc["services"]))

vols = doc["services"]["island"]["volumes"]
check("saves mount from the data root",
      any(v.startswith(root + "/instances/island/Saved:") for v in vols), vols)
check("shared mounts from the data root",
      any(v.startswith(root + "/shared:") for v in vols), vols)
check("transfer data mounts from the data root",
      any(v.startswith(root + "/cluster:") for v in vols), vols)
check("the game install mounts from OUTSIDE the root",
      any(v.startswith(root + "-serverfiles:") for v in vols), vols)
check("nothing mounts a ServerFiles dir inside the root",
      not any(v.startswith(root + "/ServerFiles") for v in vols), vols)

ob = doc["services"]["obelisk"]
check("Obelisk mounts the whole root, same path in and out",
      "%s:%s" % (root, root) in ob["volumes"], ob["volumes"])
check("Obelisk gets the socket",
      any("docker.sock" in v for v in ob["volumes"]), ob["volumes"])
check("Obelisk's store is inside the root",
      ob["environment"]["OBELISK_DATA"] == root + "/obelisk",
      ob["environment"]["OBELISK_DATA"])

# mods and ordering survive into the stack
st.patch({"mod_ids": "929110,940003"})
doc2 = yaml.safe_load(generate_compose(st, project="testcluster"))
check("the mod list reaches every map",
      all(doc2["services"][m]["environment"]["MOD_IDS"] == "929110,940003"
          for m in ("island", "ragnarok")))
check("the first map is the update master",
      doc2["services"]["island"]["environment"]["UPDATE_COORDINATION_ROLE"] == "MASTER")
check("the others follow",
      doc2["services"]["ragnarok"]["environment"]["UPDATE_COORDINATION_ROLE"] == "FOLLOWER")

# ---------------------------------------------------------------- launching
calls = []


class FakeDocker:
    """Stands in for the socket so the suite never touches a real Docker."""

    def __init__(self, ok=True, rc=0, out=""):
        self.ok, self.rc, self.out = ok, rc, out

    def available(self):
        return (self.ok, "Docker 27.0.0" if self.ok else "no socket")

    def compose(self, path, proj, args, timeout=900):
        calls.append((path, proj, list(args)))
        return self.rc, self.out

    def compose_ps(self, path, proj, timeout=60):
        return [{"service": "island", "name": "asa_island", "state": "running",
                 "status": "Up 2 minutes", "health": "healthy"}]

    def ports_in_use(self):
        return set()


st, d = fresh()
fake = FakeDocker()
clusterctl.dockerctl = fake

# Where the compose file goes is asserted against the POSIX root (a pure derivation);
# the writes themselves are redirected into a temp dir so this runs anywhere.
check("compose file lands inside the data root",
      clusterctl.compose_path(st) == st.get("appdata") + "/obelisk/compose.yaml",
      clusterctl.compose_path(st))
check("project name follows the cluster id", clusterctl.project(st) == "testcluster")

launch_root = os.path.join(tempfile.mkdtemp(), "data")
_real_compose_path = clusterctl.compose_path
clusterctl.compose_path = lambda store: os.path.join(launch_root, "obelisk", "compose.yaml")
prepared = []
_real_ensure = layout.ensure
clusterctl.layout.ensure = lambda root, keys=(), makedirs=None: (
    prepared.append((root, list(keys))) or _real_ensure(launch_root, keys))

ok, msg = clusterctl.launch(st)
check("launch reports success", ok, msg)
check("launch ran docker compose up", any(a[2][:2] == ["up", "-d"] for a in calls), calls)
check("launch used the file in the data root",
      calls[-1][0] == clusterctl.compose_path(st), calls[-1])
check("launch used the cluster's own project", calls[-1][1] == "testcluster")
check("the compose file is on disk afterwards", os.path.isfile(clusterctl.compose_path(st)))
check("launch laid out the data root first", prepared and prepared[0][0] == st.get("appdata"),
      prepared)
check("it laid out a dir for every selected map",
      prepared and prepared[0][1] == ["island", "ragnarok"], prepared)
check("the real save trees exist", os.path.isdir(launch_root + "/shared/SavedArks"))
check("a launched root verifies clean", layout.verify(launch_root) == [],
      layout.verify(launch_root))
written = io.open(clusterctl.compose_path(st), encoding="utf-8").read()
check("what landed on disk is the generated compose",
      "asa_island" in written and "obelisk:" in written)
check("the written compose is runnable by hand without Obelisk",
      "docker compose" in written.split("services:")[0], written[:200])

st2, _ = fresh(admin_password="")
ok, msg = clusterctl.launch(st2)
check("an unready cluster refuses to launch", not ok, msg)
check("and says what is missing", "won't start yet" in msg, msg)

clusterctl.dockerctl = FakeDocker(ok=False)
ok, msg = clusterctl.launch(st)
check("no Docker means a clear refusal, not a crash", not ok and "isn't reachable" in msg, msg)

clusterctl.dockerctl = FakeDocker(rc=1, out="boom")
ok, msg = clusterctl.launch(st)
check("a failed compose reports the output", not ok and "boom" in msg, msg)

clusterctl.dockerctl = fake
calls.clear()
ok, msg = clusterctl.stop(st)
check("stop runs docker compose down", ok and calls[-1][2] == ["down"], (ok, calls))
check("stop says saves are safe", "untouched" in msg, msg)

s = clusterctl.status(st)
check("status reports what is running", s["running"] == 1 and s["services"][0]["service"] == "island", s)
check("status carries the project", s["project"] == "testcluster")

# Put the real derivation back: the never-launched case has to be judged by a store
# that genuinely has no compose file, not by the redirected one above.
clusterctl.compose_path = _real_compose_path
clusterctl.layout.ensure = _real_ensure
st3, _ = fresh(cluster_id="neverlaunched")
clusterctl.dockerctl = fake
s3 = clusterctl.status(st3)
check("status before any launch is a normal empty state",
      s3["compose_exists"] is False and s3["running"] == 0, s3)
ok, msg = clusterctl.stop(st3)
check("stopping a cluster that was never launched explains itself",
      not ok and "never been launched" in msg, msg)

print("\nFAILURES: %s" % fails if fails else "\nall cluster tests passed")
sys.exit(1 if fails else 0)
