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

from . import ini, layout
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
        # Compare against what the file means, not how it spells it. `15` and `15.0`
        # are the same number, and rewriting the line to change one into the other is a
        # diff in the operator's file, a .bak, and a restart notice, in exchange for
        # nothing. Only a real difference in value earns a write.
        current = docs[which].get(section, key)
        if current is not None and _from_ini(setting, current) == value:
            continue
        changes[which][(section, key)] = _to_ini(setting, value)

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
