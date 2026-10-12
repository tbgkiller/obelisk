# Contributing to Obelisk

Read [CLAUDE.md](CLAUDE.md) first. Despite the name, it holds the project's standing
engineering rules for everyone, human or AI.

In short:

1. **The static check must pass.** CI runs `ruff check .` (rules in `ruff.toml`) before
   the tests. Undefined names and similar errors fail the build.
2. **Every setting that switches on a background loop needs a test** that runs the real
   loop for at least one full pass with that setting on.
3. **Background workers report repeated failures as events, with backoff.** Use
   `obelisk/loopguard.py`. They never retry silently.
4. **Announce an action only after it has actually started.** Announce every refusal
   with its reason.

`main` deploys to a live cluster, so it is protected. Changes go through a pull request,
and the CI `test` job must be green before merging.

Run a suite with `python -m obelisk.test_<name>`.
