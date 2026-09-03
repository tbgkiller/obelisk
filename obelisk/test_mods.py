# Fixture mod ids here are deliberately synthetic except 929110, which the product
# checks by name to enforce stacking-mod load order.
"""Mod list editing and install health.  python3 -m obelisk.test_mods"""
import os, sys, tempfile

from . import mods as m
from .settings import Store, Invalid
from .ui import render_mods

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(detail)) if detail and not cond else ""))
    if not cond: fails.append(name)

def raises(name, fn, *a):
    try:
        fn(*a); check(name, False, "did not raise")
    except (ValueError, Invalid) as e:
        check(name, True, str(e))

# ---- editing
check("add appends, so a new mod can't outrank a working one",
      m.add("929110,222222", "333333") == "929110,222222,333333")
raises("add rejects a non-numeric id", m.add, "929110", "SuperStructures")
raises("add rejects a duplicate", m.add, "929110,222222", "222222")
check("remove takes one out", m.remove("929110,222222,333333", "222222") == "929110,333333")
raises("remove rejects an id that isn't there", m.remove, "929110", "999999")
check("move up swaps with the one above",
      m.move("929110,222222,333333", "333333", -1) == "929110,333333,222222")
check("move down swaps with the one below",
      m.move("929110,222222,333333", "222222", 1) == "929110,333333,222222")
check("moving past the top is a no-op, not an error",
      m.move("929110,222222", "929110", -1) == "929110,222222")
check("moving past the bottom is a no-op",
      m.move("929110,222222", "222222", 1) == "929110,222222")
check("parse tolerates spacing", m.parse(" 929110 , 222222 ") == ["929110", "222222"])

# ---- the store still owns the rules
st = Store(os.path.join(tempfile.mkdtemp(), "s.json"))
st.patch({"admin_password": "pw", "mod_ids": "929110,222222,333333"})
demoted = m.move(st.get("mod_ids"), "929110", 1)
try:
    st.patch({"mod_ids": demoted})
    check("demoting the stacking mod is refused by the store", False)
except Invalid as e:
    check("demoting the stacking mod is refused by the store", "stacking" in str(e), str(e))
check("and the list is unchanged after the refusal", st.get("mod_ids") == "929110,222222,333333")

# ---- health: the thing that actually went wrong
installs = {"929110": {"files": 6, "kb": 3000}, "222222": {"files": 8, "kb": 4000},
            "333333": {"files": 2, "kb": 900},  "444444": {"files": 7, "kb": 2000}}
h = m.health(installs, "929110,222222,333333,444444")
check("a healthy install reads ok", h["929110"]["status"] == "ok", h["929110"])
check("a stub install is caught", h["333333"]["status"] == "stub", h["333333"])
check("the stub note says it fails silently", "say nothing" in h["333333"]["note"], h["333333"]["note"])
check("size is reported in MB", h["222222"]["mb"] == 3.9, h["222222"])

h2 = m.health(installs, "929110,222222,333333,444444,555555")
check("a listed mod with no folder reads missing", h2["555555"]["status"] == "missing")
h3 = m.health(installs, "929110,222222,333333")
check("an install not in the list reads orphan", h3["444444"]["status"] == "orphan", h3.get("444444"))

few = {"929110": {"files": 6, "kb": 3000}, "222222": {"files": 2, "kb": 900}}
h4 = m.health(few, "929110,222222")
check("no verdict with too few mods to compare",
      h4["222222"]["status"] == "ok", h4["222222"])

# ---- the page
st.patch({"mod_ids": "929110,222222,333333,444444"})
page = render_mods(st, h)
check("mods are numbered in load order", "<td class=num>1</td>" in page and "<td class=num>4</td>" in page)
check("the first mod is marked as winning conflicts", ">first<" in page)
check("a broken install is called out", "BROKEN INSTALL" in page)
check("and offers a re-download", "Re-download" in page)
# count the buttons, not the word - the warning banner says "Re-download" too
check("only the broken mod gets a re-download button",
      page.count("name=refetch") == 1, page.count("name=refetch"))
check("the button is on the broken one", 'name=refetch value="333333"' in page)
check("the banner warns against reordering instead", "order only decides" in page)
check("the top mod can't be moved up", 'name=up value="929110" disabled' in page, page[:400])
check("the bottom mod can't be moved down", 'name=down value="444444" disabled' in page)
check("mod ids are escaped", "<script>" not in render_mods(st, {"<script>x": {"status": "ok"}}))
check("an empty list says the cluster is vanilla",
      "runs vanilla" in render_mods(Store(os.path.join(tempfile.mkdtemp(), "e.json")), {}))

print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
