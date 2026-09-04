"""
The entrypoint has to come up and explain itself.

The failure this guards against: a container that exits because Docker is unreachable,
leaving `connection refused` - a symptom indistinguishable from a wrong port, a wrong
IP or a firewall, at the exact moment the operator has nothing else to go on.

Fixture values are synthetic throughout.
"""

import asyncio, os, sys, tempfile

from aiohttp.test_utils import TestClient, TestServer

from .app import build_app, COOKIE
from .firstrun import bootstrap

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


DOCKER_DOWN = (False, "can't reach Docker. Mount the host socket into this container.")
DOCKER_UP = (True, "Docker 27.0.0, compose plugin present")


async def run():
    # The store lives at <data root>/obelisk/settings.json, which is how the rest of
    # Obelisk locates the root from one mount.
    base = tempfile.mkdtemp()
    os.environ["OBELISK_ARK"] = os.path.join(base, "ark")
    store, _created, code = bootstrap(os.path.join(base, "obelisk"), environ={})

    # ---- Docker unreachable: the UI still serves, and says why
    client = TestClient(TestServer(build_app(store, docker=DOCKER_DOWN)))
    await client.start_server()

    r = await client.get("/setup")
    body = await r.text()
    check("setup page serves with no Docker", r.status == 200, r.status)
    check("it names the problem", "Docker not connected" in body)
    check("it says the UI still works",
          "cannot create or manage map containers" in body)
    check("it still shows the setup form", "Setup code" in body)

    r = await client.get("/healthz")
    js = await r.json()
    check("health endpoint answers", r.status == 200 and js["ok"] is True, js)
    check("health reports docker down", js["docker"] is False, js)

    r = await client.get("/", allow_redirects=False)
    check("root redirects to setup when unclaimed", r.status == 302, r.status)

    # ---- the setup code is what claims the instance
    r = await client.post("/setup", data={"code": "wrong-code"}, allow_redirects=False)
    check("a wrong code is refused", "printed in the container log" in await r.text())

    r = await client.post("/setup", data={"code": code}, allow_redirects=False)
    check("the right code is accepted", r.status == 302, r.status)
    check("and starts a session", COOKIE in r.cookies)

    r = await client.get("/admin")
    body = await r.text()
    check("settings page renders once claimed", r.status == 200 and "Save changes" in body)
    check("banner follows onto other pages", "Docker not connected" in body)
    check("timezone renders as a picker",
          '<select name="timezone"' in body and "Europe/London" in body)
    await client.close()

    # ---- Docker present: no banner
    store2, _c, code2 = bootstrap(
        os.path.join(tempfile.mkdtemp(), "obelisk"), environ={})
    client = TestClient(TestServer(build_app(store2, docker=DOCKER_UP)))
    await client.start_server()
    body = await (await client.get("/setup")).text()
    check("no banner when Docker is fine", "Docker not connected" not in body)
    await client.close()

    # ---- saving a setting through the UI
    client = TestClient(TestServer(build_app(store, docker=DOCKER_DOWN)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(store.get("admin_token"))})
    await client.post("/admin/save", data={"timezone": "Europe/London"},
                      allow_redirects=False)
    check("a saved timezone sticks", store.get("timezone") == "Europe/London",
          store.get("timezone"))

    await client.post("/admin/save", data={"timezone": "not-a-zone"},
                      allow_redirects=False)
    check("an invalid value is refused, not stored",
          store.get("timezone") == "Europe/London", store.get("timezone"))

    # ---- the cluster page: define, launch, stop, all from the UI
    from . import cluster as clusterctl
    acts = []

    class FakeDocker:
        def available(self): return (True, "Docker 27.0.0")
        def compose(self, path, proj, args, timeout=900):
            acts.append(list(args)); return 0, ""
        def compose_ps(self, path, proj, timeout=60):
            return [{"service": "island", "name": "asa_island", "state": "running",
                     "status": "Up 3 minutes", "health": "healthy"}]
        def ports_in_use(self): return set()

    clusterctl.dockerctl = FakeDocker()
    store.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
    store.patch({"admin_password": "synthetic-pw", "cluster_id": "uitest",
                 "host_ram_gb": 256, "maps": "island"})
    store.save()

    r = await client.get("/admin/cluster")
    body = await r.text()
    check("cluster page renders", r.status == 200 and "Plan" in body, r.status)
    check("it shows the port/RAM plan", "Game" in body and "RCON" in body)
    check("it offers a launch button", "/admin/launch" in body)

    r = await client.post("/admin/maps", data={"maps": ["island", "ragnarok"]},
                          allow_redirects=False)
    check("map selection saves from the UI", store.get("maps") == "island,ragnarok",
          store.get("maps"))

    r = await client.post("/admin/maps", data={"preset": "single"}, allow_redirects=False)
    check("a preset ticks the boxes", store.get("maps") == "island", store.get("maps"))

    tmp_compose = os.path.join(tempfile.mkdtemp(), "compose.yaml")
    _rp = clusterctl.compose_path
    _re = clusterctl.layout.ensure_ark
    _ro = clusterctl.layout.ensure_obelisk
    clusterctl.compose_path = lambda st: tmp_compose
    clusterctl.layout.ensure_ark = lambda root, keys=(), makedirs=None: []
    clusterctl.layout.ensure_obelisk = lambda root, makedirs=None: []
    r = await client.post("/admin/launch")
    body = await r.text()
    check("launch runs from the UI", any(a[:2] == ["up", "-d"] for a in acts), acts)
    check("the page reports the result", "Cluster up" in body, body[:300])
    check("a running cluster shows its services", "asa_island" in body or "island" in body)

    acts.clear()
    r = await client.post("/admin/stop")
    body = await r.text()
    check("stop runs from the UI", acts and acts[-1] == ["down"], acts)
    check("stop says saves are safe", "untouched" in body)
    clusterctl.compose_path, clusterctl.layout.ensure_ark = _rp, _re
    clusterctl.layout.ensure_obelisk = _ro

    r = await client.get("/admin/cluster", allow_redirects=False)
    check("cluster page needs a session", r.status in (200, 302))

    # ---- backups from the UI
    from . import backup as backupctl
    from . import layout as layoutmod
    # Saves come from the Ark folder; the definition from Obelisk's own.
    broot = layoutmod.ark_root_of(store)
    layoutmod.ensure_ark(broot, ["island"])
    layoutmod.ensure_obelisk(layoutmod.root_of(store))
    store.save()
    sd = os.path.join(broot, "shared", "SavedArks", "TheIsland_WP")
    os.makedirs(sd, exist_ok=True)
    open(os.path.join(sd, "TheIsland_WP.ark"), "wb").write(bytes(1024))
    store.patch({"maps": "island", "backup_keep": 2, "backup_flush": False})

    r = await client.get("/admin/backups")
    body = await r.text()
    check("backups page renders", r.status == 200 and "Back up now" in body, r.status)
    check("it warns the archive holds secrets", "admin/RCON password" in body)
    check("it says the game install is left out", "re-downloads" in body)
    check("no backups yet is a normal state", "No backups yet" in body)

    r = await client.post("/admin/backup")
    body = await r.text()
    check("backup runs from the UI", "verified readable" in body, body[:400])
    check("the new archive is listed", "obelisk-backup-" in body)
    check("one backup exists on disk", len(backupctl.listing(store)) == 1,
          backupctl.listing(store))

    await client.post("/admin/backup")
    await client.post("/admin/backup")
    check("retention applies to UI backups too", len(backupctl.listing(store)) == 2,
          backupctl.listing(store))

    check("Backups is in the nav", "/admin/backups" in body)

    # ---- the cloud page, including the connect step the owner drives
    from . import cloud as cloudctl
    import hashlib as _h
    rcalls = []

    def fake_rclone(args, timeout=300, input_text=None):
        rcalls.append(list(args))
        if "version" in args: return 0, "rclone v1.66.0"
        if "obscure" in args: return 0, _h.sha256(args[-1].encode()).hexdigest()[:32]
        if "lsjson" in args: return 0, "[]"
        return 0, ""

    cloudctl._run = fake_rclone
    cloudctl.shutil.which = lambda n: "/usr/bin/rclone"

    r = await client.get("/admin/cloud")
    body = await r.text()
    check("cloud page renders", r.status == 200, r.status)
    check("it offers the providers", "Google Drive" in body and "Backblaze B2" in body)
    check("it says the sign-in is the owner's to do", "yours to do" in body)
    check("it names the exact command to run", "rclone authorize drive" in body)
    check("it warns the passphrase cannot be recovered", "Lose it" in body)
    check("Cloud is in the nav", "/admin/cloud" in body)

    r = await client.post("/admin/cloud/connect",
                          data={"provider": "drive", "password": "",
                                "token": "x", "path": "obelisk-backups"})
    check("connecting without a passphrase is refused in the UI",
          "passphrase is required" in await r.text())

    r = await client.post("/admin/cloud/connect",
                          data={"provider": "drive", "password": "synthetic-phrase",
                                "token": '{"access_token":"synthetic"}',
                                "path": "obelisk-backups"})
    body = await r.text()
    check("connecting works from the UI", "Connected to Google Drive" in body, body[:400])
    check("the page then shows the connected state", "Disconnect" in body)
    check("the passphrase is never echoed back", "synthetic-phrase" not in body)
    check("the token is never echoed back", "access_token" not in body)

    r = await client.post("/admin/cloud/push")
    check("upload runs from the UI",
          any("copy" in c and any("cloudcrypt:" in x for x in c) for c in rcalls), rcalls[-1])

    r = await client.post("/admin/cloud/disconnect")
    check("disconnect works from the UI", "Connect and test" in await r.text())

    # ---- a save must not be blocked by fields the user cannot change
    # The live failure: the settings form rendered status_port and appdata read-only but
    # still submitted them, and the save rejected the whole request because those keys
    # are container-set. Changing a port saved nothing, and the error named two fields
    # the user had never touched. Nobody could save anything from this page.
    store.patch({"game_port_base": 7777, "rcon_port_base": 27020})
    store.save()
    payload = {
        "game_port_base": "7877",
        "rcon_port_base": "27920",
        "admin_password": "synthetic-admin-pw",
        # exactly what the browser posted back, unchanged, from the read-only fields
        "status_port": str(store.get("status_port")),
        "appdata": str(store.get("appdata")),
    }
    r = await client.post("/admin/save", data=payload, allow_redirects=False)
    check("a save carrying container-set fields is accepted, not rejected",
          r.status == 302, "%s %s" % (r.status, (await r.text())[:200]))
    check("the ports actually persisted",
          store.get("game_port_base") == 7877 and store.get("rcon_port_base") == 27920,
          (store.get("game_port_base"), store.get("rcon_port_base")))
    check("the admin password persisted too",
          str(store.get("admin_password")) == "synthetic-admin-pw")
    check("and the cluster is no longer blocked on it",
          not any(b["key"] == "admin_password" for b in store.readiness()),
          store.readiness())

    body = await (await client.get("/admin")).text()
    check("container-set fields are disabled, so a fresh page never posts them",
          body.count("readonly disabled") >= 2, body.count("readonly disabled"))
    check("editable fields are not disabled",
          'name="max_players"' in body and
          'name="max_players" value="%s" readonly disabled' % store.get("max_players")
          not in body)

    before = str(store.get("admin_token"))
    await client.post("/admin/save", data={"admin_password": ""}, allow_redirects=False)
    check("a blank password means 'leave it alone'", str(store.get("admin_token")) == before)
    await client.close()


asyncio.run(run())
print("\nFAILURES: %s" % fails if fails else "\nall app tests passed")
sys.exit(1 if fails else 0)
