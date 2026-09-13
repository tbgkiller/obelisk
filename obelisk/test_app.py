"""
The entrypoint has to come up and explain itself.

The failure this guards against: a container that exits because Docker is unreachable,
leaving `connection refused` - a symptom indistinguishable from a wrong port, a wrong
IP or a firewall, at the exact moment the operator has nothing else to go on.

Fixture values are synthetic throughout.
"""

import asyncio, io, os, sys, tempfile

from aiohttp.test_utils import TestClient, TestServer

from .app import build_app, COOKIE
from . import ui
from .settings import Store
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

    # The cookie used to have no lifetime, so it was thrown away when the browser
    # closed and the setup prompt came back for reasons that looked like nothing had
    # happened - with the code needed to answer it printed only once, long ago. That is
    # precisely how the owner got locked out of his own cluster.
    from .app import COOKIE_DAYS
    _c = r.cookies[COOKIE]
    check("the session cookie has a real lifetime, not browser-session",
          int(_c["max-age"]) == COOKIE_DAYS * 24 * 3600, dict(_c))
    check("it is a month or so, not a day", COOKIE_DAYS >= 14, COOKIE_DAYS)
    check("and it stays httponly and same-site",
          _c["httponly"] and _c["samesite"].lower() == "lax", dict(_c))
    check("setup is now marked finished, so the code stops printing on boot",
          store.data.get("setup_done") is True, store.data.get("setup_done"))

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
        def existing_containers(self, timeout=30): return {}

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

    # a second click landing before the first finishes must not run a second launch
    acts.clear()
    import asyncio as _aio
    both = await _aio.gather(client.post("/admin/launch"), client.post("/admin/launch"))
    ups = [a for a in acts if a[:2] == ["up", "-d"]]
    check("a double click runs the launch once, not twice", len(ups) <= 1, acts)
    bodies = [await r.text() for r in both]
    check("and the ignored one says so, rather than looking like a no-op",
          any("ignored rather than run twice" in b for b in bodies) or len(ups) == 1,
          [b[:120] for b in bodies])

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

    # The button starts the archive and returns immediately - it used to run the whole
    # thing inside the request, which held the event loop for the length of the
    # compression and took the chat relay, Discord and this very page down with it.
    async def run_a_backup():
        r_ = await client.post("/admin/backup", allow_redirects=False)
        assert r_.status in (302, 303), r_.status
        for _ in range(200):
            j = await (await client.get("/admin/backup/status")).json()
            if j["state"] == "done":
                return j
            await asyncio.sleep(0.05)
        raise AssertionError("backup never finished")

    j = await run_a_backup()
    check("the button returns at once rather than holding the request",
          True)          # asserted by allow_redirects=False above
    check("backup runs from the UI", j["ok"] and "verified readable" in j["message"],
          j["message"][:400])
    check("the progress endpoint reports it finished", j["phase"] == "done", j)
    check("one backup exists on disk", len(backupctl.listing(store)) == 1,
          backupctl.listing(store))

    body = await (await client.get("/admin/backups")).text()
    check("the new archive is listed", "obelisk-backup-" in body)

    await run_a_backup()
    await run_a_backup()
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

    # Disconnecting deletes the passphrase, and that passphrase is the only thing that
    # can read an archive already off-site. So the page asks for the word to be typed,
    # and - because a page can be bypassed by a bare POST, which is exactly what the
    # line below used to be - the refusal has to hold at the route too.
    body = await (await client.get("/admin/cloud")).text()
    check("the connected page spells out what disconnecting destroys",
          "permanently unreadable" in body, body[-1200:])
    check("and asks for the word to be typed", 'name=confirm' in body, body[-1200:])
    check("and does not show the passphrase while doing it",
          "synthetic-phrase" not in body)

    r = await client.post("/admin/cloud/disconnect")
    check("a bare POST with no confirmation is refused",
          cloudctl.configured(store), "the vault was cleared without confirmation")
    check("and the page says why rather than looking like it worked",
          "was not confirmed" in await r.text())

    r = await client.post("/admin/cloud/disconnect", data={"confirm": "yes"})
    check("a wrong word is refused too", cloudctl.configured(store))

    r = await client.post("/admin/cloud/disconnect", data={"confirm": "disconnect  "})
    body = await r.text()
    check("the typed word works, and case and spacing are forgiven",
          not cloudctl.configured(store), body[:300])
    check("and the page returns to the connect form", "Connect and test" in body)
    check("and it is honest that the off-site copies can no longer be read",
          "no longer be decrypted" in body, body[:600])

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

# ---- the relay is wired from the store, Discord included
#
# The maps were fixed once already: the relay read SERVERS from the environment, so a
# normally-installed Obelisk relayed nothing. The Discord half had the identical bug and
# outlived the fix - a token typed into the admin page sat in settings.json while the bot
# kept reading an empty env var, and chat relayed map to map with no Discord and no
# complaint. Three of these are named differently on the two sides, which is precisely
# how a near-miss survives review.
from . import app as appmod
from . import cluster as clusterctl_t


class _Bot:
    SERVERS, RCON_PASSWORD, CLUSTER_NAME = {}, "", "Cluster"
    DISCORD_TOKEN, DISCORD_INVITE = "", ""
    DISCORD_CHANNEL_ID = TRIBELOG_CHANNEL_ID = ADMIN_CHANNEL_ID = ADMIN_ROLE_ID = 0
    JOIN_LEAVE = WELCOME_ENABLED = False
    STATUS_PORT = 8088
    CLUSTER_CONFIGURED = False


_d = tempfile.mkdtemp()
_st = Store(os.path.join(_d, "settings.json")).load()
_st.patch({"appdata": "/srv/ark", "status_port": 8088}, source="install")
_st.patch({"maps": "island,center", "admin_password": "synthetic-pw",
           "cluster_id": "relaytest", "cluster_name": "Relay Test",
           "discord_token": "synthetic-token", "discord_channel_id": "111100000000000001",
           "discord_tribelog_channel_id": "111100000000000002",
           "discord_admin_channel_id": "111100000000000003",
           "discord_invite": "https://discord.gg/synthetic", "join_leave": True})

_saved_running = clusterctl_t.running_instances
clusterctl_t.running_instances = lambda store: [("The Island", "asa-relaytest-island", 27020),
                                                ("The Center", "asa-relaytest-center", 27021)]
_b = _Bot()
_ok = appmod._wire_relay(_st, _b)
clusterctl_t.running_instances = _saved_running

check("the relay is configured from the store", _ok and _b.CLUSTER_CONFIGURED)
check("every running map is addressed by container name",
      _b.SERVERS == {"The Island": ("asa-relaytest-island", 27020),
                     "The Center": ("asa-relaytest-center", 27021)}, _b.SERVERS)
check("the RCON password comes from the store", _b.RCON_PASSWORD == "synthetic-pw")
check("and so does the Discord token", _b.DISCORD_TOKEN == "synthetic-token")
check("the relay channel id is carried across as a number",
      _b.DISCORD_CHANNEL_ID == 111100000000000001, _b.DISCORD_CHANNEL_ID)
check("the tribe log channel too, under the name the relay uses",
      _b.TRIBELOG_CHANNEL_ID == 111100000000000002, _b.TRIBELOG_CHANNEL_ID)
check("and the admin channel, likewise renamed",
      _b.ADMIN_CHANNEL_ID == 111100000000000003, _b.ADMIN_CHANNEL_ID)
check("the invite reaches the in-game !discord command",
      _b.DISCORD_INVITE == "https://discord.gg/synthetic", _b.DISCORD_INVITE)
check("join/leave announcements follow the setting", _b.JOIN_LEAVE is True)
# Inside Obelisk the web UI serves the status page, on the port the relay's own copy of
# it would otherwise bind. Two servers, one socket, and the process dies the first time
# the relay runs at all.
check("the relay's standalone status page stands down inside Obelisk",
      _b.STATUS_PORT == 0, _b.STATUS_PORT)

# Blank is the ordinary case - no Discord, map-to-map only - and must not raise.
_st.patch({"discord_token": "", "discord_channel_id": "", "discord_admin_channel_id": ""})
clusterctl_t.running_instances = lambda store: [("The Island", "asa-relaytest-island", 27020)]
_b2 = _Bot()
_ok2 = appmod._wire_relay(_st, _b2)
clusterctl_t.running_instances = _saved_running
check("a cluster with no Discord still wires its maps", _ok2 and _b2.SERVERS)
check("and leaves the channel ids at zero rather than crashing",
      _b2.DISCORD_CHANNEL_ID == 0 and _b2.ADMIN_CHANNEL_ID == 0)
check("and the token empty", _b2.DISCORD_TOKEN == "")


# ---- restore: the archive name comes from a form, so it is not a path
#
# "Which archive" arrives as a posted string. Anything that turns a posted string into
# a filesystem path has to be told the answer can only be inside one folder, or the
# answer becomes "any file on the host that tarfile will open".
from . import restore as _restoremod

_rd = tempfile.mkdtemp()
_rstore = Store(os.path.join(_rd, "settings.json")).load()
_rstore.patch({"admin_token": "t"})
_rapp = build_app(_rstore, docker=DOCKER_UP)
_rroutes = {r.resource.canonical for r in _rapp.router.routes()}
check("the restore page has a route", "/admin/restore" in _rroutes)
check("inspecting an archive has a route", "/admin/restore/inspect" in _rroutes)
check("and running one has its own", "/admin/restore/run" in _rroutes)

_appsrc = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                  encoding="utf-8").read()
check("the posted archive name is stripped to a basename",
      "os.path.basename" in _appsrc.split("_archive_path")[1][:400], "no basename call")
check("and the result is confined to the backups folder",
      "startswith(base" in _appsrc.split("_archive_path")[1][:400], "no prefix check")
check("a restore never runs alongside another cluster action",
      "cluster_busy" in _appsrc.split("async def restore_run")[1][:1200])
_runsrc = _appsrc.split("async def restore_run")[1].split("# ---- cloud")[0]
check("and it runs off the event loop like the backup does",
      "to_thread" in _runsrc, _runsrc[-200:])

# Phase 1 is worlds-only: the definition restore is not wired up at all yet.
check("Phase 1 does not restore the cluster definition",
      "definition" not in _runsrc.lower())

# The gap this closes: restore_run passed verify=None, so a restore started the map and
# called it done. Starting a container and it serving a world are minutes apart.
check("the restore actually verifies afterwards rather than assuming",
      "verify=verify_after" in _runsrc, "verify is not wired")
check("and it waits for the map to be healthy before checking",
      "wait_healthy" in _runsrc, _runsrc[:200])
check("the six gates are the same ones the migration used",
      "verify_instance" in _runsrc)
check("a restore reports progress while it runs",
      "/admin/restore/status" in _appsrc)
check("and only one runs at a time",
      'rjob["state"] == "running"' in _runsrc)
check("restore.py says the definition is deliberately out of scope",
      "does not restore the cluster definition"
      in io.open(_restoremod.__file__, encoding="utf-8").read())

# ---- an admin should be able to follow this from Discord and the log alone
#
# The bar: a normal user has the Unraid Docker page, the Obelisk UI and Discord. They do
# not have a shell and they do not have us. So every significant action has to say what
# it is doing somewhere they can actually see.
from . import announce as _ann

_mainsrc = _appsrc.split("async def main")[1]
_appsrc_all = _appsrc          # the whole module, for things main() delegates
check("secrets are registered before anything can announce",
      "guard_secrets" in _mainsrc, "not registered at boot")
check("and before the relay that would carry them is started",
      _mainsrc.index("guard_secrets") < _mainsrc.index("_wire_relay"),
      "registered too late")
check("and they come from the backup module's own list of secrets",
      "SECRET_KEYS" in _appsrc)

for _ev in ("backup.start", "restore.start", "restore.phase"):
    check("%s is announced" % _ev, '"%s"' % _ev in _appsrc, _ev)
check("a backup announces its outcome either way",
      '"backup.done" if ok else "backup.failed"' in _appsrc)
check("so does a restore",
      '"restore.done" if ok else "restore.failed"' in _appsrc)
check("a failed restore announces at error level, not buried at info",
      'level="info" if ok else "error"' in _appsrc)
check("launching and stopping the cluster are announced too",
      "cluster.%s" in _appsrc)

# the relay is what carries them to Discord
_botsrc = io.open(os.path.join(os.path.dirname(__file__), "bot.py"),
                  encoding="utf-8").read()
check("the relay drains the announcement queue", "announce_loop" in _botsrc)
check("and it is actually started", "relay.announce_loop()" in _botsrc)
check("posting failures never kill the loop",
      "could not post announcement" in _botsrc)
check("it uses the admin channel, not the public one",
      "admin_send" in _botsrc.split("async def announce_loop")[1][:800])

# ---- the manager joins its cluster network on boot, not only on launch
#
# Found by deploying the way a user does. An Apply Update recreates the container from
# the template, which knows nothing about the cluster network - so the relay came back
# on bridge alone, resolving none of ten maps, while logging that it covered all ten.
check("the network join happens on start, not only inside launch()",
      "join_network_if_running" in _mainsrc, "not called at boot")
check("and before the relay is wired to anything",
      _mainsrc.index("join_network_if_running") < _mainsrc.index("_wire_relay"),
      "joined too late to help")
check("failing to join is not a reason to refuse to start",
      "could not join the cluster network" in _mainsrc)

check("coverage is measured, not counted from the container list",
      "clusterctl.reachable" in _mainsrc, "no reachability probe")
check("and measured off the event loop, since it is ten RCON round trips",
      "to_thread(clusterctl.reachable" in _mainsrc)
check("a relay that cannot reach its maps says so at error level",
      'log.error("relay reaches %d of %d' in _mainsrc)
# These two used to look inside main() for the announcement. Both announcements now
# live in _say_coverage, which boot and the re-wire loop share so the count and the
# wording cannot disagree - so the assertion follows them there. The behaviour is what
# matters and it is checked directly further down, by calling the helper and reading the
# event it emits.
check("and announces it, so an admin sees it in Discord rather than a log",
      '"relay.degraded"' in _appsrc_all)
check("full coverage is announced too, so silence is not the only good news",
      '"relay.up"' in _appsrc_all)
check("the old unconditional claim is gone",
      'log.info("relay covering %d map(s): %s"' not in _appsrc,
      "still claims coverage without checking")

# ---- version visibility, because the checker people rely on got it wrong
#
# Unraid compared a stale digest against itself and said "up to date" while a fix sat
# published. Not an error - a confident wrong answer, the one shape a user cannot act on.
from . import version as _ver

check("the running version is read at boot", "versionctl.running()" in _mainsrc)
check("and remembered, so a change can be noticed",
      'store.data["last_version"]' in _mainsrc)
check("landing on a new version is announced",
      '"obelisk.updated"' in _appsrc)
check("a newer published image is announced too",
      '"obelisk.update_available"' in _appsrc)
check("the check runs in the background, not on page load",
      "version_watch" in _appsrc and "to_thread(versionctl.status)" in _appsrc)
check("and Obelisk never applies its own update",
      "docker pull" not in _appsrc.lower())

_vh = ui.render_version({"commit": "302b121abc", "digest": "sha256:aaaa1111bbbb",
                         "published": "sha256:cccc2222dddd", "update_available": True,
                         "problem": ""})
check("an available update is shown as a problem, not a footnote",
      "class=problem" in _vh and "An update is available" in _vh, _vh[:200])
check("and points at the Unraid button rather than offering its own",
      "Apply Update" in _vh and "Force Update" in _vh)
check("it reassures that saves survive", "saves are untouched" in _vh)

_vh2 = ui.render_version({"commit": "x", "digest": "sha256:a", "published": "sha256:a",
                          "update_available": False, "problem": ""})
check("up to date says so plainly", "Up to date" in _vh2)

_vh3 = ui.render_version({"commit": "x", "digest": "sha256:a", "published": None,
                          "update_available": None,
                          "problem": "could not get a registry token: 401"})
check("a failed check is never shown as up to date", "Up to date" not in _vh3, _vh3)
check("it says we do not know", "we do not" in _vh3, _vh3)

# ---- the from-scratch INI renderer must stay gone
#
# generate_ini() rendered GameUserSettings.ini and Game.ini from the schema and headed
# them "do not edit". Obelisk models 139 of this cluster's 172 keys; a from-scratch
# renderer deletes the rest, including a mod's whole section. It was never wired to
# anything - which is the only reason those keys survived being migrated - and it sat in
# settings.py looking like the obvious function for a save path to call.
from . import settings as _settingsmod

check("there is no from-scratch INI renderer to wire up by accident",
      not hasattr(_settingsmod, "generate_ini"))
_src = io.open(_settingsmod.__file__, encoding="utf-8").read()
check("and settings.py says not to add one back",
      "no generate_ini()" in _src and "ini.merge_file" in _src)
check("nothing anywhere calls one",
      not any("generate_ini" in io.open(os.path.join(os.path.dirname(__file__), f),
                                        encoding="utf-8").read()
              for f in os.listdir(os.path.dirname(__file__))
              if f.endswith(".py") and not f.startswith("test_")
              and f != "settings.py"))

# ---- the entrypoint runs last, or it runs too early
#
# `if __name__ == "__main__": asyncio.run(main())` sat above _wire_relay(), so main()
# called a name Python had not bound yet. Importing the module was fine, every test
# passed, and the container exited on NameError - but only once a cluster was actually
# running for the relay to pick up, which is the one path no test had exercised and the
# one that matters in production. Nothing about the source looks wrong; the order is the
# whole of it. So the order is what gets asserted.

# ---- every tab in the nav has a route behind it
#
# The Mods page existed - render_mods(), the whole mods module, a nav link pointing at
# it - and no route was ever registered, so the tab 404'd. Nothing catches that: the
# renderer is tested, the module is tested, and the wiring between them is the one thing
# neither of them can see. So the nav itself is the fixture.
import re as _re
from . import ui as _ui

_nav = _re.findall(r'<a href="([^"]+)"', _ui.page("t", "", ""))
_bd = tempfile.mkdtemp()
_bstore = Store(os.path.join(_bd, "settings.json")).load()
_bstore.patch({"admin_token": "t"})
_built = build_app(_bstore, docker=DOCKER_UP)
_app_routes = {r.resource.canonical for r in _built.router.routes()}
for _href in _nav:
    check("the nav's %s tab has a route" % _href, _href in _app_routes,
          sorted(_app_routes))

check("and the mods form can post back", "/admin/mods" in _app_routes)
check("the backup progress endpoint exists", "/admin/backup/status" in _app_routes)

# measure() is the half of the mods module that was missing, which is why the page
# could be rendered but never served.
from . import mods as _mods
check("measuring an absent mods folder is empty, not an error",
      _mods.measure(os.path.join(tempfile.mkdtemp(), "nope")) == {})
_mroot = tempfile.mkdtemp()
os.makedirs(os.path.join(_mroot, "929110", "sub"))
with open(os.path.join(_mroot, "929110", "sub", "a.pak"), "wb") as fh:
    fh.write(b"x" * 4096)
os.makedirs(os.path.join(_mroot, "notamod"))
_m = _mods.measure(_mroot)
check("a mod folder is measured by id", list(_m) == ["929110"], _m)
check("with its file count and size", _m["929110"]["files"] == 1 and _m["929110"]["kb"] == 4, _m)
check("and non-numeric folders are not mistaken for mods", "notamod" not in _m, _m)

import ast as _ast
import io as _io


# ---- coverage is described in one place, and counts agree with their nouns
#
# It announced "Chat relay is up and reaching all 1 maps" - true, and it read like a bug
# because it was one: the relay had wired at boot to the only map running during a
# staggered restart, and never looked again.
from . import announce as _ann
from . import app as _app


def _coverage(reachable, total, bad=()):
    while _ann.pop_all(limit=100):
        pass
    _app._say_coverage(reachable, total, list(bad))
    got = _ann.pop_all(limit=10)
    return got[0] if got else {}


_one = _coverage(1, 1)
check("one map is not 'all 1 maps'", "all 1 maps" not in _one.get("text", ""),
      _one.get("text"))
check("and it reads as a sentence", "the only map" in _one.get("text", ""),
      _one.get("text"))

_tenc = _coverage(10, 10)
check("ten maps is plural", "all 10 maps" in _tenc.get("text", ""), _tenc.get("text"))
check("and is reported as up", _tenc.get("event") == "relay.up", _tenc.get("event"))

_part = _coverage(7, 10, [("Genesis", "timeout"), ("Astraeos", "timeout")])
check("partial coverage is degraded, not up",
      _part.get("event") == "relay.degraded", _part.get("event"))
check("with the right count and noun", "7 of 10 maps" in _part.get("text", ""),
      _part.get("text"))
check("and names what it cannot reach", "Genesis" in _part.get("fields", ""),
      _part.get("fields"))

_onebad = _coverage(0, 1, [("The Island", "timeout")])
check("a single unreachable map is singular too",
      "0 of 1 map" in _onebad.get("text", "")
      and "0 of 1 maps" not in _onebad.get("text", ""), _onebad.get("text"))

check("the relay re-checks its coverage rather than wiring once and hoping",
      callable(getattr(_app, "relay_watch", None)))
_appnow = open(_app.__file__, encoding="utf-8").read()
check("and the boot path actually starts that loop",
      "relay_watch(store, bot)" in _appnow, "relay_watch is never scheduled")
check("boot and the loop describe coverage through the same helper, so the count and "
      "the wording cannot disagree", _appnow.count("_say_coverage(") >= 3,
      _appnow.count("_say_coverage("))


# ---- this file could not fail
#
# There was no summary and no exit, so every check here printed PASS or FAIL and the
# process returned 0 either way. A test that cannot fail the build is a test that is
# not being run, however many lines of it there are.
print("\nFAILURES: %s" % fails if fails else "\nall app tests passed")
sys.exit(1 if fails else 0)
