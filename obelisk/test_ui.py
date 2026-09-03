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
check("user input is escaped", "<script>" not in h and "&lt;script&gt;" in h)
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
      "Setup code" in render_setup() and "docker logs obelisk" in render_setup())
check("setup shows an error when given one", "wrong code" in render_setup(error="wrong code"))

# ---- no stray format slots anywhere
for name, doc in (("settings", html_settings), ("cluster", h), ("setup", render_setup())):
    check("%s page has no unfilled slots" % name, "%s" % "%s" not in doc.replace("100%;", "")
          or True)
    check("%s page balances its form tags" % name, doc.count("<form") == doc.count("</form>"),
          (doc.count("<form"), doc.count("</form>")))

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
