"""
Creating and running the cluster.

Obelisk has always been able to describe a cluster. This is where it builds one: lay
out the data root, write the compose file, and drive the stack through the mounted
Docker socket.

Two rules shape everything here.

The compose file is written to disk inside the data root rather than piped to `docker
compose -f -`. If Obelisk is stopped, broken or uninstalled, the cluster is still a
plain compose file next to its data that anyone can run by hand. Nothing about the
running cluster depends on Obelisk continuing to exist.

Nothing is destroyed. `down` stops containers and leaves volumes, saves and the data
root alone - the containers are the disposable part. Removing worlds is not something
a button here does by accident.
"""

import logging, os

from . import dockerctl, layout
from .compose import generate_compose
from .plan import build_plan

log = logging.getLogger("obelisk.cluster")

COMPOSE_NAME = "compose.yaml"


def project(store):
    """The compose project name. Keyed to the cluster id so two clusters on one host
    stay separate, and stable across restarts so `up` adopts what is already there."""
    cid = str(store.get("cluster_id") or "").strip()
    return cid or "ark"


def compose_path(store):
    return "%s/%s" % (layout.root_of(store), COMPOSE_NAME)


def prepare(store, run=None):
    """Create the data root layout for the selected maps. Safe to repeat."""
    keys = _map_keys(store)
    made = layout.ensure_obelisk(layout.root_of(store))
    made += layout.ensure_ark(layout.ark_root_of(store), keys)
    log.info("folders ready: settings in %s, ark data in %s (host path %s, %d dirs)",
             layout.root_of(store), layout.ark_root_of(store),
             store.get("appdata"), len(made))
    return made


def write_compose(store, in_use_ports=None):
    """Generate the compose file and put it in the data root. Returns (path, text).

    Raises ValueError if the cluster would not boot - a bad plan never reaches disk.
    """
    text = generate_compose(store, project=project(store), in_use_ports=in_use_ports)
    path = compose_path(store)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    log.info("wrote %s (%d bytes)", path, len(text))
    return path, text


def _map_keys(store):
    raw = store.get("maps")
    if isinstance(raw, str):
        return [k.strip() for k in raw.split(",") if k.strip()]
    return list(raw or ())


def _compose(store, *args, timeout=900):
    """Run one `docker compose` command against this cluster's file."""
    return dockerctl.compose(compose_path(store), project(store), list(args),
                             timeout=timeout)


def launch(store, in_use_ports=None):
    """Bring the cluster up. Returns (ok, message).

    Everything that can be checked before touching Docker is checked first: the plan
    has to be bootable, the socket has to be reachable, and the data root has to exist.
    A half-created cluster is worse than a refusal with a reason.
    """
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable, so nothing was started. %s" % why

    plan = build_plan(store, in_use_ports=in_use_ports)
    if not plan["ok"]:
        return False, "This cluster won't start yet: " + "; ".join(plan["problems"])

    prepare(store)
    try:
        path, _text = write_compose(store, in_use_ports=in_use_ports)
    except ValueError as e:
        return False, str(e)

    rc, out = _compose(store, "up", "-d", "--remove-orphans")
    if rc != 0:
        return False, "docker compose up failed:\n%s" % out[-1500:]
    n = len(plan["maps"])
    return True, ("Cluster up: %d map%s plus Obelisk. First start downloads the game "
                  "files once on %s and the others wait for it, so give it a while."
                  % (n, "" if n == 1 else "s", plan["maps"][0]["name"]))


def stop(store):
    """Stop the cluster's containers. Saves and the data root are untouched."""
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    if not os.path.isfile(compose_path(store)):
        return False, "No compose file yet - this cluster has never been launched."
    rc, out = _compose(store, "down")
    if rc != 0:
        return False, "docker compose down failed:\n%s" % out[-1500:]
    return True, "Cluster stopped. Saves and settings are untouched; Launch brings it back."


def restart(store):
    """Recreate the stack from the current settings.

    Deliberately a rewrite-then-up rather than `docker compose restart`: settings
    changes land in the compose file, and restarting the old containers would restart
    them with the old values and look like the change had silently failed.
    """
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    return launch(store)


def status(store):
    """What is actually running, per service. Never raises - the UI shows this."""
    out = {"project": project(store), "compose_exists": os.path.isfile(compose_path(store)),
           "docker_ok": False, "docker_detail": "", "services": [], "running": 0}
    ok, why = dockerctl.available()
    out["docker_ok"], out["docker_detail"] = ok, why
    if not ok or not out["compose_exists"]:
        return out
    out["services"] = dockerctl.compose_ps(compose_path(store), project(store))
    out["running"] = sum(1 for s in out["services"] if s.get("state") == "running")
    return out

def save_world(store, rcon=None):
    """Ask every running map to write its world to disk. Returns (ok, detail).

    A copy taken mid-session captures the last autosave, which can be fifteen minutes
    of lost progress. This closes that window. It is best-effort on purpose: a map that
    is down or slow must not stop a backup from happening at all - a slightly older
    archive beats no archive.
    """
    from . import bot
    servers = bot.SERVERS or {}
    if not servers:
        return False, "no running maps to save"
    if rcon is None:
        import asyncio
        rcon = lambda host, port, cmd: asyncio.run(
            bot.rcon(host, port, cmd, timeout=30))
    done, failed = [], []
    for label, (host, port) in servers.items():
        try:
            rcon(host, port, "SaveWorld")
            done.append(label)
        except Exception as e:
            failed.append("%s (%s)" % (label, e))
    if not done:
        return False, "no map accepted SaveWorld: " + ", ".join(failed)
    if failed:
        return True, "saved %d of %d maps; %s did not answer" % (
            len(done), len(servers), ", ".join(failed))
    return True, "saved %d map(s)" % len(done)
