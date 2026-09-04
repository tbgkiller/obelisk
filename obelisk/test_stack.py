"""
Registering an Obelisk cluster as an Unraid stack - and never anyone else's.

The rule that matters more than the feature: Obelisk writes only the project directory
whose name it generated. Somebody's hand-built live cluster sits in the same folder, and
adopting, rewriting or even reading it is not ours to do.

Fixture values are synthetic.
"""

import os, sys, tempfile

from . import stack
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


def fresh(**over):
    root = os.path.join(tempfile.mkdtemp(), "obelisk")
    os.makedirs(root, exist_ok=True)
    st = Store(os.path.join(root, "settings.json")).load()
    st.patch({"status_port": 8088}, source="install")
    st.patch(dict({"maps": "island", "admin_password": "synthetic-pw",
                   "cluster_id": "mycluster", "cluster_name": "My Cluster"}, **over))
    return st


def projects():
    d = os.path.join(tempfile.mkdtemp(), "projects")
    os.makedirs(d, exist_ok=True)
    return d, {"OBELISK_PROJECTS": d}


# ---- an install without the mount is normal, not broken
check("no Compose Manager means not available",
      stack.available({"OBELISK_PROJECTS": "/nowhere/at/all"}) is False)

d, env = projects()
check("a mounted projects folder is available", stack.available(env) is True)

# ---- registering our own project
st = fresh()
ok, where = stack.register(st, "mycluster", "services: {}\n", environ=env)
check("registering succeeds", ok, where)
made = sorted(os.listdir(os.path.join(d, "mycluster")))
check("all five plugin files are written",
      {"name", "project_name", "compose.yaml", "description", "autostart"} <= set(made),
      made)
check("and the icon", "icon" in made, made)
check("and a marker proving Obelisk made it", stack.MARKER in made, made)

def read(name):
    return open(os.path.join(d, "mycluster", name), encoding="utf-8").read().strip()

check("project_name is the compose project", read("project_name") == "mycluster")
check("name matches it", read("name") == "mycluster")
check("the compose file is the generated text", read("compose.yaml") == "services: {}")
check("the description carries the cluster name", "My Cluster" in read("description"))
check("and says not to hand-edit it",
      "do not edit" in read("description").lower(), read("description"))
check("the icon points at the Obelisk logo", read("icon") == stack.ICON_URL)

# ---- autostart follows the cluster's intent
check("a real cluster comes back with the array", read("autostart") == "true")
st_t = fresh(cluster_autostart=False)
stack.register(st_t, "mycluster", "services: {}\n", environ=env)
check("a throwaway cluster does not", read("autostart") == "false")

# ---- re-registering our own project is fine
ok, _w = stack.register(st, "mycluster", "services: {n: 1}\n", environ=env)
check("re-registering our own project updates it", ok and read("compose.yaml") == "services: {n: 1}")

# ---- and now the part that matters: somebody else's project
foreign = os.path.join(d, "ark-asa")
os.makedirs(foreign, exist_ok=True)
open(os.path.join(foreign, "project_name"), "w").write("ark-asa")
open(os.path.join(foreign, "compose.yaml"), "w").write("THE LIVE CLUSTER\n")
open(os.path.join(foreign, "autostart"), "w").write("true")
before = sorted((f, open(os.path.join(foreign, f)).read()) for f in os.listdir(foreign))

# Note it even has a matching project_name - that is exactly the trap. Only the marker
# Obelisk writes counts as proof.
ok, why = stack.owns("ark-asa", env)
check("a foreign project is not ours even when the name matches", ok is False, why)
check("and the refusal names it", "ark-asa" in why, why)
check("and explains the way out", "Rename this cluster" in why, why)

ok, why = stack.register(fresh(cluster_id="ark-asa"), "ark-asa", "OURS\n", environ=env)
check("registering over a foreign project is REFUSED", ok is False, why)
after = sorted((f, open(os.path.join(foreign, f)).read()) for f in os.listdir(foreign))
check("and the foreign project is untouched, byte for byte", before == after, after)
check("its compose file still says what it said",
      open(os.path.join(foreign, "compose.yaml")).read() == "THE LIVE CLUSTER\n")

ok, why = stack.unregister("ark-asa", env)
check("unregistering a foreign project is REFUSED", ok is False, why)
check("and it still exists", os.path.isdir(foreign))

# a directory with no project_name is somebody else's too - we do not guess
mystery = os.path.join(d, "mystery")
os.makedirs(mystery, exist_ok=True)
ok, why = stack.owns("mystery", env)
check("an unlabelled directory is not claimed", ok is False, why)

# ---- we only ever write inside our own directory
watched = []
stack.register(st, "mycluster", "x\n", environ=env,
               write=lambda p, b: watched.append(p))
mine = os.path.join(d, "mycluster")
check("every write lands inside our own project directory",
      all(os.path.abspath(p).startswith(os.path.abspath(mine) + os.sep) for p in watched),
      watched)
check("and nothing is written anywhere else", len(watched) == 7, watched)

# ---- removing our own is allowed
ok, why = stack.unregister("mycluster", env)
check("unregistering our own project works", ok, why)
check("ours is gone", not os.path.isdir(mine))
check("and the foreign one survived it", os.path.isdir(foreign))

print("\nFAILURES: %s" % fails if fails else "\nall stack tests passed")
sys.exit(1 if fails else 0)
