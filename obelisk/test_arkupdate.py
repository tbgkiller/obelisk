"""
Knowing what is installed, and refusing to guess at what is newer.

Every check here is about one of two lies. The first is "up to date" said when nothing
was actually asked - the failure the Obelisk version check already had to be rescued
from, arriving here with two fresh sources that both invite it. The second is a verdict
about mods drawn from a cache that describes a fetch that happened days ago.

So the shape being defended is: installed comes from the filesystem, available comes
from off the machine, and when the second one fails the answer is None - which the UI
renders as "could not check", never as a green tick.
"""

import json
import sys

from . import arkupdate

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


ACF = '''"AppState"
{
\t"appid"\t\t"2430930"
\t"StateFlags"\t\t"4"
\t"buildid"\t\t"25117056"
}
'''

# The seven on the live cluster, and the file ids actually on its disk.
LIVE = {"929110": "7738786", "940003": "6830549", "929420": "8160173",
        "928621": "6621162", "929902": "7101446", "950914": "6897266",
        "931607": "7641231"}
IDS = list(LIVE)

STEAM_OK = json.dumps({"status": "success", "data": {"2430930": {"depots": {
    "branches": {"public": {"buildid": "25117056", "timeupdated": "1788548219"}}}}}})
STEAM_NEW = STEAM_OK.replace("25117056", "25200000")


def fs(mods=None, acf=ACF, library=None):
    """A pretend ServerFiles: what listdir and read would answer."""
    mods = LIVE if mods is None else mods

    def listdir(path):
        if path.endswith(arkupdate.MODS_SUBDIR.replace("/", "\\")) or \
                path.replace("\\", "/").endswith(arkupdate.MODS_SUBDIR):
            return ["%s_%s" % (p, f) for p, f in mods.items()]
        raise OSError("no such directory")

    def read(path):
        p = path.replace("\\", "/")
        if p.endswith(".acf"):
            if acf is None:
                raise OSError("not there")
            return acf
        if p.endswith("library.json"):
            if library is None:
                raise OSError("not there")
            return library
        raise OSError("not there")

    return listdir, read


def opener_for(steam=STEAM_OK, cf=None, fail=None):
    cf = LIVE if cf is None else cf

    def opener(url):
        if fail and fail in url:
            raise OSError("the network said no")
        if "steamcmd" in url:
            if steam is None:
                raise OSError("unreachable")
            return steam
        project = url.rsplit("/", 1)[-1]
        if project not in cf:
            raise OSError("404")
        return json.dumps({"id": int(project), "title": "Mod %s" % project,
                           "urls": {"curseforge": "https://example/%s" % project},
                           "download": {"id": int(cf[project])}})
    return opener


# ---- the installed build comes from the install itself
listdir, read = fs()
build, problem = arkupdate.installed_build("/ark/ServerFiles", read=read)
check("the installed build is read from the appmanifest", build == "25117056", build)
check("and nothing is wrong when it parses", problem == "", problem)

_, problem = arkupdate.installed_build("/ark/ServerFiles", read=fs(acf=None)[1])
check("a missing appmanifest is reported, not guessed around",
      "could not read" in problem, problem)

_, problem = arkupdate.installed_build("/ark/ServerFiles",
                                       read=lambda p: '"AppState" { "appid" "2430930" }')
check("an appmanifest with no buildid says the install may be incomplete",
      "incomplete" in problem, problem)

# ---- the available build, and the failure that must never look like success
build, problem = arkupdate.available_build(opener=opener_for())
check("the available build is read from Steam's public branch", build == "25117056", build)

build, problem = arkupdate.available_build(opener=opener_for(steam=None))
check("an unreachable Steam gives no build id", build is None, build)
check("and says so", "could not reach" in problem, problem)

build, problem = arkupdate.available_build(opener=lambda u: '{"data": {}}')
check("a reply without a build id is a problem, not a None treated as current",
      build is None and "did not have" in problem, problem)

# ---- installed mods are the directory names, which cannot be stale
listdir, read = fs()
got = arkupdate.installed_mods("/ark/ServerFiles", listdir=listdir, read=read)
check("every mod on disk is found", len(got) == 7, sorted(got))
check("the file id comes from the directory name",
      got["929110"]["file_id"] == "7738786", got.get("929110"))
check("a ServerFiles with no mods yet is empty, not an error",
      arkupdate.installed_mods("/ark/ServerFiles",
                               listdir=lambda p: (_ for _ in ()).throw(OSError())) == {})

# ---- library.json is allowed to name mods and nothing else
#
# This is the trap the module exists to avoid. The cache is written when mods are
# fetched and not again, so its mainFileId is the installed id by construction - on the
# live cluster all seven matched, three days after the last fetch. Believing it would
# report "all mods current" forever, including the day after a mod updates.
STALE = json.dumps({"installedMods": [
    {"pathOnDisk": "83374/929110_1111111",
     "details": {"iD": 929110, "name": "TG Stacking Mod 10000-90",
                 "mainFileId": 1111111}}]})
listdir, read = fs(library=STALE)
got = arkupdate.installed_mods("/ark/ServerFiles", listdir=listdir, read=read)
check("library.json supplies the display name",
      got["929110"]["name"] == "TG Stacking Mod 10000-90", got["929110"])
check("but never the version - that stays the directory on disk",
      got["929110"]["file_id"] == "7738786", got["929110"])
check("and its mainFileId reaches nothing at all",
      "1111111" not in json.dumps(got), got["929110"])

listdir, read = fs(library="{ this is not json")
got = arkupdate.installed_mods("/ark/ServerFiles", listdir=listdir, read=read)
check("an unreadable cache costs the names and nothing else",
      len(got) == 7 and got["929110"]["file_id"] == "7738786", got.get("929110"))

# ---- comparing, with three outcomes rather than two
rows = arkupdate.compare({"1": {"file_id": "10"}, "2": {"file_id": "20"}},
                         {"1": {"file_id": "11"}, "2": {"file_id": "20"}})
by_id = {r["id"]: r for r in rows}
check("a different file id is newer", by_id["1"]["newer"] is True, by_id["1"])
check("the same file id is not", by_id["2"]["newer"] is False, by_id["2"])

rows = arkupdate.compare({"1": {"file_id": "10"}},
                         {"1": {"file_id": None, "problem": "could not ask"}})
check("a mod we could not ask about is unknown, not current",
      rows[0]["newer"] is None, rows[0])
check("and carries why", "could not ask" in rows[0]["problem"], rows[0])

rows = arkupdate.compare({"1": {"file_id": None}}, {"1": {"file_id": "11"}})
check("a listed mod that is not on disk yet is unknown too",
      rows[0]["newer"] is None, rows[0])
check("and says that is what happened", "not on disk" in rows[0]["problem"], rows[0])

# ---- the whole status, which is what the panel renders
listdir, read = fs()
st = arkupdate.status("/ark/ServerFiles", IDS, opener=opener_for(),
                      listdir=listdir, read=read)
check("a current cluster reports no build update",
      st["build"]["newer"] is False, st["build"])
check("and no mod updates", st["mods_newer"] == [], st["mods_newer"])
check("nothing is unknown when every source answered", st["unknown"] is False, st)
check("and the panel has a row per configured mod", len(st["mods"]) == 7, len(st["mods"]))

st = arkupdate.status("/ark/ServerFiles", IDS, opener=opener_for(steam=STEAM_NEW),
                      listdir=listdir, read=read)
check("a newer build is reported as one", st["build"]["newer"] is True, st["build"])
check("and shows both numbers so the claim can be checked",
      st["build"]["running"] == "25117056" and st["build"]["latest"] == "25200000",
      st["build"])
check("any_newer is true when the build moved", st["any_newer"] is True, st)

moved = dict(LIVE, **{"929420": "9999999"})
st = arkupdate.status("/ark/ServerFiles", IDS, opener=opener_for(cf=moved),
                      listdir=listdir, read=read)
check("one updated mod is picked out", [r["id"] for r in st["mods_newer"]] == ["929420"],
      st["mods_newer"])
check("and the other six are left alone",
      sum(1 for r in st["mods"] if r["newer"] is False) == 6, st["mods"])

# ---- the failure that started all of this: silence must never read as up to date
st = arkupdate.status("/ark/ServerFiles", IDS, opener=opener_for(steam=None),
                      listdir=listdir, read=read)
check("an unreachable Steam leaves the build unknown",
      st["build"]["newer"] is None, st["build"])
check("and the panel is told something could not be checked",
      st["unknown"] is True, st)
check("which is not the same as any_newer being false",
      st["any_newer"] is False and st["unknown"] is True, st)

st = arkupdate.status("/ark/ServerFiles", IDS, opener=opener_for(fail="cfwidget"),
                      listdir=listdir, read=read)
check("mods we could not ask about are unknown, all of them",
      all(r["newer"] is None for r in st["mods"]), st["mods"][:1])
check("while the build is still answered - one source failing is not both",
      st["build"]["newer"] is False, st["build"])

# ---- leftovers on disk are not this page's business
listdir, read = fs(mods=dict(LIVE, **{"111111": "222222"}))
st = arkupdate.status("/ark/ServerFiles", IDS, opener=opener_for(),
                      listdir=listdir, read=read)
check("a mod on disk that nobody asked for is left out of the comparison",
      "111111" not in [r["id"] for r in st["mods"]], [r["id"] for r in st["mods"]])

# ---- the boot log, which is the evidence a staging server exists to produce
GOOD = "\n".join(
    ["[2026.09.04-23.29.36:635][ 26]LogCFCore: No need to update existing mod: Mod %s (%s)"
     % (p, p) for p in IDS] +
    ["[2026.09.04-23.29.36:635][ 26]LogCFCore: Mod valid: Mod %s (%s)" % (p, p)
     for p in IDS] +
    ["[2026.09.04-23.29.38:367][ 27]UShooterEngine::LoadGameMods with 7 mods"] +
    ["[2026.09.04-23.29.38:460][ 27]UShooterEngine::LoadGameMods Loading Mod "
     "ShooterGame/Mods/83374/%s_%s/X/Content/Y.uasset : %s" % (p, LIVE[p], p)
     for p in IDS])

ok, problems, detail = arkupdate.read_boot_log(GOOD, IDS)
check("a clean boot with every mod valid and loaded passes", ok, problems)
check("and reports the exact file id the server loaded",
      detail["loaded"]["929110"] == "7738786", detail["loaded"])
check("for all seven", len(detail["loaded"]) == 7, detail["loaded"])

missing = "\n".join(l for l in GOOD.splitlines() if "929420" not in l)
ok, problems, _ = arkupdate.read_boot_log(missing, IDS)
check("a mod the server never validated fails the boot", not ok, problems)
check("and is named", any("929420" in p for p in problems), problems)

valid_not_loaded = "\n".join(
    l for l in GOOD.splitlines() if "Loading Mod" not in l or "929902" not in l)
ok, problems, _ = arkupdate.read_boot_log(valid_not_loaded, IDS)
check("a mod that was valid but never loaded fails too", not ok, problems)
check("and says which of the two it was",
      any("never loaded" in p for p in problems), problems)

ok, problems, _ = arkupdate.read_boot_log(GOOD.replace("with 7 mods", "with 6 mods"), IDS)
check("a count that disagrees with the configuration fails", not ok, problems)

ok, problems, _ = arkupdate.read_boot_log("nothing happened here", IDS)
check("a log with no mod lines at all fails rather than passing vacuously",
      not ok, problems)
check("saying the server never reported loading any",
      any("never reported" in p for p in problems), problems)

noisy = GOOD + "\nLogCFCore: Warning: failed to reach analytics endpoint"
ok, _, _ = arkupdate.read_boot_log(noisy, IDS)
check("a complaint about something that is not one of our mods does not fail the boot",
      ok)

broken = GOOD + "\nLogCFCore: Error: unable to install mod 929420"
ok, problems, _ = arkupdate.read_boot_log(broken, IDS)
check("but one that names a mod we care about does",
      not ok and any("reported trouble" in p for p in problems), problems)

ok, problems, _ = arkupdate.read_boot_log("", [])
check("a cluster with no mods configured has nothing to prove", ok, problems)

# ---- the two stale sources must not be consulted anywhere in the module
#
# Prose is where both of them are supposed to appear - the module explains at length
# why it refuses them. What must not happen is either name turning up in code, so the
# module docstring and the comments come out before looking.
import ast

src = open(arkupdate.__file__, encoding="utf-8").read()
doc = ast.get_docstring(ast.parse(src), clean=False)
code = "\n".join(l for l in src.replace(doc or "", "", 1).splitlines()
                 if not l.strip().startswith("#"))
check("pending_manual_update.env is never read", "pending_manual_update" not in code)
check("and mainFileId is never compared against", "mainFileId" not in code)

print("\nFAILURES: %s" % fails if fails else "\nall arkupdate tests passed")
sys.exit(1 if fails else 0)
