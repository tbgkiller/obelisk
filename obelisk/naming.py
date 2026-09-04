"""
Two kinds of name, with two different rules.

A **session name** is what players see in the in-game browser. It is carried over
Valve's Master Server Query Protocol, which is how ARK obtains the unofficial server
list, and the game limits that field to 63 characters. Get it wrong and the server runs
perfectly, accepts direct connections, and simply never appears in the list - which is
the worst failure mode there is, because nothing is broken and nobody can find you.

A **container name** is what Docker sees. Different charset, different length, different
consequences. The two are sanitised separately and on purpose: a display name of
"Nate's Island (PvP!)" is a fine session name and an illegal container name, and
flattening one to satisfy the other would either mangle what players read or produce a
container name Docker refuses.

What is actually known about the session name, kept honest about its sources:

  * 63 characters. Documented: "Unmodified Ark servers limit their server name field to
    63 characters" (ARK Official Community Wiki, Server Browser). This is the constraint
    worth enforcing hardest, because the assembled name - prefix, number, map, tag line -
    is easy to push past it without noticing.
  * The list comes from Valve's Master Server Query Protocol, so the name travels as a
    protocol string. Control characters have no business in one.
  * A leading '#' and non-ASCII characters have both been observed to leave a server
    running but absent from the browser. That is reported behaviour rather than published
    rule, so it is enforced as a refusal with a reason rather than presented as gospel.

Where the rule is uncertain the code is permissive: ordinary punctuation people actually
use in server names is allowed. Only the things known to hide a server are refused.
"""

import re

# The documented limit for the whole assembled name.
SESSION_MAX = 63

# Printable ASCII, minus the control range. Kept wide on purpose.
_PRINTABLE = re.compile(r"^[\x20-\x7e]*$")


def session_problems(name):
    """Everything wrong with a session name, worst first. [] means fine."""
    problems = []
    text = "" if name is None else str(name)

    if not text.strip():
        problems.append("A server with no name is unfindable - give it one.")
        return problems

    if len(text) > SESSION_MAX:
        problems.append(
            "%d characters - the game's server name field holds %d, so this would be "
            "cut short in the browser. Shorten the prefix or the tag line."
            % (len(text), SESSION_MAX))

    if text.startswith("#"):
        problems.append(
            "Starting with # hides the server: it runs fine and accepts direct "
            "connections, but never appears in the in-game list.")

    bad = sorted({c for c in text if ord(c) > 126 or ord(c) < 32})
    if bad:
        shown = ", ".join(repr(c) for c in bad[:5])
        problems.append(
            "Contains characters that can hide your server from the in-game browser "
            "(%s). Stick to plain letters, digits, spaces and ordinary punctuation."
            % shown)

    return problems


def sanitize_session(name, limit=SESSION_MAX):
    """A session name made safe, keeping as much of the original as possible."""
    text = "" if name is None else str(name)
    text = "".join(c for c in text if 32 <= ord(c) <= 126)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip("#").strip()
    return text[:limit].strip()


def assemble_session(prefix, index, map_name, tags, limit=SESSION_MAX):
    """Build the browser name, trimming the least important part first.

    Order matters when something has to give: the map name is what a player is looking
    for, the prefix is how they recognise the cluster, and the tag line is decoration.
    So the tag line is dropped before the prefix, and the prefix before the map.
    """
    prefix = sanitize_session(prefix, limit)
    tags = sanitize_session(tags, limit)
    map_name = sanitize_session(map_name, limit)

    head = "%s %02d" % (prefix, index + 1) if prefix else ""
    for bits in ([head, map_name, tags], [head, map_name], [map_name]):
        candidate = " | ".join(b for b in bits if b)
        if len(candidate) <= limit:
            return candidate
    return map_name[:limit].strip()


# ---------------------------------------------------------------- container names

# Docker: [a-zA-Z0-9][a-zA-Z0-9_.-]*
_DOCKER_OK = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def docker_slug(text, fallback="instance"):
    """A Docker-legal fragment from arbitrary human text.

    Lowercased, spaces and punctuation folded to '-', because a display name is written
    for players and a container name is written for Docker, and neither should be forced
    to look like the other.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    if not slug or not slug[0].isalnum():
        slug = fallback
    return slug[:48]


def container_name(project, instance):
    """The container name for one instance of one cluster.

    Keyed on cluster *and* instance, because both halves matter. A bare `asa_island` is
    a name any other cluster on the host would also pick - a generated stack once tried
    to claim exactly that from a live hand-built cluster, and Docker refusing is the only
    reason it was a failed launch rather than a clobbered game server. Keying on the map
    type alone collides again the moment a cluster runs the same map twice.
    """
    name = "asa-%s-%s" % (docker_slug(project, "cluster"), docker_slug(instance))
    return name if _DOCKER_OK.match(name) else "asa-cluster-instance"


def is_valid_container_name(name):
    return bool(_DOCKER_OK.match(str(name or ""))) and len(str(name)) <= 128
