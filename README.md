# Obelisk

**One container to run and manage an ARK: Survival Ascended cluster.**

Obelisks are how ARK maps link to each other. This is the same idea for the servers:
install one container, tell it which maps you want, and it builds and runs the cluster —
generating the compose file, assigning ports, keeping one shared settings file across
every map, relaying chat between them and to Discord, and giving you a status page and
admin controls in the browser.

> **Status: in development.** The settings engine, cluster generator and host runner are
> built and tested. The admin UI and the packaged image are not finished yet. Nothing
> here is ready for someone else's live cluster.

## What Obelisk is trusted with

Obelisk creates and manages your map containers, so it needs the host's Docker socket
mounted in. Be clear about what that means: **anything that can create a container with
a bind mount can read and write any file on the host.** Obelisk is trusted
infrastructure on your server, not just another app, and a serious bug in it is a
serious bug on the host.

That is the same deal Portainer, Dockge and Watchtower ask for, and there is no way to
run containers without it. A filtered socket proxy sounds like the safer answer, but it
would still have to permit container creation — which is the powerful part — so it adds
moving parts without meaningfully shrinking the blast radius. Not worth pretending
otherwise.

What that buys in return: no compose file to hand-write, no port arithmetic, and
add-a-map as a checkbox rather than an editing session.

If you want to reduce exposure, the useful move is not a proxy — it is keeping
untrusted input away from the process that holds the socket. Today the only such input
is Discord chat. Splitting that into its own socket-less container is a real
improvement and is on the roadmap; a proxy is not.

## Installing

Two phases, because Docker has to know a few things before Obelisk exists.

**1. Create the container.** Three answers, all of them infrastructure: where the
cluster's data lives, where Obelisk keeps its own store, and which port serves the UI.
Two are bind mounts and one is a published port, so Docker needs them at create time —
there is no app running yet to ask. On Unraid these are the template fields; otherwise
see [`docker/compose.example.yml`](docker/compose.example.yml).

**2. Everything else in the browser.** Start the container and check its log:

```
docker logs obelisk
```

It prints a one-time setup code. Open `http://<host>:8088/setup`, enter it, and set the
rest there — maps, mods, rates, Discord, wipe schedules, passwords. None of that is
needed to boot, so none of it belongs in a template field, and nothing ships with a
blank waiting to be filled in.

Those first three stay read-only in the UI on purpose. Showing an editable box for a
bind mount would let you change a value that isn't real until somebody recreates the
container — so instead the UI shows what Docker actually created it with, and tells you
where to change it.

## Why

Running a multi-map ASA cluster today means hand-maintaining a compose file with a dozen
near-identical service blocks, a `.env`, and a shared INI — and re-learning the same
sharp edges everyone else hits. Obelisk turns that into settings with labels and help
text, and encodes the sharp edges as validation you can't save past.

## What it does

- **Generates the cluster.** Pick your maps; it writes the compose file, assigns game and
  RCON ports in order, wires one shared copy of the server files, and makes the first map
  the update master the others wait on.
- **One settings store.** Everything is declared once with a type, a range and help text.
  The UI renders from it, the API validates against it, the docs generate from it. Edit
  in the browser or edit `settings.json` in a text editor — same validation either way.
- **Knows the traps.** A server name starting with `#` runs fine and never appears in the
  browser. Non-ASCII names do the same. Stacking mods must load first. MOTD only works via
  environment because the server image regenerates that INI section on boot. Obelisk
  refuses these with a reason instead of letting you find out later.
- **Cross-map chat and Discord relay**, scheduled wild-dino wipes with in-game warnings,
  and a cluster-wide player count.
- **Status page** with per-map state, players, memory and restart counts.
- **Settings backup/restore** to any local or network share you point it at.

## How it's put together

| | |
|---|---|
| `obelisk/` | the manager — settings schema and store, generators, chat relay, status page |
| `docker/` | the image |
| `host/` | optional Unraid helpers: the whitelisted command runner and stats feed |
| `docs/` | architecture, generated settings reference, chat relay setup |

The map servers run [Acekorneya's POK image](https://github.com/Acekorneya/Ark-Survival-Ascended-Server),
which Obelisk drives rather than replaces.

Design and reasoning: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Every setting: [docs/SETTINGS.md](docs/SETTINGS.md) (generated from the schema).

## Running the tests

No server, no Docker, no cluster needed:

```bash
python3 -m obelisk.test_settings     # settings, validation, generation
python3 host/test_ark_runner.py      # command whitelist and share handling
```

## Licence

MIT — see [LICENSE](LICENSE).
