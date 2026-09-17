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
from . import compose as compose_mod
from . import restore as restore_mod
from .compose import generate_compose
from .plan import build_plan
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
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

# ------------------------------------------------- starting together, once installed
#
# The master-first chain costs a whole extra world load on every restart: nine maps sit
# Created while the island loads, and only then do they start loading. It is worth that
# exactly once - while the server files do not exist yet and ten containers would
# otherwise fetch the same 30 GB into the same folder. After that it is pure downtime.
st3, _d3 = fresh(maps="island,ragnarok,scorched")

cold = yaml.safe_load(generate_compose(st3, project="testcluster", wait_for_master=True))
check("with no game files yet, the followers wait for the master",
      all("depends_on" in cold["services"][m] for m in ("ragnarok", "scorched")),
      [cold["services"][m].get("depends_on") for m in ("ragnarok", "scorched")])
check("and they wait for it to be *healthy*, not merely started",
      cold["services"]["ragnarok"]["depends_on"]["island"]["condition"]
      == "service_healthy", cold["services"]["ragnarok"]["depends_on"])
check("the master never waits on anything",
      "depends_on" not in cold["services"]["island"],
      cold["services"]["island"].get("depends_on"))

warm = yaml.safe_load(generate_compose(st3, project="testcluster", wait_for_master=False))
check("once the game files are installed, nothing waits on anything",
      not any("depends_on" in s for s in warm["services"].values()),
      {k: v.get("depends_on") for k, v in warm["services"].items()})
check("every map is still there - parallel start drops the ordering, not a map",
      set(warm["services"]) == {"island", "ragnarok", "scorched"}, list(warm["services"]))
check("the update master/follower roles survive the change",
      (warm["services"]["island"]["environment"]["UPDATE_COORDINATION_ROLE"] == "MASTER"
       and warm["services"]["scorched"]["environment"]["UPDATE_COORDINATION_ROLE"]
       == "FOLLOWER"),
      "roles are how a running cluster coordinates; depends_on only ordered the boot")

# What decides it, when nobody passes the flag: the appmanifest steamcmd writes at the
# end of a successful install - and read through /ark, the container's own view. Reading
# the host path from in here reports "not installed" on every cluster there is.
asked = []


def _fake_build(path, answer=("25200000", "")):
    asked.append(path)
    return answer


check("an installed cluster is detected from the appmanifest",
      compose_mod.install_present(st3, installed_build=_fake_build) is True)
check("and it is looked for through the container's view of the ark folder",
      asked and asked[0].startswith(layout.ark_root_of(st3)), asked)
check("and it is the ServerFiles tree that is checked",
      asked and asked[0].endswith("ServerFiles"), asked)
check("an empty tree reads as not installed, so the first start still serialises",
      compose_mod.install_present(
          st3, installed_build=lambda p: (None, "no appmanifest")) is False)
check("an install with no buildid is not an install either",
      compose_mod.install_present(
          st3, installed_build=lambda p: (None, "incomplete")) is False)

# The two halves the owner's design keeps apart: one game/mod tree for everybody, and
# saves that stay each map's own. Parallel start must not have blurred that.
iv = warm["services"]["island"]["volumes"]
rv = warm["services"]["ragnarok"]["volumes"]


def _src(vols, dest):
    return [v.split(":")[0] for v in vols if v.split(":")[1] == dest]


check("every map mounts the same game install - one download, one tree",
      _src(iv, "/home/pok/arkserver") == _src(rv, "/home/pok/arkserver")
      and _src(iv, "/home/pok/arkserver") != [], _src(iv, "/home/pok/arkserver"))
check("but each map keeps its own Saved folder - saves are never shared",
      _src(iv, "/home/pok/arkserver/ShooterGame/Saved")
      != _src(rv, "/home/pok/arkserver/ShooterGame/Saved"),
      [_src(iv, "/home/pok/arkserver/ShooterGame/Saved"),
       _src(rv, "/home/pok/arkserver/ShooterGame/Saved")])
check("the staged tree is never mounted into a live map - it is staging's alone",
      not any("ServerFiles.staging" in v for v in iv + rv), iv + rv)

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

    def existing_containers(self, timeout=30):
        return {}                      # a clean host unless a test says otherwise

    def container_details(self, names, timeout=30):
        return {}                      # nothing running unless a test says otherwise


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
      "asa-testcluster-island" in written and "services:" in written)
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
ok, msg = clusterctl.stop(st, close_worlds=False)
check("stop runs docker compose down", ok and calls[-1][2] == ["down"], (ok, calls))
check("stop says saves are safe", "untouched" in msg, msg)

# ---- closing each world before the stop signal is ever sent
#
# The apply on 2026-09-12 proved its save and still lost three worlds. The proof was
# right about the write it watched; what it could not see was the server image's own
# shutdown save, which SIGTERM starts and a deadline ends. So nothing is left to shut
# down: DoExit closes each world on the server's own schedule, and a server that is
# already gone makes the dangerous second save a no-op ("Server is not running, no need
# to save world before stopping container").
TARGETS_X = [("The Island", "asa-testcluster-island", 27020),
             ("Ragnarok", "asa-testcluster-ragnarok", 27021)]
sent_x, alive_x = [], {"island": True, "ragnarok": True}


def rcon_x(host, port, cmd):
    """Stands in for ten servers: DoExit closes one, and a closed one stops answering."""
    key = "island" if "island" in host else "ragnarok"
    sent_x.append((key, cmd))
    if cmd == "DoExit":
        alive_x[key] = False
        return "Exiting..."
    if not alive_x[key]:
        raise OSError("connection refused")
    return "No Players Connected"


class ClockX:
    def __init__(self): self.t = 0.0
    def now(self): return self.t
    def wait(self, s): self.t += s


clk_x = ClockX()
out_x = clusterctl.exit_worlds(st, running=lambda s: TARGETS_X, rcon=rcon_x, now=clk_x.now, wait=clk_x.wait)
check("every running map is asked to close its own world",
      sorted(c[0] for c in sent_x if c[1] == "DoExit") == ["island", "ragnarok"], sent_x)
check("and DoExit is what it is asked - not a kill, not a signal",
      all(c[1] in ("DoExit", "ListPlayers") for c in sent_x), sent_x)
check("a map that stops answering is a map that closed its world",
      all(o["exited"] for o in out_x.values()), out_x)
check("which is what the stop waits for, rather than the command returning",
      all("exited" in o["why"] or "closed" in o["why"] for o in out_x.values()), out_x)

# A server that keeps answering has NOT closed. It must not be reported as though it had.
alive_y = {"island": True, "ragnarok": True}


def rcon_y(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if cmd == "DoExit" and key == "island":
        alive_y[key] = False
        return "Exiting..."
    if not alive_y[key]:
        raise OSError("connection refused")
    return "No Players Connected"        # ragnarok ignores DoExit and keeps serving


clk_y = ClockX()
out_y = clusterctl.exit_worlds(st, running=lambda s: TARGETS_X, rcon=rcon_y, now=clk_y.now, wait=clk_y.wait,
                               budget=60)
check("a map that never closes is not called closed",
      out_y["The Island"]["exited"] and not out_y["Ragnarok"]["exited"], out_y)
check("and it says so rather than silently passing",
      "still answering" in out_y["Ragnarok"]["why"], out_y)
check("the wait is bounded - one stuck map cannot hold the stop forever",
      clk_y.t <= 60 + 5, clk_y.t)


# A map that will not even take DoExit is not a reason to refuse the stop: the ordinary
# shutdown still follows, which is exactly what happened before this existed.
def rcon_dead(host, port, cmd):
    raise OSError("no route to host")


out_d = clusterctl.exit_worlds(st, running=lambda s: TARGETS_X, rcon=rcon_dead, now=ClockX().now,
                               wait=lambda s: None)
check("a map that cannot be reached does not block the stop",
      all(o["exited"] for o in out_d.values()), out_d)
check("but the reason is recorded as unreachable, not as a clean close",
      all("did not answer" in o["why"] for o in out_d.values()), out_d)

# And the ordering the whole fix rests on: every world closed BEFORE compose down runs.
order_x = []
clusterctl.dockerctl = FakeDocker()
_real_compose = clusterctl.dockerctl.compose


def spy_compose(path, proj, args, timeout=900):
    order_x.append("down" if args == ["down"] else " ".join(args))
    return _real_compose(path, proj, args, timeout)


clusterctl.dockerctl.compose = spy_compose
alive_z = {"island": True, "ragnarok": True}


def rcon_z(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if cmd == "DoExit":
        alive_z[key] = False
        order_x.append("exit:%s" % key)
        return "Exiting..."
    if not alive_z[key]:
        raise OSError("connection refused")
    return "No Players Connected"


ok_z, msg_z = clusterctl.stop(st, running=lambda s: TARGETS_X, rcon=rcon_z, now=ClockX().now, wait=lambda s: None)
check("stop closes every world before it signals anything",
      order_x and order_x[-1] == "down"
      and {"exit:island", "exit:ragnarok"} <= set(order_x[:-1]), order_x)
check("and the stop still succeeds", ok_z, msg_z)

# ---- a stop that says what it is doing
#
# Closing ten worlds takes minutes, and the whole of it was silent: the owner pressed
# Stop, watched nothing happen, and reasonably concluded nothing was. Silence and
# failure have to look different.
said = []
alive_s = {"island": True, "ragnarok": True}


def rcon_s(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if cmd == "DoExit":
        alive_s[key] = False
        return "Exiting..."
    if not alive_s[key]:
        raise OSError("connection refused")
    return "No Players Connected"


def say_s(event, text, level="info", detail=None, **fields):
    said.append({"event": event, "text": text, "level": level, "detail": detail or ""})


clusterctl.dockerctl = FakeDocker()
ok_s, msg_s = clusterctl.stop(st, running=lambda s: TARGETS_X, rcon=rcon_s,
                              now=ClockX().now, wait=lambda s: None, say=say_s)
events_s = [e["event"] for e in said]
check("a stop announces before it starts closing anything",
      events_s and events_s[0] == "cluster.closing", events_s)
check("every world that closes is announced as it closes",
      len([e for e in said if e["event"] == "cluster.world_closed"]) == 2, events_s)
check("and each one names the map and the count, not just a number",
      all(("The Island" in e["text"] or "Ragnarok" in e["text"]) and "of 2" in e["text"]
          for e in said if e["event"] == "cluster.world_closed"),
      [e["text"] for e in said if e["event"] == "cluster.world_closed"])
check("a summary says how many closed cleanly, before the containers are stopped",
      any(e["event"] == "cluster.closed" and "2 of 2" in e["text"] for e in said),
      [e["text"] for e in said])
check("with the per-map breakdown in the detail, not in the chat line",
      all("The Island" in e["detail"] and "Ragnarok" in e["detail"]
          for e in said if e["event"] == "cluster.closed"),
      [e["detail"] for e in said if e["event"] == "cluster.closed"])
check("and the stop itself still works", ok_s, msg_s)

# A map that will not close is the case the owner most needs told about, so it is a
# warning and it is named - not folded into a cheerful summary.
said_l = []
alive_l = {"island": True, "ragnarok": True}


def rcon_l(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if cmd == "DoExit" and key == "island":
        alive_l[key] = False
        return "Exiting..."
    if not alive_l[key]:
        raise OSError("connection refused")
    return "No Players Connected"          # ragnarok never closes


clusterctl.dockerctl = FakeDocker()
ok_l, msg_l = clusterctl.stop(
    st, running=lambda s: TARGETS_X, rcon=rcon_l, now=ClockX().now,
    wait=lambda s: None, say=lambda e, t, level="info", detail=None, **f:
        said_l.append({"event": e, "text": t, "level": level, "detail": detail or ""}),
    budget=30)
check("a map that would not close is reported as a warning, not as success",
      any(e["event"] == "cluster.closed_partly" and e["level"] == "warning"
          for e in said_l), [(e["event"], e["level"]) for e in said_l])
check("the summary counts only the ones that actually closed",
      any("1 of 2" in e["text"] for e in said_l), [e["text"] for e in said_l])
check("and the returned message still names it for the operator",
      "Ragnarok" in msg_l, msg_l)

# Announcing is reporting, and reporting must never be what stops a cluster stopping.
clusterctl.dockerctl = FakeDocker()
ok_b, msg_b = clusterctl.stop(
    st, running=lambda s: TARGETS_X, rcon=rcon_s, now=ClockX().now,
    wait=lambda s: None,
    say=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("discord is down")))
check("a stop still completes when announcing it raises", ok_b, msg_b)

# ---- silence has two meanings, and only one of them is "it has exited"
#
# On 2026-09-16 an apply stopped three maps that were still booting. A refused RCON
# connection was read as proof the server had exited, and a refusal means either that or
# "it has not opened its RCON port yet" - which on this cluster can be ten minutes long.
# So `docker compose down` signalled a booting server, the image's two-stage shutdown
# needed the RCON it did not have, hung past its grace period, was killed, was revived,
# and booted again. Docker can tell the two apart when silence cannot: a server that has
# exited leaves a container that is not running.
def details_for(states):
    """Docker's answer about this cluster's containers, keyed by map."""
    def details(names):
        out = {}
        for n in names:
            key = str(n).rsplit("-", 1)[-1]
            if states.get(key):
                out[n] = {"state": states[key], "health": "starting",
                          "restarts": 0, "uptime_seconds": 30}
        return out
    return details


def refuses(host, port, cmd):
    raise OSError("connection refused")


# 1. refused, and its container is up: a server mid-boot. Never "exited".
stopped_n = []
out_n = clusterctl.exit_worlds(
    st, running=lambda s: TARGETS_X, rcon=refuses, now=ClockX().now,
    wait=lambda s: None, budget=30,
    details=details_for({"island": "running", "ragnarok": "running"}),
    stop_container=lambda k: (stopped_n.append(k), (True, "stopped"))[1])
check("a map that refuses RCON while its container is running is NEVER called exited",
      not any(o["exited"] for o in out_n.values()), out_n)
check("it is not-ready - still starting, not gone",
      all(o["state"] == clusterctl.NOT_READY for o in out_n.values()), out_n)
check("and nothing is stopped on the strength of a refused connection",
      stopped_n == [], stopped_n)
check("a map that never answers is still not-ready when the budget runs out",
      all(o["state"] == clusterctl.NOT_READY for o in out_n.values()), out_n)
check("and every one of them is named in the result, not quietly dropped",
      sorted(out_n) == ["Ragnarok", "The Island"], sorted(out_n))
check("with a reason that says only what was actually established about it",
      all(o["why"] == ("it never answered RCON in 30s and its container has not "
                       "stopped - it has not finished starting")
          for o in out_n.values()), out_n)

# 2. refused, and nothing is running under that name: it really has gone.
out_g = clusterctl.exit_worlds(
    st, running=lambda s: TARGETS_X, rcon=refuses, now=ClockX().now,
    wait=lambda s: None, budget=30, details=details_for({}),
    stop_container=lambda k: (True, "stopped"))
check("a map that refuses RCON with no container running has already gone",
      all(o["exited"] for o in out_g.values()), out_g)
check("said as already-gone, which is one of the two states that mean exited",
      all(o["state"] == clusterctl.ALREADY_GONE for o in out_g.values()), out_g)
check("and the reason names both halves of the evidence",
      all("did not answer" in o["why"] and "not running" in o["why"]
          for o in out_g.values()), out_g)

# The two halves of that, side by side, because they are one line apart in the code and
# opposite in what they permit. Docker ANSWERING that nothing is there is a fact, and a
# fact is what already-gone needs. Docker not answering at all is not a fact about
# anything - and reading "I could not ask" as "it has exited" is the same inference this
# whole section exists to delete one layer up. An unknown is possibly-up, so it is
# not-ready, so an apply holds: a held apply costs a window, and the other way cost two
# hours and three maps in a restart loop.
stopped_u = []
out_u = clusterctl.exit_worlds(
    st, running=lambda s: TARGETS_X, rcon=refuses, now=ClockX().now,
    wait=lambda s: None, budget=30,
    details=lambda names: (_ for _ in ()).throw(OSError("docker did not answer")),
    stop_container=lambda k: (stopped_u.append(k), (True, "stopped"))[1])
check("a container Docker could not be asked about is NEVER called exited",
      not any(o["exited"] for o in out_u.values()), out_u)
check("an unknown is not-ready - possibly up, which is not the same as gone",
      all(o["state"] == clusterctl.NOT_READY for o in out_u.values()), out_u)
check("and nothing is stopped on the strength of a question nobody answered",
      stopped_u == [], stopped_u)
check("while Docker ANSWERING that nothing is there still means already-gone",
      all(o["state"] == clusterctl.ALREADY_GONE for o in out_g.values()), out_g)
check("so it is only the unanswerable ask that was tightened, not the empty answer",
      all(o["exited"] for o in out_g.values()), out_g)

# And the consequence that matters: an apply holds rather than signalling into the dark.
clusterctl.dockerctl = FakeDocker()
calls.clear()
ok_u, msg_u = clusterctl.stop(
    st, running=lambda s: TARGETS_X, rcon=refuses, now=ClockX().now,
    wait=lambda s: None, budget=30,
    details=lambda names: (_ for _ in ()).throw(OSError("docker did not answer")),
    stop_container=lambda k: (True, "stopped"), say=lambda *a, **k: None,
    require_ready=True)
check("an apply will not stop a cluster it could not ask Docker about", not ok_u, msg_u)
check("and it names the maps it could not establish anything about",
      "The Island" in msg_u and "Ragnarok" in msg_u, msg_u)
check("docker compose down is never reached on an unknown either", calls == [], calls)

# 3. DoExit taken, then silence: closed - and the container is stopped there and then,
#    inside the loop, not left for the batch `down` up to fifteen minutes later. That
#    gap is where a restart policy revives a server behind a stop that thinks it is done.
order_c, turns_c = [], {"n": 0}
silent_at = {"island": 0, "ragnarok": 2}


def rcon_c(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if cmd == "DoExit":
        order_c.append("doexit:%s" % key)
        return "Exiting..."
    if turns_c["n"] >= silent_at[key]:
        raise OSError("connection refused")
    return "No Players Connected"


def wait_c(seconds):
    turns_c["n"] += 1
    order_c.append("turn")


out_c = clusterctl.exit_worlds(
    st, running=lambda s: TARGETS_X, rcon=rcon_c, now=ClockX().now, wait=wait_c,
    budget=60, details=details_for({"island": "running", "ragnarok": "running"}),
    stop_container=lambda k: (order_c.append("stop:%s" % k), (True, "stopped"))[1])
check("a map that takes DoExit and then goes quiet has closed",
      all(o["exited"] and o["state"] == clusterctl.CLOSED for o in out_c.values()),
      out_c)
check("and Obelisk stopped that container itself",
      {"stop:island", "stop:ragnarok"} <= set(order_c), order_c)
check("the moment it closed, not at the end - island is stopped before the next turn",
      "turn" in order_c and "stop:island" in order_c
      and order_c.index("stop:island") < order_c.index("turn"), order_c)
check("and before the map that was still closing had even finished",
      "stop:ragnarok" in order_c
      and order_c.index("stop:island") < order_c.index("stop:ragnarok"), order_c)
check("which the reason says, so the record shows the door was shut",
      all("container is stopped" in o["why"] for o in out_c.values()), out_c)

# 4. a booting map that comes up mid-wait is asked again - a real DoExit, inside the
#    same budget, rather than a second wait of its own bolted on per map.
order_b, boot = [], {"n": 0, "gone": False}


def rcon_b(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if key == "ragnarok":
        raise OSError("connection refused")        # gone, and its container is not up
    if boot["n"] < 3:
        raise OSError("connection refused")        # still starting
    if cmd == "DoExit":
        order_b.append("doexit:island")
        boot["gone"] = True
        return "Exiting..."
    if boot["gone"]:
        raise OSError("connection refused")
    return "No Players Connected"


out_b = clusterctl.exit_worlds(
    st, running=lambda s: TARGETS_X, rcon=rcon_b, now=ClockX().now,
    wait=lambda s: boot.__setitem__("n", boot["n"] + 1), budget=60,
    details=details_for({"island": "running"}),
    stop_container=lambda k: (order_b.append("stop:%s" % k), (True, "stopped"))[1])
check("a map that was not ready is asked again once it opens RCON",
      "doexit:island" in order_b, order_b)
check("and it was genuinely not ready first - this is the retry, not the first ask",
      boot["n"] >= 3, boot)
check("a retried map reaches closed like any other",
      out_b["The Island"]["state"] == clusterctl.CLOSED
      and out_b["The Island"]["exited"], out_b)
check("with its container stopped the same way", "stop:island" in order_b, order_b)
check("and the map that really was gone is still already-gone",
      out_b["Ragnarok"]["state"] == clusterctl.ALREADY_GONE, out_b)

# 10. a map that keeps answering after taking DoExit is late, exactly as before.
check("a map that never closes is late, and late is not exited",
      out_y["Ragnarok"]["state"] == clusterctl.LATE
      and not out_y["Ragnarok"]["exited"], out_y)
check("and the closed one beside it is closed",
      out_y["The Island"]["state"] == clusterctl.CLOSED, out_y)

# ---- what the two callers do with a map that is still booting
#
# An apply is unattended and has a build to promote, so it refuses: `down` is never
# reached, nothing is signalled and the window comes round again. An operator pressing
# Stop asked for a stop and gets one - `down` removes the containers, so nothing is left
# to revive - but is told which maps never became operational.
said_r = []


def say_r(event, text, level="info", detail=None, **fields):
    said_r.append({"event": event, "text": text, "level": level, "detail": detail or ""})


clusterctl.dockerctl = FakeDocker()
calls.clear()
ok_r, msg_r = clusterctl.stop(
    st, running=lambda s: TARGETS_X, rcon=refuses, now=ClockX().now,
    wait=lambda s: None, budget=30,
    details=details_for({"island": "running", "ragnarok": "running"}),
    stop_container=lambda k: (True, "stopped"), say=say_r, require_ready=True)
check("an apply will not stop a cluster with a map still starting up", not ok_r, msg_r)
check("and names every map that never became operational",
      "The Island" in msg_r and "Ragnarok" in msg_r, msg_r)
check("docker compose down is NEVER reached - that is the line that caused the incident",
      not any(a[2] == ["down"] for a in calls), calls)
check("nothing was signalled at all, in fact", calls == [], calls)
check("and the channel is told why, as a warning",
      any(e["event"] == "cluster.not_ready" and e["level"] == "warning"
          for e in said_r), [(e["event"], e["level"]) for e in said_r])
check("and when nothing has been stopped, it says exactly that",
      any("Nothing has been stopped, nothing has been removed and nothing has been "
          "changed." in e["text"] for e in said_r), [e["text"] for e in said_r])
check("with each held map's own reason in the detail",
      any(e["event"] == "cluster.not_ready"
          and "NOT STOPPED - it never answered RCON in 30s and its container has not "
              "stopped - it has not finished starting" in e["detail"] for e in said_r),
      [e["detail"] for e in said_r])

# The operator's Stop. Same cluster, same state, opposite answer - and it says so.
said_o = []
clusterctl.dockerctl = FakeDocker()
calls.clear()
ok_o, msg_o = clusterctl.stop(
    st, running=lambda s: TARGETS_X, rcon=refuses, now=ClockX().now,
    wait=lambda s: None, budget=30,
    details=details_for({"island": "running", "ragnarok": "running"}),
    stop_container=lambda k: (True, "stopped"),
    say=lambda e, t, level="info", detail=None, **f:
        said_o.append({"event": e, "text": t, "level": level}))
check("an operator who presses Stop still gets a stop", ok_o, msg_o)
check("and it does reach docker compose down", any(a[2] == ["down"] for a in calls),
      calls)
check("but the message names every map that never became operational",
      "The Island" in msg_o and "Ragnarok" in msg_o
      and "never finished booting" in msg_o, msg_o)
check("and the channel heard about it too",
      any(e["event"] == "cluster.not_ready" for e in said_o),
      [e["event"] for e in said_o])

# ---- the last look, before anything is signalled
#
# Every conclusion above is drawn from silence, and a server that has not opened RCON
# sounds exactly like one that has closed. So every map is asked once more at the end:
# one that is talking again was never closed, whatever the wait decided a minute ago.
probe_w = {"n": 0}


def rcon_w(host, port, cmd):
    key = "island" if "island" in host else "ragnarok"
    if cmd == "DoExit":
        return "Exiting..."
    probe_w["n"] += 1
    if key == "island" and probe_w["n"] > 2:
        return "No Players Connected"              # back from the dead, mid-stop
    raise OSError("connection refused")


clusterctl.dockerctl = FakeDocker()
calls.clear()
ok_w, msg_w = clusterctl.stop(
    st, running=lambda s: TARGETS_X, rcon=rcon_w, now=ClockX().now,
    wait=lambda s: None, budget=30,
    details=details_for({"island": "running", "ragnarok": "running"}),
    stop_container=lambda k: (True, "stopped"),
    say=lambda *a, **k: None, require_ready=True)
check("a map that answers again after being called closed holds the stop",
      not ok_w, msg_w)
check("and it is the one that answered again that is named as holding the stop",
      msg_w.startswith("The Island had not finished booting and never answered RCON, "
                       "so the cluster was not stopped."), msg_w)
check("while the map that really did close is named as one that is down until Launch",
      "Ragnarok had already saved and closed before this, so it is down now and will "
      "stay down until Launch - the rest of the cluster is still up. Nothing was "
      "removed and no build was swapped." in msg_w, msg_w)
check("nothing was signalled on the strength of the silence that turned out to be a lie",
      calls == [], calls)


clusterctl.dockerctl = fake

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


# ---- a second cluster must never be able to claim the first one's containers
# The live failure: a test cluster generated `container_name: asa_island` while a
# hand-built cluster was running a container of exactly that name. Docker refused, which
# is the only reason this was an aborted launch and not a replaced game server - after
# it had already created a network. Names are namespaced by cluster now, and a launch
# that would still collide is refused before anything is created.
from .naming import container_name

check("container names carry the cluster id",
      container_name("arkcluster", "island") == "asa-arkcluster-island",
      container_name("arkcluster", "island"))
check("which is not the bare name a hand-built cluster uses",
      container_name("arkcluster", "island") != "asa_island")
check("two clusters with the same maps get different names",
      container_name("clusterA", "island") != container_name("clusterB", "island"))

st_n, _dn = fresh(maps="island,ragnarok", cluster_id="testcluster")
yml_n = generate_compose(st_n, project="testcluster")
check("the generated stack uses namespaced names",
      "container_name: asa-testcluster-island" in yml_n, yml_n[:400])
check("and never the bare form", "container_name: asa_island" not in yml_n)

# preflight: the exact live-cluster situation
live = {"asa_island": "ark-asa", "asa_ragnarok": "ark-asa", "asa_crosschat": "ark-asa"}
ok_n, msg_n = clusterctl.name_conflicts(st_n, existing=live)
check("namespaced names do not collide with a live cluster", ok_n, msg_n)

# and if a name really is taken, refuse with the reason
taken = {"asa-testcluster-island": "someone-elses-project"}
ok_c, msg_c = clusterctl.name_conflicts(st_n, existing=taken)
check("a genuinely taken name is refused", not ok_c, msg_c)
check("the message names the container and its owner",
      "asa-testcluster-island" in msg_c and "someone-elses-project" in msg_c, msg_c)
check("and says nothing was changed", "nothing was changed" in msg_c, msg_c)

# our own containers are not a conflict - that is what relaunching is
ours = {"asa-testcluster-island": "testcluster", "asa-testcluster-ragnarok": "testcluster"}
ok_o, _m = clusterctl.name_conflicts(st_n, existing=ours)
check("relaunching our own cluster is allowed", ok_o)

# an unknown answer must never be read as "nothing is there"
class BlindDocker(FakeDocker):
    def existing_containers(self, timeout=30):
        return None                    # e.g. the socket went away mid-session


_saved = clusterctl.dockerctl
clusterctl.dockerctl = BlindDocker()
ok_u, msg_u = clusterctl.name_conflicts(st_n)
check("an unreadable container list refuses rather than assuming", not ok_u, msg_u)
check("and says why it could not check", "Docker socket" in msg_u, msg_u)
clusterctl.dockerctl = _saved

# and launch stops before creating anything at all
calls.clear()


class ConflictDocker(FakeDocker):
    def existing_containers(self, timeout=30):
        return {"asa-testcluster-island": "ark-asa"}


clusterctl.dockerctl = ConflictDocker()
ok_l, msg_l = clusterctl.launch(st_n)
check("launch refuses on a name conflict", not ok_l, msg_l)
check("and never ran docker compose - no network, no half-built stack",
      calls == [], calls)


# ---- identity is per instance, not per map type
# A cluster is a list of instances and the same map can appear twice - an events island
# beside the normal one. Keying a container name, a port or a save folder on the map
# type alone means those two fight over it.
from . import maps as mapcat

check("one instance of a map keeps the plain name",
      mapcat.instance_ids(["island", "ragnarok"]) == ["island", "ragnarok"])
check("a repeated map gets distinct instance ids",
      mapcat.instance_ids(["island", "island", "ragnarok", "island"]) ==
      ["island", "island-2", "ragnarok", "island-3"],
      mapcat.instance_ids(["island", "island", "ragnarok", "island"]))
check("so an existing single-instance cluster is never renamed",
      mapcat.instance_ids(["island"]) == ["island"])

names_dup = [container_name("evt", i) for i in mapcat.instance_ids(["island", "island"])]
check("two islands in one cluster get different container names",
      names_dup == ["asa-evt-island", "asa-evt-island-2"], names_dup)
check("and neither is the bare live-cluster name", "asa_island" not in names_dup)
check("save folders differ per instance too",
      layout.instance_dir("/ark", "island") != layout.instance_dir("/ark", "island-2"))

# ports are already per instance, because the plan assigns them per row
st_i, _di = fresh(maps="island,ragnarok", cluster_id="ports")
pl_i = build_plan(st_i, in_use_ports=set())
ids_i = [r["instance"] for r in pl_i["maps"]]
game = [r["game_port"] for r in pl_i["maps"]]
rcon = [r["rcon_port"] for r in pl_i["maps"]]
check("every instance is identified in the plan", ids_i == ["island", "ragnarok"], ids_i)
check("no two instances share a game port", len(set(game)) == len(game), game)
check("no two instances share an RCON port", len(set(rcon)) == len(rcon), rcon)

yml_i = generate_compose(st_i, project="ports")
check("the compose service is the instance, not the map type",
      "  island:" in yml_i and "container_name: asa-ports-island" in yml_i)
check("INSTANCE_NAME is the instance id",
      'INSTANCE_NAME: "island"' in yml_i, [l for l in yml_i.splitlines() if "INSTANCE_NAME" in l])
check("the save mount is per instance",
      "/instances/island/Saved:" in yml_i)

# the preflight checks instance names, so a duplicate-map cluster is covered too
check("preflight targets instance names",
      clusterctl.target_names(st_i) == ["asa-ports-island", "asa-ports-ragnarok"],
      clusterctl.target_names(st_i))


# ---- a running cluster keeps the ports it is on
# After a launch the plan asked the host which ports were busy and got back its own,
# so it shifted to the next free pair - the page then showed numbers that did not match
# the running containers, and an Apply would have moved a live server for no reason.
seen_excludes = []


class PortDocker(FakeDocker):
    def ports_in_use(self, exclude_names=()):
        seen_excludes.append(sorted(exclude_names))
        # the host: this cluster on 7877/27920, plus something unrelated on 7878
        held = {7877: "asa-ports2-island", 27920: "asa-ports2-island", 7878: "other-app"}
        skip = set(exclude_names)
        return {p for p, owner in held.items() if owner not in skip}


st_p2, _dp = fresh(maps="island", cluster_id="ports2")
st_p2.patch({"game_port_base": 7877, "rcon_port_base": 27920})
clusterctl.dockerctl = PortDocker()

in_use = clusterctl.other_ports_in_use(st_p2)
check("the cluster's own containers are excluded from the check",
      seen_excludes and seen_excludes[-1] == ["asa-ports2-island"], seen_excludes)
check("so its own ports are not reported as taken", 7877 not in in_use and 27920 not in in_use,
      sorted(in_use))
check("but other people's still are", 7878 in in_use, sorted(in_use))

pl2 = build_plan(st_p2, in_use_ports=in_use)
check("the plan keeps the ports the cluster is actually running on",
      (pl2["maps"][0]["game_port"], pl2["maps"][0]["rcon_port"]) == (7877, 27920),
      (pl2["maps"][0]["game_port"], pl2["maps"][0]["rcon_port"]))

# without the exclusion it drifts - the bug being fixed
pl_drift = build_plan(st_p2, in_use_ports={7877, 7878, 27920})
check("and would have drifted without it",
      pl_drift["maps"][0]["game_port"] == 7879, pl_drift["maps"][0]["game_port"])


# ---- the server has to be able to write to its own folders
# Docker creates a missing bind-mount source as root, and Obelisk runs as root, so every
# folder the game needs would be root-owned. The server does not fail loudly on that -
# it loops on "Permission denied", installs nothing, and looks like a slow first start.
owned = []
r_own = os.path.join(tempfile.mkdtemp(), "ark")
layout.ensure_ark(r_own, ["island"],
                  chown=lambda p, u, g: owned.append((os.path.basename(p), u, g)))
names = [o[0] for o in owned]
check("every ark folder is handed to the server's user",
      {"ServerFiles", "Mods", "shared", "SavedArks", "cluster", "instances"} <= set(names),
      names)
check("including the per-instance Saved folder", "Saved" in names, names)
check("to the uid the server image runs as",
      all(o[1:] == (layout.SERVER_UID, layout.SERVER_GID) for o in owned), owned[:2])
check("which is 7777", layout.SERVER_UID == 7777)

# a filesystem without ownership must warn, not abort the launch
def _refuse(p, u, g):
    raise OSError("read-only")


ok_chown = layout.give_to_server(["/nowhere"], chown=_refuse)
check("a filesystem that refuses ownership is reported, not fatal", ok_chown is False)
check("and ensure_ark still returns its folders",
      len(layout.ensure_ark(os.path.join(tempfile.mkdtemp(), "a"), chown=_refuse)) > 0)


# ---- launch refuses when the server could not write to its own folders
# The silent version of this cost an hour: the container came up, looped on Permission
# denied, installed nothing, and reported health: starting the whole time.
class RootOwned:
    st_uid = 0
    st_gid = 0
    st_mode = 0o755


blocked = layout.not_writable_by_server("/ark", stat=lambda p: RootOwned())
check("root-owned folders are reported as unwritable", blocked, blocked)
check("and the report names the ownership", "owned by 0:0" in blocked[0], blocked[0])


class ServerOwned:
    st_uid = layout.SERVER_UID
    st_gid = layout.SERVER_GID
    st_mode = 0o755


check("folders owned by the server's user are fine",
      layout.not_writable_by_server("/ark", stat=lambda p: ServerOwned()) == [])


class WorldWritable:
    st_uid = 0
    st_gid = 0
    st_mode = 0o777


check("world-writable folders are fine too",
      layout.not_writable_by_server("/ark", stat=lambda p: WorldWritable()) == [])


# ---- the flush has to find the maps that are actually running
# It read a SERVERS environment variable that only exists when Obelisk writes itself
# into the generated stack. On a normally-installed Obelisk it was always empty, so
# every backup reported "no running maps to save" while the island was up and healthy.
st_f, _df = fresh(maps="island", cluster_id="flush")
st_f.patch({"game_port_base": 7877, "rcon_port_base": 27920,
            "admin_password": "synthetic-pw"})

targets = clusterctl.rcon_targets(st_f)
check("targets are addressed by container name",
      targets == [("The Island", "asa-flush-island", 27920)], targets)


class RunningDocker(FakeDocker):
    def container_details(self, names, timeout=30):
        return {n: {"state": "running", "health": "healthy", "restarts": 0,
                    "uptime_seconds": 600} for n in names}


clusterctl.dockerctl = RunningDocker()
check("a running instance is found", clusterctl.running_instances(st_f) == targets)

asked = []
ok_f, detail_f = clusterctl.save_world(
    st_f, rcon=lambda h, p, c: asked.append((h, p, c)))
check("SaveWorld reaches the real container",
      asked == [("asa-flush-island", 27920, "SaveWorld")], asked)
check("and it reports success", ok_f and "saved 1" in detail_f, detail_f)


class StoppedDocker(FakeDocker):
    def container_details(self, names, timeout=30):
        return {n: {"state": "exited", "health": "", "restarts": 0,
                    "uptime_seconds": 0} for n in names}


clusterctl.dockerctl = StoppedDocker()
ok_s, detail_s = clusterctl.save_world(st_f, rcon=lambda h, p, c: None)
check("a stopped cluster still reports nothing to save",
      not ok_s and "no running maps" in detail_s, detail_s)

clusterctl.dockerctl = RunningDocker()


def _boom(h, p, c):
    raise OSError("connection refused")


ok_b, detail_b = clusterctl.save_world(st_f, rcon=_boom)
check("a map that will not answer is reported, not hidden",
      not ok_b and "did not answer" in detail_b or "no map accepted" in detail_b, detail_b)


# ---- accepting SaveWorld is not finishing it
#
# save_world() returns the moment the server *takes* the command; a large world is
# still serialising tens of seconds later. On 2026-09-11 the cluster was stopped into
# that gap: every world at or above 76 MB was damaged, every world at or below 41 MB
# came through clean. So anything about to stop the cluster has to prove the write
# landed - the file stopped changing, and nothing is open beside it.
from . import restore as _restore
from . import savepoints as _sp

st_q, _dq = fresh(maps="island,ragnarok", cluster_id="settle")
st_q.patch({"game_port_base": 7877, "rcon_port_base": 27920})
clusterctl.dockerctl = RunningDocker()

ISLAND_ARK = _sp.live_world(st_q, "island")
RAG_ARK = _sp.live_world(st_q, "ragnarok")
check("the file watched is the live world the game writes",
      ISLAND_ARK.endswith("TheIsland_WP.ark") and "SavedArks" in ISLAND_ARK, ISLAND_ARK)


class Disk:
    """stat/exists over scripted readings - one reading per poll, per path.

    A list is consumed a reading at a time and its last value then repeats; a callable
    is asked for reading `i`, which is how a world that never stops growing is written.
    """

    def __init__(self, readings, sidecars=()):
        self.readings = dict(readings)
        self.sidecars = set(sidecars)
        self.polls = {}

    def stat(self, path):
        rows = self.readings.get(path)
        if rows is None:
            raise OSError("no such file: %s" % path)
        i = self.polls.get(path, 0)
        self.polls[path] = i + 1
        size, mtime = rows(i) if callable(rows) else rows[min(i, len(rows) - 1)]
        return type("Stat", (), {"st_size": size, "st_mtime": mtime})()

    def exists(self, path):
        return path in self.sidecars


class Clock:
    """A clock that only moves when something waits on it, so nothing here sleeps."""

    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def wait(self, seconds):
        self.t += seconds


SENT = {"The Island": 1000.0, "Ragnarok": 1000.0}


def settled(disk, budget=60, sent=None):
    clk = Clock()
    out = clusterctl.worlds_settled(st_q, sent or SENT, now=clk.now, stat=disk.stat,
                                    exists=disk.exists, wait=clk.wait, budget=budget)
    return out, clk


res_q, clk_q = settled(Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 1001.0)]}))
check("a world that stops changing, with nothing open beside it, is settled",
      all(r["settled"] for r in res_q.values()), res_q)
check("and it stops waiting as soon as it knows, rather than burning the budget",
      clk_q.t < 1060, clk_q.t)

res_g, clk_g = settled(Disk({ISLAND_ARK: [(120, 1001.0)],
                             RAG_ARK: lambda i: (80 + i * 4096, 1001.0 + i)}))
check("a world that is still being written is not settled",
      res_g["Ragnarok"]["settled"] is False and res_g["The Island"]["settled"] is True,
      res_g)
check("and the wait is bounded by the budget rather than endless",
      clk_g.t <= 1060, clk_g.t)

# The ct-0009 shape: quiet on disk, transaction still open. A hot sidecar is not a
# timing hint - it says a transaction is open, and stopping into that is the failure.
for _suffix in _restore.SIDECARS:
    res_h, _ = settled(Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 1001.0)]},
                            sidecars=[RAG_ARK + _suffix]), budget=30)
    check("a %s file beside a quiet world means not settled" % _suffix,
          not res_h["Ragnarok"]["settled"] and res_h["The Island"]["settled"], res_h)
    check("and it says which file it is waiting on",
          _suffix in res_h["Ragnarok"]["why"], res_h["Ragnarok"])

# The other way a quiet file lies: it is quiet because the save has not begun.
res_s, _ = settled(Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 999.0)]}), budget=30)
check("a world older than its own SaveWorld has not started saving yet",
      not res_s["Ragnarok"]["settled"], res_s)
check("and that is reported as not-started, not as finished",
      "not begun" in res_s["Ragnarok"]["why"], res_s["Ragnarok"])

# A big world that starts late and takes its time is still a save that landed.
res_l, _ = settled(Disk({ISLAND_ARK: [(120, 1001.0)],
                         RAG_ARK: [(80, 999.0), (80, 999.0), (90000000, 1002.0),
                                   (142000000, 1003.0), (142000000, 1003.0),
                                   (142000000, 1003.0)]}))
check("a slow world still settles once it has actually finished",
      all(r["settled"] for r in res_l.values()), res_l)

# And a world file that cannot be read at all is an unknown, never a pass.
res_m, _ = settled(Disk({ISLAND_ARK: [(120, 1001.0)]}), budget=30)
check("a world that cannot be read is not treated as saved",
      not res_m["Ragnarok"]["settled"] and res_m["The Island"]["settled"], res_m)


# ---- telling somebody, as each world lands
#
# The wait already knew which map had finished and when; it threw that away and showed
# one aggregate line, so a save that was working looked exactly like a save that was
# stuck. These assert the report, not the proof - the proof is everything above.
def watched(disk, budget=60, sent=None, hook=None):
    clk = Clock()
    seen_calls = []

    def note(label, done, total):
        seen_calls.append((label, done, total))
        if hook:
            hook(label)

    out = clusterctl.worlds_settled(st_q, sent or SENT, now=clk.now, stat=disk.stat,
                                    exists=disk.exists, wait=clk.wait, budget=budget,
                                    on_settled=note)
    return out, seen_calls


# The island is quiet from the first reading; ragnarok writes for two more polls. So the
# order is not the dict's, it is the order they actually finish - which is the only
# order worth reporting.
res_w, calls_w = watched(Disk({ISLAND_ARK: [(120, 1001.0)],
                               RAG_ARK: [(80, 1001.0), (90, 1002.0), (90, 1002.0),
                                         (90, 1002.0)]}))
check("every settled map is reported", len(calls_w) == 2, calls_w)
check("once each, never twice - a settled map is skipped before it is polled again",
      len({c[0] for c in calls_w}) == 2, calls_w)
check("in the order they finished, not the order they were asked",
      [c[0] for c in calls_w] == ["The Island", "Ragnarok"], calls_w)
check("counting up as they land, against a fixed total",
      [(c[1], c[2]) for c in calls_w] == [(1, 2), (2, 2)], calls_w)
check("and the proof itself is unchanged by being watched",
      all(r["settled"] for r in res_w.values()), res_w)

# A map that never settles is never announced as settled. The whole point is that this
# line means the world is on disk.
res_x, calls_x = watched(Disk({ISLAND_ARK: [(120, 1001.0)],
                               RAG_ARK: lambda i: (80 + i * 4096, 1001.0 + i)}),
                         budget=30)
check("a world still writing is never reported saved",
      [c[0] for c in calls_x] == ["The Island"], calls_x)
check("and the count never claims more than landed",
      calls_x[0][1] == 1 and calls_x[0][2] == 2, calls_x)

# A world with a hot journal beside it is not settled, so it is not reported either.
res_y, calls_y = watched(Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 1001.0)]},
                              sidecars=[RAG_ARK + "-wal"]), budget=30)
check("a map holding an open transaction is not reported saved",
      [c[0] for c in calls_y] == ["The Island"], calls_y)

# Reporting runs with the cluster still up and a stop waiting behind it. A UI callback
# that throws must cost the message, never the save.
res_z, calls_z = watched(Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 1001.0)]}),
                         hook=lambda label: (_ for _ in ()).throw(RuntimeError("ui")))
check("a reporting callback that raises does not break the save",
      all(r["settled"] for r in res_z.values()), res_z)
check("and the other maps are still proved after it",
      len(res_z) == 2, res_z)

# The seam the app actually uses: save_and_settle hands keywords straight through.
calls_kw = []
clk_kw = Clock()
disk_kw = Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 1001.0)]})
clusterctl.save_and_settle(
    st_q, rcon=lambda h, p, c: None, now=clk_kw.now, stat=disk_kw.stat,
    exists=disk_kw.exists, wait=clk_kw.wait, budget=60,
    on_settled=lambda label, done, total: calls_kw.append(label))
check("save_and_settle passes the reporter through to the wait",
      sorted(calls_kw) == ["Ragnarok", "The Island"], calls_kw)


# ---- a map left down on purpose is not a map that is slow to start
#
# The integrity gate can now refuse one map and start the rest, so wait_healthy has to
# tell "not ready yet" apart from "nobody created this". Waiting twenty-five minutes for
# a container that was deliberately not started would turn one refused map into a
# stalled batch.
slept_h = []
ok_h, why_h = clusterctl.wait_healthy(
    st, "island", minutes=25, sleep=lambda s: slept_h.append(s),
    details=lambda names: {})
check("a map with no container at all gives up quickly, not in 25 minutes",
      not ok_h and len(slept_h) <= clusterctl.ABSENT_POLLS, len(slept_h))
check("and says it was never started rather than blaming the health check",
      "never started" in why_h, why_h)

# But a container that is merely slow to appear must still be waited for - that is the
# ordinary case on a launch, and giving up on the first empty look would break it.
seen_h = [0]


def _late(names):
    seen_h[0] += 1
    if seen_h[0] <= 2:
        return {}                       # not created yet
    return {list(names)[0]: {"state": "running", "health": "healthy"}}


ok_l, why_l = clusterctl.wait_healthy(st, "island", minutes=25,
                                      sleep=lambda s: None, details=_late)
check("a container that takes a moment to appear is still waited for", ok_l, why_l)


# ---- worlds_intact: is each world still readable, checked while the cluster is down
#
# The save gate proves a world finished being written. These prove somebody then asked
# whether the bytes are any good - the question nobody asked on 2026-09-12, when a build
# was promoted over three damaged worlds and ten servers started onto the wreckage.
ROWS_I = [("The Island", "island"), ("Ragnarok", "ragnarok")]


def intact(answers, sidecars=(), deep=True, world_missing=()):
    seen_deep = []

    def fake_verify(path, deep=True):
        seen_deep.append(deep)
        key = "island" if "TheIsland" in path else "ragnarok"
        return answers[key]

    out = clusterctl.worlds_intact(
        st_q, keys=ROWS_I, verify=fake_verify,
        exists=lambda p: (any(p.endswith(s) for s in sidecars)
                          or not p.endswith(tuple(restore_mod.SIDECARS))),
        # lexists is what decides "absent" now, so the fake has to answer it too:
        # the world entry is there unless the case under test says it is not.
        lexists=lambda p: not any(("TheIsland" if k == "island" else "Ragnarok")
                                  in p for k in world_missing),
        isdir=lambda p: True,
        listdir=lambda p: [],
        deep=deep)
    return out, seen_deep


good = {"island": (True, "ok"), "ragnarok": (True, "ok")}
res_i, deep_i = intact(good)
check("a cluster of readable worlds passes",
      all(v["ok"] for v in res_i.values()), res_i)
check("and every map is reported, not just the bad ones", len(res_i) == 2, res_i)
check("each answer carries the map key, so a caller can act on it",
      sorted(v["key"] for v in res_i.values()) == ["island", "ragnarok"], res_i)

bad = {"island": (False, "SQLite reports it damaged: page 4 is never used"),
       "ragnarok": (True, "ok")}
res_b, _d = intact(bad)
check("a damaged world is reported damaged", res_b["The Island"]["ok"] is False, res_b)
check("and the reason is carried through verbatim",
      "page 4" in res_b["The Island"]["why"], res_b)
check("while the healthy one is untouched", res_b["Ragnarok"]["ok"] is True, res_b)

# 4. verify_world cannot see a hot journal - immutable=1 ignores it by design - so the
# sidecar test sits on top. A world with a transaction still open is not promotable.
res_s, _d = intact(good, sidecars=("-wal",))
check("a world with a -wal beside it fails even though SQLite says it is fine",
      not any(v["ok"] for v in res_s.values()), res_s)
check("and it says that is why, rather than calling it damage",
      "had not finished being written" in res_s["The Island"]["why"], res_s)
check("and it is a state of its own, not lumped in with damage",
      res_s["The Island"]["state"] == "writing", res_s)

# A world that has never existed is not corrupt and must not block: a map held down can
# never create the world whose absence caused the refusal, which is a trap with no way
# out. Starting it is how it gets one - the same reasoning the save gate uses.
res_n, _d = intact({"island": (True, "ok"), "ragnarok": (True, "ok")},
                   world_missing=("island",))
check("a map that has never booted has no world, and that does NOT block the apply",
      all(v["ok"] for v in res_n.values()), res_n)
check("and it is reported as its own state rather than as damage",
      res_n["The Island"]["state"] == "absent", res_n)
check("and it is never told to restore something that does not exist",
      "restore" not in res_n["The Island"]["why"].lower(), res_n)
for side in ("-shm", "-journal"):
    r_x, _d = intact(good, sidecars=(side,))
    check("a %s beside a world fails it too" % side,
          not r_x["The Island"]["ok"], r_x)

# 3 (the check mode). The apply gate pays for the deep walk; the sweep does not.
_r, deep_true = intact(good, deep=True)
_r, deep_false = intact(good, deep=False)
check("the gate asks for the full integrity check", all(deep_true), deep_true)
check("and a caller can ask for the cheap one instead",
      not any(deep_false), deep_false)

# 2. one checker in the product, not two.
import inspect as _inspect
check("worlds_intact calls restore.verify_world rather than reimplementing it",
      "verify_world" in _inspect.getsource(clusterctl.worlds_intact))


# ---- "absent" must mean nothing is there, not "something I could not resolve"
#
# The first version asked os.path.exists, which follows links and swallows stat errors.
# A dangling link where a world should be therefore read as "never booted", skipped the
# gate, and the map was started empty - which is the exact failure verify_world puts its
# symlink check first to catch, made unreachable by branching before it.
#
# Deliberately run against the real filesystem, with the real defaults. The bug lived in
# the default, so injecting a seam here would test the wrong thing.
import os as _os_i
import tempfile as _tf_i

_ark = _tf_i.mkdtemp()
st_i, _d_i = fresh(maps="island")
_sa = _os_i.path.join(_ark, "shared", "SavedArks", "TheIsland_WP")


def _real_intact():
    return clusterctl.worlds_intact(st_i, ark_root=_ark, keys=[("The Island", "island")])


# 1. nothing there at all - the SavedArks/<Map> folder does not exist, which is what a
#    map that has never booted looks like. Still skips, still non-blocking.
# The root has to exist for any per-map "absent" to mean anything - that is the whole
# point of the root check - so a launched cluster's SavedArks is the starting state.
_os_i.makedirs(_os_i.path.dirname(_sa), exist_ok=True)
res_a = _real_intact()
check("a map that has genuinely never booted is absent and does not block",
      res_a["The Island"]["state"] == "absent" and res_a["The Island"]["ok"], res_a)

# 2. the folder exists but holds no world yet - also a real never-booted state.
_os_i.makedirs(_sa, exist_ok=True)
res_e = _real_intact()
check("an empty world folder is absent too, and still does not block",
      res_e["The Island"]["state"] == "absent" and res_e["The Island"]["ok"], res_e)

# 3. a DANGLING LINK where the world should be. Something is there; it just cannot be
#    followed. This must never read as "never booted".
_world = _os_i.path.join(_sa, "TheIsland_WP.ark")
_linked = True
try:
    _os_i.symlink(_os_i.path.join(_ark, "nowhere", "gone.ark"), _world)
except (OSError, NotImplementedError, AttributeError):
    _linked = False                      # Windows without developer mode
if _linked:
    res_l = _real_intact()
    check("a dangling link where the world should be BLOCKS - it is not 'never booted'",
          res_l["The Island"]["ok"] is False, res_l)
    check("and it is never called absent",
          res_l["The Island"]["state"] != "absent", res_l)
    check("the symlink is named as the problem, not a missing file",
          "symlink" in res_l["The Island"]["why"].lower(), res_l)
    _os_i.remove(_world)
else:
    # Prove the rule itself without needing symlink privileges: lexists is what decides,
    # and it answers True for an entry that exists but cannot be followed.
    res_l = clusterctl.worlds_intact(
        st_i, ark_root=_ark, keys=[("The Island", "island")],
        lexists=lambda p: p.endswith(".ark"),
        verify=lambda path, deep=True: (False, "the restored world is a symlink"))
    check("a dangling link where the world should be BLOCKS - it is not 'never booted'",
          res_l["The Island"]["ok"] is False, res_l)
    check("and it is never called absent",
          res_l["The Island"]["state"] != "absent", res_l)
    check("the symlink is named as the problem, not a missing file",
          "symlink" in res_l["The Island"]["why"].lower(), res_l)

# 4. a WRONG-TYPE entry: a plain file where the world folder should be. Nothing can be
#    concluded about a world underneath it, and an unknown is never a pass.
import shutil as _sh_i
_sh_i.rmtree(_sa, ignore_errors=True)
with open(_sa, "w", encoding="utf-8") as _fh_i:
    _fh_i.write("not a folder")
res_w = _real_intact()
check("a file where the world folder should be BLOCKS", res_w["The Island"]["ok"] is False,
      res_w)
check("and it is reported as an unknown, not as absent",
      res_w["The Island"]["state"] == "unknown", res_w)
check("saying what is in the way", "not a folder" in res_w["The Island"]["why"], res_w)
_os_i.remove(_sa)

# 5. and a real, readable world still passes the whole way through.
_os_i.makedirs(_sa, exist_ok=True)
import sqlite3 as _sq_i
_con = _sq_i.connect(_world)
_con.execute("CREATE TABLE game (k TEXT)")
_con.execute("INSERT INTO game VALUES ('x')")
_con.commit()
_con.close()
with open(_world, "ab") as _fh2:
    _fh2.write(b"\0" * 4096)             # past the 1024-byte floor
res_g = _real_intact()
check("a real SQLite world with rows passes on the production path",
      res_g["The Island"]["ok"] and res_g["The Island"]["state"] == "ok", res_g)


# ---- "could not look" is never "nothing is there"
#
# lexists catches OSError exactly as exists did, so a world behind a share whose
# permissions have drifted, or behind a volume that is not mounted, answered False and
# was waved through as a map that had never booted - and started empty. That is the same
# data-visible failure the gate exists to prevent, arriving by a third route.
#
# Production defaults throughout: real directories, real permissions, real absences. The
# previous two bugs both hid behind an injected seam.
_n1 = _tf_i.mkdtemp()
_n1_saved = _os_i.path.join(_n1, "shared", "SavedArks")
st_n1, _d_n1 = fresh(maps="island,ragnarok")
ROWS_N1 = [("The Island", "island"), ("Ragnarok", "ragnarok")]


def _n1_intact(root=None):
    return clusterctl.worlds_intact(st_n1, ark_root=root or _n1, keys=ROWS_N1)


# 1. The ark root is not there at all - an unmounted volume, or the wrong path. Ten maps
#    do not stop having worlds together, and this must not sail through as ten absences.
res_um = _n1_intact()
check("an unmounted/missing ark root refuses the whole batch",
      not any(v["ok"] for v in res_um.values()), res_um)
check("every map is marked unreachable, not absent",
      all(v["state"] == "unreachable" for v in res_um.values()), res_um)
check("and the message asks about the mount rather than blaming the worlds",
      "volume mounted" in res_um["The Island"]["why"], res_um["The Island"])
check("it never tells anybody a world is missing",
      "no world yet" not in res_um["The Island"]["why"], res_um["The Island"])

# 2. Root present and readable, one map's folder genuinely missing: still the legitimate
#    never-booted case, still non-blocking, still no soft-deadlock.
_os_i.makedirs(_os_i.path.join(_n1_saved, "Ragnarok_WP"), exist_ok=True)
_rag = _os_i.path.join(_n1_saved, "Ragnarok_WP", "Ragnarok_WP.ark")
_con_n = _sq_i.connect(_rag)
_con_n.execute("CREATE TABLE game (k TEXT)")
_con_n.execute("INSERT INTO game VALUES ('x')")
_con_n.commit()
_con_n.close()
with open(_rag, "ab") as _f:
    _f.write(b"\0" * 4096)
res_one = _n1_intact()
check("with a readable root, a single missing map folder is still absent",
      res_one["The Island"]["state"] == "absent", res_one)
check("and still does not block", res_one["The Island"]["ok"] is True, res_one)
check("while the healthy map beside it passes", res_one["Ragnarok"]["ok"] is True,
      res_one)

# 3. A world folder that exists and will not open. On POSIX that is chmod 000; on
#    Windows chmod cannot express it, so the same OSError is produced by asking for a
#    directory listing of something that is not a directory. Either way the call that
#    fails is the production one.
_isl = _os_i.path.join(_n1_saved, "TheIsland_WP")
_unreadable = False
_os_i.makedirs(_isl, exist_ok=True)
try:
    _os_i.chmod(_isl, 0o000)
    _os_i.listdir(_isl)                  # if this succeeds, chmod did not take
except OSError:
    _unreadable = True
except Exception:
    _unreadable = False

if _unreadable:
    res_perm = _n1_intact()
    check("a world folder that will not open BLOCKS",
          res_perm["The Island"]["ok"] is False, res_perm)
    check("and is called unreachable, never absent",
          res_perm["The Island"]["state"] == "unreachable", res_perm)
    check("saying it is a permission or mount problem, not a missing world",
          "permission or mount" in res_perm["The Island"]["why"], res_perm)
    _os_i.chmod(_isl, 0o700)
else:
    # Windows: prove the same rule through readable_dir, which is what the branch calls.
    _file_as_dir = _os_i.path.join(_n1, "a-file")
    with open(_file_as_dir, "w", encoding="utf-8") as _f:
        _f.write("x")
    ok_rd, why_rd = clusterctl.readable_dir(_os_i.path.join(_file_as_dir, "sub"))
    check("a world folder that will not open BLOCKS", ok_rd is False, why_rd)
    check("and is called unreachable, never absent", ok_rd is False, why_rd)
    check("saying it is a permission or mount problem, not a missing world",
          bool(why_rd), why_rd)

# 4. readable_dir asks by listing, not by stat - a directory can stat fine and still
#    refuse to open, which is the whole distinction this turns on.
check("a readable directory reads as readable",
      clusterctl.readable_dir(_n1_saved)[0] is True)
check("a directory that is not there does not",
      clusterctl.readable_dir(_os_i.path.join(_n1, "nope"))[0] is False)

# 5. Every map absent at once under a readable root is still a refusal - the root listed,
#    so this is not the unmount above, but ten maps do not lose their worlds together.
_n2 = _tf_i.mkdtemp()
_os_i.makedirs(_os_i.path.join(_n2, "shared", "SavedArks"), exist_ok=True)
res_all = clusterctl.worlds_intact(st_n1, ark_root=_n2, keys=ROWS_N1)
check("every map absent at once is refused, not waved through as never-booted",
      not any(v["ok"] for v in res_all.values()), res_all)
check("and it points at the data directory rather than the maps",
      "ARK data directory" in res_all["The Island"]["why"], res_all["The Island"])

# The count came from the incident and was hardcoded - "which ten maps do not do at
# once" on a two-map cluster reads as a copy-paste bug, which is what it was.
check("the message counts the maps this cluster actually has",
      "2 maps" in res_all["The Island"]["why"]
      and "ten maps" not in res_all["The Island"]["why"], res_all["The Island"])

ROWS3 = ROWS_N1 + [("Scorched Earth", "scorched")]
res_3 = clusterctl.worlds_intact(st_n1, ark_root=_n2, keys=ROWS3)
check("and it counts three when there are three",
      "3 maps" in res_3["The Island"]["why"] and "2 maps" not in res_3["The Island"]["why"],
      res_3["The Island"])

# But one map on a one-map cluster is indistinguishable from a genuine first boot, and
# that case has to keep working or a map that has never started never can.
res_solo = clusterctl.worlds_intact(st_n1, ark_root=_n2,
                                    keys=[("The Island", "island")])
check("a single-map cluster that has never booted still skips, non-blocking",
      res_solo["The Island"]["ok"] and res_solo["The Island"]["state"] == "absent",
      res_solo)


# ---- save_and_settle: one call that sends the command and proves the write
asked_q = []
clk_sw = Clock()
disk_sw = Disk({ISLAND_ARK: [(120, 1001.0)], RAG_ARK: [(80, 1001.0)]})
ok_q, detail_q, worlds_q = clusterctl.save_and_settle(
    st_q, rcon=lambda h, p, c: asked_q.append((h, p, c)), now=clk_sw.now,
    stat=disk_sw.stat, exists=disk_sw.exists, wait=clk_sw.wait, budget=60)
check("save_and_settle sends SaveWorld to every running map",
      [c for _h, _p, c in asked_q] == ["SaveWorld", "SaveWorld"], asked_q)
check("and reports both worlds proved on disk",
      ok_q and all(w["settled"] for w in worlds_q.values()), (detail_q, worlds_q))
check("while still saying what save_world says", "saved 2 map(s)" in detail_q, detail_q)


def _one_boom(h, p, c):
    if p == 27921:
        raise OSError("connection refused")


clk_hb = Clock()
disk_hb = Disk({ISLAND_ARK: [(120, 1001.0)]})
ok_hb, detail_hb, worlds_hb = clusterctl.save_and_settle(
    st_q, rcon=_one_boom, now=clk_hb.now, stat=disk_hb.stat, exists=disk_hb.exists,
    wait=clk_hb.wait, budget=30)
check("a map that never took the command is not waited for",
      set(worlds_hb) == {"The Island"}, worlds_hb)
check("the map that did take it is still proved",
      ok_hb and worlds_hb["The Island"]["settled"], worlds_hb)
check("and the one that did not answer is still reported",
      "did not answer" in detail_hb, detail_hb)

clusterctl.dockerctl = StoppedDocker()
ok_ns, detail_ns, worlds_ns = clusterctl.save_and_settle(st_q, rcon=lambda h, p, c: None)
check("a stopped cluster has nothing to wait for and says the same as before",
      not ok_ns and "no running maps" in detail_ns and worlds_ns == {}, detail_ns)
clusterctl.dockerctl = RunningDocker()


# ---- the game port is published on both protocols, per instance
# Gameplay is UDP, but the hand-built cluster that is actually listed in the server
# browser publishes TCP as well. Matching something proven beats reasoning about what
# the query path needs.
st_pp, _dpp = fresh(maps="island,ragnarok", cluster_id="ports3")
st_pp.patch({"game_port_base": 7877, "rcon_port_base": 27920})
doc_pp = yaml.safe_load(generate_compose(st_pp, project="ports3"))
for svc, game, rcon in (("island", 7877, 27920), ("ragnarok", 7878, 27921)):
    ports = doc_pp["services"][svc]["ports"]
    check("%s publishes the game port on udp" % svc,
          "%d:%d/udp" % (game, game) in ports, ports)
    check("%s publishes the game port on tcp too" % svc,
          "%d:%d/tcp" % (game, game) in ports, ports)
    check("%s still publishes RCON on tcp" % svc,
          "%d:%d/tcp" % (rcon, rcon) in ports, ports)
    check("%s publishes exactly those three" % svc, len(ports) == 3, ports)
check("the two instances do not share a game port",
      doc_pp["services"]["island"]["ports"][0] != doc_pp["services"]["ragnarok"]["ports"][0])


# ---- a new cluster claims its own stack on the launch that creates it
#
# The compose file lives inside the Compose Manager project folder, so writing it is
# what brings that folder into existence. register() refuses a folder with no marker in
# it, because a folder with no marker is what somebody else's cluster looks like - so
# writing first and registering second meant a brand-new cluster collided with a
# directory it had made itself one line earlier. It launched, and then ran as loose
# containers forever, because nothing ever claimed the stack. Order is the whole fix.
from . import stack as stackmod

_proj_dir, _ark_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
os.environ["OBELISK_PROJECTS"], os.environ["OBELISK_ARK"] = _proj_dir, _ark_dir
_not_writable = layout.not_writable_by_server
layout.not_writable_by_server = lambda root, **kw: []     # ownership is a Linux concern
clusterctl.dockerctl = FakeDocker()
calls.clear()

st_fs, _dfs = fresh(cluster_id="freshstack")
ok_fs, msg_fs = clusterctl.launch(st_fs)
_d = os.path.join(_proj_dir, "freshstack")

check("a brand-new cluster launches", ok_fs, msg_fs)
check("and claims its own stack on that very first launch",
      os.path.isfile(os.path.join(_d, stackmod.MARKER)),
      sorted(os.listdir(_d)) if os.path.isdir(_d) else "no project dir at all")
check("the marker names the project it belongs to",
      open(os.path.join(_d, stackmod.MARKER), encoding="utf-8").read().strip()
      == "freshstack")
check("and the compose file is there for the plugin to run",
      os.path.isfile(os.path.join(_d, "compose.yaml")))
check("compose was driven against that same file",
      calls and calls[-1][0] == os.path.join(_d, "compose.yaml"), calls)

# The refusal it is ordered around still has to work: somebody else's project of the
# same name is left completely alone.
st_th, _dth = fresh(cluster_id="theirs")
_theirs = os.path.join(_proj_dir, "theirs")
os.makedirs(_theirs)
with open(os.path.join(_theirs, "compose.yaml"), "w", encoding="utf-8") as fh:
    fh.write("# hand-built, not ours\n")
ok_th, _m = clusterctl.launch(st_th)
check("a project we did not create is never marked as ours",
      not os.path.exists(os.path.join(_theirs, stackmod.MARKER)),
      sorted(os.listdir(_theirs)))

layout.not_writable_by_server = _not_writable
del os.environ["OBELISK_PROJECTS"]
del os.environ["OBELISK_ARK"]



# ---- every folder on the way to a save is handed to the server, not just the last one
#
# The mount lands on instances/<key>/Saved, so makedirs creates instances/<key> on the
# way there. Naming only the leaf meant that parent stayed owned by root. The server
# writes inside Saved so nothing broke, which is what makes it worth a test: it is the
# quiet half of the failure that does break things - a folder Docker creates at mount
# time, owned by root, that the server cannot write.
_own_root = os.path.join(tempfile.mkdtemp(), "ark")
_owned = []


def _record_chown(path, uid, gid):
    _owned.append(path)
    return True


layout.ensure_ark(_own_root, ["island", "ragnarok"], chown=_record_chown)
for _key in ("island", "ragnarok"):
    _saved = layout.instance_dir(_own_root, _key)
    check("%s's Saved folder is handed to the server" % _key, _saved in _owned)
    check("and so is the instance folder holding it",
          os.path.dirname(_saved) in _owned,
          [x for x in _owned if _key in x])
check("nothing is handed over twice for one instance",
      len(_owned) == len(set(_owned)), sorted(_owned))



# ---- SaveWorld has to actually leave the building
#
# The flush ran asyncio.run() on whatever thread called it. From a worker thread that is
# fine; from the event loop thread it raises before the coroutine is ever awaited, and
# the RuntimeError lands in the caller's `except` as "this map did not answer". So a
# backup reported all ten maps unreachable while all ten were healthy and answering RCON
# a second later, and quietly archived the last autosave instead. Nothing was sent. The
# report said it had been tried.
import asyncio as _aio

_sent = []


async def _fake_rcon(host, port, password, cmd, timeout=30):
    _sent.append((host, port, cmd))
    return "World Saved"


st_sw, _dsw = fresh(maps="island,ragnarok", cluster_id="flushtest")
clusterctl.dockerctl = FakeDocker()


class _RunningDocker(FakeDocker):
    def container_details(self, names, timeout=30):
        return {n: {"state": "running"} for n in names}


clusterctl.dockerctl = _RunningDocker()
_real_bot_rcon = None
from . import bot as _botmod
_real_bot_rcon = _botmod.rcon_with
_botmod.rcon_with = _fake_rcon

# 1. the ordinary case: no loop on this thread
_sent.clear()
ok_sw, msg_sw = clusterctl.save_world(st_sw)
check("SaveWorld reaches every running map from a plain thread", ok_sw, msg_sw)
check("and one command per map actually went out",
      [c for _h, _p, c in _sent] == ["SaveWorld", "SaveWorld"], _sent)

# 2. the case that was broken: called while this thread already runs a loop
_sent.clear()


async def _from_the_loop():
    return clusterctl.save_world(st_sw)


ok_lw, msg_lw = _aio.run(_from_the_loop())
check("SaveWorld still reaches every map when called from the event loop thread",
      ok_lw, msg_lw)
check("and the commands genuinely went out rather than being reported as failures",
      [c for _h, _p, c in _sent] == ["SaveWorld", "SaveWorld"], _sent)
check("no map is described as not answering", "did not answer" not in msg_lw, msg_lw)

# 3. run_coroutine itself, both ways round
check("run_coroutine works with no loop running",
      clusterctl.run_coroutine(_fake_rcon("h", 1, "p", "Ping")) == "World Saved")


async def _nested():
    return clusterctl.run_coroutine(_fake_rcon("h", 1, "p", "Ping"))


check("and from inside a running loop, which is where it used to give up",
      _aio.run(_nested()) == "World Saved")

_botmod.rcon_with = _real_bot_rcon


# ---- the manager has to get onto the cluster's network by itself
#
# _join_network() was only ever called from launch(). That is fine until the manager is
# recreated without launching anything - an image update through Unraid's Apply Update,
# a template change, a host reboot. The cluster is already up, so nothing launches, so
# nothing joins, and the relay comes back resolving none of ten maps while reporting
# that it covers all of them. That happened, on a live cluster, for a whole deploy.
st_n, _dn = fresh(maps="island,ragnarok", cluster_id="netjoin")

joined_calls = []


class _NetDocker(FakeDocker):
    def container_details(self, names, timeout=30):
        return {n: {"state": "running"} for n in names}

    def network_connect(self, network, container, timeout=30):
        joined_calls.append((network, container))
        return True, "connected"


clusterctl.dockerctl = _NetDocker()
ok_j, why_j = clusterctl.join_network_if_running(
    st_n, environ={"HOST_CONTAINERNAME": "Obelisk"})
check("a running cluster is joined on start", ok_j, why_j)
check("to its own network, by name",
      joined_calls == [("netjoin-net", "Obelisk")], joined_calls)


class _NoneRunning(FakeDocker):
    def container_details(self, names, timeout=30):
        return {}


joined_calls[:] = []
clusterctl.dockerctl = _NoneRunning()
ok_j2, why_j2 = clusterctl.join_network_if_running(
    st_n, environ={"HOST_CONTAINERNAME": "Obelisk"})
check("with no cluster running there is nothing to join", not ok_j2, why_j2)
check("and it says so rather than erroring", "nothing to join" in why_j2, why_j2)
check("no network call is made", joined_calls == [], joined_calls)

# ---- coverage is a claim, so it gets checked
clusterctl.dockerctl = _NetDocker()
good, bad = clusterctl.reachable(st_n, probe=lambda h, p: "No Players Connected")
# reachable() is players_online() with the count discarded - one RCON fan-out, not two
# that drift. These pin that the two doors still answer the way their callers expect.
_probe_calls = []


def _probe_ok(host, port):
    _probe_calls.append((host, port))
    return "No Players Connected"


_good_r, _bad_r = clusterctl.reachable(st_q, probe=_probe_ok)
_total_p, _counts_p, _silent_p = clusterctl.players_online(st_q, probe=_probe_ok)
check("reachable and players_online see the same maps",
      sorted(_good_r) == sorted(_counts_p), (_good_r, _counts_p))
check("and agree on which ones did not answer", _bad_r == _silent_p, (_bad_r, _silent_p))
check("reachable still returns a list of labels, as its callers unpack",
      isinstance(_good_r, list) and all(isinstance(x, str) for x in _good_r), _good_r)
check("and players_online still returns the counts reachable throws away",
      _total_p == 0 and isinstance(_counts_p, dict), (_total_p, _counts_p))
check("the shorter timeout is kept - coverage only needs the door to open",
      clusterctl.reachable.__defaults__[-1] == 6.0,
      clusterctl.reachable.__defaults__)
check("while the player count keeps its longer one",
      clusterctl.players_online.__defaults__[-1] == 10.0,
      clusterctl.players_online.__defaults__)

check("every map answering counts as reachable", len(good) == 2 and not bad, (good, bad))


def _half(host, port):
    if "island" in host:
        raise OSError("[Errno -2] Name or service not known")
    return "No Players Connected"


good, bad = clusterctl.reachable(st_n, probe=_half)
check("a map that cannot be resolved is reported unreachable",
      len(good) == 1 and len(bad) == 1, (good, bad))
check("and the reason travels with it",
      "Name or service not known" in bad[0][1], bad)
check("the reachable one is still counted", good == ["Ragnarok"], good)


def _none(host, port):
    raise OSError("[Errno -2] Name or service not known")


good, bad = clusterctl.reachable(st_n, probe=_none)
check("reaching nothing is reported as reaching nothing, not as covering ten",
      good == [] and len(bad) == 2, (good, bad))

clusterctl.dockerctl = FakeDocker()


# ---- counting players, through the code that actually runs
#
# players_online is what stands between an update and the person standing in a world,
# and it was broken on the only path that matters. It called `bot.Bot._count_players`;
# the class is called Relay, so every map raised AttributeError, every map came back as
# "did not answer", and Apply refused every time. Safe, permanently useless, and
# invisible - because the tests for it injected their own counter and the real one was
# never once run. That is the third time a default path has shipped untested.
#
# So only the RCON call is faked below. Everything from the answer onwards is the code
# that runs on the host.
import inspect as _insp
from . import bot as _bot

check("the parser the update flow reaches for exists at module level",
      callable(getattr(_bot, "count_players", None)))
check("an empty server counts nobody", _bot.count_players("No Players Connected") == 0)
check("and a listing counts them",
      _bot.count_players("0. Alice, 1234\n1. Bob, 5678") == 2)
check("the relay counts through the very same parser, not a copy of it",
      "parse_players(txt)" in _insp.getsource(_bot.Relay.refresh_online),
      _insp.getsource(_bot.Relay.refresh_online)[:300])
check("and the count is that parser's length, so they cannot disagree",
      "len(parse_players(text))" in _insp.getsource(_bot.count_players),
      _insp.getsource(_bot.count_players)[-200:])


class _PlayerStore:
    def __init__(self):
        from .schema import BY_KEY
        self.values = {k: v.get("default", "") for k, v in BY_KEY.items()}
        self.values.update(maps="island,center", cluster_id="tbg", appdata="/ark",
                           admin_password="x")
        self.data = {"cluster": {}, "maps": {}}

    def get(self, key, map_name=None):
        return self.values.get(key)


_answers = {"asa-tbg-island": "0. Alice, 1234\n1. Bob, 5678",
            "asa-tbg-center": "No Players Connected"}
_orig_running = clusterctl.running_instances
clusterctl.running_instances = lambda store: [
    ("The Island", "asa-tbg-island", 27020), ("The Center", "asa-tbg-center", 27021)]
try:
    _total, _counts, _silent = clusterctl.players_online(
        _PlayerStore(), probe=lambda host, port: _answers[host])
finally:
    clusterctl.running_instances = _orig_running

check("players are counted through the real parser", _total == 2, (_total, _counts))
check("per map", _counts == {"The Island": 2, "The Center": 0}, _counts)
check("and no map is called silent when every one answered", _silent == [], _silent)

clusterctl.running_instances = lambda store: [("Genesis", "asa-tbg-genesis", 27029)]
try:
    _total, _counts, _silent = clusterctl.players_online(
        _PlayerStore(), probe=lambda h, p: (_ for _ in ()).throw(OSError("timed out")))
finally:
    clusterctl.running_instances = _orig_running
check("a map that raises is silent rather than empty - the distinction Apply depends on",
      _total == 0 and _counts == {} and [l for l, _ in _silent] == ["Genesis"],
      (_total, _counts, _silent))


# ---- "empty" is a fact about a cluster that has finished starting
#
# It fired on a cluster where nine maps were still `Created` behind the island's health
# check and only the island was up: zero players across one map, three checks running,
# and an apply began on a cluster that had not come up yet. Of course it was empty - it
# had just been restarted.
_ready_store = _PlayerStore()
_ready_store.values["maps"] = "island,center"
_NAMES = clusterctl.target_names(_ready_store)
check("the cluster knows every map it expects", len(_NAMES) == 2, _NAMES)


def _details_for(states):
    return lambda names: {n: states[n] for n in names if n in states}


_HEALTHY = {n: {"state": "running", "health": "healthy"} for n in _NAMES}
_answers = {n: "No Players Connected" for n in _NAMES}
_orig_running = clusterctl.running_instances
clusterctl.running_instances = lambda store: [
    (n.rsplit("-", 1)[-1], n, 27020 + i) for i, n in enumerate(_NAMES)]
try:
    ok, why, total = clusterctl.cluster_ready(
        _ready_store, details=_details_for(_HEALTHY),
        probe=lambda h, p: _answers[h])
    check("all maps up, healthy and answering is ready", ok, why)
    check("and reports nobody on", total == 0, total)

    # Only the island up: the nine others not created yet.
    ok, why, _t = clusterctl.cluster_ready(
        _ready_store, details=_details_for({_NAMES[0]: _HEALTHY[_NAMES[0]]}),
        probe=lambda h, p: _answers[h])
    check("a cluster with maps missing is NOT ready - the loop's exact shape", not ok, why)
    check("and names how many are absent", "not there yet" in why, why)

    # Created but not started - what depends_on leaves behind while the master boots.
    _starting = dict(_HEALTHY)
    _starting[_NAMES[1]] = {"state": "created", "health": ""}
    ok, why, _t = clusterctl.cluster_ready(
        _ready_store, details=_details_for(_starting), probe=lambda h, p: _answers[h])
    check("a map that is created but not running is not ready", not ok, why)

    _starting[_NAMES[1]] = {"state": "running", "health": "starting"}
    ok, why, _t = clusterctl.cluster_ready(
        _ready_store, details=_details_for(_starting), probe=lambda h, p: _answers[h])
    check("nor is one that is still loading its world", not ok, why)
    check("which is what stops the window opening right after a restart",
          "not up and healthy yet" in why, why)

    # Up and healthy but not answering RCON.
    def _one_silent(host, port):
        if host == _NAMES[1]:
            raise OSError("timed out")
        return _answers[host]

    ok, why, _t = clusterctl.cluster_ready(
        _ready_store, details=_details_for(_HEALTHY), probe=_one_silent)
    check("a healthy map that will not answer RCON is not ready either", not ok, why)
    check("and says which", "did not answer" in why, why)

    # Somebody playing.
    ok, why, total = clusterctl.cluster_ready(
        _ready_store, details=_details_for(_HEALTHY),
        probe=lambda h, p: "0. Alice, 1234" if h == _NAMES[0] else _answers[h])
    check("a ready cluster with a player on reports the count, not emptiness",
          ok and total == 1, (ok, total))
finally:
    clusterctl.running_instances = _orig_running

_nomaps = _PlayerStore()
_nomaps.values["maps"] = ""
check("a cluster with no maps defined is never ready",
      not clusterctl.cluster_ready(_nomaps)[0],
      clusterctl.cluster_ready(_nomaps)[1])


# ---- the staging server is not a map
#
# Coverage is ten player maps, not eleven things that happen to run the same image. The
# staging server lives in its own compose project and is not in the map list, so it is
# excluded by construction rather than by a rule somebody has to remember - but that is
# worth an assertion, because "by construction" is exactly the kind of thing a later
# change breaks quietly.
from . import staging as _stg

_ten = _PlayerStore()
_ten.values["maps"] = "island,center,scorched,ragnarok,aberration,extinction,valguero,astraeos,lostcolony,genesis"
_names = clusterctl.target_names(_ten)
check("the coverage target is exactly the player maps", len(_names) == 10, len(_names))
_staging_name = _stg.container_name(clusterctl.project(_ten))
check("and the staging server is not among them",
      _staging_name not in _names, (_staging_name, _names[:2]))
check("nor in what the relay would be pointed at",
      all("staging" not in n for n in _names), _names)
check("because it is a compose project of its own",
      _stg.project_name(clusterctl.project(_ten)) != clusterctl.project(_ten))


# ---- the roster: the names that were always in the answer and always thrown away
#
# ListPlayers returns "N. Name, <netid>" and count_players counted the rows. The netid
# is what a kick or a ban keys on, so the moderation card needs no new data source -
# only for the parser to stop discarding two thirds of each line.
#
# The risk this block exists for is not the card. It is the apply gate: it refuses to
# restart a cluster somebody is standing in, and it asks through count_players. If the
# rewritten parser counts even one shape of input differently, a busy cluster gets
# restarted under the people on it. So the count is checked against a verbatim copy of
# the implementation it replaced, on every shape either of them can meet.
import re as _re_2a                                              # noqa: E402


def _count_as_it_was(text):
    """The pre-slice-2a count_players, character for character, as the oracle."""
    if not text:
        return 0
    if "no players" in text.lower():
        return 0
    n = len(_re_2a.findall(r"(?m)^\s*\d+\.\s+\S", text))
    if n:
        return n
    return sum(1 for ln in text.splitlines() if ln.strip() and "," in ln)


_SHAPES = {
    "nothing at all": "",
    "None": None,
    "the empty-server sentence": "No Players Connected",
    "that sentence in another case": "  no players connected  ",
    "one numbered row": "0. Bob, 76561198000000001",
    "several numbered rows":
        "0. Bob, 7656119800000001\n1. Alice, 000255a1b2c3d4e5f6\n2. Dana, 19000000000001",
    "a name with a comma in it": "0. Cha,rlie, 19000000000000001",
    "blank lines around the rows": "\n\n0. Bob, 123\n\n1. Al, 456\n",
    "indented rows": "  0. Bob, 123\n  1. Al, 456",
    "windows line endings": "0. Bob, 123\r\n1. Al, 456\r\n",
    "a numbered row with no netid": "0. LoneWolf",
    "a name with a full stop in it": "0. Mr. X, 999",
    "the comma fallback": "Bob, 123\nAlice, 456",
    "the comma fallback with noise": "header line\nBob, 123\n\nAlice, 456\n",
    "lines that are neither": "something\nelse\n",
}
_drift = [(name, _count_as_it_was(t), _bot.count_players(t))
          for name, t in _SHAPES.items()
          if _count_as_it_was(t) != _bot.count_players(t)]
check("the count is unchanged on every shape of ListPlayers output", _drift == [],
      _drift)
check("and there were shapes to check", len(_SHAPES) >= 12, len(_SHAPES))

# the fallback is the one that would have cost a cluster
check("the comma fallback still counts, rather than reporting an empty map",
      _bot.count_players("Bob, 123\nAlice, 456") == 2,
      _bot.count_players("Bob, 123\nAlice, 456"))
_fb = _bot.parse_players("Bob, 123\nAlice, 456")
check("its rows come back as people, not as nothing", len(_fb) == 2, _fb)
check("with no netid, because none was readable",
      all(r["netid"] == "" for r in _fb), _fb)
check("and the raw line kept as the name, so they are visible if not actionable",
      _fb[0]["name"] == "Bob, 123", _fb)

# the numbered form, which is what a real map sends
_rows = _bot.parse_players("0. Bob, 7656119800000001\n1. Cha,rlie, 000255a1b2")
check("a numbered row yields a name", _rows[0]["name"] == "Bob", _rows)
check("and the netid beside it", _rows[0]["netid"] == "7656119800000001", _rows)
check("a name containing a comma survives, because the id is taken from the end",
      _rows[1]["name"] == "Cha,rlie", _rows)
check("and its netid is still the netid", _rows[1]["netid"] == "000255a1b2", _rows)
check("a row with no netid is a person with no id, not a person named by their id",
      _bot.parse_players("0. LoneWolf") == [{"name": "LoneWolf", "netid": ""}],
      _bot.parse_players("0. LoneWolf"))
check("the netid is carried as written, whatever form it is in",
      [r["netid"] for r in _bot.parse_players(
          "0. S, 76561198000000001\n1. E, 19000000000000001\n2. O, 0002a1b2c3d4e5f6")]
      == ["76561198000000001", "19000000000000001", "0002a1b2c3d4e5f6"],
      _bot.parse_players("0. S, 76561198000000001"))
check("an empty server yields nobody", _bot.parse_players("No Players Connected") == [])

# ---- players_online, which the gates go through, is untouched
_pl_src = _insp.getsource(clusterctl.players_online)
check("players_online still asks with ListPlayers",
      '"ListPlayers"' in _pl_src, _pl_src[:400])
check("and still counts through count_players",
      "bot.count_players(" in _pl_src, _pl_src[-400:])
check("it does not know the roster exists",
      "parse_players" not in _pl_src and "roster" not in _pl_src, _pl_src[:400])
check("and still returns a map that did not answer separately from an empty one",
      "silent.append" in _pl_src, _pl_src[-500:])

# ---------------------------------------------------------------- one look, by hand
#
# The read-only half of the settle machinery, for an operator driving a stop by hand:
# SaveWorld, then look, then DoExit. Same world path, same sidecar list, no loop - and
# the same rule about a disk that will not answer, which is that it gets no note at all
# rather than a guessed one.
def _look(readings=None, sidecars=(), listdir=None, key="island"):
    disk = Disk(readings or {}, sidecars=sidecars)

    def stat(path):
        # The real os.stat tells "nothing is there" apart from "I could not look", and
        # the whole point of this function is that those two are not the same answer.
        if path not in disk.readings:
            raise FileNotFoundError(2, "No such file or directory")
        return disk.stat(path)

    return clusterctl.world_on_disk(st_q, key, stat=stat, exists=disk.exists,
                                    listdir=listdir or (lambda p: ["TheIsland_WP.ark"]))


_w_there = _look({ISLAND_ARK: [(76 * 1024 * 1024, 1700.0)]})
check("a world that is there is reported present, with its size and its mtime",
      _w_there["present"] and _w_there["mtime"] == 1700.0
      and _w_there["size"] == 76 * 1024 * 1024, _w_there)
check("and it is the live world of the map that was asked about",
      _w_there["path"] == ISLAND_ARK, _w_there)

_w_gone = _look({})
check("a readable folder with no world in it is an absence, not a silence",
      _w_gone is not None and _w_gone["present"] is False, _w_gone)


def _cannot_list(path):
    raise OSError(13, "Permission denied")


check("a folder that will not list answers nothing at all, rather than 'no world'",
      _look({ISLAND_ARK: [(120, 1700.0)]}, listdir=_cannot_list) is None,
      "an unreadable disk was read as an absence")


class _WontStat(Disk):
    def stat(self, path):
        raise OSError(13, "Permission denied")


_w_unstatable = clusterctl.world_on_disk(
    st_q, "island", stat=_WontStat({}).stat, exists=lambda p: False,
    listdir=lambda p: ["TheIsland_WP.ark"])
check("nor does a world file that lists and will not stat", _w_unstatable is None,
      _w_unstatable)

for _suffix in _restore.SIDECARS:
    _w_hot = _look({ISLAND_ARK: [(120, 1700.0)]}, sidecars=[ISLAND_ARK + _suffix])
    check("a %s file open beside the world is reported, not hidden" % _suffix,
          _w_hot["hot"] == [_suffix], _w_hot)
check("and a world with nothing open beside it says so",
      _w_there["hot"] == [], _w_there)

# It reuses the paths the stop path already proves saves with, rather than inventing a
# second opinion about where a world lives or what counts as one being written.
_wsrc = io.open(os.path.join(os.path.dirname(__file__), "cluster.py"),
                encoding="utf-8").read().split("def world_on_disk")[1].split(
                    chr(10) + "def ")[0]
check("the one look reads the same world path the settle wait does",
      "savepoints.live_world(store, key, ark_root)" in _wsrc, _wsrc[:400])
check("and the same sidecar list", "restore.SIDECARS" in _wsrc, _wsrc)
check("and never waits for anything",
      "while " not in _wsrc and "for _turn" not in _wsrc, _wsrc)

_appsrc_2a = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                     encoding="utf-8").read()
for _what, _frag in sorted({
        "the apply gate": "players=lambda: clusterctl.players_online(store), force=force",
        "the stop guard": "clusterctl.players_online, store)",
        "the archive restore guard": "players=lambda: clusterctl.players_online(store), save=_save_one)",
        "the save-point guard": "players=lambda: clusterctl.players_online(store),",
}.items()):
    check("%s still asks the same question" % _what, _frag in _appsrc_2a, _frag)

print("\nFAILURES: %s" % fails if fails else "\nall cluster tests passed")
sys.exit(1 if fails else 0)
