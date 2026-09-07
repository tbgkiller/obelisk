"""
The few things you need a shell for.

Everything Obelisk does lives in the web UI, with one unavoidable exception: getting
back into the web UI. The setup code opens it, the code is printed once on the boot
that generates it, and the only copy after that is inside settings.json - which is
behind a shell, while the page that could show it is behind the code. That is a closed
loop, and it locked a real operator out.

    docker exec <container> obelisk code          # what is the code?
    docker exec <container> obelisk code --new    # give me a different one

Showing it grants nothing new: anyone who can run `docker exec` here can already read
the file. What it saves is having to know the shape of the file. Rotating is the part
that earns its keep - after sharing the code, or when you are not sure who has it.
"""

import os
import secrets
import sys

from .settings import Store

DATA = "/data"
USAGE = """obelisk - the bits that need a shell

  obelisk code           show the setup code for the web UI
  obelisk code --new     replace it with a new one (logs out every browser)

The setup code opens the web UI. It is not the admin/RCON password, which is the
in-game one - see docs/LOCKOUT.md.
"""


def store_path(environ=None):
    environ = os.environ if environ is None else environ
    return os.path.join(environ.get("OBELISK_DATA") or DATA, "settings.json")


def main(argv=None, environ=None, out=None):
    """Returns an exit code, so a caller can test it without a subprocess."""
    argv = list(sys.argv[1:] if argv is None else argv)
    say = (lambda *a: print(*a, file=out or sys.stdout))

    if not argv or argv[0] in ("-h", "--help", "help"):
        say(USAGE.strip())
        return 0
    if argv[0] != "code":
        say("unknown command %r" % argv[0])
        say(USAGE.strip())
        return 2

    path = store_path(environ)
    if not os.path.isfile(path):
        say("no settings at %s - has Obelisk started yet?" % path)
        return 1

    store = Store(path).load()
    if "--new" in argv[1:]:
        code = secrets.token_urlsafe(9)
        store.data["cluster"]["admin_token"] = code
        # Whoever was logged in is logged out: the cookie is the old code, and it will
        # no longer match. That is the point of rotating rather than a side effect.
        store.data.pop("setup_done", None)
        store.save()
        say(code)
        say("")
        say("Rotated. Every browser signed in with the old code is signed out.")
        return 0

    code = str(store.data.get("cluster", {}).get("admin_token") or "").strip()
    if not code:
        say("no setup code yet - start Obelisk once and it will make one")
        return 1
    say(code)
    return 0
