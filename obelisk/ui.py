"""
The admin UI, rendered from the schema.

There is no per-setting UI code. Every field on the settings page comes from the
schema's label, help text, type and range, so adding a setting adds a form control
with a description and validation for free - and the page can never drift from what
the store will actually accept.

Pure functions returning strings. No server, no store writes, no I/O, so the whole
UI is testable without standing anything up.
"""

import html, re, time

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
/* Three states, three colours. "Could not check" is deliberately not green and not
   quiet - the failure this panel answers was a checker that said "up to date" about a
   question it never asked, and an unknown that looks like a pass repeats it. */
.versions{width:100%;border-collapse:collapse}
.versions td{padding:6px 8px;border-bottom:1px solid #232b36;vertical-align:top}
.current{color:#7fd18f;font-size:12px}
.newer{color:#ffc46b;font-size:12px;font-weight:600}
.unknown{color:#c9a0ff;font-size:12px;font-weight:600}
.ev{border-left:3px solid #2b3542;background:#161b22;border-radius:0 8px 8px 0;padding:8px 12px;margin:0 0 8px}
.ev.err{border-left-color:#e5534b;background:#1e1618}
.ev.warn{border-left-color:#d29922}
.ev.busy{border-left-color:#2f6feb;background:#151d2b}
.evhead{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.evtime{color:#5b6472;font-size:11px;font-variant-numeric:tabular-nums}
.evicon{font-size:12px}
.evname{color:#8b94a3;font-size:11px}
.evtext{margin-top:2px}
.evfields{margin-top:4px;color:#8b94a3;font-size:12px;word-break:break-all}
.ev details{margin-top:6px}
.ev summary{color:#8b94a3;font-size:12px;cursor:pointer}
.evbar{background:#2a2f36;border-radius:5px;height:6px;overflow:hidden;margin-top:7px;max-width:320px}
.evbar div{height:100%;background:#5b9;transition:width .5s}
.ev pre{background:#0f1620;border:1px solid #232b36;border-radius:6px;padding:8px 10px;
  margin:6px 0 0;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-word}
.card{display:flex;gap:14px;align-items:flex-start;background:#12151a;border:1px solid #303845;border-radius:10px;padding:12px 14px;margin:10px 0}
.staged{background:#16241b;color:#a9d8b5;border:1px solid #27452f;border-radius:8px;padding:10px 13px;margin:10px 0;font-size:13px}
label.inline{display:inline-block;margin-left:10px;font-size:12px;color:#8b94a3}
.callout{background:#17324a;border:1px solid #2f6feb;border-radius:10px;padding:14px 16px;margin:0 0 18px}
.callout strong{color:#e6e9ef;font-size:15px;display:block;margin-bottom:6px}
.callout .steps{color:#b8c6d9;font-size:13px;line-height:1.6}
.callout code{background:#0f1620;padding:1px 5px;border-radius:4px}
form[data-busy] button{opacity:.45;cursor:progress}
.problem{background:#2a1d1f;color:#ffb4ab;border:1px solid #4a2b2e}
.ok{color:#3fb950}
.foot{color:#5b6472;font-size:11px;margin-top:22px}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
.toolbar input[type=search]{flex:1;min-width:240px;max-width:none}
.chk{display:flex;gap:6px;align-items:center;font-weight:500;margin:0;white-space:nowrap}
.hint{color:#8b94a3;font-size:12px}
.index{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
.index a{color:#a9b4c4;background:#1a1f27;border:1px solid #262d38;border-radius:999px;
  padding:4px 11px;font-size:12px;text-decoration:none}
.index a:hover{border-color:#2f6feb;color:#e6e9ef}
.count{color:#5b6472;font-size:11px;margin-left:6px;font-variant-numeric:tabular-nums}
legend .gtoggle{background:none;color:#8b94a3;font:inherit;font-size:11px;
  text-transform:uppercase;letter-spacing:.6px;padding:0;cursor:pointer}
legend .gtoggle:hover{color:#e6e9ef}
.tag.chg{background:#17324a;color:#7fb2ff}
table.grid{max-width:420px;margin:6px 0}
table.grid input{max-width:120px;padding:4px 8px}
table.grid td,table.grid th{padding:4px 10px}
table.rows{min-width:640px}
table.rows td,table.rows th{padding:4px 8px}
table.rows input[type=text]{min-width:230px}
table.rows input[type=number]{max-width:100px}
table.permap{width:100%;max-width:560px;margin:4px 0}
table.permap td{padding:4px 8px}
"""


def _e(v):
    return html.escape("" if v is None else str(v), quote=True)


def page(title, body, nav_on=""):
    tabs = [("/", "Status"), ("/admin", "Settings"), ("/admin/cluster", "Cluster"),
            ("/admin/mods", "Mods"), ("/admin/activity", "Activity"),
            ("/admin/backups", "Backups"),
            ("/admin/restore", "Restore"),
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
    """One control, from the schema.

    A locked field is disabled rather than merely readonly, because readonly still
    submits. Posting back a value the server refuses to accept from here failed the
    entire save - so changing a port meant nothing saved at all, with an error naming
    two fields the user had not touched.
    """
    t, key = s["type"], s["key"]
    ro = " readonly disabled" if locked else ""
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
    # "Changed" is only ever claimed when the game's own default is known. For the
    # settings where nobody documents one, saying nothing is the honest answer - a
    # confident badge on a setting the operator never touched is worse than no badge.
    changed = is_changed(s, value)
    if changed:
        tags += ('<span class="tag chg" title="differs from the game default of %s">'
                 'changed</span>' % _e(_shown(s, s.get("default"))))
    hay = " ".join([s["label"], key, s.get("help", ""), s.get("group", "")]).lower()
    return ('<div class=f data-k="%s" data-hay="%s" data-changed="%s">'
            '<label for="%s">%s%s</label>%s<div class=help>%s</div></div>'
            % (_e(key), _e(hay), "1" if changed else "0",
               key, _e(s["label"]), tags, ctrl, help_txt))




# Filtering happens in the browser: the whole page is already here, and a round trip to
# re-render 194 fields to hide some of them would be slower and would lose whatever the
# operator had half-typed into another one.
SETTINGS_JS = """
<script>
(function(){
  var q=document.getElementById("q"), only=document.getElementById("onlychanged"),
      out=document.getElementById("qcount"), groups=document.querySelectorAll(".grp");
  function apply(){
    var needle=(q.value||"").trim().toLowerCase(), c=only.checked, shown=0;
    groups.forEach(function(g){
      var vis=0;
      g.querySelectorAll(".f").forEach(function(f){
        var ok=(!needle||f.dataset.hay.indexOf(needle)>=0)&&(!c||f.dataset.changed==="1");
        f.hidden=!ok; if(ok){vis++; shown++;}
      });
      g.hidden = vis===0;
      if((needle||c)&&vis) open(g,true);
    });
    out.textContent=(needle||c)?(shown+" shown"):"";
  }
  function open(g,state){
    g.querySelector(".gbody").hidden=!state;
    g.querySelector(".gtoggle").setAttribute("aria-expanded",state?"true":"false");
  }
  q.addEventListener("input",apply); only.addEventListener("change",apply);
  document.getElementById("expandall").addEventListener("click",function(){
    var anyClosed=[].some.call(groups,function(g){return g.querySelector(".gbody").hidden});
    groups.forEach(function(g){open(g,anyClosed)});
    this.textContent=anyClosed?"Collapse all":"Expand all";
  });
  groups.forEach(function(g){
    g.querySelector(".gtoggle").addEventListener("click",function(){
      open(g,g.querySelector(".gbody").hidden);
    });
  });
  // A hidden field still posts, so filtering never silently drops a value on save.
})();
</script>
"""


def _shown(s, value):
    if s["type"] == "bool":
        return "Yes" if value in (True, "true", "True", "TRUE") else "No"
    return value


def is_changed(s, value):
    """Whether this value differs from the game's own default.

    False whenever the default is not documented, which is most of Game.ini. The badge
    and the "only changed" filter both hang off this, so it has to mean what it says.
    """
    if not s.get("default_known"):
        return False
    want = s.get("default")
    if s["type"] == "bool":
        return bool(value in (True, "true", "True", "TRUE")) != bool(want)
    try:
        return float(value) != float(want)
    except (TypeError, ValueError):
        return str(value) != str(want)



def render_stat_grids(store):
    """The per-level stat multipliers, as five grids of twelve rather than sixty fields.

    Sparse cells are left blank on purpose. This operator sets two stats in each of four
    families; showing sixty boxes pre-filled with 1.0 would invite a save that turned
    eight lines into sixty, every one of them a value nobody chose. Blank means "the
    file does not mention this", and saving it blank keeps it that way.
    """
    from .gamesettings import STATS, STAT_FAMILIES
    held = store.data.get("stats", {}) or {}
    blocks = []
    for family, name, why in STAT_FAMILIES:
        cells = held.get(family, {}) or {}
        set_here = sum(1 for i, _n in STATS
                       if str(i) in cells or i in cells)
        rows = []
        for index, stat in STATS:
            val = cells.get(str(index), cells.get(index, ""))
            rows.append(
                '<tr><td class=num>%d</td><td>%s</td>'
                '<td class=num><input type=number step=any name="stat:%s:%d" '
                'value="%s" placeholder="not set" inputmode=decimal></td></tr>'
                % (index, _e(stat), _e(family), index, _e(val)))
        blocks.append(
            '<div class=f data-k="%s" data-hay="%s" data-changed="0">'
            '<label>%s%s</label>'
            '<table class=grid><tr><th class=num>#</th><th>Stat</th>'
            '<th class=num>Multiplier</th></tr>%s</table>'
            '<div class=help>%s Blank means the file says nothing about that stat, and '
            'saving it blank leaves it unmentioned. <code>%s</code></div></div>'
            % (_e(family),
               _e((" ".join([family, name, why] + [n for _i, n in STATS])).lower()),
               _e(name),
               (' <span class=count>%d of 12 set</span>' % set_here) if set_here else "",
               "".join(rows), _e(why), _e(family + "[index]")))
    return ('<fieldset id="g-per-level-stats" class=grp data-group="Per-level stats">'
            '<legend><button type=button class="ghost gtoggle" aria-expanded="true">'
            'Per-level stats</button><span class=count>%d</span></legend>'
            '<div class=gbody>%s</div></fieldset>'
            % (len(STAT_FAMILIES), "".join(blocks)))



def render_row_arrays(store):
    """The repeated-key arrays, as tables of rows.

    Columns come from the reference, but a row only writes the fields it has a value
    for - which is how three of this operator's seventeen engram entries keep their two
    fields instead of quietly acquiring five.
    """
    from .gamesettings import ROW_ARRAYS
    held = store.data.get("rows", {}) or {}
    blocks = []
    for spec in ROW_ARRAYS:
        rows = held.get(spec["key"], []) or []
        head = "".join("<th>%s</th>" % _e(lbl) for _f, lbl, _k in spec["fields"])
        body = []
        for i, row in enumerate(list(rows) + [[]]):     # one blank row to add with
            got = {k: v for k, v in row}
            cells = []
            for field, _lbl, kind in spec["fields"]:
                raw = str(got.get(field, ""))
                if kind == "text":
                    raw = raw.strip('"')
                if kind == "bool":
                    cells.append(
                        '<td><select name="row:%s:%d:%s">'
                        '<option value=""%s>-</option>'
                        '<option value="True"%s>Yes</option>'
                        '<option value="False"%s>No</option></select></td>'
                        % (_e(spec["key"]), i, _e(field),
                           "" if raw else " selected",
                           " selected" if raw == "True" else "",
                           " selected" if raw == "False" else ""))
                else:
                    cells.append('<td><input type=%s name="row:%s:%d:%s" value="%s"%s></td>'
                                 % ("number" if kind == "int" else "text",
                                    _e(spec["key"]), i, _e(field), _e(raw),
                                    ' placeholder="new row - name an engram to add it"'
                                    if i == len(rows) and field == spec["fields"][0][0]
                                    else ""))
            body.append("<tr>%s</tr>" % "".join(cells))
        blocks.append(
            '<div class=f data-k="%s" data-hay="%s" data-changed="0">'
            '<label>%s <span class=count>%d row%s</span></label>'
            '<div style="overflow-x:auto"><table class=rows><tr>%s</tr>%s</table></div>'
            '<div class=help>%s Clear the first column to delete a row; fill the blank '
            'row to add one. Empty fields are left out of the line entirely.</div></div>'
            % (_e(spec["key"]),
               _e((spec["label"] + " " + spec["key"] + " " + spec["help"]).lower()),
               _e(spec["label"]), len(rows), "" if len(rows) == 1 else "s",
               head, "".join(body), _e(spec["help"])))
    return ('<fieldset id="g-engrams" class=grp data-group="Engrams">'
            '<legend><button type=button class="ghost gtoggle" aria-expanded="true">'
            'Engrams &amp; lists</button><span class=count>%d</span></legend>'
            '<div class=gbody>%s</div></fieldset>' % (len(ROW_ARRAYS), "".join(blocks)))



def render_map_overrides(store):
    """What each map does differently, and what it simply inherits.

    Blank means inherited, and the cluster's value is in the placeholder so there is no
    guessing what blank resolves to. Typing a value makes an override; clearing it takes
    the override away rather than setting the field to nothing - which is the difference
    between "this map is the same as the others" and "this map has no players allowed".
    """
    from .schema import SETTINGS
    from . import maps as mapcat
    per_map = [s for s in SETTINGS if s.get("per_map")]
    raw = store.get("maps")
    keys = [k.strip() for k in str(raw).split(",") if k.strip()] if isinstance(raw, str) else list(raw or ())
    if not keys:
        return ""
    chosen = mapcat.resolve(keys)
    ids = mapcat.instance_ids([m["key"] for m in chosen])

    blocks, total = [], 0
    for m, instance in zip(chosen, ids):
        here = store.data.get("maps", {}).get(m["key"], {}) or {}
        rows, n = [], 0
        for s in per_map:
            key = s["key"]
            overridden = key in here
            if overridden:
                n += 1
            cluster = store.get(key)
            shown = here.get(key, "") if overridden else ""
            if s["type"] == "bool":
                ctrl = ('<select name="map:%s:%s"><option value=""%s>inherit (%s)</option>'
                        '<option value="true"%s>Yes</option>'
                        '<option value="false"%s>No</option></select>'
                        % (_e(m["key"]), _e(key), "" if overridden else " selected",
                           "Yes" if cluster in (True, "true") else "No",
                           " selected" if shown in (True, "true") else "",
                           " selected" if overridden and shown in (False, "false") else ""))
            elif s["type"] == "password":
                # Never the value, not even as a hint. The placeholder is rendered into
                # the page, so "inherits <the cluster password>" would put the shared
                # server password on screen once per map - which is how a field meant to
                # explain inheritance turns into a secret leak.
                ctrl = ('<input type=password name="map:%s:%s" value="" '
                        'placeholder="%s">'
                        % (_e(m["key"]), _e(key),
                           "set for this map - type to replace" if overridden
                           else "inherits the cluster setting"))
            else:
                ctrl = ('<input type=%s name="map:%s:%s" value="%s" placeholder="%s">'
                        % ("number" if s["type"] in ("int", "float") else "text",
                           _e(m["key"]), _e(key), _e(shown),
                           _e("inherits %s" % cluster)))
            rows.append('<tr><td>%s%s</td><td>%s</td></tr>'
                        % (_e(s["label"]),
                           ' <span class="tag chg">override</span>' if overridden else "",
                           ctrl))
        total += n
        blocks.append(
            '<div class=f data-k="map-%s" data-hay="%s" data-changed="%s">'
            '<label>%s%s</label>'
            '<table class=permap>%s</table></div>'
            % (_e(m["key"]),
               _e(("%s %s per map override" % (m["name"], instance)).lower()),
               "1" if n else "0", _e(m["name"]),
               (' <span class=count>%d override%s</span>' % (n, "" if n == 1 else "s"))
               if n else ' <span class=count>inherits everything</span>',
               "".join(rows)))

    return ('<fieldset id="g-per-map" class=grp data-group="Per-map">'
            '<legend><button type=button class="ghost gtoggle" aria-expanded="false">'
            'Per-map overrides</button><span class=count>%d</span></legend>'
            '<div class=gbody hidden>'
            '<div class=help style="margin:8px 0 14px">Blank inherits the cluster value, '
            'shown in each box. Only these %d settings can differ per map: everything '
            'that reaches the game through Game.ini or GameUserSettings.ini is shared, '
            'because the server image links every map to one copy of those files.</div>'
            '%s</div></fieldset>'
            % (total, len(per_map), "".join(blocks)))



def render_version(info):
    """Which Obelisk is running, and whether a newer one is published.

    Points at the Docker page rather than offering a button, because applying the update
    is that page's job - a manager that replaces its own container mid-flight cannot
    report how it went. And when the registry could not be reached it says so, because
    the failure this whole panel exists for was a checker that answered "up to date"
    when it did not know.
    """
    from .version import short
    if not info:
        return ""
    running = _e(short(info.get("commit"), 7))
    body = ('<div class=f><label>This Obelisk</label>'
            '<div>version <code>%s</code>, image <code>%s</code></div>' %
            (running, _e(short(info.get("digest")))))

    if info.get("problem"):
        body += ('<div class=note>Could not check for a newer version: %s. '
                 'That is not the same as being up to date - it means we do not '
                 'know.</div>' % _e(info["problem"]))
    elif info.get("update_available"):
        body += ('<div class=problem><strong>An update is available.</strong> '
                 'Published image is <code>%s</code>. Apply it from the Unraid '
                 '<b>Docker</b> page: click the <b>Obelisk</b> icon and choose '
                 '<b>Apply Update</b> - or <b>Force Update</b> if the page still says '
                 'up-to-date, which it sometimes does for this registry. Your settings, '
                 'cluster and saves are untouched by an update.</div>'
                 % _e(short(info.get("published"))))
    else:
        body += '<div class=note>Up to date.</div>'
    return '<fieldset><legend>Obelisk version</legend>%s</div></fieldset>' % body


def render_ark_update(store, status, ready=None, job=None, owns=True, staging_on=True):
    """Running against latest, for the build and for every mod, with the buttons inline.

    Three states per row and not two. "Newer" and "current" are the easy ones; the third
    is **could not check**, and it gets its own colour rather than quietly rendering as
    a tick. The whole reason this panel exists is that a checker somewhere answered "up
    to date" without asking, so a green row here has to mean an answer came back.
    """
    def cell(running, latest, newer):
        run = '<code>%s</code>' % _e(running or "—")
        if newer is None:
            return ('%s <span class=unknown>? could not check</span>' % run)
        if newer:
            return ('%s → <code>%s</code> <span class=newer>update available</span>'
                    % (run, _e(latest)))
        return '%s <span class=current>current</span>' % run

    status = status or {}
    if not status.get("build") and not status.get("mods"):
        # Never looked yet, which is not the same as "could not check" and must not
        # render as a row of unknowns that look like failures.
        return ('<fieldset><legend>ARK build and mods</legend><div class=note>'
                'Not checked yet. Obelisk asks Steam and CurseForge shortly after it '
                'starts, and every half hour after that.</div></fieldset>')

    build = status.get("build") or {}
    rows = ['<tr><td><b>ARK server build</b></td><td>%s</td></tr>'
            % cell(build.get("running"), build.get("latest"), build.get("newer"))]
    if build.get("problem"):
        rows.append('<tr><td></td><td class=help>%s</td></tr>' % _e(build["problem"]))

    for row in status.get("mods") or []:
        name = row["name"]
        if row.get("url"):
            name = '<a href="%s" target=_blank rel=noopener>%s</a>' % (
                _e(row["url"]), _e(name))
        else:
            name = _e(name)
        line = '<tr><td>%s <span class=help>%s</span></td><td>%s</td></tr>' % (
            name, _e(row["id"]),
            cell(row.get("running"), row.get("latest"), row.get("newer")))
        rows.append(line)
        if row.get("problem"):
            rows.append('<tr><td></td><td class=help>%s</td></tr>' % _e(row["problem"]))

    # What has been staged, and when it was proved. The timestamp is the point: a
    # verification from before the last mod change is not a verification of what would
    # be applied now.
    if ready:
        when = time.strftime("%d %b %H:%M", time.localtime(ready.get("when") or 0))
        loaded = ready.get("loaded") or {}
        staged = ('<div class=staged><b>Staged and verified.</b> Build <code>%s</code> '
                  'with %d mod%s booted cleanly on the staging server — '
                  '<b>verified by staging boot at %s</b>.<div class=help>%s</div></div>'
                  % (_e(ready.get("build")), len(loaded),
                     "" if len(loaded) == 1 else "s", _e(when),
                     _e(", ".join("%s→%s" % (m, f) for m, f in sorted(loaded.items())))))
    else:
        failed = (store.data.get("ark_update") or {}).get("primed")
        if isinstance(failed, dict) and not failed.get("ok"):
            staged = ('<div class=problem><b>The last rehearsal failed, so there is '
                      'nothing safe to apply.</b><div class=help>%s</div></div>'
                      % _e("; ".join(failed.get("problems") or [])))
        else:
            staged = ('<div class=note>Nothing staged yet. Priming downloads the new '
                      'build and fetches the mods on the staging server — your cluster '
                      'keeps running throughout.</div>')

    busy = (job or {}).get("state") == "running"
    step = _e((job or {}).get("step") or "")
    if busy:
        buttons = ('<div class=note>Working: %s <span class=help>This page updates '
                   'itself.</span></div>' % (step or "starting"))
    else:
        can_apply = bool(ready) and owns
        buttons = (
            '<button type=submit formaction="/admin/update/prime"%s>Prime update</button> '
            '<button type=submit formaction="/admin/update/apply"%s>Apply now</button> '
            % ("" if staging_on else " disabled", "" if can_apply else " disabled"))
        buttons += ('<label class=inline><input type=checkbox name=force value=1> '
                    'apply even with players online</label>')

    warn = ""
    if not staging_on:
        warn += ('<div class=note>The staging server is off, so updates cannot be '
                 'rehearsed. Turn it on under <b>Resources</b> to prime updates.</div>')
    if not owns:
        warn += ('<div class=problem>The server image is applying ARK updates itself, '
                 'so Obelisk will not - two update systems on one cluster means two '
                 'restarts nobody scheduled. It updates inside your update window with '
                 'no warning and no check that the new build loads with your mods. '
                 'Change <b>Who applies ARK updates</b> to Obelisk under '
                 '<b>Cluster</b> to use the flow below.</div>')

    return ('<form method=post action="/admin/update/prime">'
            '<fieldset><legend>ARK build and mods</legend>%s'
            '<table class=versions>%s</table>%s'
            '<div style="margin-top:14px">%s</div></fieldset></form>'
            % (warn, "".join(rows), staged, buttons))


def render_events(items, jobs=None, limit=None, compact=False):
    """The activity feed: everything that reached the admin channel, and more of it.

    The bar this answers is a complaint that was exactly right - the Discord channel
    knew more than the UI did, which is backwards for a product whose whole premise is
    that the UI is enough. Discord is a convenience mirror; this is the record.

    Both are fed by the same announcements, so they cannot disagree about what happened.
    Where they differ is depth: a channel gets one readable line, and this gets the
    line, the fields, and the `detail` - every problem rather than the first three, the
    whole per-mod list, the tail of the log a failure came out of.
    """
    if not items and not any((j or {}).get("state") == "running"
                             for j in (jobs or {}).values()):
        return ('<fieldset><legend>Activity</legend><div class=note>Nothing has '
                'happened yet. Backups, updates, restores and cluster changes all '
                'appear here as they run.</div></fieldset>')

    return ('<fieldset><legend>Activity</legend><div id=feed>%s%s</div>'
            '<div class=help style="margin-top:10px">Everything here also goes to the '
            'Discord admin channel, from the same source - so the two cannot drift. '
            'This one keeps the detail.</div></fieldset>'
            % (running_rows(jobs), event_rows(items, limit=limit, compact=compact)))


def event_rows(items, limit=None, compact=False):
    """Just the rows. The live feed appends these straight into the page, so it renders
    them with the same function the page did rather than a second implementation that
    can drift from it."""
    rows = []
    for item in (items[:limit] if limit else items):
        level = item.get("level") or "info"
        when = time.strftime("%d %b %H:%M:%S", time.localtime(item.get("at") or 0))
        icon = _EVENT_ICONS.get(item.get("event", "").rsplit(".", 1)[-1], "•")
        cls = "ev err" if level == "error" else ("ev warn" if level == "warning" else "ev")
        body = ('<div class="%s"><div class=evhead>'
                '<span class=evtime>%s</span> <span class=evicon>%s</span> '
                '<code class=evname>%s</code></div>'
                '<div class=evtext>%s</div>'
                % (cls, _e(when), icon, _e(item.get("event")), _e(item.get("text"))))
        if item.get("fields"):
            body += '<div class=evfields><code>%s</code></div>' % _e(item["fields"])
        if item.get("detail"):
            # Folded rather than hidden. The failure case is the one where somebody is
            # actually reading this, and making them click twice to see why is how a
            # UI ends up less useful than a chat channel.
            body += ('<details%s><summary>details</summary><pre>%s</pre></details>'
                     % (" open" if level == "error" and not compact else "",
                        _e(item["detail"])))
        rows.append(body + "</div>")
    return "".join(rows)


def running_rows(jobs):
    """What is happening right now, above the history.

    Discord gets a phase line per step; this gets the same phases plus the percentage
    the existing progress readers already produce, so a forty-minute prime is watchable
    here rather than only in a chat window.
    """
    out = ""
    for name, job in (jobs or {}).items():
        if (job or {}).get("state") != "running":
            continue
        step = job.get("step") or job.get("phase") or "starting"
        bar = ""
        percent = job.get("percent")
        if percent is None and job.get("total"):
            percent = round(100.0 * (job.get("done") or 0) / job["total"], 1)
        if percent is not None:
            bar = ('<div class=evbar><div style="width:%s%%"></div></div>'
                   % _e(min(100, max(0, percent))))
        out += ('<div class="ev busy"><div class=evhead><span class=evicon>…</span> '
                '<code class=evname>%s in progress</code></div>'
                '<div class=evtext>%s</div>%s</div>' % (_e(name), _e(step), bar))
    return out


_EVENT_ICONS = {"start": "▶", "done": "✅", "failed": "❌", "unsafe": "❌",
                "applied": "✅", "primed": "✅", "up": "✅", "degraded": "⚠",
                "warning": "⚠", "phase": "…", "available": "⬆", "updated": "⬆",
                "stop": "■", "note": "•"}


# Polls for events newer than the newest one on the page and prepends them, so an
# operation that takes forty minutes is watchable without reloading. Same idea as the
# backup progress block, generalised: the page says what is happening now, not what was
# happening when it was opened.
FEED_LIVE = """
<script>
(function(){
  const feed = document.getElementById('feed');
  if (!feed) return;
  let newest = Number(feed.dataset.newest || 0);
  async function tick(){
    try{
      const r = await fetch('/admin/activity/feed?since=' + newest, {cache:'no-store'});
      if (r.ok){
        const d = await r.json();
        if (d.html && d.newest > newest){
          newest = d.newest;
          feed.insertAdjacentHTML('afterbegin', d.html);
        }
        if (d.running !== undefined){
          document.querySelectorAll('.ev.busy').forEach(e => e.remove());
          if (d.running) feed.insertAdjacentHTML('afterbegin', d.running);
        }
      }
    }catch(e){}
    setTimeout(tick, 4000);
  }
  setTimeout(tick, 4000);
})();
</script>
"""


def render_settings(store):
    """The settings page: 194 of them, so finding one has to be a first-class job.

    A flat list was fine at 52. At 194 it is 75 KB of scrolling, and the answer to
    "where is stack size" became ctrl-F. So: a group index that jumps, groups that
    collapse, a search that filters as you type across names, keys and descriptions,
    and a filter for the ones that differ from the game's defaults - which is how you
    read a cluster somebody else configured, including your own from a year ago.
    """
    blocks, index, total_changed = [], [], 0
    for g in GROUPS:
        rows = [s for s in SETTINGS if s["group"] == g]
        if not rows:
            continue
        gid = "g-" + re.sub(r"[^a-z0-9]+", "-", g.lower()).strip("-")
        changed_here = sum(1 for s in rows
                           if is_changed(s, store.get(s["key"])))
        total_changed += changed_here
        fields = "".join(_field(s, store.get(s["key"]), s["key"] in INSTALL_KEYS)
                         for s in rows)
        index.append('<a href="#%s">%s <span class=count>%d</span></a>'
                     % (gid, _e(g), len(rows)))
        blocks.append(
            '<fieldset id="%s" class=grp data-group="%s"><legend>'
            '<button type=button class="ghost gtoggle" aria-expanded="true">%s</button>'
            '<span class=count>%d</span>%s</legend>'
            '<div class=gbody>%s</div></fieldset>'
            % (gid, _e(g), _e(g), len(rows),
               ('<span class="tag chg">%d changed</span>' % changed_here)
               if changed_here else "", fields))

    blocks.append(render_stat_grids(store))
    index.append('<a href="#g-per-level-stats">Per-level stats '
                 '<span class=count>5</span></a>')
    blocks.append(render_row_arrays(store))
    index.append('<a href="#g-engrams">Engrams &amp; lists</a>')
    _pm = render_map_overrides(store)
    if _pm:
        blocks.append(_pm)
        index.append('<a href="#g-per-map">Per-map overrides</a>')

    todo = store.readiness()
    banner = ""
    if todo:
        banner = ('<div class=problem>Before this cluster can start: %s</div>'
                  % _e(", ".join(b["label"] for b in todo)))

    known = sum(1 for s in SETTINGS if s.get("default_known"))
    toolbar = (
        '<div class=toolbar>'
        '<input type=search id=q placeholder="Search %d settings - name, key or '
        'description" autocomplete=off>'
        '<label class=chk><input type=checkbox id=onlychanged> Only changed '
        '(%d)</label>'
        '<button type=button class=ghost id=expandall>Expand all</button>'
        '<span class=hint id=qcount></span>'
        '</div>'
        '<div class=index>%s</div>'
        '<div class=help style="margin:-4px 0 14px">%d of these have a documented game '
        'default to compare against, so "changed" is only shown for those. The rest are '
        'not marked either way rather than guessed at.</div>'
        % (len(SETTINGS), total_changed, "".join(index), known))

    return ('<form method=post action="/admin/save">%s%s%s%s'
            '<button type=submit>Save changes</button></form>'
            % (banner, toolbar, "".join(blocks), SETTINGS_JS))


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
    # Colour follows what the server is doing, not merely whether a process exists. A
    # container that aborts and restarts every few seconds reports "running" the whole
    # time, and showing that in green is a status that lies.
    css = {"ok": "ok", "busy": "", "bad": "bad"}
    rows = ""
    for s in status.get("services", []):
        level = s.get("level") or ("ok" if s.get("state") == "running" else "bad")
        says = s.get("says") or s.get("status") or s.get("state") or "?"
        rows += ("<tr><td>%s</td><td class=%s>%s</td><td>%s</td></tr>"
                 % (_e(s.get("service") or s.get("name") or "?"), css.get(level, ""),
                    _e(says), _e(s.get("status") or "")))
        if s.get("log_tail"):
            rows += ('<tr><td colspan=3><details><summary class=help>why it is '
                     'failing</summary><pre class=logtail>%s</pre></details></td></tr>'
                     % _e(s["log_tail"]))
    if not rows:
        return '<div class=note>The cluster is defined but nothing is running.</div>'
    banner = ""
    bad = [s for s in status.get("services", []) if s.get("level") == "bad"]
    if bad:
        banner = ('<div class=problem><strong>%d map%s failing to start.</strong> This '
                  'is not a slow first start - the server keeps aborting and restarting. '
                  'Open the row below for the reason.</div>'
                  % (len(bad), "" if len(bad) == 1 else "s"))
    return (banner + '<fieldset><legend>Running now</legend><table>'
            '<tr><th>Map</th><th>Doing</th><th>Container</th></tr>%s</table>'
            '<div class=help style="margin-top:10px">A first start downloads about 12 GB '
            'of game files and then generates the world, so it is normally slow. The '
            'phase and elapsed time above are how you tell it is still moving.</div>'
            '</fieldset>' % rows)


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
            '<form method=post action="/admin/maps" onsubmit="for(const b of this.querySelectorAll(&quot;button&quot;)){b.disabled=true}this.querySelectorAll(&quot;button&quot;)[0].textContent=&quot;Working...&quot;">'
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
            '<div class=help><strong>This is not your in-game admin password.</strong> '
            'The setup code opens this web UI; the admin/RCON password is the one your '
            'servers use in game. They are different secrets and only the code opens '
            'this page.<br>The code is never written into a file you have to edit. If '
            'the log has scrolled past it, restarting the container prints it again '
            'until setup is finished - or run '
            '<code>docker exec &lt;container&gt; obelisk code</code>.'
            '</div></div></fieldset>'
            % err)


STATUS_STYLE = {"ok": ("ok", "installed"),
                "stub": ("bad", "BROKEN INSTALL"),
                "missing": ("bad", "not downloaded"),
                "orphan": ("warn", "leftover")}


def render_mods(store, health=None, found=None, problem=""):
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
            '</form>' + _add_a_mod(store, found, problem)) % (banner, "".join(rows))


def _mod_card(card, listed):
    """What the operator is about to add, shown before they add it.

    A number is not recognisable and never was. The picture, the author and the download
    count are how a person tells the mod they meant from the one with a similar name -
    which is the actual failure being prevented, because a wrong id is only noticed
    later, as a server fetching something nobody wanted.
    """
    already = card["id"] in modlib.parse(listed)
    picture = ('<img src="%s" alt="" width=64 height=64 style="border-radius:8px;'
               'flex:0 0 auto;background:#12151a">' % _e(card["thumbnail"])
               if card.get("thumbnail") else "")
    who = ", ".join(a for a in card.get("authors") or [] if a)
    bits = []
    if who:
        bits.append("by %s" % _e(who))
    if card.get("downloads"):
        bits.append("%s downloads" % format(card["downloads"], ","))
    if card.get("file_id"):
        bits.append("latest file <code>%s</code>%s" % (
            _e(card["file_id"]),
            " (%s)" % _e(card["file_date"]) if card.get("file_date") else ""))
    name = _e(card["name"])
    if card.get("url"):
        name = '<a href="%s" target=_blank rel=noopener>%s</a>' % (_e(card["url"]), name)

    if already:
        action = '<span class=current>already in this cluster</span>'
    else:
        action = ('<button type=submit name=addmod value="%s">Add to cluster</button>'
                  % _e(card["id"]))
    return ('<div class=card>%s<div style="flex:1 1 auto;min-width:0">'
            '<div><b>%s</b> <span class=help>%s</span></div>'
            '<div class=help>%s</div><div class=help>%s</div>'
            '<div style="margin-top:9px">%s</div></div></div>'
            % (picture, name, _e(card["id"]), _e(card.get("summary") or ""),
               " &middot; ".join(bits), action))


def _add_a_mod(store, found=None, problem=""):
    """Look it up, look at it, then add it.

    The button that actually changes the mod list only exists once something has been
    found, so "added" is always a thing the operator saw first. The keyed search sits
    behind the same form when there is a key; without one this says so rather than
    offering a worse search that looks like the real one.
    """
    body = ""
    if problem:
        body += '<div class=problem>%s</div>' % problem
    if found:
        body += _mod_card(found, str(store.get("mod_ids") or ""))
    keyed = bool(str(store.get("curseforge_api_key") or "").strip())
    note = ('Paste the mod&rsquo;s CurseForge address or its Project ID. Obelisk looks '
            'it up first, so you can see what you are adding. New mods go last so they '
            'cannot silently outrank something that already works &mdash; and the '
            'staging server fetches and checks it before your cluster ever loads it.')
    if not keyed:
        note += ('<br>Searching CurseForge from here needs a free API key from '
                 '<code>console.curseforge.com</code>; add it under <b>Advanced</b>. '
                 'Adding by Project ID works without one.')
    return ('<form method=post action="/admin/mods/find">'
            '<fieldset><legend>Add a mod</legend><div class=f>'
            '<label>CurseForge address or Project ID</label>'
            '<input type=text name=ref placeholder="929110 or '
            'https://www.curseforge.com/ark-survival-ascended/mods/...">'
            ' <button type=submit class=ghost>Look up</button>'
            '<div class=help>%s</div></div>%s</fieldset></form>' % (note, body))

# The backup runs on a worker thread now, so the page it was started from is free to
# say what it is doing. Same rule as the launch phases: name the step, show how far in,
# and never show a bar that is really just an animation - the percentage comes from
# bytes actually read, and when there is no total to divide by it says so instead.
BACKUP_PROGRESS = """
<div id=bkwrap hidden>
  <div class=note><strong id=bkphase>Working</strong> <span id=bkdetail></span></div>
  <div style="background:#2a2f36;border-radius:6px;height:10px;overflow:hidden;margin:8px 0">
    <div id=bkbar style="height:100%;width:0;background:#5b9;transition:width .4s"></div>
  </div>
</div>
<div id=bkresult></div>
<script>
(function(){
  var W=["flushing","Asking every map to save its world"],
      A=["archiving","Copying and compressing"],
      V=["verifying","Reading the archive back to prove it works"];
  var words={flushing:W[1],archiving:A[1],verifying:V[1],starting:"Starting"};
  function tick(){
    fetch("/admin/backup/status",{credentials:"same-origin"})
      .then(function(r){return r.json()}).then(function(j){
        var wrap=document.getElementById("bkwrap"),
            btn=document.getElementById("bkbtn"),
            res=document.getElementById("bkresult");
        if(j.state==="running"){
          wrap.hidden=false; if(btn){btn.disabled=true;btn.textContent="Backing up..."}
          document.getElementById("bkphase").textContent=words[j.phase]||j.phase;
          var d=document.getElementById("bkdetail");
          d.textContent=(j.percent!=null?j.percent+"% of "+(j.human||"")+" read":
                         (j.human?j.human+" read":""))+" - "+j.elapsed+"s";
          document.getElementById("bkbar").style.width=(j.percent!=null?j.percent:5)+"%";
          setTimeout(tick,1500);
        } else if(j.state==="done"){
          wrap.hidden=true; if(btn){btn.disabled=false;btn.textContent="Back up now"}
          res.innerHTML='<div class="'+(j.ok?"note":"problem")+'"></div>';
          res.firstChild.textContent=j.message;
        }
      }).catch(function(){setTimeout(tick,4000)});
  }
  tick();
})();
</script>
"""



def render_restore(store, archives, chosen=None, info=None, notes=(),
                   message="", problem="", job=None):
    """Restore one map from one archive, with what is in it shown before committing.

    The order on the page is the order of the decision: which archive, what is in it,
    how it differs from what is running, and only then which map - because "this archive
    is from a different cluster" is something you want to read before choosing a map,
    not after.
    """
    from . import maps as mapcat
    from .backup import human_size
    banner = ""
    if problem:
        banner = '<div class=problem>%s</div>' % _e(problem)
    elif message:
        banner = '<div class=note>%s</div>' % _e(message)

    job = job or {}
    running = job.get("state") == "running"
    if job.get("state") == "done" and not message and not problem:
        if job.get("ok"):
            message = job.get("message", "")
        else:
            problem = job.get("message", "")
        banner = ('<div class="%s">%s</div>'
                  % ("note" if job.get("ok") else "problem", _e(
                      job.get("message", ""))))

    if not archives:
        return (banner + '<fieldset><legend>Restore</legend><div class=help>No backups '
                'on disk yet. Make one from the Backups tab first - there is nothing to '
                'restore from.</div></fieldset>')

    opts = "".join('<option value="%s"%s>%s &mdash; %s</option>'
                   % (_e(a["name"]), " selected" if a["name"] == chosen else "",
                      _e(a["name"]), _e(human_size(a["bytes"])))
                   for a in archives)

    detail = ""
    if info:
        expected = info.get("from_manifest")
        rows = [("Taken", info.get("created") or "unknown"),
                ("Cluster id", info.get("cluster_id") or "-"),
                ("Maps inside" if not expected else "Maps expected",
                 ", ".join(info["maps"]) or "none"),
                ("Mods, in order", info.get("mod_ids") or "none")]
        detail = ('<table>%s</table>'
                  % "".join("<tr><td>%s</td><td><code>%s</code></td></tr>"
                            % (_e(k), _e(v)) for k, v in rows))
        if expected:
            detail += ('<div class=help>Read from the archive’s manifest, which '
                       'lists the maps the cluster held when it was taken. A map that '
                       'had never booted has no save to be in there - the archive itself '
                       'is checked for the map you pick, before anything is '
                       'stopped.</div>')
        if notes:
            detail += ('<div class=problem><strong>Differences from this cluster</strong>'
                       '<ul>%s</ul></div>'
                       % "".join("<li>%s</li>" % _e(n) for n in notes))
        else:
            detail += ('<div class=note>Nothing in this archive disagrees with the '
                       'cluster you are running.</div>')

    keys = [k.strip() for k in str(store.get("maps") or "").split(",") if k.strip()]
    inside = set(info["maps"]) if info else set()
    picks = "".join(
        '<option value="%s"%s>%s%s</option>'
        % (_e(k), "" if (not info or mapcat.BY_KEY[k]["map_id"] in inside) else " disabled",
           _e(mapcat.BY_KEY[k]["name"]),
           "" if (not info or mapcat.BY_KEY[k]["map_id"] in inside) else " - not in this archive")
        for k in keys if k in mapcat.BY_KEY)

    return (banner +
            '<form method=post action="/admin/restore/inspect">'
            '<fieldset><legend>1. Choose an archive</legend><div class=f>'
            '<select name=archive>%s</select> '
            '<button type=submit>Look inside</button>'
            '<div class=help>Nothing is changed by looking. The archive is opened and '
            'read, which is also how it is checked.</div></div></fieldset></form>'
            '%s'
            '<form method=post action="/admin/restore/run" data-busy>'
            '<input type=hidden name=archive value="%s">'
            '<fieldset><legend>2. Restore one map</legend><div class=f>'
            '<select name=map>%s</select> '
            '<button type=submit%s>Restore this map</button>'
            '<div class=help>Only the map you pick is stopped; the rest of the cluster '
            'keeps serving. Its current world is copied first, and the world being '
            'replaced is kept on disk afterwards - nothing is deleted. Worlds only: '
            'your mod list, ports and cluster id are not touched.</div>'
            '</div></fieldset></form>'
            '%s%s'
            % (opts, detail, _e(chosen or ""), picks,
               " disabled" if (not info or running) else "",
               RESTORE_JS, _superseded_block(store)))


def _superseded_block(store):
    """What previous restores are still holding on to, so it can be reclaimed.

    A restore keeps the world it replaced, deliberately and for ever. That is the right
    default and it is also, eventually, a disk full of worlds nobody wants - so it has
    to be visible somewhere rather than discovered.
    """
    from .restore import superseded_worlds
    rows = superseded_worlds(store)
    if not rows:
        return ""
    return ('<fieldset><legend>Replaced worlds still on disk</legend>'
            '<table><tr><th>Folder</th><th class=num>Size</th></tr>%s</table>'
            '<div class=help>Kept by earlier restores so nothing you replaced is ever '
            'gone. Delete them by hand once you are happy with the restore - Obelisk '
            'will not remove a world for you.</div></fieldset>'
            % "".join('<tr><td><code>%s</code></td><td class=num>%s</td></tr>'
                      % (_e(r["name"]), _e(r["human"])) for r in rows))


# The restore stops a map, swaps a world and then waits minutes for a server to load it.
# A form post that simply hangs for that long is indistinguishable from one that has
# failed - and this is the feature where "I do not know what it is doing" is least
# acceptable, because it is in the middle of touching somebody's saves.
RESTORE_JS = """
<div id=rswrap hidden>
  <div class=note><strong>Restoring</strong> <span id=rsstep></span>
  <span id=rselapsed></span></div>
</div>
<script>
(function(){
  function tick(){
    fetch("/admin/restore/status",{credentials:"same-origin"})
      .then(function(r){return r.json()}).then(function(j){
        var w=document.getElementById("rswrap");
        if(j.state==="running"){
          w.hidden=false;
          document.getElementById("rsstep").textContent=j.step||"";
          document.getElementById("rselapsed").textContent="("+j.elapsed+"s)";
          setTimeout(tick,2000);
        } else if(w && !w.hidden){ location.reload(); }
      }).catch(function(){setTimeout(tick,5000)});
  }
  tick();
})();
</script>
"""


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
        from .backup import human_size
        body.append("<tr><td><code>%s</code></td><td class=num>%s</td>"
                    "<td>%s</td></tr>"
                    % (_e(r["name"]), _e(human_size(r["bytes"])), _e(r["when"])))
    if not body:
        body = ['<tr><td colspan=3 class=help>No backups yet.</td></tr>']

    return (banner +
            '<form method=post action="/admin/backup">'
            '<fieldset><legend>Back up now</legend>'
            + BACKUP_PROGRESS +
            '<div class=f><button type=submit id=bkbtn>Back up now</button>'
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

def render_connect(entries, web_address="", host_known=True):
    """Where to actually connect, per map.

    The one thing a status page is for is answering "what do I type in". A container
    cannot see the address its host answers on, so this is only as good as what it was
    told - and it says so rather than printing a confident guess.
    """
    if not entries:
        return ""
    rows = "".join(
        "<tr><td>%s</td><td><code>%s</code></td></tr>" % (_e(n), _e(a))
        for n, a in entries)
    note = ""
    if not host_known:
        note = ('<div class=help>Obelisk cannot see the address this machine answers '
                'on from inside a container, so it is showing its own host name. If '
                'that is not what people type, set <b>Server address</b> in the '
                'container template to your LAN IP - it changes these lines and nothing '
                'else.</div>')
    web = ""
    if web_address:
        web = ('<div class=help style="margin-bottom:8px">Obelisk itself: '
               '<code>%s</code></div>' % _e(web_address))
    return ('<fieldset><legend>Connect</legend>%s'
            '<table><tr><th>Map</th><th>Address</th></tr>%s</table>'
            '<div class=help style="margin-top:10px">In game: <b>Join ARK</b> &rarr; '
            '<b>Unofficial</b>, or open the console and type '
            '<code>open &lt;address&gt;</code>.</div>%s</fieldset>' % (web, rows, note))
