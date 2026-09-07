"""
Detect an ARK update, rehearse it, then apply it when a person says so.

The order matters more than any of the individual steps. What the server image does
today is fetch a new build and restart into it inside the update window, with nobody
told and nothing checked - and a build that will not load with your mods is discovered
by ten maps failing to come back, at four in the morning. This turns that around:
something rehearses it first, and the restart only happens against a combination that
has already been proved to boot.

  detect  - what is installed against what is published, and never "current" for a
            question that was not answered
  prime   - the staging server downloads the build and fetches the mods, on its own
            files, while the cluster keeps serving
  verify  - the staging server's own log says every mod loaded, off the right build
  apply   - warn, save, stop, swap the trees, start, and put every map through the six
            gates before calling it done

**Two update systems must never both be running.** POK applies updates itself when
UPDATE_SERVER is TRUE, so a cluster where Obelisk also applies them has two schedulers
with different rules and no way to predict which one moves first. Rather than lock
against it, ownership is a setting: `ark_update_mode` writes UPDATE_SERVER into the
compose file, and Obelisk refuses to apply anything while POK owns the job. One of them
is in charge, it is written down, and the UI says which.
"""

import logging
import re
import time

from . import announce, arkupdate, staging

log = logging.getLogger("obelisk.updates")

STATE = "ark_update"          # store.data[STATE] - manager state, not a setting


def _lines(parts):
    """Join for an event's `detail`. The feed renders it as a block; Discord never
    sees it."""
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------- state

def state(store):
    got = store.data.get(STATE)
    return dict(got) if isinstance(got, dict) else {}


def remember(store, **changes):
    """Persist across restarts. A prime that survives a manager restart is the whole
    point of priming ahead of a scheduled window - it is usually hours in between."""
    got = state(store)
    got.update(changes)
    store.data[STATE] = got
    try:
        store.save()
    except OSError as e:
        log.warning("could not record update state: %s", e)
    return got


def primed(store):
    """The staged update, if there is one that actually passed. None otherwise.

    Verified is part of the definition on purpose. A staged-but-failed update is not a
    thing to apply later, and keeping it under the same name as a good one is how a
    scheduled window comes round and applies the thing that was proved broken.
    """
    got = state(store).get("primed")
    if isinstance(got, dict) and got.get("ok"):
        return got
    return None


def owns_updates(store):
    return str(store.get("ark_update_mode") or "automatic") == "obelisk"


# ---------------------------------------------------------------- detect

def look(store, ark_root, opener=None, listdir=None, read=None):
    """Current status, with the mod list the cluster is actually configured for."""
    from . import layout
    serverfiles = layout.ark_paths(ark_root)["serverfiles"]
    return arkupdate.status(serverfiles, store.get("mod_ids"), opener=opener,
                            listdir=listdir, read=read)


def announce_new(store, status):
    """Say something once per new build or mod file id, not once per poll.

    A watcher that runs every few hours and an update that sits unapplied for days is a
    channel full of the same line. What is new is the *version*, so that is what is
    remembered.
    """
    seen = state(store).get("announced") or {}
    fresh = {}

    build = status.get("build") or {}
    if build.get("newer") and str(seen.get("build") or "") != str(build.get("latest")):
        fresh["build"] = str(build.get("latest"))
        announce.say("ark.update_available",
                     "A new ARK server build is out: %s (running %s)."
                     % (build.get("latest"), build.get("running")),
                     running=build.get("running"), available=build.get("latest"))

    for row in status.get("mods_newer") or []:
        key = "mod:%s" % row["id"]
        if str(seen.get(key) or "") != str(row.get("latest")):
            fresh[key] = str(row.get("latest"))
            announce.say("ark.mod_update_available",
                         "%s has a new version on CurseForge (running %s, latest %s)."
                         % (row["name"], row.get("running"), row.get("latest")),
                         mod=row["id"])

    if fresh:
        remember(store, announced=dict(seen, **fresh))
    return fresh


# ---------------------------------------------------------------- prime + verify

def prime(store, ark_root, on_step=None, up=None, down=None, alive=None,
          log_of=None, container_log=None, rcon_ok=None, opener=None, wait=None,
          minutes=45, read=None, listdir=None, now=None):
    """Stage an update on the staging server and grade the result. (ok, message, detail).

    The cluster is not touched at any point here - the staging server has its own server
    files, its own shared folder and its own world. This can run with players on.
    """
    step = on_step or (lambda text: None)
    now = now or time.time
    up = up or (lambda: staging.up(store))
    down = down or (lambda: staging.down(store))
    log_of = log_of or (lambda: staging.boot_log(store, ark_root))
    container_log = container_log or (lambda: staging.container_log(store))
    alive = alive or (lambda: staging.is_running(store)[0])
    wait = wait or time.sleep

    if not staging.enabled(store):
        return False, "the staging server is turned off, so there is nothing to prime", {}

    target, problem = arkupdate.available_build(opener=opener)
    if not target:
        # Refusing here rather than staging something and calling it "the latest".
        return False, "could not find out what the current build is: %s" % problem, {}

    announce.say("ark.prime_start",
                 "Staging build %s with the cluster's mods. Nothing on the cluster "
                 "stops - the staging server has its own copy of everything." % target,
                 build=target)
    step("starting the staging server")
    ok, message = up()
    if not ok:
        announce.say("ark.prime_failed", message, level="error")
        return False, message, {}

    # It downloads ~12 GB and then generates a world, so this is tens of minutes. The
    # thing being waited for is the log saying the mods loaded, not the container
    # saying it started - those are a long way apart and only the second one is proof.
    ids = store.get("mod_ids")
    staged = staging.paths(ark_root)["staged"]
    # Counted rather than clocked, the same way wait_healthy is. A loop whose only exit
    # is "the wall clock passed a deadline" spins forever the moment anything hands it a
    # clock that does not move, and the first thing to do that was this module's own
    # test - which is a cheap way to find out that nothing else bounded it.
    last, install_failure, died = "", None, False
    for turn in range(max(1, int(minutes * 3))):
        # Two logs, because only one of them exists at a time. The container's own output
        # covers the download and the file sync; ShooterGame.log does not exist until the
        # game itself starts. Watching only the second one meant a staging server that
        # died during install looked identical to one still downloading, and this sat
        # here for the full timeout on a container that had been dead for minutes.
        outer = container_log() or ""
        phase, percent, failure = _progress(outer)
        if phase and outer != last:
            last = outer
            step(phase if percent is None else "%s (%.0f%%)" % (phase, percent))
        if failure:
            install_failure = failure
            break

        text = log_of() or ""
        done, _problems, _detail = arkupdate.read_boot_log(text, ids)
        if done:
            break

        # Give it one turn to appear before believing it is gone: `up` returns as soon as
        # Docker accepts the container, which is before it is running.
        if turn and not alive():
            died = True
            break
        wait(20)

    step("checking what it proved")
    text = log_of() or ""
    answered = False if died else (rcon_ok() if rcon_ok else _staging_answers(store))
    ok, problems, detail = staging.verify(staged, text, ids, rcon_ok=answered,
                                          target_build=target, read=read)
    # Put the real reason first. Without it the verdict is a list of mods that never
    # loaded, which is true and useless - the operator needs the sentence about why the
    # server never got as far as loading anything.
    if install_failure:
        problems = [install_failure] + list(problems)
    elif died:
        problems = ["the staging server stopped before it finished starting - its log "
                    "is the place to look"] + list(problems)
    if install_failure or died:
        ok = False

    result = {"ok": ok, "build": target, "loaded": detail.get("loaded") or {},
              "problems": problems, "when": int(now()),
              "mods": sorted(detail.get("loaded") or {})}
    remember(store, primed=result)

    if ok:
        announce.say("ark.update_primed",
                     "Build %s is staged and verified: it booted on the staging server "
                     "with all %d mod(s) loaded. Apply it whenever you like."
                     % (target, len(result["loaded"])),
                     build=target,
                     mods=",".join("%s=%s" % (m, f) for m, f
                                   in sorted(result["loaded"].items())),
                     detail="\n".join(
                         ["build %s staged and proved by a real boot" % target] +
                         ["mod %s loaded from file %s" % (m, f)
                          for m, f in sorted(result["loaded"].items())]))
    else:
        # Discord gets the first three problems; the feed gets every one of them, plus
        # the tail of the log they came out of. This is the case where "go and read
        # Discord" is least useful and the difference matters most.
        announce.say("ark.update_unsafe",
                     "Build %s is NOT safe to apply - the staging server did not come "
                     "up cleanly: %s" % (target, "; ".join(problems[:3])),
                     level="error", build=target,
                     detail="\n".join(problems +
                                      ["", "--- last lines from the staging server ---"] +
                                      (container_log() or "").splitlines()[-25:]))

    if staging.mode(store) == "on_demand":
        step("stopping the staging server")
        down()
    return ok, staging.summary(result), result


def _progress(text):
    from . import progress
    return progress.read_log(text)


def _staging_answers(store, probe=None):
    """Did the staging server answer RCON? Asked over the cluster network by name."""
    from . import bot, cluster, naming
    name = staging.container_name(cluster.project(store))
    password = str(store.get("admin_password") or "")
    probe = probe or (lambda: cluster.run_coroutine(
        bot.rcon_with(name, 27020, password, "ListPlayers", timeout=15)))
    try:
        probe()
    except Exception as e:                        # noqa: BLE001
        log.info("the staging server did not answer RCON: %s", e)
        return False
    # Any answer at all is the proof wanted here. RCON does not respond until the world
    # is loaded, so "it replied" and "it finished starting" are the same fact.
    return True


# ---------------------------------------------------------------- apply

def apply_update(store, ark_root, warn=None, save=None, stop_all=None, start_all=None,
                 verify=None, players=None, on_step=None, force=False,
                 rename=None, exists=None, now=None):
    """Make the staged build live. (ok, message, detail).

    Every refusal below happens before anything moves. The swap itself is three renames
    and is its own inverse, so a failure part way is put back rather than left half
    applied - and the previous build stays on disk either way.
    """
    step = on_step or (lambda text: None)
    now = now or time.time

    if not owns_updates(store):
        return False, ("POK is set to apply updates itself, so Obelisk will not - two "
                       "update systems on one cluster is how you get two restarts. "
                       "Switch \"Who applies ARK updates\" to Obelisk first."), {}

    ready = primed(store)
    if not ready:
        return False, ("nothing has been staged and verified yet - prime an update "
                       "first, so the restart is into something known to boot"), {}

    if not force:
        total, counts, silent = (players or (lambda: (0, {}, [])))()
        if silent:
            return False, ("%d map(s) did not answer, so it is not known whether "
                           "anyone is on them: %s. Apply with force if you mean to "
                           "restart anyway." % (len(silent),
                                                ", ".join(l for l, _ in silent))), {}
        if total:
            busiest = ", ".join("%s (%d)" % (m, n) for m, n in sorted(
                counts.items(), key=lambda kv: -kv[1]) if n)
            return False, ("%d player(s) are online: %s. Apply with force, or let the "
                           "scheduled window do it." % (total, busiest)), {}

    build = ready.get("build")
    announce.say("ark.apply_start",
                 "Applying ARK build %s. Players are being warned, worlds saved, then "
                 "the cluster restarts onto the staged files." % build, build=build)

    minutes = int(store.get("restart_notice_minutes") or 0)
    if warn and minutes:
        step("warning players (%d minutes)" % minutes)
        warn(minutes, build)

    if save:
        step("saving every world")
        ok, detail = save()
        if not ok:
            # Not fatal by itself - a map that is down cannot save and should not block
            # the update - but it is said out loud rather than swallowed.
            announce.say("ark.apply_note", "SaveWorld: %s" % detail, level="warning")

    step("stopping the cluster and the staging server")
    ok, detail = stop_all()
    if not ok:
        announce.say("ark.update_failed", "Could not stop the cluster: %s" % detail,
                     level="error")
        return False, "could not stop the cluster: %s" % detail, {}

    step("swapping the staged files in")
    steps = staging.swap_steps(ark_root)
    ok, done, problem = staging.apply_steps(steps, rename=rename, exists=exists)
    if not ok:
        staging.undo(done, rename=rename, exists=exists)
        step("starting the cluster back on the previous build")
        start_all()
        announce.say("ark.update_failed",
                     "The swap failed and was undone; the cluster is starting again on "
                     "the previous build. %s" % problem, level="error")
        return False, problem, {"undone": True}

    step("starting the cluster")
    ok, detail = start_all()
    if not ok:
        announce.say("ark.update_failed",
                     "The files were swapped but the cluster did not start: %s" % detail,
                     level="error")
        return False, "swapped, but the cluster did not start: %s" % detail, {
            "swapped": True}

    step("checking every map is really serving")
    gates = verify() if verify else (True, {})
    ok_gates, per_map = gates if isinstance(gates, tuple) else (True, {})

    remember(store, primed=None, applied={"build": build, "when": int(now()),
                                          "ok": bool(ok_gates)})
    if not ok_gates:
        bad = ", ".join(k for k, v in (per_map or {}).items() if not v)
        announce.say("ark.update_failed",
                     "Build %s is live but %s did not pass verification. The previous "
                     "build is still on disk as ServerFiles.staging if it has to go "
                     "back." % (build, bad or "some maps"), level="error", build=build,
                     detail=_lines("%-14s %s" % (k, "passed" if v else "FAILED")
                                   for k, v in sorted((per_map or {}).items())))
        return False, "applied, but verification failed on: %s" % (bad or "some maps"), {
            "swapped": True, "maps": per_map}

    announce.say("ark.update_applied",
                 "ARK build %s is live and every map passed verification." % build,
                 build=build,
                 detail=_lines(["build %s applied" % build] +
                               ["%-14s passed the six gates" % k
                                for k in sorted(per_map or {})]))
    return True, "build %s applied and verified" % build, {"maps": per_map}


# ---------------------------------------------------------------- schedule

_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp])?[Mm]?\s*$")


def parse_time(value):
    """Minutes past midnight from "4:00 AM" or "16:30". None if it is not a time."""
    m = _TIME.match(str(value or ""))
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if half == "p" and hour != 12:
        hour += 12
    elif half == "a" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def in_window(store, now_minutes):
    """Is the clock inside the update window? Windows that cross midnight count."""
    start = parse_time(store.get("update_window_start"))
    end = parse_time(store.get("update_window_end"))
    if start is None or end is None:
        return False
    if start <= end:
        return start <= now_minutes <= end
    return now_minutes >= start or now_minutes <= end


def due(store, now=None):
    """(due, why) - should the scheduler apply the staged update right now?

    Deliberately narrow. It fires only for an update that has been staged *and* proved,
    only while Obelisk owns updates, only inside the window, and only once per build -
    every one of those is a way for a scheduler to restart a cluster for no reason.
    """
    if not owns_updates(store):
        return False, "POK applies updates on this cluster, not Obelisk"
    ready = primed(store)
    if not ready:
        return False, "nothing staged and verified"
    if not store.get("update_apply_in_window"):
        return False, "applying in the window is switched off"

    when = time.localtime(now() if now else time.time())
    if not in_window(store, when.tm_hour * 60 + when.tm_min):
        return False, "outside the update window"

    last = state(store).get("applied") or {}
    if str(last.get("build") or "") == str(ready.get("build")):
        return False, "build %s has already been applied" % ready.get("build")
    return True, "build %s is staged, verified and the window is open" % ready.get("build")
