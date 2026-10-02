#!/bin/bash
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Run a Quartus tool with its environment set up.
#
#   scripts/altera.sh <tool> [args...]
#
# QUARTUS_ROOTDIR is read by Quartus itself to find the device libraries, so it has
# to be exported and not merely used to build PATH.
set -euo pipefail

export QUARTUS_ROOTDIR="${QUARTUS_ROOTDIR:-/opt/Altera/quartus}"
if [ ! -d "$QUARTUS_ROOTDIR/bin" ]; then
    echo "altera.sh: no Quartus at $QUARTUS_ROOTDIR" >&2
    exit 1
fi
export PATH="$QUARTUS_ROOTDIR/bin:$PATH"
exec "$@"
