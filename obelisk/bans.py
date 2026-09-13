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

    Deliberately a whitelist. The failure being guarded against is not an attacker -
    the value comes from a page Obelisk rendered from its own poll - it is a line of
    ListPlayers output that did not parse the way the parser assumed, arriving in a
    file the game re-reads for ever.
    """
    netid = str(netid or "")
    if not netid or len(netid) > MAX_NETID:
        return False
    if any(c.isspace() for c in netid):
        return False
    return not any(c in netid for c in '"\'\\`')


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
