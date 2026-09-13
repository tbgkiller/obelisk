"""Who Obelisk has let past the player cap - as a log of what it sent.

`AllowPlayerToJoinNoCheck` exempts one id from `MaxPlayers`: that player can join a
server that is already full. It is not the join allow-list, and this module never calls
it a whitelist, because to an ARK admin that word means the file that decides who may
connect at all.

**This is a log, not a state.** ARK has no RCON command that reads back a server's
`PlayersJoinNoCheckList`, exactly as it has none for `BanList.txt`. So a switch drawn
as on or off would be asserting something nothing here can check - somebody may have
been added in-game, or removed, or the server may have been rebuilt since. What can be
said honestly is what this manager sent and when, so that is what is kept: an allow
event per id, marked revoked when a revoke is sent, never deleted.

The same shape as `bans`, deliberately. The two lists answer the same kind of question
about the same kind of key, and an operator who has read one should not have to learn
the other.
"""

import time

from .bans import valid_netid                       # noqa: F401 - one whitelist, shared

KEEP = 200


def record(store, netid, results, when=None):
    """Remember an allow Obelisk sent. Returns the entry.

    `results` is {map label: "" for sent, or the reason it did not send}, so an allow
    that reached eight maps of ten stays legible as what it was - the player can join
    those eight when they are full and not the other two.
    """
    entry = {
        "netid": str(netid or ""),
        "when": int(when if when is not None else time.time()),
        "maps": {str(k): str(v or "") for k, v in (results or {}).items()},
    }
    held = store.data.setdefault("cap_allows", [])
    held.append(entry)
    del held[:-KEEP]
    store.save()
    return entry


def count(store):
    """How many events are held - which is not how many a page shows."""
    return len(store.data.get("cap_allows") or [])


def recent(store, limit=50):
    """Newest first, for the section that shows them."""
    held = list(store.data.get("cap_allows") or [])
    held.reverse()
    return held[:limit]


def sent_to(entry):
    """The maps that took it."""
    return sorted(k for k, v in (entry.get("maps") or {}).items() if not v)


def missed(entry):
    """The maps that did not, and why."""
    return sorted((k, v) for k, v in (entry.get("maps") or {}).items() if v)


def is_revoked(entry):
    """Has a revoke been sent for this one?"""
    return bool((entry or {}).get("revoked"))


def entries_for(store, netid):
    """Every allow event for this id, oldest first."""
    netid = str(netid or "")
    return [e for e in (store.data.get("cap_allows") or [])
            if e.get("netid") == netid]


def mark_revoked(store, netid, when=None):
    """Mark every live allow for this id as revoked. Returns the ones it marked.

    Marked rather than removed, for the reason the whole section exists: this is a log
    of what was sent, and "was this id ever let past the cap, and on which maps?" is a
    question somebody asks precisely after taking it away again.
    """
    at = int(when if when is not None else time.time())
    marked = [e for e in entries_for(store, netid) if not is_revoked(e)]
    for entry in marked:
        entry["revoked"] = at
    if marked:
        store.save()
    return marked
