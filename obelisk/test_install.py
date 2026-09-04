"""
Install-time derivation: the operator sets the path once and the port once.

Fixture values here are synthetic. The one real value anywhere in the suites is mod id
929110, which the product checks by name for stacking-mod load order.
"""

import os, sys, tempfile

from .install import (CONTAINER_PORT, candidate_mounts, derive_appdata,
                      derive_status_port, mount_points, apply_timezone)
from .firstrun import bootstrap

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


# A realistic mountinfo: the OS, Obelisk's store, the socket, and one cluster mount.
MOUNTINFO = """\
21 30 0:20 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw
22 30 0:21 / /sys rw,nosuid,nodev,noexec,relatime - sysfs sysfs rw
23 30 0:22 / /dev rw,nosuid - tmpfs tmpfs rw
30 29 0:31 / / rw,relatime - overlay overlay rw
41 30 0:35 /obelisk /data rw,relatime - ext4 /dev/sdb1 rw
42 30 0:35 /ark /srv/ark-data rw,relatime - ext4 /dev/sdb1 rw
43 30 0:36 /docker.sock /var/run/docker.sock rw,relatime - ext4 /dev/sdb1 rw
44 30 0:37 / /etc/hostname rw,relatime - ext4 /dev/sdb1 rw
"""

# ---- reading mountinfo
check("reads every mount point", "/data" in mount_points(MOUNTINFO))
check("handles escaped spaces",
      mount_points("1 2 0:3 / /mnt/my\\040pool rw - ext4 /dev/x rw") == ["/mnt/my pool"])
check("ignores short lines", mount_points("garbage\n") == [])

# ---- picking the cluster mount out of the noise
cands = candidate_mounts(MOUNTINFO)
check("finds the operator's mount", "/srv/ark-data" in cands, cands)
check("excludes Obelisk's own store", "/data" not in cands, cands)
check("excludes the docker socket", "/var/run/docker.sock" not in cands, cands)
check("excludes the OS", not any(c.startswith(("/proc", "/sys", "/dev", "/etc")) for c in cands), cands)
check("excludes the root filesystem", "/" not in cands, cands)

# a shallower mount wins over something nested inside it
deep = MOUNTINFO + "45 30 0:35 /x /srv/ark-data/Saved/deep rw - ext4 /dev/sdb1 rw\n"
check("prefers the shallower path", candidate_mounts(deep)[0] == "/srv/ark-data",
      candidate_mounts(deep))

# ---- APPDATA: derived, unless given
path, how = derive_appdata({}, MOUNTINFO)
check("derives the cluster path from the mount", path == "/srv/ark-data", path)
check("says where it came from", how == "bind mount", how)

path, how = derive_appdata({"APPDATA": "/srv/elsewhere"}, MOUNTINFO)
check("an explicit APPDATA still wins", (path, how) == ("/srv/elsewhere", "environment"))

path, how = derive_appdata({}, "30 29 0:31 / / rw - overlay overlay rw\n")
check("nothing to go on is reported, not guessed", path is None and how == "default")

# ---- the port: one field, not two
port, how = derive_status_port({})
check("port defaults to the exposed port", port == CONTAINER_PORT, port)
check("says where the port came from", how == "published port", how)
check("an explicit STATUS_PORT still wins", derive_status_port({"STATUS_PORT": "9090"})[0] == 9090)
check("0 still means the UI is off", derive_status_port({"STATUS_PORT": "0"})[0] == 0)
check("a junk port falls back rather than crashing",
      derive_status_port({"STATUS_PORT": "not-a-port"})[0] == CONTAINER_PORT)

# ---- the whole point: one path + one port is a complete install
d = tempfile.mkdtemp()
store, created, code = bootstrap(d, environ={})
check("boots with no environment at all", created and code)
check("port is set without STATUS_PORT", store.get("status_port") == CONTAINER_PORT,
      store.get("status_port"))
check("timezone defaults to UTC", store.get("timezone") == "UTC", store.get("timezone"))

# ---- a bad install value must not take the good ones down with it
d = tempfile.mkdtemp()
store, _c, _code = bootstrap(d, environ={"APPDATA": "not-an-absolute-path",
                                         "STATUS_PORT": "9091"})
check("a bad path is ignored, not fatal", store.get("appdata").startswith("/"),
      store.get("appdata"))
check("the good value beside it still applies", store.get("status_port") == 9091,
      store.get("status_port"))


# ---- timezone is a picker, and it reaches the process
from .schema import BY_KEY, TIMEZONES
tz = BY_KEY["timezone"]
check("timezone is a choice, not free text", tz["type"] == "choice", tz["type"])
check("timezone is no longer an install variable", tz.get("phase") != "install")
check("UTC is offered first", TIMEZONES[0] == "UTC", TIMEZONES[:2])
check("the list is real IANA zones", "Europe/London" in TIMEZONES and "/" in TIMEZONES[1])

from .settings import validate, Invalid
check("a valid zone is accepted", validate("timezone", "Europe/London") == "Europe/London")
for bad in ("chicago", "CST", "Mars/Olympus"):
    try:
        validate("timezone", bad)
        check("rejects %r" % bad, False, "accepted")
    except Invalid:
        check("rejects %r" % bad, True)

applied = apply_timezone("Europe/London")
check("timezone reaches the environment", os.environ.get("TZ") == "Europe/London")
check("tzset applied where the platform has it", applied or not hasattr(__import__("time"), "tzset"))

print("\nFAILURES: %s" % fails if fails else "\nall install tests passed")
sys.exit(1 if fails else 0)
