"""
Reading the operator's real settings in, and writing only theirs back out.

The two failures this guards against are opposites, and both are silent.

Reading too little: the admin page opens on a screen of defaults while the server runs
on 10x rates, so the first save quietly resets a cluster somebody tuned for months.

Writing too much: a key nobody set gets written at whatever this schema calls its
default, and Obelisk has invented configuration the operator never asked for.
"""

import os, sys, tempfile

from . import gamecfg, ini, layout
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

print("\nFAILURES: %s" % fails if fails else "\nall gamecfg tests passed")
sys.exit(1 if fails else 0)
