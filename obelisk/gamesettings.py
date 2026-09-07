"""
The game's own settings, as things you can edit.

ARK keeps its configuration in two INI files with a couple of hundred keys between
them. Obelisk used to model five. This is the rest of the scalars - the toggles and
numbers - each one pointed at the exact file, section and key it lives in, so the admin
page can render it from the schema like every other setting and the writer in ini.py can
put it back without touching anything around it.

Two deliberate limits.

**The catalogue is what this cluster actually runs.** Every entry here was read from a
real GameUserSettings.ini and Game.ini rather than transcribed from a wiki, so none of
these keys is invented and none of them is misspelt - a misspelt key is not an error in
ARK, it is a line the engine ignores for ever. Settings that exist in the game but are
not in this list can be added the same way once they are verified the same way; until
then the Extra*.ini passthrough covers them.

**Defaults here are placeholders, not the game's defaults.** Obelisk adopts the real
value from the INI on startup, so what you see in the UI is what your server is actually
running. The `default` field only decides what a key shows when it is in neither the
store nor the file, and marking "changed from default" honestly needs the real defaults,
which is Phase 2's job. Nothing is written for a key the operator has not set.
"""

import re

# (file, section, key, type, group) - read from a live cluster, not transcribed.
CATALOGUE = [
    ("GameUserSettings", "ServerSettings", "ItemStackSizeMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "OverrideOfficialDifficulty", "float", "Difficulty"),
    ("GameUserSettings", "ServerSettings", "DifficultyOffset", "float", "Difficulty"),
    ("GameUserSettings", "ServerSettings", "DinoCountMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "PlayerCharacterWaterDrainMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "PlayerCharacterFoodDrainMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "DinoCharacterStaminaDrainMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "UseOptimizedHarvestingHealth", "bool", "Rates"),
    ("GameUserSettings", "ServerSettings", "ClampResourceHarvestDamage", "bool", "Rates"),
    ("GameUserSettings", "ServerSettings", "ClampItemSpoilingTimes", "bool", "Items"),
    ("GameUserSettings", "ServerSettings", "ServerPVE", "bool", "PvE & PvP"),
    ("GameUserSettings", "ServerSettings", "ServerHardcore", "bool", "PvE & PvP"),
    ("GameUserSettings", "ServerSettings", "AllowCaveBuildingPvE", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "EnableExtraStructurePreventionVolumes", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "PreventOfflinePvP", "bool", "PvE & PvP"),
    ("GameUserSettings", "ServerSettings", "PreventTribeAlliances", "bool", "Tribes"),
    ("GameUserSettings", "ServerSettings", "PreventDiseases", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "NonPermanentDiseases", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "PreventSpawnAnimations", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "EnableCryoSicknessPVE", "bool", "PvE & PvP"),
    ("GameUserSettings", "ServerSettings", "AllowTekSuitPowersInGenesis", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "MaxHexagonsPerCharacter", "int", "Players"),
    ("GameUserSettings", "ServerSettings", "DisableWeatherFog", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "TheMaxStructuresInRange", "int", "Structures"),
    ("GameUserSettings", "ServerSettings", "PvEStructureDecayPeriodMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "PvPStructureDecay", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "OverrideStructurePlatformPrevention", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "ForceAllStructureLocking", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "OnlyAutoDestroyCoreStructures", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "OnlyDecayUnsnappedCoreStructures", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "FastDecayUnsnappedCoreStructures", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "DestroyUnconnectedWaterPipes", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "AlwaysAllowStructurePickup", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "StructurePickupTimeAfterPlacement", "float", "Structures"),
    ("GameUserSettings", "ServerSettings", "StructurePickupHoldDuration", "float", "Structures"),
    ("GameUserSettings", "ServerSettings", "AllowIntegratedSPlusStructures", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "AllowCrateSpawnsOnTopOfStructures", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "StructurePreventResourceRadiusMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "PerPlatformMaxStructuresMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "PlatformSaddleBuildAreaBoundsMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "MaxGateFrameOnSaddles", "int", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "DisableDinoDecayPvE", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "PvPDinoDecay", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "AutoDestroyDecayedDinos", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "PvEDinoDecayPeriodMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "MaxTamedDinos", "int", "Dinos"),
    ("GameUserSettings", "ServerSettings", "MaxPersonalTamedDinos", "int", "Rates"),
    ("GameUserSettings", "ServerSettings", "PersonalTamedDinosSaddleStructureCost", "int", "Structures"),
    ("GameUserSettings", "ServerSettings", "bForceCanRideFliers", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "AllowFlyerCarryPVE", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "AllowRaidDinoFeeding", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "AllowFlyingStaminaRecovery", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "DisableImprintDinoBuff", "bool", "Breeding"),
    ("GameUserSettings", "ServerSettings", "AllowAnyoneBabyImprintCuddle", "bool", "Breeding"),
    ("GameUserSettings", "ServerSettings", "RaidDinoCharacterFoodDrainMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "OxygenSwimSpeedStatMultiplier", "float", "Rates"),
    ("GameUserSettings", "ServerSettings", "ServerCrosshair", "bool", "Players"),
    ("GameUserSettings", "ServerSettings", "ServerForceNoHud", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "AllowThirdPersonPlayer", "bool", "Players"),
    ("GameUserSettings", "ServerSettings", "ShowMapPlayerLocation", "bool", "Players"),
    ("GameUserSettings", "ServerSettings", "EnablePVPGamma", "bool", "PvE & PvP"),
    ("GameUserSettings", "ServerSettings", "DisablePvEGamma", "bool", "PvE & PvP"),
    ("GameUserSettings", "ServerSettings", "ShowFloatingDamageText", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "AllowHitMarkers", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "globalVoiceChat", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "proximityChat", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "alwaysNotifyPlayerLeft", "bool", "Players"),
    ("GameUserSettings", "ServerSettings", "alwaysNotifyPlayerJoined", "bool", "Players"),
    ("GameUserSettings", "ServerSettings", "AllowSharedConnections", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "AllowHideDamageSourceFromLogs", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "TribeNameChangeCooldown", "float", "Tribes"),
    ("GameUserSettings", "ServerSettings", "TribeLogDestroyedEnemyStructures", "bool", "Structures"),
    ("GameUserSettings", "ServerSettings", "AdminLogging", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "KickIdlePlayersPeriod", "int", "Players"),
    ("GameUserSettings", "ServerSettings", "AutoSavePeriodMinutes", "float", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "RCONEnabled", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "RCONServerGameLogBuffer", "float", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "NoTributeDownloads", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "PreventDownloadSurvivors", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "PreventDownloadItems", "bool", "Items"),
    ("GameUserSettings", "ServerSettings", "PreventDownloadDinos", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "PreventUploadSurvivors", "bool", "Server behaviour"),
    ("GameUserSettings", "ServerSettings", "PreventUploadItems", "bool", "Items"),
    ("GameUserSettings", "ServerSettings", "PreventUploadDinos", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "CrossARKAllowForeignDinoDownloads", "bool", "Dinos"),
    ("GameUserSettings", "ServerSettings", "TributeItemExpirationSeconds", "int", "Rates"),
    ("GameUserSettings", "ServerSettings", "TributeDinoExpirationSeconds", "int", "Rates"),
    ("GameUserSettings", "ServerSettings", "TributeCharacterExpirationSeconds", "int", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "MaxTribeLogs", "int", "Tribes"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "MaxNumberOfPlayersInTribe", "int", "Players"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "MaxAlliancesPerTribe", "int", "Tribes"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "MaxTribesPerAlliance", "int", "Tribes"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bPvEAllowTribeWar", "bool", "PvE & PvP"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bPvEAllowTribeWarCancel", "bool", "PvE & PvP"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bDisableFriendlyFire", "bool", "PvE & PvP"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bPvEDisableFriendlyFire", "bool", "PvE & PvP"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bIncreasePvPRespawnInterval", "bool", "Players"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bAutoPvETimer", "bool", "PvE & PvP"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "GenericXPMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "CraftXPMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "HarvestXPMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "KillXPMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "SpecialXPMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "MatingSpeedMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "MatingIntervalMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "EggHatchSpeedMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "BabyCuddleIntervalMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "BabyCuddleGracePeriodMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "BabyCuddleLoseImprintQualitySpeedMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "BabyFoodConsumptionSpeedMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "LayEggIntervalMultiplier", "float", "Breeding"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bDisableLootCrates", "bool", "Items"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "RandomSupplyCratePoints", "bool", "Items"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "SupplyCrateLootQualityMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "FishingLootQualityMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bDisableDefaultMapItemSets", "bool", "Items"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bAllowCustomRecipes", "bool", "Server behaviour"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bUseCorpseLocator", "bool", "Players"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bAllowUnlimitedRespecs", "bool", "Server behaviour"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bAllowPlatformSaddleMultiFloors", "bool", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bDisableStructurePlacementCollision", "bool", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bFlyerPlatformAllowUnalignedDinoBasing", "bool", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bPassiveDefensesDamageRiderlessDinos", "bool", "Dinos"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bLimitTurretsInRange", "bool", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bHardLimitTurretsInRange", "bool", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bGenesisUseStructuresPreventionVolumes", "bool", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bDisableGenesisMissions", "bool", "Server behaviour"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bDisableWorldBuffs", "bool", "Server behaviour"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bEnableWorldBuffScaling", "bool", "Server behaviour"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "BaseHexagonRewardMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "HexagonCostMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "StructureDamageRepairCooldown", "int", "Structures"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "PvPZoneStructureDamageMultiplier", "float", "Rates"),
    ("Game", "/Script/ShooterGame.ShooterGameMode", "bAllowFlyerSpeedLeveling", "bool", "Dinos"),
]

# Written where the name does not say it, or says it misleadingly. Everything else gets
# its file and section quoted instead, which is at least true.
HELP = {
    "ServerPVE": "PvE mode: players cannot damage each other or each other's things.",
    "ServerHardcore": "Death resets the survivor to level 1.",
    "OverrideOfficialDifficulty": "The real max wild level: 6.0 gives level 180 dinos, "
                                  "against 5.0 for official 150s.",
    "DifficultyOffset": "Scales wild levels within whatever the override allows. Leave "
                        "at 1.0 unless you know why you are moving it.",
    "DinoCountMultiplier": "How many wild creatures spawn. Raising this costs RAM and "
                           "CPU on every map at once.",
    "ItemStackSizeMultiplier": "Multiplies every stack limit. The single biggest "
                               "quality-of-life setting on a modded cluster.",
    "AllowCaveBuildingPvE": "Whether players may build inside caves in PvE.",
    "PreventOfflinePvP": "Protects a tribe's things while none of them are online.",
    "PreventTribeAlliances": "Stops tribes formally allying.",
    "PreventDiseases": "Turns off Swamp Fever and the rest permanently.",
    "EnableExtraStructurePreventionVolumes": "Blocks building in the areas Wildcard "
                                             "marks off, artifact caves included.",
    "ClampResourceHarvestDamage": "Caps how much a single hit can take off a resource, "
                                  "which stops high-multiplier setups one-shotting nodes.",
    "ClampItemSpoilingTimes": "Caps spoil timers so mods cannot push them absurdly high.",
    "UseOptimizedHarvestingHealth": "Cheaper harvesting maths. Changes yields slightly.",
    "PvEDinoDecayPeriodMultiplier": "How long an unclaimed tame survives before it can "
                                    "be claimed by anyone.",
    "PvEStructureDecayPeriodMultiplier": "How long structures last untouched before they "
                                         "start to decay.",
    "MatingIntervalMultiplier": "Time between breedings. Lower is faster - this one is "
                                "the wrong way round from most.",
    "EggHatchSpeedMultiplier": "How fast fertilised eggs hatch. Higher is faster.",
    "BabyCuddleIntervalMultiplier": "How often a baby wants imprinting. Lower means more "
                                    "frequent, which means more imprint per raise.",
    "BabyImprintingStatScaleMultiplier": "How much each imprint is worth.",
    "BabyFoodConsumptionSpeedMultiplier": "How fast babies eat. Raising maturation "
                                          "without raising this starves them.",
    "AllowFlyerCarryPvE": "Whether flyers may pick up wild and tamed creatures in PvE.",
    "AllowRaidDinoFeeding": "Whether titanosaurs and the like can be permanently fed.",
    "PerPlatformMaxStructuresMultiplier": "Structure limit on platform saddles and rafts.",
    "StructurePreventResourceRadiusMultiplier": "How far a building stops resources "
                                                "respawning around it.",
    "TheMaxStructuresInRange": "The hard cap on structures near one point. Big bases hit "
                               "this before they hit anything else.",
    "MaxTamedDinos": "Cluster-wide tame cap. Reached, it stops taming entirely.",
    "AutoSavePeriodMinutes": "Minutes between automatic world saves. This is the window "
                             "a crash can cost you.",
    "KickIdlePlayersPeriod": "Seconds before an idle player is disconnected.",
}


def _label(key):
    """A readable name from a key: bAllowFlyerCarryPvE -> Allow flyer carry PvE."""
    k = re.sub(r"^b(?=[A-Z])", "", key)
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z']*|\d+", k) or [k]
    text = " ".join(words).replace("Pv E", "PvE").replace("Pv P", "PvP")
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------------------------
# The game's own defaults, for the settings where they are actually documented.
#
# Sourced from the ARK server configuration reference, not from this cluster and not
# from the server image's template - the image ships an opinionated starting config
# (ServerCrosshair=True where the game's default is False, OverrideOfficialDifficulty at
# 5.024775) and reading defaults out of it would call half a cluster "changed" when it
# is running exactly what the image gave it.
#
# It covers 46 of the 142 settings in the catalogue. The rest are genuinely not documented anywhere I could
# verify, and a guessed default is worse than none: it puts a confident "changed from
# default" badge on a setting nobody touched. Where the default is unknown, nothing is
# claimed - see default_known below.
DEFAULTS = {
    "AdminLogging": "False",
    "AllowAnyoneBabyImprintCuddle": "False",
    "AllowCaveBuildingPvE": "False",
    "AllowCaveBuildingPvP": "True",
    "AllowCrateSpawnsOnTopOfStructures": "False",
    "AllowCryoFridgeOnSaddle": "False",
    "AllowFlyerCarryPvE": "False",
    "AllowFlyingStaminaRecovery": "False",
    "AllowHideDamageSourceFromLogs": "True",
    "AllowHitMarkers": "True",
    "AllowIntegratedSPlusStructures": "True",
    "AllowMultipleAttachedC4": "False",
    "AllowRaidDinoFeeding": "False",
    "AllowSharedConnections": "False",
    "AllowTekSuitPowersInGenesis": "False",
    "AllowThirdPersonPlayer": "True",
    "AlwaysAllowStructurePickup": "False",
    "AlwaysNotifyPlayerLeft": "False",
    "ArmadoggoDeathCooldown": "3600",
    "AutoDestroyDecayedDinos": "False",
    "AutoDestroyOldStructuresMultiplier": "0.0",
    "AutoSavePeriodMinutes": "15.0",
    "ClampItemSpoilingTimes": "False",
    "ClampItemStats": "False",
    "ClampResourceHarvestDamage": "False",
    "DayCycleSpeedScale": "1.0",
    "DinoCharacterFoodDrainMultiplier": "1.0",
    "DinoCharacterHealthRecoveryMultiplier": "1.0",
    "DinoCharacterStaminaDrainMultiplier": "1.0",
    "DinoCountMultiplier": "1.0",
    "DinoDamageMultiplier": "1.0",
    "DinoResistanceMultiplier": "1.0",
    "DisableStructureDecayPVE": "False",
    "DodoResistanceMultiplier": "1.0",
    "DumpAdminLog": "False",
    "EnableExtraStructurePreventionVolumes": "False",
    "EnablePvPGamma": "True",
    "FallDamageMultiplier": "1.0",
    "FlyerPlatformAllowUnalignedDinoBasing": "False",
    "GlobalVoiceChat": "True",
    "HarvestAmountMultiplier": "1.0",
    "HarvestHealthMultiplier": "1.0",
    "ItemStackSizeMultiplier": "1.0",
    "KickIdlePlayersPeriod": "3600.0",
    "MaxNumberOfPlayersInTribe": "70",
    "MaxStructuresInRange": "135",
    "NonPvPDinoDamageMultiplier": "1.0",
    "NonPvPStructureDamageMultiplier": "1.0",
    "PlayerCharacterFoodDrainMultiplier": "1.0",
    "PlayerCharacterHealthRecoveryMultiplier": "1.0",
    "PlayerCharacterStaminaDrainMultiplier": "1.0",
    "PlayerCharacterWaterDrainMultiplier": "1.0",
    "PlayerDamageMultiplier": "1.0",
    "PlayerResistanceMultiplier": "1.0",
    "PreventDiseases": "False",
    "PreventDownloadDinos": "False",
    "PreventDownloadItems": "False",
    "PreventDownloadSurvivors": "False",
    "PreventOfflinePvP": "False",
    "PreventTribeAlliances": "False",
    "PreventUploadDinos": "False",
    "PreventUploadItems": "False",
    "PreventUploadSurvivors": "False",
    "PvEAllowStructuresAtSupplyDrops": "False",
    "PvPDinoDecay": "True",
    "PvPStructureDecay": "True",
    "ResourcesRespawnPeriodMultiplier": "1.0",
    "ServerCrosshair": "False",
    "ServerHardcore": "False",
    "ServerPVE": "False",
    "ShowMapPlayerLocation": "True",
    "StructureDamageMultiplier": "1.0",
    "StructurePickupHoldDuration": "0.5",
    "StructurePickupTimeAfterPlacement": "30.0",
    "StructureResistanceMultiplier": "1.0",
    "TamingSpeedMultiplier": "1.0",
    "XPMultiplier": "1.0",
}

_DEFAULTS_LOWER = {k.lower(): v for k, v in DEFAULTS.items()}


def documented_default(key, kind):
    """The game's default for this key, typed - or None when nobody documents one.

    Matched without case, because the files disagree with the reference about it:
    AllowFlyerCarryPVE and AllowFlyerCarryPvE are the same setting, and the engine
    does not care which you wrote.
    """
    raw = _DEFAULTS_LOWER.get(key.lower())
    if raw is None:
        return None
    try:
        if kind == "bool":
            return raw.strip().lower() == "true"
        if kind == "int":
            return int(float(raw))
        if kind == "float":
            return float(raw)
    except (TypeError, ValueError):
        return None
    return raw


_DEFAULTS = {"bool": False, "int": 0, "float": 1.0}


def settings():
    """The catalogue as schema entries, ready to be rendered and validated."""
    out = []
    for f, section, key, kind, group in CATALOGUE:
        real = documented_default(key, kind)
        out.append(dict(
            key=key, label=_label(key), group=group, type=kind,
            # A placeholder still fills the form when the real default is unknown, but
            # default_known is what decides whether anything is allowed to say the value
            # has been "changed".
            default=_DEFAULTS[kind] if real is None else real,
            default_known=real is not None,
            target="ini:%s:%s:%s" % (f, section, key),
            apply="reload", per_map=True,
            help=HELP.get(key, "%s.ini [%s] %s" % (f, section, key))))
    return out


GROUPS = ["Rates", "Breeding", "Dinos", "Players", "Structures", "Items",
          "PvE & PvP", "Tribes", "Difficulty", "Server behaviour"]


# ---------------------------------------------------------------------------------
# Per-level stat multipliers: five families, twelve stats each.
#
# In the file these are PerLevelStatsMultiplier_Player[7]=3.0 - the stat is an index
# inside the key, which is why Phase 1 swallowed eight of them as settings literally
# named "PerLevelStatsMultiplier_Player[7]". They are a grid, and they need to be
# edited as one.
#
# The index table is the game's, confirmed against two independent references before
# anything was labelled with it. It matters: index 7 is Weight and index 8 is Melee
# Damage, so this operator raising Player[7] and DinoTamed[8] is the ordinary thing a
# 10x cluster does. An earlier fetch returned a reordered table that would have labelled
# DinoTamed[8] "Crafting Speed" - a stat a dinosaur does not have - and printing that
# next to his value would have been worse than printing nothing.
STATS = [
    (0, "Health"), (1, "Stamina"), (2, "Torpidity"), (3, "Oxygen"),
    (4, "Food"), (5, "Water"), (6, "Temperature"), (7, "Weight"),
    (8, "Melee Damage"), (9, "Speed"), (10, "Fortitude"), (11, "Crafting Speed"),
]

GAME_MODE = "/Script/ShooterGame.ShooterGameMode"

# (family key, human name, what it does)
STAT_FAMILIES = [
    ("PerLevelStatsMultiplier_Player", "Players",
     "How much each stat gains per level for survivors."),
    ("PerLevelStatsMultiplier_DinoTamed", "Tamed creatures",
     "Applied to a tame's stats after taming, on top of what it was born with."),
    ("PerLevelStatsMultiplier_DinoTamed_Add", "Tamed - taming bonus",
     "The flat bonus a creature gets from being tamed, before levelling."),
    ("PerLevelStatsMultiplier_DinoTamed_Affinity", "Tamed - affinity bonus",
     "The bonus scaled by taming effectiveness - the reward for a perfect tame."),
    ("PerLevelStatsMultiplier_DinoWild", "Wild creatures",
     "How much each stat gains per level on wild creatures."),
]


# ---------------------------------------------------------------------------------
# Open-ended arrays: settings written as the same key repeated, one line per row.
#
# OverrideNamedEngramEntries is the one this cluster actually uses, seventeen times.
# Its five fields are the game's, confirmed against the configuration reference, and
# they match the file exactly. Columns are still taken from what each row contains, so
# a row that only sets two fields keeps only two - the reference decides what may be
# offered, never what gets written.
#
# The deeply nested arrays are deliberately absent. ConfigOverrideSupplyCrateItems and
# ConfigOverrideItemCraftingCosts nest tuples inside tuples inside lists, and a row
# editor that flattened them would be guessing at a shape it cannot round-trip. They
# stay in the Extra*.ini passthrough, where they work today and cannot be damaged.
ROW_ARRAYS = [
    dict(key="OverrideNamedEngramEntries", file="Game", section=GAME_MODE,
         label="Engram overrides", group="Engrams",
         help="One row per engram: hide it, change what it costs, or change the level "
              "it unlocks at. A row only writes the fields you fill in.",
         fields=[("EngramClassName", "Engram class", "text"),
                 ("EngramHidden", "Hidden", "bool"),
                 ("EngramPointsCost", "Points cost", "int"),
                 ("EngramLevelRequirement", "Level required", "int"),
                 ("RemoveEngramPreReq", "Drop prerequisites", "bool")]),
    dict(key="EngramEntryAutoUnlocks", file="Game", section=GAME_MODE,
         label="Auto-unlocked engrams", group="Engrams",
         help="Engrams handed out automatically at a level.",
         fields=[("EngramClassName", "Engram class", "text"),
                 ("LevelToAutoUnlock", "Unlocks at level", "int")]),
]

ROW_BY_KEY = {a["key"]: a for a in ROW_ARRAYS}
