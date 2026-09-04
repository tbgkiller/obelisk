"""
The container entrypoint.

The first thing Obelisk owes you is an explanation. Whatever is wrong - no Docker
socket, no cluster yet, a setting it can't use - it has to come up, listen on its port
and say so in the browser. A container that exits because something is missing gives
you a dead container and `connection refused`, which is the same symptom as a wrong
port, a wrong IP, a crashed process and a firewall. That is a black box, and the moment
you most need a working UI is the moment before you have finished setting things up.

So: bootstrap the store, start the web server, and only then look at the world. Docker
being unreachable is a banner on the page, not a reason to die. The chat relay starts
only when there is a cluster to relay between, which on a fresh install there isn't.
"""

import asyncio, logging, os, sys

from . import backup as backupctl
from . import cloud as cloudctl
from . import cluster as clusterctl
from . import dockerctl, install, ui
from .firstrun import bootstrap
from .plan import build_plan
from .settings import Invalid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("obelisk.app")

COOKIE = "obelisk_session"


def docker_state():
    """(ok, message) - checked at boot and shown in the UI, never fatal."""
    try:
        return dockerctl.available()
    except Exception as e:                      # a broken socket must not stop the UI
        return False, "couldn't check Docker: %s" % e


def build_app(store, docker=None):
    """The web application. Separated from serving so tests can drive it directly."""
    from aiohttp import web

    docker = docker if docker is not None else docker_state()

    def authed(request):
        token = str(store.get("admin_token") or "")
        return bool(token) and request.cookies.get(COOKIE) == token

    def chrome(body, title, nav_on=""):
        ok, msg = docker
        if not ok:
            body = ('<div class=problem><strong>Docker not connected.</strong> %s '
                    'Obelisk is running and you can finish setup, but it cannot create '
                    'or manage map containers until this is fixed.</div>%s'
                    % (ui._e(msg), body))
        return web.Response(text=ui.page(title, body, nav_on), content_type="text/html")

    async def setup_page(request):
        if authed(request):
            raise web.HTTPFound("/admin")
        return chrome(ui.render_setup(), "Set up Obelisk")

    async def setup_submit(request):
        form = await request.post()
        token = str(store.get("admin_token") or "")
        if not token or form.get("code", "") != token:
            return chrome(ui.render_setup(error="That code doesn't match. It is printed "
                                                "in the container log at startup."),
                          "Set up Obelisk")
        resp = web.HTTPFound("/admin")
        resp.set_cookie(COOKIE, token, httponly=True, samesite="Lax")
        raise resp

    async def admin(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return chrome(ui.render_settings(store), "Obelisk settings", "/admin")

    async def save(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        from .schema import INSTALL_KEYS
        form = await request.post()
        changes = {k: v for k, v in form.items() if k != "code"}
        # Settings Docker fixed at create time are shown here read-only. A browser that
        # posts them back - an older page, an autofill, a field that was not disabled -
        # must not be able to fail the whole save: the user changed something else and
        # is entitled to have it stick. Drop them and carry on.
        for key in INSTALL_KEYS:
            changes.pop(key, None)
        # A password left blank means "leave it alone", never "erase it".
        for key, value in list(changes.items()):
            if value == "" and _is_password(key):
                changes.pop(key)
        try:
            store.patch(changes)
            store.save()
            install.apply_timezone(store.get("timezone"))
        except Invalid as e:
            return chrome('<div class=problem>%s</div>%s'
                          % (ui._e(str(e)), ui.render_settings(store)),
                          "Obelisk settings", "/admin")
        raise web.HTTPFound("/admin")

    # ---- the cluster: define it, launch it, stop it
    def _cluster_body(request, message="", problem=""):
        try:
            in_use = dockerctl.ports_in_use()
        except Exception:
            in_use = None
        plan = build_plan(store, in_use_ports=in_use)
        st = clusterctl.status(store)
        banner = ""
        if problem:
            banner = '<div class=problem>%s</div>' % ui._e(problem)
        elif message:
            banner = '<div class=note>%s</div>' % ui._e(message)
        return banner + ui.render_cluster(store, plan, status=st)

    async def cluster_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return chrome(_cluster_body(request), "Cluster", "/admin/cluster")

    async def cluster_maps(request):
        """Update the map selection (or apply a preset) without launching anything."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        preset = form.get("preset")
        if preset:
            from .presets import BY_KEY as PRESET_BY_KEY
            chosen = PRESET_BY_KEY.get(preset, {}).get("maps", [])
        else:
            chosen = form.getall("maps", [])
        try:
            store.patch({"maps": ",".join(chosen)})
            store.save()
        except Invalid as e:
            return chrome(_cluster_body(request, problem=str(e)), "Cluster", "/admin/cluster")
        raise web.HTTPFound("/admin/cluster")

    def _act(fn, request):
        ok, msg = fn(store)
        body = _cluster_body(request, message=msg if ok else "", problem="" if ok else msg)
        return chrome(body, "Cluster", "/admin/cluster")

    async def cluster_launch(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return _act(clusterctl.launch, request)

    async def cluster_stop(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return _act(clusterctl.stop, request)

    def _connect_panel():
        """The addresses people actually type, once there are maps to type them for."""
        try:
            plan = build_plan(store)
        except Exception:
            return ""
        if not plan.get("maps"):
            return ""
        host = install.host_address()
        known = host != "<this-host>"
        entries = [(r["name"], "%s:%d" % (host, r["game_port"])) for r in plan["maps"]]
        web = "http://%s:%s/" % (host, store.get("status_port"))
        return ui.render_connect(entries, web_address=web, host_known=known)

    # ---- backups
    def _flush_for(store_):
        """The SaveWorld callable, only when the operator asked for it."""
        if not store_.get("backup_flush"):
            return None
        return lambda: clusterctl.save_world(store_)

    async def backups_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return chrome(ui.render_backups(store, backupctl.listing(store)),
                      "Backups", "/admin/backups")

    async def backup_now(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        ok, msg, _path = backupctl.create(store, flush=_flush_for(store))
        if ok:
            removed = backupctl.prune(store)
            if removed:
                msg += " Removed %d older backup%s." % (
                    len(removed), "" if len(removed) == 1 else "s")
        return chrome(ui.render_backups(store, backupctl.listing(store),
                                        message=msg if ok else "",
                                        problem="" if ok else msg),
                      "Backups", "/admin/backups")

    # ---- cloud
    async def cloud_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        st = cloudctl.status(store)
        rows = []
        if st.get("connected"):
            ok, res = cloudctl.listing(store)
            rows = res if ok and isinstance(res, list) else []
        return chrome(ui.render_cloud(store, st, rows), "Cloud", "/admin/cloud")

    def _cloud_chrome(msg="", problem=""):
        st = cloudctl.status(store)
        rows = []
        if st.get("connected"):
            ok, res = cloudctl.listing(store)
            rows = res if ok and isinstance(res, list) else []
        return chrome(ui.render_cloud(store, st, rows, message=msg, problem=problem),
                      "Cloud", "/admin/cloud")

    async def cloud_connect(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        f = await request.post()
        extra = {k: f.get(k, "") for k in ("access_key_id", "secret_access_key")}
        ok, msg = cloudctl.connect(store,
                                   provider=f.get("provider", ""),
                                   password=f.get("password", ""),
                                   path=f.get("path", "obelisk-backups"),
                                   token=f.get("token", ""),
                                   extra=extra)
        return _cloud_chrome(msg if ok else "", "" if ok else msg)

    async def cloud_disconnect(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        ok, msg = cloudctl.disconnect(store)
        return _cloud_chrome(msg, "")

    async def cloud_push(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        rows = backupctl.listing(store)
        if not rows:
            return _cloud_chrome("", "There is no local backup to upload yet.")
        return _cloud_chrome(backupctl.push_offsite(store, rows[0]["path"]), "")

    async def cloud_pull(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        f = await request.post()
        name = str(f.get("name", "")).strip()
        if not name:
            return _cloud_chrome("", "Give the archive name to download.")
        ok, res = cloudctl.pull(store, name, backupctl.backups_dir(store))
        if not ok:
            return _cloud_chrome("", res)
        return _cloud_chrome("Downloaded and decrypted %s into this Obelisk's backups "
                             "folder." % name, "")

    async def root(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        todo = store.readiness()
        st = clusterctl.status(store)
        if st.get("running"):
            body = ui.render_status(st)
        else:
            body = ('<div class=note>Cluster not running. %s</div>'
                    % (("Still to set: " + ", ".join(b["label"] for b in todo))
                       if todo else "Launch it from the Cluster tab."))
        body += _connect_panel()
        return chrome(body, "Obelisk", "/")

    async def healthz(_request):
        ok, msg = docker
        return web.json_response({"ok": True, "docker": ok, "docker_detail": msg,
                                  "ready": not store.readiness()})

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/setup", setup_page)
    app.router.add_post("/setup", setup_submit)
    app.router.add_get("/admin", admin)
    app.router.add_post("/admin/save", save)
    app.router.add_get("/admin/cluster", cluster_page)
    app.router.add_post("/admin/maps", cluster_maps)
    app.router.add_post("/admin/launch", cluster_launch)
    app.router.add_post("/admin/stop", cluster_stop)
    app.router.add_get("/admin/backups", backups_page)
    app.router.add_post("/admin/backup", backup_now)
    app.router.add_get("/admin/cloud", cloud_page)
    app.router.add_post("/admin/cloud/connect", cloud_connect)
    app.router.add_post("/admin/cloud/disconnect", cloud_disconnect)
    app.router.add_post("/admin/cloud/push", cloud_push)
    app.router.add_post("/admin/cloud/pull", cloud_pull)
    app.router.add_get("/healthz", healthz)
    return app


def _is_password(key):
    from .schema import BY_KEY
    return BY_KEY.get(key, {}).get("type") == "password"


async def serve(store, port):
    from aiohttp import web
    app = build_app(store)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("web UI on http://0.0.0.0:%d/  (setup at /setup)", port)
    while True:                                  # serve forever
        await asyncio.sleep(3600)


async def backup_scheduler(store, interval=60):
    """Fire scheduled backups. One check a minute; the schedule itself decides.

    Whether a run is due is judged against the newest archive on disk rather than a
    remembered "last run", so a restart cannot make it forget a night, and cannot make
    it fire twice for the same slot.
    """
    while True:
        try:
            due, why = backupctl.due(store)
            if due:
                log.info("%s", why)
                flush = (lambda: clusterctl.save_world(store)) if store.get("backup_flush") else None
                ok, msg = backupctl.run_scheduled(store, flush=flush)
                (log.info if ok else log.error)("scheduled backup: %s", msg)
        except Exception as e:                    # a bad night must not kill the loop
            log.error("scheduled backup failed: %s", e)
        await asyncio.sleep(interval)


async def main():
    store, created, code = bootstrap()

    ok, msg = docker_state()
    if ok:
        log.info("docker: %s", msg)
    else:
        # Loud, but not fatal. The same sentence appears in the UI.
        log.warning("docker not connected: %s", msg)
        log.warning("the web UI still works - finish setup there; map containers "
                    "cannot be created until the socket is mounted")

    listen, published, _how = install.derive_ports()
    tasks = []
    if listen > 0:
        log.info("web UI: %s", install.setup_url(published=published))
        tasks.append(asyncio.create_task(serve(store, listen)))
    else:
        log.warning("web UI disabled (port 0)")

    tasks.append(asyncio.create_task(backup_scheduler(store)))

    from . import bot
    if bot.CLUSTER_CONFIGURED:
        log.info("cluster configured - starting the chat relay")
        tasks.append(asyncio.create_task(bot.main()))
    else:
        log.info("no cluster yet - relay idle until maps are launched")

    if not tasks:
        log.error("nothing to run: web UI is off and no cluster is configured")
        return
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
