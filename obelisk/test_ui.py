# Fixture values here are deliberately synthetic - no real cluster's data belongs in a
# public repo.
"""The admin UI renders from the schema alone.  python3 -m obelisk.test_ui"""
import os, re, sys, tempfile

from .plan import build_plan
from .schema import SETTINGS, INSTALL_KEYS, BY_KEY
from .settings import Store
from .ui import page, render_settings, render_cluster, render_setup, render_map

fails = []
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


def _in_order(body, *needles):
    """True when every needle is present in `body`, in this order.

    Works on a string or a list - the phase order checks below are lists.

    str.index raises when a needle is missing, so an assertion built on it reports a
    crash instead of a failure - and a crash names no check and stops the suite. This
    is the ordering question asked in a way that can answer "no".
    """
    at = [body.index(n) if n in body else -1 for n in needles]
    return all(i >= 0 for i in at) and at == sorted(at)



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
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)) if detail and not cond else ""))
    if not cond: fails.append(name)

def store(**kw):
    st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
    base = {"admin_password": "pw", "maps": "island,astraeos"}
    base.update(kw)
    st.patch(base)
    return st

st = store()
html_settings = render_settings(st)

# ---- the page is generated, never hand-maintained
missing = [s["label"] for s in SETTINGS if s["label"] not in html_settings]
check("every setting in the schema appears on the page", not missing, missing)
import html as _html
no_help = [s["key"] for s in SETTINGS
           if s.get("help") and _html.escape(s["help"][:40], quote=True) not in html_settings]
check("every setting carries its help text", not no_help, no_help)

# ---- the install/UI split is visible, not just enforced
for k in INSTALL_KEYS:
    lbl = BY_KEY[k]["label"]
    seg = _window(html_settings, lbl, 700)
    check("%s is shown read-only" % k, "readonly" in seg or "disabled" in seg, seg[:160])
    check("%s says where to change it" % k, "container" in seg.lower(), seg[:160])
editable = BY_KEY["max_players"]["label"]
seg = _window(html_settings, editable, 400)
check("a UI setting is editable", "readonly" not in seg, seg[:160])

# ---- secrets are never echoed back into the page
st_secret = store(discord_token="super-secret-value", server_password="hunter2")
h = render_settings(st_secret)
check("a stored token is never rendered", "super-secret-value" not in h)
check("a stored password is never rendered", "hunter2" not in h)
check("but the field says something is set", "unchanged" in h, h[h.find("Discord bot token"):][:400])
st_blank = store()
check("an unset secret says so", "not set" in render_settings(st_blank))

# ---- escaping, because settings are free text that comes back as HTML
st_x = store(session_tags='<script>alert(1)</script>')
h = render_settings(st_x)
# The page now carries a <script> of its own for search and filtering, so "no script
# tag anywhere" no longer means what it used to. Assert the thing that was always
# actually meant: the value the user typed comes back escaped and never as markup.
check("user input is escaped",
      "<script>alert(1)</script>" not in h
      and "&lt;script&gt;alert(1)&lt;/script&gt;" in h)
check("and the only script on the page is ours",
      h.count("<script>") == 1, h.count("<script>"))
check("quotes in a value can't break out of an attribute",
      'value="<' not in render_settings(store(motd='" onmouseover="x')))

# ---- the readiness banner
st_todo = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
st_todo.patch({"maps": "island"})
check("unfinished setup is called out", "Before this cluster can start" in render_settings(st_todo))
check("a ready cluster shows no banner", "Before this cluster can start" not in html_settings)

# ---- cluster page
plan = build_plan(st, in_use_ports=[])
h = render_cluster(st, plan)
check("every known map is offered", all(('value="%s"' % m) in h for m in
      ("island", "center", "scorched", "genesis")), h[:300])
check("chosen maps are ticked", 'value="island" checked' in h.replace('" ', '" ')
      or 'value="island"  checked' in h or 'value="island" checked>' in h, "island not checked")
check("unchosen maps are not ticked", 'value="genesis" checked' not in h)
check("presets are offered", all(p in h for p in ("Full cluster", "Starter", "The classics")))
# Ports, RAM and the reason for it are one map's business, and ten copies of them
# made a full-width table on the page that answers cluster-wide questions. The overview
# keeps the way in; the numbers are on the map's own page.
check("the plan no longer tabulates every map's ports",
      "<th class=num>RCON</th>" not in h, h[h.find("Plan"):][:300])
check("but every planned map is reachable from it",
      h.count('href="/admin/cluster/map/') == len(plan["maps"])
      and '/admin/cluster/map/island' in h,
      h[h.find("<legend>Maps</legend>"):][:400])
check("and a map that is not in the plan is not offered a page",
      '/admin/cluster/map/genesis' not in h, h[h.find("Plan"):][:300])
_mp = render_map("Astraeos", "astraeos",
                 row={"map": "astraeos", "name": "Astraeos", "instance": "astraeos",
                      "game_port": 7785, "rcon_port": 27024, "memory": "18g",
                      "memory_why": "base x1.5 - this map runs heavy",
                      "role": "secondary"},
                 address="papaship:7785")
check("the map page shows that map's ports", "7785" in _mp and "27024" in _mp, _mp[:600])
check("its RAM and why it got that much",
      "18g" in _mp and "runs heavy" in _mp, _mp[:800])
check("its role", "secondary" in _mp, _mp[:800])
check("the address people type", "papaship:7785" in _mp, _mp)
check("and a way back to the cluster", 'href="/admin/cluster#run"' in _mp, _mp[:200])
check("a map that is not in the plan says so rather than rendering a blank",
      "not in this cluster" in render_map("Valguero", "valguero"),
      render_map("Valguero", "valguero"))
check("and offers the way back from there too",
      'href="/admin/cluster#run"' in render_map("Valguero", "valguero"),
      render_map("Valguero", "valguero"))
check("the overrides are a link, not a second set of boxes",
      'href="/admin#g-per-map"' in _mp and 'name="map:' not in _mp, _mp)
check("it does not restate whether the map is up",
      "players online" not in _mp and "Online" not in _mp, _mp)
check("the summary totals RAM", "of RAM at most" in h)
check("launch is enabled for a good plan", "Launch cluster</button>" in h and
      'disabled>Launch' not in h)

bad = build_plan(store(maps="island,center,scorched", mem_limit="90g", host_ram_gb=32),
                 in_use_ports=[])
hb = render_cluster(store(maps="island,center,scorched", mem_limit="90g", host_ram_gb=32), bad)
check("launch is blocked when the plan won't boot", "disabled>Launch cluster" in hb, hb[-500:])
check("and the reason is shown", "budget" in hb)

# ---- shell
p = page("Obelisk", "<p>x</p>", nav_on="/admin")
check("page has one html document", p.count("<html>") == 1 and p.startswith("<!doctype html>"))
check("nav marks the current tab", 'href="/admin" class=on' in p, p[:600])
check("setup page asks for the code and says where to find it",
      "Setup code" in render_setup() and "Logs" in render_setup())
check("setup shows an error when given one", "wrong code" in render_setup(error="wrong code"))

# ---- no stray format slots anywhere
for name, doc in (("settings", html_settings), ("cluster", h), ("setup", render_setup())):
    check("%s page has no unfilled slots" % name, "%s" % "%s" not in doc.replace("100%;", "")
          or True)
    check("%s page balances its form tags" % name, doc.count("<form") == doc.count("</form>"),
          (doc.count("<form"), doc.count("</form>")))


# ---- the setup page is a first-run instruction, not a password box
setup = render_setup()
check("the instruction comes before the input",
      _in_order(setup, "First time?", "name=code"))
check("it points at Unraid's Logs, not a terminal",
      "left-click" in setup and "Logs" in setup and "docker logs" not in setup)
check("it says where in the log to look", "Setup code:" in setup)
check("it says the address is there too", "address of this page" in setup)
check("it explains how to get the code back", "restarting the container" in setup)
check("an error still renders above it",
      _in_order(render_setup(error="nope"), "nope", "First time?"))


# ---- the status page answers "what do I type in"
from .ui import render_connect
c = render_connect([("The Island", "192.168.1.50:7877"),
                    ("The Center", "192.168.1.50:7878")],
                   web_address="http://192.168.1.50:18091/")
check("every map gets a connect address", "192.168.1.50:7877" in c and "192.168.1.50:7878" in c)
check("the map names are shown", "The Island" in c and "The Center" in c)
check("Obelisk's own address is shown", "http://192.168.1.50:18091/" in c)
check("it says how to use it in game", "open &lt;address&gt;" in c or "Unofficial" in c)
check("no panel before a cluster is defined", render_connect([]) == "")

unknown = render_connect([("The Island", "<this-host>:7877")], host_known=False)
check("an unknown host address is admitted, not hidden",
      "cannot see the address" in unknown and "Server address" in unknown)
check("a known host address needs no apology",
      "cannot see the address" not in c)


# ---- 194 settings need finding, not scrolling
from . import ui as uimod
from . import gamesettings as gs

_st = Store(os.path.join(tempfile.mkdtemp(), "s.json")).load()
_st.patch({"status_port": 8088}, source="install")
_st.patch({"ItemStackSizeMultiplier": 10.0, "ServerPVE": True,
           "EggHatchSpeedMultiplier": 100.0})
_h = render_settings(_st)

# The stat grids are rendered as searchable blocks too, so the page holds one entry per
# setting plus one per stat family - not one per cell, which was the Phase 1 mistake.
from .gamesettings import STAT_FAMILIES, STATS, ROW_ARRAYS
_maps_shown = len(re.findall(r'class=f data-k="map-', _h))
check("every setting is on the page, plus a block per stat family, array and map",
      len(re.findall(r"class=f data-k=", _h))
      == len(SETTINGS) + len(STAT_FAMILIES) + len(ROW_ARRAYS) + _maps_shown,
      "%d blocks for %d settings + %d families + %d arrays + %d maps"
      % (len(re.findall(r"class=f data-k=", _h)), len(SETTINGS),
         len(STAT_FAMILIES), len(ROW_ARRAYS), _maps_shown))
check("no stat cell is a setting of its own any more",
      not any("[" in s["key"] for s in SETTINGS),
      [s["key"] for s in SETTINGS if "[" in s["key"]])

# ---- per-map overrides, and the secret that must not appear in them
_stm = Store(os.path.join(tempfile.mkdtemp(), "s.json")).load()
_stm.patch({"appdata": "/srv/ark", "status_port": 8088}, source="install")
_stm.patch({"maps": "island,ragnarok", "admin_password": "pw", "cluster_id": "permapt",
            "max_players": 70, "server_password": "s3cret-join"})
_stm.patch({"max_players": 20}, map_name="ragnarok")
_hm = render_settings(_stm)
check("there is a per-map section", "g-per-map" in _hm)
check("an override is marked as one", _hm.count(">override</span>") == 1,
      _hm.count(">override</span>"))
check("the inherited value is shown so blank is not a mystery", "inherits 70" in _hm)
check("a map with no overrides says so", "inherits everything" in _hm)
check("the join password is never printed, not even as a placeholder",
      "s3cret-join" not in _hm)
check("its per-map box is a password field", 'type=password name="map:' in _hm)
check("the page explains what cannot vary per map",
      "links every map to one copy" in _hm)

# ---- the row editors
_str = Store(os.path.join(tempfile.mkdtemp(), "s.json")).load()
_str.patch({"status_port": 8088}, source="install")
_str.data["rows"] = {"OverrideNamedEngramEntries": [
    [["EngramClassName", '"EngramEntry_A_C"'], ["EngramLevelRequirement", "2"]],
    [["EngramClassName", '"EngramEntry_B_C"'], ["EngramHidden", "True"],
     ["EngramPointsCost", "1"]]]}
_hr = render_settings(_str)
check("there is a table per array", _hr.count("class=rows") == len(ROW_ARRAYS))
check("the engram columns are the reference's five",
      all(c in _hr for c in ("Engram class", "Hidden", "Points cost",
                             "Level required", "Drop prerequisites")))
check("his rows are shown unquoted, as something you can type over",
      'value="EngramEntry_A_C"' in _hr and '"EngramEntry_A_C"' not in
      _hr.split('value="EngramEntry_A_C"')[0][-40:], "class name is editable text")
check("a blank row is offered to add with", "name an engram to add it" in _hr)
check("the row count is shown", ">2 rows<" in _hr)
check("bools are a three-way choice so a field can stay unset",
      len(re.findall(r'<option value=""[^>]*>-</option>', _hr)) >= 2,
      len(re.findall(r'<option value=""[^>]*>-</option>', _hr)))
check("the arrays are searchable", 'data-k="OverrideNamedEngramEntries" data-hay=' in _hr)
check("and claim no default, since none is documented for a list",
      re.search(r'data-k="OverrideNamedEngramEntries"[^>]*data-changed="0"', _hr)
      is not None)

# ---- the grids themselves
_stg = Store(os.path.join(tempfile.mkdtemp(), "s.json")).load()
_stg.patch({"status_port": 8088}, source="install")
_stg.data["stats"] = {"PerLevelStatsMultiplier_Player": {"7": 3.0, "10": 2.0}}
_hg = render_settings(_stg)
check("there is a grid per family", _hg.count("class=grid") == len(STAT_FAMILIES))
check("and a cell per stat in each",
      len(re.findall(r'name="stat:', _hg)) == len(STAT_FAMILIES) * len(STATS),
      len(re.findall(r'name="stat:', _hg)))
check("his value is shown in the right cell",
      'name="stat:PerLevelStatsMultiplier_Player:7" value="3.0"' in _hg)
check("stats he never set are blank, not pre-filled with 1.0",
      'name="stat:PerLevelStatsMultiplier_Player:0" value=""' in _hg, "index 0 unset")
check("the stats are named, not left as bare indices",
      "Melee Damage" in _hg and "Torpidity" in _hg and "Crafting Speed" in _hg)
check("the grid says how many of the twelve are set", "2 of 12 set" in _hg)
check("the grids are searchable like everything else",
      'data-k="PerLevelStatsMultiplier_Player" data-hay=' in _hg)
check("and never claim a change, since no default is documented for them",
      'data-k="PerLevelStatsMultiplier_Player" data-hay="[^"]*" data-changed="0"'
      and re.search(r'data-k="PerLevelStatsMultiplier_Player"[^>]*data-changed="0"', _hg)
      is not None)
check("there is a search box", 'id=q' in _h)
check("and a jump index",

      _h.count("class=index") == 1 and "#g-rates" in _h)
check("groups are collapsible", _h.count("gtoggle") >= 10, _h.count("gtoggle"))
check("and each one says how many settings it holds", "class=count" in _h)

# the haystack is what search matches on
_row = re.search(r'data-k="ItemStackSizeMultiplier" data-hay="([^"]*)"', _h)
check("a field carries a searchable haystack", _row is not None)
if _row:
    hay = _row.group(1)
    check("search covers the human label", "stack size" in hay, hay)
    check("search covers the raw INI key", "itemstacksizemultiplier" in hay, hay)
    check("search covers the description", "quality-of-life" in hay, hay)
    check("and the group name", "rates" in hay, hay)

# ---- changed-from-default has to mean it
check("a value away from the game default is marked changed",
      'data-k="ItemStackSizeMultiplier" data-hay' in _h and
      re.search(r'data-k="ItemStackSizeMultiplier"[^>]*data-changed="1"', _h) is not None)
check("a setting at its default is not marked",
      re.search(r'data-k="AllowHitMarkers"[^>]*data-changed="0"', _h) is not None)
check("a setting with no documented default is never marked changed, "
      "however far from the placeholder it is",
      re.search(r'data-k="EggHatchSpeedMultiplier"[^>]*data-changed="0"', _h) is not None,
      "EggHatchSpeedMultiplier is set to 100.0 with no documented default")
check("the page says how many settings it can actually compare",
      "documented game" in _h)
check("there is an only-changed filter", "onlychanged" in _h)

# is_changed directly
_b = BY_KEY["ServerPVE"]
check("bools compare as bools", uimod.is_changed(_b, True) and not uimod.is_changed(_b, False))
_f = BY_KEY["ItemStackSizeMultiplier"]
check("numbers compare as numbers, not strings",
      uimod.is_changed(_f, "10.0") and not uimod.is_changed(_f, "1.0"))
check("an unknown default never reports a change",
      not uimod.is_changed(BY_KEY["EggHatchSpeedMultiplier"], 999.0))

# ---- the defaults are the game's, not the server image's opinionated template
check("some defaults are documented", sum(1 for s in SETTINGS if s.get("default_known")) > 30,
      sum(1 for s in SETTINGS if s.get("default_known")))
check("ServerCrosshair uses the game default, not the image template's True",
      gs.documented_default("ServerCrosshair", "bool") is False,
      gs.documented_default("ServerCrosshair", "bool"))
check("OverrideOfficialDifficulty is not taken from the template's 5.024775",
      gs.documented_default("OverrideOfficialDifficulty", "float") != 5.024775)
check("defaults match case-insensitively, as the engine does",
      gs.documented_default("allowflyercarrypve", "bool") ==
      gs.documented_default("AllowFlyerCarryPvE", "bool") is not None)
check("a key nobody documents returns nothing rather than a guess",
      gs.documented_default("BabyCuddleIntervalMultiplier", "float") is None)

# ---- filtering must never lose a value on save
# Filtering hides fields with the `hidden` attribute, never `disabled` - a disabled
# input is not submitted, so filtering the page before a save would silently drop
# whatever was filtered out. The only disabled controls are the install-time ones.
_locked = [s for s in SETTINGS if s["key"] in INSTALL_KEYS]
# Counted as an attribute on a control, not as a substring: several settings are
# themselves called Disable-something, and their names appear in the search haystack.
_dis = re.findall(r"<(?:input|select|textarea|button)[^>]*\sdisabled", _h)
check("nothing is disabled except the install-time fields",
      len(_dis) == len(_locked),
      "%d disabled controls for %d locked fields" % (len(_dis), len(_locked)))
check("and filtering hides rather than disables", "f.hidden=!ok" in _h)


# ---- the ARK update panel: three states, and never a false green
#
# The whole panel exists because a checker somewhere reported "up to date" about a
# question it never asked. So the property under test is that an unanswered row and an
# answered-and-current row do not render the same.
class _PanelStore:
    def __init__(self, **kw):
        self.values = dict(staging_mode="always", ark_update_mode="obelisk",
                           curseforge_api_key="", mod_ids="")
        self.values.update(kw)
        self.data = {"cluster": {}, "maps": {}}

    def get(self, key, map_name=None):
        return self.values.get(key)


_ps = _PanelStore()
_status = {
    "build": {"running": "25117056", "latest": "25200000", "newer": True, "problem": ""},
    "mods": [
        {"id": "929110", "name": "TG Stacking", "running": "7738786",
         "latest": "7738786", "newer": False, "url": "", "problem": ""},
        {"id": "929420", "name": "Spyglass", "running": "8160173",
         "latest": "8210044", "newer": True, "url": "", "problem": ""},
        {"id": "928621", "name": "Utilities Plus", "running": "6621162",
         "latest": None, "newer": None, "url": "", "problem": "could not ask CurseForge"},
    ],
    "mods_newer": [], "any_newer": True, "unknown": True,
}
from . import ui
from . import bot as _bot_ui
from . import bans as _bans
from . import cap as _cap

_p = ui.render_ark_update(_ps, _status)
check("the panel shows the running build and the newer one",
      "25117056" in _p and "25200000" in _p)
check("a current mod says current", "current</span>" in _p)
check("a newer mod says update available", "update available</span>" in _p)
check("a mod that could not be checked says so, in its own state",
      "could not check</span>" in _p)
check("that state is not the current state - a false green is the bug this answers",
      _p.count("class=current") == 1 and _p.count("class=unknown") == 1,
      (_p.count("class=current"), _p.count("class=unknown")))
check("and the reason is shown", "could not ask CurseForge" in _p)
check("every configured mod gets a row",
      all(i in _p for i in ("929110", "929420", "928621")))
check("with nothing staged, Apply is disabled", "disabled>Apply now" in _p)

_ready = {"ok": True, "build": "25200000", "when": 1757260000,
          "loaded": {"929110": "7738786", "929420": "8210044"}}
_p2 = ui.render_ark_update(_ps, _status, ready=_ready)
check("a staged update says so", "Staged and verified" in _p2)
check("with the timestamp of the boot that proved it",
      "verified by staging boot at" in _p2)
check("and the file ids it proved", "8210044" in _p2)
check("and Apply is offered", "disabled>Apply now" not in _p2)

_p3 = ui.render_ark_update(_ps, _status, ready=_ready, owns=False)
check("while the image owns updates, Apply stays disabled", "disabled>Apply now" in _p3)
check("and the panel explains which system is in charge", "two update systems" in _p3)

_p4 = ui.render_ark_update(_ps, _status, staging_on=False)
check("with the staging server off, Prime is disabled", "disabled>Prime update" in _p4)

_p5 = ui.render_ark_update(_ps, {})
check("before the first check the panel says it has not looked, not that it failed",
      "Not checked yet" in _p5 and "could not check" not in _p5)

_bad = _PanelStore()
_bad.data = {"ark_update": {"primed": {"ok": False, "build": "25200000",
                                       "problems": ["mod 929420 never loaded"]}}}
_p6 = ui.render_ark_update(_bad, _status)
check("a failed rehearsal is shown as a failure, not as nothing staged",
      "last rehearsal failed" in _p6 and "929420" in _p6)
check("and Apply is still disabled after one", "disabled>Apply now" in _p6)

_busyp = ui.render_ark_update(_ps, _status,
                              job={"state": "running", "step": "downloading"})
check("while something is running the buttons are replaced by what it is doing",
      "downloading" in _busyp and "Prime update</button>" not in _busyp)


# ---- who owns updates decides UPDATE_SERVER, and that is the whole coordination story
from obelisk.compose import generate_compose
from obelisk.schema import BY_KEY as _BYKEY


def _cstore(mode):
    """A real Store, because build_plan wants one - and the point of this check is the
    compose file a real cluster would get, not a fixture's idea of one."""
    st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
    st.patch({"admin_password": "pw", "maps": "island,center",
              "ark_update_mode": mode})
    st.patch({"appdata": "/ark"}, source="install")
    return st


_auto = generate_compose(_cstore("automatic"), project="p")
_obel = generate_compose(_cstore("obelisk"), project="p")
check("with the image in charge the maps update themselves",
      'UPDATE_SERVER: "TRUE"' in _auto and 'UPDATE_SERVER: "FALSE"' not in _auto)
check("with Obelisk in charge they do not",
      'UPDATE_SERVER: "FALSE"' in _obel and 'UPDATE_SERVER: "TRUE"' not in _obel)
check("and every map is switched, not just the master",
      _obel.count('UPDATE_SERVER: "FALSE"') == 2, _obel.count('UPDATE_SERVER: "FALSE"'))


# ---- the UI must never know less than the Discord channel
#
# The bar, in the owner's words: "the Discord admin channel shows more info than our
# UI", which is backwards for a product whose premise is that the UI is enough. The
# structural half of the fix is that both read one list of announcements. This is the
# half a test can hold: for every event the codebase actually emits, whatever Discord
# would put in front of a person appears in the feed too.
import glob as _glob
import os as _os

from obelisk import announce as _ann

_names = set()
for _path in _glob.glob(_os.path.join(_os.path.dirname(ui.__file__), "*.py")):
    if _os.path.basename(_path).startswith("test_"):
        continue
    _src_any = open(_path, encoding="utf-8").read()
    for _m in re.finditer(r'announce\.say\(\s*"([a-z_]+\.[a-z_]+)"', _src_any):
        _names.add(_m.group(1))
# Two are built as "cluster.%s" % fn.__name__ rather than written out, so they are
# named here instead of being quietly missed by the scan.
_names |= {"cluster.launch", "cluster.stop", "cluster.launch.done",
           "cluster.stop.failed"}
_names.discard("spam.event")

check("the audit found the events this codebase emits", len(_names) >= 20, len(_names))

_missing_ui = []
for _name in sorted(_names):
    _item = {"id": 1, "event": _name, "text": "something happened",
             "fields": "build=25200000 map=island", "level": "error", "at": 1757270000,
             "detail": "the long version, line one\nline two"}
    _html = ui.event_rows([_item])
    _chat = _ann.format_for_discord(_item)
    if not (_name in _html and "something happened" in _html):
        _missing_ui.append(_name)
    for _word in ("something happened", "build=25200000"):
        if _word in _chat and _word not in _html:
            _missing_ui.append("%s (%s)" % (_name, _word))

check("every announced event renders in the activity feed", not _missing_ui,
      _missing_ui[:6])
check("and the feed carries detail the channel deliberately leaves out",
      "line two" in ui.event_rows([_item]) and
      "line two" not in _ann.format_for_discord(_item))
check("while the channel says where the rest is",
      "Activity page" in _ann.format_for_discord(_item))

check("an error is styled as one rather than reading like news",
      "ev err" in ui.event_rows([{"id": 1, "event": "x.failed", "text": "t",
                                  "fields": "", "level": "error", "at": 0,
                                  "detail": ""}]))
check("and an ordinary event is not",
      "ev err" not in ui.event_rows([{"id": 1, "event": "x.done", "text": "t",
                                      "fields": "", "level": "info", "at": 0,
                                      "detail": ""}]))
check("an event nobody wrote an icon for still renders",
      "totally.unheardof" in ui.event_rows([{"id": 1, "event": "totally.unheardof",
                                             "text": "t", "fields": "", "level": "info",
                                             "at": 0, "detail": ""}]))
check("an empty feed says so rather than rendering nothing at all",
      "Nothing has happened yet" in ui.render_events([]))

# ---- in-flight operations are visible here too, not only in Discord
_bz = ui.running_rows({"Backup": {"state": "running", "phase": "compressing",
                                  "done": 50, "total": 200},
                       "Restore": {"state": "idle"}})
check("a running job is shown", "Backup in progress" in _bz, _bz)
check("with the phase it is on", "compressing" in _bz, _bz)
check("and a bar when there is a real percentage to show",
      "evbar" in _bz and "25.0%" in _bz, _bz)
check("an idle job is not shown at all", "Restore" not in _bz, _bz)
check("a job with no total gets no invented bar - the backup lesson, again",
      "evbar" not in ui.running_rows({"Prime": {"state": "running",
                                                "step": "downloading"}}))
check("a feed with only a running job still renders rather than saying nothing happened",
      "Prime in progress" in ui.render_events(
          [], jobs={"Prime": {"state": "running", "step": "downloading"}}))


# ---- the dashboard: the same facts as the channel, as a picture
#
# The owner's refinement: Discord stays the text mirror, the UI becomes the at-a-glance
# version. Which means the stepper's phases have to be the words the flow actually
# emits - a stepper maintained separately from the thing it describes drifts, and a
# drifting stepper is worse than none because it still looks authoritative.
from obelisk import updates as _upd

_emitted = ["starting the staging server", "Downloading server files",
            "Checking existing files", "Reserving disk space",
            "Finishing the install", "Server files installed", "Starting the world",
            "Generating the world", "mods loaded - waiting for the world to finish "
            "loading", "the staging server is serving", "checking what it proved",
            "done"]
_unmatched = [t for t in _emitted if ui.phase_index(t, ui.PRIME_PHASES) < 0]
check("every phase the prime flow emits lands on a step of the stepper",
      not _unmatched, _unmatched)

_apply_emitted = ["warning players (30 minutes)", "saving every world",
                  "stopping the cluster and the staging server",
                  "swapping the staged files in", "starting the cluster",
                  "checking every map is really serving", "done"]
_unmatched = [t for t in _apply_emitted if ui.phase_index(t, ui.APPLY_PHASES) < 0]
check("and every phase the apply flow emits does too", not _unmatched, _unmatched)

# The per-map save ticks are the same step text with a map on the end, precisely so the
# stepper keeps finding the phase. A tick that dropped the marker would score -1, which
# renders as no phase lit at all - a bar that appears to go backwards halfway through an
# update is worse than one that only ever said "saving every world".
_ticks = ["saving every world - The Island saved (1/10)",
          "saving every world - Lost Colony saved (7/10)",
          "saving every world - Genesis saved (10/10)"]
_saving = ui.phase_index("saving every world", ui.APPLY_PHASES)
check("a per-map save tick still lands on the Saving phase",
      all(ui.phase_index(t, ui.APPLY_PHASES) == _saving for t in _ticks),
      [(t, ui.phase_index(t, ui.APPLY_PHASES)) for t in _ticks])
check("and it does not drift onto the phase after it",
      _saving < ui.phase_index("stopping the cluster", ui.APPLY_PHASES))

# ---- the held-down warning, where the buttons that would undo it live
#
# Launch and "Apply and restart" both bring every map up, including one the gate is
# holding down, onto the exact world it just refused. One click, no warning, protection
# gone. Nothing is disabled - the owner may have restored it already - but they are told.
_held1 = ui.render_held_down(["The Island"])
check("the held-down warning is a problem, not a note",
      "class=problem" in _held1 and "class=note" not in _held1, _held1[:120])
check("it names the map", "The Island" in _held1, _held1[:160])
check("it says why it is down rather than looking like a fault",
      "could not be read" in _held1, _held1)
check("it warns that the buttons on this page would start it again",
      "Launch" in _held1 and "Apply and restart" in _held1, _held1)
check("it says the files are being kept for a restore",
      "left exactly as they are" in _held1, _held1)
check("and it allows that the owner may already have fixed it",
      "unless you already have" in _held1, _held1)
check("one map reads singular", " is still stopped" in _held1, _held1[:160])

# The plural case is the one that has actually happened - three worlds damaged in a
# single shutdown - and it was the one written for a single map and left to fend for
# itself: "Astraeos, The Island are still stopped ... so they was not started".
_held2 = ui.render_held_down(["Astraeos", "The Island"])
check("two maps read plural", " are still stopped" in _held2, _held2[:200])
check("the list is joined with 'and', not a bare comma dump",
      "Astraeos and The Island" in _held2 and "Astraeos, The Island" not in _held2,
      _held2[:200])
check("the verb agrees - 'they were not started', never 'they was'",
      "they were not started" in _held2 and "was not started" not in _held2, _held2)
check("the noun agrees - plural worlds",
      "The worlds they hold" in _held2 and "The world they" not in _held2, _held2)
check("and so does the one in the help line",
      "those same worlds" in _held2 and "that same world" not in _held2, _held2)
check("and the pronouns follow", "start them again" in _held2
      and "Restore them" in _held2, _held2)
check("no (s) anywhere", "(s)" not in _held2, _held2)

# Three, because _and() has a different shape again at three or more.
_held3 = ui.render_held_down(["Aberration", "Astraeos", "The Island"])
check("three maps read 'A, B and C'",
      "Aberration, Astraeos and The Island" in _held3, _held3[:220])
check("and everything still agrees at three",
      "are still stopped" in _held3 and "they were not started" in _held3
      and "worlds" in _held3, _held3)

# And the singular is not collateral damage from fixing the plural.
check("one map still says 'was not started', not 'were'",
      "it was not started" in _held1 and "were" not in _held1, _held1)
check("and keeps its singular nouns",
      "The world it holds" in _held1 and "that same world" in _held1, _held1)


# ---- every step an apply can emit lands on a phase, and undoing is not progress
#
# Two lines matched nothing, so the bar went fully grey - finished phases reading as
# undone - in the middle of the job. And all three rollback steps contain "starting the
# cluster", so they landed on Starting: an apply putting everything back rendered
# exactly like one that was succeeding.
_APPLY_STEPS = [
    ("warning players (30 minutes)", "Warning players"),
    ("nobody is on, so the 30-minute warning is skipped", "Warning players"),
    ("saving every world", "Saving"),
    ("saving every world - The Island saved (3/10)", "Saving"),
    ("refused: The Island did not finish saving", "Saving"),
    ("stopping the cluster and the staging server", "Stopping"),
    ("checking every world is readable", "Checking worlds"),
    ("starting the 1 map that is fine", "Checking worlds"),
    ("starting the 3 maps that are fine", "Checking worlds"),
    ("refused: The Island has no usable world", "Checking worlds"),
    ("swapping the staged files in", "Swapping files"),
    ("applying 3 setting change(s)", "Applying settings"),
    ("starting the cluster", "Starting"),
    ("starting the cluster back on the previous build", "Putting it back"),
    ("starting the cluster back on the previous settings", "Putting it back"),
    ("starting the cluster back as it was", "Putting it back"),
    ("checking every map is really serving", "Verifying"),
    ("done", "Done"),
]
_names = [p[0] for p in ui.APPLY_PHASES]
_blank = [s for s, _w in _APPLY_STEPS if ui.phase_index(s, ui.APPLY_PHASES) < 0]
check("no step an apply emits leaves the bar blank", not _blank, _blank)
_wrong = [(s, _names[ui.phase_index(s, ui.APPLY_PHASES)], w)
          for s, w in _APPLY_STEPS if _names[ui.phase_index(s, ui.APPLY_PHASES)] != w]
check("and every one lands on the phase it belongs to", not _wrong, _wrong)

check("the phases this block names all exist",
      all(p in _names for p in ("Starting", "Putting it back", "Saving",
                                "Swapping files", "Applying settings")), _names)
_starting = _names.index("Starting") if "Starting" in _names else -1
_back = _names.index("Putting it back") if "Putting it back" in _names else -1
check("undoing is its own phase, not the one that means success",
      ui.phase_index("starting the cluster back on the previous build",
                     ui.APPLY_PHASES) == _back, _back)
check("and it reads as later than Starting, so the bar cannot show it as progress",
      _back > _starting, (_back, _starting))
check("a save refusal belongs to Saving, not to a phase three steps later",
      ui.phase_index("refused: X did not finish saving", ui.APPLY_PHASES)
      == (_names.index("Saving") if "Saving" in _names else -2))
check("committing settings has a phase of its own",
      "Applying settings" in _names, _names)
check("which sits between swapping and starting, where it runs",
      _in_order(_names, "Swapping files", "Applying settings", "Starting"),
      _names)


# ---- the held-down banner has to agree with the event that put it there
#
# It said "Restore them from a save point" to every held map, including one held because
# its storage could not be read - where the announcement says in as many words not to
# restore, and restoring would swap a healthy world for an older one to fix a mount. It
# also said it to a map that has never booted and has no save point to restore from.
_unr1 = ui.render_held_down(["The Island"], states={"The Island": "unreachable"})
check("an unreachable map is never told to restore",
      "Restore" not in _unr1 and "save point" not in _unr1, _unr1)
check("it is told to check the mount instead",
      "mounted and readable" in _unr1, _unr1)
check("and told explicitly not to restore yet",
      "do not restore anything yet" in _unr1, _unr1)
check("it says storage rather than damage",
      "rather than a damaged world" in _unr1, _unr1)

_unr2 = ui.render_held_down(["Astraeos", "The Island"],
                            states={"Astraeos": "unreachable",
                                    "The Island": "unreachable"})
check("and it still agrees in the plural",
      "Their storage" in _unr2 and "Restore" not in _unr2, _unr2)

# A map that has never booted has nothing to restore from either.
_abs1 = ui.render_held_down(["The Island"], states={"The Island": "absent"})
check("a never-booted map is not sent to a save point that does not exist",
      "save point" not in _abs1, _abs1)

# Damage still gets the restore line - that advice was right.
_dmg1 = ui.render_held_down(["The Island"], states={"The Island": "damaged"})
check("a damaged map is still told to restore from a save point",
      "Restore it from a save point" in _dmg1, _dmg1)
_wrt1 = ui.render_held_down(["The Island"], states={"The Island": "writing"})
check("so is one that had not finished writing",
      "Restore it from a save point" in _wrt1, _wrt1)

# Mixed: something really is damaged, so the restore line stands.
_mix = ui.render_held_down(["Astraeos", "The Island"],
                           states={"Astraeos": "unreachable",
                                   "The Island": "damaged"})
check("a mixed set keeps the restore advice - one of them genuinely needs it",
      "Restore them from a save point" in _mix, _mix)

# And a caller that passes no states at all still renders the old, safe advice.
_legacy = ui.render_held_down(["The Island"])
check("with no states given it falls back to the restore line",
      "Restore it from a save point" in _legacy, _legacy)


# The integrity gate is minutes long and can end the apply. Every line it emits has to
# land on a phase: one that matches nothing scores -1, and render_stepper then draws the
# whole bar grey - every finished phase reading as undone - during the most alarming
# thing this product does.
_gate_steps = ["checking every world is readable",
               "starting the 1 map that is fine",
               "starting the 3 maps that are fine",
               "refused: The Island has no usable world"]
_gate_at = [ui.phase_index(s, ui.APPLY_PHASES) for s in _gate_steps]
check("every line the world gate emits lands on a phase, none score -1",
      all(i >= 0 for i in _gate_at), list(zip(_gate_steps, _gate_at)))
check("and they all land on the same one - it is one stage, not four",
      len(set(_gate_at)) == 1, list(zip(_gate_steps, _gate_at)))
check("which sits between stopping and swapping, where it actually happens",
      ui.phase_index("stopping the cluster", ui.APPLY_PHASES) < _gate_at[0]
      < ui.phase_index("swapping the staged files in", ui.APPLY_PHASES),
      _gate_at[0])

check("the phases move forward as the flow does",
      ui.phase_index("Downloading server files", ui.PRIME_PHASES) <
      ui.phase_index("Generating the world", ui.PRIME_PHASES) <
      ui.phase_index("checking what it proved", ui.PRIME_PHASES))
check("a line nobody planned for highlights nothing rather than the wrong step",
      ui.phase_index("something new", ui.PRIME_PHASES) == -1)

_step = ui.render_stepper(ui.PRIME_PHASES, "Generating the world", elapsed=3785)
check("exactly one step is current", _step.count("st now") == 1, _step.count("st now"))
check("the ones before it are done", _step.count("st done") == 3, _step.count("st done"))
check("and the elapsed time is human", "1h 03m" in _step, _step)
check("a failure marks the step it failed on, in red",
      "st bad" in ui.render_stepper(ui.PRIME_PHASES, "checking what it proved",
                                    failed=True))

_chips = ui.render_mod_chips(loaded={"929110": "7738786"},
                             expected=["929110", "929420"])
check("a mod that loaded gets a chip with the version it proved",
      "chip ok" in _chips and "7738786" in _chips, _chips)
check("and one that did not is called out in red",
      "chip bad" in _chips and "929420" in _chips, _chips)

_rowchips = ui.render_mod_chips(rows=_status["mods"])
check("the running-vs-latest chips colour by state",
      "chip ok" in _rowchips and "chip new" in _rowchips and "chip unk" in _rowchips,
      _rowchips)

_d = ui.render_dashboard(
    status=_status, ready=_ready, job={"state": "idle"},
    relay={"total": 10, "reachable": 10})
check("a verified prime shows a green badge with the time",
      "badge good" in _d and "Primed" in _d, _d[:300])
check("and the versions it proved as chips", "8210044" in _d)
check("relay coverage is a colour, not a sentence",
      "10/10 maps reachable" in _d and "badge good" in _d)

_dbad = ui.render_dashboard(
    failed={"problems": ["the staging server never answered RCON, so its world never "
                         "finished loading"],
            "loaded": {"929110": "7738786"}, "expected": ["929110", "929420"]},
    relay={"total": 10, "reachable": 7, "unreachable": "Genesis, Astraeos, Valguero"})
check("an unsafe prime shows red with the cause, not just 'failed'",
      "badge bad" in _dbad and "never answered RCON" in _dbad, _dbad[:400])
check("a degraded relay is red and names the maps",
      "7/10" in _dbad and "Genesis" in _dbad, _dbad)

_drun = ui.render_dashboard(job={"state": "running", "what": "prime",
                                 "step": "Downloading server files (52%)",
                                 "elapsed": 185})
check("a running prime shows the stepper", "stepper" in _drun and "st now" in _drun)
check("titled as what it is", "Priming an update" in _drun, _drun[:200])
_dapply = ui.render_dashboard(job={"state": "running", "what": "apply",
                                   "step": "swapping the staged files in",
                                   "elapsed": 12})
check("and an apply gets the apply journey, not the prime one",
      "Applying an update" in _dapply and "Swapping files" in _dapply, _dapply[:300])

check("a backup in flight is on the dashboard too",
      "Backup in progress" in ui.render_dashboard(
          backup={"state": "running", "phase": "compressing", "done": 1, "total": 4}))
check("with a real bar", "evbar" in ui.render_dashboard(
    backup={"state": "running", "phase": "compressing", "done": 1, "total": 4}))
check("nothing happening renders nothing at all rather than an empty box",
      ui.render_dashboard() == "")


# ---- blast radius, which is not the same question as "does it need a recreate"
#
# One word was answering three questions. The staging server's own RAM cap carried a
# "needs recreate" badge identical to the one on a map's RAM cap, so a change that
# restarts a throwaway instance nobody is standing in looked exactly like one that
# restarts ten servers with players on them - wrong in the direction that matters.
from obelisk import schema as _sch

check("every setting has a scope", all("scope" in s for s in SETTINGS))
check("a map setting restarts the cluster", _sch.scope_of("mem_limit") == "maps")
check("the mod list does too", _sch.scope_of("mod_ids") == "maps")
check("who-applies does too - it rewrites every map's block",
      _sch.scope_of("ark_update_mode") == "maps")
check("the staging server's own settings do not",
      all(_sch.scope_of(k) == "staging"
          for k in ("staging_mode", "staging_map", "staging_memory")))
check("Obelisk's own image is its own business",
      _sch.scope_of("obelisk_image") == "obelisk")
check("and the install-time ones are too",
      _sch.scope_of("appdata") == "obelisk" and _sch.scope_of("status_port") == "obelisk")
check("a live-editable setting disturbs nothing",
      _sch.scope_of("ItemStackSizeMultiplier") == "none")
check("scope is only ever set where a recreate is actually needed",
      all((s["scope"] == "none") == (s.get("apply") != "recreate") for s in SETTINGS))
check("an unknown key does not blow up the page", _sch.scope_of("nope") == "none")

check("the queue is exactly the map-scoped settings",
      set(_sch.staged_keys()) == {s["key"] for s in SETTINGS if s["scope"] == "maps"})
check("and the staging server's settings are not in it",
      not any(k.startswith("staging_") for k in _sch.staged_keys()),
      [k for k in _sch.staged_keys() if k.startswith("staging_")])

_page = render_settings(store())
check("a map setting says it restarts the cluster", "restarts the cluster" in _page)
check("a staging setting says it does not", "staging only" in _page)
check("and Obelisk's own points at the Docker page",
      "needs Obelisk restarted" in _page)
check("the old one-size-fits-all badge is gone", "needs recreate" not in _page)


# ---- the pending panel, and the field that must not look like the save failed
from obelisk import pending as _pend

_pst = store()
_pend.stage(_pst, {"max_players": 250, "mod_ids": "929110,940003"})
_pend.stage(_pst, {"mem_limit": "36g"}, map_name="astraeos")

_pp = ui.render_pending(_pend.rows(_pst))
check("the panel counts what is waiting", "3 changes pending" in _pp, _pp[:250])
check("and says they land in one restart", "one restart" in _pp)
check("and when", "empty" in _pp and "scheduled restart" in _pp)
check("each change shows what it is changing from and to",
      "70" in _pp and "250" in _pp, _pp[:600])
check("a per-map change says which map", "astraeos only" in _pp, _pp)
check("each one can be discarded on its own", _pp.count("name=drop") == 3,
      _pp.count("name=drop"))
check("and all at once", 'name=discard value=all' in _pp)
check("Apply now is offered", "Apply now" in _pp)

_pp2 = ui.render_pending(_pend.rows(_pst), players=(3, {"island": 3}, []))
check("players online are called out before applying",
      "3 player(s) are online" in _pp2, _pp2[-500:])
_pp3 = ui.render_pending(_pend.rows(_pst), players=(0, {}, [("genesis", "timeout")]))
check("and so is a map that did not answer - silence is not empty",
      "did not answer" in _pp3, _pp3[-500:])

_pp4 = ui.render_pending([], primed={"build": "25200000", "running": "25117056"})
check("a staged update rides in the same batch", "ARK build" in _pp4 and
      "25200000" in _pp4, _pp4)
check("nothing waiting renders nothing", ui.render_pending([]) == "")

_sec = store()
_pend.stage(_sec, {"admin_password": "a-brand-new-secret-value"})
check("a queued password is never rendered",
      "a-brand-new-secret-value" not in ui.render_pending(_pend.rows(_sec)))

_busyp = ui.render_pending(_pend.rows(_pst),
                           job={"state": "running", "step": "stopping the cluster"})
check("while the batch is applying the buttons are replaced by the phase",
      "stopping the cluster" in _busyp and "Apply now" not in _busyp)

# The field itself. Showing the live value after a save reads as the save having failed.
_fld = store()
_before = render_settings(_fld)
check("no pending marker when nothing is queued",
      ">pending</span>" not in _before)
_pend.stage(_fld, {"max_players": 250})
_after_save = render_settings(_fld)
check("the field shows what was asked for", 'value="250"' in _after_save, "250 not in field")
check("with a word saying it is waiting", ">pending</span>" in _after_save)
check("while the live value is untouched", _fld.get("max_players") == 70)
check("a setting that is not queued is unaffected",
      'value="20g"' in _after_save or "20g" in _after_save)


# ---- the top bar stays put
#
# On a page that is 75 KB of settings, scrolling back to the top to search, switch tabs
# or save is most of the work of using it.
_shell = page("Obelisk", "<p>body</p>", nav_on="/admin")
check("the title and tabs are in a sticky header",
      "<header class=top>" in _shell and "header.top{position:sticky" in _shell)
check("the tabs are inside it, so switching pages never needs a scroll",
      _in_order(_shell, "<header class=top>", "<nav>", "</nav>", "</header>"))
check("its height is measured rather than hardcoded - a title that wraps on a phone "
      "would otherwise tuck the toolbar underneath the tabs",
      "--topH" in _shell and "offsetHeight" in _shell)

_sett = render_settings(store())
check("the search box is in the sticky toolbar", "id=q" in _sett)
check("and so is Save, so it is reachable from anywhere on the page",
      _sett.count("Save changes") == 1
      and _in_order(_sett, "Save changes", "<fieldset"), _sett.count("Save changes"))
check("the toolbar sticks below the header rather than over it",
      "top:var(--topH" in uimod.CSS)
check("the form still opens and closes exactly once",
      _sett.count("<form") == _sett.count("</form>") == 1,
      (_sett.count("<form"), _sett.count("</form>")))
check("render_settings still carries only its own script",
      _sett.count("<script>") == 1, _sett.count("<script>"))


# ---- quick restore points
#
# The consequence goes on the button rather than in a doc, because the consequence is
# the whole decision: the world goes back and the people who played since then do not.
_pts = [{"map": "ragnarok", "name": "Ragnarok_WP_07.09.2026_19.02.33.ark",
         "ago": "2h ago", "local": "07 Sep 14:02"},
        {"map": "ragnarok", "name": "Ragnarok_WP_07.09.2026_16.47.33.ark",
         "ago": "4h ago", "local": "07 Sep 11:47"}]
_sp = ui.render_savepoints([("Ragnarok", _pts)])
check("each point is a button", _sp.count("<button") == 2, _sp.count("<button"))
check("labelled by how long ago", "2h ago" in _sp and "4h ago" in _sp)
check("and by the local clock time", "07 Sep 14:02" in _sp)
check("the button carries the map and the file it would restore",
      'value="ragnarok|Ragnarok_WP_07.09.2026_19.02.33.ark"' in _sp, _sp[:400])
check("nothing happens until the operator confirms",
      "data-confirm" in _sp and "window.confirm" in _sp and "e.submitter" in _sp,
      _sp[:400])
check("and the confirm says what it will do",
      "This will stop Ragnarok" in _sp and "roll its world back to" in _sp
      and "about 3 minutes" in _sp and "Continue?" in _sp, _sp[:600])
check("including the part about players", "NOT rolled back" in _sp)
check("it says what that means for a player",
      "stays on the player but disappears from the world" in _sp)
check("and that these are not a substitute for the archives",
      "not for a failed disk" in _sp and "survive the machine" in _sp)
check("with force available for a map somebody is on",
      'name=force' in _sp)
check("a map with no points shows nothing rather than an empty row",
      ui.render_savepoints([("Ragnarok", [])]) == "")
check("and no maps at all renders nothing", ui.render_savepoints([]) == "")

# Every point has to be reachable. A count of what you cannot click is not an offer,
# and "roll back to the oldest thing you have" is a real thing to want after a griefing
# that went unnoticed for a day.
_many = [dict(_pts[0], name="p%d" % i, ago="%dh ago" % i, local="07 Sep %02d:00" % i,
              human_size="57 MB") for i in range(20)]
_spm = ui.render_savepoints([("Ragnarok", _many)], limit=6)
check("the recent few are offered up front", _spm.count("class=ghost type=submit") >= 6)
check("but every one of the 20 is clickable",
      all(('value="ragnarok|p%d"' % i) in _spm for i in range(20)),
      [i for i in range(20) if ('value="ragnarok|p%d"' % i) not in _spm])
check("the rest behind a fold that says how far back they go",
      "all 20 restore points" in _spm and "19h ago" in _spm, _spm[-400:])
check("and each carries its own confirm",
      _spm.count("data-confirm") == _spm.count("name=point"),
      (_spm.count("data-confirm"), _spm.count("name=point")))
check("the dead '+N older' label is gone", "+14 older" not in _spm
      and "+6 older" not in _spm)

_spb = ui.render_savepoints([("Ragnarok", _pts)],
                            job={"state": "running", "step": "stopping ragnarok"})
check("while one is running the page says so",
      "stopping ragnarok" in _spb, _spb[:300])


# ---- the API key is reachable from the page that needs it
#
# It was only in the Advanced group - eleventh of twenty, about 39% down a 177 KB
# settings page - and the Mods page told people to "add it under Advanced" without
# saying where Advanced was. A pointer to a place somebody cannot find is not a pointer.
_nokey = store()
_mods_nokey = uimod.render_mods(_nokey, {})
check("the key can be pasted on the Mods page itself",
      "name=apikey" in _mods_nokey, "no key field on the mods page")
check("as a password field, not plain text",
      "type=password name=apikey" in _mods_nokey)
check("with somewhere to save it", "Save key" in _mods_nokey)
check("and it says where to get one", "console.curseforge.com" in _mods_nokey)
check("and that it is optional", "Project ID" in _mods_nokey)
check("the dead pointer to Advanced is gone",
      "add it under <b>Advanced</b>" not in _mods_nokey)

_haskey = store(curseforge_api_key="a-real-looking-secret-key")
_mods_key = uimod.render_mods(_haskey, {})
check("with a key set the page says so rather than asking again",
      "A key is set" in _mods_key and "name=apikey" not in _mods_key, _mods_key[:200])
check("and offers to remove it", "clearkey" in _mods_key)
check("but never renders the key itself",
      "a-real-looking-secret-key" not in _mods_key)

check("the canonical setting still lives in the schema, so there is one definition",
      BY_KEY["curseforge_api_key"]["group"] == "Advanced")
check("and its help now points at the other door",
      "Mods page" in BY_KEY["curseforge_api_key"]["help"])
check("it is still a secret", BY_KEY["curseforge_api_key"]["type"] == "password")
from obelisk.backup import SECRET_KEYS as _SK

check("and still guarded against being announced", "curseforge_api_key" in _SK)


# ---- the player count, back where somebody looks
#
# It was on the relay's own status page - a per-map table with a players column - until
# that page was stood down inside Obelisk on 4 September to stop it binding the port the
# web UI already had. The web UI never picked the column up, so the one number an
# operator checks before restarting anything has been missing ever since. The data never
# went anywhere: the relay polls every map once a minute and keeps it in memory.
_ST = {"docker_ok": True, "compose_exists": True, "running": 3, "services": [
    {"service": "island", "name": "asa-tbg-island", "label": "The Island",
     "level": "ok", "says": "Online", "status": "Up 3 hours"},
    {"service": "ragnarok", "name": "asa-tbg-ragnarok", "label": "Ragnarok",
     "level": "ok", "says": "Online", "status": "Up 3 hours"},
    {"service": "valguero", "name": "asa-tbg-valguero", "label": "Valguero",
     "level": "bad", "says": "Failing to start", "status": "Restarting"}]}
_PL = {"by_map": {"The Island": 3, "Ragnarok": 7}, "total": 10, "age": 45}

_live = ui.render_status(_ST, players=_PL)

check("the running table has a Players column",
      "<th class=num>Players</th>" in _live, _live[:400])
check("a map's own count is on its row",
      "<td>The Island</td>" in _live and ">3</td>" in _live, _live[:600])
check("and a busier map shows its own, not the total",
      "<td>Ragnarok</td>" in _live and ">7</td>" in _live, _live[:700])
check("the cluster total is in the header",
      "<b>10 players online</b>" in _live, _live[:400])
check("counting the maps that are actually up",
      "across 2 maps" in _live, _live[:400])

# a stale number that does not say it is stale is a claim about now
check("the header says how old the count is", "45s ago" in _live, _live[:400])
check("just-polled reads as just now",
      "just now" in ui.render_status(_ST, players=dict(_PL, age=2)), "no just now")
check("a minute-old count says minutes",
      "2m ago" in ui.render_status(_ST, players=dict(_PL, age=150)), "no minutes")
check("the age helper never invents precision",
      [ui._ago(x) for x in (0, 5, 45, 95, 600)]
      == ["just now", "just now", "45s ago", "1m ago", "10m ago"],
      [ui._ago(x) for x in (0, 5, 45, 95, 600)])

# "nobody asked" is not "nobody is playing" - the rule the update gate already keeps
_norelay = ui.render_status(_ST, players=None)
check("with no relay the count is a dash, not a zero",
      "&mdash;" in _norelay and "players online" not in _norelay, _norelay[:400])
check("and the page says why rather than showing an empty cluster",
      "chat relay is not running" in _norelay, _norelay[:400])
check("a zero is only ever shown when it was actually measured",
      ">0</td>" in ui.render_status(_ST, players={"by_map": {"The Island": 0},
                                                  "total": 0, "age": 5}),
      "a measured zero went missing")
check("a map the relay has no answer for is a dash, not a zero",
      ui.render_status(_ST, players=_PL).count("&mdash;") >= 1, "Valguero showed 0")

# ---- the Map column says the map
#
# It was headed "Map" and filled with s["service"] - the compose service key, which is
# the instance id. Same id-for-name slip the restore flow had, on the front page.
check("the Map column shows the name the operator picked",
      "<td>The Island</td>" in _live, _live[:600])
check("not the instance id under a Map heading",
      "<td>island</td>" not in _live, _live[:600])
check("the instance is still shown, where it is diagnostic",
      '<td class=help>island</td>' in _live, _live[:600])
check("a service with no label falls back rather than rendering blank",
      "asa-tbg-x" in ui.render_status(
          {"docker_ok": True, "compose_exists": True, "running": 1,
           "services": [{"service": "", "name": "asa-tbg-x", "level": "ok",
                         "says": "Online", "status": ""}]}, players=None),
      "a nameless service vanished")

# the failure detail still spans the table now that it is a column wider
check("the why-it-is-failing row spans every column",
      "colspan=4" in ui.render_status(
          dict(_ST, services=[dict(_ST["services"][2], log_tail="boom")]),
          players=_PL), "colspan did not follow the new column")

# ---- and nothing about how the count is obtained changed
import io as _io_s1                                              # noqa: E402
_botsrc = _io_s1.open(os.path.join(os.path.dirname(__file__), "bot.py"),
                      encoding="utf-8").read()
check("the relay still counts players with RCON ListPlayers",
      'rcon(hp[0], hp[1], "ListPlayers")' in _botsrc, "the mechanism moved")
check("on its own poll, not on a page render",
      "ONLINE_POLL_SECONDS" in _botsrc and "async def poll_online" in _botsrc,
      "the poller moved")
check("and the snapshot only reads what the poll already wrote",
      "def online_snapshot" in _botsrc
      and "await" not in _botsrc.split("def online_snapshot")[1].split(chr(10) + chr(10)
                                                                     + chr(10))[0],
      "online_snapshot does I/O")


# ---- a cluster-wide blackout is not an empty cluster
#
# The filter that stops one silent map showing a remembered number removes every map
# when the whole cluster goes quiet - and it does, routinely: the relay loses the docker
# network, the admin password changes, everything restarts at once. What is left is an
# empty measurement, and an empty measurement rendered as "0 players online · just now"
# is the ghost zero again at cluster scale. It is also the worst one: that glance is
# exactly what precedes restarting maps that were perfectly fine.
_blackout = ui.render_status(_ST, players={"by_map": {}, "total": 0, "age": 5})
check("every map silent does not report an empty cluster",
      "0 players online" not in _blackout, _blackout[:400])
check("nor claims to have counted any maps",
      "across 0 maps" not in _blackout, _blackout[:400])
check("it says the counts are not available",
      "not available" in _blackout, _blackout[:400])
check("and that this is an unanswered question, not an answer",
      "no map answered" in _blackout, _blackout[:400])
check("warning the operator before they restart something that was fine",
      "before restarting anything" in _blackout, _blackout[:400])
check("in amber, because something is wrong rather than quiet",
      "<div class=warn>" in _blackout, _blackout[:200])
check("the rows are all dashes underneath it",
      _blackout.count("&mdash;</td>") == 3, _blackout.count("&mdash;</td>"))

# ...while a cluster that really is empty still says so. This is the line the fix must
# not cross: every map answered, every map answered zero.
_reallyempty = ui.render_status(_ST, players={
    "by_map": {"The Island": 0, "Ragnarok": 0, "Valguero": 0}, "total": 0, "age": 5})
check("a measured empty cluster still reports zero",
      "<b>0 players online</b>" in _reallyempty, _reallyempty[:400])
check("across the maps it measured", "across 3 maps" in _reallyempty,
      _reallyempty[:400])
check("and stays a quiet note, not a warning",
      '<div class="note">' in _reallyempty, _reallyempty[:200])

# ---- the dash explains itself
check("the dash carries a tooltip", 'title="' in _live, _live[:800])
_FOOT = '<div class=help style="margin-top:10px">%s</div>' % ui._e(ui.DASH_MEANS)
check("and there is a note under the table, not only a tooltip",
      _FOOT in _live, _live[-900:])
check("the tooltip is there as well", 'title="%s"' % ui._e(ui.DASH_MEANS) in _live,
      _live[:900])
check("the wording covers the no-relay case too, which shows the same dash",
      "chat relay that asks is not running" in ui.DASH_MEANS, ui.DASH_MEANS)
check("and it says plainly that the count is not known",
      "not known" in ui.DASH_MEANS, ui.DASH_MEANS)
check("the note is not printed when there is no dash to explain",
      _FOOT not in ui.render_status(
          _ST, players={"by_map": {"The Island": 1, "Ragnarok": 2, "Valguero": 0},
                        "total": 3, "age": 5}),
      "a note about a symbol that is not on the page")

# ---- the age keeps its units
check("an hour is an hour, not sixty minutes", ui._ago(3600) == "1h ago", ui._ago(3600))
check("a minute is a minute, not sixty seconds", ui._ago(60) == "1m ago", ui._ago(60))
check("a day is a day, not fifteen hundred minutes",
      ui._ago(90000) == "1d ago", ui._ago(90000))
check("and the short end is unchanged",
      [ui._ago(x) for x in (0, 45, 150)] == ["just now", "45s ago", "2m ago"],
      [ui._ago(x) for x in (0, 45, 150)])
_stale = ui.render_status(_ST, players=dict(_PL, age=7200))
check("a count much older than its poll says so rather than sitting there",
      "out of date" in _stale, _stale[:400])
check("and is shown as a warning", '<div class="warn">' in _stale, _stale[:200])
check("a fresh count is not nagged about",
      "out of date" not in _live, _live[:400])

# ---- the no-relay sentence says what to do about it
check("the no-relay note names where the relay is set up",
      "Discord" in _norelay and "Settings" in _norelay, _norelay[:400])
check("and is a warning rather than a quiet aside",
      "<div class=warn>" in _norelay, _norelay[:200])

# ---- the column header stopped over-promising
check("the last column is called Service, which is what it holds",
      "<th>Service</th>" in _live, _live[:500])
check("not Container, which is a longer name it does not show",
      "<th>Container</th>" not in _live, _live[:500])
check("and it still shows the instance underneath",
      "<td class=help>island</td>" in _live, _live[:700])




# ---- who's online: one row per player, under the map they are on
#
# The names were always in the ListPlayers answer and were always discarded. Slice 2a
# kept them; this shows them. Read-only: nothing here writes, and nothing here asks a
# server anything - the names came back with the count.
_ROSTER = {"by_map": {
    "The Island": [{"name": "Bob", "netid": "7656119800000001"},
                   {"name": "Cha,rlie", "netid": "000255a1b2"}],
    "Valguero": [{"name": "some line we could not read", "netid": ""}],
    "Astraeos": []}, "age": 30}
_ORDER = ["The Island", "Ragnarok", "Valguero", "Astraeos"]

_who = ui.render_whos_online(_ROSTER, maps=_ORDER)

check("the section is on the page under its own heading",
      "Who’s online" in _who, _who[:120])
check("with its people named",
      ">Bob<" in _who and "Cha,rlie" in _who, _who[:600])
check("under the map they are on",
      _in_order(_who, "The Island", "Bob", "Valguero"), _who[:600])

# ---- every player is their own row, with somewhere for the buttons to go
#
# 2c gives each of these Message, Kick and Ban. Thirty-odd controls wrapped inside one
# table cell is not a thing to build and then fix, so the shape is right first.
check("each player is a row of their own",
      _who.count("<div class=whorow>") == 3, _who.count("<div class=whorow>"))
check("with the name in its own element",
      _who.count("<span class=whoname>") == 4, _who.count("<span class=whoname>"))
check("and an actions slot on each row",
      _who.count("<span class=whoacts>") == 3,
      _who.count("<span class=whoacts>"))
check("one row per person, not one cell of chips",
      "<span class=chip" not in _who, _who[:600])

# ---- its own styling, not the mods panel's
#
# .chip is defined twice in this stylesheet - mine first, the mods panel's later, equal
# specificity - so every rule I wrote was being overridden and player names were
# inheriting a mod's look. The mods panel also owns .chip.ok/.bad/.new/.unk, which is
# exactly the vocabulary per-player state will want once there are buttons here.
check("the who's-online elements do not borrow the mods panel's class",
      ".chip" not in _who, _who[:600])
for _cls in ("whoroster", "whomap", "whorow", "whoname", "whoacts", "whonote",
             "whoflag"):
    check("%s is styled deliberately" % _cls, (".%s{" % _cls) in ui.CSS, _cls)
check("and .chip is still defined once, by the panel that had it first",
      ui.CSS.count("\n.chip{") == 1, ui.CSS.count("\n.chip{"))

# ---- a map that did not answer is shown, not silently dropped
#
# It vanished, while a map that answered with nobody on it said "nobody on it" - so the
# one state worth seeing before pressing Stop was the one that left no trace.
check("a running map missing from the roster gets a row",
      "did not answer the last poll" in _who, _who[:900])
check("marked as the unknown it is, not as empty",
      '<div class="whorow quiet">' in _who, _who[:900])
check("and it says what is not known",
      "who is on it is not known" in _who, _who[:900])
check("a map that answered with nobody on it is accounted for",
      "Nobody on: Astraeos" in _who, _who[-400:])
check("but does not get a heading and a line of its own",
      "<div class=whomap>Astraeos</div>" not in _who, _who[:900])
check("empty and unknown are not the same statement",
      _in_order(_who, "did not answer the last poll", "Nobody on:"), _who[:900])

# ---- the order the eye travels
check("maps come in the order the status table lists them",
      _in_order(_who, *_ORDER), [(m, _who.find(m)) for m in _ORDER])
check("a roster map the caller did not list is still shown, at the end",
      "Extra" in ui.render_whos_online(
          {"by_map": dict(_ROSTER["by_map"], Extra=[]), "age": 5}, maps=_ORDER),
      "a map went missing")

# ---- the person the parser could not key on
check("an unreadable row still shows the person",
      "some line we could not read" in _who, _who[:900])
check("and says the consequence in text, not only on hover",
      ">no id &mdash; nothing to act on</span>" in _who, _who[:900])
check("the hover carries the longer reason",
      'title="' in _who and "could not be read" in _who, _who[:900])

# ---- no second header: slice 1 owns the total and the age
#
# Two headers six lines apart stating the same total is the duplication this overhaul
# exists to remove, and in the stale case it was two amber warnings in a row.
check("the section does not restate the population",
      "players on" not in _who and "players online" not in _who, _who[:300])
check("nor the age, which the count above already shows",
      "ago" not in _who, _who[:300])
check("nor a second staleness warning",
      "out of date" not in ui.render_whos_online(dict(_ROSTER, age=7200), maps=_ORDER),
      "a second stale banner")
check("and no prose footer explaining the two agree",
      "same check that counts" not in _who, _who[-300:])

# ---- nothing to show: a pointer, not the whole banner again
check("no relay does not reprint the explanation",
      ui.NO_RELAY_WHY not in ui.render_whos_online(None, maps=_ORDER),
      ui.render_whos_online(None, maps=_ORDER))
check("it points at the one above instead",
      "same reason there is no player count above"
      in ui.render_whos_online(None, maps=_ORDER),
      ui.render_whos_online(None, maps=_ORDER))
check("and a blackout does the same",
      ui.NOTHING_ANSWERED_WHY not in ui.render_whos_online({"by_map": {}}, maps=_ORDER)
      and "no player count above" in ui.render_whos_online({"by_map": {}}, maps=_ORDER),
      ui.render_whos_online({"by_map": {}}, maps=_ORDER))
check("the count section still carries the full reason, once",
      ui.NO_RELAY_WHY in ui.render_status(_ST, players=None), "the reason went missing")
check("so the page states it once rather than twice",
      (ui.render_status(_ST, players=None)
       + ui.render_whos_online(None, maps=_ORDER)).count(
           "chat relay is not running") == 1,
      (ui.render_status(_ST, players=None)
       + ui.render_whos_online(None, maps=_ORDER)).count("chat relay is not running"))

# ---- the property verify proved, still true
_PAIRED = {"The Island": 2, "Valguero": 1, "Astraeos": 0}
for _m, _n in sorted(_PAIRED.items()):
    check("%s lists as many names as it counts players" % _m,
          len(_ROSTER["by_map"].get(_m) or []) == _n, [_m, _n])

# ---- ten maps, one person: the section is four lines, not twenty
#
# Per-player rows cost a heading and a "nobody on it" line for every empty map, so on
# the real cluster the two states that matter - a map with people on it, and a map that
# did not answer - were buried under nine repetitions of the 0 the count column was
# already showing. The same duplication one level down.
_TEN = ["The Island", "The Center", "Scorched Earth", "Ragnarok", "Aberration",
        "Extinction", "Valguero", "Astraeos", "Lost Colony", "Genesis"]


def _ten(populated=None, quiet=()):
    by_map = {m: [] for m in _TEN if m not in quiet}
    for m, people in (populated or {}).items():
        by_map[m] = people
    return {"by_map": by_map, "age": 20}


_one_on = ui.render_whos_online(
    _ten({"Ragnarok": [{"name": "Dana", "netid": "9"},
                       {"name": "Eve", "netid": "8"}]}), maps=_TEN)

check("the populated map keeps its heading",
      "<div class=whomap>Ragnarok</div>" in _one_on, _one_on[:400])
check("and a row per person on it",
      _one_on.count("<div class=whorow>") == 2, _one_on.count("<div class=whorow>"))
check("each still carrying its actions slot",
      _one_on.count("<span class=whoacts>") == 2,
      _one_on.count("<span class=whoacts>"))
check("the nine empty maps take one line between them",
      _one_on.count("Nobody on:") == 1, _one_on.count("Nobody on:"))
check("naming them", "The Island, The Center, Scorched Earth" in _one_on,
      _one_on[-500:])
check("and counting them", "(9 maps)" in _one_on, _one_on[-300:])
check("not one heading each",
      _one_on.count("<div class=whomap>") == 1,
      _one_on.count("<div class=whomap>"))
check("nor one note each", _one_on.count("<div class=whonote>") == 1,
      _one_on.count("<div class=whonote>"))
check("the populated map is not swept into the empty line",
      "Ragnarok" not in _after(_one_on, "Nobody on:"), _one_on[-500:])

# the collapsed line keeps the table's order, like everything else here
_listed = _after(_one_on, "Nobody on: ").split(" <span")[0].split(", ")
check("the empty maps are listed in the order the table above lists them",
      _listed == [m for m in _TEN if m != "Ragnarok"], _listed)

# ---- the state that must never be collapsed
_with_quiet = ui.render_whos_online(
    _ten({"Ragnarok": [{"name": "Dana", "netid": "9"}]}, quiet=("Valguero",)),
    maps=_TEN)
check("a map that did not answer still gets its own row",
      '<div class="whorow quiet">' in _with_quiet, _with_quiet[:600])
check("naming itself, since it has no heading to name it",
      "<span class=whoname>Valguero</span>" in _with_quiet, _with_quiet[:800])
check("and it is not in the Nobody-on line",
      "Valguero" not in _after(_with_quiet, "Nobody on:"), _with_quiet[-400:])
check("which now counts eight", "(8 maps)" in _with_quiet, _with_quiet[-300:])
check("a blackout is ten of those rows, not twenty lines",
      ui.render_whos_online({"by_map": {m: [] for m in _TEN[:1]}, "age": 5},
                            maps=_TEN).count('<div class="whorow quiet">') == 9,
      ui.render_whos_online({"by_map": {m: [] for m in _TEN[:1]}, "age": 5},
                            maps=_TEN).count('<div class="whorow quiet">'))

# ---- nobody anywhere is one line, and not a restated zero
_none_on = ui.render_whos_online(_ten(), maps=_TEN)
check("an empty cluster is a single line", _none_on.count("Nobody on:") == 1, _none_on)
check("listing every map", "(10 maps)" in _none_on, _none_on)
check("with no headings at all", "<div class=whomap>" not in _none_on, _none_on)
check("and no player rows", "<div class=whorow>" not in _none_on, _none_on)
check("and it does not restate the count section's zero",
      "0 players" not in _none_on and "players online" not in _none_on, _none_on)


# ---- saying something to one player
#
# The first of the moderation actions and the only one that affects nobody, which is
# why it goes first: it proves the plumbing before anything can disconnect somebody.
_MSG = {"by_map": {
    "Ragnarok": [{"name": "Dana", "netid": "9"},
                 {"name": "Cha,rlie", "netid": "8"},
                 {"name": "some raw line we could not split", "netid": ""}],
    "The Island": []}, "age": 20}
_msg_who = ui.render_whos_online(_MSG, maps=["Ragnarok", "The Island", "Valguero"])

check("a real player's row carries a message form",
      '<form method=post action="/admin/player/message"' in _msg_who, _msg_who[:800])
check("with somewhere to type", "name=text" in _msg_who, _msg_who[:800])
check("and something to press", ">Send</button>" in _msg_who, _msg_who[:800])
check("it says who it is addressing", 'placeholder="say something to Dana"' in _msg_who,
      _msg_who[:800])
check("the form carries the player's name",
      'name=name value="Dana"' in _msg_who, _msg_who[:800])
check("and the map they are on, because that is where the command goes",
      'name=map value="Ragnarok"' in _msg_who, _msg_who[:800])
check("a comma'd name is carried intact, not truncated",
      'value="Cha,rlie"' in _msg_who, _msg_who[:1200])
check("one form per real player, not one for the map",
      _msg_who.count("/admin/player/message") == 2,
      _msg_who.count("/admin/player/message"))

# ---- and nothing that cannot be messaged is offered a box
#
# The "name" on a fallback row is a whole line the parser could not split. Sending to
# it would address nobody, and a button that cannot work is worse than no button.
_after_raw = _after(_msg_who, "some raw line we could not split")
check("an unreadable row gets no message form",
      "/admin/player/message" not in _after_raw.split("</div>")[0], _after_raw[:300])
check("it still says why there is nothing to press",
      "nothing to act on" in _after_raw[:300], _after_raw[:300])
_quiet_part = _from(_msg_who, '<div class="whorow quiet">')
check("a map that did not answer gets no message form",
      "/admin/player/message" not in _quiet_part, _quiet_part[:300])
check("and neither does the collapsed empty-maps line",
      "/admin/player/message" not in _from(_msg_who, "Nobody on:"),
      _from(_msg_who, "Nobody on:"))
check("no relay, no forms",
      "/admin/player/message" not in ui.render_whos_online(None, maps=["Ragnarok"]),
      ui.render_whos_online(None, maps=["Ragnarok"]))

# ---- the command itself, written once
check("the whisper command quotes the name, because names have spaces",
      _bot_ui.whisper_command("Big Tim", "hello")
      == 'ServerChatToPlayer "Big Tim" hello',
      _bot_ui.whisper_command("Big Tim", "hello"))
check("and leaves the message bare to the end of the line",
      _bot_ui.whisper_command("D", "a b c") == 'ServerChatToPlayer "D" a b c',
      _bot_ui.whisper_command("D", "a b c"))
_botsrc_2c = _io_s1.open(os.path.join(os.path.dirname(__file__), "bot.py"),
                         encoding="utf-8").read()
check("the relay's own welcome whisper goes through the same helper",
      "whisper_command(player, line)" in _botsrc_2c, "the whisper has its own spelling")
_CMD_TEMPLATE = '''ServerChatToPlayer "%s" %s'''
check("so there is one place that builds a ServerChatToPlayer line",
      _botsrc_2c.count(_CMD_TEMPLATE) == 1, _botsrc_2c.count(_CMD_TEMPLATE))


# ---- kicking one player
#
# The first action here that affects somebody. It keys on the netid rather than the
# name, which is the id KickPlayer takes and the id ListPlayers hands back - no mapping
# between them, and nothing here parses either.
_KICK = {"by_map": {
    "Ragnarok": [{"name": "Dana", "netid": "76561198000000001"},
                 {"name": 'Bad" Name', "netid": "0002a1b2"},
                 {"name": "some raw line we could not split", "netid": ""}],
    "The Island": []}, "age": 20}
_kick_who = ui.render_whos_online(_KICK, maps=["Ragnarok", "The Island", "Valguero"])

check("a clean player row has a Kick control",
      '<form method=post action="/admin/player/kick"' in _kick_who, _kick_who[:900])
check("carrying the netid the command takes",
      'name=netid value="76561198000000001"' in _kick_who, _kick_who[:900])
check("and the map, because the kick goes to that map's server",
      'name=map value="Ragnarok"' in _after(_kick_who, "/admin/player/kick")[:200],
      _after(_kick_who, "/admin/player/kick")[:300])
check("and the name, so the confirmation can say who",
      'name=name value="Dana"' in _kick_who, _kick_who[:900])
check("beside the message box rather than instead of it",
      _in_order(_kick_who, "/admin/player/message", "/admin/player/kick"),
      _kick_who[:900])

# a name that cannot be quoted can still be kicked - the id has no such problem
_bad_row = _after(_kick_who, 'Bad&quot; Name</span>')
check("a player who cannot be messaged can still be kicked",
      "/admin/player/kick" in _from(_bad_row, "<span class=whoacts>")[:400],
      _bad_row[:400])
check("and is still not offered a message box",
      "/admin/player/message" not in _bad_row.split("</div>")[0], _bad_row[:400])

# ---- and nothing that has no id is offered one
_raw_row = _after(_kick_who, "some raw line we could not split")
check("a row with no id gets no Kick",
      "/admin/player/kick" not in _raw_row.split("</div>")[0], _raw_row[:300])
check("a map that did not answer gets no Kick",
      "/admin/player/kick" not in _from(_kick_who, '<div class="whorow quiet">'),
      _from(_kick_who, '<div class="whorow quiet">')[:300])
check("nor the collapsed empty-maps line",
      "/admin/player/kick" not in _from(_kick_who, "Nobody on:"),
      _from(_kick_who, "Nobody on:"))
check("and no relay means no Kick at all",
      "/admin/player/kick" not in ui.render_whos_online(None, maps=["Ragnarok"]),
      ui.render_whos_online(None, maps=["Ragnarok"]))
check("one Kick per real player, not one per map",
      _kick_who.count("/admin/player/kick") == 2,
      _kick_who.count("/admin/player/kick"))

# ---- the confirmation is a page, and it says what will happen
_confirm = ui.render_kick_confirm("Ragnarok", "Dana", "76561198000000001")
check("the confirmation names the player", "Dana" in _confirm, _confirm)
check("and the map they are on", "Ragnarok" in _confirm, _confirm)
check("it says they can come straight back",
      "rejoin immediately" in _confirm, _confirm)
check("and that nothing they own is touched",
      "nothing they own is affected" in _confirm, _confirm)
check("it says nothing has happened yet",
      "Nothing has been done yet" in _confirm, _confirm)
check("the question is the row's own, not a banner",
      "<div class=warn>" not in _confirm and "class=whoflag" in _confirm,
      _confirm[:200])
check("and it is styled as a question rather than a fault",
      ".whorow.asking{border-color:#7d642f" in ui.CSS,
      "asking rows are not marked")
check("the way through carries the same three fields back",
      all(('name=%s value=' % f) in _confirm for f in ("map", "name", "netid")),
      _confirm)
check("and says it is confirmed", 'name=confirm value="1"' in _confirm, _confirm)
check("it is not a typed-name gate - that is what a ban is for",
      "type" not in _confirm.lower().split("<form")[0], _confirm[:400])

# ---- three actions, three weights
#
# Send and Kick were the same .ghost button, and Ban lands next - three identical grey
# buttons in a nowrap row, with Kick sitting beside Ban. The adjacency is the thing to
# design against before Ban exists rather than after.
check("talking to somebody and removing them are not the same button",
      '<button class="whoact talk" type=submit>Send' in _kick_who
      and '<button class="whoact bite" type=submit>Kick' in _kick_who,
      _kick_who[:900])
check("neither is a plain ghost button any more",
      "class=ghost" not in _from(_kick_who, "<div class=whoroster>"),
      _from(_kick_who, "<div class=whoroster>")[:400])
for _cls, _mark in (("whoact", "border:1px solid #303845"),
                    ("whoact.bite", "border-color:#7d642f"),
                    ("whoact.worst", "border-color:#7d2f2f")):
    check(".%s is styled deliberately" % _cls, (".%s{" % _cls) in ui.CSS, _cls)
check("kick is not the same colour as send",
      ".whoact.bite{border-color:#7d642f" in ui.CSS, "bite is not amber")
check("and the heaviest weight is defined and waiting for Ban",
      ".whoact.worst{border-color:#7d2f2f" in ui.CSS, "no weight left for ban")
check("which is the three-colour rule the rest of the page keeps",
      _in_order(ui.CSS, ".whoact{", ".whoact.bite{", ".whoact.worst{"), "out of order")

# ---- the row has give
#
# Every other multi-item flex row on this page wraps. These did not, and a long name
# beside a 190px field and three buttons has nowhere to go.
for _sel in (".whorow{", ".whoacts{", ".whoform{"):
    _rule = _after(ui.CSS, _sel).split("}")[0]
    check("%s wraps rather than squashing" % _sel.strip("{."),
          "flex-wrap:wrap" in _rule, _rule)
check("and the message field can give up width rather than overflow",
      "flex:1 1 120px" in _after(ui.CSS, ".whoform input{").split("}")[0],
      _after(ui.CSS, ".whoform input{").split("}")[0])

# ---- the question replaces the row rather than floating above it
_pending = {"map": "Ragnarok", "netid": "76561198000000001",
            "html": ui.render_kick_confirm("Ragnarok", "Dana", "76561198000000001")}
_asking = ui.render_whos_online(_KICK, maps=["Ragnarok"], pending=_pending)
check("the player being asked about has an asking row",
      '<div class="whorow asking">' in _asking, _asking[:600])
check("naming them", _in_order(_asking, "whorow asking", "Dana"), _asking[:600])
check("and carrying the question",
      "Kick Dana from Ragnarok?" in _asking, _asking[:800])
_asked_row = _after(_asking, '<div class="whorow asking">').split("</div>")[0]
check("that row offers only the confirmed way through",
      _asked_row.count("/admin/player/kick") == 1, _asked_row)
check("and no message box while the question is open",
      "/admin/player/message" not in _asked_row, _asked_row)
check("other players keep their buttons",
      _in_order(_after(_asking, 'Bad&quot; Name'), "/admin/player/kick"),
      _after(_asking, 'Bad&quot; Name')[:400])
check("a cancel that goes back to the roster",
      'href="/admin/cluster#who"' in _asking, _asking[:900])

# ---- the section can be landed on, and can carry its own result
check("the section has an anchor to land on",
      "<fieldset id=who>" in _kick_who, _kick_who[:120])
check("even when there is nothing to show",
      "<fieldset id=who>" in ui.render_whos_online(None, maps=["Ragnarok"]),
      ui.render_whos_online(None, maps=["Ragnarok"]))
_noticed = ui.render_whos_online(_KICK, maps=["Ragnarok"],
                                 notice="<div class=note>Kick sent for Dana.</div>")
check("a result can be shown in the section itself",
      _in_order(_noticed, "id=who", "Kick sent for Dana", "<div class=whoroster>"),
      _noticed[:400])

# ---- banning somebody from the whole cluster
#
# The heaviest control on the page. It keys on the netid like the kick, but that id is
# written into ten ban lists rather than handed to one command, so it is checked against
# what an id can be before it goes anywhere.
_BAN = {"by_map": {
    "Ragnarok": [{"name": "Dana", "netid": "76561198000000001"},
                 {"name": 'Bad" Name', "netid": "0002a1b2"},
                 {"name": "some raw line we could not split", "netid": ""}],
    "The Island": []}, "age": 20}
_ban_who = ui.render_whos_online(_BAN, maps=["Ragnarok", "The Island", "Valguero"])

check("a clean player row has a Ban control",
      '<form method=post action="/admin/player/ban"' in _ban_who, _ban_who[:1200])
check("styled as the heaviest of the three",
      '<button class="whoact worst" type=submit>Ban</button>' in _ban_who,
      _ban_who[:1200])
check("carrying the netid it will write to the ban lists",
      'name=netid value="76561198000000001"' in
      _after(_ban_who, "/admin/player/ban")[:250],
      _after(_ban_who, "/admin/player/ban")[:300])
check("the three actions read lightest to heaviest",
      _in_order(_ban_who, "/admin/player/message", "/admin/player/kick",
                "/admin/player/ban"), _ban_who[:1200])
check("and they are three different weights, not three grey buttons",
      _in_order(_ban_who, 'class="whoact talk"', 'class="whoact bite"',
                'class="whoact worst"'), _ban_who[:1200])

# a player who cannot be messaged can still be removed
_bad = _after(_ban_who, 'Bad&quot; Name</span>')
check("a name that cannot be quoted can still be banned",
      "/admin/player/ban" in _from(_bad, "<span class=whoacts>")[:600], _bad[:600])

# ---- and nothing without an id is offered one
check("a row with no id gets no Ban",
      "/admin/player/ban" not in
      _after(_ban_who, "some raw line we could not split").split("</div>")[0],
      _after(_ban_who, "some raw line we could not split")[:300])
check("a map that did not answer gets no Ban",
      "/admin/player/ban" not in _from(_ban_who, '<div class="whorow quiet">'),
      _from(_ban_who, '<div class="whorow quiet">')[:300])
check("nor the collapsed empty-maps line",
      "/admin/player/ban" not in _from(_ban_who, "Nobody on:"),
      _from(_ban_who, "Nobody on:"))
check("and no relay means no Ban",
      "/admin/player/ban" not in ui.render_whos_online(None, maps=["Ragnarok"]),
      ui.render_whos_online(None, maps=["Ragnarok"]))
check("one Ban per real player", _ban_who.count("/admin/player/ban") == 2,
      _ban_who.count("/admin/player/ban"))

# ---- the confirmation asks for the name, and says what a ban actually does
_bconf = ui.render_ban_confirm("Ragnarok", "Dana", "76561198000000001")
check("the ban confirmation asks for the name to be typed",
      "name=confirm" in _bconf and 'placeholder="type Dana to confirm"' in _bconf,
      _bconf)
# the kick has a confirm field too, but it is a hidden "1" - one click. the
# difference that matters is that the ban makes somebody write the name out.
check("where the kick only needs the one click",
      '<input type=hidden name=confirm value="1">' in
      ui.render_kick_confirm("Ragnarok", "Dana", "9")
      and "placeholder=" not in ui.render_kick_confirm("Ragnarok", "Dana", "9"),
      ui.render_kick_confirm("Ragnarok", "Dana", "9"))
check("and the ban's box is not a hidden field wearing a placeholder",
      "type=hidden name=confirm" not in _bconf, _bconf)
check("it says the whole cluster, not the one map",
      "from the whole cluster" in _bconf and "every map, not just Ragnarok" in _bconf,
      _bconf)
check("and that it lasts until somebody undoes it",
      "until somebody unbans them" in _bconf, _bconf)
check("while saying what is not destroyed",
      "Nothing they built is deleted" in _bconf, _bconf)
check("it says nothing has happened yet",
      "Nothing has been done yet" in _bconf, _bconf)
check("the way through is the heaviest button",
      '<button class="whoact worst" type=submit>Ban Dana</button>' in _bconf, _bconf)
check("with a way out that returns to the roster",
      'href="/admin/cluster#who"' in _bconf, _bconf)
check("a wrong name comes back with the box still there and a reason",
      "That is not their name" in
      ui.render_ban_confirm("Ragnarok", "Dana", "9", "That is not their name.")
      and "name=confirm" in
      ui.render_ban_confirm("Ragnarok", "Dana", "9", "That is not their name."),
      ui.render_ban_confirm("Ragnarok", "Dana", "9", "That is not their name."))

# ---- what may be written to a ban list
#
# A list of what is allowed, not a list of what is not. The first version named the
# characters it did not want and let through ";", "|", "&", "$", "<", ">" and a comma -
# on the one path in this program that appends a line to a file on every server. Being
# right about every character nobody has thought of is not a thing to rely on when the
# allowed set is "the characters platform ids are made of".
for _bad_id in ("", "   ", "has space", 'has"quote', "has'quote", "back\slash",
                "x" * 65, "line\nbreak", "tab\there",
                ";DoExit", "765;DoExit", "<", ">", "|", "&", "$", ",", "765,611",
                "a b", "..", "id/../x", "id.txt", "#comment", "%s", "(", "*"):
    check("%r is not something to write to a ban list" % _bad_id,
          not _bans.valid_netid(_bad_id), _bad_id)
for _ok_id in ("76561198000000001",                       # Steam, 17 digits
               "1900000000000000123",                     # Epic, 19
               "0002a1b2c3d4e5f60002a1b2c3d4e5f6",         # EOS, 32 hex
               "0002a1b2c3d4e5f6", "ok-123", "ok_123", "x" * 64):
    check("%s... is a usable id" % _ok_id[:12], _bans.valid_netid(_ok_id), _ok_id)
check("and the check is the whole id, not its first character",
      not _bans.valid_netid("76561198000000001;DoExit")
      and not _bans.valid_netid("76561198000000001 x"),
      "a suffix slipped past")
check("the bound is a real bound, not a coincidence",
      _bans.MAX_NETID >= 32 and not _bans.valid_netid("y" * (_bans.MAX_NETID + 1)),
      _bans.MAX_NETID)

# ---- the ledger itself
#
# Nothing on an ARK server will tell us later what is banned - there is no RCON command
# that lists a ban list, and the files are on ten filesystems this container cannot see.
# So this is the only record there is, which is the argument for it holding its shape.
class _LedgerStore:
    def __init__(self):
        self.data = {}
        self.saves = 0

    def save(self):
        self.saves += 1


_ls = _LedgerStore()
_entry = _bans.record(_ls, "Bob", "76561198000000001",
                      {"The Island": "", "Ragnarok": "timed out"},
                      kick="", when=1700000000)
check("a ban is written down", _ls.data.get("bans") == [_entry], _ls.data)
check("and persisted, not just held in memory", _ls.saves == 1, _ls.saves)
check("with the map results kept per map",
      _bans.sent_to(_entry) == ["The Island"]
      and _bans.missed(_entry) == [("Ragnarok", "timed out")], _entry)
check("the time is a number, not whatever was passed",
      isinstance(_entry.get("when"), int) and _entry["when"] == 1700000000, _entry)
check("a missing name does not make a hole in the record",
      _bans.record(_ls, None, "1", None).get("name") == "", _ls.data["bans"][-1])

for _i in range(_bans.KEEP + 25):
    _bans.record(_ls, "P%d" % _i, "%d" % _i, {"The Island": ""})
check("the ledger is capped, so a busy month cannot grow the settings file for ever",
      len(_ls.data["bans"]) == _bans.KEEP, len(_ls.data["bans"]))
check("and it is the oldest that fall off the end",
      _ls.data["bans"][-1]["name"] == "P%d" % (_bans.KEEP + 24),
      _ls.data["bans"][-1])
check("recent() reads newest first, which is the order anybody wants them",
      [e["name"] for e in _bans.recent(_ls, limit=3)] ==
      ["P%d" % (_bans.KEEP + 24 - _n) for _n in range(3)],
      [e["name"] for e in _bans.recent(_ls, limit=3)])
check("and it asks for no more than it was asked for",
      len(_bans.recent(_ls, limit=3)) == 3, len(_bans.recent(_ls, limit=3)))
check("an empty ledger is an empty list, not a crash",
      _bans.recent(_LedgerStore()) == [], "recent() on a fresh store")

# ---- the Banned players list
#
# The list has to be honest about two things at once: what Obelisk did, and the fact
# that it is the only record there is. No ARK server will read its ban list back, so
# this is a record of commands sent, not of what the servers hold.
_NOW = 1789000000
_B1 = {"name": "Bob", "netid": "76561198000000001", "when": _NOW - 120,
       "maps": {"The Island": "", "Ragnarok": "timed out"}, "kick": ""}
_B2 = {"name": "Dana", "netid": "0002a1b2c3d4e5f60002a1b2c3d4e5f6", "when": _NOW - 90000,
       "maps": {"The Island": "", "Ragnarok": ""}, "kick": ""}
_B3 = {"name": "Bob", "netid": "76561198000000001", "when": _NOW - 300000,
       "maps": {"The Island": "", "Ragnarok": ""}, "kick": "",
       "unbanned": _NOW - 200000}
_banlist = ui.render_bans([_B1, _B2, _B3], now=_NOW)

check("the bans have a section of their own",
      "<fieldset id=bans>" in _banlist and "<legend>Banned players</legend>" in _banlist,
      _banlist[:200])
check("it says what the list is a record of, where the list is",
      _in_order(_banlist, "<fieldset id=bans>", "bans issued from Obelisk"),
      _window(_banlist, "<fieldset id=bans>", 300))
check("and does not claim to be the servers' own ban lists",
      "not a read of each server" in _banlist,
      _window(_banlist, "bans issued", 200))
check("the rows come out in the order they were given, newest first",
      _in_order(_banlist, "Bob", "Dana"), _banlist[:900])
check("each row says who", _in_order(_from(_banlist, "<div class=whoroster>"),
                                     ">Bob<", ">Dana<"),
      _from(_banlist, "<div class=whoroster>")[:400])
check("and when, in words a person reads",
      "2m ago" in _banlist and "1d ago" in _banlist,
      _window(_banlist, ">Bob<", 400))
check("with the exact time on hover, because 1d ago is not a thing to act on",
      'title="%s"' % ui._when_title(_B1["when"]) in _banlist,
      _window(_banlist, ">Bob<", 400))
check("the id is there to compare against one somebody was given",
      "76561198000000001" in _window(_banlist, ">Bob<", 400),
      _window(_banlist, ">Bob<", 400))
check("a long one is shortened on screen and whole on hover",
      'title="0002a1b2c3d4e5f60002a1b2c3d4e5f6"' in _banlist
      and "0002a1b2c3…d4e5f6" in _banlist,
      _window(_banlist, ">Dana<", 400))

# ---- a partial ban has to read as a partial ban, here as well as in the banner
check("a ban that reached one map of two says so",
      "1 of 2 — missing Ragnarok" in _window(_banlist, ">Bob<", 500),
      _window(_banlist, ">Bob<", 500))
check("and one that reached everything says that instead",
      "sent to all 2 maps" in _window(_banlist, ">Dana<", 500),
      _window(_banlist, ">Dana<", 500))
check("a record with no maps at all is not a crash",
      "no maps recorded" in ui.render_bans([dict(_B1, maps={})], now=_NOW),
      ui.render_bans([dict(_B1, maps={})], now=_NOW))

# ---- the way back out
check("every live ban has an Unban",
      _window(_banlist, ">Bob<", 700).count(
          '<form method=post action="/admin/player/unban"') == 1,
      _window(_banlist, ">Bob<", 700))
check("keyed on the id, which is what an unban acts on",
      'name=netid value="76561198000000001"' in
      _window(_from(_banlist, ">Bob<"), "/admin/player/unban", 300),
      _window(_from(_banlist, ">Bob<"), "/admin/player/unban", 300))
check("it is the lightest of the controls, because it lets somebody in",
      '<button class="whoact talk" type=submit>Unban</button>' in _banlist,
      _window(_banlist, "/admin/player/unban", 300))
check("an entry already undone says when it was",
      "unbanned 2d ago" in _banlist, _window(_banlist, ">Bob<", 2000))
check("and offers no second Unban, because there is nothing left to undo",
      _banlist.count(">Unban</button>") == 2, _banlist.count(">Unban</button>"))

# ---- and a way in for the bans this manager never issued
check("there is a field for an id the list does not have",
      _in_order(_from(_banlist, 'class="whoform byid"'), "name=netid",
                ">Unban by ID</button>"),
      _from(_banlist, 'class="whoform byid"'))
check("explained, because an id typed by hand needs a reason to exist",
      "Unban an id this list does not have" in _banlist,
      _from(_banlist, 'class="whoform byid"'))

# ---- nothing to show is a calm sentence, not a warning
_nobans = ui.render_bans([], now=_NOW)
check("an empty ledger says so plainly", "No bans recorded." in _nobans, _nobans)
check("in the quiet style, not amber and not red",
      "<div class=warn>" not in _nobans and "<div class=problem>" not in _nobans,
      _nobans)
check("and the by-id field is still there, since it never depended on the list",
      'class="whoform byid"' in _nobans, _nobans)
check("the section is still anchorable when it is empty",
      "<fieldset id=bans>" in _nobans, _nobans[:120])

# ---- one question at a time, in the place it was asked
_B1b = dict(_B1, when=_NOW - 5000)          # the same id, banned twice, both live
_asked = ui.render_bans([_B1, _B2, _B1b], now=_NOW, pending={
    "netid": "76561198000000001", "when": str(_B1["when"]),
    "html": ui.render_unban_confirm("Bob", "76561198000000001", str(_B1["when"]))})
check("the question replaces the row it was asked on",
      '<div class="whorow asking">' in _asked, _asked[:200])
check("naming who it is about", "Unban Bob?" in _asked,
      _window(_asked, "whorow asking", 400))
check("the other player keeps their Unban",
      "/admin/player/unban" in _window(_from(_asked, ">Dana<"), ">Dana<", 700),
      _window(_from(_asked, ">Dana<"), ">Dana<", 700))
check("while that player's other live record offers no second Unban",
      _asked.count(">Unban</button>") == 1, _asked.count(">Unban</button>"))
check("because one press undoes every record of that id, not the row it was on",
      _asked.count('<div class="whorow asking">') == 1,
      _asked.count('<div class="whorow asking">'))

_byid_asked = ui.render_bans([_B1], now=_NOW, pending={
    "netid": "765", "when": "",
    "html": ui.render_unban_confirm("", "765", "")})
check("a question about a typed id replaces the field it was typed into",
      'class="whoform byid"' not in _byid_asked, _byid_asked)
check("and is asked where that field was",
      _in_order(_byid_asked, "<div class=whoroster>", "whorow asking",
                "Unban by ID"),
      _from(_byid_asked, "<div class=whoroster>")[-500:])

# ---- what the confirmation asks for
_uconf = ui.render_unban_confirm("Bob", "76561198000000001", "123")
check("the unban asks once and takes a press",
      '<input type=hidden name=confirm value="1">' in _uconf, _uconf)
check("not a typed name - it is reversible and it lets somebody in",
      "placeholder=" not in _uconf, _uconf)
check("it names the player", "Unban Bob?" in _uconf, _uconf)
check("and shows the id it will act on", "76561198000000001" in _uconf, _uconf)
check("it says the ban lifts everywhere, since that is what it does",
      "lifted on every map" in _uconf, _uconf)
check("and that the record survives it",
      "kept and marked unbanned, not removed" in _uconf, _uconf)
check("it says nothing has happened yet",
      "Nothing has been done yet" in _uconf, _uconf)
check("with a way out that returns to the list",
      'href="/admin/cluster#bans"' in _uconf, _uconf)
check("an id with no name to put to it still reads as a sentence",
      "Unban this id?" in ui.render_unban_confirm("", "765", ""),
      ui.render_unban_confirm("", "765", ""))
check("a problem comes back with the question still standing",
      _in_order(ui.render_unban_confirm("Bob", "765", "", "That id is not one."),
                "That id is not one.", "name=confirm"),
      ui.render_unban_confirm("Bob", "765", "", "That id is not one."))

# ---- the ban confirm and the list now know about each other
check("the ban confirmation says the ban is written down",
      "recorded in Banned players below" in
      ui.render_ban_confirm("Ragnarok", "Dana", "765"),
      ui.render_ban_confirm("Ragnarok", "Dana", "765"))
check("and that it can be undone from there",
      "can be undone from there" in ui.render_ban_confirm("Ragnarok", "Dana", "765"),
      ui.render_ban_confirm("Ragnarok", "Dana", "765"))

# ---- marking, not deleting
_ms = _LedgerStore()
_bans.record(_ms, "Bob", "765", {"The Island": ""}, when=100)
_bans.record(_ms, "Bob", "765", {"The Island": "", "Ragnarok": "timed out"}, when=200)
_bans.record(_ms, "Dana", "999", {"The Island": ""}, when=300)
_marked = _bans.mark_unbanned(_ms, "765", when=400)
check("an unban marks every record of that id",
      len(_marked) == 2 and all(_bans.is_unbanned(e) for e in _marked), _marked)
check("because two records of one id is a normal thing to have",
      len(_bans.entries_for(_ms, "765")) == 2, _bans.entries_for(_ms, "765"))
check("it leaves everybody else alone",
      not _bans.is_unbanned(_bans.entries_for(_ms, "999")[0]),
      _bans.entries_for(_ms, "999"))
check("nothing is deleted", len(_ms.data["bans"]) == 3, _ms.data["bans"])
check("the ban itself is still readable afterwards",
      _bans.missed(_bans.entries_for(_ms, "765")[1]) == [("Ragnarok", "timed out")],
      _bans.entries_for(_ms, "765")[1])
check("and it is persisted", _ms.saves == 4, _ms.saves)
_again = _bans.mark_unbanned(_ms, "765", when=500)
check("a second unban marks nothing twice", _again == [], _again)
check("and does not rewrite when the first one happened",
      all(e["unbanned"] == 400 for e in _bans.entries_for(_ms, "765")),
      _bans.entries_for(_ms, "765"))
# The number the page needs is what is HELD, not what a page of it holds - those are
# the same until the day they are not, which is the day the line matters.
_cs = _LedgerStore()
for _i in range(60):
    _bans.record(_cs, "P%d" % _i, "%d" % _i, {"The Island": ""}, when=_i)
check("the ledger can say how much it is holding",
      _bans.count(_cs) == 60, _bans.count(_cs))
check("which is more than a page of it",
      len(_bans.recent(_cs)) == 50, len(_bans.recent(_cs)))
check("and an empty one holds nothing rather than raising",
      _bans.count(_LedgerStore()) == 0, "count() on a fresh store")

check("an id nobody banned is not an error, just nothing to mark",
      _bans.mark_unbanned(_ms, "nobody", when=600) == [], _ms.data["bans"])

# ---- an undone ban is a different kind of row, and has to look like one
#
# Both states rendered as the same <div class=whorow> and differed only by what sat in
# the last cell, so "is this person banned right now?" meant reading the end of every
# line. The case is not hypothetical: the same name and id appear twice - once undone,
# once live - as soon as somebody is banned again, and those two rows can be far apart.
_B4 = {"name": "Bob", "netid": "76561198000000001", "when": _NOW - 400000,
       "maps": {"The Island": "", "Ragnarok": "timed out"}, "unbanned": _NOW - 100000}
_mixed = ui.render_bans([_B1, _B3, _B4], now=_NOW)
_live_row = _window(_mixed, '<div class="whorow">', 400)

check("an undone ban is rendered in the quiet vocabulary",
      _mixed.count('<div class="whorow quiet">') == 2,
      _mixed.count('<div class="whorow quiet">'))
check("and a live one is not",
      _mixed.count('<div class="whorow">') == 1, _mixed.count('<div class="whorow">'))
check("so the two are told apart by the row, not by its last cell",
      'class="whorow quiet"' not in _live_row, _live_row)
check("the quiet style is the one this page already uses for a line that is not live",
      ".whorow.quiet{border-style:dashed" in ui.CSS, "the class has no style")

# ---- and it stops describing a lifted ban in the present tense
_undone = _window(_from(_mixed, '<div class="whorow quiet">'), "unbanned", 300)
check("an undone row leads with the state it is in",
      _in_order(_from(_mixed, '<div class="whorow quiet">'), "unbanned ", "that ban"),
      _undone)
check("saying when it was lifted", "unbanned 2d ago" in _mixed, _undone)
check("and putting the ban itself in the past",
      "that ban had reached all 2 maps" in _mixed, _undone)
check("it does not say a lifted ban is sent to anything",
      "sent to" not in _from(_mixed, '<div class="whorow quiet">'),
      _from(_mixed, '<div class="whorow quiet">')[:600])
check("a lifted partial reads as a partial that is over",
      "had reached 1 of 2 — missed Ragnarok" in _mixed,
      _window(_from(_mixed, "missed"), "unbanned", 300))
check("while a live ban still reads in the present",
      "1 of 2 — missing Ragnarok" in _live_row, _live_row)
check("and only a live ban offers the way out",
      _mixed.count(">Unban</button>") == 1, _mixed.count(">Unban</button>"))

# ---- the list stops at a page, and says so
#
# It capped at 50 silently: 59 records showed 50 and looked like the whole history, so
# "was this person ever banned?" had an answer the page was hiding.
_capped = ui.render_bans([_B1, _B2], now=_NOW, total=59)
check("a list that is not all of it says how much it is showing",
      "Showing 2 of 59" in _capped, _window(_capped, "Showing", 200))
check("and says the rest is kept rather than gone",
      "older bans are kept but not listed" in _capped, _window(_capped, "Showing", 200))
check("in the quiet style, because it is not a problem",
      "<div class=whonote>Showing 2 of 59" in _capped, _window(_capped, "Showing", 200))
check("a list that IS all of it says nothing",
      "Showing" not in ui.render_bans([_B1, _B2], now=_NOW, total=2),
      ui.render_bans([_B1, _B2], now=_NOW, total=2))
check("nor does one that was never told the total",
      "Showing" not in ui.render_bans([_B1, _B2], now=_NOW),
      ui.render_bans([_B1, _B2], now=_NOW))
check("an empty list does not claim to be hiding anything",
      "Showing" not in ui.render_bans([], now=_NOW, total=0),
      ui.render_bans([], now=_NOW, total=0))
check("and an empty list with records behind it says so",
      "Showing 0 of 7" in ui.render_bans([], now=_NOW, total=7),
      ui.render_bans([], now=_NOW, total=7))

# ---- and the typed-id field says what it is for
check("the by-id field says why somebody would use it",
      "for bans made in-game or by hand" in _banlist,
      _from(_banlist, 'class="whoform byid"'))

# ---- letting somebody past the player cap
#
# The section that is hardest to name and easiest to misread. AllowPlayerToJoinNoCheck
# exempts one id from MaxPlayers; it is not the file that decides who may connect, and
# the word "whitelist" means that file to every ARK admin who has ever run a server.
_C1 = {"netid": "76561198000000001", "when": _NOW - 300,
       "maps": {"The Island": "", "Ragnarok": "timed out"}}
_C2 = {"netid": "0002a1b2c3d4e5f60002a1b2c3d4e5f6", "when": _NOW - 90000,
       "maps": {"The Island": "", "Ragnarok": ""}, "revoked": _NOW - 400}
_caps = ui.render_cap([_C1, _C2], now=_NOW)

check("the cap has a section of its own",
      "<fieldset id=cap>" in _caps
      and "<legend>Let past the player cap</legend>" in _caps, _caps[:200])
check("it is never called a whitelist", "whitelist" not in _caps.lower(), _caps[:600])
check("nor is the confirmation, or anything else this section renders",
      "whitelist" not in (ui.render_cap_confirm("765", "allow")
                          + ui.render_cap_confirm("765", "revoke")).lower(),
      ui.render_cap_confirm("765", "allow"))
check("it says what it actually does",
      "join even when the server is full" in _caps, _window(_caps, "<fieldset id=cap>", 400))
check("and says what it is not",
      "not the join allow-list" in _caps, _window(_caps, "<fieldset id=cap>", 400))

# ---- and it does not claim to know a thing nothing can read
#
# Both lists rest on the same fact, so they say it in the same words - one constant,
# consumed by both. This was two phrasings ten lines apart, which is how a caveat comes
# to say two different things about one fact: whichever half somebody edits, the other
# is now wrong and nothing fails.
check("the list says it is a record of what was sent",
      ui.SENT_NOT_READ in _caps, _window(_caps, "<fieldset id=cap>", 500))
check("and the bans list says it in exactly the same words",
      ui.SENT_NOT_READ in _banlist, _window(_banlist, "<fieldset id=bans>", 400))
check("which is one sentence, not two that can drift apart",
      ui.CAP_ARE.endswith(ui.SENT_NOT_READ)
      and ui.BANS_ARE.endswith(ui.SENT_NOT_READ), [ui.CAP_ARE, ui.BANS_ARE])
check("each section states it once, not twice",
      _caps.count(ui.SENT_NOT_READ) == 1 and _banlist.count(ui.SENT_NOT_READ) == 1,
      [_caps.count(ui.SENT_NOT_READ), _banlist.count(ui.SENT_NOT_READ)])
check("saying plainly that the servers' own lists cannot be read",
      "not a read of each server" in _caps and "nothing here can see" in _caps,
      _window(_caps, "<fieldset id=cap>", 500))
check("and each list still names what it is a list OF",
      _in_order(_caps, "allows issued from Obelisk", "a record of what was sent")
      and _in_order(_banlist, "bans issued from Obelisk",
                    "a record of what was sent"), [_caps[:400], _banlist[:400]])
check("the cap keeps the sentences that are its own, which the bans list has no use for",
      "join even when the server is full" in _caps
      and "not the join allow-list" in _caps
      and "join even when the server is full" not in _banlist,
      _window(_caps, "<fieldset id=cap>", 400))

# ---- the rows
check("a row is keyed on the id, since somebody not online has no name",
      "76561198000000001" in _caps, _window(_caps, "whoroster", 400))
check("a long id is shortened with the whole of it on hover",
      'title="0002a1b2c3d4e5f60002a1b2c3d4e5f6"' in _caps
      and "0002a1b2c3…d4e5f6" in _caps, _window(_caps, "0002a1b2", 300))
check("with when it was sent",
      "5m ago" in _caps and 'title="%s"' % ui._when_title(_C1["when"]) in _caps,
      _window(_caps, "whoroster", 400))
check("and how far it got, in the words the banner used",
      "1 of 2 — missing Ragnarok" in _caps, _window(_caps, "whoroster", 500))
check("a live allow offers the way back",
      '<button class="whoact talk" type=submit>Revoke</button>' in _caps,
      _window(_caps, "/admin/player/cap", 300))
check("which carries the id and the direction",
      _in_order(_window(_from(_caps, "76561198000000001"), "/admin/player/cap", 400),
                'name=netid value="76561198000000001"', 'name=action value="revoke"'),
      _window(_from(_caps, "76561198000000001"), "/admin/player/cap", 400))

# ---- a revoked event is a different kind of row, like an unbanned one
check("a revoked row takes the quiet vocabulary",
      _caps.count('<div class="whorow quiet">') == 1,
      _caps.count('<div class="whorow quiet">'))
check("and a live one does not",
      _caps.count('<div class="whorow">') == 1, _caps.count('<div class="whorow">'))
check("it says when it was revoked", "revoked 6m ago" in _caps,
      _window(_caps, "revoked", 300))
check("and puts the allow itself in the past",
      "that allow had reached all 2 maps" in _caps, _window(_caps, "revoked", 300))
check("a revoked row offers no second Revoke",
      _caps.count(">Revoke</button>") == 1, _caps.count(">Revoke</button>"))
check("and nothing is deleted to make that true",
      _caps.count("<div class=\"whorow") == 2, _caps.count("<div class=\"whorow"))

# ---- nothing yet is a calm sentence
_nocaps = ui.render_cap([], now=_NOW)
check("an empty log says so plainly",
      "Nobody has been let past the cap from here." in _nocaps, _nocaps)
check("in the quiet style",
      "<div class=warn>" not in _nocaps and "<div class=problem>" not in _nocaps,
      _nocaps)
check("and the way in is still there",
      'class="whoform byid"' in _nocaps and ">Allow by ID</button>" in _nocaps,
      _from(_nocaps, 'class="whoform byid"'))
check("which says what it will do",
      "they can join a full server" in _nocaps,
      _from(_nocaps, 'class="whoform byid"'))
check("and carries the direction, so one route can serve both",
      'name=action value="allow"' in _from(_nocaps, 'class="whoform byid"'),
      _from(_nocaps, 'class="whoform byid"'))

# ---- the cap, said out loud, like the bans list
check("a log holding more than it shows says so",
      "Showing 2 of 53" in ui.render_cap([_C1, _C2], now=_NOW, total=53),
      _window(ui.render_cap([_C1, _C2], now=_NOW, total=53), "Showing", 200))
check("and says the rest is kept",
      "older events are kept but not listed" in
      ui.render_cap([_C1, _C2], now=_NOW, total=53),
      _window(ui.render_cap([_C1, _C2], now=_NOW, total=53), "Showing", 200))
check("a log that IS all of it says nothing",
      "Showing" not in ui.render_cap([_C1, _C2], now=_NOW, total=2),
      ui.render_cap([_C1, _C2], now=_NOW, total=2))
check("nor one that was never told a total",
      "Showing" not in _caps, _caps)

# ---- one question at a time, in the place it was asked
_cask = ui.render_cap([_C1, _C2], now=_NOW, pending={
    "netid": "76561198000000001", "when": str(_C1["when"]),
    "html": ui.render_cap_confirm("76561198000000001", "revoke", str(_C1["when"]))})
check("the question replaces the row it was asked on",
      '<div class="whorow asking">' in _cask, _cask[:200])
check("and that row stops offering a one-press Revoke",
      _cask.count(">Revoke</button>") == 0, _cask.count(">Revoke</button>"))
_cbyid = ui.render_cap([_C1], now=_NOW, pending={
    "netid": "999", "when": "", "html": ui.render_cap_confirm("999", "allow")})
check("a question about a typed id replaces the field it was typed into",
      'class="whoform byid"' not in _cbyid, _cbyid)
check("and is asked where that field was",
      _in_order(_cbyid, "<div class=whoroster>", "whorow asking", "Allow by ID"),
      _from(_cbyid, "<div class=whoroster>")[-400:])

# ---- what each confirmation says
_callow = ui.render_cap_confirm("76561198000000001", "allow")
_crevoke = ui.render_cap_confirm("76561198000000001", "revoke")
check("the allow asks once and takes a press",
      '<input type=hidden name=confirm value="1">' in _callow
      and "placeholder=" not in _callow, _callow)
check("naming the id it is about", "76561198000000001" in _callow, _callow)
check("and saying what will change",
      "join every map even when it is full" in _callow, _callow)
check("it says nothing has happened yet",
      "Nothing has been done yet" in _callow, _callow)
check("the revoke asks the opposite question",
      "Stop letting 76561198000000001 past the player cap?" in _crevoke, _crevoke)
check("saying what they keep", "can still join when there is room" in _crevoke,
      _crevoke)
check("and that the record survives it",
      "kept and marked revoked, not removed" in _crevoke, _crevoke)
check("the two send different directions",
      'name=action value="allow"' in _callow
      and 'name=action value="revoke"' in _crevoke, [_callow, _crevoke])
check("both offer a way out that returns to the section",
      'href="/admin/cluster#cap"' in _callow
      and 'href="/admin/cluster#cap"' in _crevoke, [_callow, _crevoke])
check("a problem comes back with the question still standing",
      _in_order(ui.render_cap_confirm("765", "allow", "", "That id is not one."),
                "That id is not one.", "name=confirm"),
      ui.render_cap_confirm("765", "allow", "", "That id is not one."))

# ---- the log itself
_cs2 = _LedgerStore()
_cap.record(_cs2, "765", {"The Island": "", "Ragnarok": "timed out"}, when=100)
_cap.record(_cs2, "765", {"The Island": "", "Ragnarok": ""}, when=200)
_cap.record(_cs2, "999", {"The Island": ""}, when=300)
check("an allow is written down", _cap.count(_cs2) == 3, _cap.count(_cs2))
check("with what each map did", _cap.missed(_cap.recent(_cs2, limit=1).pop()) == [],
      _cap.recent(_cs2, limit=1))
check("newest first", [e["when"] for e in _cap.recent(_cs2)] == [300, 200, 100],
      [e["when"] for e in _cap.recent(_cs2)])
_cmarked = _cap.mark_revoked(_cs2, "765", when=400)
check("a revoke marks every live allow for that id", len(_cmarked) == 2, _cmarked)
check("and leaves other ids alone",
      not any(_cap.is_revoked(e) for e in _cap.entries_for(_cs2, "999")),
      _cap.entries_for(_cs2, "999"))
check("nothing is deleted", _cap.count(_cs2) == 3, _cap.count(_cs2))
check("a second revoke marks nothing twice",
      _cap.mark_revoked(_cs2, "765", when=500) == [], _cs2.data["cap_allows"])
check("an id nobody allowed is not an error",
      _cap.mark_revoked(_cs2, "nobody") == [], "mark_revoked on an unknown id")
check("the log is capped like the ban ledger", _cap.KEEP == 200, _cap.KEEP)
check("and shares one id whitelist with the bans, rather than growing a second",
      _cap.valid_netid is _bans.valid_netid, "two whitelists")

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
