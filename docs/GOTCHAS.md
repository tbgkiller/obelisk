# Things that fail silently

Hard-won, mostly the expensive way. Obelisk turns as many of these as it can into
validation or a check, so they cost someone else minutes instead of an afternoon.

## A mod can install partially and report nothing

A mod's download can fail part way and leave a folder, a subfolder, and almost no
content. Nothing complains. The server lists it on the launch line, the container is
healthy, the mod does nothing in game, and no log anywhere mentions it.

Checking that a mod is "installed" by looking for its folder will tell you everything is
fine. It isn't - **presence is not health**. Measure the install instead: a mod with a
fraction of the files its siblings have is the tell. Super Structures at 3 files and 1 MB
next to a spyglass mod at 8 files and 2 MB is a broken download, not a small mod.

Obelisk's `verify_mods` measures every install and flags outliers. `redownload_mod`
clears one so the server refetches it on the next start - which is the whole fix.

The trap this creates: it looks *exactly* like a mod-priority problem. The mod is
present, it's in the list, and it doesn't work, so you go and rearrange the load order.
Order only decides which of two **loaded** mods wins a conflict. A mod that produces no
engrams at all isn't losing a fight - it never turned up. Check that a mod is really
there before spending a cluster restart on its position.

## Server settings the container regenerates

Several `GameUserSettings.ini` sections are rewritten from environment variables every
time the server starts. Anything you put in the shared INI for those keys is silently
discarded - no error, no warning, the value simply isn't there next boot. The message of
the day is the usual casualty.

Obelisk models which settings live where and generates both, so this can't be got wrong
by hand.

## A leading `#` makes a server invisible

A server whose name starts with `#` runs fine, accepts direct connections, and completes
startup - and never appears in the in-game server list. There is no error. Non-ASCII
characters in the name do the same thing. Obelisk refuses both at save time.

"I can connect directly but it isn't listed" is the signature of this class of bug: a
direct connection proves the server is running, reachable and version-compatible, so
anything affecting only *listing* is a registration problem. Look at the name first.

## Out-of-memory looks like a clean exit

When the host kills a map for memory, it kills the game process - not the container's
supervisor. Docker reports a normal exit and restarts it. `OOMKilled` is false, the exit
code is 0, and nothing says "out of memory". The tells are a climbing restart count and
`dmesg -T | grep "killed process"` on the host.

This is why Obelisk refuses to launch a cluster whose RAM caps exceed the host budget
rather than warning about it. Caps are not reservations, so the total may never be
reached - but when it is, the failure is close to invisible.
