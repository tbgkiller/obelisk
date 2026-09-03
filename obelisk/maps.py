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


def resolve(keys):
    """Map keys -> catalogue entries, in the order given. Unknown keys raise."""
    out = []
    for k in keys:
        if k not in BY_KEY:
            raise KeyError("unknown map %r - known maps: %s" % (k, ", ".join(KEYS)))
        out.append(BY_KEY[k])
    return out
