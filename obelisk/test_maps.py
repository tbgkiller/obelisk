"""The catalogue, and a map this cluster added behaving like one Obelisk ships.

Obelisk shipped a closed list of ten maps, and four subsystems were written as though
that list were the world: ports and the update master come out of it, saves are found
under the level name in it, the restore guard types the name in it, and the settings
gate refuses anything that is not in it. Opening it is a data-model change, so this
file proves the opened one end to end without a browser: a map the operator added gets
ports and an instance, can be the update master, saves and restores under its own level
name, is in the archive and the integrity gate, and passes validation - and a stored
entry still cannot redefine one of Obelisk's own.

Run: python -m obelisk.test_maps
"""

import os
import sys
import tempfile

from . import backup, cluster as clusterctl, layout, restore, savepoints
from . import maps as mapcat
from .plan import build_plan
from .settings import Invalid, Store, validate

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def refuses(_what, _fn, *args, **kwargs):
    """The rule said no, and said why. A ValueError with no sentence is not a rule.

    The two leading underscores are not style: the things being refused take `name` and
    `weight` as keyword arguments, and this has to pass them through rather than eat
    them.
    """
    try:
        _fn(*args, **kwargs)
    except ValueError as e:
        check(_what, len(str(e)) > 20, "the refusal says nothing: %r" % str(e))
        return str(e)
    check(_what, False, "it was allowed")
    return ""


def answers(_what, _fn, *args, **kwargs):
    """Call something that should work, and report it when it does not.

    A KeyError out of a path builder names no check, and takes the rest of this module
    with it - which is how a suite reports a crash where it meant to report a failure.
    The whole point here is that these calls stop raising for a map this cluster added,
    so the failure has to be the thing that gets printed.
    """
    try:
        return _fn(*args, **kwargs)
    except Exception as e:                       # noqa: BLE001 - that is the failure
        check(_what, False, "%s: %s" % (type(e).__name__, e))
        return None


def fresh(maps="island"):
    base = tempfile.mkdtemp()
    root = os.path.join(base, "obelisk")
    ark = os.path.join(base, "ark")
    os.makedirs(root, exist_ok=True)
    os.environ["OBELISK_ARK"] = ark
    layout.ensure_ark(ark, ["island"])
    st = Store(os.path.join(root, "settings.json")).load()
    st.patch({"status_port": 8088}, source="install")
    st.patch({"maps": maps, "admin_password": "synthetic-pw",
              "cluster_id": "maptest"})
    st.data["cluster"]["appdata"] = ark
    st.save()
    return st, ark


# ---------------------------------------------------------------- the catalogue
st, ark = fresh()

check("a fresh cluster knows the maps Obelisk ships",
      set(mapcat.catalogue(st)) == set(mapcat.BY_KEY), sorted(mapcat.catalogue(st)))
check("and has none of its own", mapcat.SECTION not in st.data, st.data.get(mapcat.SECTION))

entry = mapcat.add_entry(st, "svart", "Svartalfheim", "Svartalfheim_WP",
                         mod_id="893657", when=1700000000)
check("a map this cluster added is stored with every field filled in",
      entry == {"key": "svart", "name": "Svartalfheim", "map_id": "Svartalfheim_WP",
                "official": False, "custom": True, "weight": 1.0,
                "added": 1700000000, "mod_id": "893657"}, entry)
check("in its own section, beside the bans and the cap allows",
      st.data[mapcat.SECTION] == [entry], st.data.get(mapcat.SECTION))
# store["maps"] is per-map settings overrides and store.get("maps") is the ordered list
# this cluster runs. A third thing called maps is how the wrong one gets edited.
check("not inside the per-map settings, which is a different thing called maps",
      st.data["maps"] == {}, st.data["maps"])
check("and not in the maps list either - adding a map is not choosing to run it",
      st.get("maps") == "island", st.get("maps"))

# fresh() points OBELISK_ARK at its own folder - save and restore around it here too
# (see the same guard further down), or the world-dir checks below would go looking
# for "svart"'s saved world in this fixture's folder instead of the main one's.
_ark_was0 = os.environ.get("OBELISK_ARK")
_emptymod_st, _ = fresh()
_emptymod = mapcat.add_entry(_emptymod_st, "svart", "Svartalfheim", "Svartalfheim_WP")
check("no mod id given stores no mod_id key at all - Phase 2 behaviour, unchanged",
      "mod_id" not in _emptymod, _emptymod)
if _ark_was0 is None:
    os.environ.pop("OBELISK_ARK", None)
else:
    os.environ["OBELISK_ARK"] = _ark_was0

check("the catalogue is built-ins plus this cluster's own",
      set(mapcat.catalogue(st)) == set(mapcat.BY_KEY) | {"svart"},
      sorted(mapcat.catalogue(st)))
check("in an order a page can list: Obelisk's first, then this cluster's",
      [m["key"] for m in mapcat.ordered(st)] == mapcat.KEYS + ["svart"],
      [m["key"] for m in mapcat.ordered(st)])
check("an added map is marked as this cluster's, not as official",
      mapcat.entry(st, "svart")["custom"] is True
      and mapcat.entry(st, "svart")["official"] is False, mapcat.entry(st, "svart"))
check("resolve places it in the order the list gives",
      [m["key"] for m in mapcat.resolve(st, ["svart", "island"])] == ["svart", "island"],
      mapcat.resolve(st, ["svart", "island"]))
check("a map that says nothing about weight is a normal map",
      mapcat.weight(st, "svart") == 1.0, mapcat.weight(st, "svart"))
check("and the built-in weights still hold",
      mapcat.weight(st, "astraeos") == 1.6, mapcat.weight(st, "astraeos"))
check("entry is total - it answers None rather than raising",
      mapcat.entry(st, "nosuch") is None, mapcat.entry(st, "nosuch"))
check("resolve is not - a caller that needs the entry is told",
      not mapcat.known(st, "nosuch"), "nosuch resolved")

# ---- a stored entry cannot redefine one of Obelisk's own
#
# add_entry refuses the collision, so reaching this needs a hand-edited file or a store
# restored from a backup taken against a different build. Either way the answer is the
# same: "island" means The Island, and a line of stored text does not get a vote.
st.data[mapcat.SECTION].append({"key": "island", "name": "Fake Island",
                                "map_id": "Evil_WP"})
check("a stored entry never shadows a built-in",
      mapcat.catalogue(st)["island"]["name"] == "The Island",
      mapcat.catalogue(st)["island"])
check("nor its level name, which is where the world actually is",
      mapcat.catalogue(st)["island"]["map_id"] == "TheIsland_WP",
      mapcat.catalogue(st)["island"])
check("and the shadowed entry is not offered as a second island",
      [m["key"] for m in mapcat.ordered(st)].count("island") == 1,
      [m["key"] for m in mapcat.ordered(st)])
st.data[mapcat.SECTION] = [e for e in st.data[mapcat.SECTION]
                           if e["key"] != "island"]

# ---- a stored entry answers to the same rules a typed one does
#
# check_entry is the way in, and nothing reaches it but the form. A settings.json can be
# edited, restored from a backup, or written by a later build - and catalogue() is what
# hands a key to Docker and a level name to os.path.join. A stored entry that the write
# path would refuse is dropped here and logged, so the file cannot be the way around the
# rules. A dropped key is simply unknown, which the plan reports and the Cluster page
# already names in amber.
# fresh() points OBELISK_ARK at its own folder, which is how layout finds the Ark
# directory - so this block puts it back, or the path checks further down would be
# looking in this fixture's folder instead of their own.
_ark_was = os.environ.get("OBELISK_ARK")
_bad_store, _ = fresh()
_bad_store.data[mapcat.SECTION] = [
    {"key": "escape", "name": "Escape", "map_id": "../../etc/passwd"},
    {"key": "my_map", "name": "Underscore", "map_id": "Fine_WP"},
    {"key": "Caps", "name": "Capitals", "map_id": "Fine_WP"},
    {"key": "staging", "name": "Staging", "map_id": "Fine_WP"},
    {"key": "dupe", "name": "First", "map_id": "First_WP"},
    {"key": "dupe", "name": "Second", "map_id": "Second_WP"},
    {"key": "fine", "name": "Fine", "map_id": "Fine_WP"},
]
_badcat = mapcat.catalogue(_bad_store)
check("a stored map id that could leave its folder is dropped",
      "escape" not in _badcat, sorted(_badcat))
check("a stored key that two spellings would share a container name with is dropped",
      "my_map" not in _badcat and "Caps" not in _badcat, sorted(_badcat))
check("a stored key Obelisk uses for something else is dropped",
      "staging" not in _badcat, sorted(_badcat))
check("a key stored twice keeps the first, rather than the last one to be edited in",
      _badcat.get("dupe", {}).get("name") == "First", _badcat.get("dupe"))
check("and a well-formed one beside them is kept",
      _badcat.get("fine", {}).get("map_id") == "Fine_WP", _badcat.get("fine"))
check("dropping one leaves its key unknown, which is a state this manager reports",
      mapcat.unknown(_bad_store, ["escape", "fine"]) == ["escape"],
      mapcat.unknown(_bad_store, ["escape", "fine"]))
# Names are not re-checked on the way out. A duplicate name is confusing; a duplicate
# container name or a path with a "../" in it is not. Dropping a map's whole definition
# to fix a label would take a running map out of the catalogue.
_bad_store.data[mapcat.SECTION] = [
    {"key": "twin", "name": "The Island", "map_id": "Twin_WP"}]
check("a stored name that clashes with another map is kept, not dropped",
      "twin" in mapcat.catalogue(_bad_store),
      sorted(mapcat.catalogue(_bad_store)))
check("and the map it clashes with is untouched",
      mapcat.catalogue(_bad_store)["island"]["name"] == "The Island",
      mapcat.catalogue(_bad_store)["island"])
if _ark_was is None:
    os.environ.pop("OBELISK_ARK", None)
else:
    os.environ["OBELISK_ARK"] = _ark_was

# ---------------------------------------------------------------- the rules
#
# None of these are taste. A key becomes a container name and a web address, a map id
# becomes the folder a world is written to, and a name is carried to the in-game
# browser - so each rule is refused for what it would break, and says so.
# Said as what it is. Without its own check the next rule refuses it anyway - the
# catalogue holds the built-ins too - with "this cluster already has a map called
# island", which is not what happened and not what to do about it.
_collide = refuses("a key already used by one of Obelisk's maps is refused",
                   mapcat.check_entry, st, "island", "Mine", "Mine_WP")
check("saying it is one of Obelisk's own, not one this cluster added",
      "Obelisk" in _collide, _collide)
refuses("so is one this cluster already has",
        mapcat.check_entry, st, "svart", "Another", "Another_WP")
refuses("and the one name staging.py relies on no map having",
        mapcat.check_entry, st, "staging", "Staging", "Staging_WP")
# docker_slug folds every run of non-alphanumerics to "-" and lowercases, so my_map and
# my-map would name the same container; instance_ids appends "-2" to a second instance
# of a map. Lowercase alphanumerics removes the whole class rather than chasing cases.
for bad, why in (("Sv", "too short"), ("2nd", "starting with a digit"),
                 ("my_map", "an underscore"), ("my-map", "a hyphen"),
                 ("Svart", "a capital"), ("s" * 25, "too long")):
    refuses("a key with %s is refused" % why,
            mapcat.check_entry, st, bad, "Fine Name", "Fine_WP")
check("and a plain one is not",
      mapcat.check_entry(st, "svart2", "Fine Name", "Fine_WP") == "svart2")

# The charset is the traversal guard: this reaches os.path.join.
for bad, why in (("../../etc/passwd", "a traversal"), ("My Map", "a space"),
                 ("Map.ark", "a dot"), ("a/b", "a slash"), ("My-Map", "a hyphen"),
                 ("XY", "too short")):
    refuses("a map id with %s is refused" % why,
            mapcat.check_entry, st, "goodkey", "Fine Name", bad)
check("_WP is not required, because mod maps exist that do not use it",
      mapcat.check_entry(st, "goodkey", "Fine Name", "Svartalfheim") == "goodkey")

refuses("a map with no name is refused", mapcat.check_entry, st, "goodkey", "", "Fine_WP")
refuses("so is one too long for the server browser",
        mapcat.check_entry, st, "goodkey", "N" * 41, "Fine_WP")
refuses("and one the browser cannot carry",
        mapcat.check_entry, st, "goodkey", "Svårt’s Map", "Fine_WP")
refuses("a name another map already has is refused, whatever the case",
        mapcat.check_entry, st, "goodkey", "svartALFHEIM", "Fine_WP")
refuses("including one of Obelisk's own names",
        mapcat.check_entry, st, "goodkey", "the island", "Fine_WP")
check("nothing was written by any of those refusals",
      [e["key"] for e in st.data[mapcat.SECTION]] == ["svart"],
      st.data[mapcat.SECTION])

# ---- mod_id: optional, but a non-empty one is held to the mod list's own shape
#
# An unvalidated id could never match a list entry, so letting a typo through here
# would make mod_state confidently wrong about every map that carries one - refusing it
# at the same door as the other three fields is what keeps that cross-check honest.
for bad, why in (("not-a-number", "letters"), ("12", "too short"),
                 ("123456789", "too long")):
    _badmod = refuses("a mod id that is %s is refused" % why,
                      mapcat.check_entry, st, "goodkey", "Fine Name", "Fine_WP",
                      mod_id=bad)
    check("naming the rule, in the voice mods.add already uses",
          "a mod id is a number" in _badmod, _badmod)
check("an empty mod id is not a refusal - the hint is optional",
      mapcat.check_entry(st, "goodkey", "Fine Name", "Fine_WP", mod_id="") == "goodkey")
check("and neither is a well-formed one",
      mapcat.check_entry(st, "goodkey", "Fine Name", "Fine_WP", mod_id="893657")
      == "goodkey")
check("none of those touched the store either",
      [e["key"] for e in st.data[mapcat.SECTION]] == ["svart"],
      st.data[mapcat.SECTION])

# ---------------------------------------------------------------- validation
check("the settings gate accepts a map this cluster added",
      answers("the gate accepts it", st.patch, {"maps": "island,svart"}) == {"recreate"},
      st.get("maps"))
check("and the list is what was asked for", st.get("maps") == "island,svart",
      st.get("maps"))
try:
    validate("maps", "island,svart")
    check("validate told nothing about a cluster still knows only the built-ins", False)
except Invalid as e:
    check("validate told nothing about a cluster still knows only the built-ins",
          "svart" in str(e), str(e))
check("which is why the store tells it",
      "svart" in st.map_keys() and set(mapcat.KEYS) <= set(st.map_keys()),
      st.map_keys())
try:
    st.patch({"maps": "island,ghost"})
    check("a name no catalogue explains is still refused", False, st.get("maps"))
except Invalid as e:
    check("a name no catalogue explains is still refused", "ghost" in str(e), str(e))
check("and refusing it changed nothing", st.get("maps") == "island,svart",
      st.get("maps"))

# ---------------------------------------------------------------- the plan
plan = answers("the plan is built for a cluster with a map of its own",
               build_plan, st, in_use_ports=set(), host_ram_gb=64) or {"maps": [],
                                                                       "problems": []}
rows = {r["map"]: r for r in plan["maps"]}
check("a map this cluster added gets a row in the plan", "svart" in rows, list(rows))
check("with its own ports, counting on from the map before it",
      (rows["svart"]["game_port"], rows["svart"]["rcon_port"])
      == (rows["island"]["game_port"] + 1, rows["island"]["rcon_port"] + 1), rows)
check("an instance id, which is what its container and folder are named from",
      rows["svart"]["instance"] == "svart", rows["svart"])
check("its own level name, which is where its world goes",
      rows["svart"]["map_id"] == "Svartalfheim_WP", rows["svart"])
check("and the plan is one this cluster could start", plan["ok"], plan["problems"])
check("its container name is this cluster's, not any cluster's",
      "asa-maptest-svart" in clusterctl.target_names(st),
      clusterctl.target_names(st))
check("and RCON is addressed to it by name and port",
      ("Svartalfheim", "asa-maptest-svart", rows["svart"]["rcon_port"])
      in clusterctl.rcon_targets(st), clusterctl.rcon_targets(st))

st.patch({"maps": "svart,island"})
first = build_plan(st, in_use_ports=set(), host_ram_gb=64)["maps"][0]
check("a map this cluster added can be the update master",
      first["map"] == "svart" and first["role"] == "update master", first)
check("which is the position and nothing else - the files it downloads are the same",
      build_plan(st, in_use_ports=set(), host_ram_gb=64)["maps"][1]["role"]
      == "follower", build_plan(st, in_use_ports=set())["maps"][1])
st.patch({"maps": "island,svart"})

st.data["maps"]["svart"] = {"mem_limit": "12g"}
check("a per-map override still wins for it, the same as for a built-in",
      {r["map"]: r["memory"] for r in
       build_plan(st, in_use_ports=set(), host_ram_gb=64)["maps"]}["svart"] == "12g",
      build_plan(st, in_use_ports=set())["maps"])
del st.data["maps"]["svart"]

# ---------------------------------------------------------------- saves and restores
_wd = answers("its saves are looked for without raising",
              savepoints.world_dir, st, "svart")
check("under its own level name", str(_wd).endswith("Svartalfheim_WP"), _wd)
_lw = answers("its live world is worked out without raising",
              savepoints.live_world, st, "svart")
check("as the file inside that folder",
      os.path.basename(str(_lw)) == "Svartalfheim_WP.ark", _lw)
check("listing its points reads that folder rather than raising",
      answers("list_points reads it", savepoints.list_points, st, "svart") == [],
      savepoints.world_dir(st, "svart"))

# The typed-name guard on the one irreversible action in this manager. Read from the
# built-in list alone it returned "" for a map this cluster added, and typed_matches("")
# is False for every string - so the page told the operator to type a name, and the
# name it told them to type could not work.
check("the restore guard compares against this map's real name",
      restore.confirms(st, "svart", "Svartalfheim"))
check("however it was typed", restore.confirms(st, "svart", "  svartalfheim "))
check("and still refuses another map's name",
      not restore.confirms(st, "svart", "The Island"))
check("and an empty one, which is all it used to accept for these maps",
      not restore.confirms(st, "svart", ""))

os.makedirs(savepoints.world_dir(st, "svart"), exist_ok=True)
ok_b, msg_b, arch = backup.create(st)
check("a backup includes the worlds of the maps this cluster added", ok_b, msg_b)
info = restore.inspect(arch)
check("the archive holds its level name",
      "Svartalfheim_WP" in (info.get("maps") or []), info.get("maps"))
_pf = answers("preflight runs for it", restore.preflight, st, arch, "svart")
check("and accepts it as a map to restore", _pf and _pf[0], _pf)
check("and the archive comparison counts it as one of the maps you run",
      restore.compare(st, info) == [], restore.compare(st, info))

# ---------------------------------------------------------------- lifecycle
refuses("a map the cluster runs cannot be forgotten from the catalogue",
        mapcat.remove_entry, st, "svart")
check("and it is still there", mapcat.known(st, "svart"), st.data[mapcat.SECTION])
refuses("nor can one of Obelisk's own, which is not the cluster's to forget",
        mapcat.remove_entry, st, "island")

st.patch({"maps": "island"})
check("taking it out of the list leaves the catalogue entry, so re-adding is a click",
      mapcat.known(st, "svart"), st.data[mapcat.SECTION])
st.data["maps"]["svart"] = {"mem_limit": "12g"}
gone = mapcat.remove_entry(st, "svart")
check("forgetting it then works", gone["key"] == "svart" and
      not mapcat.known(st, "svart"), st.data[mapcat.SECTION])
check("and does not touch what that map was configured with, or its world",
      st.data["maps"].get("svart") == {"mem_limit": "12g"}
      and os.path.isdir(os.path.join(ark, "shared", "SavedArks", "Svartalfheim_WP")),
      st.data["maps"])
refuses("forgetting a map twice says so rather than pretending",
        mapcat.remove_entry, st, "svart")

mapcat.add_entry(st, "svart", "Svartalfheim", "Svartalfheim_WP")
check("renaming it is allowed - a display name breaks nothing",
      mapcat.update_entry(st, "svart", name="Svartalfheim II")["name"]
      == "Svartalfheim II", mapcat.entry(st, "svart"))
refuses("changing the level name needs Obelisk to know whether there are saves",
        mapcat.update_entry, st, "svart", map_id="Other_WP")
refuses("and is refused when there are",
        mapcat.update_entry, st, "svart", map_id="Other_WP",
        saves_exist=lambda m: True)
check("the level name did not move",
      mapcat.entry(st, "svart")["map_id"] == "Svartalfheim_WP",
      mapcat.entry(st, "svart"))
check("and it can be changed when there is no world to leave behind",
      mapcat.update_entry(st, "svart", map_id="Other_WP",
                          saves_exist=lambda m: False)["map_id"] == "Other_WP",
      mapcat.entry(st, "svart"))
refuses("a rename onto another map's name is refused like any other",
        mapcat.update_entry, st, "svart", name="The Island")

# ---------------------------------------------------------------- a missing entry
#
# A store edited by hand, or restored from a backup taken while this cluster had a map
# of its own that has since been forgotten. Every page builds a plan, so raising here
# took the whole manager down over one line of stored text.
st.data["cluster"]["maps"] = "island,ghost,svart"
check("the catalogue can say which names it cannot explain",
      mapcat.unknown(st, mapcat.listed(st.get("maps"))) == ["ghost"],
      mapcat.unknown(st, mapcat.listed(st.get("maps"))))
p = answers("the plan is built at all for a list naming something unknown",
            build_plan, st, in_use_ports=set(), host_ram_gb=64) or {
                "ok": True, "problems": [], "maps": []}
check("and reports it as a problem rather than raising", not p["ok"], p["problems"])
check("naming the map it cannot place",
      any("ghost" in x for x in p["problems"]), p["problems"])
_by = {r["map"]: r for r in p["maps"]}
check("the unknown name keeps its place in the list",
      [r["map"] for r in p["maps"]] == ["island", "ghost", "svart"],
      [r["map"] for r in p["maps"]])
# The tempting fix is to drop the row. That moves every port after it - which is the
# one thing this manager refuses to do to a running cluster on purpose.
check("so every map after it keeps the port it already has",
      _by["svart"]["game_port"] == _by["island"]["game_port"] + 2, _by)
check("and the row says it is not usable",
      _by["ghost"].get("unknown") is True and not _by["island"].get("unknown"), _by)

# ---- what each of these writes, measured rather than assumed
#
# Two of the three things in this store are called some form of "maps", so an edit that
# reaches the wrong one is the failure worth measuring for: adding to the catalogue must
# not choose to run anything, and taking a map out of the list must not throw away what
# the operator set up on it.
import copy as _copy

_dst, _dark = fresh()
_before = _copy.deepcopy(_dst.data)
mapcat.add_entry(_dst, "vann", "Vannaland", "Vannaland_WP")
_moved = [k for k in set(_before) | set(_dst.data)
          if _before.get(k) != _dst.data.get(k)]
check("adding a map to the catalogue changes the catalogue and nothing else",
      _moved == [mapcat.SECTION], _moved)

_dst.patch({"maps": "island,vann"})
_dst.data["maps"]["vann"] = {"mem_limit": "9g"}
_dst.save()
_before2 = _copy.deepcopy(_dst.data)
_dst.patch({"maps": "island"})
_moved2 = [k for k in set(_before2) | set(_dst.data)
           if _before2.get(k) != _dst.data.get(k)]
check("taking a map out of the list changes the list and nothing else",
      _moved2 == ["cluster"], _moved2)
check("the catalogue entry stays, so putting it back is one click",
      mapcat.known(_dst, "vann"), _dst.data[mapcat.SECTION])
check("and so does everything that map was configured with",
      _dst.data["maps"].get("vann") == {"mem_limit": "9g"}, _dst.data["maps"])
check("which is the same promise the maps editor already made about built-ins",
      _dst.get("maps") == "island", _dst.get("maps"))

# ---- mod_id: an advisory cross-check against this map's own effective mod list
#
# The claim mod_state makes is "nothing in this cluster loads that mod", checked
# against mod_ids AND passive_mods, both read per-map with store.get(..., map_name=)
# rather than cluster-wide - both are per-map overridable (schema.PER_MAP_KEYS), and a
# map's own override is the one that is actually true for it.
_mst, _mark = fresh()
_before3 = _copy.deepcopy(_mst.data)
mapcat.add_entry(_mst, "svart", "Svartalfheim", "Svartalfheim_WP", mod_id="893657")
_moved3 = [k for k in set(_before3) | set(_mst.data)
          if _before3.get(k) != _mst.data.get(k)]
check("defining a map with a mod id still only changes the catalogue",
      _moved3 == [mapcat.SECTION], _moved3)

check("no mod_id given is a state mod_state can't judge",
      mapcat.mod_state(_mst, "island") is None, mapcat.mod_state(_mst, "island"))
check("cluster-wide mod_ids not carrying it reads as not-in-the-list",
      mapcat.mod_state(_mst, "svart") is False, mapcat.mod_state(_mst, "svart"))
_mst.patch({"mod_ids": "893657"})
check("carrying it in the cluster-wide list reads as in-the-list",
      mapcat.mod_state(_mst, "svart") is True, mapcat.mod_state(_mst, "svart"))
_mst.patch({"mod_ids": ""})
check("taking it back out reverts to not-in-the-list",
      mapcat.mod_state(_mst, "svart") is False, mapcat.mod_state(_mst, "svart"))
_mst.patch({"mod_ids": "111111"}, map_name="svart")
check("a per-map override carrying it wins, even though the cluster-wide list doesn't",
      mapcat.mod_state(_mst, "svart") is False, mapcat.mod_state(_mst, "svart"))
_mst.patch({"mod_ids": "893657"}, map_name="svart")
check("this map's own override carrying it reads as in-the-list too",
      mapcat.mod_state(_mst, "svart") is True, mapcat.mod_state(_mst, "svart"))
_mst.patch({"mod_ids": ""}, map_name="svart")
_mst.patch({"passive_mods": "893657"}, map_name="svart")
check("a mod only ever loaded passively still counts as known",
      mapcat.mod_state(_mst, "svart") is True, mapcat.mod_state(_mst, "svart"))
_mst.patch({"passive_mods": ""}, map_name="svart")

_mst.data[mapcat.SECTION].append({"key": "broken", "name": "Broken",
                                  "map_id": "Broken_WP", "mod_id": "not-a-number"})
check("a malformed stored mod id is not dropped from the catalogue",
      mapcat.known(_mst, "broken"), sorted(mapcat.catalogue(_mst)))
check("but there is nothing truthful mod_state can say about it",
      mapcat.mod_state(_mst, "broken") is None, mapcat.mod_state(_mst, "broken"))

# Advisory only: a map whose mod is missing from every list still runs, still plans,
# still offers itself to be added. Nothing here is a gate.
_mst.patch({"maps": "island,svart"})
check("svart's mod is confirmed missing from every list right now",
      mapcat.mod_state(_mst, "svart") is False, mapcat.mod_state(_mst, "svart"))
_plan9 = build_plan(_mst, in_use_ports=set(), host_ram_gb=64)
check("and the plan is still ok - a missing mod is advice, never a refusal",
      _plan9["ok"], _plan9["problems"])
check("svart is still an offered map, not dropped for lacking its mod",
      "svart" in mapcat.catalogue(_mst), sorted(mapcat.catalogue(_mst)))

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
