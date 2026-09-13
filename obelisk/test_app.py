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



def _in_order(body, *needles):
    """True when every needle is present in `body`, in this order.

    str.index raises when a needle is missing, so an assertion built on it reports a
    crash instead of a failure - and a crash names no check and stops the suite. This
    asks the ordering question in a way that can answer "no".
    """
    at = [body.index(n) if n in body else -1 for n in needles]
    return all(i >= 0 for i in at) and at == sorted(at)


def _from(body, anchor):
    """`body` from `anchor` onwards, or "" when it is not there.

    Scoping a check to a section is right; slicing with .index to do it turns a missing
    section into a crash. An empty string fails every check made against it, which is
    what a missing section should do.
    """
    i = body.find(anchor)
    return body[i:] if i >= 0 else ""



def _window(body, anchor, size):
    """`size` characters of `body` from `anchor`, or "" when it is not there."""
    i = body.find(anchor)
    return body[i:i + size] if i >= 0 else ""


def _after(body, anchor):
    """`body.split(anchor)[1]`, but "" instead of IndexError when it is not there.

    The same family as .index, and missed in the first pass: splitting on a needle that
    is gone raises, so a check scoped this way reports a crash rather than a failure -
    and a crash names no check and stops the module before the rest of it runs. The
    segment is the one split() would have given, so nothing changes about what is being
    looked at.
    """
    parts = body.split(anchor)
    return parts[1] if len(parts) > 1 else ""


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
    check("it offers a way into each map rather than a table of all of them",
          '/admin/cluster/map/island' in body and "<th>RCON</th>" not in body, body[:400])
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
    r = await client.post("/admin/stop", data={"force": "1"},
                          allow_redirects=False)
    check("stop hands the page straight back rather than holding it open",
          r.status == 302, r.status)
    for _ in range(40):
        if acts and acts[-1] == ["down"]:
            break
        await _aio.sleep(0.05)
    check("stop still runs from the UI", acts and acts[-1] == ["down"], acts)
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

    # The nav element alone: the forms on the page still post to /admin/mods and the
    # rest of the old paths, which is the point - only the tabs collapsed.
    nav = _from(body, "<nav>").split("</nav>")[0]
    check("the Data area is in the nav", "/admin/data" in nav, nav)
    check("and the four old tabs are not", not any(
        ('"%s"' % t) in nav for t in ("/admin/backups", "/admin/restore",
                                      "/admin/cloud", "/admin/mods")), nav)

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
    _at_dis = body.find("no longer be decrypted")
    _banner = body[max(0, _at_dis - 400):_at_dis] if _at_dis >= 0 else ""
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
check("through the helper both restore paths share",
      "verify_restored(store, key, note)" in _runsrc, _runsrc[:300])
_vrsrc = _appsrc.split("def verify_restored(")[1].split(chr(10) + "def ")[0]
check("and it waits for the map to be healthy before checking",
      "wait_healthy" in _vrsrc, _vrsrc[:200])
check("the six gates are the same ones the migration used",
      "verify_instance" in _vrsrc)
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
      _in_order(_mainsrc, "guard_secrets", "_wire_relay"),
      "registered too late")
check("and they come from the backup module's own list of secrets",
      "SECRET_KEYS" in _appsrc)

for _ev in ("backup.start", "restore.start", "restore.phase"):
    check("%s is announced" % _ev, '"%s"' % _ev in _appsrc, _ev)
check("a backup announces its outcome either way",
      '"backup.done" if ok else "backup.failed"' in _appsrc)
check("so does a restore, three ways",
      _appsrc.count('"restore.done" if ok else') == 2
      and _appsrc.count('"restore.refused" if refused else "restore.failed"') == 2,
      [_appsrc.count('"restore.done" if ok else'),
       _appsrc.count('"restore.refused" if refused else "restore.failed"')])
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
      _in_order(_mainsrc, "join_network_if_running", "_wire_relay"),
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
from . import announce as _ann2
from . import bans as _bans_app                                  # noqa: E402
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
          _in_order(_names3, "world.damaged", "world.readable_again"),
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
          and _in_order(_names4, "cluster.map_looping", "cluster.map_settled"),
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
      _in_order(_ml_src, "while True:", "for x in WIPE_TIMES"), _ml_src[:300])
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
      _after(_b3body, "name=force")[:40], "force is not an opt-in")
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



# ---- stopping the cluster: who is on, and a page that keeps answering
#
# Two faults on one button. It asked nobody whether people were playing before
# disconnecting ten servers' worth of them, and it held the HTTP response open across
# the whole stop - minutes, on ten maps - so the browser showed a dead tab with no way
# to tell a stop that was working from one that had hung.

# 1. every string the stop emits lands on exactly one phase. A stepper that drifts
#    from the thing it describes sits on -1 and reads as "nothing is happening", which
#    is the fault it exists to fix. APPLY_PHASES learned this the hard way.
_STOP_EMITS = [
    "Stopping the cluster. Each map is being asked to save and close its own world "
    "first, which takes a few minutes on a big map - nothing is shut down until its "
    "world is written.",
    "Ragnarok saved its world and closed (3 of 10).",
    "The Island saved its world and closed (10 of 10).",
    "The maps could not be asked to close their worlds, so every one of them is being "
    "stopped the ordinary way instead - their last saves are whatever each server "
    "wrote on its way out. Reason: docker went away",
    "10 of 10 worlds saved and closed. Stopping the servers now - nothing is left "
    "writing.",
    "8 of 10 worlds saved and closed. Valguero and Astraeos would not close and are "
    "being stopped the ordinary way instead - worth checking them once the cluster is "
    "back up. Stopping the servers now.",
    "1 of 1 world saved and closed. Stopping the servers now - nothing is left writing.",
    "Cluster stopped. Saves and settings are untouched; Launch brings it back.",
]
_lost = [t for t in _STOP_EMITS if ui.phase_index(t, ui.STOP_PHASES) < 0]
check("every stage the stop announces lands on a phase", _lost == [],
      [t[:60] for t in _lost])
_ambiguous = []
for _t in _STOP_EMITS:
    _hits = [m for _l, _ms in ui.STOP_PHASES for m in _ms if m.lower() in _t.lower()]
    if len(_hits) != 1:
        _ambiguous.append((_t[:50], _hits))
check("and on exactly one marker, so the bar cannot jump", _ambiguous == [],
      _ambiguous)
check("the stages come in the order the stop takes them",
      [ui.phase_index(t, ui.STOP_PHASES) for t in _STOP_EMITS]
      == [1, 1, 1, 1, 2, 2, 2, 3],
      [ui.phase_index(t, ui.STOP_PHASES) for t in _STOP_EMITS])

# ...and those strings are the real ones. Pinned against cluster.py's own source, so a
# reworded stage breaks here rather than silently on the page.
_clsrc = io.open(os.path.join(os.path.dirname(__file__), "cluster.py"),
                 encoding="utf-8").read()
_stopsrc = _clsrc.split("def stop(store")[1].split("def restart(")[0]
for _frag in ("asked to save and close", "saved its world and closed",
              "could not be asked to close", "Stopping the servers now",
              "Cluster stopped"):
    check("the stop still says %r, which the stepper matches on" % _frag,
          _frag in _stopsrc, _frag)

# 2. the wording, through the helper the rest of the product uses
check("one player reads as one player",
      "1 player is on Ragnarok" in ui.stop_reason({"Ragnarok": 1}, []),
      ui.stop_reason({"Ragnarok": 1}, []))
check("several read as several, and the maps are joined as a sentence",
      "4 players are on Ragnarok and The Island"
      in ui.stop_reason({"Ragnarok": 3, "The Island": 1}, []),
      ui.stop_reason({"Ragnarok": 3, "The Island": 1}, []))
check("three maps get their commas and their and",
      "Astraeos, Ragnarok and The Island"
      in ui.stop_reason({"Ragnarok": 1, "The Island": 1, "Astraeos": 1}, []),
      ui.stop_reason({"Ragnarok": 1, "The Island": 1, "Astraeos": 1}, []))
check("a map that did not answer is said, not swallowed",
      "did not answer" in ui.stop_reason({}, [("Valguero", "timed out")]),
      ui.stop_reason({}, [("Valguero", "timed out")]))
check("and a map nobody is on is not listed as occupied",
      "Ragnarok" not in ui.stop_reason({"Ragnarok": 0, "The Island": 2}, []),
      ui.stop_reason({"Ragnarok": 0, "The Island": 2}, []))

_warn_block = ui.render_stop_warning({"Ragnarok": 3, "The Island": 1}, [])
check("the refusal block is amber, the way the restore guard's is",
      "<div class=warn>" in _warn_block, _warn_block[:120])
check("it names who is on", "4 players are on Ragnarok and The Island" in _warn_block,
      _warn_block[:200])
check("it says what stopping would do to them",
      "disconnects them" in _warn_block, _warn_block[:300])
check("it says nothing has happened yet",
      "Nothing has been stopped" in _warn_block, _warn_block[:300])
check("and it offers the way through rather than only refusing",
      'name=force value="1"' in _warn_block and "Stop anyway" in _warn_block,
      _warn_block[-300:])

# 3. the route: refuse, force, and hand the page straight back
_sd = tempfile.mkdtemp()
os.environ["OBELISK_ARK"] = os.path.join(_sd, "ark")
_sstore, _sc, _scode = bootstrap(os.path.join(_sd, "obelisk"), environ={})
_sstore.patch({"maps": "island,ragnarok", "admin_password": "pw",
               "cluster_id": "stoptest"})

_stop_calls = []
_real_players, _real_stop = _appmod.clusterctl.players_online, _appmod.clusterctl.stop


def _slow_stop(store, **kw):
    """A stop that takes a while and describes itself on the way, like the real one."""
    say = kw.get("say") or (lambda *a, **k: None)
    _stop_calls.append("stop")
    say("cluster.closing",
        "Stopping the cluster. Each map is being asked to save and close its own "
        "world first, which takes a few minutes on a big map - nothing is shut down "
        "until its world is written.", slot=_appmod.clusterctl.STOP_SLOT)
    _time_dead.sleep(0.4)
    say("cluster.closed",
        "2 of 2 worlds saved and closed. Stopping the servers now - nothing is left "
        "writing.", slot=_appmod.clusterctl.STOP_SLOT)
    return True, "Cluster stopped. Saves and settings are untouched; Launch brings it back."


async def _post_stop(players, data=None, wait_done=True):
    _stop_calls.clear()
    _appmod.clusterctl.players_online = lambda store, **k: players
    _appmod.clusterctl.stop = _slow_stop
    client = TestClient(TestServer(build_app(_sstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_sstore.get("admin_token"))})
    began = _time_dead.monotonic()
    r = await client.post("/admin/stop", data=data or {}, allow_redirects=False)
    took = _time_dead.monotonic() - began
    body = await r.text()
    status = await (await client.get("/admin/cluster/status")).json()
    if wait_done:
        for _ in range(60):
            later = await (await client.get("/admin/cluster/status")).json()
            if later.get("state") != "running":
                break
            await _aio2.sleep(0.05)
    await client.close()
    return r, body, took, status


_t7 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    # nobody on: it just goes
    _r_ok, _b_ok, _took_ok, _mid = _t7.run_until_complete(
        _post_stop((0, {}, []), wait_done=False))
    check("a stop with nobody on is not refused", _r_ok.status == 302, _r_ok.status)
    check("the page comes straight back rather than being held open",
          _took_ok < 0.3, _took_ok)
    check("while the stop is still running behind it",
          _mid.get("state") == "running", _mid)
    check("and the status endpoint says where it has got to",
          "step" in _mid and "elapsed" in _mid, sorted(_mid))
    check("carrying the panel the page shows",
          "Stopping the cluster" in (_mid.get("html") or ""), _mid.get("html"))
    _t7.run_until_complete(_aio2.sleep(0.6))
    _drain2()

    # players on: refused, and nothing is stopped
    _r_no, _b_no, _took_no, _idle = _t7.run_until_complete(
        _post_stop((4, {"Ragnarok": 3, "The Island": 1}, [])))
    _ev_no = _drain2()
    check("a stop with players on is refused", _r_no.status == 200, _r_no.status)
    check("the cluster was never asked to stop", _stop_calls == [], _stop_calls)
    check("no stop job was started", _idle.get("state") != "running", _idle)
    check("the page names who is on",
          "4 players are on Ragnarok and The Island" in _b_no, _b_no[:400])
    check("in the amber block, not the red one",
          "<div class=warn>" in _b_no, "refusal is not amber")
    check("and offers Stop anyway", "Stop anyway" in _b_no, "no way through")
    _no_names = [(i["event"], i["level"]) for i in _ev_no]
    check("the refusal is announced as a refusal, at warning",
          ("cluster.stop_refused", "warning") in _no_names, _no_names)
    check("and never as a stop that happened",
          not any(e.startswith("cluster.stop.") for e, _l in _no_names), _no_names)

    # a map that did not answer counts as occupied
    _r_q, _b_q, _took_q, _idle_q = _t7.run_until_complete(
        _post_stop((0, {}, [("Valguero", "timed out")])))
    _drain2()
    check("a map that did not answer blocks the stop too", _r_q.status == 200,
          _r_q.status)
    check("nothing was stopped", _stop_calls == [], _stop_calls)
    check("and it says why rather than implying the cluster was empty",
          "did not answer" in _b_q, _b_q[:400])

    # force goes through
    _r_f, _b_f, _took_f, _mid_f = _t7.run_until_complete(
        _post_stop((4, {"Ragnarok": 3, "The Island": 1}, []), data={"force": "1"},
                   wait_done=False))
    check("force stops it anyway", _r_f.status == 302, _r_f.status)
    check("and hands the page straight back as well", _took_f < 0.3, _took_f)
    check("with the stop actually running", _mid_f.get("state") == "running", _mid_f)
    _t7.run_until_complete(_aio2.sleep(0.6))
    _ev_f = _drain2()

    # 4. Discord is unchanged: one slot for the whole stop, ended at the result
    _slots = [(i["event"], i["slot"], i["slot_end"]) for i in _ev_f
              if i["event"].startswith("cluster.")]
    check("every stage of the stop goes into one slot",
          _slots and all(sl == _appmod.clusterctl.STOP_SLOT for _e2, sl, _se in _slots),
          _slots)
    check("so the channel carries one message rather than a scroll",
          len({sl for _e2, sl, _se in _slots}) == 1, _slots)
    check("and the result ends it, so the next stop starts a fresh one",
          [se for _e2, _sl, se in _slots][-1] is True, _slots)
    check("the request itself is part of the same story",
          _slots and _slots[0][0] == "cluster.stop", _slots)
    check("the stop still reports its result",
          any(e == "cluster.stop.done" for e, _sl, _se in _slots), _slots)
finally:
    _t7.close()
    _appmod.clusterctl.players_online = _real_players
    _appmod.clusterctl.stop = _real_stop

# the page has to actually poll, or none of the above is visible to anybody
check("the cluster page carries the stop poller",
      "/admin/cluster/status" in ui.STOP_JS, "no poller")
check("which swaps in the panel the server rendered",
      "wrap.innerHTML=j.html" in ui.STOP_JS, "panel is rebuilt in the browser")
check("and reloads once the stop is done rather than polling for ever",
      "location.reload()" in ui.STOP_JS, "never reloads")
check("an idle stop renders the empty wrapper and nothing else",
      ui.render_stop_job({"state": "idle"}) == "<div id=stopwrap></div>",
      ui.render_stop_job({"state": "idle"}))
check("a running one renders the stepper",
      "stepper" in ui.render_stop_job({"state": "running", "step": _STOP_EMITS[0]}),
      "no stepper")



# ---- a stop that is over has to say how it went
#
# The first version rendered nothing at all for a finished job and reloaded the page,
# so a stop that failed showed no reason anywhere - worse than the synchronous version
# it replaced, which at least put the docker error in a red banner. And a stop that
# worked lost its acknowledgement: "nothing is running" describes a state, it does not
# confirm an action, and it reads exactly like a cluster that was never launched.
_STOPPED_MSG = ("Cluster stopped. Saves and settings are untouched; Launch brings it "
                "back.")
_FAILED_MSG = "docker compose down failed:\nno such network: asa-cluster"

_good_job = {"state": "done", "ok": True, "message": _STOPPED_MSG, "step": "done"}
_bad_job = {"state": "done", "ok": False, "message": _FAILED_MSG, "step": "failed"}

_good_html = ui.render_stop_job(_good_job)
_bad_html = ui.render_stop_job(_bad_job)

check("a stop that worked says so on the page, not only in Discord",
      ui._e(_STOPPED_MSG) in _good_html, _good_html)
check("as a note, because it is what was asked for",
      '<div class="note">' in _good_html, _good_html)
check("and it confirms the action rather than describing a state",
      "untouched" in _good_html and "Launch brings it back" in _good_html, _good_html)
check("a stop that failed shows the reason",
      "no such network" in _bad_html, _bad_html)
check("in red, because something is wrong",
      '<div class="problem">' in _bad_html, _bad_html)
check("the result is read from the message, not from the step",
      ui._e(_FAILED_MSG) in _bad_html, _bad_html)
check("a bare step of 'failed' never becomes the whole banner",
      _bad_html != '<div id=stopwrap><div class="problem">failed</div></div>',
      _bad_html)

# ---- the bar is lit from the first paint, including the route's own opening steps
#
# The panel appears on the redirect, a second or two before the stop itself says
# anything. Those opening steps used to resolve to -1, so the first thing the operator
# saw was a bar with no cell lit - indistinguishable from a broken one, which is the
# exact thing this stepper exists to rule out.
_OPENING = ["starting", "Stop requested from the web UI."]
_WHOLE_FLOW = _OPENING + _STOP_EMITS
_dark = [t for t in _WHOLE_FLOW if ui.phase_index(t, ui.STOP_PHASES) < 0]
check("no step in the whole flow leaves the bar dark", _dark == [],
      [t[:60] for t in _dark])
check("including the very first one the route sets",
      ui.phase_index("starting", ui.STOP_PHASES) == 0,
      ui.phase_index("starting", ui.STOP_PHASES))
check("and the one the request announces",
      ui.phase_index("Stop requested from the web UI.", ui.STOP_PHASES) == 0,
      ui.phase_index("Stop requested from the web UI.", ui.STOP_PHASES))
_idx_flow = [ui.phase_index(t, ui.STOP_PHASES) for t in _WHOLE_FLOW]
check("the bar only ever moves forward", all(b >= a for a, b in zip(_idx_flow,
                                                                   _idx_flow[1:])),
      _idx_flow)
_amb2 = [(t[:40], [m for _l, ms in ui.STOP_PHASES for m in ms if m.lower() in t.lower()])
         for t in _WHOLE_FLOW
         if len([m for _l, ms in ui.STOP_PHASES for m in ms if m.lower() in t.lower()]) != 1]
check("and every step still matches exactly one marker", _amb2 == [], _amb2)
check("a running stop always lights a cell",
      '"st now"' in ui.render_stop_job({"state": "running", "step": "starting"}),
      ui.render_stop_job({"state": "running", "step": "starting"}))

# ---- one panel, not two
#
# _cluster_body rendered the panel inline and STOP_JS injected another into its own
# #stopwrap, so the operator watched two identical "Stopping the cluster" panels
# stacked for the length of the stop.
check("the wrapper the poller targets is rendered exactly once",
      (ui.render_stop_job({"state": "running", "step": "starting"})
       + ui.STOP_JS).count("id=stopwrap") == 1,
      (ui.render_stop_job({"state": "running", "step": "starting"})
       + ui.STOP_JS).count("id=stopwrap"))
check("the script no longer carries a wrapper of its own",
      "id=stopwrap" not in ui.STOP_JS, ui.STOP_JS[:200])
check("and it replaces what is inside the one that is there",
      "wrap.innerHTML=j.html" in ui.STOP_JS, "poller does not target the wrapper")

# ---- polling stops when the job does
check("the poller only schedules another round while the job is running",
      ui.STOP_JS.count("setTimeout(tick,2000)") == 1
      and "return;" in ui.STOP_JS.split("setTimeout(tick,2000)")[1][:40],
      ui.STOP_JS)
check("a terminal state is not polled for ever",
      "if(j.state==='running')" in ui.STOP_JS, "no terminal check")
check("and the result is swapped in before it gives up",
      _in_order(ui.STOP_JS, "wrap.innerHTML=j.html",
                "if(j.state==='running')"),
      "result swap comes too late")

# ---- the refusal splits the count per map
_split_block = ui.render_stop_warning({"Ragnarok": 3, "The Island": 1}, [])
check("the refusal says where the players are, not just how many",
      "3 on Ragnarok, 1 on The Island" in _split_block, _split_block[:300])
check("with the total still in front of it",
      "4 players are on Ragnarok and The Island" in _split_block, _split_block[:300])
check("one map needs no split", "(1 on Ragnarok)" not in
      ui.render_stop_warning({"Ragnarok": 1}, []),
      ui.render_stop_warning({"Ragnarok": 1}, []))
check("and it still reads as one sentence for one player",
      "1 player is on Ragnarok" in ui.render_stop_warning({"Ragnarok": 1}, []),
      ui.render_stop_warning({"Ragnarok": 1}, []))

# ---- and the whole thing end to end: a finished stop's result reaches the page
_real_players2, _real_stop2 = _appmod.clusterctl.players_online, _appmod.clusterctl.stop


async def _stop_to_the_end(result):
    _appmod.clusterctl.players_online = lambda store, **k: (0, {}, [])
    _appmod.clusterctl.stop = lambda store, **k: result
    client = TestClient(TestServer(build_app(_sstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_sstore.get("admin_token"))})
    await client.post("/admin/stop", allow_redirects=False)
    for _ in range(60):
        j = await (await client.get("/admin/cluster/status")).json()
        if j.get("state") != "running":
            break
        await _aio2.sleep(0.05)
    page = await (await client.get("/admin/cluster")).text()
    await client.close()
    return j, page


_t8 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _j_ok, _page_ok = _t8.run_until_complete(
        _stop_to_the_end((True, _STOPPED_MSG)))
    _j_bad, _page_bad = _t8.run_until_complete(
        _stop_to_the_end((False, _FAILED_MSG)))
    _drain2()
finally:
    _t8.close()
    _appmod.clusterctl.players_online = _real_players2
    _appmod.clusterctl.stop = _real_stop2

check("the status endpoint carries the result once the stop is over",
      _j_ok.get("ok") is True and _STOPPED_MSG in (_j_ok.get("message") or ""), _j_ok)
check("and the html it hands the page is the result banner",
      '<div class="note">' in (_j_ok.get("html") or ""), _j_ok.get("html"))
check("a reload after a successful stop still shows what happened",
      ui._e(_STOPPED_MSG) in _page_ok, _page_ok[-800:])
check("a failed stop carries its reason in the status too",
      _j_bad.get("ok") is False and "no such network" in (_j_bad.get("message") or ""),
      _j_bad)
check("and a reload after it shows the reason in red",
      "no such network" in _page_bad and '<div class="problem">' in _page_bad,
      _page_bad[-800:])
check("neither page stacks two stop panels",
      _page_ok.count("id=stopwrap") == 1 and _page_bad.count("id=stopwrap") == 1,
      [_page_ok.count("id=stopwrap"), _page_bad.count("id=stopwrap")])



# ---- one #stopwrap in the DOM, during the poll as well as at first paint
#
# The committed test measured the server paint only, so it proved the operator does not
# see two panels and missed that the poller was nesting one wrapper inside the other:
# render_stop_job's html carried the id, and STOP_JS dropped that html *inside* the
# element with the same id. getElementById takes the outer one, so nothing broke -
# which is exactly how a duplicate id survives, right up until something queries it.
#
# So this models what the browser actually ends up holding: the wrapper the page
# rendered, with the polled html assigned into it, the way `wrap.innerHTML = j.html`
# does.
import re as _re_nest                                            # noqa: E402


def _dom_after_poll(job):
    """The page's wrapper with the poller's html inside it, as innerHTML would leave it."""
    page = ui.render_stop_job(job)
    polled = ui.render_stop_panel(job)
    opening = _re_nest.match(r"<div id=stopwrap>", page)
    assert opening, page[:80]
    return "<div id=stopwrap>%s</div>" % polled


for _label, _job in (
        ("at rest", {"state": "idle"}),
        ("on the first paint", {"state": "running", "step": "starting", "elapsed": 0}),
        ("mid-stop", {"state": "running", "elapsed": 42,
                      "step": "Ragnarok saved its world and closed (3 of 10)."}),
        ("when it worked", {"state": "done", "ok": True, "step": "done",
                            "message": _STOPPED_MSG}),
        ("when it failed", {"state": "done", "ok": False, "step": "failed",
                            "message": _FAILED_MSG})):
    check("exactly one #stopwrap %s, server-rendered" % _label,
          ui.render_stop_job(_job).count("id=stopwrap") == 1,
          ui.render_stop_job(_job)[:120])
    check("and the polled html brings none of its own %s" % _label,
          ui.render_stop_panel(_job).count("id=stopwrap") == 0,
          ui.render_stop_panel(_job)[:120])
    check("so the DOM holds exactly one after the poll lands %s" % _label,
          _dom_after_poll(_job).count("id=stopwrap") == 1,
          _dom_after_poll(_job)[:160])
    check("with no wrapper nested inside a wrapper %s" % _label,
          "<div id=stopwrap><div id=stopwrap>" not in _dom_after_poll(_job),
          _dom_after_poll(_job)[:160])

check("the id is emitted by one function and not the other",
      "id=stopwrap" in _insp_dead.getsource(ui.render_stop_job)
      and "id=stopwrap" not in _insp_dead.getsource(ui.render_stop_panel),
      "both or neither emit the id")
check("and the script still carries none",
      "id=stopwrap" not in ui.STOP_JS, ui.STOP_JS[:120])

# and the endpoint the poller actually calls hands back the unwrapped panel
_real_players3, _real_stop3 = _appmod.clusterctl.players_online, _appmod.clusterctl.stop


async def _poll_mid_stop():
    _appmod.clusterctl.players_online = lambda store, **k: (0, {}, [])

    def _slow(store, **kw):
        say = kw.get("say") or (lambda *a, **k: None)
        say("cluster.closing",
            "Stopping the cluster. Each map is being asked to save and close its own "
            "world first, which takes a few minutes on a big map - nothing is shut "
            "down until its world is written.",
            slot=_appmod.clusterctl.STOP_SLOT)
        _time_dead.sleep(0.5)
        return True, _STOPPED_MSG

    _appmod.clusterctl.stop = _slow
    client = TestClient(TestServer(build_app(_sstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_sstore.get("admin_token"))})
    page = await (await client.post("/admin/stop", allow_redirects=True)).text()
    mid = await (await client.get("/admin/cluster/status")).json()
    for _ in range(60):
        j = await (await client.get("/admin/cluster/status")).json()
        if j.get("state") != "running":
            break
        await _aio2.sleep(0.05)
    after = await (await client.get("/admin/cluster")).text()
    await client.close()
    return page, mid, j, after


_t9 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _page_mid, _mid_js, _end_js, _page_after = _t9.run_until_complete(_poll_mid_stop())
    _drain2()
finally:
    _t9.close()
    _appmod.clusterctl.players_online = _real_players3
    _appmod.clusterctl.stop = _real_stop3

check("the live page has one wrapper while a stop is running",
      _page_mid.count("id=stopwrap") == 1, _page_mid.count("id=stopwrap"))
check("the status endpoint hands back the panel without a wrapper",
      "id=stopwrap" not in (_mid_js.get("html") or ""), _mid_js.get("html"))
check("but it is still a panel, not nothing",
      "Stopping the cluster" in (_mid_js.get("html") or ""), _mid_js.get("html"))
check("dropping it into the live page leaves one wrapper",
      ("<div id=stopwrap>%s</div>" % (_mid_js.get("html") or "")).count(
          "id=stopwrap") == 1,
      _mid_js.get("html"))
check("the finished status is unwrapped too",
      "id=stopwrap" not in (_end_js.get("html") or ""), _end_js.get("html"))
check("and still carries the result",
      _STOPPED_MSG in (_end_js.get("html") or ""), _end_js.get("html"))
check("the page after it still has exactly one wrapper",
      _page_after.count("id=stopwrap") == 1, _page_after.count("id=stopwrap"))



# ---- one verify_after, and it asks every question
#
# Two byte-identical copies, both reading `if not ok_h: return False, [why_h]` - the
# same short circuit A3 took out of the apply gate, in a different feature. A map that
# did not report healthy was never asked whether its WORLD verifies, seconds after that
# world was swapped underneath it. That is the one question a restore exists to answer,
# skipped in exactly the case that most needed it.
_vr_store = Store(os.path.join(tempfile.mkdtemp(), "settings.json")).load()
_vr_store.patch({"appdata": "/srv/ark-data", "status_port": 8088}, source="install")
_vr_store.patch({"maps": "island", "admin_password": "pw", "cluster_id": "vrtest"})

_vr_asked = []
_vr_notes = []
_real_wh2, _real_vi2 = _cl2.wait_healthy, _cl2.verify_instance


def _vr_run(healthy, gates):
    _vr_asked[:] = []
    _vr_notes[:] = []
    _cl2.wait_healthy = lambda store, key, **k: healthy
    _cl2.verify_instance = lambda store, key, **k: (_vr_asked.append(key) or gates)
    try:
        return _appmod.verify_restored(_vr_store, "island", _vr_notes.append)
    finally:
        _cl2.wait_healthy, _cl2.verify_instance = _real_wh2, _real_vi2


_ok_u, _why_u = _vr_run((False, "the container exited while starting"),
                        (False, ["the world on disk does not verify: it is 0 bytes"]))
check("a restored map that is not healthy is still asked about its world",
      _vr_asked == ["island"], _vr_asked)
check("the restore is still a failure", _ok_u is False, _ok_u)
check("the world's verdict survives, which is the whole question",
      any("0 bytes" in r for r in _why_u), _why_u)
check("and the health timeout is kept as a reason, not as a reason to stop asking",
      any("exited while starting" in r for r in _why_u), _why_u)
check("both reasons, not just the first", len(_why_u) == 2, _why_u)

_ok_h2, _why_h2 = _vr_run((True, "healthy"), (True, []))
check("the healthy path is unchanged", _ok_h2 is True and _why_h2 == [],
      [_ok_h2, _why_h2])
_ok_g, _why_g = _vr_run((True, "healthy"), (False, ["RCON is not answering"]))
check("a healthy map that fails its gates still fails",
      _ok_g is False and _why_g == ["RCON is not answering"], [_ok_g, _why_g])
check("the steps are reported to the route either way", len(_vr_notes) == 2, _vr_notes)


def _vr_boom(store, key, **k):
    raise RuntimeError("docker went away")


_cl2.wait_healthy = lambda store, key, **k: (True, "healthy")
_cl2.verify_instance = _vr_boom
try:
    _ok_x, _why_x = _appmod.verify_restored(_vr_store, "island")
finally:
    _cl2.wait_healthy, _cl2.verify_instance = _real_wh2, _real_vi2
check("a check that raises is a failed map, not a failed restore",
      _ok_x is False, _ok_x)
check("and says what went wrong",
      any("docker went away" in r for r in _why_x), _why_x)
check("it works with no step reporter at all",
      isinstance(_why_x, list), type(_why_x).__name__)

# one copy, and a test that fails if either route grows its own again
_n3src = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                 encoding="utf-8").read()
import ast as _ast_n3                                            # noqa: E402
_vrtree = next(n for n in _ast_n3.walk(_ast_n3.parse(_n3src))
               if isinstance(n, _ast_n3.FunctionDef) and n.name == "verify_restored")
check("the helper has one exit, so it cannot bail before the gates",
      len([n for n in _ast_n3.walk(_vrtree)
           if isinstance(n, _ast_n3.Return)]) == 1,
      len([n for n in _ast_n3.walk(_vrtree) if isinstance(n, _ast_n3.Return)]))
check("and there is exactly one place in the file that waits and then gates",
      _n3src.count("clusterctl.wait_healthy(store, key)") == 1,
      _n3src.count("clusterctl.wait_healthy(store, key)"))
check("the apply gate goes through it too, rather than keeping a third copy",
      "verify_restored(store, key)" in
      _n3src.split("def verify_every_map(")[1].split(chr(10) + "def ")[0],
      "verify_every_map still has its own copy")
check("both restore routes call the shared helper",
      _n3src.count("verify_restored(store, key, note)") == 2,
      _n3src.count("verify_restored(store, key, note)"))


# ---- the save-point restore says no the way the archive restore does
#
# It never set detail["refused"], so a guard refusing because somebody is playing
# announced restore.failed at error and rendered red - beside an identical refusal from
# the archive path, amber, one page apart.
from . import savepoints as _sp2                                 # noqa: E402
from . import cluster as _clm                                    # noqa: E402

check("the plural agrees with the count",
      _clm._are(1) == "1 player is" and _clm._are(3) == "3 players are",
      [_clm._are(1), _clm._are(3)])
check("and the parenthetical is gone from the save-point path",
      "player(s)" not in _insp_dead.getsource(_sp2.restore_point),
      "player(s) is still there")

_spsrc = _insp_dead.getsource(_sp2.restore_point)
check("a save-point refusal marks itself as a refusal",
      _spsrc.count('detail["refused"]') == 2, _spsrc.count('detail["refused"]'))
check("both of them say nothing has been changed",
      _spsrc.count("Nothing has been changed") == 2,
      _spsrc.count("Nothing has been changed"))
check("and the route reads that to announce it as one",
      '"restore.refused" if refused else "restore.failed"' in _n3src
      and _n3src.count('"restore.refused" if refused') == 2,
      _n3src.count('"restore.refused" if refused'))
check("at warning rather than error",
      _n3src.count('"warning" if refused else "error"') == 2,
      _n3src.count('"warning" if refused else "error"'))

# the page renders it amber through the path the archive restore already uses - no
# second rendering route, so the two cannot drift apart again
_sp_refused_job = {"state": "done", "ok": False, "step": "done",
                   "message": "3 players are on Ragnarok. Nothing has been changed. "
                              "Restore with force, or wait until they are off.",
                   "detail": {"refused": "players"}}
_sp_failed_job = {"state": "done", "ok": False, "step": "done",
                  "message": "that restore point will not open: SQLite reports it "
                             "damaged",
                  "detail": {"steps": []}}
_sp_ref_body = ui.render_restore(_b3store, _ARCS, chosen=_ARC_A, info=dict(_INFO),
                                 notes=[], savepoints_by_map=[], job=_sp_refused_job)
_sp_fail_body = ui.render_restore(_b3store, _ARCS, chosen=_ARC_A, info=dict(_INFO),
                                  notes=[], savepoints_by_map=[], job=_sp_failed_job)
check("a refused save-point restore renders amber",
      ('<div class="warn">' + ui._e(_sp_refused_job["message"])) in _sp_ref_body,
      _sp_ref_body[:200])
check("not red", ('<div class="problem">' + ui._e(_sp_refused_job["message"]))
      not in _sp_ref_body)
check("a save-point restore that actually broke is still red",
      ('<div class="problem">' + ui._e(_sp_failed_job["message"])) in _sp_fail_body,
      _sp_fail_body[:200])


# ---- a manual backup that failed is not routine
#
# No level= at all meant info, so a failure was announced in the styling of a success,
# under an icon that is a cross. The event name was right and everything around it
# said nothing happened.
_bk_src = _n3src.split("def _run_backup()")[1].split("async def _backup_task")[0]
check("a manual backup failure is announced at error",
      'level="info" if ok else "error"' in _bk_src, _bk_src[-400:])
check("and a successful one is still information",
      '"backup.done" if ok else "backup.failed"' in _bk_src, _bk_src[-400:])
check("the failure icon and the failure level now agree",
      _ann2.ICONS.get("failed") == "❌", _ann2.ICONS.get("failed"))



# ---- the count reaches the pages, and costs nothing to draw
#
# The relay already asks every map once a minute and keeps the answer. Asking again to
# render a page would pay twice for it, and would put ten RCON round trips inside a
# request handler.
from . import bot as _bot_s1                                     # noqa: E402

_real_live = _bot_s1.LIVE
try:
    _bot_s1.LIVE = None
    check("with no relay running there is no snapshot",
          _bot_s1.online_snapshot() is None, _bot_s1.online_snapshot())

    class _FakeRelay:
        online_by_map = {"The Island": 3, "Ragnarok": 7}
        map_up = {"The Island": True, "Ragnarok": True}
        online_total = 10
        last_refresh = 0.0

    _fr = _FakeRelay()
    _bot_s1.LIVE = _fr
    check("a relay that has never polled yet is also no snapshot",
          _bot_s1.online_snapshot() is None, "an unpolled relay answered")

    _fr.last_refresh = _time_dead.time() - 30
    _snap = _bot_s1.online_snapshot()
    check("once it has polled, the snapshot is there", _snap is not None, _snap)
    _by, _total, _age = _snap
    check("carrying the per-map counts", _by == {"The Island": 3, "Ragnarok": 7}, _by)
    check("the cluster total", _total == 10, _total)
    check("and how old they are", 29 <= _age <= 40, _age)
    check("the snapshot is a copy, so a page cannot edit the relay's state",
          (_by.__setitem__("The Island", 999) or _fr.online_by_map["The Island"]) == 3,
          _fr.online_by_map)
finally:
    _bot_s1.LIVE = _real_live

_s1src = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                 encoding="utf-8").read()
# The front page IS the cluster page now. Status called render_status with the same
# services and the same poll as this one, which is two renderings of one answer and two
# places for it to go stale differently.
_rootsrc = _after(_s1src, "async def root(request):").split(
    chr(10) + "    async def ")[0]
check("the front door renders nothing of its own",
      "ui.render_" not in _rootsrc and "_players_now()" not in _rootsrc, _rootsrc)
check("it sends people to the one page that does",
      'HTTPFound("/admin/cluster")' in _rootsrc, _rootsrc)
check("and still sends a stranger to setup first",
      _in_order(_rootsrc, "authed(request)", '"/setup"', '"/admin/cluster"'), _rootsrc)
_cbsrc = _s1src.split("def _cluster_body")[1].split(chr(10) + "    def ")[0]
check("the page still names its maps and asks for the count",
      "_label_services(st)" in _cbsrc
      and "players=_players_now()" in _after(_s1src, "def _summary_band"),
      _window(_s1src, "def _summary_band", 900))
check("the count is read, never measured, on a page render",
      "online_snapshot" in _s1src and "players_online" not in
      _s1src.split("def _players_now")[1].split("    def ")[0],
      "a page render is doing RCON")
check("naming a map is a lookup against the plan, not a guess",
      "by_instance" in _after(_s1src, "def _label_services").split(
          chr(10) + "    def ")[0],
      "labels are not from the plan")
check("and the key comes off the same row as the name, never reversed from it",
      'r["name"], r["map"]' in _after(_s1src, "def _label_services"),
      _window(_s1src, "def _label_services", 900))
check("and a plan that cannot be built does not blank the page",
      "except Exception" in _s1src.split("def _label_services")[1][:900],
      "_label_services can raise")

# ---- and none of the paths that touch the cluster moved
_untouched = {
    "the apply gate": "def verify_every_map(store):",
    "the restore gate": "def verify_restored(store, key, note=None):",
    "the stop guard": "ui.render_stop_warning(counts, silent)",
    "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
    "the save-before-stop": "save=lambda: clusterctl.save_and_settle(",
}
for _what, _frag in sorted(_untouched.items()):
    check("%s is untouched" % _what, _frag in _s1src, _frag)

# ...and it actually works, not merely says it does. A source check passes a mutation
# that leaves the lookup in place and stops assigning the result.
from .plan import build_plan as _bp_s1                            # noqa: E402

_ld = tempfile.mkdtemp()
os.environ["OBELISK_ARK"] = os.path.join(_ld, "ark")
_lstore, _lc, _lcode = bootstrap(os.path.join(_ld, "obelisk"), environ={})
_lstore.patch({"maps": "island,ragnarok", "admin_password": "pw",
               "cluster_id": "labeltest"})
_lrows = _bp_s1(_lstore)["maps"]
_linst = {r["name"]: r["instance"] for r in _lrows}
check("the plan knows both maps by name and instance", len(_linst) == 2, _linst)

_lstatus = {"docker_ok": True, "compose_exists": True, "running": 2, "services": [
    {"service": _linst["The Island"], "name": "asa-labeltest-island",
     "level": "ok", "says": "Online", "status": "Up"},
    {"service": _linst["Ragnarok"], "name": "asa-labeltest-ragnarok",
     "level": "ok", "says": "Online", "status": "Up"}]}


class _LiveRelay:
    online_by_map = {"The Island": 4, "Ragnarok": 1}
    map_up = {"The Island": True, "Ragnarok": True}
    online_total = 5
    last_refresh = 0.0


async def _page_with(relay):
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    _bot_s1.LIVE = relay
    client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_lstore.get("admin_token"))})
    front = await (await client.get("/")).text()
    cluster = await (await client.get("/admin/cluster")).text()
    await client.close()
    return front, cluster


_real_status_s1 = _appmod.clusterctl.status
_t10 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _lr = _LiveRelay()
    _lr.last_refresh = _time_dead.time() - 20
    _front, _clusterpg = _t10.run_until_complete(_page_with(_lr))
    _front_off, _cluster_off = _t10.run_until_complete(_page_with(None))
finally:
    _t10.close()
    _appmod.clusterctl.status = _real_status_s1
    _bot_s1.LIVE = _real_live

def _runtable(body):
    """The Running-now table alone. The Maps row renders the same link markup, so a
    pin that does not say which element it means is covered by the other one."""
    return _from(body, "<fieldset id=run>").split("</fieldset>")[0]


for _name, _body in (("the front page", _front), ("the cluster page", _clusterpg)):
    check("%s names its maps" % _name,
          '<a class=maplink href="/admin/cluster/map/island">The Island</a>'
          in _runtable(_body)
          and '<a class=maplink href="/admin/cluster/map/ragnarok">Ragnarok</a>'
          in _runtable(_body), _runtable(_body)[:400])
    check("%s styles that link rather than leaving a browser default" % _name,
          "<a href=\"/admin/cluster/map/" not in _runtable(_body),
          _runtable(_body)[:400])
    check("%s does not head an instance id as a Map" % _name,
          "<td>%s</td>" % _linst["The Island"] not in _body,
          _body[_body.find("Running now"):][:400])
    check("%s shows each map's own count" % _name,
          ">4</td>" in _body and ">1</td>" in _body,
          _body[_body.find("Running now"):][:400])
    check("%s shows the cluster total with its age" % _name,
          "<b>5 players online</b>" in _body and "ago" in _body,
          _body[_body.find("players online") - 40:][:200])

for _name, _body in (("the front page", _front_off), ("the cluster page", _cluster_off)):
    check("%s shows a dash when the relay is not running" % _name,
          "&mdash;" in _body and "players online" not in _body,
          _body[_body.find("Running now"):][:300])
    check("%s still names its maps without a relay" % _name,
          '<a class=maplink href="/admin/cluster/map/island">The Island</a>'
          in _runtable(_body), _runtable(_body)[:300])

# ---- a remembered number must never be served as a measured one
#
# refresh_online deliberately keeps a map's last-known count when it stops answering -
# the relay wants that, because the in-game population summary is better off slightly
# old than silent. A status page is not. A map that had nobody on it, gained players,
# then went quiet would show a confident "0" beside an age of "just now": the exact
# mistake the dash exists to prevent, wearing the feature's own clothes.
class _Flaky:
    """A relay whose second pass loses a map, the way refresh_online leaves it."""

    def __init__(self):
        self.online_by_map = {"The Island": 4, "Ragnarok": 1}
        self.map_up = {"The Island": True, "Ragnarok": True}
        self.online_total = 5
        self.last_refresh = _time_dead.time() - 15

    def goes_quiet(self, label):
        # exactly what the real poll does: keep the old number, flip map_up
        self.map_up[label] = False
        self.last_refresh = _time_dead.time() - 5


_real_live2 = _bot_s1.LIVE
try:
    _fl = _Flaky()
    _bot_s1.LIVE = _fl
    _b1, _t1, _a1 = _bot_s1.online_snapshot()
    check("both maps are measured to start with",
          _b1 == {"The Island": 4, "Ragnarok": 1}, _b1)
    check("and the total is both of them", _t1 == 5, _t1)

    _fl.goes_quiet("Ragnarok")
    _b2, _t2, _a2 = _bot_s1.online_snapshot()
    check("a map that stopped answering is left out of the snapshot",
          "Ragnarok" not in _b2, _b2)
    check("rather than served as a fresh number", _b2 == {"The Island": 4}, _b2)
    check("the relay still remembers it, for the in-game summary",
          _fl.online_by_map.get("Ragnarok") == 1, _fl.online_by_map)
    check("but the total counts only what was measured this pass", _t2 == 4, _t2)
    check("not the relay's own running total, which includes the ghost",
          _t2 != _fl.online_total, [_t2, _fl.online_total])

    # the nastiest shape: a map whose remembered number is 0
    _fl0 = _Flaky()
    _fl0.online_by_map = {"The Island": 4, "Valguero": 0}
    _fl0.map_up = {"The Island": True, "Valguero": True}
    _bot_s1.LIVE = _fl0
    check("a measured zero is still a zero",
          _bot_s1.online_snapshot()[0].get("Valguero") == 0,
          _bot_s1.online_snapshot()[0])
    _fl0.goes_quiet("Valguero")
    check("a remembered zero is not, because it is the one that looks fine",
          "Valguero" not in _bot_s1.online_snapshot()[0],
          _bot_s1.online_snapshot()[0])

    # a label nothing has a verdict for is not evidence of an answer
    _fl0.map_up.pop("Valguero")
    check("a map with no up/down record at all is left out too",
          "Valguero" not in _bot_s1.online_snapshot()[0],
          _bot_s1.online_snapshot()[0])
finally:
    _bot_s1.LIVE = _real_live2


# ---- and it reaches the page as a dash, with the header agreeing
_t11 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _quiet = _LiveRelay()
    _quiet.map_up = {"The Island": True, "Ragnarok": False}
    _quiet.last_refresh = _time_dead.time() - 10
    _page_q, _cluster_q = _t11.run_until_complete(_page_with(_quiet))

    # an unhealthy map with players on it used to give "across 0 maps"
    _unwell = _LiveRelay()
    _unwell.last_refresh = _time_dead.time() - 10
    _real_status_q = _appmod.clusterctl.status
    _appmod.clusterctl.status = lambda store: dict(_lstatus, services=[
        dict(x, level="bad", says="Failing to start") for x in _lstatus["services"]])
    try:
        _page_u, _cluster_u = _t11.run_until_complete(_page_with(_unwell))
    finally:
        _appmod.clusterctl.status = _real_status_q
finally:
    _t11.close()
    _appmod.clusterctl.status = _real_status_s1
    _bot_s1.LIVE = _real_live

for _name, _body in (("the front page", _page_q), ("the cluster page", _cluster_q)):
    _tbl = _body[_body.find("Running now"):][:600]
    check("%s shows the map that answered" % _name, ">4</td>" in _tbl, _tbl)
    check("%s shows a dash for the one that went quiet" % _name,
          "&mdash;" in _tbl and ">1</td>" not in _tbl, _tbl)
    check("%s totals only the map it could measure" % _name,
          "<b>4 players online</b>" in _body, _body[_body.find("players online") - 40:][:160])
    check("%s counts one map, because one is what it covered" % _name,
          "across 1 map " in _body and "across 1 maps" not in _body,
          _body[_body.find("players online") - 40:][:200])

for _name, _body in (("the front page", _page_u), ("the cluster page", _cluster_u)):
    check("%s never says players are online across no maps" % _name,
          "across 0 maps" not in _body,
          _body[_body.find("players online") - 40:][:200])
    check("%s counts the maps the players are actually on" % _name,
          "<b>5 players online</b>" in _body and "across 2 maps" in _body,
          _body[_body.find("players online") - 40:][:200])

# ---- the blackout reaches the real page as a warning, not as a zero
#
# Cluster-wide RCON loss is ordinary: the relay drops the docker network, the admin
# password changes, everything restarts at once. The page it produces is the one that
# gets somebody to restart maps that were serving fine.
_t12 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _dark = _LiveRelay()
    _dark.map_up = {"The Island": False, "Ragnarok": False}
    _dark.last_refresh = _time_dead.time() - 8
    _page_d, _cluster_d = _t12.run_until_complete(_page_with(_dark))
finally:
    _t12.close()
    _appmod.clusterctl.status = _real_status_s1
    _bot_s1.LIVE = _real_live

check("a blackout leaves the snapshot measuring nothing",
      _page_d.count("&mdash;</td>") == 2, _page_d.count("&mdash;</td>"))
for _name, _body in (("the front page", _page_d), ("the cluster page", _cluster_d)):
    check("%s does not call a blackout an empty cluster" % _name,
          "0 players online" not in _body,
          _body[_body.find("not available") - 80:][:300])
    check("%s says nothing answered" % _name, "no map answered" in _body,
          _body[_body.find("not available") - 80:][:300])
    check("%s warns before a restart rather than after one" % _name,
          "before restarting anything" in _body,
          _body[_body.find("not available") - 80:][:300])
    check("%s explains the dashes in its table" % _name,
          "did not answer the last poll" in _body, "no dash note")
    check("%s heads the last column Service" % _name,
          "<th>Service</th>" in _body and "<th>Container</th>" not in _body,
          "wrong column header")

check("and the no-relay page points at where to set one up",
      "Discord" in _front_off and "Settings" in _front_off,
      _front_off[_front_off.find("not available") - 40:][:300])

# ---- the roster keeps the snapshot's honesty rule
#
# A moderation card built on a stale roster would offer to kick somebody who may have
# left, from a map that is not answering anyway - and would then report the kick as
# sent. Same rule as the count: only maps that answered this poll.
class _Rostered:
    def __init__(self):
        self.online_by_map = {"The Island": 2, "Ragnarok": 1}
        self.online_names = {
            "The Island": [{"name": "Bob", "netid": "7656119800000001"},
                           {"name": "Cha,rlie", "netid": "000255a1b2"}],
            "Ragnarok": [{"name": "Dana", "netid": "19000000000000001"}]}
        self.map_up = {"The Island": True, "Ragnarok": True}
        self.online_total = 3
        self.last_refresh = _time_dead.time() - 12


_real_live_2a = _bot_s1.LIVE
try:
    _bot_s1.LIVE = None
    check("no relay means no roster at all",
          _bot_s1.online_roster() is None, _bot_s1.online_roster())

    _ro = _Rostered()
    _bot_s1.LIVE = _ro
    _by, _age = _bot_s1.online_roster()
    check("both maps' people come back", sorted(_by) == ["Ragnarok", "The Island"], _by)
    check("with their names", [p["name"] for p in _by["The Island"]] == ["Bob", "Cha,rlie"],
          _by["The Island"])
    check("and the netid a kick would key on",
          _by["Ragnarok"][0]["netid"] == "19000000000000001", _by["Ragnarok"])
    check("the age travels with it, as it does for the count", 10 <= _age <= 25, _age)

    # a page must not be able to edit what the relay thinks is going on
    _by["The Island"][0]["name"] = "tampered"
    _by["Ragnarok"] = []
    check("the roster is a copy, rows and all",
          _ro.online_names["The Island"][0]["name"] == "Bob"
          and _ro.online_names["Ragnarok"] != [],
          _ro.online_names)

    _ro.map_up["Ragnarok"] = False
    _by2, _ = _bot_s1.online_roster()
    check("a map that stopped answering contributes nobody",
          "Ragnarok" not in _by2, _by2)
    check("rather than the people who were standing on it a minute ago",
          _ro.online_names.get("Ragnarok"), "the relay forgot them entirely")
    check("the map that did answer is unaffected", "The Island" in _by2, _by2)

    _ro.map_up["The Island"] = False
    check("a total blackout is an empty roster, and the caller can see it is empty",
          _bot_s1.online_roster()[0] == {}, _bot_s1.online_roster())

    # the roster and the count are taken from one poll, so they cannot disagree
    _ro.map_up = {"The Island": True, "Ragnarok": False}
    _rby, _ = _bot_s1.online_roster()
    _sby, _stot, _ = _bot_s1.online_snapshot()
    check("roster and count cover the same maps", sorted(_rby) == sorted(_sby),
          [sorted(_rby), sorted(_sby)])
    check("and agree on how many people are on each",
          all(len(_rby[m]) == _sby[m] for m in _rby), [_rby, _sby])
    check("and on the total", sum(len(v) for v in _rby.values()) == _stot,
          [_rby, _stot])

    # ---- N9: the in-game answer is the same answer
    _ro.map_up = {"The Island": True, "Ragnarok": False}
    _said = _bot_s1.Relay.online_summary(_ro)
    check("the in-game summary counts only maps that answered",
          "2 survivors" in _said, _said)
    check("and does not mention the map that went quiet",
          "Ragnarok" not in _said, _said)
    check("so the game and the page agree about the population",
          str(sum(len(v) for v in _rby.values())) in _said, [_said, _rby])

    _ro.map_up = {"The Island": False, "Ragnarok": False}
    _dark_said = _bot_s1.Relay.online_summary(_ro)
    check("nothing answering is not told to a player as an empty cluster",
          "cluster to yourself" not in _dark_said, _dark_said)
    check("it says the question could not be asked",
          "cannot tell" in _dark_said and "no map answered" in _dark_said, _dark_said)

    _ro.map_up = {"The Island": True, "Ragnarok": True}
    _ro.online_by_map = {"The Island": 0, "Ragnarok": 0}
    check("a measured empty cluster is still told as an empty cluster",
          "cluster to yourself" in _bot_s1.Relay.online_summary(_ro),
          _bot_s1.Relay.online_summary(_ro))
finally:
    _bot_s1.LIVE = _real_live_2a

# the accessor reads; it never asks
_botsrc_2a = io.open(os.path.join(os.path.dirname(__file__), "bot.py"),
                     encoding="utf-8").read()
_ros_src = _botsrc_2a.split("def online_roster")[1].split(chr(10) + "def ")[0]
check("the roster accessor does no I/O of its own",
      "await" not in _ros_src and "rcon" not in _ros_src, _ros_src[:300])
# There is one other place that asks - the Discord !players command, which is an admin
# asking for the live answer rather than the cached one. That is fine; what was not is
# that it had its own splitter, cutting each name at the first comma, so a player called
# "Cha,rlie" came back as "Cha" in Discord and "Cha,rlie" on every other surface.
_players_cmd = _botsrc_2a.split('if cmd == "!players"')[1].split("if cmd ==")[0]
check("the !players command reads through the one parser",
      "parse_players(r)" in _players_cmd, _players_cmd[:400])
check("and no longer cuts names at the first comma",
      'split(",")[0]' not in _players_cmd, _players_cmd[:400])
check("the background poll is still the only thing that asks on a schedule",
      _botsrc_2a.count('rcon(hp[0], hp[1], "ListPlayers")') == 2,
      _botsrc_2a.count('rcon(hp[0], hp[1], "ListPlayers")'))
check("and every surface now names a comma'd player the same way",
      [p["name"] for p in _bot_s1.parse_players("0. Cha,rlie, 123")] == ["Cha,rlie"],
      _bot_s1.parse_players("0. Cha,rlie, 123"))

# ---- and it reaches the real Cluster page, from the same poll as the count
_t13 = _aio2.get_event_loop_policy().new_event_loop()
try:
    class _RosterRelay(_LiveRelay):
        online_names = {
            "The Island": [{"name": "Bob", "netid": "7656119800000001"},
                           {"name": "Cha,rlie", "netid": "000255a1b2"},
                           {"name": "Dee", "netid": "19000000000000001"},
                           {"name": "Eve", "netid": "0002a1b2c3d4"}],
            "Ragnarok": [{"name": "unreadable row", "netid": ""}]}

    _rr = _RosterRelay()
    _rr.online_by_map = {"The Island": 4, "Ragnarok": 1}
    _rr.map_up = {"The Island": True, "Ragnarok": True}
    _rr.online_total = 5
    _rr.last_refresh = _time_dead.time() - 25
    _front_r, _cluster_r = _t13.run_until_complete(_page_with(_rr))

    _rr_quiet = _RosterRelay()
    _rr_quiet.online_by_map = {"The Island": 4, "Ragnarok": 1}
    _rr_quiet.map_up = {"The Island": True, "Ragnarok": False}
    _rr_quiet.last_refresh = _time_dead.time() - 25
    _front_q2, _cluster_q2 = _t13.run_until_complete(_page_with(_rr_quiet))

    _front_nr, _cluster_nr = _t13.run_until_complete(_page_with(None))
finally:
    _t13.close()
    _appmod.clusterctl.status = _real_status_s1
    _bot_s1.LIVE = _real_live

check("the Cluster page carries the who's-online section",
      "Who\u2019s online" in _cluster_r, _cluster_r[-1400:])
check("listing the people, one row each",
      _cluster_r.count("<div class=whorow>") == 5,
      _cluster_r.count("<div class=whorow>"))
check("with the comma'd name intact", "Cha,rlie" in _cluster_r, _cluster_r[-1600:])
check("and every row has an actions slot",
      _cluster_r.count("<span class=whoacts>") == 5,
      _cluster_r.count("<span class=whoacts>"))
check("the unreadable row is shown and says what it costs",
      "unreadable row" in _cluster_r and "nothing to act on" in _cluster_r,
      _cluster_r[-1600:])

# the count column and the name list are one poll, so they agree on screen
# Scoped to the roster: "The Island</div>" also ends the relay card's "Cannot reach:"
# line further up the merged page, and _after() returns the span to the NEXT occurrence.
_r_island = _after(_from(_cluster_r, "<fieldset id=who>"), "The Island</div>")
check("a map counted at four lists four names",
      ">4</td>" in _cluster_r and _r_island.count("<div class=whorow>") >= 4,
      _r_island[:400])

# ---- one header, not two
_r_section = _from(_cluster_r, "Who’s online")
check("only the count section states the population",
      "players" not in _r_section.split("<div class=whoroster>")[0],
      _r_section[:300])
check("and only it states the age",
      "ago" not in _r_section.split("<div class=whoroster>")[0], _r_section[:300])
check("the page states the population exactly once",
      _cluster_r.count("players online") == 1, _cluster_r.count("players online"))

# ---- the map order is the status table's
_tbl_order = [m for m in ("The Island", "Ragnarok")
              if ("<td>%s</td>" % m) in _cluster_r]
_roster_part = _from(_cluster_r, "Who\u2019s online")
check("the section lists maps in the order the table above did",
      _in_order(_roster_part, *_tbl_order),
      [(m, _roster_part.find(m)) for m in _tbl_order])

# a map that went quiet leaves both views at once, and says so in both
check("a quiet map's people are gone from the roster",
      "unreadable row" not in _cluster_q2, _cluster_q2[-1400:])
# Scoped to the section, and asserted on the element: the phrase "did not answer the
# last poll" also lives in the dash tooltip above, so looking for it anywhere on the
# page passes with this row deleted.
_q2_section = _from(_cluster_q2, "Who’s online")
check("but the map itself is still listed, as unknown rather than absent",
      '<div class="whorow quiet">' in _q2_section
      and "Ragnarok" in _q2_section, _q2_section[:600])
check("and dashed in the count, not zeroed",
      "&mdash;</td>" in _cluster_q2, _cluster_q2[-1600:])

check("no relay explains itself once, not twice on one page",
      _cluster_nr.count("chat relay is not running") == 1,
      _cluster_nr.count("chat relay is not running"))
check("with the section pointing at the explanation above it",
      "no player count above" in _cluster_nr, _cluster_nr[-800:])

# One page now, so "/" and "/admin/cluster" are the same page rather than two that
# each drew the running-maps table.
check("the front door lands on the page with the roster on it",
      "Who\u2019s online" in _front_r, _front_r[-800:])
check("and the running-maps table is drawn once there, not once per page",
      _front_r.count("<legend>Running now</legend>") == 1,
      _front_r.count("<legend>Running now</legend>"))

# ---- read-only, and it cannot reach back into the relay
_appsrc_2b = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                     encoding="utf-8").read()
_rn = _appsrc_2b.split("def _roster_now")[1].split(chr(10) + "    def ")[0]
check("the roster reader reads and does not ask",
      "online_roster()" in _rn and "rcon" not in _rn and "ListPlayers" not in _rn, _rn)
check("and never a blank page if it cannot",
      "except Exception" in _rn, _rn)

_before = {"The Island": [{"name": "Bob", "netid": "1"}]}


class _Mutable:
    online_by_map = {"The Island": 1}
    online_names = {"The Island": [{"name": "Bob", "netid": "1"}]}
    map_up = {"The Island": True}
    online_total = 1
    last_refresh = 0.0


_mu = _Mutable()
_mu.last_refresh = _time_dead.time() - 5
_bot_s1.LIVE = _mu
try:
    _got, _ = _bot_s1.online_roster()
    _got["The Island"][0]["name"] = "rendered over"
    _got["The Island"].append({"name": "ghost", "netid": "x"})
    check("rendering cannot edit the relay's roster",
          _mu.online_names == _before, _mu.online_names)
finally:
    _bot_s1.LIVE = _real_live

# ---- nothing that touches the cluster moved
for _what, _frag in sorted({
        "the apply gate": "def verify_every_map(store):",
        "the restore gate": "def verify_restored(store, key, note=None):",
        "the stop guard": "ui.render_stop_warning(counts, silent)",
        "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
        "the save-before-stop": "save=lambda: clusterctl.save_and_settle(",
}.items()):
    check("%s is untouched by the display" % _what, _frag in _appsrc_2b, _frag)


# ---- the message route: what it sends, and what it refuses to claim
#
# RCON can tell us the server took the command. It cannot tell us the player read it,
# or was still standing there when it arrived. So every sentence here says "sent".
_sent = []


class _MsgRelay(_LiveRelay):
    online_names = {"The Island": [{"name": "Bob", "netid": "1"},
                                   {"name": "Cha,rlie", "netid": "2"}],
                    "Ragnarok": [{"name": "Dana", "netid": "3"}]}


def _msg_relay(quiet=()):
    r = _MsgRelay()
    r.online_by_map = {"The Island": 2, "Ragnarok": 1}
    r.map_up = {m: (m not in quiet) for m in ("The Island", "Ragnarok")}
    r.online_total = 3
    r.last_refresh = _time_dead.time() - 10
    return r


_real_rw = _bot_s1.rcon_with


async def _fake_rcon(host, port, password, command, timeout=6.0):
    _sent.append({"host": host, "port": port, "command": command})
    return "Server received, But no response!!"


async def _boom_rcon(host, port, password, command, timeout=6.0):
    _sent.append({"host": host, "port": port, "command": command})
    raise TimeoutError("timed out after 10s")


async def _post_message(relay, data, rcon=None):
    _sent[:] = []
    _bot_s1.LIVE = relay
    _bot_s1.rcon_with = rcon or _fake_rcon
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_lstore.get("admin_token"))})
    body = await (await client.post("/admin/player/message", data=data)).text()
    await client.close()
    return body


_t14 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _ok_body = _t14.run_until_complete(_post_message(
        _msg_relay(), {"map": "The Island", "name": "Bob", "text": "dinner in 10"}))
    _ok_ev = _drain2()

    _comma_body = _t14.run_until_complete(_post_message(
        _msg_relay(), {"map": "The Island", "name": "Cha,rlie", "text": "hello"}))
    _comma_sent = list(_sent)
    _drain2()

    _blank_body = _t14.run_until_complete(_post_message(
        _msg_relay(), {"map": "The Island", "name": "Bob", "text": "   "}))
    _blank_sent = list(_sent)
    _drain2()

    _gone_body = _t14.run_until_complete(_post_message(
        _msg_relay(), {"map": "The Island", "name": "Someone Else", "text": "hi"}))
    _gone_sent = list(_sent)
    _drain2()

    _quiet_body = _t14.run_until_complete(_post_message(
        _msg_relay(quiet=("Ragnarok",)),
        {"map": "Ragnarok", "name": "Dana", "text": "hi"}))
    _quiet_sent = list(_sent)
    _drain2()

    _fail_body = _t14.run_until_complete(_post_message(
        _msg_relay(), {"map": "The Island", "name": "Bob", "text": "hi"},
        rcon=_boom_rcon))
    _fail_ev = _drain2()

    _norelay_body = _t14.run_until_complete(_post_message(
        None, {"map": "The Island", "name": "Bob", "text": "hi"}))
    _norelay_sent = list(_sent)
    _drain2()
finally:
    _t14.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

# it sends, to the right map, with the right words
check("a message reaches RCON", len(_comma_sent) == 1, _comma_sent)
check("as ServerChatToPlayer with the name quoted",
      _comma_sent[0]["command"] == 'ServerChatToPlayer "Cha,rlie" hello',
      _comma_sent[0]["command"])
check("and the page says it was sent, naming who and where",
      "Message sent to Bob on The Island." in _ok_body,
      _ok_body[_ok_body.find("Message sent") - 60:][:200])
check("as a note, because it worked",
      "<div class=note>Message sent" in _ok_body, _ok_body[:400])
check("it never claims the player read it",
      "messaged" not in _ok_body.lower() and "delivered" not in _ok_body.lower(),
      "the page claimed more than RCON can tell it")
check("the send is announced, at info",
      ("player.message_sent", "info") in [(i["event"], i["level"]) for i in _ok_ev],
      [(i["event"], i["level"]) for i in _ok_ev])
check("saying who, where and that it came from the web UI",
      any("Bob" in (i.get("text") or "") and "The Island" in (i.get("text") or "")
          and "web UI" in (i.get("text") or "") for i in _ok_ev),
      [i.get("text") for i in _ok_ev])
check("with the message itself in the detail, not the channel line",
      any((i.get("detail") or "") == "dinner in 10" for i in _ok_ev),
      [i.get("detail") for i in _ok_ev])

# ---- the four refusals, none of which send anything
check("blank text is refused", "Type a message first" in _blank_body,
      _blank_body[_blank_body.find("Type a message") - 60:][:200])
check("and nothing was sent", _blank_sent == [], _blank_sent)
check("a player no longer listed is refused",
      "no longer listed on The Island" in _gone_body,
      _gone_body[_gone_body.find("no longer listed") - 80:][:200])
check("without guessing at them", _gone_sent == [], _gone_sent)
check("a map that has gone quiet is refused",
      "did not answer the last check" in _quiet_body,
      _quiet_body[_quiet_body.find("did not answer") - 80:][:200])
check("rather than messaging into the dark", _quiet_sent == [], _quiet_sent)
check("no relay at all is refused",
      "chat relay is not running" in _norelay_body and "nothing was sent"
      in _norelay_body, _norelay_body[_norelay_body.find("relay is not") - 60:][:200])
check("and sends nothing", _norelay_sent == [], _norelay_sent)
for _b in (_blank_body, _gone_body, _quiet_body, _norelay_body):
    check("every refusal says plainly that nothing was sent",
          "nothing was sent" in _b.lower(), _b[:200])

# ---- a send that did not happen is never reported as one
check("an RCON failure is reported as not sent",
      "did NOT send" in _fail_body, _fail_body[_fail_body.find("did NOT") - 80:][:250])
check("naming the reason", "timed out" in _fail_body, _fail_body[:400])
check("and saying nothing reached the server",
      "Nothing reached the server" in _fail_body, _fail_body[:400])
check("in red", "<div class=problem>" in _fail_body, _fail_body[:400])
check("it is announced as a failure, at error",
      ("player.message_failed", "error")
      in [(i["event"], i["level"]) for i in _fail_ev],
      [(i["event"], i["level"]) for i in _fail_ev])
check("and never announced as a send",
      not any(i["event"] == "player.message_sent" for i in _fail_ev),
      [i["event"] for i in _fail_ev])

# "Server received, But no response!!" is the server taking the command, not refusing it
check("the accepted-but-no-output reply counts as sent",
      "Message sent to Bob" in _ok_body, "the usual ARK reply was read as a failure")

for _tail, _want in (("message_sent", "✅"), ("message_failed", "❌")):
    check("%s has an icon of its own" % _tail, _ann2.ICONS.get(_tail) == _want,
          _ann2.ICONS.get(_tail))

# ---- this added a chat write and nothing else
_appsrc_2c = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                     encoding="utf-8").read()
_msgsrc = _appsrc_2c.split("async def player_message")[1].split(
    chr(10) + "    async def ")[0]
check("the route only ever sends a chat line",
      "whisper_command" in _msgsrc and "KickPlayer" not in _msgsrc
      and "BanPlayer" not in _msgsrc, _msgsrc[:400])
check("and checks the roster before it sends",
      _in_order(_msgsrc, "_roster_now()", "whisper_command"), _msgsrc[:600])
for _what, _frag in sorted({
        "the apply gate": "def verify_every_map(store):",
        "the restore gate": "def verify_restored(store, key, note=None):",
        "the stop guard": "ui.render_stop_warning(counts, silent)",
        "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
        "the save-before-stop": "save=lambda: clusterctl.save_and_settle(",
        "the roster read": "def _roster_now():",
}.items()):
    check("%s is untouched" % _what, _frag in _appsrc_2c, _frag)

# ---- forgetting to type is not an error
#
# All four refusals rendered the same red as a send that actually broke. The page has
# had an amber refusal channel since the stop guard, and the restore guards use it for
# exactly this: nothing happened, and nothing is wrong.
for _name_r, _body_r in (("blank text", _blank_body),
                         ("a player who has left", _gone_body),
                         ("a map that went quiet", _quiet_body),
                         ("no relay at all", _norelay_body)):
    check("%s is refused in amber" % _name_r,
          "<div class=warn>" in _body_r, _body_r[:300])
    check("and not in the red kept for a send that broke" ,
          "<div class=problem>" not in _body_r, _body_r[:300])

check("a send that actually failed is still red",
      "<div class=problem>" in _fail_body and "<div class=warn>" not in _fail_body,
      _fail_body[:300])

# ---- a refresh must not send it again
#
# The POST answered with a rendered page while every sibling action on this page
# answers with a redirect, so a refresh re-posted it - and a re-sent message is a
# second line of chat the player sees, from somebody who pressed F5.
_t15 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _send_no_follow():
        _sent[:] = []
        _bot_s1.LIVE = _msg_relay()
        _bot_s1.rcon_with = _fake_rcon
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        r = await client.post("/admin/player/message",
                              data={"map": "The Island", "name": "Bob",
                                    "text": "once only"},
                              allow_redirects=False)
        after = len(_sent)
        where = r.headers.get("Location", "/admin/cluster")
        # the refresh a person actually does: re-request the page they landed on
        page1 = await (await client.get(where)).text()
        page2 = await (await client.get(where)).text()
        await client.close()
        return r.status, after, len(_sent), page1, page2

    _status, _after_post, _after_reload, _p1, _p2 = _t15.run_until_complete(
        _send_no_follow())

    async def _refuse_no_follow():
        _bot_s1.LIVE = _msg_relay()
        _bot_s1.rcon_with = _fake_rcon
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        r = await client.post("/admin/player/message",
                              data={"map": "The Island", "name": "Bob", "text": ""},
                              allow_redirects=False)
        body = await r.text()
        await client.close()
        return r.status, body

    _rstatus, _rbody = _t15.run_until_complete(_refuse_no_follow())
finally:
    _t15.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

check("a successful send answers with a redirect, like its siblings",
      _status == 302, _status)
check("having sent exactly once", _after_post == 1, _after_post)
check("and reloading the page afterwards sends nothing more",
      _after_reload == 1, _after_reload)
check("the result is waiting on the page it redirects to",
      "Message sent to Bob on The Island." in _p1,
      _p1[_p1.find("Message sent") - 60:][:200])
check("shown once, not on every later view",
      "Message sent to Bob on The Island." not in _p2, _p2[:400])
check("a refusal still answers with the page, since nothing was sent to repeat",
      _rstatus == 200 and "Type a message first" in _rbody, [_rstatus, _rbody[:200]])

# ---- a name the command cannot address
#
# ServerChatToPlayer puts the name in quotes and the console has no escape for one
# inside them, so a name containing a quote ended the quoted section early: the message
# addressed somebody else or nobody, while the page said it was sent.
check("a quote in a name is refused by the command builder",
      not _bot_s1.can_whisper('Bob" X'), 'Bob" X')
check("as is a line break, which would end the command outright",
      not _bot_s1.can_whisper("Bob\nX") and not _bot_s1.can_whisper("Bob\rX"),
      "a newline got through")
check("an ordinary name with spaces is fine", _bot_s1.can_whisper("Big Tim"))
_raised = ""
try:
    _bot_s1.whisper_command('Bob" X', "hello")
except ValueError as e:
    _raised = str(e)
check("and building that command raises rather than returning something malformed",
      "cannot be addressed" in _raised, _raised or "it built one anyway")
check("a message's own newlines are collapsed, not left to end the command",
      _bot_s1.whisper_command("Bob", "a\nb") == 'ServerChatToPlayer "Bob" a b',
      _bot_s1.whisper_command("Bob", "a\nb"))

_t16 = _aio2.get_event_loop_policy().new_event_loop()
try:
    class _QuoteRelay(_MsgRelay):
        online_names = {"The Island": [{"name": 'Bob" X', "netid": "1"}],
                        "Ragnarok": []}

    _qr = _QuoteRelay()
    _qr.online_by_map = {"The Island": 1, "Ragnarok": 0}
    _qr.map_up = {"The Island": True, "Ragnarok": True}
    _qr.last_refresh = _time_dead.time() - 10
    _drain2()
    _q_body = _t16.run_until_complete(_post_message(
        _qr, {"map": "The Island", "name": 'Bob" X', "text": "hi"}))
    _q_sent = list(_sent)
    _q_ev = _drain2()
    _q_page = _t16.run_until_complete(_post_message(
        _qr, {"map": "The Island", "name": "nobody", "text": "hi"}))
    _drain2()
finally:
    _t16.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

_q_at = _q_body.find("Who’s online")
# Everything above the roster. A find() that misses returns -1 and [: -1] is
# very nearly the whole page, which would scope this check to nothing at all.
_q_banner = _q_body[:_q_at] if _q_at >= 0 else ""
check("a player whose name has a quote is refused, not sent to",
      "cannot be messaged" in _q_banner, _q_banner[-400:])
check("and the refusal is the banner, not merely the row's own marker",
      "<div class=warn>" in _q_banner and "nothing was sent" in _q_banner.lower(),
      _q_banner[-400:])
check("nothing went to RCON", _q_sent == [], _q_sent)
check("and it was never announced as sent",
      not any(i["event"] == "player.message_sent" for i in _q_ev),
      [i["event"] for i in _q_ev])
check("the refusal says why, in amber",
      "quote or a line break" in _q_banner and "<div class=warn>" in _q_banner,
      _q_banner[-400:])

# ---- the confirmation belongs to whoever pressed the button
#
# It used to be one slot for the whole manager, so on a cluster with two admins
# whichever browser rendered /admin/cluster first took the banner: the person who sent
# the message saw nothing, and somebody who sent nothing was told a message had been
# sent. Harmless for a line of chat. Not harmless for "banned on 8 of 10 maps", and
# every action slice after this one uses the same mechanism.
#
# There is nobody to key it on, either: authed() compares one shared admin_token, so
# two admins present the same cookie and are indistinguishable. The result travels with
# the redirect instead, which belongs to the request that caused it.
_t17 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _two_admins():
        _sent[:] = []
        _bot_s1.LIVE = _msg_relay()
        _bot_s1.rcon_with = _fake_rcon
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        app = build_app(_lstore, docker=DOCKER_UP)
        server = TestServer(app)
        sender = TestClient(server)
        await sender.start_server()
        other = TestClient(server)
        await other.start_server()
        for c in (sender, other):
            c.session.cookie_jar.update_cookies(
                {COOKIE: str(_lstore.get("admin_token"))})

        r = await sender.post("/admin/player/message",
                              data={"map": "The Island", "name": "Bob",
                                    "text": "for the sender only"},
                              allow_redirects=False)
        where = r.headers.get("Location", "/admin/cluster")
        # the other admin happens to refresh first, as they would
        other_first = await (await other.get("/admin/cluster")).text()
        sender_sees = await (await sender.get(where)).text()
        sender_again = await (await sender.get(where)).text()
        other_after = await (await other.get("/admin/cluster")).text()
        await sender.close()
        await other.close()
        return where, other_first, sender_sees, sender_again, other_after

    _where, _other1, _mine, _mine2, _other2 = _t17.run_until_complete(_two_admins())
finally:
    _t17.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

_BANNER = "Message sent to Bob on The Island."
check("the redirect carries a token rather than pointing at a bare page",
      _where.startswith("/admin/cluster?said="), _where)
check("another admin refreshing first does not take the banner",
      _BANNER not in _other1, _other1[:400])
check("the sender sees it on the page they were sent to",
      _BANNER in _mine, _mine[_mine.find("Message sent") - 60:][:200])
check("once, and not on a second look",
      _BANNER not in _mine2, _mine2[:400])
check("and the other admin never sees it at all",
      _BANNER not in _other2, _other2[:400])

# a token nobody parked, or one already spent, is simply nothing
_t18 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _bogus():
        _bot_s1.LIVE = _msg_relay()
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        made_up = await (await client.get("/admin/cluster?said=notatoken")).text()
        empty = await (await client.get("/admin/cluster?said=")).text()
        await client.close()
        return made_up, empty

    _madeup, _emptytok = _t18.run_until_complete(_bogus())
finally:
    _t18.close()
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

check("a token that was never issued shows nothing, and does not break the page",
      "Running now" in _madeup and _BANNER not in _madeup, _madeup[:300])
check("nor does an empty one",
      "Running now" in _emptytok and _BANNER not in _emptytok, _emptytok[:300])

# ---- and the slot cannot grow without bound from redirects nobody follows
_appsrc_n11 = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                      encoding="utf-8").read()
_saysrc = _appsrc_n11.split("def _say_next")[1].split(chr(10) + "    def ")[0]
check("old results are dropped rather than kept for ever",
      "SAID_TTL" in _saysrc, _saysrc[:400])
check("and the slot is capped, for redirects nobody ever follows",
      "SAID_MAX" in _saysrc, _saysrc[:400])
check("the token is not guessable",
      "secrets" in _saysrc, _saysrc[:200])
check("the result is taken out when it is read, not copied",
      "_said.pop(" in _appsrc_n11.split("def _cluster_body")[1][:600],
      _appsrc_n11.split("def _cluster_body")[1][:600])


# ---- the kick route: asked first, sent once, and only ever called "sent"
_kicked = []


class _KickRelay(_LiveRelay):
    online_names = {"The Island": [{"name": "Bob", "netid": "76561198000000001"},
                                   {"name": "Cha,rlie", "netid": "0002a1b2"}],
                    "Ragnarok": [{"name": "Dana", "netid": "19000000000000001"}]}


def _kick_relay(quiet=()):
    r = _KickRelay()
    r.online_by_map = {"The Island": 2, "Ragnarok": 1}
    r.map_up = {m: (m not in quiet) for m in ("The Island", "Ragnarok")}
    r.online_total = 3
    r.last_refresh = _time_dead.time() - 10
    return r


async def _boom_kick(host, port, password, command, timeout=6.0):
    _kicked.append({"host": host, "port": port, "command": command})
    raise TimeoutError("timed out after 10s")


async def _post_kick(relay, data, rcon=None, follow=True):
    _kicked[:] = []
    _bot_s1.LIVE = relay
    _bot_s1.rcon_with = rcon or (
        lambda h, p, pw, c, timeout=6.0: _fake_kick(h, p, pw, c, timeout))
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_lstore.get("admin_token"))})
    r = await client.post("/admin/player/kick", data=data, allow_redirects=False)
    body = await r.text()
    landed = ""
    if follow and r.status == 302:
        landed = await (await client.get(r.headers.get("Location",
                                                       "/admin/cluster"))).text()
    await client.close()
    return r.status, body, landed


async def _fake_kick(host, port, password, command, timeout=6.0):
    _kicked.append({"host": host, "port": port, "command": command})
    return "Server received, But no response!!"


_ISLAND = {"map": "The Island", "name": "Bob", "netid": "76561198000000001"}
_t19 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _ask_st, _ask_body, _ = _t19.run_until_complete(
        _post_kick(_kick_relay(), dict(_ISLAND)))
    _ask_sent = list(_kicked)
    _ask_ev = _drain2()

    _go_st, _go_body, _go_landed = _t19.run_until_complete(
        _post_kick(_kick_relay(), dict(_ISLAND, confirm="1")))
    _go_sent = list(_kicked)
    _go_ev = _drain2()

    _gone_st, _gone_body, _ = _t19.run_until_complete(
        _post_kick(_kick_relay(), {"map": "The Island", "name": "Ghost",
                                   "netid": "99999", "confirm": "1"}))
    _gone_sent = list(_kicked)
    _drain2()

    _quiet_st, _quiet_body, _ = _t19.run_until_complete(
        _post_kick(_kick_relay(quiet=("Ragnarok",)),
                   {"map": "Ragnarok", "name": "Dana",
                    "netid": "19000000000000001", "confirm": "1"}))
    _quiet_sent = list(_kicked)
    _drain2()

    _blank_st, _blank_body, _ = _t19.run_until_complete(
        _post_kick(_kick_relay(), {"map": "The Island", "name": "Bob",
                                   "netid": "", "confirm": "1"}))
    _blank_sent = list(_kicked)
    _drain2()

    _fail_st, _fail_body, _ = _t19.run_until_complete(
        _post_kick(_kick_relay(), dict(_ISLAND, confirm="1"), rcon=_boom_kick))
    _fail_ev = _drain2()
finally:
    _t19.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

# ---- the first press asks, and does nothing
check("pressing Kick asks before it acts", _ask_st == 200, _ask_st)
check("naming the player and the map",
      _in_order(_ask_body, "Kick Bob from The Island?"), _after(_ask_body, "class=warn")[:200])
check("nothing was sent while it was asking", _ask_sent == [], _ask_sent)
check("and nothing was announced either", _ask_ev == [], [i["event"] for i in _ask_ev])
check("the question is asked on the player's own row",
      '<div class="whorow asking">' in _ask_body,
      _after(_ask_body, "whorow asking")[:220])
check("and that row no longer offers a live Kick underneath it",
      "/admin/player/kick" not in
      _after(_ask_body, '<div class="whorow asking">').split("</div>")[0]
      .split("<form")[0] + "",
      _after(_ask_body, "whorow asking")[:300])

# ---- the confirmed press sends, once, to that map only
check("confirming sends the kick", len(_go_sent) == 1, _go_sent)
check("as KickPlayer keyed on the netid",
      _go_sent[0]["command"] == "KickPlayer 76561198000000001", _go_sent[0]["command"])
check("to the map that player is on and no other",
      _go_sent[0]["port"] == [p for l, h, p in
                              _appmod.clusterctl.rcon_targets(_lstore)
                              if l == "The Island"][0],
      _go_sent[0])
check("and it answers with a redirect, so a refresh cannot kick twice",
      _go_st == 302, _go_st)
check("the result lands on the page it redirects to",
      "Kick sent for Bob on The Island." in _go_landed,
      _after(_go_landed, "Kick sent")[:160] or _go_landed[:200])
check("it says sent, never kicked",
      "kicked" not in _go_landed.lower().split("Who")[0], "the page claimed the effect")
check("and says how to find out, without promising an update it does not make",
      "reload to see whether they are off" in _go_landed
      and "next check will show" not in _go_landed,
      _after(_go_landed, "Kick sent")[:220])
_go_names = [(i["event"], i["level"]) for i in _go_ev]
check("the kick is announced at info", ("player.kick_sent", "info") in _go_names,
      _go_names)
check("naming who, where, and that it came from the web UI",
      any("Bob" in (i.get("text") or "") and "The Island" in (i.get("text") or "")
          and "web UI" in (i.get("text") or "") for i in _go_ev),
      [i.get("text") for i in _go_ev])

# "Server received, But no response!!" is acceptance, not refusal
check("the accepted-but-no-output reply counts as sent",
      "Kick sent for Bob" in _go_landed, "the usual ARK reply was read as a failure")

# ---- the refusals, none of which send anything
for _label_r, _st, _body, _sent_r, _phrase in (
        ("a player who is no longer listed", _gone_st, _gone_body, _gone_sent,
         "no longer listed on The Island"),
        ("a map that went quiet", _quiet_st, _quiet_body, _quiet_sent,
         "did not answer the last check"),
        ("a row with no id", _blank_st, _blank_body, _blank_sent,
         "did not say who to kick")):
    check("%s is refused" % _label_r, _st == 200 and _phrase in _body,
          [_st, _after(_body, "class=warn")[:160]])
    check("in amber, because nothing is broken",
          "<div class=warn>" in _body and "<div class=problem>" not in _body,
          _after(_body, "class=warn")[:160])
    check("and nothing was sent", _sent_r == [], _sent_r)

# ---- a kick that did not send is never reported as one
check("an RCON failure says the kick did NOT send",
      "did NOT send" in _fail_body, _after(_fail_body, "did NOT")[:200])
check("naming the reason", "timed out" in _fail_body, _after(_fail_body, "did NOT")[:200])
check("and saying they are still on the map",
      "still on it" in _fail_body, _after(_fail_body, "did NOT")[:200])
check("in red", "<div class=problem>" in _fail_body, _fail_body[:300])
check("announced as a failure, at error",
      ("player.kick_failed", "error") in [(i["event"], i["level"]) for i in _fail_ev],
      [(i["event"], i["level"]) for i in _fail_ev])
check("and never announced as a kick that was sent",
      not any(i["event"] == "player.kick_sent" for i in _fail_ev),
      [i["event"] for i in _fail_ev])

for _tail, _want in (("kick_sent", "✅"), ("kick_failed", "❌")):
    check("%s has an icon of its own" % _tail, _ann2.ICONS.get(_tail) == _want,
          _ann2.ICONS.get(_tail))

# ---- the roster is re-checked, and this added a kick and nothing else
_uisrc_merge = io.open(os.path.join(os.path.dirname(__file__), "ui.py"),
                          encoding="utf-8").read()
_appsrc_2d = io.open(os.path.join(os.path.dirname(__file__), "app.py"),
                     encoding="utf-8").read()
_kicksrc = _after(_appsrc_2d, "async def player_kick").split(
    chr(10) + "    async def ")[0]
check("the route checks the roster before it asks the question",
      _in_order(_kicksrc, "_roster_now()", "if not confirmed", "KickPlayer"),
      _kicksrc[:600])
check("it only ever sends a kick",
      "KickPlayer" in _kicksrc and "BanPlayer" not in _kicksrc
      and "ServerChat" not in _kicksrc, _kicksrc[:400])
check("keyed on the id, not the name",
      'KickPlayer %s" % netid' in _kicksrc, _after(_kicksrc, "KickPlayer")[:120])
for _what, _frag in sorted({
        "the apply gate": "def verify_every_map(store):",
        "the restore gate": "def verify_restored(store, key, note=None):",
        "the stop guard": "ui.render_stop_warning(counts, silent)",
        "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
        "the message action": "async def player_message",
}.items()):
    check("%s is untouched" % _what, _frag in _appsrc_2d, _frag)

# ---- the operator stays where they were looking
#
# The question and the result both rendered at the top of the page, bouncing the
# operator away from the row twice per kick - and while the question was pending, that
# row below still offered a live Kick.
check("the question lands on the row, not in a banner at the top",
      '<div class="whorow asking">' in _ask_body
      and "<div class=warn>" not in _after(_ask_body, "<fieldset id=who>"),
      _after(_ask_body, "whorow asking")[:250])
check("and the page can be landed on at the roster",
      "<fieldset id=who>" in _ask_body, "no anchor")

_t20 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _where_does_it_land():
        _kicked[:] = []
        _bot_s1.LIVE = _kick_relay()
        _bot_s1.rcon_with = _fake_kick
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        r = await client.post("/admin/player/kick", data=dict(_ISLAND, confirm="1"),
                              allow_redirects=False)
        where = r.headers.get("Location", "")
        landed = await (await client.get(where)).text()
        m = await client.post("/admin/player/message",
                              data={"map": "The Island", "name": "Bob", "text": "hi"},
                              allow_redirects=False)
        await client.close()
        return where, landed, m.headers.get("Location", "")

    _kick_where, _kick_landed, _msg_where = _t20.run_until_complete(
        _where_does_it_land())
finally:
    _t20.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

check("the kick redirect points at the roster, not the page top",
      _kick_where.endswith("#who") and "said=" in _kick_where, _kick_where)
check("and so does the message redirect",
      _msg_where.endswith("#who") and "said=" in _msg_where, _msg_where)
check("the result is shown inside the section it is about",
      _in_order(_kick_landed, "<fieldset id=who>", "Kick sent for Bob on The Island."),
      _after(_kick_landed, "<fieldset id=who>")[:300])
# The events feed above the sections now carries the announcement of this same kick,
# which is the log doing its job - a line of history, not a second result banner. What
# must not happen is the RESULT being painted twice.
check("and the result banner is not repeated at the top of the page",
      "<div class=note>Kick sent for Bob" not in
      _kick_landed.split("<fieldset id=who>")[0],
      _kick_landed.split("<fieldset id=who>")[0][-300:])
check("it says reload rather than promising a refresh nothing performs",
      "reload to see whether they are off" in _kick_landed,
      _after(_kick_landed, "Kick sent")[:220])

# ---- the ban route: the heaviest control, and the only one that fans out
#
# A ban is not one command on one map. Every server keeps its own BanList.txt, so a ban
# that reached one map of two looks more like success than anything else on this page
# and is less like it - which is why the partial case gets as many pins here as the
# happy one.
_bansent = []


def _ban_rcon(fail=(), fail_kick=False):
    async def go(host, port, password, command, timeout=6.0):
        _bansent.append({"host": host, "command": command})
        if any(f in host for f in fail):
            raise TimeoutError("timed out after 10s")
        if fail_kick and command.startswith("KickPlayer"):
            raise TimeoutError("timed out after 10s")
        return "Server received, But no response!!"
    return go


# Ten maps, for the outcomes whose wording depends on there being more of them than
# anybody wants listed in a sentence.
_TEN = ([("The Island", "asa-labeltest-island", 27020)]
        + [("Map%d" % i, "asa-labeltest-m%d" % i, 27020 + i) for i in range(1, 10)])


async def _post_ban(data, rcon=None, relay="default", follow=True, targets=None):
    _bansent[:] = []
    _bot_s1.LIVE = _kick_relay() if relay == "default" else relay
    _bot_s1.rcon_with = rcon or _ban_rcon()
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    _real_targets = _appmod.clusterctl.rcon_targets
    if targets is not None:
        _appmod.clusterctl.rcon_targets = lambda store: list(targets)
    try:
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        r = await client.post("/admin/player/ban", data=data, allow_redirects=False)
        body = await r.text()
        landed = ""
        if follow and r.status == 302:
            landed = await (await client.get(r.headers.get("Location",
                                                           "/admin/cluster"))).text()
        await client.close()
    finally:
        _appmod.clusterctl.rcon_targets = _real_targets
    return r.status, body, landed


async def _post_ban_and_refresh(data, rcon=None, relay="default"):
    """Post, land, and then reload the landing the way a browser would.

    The case this exists for: a result rendered straight onto a POST leaves that POST
    in the history, so the reload re-runs the action. What comes back is the status,
    where it sent the browser, the first landing, the reloaded landing, and what was
    sent in total - so "the refresh did nothing" can be asked rather than assumed.
    """
    _bansent[:] = []
    _bot_s1.LIVE = _kick_relay() if relay == "default" else relay
    _bot_s1.rcon_with = rcon or _ban_rcon()
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_lstore.get("admin_token"))})
    r = await client.post("/admin/player/ban", data=data, allow_redirects=False)
    where = r.headers.get("Location", "")
    landed = again = ""
    if r.status == 302:
        landed = await (await client.get(where)).text()
        again = await (await client.get(where)).text()
    else:
        landed = await r.text()
    await client.close()
    return r.status, where, landed, again, list(_bansent)


def _ledger():
    return list(_lstore.data.get("bans") or [])


def _verbs(sent):
    return [x["command"].split(" ")[0] for x in sent]


_BOB = {"map": "The Island", "name": "Bob", "netid": "76561198000000001"}
_t21 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _lstore.data["bans"] = []
    _bask_st, _bask_body, _ = _t21.run_until_complete(_post_ban(dict(_BOB)))
    _bask_sent, _bask_ledger, _bask_ev = list(_bansent), _ledger(), _drain2()

    _bwrong_st, _bwrong_body, _ = _t21.run_until_complete(
        _post_ban(dict(_BOB, confirm="Bobby")))
    _bwrong_sent, _bwrong_ledger = list(_bansent), _ledger()
    _drain2()

    _bgo_st, _bgo_body, _bgo_landed = _t21.run_until_complete(
        _post_ban(dict(_BOB, confirm="  bob ")))
    _bgo_sent, _bgo_ev = list(_bansent), _drain2()
    _bgo_entry = (_ledger() or [None])[-1]

    _lstore.data["bans"] = []
    (_bpart_st, _bpart_where, _bpart_landed, _bpart_again,
     _bpart_after_refresh) = _t21.run_until_complete(
        _post_ban_and_refresh(dict(_BOB, confirm="Bob"),
                              rcon=_ban_rcon(fail=("ragnarok",))))
    _bpart_sent, _bpart_ev = list(_bansent), _drain2()
    _bpart_entry = (_ledger() or [None])[-1]
    _bpart_ledger = _ledger()

    # their own map is the one that refused it: a different sentence from a kick that
    # was sent and failed, because they are different facts
    _lstore.data["bans"] = []
    _bhere_st, _, _bhere_landed, _, _ = _t21.run_until_complete(
        _post_ban_and_refresh(dict(_BOB, confirm="Bob"),
                              rcon=_ban_rcon(fail=("island",))))
    _drain2()

    # ...and a kick that was sent and did not land, on a ban that reached everything
    _lstore.data["bans"] = []
    _bkick_st, _bkick_body, _bkick_landed = _t21.run_until_complete(
        _post_ban(dict(_BOB, confirm="Bob"), rcon=_ban_rcon(fail_kick=True)))
    _drain2()

    # a ten-map cluster that answers on none of them
    _lstore.data["bans"] = []
    _bten_st, _bten_body, _ = _t21.run_until_complete(
        _post_ban(dict(_BOB, confirm="Bob"), rcon=_ban_rcon(fail=("asa-",)),
                  targets=_TEN))
    _bten_ev = _drain2()

    _lstore.data["bans"] = []
    _bnone_st, _bnone_body, _ = _t21.run_until_complete(
        _post_ban(dict(_BOB, confirm="Bob"), rcon=_ban_rcon(fail=("asa-",))))
    _bnone_sent, _bnone_ev, _bnone_ledger = list(_bansent), _drain2(), _ledger()

    _lstore.data["bans"] = []
    _bshape_st, _bshape_body, _ = _t21.run_until_complete(
        _post_ban({"map": "The Island", "name": "Bob", "netid": "76561 198",
                   "confirm": "Bob"}))
    _bshape_sent, _bshape_ledger = list(_bansent), _ledger()
    _drain2()

    _bgone_st, _bgone_body, _ = _t21.run_until_complete(
        _post_ban({"map": "The Island", "name": "Ghost", "netid": "99999",
                   "confirm": "Ghost"}))
    _bgone_sent = list(_bansent)
    _drain2()

    _bquiet_st, _bquiet_body, _ = _t21.run_until_complete(
        _post_ban({"map": "Ragnarok", "name": "Dana", "netid": "19000000000000001",
                   "confirm": "Dana"}, relay=_kick_relay(quiet=("Ragnarok",))))
    _bquiet_sent = list(_bansent)
    _drain2()

    _bdead_st, _bdead_body, _ = _t21.run_until_complete(
        _post_ban(dict(_BOB, confirm="Bob"), relay=None))
    _bdead_sent = list(_bansent)
    _drain2()
finally:
    _t21.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

# ---- pressing Ban asks for the name, and does nothing at all
check("pressing Ban asks before it acts", _bask_st == 200, _bask_st)
check("saying it is the whole cluster, not the one map",
      "Ban Bob from the whole cluster?" in _bask_body,
      _window(_bask_body, "whorow asking", 400))
check("on the player's own row", '<div class="whorow asking">' in _bask_body,
      _bask_body[:200])
check("and that row stops offering a one-press Ban",
      '<button class="whoact worst" type=submit>Ban</button>' not in
      _from(_bask_body, '<div class="whorow asking">').split("</div>")[0],
      _from(_bask_body, '<div class="whorow asking">')[:500])
check("nothing was sent while it was asking", _bask_sent == [], _bask_sent)
check("nothing was announced", _bask_ev == [], [i["event"] for i in _bask_ev])
check("and nothing was written down", _bask_ledger == [], _bask_ledger)
check("the first press does not accuse them of mistyping a name they never typed",
      "That is not their name" not in _bask_body,
      _window(_bask_body, "whorow asking", 400))

# ---- the wrong name is not a near miss
check("a name that is not theirs comes back asking", _bwrong_st == 200, _bwrong_st)
check("saying so", "That is not their name" in _bwrong_body,
      _window(_bwrong_body, "whorow asking", 500))
check("in amber, because nothing is broken",
      "<div class=problem>" not in _bwrong_body, _bwrong_body[:400])
check("with the box still there to type in",
      "name=confirm" in _from(_bwrong_body, '<div class="whorow asking">'),
      _from(_bwrong_body, '<div class="whorow asking">')[:500])
check("nothing was sent for a wrong name", _bwrong_sent == [], _bwrong_sent)
check("and nothing was written down", _bwrong_ledger == [], _bwrong_ledger)

# ---- typed correctly: every map, then the door they are standing in
check("the ban goes to every map, not just theirs",
      sorted(x["command"] for x in _bgo_sent
             if x["command"].startswith("BanPlayer")) ==
      ["BanPlayer 76561198000000001"] * 2,
      [x["command"] for x in _bgo_sent])
check("two maps, two hosts",
      len({x["host"] for x in _bgo_sent
           if x["command"].startswith("BanPlayer")}) == 2,
      [x["host"] for x in _bgo_sent])
check("keyed on the id the server gave, not the name",
      all("Bob" not in x["command"] for x in _bgo_sent),
      [x["command"] for x in _bgo_sent])
check("and they are kicked, because a ban list only stops the next connection",
      _verbs(_bgo_sent).count("KickPlayer") == 1, [x["command"] for x in _bgo_sent])
check("on the map they are standing on",
      [x["host"] for x in _bgo_sent
       if x["command"].startswith("KickPlayer")] == ["asa-labeltest-island"],
      [(x["host"], x["command"]) for x in _bgo_sent])
check("kicked after the ban landed, not before",
      _verbs(_bgo_sent) == ["BanPlayer", "BanPlayer", "KickPlayer"],
      _verbs(_bgo_sent))
check("the operator is redirected, so a refresh cannot ban twice", _bgo_st == 302,
      _bgo_st)
check("the result is shown in the section it is about",
      _in_order(_bgo_landed, "<fieldset id=who>", "Ban sent for Bob on all 2 maps."),
      _after(_bgo_landed, "<fieldset id=who>")[:300])
# The rule is that the banner must not claim the ban took effect - Obelisk sent ten
# commands and heard "Server received, But no response!!" ten times, which is not the
# same as knowing. It is not a ban on the word "banned": since 2f the banner points at
# the Banned players list, and naming a section is not a claim about a player.
_bgo_says = _window(_from(_bgo_landed, "<fieldset id=who>"), "Ban sent for Bob", 400)
for _claim in ("has been banned", "is banned", "now banned", "was banned",
               "Banned Bob", "banned Bob"):
    check("the banner does not say %r" % _claim, _claim not in _bgo_says, _bgo_says)
check("it says the ban was sent", "Ban sent for Bob on all 2 maps." in _bgo_says,
      _bgo_says)
check("and says the list on screen is the old one",
      "reload to see it" in _bgo_says, _bgo_says)
check("announced at info", ("player.ban_sent", "info") in
      [(i["event"], i["level"]) for i in _bgo_ev],
      [(i["event"], i["level"]) for i in _bgo_ev])

# ---- and written down, because nothing on the server will tell us later
check("a real ban is recorded", _bgo_entry is not None, _bgo_entry)
check("with who it was", (_bgo_entry or {}).get("name") == "Bob", _bgo_entry)
check("which id was written to the lists",
      (_bgo_entry or {}).get("netid") == "76561198000000001", _bgo_entry)
check("when it happened",
      abs((_bgo_entry or {}).get("when", 0) - _time_dead.time()) < 300, _bgo_entry)
check("and what each map did with it",
      _bans_app.sent_to(_bgo_entry or {}) == ["Ragnarok", "The Island"]
      and _bans_app.missed(_bgo_entry or {}) == [],
      (_bgo_entry or {}).get("maps"))

# ---- one map short is not success
#
# And it still has to be a redirect. Answered with a page, the partial leaves a POST in
# the browser's history: the reload re-runs ten BanPlayer calls and writes a SECOND
# ledger row for one ban - in the only record that says what this manager banned, which
# 2f is about to show and to hang Unban off.
check("a partial redirects like any other finished action", _bpart_st == 302, _bpart_st)
check("to the roster, carrying a one-shot result",
      _bpart_where.endswith("#who") and "said=" in _bpart_where, _bpart_where)
check("it names the map that did not take it",
      "NOT on Ragnarok" in _from(_bpart_landed, "<fieldset id=who>"),
      _window(_bpart_landed, "Ban sent for Bob", 400))
check("and says they can still get in there",
      "can still join those" in _from(_bpart_landed, "<fieldset id=who>"),
      _window(_bpart_landed, "Ban sent for Bob", 400))
check("ending with something to do about it, like every other refusal here",
      "Try those maps again, or check they are reachable." in
      _from(_bpart_landed, "<fieldset id=who>"),
      _window(_bpart_landed, "Ban sent for Bob", 400))
check("in amber, in the section it is about - something was done, and something not",
      _in_order(_from(_bpart_landed, "<fieldset id=who>"), "<div class=warn>",
                "Ban sent for Bob")
      and "<div class=problem>" not in _bpart_landed,
      _window(_from(_bpart_landed, "<fieldset id=who>"), "<div class=warn>", 300))
# The one-shot slot is emptied by the first render. The feed above still lists the
# announcement, because that is a log and not a result.
check("said once, to whoever pressed the button",
      "Ban sent for Bob" not in _from(_bpart_again, "<fieldset id=who>"),
      _window(_bpart_again, "<fieldset id=who>", 300))
check("and reloading that page bans nobody a second time",
      [x for x in _bpart_after_refresh
       if x["command"].startswith("BanPlayer")] ==
      [x for x in _bpart_sent if x["command"].startswith("BanPlayer")],
      [x["command"] for x in _bpart_after_refresh])
check("nor writes a second row for the one ban", len(_bpart_ledger) == 1,
      _bpart_ledger)
check("announced as a partial, at warning",
      ("player.ban_partial", "warning") in
      [(i["event"], i["level"]) for i in _bpart_ev],
      [(i["event"], i["level"]) for i in _bpart_ev])
check("never announced as a clean ban",
      not any(i["event"] == "player.ban_sent" for i in _bpart_ev),
      [i["event"] for i in _bpart_ev])
check("the map that did take it still kicked them",
      "KickPlayer" in _verbs(_bpart_sent), _verbs(_bpart_sent))
check("a partial is recorded too, since those maps are somebody's problem now",
      _bans_app.sent_to(_bpart_entry or {}) == ["The Island"]
      and [l for l, _w in _bans_app.missed(_bpart_entry or {})] == ["Ragnarok"],
      (_bpart_entry or {}).get("maps"))
check("with the reason the map gave",
      "timed out" in dict(_bans_app.missed(_bpart_entry or {})).get("Ragnarok", ""),
      (_bpart_entry or {}).get("maps"))

# ---- nothing took it: that is a failure, in red, and nothing is claimed
check("a ban that reached no map is not a redirect", _bnone_st == 200, _bnone_st)
check("it says it did NOT send",
      "did NOT send to any of the 2 maps" in _bnone_body,
      _after(_bnone_body, "did NOT")[:260])
check("and that the player is unaffected",
      "still able to play" in _bnone_body, _after(_bnone_body, "did NOT")[:260])
check("in red", "<div class=problem>" in _bnone_body, _bnone_body[:400])
check("announced as a failure, at error",
      ("player.ban_failed", "error") in
      [(i["event"], i["level"]) for i in _bnone_ev],
      [(i["event"], i["level"]) for i in _bnone_ev])
check("and never as one that was sent",
      not any(i["event"] in ("player.ban_sent", "player.ban_partial")
              for i in _bnone_ev), [i["event"] for i in _bnone_ev])
check("nobody is kicked off a server that never took the ban",
      "KickPlayer" not in _verbs(_bnone_sent), _verbs(_bnone_sent))
check("and the ledger is left empty, which is what the page claims",
      _bnone_ledger == [], _bnone_ledger)

# ---- two different reasons somebody is still standing on the map
#
# A kick that was sent and did not land, and a kick that was never sent because the ban
# did not reach that map, are different facts. Wrapped in one sentence they read as a
# stutter that states the same thing twice and explains neither.
_bkick_who = _from(_bkick_landed, "<fieldset id=who>")
_bhere_who = _from(_bhere_landed, "<fieldset id=who>")
check("a kick that was sent and did not land says so",
      "The kick did not send" in _bkick_who,
      _window(_bkick_who, "Ban sent for Bob", 400))
check("naming the reason the map gave", "timed out" in _bkick_who,
      _window(_bkick_who, "Ban sent for Bob", 400))
check("and that they stay there until they log off",
      "until they log off" in _bkick_who, _window(_bkick_who, "Ban sent", 400))
check("a kick that was never sent does not claim it was sent and failed",
      "The kick did not send" not in _bhere_who,
      _window(_bhere_who, "Ban sent for Bob", 400))
check("it says instead that there was no kick, and why",
      "They were not kicked either" in _bhere_who,
      _window(_bhere_who, "Ban sent for Bob", 400))
check("without saying the same thing twice in one sentence",
      _bhere_who.count("did not take the ban") == 1,
      _window(_bhere_who, "Ban sent for Bob", 400))
check("that partial still redirects like the other one", _bhere_st == 302, _bhere_st)
check("and a failed kick does not turn a clean ban into a failure", _bkick_st == 302,
      _bkick_st)

# ---- a list of four out of ten is not a list of the maps that failed
check("a ten-map failure counts ten", "did NOT send to any of the 10 maps" in _bten_body,
      _after(_bten_body, "did NOT")[:400])
check("and says how many it did not print",
      "and 6 more" in _after(_bten_body, "did NOT send to any"),
      _after(_bten_body, "did NOT")[:400])
check("the announcement counts the same way",
      any("and 6 more" in (i.get("text") or "") for i in _bten_ev),
      [i.get("text") for i in _bten_ev])
check("a two-map failure does not invent a remainder",
      "and 0 more" not in _bnone_body and "more" not in
      _window(_bnone_body, "did NOT send to any", 200),
      _window(_bnone_body, "did NOT send to any", 200))

for _tail, _want in (("ban_sent", "✅"), ("ban_partial", "⚠"),
                     ("ban_failed", "❌")):
    check("%s has an icon of its own" % _tail, _ann2.ICONS.get(_tail) == _want,
          _ann2.ICONS.get(_tail))

# ---- an id that is not an id never reaches a ban list
#
# The one guard here that is not about the operator. A kick with a malformed id is a
# command the server declines; a ban with one is a line written into every BanList.txt
# on the cluster, which the game re-reads for ever and nothing here can take back.
check("an id with whitespace in it is refused", _bshape_st == 200, _bshape_st)
check("saying it cannot be written to a ban list safely",
      "not one that can be written to a ban list safely" in _bshape_body,
      _after(_bshape_body, "class=warn")[:260])
check("in amber", "<div class=problem>" not in _bshape_body, _bshape_body[:400])
check("and NOTHING was sent - not even to the first map", _bshape_sent == [],
      _bshape_sent)
check("nor written down", _bshape_ledger == [], _bshape_ledger)

# ---- and the roster is re-read before any of it
for _label_b, _st_b, _body_b, _sent_b, _phrase_b in (
        ("somebody who has left", _bgone_st, _bgone_body, _bgone_sent,
         "no longer listed on The Island"),
        ("a map that went quiet", _bquiet_st, _bquiet_body, _bquiet_sent,
         "did not answer the last check"),
        ("a dead relay", _bdead_st, _bdead_body, _bdead_sent,
         "chat relay is not running")):
    check("%s is refused" % _label_b, _st_b == 200 and _phrase_b in _body_b,
          [_st_b, _after(_body_b, "class=warn")[:200]])
    check("in amber, because nothing is broken",
          "<div class=warn>" in _body_b and "<div class=problem>" not in _body_b,
          _after(_body_b, "class=warn")[:200])
    check("and nothing was sent", _sent_b == [], _sent_b)

# ---- the shape of the route, and what it did not disturb
_bansrc = _after(_appsrc_2d, "async def player_ban").split(
    chr(10) + "    async def ")[0]
check("the id is checked before the roster and long before any command",
      _in_order(_bansrc, "valid_netid", "_roster_now()", "typed_matches", "BanPlayer"),
      _bansrc[:1400])
check("every map is a target, not just the one posted",
      "rcon_targets(store)" in _bansrc, _window(_bansrc, "rcon_targets", 200))
check("sent to all of them at once, so ten maps is not ten waits",
      "asyncio.gather" in _bansrc, _window(_bansrc, "gather", 200))
check("keyed on the id", 'BanPlayer %s" % netid' in _bansrc,
      _window(_bansrc, "BanPlayer", 140))
check("the kick is only attempted where the ban actually landed",
      "if label in took:" in _bansrc, _window(_bansrc, "if label in took", 200))
check("and the ledger is the module, not a dict written here",
      "bansctl.record(" in _bansrc, _window(_bansrc, "bansctl", 140))
for _what_b, _frag_b in sorted({
        "the apply gate": "def verify_every_map(store):",
        "the restore gate": "def verify_restored(store, key, note=None):",
        "the stop guard": "ui.render_stop_warning(counts, silent)",
        "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
        "the message action": "async def player_message",
        "the kick action": "async def player_kick",
}.items()):
    check("%s is untouched by the ban" % _what_b, _frag_b in _appsrc_2d, _frag_b)

# ---- the unban route: the way back out, and the only record that it happened
#
# It keys on the id rather than on a row of the list, because two records of one id is
# a normal thing to have - a ban that reached eight maps and was sent again is two
# honest records - and undoing "entry 3" would leave the same person banned by entry 2.
_unsent = []


def _unban_rcon(fail=()):
    async def go(host, port, password, command, timeout=6.0):
        _unsent.append({"host": host, "command": command})
        if any(f in host for f in fail):
            raise TimeoutError("timed out after 10s")
        return "Server received, But no response!!"
    return go


def _seed_bans(rows):
    _lstore.data["bans"] = [dict(r) for r in rows]


_UB1 = {"name": "Bob", "netid": "76561198000000001", "when": 1789000000,
        "maps": {"The Island": "", "Ragnarok": "timed out"}, "kick": ""}
_UB2 = {"name": "Bob", "netid": "76561198000000001", "when": 1789000900,
        "maps": {"The Island": "", "Ragnarok": ""}, "kick": ""}
_UB3 = {"name": "Dana", "netid": "999888777666555444", "when": 1789000500,
        "maps": {"The Island": "", "Ragnarok": ""}, "kick": ""}
_BOBS = {"netid": "76561198000000001", "name": "Bob", "when": "1789000000"}


async def _post_unban(data, rcon=None, follow=True):
    _unsent[:] = []
    _bot_s1.LIVE = _kick_relay()
    _bot_s1.rcon_with = rcon or _unban_rcon()
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_lstore.get("admin_token"))})
    r = await client.post("/admin/player/unban", data=data, allow_redirects=False)
    body = await r.text()
    where, landed, again = r.headers.get("Location", ""), "", ""
    if follow and r.status == 302:
        landed = await (await client.get(where)).text()
        again = await (await client.get(where)).text()
    await client.close()
    return r.status, where, body, landed, again


_t22 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _seed_bans([_UB1, _UB2, _UB3])
    _uask_st, _, _uask_body, _, _ = _t22.run_until_complete(_post_unban(dict(_BOBS)))
    _uask_sent, _uask_ev = list(_unsent), _drain2()
    _uask_rows = [dict(r) for r in _lstore.data["bans"]]

    _seed_bans([_UB1, _UB2, _UB3])
    _ugo_st, _ugo_where, _, _ugo_landed, _ugo_again = _t22.run_until_complete(
        _post_unban(dict(_BOBS, confirm="1")))
    _ugo_sent, _ugo_ev = list(_unsent), _drain2()
    _ugo_rows = [dict(r) for r in _lstore.data["bans"]]

    _seed_bans([_UB1, _UB2, _UB3])
    _upart_st, _upart_where, _, _upart_landed, _ = _t22.run_until_complete(
        _post_unban(dict(_BOBS, confirm="1"), rcon=_unban_rcon(fail=("ragnarok",))))
    _upart_sent, _upart_ev = list(_unsent), _drain2()
    _upart_rows = [dict(r) for r in _lstore.data["bans"]]

    _seed_bans([_UB1, _UB2, _UB3])
    _unone_st, _, _unone_body, _, _ = _t22.run_until_complete(
        _post_unban(dict(_BOBS, confirm="1"), rcon=_unban_rcon(fail=("asa-",))))
    _unone_sent, _unone_ev = list(_unsent), _drain2()
    _unone_rows = [dict(r) for r in _lstore.data["bans"]]

    _seed_bans([_UB1, _UB2, _UB3])
    _ubad_st, _, _ubad_body, _, _ = _t22.run_until_complete(
        _post_unban({"netid": "765;DoExit", "confirm": "1"}))
    _ubad_sent, _ubad_rows = list(_unsent), [dict(r) for r in _lstore.data["bans"]]
    _drain2()

    _unone_id_st, _, _unone_id_body, _, _ = _t22.run_until_complete(
        _post_unban({"netid": "", "confirm": "1"}))
    _unone_id_sent = list(_unsent)
    _drain2()

    # an id this manager never banned: the unban still goes out, and says so
    _seed_bans([_UB3])
    _uunknown_st, _, _, _uunknown_landed, _ = _t22.run_until_complete(
        _post_unban({"netid": "11112222333344445", "confirm": "1"}))
    _uunknown_sent = list(_unsent)
    _uunknown_rows = [dict(r) for r in _lstore.data["bans"]]
    _drain2()

    # and the by-id field's first press, which has a row for nothing
    _seed_bans([])
    _ubyid_st, _, _ubyid_body, _, _ = _t22.run_until_complete(
        _post_unban({"netid": "11112222333344445"}))
    _ubyid_sent = list(_unsent)
    _drain2()
finally:
    _t22.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

# ---- it asks first, and asking does nothing
check("pressing Unban asks before it acts", _uask_st == 200, _uask_st)
check("naming who", "Unban Bob?" in _uask_body, _window(_uask_body, "whorow asking", 400))
check("in the bans section, not the roster",
      _in_order(_uask_body, "<fieldset id=bans>", "Unban Bob?"),
      _window(_from(_uask_body, "<fieldset id=bans>"), "asking", 300))
check("nothing was sent while it was asking", _uask_sent == [], _uask_sent)
check("nothing was announced", _uask_ev == [], [i["event"] for i in _uask_ev])
check("and nothing was marked", not any(i.get("unbanned") for i in _uask_rows),
      _uask_rows)
check("the by-id field asks the same way",
      _ubyid_st == 200 and "Unban this id?" in _ubyid_body,
      _window(_ubyid_body, "asking", 400))
check("and sends nothing either", _ubyid_sent == [], _ubyid_sent)

# ---- confirmed: every map, because the ban went to every map
check("the unban goes to every map",
      sorted(x["command"] for x in _ugo_sent) ==
      ["UnbanPlayer 76561198000000001"] * 2, [x["command"] for x in _ugo_sent])
check("two maps, two hosts", len({x["host"] for x in _ugo_sent}) == 2,
      [x["host"] for x in _ugo_sent])
check("keyed on the id, never the name",
      all("Bob" not in x["command"] for x in _ugo_sent),
      [x["command"] for x in _ugo_sent])
check("and it bans nobody on the way past",
      not any("BanPlayer" in x["command"] and "Unban" not in x["command"]
              for x in _ugo_sent), [x["command"] for x in _ugo_sent])
check("the operator is redirected, so a refresh cannot unban twice", _ugo_st == 302,
      _ugo_st)
check("to the list it is about", _ugo_where.endswith("#bans") and "said=" in _ugo_where,
      _ugo_where)
check("the result is shown in that section",
      "Unban sent for Bob on all 2 maps" in _from(_ugo_landed, "<fieldset id=bans>"),
      _window(_from(_ugo_landed, "<fieldset id=bans>"), "Unban sent", 300))
_ugo_says = _window(_from(_ugo_landed, "<fieldset id=bans>"), "Unban sent for Bob",
                    300)
check("it says sent, and does not claim they are back",
      "is unbanned" not in _ugo_says and "they can join again" in _ugo_says, _ugo_says)
check("said once, to whoever pressed it",
      "Unban sent for Bob" not in _from(_ugo_again, "<fieldset id=bans>"),
      _window(_ugo_again, "<fieldset id=bans>", 300))
check("announced at info",
      ("player.unban_sent", "info") in [(i["event"], i["level"]) for i in _ugo_ev],
      [(i["event"], i["level"]) for i in _ugo_ev])

# ---- and the record is marked, not removed
check("nothing is deleted from the list", len(_ugo_rows) == 3, _ugo_rows)
check("every record of that id is marked",
      [bool(r.get("unbanned")) for r in _ugo_rows] == [True, True, False], _ugo_rows)
check("which is the point of keying on the id rather than on a row",
      len([r for r in _ugo_rows if r["netid"] == "76561198000000001"]) == 2, _ugo_rows)
check("somebody else's ban is untouched",
      not _ugo_rows[2].get("unbanned"), _ugo_rows[2])
check("and what the ban did is still readable afterwards",
      _bans_app.missed(_ugo_rows[0]) == [("Ragnarok", "timed out")], _ugo_rows[0])
check("the list now says so on the page",
      "unbanned " in _from(_ugo_landed, "<fieldset id=bans>"),
      _window(_from(_ugo_landed, "<fieldset id=bans>"), "unbanned", 200))

# ---- one map short
check("a partial redirects like the whole one", _upart_st == 302, _upart_st)
check("naming the map that did not take it",
      "NOT on Ragnarok" in _from(_upart_landed, "<fieldset id=bans>"),
      _window(_upart_landed, "Unban sent for Bob", 400))
check("and saying they are still banned there",
      "still banned there" in _from(_upart_landed, "<fieldset id=bans>"),
      _window(_upart_landed, "Unban sent for Bob", 400))
check("ending with something to do about it",
      "Try those maps again, or check they are reachable." in
      _from(_upart_landed, "<fieldset id=bans>"),
      _window(_upart_landed, "Unban sent for Bob", 400))
check("in amber, in the section it is about",
      _in_order(_from(_upart_landed, "<fieldset id=bans>"), "<div class=warn>",
                "Unban sent for Bob")
      and "<div class=problem>" not in _upart_landed,
      _window(_from(_upart_landed, "<fieldset id=bans>"), "<div class=warn>", 300))
check("announced as a partial, at warning",
      ("player.unban_partial", "warning") in
      [(i["event"], i["level"]) for i in _upart_ev],
      [(i["event"], i["level"]) for i in _upart_ev])
check("never as a clean one",
      not any(i["event"] == "player.unban_sent" for i in _upart_ev),
      [i["event"] for i in _upart_ev])
check("the records are still marked, because the unban did happen somewhere",
      [bool(r.get("unbanned")) for r in _upart_rows] == [True, True, False],
      _upart_rows)

# ---- and nothing at all
check("an unban that reached no map is not a redirect", _unone_st == 200, _unone_st)
check("it says it did NOT send",
      "did NOT send to any of the 2 maps" in _unone_body,
      _after(_unone_body, "did NOT")[:260])
check("and that they are still banned",
      "still banned" in _unone_body, _after(_unone_body, "did NOT")[:260])
check("in red", "<div class=problem>" in _unone_body, _unone_body[:400])
check("announced as a failure, at error",
      ("player.unban_failed", "error") in
      [(i["event"], i["level"]) for i in _unone_ev],
      [(i["event"], i["level"]) for i in _unone_ev])
check("and never as one that was sent",
      not any(i["event"] in ("player.unban_sent", "player.unban_partial")
              for i in _unone_ev), [i["event"] for i in _unone_ev])
check("NOTHING is marked unbanned when nothing took it - the one lie this list "
      "must not tell", not any(r.get("unbanned") for r in _unone_rows), _unone_rows)

for _tail, _want in (("unban_sent", "✅"), ("unban_partial", "⚠"),
                     ("unban_failed", "❌")):
    check("%s has an icon of its own" % _tail, _ann2.ICONS.get(_tail) == _want,
          _ann2.ICONS.get(_tail))

# ---- the id is checked, because this one can be typed
check("an id with a semicolon in it is refused", _ubad_st == 200, _ubad_st)
check("saying what an id is",
      "platform ids are letters and digits" in _ubad_body,
      _after(_ubad_body, "class=warn")[:260])
check("in amber", "<div class=problem>" not in _ubad_body, _ubad_body[:400])
check("and NOTHING was sent", _ubad_sent == [], _ubad_sent)
check("nor marked", not any(r.get("unbanned") for r in _ubad_rows), _ubad_rows)
check("an empty id is refused too",
      _unone_id_st == 200 and "did not say which id" in _unone_id_body,
      _after(_unone_id_body, "class=warn")[:200])
check("and sends nothing", _unone_id_sent == [], _unone_id_sent)

# ---- an id this manager never banned still works, and says so
check("an id with no record here is still unbanned on every map",
      sorted(x["command"] for x in _uunknown_sent) ==
      ["UnbanPlayer 11112222333344445"] * 2,
      [x["command"] for x in _uunknown_sent])
check("and the page says the list did not change, rather than implying it did",
      "no record of that id" in _from(_uunknown_landed, "<fieldset id=bans>"),
      _window(_uunknown_landed, "Unban sent", 300))
check("nobody else's record was touched",
      not any(r.get("unbanned") for r in _uunknown_rows), _uunknown_rows)

# ---- the shape of the route, and what it left alone
_unsrc = _after(_appsrc_2d, "async def player_unban").split(
    chr(10) + "    async def ")[0]
check("the id is checked before anything is sent",
      _in_order(_unsrc, "valid_netid", "if not confirmed", "UnbanPlayer"),
      _unsrc[:900])
check("every map is a target",
      "rcon_targets(store)" in _unsrc, _window(_unsrc, "rcon_targets", 200))
check("all at once, like the ban", "asyncio.gather" in _unsrc,
      _window(_unsrc, "gather", 200))
check("keyed on the id", 'UnbanPlayer %s" % netid' in _unsrc,
      _window(_unsrc, "UnbanPlayer", 140))
check("the ledger is marked only after something took it",
      _in_order(_unsrc, "if not took:", "mark_unbanned"),
      _window(_unsrc, "if not took", 600))
check("and it marks rather than deletes",
      "mark_unbanned" in _unsrc and "del " not in _unsrc
      and ".remove(" not in _unsrc, _window(_unsrc, "mark_unbanned", 200))
for _what_u, _frag_u in sorted({
        "the ban action": "async def player_ban",
        "the kick action": "async def player_kick",
        "the message action": "async def player_message",
        "the apply gate": "def verify_every_map(store):",
        "the restore gate": "def verify_restored(store, key, note=None):",
        "the stop guard": "ui.render_stop_warning(counts, silent)",
        "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
}.items()):
    check("%s is untouched by the unban" % _what_u, _frag_u in _appsrc_2d, _frag_u)

# ---- the page is told how much the list is NOT showing
#
# The renderer can say "showing 50 of 59" all it likes; this is the pin that the page
# hands it the number. Without it the section reads as the whole history of the
# cluster while quietly holding four times as much.
_t23 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _seed_bans([dict(_UB3, when=1789000000 + _i) for _i in range(51)])
    _ucap_st, _, _ucap_body, _, _ = _t23.run_until_complete(_post_unban({"netid": ""}))
    _drain2()
finally:
    _t23.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1
    _seed_bans([])

_ucap_who = _from(_ucap_body, "<fieldset id=bans>")
check("a list holding more than it shows says so on the page",
      "Showing 50 of 51" in _ucap_who, _window(_ucap_who, "Showing", 200))
check("and says the rest is kept",
      "older bans are kept but not listed" in _ucap_who,
      _window(_ucap_who, "Showing", 200))
check("it still only draws the page it said it was drawing",
      _ucap_who.count(">Unban</button>") == 50, _ucap_who.count(">Unban</button>"))

# ---- and the ban now points at the list it writes to
check("the ban result names where the record went",
      "Recorded - see Banned players below" in _bgo_says, _bgo_says)
check("the page it points at is on the same page, below the roster",
      _in_order(_bgo_landed, "<fieldset id=who>", "<fieldset id=bans>"),
      "sections out of order")

# ---- the cap route: one route, two directions, and no claim about the live list
_csent = []


def _cap_rcon(fail=()):
    async def go(host, port, password, command, timeout=6.0):
        _csent.append({"host": host, "command": command})
        if any(f in host for f in fail):
            raise TimeoutError("timed out after 10s")
        return "Server received, But no response!!"
    return go


def _seed_caps(rows):
    _lstore.data["cap_allows"] = [dict(r) for r in rows]


def _caps_now():
    return [dict(r) for r in (_lstore.data.get("cap_allows") or [])]


_CAPID = "76561198000000001"
_CA1 = {"netid": _CAPID, "when": 1789000000, "maps": {"The Island": "", "Ragnarok": ""}}
_CA2 = {"netid": "999888777666555444", "when": 1789000500,
        "maps": {"The Island": "", "Ragnarok": ""}}


async def _post_cap(data, rcon=None, follow=True):
    _csent[:] = []
    _bot_s1.LIVE = _kick_relay()
    _bot_s1.rcon_with = rcon or _cap_rcon()
    _appmod.clusterctl.status = lambda store: dict(
        _lstatus, services=[dict(x) for x in _lstatus["services"]])
    client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
    await client.start_server()
    client.session.cookie_jar.update_cookies({COOKIE: str(_lstore.get("admin_token"))})
    r = await client.post("/admin/player/cap", data=data, allow_redirects=False)
    body = await r.text()
    where, landed, again = r.headers.get("Location", ""), "", ""
    if follow and r.status == 302:
        landed = await (await client.get(where)).text()
        again = await (await client.get(where)).text()
    await client.close()
    return r.status, where, body, landed, again


_t24 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _drain2()
    _seed_caps([])
    _cask_st, _, _cask_body, _, _ = _t24.run_until_complete(
        _post_cap({"netid": _CAPID, "action": "allow"}))
    _cask_sent, _cask_ev, _cask_rows = list(_csent), _drain2(), _caps_now()

    _seed_caps([])
    _cgo_st, _cgo_where, _, _cgo_landed, _cgo_again = _t24.run_until_complete(
        _post_cap({"netid": _CAPID, "action": "allow", "confirm": "1"}))
    _cgo_sent, _cgo_ev, _cgo_rows = list(_csent), _drain2(), _caps_now()

    _seed_caps([])
    _cpart_st, _, _, _cpart_landed, _ = _t24.run_until_complete(
        _post_cap({"netid": _CAPID, "action": "allow", "confirm": "1"},
                  rcon=_cap_rcon(fail=("ragnarok",))))
    _cpart_ev, _cpart_rows = _drain2(), _caps_now()

    _seed_caps([])
    _cnone_st, _, _cnone_body, _, _ = _t24.run_until_complete(
        _post_cap({"netid": _CAPID, "action": "allow", "confirm": "1"},
                  rcon=_cap_rcon(fail=("asa-",))))
    _cnone_ev, _cnone_rows = _drain2(), _caps_now()

    # ---- and back the other way
    _seed_caps([_CA1, _CA2])
    _crev_st, _crev_where, _, _crev_landed, _ = _t24.run_until_complete(
        _post_cap({"netid": _CAPID, "action": "revoke", "confirm": "1"}))
    _crev_sent, _crev_ev, _crev_rows = list(_csent), _drain2(), _caps_now()

    _seed_caps([_CA1, _CA2])
    _crevnone_st, _, _crevnone_body, _, _ = _t24.run_until_complete(
        _post_cap({"netid": _CAPID, "action": "revoke", "confirm": "1"},
                  rcon=_cap_rcon(fail=("asa-",))))
    _crevnone_rows = _caps_now()
    _drain2()

    # a revoke for an id this manager never allowed
    _seed_caps([_CA2])
    _cunknown_st, _, _, _cunknown_landed, _ = _t24.run_until_complete(
        _post_cap({"netid": "11112222333344445", "action": "revoke", "confirm": "1"}))
    _cunknown_sent, _cunknown_rows = list(_csent), _caps_now()
    _drain2()

    _seed_caps([])
    _cbad_st, _, _cbad_body, _, _ = _t24.run_until_complete(
        _post_cap({"netid": "765;DoExit", "action": "allow", "confirm": "1"}))
    _cbad_sent, _cbad_rows = list(_csent), _caps_now()
    _drain2()

    _cempty_st, _, _cempty_body, _, _ = _t24.run_until_complete(
        _post_cap({"netid": "", "action": "allow", "confirm": "1"}))
    _cempty_sent = list(_csent)
    _drain2()

    _seed_caps([dict(_CA2, when=1789000000 + _i) for _i in range(51)])
    _ccap_st, _, _ccap_body, _, _ = _t24.run_until_complete(
        _post_cap({"netid": "", "action": "allow"}))
    _drain2()
finally:
    _t24.close()
    _bot_s1.rcon_with = _real_rw
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1
    _seed_caps([])

# ---- it asks first
check("pressing Allow asks before it acts", _cask_st == 200, _cask_st)
check("naming the id", "Let %s past the player cap?" % _CAPID in _cask_body,
      _window(_cask_body, "whorow asking", 400))
check("in its own section", _in_order(_cask_body, "<fieldset id=cap>", "past the player cap?"),
      _window(_from(_cask_body, "<fieldset id=cap>"), "asking", 300))
check("nothing was sent while it was asking", _cask_sent == [], _cask_sent)
check("nothing was announced", _cask_ev == [], [i["event"] for i in _cask_ev])
check("and nothing was written down", _cask_rows == [], _cask_rows)

# ---- allow, confirmed
check("the allow goes to every map",
      sorted(x["command"] for x in _cgo_sent) ==
      ["AllowPlayerToJoinNoCheck %s" % _CAPID] * 2, [x["command"] for x in _cgo_sent])
check("two maps, two hosts", len({x["host"] for x in _cgo_sent}) == 2,
      [x["host"] for x in _cgo_sent])
check("and it is the allow command, not the ban's",
      not any("Ban" in x["command"] for x in _cgo_sent),
      [x["command"] for x in _cgo_sent])
check("the operator is redirected, so a refresh cannot send it twice", _cgo_st == 302,
      _cgo_st)
check("to the section it is about",
      _cgo_where.endswith("#cap") and "said=" in _cgo_where, _cgo_where)
check("the result is shown there",
      _in_order(_cgo_landed, "<fieldset id=cap>", "Allow sent for %s" % _CAPID),
      _window(_from(_cgo_landed, "<fieldset id=cap>"), "Allow sent", 300))
check("said once, to whoever pressed it",
      "Allow sent for" not in _cgo_again, _window(_cgo_again, "<fieldset id=cap>", 300))
check("announced at info",
      ("player.cap_allow_sent", "info") in [(i["event"], i["level"]) for i in _cgo_ev],
      [(i["event"], i["level"]) for i in _cgo_ev])
check("and written down, because nothing on the server will say so later",
      len(_cgo_rows) == 1, _cgo_rows)
check("with what each map did",
      sorted((_cgo_rows or [{}]).pop().get("maps", {}).keys()) ==
      ["Ragnarok", "The Island"], _cgo_rows)

# ---- allow, one map short
check("a partial redirects like the whole one", _cpart_st == 302, _cpart_st)
check("naming the map that did not take it",
      "NOT on Ragnarok" in _from(_cpart_landed, "<fieldset id=cap>"),
      _window(_cpart_landed, "Allow sent", 400))
check("and saying what that means for the player",
      "still held to the cap" in _from(_cpart_landed, "<fieldset id=cap>"),
      _window(_cpart_landed, "Allow sent", 400))
check("ending with something to do about it",
      "Try those maps again, or check they are reachable." in
      _from(_cpart_landed, "<fieldset id=cap>"),
      _window(_cpart_landed, "Allow sent", 400))
check("in amber, in its own section",
      _in_order(_cpart_landed, "<fieldset id=cap>", "<div class=warn>", "Allow sent")
      and "<div class=problem>" not in _cpart_landed,
      _window(_from(_cpart_landed, "<fieldset id=cap>"), "<div class=warn>", 300))
check("announced as a partial, at warning",
      ("player.cap_allow_partial", "warning") in
      [(i["event"], i["level"]) for i in _cpart_ev],
      [(i["event"], i["level"]) for i in _cpart_ev])
check("a partial is still written down, since one map did take it",
      len(_cpart_rows) == 1, _cpart_rows)

# ---- allow, nothing at all
check("an allow that reached no map is not a redirect", _cnone_st == 200, _cnone_st)
check("it says it did NOT send",
      "did NOT send to any of the 2 maps" in _cnone_body,
      _after(_cnone_body, "did NOT")[:260])
check("in red", "<div class=problem>" in _cnone_body, _cnone_body[:400])
check("announced as a failure, at error",
      ("player.cap_allow_failed", "error") in
      [(i["event"], i["level"]) for i in _cnone_ev],
      [(i["event"], i["level"]) for i in _cnone_ev])
check("and NOTHING is written down - the log would be claiming a command nobody took",
      _cnone_rows == [], _cnone_rows)

# ---- revoke
check("the revoke sends the other command, to every map",
      sorted(x["command"] for x in _crev_sent) ==
      ["DisallowPlayerToJoinNoCheck %s" % _CAPID] * 2,
      [x["command"] for x in _crev_sent])
check("it redirects to the same section", _crev_st == 302
      and _crev_where.endswith("#cap"), [_crev_st, _crev_where])
check("saying so there",
      "Revoke sent for %s" % _CAPID in _from(_crev_landed, "<fieldset id=cap>"),
      _window(_from(_crev_landed, "<fieldset id=cap>"), "Revoke sent", 300))
check("announced on its own event",
      ("player.cap_revoke_sent", "info") in
      [(i["event"], i["level"]) for i in _crev_ev],
      [(i["event"], i["level"]) for i in _crev_ev])
check("the matching record is marked, not deleted",
      len(_crev_rows) == 2 and [bool(r.get("revoked")) for r in _crev_rows]
      == [True, False], _crev_rows)
check("and somebody else's allow is untouched",
      not any(r.get("revoked") for r in _crev_rows if r["netid"] != _CAPID),
      _crev_rows)
check("a revoke that reached no map marks nothing",
      not any(r.get("revoked") for r in _crevnone_rows), _crevnone_rows)
check("and says so in red",
      _crevnone_st == 200 and "<div class=problem>" in _crevnone_body,
      [_crevnone_st, _crevnone_body[:200]])

# ---- a revoke for an id this manager never allowed
check("an id with no record here is still revoked on every map",
      sorted(x["command"] for x in _cunknown_sent) ==
      ["DisallowPlayerToJoinNoCheck 11112222333344445"] * 2,
      [x["command"] for x in _cunknown_sent])
check("and the page says the log did not change",
      "no record of letting that id past" in
      _from(_cunknown_landed, "<fieldset id=cap>"),
      _window(_cunknown_landed, "Revoke sent", 300))
check("nobody else's record was touched",
      not any(r.get("revoked") for r in _cunknown_rows), _cunknown_rows)

for _tail, _want in (("cap_allow_sent", "✅"), ("cap_allow_partial", "⚠"),
                     ("cap_allow_failed", "❌"), ("cap_revoke_sent", "✅"),
                     ("cap_revoke_partial", "⚠"),
                     ("cap_revoke_failed", "❌")):
    check("%s has an icon of its own" % _tail, _ann2.ICONS.get(_tail) == _want,
          _ann2.ICONS.get(_tail))

# ---- the id is checked, and this one is always typed by hand
check("an id with a semicolon in it is refused", _cbad_st == 200, _cbad_st)
check("saying what an id is",
      "platform ids are letters and digits" in _cbad_body,
      _after(_cbad_body, "class=warn")[:260])
check("in amber", "<div class=problem>" not in _cbad_body, _cbad_body[:400])
check("and NOTHING was sent", _cbad_sent == [], _cbad_sent)
check("nor written down", _cbad_rows == [], _cbad_rows)
check("an empty id is refused too",
      _cempty_st == 200 and "did not say which id" in _cempty_body,
      _after(_cempty_body, "class=warn")[:200])
check("and sends nothing", _cempty_sent == [], _cempty_sent)

# ---- and the page says how much of the log it is showing
_ccap_sec = _from(_ccap_body, "<fieldset id=cap>")
check("a log holding more than it shows says so on the page",
      "Showing 50 of 51" in _ccap_sec, _window(_ccap_sec, "Showing", 200))
check("drawing only the page it said it was drawing",
      _ccap_sec.count(">Revoke</button>") == 50,
      _ccap_sec.count(">Revoke</button>"))

# ---- the shape of the route, and what it left alone
_capsrc = _after(_appsrc_2d, "async def player_cap").split(
    chr(10) + "    async def ")[0]
check("the id is checked before anything is sent",
      _in_order(_capsrc, "valid_netid", "if not confirmed", "PlayerToJoinNoCheck"),
      _capsrc[:900])
check("every map is a target", "rcon_targets(store)" in _capsrc,
      _window(_capsrc, "rcon_targets", 200))
check("all at once, like the ban and the unban", "asyncio.gather" in _capsrc,
      _window(_capsrc, "gather", 200))
check("both directions are keyed on the id",
      'DisallowPlayerToJoinNoCheck %s" if revoking' in _capsrc
      and 'AllowPlayerToJoinNoCheck %s") % netid' in _capsrc,
      _window(_capsrc, "PlayerToJoinNoCheck", 260))
check("the log is written only after a map took it",
      _in_order(_capsrc, "if not took:", "capctl.record("),
      _window(_capsrc, "if not took", 800))
check("and a revoke marks rather than deletes",
      "capctl.mark_revoked" in _capsrc and ".remove(" not in _capsrc,
      _window(_capsrc, "mark_revoked", 200))
check("the word whitelist is nowhere in the route",
      "whitelist" not in _capsrc.lower(), _capsrc[:300])
for _what_c, _frag_c in sorted({
        "the ban action": "async def player_ban",
        "the unban action": "async def player_unban",
        "the kick action": "async def player_kick",
        "the message action": "async def player_message",
        "the apply gate": "def verify_every_map(store):",
        "the restore gate": "def verify_restored(store, key, note=None):",
        "the stop guard": "ui.render_stop_warning(counts, silent)",
        "the integrity gate": "check_worlds=lambda: clusterctl.worlds_intact(",
}.items()):
    check("%s is untouched by the cap" % _what_c, _frag_c in _appsrc_2d, _frag_c)

# ---- Status is gone, and the page it duplicated is the one that remains
#
# Both pages called render_status with the same services and the same poll: the
# running-maps table, the player-count header and the failing banner were drawn twice,
# on two tabs, from one set of facts. Two places to look for one answer, and two places
# for it to be stale differently.
_real_version = _appmod.VERSION_INFO
_t25 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _appmod.VERSION_INFO = {"commit": "abc1234def", "digest": "sha256:feed1234",
                            "published": "sha256:feed1234"}
    _real_relay_info = _appmod.RELAY_INFO
    _appmod.RELAY_INFO = {"total": 2, "reachable": 2}

    async def _merged():
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        _bot_s1.LIVE = _kick_relay()
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        r = await client.get("/", allow_redirects=False)
        where = r.headers.get("Location", "")
        page = await (await client.get("/admin/cluster")).text()
        health = await client.get("/healthz")
        hbody = await health.json()
        # and the same door, to somebody who has not logged in
        bare = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await bare.start_server()
        out = await bare.get("/", allow_redirects=False)
        stranger = out.headers.get("Location", "")
        await bare.close()
        await client.close()
        return r.status, where, page, health.status, hbody, stranger

    (_m_st, _m_where, _merged_pg, _m_health, _m_hbody,
     _m_stranger) = _t25.run_until_complete(_merged())

    async def _merged_idle():
        _appmod.clusterctl.status = lambda store: {
            "docker_ok": True, "compose_exists": True, "running": 0, "services": []}
        _bot_s1.LIVE = None
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        page = await (await client.get("/admin/cluster")).text()
        await client.close()
        return page

    _idle_pg = _t25.run_until_complete(_merged_idle())
finally:
    _t25.close()
    _appmod.VERSION_INFO = _real_version
    _appmod.RELAY_INFO = _real_relay_info
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

# ---- one door
check("the front door redirects rather than rendering", _m_st == 302, _m_st)
check("to the cluster page", _m_where == "/admin/cluster", _m_where)
check("and a stranger is still sent to set up first", _m_stranger == "/setup",
      _m_stranger)
check("healthz is untouched by any of this",
      _m_health == 200 and _m_hbody.get("ok") is True, [_m_health, _m_hbody])

# ---- one running-maps table
check("the running-maps table is drawn once",
      _merged_pg.count("<legend>Running now</legend>") == 1,
      _merged_pg.count("<legend>Running now</legend>"))
check("and the player-count header states the population once",
      _merged_pg.count("players online") == 1, _merged_pg.count("players online"))
# One call in the whole program. It moved out of render_cluster and into the page's
# own composition so it could sit above the events feed - which is a different place,
# not a second one.
check("the duplication is gone at the call, not merely on the page",
      _s1src.count("ui.render_status(") + _uisrc_merge.count("render_status(") == 2,
      [_s1src.count("ui.render_status("), _uisrc_merge.count("render_status(")])
check("and the one that remains is the page composing its own summary",
      "ui.render_status(" in _after(_s1src, "def _summary_band"),
      _window(_s1src, "def _summary_band", 900))
check("the cluster renderer no longer draws it a second time",
      "render_status(" not in _after(_uisrc_merge, "def render_cluster("),
      _window(_after(_uisrc_merge, "def render_cluster("), "return", 300))

# ---- and no way back to a page that no longer exists
check("the nav has no Status tab", ">Status</a>" not in _merged_pg,
      _window(_merged_pg, "<nav>", 400))
check("cluster is the first tab",
      _in_order(_window(_merged_pg, "<nav>", 500), "/admin/cluster", "/admin\"",
                "/admin/data", "/admin/activity"),
      _window(_merged_pg, "<nav>", 500))
_merged_nav = _from(_merged_pg, "<nav>").split("</nav>")[0]
check("and there are four of them, not seven",
      _merged_nav.count("<a href=") == 4, _merged_nav)
check("and it is the one marked as where you are",
      '<a href="/admin/cluster" class=on>Cluster</a>' in _merged_pg,
      _window(_merged_pg, "<nav>", 400))

# ---- what Status brought with it
check("the connect addresses came too",
      "<legend>Connect</legend>" in _merged_pg, "no connect panel")
check("with an address per map",
      _from(_merged_pg, "<legend>Connect</legend>").count("<code>") >= 3,
      _window(_merged_pg, "<legend>Connect</legend>", 400))
check("and the address of Obelisk itself",
      "Obelisk itself:" in _from(_merged_pg, "<legend>Connect</legend>"),
      _window(_merged_pg, "<legend>Connect</legend>", 300))
check("the version panel came too",
      "<legend>Obelisk version</legend>" in _merged_pg
      and "abc1234" in _merged_pg, _window(_merged_pg, "Obelisk version", 300))
check("the recent events came too, compact",
      _in_order(_merged_pg, "<div id=feed", "See everything"),
      _window(_merged_pg, "<div id=feed", 200))
check("live, so it updates without reloading the page under somebody",
      'data-newest="' in _merged_pg
      and "/admin/activity/feed?since=" in _merged_pg,
      _window(_merged_pg, "data-newest", 120))
check("pointing at the full history rather than trying to be it",
      'href="/admin/activity"' in _from(_merged_pg, "<div id=feed"),
      _window(_from(_merged_pg, "<div id=feed"), "activity", 120))
check("the dashboard cards came too",
      _in_order(_merged_pg, "<legend>Right now</legend>", "Chat relay 2/2"),
      _window(_merged_pg, "Right now", 300))
check("which is where the jobs Obelisk runs end to end report themselves",
      "<div class=dash>" in _merged_pg, _window(_merged_pg, "Right now", 300))
check("and the readiness note, when there is nothing running",
      "Cluster not running." in _idle_pg, _window(_idle_pg, "not running", 200))
check("which no longer sends people to a tab they are already on",
      "from the Cluster tab" not in _idle_pg,
      _window(_idle_pg, "not running", 200))
check("a running cluster is not told it is not running",
      "Cluster not running." not in _merged_pg, "the note is unconditional")

# ---- one rendering of update state, not two
#
# render_dashboard drew the build badge, the primed/unsafe card and the apply stepper;
# render_ark_update draws all three again, differently, with the buttons that act on
# them. Two pictures of one state, and the operator has no way to know which is stale.
check("the update panel is on the page", "ARK build and mods" in _merged_pg,
      "no update panel")
# Named one by one, because the cards this page DOES keep use the same badge markup -
# the relay card is a "badge good" too. What must not be here is a second picture of the
# build, the prime, or an apply in flight.
for _twice in ("Primed &amp; verified", "Unsafe &mdash;", "Applying an update",
               "Priming an update", "could not check", "available</div>"):
    check("the dashboard does not draw %r beside the update panel" % _twice,
          _twice not in _from(_merged_pg, "<legend>Right now</legend>").split(
              "</fieldset>")[0],
          _window(_merged_pg, "Right now", 400))
check("the page asks for the cards without the update half",
      "render_dashboard(relay=" in _s1src, _window(_s1src, "render_dashboard", 200))
check("so nothing on it computes an update state for a second renderer",
      "failed=failed" not in _s1src, _window(_s1src, "render_dashboard", 300))

# ---- and the operational half is all still there, under the summary
for _sec, _mark in (("the running-maps table", "<legend>Running now</legend>"),
                    ("who is online", "<fieldset id=who>"),
                    ("the banned list", "<fieldset id=bans>"),
                    ("the cap log", "<fieldset id=cap>"),
                    ("the presets", "<legend>Presets</legend>"),
                    ("the map checkboxes", "<legend>Maps</legend>"),
                    ("the plan", "<legend>Plan</legend>"),
                    ("the moderation controls", "/admin/player/kick"),
                    ("the launch controls", 'formaction="/admin/launch"')):
    check("%s is still on the page" % _sec, _mark in _merged_pg, _sec)

# The page answers, top to bottom: is it up, who is on, is anything broken, what has
# just happened - and then offers the things that act on any of it. The first cut of
# this merge put the events feed above the running-maps block, so a cluster with a map
# crash-looping opened on a history log with the failing banner third.
check("the page is in the order the questions are asked",
      _in_order(_merged_pg,
                "<fieldset id=run>",                  # is it up
                "<div id=feed",                       # what just happened
                "ARK build and mods",                 # the operations
                "<fieldset id=who>", "<fieldset id=bans>", "<fieldset id=cap>",
                "<legend>Presets</legend>", "<legend>Plan</legend>",
                "<fieldset id=connect>"),
      "the page is out of order")
check("the population is stated before the history, not after it",
      _in_order(_merged_pg, "players online", "<div id=feed"),
      "the count is below the feed")
check("and the running table is above the update panel, not below it",
      _in_order(_merged_pg, "<fieldset id=run>", "ARK build and mods"),
      "the table is below the panel")
check("and the reference material sits below it, not between",
      _in_order(_merged_pg, "<legend>Plan</legend>", "<legend>Connect</legend>",
                "<legend>Obelisk version</legend>"),
      "connect and version are not at the foot")

# ---- what a page is FOR, when something is wrong
#
# The whole argument for a landing page: a map is crash-looping and the operator opens
# Obelisk. What they must not meet is a history log, with the red banner third.
_t26 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _BROKE = {"docker_ok": True, "compose_exists": True, "running": 2, "services": [
        {"service": _linst["The Island"], "name": "asa-labeltest-island",
         "level": "ok", "says": "Online", "status": "Up"},
        {"service": _linst["Ragnarok"], "name": "asa-labeltest-ragnarok",
         "level": "bad", "says": "Restarting", "status": "Restarting",
         "log_tail": "Fatal error: could not read the world"}]}

    async def _page_when(status_dict, relay=None):
        _appmod.clusterctl.status = lambda store: dict(
            status_dict, services=[dict(x) for x in status_dict["services"]])
        _bot_s1.LIVE = relay
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        page = await (await client.get("/admin/cluster")).text()
        await client.close()
        return page

    _broke_pg = _t26.run_until_complete(_page_when(_BROKE, _kick_relay()))
    _fresh_pg = _t26.run_until_complete(_page_when(
        {"docker_ok": True, "compose_exists": False, "running": 0, "services": []}))
finally:
    _t26.close()
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

check("a failing map is announced in red", "failing to start" in _broke_pg,
      _window(_broke_pg, "failing", 200))
check("above the history, not below it",
      _in_order(_broke_pg, "failing to start", "<div id=feed"),
      "the failing banner is below the feed")
check("above the update panel too",
      _in_order(_broke_pg, "failing to start", "ARK build and mods"),
      "the failing banner is below the update panel")
check("and above every section that acts on a map",
      _in_order(_broke_pg, "failing to start", "<fieldset id=who>",
                "<legend>Plan</legend>"),
      "the failing banner is below the sections")
check("with the reason still one click away",
      "could not read the world" in _broke_pg, _window(_broke_pg, "why it is", 200))
check("and the table it belongs to right under it",
      _in_order(_broke_pg, "failing to start", "<fieldset id=run>", "<div id=feed"),
      "the table is not under its own banner")

# ---- a machine that has never launched anything
#
# Three moderation sections about servers that do not exist, above the only controls
# that would create them. Empty boxes are not the first thing to read on day one.
check("the first thing offered is the thing to do",
      _in_order(_fresh_pg, "<legend>Presets</legend>", "<fieldset id=maps>",
                "<fieldset id=who>"),
      "the empty sections come first on a fresh install")
check("the moderation sections are still there, underneath",
      _in_order(_fresh_pg, "<fieldset id=who>", "<fieldset id=bans>",
                "<fieldset id=cap>"),
      "a section went missing on a fresh install")
check("and it says there is nothing running yet",
      "has been launched from this Obelisk yet" in _fresh_pg,
      _window(_fresh_pg, "launched", 200))
check("a launched cluster keeps the order the other way round",
      _in_order(_merged_pg, "<fieldset id=who>", "<legend>Presets</legend>"),
      "a running cluster leads with the form")

# ---- and a way around a page this long
check("the page offers its own sections",
      '<div class=jump>' in _merged_pg, _window(_merged_pg, "class=jump", 300))
check("near the top, where somebody deciding where to go is looking",
      _in_order(_merged_pg, "class=jump", "<fieldset id=run>"),
      "the jump row is not at the top")
for _href, _target in (("#run", "<fieldset id=run>"), ("#who", "<fieldset id=who>"),
                       ("#bans", "<fieldset id=bans>"), ("#cap", "<fieldset id=cap>"),
                       ("#maps", "<fieldset id=maps>"),
                       ("#connect", "<fieldset id=connect>")):
    check("%s is offered and lands somewhere" % _href,
          ('href="%s"' % _href) in _merged_pg and _target in _merged_pg,
          [_href, _target])

# ---- the note that used to send people to a tab that no longer exists
check("and the readiness note says where the addresses went",
      "addresses people" in _idle_pg and "foot of this page" in _idle_pg,
      _window(_idle_pg, "not running", 260))
# The note promises something at the foot of the page. For a while the foot held only
# Obelisk's own address, so the promise pointed at a panel that no longer kept what it
# was promising.
_idle_connect = _from(_idle_pg, "<fieldset id=connect>").split("</fieldset>")[0]
check("and what it points at is actually there",
      _idle_connect.count("<tr><td>") >= 1 and "The Island" in _idle_connect,
      _idle_connect)

# ---- one map, in detail, addressed by its own key
#
# The overview answers cluster questions. Ports, RAM, the address people type and the
# saves the game took are none of those: they are a paragraph per map, and ten of them
# were two full-width tables above the answers.
_t27 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _FAKE_POINTS = [{"map": "island", "name": "TheIsland_09.13.2026_04.00.00",
                     "local": "13 Sep 04:00", "ago": "6h ago",
                     "human_size": "412 MB"}]

    async def _map_get(path, points=None):
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        _bot_s1.LIVE = _kick_relay()
        _real_pts = _appmod.pointsctl.list_points
        _real_list = _appmod.backupctl.listing
        if points is not None:
            _appmod.pointsctl.list_points = lambda store, key: list(points)
        # The restore page says "no backups on disk yet" and renders nothing else, so
        # without an archive to list, a pin about what that page does NOT show cannot
        # fail whatever the page does.
        _appmod.backupctl.listing = lambda store: [
            {"name": "obelisk-2026-09-13.tar.zst", "path": "/tmp/x.tar.zst",
             "bytes": 1234567, "mtime": 1789000000.0, "when": "2026-09-13 04:00"}]
        try:
            client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
            await client.start_server()
            client.session.cookie_jar.update_cookies(
                {COOKIE: str(_lstore.get("admin_token"))})
            r = await client.get(path, allow_redirects=False)
            body = await r.text() if r.status == 200 else ""
            where = r.headers.get("Location", "")
            await client.close()
        finally:
            _appmod.pointsctl.list_points = _real_pts
            _appmod.backupctl.listing = _real_list
        return r.status, where, body

    _mp_st, _, _mp_body = _t27.run_until_complete(
        _map_get("/admin/cluster/map/island", points=_FAKE_POINTS))
    _mp_none_st, _, _mp_none_body = _t27.run_until_complete(
        _map_get("/admin/cluster/map/island", points=[]))
    _mp_bad_st, _mp_bad_where, _ = _t27.run_until_complete(
        _map_get("/admin/cluster/map/nosuchmap"))
    _mp_unplanned_st, _, _mp_unplanned_body = _t27.run_until_complete(
        _map_get("/admin/cluster/map/valguero"))
    _mp_rest_st, _mp_rest_where, _mp_rest_body = _t27.run_until_complete(
        _map_get("/admin/restore", points=_FAKE_POINTS))
    _data_st, _, _data_pg = _t27.run_until_complete(
        _map_get("/admin/data", points=_FAKE_POINTS))

    _real_st27 = _appmod.clusterctl.status

    async def _map_down():
        _appmod.clusterctl.status = lambda store: {
            "docker_ok": True, "compose_exists": False, "running": 0, "services": []}
        client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
        await client.start_server()
        client.session.cookie_jar.update_cookies(
            {COOKIE: str(_lstore.get("admin_token"))})
        body = await (await client.get("/admin/cluster/map/island")).text()
        await client.close()
        return body

    try:
        _mp_down_body = _t27.run_until_complete(_map_down())
    finally:
        _appmod.clusterctl.status = _real_st27
finally:
    _t27.close()
    _bot_s1.LIVE = _real_live
    _appmod.clusterctl.status = _real_status_s1

check("a map has a page of its own", _mp_st == 200, _mp_st)
check("headed by the map's name, not its instance",
      "<legend>The Island</legend>" in _mp_body, _window(_mp_body, "id=detail", 200))
check("it shows that map's game port",
      "7777" in _from(_mp_body, "<fieldset id=detail>"),
      _window(_mp_body, "id=detail", 400))
check("and its RCON port",
      "27020" in _from(_mp_body, "<fieldset id=detail>"),
      _window(_mp_body, "id=detail", 400))
check("its RAM, with the reason it got that much",
      _in_order(_from(_mp_body, "<fieldset id=detail>"), "RAM", "base"),
      _window(_mp_body, "id=detail", 400))
check("and its role", "Role" in _from(_mp_body, "<fieldset id=detail>"),
      _window(_mp_body, "id=detail", 400))
check("the address people type is here",
      "7777" in _from(_mp_body, "<legend>Connect</legend>"),
      _window(_mp_body, "<legend>Connect</legend>", 300))
# Scoped to the panel: _from() runs to the end of the document, and the help line
# under the table has a <code> of its own.
_mp_connect = _from(_mp_body, "<legend>Connect</legend>").split("</fieldset>")[0]
check("for this map alone, not a table of ten",
      _mp_connect.count("<td><code>") == 1, _mp_connect)

# ---- the address reads like every other fact on the page
#
# It was a two-column table headed "Map" and "Address" with one row under it - the shape
# it had when it listed ten maps on the overview. The page is already about one map.
_mp_conn = _from(_mp_body, "<fieldset id=connect>").split("</fieldset>")[0]
check("the address is a label and a value",
      "<tr><td>Address</td><td><code>" in _mp_conn, _mp_conn)
check("with no column repeating the map's name",
      "<th>" not in _mp_conn and "<th>Map</th>" not in _mp_conn, _mp_conn)
check("one address, because the page is about one map",
      _mp_conn.count("<tr>") == 1, _mp_conn)
check("and it still says how to use it",
      "Join ARK" in _mp_conn, _mp_conn)

# ---- which of the two states this map is in
#
# The page read byte-identically whether the map was serving or had never been started,
# so clicking a row that said "Online" landed somewhere that did not confirm it.
check("a running map says what Docker says about it",
      "Docker says <b>Online</b> for this map" in _mp_body,
      _window(_mp_body, "Docker says", 200))
check("and points at the one page that keeps that up to date",
      '<a class=maplink href="/admin/cluster#run">Running now</a>' in
      _window(_mp_body, "Docker says", 300), _window(_mp_body, "Docker says", 300))
check("without restating the count, which has one home",
      "players online" not in _mp_body and "players</b>" not in _mp_body,
      _window(_mp_body, "Docker says", 300))
check("a map that is not running says so instead",
      "This map is not running" in _mp_down_body,
      _window(_mp_down_body, "not running", 200))
check("and the two pages are not the same page",
      ("Docker says" in _mp_body) != ("Docker says" in _mp_down_body),
      [("Docker says" in _mp_body), ("Docker says" in _mp_down_body)])

# ---- the saves the game took, on the page about the map they belong to
check("its restore points are here", "Quick restore points" in _mp_body,
      _window(_mp_body, "Quick restore points", 300))
check("offered as the same guarded button the restore page used",
      'action="/admin/restore/point"' in _mp_body
      and 'value="island|TheIsland_09.13.2026_04.00.00"' in _mp_body,
      _window(_mp_body, "restore/point", 400))
check("carrying the consent sentence, not a tooltip",
      "data-confirm=" in _mp_body and "Player characters and tribes are NOT" in _mp_body,
      _window(_mp_body, "data-confirm", 300))
check("a map with no saves yet says so rather than showing nothing",
      "No dated saves for The Island yet" in _mp_none_body,
      _window(_mp_none_body, "Quick restore", 300))
check("the restore section still offers the archives",
      _mp_rest_st == 302 and _mp_rest_where == "/admin/data#restore",
      [_mp_rest_st, _mp_rest_where])
check("the archives are on the Data page it points at",
      _data_st == 200 and "obelisk-2026-09-13.tar.zst" in _data_pg,
      _window(_data_pg, "<legend>Restore", 300))
check("and no longer carries the per-map grid",
      "Quick restore points" not in _data_pg and "pointform" not in _data_pg,
      _window(_data_pg, "Quick restore", 200))
check("nor gathers the points it would need for one",
      "_points_by_map" not in _s1src, "the restore page still collects save points")

# ---- overrides are a link, and stay one
check("the overrides are pointed at, not copied here",
      'href="/admin#g-per-map"' in _mp_body,
      _window(_mp_body, "g-per-map", 200))
check("no second way to write them",
      'name="map:' not in _mp_body, _window(_mp_body, "Settings for this map", 400))
check("and the route adds no write path of its own",
      "add_post(\"/admin/cluster/map" not in _s1src, "a new write path appeared")

# ---- keys all the way down
check("an unknown key is not a page", _mp_bad_st == 302, _mp_bad_st)
check("it goes back to the list of real ones",
      _mp_bad_where == "/admin/cluster#run", _mp_bad_where)
check("a real map that is not in this plan says so",
      _mp_unplanned_st == 200 and "not in this cluster" in _mp_unplanned_body,
      _window(_mp_unplanned_body, "not in", 200))
_mapsrc = _after(_s1src, "async def map_page").split(chr(10) + "    async def ")[0]
check("the route is keyed on the map key, and checks it against the catalogue",
      _in_order(_mapsrc, "match_info", "mapsmod.BY_KEY", "build_plan"), _mapsrc[:600])
check("it finds its plan row by key rather than by name",
      'r.get("map") == key' in _mapsrc, _window(_mapsrc, "plan", 400))
check("and nothing in it turns a name back into a key",
      '["name"]' not in _after(_mapsrc, "rows = []"),
      _window(_mapsrc, "rows = []", 400))

# ---- and the overview is lighter for it
check("the overview no longer tabulates every map's ports",
      "<th class=num>RCON</th>" not in _merged_pg, _window(_merged_pg, "Plan", 300))
# An address is host:game_port, read off the plan row rather than measured, so the
# overview and the map page cannot drift apart the way two readings of live state can -
# and handing somebody every address is a cluster-wide job that ten page visits made
# worse rather than better.
_ov_connect = _from(_merged_pg, "<fieldset id=connect>").split("</fieldset>")[0]
check("the overview lists every map's address",
      _ov_connect.count("<tr><td>") == 2
      and "The Island" in _ov_connect and "Ragnarok" in _ov_connect, _ov_connect)
check("with the port each map was planned on",
      "7777" in _ov_connect, _ov_connect)
check("and Obelisk's own address, which is not a fact about any map",
      "Obelisk itself:" in _ov_connect, _ov_connect)
check("the map page still carries its own, for whoever arrived there",
      "papaship" in _mp_body or "7777" in
      _from(_mp_body, "<fieldset id=connect>").split("</fieldset>")[0],
      _from(_mp_body, "<fieldset id=connect>").split("</fieldset>")[0])
check("the plan still says what it will cost and what is wrong with it",
      "of RAM at most" in _merged_pg, _window(_merged_pg, "<legend>Plan</legend>", 300))
check("and still launches", 'formaction="/admin/launch"' in _merged_pg, "no launch")

# ---- reachable both ways
check("a launched map is reachable from its running row",
      '<a class=maplink href="/admin/cluster/map/island">The Island</a>'
      in _runtable(_merged_pg), _runtable(_merged_pg)[:600])
check("and the class it carries has a style of its own",
      ".maplink{" in _uisrc_merge and ".maplink:visited{" in _uisrc_merge,
      "maplink is unstyled")
check("and a never-launched one from the maps it has chosen",
      _in_order(_fresh_pg, "<fieldset id=maps>", '/admin/cluster/map/island'),
      _window(_fresh_pg, "<fieldset id=maps>", 900))
check("which is the only way in before anything is running",
      "<fieldset id=run>" not in _fresh_pg, _window(_fresh_pg, "Running", 200))

# ---- four tabs become one page, and every form keeps its own address
#
# Backups, off-site, restore and mods were four tabs for one subject. Three of them are
# one story told in order - make a copy, put it somewhere else, put it back - and
# reading it took three tabs, with the archive being restored listed on one page and the
# thing that wrote it on another.
_t28 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _data_and_redirects():
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        _real_list4 = _appmod.backupctl.listing
        _appmod.backupctl.listing = lambda store: [
            {"name": "obelisk-2026-09-13.tar.zst", "path": "/tmp/x.tar.zst",
             "bytes": 1234567, "mtime": 1789000000.0, "when": "2026-09-13 04:00"}]
        # Connected, because half the cloud section only exists once it is: push, pull
        # and the disconnect guard are what this slice has to carry across intact, and
        # an unconnected fixture renders none of them.
        _real_cst = _appmod.cloudctl.status
        _real_cls = _appmod.cloudctl.listing
        _appmod.cloudctl.status = lambda store: {
            "rclone_ok": True, "rclone_detail": "", "encryption_ok": True,
            "connected": True, "provider": "Backblaze B2", "path": "obelisk/",
            "reachable": True, "reachable_detail": ""}
        _appmod.cloudctl.listing = lambda store: (True, [
            {"name": "obelisk-2026-09-12.tar.zst.age", "bytes": 999,
             "when": "2026-09-12 04:00"}])
        try:
            client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
            await client.start_server()
            client.session.cookie_jar.update_cookies(
                {COOKIE: str(_lstore.get("admin_token"))})
            page = await (await client.get("/admin/data")).text()
            # and again with no cloud connected, because the connect form and the
            # disconnect guard are never on the page at the same time
            _appmod.cloudctl.status = lambda store: {
                "rclone_ok": True, "rclone_detail": "", "encryption_ok": True,
                "connected": False, "provider": "", "path": "", "reachable": None,
                "reachable_detail": ""}
            off = await (await client.get("/admin/data")).text()
            olds = {}
            for path in ("/admin/backups", "/admin/restore", "/admin/cloud",
                         "/admin/mods"):
                r = await client.get(path, allow_redirects=False)
                olds[path] = (r.status, r.headers.get("Location", ""))
            bk = await client.get("/admin/backup/status")
            rs = await client.get("/admin/restore/status")
            codes = (bk.status, rs.status)
            health = await client.get("/healthz")
            await client.close()
        finally:
            _appmod.backupctl.listing = _real_list4
            _appmod.cloudctl.status = _real_cst
            _appmod.cloudctl.listing = _real_cls
        return page, off, olds, codes, health.status

    (_data_pg4, _data_off4, _olds4, _poll_codes,
     _health4) = _t28.run_until_complete(_data_and_redirects())
finally:
    _t28.close()
    _appmod.clusterctl.status = _real_status_s1

# ---- one page, four sections, in the order the story runs
def _area(page, name):
    """One section of the Data page, bounded by the next one."""
    return _from(page, '<section id=%s class=area>' % name).split("</section>")[0]


for _sec in ("backups", "cloud", "restore", "mods"):
    check("the Data page has a %s section to land on" % _sec,
          ('<section id=%s class=area>' % _sec) in _data_pg4,
          _window(_data_pg4, "id=%s" % _sec, 140))
check("in the order the work happens in",
      _in_order(_data_pg4, "<section id=backups", "<section id=cloud",
                "<section id=restore", "<section id=mods"),
      "the sections are out of order")
check("each one still renders what its page rendered",
      _in_order(_data_pg4, "Back up now", "Off-site copies", "1. Choose an archive",
                "Mods, in load order"), "a section lost its content")
check("with a row of links to them",
      _in_order(_from(_data_pg4, "<div class=jump>"), '"#backups"', '"#cloud"',
                '"#restore"', '"#mods"'),
      _window(_data_pg4, "<div class=jump>", 300))
check("and the archive it holds is listed under restore",
      "obelisk-2026-09-13.tar.zst" in _data_pg4,
      _area(_data_pg4, "restore")[:600])

# ---- the old addresses still work
for _old, _want in (("/admin/backups", "/admin/data#backups"),
                    ("/admin/restore", "/admin/data#restore"),
                    ("/admin/cloud", "/admin/data#cloud"),
                    ("/admin/mods", "/admin/data#mods")):
    _st, _where = _olds4[_old]
    check("%s still resolves" % _old, _st == 302, [_old, _st])
    check("landing on its own section", _where == _want, [_where, _want])

# ---- and every form still posts where it always did
for _action in ("/admin/backup", "/admin/mods", "/admin/mods/find", "/admin/mods/key",
                "/admin/restore/inspect", "/admin/restore/run",
                "/admin/cloud/disconnect", "/admin/cloud/push", "/admin/cloud/pull"):
    check("a form still posts to %s" % _action,
          ('action="%s"' % _action) in _data_pg4, _action)
# The connect form only exists while no cloud is connected - the two are never on the
# page together, which is why this one is checked against the other render.
check("and the connect form still posts to /admin/cloud/connect",
      'action="/admin/cloud/connect"' in _data_off4,
      _area(_data_off4, "cloud")[:600])
check("which is the only cloud form offered when none is connected",
      'action="/admin/cloud/disconnect"' not in _data_off4,
      _area(_data_off4, "cloud")[:600])
check("no form posts to the page it happens to be rendered on",
      'action="/admin/data"' not in _data_pg4, "a form was re-addressed")

# ---- both jobs still report themselves, on one page, without colliding
check("the backup job has its panel", 'id="bkwrap"' in _data_pg4 or "id=bkwrap"
      in _data_pg4, _window(_data_pg4, "bkwrap", 160))
check("and the restore job has its own", "id=rswrap" in _data_pg4,
      _window(_data_pg4, "rswrap", 160))
check("both pollers are wired",
      "/admin/backup/status" in _data_pg4 and "/admin/restore/status" in _data_pg4,
      "a poller is missing")
check("and both still answer", _poll_codes == (200, 200), _poll_codes)
check("their element ids do not collide",
      not ({"bkwrap", "bkbar", "bkbtn", "bkphase", "bkdetail", "bkresult"}
           & {"rswrap", "rsstep", "rselapsed"}), "ids overlap")
check("each id appears once on the page",
      all(_data_pg4.count("id=%s" % i) == 1 for i in ("bkwrap", "rswrap")),
      [_data_pg4.count("id=bkwrap"), _data_pg4.count("id=rswrap")])

# ---- the guards came across unchanged
check("disconnecting a cloud still takes the typed word",
      "DISCONNECT" in _area(_data_pg4, "cloud"),
      _window(_area(_data_pg4, "cloud"), "disconnect", 400))
_discsrc = _after(_s1src, "async def cloud_disconnect").split(
    chr(10) + "    async def ")[0]
check("and the route still compares it rather than trusting a click",
      "cloudctl.DISCONNECT_WORD" in _discsrc and "confirmed=confirmed" in _discsrc,
      _discsrc[:400])
_runsrc = _after(_s1src, "async def restore_run").split(chr(10) + "    async def ")[0]
check("restoring still needs the archive to have been looked inside",
      "posted != looked" in _runsrc, _window(_runsrc, "looked", 300))
check("and still needs the map's name typed",
      "restorectl.confirms(map_key, confirm)" in _runsrc,
      _window(_runsrc, "confirms", 200))
# The secrets the connect form takes are typed in, never rendered back: the inputs are
# password fields and neither carries a value= for the browser or a screenshot to keep.
_cloud_off = _area(_data_off4, "cloud")
check("the passphrase field is a password field",
      "<input type=password name=password autocomplete=new-password>" in _cloud_off,
      _window(_cloud_off, "password", 200))
check("and it is never rendered back with a value",
      "name=password value" not in _cloud_off and 'name="password" value' not in
      _cloud_off, _window(_cloud_off, "password", 200))
check("nor is the provider's secret key",
      "name=secret_access_key value" not in _cloud_off,
      _window(_cloud_off, "secret_access_key", 200))

# ---- and nothing else moved
check("healthz is unaffected", _health4 == 200, _health4)
for _what, _frag in sorted({
        "the cluster page": 'add_get("/admin/cluster", cluster_page)',
        "the map drill-down": 'add_get("/admin/cluster/map/{key}", map_page)',
        "the ban action": 'add_post("/admin/player/ban", player_ban)',
        "the restore point action": 'add_post("/admin/restore/point", restore_point)',
}.items()):
    check("%s is untouched" % _what, _frag in _s1src, _frag)

# ---- a result lands where the work was, not at the top of a long page
#
# The redirecting actions already did. The ones that rendered their answer left the
# operator at the top of the Data page with the answer half a screen down, and the
# address bar sitting on /admin/restore/run.
_t29 = _aio2.get_event_loop_policy().new_event_loop()
try:
    async def _post_data(path, data, cloud_connected=False):
        _appmod.clusterctl.status = lambda store: dict(
            _lstatus, services=[dict(x) for x in _lstatus["services"]])
        _real_cst9 = _appmod.cloudctl.status
        _real_cls9 = _appmod.cloudctl.listing
        _appmod.cloudctl.status = lambda store: {
            "rclone_ok": True, "rclone_detail": "", "encryption_ok": True,
            "connected": cloud_connected, "provider": "Backblaze B2",
            "path": "obelisk/", "reachable": True, "reachable_detail": ""}
        _appmod.cloudctl.listing = lambda store: (True, [])
        try:
            client = TestClient(TestServer(build_app(_lstore, docker=DOCKER_UP)))
            await client.start_server()
            client.session.cookie_jar.update_cookies(
                {COOKIE: str(_lstore.get("admin_token"))})
            r = await client.post(path, data=data, allow_redirects=False)
            where = r.headers.get("Location", "")
            landed = again = ""
            if r.status == 302 and where.startswith("/admin/data"):
                landed = await (await client.get(where)).text()
                again = await (await client.get(where)).text()
            body = await r.text() if r.status == 200 else ""
            await client.close()
        finally:
            _appmod.cloudctl.status = _real_cst9
            _appmod.cloudctl.listing = _real_cls9
        return r.status, where, body, landed, again

    _cc_st, _cc_where, _cc_body, _cc_landed, _cc_again = _t29.run_until_complete(
        _post_data("/admin/cloud/connect", {"provider": "s3", "password": ""}))
    _ri_st, _ri_where, _, _ri_landed, _ri_again = _t29.run_until_complete(
        _post_data("/admin/restore/inspect", {"archive": "nope.tar.zst"}))
    # A real file, because _archive_path resolves against the backups folder and a
    # name that is not there is refused before the guard this is about is reached.
    _bdir = _appmod.backupctl.backups_dir(_lstore)
    os.makedirs(_bdir, exist_ok=True)
    _real_arc = os.path.join(_bdir, "obelisk-real.tar.zst")
    io.open(_real_arc, "w", encoding="utf-8").write("not really an archive")
    _rr_st, _rr_where, _, _rr_landed, _rr_again = _t29.run_until_complete(
        _post_data("/admin/restore/run", {"archive": "obelisk-real.tar.zst",
                                          "map": "island", "confirm": "The Island"}))
finally:
    _t29.close()
    _appmod.clusterctl.status = _real_status_s1

for _what, _st, _where, _landed, _again, _phrase, _sect in (
        ("a cloud connect with nothing typed", _cc_st, _cc_where, _cc_landed,
         _cc_again, "encryption passphrase is required", "cloud"),
        ("an archive that is not there", _ri_st, _ri_where, _ri_landed, _ri_again,
         "No such archive", "restore"),
        ("a restore with nothing looked inside", _rr_st, _rr_where, _rr_landed,
         _rr_again, "Look inside an archive first", "restore")):
    check("%s redirects rather than rendering" % _what, _st == 302, [_what, _st])
    check("carrying a one-shot result to its own section",
          _where.startswith("/admin/data?said=") and _where.endswith("#" + _sect),
          _where)
    check("and the answer is in that section when it lands",
          _phrase in _area(_landed, _sect), _area(_landed, _sect)[:400])
    check("and said once - reloading that page does not repeat it",
          _phrase not in _area(_again, _sect), _area(_again, _sect)[:300])

check("no answer is left rendered at the top of the page",
      _cc_body == "" and _cc_st != 200, [_cc_st, _cc_body[:80]])

# The other three cloud actions go through one helper, so one of them proves the shape.
_t30 = _aio2.get_event_loop_policy().new_event_loop()
try:
    _dc_st, _dc_where, _dc_body, _dc_landed, _ = _t30.run_until_complete(
        _post_data("/admin/cloud/disconnect", {"confirm": "nope"},
                   cloud_connected=True))
finally:
    _t30.close()
    _appmod.clusterctl.status = _real_status_s1

check("a cloud disconnect answers with a redirect too", _dc_st == 302, _dc_st)
check("to the cloud section, carrying its result",
      _dc_where.startswith("/admin/data?said=") and _dc_where.endswith("#cloud"),
      _dc_where)
check("and nothing is rendered onto the POST", _dc_body == "", _dc_body[:120])
check("the answer is in the cloud section when it lands",
      "<div class=" in _area(_dc_landed, "cloud"), _area(_dc_landed, "cloud")[:300])

# ---- and nothing still points at a tab that no longer exists
check("no page sends anybody to one of the four tabs that were folded in",
      "Backups tab" not in _uisrc_merge and "Cloud tab" not in _uisrc_merge
      and "Restore tab" not in _uisrc_merge and "Mods tab" not in _uisrc_merge,
      "a stale tab pointer survived the fold")

# ---- the seams between four things that were four pages
for _anchor, _title in (("backups", "Back up"), ("cloud", "Off-site"),
                        ("restore", "Restore"), ("mods", "Mods")):
    check("the %s section is headed %r" % (_anchor, _title),
          ('<h2 class=areah>%s</h2>' % _title) in _area(_data_pg4, _anchor),
          _area(_data_pg4, _anchor)[:200])
    check("which is the word the jump row used to get here",
          ('<a href="#%s">%s</a>' % (_anchor, _title)) in _data_pg4,
          _window(_data_pg4, "<div class=jump>", 300))
check("the headings come before the boxes they head",
      _in_order(_data_pg4, "<h2 class=areah>Back up</h2>", "Back up now",
                "<h2 class=areah>Off-site</h2>", "<h2 class=areah>Restore</h2>",
                "1. Choose an archive", "<h2 class=areah>Mods</h2>",
                "Mods, in load order"), "a heading is out of place")
check("and the sections are drawn apart, not stacked",
      ".area{border-top:" in _uisrc_merge, "no rule between sections")

# ---- one colour for "you have not filled it in"
check("a cloud refusal is amber, like every other refusal here",
      "<div class=warn>" in _area(_cc_landed, "cloud")
      and "<div class=problem>" not in _area(_cc_landed, "cloud"),
      _area(_cc_landed, "cloud")[:400])
check("and a real cloud failure is still red",
      "problem=msg" in _after(_s1src, "async def cloud_connect"),
      _window(_s1src, "async def cloud_connect", 900))
check("the two are told apart before anything is attempted",
      _in_order(_after(_s1src, "async def cloud_connect"), "connect_refusal",
                "cloudctl.connect("),
      _window(_s1src, "async def cloud_connect", 900))
check("by asking the module that owns the rule, not by reading its wording",
      "def connect_refusal(" in io.open(
          os.path.join(os.path.dirname(__file__), "cloud.py"),
          encoding="utf-8").read(), "no shared refusal check")

# ---- two jobs, two names
check("the backup panel says which job it is",
      "Backing up" in _data_pg4, _window(_data_pg4, "bkphase", 160))
check("not the word the other one could be showing at the same time",
      ">Working</strong>" not in _data_pg4, _window(_data_pg4, "bkphase", 160))
check("and the restore panel still says its own",
      "Restoring" in _data_pg4, _window(_data_pg4, "rswrap", 200))

print("\nFAILURES: %s" % fails if fails else "\nall app tests passed")
sys.exit(1 if fails else 0)

