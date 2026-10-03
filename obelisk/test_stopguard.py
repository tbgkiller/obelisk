"""The stop guard: an outside `docker stop` gets Obelisk's semantics, not POK's.

On 1 October Unraid's Appdata Backup stopped every map with a plain `docker stop`. POK's
SIGTERM handler saved and then killed Wine before SQLite had finished, and two worlds
came back malformed - the world's last write landed eleven seconds into a fifteen-second
stop. The guard sits in front of POK and turns that signal into DoExit, a wait for the
server process to be gone, and only then the signal.

Most of this file RUNS the guard, the way Docker would: as a separate process with a
child under it, a fake RCON listener on localhost and a fake process table, sent a real
SIGTERM. A guard that only reads right is not the thing that failed on 1 October; what
failed was an order of events, and an order of events has to be watched happening.

Fixture values are synthetic throughout.
    python3 -m obelisk.test_stopguard
"""

import os, signal, socket, struct, subprocess, sys, tempfile, threading, time

import yaml

from . import cluster, compose, layout, stopguard
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- the packet, by hand
check("an RCON packet is size, id, type, body and two NULs, little-endian",
      stopguard.packet(1, 3, "pw") ==
      b"\x0c\x00\x00\x00" + b"\x01\x00\x00\x00" + b"\x03\x00\x00\x00" + b"pw\x00\x00",
      stopguard.packet(1, 3, "pw"))
check("the size counts everything after itself and not itself",
      struct.unpack("<i", stopguard.packet(7, 2, "DoExit")[:4])[0] == 4 + 4 + 6 + 2)
check("a negative id packs as the protocol's -1",
      stopguard.packet(-1, 2, "")[4:8] == b"\xff\xff\xff\xff")

# ---------------------------------------------------------------- the same server rule
# A copy of cluster.is_server_process, because the guard cannot import Obelisk. Pinned to
# the same answers so the copy cannot drift from the rule it copies.
_LINES = [
    'ArkAscendedServer.exe TheIsland_WP?listen?SessionName="TBG 01" -server',
    "python3 /opt/GE-Proton10-34/proton run ArkAscendedServer.exe TheIsland_WP",
    r"c:\windows\system32\steam.exe ArkAscendedServer.exe TheIsland_WP",
    r"Z:\ARK\ShooterGame\Binaries\Win64\ArkAscendedServer.exe TheIsland_WP",
    '"ArkAscendedServer.exe" x', "arkascendedserver.exe", "/tini -- /home/pok/scripts/init.sh",
    "python3 /opt/obelisk/stop-guard.py /home/pok/scripts/init.sh", "", None,
]
check("the guard reads a server process exactly as the stop path does",
      [stopguard.is_server_process(l) for l in _LINES] ==
      [cluster.is_server_process(l) for l in _LINES],
      [(l, stopguard.is_server_process(l), cluster.is_server_process(l)) for l in _LINES])
check("and the guard's own line is not a server - it must not wait on itself",
      not stopguard.is_server_process(
          "python3 /opt/obelisk/stop-guard.py /home/pok/scripts/init.sh"))


def fake_proc(*cmdlines):
    """A /proc with these processes in it. cmdline is NUL-separated, as the kernel has
    it; one entry is left in Wine's space-joined form, which the guard must read too."""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "self"))          # non-numeric entries are skipped
    for i, line in enumerate(cmdlines, start=100):
        os.makedirs(os.path.join(root, str(i)))
        with open(os.path.join(root, str(i), "cmdline"), "wb") as fh:
            fh.write(line)
    return root


_SERVER = b"ArkAscendedServer.exe\x00TheIsland_WP?listen\x00-server\x00"
_PROTON = b"python3\x00/opt/proton\x00run\x00ArkAscendedServer.exe\x00"
_WINE_JOINED = b"Z:\\ARK\\ArkAscendedServer.exe TheIsland_WP?listen -server"
check("a server in the process table is seen", stopguard.server_running(
    fake_proc(_PROTON, _SERVER)))
check("a server whose argv Wine joined into one string is seen too",
      stopguard.server_running(fake_proc(_WINE_JOINED)))
check("the wrappers alone are not a server",
      not stopguard.server_running(fake_proc(_PROTON, b"/tini\x00--\x00init.sh\x00")))
check("a process table that will not list is not a server",
      not stopguard.server_running(os.path.join(tempfile.mkdtemp(), "nope")))

# ---------------------------------------------------------------- the compose file
POSIX_ROOT = "/srv/ark-data"


def fresh(**over):
    st = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
    st.patch({"appdata": POSIX_ROOT, "status_port": 8088}, source="install")
    st.patch(dict({"maps": "island,ragnarok", "admin_password": "synthetic-pw",
                   "cluster_id": "guardtest", "host_ram_gb": 256}, **over))
    return st


_st = fresh()
check("the guard is on by default", compose.stop_guard_on(_st), _st.get("stop_guard"))
_doc = yaml.safe_load(compose.generate_compose(_st, project="guardtest",
                                               wait_for_master=False))
_isl = _doc["services"]["island"]
check("every map's entrypoint is tini, then the guard, then POK's own init",
      _isl.get("entrypoint") == ["/tini", "--", "python3", "/opt/obelisk/stop-guard.py",
                                 "/home/pok/scripts/init.sh"], _isl.get("entrypoint"))
check("on every map, not just the first",
      _doc["services"]["ragnarok"].get("entrypoint") == _isl.get("entrypoint"))
# The upstream dockerfile at 67ed7ad and `docker top` on the live fleet both say this.
# If POK ever moves init.sh, this is the line that has to change with it.
check("POK's entrypoint is the one its dockerfile declares",
      compose.POK_ENTRYPOINT == ("/tini", "--", "/home/pok/scripts/init.sh"))
check("the guard's folder is mounted read-only from the ark root",
      POSIX_ROOT + "/obelisk:/opt/obelisk:ro" in _isl["volumes"], _isl["volumes"])
check("the mount is the folder, never the single file Docker would create as a directory",
      not any("stop-guard.py:" in v for v in _isl["volumes"]), _isl["volumes"])
_env = _isl["environment"]
check("the guard is given its budget", _env.get("OBELISK_STOP_GUARD_BUDGET") == "180", _env)
check("which is comfortably inside the grace period",
      compose.GUARD_BUDGET + 20 <= 210 and _isl.get("stop_grace_period") == "210s",
      (compose.GUARD_BUDGET, _isl.get("stop_grace_period")))
check("and the RCON port and password it signs in with are the ones POK already has",
      _env.get("RCON_PORT") == "27020" and _env.get("SERVER_ADMIN_PASSWORD") ==
      "synthetic-pw", _env)

_off = yaml.safe_load(compose.generate_compose(fresh(stop_guard=False),
                                               project="guardtest",
                                               wait_for_master=False))["services"]["island"]
check("with the setting off the image's own entrypoint is left alone",
      "entrypoint" not in _off and "OBELISK_STOP_GUARD_BUDGET" not in _off["environment"]
      and not any("/opt/obelisk" in v for v in _off["volumes"]), _off)

from . import schema as _schema                                   # noqa: E402
_row = [s for s in _schema.SETTINGS if s["key"] == "stop_guard"]
check("the setting says it needs a recreate, like the restart policy does",
      _row and _row[0]["apply"] == "recreate"
      and "recreated" in _row[0]["help"], _row)

# ---------------------------------------------------------------- the file on disk
_ark = tempfile.mkdtemp()
_path = cluster.write_guard(_st, ark_root=_ark)
check("the guard is written into the ark root's generated folder",
      _path == os.path.join(_ark, "obelisk", "stop-guard.py"), _path)
_text = open(_path, encoding="utf-8").read()
check("it is the shipped guard, verbatim, under a do-not-edit line",
      _text == cluster.GUARD_HEADER + open(stopguard.__file__, encoding="utf-8").read())
check("readable and runnable by the server's user through a mount it does not own",
      (os.stat(_path).st_mode & 0o777) == 0o755, oct(os.stat(_path).st_mode))
check("and so is its folder", (os.stat(os.path.dirname(_path)).st_mode & 0o777) == 0o755)
cluster.write_guard(_st, ark_root=_ark)
check("writing it again is harmless and leaves no temp file behind",
      sorted(os.listdir(os.path.dirname(_path))) == ["stop-guard.py"],
      os.listdir(os.path.dirname(_path)))
check("it imports nothing from Obelisk - there is no Obelisk inside a map container",
      "from ." not in _text and "import obelisk" not in _text)
check("compose.py's mount and the file agree on the name",
      compose.GUARD_FILE == os.path.basename(_path))


# ---------------------------------------------------------------- running it
#
# The guard is started the way tini starts it: as a process with POK's init as its
# child. The child here is a stand-in that writes down the moment it is signalled.
_CHILD = r'''
import os, signal, sys, time
out = sys.argv[1]
def done(signum, frame):
    with open(os.path.join(out, "termed"), "w") as fh:
        fh.write("%r" % time.time())
    sys.exit(int(os.environ.get("FAKE_CHILD_EXIT") or 0))
signal.signal(signal.SIGTERM, done)
open(os.path.join(out, "ready"), "w").close()
while True:
    time.sleep(0.05)
'''


class FakeRcon:
    """A Source RCON listener on localhost. On DoExit it takes the server out of the
    fake process table after `exit_after` seconds - which is what a real server does,
    eleven seconds after the command on The Center in September."""

    def __init__(self, procdir=None, exit_after=1.0):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.procdir, self.exit_after = procdir, exit_after
        self.got, self.password, self.gone_at, self.connections = [], None, None, 0
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            try:
                while True:
                    rid, kind, body = stopguard._read_packet(conn)
                    if kind == stopguard.AUTH:
                        self.password = body
                        conn.sendall(stopguard.packet(rid, 2, ""))
                        continue
                    self.got.append(body)
                    if body == "DoExit":
                        conn.sendall(stopguard.packet(rid, 0, "Exiting..."))
                        threading.Timer(self.exit_after, self.server_exits).start()
            except Exception:                            # noqa: BLE001 - client went
                conn.close()

    def server_exits(self):
        self.gone_at = time.time()
        for pid in os.listdir(self.procdir):
            if pid.isdigit():
                os.remove(os.path.join(self.procdir, pid, "cmdline"))

    def close(self):
        self.sock.close()


def run_guard(procdir, port, budget=10, child_exit=0, signal_at=None):
    """Start the guard over the fake child, SIGTERM it once the child is up. Returns
    (exit code, stderr, signal time, child-termed time or None)."""
    work = tempfile.mkdtemp()
    child = os.path.join(work, "child.py")
    with open(child, "w") as fh:
        fh.write(_CHILD)
    env = dict(os.environ, RCON_PORT=str(port), SERVER_ADMIN_PASSWORD="synthetic-pw",
               OBELISK_STOP_GUARD_BUDGET=str(budget), OBELISK_STOP_GUARD_PROC=procdir,
               FAKE_CHILD_EXIT=str(child_exit))
    p = subprocess.Popen([sys.executable, stopguard.__file__, sys.executable, child, work],
                         env=env, stderr=subprocess.PIPE, text=True)
    for _ in range(200):
        if os.path.exists(os.path.join(work, "ready")):
            break
        time.sleep(0.05)
    sent = time.time()
    p.send_signal(signal.SIGTERM)
    if signal_at:
        signal_at()
    try:
        _out, err = p.communicate(timeout=budget + 20)
    except subprocess.TimeoutExpired:
        p.kill()
        _out, err = p.communicate()
    termed = os.path.join(work, "termed")
    when = float(open(termed).read()) if os.path.exists(termed) else None
    return p.returncode, err, sent, when


# 1. the whole point: signal -> DoExit -> the server exits -> only then the signal
_pd = fake_proc(_PROTON, _SERVER)
_rc = FakeRcon(_pd, exit_after=1.0)
_code, _err, _sent, _termed = run_guard(_pd, _rc.port)
check("an outside SIGTERM is turned into a DoExit first", _rc.got == ["DoExit"], _rc.got)
check("signed in with the admin password the compose file gives POK",
      _rc.password == "synthetic-pw", _rc.password)
check("POK is not signalled until the server process has gone",
      _termed is not None and _rc.gone_at is not None and _termed >= _rc.gone_at,
      (_termed, _rc.gone_at))
check("and then it is - the container does stop", _termed is not None, _err)
check("the guard exits with POK's own status", _code == 0, (_code, _err))
check("and says what it did, in the container's own log",
      "DoExit sent" in _err and "has exited" in _err, _err)
_rc.close()

# 2. Obelisk's own stops: the server is already gone when the signal arrives
_pd = fake_proc(_PROTON, b"/tini\x00--\x00init.sh\x00")
_rc = FakeRcon(_pd)
_code, _err, _sent, _termed = run_guard(_pd, _rc.port)
check("with no server running the signal is passed on at once",
      _termed is not None and _termed - _sent < 1.5, (_termed and _termed - _sent, _err))
check("without so much as an RCON connection", _rc.connections == 0, _rc.connections)
check("and it says so", "no ARK server is running" in _err, _err)
_rc.close()

# 3. RCON refused: still wait for the server, never forward into a save
_closed = socket.socket()
_closed.bind(("127.0.0.1", 0))
_dead_port = _closed.getsockname()[1]
_closed.close()
_pd = fake_proc(_SERVER)
_gone = {}


def _server_exits_later():
    def go():
        _gone["at"] = time.time()
        os.remove(os.path.join(_pd, "100", "cmdline"))
    threading.Timer(1.5, go).start()


_code, _err, _sent, _termed = run_guard(_pd, _dead_port, signal_at=_server_exits_later)
check("an RCON that will not answer still means waiting for the server",
      _termed is not None and "at" in _gone and _termed >= _gone["at"],
      (_termed, _gone, _err))
check("and says the DoExit could not be sent", "could not be sent" in _err, _err)

# 4. the budget runs out: forward anyway, and loudly
_pd = fake_proc(_SERVER)
_code, _err, _sent, _termed = run_guard(_pd, _dead_port, budget=1)
check("a server still running at the end of the budget gets the signal anyway",
      _termed is not None and 0.9 <= _termed - _sent < 6, (_termed and _termed - _sent))
check("said loudly, because Docker's kill is coming regardless",
      "STILL RUNNING" in _err and "restore it from a save point" in _err, _err)

# 5. POK's exit status is the container's exit status
_pd = fake_proc()
_code, _err, _s, _t = run_guard(_pd, _dead_port, child_exit=3)
check("the guard exits with whatever POK exited with", _code == 3, (_code, _err))

print("\nFAILURES: %s" % fails if fails else "\nall stop guard tests passed")
sys.exit(1 if fails else 0)
