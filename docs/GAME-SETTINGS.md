# Game settings: the whole surface, editable

_**Shipped** - all five phases are built and deployed. This is kept as the design
record: what was measured, what was decided, and why the write path is shaped the way
it is. Numbers marked **measured** were read off a live cluster._

**Built:** phase 0 the read-merge-write path (`ini.py`), phase 1 the 134-setting
catalogue and adoption (`gamesettings.py`, `gamecfg.py`), phase 2 search, grouping and
changed-from-default, phase 3 the per-level stat grids, phase 4 the repeated-key row
editors, phase 5 per-map overrides. 186 settings on the page, 139 of them game config.

**Not built, and why:** per-map *game config* is impossible under the server image's
layout - it links every map's Game.ini and GameUserSettings.ini to one shared copy at
every start - so per-map overrides cover the twelve settings the generated compose file
carries instead. The deeply nested arrays stay in the Extra*.ini passthrough. 96 of the
139 settings have no documented default, so nothing claims they have changed.

## The gap, measured

The owner's live configuration, counted directly from the files the migration carried
across byte-identical:

| File | Section | Keys |
|---|---|---|
| GameUserSettings.ini | `[ServerSettings]` | **91** |
| | `[MessageOfTheDay]` | 2 |
| | `[Internationalization]` | 1 |
| | `[GaiaEssentials]` (mod) | 6 |
| Game.ini | `[/Script/ShooterGame.ShooterGameMode]` | **72** |
| | **total** | **172** |

Obelisk's schema has 52 settings, of which **5** are INI-backed:

```
xp_multiplier         -> GameUserSettings [ServerSettings] XPMultiplier
harvest_multiplier    -> GameUserSettings [ServerSettings] HarvestAmountMultiplier
taming_multiplier     -> GameUserSettings [ServerSettings] TamingSpeedMultiplier
structure_decay       -> GameUserSettings [ServerSettings] DisableStructureDecayPVE
maturation_multiplier -> Game.ini [ShooterGameMode] BabyMatureSpeedMultiplier
```

So **167 of his 172 settings are invisible to the UI**. He is right: to change any of
them he has to edit the INI by hand, and the admin page silently implies those are the
only knobs there are.

## The thing to be careful about, stated first

`settings.generate_ini()` existed and was **never called**. That is the only reason his
customizations survived the migration: the files were copied, and then left alone. It
has since been **deleted** - it rendered these files from scratch and looked like the
obvious function to call from a save path, which made it a loaded gun rather than dead
code. `settings.py` carries a comment where it was, saying not to add one back.

The moment this feature starts writing them, that safety disappears. A generator that
renders the INI from Obelisk's settings would erase all 167 keys it does not model -
including the six from a mod (`[GaiaEssentials]`) that Obelisk has no business knowing
about at all.

**So the write path is read-merge-write, never generate-from-scratch:**

1. Parse the existing file, keeping every section, key, comment and ordering.
2. Overlay only the keys Obelisk manages *and* that the operator has actually set.
3. Write back. Unknown sections, mod sections, hand-written comments all survive.
4. Keep a `.bak` of the previous file on every write.

A setting Obelisk does not model must be a setting Obelisk does not touch. That rule is
what makes the rest of this safe, and it is the first thing to test.

## Import before edit

On first run of this feature, Obelisk reads both INIs and **adopts** what it finds into
the store, so the UI opens showing his real values rather than defaults. A field the
operator has never touched shows the game's default and is not written; a field found in
his file shows his value and is marked as set. Adoption is exactly the `import_from`
mechanism the schema already uses for environment variables, pointed at INI keys.

## The inventory, by category

Sections in the real spec, with what belongs in each. Counts are what the reference
documents; the "his" column is what this owner actually has set.

### GameUserSettings.ini

| Category | Keys _(est.)_ | His | Notes |
|---|---|---|---|
| Rates and multipliers | ~25 | 12 | XP, harvest, taming, stack size, day/night cycle |
| Player survival | ~20 | 9 | food/water/stamina/oxygen drain, health recovery |
| Dino behaviour | ~20 | 7 | counts, wandering, decay, stamina drain |
| Structures | ~20 | 11 | decay, resource radius, platform limits, pickup |
| PvE / PvP rules | ~15 | 6 | `ServerPVE`, friendly fire, tribe war, offline raiding |
| Tribes and alliances | ~10 | 3 | limits, cooldowns |
| Difficulty | 2 | 2 | `OverrideOfficialDifficulty`, `DifficultyOffset` |
| Server behaviour | ~25 | 20 | crosshair, third person, map markers, tether, auto-save |
| Session/network | ~6 | 0 | **owned by Obelisk already** - port, name, MaxPlayers |
| `[MessageOfTheDay]` | 2 | 2 | already modelled |

### Game.ini `[/Script/ShooterGame.ShooterGameMode]`

| Category | Shape | His | Notes |
|---|---|---|---|
| Breeding | ~12 scalars | 9 | mating interval, hatch, mature, imprint, cuddle |
| Per-level stat multipliers | **5 arrays × 12 stats = 60 slots** | 4 arrays set | `PerLevelStatsMultiplier_Player`, `_DinoTamed`, `_DinoTamed_Add`, `_DinoTamed_Affinity`, `_DinoWild` |
| Per-dino damage/resistance | 4 open-ended arrays | 0 | `DinoClassDamageMultipliers` and friends - one row per creature |
| Harvest per resource | 1 open-ended array | 0 | `HarvestResourceItemAmountClassMultipliers` |
| Engrams | 3 arrays | **17 rows** | `OverrideNamedEngramEntries` - his biggest customization |
| Crafting / supply crates | 3 arrays | 0 | `ConfigOverrideItemCraftingCosts`, `...SupplyCrateItems` |
| Misc gameplay | ~30 scalars | ~33 | corpse locator, tribe war, flyer carry, etc. |

**Working total: roughly 200-260 individually addressable settings**, of which perhaps
150 are plain scalars and the rest live in arrays. The arrays are the hard part and the
reason a naive "one form field per key" design fails.

## The UI

Four kinds of control, because the settings are genuinely four different shapes:

1. **Scalars** - toggle, number with min/max, or slider where a range is meaningful.
   Rendered from the schema exactly as today, so adding one adds a validated field free.
2. **Stat tables** - the `PerLevelStatsMultiplier_*` families as a 5×12 grid with the
   stat names down the side (Health, Stamina, Oxygen, Food, Water, Temperature, Weight,
   Melee, Speed, Fortitude, Crafting), not sixty separate inputs.
3. **Row editors** - per-dino, per-resource, per-engram arrays as an add/remove table
   with a picker for the class name. His 17 engram overrides are this.
4. **Raw passthrough** - `extra_gameusersettings` and `extra_game` stay exactly as they
   are, for anything the model does not cover. They are the escape hatch and they are
   never removed.

Around that:

- **Grouped and searchable.** ~250 settings is unusable as a flat list. Categories from
  the table above, plus a filter box that matches label, key and help text.
- **"Changed from default" markers**, and a filter for *only* those - which is how you
  read someone else's cluster, and how the owner sees the 172 he actually set.
- **Descriptions** from the reference, not invented - including the ones where the name
  lies about the behaviour.
- **Per-map scoping** where ARK supports it. The store already does per-map overrides;
  the UI needs to show "cluster default 10.0, this map 15.0" rather than pretending each
  map is independent.

## Phasing

Highest impact first, and each phase ships working.

**Phase 0 - the safe write path.** INI parse/merge/write preserving unknown keys,
comments and mod sections; `.bak` on write; adoption of existing values into the store;
tests that take his real 172-key file, round-trip it, and assert byte-equality when
nothing changed. **Nothing ships until this passes** - it is the whole safety story.

**Phase 1 - the scalars people actually change.** Rates, breeding, difficulty, player
survival, structures, PvE rules. Maybe 80 settings, all plain fields, all covered by the
existing schema-driven renderer. This alone closes most of the owner's gap.

**Phase 2 - search, grouping, changed-from-default.** The navigation the count demands.

**Phase 3 - stat tables.** The 5×12 grids.

**Phase 4 - row editors.** Per-dino, per-resource, per-engram arrays.

**Phase 5 - per-map overrides in the UI**, with inheritance shown.

## What must not regress

- The 5 existing curated settings keep their keys, so nothing in the store breaks.
- `extra_gameusersettings` / `extra_game` keep working throughout.
- Every setting Obelisk does not model stays exactly as written, byte for byte.
