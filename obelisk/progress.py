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

# Lines that mean something went wrong, worst first. Each maps to what to tell a person.
_FAILURES = [
    (re.compile(r"Unable to begin the coordination cycle"),
     "The server could not create its coordination folder. This is almost always the "
     "data folder not being writable by the server's user."),
    (re.compile(r"Aborting startup to avoid running with inconsistent files"),
     "The server stopped itself rather than start with a half-finished install."),
    (re.compile(r"install/update helper exited with status [1-9]"),
     "The install step failed, so the server refused to start."),
    (re.compile(r"[Pp]ermission denied"),
     "Something the server needs to write to is not writable by it."),
    (re.compile(r"No space left on device"),
     "The disk is full."),
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
    if failure and state != "running":
        return "bad", failure
    if state != "running":
        return "busy", state or "unknown"

    if health == "healthy":
        return "ok", "Online"

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
