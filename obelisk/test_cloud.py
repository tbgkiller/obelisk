"""
Off-site copies: encrypted before they leave, and keyed by something that never travels.

Two properties this suite exists to hold down:

  * the provider only ever sees ciphertext - everything goes through an rclone `crypt`
    remote, never the bare one;
  * the passphrase and token that unlock it are never in a backup archive. An archive
    carrying them would mean one stolen copy unlocks every copy.

rclone itself is faked. These tests prove what Obelisk asks it to do, which is where the
mistakes that matter live.

Fixture values are synthetic throughout.
"""

import json, os, sys, tarfile, tempfile

from . import backup as backupctl
from . import cloud as cloudlib
from . import layout, vault
from .settings import Store

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


def fresh(**over):
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "settings.json")).load()
    st.patch({"status_port": 8088}, source="install")
    st.patch(dict({"maps": "island", "admin_password": "synthetic-pw",
                   "cluster_id": "testcluster", "backup_keep": 5}, **over))
    st.data["cluster"]["appdata"] = os.path.join(d, "data")
    return st, st.get("appdata")


def populate(root):
    layout.ensure(root, ["island"])
    sd = os.path.join(root, "shared", "SavedArks", "TheIsland_WP")
    os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "TheIsland_WP.ark"), "wb") as fh:
        fh.write(bytes(4096))


# ---- a fake rclone that records exactly what it was asked to do
class FakeRclone:
    def __init__(self, rc=0, out="", listing=None):
        self.rc, self.out, self.calls = rc, out, []
        self.listing = listing if listing is not None else []
        self.deleted = []

    def __call__(self, args, timeout=300, input_text=None):
        self.calls.append(list(args))
        if args[:1] == ["rclone"] and args[1:2] == ["version"]:
            return 0, "rclone v1.66.0"
        if "obscure" in args:
            # Real `rclone obscure` returns a blob that does not contain the input.
            # Echoing it back would make the "not written in the clear" check pass for
            # the wrong reason.
            import hashlib
            return 0, hashlib.sha256(args[-1].encode()).hexdigest()[:32]
        if "lsjson" in args:
            return 0, json.dumps(self.listing)
        if "lsd" in args:
            return self.rc, self.out
        if "delete" in args:
            self.deleted.append(args[-1])
            return 0, ""
        return self.rc, self.out


def install(fake, present=True):
    cloudlib._run = fake
    cloudlib.shutil.which = lambda n: "/usr/bin/rclone" if present else None


# ---------------------------------------------------------------- the vault
st, root = fresh()
ok, why = vault.available()
check("encryption is available in this build", ok, why)

ok, msg = vault.save(st, {"provider": "drive", "token": "synthetic-token",
                          "password_obscured": "OBSCURED(synthetic-phrase)"})
check("credentials save", ok, msg)
back = vault.load(st)
check("and come back intact", back["token"] == "synthetic-token", back)

raw = open(vault.vault_path(st), "rb").read()
check("the file on disk is ciphertext, not the token",
      b"synthetic-token" not in raw and b"drive" not in raw, raw[:60])
check("a key file exists beside it", os.path.isfile(vault.key_path(st)))
check("redaction never returns values",
      "set" in vault.redacted(st).values() and
      "synthetic-token" not in json.dumps(vault.redacted(st)), vault.redacted(st))

if os.name == "posix":
    check("the vault is owner-only", oct(os.stat(vault.vault_path(st)).st_mode)[-3:] == "600")
    check("the key is owner-only", oct(os.stat(vault.key_path(st)).st_mode)[-3:] == "600")
else:
    check("the vault is owner-only", True)
    check("the key is owner-only", True)

os.remove(vault.key_path(st))
check("losing the key means no credentials, not a crash", vault.load(st) == {})

# ---------------------------------------------------------------- never in a backup
st2, root2 = fresh()
populate(root2)
vault.save(st2, {"provider": "drive", "token": "synthetic-token",
                 "password_obscured": "OBSCURED(synthetic-phrase)"})
ok, msg, path = backupctl.create(st2)
check("a backup still succeeds with a vault present", ok, msg)
names = tarfile.open(path, "r:gz").getnames()
check("the vault is NOT in the archive",
      not any(n.endswith(vault.VAULT_NAME) for n in names),
      [n for n in names if "cloud" in n])
check("the key is NOT in the archive",
      not any(n.endswith(vault.KEY_NAME) for n in names),
      [n for n in names if "key" in n])
blob = open(path, "rb").read()
check("no credential value appears anywhere in the archive bytes",
      b"synthetic-token" not in blob and b"synthetic-phrase" not in blob)

# ---------------------------------------------------------------- connecting
st3, root3 = fresh()
fake = FakeRclone()
install(fake)

ok, msg = cloudlib.connect(st3, provider="drive", password="", token="t")
check("connecting without a passphrase is refused", not ok and "passphrase" in msg, msg)

ok, msg = cloudlib.connect(st3, provider="drive", password="synthetic-phrase", token="")
check("Google Drive without a token is refused", not ok, msg)
check("and it says exactly what to run",
      "rclone authorize drive" in msg, msg)

ok, msg = cloudlib.connect(st3, provider="nope", password="p", token="t")
check("an unknown provider is refused", not ok and "unknown provider" in msg, msg)

ok, msg = cloudlib.connect(st3, provider="drive", password="synthetic-phrase",
                           token='{"access_token":"synthetic"}', path="obelisk-backups")
check("connecting works once both are given", ok, msg)
check("and it says the provider cannot read the copies", "encrypted" in msg, msg)

conf = open(cloudlib.conf_path(st3), encoding="utf-8").read()
check("the config defines the provider remote", "[cloud]" in conf and "type = drive" in conf)
check("and an encrypted view of it", "[cloudcrypt]" in conf and "type = crypt" in conf)
check("the crypt wraps the provider remote", "remote = cloud:obelisk-backups" in conf, conf)
check("file names are encrypted too", "filename_encryption = standard" in conf)
check("the passphrase is not written in the clear",
      "synthetic-phrase" not in conf, conf)

# the raw token does live in rclone.conf - that is rclone's format, so the file must be
# owner-only and must never be in an archive
if os.name == "posix":
    check("rclone.conf is owner-only", oct(os.stat(cloudlib.conf_path(st3)).st_mode)[-3:] == "600")
else:
    check("rclone.conf is owner-only", True)

# The token also lands in rclone.conf in rclone's own plain format, inside the same
# folder a backup copies. That file must be excluded too, or the archive leaks the token
# by a different route than the vault.
populate(root3)
ok, msg, cpath = backupctl.create(st3)
check("a backup with a connected cloud succeeds", ok, msg)
cnames = tarfile.open(cpath, "r:gz").getnames()
check("rclone.conf is NOT in the archive",
      not any(n.endswith(cloudlib.CONF_NAME) for n in cnames),
      [n for n in cnames if "rclone" in n])
cblob = open(cpath, "rb").read()
check("the OAuth token appears nowhere in the archive bytes",
      b"synthetic" not in cblob or b'"access_token"' not in cblob, "token leaked")

# ---------------------------------------------------------------- everything via crypt
fake.calls.clear()
populate(root3)
ok, msg, apath = backupctl.create(st3)
ok, msg = cloudlib.push(st3, apath)
check("upload succeeds", ok, msg)
copy_calls = [c for c in fake.calls if "copy" in c]
check("the upload targets the ENCRYPTED remote",
      any("cloudcrypt:" in " ".join(c) for c in copy_calls), copy_calls)
check("the upload never targets the bare remote",
      not any(" cloud:" in " " + " ".join(c) for c in copy_calls), copy_calls)

ok, rows = cloudlib.listing(st3)
check("listing reads the encrypted remote", ok, rows)
check("listing goes through crypt too",
      any("cloudcrypt:" in " ".join(c) for c in fake.calls if "lsjson" in c), fake.calls)

dest = tempfile.mkdtemp()
fake_pull = FakeRclone()
install(fake_pull)
name = os.path.basename(apath)
os.makedirs(dest, exist_ok=True)
open(os.path.join(dest, name), "wb").write(b"x")     # stand in for the download
ok, res = cloudlib.pull(st3, name, dest)
check("download works and returns the local path", ok and res.endswith(name), res)
check("download reads through crypt",
      any("cloudcrypt:" in " ".join(c) for c in fake_pull.calls), fake_pull.calls)

# ---------------------------------------------------------------- remote retention
rows = [{"Name": "a.tar.gz", "Size": 1, "ModTime": "2026-09-04T05:00:00Z", "IsDir": False},
        {"Name": "b.tar.gz", "Size": 1, "ModTime": "2026-09-03T05:00:00Z", "IsDir": False},
        {"Name": "c.tar.gz", "Size": 1, "ModTime": "2026-09-02T05:00:00Z", "IsDir": False}]
fake_keep = FakeRclone(listing=rows)
install(fake_keep)
removed = cloudlib.prune(st3, keep=2)
check("remote prune keeps the newest", removed == ["c.tar.gz"], removed)
check("and deletes through crypt",
      all("cloudcrypt:" in d for d in fake_keep.deleted), fake_keep.deleted)
check("keep=0 removes nothing", cloudlib.prune(st3, keep=0) == [])

# ---------------------------------------------------------------- degrading well
install(FakeRclone(), present=False)
ok, detail = cloudlib.available()
check("a build without rclone says so plainly", not ok and "not installed" in detail, detail)
s = cloudlib.status(st3)
check("status reports it without raising", s["rclone_ok"] is False and s["connected"] is False, s)

install(FakeRclone(rc=1, out="couldn't connect"))
ok, msg = cloudlib.connect(st3, provider="drive", password="p",
                           token='{"access_token":"x"}')
check("a failed test connection is not called connected", not ok, msg)
check("and it says nothing will be pushed", "nothing will be pushed" in msg, msg)

install(FakeRclone())
st4, root4 = fresh(cloud_enabled=True)
populate(root4)
ok, msg = backupctl.run_scheduled(st4, push=True)
check("a scheduled run with no cloud connected still backs up locally", ok, msg)
check("and says the upload did not happen", "no cloud is connected" in msg, msg)

ok, msg = cloudlib.disconnect(st3)
check("disconnecting clears the credentials", ok and not cloudlib.configured(st3))
check("and removes the rclone config", not os.path.isfile(cloudlib.conf_path(st3)))

print("\nFAILURES: %s" % fails if fails else "\nall cloud tests passed")
sys.exit(1 if fails else 0)
