"""
Background loops: every one is guarded, and every setting that switches one on is run.

From 11 October: `empty_watch` raised NameError on every pass for a day. Two things let
that ship. The path only ran with "Apply waiting changes when nobody is on" switched on,
and no test ever ran the loop with it on. And the loop's catch-all logged one line and
went round again, so a broken loop looked exactly like a quiet one.

So, here:
  1. The guard: the same exception N times running is one error event, a backoff and a
     red banner on the Cluster page - and a recovery event when it works again.
  2. Every background task main() starts is guarded, checked from the source so a new
     loop cannot be added without one.
  3. Every setting that switches on a loop's acting branch gets that loop run for real,
     at least one full pass, with the setting on: the update window, both staging modes,
     the crash watch and the automatic restore. (Apply-when-empty has its own suite,
     test_empty_watch.)

No game servers: Docker, the apply, the prime and the restore are handed in.
"""

import asyncio
import inspect
import io
import os
import re
import sys
import tempfile
import time

from . import announce, app, loopguard, ui
from . import updates as upd
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def drain():
    out, batch = [], announce.pop_all(limit=100)
    while batch:
        out += batch
        batch = announce.pop_all(limit=100)
    return out


class Stop(Exception):
    pass


def stopper(passes):
    """A sleep that ends the loop after `passes` waits, recording what it was asked."""
    asked = []

    async def sleep(seconds):
        asked.append(seconds)
        if len(asked) >= passes:
            raise Stop()
    return sleep, asked


# ================================================================ 1. the guard

said = []


def say(event, text, level="info", **fields):
    said.append((event, text, level))


slept = []


async def fake_sleep(seconds):
    slept.append(seconds)


clock = [1000.0]
g = loopguard.Guard("demo", "The demo loop", say=say, sleep=fake_sleep,
                    now=lambda: clock[0])
boom = RuntimeError("name 'lock' is not defined")


async def fail_n(n, exc=boom):
    for _ in range(n):
        g.tick()
        await g.failed(exc)


asyncio.run(fail_n(2))
check("two failures are still just bad passes: nothing said, no backoff",
      said == [] and slept == [] and "demo" not in loopguard.FAILING, (said, slept))
asyncio.run(fail_n(1))
check("the third identical failure is one error event",
      [s[0] for s in said] == ["loop.failing"] and said[0][2] == "error", said)
check("naming the loop and the exception",
      "The demo loop" in said[0][1] and "NameError" not in said[0][1]
      and "name 'lock' is not defined" in said[0][1], said[0][1])
check("and it backs off", slept == [60], slept)
check("and the Cluster page has it", [r["name"] for r in loopguard.failing()] == ["demo"],
      loopguard.failing())
asyncio.run(fail_n(3))
check("further identical failures are not said again", len(said) == 1, said)
check("and the backoff doubles", slept == [60, 120, 240, 480], slept)
asyncio.run(fail_n(10))
check("up to a cap", max(slept) == loopguard.CAP, slept)

# A different exception starts the count again - it is a new problem, not the same one.
g2 = loopguard.Guard("demo2", "Another loop", say=say, sleep=fake_sleep)
said.clear()
slept.clear()


async def mixed():
    for exc in (boom, ValueError("x"), boom, ValueError("x"), boom):
        g2.tick()
        await g2.failed(exc)


asyncio.run(mixed())
check("alternating errors are not 'the same error N times'", said == [] and slept == [],
      (said, slept))

# Recovery: a pass that reaches the next tick without failing - including one that
# ended with `continue` - is a success.
said.clear()
g.tick()          # the pass after the last failure, which did not fail
g.tick()          # ...is judged here
check("a loop that works again says so once",
      [s[0] for s in said] == ["loop.recovered"], said)
check("and leaves the page", "demo" not in loopguard.FAILING, loopguard.FAILING)

html = ui.render_loop_failures([{"label": "The demo loop", "count": 5,
                                 "error": "RuntimeError: boom",
                                 "retry_at": time.time() + 600}])
check("the banner is red and names the loop and the error",
      "class=problem" in html and "The demo loop" in html and "RuntimeError: boom" in html,
      html)
check("and is absent when nothing is failing", ui.render_loop_failures([]) == "")


# ================================================================ 2. every loop guarded

main_src = inspect.getsource(app.main)
started = re.findall(r"asyncio\.create_task\((\w+)\(", main_src)
# These three are not passes on a timer: serve() is the web server, the supervisor and
# the Discord fallback have their own restart-and-say logic (relay.failing,
# discord.admin_unreachable).
OWN_HANDLING = {"serve", "relay_supervisor", "discord_admin_fallback"}
unguarded = [n for n in started if n not in OWN_HANDLING
             and "loopguard.Guard(" not in inspect.getsource(getattr(app, n))]
check("main() starts the loops this expects", len(started) >= 12, started)
check("every background loop main() starts is guarded", unguarded == [], unguarded)
for n in started:
    if n in OWN_HANDLING:
        continue
    src = inspect.getsource(getattr(app, n))
    check("%s ticks every pass and reports failures to its guard" % n,
          "guard.tick()" in src and "await guard.failed(e)" in src)

page = inspect.getsource(app.build_app)
check("the Cluster page draws the banner", "render_loop_failures(loopguard.failing())"
      in page)


# ================================================================ 3. the settings, run

ARK = tempfile.mkdtemp()
os.environ["OBELISK_ARK"] = ARK
os.makedirs(os.path.join(ARK, "ServerFiles"), exist_ok=True)
io.open(os.path.join(ARK, "ServerFiles", "appmanifest_2430930.acf"), "w",
        encoding="utf-8").write('"AppState"\n{\n\t"buildid"\t\t"25763660"\n}\n')


def store(**kw):
    st = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
    st.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
    base = {"maps": "island,ragnarok", "admin_password": "pw", "cluster_id": "lgtest",
            "host_ram_gb": 256, "ark_update_mode": "obelisk"}
    base.update(kw)
    st.patch(base)
    return st


STATUS = {"build": {"running": "25763660", "latest": "25814268", "newer": True,
                    "problem": ""},
          "mods": [], "mods_newer": [], "any_newer": True, "any_missing": False,
          "unknown": False}


def run_update_watch(st, passes=1):
    """ark_update_watch for real, with the network, the prime and the apply handed in."""
    primes, applies = [], []
    real = (upd.look, upd.prime, app._scheduled_apply)
    upd.look = lambda store, root, **kw: dict(STATUS)

    def fake_prime(store, root, target=None, **kw):
        primes.append(target)
        return True, "primed", {}

    def fake_apply(store, force=False, opening=None, opening_event=None):
        applies.append((opening_event, opening))
        return True, "applied", {}

    upd.prime, app._scheduled_apply = fake_prime, fake_apply
    sleep, _asked = stopper(passes)
    drain()
    try:
        asyncio.run(app.ark_update_watch(st, panel={}, sleep=sleep))
    except Stop:
        pass
    finally:
        upd.look, upd.prime, app._scheduled_apply = real
    return primes, applies, drain()


# ---- the update window, switched on
def clock12(hour, minute):
    """The settings page's own format: "4:00 AM"."""
    return "%d:%02d %s" % ((hour % 12) or 12, minute, "AM" if hour % 24 < 12 else "PM")


now = time.localtime()
window = dict(update_apply_in_window=True,
              update_window_start=clock12((now.tm_hour - 1) % 24, now.tm_min),
              update_window_end=clock12((now.tm_hour + 1) % 24, now.tm_min),
              staging_mode="off")
st = store(**window)
upd.remember(st, primed={"ok": True, "build": "25814268", "loaded": {}, "when": 1,
                         "target": upd.target_key(STATUS)})
primes, applies, events = run_update_watch(st)
check("update window on: one full pass applies the staged build",
      len(applies) == 1, applies)
check("and announces it through the apply, as ark.window_open",
      applies and applies[0][0] == "ark.window_open", applies)
check("and the loop's guard saw no failure", "ark_update_watch" not in loopguard.FAILING,
      loopguard.FAILING)
st_off = store(**dict(window, update_apply_in_window=False))
upd.remember(st_off, primed={"ok": True, "build": "25814268", "loaded": {}, "when": 1,
                             "target": upd.target_key(STATUS)})
_p, applies_off, _e = run_update_watch(st_off)
check("and with it off the same pass applies nothing", applies_off == [], applies_off)

# ---- staging: always, and on demand, each switched on with nothing staged
for mode in ("always", "on_demand"):
    st = store(staging_mode=mode)
    primes, applies, _e = run_update_watch(st)
    check("staging %s: one full pass stages the new build" % mode,
          primes == [upd.target_key(STATUS)], primes)
    check("staging %s: and applies nothing on its own" % mode, applies == [], applies)
st = store(staging_mode="off")
primes, _a, _e = run_update_watch(st)
check("staging off: nothing is staged", primes == [], primes)

# ---- the crash watch, switched on: a map down on two looks is brought back
NAMES = {"The Island": ("asa-lgtest-island", "island"),
         "Ragnarok": ("asa-lgtest-ragnarok", "ragnarok")}
cst = store(crash_watch=True)
app.intentctl.remember_many(cst, ["island", "ragnarok"], app.intentctl.UP, "operator")
starts = []
sleep, _asked = stopper(2)
drain()
try:
    asyncio.run(app.crash_watch(
        cst, sleep_first=False, sleep=sleep,
        details=lambda names: {n: {"state": "exited" if "island" in n else "running"}
                               for n in names},
        start=lambda store, key, record=True: (starts.append(key) or (True, "started")),
        say=lambda *a, **k: None, locked=lambda: False, policy=lambda _s: "no",
        names=lambda _s: dict(NAMES), world=lambda _s, _k: ("ok", "intact"),
        external=lambda _k: None))
except Stop:
    pass
check("crash watch on: two full passes bring a crashed map back", starts == ["island"],
      starts)
check("and its guard saw no failure", "crash_watch" not in loopguard.FAILING,
      loopguard.FAILING)

# ---- the automatic restore, switched on: a held, damaged map is restored
ast = store(auto_restore=True)
upd.remember(ast, held_down={"maps": ["The Island"], "when": 1000,
                             "states": {"The Island": "damaged"}})
restores = []
sleep, _asked = stopper(1)
try:
    asyncio.run(app.auto_restore_watch(
        ast, sleep_first=False, sleep=sleep,
        restore=lambda store, key: (restores.append(key)
                                    or (True, "The Island is back.", {"steps": []})),
        names=lambda _s: dict(NAMES), existing=lambda: {}, details=lambda names: {},
        say=lambda *a, **k: None))
except Stop:
    pass
check("automatic restore on: one full pass restores the damaged map",
      restores == ["island"], restores)
check("and its guard saw no failure", "auto_restore_watch" not in loopguard.FAILING,
      loopguard.FAILING)
ast_off = store(auto_restore=False)
upd.remember(ast_off, held_down={"maps": ["The Island"], "when": 1000,
                                 "states": {"The Island": "damaged"}})
restores.clear()
sleep, _asked = stopper(1)
try:
    asyncio.run(app.auto_restore_watch(
        ast_off, sleep_first=False, sleep=sleep,
        restore=lambda store, key: (restores.append(key) or (True, "", {})),
        names=lambda _s: dict(NAMES), existing=lambda: {}, details=lambda names: {},
        say=lambda *a, **k: None))
except Stop:
    pass
check("and with it off nothing is restored", restores == [], restores)

# ---- and a loop that breaks for real is caught by its guard
loopguard.FAILING.clear()
real_look = upd.look
upd.look = lambda store, root, **kw: (_ for _ in ()).throw(NameError("name 'x' is not "
                                                                      "defined"))
sleep, asked = stopper(4)
drain()
try:
    asyncio.run(app.ark_update_watch(store(staging_mode="always"), panel={},
                                     sleep=sleep))
except Stop:
    pass
finally:
    upd.look = real_look
ev = [e for e in drain() if e["event"] == "loop.failing"]
check("a real loop failing the same way three times raises loop.failing",
      len(ev) == 1 and "ARK update check" in ev[0]["text"], ev)
check("and backs off on top of its interval", 60 in asked, asked)
check("and is on the Cluster page", "ark_update_watch" in loopguard.FAILING,
      loopguard.FAILING)

print("\nFAILURES: %s" % fails if fails else "\nall loop guard tests passed")
sys.exit(1 if fails else 0)
