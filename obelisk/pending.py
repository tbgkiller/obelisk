"""
Changes that are waiting for a safe moment.

Some settings cannot take effect without restarting every map, and a restart costs
whoever is playing. So they queue instead: the operator changes what they like, whenever
they like, and the batch lands once - when the cluster is empty, when the update window
opens, or when somebody decides it is worth interrupting people for.

**This is mostly making an existing behaviour honest.** Saving a recreate-class setting
already did nothing to a running cluster; the value went into settings.json and sat
there until the next Launch happened to pick it up. That deferral was invisible,
unbatched and applied by whatever restarted the cluster next - which might be a restore,
or a crash recovery, carrying a config change nobody meant to apply just then.

**The queue is an overlay and never a write.** That is the whole design and the reason
this file exists rather than a `needs_restart` flag. `compose.py` and `plan.py` read the
store, so a pending value written into the store *is* an applied value the moment
anything regenerates the stack. Held to one side, a pending change cannot be applied by
accident: it takes the batch, deliberately, and the batch is the only thing that moves
it across.

Applying is therefore two writes that have to succeed or fail together - the files and
the settings - so `snapshot()` records exactly what the store held before, and
`restore()` puts it back. A batch that fails leaves the previous build *and* the
previous configuration, which is the only state a cluster can be safely restarted into.
"""

import logging
import time

from .schema import BY_KEY, scope_of

log = logging.getLogger("obelisk.pending")

STATE = "pending"          # store.data[STATE] - manager state, not a setting

# Changing this renames the compose project, which means the running containers stop
# being recognised as this cluster's. Not a reason to refuse it - people do rename
# clusters - but it is not the same kind of change as raising a RAM cap, and the UI
# should not present it as though it were.
LOUD = {
    "cluster_id": ("This renames the compose project. The running containers belong to "
                   "the old name, so applying it recreates all of them and anything "
                   "outside Obelisk that refers to the old project - scripts, the "
                   "Compose Manager entry - stops matching."),
    "admin_password": ("Until this is applied, Obelisk keeps using the current password "
                       "to reach the running maps, which is what keeps chat relay and "
                       "in-game commands working. Both change over at the restart."),
    "game_port_base": ("Players connect to these ports. Changing them means anyone with "
                       "a saved favourite has to be told the new address."),
    "rcon_port_base": ("Anything outside Obelisk that speaks RCON to these maps will "
                       "need the new ports."),
    "maps": ("Removing a map stops that server. Its world is left on disk untouched, "
             "but nobody can reach it until the map is added back."),
}


def _state(store):
    got = store.data.get(STATE)
    return dict(got) if isinstance(got, dict) else {}


def _save(store, data):
    if data.get("cluster") or data.get("maps"):
        store.data[STATE] = data
    else:
        store.data.pop(STATE, None)
    try:
        store.save()
    except OSError as e:
        log.warning("could not record pending changes: %s", e)
    return data


def queued(store):
    """{"cluster": {...}, "maps": {map: {...}}, "since": ts} - never the live values."""
    got = _state(store)
    return {"cluster": dict(got.get("cluster") or {}),
            "maps": {m: dict(v) for m, v in (got.get("maps") or {}).items()},
            "since": got.get("since") or 0}


def count(store):
    got = queued(store)
    return len(got["cluster"]) + sum(len(v) for v in got["maps"].values())


def any_pending(store):
    return count(store) > 0


def stageable(key):
    """Does a change to this setting have to wait? Only if it restarts the maps."""
    return scope_of(key) == "maps"


def split(changes, store, map_name=None):
    """(apply now, wait for a safe moment) for a dict of validated changes.

    Split on scope rather than on a list kept here, so a setting added to the schema
    lands in the right half without this file being touched. A change that does not
    differ from what is already live is in neither half - "set it to what it already is"
    is not a pending change, and queueing one would make the panel lie about how much is
    waiting.
    """
    now, later = {}, {}
    for key, value in (changes or {}).items():
        if key not in BY_KEY:
            continue
        if not stageable(key):
            now[key] = value
            continue
        if _same(store.get(key, map_name=map_name), value):
            continue
        later[key] = value
    return now, later


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return str(a) == str(b)


def stage(store, changes, map_name=None, now=None):
    """Queue changes. Returns what was queued, keyed the way the UI shows it.

    A later edit of the same setting replaces the earlier one rather than stacking, and
    setting something back to its live value removes it from the queue entirely - so the
    panel always describes the difference between what is running and what was asked
    for, not a history of keystrokes.
    """
    now = now or time.time
    data = queued(store)
    staged = {}
    for key, value in (changes or {}).items():
        if not stageable(key) or key not in BY_KEY:
            continue
        holder = data["cluster"] if not map_name else data["maps"].setdefault(map_name, {})
        if _same(store.get(key, map_name=map_name), value):
            holder.pop(key, None)          # back to what is running: nothing to do
            continue
        holder[key] = value
        staged[key] = value
    data["maps"] = {m: v for m, v in data["maps"].items() if v}
    if (data["cluster"] or data["maps"]) and not data.get("since"):
        data["since"] = int(now())
    _save(store, data)
    return staged


def discard(store, key=None, map_name=None):
    """Drop one queued change, or all of them. Returns how many went."""
    data = queued(store)
    before = len(data["cluster"]) + sum(len(v) for v in data["maps"].values())
    if key is None:
        data = {"cluster": {}, "maps": {}, "since": 0}
    elif map_name:
        (data["maps"].get(map_name) or {}).pop(key, None)
        data["maps"] = {m: v for m, v in data["maps"].items() if v}
    else:
        data["cluster"].pop(key, None)
    after = len(data["cluster"]) + sum(len(v) for v in data["maps"].values())
    if not (data["cluster"] or data["maps"]):
        data["since"] = 0
    _save(store, data)
    return before - after


def rows(store):
    """What the panel renders: one row per queued change, live value and wanted value."""
    data = queued(store)
    out = []
    for key, value in sorted(data["cluster"].items()):
        out.append(_row(store, key, value, None))
    for map_name in sorted(data["maps"]):
        for key, value in sorted(data["maps"][map_name].items()):
            out.append(_row(store, key, value, map_name))
    return out


def _row(store, key, value, map_name):
    spec = BY_KEY.get(key) or {}
    secret = spec.get("type") == "password"
    return {
        "key": key,
        "map": map_name or "",
        "label": spec.get("label") or key,
        # A password's *value* is never rendered, here or anywhere. That it is changing
        # is the useful fact; what it is changing to is not the panel's business.
        "from": "(unchanged)" if secret else store.get(key, map_name=map_name),
        "to": "(new password)" if secret else value,
        "secret": secret,
        "warning": LOUD.get(key, ""),
    }


# ---------------------------------------------------------------- applying

def snapshot(store):
    """Exactly what the store holds for every queued key, before anything is written.

    Taken from `data` rather than `get()` on purpose: get() falls back to the cluster
    value and then the schema default, so a per-map key with no override would come back
    as the inherited value and be restored as a *new* override that was never there.
    The distinction between "this map says 20g" and "this map says nothing" is the whole
    contract of per-map settings, and a rollback that loses it silently changes the
    cluster it was supposed to put back.
    """
    data = queued(store)
    return {
        "cluster": {k: (store.data.get("cluster", {}).get(k, _MISSING))
                    for k in data["cluster"]},
        "maps": {m: {k: (store.data.get("maps", {}).get(m, {}).get(k, _MISSING))
                     for k in keys}
                 for m, keys in data["maps"].items()},
    }


class _Missing(object):
    """A key that was not there, which is not the same as a key that was empty."""

    def __repr__(self):
        return "<not set>"


_MISSING = _Missing()


def commit(store, validate=None):
    """Write the queued changes into the store for real. (ok, problem, snapshot).

    The snapshot comes back so a caller that fails later can put it back. Nothing is
    written unless every value validates, so a bad one cannot leave the store half
    updated between a stop and a start.
    """
    validate = validate or _validate
    data = queued(store)
    before = snapshot(store)

    for key, value in data["cluster"].items():
        ok, why = validate(key, value)
        if not ok:
            return False, "%s: %s" % (key, why), before
    for map_name, values in data["maps"].items():
        for key, value in values.items():
            ok, why = validate(key, value)
            if not ok:
                return False, "%s on %s: %s" % (key, map_name, why), before

    store.data.setdefault("cluster", {}).update(data["cluster"])
    for map_name, values in data["maps"].items():
        store.data.setdefault("maps", {}).setdefault(map_name, {}).update(values)
    store.data.pop(STATE, None)
    try:
        store.save()
    except OSError as e:
        restore(store, before)
        return False, "could not write the settings: %s" % e, before
    return True, "", before


def _validate(key, value):
    from .settings import Invalid, validate as check
    try:
        check(key, value)
    except Invalid as e:
        return False, str(e)
    return True, ""


def restore(store, before, requeue=None):
    """Put the store back exactly as `snapshot` found it. (ok, problem).

    Keys that were absent are removed rather than written as their inherited value -
    see snapshot(). `requeue` puts the changes back in the queue, which is what a failed
    apply wants: the operator asked for them, they did not happen, and losing them
    silently would be the worst of the three outcomes.
    """
    for key, value in (before.get("cluster") or {}).items():
        if isinstance(value, _Missing):
            store.data.get("cluster", {}).pop(key, None)
        else:
            store.data.setdefault("cluster", {})[key] = value
    for map_name, values in (before.get("maps") or {}).items():
        holder = store.data.setdefault("maps", {}).setdefault(map_name, {})
        for key, value in values.items():
            if isinstance(value, _Missing):
                holder.pop(key, None)
            else:
                holder[key] = value
        if not holder:
            store.data["maps"].pop(map_name, None)
    if requeue:
        store.data[STATE] = {"cluster": dict(requeue.get("cluster") or {}),
                             "maps": {m: dict(v)
                                      for m, v in (requeue.get("maps") or {}).items()},
                             "since": requeue.get("since") or int(time.time())}
    try:
        store.save()
    except OSError as e:
        return False, "could not put the settings back: %s" % e
    return True, ""


def summary(store):
    """One sentence for an announcement."""
    n = count(store)
    if not n:
        return "no changes are waiting"
    return "%d change%s waiting to be applied" % (n, "" if n == 1 else "s")
