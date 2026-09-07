"""
Which Obelisk is running, and whether a newer one is published.

This exists because the update checker people actually rely on lied. Unraid compares
the digest it has against the digest it fetches, and fetching from GHCR needs a bearer
token even for a public image - a step it appears to skip, so it records a stale digest
and reports "up to date" while an update sits unapplied. Not an error, not a warning: a
confident wrong answer, which is the one shape of failure a user cannot act on.

So Obelisk asks for itself, in the place the operator is already looking. It does the
token exchange, compares the published digest against the one it is running, and says
which. That is all it does.

**It never updates itself.** Applying the update is the Docker page's job - Apply
Update, or Force Update when the checker is being stubborn - because a manager that
replaces its own container mid-flight is a manager that cannot report how it went. This
tells the truth and points at the button.
"""

import json
import logging
import os
import urllib.request

log = logging.getLogger("obelisk.version")

# Where to ask, per registry. The image reference says which one it came from, so that
# is what decides - hardcoding a registry meant that after the template moved to Docker
# Hub this asked GHCR about a Hub image and got the right answer only because the two
# are published in lockstep. Right by luck is not right.
REGISTRIES = {
    "ghcr.io": {
        "token": "https://ghcr.io/token?scope=repository%3A{path}%3Apull&service=ghcr.io",
        "manifest": "https://ghcr.io/v2/{repo}/manifests/{tag}",
    },
    "docker.io": {
        "token": ("https://auth.docker.io/token?service=registry.docker.io"
                  "&scope=repository%3A{path}%3Apull"),
        "manifest": "https://registry-1.docker.io/v2/{repo}/manifests/{tag}",
    },
}
DEFAULT_REGISTRY = "docker.io"
ACCEPT = ("application/vnd.oci.image.index.v1+json,"
          "application/vnd.docker.distribution.manifest.list.v2+json,"
          "application/vnd.oci.image.manifest.v1+json,"
          "application/vnd.docker.distribution.manifest.v2+json")
TIMEOUT = 8


def running(inspect=None, environ=None):
    """What this container is: its image reference, digest and the commit it was built
    from. Asked of Docker, because the container knows and guessing would be worse."""
    environ = os.environ if environ is None else environ
    name = (environ.get("HOST_CONTAINERNAME") or "").strip() or "Obelisk"
    out = {"container": name, "commit": None, "digest": None, "repo": None, "image": None}
    if inspect is None:
        from . import dockerctl

        def inspect(args):
            # "docker" first: _run() executes exactly the argv it is given, it does not
            # prepend anything. Leaving it off asked the OS to run a binary called
            # `inspect`, which reported "not installed in this image" - and because the
            # tests injected their own inspector, the real default was never once
            # exercised until it ran on a live host.
            return dockerctl._run(["docker", "inspect"] + args, timeout=20)

    rc, text = inspect([name, "--format",
                        "{{index .Config.Labels \"org.opencontainers.image.revision\"}}"
                        "|{{.Image}}"])
    if rc == 0 and text.strip():
        commit, _, image = text.strip().partition("|")
        out["commit"] = commit or None
        out["image"] = image or None

    if out["image"]:
        rc, text = inspect([out["image"], "--format",
                            "{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}"])
        ref = text.strip() if rc == 0 else ""
        if "@" in ref:
            out["repo"], _, out["digest"] = ref.partition("@")
    return out


def published(repo, tag="latest", opener=None):
    """(digest, problem) for repo:tag as the registry serves it right now.

    The token exchange is the whole point of this function - it is the step whose
    absence produces the wrong answer everybody else is getting.
    """
    host, path = split_ref(repo)
    urls = REGISTRIES.get(host)
    if not urls:
        return None, ("%s is a registry this does not know how to ask (%s)"
                      % (host, repo))
    opener = opener or _fetch
    try:
        raw = opener(urls["token"].format(path=path.replace("/", "%2F")), None)
        token = json.loads(raw)["token"]
    except Exception as e:                        # noqa: BLE001 - reported, never raised
        return None, "could not get a registry token: %s" % e
    try:
        digest = opener(urls["manifest"].format(repo=path, tag=tag), token)
    except Exception as e:                        # noqa: BLE001
        return None, "could not read the published manifest: %s" % e
    return digest, ""


def split_ref(repo):
    """(registry host, repository path) from an image reference.

    Docker writes a Hub image as `owner/name` with no host at all, so "has no dot in the
    first segment" is what distinguishes a Hub repository from a registry hostname -
    the same rule Docker itself uses.
    """
    first = repo.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        host, _, path = repo.partition("/")
        return host, path
    return DEFAULT_REGISTRY, repo


def _fetch(url, token):
    """A token when token is None, otherwise the manifest's content digest header."""
    req = urllib.request.Request(url)
    if token is None:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8")
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", ACCEPT)
    req.get_method = lambda: "HEAD"
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.headers.get("Docker-Content-Digest") or ""


def status(inspect=None, opener=None, environ=None):
    """Everything the UI needs to say something true about updates."""
    out = dict(running(inspect=inspect, environ=environ))
    out.update({"published": None, "update_available": None, "problem": ""})
    if not out.get("repo") or not out.get("digest"):
        out["problem"] = ("this container was not started from a published image, so "
                          "there is nothing to compare against")
        return out
    digest, problem = published(out["repo"], opener=opener)
    out["published"] = digest
    if problem or not digest:
        out["problem"] = problem or "the registry did not return a digest"
        return out
    out["update_available"] = digest != out["digest"]
    return out


def short(value, n=12):
    text = str(value or "")
    return text.split(":", 1)[-1][:n] if text else "unknown"
