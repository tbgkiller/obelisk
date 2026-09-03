"""
Presets: a named set of maps, nothing more.

A preset pre-ticks the checklist and then gets out of the way - it carries no cluster
name, no password, no paths, no mod list. Those come from the operator. That keeps a
preset shareable without carrying anyone's setup in it, and means adopting one is a
starting point rather than a commitment.
"""

PRESETS = [
    dict(key="full", name="Full cluster",
         description="Every official map. The whole thing, clustered, ports and RAM "
                     "assigned for you. Expect this to want a lot of memory - the "
                     "review step totals it up before anything starts.",
         maps=["island", "center", "scorched", "aberration", "extinction",
               "astraeos", "ragnarok", "valguero", "lostcolony", "genesis"]),

    dict(key="starter", name="Starter",
         description="Two maps that play well together and won't eat a whole host. "
                     "A good first cluster - you can add maps later without "
                     "disturbing these.",
         maps=["island", "ragnarok"]),

    dict(key="classics", name="The classics",
         description="The original progression: Island, Scorched Earth, Aberration, "
                     "Extinction. Story maps in order, nothing extra.",
         maps=["island", "scorched", "aberration", "extinction"]),

    dict(key="single", name="Single map",
         description="Just The Island. Cheapest way to get something running and see "
                     "how the rest works before committing memory to more.",
         maps=["island"]),
]

BY_KEY = {p["key"]: p for p in PRESETS}
