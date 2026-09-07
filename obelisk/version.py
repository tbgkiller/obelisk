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

REGISTRY = "ghcr.io"
TOKEN_URL = ("https://ghcr.io/token?scope=repository%3A{repo}%3Apull&service=ghcr.io")
MANIFEST_URL = "https://ghcr.io/v2/{repo}/manifests/{tag}"
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
            return dockerctl._run(["inspect"] + args, timeout=20)

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
    path = repo.split("/", 1)[-1] if repo.startswith(REGISTRY + "/") else repo
    opener = opener or _fetch
    try:
        raw = opener(TOKEN_URL.format(repo=path.replace("/", "%2F")), None)
        token = json.loads(raw)["token"]
    except Exception as e:                        # noqa: BLE001 - reported, never raised
        return None, "could not get a registry token: %s" % e
    try:
        digest = opener(MANIFEST_URL.format(repo=path, tag=tag), token)
    except Exception as e:                        # noqa: BLE001
        return None, "could not read the published manifest: %s" % e
    return digest, ""


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
