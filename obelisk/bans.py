"""
What Obelisk has banned, and what it is allowed to ban.

Two jobs, and the first one is the one that earns a module of its own.

**The netid is written to a file.** `BanPlayer` does not merely run a command that the
server can decline - it appends the id to that server's `BanList.txt`, on every map, and
the server reads that file back on every connection attempt for ever. A kick with a
malformed id is a command nobody acts on; a ban with a malformed id is a line in ten
files that somebody has to find and remove by hand. So the id is checked against what an
id can be *before* it goes anywhere near the command, rather than trusted because the
page that posted it was one we rendered.

**And a ban is worth a record.** ARK has no RCON command that lists bans - the only
source of truth is each server's own `BanList.txt`, in a location this manager cannot
see from inside its container. So Obelisk keeps its own ledger of what it issued: who,
which id, when, and which maps actually took it. That is honestly less than "every ban
on this cluster", and it is exactly the thing an operator needs to undo their own
mistake, which is the case that actually comes up.
"""

import time

# Long enough for any platform id in use - 17 digits for Steam, 19 for Epic, 32
# characters for EOS - and short enough that a paste accident is refused rather than
# written to ten files.
MAX_NETID = 64
KEEP = 200


def valid_netid(netid):
    """Is this something that can be written to a ban list and read back?

    A whitelist, and it has to be one. The first version of this listed the characters
    it did not want - whitespace, quotes, a backslash - which reads like a guard and is
    not one: it let through `;`, `|`, `&`, `$`, `<`, `>` and a comma, on the single path
    in this program that writes a line into a file on ten servers. Listing what is
    forbidden means being right about every character nobody has thought of yet.

    Listing what is allowed costs nothing here, because every id this could ever hold is
    alphanumeric: 17 digits for Steam, 19 for Epic, 32 hex characters for EOS. `-` and
    `_` are permitted so a platform that adds a separator does not silently lose the
    ability to be banned.

    The failure being guarded against is not an attacker - the value comes from a page
    Obelisk rendered from its own poll - it is a line of ListPlayers output that did not
    parse the way the parser assumed, arriving in a file the game re-reads for ever.
    """
    netid = str(netid or "")
    if not netid or len(netid) > MAX_NETID:
        return False
    return all(c.isalnum() or c in "-_" for c in netid)


def record(store, name, netid, results, kick=None, when=None):
    """Remember a ban Obelisk issued. Returns the entry.

    `results` is {map label: "" for sent, or the reason it did not send}, which is what
    makes this worth keeping rather than a line in a log: a ban that reached eight maps
    of ten is a different fact from a ban that reached all of them, and the two maps it
    missed are the ones somebody has to deal with.
    """
    entry = {
        "name": str(name or ""),
        "netid": str(netid or ""),
        "when": int(when if when is not None else time.time()),
        "maps": {str(k): str(v or "") for k, v in (results or {}).items()},
        "kick": str(kick or ""),
    }
    held = store.data.setdefault("bans", [])
    held.append(entry)
    del held[:-KEEP]
    store.save()
    return entry


def count(store):
    """How many records are held - which is not how many a page shows."""
    return len(store.data.get("bans") or [])


def recent(store, limit=50):
    """Newest first, for the page that will show them."""
    held = list(store.data.get("bans") or [])
    held.reverse()
    return held[:limit]


def sent_to(entry):
    """The maps that took this ban."""
    return sorted(k for k, v in (entry.get("maps") or {}).items() if not v)


def missed(entry):
    """The maps that did not, and why."""
    return sorted((k, v) for k, v in (entry.get("maps") or {}).items() if v)


def is_unbanned(entry):
    """Has this one been undone from here?"""
    return bool((entry or {}).get("unbanned"))


def entries_for(store, netid):
    """Every record of this id being banned, oldest first.

    Plural on purpose. A ban that reached eight maps of ten and was sent again is two
    honest records of the same id, and the second does not replace the first - what
    happened on those eight maps the first time is still what happened.
    """
    netid = str(netid or "")
    return [e for e in (store.data.get("bans") or []) if e.get("netid") == netid]


def mark_unbanned(store, netid, when=None):
    """Mark every record of this id as undone. Returns the ones it marked.

    Marked, not removed. Deleting the row would make the list agree with the present
    and lie about the past: "was this person ever banned, and did it reach every map?"
    is the question somebody asks precisely because they were let back in. An entry
    already marked is left alone, so a second unban does not rewrite when the first
    one happened.
    """
    at = int(when if when is not None else time.time())
    marked = [e for e in entries_for(store, netid) if not is_unbanned(e)]
    for entry in marked:
        entry["unbanned"] = at
    if marked:
        store.save()
    return marked
