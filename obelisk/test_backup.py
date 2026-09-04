"""
Backups: portable, verified, pruned, scheduled.

The failure this suite is built around: an archive that exists, is the right shape, and
restores nothing. A backup is only a backup if it has been read back, so `create()` is
tested by opening what it wrote and looking for the worlds - and by deliberately
producing an empty root and checking that the attempt is *refused*, not filed.

Fixture values are synthetic throughout.
"""

import json, os, sys, tarfile, tempfile, time

from . import backup as backupctl
from . import layout
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


def fresh(maps="island,ragnarok", **over):
    """A store whose data root is a real temp dir.

    backup.py works on the root as a path, so unlike compose generation it does not
    need the POSIX-only validator - which lets these tests use a genuine filesystem.
    """
    base = tempfile.mkdtemp()
    root = os.path.join(base, "obelisk")          # Obelisk data: the definition
    ark = os.path.join(base, "ark")               # Ark data: the bulk
    os.makedirs(root, exist_ok=True)
    os.makedirs(ark, exist_ok=True)
    os.environ["OBELISK_ARK"] = ark
    st = Store(os.path.join(root, "settings.json")).load()
    st.patch({"status_port": 8088}, source="install")
    st.patch(dict({"maps": maps, "admin_password": "synthetic-pw",
                   "discord_token": "synthetic-token", "cluster_id": "testcluster",
                   "mod_ids": "929110,940003", "backup_keep": 3}, **over))
    # The host path is a separate fact from where we see it; only compose uses it.
    st.data["cluster"]["appdata"] = "/mnt/user/appdata/ark"
    st.save()            # the definition has to exist on disk to be backed up
    return st, ark


def populate(root, map_ids=("TheIsland_WP", "Ragnarok_WP"), size=2048):
    """Fill the Ark folder: saves, config, transfer data - and bulk that must NOT be
    backed up, so the exclusion is proved against something real."""
    layout.ensure_ark(root, ["island", "ragnarok"])
    os.makedirs(os.path.join(root, layout.SERVERFILES, "ShooterGame"), exist_ok=True)
    with open(os.path.join(root, layout.SERVERFILES, "ShooterGame", "big.bin"), "wb") as fh:
        fh.write(bytes(8192))
    os.makedirs(os.path.join(root, layout.MODS, "929110"), exist_ok=True)
    with open(os.path.join(root, layout.MODS, "929110", "mod.bin"), "wb") as fh:
        fh.write(bytes(4096))
    for mid in map_ids:
        d = os.path.join(root, "shared", "SavedArks", mid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, mid + ".ark"), "wb") as fh:
            fh.write(b"\0" * size)
        with open(os.path.join(d, "player.arkprofile"), "wb") as fh:
            fh.write(b"\0" * 64)
    with open(os.path.join(root, "shared", "Config", "GameUserSettings.ini"), "w") as fh:
        fh.write("[ServerSettings]\nXPMultiplier=3.0\n")
    os.makedirs(os.path.join(root, "cluster", "testcluster"), exist_ok=True)
    with open(os.path.join(root, "cluster", "testcluster", "transfer"), "w") as fh:
        fh.write("synthetic")


# ---------------------------------------------------------------- a real backup
st, root = fresh()
populate(root)
ok, msg, path = backupctl.create(st)
check("a populated root backs up", ok, msg)
check("it says what it captured", "2 maps" in msg and "verified readable" in msg, msg)
check("the archive is on disk", path and os.path.isfile(path), path)

names = tarfile.open(path, "r:gz").getnames()
check("both worlds are inside",
      any(n.endswith("shared/SavedArks/TheIsland_WP/TheIsland_WP.ark") for n in names) and
      any(n.endswith("shared/SavedArks/Ragnarok_WP/Ragnarok_WP.ark") for n in names), names[:8])
check("saves come from the Ark folder", any(n.startswith("ark/shared/") for n in names))
check("the definition comes from the Obelisk folder",
      any(n.startswith("obelisk/") for n in names), names[:8])
check("player data is inside", any(n.endswith(".arkprofile") for n in names))
check("the shared config is inside", any(n.endswith("GameUserSettings.ini") for n in names))
check("transfer data is inside", any("cluster/testcluster" in n for n in names))
check("Obelisk's own settings are inside",
      any(n.endswith("obelisk/settings.json") for n in names), names[:8])
check("the game install is NOT inside",
      not any(layout.SERVERFILES in n for n in names),
      [n for n in names if layout.SERVERFILES in n][:3])
check("the downloaded mods are NOT inside",
      not any(("/" + layout.MODS + "/") in n or n.endswith("/" + layout.MODS) for n in names),
      [n for n in names if layout.MODS in n][:3])
check("but the mod IDs that rebuild them are",
      json.loads(tarfile.open(path, "r:gz").extractfile("cluster-definition.json").read())
      ["mod_ids"] == "929110,940003")

# ---- the cluster definition: what the .ark files cannot tell you
defn = json.loads(tarfile.open(path, "r:gz").extractfile("cluster-definition.json").read())
check("the definition carries the mod list", defn["mod_ids"] == "929110,940003", defn["mod_ids"])
check("it records that mod order matters", defn["mod_order_matters"] is True)
check("it carries the cluster id", defn["cluster_id"] == "testcluster")
check("it carries the map selection", defn["maps"] == "island,ragnarok")
check("it carries ports and RAM",
      defn["settings"]["game_port_base"] and defn["settings"]["mem_limit"], defn["settings"])

# ---- secrets: named, never carried
blob = json.dumps(defn)
check("the admin password is NOT in the definition", "synthetic-pw" not in blob)
check("the Discord token is NOT in the definition", "synthetic-token" not in blob)
check("but the restore knows to ask for them",
      set(defn["secrets_required"]) >= {"admin_password", "discord_token"},
      defn["secrets_required"])

if os.name == "posix":
    check("the archive is owner-only", oct(os.stat(path).st_mode)[-3:] == "600",
          oct(os.stat(path).st_mode))
else:
    check("the archive is owner-only", True)          # NTFS has no POSIX mode bits

man = json.load(open(path + ".json"))
check("a manifest is written beside it", man["entries"] > 0 and man["contains_secrets"] is True)
check("the manifest names the secret keys without values",
      "admin_password" in man["secret_keys"] and "synthetic-pw" not in json.dumps(man))
check("the manifest says what was excluded", any("game install" in x for x in man["excluded"]))

# ---------------------------------------------------------------- the empty-backup bug
st2, root2 = fresh()
layout.ensure_ark(root2, ["island", "ragnarok"])      # layout exists, no worlds in it
ok2, msg2, path2 = backupctl.create(st2)
check("a root with no worlds is REFUSED, not filed as success", not ok2, msg2)
check("and says why in plain terms", "restore nothing" in msg2, msg2)
check("the unusable archive is not left behind",
      not any(n.endswith(".tar.gz") for n in os.listdir(backupctl.backups_dir(st2)))
      if os.path.isdir(backupctl.backups_dir(st2)) else True)

# a partially-started cluster is fine: some maps have saves, others never booted
st3, root3 = fresh()
populate(root3, map_ids=("TheIsland_WP",))
ok3, msg3, _p = backupctl.create(st3)
check("a half-started cluster still backs up", ok3, msg3)

# ---------------------------------------------------------------- flushing first
st4, root4 = fresh()
populate(root4)
calls = []
ok4, msg4, _p = backupctl.create(st4, flush=lambda: (calls.append(1) or (True, "saved 2")))
check("SaveWorld runs before the copy when asked", calls == [1], calls)
check("and the result says so", "Saved all maps first" in msg4, msg4)

ok5, msg5, _p = backupctl.create(st4, flush=lambda: (False, "map down"))
check("a failed flush does not stop the backup", ok5, msg5)
check("but it is reported honestly", "last autosave" in msg5, msg5)

ok6, msg6, _p = backupctl.create(st4, flush=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
check("a crashing flush still yields a backup", ok6, msg6)

# ---------------------------------------------------------------- retention
st7, root7 = fresh(backup_keep=3)
populate(root7)
for i in range(5):
    backupctl.create(st7, when=time.time() - (5 - i) * 3600)
rows = backupctl.listing(st7)
check("every backup is listed", len(rows) == 5, len(rows))
check("newest first", rows[0]["mtime"] >= rows[-1]["mtime"])
removed = backupctl.prune(st7)
after = backupctl.listing(st7)
check("prune keeps exactly the limit", len(after) == 3, len(after))
check("prune removed the oldest", len(removed) == 2, removed)
check("the survivors are the newest",
      [r["name"] for r in after] == [r["name"] for r in rows[:3]], after)
check("manifests go with them",
      not any(f.endswith(".json") and f[:-5] in removed
              for f in os.listdir(backupctl.backups_dir(st7))))
check("prune with keep=0 is a no-op, not a wipe", backupctl.prune(st7, keep=0) == [])

# a backup never contains its own backups folder
names7 = tarfile.open(backupctl.listing(st7)[0]["path"], "r:gz").getnames()
check("archives do not nest inside each other",
      not any("backups" in n.split("/") for n in names7), [n for n in names7 if "backup" in n][:3])

# two backups in the same second must not overwrite each other
st_u, root_u = fresh(backup_keep=10)
populate(root_u)
t = time.time()
backupctl.create(st_u, when=t)
backupctl.create(st_u, when=t)
check("a same-second backup does not replace the previous one",
      len(backupctl.listing(st_u)) == 2, backupctl.listing(st_u))

# ---------------------------------------------------------------- the schedule
st8, root8 = fresh(backup_times="")
populate(root8)
d, why = backupctl.due(st8)
check("no schedule means never due", not d and "no schedule" in why, why)

st8.patch({"backup_times": "04:00"})
now = time.mktime(time.strptime("2026-09-04 05:00", "%Y-%m-%d %H:%M"))
d, why = backupctl.due(st8, now=now, last=0)
check("a passed slot with no prior backup is due", d, why)
d, why = backupctl.due(st8, now=now, last=now - 1800)
check("a slot already covered is not due again", not d, why)
now_early = time.mktime(time.strptime("2026-09-04 03:00", "%Y-%m-%d %H:%M"))
d, why = backupctl.due(st8, now=now_early, last=0)
check("a slot still ahead is not due yet", not d, why)

st8.patch({"backup_times": "04:00,21:45"})
d, why = backupctl.due(st8, now=now, last=0)
check("multiple slots are honoured", d and "04:00" in why, why)

# run_scheduled does the whole job
st9, root9 = fresh(backup_keep=2)
populate(root9)
for i in range(3):
    backupctl.create(st9, when=time.time() - (3 - i) * 3600)
ok9, msg9 = backupctl.run_scheduled(st9)
check("a scheduled run backs up and prunes", ok9 and "Removed" in msg9, msg9)
check("and leaves exactly the retention limit", len(backupctl.listing(st9)) == 2,
      len(backupctl.listing(st9)))

# ---------------------------------------------------------------- missing root
# A store pointing at a root that does not exist: the container-side root now comes
# from where the store file is, so this is built by putting the store somewhere gone.
gone = os.path.join(tempfile.mkdtemp(), "nope", "obelisk")
st10 = Store(os.path.join(gone, "settings.json"))
st10.data = {"version": 1, "cluster": {"maps": "island"}, "maps": {}}
ok10, msg10, _p = backupctl.create(st10)
check("a missing data root is a clear refusal", not ok10 and "nothing to back up" in msg10, msg10)

print("\nFAILURES: %s" % fails if fails else "\nall backup tests passed")
sys.exit(1 if fails else 0)
