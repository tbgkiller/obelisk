"""
Install-time derivation: the operator sets the path once and the port once.

Fixture values here are synthetic. The one real value anywhere in the suites is mod id
929110, which the product checks by name for stacking-mod load order.
"""

import os, sys, tempfile

from . import install as install_mod
from .install import (CONTAINER_PORT, candidate_mounts, container_root, derive_ports,
                      derive_appdata, derive_status_port, host_path_of,
                      mount_points, apply_timezone)
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


# ---- TWO folders, because they hold two different things
# Not the old duplicate-field trap: that asked for one folder twice and let the answers
# disagree. These are two places - the definition, and the bulk - with different
# contents, different sizes and different storage needs.
from . import layout as layoutmod
from .compose import generate_compose
from .settings import Store

base = tempfile.mkdtemp()
ob_root = os.path.join(base, "obelisk")
ark_root = os.path.join(base, "ark")
st_two, created_two, code_two = bootstrap(ob_root, environ={"OBELISK_ARK": ark_root})
check("a fresh install boots with both folders given", created_two and code_two)
check("the settings landed in the Obelisk folder",
      os.path.isfile(os.path.join(ob_root, "settings.json")))
check("the root is worked out from where the store is",
      layoutmod.root_of(st_two) == os.path.abspath(ob_root), layoutmod.root_of(st_two))
check("no game data is in the Obelisk folder",
      not any(n in os.listdir(ob_root) for n in ("shared", "ServerFiles", "Mods")),
      os.listdir(ob_root))

layoutmod.ensure_ark(ark_root, ["island"])
for sub in ("shared/SavedArks", "shared/Config", "cluster", "instances/island/Saved",
            "ServerFiles", "Mods"):
    check("the Ark folder has %s" % sub,
          os.path.isdir(os.path.join(ark_root, *sub.split("/"))))
check("the Obelisk folder keeps its backups", os.path.isdir(os.path.join(ob_root, "backups")))

# the two host paths are separate facts, each asked of Docker
def _mounts_inspect(name):
    return (0, "/mnt/user/appdata/obelisk" + chr(9) + "/data" + chr(10) +
            "/mnt/zfs/appdata/ark" + chr(9) + "/ark" + chr(10))

check("the Ark host path comes from its own mount",
      host_path_of("/ark", environ={"HOSTNAME": "a"}, inspect=_mounts_inspect)[0] ==
      "/mnt/zfs/appdata/ark")
check("the Obelisk host path comes from its own mount",
      host_path_of("/data", environ={"HOSTNAME": "a"}, inspect=_mounts_inspect)[0] ==
      "/mnt/user/appdata/obelisk")

st_l = Store(os.path.join(ob_root, "settings.json")).load()
st_l.patch({"appdata": "/mnt/zfs/appdata/ark"}, source="install")
st_l.patch({"maps": "island", "admin_password": "synthetic-pw", "cluster_id": "twofolder"})
yml = generate_compose(st_l, project="twofolder")
check("map saves come from the Ark folder",
      "/mnt/zfs/appdata/ark/instances/island/Saved" in yml)
check("the game install is inside the Ark folder",
      "/mnt/zfs/appdata/ark/ServerFiles:/home/pok/arkserver" in yml)
check("the generated stack contains no manager service",
      "container_name: obelisk" not in yml)
check("no data path is passed as a variable",
      "APPDATA" not in yml and "STATUS_PORT" not in yml,
      [l for l in yml.splitlines() if "APPDATA" in l or "STATUS_PORT" in l])

# ---- THREE fields, and no hidden twin to hand-add
# The seam this closes: Obelisk bound the port it was listening on and published that
# same number in the stack it generated. An operator who mapped 18091 got a generated
# stack trying to bind 8088 - so they had to hand-add STATUS_PORT to make the two
# agree, which is the same duplicate-value footgun as the old APPDATA field.
def _ports_inspect(name):
    return 0, "8088/tcp 18091" + chr(10)

listen, published, how = derive_ports({"HOSTNAME": "abc123"}, inspect=_ports_inspect)
check("the listening port is the container's", listen == CONTAINER_PORT, listen)
check("the published port is the operator's choice", published == 18091, published)
check("and Docker is where that came from", how == "docker", how)
check("the two are allowed to differ", listen != published)

many = lambda n: (0, "7777/udp 7777" + chr(10) + "8088/tcp 18091" + chr(10))
check("the exposed port's mapping is the one that counts",
      derive_ports({"HOSTNAME": "a"}, inspect=many)[:2] == (CONTAINER_PORT, 18091),
      derive_ports({"HOSTNAME": "a"}, inspect=many))

check("an explicit STATUS_PORT still wins, for an upgrade",
      derive_ports({"STATUS_PORT": "9000"}) == (9000, 9000, "environment"))
check("no socket falls back to the exposed port rather than guessing",
      derive_ports({"HOSTNAME": "a"}, inspect=lambda n: (1, "")) ==
      (CONTAINER_PORT, CONTAINER_PORT, "assumed"))

# a fresh install with only the three fields: the store learns the published port
three_root = os.path.join(tempfile.mkdtemp(), "data")
_real_derive = install_mod.derive_ports
install_mod.derive_ports = lambda environ=None, inspect=None: (8088, 18091, "docker")
st_three, _c, _code = bootstrap(os.path.join(three_root, "obelisk"), environ={})
install_mod.derive_ports = _real_derive
check("the store records the port the operator actually mapped",
      st_three.get("status_port") == 18091, st_three.get("status_port"))

st_three.patch({"appdata": "/mnt/user/appdata/obelisk-testcluster"}, source="install")
st_three.patch({"maps": "island", "admin_password": "synthetic-pw",
                "cluster_id": "threefield", "game_port_base": 7877,
                "rcon_port_base": 27920})
yml3 = generate_compose(st_three, project="threefield")
# The manager is not in the stack, so its port must not appear there at all - that is
# what stopped a launch from an already-running Obelisk colliding with itself.
check("the generated stack never publishes the manager's port",
      "18091" not in yml3, [l for l in yml3.splitlines() if "18091" in l])
check("and never publishes 8088 on the host", '"8088:8088"' not in yml3, yml3[-500:])
check("no STATUS_PORT is passed to anything",
      "STATUS_PORT" not in yml3, [l for l in yml3.splitlines() if "STATUS_PORT" in l])


# ---- dual-stack publishing: the same port, twice
# Docker publishes on IPv4 and IPv6, so one mapping is reported as two bindings. With no
# separator between them 18091 came back as 1809118091 - rejected by the validator,
# leaving the container claiming it was published on the port it merely listens on, and
# a generated stack that would collide on 8088.
_real_dual = lambda n: (0, "8088/tcp 18091 18091 " + chr(10))
check("a port published on both stacks reads as one port",
      derive_ports({"HOSTNAME": "a"}, inspect=_real_dual) == (8088, 18091, "docker"),
      derive_ports({"HOSTNAME": "a"}, inspect=_real_dual))
check("a single binding still works",
      derive_ports({"HOSTNAME": "a"},
                   inspect=lambda n: (0, "8088/tcp 18091 " + chr(10)))[1] == 18091)
check("an unpublished port falls back rather than inventing one",
      derive_ports({"HOSTNAME": "a"},
                   inspect=lambda n: (0, "8088/tcp " + chr(10)))[2] == "assumed")
check("a nonsense host port is ignored, not stored",
      derive_ports({"HOSTNAME": "a"},
                   inspect=lambda n: (0, "8088/tcp 1809118091 " + chr(10)))[2] == "assumed",
      derive_ports({"HOSTNAME": "a"}, inspect=lambda n: (0, "8088/tcp 1809118091 " + chr(10))))

# ---- the log has to hand someone a URL, not a puzzle
from .install import host_address, setup_url
check("the host name Unraid passes is used",
      host_address({"HOST_HOSTNAME": "tower"}) == "tower")
check("an explicit host wins", host_address({"OBELISK_HOST": "10.0.0.5",
                                             "HOST_HOSTNAME": "tower"}) == "10.0.0.5")
check("with neither, it says so rather than guessing wrong",
      host_address({}) == "<this-host>")
check("the logged URL carries the published port, not the listening one",
      setup_url({"HOST_HOSTNAME": "tower"}, published=18091) ==
      "http://tower:18091/setup",
      setup_url({"HOST_HOSTNAME": "tower"}, published=18091))


# ---- the server address is display-only, and never reaches the servers
# Pinning an ASA server to one address stops Steam and Epic advertising it correctly on
# a single host, so the generated cluster must never carry one.
st_ip = Store(os.path.join(tempfile.mkdtemp(), "settings.json"))
st_ip.data = {"version": 1, "cluster": {}, "maps": {}}
st_ip.patch({"appdata": "/mnt/zfs/appdata/ark", "status_port": 18091}, source="install")
st_ip.patch({"maps": "island", "admin_password": "synthetic-pw", "cluster_id": "ipcheck"})
yml_ip = generate_compose(st_ip, project="ipcheck")
check("no MULTIHOME in the generated cluster", "MULTIHOME" not in yml_ip)
import re as _re
check("no IP literal in the generated cluster",
      not _re.search(r"\d{1,3}(?:\.\d{1,3}){3}", yml_ip),
      _re.findall(r"\d{1,3}(?:\.\d{1,3}){3}", yml_ip))
check("the server address is not passed to any map",
      "OBELISK_HOST" not in yml_ip, [l for l in yml_ip.splitlines() if "OBELISK_HOST" in l])
check("maps are reached by container name, not address",
      "container_name: asa-ipcheck-island" in yml_ip,
      [l for l in yml_ip.splitlines() if "container_name" in l])

print("\nFAILURES: %s" % fails if fails else "\nall install tests passed")
sys.exit(1 if fails else 0)
