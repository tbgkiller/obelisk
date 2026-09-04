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


# ---- ONE mount is a complete install
# The install form used to ask for the data folder twice - a store mount and a saves
# mount - and the workaround was pointing both at the same folder. One mount now has to
# be genuinely sufficient: store, saves and everything else live inside it.
from . import layout as layoutmod
from .compose import generate_compose
from .settings import Store

one_root = os.path.join(tempfile.mkdtemp(), "data")
st_one, created_one, code_one = bootstrap(os.path.join(one_root, "obelisk"), environ={})
check("a fresh install boots from one folder", created_one and code_one)
check("the store landed inside that folder",
      os.path.isfile(os.path.join(one_root, "obelisk", "settings.json")))
check("the root is worked out from where the store is",
      layoutmod.root_of(st_one) == os.path.abspath(one_root), layoutmod.root_of(st_one))

layoutmod.ensure(layoutmod.root_of(st_one), ["island"])
for sub in ("obelisk", "shared", "shared/SavedArks", "cluster", "instances/island/Saved"):
    check("the layout has %s inside the one folder" % sub,
          os.path.isdir(os.path.join(one_root, *sub.split("/"))))

# the host path is a separate fact, asked of Docker rather than assumed equal
def _fake_inspect(name):
    return 0, "/mnt/user/appdata/obelisk-testcluster" + chr(9) + "/data" + chr(10)

host, how = host_path_of("/data", environ={"HOSTNAME": "abc123"}, inspect=_fake_inspect)
check("the host path comes back from Docker, not the mount table",
      host == "/mnt/user/appdata/obelisk-testcluster", host)
check("and it is allowed to differ from the container path", host != "/data")

st_l = Store(os.path.join(one_root, "obelisk", "settings.json")).load()
st_l.patch({"appdata": "/mnt/user/appdata/obelisk-testcluster"}, source="install")
st_l.patch({"maps": "island", "admin_password": "synthetic-pw", "cluster_id": "onemount"})
yml = generate_compose(st_l, project="onemount")
check("the compose names the HOST path for map saves",
      "/mnt/user/appdata/obelisk-testcluster/instances/island/Saved" in yml)
check("Obelisk itself gets one mount at /data",
      '"/mnt/user/appdata/obelisk-testcluster:/data"' in yml)
check("and there is no second data bind",
      yml.count("/mnt/user/appdata/obelisk-testcluster:/") == 1, yml[-600:])
check("the game install is still a sibling outside the root",
      "/mnt/user/appdata/obelisk-testcluster-serverfiles:/home/pok/arkserver" in yml)


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
check("the generated stack publishes the operator's port, not 8088",
      '"18091:8088"' in yml3, [l for l in yml3.splitlines() if "18091" in l or "8088" in l])
check("and never publishes 8088 on the host",
      '"8088:8088"' not in yml3, yml3[-500:])
check("no STATUS_PORT is passed to the generated container",
      "STATUS_PORT" not in yml3, [l for l in yml3.splitlines() if "STATUS_PORT" in l])
check("so nothing has to be hand-added to make the two agree",
      yml3.count("18091") == 1, [l for l in yml3.splitlines() if "18091" in l])

print("\nFAILURES: %s" % fails if fails else "\nall install tests passed")
sys.exit(1 if fails else 0)
