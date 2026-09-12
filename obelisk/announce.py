"""
Saying what Obelisk is doing, where an admin will actually see it.

Two audiences and one source. Every significant action writes a **structured log line**
- that is the record, it is greppable, and it survives the Discord bot being offline,
misconfigured or never set up at all. The same line is then mirrored to the Discord
admin channel, because an admin who is not sitting in front of the web UI still needs
to know that a backup started, a restore replaced a world, or the cluster is updating.

The log is primary and Discord is the mirror, deliberately. Losing the bot must never
lose the audit trail, and nothing here waits on Discord to succeed.

Actions run wherever they run - a backup on a worker thread, a restore on another, the
relay in an asyncio loop - so this is a thread-safe queue that the relay drains. An
announcement made while Discord is down is not lost; it is delivered when the bot comes
back, which is exactly when somebody is most likely to be asking what happened.

**Nothing secret is ever announced.** Callers are trusted to describe rather than dump,
and then not trusted at all: every string is scrubbed against the values the store says
are secrets before it is logged or sent. A password that reaches this module by accident
- inside an exception message, say - does not reach the log or the channel.
"""

import collections
import json
import logging
import os
import queue
import tempfile
import threading
import time

log = logging.getLogger("obelisk.announce")

MAX_PENDING = 500
_pending = queue.Queue(maxsize=MAX_PENDING)
_secrets = []

# ---- the feed the UI reads
#
# A *second* consumer, not a second queue. The Discord queue is drained - pop_all takes
# items away, because the relay has to know what it has already posted - so the UI
# cannot read from it without stealing announcements from the channel. Both are fed at
# say(), and this one is never emptied: it is a window on the last few hundred events
# that anything can read, as many times as it likes.
#
# That is what makes drift impossible. The UI and Discord are not two descriptions of
# what happened; they are two readers of one list.
RING = 400
_recent = collections.deque(maxlen=RING)
_lock = threading.Lock()
_next_id = [1]
_revision = [0]


def guard_secrets(values):
    """Register the values that must never appear in an announcement.

    Called with the store's secrets at startup. Held as strings only so that nothing
    here can read the store later and widen what it knows.
    """
    global _secrets
    _secrets = sorted({str(v) for v in values if v and len(str(v)) >= 6},
                      key=len, reverse=True)
    return len(_secrets)


def scrub(text):
    """Any registered secret in this text, replaced. Longest first, so a password that
    contains another one does not leave a fragment behind."""
    out = str(text)
    for s in _secrets:
        if s in out:
            out = out.replace(s, "[redacted]")
    return out


def say(event, text, level="info", detail=None, slot=None, slot_end=False,
        **fields):
    """Record something worth an admin knowing. Never raises, never blocks.

    `event` is a dotted machine name - backup.start, restore.failed, update.phase - so
    the log can be filtered by it. `text` is the sentence a person reads.

    `detail` is the long version: every problem rather than the first three, the whole
    per-mod list, the tail of a log. It goes to the feed and the log, and Discord gets
    the sentence. That asymmetry is deliberate - a chat channel wants one readable line,
    and the admin who needs the full story should not have to leave the UI to get it.

    `slot` names a running story rather than a moment - "the stop that is happening now".
    Everything said into the same slot replaces the last thing said into it, so a
    sequence that takes five minutes is one message in the channel that keeps changing
    instead of six that scroll. It changes nothing about the feed: every stage is still
    its own entry there, because the feed is the record and the channel is the glance.
    """
    text = scrub(text)
    extra = " ".join("%s=%s" % (k, scrub(v)) for k, v in sorted(fields.items())
                     if v not in (None, ""))
    detail = scrub(detail) if detail else ""
    line = "EVENT %s %s%s" % (event, text, (" | " + extra) if extra else "")
    getattr(log, level if level in ("info", "warning", "error") else "info")(line)
    if detail:
        log.info("EVENT %s detail: %s", event, detail.replace("\n", " / ")[:2000])

    item = {"event": event, "text": text, "fields": extra, "level": level,
            "at": time.time(), "detail": detail, "slot": slot or "",
            "slot_end": bool(slot_end)}

    # The feed first, and outside the try. Whether Discord can take an announcement has
    # nothing to do with whether the UI should show it - and the queue filling up is
    # exactly when somebody is most likely to be looking for what went wrong.
    with _lock:
        item["id"] = _next_id[0]
        _next_id[0] += 1
        _revision[0] += 1
        _recent.append(item)

    try:
        _pending.put_nowait(dict(item))
    except queue.Full:
        # The log and the feed already have it. Dropping the mirror is the right way to
        # fail: an admin channel that is down must not be able to stall a backup.
        log.warning("announcement queue is full - %s not mirrored to Discord", event)


def recent(limit=100, since=None):
    """Newest first, for the UI. Never removes anything.

    `since` is an event id: pass the newest one you have and get only what arrived
    after it, which is what makes a polling feed cheap.
    """
    with _lock:
        items = list(_recent)
    if since is not None:
        try:
            floor = int(since)
        except (TypeError, ValueError):
            floor = 0
        items = [i for i in items if i.get("id", 0) > floor]
    items.reverse()
    return items[:limit]


def newest_id():
    with _lock:
        return _recent[-1].get("id", 0) if _recent else 0


def revision():
    """Bumped on every announcement, so a persister can tell if there is new work."""
    return _revision[0]


def save_to(path):
    """Persist the feed, so an admin who opens the UI tomorrow sees what happened.

    Written the way the store is - a temporary file and a rename - because the thing
    being protected against is a half-written file after a crash, and a truncated
    history that cannot be parsed is worse than one that stops a few events early.
    """
    with _lock:
        items = list(_recent)
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "events": items}, fh)
        os.replace(tmp, path)
        return True, ""
    except OSError as e:
        return False, str(e)


def load_from(path):
    """Put a saved feed back, oldest first, and carry on numbering after it.

    Ids have to keep climbing across a restart or the UI's "anything newer than N?"
    poll would be answered with events it has already shown.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    items = [i for i in (data.get("events") or []) if isinstance(i, dict)]
    with _lock:
        _recent.clear()
        highest = 0
        for item in items[-RING:]:
            item.setdefault("detail", "")
            item.setdefault("level", "info")
            highest = max(highest, int(item.get("id") or 0))
            _recent.append(item)
        _next_id[0] = highest + 1
    return len(items)


def pop_all(limit=20):
    """Everything waiting, oldest first. The relay calls this on its own schedule."""
    out = []
    while len(out) < limit:
        try:
            out.append(_pending.get_nowait())
        except queue.Empty:
            break
    return out


def pending():
    return _pending.qsize()


ICONS = {
    "start": "▶", "done": "✅", "failed": "❌",
    "warning": "⚠", "phase": "…",
}


def format_for_discord(item):
    """One line, readable at a glance in a channel that is mostly quiet.

    Deliberately not the whole story. `detail` is left out here and shown in the UI,
    because a channel is for noticing that something happened and the UI is for finding
    out what - and pasting forty lines of per-mod results into chat helps nobody.
    """
    tail = item["event"].rsplit(".", 1)[-1]
    icon = ICONS.get(tail, "•")
    body = "%s **%s** %s" % (icon, item["event"], item["text"])
    if item["fields"]:
        body += "\n`%s`" % item["fields"]
    if item.get("detail"):
        body += "\n_(full detail on the Activity page)_"
    return body[:1900]
