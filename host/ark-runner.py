#!/usr/bin/env python3
"""
Host-side command runner for an Obelisk cluster admin page.

The Obelisk container has no docker socket and cannot touch /boot on purpose -
it holds the Discord token and talks to the internet. So the admin page never
executes anything itself: it drops a JSON command into Shared/commands/ and this
script, running on the Unraid host from a 1-minute User Scripts job, executes a
fixed whitelist and writes the outcome back to Shared/results/.

The whitelist below is the security boundary. Nothing outside it can run, and no
value from a command file is ever passed to a shell.
"""

import json, os, re, shutil, subprocess, sys, time, datetime

# Everything is relative to one path so this works on any install. Override with
#   OBELISK_APPDATA=/mnt/user/appdata/ark   OBELISK_PROJECT=ark
APPDATA  = os.environ.get("OBELISK_APPDATA", "/mnt/user/appdata/ark").rstrip("/")
PROJECT  = os.environ.get("OBELISK_PROJECT", "ark")
SHARED   = os.path.join(APPDATA, "Shared")
CMD_DIR  = os.path.join(SHARED, "commands")
RES_DIR  = os.path.join(SHARED, "results")
ADMIN_CFG= os.path.join(SHARED, "admin_config.json")

COMPOSE_DIR = os.environ.get("OBELISK_COMPOSE_DIR",
              "/boot/config/plugins/compose.manager/projects/" + PROJECT)
ENV_FILE = os.path.join(COMPOSE_DIR, ".env")
CFG_DIR  = os.path.join(SHARED, "Config")
STATUS   = os.environ.get("OBELISK_STATUS_URL", "http://localhost:8088/api/status")

MAX_PER_TICK = 8
CMD_MAX_AGE  = 600          # ignore anything older than 10 min - stale queue, not a backlog

# Files that count as "custom settings" and move as a set.
SETTINGS = [
    ("env",                   ENV_FILE,                                 0o600),
    ("GameUserSettings.ini",  os.path.join(CFG_DIR, "GameUserSettings.ini"), 0o644),
    ("Game.ini",              os.path.join(CFG_DIR, "Game.ini"),        0o644),
]

# A share path must sit somewhere under /mnt - that covers Unraid shares
# (/mnt/user), remote mounts (/mnt/remotes), unassigned disks (/mnt/disks) and any
# named cache or ZFS pool, which is whatever the operator called it rather than
# something we can list up front. Everything outside /mnt is refused outright.
SHARE_PREFIX = "/mnt/"

# /mnt/user0 bypasses Unraid's share layer. Writing there is a well-known way to
# lose data, so it is refused even though it is under /mnt.
SHARE_DENY = ("/mnt/user0",)

def known_maps():
    """Whatever this cluster actually runs, read from the running containers -
    so the runner never needs a hardcoded map list to stay in step with."""
    rc, out = run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=30)
    return sorted(n[4:] for n in out.split() if n.startswith("asa_"))


def run(args, timeout=300):
    """Always a list, never a shell string - nothing from a command file can inject."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout
    except Exception as e:
        return 1, "failed to run: %s" % e


def players_online():
    """Returns int, or None when the status page can't be reached."""
    rc, out = run(["curl", "-s", "-m", "6", STATUS], timeout=15)
    if rc != 0:
        return None
    try:
        return int(json.loads(out).get("total"))
    except Exception:
        return None


def load_cfg():
    try:
        with open(ADMIN_CFG) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_cfg(cfg):
    tmp = ADMIN_CFG + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, ADMIN_CFG)
    os.chmod(ADMIN_CFG, 0o644)


def check_share(path):
    """Validate a user-supplied share path. Returns (ok, message)."""
    if not path or not isinstance(path, str):
        return False, "no path given"
    if "\x00" in path or ".." in path.split("/"):
        return False, "path contains .. or a null byte"
    path = os.path.normpath(path)
    if path == SHARE_PREFIX.rstrip("/") or not path.startswith(SHARE_PREFIX):
        return False, ("must be a folder under /mnt - an Unraid share (/mnt/user/...), "
                       "a named pool, or a remote you have mounted with Unassigned "
                       "Devices (/mnt/remotes/...)")
    if path == SHARE_DENY[0] or path.startswith(SHARE_DENY[0] + "/"):
        return False, ("/mnt/user0 bypasses Unraid's share layer and writing there can "
                       "lose data - use /mnt/user instead")
    # Depth is measured from the prefix, not from /, so this holds however
    # SHARE_PREFIX is configured: <pool>/<folder> is the shallowest we accept.
    rel = path[len(SHARE_PREFIX):].strip("/")
    if "/" not in rel:
        return False, ("point at a folder inside the share, not the share root itself "
                       "- e.g. /mnt/user/backups/obelisk")
    if not os.path.isdir(path):
        return False, "not a directory (or the remote share isn't mounted right now)"
    probe = os.path.join(path, ".ark_write_test")
    try:
        with open(probe, "w") as fh:
            fh.write("ok")
        os.unlink(probe)
    except Exception as e:
        return False, "not writable: %s" % e
    return True, "ok"


def settings_root(cfg):
    ok, msg = check_share(cfg.get("share_path", ""))
    if not ok:
        return None, msg
    return os.path.join(os.path.normpath(cfg["share_path"]), "ark-settings"), "ok"


# ----------------------------------------------------------------- actions

def a_ping(args, cfg):
    return True, "pong from %s at %s" % (os.uname().nodename,
                                         datetime.datetime.now().strftime("%H:%M:%S"))


def a_validate_share(args, cfg):
    path = args.get("path") or cfg.get("share_path", "")
    ok, msg = check_share(path)
    if ok:
        cfg["share_path"] = os.path.normpath(path)
        save_cfg(cfg)
        return True, "share OK and saved: %s" % cfg["share_path"]
    return False, msg


def a_settings_push(args, cfg):
    """Live settings -> share. Snapshot by timestamp, plus a 'latest' copy."""
    root, msg = settings_root(cfg)
    if not root:
        return False, msg
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    snap = os.path.join(root, "snapshots", stamp)
    latest = os.path.join(root, "latest")
    os.makedirs(snap, exist_ok=True)
    os.makedirs(latest, exist_ok=True)
    copied, missing = [], []
    for name, src, mode in SETTINGS:
        if not os.path.isfile(src):
            missing.append(name)
            continue
        for dest_dir in (snap, latest):
            dest = os.path.join(dest_dir, name)
            shutil.copy2(src, dest)
            os.chmod(dest, mode)
        copied.append(name)
    note = "pushed %s -> %s" % (", ".join(copied) or "nothing", snap)
    if missing:
        note += " (not present on server: %s)" % ", ".join(missing)
    note += "\nNOTE: the env copy holds your Discord token and admin password - keep this share private."
    return True, note


def a_settings_pull(args, cfg):
    """Share -> live settings. Backs up what it replaces; never auto-restarts."""
    root, msg = settings_root(cfg)
    if not root:
        return False, msg
    src_dir = os.path.join(root, args["snapshot"]) if args.get("snapshot") else os.path.join(root, "latest")
    if not os.path.isdir(src_dir):
        return False, "no such snapshot on the share: %s" % src_dir
    # Refuse an env that doesn't look like ours - a truncated or wrong file here
    # would take the whole cluster down on the next recreate.
    env_src = os.path.join(src_dir, "env")
    if os.path.isfile(env_src):
        txt = open(env_src, encoding="utf-8", errors="replace").read()
        required = ["SESSION_PREFIX", "MOD_IDS", "SERVER_ADMIN_PASSWORD"]
        absent = [k for k in required if not re.search(r"^%s=" % k, txt, re.M)]
        if absent:
            return False, "refusing to apply: env on the share is missing %s" % ", ".join(absent)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    applied = []
    for name, dest, mode in SETTINGS:
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            continue
        if os.path.isfile(dest):
            shutil.copy2(dest, "%s.bak.%s" % (dest, stamp))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        os.chmod(dest, mode)
        applied.append(name)
    if not applied:
        return False, "snapshot contained none of the settings files"
    return True, ("applied %s from %s (previous versions saved as *.bak.%s)\n"
                  "Not live yet - use Recreate cluster to apply."
                  % (", ".join(applied), src_dir, stamp))


def a_list_snapshots(args, cfg):
    root, msg = settings_root(cfg)
    if not root:
        return False, msg
    snaps = os.path.join(root, "snapshots")
    if not os.path.isdir(snaps):
        return True, "no snapshots yet"
    names = sorted(os.listdir(snaps), reverse=True)[:25]
    return True, "\n".join(names) if names else "no snapshots yet"


def a_restart_map(args, cfg):
    m = str(args.get("map", "")).lower()
    known = known_maps()
    if m not in known:
        return False, "unknown map %r - this cluster runs: %s" % (m, ", ".join(known) or "none")
    if not args.get("force") and (players_online() or 0) > 0:
        return False, "players are online - re-send with force to restart anyway"
    rc, out = run(["docker", "restart", "asa_" + m], timeout=180)
    return rc == 0, out.strip() or ("restarted asa_" + m)


def a_recreate_all(args, cfg):
    """Full down/up. Needed after any change to .env or mod order."""
    n = players_online()
    if not args.get("force"):
        if n is None:
            return False, "can't read the player count - re-send with force if you're sure"
        if n > 0:
            return False, "%d player(s) online - re-send with force to restart anyway" % n
    rc1, out1 = run(["docker", "compose", "-p", PROJECT, "down"], timeout=300)
    rc2, out2 = run(["docker", "compose", "-p", PROJECT, "up", "-d"], timeout=600)
    return rc2 == 0, (out1[-600:] + "\n" + out2[-900:]).strip()


def a_update_core(args, cfg):
    """Pull the published Obelisk image and recreate just that container."""
    rc1, out1 = run(["docker", "compose", "-p", PROJECT, "pull", "obelisk"], timeout=600)
    rc2, out2 = run(["docker", "compose", "-p", PROJECT, "up", "-d",
                     "--no-deps", "obelisk"], timeout=300)
    return rc2 == 0, (out1[-500:] + "\n" + out2[-500:]).strip()


# Mods are the part of an ARK cluster most likely to be quietly wrong: the server
# downloads them itself, logs nothing useful about it, and load ORDER decides which
# mod wins a conflicting remap. So check all three layers and say which one disagrees.
STACKING_MODS = {"929110"}          # stack-size mods must load first or later mods win


def _mods_from_env_file():
    try:
        for line in open(ENV_FILE, encoding="utf-8", errors="replace"):
            if line.strip().startswith("MOD_IDS="):
                return [m.strip() for m in line.split("=", 1)[1].strip().split(",") if m.strip()]
    except Exception:
        pass
    return []


def _first_running_map():
    rc, out = run(["docker", "ps", "--format", "{{.Names}}"], timeout=30)
    for n in out.split():
        if n.startswith("asa_"):
            return n
    return None


def a_verify_mods(args, cfg):
    """Compare what settings ask for, what a map is actually running, and what is on disk."""
    want = _mods_from_env_file()
    container = args.get("container") or _first_running_map()
    if not container:
        return False, "no map containers are running, so there is nothing to check against"

    rc, out = run(["docker", "exec", container, "printenv", "MOD_IDS"], timeout=60)
    running = [m.strip() for m in out.strip().split(",") if m.strip()] if rc == 0 else []

    # Mod folders are named <modid>_<fileid> under the game's own Mods tree. Find them
    # rather than assuming the path, which changes between game versions.
    rc2, out2 = run(["docker", "exec", container, "sh", "-c",
                     "find /home/pok/arkserver/ShooterGame/Binaries -maxdepth 6 -type d "
                     "-name '[0-9]*_[0-9]*' 2>/dev/null"], timeout=120)
    on_disk = []
    for line in out2.split():
        base = line.rsplit("/", 1)[-1]
        mid = base.split("_", 1)[0]
        if mid.isdigit() and mid not in on_disk:
            on_disk.append(mid)

    lines = ["checked against %s" % container,
             "  settings ask for : %s" % (", ".join(want) or "(none)"),
             "  actually running : %s" % (", ".join(running) or "(none)"),
             "  present on disk  : %s" % (", ".join(sorted(on_disk)) or "(none)"),
             ""]
    problems = []

    if want and running and want != running:
        if set(want) == set(running):
            problems.append("ORDER DIFFERS between settings and the running server. Order decides "
                            "which mod wins a conflicting remap, so this changes behaviour. "
                            "Recreate the cluster to apply.")
        else:
            problems.append("NOT APPLIED - settings and the running server disagree on which mods "
                            "to load. Recreate the cluster to apply.")

    missing = [m for m in (running or want) if m not in on_disk]
    if missing:
        problems.append("MISSING FROM DISK: %s. The server never finished downloading these, so "
                        "they are not loaded no matter what the launch line says. Restart that map "
                        "and watch it, or check the mod still exists on CurseForge."
                        % ", ".join(missing))

    extra = [m for m in on_disk if running and m not in running]
    if extra:
        problems.append("ON DISK BUT NOT LOADED: %s. Harmless, just leftovers from a mod you "
                        "removed - they take up space until cleaned up." % ", ".join(extra))

    order_list = running or want
    stackers = [m for m in order_list if m in STACKING_MODS]
    if stackers and order_list and order_list[0] not in STACKING_MODS:
        problems.append("LOAD ORDER: stacking mod %s is not first. Later mods override its stack "
                        "sizes, so stacking silently stops working." % stackers[0])

    if problems:
        return False, "\n".join(lines + problems)
    return True, "\n".join(lines + ["OK - %d mod(s), settings and server agree, all present on disk."
                                    % len(order_list)])


ACTIONS = {
    "ping":            a_ping,
    "validate_share":  a_validate_share,
    "settings_push":   a_settings_push,
    "settings_pull":   a_settings_pull,
    "list_snapshots":  a_list_snapshots,
    "verify_mods":     a_verify_mods,
    "restart_map":     a_restart_map,
    "recreate_all":    a_recreate_all,
    "update_core":     a_update_core,
}


def main():
    os.makedirs(CMD_DIR, exist_ok=True)
    os.makedirs(RES_DIR, exist_ok=True)
    os.chmod(CMD_DIR, 0o777)          # the container writes here as uid 7777
    os.chmod(RES_DIR, 0o755)
    cfg = load_cfg()

    files = sorted(f for f in os.listdir(CMD_DIR) if f.endswith(".json"))
    for fname in files[:MAX_PER_TICK]:
        path = os.path.join(CMD_DIR, fname)
        cid = fname[:-5]
        try:
            with open(path) as fh:
                cmd = json.load(fh)
        except Exception as e:
            cmd = None
        try:
            os.unlink(path)           # one attempt per command, always
        except Exception:
            pass
        if cmd is None:
            result = (False, "unreadable command file")
        elif time.time() - float(cmd.get("ts", 0)) > CMD_MAX_AGE:
            result = (False, "command was stale (older than %ds) - not run" % CMD_MAX_AGE)
        else:
            fn = ACTIONS.get(cmd.get("action"))
            if not fn:
                result = (False, "action %r is not on the whitelist" % cmd.get("action"))
            else:
                try:
                    result = fn(cmd.get("args") or {}, cfg)
                except Exception as e:
                    result = (False, "runner error: %s" % e)
        ok, output = result
        out = {"id": cid, "action": (cmd or {}).get("action"), "ok": bool(ok),
               "output": str(output)[:8000], "ts": int(time.time())}
        tmp = os.path.join(RES_DIR, cid + ".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(out, fh)
        os.replace(tmp, os.path.join(RES_DIR, cid + ".json"))
        os.chmod(os.path.join(RES_DIR, cid + ".json"), 0o644)

    # keep the results dir from growing forever
    res = sorted(os.listdir(RES_DIR))
    for old in res[:-200]:
        try:
            os.unlink(os.path.join(RES_DIR, old))
        except Exception:
            pass


if __name__ == "__main__":
    main()
