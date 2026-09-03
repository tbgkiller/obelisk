# Obelisk - architecture

Working design. Named for the thing that links ARK maps to each other.

This repo is the product and contains nothing specific to one person's cluster.
Operators' own settings live in Obelisk's store on their server (and, on a dev
machine, in a gitignored `local/` folder) - never here.

## Shape

```
                 git push  ──►  GitHub Actions  ──►  ghcr.io/<owner>/obelisk
                                                              │  pull
                                                              ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  obelisk                (the only container you install by hand)     │
  │                                                                      │
  │   settings store   ── the single source of truth, in its own appdata │
  │   config API       ── each map fetches its own settings at start     │
  │   admin UI + API   ── :8088  public status  /  token-gated /admin    │
  │   chat relay       ── cross-map + Discord (what crosschat did)       │
  │   scheduler        ── wild wipes, announcements, polling             │
  └──────────────────────────────────────────────────────────────────────┘
            ▲ obelisk-net (shared user-defined bridge, not a shared namespace)
            │
  ┌─────────┴───────────────────────────────────────────────────────────┐
  │ asa_island   asa_center   asa_scorched   ...   (10 thin ARK images) │
  │ each one's entire config:  OBELISK=obelisk:8088   MAP=TheIsland     │
  └─────────────────────────────────────────────────────────────────────┘
```

## Decisions and why

**Shared network, not a shared network namespace.** The VPN-container pattern
(`network_mode: container:vpn`) means restarting the network owner drops the network
for every dependent. Obelisk is the piece that restarts most often, so that would make
every Obelisk update a full-cluster network outage. A user-defined bridge gives the same
DNS-by-name and isolation without coupling restarts.

**Obelisk is the only writer.** Settings live in its store; `.env`, `compose.yaml` and the
shared INIs are *generated* from it. One writer means no drift. The cost: hand-editing
`.env` stops working - the generator will overwrite it.

**Thin ARK image over a fork.** `FROM acekorneya/asa_server:<pinned>` plus an entrypoint
shim that fetches settings from Obelisk, exports them as env, and execs POK's original
entrypoint. We consume POK rather than forking it; the pin means an upstream change
can't surprise ten servers at once.

**Config changes need no Docker access.** Obelisk signals a map to reload, the shim exits
cleanly, `restart: unless-stopped` brings the container back, and it re-fetches config on
the way up. Docker access is only needed to add/remove a map or pull a new ARK image -
which stays on the whitelisted host runner (`unraid/ark-runner.py`), or moves to a docker
socket proxy later if Obelisk should be fully self-contained.

**Generate compose, don't drive the container API.** Slightly less pure, but the stack
stays inspectable and recoverable by hand when Obelisk is broken or stopped. Keep a
last-known-good compose and validate before applying - the first bad generation must not
be able to take ten maps down with no way back.

## Where things live

| What | Channel | Why |
|---|---|---|
| Code, Dockerfiles, compose templates, scripts | git ► Actions ► GHCR | reviewable, versioned, and the update notification |
| Cluster settings (rates, mods, names, ports, wipe times) | Obelisk's own store | one writer, no drift |
| Secrets (Discord token, admin password) | Obelisk's store, never git | `.gitignore` + `.env.example` |
| Settings backup / profile sharing | push/pull to a user-chosen share | portable, and a way back |

## Staging

1. Settings store + generation of `.env` / `compose.yaml` / INIs from it.
2. Thin ARK image with the fetch-on-start shim; move one map onto it before all ten.
3. Admin UI: share path, push/pull, restart, update, schedule editing.
4. Packaging: image, template, docs. Community Applications only if we decide to publish.

The live cluster keeps running throughout. Each stage stands on its own.
