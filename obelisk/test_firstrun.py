"""First-run behaviour: starting the container is the whole setup.
   python3 -m obelisk.test_firstrun"""
# Fixture values here are deliberately synthetic - no real cluster's ids, times or
# timezone belong in a public repo. The one exception is mod id 929110: the product
# checks that specific public id to enforce stacking-mod load order, so a test of
# that rule has to use the real one.
import json, os, shutil, sys, tempfile

from .firstrun import bootstrap
from .settings import Store

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)) if detail and not cond else ""))
    if not cond: fails.append(name)

# ---- a brand-new install needs nothing but the container starting
d = tempfile.mkdtemp()
store, created, code = bootstrap(d, environ={})
check("creates its own store", created and os.path.isfile(os.path.join(d, "settings.json")))
check("issues a one-time setup code", bool(code) and len(code) >= 10, code)
check("the code is the admin token", store.get("admin_token") == code)
check("store is written 0600", oct(os.stat(store.path).st_mode)[-3:] == "600")
check("nothing had to be pre-filled by hand", store.get("session_prefix") == "")
check("says what is still needed", [b["key"] for b in store.readiness()] == ["admin_password"],
      store.readiness())

# ---- restarting must not re-issue a code or clobber anything
store.patch({"admin_password": "chosen", "session_prefix": "MINE"}); store.save()
store2, created2, code2 = bootstrap(d, environ={})
check("second start does not re-create", not created2)
check("second start issues no new code", code2 is None)
check("settings survive a restart", store2.get("session_prefix") == "MINE")
check("admin token is unchanged", store2.get("admin_token") == code)
shutil.rmtree(d)

# ---- upgrading an existing env-configured stack carries settings across
d = tempfile.mkdtemp()
legacy = {"SESSION_PREFIX": "OLD", "MOD_IDS": "929110,222222", "MAX_PLAYERS": "42",
          "SERVER_ADMIN_PASSWORD": "fromenv", "CLUSTER_ID": "oldcluster",
          "WIPE_TIMES": "03:15", "TZ": "Etc/UTC"}
store, created, code = bootstrap(d, environ=legacy)
check("imports an existing stack's settings", store.get("session_prefix") == "OLD")
check("imports the mod list", store.get("mod_ids") == "929110,222222")
check("imports a number correctly", store.get("max_players") == 42)
check("imports the admin password", store.get("admin_password") == "fromenv")
check("imports a setting Obelisk now owns", store.get("wipe_times") == "03:15")
check("an imported cluster needs nothing more", store.readiness() == [], store.readiness())
shutil.rmtree(d)

# ---- one bad legacy value must not stop the container booting
d = tempfile.mkdtemp()
store, created, code = bootstrap(d, environ={"SESSION_PREFIX": "OK", "MAX_PLAYERS": "not-a-number"})
check("a bad legacy value doesn't block startup", store.get("session_prefix") == "OK")
check("the bad value falls back to the default", store.get("max_players") == 70)
shutil.rmtree(d)

# ---- an operator who set their own token keeps it, and gets no setup code
d = tempfile.mkdtemp()
s = Store(os.path.join(d, "settings.json")); s.patch({"admin_token": "mine"}); s.save()
store, created, code = bootstrap(d, environ={})
check("an existing admin token is left alone", store.get("admin_token") == "mine" and code is None)
shutil.rmtree(d)

# ---- install settings follow the container, not the store
d = tempfile.mkdtemp()
store, _, _ = bootstrap(d, environ={"APPDATA": "/mnt/tank/ark", "STATUS_PORT": "9090"})
check("boot takes install settings from the container", store.get("appdata") == "/mnt/tank/ark"
      and store.get("status_port") == 9090)
# recreate the container with a different mount - the store must follow it
store2, _, _ = bootstrap(d, environ={"APPDATA": "/mnt/cache/ark", "STATUS_PORT": "9090"})
check("re-creating the container moves the stored path", store2.get("appdata") == "/mnt/cache/ark")
# ...but a boot with nothing set must not blank what was there
store3, _, _ = bootstrap(d, environ={})
check("a boot with no env keeps the last known mount", store3.get("appdata") == "/mnt/cache/ark")
shutil.rmtree(d)

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
