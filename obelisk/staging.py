"""
The staging server: a real ARK server whose only job is to go first.

steamcmd can fetch the game. It cannot fetch mods - the ASA binary does that itself,
through CurseForge, at startup, and no amount of pre-downloading reaches them. So the
only thing that can pre-fetch a mod is a server that boots with it.

That turns out to be worth much more than the download. A server that boots the new
build with all the cluster's mods has *proved* the combination loads: it queries
CurseForge, states each mod valid, and prints the exact `<projectId>_<fileId>` it
loaded. So the same instance that primes an update also verifies it, before ten live
maps are asked to restart into it.

Everything below exists to keep that instance from touching the cluster it is
rehearsing for. It is not a smaller member of the cluster; it is a separate thing that
happens to run the same image:

  * **Its own ServerFiles.** The whole point. The migration's hard lesson was never to
    rewrite files a running server is using, and the staged build is a second full tree
    that the live maps never see until an apply swaps it in.

  * **Its own shared folder.** This one is not optional and is the easiest to get
    wrong. The server image deletes and recreates `Saved/Config/*.ini` and
    `SavedArks/<Map>` as symlinks into /home/pok/shared at every start. Point staging at
    the cluster's shared folder and it does not merely read the live config - it
    symlinks its throwaway world onto a *live map's save* and writes to it. A staging
    server sharing that folder is a staging server editing the cluster.

  * **No cluster folder and no published ports.** Nothing to transfer into, nothing for
    a player to reach. Whatever the server browser decides to show, there is no route.

  * **Outside the backup set.** It lives under paths that are not in ARK_PORTABLE, so
    the portable backup keeps ignoring it without being taught to.
"""

import logging
import os

from . import arkupdate, layout, naming
from . import maps as mapcat

log = logging.getLogger("obelisk.staging")

INSTANCE = "staging"

# The staged tree, beside the live one and never inside it.
STAGED = "ServerFiles.staging"
PREVIOUS = "ServerFiles.previous"
STAGING_ROOT = "staging"          # its own shared/ and instance/, outside ARK_PORTABLE

# Directories that describe *the live cluster* rather than the game files, and must
# therefore stay put when the trees are swapped. `.pok-manager` holds update notices,
# `update_coordination` holds the per-instance envs and cycle state the ten maps use to
# take turns, and `instance_flags` holds their dirty flags. Carrying the staging
# server's copies of these into the live tree would hand the cluster a coordination
# state describing a server that is not in it.
LIVE_ONLY = (".pok-manager", "update_coordination", "instance_flags")

MODES = ("off", "on_demand", "always")

# How long to let the staging server shut itself down before it is killed. The live maps
# get 210 seconds because their worlds are irreplaceable; this one is regenerated every
# rehearsal, so waiting out a save verification that has already failed buys nothing.
STOP_TIMEOUT = 30


def paths(ark_root):
    """Every path the staging server owns. Nothing here is shared with the cluster."""
    root = str(ark_root).rstrip("/")
    return {
        "staged": "%s/%s" % (root, STAGED),
        "previous": "%s/%s" % (root, PREVIOUS),
        "live": layout.ark_paths(root)["serverfiles"],
        "instance": "%s/%s/instance/Saved" % (root, STAGING_ROOT),
        "shared": "%s/%s/shared" % (root, STAGING_ROOT),
    }


def mode(store):
    value = str(store.get("staging_mode") or "off").strip().lower()
    return value if value in MODES else "off"


def enabled(store):
    return mode(store) != "off"


def project_name(project):
    """A compose project of its own, so the cluster's lifecycle never moves it.

    Sharing the cluster's project would mean `docker compose up` starts the staging
    server as an eleventh map and every status, stop and restart would need to remember
    to leave it out. A separate project is one fact instead of a rule applied in a dozen
    places - and the apply flow has to stop it separately anyway.
    """
    return "%s-staging" % project


def container_name(project):
    """asa-<cluster>-staging.

    Named from the *cluster* project rather than the staging one: the staging compose
    project is already `<cluster>-staging`, so building the container name from it too
    produced `asa-tbgcluster-staging-staging`. No map is ever called "staging", so this
    cannot collide with one.
    """
    return naming.container_name(project, INSTANCE)


def map_id(store):
    """Which map it rehearses on. Any map proves the mods - they are fetched from the
    command line, not from the world - so this is only a question of what is cheapest."""
    key = str(store.get("staging_map") or "scorched").strip()
    entry = mapcat.BY_KEY.get(key) or mapcat.BY_KEY["scorched"]
    return entry["map_id"], entry["key"]


def compose_text(store, project, ark_host_root):
    """The staging stack, as plain YAML for the same reason the cluster's is.

    Deliberately not a service in the cluster's file: see project_name().
    """
    p = paths(ark_host_root)
    mid, key = map_id(store)
    net = "%s-net" % project
    prefix = str(store.get("session_prefix") or "Obelisk").strip()

    return "\n".join([
        "# Generated by Obelisk - do not edit.",
        "# The staging server: primes and verifies an update before the cluster takes it.",
        "# Not player-facing. No published ports, no cluster folder, its own server files.",
        "services:",
        "  %s:" % INSTANCE,
        "    image: %s" % store.get("ark_image"),
        "    container_name: %s" % container_name(project),
        "    restart: %s" % ("unless-stopped" if mode(store) == "always" else "no"),
        # Short on purpose - see down(). A throwaway world is not worth
        # three and a half minutes of Docker grace.
        "    stop_grace_period: %ds" % STOP_TIMEOUT,
        "    mem_limit: %s" % (store.get("staging_memory") or "10g"),
        "    networks: [%s]" % net,
        "    ulimits:",
        "      nofile: {soft: 1000000, hard: 1000000}",
        "    environment:",
        "      TZ: %s" % _q(store.get("timezone")),
        "      MAP_NAME: %s" % _q(mid),
        "      INSTANCE_NAME: %s" % _q(INSTANCE),
        # Its own one-instance cluster, so POK's coordination has nothing to wait for
        # and it fetches a new build the moment one appears.
        "      UPDATE_COORDINATION_ROLE: \"MASTER\"",
        "      UPDATE_COORDINATION_PRIORITY: \"1\"",
        "      UPDATE_SERVER: \"TRUE\"",
        "      CHECK_FOR_UPDATE_INTERVAL: \"1\"",
        "      API: \"FALSE\"",
        "      SESSION_NAME: %s" % _q("%s STAGING - not a game server" % prefix),
        "      ASA_PORT: \"7777\"",
        "      RCON_PORT: \"27020\"",
        "      RCON_ENABLED: \"TRUE\"",
        "      SERVER_ADMIN_PASSWORD: %s" % _q(store.get("admin_password")),
        # One slot, and it is not a cap anyone will meet: nothing can reach it.
        "      MAX_PLAYERS: \"1\"",
        # A cluster id of its own. Sharing the live one would list a server that cannot
        # be transferred to and has no cluster folder to transfer with.
        "      CLUSTER_ID: %s" % _q("%s-staging" % store.get("cluster_id")),
        # The whole reason this instance exists.
        "      MOD_IDS: %s" % _q(store.get("mod_ids")),
        "      PASSIVE_MODS: %s" % _q(store.get("passive_mods")),
        "      BATTLEEYE: \"FALSE\"",
        "      ENABLE_MOTD: \"FALSE\"",
        "      RANDOM_STARTUP_DELAY: \"FALSE\"",
        "      DISPLAY_POK_MONITOR_MESSAGE: \"FALSE\"",
        "      SHOW_ADMIN_COMMANDS_IN_CHAT: \"FALSE\"",
        "      CPU_OPTIMIZATION: \"FALSE\"",
        "      BACKUP_DIR: /home/pok/shared",
        # ---- no ports block, on purpose
        #
        # Obelisk reaches it over the cluster network for RCON and health. Publishing
        # would put a server nobody should join on the host's interface, and the only
        # thing that would gain is the ability to join it.
        "    volumes:",
        '      - "%s:/home/pok/arkserver"' % p["staged"],
        '      - "%s:/home/pok/arkserver/ShooterGame/Saved"' % p["instance"],
        # Its own shared folder. See the module docstring - pointing this at the
        # cluster's would symlink a throwaway world onto a live map's save.
        '      - "%s:/home/pok/shared"' % p["shared"],
        "",
        "networks:",
        "  %s:" % net,
        "    name: %s" % net,
        "    external: true",
        "",
    ])


def _q(v):
    s = "" if v is None else str(v)
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def ensure(ark_root, makedirs=None, chown=None):
    """Create the staging folders, owned by the server's user. Safe to repeat.

    Every folder, not every folder that got named. This got written once already with
    only the ends of each path listed, and it failed on a live host in two places at
    once - which is the third time this exact trap has been walked into, so the list
    below is spelled out rather than derived:

      * `staging/instance` is created on the way to `staging/instance/Saved`. makedirs
        makes intermediates and chown does not follow them, so naming only the leaf left
        the parent owned by root. That is word for word the bug ensure_ark already
        carries a comment about.

      * `ServerFiles.staging/ShooterGame` is the one that actually broke it. The bind
        mount's *destination* is inside the staged tree - ShooterGame/Saved - and Docker
        creates a missing destination as root. So ShooterGame ended up root-owned inside
        a tree the server otherwise owned, steamcmd (running as 7777) could not create
        ShooterGame/Binaries beside it, and the install aborted with "Permission denied"
        after downloading 325 MB. Making the destination here, before the container is
        ever started, is what stops Docker inventing it.
    """
    makedirs = makedirs or (lambda p: os.makedirs(p, exist_ok=True))
    p = paths(ark_root)
    root = str(ark_root).rstrip("/")
    made = [
        p["staged"],
        "%s/ShooterGame" % p["staged"],
        "%s/ShooterGame/Saved" % p["staged"],
        "%s/%s" % (root, STAGING_ROOT),
        os.path.dirname(p["instance"]),
        p["instance"],
        p["shared"],
    ]
    for path in made:
        makedirs(path)
    layout.give_to_server(made, chown=chown)
    return made


# ---------------------------------------------------------------- lifecycle

COMPOSE_NAME = "compose.staging.yml"


def compose_file(store):
    """Beside the cluster's, in whichever folder that one lives in."""
    from . import cluster, layout as _layout, stack
    if stack.available():
        return stack.compose_file(project_name(cluster.project(store)))
    return "%s/%s" % (_layout.root_of(store), COMPOSE_NAME)


def write_compose(store, ark_host_root=None):
    """Generate the staging stack and put it beside the cluster's. (path, text)."""
    from . import cluster
    root = ark_host_root or str(store.get("appdata")).rstrip("/")
    text = compose_text(store, cluster.project(store), root)
    path = compose_file(store)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    log.info("wrote %s (%d bytes)", path, len(text))
    return path, text


def _compose(store, *args, timeout=900):
    from . import cluster, dockerctl
    return dockerctl.compose(compose_file(store),
                             project_name(cluster.project(store)),
                             list(args), timeout=timeout)


def up(store):
    """Start the staging server, writing its compose file first. (ok, message).

    Always regenerated: the mod list is the reason this instance exists, so starting it
    from a file written before the last mod change would rehearse the wrong cluster.
    """
    from . import cluster, dockerctl
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    if not enabled(store):
        return False, "the staging server is turned off"
    try:
        ensure(cluster.layout.ark_root_of(store))
        write_compose(store)
    except OSError as e:
        return False, "could not prepare the staging folders: %s" % e
    # --remove-orphans so a container this project no longer names is taken
    # away with it, rather than sitting exited in the Docker page forever.
    rc, out = _compose(store, "up", "-d", "--remove-orphans", timeout=900)
    if rc != 0:
        return False, "could not start the staging server: %s" % out[-500:]
    return True, "the staging server is starting"


def down(store, timeout=STOP_TIMEOUT):
    """Stop it, promptly. Its staged files are kept; its world is not worth waiting for.

    The image will not let a container exit until it has verified a two-stage SaveWorld
    over RCON, and refuses with "PID 1 will remain alive until Docker's grace period
    expires" when that fails. For the ten live maps that is exactly right - those worlds
    are the thing being protected. For this one it is not: the staging server has no
    players, and its world is a throwaway generated fresh for the rehearsal. So a failed
    prime sat there for three and a half minutes proving a save nobody wanted.

    Hence a short grace period and, if it is still there afterwards, a kill. Refusing to
    force it would mean the most likely time to need a clean-up - a rehearsal that went
    wrong, where RCON is exactly what did not work - is the time it takes longest.
    """
    from . import cluster, dockerctl
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    if not os.path.isfile(compose_file(store)):
        return True, "the staging server was not running"

    rc, out = _compose(store, "down", "--timeout", str(int(timeout)),
                       timeout=timeout + 120)
    name = container_name(cluster.project(store))
    still_here, _ = is_running(store)
    if rc == 0 and not still_here:
        return True, "the staging server is stopped"

    log.info("the staging server did not stop cleanly (%s) - removing it",
             (out or "").strip()[-200:] or "no output")
    rc2, out2 = dockerctl._run(["docker", "rm", "-f", name], timeout=90)
    if rc2 != 0 and "No such container" not in (out2 or ""):
        return False, "could not remove the staging server: %s" % (out2 or "")[-300:]
    return True, ("the staging server was force-stopped - its world is a throwaway, so "
                  "nothing was lost")


def is_running(store, details=None):
    from . import cluster, dockerctl
    details = details or dockerctl.container_details
    name = container_name(cluster.project(store))
    got = (details([name]) or {}).get(name, {})
    return got.get("state") == "running", got


def container_log(store, tail=400, logs=None):
    """POK's own output for the staging container.

    Separate from boot_log() because they cover different halves of a start and only one
    of them exists at a time. Everything before the game launches - the download, the
    file sync, permission failures - is here; ShooterGame.log does not exist yet. Reading
    only the game log meant a staging server that died during install looked exactly like
    one that was still downloading, and the watch sat there for its full timeout.
    """
    from . import cluster, dockerctl
    logs = logs or dockerctl.logs
    try:
        return logs(container_name(cluster.project(store)), tail=tail) or ""
    except Exception:                             # noqa: BLE001 - absence is a state
        return ""


def boot_log(store, ark_root=None, read=None, listdir=None):
    """The staging server's own ShooterGame.log, which is where the proof lives.

    Docker's log is POK's wrapper chatter; the mod verdicts are written by the game into
    its instance folder. Newest log wins - a server that has restarted has written a
    fresh one, and reading the stale one would grade the previous boot.
    """
    from . import cluster
    root = ark_root or cluster.layout.ark_root_of(store)
    logs = "%s/Logs" % paths(root)["instance"]
    listdir = listdir or os.listdir
    read = read or (lambda p: open(p, encoding="utf-8", errors="replace").read())
    try:
        names = [n for n in listdir(logs) if n == "ShooterGame.log"]
    except OSError:
        return ""
    if not names:
        return ""
    try:
        return read("%s/%s" % (logs, "ShooterGame.log"))
    except OSError:
        return ""


# ---------------------------------------------------------------- the verdict

def verify(staged_root, boot_log, mod_ids, rcon_ok, target_build=None, read=None):
    """Did the staging boot actually prove anything? (ok, problems, detail).

    Four independent gates, because each one passes in a case the others fail. A
    container can be healthy with the world never loaded; a world can load with the mods
    missing; the mods can load off a build that is not the one being staged; and all
    three can be true of a server that answers nothing.
    """
    problems, detail = [], {}

    build, build_problem = arkupdate.installed_build(staged_root, read=read)
    detail["build"] = build
    if not build:
        problems.append("the staged tree has no readable build id: %s" % build_problem)
    elif target_build and str(build) != str(target_build):
        problems.append("the staged build is %s but %s was being staged"
                        % (build, target_build))

    mods_ok, mod_problems, mod_detail = arkupdate.read_boot_log(boot_log, mod_ids)
    detail["loaded"] = mod_detail.get("loaded", {})
    detail["fetched"] = mod_detail.get("fetched", {})
    if not mods_ok:
        problems += mod_problems

    if not rcon_ok:
        problems.append("the staging server never answered RCON, so its world never "
                        "finished loading")

    return (not problems), problems, detail


def summary(result):
    """One sentence for #ark-admin and the panel. Says what was proved, or what was not."""
    if not result:
        return "nothing has been staged yet"
    if result.get("ok"):
        loaded = result.get("loaded") or {}
        return ("build %s and %d mod(s) booted cleanly on the staging server"
                % (result.get("build") or "?", len(loaded)))
    problems = result.get("problems") or []
    return ("the staged update did not come up cleanly: %s"
            % ("; ".join(problems[:3]) or "no reason recorded"))


# ---------------------------------------------------------------- the swap

def swap_steps(ark_root):
    """The renames that make the staged tree live, in order. Nothing is executed here.

    A rename rather than a copy. POK's own sync is `cp -f` file by file over 12 GB of
    live install - minutes of downtime, and because it only ever adds and overwrites, a
    file removed upstream is left behind forever. Every map is stopped during an apply
    anyway, so the trees can simply change places: seconds instead of minutes, the
    previous build kept intact as the rollback, and the tree that steps aside becomes
    the next staging tree so the following prime is a delta rather than 12 GB again.

    Then the three coordination folders are exchanged back, because they belong to the
    cluster and not to the files - after the trees change places the live tree is
    holding the *staging server's* copies, which describe a server the cluster has never
    heard of. Exchanged rather than deleted and recreated: nothing here removes anything,
    so a step that fails half way leaves both trees complete.

    Returned as data so the dangerous part can be read, reviewed and simulated without
    anything moving.
    """
    p = paths(ark_root)
    steps = [
        {"op": "rename", "src": p["live"], "dst": p["previous"], "optional": False},
        {"op": "rename", "src": p["staged"], "dst": p["live"], "optional": False},
        {"op": "rename", "src": p["previous"], "dst": p["staged"], "optional": False},
    ]
    for name in LIVE_ONLY:
        mine = "%s/%s" % (p["live"], name)          # staging's, now in the live tree
        theirs = "%s/%s" % (p["staged"], name)      # the cluster's, now in the staged tree
        hold = "%s/%s.swapping" % (p["live"], name)
        # Optional throughout: a tree that has never run has none of these yet, and a
        # missing folder is a state rather than a failure.
        steps += [
            {"op": "rename", "src": mine, "dst": hold, "optional": True},
            {"op": "rename", "src": theirs, "dst": mine, "optional": True},
            {"op": "rename", "src": hold, "dst": theirs, "optional": True},
        ]
    return steps


def rollback_steps(ark_root):
    """The same steps again, which is exactly what puts the previous build back.

    Every step is an exchange, so the sequence is its own inverse. There is no separate
    rollback path to get wrong, and no rollback that can only be tested by needing it.
    """
    return swap_steps(ark_root)


def apply_steps(steps, rename=None, exists=None):
    """Run a plan from swap_steps(). (ok, done, problem).

    `done` is the steps that actually happened, so a caller that fails part way can put
    them back by reversing it rather than guessing at what moved.
    """
    rename = rename or os.rename
    exists = exists or os.path.exists

    # Every required source that is not produced by an earlier step is checked before
    # anything moves. Without this the first rename happens and *then* the missing
    # staged tree is discovered - the live install renamed out of the way for an update
    # that was never downloaded. The cheapest possible check, guarding the most
    # expensive possible mistake.
    produced = set()
    for step in steps:
        if not step["optional"] and step["src"] not in produced and not exists(step["src"]):
            return False, [], ("%s is missing, so the swap cannot start" % step["src"])
        produced.add(step["dst"])

    done = []
    for step in steps:
        src, dst = step["src"], step["dst"]
        if not exists(src):
            if step["optional"]:
                continue
            return False, done, "%s is missing, so the swap cannot start" % src
        try:
            rename(src, dst)
        except OSError as e:
            return False, done, "could not move %s to %s: %s" % (src, dst, e)
        done.append(step)
    return True, done, ""


def undo(done, rename=None, exists=None):
    """Put back exactly what apply_steps() moved, newest first."""
    rename = rename or os.rename
    exists = exists or os.path.exists
    for step in reversed(done):
        if exists(step["dst"]):
            try:
                rename(step["dst"], step["src"])
            except OSError as e:
                return False, "could not undo %s: %s" % (step["dst"], e)
    return True, ""
