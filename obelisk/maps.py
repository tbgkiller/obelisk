"""
The maps Obelisk knows how to run.

`key` is the stable identifier used everywhere - container name (asa_<key>),
per-map settings overrides, save folders. Never rename one after a cluster is
live or its saves stop being found.

`map_id` is what the ARK server binary expects. `official` marks maps Wildcard
ships; the rest come from mods and only work once their mod id is in the mod list.

`weight` scales the per-map RAM cap: 1.0 is a normal map, higher means this one is
consistently hungrier. These are heuristics from observed behaviour, not published
figures - a map at 1.0 that keeps getting OOM-killed on your host is a reason to raise
its own override rather than evidence the weight is wrong. Expressing it as a multiple
of your base means the whole cluster scales when you change one number.
"""

MAPS = [
    dict(key="island",     name="The Island",     map_id="TheIsland_WP",     official=True),
    dict(key="center",     name="The Center",     map_id="TheCenter_WP",     official=True),
    dict(key="scorched",   name="Scorched Earth", map_id="ScorchedEarth_WP", official=True),
    dict(key="aberration", name="Aberration",     map_id="Aberration_WP",    official=True),
    dict(key="extinction", name="Extinction",     map_id="Extinction_WP",    official=True),
    # Astraeos is a large, dense map and the one most likely to be OOM-killed at a
    # cap that every other map is comfortable at.
    dict(key="astraeos",   name="Astraeos",       map_id="Astraeos_WP",      official=True,
         weight=1.6),
    dict(key="ragnarok",   name="Ragnarok",       map_id="Ragnarok_WP",      official=True),
    dict(key="valguero",   name="Valguero",       map_id="Valguero_WP",      official=True),
    dict(key="lostcolony", name="Lost Colony",    map_id="LostColony_WP",    official=True,
         weight=1.4),
    dict(key="genesis",    name="Genesis",        map_id="Genesis_WP",       official=True),
]

BY_KEY = {m["key"]: m for m in MAPS}
KEYS = [m["key"] for m in MAPS]


def weight(key):
    return float(BY_KEY[key].get("weight", 1.0))


# ---- the list a cluster runs, as a list
#
# Order is not presentation here. The first entry is the update master - it downloads
# the thirty-odd gigabytes of server files once while the others wait - and ports are
# handed out walking the list, so moving an entry moves the port people type. That is
# why the editor has arrows rather than a set of checkboxes whose order is whatever the
# catalogue happens to be in.
#
# Written as plain string operations for the same reason the mod list's are: they are
# the part worth testing without a browser, and the page and the route then cannot
# disagree about what "move it up" means.
def listed(value):
    """The keys in this cluster's map string, in order."""
    if isinstance(value, (list, tuple)):
        return [str(k).strip() for k in value if str(k).strip()]
    return [k.strip() for k in str(value or "").split(",") if k.strip()]


def joined(keys):
    return ",".join(keys)


def add(value, key):
    """Append a map. Refuses one that is already listed.

    Appends rather than inserts because appending is the only edit that leaves every
    existing map's port where it was - which is what the help has always told people to
    do, and now the editor only offers.
    """
    keys = listed(value)
    key = str(key).strip()
    if key not in BY_KEY:
        raise ValueError("%s isn't a map Obelisk knows" % key)
    if key in keys:
        raise ValueError("%s is already in this cluster" % BY_KEY[key]["name"])
    return joined(keys + [key])


def remove(value, key):
    """Drop a map from the list. Its per-map settings are not touched.

    Removing a map from the list does not delete what that map was configured with:
    somebody taking a map out for a month and putting it back should find it as they
    left it, and a list edit is not the place to throw settings away.
    """
    keys = listed(value)
    key = str(key).strip()
    if key not in keys:
        raise ValueError("%s isn't in this cluster" % key)
    return joined([k for k in keys if k != key])


def move(value, key, delta):
    """Move one map up (-1) or down (+1). Off either end is a no-op, not an error."""
    keys = listed(value)
    key = str(key).strip()
    if key not in keys:
        raise ValueError("%s isn't in this cluster" % key)
    i = keys.index(key)
    j = i + int(delta)
    if j < 0 or j >= len(keys):
        return joined(keys)
    keys[i], keys[j] = keys[j], keys[i]
    return joined(keys)


def resolve(keys):
    """Map keys -> catalogue entries, in the order given. Unknown keys raise."""
    out = []
    for k in keys:
        if k not in BY_KEY:
            raise KeyError("unknown map %r - known maps: %s" % (k, ", ".join(KEYS)))
        out.append(BY_KEY[k])
    return out

def instance_ids(keys):
    """Stable, unique ids for a list of chosen maps - one per *instance*, not per map.

    A cluster is a list of instances, and the same map can appear more than once: an
    events island beside the normal one, running different mods. So identity has to be
    per instance. The first instance of a map keeps the plain key, later ones are
    numbered - island, island-2 - so an existing single-instance cluster keeps the names
    and folders it already has.

    Everything a running instance owns is keyed off this: its container name, its ports
    and its save folder. Keying any of those off the map type alone means two islands
    fight over one of them.
    """
    seen, out = {}, []
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
        out.append(key if seen[key] == 1 else "%s-%d" % (key, seen[key]))
    return out
