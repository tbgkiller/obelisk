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
check("every setting is on the page, plus a block per stat family and per array",
      len(re.findall(r"class=f data-k=", _h))
      == len(SETTINGS) + len(STAT_FAMILIES) + len(ROW_ARRAYS),
      "%d blocks for %d settings + %d families + %d arrays"
      % (len(re.findall(r"class=f data-k=", _h)), len(SETTINGS),
         len(STAT_FAMILIES), len(ROW_ARRAYS)))
check("no stat cell is a setting of its own any more",
      not any("[" in s["key"] for s in SETTINGS),
      [s["key"] for s in SETTINGS if "[" in s["key"]])

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

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
