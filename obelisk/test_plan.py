# Fixture values here are deliberately synthetic - no real cluster's ids, times or
# timezone belong in a public repo.
"""Port/RAM planning and presets.  python3 -m obelisk.test_plan"""
import os, shutil, sys, tempfile

from .plan import build_plan, describe
from .presets import PRESETS, BY_KEY as PRESET_BY_KEY
from .maps import BY_KEY as MAP_BY_KEY, weight
from .settings import Store

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)) if detail and not cond else ""))
    if not cond: fails.append(name)

def store(**kw):
    st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
    base = {"admin_password": "pw", "maps": "island,center,scorched"}
    base.update(kw)
    st.patch(base)
    return st

# ---- ports
p = build_plan(store(), in_use_ports=[])
check("ports count up from the base", [r["game_port"] for r in p["maps"]] == [7777, 7778, 7779],
      [r["game_port"] for r in p["maps"]])
check("rcon has its own range", [r["rcon_port"] for r in p["maps"]] == [27020, 27021, 27022])
check("first map is the update master", p["maps"][0]["role"] == "update master")
check("the rest follow", all(r["role"] == "follower" for r in p["maps"][1:]))

p = build_plan(store(), in_use_ports=[7778, 7779, 27021])
check("skips ports already bound on the host",
      [r["game_port"] for r in p["maps"]] == [7777, 7780, 7781], [r["game_port"] for r in p["maps"]])
check("skips a bound rcon port too",
      [r["rcon_port"] for r in p["maps"]] == [27020, 27022, 27023], [r["rcon_port"] for r in p["maps"]])
check("a clash with the host is not silently accepted", p["ok"], p["problems"])

allp = [r["game_port"] for r in p["maps"]] + [r["rcon_port"] for r in p["maps"]]
check("no port is ever assigned twice", len(allp) == len(set(allp)))

p = build_plan(store(game_port_base=8088), in_use_ports=[])
check("catches a collision with Obelisk's own web port",
      not p["ok"] and any("web port" in x for x in p["problems"]), p["problems"])

p = build_plan(store())
check("says so when host ports were never checked",
      not p["ports_checked"] and any("wasn't checked" in n for n in p["notes"]), p["notes"])

# ---- memory
p = build_plan(store(maps="island,astraeos,lostcolony", mem_limit="20g"), in_use_ports=[])
mem = {r["map"]: r["memory"] for r in p["maps"]}
check("a normal map gets the base", mem["island"] == "20g", mem)
check("a heavy map is scaled up", mem["astraeos"] == "32g", mem)
check("weights scale with the base", 
      build_plan(store(maps="astraeos", mem_limit="10g"), in_use_ports=[])["maps"][0]["memory"] == "16g")
check("the plan explains why a map differs", "runs heavy" in
      [r for r in p["maps"] if r["map"] == "astraeos"][0]["memory_why"])
check("totals the caps", p["total_memory"] == "80g", p["total_memory"])

st = store(maps="island,astraeos", mem_limit="20g")
st.patch({"mem_limit": "48g"}, map_name="astraeos")
row = [r for r in build_plan(st, in_use_ports=[])["maps"] if r["map"] == "astraeos"][0]
check("a per-map override beats the weight", row["memory"] == "48g", row)
check("and the plan says it was set by hand", row["memory_why"] == "set for this map")

# ---- host budget
p = build_plan(store(maps="island,center,scorched", mem_limit="20g"), in_use_ports=[], host_ram_gb=32)
check("refuses a cluster that overcommits the host",
      not p["ok"] and any("more than the 32g budget" in x for x in p["problems"]), p["problems"])
check("the refusal explains caps vs reservations",
      any("caps rather than reservations" in x for x in p["problems"]))
p = build_plan(store(maps="island,center", mem_limit="20g"), in_use_ports=[], host_ram_gb=44)
check("warns when close to the budget without refusing",
      p["ok"] and any("close enough to watch" in n for n in p["notes"]), p)
p = build_plan(store(maps="island,center", mem_limit="20g"), in_use_ports=[], host_ram_gb=0)
check("budget of 0 turns the check off", p["ok"])

# The budget must apply even when the caller says nothing - a safety check that turns
# itself off when an argument is forgotten is worse than no check at all.
p = build_plan(store(maps="island,center,scorched", mem_limit="90g", host_ram_gb=32),
               in_use_ports=[])
check("the budget applies without being passed explicitly",
      not p["ok"] and any("budget" in x for x in p["problems"]), p["problems"])

# ---- unfinished setup shows up as a plan problem, not a crash later
st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
st.patch({"maps": "island"})
p = build_plan(st, in_use_ports=[])
check("an unset admin password blocks the plan",
      not p["ok"] and any("Admin / RCON password" in x for x in p["problems"]), p["problems"])

# ---- the review text a person actually reads
txt = describe(build_plan(store(maps="island,astraeos"), in_use_ports=[], host_ram_gb=128))
for must in ("The Island", "Astraeos", "update master", "7777", "27020", "32g", "Obelisk on port 8088"):
    check("review shows %r" % must, must in txt, txt)

# ---- presets carry a shape, never a setup
check("presets reference only known maps",
      all(m in MAP_BY_KEY for p_ in PRESETS for m in p_["maps"]),
      [(p_["key"], m) for p_ in PRESETS for m in p_["maps"] if m not in MAP_BY_KEY])
check("a full-cluster preset exists", "full" in PRESET_BY_KEY and len(PRESET_BY_KEY["full"]["maps"]) == 10)
check("a small preset exists for new users", len(PRESET_BY_KEY["starter"]["maps"]) <= 3)
check("presets carry no settings, only maps",
      all(set(p_) == {"key", "name", "description", "maps"} for p_ in PRESETS),
      [set(p_) for p_ in PRESETS])
check("every preset explains itself", all(len(p_["description"]) > 40 for p_ in PRESETS))

# adopting a preset produces a bootable plan
st = store(maps=",".join(PRESET_BY_KEY["full"]["maps"]), mem_limit="20g")
p = build_plan(st, in_use_ports=[])
check("the full preset plans cleanly", p["ok"], p["problems"])
check("the full preset covers ten maps", len(p["maps"]) == 10)
check("the full preset totals what you'd expect", p["total_memory"] == "220g", p["total_memory"])

# ---- nothing unbootable ever reaches the generator
from .compose import generate_compose
try:
    generate_compose(store(maps="island,center", mem_limit="20g"), in_use_ports=[])
    ok = True
except ValueError:
    ok = False
check("a good plan generates", ok)
try:
    generate_compose(store(maps="island,center,scorched", mem_limit="90g",
                           host_ram_gb=32), in_use_ports=[])
    check("a bad plan is never written out", False)
except ValueError as e:
    check("a bad plan is never written out", "budget" in str(e), str(e))

# ---- reading host port usage from docker
from . import dockerctl
_real = dockerctl._run

dockerctl._run = lambda a, timeout=60: (0,
    "0.0.0.0:8088->8088/tcp, :::8088->8088/tcp\n"
    "0.0.0.0:7777->7777/udp, :::7777->7777/udp\n"
    "7779/udp\n"                       # exposed but not published - not in use on the host
    "0.0.0.0:27020->27020/tcp\n")
got = dockerctl.ports_in_use()
check("reads published host ports", got == {8088, 7777, 27020}, got)
check("ignores a port that is only exposed", 7779 not in got, got)

dockerctl._run = lambda a, timeout=60: (1, "Cannot connect to the Docker daemon")
check("returns None when docker is unreachable", dockerctl.ports_in_use() is None)
# available() has three distinct failure modes and each should name its own cause
import shutil as _sh
_real_which = dockerctl.shutil.which

dockerctl.shutil.which = lambda n: None
ok, msg = dockerctl.available()
check("names a missing docker CLI", not ok and "CLI is missing" in msg, msg)

dockerctl.shutil.which = lambda n: "/usr/bin/docker"
dockerctl._run = lambda a, timeout=60: (1, "Cannot connect to the Docker daemon")
ok, msg = dockerctl.available()
check("names an unmounted socket", not ok and "/var/run/docker.sock" in msg, msg)

def _no_compose(a, timeout=60):
    return (0, "27.0.0") if a[1] == "version" else (1, "unknown command")
dockerctl._run = _no_compose
ok, msg = dockerctl.available()
check("names a missing compose plugin", not ok and "compose plugin" in msg, msg)

dockerctl._run = lambda a, timeout=60: (0, "27.0.0")
ok, msg = dockerctl.available()
check("reports the server version when all is well", ok and "27.0.0" in msg, msg)

dockerctl.shutil.which = _real_which

dockerctl._run = _real

# a plan built from real host usage skips those ports
p = build_plan(store(), in_use_ports={7777, 27020})
check("planning around live host ports", p["maps"][0]["game_port"] == 7778
      and p["maps"][0]["rcon_port"] == 27021, p["maps"][0])

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
