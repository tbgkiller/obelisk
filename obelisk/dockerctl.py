"""
Talking to Docker.

Obelisk creates and manages the map containers, so it needs the host's Docker socket
mounted in. That is a real grant of power - anything that can create a container with
a bind mount can read and write any file on the host - and it is the reason this
container should be treated as trusted infrastructure rather than just another app.

Everything here is a thin wrapper with one job: fail clearly. A missing socket should
say "the socket isn't mounted", not surface as a confusing error twenty steps later
when a launch half-completes.
"""

import json, shutil, subprocess

SOCKET = "/var/run/docker.sock"


def _run(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "%s is not installed in this image" % args[0]
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout
    except Exception as e:
        return 1, str(e)


def available():
    """(ok, message). Checked at startup so the UI can say what is wrong up front."""
    if not shutil.which("docker"):
        return False, ("the Docker CLI is missing from this image - this build can't "
                       "manage containers")
    rc, out = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=20)
    if rc != 0:
        return False, ("can't reach Docker. Mount the host socket into this container "
                       "(%s:%s) and restart it. Obelisk needs it to create the map "
                       "servers." % (SOCKET, SOCKET))
    server = out.strip().splitlines()[-1] if out.strip() else "?"
    rc2, _ = _run(["docker", "compose", "version"], timeout=20)
    if rc2 != 0:
        return False, "the docker compose plugin is missing from this image"
    return True, "Docker %s, compose plugin present" % server


def ports_in_use():
    """Host ports already bound by any container, so a plan never lands on one.

    Returns None when Docker can't be reached - the plan reports that as
    'not checked' rather than pretending nothing is in use.
    """
    rc, out = _run(["docker", "ps", "--format", "{{.Ports}}"], timeout=30)
    if rc != 0:
        return None
    ports = set()
    for line in out.splitlines():
        for chunk in line.split(","):
            chunk = chunk.strip()
            if "->" not in chunk:
                continue
            hostside = chunk.split("->")[0]
            bit = hostside.rsplit(":", 1)[-1]
            if bit.isdigit():
                ports.add(int(bit))
    return ports

def compose(compose_file, project_name, args, timeout=900):
    """Run one `docker compose` command against a specific file and project.

    Always -f and -p explicitly: Obelisk's own working directory is not the cluster's,
    and an inherited project name would silently adopt or orphan somebody else's stack.
    """
    cmd = ["docker", "compose", "-f", compose_file, "-p", project_name] + list(args)
    return _run(cmd, timeout=timeout)


def compose_ps(compose_file, project_name, timeout=60):
    """Per-service state as a list of dicts. Empty when nothing is up or Docker is out.

    Parses the JSON-lines form rather than the table: the table's columns move between
    Docker versions, and a status page that misreads them is worse than none.
    """
    rc, out = compose(compose_file, project_name, ["ps", "--format", "json"],
                      timeout=timeout)
    if rc != 0:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rows.append({"service": d.get("Service") or d.get("Name", ""),
                     "name": d.get("Name", ""),
                     "state": (d.get("State") or "").lower(),
                     "status": d.get("Status", ""),
                     "health": (d.get("Health") or "").lower()})
    return rows

def network_connect(network, container, timeout=30):
    """Put `container` on `network`. Already-connected is success, not an error."""
    rc, out = _run(["docker", "network", "connect", network, container], timeout=timeout)
    if rc == 0:
        return True, "connected"
    if "already exists" in out or "already connected" in out:
        return True, "already connected"
    return False, out.strip()[-200:]

def existing_containers(timeout=30):
    """Every container name on the host, with the compose project that owns it.

    Includes stopped containers: a stopped container still holds its name, and Docker
    refuses to reuse it. Returns {name: project_or_empty}.
    """
    fmt = "{{.Names}}" + chr(9) + '{{.Label "com.docker.compose.project"}}'
    rc, out = _run(["docker", "ps", "-a", "--format", fmt], timeout=timeout)
    if rc != 0:
        return None                      # unknown, which the caller must not treat as "none"
    found = {}
    for line in out.splitlines():
        if chr(9) not in line:
            continue
        name, project = line.split(chr(9), 1)
        name = name.strip()
        if name:
            found[name] = project.strip()
    return found
