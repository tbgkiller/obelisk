"""
Migrating a live cluster onto Obelisk without losing it.

Two things this suite is really about. The migrated cluster must keep the *identity* of
the one it replaces - same cluster id, ports, mods and order - because a migration that
renumbers any of that has produced a different cluster that merely resembles the old one.
And nothing is taken away until its replacement is proven, which means a verification
that fails has to halt with the old map still serving.

Values here mirror the shape of a real ten-map cluster without being anyone's.
"""

import os, sys, tempfile

from . import migrate
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


ROWS = [
    {"map": "island", "name": "The Island", "game_port": 7777, "rcon_port": 27020, "memory": "20g"},
    {"map": "center", "name": "The Center", "game_port": 7778, "rcon_port": 27021, "memory": "20g"},
    {"map": "scorched", "name": "Scorched Earth", "game_port": 7779, "rcon_port": 27022, "memory": "20g"},
    {"map": "astraeos", "name": "Astraeos", "game_port": 7784, "rcon_port": 27027, "memory": "32g"},
]

# ---------------------------------------------------------------- order
order = migrate.migration_order([r["map"] for r in ROWS])
check("the busy map goes last", order[-1] == "island", order)
check("every map is still in the plan", sorted(order) == sorted(r["map"] for r in ROWS), order)
check("the others keep their order", order[:3] == ["center", "scorched", "astraeos"], order)
check("a cluster without that map is unaffected",
      migrate.migration_order(["center", "scorched"]) == ["center", "scorched"])

# ---------------------------------------------------------------- ports
tg, tr = migrate.temp_ports(7777, 27020)
check("temp ports are clear of the live ones", tg == 8777 and tr == 28020, (tg, tr))
check("no temp port collides with any live port",
      not ({migrate.temp_ports(r["game_port"], r["rcon_port"])[0] for r in ROWS} &
           {r["game_port"] for r in ROWS}))
check("and no temp RCON port collides either",
      not ({migrate.temp_ports(r["game_port"], r["rcon_port"])[1] for r in ROWS} &
           {r["rcon_port"] for r in ROWS}))

# ---------------------------------------------------------------- headroom
# A 20 GB map on a 251 GB host committing 220 GB fits with room to spare.
ok, msg = migrate.headroom(largest_map_mb=20 * 1024, host_ram_gb=251,
                           committed_mb=220 * 1024)
check("a normal map fits alongside the live cluster", ok, msg)
check("and it says one map, not one cluster", "one more map" in msg, msg)

# The heaviest map on that same host does NOT - 220 + 32 is 252 against 251. This is
# exactly the case the check exists for: it is one gigabyte, and finding it during a
# cutover rather than before one is the difference between a plan and an incident.
ok, msg = migrate.headroom(largest_map_mb=32 * 1024, host_ram_gb=251,
                           committed_mb=220 * 1024)
check("the heaviest map overflowing by 1 GB is caught", not ok, msg)
check("and the shortfall is stated precisely", "1 GB over" in msg, msg)

ok, msg = migrate.headroom(largest_map_mb=32 * 1024, host_ram_gb=64,
                           committed_mb=60 * 1024)
check("it refuses when there is no room", not ok, msg)
check("the refusal offers the short-outage alternative",
      "short outage" in msg and "stop the old one first" in msg, msg)
check("and says how far over it is", "over your 64 GB budget" in msg, msg)

ok, msg = migrate.headroom(largest_map_mb=20 * 1024, host_ram_gb=0, committed_mb=0)
check("no budget set is reported rather than assumed safe", "cannot be checked" in msg, msg)

# ---------------------------------------------------------------- verification
good = {"running": True, "healthy": True, "rcon": True, "save_present": True,
        "mods_expected": ["929110", "940003"], "mods_loaded": ["929110", "940003"]}
ok, why = migrate.verify("center", good)
check("a genuinely serving instance verifies", ok, why)

for field, label in [("running", "a stopped container"), ("healthy", "an unhealthy one"),
                     ("rcon", "one whose RCON is silent"),
                     ("save_present", "one with no world written")]:
    bad = dict(good, **{field: False})
    ok, why = migrate.verify("center", bad)
    check("%s does not verify" % label, not ok, why)

ok, why = migrate.verify("center", dict(good, mods_loaded=["940003", "929110"]))
check("mods in the wrong ORDER do not verify", not ok, why)
check("and the reason names both lists", "wanted" in why[0] and "loaded" in why[0], why)

ok, why = migrate.verify("center", dict(good, mods_loaded=["929110"]))
check("a missing mod does not verify", not ok, why)

# ---------------------------------------------------------------- the quiet window
ok, msg = migrate.quiet_window("center", players=["someone"])
check("a non-final map does not wait for an empty server", ok, msg)

ok, msg = migrate.quiet_window("island", players=[])
check("the final map goes when it is empty", ok, msg)

ok, msg = migrate.quiet_window("island", players=["swaXXXX"])
check("the final map WAITS while somebody is on it", not ok, msg)
check("and says why, in terms of the player", "drops anyone connected" in msg, msg)

ok, msg = migrate.quiet_window("island", players=["swaXXXX"], forced=True)
check("forcing it is possible but explicit", ok, msg)
check("and warns they will be disconnected", "disconnected" in msg, msg)

# ---------------------------------------------------------------- the whole plan
steps = migrate.plan_steps(ROWS)
check("it starts by pre-staging the download", steps[0]["step"] == "prestage", steps[0])
check("and ends by retiring the old stack", steps[-1]["step"] == "finish", steps[-1])
cut = [s for s in steps if s["step"] == "cutover"]
check("one cutover per map", len(cut) == len(ROWS), len(cut))
check("the busy map is the last cutover", cut[-1]["map"] == "island", cut[-1]["map"])
check("only that one is flagged as final",
      [s["map"] for s in cut if s["is_last"]] == ["island"])
check("each step names both port pairs",
      all(s["temp_game"] and s["real_game"] and s["temp_rcon"] and s["real_rcon"]
          for s in cut))
check("the end state is the real ports",
      [s["real_game"] for s in cut] == [7778, 7779, 7784, 7777],
      [s["real_game"] for s in cut])
check("every step states its gate", all(s.get("gate") for s in cut))

# ---------------------------------------------------------------- adopting identity
d = os.path.join(tempfile.mkdtemp(), "obelisk")
os.makedirs(d, exist_ok=True)
st = Store(os.path.join(d, "settings.json")).load()
st.patch({"status_port": 8088}, source="install")
st.patch({"cluster_id": "somethingelse", "maps": "island", "admin_password": "pw"})

live = {"cluster_id": "livecluster", "session_prefix": "LIVE",
        "session_tags": "PvE 10x | NoWipe", "mod_ids": "929110,940003,929420",
        "max_players": 250, "battleye": True, "timezone": "America/Chicago",
        "game_port_base": 7777, "rcon_port_base": 27020, "mem_limit": "20g",
        "maps": "island,center,scorched,astraeos",
        "per_map_memory": {"astraeos": "32g"}}
changed = migrate.adopt(st, live)
check("the cluster id is adopted, not invented", st.get("cluster_id") == "livecluster")
check("the ports are adopted so bookmarks keep working",
      st.get("game_port_base") == 7777 and st.get("rcon_port_base") == 27020)
check("the mod list is adopted in order", st.get("mod_ids") == "929110,940003,929420")
check("the session prefix is adopted", st.get("session_prefix") == "LIVE")
check("the map set is adopted", st.get("maps") == "island,center,scorched,astraeos")
check("per-map memory is adopted too",
      st.data["maps"].get("astraeos", {}).get("mem_limit") == "32g", st.data["maps"])
check("and it reports what it changed", "cluster_id" in changed and "mod_ids" in changed)

print("\nFAILURES: %s" % fails if fails else "\nall migrate tests passed")
sys.exit(1 if fails else 0)
