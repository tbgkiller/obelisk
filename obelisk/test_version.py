"""
Telling the operator the truth about updates, when the usual checker does not.

The failure being answered: Unraid compared a stale digest against itself and reported
"up to date" while a fix sat published and unapplied. Not an error - a confident wrong
answer, which is the one kind a user cannot act on, because there is nothing on screen
suggesting they should look.

So the properties that matter here are about honesty rather than features. Say "an
update is available" only when the digests genuinely differ. When the registry cannot
be reached, say *that* - never "up to date", which is the lie being replaced.
"""

import sys

from . import version

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


OLD = "sha256:bebec1b9e6e150ed04a5e27f7f03b686fedb0ff8646894a2f3fade42cc9dc9a9"
NEW = "sha256:a58503a3453657f864e928c48cc41f8a032ebe299b2ab6096236549d6a64a265"


def inspector(commit="fc81c2c", image="sha256:img", digest=OLD, repo="ghcr.io/x/obelisk"):
    def inspect(args):
        if args[0] == image:
            return 0, ("%s@%s" % (repo, digest)) if digest else ""
        return 0, "%s|%s" % (commit, image)
    return inspect


def opener_for(digest, fail_token=False, fail_manifest=False):
    def opener(url, token):
        if token is None:
            if fail_token:
                raise OSError("401 Unauthorized")
            return '{"token": "synthetic"}'
        if fail_manifest:
            raise OSError("500 from the registry")
        return digest
    return opener


# ---- the default inspector must actually be a docker command
#
# It was not. running() built ["inspect", ...] and handed it to dockerctl._run(), which
# executes exactly the argv it is given - so the OS was asked for a binary called
# `inspect` and the panel reported "not started from a published image" on a container
# that plainly was. Every test here injected its own inspector, so the default path was
# never exercised until a live host ran it. Now the default is the thing under test.
_argv = []


def _spy(args, timeout=None):
    _argv.append(list(args))
    return 127, "pretend it failed - the argv is what matters"


import obelisk.dockerctl as _dockerctl
_real_run = _dockerctl._run
_dockerctl._run = _spy
version.running(environ={"HOST_CONTAINERNAME": "Obelisk"})
_dockerctl._run = _real_run

check("the default inspector shells out to docker", _argv and _argv[0][0] == "docker",
      _argv[:1])
check("and asks it to inspect", _argv and _argv[0][1] == "inspect", _argv[:1])
check("naming the container it is running in", "Obelisk" in _argv[0], _argv[:1])


# ---- what is running
run = version.running(inspect=inspector(), environ={"HOST_CONTAINERNAME": "Obelisk"})
check("the running commit is read from the image label", run["commit"] == "fc81c2c", run)
check("and the digest it was pulled as", run["digest"] == OLD, run)
check("and the repository it came from", run["repo"] == "ghcr.io/x/obelisk", run)

# ---- an update that really is available
st = version.status(inspect=inspector(), opener=opener_for(NEW),
                    environ={"HOST_CONTAINERNAME": "Obelisk"})
check("a genuinely newer digest is reported as an update",
      st["update_available"] is True, st)
check("both digests are shown, so the claim can be checked",
      st["digest"] == OLD and st["published"] == NEW, st)
check("no problem is reported when it worked", st["problem"] == "", st)

# ---- and one that is not
st = version.status(inspect=inspector(digest=NEW), opener=opener_for(NEW),
                    environ={"HOST_CONTAINERNAME": "Obelisk"})
check("the same digest is not an update", st["update_available"] is False, st)

# ---- the failure that started this: never claim up-to-date when we do not know
st = version.status(inspect=inspector(), opener=opener_for(NEW, fail_token=True),
                    environ={"HOST_CONTAINERNAME": "Obelisk"})
check("a registry we cannot authenticate to is not 'up to date'",
      st["update_available"] is None, st)
check("it says what went wrong instead", "token" in st["problem"], st)

st = version.status(inspect=inspector(), opener=opener_for(NEW, fail_manifest=True),
                    environ={"HOST_CONTAINERNAME": "Obelisk"})
check("a registry that errors is not 'up to date' either",
      st["update_available"] is None, st)
check("and that is explained too", "manifest" in st["problem"], st)

st = version.status(inspect=inspector(digest=None), opener=opener_for(NEW),
                    environ={"HOST_CONTAINERNAME": "Obelisk"})
check("a container built locally has nothing to compare against",
      st["update_available"] is None, st)
check("and says so rather than guessing", "nothing to compare" in st["problem"], st)

# ---- the registry is chosen from the image reference, not hardcoded
#
# This asked GHCR about everything. After the template moved to Docker Hub it was
# therefore asking the wrong registry - and getting the right answer, because the two
# are published in lockstep from one build. Right by luck stops being right the moment
# somebody runs only one of them.
check("a bare owner/name is Docker Hub, as Docker itself reads it",
      version.split_ref("tbgkiller/obelisk") == ("docker.io", "tbgkiller/obelisk"))
check("a hostname with a dot is a registry",
      version.split_ref("ghcr.io/a/b") == ("ghcr.io", "a/b"))
check("an explicit docker.io is Hub too",
      version.split_ref("docker.io/a/b") == ("docker.io", "a/b"))
check("and a port makes it a host as well",
      version.split_ref("localhost:5000/a/b") == ("localhost:5000", "a/b"))

_urls = []


def _spy_opener(url, token):
    _urls.append(url)
    return '{"token": "t"}' if token is None else NEW


version.published("tbgkiller/obelisk", opener=_spy_opener)
check("a Hub image is asked of Docker Hub",
      all("docker.io" in u for u in _urls), _urls)
check("using Hub's own token service", "auth.docker.io" in _urls[0], _urls[0])
check("and Hub's registry for the manifest", "registry-1.docker.io" in _urls[1], _urls[1])

_urls[:] = []
version.published("ghcr.io/tbgkiller/obelisk", opener=_spy_opener)
check("a GHCR image is still asked of GHCR",
      all("ghcr.io" in u for u in _urls), _urls)
check("and the repository path drops the host",
      _urls[1].endswith("/v2/tbgkiller/obelisk/manifests/latest"), _urls[1])

_d, _p = version.published("some.registry.example/a/b", opener=_spy_opener)
check("a registry we have no recipe for is refused, not guessed at",
      _d is None and "does not know how to ask" in _p.replace("this ", ""), _p)

# ---- the token exchange is the whole point
seen = []


def watching(url, token):
    seen.append((url, token))
    return '{"token": "t"}' if token is None else NEW


version.status(inspect=inspector(), opener=watching,
               environ={"HOST_CONTAINERNAME": "Obelisk"})
check("a token is fetched before the manifest is asked for",
      len(seen) == 2 and seen[0][1] is None and seen[1][1] == "t", seen)
check("the token request names the repository",
      "obelisk" in seen[0][0], seen[0][0])
check("and the manifest request asks for the tag we run",
      seen[1][0].endswith("/manifests/latest"), seen[1][0])

# ---- it must never update itself
src = open(version.__file__, encoding="utf-8").read()
for word in ("docker pull", "subprocess", "restart", "recreate"):
    check("version.py never tries to %s" % word, word not in src.lower(), word)
check("and says the Docker page is what applies it",
      "Force Update" in src or "Apply\nUpdate" in src or "Apply Update" in src)

check("digests are shortened for display", version.short(NEW) == "a58503a34536",
      version.short(NEW))
check("and an unknown one says so", version.short(None) == "unknown")

print("\nFAILURES: %s" % fails if fails else "\nall version tests passed")
sys.exit(1 if fails else 0)
