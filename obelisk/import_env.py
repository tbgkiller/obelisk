"""
One-way migration: an existing hand-maintained .env -> the Obelisk store.

Run once when adopting Obelisk. Anything in the .env that no setting claims is
reported rather than silently dropped, so nothing goes missing quietly.

    python3 -m obelisk.import_env /path/to/.env /path/to/settings.json
"""
import re, sys

from .schema import SETTINGS, BY_KEY
from .settings import Store, Invalid, MAPS

ENV_TO_KEY = {s["target"].split(":", 1)[1]: s["key"]
              for s in SETTINGS if s["target"].startswith("env:")}

# Settings Obelisk now owns itself, but which used to live in .env. Without this the
# importer would report them as unclaimed and quietly lose them on migration.
ENV_TO_KEY.update({s["import_from"]: s["key"] for s in SETTINGS if s.get("import_from")})


def parse_env(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def import_env(env_text, store):
    raw = parse_env(env_text)
    applied, rejected, unclaimed = {}, {}, []
    per_map = {}

    for k, v in raw.items():
        if k in ENV_TO_KEY:
            applied[ENV_TO_KEY[k]] = v
            continue
        # per-map overrides look like MEM_LIMIT_ASTRAEOS
        hit = False
        for env_name, key in ENV_TO_KEY.items():
            if BY_KEY[key].get("per_map") and k.startswith(env_name + "_"):
                m = k[len(env_name) + 1:].lower()
                if m in MAPS:
                    per_map.setdefault(m, {})[key] = v
                    hit = True
                    break
        if not hit:
            unclaimed.append(k)

    for k, v in list(applied.items()):
        try:
            store.patch({k: v})
        except Invalid as e:
            rejected[k] = str(e)
            applied.pop(k)
    for m, changes in per_map.items():
        for k, v in changes.items():
            try:
                store.patch({k: v}, map_name=m)
            except Invalid as e:
                rejected["%s.%s" % (m, k)] = str(e)
    return applied, per_map, rejected, unclaimed


if __name__ == "__main__":
    env_path, store_path = sys.argv[1], sys.argv[2]
    st = Store(store_path).load()
    a, pm, rej, un = import_env(open(env_path, encoding="utf-8").read(), st)
    st.save()
    print("imported %d cluster settings" % len(a))
    for m, c in sorted(pm.items()):
        print("  per-map %s: %s" % (m, ", ".join(sorted(c))))
    if rej:
        print("\nREJECTED (left at their defaults - fix and set these by hand):")
        for k, why in sorted(rej.items()):
            print("  %-28s %s" % (k, why))
    if un:
        print("\nNOT CLAIMED BY ANY SETTING (carried nowhere - check these matter):")
        for k in sorted(un):
            print("  %s" % k)
