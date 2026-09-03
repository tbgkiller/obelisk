"""
Editing the mod list.

Order is meaningful - a mod earlier in the list wins conflicting remaps - so the list
is edited as an ordered thing with explicit moves, not retyped as a comma-separated
string and hoped over. Every operation returns a new list for `mod_ids` to be validated
by the store, which is where the rules live (numeric ids, stacking mods first).

Health is separate from order and is the question people actually get wrong: a mod can
be listed, present on disk, and still not installed. See docs/GOTCHAS.md.
"""

STUB_RATIO = 2          # a mod with fewer than median/2 files is suspect


def parse(value):
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def join(ids):
    return ",".join(ids)


def add(value, mod_id):
    """Append. New mods go last so they can't silently outrank what already works."""
    ids = parse(value)
    mod_id = str(mod_id).strip()
    if not mod_id.isdigit():
        raise ValueError("a mod id is a number - copy it from the mod's CurseForge page")
    if mod_id in ids:
        raise ValueError("mod %s is already in the list" % mod_id)
    return join(ids + [mod_id])


def remove(value, mod_id):
    ids = parse(value)
    mod_id = str(mod_id).strip()
    if mod_id not in ids:
        raise ValueError("mod %s isn't in the list" % mod_id)
    return join([m for m in ids if m != mod_id])


def move(value, mod_id, delta):
    """Move one mod up (-1) or down (+1). Out-of-range moves are a no-op, not an error."""
    ids = parse(value)
    mod_id = str(mod_id).strip()
    if mod_id not in ids:
        raise ValueError("mod %s isn't in the list" % mod_id)
    i = ids.index(mod_id)
    j = i + int(delta)
    if j < 0 or j >= len(ids):
        return join(ids)
    ids[i], ids[j] = ids[j], ids[i]
    return join(ids)


def health(installs, listed):
    """Turn raw install sizes into a per-mod verdict.

    installs: {mod_id: {"files": n, "kb": n}} as measured on disk
    listed:   the ids the cluster is configured to load

    Compares each install with its siblings rather than a fixed expectation, because
    there is nothing to look an expected size up against. Silent when there are too few
    mods to have an opinion - a wrong "this is broken" is worse than no verdict.
    """
    out = {}
    counts = sorted(v["files"] for v in installs.values())
    median = counts[len(counts) // 2] if counts else 0
    can_judge = len(counts) >= 4

    for mod_id in parse(listed):
        got = installs.get(mod_id)
        if not got:
            out[mod_id] = {"status": "missing", "files": 0, "mb": 0.0,
                           "note": "not on disk - the server hasn't downloaded it"}
            continue
        mb = round(got["kb"] / 1024.0, 1)
        if can_judge and got["files"] * STUB_RATIO <= median:
            out[mod_id] = {"status": "stub", "files": got["files"], "mb": mb,
                           "note": "far smaller than the other mods - a download that "
                                   "failed part way. It will load nothing and say nothing."}
        else:
            out[mod_id] = {"status": "ok", "files": got["files"], "mb": mb, "note": ""}

    for mod_id, got in installs.items():
        if mod_id not in out:
            out[mod_id] = {"status": "orphan", "files": got["files"],
                           "mb": round(got["kb"] / 1024.0, 1),
                           "note": "on disk but not in the list - left over from a "
                                   "mod you removed"}
    return out
