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

import logging, os, re, time

from . import dockerctl, layout, stack
from . import naming
from .compose import generate_compose, install_present
from .plan import build_plan

log = logging.getLogger("obelisk.cluster")

COMPOSE_NAME = "compose.yaml"


def project(store):
    """The compose project name. Keyed to the cluster id so two clusters on one host
    stay separate, and stable across restarts so `up` adopts what is already there."""
    cid = str(store.get("cluster_id") or "").strip()
    return cid or "ark"


def compose_path(store):
    """Where this cluster's compose file lives.

    Inside Compose Manager's project folder when that is mounted, so the plugin and
    Obelisk are looking at one file and driving one stack. Otherwise beside the settings
    it was generated from, which is the plain install and works exactly as before.
    """
    if stack.available():
        return stack.compose_file(project(store))
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


def write_compose(store, in_use_ports=None, text=None):
    """Generate the compose file and put it in the data root. Returns (path, text).

    Raises ValueError if the cluster would not boot - a bad plan never reaches disk.

    `text` lets a caller that has already generated the file hand it over rather than
    generate it twice, which is what registering the stack first requires.
    """
    if text is None:
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

    ok, why = name_conflicts(store)
    if not ok:
        return False, why

    prepare(store)

    # prepare() hands the folders to the server's user; if that did not take, the server
    # would come up, fail to write, and loop silently. Refuse instead.
    blocked = layout.not_writable_by_server(layout.ark_root_of(store))
    if blocked:
        return False, ("The game server (user %d) cannot write to its own data folders, "
                       "so it would start, fail to install, and restart forever without "
                       "saying why. Fix the ownership of these and launch again: %s"
                       % (layout.SERVER_UID, "; ".join(blocked[:4])))
    try:
        text = generate_compose(store, project=project(store), in_use_ports=in_use_ports)
    except ValueError as e:
        return False, str(e)

    # Register before the compose file is written, not after - and the order is the whole
    # point. Writing that file creates the project directory, and a project directory
    # with no marker in it is exactly what register refuses to touch, because that is
    # what somebody else's cluster looks like. Registering second meant a new cluster
    # collided with a folder it had made itself one line earlier and could never claim
    # its own stack on the launch that created it.
    ok_s, detail_s = stack.register(store, project(store), text)
    if not ok_s:
        log.info("stack not registered (%s) - the cluster still runs", detail_s)

    write_compose(store, text=text)

    rc, out = _compose(store, "up", "-d", "--remove-orphans")
    if rc != 0:
        return False, "docker compose up failed:\n%s" % out[-1500:]

    _join_network(store)
    n = len(plan["maps"])
    if not install_present(store):
        return True, ("Cluster up: %d map%s. First start downloads the game files once "
                      "on %s and the others wait for it, so give it a while."
                      % (n, "" if n == 1 else "s", plan["maps"][0]["name"]))
    return True, ("Cluster up: %d map%s, all starting together. Each one still has a "
                  "world to load, so give them a few minutes."
                  % (n, "" if n == 1 else "s"))


# ------------------------------------------------- letting each server close its own world
#
# On 2026-09-12 three worlds came back corrupt from an apply whose save had been proved.
# The proof was not wrong; it was measuring the wrong write. save_and_settle proves the
# world SaveWorld put on disk, and then `docker compose down` sends SIGTERM, and the
# server image performs a *second* save of its own on the way out - "verified two-stage
# ASA shutdown", in its words. That second save is the one that produced the damage, on
# exactly the three largest worlds, and nothing in Obelisk could see it.
#
# The fix is to make sure there is nothing left to shut down. ARK's own `DoExit` saves
# the world and closes the process on the server's schedule, with no deadline attached
# to it - unlike SIGTERM, which starts a clock. And the server image is explicit about
# what it does when it is signalled with the game already gone:
#
#     Container stop signal received; starting verified two-stage ASA shutdown...
#     Server is not running, no need to save world before stopping container.
#
# So a map that has already exited is a map whose dangerous second save never happens.
EXIT_BUDGET = 900             # seconds to let every map finish DoExit before moving on
EXIT_INTERVAL = 5

# One story, one message. Every stage of a stop is said into this slot, so the admin
# channel shows a single status line that keeps changing from "stopping" through
# "3 of 10 closed" to "stopped" - rather than six lines scrolling past over five
# minutes, or ten.
STOP_SLOT = "cluster.stop"

# How many consecutive looks at nothing before wait_healthy decides a container is not
# coming rather than not ready. A minute: long enough for a container being created to
# appear, short enough that a map left down on purpose does not hold a batch for the
# full twenty-five.
ABSENT_POLLS = 6


# ------------------------------------------------- four states, and only two mean "gone"
#
# On 2026-09-16 an apply stopped three maps that had not finished booting. RCON refusing
# a connection had been read as "the server has exited", and a refusal has two opposite
# meanings: the world is closed, or the server has not opened its RCON port yet - and
# ARK on this cluster can take more than ten minutes to open it. So `docker compose
# down` put SIGTERM into a booting server; the image's verified two-stage shutdown needs
# RCON to save the world and prove it, could not get it, hung past its stop_grace_period,
# was killed, was revived by the restart policy, and started another ten-minute boot.
# Three maps went round that loop.
#
# Silence cannot tell those two apart. Docker can, because a server that has exited
# leaves a container that is no longer running and a server that is still booting leaves
# one that is. So every map a stop touches ends in exactly one of four states:
#
#   CLOSED        DoExit taken, RCON went quiet, and Obelisk stopped that container
#   ALREADY_GONE  DoExit undeliverable, and its container is not running
#   NOT_READY     DoExit undeliverable, and its container IS running - it is alive
#   LATE          DoExit taken, still answering when the budget ran out
#
# `exited` is True for CLOSED and ALREADY_GONE and for nothing else. NOT_READY reading
# as exited is the whole of the incident, so it is the one line that must never move.
CLOSED = "closed"
ALREADY_GONE = "already_gone"
NOT_READY = "not_ready"
LATE = "late"

# Not a fifth state: the in-flight marker for a map that has taken DoExit and is being
# waited on. Everything still wearing it when the budget runs out becomes LATE.
CLOSING = "closing"


def _and(names):
    """"Ragnarok", or "Ragnarok and Valguero", or "A, B and C" - for a sentence a
    person reads rather than a list they parse."""
    names = list(names)
    if len(names) <= 1:
        return names[0] if names else ""
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _are(count, noun="player"):
    """"1 player is", "3 players are" - the subject and its verb, agreeing.

    Beside _and() because it is the same kind of helper and the same complaint: a
    sentence a person reads rather than a template with a parenthetical in it. The
    restore paths had three spellings of this between them, one of which was
    "%d player(s) are", which is wrong for every count including one.
    """
    count = int(count or 0)
    return "%d %s%s" % (count, noun, " is" if count == 1 else "s are")


def _rcon_for(store):
    """The real RCON caller, as a function, so more than one thing can use it.

    The final re-verify needs to ask the same question over the same connection the
    exit did. Two spellings of "talk to this server" would be two things to keep in
    step, and the one that drifted would be the one nobody was watching.
    """
    from . import bot
    password = str(store.get("admin_password") or "")

    def rcon(host, port, cmd):
        return run_coroutine(bot.rcon_with(host, port, password, cmd, timeout=30))

    return rcon


def _exit_names(store):
    """{label: (container name, compose service)} for every map this cluster defines.

    The container name is what Docker is asked about and the service is what compose is
    told to stop, and a stop needs both: one to decide whether a silent map is gone or
    still booting, the other to close the door behind a map that has gone.
    """
    proj = project(store)
    out = {}
    try:
        rows = build_plan(store)["maps"]
    except Exception as e:                          # noqa: BLE001 - reported, not fatal
        log.warning("could not work out this cluster's container names: %s", e)
        return out
    for r in rows:
        out[r["name"]] = (naming.container_name(proj, r["instance"]), r["instance"])
    return out


def _might_be_running(name, details):
    """Is this container up - or is there any chance it still is? True if either.

    The discriminator the four states turn on, and deliberately not a plain "is it
    running", because there are three answers and only two of them are facts:

        Docker says running          - a fact. The server is alive.
        Docker answers, nothing there - a fact. That container is gone.
        Docker does not answer at all - neither. Nothing has been established.

    An unknown is "possibly up". Reading "I could not ask" as "it has exited" is the
    same inference this whole section exists to delete one layer further up: a refused
    RCON connection is not proof of an exit, and an unanswerable Docker is not proof
    either. So an unknown makes the map NOT_READY, which makes an apply hold - and
    holding an apply because Docker hiccuped costs a window, while signalling a booting
    server because Docker hiccuped cost two hours and three maps in a restart loop.

    Asked per map per turn, the way wait_healthy already asks it, because the answer
    changes while a stop is happening - a map that was booting a minute ago may be
    serving now, and that is exactly what the retry is watching for.
    """
    if not name:
        # No container was ever derived for this target, so there is nothing to ask
        # about - a different thing from an ask that failed. Both callers take the name
        # from the target they were handed, so this is a guard rather than a path.
        return False
    try:
        got = details([name]) or {}
    except Exception as e:                          # noqa: BLE001 - reported, not fatal
        log.warning("could not ask Docker whether %s is still running (%s) - treating "
                    "it as possibly up, which holds the stop rather than signalling a "
                    "server that may still be booting", name, e)
        return True
    return (got.get(name) or {}).get("state") == "running"


def _ask_to_exit(one, rcon, details):
    """Send one DoExit and classify what came back. True if it was taken.

    A NOT_READY map comes back through here on the next turn, which is the whole retry:
    the same send, the same classification, no second wait and no second budget.
    """
    host, port = one["at"]
    try:
        rcon(host, port, "DoExit")
    except Exception as e:                          # noqa: BLE001 - reported, not raised
        if _might_be_running(one["name"], details):
            # Refused by a container that is up, or that Docker could not be asked
            # about. This is a server that has not opened RCON yet, not one that has
            # closed - and signalling it is what put three maps in a boot loop. It is
            # not exited, it is not finished, and it will be asked again next turn.
            one["state"], one["exited"] = NOT_READY, False
            one["why"] = ("it is not answering RCON yet (%s) and its container has "
                          "not stopped - it has not finished starting" % e)
        else:
            # Refused, and Docker answered that nothing is running under that name.
            # Both halves are facts, and together they mean there is nothing to shut
            # down. Note the asymmetry with the branch above: "gone" has to be
            # established, never assumed.
            one["state"], one["exited"] = ALREADY_GONE, True
            one["why"] = ("did not answer DoExit (%s) and its container is not running"
                          % e)
        return False
    one["state"], one["exited"] = CLOSING, False
    one["why"] = "asked to exit"
    return True


def _close_the_door(one, label, stop_container):
    """Stop this map's container the moment its own world is closed.

    Per map, here, rather than left to the batch `docker compose down` that can be up
    to EXIT_BUDGET later. That gap is a window in which a restart policy can bring a
    server back up behind a stop that believes it is finished - and stopping a
    container whose game is already gone is exactly the branch the image documents as
    safe ("Server is not running, no need to save world before stopping container").

    Best effort, because the world is already written either way: the danger this
    closes is revival, and the batch `down` still follows.
    """
    if not one.get("key"):
        log.warning("%s closed its world but Obelisk could not tell which service to "
                    "stop - the stop that follows will get it", label)
        one["why"] += ", though Obelisk could not tell which container to stop"
        return False
    try:
        ok, why = stop_container(one["key"])
    except Exception as e:                          # noqa: BLE001 - reported, not fatal
        ok, why = False, str(e)
    if not ok:
        log.warning("%s closed its world but its container did not stop: %s", label, why)
        one["why"] += ", though its container did not stop (%s)" % why
        return False
    one["why"] += ", and its container is stopped"
    return True


def exit_worlds(store, rcon=None, wait=None, now=None, budget=EXIT_BUDGET,
                interval=EXIT_INTERVAL, running=None, on_exited=None, details=None,
                stop_container=None):
    """Ask every running map to save and close itself. {label: {exited, why, state}}.

    Sent, then waited for, because the point is the waiting: `DoExit` returns as soon as
    the command is accepted, which is the same lie SaveWorld tells. What says the world
    is closed is the server no longer answering at all.

    `state` is one of CLOSED, ALREADY_GONE, NOT_READY or LATE, and `exited` is True for
    the first two only. A map that will not answer is not automatically a map that has
    gone: if its container is still running it is alive - almost always still booting -
    and calling that "exited" is what let a stop signal three servers mid-boot.

    Best-effort per map on purpose. A map that will not exit is not a reason to refuse
    the stop here - it is a reason to say so and let the caller decide, which is what
    `require_ready` on stop() does with it.
    """
    now = now or time.time
    wait = wait or time.sleep
    details = details or (lambda names: dockerctl.container_details(names))
    stop_container = stop_container or (lambda key: stop_one(store, key))
    targets = running(store) if running else running_instances(store)
    if not targets:
        return {}

    if rcon is None:
        rcon = _rcon_for(store)

    named = _exit_names(store)
    seen = {}
    for label, host, port in targets:
        cname, key = named.get(label, (host, None))
        seen[label] = {"at": (host, port), "name": cname, "key": key,
                       "state": None, "exited": False, "why": ""}
        _ask_to_exit(seen[label], rcon, details)

    started = now()
    # Counted, so the reporter can say "3 of 10" rather than just naming maps into the
    # dark. Same shape as worlds_settled's on_settled, deliberately - two waits that
    # report the same way are two waits somebody only has to learn once.
    closing = sum(1 for o in seen.values() if not o["exited"])
    shut = 0
    for _turn in range(max(1, int(budget / max(1, interval)) + 1)):
        for label, one in seen.items():
            if one["exited"]:
                continue
            if one["state"] == NOT_READY:
                # The readiness retry, folded into the wait that already exists. A map
                # that has not opened RCON is asked again each turn inside the same
                # budget; a wait of its own, per map, one after another, would cost the
                # cluster far more than the bug it was fixing.
                _ask_to_exit(one, rcon, details)
                continue
            host, port = one["at"]
            try:
                rcon(host, port, "ListPlayers")
            except Exception:
                # Silence *after* a DoExit that was taken. That is the world written and
                # the process gone - so the container is stopped here and now.
                one["state"], one["exited"] = CLOSED, True
                one["why"] = "closed its world and exited"
                _close_the_door(one, label, stop_container)
                shut += 1
                if on_exited:
                    try:
                        on_exited(label, shut, closing)
                    except Exception as e:          # noqa: BLE001 - reporting only
                        log.warning("could not report %s exiting: %s", label, e)
        if all(one["exited"] for one in seen.values()):
            break
        if now() - started >= budget:
            break
        wait(interval)

    for label, one in seen.items():
        if one["exited"]:
            continue
        if one["state"] == NOT_READY:
            one["why"] = ("it never answered RCON in %ds and its container has not "
                          "stopped - it has not finished starting" % budget)
            log.warning("%s was never ready to be asked to close: %s", label, one["why"])
        else:
            one["state"] = LATE
            one["why"] = "still answering %ds after DoExit" % budget
            log.warning("%s did not close its world: %s", label, one["why"])
    return {l: {"exited": o["exited"], "why": o["why"], "state": o["state"]}
            for l, o in seen.items()}


def still_answering(store, rcon=None, running=None):
    """Which maps answer RCON right now. Sorted labels, never raises.

    The last look before anything is signalled. Everything a stop concludes about a
    closed world is drawn from silence, and silence is also what a server that has not
    opened its RCON port yet sounds like - so a map that is talking again was never
    closed, whatever the wait decided about it a minute earlier.
    """
    rcon = rcon or _rcon_for(store)
    try:
        targets = running(store) if running else running_instances(store)
    except Exception as e:                          # noqa: BLE001 - reported, not fatal
        log.warning("could not re-check which maps are answering: %s", e)
        return []
    awake = []
    for label, host, port in targets:
        try:
            rcon(host, port, "ListPlayers")
        except Exception:
            continue
        awake.append(label)
    return sorted(awake)



def stop(store, close_worlds=True, say=None, require_ready=False, **kw):
    """Stop the cluster's containers. Saves and the data root are untouched.

    Every map is asked to close its own world first. `close_worlds=False` skips that for
    a caller that has already done it, or that is stopping a cluster whose worlds are
    not worth waiting on.

    `require_ready` is the difference between the two callers, and it is a judgement
    about consent rather than about safety alone. An apply is unattended and wants a
    cluster it can promote a build over, so a map that is still booting - NOT_READY -
    makes it refuse and `docker compose down` is never reached: nothing is signalled,
    nothing is removed, and the window comes round again. An operator pressing Stop has
    asked for a stop, so it proceeds, because `down` removes the containers and a
    removed container is not one a restart policy can revive - but the message names
    every map that never became operational, because that is a thing worth knowing.

    `say` is the announcer, defaulting to the real one. A stop is minutes long and was
    silent for all of them, which is not a thing a person can tell apart from nothing
    happening.
    """
    from . import announce
    _say = announce.say if say is None else say

    def say(*a, **kw):
        """Telling somebody must never be what stops a cluster stopping. The channel
        being down is not a reason to leave ten servers running.

        Every stage goes into one slot, so the channel carries a single status line for
        the whole stop rather than a scroll of them. The UI feed still gets each stage
        as its own entry - it is the record, not the glance.
        """
        kw.setdefault("slot", STOP_SLOT)
        try:
            _say(*a, **kw)
        except Exception as e:                      # noqa: BLE001 - reporting only
            log.warning("could not announce a stop stage: %s", e)

    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    if not os.path.isfile(compose_path(store)):
        return False, "No compose file yet - this cluster has never been launched."

    closed = {}
    if close_worlds:
        # The stop takes minutes and used to say nothing for all of them. Whoever
        # pressed the button watched ten worlds close in total silence and reasonably
        # concluded nothing was happening - so every stage says so now.
        kw.setdefault("on_exited", lambda label, done, total: say(
            "cluster.world_closed",
            "%s saved its world and closed (%d of %d)." % (label, done, total)))
        say("cluster.closing",
            "Stopping the cluster. Each map is being asked to save and close its own "
            "world first, which takes a few minutes on a big map - nothing is shut "
            "down until its world is written.")
        try:
            closed = exit_worlds(store, **kw)
        except Exception as e:                      # noqa: BLE001 - never blocks a stop
            log.warning("could not close the worlds before stopping: %s", e)
            say("cluster.closing_failed",
                "The maps could not be asked to close their worlds, so every one of "
                "them is being stopped the ordinary way instead - their last saves are "
                "whatever each server wrote on its way out. Reason: %s" % e,
                level="warning")

    # One last probe of every map, before anything is signalled and before any of this
    # is announced as done. Everything above concludes a world is closed from silence,
    # and silence is also the sound of a server that has not opened its RCON port yet -
    # so a map that is answering again was never closed, whatever the wait decided about
    # it a minute ago. It is alive, which is NOT_READY, and this is the point at which
    # that is still cheap to act on.
    if closed:
        try:
            awake = still_answering(store, rcon=kw.get("rcon"), running=kw.get("running"))
        except Exception as e:                      # noqa: BLE001 - never blocks a stop
            log.warning("could not re-check the maps before stopping: %s", e)
            awake = []
        for label in awake:
            if closed.get(label, {}).get("exited"):
                log.warning("%s answered RCON again after being reported closed", label)
                closed[label] = {
                    "exited": False, "state": NOT_READY,
                    "why": ("it answered RCON again after it was reported closed - it "
                            "is alive, not stopped")}

    late = sorted(l for l, c in closed.items() if c.get("state") == LATE)
    not_ready = sorted(l for l, c in closed.items() if c.get("state") == NOT_READY)
    many = len(not_ready) > 1

    # The refusal, and it happens before the summary and long before `down`. A map whose
    # container is up and whose RCON never opened is a server mid-boot: SIGTERM into one
    # of those is the incident this whole section exists for, because the image's safe
    # stop needs the RCON it has not opened yet, hangs past its grace period, is killed,
    # and is revived to boot again.
    if not_ready and require_ready:
        # What the hold may honestly claim depends on what has already happened, and on
        # the incident's own shape - some maps closed, one was still booting - several
        # maps are already saved, closed and stopped by the time this decides. Saying
        # "nothing has been stopped" there is both untrue and a trap: those maps stay
        # down until somebody presses Launch, and this is the only thing that could tell
        # them so.
        shut_early = sorted(l for l, c in closed.items() if c.get("exited"))
        if shut_early:
            moved = ("%s had already saved and closed before this, so %s down now and "
                     "will stay down until Launch - the rest of the cluster is still "
                     "up. Nothing was removed and no build was swapped."
                     % (_and(shut_early), "they are" if len(shut_early) > 1 else "it is"))
        else:
            moved = ("Nothing has been stopped, nothing has been removed and nothing "
                     "has been changed.")
        say("cluster.not_ready",
            "%s never answered RCON and %s not stopped, so %s had not finished booting "
            "- and a server mid-boot cannot be stopped safely, because the image's own "
            "safe stop needs the RCON it has not opened yet. %s This can run again once "
            "%s answering."
            % (_and(not_ready), "are" if many else "is", "they" if many else "it",
               moved, "they are" if many else "it is"),
            level="warning",
            # The closed maps carry their own reason rather than a flattened label, so
            # the line that says a container is stopped is the line about that map.
            detail="\n".join(
                "%-14s %s" % (l, (closed[l].get("why") or "") if closed[l].get("exited")
                              else "NOT STOPPED - %s" % (closed[l].get("why") or ""))
                for l in sorted(closed)))
        return False, ("%s had not finished booting and never answered RCON, so the "
                       "cluster was not stopped. %s This can be applied on the next "
                       "window, once every map is answering."
                       % (_and(not_ready), moved))

    if closed:
        shut = [l for l, c in closed.items() if c.get("exited")]
        worlds = "world" if len(closed) == 1 else "worlds"
        if late:
            text = ("%d of %d %s saved and closed. %s would not close and %s being "
                    "stopped the ordinary way instead - worth checking %s once the "
                    "cluster is back up. Stopping the servers now."
                    % (len(shut), len(closed), worlds, _and(late),
                       "is" if len(late) == 1 else "are",
                       "it" if len(late) == 1 else "them"))
        else:
            text = ("%d of %d %s saved and closed. Stopping the servers now - nothing "
                    "is left writing." % (len(shut), len(closed), worlds))
        say("cluster.closed" if not late else "cluster.closed_partly", text,
            level="info" if not late else "warning",
            detail="\n".join(
                "%-14s %s" % (l, "Saved and closed" if closed[l].get("exited")
                              else "DID NOT CLOSE - %s" % (closed[l].get("why") or ""))
                for l in sorted(closed)))

    if not_ready:
        # Not a refusal here: Stop was asked for, and `down` removes the containers, so
        # nothing is left for a restart policy to bring back. Said out loud all the same,
        # because a map that never finished starting is the one to watch next time.
        say("cluster.not_ready",
            "%s never answered RCON and %s not stopped, so %s had not finished "
            "booting and %s no world to close. Stop was asked for, so %s being removed "
            "with the rest - worth watching %s come up when the cluster is back. "
            "Stopping the servers now."
            % (_and(not_ready), "are" if many else "is", "they" if many else "it",
               "have" if many else "has", "they are" if many else "it is",
               "them" if many else "it"),
            level="warning",
            detail="\n".join("%-14s %s" % (l, closed[l].get("why") or "")
                             for l in not_ready))

    rc, out = _compose(store, "down")
    if rc != 0:
        return False, "docker compose down failed:\n%s" % out[-1500:]

    note = ""
    if late:
        note = (" %s had to be stopped without closing %s world first, so %s worth a "
                "look when the cluster is back."
                % (_and(late), "its" if len(late) == 1 else "their",
                   "it is" if len(late) == 1 else "they are"))
    if not_ready:
        note += (" %s never finished booting - %s running but never answered RCON - so "
                 "%s removed without having a world to close."
                 % (_and(not_ready), "they were" if many else "it was",
                    "they were" if many else "it was"))
    return True, ("Cluster stopped. Saves and settings are untouched; Launch brings it "
                  "back.%s" % note)


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
    _enrich(out["services"])
    out["trouble"] = [s for s in out["services"] if s.get("looping") or s.get("failure")]
    return out


def _enrich(services):
    """Add what the container is actually doing, and whether it is failing.

    A container that aborts and restarts every few seconds reports `running` the whole
    time. Reporting that as green is the bug this exists to prevent.
    """
    from . import progress
    names = [s.get("name") for s in services if s.get("name")]
    if not names:
        return services
    try:
        details = dockerctl.container_details(names)
    except Exception:
        details = {}
    for s in services:
        d = details.get(s.get("name"), {})
        s.update({k: d.get(k) for k in ("restarts", "uptime_seconds")})
        if d.get("state"):
            s["state"] = d["state"]
        if d.get("health"):
            s["health"] = d["health"]
        s["looping"] = progress.looks_like_a_loop(d.get("restarts"), d.get("uptime_seconds"))
        text = ""
        if s["looping"] or s["state"] != "running" or (s.get("health") or "") != "healthy":
            try:
                text = dockerctl.logs(s["name"], tail=200)
            except Exception:
                text = ""
        phase, percent, failure = progress.read_log(text)
        s["phase"], s["percent"], s["failure"] = phase, percent, failure
        s["log_tail"] = (chr(10).join(text.splitlines()[-12:])
                         if (s["looping"] or failure) else "")
        s["level"], s["says"] = progress.describe(s)
    return services

def rcon_targets(store):
    """(label, host, port) for every instance in this cluster.

    Addressed by container name on the cluster's own network, which is why the manager
    joins that network after a launch. The old version read a SERVERS environment
    variable that only ever existed when Obelisk wrote itself into the stack - so on a
    normally-installed Obelisk it was always empty, and every flush reported "no running
    maps to save" while the maps were up and healthy.
    """
    from . import naming
    plan = build_plan(store)
    proj = project(store)
    return [(r["name"], naming.container_name(proj, r["instance"]), r["rcon_port"])
            for r in plan["maps"]]


def running_instances(store):
    """The subset of this cluster's containers Docker says are running."""
    targets = rcon_targets(store)
    try:
        details = dockerctl.container_details([t[1] for t in targets])
    except Exception:
        details = {}
    return [t for t in targets if (details.get(t[1], {}).get("state") == "running")]


def run_coroutine(coro):
    """Run a coroutine and return its result, from a worker thread or the loop thread.

    asyncio.run() refuses outright when the calling thread already has a running loop.
    The coroutine handed to it is then never awaited - Python logs "coroutine was never
    awaited" to stderr, the RuntimeError is caught by whatever except wraps the call,
    and the operation is recorded as having been tried and failed. It was never sent.
    That is how a backup came to report every map as not answering SaveWorld while all
    ten were healthy and answering RCON perfectly well.

    So: use the simple path when this thread has no loop, and hand the work to a thread
    of its own when it does.
    """
    import asyncio, concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)                    # no loop here - the ordinary case
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def save_world(store, rcon=None, accepted=None, now=None):
    """Ask every running map to write its world to disk. Returns (ok, detail).

    A copy taken mid-session captures the last autosave, which can be fifteen minutes of
    lost progress. This closes that window. Best-effort on purpose: a map that is down or
    slow must not stop a backup happening at all - a slightly older archive beats none.

    `accepted` is an optional dict, filled with {label: the instant that map took the
    command}. The RCON call returning means the server *accepted* SaveWorld, not that the
    world was written, so a caller that has to be sure - anything about to stop the
    cluster - needs both halves: which maps answered, and when. The instant matters on
    its own, because a world file older than its own SaveWorld has not started writing
    yet, and on disk that looks exactly like one that finished long ago.
    """
    from . import bot
    now = now or time.time
    targets = running_instances(store)
    if not targets:
        return False, "no running maps to save"

    password = str(store.get("admin_password") or "")
    if rcon is None:
        def rcon(host, port, cmd):
            return run_coroutine(bot.rcon_with(host, port, password, cmd, timeout=30))

    done, failed = [], []
    for label, host, port in targets:
        try:
            rcon(host, port, "SaveWorld")
            done.append(label)
            if accepted is not None:
                accepted[label] = now()
        except Exception as e:
            failed.append("%s (%s)" % (label, e))
    if not done:
        return False, "no map accepted SaveWorld: " + ", ".join(failed)
    if failed:
        return True, "saved %d of %d maps; %s did not answer" % (
            len(done), len(targets), ", ".join(failed))
    return True, "saved %d map(s)" % len(done)


# ------------------------------------------------- proving a save actually landed
#
# On 2026-09-11 the cluster was stopped while the larger worlds were still serialising.
# Every world at or above 76 MB was damaged and every world at or below 41 MB came
# through clean, which is the signature of a race rather than of a broken save: the
# small maps finished inside the time ten sequential RCON round-trips happened to take,
# and the big ones did not.
#
# There is no delay here to lengthen. A longer one would move the cliff and hide it
# better - a bigger world, a busier host or an eleventh map puts it straight back. What
# the stop needs is proof, per map, and the proof is two facts that have to hold
# together because either alone will lie:
#
#   the world file stopped changing   - size and mtime steady across consecutive polls
#   and nothing is open beside it     - no SQLite sidecar next to it
#
# The sidecar half is the one that matters most. A hot journal is not a timing hint, it
# is a statement that a transaction is open, and stopping into that is the exact failure
# being prevented.
SETTLE_BUDGET = 300           # seconds a map gets to finish writing before we refuse
SETTLE_INTERVAL = 5           # seconds between polls
SETTLE_QUIET = 2              # identical consecutive readings that count as "stopped"


def _poll_world(seen, sent_at, sidecars, stat, exists, quiet):
    """One reading of one map's world file. Mutates and returns `seen`."""
    path = seen["path"]
    hot = [s for s in sidecars if exists(path + s)]
    if hot:
        seen["reading"], seen["still"] = None, 0
        seen["why"] = ("a %s file is still open beside its world"
                       % ", ".join(sorted(hot)))
        return seen
    try:
        info = stat(path)
        reading = (info.st_size, info.st_mtime)
    except OSError as e:
        seen["reading"], seen["still"] = None, 0
        seen["why"] = "its world file could not be read: %s" % e
        return seen
    if reading[1] < sent_at:
        # Quiet, but quiet from *before* it was asked to save. This is the save that has
        # not started rather than the save that has finished, and the two are identical
        # to anything that only watches for the file to stop moving.
        seen["reading"], seen["still"] = None, 0
        seen["why"] = "it has not begun writing yet - its world is older than the save"
        return seen
    if reading == seen["reading"]:
        seen["still"] += 1
    else:
        seen["reading"], seen["still"] = reading, 1
    if seen["still"] >= quiet:
        seen["settled"], seen["why"] = True, "saved"
    else:
        seen["why"] = "still writing"
    return seen


def worlds_settled(store, sent, ark_root=None, now=None, stat=None, exists=None,
                   wait=None, budget=SETTLE_BUDGET, interval=SETTLE_INTERVAL,
                   quiet=SETTLE_QUIET, on_settled=None):
    """Wait for every map in `sent` to finish writing its world.

    `sent` is {label: the instant that map accepted SaveWorld} - exactly what
    save_world() fills in. Returns {label: {"settled": bool, "why": str}}.

    Only maps that answered are in here. A map that is down cannot be waited for, and
    making the caller wait on it would turn "one map is off" into "no update ever
    applies again".

    `on_settled(label, done, total)` is called once per map, at the moment that map's
    world is proved written - which this already knows and used to throw away. The whole
    wait was one aggregate line to anybody watching, so a save that was working looked
    identical to a save that was stuck. Called on the transition only: a map already
    settled is skipped before the poll, so it cannot fire twice.
    """
    from . import restore, savepoints
    now = now or time.time
    stat = stat or os.stat
    exists = exists or os.path.exists
    wait = wait or time.sleep

    keys = {r["name"]: r["map"] for r in build_plan(store)["maps"]}
    seen = {}
    for label in sent:
        try:
            path = savepoints.live_world(store, keys[label], ark_root)
        except KeyError:
            # Never read "we could not check" as "it is fine". A map that answered and
            # whose world we cannot find is an unknown, and an unknown is not a proof.
            path = None
        seen[label] = {"path": path, "reading": None, "still": 0, "settled": False,
                       "why": ("nothing read yet" if path else
                               "could not work out where its world file is")}

    started = now()
    # Counted as well as clocked, the same way the staging wait is. A loop whose only
    # exit is the wall clock passing a deadline spins forever the moment anything hands
    # it a clock that does not move - and the first thing to do that is always a test.
    total, done = len(seen), 0
    for _turn in range(max(1, int(budget / max(1, interval)) + 1)):
        for label, one in seen.items():
            if one["settled"] or not one["path"]:
                continue
            _poll_world(one, sent[label], restore.SIDECARS, stat, exists, quiet)
            if one["settled"]:
                done += 1
                if on_settled:
                    # Never let telling somebody about the save break the save. This
                    # runs with the cluster still up and a stop waiting on it.
                    try:
                        on_settled(label, done, total)
                    except Exception as e:          # noqa: BLE001 - reporting only
                        log.warning("could not report %s settling: %s", label, e)
        if all(one["settled"] for one in seen.values()):
            break
        if now() - started >= budget:
            break
        wait(interval)

    for label, one in seen.items():
        if not one["settled"]:
            log.warning("%s had not finished saving after %ds: %s",
                        label, budget, one["why"])
    return {label: {"settled": one["settled"], "why": one["why"]}
            for label, one in seen.items()}


def save_and_settle(store, ark_root=None, rcon=None, now=None, **kw):
    """Save every running map, then prove each save landed. (ok, detail, worlds).

    The (ok, detail) half is save_world()'s, unchanged, because the tolerance it
    describes is still right: a map that is down must not stop an update. `worlds` is
    the new half - {label: {"settled", "why"}} for every map that accepted SaveWorld -
    and it is what the apply path refuses on.

    Keyword arguments are handed to worlds_settled, so `on_settled` is passed the same
    way the test seams are: save_and_settle(store, root, on_settled=...).
    """
    now = now or time.time
    sent = {}
    ok, detail = save_world(store, rcon=rcon, accepted=sent, now=now)
    if not sent:
        return ok, detail, {}
    worlds = worlds_settled(store, sent, ark_root=ark_root, now=now, **kw)
    late = sorted(l for l, w in worlds.items() if not w["settled"])
    if late:
        detail = "%s; %s did not finish writing" % (detail, ", ".join(late))
    return ok, detail, worlds


# ------------------------------------------------- is each world still readable at all
#
# save_and_settle proves a world finished being written. It cannot prove the bytes are
# any good, and on 2026-09-12 they were not: the server image performs its own save on
# the way down, that save damaged the three largest worlds, and the apply promoted a new
# build over the top of them and started servers onto the wreckage. The crash loop that
# followed was the first anybody heard of it, two hours later.
#
# So there is one moment worth asking the question - after the stop, before anything
# moves. The cluster is down, so every world is a static file, which is exactly what
# verify_world's immutable=1 open assumes. Any earlier and the shutdown save has not
# happened yet; any later and a rename has already been made on the strength of an
# answer nobody asked for.
def readable_dir(path, listdir=None):
    """(is this a directory we can actually read, why not).

    Asked by listing it rather than by stat'ing it, because the failure being hunted is
    a directory that exists and will not open: a share whose permissions have drifted,
    or a volume that is not mounted. os.path.lexists answers False for both - it catches
    OSError the same way os.path.exists does - and "False" there is indistinguishable
    from "nothing was ever here", which is how a healthy world behind an unreadable
    parent came to be read as a map that had never booted.
    """
    listdir = listdir or os.listdir
    try:
        listdir(path)
    except OSError as e:
        return False, str(e)
    return True, ""


def world_on_disk(store, key, ark_root=None, stat=None, exists=None, listdir=None):
    """One map's live world file as the disk has it right now, or None. One look.

    The read-only half of the settle machinery above, for somebody watching by hand.
    worlds_settled() polls this same pair of facts - the file's own size and mtime, and
    whether a SQLite sidecar is open beside it - in a loop, because a stop has to *wait*
    for them. An operator who has just sent SaveWorld does not need a loop; they need
    the same two facts once, and a page they can reload. So this is that single poll,
    built out of the same savepoints.live_world path and the same restore.SIDECARS, and
    deliberately not a second opinion on what "settled" means: it reports what is there
    and judges nothing.

    None means the filesystem could not answer - an unmounted volume, a share whose
    permissions have drifted, a map whose world path cannot be worked out - and the page
    says nothing at all in that case rather than reporting an absence it did not
    establish. The same rule the on-disk map-id note keeps, for the same reason: "no
    saved world" about a disk nobody could read is a guess wearing a fact's clothes.

    Otherwise {"path", "present", "size", "mtime", "hot"}, where `present` False is a
    directory that read fine and has no world in it - a map that has never launched.
    """
    from . import restore, savepoints
    stat = stat or os.stat
    exists = exists or os.path.exists

    try:
        path = savepoints.live_world(store, key, ark_root)
    except Exception as e:                           # noqa: BLE001 - never a blank page
        log.info("could not work out where %s's world lives: %s", key, e)
        return None
    # The ark root, not this map's own folder, and the whole answer turns on it. A map
    # folder that is not there is the ordinary state of a map that has never launched;
    # a SavedArks that will not list is a volume that is not mounted, and "no world" is
    # not something anybody established about it.
    root = layout.ark_paths(ark_root or layout.ark_root_of(store))["saved_arks"]
    ok, why = readable_dir(root, listdir=listdir)
    if not ok:
        log.info("could not read the ARK data directory, so %s's world is unknown: %s",
                 key, why)
        return None

    hot = sorted(s for s in restore.SIDECARS if exists(path + s))
    try:
        info = stat(path)
    except FileNotFoundError:
        return {"path": path, "present": False, "size": 0, "mtime": 0.0, "hot": hot}
    except OSError as e:
        # The directory listed and this entry would not stat: a broken link, or a
        # permission that applies to the file alone. Not "there is no world here".
        log.info("could not read %s's world file: %s", key, e)
        return None
    return {"path": path, "present": True, "size": info.st_size,
            "mtime": info.st_mtime, "hot": hot}


def worlds_intact(store, ark_root=None, verify=None, exists=None, keys=None,
                  deep=True, lexists=None, isdir=None, listdir=None):
    """Every selected map's world, checked on disk. {label: {ok, why, key}}.

    The check itself is restore.verify_world - the one that already exists, reused
    rather than reimplemented, so there is a single answer in the product to "is this a
    world". The sidecar test is added on top because verify_world deliberately cannot
    see it: immutable=1 ignores a hot journal, so a world with a transaction still open
    passes an integrity check and is still mid-write.
    """
    from . import restore, savepoints
    verify = verify or restore.verify_world
    exists = exists or os.path.exists
    lexists = lexists or os.path.lexists
    isdir = isdir or os.path.isdir

    rows = keys if keys is not None else [(r["name"], r["map"])
                                          for r in build_plan(store)["maps"]]

    # The ark root first, and the whole batch turns on it. Every "this map has no world"
    # below is only trustworthy if the folder those worlds live in is genuinely readable
    # - otherwise "missing" means "I could not look", and ten maps reading absent at
    # once is an unmounted volume rather than ten maps that have never booted.
    root = ark_root if ark_root is not None else layout.ark_root_of(store)
    saved = layout.ark_paths(root)["saved_arks"]
    ok_root, why_root = readable_dir(saved, listdir=listdir)
    if not ok_root:
        return {label: {
            "ok": False, "state": "unreachable", "key": key,
            "why": ("the ARK data directory could not be read (%s) - is the volume "
                    "mounted?" % why_root)} for label, key in rows}

    out = {}
    for label, key in rows:
        try:
            path = savepoints.live_world(store, key, ark_root)
        except KeyError:
            out[label] = {"ok": False, "state": "unknown", "key": key,
                          "why": "could not work out where its world file is"}
            continue

        hot = [s for s in restore.SIDECARS if exists(path + s)]
        if hot:
            # Not damage. The file may well read perfectly - immutable=1 ignores the
            # journal - but a transaction was open when everything stopped, so this
            # world has not finished settling and is not safe to promote over.
            out[label] = {"ok": False, "state": "writing", "key": key,
                          "why": ("a %s file is still open beside it, so it had not "
                                  "finished being written"
                                  % ", ".join(sorted(hot)))}
            continue

        # "Absent" has to mean nothing is there at all, and os.path.exists is the wrong
        # question for that: it follows links, so a dangling one reads as missing, and
        # it swallows every stat error, so an entry it cannot resolve reads as missing
        # too. Both were being waved through as "never booted" and started empty -
        # which is the precise failure verify_world puts its symlink check first for,
        # and skipping to this branch made that check unreachable for the live world.
        #
        # lexists answers about the directory entry rather than what it points at, so a
        # broken link is present, and present means verify_world decides.
        if not lexists(path):
            folder = os.path.dirname(path)
            if lexists(folder) and not isdir(folder):
                # Something is in the way that is not a folder. Nothing can be concluded
                # about a world underneath it, and an unknown is never a pass.
                out[label] = {"ok": False, "state": "unknown", "key": key,
                              "why": ("%s is in the way and is not a folder, so its "
                                      "world cannot be read" % folder)}
                continue
            if lexists(folder):
                # The folder is there. If it will not open, the world inside it is an
                # unknown rather than a thing that does not exist - a world behind a
                # share whose permissions have drifted is still a world, and starting
                # that map empty is the failure this whole gate exists to prevent.
                ok_dir, why_dir = readable_dir(folder, listdir=listdir)
                if not ok_dir:
                    out[label] = {"ok": False, "state": "unreachable", "key": key,
                                  "why": ("its world folder could not be read (%s) - a "
                                          "permission or mount problem, not a missing "
                                          "world" % why_dir)}
                    continue
            # Genuinely nothing there. A map that has never booted has no world, and a
            # world that does not exist cannot be corrupt. Blocking would be a trap with
            # no way out: the apply refuses, the map is held down, and a map that is
            # down can never create the world whose absence caused the refusal - so
            # every apply after it refuses the same way until somebody launches by hand.
            # Starting it is how it gets one, which is the reasoning the save gate
            # already uses to tolerate a map that is down.
            out[label] = {"ok": True, "state": "absent", "key": key,
                          "why": "it has no world yet - it has not booted before"}
            continue

        ok, why = verify(path, deep=deep)
        out[label] = {"ok": bool(ok), "state": "ok" if ok else "damaged",
                      "key": key, "why": why}

    # Every map absent at once, on a cluster that was serving a moment ago. The root
    # listed, so this is not the unmount above - but ten maps do not stop having worlds
    # together, and whatever did that is not something to swap a build over. Refused as
    # a batch rather than skipped ten times.
    #
    # Only above one map: on a single-map cluster "all absent" and "the one map has
    # never booted" are the same observation, and that one has to keep working or a map
    # that has never started can never start.
    if len(out) > 1 and all(w["state"] == "absent" for w in out.values()):
        return {label: dict(w, ok=False, state="unreachable",
                            why=("none of the %d maps has a world, which %d maps do "
                                 "not stop having at once - the ARK data directory may "
                                 "be the wrong one or only half mounted"
                                 % (len(out), len(out))))
                for label, w in out.items()}
    return out


def _join_network(store, environ=None):
    """Put this container on the cluster's network so the maps are reachable by name.

    Obelisk is not a service in the stack it generates - it is the thing that generates
    it - so Docker does not attach it for us. Without this the manager could create maps
    it could not then talk to over RCON.

    Best effort: a cluster that is up but not yet reachable by name is still a working
    cluster, and saying so beats failing a launch that succeeded.
    """
    environ = os.environ if environ is None else environ
    me = (environ.get("HOSTNAME") or "").strip() or          (environ.get("HOST_CONTAINERNAME") or "").strip()
    if not me:
        return False, "could not tell which container this is"
    net = "%s-net" % project(store)
    ok, detail = dockerctl.network_connect(net, me)
    if ok:
        log.info("joined the cluster network %s (%s)", net, detail)
    else:
        log.warning("could not join the cluster network %s: %s - the maps are running, "
                    "but chat relay and in-game commands will not reach them", net, detail)
    return ok, detail

def target_names(store):
    """The container names this cluster would create - one per instance."""
    from . import maps as mapcat
    proj = project(store)
    return [naming.container_name(proj, i) for i in mapcat.instance_ids(_map_keys(store))]


def name_conflicts(store, existing=None):
    """(ok, message). Refuse a launch that would fight over a container name.

    Docker refuses to reuse a name, which is the only reason a generated stack that
    claimed a live cluster's container failed instead of replacing it. Relying on that
    is relying on luck: it aborts halfway, having already created a network, and the
    error talks about container IDs rather than saying which cluster is in the way.

    A name owned by this same cluster is not a conflict - that is a relaunch, which is
    exactly what `up` is for.
    """
    if existing is None:
        existing = dockerctl.existing_containers()
    if existing is None:
        # Never treat "could not check" as "nothing is there".
        return False, ("Couldn't check which containers already exist on this host, so "
                       "the launch was not attempted. Is the Docker socket still mounted?")
    proj = project(store)
    clashes = []
    for name in target_names(store):
        owner = existing.get(name)
        if owner is None:
            continue                       # nothing has this name
        if owner == proj:
            continue                       # ours already - relaunching is fine
        clashes.append((name, owner or "a container created outside compose"))
    if not clashes:
        return True, ""
    lines = ", ".join("%s (owned by %s)" % (n, o) for n, o in clashes)
    return False, ("This cluster would need container names that already exist on this "
                   "host: %s. Nothing was started and nothing was changed. Rename this "
                   "cluster - the cluster ID is part of every container name - or remove "
                   "the containers that hold those names." % lines)

def other_ports_in_use(store):
    """Host ports held by everything except this cluster.

    The plan is asking "where would this cluster sit", and a cluster is allowed to keep
    the ports it is already on. Counting its own bindings as occupied made a launched
    cluster's plan drift to the next free pair, which reads as though something moved.
    """
    return dockerctl.ports_in_use(exclude_names=target_names(store))


def stop_one(store, map_key):
    """Stop a single map, leaving the rest of the cluster serving. (ok, message).

    `--no-deps` matters: the first map downloads the server files and every other
    service declares it as a dependency, so without it compose would happily start that
    first map in order to stop something else.
    """
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    rc, out = _compose(store, "stop", map_key, timeout=420)
    if rc != 0:
        return False, "could not stop %s: %s" % (map_key, out[-400:])
    return True, "stopped"


def start_one(store, map_key):
    """Bring a single map back up, without touching the others. (ok, message)."""
    ok, why = dockerctl.available()
    if not ok:
        return False, "Docker isn't reachable. %s" % why
    rc, out = _compose(store, "up", "-d", "--no-deps", map_key, timeout=420)
    if rc != 0:
        return False, "could not start %s: %s" % (map_key, out[-400:])
    return True, "started"


def wait_healthy(store, map_key, minutes=25, sleep=None, details=None):
    """(ok, detail) - block until this map reports healthy, or give up saying so.

    A map takes several minutes to load a world, so "it started" and "it is serving" are
    minutes apart. Anything that checks the second one immediately after doing the first
    is measuring the wrong thing.
    """
    import time as _time
    sleep = sleep or _time.sleep
    details = details or (lambda names: dockerctl.container_details(names))
    name = naming.container_name(project(store), map_key)
    # At least one look, always. int(minutes * 6) rounds a short timeout down to zero
    # iterations, so a caller asking for a quick check got "it did not report healthy"
    # without the container ever having been asked.
    # A map that was deliberately not started is not a map that is slow to start. The
    # integrity gate can now leave one down on purpose, and waiting twenty-five minutes
    # for a container nobody created would turn one refused map into a stalled batch.
    # Counted rather than decided on the first look, because a container that is being
    # created legitimately takes a moment to appear.
    missing = 0
    for _ in range(max(1, int(minutes * 6))):
        got = (details([name]) or {}).get(name, {})
        if got.get("health") == "healthy":
            return True, "healthy"
        if got.get("state") == "exited":
            return False, "the container exited while starting"
        missing = missing + 1 if not got else 0
        if missing >= ABSENT_POLLS:
            return False, ("there is no container for it - it was never started, or "
                           "Docker did not answer")
        sleep(10)
    return False, "it did not report healthy within %d minutes" % minutes


def verify_instance(store, map_key, rcon=None, details=None, logs=None):
    """(ok, reasons) - the six gates, as a function anything can call.

    The same checks the migration ran by hand on every cutover: a container can be
    `running` while the server inside it aborts in a loop, and a world can load
    perfectly well with its mods missing, which is the one that quietly ruins a save.
    """
    from . import bot, migrate, restore
    from . import maps as mapcat

    name = naming.container_name(project(store), map_key)
    details = details or (lambda names: dockerctl.container_details(names))
    logs = logs or (lambda n: dockerctl.logs(n, tail=4000))

    got = (details([name]) or {}).get(name, {})
    checks = {"running": got.get("state") == "running",
              "healthy": got.get("health") == "healthy"}

    port = None
    for _label, cname, rport in rcon_targets(store):
        if cname == name:
            port = rport
    answer = ""
    if rcon:
        answer = rcon(name, port)
    elif port:
        try:
            answer = run_coroutine(bot.rcon_with(name, port,
                                                 str(store.get("admin_password") or ""),
                                                 "ListPlayers", timeout=10)) or ""
        except Exception as e:                    # noqa: BLE001 - reported as a reason
            answer = "ERROR %s" % e
    checks["rcon"] = "No Players Connected" in answer or bool(
        answer and "ERROR" not in answer)

    map_id = (mapcat.entry(store, map_key) or {}).get("map_id") or map_key
    world = os.path.join(layout.ark_root_of(store),
                         layout.SAVED_ARKS.replace("/", os.sep), map_id,
                         "%s.ark" % map_id)
    ok_w, why_w = restore.verify_world(world)
    checks["save_present"] = ok_w

    text = logs(name) or ""
    expected = [m.strip() for m in str(store.get("mod_ids") or "").split(",") if m.strip()]
    loaded = []
    for m in re.findall(r"-mods=([0-9,]+)", text):
        loaded = [x for x in m.split(",") if x]

    ok, reasons = migrate.verify(map_key, dict(checks, mods_expected=expected,
                                               mods_loaded=loaded or expected))
    if not ok_w:
        reasons = [r for r in reasons if "world save" not in r] + \
                  ["the world on disk does not verify: %s" % why_w]
        ok = False
    if re.search(r"missing mod|failed to (load|download) mod", text, re.I):
        reasons.append("the log says a mod did not load")
        ok = False
    return ok, reasons


def join_network_if_running(store, environ=None, running=None):
    """Attach to the cluster network whenever there is a cluster to attach to.

    _join_network() was only ever called from launch(), which is fine right up until the
    manager is recreated without launching anything - an image update, a template edit,
    a host reboot. The cluster is already up, so nothing launches, so nothing joins, and
    the relay comes back reporting ten maps it cannot resolve.

    Called on every start. Joining a network twice is not an error, and there is nothing
    to do when no maps are running.
    """
    running = running if running is not None else running_instances(store)
    if not running:
        return False, "no cluster running, so nothing to join"
    return _join_network(store, environ)


def cluster_ready(store, details=None, probe=None):
    """(ready, why, total players) - is the WHOLE cluster up, healthy and answering?

    "Nobody is playing" is only a fact about a cluster that is actually running. It
    fired on a cluster where nine maps were still `Created` behind the island's health
    check and only the island was up: zero players across one map, three checks running,
    and an apply began on a cluster that had not finished starting. Of course it was
    empty - it had just come up.

    So every map this cluster defines has to exist, report running *and* healthy, and
    answer RCON before the count means anything. A map that is starting fails this the
    same way a silent one does, which is also what stops the window opening in the
    minutes after a restart: the cluster has to come all the way back first.
    """
    expected = target_names(store)
    if not expected:
        return False, "this cluster has no maps defined", 0
    details = details or dockerctl.container_details
    try:
        got = details(expected) or {}
    except Exception as e:                        # noqa: BLE001 - never raises upward
        return False, "could not ask Docker about the maps: %s" % e, 0

    missing = [n for n in expected if n not in got]
    if missing:
        return False, ("%d map(s) are not there yet: %s"
                       % (len(missing), ", ".join(_short(m) for m in missing[:4]))), 0
    unwell = [n for n in expected
              if got[n].get("state") != "running" or got[n].get("health") != "healthy"]
    if unwell:
        return False, ("%d map(s) are not up and healthy yet: %s"
                       % (len(unwell), ", ".join(
                           "%s (%s)" % (_short(n), got[n].get("health")
                                        or got[n].get("state") or "?")
                           for n in unwell[:4]))), 0

    total, counts, silent = players_online(store, probe=probe)
    if silent:
        return False, ("%d map(s) did not answer RCON: %s"
                       % (len(silent), ", ".join(l for l, _w in silent[:4]))), total
    if len(counts) != len(expected):
        return False, ("only %d of %d maps answered" % (len(counts), len(expected))), total
    return True, "all %d maps are up, healthy and answering" % len(expected), total


def _short(container_name):
    return str(container_name).rsplit("-", 1)[-1]


def players_online(store, probe=None, timeout=10.0):
    """(total, per-map counts, maps that did not answer).

    Asked of the servers rather than of Docker, because "the container is running" and
    "somebody is standing in it" are different questions and only the second one decides
    whether an update may restart the cluster. A map that does not answer is returned
    separately and never counted as empty - "we could not ask" is not "nobody is there",
    and treating it as such is how an update kicks the one person online.
    """
    from . import bot
    password = str(store.get("admin_password") or "")

    def ask(host, port):
        return run_coroutine(bot.rcon_with(host, port, password, "ListPlayers",
                                           timeout=timeout))

    probe = probe or ask
    counts, silent = {}, []
    for label, host, port in running_instances(store):
        try:
            counts[label] = bot.count_players(probe(host, port) or "")
        except Exception as e:                    # noqa: BLE001 - the reason is the point
            silent.append((label, str(e).strip() or e.__class__.__name__))
    return sum(counts.values()), counts, silent


def reachable(store, probe=None, timeout=6.0):
    """(reachable, unreachable) - which maps this container can actually talk to.

    Being told by Docker that ten containers exist is not the same as being able to
    reach them, and the relay used to conflate the two: it reported "covering 10 maps"
    from the container list while resolving none of them, and the only sign was a
    warning per map per poll. Coverage is a claim, so it gets checked.

    The check is players_online with the count thrown away - the same RCON fan-out over
    the same containers asking the same question. Keeping two of them meant two things
    to maintain that had already drifted on timeout for no stated reason. The shorter
    timeout stays: this runs on a two-minute loop and only needs to know whether the
    door opens.
    """
    _total, counts, bad = players_online(store, probe=probe, timeout=timeout)
    return list(counts), bad
