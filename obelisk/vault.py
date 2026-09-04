"""
Secrets that stay on this machine.

Cloud credentials are a different class of secret from the ones already in the store.
The admin password protects a game server; a Drive token and a crypt passphrase protect
every backup you have ever pushed. So they are kept apart: encrypted at rest, in their
own file, and deliberately excluded from the portable archive.

That exclusion is the point. A backup that carried the keys to the place it is stored
would mean anyone who obtained one archive could read all of them - and the archive is
the thing most likely to be copied somewhere careless. Losing the vault costs a
reconnect; leaking it costs everything.

The key file sits beside the ciphertext, so this is not protection against someone who
already has the whole data root. It is protection against the archive, against a stray
copy of settings.json, and against a cloud push - which is exactly where these values
would otherwise travel.
"""

import base64, json, logging, os, stat

from . import layout

log = logging.getLogger("obelisk.vault")

KEY_NAME = "secret.key"
VAULT_NAME = "cloud.json"

# Names never written into a backup, whatever else is in the data root.
EXCLUDED_FROM_BACKUP = (KEY_NAME, VAULT_NAME)


def _dir(store):
    return layout.paths(layout.root_of(store))["obelisk"]


def key_path(store):
    return os.path.join(_dir(store), KEY_NAME)


def vault_path(store):
    return os.path.join(_dir(store), VAULT_NAME)


def _fernet(store, create=True):
    """The cipher, keyed from a file generated once and kept owner-only."""
    from cryptography.fernet import Fernet

    path = key_path(store)
    if not os.path.isfile(path):
        if not create:
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        key = Fernet.generate_key()
        with open(path, "wb") as fh:
            fh.write(key)
        _own_only(path)
        log.info("generated a new secret key at %s", path)
    with open(path, "rb") as fh:
        return Fernet(fh.read().strip())


def _own_only(path):
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass                     # a filesystem without POSIX modes; nothing to do


def available():
    """Whether encryption is usable at all. Never guess - refuse instead."""
    try:
        import cryptography  # noqa: F401
        return True, ""
    except ImportError:
        return False, ("this build has no encryption library, so cloud credentials "
                       "cannot be stored safely")


def save(store, values):
    """Encrypt and write the credential set. Returns (ok, message)."""
    ok, why = available()
    if not ok:
        return False, why
    try:
        f = _fernet(store)
        blob = f.encrypt(json.dumps(values, sort_keys=True).encode("utf-8"))
        path = vault_path(store)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(blob)
        _own_only(path)
    except Exception as e:
        return False, "could not store credentials: %s" % e
    # Deliberately no value, and no key names either: this line ends up in logs.
    log.info("cloud credentials updated")
    return True, "saved"


def load(store):
    """The credential set, or {} when there is none or it cannot be read."""
    path = vault_path(store)
    if not os.path.isfile(path):
        return {}
    try:
        f = _fernet(store, create=False)
        if f is None:
            log.warning("credentials exist but the key file is gone - reconnect the cloud")
            return {}
        with open(path, "rb") as fh:
            return json.loads(f.decrypt(fh.read()).decode("utf-8"))
    except Exception as e:
        log.warning("stored credentials could not be read (%s) - reconnect the cloud", e)
        return {}


def clear(store):
    for p in (vault_path(store),):
        try:
            os.remove(p)
        except OSError:
            pass
    return True, "disconnected"


def configured(store):
    return bool(load(store))


def redacted(store):
    """What is stored, safe to render. Values are never returned."""
    values = load(store)
    return {k: ("set" if str(v).strip() else "empty") for k, v in sorted(values.items())}
