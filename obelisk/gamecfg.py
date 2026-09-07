"""
The bridge between Obelisk's settings and the game's two INI files.

Two directions, and the asymmetry between them is the point.

**Reading is greedy.** On startup every catalogued key that exists in the files is
adopted into the store, so the admin page opens showing what the server is actually
running rather than a screen of defaults. An operator who has spent months tuning a
cluster should recognise their own settings the first time they see this page; showing
them a blank 1.0 next to a value that is really 10.0 would be worse than showing nothing.

**Writing is stingy.** Only keys the operator has actually set are written, and only
through ini.py, which edits lines in place. Nothing renders a file from scratch, so a
key Obelisk has never heard of cannot be lost by writing one it has - and that includes
the mod section, the comment banner and the seventeen repeated engram lines that no part
of this module knows exist.
"""

import logging

from . import gamesettings, ini, layout
from .schema import SETTINGS

log = logging.getLogger("obelisk.gamecfg")

FILES = ("GameUserSettings", "Game")


def config_dir(store):
    return layout.ark_paths(layout.ark_root_of(store))["shared_config"]


def path_of(store, which):
    return "%s/%s.ini" % (config_dir(store), which)


def targets():
    """(setting, file, section, key) for every schema entry backed by an INI key."""
    out = []
    for s in SETTINGS:
        t = str(s.get("target") or "")
        if not t.startswith("ini:"):
            continue
        _tag, which, section, key = t.split(":", 3)
        out.append((s, which, section, key))
    return out


def _to_ini(setting, value):
    """A store value as the game writes it."""
    if setting["type"] == "bool":
        return "True" if value in (True, "true", "True", "TRUE", 1) else "False"
    return str(value)


def _from_ini(setting, raw):
    """An INI value as the store holds it. None means 'leave this one alone'."""
    raw = str(raw).strip()
    t = setting["type"]
    try:
        if t == "bool":
            low = raw.lower()
            if low in ("true", "1"):
                return True
            if low in ("false", "0"):
                return False
            return None
        if t == "int":
            return int(float(raw))
        if t == "float":
            return float(raw)
    except (TypeError, ValueError):
        return None
    return raw


def adopt(store, read=None):
    """Pull the live INI values into the store. Returns (adopted, skipped).

    Runs every start. It is not an import-once: the files are the operator's to edit by
    hand, and a value changed there should show up here rather than being silently
    overwritten by whatever the store remembered from last week.
    """
    read = read or ini.read
    docs = {}
    for which in FILES:
        docs[which] = read(path_of(store, which))

    adopted, skipped = 0, []
    for setting, which, section, key in targets():
        raw = docs[which].get(section, key)
        if raw is None:
            continue
        value = _from_ini(setting, raw)
        if value is None:
            skipped.append("%s=%s" % (key, raw))
            continue
        if store.data["cluster"].get(setting["key"]) != value:
            store.data["cluster"][setting["key"]] = value
        adopted += 1
    if adopted:
        log.info("adopted %d game setting(s) from the INI files", adopted)
    if skipped:
        log.info("left alone, unreadable as %s: %s",
                 "the type expected", ", ".join(skipped[:5]))
    return adopted, skipped


def apply(store, backup=True, merge=None):
    """Write the settings the operator has set back to the INI files. (ok, message).

    Only keys present in the store are written - a key the operator has never touched
    stays out of the file entirely rather than being written at whatever this schema
    happens to call its default, which would be Obelisk inventing configuration.
    """
    merge = merge or ini.merge_file
    docs = {w: ini.read(path_of(store, w)) for w in FILES}
    changes = {which: {} for which in FILES}
    for setting, which, section, key in targets():
        if setting["key"] not in store.data["cluster"]:
            continue
        value = store.data["cluster"][setting["key"]]
        current = docs[which].get(section, key)

        # A key that is not in the file and is sitting at its own default is not a
        # decision anybody made - it is a default that got persisted into settings.json
        # at some point and now looks identical to a deliberate choice. Writing it adds
        # a line the operator never asked for to a file they wrote by hand. Presence in
        # the store is not evidence of intent; differing from the default is.
        # ...but only when the default is the game's, not a placeholder. Skipping on a
        # made-up default would silently refuse to write a value the operator did choose.
        if (current is None and setting.get("default_known")
                and value == setting.get("default")):
            continue

        # Compare against what the file means, not how it spells it. `15` and `15.0`
        # are the same number, and rewriting the line to change one into the other is a
        # diff in the operator's file, a .bak, and a restart notice, in exchange for
        # nothing. Only a real difference in value earns a write.
        if current is not None and _from_ini(setting, current) == value:
            continue
        changes[which][(section, key)] = _to_ini(setting, value)

    # The stat grids live in the same file, so they ride along in the same write - one
    # .bak, one restart notice, one diff.
    for target, value in grid_changes(store, docs["Game"]).items():
        changes["Game"][target] = value

    # Arrays are lists of lines rather than one line, so they are handed over as such -
    # ini.py turns them into the smallest edit that gets from one to the other.
    for (which, section, key), values in row_changes(store, docs).items():
        changes[which][(section, key)] = values

    written, notes = [], []
    for which in FILES:
        if not changes[which]:
            continue
        ok, detail = merge(path_of(store, which), changes[which], backup=backup)
        if ok:
            written.append("%s.ini" % which)
        elif detail != "no change":
            notes.append("%s.ini: %s" % (which, detail))

    if notes:
        return False, "; ".join(notes)
    if not written:
        return True, "Game settings already match the INI files."
    return True, ("Wrote %s. The maps read these at start, so restart the cluster for "
                  "them to take effect. The previous files are kept as .bak."
                  % " and ".join(written))


# ---------------------------------------------------------------------------------
# Per-level stat multipliers.
#
# Sparse on purpose. This operator's file sets two of the twelve stats in each of four
# families, and a grid editor that helpfully wrote all sixty back would turn eight lines
# into sixty - every one of them a value nobody chose, in a file somebody organised by
# hand. Only cells that are in the file, or that the operator has just typed, exist.

def _cell_key(family, index):
    return "%s[%d]" % (family, index)


def read_grids(store, doc=None):
    """{family: {index: value}} as the file has it. Absent cells simply are not there."""
    doc = doc if doc is not None else ini.read(path_of(store, "Game"))
    out = {}
    for family, _name, _help in gamesettings.STAT_FAMILIES:
        cells = {}
        for index, _stat in gamesettings.STATS:
            raw = doc.get(gamesettings.GAME_MODE, _cell_key(family, index))
            if raw is None:
                continue
            try:
                cells[index] = float(raw)
            except (TypeError, ValueError):
                continue
        if cells:
            out[family] = cells
    return out


def adopt_grids(store, doc=None):
    """Pull the grids into the store. Returns how many cells were found."""
    grids = read_grids(store, doc)
    store.data.setdefault("stats", {})
    total = 0
    for family, cells in grids.items():
        store.data["stats"][family] = {str(i): v for i, v in cells.items()}
        total += len(cells)
    return total


def grid_changes(store, doc=None):
    """{(section, key): value-or-None} for the cells that actually differ from the file.

    None means remove the line: a cell the operator has cleared. Everything else is left
    out entirely, which is what keeps a sparse family sparse.
    """
    doc = doc if doc is not None else ini.read(path_of(store, "Game"))
    wanted = store.data.get("stats", {}) or {}
    changes = {}
    for family, _name, _help in gamesettings.STAT_FAMILIES:
        # A family the store says nothing about is a family we leave entirely alone.
        # Without this, a store that had not adopted the grids yet - a fresh install, or
        # any caller that skipped adopt_grids - looked exactly like an operator who had
        # just cleared every cell, and apply() deleted every stat line in the file.
        if family not in wanted:
            continue
        mine = wanted.get(family, {}) or {}
        for index, _stat in gamesettings.STATS:
            key = _cell_key(family, index)
            raw = doc.get(gamesettings.GAME_MODE, key)
            here = mine.get(str(index), mine.get(index))
            if here in (None, ""):
                if raw is not None:
                    changes[(gamesettings.GAME_MODE, key)] = None   # cleared
                continue
            try:
                value = float(here)
            except (TypeError, ValueError):
                continue
            if raw is not None and float(raw) == value:
                continue
            changes[(gamesettings.GAME_MODE, key)] = _fmt(value)
    return changes


def _fmt(value):
    """Numbers the way the file writes them: 3.0, not 3."""
    text = ("%f" % value).rstrip("0")
    return text + "0" if text.endswith(".") else text


# ---------------------------------------------------------------------------------
# Repeated-key arrays, as rows.
#
# The shape that is easiest to destroy: seventeen lines sharing one key, which
# configparser would collapse to the last one. ini.py keeps them, and everything here
# is arranged so that a row survives untouched unless somebody edits that row.

def split_fields(body):
    """(Field=Value, ...) -> [(field, raw)], respecting quotes. None if it is nested.

    Nested tuples are refused rather than half-understood. A row editor that flattened
    ConfigOverrideSupplyCrateItems would be guessing at a shape it cannot put back.
    """
    text = body.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return None
    inner, out, buf, quoted, depth = text[1:-1], [], "", False, 0
    for ch in inner:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "([":
            depth += 1
        elif not quoted and ch in ")]":
            depth -= 1
        if ch == "," and not quoted and depth == 0:
            out.append(buf)
            buf = ""
            continue
        buf += ch
    if buf.strip():
        out.append(buf)
    if depth != 0:
        return None
    pairs = []
    for part in out:
        if "=" not in part:
            return None
        name, _eq, value = part.partition("=")
        if "(" in value or "[" in value:
            return None                      # nested: not ours to edit
        pairs.append((name.strip(), value.strip()))
    return pairs


def join_fields(pairs):
    return "(%s)" % ",".join("%s=%s" % (k, v) for k, v in pairs)


def read_rows(store, docs=None):
    """{array key: [[(field, raw)], ...]} exactly as the file has them, in file order."""
    docs = docs or {w: ini.read(path_of(store, w)) for w in FILES}
    out = {}
    for spec in gamesettings.ROW_ARRAYS:
        raws = docs[spec["file"]].get_all(spec["section"], spec["key"])
        rows = []
        for raw in raws:
            pairs = split_fields(raw)
            if pairs is None:                # leave the whole array alone if any row
                rows = None                  # is a shape we cannot put back
                break
            rows.append(pairs)
        if rows:
            out[spec["key"]] = rows
    return out


def adopt_rows(store, docs=None):
    """Pull the arrays into the store. Returns how many rows were found."""
    rows = read_rows(store, docs)
    store.data.setdefault("rows", {})
    total = 0
    for key, value in rows.items():
        store.data["rows"][key] = [[[k, v] for k, v in row] for row in value]
        total += len(value)
    return total


def row_changes(store, docs=None):
    """{(section, key): [line values]} for arrays whose rows actually differ."""
    docs = docs or {w: ini.read(path_of(store, w)) for w in FILES}
    held = store.data.get("rows", {}) or {}
    changes = {}
    for spec in gamesettings.ROW_ARRAYS:
        key = spec["key"]
        # An array the store says nothing about is one we do not touch. Without this a
        # store that had not adopted the rows yet is indistinguishable from an operator
        # who deleted every one of them - the same trap the stat grids sprang in Phase 3.
        if key not in held:
            continue
        current = docs[spec["file"]].get_all(spec["section"], key)
        if any(split_fields(r) is None for r in current):
            continue                          # a shape we refused to read, so do not write
        wanted = [join_fields([(k, v) for k, v in row]) for row in held[key]]
        if wanted != current:
            changes[(spec["file"], spec["section"], key)] = wanted
    return changes
