"""
Two names, two rule sets.

The session name is what players search for in the in-game browser; get it wrong and the
server runs perfectly and is simply never listed, which is the hardest kind of broken to
diagnose. The container name is what Docker accepts. They are sanitised separately
because a good display name is often an illegal container name.

Fixture values are synthetic.
"""

import sys

from . import naming

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % detail))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- session names
check("the documented limit is 63", naming.SESSION_MAX == 63)

check("an ordinary name is fine", naming.session_problems("MYCLUSTER 01 | The Island") == [])
check("punctuation people actually use is allowed",
      naming.session_problems("Nate's Island (PvE) - 10x!") == [],
      naming.session_problems("Nate's Island (PvE) - 10x!"))

p = naming.session_problems("#Hidden Cluster")
check("a leading # is refused", p and "hides the server" in p[0], p)
check("and the reason is about the browser, not syntax",
      "never appears in the in-game list" in p[0], p)

p = naming.session_problems("Caf\u00e9 Cluster")
check("non-ASCII is refused", p and "hide your server" in p[0], p)
check("and it names the offending character", "'\u00e9'" in p[0], p)

p = naming.session_problems("Bad\x07Name")
check("control characters are refused", p and "hide your server" in p[0], p)

p = naming.session_problems("X" * 64)
check("64 characters is too long", p and "63" in p[0], p)
check("63 is not", naming.session_problems("X" * 63) == [])
check("an empty name is refused", naming.session_problems("  ") != [])

# ---- sanitising keeps what it can
check("sanitise strips what hides a server",
      naming.sanitize_session("#Caf\u00e9  Cluster\x07") == "Cluster" or
      "Caf" in naming.sanitize_session("#Caf\u00e9  Cluster\x07"),
      naming.sanitize_session("#Caf\u00e9  Cluster\x07"))
check("sanitise collapses whitespace",
      naming.sanitize_session("A    B") == "A B")
check("sanitise obeys the limit", len(naming.sanitize_session("X" * 200)) == 63)
check("a sanitised name has no problems left",
      naming.session_problems(naming.sanitize_session("#Caf\u00e9 Cluster \x07")) == [])

# ---- assembly trims the least important part first
long_tags = "PvE 10x | NoWipe | All Maps Linked | Come And Play With Us Today"
n = naming.assemble_session("MYCLUSTER", 2, "Scorched Earth", long_tags)
check("the assembled name fits the browser", len(n) <= 63, (n, len(n)))
check("the map survives trimming", "Scorched Earth" in n, n)
check("the prefix survives trimming", "MYCLUSTER" in n, n)
check("the tag line is what gets dropped", "Come And Play" not in n, n)
check("numbering keeps the cluster together", "MYCLUSTER 03" in n, n)

short = naming.assemble_session("MY", 0, "The Island", "PvE")
check("nothing is dropped when it all fits", short == "MY 01 | The Island | PvE", short)

check("a map name alone still works",
      naming.assemble_session("", 0, "The Island", "") == "The Island")
check("even an absurd prefix cannot push out the map",
      "The Island" in naming.assemble_session("X" * 80, 0, "The Island", "Y" * 80))

# ---------------------------------------------------------------- container names
c = naming.container_name("arkcluster", "island")
check("the normal case is unchanged", c == "asa-arkcluster-island", c)
check("it is a legal Docker name", naming.is_valid_container_name(c))

fancy = naming.container_name("My Cluster!", "Nate's Island (PvP!)")
check("a fancy display name still yields a legal Docker name",
      naming.is_valid_container_name(fancy), fancy)
check("and it is recognisable", "island" in fancy and "pvp" in fancy, fancy)
check("no spaces or apostrophes survive",
      " " not in fancy and "'" not in fancy and "!" not in fancy, fancy)

check("unicode folds away rather than breaking Docker",
      naming.is_valid_container_name(naming.container_name("caf\u00e9", "\u00eele")),
      naming.container_name("caf\u00e9", "\u00eele"))
check("a name that would start with punctuation is repaired",
      naming.is_valid_container_name(naming.container_name("---", "---")),
      naming.container_name("---", "---"))

# the two sanitisers are genuinely independent
display = "Nate's Island (PvP!)"
check("the session name keeps what players read",
      naming.sanitize_session(display) == display, naming.sanitize_session(display))
check("while the container name folds it for Docker",
      naming.docker_slug(display) == "nate-s-island-pvp", naming.docker_slug(display))
check("so neither is forced to look like the other",
      naming.sanitize_session(display) != naming.docker_slug(display))

# duplicate instances stay distinct through both
check("two islands get distinct container names",
      naming.container_name("evt", "island") != naming.container_name("evt", "island-2"))

print("\nFAILURES: %s" % fails if fails else "\nall naming tests passed")
sys.exit(1 if fails else 0)
