"""
The plan: what Obelisk is about to build, before it builds it.

Every launch goes through here first. It assigns ports, works out per-map memory,
totals it against the host, and reports anything that would produce a stack that
cannot boot. The UI renders it as the review step; the generator consumes the same
object. Nothing else does port maths, so there is one place to get it right.

Deliberately pure: no docker, no filesystem, no network. Ports already in use on the
host are passed in by whoever has the socket, which keeps this testable and keeps the
collision rule honest about what it does and doesn't know.
"""

import re

from . import maps as mapcat
from .schema import BY_KEY

PORT_CEILING = 65535


def _mem_to_mb(value):
    m = re.match(r"^(\d+)([mg])$", str(value).strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    return n if m.group(2) == "m" else n * 1024


def _mb_to_mem(mb):
    if mb % 1024 == 0:
        return "%dg" % (mb // 1024)
    return "%dm" % mb


def _next_free(port, taken, in_use):
    while port in taken or port in in_use:
        port += 1
        if port > PORT_CEILING:
            raise ValueError("ran out of ports below %d - lower the port base" % PORT_CEILING)
    return port


def build_plan(store, in_use_ports=None, host_ram_gb=None):
    """Return the full picture of what would be created.

    in_use_ports: ports already bound on the host, so a cluster never lands on top of
    something else. Absent means 'nobody told us', which is reported rather than
    silently treated as 'nothing is in use'.
    """
    in_use = set(in_use_ports or ())
    known_usage = in_use_ports is not None

    # Default the budget from the store rather than requiring every caller to pass it.
    # A safety check that silently switches itself off when someone forgets an argument
    # is worse than no check at all - the UI did exactly that and offered a Launch
    # button for a cluster that would have overcommitted the host.
    if host_ram_gb is None:
        host_ram_gb = store.get("host_ram_gb") or None

    raw = store.get("maps")
    keys = [k.strip() for k in str(raw).split(",") if k.strip()] if isinstance(raw, str) else list(raw)
    chosen = mapcat.resolve(keys)

    base_mb = _mem_to_mb(store.get("mem_limit"))
    game_port = int(store.get("game_port_base"))
    rcon_port = int(store.get("rcon_port_base"))

    rows, taken, problems, notes = [], set(), [], []

    for i, m in enumerate(chosen):
        key = m["key"]
        g = _next_free(game_port, taken, in_use)
        taken.add(g)
        r = _next_free(rcon_port, taken, in_use)
        taken.add(r)
        game_port, rcon_port = g + 1, r + 1

        # A per-map override always wins; otherwise the map's weight scales the base.
        override = store.data["maps"].get(key, {}).get("mem_limit")
        if override:
            mem, why = override, "set for this map"
        else:
            w = mapcat.weight(key)
            mem = _mb_to_mem(int(round(base_mb * w / 1024.0)) * 1024)
            why = "base" if w == 1.0 else "base x%.1f - this map runs heavy" % w

        rows.append({"map": key, "name": m["name"], "map_id": m["map_id"],
                     "game_port": g, "rcon_port": r,
                     "memory": mem, "memory_mb": _mem_to_mb(mem), "memory_why": why,
                     "role": "update master" if i == 0 else "follower"})

    # ---- the things that make a stack unbootable, caught before it is written
    status_port = int(store.get("status_port"))
    if status_port in taken:
        problems.append("A map has been given port %d, which is Obelisk's own web port. "
                        "Move the port base, or change the WebUI port on this container."
                        % status_port)
    if known_usage:
        clash = sorted(p for p in taken if p in in_use)
        if clash:
            problems.append("ports already in use on this host: %s"
                            % ", ".join(str(p) for p in clash))
        # Obelisk's own port is deliberately not checked against the host here. It is
        # in use - by Obelisk, which is serving the page this plan is displayed on. The
        # generated stack no longer contains a manager service, so nothing in it will
        # try to bind that port. Checking it meant every launch from a running Obelisk
        # was refused for colliding with itself, and told the operator to free a port
        # that was already free.
    else:
        notes.append("Host port usage wasn't checked, so a clash with something outside "
                     "this cluster is still possible.")

    total_mb = sum(r["memory_mb"] or 0 for r in rows)
    budget_mb = int(host_ram_gb * 1024) if host_ram_gb else None
    if budget_mb and total_mb > budget_mb:
        problems.append(
            "the maps would cap at %s of RAM, more than the %dg budget for this host. "
            "These are caps rather than reservations, so the total may never be reached - "
            "but if it is, the host starts killing servers. Drop a map or lower the base."
            % (_mb_to_mem(total_mb), host_ram_gb))
    elif budget_mb and total_mb > budget_mb * 0.85:
        notes.append("RAM caps total %s against a %dg budget - close enough to watch."
                     % (_mb_to_mem(total_mb), host_ram_gb))

    for b in store.readiness():
        problems.append("%s still needs setting" % b["label"])

    return {"maps": rows,
            "total_memory": _mb_to_mem(total_mb) if total_mb else "0g",
            "total_memory_mb": total_mb,
            "obelisk_port": status_port,
            "ports_checked": known_usage,
            "problems": problems,
            "notes": notes,
            "ok": not problems}


def describe(plan):
    """The review step as plain text - same content the UI table shows."""
    w = max([len(r["name"]) for r in plan["maps"]] + [4])
    out = ["%-*s  %-5s  %-5s  %-6s  %s" % (w, "MAP", "GAME", "RCON", "RAM", "ROLE"),
           "%-*s  %-5s  %-5s  %-6s  %s" % (w, "-" * w, "-----", "-----", "------", "----")]
    for r in plan["maps"]:
        out.append("%-*s  %-5d  %-5d  %-6s  %s"
                   % (w, r["name"], r["game_port"], r["rcon_port"], r["memory"], r["role"]))
    out.append("")
    out.append("%d map(s), %s of RAM at most, plus Obelisk on port %d."
               % (len(plan["maps"]), plan["total_memory"], plan["obelisk_port"]))
    for n in plan["notes"]:
        out.append("note: " + n)
    for p in plan["problems"]:
        out.append("PROBLEM: " + p)
    return "\n".join(out)
