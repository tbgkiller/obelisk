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
from . import arkupdate
from . import bans as bansctl
from . import cap as capctl
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

# One lock for anything that stops and starts the cluster - the buttons and, crucially,
# the two loops that fire on their own.
#
# It used to be created inside build_app, so it guarded the web routes and nothing else.
# The empty-cluster watcher took a `busy` argument that main() never passed - so the
# lock it used was whatever the default said, which was none - and the scheduled window
# took no lock at all. On 8 September the empty watcher began an apply
# at 03:58 and the window started a second one at 04:28 while the first was still
# stopping and starting ten servers. Aberration's world was half-written when the second
# stop reached it, and the map spent the next five hours refusing to load a corrupt
# database. Guarding the buttons and not the unattended paths is the worst possible half
# of this to have done.
#
# The argument that caused it is gone too. Making the lock module-level fixed the
# incident, but the parameter that let a caller supply a different one - or none - was
# left on the signature, still defaulting to None, still there for the next person who
# passed something. A door that has been bolted is not the same as a door that has been
# removed.
APPLY_LOCK = asyncio.Lock()

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

    def _settings_body(problem="", refusal=""):
        """The settings page, with the mod list editor under it.

        The editor is the same one the Data page used to draw. It moved rather than
        being rebuilt, and it still writes through its own routes - which is the one
        writer this value has always had, whatever page the boxes were on.
        """
        return ui.render_settings(
            store, mods=_mods_section(problem=problem, refusal=refusal))

    async def admin(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        # A refusal from the mod editor comes back the way every other page's does:
        # parked in the one-shot slot, collected once by the render that follows.
        token = str((getattr(request, "query", None) or {}).get("said") or "")
        said = _said.pop(token, None) if token else None
        refusal = (said or {}).get("refusal", "") if (
            said or {}).get("where") == "mods" else ""
        return chrome(_settings_body(refusal=refusal), "Obelisk settings", "/admin")

    def _safe_back(raw):
        """Where to send the browser after a save. Never what the form asked for.

        The form carries a `back` so an edit made on a map's page returns to that page.
        Echoing it into a redirect would be an open redirect with a hidden field for a
        control - so this does not echo it: it recognises it. Only the two shapes this
        manager renders are accepted, and the map key has to be a real one; anything
        else goes to the settings page, which is where a save without a `back` has
        always gone.
        """
        back = str(raw or "")
        # Exact matches only. Trimming a path back into something acceptable - taking
        # the first segment of "island/../../../etc" and calling it "island" - is safe
        # this time and is the habit that lets the next one through. Either it is one of
        # the addresses this manager renders, or it is not.
        for key in mapsmod.catalogue(store):
            if back == "/admin/cluster/map/" + key:
                return back
        for sect in ("backups", "cloud", "restore"):
            if back == "/admin/data#" + sect:
                return back
        if back == "/admin#mods":
            return back
        return "/admin"

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
            _reguard()
            await _restage_if_needed(restage)
        except Invalid as e:
            return chrome('<div class=problem>%s</div>%s'
                          % (ui._e(str(e)), _settings_body()),
                          "Obelisk settings", "/admin")
        where = _safe_back(form.get("back"))
        if where == "/admin":
            raise web.HTTPFound("/admin")
        # An edit made somewhere else says so where it was made, the way every other
        # action on those pages does.
        said = _say_next(where="map" if "/map/" in where else "data",
                         message="Saved.")
        raise web.HTTPFound("%s%ssaid=%s%s"
                            % (where.split("#")[0],
                               "&" if "?" in where else "?", said,
                               "#" + where.split("#")[1] if "#" in where else ""))

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
    def _label_services(st):
        """Put each map's own name on its running service, so a page can say it.

        compose keys a service by its instance and the plan knows what that instance is
        called, so this is a lookup rather than a guess. The status table was headed
        "Map" and filled with the instance - the same id-for-name slip the restore flow
        had, on the page people open first.
        """
        try:
            rows = (build_plan(store).get("maps") or [])
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.info("could not name the running maps: %s", e)
            return st
        # The key travels with the name. Both come off the same plan row, so a page
        # can link a row to that map without turning a display name back into a key -
        # which is the slip this function was written to stop, and doing it per row
        # would be doing it everywhere.
        by_instance = {r["instance"]: (r["name"], r["map"]) for r in rows}
        for svc in st.get("services") or []:
            name, key = by_instance.get(svc.get("service"), ("", ""))
            svc["label"] = name
            svc["map"] = key
        return st

    def _roster_now():
        """Who the relay last saw, for the page. Read, never measured.

        The same poll the count comes from, so the two cannot disagree about a map.
        """
        from . import bot
        try:
            snap = bot.online_roster()
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.info("could not read the player roster: %s", e)
            return None
        if snap is None:
            return None
        by_map, age = snap
        return {"by_map": by_map, "age": age}

    def _players_now():
        """The relay's cached population, or None when there is no relay to have asked.

        Read, never measured. The relay already asks every map once a minute and the
        answer is sitting in memory; asking the servers again to draw a page would pay
        twice for it - and would put ten RCON round trips inside a page render.
        """
        from . import bot
        try:
            snap = bot.online_snapshot()
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.info("could not read the player count: %s", e)
            return None
        if snap is None:
            return None
        by_map, total, age = snap
        return {"by_map": by_map, "total": total, "age": age}

    # What a one-shot action said, waiting for the render that follows its redirect.
    # The slower actions redirect and leave their result in a job dict; this is the
    # same idea for a command that finishes inside the request - without it the action
    # would have to answer with a rendered page, and a refresh would run it again.
    #
    # Keyed by a token that travels in the redirect, not by the admin. There is no
    # "the admin" to key on: authed() compares one shared admin_token, so two people
    # logged in present the same cookie and are indistinguishable here. A single slot
    # was worse than indistinguishable - whichever browser rendered the page first took
    # the banner, so the person who pressed the button saw nothing and somebody who
    # pressed nothing was told a message had been sent. Harmless for a line of chat;
    # not harmless for "banned on 8 of 10 maps", and every action slice after this one
    # uses the same mechanism.
    #
    # The token belongs to the request that caused it rather than to a person, which
    # also means two tabs belonging to the same admin do not steal from each other.
    _said = {}
    SAID_TTL = 120.0
    SAID_MAX = 64

    def _say_next(**kw):
        """Park a result and return the token that collects it, exactly once."""
        import secrets
        now = time.time()
        for tok in [t for t, v in _said.items() if now - v["at"] > SAID_TTL]:
            _said.pop(tok, None)
        while len(_said) >= SAID_MAX:                # a redirect nobody followed
            _said.pop(min(_said, key=lambda t: _said[t]["at"]), None)
        token = secrets.token_urlsafe(9)
        _said[token] = dict({"message": "", "problem": "", "refusal": ""}, at=now, **kw)
        return token

    def _asking(pending, where):
        """The pending question, if it is this section's to ask."""
        return pending if (pending or {}).get("where", "who") == where else None

    def _cluster_body(request, message="", problem="", refusal="", pending=None):
        # A result about a player is shown in the who's-online section rather than at
        # the top of the page: that is where the operator is looking when they press
        # the button, and where the answer changes something.
        notice = bans_notice = caps_notice = ""
        maps_refusal = ""
        if not (message or problem or refusal):
            token = str((getattr(request, "query", None) or {}).get("said") or "")
            said = _said.pop(token, None) if token else None
            if said:
                if said.get("where") in ("who", "bans", "cap", "maps"):
                    block = ui.warn_block(said["problem"]) if said["problem"] else (
                        '<div class=note>%s</div>' % ui._e(said["message"]))
                    if said["where"] == "bans":
                        bans_notice = block
                    elif said["where"] == "cap":
                        caps_notice = block
                    elif said["where"] == "maps":
                        maps_refusal = said.get("refusal") or said.get("problem") or ""
                    else:
                        notice = block
                else:
                    message = said["message"]
                    problem = said["problem"]
                    refusal = said["refusal"]
        try:
            in_use = clusterctl.other_ports_in_use(store)
        except Exception:
            in_use = None
        plan = build_plan(store, in_use_ports=in_use)
        # A name in the list the catalogue cannot explain. It is shown at the editor,
        # amber, and it wins the slot: a refusal from the last click matters less than
        # the cluster being unable to start at all, and the click is repeatable.
        gone = mapsmod.unknown(store, mapsmod.listed(store.get("maps")))
        if gone:
            maps_refusal = ui.maps_unknown(gone)
        st = clusterctl.status(store)
        banner = ""
        if problem:
            banner = '<div class=problem>%s</div>' % ui._e(problem)
        elif refusal:
            # Already styled, and amber rather than red: a stop refused because people
            # are playing is the same kind of answer as a restore refused for the same
            # reason, and nothing has happened yet either way.
            banner = refusal
        elif message:
            banner = '<div class=note>%s</div>' % ui._e(message)
        # Both buttons on this page start every map, including one the gate is holding
        # down - onto the world it just refused. Only warned about while the map is
        # actually still down, so it clears itself once somebody has dealt with it.
        held = updatesctl.held_down(store)
        # Only while Docker is actually answering. With no `compose ps` the running set
        # is empty, and an empty set would make this reappear over maps that are up.
        if held and st.get("docker_ok"):
            running = {s.get("service") for s in (st.get("services") or [])
                       if s.get("state") == "running"}
            keys = {r["name"]: r["instance"] for r in plan.get("maps") or []}
            # A map that has since been deselected is not part of this cluster any more,
            # so there is nothing to warn about and nothing that would ever clear it.
            still = [l for l in held if l in keys and keys[l] not in running]
            if still:
                # The state travels with them, because "restore it" and "check the
                # mount" are opposite advice and the label alone cannot tell which.
                banner += ui.render_held_down(
                    still, states=updatesctl.held_down_states(store))
        # One panel. render_stop_job owns #stopwrap and the poller replaces what is
        # inside it, so the server-rendered paint and the polled one are the same
        # element rather than two of them stacked.
        launched = bool((st or {}).get("compose_exists"))
        return (banner + ui.render_jump(launched) + _summary_band(st)
                + _pending_panel() + _update_panel() +
                ui.render_stop_job(_sjob_live()) + ui.STOP_JS +
                ui.render_cluster(store, plan, status=_label_services(st),
                                  roster=_roster_now(),
                                  web_address=_web_address(),
                                  maps_editor=ui.render_maps_editor(
                                      store, running=bool(st.get("running")),
                                      refusal=maps_refusal),
                                  pending=_asking(pending, "who"), notice=notice,
                                  bans=bansctl.recent(store),
                                  bans_pending=_asking(pending, "bans"),
                                  bans_notice=bans_notice,
                                  bans_total=bansctl.count(store),
                                  caps=capctl.recent(store),
                                  caps_pending=_asking(pending, "cap"),
                                  caps_notice=caps_notice,
                                  caps_total=capctl.count(store))
                + _reference_foot())

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

    def _reguard():
        """Re-register the secrets after one of them changes.

        guard_secrets ran once at boot, so a password changed through the UI was not
        scrubbed from announcements until the next restart - and the most likely way a
        secret reaches the channel is inside an exception message nobody wrote by hand.
        The guard is a net; a net that only knows last week's values is most of a net.
        """
        from .backup import SECRET_KEYS
        try:
            announce.guard_secrets([store.get(k) for k in SECRET_KEYS])
        except Exception as e:                       # noqa: BLE001 - never fatal
            log.warning("could not re-register the secrets: %s", e)

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

    def _recent_panel():
        """The last few things Obelisk did, live, with the way to the full history.

        Six lines rather than the feed: "what has this thing been doing" should be
        answerable without opening a tab, and everything past six is what Activity is
        for. The data-newest attribute is what FEED_LIVE polls against, so the panel
        updates itself without the page reloading under somebody mid-action.
        """
        try:
            feed = ui.render_events(announce.recent(limit=6), jobs=_jobs(),
                                    compact=True)
            feed = feed.replace('<div id=feed>',
                                '<div id=feed data-newest="%d">'
                                % announce.newest_id(), 1)
            return feed.replace("</fieldset>",
                                '<a href="/admin/activity">See everything</a>'
                                "</fieldset>", 1)
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.warning("could not render the recent events: %s", e)
            return ""

    def _summary_band(st):
        """The at-a-glance half of the page, in the order the questions are asked.

        Is anything wrong, is anything running, who is on, and only then what has
        happened. The first version of this put the events feed above the running-maps
        table, so a cluster with a map crash-looping opened on a history log and the
        failing banner was the third thing down the page - the merge had inverted the
        one job a landing page has.

        The running-maps block carries the failing banner and the player count as well
        as the table, which is why it sits here rather than with the sections that act
        on maps: it is the answer, not an operation.
        """
        note = ""
        if not (st or {}).get("running"):
            todo = store.readiness()
            # What the operator is looking at, not a promise about later. A
            # cluster that has been launched and stopped still draws its table, with
            # every address in it and every row reading "exited" - so "the address
            # appears once it is running" described a future that was already on the
            # screen. A cluster that has never been launched has no table and no
            # addresses, and is told only where to start.
            if todo:
                rest = "Still to set: " + ", ".join(b["label"] for b in todo)
            elif (st or {}).get("compose_exists"):
                rest = ("Launch it below \u2014 the addresses are beside each "
                        "map.")
            else:
                rest = "Launch it below."
            note = '<div class=note>Cluster not running. %s</div>' % rest
        return (note + _dashboard()
                + ui.render_status(_label_services(st), players=_players_now(),
                                   addresses=_addresses(),
                                   host_known=install.host_address() != "<this-host>")
                + _recent_panel())

    def _reference_foot():
        """Where to connect and what this is - at the foot, where reference belongs.

        Connect is a row per map, and so is the running-maps table. Stacked, they are
        two full-width tables of the same ten names competing for the top of the page,
        which is the shape this merge is supposed to be removing rather than creating.
        Down here it is where somebody looks when they want it and nowhere near what
        they read when something is wrong.
        """
        return ui.render_version(VERSION_INFO) + ui.FEED_LIVE

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

    def _note_step(text):
        """The page only. Ten maps settling is ten useful lines on a progress bar and
        ten pings in a chat channel, and the channel already gets one sentence with the
        whole list in its detail when the save completes."""
        ujob["step"] = text

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
            return verify_every_map(store)

        def _start_some(keys):
            """Start just these maps, and say which actually came up.

            One at a time rather than through launch(), which regenerates the compose
            file and brings the whole stack up - the point here is that some maps are
            deliberately staying down.
            """
            up = []
            for key in keys:
                ok_s, why_s = clusterctl.start_one(store, key)
                if ok_s:
                    up.append(key)
                else:
                    log.warning("could not start %s after the gate refused: %s",
                                key, why_s)
            return up

        return updatesctl.apply_batch(
            store, _ark_root(), warn=warn,
            # save_and_settle rather than save_world: the apply is about to stop the
            # cluster, so it needs the saves proved on disk rather than merely accepted.
            # The phrase "saving every world" has to survive into every one of these:
            # the stepper finds the phase by looking for it in the step text, so a tick
            # that drops it lands on no phase at all and the bar reads as if the apply
            # went backwards.
            save=lambda: clusterctl.save_and_settle(
                store, _ark_root(),
                on_settled=lambda label, done, total: _note_step(
                    "saving every world - %s saved (%d/%d)" % (label, done, total))),
            stop_all=stop_all, start_all=lambda: clusterctl.launch(store),
            verify=verify_all,
            # Asked after the stop and before the swap, while every world is a static
            # file. start_some is what keeps a refusal from costing the whole cluster:
            # the maps that are fine come back, the ones that are not stay down.
            check_worlds=lambda: clusterctl.worlds_intact(store, _ark_root()),
            start_some=_start_some,
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
        # No "Right now" card here. It is the same panel the cluster page draws
        # from the same job state, and this page is the history - two live
        # copies of one thing on two tabs is what this has been removing.
        body = ui.render_events(items, jobs=_jobs())
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
            # No update state here. The ARK build, whether it is primed, and an
            # apply in flight are all rendered by the update panel below, which is the
            # one that also carries the buttons - and two differing pictures of one
            # state is the thing this merge exists to remove, not to relocate. The
            # cards keep the jobs Obelisk owns end to end and the relay.
            return ui.render_dashboard(relay=RELAY_INFO, backup=job, restore=rjob)
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.warning("could not render the dashboard: %s", e)
            return ""

    async def cluster_maps(request):
        """Add, remove, reorder or preset the maps this cluster runs.

        One action per post, like the mod list: the editor is a list with arrows rather
        than a set of checkboxes, because the order is a fact about the cluster - the
        first map is the update master and ports are handed out down the list - and a
        checkbox set posts whatever order the catalogue happens to be in.

        The whole ordered string is rebuilt and written through the same staging path
        every other setting uses. Removing a map does not touch what that map was
        configured with: those live under store["maps"][key] and are left alone, so
        taking a map out for a month and putting it back finds it as it was.
        """
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        listed = str(store.get("maps") or "")
        preset = form.get("preset")

        def refuse_maps(text_):
            return web.HTTPFound(
                "/admin/cluster?said=%s#maps"
                % _say_next(where="maps", refusal=text_))

        # Emptying the list is a rule, not a failure. Validation refuses maps="" - a
        # cluster with no maps is not a state this manager has - so without this the
        # only remove on a single-map cluster either returned a rendered 200 carrying
        # the validator's own words (a screen away from the editor, and a resubmit if
        # the operator refreshes) or, while running, queued the empty list to break at
        # the next recreate. Both are the "agree now, find out later" this route's
        # other guard exists to prevent.
        going_now = str(form.get("drop") or "").strip()
        if going_now and mapsmod.listed(listed) == [going_now]:
            raise refuse_maps(ui.ONLY_MAP)

        # The guard, not the hint. The buttons for these are disabled while the cluster
        # runs, which is the explanation; this is what actually holds, because a
        # disabled attribute is a suggestion to anything that is not a browser.
        #
        # Reordering is refused rather than queued, which is the one place this manager
        # does not queue a change. Everything else means the same thing before and after
        # a restart; an order does not. Ports are handed out walking the list, so moving
        # an entry moves the address somebody already has in their launcher, and moving
        # the first changes which map downloads the server files. Queuing that would be
        # agreeing now and finding out at the recreate.
        if _running_now():
            here = mapsmod.listed(listed)
            if form.get("up") or form.get("down"):
                raise refuse_maps(ui.REORDER_RUNNING)
            if preset:
                raise refuse_maps(ui.PRESET_RUNNING)
            # Removing the last one is free: nothing comes after it to move down, and
            # the first map - the update master - is not it unless it is the only one,
            # which the rule above has already refused.
            if going_now and here and going_now != here[-1]:
                name = ((mapsmod.entry(store, going_now) or {}).get("name")
                        or going_now)
                raise refuse_maps(ui.REMOVE_RUNNING % name)
        try:
            if preset:
                from .presets import BY_KEY as PRESET_BY_KEY
                chosen = PRESET_BY_KEY.get(preset, {}).get("maps", [])
                listed = ",".join(chosen)
            elif form.get("add"):
                listed = mapsmod.add(store, listed, str(form.get("add")).strip())
            elif form.get("drop"):
                listed = mapsmod.remove(listed, str(form.get("drop")).strip())
            elif form.get("up"):
                listed = mapsmod.move(listed, str(form.get("up")).strip(), -1)
            elif form.get("down"):
                listed = mapsmod.move(listed, str(form.get("down")).strip(), 1)
        except ValueError as e:
            # A rule, not a failure: nothing was written and the list is what it was.
            raise web.HTTPFound(
                "/admin/cluster?said=%s#maps"
                % _say_next(where="maps",
                            refusal="%s - the map list is unchanged."
                                    % str(e).strip().rstrip(".")))
        chosen = mapsmod.listed(listed)
        try:
            live, later = _stage_or_apply({"maps": listed})
            store.patch(live)
            if later:
                pendingctl.stage(store, later)
                announce.say("change.staged",
                             "Map selection saved and waiting for a safe moment: %s."
                             % ", ".join(chosen), detail=_pending_detail(),
                             count=pendingctl.count(store))
            store.save()
        except Invalid as e:
            return chrome(_cluster_body(request, problem=str(e)), "Cluster",
                          "/admin/cluster")
        raise web.HTTPFound("/admin/cluster#maps")

    cluster_busy = APPLY_LOCK          # module level: see the comment there

    def _act(fn, request):
        """Launch, synchronously. The stop has its own path below - it is the one that
        takes minutes and the one somebody has to be warned before."""
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

    def bot_can_whisper(name):
        from . import bot
        return bot.can_whisper(name)

    # ---- saying something to one player
    #
    # The first of the moderation actions, and the only one that affects nobody: a line
    # of chat. It is unguarded for that reason and announced for the same reason every
    # other significant thing here is - an admin who did not send it should be able to
    # see that somebody did.
    #
    # What RCON can tell us is that the server took the command. It cannot tell us the
    # player read it, or was still standing there when it arrived. So the page says
    # "sent", and never "messaged" or "delivered", which are claims about the other end.
    async def player_message(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        label = str(form.get("map") or "").strip()
        name = str(form.get("name") or "").strip()
        text = " ".join(str(form.get("text") or "").split())

        def refuse(text_):
            """Nothing happened and nothing is broken - the amber the stop guard and
            the restore guards already use for exactly this."""
            return chrome(_cluster_body(request, refusal=ui.warn_block(text_)),
                          "Cluster", "/admin/cluster")

        def broke(text_):
            """Red is for a command that went to the server and did not work."""
            return chrome(_cluster_body(request, problem=text_),
                          "Cluster", "/admin/cluster")

        if not text:
            return refuse("Type a message first - nothing was sent.")
        if not name or not label:
            return refuse("That row did not say who to message. Reload the page "
                          "and try again - nothing was sent.")
        if not bot_can_whisper(name):
            return refuse("%s cannot be messaged: the name has a quote or a line break "
                          "in it, and the in-game command puts the name in quotes with "
                          "no way to escape one. Nothing was sent." % name)

        # The page was describing a moment. Between rendering it and pressing Send the
        # poll may have run again, the player may have left, the map may have stopped
        # answering - and a message addressed into that is not a message, it is a
        # command sent at a guess. The roster is the same one the page was drawn from,
        # so asking it again is asking whether the page is still true.
        snap = _roster_now()
        if snap is None:
            return refuse("The chat relay is not running, so there is nobody to "
                          "message - nothing was sent.")
        on_map = (snap.get("by_map") or {}).get(label)
        if on_map is None:
            return refuse("%s did not answer the last check, so who is on it is "
                          "not known - nothing was sent to %s." % (label, name))
        if not any(p.get("name") == name for p in on_map):
            return refuse("%s is no longer listed on %s - nothing was sent."
                          % (name, label))

        target = None
        for lbl, host, port in clusterctl.rcon_targets(store):
            if lbl == label:
                target = (host, port)
        if target is None:
            return refuse("%s is not a map this cluster runs - nothing was sent."
                          % label)

        from . import bot
        try:
            await bot.rcon_with(target[0], target[1],
                                str(store.get("admin_password") or ""),
                                bot.whisper_command(name, text), timeout=10)
        except Exception as e:                       # noqa: BLE001 - reported as itself
            why = str(e).strip() or e.__class__.__name__
            announce.say("player.message_failed",
                         "A message to %s on %s did NOT send: %s" % (name, label, why),
                         level="error", map=label, player=name)
            return broke("The message did NOT send to %s on %s: %s. Nothing "
                         "reached the server." % (name, label, why))

        # Accepted. ARK answers most writes with "Server received, But no response!!",
        # which is the server saying it took the command and has nothing to add - not
        # a failure. Either way the only honest claim is that it was sent.
        announce.say("player.message_sent",
                     "Message sent to %s on %s from the web UI." % (name, label),
                     map=label, player=name, detail=text)
        # Redirect, like every other action on this page. Answering a POST with a page
        # means a refresh re-posts it - and a re-sent message is a second line of chat
        # the player sees, from somebody who pressed F5.
        raise web.HTTPFound(
            "/admin/cluster?said=%s#who"
            % _say_next(where="who",
                        message="Message sent to %s on %s." % (name, label)))

    # ---- disconnecting one player
    #
    # The first action here that affects somebody. It is guarded by a page rather than
    # a dialog, the way the stop guard is: the answer comes back from the server, it
    # survives a second tab, and it can be tested without a browser.
    #
    # A click-through rather than a typed name. A kick costs the walk back from a spawn
    # point and nothing that was built; making an operator type a name for this and for
    # a ban would teach them to type it without reading, which is the guard the ban
    # needs to keep.
    async def player_kick(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        label = str(form.get("map") or "").strip()
        name = str(form.get("name") or "").strip()
        netid = str(form.get("netid") or "").strip()
        confirmed = bool(form.get("confirm"))

        def refuse(text_):
            """Nothing happened and nothing is broken."""
            return chrome(_cluster_body(request, refusal=ui.warn_block(text_)),
                          "Cluster", "/admin/cluster")

        def broke(text_):
            """Red is for a command that went to the server and did not work."""
            return chrome(_cluster_body(request, problem=text_),
                          "Cluster", "/admin/cluster")

        if not (name and label and netid):
            return refuse("That row did not say who to kick. Reload the page and try "
                          "again - nothing has been done.")

        # The page was describing a moment. Between rendering it and pressing Kick the
        # poll may have run again, the player may have left, the map may have stopped
        # answering. Checked before the confirmation as well as after it, so the
        # question is never asked about somebody who has already gone.
        def still_there():
            snap = _roster_now()
            if snap is None:
                return None, ("The chat relay is not running, so there is nobody to "
                              "kick - nothing has been done.")
            on_map = (snap.get("by_map") or {}).get(label)
            if on_map is None:
                return None, ("%s did not answer the last check, so who is on it is "
                              "not known - %s has not been kicked." % (label, name))
            if not any(p.get("netid") == netid for p in on_map):
                return None, ("%s is no longer listed on %s - nothing has been done."
                              % (name, label))
            return on_map, ""

        _on, why_not = still_there()
        if why_not:
            return refuse(why_not)

        if not confirmed:
            # Asked on the row itself. At the top of the page it bounced the operator
            # away from what they were reading and left a live Kick underneath - two
            # routes to the same act, one of them unconfirmed.
            return chrome(
                _cluster_body(request, pending={
                    "map": label, "netid": netid,
                    "html": ui.render_kick_confirm(label, name, netid)}),
                "Cluster", "/admin/cluster")

        target = None
        for lbl, host, port in clusterctl.rcon_targets(store):
            if lbl == label:
                target = (host, port)
        if target is None:
            return refuse("%s is not a map this cluster runs - nothing has been done."
                          % label)

        from . import bot
        try:
            await bot.rcon_with(target[0], target[1],
                                str(store.get("admin_password") or ""),
                                "KickPlayer %s" % netid, timeout=10)
        except Exception as e:                       # noqa: BLE001 - reported as itself
            why = str(e).strip() or e.__class__.__name__
            announce.say("player.kick_failed",
                         "A kick for %s on %s did NOT send: %s" % (name, label, why),
                         level="error", map=label, player=name)
            return broke("The kick did NOT send to %s on %s: %s. Nothing reached the "
                         "server, and they are still on it." % (name, label, why))

        # Accepted. ARK answers most writes with "Server received, But no response!!",
        # which is the server taking the command and having nothing to add. What we can
        # say is that it was sent; whether the player is off is the server's business
        # and the next poll's answer.
        announce.say("player.kick_sent",
                     "Kick sent for %s on %s from the web UI." % (name, label),
                     map=label, player=name)
        # "The next check will show" promised an update this section does not make on
        # its own - the roster is drawn from whatever the last poll left, and nothing
        # here refreshes it. Reload is the honest instruction, and the list already
        # says how old it is.
        raise web.HTTPFound(
            "/admin/cluster?said=%s#who"
            % _say_next(where="who",
                        message="Kick sent for %s on %s. This list is from the last "
                                "check - reload to see whether they are off."
                                % (name, label)))

    # ---- banning somebody from the whole cluster
    #
    # The heaviest thing on this page, and the one place the per-map shape of ARK
    # actually bites. A ban is not a cluster fact: every server keeps its own
    # BanList.txt, so banning on The Island bans from The Island and the player walks
    # over to Ragnarok. It has to be sent to every map, and what happened on each of
    # them has to be said out loud, because "banned on eight of ten maps" is the answer
    # that looks most like success and is least like it.
    #
    # And the ban list only refuses the NEXT connection. Somebody already standing on a
    # map stays there until they log off, so the ban is followed by a kick - without it
    # "banned" is false for as long as they care to keep playing.
    async def player_ban(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        label = str(form.get("map") or "").strip()
        name = str(form.get("name") or "").strip()
        netid = str(form.get("netid") or "").strip()
        typed = str(form.get("confirm") or "")

        def refuse(text_):
            return chrome(_cluster_body(request, refusal=ui.warn_block(text_)),
                          "Cluster", "/admin/cluster")

        def broke(text_):
            return chrome(_cluster_body(request, problem=text_),
                          "Cluster", "/admin/cluster")

        def ask(problem=""):
            return chrome(
                _cluster_body(request, pending={
                    "map": label, "netid": netid,
                    "html": ui.render_ban_confirm(label, name, netid, problem)}),
                "Cluster", "/admin/cluster")

        if not (name and label and netid):
            return refuse("That row did not say who to ban. Reload the page and try "
                          "again - nothing has been done.")

        # Checked before anything else touches it. For a kick a malformed id is a
        # command the server declines; for a ban it is a line written into ten files
        # that the game re-reads on every connection for ever.
        if not bansctl.valid_netid(netid):
            return refuse("%s cannot be banned: the id the server gave for them is not "
                          "one that can be written to a ban list safely. Nothing has "
                          "been done." % name)

        snap = _roster_now()
        if snap is None:
            return refuse("The chat relay is not running, so there is nobody to ban - "
                          "nothing has been done.")
        on_map = (snap.get("by_map") or {}).get(label)
        if on_map is None:
            return refuse("%s did not answer the last check, so who is on it is not "
                          "known - %s has not been banned." % (label, name))
        if not any(p.get("netid") == netid for p in on_map):
            return refuse("%s is no longer listed on %s - nothing has been done."
                          % (name, label))

        if not typed.strip():
            return ask()
        if not restorectl.typed_matches(name, typed):
            return ask("That is not their name. Nothing has been done.")

        # Every map at once rather than one after another: ten maps at ten seconds each
        # is a page that looks hung, and they have nothing to do with each other.
        from . import bot
        password = str(store.get("admin_password") or "")
        targets = clusterctl.rcon_targets(store)

        async def ban_one(lbl, host, port):
            try:
                await bot.rcon_with(host, port, password, "BanPlayer %s" % netid,
                                    timeout=10)
                return lbl, ""
            except Exception as e:                   # noqa: BLE001 - the reason is data
                return lbl, (str(e).strip() or e.__class__.__name__)

        results = dict(await asyncio.gather(
            *(ban_one(l, h, p) for l, h, p in targets)))
        took = sorted(l for l, why in results.items() if not why)
        missed = sorted((l, why) for l, why in results.items() if why)

        # Then the kick, on the map they are standing on. Only worth attempting if the
        # ban reached that map - kicking somebody off a server that did not take the
        # ban just sends them back through a door that is still open.
        #
        # Two different things can leave them still standing there, and they read as
        # one sentence if they share a phrasing: the kick was tried and did not land,
        # or it was never sent because the ban did not reach that map. kick_why is the
        # reason for the ledger; kick_note is the sentence for the operator.
        kick_why = kick_note = ""
        if label in took:
            here = [(h, p) for l, h, p in targets if l == label]
            if here:
                try:
                    await bot.rcon_with(here[0][0], here[0][1], password,
                                        "KickPlayer %s" % netid, timeout=10)
                except Exception as e:               # noqa: BLE001 - reported, not fatal
                    kick_why = str(e).strip() or e.__class__.__name__
                    kick_note = (" The kick did not send (%s), so they stay on %s until "
                                 "they log off." % (kick_why, label))
        else:
            kick_why = "%s did not take the ban, so no kick was sent there" % label
            kick_note = (" They were not kicked either, because %s is the map that did "
                         "not take the ban - removing them from a door still open would "
                         "only look like it worked." % label)

        def reasons(items, cap=4):
            """The first few maps and why, without pretending there were only a few."""
            shown = "; ".join("%s (%s)" % (l, w) for l, w in items[:cap])
            return shown + ("; and %d more" % (len(items) - cap)
                            if len(items) > cap else "")

        if not took:
            announce.say("player.ban_failed",
                         "A ban for %s did NOT send to any map: %s"
                         % (name, reasons(missed)),
                         level="error", player=name, netid=netid)
            return broke("The ban did NOT send to any of the %d maps: %s. Nothing was "
                         "written and %s is still able to play."
                         % (len(results), reasons(missed), name))

        # Recorded whatever the spread, because the ledger is what Obelisk did rather
        # than what worked - and the maps it missed are precisely what somebody has to
        # deal with afterwards.
        bansctl.record(store, name, netid, results, kick=kick_why)

        if missed:
            announce.say(
                "player.ban_partial",
                "Ban sent for %s on %d of %d maps. NOT sent on %s - they can still "
                "join those." % (name, len(took), len(results),
                                 clusterctl._and([l for l, _w in missed])),
                level="warning", player=name, netid=netid,
                detail="\n".join("%-14s %s" % (l, w or "sent")
                                  for l, w in sorted(results.items())))
            # Redirected like the clean ban, not rendered. A partial answered with a
            # page is a POST sitting in the browser's history: a refresh re-runs the
            # whole fan-out and, worse, writes a second ledger entry for one ban - and
            # the ledger is the only record there is of what was banned, so a phantom
            # row there makes the list lie and makes "which of these does Unban undo?"
            # a question about an artefact. The amber comes back through the same
            # one-shot slot the clean ban uses, so it is still shown once, to the
            # person who pressed the button.
            raise web.HTTPFound(
                "/admin/cluster?said=%s#who"
                % _say_next(where="who",
                            problem="Ban sent for %s on %d of %d maps - NOT on %s, and "
                                    "they can still join those.%s Recorded - see Banned "
                                    "players below. Try those maps again, "
                                    "or check they are reachable."
                                    % (name, len(took), len(results),
                                       clusterctl._and([l for l, _w in missed]),
                                       kick_note)))

        announce.say("player.ban_sent",
                     "Ban sent for %s on all %d maps from the web UI.%s"
                     % (name, len(results),
                        " The kick did not send: %s" % kick_why if kick_why else ""),
                     level="info", player=name, netid=netid,
                     detail="\n".join("%-14s sent" % l for l in took))
        raise web.HTTPFound(
            "/admin/cluster?said=%s#who"
            % _say_next(where="who",
                        message="Ban sent for %s on all %d maps.%s Recorded - "
                                "see Banned players below. This list is from "
                                "the last check - reload to see it."
                                % (name, len(results), kick_note)))

    # ---- letting somebody back in
    #
    # The same fan-out as the ban and for the same reason: the ban went into ten
    # separate BanList.txt files, so taking it out of nine of them leaves a player who
    # is banned from one map and cannot understand why.
    #
    # Keyed on the id rather than on a row of the list. Two records of the same id is a
    # normal thing to have - a ban that reached eight maps and was sent again is two
    # honest records - and "undo entry 3" would then leave the same person banned by
    # entry 2. There is one question worth asking here, and it is about the player.
    async def player_unban(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        netid = str(form.get("netid") or "").strip()
        name = str(form.get("name") or "").strip()
        when = str(form.get("when") or "").strip()
        confirmed = str(form.get("confirm") or "").strip()
        who = name or "that id"

        def refuse(text_):
            return chrome(_cluster_body(request, refusal=ui.warn_block(text_)),
                          "Cluster", "/admin/cluster")

        if not netid:
            return refuse("That did not say which id to unban - nothing has been done.")

        # Whitelisted like the ban's id, though this one is written to no file. It is
        # still an argument to a console command, and unlike the ban's it can be typed:
        # the by-id field is there precisely for ids this manager never saw.
        if not bansctl.valid_netid(netid):
            return refuse("%s is not an id that can be unbanned - platform ids are "
                          "letters and digits. Nothing has been done."
                          % (netid[:24] + ("..." if len(netid) > 24 else "")))

        if not confirmed:
            return chrome(
                _cluster_body(request, pending={
                    "where": "bans", "netid": netid, "when": when,
                    "html": ui.render_unban_confirm(name, netid, when)}),
                "Cluster", "/admin/cluster")

        from . import bot
        password = str(store.get("admin_password") or "")
        targets = clusterctl.rcon_targets(store)

        async def unban_one(lbl, host, port):
            try:
                await bot.rcon_with(host, port, password, "UnbanPlayer %s" % netid,
                                    timeout=10)
                return lbl, ""
            except Exception as e:                   # noqa: BLE001 - the reason is data
                return lbl, (str(e).strip() or e.__class__.__name__)

        results = dict(await asyncio.gather(
            *(unban_one(l, h, p) for l, h, p in targets)))
        took = sorted(l for l, why in results.items() if not why)
        gone = sorted((l, why) for l, why in results.items() if why)

        def reasons(items, cap=4):
            shown = "; ".join("%s (%s)" % (l, w) for l, w in items[:cap])
            return shown + ("; and %d more" % (len(items) - cap)
                            if len(items) > cap else "")

        if not took:
            announce.say("player.unban_failed",
                         "An unban for %s did NOT send to any map: %s"
                         % (who, reasons(gone)),
                         level="error", player=name, netid=netid)
            return chrome(_cluster_body(request, problem=(
                "The unban did NOT send to any of the %d maps: %s. Nothing has changed "
                "and %s is still banned." % (len(results), reasons(gone), who))),
                "Cluster", "/admin/cluster")

        # Marked, not removed, and only once the command actually went somewhere. An
        # entry that says "unbanned" while every server still refuses them is the one
        # thing this list must not do.
        marked = bansctl.mark_unbanned(store, netid)
        kept = (" The record is kept and marked unbanned." if marked else
                " Obelisk had no record of that id, so nothing in the list changed.")

        if gone:
            announce.say(
                "player.unban_partial",
                "Unban sent for %s on %d of %d maps. NOT sent on %s - they are still "
                "banned there." % (who, len(took), len(results),
                                   clusterctl._and([l for l, _w in gone])),
                level="warning", player=name, netid=netid,
                detail="\n".join("%-14s %s" % (l, w or "sent")
                                    for l, w in sorted(results.items())))
            raise web.HTTPFound(
                "/admin/cluster?said=%s#bans"
                % _say_next(where="bans",
                            problem="Unban sent for %s on %d of %d maps - NOT on %s, "
                                    "and they are still banned there.%s Try those maps "
                                    "again, or check they are reachable."
                                    % (who, len(took), len(results),
                                       clusterctl._and([l for l, _w in gone]), kept)))

        announce.say("player.unban_sent",
                     "Unban sent for %s on all %d maps from the web UI."
                     % (who, len(results)),
                     level="info", player=name, netid=netid,
                     detail="\n".join("%-14s sent" % l for l in took))
        raise web.HTTPFound(
            "/admin/cluster?said=%s#bans"
            % _say_next(where="bans",
                        message="Unban sent for %s on all %d maps - they can join "
                                "again.%s" % (who, len(results), kept)))

    # ---- letting somebody past the player cap
    #
    # AllowPlayerToJoinNoCheck exempts one id from MaxPlayers: that player gets in when
    # the server is full. It is not the join allow-list, and nothing here calls it a
    # whitelist - to an ARK admin that word is the file deciding who may connect at
    # all, and calling this by that name would be the most expensive kind of wrong.
    #
    # Both directions fan out, because the cap is per server like the ban list: allowed
    # on nine maps of ten is a player who cannot get into the tenth when it fills, and
    # revoked on nine is somebody still walking past the cap on the one.
    #
    # What this cannot do is tell anybody who is currently allowed. There is no RCON
    # command that reads PlayersJoinNoCheckList back, so the section under this is a
    # log of what Obelisk sent rather than a switch showing a state nothing can check.
    async def player_cap(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        netid = str(form.get("netid") or "").strip()
        when = str(form.get("when") or "").strip()
        confirmed = str(form.get("confirm") or "").strip()
        revoking = str(form.get("action") or "allow").strip() == "revoke"
        doing = "revoke" if revoking else "allow"

        def refuse(text_):
            return chrome(_cluster_body(request, refusal=ui.warn_block(text_)),
                          "Cluster", "/admin/cluster")

        if not netid:
            return refuse("That did not say which id to %s - nothing has been done."
                          % doing)

        # The same id check the ban writes through, for a field that is typed by hand
        # every time: there is no roster row to take this id from, because somebody who
        # needs letting past a full server is by definition not on it.
        #
        # Called an id check rather than by its other common name, because that name
        # means the join allow-list to every ARK admin - which is precisely the thing
        # this feature is not, three lines away. The test refuses the word outright
        # rather than trying to judge which sense a future edit meant.
        if not bansctl.valid_netid(netid):
            return refuse("%s is not an id - platform ids are letters and digits. "
                          "Nothing has been done."
                          % (netid[:24] + ("..." if len(netid) > 24 else "")))

        if not confirmed:
            return chrome(
                _cluster_body(request, pending={
                    "where": "cap", "netid": netid, "when": when,
                    "html": ui.render_cap_confirm(netid, doing, when)}),
                "Cluster", "/admin/cluster")

        from . import bot
        password = str(store.get("admin_password") or "")
        targets = clusterctl.rcon_targets(store)
        command = ("DisallowPlayerToJoinNoCheck %s" if revoking
                   else "AllowPlayerToJoinNoCheck %s") % netid

        async def cap_one(lbl, host, port):
            try:
                await bot.rcon_with(host, port, password, command, timeout=10)
                return lbl, ""
            except Exception as e:                   # noqa: BLE001 - the reason is data
                return lbl, (str(e).strip() or e.__class__.__name__)

        results = dict(await asyncio.gather(
            *(cap_one(l, h, p) for l, h, p in targets)))
        took = sorted(l for l, why in results.items() if not why)
        gone = sorted((l, why) for l, why in results.items() if why)

        def reasons(items, cap=4):
            shown = "; ".join("%s (%s)" % (l, w) for l, w in items[:cap])
            return shown + ("; and %d more" % (len(items) - cap)
                            if len(items) > cap else "")

        did = "revoke for" if revoking else "allow for"
        if not took:
            announce.say("player.cap_%s_failed" % doing,
                         "A cap %s %s did NOT send to any map: %s"
                         % (did, netid, reasons(gone)),
                         level="error", netid=netid)
            return chrome(_cluster_body(request, problem=(
                "The %s did NOT send to any of the %d maps: %s. Nothing has changed "
                "and nothing was written down."
                % (doing, len(results), reasons(gone)))),
                "Cluster", "/admin/cluster")

        # Written only once a map has taken it. A log saying "allowed" while every
        # server refused the command is worth less than no log at all.
        if revoking:
            marked = capctl.mark_revoked(store, netid)
            kept = (" The record is kept and marked revoked." if marked else
                    " Obelisk had no record of letting that id past, so nothing in "
                    "the list changed.")
        else:
            capctl.record(store, netid, results)
            kept = " Recorded below."

        if gone:
            announce.say(
                "player.cap_%s_partial" % doing,
                "Cap %s %s sent on %d of %d maps. NOT sent on %s."
                % (did, netid, len(took), len(results),
                   clusterctl._and([l for l, _w in gone])),
                level="warning", netid=netid,
                detail="\n".join("%-14s %s" % (l, w or "sent")
                                    for l, w in sorted(results.items())))
            raise web.HTTPFound(
                "/admin/cluster?said=%s#cap"
                % _say_next(where="cap",
                            problem="%s sent for %s on %d of %d maps - NOT on %s, so "
                                    "%s there.%s Try those maps again, or check they "
                                    "are reachable."
                                    % ("Revoke" if revoking else "Allow", netid,
                                       len(took), len(results),
                                       clusterctl._and([l for l, _w in gone]),
                                       "the allow still stands" if revoking
                                       else "they are still held to the cap", kept)))

        announce.say("player.cap_%s_sent" % doing,
                     "Cap %s %s sent on all %d maps from the web UI."
                     % (did, netid, len(results)),
                     level="info", netid=netid,
                     detail="\n".join("%-14s sent" % l for l in took))
        raise web.HTTPFound(
            "/admin/cluster?said=%s#cap"
            % _say_next(where="cap",
                        message="%s sent for %s on all %d maps.%s"
                                % ("Revoke" if revoking else "Allow", netid,
                                   len(results), kept)))

    def _points_for(key):
        """The game's own dated saves for one map. Read from disk each time - ARK
        prunes them on its own schedule and a cached list offers rollbacks that are
        already gone."""
        try:
            return pointsctl.list_points(store, key)
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.info("could not list restore points for %s: %s", key, e)
            return []

    async def map_page(request):
        """One map: its ports, its RAM and why, its address, and its saves.

        Addressed by the map's own key the whole way down - the plan row, the world
        folder, the settings overrides and the restore guards are all keyed that way
        already, so this page invents no identity and reverses none.
        """
        if not authed(request):
            raise web.HTTPFound("/setup")
        key = str(request.match_info.get("key") or "")
        if not mapsmod.known(store, key):
            raise web.HTTPFound("/admin/cluster#run")
        name = mapsmod.entry(store, key)["name"]
        try:
            rows = build_plan(store).get("maps") or []
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.warning("could not build the plan for %s: %s", key, e)
            rows = []
        row = None
        for r in rows:
            if r.get("map") == key:
                row = r
                break
        host = install.host_address()
        address = "%s:%d" % (host, row["game_port"]) if row else ""
        # Which state this map is in, from the same labelled status the overview reads.
        # Enough to confirm the operator landed where they meant to; the live picture
        # and the count stay on the one page that polls for every map at once.
        state = None
        try:
            for svc in (_label_services(clusterctl.status(store)).get("services")
                        or []):
                if svc.get("map") == key:
                    state = svc
                    break
        except Exception as e:                       # noqa: BLE001 - never a blank page
            log.info("could not read the state of %s: %s", key, e)
        q = pendingctl.queued(store)
        overrides = ui.render_map_overrides(
            store, key, queued=(q.get("maps") or {}).get(key) or {},
            clears=(q.get("clears") or {}).get(key) or ())
        notice = ""
        token = str((getattr(request, "query", None) or {}).get("said") or "")
        said = _said.pop(token, None) if token else None
        if said and said.get("where") == "map":
            notice = ('<div class=note>%s</div>' % ui._e(said["message"])
                      if said.get("message") else
                      ui.warn_block(said.get("problem") or said.get("refusal") or ""))
        try:
            launched = bool(clusterctl.status(store).get("compose_exists"))
        except Exception:                            # noqa: BLE001 - never a blank page
            launched = False
        return chrome(ui.render_map(name, key, row=row, address=address,
                                    host_known=host != "<this-host>",
                                    points=_points_for(key) if row else [],
                                    job=rjob, state=state, overrides=overrides,
                                    notice=notice, launched=launched),
                      name, "/admin/cluster")

    async def cluster_launch(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return await _act_once(clusterctl.launch, request)

    # ---- stopping the cluster: who is on, and where it has got to
    #
    # One stop at a time, and what it is currently doing. A stop takes minutes on ten
    # maps and the request used to be held open for all of them, so the browser sat on
    # a dead tab with no way to tell a stop that was working from one that had hung -
    # and nobody had been given the chance to find out who was playing first.
    sjob = {"state": "idle", "step": "", "message": "", "ok": None, "started": 0.0}

    def _stop_say(event, text, **kw):
        """The stop's own description, to the channel and to the page at once.

        cluster.stop already emits a stage per map and one slot-edited message to
        Discord. The page was the only surface not reading it. Nothing new is measured
        here - it is the same sentence, written somewhere else as well.
        """
        sjob["step"] = text
        announce.say(event, text, **kw)

    def _run_stop():
        # A stop is a story rather than a moment: the request, every stage inside it
        # and the result all belong to one slot, so the channel carries a single status
        # line from "Stop requested" to "Cluster stopped." The result ends the slot, so
        # the next stop starts a fresh message instead of rewriting this one.
        slot = clusterctl.STOP_SLOT
        _stop_say("cluster.stop", "Stop requested from the web UI.", slot=slot)
        ok, msg = clusterctl.stop(store, say=_stop_say)
        _stop_say("cluster.stop.done" if ok else "cluster.stop.failed", msg,
                  level="info" if ok else "error", slot=slot, slot_end=True)
        return ok, msg

    async def _stop_task():
        try:
            async with cluster_busy:
                ok, msg = await asyncio.to_thread(_run_stop)
        except Exception as e:                       # noqa: BLE001 - surfaced below
            ok, msg = False, "Stopping the cluster failed: %s" % e
            log.exception("stopping the cluster failed")
        sjob.update(state="done", ok=ok, message=msg, step=msg if ok else "failed")

    async def cluster_stop(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        if sjob["state"] == "running" or cluster_busy.locked():
            return chrome(_cluster_body(
                request, message="Already working on the last request - this one was "
                                 "ignored rather than run twice."),
                "Cluster", "/admin/cluster")

        form = await request.post()
        force = bool(form.get("force"))
        if not force:
            # Asked off the loop: ten RCON round trips is not something to do inside a
            # request handler. A map that does not answer counts as occupied, for the
            # reason it counts everywhere else - "it is not known whether anyone is on
            # it" is not "nobody is on it".
            try:
                _total, counts, silent = await asyncio.to_thread(
                    clusterctl.players_online, store)
            except Exception as e:                   # noqa: BLE001 - reported, not fatal
                log.warning("could not count players before stopping: %s", e)
                _total, counts, silent = 0, {}, [("the cluster", str(e))]
            if any(n for n in (counts or {}).values()) or silent:
                announce.say(
                    "cluster.stop_refused",
                    "Stop refused: %s Nothing has been stopped."
                    % ui.stop_reason(counts, silent), level="warning")
                return chrome(_cluster_body(
                    request, refusal=ui.render_stop_warning(counts, silent)),
                    "Cluster", "/admin/cluster")

        sjob.update(state="running", ok=None, message="", step="starting",
                    started=time.time())
        # Started, not awaited. Holding the response across the stop is what made the
        # page look hung for the several minutes it takes ten maps to close their
        # worlds - the one stretch where somebody most wants to know it is working.
        asyncio.create_task(_stop_task())
        raise web.HTTPFound("/admin/cluster")

    def _sjob_live():
        out = dict(sjob)
        out["elapsed"] = int(time.time() - sjob["started"]) if sjob.get("started") else 0
        return out

    async def cluster_status(request):
        if not authed(request):
            return web.json_response({"state": "denied"}, status=403)
        live = _sjob_live()
        # The panel, not the wrapper. The poller puts this inside #stopwrap, so
        # handing it the wrapper as well nested one inside the other.
        live["html"] = ui.render_stop_panel(live)
        return web.json_response(live)

    def _addresses():
        """{map name: host:port}, for the column that replaced the Connect table.

        Derived from the plan rather than measured, which is why it can sit in the same
        row as the live state without the two being able to disagree: a port is a fact
        about what was planned, not a reading of what is happening.
        """
        try:
            plan = build_plan(store)
        except Exception:                            # noqa: BLE001 - never a blank page
            return {}
        host = install.host_address()
        return {r["name"]: "%s:%d" % (host, r["game_port"])
                for r in (plan.get("maps") or [])}

    def _web_address():
        """Where Obelisk answers - not a fact about a map, so not in the map table."""
        return "http://%s:%s/" % (install.host_address(), store.get("status_port"))

    # ---- backups
    def _flush_for(store_):
        """The SaveWorld callable, only when the operator asked for it."""
        if not store_.get("backup_flush"):
            return None
        return lambda: clusterctl.save_world(store_)

    # ---- one place for the copies of this cluster, and what loads into it
    #
    # Backups, off-site, restore and mods were four tabs and four pages. Three of them
    # are one story told in order - make a copy, put the copy somewhere else, put it
    # back - and reading it meant three tabs, with the archive you were restoring listed
    # on one page and the thing that made it on another. They are sections now, in the
    # order the story runs.
    #
    # Every form still posts where it always did. The addresses are the contract this
    # page does not get to change: what moved is where a result is rendered, not where
    # it is sent.
    DATA_SECTIONS = (("#backups", "Back up"), ("#cloud", "Off-site"),
                     ("#restore", "Restore"))

    def _cloud_section(msg="", problem="", warning=""):
        st = cloudctl.status(store)
        rows = []
        if st.get("connected"):
            ok, res = cloudctl.listing(store)
            rows = res if ok and isinstance(res, list) else []
        return ui.render_cloud(store, st, rows, message=msg, problem=problem,
                               warning=warning)

    def _mods_section(problem="", refusal=""):
        """The mod list, the passive list, and the sentence that tells them apart.

        passive_mods used to be a field in a "Mods" group eighteen groups and a hundred
        and twenty thousand characters away from the list it belongs beside - two index
        entries both reading "Mods", with even odds of clicking the wrong one. It is a
        mod list too, so it is here; it saves through the settings writer, so it keeps
        its own small form.
        """
        # Names and categories come from the check the watcher already did, so the
        # page renders without waiting on anybody's API.
        known = {r["id"]: r for r in (ARK_UPDATE.get("mods") or [])}
        try:
            waiting = pendingctl.queued(store)["cluster"]
        except Exception:                            # noqa: BLE001 - never a blank page
            waiting = {}
        return (ui.MODS_ACT_NOW
                + ui.render_mods(store, modsctl.measure(layout.mods_dir(store)),
                                 found=_found["card"],
                                 problem=problem or _found["problem"], known=known,
                                 refusal=refusal)
                + ui.render_data_settings(store, ("passive_mods",), "/admin#mods",
                                          queued=waiting, legend="Passive mods"))

    def _data_body(backups=None, cloud=None, restore=None):
        """The four sections, each with its own result slot.

        A result belongs to the section that caused it, the same rule the cluster
        page's actions follow: the operator pressed a button in one of four places and
        the answer has to come back where they are looking.
        """
        b, c, r = (backups or {}), (cloud or {}), (restore or {})
        titles = dict((a.lstrip("#"), t) for a, t in DATA_SECTIONS)
        return (ui.render_jump_row(DATA_SECTIONS)
                + ui.render_area("backups", titles["backups"], ui.render_backups(
                    store, backupctl.listing(store),
                    message=b.get("message", ""), problem=b.get("problem", ""))
                    + _schedule_fields(("backup_times", "backup_keep", "backup_flush"),
                                       "backups", "When backups happen"))
                + ui.render_area("cloud", titles["cloud"], _schedule_after(
                    _cloud_section(
                    msg=c.get("message", ""), problem=c.get("problem", ""),
                    warning=c.get("warning", "") or c.get("refusal", "")),
                    ("cloud_enabled", "cloud_keep"), "cloud",
                    "What goes off-site"))
                + ui.render_area("restore", titles["restore"], _restore_body(
                    message=r.get("message", ""), problem=r.get("problem", ""),
                    refusal=r.get("refusal", ""))))

    def _schedule_fields(keys, section, legend):
        """The settings that govern this section, on the page with its buttons."""
        try:
            q = pendingctl.queued(store)["cluster"]
        except Exception:                            # noqa: BLE001 - never a blank page
            q = {}
        return ui.render_data_settings(store, keys, "/admin/data#" + section,
                                       queued=q, legend=legend,
                                       anchor="schedule" if section == "backups" else "")

    def _schedule_after(body, keys, section, legend):
        return body + _schedule_fields(keys, section, legend)

    def _data(**kw):
        return chrome(_data_body(**kw), "Data", "/admin/data")

    def _data_next(where, **kw):
        """Park a result for one section and send the browser to that section.

        The redirect-style actions already landed where they belonged; the ones that
        rendered their answer left the operator at the top of a long page with the
        answer half a screen down, and the address bar on /admin/restore/run. Same
        one-shot slot the cluster page's actions use, same reason.
        """
        return web.HTTPFound("/admin/data?said=%s#%s"
                             % (_say_next(where=where, **kw), where))

    def _said_section(request):
        """A parked result, if this request carries its token, as _data_body kwargs."""
        token = str((getattr(request, "query", None) or {}).get("said") or "")
        said = _said.pop(token, None) if token else None
        where = (said or {}).get("where") or ""
        if where not in ("backups", "cloud", "restore"):
            return {}
        return {where: {"message": said.get("message", ""),
                        "problem": said.get("problem", ""),
                        "refusal": said.get("refusal", "")}}

    async def data_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        return _data(**_said_section(request))

    async def backups_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        raise web.HTTPFound("/admin/data#backups")

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
        # No level at all meant info, so a manual backup that FAILED was announced in
        # the styling of one that worked - under an icon that is a cross. The event
        # name was right and everything around it said routine.
        announce.say("backup.done" if ok else "backup.failed", msg,
                     level="info" if ok else "error")
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
            raise web.HTTPFound("/admin/data#backups")
        # Started, not awaited. Compressing the data root takes minutes, and doing that
        # inside the request handler ran it on the event loop - which stopped the chat
        # relay, dropped Discord's heartbeat and made the web UI itself unanswerable for
        # the whole archive. The one thing a backup must not do is take the manager down.
        job.update(state="running", phase="starting", done=0, total=0,
                   message="", ok=None, started=time.time())
        asyncio.create_task(_backup_task())
        raise web.HTTPFound("/admin/data#backups")

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
        """The old tab's address. The editor is on Settings now."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        raise web.HTTPFound("/admin#mods")

    async def mods_key(request):
        """Save or clear the CurseForge key, from the page where it is wanted.

        The canonical setting is still the schema's - this writes to it - so there is
        one definition and one place it is stored, reachable from two doors.
        """
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        if form.get("clearkey"):
            store.patch({"curseforge_api_key": ""})
            store.save()
            _reguard()
            announce.say("mods.key_cleared",
                         "The CurseForge API key was removed. Searching is off; adding "
                         "a mod by Project ID still works.")
            raise web.HTTPFound("/admin#mods")
        value = str(form.get("apikey") or "").strip()
        if value:
            try:
                store.patch({"curseforge_api_key": value})
                store.save()
                _reguard()
                # The value is never in the message, the log line or the fields - only
                # that there now is one.
                announce.say("mods.key_set",
                             "A CurseForge API key was saved. Searching and browsing "
                             "from the Mods page are now available.")
            except Invalid as e:
                log.info("CurseForge key rejected: %s", e)
        raise web.HTTPFound("/admin#mods")

    async def mods_find(request):
        """Look a mod up before it can be added. Never writes anything."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        # Off the loop: this is two DNS lookups and an HTTPS round trip to a service
        # that is somebody else's, and the chat relay lives on this thread.
        card, problem = await asyncio.to_thread(
            lambda: cfctl.lookup(form.get("ref"), store=store))
        _found.update(card=card, problem=problem)
        raise web.HTTPFound("/admin#mods")

    async def mods_edit(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        listed = str(store.get("mod_ids") or "")
        # The list has rules - a mod cannot be added twice, and one that is not on the
        # list cannot be moved or removed - and they were raising straight out of this
        # handler. Double-clicking Add returned a stack trace, on the page every other
        # setting lives on, where every other refusal in this manager is an amber line
        # saying nothing happened.
        try:
            if form.get("addmod"):
                listed = modsctl.add(listed, str(form.get("addmod")).strip())
                _found["problem"] = ""
            elif form.get("drop"):
                listed = modsctl.remove(listed, str(form.get("drop")))
            elif form.get("up"):
                listed = modsctl.move(listed, str(form.get("up")), -1)
            elif form.get("down"):
                listed = modsctl.move(listed, str(form.get("down")), 1)
        except ValueError as e:
            # Nothing was written: `listed` is still what the store holds, and the
            # refusal says which rule and what the list still is.
            raise web.HTTPFound(
                "/admin?said=%s#mods"
                % _say_next(where="mods",
                            refusal="%s - the mod list is unchanged."
                                    % str(e).strip().rstrip(".")))
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
        raise web.HTTPFound("/admin#mods")


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

    def _save_one(key):
        """Ask this one map to write its world out. Best effort, by design.

        A map that shuts down cleanly does not leave a hot journal beside its world,
        which is what refused the Genesis restore. So it is worth asking - but the
        world has already been copied aside as a file, so a map that cannot answer
        must not be a map that cannot be restored. That is precisely when somebody
        wants to.

        Shared by both restore paths. It sat inside the save-point handler, which is
        how the archive restore - the one that replaces the whole world directory -
        ended up being the path that never asked the map to save.
        """
        from . import bot
        password = str(store.get("admin_password") or "")
        for label, host, port in clusterctl.running_instances(store):
            if label not in (key, (mapsmod.entry(store, key) or {}).get("name")):
                continue
            try:
                clusterctl.run_coroutine(
                    bot.rcon_with(host, port, password, "SaveWorld", timeout=30))
                return True, "saved"
            except Exception as e:                   # noqa: BLE001 - reported, not fatal
                return False, str(e).strip() or e.__class__.__name__
        return False, "it is not running"

    def _restore_body(message="", problem="", refusal=""):
        return ui.render_restore(store, backupctl.listing(store),
                                 chosen=_looked["archive"], info=_looked["info"],
                                 notes=_looked["notes"], message=message, problem=problem,
                                 refusal=refusal, job=rjob,
                                 savepoints_by_map=None)

    async def restore_page(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        raise web.HTTPFound("/admin/data#restore")

    async def restore_inspect(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        path = _archive_path(form.get("archive"))
        if not path:
            return _data_next("restore", problem="No such archive.")
        # Off the loop regardless: an archive written before the manifest carried a map
        # list still has to be decompressed to describe it, and the relay, Discord and
        # this page all live on the thread that would be doing it.
        info = await asyncio.to_thread(restorectl.inspect, path)
        _looked.update(archive=os.path.basename(path), info=info if info["ok"] else None,
                       notes=restorectl.compare(store, info) if info["ok"] else [])
        return _data_next("restore",
                          problem="" if info["ok"] else info["problem"])

    async def restore_run(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        posted = os.path.basename(str(form.get("archive") or ""))
        path = _archive_path(posted)
        map_key = str(form.get("map") or "")
        confirm = str(form.get("confirm") or "")
        force = bool(form.get("force"))
        if not path:
            return _data_next("restore", problem="No such archive.")

        # The archive that was looked inside is the only archive this will restore.
        # The page has two forms - one to inspect, one to run - and the run form used
        # to carry its own hidden copy of the name. Change the dropdown without
        # pressing "Look inside" and the page showed one archive while the button
        # restored another, over a live world, with no confirmation anywhere. The
        # check is here rather than only in the browser because that is what makes it
        # true for a second tab, and what can be tested without one.
        looked = _looked["archive"]
        if not looked or not _looked["info"]:
            announce.say("restore.refused",
                         "Restore refused: no archive has been looked inside yet, so "
                         "there is nothing proven to restore from. Nothing has been "
                         "changed.", level="warning", map=map_key)
            return _data_next(
                "restore",
                refusal="Look inside an archive first - nothing is restored from an "
                        "archive that has not been opened and checked.")
        if posted != looked:
            announce.say("restore.refused",
                         "Restore refused: the page asked to restore %s but %s is the "
                         "archive that was looked inside. Nothing has been changed."
                         % (posted, looked), level="warning", map=map_key)
            return _data_next(
                "restore",
                refusal="The archive shown is not the one that was looked inside: you "
                        "asked for %s, and %s is what was opened and checked. Look "
                        "inside %s again before restoring from it."
                        % (posted, looked, posted))

        # Typed, not clicked, and it is the map's own name rather than a fixed word:
        # this replaces one map's entire world, and the mistake worth preventing is
        # doing it to the wrong map as much as doing it at all. restore_map refuses
        # again on its own - this one is here so the answer is instant and nothing
        # announces a restore that is about to be refused.
        if not restorectl.confirms(store, map_key, confirm):
            want = ((mapsmod.entry(store, map_key) or {}).get("name") or map_key)
            announce.say("restore.refused",
                         "Restore of %s refused: the confirmation did not match. "
                         "Nothing has been changed." % (want or "that map"),
                         level="warning", map=map_key, archive=posted)
            return _data_next(
                "restore",
                refusal="Type %s to confirm. This replaces that map's whole world with "
                        "the one in the archive, and there is no undo - so the name is "
                        "typed rather than clicked." % (want or "the map's name"))

        if cluster_busy.locked():
            return _data_next(
                "restore",
                problem="Something else is already working on the cluster - this was "
                        "ignored rather than run alongside it.")

        if rjob["state"] == "running":
            raise web.HTTPFound("/admin/data#restore")

        def note(text):
            rjob["step"] = text
            announce.say("restore.phase", text, map=map_key)

        def verify_after(key):
            """Wait for the map to come back, then put it through the six gates.

            Starting a container and asking whether it is serving are minutes apart, so
            checking immediately would measure the wrong thing and call every restore a
            success. This is the step that makes the promise real.
            """
            return verify_restored(store, key, note)

        def go():
            return restorectl.restore_map(
                store, path, map_key,
                stop=lambda k: (note("stopping %s" % k) or clusterctl.stop_one(store, k)),
                start=lambda k: (note("starting %s" % k) or clusterctl.start_one(store, k)),
                verify=verify_after, on_step=note,
                # The same three guards the save-point rollback has had all along, on
                # the path that replaces the whole world directory rather than one file.
                confirm=confirm, force=force,
                players=lambda: clusterctl.players_online(store), save=_save_one)

        async def run_it():
            try:
                async with cluster_busy:
                    ok, msg, detail = await asyncio.to_thread(go)
            except Exception as e:                       # noqa: BLE001 - surfaced below
                ok, msg, detail = False, "Restore failed: %s" % e, {}
                log.exception("restore failed")
            # A guard saying no is not a restore that broke. Announcing them the
            # same way trains somebody to read past the one that matters - the same
            # collapse the off-site copy had between "not set up" and "it failed".
            refused = bool((detail or {}).get("refused"))
            announce.say("restore.done" if ok else
                         ("restore.refused" if refused else "restore.failed"), str(msg),
                         level="info" if ok else ("warning" if refused else "error"),
                         map=map_key, archive=os.path.basename(path))
            rjob.update(state="done", ok=ok, message=msg, step="done",
                        detail=detail)

        announce.say("restore.start",
                     "Restoring %s from %s - only that map stops; its current world is "
                     "copied first and the one it replaces is kept."
                     % ((mapsmod.entry(store, map_key) or {}).get("name")
                        or map_key,
                        os.path.basename(path)),
                     map=map_key, archive=os.path.basename(path))
        rjob.update(state="running", ok=None, message="", step="starting",
                    map=map_key, archive=os.path.basename(path),
                    started=time.time(), detail={})
        asyncio.create_task(run_it())
        raise web.HTTPFound("/admin/data#restore")

    async def restore_point(request):
        """Roll one map back to one of the game's own dated saves."""
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        map_key, _, name = str(form.get("point") or "").partition("|")
        force = bool(form.get("force"))
        if not map_key or not name:
            raise web.HTTPFound("/admin/data#restore")
        if rjob["state"] == "running" or cluster_busy.locked():
            raise web.HTTPFound("/admin/data#restore")

        def note(text):
            rjob["step"] = text
            announce.say("restore.phase", text, map=map_key)

        def verify_after(key):
            return verify_restored(store, key, note)

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
            # The same three-way the archive restore makes: worked, refused, broke.
            # This path announced its player refusals as restore.failed at error, so a
            # guard saying "somebody is playing on it" arrived with a red cross beside
            # an identical refusal from the archive path rendered amber.
            refused = bool((detail or {}).get("refused"))
            announce.say("restore.done" if ok else
                         ("restore.refused" if refused else "restore.failed"), str(msg),
                         level="info" if ok else ("warning" if refused else "error"),
                         map=map_key, point=name,
                         detail="\n".join(detail.get("steps") or []))
            rjob.update(state="done", ok=ok, message=msg, step="done", detail=detail)

        announce.say("restore.point_start",
                     "Rolling %s back to its save %s. Only that map stops; its current "
                     "world is copied first." % (map_key, name),
                     map=map_key, point=name)
        rjob.update(state="running", ok=None, message="", step="starting",
                    map=map_key, archive=name, started=time.time(), detail={})
        asyncio.create_task(run_it())
        raise web.HTTPFound("/admin/data#restore")

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
        raise web.HTTPFound("/admin/data#cloud")

    def _cloud_chrome(msg="", problem="", warning=""):
        """Park what happened and go to the section it happened in."""
        return _data_next("cloud", message=msg, problem=problem, refusal=warning)

    async def cloud_connect(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        f = await request.post()
        extra = {k: f.get(k, "") for k in ("access_key_id", "secret_access_key")}
        # Asked before anything is attempted, so "you have not typed a passphrase"
        # arrives in the amber that means nothing happened rather than the red that
        # means something broke. Both used to be red, side by side with a restore
        # refusal in amber saying the same kind of thing.
        refusal = cloudctl.connect_refusal(f.get("provider", ""),
                                           f.get("password", ""), f.get("token", ""))
        if refusal:
            raise _data_next("cloud", refusal=refusal)
        ok, msg = cloudctl.connect(store,
                                   provider=f.get("provider", ""),
                                   password=f.get("password", ""),
                                   path=f.get("path", "obelisk-backups"),
                                   token=f.get("token", ""),
                                   extra=extra)
        if ok:
            raise _data_next("cloud", message=msg)
        raise _data_next("cloud", problem=msg)

    async def cloud_disconnect(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        form = await request.post()
        typed = str(form.get("confirm") or "").strip()
        # Case and surrounding space are forgiven; the word is not. Somebody who has
        # read what this does can type it, and nobody reaches it by clicking.
        confirmed = typed.upper() == cloudctl.DISCONNECT_WORD
        ok, msg = cloudctl.disconnect(store, confirmed=confirmed)
        # Both outcomes are severe, so both are rendered severe. Success used to go to
        # `message`, which is the grey note "Connected to Google Drive" uses - the
        # single most consequential and least reversible screen in the product, styled
        # as routine chatter. Refusing is a warning; succeeding is worse.
        return _cloud_chrome("", msg)

    async def cloud_push(request):
        if not authed(request):
            raise web.HTTPFound("/setup")
        rows = backupctl.listing(store)
        if not rows:
            return _cloud_chrome("", "There is no local backup to upload yet.")
        # The sentence used to go into the message slot whatever it said, so "the
        # upload failed" arrived in the same grey box, in the same voice, as "the
        # upload worked" - on the page whose entire job is telling you whether there
        # is a copy of your cluster somewhere other than this machine.
        ok_up, configured, text = backupctl.push_offsite(store, rows[0]["path"])
        if ok_up:
            announce.say("cloud.push_done", text)
            return _cloud_chrome(text, "")
        # A cloud nobody has connected yet is not an outage. Red here would be the
        # same lie as grey was, pointed the other way: it says something broke when
        # what actually happened is that a step was never taken.
        if not configured:
            announce.say("cloud.push_unconfigured", text, level="warning")
            return _cloud_chrome("", "", warning=text)
        announce.say("cloud.push_failed", text, level="error")
        return _cloud_chrome("", text)

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
        """The front door is the Cluster page.

        Status and Cluster had grown into two renderings of one thing: both called
        render_status, with the same services and the same poll, so the running-maps
        table and the player count were drawn twice on two tabs. Everything Status had
        of its own now lives on Cluster, and this address goes there rather than
        rendering a third copy of any of it.
        """
        if not authed(request):
            raise web.HTTPFound("/setup")
        raise web.HTTPFound("/admin/cluster")

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
    app.router.add_post("/admin/player/message", player_message)
    app.router.add_post("/admin/player/kick", player_kick)
    app.router.add_post("/admin/player/ban", player_ban)
    app.router.add_post("/admin/player/unban", player_unban)
    app.router.add_post("/admin/player/cap", player_cap)
    app.router.add_get("/admin/cluster/status", cluster_status)
    app.router.add_get("/admin/cluster/map/{key}", map_page)
    app.router.add_get("/admin/data", data_page)
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
    app.router.add_post("/admin/mods/key", mods_key)
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
                ok, msg, offsite = backupctl.run_scheduled(store, flush=flush)
                (log.info if ok else log.error)("scheduled backup: %s", msg)
                # The nightly backup is the thing this product exists to keep, and it
                # said nothing to the channel either way - so a schedule that had been
                # failing for a week looked exactly like one that had been working.
                # The local result first, because it is the one that is usually good.
                announce.say("backup.done" if ok else "backup.failed",
                             "Scheduled backup: %s" % msg,
                             level="info" if ok else "error")
                if offsite is not None:
                    ok_up, configured, text = offsite
                    # Separately, and in its own colour. A failed upload does not make
                    # a good local backup a failed one, and folding it into that line
                    # is how a failure ends up announced with a tick beside it.
                    #
                    # Three states rather than two. "Off-site is on but no cloud is
                    # connected" is a setup step nobody has finished; "the upload did
                    # not happen" is the night your cluster exists in one place only.
                    # Announced identically, the first teaches the operator to ignore
                    # the second.
                    if ok_up:
                        announce.say("backup.offsite_done", text)
                    elif not configured:
                        announce.say("backup.offsite_unconfigured", text,
                                     level="warning")
                    else:
                        announce.say("backup.offsite_failed", text, level="error")
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


async def empty_watch(store, interval=60, needed=3):
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
            # Is there anything to apply at all? Asked first because it is free and
            # because the answer was wrong: a primed record for the build already
            # running is not an update, and reading it as one restarted ten servers to
            # install what they were already on - which emptied the cluster, which
            # started the whole thing again.
            worth, why_worth = upd.worth_applying(
                store, installed=arkupdate.installed_build(
                    layout.ark_paths(layout.ark_root_of(store))["serverfiles"])[0])
            if not worth:
                streak = 0
                continue

            if APPLY_LOCK.locked():
                # Something is already stopping and starting the cluster. Not a moment
                # to start a second one, and not a moment to count as "idle" either.
                streak = 0
                continue

            # And is the cluster actually up? Zero players across a cluster that has
            # not finished starting is not an empty cluster, it is an unfinished one.
            ready, why_ready, total = await asyncio.to_thread(
                clusterctl.cluster_ready, store)
            if not ready:
                if streak:
                    log.info("empty streak reset: %s", why_ready)
                streak = 0
                continue
            streak = streak + 1 if total == 0 else 0

            go, why = upd.empty_enough(store, streak, needed=needed)
            if not go:
                continue

            log.info("nobody is on and something is waiting: %s", why)
            announce.say("change.window_open",
                         "The cluster has been empty for %d minute(s) and %s, so it is "
                         "being applied now."
                         % (streak * interval // 60, why_worth))
            streak = 0
            async with lock:
                await asyncio.to_thread(_scheduled_apply, store)
        except Exception as e:                          # a bad minute must not kill it
            log.error("empty-cluster check failed: %s", e)
            streak = 0


async def world_watch(store, interval=6 * 3600, check=None, sleep_first=True):
    """Every few hours, is the newest save point of each map still readable?

    **It looks at save points, never at the live world.** The live file is rewritten at
    every autosave - all ten within the same second, as it happens - and verify_world
    opens with immutable=1, which is a promise that the file is static. Point that at a
    world mid-write and SQLite can read a torn page and report damage that is not there.
    A false "your world is corrupt" at three in the morning is worse than no sweep at
    all, so the sweep asks about the files the game has finished with.

    That is also the more useful question. A save point is what a restore actually comes
    from, so "is the newest one good" is the thing somebody needs to know *before* they
    need it - which is the whole complaint about the backups this product exists to keep.

    quick_check rather than the full walk: ten worlds of up to 135 MB on spinning disks,
    four times a day. The damage this hunts is structural and quick_check sees it; the
    apply gate pays for the deep check because that answer decides a promotion.

    **It never acts.** No stop, no start, no move, no delete, no restore - it says what
    it found and that is the end of its authority. A background loop that can touch a
    cluster is a background loop that will, at four in the morning, for a reason nobody
    is awake to read.
    """
    from . import savepoints
    seen_bad = set()
    while True:
        if sleep_first:
            await asyncio.sleep(interval)
        sleep_first = True
        try:
            for key in clusterctl._map_keys(store):
                points = (check or savepoints.list_points)(store, key)
                if not points:
                    continue                      # never started, or pruned: not news
                newest = points[0]
                ok, why = restorectl.verify_world(newest["path"], deep=False)
                if ok:
                    if key in seen_bad:
                        seen_bad.discard(key)
                        announce.say("world.readable_again",
                                     "%s's newest save point reads cleanly again (%s)."
                                     % (key, newest["name"]))
                    continue
                if key in seen_bad:
                    continue                      # already said; do not storm the channel
                seen_bad.add(key)
                announce.say(
                    "world.damaged",
                    "%s's newest save point will not read: %s. Nothing has been "
                    "changed - this is a warning, not an action. Check the older save "
                    "points for this map before the next restart needs one."
                    % (key, why), level="error",
                    detail="%s\n%s" % (newest["path"], why))
        except Exception as e:                    # noqa: BLE001 - never fatal
            log.info("world sweep skipped: %s", e)


def verify_restored(store, key, note=None):
    """The six gates on a map whose world has just been replaced. (ok, reasons).

    Both restore paths had a byte-identical copy of this, and both read
    `if not ok_h: return False, [why_h]` - the same short circuit A3 took out of the
    apply gate, in a different feature. A map that did not report healthy was never
    asked whether its world verifies, seconds after that world was swapped underneath
    it. That is the one question a restore exists to answer, and it was skipped in
    exactly the case that most needed it.

    So every question is asked and the reasons are collected. A health timeout is a
    reason like any other, not a reason to stop asking, and a check that raises is a
    failed map rather than a failed restore - the world is already in place by the time
    this runs, so an exception here would leave the operator with no verdict at all.

    `note` is the route's step reporter. One function, two callers, and a test that
    fails if either grows its own copy again.
    """
    say = note or (lambda _text: None)
    say("waiting for %s to come back" % key)
    ok_h, why_h = clusterctl.wait_healthy(store, key)
    reasons = [] if ok_h else ["did not report healthy: %s" % why_h]
    say("checking it is really serving")
    try:
        ok_v, reasons_v = clusterctl.verify_instance(store, key)
    except Exception as e:                            # noqa: BLE001 - a failure is a result
        ok_v, reasons_v = False, ["the check itself failed: %s" % e]
    return (bool(ok_h) and bool(ok_v)), reasons + list(reasons_v or [])


def verify_every_map(store):
    """The six gates on every map, whatever the map before it did. (ok, per_map, why).

    It used to read `bool(ok_h) and verify_instance(...)`, which is a short circuit:
    a map that did not report healthy was never asked whether its world was intact,
    whether its mods loaded, or whether RCON answered. The one map most likely to be
    damaged after a build swap was the one map that got no integrity check at all, and
    the batch reported it as a bare FAILED with nothing to act on.

    So every map is asked every question, and the reasons are kept. "island FAILED" and
    "island FAILED: the world on disk does not verify" are the difference between
    knowing something is wrong and knowing what to do about it - and with ten maps, the
    second and third reasons matter as much as the first, because they are what says
    whether this is one broken map or a broken swap.

    A health timeout is a reason like any other, not a reason to stop asking.
    """
    results, why = {}, {}
    for key in clusterctl._map_keys(store):
        # The same question the restore paths ask, asked once per map. It was written
        # out here as well until this commit - three copies of "wait, then gate, and
        # keep the reasons" in one file, which is how two of them kept a short circuit
        # the third had already had removed.
        results[key], why[key] = verify_restored(store, key)
    return all(results.values()), results, why


async def loop_watch(store, interval=120, status=None, sleep_first=True):
    """A map that keeps restarting, said once, where somebody will see it.

    The restart loop is already detected - progress.looks_like_a_loop runs inside every
    status() call and the Cluster page colours the map red for it. But status() only
    runs when a page is being rendered, so a map that went into a loop at three in the
    morning with nobody looking left no record anywhere: not in the channel, not in the
    log, not in the event feed. The detection was fine; nothing was listening to it.

    Said once per loop, not once per poll. A container that is failing every forty
    seconds is failing for hours, and a line every two minutes for hours is how a
    channel gets muted - which costs the next alert too. The recovery is announced the
    same way, so the channel is not left on red after the map came back.

    A map that disappears between passes is not a recovery. Stopping the cluster removes
    every container, and reporting ten maps as settled because the operator pressed Stop
    would be a lie told at exactly the wrong moment - so a name that is simply gone is
    forgotten silently, and only a map that is still there and no longer looping counts.

    **It never acts.** Like the world sweep, it reports and that is the end of its
    authority.
    """
    looping = set()
    while True:
        if sleep_first:
            await asyncio.sleep(interval)
        sleep_first = True
        try:
            st = (status or clusterctl.status)(store)
            services = st.get("services") or []
            present = {s.get("name") for s in services if s.get("name")}
            now = {s.get("name") for s in services
                   if s.get("name") and s.get("looping")}
            for name in sorted(now - looping):
                svc = next(s for s in services if s.get("name") == name)
                why = svc.get("failure") or "See the map's log on the Cluster page."
                announce.say(
                    "cluster.map_looping",
                    "%s keeps restarting instead of staying up: %s Nothing has been "
                    "changed - this is a report, not an action." % (name, why),
                    level="error", detail=svc.get("log_tail") or "")
            for name in sorted((looping - now) & present):
                announce.say("cluster.map_settled",
                             "%s has stopped restarting and is staying up." % name)
            looping = now
        except Exception as e:                    # noqa: BLE001 - never fatal
            log.info("restart-loop watch skipped: %s", e)


async def relay_watch(store, bot, interval=120):
    """Keep the relay pointed at every player map, as they come and go.

    It wired once, at boot, from whatever happened to be running at that instant - and a
    staggered start is exactly the instant it should not have trusted. On 8 September
    the cluster was relaunched, nine maps were still waiting behind the island's health
    check, and the relay came up covering one map and stayed there. Chat was broken for
    the rest of the cluster until somebody restarted the manager by hand.

    So coverage is re-checked rather than assumed. It already measures reachability -
    that is where relay.up and relay.degraded come from - and this drives the re-wire off
    the same measurement, on a loop. A cluster that comes up in pieces heals to full
    coverage on its own.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            before = set(bot.SERVERS or {})
            if not _wire_relay(store, bot):
                continue
            after = set(bot.SERVERS or {})
            if after == before:
                continue
            log.info("relay re-wired: now covering %d map(s) (was %d)",
                     len(after), len(before))
            good, bad = await asyncio.to_thread(clusterctl.reachable, store)
            _say_coverage(len(good), len(after), bad)
        except Exception as e:                          # noqa: BLE001 - never fatal
            log.info("relay coverage check skipped: %s", e)


def _say_coverage(reachable, total, bad):
    """One place that describes coverage, so the count and the wording agree.

    It said "reaching all 1 maps" - true, and read like a bug because it was one.
    """
    RELAY_INFO.update(total=total, reachable=reachable,
                      unreachable=", ".join(n for n, _w in bad))
    maps = "map" if total == 1 else "maps"
    if bad:
        announce.say("relay.degraded",
                     "Chat relay can reach %d of %d %s. Cross-map chat will not work "
                     "for the rest until this is fixed." % (reachable, total, maps),
                     level="error", unreachable=", ".join(n for n, _w in bad))
    else:
        announce.say("relay.up",
                     "Chat relay is up and reaching %s."
                     % ("the only map" if total == 1 else "all %d maps" % total))


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
            if not due and "nothing to add" in why:
                # Worth one line so the absence of a 4 a.m. restart is a thing somebody
                # can see rather than something they have to infer.
                log.info("update window skipped: %s", why)
            if due:
                if APPLY_LOCK.locked():
                    log.info("the window is open but an apply is already running - "
                             "leaving it to finish")
                else:
                    log.info("the update window is the backstop, and it is needed: %s",
                             why)
                    # Say what is actually being applied. This announced "a verified
                    # update is staged" whatever the batch held, so a night that applied
                    # one setting change read like a build rollout in the channel.
                    announce.say("ark.window_open",
                                 "The update window is open and there are changes "
                                 "waiting: %s. Applying them." % why)
                    async with APPLY_LOCK:
                        # Through the same routine the button uses, so the scheduled
                        # path cannot drift from the one exercised by hand.
                        await asyncio.to_thread(_scheduled_apply, store)
        except Exception as e:                          # a bad night must not kill it
            log.error("ARK update check failed: %s", e)
        await asyncio.sleep(interval)


def _scheduled_apply(store, force=False):
    """The unattended apply. Same verbs as the button, assembled in one place.

    The player count is taken again here, always. Whatever decided to call this did so
    from a count up to a minute old, and a minute is long enough for somebody to log in.
    It used to be a parameter defaulting to on, described in this docstring as "not
    optional in practice" - which is a switch for turning off the check that stops an
    update kicking the person who just arrived.
    """
    from . import bot, pending as pnd, staging as stg, updates as upd

    if not force:
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
        return verify_every_map(store)

    def start_some(keys):
        up = []
        for key in keys:
            ok_s, why_s = clusterctl.start_one(store, key)
            if ok_s:
                up.append(key)
            else:
                log.warning("could not start %s after the gate refused: %s", key, why_s)
        return up

    return upd.apply_batch(
        store, layout.ark_root_of(store), warn=warn, force=force,
        # The unattended path gets the same gate as the button. An apply nobody is
        # watching is the one that most needs to refuse rather than promote.
        check_worlds=lambda: clusterctl.worlds_intact(
            store, layout.ark_root_of(store)),
        start_some=start_some,
        # Proved on disk, not merely accepted - a stop is what follows this.
        save=lambda: clusterctl.save_and_settle(store, layout.ark_root_of(store)),
        stop_all=stop_all,
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
    tasks.append(asyncio.create_task(world_watch(store)))
    tasks.append(asyncio.create_task(loop_watch(store)))

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
            log.error("relay reaches %d of %d map(s) - cannot reach: %s",
                      len(good), total,
                      ", ".join("%s (%s)" % (n, why) for n, why in bad[:4]))
        else:
            log.info("relay covering %d map(s), all reachable: %s",
                     total, ", ".join(bot.SERVERS))
        _say_coverage(len(good), total, bad)
        log.info("relaying chat between: %s", ", ".join(bot.SERVERS))
        # And keep checking. A cluster that comes up in pieces used to leave the relay
        # pointed at whatever was running when the manager booted.
        tasks.append(asyncio.create_task(relay_watch(store, bot)))
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
            value = cast(store.get(key))
        except (TypeError, ValueError):
            log.warning("ignoring unusable value for %s", key)
            continue
        # None is a cast saying "there was something here and I could not read it".
        # Leaving the relay on what it already had beats replacing a schedule somebody
        # meant to set with silence - and beats handing the scheduler a value that
        # will raise on it once every twenty seconds.
        if value is None:
            log.warning("ignoring unusable value for %s - keeping the last good one",
                        key)
            continue
        setattr(bot, attr, value)

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


def _times(v):
    """"03:15, 21:45" as the relay's list of clock strings, junk dropped.

    Entries the scheduler cannot read are left out rather than passed through. They
    used to be passed through: the settings page rejects "ab:cd", but settings.json is
    a file a person edits, and a value that arrives that way is read back unvalidated.
    It then reached a scheduling loop that called int() on it every twenty seconds.

    A value that is blank is a cluster that does not wipe, and returns an empty list.
    A value that is not blank but has nothing usable in it returns None, which means
    "I could not read this" - see _wire_relay. Silently turning a mistyped schedule
    into no schedule at all is the failure that looks most like success.
    """
    raw = [x.strip() for x in str(v or "").split(",") if x.strip()]
    good = [t for t in raw if _clock_minutes(t) is not None]
    if raw and not good:
        return None
    return good


def _clock_minutes(text):
    """The relay's own clock parser, so the two cannot disagree about what is valid."""
    from . import bot as _bot
    return _bot._hhmm_to_min(text)


def _minutes(v):
    """"1,5,10" as the relay's minutes, largest first - the order it warns in.

    Sorted here as well as in the validator, because the validator only sees values
    that came through the settings page and this also reads a store edited by hand.
    Junk is dropped for the same reason, and a value that is entirely junk is None
    rather than an empty list: no warnings at all is a decision, not a typo.
    """
    raw = [x.strip() for x in str(v or "").split(",") if x.strip()]
    good = set()
    for x in raw:
        try:
            good.add(int(x))
        except (TypeError, ValueError):
            continue
    if raw and not good:
        return None
    return sorted(good, reverse=True)


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
    # The wild-dino schedule was the last thing still crossing by environment variable.
    # generate_env writes WIPE_TIMES into the .env the *maps* read, and POK has no idea
    # what it means; the relay that does is in this process, whose own environment has
    # never had it set. So the setting saved, validated, appeared in the docs, and the
    # wipe never happened - on any Obelisk install, ever.
    ("wipe_times", "WIPE_TIMES", _times),
    ("wipe_warn_minutes", "WIPE_WARN_MINUTES", _minutes),
)


# Last in the file, and it has to stay last: everything main() reaches for must
# already be defined by the time this line runs. Sitting above _wire_relay(), it
# called a name that did not exist yet - so the container exited on NameError the
# moment a cluster was actually running for the relay to pick up, and only then.
if __name__ == "__main__":
    asyncio.run(main())
