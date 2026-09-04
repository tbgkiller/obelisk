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
from .plan import build_plan
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
root = st.get("appdata")           # the Ark folder's host path
p = layout.ark_paths(root)
check("ark data holds the real saves", p["saved_arks"].endswith("/shared/SavedArks"))
check("ark data holds transfer data", p["cluster"].endswith("/cluster"))
check("ark data holds the server install", p["serverfiles"].endswith("/ServerFiles"))
check("ark data holds the mods", p["mods"].endswith("/Mods"))
check("per-map Saved is under the ark root",
      layout.instance_dir(root, "island") == root + "/instances/island/Saved")

# what a backup takes from the ark folder, and what it leaves
check("the game install is NOT in the portable set",
      layout.SERVERFILES not in layout.ARK_PORTABLE)
check("the mods are NOT in the portable set", layout.MODS not in layout.ARK_PORTABLE)
check("the portable part is saves, transfers and per-map config",
      set(layout.ARK_PORTABLE) == {"shared", "cluster", "instances"}, layout.ARK_PORTABLE)
check("and the excluded part is named", set(layout.ARK_EXCLUDED) == {"ServerFiles", "Mods"},
      layout.ARK_EXCLUDED)

check("the game install lives inside the ark folder",
      layout.serverfiles_dir(st) == root + "/ServerFiles", layout.serverfiles_dir(st))
check("so do the mods", layout.mods_dir(st) == root + "/Mods")

# ensure/verify work on a real directory, so they get a real one
disk_root = os.path.join(tempfile.mkdtemp(), "data")
made = layout.ensure_ark(disk_root, ["island", "ragnarok"])
check("ensure creates the whole layout", all(os.path.isdir(x) for x in made), made)
check("ensure is repeatable", layout.ensure_ark(disk_root, ["island"]) and True)
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
# Only the maps: Obelisk manages this stack, it is not in it.
check("a service per map, and nothing else", set(doc["services"]) == {"island", "ragnarok"},
      list(doc["services"]))

vols = doc["services"]["island"]["volumes"]
check("saves mount from the ark folder",
      any(v.startswith(root + "/instances/island/Saved:") for v in vols), vols)
check("shared mounts from the ark folder",
      any(v.startswith(root + "/shared:") for v in vols), vols)
check("transfer data mounts from the ark folder",
      any(v.startswith(root + "/cluster:") for v in vols), vols)
check("the game install mounts from inside the ark folder",
      any(v.startswith(root + "/ServerFiles:") for v in vols), vols)

# How Obelisk itself is installed is no longer described by the file it generates -
# it is the manager, not a member. That contract lives in the compose example, which is
# what an operator actually runs to install it, so it is checked there.
example = io.open("docker/compose.example.yml", encoding="utf-8").read()
ex = yaml.safe_load(example)["services"]["obelisk"]
check("Obelisk installs with its own settings folder at /data",
      any(v.endswith(":/data") for v in ex["volumes"]), ex["volumes"])
check("and the ark folder at /ark",
      any(v.endswith(":/ark") for v in ex["volumes"]), ex["volumes"])
check("the two are different folders",
      [v.split(":")[0] for v in ex["volumes"] if v.endswith(":/data")] !=
      [v.split(":")[0] for v in ex["volumes"] if v.endswith(":/ark")], ex["volumes"])
check("and it gets the socket",
      any("docker.sock" in v for v in ex["volumes"]), ex["volumes"])

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
# The compose file is written where Obelisk can see it - the container-side root,
# which the store locates itself from - not the host path that goes inside the file.
import os as _os
from . import layout as _layout
# The compose file belongs with the definition, not with the game files: it is
# generated from the settings and is small.
check("compose file lands in the Obelisk data folder",
      clusterctl.compose_path(st) == _layout.root_of(st) + "/compose.yaml",
      clusterctl.compose_path(st))
check("project name follows the cluster id", clusterctl.project(st) == "testcluster")

launch_root = os.path.join(tempfile.mkdtemp(), "data")
_real_compose_path = clusterctl.compose_path
clusterctl.compose_path = lambda store: os.path.join(launch_root, "obelisk", "compose.yaml")
prepared = []
_real_ensure = layout.ensure_ark
clusterctl.layout.ensure_ark = lambda root, keys=(), makedirs=None: (
    prepared.append((root, list(keys))) or _real_ensure(launch_root, keys))
clusterctl.layout.ensure_obelisk = lambda root, makedirs=None: []

ok, msg = clusterctl.launch(st)
check("launch reports success", ok, msg)
check("launch ran docker compose up", any(a[2][:2] == ["up", "-d"] for a in calls), calls)
check("launch used the file in the data root",
      calls[-1][0] == clusterctl.compose_path(st), calls[-1])
check("launch used the cluster's own project", calls[-1][1] == "testcluster")
check("the compose file is on disk afterwards", os.path.isfile(clusterctl.compose_path(st)))
check("launch laid out the ark folder first",
      prepared and prepared[0][0] == layout.ark_root_of(st), prepared)
check("it laid out a dir for every selected map",
      prepared and prepared[0][1] == ["island", "ragnarok"], prepared)
check("the real save trees exist", os.path.isdir(launch_root + "/shared/SavedArks"))
check("a launched root verifies clean", layout.verify(launch_root) == [],
      layout.verify(launch_root))
written = io.open(clusterctl.compose_path(st), encoding="utf-8").read()
check("what landed on disk is the generated compose",
      "asa_island" in written and "services:" in written)
check("and it does not contain the manager", "container_name: obelisk" not in written)
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
clusterctl.layout.ensure_ark = _real_ensure
st3, _ = fresh(cluster_id="neverlaunched")
clusterctl.dockerctl = fake
s3 = clusterctl.status(st3)
check("status before any launch is a normal empty state",
      s3["compose_exists"] is False and s3["running"] == 0, s3)
ok, msg = clusterctl.stop(st3)
check("stopping a cluster that was never launched explains itself",
      not ok and "never been launched" in msg, msg)


# ---- launching from an already-running Obelisk must not collide with itself
# The live failure: the generated stack contained a manager service publishing the same
# port the running manager was already bound to. Every launch was refused for clashing
# with itself, advising the operator to free a port that was already free.
st_self, _d = fresh(maps="island", cluster_id="selftest")
st_self.patch({"game_port_base": 7877, "rcon_port_base": 27920})
st_self.patch({"status_port": 18091}, source="install")

plan_self = build_plan(st_self, in_use_ports={18091, 8088, 7777, 27020})
check("a plan is not blocked by Obelisk's own port being in use",
      plan_self["ok"], plan_self["problems"])
check("and nothing advises freeing a port",
      not any("STATUS_PORT" in p for p in plan_self["problems"]), plan_self["problems"])

yml_self = generate_compose(st_self, project="selftest")
doc_self = yaml.safe_load(yml_self)
check("the generated stack has no manager service",
      "obelisk" not in doc_self["services"], list(doc_self["services"]))
check("it is only the maps", set(doc_self["services"]) == {"island"},
      list(doc_self["services"]))
check("the manager's port appears nowhere in it", "18091" not in yml_self,
      [l for l in yml_self.splitlines() if "18091" in l])
check("the cluster network is still defined", "selftest-net" in yml_self)

# a map is still not allowed to take the manager's port
st_clash, _d2 = fresh(maps="island", cluster_id="clash")
st_clash.patch({"status_port": 18091}, source="install")
st_clash.patch({"game_port_base": 18091})
pc = build_plan(st_clash, in_use_ports=set())
check("a map given the manager's port is still refused", not pc["ok"], pc["problems"])
check("and the reason names the map, not a phantom stack service",
      any("Obelisk's own web port" in p for p in pc["problems"]), pc["problems"])

# the manager joins the cluster network, so it can still reach the maps it made
joined = []
_fake_net = FakeDocker()
_fake_net.network_connect = lambda net, name, timeout=30: (
    joined.append((net, name)) or (True, "connected"))
clusterctl.dockerctl = _fake_net
clusterctl._join_network(st_self, environ={"HOSTNAME": "obelisk123"})
check("the manager joins the cluster's network after launch",
      joined == [("selftest-net", "obelisk123")], joined)
_fake_net.network_connect = lambda net, name, timeout=30: (False, "boom")
ok_j, _d3 = clusterctl._join_network(st_self, environ={"HOSTNAME": "obelisk123"})
check("a failed join is reported but does not fail the launch", ok_j is False)

print("\nFAILURES: %s" % fails if fails else "\nall cluster tests passed")
sys.exit(1 if fails else 0)
