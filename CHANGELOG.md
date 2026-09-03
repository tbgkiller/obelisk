# Changelog

## Unreleased — initial public release

Obelisk is published from a single initial commit. It grew out of running a real ten-map
ASA cluster, and the private history of that cluster is not part of this repo. The
reasoning worth keeping from that development is recorded below, because the decisions
explain why the code looks the way it does.

### The settings engine

Every setting is declared once with a label, help text, type, range and target. The admin
UI, the API, validation and `docs/SETTINGS.md` all derive from that one list, so adding a
setting never means touching UI code, and a hand edit gets exactly the same checks as an
API call.

Rules the cluster learned the hard way are encoded as validation rather than left as lore:

- A server name starting with `#` runs fine and never appears in the server browser.
- Non-ASCII names do the same thing.
- Stacking mods must load first, or a later mod wins a conflicting remap.
- MOTD only works through the environment, because the server image regenerates that INI
  section on every start — setting it in the shared INI does nothing.

Obelisk refuses these with a reason instead of letting you discover them in production.

The importer was checked against a real hand-written `.env`: all 28 keys regenerate
identically, so adopting Obelisk does not silently change a running cluster.

### The cluster generator

A cluster is "pick your maps", not ten hand-maintained compose service blocks. Ports are
assigned from a base, the first map is the update master the others wait on, and per-map
overrides still work.

The generator was diffed against the hand-written compose file it replaced, service by
service and key by key. That comparison caught three environment keys the first version
dropped — `INSTANCE_NAME`, `UPDATE_COORDINATION_ROLE` and `UPDATE_COORDINATION_PRIORITY` —
two of which are how the cluster avoids ten containers downloading the same 30 GB at once.
Without that check the generated stack would have looked correct and behaved badly.

### The host runner

The container deliberately has no docker socket and no access to `/boot`. Anything needing
either goes through a host-side runner with an explicit action whitelist, so the blast
radius of the web UI stays small.

`verify_mods` compares mods across three layers — the settings, the running server and what
is actually on disk — and names the layer that disagrees: an unapplied change, a mod that
never finished downloading, leftovers from a removed mod, or a stacking mod demoted out of
first place. Mods are the part of an ARK cluster most likely to be quietly wrong; the
server downloads them itself and logs nothing useful about it. Establishing that seven mods
were present and loaded once took an hour inside a container. It is now one action.

### The repo is the product

Everything specific to one deployment lives outside the tracked tree. Schema defaults are
neutral — vanilla rates, no wipe schedule, `/mnt/user/appdata/ark`, `UTC` — and a test
fails if a personal default creeps back in. That guard is structural rather than a word
list, so it catches new personal data instead of only the examples someone thought to add.

Operator-specific configuration is supplied through the settings store or imported from an
existing `.env` with `python -m obelisk.import_env`. That importer is the supported way to
bring an existing cluster in.

### Known gaps

- The admin UI and the packaged image are not finished. Nothing here is ready to point at
  someone else's live cluster.
- `host/prepare-appdata.sh` seeds INI templates from a `config/` directory that this repo
  does not ship. The copy is guarded, so the script runs and simply skips seeding; supply
  your own templates there if you want that step to do something.
