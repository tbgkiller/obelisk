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
