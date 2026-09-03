#!/bin/bash
# ============================================================
#  Run ONCE on the Unraid host (web terminal or SSH) before the
#  first "compose up". Creates the folder layout, seeds the INI
#  templates into every map instance, and fixes ownership for
#  the container's user (UID/GID 7777).
#
#  Usage:  bash prepare-appdata.sh [/mnt/user/appdata/ark]
# ============================================================
set -euo pipefail

APPDATA="${1:-${OBELISK_APPDATA:-/mnt/user/appdata/ark}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$HERE/../config"
MAPS="island center scorched ragnarok aberration extinction valguero astraeos lostcolony genesis"

echo "==> Creating $APPDATA"
# Pre-create the nested mount points ourselves. If Docker creates them it makes
# them root:root and the container (uid 7777) can't write ShooterGame/ -> the
# install fails with "Failed to create directory .../ShooterGame/Binaries/Win64".
mkdir -p "$APPDATA/ServerFiles/arkserver/ShooterGame/Saved/clusters" \
         "$APPDATA/ServerFiles/arkserver/ShooterGame/Binaries/Win64" \
         "$APPDATA/Cluster"

for m in $MAPS; do
  d="$APPDATA/Instance_$m/Saved/Config/WindowsServer"
  mkdir -p "$d" "$APPDATA/Instance_$m/Saved/clusters" "$APPDATA/Instance_$m/Saved/SavedArks"
  for f in GameUserSettings.ini Game.ini; do
    if [ -f "$CFG/$f" ] && [ ! -f "$d/$f" ]; then
      cp "$CFG/$f" "$d/$f"
      echo "    seeded $f -> Instance_$m"
    fi
  done
done

echo "==> Setting ownership to 7777:7777"
chown -R 7777:7777 "$APPDATA"
chmod -R u+rwX,g+rwX "$APPDATA"

echo "==> vm.max_map_count (ASA/Proton needs >= 262144)"
CUR=$(sysctl -n vm.max_map_count)
if [ "$CUR" -lt 262144 ]; then
  sysctl -w vm.max_map_count=262144
  if ! grep -q 'vm.max_map_count' /boot/config/go 2>/dev/null; then
    echo 'sysctl -w vm.max_map_count=262144   # Ark ASA / Proton' >> /boot/config/go
    echo "    added to /boot/config/go so it survives reboots"
  fi
else
  echo "    already $CUR - nothing to do"
fi

echo
echo "Done. Layout:"
ls -la "$APPDATA"
