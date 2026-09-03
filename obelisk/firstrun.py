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

log = logging.getLogger("obelisk.firstrun")

DATA_DIR   = os.environ.get("OBELISK_DATA", "/data")
STORE_NAME = "settings.json"


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
    data_dir = data_dir or DATA_DIR
    environ = environ if environ is not None else os.environ
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

    # Install-phase settings are re-read from the environment on every boot, not just
    # the first: they mirror what Docker actually created the container with, so
    # editing the template and recreating is how they change. Anything absent keeps
    # the schema default rather than being blanked.
    from_env = {}
    for key in INSTALL_KEYS:
        target = BY_KEY[key]["target"]
        if not target.startswith("env:"):
            continue
        raw = environ.get(target.split(":", 1)[1])
        if raw not in (None, ""):
            from_env[key] = raw
    if from_env:
        try:
            store.patch(from_env, source="install")
        except Invalid as e:
            log.warning("container environment has an unusable install setting: %s", e)

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
            "  ┌──────────────────────────────────────────────────────────┐\n"
            "  │  Obelisk is ready to set up.                             │\n"
            "  │  Open  http://<this-host>:%-5s/setup                     │\n"
            "  │  Setup code:  %-42s │\n"
            "  └──────────────────────────────────────────────────────────┘\n"
            "  Still to configure: %s",
            store.get("status_port"), setup_code, blocking)

    return store, created, setup_code
