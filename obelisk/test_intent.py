"""What Obelisk meant each map to be doing, and the one rule that decides a relaunch.

The crash watch is the thing that replaced `restart: unless-stopped`, and the whole
difference between it and the policy it replaced is this record. The policy brought back
anything that exited, including the ten maps an apply had just deliberately closed. This
brings back only a map Obelisk has on record as one that should be up.

THE OWNER'S RULE, verbatim: "Get this wrong in the safe direction: if intent is
ambiguous, do NOT relaunch." Most of this file is that one sentence, asked in every way
an ambiguous record can actually arrive.
"""

import os, sys, tempfile

from . import intent
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def fresh():
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "settings.json")).load()
    st.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
    st.patch({"maps": "island,ragnarok", "admin_password": "synthetic-pw",
              "cluster_id": "testcluster", "host_ram_gb": 256})
    return st


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def now(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


# ---- nothing is remembered until something is written
st = fresh()
check("a map nobody has decided anything about is not one to bring back",
      not intent.wants_up(st, "island"), intent.read(st, "island"))
ok, why = intent.may_relaunch(st, "island")
check("and the gate refuses it, rather than falling through to a default",
      not ok, (ok, why))
check("saying so in words about the record, not about the container",
      "recorded as a map that should be up" in why, why)

# ---- up, and only the exact string
st = fresh()
clk = Clock()
intent.remember(st, "island", intent.UP, "start", now=clk.now)
check("a map that was launched is one to bring back", intent.wants_up(st, "island"))
ok, why = intent.may_relaunch(st, "island", now=clk.now)
check("and the gate allows it", ok, why)
check("the record says who decided and when",
      intent.read(st, "island")["by"] == "start"
      and intent.read(st, "island")["at"] == int(clk.now()), intent.read(st, "island"))

# THE RULE, asked in every shape an ambiguous record actually arrives in. None of these
# is "down" - every one of them is something nobody meant, and every one has to read the
# same way as "down" does.
for _name, _value in (("a missing intent key", {}),
                      ("a null intent", {"intent": None}),
                      ("an empty string", {"intent": ""}),
                      ("a capitalised Up", {"intent": "Up"}),
                      ("an upper-case UP", {"intent": "UP"}),
                      ("up with a space on it", {"intent": " up"}),
                      ("a boolean True", {"intent": True}),
                      ("the number 1", {"intent": 1}),
                      ("a spelling from some future version", {"intent": "running"}),
                      ("a record that is not a dict at all", "up"),
                      ("a record that is a list", ["up"]),
                      ("a record that is the bare word", "yes")):
    st_a = fresh()
    st_a.data[intent.STATE] = {"island": _value}
    check("%s is NOT up" % _name, not intent.wants_up(st_a, "island"), _value)
    _ok_a, _why_a = intent.may_relaunch(st_a, "island")
    check("and %s never earns a relaunch" % _name, not _ok_a, (_value, _why_a))

# The state bag itself being the wrong shape is the same answer.
for _bag in (None, [], "up", 7):
    st_b = fresh()
    st_b.data[intent.STATE] = _bag
    check("a whole intent table of %r reads as nothing decided" % (_bag,),
          not intent.wants_up(st_b, "island") and intent.read(st_b, "island") == {},
          _bag)

# ---- down means down, and it is what a stop writes
st = fresh()
intent.remember(st, "island", intent.DOWN, "operator")
check("a map Obelisk stopped is never brought back",
      not intent.wants_up(st, "island"), intent.read(st, "island"))
ok, why = intent.may_relaunch(st, "island")
check("the gate says so", not ok, why)
check("and it is recorded as the operator's decision",
      intent.read(st, "island")["by"] == "operator", intent.read(st, "island"))

# ---- the budget: three in six hours, then it stands down for good
st = fresh()
clk = Clock()
intent.remember(st, "island", intent.UP, "start", now=clk.now)
for _i in range(intent.BUDGET):
    ok, why = intent.may_relaunch(st, "island", now=clk.now)
    check("relaunch %d of the budget is allowed" % (_i + 1), ok, why)
    intent.record_relaunch(st, "island", now=clk.now)
    clk.tick(60)
ok, why = intent.may_relaunch(st, "island", now=clk.now)
check("the fourth is not - the budget is spent", not ok, why)
check("and it says how many and over what window",
      "brought back 3 times in 6 hours" in why, why)
check("the budget is a small number, defensibly so", intent.BUDGET == 3, intent.BUDGET)

# It is a ROLLING window, not a counter that never resets. A map that fell over three
# times last night is not a map that may never be brought back again.
clk.tick(intent.WINDOW + 1)
ok, why = intent.may_relaunch(st, "island", now=clk.now)
check("once the window has rolled past, the budget is there again", ok, why)
check("and the old timestamps are dropped rather than accumulating",
      intent.relaunches(st, "island", now=clk.now) == 0,
      intent.read(st, "island")["relaunches"])

# ---- standing down is permanent until a human acts
st = fresh()
intent.remember(st, "island", intent.UP, "start")
intent.stand_down(st, "island", "it has already been brought back 3 times in 6 hours")
ok, why = intent.may_relaunch(st, "island")
check("a map the watch stood down on is not tried again", not ok, why)
check("even though its intent is still up - the stand-down wins",
      intent.wants_up(st, "island"), intent.read(st, "island"))
check("the reason travels with it, so the page can say why",
      "stood down" in why and "3 times" in why, why)
check("and it is listed for whoever is looking",
      intent.stood_down(st) == {"island": "it has already been brought back 3 times in "
                                          "6 hours"}, intent.stood_down(st))

# A human writing an intent is the reset. That is the whole of "until a human acts".
intent.remember(st, "island", intent.UP, "start")
ok, why = intent.may_relaunch(st, "island")
check("an operator starting it again clears the stand-down", ok, why)
check("and the relaunch history with it", intent.stood_down(st) == {},
      intent.stood_down(st))

# ...including a stop, because a map that is deliberately down and then launched again
# deserves a fresh budget rather than last week's.
st = fresh()
clk = Clock()
intent.remember(st, "island", intent.UP, "start", now=clk.now)
intent.record_relaunch(st, "island", now=clk.now)
intent.record_relaunch(st, "island", now=clk.now)
intent.remember(st, "island", intent.DOWN, "operator", now=clk.now)
intent.remember(st, "island", intent.UP, "start", now=clk.now)
check("a stop and a relaunch reset the budget", intent.relaunches(st, "island",
                                                                 now=clk.now) == 0,
      intent.read(st, "island"))

# ---- it survives a manager restart, because that is the entire point
#
# The manager image is pinned :latest and Watchtower restarts it on any push. In-memory
# intent would be erased at a moment nobody chose, and an empty memory reading as "up"
# would relaunch every map the operator had just stopped.
st = fresh()
intent.remember(st, "island", intent.DOWN, "apply")
intent.remember(st, "ragnarok", intent.UP, "start")
again = Store(st.path).load()
check("intent is read back off disk by a manager that restarted",
      not intent.wants_up(again, "island") and intent.wants_up(again, "ragnarok"),
      again.data.get(intent.STATE))
check("and so is who decided it",
      intent.read(again, "island")["by"] == "apply", intent.read(again, "island"))

# ---- a map that leaves the cluster leaves the record
st = fresh()
intent.remember(st, "island", intent.UP, "start")
intent.forget(st, "island")
check("forgetting a map leaves nothing behind to act on",
      intent.read(st, "island") == {} and not intent.wants_up(st, "island"))

# ---- a store that cannot be written is not a crash
st = fresh()


def _boom():
    raise OSError("read-only filesystem")


st.save = _boom
intent.remember(st, "island", intent.DOWN, "operator")
check("a stop whose intent could not be saved is still a stop", True)

print("\nFAILURES: %s" % fails if fails else "\nall intent tests passed")
sys.exit(1 if fails else 0)
