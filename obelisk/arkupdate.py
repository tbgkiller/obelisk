"""
What ARK build and which mod versions are installed, and what is newer.

The failure this answers is the one the Obelisk version check already answered for
itself: a confident wrong "up to date". There are two tempting sources here that both
produce exactly that, and both had to be rejected after reading them on a live cluster.

The first is POK's own `pending_manual_update.env`. It is written by a notifier on a
24-hour timer and cleared only on a later tick - the file on the live host says
`INSTALLED_BUILD_ID=25090264` while the appmanifest beside it says 25117056. It is a
record of a past comparison, not a current one.

The second is `library.json`, the CurseForge client's cache, which carries a
`mainFileId` per mod that looks exactly like "the latest version". It is written when
the server *fetches* mods and not again; on the live cluster it has not been touched in
three days. Comparing the installed id against it would compare a number with itself
and report "all mods current" forever.

So: installed comes from the filesystem, which cannot be stale because it *is* the
thing. Available comes from off the machine, and when that fails this says it failed.
The authoritative answer for mods is a staging server boot - see staging.py - because
the ARK server asks CurseForge itself and writes down what it found. The poll here is
the cheap early warning between boots, not the verdict.
"""

import json
import logging
import os
import re
import urllib.request

log = logging.getLogger("obelisk.arkupdate")

APPID = "2430930"
GAME_ID = "83374"                 # CurseForge's game id for ARK: Survival Ascended

# Where the mods land. Inside ServerFiles, which is what lets the staging swap carry
# the build and the mods together as one tree.
MODS_SUBDIR = "ShooterGame/Binaries/Win64/ShooterGame/Mods/" + GAME_ID
USERDATA_SUBDIR = "ShooterGame/Binaries/Win64/ShooterGame/ModsUserData/" + GAME_ID

# Steam's public app info, mirrored over plain HTTP. steamcmd itself is the authority
# and is what actually performs the download, but running it costs a container; this
# costs a request. The staging prime re-asks steamcmd before fetching anything, so a
# wrong answer here delays a notification and can never cause a wrong download.
STEAM_INFO = "https://api.steamcmd.net/v1/info/" + APPID

# Per-project lookup that needs no key. The official API answers 403 without one.
CFWIDGET = "https://api.cfwidget.com/%s"

TIMEOUT = 12

# ---- what the ARK server says about its own mods, at boot
#
# Taken from a real start on the live cluster, not from documentation:
#
#   LogCFCore: No need to update existing mod: TG Stacking Mod 10000-90 (929110)
#   LogCFCore: Mod valid: TG Stacking Mod 10000-90 (929110)
#   UShooterEngine::LoadGameMods with 7 mods
#   UShooterEngine::LoadGameMods Loading Mod ShooterGame/Mods/83374/929110_7738786/... : 929110
#
# The last line is the valuable one: it names the exact file id that got loaded, so a
# staging boot reports the version it proved rather than the version we asked for.
_VALID = re.compile(r"LogCFCore: Mod valid: (.+?) \((\d+)\)")
_LOADED = re.compile(r"LoadGameMods Loading Mod ShooterGame/Mods/%s/(\d+)_(\d+)/" % GAME_ID)
_COUNT = re.compile(r"LoadGameMods with (\d+) mods")
_FETCHED = re.compile(r"LogCFCore: (?:Installed|Updated|Downloaded) mod:? (.+?) \((\d+)\)")

# Only the success shape has been observed on a live host. Rather than guess at the
# wording of failures nobody has seen, the verdict is built from what is *present*:
# every expected mod valid, every expected mod loaded, and the count agreeing. A failure
# worded in a way we have never met still fails, because the mod it broke is missing
# from those sets. The pattern below is a bonus, not the mechanism.
_CF_TROUBLE = re.compile(r"LogCFCore: .*\b(error|failed|failure|cannot|unable)\b", re.I)


def _fetch(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "obelisk"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- the ARK build

def installed_build(serverfiles, read=None):
    """(build id, problem) as recorded by Steam in the install itself.

    The appmanifest is written by steamcmd at the end of a successful install, so it
    describes files that are actually on disk. Nothing else here is trusted for this.
    """
    path = os.path.join(str(serverfiles), "appmanifest_%s.acf" % APPID)
    try:
        text = read(path) if read else open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return None, "could not read %s (%s)" % (os.path.basename(path), e)
    m = re.search(r'"buildid"\s+"(\d+)"', text)
    if not m:
        return None, "no buildid in the appmanifest - the install may be incomplete"
    return m.group(1), ""


def available_build(opener=None):
    """(build id, problem) for the public branch, right now."""
    opener = opener or _fetch
    try:
        raw = opener(STEAM_INFO)
    except Exception as e:                     # noqa: BLE001 - reported, never raised
        return None, "could not reach Steam's app info: %s" % e
    try:
        branch = json.loads(raw)["data"][APPID]["depots"]["branches"]["public"]
        build = str(branch["buildid"])
    except Exception as e:                     # noqa: BLE001
        return None, "Steam's app info did not have a public build id: %s" % e
    if not build.isdigit():
        return None, "Steam returned a build id that is not a number: %r" % build
    return build, ""


# ---------------------------------------------------------------- the mods

def installed_mods(serverfiles, listdir=None, read=None):
    """{project id: {"file_id", "name", "path"}} for what is on disk.

    The directory name is the record: `<projectId>_<fileId>`. Names are a nicety read
    from the CurseForge client's cache - and *only* the names, because that file's
    opinion about what is current is stale by design and must never be consulted.
    """
    root = os.path.join(str(serverfiles), MODS_SUBDIR)
    listdir = listdir or os.listdir
    try:
        entries = sorted(listdir(root))
    except OSError:
        return {}                     # nothing fetched yet is a state, not an error

    out = {}
    for name in entries:
        project, _, file_id = name.partition("_")
        if not project.isdigit() or not file_id.isdigit():
            continue
        out[project] = {"file_id": file_id, "name": "", "path": name}

    for project, display in _cached_names(serverfiles, read).items():
        if project in out:
            out[project]["name"] = display
    return out


def _cached_names(serverfiles, read=None):
    """{project id: display name} from library.json. Names only - see the docstring."""
    path = os.path.join(str(serverfiles), USERDATA_SUBDIR, "library.json")
    try:
        raw = read(path) if read else open(path, encoding="utf-8-sig",
                                           errors="replace").read()
        data = json.loads(raw.lstrip("﻿"))
    except Exception:                          # noqa: BLE001 - names are optional
        return {}
    out = {}
    for entry in data.get("installedMods") or []:
        details = entry.get("details") or {}
        project = str(details.get("iD") or "")
        if project.isdigit() and details.get("name"):
            out[project] = str(details["name"])
    return out


def available_mods(ids, opener=None, source=None):
    """{project id: {"file_id", "name", "url", "problem"}} as CurseForge serves it.

    `source` is how the caller says "ask CurseForge itself" - one authenticated request
    for every mod at once, when the operator has supplied a key. It takes the ids and
    returns the same shape. Without one this falls back to the keyless per-project
    service, which is what a cluster with no key has always used.

    That distinction matters more here than anywhere else on the page: this is the
    answer that decides whether a staging prime starts, and a third-party cache is not
    something to hang that on when the operator has given us a better source.
    """
    ids = _as_ids(ids)
    if source is not None:
        got, problem = source(ids)
        if not problem:
            out = {}
            for project in ids:
                card = got.get(project)
                if card and card.get("file_id"):
                    out[project] = {"file_id": str(card["file_id"]),
                                    "name": card.get("name") or "",
                                    "url": card.get("url") or "",
                                    "categories": card.get("categories") or [],
                                    "problem": ""}
                else:
                    out[project] = {"file_id": None, "name": card.get("name") or ""
                                    if card else "", "url": "", "categories": [],
                                    "problem": ("CurseForge listed no downloadable file"
                                                if card else
                                                "CurseForge does not know this mod")}
            return out
        # A key that cannot answer is not a reason to report every mod unknown when
        # there is a keyless service that can. Say what happened and carry on.
        log.info("the keyed CurseForge lookup failed (%s) - falling back", problem)

    opener = opener or _fetch
    out = {}
    for project in _as_ids(ids):
        entry = {"file_id": None, "name": "", "url": "", "categories": [],
                 "problem": ""}
        try:
            data = json.loads(opener(CFWIDGET % project))
        except Exception as e:                 # noqa: BLE001
            entry["problem"] = "could not ask CurseForge: %s" % e
            out[project] = entry
            continue
        download = data.get("download") or {}
        file_id = download.get("id")
        entry["name"] = str(data.get("title") or "")
        entry["url"] = str((data.get("urls") or {}).get("curseforge") or "")
        entry["categories"] = [str(c.get("name") or "")
                               for c in (data.get("categories") or [])
                               if isinstance(c, dict)]
        if file_id is None:
            entry["problem"] = "CurseForge listed no downloadable file for this mod"
        else:
            entry["file_id"] = str(file_id)
        out[project] = entry
    return out


def _as_ids(value):
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


# ---------------------------------------------------------------- the verdict

def compare(installed, available):
    """Per-mod rows the UI renders, with `newer` only ever True on real evidence.

    Three outcomes, and the third is the point: newer, current, or *unknown*. A mod we
    could not ask about is not current, and the row says so instead of going quiet.
    """
    rows = []
    for project in sorted(set(installed) | set(available), key=int):
        have = installed.get(project) or {}
        want = available.get(project) or {}
        running = have.get("file_id")
        latest = want.get("file_id")
        problem = want.get("problem") or ""
        if not running:
            problem = problem or "listed for the cluster but not on disk yet"
        newer = None
        if running and latest:
            newer = running != latest
        rows.append({
            "id": project,
            "name": want.get("name") or have.get("name") or ("mod %s" % project),
            "running": running,
            "latest": latest,
            "newer": newer,
            "url": want.get("url") or "",
            "categories": want.get("categories") or [],
            "problem": problem,
        })
    return rows


def status(serverfiles, mod_ids, opener=None, listdir=None, read=None,
           source=None):
    """Everything the update panel needs, in one shape.

    `build["newer"]` and each row's `newer` are True, False or None, and None is a real
    answer meaning "we could not find out" - never rendered as up to date.
    """
    have_build, have_problem = installed_build(serverfiles, read=read)
    want_build, want_problem = available_build(opener=opener)
    ids = _as_ids(mod_ids)

    installed = installed_mods(serverfiles, listdir=listdir, read=read)
    available = available_mods(ids, opener=opener, source=source) if ids else {}

    # Mods on disk nobody asked for are left out of the comparison: they are leftovers
    # of a removed mod, and mods.health() is the page that talks about those.
    if ids:
        wanted = set(ids)
        installed = {k: v for k, v in installed.items() if k in wanted}
        for missing in wanted - set(installed):
            installed.setdefault(missing, {"file_id": None, "name": "", "path": ""})

    build_newer = None
    if have_build and want_build:
        build_newer = have_build != want_build

    rows = compare(installed, available)
    return {
        "build": {"running": have_build, "latest": want_build, "newer": build_newer,
                  "problem": have_problem or want_problem},
        "mods": rows,
        "mods_newer": [r for r in rows if r["newer"]],
        "any_newer": bool(build_newer) or any(r["newer"] for r in rows),
        "unknown": (build_newer is None) or any(r["newer"] is None for r in rows),
    }


# ---------------------------------------------------------------- the boot log

def read_boot_log(text, expected_ids):
    """What a server's own log proves about its mods. (ok, problems, detail).

    This is the evidence the staging server exists to produce. The server queries
    CurseForge at startup, states each mod valid or not, and prints the exact
    `<projectId>_<fileId>` it loaded - so the answer is read off what happened rather
    than inferred from what we asked for.
    """
    expected = _as_ids(expected_ids)
    lines = str(text or "").splitlines()

    valid, loaded, fetched = set(), {}, {}
    count = None
    for line in lines:
        m = _VALID.search(line)
        if m:
            valid.add(m.group(2))
        m = _LOADED.search(line)
        if m:
            loaded[m.group(1)] = m.group(2)
        m = _COUNT.search(line)
        if m:
            count = int(m.group(1))
        m = _FETCHED.search(line)
        if m:
            fetched[m.group(2)] = m.group(1)

    problems = []
    for project in expected:
        if project not in valid:
            problems.append("mod %s was never reported valid by the server" % project)
        elif project not in loaded:
            problems.append("mod %s was valid but never loaded" % project)
    if count is not None and count != len(expected):
        problems.append("the server loaded %d mods but %d are configured"
                        % (count, len(expected)))
    if count is None and expected:
        problems.append("the server never reported loading any mods")

    # Counted only when it lands on a mod we care about - CFCore is chatty, and a
    # complaint about something unrelated must not fail an otherwise clean boot.
    for line in lines:
        if _CF_TROUBLE.search(line) and any(p in line for p in expected):
            problems.append("the mod client reported trouble: %s" % line.strip()[:200])
            break

    return (not problems), problems, {"loaded": loaded, "fetched": fetched}
