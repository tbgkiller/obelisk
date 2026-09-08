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

import asyncio, logging, os, sys, time

from . import announce
from . import version as versionctl
from . import backup as backupctl
from . import restore as restorectl
from . import cloud as cloudctl
from . import cluster as clusterctl
from . import dockerctl, gamecfg, install, layout, ui
from . import mods as modsctl
from . import curseforge as cfctl
from . import staging as stagingctl
from . import updates as updatesctl
from . import pending as pendingctl
from . import savepoints as pointsctl
from . import maps as mapsmod
from .firstrun import bootstrap
from .plan import build_plan
from .settings import Invalid, validate as validate_setting

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("obelisk.app")

# Checked in the background and read by the status page: two network round trips to a
# registry, and a status page that waits on the internet is one that hangs when the
# internet is the thing that is broken.
VERSION_INFO = {}

# The last ARK build/mod check. Module level for the same reason VERSION_INFO is: the
# watcher fills it in the background and the cluster page reads it, so opening the page
# never waits on Steam or CurseForge to answer.
ARK_UPDATE = {}

# Relay coverage, measured rather than counted. Announced to Discord already; kept here
# so the dashboard can show the same fact as a colour instead of a sentence.
RELAY_INFO = {}

COOKIE = "obelisk_session"
# Long enough that signing in is a thing you do occasionally, short enough that a
# forgotten browser on someone else's machine does not stay signed in for ever.
COOKIE_DAYS = 30


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
        # A lifetime, rather than the browser-session default this had. Without one the
        # cookie was discarded when the browser closed, so the setup prompt came back for
        # reasons that looked like nothing had happened - and the code needed to answer
        # it had only ever been printed once. That is what locked the owner out.
        store.data["setup_done"] = True
        store.save()
        resp = web.HTTPFound("/admin")
        resp.set_cookie(COOKIE, token, max_age=COOKIE_DAYS * 24 * 3600,
                        httponly=True, samesite="Lax")
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
        # The stat grids post as stat:<family>:<index>. They are not schema settings -
        # they are cells of a sparse array - so they are pulled out before the rest goes
        # anywhere near the validator, and a blank one means "leave this stat unmentioned"
        # rather than "set it to zero".
        # Every family the form rendered is represented, empty or not, so that clearing
        # the last cell in a family reads as "cleared" rather than "never mentioned".
        from .gamesettings import STAT_FAMILIES
        stats = {fam: {} for fam, _n, _h in STAT_FAMILIES}
        for name, raw in form.items():
            if not name.startswith("stat:"):
                continue
            _tag, family, index = name.split(":", 2)
            text = str(raw).strip()
            if text:
                try:
                    stats.setdefault(family, {})[index] = float(text)
                except ValueError:
                    log.info("ignoring unreadable stat cell %s=%r", name, raw)
        if any(n.startswith("stat:") for n in form):
            store.data["stats"] = stats

        # Arrays post as row:<key>:<index>:<field>. A row whose first field is blank is
        # a deleted row; a blank field inside a kept row simply is not written, which is
        # how a two-field engram entry stays a two-field engram entry.
        from .gamesettings import ROW_ARRAYS, ROW_BY_KEY
        posted = {}
        for name, raw in form.items():
            if not name.startswith("row:"):
                continue
            _tag, akey, index, field = name.split(":", 3)
            posted.setdefault(akey, {}).setdefault(index, {})[field] = str(raw).strip()
        if posted:
            rows_out = {}
            for spec in ROW_ARRAYS:
                akey = spec["key"]
                if akey not in posted:
                    continue
                order = sorted(posted[akey], key=lambda x: int(x))
                built = []
                for index in order:
                    got = posted[akey][index]
                    first = spec["fields"][0][0]
                    if not got.get(first):
                        continue                     # no class name: the row is gone
                    built.append([[f, _row_value(kind, got[f])]
                                  for f, _lbl, kind in spec["fields"]
                                  if got.get(f) not in (None, "")])
                rows_out[akey] = built
            store.data.setdefault("rows", {}).update(rows_out)

        # Per-map overrides post as map:<map>:<setting>. Blank means inherit, so it
        # removes the override rather than storing an empty value - "the same as every
        # other map" and "no players allowed" must not be the same keystroke.
        from .schema import BY_KEY as _BY
        map_fields = [n for n in form if n.startswith("map:")]
        for name in map_fields:
            _tag, map_key, setting = name.split(":", 2)
            text = str(form.get(name)).strip()
            # Overrides restart the map they belong to, so they queue like everything
            # else that does - including clearing one, because inheriting the cluster
            # value is a different effective value and costs the same restart.
            if text == "":
                if not (_running_now()
                        and pendingctl.clear_override(store, setting, map_key)):
                    store.data.setdefault("maps", {}).setdefault(
                        map_key, {}).pop(setting, None)
                continue
            try:
                value = validate_setting(setting, text)
            except Invalid as e:
                log.info("per-map override rejected for %s/%s: %s", map_key, setting, e)
                continue
            if pendingctl.stageable(setting) and _running_now():
                pendingctl.stage(store, {setting: value}, map_name=map_key)
            else:
                try:
                    store.patch({setting: value}, map_name=map_key)
                except Invalid as e:
                    log.info("per-map override rejected for %s/%s: %s",
                             map_key, setting, e)
        for map_key in list(store.data.get("maps", {})):
            if not store.data["maps"][map_key]:
                del store.data["maps"][map_key]

        changes = {k: v for k, v in form.items()
                   if k != "code" and not k.startswith("stat:")
                   and not k.startswith("row:") and not k.startswith("map:")}
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
        # Changes that restart servers people are playing on do not happen here. They
        # queue, and the batch lands once - when the cluster is empty, when the window
        # opens, or when somebody decides it is worth interrupting people for. Which is
        # what these settings already did, except invisibly: the value went into the
        # store and waited for whatever Launch came next.
        live, later = _stage_or_apply(changes)
        # Which staging-scope values are *different*, worked out before the patch that
        # makes them the same. The form posts every field on the page, so "a staging
        # key is in this submission" is true of every save ever made - restarting the
        # staging server on that would bounce it, and kill any prime it was in the
        # middle of, every time somebody changed an unrelated setting.
        restage = [k for k in live
                   if pendingctl.scope_of(k) == "staging"
                   and not pendingctl.same(store.get(k), live[k])]
        try:
            store.patch(live)
            store.save()
            if later:
                staged = pendingctl.stage(store, later)
                if staged:
                    announce.say(
                        "change.staged",
                        "%d setting(s) saved and waiting for a safe moment to restart "
                        "the cluster: %s." % (len(staged), ", ".join(sorted(staged))),
                        detail=_pending_detail(),
                        count=pendingctl.count(store))
            install.apply_timezone(store.get("timezone"))
            # Push the game settings back into the INI files the servers actually read.
            # Only the keys the operator has set, only through the line editor, and only
            # where the value really differs from what is already written.
            ok_ini, ini_msg = gamecfg.apply(store)
            if not ok_ini:
                log.warning("game settings not written: %s", ini_msg)
            # Staging-scope settings take effect immediately, because the only thing
            # they disturb is a server with no players on it and a world regenerated
            # every rehearsal. Making those wait for an empty cluster would be treating
            # a free change like an expensive one.
            await _restage_if_needed(restage)
        except Invalid as e:
            return chrome('<div class=problem>%s</div>%s'
                          % (ui._e(str(e)), ui.render_settings(store)),
                          "Obelisk settings", "/admin")
        raise web.HTTPFound("/admin")

    async def _restage_if_needed(changed):
        """Bounce the staging server, but only for a value that actually moved."""
        if not changed:
            return

        def go():
            ok, msg = stagingctl.down(store)
            if not ok:
                return False, msg
            if stagingctl.mode(store) != "always":
                return True, "the staging server is stopped"
            return stagingctl.up(store)

        try:
            ok, msg = await asyncio.to_thread(go)
        except Exception as e:                           # noqa: BLE001 - never fatal
            ok, msg = False, str(e)
        announce.say("staging.reconfigured" if ok else "staging.failed",
                     "Staging server settings changed: %s" % msg,
                     level="info" if ok else "error")

    # ---- the cluster: define it, launch it, stop it
    def _cluster_body(request, message="", problem=""):
        try:
            in_use = clusterctl.other_ports_in_use(store)
        except Exception:
            in_use = None
        plan = build_plan(store, in_use_ports=in_use)
        st = clusterctl.status(store)
        banner = ""
        if problem:
            banner = '<div class=problem>%s</div>' % ui._e(problem)
        elif message:
            banner = '<div class=note>%s</div>' % ui._e(message)
        return (banner + _pending_panel() + _update_panel() +
                ui.render_cluster(store, plan, status=st))

    # The last poll, so opening the page does not go to the network before it renders.
    # A panel that takes two round trips to CurseForge to appear is a panel people
    # learn not to open, and the watcher below refreshes it anyway.
    ujob = {"state": "idle", "step": "", "message": "", "ok": None, "started": 0.0,
            "what": ""}

    def _update_panel():
        try:
            return ui.render_ark_update(
                store, ARK_UPDATE, ready=updatesctl.primed(store), job=ujob,
                owns=updatesctl.owns_updates(store),
                staging_on=stagingctl.enabled(store),
                target=updatesctl.target_key(ARK_UPDATE))
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.warning("could not render the update panel: %s", e)
            return ""

    def _running_now():
        try:
            return int(clusterctl.status(store).get("running") or 0)
        except Exception:                            # noqa: BLE001 - assume nothing runs
            return 0

    def _stage_or_apply(changes, map_name=None):
        """(applied now, queued). Queues only what a running cluster would be hurt by.

        A cluster that is not running has nobody to disturb, so nothing waits - which
        matters most on a fresh install, where every setting is being chosen before the
        first launch and a queue would hold all of them behind an empty-cluster trigger
        that can never fire.
        """
        live, later = pendingctl.split(changes, store, map_name=map_name)
        if later and not _running_now():
            live.update(later)
            later = {}
        return live, later

    def _pending_detail():
        """Every queued change, one per line, for the feed's expandable detail."""
        return "\n".join(
            "%-26s %s -> %s%s" % (r["label"], r["from"], r["to"],
                                  ("  [%s]" % r["map"]) if r["map"] else "")
            for r in pendingctl.rows(store))

    def _pending_panel(players=None):
        try:
            ready = updatesctl.primed(store)
            primed = None
            if ready:
                primed = dict(ready,
                              running=(ARK_UPDATE.get("build") or {}).get("running"))
            return ui.render_pending(pendingctl.rows(store), job=ujob,
                                     players=players, primed=primed)
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.warning("could not render pending changes: %s", e)
            return ""

    async def cluster_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return chrome(_cluster_body(request), "Cluster", "/admin/cluster")

    async def pending_post(request):
        """Discard one, discard all, or apply the batch now."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        if form.get("drop"):
            map_key, _, key = str(form["drop"]).partition("|")
            gone = pendingctl.discard(store, key, map_name=map_key or None)
            if gone:
                announce.say("change.discarded",
                             "Dropped a queued change to %s%s."
                             % (key, (" on " + map_key) if map_key else ""),
                             remaining=pendingctl.count(store))
        elif form.get("discard") == "all":
            gone = pendingctl.discard(store)
            if gone:
                announce.say("change.discarded",
                             "Dropped all %d queued change(s)." % gone)
        elif form.get("apply"):
            if ujob["state"] == "running" or cluster_busy.locked():
                raise web.HTTPFound("/admin/cluster")
            ujob.update(state="running", ok=None, message="", step="starting",
                        what="apply", started=time.time())
            asyncio.create_task(_apply_task(bool(form.get("force"))))
        raise web.HTTPFound("/admin/cluster")

    # ---- ARK updates: prime, apply, and what either is doing
    def _ark_root():
        return layout.ark_root_of(store)

    def _note_update(text):
        ujob["step"] = text
        announce.say("ark.phase", text)

    async def _prime_task():
        try:
            async with cluster_busy:
                # The fingerprint of what is being staged goes with it, so a manual
                # prime and an automatic one record the same thing and neither has to
                # be repeated because the other did not say what it staged.
                ok, msg, _detail = await asyncio.to_thread(
                    lambda: updatesctl.prime(
                        store, _ark_root(), _note_update,
                        target=updatesctl.target_key(ARK_UPDATE)))
        except Exception as e:                       # noqa: BLE001 - surfaced below
            ok, msg = False, "Priming failed: %s" % e
            log.exception("priming failed")
        ujob.update(state="done", ok=ok, message=msg, step="done")

    async def update_prime(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        if ujob["state"] == "running" or cluster_busy.locked():
            raise web.HTTPFound("/admin/cluster")
        ujob.update(state="running", ok=None, message="", step="starting",
                    what="prime", started=time.time())
        asyncio.create_task(_prime_task())
        raise web.HTTPFound("/admin/cluster")

    def _apply_now(force):
        """Everything the swap needs from the cluster, wired to the real thing here.

        updates.apply_update takes these as arguments so the whole routine can be tested
        without a Docker socket - the module decides the order and the refusals, this
        decides what the verbs actually do.
        """
        def warn(minutes, build):
            from . import bot
            password = str(store.get("admin_password") or "")
            text = ("Server restarting in %d minutes to apply ARK build %s"
                    % (minutes, build))
            for _label, host, port in clusterctl.running_instances(store):
                try:
                    clusterctl.run_coroutine(
                        bot.rcon_with(host, port, password,
                                      "ServerChat %s" % text, timeout=20))
                except Exception as e:               # noqa: BLE001 - best effort
                    log.info("could not warn one map: %s", e)
            time.sleep(minutes * 60)

        def stop_all():
            ok_s, msg_s = stagingctl.down(store)
            if not ok_s:
                log.warning("the staging server did not stop cleanly: %s", msg_s)
            return clusterctl.stop(store)

        def verify_all():
            """The six gates, per map, after waiting for each to actually be serving."""
            results = {}
            for key in clusterctl._map_keys(store):
                ok_h, _why = clusterctl.wait_healthy(store, key)
                results[key] = bool(ok_h) and clusterctl.verify_instance(store, key)[0]
            return all(results.values()), results

        return updatesctl.apply_batch(
            store, _ark_root(), warn=warn, save=lambda: clusterctl.save_world(store),
            stop_all=stop_all, start_all=lambda: clusterctl.launch(store),
            verify=verify_all,
            players=lambda: clusterctl.players_online(store), force=force,
            on_step=_note_update)

    async def _apply_task(force):
        try:
            async with cluster_busy:
                ok, msg, _detail = await asyncio.to_thread(_apply_now, force)
        except Exception as e:                       # noqa: BLE001 - surfaced below
            ok, msg = False, "Applying the update failed: %s" % e
            log.exception("applying the update failed")
        ujob.update(state="done", ok=ok, message=msg, step="done")

    async def update_apply(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        if ujob["state"] == "running" or cluster_busy.locked():
            raise web.HTTPFound("/admin/cluster")
        ujob.update(state="running", ok=None, message="", step="starting",
                    what="apply", started=time.time())
        asyncio.create_task(_apply_task(bool(form.get("force"))))
        raise web.HTTPFound("/admin/cluster")

    # ---- the activity feed
    #
    # Everything that reaches the Discord admin channel, and more of it. The complaint
    # that produced this was exactly right: the channel knew more than the UI, which is
    # backwards for a product whose premise is that the UI is enough on its own.
    def _jobs():
        """The operations that can be in flight, named the way a person would say them."""
        return {"Backup": job, "Restore": rjob,
                ("Update" if ujob.get("what") != "prime" else "Priming"): ujob}

    async def activity_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        items = announce.recent(limit=200)
        body = _dashboard() + ui.render_events(items, jobs=_jobs())
        body = body.replace("<div id=feed>",
                            '<div id=feed data-newest="%d">' % announce.newest_id(), 1)
        return chrome(body + ui.FEED_LIVE, "Activity", "/admin/activity")

    async def activity_feed(request):
        """Only what the page has not already got, so polling is cheap."""
        if not authed(request):
            return web.json_response({"html": ""}, status=403)
        try:
            since = int(request.query.get("since") or 0)
        except ValueError:
            since = 0
        fresh = announce.recent(limit=50, since=since)
        # The same builders the page used, so the rows appended by polling cannot look
        # or behave differently from the ones rendered server-side.
        return web.json_response({
            "html": ui.event_rows(fresh, compact=True) if fresh else "",
            "newest": announce.newest_id(),
            "running": ui.running_rows(_jobs()),
            "dash": _dashboard(),
        })

    def _ujob_live():
        out = dict(ujob)
        out["elapsed"] = int(time.time() - ujob["started"]) if ujob.get("started") else 0
        return out

    def _dashboard():
        try:
            failed = None
            if not updatesctl.primed(store):
                got = (store.data.get("ark_update") or {}).get("primed")
                if isinstance(got, dict) and not got.get("ok"):
                    failed = dict(got, expected=[
                        m.strip() for m in str(store.get("mod_ids") or "").split(",")
                        if m.strip()])
            return ui.render_dashboard(
                status=ARK_UPDATE, ready=updatesctl.primed(store), failed=failed,
                job=_ujob_live(), relay=RELAY_INFO, backup=job, restore=rjob)
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.warning("could not render the dashboard: %s", e)
            return ""

    async def update_status(request):
        if not authed(request):
            return web.json_response({"state": "denied"}, status=403)
        out = dict(ujob)
        out["elapsed"] = int(time.time() - ujob["started"]) if ujob["started"] else 0
        return web.json_response(out)

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
            live, later = _stage_or_apply({"maps": ",".join(chosen)})
            store.patch(live)
            if later:
                pendingctl.stage(store, later)
                announce.say("change.staged",
                             "Map selection saved and waiting for a safe moment: %s."
                             % ", ".join(chosen), detail=_pending_detail(),
                             count=pendingctl.count(store))
            store.save()
        except Invalid as e:
            return chrome(_cluster_body(request, problem=str(e)), "Cluster", "/admin/cluster")
        raise web.HTTPFound("/admin/cluster")

    cluster_busy = asyncio.Lock()

    def _act(fn, request):
        announce.say("cluster.%s" % fn.__name__, "%s requested from the web UI."
                     % fn.__name__.title())
        ok, msg = fn(store)
        announce.say("cluster.%s.%s" % (fn.__name__, "done" if ok else "failed"), msg,
                     level="info" if ok else "error")
        body = _cluster_body(request, message=msg if ok else "", problem="" if ok else msg)
        return chrome(body, "Cluster", "/admin/cluster")

    async def _act_once(fn, request):
        """Serialise cluster actions.

        Disabling the button in the browser is a courtesy, not a guarantee - a second
        click that lands before the first response renders would otherwise run a second
        `compose up` alongside the first. That happens to be harmless today only because
        compose adopts a container by name; relying on that is relying on a coincidence.
        """
        if cluster_busy.locked():
            return chrome(_cluster_body(
                request, message="Already working on the last request - this one was "
                                 "ignored rather than run twice."),
                "Cluster", "/admin/cluster")
        async with cluster_busy:
            return await asyncio.to_thread(_act, fn, request)

    async def cluster_launch(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return await _act_once(clusterctl.launch, request)

    async def cluster_stop(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return await _act_once(clusterctl.stop, request)

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

    # One backup at a time, and what it is currently doing. Held here rather than in
    # backup.py because it is a property of this running manager, not of the archive.
    job = {"state": "idle", "phase": "", "done": 0, "total": 0, "message": "",
           "ok": None, "started": 0.0}


    def _note(phase, done, total):
        job.update(phase=phase, done=done, total=total)

    def _run_backup():
        """The whole archive, start to finish, on a worker thread."""
        announce.say("backup.start", "Backup started.")
        ok, msg, _path = backupctl.create(store, flush=_flush_for(store),
                                          progress=_note)
        announce.say("backup.done" if ok else "backup.failed", msg)
        if ok:
            removed = backupctl.prune(store)
            if removed:
                msg += " Removed %d older backup%s." % (
                    len(removed), "" if len(removed) == 1 else "s")
        return ok, msg

    async def _backup_task():
        try:
            ok, msg = await asyncio.to_thread(_run_backup)
        except Exception as e:                       # noqa: BLE001 - surfaced below
            ok, msg = False, "Backup failed: %s" % e
            log.exception("backup failed")
        job.update(state="done", ok=ok, message=msg,
                   phase="done" if ok else "failed")

    async def backup_now(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        if job["state"] == "running":
            raise web.HTTPFound("/admin/backups")
        # Started, not awaited. Compressing the data root takes minutes, and doing that
        # inside the request handler ran it on the event loop - which stopped the chat
        # relay, dropped Discord's heartbeat and made the web UI itself unanswerable for
        # the whole archive. The one thing a backup must not do is take the manager down.
        job.update(state="running", phase="starting", done=0, total=0,
                   message="", ok=None, started=time.time())
        asyncio.create_task(_backup_task())
        raise web.HTTPFound("/admin/backups")

    async def backup_status(request):
        if not authed(request):
            return web.json_response({"state": "denied"}, status=403)
        out = dict(job)
        out["elapsed"] = int(time.time() - job["started"]) if job["started"] else 0
        out["human"] = backupctl.human_size(job["done"]) if job["done"] else ""
        out["percent"] = (round(100.0 * job["done"] / job["total"], 1)
                          if job["total"] else None)
        return web.json_response(out)

    # ---- mods
    # What the last lookup found, so the card survives the redirect that adds it and
    # the operator sees the mod they just added rather than an empty box.
    _found = {"card": None, "problem": ""}

    async def mods_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return chrome(ui.render_mods(store, modsctl.measure(layout.mods_dir(store)),
                                     found=_found["card"], problem=_found["problem"]),
                      "Mods", "/admin/mods")

    async def mods_find(request):
        """Look a mod up before it can be added. Never writes anything."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        # Off the loop: this is two DNS lookups and an HTTPS round trip to a service
        # that is somebody else's, and the chat relay lives on this thread.
        card, problem = await asyncio.to_thread(cfctl.lookup, form.get("ref"))
        _found.update(card=card, problem=problem)
        raise web.HTTPFound("/admin/mods")

    async def mods_edit(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        listed = str(store.get("mod_ids") or "")
        if form.get("addmod"):
            listed = modsctl.add(listed, str(form.get("addmod")).strip())
            _found["problem"] = ""
        elif form.get("drop"):
            listed = modsctl.remove(listed, str(form.get("drop")))
        elif form.get("up"):
            listed = modsctl.move(listed, str(form.get("up")), -1)
        elif form.get("down"):
            listed = modsctl.move(listed, str(form.get("down")), 1)
        try:
            live, later = _stage_or_apply({"mod_ids": listed})
            store.patch(live)
            if later:
                pendingctl.stage(store, later)
                announce.say("change.staged",
                             "Mod list saved and waiting for a safe moment to restart "
                             "the cluster: %s." % listed,
                             detail=_pending_detail(),
                             count=pendingctl.count(store))
            store.save()
        except Exception as e:                       # noqa: BLE001 - shown to the user
            log.warning("mod list rejected: %s", e)
        raise web.HTTPFound("/admin/mods")


    # ---- restore
    _looked = {"archive": None, "info": None, "notes": []}
    # One restore at a time, and what it is currently doing. A restore stops a map,
    # unpacks, swaps and then waits several minutes for the server to load a world - so
    # a page that just spins tells the operator nothing about a thing that is, by
    # design, in the middle of touching their data.
    rjob = {"state": "idle", "step": "", "message": "", "ok": None,
            "map": "", "archive": "", "started": 0.0}

    def _archive_path(name):
        """Resolve a posted name inside the backups folder, and nowhere else."""
        base = os.path.abspath(backupctl.backups_dir(store))
        p = os.path.abspath(os.path.join(base, os.path.basename(str(name or ""))))
        return p if p.startswith(base + os.sep) and os.path.isfile(p) else None

    def _points_by_map():
        """Read from disk each time - the game prunes these on its own schedule."""
        out = []
        for key in clusterctl._map_keys(store):
            try:
                found = pointsctl.list_points(store, key)
            except Exception as e:                   # noqa: BLE001 - never a blank page
                log.info("could not list restore points for %s: %s", key, e)
                continue
            if found:
                from .maps import BY_KEY as _MAPS
                out.append((_MAPS[key]["name"], found))
        return out

    def _restore_body(message="", problem=""):
        return ui.render_restore(store, backupctl.listing(store),
                                 chosen=_looked["archive"], info=_looked["info"],
                                 notes=_looked["notes"], message=message, problem=problem,
                                 job=rjob, savepoints_by_map=_points_by_map())

    async def restore_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return chrome(_restore_body(), "Restore", "/admin/restore")

    async def restore_inspect(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        path = _archive_path(form.get("archive"))
        if not path:
            return chrome(_restore_body(problem="No such archive."), "Restore",
                          "/admin/restore")
        # Off the loop regardless: an archive written before the manifest carried a map
        # list still has to be decompressed to describe it, and the relay, Discord and
        # this page all live on the thread that would be doing it.
        info = await asyncio.to_thread(restorectl.inspect, path)
        _looked.update(archive=os.path.basename(path), info=info if info["ok"] else None,
                       notes=restorectl.compare(store, info) if info["ok"] else [])
        return chrome(_restore_body(problem="" if info["ok"] else info["problem"]),
                      "Restore", "/admin/restore")

    async def restore_run(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        path = _archive_path(form.get("archive"))
        map_key = str(form.get("map") or "")
        if not path:
            return chrome(_restore_body(problem="No such archive."), "Restore",
                          "/admin/restore")
        if cluster_busy.locked():
            return chrome(_restore_body(problem="Something else is already working on "
                                                "the cluster - this was ignored rather "
                                                "than run alongside it."),
                          "Restore", "/admin/restore")

        if rjob["state"] == "running":
            raise web.HTTPFound("/admin/restore")

        def note(text):
            rjob["step"] = text
            announce.say("restore.phase", text, map=map_key)

        def verify_after(key):
            """Wait for the map to come back, then put it through the six gates.

            Starting a container and asking whether it is serving are minutes apart, so
            checking immediately would measure the wrong thing and call every restore a
            success. This is the step that makes the promise real.
            """
            note("waiting for %s to come back" % key)
            ok_h, why_h = clusterctl.wait_healthy(store, key)
            if not ok_h:
                return False, [why_h]
            note("checking it is really serving")
            return clusterctl.verify_instance(store, key)

        def go():
            return restorectl.restore_map(
                store, path, map_key,
                stop=lambda k: (note("stopping %s" % k) or clusterctl.stop_one(store, k)),
                start=lambda k: (note("starting %s" % k) or clusterctl.start_one(store, k)),
                verify=verify_after, on_step=note)

        async def run_it():
            try:
                async with cluster_busy:
                    ok, msg, detail = await asyncio.to_thread(go)
            except Exception as e:                       # noqa: BLE001 - surfaced below
                ok, msg, detail = False, "Restore failed: %s" % e, {}
                log.exception("restore failed")
            announce.say("restore.done" if ok else "restore.failed", str(msg),
                         level="info" if ok else "error", map=map_key)
            rjob.update(state="done", ok=ok, message=msg, step="done",
                        detail=detail)

        announce.say("restore.start",
                     "Restoring %s - only that map stops; its current world is copied "
                     "first and the one it replaces is kept." % map_key,
                     archive=os.path.basename(path))
        rjob.update(state="running", ok=None, message="", step="starting",
                    map=map_key, archive=os.path.basename(path),
                    started=time.time(), detail={})
        asyncio.create_task(run_it())
        raise web.HTTPFound("/admin/restore")

    async def restore_point(request):
        """Roll one map back to one of the game's own dated saves."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        map_key, _, name = str(form.get("point") or "").partition("|")
        force = bool(form.get("force"))
        if not map_key or not name:
            raise web.HTTPFound("/admin/restore")
        if rjob["state"] == "running" or cluster_busy.locked():
            raise web.HTTPFound("/admin/restore")

        def note(text):
            rjob["step"] = text
            announce.say("restore.phase", text, map=map_key)

        def verify_after(key):
            note("waiting for %s to come back" % key)
            ok_h, why_h = clusterctl.wait_healthy(store, key)
            if not ok_h:
                return False, [why_h]
            note("checking it is really serving")
            return clusterctl.verify_instance(store, key)

        def _save_one(key):
            """Ask this one map to write its world out. Best effort, by design.

            A map that shuts down cleanly does not leave a hot journal beside its world,
            which is what refused the Genesis restore. So it is worth asking - but the
            world has already been copied aside as a file, so a map that cannot answer
            must not be a map that cannot be restored. That is precisely when somebody
            wants to.
            """
            from . import bot
            password = str(store.get("admin_password") or "")
            for label, host, port in clusterctl.running_instances(store):
                if label not in (key, mapsmod.BY_KEY[key]["name"]):
                    continue
                try:
                    clusterctl.run_coroutine(
                        bot.rcon_with(host, port, password, "SaveWorld", timeout=30))
                    return True, "saved"
                except Exception as e:               # noqa: BLE001 - reported, not fatal
                    return False, str(e).strip() or e.__class__.__name__
            return False, "it is not running"

        def go():
            return pointsctl.restore_point(
                store, map_key, name,
                stop=lambda k: (note("stopping %s" % k) or clusterctl.stop_one(store, k)),
                start=lambda k: (note("starting %s" % k)
                                 or clusterctl.start_one(store, k)),
                verify=verify_after, force=force, on_step=note,
                players=lambda: clusterctl.players_online(store),
                save=_save_one)

        async def run_it():
            try:
                async with cluster_busy:
                    ok, msg, detail = await asyncio.to_thread(go)
            except Exception as e:                   # noqa: BLE001 - surfaced below
                ok, msg, detail = False, "Restore point failed: %s" % e, {}
                log.exception("restore point failed")
            announce.say("restore.done" if ok else "restore.failed", str(msg),
                         level="info" if ok else "error", map=map_key,
                         detail="\n".join(detail.get("steps") or []))
            rjob.update(state="done", ok=ok, message=msg, step="done", detail=detail)

        announce.say("restore.point_start",
                     "Rolling %s back to its save %s. Only that map stops; its current "
                     "world is copied first." % (map_key, name),
                     map=map_key, point=name)
        rjob.update(state="running", ok=None, message="", step="starting",
                    map=map_key, archive=name, started=time.time(), detail={})
        asyncio.create_task(run_it())
        raise web.HTTPFound("/admin/restore")

    async def restore_status(request):
        if not authed(request):
            return web.json_response({"state": "denied"}, status=403)
        out = dict(rjob)
        out.pop("detail", None)
        out["elapsed"] = int(time.time() - rjob["started"]) if rjob.get("started") else 0
        return web.json_response(out)

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
        body_version = ui.render_version(VERSION_INFO)
        todo = store.readiness()
        st = clusterctl.status(store)
        if st.get("running"):
            body = ui.render_status(st)
        else:
            body = ('<div class=note>Cluster not running. %s</div>'
                    % (("Still to set: " + ", ".join(b["label"] for b in todo))
                       if todo else "Launch it from the Cluster tab."))
        # The last few events on the front page, because "what has Obelisk been doing"
        # should not need a tab - that was the shape of the complaint. The full history
        # and the detail live on Activity.
        recent = _dashboard() + ui.render_events(announce.recent(limit=6),
                                                 jobs=_jobs(), compact=True)
        recent = recent.replace("<div id=feed>",
                                '<div id=feed data-newest="%d">' % announce.newest_id(), 1)
        recent = recent.replace("</fieldset>",
                                '<a href="/admin/activity">See everything</a>'
                                "</fieldset>", 1)
        body += recent + _connect_panel() + body_version + ui.FEED_LIVE
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
    app.router.add_get("/admin/backup/status", backup_status)
    app.router.add_get("/admin/restore", restore_page)
    app.router.add_post("/admin/restore/inspect", restore_inspect)
    app.router.add_post("/admin/restore/run", restore_run)
    app.router.add_post("/admin/restore/point", restore_point)
    app.router.add_get("/admin/restore/status", restore_status)
    app.router.add_get("/admin/mods", mods_page)
    app.router.add_post("/admin/mods", mods_edit)
    app.router.add_post("/admin/mods/find", mods_find)
    app.router.add_get("/admin/cloud", cloud_page)
    app.router.add_post("/admin/cloud/connect", cloud_connect)
    app.router.add_post("/admin/cloud/disconnect", cloud_disconnect)
    app.router.add_post("/admin/cloud/push", cloud_push)
    app.router.add_post("/admin/cloud/pull", cloud_pull)
    app.router.add_post("/admin/pending", pending_post)
    app.router.add_get("/admin/activity", activity_page)
    app.router.add_get("/admin/activity/feed", activity_feed)
    app.router.add_post("/admin/update/prime", update_prime)
    app.router.add_post("/admin/update/apply", update_apply)
    app.router.add_get("/admin/update/status", update_status)
    app.router.add_get("/healthz", healthz)
    return app


def _row_value(kind, text):
    """A form field as the file spells it: strings quoted, bools capitalised."""
    text = str(text).strip()
    if kind == "text":
        return text if text.startswith('"') else '"%s"' % text.strip('"')
    if kind == "bool":
        return "True" if text.lower() in ("true", "yes", "on", "1") else "False"
    return text


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


async def version_watch():
    """Ask the registry whether a newer Obelisk is published, occasionally.

    Backstop for a checker that gets this wrong: Unraid compares digests but does not do
    the token exchange GHCR needs, so it records a stale one and reports "up to date"
    while an update sits unapplied. Obelisk asks for itself and says so in the UI. It
    never applies anything - that is the Docker page's job.
    """
    while True:
        try:
            info = await asyncio.to_thread(versionctl.status)
            VERSION_INFO.update(info)
            if info.get("update_available"):
                announce.say("obelisk.update_available",
                             "A newer Obelisk is published. Apply it from the Unraid "
                             "Docker page (Apply Update, or Force Update if the page "
                             "still says up-to-date).",
                             running=versionctl.short(info.get("commit"), 7))
        except Exception as e:                           # noqa: BLE001 - never fatal
            log.info("version check skipped: %s", e)
        await asyncio.sleep(6 * 3600)


async def empty_watch(store, interval=60, needed=3, busy=None, apply_now=None):
    """Apply what is waiting the moment nobody is playing.

    A restart costs whoever is on. An empty cluster costs nobody, so that is when the
    queue should land - and on a ten-map cluster there is usually an empty hour every
    day without anybody having to schedule one.

    Three things keep this from being the feature that restarts your cluster while
    somebody is standing in it. Every map has to *answer*, because a map that timed out
    is not an empty map. It has to be empty for several checks running, because one poll
    returning zero is a moment - somebody loading a map, somebody swapping servers - and
    not a state. And the count is taken again inside the lock, immediately before
    anything stops, because the gap between deciding and acting is exactly long enough
    for somebody to log in.
    """
    from . import pending as pnd, updates as upd

    streak = 0
    while True:
        await asyncio.sleep(interval)
        try:
            if not (pnd.any_pending(store) or upd.primed(store)):
                streak = 0
                continue
            if busy is not None and busy.locked():
                streak = 0                       # something else is working on it
                continue

            total, counts, silent = await asyncio.to_thread(
                clusterctl.players_online, store)
            if silent or not counts:
                # Nobody answering is not nobody playing. It is also what a stopped
                # cluster looks like, and restarting one of those helps no one.
                streak = 0
                continue
            streak = streak + 1 if total == 0 else 0

            go, why = upd.empty_enough(store, streak, needed=needed)
            if not go:
                continue

            log.info("nobody is on and something is waiting: %s", why)
            announce.say("change.window_open",
                         "The cluster has been empty for %d minute(s) and there are "
                         "changes waiting, so they are being applied now."
                         % (streak * interval // 60))
            streak = 0
            await asyncio.to_thread(apply_now or (lambda: _scheduled_apply(store)))
        except Exception as e:                          # a bad minute must not kill it
            log.error("empty-cluster check failed: %s", e)
            streak = 0


async def events_persist(store, interval=5):
    """Write the activity feed out when it changes, so it survives a restart.

    On a timer against a revision counter rather than on every announcement: a prime
    emits a phase line every twenty seconds for forty minutes, and rewriting the file
    for each one would be a lot of disk for no more information. Five seconds is well
    inside how long anybody takes to notice something happened.
    """
    path = layout.obelisk_paths(layout.root_of(store))["events"]
    last = announce.revision()
    while True:
        await asyncio.sleep(interval)
        try:
            now = announce.revision()
            if now != last:
                ok, why = await asyncio.to_thread(announce.save_to, path)
                if ok:
                    last = now
                else:
                    log.warning("could not write the activity feed: %s", why)
        except Exception as e:                          # noqa: BLE001 - never fatal
            log.warning("activity feed not saved: %s", e)


async def ark_update_watch(store, interval=1800, panel=None):
    """Notice new ARK builds and mod versions, and apply a staged one when the window
    opens. One loop, because both jobs are the same question asked at two intervals.

    The checking half is cheap and harmless. The applying half is deliberately hard to
    trigger: it needs an update that was staged *and* verified, Obelisk to own updates,
    the setting to be on, the clock inside the window, and that build not to have been
    applied already. Every one of those is a way for a scheduler to restart a cluster
    nobody asked it to.
    """
    from . import staging as stg, updates as upd
    while True:
        try:
            root = layout.ark_root_of(store)
            status = await asyncio.to_thread(upd.look, store, root)
            seen = ARK_UPDATE if panel is None else panel
            seen.clear()
            seen.update(status)
            upd.announce_new(store, status)

            # ---- stage it before anybody asks
            #
            # The point of the staging server is to be *ahead*: by the time a window
            # opens or somebody clicks Apply, the files are already downloaded and
            # already proved, so applying is a rename rather than a 12 GB pull at four
            # in the morning. So priming is not something a person has to remember to
            # do - a new build or a new mod version starts it, and the button is there
            # for when somebody wants it now.
            #
            # needs_prime() carries the whole guard, including why not, so a decision
            # not to stage is visible rather than silent.
            go, why = upd.needs_prime(store, status)
            if go:
                log.info("staging ahead: %s", why)
                await asyncio.to_thread(upd.prime, store, root,
                                        target=upd.target_key(status))
            elif status.get("any_newer"):
                log.info("not staging: %s", why)

            due, why = upd.due(store)
            if due:
                log.info("scheduled ARK update: %s", why)
                announce.say("ark.window_open", "The update window is open and a "
                                                "verified update is staged. Applying it.")
                # Applies through the same routine the button uses, so the scheduled
                # path cannot drift from the one that gets exercised by hand.
                await asyncio.to_thread(_scheduled_apply, store)
        except Exception as e:                          # a bad night must not kill it
            log.error("ARK update check failed: %s", e)
        await asyncio.sleep(interval)


def _scheduled_apply(store, force=False, recheck=True):
    """The unattended apply. Same verbs as the button, assembled in one place.

    `recheck` is not optional in practice: whatever decided to call this did so from a
    player count taken up to a minute ago, and a minute is long enough for somebody to
    log in. The count is taken again here, immediately before anything stops.
    """
    from . import bot, pending as pnd, staging as stg, updates as upd

    if recheck and not force:
        total, counts, silent = clusterctl.players_online(store)
        if silent or total:
            who = (", ".join("%s (%d)" % (m, c) for m, c in sorted(counts.items()) if c)
                   or ", ".join(l for l, _ in silent))
            log.info("not applying after all - somebody arrived: %s", who)
            announce.say("change.deferred",
                         "Changes were about to be applied to an empty cluster, but %s "
                         "answered differently on the final check. Left for later."
                         % who)
            return False, "somebody is on after all", {}

    def warn(minutes, build):
        password = str(store.get("admin_password") or "")
        for _label, host, port in clusterctl.running_instances(store):
            try:
                clusterctl.run_coroutine(bot.rcon_with(
                    host, port, password,
                    "ServerChat Server restarting in %d minutes to apply ARK build %s"
                    % (minutes, build), timeout=20))
            except Exception as e:                      # noqa: BLE001 - best effort
                log.info("could not warn one map: %s", e)
        time.sleep(minutes * 60)

    def stop_all():
        stg.down(store)
        return clusterctl.stop(store)

    def verify_all():
        results = {}
        for key in clusterctl._map_keys(store):
            ok_h, _why = clusterctl.wait_healthy(store, key)
            results[key] = bool(ok_h) and clusterctl.verify_instance(store, key)[0]
        return all(results.values()), results

    return upd.apply_batch(
        store, layout.ark_root_of(store), warn=warn, force=force,
        save=lambda: clusterctl.save_world(store), stop_all=stop_all,
        start_all=lambda: clusterctl.launch(store), verify=verify_all,
        players=lambda: clusterctl.players_online(store),
        on_step=lambda text: log.info("update: %s", text))


async def main():
    store, created, code = bootstrap()

    # Read the game's own config in before serving a page about it. An operator who has
    # spent months tuning a cluster should recognise their settings the first time they
    # open this, not a screen of defaults sitting on top of the real values.
    try:
        adopted, unreadable = gamecfg.adopt(store)
        cells = gamecfg.adopt_grids(store)
        rows = gamecfg.adopt_rows(store)
        if cells:
            log.info("game settings: %d per-level stat cell(s) read", cells)
        if rows:
            log.info("game settings: %d array row(s) read", rows)
        if adopted or cells or rows:
            store.save()
            log.info("game settings: %d read from the INI files", adopted)
        if unreadable:
            log.info("game settings left alone (not the expected type): %s",
                     ", ".join(unreadable[:5]))
    except Exception as e:                       # never a reason not to start
        log.warning("could not read the game settings: %s", e)

    # Register what must never appear in an announcement, before anything can announce.
    # The guard is a net, not a substitute for callers being careful - but the most
    # likely way a password gets announced is inside an exception nobody wrote by hand.
    from .backup import SECRET_KEYS
    announce.guard_secrets([store.get(k) for k in SECRET_KEYS])

    # The activity feed from last time, put back before anything announces - so an admin
    # opening the UI after a restart sees the backup that ran last night, not an empty
    # page implying nothing ever happens. Ids carry on climbing from where they left off.
    try:
        restored = announce.load_from(
            layout.obelisk_paths(layout.root_of(store))["events"])
        if restored:
            log.info("activity feed: %d earlier event(s) restored", restored)
    except Exception as e:                               # never a reason not to start
        log.warning("could not read the activity feed: %s", e)

    # Which Obelisk this is, and whether it just changed. The store remembers the last
    # version it saw, so coming back on a different one is reported as an update having
    # landed - which is the question somebody actually has after clicking Apply Update.
    try:
        me = versionctl.running()
        VERSION_INFO.update(me)
        seen = store.data.get("last_version")
        now_v = versionctl.short(me.get("commit"), 7)
        if me.get("commit"):
            if seen and seen != me["commit"]:
                announce.say("obelisk.updated",
                             "Obelisk restarted on a new version: %s (was %s)."
                             % (now_v, versionctl.short(seen, 7)))
            else:
                log.info("obelisk version %s", now_v)
            store.data["last_version"] = me["commit"]
            store.save()
    except Exception as e:                               # never a reason not to start
        log.warning("could not read this container's version: %s", e)

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
    tasks.append(asyncio.create_task(version_watch()))
    tasks.append(asyncio.create_task(ark_update_watch(store)))
    tasks.append(asyncio.create_task(events_persist(store)))
    tasks.append(asyncio.create_task(empty_watch(store)))

    from . import bot
    # The relay used to learn its maps from a SERVERS environment variable, which only
    # ever existed when Obelisk wrote itself into the stack it generated. It no longer
    # does, so on a normally-installed Obelisk the relay was permanently inert. It reads
    # the cluster from the store instead - the same place everything else does.
    # Attach to the cluster's network before wiring anything to it. This used to happen
    # only inside launch(), so a manager recreated without launching - an image update,
    # a template change, a reboot - came back unable to resolve a single map while
    # cheerfully reporting that it covered all ten.
    try:
        joined, why_net = clusterctl.join_network_if_running(store)
        log.info("cluster network: %s", why_net)
    except Exception as e:                               # never a reason not to start
        log.warning("could not join the cluster network: %s", e)

    wired = _wire_relay(store, bot)
    if wired:
        # Coverage is a claim, so check it rather than counting containers. The relay
        # reported ten maps it could not reach for a full deploy cycle, and the only
        # sign was a warning per map per poll, in a log nobody was reading.
        try:
            good, bad = await asyncio.to_thread(clusterctl.reachable, store)
        except Exception as e:                           # noqa: BLE001
            good, bad = [], [("all maps", str(e))]
        total = len(bot.SERVERS)
        if bad:
            names = ", ".join("%s (%s)" % (n, why) for n, why in bad[:4])
            log.error("relay reaches %d of %d map(s) - cannot reach: %s",
                      len(good), total, names)
            RELAY_INFO.update(total=total, reachable=len(good),
                              unreachable=", ".join(n for n, _w in bad))
            announce.say("relay.degraded",
                         "Chat relay can reach %d of %d maps. Cross-map chat will not "
                         "work for the rest until this is fixed." % (len(good), total),
                         level="error", unreachable=", ".join(n for n, _w in bad))
        else:
            log.info("relay covering %d map(s), all reachable: %s",
                     total, ", ".join(bot.SERVERS))
            RELAY_INFO.update(total=total, reachable=total, unreachable="")
            announce.say("relay.up", "Chat relay is up and reaching all %d maps." % total)
        tasks.append(asyncio.create_task(bot.main()))
    else:
        log.info("no cluster running yet - relay idle until maps are launched")

    if not tasks:
        log.error("nothing to run: web UI is off and no cluster is configured")
        return
    await asyncio.gather(*tasks)


def _wire_relay(store, bot):
    """Point the chat relay at this cluster's maps. Returns True if there are any.

    Addresses each map by container name on the cluster network, which is the same route
    the save-flush uses and the reason the manager joins that network after a launch.
    """
    try:
        targets = clusterctl.running_instances(store)
    except Exception as e:
        log.warning("could not work out which maps are running: %s", e)
        return False
    if not targets:
        return False
    bot.SERVERS = {label: (host, port) for label, host, port in targets}
    bot.RCON_PASSWORD = str(store.get("admin_password") or "")
    bot.CLUSTER_NAME = str(store.get("cluster_name") or bot.CLUSTER_NAME)

    # The Discord half has to come across too. The relay reads these from its own module
    # globals, seeded once from the environment - so a token typed into the admin page
    # sat in settings.json while the bot went on looking at an empty env var and relayed
    # map to map only, silently. Same shape as the map list before it: the setting is
    # stored in one place and read from another, and nothing says so.
    for key, attr, cast in _RELAY_SETTINGS:
        try:
            setattr(bot, attr, cast(store.get(key)))
        except (TypeError, ValueError):
            log.warning("ignoring unusable value for %s", key)

    # The relay still carries the standalone status page from when it was its own
    # container, and it binds the same port the web UI is already serving on - so the
    # two collided and the process died on "address already in use" the first time the
    # relay actually got as far as starting. Inside Obelisk that page is redundant; the
    # relay has always had a switch for turning it off.
    bot.STATUS_PORT = 0

    bot.CLUSTER_CONFIGURED = bool(bot.SERVERS and bot.RCON_PASSWORD)
    return bot.CLUSTER_CONFIGURED


def _id(v):
    """A Discord snowflake from the store, or 0. Blank is normal, not an error."""
    s = str(v or "").strip()
    return int(s) if s.isdigit() else 0


def _text(v):
    return str(v or "").strip()


# store key -> the relay's own global, and how to read it. Spelled out rather than
# derived from the schema's env: targets, because three of them are named differently on
# the two sides and a silent near-miss is exactly the bug above.
_RELAY_SETTINGS = (
    ("discord_token", "DISCORD_TOKEN", _text),
    ("discord_channel_id", "DISCORD_CHANNEL_ID", _id),
    ("discord_tribelog_channel_id", "TRIBELOG_CHANNEL_ID", _id),
    ("discord_admin_channel_id", "ADMIN_CHANNEL_ID", _id),
    ("discord_admin_role_id", "ADMIN_ROLE_ID", _id),
    ("discord_invite", "DISCORD_INVITE", _text),
    ("join_leave", "JOIN_LEAVE", bool),
    ("welcome_enabled", "WELCOME_ENABLED", bool),
)


# Last in the file, and it has to stay last: everything main() reaches for must
# already be defined by the time this line runs. Sitting above _wire_relay(), it
# called a name that did not exist yet - so the container exited on NameError the
# moment a cluster was actually running for the relay to pick up, and only then.
if __name__ == "__main__":
    asyncio.run(main())
