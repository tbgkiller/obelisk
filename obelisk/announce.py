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

import logging
import queue
import time

log = logging.getLogger("obelisk.announce")

MAX_PENDING = 500
_pending = queue.Queue(maxsize=MAX_PENDING)
_secrets = []


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


def say(event, text, level="info", **fields):
    """Record something worth an admin knowing. Never raises, never blocks.

    `event` is a dotted machine name - backup.start, restore.failed, update.phase - so
    the log can be filtered by it. `text` is the sentence a person reads.
    """
    text = scrub(text)
    extra = " ".join("%s=%s" % (k, scrub(v)) for k, v in sorted(fields.items())
                     if v not in (None, ""))
    line = "EVENT %s %s%s" % (event, text, (" | " + extra) if extra else "")
    getattr(log, level if level in ("info", "warning", "error") else "info")(line)

    try:
        _pending.put_nowait({"event": event, "text": text, "fields": extra,
                             "level": level, "at": time.time()})
    except queue.Full:
        # The log already has it. Dropping the mirror is the right way to fail: an
        # admin channel that is down must not be able to stall a backup.
        log.warning("announcement queue is full - %s not mirrored to Discord", event)


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
    """One line, readable at a glance in a channel that is mostly quiet."""
    tail = item["event"].rsplit(".", 1)[-1]
    icon = ICONS.get(tail, "•")
    body = "%s **%s** %s" % (icon, item["event"], item["text"])
    if item["fields"]:
        body += "\n`%s`" % item["fields"]
    return body[:1900]
