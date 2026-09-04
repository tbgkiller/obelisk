"""
Status has to tell the truth, especially when things are going wrong.

The failure this suite exists for: a container aborting and restarting every seventeen
seconds reported "running (health: starting)" - the same words it uses during a normal
twelve-gigabyte first start. Green for both is worse than no status at all.

Log lines below are verbatim from a real acekorneya/asa_server first start.
"""

import sys

from . import progress

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


CRASH = """[INFO] MASTER starting startup-install coordination cycle for shared server files
[ERROR] Unable to begin the coordination cycle for startup installation
Server install/update helper exited with status 1
   Aborting startup to avoid running with inconsistent files.
Random startup delay enabled. Waiting 10 seconds before proceeding..."""

DOWNLOADING = """[INFO] Downloading ARK server files to temporary directory
 Update state (0x61) downloading, progress: 32.57 (3976028625 / 12206302160)"""

# ---- phases, from the real markers
phase, pct, fail = progress.read_log(DOWNLOADING)
check("a download reports its phase", phase == "Downloading server files", phase)
check("and the real percentage", pct == 32.6, pct)
check("with no failure", fail is None)

for line, want in [
        ("Update state (0x5) verifying install, progress: 78.10 (1/2)", "Checking existing files"),
        ("Update state (0x11) preallocating, progress: 4.44 (1/2)", "Reserving disk space"),
        ("Update state (0x101) committing, progress: 100.00 (1/2)", "Finishing the install"),
        ("Success! App '2430930' fully installed.", "Server files installed"),
        ("[INFO] ARK Server process detected with PID: 387", "Starting the world"),
        ("[INFO] Waiting for server to complete initialization (0s elapsed)", "Generating the world")]:
    got = progress.read_log(line)[0]
    check("phase for %r" % want, got == want, got)

check("an unknown log is not given a made-up percentage",
      progress.read_log("something nobody has seen before") == (None, None, None))

# ---- the crash loop, which is the point
phase, pct, fail = progress.read_log(CRASH)
check("a crash log yields a failure", fail is not None, fail)
check("and names the fixable cause, not the symptom",
      "coordination folder" in fail and "writable" in fail, fail)
check("the most specific reason wins over the later, vaguer one",
      "stopped itself" not in fail, fail)

check("a permission failure is recognised on its own",
      progress.read_log("mkdir: cannot create directory: Permission denied")[2] is not None)
check("a full disk is recognised",
      "disk is full" in (progress.read_log("No space left on device")[2] or ""))

# ---- loop detection
check("restarts plus tiny uptime is a loop", progress.looks_like_a_loop(4, 5))
check("uptime that never climbs is a loop", progress.looks_like_a_loop(1, 17))
check("a long first start is NOT a loop", progress.looks_like_a_loop(0, 900) is False)
check("a restarted-but-now-stable container is not a loop",
      progress.looks_like_a_loop(2, 3600) is False)
check("a climbing restart count is a loop even when young",
      progress.looks_like_a_loop(3, 200, previous_restarts=2))

# ---- what the UI is told
level, says = progress.describe(
    {"state": "running", "looping": True,
     "failure": "The server could not create its coordination folder."})
check("a looping container is never 'ok'", level == "bad", level)
check("and says it is failing, not starting", "Failing to start" in says, says)
check("and carries the reason", "coordination folder" in says, says)

level, says = progress.describe(
    {"state": "running", "health": "starting", "phase": "Downloading server files",
     "percent": 32.6, "uptime_seconds": 400})
check("a real download is 'busy', not 'bad'", level == "busy", level)
check("and shows progress", "32.6%" in says, says)
check("and shows elapsed time so it is obviously alive", "6m so far" in says, says)

level, says = progress.describe({"state": "running", "health": "healthy"})
check("a healthy server is 'ok'", level == "ok" and says == "Online", (level, says))

level, says = progress.describe({"state": "exited", "failure": "The disk is full."})
check("an exited container is 'bad'", level == "bad", level)
check("with its reason", "disk is full" in says, says)

level, says = progress.describe({"state": "running", "health": "starting",
                                 "uptime_seconds": 30})
check("an unrecognised phase still shows it is alive",
      level == "busy" and "Starting up" in says and "30s" in says, says)

print("\nFAILURES: %s" % fails if fails else "\nall progress tests passed")
sys.exit(1 if fails else 0)
