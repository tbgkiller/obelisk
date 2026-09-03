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

import shutil, subprocess

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
