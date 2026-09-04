"""Offline tests for the Obelisk settings engine.  python3 -m obelisk.test_settings"""
# Fixture values here are deliberately synthetic - no real cluster's ids, times or
# timezone belong in a public repo. The one exception is mod id 929110: the product
# checks that specific public id to enforce stacking-mod load order, so a test of
# that rule has to use the real one.
import json, os, sys, tempfile

from .settings import Store, Invalid, validate, generate_env, generate_ini
from .schema import SETTINGS, BY_KEY, GROUPS, markdown

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)) if detail and not cond else ""))
    if not cond: fails.append(name)

def rejects(name, key, value):
    try:
        validate(key, value)
        check(name, False, "accepted %r" % (value,))
    except Invalid as e:
        check(name, True, str(e))


def _try_default(key):
    """True if this setting's shipped default fails its own validator."""
    try:
        validate(key, BY_KEY[key].get("default", ""))
        return False
    except Invalid:
        return True

def new_store():
    return Store(os.path.join(tempfile.mkdtemp(), "settings.json"))

# ---- schema integrity
check("no duplicate keys", len(BY_KEY) == len(SETTINGS))
check("every group is declared", all(s["group"] in GROUPS for s in SETTINGS),
      [s["key"] for s in SETTINGS if s["group"] not in GROUPS])
check("every setting has help text", all(s.get("help") for s in SETTINGS))
check("markdown docs generate", "## Identity" in markdown())

# ---- the hard-won rules, encoded
rejects("rejects a leading # in the name prefix", "session_prefix", "#ACME")
rejects("rejects non-ASCII in the name prefix", "session_prefix", "ACME • Cluster")
check("accepts pipes and spaces", validate("session_tags", "PvE 10x | NoWipe") == "PvE 10x | NoWipe")
rejects("rejects a stacking mod that isn't first", "mod_ids", "222222,929110,444444")
check("accepts a stacking mod first", validate("mod_ids", "929110,222222") == "929110,222222")
check("mod list tolerates spacing", validate("mod_ids", " 929110 , 222222 ") == "929110,222222")
rejects("rejects a non-numeric mod id", "mod_ids", "929110,not-a-mod")

# ---- types
rejects("rejects a bad memory value", "mem_limit", "20 gigs")
check("accepts 32g", validate("mem_limit", "32G") == "32g")
rejects("rejects a bad clock time", "wipe_times", "3:15")
rejects("rejects hour 25", "wipe_times", "25:00")
check("accepts wipe times", validate("wipe_times", "03:15, 21:45") == "03:15,21:45")
check("sorts warnings descending", validate("wipe_warn_minutes", "1,10,5") == "10,5,1")
rejects("rejects a privileged port", "status_port", 80)
check("port 0 means off", validate("status_port", 0) == 0)
rejects("rejects XP rate above the cap", "xp_multiplier", 99999)
check("a blank admin password saves (a fresh install must be able to)",
      validate("admin_password", "") == "")
rejects("rejects a bad cluster id", "cluster_id", "ACME Cluster!")
check("bool accepts TRUE", validate("join_leave", "TRUE") is True)
check("bool accepts off", validate("join_leave", "off") is False)

# ---- store behaviour
st = new_store()
st.patch({"session_prefix": "ACME", "admin_password": "hunter2", "xp_multiplier": 10})
check("reads back a set value", st.get("session_prefix") == "ACME")
check("falls back to the default", st.get("cluster_id") == "arkcluster")
check("apply level reported", "recreate" in st.patch({"max_players": 200}))
check("no-op change reports nothing", st.patch({"max_players": 200}) == set())

try:
    st.patch({"max_players": 20, "mod_ids": "bad"})
    check("a bad value blocks the whole patch", False)
except Invalid as e:
    check("a bad value blocks the whole patch", True)
check("patch was atomic - good value not applied", st.get("max_players") == 200)

st.patch({"mem_limit": "32g"}, map_name="astraeos")
check("per-map override reads back", st.get("mem_limit", "astraeos") == "32g")
check("other maps keep the cluster value", st.get("mem_limit", "island") == "20g")
try:
    st.patch({"xp_multiplier": 5}, map_name="island")
    check("refuses a per-map value on a cluster-wide setting", False)
except Invalid:
    check("refuses a per-map value on a cluster-wide setting", True)

st.save()
check("store is saved 0600", oct(os.stat(st.path).st_mode)[-3:] == "600",
      oct(os.stat(st.path).st_mode))
check("store reloads", Store(st.path).load().get("session_prefix") == "ACME")

# ---- hand edits are caught, not trusted
bad = json.load(open(st.path))
bad["cluster"]["mod_ids"] = "not,numbers"
bad["cluster"]["nonsense_key"] = 1
json.dump(bad, open(st.path, "w"))
problems = Store(st.path).load().validate_all()
check("hand-edited bad value caught", "cluster.mod_ids" in problems, problems)
check("hand-edited unknown key caught", "cluster.nonsense_key" in problems, problems)

# ---- generation
st = new_store()
st.patch({"admin_password": "pw", "mod_ids": "929110,222222", "motd": "hello"})
st.patch({"mem_limit": "32g"}, map_name="astraeos")
env = generate_env(st)
check("env has the mod list", "MOD_IDS=929110,222222" in env)
check("env writes bools as TRUE", "BATTLEEYE=TRUE" in env)
check("env carries the per-map override", "MEM_LIMIT_ASTRAEOS=32g" in env, env[-200:])
check("env says it is generated", env.startswith("# Generated by Obelisk"))
check("no obelisk-only setting leaks into env", "admin_token" not in env.lower())
st.patch({"wipe_times": "03:15,21:45"})
env = generate_env(st)
check("wipe schedule still reaches the running bot", "WIPE_TIMES=03:15,21:45" in env, env)
check("wipe warnings still reach the running bot", "WIPE_WARN_MINUTES=10,5,1" in env, env)

gus = generate_ini(st, "GameUserSettings")
check("INI has the XP rate", "XPMultiplier=1.0" in gus, gus)
check("inverted bool written correctly", "DisableStructureDecayPVE=False" in gus, gus)
game = generate_ini(st, "Game")
check("Game.ini gets maturation", "BabyMatureSpeedMultiplier=1.0" in game, game)
check("Game.ini has the right section",
      "[/Script/ShooterGame.ShooterGameMode]" in game, game)
check("MOTD is not written to the INI (POK regenerates it)", "MOTD" not in gus.upper())

# ---- nothing in the product is one person's cluster
from .schema import SETTINGS as _ALL
# Defaults must describe nobody in particular: no host names, no pool-specific
# paths, no leftover branding. This is the guard that keeps the repo a product.
import re as _re
_bad_default = _re.compile(r"(/mnt/(zfs|disk\d)|\b\d{1,3}(\.\d{1,3}){3}\b|\.local\b)", _re.I)
_leak = [s["key"] for s in _ALL if _bad_default.search(str(s.get("default", "")))]
check("no deployment-specific defaults in the schema", not _leak, _leak)
_shouty = [s["key"] for s in _ALL
           if s["key"] in ("session_prefix", "session_tags", "motd", "wipe_times")
           and str(s.get("default", ""))]
check("no opinionated branding or schedules by default", not _shouty, _shouty)

# ---- readiness: savable now, but not startable until it's filled in
st = new_store()
_bad_defaults = [k for k in BY_KEY if _try_default(k)]
check("a fresh install can save every one of its own defaults", not _bad_defaults, _bad_defaults)
blocking = st.readiness()
check("readiness names the admin password", [b["key"] for b in blocking] == ["admin_password"], blocking)
st.patch({"admin_password": "pw"})
check("readiness clears once it is set", st.readiness() == [])

from .compose import generate_compose as _gc
st_blank = new_store()
st_blank.patch({"maps": "island"})
try:
    _gc(st_blank); check("cluster generation refuses when not ready", False)
except ValueError as e:
    check("cluster generation refuses when not ready", "Admin / RCON password" in str(e), str(e))

# ---- two phases: Docker owns the mounts and ports, the UI owns everything else
from .schema import INSTALL_KEYS
st_p = new_store()
try:
    st_p.patch({"appdata": "/mnt/user/somewhere"})
    check("the UI cannot change a bind mount", False)
except Invalid as e:
    check("the UI cannot change a bind mount", "container" in str(e).lower(), str(e))
try:
    st_p.patch({"status_port": 9000})
    check("the UI cannot change the published port", False)
except Invalid:
    check("the UI cannot change the published port", True)
st_p.patch({"appdata": "/mnt/user/somewhere", "status_port": 9000}, source="install")
check("the container can set them at boot", st_p.get("appdata") == "/mnt/user/somewhere"
      and st_p.get("status_port") == 9000)
check("the UI can still change everything else", st_p.patch({"max_players": 30}) != set())
# Only what Docker genuinely fixes at create time belongs here. Timezone used to be on
# this list and wasn't boot-critical - it is a UI setting now, picked from the zone list.
check("install list is short and honest", sorted(INSTALL_KEYS) ==
      ["appdata", "status_port"], INSTALL_KEYS)
check("timezone is not an install setting", "timezone" not in INSTALL_KEYS)
rows = st_p.install_settings()
check("install settings are exposed for a read-only panel",
      {r["key"] for r in rows} == set(INSTALL_KEYS) and all(r["help"] for r in rows), rows)

# ---- map selection
rejects("rejects an unknown map", "maps", "island,atlantis")
rejects("rejects a duplicated map", "maps", "island,island")
rejects("rejects an empty map list", "maps", "")
check("accepts a map list", validate("maps", "Island, Center") == "island,center")

# ---- compose generation
from .compose import generate_compose
st = new_store()
st.patch({"admin_password": "pw", "maps": "island,center,scorched",
          "session_prefix": "TEST"})
st.patch({"appdata": "/mnt/user/appdata/ark"}, source="install")   # as the container would
st.patch({"mem_limit": "32g"}, map_name="center")
yml = generate_compose(st, project="ark")
check("compose names every chosen map", all(("container_name: asa_%s" % m) in yml
                                            for m in ("island", "center", "scorched")), yml[:400])
check("compose omits maps not chosen", "asa_genesis" not in yml)
check("ports count up from the base", 'ASA_PORT: "7779"' in yml and 'RCON_PORT: "27022"' in yml, yml)
check("first map is the update master", "depends_on" in yml and yml.count("depends_on") == 2)
check("per-map memory override honoured", "mem_limit: 32g" in yml and "mem_limit: 20g" in yml)
check("session name is numbered after the prefix", 'SESSION_NAME: "TEST 03 | Scorched Earth"' in yml, yml)
# The manager is not in the stack it generates - it is the thing generating it.
check("no manager service in the generated stack", "container_name: obelisk" not in yml)
check("maps are addressable by container name", "container_name: asa_center" in yml, yml)
# The data root carries the saves; the game install is deliberately outside it, which
# is what keeps a backup of the root portable.
check("the data root is used for saves",
      "/mnt/user/appdata/ark/instances/island/Saved" in yml and
      "/mnt/user/appdata/ark/shared" in yml, yml[:400])
# The game install belongs with the other large files, inside the Ark folder - it is
# kept out of *backups*, not out of the folder.
check("the game install lives inside the Ark folder",
      "/mnt/user/appdata/ark/ServerFiles:/home/pok/arkserver" in yml, yml[:400])
check("compose says it is generated", yml.startswith("# Generated by Obelisk"))
check("every map gets an instance name", yml.count("INSTANCE_NAME:") == 3, yml)
check("first map is the update MASTER", 'UPDATE_COORDINATION_ROLE: "MASTER"' in yml)
check("the rest are FOLLOWERs", yml.count('UPDATE_COORDINATION_ROLE: "FOLLOWER"') == 2)
check("update priority counts up", 'UPDATE_COORDINATION_PRIORITY: "3"' in yml, yml)
try:
    st2 = new_store(); st2.data["cluster"]["maps"] = "island,atlantis"
    generate_compose(st2); check("compose rejects an unknown map", False)
except KeyError:
    check("compose rejects an unknown map", True)

# ---- passthrough for anything the schema doesn't model
st.patch({"extra_gameusersettings": "[GaiaEssentials]\nFoodPack=True"})
gus2 = generate_ini(st, "GameUserSettings")
check("extra INI passed through verbatim", "[GaiaEssentials]" in gus2 and "FoodPack=True" in gus2, gus2)
check("passthrough is marked in the output", "passed through from Advanced" in gus2)

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)

