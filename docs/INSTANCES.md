# The instance model

_Design, not yet built. The naming half has landed; the rest is specified here._

## The shift

A cluster is **a list of instances**, not a checklist of map types. Today "maps" is a set
of ticked boxes, one per map, which quietly assumes a cluster runs each map at most once.
Clusters do not work that way: an events Island beside the normal Island, a PvP Ragnarok
with different rates, a test map with a mod being trialled before it goes cluster-wide.

An **instance** is one running server. It has:

| Field | Notes |
|---|---|
| map type | which map it runs - `island`, `ragnarok`, … |
| instance id | unique within the cluster; `island`, `island-2`, … |
| display name | what players see in the browser; defaults from the map name |
| ports | game and RCON, auto-assigned, unique across the cluster |
| mods | inherited from the cluster, or overridden for this instance |
| settings | inherited from the cluster, or overridden for this instance |

## Inheritance, because the common case is uniform

Most clusters want every map identical. Divergence is the exception, and an interface
that makes the exception cost nothing usually makes the common case cost something.

So: **"All maps use the same mod list" is a cluster-wide toggle, default ON.** While it
is on, every instance inherits `mod_ids` and the per-instance mod field is hidden - not
merely ignored, hidden, because a visible field that does nothing is how people end up
believing they changed something. Turning it off reveals per-instance mods, pre-filled
with the cluster list so nothing changes until it is edited.

Game settings work the same way: inherit by default, override where asked. Every
overridden value should show what it is overriding, so a cluster does not silently drift
into ten different configurations nobody remembers choosing.

The preset checklist stays as the fast path - ticking maps adds one instance each.
Adding, duplicating and naming instances is the power-user route to the same model.

## Names: two of them, with different rules

Each instance carries a **display name** (what players see) and a **container name**
(what Docker sees). They are sanitised separately, because a good one of the first is
usually an illegal one of the second - `Nate's Island (PvP!)` reads well in the browser
and is not a valid container name.

**Display name → session name.** ARK obtains the unofficial server list over Valve's
Master Server Query Protocol, and the game limits the name field to 63 characters
(ARK Official Community Wiki, *Server Browser*). Exceed it and the name is cut short;
certain characters do something worse and leave the server running, reachable by direct
connect, and absent from the browser entirely. Known offenders: a leading `#`, and
non-ASCII characters. Those two are reported behaviour rather than published rule, so
Obelisk refuses them with a reason rather than claiming a specification.

The limit applies to the **finished** string - prefix, number, map, tag line - which is
why it cannot be enforced field by field. Assembly drops the least important part first:
tag line, then prefix, never the map name, because the map is what a player is scanning
for.

Per instance, the display name defaults to the map's name and inherits the cluster
prefix and tag line. Overriding it is how an events island announces itself as one.

**Container name.** `asa-<cluster>-<instance>`, each half folded to Docker's charset
independently of anything a player sees. A display name never determines a container
name directly; the instance id does.

## What is already done

Identity is per instance, because it had to be for the safety fix:

- container name is `asa-<cluster>-<instance>`, so `asa-evt-island` and
  `asa-evt-island-2` coexist, and neither can collide with another cluster's containers;
- ports are assigned per row in the plan, so instances never share one;
- the save folder is `instances/<instance>/Saved`, per instance;
- `INSTANCE_NAME` is the instance id, which is what the server image keys its own
  per-instance state off.

The first instance of a map keeps the plain key, so a cluster that already exists is
never renamed by this.

## The problem to solve before duplicates can ship

**Two instances of the same map would share one save folder in the shared tree.**

The server image links `SavedArks/<map_id>` out of the shared directory, and `map_id` is
a property of the map, not the instance - both Islands are `TheIsland_WP`. Two Islands
would therefore write the same world. That is data loss, not a cosmetic clash, and it is
the reason duplicate maps are still rejected by validation rather than half-supported.

Options, in the order they should be evaluated:

1. **Give each instance its own SavedArks subtree** keyed by instance id rather than map
   id, and point the instance at it. Cleanest if the image allows the path to be set.
2. **Keep saves entirely inside `instances/<instance>/Saved`** and stop sharing that tree
   at all, accepting that the shared-config trick no longer covers saves.
3. Refuse duplicates of a map whose saves cannot be isolated, and support duplicates only
   where option 1 works.

Whichever is chosen, the check that a backup actually contains one save per instance
needs to follow it - the current check counts saves per map type.

## Above the clusters, not inside one: many clusters, one Obelisk

Obelisk is deliberately not a service in the stack it generates. That started as a bug
fix - a manager that wrote itself into its own compose collided with itself on its own
port - but the shape it forced is the right one, and the owner has confirmed the
direction it implies: **one Obelisk manages several clusters.**

The manager sits above the clusters. It has to be up before any of them, it is not
restarted by recreating one, and it may run and manage more than one at a time. Each
cluster is its own registered Compose Manager stack, with its own project name, data
root, ports, instances and mod list.

That is also what makes a migration expressible: the old cluster and the new one are two
stacks under one manager, both visible, cut over map by map. A manager that could only
ever describe one cluster would have to become two managers to do that, which is exactly
the situation that produces a collision.

What it means for the work already done:

- container names, ports, save folders and stack project names are already namespaced per
  cluster, so two clusters coexisting is a UI problem now rather than a data problem;
- the store currently holds one cluster's settings at its top level, and would grow a
  clusters list with the existing values becoming the first entry;
- the data root is per cluster, so a second cluster means a second Ark folder, and the
  headroom check in the migration design is the same arithmetic;
- the UI assumes one cluster everywhere - it should list clusters and let you pick one,
  with a single-cluster install still landing straight on it rather than paying for a
  chooser it does not need.

Not scoped yet; captured so the pieces keep being built in a shape that allows it.

## Order of work

1. Instance list in the store, replacing the map checklist, with a migration from it.
2. Save isolation - the problem above - before duplicates are allowed through validation.
3. The uniform-mods toggle and per-instance overrides.
4. Add / duplicate / rename in the Cluster page, with presets still adding one per map.
5. Multiple clusters under one manager: a clusters list in the store, a chooser in the UI,
   and each cluster its own stack - the shape migration already assumes.
