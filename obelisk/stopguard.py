#!/usr/bin/env python3
"""
The stop guard: what a map container does with a SIGTERM that did not come from Obelisk.

Generated into the ARK data root by Obelisk and mounted read-only into every map
container, where it runs between tini and POK's own entrypoint. THIS FILE IS SHIPPED
VERBATIM, so it imports nothing from Obelisk and nothing outside the standard library:
inside the map container there is no Obelisk package, only POK's python3.

WHY IT EXISTS. On 1 October Unraid's Appdata Backup plugin ran "stop, backup, start"
over every container on the host, which is a plain `docker stop` on each map. POK
answers SIGTERM by saving and then killing Wine before SQLite has finished - the same
6-out-of-6 orphaned journals cluster.SIGNAL_OK records from 20 September - and each
stop took 14-18 seconds. The Island's world was last written eleven seconds into its
stop; Ragnarok's, eleven seconds into its. Both came back malformed. stop_grace_period
did not help: it is POK's own handler that ends the process early, not Docker.

Obelisk's own stops were never the problem: they send DoExit, watch the process list,
and only signal once the server is gone. Everything else on the host - a backup plugin,
the Docker tab's Stop and Restart, an array stop, a reboot, Watchtower - sends SIGTERM
straight in. So the guard gives every one of them Obelisk's semantics:

  1. ask the server to exit over RCON (DoExit), on this map's RCON port, from inside the
     container, with the admin password the compose file already hands POK;
  2. wait until no process whose FIRST TOKEN is ArkAscendedServer.exe remains - the
     same rule as cluster.is_server_process, read from /proc - bounded by a budget
     comfortably under stop_grace_period;
  3. only then pass the signal on to POK, wait for it, and exit with its status.

If RCON cannot be reached the wait still happens: never forward into a server that may
still be writing. If the budget runs out the signal is forwarded anyway and said loudly
- Docker SIGKILLs the container at the grace period whatever happens here. And when no
server process exists at all, which is what every Obelisk stop looks like by the time
its signal arrives, the signal is forwarded at once.

WHERE IT SITS. tini stays PID 1 - it is POK's own entrypoint, it reaps zombies, and it
forwards a signal to its direct child only. The guard is that child, so the signal
reaches the guard and not POK, and POK's init.sh runs under the guard as an ordinary
child (forked, never exec'd - an exec would hand the signal straight back to POK).

The environment it reads, all set by the generated compose file:
    RCON_PORT, SERVER_ADMIN_PASSWORD     POK's own variables
    OBELISK_STOP_GUARD_BUDGET            seconds to wait for the server (default 180)
    OBELISK_STOP_GUARD_PROC              where the process table is (default /proc;
                                         the tests point it at a fake one)
"""

import os
import signal
import socket
import struct
import sys
import time

SERVER_EXE = "ArkAscendedServer.exe"
DEFAULT_CHILD = ["/home/pok/scripts/init.sh"]
DEFAULT_BUDGET = 180
POLL = 0.5

# Source RCON packet types. Auth and exec-command share a number on the way back
# (SERVERDATA_AUTH_RESPONSE is 2, the same as SERVERDATA_EXECCOMMAND), which is why the
# reply to an auth is told apart by its id and not by its type.
AUTH = 3
EXEC = 2


def say(text):
    """Into the container's own log, where `docker logs` and POK's output both land."""
    sys.stderr.write("[obelisk stop guard] %s\n" % text)
    sys.stderr.flush()


# ---------------------------------------------------------------- is the server there

def is_server_process(command):
    """The FIRST token, basename on either separator, case ignored - and nothing else.

    A copy of cluster.is_server_process, because this file cannot import it. Two of
    the three lines in a running map that mention the exe are wrappers in front of it
    (`python3 .../proton run ArkAscendedServer.exe`, `c:\\windows\\system32\\steam.exe
    ArkAscendedServer.exe`), and a containment test would wait for ever on the launcher.
    test_stopguard pins the two copies to the same answers.
    """
    line = str(command or "").strip()
    if not line:
        return False
    first = line.split()[0].strip("'\"")
    first = first.replace(chr(92), "/").rsplit("/", 1)[-1]
    return first.casefold() == SERVER_EXE.casefold()


def server_running(proc=None):
    """Is an ARK server process alive in this container? Read from /proc each time.

    cmdline is NUL-separated; Wine sometimes rewrites argv into one space-separated
    string. Joining on spaces reads both the same way. A process that vanishes between
    the listing and the read is simply not there.
    """
    proc = proc or os.environ.get("OBELISK_STOP_GUARD_PROC") or "/proc"
    try:
        entries = os.listdir(proc)
    except OSError:
        return False
    for pid in entries:
        if not pid.isdigit():
            continue
        try:
            with open(os.path.join(proc, pid, "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        line = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        if is_server_process(line):
            return True
    return False


# ---------------------------------------------------------------- RCON, by hand

def packet(req_id, kind, body):
    """One Source RCON packet: little-endian size, id, type, the body, two NULs.

    Size counts everything after itself - id (4) + type (4) + body + the two
    terminators - and not the size field.
    """
    data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    payload = struct.pack("<ii", int(req_id), int(kind)) + data + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _read_packet(sock):
    """(id, type, body) for one reply."""
    head = _read_exact(sock, 4)
    (size,) = struct.unpack("<i", head)
    if size < 10 or size > 1 << 20:
        raise ValueError("an RCON reply of %d bytes is not a packet" % size)
    rest = _read_exact(sock, size)
    req_id, kind = struct.unpack("<ii", rest[:8])
    return req_id, kind, rest[8:-2].decode("utf-8", "replace")


def _read_exact(sock, n):
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("the server closed the RCON connection")
        out += chunk
    return out


def rcon(port, password, command, host="127.0.0.1", timeout=10):
    """(ok, text). Authenticate, send one command, read one reply if one comes.

    DoExit often answers with nothing at all - the server is on its way out - so a
    reply that does not arrive after a sent command is still a command that was sent.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.sendall(packet(1, AUTH, password or ""))
            # Some servers send an empty RESPONSE_VALUE before the auth answer.
            for _ in range(2):
                req_id, kind, _body = _read_packet(sock)
                if req_id == -1:
                    return False, "the admin password was refused"
                if kind == EXEC and req_id == 1:
                    break
            sock.sendall(packet(2, EXEC, command))
            try:
                _rid, _kind, body = _read_packet(sock)
            except (OSError, ConnectionError, ValueError):
                body = ""
            return True, body.strip() or "sent"
    except (OSError, ValueError, ConnectionError, struct.error) as e:
        return False, str(e) or e.__class__.__name__


# ---------------------------------------------------------------- the guarded stop

def budget():
    try:
        return max(0.0, float(os.environ.get("OBELISK_STOP_GUARD_BUDGET")
                              or DEFAULT_BUDGET))
    except ValueError:
        return float(DEFAULT_BUDGET)


def let_it_exit(signum, clock=time.monotonic, sleep=time.sleep, running=None,
                ask=None):
    """Everything between a stop signal arriving and it being passed on. Returns why.

    `running` and `ask` are server_running and a DoExit over rcon, handed in for tests.
    """
    running = running or server_running
    if ask is None:
        def ask():
            return rcon(os.environ.get("RCON_PORT") or "27020",
                        os.environ.get("SERVER_ADMIN_PASSWORD") or "", "DoExit")
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = str(signum)

    if not running():
        say("%s received and no ARK server is running - passing it on at once" % name)
        return "no server"

    say("%s received while the ARK server is running - asking it to exit over RCON "
        "first, so it is not killed in the middle of writing its world" % name)
    ok, why = ask()
    if ok:
        say("DoExit sent (%s); waiting up to %ds for the server to finish and exit"
            % (why, budget()))
    else:
        say("DoExit could not be sent (%s) - waiting up to %ds for the server anyway, "
            "because passing the signal on now could interrupt a save" % (why, budget()))

    started = clock()
    while running():
        if clock() - started >= budget():
            say("!!! THE ARK SERVER IS STILL RUNNING AFTER %ds. Passing %s on anyway - "
                "Docker kills this container at its grace period regardless. If this "
                "world will not load afterwards, restore it from a save point. !!!"
                % (budget(), name))
            return "budget"
        sleep(POLL)
    say("the ARK server has exited after %.0fs - passing %s on" % (clock() - started,
                                                                    name))
    return "exited"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv) or list(DEFAULT_CHILD)
    pending = []

    def on_stop(signum, _frame):
        pending.append(signum)

    def pass_on(signum, _frame):
        try:
            os.kill(child, signum)
        except OSError:
            pass

    # Installed before the fork, so there is no instant in which a stop could reach the
    # guard with the default action (exit) still in place. The child resets them.
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            os.execvp(argv[0], argv)
        except OSError as e:
            say("could not start %s: %s" % (" ".join(argv), e))
        os._exit(127)
    for other in (signal.SIGHUP, signal.SIGUSR1, signal.SIGUSR2, signal.SIGQUIT):
        signal.signal(other, pass_on)

    status = None
    forwarded = False
    while status is None:
        if pending and not forwarded:
            signum = pending[0]
            let_it_exit(signum)
            forwarded = True
            try:
                os.kill(child, signum)
            except OSError:
                pass
        elif len(pending) > 1 and forwarded:
            # A second stop while the first is being handled is the same stop.
            del pending[1:]
        try:
            pid, got = os.waitpid(child, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == child:
            status = got
            break
        time.sleep(0.2)

    if status is None:
        return 0
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


if __name__ == "__main__":
    sys.exit(main())
