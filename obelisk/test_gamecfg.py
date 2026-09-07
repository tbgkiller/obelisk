"""
Reading the operator's real settings in, and writing only theirs back out.

The two failures this guards against are opposites, and both are silent.

Reading too little: the admin page opens on a screen of defaults while the server runs
on 10x rates, so the first save quietly resets a cluster somebody tuned for months.

Writing too much: a key nobody set gets written at whatever this schema calls its
default, and Obelisk has invented configuration the operator never asked for.
"""

import os, sys, tempfile

from . import gamecfg, gamesettings, ini, layout
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


GUS = """\
; a banner somebody wrote
[ServerSettings]
; --- Rates ---
TamingSpeedMultiplier=10.0
HarvestAmountMultiplier=10.0
ItemStackSizeMultiplier=10.0
ServerPVE=True
DinoCountMultiplier=2.0
AutoSavePeriodMinutes=15

[GaiaEssentials]
bEnableStarterKit=True
"""

GAME = """\
[/Script/ShooterGame.ShooterGameMode]
BabyMatureSpeedMultiplier=1000.0
MatingIntervalMultiplier=0.05
PerLevelStatsMultiplier_Player[7]=3.0
PerLevelStatsMultiplier_Player[10]=2.0
PerLevelStatsMultiplier_DinoTamed[0]=1.0
PerLevelStatsMultiplier_DinoTamed[8]=1.0
OverrideNamedEngramEntries=(EngramClassName="EngramEntry_A_C",EngramLevelRequirement=2)
OverrideNamedEngramEntries=(EngramClassName="EngramEntry_B_C",EngramLevelRequirement=2)
"""


def fresh():
    base = tempfile.mkdtemp()
    root = os.path.join(base, "obelisk")
    ark = os.path.join(base, "ark")
    os.makedirs(root, exist_ok=True)
    os.environ["OBELISK_ARK"] = ark
    layout.ensure_ark(ark)
    conf = layout.ark_paths(ark)["shared_config"]
    with open(os.path.join(conf, "GameUserSettings.ini"), "w", encoding="utf-8", newline="") as fh:
        fh.write(GUS)
    with open(os.path.join(conf, "Game.ini"), "w", encoding="utf-8", newline="") as fh:
        fh.write(GAME)
    st = Store(os.path.join(root, "settings.json")).load()
    st.patch({"status_port": 8088}, source="install")
    st.patch({"maps": "island", "admin_password": "synthetic-pw", "cluster_id": "cfgtest"})
    return st, conf


# ---------------------------------------------------------------- the catalogue
tg = gamecfg.targets()
check("the catalogue is wired into the schema", len(tg) > 100, len(tg))
check("every target names a real file",
      all(w in gamecfg.FILES for _s, w, _sec, _k in tg),
      sorted({w for _s, w, _sec, _k in tg}))
check("the curated five are still in there",
      {"XPMultiplier", "HarvestAmountMultiplier", "TamingSpeedMultiplier",
       "DisableStructureDecayPVE", "BabyMatureSpeedMultiplier"}
      <= {k for _s, _w, _sec, k in tg})
check("no INI key is claimed by two settings",
      len({(w, sec, k) for _s, w, sec, k in tg}) == len(tg))

# ---------------------------------------------------------------- adoption
st, conf = fresh()
check("nothing is in the store before adoption",
      "ItemStackSizeMultiplier" not in st.data["cluster"])

adopted, skipped = gamecfg.adopt(st)
check("the operator's values are adopted", adopted >= 6, (adopted, skipped))
check("a float comes back as a number", st.get("ItemStackSizeMultiplier") == 10.0,
      st.get("ItemStackSizeMultiplier"))
check("a bool comes back as a bool", st.get("ServerPVE") is True)
check("an int comes back as an int", st.get("AutoSavePeriodMinutes") == 15)
check("Game.ini is adopted too", st.get("MatingIntervalMultiplier") == 0.05)
check("the curated key adopts his value, not its own default",
      st.get("maturation_multiplier") == 1000.0, st.get("maturation_multiplier"))
check("a key that is in neither file is not invented",
      "PreventDiseases" not in st.data["cluster"])

# ---------------------------------------------------------------- writing back
before_gus = open(os.path.join(conf, "GameUserSettings.ini"), encoding="utf-8").read()
before_game = open(os.path.join(conf, "Game.ini"), encoding="utf-8").read()

ok, msg = gamecfg.apply(st)
check("writing back straight after adoption changes nothing", ok, msg)
check("and says so", "already match" in msg, msg)
check("so the file is untouched, byte for byte",
      open(os.path.join(conf, "GameUserSettings.ini"), encoding="utf-8").read() == before_gus)
check("no .bak is left behind for a write that did not happen",
      not os.path.exists(os.path.join(conf, "GameUserSettings.ini.bak")))

# now a real edit
st.patch({"ItemStackSizeMultiplier": 20.0})
ok, msg = gamecfg.apply(st)
check("a changed setting is written", ok and "Wrote" in msg, msg)
after = open(os.path.join(conf, "GameUserSettings.ini"), encoding="utf-8").read()
check("the new value is in the file", "ItemStackSizeMultiplier=20.0" in after)
check("exactly one line differs",
      sum(1 for a, b in zip(before_gus.splitlines(), after.splitlines()) if a != b) == 1,
      [(a, b) for a, b in zip(before_gus.splitlines(), after.splitlines()) if a != b])
check("the previous file is kept", os.path.isfile(os.path.join(conf, "GameUserSettings.ini.bak")))

# ---- the guard, again, at this level
check("the mod's section survives a write",
      "[GaiaEssentials]\nbEnableStarterKit=True" in after, after)
check("the banner comment survives", after.startswith("; a banner somebody wrote"))
check("Game.ini was not touched at all, since nothing in it changed",
      open(os.path.join(conf, "Game.ini"), encoding="utf-8").read() == before_game)

st.patch({"maturation_multiplier": 50.0})
gamecfg.apply(st)
after_game = open(os.path.join(conf, "Game.ini"), encoding="utf-8").read()
check("the repeated engram lines survive a write to Game.ini",
      len(ini.parse(after_game).get_all("/Script/ShooterGame.ShooterGameMode",
                                        "OverrideNamedEngramEntries")) == 2, after_game)
check("and the curated key wrote through its own name",
      "BabyMatureSpeedMultiplier=50.0" in after_game, after_game)

# ---------------------------------------------------------------- the stingy half
st2, conf2 = fresh()
st2.patch({"ItemStackSizeMultiplier": 7.0})          # one key, set deliberately
before2 = open(os.path.join(conf2, "GameUserSettings.ini"), encoding="utf-8").read()
gamecfg.apply(st2)
after2 = open(os.path.join(conf2, "GameUserSettings.ini"), encoding="utf-8").read()
check("only the key that was set is written",
      sum(1 for a, b in zip(before2.splitlines(), after2.splitlines()) if a != b) == 1,
      [(a, b) for a, b in zip(before2.splitlines(), after2.splitlines()) if a != b])
check("no unset catalogue key is added to the file",
      len(after2.splitlines()) == len(before2.splitlines()), len(after2.splitlines()))
check("in particular nothing appears at a schema default",
      "PreventDiseases" not in after2 and "ServerHardcore" not in after2)

# ---------------------------------------------------------------- persisted defaults
#
# Caught on a live cluster: a Save with nothing changed added XPMultiplier=1.0 to a file
# that had never contained it. xp_multiplier was in the store at its own default -
# written there by an ordinary store.save() long before - and "present in the store" was
# being read as "the operator set this". It is not. The operator's file said nothing
# about XP, and Obelisk answered on their behalf.
st4, conf4 = fresh()
gamecfg.adopt(st4)
_before4 = open(os.path.join(conf4, "GameUserSettings.ini"), encoding="utf-8").read()
check("XPMultiplier is genuinely absent from this file", "XPMultiplier" not in _before4)

st4.data["cluster"]["xp_multiplier"] = st4.get("xp_multiplier")   # a persisted default
ok4, msg4 = gamecfg.apply(st4)
_after4 = open(os.path.join(conf4, "GameUserSettings.ini"), encoding="utf-8").read()
check("a default sitting in the store is not written into the file",
      "XPMultiplier" not in _after4, _after4)
check("so the file is untouched", _after4 == _before4)
check("and nothing claims to have written it", "already match" in msg4, msg4)

# But a value the operator actually chose is written, even into a key the file lacks.
st4.patch({"xp_multiplier": 3.0})
gamecfg.apply(st4)
_after5 = open(os.path.join(conf4, "GameUserSettings.ini"), encoding="utf-8").read()
check("a value that differs from the default is written, absent or not",
      "XPMultiplier=3.0" in _after5, _after5)
check("and it lands inside its own section",
      _after5.index("XPMultiplier=3.0") < _after5.index("[GaiaEssentials]"), _after5)

# ---------------------------------------------------------------- unreadable values
st3, conf3 = fresh()
# Inside [ServerSettings], where the key really lives. Appending to the end of the file
# would have put it in the mod's section, where nothing is looking for it - which is how
# the first version of this test passed while proving nothing.
_p3 = os.path.join(conf3, "GameUserSettings.ini")
_t3 = open(_p3, encoding="utf-8").read().replace("DinoCountMultiplier=2.0",
                                                 "DinoCountMultiplier=lots")
open(_p3, "w", encoding="utf-8", newline="").write(_t3)
_a, skipped3 = gamecfg.adopt(st3)
check("a value that is not the type we expect is left alone, not guessed",
      any("lots" in s for s in skipped3), skipped3)
check("and the store keeps no nonsense",
      st3.data["cluster"].get("DinoCountMultiplier") in (None, 2.0),
      st3.data["cluster"].get("DinoCountMultiplier"))


# ---------------------------------------------------------------- per-level stat grids
#
# Sparse is the whole point. This file sets two stats in each of two families. A grid
# editor that wrote all twelve back would turn four lines into twenty-four, every one of
# them a number nobody chose, in a file somebody laid out by hand.
st5, conf5 = fresh()
_gpath = os.path.join(conf5, "Game.ini")
_before5 = open(_gpath, encoding="utf-8").read()

cells = gamecfg.adopt_grids(st5)
check("the operator's stat cells are adopted", cells == 4, cells)
grids = st5.data["stats"]
check("only the families that appear in the file are held",
      set(grids) == {"PerLevelStatsMultiplier_Player",
                     "PerLevelStatsMultiplier_DinoTamed"}, set(grids))
check("and only the stats they actually set",
      set(grids["PerLevelStatsMultiplier_Player"]) == {"7", "10"},
      grids["PerLevelStatsMultiplier_Player"])
check("with his values", grids["PerLevelStatsMultiplier_Player"]["7"] == 3.0)
check("index 7 is Weight and 8 is Melee Damage, per the game's table",
      dict(gamesettings.STATS)[7] == "Weight"
      and dict(gamesettings.STATS)[8] == "Melee Damage", gamesettings.STATS)
check("all twelve stats are named", len(gamesettings.STATS) == 12)
check("all five families are modelled", len(gamesettings.STAT_FAMILIES) == 5)

ok5, msg5 = gamecfg.apply(st5)
check("a grid round trip writes nothing", "already match" in msg5, msg5)
check("and the file is byte-identical",
      open(_gpath, encoding="utf-8").read() == _before5)

st5.data["stats"]["PerLevelStatsMultiplier_Player"]["7"] = 5.0
gamecfg.apply(st5)
_after5 = open(_gpath, encoding="utf-8").read()
check("editing one cell writes exactly one line",
      sum(1 for a, b in zip(_before5.splitlines(), _after5.splitlines()) if a != b) == 1,
      [(a, b) for a, b in zip(_before5.splitlines(), _after5.splitlines()) if a != b])
check("the file gains no lines - the family is still sparse",
      len(_after5.splitlines()) == len(_before5.splitlines()),
      "%d -> %d" % (len(_before5.splitlines()), len(_after5.splitlines())))
check("the changed cell has the new value",
      "PerLevelStatsMultiplier_Player[7]=5.0" in _after5)
check("the untouched cells are exactly as they were",
      "PerLevelStatsMultiplier_Player[10]=2.0" in _after5
      and "PerLevelStatsMultiplier_DinoTamed[0]=1.0" in _after5, _after5)
check("no stat the file never mentioned has appeared",
      "PerLevelStatsMultiplier_Player[0]" not in _after5
      and "PerLevelStatsMultiplier_DinoWild" not in _after5, _after5)
check("the repeated engram lines survive a grid write",
      len(ini.parse(_after5).get_all(gamesettings.GAME_MODE,
                                     "OverrideNamedEngramEntries")) == 2, _after5)
check("and the scalars beside them are untouched",
      "BabyMatureSpeedMultiplier=1000.0" in _after5)

st5.data["stats"]["PerLevelStatsMultiplier_Player"]["1"] = 2.5
gamecfg.apply(st5)
_after6 = open(_gpath, encoding="utf-8").read()
check("a newly set stat is written", "PerLevelStatsMultiplier_Player[1]=2.5" in _after6)
check("and it is the only line added",
      len(_after6.splitlines()) == len(_after5.splitlines()) + 1)

del st5.data["stats"]["PerLevelStatsMultiplier_Player"]["1"]
gamecfg.apply(st5)
_after7 = open(_gpath, encoding="utf-8").read()
check("clearing a stat removes its line instead of writing 0",
      "PerLevelStatsMultiplier_Player[1]" not in _after7, _after7)
check("and nothing else moved", _after7 == _after5)
check("an untouched family is not created",
      "PerLevelStatsMultiplier_DinoWild" not in _after7)

# ---- the near miss: a store that has not adopted the grids must not delete them
#
# grid_changes read "no cell here" as "the operator cleared it", so a store that had
# never called adopt_grids - a fresh install, or any caller that skipped it - looked
# identical to somebody who had just emptied every box, and apply() removed every stat
# line in the file. Three older tests in this suite failed the moment stat cells were
# added to the fixture, which is the only reason it was caught before deploying.
st6, conf6 = fresh()
_g6 = os.path.join(conf6, "Game.ini")
_b6 = open(_g6, encoding="utf-8").read()
gamecfg.adopt(st6)                      # scalars only - deliberately not adopt_grids
check("a store with no grids at all knows it has nothing to say",
      "stats" not in st6.data or not st6.data.get("stats"))
ok6, msg6 = gamecfg.apply(st6)
check("and writes nothing to Game.ini", open(_g6, encoding="utf-8").read() == _b6,
      msg6)
check("in particular it does not delete the stat lines",
      "PerLevelStatsMultiplier_Player[7]=3.0" in open(_g6, encoding="utf-8").read())

# but an operator who really does clear a family gets it removed
st7, conf7 = fresh()
_g7 = os.path.join(conf7, "Game.ini")
gamecfg.adopt_grids(st7)
st7.data["stats"]["PerLevelStatsMultiplier_DinoTamed"] = {}      # emptied in the form
gamecfg.apply(st7)
_a7 = open(_g7, encoding="utf-8").read()
check("clearing a whole family removes its lines",
      "PerLevelStatsMultiplier_DinoTamed[" not in _a7, _a7)
check("and leaves the other family alone",
      "PerLevelStatsMultiplier_Player[7]=3.0" in _a7)

# and the bracketed cells must never come back as scalar settings
check("no stat cell is modelled as a scalar setting",
      not any("[" in k for _s, _w, _sec, k in gamecfg.targets()),
      [k for _s, _w, _sec, k in gamecfg.targets() if "[" in k])

print("\nFAILURES: %s" % fails if fails else "\nall gamecfg tests passed")
sys.exit(1 if fails else 0)
