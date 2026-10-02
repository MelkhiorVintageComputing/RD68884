#!/bin/bash
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Run a Xilinx tool with its environment set up.
#
#   scripts/xilinx.sh <tool> [args...]
#
# The tools are not on PATH and refuse to run without settings64.sh, which also
# clobbers a good deal of the environment -- so it is sourced here rather than by
# whoever calls this.
set -euo pipefail

SETTINGS="${VIVADO_SETTINGS:-/opt/Xilinx/2025.2/Vivado/settings64.sh}"
if [ ! -f "$SETTINGS" ]; then
    echo "xilinx.sh: no settings file at $SETTINGS" >&2
    echo "           set VIVADO_SETTINGS to the right one" >&2
    exit 1
fi

# xelab compiles the elaborated design to C and links it with the system linker,
# which on some machines cannot find crti.o on its own. The failure is "cannot find
# crti.o" from /usr/bin/ld, at the very end of an otherwise clean elaboration.
for d in /usr/lib/x86_64-linux-gnu /usr/lib64; do
    [ -f "$d/crti.o" ] && export LIBRARY_PATH="${LIBRARY_PATH:+$LIBRARY_PATH:}$d"
done

# shellcheck disable=SC1090
source "$SETTINGS"
exec "$@"
