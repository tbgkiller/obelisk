"""
What Docker already told us.

Three facts are fixed the moment the container is created: where the cluster's data is
bind-mounted, which port is published, and where Obelisk's own store lives. Asking for
any of them a second time as an environment variable is a footgun - the template used
to carry both a "Cluster data" mount and an APPDATA variable that had to match it, and
a "WebUI port" beside a STATUS_PORT that had to match that. Two fields, one truth, and
nothing checking they agreed: change the port in the template and the container would
keep listening on the old one, with no error and no page.

So derive them instead. An explicit environment variable still wins - that is how an
upgraded hand-built stack keeps working - but nothing has to be repeated.
"""

import os

# The port the image EXPOSEs. Unraid and compose map a host port onto this one, so the
# container side is a constant: whatever the operator publishes on the outside arrives
# here. Binding anything else is how you get a container that is up and unreachable.
CONTAINER_PORT = 8088

MOUNTINFO = "/proc/self/mountinfo"

# Mount points that are never the cluster's data: the OS, the runtime, Obelisk's own
# store, and the socket. Anything left is something the operator chose to mount.
_SYSTEM = ("/proc", "/sys", "/dev", "/etc", "/run", "/var/run", "/var/log", "/var/lib",
           "/usr", "/lib", "/lib64", "/bin", "/sbin", "/boot", "/tmp", "/app", "/opt")


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def mount_points(mountinfo_text):
    """Every mount point in a /proc/self/mountinfo dump, in order.

    Field 5 is the mount point; the kernel escapes spaces as \\040.
    """
    points = []
    for line in mountinfo_text.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        points.append(fields[4].replace("\\040", " "))
    return points


def candidate_mounts(mountinfo_text, data_dir="/data"):
    """Operator-chosen mounts, best candidate first.

    Excludes the OS, Obelisk's own store and the docker socket. What remains is what
    somebody deliberately bind-mounted, which on any sane install is the cluster.
    """
    out = []
    for mp in mount_points(mountinfo_text):
        if mp in ("/", "", data_dir) or mp.startswith(data_dir.rstrip("/") + "/"):
            continue
        if mp.endswith("docker.sock"):
            continue
        if any(mp == s or mp.startswith(s + "/") for s in _SYSTEM):
            continue
        if mp in out:
            continue
        out.append(mp)
    # A shallower path is likelier to be the cluster root than something inside it.
    out.sort(key=lambda p: (p.count("/"), len(p)))
    return out


def derive_appdata(environ=None, mountinfo_text=None, data_dir=None):
    """(path, how) - where the cluster's data lives on the host.

    `how` is "environment" when it was given explicitly, "bind mount" when it was
    worked out from what Docker mounted, and "default" when there was nothing to go on.
    Returns the schema default's caller problem as None so the caller can fall back.
    """
    environ = os.environ if environ is None else environ
    data_dir = data_dir or environ.get("OBELISK_DATA", "/data")

    explicit = (environ.get("APPDATA") or "").strip()
    if explicit:
        return explicit, "environment"

    text = mountinfo_text if mountinfo_text is not None else _read(MOUNTINFO)
    for mp in candidate_mounts(text, data_dir):
        return mp, "bind mount"
    return None, "default"


def derive_status_port(environ=None):
    """(port, how) - the port to listen on inside the container.

    An explicit STATUS_PORT still wins so an upgraded stack keeps its behaviour, and 0
    still means "no web UI". Otherwise this is the exposed port: the operator picks the
    host side in the template, Docker maps it here, and there is nothing to keep in sync.
    """
    environ = os.environ if environ is None else environ
    raw = (environ.get("STATUS_PORT") or "").strip()
    if raw:
        try:
            return int(raw), "environment"
        except ValueError:
            pass
    return CONTAINER_PORT, "published port"


def apply_timezone(tz):
    """Make the process observe `tz`, so wipe times and log stamps are local time.

    Timezone is a UI setting rather than an install-time variable, so it changes while
    Obelisk is running and has to take effect without a recreate.
    """
    if not tz:
        return False
    os.environ["TZ"] = str(tz)
    try:
        import time
        time.tzset()          # POSIX only; absent on Windows, where the env var is enough
        return True
    except AttributeError:
        return False
