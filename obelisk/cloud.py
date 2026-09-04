"""
Off-site copies, encrypted before they leave.

rclone does the talking, so the provider is a detail: Drive, S3, B2, Dropbox and forty
others behave the same from here. Everything is written through an rclone `crypt` remote
wrapping the real one, so what the provider stores is ciphertext with obscured filenames.
They hold your backups; they cannot read them, and neither can anyone who takes their
copy.

The passphrase and the provider token live in the vault - encrypted on this machine and
excluded from the archive. That is the asymmetry worth keeping: losing them costs a
reconnect, leaking them costs every backup you ever pushed.

Connecting Google Drive needs a Google login, which is the owner's to do and nobody
else's. So the flow here never asks for a password: `rclone authorize drive` is run
wherever a browser is, and the resulting token is pasted back. Obelisk stores the token
and never sees the account.
"""

import json, logging, os, shutil, subprocess

from . import vault

log = logging.getLogger("obelisk.cloud")

REMOTE = "cloud"            # the provider
CRYPT = "cloudcrypt"        # the encrypted view of it - everything goes through this
CONF_NAME = "rclone.conf"

# Providers whose token comes from `rclone authorize` rather than a key pair.
OAUTH_PROVIDERS = ("drive", "dropbox", "onedrive", "box", "pcloud", "yandex")

PROVIDERS = [
    dict(key="drive", name="Google Drive", oauth=True),
    dict(key="dropbox", name="Dropbox", oauth=True),
    dict(key="onedrive", name="Microsoft OneDrive", oauth=True),
    dict(key="s3", name="Amazon S3 (or any S3-compatible)", oauth=False),
    dict(key="b2", name="Backblaze B2", oauth=False),
]
BY_KEY = {p["key"]: p for p in PROVIDERS}


def _run(args, timeout=300, input_text=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           input=input_text)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "rclone is not installed in this image"
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout
    except Exception as e:
        return 1, str(e)


def available():
    """(ok, message) - is rclone usable at all."""
    if not shutil.which("rclone"):
        return False, ("rclone is not installed in this image, so cloud sync is "
                       "unavailable")
    rc, out = _run(["rclone", "version"], timeout=30)
    if rc != 0:
        return False, "rclone will not run: %s" % out.strip()[:200]
    return True, out.strip().splitlines()[0] if out.strip() else "rclone present"


def conf_path(store):
    from . import layout
    return os.path.join(layout.paths(store.get("appdata"))["obelisk"], CONF_NAME)


def authorize_command(provider):
    """The command the owner runs on a machine that has a browser.

    Deliberately not run here. It opens a Google login, and that is theirs to complete -
    Obelisk never sees the account, only the token that comes back.
    """
    return "rclone authorize %s" % provider


def write_config(store, creds=None):
    """Write rclone.conf from the vault. Returns (ok, message).

    Regenerated from the vault every time rather than edited in place, so the file on
    disk can never drift from what was actually saved.
    """
    creds = creds if creds is not None else vault.load(store)
    if not creds:
        return False, "no cloud credentials stored yet"

    provider = creds.get("provider", "")
    if provider not in BY_KEY:
        return False, "unknown provider %r" % provider

    lines = ["[%s]" % REMOTE, "type = %s" % provider]
    if BY_KEY[provider]["oauth"]:
        token = str(creds.get("token", "")).strip()
        if not token:
            return False, "no token stored - reconnect the cloud"
        lines.append("token = %s" % token)
    else:
        for field in ("access_key_id", "secret_access_key", "account", "key",
                      "region", "endpoint", "provider_name"):
            v = str(creds.get(field, "")).strip()
            if v:
                name = "provider" if field == "provider_name" else field
                lines.append("%s = %s" % (name, v))

    # Everything Obelisk writes goes through the crypt view, never the bare remote.
    base = "%s:%s" % (REMOTE, str(creds.get("path", "obelisk-backups")).strip("/"))
    lines += ["", "[%s]" % CRYPT, "type = crypt", "remote = %s" % base,
              "filename_encryption = standard", "directory_name_encryption = true",
              "password = %s" % creds.get("password_obscured", ""),
              ]
    if creds.get("password2_obscured"):
        lines.append("password2 = %s" % creds["password2_obscured"])

    path = conf_path(store)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True, path


def obscure(secret):
    """rclone's own reversible obscuring. Not encryption - the crypt password still
    has to be treated as a secret, which is why it lives in the vault."""
    rc, out = _run(["rclone", "obscure", secret], timeout=30)
    if rc != 0:
        return None, out.strip()[:200]
    return out.strip().splitlines()[-1], ""


def _rclone(store, args, timeout=1800):
    ok, why = available()
    if not ok:
        return 127, why
    path = conf_path(store)
    if not os.path.isfile(path):
        ok2, msg = write_config(store)
        if not ok2:
            return 1, msg
    return _run(["rclone", "--config", path] + list(args), timeout=timeout)


def connect(store, provider, password, path="obelisk-backups", token="", extra=None,
            password2=""):
    """Store credentials and prove they work. Returns (ok, message).

    The connection is tested before it is called connected: a cloud backup that has
    never successfully talked to the provider is exactly the kind of thing people
    discover at restore time.
    """
    ok, why = vault.available()
    if not ok:
        return False, why
    ok, why = available()
    if not ok:
        return False, why
    if provider not in BY_KEY:
        return False, "unknown provider %r" % provider
    if not str(password).strip():
        return False, ("an encryption passphrase is required - it is what keeps the "
                       "provider from reading your saves")
    if BY_KEY[provider]["oauth"] and not str(token).strip():
        return False, ("no token yet - run `%s` on a machine with a browser and paste "
                       "the result here" % authorize_command(provider))

    obscured, err = obscure(str(password))
    if obscured is None:
        return False, "could not prepare the passphrase: %s" % err
    creds = {"provider": provider, "path": path or "obelisk-backups",
             "token": str(token).strip(), "password_obscured": obscured}
    if str(password2).strip():
        o2, err2 = obscure(str(password2))
        if o2 is None:
            return False, "could not prepare the salt: %s" % err2
        creds["password2_obscured"] = o2
    creds.update({k: v for k, v in (extra or {}).items() if str(v).strip()})

    ok, msg = vault.save(store, creds)
    if not ok:
        return False, msg
    ok, msg = write_config(store, creds)
    if not ok:
        return False, msg

    ok, detail = test(store)
    if not ok:
        return False, ("credentials stored but the connection failed, so nothing will "
                       "be pushed yet: %s" % detail)
    return True, "Connected to %s. Backups are encrypted here before they are sent." \
                 % BY_KEY[provider]["name"]


def test(store):
    """Prove the remote answers and the passphrase decrypts it. (ok, detail)."""
    rc, out = _rclone(store, ["lsd", "%s:" % CRYPT], timeout=120)
    if rc == 0:
        return True, "reachable"
    # A fresh remote with no directory yet is a success, not a failure.
    if "directory not found" in out.lower() or "not found" in out.lower():
        return True, "reachable (empty)"
    return False, out.strip()[-400:] or "rclone exit %d" % rc


def disconnect(store):
    vault.clear(store)
    try:
        os.remove(conf_path(store))
    except OSError:
        pass
    return True, "Cloud disconnected. Nothing further is sent; what is already there stays."


def configured(store):
    return vault.configured(store)


def status(store):
    """Everything the Cloud page needs. Never raises."""
    ok, detail = available()
    out = {"rclone_ok": ok, "rclone_detail": detail,
           "encryption_ok": vault.available()[0],
           "connected": False, "provider": "", "path": "", "reachable": None,
           "reachable_detail": ""}
    if not ok:
        return out
    creds = vault.load(store)
    if not creds:
        return out
    out["connected"] = True
    out["provider"] = BY_KEY.get(creds.get("provider", ""), {}).get("name", creds.get("provider", ""))
    out["path"] = creds.get("path", "")
    out["reachable"], out["reachable_detail"] = test(store)
    return out


def push(store, local_path, timeout=7200):
    """Upload one archive, encrypted. Returns (ok, message)."""
    if not configured(store):
        return False, "no cloud connected"
    if not os.path.isfile(local_path):
        return False, "nothing to upload at %s" % local_path
    rc, out = _rclone(store, ["copy", local_path, "%s:" % CRYPT, "--no-traverse"],
                      timeout=timeout)
    if rc != 0:
        return False, "upload failed: %s" % out.strip()[-400:]
    return True, "Uploaded %s, encrypted." % os.path.basename(local_path)


def listing(store, timeout=300):
    """What is in the cloud, newest first. Returns (ok, rows_or_message)."""
    if not configured(store):
        return False, "no cloud connected"
    rc, out = _rclone(store, ["lsjson", "%s:" % CRYPT], timeout=timeout)
    if rc != 0:
        if "directory not found" in out.lower():
            return True, []
        return False, out.strip()[-400:]
    try:
        rows = json.loads(out[out.index("["):]) if "[" in out else []
    except ValueError:
        return False, "could not read the remote listing"
    files = [{"name": r.get("Name", ""), "bytes": r.get("Size", 0),
              "when": (r.get("ModTime", "") or "")[:16].replace("T", " ")}
             for r in rows if not r.get("IsDir")]
    files.sort(key=lambda r: r["when"], reverse=True)
    return True, files


def pull(store, name, dest_dir, timeout=7200):
    """Download one archive and decrypt it on the way in. (ok, message_or_path)."""
    if not configured(store):
        return False, "no cloud connected"
    os.makedirs(dest_dir, exist_ok=True)
    rc, out = _rclone(store, ["copy", "%s:%s" % (CRYPT, name), dest_dir], timeout=timeout)
    if rc != 0:
        return False, "download failed: %s" % out.strip()[-400:]
    path = os.path.join(dest_dir, name)
    if not os.path.isfile(path):
        return False, "rclone reported success but %s is not here" % name
    return True, path


def prune(store, keep, timeout=600):
    """Keep only the newest `keep` archives in the cloud. Returns names removed."""
    keep = int(keep or 0)
    if keep <= 0 or not configured(store):
        return []
    ok, rows = listing(store)
    if not ok or not isinstance(rows, list):
        return []
    removed = []
    for row in rows[keep:]:
        rc, _out = _rclone(store, ["delete", "%s:%s" % (CRYPT, row["name"])], timeout=timeout)
        if rc == 0:
            removed.append(row["name"])
    if removed:
        log.info("pruned %d old cloud backup(s)", len(removed))
    return removed
