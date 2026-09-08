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


# ---------------------------------------------------------------- what to stage

# How many times to rehearse the same thing before leaving it alone, and how long to
# wait between tries. A rehearsal that fails will keep failing for the same reason -
# the build genuinely does not load, a mod is broken upstream - and retrying it every
# half hour is a 12 GB download and an 8 GB container, over and over, announcing the
# same failure into the channel each time. Three tries, spaced, then silence until the
# thing being staged actually changes.
PRIME_TRIES = 3
PRIME_BACKOFF = (0, 2 * 3600, 6 * 3600)


def target_key(status):
    """A fingerprint of what would be staged: the build, and every mod's newest file.

    Identity rather than a timestamp, because the question "is what we staged still the
    right thing" is answered by *what* it is, not when it happened. A mod publishing a
    new file makes a new target; nothing else does.
    """
    build = (status.get("build") or {}).get("latest")
    if not build:
        return ""
    mods = []
    for row in status.get("mods") or []:
        if not row.get("latest"):
            return ""          # an unknown makes the whole fingerprint a guess
        mods.append("%s=%s" % (row["id"], row["latest"]))
    return "%s|%s" % (build, ",".join(sorted(mods)))


def staged_target(store):
    """The fingerprint the staged, verified tree was proved against. "" if none."""
    ready = primed(store)
    return str((ready or {}).get("target") or "")


def needs_prime(store, status, now=None):
    """(should we stage this now, why).

    The guard against thrashing lives here rather than in the loop, so the reason is a
    sentence the UI and the log can both show - "tried three times" is a state somebody
    needs to be able to see, not a silent decision.
    """
    now = (now or time.time)()
    if not staging.enabled(store):
        return False, "the staging server is off"
    key = target_key(status)
    if not key:
        return False, ("not everything could be checked, so there is no telling what "
                       "to stage")
    if staged_target(store) == key:
        return False, "the staged tree already holds this, verified"

    # on_demand exists to cost nothing until there is something to do, so it waits for
    # an actual update. `always` is running anyway and its whole point is to be ahead,
    # so it stages whatever the current target is - including the first one, which warms
    # the tree and proves the path before anybody is relying on it.
    if staging.mode(store) == "on_demand" and not status.get("any_newer"):
        return False, "nothing newer, and the staging server only runs on demand"

    tried = (state(store).get("attempts") or {}).get(key) or {}
    count = int(tried.get("count") or 0)
    if count >= PRIME_TRIES:
        return False, ("this has already failed to stage %d times - it will be tried "
                       "again when the build or a mod changes" % count)
    wait = PRIME_BACKOFF[min(count, len(PRIME_BACKOFF) - 1)]
    last = float(tried.get("last") or 0)
    if last and now - last < wait:
        return False, ("waiting %d more minute(s) before trying again"
                       % int((wait - (now - last)) / 60))
    return True, "there is something newer to stage" if status.get("any_newer") else \
                 "nothing is staged yet"


def note_attempt(store, key, ok, now=None):
    """Remember that this target was tried, so a failure is not retried forever."""
    if not key:
        return
    now = (now or time.time)()
    attempts = dict(state(store).get("attempts") or {})
    if ok:
        attempts.pop(key, None)          # succeeded: nothing left to hold against it
    else:
        was = attempts.get(key) or {}
        attempts[key] = {"count": int(was.get("count") or 0) + 1, "last": now}
    remember(store, attempts=attempts)


# ---------------------------------------------------------------- detect

def look(store, ark_root, opener=None, listdir=None, read=None, source=None):
    """Current status, with the mod list the cluster is actually configured for.

    When the operator has given us a CurseForge key, the mod half of this comes from
    CurseForge itself in one authenticated request rather than seven to a third-party
    cache. This is the answer that decides whether a staging prime starts, so it is
    worth asking the source when we can.
    """
    from . import curseforge, layout
    serverfiles = layout.ark_paths(ark_root)["serverfiles"]
    if source is None and curseforge.has_key(store):
        source = lambda ids: curseforge.batch(store, ids)     # noqa: E731 - one line
    return arkupdate.status(serverfiles, store.get("mod_ids"), opener=opener,
                            listdir=listdir, read=read, source=source)


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
          minutes=45, read=None, listdir=None, now=None, target=None):
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

    fingerprint = target
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
    mods_ok, answered = False, False
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

        # ---- two things have to be true, and they are minutes apart
        #
        # The mods go valid early and the world is not up for a long time afterwards.
        # Breaking here the moment the mod lines appeared and asking RCON straight after
        # measured the wrong thing: on the run that found this, the mods were proved at
        # 17:01:35, this gave up at 17:02:04, and the server finished starting at
        # 17:03:43 - so a perfectly healthy staging boot was reported as never having
        # answered RCON, 99 seconds early. Which is "it started is not it is serving",
        # the same lesson wait_healthy exists for, walked into again.
        if not mods_ok:
            mods_ok, _problems, _detail = arkupdate.read_boot_log(log_of() or "", ids)
            if mods_ok:
                step("mods loaded - waiting for the world to finish loading")
        if mods_ok:
            answered = rcon_ok() if rcon_ok else _staging_answers(store)
            if answered:
                step("the staging server is serving")
                break

        # Give it one turn to appear before believing it is gone: `up` returns as soon as
        # Docker accepts the container, which is before it is running.
        if turn and not alive():
            died = True
            break
        wait(20)

    step("checking what it proved")
    text = log_of() or ""
    answered = answered and not died
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
              "target": str(fingerprint or ""),
              "mods": sorted(detail.get("loaded") or {})}
    remember(store, primed=result)
    # Booked whether it passed or not: the point of the record is to stop a rehearsal
    # that cannot succeed from being attempted every half hour forever.
    note_attempt(store, fingerprint, ok, now=now)

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

def apply_batch(store, ark_root, warn=None, save=None, stop_all=None, start_all=None,
                verify=None, players=None, on_step=None, force=False,
                rename=None, exists=None, now=None):
    """Apply everything that is waiting, in one restart. (ok, message, detail).

    Two kinds of thing wait for a safe moment - a build that has been staged and proved,
    and settings that cannot take effect without recreating the containers - and they
    are the same job. Applying them separately would stop ten servers twice for one
    decision, so this does both or neither.

    Every refusal happens before anything moves. The file swap is three renames and is
    its own inverse; the settings are committed from a snapshot that can be put back. A
    failure after either leaves the previous build *and* the previous configuration,
    which is the only state a cluster can safely be restarted into.
    """
    from . import pending

    step = on_step or (lambda text: None)
    now = now or time.time

    ready = primed(store)
    # Ownership gates the *files*, not the settings. A config change is Obelisk's
    # business whoever is applying builds, so refusing the whole batch over it would
    # mean the mod list could never be applied on a cluster where POK still owns updates.
    swap_files = bool(ready) and owns_updates(store)
    waiting = pending.count(store)

    if not swap_files and not waiting:
        if ready and not owns_updates(store):
            return False, ("a build is staged and verified, but POK is set to apply "
                           "updates itself - two update systems on one cluster is how "
                           "you get two restarts. Switch \"Who applies ARK updates\" to "
                           "Obelisk first."), {}
        return False, ("nothing is waiting to be applied - no settings queued, and no "
                       "update staged and verified"), {}

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

    build = (ready or {}).get("build")
    what = []
    if swap_files:
        what.append("ARK build %s" % build)
    if waiting:
        what.append("%d setting change(s)" % waiting)
    announce.say("ark.apply_start",
                 "Applying %s in one restart. Players are being warned, worlds saved, "
                 "then the cluster comes back." % " and ".join(what),
                 build=build if swap_files else "",
                 detail=pending.summary(store) if waiting else "")

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

    done = []
    if swap_files:
        step("swapping the staged files in")
        steps = staging.swap_steps(ark_root)
        ok, done, problem = staging.apply_steps(steps, rename=rename, exists=exists)
        if not ok:
            staging.undo(done, rename=rename, exists=exists)
            step("starting the cluster back on the previous build")
            start_all()
            announce.say("ark.update_failed",
                         "The swap failed and was undone; the cluster is starting again "
                         "on the previous build. %s" % problem, level="error")
            return False, problem, {"undone": True}

    # The settings go in between the swap and the start, because start_all regenerates
    # the compose file from the store - so this is the last moment they can land and
    # still be what the cluster comes up on.
    queue_was, before = pending.queued(store), None
    if waiting:
        step("applying %d setting change(s)" % waiting)
        ok_c, why_c, before = pending.commit(store)
        if not ok_c:
            staging.undo(done, rename=rename, exists=exists)
            step("starting the cluster back on the previous settings")
            start_all()
            announce.say("change.batch_failed",
                         "A queued setting would not apply, so nothing was changed and "
                         "the cluster is starting again as it was: %s" % why_c,
                         level="error", detail=_pending_lines(queue_was))
            return False, why_c, {"undone": True}

    def put_back(reason):
        """Everything this batch moved, moved back, before the cluster is restarted."""
        staging.undo(done, rename=rename, exists=exists)
        if before is not None:
            pending.restore(store, before, requeue=queue_was)
        step("starting the cluster back as it was")
        start_all()
        announce.say("change.batch_failed", reason, level="error")

    step("starting the cluster")
    ok, detail = start_all()
    if not ok:
        put_back("The cluster did not start, so the build and the settings were put "
                 "back and it is being started again as it was: %s" % detail)
        return False, "the cluster did not start: %s" % detail, {"undone": True}

    step("checking every map is really serving")
    gates = verify() if verify else (True, {})
    ok_gates, per_map = gates if isinstance(gates, tuple) else (True, {})

    if swap_files:
        remember(store, primed=None, applied={"build": build, "when": int(now()),
                                              "ok": bool(ok_gates)})
    if not ok_gates:
        bad = ", ".join(k for k, v in (per_map or {}).items() if not v)
        # A batch that failed its gates did not succeed, so its settings do not stay.
        # They did: `ark_update_mode` went from automatic to obelisk through a batch
        # that reported itself failed, because this branch returned without putting
        # anything back. A change that lands through a failure is a change nobody chose
        # the moment of - it goes back in the queue for a batch that works.
        if waiting and before is not None:
            pending.restore(store, before, requeue=queue_was)
            step("the settings were put back and requeued - this batch did not pass")
        announce.say("ark.update_failed" if swap_files else "change.batch_failed",
                     "%s did not pass verification after the restart, so the settings "
                     "were put back and are waiting again.%s"
                     % (bad or "Some maps",
                        (" The previous build is still on disk as ServerFiles.staging "
                         "if it has to go back." if swap_files else "")),
                     level="error", build=build if swap_files else "",
                     detail=_lines("%-14s %s" % (k, "passed" if v else "FAILED")
                                   for k, v in sorted((per_map or {}).items())))
        return False, "verification failed on: %s" % (bad or "some maps"), {
            "swapped": swap_files, "maps": per_map, "undone": bool(waiting)}

    announce.say("ark.update_applied" if swap_files else "change.applied",
                 "%s - every map passed verification." % " and ".join(what).capitalize(),
                 build=build if swap_files else "",
                 detail=_lines((["build %s applied" % build] if swap_files else []) +
                               _pending_lines(queue_was).splitlines() +
                               ["%-14s passed the six gates" % k
                                for k in sorted(per_map or {})]))
    return True, "applied: %s" % " and ".join(what), {"maps": per_map}


def _pending_lines(queue):
    """The batch's setting changes, one per line, for an announcement's detail."""
    out = ["%s = %s" % (k, v) for k, v in sorted((queue.get("cluster") or {}).items())]
    for m, values in sorted((queue.get("maps") or {}).items()):
        out += ["%s = %s  [%s]" % (k, v, m) for k, v in sorted(values.items())]
    for m, keys in sorted((queue.get("clears") or {}).items()):
        out += ["%s back to the cluster value  [%s]" % (k, m) for k in sorted(keys)]
    return _lines(out)


# apply_update was the original name and is what the update flow still calls it. The
# batch is the general case; an update with no queued settings is exactly the same
# routine with one half empty.
apply_update = apply_batch


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


def empty_enough(store, streak, needed=3):
    """(may we apply now, why) given how many consecutive polls found nobody.

    The debounce is the whole point. One poll returning zero is a moment, not a state -
    somebody is loading a map, somebody disconnected to swap servers - and restarting on
    it would kick the person who was about to be back. Consecutive empty polls are a
    cheap way of asking "is this cluster actually idle".
    """
    if not store.get("apply_when_empty"):
        return False, "applying when the cluster is empty is switched off"
    if streak < needed:
        return False, ("the cluster has been empty for %d of the %d checks needed"
                       % (streak, needed))
    return True, "the cluster has been empty for %d checks running" % streak


def due(store, now=None):
    """(due, why) - should the scheduler apply the staged update right now?

    Deliberately narrow. It fires only for an update that has been staged *and* proved,
    only while Obelisk owns updates, only inside the window, and only once per build -
    every one of those is a way for a scheduler to restart a cluster for no reason.
    """
    from . import pending

    ready = primed(store)
    waiting = pending.count(store)
    swap_files = bool(ready) and owns_updates(store)
    if not swap_files and not waiting:
        if ready and not owns_updates(store):
            return False, "POK applies updates on this cluster, not Obelisk"
        return False, "nothing staged and verified, and nothing queued"
    if not store.get("update_apply_in_window"):
        return False, "applying in the window is switched off"

    when = time.localtime(now() if now else time.time())
    if not in_window(store, when.tm_hour * 60 + when.tm_min):
        return False, "outside the update window"

    # A build already applied does not fire again the next night. Queued settings are
    # not subject to that: they are removed from the queue when they land, so their
    # absence is what stops them repeating.
    last = state(store).get("applied") or {}
    if swap_files and not waiting and             str(last.get("build") or "") == str(ready.get("build")):
        return False, "build %s has already been applied" % ready.get("build")
    what = []
    if swap_files:
        what.append("build %s is staged and verified" % ready.get("build"))
    if waiting:
        what.append("%d setting change(s) are queued" % waiting)
    return True, "%s and the window is open" % " and ".join(what)
