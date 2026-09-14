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
from . import bans as bansctl
from . import cap as capctl

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{background:#12151a;color:#e6e9ef;font:14px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}
/* The tabs, the search and Save stay put. On a page that is 75 KB of settings, having
   to scroll back to the top to search or save is most of the work of using it. The
   header's real height goes into --topH so the toolbar can sit directly under it
   without anybody hardcoding a number that a longer title would break. */
header.top{position:sticky;top:-24px;z-index:30;background:#12151a;
  margin:-24px -24px 0;padding:24px 24px 0}
header.top nav{margin-bottom:0;padding-bottom:10px}
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
.note,.problem,.warn{border-radius:8px;padding:10px 13px;margin:10px 0;font-size:13px}
.note{background:#1d2530;color:#a9b4c4;border:1px solid #2b3542}
/* Three states, three colours - the same rule the version panel keeps. Amber is
   "this is not set up", which is neither the grey of a thing that worked nor the
   red of a thing that broke. Collapsing it into either one is how a cloud nobody
   ever connected reads as an outage, or as a completed upload. */
.warn{background:#2a2519;color:#e8c37a;border:1px solid #4a3f22}
/* Who's online. Its own names, not .chip - the mods panel defines .chip later in
   this same sheet at equal specificity, so the rules here were being overridden
   and player names were quietly inheriting a mod's look. It also owns .chip.ok,
   .bad, .new and .unk, which is the exact vocabulary per-player state will want
   once there are buttons on these rows. */
.whoroster{margin:2px 0 4px}
.whomap{margin:12px 0 4px;font-size:12px;color:#8b94a3;letter-spacing:.3px;text-transform:uppercase}
.whomap:first-child{margin-top:2px}
/* Every other multi-item flex row on this page wraps - the presets, the stepper,
   the mod chips, the toolbar. These did not, and a long name beside a text field
   and three buttons has no give at all. */
.whorow{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:6px 10px;margin:3px 0;background:#12151a;border:1px solid #232b36;border-radius:8px}
.whoname{font-weight:500;color:#e6e9ef}
.whoacts{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:flex-end}
.whonote{padding:6px 10px;margin:3px 0;font-size:12px;color:#8b94a3}
.whorow.quiet{border-style:dashed;color:#8b94a3}
.whorow.asking{border-color:#7d642f;background:#1b1710}
.whoflag{font-size:11px;color:#8b94a3}
.whoform{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0}
.whoform input{font-size:12px;padding:4px 8px;width:170px;min-width:120px;flex:1 1 120px;margin:0}
/* The by-id field is the one control here that is not about a row, so it gets
   the space a form gets rather than sitting in the list. */
.whoform.byid{margin-top:10px;padding-top:10px;border-top:1px solid #232b36}
.jump{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 12px;font-size:12px}
.jump a{color:#8b94a3;text-decoration:none;border-bottom:1px dotted #303845}
/* The map name in the running table is a link, and with no rule of its own it
   rendered as the browser default: blue, underlined, and purple once visited -
   which on a dark page reads as a bug, and gives a table cell a colour that
   changes meaninglessly depending on where somebody has clicked before. */
.maplink{color:#e6e9ef;text-decoration:none;border-bottom:1px dotted #3d4757}
.maplink:visited{color:#e6e9ef}
.maplink:hover{border-bottom-color:#8b94a3}
.jump a:hover{color:#e6e9ef}
/* Four pages became four sections and nothing on the page said so: eight
   fieldsets in a row, no heading between them, and Mods reading as the tail of
   Restore. The rule is the seam; the heading says which section you are in and
   matches the jump link that brings you here. */
.area{border-top:1px solid #232b36;margin-top:26px;padding-top:6px}
.area:first-of-type{border-top:0;margin-top:0}
.areah{margin:0 0 10px;font-size:13px;letter-spacing:.4px;text-transform:uppercase;color:#8b94a3;font-weight:600}
.whoform.byid input{width:260px;flex:1 1 200px}
/* Three actions, three weights. Talking to somebody and removing them should not
   be the same button, and Kick sitting beside Ban in identical grey is the
   adjacency worth designing against before Ban exists. Same three-colour rule the
   version panel and the banners keep: neutral, amber, red. */
.whoact{font-size:12px;padding:4px 10px;border-radius:6px;cursor:pointer;
  border:1px solid #303845;background:#12151a;color:#a9b4c4}
.whoact:hover{border-color:#3d4757}
.whoact.talk{}
.whoact.bite{border-color:#7d642f;color:#ffc46b}
.whoact.bite:hover{border-color:#a1812f;background:#1b1710}
.whoact.worst{border-color:#7d2f2f;color:#ff9d94}
.whoact.worst:hover{border-color:#a13a3a;background:#1d1214}
/* Three states, three colours. "Could not check" is deliberately not green and not
   quiet - the failure this panel answers was a checker that said "up to date" about a
   question it never asked, and an unknown that looks like a pass repeats it. */
.versions{width:100%;border-collapse:collapse}
.versions td{padding:6px 8px;border-bottom:1px solid #232b36;vertical-align:top}
.current{color:#7fd18f;font-size:12px}
.newer{color:#ffc46b;font-size:12px;font-weight:600}
.unknown{color:#c9a0ff;font-size:12px;font-weight:600}
.dash{display:flex;flex-direction:column;gap:10px}
.panel{background:#161b22;border:1px solid #262d38;border-radius:9px;padding:11px 13px}
.ptitle{color:#8b94a3;font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px}
.stepper{display:flex;flex-wrap:wrap;gap:4px}
.st{flex:1 1 90px;min-width:90px;font-size:11px;color:#5b6472;text-align:center}
.st span{display:block;height:4px;border-radius:2px;background:#2a3140;margin-bottom:5px}
.st.done{color:#7fd18f}
.st.done span{background:#2f7d45}
.st.now{color:#7fb2ff;font-weight:700}
.st.now span{background:#2f6feb;animation:pulse 1.6s ease-in-out infinite}
.st.bad{color:#ff9d94;font-weight:700}
.st.bad span{background:#e5534b}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.badge{display:inline-block;border-radius:999px;padding:4px 12px;font-size:12px;font-weight:600}
.badge.good{background:#16351f;color:#7fd18f;border:1px solid #2f7d45}
.badge.bad{background:#38191b;color:#ff9d94;border:1px solid #7d2f2f}
.badge.new{background:#3a2f14;color:#ffc46b;border:1px solid #7d642f}
.badge.unk{background:#2b2440;color:#c9a0ff;border:1px solid #4d3f7d}
td.points{line-height:2.4}
table.allpoints{margin:8px 0 2px;max-width:520px}
table.allpoints td{padding:3px 10px}
details summary{color:#8b94a3;font-size:12px;cursor:pointer;margin-top:6px}
td.points button{margin:0 6px 6px 0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.chip{font-size:11px;border-radius:6px;padding:3px 8px;border:1px solid #303845;background:#12151a;color:#a9b4c4;white-space:nowrap}
.chip b{font-weight:600;color:#e6e9ef}
.chip.ok{border-color:#2f7d45;color:#7fd18f}
.chip.bad{border-color:#7d2f2f;color:#ff9d94}
.chip.new{border-color:#7d642f;color:#ffc46b}
.chip.unk{border-color:#4d3f7d;color:#c9a0ff}
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
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  position:sticky;top:var(--topH,86px);z-index:20;background:#12151a;
  padding:10px 0;margin:0 0 10px;border-bottom:1px solid #20262f}
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


class _Unset(object):
    """No pending value, which is not the same as a pending value of ""."""


_UNSET = _Unset()


# Measured rather than assumed: the header is a title plus a row of tabs, and both wrap
# on a narrow screen. A hardcoded offset would tuck the toolbar under the tabs on a
# phone, which is exactly where scrolling back up hurts most.
STICKY_JS = """
<script>
(function(){
  const top = document.querySelector('header.top');
  if (!top) return;
  const set = () => document.documentElement.style.setProperty(
    '--topH', (top.offsetHeight - 24) + 'px');
  set();
  addEventListener('resize', set);
})();
</script>
"""


def _e(v):
    return html.escape("" if v is None else str(v), quote=True)


def page(title, body, nav_on=""):
    # Cluster is the front door. Status used to be the first tab and rendered the same
    # running-maps table this one does - the same function, the same data, two pages -
    # so it was two places to look for one answer and two places for that answer to
    # disagree with itself.
    # Backups, Cloud, Restore and Mods were four tabs for one subject: the copies of
    # this cluster and what loads into it. Three of them are one story told in order,
    # and telling it took three tabs - with the archive being restored listed on one
    # page and the thing that wrote it on another.
    tabs = [("/admin/cluster", "Cluster"), ("/admin", "Settings"),
            ("/admin/data", "Data"), ("/admin/activity", "Activity")]
    nav = "".join('<a href="%s"%s>%s</a>' % (h, ' class=on' if h == nav_on else "", _e(t))
                  for h, t in tabs)
    return ("<!doctype html><html><head><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><div class=wrap>"
            "<header class=top><h1>%s</h1><nav>%s</nav></header>%s"
            "<div class=foot>Obelisk</div></div>%s</body></html>"
            % (_e(title), CSS, _e(title), nav, body, STICKY_JS))


def _field(s, value, locked, pending_value=_UNSET):
    """One control, from the schema.

    A locked field is disabled rather than merely readonly, because readonly still
    submits. Posting back a value the server refuses to accept from here failed the
    entire save - so changing a port meant nothing saved at all, with an error naming
    two fields the user had not touched.
    """
    t, key = s["type"], s["key"]
    ro = " readonly disabled" if locked else ""
    # Show what was asked for, not what is running. Rendering the live value here would
    # mean typing 250, saving, and being shown 70 again - which reads as the save having
    # failed. The badge below says the difference is deliberate and when it lands.
    waiting = not isinstance(pending_value, _Unset)
    if waiting:
        value = pending_value
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
    # What a change actually disturbs, rather than one word for three very different
    # amounts of disruption. "Needs recreate" on the staging server's RAM told an
    # operator that a harmless change would restart ten servers with players on them.
    scope = s.get("scope", "none")
    if scope == "maps":
        tags += ('<span class=tag title="waits for an empty cluster or the update '
                 'window, then restarts every map once">restarts the cluster</span>')
    elif scope == "staging":
        tags += ('<span class=tag title="restarts only the staging server, which has '
                 'no players on it">staging only</span>')
    elif scope == "obelisk":
        tags += ('<span class=tag title="applied from the Unraid Docker page - Obelisk '
                 'cannot replace its own container mid-flight">needs Obelisk restarted'
                 '</span>')
    if waiting:
        # The field above shows the value that was asked for rather than the one that is
        # running, because showing the running value after a save reads as the save
        # having failed. This one word is what keeps that from being a lie.
        tags += '<span class=tag>pending</span>' 
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



def render_map_overrides(store, map_key, queued=None, clears=()):
    """What this one map does differently, and what it simply inherits.

    Blank means inherited, and the cluster's value is in the placeholder so there is no
    guessing what blank resolves to. Typing a value makes an override; clearing it takes
    the override away rather than setting the field to nothing - which is the difference
    between "this map is the same as the others" and "this map has no players allowed".

    It lives on the map's own page now. As one table per map on the Settings page it was
    ten tables of twelve rows attached to a form about the cluster, and the operator had
    to find their map in a stack of them; here there is one map and it is the page they
    are already on.

    `queued` is what has been asked for but is waiting for a restart, and `clears` the
    overrides waiting to be taken away. Both are shown, because an edit that queues and
    an edit that did nothing look identical otherwise.
    """
    from .schema import SETTINGS
    from . import maps as mapcat
    per_map = [s for s in SETTINGS if s.get("per_map")]
    if not mapcat.known(store, map_key):
        return ""
    chosen = [mapcat.entry(store, map_key)]
    queued = queued or {}
    clears = set(clears or ())

    blocks, total = [], 0
    for m in chosen:
        here = store.data.get("maps", {}).get(m["key"], {}) or {}
        rows, n = [], 0
        for s in per_map:
            key = s["key"]
            overridden = key in here
            if overridden:
                n += 1
            cluster = store.get(key)
            shown = here.get(key, "") if overridden else ""
            # What was asked for beats what is stored: a queued edit that rendered the
            # old value would read as a save that did not happen.
            waiting = key in queued
            clearing = key in clears
            if waiting:
                shown, overridden = queued[key], True
            elif clearing:
                shown, overridden = "", False
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
        blocks.append('<table class=permap>%s</table>' % "".join(rows))

    waiting_note = ""
    if queued or clears:
        waiting_note = ('<div class=warn>%d change%s saved and waiting for this map to '
                        'restart. It is running now, so the world it is in does not '
                        'change under the people on it.</div>'
                        % (len(queued) + len(clears),
                           "" if (len(queued) + len(clears)) == 1 else "s"))
    return ('<fieldset id=overrides><legend>Settings for this map</legend>%s'
            '<div class=help style="margin:0 0 14px">Blank inherits the cluster value, '
            'shown in each box - clearing a box takes the override away rather than '
            'setting the field to nothing. Only these %d settings can differ per map: '
            'everything that reaches the game through Game.ini or GameUserSettings.ini '
            'is shared, because the server image links every map to one copy of those '
            'files.</div>'
            '<form method=post action="/admin/save">'
            '<input type=hidden name=back value="/admin/cluster/map/%s">'
            '%s<div style="margin-top:12px">'
            '<button type=submit class=ghost>Save this map\u2019s settings</button> '
            '<span class=help>%d override%s set</span></div></form></fieldset>'
            % (waiting_note, len(per_map), _e(map_key), "".join(blocks), total,
               "" if total == 1 else "s"))



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


def render_ark_update(store, status, ready=None, job=None, owns=True,
                      staging_on=True, target=""):
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
        # A staged tree proves the combination it was staged against. If a mod has
        # published since, what is on disk is still verified and still worth applying -
        # but it is no longer the newest thing, and saying "verified" without saying
        # that would be the same quiet half-truth this panel exists to avoid.
        stale = ""
        if target and ready.get("target") and ready["target"] != target:
            stale = ('<div class=help><b>Something newer has appeared since this was '
                     'staged.</b> This is still proved and still safe to apply; the '
                     'staging server is rehearsing the newer one now.</div>')
        staged = ('<div class=staged><b>Staged and verified.</b> Build <code>%s</code> '
                  'with %d mod%s booted cleanly on the staging server — '
                  '<b>verified by staging boot at %s</b>.<div class=help>%s</div>%s</div>'
                  % (_e(ready.get("build")), len(loaded),
                     "" if len(loaded) == 1 else "s", _e(when),
                     _e(", ".join("%s→%s" % (m, f) for m, f in sorted(loaded.items()))),
                     stale))
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


# The phases a prime actually goes through, in order, with the words the flow already
# emits. Taken from a real run's phase stream rather than invented - the same lines that
# went to the channel become the highlighted step here, so the two cannot describe
# different journeys.
PRIME_PHASES = [
    ("Starting", ("starting the staging server",)),
    ("Downloading", ("Downloading server files",)),
    ("Installing", ("Checking existing files", "Reserving disk space",
                    "Finishing the install", "Server files installed")),
    ("Loading world", ("Starting the world", "Generating the world",
                       "Preparing the runtime")),
    ("Validating", ("mods loaded", "the staging server is serving",
                    "checking what it proved")),
    ("Done", ("done", "stopping the staging server")),
]

# phase_index takes the LAST phase whose marker appears in the step text, which is what
# lets a rollback overrule the forward phase whose words it necessarily contains.
APPLY_PHASES = [
    # The skip line is here too: an empty cluster is not warned, and a step that matches
    # nothing scores -1 and greys the whole bar.
    ("Warning players", ("warning players", "warning is skipped")),
    # A save refusal belongs to the save, not to whatever comes after it. It used to be
    # caught by a bare "refused:" marker further down and reported three phases late.
    ("Saving", ("saving every world", "did not finish saving")),
    ("Stopping", ("stopping the cluster",)),
    # Its own phase because it is minutes long and can end the apply - and the piecemeal
    # restart that follows a refusal belongs to it rather than to Starting.
    ("Checking worlds", ("checking every world is readable", "has no usable world",
                         "that is fine", "that are fine")),
    ("Swapping files", ("swapping the staged files",)),
    # Committing the queued settings is one of the two things an apply exists to do, and
    # it matched nothing - so the bar went blank in the middle of the job.
    ("Applying settings", ("setting change(s)",)),
    ("Starting", ("starting the cluster",)),
    # Undoing is not progress. All three rollback steps contain "starting the cluster",
    # so they used to land on Starting and an apply that was putting everything back
    # rendered exactly like one that was succeeding. Being later in the list is what
    # makes them win.
    ("Putting it back", ("back on the previous", "back as it was")),
    ("Verifying", ("checking every map",)),
    ("Done", ("done",)),
]


# The stop, as the stop actually describes itself. Matched on the words cluster.stop
# emits rather than a parallel enumeration somebody has to remember to update - the
# same rule APPLY_PHASES learned the hard way, where a reworded step left the bar on
# no phase at all and the apply read as having gone backwards.
#
# Three stages, because a stop has three: ask every map to close its own world, stop
# the servers, done. The per-map "3 of 10" lives in the step text under the bar, which
# is where a count belongs - it is not a phase of its own.
STOP_PHASES = [
    ("Requested", ("starting", "stop requested")),
    ("Closing worlds", ("asked to save and close", "could not be asked to close",
                        "saved its world and closed")),
    ("Stopping servers", ("stopping the servers now",)),
    ("Stopped", ("cluster stopped",)),
]


def phase_index(step, phases):
    """Which step of the journey a status line belongs to. -1 when nothing matches.

    Matched on the words the flow emits rather than a parallel enumeration it has to be
    kept in step with - a stepper that drifts from the thing it describes is worse than
    no stepper, because it looks authoritative.
    """
    text = str(step or "").lower()
    best = -1
    for i, (_label, markers) in enumerate(phases):
        if any(m.lower() in text for m in markers):
            best = i
    return best


def render_stepper(phases, step, elapsed=None, failed=False):
    """The journey, with where we are on it and how long it has taken."""
    here = phase_index(step, phases)
    cells = []
    for i, (label, _markers) in enumerate(phases):
        if failed and i == here:
            cls = "st bad"
        elif i < here:
            cls = "st done"
        elif i == here:
            cls = "st now"
        else:
            cls = "st"
        cells.append('<div class="%s"><span></span>%s</div>' % (cls, _e(label)))
    timer = ""
    if elapsed:
        timer = ('<div class=help style="margin-top:4px">%s elapsed</div>'
                 % _e(_duration(elapsed)))
    return '<div class=stepper>%s</div>%s' % ("".join(cells), timer)


def _duration(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm %02ds" % (seconds // 60, seconds % 60)
    return "%dh %02dm" % (seconds // 3600, (seconds % 3600) // 60)


def render_mod_chips(rows=None, loaded=None, expected=None):
    """Every mod as a chip: what it is, what version, and whether it is proved.

    A grid rather than a list because the question people actually ask is "is anything
    wrong", and that is answered by a colour at a glance. The version is on the chip so
    the answer to "wrong how" is there too without a second click.
    """
    chips = []
    if loaded is not None:
        for mod_id in sorted(expected or loaded, key=lambda x: int(x)):
            file_id = (loaded or {}).get(mod_id)
            if file_id:
                chips.append('<span class="chip ok" title="loaded from file %s">'
                             '✓ %s <b>%s</b></span>'
                             % (_e(file_id), _e(mod_id), _e(file_id)))
            else:
                chips.append('<span class="chip bad" title="never loaded">'
                             '✕ %s <b>did not load</b></span>' % _e(mod_id))
    else:
        for row in rows or []:
            if row.get("newer") is None:
                cls, mark, tail = "chip unk", "?", "unknown"
            elif row.get("newer"):
                cls, mark, tail = "chip new", "⬆", "%s available" % (row.get("latest") or "")
            else:
                cls, mark, tail = "chip ok", "✓", "current"
            chips.append('<span class="%s" title="%s">%s %s <b>%s</b></span>'
                         % (cls, _e(row.get("name") or row["id"]), mark,
                            _e(row.get("name") or row["id"])[:24], _e(tail)))
    return '<div class=chips>%s</div>' % "".join(chips) if chips else ""


def render_relay(info):
    """10/10 reachable, in green, or which ones are not, in red."""
    if not info:
        return ""
    total = int(info.get("total") or 0)
    good = int(info.get("reachable") or 0)
    if not total:
        return ""
    ok = good == total
    return ('<div class="badge %s">Chat relay %d/%d maps reachable</div>%s'
            % ("good" if ok else "bad", good, total,
               ('<div class=help>Cannot reach: %s</div>' % _e(info.get("unreachable"))
                if not ok and info.get("unreachable") else "")))


def render_dashboard(status=None, ready=None, failed=None, job=None, relay=None,
                     backup=None, restore=None):
    """What Obelisk is doing right now, as a picture rather than a paragraph.

    The channel gets the same phases as text; this is the at-a-glance version. Both are
    driven by the same job state and the same announcements, so neither can be showing a
    stage the other has not reached.
    """
    blocks = []

    if (job or {}).get("state") == "running":
        phases = APPLY_PHASES if job.get("what") == "apply" else PRIME_PHASES
        title = "Applying an update" if job.get("what") == "apply" else "Priming an update"
        blocks.append('<div class=panel><div class=ptitle>%s</div>%s</div>'
                      % (_e(title),
                         render_stepper(phases, job.get("step"),
                                        elapsed=job.get("elapsed"))))
    for name, other, phases in (("Backup", backup, None), ("Restore", restore, None)):
        if (other or {}).get("state") != "running":
            continue
        step = other.get("step") or other.get("phase") or "working"
        pct = other.get("percent")
        if pct is None and other.get("total"):
            pct = round(100.0 * (other.get("done") or 0) / other["total"], 1)
        bar = ('<div class=evbar><div style="width:%s%%"></div></div>' % _e(pct)
               if pct is not None else "")
        blocks.append('<div class=panel><div class=ptitle>%s in progress</div>'
                      '<div>%s</div>%s</div>' % (_e(name), _e(step), bar))

    if ready:
        when = time.strftime("%H:%M", time.localtime(ready.get("when") or 0))
        loaded = ready.get("loaded") or {}
        blocks.append('<div class=panel><div class="badge good">Primed &amp; verified '
                      '%s &middot; build %s</div>%s</div>'
                      % (_e(when), _e(ready.get("build")),
                         render_mod_chips(loaded=loaded)))
    elif failed:
        problems = failed.get("problems") or []
        blocks.append('<div class=panel><div class="badge bad">Unsafe &mdash; %s</div>'
                      '%s</div>'
                      % (_e(problems[0] if problems else "the rehearsal did not pass"),
                         render_mod_chips(loaded=failed.get("loaded") or {},
                                          expected=failed.get("expected"))))

    if status and status.get("mods"):
        build = status.get("build") or {}
        if build.get("newer") is None:
            cls, word = "unk", "build %s &middot; could not check" % (
                build.get("running") or "?")
        elif build.get("newer"):
            cls, word = "new", "build %s &rarr; %s available" % (
                build.get("running"), build.get("latest"))
        else:
            cls, word = "good", "build %s &middot; current" % build.get("running")
        blocks.append('<div class=panel><div class="badge %s">%s</div>%s</div>'
                      % (cls, word, render_mod_chips(rows=status["mods"])))

    relay_html = render_relay(relay)
    if relay_html:
        blocks.append('<div class=panel>%s</div>' % relay_html)

    if not blocks:
        return ""
    return ('<fieldset><legend>Right now</legend><div class=dash>%s</div></fieldset>'
            % "".join(blocks))


def render_pending(rows, job=None, players=None, primed=None):
    """What is waiting, and the two things you can do about it.

    One panel, one sentence, one row per change. The useful facts are how many are
    waiting, what each one changes, and that they all land in a single restart - not a
    workflow.
    """
    if not rows and not primed:
        return ""

    lines = []
    for row in rows:
        where = _e(row["map"]) + " only" if row["map"] else "all maps"
        lines.append(
            '<tr><td>%s <span class=help>%s</span></td>'
            '<td><code>%s</code> &rarr; <code>%s</code></td>'
            '<td class=num><button class=ghost type=submit name=drop value="%s">'
            'discard</button></td></tr>'
            % (_e(row["label"]), where, _e(row["from"]), _e(row["to"]),
               _e("%s|%s" % (row["map"], row["key"]))))

    if primed:
        lines.append('<tr><td>ARK build <span class=help>staged &amp; verified</span>'
                     '</td><td><code>%s</code> &rarr; <code>%s</code></td>'
                     '<td class=num></td></tr>'
                     % (_e(primed.get("running") or "current"),
                        _e(primed.get("build"))))

    n = len(rows) + (1 if primed else 0)
    head = ('<div class=note><b>%d change%s pending.</b> They apply together in one '
            'restart, when the cluster is empty or at the next scheduled restart.</div>'
            % (n, "" if n == 1 else "s"))

    if (job or {}).get("state") == "running":
        buttons = ('<div class=note>Applying now: %s</div>'
                   % _e(job.get("step") or "starting"))
    else:
        total, counts, silent = players or (0, {}, [])
        note = ""
        if silent:
            note = ('<div class=problem>%d map(s) did not answer, so it is not known '
                    'whether anyone is on them.</div>' % len(silent))
        elif total:
            note = ('<div class=problem>%d player(s) are online - applying now would '
                    'restart their servers.</div>' % total)
        buttons = (note +
                   '<button type=submit name=apply value=1>Apply now</button> '
                   '<button class=ghost type=submit name=discard value=all>'
                   'Discard all</button>'
                   '<label class=inline><input type=checkbox name=force value=1> '
                   'apply even with players online</label>')

    return ('<form method=post action="/admin/pending">'
            '<fieldset><legend>Waiting to be applied</legend>%s'
            '<table>%s</table><div style="margin-top:14px">%s</div>'
            '</fieldset></form>' % (head, "".join(lines), buttons))


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


# The feed's icons are the channel's icons. Kept as a name here because the renderer
# reads it, but there is one dict and it lives with the announcements.
from .announce import ICONS as _EVENT_ICONS


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
        if (d.dash !== undefined){
          const dash = document.querySelector('.dash');
          if (dash && d.dash){
            const fresh = new DOMParser().parseFromString(d.dash, 'text/html')
                            .querySelector('.dash');
            if (fresh) dash.innerHTML = fresh.innerHTML;
          }
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


# Rendered on the Data page, beside the backups and off-site actions they govern,
# rather than on a settings page two tabs from the button they describe.
DATA_PAGE_KEYS = ("backup_times", "backup_keep", "backup_flush",
                  "cloud_enabled", "cloud_keep")

# The mod list has a real editor - an ordered list with add, remove and reorder, and a
# CurseForge lookup - which used to live on the Data page while this page offered the
# same value as a comma-separated string to type by hand. One value, two editors. The
# editor is on this page now and the raw field is gone; passive_mods and
# custom_server_args stay as ordinary fields beside it, because they are ordinary
# fields.
MODS_EDITOR_KEYS = ("mod_ids", "passive_mods")

# The mod controls are not the settings form. Add, remove and the arrows post the moment
# they are clicked and queue like any other change; everything else on this page waits
# for Save changes at the top. Two behaviours on one page is worth one sentence.
MODS_ACT_NOW = ('<div class=help style="margin:0 0 12px">These act as soon as you click '
                'them \u2014 they do not wait for <b>Save changes</b> at the top of the '
                'page. Like every other change that restarts servers, they queue until '
                'the cluster is empty or the window opens.</div>')


def render_data_settings(store, keys, back, queued=None, legend="", anchor=""):
    """A handful of settings, on the page about the thing they control.

    The same controls the settings page draws and the same form target - this is where
    they are rendered, not a second way to write them. The anchor belongs here too, so
    the sentence above that points at this box and the box itself cannot drift apart.
    """
    from .schema import BY_KEY, INSTALL_KEYS
    queued = queued or {}
    rows = [BY_KEY[k] for k in keys if k in BY_KEY]
    if not rows:
        return ""
    fields = "".join(
        _field(s, store.get(s["key"]), s["key"] in INSTALL_KEYS,
               pending_value=(queued[s["key"]] if s["key"] in queued else _UNSET))
        for s in rows)
    form = ('<form method=post action="/admin/save">'
            '<input type=hidden name=back value="%s">%s'
            '<div style="margin-top:10px">'
            '<button type=submit class=ghost>Save</button></div></form>'
            % (_e(back), fields))
    if not legend:
        return form
    return ('<fieldset%s><legend>%s</legend>%s</fieldset>'
            % ((" id=%s" % _e(anchor)) if anchor else "", _e(legend), form))


def render_settings(store, mods=""):
    """The settings page: 194 of them, so finding one has to be a first-class job.

    A flat list was fine at 52. At 194 it is 75 KB of scrolling, and the answer to
    "where is stack size" became ctrl-F. So: a group index that jumps, groups that
    collapse, a search that filters as you type across names, keys and descriptions,
    and a filter for the ones that differ from the game's defaults - which is how you
    read a cluster somebody else configured, including your own from a year ago.
    """
    # Cluster-level queued changes, so a field shows what was asked for rather than
    # what is still running. Per-map ones live on the overrides table and are shown there.
    from .pending import queued as _queued
    waiting = _queued(store)["cluster"] if hasattr(store, "data") else {}

    blocks, index, total_changed = [], [], 0
    for g in GROUPS:
        # What a backup does and when it happens are one subject, and this page was the
        # other half of it: the schedule here, the button two tabs away. The five keys
        # that govern the archives are rendered beside the actions that make them.
        rows = [s for s in SETTINGS
                if s["group"] == g and s["key"] not in DATA_PAGE_KEYS
                and s["key"] not in MODS_EDITOR_KEYS
                and s["key"] not in CLUSTER_PAGE_KEYS]
        if not rows:
            continue
        gid = "g-" + re.sub(r"[^a-z0-9]+", "-", g.lower()).strip("-")
        changed_here = sum(1 for s in rows
                           if is_changed(s, store.get(s["key"])))
        total_changed += changed_here
        fields = "".join(
            _field(s, store.get(s["key"]), s["key"] in INSTALL_KEYS,
                   pending_value=(waiting[s["key"]] if s["key"] in waiting else _UNSET))
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
    # Where the per-map values went. This page holds the cluster defaults now, and two
    # of them still describe themselves as things a map can differ on - which is true,
    # and was true here until this slice. Without this line there is no route from the
    # page that says "a map can differ" to the page where that is done.
    index.append('<a href="#mods">Mods</a>')
    blocks.append(
        '<fieldset id="g-per-map-moved"><legend>Per-map overrides</legend>'
        '<div class=help>Everything here is the cluster default. A map that needs to '
        'differ - more RAM, a different player cap, its own message of the day - sets '
        'that on its own page: open it from <a href="/admin/cluster#maps">Maps</a> on '
        'the Cluster page.</div></fieldset>')
    index.append('<a href="#g-per-map-moved">Per-map overrides</a>')

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
        '<button type=submit>Save changes</button>'
        '</div>'
        '<div class=index>%s</div>'
        '<div class=help style="margin:-4px 0 14px">%d of these have a documented game '
        'default to compare against, so "changed" is only shown for those. The rest are '
        'not marked either way rather than guessed at.</div>'
        % (len(SETTINGS), total_changed, "".join(index), known))

    # After the form, not inside it: the mod editor posts to its own routes, and a
    # form cannot be nested in another form. It gets an index entry of its own so it is
    # reachable the way every group here is.
    return ('<form method=post action="/admin/save">%s%s%s%s</form>%s'
            % (banner, toolbar, "".join(blocks), SETTINGS_JS,
               ('<section id=mods class=area><h2 class=areah>Mods</h2>%s</section>'
                % mods) if mods else ""))


# The population poll runs once a minute, so anything much older than that is not a
# slightly stale number - it is a poller that has stopped. Said out loud rather than
# left for somebody to work out from a large figure.
STALE_AFTER = 300

# One sentence for the dash, used by the cell's tooltip and by the note under the
# table, and written to cover both ways of getting one: the map did not answer, or
# there is no relay to have asked it. Either way it is "not known", which is the whole
# distinction this column exists to draw.
# The two reasons a population question has no answer, written once. The count and the
# roster are two views of one poll, so they have to give the same reason when it did not
# happen - and a second copy of a sentence is a second copy that drifts.
NO_RELAY_WHY = ("the chat relay is not running, and it is what asks the maps. "
                "Set it up under <b>Discord</b> in Settings.")
NOTHING_ANSWERED_WHY = ("no map answered the last poll, so this is not an empty "
                        "cluster, it is an unanswered question. Check the maps are "
                        "reachable before restarting anything.")


def warn_block(text):
    """A sentence in the amber that means "this did not happen, and nothing broke".

    _cluster_body's refusal slot takes finished markup, because the stop guard builds
    its own block with a button in it. Everything else refusing on that page is one
    sentence, and this is how it gets into the same slot without each caller writing
    its own div.
    """
    return '<div class=warn>%s</div>' % _e(text)


def _unavailable(subject, why):
    """"<subject> are not available - <why>", in the amber that means "not set up"."""
    return '<div class=warn>%s are not available &mdash; %s</div>' % (subject, why)


DASH_MEANS = ("\u2014 means that map's player count is not known: it did not answer "
              "the last poll, or the chat relay that asks is not running.")


def _ago(seconds):
    """"just now" / "45s ago" / "3m ago" / "2h ago" / "1d ago".

    It used to stop at minutes, so an hour read "60m ago" and a day "1500m ago" - a
    number that has to be divided in your head before it means anything, at exactly the
    moment it is telling you something has gone wrong.
    """
    seconds = int(seconds or 0)
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return "%ds ago" % seconds
    if seconds < 3600:
        return "%dm ago" % (seconds // 60)
    if seconds < 86400:
        return "%dh ago" % (seconds // 3600)
    return "%dd ago" % (seconds // 86400)


def render_status(status, players=None, addresses=None, host_known=True):
    """What is actually running. Absent or empty is a normal state, not an error.

    `players` is the relay's cached population: {"by_map": {name: n}, "total": int,
    "age": seconds}, or None when there is no relay to have asked. None renders as a
    dash rather than a zero, because "nobody asked" is not "nobody is playing" - the
    same rule the update gate keeps about a map that did not answer.

    `addresses` is {map name: host:port}. It used to be a table of its own under this
    one - the same ten names down the left of both, which is the duplication the owner
    kept pointing at. A row already says which map it is; the address is another thing
    that is true of that map, so it is a column.

    The count is shown with its age. It comes from a poll that runs once a minute, so
    presenting it bare would be presenting a claim about now that it is not making.
    """
    if not status:
        return ""
    if not status.get("docker_ok"):
        return ('<div class=problem><strong>Docker not connected.</strong> %s</div>'
                % _e(status.get("docker_detail", "")))
    if not status.get("compose_exists"):
        # Nothing, not a note. The page's own readiness line says the cluster is not
        # running and where to start it, immediately above this - two notes a hundred
        # characters apart saying the same thing is what the whole consolidation has
        # been removing.
        return ""
    # Colour follows what the server is doing, not merely whether a process exists. A
    # container that aborts and restarts every few seconds reports "running" the whole
    # time, and showing that in green is a status that lies.
    css = {"ok": "ok", "busy": "", "bad": "bad"}
    seen = (players or {}).get("by_map") or {}
    rows = ""
    unknown_count = False
    for s in status.get("services", []):
        level = s.get("level") or ("ok" if s.get("state") == "running" else "bad")
        says = s.get("says") or s.get("status") or s.get("state") or "?"
        # The map, by the name the operator chose it as. This column was headed "Map"
        # and filled with the compose service name, which is the same id-for-name slip
        # the restore flow had - on the page most people look at first.
        label = s.get("label") or s.get("service") or s.get("name") or "?"
        if players is not None and label in seen:
            cell = '<td class=num>%d</td>' % seen[label]
        else:
            # The dash carries its own explanation, because a dash beside a row Docker
            # still calls "serving" is otherwise indistinguishable from a quiet map.
            # The footnote below is keyed on this, not on scanning the finished markup:
            # the address column has a dash of its own and means something else by it.
            cell = ('<td class=num title="%s">&mdash;</td>' % _e(DASH_MEANS))
            unknown_count = True
        # The name is the way in. It is keyed on the map's own key, which the
        # status row carries because the same lookup that put the name there had it -
        # reversing a display name back into a key is the id-for-name slip this
        # function exists to avoid, and it would be doing it on every row.
        key = s.get("map") or ""
        named = ('<a class=maplink href="/admin/cluster/map/%s">%s</a>'
                 % (_e(key), _e(label)) if key else _e(label))
        # The instance id used to be the fourth column. It is on the map's own page
        # as Container, it is not something anybody acts on from here, and the address
        # is what people actually came to this row for.
        addr = (addresses or {}).get(label, "")
        rows += ("<tr><td>%s</td><td class=%s>%s</td>%s"
                 "<td>%s</td></tr>"
                 % (named, css.get(level, ""), _e(says), cell,
                    ('<code>%s</code>' % _e(addr)) if addr else
                    '<span class=help>&mdash;</span>'))
        if s.get("log_tail"):
            rows += ('<tr><td colspan=4><details><summary class=help>why it is '
                     'failing</summary><pre class=logtail>%s</pre></details></td></tr>'
                     % _e(s["log_tail"]))
    if not rows:
        # Carries the table's id. After a real Stop, compose down removes the
        # containers, so this note - not the table - is what a stopped-but-defined
        # cluster renders, and the jump row and the map page's way back both key on
        # compose_exists and offer #run. Without the id those are two links to nowhere,
        # in the state an operator is in every time they stop the cluster. The note
        # stands in for the table, so it answers to the table's name.
        return ('<div class=note id=run>The cluster is defined but nothing is '
                'running.</div>')
    banner = ""
    bad = [s for s in status.get("services", []) if s.get("level") == "bad"]
    if bad:
        banner = ('<div class=problem><strong>%d map%s failing to start.</strong> This '
                  'is not a slow first start - the server keeps aborting and restarting. '
                  'Open the row below for the reason.</div>'
                  % (len(bad), "" if len(bad) == 1 else "s"))
    # The population, where somebody looking at the cluster will actually see it. It
    # was on the relay's own status page until that page was stood down inside Obelisk
    # on 4 September, and the web UI never picked it up - so the one number an operator
    # checks before restarting anything has been missing ever since.
    # The maps the count actually covers, not the maps that look healthy. They were two
    # different populations - the numerator came from every map that answered and the
    # denominator from every map reporting "ok" - so a cluster with players on an
    # unhealthy map read "5 players online across 0 maps". N and M have to describe the
    # same set of maps or the sentence is not about anything.
    covered = len(seen) if players is not None else 0
    if players is None:
        head = _unavailable("Player counts", NO_RELAY_WHY)
    elif not covered:
        # Nothing answered. The filter that stops one silent map showing a remembered
        # number removes every map when the whole cluster goes quiet - which it does,
        # routinely: the relay loses the docker network, the admin password changes,
        # everything restarts at once. What is left is an empty measurement, and an
        # empty measurement rendered as "0 players online" is the ghost zero again at
        # cluster scale. It is also the worst one: a glance at "0 online, just now" is
        # exactly what precedes restarting maps that were perfectly fine.
        head = _unavailable("Player counts", NOTHING_ANSWERED_WHY)
    else:
        stale = int(players.get("age") or 0) > STALE_AFTER
        head = ('<div class="%s"><b>%d player%s online</b> across %d map%s '
                '<span class=help>&middot; %s%s</span></div>'
                % ("warn" if stale else "note",
                   int(players.get("total") or 0),
                   "" if int(players.get("total") or 0) == 1 else "s",
                   covered, "" if covered == 1 else "s",
                   _e(_ago(players.get("age"))),
                   " &middot; the count refreshes every minute, so this is out of date"
                   if stale else ""))

    # Only when there is a dash to explain. A note about a symbol that is not on the
    # page is noise that teaches people to stop reading the notes.
    dashes = ('<div class=help style="margin-top:10px">%s</div>' % _e(DASH_MEANS)
              if unknown_count else "")

    # The address column replaced the instance id, which is on each map's own page
    # as Container and was never something anybody acted on from here. The two help
    # lines under the table came from the Connect panel this absorbed - the same
    # constants that panel used, and the same ones the map page uses.
    return (banner + head + '<fieldset id=run><legend>Running now</legend><table>'
            '<tr><th>Map</th><th>Doing</th><th class=num>Players</th>'
            '<th>Address</th></tr>%s</table>%s%s%s'
            '<div class=help style="margin-top:10px">A first start downloads about 12 GB '
            'of game files and then generates the world, so it is normally slow. The '
            'phase and elapsed time above are how you tell it is still moving.</div>'
            '</fieldset>'
            % (rows, dashes, IN_GAME_HELP if addresses else "",
               "" if host_known else HOST_UNKNOWN_WHY))


def stop_reason(counts, silent=()):
    """One sentence naming who is on, for the channel. The page gets the block below.

    Shared so the two cannot disagree about who was playing - the complaint that made
    the icon map one map instead of two.
    """
    from .cluster import _and
    rows = sorted((label, n) for label, n in (counts or {}).items() if n)
    total = sum(n for _l, n in rows)
    quiet = sorted(l for l, _w in (silent or []))
    parts = []
    if rows:
        parts.append("%d player%s on %s"
                     % (total, " is" if total == 1 else "s are",
                        _and([l for l, _n in rows])))
    if quiet:
        parts.append("%s did not answer, so it is not known whether anyone is on %s"
                     % (_and(quiet), "it" if len(quiet) == 1 else "them"))
    return ("%s." % "; ".join(parts)) if parts else "somebody may be playing."


def render_stop_warning(counts, silent=()):
    """Who is on, before the cluster is stopped out from under them.

    A stop is reversible - Launch brings it back - so this is amber rather than red,
    the same colour and the same shape as the restore guard's player refusal. What it
    is not is a dialog box: the answer comes back as a page, so it is the server that
    decided, it survives a second tab, and it can be tested without a browser.

    A map that did not answer counts as occupied, for the reason it counts everywhere
    else: "it is not known whether anyone is on it" is not "nobody is on it".
    """
    from .cluster import _and
    rows = sorted((label, n) for label, n in (counts or {}).items() if n)
    total = sum(n for _l, n in rows)
    quiet = sorted(l for l, _w in (silent or []))

    lines = []
    if rows:
        # Per map as well as in total, because "four players are on" is not a decision
        # and "three on Ragnarok, one on The Island" is - one of those is a raid night
        # and the other is somebody parked at a spawn point.
        split = ", ".join("%d on %s" % (n, _e(l)) for l, n in rows)
        lines.append("<b>%d player%s on %s.</b> Stopping now disconnects them.%s"
                     % (total, " is" if total == 1 else "s are",
                        _e(_and([l for l, _n in rows])),
                        (" (%s)" % split) if len(rows) > 1 else ""))
    if quiet:
        lines.append("%s did not answer, so it is not known whether anyone is on %s."
                     % (_e(_and(quiet)), "it" if len(quiet) == 1 else "them"))
    lines.append('Nothing has been stopped. Wait until they are off, or stop anyway.')
    return ('<div class=warn>%s</div>'
            '<form method=post action="/admin/stop" style="margin:-4px 0 14px">'
            '<input type=hidden name=force value="1">'
            '<button class=ghost type=submit>Stop anyway</button></form>'
            % "<br>".join(lines))


def render_stop_panel(job):
    """Where the stop has got to, and - when it is over - how it went.

    The request used to be held open across the whole thing - minutes, on ten maps - so
    the browser showed a dead tab and the operator had no way to tell a stop that was
    working from one that had hung. The stop already described itself stage by stage to
    Discord; this is the same description, on the screen where the button was pressed.

    And it says how it ended, which the first version of this did not. It rendered
    nothing at all for a finished job, so a stop that failed reloaded the page and
    showed no reason anywhere - worse than the synchronous version it replaced, which
    at least put the docker error in a red banner. A stop that worked was no better
    off: "nothing is running" describes a state, it does not confirm an action, and it
    reads exactly like a cluster that was never launched.

    The result comes from `message` rather than `step`. `step` is the running
    commentary and is overwritten with a bare "failed" on the way out; the sentence
    worth reading is the one the stop returned.

    The panel only, with no wrapper of its own - render_stop_job puts it inside the one
    element the poller owns, and the poller replaces what is inside that element. It
    used to render inline *and* be injected, which stacked two identical panels on the
    page for the length of the stop.
    """
    job = job or {}
    state = job.get("state")
    if state == "running":
        inner = ('<div class=panel><div class=ptitle>Stopping the cluster</div>%s'
                 '<div class=help style="margin-top:6px">%s</div></div>'
                 % (render_stepper(STOP_PHASES, job.get("step"),
                                   elapsed=job.get("elapsed")),
                    _e(job.get("step") or "starting")))
    elif state == "done":
        text = job.get("message") or ""
        inner = ('<div class="%s">%s</div>'
                 % ("note" if job.get("ok") else "problem", _e(text))) if text else ""
    else:
        inner = ""
    return inner


def render_stop_job(job):
    """render_stop_panel inside the element the poller replaces the contents of.

    The id is emitted here and nowhere else. It used to be emitted by the panel itself,
    which the poller then dropped *inside* the wrapper - so the running page ended up
    with a #stopwrap nested in a #stopwrap. Harmless in practice, because
    getElementById takes the outer one, and invalid all the same: two elements with one
    id is the kind of thing that stays harmless right up until something queries it.
    """
    return '<div id=stopwrap>%s</div>' % render_stop_panel(job)


# Shaped like RESTORE_JS, and for the same reason: a long job on a page that would
# otherwise say nothing until it finished. The panel's HTML comes back with the poll
# rather than being rebuilt in the browser - the same swap the activity feed already
# does with its dashboard block, so the stepper on screen and the stepper the server
# would render cannot disagree.
#
# Polling stops the moment the job is terminal, and the result it swaps in is the last
# thing it does. The reload after it is what makes the rest of the page true again -
# the map table still says the cluster is up - and it is safe now only because the
# result banner is rendered from sjob server-side: the reload shows the same sentence
# rather than losing it, which is what the first version did.
STOP_JS = """
<script>
(function(){
  const wrap=document.getElementById('stopwrap');
  if(!wrap) return;
  let sawRunning=false;
  function tick(){
    fetch('/admin/cluster/status',{credentials:'same-origin'})
      .then(function(r){return r.json()}).then(function(j){
        wrap.innerHTML=j.html||'';
        if(j.state==='running'){
          sawRunning=true;
          setTimeout(tick,2000);
          return;
        }
        if(sawRunning){ location.reload(); }
      }).catch(function(){setTimeout(tick,5000)});
  }
  tick();
})();
</script>
"""


def render_held_down(maps, states=None):
    """The gate refused these and is holding them down. Say so where the buttons are.

    Launch and "Apply and restart" both bring every map up, this one included, onto the
    exact world that was just refused - one click, no warning, and the protection is
    undone. Nothing here is disabled: the operator may well have restored it already and
    be doing precisely the right thing. They are told, and they decide.

    The plural case is the one that has actually happened - three worlds were damaged in
    a single shutdown on 2026-09-12 - so every verb, noun and pronoun agrees with the
    count rather than being written for one map and left to fend for itself.

    `states` decides the last sentence, and it is the reason this takes them at all. A
    map held down because its storage could not be read must not be told to restore: the
    announcement for that case says in as many words not to, and restoring there would
    swap a healthy world for an older one to fix a mount. A map that has never booted has
    no save point to restore from either.
    """
    from .cluster import _and
    one = len(maps) == 1
    kinds = {(states or {}).get(m) or "" for m in maps}
    storage_only = bool(kinds) and kinds <= {"unreachable"}
    nothing_saved = bool(kinds) and kinds <= {"unreachable", "absent", "unknown"}

    if storage_only:
        advice = ('Their storage could not be read, so this is a mount or permission '
                  'problem rather than a damaged world. Check the ARK volume is mounted '
                  'and readable, then apply again - do not restore anything yet.'
                  if not one else
                  'Its storage could not be read, so this is a mount or permission '
                  'problem rather than a damaged world. Check the ARK volume is mounted '
                  'and readable, then apply again - do not restore anything yet.')
    elif nothing_saved:
        advice = ('Check the ARK volume is mounted and readable before starting %s '
                  'again.' % ("it" if one else "them"))
    else:
        advice = ('Restore %s from a save point first, unless you already have.'
                  % ("it" if one else "them"))

    return ('<div class=problem><b>%s %s still stopped after a refused update.</b> '
            'The %s %s %s could not be read, so %s not started - the files are '
            'being left exactly as they are.'
            '<div class=help style="margin-top:6px">Launch and Apply and restart will '
            'start %s again on %s same %s. %s</div></div>'
            % (_e(_and(maps)), "is" if one else "are",
               "world" if one else "worlds", "it" if one else "they",
               "holds" if one else "hold",
               "it was" if one else "they were",
               "it" if one else "them", "that" if one else "those",
               "world" if one else "worlds", advice))


NAME_UNREADABLE = ("this line of the server's answer could not be read as a name and "
                   "an id, so there is nothing here to act on")


def _message_form(label, name):
    """Say something to one player, from the row they are on.

    The map travels with the name because that is what the command needs: a player is
    on exactly one map, and the message goes to that map's server. Posting the map from
    the page rather than looking it up again also means the server can tell that the
    page was describing a state that has since changed - see the route, which refuses
    rather than guessing.
    """
    return ('<form method=post action="/admin/player/message" class=whoform>'
            '<input type=hidden name=map value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input name=text maxlength=200 placeholder="say something to %s" '
            'autocomplete=off>'
            '<button class="whoact talk" type=submit>Send</button></form>'
            % (_e(label), _e(name), _e(name)))


def _kick_form(label, name, netid):
    """Disconnect one player, from the row they are on.

    The netid travels with them because that is what KickPlayer takes - the same
    platform id ListPlayers hands back, whatever form the player's platform gives it.
    Nothing here parses it or cares.

    One button. The confirmation is a page, not a dialog: see kick_confirm below and
    the stop guard it copies.
    """
    return ('<form method=post action="/admin/player/kick" class=whoform>'
            '<input type=hidden name=map value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input type=hidden name=netid value="%s">'
            '<button class="whoact bite" type=submit>Kick</button></form>'
            % (_e(label), _e(name), _e(netid)))


def _ban_form(label, name, netid):
    """Remove somebody from the whole cluster, from the row they are standing on."""
    return ('<form method=post action="/admin/player/ban" class=whoform>'
            '<input type=hidden name=map value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input type=hidden name=netid value="%s">'
            '<button class="whoact worst" type=submit>Ban</button></form>'
            % (_e(label), _e(name), _e(netid)))


def render_ban_confirm(label, name, netid, problem=""):
    """Ask, and make them type the name.

    The heavy guard, matched to what it does. A kick costs the walk back from a spawn
    point and a click-through is proportionate to that. A ban removes somebody from
    every map in the cluster and stays in ten files until an admin takes it out - so
    the operator types the name, which is the only guard that also defends against
    doing it to the wrong person off a list of ten.

    Asked on the row, like the kick, so there is no live Ban still sitting underneath
    an unanswered question.
    """
    warn = ('<span class=whoflag style="color:#ff9d94">%s</span>' % _e(problem)
            if problem else "")
    return ('<span class=whoflag><b>Ban %s from the whole cluster?</b> They are removed '
            'from every map, not just %s, and stay out until somebody unbans them. '
            'Nothing they built is deleted. It is recorded in Banned players '
            'below and can be undone from there. Nothing has been done yet.'
            '</span>%s'
            '<form method=post action="/admin/player/ban" class=whoform>'
            '<input type=hidden name=map value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input type=hidden name=netid value="%s">'
            '<input name=confirm autocomplete=off placeholder="type %s to confirm">'
            '<button class="whoact worst" type=submit>Ban %s</button> '
            '<a class=help href="/admin/cluster#who">Cancel</a></form>'
            % (_e(name), _e(label), warn, _e(label), _e(name), _e(netid),
               _e(name), _e(name)))


def render_kick_confirm(label, name, netid):
    """Ask before disconnecting somebody, on the row they are standing on.

    The answer comes back as a page rather than from a dialog, the way the stop guard
    works: it is the server that decided, it survives a second tab, and it can be
    tested without a browser.

    Asked in place, which is the part that took two goes. Rendered at the top of the
    page it bounced the operator away from the row they were reading, and left that row
    below still offering a live Kick - two routes to the same act, one of them
    unconfirmed. Here it replaces that row's buttons instead.

    Lighter than the restore guard on purpose. A kick costs somebody the walk back from
    the spawn point; it does not cost them anything they built. Making the operator
    type a name for this and for a ban both would teach them to type it without
    reading, which is what the ban guard is for.
    """
    return ('<span class=whoflag><b>Kick %s from %s?</b> They are disconnected now and '
            'can rejoin immediately - nothing they own is affected. Nothing has been '
            'done yet.</span>'
            '<form method=post action="/admin/player/kick" class=whoform>'
            '<input type=hidden name=map value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input type=hidden name=netid value="%s">'
            '<input type=hidden name=confirm value="1">'
            '<button class="whoact bite" type=submit>Yes, kick %s</button> '
            '<a class=help href="/admin/cluster#who">Cancel</a></form>'
            % (_e(name), _e(label), _e(label), _e(name), _e(netid), _e(name)))


def render_whos_online(roster, maps=(), pending=None, notice=None):
    """Who is on, one row per player, under the map they are on.

    **No header.** The count section six lines above already says how many people are
    on how many maps and how old the answer is, from the same poll - restating it here
    was the duplication this whole overhaul exists to remove, and in the stale case it
    was two near-identical amber warnings one after the other.

    `maps` is the running maps in the order the status table lists them, so the eye can
    travel down the two without re-sorting. It is also what makes a map that did not
    answer visible here: the roster simply has no entry for it, and a map that silently
    disappears from a list of who is online is the "we cannot tell who is on Ragnarok"
    signal going missing exactly where somebody is about to press Stop.

    One row per player rather than a line of chips, because every one of these rows
    carries Message and Kick and will carry Ban. Thirty-odd controls wrapped inside one
    table cell is not a thing to build and then fix.

    `pending` is a player this page is currently asking about - their row shows the
    question instead of its buttons, so there is never a second unconfirmed route to
    the same act sitting live underneath it. `notice` is what the last action here
    said, shown in this section rather than at the top of the page, because this is
    where the operator is looking and where the answer changes something.
    """
    if roster is None or not (roster.get("by_map") or {}):
        # Slice 1 has already printed the full explanation immediately above - why
        # there is no count is exactly why there are no names. Saying all thirty words
        # again eight lines later is how people learn to skip both.
        return ('<fieldset id=who><legend>Who\u2019s online</legend>%s'
                '<div class=help>No names to show \u2014 for the same reason there is '
                'no player count above.</div></fieldset>' % (notice or ""))

    by_map = roster.get("by_map") or {}
    order = [m for m in (maps or []) if m]
    order += [m for m in sorted(by_map) if m not in order]

    # Three states, and only two of them are worth a line each. A map with players on
    # it is where an action goes; a map that did not answer is what somebody needs to
    # know before pressing Stop. A map that answered and is empty is neither - and on
    # ten maps it was nine headings and nine "nobody on it" lines burying the two that
    # matter, restating the 0 the count column already shows. So the empty ones become
    # one line.
    out, empty = [], []
    for label in order:
        if label not in by_map:
            # Named in the row itself rather than under a heading of its own: this is
            # the state a blackout produces on every map at once, and twenty lines of
            # it is how the signal gets lost a second time.
            out.append('<div class="whorow quiet"><span class=whoname>%s</span>'
                       '<span class=whoflag>did not answer the last poll, so who is on '
                       'it is not known</span></div>' % _e(label))
            continue
        people = by_map[label] or []
        if not people:
            empty.append(label)
            continue
        out.append('<div class=whomap>%s</div>' % _e(label))
        for row in people:
            name = _e(row.get("name") or "?")
            if ((pending or {}).get("netid")
                    and pending.get("netid") == (row.get("netid") or "")
                    and pending.get("map") == label):
                out.append('<div class="whorow asking">'
                           '<span class=whoname>%s</span>%s</div>'
                           % (name, pending.get("html") or ""))
                continue
            from .bot import can_whisper
            netid = row.get("netid") or ""
            if netid and can_whisper(row.get("name")):
                flag = ""
                acts = (_message_form(label, row.get("name") or "")
                        + _kick_form(label, row.get("name") or "", netid)
                        + _ban_form(label, row.get("name") or "", netid))
            elif netid:
                # No message box - the name cannot go inside a quoted argument - but
                # kicking and banning key on the id, which has no such problem. The row
                # keeps the actions it can actually take, and drops only the one it
                # cannot.
                acts = (_kick_form(label, row.get("name") or "", netid)
                        + _ban_form(label, row.get("name") or "", netid))
                # A name with a quote or a line break in it cannot be put inside a
                # ServerChatToPlayer line - see can_whisper. The row is real and the
                # player is real; the message box is the thing that cannot work.
                flag = ('<span class=whoflag title="%s">cannot be messaged &mdash; the '
                        'name has a quote or a line break in it</span>'
                        % _e("ServerChatToPlayer puts the name in quotes and the "
                            "console has no way to escape one inside them"))
            else:
                # The consequence, in text, not only in a tooltip - a title attribute
                # is invisible on a touch screen, which is where half of this gets read.
                #
                # And no form. The "name" on one of these rows is a whole line the
                # parser could not split, so sending a message to it would address
                # nobody - a button that cannot work is worse than no button.
                flag = ('<span class=whoflag title="%s">no id &mdash; nothing to act '
                        'on</span>' % _e(NAME_UNREADABLE))
                acts = ""
            out.append('<div class=whorow><span class=whoname>%s</span>%s'
                       '<span class=whoacts>%s</span></div>' % (name, flag, acts))

    if empty:
        # In the order the table above lists them, like everything else here.
        out.append('<div class=whonote>Nobody on: %s <span class=help>(%d map%s)'
                   '</span></div>'
                   % (_e(", ".join(empty)), len(empty), "" if len(empty) == 1 else "s"))

    return ('<fieldset id=who><legend>Who\u2019s online</legend>%s'
            '<div class=whoroster>%s</div></fieldset>'
            % (notice or "", "".join(out)))


# What these lists are records OF, said once for both of them.
#
# There are two on this page - the bans and the cap - and they rest on exactly the same
# fact: ARK has no RCON command that reads either list back, and the files sit on ten
# filesystems this container cannot open. So what can honestly be shown is what this
# manager sent, which is a different claim from what the servers hold.
#
# One constant, consumed by both, for the reason NO_RELAY_WHY is one constant: the
# second copy of a caveat is the copy that drifts, and two drifted halves of one caveat
# are two different claims about the same thing. Each section supplies only its own
# subject.
SENT_NOT_READ = ("\u2014 a record of what was sent, and not a read of each "
                 "server\u2019s own list, which nothing here can see")

BANS_ARE = "bans issued from Obelisk " + SENT_NOT_READ


def _when_title(when):
    """The exact time, for the hover, since "3d ago" is not a thing to act on."""
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(when or 0)))
    except Exception:                                # noqa: BLE001 - never a blank page
        return ""


def _spread(entry, past=False):
    """How far a ban actually got, in the words the ban banner used.

    Past tense for a ban somebody has already undone. "Sent to all 10 maps" beside
    "unbanned 8 days ago" reads as a statement about now, and the row it is on is the
    row where "is this person banned?" has to be answerable at a glance.
    """
    maps = entry.get("maps") or {}
    took, gone = bansctl.sent_to(entry), bansctl.missed(entry)
    if not maps:
        return "no maps recorded"
    if not gone:
        return ("had reached all %d maps" if past else "sent to all %d maps") % len(maps)
    if not took:
        return (("had reached none of the %d maps" if past
                 else "sent to none of the %d maps") % len(maps))
    return (("had reached %d of %d \u2014 missed %s" if past
             else "%d of %d \u2014 missing %s")
            % (len(took), len(maps), ", ".join(l for l, _w in gone)))


def _unban_form(entry):
    return ('<form method=post action="/admin/player/unban" class=whoform>'
            '<input type=hidden name=netid value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input type=hidden name=when value="%s">'
            '<button class="whoact talk" type=submit>Unban</button></form>'
            % (_e(entry.get("netid") or ""), _e(entry.get("name") or ""),
               _e(str(entry.get("when") or 0))))


def render_unban_confirm(name, netid, when="", problem=""):
    """Ask once, and let them press it.

    A click, not a typed name. The ban guard makes somebody write the name out because
    a ban is hard to notice and lasts until an admin takes it out; an unban lets one
    person back in and any of its mistakes are one press to correct. Asking for the
    same ceremony either way is how a typed confirmation turns into something people
    type without reading.
    """
    who = _e(name) if name else "this id"
    warn = ('<span class=whoflag style="color:#ff9d94">%s</span>' % _e(problem)
            if problem else "")
    return ('<span class=whoflag><b>Unban %s?</b> The ban is lifted on every map and '
            'they can join again. The record below is kept and marked unbanned, not '
            'removed. Nothing has been done yet.</span>%s'
            '<form method=post action="/admin/player/unban" class=whoform>'
            '<input type=hidden name=netid value="%s">'
            '<input type=hidden name=name value="%s">'
            '<input type=hidden name=when value="%s">'
            '<input type=hidden name=confirm value="1">'
            '<span class=whoflag>%s</span>'
            '<button class="whoact talk" type=submit>Yes, unban</button> '
            '<a class=help href="/admin/cluster#bans">Cancel</a></form>'
            % (who, warn, _e(netid), _e(name or ""), _e(when or ""), _e(netid)))


def _unban_by_id_form():
    """For the bans this manager never issued.

    Somebody banned in-game or by hand has no row here - there is nothing to read the
    server's list from - so the only way back in through this page is to type the id.
    """
    return ('<form method=post action="/admin/player/unban" class="whoform byid">'
            '<label class=help for=unbanid>Unban an id this list does not have '
            '— for bans made in-game or by hand:</label>'
            '<input id=unbanid name=netid autocomplete=off '
            'placeholder="platform id (Steam, Epic or EOS)">'
            '<button class="whoact talk" type=submit>Unban by ID</button></form>')


def render_bans(entries, notice=None, pending=None, now=None,
                total=None):
    """What Obelisk banned, newest first, and the way to undo it.

    Its own section rather than a column of the roster above: these are the people who
    are *not* there, and the two lists answer opposite questions.

    `pending` is an unban this page is currently asking about, keyed on the id. Every
    row for that id drops its button while the question stands - including rows the
    question is not rendered on, because one press undoes them all and a second live
    Unban beside an unanswered one is two ways to do the same thing.
    """
    now = time.time() if now is None else now
    held = list(entries or [])
    asking = str((pending or {}).get("netid") or "")
    out = []
    for entry in held:
        netid = str(entry.get("netid") or "")
        name = _e(entry.get("name") or "?")
        when = entry.get("when") or 0
        mine = asking and netid == asking
        here = mine and str(pending.get("when") or "") == str(when)
        gone = bansctl.is_unbanned(entry)
        if here:
            out.append('<div class="whorow asking"><span class=whoname>%s</span>%s</div>'
                       % (name, pending.get("html") or ""))
            continue
        # An undone ban is a different kind of row, so it is a different-looking row.
        # Both states rendered identically meant "is this person banned right now?"
        # was a question about the last cell of every line - and the same name and id
        # can legitimately appear twice, once undone and once live, rows apart. The
        # dashed, dimmed vocabulary is the one who's-online already uses for a line
        # that is not live.
        if gone:
            act = ""
            state = ("unbanned %s \u00b7 that ban %s"
                     % (_ago(now - int(entry.get("unbanned") or 0)),
                        _spread(entry, past=True)))
        else:
            act = "" if mine else _unban_form(entry)
            state = _spread(entry)
        out.append('<div class="whorow%s"><span class=whoname>%s</span>'
                   '<span class=whoflag title="%s">%s</span>'
                   '<span class=whoflag title="%s">%s</span>'
                   '<span class=whoflag>%s</span>'
                   '<span class=whoacts>%s</span></div>'
                   % (" quiet" if gone else "", name,
                      _e(netid), _e(_shorten_id(netid)),
                      _e(_when_title(when)), _e(_ago(now - int(when))),
                      _e(state), act))
    if not held:
        # Not a warning. An empty ban list is the state this cluster is in most of the
        # time, and a manager that paints it amber teaches people to ignore amber.
        out.append('<div class=whonote>No bans recorded.</div>')
    # The list stops at a page's worth, and said nothing about it: a manager holding 59
    # records showed 50 and looked complete, so "was this person ever banned?" had an
    # answer the page was quietly hiding. The cap stays - the alternative is a section
    # that grows without limit - but it says so.
    if total is not None and int(total) > len(held):
        out.append('<div class=whonote>Showing %d of %d — older bans are kept '
                   'but not listed.</div>' % (len(held), int(total)))
    # A question about a typed id replaces the field it was typed into, for the same
    # reason a question about a row replaces that row's buttons.
    if asking and not str((pending or {}).get("when") or ""):
        byid = ('<div class="whorow asking"><span class=whoname>Unban by ID</span>%s'
                '</div>' % (pending.get("html") or ""))
    else:
        byid = _unban_by_id_form()
    return ('<fieldset id=bans><legend>Banned players</legend>%s'
            '<div class=help>%s</div>'
            '<div class=whoroster>%s</div>%s</fieldset>'
            % (notice or "", BANS_ARE, "".join(out), byid))


def _shorten_id(netid):
    """Enough of an id to tell two apart, with the whole of it on hover.

    A 32-character EOS id in a row that also holds a name, a time and ten map names
    pushes everything else off a phone screen, and nobody reads it character by
    character anyway - they compare it to one they were given.
    """
    netid = str(netid or "")
    return netid if len(netid) <= 20 else netid[:10] + "\u2026" + netid[-6:]


# Two sentences, because the name of this thing is the part people get wrong. The first
# says what it does; the second says what the list under it is. Neither of them is the
# word "whitelist" - to an ARK admin that is the file deciding who may connect at all,
# and this only exempts somebody from MaxPlayers.
CAP_DOES = ("Lets one id join even when the server is full. It is not the join "
            "allow-list \u2014 it does not decide who may connect, only who may "
            "connect to a full server.")
# The provenance half is the shared sentence above - the same words under both lists,
# because it is the same fact. Only the subject is this section's own.
CAP_ARE = "allows issued from Obelisk " + SENT_NOT_READ


def _cap_form(entry):
    return ('<form method=post action="/admin/player/cap" class=whoform>'
            '<input type=hidden name=netid value="%s">'
            '<input type=hidden name=when value="%s">'
            '<input type=hidden name=action value="revoke">'
            '<button class="whoact talk" type=submit>Revoke</button></form>'
            % (_e(entry.get("netid") or ""), _e(str(entry.get("when") or 0))))


def render_cap_confirm(netid, action="allow", when="", problem=""):
    """Ask once, and let them press it.

    A click either way. Letting somebody past the cap costs a seat on a full server and
    taking it away costs them nothing they own, so neither direction earns the typed
    name the ban guard asks for - and a confirmation people type without reading is
    worth less than one they read.
    """
    warn = ('<span class=whoflag style="color:#ff9d94">%s</span>' % _e(problem)
            if problem else "")
    if action == "revoke":
        head = ('<b>Stop letting %s past the player cap?</b> They can still join when '
                'there is room. The record below is kept and marked revoked, not '
                'removed. Nothing has been done yet.' % _e(netid))
        go = "Yes, revoke"
    else:
        head = ('<b>Let %s past the player cap?</b> They will be able to join every '
                'map even when it is full. This is not the join allow-list. Nothing '
                'has been done yet.' % _e(netid))
        go = "Yes, let them past"
    return ('<span class=whoflag>%s</span>%s'
            '<form method=post action="/admin/player/cap" class=whoform>'
            '<input type=hidden name=netid value="%s">'
            '<input type=hidden name=when value="%s">'
            '<input type=hidden name=action value="%s">'
            '<input type=hidden name=confirm value="1">'
            '<button class="whoact talk" type=submit>%s</button> '
            '<a class=help href="/admin/cluster#cap">Cancel</a></form>'
            % (head, warn, _e(netid), _e(when or ""),
               "revoke" if action == "revoke" else "allow", go))


def _cap_by_id_form():
    return ('<form method=post action="/admin/player/cap" class="whoform byid">'
            '<label class=help for=capid>Let an id past the cap '
            '\u2014 they can join a full server:</label>'
            '<input id=capid name=netid autocomplete=off '
            'placeholder="platform id (Steam, Epic or EOS)">'
            '<input type=hidden name=action value="allow">'
            '<button class="whoact talk" type=submit>Allow by ID</button></form>')


def render_cap(entries, notice=None, pending=None, now=None, total=None):
    """What Obelisk let past the player cap, newest first, and how to take it back.

    Built the same way as the bans list and for the same reason: the two sections
    answer the same kind of question about the same kind of key, and an operator who
    has read one should not have to learn the other. The rows carry no name - somebody
    who is not online has none to carry, and that is exactly who this is used on.
    """
    now = time.time() if now is None else now
    held = list(entries or [])
    asking = str((pending or {}).get("netid") or "")
    out = []
    for entry in held:
        netid = str(entry.get("netid") or "")
        when = entry.get("when") or 0
        mine = asking and netid == asking
        here = mine and str(pending.get("when") or "") == str(when)
        gone = capctl.is_revoked(entry)
        if here:
            out.append('<div class="whorow asking"><span class=whoname>%s</span>%s'
                       '</div>' % (_e(_shorten_id(netid)), pending.get("html") or ""))
            continue
        if gone:
            act = ""
            state = ("revoked %s \u00b7 that allow %s"
                     % (_ago(now - int(entry.get("revoked") or 0)),
                        _spread(entry, past=True)))
        else:
            act = "" if mine else _cap_form(entry)
            state = _spread(entry)
        out.append('<div class="whorow%s"><span class=whoname title="%s">%s</span>'
                   '<span class=whoflag title="%s">%s</span>'
                   '<span class=whoflag>%s</span>'
                   '<span class=whoacts>%s</span></div>'
                   % (" quiet" if gone else "", _e(netid), _e(_shorten_id(netid)),
                      _e(_when_title(when)), _e(_ago(now - int(when))),
                      _e(state), act))
    if not held:
        out.append('<div class=whonote>Nobody has been let past the cap from '
                   'here.</div>')
    if total is not None and int(total) > len(held):
        out.append('<div class=whonote>Showing %d of %d \u2014 older events are kept '
                   'but not listed.</div>' % (len(held), int(total)))
    if asking and not str((pending or {}).get("when") or ""):
        byid = ('<div class="whorow asking"><span class=whoname>Allow by ID</span>%s'
                '</div>' % (pending.get("html") or ""))
    else:
        byid = _cap_by_id_form()
    return ('<fieldset id=cap><legend>Let past the player cap</legend>%s'
            '<div class=help>%s %s</div>'
            '<div class=whoroster>%s</div>%s</fieldset>'
            % (notice or "", CAP_DOES, CAP_ARE, "".join(out), byid))


# Ten fieldsets and twenty-odd thousand characters of page. The section anchors were
# already there for the redirects to land on; this is the only thing that offers them to
# the person reading, which costs one line and saves a scroll through the roster to
# reach the maps.
# Two orders, because the page draws two: a cluster that has never been launched
# has no running-maps table to point at, and its maps form comes first. A chip that
# scrolls nowhere is worse than one that is missing.
# "Addresses" rather than "Connect", and it points at the running table, because that
# is where the addresses are now. A cluster that has never been launched has neither.
JUMPS = (("#run", "Running"), ("#who", "Players"), ("#bans", "Bans"),
         ("#cap", "Cap"), ("#maps", "Maps"))
JUMPS_FRESH = (("#maps", "Maps"), ("#who", "Players"), ("#bans", "Bans"),
               ("#cap", "Cap"))


def render_area(anchor, title, body):
    """One section of a page that used to be several pages.

    The anchor is what the jump row and every redirect land on; the heading is what
    tells somebody they have landed, and it is the same word the jump row used - a link
    saying "Off-site" that arrives at a box headed "Connect a cloud" leaves the reader
    to work out whether they went to the right place.
    """
    return ('<section id=%s class=area><h2 class=areah>%s</h2>%s</section>'
            % (_e(anchor), _e(title), body))


def render_jump_row(items):
    """A row of links to the sections of a long page.

    Shared, because the Data page has the same problem the cluster page has: several
    fieldsets, a screen and a half of scrolling, and anchors that existed only for
    redirects to land on.
    """
    return ('<div class=jump>%s</div>'
            % " ".join('<a href="%s">%s</a>' % (_e(h), _e(t)) for h, t in items))


def render_jump(launched=True):
    return render_jump_row(JUMPS if launched else JUMPS_FRESH)


def render_map(name, key, row=None, address="", host_known=True, points=None,
               job=None, state=None, overrides="", notice="",
               launched=True):
    """One map, in detail, for the things that are only true of that map.

    The overview answers "is it up, who is on, is anything broken" for a cluster. Ports,
    RAM, why that RAM, the role, the address people type and the saves the game took are
    none of those things: they are a paragraph per map, and ten of them made two
    full-width tables that pushed the answers off the top of the page.

    Detail only, on purpose. Whether this map is up and how many are on it is the
    overview's row to state, and stating it here too would be a second copy that can
    disagree with the first - which is the duplication this whole consolidation is for.
    """
    # Back to something that is actually on that page. #run is the running-maps
    # table, which a cluster that has never been launched does not have.
    back_to = "/admin/cluster#run" if launched else "/admin/cluster#maps"
    if not row:
        return ('<div class=jump><a href="%s">Back to the cluster</a></div>'
                '<div class=warn>%s is not in this cluster\u2019s plan. Tick it under '
                '<b>Maps</b> to add it.</div>' % (back_to, _e(name)))
    facts = (("Game port", str(row.get("game_port") or "")),
             ("RCON port", str(row.get("rcon_port") or "")),
             ("RAM", "%s \u2014 %s" % (row.get("memory") or "",
                                       row.get("memory_why") or "")),
             ("Role", str(row.get("role") or "")),
             ("Container", str(row.get("instance") or "")))
    table = "".join("<tr><td>%s</td><td>%s</td></tr>" % (_e(k), _e(v))
                    for k, v in facts)
    here = ('<fieldset id=detail><legend>%s</legend><table>%s</table>'
            '<div class=help style="margin-top:10px">These are what this map is given '
            'when the cluster is applied. Changing them is the <b>Maps</b> and '
            '<b>Settings</b> pages\u2019 job.</div></fieldset>'
            % (_e(name), table))
    # A label and a value, like the facts above it. This was a two-column table with
    # "Map" and "Address" headers and one row in it - the shape it had when it listed
    # ten maps on the overview, kept after the cut. The page is already about one map;
    # a column repeating its name is a header with nothing to distinguish.
    connect = ('<fieldset id=connect><legend>Connect</legend>'
               '<table><tr><td>Address</td><td><code>%s</code></td></tr></table>'
               '%s%s</fieldset>'
               % (_e(address), IN_GAME_HELP,
                  "" if host_known else HOST_UNKNOWN_WHY)) if address else ""
    saves = render_savepoints([(name, list(points or []))], job=job)
    if not saves:
        saves = ('<fieldset><legend>Quick restore points</legend>'
                 '<div class=note>No dated saves for %s yet. ARK writes one every '
                 '15 minutes once the map has been running.</div></fieldset>'
                 % _e(name))
    # The boxes themselves now, not a link to a page that had ten maps' worth of them
    # stacked in one collapsed block. Still one writer: this form posts the same
    # map:<key>:<setting> names to the same /admin/save that the settings page uses.
    # Orientation, not a second reading. Which state this map is in is a fact the
    # operator needs to have landed in the right place - what it is doing right now,
    # and how many are on it, stay on the overview where one poll answers for every
    # map. A page that could not tell a serving map from one that has never started
    # was the same page either way, and an operator clicking a row that says "Online"
    # arrived somewhere that did not confirm it.
    if state and state.get("says"):
        where = ('<div class=help>Docker says <b>%s</b> for this map. '
                 '<a class=maplink href="%s">Running now</a> on the cluster page is '
                 'what keeps that up to date.</div>' % (_e(state["says"]), back_to))
    elif launched:
        where = ('<div class=help>This map is not running. '
                 '<a class=maplink href="%s">Running now</a> on the cluster page shows '
                 'what is.</div>' % back_to)
    else:
        where = ('<div class=help>This cluster has never been launched. '
                 '<a class=maplink href="%s">Maps</a> on the cluster page is where it '
                 'starts.</div>' % back_to)
    return ('<div class=jump><a href="%s">Back to the cluster</a></div>' % back_to
            + (notice or "") + here + where + connect + saves + (overrides or ""))


# Maps belong to the cluster, not to the settings form: this page is what the cluster
# is. The text field there and the checkboxes here were one value with two editors, and
# neither could express the thing that matters most about it - the order.
CLUSTER_PAGE_KEYS = ("maps",)

# Why a running cluster cannot be reordered, said once so the disabled button and the
# route that refuses the post cannot describe it differently.
#
# Every other change in this manager queues: it is written down and applied the next
# time the cluster is recreated, which is safe because the value means the same thing
# before and after. An order does not. Ports are handed out walking the list, so moving
# an entry moves the address people have in their launcher, and moving the first entry
# changes which map downloads the server files for the rest. Queuing that would mean
# agreeing to it now and discovering it at the restart.
REORDER_RUNNING = ("Stop the cluster to reorder maps \u2014 reordering moves every "
                   "map\u2019s ports and changes which map downloads the server files "
                   "for the others. Nothing has been changed.")
REMOVE_RUNNING = ("Stop the cluster to remove %s \u2014 every map after it moves down "
                  "a port. The last map in the list can be removed while running, and "
                  "adding to the end is always safe. Nothing has been changed.")
PRESET_RUNNING = ("Stop the cluster to apply a preset \u2014 it rewrites the whole "
                  "list, which moves ports and can change which map downloads the "
                  "server files. Nothing has been changed.")

# A cluster with no maps is not a state this manager has - validation refuses the empty
# list - so removing the only map has no good outcome to offer. Said once, as the
# button's reason for being disabled and as the route's refusal, so the two cannot
# describe the same rule differently.
def maps_unknown(keys):
    """The amber for a map list naming something the catalogue cannot explain.

    A store edited by hand, or restored from a backup taken while the cluster had a map
    of its own that has since been forgotten. The list is left exactly as it is: the
    answer is to put the map back in the catalogue or take the name out of the list,
    and guessing which - by dropping the name - would move every port after it.
    """
    keys = list(keys)
    return ("This cluster's map list names %s, which %s a map Obelisk knows or one "
            "this cluster has added. Nothing will start until %s put back in the "
            "catalogue or taken out of the list. Nothing has been changed."
            % (", ".join(keys), "isn’t" if len(keys) == 1 else "aren’t",
               "it is" if len(keys) == 1 else "they are"))


ONLY_MAP_WHY = "A cluster needs at least one map"

# What the on-disk look can and cannot tell the operator. It is a stat, not a check: a
# folder that is not there is the normal state of a map that has never launched, and a
# folder that is there proves the name is spelled the way the game spells it - which is
# the one thing about a map id Obelisk genuinely cannot work out on its own.
NO_WORLD_YET = ("no saved world under this map id yet — normal for a map that has "
                "not launched")
WORLD_THERE = "a saved world is already on disk under this map id"
MAP_ID_ADVICE = ("The map id is the level name the server expects, and the folder its "
                 "world is saved in — <code>Ragnarok_WP</code>, "
                 "<code>Svartalfheim_WP</code>. Obelisk cannot tell a wrong one from a "
                 "right one: a map id nothing uses yet simply makes a new, empty world "
                 "under that name. Take it from the mod's own page.")
ONLY_MAP = ONLY_MAP_WHY + " \u2014 the list is unchanged."


def render_maps_editor(store, running=False, refusal="", message="", values=None,
                       saves=None):
    """The maps this cluster runs, in the order it runs them.

    Four parts of one editor. The list, because order is a fact about this cluster that
    nothing else could show: the first map is the update master and ports are handed out
    walking down it. The catalogue, to add from, appending to the end - the one edit that
    leaves every existing port alone. Presets, which are a bulk tick of the boxes and
    nothing more. And the maps this cluster defined for itself, which is the one part
    that writes to the catalogue rather than reading from it.

    A set of checkboxes cannot say any of that. It was sorted by whatever order the
    catalogue happens to be in, and the value it posted was that order.

    `values` is what the operator typed into the add form when it was refused - handed
    back so a refusal is a correction rather than a retype. `saves` is {map_id: bool}
    from a look at the disk, which is advice and never a gate: see NO_WORLD_YET.
    """
    keys = mapcat.listed(store.get("maps"))
    # This cluster's catalogue, not Obelisk's: a map the operator added is a map
    # this editor lists, links to and offers, like any other.
    cat = mapcat.catalogue(store)
    warn = ""
    if running and keys:
        # Said here rather than in a help string on another page. This is the moment
        # somebody is about to change a cluster people are playing on.
        warn = ('<div class=warn>This cluster is running. You can <b>add</b> a map - it '
                'goes on the end, where it moves nobody else\u2019s ports - and that is '
                'queued until the maps are next recreated. Reordering and removing are '
                'not offered until the cluster is stopped, because both move the ports '
                'people already have and can change which map downloads the server '
                'files. %s</div>'
                % ('The last map in the list is the exception: nothing comes after it, '
                   'so it can go.' if len(keys) > 1 else
                   '%s, so this one stays until you add another.' % ONLY_MAP_WHY))

    rows = []
    for i, key in enumerate(keys):
        name = (cat.get(key) or {}).get("name") or key
        first = i == 0
        last = i == len(keys) - 1
        only = len(keys) == 1
        # Disabled while the cluster runs, and refused by the route as well. The button
        # is the explanation; the route is the guard, because a disabled button is a
        # suggestion to anything that is not a browser. Removing the only map is
        # disabled in both states - see ONLY_MAP.
        stuck = " disabled" if running else ""
        rows.append(
            '<tr><td class=num>%d</td><td>%s%s</td>'
            '<td class=num>%s %s %s</td></tr>'
            % (i + 1,
               '<a class=maplink href="/admin/cluster/map/%s">%s</a>'
               % (_e(key), _e(name)) if key in cat else _e(name),
               ' <span class="tag chg">update master</span>' if first else "",
               '<button class=ghost type=submit name=up value="%s"%s%s>\u2191</button>'
               % (_e(key), " disabled" if first else "", stuck),
               '<button class=ghost type=submit name=down value="%s"%s%s>\u2193</button>'
               % (_e(key), " disabled" if last else "", stuck),
               '<button class=ghost type=submit name=drop value="%s"%s%s>remove'
               '</button>'
               % (_e(key),
                  ' title="%s"' % _e(ONLY_MAP_WHY) if only else "",
                  " disabled" if only or (running and not last) else "")))
    if not rows:
        rows.append('<tr><td colspan=3 class=help>No maps chosen yet. Add one '
                    'below.</td></tr>')

    chosen = set(keys)
    # A map this cluster defined is offered beside the ones Obelisk ships, and says
    # which it is: the operator typed its level name, so if it turns out to be wrong,
    # knowing which maps are theirs is where they would start.
    catalogue = "".join(
        '<button class=ghost type=submit name=add value="%s"%s title="%s">%s%s</button>'
        % (_e(m["key"]), " disabled" if m["key"] in chosen else "",
           _e("already in this cluster" if m["key"] in chosen else
              "add %s to the end" % m["name"]), _e(m["name"]),
           ' <span class=tag>custom</span>' if m.get("custom") else "")
        for m in mapcat.ordered(store))
    presets = "".join(
        '<button class=ghost type=submit name=preset value="%s" title="%s"%s>%s</button>'
        % (_e(p["key"]), _e(p["description"]), " disabled" if running else "",
           _e(p["name"])) for p in PRESETS)

    # The two forms post to two different routes - the list, and the catalogue behind
    # it - so they cannot be one form, and a form cannot be nested in another. The
    # fieldset holds both, because to the operator this is one place.
    return ('<fieldset id=maps><legend>Maps</legend>%s%s%s'
            '<form method=post action="/admin/maps">'
            '<table><tr><th class=num>#</th><th>Map</th>'
            '<th class=num>Order</th></tr>%s</table>'
            '<div class=help style="margin-top:10px">The first map is the update '
            'master: it downloads the server files once and the others wait for it '
            'rather than all fetching the same thing at once. Ports are assigned down '
            'this list, so adding to the end leaves everyone else where they are and '
            'reordering does not.</div>'
            '<div class=help style="margin:14px 0 6px">Add a map</div>'
            '<div class=maps>%s</div>'
            '<div class=help style="margin:14px 0 6px">Or start from a preset '
            '\u2014 it rewrites the whole list and carries no settings of its '
            'own.%s</div>'
            '<div class=presets>%s</div>'
            '</form>%s'
            '</fieldset>'
            % (warn_block(refusal) if refusal else "",
               ('<div class=note>%s</div>' % _e(message)) if message else "", warn,
               "".join(rows), catalogue,
               " That needs the cluster stopped." if running else "", presets,
               # Open after a result of either kind. A refusal that hides the form it
               # is about cannot be acted on - and a success that folds the section
               # shut hides the row it just made, along with the note saying whether a
               # world already exists under that map id. That note is the only evidence
               # the operator has that they typed the level name correctly, and the
               # moment they have just typed it is when it is worth reading.
               _own_maps(store, values=values, saves=saves, listed=keys,
                         opened=bool(refusal or values or message))))


def _own_maps(store, values=None, saves=None, listed=(), opened=False):
    """Maps this cluster defined for itself: what they are, and how to add one.

    Folded away by default. Most clusters run the ten maps Obelisk ships and never open
    this; the ones that need it need three fields and the sentence about what Obelisk
    cannot check for them. It springs open on a refusal, because a refusal that hides
    the form it is about is a refusal nobody can act on.
    """
    values = values or {}
    saves = saves or {}
    listed = set(listed or ())

    rows = ""
    for m in mapcat.ordered(store):
        if not m.get("custom"):
            continue
        here = m["key"] in listed
        # Advice, never a gate. A folder that is not there is the ordinary state of a
        # map nobody has launched yet; a folder that is there is the one confirmation
        # available that the level name is spelled the way the game spells it.
        seen = saves.get(m["map_id"])
        note = "" if seen is None else (
            '<div class=help>%s</div>' % _e(WORLD_THERE if seen else NO_WORLD_YET))
        rows += ('<tr><td>%s</td><td><code>%s</code></td><td><code>%s</code>%s%s</td>'
                 '<td class=num>%s</td></tr>'
                 % (_e(m["name"]), _e(m["key"]), _e(m["map_id"]),
                    ('<div class=help>mod %s</div>' % _e(m["mod_id"]))
                    if m.get("mod_id") else "", note,
                    '<button class=ghost type=submit name=forget value="%s"%s%s>'
                    'forget</button>'
                    % (_e(m["key"]),
                       ' title="%s is in the map list above"' % _e(m["name"])
                       if here else "", " disabled" if here else "")))
    if rows:
        rows = ('<table><tr><th>Map</th><th>Key</th><th>Map id</th>'
                '<th class=num></th></tr>%s</table>'
                '<div class=help>Forgetting a map removes nothing: its world stays on '
                'disk and its settings stay in this cluster, so defining it again '
                'finds both. A map named in the list above cannot be forgotten '
                '\u2014 take it out of the list first.</div>' % rows)
    else:
        rows = ('<div class=help>None yet. Everything in the list above is a map '
                'Obelisk ships with.</div>')

    def field(name, label, hint, place):
        return ('<div style="margin:8px 0"><label class=block>%s</label>'
                '<input name=%s value="%s" placeholder="%s" autocomplete=off>'
                '<div class=help>%s</div></div>'
                % (_e(label), name, _e(str(values.get(name) or "")), _e(place), hint))

    # The table goes INSIDE the form, not above it. A submit button belongs to the form
    # it sits in, and one that sits in no form belongs to nothing: it renders, it looks
    # live, and clicking it sends no request at all. The forget buttons were between the
    # list form's close and this form's open, so the one case forgetting is meant to
    # work - a map this cluster defined and is not running - could not be reached from
    # the page, while the route that refuses the other two cases was perfectly correct.
    #
    # Both actions in one form is fine: the route reads `forget` and answers it before
    # it looks at the add fields, so forgetting a map does not care that the three boxes
    # above it are empty.
    return ('<details id=ownmaps%s style="margin-top:16px">'
            '<summary>Maps this cluster added</summary>'
            '<div class=help style="margin:8px 0">A map Obelisk does not ship with '
            '\u2014 a mod map, or an official one released since this build. Defining '
            'it here only teaches Obelisk how to spell it; it is added to the cluster '
            'from the list above, like any other map.</div>'
            '<form method=post action="/admin/maps/catalogue">'
            '%s'
            '<div class=help style="margin:14px 0 6px"><b>Define a map</b></div>'
            '%s%s%s'
            '<button type=submit name=define value=1>Add this map</button>'
            '</form></details>'
            % (" open" if opened else "", rows,
               field("key", "Key", 'Lowercase letters and digits, 3 to 24 of them. It '
                     'becomes this map\u2019s container name, its folder and its web '
                     'address here, and it cannot be changed later.', "svartalfheim"),
               field("map_id", "Map id", MAP_ID_ADVICE, "Svartalfheim_WP"),
               field("name", "Name", 'What players see in the server browser, and what '
                     'this manager calls it. Up to %d characters.' % mapcat.NAME_MAX,
                     "Svartalfheim")))


def render_cluster(store, plan, status=None, roster=None, web_address="",
                   maps_editor="", pending=None, notice=None, bans=None,
                   bans_pending=None, bans_notice=None,
                   bans_total=None,
                   caps=None, caps_pending=None,
                   caps_notice=None, caps_total=None):
    # Ports, RAM and role left this page for each map's own. What stays is the way
    # in, which is also the only entry point before anything has been launched: there
    # is no running-maps row to click on a cluster that has never started.
    chosen_links = ""
    if plan["maps"]:
        chosen_links = ('<div class=jump style="margin-top:10px">%s</div>'
                        % " ".join('<a href="/admin/cluster/map/%s">%s</a>'
                                   % (_e(r["map"]), _e(r["name"]))
                                   for r in plan["maps"]))

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
    # This sentence already named the port Obelisk answers on, and the Connect panel
    # printed the whole URL a screen below it - the same fact twice on one page. The
    # sentence carries it now, and the panel is gone.
    summary = ("%d map%s, %s of RAM at most, plus Obelisk itself at %s."
               % (len(plan["maps"]), "" if len(plan["maps"]) == 1 else "s",
                  plan["total_memory"], web_address)
               if web_address else
               "%d map%s, %s of RAM at most, plus Obelisk on port %s."
               % (len(plan["maps"]), "" if len(plan["maps"]) == 1 else "s",
                  plan["total_memory"], plan["obelisk_port"]))

    # The same sequence the status table renders, so the two read down together.
    running = [x.get("label") or x.get("service") or ""
               for x in ((status or {}).get("services") or [])]
    # Who is on, what is banned, who is let past - and the form that defines the
    # cluster. Which comes first depends on whether there IS a cluster: on a machine
    # that has never launched one, the three moderation sections are three empty boxes
    # about servers that do not exist, sitting above the only controls that would
    # create them.
    moderation = (
            render_whos_online(roster, maps=[m for m in running if m],
                               pending=pending, notice=notice) +
            render_bans(bans, notice=bans_notice, pending=bans_pending,
                        total=bans_total) +
            render_cap(caps, notice=caps_notice, pending=caps_pending,
                       total=caps_total))
    # The maps editor is its own form, above this one: it posts one action at a time
    # (add this, move that) the way the mod list does, and a form cannot nest. What is
    # left here is the plan the choice produces and the buttons that act on it.
    form = (maps_editor
            + '<form method=post action="/admin/launch" onsubmit="for(const b of this.querySelectorAll(&quot;button&quot;)){b.disabled=true}this.querySelectorAll(&quot;button&quot;)[0].textContent=&quot;Working...&quot;">'
            '<fieldset><legend>Plan</legend>%s'
            '<div class=help style="margin-top:10px">%s</div>%s'
            '<div style="margin-top:14px">%s</div></fieldset></form>'
            % (chosen_links, _e(summary), msgs, launch))
    launched = bool((status or {}).get("compose_exists"))
    return (moderation + form) if launched else (form + moderation)


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


def render_mods(store, health=None, found=None, problem="", known=None,
                refusal=""):
    """The mod list, in load order, with what is actually on disk beside each one.

    Order is the first thing people get wrong and install health is the second, so both
    are on the same screen: a mod can be listed, present, and still not installed.

    `refusal` is an edit the list would not accept - a mod already on it, a move that
    would put a stacking mod second. Amber, because nothing happened: the list is
    already what was asked for, or the rule says it cannot be.
    """
    ids = modlib.parse(store.get("mod_ids"))
    health = health or {}
    # What the update check already learned about each mod - name and category. Read
    # from what is in hand rather than fetched here, so opening this page never waits
    # on CurseForge.
    known = {str(k): v for k, v in (known or {}).items()}
    rows = []
    for i, m in enumerate(ids):
        h = health.get(m, {})
        about = known.get(m) or {}
        title = _e(about.get("name") or "")
        cats = ", ".join(c for c in (about.get("categories") or []) if c)
        named = ('<div class=help>%s%s</div>'
                 % (title, (" &middot; " + _e(cats)) if cats else "")) if title else ""
        cls, label = STATUS_STYLE.get(h.get("status", ""), ("", "not checked"))
        detail = ("%d files, %s MB" % (h["files"], h["mb"])) if h.get("files") else "-"
        note = ('<div class=help>%s</div>' % _e(h["note"])) if h.get("note") else ""
        first = ' <span class=tag title="loads first, so it wins conflicts">first</span>' if i == 0 else ""
        rows.append(
            "<tr><td class=num>%d</td><td><code>%s</code>%s%s</td>"
            "<td class=%s>%s%s</td><td class=num>%s</td><td class=num>"
            '<button class=ghost name=up value="%s"%s>&uarr;</button> '
            '<button class=ghost name=down value="%s"%s>&darr;</button> '
            '<button class=ghost name=drop value="%s">Remove</button>'
            "%s</td></tr>"
            % (i + 1, _e(m), first, named, cls, _e(label), note, _e(detail),
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

    # The % is applied to the template and nothing else. It used to be applied to the
    # template *concatenated with* _add_a_mod's finished HTML, so a single literal %
    # anywhere in a CurseForge card or a lookup problem - a mod with % in its name, a
    # provider message quoting one - raised out of here. _found is module state that
    # persists, so that did not fail one request: it failed every later render of the
    # page this editor is on, until the manager restarted.
    listed = ('<form method=post action="/admin/mods">%s'
              '<fieldset><legend>Mods, in load order</legend>'
              '<table><tr><th class=num>#</th><th>Mod</th><th>On disk</th>'
              '<th class=num>Size</th><th class=num>Order</th></tr>%s</table>'
              '<div class=help style="margin-top:10px">A mod earlier in the list wins '
              'conflicting changes, which is why stacking mods have to be first. '
              'Reordering takes effect on the next cluster recreate.</div>'
              '</fieldset>'
              '</form>') % (banner, "".join(rows))
    return (warn_block(refusal) if refusal else "") + listed + _add_a_mod(
        store, found, problem)


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
    # The key input lives here, where somebody actually wants it, rather than only in
    # the Advanced group eleven sections down a settings page. The canonical setting is
    # still the schema's - this posts to it - so there is one definition and one place
    # it is stored, and two doors to it.
    if keyed:
        key_box = ('<div class=f><label>CurseForge API key</label>'
                   '<div class=note>A key is set, so search and browse are available. '
                   '<button class=ghost type=submit name=clearkey value=1>Remove it'
                   '</button></div></div>')
    else:
        key_box = ('<div class=f><label>CurseForge API key '
                   '<span class=help>enables search and browse</span></label>'
                   '<input type=password name=apikey autocomplete=off '
                   'placeholder="paste a key from console.curseforge.com"> '
                   '<button type=submit>Save key</button>'
                   '<div class=help>Free from <code>console.curseforge.com</code>. '
                   'Without one you can still add any mod by pasting its Project ID '
                   'above - the key only adds searching from in here. Stored like any '
                   'other password and never written to a log or the Discord channel.'
                   '</div></div>')
    note = ('Paste the mod&rsquo;s CurseForge address or its Project ID. Obelisk looks '
            'it up first, so you can see what you are adding. New mods go last so they '
            'cannot silently outrank something that already works &mdash; and the '
            'staging server fetches and checks it before your cluster ever loads it.')
    if not keyed:
        note += ('<br>Pasting the Project ID is the reliable route: the keyless lookup '
                 'can only resolve a name from the address for mods it happens to have '
                 'seen before.')
    return ('<form method=post action="/admin/mods/find">'
            '<fieldset><legend>Add a mod</legend><div class=f>'
            '<label>CurseForge address or Project ID</label>'
            '<input type=text name=ref placeholder="929110 or '
            'https://www.curseforge.com/ark-survival-ascended/mods/...">'
            ' <button type=submit class=ghost>Look up</button>'
            '<div class=help>%s</div></div>%s</fieldset></form>'
            '<form method=post action="/admin/mods/key">'
            '<fieldset><legend>Searching CurseForge</legend>%s</fieldset></form>'
            % (note, body, key_box))

# The backup runs on a worker thread now, so the page it was started from is free to
# say what it is doing. Same rule as the launch phases: name the step, show how far in,
# and never show a bar that is really just an animation - the percentage comes from
# bytes actually read, and when there is no total to divide by it says so instead.
BACKUP_PROGRESS = """
<div id=bkwrap hidden>
  <div class=note><strong id=bkphase>Backing up</strong> <span id=bkdetail></span></div>
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
                   message="", problem="", job=None, savepoints_by_map=None,
                   refusal=""):
    """Restore one map from one archive, with what is in it shown before committing.

    The order on the page is the order of the decision: which archive, what is in it,
    how it differs from what is running, and only then which map - because "this archive
    is from a different cluster" is something you want to read before choosing a map,
    not after.

    The quick restore points come after, deliberately: they are the commoner answer but
    the smaller one, and putting them first would make the archives look optional.
    """
    points_block = render_savepoints(savepoints_by_map or [], job=job)
    from . import maps as mapcat
    from .backup import human_size
    banner = ""
    if problem:
        banner = '<div class=problem>%s</div>' % _e(problem)
    elif message:
        banner = '<div class=note>%s</div>' % _e(message)

    # A refusal is amber, a failure is red, a success is a note. The channel already
    # tells these apart - restore.refused at warning, restore.failed at error - and
    # this page did not: "type the map's name to confirm" arrived in the same red box
    # as "the world was restored but the map did not start again". One of those means
    # nothing happened and the other means something is half done, and the operator is
    # standing in front of this screen rather than in front of Discord.
    if refusal and not problem:
        banner = '<div class=warn>%s</div>' % _e(refusal)

    job = job or {}
    running = job.get("state") == "running"
    if job.get("state") == "done" and not message and not problem and not refusal:
        refused = bool((job.get("detail") or {}).get("refused"))
        if job.get("ok"):
            message = job.get("message", "")
        elif refused:
            refusal = job.get("message", "")
        else:
            problem = job.get("message", "")
        banner = ('<div class="%s">%s</div>'
                  % ("note" if job.get("ok") else ("warn" if refused else "problem"),
                     _e(job.get("message", ""))))

    if not archives:
        return (banner + '<fieldset><legend>Restore</legend><div class=help>No backups '
                'on disk yet. Make one under <b>Back up</b> above first - there is nothing to '
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
    # This cluster's catalogue. Read from the built-in list, a map the operator added
    # was missing from the picker, from the "you run" sentence and from the archive
    # comparison - so the one map most likely to need a restore was the one map this
    # page would not offer.
    cat = mapcat.catalogue(store)
    usable = [k for k in keys if k in cat
              and (not info or cat[k]["map_id"] in inside)]
    first_name = cat[usable[0]]["name"] if usable else "the map's name"
    # An archive from another cluster has every map greyed out, and the button next to
    # them stayed live with a placeholder that had degraded to the literal words "the
    # map's name". Pressing it answered "type the map's name to confirm" - advice with
    # nothing to do about it. There is no map here to restore, so say that instead.
    nothing_here = bool(info) and not usable
    picks = "".join(
        '<option value="%s"%s>%s%s</option>'
        % (_e(k), "" if (not info or cat[k]["map_id"] in inside) else " disabled",
           _e(cat[k]["name"]),
           "" if (not info or cat[k]["map_id"] in inside) else " - not in this archive")
        for k in keys if k in cat)

    # The archive this will restore, named where the decision is made. The page used
    # to show one archive in the dropdown and carry another in the run form's hidden
    # field - change the select without pressing "Look inside" and the button restored
    # something the screen was not showing, over a live world, with nothing to read.
    taken = (info or {}).get("created") or "unknown date"
    naming = ""
    if nothing_here:
        naming = ('<div class=warn>%s holds no world for any map this cluster runs. '
                  'It has %s; you run %s. There is nothing here to restore - pick a '
                  'different archive.</div>'
                  % (_e(chosen or "That archive"),
                     _e(", ".join((info or {}).get("maps") or []) or "nothing"),
                     _e(", ".join(cat[k]["name"] for k in keys
                                  if k in cat) or "no maps")))
    elif chosen:
        naming = ('<div class=warn><b>This replaces the chosen map’s entire world '
                  'with the one in %s</b> (taken %s). Everything built on that map '
                  'since then is gone from the world - and there is no undo button '
                  'here, so the map’s name is typed rather than clicked.</div>'
                  % (_e(chosen), _e(taken)))

    return (banner +
            '<form method=post action="/admin/restore/inspect">'
            '<fieldset><legend>1. Choose an archive</legend><div class=f>'
            '<select name=archive id=archivepick data-looked="%s">%s</select> '
            '<button type=submit>Look inside</button>'
            '<div class=help>Nothing is changed by looking. The archive is opened and '
            'read, which is also how it is checked.</div></div></fieldset></form>'
            '%s'
            '<form method=post action="/admin/restore/run" id=runform data-busy>'
            '<input type=hidden name=archive value="%s">'
            '<fieldset><legend>2. Restore one map</legend>'
            '%s'
            '<div class=f>'
            '<select name=map id=runmap>%s</select> '
            '<label class=inline>Type the map’s name to confirm</label>'
            '<input name=confirm id=runconfirm autocomplete=off placeholder="%s"> '
            '<label class=inline><input type=checkbox name=force> Restore even if '
            'players are on it</label> '
            '<button type=submit id=runbtn%s>Restore this map</button>'
            '<div class=help>Only the map you pick is stopped; the rest of the cluster '
            'keeps serving. Its current world is copied first, and the world being '
            'replaced is kept on disk afterwards - nothing is deleted. Worlds only: '
            'your mod list, ports and cluster id are not touched.</div>'
            '</div></fieldset></form>'
            '%s%s%s%s'
            % (_e(chosen or ""), opts, detail, _e(chosen or ""), naming, picks,
               _e(first_name),
               " disabled" if (not info or running or nothing_here) else "",
               RESTORE_PICK_JS, RESTORE_JS, points_block, _superseded_block(store)))


def render_savepoints(by_map, job=None, limit=6):
    """The saves the game already took, offered per map as one-click rollbacks.

    The recent few are buttons; *all* of them are behind a fold, because "roll back to
    the oldest point you have" is a real thing to want after a griefing that was not
    noticed for a day, and a count of what you cannot reach is not an offer.

    Nothing stops until the operator confirms. The consequence used to live in a hover
    tooltip, which meant clicking a point stopped a server with no warning anybody had
    read - a tooltip is documentation, not consent.
    """
    if not by_map:
        return ""

    rows = []
    for map_name, points in by_map:
        if not points:
            continue
        quick = "".join(_point_button(map_name, p, ghost=True) for p in points[:limit])
        rest = ""
        if len(points) > limit:
            older = "".join(
                '<tr><td>%s</td><td class=help>%s</td><td class=help>%s</td>'
                '<td class=num>%s</td></tr>'
                % (_e(p["local"]), _e(p["ago"]), _e(p.get("human_size") or ""),
                   _point_button(map_name, p, ghost=True, label="restore"))
                for p in points)
            rest = ('<details><summary>all %d restore points for %s, back to %s'
                    '</summary><table class=allpoints>%s</table></details>'
                    % (len(points), _e(map_name), _e(points[-1]["ago"]), older))
        rows.append('<tr><td>%s</td><td class=points>%s%s</td></tr>'
                    % (_e(map_name), quick, rest))

    if not rows:
        return ""

    busy = (job or {}).get("state") == "running"
    return ('<form method=post action="/admin/restore/point" id=pointform>'
            '<fieldset><legend>Quick restore points</legend>'
            '<div class=note>ARK saves every world every 15 minutes and keeps about '
            '<b>45 hours</b> of dated copies beside the live one. Rolling one map back '
            'to any of them takes a couple of minutes and stops only that map.</div>'
            '%s<table>%s</table>'
            '<div class=help style="margin-top:10px"><b>The world goes back; players do '
            'not.</b> Characters, levels and tribes are not rolled back - anything '
            'gained since the point you pick stays on the player but disappears from '
            'the world.</div>'
            '<div class=help>These live on the same disk as the cluster. They are for '
            'griefing, a bad wipe or a mod that ate something - not for a failed disk. '
            'The archives above are what survive the machine.</div>'
            '<label class=inline><input type=checkbox name=force value=1> restore even '
            'with players on that map</label>'
            '</fieldset></form>%s'
            % ('<div class=note>Working: %s</div>' % _e((job or {}).get("step") or "")
               if busy else "", "".join(rows), POINT_CONFIRM_JS))


def _point_button(map_name, point, ghost=False, label=None):
    """One restore point, carrying the sentence the operator has to agree to."""
    text = CONFIRM_TEXT % (map_name, point["local"])
    return ('<button%s type=submit name=point value="%s|%s" data-confirm="%s">'
            '%s%s</button>'
            % (" class=ghost" if ghost else "", _e(point["map"]), _e(point["name"]),
               _e(text), _e(label or point["ago"]),
               "" if label else '<span class=help> &middot; %s</span>' % _e(point["local"])))


CONFIRM_TEXT = ("This will stop %s, roll its world back to %s, and restart it "
                "(about 3 minutes).\n\n"
                "Player characters and tribes are NOT rolled back - anything gained "
                "since then stays on the player but disappears from the world.\n\n"
                "Continue?")

WARN_TEXT = ("Rolls this map's world back to %s. Player characters and tribes are NOT "
             "rolled back - anything gained since then stays on the player but "
             "disappears from the world.")

# A confirm, not a tooltip. The first version put the consequence in a title attribute,
# so clicking a restore point stopped a live server without anybody having read a word
# of it - a hover is documentation, and this needs consent.
POINT_CONFIRM_JS = """
<script>
(function(){
  const form = document.getElementById('pointform');
  if (!form) return;
  form.addEventListener('submit', function(e){
    const b = e.submitter;
    if (!b || b.name !== 'point') return;
    if (!window.confirm(b.dataset.confirm || 'Roll this map back?')) {
      e.preventDefault();
    }
  });
})();
</script>
"""


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
            'gone. <b>If the restore was not what you wanted, this folder is the way '
            'back:</b> stop that map, rename the folder to the map id with the '
            '<code>.superseded-…</code> part removed, and start it again. There is no '
            'undo button here, so that move is the undo. Once you are happy with the '
            'restore, delete them by hand - Obelisk will not remove a world for '
            'you.</div></fieldset>'
            % "".join('<tr><td><code>%s</code></td><td class=num>%s</td></tr>'
                      % (_e(r["name"]), _e(r["human"])) for r in rows))


# The restore stops a map, swaps a world and then waits minutes for a server to load it.
# A form post that simply hangs for that long is indistinguishable from one that has
# failed - and this is the feature where "I do not know what it is doing" is least
# acceptable, because it is in the middle of touching somebody's saves.
# The browser half of the same guard. The server refuses a mismatch outright - that is
# what makes it true for a second tab - but a button that is still live after the
# dropdown has moved on is an invitation to press it and read a refusal. Same shape as
# the restore-point confirm: one listener, no framework, and it fails safe by doing
# nothing if the elements are not there.
RESTORE_PICK_JS = """
<script>
(function(){
  const pick=document.getElementById('archivepick');
  const btn=document.getElementById('runbtn');
  const form=document.getElementById('runform');
  if(!pick||!btn) return;
  const looked=pick.dataset.looked||'';
  function sync(){
    const moved = pick.value !== looked;
    btn.disabled = moved || btn.dataset.locked === '1';
    let n=document.getElementById('pickwarn');
    if(moved && !n){
      n=document.createElement('div');
      n.id='pickwarn'; n.className='warn';
      n.textContent='That is not the archive that was looked inside. Press "Look '
                  + 'inside" first - nothing is restored from an archive that has '
                  + 'not been opened and checked.';
      (form||pick.parentNode).insertBefore(n,(form||pick).firstChild);
    } else if(!moved && n){ n.remove() }
  }
  if(btn.disabled) btn.dataset.locked='1';
  pick.addEventListener('change',sync);
  sync();
})();
</script>
"""


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
            "No schedule - backups happen when you press the button. Set a time "
            "under <a href=\"#schedule\">When backups happen</a> below to run them "
            "nightly.")

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

def render_cloud(store, state, remote=None, message="", problem="", warning=""):
    """Connect a provider, see what is off-site, restore from it.

    The connect step is written for the one fact that shapes it: signing in to Google is
    the owner's to do, in their own browser. Obelisk asks for the token that comes back,
    never for the account.
    """
    from . import cloud as cloudlib
    banner = ""
    if problem:
        banner = '<div class=problem>%s</div>' % _e(problem)
    elif warning:
        banner = '<div class=warn>%s</div>' % _e(warning)
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
                # Not a button. Disconnecting deletes the passphrase, and the passphrase
                # is the only thing that can read an off-site archive back - so the
                # consequence is written out where the click used to be, and the word
                # has to be typed. The passphrase itself is never shown here: pointing
                # at it is the help this can safely give.
                '<form method=post action="/admin/cloud/disconnect">'
                '<div class=problem><b>Disconnecting deletes the encryption '
                'passphrase.</b> Every archive already off-site stays on the provider '
                'and becomes permanently unreadable - by you, and by Obelisk. This '
                'cannot be undone, and no copy is kept.'
                '<div class=help style="margin-top:6px">If you want those archives to '
                'stay usable, make sure you have the passphrase saved somewhere else '
                'before you do this. Obelisk will not show it to you here.</div></div>'
                '<div class=f><label>Type %s to confirm</label>'
                '<input name=confirm autocomplete=off placeholder="%s"></div>'
                '<button class=ghost type=submit>Disconnect and delete the passphrase'
                '</button></form></fieldset>'
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
                % (_e(state.get("provider", "")), _e(state.get("path", "")), line,
                   _e(cloudlib.DISCONNECT_WORD), _e(cloudlib.DISCONNECT_WORD), rows))

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

# Said once, because two panels say it: the overview lists every map's address and a
# map's own page states its own. A container cannot see the address its host answers
# on, and the sentence explaining that is the same sentence in both places.
HOST_UNKNOWN_WHY = ('<div class=help>Obelisk cannot see the address this machine '
                    'answers on from inside a container, so it is showing its own host '
                    'name. If that is not what people type, set <b>Server address</b> '
                    'in the container template to your LAN IP - it changes these lines '
                    'and nothing else.</div>')

IN_GAME_HELP = ('<div class=help style="margin-top:10px">In game: <b>Join ARK</b> '
                '&rarr; <b>Unofficial</b>, or open the console and type '
                '<code>open &lt;address&gt;</code>.</div>')
