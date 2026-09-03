# Append these lines to /boot/config/go on Unraid (prepare-appdata.sh does this for you).
# They run at every boot, before Docker starts.
sysctl -w vm.max_map_count=262144   # Ark ASA / Proton
