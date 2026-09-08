"""
Looking a mod up before it goes anywhere near the cluster.

Adding a mod used to mean typing a number. Nothing checked that the number was a mod,
that it was an ARK mod, or that it was the mod you meant - a digit wrong and the first
sign was a server fetching something else, or nothing. So: paste the page's address or
the id, see the actual mod, then add it.

**Browsing needs a key and adding does not.** Every keyless route to a search was
tried and every one refuses: `api.curseforge.com/v1/mods/search` and the game-scoped
`83374.api.curseforge.com` both answer 403 without an `x-api-key`, curseforge.com's own
endpoint sits behind Cloudflare, and cfwidget has no search at all. What cfwidget will
answer is one project at a time, by numeric id or by the slug out of the page URL, and
that is enough to look at a mod somebody has already found. Search is therefore gated
on a key the operator supplies, and its absence costs discovery rather than the feature.

cfwidget is a courtesy service, not a contract: it refuses a default user agent, and it
is a cache in front of CurseForge rather than CurseForge. Fine for showing somebody a
mod they are about to add. Not something that should ever decide whether to restart a
cluster - that stays with the staging server, which asks CurseForge itself.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("obelisk.curseforge")

GAME = "ark-survival-ascended"
BY_ID = "https://api.cfwidget.com/%s"
BY_SLUG = "https://api.cfwidget.com/" + GAME + "/mods/%s"

# The official one, for when there is a key. Kept here so the two paths are visibly the
# same shape and the keyed one is not a rewrite bolted on later.
SEARCH = ("https://api.curseforge.com/v1/mods/search?gameId=83374&pageSize=%d"
          "&sortField=2&sortOrder=desc&searchFilter=%s")

TIMEOUT = 15

# curseforge.com/ark-survival-ascended/mods/<slug>[/files/<id>], with or without scheme,
# and with whatever query string the browser's address bar picked up on the way.
_URL = re.compile(r"curseforge\.com/[^/\s]+/mods/([A-Za-z0-9][A-Za-z0-9._-]*)", re.I)
_ID = re.compile(r"^\s*(\d{3,10})\s*$")


def parse_ref(text):
    """(kind, value) for whatever the operator pasted, or (None, reason).

    Both halves of what people actually have to hand: the number from a wiki post, and
    the address bar. Anything else is refused with a sentence rather than looked up on
    the off chance - a lookup for nonsense returns a 404 that reads like the mod is
    gone.
    """
    raw = str(text or "").strip()
    if not raw:
        return None, "paste a CurseForge address or a mod id"
    m = _ID.match(raw)
    if m:
        return "id", m.group(1)
    m = _URL.search(raw)
    if m:
        return "slug", m.group(1)
    if raw.isdigit():
        return None, ("%s is too short to be a mod id - copy the number from the mod's "
                      "CurseForge page, or paste the whole address" % raw)
    return None, ("that does not look like a CurseForge mod. Paste the address of the "
                  "mod's page, or just its id.")


def _fetch(url):
    # cfwidget answers 403 to urllib's default user agent, so this is not decoration.
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "obelisk"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def lookup(text, opener=None, store=None):
    """(card, problem) for one mod, from an id or a page address.

    `card` carries what a person needs to recognise a mod: the name, the picture, who
    wrote it, what it says it does, how many people use it, and which file is current.

    With a key, an id goes straight to CurseForge - authoritative, and no dependence on
    a cache that may be days behind. Without one, or for a name out of a URL, the
    keyless service answers. Same card either way, so nothing downstream can tell which
    door it came through.
    """
    kind, value = parse_ref(text)
    if not kind:
        return None, value
    if kind == "id" and store is not None and opener is None:
        keyed = _keyed_opener(store)
        if keyed:
            try:
                data = json.loads(keyed(ONE % value))
            except Exception as e:                  # noqa: BLE001 - fall back, say why
                log.info("CurseForge did not answer for %s (%s) - trying the keyless "
                         "lookup", value, e)
            else:
                mod = data.get("data") or {}
                if mod.get("id"):
                    return _from_official(mod), ""
                return None, ("CurseForge has no ARK mod with id %s. Check the number, "
                              "or whether the mod has been taken down." % value)
    opener = opener or _fetch
    url = (BY_ID % value) if kind == "id" else (BY_SLUG % value)
    try:
        data = json.loads(opener(url))
    except Exception as e:                          # noqa: BLE001 - shown, never raised
        if "404" not in str(e):
            return None, "could not reach CurseForge: %s" % e
        if kind == "slug":
            # Measured, not assumed: the keyless service resolves a name-in-the-URL
            # only for projects it happens to have cached under that exact path, and
            # returns 404 for the rest however many times it is asked. So the address
            # is worth trying and is not worth promising, and when it misses the answer
            # has to be the thing that always works rather than "try again".
            return None, ("CurseForge would not resolve \"%s\" by name. Open the mod's "
                          "page and copy its <b>Project ID</b> - it is in the panel on "
                          "the right - then paste that number here instead." % value)
        return None, ("CurseForge has no ARK mod with id %s. Check the number, or "
                      "whether the mod has been taken down." % value)
    return _card(data), ""


def _card(data):
    download = data.get("download") or {}
    project = str(data.get("id") or "")
    return {
        "id": project,
        "name": str(data.get("title") or ("mod %s" % project)),
        "summary": str(data.get("summary") or ""),
        "thumbnail": str(data.get("thumbnail") or ""),
        "downloads": int((data.get("downloads") or {}).get("total") or 0),
        "authors": [str(m.get("username") or m.get("name") or "")
                    for m in (data.get("members") or []) if m],
        "url": str((data.get("urls") or {}).get("curseforge") or ""),
        "file_id": str(download.get("id") or ""),
        "file_name": str(download.get("display") or download.get("name") or ""),
        "file_date": str(download.get("uploaded_at") or "")[:10],
        "categories": [str(c.get("name") or "") for c in (data.get("categories") or [])
                       if isinstance(c, dict)],
    }


def has_key(store):
    return bool(str(store.get("curseforge_api_key") or "").strip())


# The endpoints a console key actually has. Search is not among them - CurseForge grants
# that separately - but everything below answers 200, which is enough to stop the part
# that matters depending on somebody else's cache.
BY_IDS = "https://api.curseforge.com/v1/mods"
ONE = "https://api.curseforge.com/v1/mods/%s"
CATEGORIES = "https://api.curseforge.com/v1/categories?gameId=83374"


def _keyed_opener(store):
    """A fetcher that speaks to CurseForge as this operator, or None without a key.

    The key travels in a header. Never a query string: an exception carries the URL it
    was fetching, and a URL that carries a credential is a credential in a log line.
    """
    key = str(store.get("curseforge_api_key") or "").strip()
    if not key:
        return None

    def fetch(url, data=None):
        req = urllib.request.Request(
            url, data=data, method="POST" if data is not None else "GET",
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "x-api-key": key, "User-Agent": "obelisk"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    return fetch


def batch(store, ids, opener=None):
    """({project id: card}, problem) for many mods in ONE authenticated request.

    This is the point of the key. The update checker asked a third-party cache seven
    separate times to decide whether to stage an update - a service that rejects default
    user agents, lags behind CurseForge by design, and was explicitly not something to
    make load-bearing. With a key it is one call to CurseForge itself, and the answer
    that gates a staging prime comes from the place that knows.
    """
    wanted = _as_ints(ids)
    if not wanted:
        return {}, ""
    fetch = opener or _keyed_opener(store)
    if not fetch:
        return {}, "no CurseForge key is set"
    body = json.dumps({"modIds": wanted}).encode("utf-8")
    try:
        data = json.loads(fetch(BY_IDS, body))
    except Exception as e:                          # noqa: BLE001 - reported, not raised
        return {}, "could not ask CurseForge: %s" % str(e)[:200]
    out = {}
    for mod in data.get("data") or []:
        card = _from_official(mod)
        if card["id"]:
            out[card["id"]] = card
    return out, ""


def _as_ints(ids):
    if isinstance(ids, str):
        ids = ids.split(",")
    out = []
    for one in ids or []:
        text = str(one).strip()
        if text.isdigit():
            out.append(int(text))
    return out


def categories(store, opener=None):
    """{category id: name} for this game. Read once and kept - they do not move."""
    if _CATEGORIES:
        return _CATEGORIES
    fetch = opener or _keyed_opener(store)
    if not fetch:
        return {}
    try:
        data = json.loads(fetch(CATEGORIES))
    except Exception as e:                          # noqa: BLE001 - a nicety, not a need
        log.info("could not read CurseForge categories: %s", e)
        return {}
    for row in data.get("data") or []:
        if row.get("id") and row.get("name"):
            _CATEGORIES[str(row["id"])] = str(row["name"])
    return _CATEGORIES


_CATEGORIES = {}


def search(store, query, limit=24, opener=None):
    """(cards, problem). Needs a key; says so plainly when there is not one.

    Not a degraded search and not a scrape of the website - either the operator has a
    key and this is the real thing, or it says what is missing and points at the lookup
    that works without one. A search that silently returns worse results than the site
    is worse than no search.
    """
    key = str(store.get("curseforge_api_key") or "").strip()
    if not key:
        return [], ("Searching CurseForge needs an API key, which is free from "
                    "console.curseforge.com. Without one you can still add any mod by "
                    "pasting its address or id above.")
    text = str(query or "").strip()
    if not text:
        return [], ""
    url = SEARCH % (int(limit), urllib.parse.quote(text))

    def keyed(u):
        req = urllib.request.Request(u, headers={"Accept": "application/json",
                                                 "x-api-key": key,
                                                 "User-Agent": "obelisk"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")

    try:
        data = json.loads((opener or keyed)(url))
    except Exception as e:                          # noqa: BLE001
        # The key is never echoed back, here or anywhere - the exception text can carry
        # the request URL, and the URL is not where the key is, but this is the one
        # place it would be easy to get wrong.
        #
        # 403 here does not mean the key is bad, and saying so would send somebody off
        # to generate a new one that behaves identically. CurseForge gates
        # /v1/mods/search separately from the rest of the API: a console key reads
        # /v1/games, /v1/mods/{id}, /v1/categories and POST /v1/mods perfectly well and
        # is still refused for search - measured against a real key, and refused for
        # every game, not just this one. What is missing is a permission on the key,
        # and that is a different thing to go and ask for.
        if "403" in str(e) or "Forbidden" in str(e):
            return [], ("This CurseForge key works, but it does not have search access "
                        "- CurseForge grants that separately from the rest of the API. "
                        "Ask for search on the key at console.curseforge.com; nothing "
                        "else needs to change. Adding a mod by Project ID is unaffected.")
        return [], "CurseForge refused the search: %s" % str(e)[:200]
    return [_from_official(m) for m in (data.get("data") or [])], ""


def _from_official(mod):
    """The official API's shape, mapped onto the same card as the keyless one."""
    files = mod.get("latestFiles") or []
    newest = files[0] if files else {}
    return {
        "id": str(mod.get("id") or ""),
        "name": str(mod.get("name") or ""),
        "summary": str(mod.get("summary") or ""),
        "thumbnail": str((mod.get("logo") or {}).get("thumbnailUrl") or ""),
        "downloads": int(mod.get("downloadCount") or 0),
        "authors": [str(a.get("name") or "") for a in (mod.get("authors") or [])],
        "url": str((mod.get("links") or {}).get("websiteUrl") or ""),
        "file_id": str(mod.get("mainFileId") or newest.get("id") or ""),
        "file_name": str(newest.get("displayName") or ""),
        "file_date": str(newest.get("fileDate") or "")[:10],
        "categories": [str(c.get("name") or "") for c in (mod.get("categories") or [])],
    }
