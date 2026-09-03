#!/bin/bash
# User Scripts job "ark-runner" - schedule: Custom  * * * * *
exec python3 "${OBELISK_HOST_DIR:-/mnt/user/appdata/ark/host}/ark-runner.py"
