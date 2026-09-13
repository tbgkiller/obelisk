# Proposal: confirm each world's save actually landed before stopping the cluster

Status: **IMPLEMENTED and deployed.** Filed from incident ct-0009 (2026-09-11);
shipped as `cluster.save_and_settle` / `cluster.worlds_settled` and the refuse-before-
stop gate in `updates.apply_batch`, live since 2026-09-12.

Everything below is the original proposal, kept as the record of why. Two things went
further than it asked: the stop now closes each world with RCON `DoExit` first, because
the server image's own shutdown save turned out to be what damaged three worlds on
2026-09-12; and an integrity gate re-checks every world after the stop and before the
swap, so a build is never promoted over a world that will not read.

## The seam

`obelisk/updates.py`, inside the apply path:

```
463:    if save:
464:        step("saving every world")
465:        ok, detail = save()
466:        if not ok:
467:            # Not fatal by itself - a map that is down cannot save and should not block
468:            # the update - but it is said out loud rather than swallowed.
469:            announce.say("ark.apply_note", "SaveWorld: %s" % detail, level="warning")
470:
471:    step("stopping the cluster and the staging server")
472:    ok, detail = stop_all()
```

Nine lines. `save()` returns, `stop_all()` runs. Nothing in between establishes that the
saves finished.

## Why it bites

`cluster.save_world()` (`obelisk/cluster.py:274`) sends RCON `SaveWorld` to each running map
and counts a map as saved the moment the RCON call returns:

```python
try:
    rcon(host, port, "SaveWorld")
    done.append(label)
```

That return means *the server accepted the command*. It does not mean the world was written.
A large world takes tens of seconds to serialise. The docstring is honest about what the
function is for — "best-effort on purpose" — and for its original caller, taking a backup,
best-effort is the right contract. The update path inherited that contract and needs a
stronger one, because what follows it is not a copy, it is a stop.

On 2026-09-11 the cluster was hard-killed roughly 19 s after `save()` returned, while the
larger worlds were still writing. Four maps (Astraeos, TheCenter, Valguero, Ragnarok) were
left with hot journals; The Island was left malformed. The five smaller maps finished in
time and came through clean — which is the signature of a race, not of a broken save.

**There is no fixed delay in this path to lengthen.** The only `time.sleep` in `updates.py`
is the injectable waiter at line 245, used elsewhere. The ~19 s was not a designed pause —
it is simply how long ten sequential RCON round-trips took. Making it 60 s would not fix
this; it would move the cliff and hide it better. A bigger world, a busier host or an
eleventh map puts it right back.

## Proposed fix

Between `save()` and `stop_all()`, prove per map that the save is done. Two signals, both
needed, because either alone can lie:

1. **`SaveWorld` confirmation.** Take the map's RCON reply as the acknowledgement rather
   than discarding it. This needs `cluster.save_world()` to return per-map results instead
   of a summary string — a mapped result the caller can act on, not prose it has to parse.
2. **Quiescence on disk.** For each map's `<map_id>.ark`: its size and mtime stop changing
   across consecutive polls, **and** none of `-wal` / `-shm` / `-journal` sits beside it.
   `restore.SIDECARS` already names those three suffixes; reuse it rather than restating it.

Poll both under a timeout — a per-map budget scaled to world size, or one cluster-wide
budget generous enough for the largest map. The check is cheap: a `stat` and three
`os.path.exists` per poll.

The sidecar half of the condition is the part that matters most. A hot sidecar is not a
timing hint, it is a statement that a transaction is open. Stopping into that is exactly the
failure being prevented.

### Refuse before stop

If a map does not go quiet inside its budget, **do not stop the cluster.** Return a refusal
from the apply path the way the existing `stop_all()` failure at :473–:476 already does:

```python
    if not ok:
        announce.say("ark.update_failed", "Could not stop the cluster: %s" % detail,
                     level="error")
        return False, "could not stop the cluster: %s" % detail, {}
```

Same shape, earlier. The refusal must name the maps that did not settle, and say that the
cluster is still up and still serving.

This is the right default because the two outcomes are not symmetric. A deferred update
costs a postponement — the backstop window will come round again, and `remember(store,
last_apply=...)` at :482 is not reached, so nothing thinks the disruption was spent. Stopping
into an open transaction costs hot journals on live worlds and a gated, owner-only recovery.
An update is always safe to do later. A corrupted world is not always recoverable.

Worth pairing with a force path for the operator who genuinely wants the stop anyway — the
same escape hatch the player-online check at :440–:444 already offers ("Apply with force, or
let the scheduled window do it"). Refusing by default, with a way through, not a wall.

## Both call sites pass `save=`

Both are in `obelisk/app.py` and both reach this seam, so both inherit the fix:

- **`app.py:484`** — `store, _ark_root(), warn=warn, save=lambda: clusterctl.save_world(store),`
- **`app.py:1466`** — `save=lambda: clusterctl.save_world(store), stop_all=stop_all,`

Neither needs to change if `save_world()` grows per-map results while keeping its
`(ok, detail)` shape — but both must be re-checked when it does, since they are the only
things constructing this callable.

(`app.py:985` also passes a `save=`, but it is `save=_save_one` on the single-map restore
path. Different callable, different seam. Out of scope here.)

## Tests this should come with

- Every map goes quiet inside the budget → `stop_all()` is called exactly once.
- One map never goes quiet → `stop_all()` is **not** called; the refusal names that map.
- A map's `.ark` stops growing but a `-journal` remains → still treated as not settled.
  This is the case a size-and-mtime-only check would wave through, and it is the one that
  produced ct-0009.
- A map that is down cannot save → must not block the update, matching the existing
  tolerance at :466–:469.
