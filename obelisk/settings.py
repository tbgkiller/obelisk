"""
The settings store: one source of truth, three ways in.

  * the admin UI      -> Store.patch()
  * the HTTP API      -> Store.patch()   (same path, same validation)
  * a text editor     -> settings.json, then Store.load() revalidates on read

Everything downstream (.env, the game INIs, compose) is *generated* from here, so
the store is the thing worth backing up and the generated files are disposable.
"""

import json, os, re, tempfile

from .schema import SETTINGS, BY_KEY, GROUPS, INSTALL_KEYS

MAPS = ["island", "center", "scorched", "ragnarok", "aberration",
        "extinction", "valguero", "astraeos", "lostcolony", "genesis"]

# Stacking mods have to load first or their stack sizes lose to later mods.
KNOWN_STACKING_MODS = {"929110"}


class Invalid(ValueError):
    """Raised with a message written for a person, not a log file."""


def _as_bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    raise Invalid("must be true or false")


def validate(key, value):
    """Returns the coerced value, or raises Invalid with a readable reason."""
    s = BY_KEY.get(key)
    if not s:
        raise Invalid("no setting called %r" % key)
    t = s["type"]

    if t in ("text", "longtext", "password"):
        v = "" if value is None else str(value)
        if s.get("required") and not v:
            raise Invalid("can't be empty")
        if "max_len" in s and len(v) > s["max_len"]:
            raise Invalid("too long - %d characters, limit is %d" % (len(v), s["max_len"]))
        # Anything that ends up in the in-game server name is checked by the rules that
        # own that field, so the message a person sees is about the browser rather than
        # about a regex.
        if s.get("session_safe") and v.strip():
            from .naming import session_problems
            trouble = [p for p in session_problems(v)
                       if "server name field holds" not in p]   # length is checked whole
            if trouble:
                raise Invalid(trouble[0])
        if s.get("ascii_only") and any(ord(c) > 127 for c in v):
            raise Invalid("use plain ASCII only - accented or fancy characters can stop "
                          "the server appearing in the browser")
        if s.get("no_leading_hash") and v.startswith("#"):
            raise Invalid("can't start with # - a server whose name begins with # runs "
                          "fine and accepts direct connections but never appears in the "
                          "in-game server list")
        if s.get("pattern") and not re.match(s["pattern"], v):
            raise Invalid("doesn't look right (expected pattern %s)" % s["pattern"])
        return v

    if t == "bool":
        return _as_bool(value)

    if t in ("int", "port"):
        try:
            n = int(str(value).strip())
        except Exception:
            raise Invalid("must be a whole number")
        if t == "port":
            if n != 0 and not (1024 <= n <= 65535):
                raise Invalid("must be 0 (off) or between 1024 and 65535")
            return n
        if "min" in s and n < s["min"]:
            raise Invalid("must be at least %s" % s["min"])
        if "max" in s and n > s["max"]:
            raise Invalid("must be at most %s" % s["max"])
        return n

    if t == "float":
        try:
            f = float(str(value).strip())
        except Exception:
            raise Invalid("must be a number")
        if "min" in s and f < s["min"]:
            raise Invalid("must be at least %s" % s["min"])
        if "max" in s and f > s["max"]:
            raise Invalid("must be at most %s" % s["max"])
        return f

    if t == "choice":
        v = str(value)
        if v not in s["choices"]:
            raise Invalid("must be one of: %s" % ", ".join(s["choices"]))
        return v

    if t == "csv":
        raw = value if isinstance(value, list) else str(value).split(",")
        items = [str(x).strip() for x in raw if str(x).strip()]
        pat = s.get("item_pattern")
        for it in items:
            if pat and not re.match(pat, it):
                raise Invalid("%r doesn't look like a valid entry" % it)
        if s.get("stacking_first") and items:
            stackers = [i for i in items if i in KNOWN_STACKING_MODS]
            if stackers and items[0] not in KNOWN_STACKING_MODS:
                raise Invalid("stacking mod %s has to be first in the list, or later mods "
                              "override its stack sizes" % stackers[0])
        return ",".join(items)

    if t == "maps":
        from .maps import BY_KEY as MAP_BY_KEY, KEYS as MAP_KEYS
        raw = value if isinstance(value, list) else str(value).split(",")
        items = [str(x).strip().lower() for x in raw if str(x).strip()]
        if not items:
            raise Invalid("pick at least one map")
        seen = set()
        for it in items:
            if it not in MAP_BY_KEY:
                raise Invalid("%r isn't a map Obelisk knows - choose from: %s"
                              % (it, ", ".join(MAP_KEYS)))
            if it in seen:
                raise Invalid("%r is listed twice" % it)
            seen.add(it)
        return ",".join(items)

    if t == "memory":
        v = str(value).strip().lower()
        if not re.match(r"^\d+[mg]$", v):
            raise Invalid("use a number followed by m or g, e.g. 20g")
        return v

    if t == "times":
        raw = value if isinstance(value, list) else str(value).split(",")
        out = []
        for x in [str(i).strip() for i in raw if str(i).strip()]:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", x):
                raise Invalid("%r isn't a 24-hour time like 06:00" % x)
            out.append(x)
        return ",".join(out)

    if t == "minutes":
        raw = value if isinstance(value, list) else str(value).split(",")
        out = []
        for x in [str(i).strip() for i in raw if str(i).strip()]:
            if not re.match(r"^\d{1,4}$", x):
                raise Invalid("%r isn't a number of minutes" % x)
            out.append(int(x))
        return ",".join(str(n) for n in sorted(set(out), reverse=True))

    raise Invalid("unknown setting type %r" % t)


class Store:
    def __init__(self, path):
        self.path = path
        self.data = {"version": 1, "cluster": {}, "maps": {}}

    # ------------------------------------------------------------ persistence
    def load(self):
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        self.data.setdefault("cluster", {})
        self.data.setdefault("maps", {})
        return self

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)          # holds the Discord token and admin password
        return self

    # ------------------------------------------------------------ reading
    def get(self, key, map_name=None):
        """Effective value: per-map override, else cluster, else the schema default."""
        s = BY_KEY[key]
        if map_name and s.get("per_map"):
            m = self.data["maps"].get(map_name, {})
            if key in m:
                return m[key]
        if key in self.data["cluster"]:
            return self.data["cluster"][key]
        return s.get("default", "")

    def effective(self, map_name=None):
        return {k: self.get(k, map_name) for k in BY_KEY}

    # ------------------------------------------------------------ writing
    def patch(self, changes, map_name=None, source="ui"):
        """Validate everything first, then apply. A bad value changes nothing.

        `source` is "ui" for anything a person or the API is changing at runtime, and
        "install" for the container's own environment at boot. Install-phase settings
        are bind mounts and published ports: Docker fixed them when the container was
        created, so letting the UI edit them would show a value that isn't real until
        somebody recreates the container. They are writable only from "install".

        Returns the set of apply levels touched, so the caller knows whether this
        needs a reload, a recreate, or nothing at all.
        """
        errors, coerced = {}, {}
        for k, v in changes.items():
            if k not in BY_KEY:
                errors[k] = "no setting called %r" % k
                continue
            if map_name and not BY_KEY[k].get("per_map"):
                errors[k] = "%s is cluster-wide - it can't be set per map" % BY_KEY[k]["label"]
                continue
            if source != "install" and k in INSTALL_KEYS:
                errors[k] = ("%s is set when the container is created, not from here. "
                             "Change it in the container's template (or compose) and "
                             "recreate the container." % BY_KEY[k]["label"])
                continue
            try:
                coerced[k] = validate(k, v)
            except Invalid as e:
                errors[k] = str(e)
        if errors:
            raise Invalid(json.dumps(errors))

        target = self.data["maps"].setdefault(map_name, {}) if map_name else self.data["cluster"]
        applied = set()
        for k, v in coerced.items():
            if target.get(k) != v:
                target[k] = v
                applied.add(BY_KEY[k].get("apply", "none"))
        return applied

    def install_settings(self):
        """What the container template controls. Read-only in the UI."""
        return [{"key": k, "label": BY_KEY[k]["label"], "value": self.get(k),
                 "help": BY_KEY[k].get("help", "")} for k in INSTALL_KEYS]

    def readiness(self):
        """What still needs setting before this cluster can start.

        Deliberately separate from validate(): a fresh install has to be able to
        save its own untouched defaults, so 'you must set this eventually' can't
        be the same rule as 'this value is invalid'.

        Returns [] when the cluster is ready to generate.
        """
        blocking = []
        for s in SETTINGS:
            if not s.get("required_before_start"):
                continue
            if not str(self.get(s["key"])).strip():
                blocking.append({"key": s["key"], "label": s["label"],
                                 "why": s.get("help", "").split(".")[0] + "."})
        return blocking

    def validate_all(self):
        """Re-check a hand-edited store. Returns {key: reason} for anything wrong."""
        bad = {}
        for scope, block in [("cluster", self.data["cluster"])] + \
                            [(m, b) for m, b in self.data["maps"].items()]:
            for k, v in block.items():
                if k not in BY_KEY:
                    bad["%s.%s" % (scope, k)] = "no setting called %r" % k
                    continue
                try:
                    validate(k, v)
                except Invalid as e:
                    bad["%s.%s" % (scope, k)] = str(e)
        return bad


# ---------------------------------------------------------------- generation

def _env_value(s, v):
    if s["type"] == "bool":
        return "TRUE" if v else "FALSE"
    return "" if v is None else str(v)


def generate_env(store):
    """The .env POK reads. Regenerated on every apply - edit the store, not this."""
    lines = ["# Generated by Obelisk from settings.json - do not edit.",
             "# Change settings in the admin page or in settings.json, then Apply.",
             ""]
    by_group = {}
    for s in SETTINGS:
        if not s["target"].startswith("env:"):
            continue
        by_group.setdefault(s["group"], []).append(s)
    for g in GROUPS:
        rows = by_group.get(g)
        if not rows:
            continue
        lines.append("# --- %s ---" % g)
        for s in rows:
            lines.append("%s=%s" % (s["target"].split(":", 1)[1],
                                    _env_value(s, store.get(s["key"]))))
        lines.append("")
    # Transitional: settings Obelisk now owns but the running bot still reads from
    # the environment. Emitting them keeps a generated .env a drop-in replacement for
    # the hand-written one - without this, generating .env today would silently wipe
    # the wild-dino schedule. Remove each line once the bot reads it from the store.
    legacy = ["%s=%s" % (s["import_from"], _env_value(s, store.get(s["key"])))
              for s in SETTINGS if s.get("import_from")]
    if legacy:
        lines.append("# --- still read from the environment by the running bot ---")
        lines.extend(legacy)
        lines.append("")

    # per-map overrides, e.g. MEM_LIMIT_ASTRAEOS
    overrides = []
    for m in MAPS:
        for k, v in sorted(store.data["maps"].get(m, {}).items()):
            s = BY_KEY.get(k)
            if s and s.get("per_map") and s["target"].startswith("env:"):
                overrides.append("%s_%s=%s" % (s["target"].split(":", 1)[1], m.upper(),
                                               _env_value(s, v)))
    if overrides:
        lines.append("# --- per-map overrides ---")
        lines.extend(overrides)
        lines.append("")
    return "\n".join(lines)


def generate_ini(store, which):
    """which is 'GameUserSettings' or 'Game'."""
    sections = {}
    for s in SETTINGS:
        parts = s["target"].split(":")
        if parts[0] != "ini" or parts[1] != which:
            continue
        v = store.get(s["key"])
        if s["type"] == "bool":
            if s.get("invert"):
                v = not v
            v = "True" if v else "False"
        sections.setdefault(parts[2], []).append("%s=%s" % (parts[3], v))
    out = ["; Generated by Obelisk from settings.json - do not edit.", ""]
    for sec in sorted(sections):
        out.append("[%s]" % sec)
        out.extend(sorted(sections[sec]))
        out.append("")
    # Obelisk only models the settings in the schema. Anything else the operator
    # needs - mod config blocks, rare options - is passed through untouched.
    extra = store.get("extra_gameusersettings" if which == "GameUserSettings" else "extra_game")
    if str(extra).strip():
        out += ["; --- passed through from Advanced settings ---", str(extra).strip(), ""]
    return "\n".join(out)
