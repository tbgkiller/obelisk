"""
First run: starting the container is the whole setup.

Obelisk ships with no template to fill in and no environment variables you must
set before it will boot. On first start it creates its own store from the schema
defaults, prints a one-time setup code to the container log, and waits for you to
finish the rest in the browser.

The one exception is an upgrade: if the container is started with the environment
variables an older hand-built stack used, those are imported once so an existing
cluster carries its settings across instead of starting blank.
"""

import logging, os, secrets, stat

from .schema import BY_KEY, INSTALL_KEYS
from .settings import Store, Invalid
from .import_env import ENV_TO_KEY
from . import install

log = logging.getLogger("obelisk.firstrun")

STORE_NAME = "settings.json"


def store_dir(environ=None):
    """<data root>/obelisk - one mount, and the store is a folder inside it.

    Keeping the store under the same root as the saves is what lets the install form
    ask for one folder instead of two, and lets the store tell the rest of Obelisk
    where the root is without being told twice.
    """
    from . import layout
    return layout.paths(install.container_root(environ))["obelisk"]


def _import_legacy_env(store, environ):
    """Carry an older env-configured stack into the store. Returns keys imported."""
    taken = []
    for env_name, key in ENV_TO_KEY.items():
        raw = environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            store.patch({key: raw})
            taken.append(key)
        except Invalid:
            # A bad legacy value must not stop the container booting - the setup
            # page will show it as still needing attention.
            log.warning("ignoring unusable %s from the environment", env_name)
    return taken


def bootstrap(data_dir=None, environ=None):
    """Make sure there is a usable store. Returns (store, created, setup_code).

    `setup_code` is non-None only when Obelisk has no admin token yet - it is the
    one-time code that lets you claim a fresh instance, and it is printed to the
    container log rather than baked into any file you have to edit.
    """
    environ = environ if environ is not None else os.environ
    data_dir = data_dir or store_dir(environ)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, STORE_NAME)
    created = not os.path.isfile(path)

    store = Store(path)
    if not created:
        store.load()

    if created:
        imported = _import_legacy_env(store, environ)
        if imported:
            log.info("first run: imported %d setting(s) from the environment of an "
                     "existing stack", len(imported))
        else:
            log.info("first run: starting from defaults - nothing to import")

    # Install-phase settings mirror what Docker actually created the container with, so
    # they are re-read on every boot rather than only the first: editing the template
    # and recreating is how they change.
    #
    # They are derived, not asked for twice. The cluster's data path is whatever was
    # bind-mounted and the listening port is the one the image exposes, so the operator
    # sets each of them once, in the place Docker already needed them. An explicit
    # environment variable still wins, which is what keeps an upgraded hand-built stack
    # working.
    from_env = {}

    appdata, how = install.derive_appdata(environ, data_dir=data_dir)
    if appdata:
        from_env["appdata"] = appdata
        log.info("cluster data: %s (from the %s)", appdata, how)
    else:
        log.warning("no cluster data mount found - falling back to %s. Bind-mount the "
                    "cluster's folder at the same path inside and out.",
                    BY_KEY["appdata"]["default"])

    port, how = install.derive_status_port(environ)
    from_env["status_port"] = port
    log.info("web UI port: %s (from the %s)", port, how)

    # One at a time, deliberately. store.patch() is all-or-nothing so a half-applied
    # change can never happen, which is right for a form submission and wrong here: a
    # single unusable value would silently discard the good ones beside it, and the
    # container would come up listening on a different port than it just announced.
    for key, value in from_env.items():
        try:
            store.patch({key: value}, source="install")
        except Invalid as e:
            log.warning("ignoring unusable %s (%s) - keeping %r",
                        key, e, store.get(key))

    # Timezone is a UI setting, so it has to take effect on the process without a
    # recreate - otherwise wipe times and log stamps would lag a setting change.
    install.apply_timezone(store.get("timezone"))

    # Lay out the root at boot rather than waiting for a launch, so the one folder the
    # operator picked immediately describes itself: obelisk/, shared/, cluster/,
    # instances/. An empty folder gives no clue that it is the whole cluster.
    try:
        from . import layout
        layout.ensure(layout.root_of(store))
    except OSError as e:
        log.warning("could not create the data layout: %s", e)

    setup_code = None
    if not str(store.get("admin_token")).strip():
        setup_code = secrets.token_urlsafe(9)
        store.patch({"admin_token": setup_code})

    store.save()
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    if setup_code:
        blocking = ", ".join(b["label"] for b in store.readiness()) or "nothing"
        log.warning(
            "\n"
            "  +----------------------------------------------------------+\n"
            "  |  Obelisk is ready to set up.                             |\n"
            "  |  Open  http://<this-host>:%-5s/setup                     |\n"
            "  |  Setup code:  %-42s |\n"
            "  +----------------------------------------------------------+\n"
            "  Still to configure: %s",
            store.get("status_port"), setup_code, blocking)

    return store, created, setup_code
