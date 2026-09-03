#!/usr/bin/env python3
"""
Offline tests for ark-runner.py. Runs anywhere with python3 - no Unraid, no docker,
no cluster. Redirects every path at a temp dir and stubs the player count.

    python3 unraid/test_ark_runner.py

Exits non-zero if anything fails. Worth re-running before any change to the
whitelist or the share-path validation, since that is the security boundary.
"""
# Fixture values here are deliberately synthetic - no real cluster's ids, times or
# timezone belong in a public repo. The one exception is mod id 929110: the product
# checks that specific public id to enforce stacking-mod load order, so a test of
# that rule has to use the real one.
import importlib.util, json, os, shutil, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("runner", os.path.join(HERE, "ark-runner.py"))
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

base = tempfile.mkdtemp()
r.APPDATA   = os.path.join(base, "appdata")
r.SHARED    = os.path.join(base, "Shared")
r.CMD_DIR   = os.path.join(r.SHARED, "commands")
r.RES_DIR   = os.path.join(r.SHARED, "results")
r.ADMIN_CFG = os.path.join(r.SHARED, "admin_config.json")
r.CFG_DIR   = os.path.join(r.SHARED, "Config")
r.COMPOSE_DIR = os.path.join(base, "project")
r.PROJECT    = "ark"
r.ENV_FILE  = os.path.join(r.COMPOSE_DIR, ".env")
share       = os.path.join(base, "mnt", "user", "backups")
r.SHARE_PREFIX = os.path.join(base, "mnt") + "/"
r.SHARE_DENY = (os.path.join(base, "mnt", "user0"),)
r.SETTINGS = [("env", r.ENV_FILE, 0o600),
              ("GameUserSettings.ini", os.path.join(r.CFG_DIR, "GameUserSettings.ini"), 0o644),
              ("Game.ini", os.path.join(r.CFG_DIR, "Game.ini"), 0o644)]
os.makedirs(r.COMPOSE_DIR); os.makedirs(r.CFG_DIR, exist_ok=True); os.makedirs(share)
open(r.ENV_FILE, "w").write("SESSION_PREFIX=ACME\nMOD_IDS=1,2,3\nSERVER_ADMIN_PASSWORD=secret\n")
open(os.path.join(r.CFG_DIR, "GameUserSettings.ini"), "w").write("[ServerSettings]\nXPMultiplier=10\n")

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)) if detail and not cond else ""))
    if not cond: fails.append(name)

for bad, why in [("", "empty"), ("/etc", "outside allowlist"),
                 (os.path.join(base, "mnt", "user", "..", "..", "etc"), "dotdot"),
                 (os.path.join(base, "mnt", "user", "nope"), "missing dir")]:
    ok, msg = r.check_share(bad); check("reject share (%s)" % why, not ok, msg)
ok, msg = r.check_share(share); check("accept real share", ok, msg)

# a pool can be called anything, so the rule is "under /mnt", not a fixed list
for name in ("cache", "zfs", "nvme_fast", "tank"):
    d2 = os.path.join(base, "mnt", name, "obelisk"); os.makedirs(d2, exist_ok=True)
    ok2, m2 = r.check_share(d2)
    check("accepts a pool named %r" % name, ok2, m2)
os.makedirs(os.path.join(base, "mnt", "user0", "x"), exist_ok=True)
ok3, m3 = r.check_share(os.path.join(base, "mnt", "user0", "x"))
check("refuses /mnt/user0 (bypasses the share layer)", not ok3, m3)
ok4, m4 = r.check_share(os.path.join(base, "mnt", "user"))
check("refuses a bare share root", not ok4, m4)

def queue(action, args=None, ts=None, cid=None):
    os.makedirs(r.CMD_DIR, exist_ok=True)
    cid = cid or ("c%d" % time.time_ns())
    json.dump({"id": cid, "action": action, "args": args or {}, "ts": ts or time.time()},
              open(os.path.join(r.CMD_DIR, cid + ".json"), "w"))
    return cid
def result(cid):
    return json.load(open(os.path.join(r.RES_DIR, cid + ".json")))

c1 = queue("ping"); c2 = queue("rm -rf /"); c3 = queue("ping", ts=time.time() - 99999)
os.makedirs(r.CMD_DIR, exist_ok=True)
open(os.path.join(r.CMD_DIR, "junk.json"), "w").write("{not json")
r.main()
check("ping ran", result(c1)["ok"], result(c1))
check("non-whitelisted action refused", not result(c2)["ok"], result(c2)["output"])
check("stale command refused", not result(c3)["ok"], result(c3)["output"])
check("unreadable command handled", not result("junk")["ok"], result("junk")["output"])
check("command files consumed", os.listdir(r.CMD_DIR) == [], os.listdir(r.CMD_DIR))

c = queue("validate_share", {"path": share}); r.main()
check("validate_share saves path", result(c)["ok"], result(c)["output"])
c = queue("settings_push"); r.main()
check("settings_push ok", result(c)["ok"], result(c)["output"])
latest = os.path.join(share, "ark-settings", "latest")
check("env landed on share", os.path.isfile(os.path.join(latest, "env")))
check("env is 0600 on share", oct(os.stat(os.path.join(latest, "env")).st_mode)[-3:] == "600")
check("Game.ini absence tolerated", "Game.ini" in result(c)["output"])

open(r.ENV_FILE, "w").write("SESSION_PREFIX=BROKEN\nMOD_IDS=9\nSERVER_ADMIN_PASSWORD=x\n")
c = queue("settings_pull"); r.main()
check("settings_pull ok", result(c)["ok"], result(c)["output"])
check("live env restored", "ACME" in open(r.ENV_FILE).read())
check("previous env backed up", any(f.startswith(".env.bak.") for f in os.listdir(r.COMPOSE_DIR)))

open(os.path.join(latest, "env"), "w").write("SESSION_PREFIX=ACME\n")
before = open(r.ENV_FILE).read()
c = queue("settings_pull"); r.main()
check("pull refuses incomplete env", not result(c)["ok"], result(c)["output"])
check("live env untouched after refusal", open(r.ENV_FILE).read() == before)

r.players_online = lambda: 3
c = queue("recreate_all"); r.main()
check("recreate_all blocked with players on", not result(c)["ok"], result(c)["output"])
r.known_maps = lambda: ["island", "center"]
c = queue("restart_map", {"map": "nope"}); r.main()
check("restart_map rejects unknown map", not result(c)["ok"], result(c)["output"])
r.players_online = lambda: None
c = queue("recreate_all"); r.main()
check("recreate_all blocked when count unknown", not result(c)["ok"], result(c)["output"])

# --------------------------------------------------------------- verify_mods
# The three layers that can disagree: what settings ask for, what the server was
# actually launched with, and what made it onto disk.

def fake_docker(ps_names, env_mods, disk_ids):
    """Installs of a normal, healthy size - the stub case has its own fake below."""
    def _run(args, timeout=300):
        if args[:3] == ["docker", "ps", "--format"]:
            return 0, " ".join(ps_names)
        if len(args) > 4 and args[1] == "exec" and args[3:] == ["printenv", "MOD_IDS"]:
            return 0, env_mods + "\n"
        if len(args) > 3 and args[1] == "exec" and args[3] == "sh":
            return 0, "\n".join("%s_555%d 7 3000" % (m, n) for n, m in enumerate(disk_ids))
        return 0, ""
    return _run

def set_env_mods(ids):
    open(r.ENV_FILE, "w").write("SESSION_PREFIX=ACME\nMOD_IDS=%s\n"
                                "SERVER_ADMIN_PASSWORD=secret\n" % ids)

_real_run = r.run

set_env_mods("929110,222222,444444")
r.run = fake_docker(["asa_island", "obelisk"], "929110,222222,444444", ["929110", "222222", "444444"])
c = queue("verify_mods"); r.main()
check("verify_mods passes when everything agrees", result(c)["ok"], result(c)["output"])
check("verify_mods reports what it compared", "settings ask for" in result(c)["output"])

# the exact failure this cluster hit: order changed in settings but never applied
r.run = fake_docker(["asa_island"], "929110,333333,222222", ["929110", "333333", "222222"])
set_env_mods("929110,222222,333333")
c = queue("verify_mods"); r.main()
out = result(c)["output"]
check("catches an unapplied order change", not result(c)["ok"] and "ORDER DIFFERS" in out, out)

# a mod that never finished downloading - loaded on the launch line, absent on disk
set_env_mods("929110,222222")
r.run = fake_docker(["asa_island"], "929110,222222", ["929110"])
c = queue("verify_mods"); r.main()
out = result(c)["output"]
check("catches a mod missing from disk", not result(c)["ok"] and "MISSING FROM DISK: 222222" in out, out)

# stacking mod demoted - silently stops working
set_env_mods("222222,929110")
r.run = fake_docker(["asa_island"], "222222,929110", ["222222", "929110"])
c = queue("verify_mods"); r.main()
out = result(c)["output"]
check("catches a demoted stacking mod", not result(c)["ok"] and "LOAD ORDER" in out, out)

# leftovers from a removed mod are reported but not treated as a failure cause
set_env_mods("929110")
r.run = fake_docker(["asa_island"], "929110", ["929110", "888888"])
c = queue("verify_mods"); r.main()
check("notices leftover mod folders", "ON DISK BUT NOT LOADED: 888888" in result(c)["output"],
      result(c)["output"])

# nothing running - say so rather than reporting a false all-clear
r.run = fake_docker(["obelisk"], "", [])
c = queue("verify_mods"); r.main()
check("no false all-clear when nothing is running",
      not result(c)["ok"] and "no map containers" in result(c)["output"], result(c)["output"])

# ---- presence is not health: a stub install must be caught
def fake_docker_sized(ps_names, env_mods, sized):
    """sized: {mod_id: (files, kb)}"""
    def _run(args, timeout=300):
        if args[:3] == ["docker", "ps", "--format"]:
            return 0, " ".join(ps_names)
        if len(args) > 4 and args[1] == "exec" and args[3:] == ["printenv", "MOD_IDS"]:
            return 0, env_mods + "\n"
        if len(args) > 3 and args[1] == "exec" and args[3] == "sh":
            return 0, "\n".join("%s_555%d %d %d" % (m, i, f, kb)
                                 for i, (m, (f, kb)) in enumerate(sized.items()))
        return 0, ""
    return _run

healthy = {"929110": (6, 3000), "222222": (8, 4000), "333333": (8, 2000),
           "444444": (6, 1500)}
set_env_mods("929110,222222,333333,444444")
r.run = fake_docker_sized(["asa_island"], "929110,222222,333333,444444", healthy)
c = queue("verify_mods"); r.main()
check("healthy installs pass", result(c)["ok"], result(c)["output"])
check("sizes are reported", "install sizes:" in result(c)["output"])

stub = dict(healthy); stub["333333"] = (2, 900)          # folder there, content isn't
r.run = fake_docker_sized(["asa_island"], "929110,222222,333333,444444", stub)
c = queue("verify_mods"); r.main()
out = result(c)["output"]
check("a stub install is caught even though the folder exists",
      not result(c)["ok"] and "LOOKS INSTALLED BUT ISN'T: 333333" in out, out)
check("and it is flagged in the size table", "suspiciously small" in out, out)
check("the message explains the silent-failure shape", "no error anywhere" in out)

# with too few mods to compare against, don't cry wolf
few = {"929110": (6, 3000), "222222": (2, 900)}
set_env_mods("929110,222222")
r.run = fake_docker_sized(["asa_island"], "929110,222222", few)
c = queue("verify_mods"); r.main()
check("no stub guesswork with too small a sample", result(c)["ok"], result(c)["output"])

# ---- redownload_mod
r.run = fake_docker_sized(["asa_island"], "929110", healthy)
c = queue("redownload_mod", {"mod": "222222"}); r.main()
check("refuses to delete while servers are running",
      not result(c)["ok"] and "stop the cluster first" in result(c)["output"], result(c)["output"])
r.run = fake_docker_sized(["obelisk"], "929110", healthy)
c = queue("redownload_mod", {"mod": "not-a-number"}); r.main()
check("refuses a non-numeric mod id", not result(c)["ok"], result(c)["output"])
import os as _os
modroot = _os.path.join(r.APPDATA, "ServerFiles", "arkserver", "ShooterGame", "Binaries",
                        "Win64", "ShooterGame", "Mods", "83374")
_os.makedirs(modroot, exist_ok=True)

c = queue("redownload_mod", {"mod": "999999"}); r.main()
check("says so when there is nothing to clear",
      not result(c)["ok"] and "nothing to clear" in result(c)["output"], result(c)["output"])

_os.makedirs(_os.path.join(modroot, "222222_5551"), exist_ok=True)
open(_os.path.join(modroot, "222222_5551", "x.pak"), "w").write("x")
c = queue("redownload_mod", {"mod": "222222"}); r.main()
check("clears the install when the cluster is stopped", result(c)["ok"], result(c)["output"])
check("the folder is actually gone", not _os.path.isdir(_os.path.join(modroot, "222222_5551")))
check("and it says what to do next", "download it again" in result(c)["output"])

r.run = _real_run

print("\nFAILURES:", fails if fails else "none")
shutil.rmtree(base)
sys.exit(1 if fails else 0)
