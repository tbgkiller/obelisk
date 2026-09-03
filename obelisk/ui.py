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
.problem{background:#2a1d1f;color:#ffb4ab;border:1px solid #4a2b2e}
.ok{color:#3fb950}
.foot{color:#5b6472;font-size:11px;margin-top:22px}
"""


def _e(v):
    return html.escape("" if v is None else str(v), quote=True)


def page(title, body, nav_on=""):
    tabs = [("/", "Status"), ("/admin", "Settings"), ("/admin/cluster", "Cluster")]
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


def render_cluster(store, plan):
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

    launch = ('<button type=submit formaction="/admin/launch"%s>Launch cluster</button>'
              % ("" if plan["ok"] else " disabled"))
    summary = ("%d map%s, %s of RAM at most, plus Obelisk on port %s."
               % (len(plan["maps"]), "" if len(plan["maps"]) == 1 else "s",
                  plan["total_memory"], plan["obelisk_port"]))

    return ('<form method=post action="/admin/maps">'
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
    err = '<div class=problem>%s</div>' % _e(error) if error else ""
    return ('%s<fieldset><legend>Set up Obelisk</legend>'
            '<div class=f><label>Setup code</label>'
            '<form method=post action="/setup"><input type=password name=code '
            'autocomplete=off autofocus> <button type=submit>Continue</button></form>'
            '<div class=help>Printed to the container log when Obelisk first started. '
            'Run <code>docker logs obelisk</code> to see it.</div></div></fieldset>'
            % err)
