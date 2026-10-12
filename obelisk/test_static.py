"""
The static check cannot be quietly switched off.

A guard that can be disabled by deleting a line in a config file is a guard that one day
will be - in a hurry, to get a red build green. So this proves, on every run, that:

  - ruff.toml still selects undefined names (F821) and the other rules CLAUDE.md names;
  - the workflow still runs the check, on every pull request, and lets it fail the job;
  - with the repo's own configuration, ruff really does fail on a planted undefined
    name, an unused local and a redefinition - the 11 October bug among them.

The last part needs ruff installed. In CI it is (the workflow installs it); if this runs
in CI without it, that is a failure, not a skip.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile

fails = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: %s" % (detail,)))
    if not cond:
        fails.append(name)


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# ---- the configuration
try:
    import tomllib
    cfg = tomllib.loads(read("ruff.toml"))
except Exception as e:                                   # noqa: BLE001 - reported
    cfg = {}
    check("ruff.toml exists and parses", False, e)
selected = set((cfg.get("lint") or {}).get("select") or [])
check("ruff.toml selects undefined names (F82 / F821)",
      bool(selected & {"F", "F8", "F82", "F821"}), sorted(selected))
check("and redefinitions (F811)", bool(selected & {"F", "F8", "F81", "F811"}),
      sorted(selected))
check("and unused locals (F841)", bool(selected & {"F", "F8", "F84", "F841"}),
      sorted(selected))
check("and syntax errors (E9)", bool(selected & {"E", "E9"}), sorted(selected))
ignored = set((cfg.get("lint") or {}).get("ignore") or []) | set(
    (cfg.get("lint") or {}).get("extend-ignore") or [])
check("and none of them is ignored again",
      not (ignored & {"F", "F8", "F82", "F821", "F811", "F841", "E9"}), sorted(ignored))
check("no per-file exemption for the undefined-name rule",
      "per-file-ignores" not in read("ruff.toml"), read("ruff.toml"))

# ---- the workflow
wf = read(".github", "workflows", "publish.yml")
test_job = wf.split("\n  test:", 1)[-1].split("\n  build:", 1)[0]
check("the test job runs the static check", "ruff check" in test_job, test_job[:600])
check("and the self-test that plants an undefined name",
      "Static check self-test" in test_job, test_job[:600])
check("and nothing in the test job is allowed to fail quietly",
      "continue-on-error" not in test_job, test_job[:600])
pr = wf.split("pull_request:", 1)[-1].split("workflow_dispatch:", 1)[0]
check("pull requests run it whatever files they touch, so a required check always "
      "reports", "paths:" not in pr, pr)

# ---- and it actually bites
PLANTED = '''import asyncio


async def watch():
    async with lock:                 # the 11 October line: `lock` does not exist
        pass


def unused():
    result = 1 + 1                   # F841
    return None


def twice():
    return 1


def twice():                         # F811
    return 2
'''

ruff = shutil.which("ruff")
if ruff is None:
    exe = os.path.join(os.path.dirname(sys.executable), "ruff")
    ruff = exe if os.path.exists(exe) or os.path.exists(exe + ".exe") else None
if ruff is None:
    if os.environ.get("CI"):
        check("ruff is installed in CI, so the planted-error proof runs", False,
              "ruff not found")
    else:
        print("SKIP the planted-error proof: ruff is not installed here "
              "(pip install \"ruff==0.17.0\"); CI runs it")
else:
    d = tempfile.mkdtemp()
    planted = os.path.join(d, "planted.py")
    io.open(planted, "w", encoding="utf-8").write(PLANTED)
    run = subprocess.run([ruff, "check", "--no-cache", "--output-format", "concise",
                          "--config", os.path.join(ROOT, "ruff.toml"), planted],
                         capture_output=True, text=True)
    out = run.stdout + run.stderr
    check("with the repo's own config, ruff fails on a planted undefined name",
          run.returncode != 0 and "F821" in out and "lock" in out, out)
    check("and on an unused local", "F841" in out, out)
    check("and on a redefinition", "F811" in out, out)

    clean = os.path.join(d, "clean.py")
    io.open(clean, "w", encoding="utf-8").write("def ok():\n    return 1\n")
    run2 = subprocess.run([ruff, "check", "--no-cache", "--config",
                           os.path.join(ROOT, "ruff.toml"), clean],
                          capture_output=True, text=True)
    check("and passes clean code, so a failure means the planted errors",
          run2.returncode == 0, run2.stdout + run2.stderr)

print("\nFAILURES: %s" % fails if fails else "\nall static-check tests passed")
sys.exit(1 if fails else 0)
