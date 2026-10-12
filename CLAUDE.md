# Obelisk: read this first

This file is for anyone changing Obelisk: AI coding sessions and human contributors
alike (CONTRIBUTING.md points here). The rules below are standing rules, not suggestions.
Each one exists because breaking it cost the live cluster something.

## Why these rules are strict

`main` deploys. Every push to `main` builds and publishes the image that a live ten-map
ARK cluster updates itself from. There is no staging environment for Obelisk itself:
merging is shipping. So `main` is protected. Every change goes through a pull request,
and the CI `test` job must be green before it can merge.

## Engineering rules

### 1. The static check must pass

`ruff check .` runs in CI before the test suite and fails the build on its own. The rules
live in `ruff.toml`:

- undefined names (F821) and the other F82 errors
- names redefined before use (F811)
- locals assigned and never used (F841)
- syntax errors and statement or comparison errors (E9, F63, F7)

Do not weaken `ruff.toml`, add `# noqa` to silence an F82 error, or mark the step
`continue-on-error`. CI has a self-test that plants an undefined name and fails if the
check does not catch it (`obelisk/test_static.py` and the "Static check self-test" step).

*Why:* on 11 October, `async with lock:` referred to a name that had been removed a month
earlier. It sat in a path no test ran. On the live cluster it failed every three minutes
for a day. F821 flags that line.

### 2. Every setting that switches on a background loop has a test that runs the loop with it on

If a setting enables or changes what a background loop does, a test must drive the real
loop for at least one full pass with that setting on, and with it off where off matters.
That applies to "apply when empty", the update window, the staging modes, the crash watch,
automatic restore, and anything added later. Hand the loop a fake `sleep`, clock and
cluster; never stub out the loop body itself. `obelisk/test_loopguard.py` and
`obelisk/test_empty_watch.py` show the pattern.

*Why:* the 11 October bug only ran with "Apply waiting changes when nobody is on" switched
on, and no test had ever run the loop that way.

### 3. Background workers report repeated failures, with backoff. They never retry silently.

Every long-lived loop started by `main()` uses `obelisk/loopguard.py`. It calls
`guard.tick()` at the top of each pass and `await guard.failed(e)` in its catch-all. The
same exception three passes running then becomes:

- one `loop.failing` error event (Activity feed and Discord)
- an exponential backoff
- a red banner on the Cluster page
- `loop.recovered` when the loop works again

A test fails CI if `main()` starts a loop without a guard. A loop with its own
restart-and-report logic (the relay supervisor, the Discord fallback) must still raise an
event and back off. Catching everything so one bad pass cannot kill the web UI is right.
Catching everything and only logging it is not.

*Why:* the 11 October loop's catch-all logged one line per failure and went round again.
A broken loop looked exactly like a quiet one.

### 4. Announce an action only after it has actually started

"Applying now", "restarting", "priming" and the like are said once the thing is really
underway. That means after the lock is held, the final checks (player count, readiness,
ownership) have passed, and the call that does the work has been made. Never say it before
a check that can still call the action off.

Every refusal or failure is announced too, with its reason. A button that does nothing
must say why, on the page where it was pressed and in the Activity feed.

*Why:* on 11 October the feed said "being applied now" more than 200 times while nothing
was applied. On 4 October an Apply refused over an unreadable player count went back to
"1 change pending" without a word.

## Working on Obelisk

- **Tests:** each `obelisk/test_*.py` is a standalone script. Run it with
  `python -m obelisk.test_<name>`; CI runs them all. Dependencies:
  `pip install aiohttp pyyaml cryptography "ruff==0.17.0"`.
- **On Windows:** `test_firstrun`, `test_settings`, `test_savepoints` and
  `test_stopguard` fail. They need POSIX file modes, `os.fork` and symlinks. CI on Linux
  is the authority for those four.
- **No fake game servers in tests.** Hand the code under test its Docker, RCON, clock and
  filesystem, the way the existing suites do.
- **Never merge with CI red or pending.** Never act on the live cluster from a
  development session (apply, restart, stop) unless the owner asks for exactly that.
