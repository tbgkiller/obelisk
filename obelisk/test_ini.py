"""
Editing somebody else's config file without damaging it.

The failure this suite exists for has not happened yet, and that is the point. Obelisk
models five of the 172 keys in this cluster's real INIs. A writer that renders those
files from Obelisk's own settings would delete the other 167 - including six belonging
to a mod - and the operator would find out at the next server start, from behaviour
rather than from an error.

So the rule is tested rather than intended: a setting Obelisk does not model is a
setting Obelisk does not touch.

Fixture values are synthetic, but their SHAPE is taken from the real files: a comment
banner, comments between keys inside a section, a mod's own section, and a key repeated
because that is how the engine expresses a list.
"""

import os, sys, tempfile

from . import ini

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


REAL_SHAPE = """\
; ============================================================
;  Synthetic cluster - GameUserSettings.ini (shared by every map)
;  Session name, ports and admin password are injected by the
;  container - don't put them here.
; ============================================================

[ServerSettings]
; --- Rates ---
TamingSpeedMultiplier=10.0
HarvestAmountMultiplier=10.0
ItemStackSizeMultiplier=10.0

; --- Survival ---
PlayerCharacterWaterDrainMultiplier=0.5
ServerPVE=True
; MaxPlayers is injected via -WinLiveMaxPlayers by the container.

[/Script/Engine.GameSession]
MaxPlayers=70

[GaiaEssentials]
bEnableStarterKit=True
StarterKitLevel=5

[MessageOfTheDay]
Message=Welcome
Duration=30
"""

ARRAYS = """\
[/Script/ShooterGame.ShooterGameMode]
; The engram overrides carried over from the old cluster
OverrideNamedEngramEntries=(EngramClassName="EngramEntry_A_C",EngramLevelRequirement=2)
OverrideNamedEngramEntries=(EngramClassName="EngramEntry_B_C",EngramLevelRequirement=2)
OverrideNamedEngramEntries=(EngramClassName="EngramEntry_C_C",EngramLevelRequirement=3)
BabyMatureSpeedMultiplier=20.0
"""


# ---------------------------------------------------------------- round trip
for name, text in (("the real file's shape", REAL_SHAPE),
                   ("repeated keys", ARRAYS),
                   ("CRLF line endings", REAL_SHAPE.replace("\n", "\r\n")),
                   ("no trailing newline", REAL_SHAPE.rstrip("\n")),
                   ("an empty file", ""),
                   ("only comments", "; nothing but a note\n"),
                   ("a stray key before any section", "Loose=1\n[S]\nA=2\n")):
    check("parse and write is byte-for-byte identical: %s" % name,
          ini.parse(text).text() == text,
          repr(ini.parse(text).text()[:120]))

# ---------------------------------------------------------------- reading
d = ini.parse(REAL_SHAPE)
check("sections are found in file order",
      d.sections() == ["ServerSettings", "/Script/Engine.GameSession",
                       "GaiaEssentials", "MessageOfTheDay"], d.sections())
check("a value reads back", d.get("ServerSettings", "TamingSpeedMultiplier") == "10.0")
check("a mod's section is readable like any other",
      d.get("GaiaEssentials", "StarterKitLevel") == "5")
check("a missing key is None, not an error",
      d.get("ServerSettings", "NoSuchThing") is None)
check("key lookup ignores case, as the engine does",
      d.get("ServerSettings", "serverpve") == "True")
check("a semicolon inside a value is not a comment",
      ini.parse('[S]\nA=(Name="x;y")\n').get("S", "A") == '(Name="x;y")')
check("a commented-out key is not a key",
      ini.parse("[S]\n;A=1\n").get("S", "A") is None)

a = ini.parse(ARRAYS)
check("a repeated key reads back as a list",
      len(a.get_all("/Script/ShooterGame.ShooterGameMode",
                    "OverrideNamedEngramEntries")) == 3,
      a.get_all("/Script/ShooterGame.ShooterGameMode", "OverrideNamedEngramEntries"))
check("and the scalar reader takes the last, like the engine",
      ini.parse("[S]\nA=1\nA=2\n").get("S", "A") == "2")

# ---------------------------------------------------------------- writing
d = ini.parse(REAL_SHAPE)
d.set("ServerSettings", "TamingSpeedMultiplier", "15.0")
out = d.text()
check("changing a value changes exactly one line",
      sum(1 for x, y in zip(REAL_SHAPE.splitlines(), out.splitlines()) if x != y) == 1,
      [(x, y) for x, y in zip(REAL_SHAPE.splitlines(), out.splitlines()) if x != y])
check("the file is the same length in lines",
      len(out.splitlines()) == len(REAL_SHAPE.splitlines()))
check("the new value is there", "TamingSpeedMultiplier=15.0" in out)
check("every comment survives",
      [l for l in out.splitlines() if l.startswith(";")] ==
      [l for l in REAL_SHAPE.splitlines() if l.startswith(";")])

# ---- the guard this whole file is for
check("the mod's section is untouched by a write elsewhere",
      "[GaiaEssentials]\nbEnableStarterKit=True\nStarterKitLevel=5" in out, out)
check("and every other unmodelled key keeps its value",
      all(k in out for k in ("MaxPlayers=70", "ServerPVE=True", "Message=Welcome",
                             "ItemStackSizeMultiplier=10.0",
                             "PlayerCharacterWaterDrainMultiplier=0.5")), out)
check("section order is unchanged", ini.parse(out).sections() == d.sections())

# ---- a new key goes in its section, not at the end of the file
d2 = ini.parse(REAL_SHAPE)
d2.set("ServerSettings", "XPMultiplier", "3.0")
lines2 = d2.text().splitlines()
check("a new key is added", "XPMultiplier=3.0" in lines2)
check("inside its own section, not appended to the file",
      lines2.index("XPMultiplier=3.0") < lines2.index("[/Script/Engine.GameSession]"),
      lines2)
check("and the trailing comment of that section is kept",
      "; MaxPlayers is injected via -WinLiveMaxPlayers by the container." in lines2)

# ---- a new section is created only when asked for
d3 = ini.parse(REAL_SHAPE)
d3.set("BrandNew", "Thing", "1")
check("a missing section is created at the end",
      d3.text().rstrip().endswith("[BrandNew]\nThing=1"), d3.text()[-60:])
check("without disturbing what was there",
      d3.text().startswith(REAL_SHAPE.rstrip("\n").split("[BrandNew]")[0][:200]))

# ---- repeated keys
a2 = ini.parse(ARRAYS)
a2.set_all("/Script/ShooterGame.ShooterGameMode", "OverrideNamedEngramEntries",
           ['(EngramClassName="EngramEntry_Z_C",EngramLevelRequirement=9)'])
check("replacing a list leaves exactly the values given",
      a2.get_all("/Script/ShooterGame.ShooterGameMode",
                 "OverrideNamedEngramEntries") ==
      ['(EngramClassName="EngramEntry_Z_C",EngramLevelRequirement=9)'])
check("and does not disturb the scalar beside it",
      a2.get("/Script/ShooterGame.ShooterGameMode", "BabyMatureSpeedMultiplier") == "20.0")
check("nor the comment above it",
      "; The engram overrides carried over from the old cluster" in a2.text())

a3 = ini.parse(ARRAYS)
a3.set("/Script/ShooterGame.ShooterGameMode", "BabyMatureSpeedMultiplier", "30.0")
check("editing a scalar leaves a repeated key alone",
      len(a3.get_all("/Script/ShooterGame.ShooterGameMode",
                     "OverrideNamedEngramEntries")) == 3)

# ---- unset
d4 = ini.parse(REAL_SHAPE)
d4.unset("ServerSettings", "ServerPVE")
check("unset removes the key", d4.get("ServerSettings", "ServerPVE") is None)
check("and only that key", "TamingSpeedMultiplier=10.0" in d4.text())

# ---------------------------------------------------------------- the file on disk
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "GameUserSettings.ini")
with open(path, "w", encoding="utf-8", newline="") as fh:
    fh.write(REAL_SHAPE)

changed, detail = ini.merge_file(path, {})
check("a no-op writes nothing at all", changed is False and detail == "no change", detail)
check("and leaves no backup behind, because nothing was replaced",
      not os.path.exists(path + ".bak"))

changed, detail = ini.merge_file(
    path, {("ServerSettings", "TamingSpeedMultiplier"): "12.0"})
check("a real change is written", changed, detail)
check("the previous file is kept as .bak", os.path.isfile(path + ".bak"))
with open(path + ".bak", encoding="utf-8") as fh:
    check("and the .bak is exactly what was there before", fh.read() == REAL_SHAPE)
with open(path, encoding="utf-8", newline="") as fh:
    now = fh.read()
check("the new file has the new value", "TamingSpeedMultiplier=12.0" in now)
check("and is otherwise identical",
      now.replace("TamingSpeedMultiplier=12.0", "TamingSpeedMultiplier=10.0") == REAL_SHAPE)

# Writing a value that is already there is still a no-op.
changed, _d = ini.merge_file(path, {("ServerSettings", "TamingSpeedMultiplier"): "12.0"})
check("setting a value to what it already is changes nothing", changed is False)

# A file that does not exist yet is created, not an error.
fresh = os.path.join(tmp, "Game.ini")
changed, _d = ini.merge_file(fresh, {("/Script/ShooterGame.ShooterGameMode",
                                      "BabyMatureSpeedMultiplier"): "20.0"})
check("a file that does not exist yet is created", changed and os.path.isfile(fresh))
check("with the section it needs",
      ini.read(fresh).get("/Script/ShooterGame.ShooterGameMode",
                          "BabyMatureSpeedMultiplier") == "20.0")

print("\nFAILURES: %s" % fails if fails else "\nall ini tests passed")
sys.exit(1 if fails else 0)
