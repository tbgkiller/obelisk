# Migration and rebuild

_Design, not yet built. Comes after the backup/restore drill._

Moving a live cluster is the highest-stakes thing Obelisk will ever do: the worlds are
irreplaceable and people are playing on them. The design follows one invariant, the same
one the rest of the project runs on - **nothing is taken away until its replacement is
proven to work**.

## Pre-staging: never download at cutover

The game files are twelve gigabytes and take tens of minutes. Downloading them during a
migration means the cluster is half-moved for the whole of that, which is the worst place
to be if anything goes wrong.

So pre-staging is a separate, explicit action: **"Prepare server files"**, runnable any
time in advance, as many times as you like. It fetches or updates `ServerFiles` in the
destination Ark folder using the same throwaway-container mechanism as the update master -
a `--rm` container from the server image with `UPDATE_SERVER=TRUE`, mounting only the
files being staged, while nothing is running from them.

By the time a migration starts, the bulk is already on disk and the cutover is a matter
of starting containers.

Pre-staging is also what makes a restore quick: the archive carries saves and definition,
never the install, so a restore onto a pre-staged host is minutes rather than an hour.

## Rolling cutover, one map at a time

For each map, in order:

1. **Launch the new instance** against the destination folder.
2. **Verify it** - healthy, world loaded, RCON answering, save file present and growing.
   The status work already distinguishes "still downloading" from "failing", and this
   step consumes that: a map that never leaves "starting" is a failed cutover, not a
   slow one.
3. **Transfer and confirm** whatever that map needs carried across.
4. **Only then stop the old instance** and reclaim its ports and memory.
5. Repeat.

A verification that does not pass stops the migration where it is, with the old map still
running. There is no step that removes something on the promise that the replacement will
be fine.

## The headroom this requires, stated up front

A rolling cutover means one map exists twice for the duration of its own step: the new one
is up and proven before the old one goes down. That costs **one extra map's worth of RAM
and disk** for the duration - not one extra cluster, one extra *map*.

Obelisk must check and say so before starting, against the existing RAM budget setting:

> "This migration needs room for one more map than you run today: about 20 GB of RAM and
> 12 GB of disk beyond the current cluster. Your host budget is 251 GB with 220 GB
> committed - there is room."

and refuse, with the numbers, when there is not. The check is the same arithmetic the
launch plan already does; the only new part is adding the largest single map to the total
rather than the whole destination cluster.

If the headroom genuinely is not there, the fallback is a map-at-a-time cutover with a
brief gap - old down, new up - which is honest about being a short outage rather than
pretending to be seamless. That must be an explicit choice, never a silent degradation.

## What the operator sees

Per-map progress for the whole run: which map is being cut over, which phase it is in,
which are done, which are still on the old cluster. The same phase display the first-start
work produced, applied per map, so a long step looks like progress rather than a hang.

And a stop button that stops *starting new steps* rather than killing the one in flight -
interrupting a cutover half way is how you end up with neither map running.

## Order of work

1. Pre-stage action, sharing the updater mechanism with the update master.
2. Headroom check wired to the RAM budget, with the message above.
3. Per-map cutover loop with verification gates.
4. Progress display per map, reusing the phase reader.
