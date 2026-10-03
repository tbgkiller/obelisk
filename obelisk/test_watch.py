"""The crash watch: what replaced `restart: unless-stopped`.

The policy brought back anything that exited, which is why a deliberate DoExit turned
into a ten-minute boot loop. The default is `restart: no` now, and the cost is that a
genuine crash leaves a map down until somebody notices. This is the thing that notices -
and the difference between it and the policy is that it acts on a RECORD of what Obelisk
meant the map to be doing, which a container exit cannot tell you.

THE OWNER'S RULE: "Get this wrong in the safe direction: if intent is ambiguous, do NOT
relaunch." Most of what follows is that sentence.

NARROW on purpose, which is why it is a module of its own rather than another section of
test_app. `crash_pass` was split out of the async loop so the decision could be tested
without a clock, and this tests that decision and nothing else: Docker, the start verb,
the announcer, the lock, the restart policy and the container names are all handed in.
What is left is app.py's own branches.

Two crash-watch sections deliberately stay in test_app rather than moving here: the
ambiguous record in every shape it arrives in, and the budget running out. Both are
intent.py's invariants seen through the watch rather than app.py's own, and test_intent
is the module that pins them - a second module going red on the same mutation is a
mutation that is not targeted.

Fixture values are synthetic throughout.
"""

import inspect, os, sys, tempfile

from . import announce
from . import app as appmod
from . import intent
from . import ui
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


_cw_store = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
_cw_store.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
_cw_store.patch({"maps": "island,ragnarok", "admin_password": "pw",
                 "cluster_id": "cwtest", "host_ram_gb": 256, "crash_watch": True})

_CW_NAMES = {"The Island": ("asa-cwtest-island", "island"),
             "Ragnarok": ("asa-cwtest-ragnarok", "ragnarok")}


def _cw_details(states):
    """Docker's answer about these containers. A name left out is one it did not answer
    about - a hiccup, or a container that has been removed - and neither is evidence."""
    def details(names):
        return {n: {"state": states[n]} for n in names if n in states}
    return details


class _CwStarts:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def start(self, store, key, record=True):
        self.calls.append((key, record))
        return (self.ok, "started" if self.ok else "docker said no")


def _cw_pass(store, states, starts=None, locked=False, policy="no", twice=True):
    """One decided pass, after the map has already been seen down once.

    `twice=False` is the FIRST look at a map that is down, which must never act: a
    recreate, a restart and an apply all pass through `exited` on the way somewhere
    else.
    """
    starts = starts or _CwStarts()
    seen = {k for _n, k in _CW_NAMES.values()} if twice else set()
    out = appmod.crash_pass(
        store, details=_cw_details(states), start=starts.start,
        say=lambda *a, **k: _cw_said.append((a[0], a[1], k.get("level", "info"))),
        locked=lambda: locked, policy=lambda _s: policy,
        names=lambda _s: dict(_CW_NAMES), seen_down=seen)
    return out, starts


_cw_said = []
_DOWN_BOTH = {"asa-cwtest-island": "exited", "asa-cwtest-ragnarok": "exited"}
_UP_BOTH = {"asa-cwtest-island": "running", "asa-cwtest-ragnarok": "running"}

# 1. the map Obelisk meant to be up, and the map it stopped on purpose, side by side.
#    This is the whole difference from the restart policy, in one pass.
intent.remember(_cw_store, "island", intent.UP, "start")
intent.remember(_cw_store, "ragnarok", intent.DOWN, "apply")
_cw_said.clear()
_out, _starts = _cw_pass(_cw_store, _DOWN_BOTH)
check("a map that is down and was meant to be up is started again",
      _out["relaunched"] == ["island"], _out)
check("and a map Obelisk stopped on purpose is left alone - the policy could not tell "
      "those apart", [c for c in _starts.calls if c[0] == "ragnarok"] == [],
      _starts.calls)
check("the relaunch does not record a new intent, which is what keeps it bounded",
      _starts.calls == [("island", False)], _starts.calls)
check("and it is announced, naming the map and the budget it is spending",
      any(e == "cluster.map_relaunched" and "The Island" in t and "1 of 3" in t
          for e, t, _l in _cw_said), _cw_said)
check("with the out-of-band stop said out loud, because it cannot be detected",
      any("outside Obelisk" in t for _e, t, _l in _cw_said), _cw_said)

# 2. the container has to be POSITIVELY down. A Docker that did not answer about a
#    container, and a container that has been removed, both arrive as an absent answer -
#    and neither is a reason to start a server.
intent.remember(_cw_store, "island", intent.UP, "start")
_cw_said.clear()
_o_h, _s_h = _cw_pass(_cw_store, {})
check("a Docker that did not answer is not a map that is down", _s_h.calls == [],
      _s_h.calls)
_o_r, _s_r = _cw_pass(_cw_store, {"asa-cwtest-island": "restarting"})
check("nor is a container Docker calls restarting", _s_r.calls == [], _s_r.calls)
_o_up, _s_up = _cw_pass(_cw_store, _UP_BOTH)
check("nor is one that is running", _s_up.calls == [], _s_up.calls)

# 3. one look is not enough. A recreate passes through `exited` on its way up.
_o_1, _s_1 = _cw_pass(_cw_store, _DOWN_BOTH, twice=False)
check("a map seen down for the first time is watched, not acted on", _s_1.calls == [],
      _s_1.calls)
check("but it is remembered, so the next pass can act", "island" in _o_1["down"], _o_1)

# ...and the count starts again after a relaunch. A container that has just been told to
# come up is not yet a container that is up, and reading that gap as a second confirmed
# sighting would spend the whole budget in three passes on a map that was starting
# normally.
intent.remember(_cw_store, "island", intent.UP, "start")
_o_2, _s_2 = _cw_pass(_cw_store, _DOWN_BOTH)
check("a map that was just relaunched is not counted as still seen down",
      _s_2.calls == [("island", False)] and "island" not in _o_2["down"], _o_2)

# 4. an apply is in flight. It stops ten maps on purpose and takes minutes over it.
_o_l, _s_l = _cw_pass(_cw_store, _DOWN_BOTH, locked=True)
check("nothing is relaunched while an apply holds the lock", _s_l.calls == [],
      _s_l.calls)
check("and it says why rather than going quiet", "apply is in flight" in _o_l["skipped"],
      _o_l)

# 5. THE ADDENDUM'S RULE: Docker owns recovery when the policy is on.
_o_p, _s_p = _cw_pass(_cw_store, _DOWN_BOTH, policy="unless-stopped")
check("the watch stands down entirely while the restart policy is unless-stopped",
      _s_p.calls == [], _s_p.calls)
check("saying that Docker is doing it, not that nothing is wrong",
      "Docker is doing this" in _o_p["skipped"], _o_p)

# 6. the switch means what it says
_cw_store.patch({"crash_watch": False})
_o_o, _s_o = _cw_pass(_cw_store, _DOWN_BOTH)
check("a watch that is switched off relaunches nothing", _s_o.calls == [], _s_o.calls)
_cw_store.patch({"crash_watch": True})

# 7. a map it has already given up on. Standing down is permanent until a human writes
#    an intent for that map again - and it is not announced a second time, because a
#    line every two minutes for hours is how a channel gets muted.
intent.remember(_cw_store, "island", intent.UP, "start")
intent.stand_down(_cw_store, "island", "it has already been brought back 3 times")
_cw_said.clear()
_o_sd, _s_sd = _cw_pass(_cw_store, _DOWN_BOTH)
check("a map the watch stood down on is not started again", _s_sd.calls == [],
      _s_sd.calls)
check("and it is not said again on every pass after the one it happened on",
      [e for e, _t, _l in _cw_said if e == "cluster.watch_stood_down"] == [], _cw_said)
intent.remember(_cw_store, "island", intent.UP, "start")
_o_a, _s_a = _cw_pass(_cw_store, _DOWN_BOTH)
check("an operator starting it by hand puts the watch back on it",
      _s_a.calls == [("island", False)], _s_a.calls)

# 8. a relaunch that fails is a failure, said as one, and it still spent its budget
intent.remember(_cw_store, "island", intent.UP, "start")
_cw_said.clear()
_o_f, _s_f = _cw_pass(_cw_store, _DOWN_BOTH, starts=_CwStarts(ok=False))
check("a relaunch that would not start is reported as a failure",
      _o_f["failed"] == ["island"] and _o_f["relaunched"] == [], _o_f)
check("to the channel, in red",
      any(e == "cluster.relaunch_failed" and l == "error" for e, _t, l in _cw_said),
      _cw_said)
# The reason travels with it, which is what tells a map that would not start from one
# that Obelisk REFUSED to start. start_one now declines to `up` a map whose ARK server
# is alive or unproven - `up` recreates a drifted container, and a recreate is a signal
# into a live server - and that sentence has to reach the channel rather than being
# flattened into "starting it again failed".
check("carrying the reason the start verb gave, whatever that reason was",
      any(e == "cluster.relaunch_failed" and "docker said no" in t
          for e, t, _l in _cw_said), _cw_said)
check("and it still spent a try, so a map that cannot start cannot loop forever",
      intent.relaunches(_cw_store, "island") == 1,
      intent.read(_cw_store, "island"))

# 9. every event it raises renders as something rather than a bullet
for _tail_c in ("map_relaunched", "relaunch_failed", "watch_stood_down",
                "watch_standing_by"):
    check("%s has an icon of its own" % _tail_c,
          announce.ICONS.get(_tail_c) not in (None, "•"),
          announce.ICONS.get(_tail_c))

# 10. the three states of the switch, and the one that must not be hidden: ON, and
#     doing nothing, because Docker has the job.
_note_on = ui.render_crash_watch(True, "unless-stopped")
check("a watch standing down behind the restart policy says so where it is seen",
      "standing down" in _note_on and "unless-stopped" in _note_on, _note_on)
check("and says which of the two has the job of restarting maps",
      "Docker has this job and Obelisk will not" in _note_on, _note_on)
# It may NOT claim Docker is already doing it. The policy is a setting and a container
# keeps the one it was created with, so between changing this and recreating the maps a
# container can be carrying neither watcher - Obelisk stood down on the setting, Docker
# never told by the container. Asserting cover that is not there is the failure mode
# this whole panel exists to prevent, so the banner names the gap instead.
check("without claiming cover a container that was never recreated does not have",
      "not recreated" in _note_on and "neither" in _note_on, _note_on)
check("a watch that is on and working draws no note at all",
      ui.render_crash_watch(True, "no") == "",
      ui.render_crash_watch(True, "no"))
check("and one that is switched off is not blamed on the policy",
      ui.render_crash_watch(False, "unless-stopped") == "",
      ui.render_crash_watch(False, "unless-stopped"))
_note_sd = ui.render_crash_watch(True, "no", ["The Island"])
check("a map the watch gave up on is named where the buttons are",
      "given up on The Island" in _note_sd and "class=problem" in _note_sd, _note_sd)
check("and the operator is told it will stay down until they act",
      "start it yourself" in _note_sd, _note_sd)

# 11. it has to actually be running, or none of the above happens on a real manager.
_ba_src = inspect.getsource(appmod.main)
check("the crash watch is started with the other background watches",
      "crash_watch(store)" in _ba_src, _ba_src[-600:])

# 12. standing down asks after the world, and holds a DAMAGED one for the restore.
#     1 October: the watch gave up on two maps whose worlds were malformed, recorded
#     nothing about why, and they sat down until an apply happened to check them.
from . import updates as _upd                                    # noqa: E402


def _stand_down(world_says):
    st = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
    st.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
    st.patch({"maps": "island,ragnarok", "admin_password": "pw", "cluster_id": "cwtest",
              "host_ram_gb": 256, "crash_watch": True})
    intent.remember(st, "island", intent.UP, "start")
    intent.remember(st, "ragnarok", intent.DOWN, "apply")
    asked, said = [], []
    for _i in range(intent.BUDGET + 1):
        out = appmod.crash_pass(
            st, details=_cw_details(_DOWN_BOTH), start=_CwStarts().start,
            say=lambda *a, **k: said.append((a[0], a[1], k.get("level", "info"))),
            locked=lambda: False, policy=lambda _s: "no",
            names=lambda _s: dict(_CW_NAMES),
            seen_down={k for _n, k in _CW_NAMES.values()},
            world=lambda _s, k: (asked.append(k) or world_says))
    return st, out, asked, said


_st_d, _out_d, _asked_d, _said_d = _stand_down(("damaged", "malformed"))
check("a map stood down on a damaged world is asked about its world, once",
      _asked_d == ["island"], _asked_d)
check("and recorded as held down, damaged, for the automatic restore",
      _upd.held_down(_st_d) == ["The Island"]
      and _upd.held_down_states(_st_d) == {"The Island": "damaged"},
      _upd.state(_st_d).get("held_down"))
check("it is still stood down - the watch's own guarantee is unchanged",
      intent.read(_st_d, "island").get("stood_down"), intent.read(_st_d, "island"))
_sd_d = [t for e, t, _l in _said_d if e == "cluster.watch_stood_down"]
check("and the announcement says it will be restored rather than to start it by hand",
      _sd_d and "restore it automatically" in _sd_d[0]
      and "start it yourself" not in _sd_d[0], _sd_d)

_st_i, _out_i, _asked_i, _said_i = _stand_down(("ok", "intact"))
check("a stand-down over an intact world holds nothing down",
      _upd.held_down(_st_i) == [], _upd.state(_st_i).get("held_down"))
check("and keeps the old advice to start it by hand",
      any("start it yourself" in t for e, t, _l in _said_i
          if e == "cluster.watch_stood_down"), _said_i)
for _not_damage in ("unreachable", "writing", "absent"):
    _st_n, _o, _a, _s = _stand_down((_not_damage, "injected"))
    check("nor over a world that is %s - a restore does not fix that" % _not_damage,
          _upd.held_down(_st_n) == [], _upd.state(_st_n).get("held_down"))


def _boom(_s, _k):
    raise OSError("the share went away")


_st_b = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
_st_b.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
_st_b.patch({"maps": "island", "admin_password": "pw", "cluster_id": "cwtest",
             "host_ram_gb": 256, "crash_watch": True})
check("a world check that raises holds nothing and does not break the watch",
      appmod._hold_if_damaged(_st_b, "The Island", "island", world=_boom) is False
      and _upd.held_down(_st_b) == [])

# 13. the automatic restore pass
_ANN = []


def _ar_store(states, when=1000):
    st = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
    st.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
    st.patch({"maps": "island,ragnarok", "admin_password": "pw", "cluster_id": "cwtest",
              "host_ram_gb": 256})
    _upd.remember(st, held_down={"maps": sorted(states), "when": when,
                                 "states": dict(states)})
    return st


class _ArRestores:
    def __init__(self, ok=True, refused=""):
        self.calls, self.ok, self.refused = [], ok, refused

    def __call__(self, store, key):
        self.calls.append(key)
        if self.ok:
            return True, "The Island is back, rolled back about 54 minutes.", {
                "point": "TheIsland_WP_01.10.2026_06.47.00.ark", "steps": ["s"]}
        return False, "none of its saves would open", (
            {"refused": self.refused} if self.refused else {})


def _ar_pass(st, restores, there=None, states=None, locked=False):
    """`there` is the `docker ps -a` answer (None: the listing failed); `states` is what
    inspect says about each container."""
    _ANN.clear()
    return appmod.auto_restore_pass(
        st, restore=restores, names=lambda _s: dict(_CW_NAMES),
        existing=lambda: there, details=_cw_details(states or {}),
        locked=lambda: locked,
        say=lambda *a, **k: _ANN.append((a[0], a[1], k.get("level", "info"))))


_st_a = _ar_store({"The Island": "damaged", "Ragnarok": "unreachable"})
check("the setting is on by default", bool(_st_a.get("auto_restore")),
      _st_a.get("auto_restore"))
_r = _ArRestores()
_o = _ar_pass(_st_a, _r, there={})
check("the pass restores a damaged map whose container is absent from a listing that "
      "succeeded", _r.calls == ["island"], _r.calls)
check("and never one held for any other reason",
      "ragnarok" not in _r.calls and "Ragnarok" in _o["passed"], _o)
check("success takes it out of the held-down record",
      _upd.held_down(_st_a) == ["Ragnarok"]
      and _upd.held_down_states(_st_a) == {"Ragnarok": "unreachable"},
      _upd.state(_st_a).get("held_down"))
check("and is announced at info, naming what it did",
      any(e == "restore.auto_done" and l == "info" and "54 minutes" in t
          for e, t, l in _ANN), _ANN)
check("with a start line before it, so the channel sees it begin",
      [e for e, _t, _l in _ANN][:1] == ["restore.auto_start"], _ANN)

# positively not running, or nothing
for _what, _there, _states in (
        ("a failed container listing and no inspect answer", None, {}),
        ("a container that exists and is running", {"asa-cwtest-island": "cwtest"},
         {"asa-cwtest-island": "running"}),
        ("a container that is restarting", None, {"asa-cwtest-island": "restarting"})):
    _st_p = _ar_store({"The Island": "damaged"})
    _r = _ArRestores()
    _o = _ar_pass(_st_p, _r, there=_there, states=_states)
    check("nothing is restored over %s" % _what, _r.calls == [], (_r.calls, _o))
    check("and that is not counted as an attempt, so it is tried once it is down (%s)"
          % _what, _upd.restore_tried(_st_p, "The Island") is None,
          _upd.restore_tries(_st_p))
_st_x = _ar_store({"The Island": "damaged"})
_r = _ArRestores()
_ar_pass(_st_x, _r, there={"asa-cwtest-island": "cwtest"},
         states={"asa-cwtest-island": "exited"})
check("a container Docker says has exited is positively down", _r.calls == ["island"],
      _r.calls)

# once per hold
_st_f = _ar_store({"The Island": "damaged"})
_r = _ArRestores(ok=False)
_ar_pass(_st_f, _r, there={})
check("a failed restore leaves the map held down",
      _upd.held_down(_st_f) == ["The Island"], _upd.state(_st_f).get("held_down"))
check("and says so at error, with the reason",
      any(e == "restore.auto_failed" and l == "error" and "none of its saves" in t
          for e, t, l in _ANN), _ANN)
_ar_pass(_st_f, _r, there={})
check("and it is not tried again for the same hold", _r.calls == ["island"], _r.calls)
_upd.remember(_st_f, held_down={"maps": ["The Island"], "when": 2000,
                                "states": {"The Island": "damaged"}})
_ar_pass(_st_f, _r, there={})
check("but a new hold is a new attempt", _r.calls == ["island", "island"], _r.calls)

# a refusal - it looked again and found no damage - is amber, and stays held
_st_r = _ar_store({"The Island": "damaged"})
_ar_pass(_st_r, _ArRestores(ok=False, refused="intact"), there={})
check("a restore that refused is announced as a refusal, not a failure",
      any(e == "restore.auto_refused" and l == "warning" for e, _t, l in _ANN)
      and not any(e == "restore.auto_failed" for e, _t, _l in _ANN), _ANN)
check("and the map stays held for a person to decide",
      _upd.held_down(_st_r) == ["The Island"])

# a restore that raises is a failure, recorded, not a crashed pass
_st_e = _ar_store({"The Island": "damaged"})


def _raises(_s, _k):
    raise RuntimeError("docker went away")


_o_e = _ar_pass(_st_e, _raises, there={})
check("a restore that raises is a failure the pass survives",
      _o_e["failed"] == ["island"] and _upd.restore_tried(_st_e, "The Island"), _o_e)

# serialised, and switchable
_st_l = _ar_store({"The Island": "damaged"})
_r = _ArRestores()
_o_l = _ar_pass(_st_l, _r, there={}, locked=True)
check("nothing is restored while something else holds the lock",
      _r.calls == [] and _o_l["skipped"], _o_l)
_st_l.patch({"auto_restore": False})
_o_off = _ar_pass(_st_l, _r, there={})
check("and nothing at all while the setting is off",
      _r.calls == [] and "off" in _o_off["skipped"], _o_off)

# straight after an apply the gate refused - only for damage, and from inside the lock
_st_t = _ar_store({"The Island": "damaged"})
_r = _ArRestores()
check("a refusal with no damaged world runs no restore",
      appmod.after_refused_apply(_st_t, {"damaged": [], "writing": ["The Island"]},
                                 restore=_r) is None and _r.calls == [], _r.calls)
appmod.after_refused_apply(_st_t, {"damaged": ["The Island"]}, restore=_r,
                           names=lambda _s: dict(_CW_NAMES), existing=lambda: {},
                           say=lambda *a, **k: None)
check("a refusal naming a damaged world runs it, past the lock its caller holds",
      _r.calls == ["island"], _r.calls)

for _fn, _label in ((appmod._scheduled_apply, "the scheduled apply"),
                    (appmod.build_app, "the Apply button")):
    check("%s hands its refusal to the automatic restore" % _label,
          "after_refused_apply(store, " in inspect.getsource(_fn), _label)
check("and the periodic restore is started with the other watches",
      "auto_restore_watch(store)" in inspect.getsource(appmod.main))
for _tail_r in ("auto_done", "auto_failed", "auto_refused", "auto_start"):
    check("%s has an icon of its own" % _tail_r,
          announce.ICONS.get(_tail_r) not in (None, "•"), announce.ICONS.get(_tail_r))

print("\nFAILURES: %s" % fails if fails else "\nall crash watch tests passed")
sys.exit(1 if fails else 0)
