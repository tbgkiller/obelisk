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

rows, problem = cf.search(FakeStore("a-key"), "stacking",
                          opener=lambda u: (_ for _ in ()).throw(OSError("403")))
check("a refused search reports the refusal rather than an empty shelf",
      rows == [] and "refused" in problem, problem)

# The key travels in a header, so it is not in the URL an exception would quote. Worth
# an assertion anyway: this is the one place where getting it wrong posts a credential
# into a log line.
check("the key is never put in a query string", "api_key=" not in cf.SEARCH
      and "apikey=" not in cf.SEARCH.lower(), cf.SEARCH)

src = open(cf.__file__, encoding="utf-8").read()
check("and never logged", not any(
    line.strip().startswith("log.") and "key" in line for line in src.splitlines()))

# ---- the user agent is load-bearing, not decoration
check("requests identify themselves - the keyless service 403s a default agent",
      "User-Agent" in src)

print("\nFAILURES: %s" % fails if fails else "\nall curseforge tests passed")
sys.exit(1 if fails else 0)
