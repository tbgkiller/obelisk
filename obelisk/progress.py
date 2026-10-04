"""
What the server is actually doing, in words a person can act on.

A first start takes a long time: twelve gigabytes of game files, then a world to
generate. For all of it the container says `running (health: starting)`, which is the
same thing it says when the server is aborting and restarting every seventeen seconds.
Green for both is worse than no status at all - it is a status that lies.

So two jobs here. Turn the log into a phase with progress where the numbers exist, and
notice when a container is failing rather than working. The markers below are taken from
a real first start of acekorneya/asa_server, not from documentation or guesswork:

    Update state (0x61) downloading, progress: 32.57 (3976028625 / 12206302160)
    Update state (0x5) verifying install, progress: 78.10 (...)
    Update state (0x11) preallocating, progress: 4.44 (...)
    Update state (0x101) committing, progress: 100.00 (...)
    Success! App '2430930' fully installed.
    [ERROR] Unable to begin the coordination cycle for startup installation
    Server install/update helper exited with status 1

A phase that cannot be recognised is reported as "starting up" with an elapsed timer,
never as a percentage nobody measured.
"""

import re

# steamcmd's own state machine, in the order it runs.
_STATE = re.compile(r"Update state \(0x[0-9a-f]+\) ([a-z ]+), progress: ([0-9.]+)")

_STATE_WORDS = {
    "verifying install": "Checking existing files",
    "preallocating": "Reserving disk space",
    "downloading": "Downloading server files",
    "committing": "Finishing the install",
}

# Lines that mean something went wrong. **Causes first, consequences last** - the first
# match wins, so this order is the whole behaviour.
#
# It was not in that order. "Aborting startup to avoid running with inconsistent files"
# sat above "Permission denied", and both appear in the same failed start: the server
# cannot write, so it aborts. Reporting the abort told the operator the server had
# stopped itself, which they could see, instead of that a folder was not writable, which
# they could fix. A live staging server failed exactly this way and the message named
# the wrong half.
_FAILURES = [
    (re.compile(r"Unable to begin the coordination cycle"),
     "The server could not create its coordination folder. This is almost always the "
     "data folder not being writable by the server's user."),
    (re.compile(r"Failed to create directory .*\(check permissions\)"),
     "The server could not create a folder inside its own install. That folder exists "
     "and is owned by root - usually because Docker made it, which it does for any "
     "bind-mount destination that is missing when the container starts."),
    (re.compile(r"[Pp]ermission denied"),
     "Something the server needs to write to is not writable by it."),
    (re.compile(r"No space left on device"),
     "The disk is full."),
    (re.compile(r"Failed to sync temporary download into live server directory"),
     "The new server files downloaded, but could not be copied into place."),
    (re.compile(r"Aborting startup to avoid running with inconsistent files"),
     "The server stopped itself rather than start with a half-finished install."),
    (re.compile(r"install/update helper exited with status [1-9]"),
     "The install step failed, so the server refused to start."),
    # A map that waits for the download will not touch the shared game files; it waits
    # for the map that owns that job. If that map is not running - and during a rolling
    # migration it may not have moved yet - the wait never ends. It is not a slow start,
    # and reporting it as one costs twenty minutes before anyone finds out the server
    # was never going to come up.
    (re.compile(r"FOLLOWER waiting for configured master"),
     "A new server build is out, and this map is waiting for the map that downloads "
     "first to fetch it (the server's own log calls that map the MASTER). That map is "
     "not running, so the wait will not end. Start the map that downloads first, or "
     "pre-stage the new build before this map starts."),
]

_MARKERS = [
    (re.compile(r"Success! App '\d+' fully installed"), "Server files installed", None),
    (re.compile(r"ARK Server process detected with PID"), "Starting the world", None),
    (re.compile(r"Waiting for server to complete initialization"), "Generating the world", None),
    (re.compile(r"Server log file not created yet"), "Generating the world", None),
    (re.compile(r"Proton: Upgrading prefix"), "Preparing the runtime", None),
    (re.compile(r"Downloading ARK server files"), "Downloading server files", None),
    (re.compile(r"waiting .*coordination|wait_for_coordination"), "Waiting for another map to finish downloading", None),
]


def read_log(text, tail=400):
    """(phase, percent, failure) from the tail of a container log.

    `percent` is None unless the log actually reported one. `failure` is a sentence
    about what went wrong, or None.
    """
    lines = [l for l in str(text or "").splitlines() if l.strip()][-tail:]

    # Patterns are ordered most-specific first, and the most specific wins even when a
    # vaguer line came later. "Aborting startup" is the consequence; "could not create
    # its coordination folder" is the thing a person can actually fix.
    failure = None
    for pattern, message in _FAILURES:
        if any(pattern.search(l) for l in lines):
            failure = message
            break

    phase, percent = None, None
    for line in reversed(lines):
        m = _STATE.search(line)
        if m:
            phase = _STATE_WORDS.get(m.group(1).strip(), "Installing")
            try:
                percent = round(float(m.group(2)), 1)
            except ValueError:
                percent = None
            break
        hit = False
        for pattern, word, _ in _MARKERS:
            if pattern.search(line):
                phase, hit = word, True
                break
        if hit:
            break

    return phase, percent, failure


def looks_like_a_loop(restarts, uptime_seconds, previous_restarts=None):
    """Is this container failing over and over rather than working?

    Uptime that never climbs is the signal a person actually sees - "Up 5 seconds"
    that is still "Up 5 seconds" a minute later. A container that has restarted and is
    only ever a few seconds old is looping, whatever its health check says.
    """
    if restarts and restarts > 0 and uptime_seconds is not None and uptime_seconds < 90:
        return True
    if previous_restarts is not None and restarts > previous_restarts:
        return True
    return False


def describe(service):
    """One line of human status for a service dict from cluster.status().

    Returns (level, text) where level is "ok", "busy" or "bad" - the UI colours from
    that, and it must never be "ok" for a container that is failing.
    """
    state = (service.get("state") or "").lower()
    health = (service.get("health") or "").lower()
    phase = service.get("phase")
    percent = service.get("percent")
    failure = service.get("failure")
    elapsed = service.get("uptime_seconds")

    if service.get("looping"):
        return "bad", ("Failing to start - it keeps restarting. %s"
                       % (failure or "See the log below for why."))
    if state in ("exited", "dead"):
        return "bad", ("Stopped unexpectedly. %s" % (failure or "See the log below."))
    if state == "running" and health == "healthy":
        return "ok", "Online"
    # A recognised failure is a failure whether or not the container is still running.
    # Every pattern above describes a process that is up and stuck - an unwritable data
    # folder, a full disk, a map waiting on a download that will never start - and
    # requiring the container to have exited first meant all of them were reported as
    # "Starting up", which is the exact lie this module exists to prevent.
    if failure:
        return "bad", failure
    if state != "running":
        return "busy", state or "unknown"

    bits = phase or "Starting up"
    if percent is not None:
        bits += " - %.1f%%" % percent
    if elapsed:
        bits += " (%s so far)" % _mins(elapsed)
    return "busy", bits


def _mins(seconds):
    seconds = int(seconds or 0)
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%dm" % (seconds // 60)
    return "%dh %dm" % (seconds // 3600, (seconds % 3600) // 60)


# ---------------------------------------------------------------- what it said last
#
# The phase is a word; the last line is the evidence. During the 4 October update every
# map read "Generating the world (Nm so far)" for a quarter of an hour, which is true and
# says nothing about whether anything is moving - while the container log was printing
# "Server log file not created yet... (510s elapsed)" and the game log "Log file open".
# The line itself is what tells a slow start from a stuck one, so it is shown.

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# "[2026.10.04-12.00.00:123][  0]" (the game's own log) and "2026-10-04 12:00:00 " /
# "[12:00:00]" (POK's wrapper) - the clock is already on the page as elapsed time.
_STAMP = re.compile(r"^(\[[\d.:\-]+\]\s*(\[\s*\d+\])?\s*|\d{4}-\d{2}-\d{2}[ T][\d:.,]+\s*"
                    r"|\[?\d{2}:\d{2}:\d{2}\]?\s*)")
# Lines that are true and useless: separators, blank banners, steamcmd's spinner.
_NOISE = re.compile(r"^[-=*#_.\s]*$|^Redirecting stderr|^Loading Steam API")


def last_line(text, width=180):
    """The last line of a log that says something, cleaned for a table cell."""
    for raw in reversed(str(text or "").splitlines()):
        line = _STAMP.sub("", _ANSI.sub("", raw)).strip()
        if not line or _NOISE.search(line):
            continue
        return line if len(line) <= width else line[:width - 1] + "…"
    return ""


_FULL_STARTUP = re.compile(r"Full Startup:\s*([0-9.]+)\s*seconds")


def full_startup_seconds(text):
    """The game's own "Full Startup: 932.35 seconds", or None if it has not said it."""
    hits = _FULL_STARTUP.findall(str(text or ""))
    try:
        return float(hits[-1]) if hits else None
    except ValueError:
        return None


# path -> ((mtime, size), seconds). A finished log never changes, so it is read once.
_STARTUP_CACHE = {}


def previous_startup(logs_dir, listdir=None, stat=None, read=None, limit=6,
                     max_bytes=64 * 1024 * 1024):
    """How long this map's last completed start took, from its own logs. Or None.

    ASA keeps the previous runs' logs beside the current one, and each completed start
    wrote its own "Full Startup" line - so the expectation is this map's measured
    history, not a number somebody guessed. Newest first; the first log that has the
    line wins.
    """
    import os
    listdir = listdir or os.listdir
    stat = stat or os.stat
    read = read or (lambda p: open(p, encoding="utf-8", errors="replace").read())
    try:
        names = [n for n in listdir(logs_dir)
                 if n.startswith("ShooterGame") and n.endswith(".log")]
    except OSError:
        return None
    found = []
    for n in names:
        path = "%s/%s" % (str(logs_dir).rstrip("/"), n)
        try:
            st = stat(path)
        except OSError:
            continue
        found.append((st.st_mtime, st.st_size, path))
    for mtime, size, path in sorted(found, reverse=True)[:limit]:
        key = (mtime, size)
        cached = _STARTUP_CACHE.get(path)
        if cached and cached[0] == key:
            secs = cached[1]
        elif size > max_bytes:
            continue
        else:
            try:
                secs = full_startup_seconds(read(path))
            except OSError:
                continue
            _STARTUP_CACHE[path] = (key, secs)
        if secs:
            return secs
    return None


# What a normal start looks like, said so a slow one does not read as a stuck one.
# Measured, not assumed: ASA under Proton took six to sixteen minutes a map with ten
# starting at once on the cluster this was written against (The Center 932s, The
# Island 351s on 2 October).
TYPICAL = "6-16 min per map is normal for ASA under Proton when several maps start together"


def expectation(elapsed, previous=None, phase=None):
    """One sentence on whether this boot is in the normal range. "" when not useful."""
    if not elapsed or elapsed < 60:
        return ""
    if previous:
        if elapsed > max(previous * 1.5, previous + 300):
            return ("Taking longer than its last start (%s) - check the line above is "
                    "still changing." % _mins(previous))
        return "Its last full start took %s; %s." % (_mins(previous), TYPICAL)
    return TYPICAL[0].upper() + TYPICAL[1:] + "."
