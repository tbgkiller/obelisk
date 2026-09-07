"""
The shell-side commands, which exist for one reason: getting back into the web UI.

The lockout was a closed loop. The setup code opens the UI. It was printed once, on the
boot that generated it. The only copy afterwards was inside settings.json - behind a
shell - while the page that could have shown it was behind the code. A real operator hit
this, tried the password he knew (the in-game one, which opens nothing here), and was
stuck.
"""

import io, os, sys, tempfile

from .cli import main, store_path
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


def fresh(token="synthetic-code"):
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "settings.json")).load()
    if token:
        st.patch({"admin_token": token})
    st.save()
    return d


def run(args, d):
    out = io.StringIO()
    rc = main(args, environ={"OBELISK_DATA": d}, out=out)
    return rc, out.getvalue()


# ---- show
d = fresh()
rc, text = run(["code"], d)
check("`obelisk code` succeeds", rc == 0, rc)
check("and prints the code, on its own line so it can be copied",
      text.strip() == "synthetic-code", repr(text))

# ---- rotate
rc, text = run(["code", "--new"], d)
first = text.strip().splitlines()[0]
check("`obelisk code --new` succeeds", rc == 0, rc)
check("it prints a different code", first != "synthetic-code", first)
check("it is long enough to be worth having", len(first) >= 10, first)
check("and it says the old sessions are gone",
      "signed out" in text, text)
check("the new code is what the store now holds",
      Store(store_path({"OBELISK_DATA": d})).load().get("admin_token") == first)
rc, text2 = run(["code"], d)
check("and what `obelisk code` reports next time", text2.strip() == first)

rc, third = run(["code", "--new"], d)
check("rotating twice gives a different code again",
      third.strip().splitlines()[0] != first)

# ---- rotating puts the operator back into setup, so the code prints on boot again
st = Store(store_path({"OBELISK_DATA": d})).load()
check("rotating clears the 'setup finished' mark",
      not st.data.get("setup_done"), st.data.get("setup_done"))

# ---- the unhappy paths say something useful
rc, text = run(["code"], tempfile.mkdtemp())
check("a data folder with no settings is explained, not a traceback",
      rc == 1 and "has Obelisk started" in text, (rc, text))

d3 = fresh(token=None)
rc, text = run(["code"], d3)
check("a store with no code yet says so", rc == 1 and "no setup code yet" in text,
      (rc, text))

rc, text = run(["wat"], d)
check("an unknown command is refused", rc == 2, rc)
check("and prints the usage", "obelisk code" in text)

rc, text = run([], d)
check("no arguments prints help rather than doing something", rc == 0)
check("and the help names the two passwords apart",
      "not the admin/RCON password" in text or "admin/RCON" in text, text)

# ---- the stance: the admin password must not be an alternative way in
from . import cli as climod
check("the CLI never reads the admin password",
      "admin_password" not in io.open(climod.__file__, encoding="utf-8").read())

print("\nFAILURES: %s" % fails if fails else "\nall cli tests passed")
sys.exit(1 if fails else 0)
