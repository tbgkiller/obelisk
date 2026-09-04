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


def container_root(environ=None):
    """Obelisk's own data folder, as this container sees it. Holds the definition."""
    environ = os.environ if environ is None else environ
    return (environ.get("OBELISK_DATA") or "/data").rstrip("/")


def ark_container_root(environ=None):
    """The Ark data folder, as this container sees it. Holds the bulk."""
    environ = os.environ if environ is None else environ
    return (environ.get("OBELISK_ARK") or "/ark").rstrip("/")


def _self_names(environ):
    """The names Docker might know this container by, best first."""
    return [n for n in ((environ.get("HOSTNAME") or "").strip(),
                        (environ.get("HOST_CONTAINERNAME") or "").strip()) if n]


def _inspector(fmt):
    """A function that runs `docker inspect -f fmt <name>` and returns (rc, out)."""
    from . import dockerctl

    def run(name):
        return dockerctl._run(["docker", "inspect", "-f", fmt, name], timeout=20)
    return run


def derive_ports(environ=None, inspect=None):
    """(listen, published, how) - the two ports that are not the same number.

    `listen` is the port to bind inside this container. `published` is the port the
    operator actually mapped on the host, which is what a generated stack has to publish
    and what the browser connects to.

    Conflating them is what forced a hand-added STATUS_PORT: the generated stack
    published whatever the container was listening on, so an operator who mapped
    18091 got a stack trying to bind 8088 and colliding with whatever was already there.
    Docker knows both numbers, so ask it.
    """
    environ = os.environ if environ is None else environ

    raw = (environ.get("STATUS_PORT") or "").strip()
    if raw:
        try:
            n = int(raw)
            return n, n, "environment"          # explicit still wins, for an upgrade
        except ValueError:
            pass

    fmt = ("{{range $p, $conf := .NetworkSettings.Ports}}{{$p}} "
           "{{range $conf}}{{.HostPort}}{{end}}" + chr(10) + "{{end}}")
    run = inspect or _inspector(fmt)
    for name in _self_names(environ):
        rc, out = run(name)
        if rc != 0:
            continue
        found = []
        for line in out.splitlines():
            bits = line.split()
            if len(bits) < 2 or "/" not in bits[0]:
                continue
            try:
                container_port = int(bits[0].split("/")[0])
                host_port = int(bits[1])
            except ValueError:
                continue
            found.append((container_port, host_port))
        if not found:
            continue
        # Prefer the mapping for the port this image exposes; otherwise take the first.
        for container_port, host_port in found:
            if container_port == CONTAINER_PORT:
                return container_port, host_port, "docker"
        return found[0][0], found[0][1], "docker"

    # No socket, or nothing published. Bind the exposed port and assume the operator
    # mapped it straight through - the plan's port check is what catches it if not.
    return CONTAINER_PORT, CONTAINER_PORT, "assumed"


def host_path_of(container_path, environ=None, inspect=None):
    """Where a path inside this container comes from on the host.

    Asking Docker is the only way to know. /proc/self/mountinfo reports the mount point
    *in here*, which is a different string from the folder the operator picked - and
    the generated compose has to name the host one or the map containers mount nothing.

    Getting this from Docker is what lets the install form ask for the folder once
    instead of twice: the two paths no longer have to be the same string.
    """
    environ = os.environ if environ is None else environ
    if inspect is None:
        inspect = _inspector("{{range .Mounts}}{{.Source}}" + chr(9) +
                             "{{.Destination}}" + chr(10) + "{{end}}")
    want = container_path.rstrip("/")
    for name in _self_names(environ):
        rc, out = inspect(name)
        if rc != 0:
            continue
        for line in out.splitlines():
            if chr(9) not in line:
                continue
            source, dest = line.split(chr(9), 1)
            if dest.strip().rstrip("/") == want:
                return source.strip(), "docker"
    return None, "unknown"


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

    # Ask Docker where the *Ark* mount comes from. That host path is what a generated
    # compose has to name for the map containers, and it is not the path we see it at.
    root = ark_container_root(environ)
    host, how = host_path_of(root, environ=environ)
    if host:
        return host, "docker"

    # Without a socket, fall back to the mount table. That reports container-side
    # paths, so it is only right when the folder is mounted at the same path in and
    # out - true of the stack Obelisk generates, not of a hand-made container.
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
