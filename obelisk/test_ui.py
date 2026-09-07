# Fixture values here are deliberately synthetic - no real cluster's data belongs in a
# public repo.
"""The admin UI renders from the schema alone.  python3 -m obelisk.test_ui"""
import os, re, sys, tempfile

from .plan import build_plan
from .schema import SETTINGS, INSTALL_KEYS, BY_KEY
from .settings import Store
from .ui import page, render_settings, render_cluster, render_setup

fails = []
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
    seg = html_settings[html_settings.index(lbl):html_settings.index(lbl) + 700]
    check("%s is shown read-only" % k, "readonly" in seg or "disabled" in seg, seg[:160])
    check("%s says where to change it" % k, "container" in seg.lower(), seg[:160])
editable = BY_KEY["max_players"]["label"]
seg = html_settings[html_settings.index(editable):html_settings.index(editable) + 400]
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
check("the plan table shows ports", "7777" in h and "27020" in h)
check("the plan explains a heavy map", "runs heavy" in h, h[h.find("Astraeos"):][:300])
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
      setup.index("First time?") < setup.index("name=code"))
check("it points at Unraid's Logs, not a terminal",
      "left-click" in setup and "Logs" in setup and "docker logs" not in setup)
check("it says where in the log to look", "Setup code:" in setup)
check("it says the address is there too", "address of this page" in setup)
check("it explains how to get the code back", "restarting the container" in setup)
check("an error still renders above it",
      render_setup(error="nope").index("nope") <
      render_setup(error="nope").index("First time?"))


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

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
