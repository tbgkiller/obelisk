"""
Saying what happened, without saying anything that should not be said.

Two properties matter here and both are about failure. The log line has to be written
even when Discord is absent, misconfigured or full - it is the record, and a backup must
never stall because a channel is unreachable. And nothing secret may reach either
destination, including by accident, because the most likely way a password gets
announced is inside an exception message nobody wrote by hand.
"""

import logging, queue, sys

from . import announce

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


class Capture(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
announce.log.addHandler(cap)
announce.log.setLevel(logging.INFO)


def drain():
    """Everything, not a page of it - pop_all() is deliberately batched for the relay."""
    out, batch = [], announce.pop_all(limit=100)
    while batch:
        out += batch
        batch = announce.pop_all(limit=100)
    return out


# ---- the log line is the record
drain()
cap.lines[:] = []
announce.say("backup.start", "Backing up the cluster", archive="obelisk-backup-1.tar.gz")
check("a log line is written", len(cap.lines) == 1, cap.lines)
line = cap.lines[0]
check("it is machine-filterable", line.startswith("EVENT backup.start "), line)
check("it carries the sentence a person reads", "Backing up the cluster" in line, line)
check("and the structured fields", "archive=obelisk-backup-1.tar.gz" in line, line)

items = drain()
check("and it is queued for Discord", len(items) == 1, items)
check("with its event name", items[0]["event"] == "backup.start")
check("Discord gets a readable line",
      "backup.start" in announce.format_for_discord(items[0]), items[0])
check("with an icon that matches the outcome",
      announce.format_for_discord({"event": "x.done", "text": "t", "fields": "",
                                   "level": "info"}).startswith("✅"))
check("a failure looks like a failure",
      announce.format_for_discord({"event": "x.failed", "text": "t", "fields": "",
                                   "level": "info"}).startswith("❌"))

# ---- empty fields are left out rather than printed as nothing
cap.lines[:] = []
announce.say("cluster.stop", "Stopped", map="", reason=None, who="ui")
check("blank fields are omitted", "map=" not in cap.lines[0] and "reason=" not in cap.lines[0],
      cap.lines[0])
check("real ones are kept", "who=ui" in cap.lines[0], cap.lines[0])
drain()

# ---- secrets never appear, in either destination
n = announce.guard_secrets(["hunter2-the-admin-password", "discord-bot-token-value",
                            "", None, "abc"])
check("short values are not registered as secrets - they would redact real words",
      n == 2, n)

cap.lines[:] = []
announce.say("restore.failed", "RCON refused: password hunter2-the-admin-password bad",
             detail="token discord-bot-token-value here")
line = cap.lines[0]
check("a secret inside the sentence is redacted",
      "hunter2-the-admin-password" not in line, line)
check("and one inside a field is too", "discord-bot-token-value" not in line, line)
check("what is left says something was hidden", "[redacted]" in line, line)
item = drain()[0]
check("the Discord copy is redacted as well",
      "hunter2" not in announce.format_for_discord(item), item)

check("scrub is available on its own for callers that build text first",
      announce.scrub("x hunter2-the-admin-password y") == "x [redacted] y")
check("longest secrets go first, so no fragment survives",
      "hunter2" not in announce.scrub("hunter2-the-admin-password"))
announce.guard_secrets([])

# ---- Discord being absent or full must never break the caller
cap.lines[:] = []
for i in range(announce.MAX_PENDING + 25):
    announce.say("spam.event", "message %d" % i)
check("a full queue does not raise", True)
check("every one of them still reached the log",
      sum(1 for l in cap.lines if l.startswith("EVENT spam.event")) ==
      announce.MAX_PENDING + 25,
      sum(1 for l in cap.lines if l.startswith("EVENT spam.event")))
check("and the overflow is reported rather than silent",
      any("queue is full" in l for l in cap.lines))
check("the queue holds at most its limit", announce.pending() <= announce.MAX_PENDING,
      announce.pending())
drain()
check("draining empties it", announce.pending() == 0, announce.pending())
check("but one call is a batch, not the whole queue - the relay posts in pages",
      True)

# ---- announcements survive the bot being down and arrive when it returns
announce.say("update.start", "Cluster update starting")
announce.say("update.done", "Cluster update finished")
check("two announcements are waiting while Discord is offline", announce.pending() == 2)
later = drain()
check("both are delivered when it comes back", len(later) == 2,
      [i["event"] for i in later])
check("oldest first, so the story reads in order",
      [i["event"] for i in later] == ["update.start", "update.done"],
      [i["event"] for i in later])


# ---- the UI feed is a second reader, not a second queue
#
# The complaint that produced this was that Discord knew more than the UI, which is
# backwards. The fix has to be one source with two readers, or the two drift - and the
# specific way it would drift is if the UI read from the queue the relay drains, and
# started stealing announcements out of the channel.
drain()
cap.lines[:] = []
# Everything above has been filling the ring, which is the point of it - so the mark is
# taken here and only what comes after is looked at.
mark = announce.newest_id()
announce.say("backup.start", "Backing up", archive="a.tar.gz")
announce.say("ark.update_unsafe", "Not safe", level="error",
             detail="mod 929420 never loaded\nmod 929902 never loaded", build="25200000")

feed = announce.recent(since=mark)
check("the feed has both events", len(feed) == 2, [i["event"] for i in feed])
check("newest first, which is how a person reads a log",
      feed[0]["event"] == "ark.update_unsafe", [i["event"] for i in feed])
check("with the level, so an error can look like one", feed[0]["level"] == "error",
      feed[0]["level"])
check("and the fields", "build=25200000" in feed[0]["fields"], feed[0]["fields"])

posted = drain()
check("Discord got both as well", len(posted) == 2, [i["event"] for i in posted])
check("and draining Discord does not empty the feed - they are separate readers",
      len(announce.recent(since=mark)) == 2,
      [i["event"] for i in announce.recent(since=mark)])

# ---- detail: the UI gets more than the channel, on purpose
check("the feed carries the long version",
      "929902" in feed[0]["detail"], feed[0]["detail"])
line = announce.format_for_discord(posted[1])
check("Discord gets the sentence, not the whole thing", "929902" not in line, line)
check("but is told where the rest is", "Activity page" in line, line)
check("the detail is in the log too, since the log is the record",
      any("929902" in l for l in cap.lines), cap.lines)

# ---- ids, so a polling UI can ask for only what is new
newest = announce.newest_id()
announce.say("cluster.launch", "Cluster starting")
fresh = announce.recent(since=newest)
check("asking for what is newer than an id returns only that",
      [i["event"] for i in fresh] == ["cluster.launch"], [i["event"] for i in fresh])
check("and the id climbs", announce.newest_id() > newest)
check("a nonsense since is treated as 'everything' rather than crashing the page",
      len(announce.recent(since="banana")) >= 3)

# ---- secrets are scrubbed out of detail as well as text
announce.guard_secrets(["hunter2-the-admin-password"])
announce.say("restore.failed", "it went wrong", level="error",
             detail="RCON said: password hunter2-the-admin-password rejected")
check("a secret inside the long detail is redacted too",
      "hunter2" not in announce.recent()[0]["detail"], announce.recent()[0]["detail"])
announce.guard_secrets([])

# ---- surviving a restart, which is the difference between a feed and a live tail
import json, os, tempfile

_dir = tempfile.mkdtemp()
_path = os.path.join(_dir, "events.json")
before = announce.recent(limit=500)
ok_save, why = announce.save_to(_path)
check("the feed can be written out", ok_save, why)

announce.load_from(_path)
after = announce.recent(limit=500)
check("and read back whole", len(after) == len(before), (len(after), len(before)))
check("with the detail intact",
      any(i.get("detail") for i in after), [i["event"] for i in after])
check("ids carry on climbing after a restore, so a polling page is not re-sent old rows",
      (announce.say("x.after", "after") or announce.newest_id()) >
      max(i["id"] for i in before), announce.newest_id())

check("a missing file is not an error - a first run has no history",
      announce.load_from(os.path.join(_dir, "nope.json")) == 0)
with open(_path, "w", encoding="utf-8") as fh:
    fh.write("{ this is not json")
check("and neither is a corrupt one", announce.load_from(_path) == 0)

announce.log.removeHandler(cap)
print("\nFAILURES: %s" % fails if fails else "\nall announce tests passed")
sys.exit(1 if fails else 0)
