"""
Making an Obelisk cluster a first-class Unraid stack.

Unraid's Compose Manager shows a project as a grouped stack with its own start, stop and
update controls. A project is nothing more than a directory of five small files, and the
join between that directory and the running containers is the compose project name -
Docker labels every container with `com.docker.compose.project`, and the plugin matches
it against the `project_name` file.

Obelisk's containers already carried the right label. All that was missing was the
directory saying the project exists, so a generated cluster showed up as loose containers
instead of the stack the operator expected.

Both tools then drive the same stack, because Compose identifies a stack by project name
and not by file path: the plugin's buttons and Obelisk's launch are two front ends to one
thing. The one rule that follows is that the compose file has a single author. Obelisk
regenerates it from the store on every apply, so hand edits in the plugin's editor are
overwritten - which is why the generated file and the project description both say so.

Nothing here ever touches a project it did not create. The directory is named for this
cluster, and if a directory of that name already exists belonging to someone else, this
refuses rather than adopting it. Somebody's live cluster is not ours to register.
"""

import logging, os, shutil

log = logging.getLogger("obelisk.stack")

PROJECTS = "/boot/config/plugins/compose.manager/projects"
COMPOSE_NAME = "compose.yaml"

# Proof that Obelisk created a project, written into every one it owns. Matching the
# project *name* is not proof of anything: a hand-built cluster whose name we happened
# to generate would look like ours and get overwritten. Ownership has to be something we
# put there ourselves.
MARKER = ".managed-by-obelisk"
ICON_URL = "https://raw.githubusercontent.com/tbgkiller/obelisk/main/docs/icon.png"

# Written into every project we own, so the next person to open it knows the rule.
DO_NOT_EDIT = ("Generated and managed by Obelisk - do not edit here. Obelisk rewrites "
               "this stack from its own settings on every apply.")


def projects_dir(environ=None):
    """Where Compose Manager keeps projects, if it is mounted into this container."""
    environ = os.environ if environ is None else environ
    return (environ.get("OBELISK_PROJECTS") or PROJECTS).rstrip("/")


def available(environ=None, isdir=None):
    """Whether this install can register stacks at all.

    Absent is a normal, supported install - the cluster simply runs as containers, which
    is what happened before any of this existed.
    """
    isdir = isdir or os.path.isdir
    return isdir(projects_dir(environ))


def project_dir(project_name, environ=None):
    return os.path.join(projects_dir(environ), str(project_name))


def compose_file(project_name, environ=None):
    return os.path.join(project_dir(project_name, environ), COMPOSE_NAME)


def owns(project_name, environ=None, read=None, isdir=None):
    """(ok, why) - may we write this project directory?

    Ours if it does not exist yet, or if it carries the marker Obelisk writes. A
    directory without that marker belongs to somebody else - most importantly the
    operator's own hand-built cluster - and is never written to, adopted, or read beyond
    this single check. Matching names prove nothing.
    """
    isdir = isdir or os.path.isdir
    read = read or _read
    d = project_dir(project_name, environ)
    if not isdir(d):
        return True, "new"
    marked = (read(os.path.join(d, MARKER)) or "").strip()
    if not marked:
        return False, ("%s already exists and was not created by Obelisk - refusing to "
                       "touch it. Rename this cluster if you want a stack of your own."
                       % d)
    if marked != str(project_name):
        return False, ("%s is Obelisk's project %r, not %r - refusing to touch it"
                       % (d, marked, str(project_name)))
    return True, "ours"


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def register(store, project_name, compose_text, autostart=None, environ=None,
             write=None, isdir=None, makedirs=None):
    """Write the project so Unraid shows this cluster as a stack. (ok, message).

    Best effort by design: a cluster that runs but is not grouped in the UI is a cosmetic
    loss, and refusing to launch over it would be a real one.
    """
    if not available(environ, isdir=isdir):
        return False, "Compose Manager is not mounted, so the cluster runs as containers"

    ok, why = owns(project_name, environ, isdir=isdir)
    if not ok:
        log.warning("not registering a stack: %s", why)
        return False, why

    if autostart is None:
        autostart = bool(store.get("cluster_autostart"))

    d = project_dir(project_name, environ)
    write = write or _write
    makedirs = makedirs or (lambda p: os.makedirs(p, exist_ok=True))
    try:
        makedirs(d)
        files = {
            MARKER: str(project_name),
            "name": str(project_name),
            "project_name": str(project_name),
            COMPOSE_NAME: compose_text,
            "description": "%s. %s" % (
                str(store.get("cluster_name") or "ARK cluster").strip(), DO_NOT_EDIT),
            "autostart": "true" if autostart else "false",
            "icon": ICON_URL,
        }
        for name, body in files.items():
            write(os.path.join(d, name), body)
    except OSError as e:
        return False, "could not write the stack project: %s" % e

    log.info("registered stack %r in Compose Manager (autostart=%s)",
             project_name, "true" if autostart else "false")
    return True, d


def _write(path, body):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body if body.endswith("\n") or path.endswith(COMPOSE_NAME) else body)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def unregister(project_name, environ=None):
    """Remove a project directory we own. Never removes one we do not."""
    ok, why = owns(project_name, environ)
    if not ok:
        return False, why
    d = project_dir(project_name, environ)
    if not os.path.isdir(d):
        return True, "nothing to remove"
    shutil.rmtree(d, ignore_errors=True)
    return True, "removed %s" % d
