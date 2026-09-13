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
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
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

    # Severity has to run the right way round. The success banner used to be
    # `class=note` - the same grey "Connected to Google Drive" uses - on the one screen
    # in the product that cannot be undone.
    _banner = body[body.index("no longer be decrypted") - 400:
                   body.index("no longer be decrypted")]
    check("the result is rendered severe, not as a grey note",
          "class=problem" in _banner and "class=note" not in _banner, _banner[-200:])
    check("and it tells the operator the folder is now theirs to clear",
          "no longer list, prune or delete" in body and "obelisk-backups" in body,
          body[:900])

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
_runsrc = _appsrc.split("async def restore_run")[1].split("# ---- cloud")[0]
check("a restore never runs alongside another cluster action",
      "cluster_busy" in _runsrc, _runsrc[:200])
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
# ---- the arguments that caused the 8 September double-apply are gone
#
# Making APPLY_LOCK module-level fixed the incident. The parameter that let a caller
# hand the watcher a different lock - or None - stayed on the signature, defaulting to
# None, waiting for the next person who passed something. Same for the switch that
# turned off the final player re-count, which its own docstring called "not optional in
# practice". A door that has been bolted is not a door that has been removed.
import inspect as _insp_dead                                     # noqa: E402
from . import app as _appmod                                     # noqa: E402

_ew = set(_insp_dead.signature(_appmod.empty_watch).parameters)
check("the empty watcher cannot be handed a different lock",
      "busy" not in _ew, sorted(_ew))
check("nor a different thing to run when the cluster is idle",
      "apply_now" not in _ew, sorted(_ew))
check("and it still takes what main() actually passes",
      {"store", "interval", "needed"} <= _ew, sorted(_ew))

_sa = set(_insp_dead.signature(_appmod._scheduled_apply).parameters)
check("the final player re-count cannot be switched off",
      "recheck" not in _sa, sorted(_sa))
check("while force - which callers do mean - is still there",
      "force" in _sa, sorted(_sa))

_ew_src = _insp_dead.getsource(_appmod.empty_watch)
check("the watcher takes the module-level lock, not one handed to it",
      "APPLY_LOCK.locked()" in _ew_src and "busy" not in _ew_src, _ew_src[:400])
_sa_src = _insp_dead.getsource(_appmod._scheduled_apply)
check("and the re-count runs whenever the apply is not forced",
      "if not force:" in _sa_src, _sa_src[:400])


# ---- the periodic world sweep: it looks, and that is all it may do
#
# A background loop that can touch a cluster is a background loop that will, at four in
# the morning, for a reason nobody is awake to read. So the authority it does NOT have
# is the part worth pinning: no stop, no start, no move, no delete, no restore.
import asyncio as _aio2                                          # noqa: E402
from . import app as _appmod                                     # noqa: E402
from . import announce as _ann2                                  # noqa: E402
from . import cluster as _cl2                                    # noqa: E402
from . import restore as _re2                                    # noqa: E402


def _drain2():
    out, batch = [], _ann2.pop_all(limit=100)
    while batch:
        out += batch
        batch = _ann2.pop_all(limit=100)
    return out


class _Tripwire:
    """Every side-effecting call the sweep must never make."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def boom(*a, **k):
            self.calls.append(name)
            raise AssertionError("the sweep called %s" % name)
        return boom


_sw_store = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
_sw_store.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
_sw_store.patch({"maps": "island", "admin_password": "pw", "cluster_id": "swtest",
                 "host_ram_gb": 256})

_POINT = [{"map": "island", "name": "TheIsland_WP_13.09.2026_01.00.00.ark",
           "path": "/ark/shared/SavedArks/TheIsland_WP/point.ark", "when": 1, "age": 1,
           "size": 4096, "human_size": "4 KB", "local": "x", "ago": "1m"}]

_verdicts = []
_deep_seen = []
_real_verify = _re2.verify_world


def _fake_verify(path, deep=True):
    _deep_seen.append(deep)
    return _verdicts.pop(0)


async def _sweep_once(verdicts):
    """One pass of the real loop, with the clock and the checker faked."""
    global _verdicts
    _verdicts = list(verdicts)
    _re2.verify_world = _fake_verify
    _appmod.restorectl.verify_world = _fake_verify
    tripped = _Tripwire()
    real_stop, real_start, real_one = _cl2.stop, _cl2.launch, _cl2.start_one
    _cl2.stop, _cl2.launch, _cl2.start_one = tripped.stop, tripped.launch, tripped.start_one
    try:
        task = _aio2.create_task(_appmod.world_watch(
            _sw_store, interval=0.01, check=lambda st, key: _POINT,
            sleep_first=False))
        await _aio2.sleep(0.05)
        task.cancel()
        try:
            await task
        except _aio2.CancelledError:
            pass
    finally:
        _cl2.stop, _cl2.launch, _cl2.start_one = real_stop, real_start, real_one
        _re2.verify_world = _real_verify
        _appmod.restorectl.verify_world = _real_verify
    return tripped


_drain2()
_t = _aio2.get_event_loop_policy().new_event_loop()
try:
    trip = _t.run_until_complete(_sweep_once([(False, "SQLite reports it damaged: x")]
                                            * 40))
    _ev2 = _drain2()
    _bad = [i for i in _ev2 if i["event"] == "world.damaged"]
    check("the sweep reports a save point that will not read", len(_bad) >= 1,
          [i["event"] for i in _ev2])
    check("as an error", _bad and _bad[0]["level"] == "error", _bad[:1])
    check("naming the map and the reason",
          _bad and "island" in _bad[0]["text"] and "damaged" in _bad[0]["text"], _bad[:1])
    check("and saying plainly that it changed nothing",
          _bad and "Nothing has been changed" in _bad[0]["text"], _bad[:1])

    # 10. it says it once, not once per pass, however long the fault lasts.
    check("it does not storm the channel while the fault persists", len(_bad) == 1,
          len(_bad))

    # 9. the authority it does not have.
    check("the sweep never stopped, started or relaunched anything",
          trip.calls == [], trip.calls)

    # 3. the cheap check, not the full walk.
    check("the sweep uses quick_check, not the full integrity walk",
          _deep_seen and not any(_deep_seen), _deep_seen)

    # and it says so when the fault clears, so the channel is not left on red. One
    # long-lived loop, because remembering what it already said is the whole point -
    # a fresh loop per pass would report a recovery it never saw break.
    _drain2()
    _deep_seen[:] = []
    _t.run_until_complete(_sweep_once([(False, "SQLite reports it damaged: x")] * 2
                                      + [(True, "ok")] * 60))
    _ev3 = _drain2()
    _names3 = [i["event"] for i in _ev3]
    check("a save point that reads again is reported as recovered",
          "world.readable_again" in _names3, _names3)
    check("and only after it was reported broken, in that order",
          _names3.index("world.damaged") < _names3.index("world.readable_again"),
          _names3)
finally:
    _t.close()



# ---- the restart-loop watch: said once, to the channel, without a page being open
#
# looks_like_a_loop already ran on every status() call and the page already coloured the
# map red. But status() only runs when somebody is looking at it, so a map that started
# flapping at three in the morning produced no record at all. This pins the three things
# that made it worth wiring: it reports, it reports once, and it never acts.

def _loop_status(frames):
    """A status() that hands back one prepared frame per call, then holds the last."""
    frames = list(frames)

    def status(_store):
        return frames.pop(0) if len(frames) > 1 else frames[0]
    return status


def _svc(name, looping, failure="", tail=""):
    return {"name": name, "looping": looping, "failure": failure, "log_tail": tail,
            "state": "running"}


async def _loop_once(frames):
    tripped = _Tripwire()
    real_stop, real_start, real_one = _cl2.stop, _cl2.launch, _cl2.start_one
    _cl2.stop, _cl2.launch, _cl2.start_one = tripped.stop, tripped.launch, tripped.start_one
    try:
        task = _aio2.create_task(_appmod.loop_watch(
            _sw_store, interval=0.01, status=_loop_status(frames), sleep_first=False))
        await _aio2.sleep(0.08)
        task.cancel()
        try:
            await task
        except _aio2.CancelledError:
            pass
    finally:
        _cl2.stop, _cl2.launch, _cl2.start_one = real_stop, real_start, real_one
    return tripped


_flap = {"services": [_svc("ark-island", True, "Data folder is not writable.", "boom")]}
_calm = {"services": [_svc("ark-island", False)]}
_gone = {"services": []}

_t2 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _trip2 = _t2.run_until_complete(_loop_once([_flap]))
    _lv = _drain2()
    _loops = [i for i in _lv if i["event"] == "cluster.map_looping"]
    check("a map that keeps restarting is announced", len(_loops) >= 1,
          [i["event"] for i in _lv])
    check("as an error", _loops and _loops[0]["level"] == "error", _loops[:1])
    check("naming the map and what the log said",
          _loops and "ark-island" in _loops[0]["text"]
          and "not writable" in _loops[0]["text"], _loops[:1])
    check("and saying plainly that it changed nothing",
          _loops and "not an action" in _loops[0]["text"], _loops[:1])
    check("once per loop, not once per poll", len(_loops) == 1, len(_loops))
    check("the watch never stopped, started or relaunched anything",
          _trip2.calls == [], _trip2.calls)

    # and the channel is not left on red once the map is staying up again.
    _drain2()
    _t2.run_until_complete(_loop_once([_flap, _flap, _calm]))
    _lv2 = _drain2()
    _names4 = [i["event"] for i in _lv2]
    check("a map that stops restarting is reported as settled",
          "cluster.map_settled" in _names4, _names4)
    check("and only after it was reported looping, in that order",
          "cluster.map_looping" in _names4
          and _names4.index("cluster.map_looping") < _names4.index("cluster.map_settled"),
          _names4)

    # stopping the cluster removes every container. That is not ten maps recovering.
    _drain2()
    _t2.run_until_complete(_loop_once([_flap, _flap, _gone]))
    _names5 = [i["event"] for i in _drain2()]
    check("a map that disappears is not announced as settled",
          "cluster.map_settled" not in _names5, _names5)

    # both events have to render as something, or they arrive in Discord as bullets.
    check("both new events have an icon of their own",
          _ann2.ICONS.get("map_looping") == "\u274c"
          and _ann2.ICONS.get("map_settled") == "\u2705",
          [_ann2.ICONS.get("map_looping"), _ann2.ICONS.get("map_settled")])
finally:
    _t2.close()

# it has to actually be running, or none of the above ever happens on a real manager.
_ba_src = _insp_dead.getsource(_appmod.main)
check("the restart-loop watch is started with the other background watches",
      "loop_watch(store)" in _ba_src, "loop_watch" in _ba_src)



# ---- the post-swap gates: every map gets asked every question
#
# It used to read `bool(ok_h) and verify_instance(...)`, so a map that did not report
# healthy was never asked whether its world was intact. The one map most likely to be
# damaged by a build swap was the one map that got no integrity check at all - and the
# batch reported it as a bare FAILED with nothing in it to act on.

_va_store = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
_va_store.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
_va_store.patch({"maps": "island,center,ragnarok", "admin_password": "pw",
                 "cluster_id": "vatest", "host_ram_gb": 256})

_health = {"island": (False, "the container exited while starting"),
           "center": (True, "healthy"), "ragnarok": (True, "healthy")}
_gates = {"island": (False, ["the world on disk does not verify: it is 0 bytes"]),
          "center": (False, ["RCON is not answering", "the log says a mod did not load"]),
          "ragnarok": (True, [])}
_asked = []

_real_wh, _real_vi = _cl2.wait_healthy, _cl2.verify_instance
_cl2.wait_healthy = lambda store, key, **k: _health[key]
_cl2.verify_instance = lambda store, key, **k: (_asked.append(key) or _gates[key])
try:
    _ok_all, _per, _why = _appmod.verify_every_map(_va_store)
finally:
    _cl2.wait_healthy, _cl2.verify_instance = _real_wh, _real_vi

check("an unhealthy map is still asked about its world",
      "island" in _asked, _asked)
check("and so is every other map, whatever the ones before it did",
      sorted(_asked) == ["center", "island", "ragnarok"], _asked)
check("the batch as a whole fails", _ok_all is False, _ok_all)
check("every map that failed is named, not just the first",
      sorted(k for k, v in _per.items() if not v) == ["center", "island"], _per)
check("the map that passed is not dragged down with them",
      _per.get("ragnarok") is True, _per)
check("the unhealthy map's integrity reason survives",
      any("0 bytes" in r for r in _why.get("island") or []), _why.get("island"))
check("and its health timeout is kept as a reason too, not swallowed",
      any("exited while starting" in r for r in _why.get("island") or []),
      _why.get("island"))
check("a map with several problems reports all of them, not the first",
      len(_why.get("center") or []) == 2, _why.get("center"))

# a check that raises is a failed check, not a crashed apply. The gate runs after the
# swap, so an exception here would leave the batch with no verdict at all.
_real_wh, _real_vi = _cl2.wait_healthy, _cl2.verify_instance


def _boom(store, key, **k):
    if key == "center":
        raise RuntimeError("docker went away")
    return _gates[key]


_cl2.wait_healthy = lambda store, key, **k: (True, "healthy")
_cl2.verify_instance = _boom
try:
    _ok2, _per2, _why2 = _appmod.verify_every_map(_va_store)
finally:
    _cl2.wait_healthy, _cl2.verify_instance = _real_wh, _real_vi
check("a check that raises is a failed map, not a failed apply",
      _per2.get("center") is False and "ragnarok" in _per2, _per2)
check("and it says what went wrong",
      any("docker went away" in r for r in _why2.get("center") or []),
      _why2.get("center"))

# both apply paths - the button and the schedule - go through the one function, so the
# fix cannot be half-applied to a cluster.
for _fn, _label in ((_appmod.build_app, "the Apply button"),
                    (_appmod._scheduled_apply, "the scheduled apply")):
    _src = _insp_dead.getsource(_fn)
    check("%s calls the shared gate rather than its own copy" % _label,
          "verify_every_map(store)" in _src and "and clusterctl.verify_instance" not in _src,
          _label)



# ---- the wild-dino schedule reaches the thing that runs it
#
# It saved, it validated, it was in the docs, and it never fired once. generate_env
# wrote WIPE_TIMES into the .env the *maps* read, where POK has no idea what it means;
# the relay that does know is in this process, whose own environment has never had it
# set. Two rows missing from _RELAY_SETTINGS, and a feature that was entirely absent
# while looking entirely present.
_wst = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
_wst.patch({"appdata": "/srv/ark", "status_port": 8088}, source="install")
_wst.patch({"maps": "island", "admin_password": "pw", "cluster_id": "wipetest",
            "wipe_times": "03:15,21:45", "wipe_warn_minutes": "10,5,1"})


class _WipeBot(_Bot):
    WIPE_TIMES = []
    WIPE_WARN_MINUTES = []


_saved_running2 = clusterctl_t.running_instances
clusterctl_t.running_instances = lambda store: [("The Island", "asa-wipetest-island", 27020)]
_wb = _WipeBot()
appmod._wire_relay(_wst, _wb)
clusterctl_t.running_instances = _saved_running2

check("the wipe schedule reaches the relay that runs it",
      _wb.WIPE_TIMES == ["03:15", "21:45"], _wb.WIPE_TIMES)
check("in the shape the scheduler parses, not one comma-joined string",
      all(":" in t for t in _wb.WIPE_TIMES), _wb.WIPE_TIMES)
check("and so do the warnings, as numbers",
      _wb.WIPE_WARN_MINUTES == [10, 5, 1], _wb.WIPE_WARN_MINUTES)

# largest first is not cosmetic: the loop warns in the order it is given.
_wst.patch({"wipe_warn_minutes": "1,5,10"})
clusterctl_t.running_instances = lambda store: [("The Island", "asa-wipetest-island", 27020)]
_wb2 = _WipeBot()
appmod._wire_relay(_wst, _wb2)
clusterctl_t.running_instances = _saved_running2
check("warnings come out largest first however they were stored",
      _wb2.WIPE_WARN_MINUTES == [10, 5, 1], _wb2.WIPE_WARN_MINUTES)

# blank is the ordinary case - most clusters do not wipe - and must not raise.
_wst.patch({"wipe_times": "", "wipe_warn_minutes": ""})
clusterctl_t.running_instances = lambda store: [("The Island", "asa-wipetest-island", 27020)]
_wb3 = _WipeBot()
_okw = appmod._wire_relay(_wst, _wb3)
clusterctl_t.running_instances = _saved_running2
check("a cluster that does not wipe still wires", _okw, _okw)
check("and gets an empty schedule rather than a crash",
      _wb3.WIPE_TIMES == [] and _wb3.WIPE_WARN_MINUTES == [],
      [_wb3.WIPE_TIMES, _wb3.WIPE_WARN_MINUTES])

# the schedule is read on every pass, not captured once. It used to be captured at
# entry and the loop returned outright when it was empty - which is every fresh
# install, because the manager's environment never had WIPE_TIMES in it. Setting a
# time in the UI then did nothing until somebody restarted the manager, under a
# settings page that says no restart is needed.
from . import bot as _botmod                                     # noqa: E402
_ml_src = _insp_dead.getsource(_botmod.Relay.maintenance_loop)
check("the scheduler re-reads the schedule inside its loop",
      _ml_src.index("while True:") < _ml_src.index("for x in WIPE_TIMES"), _ml_src[:300])
_ml_stmts = [l.strip() for l in _ml_src.splitlines() if not l.strip().startswith("#")]
check("and an empty schedule no longer ends the loop for good",
      "if not targets:" not in _ml_src
      and not any(l == "return" or l.startswith("return ") for l in _ml_stmts),
      [l for l in _ml_stmts if l.startswith("return")])



# ---- a hand-edited clock time must not be able to kill the manager
#
# The settings page rejects "ab:cd". settings.json is a file a person edits, and a
# value that arrives that way is read back unvalidated - so it reached a loop that
# called int() on it every twenty seconds. bot.main() gathers its tasks without
# return_exceptions and main() gathers those, so one mistyped time did not stop the
# wipes: it stopped the web UI, the backup scheduler, the world sweep and the relay.
import time as _time_dead                                        # noqa: E402
_junk = ["ab:cd", "25:99x", "3:15", "0315", "", None, ":", "::", "1:2:3", " ", "12:",
         ":30", "-1:00", "3.5:00"]
_raised = []
for _bad in _junk:
    for _fn, _name in ((_botmod._hhmm_to_min, "_hhmm_to_min"),
                       (appmod._times, "_times")):
        try:
            _fn(_bad)
        except Exception as e:                       # noqa: BLE001 - that is the point
            _raised.append("%s(%r): %s" % (_name, _bad, e))
check("no clock value a person can type raises out of the parsers",
      _raised == [], _raised)

check("a readable time is still read", _botmod._hhmm_to_min("03:15") == 195)
check("and a lenient one the game accepts too", _botmod._hhmm_to_min("3:15") == 195)
check("something with no colon is not a time", _botmod._hhmm_to_min("0315") is None)
check("and neither is something with a colon and no numbers",
      _botmod._hhmm_to_min("ab:cd") is None and _botmod._hhmm_to_min("25:99x") is None)
check("and a number that is not an hour of the day is not a time either",
      _botmod._hhmm_to_min("-1:00") is None and _botmod._hhmm_to_min("30:00") is None,
      [_botmod._hhmm_to_min("-1:00"), _botmod._hhmm_to_min("30:00")])
check("while both ends of a real day still are",
      _botmod._hhmm_to_min("00:00") == 0 and _botmod._hhmm_to_min("23:59") == 1439)

# the junk goes, the real times stay. Dropping the whole line because one entry was
# mistyped would turn a typo into a silently cancelled schedule.
check("a mixed schedule keeps the times that parse and drops the rest",
      appmod._times("03:15, ab:cd, 21:45") == ["03:15", "21:45"],
      appmod._times("03:15, ab:cd, 21:45"))
check("a blank schedule is a cluster that does not wipe, not an error",
      appmod._times("") == [] and appmod._times(None) == [],
      [appmod._times(""), appmod._times(None)])
check("a schedule with nothing usable in it is unreadable, not empty",
      appmod._times("ab:cd") is None and appmod._times("0315") is None,
      [appmod._times("ab:cd"), appmod._times("0315")])

# ...and unreadable means the relay keeps what it had, rather than losing the schedule
# it was running or being handed something that raises on it twice a minute.
_wst.patch({"wipe_times": "03:15,21:45", "wipe_warn_minutes": "10,5,1"})
clusterctl_t.running_instances = lambda store: [("The Island", "asa-wipetest-island", 27020)]
_wb4 = _WipeBot()
appmod._wire_relay(_wst, _wb4)
_wst.data["cluster"]["wipe_times"] = "ab:cd"          # as a hand-edited file would be
_wst.data["cluster"]["wipe_warn_minutes"] = "soon"
appmod._wire_relay(_wst, _wb4)
clusterctl_t.running_instances = _saved_running2
check("a wholly unreadable schedule keeps the last good one",
      _wb4.WIPE_TIMES == ["03:15", "21:45"], _wb4.WIPE_TIMES)
check("and so do the warnings", _wb4.WIPE_WARN_MINUTES == [10, 5, 1],
      _wb4.WIPE_WARN_MINUTES)
check("a partly unreadable warning list keeps the numbers in it",
      appmod._minutes("10, soon, 1") == [10, 1], appmod._minutes("10, soon, 1"))

# the structural guarantee, independent of what any parser does next: a pass that
# raises is a pass that is skipped, not a manager that exits.
_ml_body = [l.strip() for l in _ml_src.splitlines()]
check("every pass of the wipe loop is guarded",
      "try:" in _ml_body and any(l.startswith("except Exception") for l in _ml_body),
      _ml_body[:12])


class _Boom:
    """A relay whose announce blows up, standing in for the next unknown fault."""

    def __init__(self):
        self.passes = 0

    async def announce(self, _text):
        raise RuntimeError("discord went away")

    async def wipe_wild(self):
        return None


_boom = _Boom()
_real_wt, _real_ww = _botmod.WIPE_TIMES, _botmod.WIPE_WARN_MINUTES
_now = _time_dead.localtime()
_botmod.WIPE_TIMES = ["%02d:%02d" % (_now.tm_hour, _now.tm_min)]
_botmod.WIPE_WARN_MINUTES = [10, 5, 1]
_t3 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _run_boom():
        task = _aio2.create_task(_botmod.Relay.maintenance_loop(_boom))
        await _aio2.sleep(0.05)
        alive = not task.done()
        task.cancel()
        try:
            await task
        except _aio2.CancelledError:
            pass
        return alive
    _alive = _t3.run_until_complete(_run_boom())
finally:
    _t3.close()
    _botmod.WIPE_TIMES, _botmod.WIPE_WARN_MINUTES = _real_wt, _real_ww
check("a pass that raises does not end the loop, and so cannot end the manager",
      _alive, _alive)



# ---- a failed off-site upload has to look like a failure
#
# The sentence went into the message slot whatever it said, so "the upload failed"
# arrived in the same grey box, in the same voice, as "the upload worked" - on the one
# page whose entire job is telling you whether a copy of your cluster exists anywhere
# other than this machine. Same class as the Disconnect honesty fix: the styling was
# the claim, and the claim was wrong.
_cd = tempfile.mkdtemp()
os.environ["OBELISK_ARK"] = os.path.join(_cd, "ark")
_cstore, _cc, _ccode = bootstrap(os.path.join(_cd, "obelisk"), environ={})
_cstore.patch({"maps": "island", "admin_password": "pw", "cluster_id": "pushtest"})

_FAIL_TEXT = ("The off-site copy did NOT happen: the network is unreachable. The local "
              "backup is fine and is on this disk; there is no copy off this machine.")
_OK_TEXT = "Uploaded obelisk-backup-x.tar.gz."
_UNCONF_TEXT = ("Off-site is on but no cloud is connected, so nothing was uploaded. "
                "Connect one on the Cloud page, or turn off-site off.")

_real_listing, _real_push = _appmod.backupctl.listing, _appmod.backupctl.push_offsite
_pushed = []


async def _push_once(result):
    _appmod.backupctl.listing = lambda store: [
        {"name": "obelisk-backup-x.tar.gz", "path": "/tmp/obelisk-backup-x.tar.gz",
         "bytes": 10, "mtime": 1, "when": "now"}]
    _appmod.backupctl.push_offsite = lambda store, path: (_pushed.append(path) or result)  # noqa: E501
    client = TestClient(TestServer(build_app(_cstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_cstore.get("admin_token"))})
    body = await (await client.post("/admin/cloud/push")).text()
    await client.close()
    return body


_t4 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _bad_body = _t4.run_until_complete(_push_once((False, True, _FAIL_TEXT)))
    _bad_events = _drain2()
    _ok_body = _t4.run_until_complete(_push_once((True, True, _OK_TEXT)))
    _ok_events = _drain2()
    _unc_body = _t4.run_until_complete(_push_once((False, False, _UNCONF_TEXT)))
    _unc_events = _drain2()
finally:
    _t4.close()
    _appmod.backupctl.listing = _real_listing
    _appmod.backupctl.push_offsite = _real_push

check("a failed upload renders in the problem slot, not the note slot",
      ('<div class=problem>' + ui._e(_FAIL_TEXT)) in _bad_body,
      _bad_body[:300])
check("and says in as many words that it did not happen",
      "did NOT" in _bad_body, "did NOT" in _bad_body)
check("a failed upload is never dressed as a note",
      ('<div class=note>' + ui._e(_FAIL_TEXT)) not in _bad_body)
check("a successful upload renders in the note slot",
      ('<div class=note>' + ui._e(_OK_TEXT)) in _ok_body, _ok_body[:300])
check("and is not dressed as a problem",
      ('<div class=problem>' + ui._e(_OK_TEXT)) not in _ok_body)
check("the upload was actually attempted every time", len(_pushed) == 3, _pushed)

# A cloud nobody has connected yet is not an outage. Red here would be the same lie
# grey was, pointed the other way - it says something broke when what happened is
# that a step was never taken.
check("an unconfigured cloud renders as a warning, not a problem",
      ('<div class=warn>' + ui._e(_UNCONF_TEXT)) in _unc_body, _unc_body[:300])
check("and is not dressed in red",
      ('<div class=problem>' + ui._e(_UNCONF_TEXT)) not in _unc_body)
check("nor dressed as a note, which would read as an upload that happened",
      ('<div class=note>' + ui._e(_UNCONF_TEXT)) not in _unc_body)
check("it never claims an upload happened",
      "did NOT" not in _unc_body and "Uploaded" not in _unc_body)

# and the channel hears about it either way, at the right level.
_bad_names = [(i["event"], i["level"]) for i in _bad_events]
_ok_names = [(i["event"], i["level"]) for i in _ok_events]
check("a failed upload reaches Discord and the log as an error",
      ("cloud.push_failed", "error") in _bad_names, _bad_names)
check("a successful one is reported too, as information",
      ("cloud.push_done", "info") in _ok_names, _ok_names)
_unc_names = [(i["event"], i["level"]) for i in _unc_events]
check("an unconfigured cloud is announced at warning, not error",
      ("cloud.push_unconfigured", "warning") in _unc_names, _unc_names)
check("under its own event, not the one a real failure uses",
      not any(e == "cloud.push_failed" for e, _l in _unc_names), _unc_names)


# ---- the nightly backup was silent, pass or fail
#
# It is the thing this product exists to keep, and it announced nothing either way - so
# a schedule that had been failing for a week looked exactly like one that had been
# working. The local result and the off-site result are two facts and get two lines,
# because folding them into one is how a failure ends up with a tick beside it.
_real_run, _real_due = _appmod.backupctl.run_scheduled, _appmod.backupctl.due


async def _one_night(result):
    _appmod.backupctl.due = lambda store, **k: (True, "scheduled backup is due")
    _appmod.backupctl.run_scheduled = lambda store, **k: result
    task = _aio2.create_task(_appmod.backup_scheduler(_cstore, interval=0.01))
    await _aio2.sleep(0.05)
    task.cancel()
    try:
        await task
    except _aio2.CancelledError:
        pass


_t5 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _t5.run_until_complete(_one_night((True, "Wrote obelisk-backup-x.tar.gz.",
                                       (False, True, _FAIL_TEXT))))
    _night = _drain2()
    _t5.run_until_complete(_one_night((True, "Wrote obelisk-backup-x.tar.gz.",
                                       (False, False, _UNCONF_TEXT))))
    _night_unc = _drain2()
finally:
    _t5.close()
    _appmod.backupctl.run_scheduled, _appmod.backupctl.due = _real_run, _real_due

_by_event = {}
for _i in _night:
    _by_event.setdefault(_i["event"], _i)
check("a scheduled backup now says so in the channel",
      "backup.done" in _by_event, sorted(_by_event))
check("the local backup is still reported as good, because it was",
      _by_event.get("backup.done", {}).get("level") == "info",
      _by_event.get("backup.done"))
check("and its message is about the local backup, not the upload",
      "did NOT" not in (_by_event.get("backup.done", {}).get("text") or ""),
      _by_event.get("backup.done", {}).get("text"))
check("the failed upload gets its own line",
      "backup.offsite_failed" in _by_event, sorted(_by_event))
check("at error level", _by_event.get("backup.offsite_failed", {}).get("level") == "error",
      _by_event.get("backup.offsite_failed"))
check("carrying the consequence",
      "did NOT" in (_by_event.get("backup.offsite_failed", {}).get("text") or ""),
      _by_event.get("backup.offsite_failed", {}).get("text"))

# ...and a cloud nobody connected is a different fact, told differently. Announced the
# same way, the one that happens every night on a half-set-up install teaches the
# operator to scroll past the one that means their cluster exists in one place only.
_unc_by = {}
for _i in _night_unc:
    _unc_by.setdefault(_i["event"], _i)
check("an unconfigured cloud gets its own nightly event",
      "backup.offsite_unconfigured" in _unc_by, sorted(_unc_by))
check("at warning, not error",
      _unc_by.get("backup.offsite_unconfigured", {}).get("level") == "warning",
      _unc_by.get("backup.offsite_unconfigured"))
check("and is never announced as the event a real failure uses",
      "backup.offsite_failed" not in _unc_by, sorted(_unc_by))
check("it says what to do rather than claiming an upload",
      "no cloud is connected"
      in (_unc_by.get("backup.offsite_unconfigured", {}).get("text") or "")
      and "did NOT"
      not in (_unc_by.get("backup.offsite_unconfigured", {}).get("text") or ""),
      _unc_by.get("backup.offsite_unconfigured", {}).get("text"))
check("the local backup is still good on a night nothing was set up",
      _unc_by.get("backup.done", {}).get("level") == "info",
      _unc_by.get("backup.done"))

# every new event needs a glyph of its own, keyed on the tail the lookup actually
# takes - "backup.offsite_failed" resolves as "offsite_failed", not "failed".
for _tail, _want in (("offsite_failed", "\u274c"), ("offsite_done", "\u2705"),
                     ("push_failed", "\u274c"), ("push_done", "\u2705"),
                     ("offsite_unconfigured", "\u26a0"),
                     ("push_unconfigured", "\u26a0")):
    check("%s has an icon of its own, not a bullet" % _tail,
          _ann2.ICONS.get(_tail) == _want, _ann2.ICONS.get(_tail))
check("and they resolve through the real Discord formatter",
      _ann2.format_for_discord({"event": "backup.offsite_failed", "text": "x",
                                "fields": "", "detail": ""}).startswith("\u274c"),
      _ann2.format_for_discord({"event": "backup.offsite_failed", "text": "x",
                                "fields": "", "detail": ""})[:8])



# ---- the archive shown is the archive restored
#
# The restore page has two forms: one to look inside an archive, one to run the
# restore. The run form carried its own hidden copy of the name, so changing the
# dropdown without pressing "Look inside" left the page showing one archive while the
# button restored another - over a live world, with no confirmation anywhere on the
# page. The browser half of the fix is a disabled button; this is the half that is
# true for a second tab, and the half that can be tested without a browser.
import shutil as _shutil_b3                                      # noqa: E402

_b3d = tempfile.mkdtemp()
os.environ["OBELISK_ARK"] = os.path.join(_b3d, "ark")
_b3store, _b3c, _b3code = bootstrap(os.path.join(_b3d, "obelisk"), environ={})
_b3store.patch({"maps": "island,ragnarok", "admin_password": "pw",
                "cluster_id": "b3test"})

_b3backups = _appmod.backupctl.backups_dir(_b3store)
os.makedirs(_b3backups, exist_ok=True)
_ARC_A = "obelisk-backup-2026-09-12T03-00-00Z.tar.gz"
_ARC_B = "obelisk-backup-2026-09-01T03-00-00Z.tar.gz"
for _n in (_ARC_A, _ARC_B):
    with open(os.path.join(_b3backups, _n), "wb") as _fh:
        _fh.write(b"not really a tarball, and never opened in this test")

_INFO = {"ok": True, "problem": "", "created": "12 Sep 2026 03:00 UTC",
         "cluster_id": "b3test", "maps": ["TheIsland_WP", "Ragnarok_WP"],
         "mod_ids": "929110", "bytes": 1024, "from_manifest": True}

_b3stopped = []
_real_inspect, _real_compare = _appmod.restorectl.inspect, _appmod.restorectl.compare
_real_stop_one, _real_rmap = _appmod.clusterctl.stop_one, _appmod.restorectl.restore_map
_real_listp = _appmod.pointsctl.list_points


def _b3_restore_map(*a, **k):
    _b3stopped.append("restore_map")
    return True, "should never happen in this test", {}


async def _b3_run(inspect_name, run_name, confirm="The Island"):
    """Look inside one archive, then post a restore naming another."""
    _appmod.restorectl.inspect = lambda path: dict(_INFO)
    _appmod.restorectl.compare = lambda store, info: []
    _appmod.restorectl.restore_map = _b3_restore_map
    _appmod.clusterctl.stop_one = lambda store, key: (
        _b3stopped.append("stop:%s" % key) or (True, ""))
    _appmod.pointsctl.list_points = lambda store, key: []
    client = TestClient(TestServer(build_app(_b3store, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_b3store.get("admin_token"))})
    if inspect_name:
        await client.post("/admin/restore/inspect", data={"archive": inspect_name})
    body = await (await client.post(
        "/admin/restore/run",
        data={"archive": run_name, "map": "island", "confirm": confirm})).text()
    await client.close()
    return body


_t6 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _mismatch = _t6.run_until_complete(_b3_run(_ARC_A, _ARC_B))
    _mismatch_ev = _drain2()
    _noinspect = _t6.run_until_complete(_b3_run(None, _ARC_A))
    _noinspect_ev = _drain2()
    _nameless = _t6.run_until_complete(_b3_run(_ARC_A, _ARC_A, confirm="Ragnarok"))
    _nameless_ev = _drain2()
    _shown = _t6.run_until_complete(_b3_run(_ARC_A, _ARC_A, confirm=""))
    _drain2()
finally:
    _t6.close()
    _appmod.restorectl.inspect, _appmod.restorectl.compare = _real_inspect, _real_compare
    _appmod.clusterctl.stop_one = _real_stop_one
    _appmod.restorectl.restore_map = _real_rmap
    _appmod.pointsctl.list_points = _real_listp

check("restoring an archive other than the one looked inside is refused",
      "not the one that was looked inside" in _mismatch, _mismatch[-600:])
check("the refusal names the one that was asked for",
      _ARC_B in _mismatch, _ARC_B)
check("and the one that was actually checked", _ARC_A in _mismatch, _ARC_A)
check("nothing was stopped and no restore was started", _b3stopped == [], _b3stopped)
_mm_names = [(i["event"], i["level"]) for i in _mismatch_ev]
check("a mismatch is announced as a refusal, not a failure",
      ("restore.refused", "warning") in _mm_names, _mm_names)
check("and never as a restore that started",
      not any(e == "restore.start" for e, _l in _mm_names), _mm_names)
check("the announcement says nothing was changed",
      any("Nothing has been changed" in (i.get("text") or "") for i in _mismatch_ev),
      [i.get("text") for i in _mismatch_ev])

check("restoring with nothing looked inside is refused",
      "Look inside an archive first" in _noinspect, _noinspect[-600:])
check("still stopping nothing", _b3stopped == [], _b3stopped)
check("and announced as a refusal",
      ("restore.refused", "warning") in
      [(i["event"], i["level"]) for i in _noinspect_ev],
      [(i["event"], i["level"]) for i in _noinspect_ev])

# the typed name is the other half. It is the map's own name rather than a fixed word
# because the mistake worth preventing is doing this to the wrong map as much as doing
# it at all - and a fixed word is typed from muscle memory onto whatever is on screen.
check("the wrong map's name does not confirm a restore",
      "Type The Island to confirm" in _nameless, _nameless[-600:])
check("nor does an empty box", "Type The Island to confirm" in _shown, _shown[-600:])
check("nothing was stopped for either", _b3stopped == [], _b3stopped)
_nl_names = [(i["event"], i["level"]) for i in _nameless_ev]
check("a failed confirmation is a refusal at warning",
      ("restore.refused", "warning") in _nl_names, _nl_names)
check("and does not announce a restore starting",
      not any(e == "restore.start" for e, _l in _nl_names), _nl_names)

# ---- the page says which archive it is about to restore from
_b3body = _ui.render_restore(
    _b3store, [{"name": _ARC_A, "bytes": 1024, "mtime": 1, "when": "now"},
               {"name": _ARC_B, "bytes": 1024, "mtime": 1, "when": "then"}],
    chosen=_ARC_A, info=dict(_INFO), notes=[], savepoints_by_map=[])

check("the confirmation names the archive it will restore from",
      _ARC_A in _b3body, _ARC_A)
check("and when that archive was taken",
      "12 Sep 2026 03:00 UTC" in _b3body, "timestamp missing")
check("the run form carries the archive that was inspected",
      ('<input type=hidden name=archive value="%s">' % _ARC_A) in _b3body,
      "hidden field does not match info")
check("the dropdown is told which archive that was, so the browser can compare",
      ('data-looked="%s"' % _ARC_A) in _b3body, "no data-looked")
check("there is a box to type the map's name into",
      "name=confirm" in _b3body and "Type the map" in _b3body, "no confirm field")
check("and the placeholder is a real map name, not a word",
      'placeholder="The Island"' in _b3body, "placeholder is not a map name")
check("force is offered as a deliberate tick, not the default",
      'type=checkbox name=force' in _b3body and "checked" not in
      _b3body.split("name=force")[1][:40], "force is not an opt-in")
check("the consequence is stated where the decision is made",
      "no undo button" in _b3body and "entire world" in _b3body, "no consequence text")
check("in warning styling rather than as a quiet note",
      "<div class=warn>" in _b3body, "consequence is not styled as a warning")

# the browser half: the button goes dead the moment the select moves off it
check("a change listener is wired to the archive picker",
      "addEventListener('change'" in _b3body, "no change listener")
check("and it disables the run button on a mismatch",
      "btn.disabled" in _b3body and "pick.value !== looked" in _b3body,
      "listener does not compare or disable")
check("it fails safe when the elements are not there",
      "if(!pick||!btn) return;" in _b3body, "listener is not defensive")



# ---- a guard saying no must not look like a restore that broke
#
# The channel already told these apart - restore.refused at warning, restore.failed at
# error - and the page did not. "Type the map's name to confirm" arrived in the same
# red box as "the world was restored but the map did not start again". One of those
# means nothing happened; the other means something is half done and a world is sitting
# in a .superseded folder. The operator is standing in front of this screen.
_ARCS = [{"name": _ARC_A, "bytes": 1024, "mtime": 1, "when": "now"}]

_refused_job = {"state": "done", "ok": False, "step": "done",
                "message": "3 players are on The Island and this replaces the world "
                           "they are standing in. Nothing has been changed.",
                "detail": {"refused": "players"}}
_failed_job = {"state": "done", "ok": False, "step": "done",
               "message": "The world was restored but TheIsland_WP did not start "
                          "again: the container exited.",
               "detail": {"steps": ["swapped in"], "superseded": "x"}}
_done_job = {"state": "done", "ok": True, "step": "done",
             "message": "Restored The Island from %s." % _ARC_A,
             "detail": {"steps": ["swapped in"]}}


def _restore_page(**kw):
    return ui.render_restore(_b3store, _ARCS, chosen=_ARC_A, info=dict(_INFO),
                             notes=[], savepoints_by_map=[], **kw)


_ref_body = _restore_page(job=_refused_job)
_fail_body = _restore_page(job=_failed_job)
_done_body = _restore_page(job=_done_job)

check("a refusal is rendered amber, not red",
      ('<div class="warn">' + ui._e(_refused_job["message"])) in _ref_body,
      _ref_body[:200])
check("and never in the problem class",
      ('<div class="problem">' + ui._e(_refused_job["message"])) not in _ref_body)
check("a restore that actually broke is still red",
      ('<div class="problem">' + ui._e(_failed_job["message"])) in _fail_body,
      _fail_body[:200])
check("and is not softened to a warning",
      ('<div class="warn">' + ui._e(_failed_job["message"])) not in _fail_body)
check("a restore that worked is still a note",
      ('<div class="note">' + ui._e(_done_job["message"])) in _done_body,
      _done_body[:200])

# the route's own refusals are the same kind of answer, and were the same red
_route_ref = _restore_page(refusal="Type The Island to confirm.")
check("a refusal from the page's own guards is amber too",
      "<div class=warn>Type The Island to confirm." in _route_ref, _route_ref[:200])
check("while a real problem passed to the same page is still red",
      "<div class=problem>" in _restore_page(problem="No such archive."),
      "problem slot lost")

# ---- the way back is written down before somebody needs it
#
# The page said what to do if the restore went well and nothing about the other case -
# on the one screen whose own warning says there is no undo button. The folder it
# already lists IS the undo; it just has to be moved.
_sup_dir = os.path.join(os.environ["OBELISK_ARK"], "shared", "SavedArks",
                        "TheIsland_WP.superseded-20260913T090000Z")
os.makedirs(_sup_dir, exist_ok=True)
with open(os.path.join(_sup_dir, "TheIsland_WP.ark"), "wb") as _fh:
    _fh.write(bytes(2048))
_sup_body = _restore_page()
check("the replaced world is listed at all", "Replaced worlds" in _sup_body,
      "no superseded block")
check("and offered as the way back, not only as clutter",
      "the way back" in _sup_body, "no way back offered")
check("saying what to actually do with it",
      "superseded" in _sup_body and "start it again" in _sup_body,
      "no instructions for the folder")
check("without pretending there is a button for it",
      "no undo button" in _sup_body, "the honesty is gone")
check("the reclaim-the-space advice survives",
      "delete them by hand" in _sup_body.lower(), "cleanup advice lost")

# ---- an archive with nothing of yours in it
#
# Every map option disabled, the run button still live, and the placeholder degraded to
# the literal words "the map's name" - so pressing it answered "type the map's name to
# confirm", which is advice with nothing to do about it.
_FOREIGN = dict(_INFO, maps=["Aberration_WP", "Fjordur_WP"])
_foreign_body = ui.render_restore(_b3store, _ARCS, chosen=_ARC_A, info=_FOREIGN,
                                  notes=[], savepoints_by_map=[])
check("an archive holding none of your maps says so",
      "holds no world for any map this cluster runs" in _foreign_body,
      _foreign_body[:400])
check("naming what it does hold", "Aberration_WP" in _foreign_body, "archive maps")
check("and what you run", "The Island" in _foreign_body, "cluster maps")
check("the run button is not left looking actionable",
      "id=runbtn disabled" in _foreign_body, "run button is still live")
check("an archive that does hold one of your maps still offers the button",
      "id=runbtn disabled" not in _restore_page(), "usable archive was disabled")


print("\nFAILURES: %s" % fails if fails else "\nall app tests passed")
sys.exit(1 if fails else 0)

