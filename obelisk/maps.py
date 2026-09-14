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

The list below is what Obelisk ships knowing. A cluster can add its own - a mod map,
or an official one released after this build - and those live in the store, so
everything here takes the store and asks it. That is why nothing in this module holds
a merged catalogue of its own: a module-level registry loaded from "the" store is one
process-wide variable pretending to belong to one cluster, and this manager already has
one of those on its backlog. The catalogue is a function of a store or it is nothing.
"""

import logging
import re
import time

log = logging.getLogger("obelisk.maps")

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

# ---- the cluster's own maps
#
# One top-level section, beside bans and cap_allows rather than inside store["maps"] -
# which is per-map settings overrides, while store.get("maps") is the ordered list this
# cluster runs. Two things called maps is already one too many; a third would be the
# name-for-two-things confusion this manager keeps removing from its own pages.
SECTION = "map_catalogue"

# A key is a container name fragment, a URL path segment and a folder name. naming's
# docker_slug lowercases and folds every run of non-alphanumerics to "-", so "my_map"
# and "my-map" would name the same container, and instance_ids appends "-2" to the
# second instance of a map. Lowercase alphanumerics only removes that whole class of
# collision rather than trying to catch its cases.
KEY_OK = re.compile(r"^[a-z][a-z0-9]{2,23}$")

# The level name the server binary is given, and the folder its world is written to:
# shared/SavedArks/<map_id>/<map_id>.ark. It reaches a path, so the charset is the
# traversal guard - no dot, no slash, no backslash, no space. Underscores are how these
# are actually spelled (TheIsland_WP).
MAP_ID_OK = re.compile(r"^[A-Za-z0-9_]{3,64}$")

# Not a map. staging.py builds its container name from the cluster project and relies
# on no map ever being called this.
RESERVED = {"staging"}

NAME_MAX = 40


def _held(store):
    """This cluster's own catalogue entries, as stored. Never the built-ins."""
    data = getattr(store, "data", None) or {}
    held = data.get(SECTION)
    return list(held) if isinstance(held, list) else []


def shape_problem(key, map_id):
    """Why this key and level name cannot be used, or "" if they can.

    The half of the rules that is about what these two strings *are*, rather than what
    else is in the catalogue. Split out because it is wanted twice: once on the way in,
    where it explains the refusal, and once on the way out, where a stored entry has to
    answer for itself too. A file can be edited, restored from a backup, or written by a
    later build, and the read path handing that straight to os.path.join and to Docker
    is the write path's rules being a suggestion.
    """
    key = str(key or "").strip()
    map_id = str(map_id or "").strip()
    if not KEY_OK.match(key):
        return ("a map key is 3 to 24 characters, lowercase letters and digits only, "
                "starting with a letter - it becomes a container name and a web address")
    if key in RESERVED:
        return "%s is a name Obelisk uses for something else" % key
    if not MAP_ID_OK.match(map_id):
        return ("a map id is 3 to 64 characters, letters, digits and underscores only - "
                "it is the folder the world is saved in, so it cannot contain a dot, a "
                "slash or a space")
    return ""


def catalogue(store):
    """key -> entry: what Obelisk ships, plus what this cluster added.

    Built-ins win, always. add_entry refuses a key that collides with one, and this
    skips it as well - a store edited by hand, or restored from a backup taken against
    a different build, must not be able to redefine what TheIsland means.

    An entry the write path would refuse today is dropped here too, and logged. Stored
    text does not get to be trusted more than typed text: the key becomes a container
    name and a URL, the level name becomes a folder, and this function is what hands
    both to the rest of the manager. A dropped entry leaves its key unknown, which the
    Cluster page already names in amber and the plan already reports - the same answer
    as a map that was never defined, which is what this one now effectively is.

    Names are not re-checked. A duplicate display name is confusing rather than unsafe,
    and dropping a map's definition over one would take a running map out of the
    catalogue to fix a label.
    """
    out = dict(BY_KEY)
    for e in _held(store):
        if not isinstance(e, dict):
            continue
        key = str(e.get("key") or "").strip()
        if not key:
            continue
        if key in BY_KEY:
            log.warning("ignoring a stored map called %r: that is one of Obelisk's own "
                        "maps, and a stored entry does not get to redefine it", key)
            continue
        if key in out:
            log.warning("ignoring a second stored map called %r: the first one in the "
                        "file is the one this cluster means", key)
            continue
        why = shape_problem(key, e.get("map_id"))
        if why:
            log.warning("ignoring the stored map %r: %s", key, why)
            continue
        out[key] = dict(e, key=key, official=False, custom=True)
    return out


def keys_of(store):
    """Every key this cluster could run, built-ins first, then its own."""
    cat = catalogue(store)
    return KEYS + [k for k in cat if k not in BY_KEY]


def ordered(store):
    """Every entry, for a page that lists them: Obelisk's own first, then this
    cluster's, each in the order they were written down."""
    cat = catalogue(store)
    return [cat[k] for k in keys_of(store)]


def entry(store, key):
    """One catalogue entry, or None. Total - callers that need it to exist say so."""
    return catalogue(store).get(str(key).strip())


def known(store, key):
    return str(key).strip() in catalogue(store)


def unknown(store, keys):
    """The keys in this list the catalogue cannot explain, in order, once each."""
    cat = catalogue(store)
    out = []
    for k in keys:
        k = str(k).strip()
        if k and k not in cat and k not in out:
            out.append(k)
    return out


def weight(store, key):
    """The RAM multiplier for one map. A map that says nothing is a normal map."""
    return float((entry(store, key) or {}).get("weight") or 1.0)


# ---- adding a map Obelisk does not ship
#
# Everything here refuses before it writes. The rules are not taste: a key reaches a
# container name and a URL, a map_id reaches a path and the server's command line, and
# a name reaches the in-game browser. What cannot be checked is whether the map_id is
# the *right* one - a well-formed wrong id means the server quietly creates a new world
# under that name - so that is said plainly where it is asked for rather than pretended
# about here.
def check_entry(store, key, name, map_id, existing=None):
    """Raise ValueError unless these three fields can be added. Returns the key."""
    key = str(key or "").strip().lower()
    name = str(name or "").strip()
    map_id = str(map_id or "").strip()
    cat = catalogue(store)
    if existing:
        cat = {k: v for k, v in cat.items() if k != existing}

    # The rules about the two strings themselves, shared with the read path so a stored
    # entry answers to exactly what a typed one does.
    why = shape_problem(key, map_id)
    if why:
        raise ValueError(why)
    if key in BY_KEY:
        raise ValueError("%s is one of Obelisk's own maps - it is already in the list "
                         "to add" % key)
    if key in cat:
        raise ValueError("this cluster already has a map called %s" % key)

    if not name:
        raise ValueError("give the map a name - it is what players see in the browser")
    if len(name) > NAME_MAX:
        raise ValueError("that name is %d characters; keep it to %d so the whole "
                         "server name still fits the in-game browser"
                         % (len(name), NAME_MAX))
    if any(ord(c) < 0x20 or ord(c) > 0x7e for c in name):
        raise ValueError("that name has a character the server browser cannot carry - "
                         "plain letters, digits and punctuation only")
    for other in cat.values():
        if str(other.get("name", "")).strip().lower() == name.lower():
            raise ValueError("%s is already the name of a map in this cluster - two "
                             "rows with one name is how the wrong one gets picked"
                             % other["name"])
    return key


def add_entry(store, key, name, map_id, mod_id="", weight=1.0, when=None):
    """Add a map of this cluster's own. Returns the stored entry.

    Nothing else in the store is touched: adding a map Obelisk can run is not the same
    act as choosing to run it, and the maps list is edited separately.
    """
    key = check_entry(store, key, name, map_id)
    e = {"key": key, "name": str(name).strip(), "map_id": str(map_id).strip(),
         "official": False, "custom": True, "weight": float(weight or 1.0),
         "added": int(when if when is not None else time.time())}
    mod_id = str(mod_id or "").strip()
    if mod_id:
        # A hint, not a dependency: this module does not check the mod list, and a map
        # whose mod is missing fails at the server, not here.
        e["mod_id"] = mod_id
    store.data.setdefault(SECTION, []).append(e)
    store.save()
    return e


def update_entry(store, key, name=None, map_id=None, mod_id=None, weight=None,
                 saves_exist=None):
    """Change one of this cluster's own maps. Returns the stored entry.

    `saves_exist` is a callable taking a map_id and saying whether a world is on disk
    under it. It is required to change a map_id and only then: changing it points the
    whole manager - saves, restore points, archives, the integrity gate - at a folder
    the game has never written to, and the world that exists stays where it is under a
    name nothing looks for any more. The check is the caller's because this module does
    not touch the filesystem; refusing to guess is the point.
    """
    key = str(key).strip()
    held = _held(store)
    at = next((i for i, e in enumerate(held)
               if isinstance(e, dict) and str(e.get("key") or "").strip() == key), None)
    if at is None:
        raise ValueError("%s isn't a map this cluster added" % key)
    cur = dict(held[at])

    want_id = cur["map_id"] if map_id is None else str(map_id).strip()
    if want_id != cur["map_id"]:
        if saves_exist is None:
            raise ValueError("a map id can only be changed when Obelisk can see "
                             "whether that map already has saves")
        if saves_exist(cur["map_id"]):
            raise ValueError(
                "%s already has saves under %s - changing the map id now would point "
                "Obelisk at a folder the game has never written to, and leave the "
                "world it does have under a name nothing looks for. Nothing has been "
                "changed." % (cur.get("name") or key, cur["map_id"]))

    want_name = cur["name"] if name is None else str(name).strip()
    check_entry(store, key, want_name, want_id, existing=key)
    cur["name"], cur["map_id"] = want_name, want_id
    if weight is not None:
        cur["weight"] = float(weight or 1.0)
    if mod_id is not None:
        mod_id = str(mod_id).strip()
        if mod_id:
            cur["mod_id"] = mod_id
        else:
            cur.pop("mod_id", None)
    store.data.setdefault(SECTION, [])[at] = cur
    store.save()
    return cur


def remove_entry(store, key):
    """Forget one of this cluster's own maps. Refuses while the cluster lists it.

    Nothing on disk is touched: the world this map wrote stays where it is, and so do
    its per-map settings. This is the catalogue forgetting how to spell a map, not a
    delete.
    """
    key = str(key).strip()
    if key in BY_KEY:
        raise ValueError("%s is one of Obelisk's own maps - it cannot be removed from "
                         "the catalogue" % BY_KEY[key]["name"])
    held = _held(store)
    at = next((i for i, e in enumerate(held)
               if isinstance(e, dict) and str(e.get("key") or "").strip() == key), None)
    if at is None:
        raise ValueError("%s isn't a map this cluster added" % key)
    if key in listed(store.get("maps")):
        raise ValueError("%s is one of the maps this cluster runs - take it out of the "
                         "list first. Nothing has been changed."
                         % (held[at].get("name") or key))
    gone = held.pop(at)
    store.data[SECTION] = held
    store.save()
    return gone


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


def add(store, value, key):
    """Append a map. Refuses one that is already listed.

    Appends rather than inserts because appending is the only edit that leaves every
    existing map's port where it was - which is what the help has always told people to
    do, and now the editor only offers.
    """
    keys = listed(value)
    key = str(key).strip()
    cat = catalogue(store)
    if key not in cat:
        raise ValueError("%s isn't a map Obelisk knows" % key)
    if key in keys:
        raise ValueError("%s is already in this cluster" % cat[key]["name"])
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


def resolve(store, keys):
    """Map keys -> catalogue entries, in the order given. Unknown keys raise."""
    cat = catalogue(store)
    out = []
    for k in keys:
        if k not in cat:
            raise KeyError("unknown map %r - known maps: %s"
                           % (k, ", ".join(keys_of(store))))
        out.append(cat[k])
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
