"""
Editing an INI file that somebody else wrote.

ARK's two config files are not Obelisk's. They arrive with a history: hand-written
comment banners, sections belonging to mods, keys repeated seventeen times because that
is how the engine expresses a list, and an ordering the person who made it can navigate.
Obelisk models a fraction of what is in them and always will.

So this does not generate a file. It edits one, and the rule it exists to enforce is:

    a setting Obelisk does not model is a setting Obelisk does not touch.

Everything follows from that. The document keeps the file's own lines, byte for byte,
including their line endings - so a parse-and-write round trip with no changes produces
the input exactly, and the test that proves it runs against the real 172-key file this
was written for. A key that is changed has its own line rewritten and nothing else
moves. A key that is new is placed inside its section rather than at the end of the
file. Sections nobody here has heard of are carried through untouched, which is the
whole point: `[GaiaEssentials]` belongs to a mod, and a config tool that eats it because
it did not recognise it has destroyed something it was never asked to manage.

configparser cannot do this. It discards comments, reorders on write, and collapses
repeated keys to the last one - which for `OverrideNamedEngramEntries` means silently
deleting sixteen of seventeen engram overrides.
"""

import logging, os, re, shutil

log = logging.getLogger("obelisk.ini")

_SECTION = re.compile(r"^\s*\[([^\]]*)\]\s*$")
# A key line. Deliberately not anchored on a comment character: ARK values contain
# semicolons inside quoted class names, so only a leading ; or # is a comment.
_ENTRY = re.compile(r"^(\s*)([^;#=\s][^=]*?)(\s*)=(.*)$")


def _split(text):
    """Lines with their endings kept, so joining them reproduces the input exactly."""
    return text.splitlines(keepends=True)


def _ending(lines, default="\n"):
    for raw in lines:
        if raw.endswith("\r\n"):
            return "\r\n"
        if raw.endswith("\n"):
            return "\n"
    return default


class Doc:
    """An INI file as the lines it is actually made of."""

    def __init__(self, text=""):
        self.lines = _split(text)
        self.eol = _ending(self.lines)

    # ------------------------------------------------------------------ reading
    def text(self):
        return "".join(self.lines)

    def _walk(self):
        """(index, section, key, value) for every line, section None before the first."""
        section = None
        for i, raw in enumerate(self.lines):
            body = raw.rstrip("\r\n")
            m = _SECTION.match(body)
            if m:
                section = m.group(1)
                yield i, section, None, None
                continue
            m = _ENTRY.match(body)
            if m:
                yield i, section, m.group(2), m.group(4)
            else:
                yield i, section, None, None

    def sections(self):
        out = []
        for _i, sec, key, _v in self._walk():
            if key is None and sec is not None and sec not in out:
                out.append(sec)
        return out

    def get_all(self, section, key):
        """Every value for this key, in file order. ARK uses repeats as a list."""
        return [v for _i, sec, k, v in self._walk()
                if sec == section and k is not None and k.lower() == key.lower()]

    def get(self, section, key, default=None):
        """The effective scalar: the last one wins, which is what the engine does."""
        vals = self.get_all(section, key)
        return vals[-1] if vals else default

    def has(self, section, key):
        return bool(self.get_all(section, key))

    # ------------------------------------------------------------------ writing
    def _rows(self, section, key=None):
        return [(i, k) for i, sec, k, _v in self._walk()
                if sec == section and k is not None
                and (key is None or k.lower() == key.lower())]

    def _section_line(self, section):
        for i, sec, k, _v in self._walk():
            if k is None and sec == section:
                return i
        return None

    def set(self, section, key, value):
        """Set a scalar. Rewrites the existing line in place, keeping its own layout.

        Repeats of the same key are removed, because for a scalar they are leftovers and
        the engine would take the last one regardless - but only when we were already
        going to write this key. Nothing is tidied for its own sake.
        """
        value = "" if value is None else str(value)
        rows = self._rows(section, key)
        if rows:
            first = rows[0][0]
            body = self.lines[first].rstrip("\r\n")
            end = self.lines[first][len(body):] or self.eol
            m = _ENTRY.match(body)
            self.lines[first] = "%s%s%s=%s%s" % (m.group(1), m.group(2), m.group(3),
                                                 value, end)
            for i, _k in reversed(rows[1:]):
                del self.lines[i]
            return self
        return self._insert(section, key, value)

    def set_all(self, section, key, values):
        """Replace a repeated key with exactly these values, in this order.

        As a minimal edit, not a rewrite. Deleting seventeen lines and writing seventeen
        back produces a seventeen-line diff for a one-word change, and buries the edit
        somebody actually made in noise - in a file they will read again later, and in
        the .bak they would have to compare against if something went wrong. Adding a row
        adds a line, removing one removes a line, changing one changes a line.
        """
        import difflib
        rows = self._rows(section, key)
        values = [str(v) for v in values]
        if not rows:
            for v in values:
                self._insert(section, key, v)
            return self

        at = [i for i, _k in rows]
        current = [_ENTRY.match(self.lines[i].rstrip("\r\n")).group(4) for i in at]
        line_for = lambda v: "%s=%s%s" % (key, v, self.eol)   # noqa: E731 - one shape

        # Work back to front so earlier indices stay valid as lines move.
        ops = difflib.SequenceMatcher(a=current, b=values, autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in reversed(ops):
            if tag == "equal":
                continue
            if tag == "replace":
                for off in range(min(i2 - i1, j2 - j1)):
                    body = self.lines[at[i1 + off]].rstrip("\r\n")
                    end = self.lines[at[i1 + off]][len(body):] or self.eol
                    self.lines[at[i1 + off]] = "%s=%s%s" % (key, values[j1 + off], end)
                for i in reversed(range(i1 + (j2 - j1), i2)):
                    del self.lines[at[i]]
                for off in range(i2 - i1, j2 - j1):
                    self.lines.insert(at[i2 - 1] + 1 + (off - (i2 - i1)),
                                      line_for(values[j1 + off]))
            elif tag == "delete":
                for i in reversed(range(i1, i2)):
                    del self.lines[at[i]]
            elif tag == "insert":
                where = (at[i1] if i1 < len(at) else at[-1] + 1)
                for off, v in enumerate(values[j1:j2]):
                    self.lines.insert(where + off, line_for(v))
        return self

    def unset(self, section, key):
        for i, _k in reversed(self._rows(section, key)):
            del self.lines[i]
        return self

    def _insert(self, section, key, value):
        """Put a new key inside its section, after the last thing already in it.

        Appending to the end of the file would work for the engine and be wrong for the
        person: their file is organised, and a tool that scatters keys outside the
        section they belong to has made the file harder to read every time it runs.
        """
        line = "%s=%s%s" % (key, value, self.eol)
        head = self._section_line(section)
        if head is None:
            if self.lines and not self.lines[-1].endswith(("\n", "\r")):
                self.lines[-1] += self.eol
            if self.lines and self.lines[-1].strip():
                self.lines.append(self.eol)
            self.lines.append("[%s]%s" % (section, self.eol))
            self.lines.append(line)
            return self
        rows = self._rows(section)
        at = (rows[-1][0] + 1) if rows else head + 1
        self.lines.insert(at, line)
        return self


def parse(text):
    return Doc(text)


def read(path):
    """The file as a Doc. A file that is not there yet is an empty one, not an error."""
    try:
        with open(path, encoding="utf-8-sig", errors="surrogateescape") as fh:
            return Doc(fh.read())
    except FileNotFoundError:
        return Doc("")


def merge_file(path, changes, backup=True):
    """Apply {(section, key): value} to the file at `path`. Returns (changed, detail).

    Writes nothing at all when nothing differs - which is the cheapest way to guarantee
    that a no-op cannot corrupt anything - and keeps the previous file beside the new
    one so every write can be undone by hand.
    """
    doc = read(path)
    before = doc.text()
    for (section, key), value in changes.items():
        if value is None:
            doc.unset(section, key)
        elif isinstance(value, (list, tuple)):
            doc.set_all(section, key, value)
        else:
            doc.set(section, key, value)
    after = doc.text()
    if after == before:
        return False, "no change"

    if backup and os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError as e:
            return False, "could not keep a backup of %s, so it was not written: %s" % (path, e)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8", newline="", errors="surrogateescape") as fh:
        fh.write(after)
    os.replace(tmp, path)
    log.info("wrote %s (%d change(s), previous kept as %s.bak)",
             path, len(changes), os.path.basename(path))
    return True, "wrote %d change(s)" % len(changes)
