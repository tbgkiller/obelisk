"""
The admin UI, rendered from the schema.

There is no per-setting UI code. Every field on the settings page comes from the
schema's label, help text, type and range, so adding a setting adds a form control
with a description and validation for free - and the page can never drift from what
the store will actually accept.

Pure functions returning strings. No server, no store writes, no I/O, so the whole
UI is testable without standing anything up.
"""

import html

from .schema import SETTINGS, GROUPS, INSTALL_KEYS
from . import maps as mapcat
from .presets import PRESETS
from . import mods as modlib

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{background:#12151a;color:#e6e9ef;font:14px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}
.wrap{max-width:940px;margin:0 auto}
h1{font-size:19px;margin:0 0 2px;letter-spacing:.3px}
.sub{color:#8b94a3;font-size:12px;margin-bottom:20px}
nav{display:flex;gap:16px;margin-bottom:22px;border-bottom:1px solid #262d38;padding-bottom:10px}
nav a{color:#8b94a3;text-decoration:none;font-size:13px}
nav a.on{color:#e6e9ef;font-weight:600}
fieldset{background:#1a1f27;border:1px solid #262d38;border-radius:10px;padding:6px 16px 14px;margin:0 0 16px}
legend{color:#8b94a3;font-size:11px;text-transform:uppercase;letter-spacing:.6px;padding:0 6px}
.f{padding:12px 0;border-bottom:1px solid #20262f}
.f:last-child{border-bottom:none}
label{display:block;font-weight:600;margin-bottom:3px}
.help{color:#8b94a3;font-size:12px;margin-top:5px;max-width:62ch}
input[type=text],input[type=password],input[type=number],textarea,select{
  background:#12151a;color:#e6e9ef;border:1px solid #303845;border-radius:7px;
  padding:7px 10px;font:inherit;width:100%;max-width:460px}
textarea{min-height:82px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;max-width:100%}
input[readonly]{background:#171b22;color:#8b94a3;border-style:dashed}
.tag{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.6px;
  background:#262d38;color:#8b94a3;border-radius:4px;padding:2px 6px;margin-left:8px;vertical-align:2px}
button{background:#2f6feb;color:#fff;border:0;border-radius:7px;padding:9px 16px;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:#262d38;color:#e6e9ef}
button:disabled{opacity:.45;cursor:not-allowed}
table{width:100%;border-collapse:collapse;background:#1a1f27;border:1px solid #262d38;border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#8b94a3;padding:9px 13px;border-bottom:1px solid #262d38}
td{padding:9px 13px;border-bottom:1px solid #20262f}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.maps{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;margin:4px 0 8px}
.maps label{display:flex;gap:9px;align-items:center;background:#12151a;border:1px solid #303845;
  border-radius:8px;padding:9px 11px;font-weight:500;cursor:pointer;margin:0}
.presets{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.note,.problem{border-radius:8px;padding:10px 13px;margin:10px 0;font-size:13px}
.note{background:#1d2530;color:#a9b4c4;border:1px solid #2b3542}
.callout{background:#17324a;border:1px solid #2f6feb;border-radius:10px;padding:14px 16px;margin:0 0 18px}
.callout strong{color:#e6e9ef;font-size:15px;display:block;margin-bottom:6px}
.callout .steps{color:#b8c6d9;font-size:13px;line-height:1.6}
.callout code{background:#0f1620;padding:1px 5px;border-radius:4px}
.problem{background:#2a1d1f;color:#ffb4ab;border:1px solid #4a2b2e}
.ok{color:#3fb950}
.foot{color:#5b6472;font-size:11px;margin-top:22px}
"""


def _e(v):
    return html.escape("" if v is None else str(v), quote=True)


def page(title, body, nav_on=""):
    tabs = [("/", "Status"), ("/admin", "Settings"), ("/admin/cluster", "Cluster"),
            ("/admin/mods", "Mods"), ("/admin/backups", "Backups"),
            ("/admin/cloud", "Cloud")]
    nav = "".join('<a href="%s"%s>%s</a>' % (h, ' class=on' if h == nav_on else "", _e(t))
                  for h, t in tabs)
    return ("<!doctype html><html><head><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><div class=wrap>"
            "<h1>%s</h1><nav>%s</nav>%s"
            "<div class=foot>Obelisk</div></div></body></html>"
            % (_e(title), CSS, _e(title), nav, body))


def _field(s, value, locked):
    t, key = s["type"], s["key"]
    ro = " readonly" if locked else ""
    if t == "bool":
        ctrl = ('<select name="%s"%s><option value="true"%s>Yes</option>'
                '<option value="false"%s>No</option></select>'
                % (key, " disabled" if locked else "",
                   " selected" if value in (True, "true", "TRUE") else "",
                   "" if value in (True, "true", "TRUE") else " selected"))
    elif t == "choice":
        ctrl = '<select name="%s"%s>%s</select>' % (
            key, " disabled" if locked else "",
            "".join('<option%s>%s</option>' % (" selected" if str(value) == c else "", _e(c))
                    for c in s["choices"]))
    elif t == "longtext":
        ctrl = '<textarea name="%s"%s>%s</textarea>' % (key, ro, _e(value))
    elif t == "password":
        # Never render a stored secret back into the page. Blank means "leave alone".
        ph = "unchanged - type to replace" if str(value).strip() else "not set"
        ctrl = ('<input type=password name="%s" value="" placeholder="%s"%s>'
                % (key, _e(ph), ro))
    elif t in ("int", "port"):
        rng = ""
        if "min" in s: rng += ' min="%s"' % s["min"]
        if "max" in s: rng += ' max="%s"' % s["max"]
        ctrl = '<input type=number name="%s" value="%s"%s%s>' % (key, _e(value), rng, ro)
    elif t == "float":
        ctrl = '<input type=number step=any name="%s" value="%s"%s>' % (key, _e(value), ro)
    else:
        ctrl = '<input type=text name="%s" value="%s"%s>' % (key, _e(value), ro)

    tags = ""
    if locked:
        tags += '<span class=tag title="set when the container is created">container</span>'
    if s.get("apply") == "recreate":
        tags += '<span class=tag>needs recreate</span>'
    help_txt = _e(s.get("help", ""))
    if locked:
        help_txt += (" <strong>Set when the container was created</strong> - change it in "
                     "the container's template and recreate the container.")
    return ('<div class=f><label for="%s">%s%s</label>%s<div class=help>%s</div></div>'
            % (key, _e(s["label"]), tags, ctrl, help_txt))


def render_settings(store):
    blocks = []
    for g in GROUPS:
        rows = [s for s in SETTINGS if s["group"] == g]
        if not rows:
            continue
        fields = "".join(_field(s, store.get(s["key"]), s["key"] in INSTALL_KEYS)
                         for s in rows)
        blocks.append("<fieldset><legend>%s</legend>%s</fieldset>" % (_e(g), fields))
    todo = store.readiness()
    banner = ""
    if todo:
        banner = ('<div class=problem>Before this cluster can start: %s</div>'
                  % _e(", ".join(b["label"] for b in todo)))
    return ('<form method=post action="/admin/save">%s%s'
            '<button type=submit>Save changes</button></form>'
            % (banner, "".join(blocks)))


def render_status(status):
    """What is actually running. Absent or empty is a normal state, not an error."""
    if not status:
        return ""
    if not status.get("docker_ok"):
        return ('<div class=problem><strong>Docker not connected.</strong> %s</div>'
                % _e(status.get("docker_detail", "")))
    if not status.get("compose_exists"):
        return ('<div class=note>No cluster has been launched from this Obelisk yet. '
                'Pick your maps below and launch.</div>')
    rows = "".join(
        "<tr><td>%s</td><td class=%s>%s</td><td>%s</td></tr>"
        % (_e(s["service"]),
           "ok" if s["state"] == "running" else "bad",
           _e(s["state"] or "?"), _e(s["status"]))
        for s in status.get("services", []))
    if not rows:
        return '<div class=note>The cluster is defined but nothing is running.</div>'
    return ('<fieldset><legend>Running now</legend><table>'
            '<tr><th>Service</th><th>State</th><th>Detail</th></tr>%s</table></fieldset>'
            % rows)


def render_cluster(store, plan, status=None):
    selected = set(str(store.get("maps")).split(","))
    presets = "".join(
        '<button class=ghost type=button name=preset value="%s" title="%s">%s</button>'
        % (_e(p["key"]), _e(p["description"]), _e(p["name"])) for p in PRESETS)

    boxes = "".join(
        '<label><input type=checkbox name=maps value="%s"%s>%s</label>'
        % (_e(m["key"]), " checked" if m["key"] in selected else "", _e(m["name"]))
        for m in mapcat.MAPS)

    rows = "".join(
        "<tr><td>%s</td><td class=num>%s</td><td class=num>%s</td>"
        "<td class=num>%s</td><td>%s</td><td>%s</td></tr>"
        % (_e(r["name"]), r["game_port"], r["rcon_port"], _e(r["memory"]),
           _e(r["memory_why"]), _e(r["role"]))
        for r in plan["maps"])

    msgs = "".join('<div class=problem>%s</div>' % _e(p) for p in plan["problems"])
    msgs += "".join('<div class=note>%s</div>' % _e(n) for n in plan["notes"])

    up = (status or {}).get("running", 0)
    if up:
        launch = ('<button type=submit formaction="/admin/launch"%s>Apply and restart</button> '
                  '<button class=ghost type=submit formaction="/admin/stop">Stop cluster</button>'
                  % ("" if plan["ok"] else " disabled"))
    else:
        launch = ('<button type=submit formaction="/admin/launch"%s>Launch cluster</button>'
                  % ("" if plan["ok"] else " disabled"))
    summary = ("%d map%s, %s of RAM at most, plus Obelisk on port %s."
               % (len(plan["maps"]), "" if len(plan["maps"]) == 1 else "s",
                  plan["total_memory"], plan["obelisk_port"]))

    return (render_status(status) +
            '<form method=post action="/admin/maps">'
            '<fieldset><legend>Presets</legend><div class=presets>%s</div>'
            '<div class=help>A preset just ticks boxes - it carries no settings of its '
            'own. Trim it afterwards.</div></fieldset>'
            '<fieldset><legend>Maps</legend><div class=maps>%s</div>'
            '<button type=submit class=ghost>Update plan</button></fieldset>'
            '<fieldset><legend>Plan</legend>'
            '<table><tr><th>Map</th><th class=num>Game</th><th class=num>RCON</th>'
            '<th class=num>RAM</th><th>Why</th><th>Role</th></tr>%s</table>'
            '<div class=help style="margin-top:10px">%s</div>%s'
            '<div style="margin-top:14px">%s</div></fieldset></form>'
            % (presets, boxes, rows, _e(summary), msgs, launch))


def render_setup(setup_needed=True, error=""):
    """The first screen anyone sees, so the one thing they need comes first.

    Where to find the code was previously a sentence under the input, phrased as a
    command line. Someone installing from the Unraid template has a Log menu, not a
    terminal, and the instruction is the whole content of this page - so it goes at the
    top, in a box, in their words.
    """
    err = '<div class=problem>%s</div>' % _e(error) if error else ""
    return ('%s'
            '<div class=callout><strong>First time? Grab your one-time setup code from '
            'the container log.</strong>'
            '<div class=steps>In Unraid: <b>Docker</b> tab &rarr; left-click the '
            '<b>Obelisk</b> icon &rarr; <b>Logs</b>. The code is near the top, beside '
            '<code>Setup code:</code>, with the address of this page next to it. '
            'Copy it and paste it below.</div></div>'
            '<fieldset><legend>Set up Obelisk</legend>'
            '<div class=f><label for=code>Setup code</label>'
            '<form method=post action="/setup"><input id=code type=password name=code '
            'autocomplete=off autofocus placeholder="paste the code from the log"> '
            '<button type=submit>Continue</button></form>'
            '<div class=help>The code is generated once, when Obelisk first starts, and '
            'is never written into a file you have to edit. If the log has scrolled past '
            'it, restarting the container prints it again.</div></div></fieldset>'
            % err)


STATUS_STYLE = {"ok": ("ok", "installed"),
                "stub": ("bad", "BROKEN INSTALL"),
                "missing": ("bad", "not downloaded"),
                "orphan": ("warn", "leftover")}


def render_mods(store, health=None):
    """The mod list, in load order, with what is actually on disk beside each one.

    Order is the first thing people get wrong and install health is the second, so both
    are on the same screen: a mod can be listed, present, and still not installed.
    """
    ids = modlib.parse(store.get("mod_ids"))
    health = health or {}
    rows = []
    for i, m in enumerate(ids):
        h = health.get(m, {})
        cls, label = STATUS_STYLE.get(h.get("status", ""), ("", "not checked"))
        detail = ("%d files, %s MB" % (h["files"], h["mb"])) if h.get("files") else "-"
        note = ('<div class=help>%s</div>' % _e(h["note"])) if h.get("note") else ""
        first = ' <span class=tag title="loads first, so it wins conflicts">first</span>' if i == 0 else ""
        rows.append(
            "<tr><td class=num>%d</td><td><code>%s</code>%s</td>"
            "<td class=%s>%s%s</td><td class=num>%s</td><td class=num>"
            '<button class=ghost name=up value="%s"%s>&uarr;</button> '
            '<button class=ghost name=down value="%s"%s>&darr;</button> '
            '<button class=ghost name=drop value="%s">Remove</button>'
            "%s</td></tr>"
            % (i + 1, _e(m), first, cls, _e(label), note, _e(detail),
               _e(m), " disabled" if i == 0 else "",
               _e(m), " disabled" if i == len(ids) - 1 else "",
               _e(m),
               ' <button name=refetch value="%s" title="clear it so the server '
               'downloads it again">Re-download</button>' % _e(m)
               if h.get("status") in ("stub", "missing") else ""))

    if not rows:
        rows = ['<tr><td colspan=5 class=help>No mods. The cluster runs vanilla.</td></tr>']

    broken = [m for m, h in health.items() if h.get("status") in ("stub", "missing")]
    banner = ""
    if broken:
        banner = ('<div class=problem>%s looks installed but is not - a download that '
                  'failed part way. It loads nothing and reports no error. Re-download '
                  'it rather than changing the order; order only decides which of two '
                  '<em>loaded</em> mods wins.</div>' % _e(", ".join(sorted(broken))))

    return ('<form method=post action="/admin/mods">%s'
            '<fieldset><legend>Mods, in load order</legend>'
            '<table><tr><th class=num>#</th><th>Mod</th><th>On disk</th>'
            '<th class=num>Size</th><th class=num>Order</th></tr>%s</table>'
            '<div class=help style="margin-top:10px">A mod earlier in the list wins '
            'conflicting changes, which is why stacking mods have to be first. '
            'Reordering takes effect on the next cluster recreate.</div>'
            '</fieldset>'
            '<fieldset><legend>Add a mod</legend><div class=f>'
            '<label>CurseForge mod ID</label>'
            '<input type=text name=addmod placeholder="e.g. 929110" inputmode=numeric> '
            '<button type=submit name=action value=add>Add</button>'
            '<div class=help>The number in the mod\u2019s CurseForge URL. New mods are '
            'added last so they cannot silently outrank something that already works.'
            '</div></div></fieldset></form>' % (banner, "".join(rows)))

def render_backups(store, rows, message="", problem=""):
    """Backups: make one now, see what exists, and what the schedule will do.

    The list is the point. A backup feature you cannot see the results of is a feature
    you have to trust, and trusting an unverified backup is how people discover at
    restore time that it was empty.
    """
    banner = ""
    if problem:
        banner = '<div class=problem>%s</div>' % _e(problem)
    elif message:
        banner = '<div class=note>%s</div>' % _e(message)

    times = str(store.get("backup_times") or "").strip()
    keep = store.get("backup_keep")
    when = ("Scheduled for %s each day, keeping the newest %s." % (_e(times), _e(keep))
            if times else
            "No schedule - backups happen when you press the button. Set a time in "
            "Settings to run them nightly.")

    body = []
    for r in rows:
        body.append("<tr><td><code>%s</code></td><td class=num>%.1f MB</td>"
                    "<td>%s</td></tr>"
                    % (_e(r["name"]), r["bytes"] / 1048576.0, _e(r["when"])))
    if not body:
        body = ['<tr><td colspan=3 class=help>No backups yet.</td></tr>']

    return (banner +
            '<form method=post action="/admin/backup">'
            '<fieldset><legend>Back up now</legend>'
            '<div class=f><button type=submit>Back up now</button>'
            '<div class=help>Copies the whole data root - every map’s saves, the '
            'shared config, the transfer data - plus the cluster definition with your '
            'mod list, so a rebuild knows what to load. The game install is left out '
            'on purpose: it re-downloads for free and carrying it is what stops a '
            'backup being portable. Every archive is opened and checked after writing; '
            'one that cannot be read is discarded rather than kept.</div></div>'
            '<div class=note>%s</div>'
            '<div class=problem>These archives contain your admin/RCON password and '
            'Discord token, because a restore without them is not a restore. They are '
            'written owner-only (0600). Encrypt them before copying anywhere else.</div>'
            '</fieldset>'
            '<fieldset><legend>Backups on disk</legend>'
            '<table><tr><th>Archive</th><th class=num>Size</th><th>When</th></tr>%s</table>'
            '</fieldset></form>' % (when, "".join(body)))

def render_cloud(store, state, remote=None, message="", problem=""):
    """Connect a provider, see what is off-site, restore from it.

    The connect step is written for the one fact that shapes it: signing in to Google is
    the owner's to do, in their own browser. Obelisk asks for the token that comes back,
    never for the account.
    """
    from . import cloud as cloudlib
    banner = ""
    if problem:
        banner = '<div class=problem>%s</div>' % _e(problem)
    elif message:
        banner = '<div class=note>%s</div>' % _e(message)

    if not state.get("rclone_ok"):
        return banner + ('<div class=problem>%s</div>' % _e(state.get("rclone_detail", "")))
    if not state.get("encryption_ok"):
        return banner + ('<div class=problem>This build cannot encrypt, so connecting a '
                         'cloud is disabled.</div>')

    if state.get("connected"):
        reach = state.get("reachable")
        line = ('<span class=ok>reachable</span>' if reach else
                '<span class=bad>not reachable</span> - %s' % _e(state.get("reachable_detail", "")))
        rows = ""
        for r in (remote or []):
            rows += ("<tr><td><code>%s</code></td><td class=num>%.1f MB</td><td>%s</td></tr>"
                     % (_e(r["name"]), (r["bytes"] or 0) / 1048576.0, _e(r["when"])))
        if not rows:
            rows = '<tr><td colspan=3 class=help>Nothing uploaded yet.</td></tr>'
        return (banner +
                '<fieldset><legend>Connected</legend>'
                '<div class=f><label>Provider</label><div class=help>%s, folder '
                '<code>%s</code> - %s</div></div>'
                '<div class=f><div class=help>Everything is encrypted on this machine '
                'before it is sent. The provider stores ciphertext with obscured file '
                'names and cannot read your saves.</div></div>'
                '<form method=post action="/admin/cloud/disconnect">'
                '<button class=ghost type=submit>Disconnect</button></form></fieldset>'
                '<fieldset><legend>Off-site copies</legend>'
                '<table><tr><th>Archive</th><th class=num>Size</th><th>When</th></tr>%s</table>'
                '<form method=post action="/admin/cloud/push" style="margin-top:12px">'
                '<button type=submit>Upload the newest backup now</button></form>'
                '</fieldset>'
                '<fieldset><legend>Restore from the cloud</legend>'
                '<form method=post action="/admin/cloud/pull">'
                '<div class=f><label>Archive name</label>'
                '<input type=text name=name placeholder="obelisk-backup-....tar.gz"> '
                '<button type=submit class=ghost>Download</button>'
                '<div class=help>Downloads and decrypts it into this Obelisk’s backups '
                'folder, where it becomes an ordinary local archive. Putting it back into '
                'the cluster is the next step and is not wired up yet.</div></div>'
                '</form></fieldset>'
                % (_e(state.get("provider", "")), _e(state.get("path", "")), line, rows))

    opts = "".join('<option value="%s">%s</option>' % (_e(p["key"]), _e(p["name"]))
                   for p in cloudlib.PROVIDERS)
    return (banner +
            '<form method=post action="/admin/cloud/connect">'
            '<fieldset><legend>Connect a cloud</legend>'
            '<div class=f><label>Provider</label>'
            '<select name=provider>%s</select>'
            '<div class=help>Any of these work the same way. Backups are encrypted here '
            'first, so the provider only ever holds ciphertext.</div></div>'
            '<div class=f><label>Encryption passphrase</label>'
            '<input type=password name=password autocomplete=new-password>'
            '<div class=help><strong>Write this down somewhere safe.</strong> It is what '
            'makes the copies unreadable to the provider - and it is the only thing that '
            'can read them back. Lose it and the off-site backups are gone, whatever the '
            'provider still has.</div></div>'
            '<div class=f><label>Folder</label>'
            '<input type=text name=path value="obelisk-backups">'
            '<div class=help>Created in the account if it does not exist.</div></div>'
            '<div class=f><label>Access token <span class=tag>Google Drive and similar</span></label>'
            '<textarea name=token rows=3 '
            'placeholder="paste the whole token block rclone prints"></textarea>'
            '<div class=help>Signing in to your account is yours to do, in your own '
            'browser - Obelisk never sees it. On any machine with a browser and rclone '
            'installed, run <code>rclone authorize drive</code>, complete the sign-in, '
            'and paste the whole token block it prints here. Leave blank for S3 or B2 '
            'and fill in the keys below instead.</div></div>'
            '<div class=f><label>S3 / B2 key <span class=tag>optional</span></label>'
            '<input type=text name=access_key_id placeholder="access key id"> '
            '<input type=password name=secret_access_key placeholder="secret key">'
            '<div class=help>Only for providers that use keys instead of a sign-in.</div></div>'
            '<button type=submit>Connect and test</button>'
            '<div class=help style="margin-top:8px">Nothing is called connected until a '
            'test upload path actually answers.</div>'
            '</fieldset></form>' % opts)
