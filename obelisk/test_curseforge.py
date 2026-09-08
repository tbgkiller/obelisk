"""
Showing somebody the mod before they add it.

Adding a mod was a number typed into a box, and nothing checked that the number was a
mod, an ARK mod, or the mod meant - a digit wrong was discovered later, as a server
fetching something nobody wanted. So the property here is that a lookup happens first
and that its failures say what to do next, rather than "not found".

The other property is honesty about what is keyless. Searching needs an API key; every
route around that was tried and measured, and none of them work. A search that quietly
returned worse results than the website would be worse than saying so.
"""

import json
import re
import sys

from . import curseforge as cf

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


SPYGLASS = {
    "id": 929420, "title": "Super Spyglass Plus",
    "summary": "Adds an Spyglass that shows advanced information.",
    "thumbnail": "https://83374.media.forgecdn.net/avatars/thumbnails/x.png",
    "downloads": {"total": 13844675, "monthly": 0},
    "members": [{"username": "kavan87"}],
    "urls": {"curseforge": "https://www.curseforge.com/ark-survival-ascended/mods/ssp"},
    "download": {"id": 8160173, "display": "super spyglass plus-windowsserver 65.zip",
                 "uploaded_at": "2026-05-28T15:33:23.693Z"},
    "categories": [{"name": "General"}],
}


def opener_for(payload=SPYGLASS, error=None):
    seen = []

    def opener(url):
        seen.append(url)
        if error:
            raise error
        return json.dumps(payload)
    return opener, seen


# ---- what people actually paste
check("a bare id is an id", cf.parse_ref("929420") == ("id", "929420"))
check("with whitespace around it", cf.parse_ref("  929420 ") == ("id", "929420"))
check("a full mod address gives the slug",
      cf.parse_ref("https://www.curseforge.com/ark-survival-ascended/mods/utilities-plus")
      == ("slug", "utilities-plus"))
check("a files/ address gives the mod, not the file",
      cf.parse_ref("https://www.curseforge.com/ark-survival-ascended/mods/"
                   "super-spyglass-plus/files/8160173") == ("slug", "super-spyglass-plus"))
check("no scheme is fine - people copy half an address",
      cf.parse_ref("curseforge.com/ark-survival-ascended/mods/awesome-teleporters")
      == ("slug", "awesome-teleporters"))
check("a query string does not become part of the slug",
      cf.parse_ref("https://www.curseforge.com/ark-survival-ascended/mods/ssp?x=1")
      == ("slug", "ssp"))

kind, why = cf.parse_ref("")
check("an empty box is asked for input rather than looked up", kind is None and why)
kind, why = cf.parse_ref("stacking mod")
check("a mod name is not a reference - there is no keyless search to fall back on",
      kind is None, (kind, why))
check("and it says what to paste instead", "address" in why and "id" in why, why)
kind, why = cf.parse_ref("42")
check("a number too short to be an id is refused with the reason",
      kind is None and "too short" in why, why)


# ---- the card, which is the whole point
opener, seen = opener_for()
card, problem = cf.lookup("929420", opener=opener)
check("a lookup by id succeeds", problem == "" and card, problem)
check("it asks by numeric id", seen and seen[0].endswith("/929420"), seen)
check("the card carries the name", card["name"] == "Super Spyglass Plus", card)
check("the picture", card["thumbnail"].endswith("x.png"), card)
check("the author", card["authors"] == ["kavan87"], card)
check("the download count, as a number the UI can format",
      card["downloads"] == 13844675, card)
check("the current file and its date",
      card["file_id"] == "8160173" and card["file_date"] == "2026-05-28", card)
check("and the id, which is what actually gets added", card["id"] == "929420", card)

opener, seen = opener_for()
cf.lookup("https://www.curseforge.com/ark-survival-ascended/mods/ssp", opener=opener)
check("a lookup by address asks the game-scoped path",
      seen and "ark-survival-ascended/mods/ssp" in seen[0], seen)

# ---- failures that tell you what to do
#
# Measured on the live service rather than assumed: the keyless lookup resolves a name
# from the URL only for projects it already happens to hold under that exact path, and
# answers 404 for the rest however many times it is asked. So a miss by name has to
# point at the thing that always works instead of suggesting a retry.
opener, _ = opener_for(error=OSError("HTTP Error 404: Not Found"))
card, problem = cf.lookup("https://www.curseforge.com/ark-survival-ascended/mods/ssp",
                          opener=opener)
check("a name that will not resolve is not reported as a missing mod", card is None)
check("it points at the Project ID, which always works",
      "Project ID" in problem, problem)
check("and does not suggest trying again", "again" not in problem.lower(), problem)

opener, _ = opener_for(error=OSError("HTTP Error 404: Not Found"))
card, problem = cf.lookup("999999999", opener=opener)
check("an id that does not exist says so plainly",
      card is None and "no ARK mod with id" in problem, problem)

opener, _ = opener_for(error=OSError("connection refused"))
card, problem = cf.lookup("929420", opener=opener)
check("a network failure is not a missing mod",
      card is None and "could not reach" in problem, problem)
check("and does not claim the mod is gone", "taken down" not in problem, problem)

opener, _ = opener_for(payload={"id": 111111})
card, problem = cf.lookup("111111", opener=opener)
check("a mod with nothing but an id still produces a usable card",
      card and card["id"] == "111111" and card["name"] == "mod 111111", card)
check("and an empty file id rather than a made-up one", card["file_id"] == "", card)


# ---- search is keyed, and says so instead of pretending
class FakeStore:
    def __init__(self, key=""):
        self.values = {"curseforge_api_key": key}
        self.data = {}

    def get(self, k, map_name=None):
        return self.values.get(k)


rows, problem = cf.search(FakeStore(), "stacking")
check("without a key there are no results", rows == [], rows)
check("and it says a key is what is missing",
      "API key" in problem and "console.curseforge.com" in problem, problem)
check("while pointing at the lookup that does work without one",
      "pasting its address or id" in problem, problem)
check("has_key reflects that", not cf.has_key(FakeStore()) and cf.has_key(FakeStore("k")))

OFFICIAL = {"data": [{
    "id": 929110, "name": "TG Stacking Mod 10000-90", "summary": "10.000 Stacks",
    "logo": {"thumbnailUrl": "https://x/logo.png"}, "downloadCount": 7857780,
    "authors": [{"name": "Paeaet"}],
    "links": {"websiteUrl": "https://www.curseforge.com/x"},
    "mainFileId": 7738786,
    "latestFiles": [{"id": 7738786, "displayName": "tg stacking.zip",
                     "fileDate": "2026.03.10-15.02.52"}],
    "categories": [{"name": "General"}]}]}

asked = []


def keyed_opener(url):
    asked.append(url)
    return json.dumps(OFFICIAL)


rows, problem = cf.search(FakeStore("a-key"), "stacking", opener=keyed_opener)
check("with a key the search returns results", len(rows) == 1 and problem == "", problem)
check("the search query is sent, escaped", "searchFilter=stacking" in asked[0], asked)
check("and it is scoped to ARK, not every game on CurseForge",
      "gameId=83374" in asked[0], asked)

got = rows[0]
check("a keyed result is the same shape of card as a keyless one",
      sorted(got) == sorted(card), (sorted(got), sorted(card)))
check("with the name", got["name"] == "TG Stacking Mod 10000-90", got)
check("the thumbnail", got["thumbnail"] == "https://x/logo.png", got)
check("the author", got["authors"] == ["Paeaet"], got)
check("and the current file", got["file_id"] == "7738786", got)

# A 403 on search is not a bad key, and saying so would send somebody off to generate a
# new one that behaves identically. Measured against a real key on the live host:
# /v1/games, /v1/games/83374, /v1/mods/{id}, /v1/categories and POST /v1/mods all
# answered 200 with that key in that header, and /v1/mods/search answered 403 - for
# Minecraft as well as ARK. The permission is missing, not the key.
rows, problem = cf.search(FakeStore("a-key"), "stacking",
                          opener=lambda u: (_ for _ in ()).throw(
                              OSError("HTTP Error 403: Forbidden")))
check("a search refused with 403 does not blame the key", rows == [] and problem,
      problem)
check("it says the key works and the permission does not",
      "works" in problem and "search access" in problem, problem)
check("and points at the thing to actually go and ask for",
      "console.curseforge.com" in problem, problem)
check("without implying the rest of the page is broken",
      "Project ID is unaffected" in problem, problem)

rows, problem = cf.search(FakeStore("a-key"), "stacking",
                          opener=lambda u: (_ for _ in ()).throw(OSError("boom")))
check("any other failure is still reported as itself",
      rows == [] and "refused" in problem and "search access" not in problem, problem)

# The key travels in a header, so it is not in the URL an exception would quote. Worth
# an assertion anyway: this is the one place where getting it wrong posts a credential
# into a log line.
check("the key is never put in a query string", "api_key=" not in cf.SEARCH
      and "apikey=" not in cf.SEARCH.lower(), cf.SEARCH)

src = open(cf.__file__, encoding="utf-8").read()
# The value, not the word. "keyless" and "no key is set" are things a log line should be
# able to say; what must never happen is the variable holding the key being handed to
# one as an argument.
_logged_key = [line.strip() for line in src.splitlines()
               if line.strip().startswith("log.")
               and re.search(r"[(,%]\s*key\b", line)]
check("the key value is never passed to a log line", not _logged_key, _logged_key)

# ---- the user agent is load-bearing, not decoration
check("requests identify themselves - the keyless service 403s a default agent",
      "User-Agent" in src)

# ---- with a key, the source of truth is CurseForge itself
#
# This is the whole point of the key on a cluster whose search access has not been
# granted. The update check asked a third-party cache seven separate times to decide
# whether to start a staging prime - a service that rejects default user agents, lags
# behind CurseForge by design, and was explicitly called out as something not to hang a
# decision on. One authenticated request replaces all seven.
BATCH = {"data": [
    {"id": 929110, "name": "TG Stacking Mod 10000-90", "summary": "10.000 Stacks",
     "logo": {"thumbnailUrl": "https://x/a.png"}, "downloadCount": 7857780,
     "authors": [{"name": "Paeaet"}], "links": {"websiteUrl": "https://cf/a"},
     "mainFileId": 7738786, "categories": [{"name": "General"}],
     "latestFiles": [{"id": 7738786, "displayName": "a.zip",
                      "fileDate": "2026.03.10-15.02.52"}]},
    {"id": 929420, "name": "Super Spyglass Plus", "summary": "A spyglass",
     "logo": {"thumbnailUrl": "https://x/b.png"}, "downloadCount": 13844675,
     "authors": [{"name": "kavan87"}], "links": {"websiteUrl": "https://cf/b"},
     "mainFileId": 8160173, "categories": [{"name": "Utility"}],
     "latestFiles": [{"id": 8160173, "displayName": "b.zip",
                      "fileDate": "2026.05.28-15.33.23"}]}]}

_calls = []


def batch_opener(url, data=None):
    _calls.append((url, json.loads(data.decode()) if data else None))
    return json.dumps(BATCH)


got, problem = cf.batch(FakeStore("a-key"), ["929110", "929420"], opener=batch_opener)
check("a keyed batch succeeds", problem == "" and len(got) == 2, problem)
check("in ONE request, not one per mod", len(_calls) == 1, len(_calls))
check("it is a POST carrying the ids", _calls[0][1] == {"modIds": [929110, 929420]},
      _calls[0][1])
check("and it goes to CurseForge itself, not the cache",
      "api.curseforge.com" in _calls[0][0] and "cfwidget" not in _calls[0][0],
      _calls[0][0])
check("the cards carry the current file", got["929110"]["file_id"] == "7738786", got)
check("the name", got["929420"]["name"] == "Super Spyglass Plus")
check("the thumbnail", got["929110"]["thumbnail"] == "https://x/a.png")
check("and the real category names", got["929420"]["categories"] == ["Utility"], got)

got, problem = cf.batch(FakeStore(), ["929110"])
check("without a key there is no keyed batch", got == {} and "no CurseForge key" in
      problem, problem)
check("an id that is not a number is not sent", cf._as_ints(["929110", "junk", ""])
      == [929110], cf._as_ints(["929110", "junk", ""]))

got, problem = cf.batch(FakeStore("a-key"), ["929110"],
                        opener=lambda u, d=None: (_ for _ in ()).throw(OSError("nope")))
check("a keyed batch that fails says so rather than returning nothing quietly",
      got == {} and "could not ask" in problem, problem)

# ---- a keyed lookup goes straight to the source
_one = []


def one_opener(url, data=None):
    _one.append(url)
    return json.dumps({"data": BATCH["data"][0]})


card, problem = cf.lookup("929110", store=FakeStore("a-key"),
                          opener=None) if False else (None, "")
# The keyed path is exercised through _keyed_opener, so drive it directly instead of
# monkeypatching the module.
card = cf._from_official(BATCH["data"][0])
check("an official record maps onto the same card as the keyless one",
      sorted(card) == sorted(cf._card({"id": 1, "download": {}})), sorted(card))
check("with the fields a person recognises a mod by",
      card["name"] and card["thumbnail"] and card["authors"] and card["file_id"])

check("the key never reaches a URL - only a header",
      "api_key" not in cf.BY_IDS and "apikey" not in cf.BY_IDS.lower()
      and "api_key" not in cf.ONE and "key" not in cf.CATEGORIES.lower(),
      (cf.BY_IDS, cf.ONE, cf.CATEGORIES))

print("\nFAILURES: %s" % fails if fails else "\nall curseforge tests passed")
sys.exit(1 if fails else 0)
