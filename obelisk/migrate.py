"""
Moving a running cluster onto Obelisk, one map at a time, without losing it.

The cluster being migrated is not replaced by a new one - it is **adopted**. Same cluster
id, same session names, same mods in the same order, same per-map memory, and above all
the same ports, because players have those addresses bookmarked and the cluster id is
what makes a character transfer between maps work at all. The end state is "the same
cluster, now managed by Obelisk", and anything that changes identity has failed at the
thing it was for.

The invariant is the one the whole project runs on: **nothing is taken away until its
replacement is proven**. Per map that means launch, verify, and only then stop the old
one - and a verification that does not pass halts the migration with the old map still
serving players.

Ports are the one thing that cannot simply be carried across, because two containers
cannot hold one port at the same time. So each map moves in two beats: the new instance
comes up on a temporary port and is proven there, then the old map is stopped and the new
one is recreated on the real port. That second beat is a short reconnect for anyone on
that map - it is not hidden, it is reported, and it is the reason the island goes last
and only when nobody is on it.
"""

import logging

log = logging.getLogger("obelisk.migrate")

# Temporary ports live well clear of the real ones so a half-finished migration can never
# collide with the cluster it is migrating.
TEMP_OFFSET = 1000

# The map that goes last. It is where people play, so it is the one that gets the quiet
# window and the freshest possible save.
LAST = "island"


def migration_order(map_keys, last=LAST):
    """The order maps are moved in: everything else first, the busy one last."""
    keys = [k for k in map_keys]
    tail = [k for k in keys if k == last]
    return [k for k in keys if k != last] + tail


def temp_ports(game_port, rcon_port, offset=TEMP_OFFSET):
    """Where a new instance lives while its old one still holds the real port."""
    return game_port + offset, rcon_port + offset


def headroom(largest_map_mb, host_ram_gb, committed_mb):
    """(ok, message) - is there room for one extra map while each one cuts over?

    A rolling cutover runs one map twice for the length of its own step. That is one
    extra map, not one extra cluster, and saying which is the difference between a plan
    somebody can act on and a number that just sounds alarming.
    """
    if not host_ram_gb:
        return True, ("No host RAM budget is set, so the extra map cannot be checked "
                      "against anything. Set one before migrating.")
    budget_mb = int(host_ram_gb) * 1024
    peak = committed_mb + largest_map_mb
    if peak <= budget_mb:
        return True, ("Needs room for one more map than you run today: %.0f GB on top of "
                      "%.0f GB, against a %d GB budget. There is room."
                      % (largest_map_mb / 1024.0, committed_mb / 1024.0, host_ram_gb))
    return False, ("This migration needs room for one more map than you run today - "
                   "%.0f GB on top of %.0f GB, which is %.0f GB over your %d GB budget. "
                   "Either raise the budget, or migrate with a short outage per map "
                   "(stop the old one first, then start its replacement) - that trades "
                   "the overlap for a few minutes offline on one map at a time."
                   % (largest_map_mb / 1024.0, committed_mb / 1024.0,
                      (peak - budget_mb) / 1024.0, host_ram_gb))


def verify(instance, checks):
    """(ok, reasons) - is this new instance genuinely serving?

    Deliberately several independent signals. A container can be `running` while the
    server inside it aborts in a loop, and a world can be loaded while the mods that
    world needs are absent - so "it started" is not evidence and is not accepted here.
    """
    reasons = []
    if not checks.get("running"):
        reasons.append("the container is not running")
    if not checks.get("healthy"):
        reasons.append("it has not reported healthy")
    if not checks.get("rcon"):
        reasons.append("RCON is not answering")
    if not checks.get("save_present"):
        reasons.append("no world save has been written")
    wanted = list(checks.get("mods_expected") or [])
    got = list(checks.get("mods_loaded") or [])
    if wanted and got != wanted:
        reasons.append("mods differ from the live cluster (wanted %s, loaded %s)"
                       % (",".join(wanted), ",".join(got)))
    return (not reasons), reasons


def quiet_window(map_key, players, forced=False, last=LAST):
    """(ok, message) - may this map be cut over now?

    Only the last map is gated on being empty. Everywhere else the overlap covers it;
    on the busy one, a reconnect during the port swap is something a person should be
    asked about rather than have happen to them.
    """
    if map_key != last:
        return True, "not the final map - no quiet window needed"
    n = len(players or [])
    if n == 0:
        return True, "nobody is on it"
    if forced:
        return True, ("%d player(s) on it and the migration was forced - they will be "
                      "disconnected during the port swap" % n)
    return False, ("%d player(s) are on %s. This is the last map and its cutover drops "
                   "anyone connected, so it waits. Try again when it is empty, or force "
                   "it deliberately." % (n, map_key))


def plan_steps(rows, order=None, last=LAST):
    """The whole migration as a list of steps, in the order they happen.

    `rows` are the live maps: {"map", "name", "game_port", "rcon_port", "memory"}.
    """
    by_key = {r["map"]: r for r in rows}
    keys = order or migration_order([r["map"] for r in rows], last=last)
    steps = [{"step": "prestage", "map": None,
              "what": "Download the server files into the new cluster's folder, so no "
                      "map's cutover waits on a 12 GB download."}]
    for i, key in enumerate(keys, 1):
        r = by_key[key]
        tg, tr = temp_ports(r["game_port"], r["rcon_port"])
        steps.append({
            "step": "cutover", "map": key, "name": r["name"], "position": i,
            "is_last": key == last,
            "temp_game": tg, "temp_rcon": tr,
            "real_game": r["game_port"], "real_rcon": r["rcon_port"],
            "memory": r.get("memory"),
            "what": ("Launch %s on temp ports %d/%d, verify it, stop the live map, then "
                     "recreate it on its real ports %d/%d and verify again."
                     % (r["name"], tg, tr, r["game_port"], r["rcon_port"])),
            "gate": ("Waits for an empty map - this is where people play."
                     if key == last else
                     "Old map keeps serving until the new one is proven."),
        })
    steps.append({"step": "finish", "map": None,
                  "what": "Retire the old stack's containers, leaving the migrated "
                          "cluster in their place on the same ports."})
    return steps


def adopt(store, live):
    """Copy the live cluster's identity into the store. Returns what changed.

    Identity is the point of the exercise: the cluster id is what lets a character move
    between maps, the ports are what players have bookmarked, and the mod order decides
    which mod wins a conflict. A migration that quietly renumbers any of it has produced
    a different cluster that merely looks similar.
    """
    wanted = {
        "cluster_id": live.get("cluster_id"),
        "session_prefix": live.get("session_prefix"),
        "session_tags": live.get("session_tags"),
        "mod_ids": live.get("mod_ids"),
        "max_players": live.get("max_players"),
        "battleye": live.get("battleye"),
        "timezone": live.get("timezone"),
        "game_port_base": live.get("game_port_base"),
        "rcon_port_base": live.get("rcon_port_base"),
        "mem_limit": live.get("mem_limit"),
        "maps": live.get("maps"),
    }
    changes = {k: v for k, v in wanted.items() if v not in (None, "")}
    store.patch(changes)
    for map_key, mem in (live.get("per_map_memory") or {}).items():
        store.patch({"mem_limit": mem}, map_name=map_key)
    log.info("adopted the live cluster's identity: %s", ", ".join(sorted(changes)))
    return changes


def master_instance(order, last=LAST):
    """Which map holds the update master's job *while a migration is running*.

    In a settled cluster the master is the first map, and the first map is the island.
    But the island is deliberately the last thing to move, so for the whole of a rolling
    migration the master does not exist yet - and a follower that finds a new build
    published waits for it, forever, looking exactly like a slow start. That cost a map
    twenty minutes of silence before anyone learned it was never going to come up.

    So during a migration the job belongs to the first map that actually moved. It is
    already running by the time the second one starts, which is the only property the
    role needs.
    """
    order = [k for k in order]
    return order[0] if order else last


def build_gate(installed, available, master_running):
    """(ok, message) - may a cutover start with these two build ids?

    Matching builds are the ordinary case and pass. When they differ, whether this is
    safe turns entirely on whether the master is up: with a master, the cluster updates
    the way it is designed to; without one, every follower stalls. Refusing here costs
    seconds. Finding out from a health check costs twenty minutes per map, and says
    "never became healthy" rather than why.
    """
    if not installed or not available or str(installed) == str(available):
        return True, "the installed build is current"
    if master_running:
        return True, ("build %s is out and %s is installed; the update master is running "
                      "and will fetch it" % (available, installed))
    return False, (
        "A new server build is out (%s installed, %s available) and the update master "
        "is not running, so this map would wait for it and never start. Either pre-stage "
        "the new build before cutting over, or pin this run to the installed build and "
        "update the whole cluster together once the master has migrated."
        % (installed, available))
