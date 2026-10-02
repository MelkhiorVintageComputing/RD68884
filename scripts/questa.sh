#!/bin/bash
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Compile and elaborate the RTL under Questa -- a sixth front-end, and a strict one.
#
#   scripts/questa.sh <build-dir> <top> <file>...
#
# Only vlog and vopt are used. Neither needs a licence; vsim does, and this machine
# has none, so nothing simulates here. The binaries live in linux_x86_64/, not bin/.
set -euo pipefail

QUESTA_ROOTDIR="${QUESTA_ROOTDIR:-/opt/Altera/questa_fse}"
BIN="$QUESTA_ROOTDIR/linux_x86_64"
if [ ! -x "$BIN/vlog" ]; then
    echo "questa.sh: no vlog at $BIN" >&2
    exit 1
fi

BUILD="$1"; shift
TOP="$1"; shift

WORK="$BUILD/questa_work"
rm -rf "$WORK"
mkdir -p "$BUILD"
"$BIN/vlib" "$WORK"
"$BIN/vlog" -sv -work "$WORK" -quiet "$@"
"$BIN/vopt" -work "$WORK" -quiet "$TOP" -o "${TOP}_opt"
