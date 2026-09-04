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
    """Obelisk's own data folder - the definition lives at the top of it.

    Separate from the Ark folder on purpose: this is the small, irreplaceable half.
    """
    return install.container_root(environ)


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

    listen, published, how = install.derive_ports(environ)
    from_env["status_port"] = published
    if listen != published:
        log.info("web UI: listening on %s inside the container, published as %s on the "
                 "host (from %s)", listen, published, how)
    else:
        log.info("web UI port: %s (from the %s)", published, how)

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

    # Lay both folders out at boot rather than waiting for a launch, so each one
    # immediately describes what belongs in it.
    try:
        from . import layout
        layout.ensure_obelisk(layout.root_of(store))
        layout.ensure_ark(layout.ark_root_of(store, environ))
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
        # The whole first-run instruction in one place, with a URL that can be copied
        # straight out of the container log rather than reassembled by the reader.
        url = install.setup_url(environ, published=store.get("status_port"))
        log.warning(
            "\n"
            "  +--------------------------------------------------------------+\n"
            "  |  Obelisk is up and ready to set up.                          |\n"
            "  +--------------------------------------------------------------+\n"
            "     Setup code:  %s\n"
            "     Open:        %s\n"
            "\n"
            "     Paste the code into that page.\n"
            "     Still to configure after that: %s\n",
            setup_code, url, blocking)

    return store, created, setup_code
